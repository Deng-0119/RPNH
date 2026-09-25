"""Retained historical timed marking implementation behind the marking gate."""
from __future__ import annotations

import itertools
import json
import math
import random
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

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

if TYPE_CHECKING:
    from cpn.rpnh.petri_runtime_interfaces import MarkingNetStructure
    from cpn.rpnh.registry.resources import (
        ExecutableNetAuthority,
        TypedMarkingAuthority,
    )

def _bound_input(token: Token) -> BoundInputToken:
    if not isinstance(token.token_ref, VersionRef):
        raise MarkingStateError('timed binding input lacks committed petri_token/v1 authority')
    return BoundInputToken(place=token.place, token_id=token.token_id, token_ref=token.token_ref)

def _make_timed_binding(transition_id: str, claim_epoch: int, tokens: tuple[Token, ...], resource_demands: tuple[ResourceDemand, ...]) -> TimedBinding:
    inputs = tuple(sorted((TeamNetMarking._bound_input(token) for token in tokens), key=lambda item: (item.place, _version_ref_key(item.token_ref), item.token_id)))
    payload = {'claim_epoch': claim_epoch, 'inputs': [{'place': item.place, 'token_id': item.token_id, 'token_ref': {'entity_type': item.token_ref.entity_type, 'logical_id': str(item.token_ref.entity_id), 'version_id': str(item.token_ref.version_id)}} for item in inputs], 'resource_demands': [{'count': demand.count, 'place': demand.place} for demand in resource_demands], 'transition_id': transition_id}
    canonical_key = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return TimedBinding(transition_id=transition_id, claim_epoch=claim_epoch, inputs=inputs, resource_demands=resource_demands, canonical_key=canonical_key)

def _enumerate_timed_bindings_locked(self, transition_id: str) -> tuple[TimedBinding, ...]:
    """Enumerate every current-epoch compatible binding (caller holds lock)."""
    if transition_id not in self._net.transitions:
        raise MarkingStateError(f'timed binding transition {transition_id!r} is not declared')
    for counter_place, threshold, kind, scope in self._net.count_predicates_of(transition_id):
        reader = None if scope == 'all' else transition_id
        count = len(self._fresh_on(counter_place, reader))
        if not count_guard_holds(count, threshold, kind):
            return ()
    resource_places = self._net.agent_resource_places()
    input_weights: dict[str, int] = {}
    demand_weights: dict[str, int] = {}
    for place, _target, weight in self._claim_input_arcs(transition_id):
        target = demand_weights if place in resource_places else input_weights
        target[place] = target.get(place, 0) + weight
    resource_demands = tuple((ResourceDemand(place=place, count=demand_weights[place]) for place in sorted(demand_weights)))
    guard_colors = dict(self._net.verdict_guards_of(transition_id))
    choices: list[tuple[tuple[Token, ...], ...]] = []
    for place in sorted(input_weights):
        candidates = list(self._fresh_on(place, transition_id))
        if place in guard_colors:
            candidates = [token for token in candidates if _verdict_color_matches(token.verdict, guard_colors[place])]
        candidates.sort(key=lambda token: (_version_ref_key(token.token_ref) if isinstance(token.token_ref, VersionRef) else ('', '', ''),))
        weight = input_weights[place]
        if len(candidates) < weight:
            return ()
        choices.append(tuple(itertools.combinations(candidates, weight)))
    if not input_weights and (not demand_weights) and (not self._net.is_guard_only_transition(transition_id)):
        return ()
    bindings: list[TimedBinding] = []
    for selected_groups in itertools.product(*choices):
        selected = tuple((token for group in selected_groups for token in group))
        token_ids = tuple((token.token_id for token in selected))
        if len(set(token_ids)) != len(token_ids):
            continue
        bindings.append(self._make_timed_binding(transition_id, self._epoch, selected, resource_demands))
    return tuple(sorted(bindings, key=lambda binding: binding.canonical_key))

def _timed_binding_count_locked(self, transition_id: str) -> int:
    """Count the exact Cartesian binding space without materializing it."""
    if transition_id not in self._net.transitions:
        raise MarkingStateError(f'timed binding transition {transition_id!r} is not declared')
    for counter_place, threshold, kind, scope in self._net.count_predicates_of(transition_id):
        reader = None if scope == 'all' else transition_id
        count = len(self._fresh_on(counter_place, reader))
        if not count_guard_holds(count, threshold, kind):
            return 0
    resource_places = self._net.agent_resource_places()
    input_weights: dict[str, int] = {}
    has_resource_demand = False
    for place, _target, weight in self._claim_input_arcs(transition_id):
        if place in resource_places:
            has_resource_demand = True
        else:
            input_weights[place] = input_weights.get(place, 0) + weight
    if not input_weights and (not has_resource_demand) and (not self._net.is_guard_only_transition(transition_id)):
        return 0
    guard_colors = dict(self._net.verdict_guards_of(transition_id))
    binding_count = 1
    for place in sorted(input_weights):
        candidates = self._fresh_on(place, transition_id)
        if place in guard_colors:
            candidates = [token for token in candidates if _verdict_color_matches(token.verdict, guard_colors[place])]
        weight = input_weights[place]
        if len(candidates) < weight:
            return 0
        binding_count *= math.comb(len(candidates), weight)
    return binding_count

def enumerate_timed_bindings(self, transition_id: str, *, queue_bound: Optional[int]=None) -> BindingEnumeration:
    """Return the deterministic binding set, failing closed before overflow.

        The exact Cartesian count is computed without constructing combinations.
        If it exceeds ``queue_bound``, no binding is materialized or truncated.
        """
    if queue_bound is not None:
        queue_bound = _nonnegative_integer(queue_bound, 'queue_bound')
    with self._lock:
        binding_count = self._timed_binding_count_locked(transition_id)
        if queue_bound is not None and binding_count > queue_bound:
            return BindingEnumeration(transition_id=transition_id, bindings=(), overflow=BindingQueueOverflow(queue_bound=queue_bound, binding_count=binding_count))
        bindings = self._enumerate_timed_bindings_locked(transition_id)
        if len(bindings) != binding_count:
            raise MarkingStateError('timed binding count changed during locked materialization')
        return BindingEnumeration(transition_id=transition_id, bindings=bindings, overflow=None)

def _canonical_firing_id(firing_id: object) -> str:
    if not isinstance(firing_id, str) or not firing_id or firing_id != firing_id.strip():
        raise MarkingStateError('firing_id must be a non-empty canonical string')
    return firing_id

def claim_timed_binding(self, binding: TimedBinding, *, firing_id: str, resource_claims: Optional[Mapping[str, Sequence[str]]]=None) -> ActiveFiringClaim:
    """Atomically claim one queued binding and scheduler-selected resources.

        ``resource_claims`` carries the exact canonical token identities selected
        from the same runtime availability snapshot used by the scheduler.  The
        marking never independently chooses a lookalike pool unit.
        """
    if not isinstance(binding, TimedBinding):
        raise MarkingStateError('timed START requires a TimedBinding')
    canonical_firing_id = self._canonical_firing_id(firing_id)
    supplied_claims = {} if resource_claims is None else dict(resource_claims)
    if any((not isinstance(key, str) or not key for key in supplied_claims)):
        raise MarkingStateError('resource claims require canonical place ids')
    with self._lock:
        if canonical_firing_id in self._timed_active_claims:
            raise MarkingStateError(f'firing_id {canonical_firing_id!r} already has an active claim')
        current = next((item for item in self._enumerate_timed_bindings_locked(binding.transition_id) if item.canonical_key == binding.canonical_key), None)
        if current is None or current != binding:
            raise MarkingStateError('queued binding is stale or differs from the exact current marking')
        by_id = {token.token_id: token for token in self._tokens}
        selected_resources: list[Token] = []
        expected_places = {item.place for item in binding.resource_demands}
        if set(supplied_claims) != expected_places:
            raise MarkingStateError('resource claims must name exactly every demanded place')
        for demand in binding.resource_demands:
            candidates = [token for token in self._fresh_on(demand.place, binding.transition_id)]
            by_exact_id = {exact_token_identity(token.token_ref): token for token in candidates if isinstance(token.token_ref, VersionRef)}
            requested = tuple(supplied_claims[demand.place])
            if len(requested) != demand.count or len(set(requested)) != len(requested) or any((item not in by_exact_id for item in requested)):
                raise MarkingStateError(f'exact resource demand {demand.place!r} is unavailable')
            selected_resources.extend((by_exact_id[item] for item in requested))
        claimed_tokens = [by_id[item.token_id] for item in binding.inputs if item.token_id in by_id] + selected_resources
        expected_count = len(binding.inputs) + sum((demand.count for demand in binding.resource_demands))
        if len(claimed_tokens) != expected_count or len({token.token_id for token in claimed_tokens}) != expected_count or any((token.consumed_by is not None for token in claimed_tokens)):
            raise MarkingStateError('timed START cannot claim a token twice')
        for token in claimed_tokens:
            token.consumed_by = binding.transition_id
        resource_tokens = tuple(sorted((self._bound_input(token) for token in selected_resources), key=lambda item: (item.place, _version_ref_key(item.token_ref), item.token_id)))
        claim = ActiveFiringClaim(firing_id=canonical_firing_id, transition_id=binding.transition_id, binding_key=binding.canonical_key, claim_epoch=binding.claim_epoch, inputs=binding.inputs, resource_tokens=resource_tokens, resource_demands=binding.resource_demands)
        self._timed_active_claims[canonical_firing_id] = claim
        return claim

def active_timed_claim(self, firing_id: str) -> ActiveFiringClaim:
    """Return one firing-keyed active claim for scheduler projection."""
    canonical_firing_id = self._canonical_firing_id(firing_id)
    with self._lock:
        claim = self._timed_active_claims.get(canonical_firing_id)
        if claim is None:
            raise MarkingStateError(f'firing_id {canonical_firing_id!r} has no active claim')
        return claim

def active_timed_claims(self) -> tuple[ActiveFiringClaim, ...]:
    """Return all active claims in deterministic firing-id order."""
    with self._lock:
        return tuple((self._timed_active_claims[firing_id] for firing_id in sorted(self._timed_active_claims)))

def runtime_resource_availability(self, transition_id: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Expose exact currently available capacity identities by demanded place.

        This is the adapter bridge into ``RuntimeAvailability.resource_pools``.
        It is read-only and makes no reservation.
        """
    with self._lock:
        bindings = self._enumerate_timed_bindings_locked(transition_id)
        demands = bindings[0].resource_demands if bindings else ()
        rows: list[tuple[str, tuple[str, ...]]] = []
        for demand in demands:
            identities = tuple(sorted((exact_token_identity(token.token_ref) for token in self._fresh_on(demand.place, transition_id) if isinstance(token.token_ref, VersionRef))))
            rows.append((demand.place, identities))
        return tuple(rows)

def timed_claim_snapshot(self) -> dict:
    """Return the canonical persistence payload for the typed claim closure."""
    with self._lock:
        rows = _canonical_timed_claim_rows(self.active_timed_claims())
        return {'epoch': self._epoch, 'schema': 'team_net_marking/runtime_claims/v2', 'claims': rows}

def _local_state_locked(self) -> dict[str, object]:
    """Return the direct typed local token/counter and claim state."""
    return {'schema': 'team_net_marking/local_state/v1', 'marking': self._validated_candidate_mapping(), 'timed_claims': self.timed_claim_snapshot()}

def local_state(self) -> dict[str, object]:
    """Return the complete durable local state for direct comparison."""
    with self._lock:
        return deepcopy(self._local_state_locked())

def timed_checkpoint_authority(self, marking_authority: object) -> TimedMarkingCheckpointAuthority:
    """Bind active claims to one Registry-issued typed marking authority."""
    from cpn.rpnh.registry.resources import TypedMarkingAuthority
    if not isinstance(marking_authority, TypedMarkingAuthority):
        raise MarkingStateError('timed checkpoint requires a TypedMarkingAuthority')
    with self._lock:
        if marking_authority.epoch != self._epoch:
            raise MarkingStateError('timed claims and marking authority have different epochs')
        snapshot = self.timed_claim_snapshot()
        return TimedMarkingCheckpointAuthority(marking_authority=marking_authority, epoch=self._epoch, claims=self.active_timed_claims())

def from_timed_checkpoint_authority(cls, net: 'MarkingNetStructure', authority: TimedMarkingCheckpointAuthority) -> 'TeamNetMarking':
    """Atomically restore marking and exact claims from one typed authority."""
    if not isinstance(authority, TimedMarkingCheckpointAuthority):
        raise MarkingStateError('timed restore requires TimedMarkingCheckpointAuthority')
    restored = cls.from_authority(net, authority.marking_authority)
    if authority.epoch != restored.epoch:
        raise MarkingStateError('timed claim authority epoch differs from marking authority')
    for claim in authority.claims:
        if not isinstance(claim, ActiveFiringClaim):
            raise MarkingStateError('timed claim authority contains an untyped claim')
    rows = _canonical_timed_claim_rows(authority.claims)
    snapshot = {'schema': 'team_net_marking/runtime_claims/v2', 'epoch': authority.epoch, 'claims': rows}
    restored.restore_timed_claim_snapshot(snapshot)
    return restored

def restore_timed_claim_snapshot(self, snapshot: object) -> None:
    """Atomically restore firing-keyed claims from exact persisted identities.

        This method is intentionally separate from Registry authority restore:
        the adapter must present both the recovered token checkpoint and the
        scheduler claim snapshot. No claim is inferred from ``consumed_by``.
        """
    if not isinstance(snapshot, dict) or set(snapshot) != {'schema', 'epoch', 'claims'} or snapshot.get('schema') != 'team_net_marking/runtime_claims/v2' or (not isinstance(snapshot.get('claims'), list)):
        raise MarkingStateError('invalid runtime claim snapshot envelope')
    epoch = self._state_int(snapshot['epoch'], 'claim_snapshot.epoch')
    with self._lock:
        if epoch != self._epoch:
            raise MarkingStateError('runtime claim snapshot epoch differs from marking')
        if self._timed_active_claims:
            raise MarkingStateError('runtime claims are already active')
        by_identity = {exact_token_identity(token.token_ref): token for token in self._tokens if isinstance(token.token_ref, VersionRef)}
        proposed: dict[str, ActiveFiringClaim] = {}
        occupied: set[int] = set()
        tokens_to_consume: list[tuple[Token, str]] = []
        for index, row in enumerate(snapshot['claims']):
            path = f'claim_snapshot.claims[{index}]'
            expected = {'binding_key', 'claim_epoch', 'firing_id', 'input_ids', 'resource_claims', 'transition_id'}
            if not isinstance(row, dict) or set(row) != expected:
                raise MarkingStateError(f'{path} has invalid fields')
            firing_id = self._canonical_firing_id(row['firing_id'])
            if firing_id in proposed:
                raise MarkingStateError(f'{path} repeats a firing identity')
            transition_id = self._optional_string(row['transition_id'], f'{path}.transition_id')
            if transition_id not in self._net.transitions:
                raise MarkingStateError(f'{path} names an undeclared transition')
            claim_epoch = self._state_int(row['claim_epoch'], f'{path}.claim_epoch')
            if claim_epoch != self._epoch:
                raise MarkingStateError(f'{path} belongs to another epoch')
            binding_key = self._optional_string(row['binding_key'], f'{path}.binding_key')
            if binding_key is None:
                raise MarkingStateError(f'{path} binding key is invalid')
            if not isinstance(row['input_ids'], list):
                raise MarkingStateError(f'{path}.input_ids must be a list')
            input_ids = tuple(row['input_ids'])
            if any((not isinstance(item, str) for item in input_ids)) or len(set(input_ids)) != len(input_ids) or tuple(sorted(input_ids)) != input_ids:
                raise MarkingStateError(f'{path}.input_ids are not exact and unique')
            input_tokens = [by_identity.get(item) for item in input_ids]
            if any((item is None for item in input_tokens)):
                raise MarkingStateError(f'{path} references an unavailable input token')
            resource_rows = row['resource_claims']
            if not isinstance(resource_rows, list):
                raise MarkingStateError(f'{path}.resource_claims must be a list')
            if tuple((item.get('place') if isinstance(item, dict) else None for item in resource_rows)) != tuple(sorted((item.get('place') if isinstance(item, dict) else '' for item in resource_rows))):
                raise MarkingStateError(f'{path}.resource_claims are not in canonical place order')
            demands: list[ResourceDemand] = []
            resource_tokens: list[Token] = []
            seen_places: set[str] = set()
            for claim_row in resource_rows:
                if not isinstance(claim_row, dict) or set(claim_row) != {'count', 'place', 'resource_ids'} or (not isinstance(claim_row['resource_ids'], list)):
                    raise MarkingStateError(f'{path} has an invalid resource claim')
                place = self._optional_string(claim_row['place'], f'{path}.resource_claim.place')
                resource_ids = tuple(claim_row['resource_ids'])
                demand_count = self._state_int(claim_row['count'], f'{path}.resource_claim.count', minimum=1)
                if place is None or place in seen_places or len(resource_ids) not in (0, demand_count) or (len(set(resource_ids)) != len(resource_ids)) or (tuple(sorted(resource_ids)) != resource_ids):
                    raise MarkingStateError(f'{path} resource claim is not exact')
                seen_places.add(place)
                resolved = [by_identity.get(item) for item in resource_ids]
                if any((item is None or item.place != place for item in resolved)):
                    raise MarkingStateError(f'{path} resource identity/place mismatch')
                demands.append(ResourceDemand(place, demand_count))
                resource_tokens.extend(resolved)
            expected_inputs: dict[str, int] = {}
            expected_demands: dict[str, int] = {}
            resource_places = self._net.agent_resource_places()
            for place, _target, weight in self._claim_input_arcs(transition_id):
                target = expected_demands if place in resource_places else expected_inputs
                target[place] = target.get(place, 0) + weight
            actual_inputs: dict[str, int] = {}
            for token in input_tokens:
                assert token is not None
                actual_inputs[token.place] = actual_inputs.get(token.place, 0) + 1
                if token.epoch != claim_epoch or token.consumer not in (None, transition_id):
                    raise MarkingStateError(f'{path} input identity is incompatible with transition/epoch')
            actual_demands = {item.place: item.count for item in demands}
            if actual_inputs != expected_inputs or actual_demands != expected_demands:
                raise MarkingStateError(f'{path} differs from declared input/resource arcs')
            for token in resource_tokens:
                if token.epoch != claim_epoch or token.consumer not in (None, transition_id):
                    raise MarkingStateError(f'{path} resource identity is incompatible with transition/epoch')
            candidate = self._make_timed_binding(transition_id, claim_epoch, tuple(input_tokens), tuple(sorted(demands, key=lambda item: item.place)))
            if candidate.canonical_key != binding_key or tuple(sorted((item.exact_identity for item in candidate.inputs))) != input_ids:
                raise MarkingStateError(f'{path} exact binding differs from places/arcs/token identities')
            all_tokens = [*input_tokens, *resource_tokens]
            if any((token.token_id in occupied for token in all_tokens)) or any((token.consumed_by not in (None, transition_id) for token in all_tokens)):
                raise MarkingStateError(f'{path} overlaps an active/unavailable token')
            for token in all_tokens:
                occupied.add(token.token_id)
                tokens_to_consume.append((token, transition_id))
            proposed[firing_id] = ActiveFiringClaim(firing_id=firing_id, transition_id=transition_id, binding_key=binding_key, claim_epoch=claim_epoch, inputs=tuple((self._bound_input(token) for token in input_tokens)), resource_tokens=tuple(sorted((self._bound_input(token) for token in resource_tokens), key=lambda item: (item.place, _version_ref_key(item.token_ref)))), resource_demands=tuple(sorted(demands, key=lambda item: item.place)))
        for token, transition_id in tokens_to_consume:
            token.consumed_by = transition_id
        self._timed_active_claims = proposed

def release_timed_claim(self, firing_id: str) -> ActiveFiringClaim:
    """Release one settled firing claim and return its fungible tokens.

        Non-fungible inputs remain consumed by Petri firing semantics. Exact
        fungible capacity tokens become available again only at this settlement
        boundary; queue enumeration itself never reserves them.
        """
    canonical_firing_id = self._canonical_firing_id(firing_id)
    with self._lock:
        claim = self._timed_active_claims.get(canonical_firing_id)
        if claim is None:
            raise MarkingStateError(f'firing_id {canonical_firing_id!r} has no active claim')
        by_id = {token.token_id: token for token in self._tokens}
        resources = [by_id[item.token_id] for item in claim.resource_tokens if item.token_id in by_id]
        if len(resources) != len(claim.resource_tokens) or any((token.consumed_by != claim.transition_id for token in resources)):
            raise MarkingStateError('active firing resource claim no longer matches the marking')
        for token in resources:
            token.consumed_by = None
        del self._timed_active_claims[canonical_firing_id]
        return claim

def release_timed_attempt_resources(self, logical_firing_id: str) -> ActiveFiringClaim:
    """Release only fungible capacity while retaining the logical input claim."""
    canonical_id = self._canonical_firing_id(logical_firing_id)
    with self._lock:
        claim = self._timed_active_claims.get(canonical_id)
        if claim is None:
            raise MarkingStateError(f'firing_id {canonical_id!r} has no active claim')
        by_id = {token.token_id: token for token in self._tokens}
        resources = [by_id.get(item.token_id) for item in claim.resource_tokens]
        if any((token is None or token.consumed_by != claim.transition_id for token in resources)):
            raise MarkingStateError('retry resource release differs from the exact logical claim')
        for token in resources:
            assert token is not None
            token.consumed_by = None
        retained = replace(claim, resource_tokens=())
        self._timed_active_claims[canonical_id] = retained
        return retained

def reclaim_timed_retry_resources(self, logical_firing_id: str, *, transition_id: str, binding_key: str, resource_claims: Mapping[str, Sequence[str]]) -> ActiveFiringClaim:
    """Attach a new attempt's runtime capacity to the original Petri claim."""
    canonical_id = self._canonical_firing_id(logical_firing_id)
    with self._lock:
        claim = self._timed_active_claims.get(canonical_id)
        if claim is None:
            raise MarkingStateError(f'firing_id {canonical_id!r} has no retained logical claim')
        if claim.transition_id != transition_id or claim.binding_key != binding_key or claim.resource_tokens:
            raise MarkingStateError('retry differs from predecessor transition/binding or is already claimed')
        supplied = dict(resource_claims)
        if set(supplied) != {item.place for item in claim.resource_demands}:
            raise MarkingStateError('retry resources must name exactly every original demand')
        selected: list[Token] = []
        for demand in claim.resource_demands:
            candidates = {exact_token_identity(token.token_ref): token for token in self._fresh_on(demand.place, transition_id) if isinstance(token.token_ref, VersionRef)}
            requested = tuple(supplied[demand.place])
            if len(requested) != demand.count or len(set(requested)) != len(requested) or any((identity not in candidates for identity in requested)):
                raise MarkingStateError(f'exact retry resource demand {demand.place!r} is unavailable')
            selected.extend((candidates[identity] for identity in requested))
        if len({token.token_id for token in selected}) != len(selected):
            raise MarkingStateError('retry resource identities overlap')
        for token in selected:
            token.consumed_by = transition_id
        updated = replace(claim, resource_tokens=tuple(sorted((self._bound_input(token) for token in selected), key=lambda item: (item.place, _version_ref_key(item.token_ref), item.token_id))))
        self._timed_active_claims[canonical_id] = updated
        return updated

def _timed_claim_snapshot_for(epoch: int, claims: tuple[ActiveFiringClaim, ...]) -> dict:
    if tuple(sorted(claims, key=lambda item: item.firing_id)) != claims:
        raise MarkingStateError('timed successor claims are not in canonical firing order')
    rows = _canonical_timed_claim_rows(claims)
    return {'schema': 'team_net_marking/runtime_claims/v2', 'epoch': epoch, 'claims': rows}

def _terminal_output_from_validated(spec: Mapping[str, Any]) -> TimedTerminalOutput:
    continuation = spec['continuation']
    return TimedTerminalOutput(place=spec['place'], resource_ref=spec['resource_ref'], work_resource_ref=spec['work_resource_ref'], kind=spec['kind'], verdict=spec['verdict'], continuation_round=None if continuation is None else continuation['round'], continuation_source_ref=None if continuation is None else continuation['source_ref'], output_port_id=spec['output_port_id'], output_place_ref=spec['output_place_ref'], output_binding_ref=spec['output_binding_ref'])

def _deposit_timed_terminal_outputs(self, claim: ActiveFiringClaim, outputs: tuple[TimedTerminalOutput, ...]) -> None:
    """Reuse output semantics without minting a second capacity return."""
    with self._lock:
        temporary_key = -1
        if self._active_claims or temporary_key in self._active_claims:
            raise MarkingStateError('timed terminal conflicts with a process-local firing claim')
        self._active_claims[temporary_key] = _ActiveClaim(claim_id=-1, transition_id=claim.transition_id, epoch=claim.claim_epoch, token_ids=tuple((item.token_id for item in claim.inputs)), token_refs=tuple((item.token_ref for item in claim.inputs)))
        try:
            self.deposit_outputs(claim.transition_id, [item._deposit_spec() for item in outputs], claim_epoch=claim.claim_epoch)
        finally:
            self._active_claims.pop(temporary_key, None)

def prepare_timed_terminal_proposal(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator, base_marking_authority: TypedMarkingAuthority, *, logical_firing_id: str, terminal_status: str, produced: Optional[Sequence[Mapping[str, Any]]]=None, retain_logical_claim_for_retry: bool=False) -> TimedTerminalProposal:
    """Prepare one deterministic terminal successor without mutating ``self``.

        The complete base is rehydrated from Registry authority and the current
        claim closure under the marking lock.  All terminal effects occur only on
        that isolated clone; any validation or deposit failure discards it.
        """
    from cpn.rpnh.registry.resources import TypedMarkingAuthority
    if not isinstance(base_marking_authority, TypedMarkingAuthority):
        raise MarkingStateError('timed terminal proposal requires TypedMarkingAuthority')
    if terminal_status != 'SUCCEEDED':
        raise MarkingStateError('current timed terminal status must be SUCCEEDED')
    if type(retain_logical_claim_for_retry) is not bool:
        raise MarkingStateError('retain_logical_claim_for_retry must be a boolean')
    if retain_logical_claim_for_retry:
        raise MarkingStateError('current timed terminal cannot retain a fault retry claim')
    canonical_id = self._canonical_firing_id(logical_firing_id)
    with self._lock:
        if self._active_claims:
            raise MarkingStateError('timed terminal preparation conflicts with local firing claims')
        base_claim_snapshot = deepcopy(self.timed_claim_snapshot())
        base_local_state = deepcopy(self._local_state_locked())
        successor = type(self).from_authority(self._net, base_marking_authority)
        successor.restore_timed_claim_snapshot(base_claim_snapshot)
        if successor.local_state() != base_local_state:
            raise MarkingStateError('base TypedMarkingAuthority/claims differ from local state')
        claim = successor.active_timed_claim(canonical_id)
    raw_outputs = [] if produced is None else [deepcopy(dict(item)) for item in produced]
    validated_outputs = successor._validated_output_specs(raw_outputs)
    outputs = tuple(sorted((self._terminal_output_from_validated(item) for item in validated_outputs), key=lambda item: json.dumps(_canonical_marking_material(item), sort_keys=True, separators=(',', ':'), ensure_ascii=True)))
    output_resource_refs = tuple(sorted((item.resource_ref for item in outputs if item.resource_ref is not None), key=lambda ref: (str(ref.resource_id), str(ref.resource_version_id))))
    if len(set(output_resource_refs)) != len(output_resource_refs):
        raise MarkingStateError('timed terminal outputs repeat an exact resource version')
    successor._deposit_timed_terminal_outputs(claim, outputs)
    successor.release_timed_claim(canonical_id)
    released_resource_refs = tuple(sorted((item.token_ref for item in claim.resource_tokens), key=_version_ref_key))
    successor_snapshot = successor._typed_snapshot_with_active_timed_claims(executable, registry)
    successor._adopt_snapshot_token_refs(successor_snapshot)
    successor_claim_snapshot = successor.timed_claim_snapshot()
    successor_claims = successor.active_timed_claims()
    successor_local_state = successor.local_state()
    successor_token_refs = tuple(sorted((state.token_ref for state in successor_snapshot.tokens if isinstance(state.token_ref, VersionRef)), key=_version_ref_key))
    base_token_refs = set(base_marking_authority.token_refs)
    deposited_token_refs = tuple(sorted((state.token_ref for state in successor_snapshot.tokens if isinstance(state.token_ref, VersionRef) and state.token_ref not in base_token_refs), key=_version_ref_key))
    delta = TimedMarkingDeltaMaterial(base_checkpoint_ref=base_marking_authority.checkpoint_ref, logical_firing_id=canonical_id, transition_id=claim.transition_id, consumed_input_token_refs=() if retain_logical_claim_for_retry else tuple(sorted((item.token_ref for item in claim.inputs), key=_version_ref_key)), retained_input_token_refs=tuple(sorted((item.token_ref for item in claim.inputs), key=_version_ref_key)) if retain_logical_claim_for_retry else (), released_resource_token_refs=released_resource_refs, deposited_token_refs=deposited_token_refs, successor_token_refs=successor_token_refs, retain_logical_claim_for_retry=retain_logical_claim_for_retry)
    proposal = TimedTerminalProposal(schema='team_net_marking/timed_terminal_proposal/v1', base_marking_authority=base_marking_authority, base_checkpoint_ref=base_marking_authority.checkpoint_ref, base_local_state=base_local_state, base_claims=self.active_timed_claims(), logical_firing_id=canonical_id, transition_id=claim.transition_id, binding_key=claim.binding_key, active_claim=claim, terminal_status=terminal_status, terminal_outputs=outputs, terminal_output_resource_refs=output_resource_refs, retain_logical_claim_for_retry=retain_logical_claim_for_retry, released_resource_token_refs=released_resource_refs, successor_snapshot=successor_snapshot, marking_delta_material=delta, successor_claims=successor_claims, successor_local_state=successor_local_state)
    return proposal

def install_timed_terminal_successor(self, proposal: TimedTerminalProposal, receipt: object) -> TypedMarkingAuthority:
    """Atomically install one exact Registry-committed timed successor."""
    from cpn.rpnh.registry.models import RuntimeTimedCPNProjection, RuntimeTimedMarkingCheckpointAuthority, RuntimeTimedMarkingClaim, TimedSuccessorSettlementReceipt
    from cpn.rpnh.registry.resources import TypedMarkingAuthority
    if not isinstance(proposal, TimedTerminalProposal):
        raise MarkingStateError('timed successor install requires TimedTerminalProposal')
    if proposal.schema != 'team_net_marking/timed_terminal_proposal/v1':
        raise MarkingStateError('timed terminal proposal schema differs from the current closure')
    if not isinstance(receipt, TimedSuccessorSettlementReceipt):
        raise MarkingStateError('timed successor install requires exact Registry receipt')
    authority = receipt.marking_authority
    projection = receipt.runtime_projection
    if receipt.base_checkpoint_ref != proposal.base_checkpoint_ref or receipt.logical_firing_id != proposal.logical_firing_id or (not isinstance(authority, TypedMarkingAuthority)) or (not isinstance(projection, RuntimeTimedCPNProjection)) or (not isinstance(projection.marking_authority, RuntimeTimedMarkingCheckpointAuthority)):
        raise MarkingStateError('Registry receipt does not authorize this exact proposal')
    if receipt.checkpoint_ref != authority.checkpoint_ref or receipt.checkpoint_ref != projection.marking_authority.marking_checkpoint_ref or receipt.marking_delta_ref != authority.settlement_delta_ref or (receipt.marking_delta_ref.entity_type != 'marking_delta/v1') or (authority.previous_checkpoint_ref != proposal.base_checkpoint_ref) or (authority.net_ref != proposal.base_marking_authority.net_ref) or (projection.net_instance_ref != authority.net_ref):
        raise MarkingStateError('Registry receipt/checkpoint/delta does not extend the proposal base')
    snapshot = proposal.successor_snapshot
    try:
        snapshot_states = snapshot.tokens
        snapshot_attempts = snapshot.attempts
        snapshot_epoch = snapshot.epoch
        snapshot_next = snapshot.next_token_id
    except AttributeError as exc:
        raise MarkingStateError('timed proposal successor snapshot is not typed') from exc
    if authority.epoch != snapshot_epoch or authority.next_token_id != snapshot_next or authority.attempts != snapshot_attempts or (tuple((item.state for item in authority.tokens)) != snapshot_states) or (authority.token_refs != tuple(sorted((item.token_ref for item in authority.tokens), key=_version_ref_key))):
        raise MarkingStateError('Registry marking authority differs from proposed successor snapshot')
    expected_runtime_claims = tuple((RuntimeTimedMarkingClaim(logical_firing_id=claim.firing_id, transition_id=claim.transition_id, binding_key=claim.binding_key, claim_epoch=claim.claim_epoch, input_ids=tuple(sorted((item.exact_identity for item in claim.inputs))), resource_claims=tuple(((demand.place, demand.count, tuple(sorted((item.exact_identity for item in claim.resource_tokens if item.place == demand.place)))) for demand in sorted(claim.resource_demands, key=lambda item: item.place)))) for claim in proposal.successor_claims))
    runtime_marking = projection.marking_authority
    if runtime_marking.epoch != snapshot_epoch or runtime_marking.claims != expected_runtime_claims or runtime_marking.claims != proposal.successor_claims:
        raise MarkingStateError('Registry runtime authority differs from successor claims')
    installed = type(self).from_authority(self._net, authority)
    claim_snapshot = self._timed_claim_snapshot_for(installed.epoch, proposal.successor_claims)
    installed.restore_timed_claim_snapshot(claim_snapshot)
    if installed.active_timed_claims() != proposal.successor_claims or installed.local_state() != proposal.successor_local_state:
        raise MarkingStateError('Registry successor cannot reconstruct proposed local state')
    new_tokens = deepcopy(installed._tokens)
    new_attempts = dict(installed._attempts)
    new_claims = dict(installed._timed_active_claims)
    with self._lock:
        current_state = self._local_state_locked()
        if current_state == proposal.successor_local_state:
            return authority
        if current_state != proposal.base_local_state or self._active_claims:
            raise MarkingStateError('timed successor install found stale/conflicting local state')
        self._tokens = new_tokens
        self._epoch = installed._epoch
        self._next_id = installed._next_id
        self._attempts = new_attempts
        self._active_claims = {}
        self._next_claim_id = 0
        self._timed_active_claims = new_claims
        self._guard_suppressed_last = installed._guard_suppressed_last
    return authority
