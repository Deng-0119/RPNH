"""Token allocation, counters, attempts, and direct marking mutations."""
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

def _local_token_view(self) -> tuple[int, tuple[Token, ...]]:
    """Return an isolated typed view for local structural projection only.

        This is not checkpoint or transfer authority.  It deliberately carries no
        mapping/JSON representation and every mutable token record is deep-copied while
        the ledger lock is held.
        """
    with self._lock:
        return (self._epoch, tuple(deepcopy(self._tokens)))

def _new_token(self, **kw) -> Token:
    """Allocate a token with a unique id (caller holds the lock)."""
    self._require_concrete_place_colour(kw.get('place'), kw.get('verdict'), path='new token')
    tok = Token(token_id=self._next_id, **kw)
    self._next_id += 1
    self._tokens.append(tok)
    return tok

def _require_concrete_place_colour(self, place: object, colour: object, *, path: str) -> None:
    """Reject absent or non-domain values for an explicitly coloured place."""
    domain = self._net.place_color_sets.get(place, ())
    if not domain:
        return
    if colour is None or not any((_verdict_color_matches(colour, expected) for expected in domain)):
        raise MarkingStateError(f'{path} on coloured place {place!r} requires one concrete colour from its declared domain')

def _fresh_on(self, place: str, t_id: Optional[str], *, require_token_ref: bool=True) -> list[Token]:
    """Tokens on ``place`` that are FRESH (caller holds the lock).

        Fresh = current-epoch AND unconsumed. When ``t_id`` is given,
        additionally require the token be CONSUMABLE BY t — i.e. addressed to t OR shared
        (``consumer is None``). ``t_id=None`` = "is the place marked at all"
        (the goal-place check), ignoring addressing. Normal enablement/claim
        callers require committed token refs. An isolated atomic marking rewrite
        may set ``require_token_ref=False`` only while deduplicating provisional
        tokens that the same rewrite will publish in its final typed snapshot.

        """
    out: list[Token] = []
    for tok in self._tokens:
        if tok.place != place:
            continue
        if tok.epoch != self._epoch or tok.consumed_by is not None:
            continue
        if t_id is not None and tok.consumer is not None and (tok.consumer != t_id):
            continue
        self._validate_runtime_token_refs([tok], f'fresh_token[{place!r}]', require_token_ref=require_token_ref)
        out.append(tok)
    return out

def clear_counter_place(self, place: str) -> int:
    """§36 P0 (design §3.2 option (i) / §3.3(B)) — RESET a counter-place tally to 0.

        Stamps every FRESH (current-epoch, unconsumed) token on ``place`` non-fresh
        (``consumed_by = INVALIDATED``), directly analogous to :meth:`invalidate_places` but
        scoped to ONE counter-place — so the count guard's ``len(_fresh_on(place, …))`` drops
        to 0. Returns the count cleared. This is the marking-time RESET the explicit-Petri
        escalation fires on ``regenerate_from_scratch`` / ``accept_override`` (and a confirm
        drain) so the reject tally restarts a fresh round window — the structural re-expression
        of the imperative ``self._gG_reject_count_by_gate[gate] = 0`` (``team_net_executor``).

        NOT an incidence arc (invisible to ``incidence_matrix``, exactly like
        :meth:`invalidate_places`), so it CANNOT break
        ``check_boundedness``: a decrease-only marking op never manufactures unboundedness. A
        cleared token carries ``consumed_by`` set, so a subsequent counter-place-EXEMPT re-mark
        epoch bump (``remark_faulted_artifact(exempt_counter_places=True)``) does NOT re-freshen
        it (``bump_epoch`` promotes only ``consumed_by is None`` tokens) — reset and accumulate
        compose cleanly. Content-blind (§34.0): reads only the place name + freshness, judges no
        content. Atomic under the ledger lock. LIVE in the explicit-review Petri subnet (Phase 3
        landed): ``_route_structural_escalation`` (``team_net_executor``) calls this on
        ``regenerate_from_scratch`` / ``accept_override`` to reset the reject tally after an
        escalation. Flag-OFF no net declares a reject counter, so it is never reached
        (byte-identical for every legacy net)."""
    with self._lock:
        n = 0
        for tok in self._tokens:
            if tok.place == place and tok.epoch == self._epoch and (tok.consumed_by is None):
                tok.consumed_by = self.INVALIDATED
                n += 1
        return n

def deposit_count_token(self, place: str) -> None:
    """§36 B3 (design §B3 step 1) — deposit ONE fresh, SHARED count token on a counter-place
        ``place`` (the ledger-side increment of a per-node re-fire tally). Used by
        :func:`team_net_remark.remark_faulted_artifact` to accrue ``atom_node_refire_count`` on a
        ``regenerate`` / crash / faulted re-fire (the structural ``T_reject`` deposit is a declared
        arc; this covers the re-marks that do NOT fire ``T_reject``). The token carries NO
        artifact / verdict (a content-LESS count token, exactly like a ``T_reject``-deposited
        counter token) and NO addressed consumer (SHARED — the executor reads the TOTAL tally off
        the place, not a per-consumer count), so ``fresh_count(place, None)`` reads the accrued
        re-fire count. Deposited at the CURRENT epoch → fresh; because the counter place is
        exempt from the re-mark staling (``remark_faulted_artifact(exempt_counter_places=True)``),
        the token is carried forward across the next epoch bump = MONOTONE. Atomic under the ledger
        lock. Content-blind (§34.0): a token count, judged nowhere."""
    with self._lock:
        self._new_token(place=place, epoch=self._epoch, producer=None, consumer=None)

def consume_place_tokens(self, place: str) -> int:
    """§36 X3 (fix ③, design §10.3) — CONSUME (invalidate) every FRESH (current-epoch,
        unconsumed) token on ``place``, returning the count consumed. The GENERAL sibling of
        :meth:`clear_counter_place` (SAME under-lock invalidate mechanism), used by the executor to
        consume the ``atom_escalation_decision`` token(s) when it ROUTES the escalation decision to
        the chosen transition — so the OTHER decision transitions cannot free-fire on a residual
        decision token in the next pass (the executor drives the guarded free-choice the marking
        cannot disambiguate). NOT an incidence arc (invisible to ``incidence_matrix``): a
        decrease-only marking op cannot manufacture unboundedness. Content-blind (§34.0): reads only
        the place name + freshness; judges no content. Atomic under the ledger lock."""
    with self._lock:
        n = 0
        for tok in self._tokens:
            if tok.place == place and tok.epoch == self._epoch and (tok.consumed_by is None):
                tok.consumed_by = self.INVALIDATED
                n += 1
        return n

def attempt(self, t_id: str) -> int:
    """The current (highest-issued) attempt index ``k`` for ``t_id`` (0 if it has never
        fired). Pure read under the ledger lock; content-blind."""
    with self._lock:
        return int(self._attempts.get(t_id, 0))

def next_attempt(self, t_id: str) -> int:
    """Issue + return the NEXT attempt index ``k`` for ``t_id`` (increment-then-return,
        so a transition's FIRST firing is ``k = 1`` → an ``@a1`` round-stamped key). The
        counter is a TOP-LEVEL marking field — never a per-token field. Atomic under the
        ledger lock; content-blind (it counts firings, judges no content)."""
    with self._lock:
        k = int(self._attempts.get(t_id, 0)) + 1
        self._attempts[t_id] = k
        return k

def record_settled_attempt(self, transition_id: str, attempt: int) -> int:
    """Record one exact start-assigned attempt applied at firing success.

        Concurrent firings of the same transition may settle out of order, so
        success projection advances the checkpoint counter to the greater of
        its current value and this firing's exact positive attempt.  It never
        issues a new attempt and never regresses an already-settled value.
        """
    transitions = set(self._net.transitions) | set(self._net.registered_fault_transition_routes)
    if not isinstance(transition_id, str) or transition_id not in transitions:
        raise MarkingStateError('settled attempt requires one declared transition id')
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt <= 0:
        raise MarkingStateError('settled attempt must be a positive integer')
    with self._lock:
        highest = max(self._attempts.get(transition_id, 0), attempt)
        self._attempts[transition_id] = highest
        return highest

def reconcile_attempt(self, t_id: str, highest_retained: int) -> int:
    """Resume reconcile (§34.20 G1 gap-fix 4): advance ``k`` for ``t_id`` to AT LEAST
        ``highest_retained`` (the highest attempt already registered on disk), so the next
        issued attempt cannot COLLIDE with an already-retained round (which would overwrite
        it). NEVER lowers the counter. Returns the reconciled value. Atomic; content-blind."""
    with self._lock:
        try:
            floor = int(highest_retained)
        except (TypeError, ValueError):
            floor = 0
        if floor > int(self._attempts.get(t_id, 0)):
            self._attempts[t_id] = floor
        return int(self._attempts.get(t_id, 0))

def seed_initial(self, initial_marking: Optional[dict]=None) -> int:
    """Seed an unregistered M₀ with the legacy private-ignition model.

        A Registry-bound exact net must be restored from
        :class:`TypedMarkingAuthority`; seeding it would fabricate an ignition token
        outside Registry admission and is rejected.  For an unregistered net, the
        historical §34.17-§6.6 model treats task inputs as ambient reads, so
        seeding does NOT deposit a contested token on ``user_prompt`` / any
        registry-read source place. Instead:
          (a) for each SOURCE transition (``net.synthetic_ignition_arcs``), deposit ONE
              private, ADDRESSED, content-less IGNITION token on its internal
              ``_ignition_place(t)`` slot (``consumer=t``) — so multiple sources NEVER
              contend (each consumes its OWN ignition token → all fire; the source-fan-out
              CHOICE deadlock is gone by construction).
          (b) a declared M₀ on a registry-read (source) place is INERT — a task input holds
              no marking token at all (``registry ≠ marking`` REINFORCED); it is skipped
              (the loader already records an undeclared/absent M₀ as a structural smell).
          (c) a declared M₀ on a NON-source place (rare/unusual) is deposited PER-CONSUMER
              ADDRESSED (PANEL), never SHARED, so a pre-marked place feeding several readers
              can never re-introduce the contention the source-fan-out bug exposed.
        Produced places still start empty (their tokens appear when their producer fires).
        The framework NEVER fabricates an M₀; it only ignites the declared START actions.
        """
    if isinstance(getattr(self._net, 'registry_net_ref', None), VersionRef):
        raise MarkingStateError('registered exact marking must be restored from Registry authority')
    m0 = initial_marking if initial_marking is not None else dict(self._net.initial_marking)
    source_set = set(self._net.sources)
    deposited = 0
    with self._lock:
        for ig_place, t, _w in self._net.synthetic_ignition_arcs:
            self._new_token(place=ig_place, epoch=self._epoch, producer=None, consumer=t)
            deposited += 1
        for place, count in m0.items():
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            n = max(0, n)
            lease_pool = next((pool for pool in self._net.resource_lease_pool_bindings if str(place) == pool.lease_pool_place), None)
            if lease_pool is not None:
                exact_identities = (*lease_pool.initial_resource_refs, *lease_pool.initial_logical_slot_refs)
                if n != len(exact_identities):
                    raise MarkingStateError('v6 lease-pool M0 differs from its colour set')
                for exact in exact_identities:
                    identity = self._version_ref_from_exact(exact)
                    current = self._resource_ref_from_exact(exact) if exact.entity_type == 'resource_version/v1' else None
                    self._new_token(place=str(place), epoch=self._epoch, producer=None, consumer=None, resource_ref=current, lease_identity_ref=identity)
                    deposited += 1
                continue
            if place in source_set:
                continue
            consumers = tuple(sorted(self._net.consumers_of(str(place))))
            if str(place) in self._net.agent_resource_places():
                consumer = consumers[0] if len(consumers) == 1 else None
                for _ in range(n):
                    self._new_token(place=str(place), epoch=self._epoch, producer=None, consumer=consumer)
                    deposited += 1
            elif consumers:
                for c in consumers:
                    for _ in range(n):
                        self._new_token(place=str(place), epoch=self._epoch, producer=None, consumer=c)
                        deposited += 1
            else:
                for _ in range(n):
                    self._new_token(place=str(place), epoch=self._epoch, producer=None, consumer=None)
                    deposited += 1
    return deposited

def deposit_resource_token(self, place: str, resource_ref: ResourceVersionRef, kind: Optional[str]=None) -> int:
    """Phase E / R-1 (design §5.2 step 2) — deposit the ONE runtime RESOURCE token on a
        freshly-bound append-only resource ``place``, coloured by an exact resource version.

        The other half of a ``bind_resource`` typed marking carry: after :meth:`carry_to`
        validates every in-flight token against the extended net (a monotone append renames
        nothing), this deposits EXACTLY ONE token on the new
        resource place — via the SAME single-token primitive ``_new_token`` ``seed_initial`` uses
        — and NOTHING else (it does NOT call ``seed_initial``, which would re-ignite every
        already-fired source + re-deposit the declared M₀). The token is SHARED (``consumer is
        None``): the resource's consumer is a FUTURE author added at the next round boundary
        (§5.2b), so at bind time no consumer is addressed. ``resource_ref`` is already exact;
        no address/head/version selection is permitted. Returns 1. Content-blind (§34.0)."""
    if not isinstance(resource_ref, ResourceVersionRef):
        raise MarkingResourceRefError('semantic resource token requires an exact ResourceVersionRef')
    with self._lock:
        self._new_token(place=str(place), epoch=self._epoch, producer=None, consumer=None, resource_ref=resource_ref, kind=kind)
    return 1
