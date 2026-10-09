"""Exact firing claim reservation, re-keying, and settlement views."""
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

def _require_exact_registered_claim_refs(transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> tuple[VersionRef, ...]:
    if not isinstance(transition_id, str) or not isinstance(claimed_token_refs, tuple) or any((not isinstance(ref, VersionRef) or ref.entity_type != 'petri_token/v1' for ref in claimed_token_refs)) or (len(set(claimed_token_refs)) != len(claimed_token_refs)):
        raise MarkingStateError('exact registered firing claim requires one declared transition and unique petri_token/v1 refs')
    return tuple(sorted(claimed_token_refs, key=_version_ref_key))

def _install_exact_registered_claim_locked(self, transition_id: str, expected_refs: tuple[VersionRef, ...], *, verify_enabled: bool, allow_siblings: bool=False) -> int:
    """Install a Registry-selected occurrence without replaying settlement.

        ``verify_enabled=True`` is the scheduler's initial exact-claim path.
        ``verify_enabled=False`` is only for a pure successor projection whose
        Registry firing and predecessor checkpoint were already admitted.  That
        path reconstructs the local consume/read split needed by
        ``deposit_outputs``; it does not run the enabledness or reservation
        algorithm a second time.
        """
    with self._lock:
        if self._timed_active_claims or (self._active_claims and (not allow_siblings)):
            raise MarkingStateError('exact registered firing projection requires no sibling claim')
        claim_epoch = self._epoch
        by_ref = {token.token_ref: token for token in self._tokens if isinstance(token.token_ref, VersionRef)}
        if len(by_ref) != sum((isinstance(token.token_ref, VersionRef) for token in self._tokens)):
            raise MarkingStateError('exact registered firing projector repeats a token ref')
        selected = tuple((by_ref.get(ref) for ref in expected_refs))
        if any((token is None for token in selected)):
            raise MarkingStateError('exact registered firing ref is absent from the projector')
        selected = tuple((token for token in selected if token is not None))
        selected_ids = {token.token_id for token in selected}
        if verify_enabled:
            # Exact authorization checks the supplied occurrence, not the
            # scheduler's unrelated first/default carrier. Guards are shared;
            # ordinary scheduling still uses its unchanged unrestricted path.
            if not self._is_enabled_locked(transition_id, allowed_token_ids=selected_ids):
                raise MarkingStateError('exact registered firing is not currently enabled')
            matched = self._try_reserve(transition_id, set(), allowed_token_ids=selected_ids)
            matched_ids = set((*matched.token_ids, *matched.reference_token_ids)) if matched is not None else set()
            if matched is None or matched_ids != selected_ids or len(matched_ids) != len(expected_refs):
                raise MarkingStateError('exact registered firing refs are not one enabled occurrence')
            consumed_ids = tuple(matched.token_ids)
            reference_ids = tuple(matched.reference_token_ids)
            lease_accesses = matched.lease_accesses
        else:
            variable = self._net.variable_resource_arc_for(transition_id)
            reference_ids: set[int] = set()
            variable_ids: set[int] = set()
            lease_accesses: tuple[tuple[VersionRef, str], ...] = ()
            if variable is not None:
                claim_tokens = [token for token in selected if token.place == variable.claim_token_place]
                if len(claim_tokens) != 1:
                    raise MarkingStateError('registered firing claim lacks its variable lease token')
                lease_accesses = tuple(((claim['lease_identity_ref'], claim['access_mode']) for claim in claim_tokens[0].lease_claims))
                pool_tokens = [token for token in selected if token.place == variable.lease_pool_place]
                for claim in claim_tokens[0].lease_claims:
                    matches = [token for token in pool_tokens if token.lease_identity_ref == claim['lease_identity_ref'] and (claim['expected_resource_ref'] is None or token.resource_ref == claim['expected_resource_ref']) and (claim['access_mode'] == 'produce' or token.resource_ref is not None)]
                    if len(matches) != 1:
                        raise MarkingStateError('registered firing claim has a mismatched lease colour')
                    variable_ids.add(matches[0].token_id)
                    if claim['access_mode'] == 'read':
                        reference_ids.add(matches[0].token_id)
            # Static lease reads use the existing reference union. In a shared
            # pool a variable read may satisfy the same static arc occurrence.
            static_places: set[str] = set()
            variable_references = set(reference_ids)
            for place, target, weight in getattr(self._net, 'lease_reference_arcs', ()):
                if target != transition_id:
                    continue
                static_places.add(place)
                candidates = sorted((token for token in selected if token.place == place
                    and token.token_id not in variable_ids - variable_references),
                    key=lambda token: (token.token_id not in variable_references, token.token_id))
                if len(candidates) < weight:
                    raise MarkingStateError('registered firing claim lacks its exact static lease read')
                static_only = {token.token_id for token in candidates if token.token_id not in variable_ids}
                if len(static_only) > weight:
                    raise MarkingStateError('registered firing claim exceeds its static lease read weight')
                # Every additional selected ref must be accounted for by this
                # arc, but overlap with variable reads need not be maximal.
                reference_ids.update(static_only)
            if variable is not None and any(token.token_id not in variable_ids | reference_ids for token in pool_tokens):
                raise MarkingStateError('registered firing claim lacks its exact lease colours')
            lease_accesses = tuple(sorted(set(lease_accesses) | {
                (token.lease_identity_ref, 'read') for token in selected
                if token.token_id in reference_ids and token.place in static_places
                and token.lease_identity_ref is not None},
                key=lambda item: (_version_ref_key(item[0]), item[1])))
            consumed_ids = tuple((token.token_id for token in selected if token.token_id not in reference_ids))
            expected_places = sorted((place for place, _target, weight in self._claim_input_arcs(transition_id) for _ in range(weight)))
            ordinary_places = sorted((token.place for token in selected if token.token_id not in reference_ids and (variable is None or token.place != variable.lease_pool_place)))
            if ordinary_places != expected_places:
                raise MarkingStateError('registered firing claim differs from declared input arcs')
            reference_ids = tuple(sorted(reference_ids))
            for token in selected:
                if token.epoch != self._epoch or token.consumed_by is not None or token.consumer not in {None, transition_id}:
                    raise MarkingStateError('registered firing claim contains a stale token')
            self._validate_runtime_token_refs(list(selected), 'registered firing claim')
        if allow_siblings:
            prior_consumed = {token_id for claim in self._active_claims.values() for token_id in claim.token_ids}
            prior_references = {token_id for claim in self._active_claims.values() for token_id in claim.reference_token_ids}
            prior_accesses = tuple((access for claim in self._active_claims.values() for access in claim.lease_accesses))
            if set(consumed_ids) & (prior_consumed | prior_references) or set(reference_ids) & prior_consumed or (not self._lease_accesses_compatible(lease_accesses, prior_accesses)):
                raise MarkingStateError('registered active firing conflicts with a sibling claim')
        by_id = {token.token_id: token for token in self._tokens}
        for token_id in consumed_ids:
            by_id[token_id].consumed_by = transition_id
        consumed_refs = tuple(sorted((by_id[token_id].token_ref for token_id in consumed_ids), key=_version_ref_key))
        reference_refs = tuple(sorted((by_id[token_id].token_ref for token_id in reference_ids), key=_version_ref_key))
        claim_id = self._next_claim_id
        self._next_claim_id += 1
        self._active_claims[claim_id] = _ActiveClaim(claim_id=claim_id, transition_id=transition_id, epoch=claim_epoch, token_ids=consumed_ids, token_refs=consumed_refs, reference_token_ids=reference_ids, reference_token_refs=reference_refs, lease_accesses=lease_accesses)
        return claim_epoch

def claim_exact_registered_firing(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
    """Install one exact Registry-authorized enabled occurrence."""
    if not isinstance(transition_id, str) or transition_id not in self._net.transitions:
        raise MarkingStateError('exact registered firing claim requires one declared transition and unique petri_token/v1 refs')
    expected_refs = self._require_exact_registered_claim_refs(transition_id, claimed_token_refs)
    return self._install_exact_registered_claim_locked(transition_id, expected_refs=expected_refs, verify_enabled=True)

def install_registered_firing_claim(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
    """Install an already Registry-admitted occurrence for pure projection."""
    if not isinstance(transition_id, str) or transition_id not in self._net.transitions:
        raise MarkingStateError('registered firing projection requires one declared transition')
    expected_refs = self._require_exact_registered_claim_refs(transition_id, claimed_token_refs)
    return self._install_exact_registered_claim_locked(transition_id, expected_refs=expected_refs, verify_enabled=False)

def install_registered_active_firing_claim(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
    """Restore one member of a conflict-free Registry active set."""
    if not isinstance(transition_id, str) or transition_id not in self._net.transitions:
        raise MarkingStateError('active firing projection requires one declared transition')
    expected_refs = self._require_exact_registered_claim_refs(transition_id, claimed_token_refs)
    return self._install_exact_registered_claim_locked(transition_id, expected_refs=expected_refs, verify_enabled=False, allow_siblings=True)

def claim_firing_set(self, max_count: Optional[int]=None, allowed: Optional['set[str]']=None, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> tuple[list[str], int]:
    """Select a CONFLICT-FREE firing set AND consume its input tokens — ATOMIC.

        The exact enabled binding occurrences are enumerated first, then their
        queue is randomized before greedy allocation.  Declaration position and
        transition id therefore have no rivalry priority.  Distinct bindings of
        one transition may be selected together when their exact token occurrences
        do not overlap. Consuming inputs are reserved before a body launches;
        exact read references remain in the shared marking.

        ``max_count`` caps the set size (the executor passes the remaining F1
        budget, so a pass never commits more firings than the budget allows).

        ``allowed`` (§40 R24/ES-C — the ceiling-freeze SCHEDULING restriction) is an
        optional transition-id SET the selection is restricted to: when supplied, ONLY
        enabled transitions whose id is in ``allowed`` are considered (the rest are not
        stepped THIS pass). This is off-net SCHEDULING (WHICH enabled transition steps),
        NOT routing — it moves no token and changes no arc (ARCH v3.14 boundary). The
        executor passes the failure-termination machinery here at the ceiling so
        finalization drains over its DECLARED net route while no new work is admitted.
        ``None`` (the default) → every enabled transition is eligible (byte-identical).

        Returns ``(firing_set, claim_epoch)``. ``claim_epoch`` is the epoch at
        claim time; the executor passes it to :meth:`deposit_outputs` so a re-mark
        that bumped the epoch mid-firing drops the now-stale in-flight outputs
        (§5.1bis step 3).
        """
    with self._lock:
        claim_epoch = self._epoch
        timed_wait_guard_state_map = self._timed_wait_guard_state_map(timed_wait_guard_states)
        transition_ids = (*self._net.transitions, *sorted(self._net.registered_fault_transition_routes))
        enabled = [tid for tid in transition_ids if self._is_enabled_locked(tid, timed_wait_guard_states, _timed_wait_guard_state_map=timed_wait_guard_state_map)]
        if allowed is not None:
            enabled = [tid for tid in enabled if tid in allowed]
        selected_routes = [tid for tid in enabled if tid in self._net.transitions and any((emit == 'route_selected' for _place, emit in self._net.output_emit_of(tid)))]
        if selected_routes:
            enabled = selected_routes
            max_count = 1
        candidates: list[tuple[str, _PendingClaim]] = []
        for tid in enabled:
            local_reserved: set[int] = set()
            while True:
                candidate = self._try_reserve(tid, local_reserved)
                if candidate is None:
                    break
                candidates.append((tid, candidate))
                if not candidate.token_ids:
                    break
                local_reserved.update(candidate.token_ids)
        random.SystemRandom().shuffle(candidates)
        reserved: set[int] = set()
        referenced: set[int] = set()
        selected_lease_accesses: list[tuple[VersionRef, str]] = []
        firing_set: list[str] = []
        claims: list[tuple[int, str, _PendingClaim]] = []
        for tid, claim in candidates:
            if max_count is not None and len(firing_set) >= max_count:
                break
            if ((reserved | referenced).intersection(claim.token_ids)
                    or reserved.intersection(claim.reference_token_ids)):
                continue
            if not self._lease_accesses_compatible(claim.lease_accesses, selected_lease_accesses):
                continue
            firing_set.append(tid)
            claim_id = self._next_claim_id + len(claims)
            claims.append((claim_id, tid, claim))
            reserved.update(claim.token_ids)
            referenced.update(claim.reference_token_ids)
            selected_lease_accesses.extend(claim.lease_accesses)
        by_id = {tok.token_id: tok for tok in self._tokens}
        claimed_token_refs: dict[int, tuple[VersionRef, ...]] = {}
        operation_token_refs: dict[int, tuple[VersionRef, ...]] = {}
        for claim_id, tid, claim in claims:
            refs = tuple(sorted((by_id[token_id].token_ref for token_id in claim.token_ids), key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id)) if isinstance(ref, VersionRef) else ('', '', '')))
            if any((not isinstance(ref, VersionRef) for ref in refs)):
                raise MarkingStateError(f'active claim {tid!r} contains an unregistered Petri token')
            typed_refs = tuple((ref for ref in refs if isinstance(ref, VersionRef)))
            if len(set(typed_refs)) != len(typed_refs):
                raise MarkingStateError(f'active claim {tid!r} repeats a Petri token ref')
            claimed_token_refs[claim_id] = typed_refs
            operation_refs = tuple(sorted((by_id[token_id].token_ref for token_id in (*claim.token_ids, *claim.reference_token_ids)), key=_version_ref_key))
            if any((not isinstance(ref, VersionRef) for ref in operation_refs)) or len(set(operation_refs)) != len(operation_refs):
                raise MarkingStateError(f'active operation claim {tid!r} repeats or lacks a Petri token ref')
            operation_token_refs[claim_id] = operation_refs
        from ..checks import analyze_checkpoint_firing_step
        analyze_checkpoint_firing_step(self._net, claim_epoch=claim_epoch, tokens=tuple(self._tokens), selected_firing_ids=tuple(firing_set), selected_occurrence_keys=tuple((claim_id for claim_id, _tid, _claim in claims)), claimed_token_refs=claimed_token_refs, expected_consumed_place_counts={claim_id: claim.consumed_place_counts for claim_id, _tid, claim in claims}, enabledness=lambda transition_id: self._is_enabled_locked(transition_id, timed_wait_guard_states, _timed_wait_guard_state_map=timed_wait_guard_state_map))
        active_claims: dict[int, _ActiveClaim] = {}
        for claim_id, tid, claim in claims:
            active_claims[claim_id] = _ActiveClaim(claim_id=claim_id, transition_id=tid, epoch=claim_epoch, token_ids=tuple(sorted(claim.token_ids)), token_refs=claimed_token_refs[claim_id], reference_token_ids=tuple(sorted(claim.reference_token_ids)), reference_token_refs=tuple(sorted((by_id[token_id].token_ref for token_id in claim.reference_token_ids), key=_version_ref_key)), lease_accesses=claim.lease_accesses)
        for _claim_id, tid, claim in claims:
            for tok_id in claim.token_ids:
                by_id[tok_id].consumed_by = tid
        self._active_claims.update(active_claims)
        self._next_claim_id += len(active_claims)
        eligible_queue = tuple(((tid, tuple(sorted((by_id[token_id].token_ref for token_id in (*claim.token_ids, *claim.reference_token_ids)), key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))) for tid, claim in candidates))
        allocations = tuple((FiringClaimOccurrence(local_key=claim_id, transition_id=tid, claim_epoch=claim_epoch, token_refs=operation_token_refs[claim_id]) for claim_id, tid, _claim in claims))
        self._last_firing_allocation = FiringAllocationEvidence(claim_epoch=claim_epoch, eligible_queue=eligible_queue, allocations=allocations, scheduler_seed=None)
        self._published_firing_allocation = None
        return (firing_set, claim_epoch)

def firing_allocation_evidence(self) -> FiringAllocationEvidence:
    """Return the last actual queue/allocation; seed is provenance only."""
    with self._lock:
        return self._last_firing_allocation

def bind_firing_allocation_authority(self, evidence: FiringAllocationEvidence, authority: object) -> None:
    """Bind P0's typed publication receipt to the exact local allocation."""
    with self._lock:
        if not isinstance(evidence, FiringAllocationEvidence) or evidence != self._last_firing_allocation or authority is None:
            raise MarkingStateError('allocation authority differs from the current exact queue')
        self._published_firing_allocation = (evidence, authority)

def require_published_firing_occurrence(self, occurrence_key: int | VersionRef, transition_id: str, claim_epoch: int) -> object:
    """Fail closed unless P0 published this occurrence before admission."""
    with self._lock:
        published = self._published_firing_allocation
        if published is None:
            raise MarkingStateError('firing occurrence has no Registry allocation publication')
        evidence, authority = published
        matches = tuple((item for item in evidence.allocations if item.local_key == occurrence_key and item.transition_id == transition_id and (item.claim_epoch == claim_epoch)))
        if len(matches) != 1:
            raise MarkingStateError('firing occurrence differs from published allocation')
        return authority

def active_claim_occurrences(self, claim_epoch: int) -> tuple[FiringClaimOccurrence, ...]:
    """Return exact active occurrences in allocation/insertion order."""
    with self._lock:
        return tuple((FiringClaimOccurrence(local_key=key, transition_id=claim.transition_id, claim_epoch=claim.epoch, token_refs=tuple(sorted((*claim.token_refs, *claim.reference_token_refs), key=_version_ref_key))) for key, claim in self._active_claims.items() if claim.epoch == claim_epoch))

def active_claim_key(self, t_id: str, claim_epoch: int) -> int | VersionRef:
    """Return the unique instance key for one process-local active claim."""
    with self._lock:
        matches = tuple((key for key, claim in self._active_claims.items() if claim.transition_id == t_id and claim.epoch == claim_epoch))
        if len(matches) != 1:
            raise MarkingStateError(f'transition {t_id!r} has no unique active instance at epoch {claim_epoch}')
        return matches[0]

def bind_active_claim(self, local_key: int | VersionRef, firing_ref: VersionRef) -> VersionRef:
    """Replace a temporary claim key with its exact Registry firing ref."""
    if not isinstance(firing_ref, VersionRef) or firing_ref.entity_type not in {'transition_firing/v1', 'transition_firing/v2'}:
        raise MarkingStateError('active claim binding requires an exact Registry firing ref')
    with self._lock:
        claim = self._active_claims.get(local_key)
        if claim is None:
            if firing_ref in self._active_claims:
                return firing_ref
            raise MarkingStateError('active claim local key is stale')
        if firing_ref in self._active_claims and firing_ref != local_key:
            raise MarkingStateError('Registry firing ref already owns another claim')
        del self._active_claims[local_key]
        self._active_claims[firing_ref] = claim
        return firing_ref

def _active_claim(self, t_id: str, claim_epoch: int, instance_key: int | VersionRef | None=None) -> tuple[int | VersionRef, _ActiveClaim]:
    if instance_key is not None:
        claim = self._active_claims.get(instance_key)
        if claim is None or claim.transition_id != t_id or claim.epoch != claim_epoch:
            raise MarkingStateError(f'firing instance {instance_key!r} has no matching active claim')
        return (instance_key, claim)
    key = self.active_claim_key(t_id, claim_epoch)
    return (key, self._active_claims[key])

def derive_firing_resource_access(self, t_id: str, claim_epoch: int, firing_ref: VersionRef, requested: ResourceVersionRef, access_mode: str) -> PetriFiringResourceAccess:
    """Purely derive one occurrence-local access arc from the marking.

        The caller supplies no place, token, lease identity, claim, or arc
        inscription.  Those are selected mechanically from the transition's
        declared variable-resource pool and the one exact live resource token.
        """
    if not isinstance(firing_ref, VersionRef) or firing_ref.entity_type not in {'transition_firing/v1', 'transition_firing/v2'} or (not isinstance(requested, ResourceVersionRef)) or (access_mode not in {'read', 'edit'}):
        raise MarkingStateError('resource access derivation requires one firing, resource, and mode')
    with self._lock:
        key, active = self._active_claim(t_id, claim_epoch, firing_ref)
        if key != firing_ref:
            raise MarkingStateError('resource access requires the Registry-bound firing occurrence')
        variable = self._net.variable_resource_arc_for(t_id)
        if variable is None:
            raise MarkingStateError('transition declares no variable resource-access arc')
        matching = tuple((token for token in self._tokens if token.place == variable.lease_pool_place and token.epoch == active.epoch and (token.resource_ref == requested) and (token.lease_identity_ref is not None)))
        if len(matching) != 1:
            raise MarkingStateError('requested resource has no unique live lease-pool token')
        token = matching[0]
        if not isinstance(token.token_ref, VersionRef):
            raise MarkingStateError('requested resource token lacks its exact Registry identity')
        own_modes = tuple((mode for identity, mode in active.lease_accesses if identity == token.lease_identity_ref))
        if len(own_modes) > 1:
            raise MarkingStateError('active firing repeats one resource-access colour')
        upgrading = own_modes == ('read',) and access_mode == 'edit'
        if own_modes and (not upgrading):
            raise MarkingStateError('active firing already holds the requested resource access')
        if token.token_id in active.token_ids or token.token_ref in active.token_refs:
            raise MarkingStateError('requested resource token is already consumed by this firing')
        if upgrading:
            if token.token_id not in active.reference_token_ids or token.token_ref not in active.reference_token_refs or token.consumed_by is not None:
                raise MarkingStateError('resource edit upgrade lacks its exact active read arc')
        elif token.token_id in active.reference_token_ids or token.token_ref in active.reference_token_refs:
            raise MarkingStateError('requested resource token is already referenced by this firing')
        elif token.consumed_by is not None:
            raise MarkingStateError('requested resource token is already consumed')
        other_accesses = tuple((item for other_key, claim in self._active_claims.items() if other_key != firing_ref for item in claim.lease_accesses))
        if not self._lease_accesses_compatible(((token.lease_identity_ref, access_mode),), other_accesses):
            raise MarkingStateError('requested resource access conflicts with an active firing')
        return PetriFiringResourceAccess(firing_ref=firing_ref, transition_id=t_id, claim_epoch=claim_epoch, lease_pool_place=variable.lease_pool_place, resource_token_ref=token.token_ref, lease_identity_ref=token.lease_identity_ref, resource_ref=requested, access_mode=access_mode, input_arc_mode='read' if access_mode == 'read' else 'borrow', output_arc_mode=None if access_mode == 'read' else 'return')

def apply_firing_resource_access(self, access: PetriFiringResourceAccess) -> None:
    """Apply exactly the still-current formal access arc to its firing."""
    if not isinstance(access, PetriFiringResourceAccess):
        raise MarkingStateError('resource access application requires a typed formal arc')
    with self._lock:
        expected = self.derive_firing_resource_access(access.transition_id, access.claim_epoch, access.firing_ref, access.resource_ref, access.access_mode)
        if expected != access:
            raise MarkingStateError('resource access arc differs from the current formal derivation')
        key, active = self._active_claim(access.transition_id, access.claim_epoch, access.firing_ref)
        by_ref = {token.token_ref: token for token in self._tokens if isinstance(token.token_ref, VersionRef)}
        token = by_ref.get(access.resource_token_ref)
        if token is None:
            raise MarkingStateError('resource access token disappeared before application')
        reference_ids = list(active.reference_token_ids)
        reference_refs = list(active.reference_token_refs)
        token_ids = list(active.token_ids)
        token_refs = list(active.token_refs)
        lease_accesses = [item for item in active.lease_accesses if item[0] != access.lease_identity_ref]
        if access.access_mode == 'read':
            reference_ids.append(token.token_id)
            reference_refs.append(access.resource_token_ref)
        else:
            if token.token_id in reference_ids:
                reference_ids.remove(token.token_id)
            if access.resource_token_ref in reference_refs:
                reference_refs.remove(access.resource_token_ref)
            token.consumed_by = access.transition_id
            token_ids.append(token.token_id)
            token_refs.append(access.resource_token_ref)
        lease_accesses.append((access.lease_identity_ref, access.access_mode))
        self._active_claims[key] = replace(active, token_ids=tuple(token_ids), token_refs=tuple(sorted(token_refs, key=_version_ref_key)), reference_token_ids=tuple(reference_ids), reference_token_refs=tuple(sorted(reference_refs, key=_version_ref_key)), lease_accesses=tuple(sorted(lease_accesses, key=lambda item: (_version_ref_key(item[0]), item[1]))))

def _try_reserve(self, t_id: str, reserved: set[int], *, allowed_token_ids: Optional[set[int]]=None) -> Optional[_PendingClaim]:
    """Tentatively reserve ``t``'s required input tokens (caller holds lock).

        Returns the token ids to consume plus the exact marking-owned place-count
        projection (one batch per exact input-arc weight)
        if ALL token input places can be satisfied from tokens not already in ``reserved``;
        else ``None`` (this transition loses the conflict this pass).  A registered
        exact net reserves its declared file tokens plus produced/capacity tokens and
        never reserves a synthetic ignition token.  An unregistered net retains the
        historical produced-token + private ignition behavior.

        §40.12 R26c — a VERDICT-GUARDED input place reserves ONLY a token whose reserved COLOR
        MATCHES the color the enabling rule (``verdict_guard_enabled``) checked for that place
        (the SAME ``_verdict_color_matches`` mirror). The net DECLARES via the verdict guard that
        only a matching-color token enables the transition; without this filter a place carrying
        within-epoch litter (e.g. a ``continue`` token AND a ``pass`` token) could have a
        ``pass``-guarded transition consume the WRONG-color token and fire spuriously. An
        UNGUARDED input place is unchanged (reserve as today) — a net that declares no verdict
        guard is byte-identical (``verdict_guards_of`` empty → the map is empty).
        """
    registered_fault = self._net.registered_fault_transition(t_id)
    if registered_fault is not None:
        raise MarkingStateError('retired registered fault transition cannot reserve tokens')
    guard_colors = dict(self._net.verdict_guards_of(t_id))
    active_references = {token_id for active in self._active_claims.values()
                         for token_id in active.reference_token_ids}
    picked: list[int] = []
    consumed_place_counts: dict[str, int] = {}
    for p, _t, w in self._claim_input_arcs(t_id):
        consumed_place_counts[p] = consumed_place_counts.get(p, 0) + w
        avail = [tok for tok in self._fresh_on(p, t_id) if tok.token_id not in reserved and tok.token_id not in active_references and tok.token_id not in picked and (allowed_token_ids is None or tok.token_id in allowed_token_ids)]
        if p in guard_colors:
            avail = [tok for tok in avail if _verdict_color_matches(tok.verdict, guard_colors[p])]
        if len(avail) < w:
            return None
        picked.extend((tok.token_id for tok in avail[:w]))
    variable_selection = self._variable_lease_token_ids(t_id, picked, reserved, allowed_token_ids=allowed_token_ids)
    if variable_selection is None:
        return None
    variable_ids, reference_ids = variable_selection
    variable_binding = self._net.variable_resource_arc_for(t_id)
    lease_accesses: tuple[tuple[VersionRef, str], ...] = ()
    if variable_binding is not None:
        by_id = {token.token_id: token for token in self._tokens}
        claim_tokens = [by_id[token_id] for token_id in picked if token_id in by_id and by_id[token_id].place == variable_binding.claim_token_place]
        lease_accesses = tuple(((claim['lease_identity_ref'], claim['access_mode']) for claim in claim_tokens[0].lease_claims)) if len(claim_tokens) == 1 else ()
        active_accesses = tuple((access for active in self._active_claims.values() for access in active.lease_accesses))
        if len(claim_tokens) != 1 or len(variable_ids) != sum((claim['access_mode'] != 'read' for claim in claim_tokens[0].lease_claims)) or len(reference_ids) != sum((claim['access_mode'] == 'read' for claim in claim_tokens[0].lease_claims)) or (not self._lease_accesses_compatible(lease_accesses, active_accesses)):
            return None
        if variable_ids:
            consumed_place_counts[variable_binding.lease_pool_place] = consumed_place_counts.get(variable_binding.lease_pool_place, 0) + len(variable_ids)
    picked.extend(variable_ids)
    if active_references.intersection(variable_ids):
        return None
    for place, target, weight in getattr(self._net, 'lease_reference_arcs', ()):
        if target != t_id:
            continue
        available = sorted((token for token in self._fresh_on(place, t_id)
            if token.token_id not in reserved and token.token_id not in picked
            and (allowed_token_ids is None or token.token_id in allowed_token_ids)
            and (place not in guard_colors or _verdict_color_matches(token.verdict, guard_colors[place]))),
            key=lambda token: ((token.token_id in reference_ids) if allowed_token_ids is not None
                               else (token.token_id not in reference_ids), token.token_id))
        if len(available) < weight:
            return None
        for token in available[:weight]:
            if token.token_id not in reference_ids:
                reference_ids.append(token.token_id)
            if token.lease_identity_ref is not None:
                lease_accesses = tuple(set(lease_accesses) | {(token.lease_identity_ref, 'read')})
    active_accesses = tuple(access for active in self._active_claims.values() for access in active.lease_accesses)
    if not self._lease_accesses_compatible(lease_accesses, active_accesses):
        return None
    lease_accesses = tuple(sorted(lease_accesses, key=lambda item: (_version_ref_key(item[0]), item[1])))
    if picked or reference_ids:
        return _PendingClaim(token_ids=tuple(picked), consumed_place_counts=consumed_place_counts, reference_token_ids=tuple(reference_ids), lease_accesses=lease_accesses)
    if self._net.is_guard_only_transition(t_id):
        return _PendingClaim(token_ids=(), consumed_place_counts={})
    return None

def claimed_tokens(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> list[Token]:
    """The tokens ``t`` consumed at ``claim_epoch`` (for STAGE / observability).

        Lets the executor stage the consumed artifacts into ``t``'s sandbox read
        surface (the routed slice the body reads). Pure read.
        """
    with self._lock:
        _key, active = self._active_claim(t_id, claim_epoch, instance_key)
        by_id = {token.token_id: token for token in self._tokens}
        claimed = [by_id[token_id] for token_id in active.token_ids if token_id in by_id]
        if len(claimed) != len(active.token_ids) or any((token.consumed_by != t_id or token.epoch != claim_epoch for token in claimed)):
            raise MarkingStateError(f'transition {t_id!r} active claim no longer matches its tokens')
        self._validate_runtime_token_refs(claimed, f'claimed_tokens[{t_id!r}]', require_token_ref=True)
        if tuple(sorted((token.token_ref for token in claimed), key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id)) if isinstance(ref, VersionRef) else ('', '', ''))) != active.token_refs:
            raise MarkingStateError(f'transition {t_id!r} active claim refs changed in memory')
        return claimed

def claimed_token_refs(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> tuple[VersionRef, ...]:
    """Return the exact registered consume/read identities of one firing.

        Resource identity is deliberately not a substitute: content-less capacity,
        ignition, verdict, and counter tokens also have their own ``petri_token/v1``
        records.  Missing, duplicate, or non-canonical token refs fail closed.
        """
    self.claimed_operation_tokens(t_id, claim_epoch, instance_key=instance_key)
    with self._lock:
        _key, active = self._active_claim(t_id, claim_epoch, instance_key)
        refs = tuple(sorted((*active.token_refs, *active.reference_token_refs), key=_version_ref_key))
        if len(set(refs)) != len(refs):
            raise MarkingStateError('active claim repeats a petri_token/v1 ref')
        return refs

def claimed_operation_tokens(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> list[Token]:
    """Return consumed inputs plus non-consuming read-lease occurrences."""
    consumed = self.claimed_tokens(t_id, claim_epoch, instance_key=instance_key)
    with self._lock:
        _key, active = self._active_claim(t_id, claim_epoch, instance_key)
        by_id = {token.token_id: token for token in self._tokens}
        references = [by_id[token_id] for token_id in active.reference_token_ids if token_id in by_id]
        if len(references) != len(active.reference_token_ids) or any((token.consumed_by is not None or token.epoch != claim_epoch for token in references)):
            raise MarkingStateError(f'transition {t_id!r} read claim no longer matches its tokens')
        self._validate_runtime_token_refs(references, f'claimed_read_tokens[{t_id!r}]', require_token_ref=True)
        if tuple(sorted((token.token_ref for token in references), key=_version_ref_key)) != active.reference_token_refs:
            raise MarkingStateError(f'transition {t_id!r} read claim refs changed in memory')
        return [*consumed, *references]

def verify_active_claim(self, t_id: str, claim_epoch: int, claimed_token_refs: tuple[VersionRef, ...], *, instance_key: int | VersionRef | None=None) -> None:
    """Bind an admitted Registry firing to the just-selected local claim."""
    if not isinstance(claimed_token_refs, tuple) or any((not isinstance(ref, VersionRef) for ref in claimed_token_refs)):
        raise MarkingStateError('active claim verification requires an exact-ref tuple')
    expected = tuple(sorted(claimed_token_refs, key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    actual = self.claimed_token_refs(t_id, claim_epoch, instance_key=instance_key)
    if actual != expected:
        raise MarkingStateError(f'transition {t_id!r} local claim differs from Registry admission')

def clear_active_claim(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> None:
    """Forget one settled process-local claim without changing token history."""
    with self._lock:
        key, _active = self._active_claim(t_id, claim_epoch, instance_key)
        del self._active_claims[key]

def singleton_settlement_view(self, instance_key: VersionRef) -> 'TeamNetMarking':
    """Clone one completed instance while retaining siblings as claims only.

        Unfinished siblings remain atomically reserved in ``self`` and in the
        Registry active-firing set.  Their provisional local ``consumed_by``
        stamps are cleared only in the isolated snapshot candidate so settlement
        cannot falsely consume them.
        """
    if not isinstance(instance_key, VersionRef):
        raise MarkingStateError('singleton settlement requires an exact firing instance ref')
    with self._lock:
        target = self._active_claims.get(instance_key)
        if target is None:
            raise MarkingStateError('singleton settlement instance is not active')
        candidate = self._validated_candidate_mapping()
        sibling_claims = tuple((claim for key, claim in self._active_claims.items() if key != instance_key))
        next_claim_id = self._next_claim_id
    view = type(self)(self._net)
    view._restore_candidate_mapping(candidate)
    with view._lock:
        by_id = {token.token_id: token for token in view._tokens}
        occupied: set[int] = set()
        for claim in sibling_claims:
            for token_id in claim.token_ids:
                token = by_id.get(token_id)
                if token is None or token.token_ref not in claim.token_refs or token.consumed_by != claim.transition_id or (token_id in occupied):
                    raise MarkingStateError('active sibling claim differs from settlement candidate')
                occupied.add(token_id)
                token.consumed_by = None
            reference_tokens = [by_id.get(token_id) for token_id in claim.reference_token_ids]
            if any((token is None or token.consumed_by is not None or token.token_ref not in claim.reference_token_refs for token in reference_tokens)) or len(reference_tokens) != len(claim.reference_token_refs):
                raise MarkingStateError('active sibling read claim differs from settlement candidate')
        view._active_claims = {instance_key: target}
        view._next_claim_id = next_claim_id
    return view

def verify_exclusive_active_claim(self, instance_key: VersionRef) -> None:
    """Require one exact firing to own the complete process-local claim set.

        An epoch-changing proposal rewrites the whole marking and therefore
        cannot be rebased as one disjoint sibling delta.  The caller may submit
        that proposal through exact predecessor CAS only when no unfinished
        sibling claim would be crossed by the rewrite.
        """
    if not isinstance(instance_key, VersionRef):
        raise MarkingStateError('exclusive settlement requires an exact firing instance ref')
    with self._lock:
        if set(self._active_claims) != {instance_key}:
            raise MarkingStateError('epoch-changing settlement requires one exclusive active claim')

def carry_active_claims_to(self, successor: 'TeamNetMarking', *, settled_instance_key: VersionRef) -> None:
    """Reattach exact unfinished claims to a committed successor marking."""
    if not isinstance(successor, TeamNetMarking):
        raise MarkingStateError('claim carry requires TeamNetMarking successor')
    with self._lock:
        remaining = tuple(((key, claim) for key, claim in self._active_claims.items() if key != settled_instance_key))
        next_claim_id = self._next_claim_id
    with successor._lock:
        if successor._active_claims or successor._timed_active_claims:
            raise MarkingStateError('successor already contains active claims')
        by_ref = {token.token_ref: token for token in successor._tokens if isinstance(token.token_ref, VersionRef)}
        occupied: set[int] = set()
        carried: dict[int | VersionRef, _ActiveClaim] = {}
        for key, claim in remaining:
            tokens = [by_ref.get(ref) for ref in claim.token_refs]
            if len(tokens) != len(claim.token_refs) or any((token is None or token.consumed_by is not None or token.epoch != claim.epoch or (token.token_id in occupied) for token in tokens)):
                raise MarkingStateError('committed successor cannot reconstruct an active sibling claim')
            for token in tokens:
                assert token is not None
                token.consumed_by = claim.transition_id
                occupied.add(token.token_id)
            reference_tokens = [by_ref.get(ref) for ref in claim.reference_token_refs]
            if len(reference_tokens) != len(claim.reference_token_refs) or any((token is None or token.consumed_by is not None or token.epoch != claim.epoch for token in reference_tokens)):
                raise MarkingStateError('committed successor cannot reconstruct a sibling read claim')
            carried[key] = claim
        successor._active_claims = carried
        successor._next_claim_id = next_claim_id
