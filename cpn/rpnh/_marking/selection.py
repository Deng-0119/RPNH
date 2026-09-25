"""Shared Petri eligibility and deterministic transition selection."""
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

def is_marked(self, place: str) -> bool:
    """True iff ``place`` holds ≥ 1 fresh token (the goal-place test, §3.2)."""
    with self._lock:
        return bool(self._fresh_on(place, None))

def fresh_count(self, place: str, consumer: Optional[str]=None) -> int:
    """§36 multi-node B5 — the number of FRESH tokens on ``place`` addressed-to / shared-for
        ``consumer`` (the SAME count the §35 PB counter-guard enabling rule reads via
        ``len(self._fresh_on(place, consumer))``). Used to feed the STRUCTURAL escalation consult
        the reject counter-place TALLY (the number of rejects a churning gate accrued) instead of
        the imperative ``_gG_reject_count_by_gate`` map, which is never written on a purely-
        structural (injected) node. Content-blind (a token COUNT; judges no content); takes the
        lock (``_fresh_on`` requires it)."""
    with self._lock:
        return len(self._fresh_on(place, consumer))

def _timed_wait_guard_state_map(self, timed_wait_guard_states: Sequence[TimedWaitGuardState]) -> dict[str, TimedWaitGuardState]:
    """Validate and index one checkpoint-local timed-wait projection."""
    states: dict[str, TimedWaitGuardState] = {}
    for index, state in enumerate(timed_wait_guard_states):
        if not isinstance(state, TimedWaitGuardState):
            raise MarkingStateError(f'timed_wait_guard_states[{index}] is not TimedWaitGuardState')
        if not isinstance(state.transition_id, str) or not state.transition_id:
            raise MarkingStateError(f'timed_wait_guard_states[{index}].transition_id must be a non-empty string')
        if not isinstance(state.expired, bool):
            raise MarkingStateError(f'timed_wait_guard_states[{index}].expired must be a boolean')
        if state.transition_id in states:
            raise MarkingStateError(f'timed_wait_guard_states contains duplicate transition state {state.transition_id!r}')
        states[state.transition_id] = state
    return states

def is_enabled(self, t_id: str, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> bool:
    """True iff every input place of ``t`` holds ≥ arc-weight FRESH tokens
        consumable by ``t`` (the ENABLING RULE, §3.2)."""
    with self._lock:
        timed_wait_guard_state_map = self._timed_wait_guard_state_map(timed_wait_guard_states)
        return self._is_enabled_locked(t_id, timed_wait_guard_states, _timed_wait_guard_state_map=timed_wait_guard_state_map)

def _claim_input_arcs(self, t_id: str) -> tuple[tuple[str, str, int], ...]:
    """Return the exact consumable Petri inputs for one business firing.

        A Registry-bound born-D1 net carries its declared file inputs as
        separately registered ``petri_token/v1`` records.  Those source-place
        tokens are therefore genuine, auditable Petri inputs: enabling and
        consume-on-fire must include ``registry_read_arcs``.  The legacy
        process-local ignition token is excluded in that mode because it has no
        counterpart in the Registry firing admission.

        Unregistered nets retain the historical ignition model unchanged.
        Reference and counter arcs remain outside both collections and are
        consequently never consumed here.
        """
    net = self._net
    if not isinstance(getattr(net, 'registry_net_ref', None), VersionRef):
        return tuple((arc for arc in net.token_input_arcs if arc[1] == t_id))
    ignition = set(net.synthetic_ignition_arcs)
    arcs = [arc for arc in net.token_input_arcs if arc[1] == t_id and arc not in ignition]
    arcs.extend((arc for arc in net.registry_read_arcs if arc[1] == t_id))
    return tuple(arcs)

def _is_enabled_locked(self, t_id: str, timed_wait_guard_states: Sequence[TimedWaitGuardState]=(), *, _timed_wait_guard_state_map: Optional[Mapping[str, TimedWaitGuardState]]=None) -> bool:
    net = self._net
    timed_wait_guard_state_map = self._timed_wait_guard_state_map(timed_wait_guard_states) if _timed_wait_guard_state_map is None else _timed_wait_guard_state_map
    if net.registered_fault_transition(t_id) is not None:
        raise MarkingStateError('retired registered fault transition entered current marking')
    claim_arcs = self._claim_input_arcs(t_id)
    for p, _t, w in claim_arcs:
        if len(self._fresh_on(p, t_id)) < w:
            return False
    for cplace, threshold, kind, scope in net.count_predicates_of(t_id):
        reader = None if scope == 'all' else t_id
        count = len(self._fresh_on(cplace, reader))
        if not count_guard_holds(count, threshold, kind):
            return False
    for vplace, expected in net.verdict_guards_of(t_id):
        if not self.verdict_guard_enabled(vplace, expected, t_id):
            return False
    if any((guard.transition_id == t_id for guard in net.timed_wait_guards)):
        state = timed_wait_guard_state_map.get(t_id)
        if state is None or not state.expired:
            return False
    structurally_enabled = bool(claim_arcs) or net.is_guard_only_transition(t_id)
    if not structurally_enabled:
        return False
    return self._try_reserve(t_id, set()) is not None

def _version_ref_from_exact(value: object) -> VersionRef:
    try:
        return VersionRef(str(getattr(value, 'entity_type')), TypedId.parse(str(getattr(value, 'logical_id'))), TypedId.parse(str(getattr(value, 'version_id'))))
    except (TypeError, ValueError) as exc:
        raise MarkingResourceRefError('declared lease claim is not one exact VersionRef') from exc

def _resource_ref_from_exact(value: object | None) -> Optional[ResourceVersionRef]:
    if value is None:
        return None
    try:
        if str(getattr(value, 'entity_type')) != 'resource_version/v1':
            raise ValueError('not a resource version')
        return ResourceVersionRef(TypedId.parse(str(getattr(value, 'logical_id')), expected='resource'), TypedId.parse(str(getattr(value, 'version_id')), expected='resource_version'))
    except (TypeError, ValueError) as exc:
        raise MarkingResourceRefError('declared expected lease resource is not exact') from exc

def _declared_lease_claims_for_place(self, place: str) -> tuple[dict, ...]:
    bindings = tuple((binding for binding in self._net.variable_resource_arcs if binding.claim_token_place == place))
    if not bindings:
        return ()
    projections = tuple((self._merge_lease_claims(tuple(({'lease_identity_ref': self._version_ref_from_exact(claim.lease_identity_ref), 'expected_resource_ref': self._resource_ref_from_exact(claim.expected_resource_ref), 'access_mode': claim.access_mode} for claim in binding.initial_claims))) for binding in bindings))
    declared = projections[0]
    if any((projection != declared for projection in projections[1:])):
        raise MarkingStateError(f'claim-token place {place!r} has ambiguous variable arcs')
    return declared

def _declared_output_lease_claims(self, transition_id: str, output_place: str, operation_tokens: Sequence[Token]) -> tuple[dict, ...]:
    """Project only typed output-arc lease-colour expressions."""
    claims = []
    for expression in self._net.output_lease_claims_for(transition_id, output_place):
        identity = self._net.lease_identity_ref(expression.lease_identity)
        source = expression.expected_resource_source
        expected = None
        if source is not None:
            candidates = tuple((token for token in operation_tokens if token.place == source and token.resource_ref is not None))
            if len(candidates) != 1:
                raise MarkingStateError('output lease-colour projection lacks one exact input resource')
            expected = candidates[0].resource_ref
        claims.append({'lease_identity_ref': identity, 'expected_resource_ref': expected, 'access_mode': expression.access_mode})
    return tuple(claims)

def _declared_output_lease_exclusions(self, transition_id: str, output_place: str) -> frozenset[VersionRef]:
    return frozenset((self._net.lease_identity_ref(name) for name in self._net.output_lease_claim_exclusions_for(transition_id, output_place)))

def _formal_output_lease_claims(self, transition_id: str, output_place: str, operation_tokens: Sequence[Token], produced: Sequence[dict], supplied: Sequence[dict]) -> tuple[dict, ...]:
    """Derive one output claim set only from declared Petri sources."""
    excluded = self._declared_output_lease_exclusions(transition_id, output_place)
    normalized_supplied = []
    for claim in supplied:
        staging_place = claim.get('staging_place')
        if staging_place not in {None, output_place}:
            raise MarkingStateError('registered output claim names another carrier place')
        normalized_supplied.append({'lease_identity_ref': claim['lease_identity_ref'], 'expected_resource_ref': claim['expected_resource_ref'], 'access_mode': claim['access_mode']})
    supplied_claims = self._merge_lease_claims(normalized_supplied)
    if any((claim['lease_identity_ref'] in excluded for claim in supplied_claims)):
        raise MarkingStateError('registered output claims contradict the formal arc exclusion')
    declared = self._merge_lease_claims(self._declared_output_lease_claims(transition_id, output_place, operation_tokens), self._declared_lease_claims_for_place(output_place))
    declared = tuple((claim for claim in declared if claim['lease_identity_ref'] not in excluded))
    expression = self._net.output_lease_claim_set_for(transition_id, output_place)
    if expression is None:
        if supplied_claims and supplied_claims != declared:
            raise MarkingStateError('registered output claims lack a formal Petri claim-set expression')
        return declared
    projected: list[dict] = list(declared)
    if expression.inherit_input_claims:
        projected.extend((claim for token in operation_tokens for claim in token.lease_claims))
    if expression.operation_resource_updates:
        variable = self._net.variable_resource_arc_for(transition_id)
        if variable is None:
            raise MarkingStateError('operation-resource claim projection lacks its variable arc')
        output_by_port = {spec['output_port_id']: spec['resource_ref'] for spec in produced if spec['output_port_id'] is not None and spec['resource_ref'] is not None}
        for logical in self._net.logical_artifact_bindings:
            if logical.producer_transition_id != transition_id:
                continue
            resource_ref = output_by_port.get(logical.output_port_id)
            if resource_ref is not None:
                projected.append({'lease_identity_ref': self._version_ref_from_exact(logical.slot_ref), 'expected_resource_ref': resource_ref, 'access_mode': expression.access_mode})
        projected.extend(({'lease_identity_ref': spec['resource_ref'].as_version_ref(), 'expected_resource_ref': spec['resource_ref'], 'access_mode': expression.access_mode} for spec in produced if spec['resource_ref'] is not None and self._net.output_emit_for(transition_id, spec['place']) == 'lease_mint'))
    if expression.claim_output_products:
        projected.extend(({'lease_identity_ref': spec['resource_ref'].as_version_ref(), 'expected_resource_ref': spec['resource_ref'], 'access_mode': expression.access_mode} for spec in produced if spec['resource_ref'] is not None))
    projected_claims = self._merge_lease_claims(projected)
    projected_by_identity = {claim['lease_identity_ref']: claim for claim in projected_claims}
    extras = tuple((claim for claim in supplied_claims if claim['lease_identity_ref'] not in projected_by_identity))
    if extras:
        pool_name = expression.registered_pool_subset
        if pool_name is None:
            raise MarkingStateError('registered output claims exceed their formal Petri sources')
        pool_place = self._net.lease_pool_place_for(pool_name)
        live_pool = {token.lease_identity_ref: token.resource_ref for token in self._tokens if token.place == pool_place and token.epoch == self._epoch and (token.consumed_by is None) and (token.lease_identity_ref is not None)}
        if any((claim['access_mode'] != expression.access_mode or claim['lease_identity_ref'] not in live_pool or claim['expected_resource_ref'] is None or (live_pool[claim['lease_identity_ref']] != claim['expected_resource_ref']) for claim in extras)):
            raise MarkingStateError('registered output claim is outside its formal live lease pool')
    result = self._merge_lease_claims(projected_claims, extras)
    if supplied_claims and supplied_claims != result:
        raise MarkingStateError('registered output claims differ from the formal Petri projection')
    return result

def _merge_lease_claims(*groups: Sequence[dict]) -> tuple[dict, ...]:
    """Merge exact upstream and destination claims without weakening either."""
    merged: dict[VersionRef, dict] = {}
    for group in groups:
        for claim in group:
            identity = claim['lease_identity_ref']
            prior = merged.get(identity)
            if prior is None:
                merged[identity] = claim
                continue
            if prior['access_mode'] != claim['access_mode']:
                raise MarkingStateError('one lease identity has conflicting handoff and destination access modes')
            prior_expected = prior['expected_resource_ref']
            next_expected = claim['expected_resource_ref']
            if prior_expected is not None and next_expected is not None and (prior_expected != next_expected):
                raise MarkingStateError('one lease identity has conflicting handoff and destination resource versions')
            if prior_expected is None and next_expected is not None:
                merged[identity] = claim
    return tuple((merged[identity] for identity in sorted(merged, key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id)))))

def _variable_lease_token_ids(self, t_id: str, ordinary_ids: Sequence[int], reserved: set[int], *, allowed_token_ids: Optional[set[int]]=None) -> Optional[tuple[list[int], list[int]]]:
    binding = self._net.variable_resource_arc_for(t_id)
    if binding is None:
        return ([], [])
    by_id = {token.token_id: token for token in self._tokens}
    claim_tokens = [by_id[token_id] for token_id in ordinary_ids if token_id in by_id and by_id[token_id].place == binding.claim_token_place]
    if len(claim_tokens) != 1:
        return None
    pool_tokens = sorted((token for token in self._fresh_on(binding.lease_pool_place, t_id) if token.token_id not in reserved and token.token_id not in ordinary_ids), key=lambda token: token.token_id)
    selected_consumed: list[int] = []
    selected_references: list[int] = []
    for claim in claim_tokens[0].lease_claims:
        identity = claim['lease_identity_ref']
        expected = claim['expected_resource_ref']
        access_mode = claim['access_mode']
        selected = {*selected_consumed, *selected_references}
        matches = [token for token in pool_tokens if token.token_id not in selected and token.lease_identity_ref == identity and (expected is None or token.resource_ref == expected) and (access_mode == 'produce' or token.resource_ref is not None) and (allowed_token_ids is None or token.token_id in allowed_token_ids)]
        if len(matches) != 1:
            return None
        if access_mode == 'read':
            selected_references.append(matches[0].token_id)
        else:
            selected_consumed.append(matches[0].token_id)
    return (selected_consumed, selected_references)

def _lease_accesses_compatible(left: Sequence[tuple[VersionRef, str]], right: Sequence[tuple[VersionRef, str]]) -> bool:
    right_by_identity: dict[VersionRef, set[str]] = {}
    for identity, mode in right:
        right_by_identity.setdefault(identity, set()).add(mode)
    return all((identity not in right_by_identity or (mode == 'read' and right_by_identity[identity] == {'read'}) for identity, mode in left))

def enabled_transitions(self, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> list[str]:
    """Diagnostic enabled ids only; never an allocation/acceptance order."""
    with self._lock:
        timed_wait_guard_state_map = self._timed_wait_guard_state_map(timed_wait_guard_states)
        transition_ids = (*self._net.transitions, *sorted(self._net.registered_fault_transition_routes))
        return [tid for tid in transition_ids if self._is_enabled_locked(tid, timed_wait_guard_states, _timed_wait_guard_state_map=timed_wait_guard_state_map)]

def verdict_guard_enabled(self, place: str, expected: bool | str, t_id: Optional[str]=None) -> bool:
    """§36 P0 verdict-guard family (design §2.2/§8) — the CONTENT-BLIND enabling-time
        evaluator of a ``kind="verdict"`` guard. True iff ``place`` holds ≥ 1 FRESH token whose
        RESERVED VERDICT COLOR (:attr:`Token.verdict`) is EXACTLY ``expected`` (identity, so a
        ``True``-guard for ``T_pass`` matches only a confirm token and a ``False``-guard for
        ``T_reject`` matches only a reject token; a ``None`` / unstamped verdict matches
        NEITHER — the same tri-state polarity as ``_is_reviewer_confirm`` (``is True``) /
        ``_is_reviewer_reject`` (``is False``)). ``t_id`` addresses the read (consumable-by /
        shared), like :meth:`_fresh_on`; ``None`` = "is any such-colored token present".

        This is the marking half of the verdict guard family — the piece Phase 3 wires into the
        enabling rule (``_is_enabled_locked``) as a sibling of the count-guard loop. It reads a
        structural token COLOR (like ``kind`` / ``faulted``), NOT artifact content, and routes on
        the boolean's truthiness — it judges no meaning (§34.0, ``feedback_no_coarse_
        deterministic_parsing_of_llm_io``: the color was set by the exact-key reserved read at
        stamp time, never a substring/keyword scan). Pure read under the ledger lock. LIVE behind
        the explicit-review Petri subnet (Phase 3 landed): ``_is_enabled_locked`` calls this for each
        ``kind="verdict"`` guard so ``T_pass`` / ``T_reject`` fire on the reviewer's ``confirmed``
        color. Still byte-identical for a legacy net that declares no verdict guard (the guard loop
        is empty).

        §39.2 (v2.53) — a STRING ``expected`` (the A2C 4-way decision color
        ``{"pass","continue","escalate","give_up"}``) is matched by ``==`` (interning-safe); a
        BOOLEAN ``expected`` keeps the pre-§39 ``is`` identity (so the confirm/reject tri-state
        ``True``/``False``/``None`` is byte-identical). A ``None`` / mistyped token color matches
        NEITHER polarity. Content-blind either way (§34.0)."""
    with self._lock:
        return any((_verdict_color_matches(tok.verdict, expected) for tok in self._fresh_on(place, t_id)))
