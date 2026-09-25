"""Epoch revision, invalidation, and structural re-marking."""
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
    from cpn.rpnh.registry.resources import PetriTokenState

def bump_epoch(self, *, carry_forward_except: Optional[set[str]]=None) -> int:
    """Bump the provenance epoch (§5.1bis step 1). Returns the new epoch.

        DEFAULT (``carry_forward_except is None``) — every pre-bump token becomes non-fresh
        by construction (fresh requires ``epoch == current``). This preserves the in-flight
        guard (``deposit_outputs`` drops an output claimed at an older epoch).

        SCOPED (``carry_forward_except`` given — the §5.1bis re-mark, fix 1) — only tokens on
        a place IN that set go stale; every OTHER currently-fresh token is PROMOTED to the new
        epoch so it CARRIES FORWARD valid. A re-mark passes the INVALIDATED DOWNSTREAM CLOSURE
        of the faulted artifact here, so the faulted artifact + its transitive downstream
        tokens become stale while an UNAFFECTED fork/join branch's good token survives (it is
        not stranded by a blanket global bump → the join no longer deadlocks). Every carried
        token is a fresh monotonic occurrence; the immutable prior occurrence is never mutated
        or re-admitted under its historical Registry ref. Content-blind: this judges no token
        content; it only carries exact structural colour into the new occurrence."""
    with self._lock:
        old = self._epoch
        self._epoch += 1
        if carry_forward_except is not None:
            carried = tuple((tok for tok in self._tokens if tok.epoch == old and tok.consumed_by is None and (tok.place not in carry_forward_except)))
            carried_ids = {tok.token_id for tok in carried}
            replacements = []
            for tok in carried:
                if tok.token_ref is None:
                    replacements.append(replace(tok, epoch=self._epoch))
                else:
                    replacements.append(replace(tok, token_ref=None, token_id=self._next_id, epoch=self._epoch))
                    self._next_id += 1
            self._tokens = [tok for tok in self._tokens if tok.token_id not in carried_ids]
            self._tokens.extend(replacements)
        return self._epoch

def downstream_closure(net: 'MarkingNetStructure', faulted_place: str) -> set[str]:
    """The transitive downstream PLACE closure of ``faulted_place`` over the
        declared arc-graph (§5.1bis step 2) — every place reachable via
        place→transition→place arcs, including ``faulted_place`` itself.

        Pure graph traversal over the DECLARED arcs (content-blind); a re-marking
        invalidates the tokens on this closure so a join cannot fire on mixed
        fresh+stale input. Provided here as the cheap helper T3's transaction calls.
        """
    seen: set[str] = set()
    frontier: list[str] = [faulted_place]
    while frontier:
        place = frontier.pop()
        if place in seen:
            continue
        seen.add(place)
        for t in net.consumers_of(place):
            for out_p in net.outputs_of(t):
                if out_p not in seen:
                    frontier.append(out_p)
    return seen

def invalidate_places(self, places: set[str]) -> int:
    """Mark every fresh token on ``places`` invalidated (re-marking, §5.1bis).

        Stamps ``consumed_by = INVALIDATED`` so the token renders non-fresh without
        being attributed to a real firing. Returns the count invalidated. (The
        epoch bump already renders pre-bump tokens non-fresh; this additionally
        clears any same-epoch tokens on the closure — used by T3's transaction.)
        """
    with self._lock:
        n = 0
        for tok in self._tokens:
            if tok.place in places and tok.consumed_by is None:
                tok.consumed_by = self.INVALIDATED
                n += 1
        return n

def _resource_tokens_for_remark(self, place: str, t_id: str) -> list[Token]:
    """The producer ``t_id``'s recorded semantic upstream input tokens on
        ``place`` (caller holds the lock) — tokens (consumed or still fresh) that carry an
        exact ``resource_ref``, lease claim, or concrete declared colour and that ``t_id``
        could read (addressed to ``t_id`` OR shared), ordered by descending allocation id.
        Used by :meth:`remark_inputs` to re-deposit the producer's REAL inputs (a
        content-BLIND exact-ref/colour copy). EMPTY when ``place`` never held a semantic token
        (a source / M₀ place — the source has no upstream content to copy).

        The returned token is copied only for its ordinary Petri provenance and
        resource/colour fields; it carries no separate control identity or fault state."""
    cands = [tok for tok in self._tokens if tok.place == place and (tok.resource_ref is not None or tok.lease_claims or tok.verdict is not None) and (tok.consumer == t_id or tok.consumer is None)]
    cands.sort(key=lambda tok: tok.token_id, reverse=True)
    self._validate_runtime_token_refs(cands, f'remark_source[{place!r},{t_id!r}]')
    return cands

def remark_inputs(self, t_id: str, *, continuation: Optional[dict]=None, additional_lease_claims: tuple[dict, ...]=(), direct_route_resource: Optional[tuple[str, ResourceVersionRef, str]]=None) -> int:
    """Re-deposit fresh tokens on each claim input of ``t`` so the executor re-fires it
        (§5 step 4). Returns the count deposited.

        The ledger side of routing a veto to a declared producer transition: it re-enables
        ``t`` so the executor's ordinary firing re-fires it (and, via the deposited outputs,
        its downstream tail). A registered exact net iterates the same
        :meth:`_claim_input_arcs` used by enabling/consume-on-fire, so every immutable
        input file is re-presented as a Petri token and no synthetic ignition is minted.
        An unregistered net retains the historical produced-input + ignition behavior.

        Each re-deposited PRODUCED-input token CARRIES a recorded semantic upstream input
        (its existing exact resource refs / kind — fix 1): the admitted firing boundary can
        deliver those exact bytes again, without resolving an address. A SOURCE transition's only token input is
        its IGNITION slot (no content) → it re-deposits a content-less, ADDRESSED ignition
        token (``consumer=t``) so the source re-IGNITES precisely.  A declared ``read`` input
        is the exception: its occurrence remains shared so a later declared consumer can use
        the returned token. Content-blind: the
        framework copies the exact ref it already holds; it judges no content. The per-place
        count matches the declared INPUT-arc weight (ignition arc weight 1), so a producer
        that consumes ≥ 2 tokens from a place is actually re-enabled."""
    continuation = self._runtime_continuation(continuation, 'remark_inputs.continuation')
    additional_claims = self._runtime_lease_claims(additional_lease_claims, 'remark_inputs.additional_lease_claims')
    direct_resource_place = None
    direct_resource_ref = None
    direct_resource_authority = None
    if direct_route_resource is not None:
        if not isinstance(direct_route_resource, tuple) or len(direct_route_resource) != 3 or (not isinstance(direct_route_resource[0], str)) or (not isinstance(direct_route_resource[1], ResourceVersionRef)) or (not isinstance(direct_route_resource[2], str)):
            raise MarkingResourceRefError('direct route resource must be one exact place-resource-authority tuple')
        direct_resource_place, direct_resource_ref, direct_resource_authority = direct_route_resource
        if self._net.input_arc_weight(direct_resource_place, t_id) != 1 or direct_resource_authority not in self._net.transitions:
            raise MarkingStateError('direct route resource differs from the declared target input')
    with self._lock:
        plan: list[tuple[str, int, list[Token]]] = []
        seen: set[str] = set()
        registered_read_places = {place for place, transition_id, _weight in self._net.registry_read_arcs if transition_id == t_id} if isinstance(getattr(self._net, 'registry_net_ref', None), VersionRef) else set()
        read_scope_places = {place for place, scope in self._net.read_scope_of(t_id) if scope == 'read'}
        variable_arc = self._net.variable_resource_arc_for(t_id)
        claim_token_place = variable_arc.claim_token_place if variable_arc is not None else None

        def current_claims(place: str, recorded: tuple[dict, ...]) -> tuple[dict, ...]:
            claims = recorded if recorded else self._declared_lease_claims_for_place(place)
            if not additional_claims or place != claim_token_place:
                return claims
            identities = {claim['lease_identity_ref'] for claim in claims}
            additions = tuple((claim for claim in additional_claims if claim['lease_identity_ref'] not in identities))
            return (*claims, *additions)
        for p, _t, _w in self._claim_input_arcs(t_id):
            if p in seen:
                continue
            seen.add(p)
            if p in self._net.agent_resource_places():
                continue
            weight = self._net.input_arc_weight(p, t_id) or 1
            fresh = self._fresh_on(p, t_id, require_token_ref=False)
            missing = max(0, weight - len(fresh))
            content = self._resource_tokens_for_remark(p, t_id)
            if p in registered_read_places and p != direct_resource_place and (len(content) < missing):
                raise MarkingResourceRefError('registered exact re-mark cannot fabricate a missing file token')
            plan.append((p, missing, content))
        if direct_resource_place is not None and direct_resource_place not in seen:
            plan.append((direct_resource_place, 1, []))
        deposited = 0
        for p, weight, content in plan:
            for i in range(weight):
                if p == direct_resource_place:
                    assert direct_resource_ref is not None
                    assert direct_resource_authority is not None
                    consumer = None if p in read_scope_places else t_id
                    self._new_token(place=p, epoch=self._epoch, producer=None, consumer=consumer, resource_ref=direct_resource_ref, continuation=continuation)
                elif content:
                    src = content[min(i, len(content) - 1)]
                    lease_claims = current_claims(p, tuple(src.lease_claims))
                    consumer = None if p in read_scope_places else t_id
                    self._new_token(place=p, epoch=self._epoch, producer=src.producer, consumer=consumer, resource_ref=src.resource_ref, work_resource_ref=src.work_resource_ref, kind=src.kind, verdict=src.verdict, continuation=continuation, lease_identity_ref=src.lease_identity_ref, lease_claims=lease_claims)
                else:
                    verdict = None
                    domain = self._net.place_color_sets.get(p, ())
                    if domain:
                        colour_source = next((token for token in sorted(self._tokens, key=lambda token: token.token_id, reverse=True) if token.place == p and (token.consumer == t_id or token.consumer is None) and (token.verdict is not None) and any((_verdict_color_matches(token.verdict, expected) for expected in domain))), None)
                        if colour_source is None:
                            raise MarkingStateError('coloured re-mark input lacks a recorded concrete colour')
                        verdict = colour_source.verdict
                    consumer = None if p in read_scope_places else t_id
                    lease_claims = current_claims(p, ())
                    self._new_token(place=p, epoch=self._epoch, producer=None, consumer=consumer, resource_ref=None, verdict=verdict, continuation=continuation, lease_claims=lease_claims)
                deposited += 1
        return deposited

def would_remark_reenable(self, t_id: str) -> bool:
    """§34.21 P4 (codex P1) — READ-ONLY prediction of whether re-marking ``t_id``'s inputs
        (:meth:`remark_inputs`) would leave it ENABLED (:meth:`is_enabled` → True), WITHOUT
        mutating the ledger.

        The finalization-veto loop-close must consult the SHARED §G re-arm budget for the
        producer it is ABOUT TO re-arm BEFORE the mutating ``remark_faulted_artifact``
        (gate-BEFORE-mutate): an EXHAUSTED budget must SKIP the re-mark entirely so the producer
        is NEVER re-armed (the codex P1 ordering defect re-armed it first, then only suppressed
        the resume). To gate the genuine re-arm ROOT before the mutation — and ONLY the root, so a
        the runner needs to know, read-only, which handle would ACTUALLY re-enable its producer
        (the post-remark ``target_enabled`` fact, computed BEFORE the remark).

        This predicts that fact from the SAME structural inputs ``remark_inputs`` +
        ``_is_enabled_locked`` read — for EVERY TOKEN input arc of ``t`` the re-mark re-deposits
        ``input_arc_weight`` copies of recorded semantic upstream tokens
        (``_resource_tokens_for_remark``); a SOURCE/ignition arc re-deposits a fresh content-less
        token. The producer would be enabled iff EVERY token-input arc would then hold its
        declared arc weight. Read-only;
        content-blind; judges no content (§34.0)."""
    with self._lock:
        arcs = [(p, w) for p, _t, w in self._claim_input_arcs(t_id)]
        if not arcs:
            return False
        seen: set[str] = set()
        for p, _w in arcs:
            if p in seen:
                continue
            seen.add(p)
            content = self._resource_tokens_for_remark(p, t_id)
            if not content:
                continue
            weight = self._net.input_arc_weight(p, t_id) or 1
        return True

def remark_join_siblings(self, closure: set[str], refired_producer: str, *, settled_join_inputs: Sequence[tuple[str, 'PetriTokenState']] | None=None) -> int:
    """Re-deposit (REHYDRATE) the already-CONSUMED SIBLING inputs of every downstream
        JOIN in ``closure`` so a fired join can re-enable after a re-mark (§5.1bis, fix —
        join-aware rehydration). Returns the count re-deposited. Atomic under the ledger lock.

        The scoped epoch bump (``bump_epoch(carry_forward_except=closure)``) CARRIES FORWARD an
        unaffected branch's STILL-FRESH token, but it cannot resurrect a token a join ALREADY
        CONSUMED. Once a join ``t_join`` has FIRED — consuming BOTH ``done_a`` and ``done_b``
        atomically — and a downstream veto re-marks ``done_a``, the re-fired producer ``t_a``
        re-supplies ONLY ``done_a`` (``remark_inputs`` touches the producer's own inputs); the
        sibling ``done_b`` token is gone and the join can NEVER re-enable → the net STALLS below
        F1 (never reaches the ceiling, never terminates cleanly). This re-presents the sibling.

        For every transition that (a) consumes ≥ 1 place in ``closure`` (it is in the re-firing
        tail) AND (b) is a JOIN (≥ 2 DISTINCT PRODUCED input places — ``token_inputs_of``;
        registry-read source inputs are excluded, they hold no consumable token and so can
        never be a stranded sibling), it re-deposits — at the NEW epoch,
        respecting the declared INPUT-arc weight, ADDRESSED to the join — that join's recorded
        CONTENT-BEARING consumed token for each SIBLING input place. A sibling input place is one
        that is:
          (i)   NOT in ``closure`` — it is NOT on the faulted branch, so the re-firing cascade
                will NOT re-produce it (a place IN the closure WILL be re-produced → skip it, no
                double-deposit);
          (ii)  NOT produced by ``refired_producer`` — the directly re-marked producer re-supplies
                its OWN outputs when it re-fires (no double-deposit on the faulted branch); and
          (iii) NOT already holding a fresh consumable token — a still-fresh sibling was carried
                forward by the scoped bump (the join-enabled-but-NOT-yet-fired case), so leave it
                untouched → that case stays byte-identical.

        Content-blind handle copy (§34.0): the rehydrated token re-presents the sibling's EXISTING
        exact resource refs / kind — the sibling content is ALREADY VALID, the sibling
        producer is NOT re-run — promoted to the new epoch. For a Registry-projected selected
        route, ``settled_join_inputs`` contains only exact claimed token states from completed
        firings in the predecessor checkpoint lineage. That tuple both limits rehydration to a
        join that actually settled and restores a consumed contentless colour without inventing
        an alternative route. The in-memory fault-remap caller supplies no tuple and retains its
        local token-ledger fallback. Ledger-only; Registry-v1 is untouched."""
    net = self._net
    deposited = 0
    with self._lock:
        self._validate_runtime_token_refs(self._tokens, 'remark_join_siblings.tokens')
        historical_by_join_place: dict[tuple[str, str], list['PetriTokenState']] | None = None
        historical_transitions: set[str] = set()
        if settled_join_inputs is not None:
            from cpn.rpnh.registry.resources import PetriTokenState
            if not isinstance(settled_join_inputs, Sequence) or isinstance(settled_join_inputs, (str, bytes)):
                raise MarkingStateError('settled join inputs must be one typed sequence')
            historical_by_join_place = {}
            for index, item in enumerate(settled_join_inputs):
                if not isinstance(item, tuple) or len(item) != 2 or (not isinstance(item[0], str)) or (item[0] not in net.transitions) or (not isinstance(item[1], PetriTokenState)) or (not isinstance(item[1].token_ref, VersionRef)) or (item[1].place not in net.token_inputs_of(item[0])):
                    raise MarkingStateError(f'settled_join_inputs[{index}] is not one exact claimed join input')
                transition_id, state = item
                historical_transitions.add(transition_id)
                historical_by_join_place.setdefault((transition_id, state.place), []).append(state)
            for states in historical_by_join_place.values():
                states.sort(key=lambda state: state.token_id, reverse=True)
        for t_id in net.transitions:
            distinct_inputs: list[str] = []
            for p in net.token_inputs_of(t_id):
                if p not in distinct_inputs:
                    distinct_inputs.append(p)
            if len(distinct_inputs) < 2:
                continue
            if historical_by_join_place is not None and t_id not in historical_transitions:
                continue
            if all((p not in closure for p in distinct_inputs)):
                continue
            for p_sib in distinct_inputs:
                if p_sib in closure:
                    continue
                if refired_producer in net.producers_of(p_sib):
                    continue
                if self._fresh_on(p_sib, t_id, require_token_ref=False):
                    continue
                weight = net.input_arc_weight(p_sib, t_id) or 1
                content = self._resource_tokens_for_remark(p_sib, t_id)
                historical = historical_by_join_place.get((t_id, p_sib), []) if historical_by_join_place is not None else []
                guard_colors = dict(net.verdict_guards_of(t_id))
                for i in range(weight):
                    if content and historical_by_join_place is None:
                        src = content[min(i, len(content) - 1)]
                        self._new_token(place=p_sib, epoch=self._epoch, producer=src.producer, consumer=t_id, resource_ref=src.resource_ref, work_resource_ref=src.work_resource_ref, kind=src.kind, verdict=src.verdict, continuation=deepcopy(src.continuation), lease_identity_ref=src.lease_identity_ref, lease_claims=tuple(src.lease_claims))
                    elif historical:
                        src = historical[min(i, len(historical) - 1)]
                        if p_sib in guard_colors and (not _verdict_color_matches(src.verdict, guard_colors[p_sib])):
                            raise MarkingStateError('settled join input colour differs from its declared transition guard')
                        continuation = None if src.continuation is None else {'round': src.continuation.round, 'source_ref': src.continuation.source_ref}
                        lease_claims = tuple(({'lease_identity_ref': claim.lease_identity_ref, 'expected_resource_ref': claim.expected_resource_ref, 'access_mode': claim.access_mode, 'staging_place': claim.staging_place} for claim in src.lease_claims))
                        self._new_token(place=p_sib, epoch=self._epoch, producer=src.producer, consumer=t_id, resource_ref=src.resource_ref, work_resource_ref=src.work_resource_ref, kind=src.kind, verdict=src.verdict, continuation=continuation, lease_identity_ref=src.lease_identity_ref, lease_claims=lease_claims)
                    elif historical_by_join_place is not None:
                        continue
                    else:
                        if self._net.place_color_sets.get(p_sib, ()):
                            continue
                        self._new_token(place=p_sib, epoch=self._epoch, producer=None, consumer=t_id)
                    deposited += 1
    return deposited

def restore_declared_read_lanes(self, closure: set[str], *, excluded_places: Sequence[str]=()) -> int:
    """Restore declared initial read lanes needed by an invalidated tail.

        An epoch rewrite can invalidate an optional read input even when the
        selected route re-marks only the transition's entry token.  Restore
        exactly the initial, unit-weight inputs declared with ``read`` scope
        before downstream scheduling.  No transition config or resource bytes
        participate in the selection.
        Existing resource-bearing occurrences preserve actor progress; an
        initially contentless lane remains contentless.
        """
    excluded = set(excluded_places)
    if len(excluded) != len(tuple(excluded_places)) or any((place not in self._net.places for place in excluded)):
        raise MarkingStateError('excluded read lanes must be unique declared places')
    deposited = 0
    with self._lock:
        for transition_id in self._net.transitions:
            if not any((place in closure for place in self._net.inputs_of(transition_id))):
                continue
            lanes = tuple(dict.fromkeys((place for place, scope in self._net.read_scope_of(transition_id) if scope == 'read' and self._net.initial_marking.get(place) == 1 and (self._net.input_arc_weight(place, transition_id) == 1))))
            for place in lanes:
                if place in excluded:
                    continue
                if self._fresh_on(place, transition_id, require_token_ref=False):
                    continue
                content = self._resource_tokens_for_remark(place, transition_id)
                if content:
                    source = content[0]
                    self._new_token(place=place, epoch=self._epoch, producer=source.producer, consumer=None, resource_ref=source.resource_ref, work_resource_ref=source.work_resource_ref, kind=source.kind, verdict=source.verdict, continuation=deepcopy(source.continuation), lease_identity_ref=source.lease_identity_ref, lease_claims=tuple(source.lease_claims))
                else:
                    self._new_token(place=place, epoch=self._epoch, producer=None, consumer=None)
                deposited += 1
    return deposited
