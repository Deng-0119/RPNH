"""Declared Petri output validation and token deposition."""
from __future__ import annotations

import itertools
import json
import math
import random
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping, Optional, Sequence

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.petri_runtime_interfaces import RegistryTokenAllocator
from cpn.rpnh.marking import (
    ActiveFiringClaim,
    BindingEnumeration,
    BindingQueueOverflow,
    BoundInputToken,
    FiringAllocationEvidence,
    FiringClaimOccurrence,
    MarkingResourceRefError,
    MarkingStateError,
    PetriFiringResourceAccess,
    ResourceDemand,
    SelectedRouteEpochEffectResult,
    TeamNetMarking,
    TimedBinding,
    TimedMarkingCheckpointAuthority,
    TimedMarkingDeltaMaterial,
    TimedTerminalOutput,
    TimedTerminalProposal,
    TimedWaitGuardState,
    Token,
    VERDICT_GUARD_KIND,
    _ActiveClaim,
    _PendingClaim,
    _canonical_marking_material,
    _canonical_timed_claim_rows,
    _nonnegative_integer,
    _verdict_color_matches,
    _version_ref_key,
    continuation_from_tokens,
    count_guard_holds,
    exact_token_identity,
    validate_typed_marking_state,
)

def _validated_output_specs(self, produced: object, *, selected_route_places=frozenset()) -> list[dict]:
    """Validate the complete output batch before any marking mutation."""
    if not isinstance(produced, list):
        raise MarkingStateError('produced token specs must be an array')
    base_expected = {'place', 'resource_ref', 'work_resource_ref', 'kind', 'verdict', 'continuation'}
    binding_expected = {'output_port_id', 'output_place_ref', 'output_binding_ref'}
    expected = base_expected | binding_expected
    expected_with_claims = expected | {'lease_claims'}
    base_with_claims = base_expected | {'lease_claims'}
    registered_exact = isinstance(self._net.registry_net_ref, VersionRef)
    validated: list[dict] = []
    for index, spec in enumerate(produced):
        path = f'produced[{index}]'
        if not isinstance(spec, dict):
            raise MarkingStateError(f'{path} must be an object')
        accepted_shapes = {frozenset(expected), frozenset(expected_with_claims)} if registered_exact else {frozenset(base_expected), frozenset(base_with_claims), frozenset(expected), frozenset(expected_with_claims)}
        if frozenset(spec) not in accepted_shapes:
            raise MarkingStateError(f'{path} fields must be exactly {sorted(expected if registered_exact else base_expected)!r}, got {sorted(spec, key=repr)!r}')
        place = self._optional_string(spec['place'], f'{path}.place')
        if place is None:
            raise MarkingStateError(f'{path}.place must not be null')
        resource_ref = spec['resource_ref']
        work_resource_ref = spec['work_resource_ref']
        if resource_ref is not None and (not isinstance(resource_ref, ResourceVersionRef)):
            raise MarkingResourceRefError(f'{path}.resource_ref must be an exact ResourceVersionRef')
        if work_resource_ref is not None and (not isinstance(work_resource_ref, ResourceVersionRef)):
            raise MarkingResourceRefError(f'{path}.work_resource_ref must be an exact ResourceVersionRef')
        if work_resource_ref is not None and resource_ref is None:
            raise MarkingResourceRefError(f'{path}.work_resource_ref requires a primary resource_ref')
        output_port_id = spec.get('output_port_id')
        output_place_ref = spec.get('output_place_ref')
        output_binding_ref = spec.get('output_binding_ref')
        if resource_ref is None:
            if any((value is not None for value in (output_port_id, output_place_ref, output_binding_ref))):
                raise MarkingResourceRefError(f'{path} content-less token cannot carry output binding authority')
        elif place in selected_route_places:
            if any((value is not None for value in (output_port_id, output_place_ref, output_binding_ref))):
                raise MarkingResourceRefError('selected route cannot fabricate an output binding')
        elif registered_exact:
            output_port_id = self._optional_string(output_port_id, f'{path}.output_port_id')
            if output_port_id is None or not isinstance(output_place_ref, VersionRef) or (not isinstance(output_binding_ref, VersionRef)) or (output_binding_ref.entity_type != 'output_binding/v1'):
                raise MarkingResourceRefError(f'{path} semantic token requires one exact output port/place/binding')
        kind = self._optional_string(spec['kind'], f'{path}.kind')
        if kind is not None and resource_ref is None:
            raise MarkingResourceRefError(f'{path} semantic token kind {kind!r} requires an exact resource_ref')
        verdict = spec['verdict']
        if not (verdict is None or type(verdict) is bool or (isinstance(verdict, str) and verdict and (verdict == verdict.strip()))):
            raise MarkingStateError(f'{path}.verdict must be null, a boolean, or a canonical string')
        self._require_concrete_place_colour(place, verdict, path=path)
        continuation = self._runtime_continuation(spec['continuation'], f'{path}.continuation')
        lease_claims = self._runtime_lease_claims(spec.get('lease_claims', ()), f'{path}.lease_claims')
        validated.append({'place': place, 'resource_ref': resource_ref, 'output_port_id': output_port_id, 'output_place_ref': output_place_ref, 'output_binding_ref': output_binding_ref, 'work_resource_ref': work_resource_ref, 'kind': kind, 'verdict': verdict, 'continuation': continuation, 'lease_claims': lease_claims})
    return validated

def _prevalidate_declared_output_colours(self, t_id: str, proposed: object, *, claim_epoch: int) -> object:
    """Derive coloured ``forward`` / ``control_only`` output occurrences.

        The caller owns an isolated copy and holds ``self._lock``. This narrow
        pass precedes strict output validation because expressions and declared
        inheritance derive the concrete destination colour from the complete
        claimed occurrence multiset. Malformed shapes remain for the strict
        validator; this method never supplies missing ABI fields.
        """
    if not isinstance(proposed, list):
        return proposed
    eligible = [spec for spec in proposed if isinstance(spec, dict) and 'verdict' in spec and isinstance(spec.get('place'), str) and (self._net.output_emit_for(t_id, spec['place']) in {'forward', 'control_only'} or self._net.output_color_expression_for(t_id, spec['place']) is not None) and bool(self._net.place_color_sets.get(spec['place'], ()))]
    if not eligible:
        return proposed
    claimed = self.claimed_tokens(t_id, claim_epoch)
    claimed_colours = tuple(((token.place, token.verdict) for token in claimed))
    input_guards = self._net.verdict_guards_of(t_id)
    for spec in eligible:
        place = spec['place']
        expression = self._net.output_color_expression_for(t_id, place)
        if expression is not None:
            try:
                derived = self._net.project_output_color(t_id, place, claimed_colours)
            except ValueError as exc:
                raise MarkingStateError(f'output colour projection failed closed for {t_id!r}->{place!r}: {exc}') from exc
            if derived is None:
                raise MarkingStateError('declared output colour expression produced no colour')
        else:
            forward_source = self._net.forward_source_place(t_id, place) if self._net.output_emit_for(t_id, place) == 'forward' else None
            if forward_source is not None:
                source_place = forward_source
                guarded = tuple((expected for guarded_place, expected in input_guards if guarded_place == source_place))
                expected_input = guarded[0] if len(guarded) == 1 else None
            elif len(input_guards) == 1:
                source_place, expected_input = input_guards[0]
            else:
                if spec['verdict'] is not None and self._net.output_guard_for(t_id, place) is None:
                    continue
                raise MarkingStateError('declared colour inheritance requires the sole declared verdict-guard source')
            source_occurrences = tuple((token for token in claimed if token.place == source_place))
            if len(source_occurrences) != 1:
                raise MarkingStateError('declared colour inheritance requires exactly one claimed occurrence on its declared colour source')
            derived = source_occurrences[0].verdict
            if derived is None:
                raise MarkingStateError('declared colour inheritance requires one concrete colour')
            source_domain = self._net.place_color_sets.get(source_place, ())
            if not source_domain or not any((_verdict_color_matches(derived, colour) for colour in source_domain)):
                raise MarkingStateError('declared colour inheritance source colour is absent from its source domain')
            if expected_input is not None and (not _verdict_color_matches(derived, expected_input)):
                raise MarkingStateError('declared colour inheritance disagrees with its input guard')
            if any((not _verdict_color_matches(derived, expected_output) for _output_place, expected_output in self._net.output_guards_of(t_id))):
                raise MarkingStateError('declared colour inheritance disagrees with its output guard')
        destination_domain = self._net.place_color_sets[place]
        if not any((_verdict_color_matches(derived, colour) for colour in destination_domain)):
            raise MarkingStateError('derived output colour is absent from its destination domain')
        explicit = spec['verdict']
        if explicit is not None and (not _verdict_color_matches(explicit, derived)):
            raise MarkingStateError('transition body contradicted its declared output colour')
        spec['verdict'] = derived
    return proposed

def deposit_outputs(self, t_id: str, produced: list[dict], *, claim_epoch: int) -> tuple[int, int]:
    """Deposit fresh tokens for ``t``'s produced artifacts.

        Each produced occurrence is deposited once per output-arc weight, independent
        of the number of outgoing consumers.  Every consumer therefore contests the
        same shared occurrence and a concurrently selected firing step must satisfy
        their combined multiset demand.  An explicitly coloured place additionally
        requires one concrete value from its declared domain.

        ``produced`` is a list of closed output proposals. In a Registry-bound net each
        semantic proposal carries ``place``, the exact output resource, and the exact
        ``output_port_id`` / ``output_place_ref`` / ``output_binding_ref`` closure from the
        registered operation; content-less Petri control outputs carry those fields and
        ``resource_ref`` as ``None``. This method applies the declared output multiset and
        allocates independent local token ids. :meth:`typed_snapshot` then derives one exact
        ``petri_token/v1`` ref and immutable ``PetriTokenState`` per token for the unified
        Registry batch. The output resource ref is payload provenance, never the token ref.
        The executor builds proposals from the transition body's
        :class:`~cpn.atom.team_net_executor.ProducedArtifact` outputs). A produced
        place that is not a declared output of ``t`` is still deposited (content-
        blind — the body knows its outputs; the T3 divergence check WARNS), but it
        is recorded in the returned ``off_arc`` count.

        STALE-IN-FLIGHT GUARD (§5.1bis step 3): if ``claim_epoch`` is older than the
        current epoch, a re-marking bumped the epoch while this firing was in
        flight → its outputs are stale → DROPPED (none deposited). The registry
        append the body already made is harmless (append-only / idempotent); the
        ledger simply refuses to deposit a stale-epoch fresh token.

        Returns ``(deposited, off_arc)``.
        """
    proposed = deepcopy(produced)
    selected_output_selector = getattr(self._net, 'output_effect_selector_for', lambda _transition_id, _place: None)
    selected_routes = Counter((spec['place'] for spec in proposed if spec.get('selected_route_occurrence') is True))
    selected_controls = Counter((spec['place'] for spec in proposed if spec.get('selected_control_occurrence') is True))
    for spec in proposed:
        if self._net.output_emit_for(t_id, spec['place']) == 'route_selected' and spec.get('selected_route_occurrence') is not True:
            raise MarkingStateError('route_selected requires explicit selected occurrences')
        if selected_output_selector(t_id, spec['place']) is not None and spec.get('selected_control_occurrence') is not True:
            raise MarkingStateError('selected control output requires an explicit selected occurrence')
        if 'selected_route_occurrence' in spec:
            if spec.pop('selected_route_occurrence') is not True or self._net.output_emit_for(t_id, spec['place']) != 'route_selected' or spec.get('resource_ref') is None:
                raise MarkingStateError('selected route requires an exact resource and declared lane')
        if 'selected_control_occurrence' in spec:
            if spec.pop('selected_control_occurrence') is not True or selected_output_selector(t_id, spec['place']) is None or spec.get('resource_ref') is not None:
                raise MarkingStateError('selected control output requires one declared contentless lane')
    if any((count > self._net.output_arc_weight(t_id, place) for place, count in selected_routes.items())):
        raise MarkingStateError('selected routes exceed declared output multiplicity')
    if any((count != 1 for count in selected_controls.values())):
        raise MarkingStateError('selected control output repeats one formal output arc')
    with self._lock:
        proposed = self._prevalidate_declared_output_colours(t_id, proposed, claim_epoch=claim_epoch)
        validated_produced = self._validated_output_specs(proposed, selected_route_places=frozenset(selected_routes))
        if claim_epoch < self._epoch:
            self._guard_suppressed_last = 0
            return (0, 0)
        declared_outputs = set(self._net.outputs_of(t_id))
        reusable_places = {place for place in self._net.places if self._net.place_kind(place) in {'agent_resource', 'resource_lease'}}
        _guarded = bool(self._net.output_guards_of(t_id) or self._net.output_emit_of(t_id) or self._net.output_color_expressions_of(t_id))
        _c: bool | str | None = None
        claimed: list[Token] = []
        if _guarded:
            claimed = self.claimed_tokens(t_id, claim_epoch)
            _decisions = {tok.verdict for tok in claimed if tok.verdict is not None}
            _c = next(iter(_decisions)) if len(_decisions) == 1 else None
        if not claimed:
            claimed = self.claimed_tokens(t_id, claim_epoch)
        operation_tokens = self.claimed_operation_tokens(t_id, claim_epoch)
        self._validate_runtime_token_refs(claimed, f'claimed_tokens[{t_id!r}]')
        inherited_continuation = continuation_from_tokens(claimed)
        self._guard_suppressed_last = 0
        deposited = 0
        off_arc = 0
        for spec in validated_produced:
            place = spec['place']
            explicit_route = self._net.output_emit_for(t_id, place) == 'route_selected'
            if explicit_route and place not in selected_routes:
                raise MarkingStateError('route_selected requires explicit selected occurrences')
            if place in reusable_places:
                continue
            if place not in declared_outputs:
                off_arc += 1
            if _guarded:
                _g = self._net.output_guard_for(t_id, place)
                if _g is not None:
                    _matched = _c is not None and (_c == _g if isinstance(_g, str) else _c is _g)
                    if not _matched:
                        self._guard_suppressed_last += 1
                        continue
            lease_claims = self._formal_output_lease_claims(t_id, place, operation_tokens, validated_produced, spec['lease_claims'])
            resource_ref = spec['resource_ref']
            work_resource_ref = spec['work_resource_ref']
            kind = spec['kind']
            verdict = spec['verdict']
            continuation = spec['continuation']
            if continuation is None:
                continuation = inherited_continuation
            if explicit_route:
                continuation = None
            expression = self._net.output_color_expression_for(t_id, place)
            if expression is not None:
                try:
                    projected_verdict = self._net.project_output_color(t_id, place, tuple(((token.place, token.verdict) for token in claimed)))
                except ValueError as exc:
                    raise MarkingStateError(f'output colour projection failed closed for {t_id!r}->{place!r}: {exc}') from exc
                if projected_verdict is None:
                    raise MarkingStateError('declared output colour expression produced no colour')
                if verdict is not None and verdict != projected_verdict:
                    raise MarkingStateError('transition body contradicted its declared output colour expression')
                verdict = projected_verdict
            if self._net.output_emit_for(t_id, place) == 'control_only':
                resource_ref = None
                work_resource_ref = None
                kind = None
                if selected_output_selector(t_id, place) is None:
                    lease_claims = ()
            if _guarded and verdict is None and (self._net.output_emit_for(t_id, place) == 'forward'):
                verdict = _c
            if _guarded and self._net.output_emit_for(t_id, place) == 'forward':
                explicit_source = self._net.forward_source_place(t_id, place)
                excluded_documents = self._net.logical_document_input_places(t_id)
                forwarded = {(tok.resource_ref, tok.work_resource_ref, tok.kind) for tok in claimed if tok.resource_ref is not None and (tok.place == explicit_source if explicit_source is not None else tok.place not in reusable_places and tok.place not in excluded_documents)}
                if len(forwarded) == 1:
                    f_ref, f_work_ref, f_kind = next(iter(forwarded))
                    if resource_ref is None:
                        resource_ref, kind = (f_ref, f_kind)
                        if work_resource_ref is None:
                            work_resource_ref = f_work_ref or f_ref
            weight = 1 if explicit_route else self._net.output_arc_weight(t_id, place) or 1
            for _ in range(weight):
                self._new_token(place=place, epoch=self._epoch, producer=t_id, consumer=None, resource_ref=resource_ref, work_resource_ref=work_resource_ref, kind=kind, verdict=verdict, continuation=continuation, lease_claims=lease_claims)
                deposited += 1
        if reusable_places:
            consumed_reusable = {tok.place: tok for tok in claimed if tok.place in reusable_places}
            for r_place, consumed_token in consumed_reusable.items():
                if self._net.output_emit_for(t_id, r_place) != 'content_less':
                    continue
                r_consumers = tuple(sorted(self._net.consumers_of(r_place)))
                consumer = r_consumers[0] if len(r_consumers) == 1 else None
                self._new_token(place=r_place, epoch=self._epoch, producer=t_id, consumer=consumer, resource_ref=consumed_token.resource_ref if self._net.place_kind(r_place) == 'resource_lease' else None)
                deposited += 1
        returned_read_places = {place for place, scope in self._net.read_scope_of(t_id) if scope == 'read' and place not in reusable_places}
        for consumed_token in claimed:
            if consumed_token.place not in returned_read_places:
                continue
            self._new_token(place=consumed_token.place, epoch=self._epoch, producer=consumed_token.producer, consumer=consumed_token.consumer, resource_ref=consumed_token.resource_ref, work_resource_ref=consumed_token.work_resource_ref, kind=consumed_token.kind, override_warning=deepcopy(consumed_token.override_warning), verdict=consumed_token.verdict, continuation=deepcopy(consumed_token.continuation), lease_identity_ref=consumed_token.lease_identity_ref, lease_claims=deepcopy(consumed_token.lease_claims))
            deposited += 1
        variable_binding = self._net.variable_resource_arc_for(t_id)
        if variable_binding is not None:
            lease_updates: dict[VersionRef, ResourceVersionRef] = {}
            output_by_port = {spec['output_port_id']: spec['resource_ref'] for spec in validated_produced if spec['output_port_id'] is not None and spec['resource_ref'] is not None}
            for logical in self._net.logical_artifact_bindings:
                if logical.producer_transition_id != t_id:
                    continue
                updated = output_by_port.get(logical.output_port_id)
                if updated is not None:
                    lease_updates[self._version_ref_from_exact(logical.slot_ref)] = updated
            claimed_pool = sorted((token for token in claimed if token.place == variable_binding.lease_pool_place), key=lambda token: token.token_id)
            _claim_key, active_claim = self._active_claim(t_id, claim_epoch)
            expected_identities = {identity for identity, mode in active_claim.lease_accesses if mode != 'read'}
            claimed_identities = {token.lease_identity_ref for token in claimed_pool}
            if None in claimed_identities or len(claimed_pool) != len(expected_identities) or claimed_identities != expected_identities:
                raise MarkingStateError('variable return inscription differs from admitted claim multiset')
            for consumed_token in claimed_pool:
                identity = consumed_token.lease_identity_ref
                if identity is None:
                    raise MarkingStateError('claimed lease-pool token lacks its colour identity')
                self._new_token(place=variable_binding.lease_pool_place, epoch=self._epoch, producer=t_id, consumer=None, resource_ref=lease_updates.get(identity, consumed_token.resource_ref), lease_identity_ref=identity)
                deposited += 1
        pool_places = {pool.lease_pool_place for pool in self._net.resource_lease_pool_bindings}
        mint_specs = sorted((spec for spec in validated_produced if self._net.output_emit_for(t_id, spec['place']) == 'lease_mint'), key=lambda spec: (str(spec['resource_ref'].resource_id) if spec['resource_ref'] is not None else '', str(spec['resource_ref'].resource_version_id) if spec['resource_ref'] is not None else ''))
        live_identities = {token.lease_identity_ref for token in self._tokens if token.epoch == self._epoch and token.consumed_by is None and (token.lease_identity_ref is not None)}
        for spec in mint_specs:
            resource_ref = spec['resource_ref']
            if spec['place'] not in pool_places or not isinstance(resource_ref, ResourceVersionRef) or spec['lease_claims']:
                raise MarkingStateError('lease_mint requires one exact claim-free registered product')
            identity = resource_ref.as_version_ref()
            if identity in live_identities:
                raise MarkingStateError('lease_mint would duplicate one live exact M=1 identity')
            self._new_token(place=spec['place'], epoch=self._epoch, producer=t_id, consumer=None, resource_ref=resource_ref, lease_identity_ref=identity)
            live_identities.add(identity)
            deposited += 1
        claim_pool_by_place: dict[str, str] = {}
        for binding in self._net.variable_resource_arcs:
            pool_place = binding.lease_pool_place
            prior = claim_pool_by_place.setdefault(binding.claim_token_place, pool_place)
            if prior != pool_place:
                raise MarkingStateError('claim-token place has ambiguous lease pools')
        live_pool_identities = {pool.lease_pool_place: set() for pool in self._net.resource_lease_pool_bindings}
        for candidate in self._tokens:
            if candidate.place in live_pool_identities and candidate.epoch == self._epoch and (candidate.consumed_by is None) and (candidate.lease_identity_ref is not None):
                live_pool_identities[candidate.place].add(candidate.lease_identity_ref)
        for spec in validated_produced:
            pool_place = claim_pool_by_place.get(spec['place'])
            if pool_place is None:
                continue
            missing = {claim['lease_identity_ref'] for claim in spec['lease_claims'] if claim['lease_identity_ref'] not in live_pool_identities.get(pool_place, set())}
            if missing:
                raise MarkingStateError('output lease claim lacks an explicitly live pool colour')
        staging_sets = tuple((spec['lease_claims'] for spec in validated_produced if any(('staging_place' in claim for claim in spec['lease_claims']))))
        if staging_sets:
            if any((claims != staging_sets[0] for claims in staging_sets[1:])):
                raise MarkingStateError('finalization staging claims are conflicting')
            represented_staging_places = {spec['place'] for spec in validated_produced if spec['lease_claims'] == staging_sets[0]}
            staged_pairs: set[tuple[str, ResourceVersionRef]] = set()
            for claim in staging_sets[0]:
                staging_place = claim.get('staging_place')
                if staging_place is None:
                    continue
                resource_ref = claim['expected_resource_ref']
                if not isinstance(resource_ref, ResourceVersionRef) or staging_place not in self._net.outputs_of(t_id) or self._net.output_emit_for(t_id, staging_place) not in {None, 'forward'}:
                    raise MarkingStateError('finalization claim lacks one declared staging output')
                pair = (staging_place, resource_ref)
                if pair in staged_pairs:
                    raise MarkingStateError('finalization staging repeats one place/resource pair')
                staged_pairs.add(pair)
                if staging_place not in represented_staging_places:
                    raise MarkingStateError('staging metadata cannot synthesize an undeclared token')
        return (deposited, off_arc)
