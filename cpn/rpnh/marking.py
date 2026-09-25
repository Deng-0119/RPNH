"""Exact occurrence marking, DTOs and checkpoint-local structural validation.

Exact marking mechanics read explicitly declared epoch witnesses and
port-scoped claim exclusions; their example policy is supplied outside Core.
Disabled historical timed entrypoints remain disabled.
"""
from __future__ import annotations
from collections import Counter

import itertools
import json
import math
import random
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from .petri_runtime_interfaces import MarkingNetStructure, RegistryTokenAllocator

if TYPE_CHECKING:  # avoid an import cycle at module load (loader has no runtime dep on us)
    from cpn.rpnh.registry.resources import (
        AttemptCounterAuthority,
        ExecutableNetAuthority,
        PetriTokenState,
        TypedMarkingAuthority,
        TypedMarkingSnapshot,
    )


# §36 P0 — the VERDICT guard family kind (design §2.2/§8): a NEW guard family, a sibling of
# the §35 PB count guards ``"threshold"`` / ``"inhibitor"``, but distinct — it reads a token's
# RESERVED VERDICT COLOR (the ``confirmed`` boolean), not a token COUNT. Used to route the
# explicit ``T_pass`` (color ``True``) / ``T_reject`` (color ``False``) free-choice on the
# ``p_verdict`` place. Named here so the composer / loader / executor share one handle; the
# enabling-rule wiring (a ``verdict``-guard branch in ``_is_enabled_locked`` consuming a
# loader ``verdict_guards_of`` accessor) + the executor stamp LANDED in Phase 2/3 behind
# the explicit-review Petri subnet; the content-blind evaluator
# (:meth:`TeamNetMarking.verdict_guard_enabled`) reads the token color slot
# (:attr:`Token.verdict`) — LIVE behind the flag, byte-identical for a legacy net that
# declares no verdict guard.
VERDICT_GUARD_KIND = "verdict"


class MarkingStateError(ValueError):
    """A persisted marking is not an exact state of its declared Petri net.

    Recovery is an authority boundary: callers may catch this typed error and
    fail the resume, but the marking never repairs, defaults, skips, or partially
    adopts the rejected checkpoint.
    """


class MarkingResourceRefError(MarkingStateError):
    """A semantic Petri token lacks one exact Registry-v1 resource version."""


def count_guard_holds(count: int, threshold: int, comparison: str) -> bool:
    """Evaluate declared PN count predicates without application selectors."""
    if type(count) is not int or type(threshold) is not int or min(count, threshold) < 0:
        raise MarkingStateError("count guards require exact nonnegative quantities")
    comparisons = {
        "threshold": count >= threshold, "inhibitor": count < threshold,
        "ge": count >= threshold, "gt": count > threshold,
        "le": count <= threshold, "lt": count < threshold,
        "eq": count == threshold, "ne": count != threshold,
    }
    if comparison not in comparisons:
        raise MarkingStateError("count predicate comparison is not declared")
    return comparisons[comparison]


def _version_ref_key(ref: VersionRef) -> tuple[str, str, str]:
    """Canonical exact-reference ordering shared by bindings and claims."""
    return ref.entity_type, str(ref.entity_id), str(ref.version_id)


# Keep the historic public module as the compatibility boundary.  Importing
# after its error and ordering helpers avoids a cycle while the pure delta
# implementation preserves their exact semantics.
from ._marking.delta import (
    PetriMarkingDelta,
    PetriTokenEdit,
    apply_petri_marking_delta,
    derive_petri_marking_delta,
    verify_petri_marking_delta,
)


def exact_token_identity(ref: VersionRef) -> str:
    """Canonical exact identity shared with runtime resource observations.

    The scheduler treats this text as opaque.  The marking is the authority that
    resolves it back to one committed Petri token, preventing a separately
    invented pool/unit name from claiming a different resource token.
    """
    if not isinstance(ref, VersionRef):
        raise MarkingStateError("exact token identity requires a VersionRef")
    return json.dumps(
        {
            "entity_type": ref.entity_type,
            "logical_id": str(ref.entity_id),
            "version_id": str(ref.version_id),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _nonnegative_integer(value: object, path: str) -> int:
    """Validate one non-negative structural bound."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MarkingStateError(
            f"{path} must be a non-negative integer, got {value!r}")
    return value


def _canonical_marking_material(value: object) -> object:
    """Convert one typed authority closure to deterministic JSON material."""
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, ResourceVersionRef):
        return {
            "entity_type": "resource_version/v1",
            "logical_id": str(value.resource_id),
            "version_id": str(value.resource_version_id),
        }
    if isinstance(value, VersionRef):
        return {
            "entity_type": value.entity_type,
            "logical_id": str(value.entity_id),
            "version_id": str(value.version_id),
        }
    if isinstance(value, TypedId):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_marking_material(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_marking_material(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_marking_material(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        )
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_marking_material(getattr(value, field.name))
            for field in fields(value)
        }
    raise MarkingStateError(
        f"proposal material contains unsupported {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class BoundInputToken:
    """One exact non-fungible token selected by a timed binding."""

    place: str
    token_id: int
    token_ref: VersionRef

    @property
    def exact_identity(self) -> str:
        return exact_token_identity(self.token_ref)


@dataclass(frozen=True, slots=True)
class ResourceDemand:
    """Fungible input demand; exact capacity tokens are selected only at START."""

    place: str
    count: int


@dataclass(frozen=True, slots=True)
class TimedWaitGuardState:
    """Checkpoint-local expiry state for one declared timed wait guard."""

    transition_id: str
    expired: bool


@dataclass(frozen=True, slots=True)
class TimedBinding:
    """One complete all-or-nothing currently enabled input combination."""

    transition_id: str
    claim_epoch: int
    inputs: tuple[BoundInputToken, ...]
    resource_demands: tuple[ResourceDemand, ...]
    canonical_key: str

    @property
    def token_refs(self) -> tuple[VersionRef, ...]:
        return tuple(item.token_ref for item in self.inputs)

@dataclass(frozen=True, slots=True)
class BindingQueueOverflow:
    """Explicit fail-closed signal; bindings are never truncated."""

    queue_bound: int
    binding_count: int


@dataclass(frozen=True, slots=True)
class BindingEnumeration:
    """The exhaustive deterministic binding set and optional overflow signal."""

    transition_id: str
    bindings: tuple[TimedBinding, ...]
    overflow: Optional[BindingQueueOverflow] = None

    @property
    def fail_closed(self) -> bool:
        return self.overflow is not None


@dataclass(frozen=True, slots=True)
class ActiveFiringClaim:
    """Exact token claim held under one Registry-compatible firing identity."""

    firing_id: str
    transition_id: str
    binding_key: str
    claim_epoch: int
    inputs: tuple[BoundInputToken, ...]
    resource_tokens: tuple[BoundInputToken, ...]
    resource_demands: tuple[ResourceDemand, ...]

    @property
    def claimed_token_refs(self) -> tuple[VersionRef, ...]:
        return tuple(
            item.token_ref for item in (*self.inputs, *self.resource_tokens))


def _canonical_timed_claim_rows(
    claims: Sequence[ActiveFiringClaim],
) -> list[dict[str, object]]:
    """Serialize claim authority independently of binding/place tuple order."""
    return [{
        "binding_key": claim.binding_key,
        "claim_epoch": claim.claim_epoch,
        "firing_id": claim.firing_id,
        "input_ids": sorted(item.exact_identity for item in claim.inputs),
        "resource_claims": [{
            "count": demand.count,
            "place": demand.place,
            "resource_ids": sorted(
                item.exact_identity
                for item in claim.resource_tokens
                if item.place == demand.place),
        } for demand in sorted(
            claim.resource_demands, key=lambda item: item.place)],
        "transition_id": claim.transition_id,
    } for claim in sorted(claims, key=lambda item: item.firing_id)]


@dataclass(frozen=True, slots=True)
class TimedMarkingCheckpointAuthority:
    """Typed marking checkpoint plus its exact active timed-claim closure."""

    marking_authority: object
    epoch: int
    claims: tuple[ActiveFiringClaim, ...]


@dataclass(frozen=True, slots=True)
class TimedTerminalOutput:
    """One immutable, exact output spec used by terminal proposal preparation."""

    place: str
    resource_ref: Optional[ResourceVersionRef]
    work_resource_ref: Optional[ResourceVersionRef]
    kind: Optional[str]
    verdict: Optional[bool | str]
    continuation_round: Optional[int]
    continuation_source_ref: Optional[ResourceVersionRef]
    output_port_id: Optional[str]
    output_place_ref: Optional[VersionRef]
    output_binding_ref: Optional[VersionRef]

    def _deposit_spec(self) -> dict:
        continuation = (
            None if self.continuation_round is None else {
                "round": self.continuation_round,
                "source_ref": self.continuation_source_ref,
            }
        )
        return {
            "place": self.place,
            "resource_ref": self.resource_ref,
            "work_resource_ref": self.work_resource_ref,
            "kind": self.kind,
            "verdict": self.verdict,
            "continuation": continuation,
            "output_port_id": self.output_port_id,
            "output_place_ref": self.output_place_ref,
            "output_binding_ref": self.output_binding_ref,
        }


@dataclass(frozen=True, slots=True)
class TimedMarkingDeltaMaterial:
    """Canonical marking-side material Registry binds to its exact delta ref."""

    base_checkpoint_ref: VersionRef
    logical_firing_id: str
    transition_id: str
    consumed_input_token_refs: tuple[VersionRef, ...]
    retained_input_token_refs: tuple[VersionRef, ...]
    released_resource_token_refs: tuple[VersionRef, ...]
    deposited_token_refs: tuple[VersionRef, ...]
    successor_token_refs: tuple[VersionRef, ...]
    retain_logical_claim_for_retry: bool


@dataclass(frozen=True, slots=True)
class TimedTerminalProposal:
    """Immutable marking successor proposed to one atomic Registry settlement."""

    schema: str
    base_marking_authority: object
    base_checkpoint_ref: VersionRef
    base_local_state: Mapping[str, object]
    base_claims: tuple[ActiveFiringClaim, ...]
    logical_firing_id: str
    transition_id: str
    binding_key: str
    active_claim: ActiveFiringClaim
    terminal_status: str
    terminal_outputs: tuple[TimedTerminalOutput, ...]
    terminal_output_resource_refs: tuple[ResourceVersionRef, ...]
    retain_logical_claim_for_retry: bool
    released_resource_token_refs: tuple[VersionRef, ...]
    successor_snapshot: object
    marking_delta_material: TimedMarkingDeltaMaterial
    successor_claims: tuple[ActiveFiringClaim, ...]
    successor_local_state: Mapping[str, object]


@dataclass(frozen=True)
class RemarkResult:
    """Outcome of one mechanical marking re-fire transaction."""

    resolved: bool
    faulted_place: str
    producer: Optional[str]
    new_epoch: Optional[int]
    invalidated: int
    remarked: int
    closure: tuple[str, ...]
    whole_net: bool
    rehydrated: int = 0
    notes: tuple[str, ...] = ()

    @property
    def re_enabled(self) -> bool:
        return self.resolved and self.remarked > 0


@dataclass(frozen=True)
class SelectedRouteEpochEffectResult:
    """One declarative selected-route epoch effect over a target antichain."""

    requested_places: tuple[str, ...]
    root_places: tuple[str, ...]
    producers: tuple[str, ...]
    new_epoch: int
    invalidated: int
    remarked: int
    rehydrated: int
    closure: tuple[str, ...]


def _verdict_color_matches(
    verdict: bool | str | None, expected: bool | str,
) -> bool:
    """The verdict-guard COLOUR match, the SINGLE mirror shared by the enabling-time evaluator
    (:meth:`TeamNetMarking.verdict_guard_enabled`) and the reservation-time colour filter
    (:meth:`TeamNetMarking._try_reserve`), so consume can NEVER disagree with the enabling rule the
    net declares. A STRING ``expected`` (the §39.2 A2C 4-way decision color) matches by ``==``
    (interning-safe) and only against a string ``verdict``; any other ``expected`` (the pre-§39
    confirm/reject bool) matches by ``is`` identity — so a ``None`` / unstamped verdict matches
    NEITHER polarity (the tri-state of ``_is_reviewer_confirm`` / ``_is_reviewer_reject``). Content-
    blind (§34.0): it reads the reserved token COLOR set by the exact-key reserved read at stamp
    time, never a substring/keyword scan."""
    if isinstance(expected, str):
        return isinstance(verdict, str) and verdict == expected
    return verdict is expected


def continuation_from_tokens(tokens: list["Token"]) -> Optional[dict]:
    """Return the one unambiguous continuation indicator in ``tokens``.

    Continuation is structural token colour, not a merge policy: a transition that
    consumes several incompatible continuation colours has no declared way to pick
    one, so this returns ``None`` rather than silently selecting a predecessor.
    """
    values: list[dict] = []
    for index, tok in enumerate(tokens):
        value = tok.continuation
        if value is None:
            continue
        if not isinstance(value, dict) or set(value) != {"round", "source_ref"}:
            raise MarkingResourceRefError(
                f"tokens[{index}].continuation is not a closed continuation object")
        round_ = value["round"]
        source_ref = value["source_ref"]
        if (not isinstance(round_, int) or isinstance(round_, bool) or round_ < 0
                or not isinstance(source_ref, ResourceVersionRef)):
            raise MarkingResourceRefError(
                f"tokens[{index}].continuation requires a non-negative round and exact "
                "ResourceVersionRef")
        normalized = {"round": round_, "source_ref": source_ref}
        if normalized not in values:
            values.append(normalized)
    return dict(values[0]) if len(values) == 1 else None


def next_continuation(
    tokens: list["Token"], source_ref: Optional[ResourceVersionRef]
) -> Optional[dict]:
    """Build the next loop-back indicator from one exact resource version."""
    if source_ref is None:
        return None
    if not isinstance(source_ref, ResourceVersionRef):
        raise MarkingResourceRefError(
            "continuation source_ref must be an exact ResourceVersionRef")
    prior = continuation_from_tokens(tokens)
    return {"round": (int(prior["round"]) + 1) if prior is not None else 1,
            "source_ref": source_ref}


@dataclass
class Token:
    """One ledger token = one Petri occurrence in the current marking.

    Keyed ``{place, resource_ref, producer, consumer, epoch}`` (§3.1bis).
    A semantic token names its immutable bytes only through ``resource_ref``; the
    ledger tracks only exact identity and freshness (``epoch`` + ``consumed_by``).

    * ``consumer`` — an explicit compatibility/control address, or ``None`` for a
      SHARED / contested token. Produced output occurrences are shared; an address is
      retained only for narrow mechanisms such as private source ignition and targeted
      re-marking. An addressed token is consumable only by its named transition. A
      shared token is consumable by any reader and the first firing consumes it.
    * ``consumed_by`` — ``None`` while the token is fresh/unconsumed; the id of the
      transition that consumed it once spent (consume-on-fire).
    * ``kind`` — the token "color" / class = the universal artifact-``kind``
      (``KNOWN_COMM_KINDS``), explicitly NOT the role label (fix 15).
    """
    token_id: int
    place: str
    epoch: int
    # Registry persistence locator for this immutable token fact. The token itself
    # is the Petri logical identity and token_id remains its intrinsic ordinal.
    # ``None`` is permitted only while assembling a newly produced fact.
    token_ref: Optional[VersionRef] = None
    producer: Optional[str] = None
    consumer: Optional[str] = None
    # None is reserved for content-less Petri control tokens (ignition, capacity,
    # counters, and verdict routing).
    resource_ref: Optional[ResourceVersionRef] = None
    # Exact immutable work item a review resource judged. This is distinct from
    # ``resource_ref`` when the current token carries the review result itself.
    work_resource_ref: Optional[ResourceVersionRef] = None
    kind: Optional[str] = None
    consumed_by: Optional[str] = None
    # §34.29 accept_override — a structural warning carried by a token when an escalation
    # reviewer chooses to force-accept a review gate. A dict
    # ``{reason, gate=(reviewer,producer), overridden_attempt, escalation_provenance,
    # gate_output_place}`` — the framework CARRIES it (a structural fact) and SURFACES it to
    # downstream reviewers; it judges no content (§34.0). Default ``None`` → every ordinary
    # token is byte-identical.
    override_warning: Optional[dict] = None
    # §36 P0 verdict-guard family (design §2.2/§8) — the RESERVED VERDICT COLOR of a
    # p_verdict token: the reviewer's reserved ``confirmed`` boolean, stamped onto the token
    # by the executor (Phase 3, read via the SAME exact-key ``_unwrap_declared_envelope``
    # reserved-channel reader used by ``_is_reviewer_reject`` / ``_is_reviewer_confirm``), so
    # the marking's enabling rule can route the explicit ``T_pass`` / ``T_reject`` free-choice
    # on it at ENABLING time (:meth:`verdict_guard_enabled`). It is a structural token COLOR
    # (like ``kind``), NOT artifact content — the guard routes on the boolean's
    # truthiness and judges no meaning (§34.0). ``True`` = confirm, ``False`` = reject, ``None``
    # = no verdict stamped. Default ``None`` → every ordinary token is byte-identical. §40.12 R21 —
    # it IS in ``_TOKEN_FIELDS`` (persisted), so a cross-process resume keeps a coloured token's
    # routing color instead of silently dropping it; exact recovery requires the field even when its
    # value is ``None``. LIVE in the explicit-review Petri subnet: the executor stamps this color on
    # a p_verdict token and ``_is_enabled_locked`` / ``verdict_guard_enabled`` read it; flag-OFF no
    # net declares a verdict guard, so nothing stamps or reads it (byte-identical).
    #
    # §39.2 (v2.53) — the A2C SINGLE-NODE decision place widens this COLOR from the boolean
    # confirm/reject verdict to the 4-way closed enum ``{"pass", "continue", "escalate",
    # "give_up"}`` (a string), so the ONE critic's decision routes the ``T_pass`` / ``T_continue``
    # / ``T_escalate`` / ``T_give_up`` free-choice off a single colored decision token. The legacy
    # boolean path is UNTOUCHED (``verdict_guard_enabled`` keeps ``is`` identity for a bool
    # ``expected`` and uses ``==`` only for a string ``expected``), so every pre-§39 verdict net is
    # byte-identical. Still a structural token COLOR (like ``kind`` / ``faulted``), NOT artifact
    # content — routed on the reserved enum, judged nowhere (§34.0).
    verdict: Optional[bool | str] = None
    # §41.4 (LOCKED) — the CONTINUATION indicator carried by a WORK token that RE-ENTERS a shadow
    # (the A2C ``continue`` re-fire; the finalization ``continue``-cascade block re-entry). A small
    # STRUCTURAL dict ``{"round": <int>, "source_ref": <ResourceVersionRef>}`` — the round index +
    # an exact reference to the returned document / feedback resource (NEVER content, §34.0). It tells a
    # re-fired shadow "this is a continuation — edit the prior product / adjudicate the returned
    # document", NOT "start fresh". A structural token annotation (like ``verdict`` / ``kind`` /
    # ``faulted``), read/carried by the framework and surfaced to the re-fired shadow; it judges no
    # meaning. PERSISTED in ``_TOKEN_FIELDS`` + the resume path; exact recovery requires the key
    # with value ``None`` for an ordinary non-continuation token. Default ``None`` keeps every
    # ordinary live token byte-identical before serialization.
    continuation: Optional[dict] = None
    # §44.4c — Scheme-B coloured lease identity on a token held by the single
    # ``resource_lease`` pool.  ``resource_ref`` is that identity's current
    # immutable resource version (nullable only for an unpublished logical slot).
    lease_identity_ref: Optional[VersionRef] = None
    # Closed variable-arc inscription carried by the ordinary claim token.  The
    # tuple is structural colour; Registry records it but never interprets bytes.
    lease_claims: tuple[dict, ...] = ()

    @property
    def shared(self) -> bool:
        """A SHARED / contested token (no addressed consumer) — CHOICE-eligible."""
        return self.consumer is None


@dataclass(frozen=True, slots=True)
class _ActiveClaim:
    """One process-local consume-on-fire batch; never recovery authority."""

    claim_id: int
    transition_id: str
    epoch: int
    token_ids: tuple[int, ...]
    token_refs: tuple[VersionRef, ...]
    reference_token_ids: tuple[int, ...] = ()
    reference_token_refs: tuple[VersionRef, ...] = ()
    lease_accesses: tuple[tuple[VersionRef, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _PendingClaim:
    """One pre-mutation reservation and its marking-owned consume projection."""

    token_ids: tuple[int, ...]
    consumed_place_counts: Mapping[str, int]
    reference_token_ids: tuple[int, ...] = ()
    lease_accesses: tuple[tuple[VersionRef, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FiringClaimOccurrence:
    """One exact active occurrence; transition ids are never claim keys."""

    local_key: int | VersionRef
    transition_id: str
    claim_epoch: int
    token_refs: tuple[VersionRef, ...]


@dataclass(frozen=True, slots=True)
class PetriFiringResourceAccess:
    """One firing-occurrence-local resource arc derived by the Harness.

    The component selects only an exact immutable resource version and an
    access mode.  The Harness resolves the matching live lease-pool token and
    derives the arc inscription.  A read is a non-consuming input arc; an edit
    is an exact borrow/return pair whose input token stays claimed until the
    firing settles.
    """

    firing_ref: VersionRef
    transition_id: str
    claim_epoch: int
    lease_pool_place: str
    resource_token_ref: VersionRef
    lease_identity_ref: VersionRef
    resource_ref: ResourceVersionRef
    access_mode: str
    input_arc_mode: str
    output_arc_mode: str | None

    def __post_init__(self) -> None:
        if (not isinstance(self.firing_ref, VersionRef)
                or self.firing_ref.entity_type not in {
                    "transition_firing/v1", "transition_firing/v2"}
                or not isinstance(self.transition_id, str)
                or not self.transition_id
                or isinstance(self.claim_epoch, bool)
                or not isinstance(self.claim_epoch, int)
                or self.claim_epoch < 0
                or not isinstance(self.lease_pool_place, str)
                or not self.lease_pool_place
                or not isinstance(self.resource_token_ref, VersionRef)
                or self.resource_token_ref.entity_type != "petri_token/v1"
                or not isinstance(self.lease_identity_ref, VersionRef)
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or self.access_mode not in {"read", "edit"}
                or (self.access_mode == "read" and (
                    self.input_arc_mode != "read"
                    or self.output_arc_mode is not None))
                or (self.access_mode == "edit" and (
                    self.input_arc_mode != "borrow"
                    or self.output_arc_mode != "return"))):
            raise TypeError("Petri firing resource access is incomplete")

    @property
    def return_arc_required(self) -> bool:
        return self.output_arc_mode == "return"


@dataclass(frozen=True, slots=True)
class FiringAllocationEvidence:
    """Actual randomized eligible queue and exact allocated occurrences."""

    claim_epoch: int
    eligible_queue: tuple[tuple[str, tuple[VersionRef, ...]], ...]
    allocations: tuple[FiringClaimOccurrence, ...]
    scheduler_seed: int | None = None


class TeamNetMarking:
    """The executor-owned dynamic marking of one loaded :class:`TeamNet`.

    Owns the token list, the provenance epoch, and its OWN lock. NEVER touches the
    Registry-v1 (which keeps immutable resource/provenance evidence). One marking per net
    per immutable plan version; a re-declared net gets a fresh marking (§3.4).
    """

    # The id stamped on a token's ``consumed_by`` when a re-marking transaction
    # invalidates it (vs a normal firing's consume). Observability only — both
    # render a token non-fresh; a re-keyed analyst can tell them apart.
    INVALIDATED = "__invalidated__"

    _DISABLED_TIMED_ENTRY_PREFIXES = (
        "active_timed",
        "begin_timed",
        "install_timed",
        "propose_timed",
        "restore_timed",
        "settle_timed",
        "start_timed",
        "timed_claim",
        "runtime_timed",
    )
    _TIMED_DATA_EXEMPTIONS = frozenset({
        "_timed_active_claims",
        "_timed_wait_guard_state_map",
    })

    def __getattribute__(self, name: str) -> object:
        """Physically isolate the historical timed/hash scheduler API.

        This Harness has one synchronous Petri firing path. Public entrypoints
        of the retained historical timed/hash scheduler cannot be resolved, so
        that second runtime cannot compete with the formal marking authority.
        """
        disabled = bool(isinstance(name, str) and (
            name.startswith(
                TeamNetMarking._DISABLED_TIMED_ENTRY_PREFIXES)
            or (name.startswith("_timed_")
                and name not in
                TeamNetMarking._TIMED_DATA_EXEMPTIONS)))
        if disabled:
            raise MarkingStateError(
                "historical timed/hash scheduler is unsupported by this Harness")
        return object.__getattribute__(self, name)

    def __init__(self, net: "MarkingNetStructure") -> None:
        self._net = net
        # The ledger's OWN lock (re-entrant: claim_firing_set calls enabled-queries
        # that also take the lock). Distinct from the Registry event-store append
        # lock, which never serializes Petri token-state mutations.
        self._lock: threading.RLock = threading.RLock()
        self._tokens: list[Token] = []
        self._epoch: int = 0
        self._next_id: int = 0
        # §34.20 G1 — the PER-TRANSITION attempt counter ``k`` (a TOP-LEVEL marking field,
        # NOT a per-token field). ``k`` is a content-blind monotonic attempt index per
        # transition, issued once per firing so each round registers its product under a
        # DISTINCT round-stamped lineage key (round N is never overwritten by round N+1).
        # Registered in the typed marking checkpoint so a FRESH-PROCESS resume continues ``k``
        # PAST the highest retained attempt (no collision that would overwrite a retained round).
        # Empty for a clean run that never fired. Substrate-only (§34.0):
        # it counts firings; it judges no content.
        self._attempts: dict[str, int] = {}
        # Current in-process claims prevent a later firing of the same transition
        # in the same epoch from rediscovering every historical consumed token.
        # This map is deliberately absent from typed snapshots/from_authority:
        # Registry's active firing authority is the recovery SSOT.
        # A claim starts under its monotonic process-local integer id and is
        # re-keyed to the exact Registry firing ref immediately after admission.
        # Transition identity is retained only as claim data; it is never the
        # ledger key.
        self._active_claims: dict[int | VersionRef, _ActiveClaim] = {}
        self._next_claim_id: int = 0
        self._last_firing_allocation = FiringAllocationEvidence(0, (), ())
        self._published_firing_allocation: tuple[
            FiringAllocationEvidence, object] | None = None
        # Timed starts are keyed by immutable firing identity, permitting several
        # concurrent starts of one transition while still preventing token overlap.
        self._timed_active_claims: dict[str, ActiveFiringClaim] = {}
        # §40.11 Stage-1 M1 — the count of guarded output arcs SUPPRESSED (deposited nothing)
        # by the last ``deposit_outputs`` call, whose consumed decision color did NOT match the
        # arc's declared ``expected`` (or was undetermined). Read-only TELEMETRY on the marking,
        # NOT a widened return: ``deposit_outputs`` still returns the ``(deposited, off_arc)``
        # 2-tuple (so every existing 2-tuple caller / test is byte-identical). Reset to 0 at the
        # top of every ``deposit_outputs`` call, incremented ONLY inside the guard gate. For every
        # net that declares no output guard it stays 0 (the gate is never entered).
        self._guard_suppressed_last: int = 0

    # ── observability / read accessors ───────────────────────────────────────

    @property
    def lock(self) -> threading.RLock:
        """The ledger's OWN lock (re-marking + claim hold this, NOT the registry's)."""
        return self._lock

    @property
    def epoch(self) -> int:
        """The CURRENT provenance epoch. A token is fresh only at this epoch."""
        with self._lock:
            return self._epoch

    @property
    def net(self) -> "MarkingNetStructure":
        return self._net

    def _local_token_view(self) -> tuple[int, tuple[Token, ...]]:
        from ._marking import token_state as _token_state
        return _token_state._local_token_view(self)

    def _new_token(self, **kw) -> Token:
        from ._marking import token_state as _token_state
        return _token_state._new_token(self, **kw)

    def _require_concrete_place_colour(self, place: object, colour: object, *, path: str) -> None:
        from ._marking import token_state as _token_state
        return _token_state._require_concrete_place_colour(self, place, colour, path=path)

    def _fresh_on(self, place: str, t_id: Optional[str], *, require_token_ref: bool=True) -> list[Token]:
        from ._marking import token_state as _token_state
        return _token_state._fresh_on(self, place, t_id, require_token_ref=require_token_ref)

    def is_marked(self, place: str) -> bool:
        from ._marking import selection as _selection
        return _selection.is_marked(self, place)

    def fresh_count(self, place: str, consumer: Optional[str]=None) -> int:
        from ._marking import selection as _selection
        return _selection.fresh_count(self, place, consumer)

    def _timed_wait_guard_state_map(self, timed_wait_guard_states: Sequence[TimedWaitGuardState]) -> dict[str, TimedWaitGuardState]:
        from ._marking import selection as _selection
        return _selection._timed_wait_guard_state_map(self, timed_wait_guard_states)

    def is_enabled(self, t_id: str, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> bool:
        from ._marking import selection as _selection
        return _selection.is_enabled(self, t_id, timed_wait_guard_states=timed_wait_guard_states)


    def _claim_input_arcs(self, t_id: str) -> tuple[tuple[str, str, int], ...]:
        from ._marking import selection as _selection
        return _selection._claim_input_arcs(self, t_id)

    def _is_enabled_locked(self, t_id: str, timed_wait_guard_states: Sequence[TimedWaitGuardState]=(), *, _timed_wait_guard_state_map: Optional[Mapping[str, TimedWaitGuardState]]=None) -> bool:
        from ._marking import selection as _selection
        return _selection._is_enabled_locked(self, t_id, timed_wait_guard_states, _timed_wait_guard_state_map=_timed_wait_guard_state_map)

    @staticmethod
    def _version_ref_from_exact(value: object) -> VersionRef:
        from ._marking import selection as _selection
        return _selection._version_ref_from_exact(value)

    @staticmethod
    def _resource_ref_from_exact(value: object | None) -> Optional[ResourceVersionRef]:
        from ._marking import selection as _selection
        return _selection._resource_ref_from_exact(value)

    def _declared_lease_claims_for_place(self, place: str) -> tuple[dict, ...]:
        from ._marking import selection as _selection
        return _selection._declared_lease_claims_for_place(self, place)

    def _declared_output_lease_claims(self, transition_id: str, output_place: str, operation_tokens: Sequence[Token]) -> tuple[dict, ...]:
        from ._marking import selection as _selection
        return _selection._declared_output_lease_claims(self, transition_id, output_place, operation_tokens)

    def _declared_output_lease_exclusions(self, transition_id: str, output_place: str) -> frozenset[VersionRef]:
        from ._marking import selection as _selection
        return _selection._declared_output_lease_exclusions(self, transition_id, output_place)

    def _formal_output_lease_claims(self, transition_id: str, output_place: str, operation_tokens: Sequence[Token], produced: Sequence[dict], supplied: Sequence[dict]) -> tuple[dict, ...]:
        from ._marking import selection as _selection
        return _selection._formal_output_lease_claims(self, transition_id, output_place, operation_tokens, produced, supplied)

    @staticmethod
    def _merge_lease_claims(*groups: Sequence[dict]) -> tuple[dict, ...]:
        from ._marking import selection as _selection
        return _selection._merge_lease_claims(*groups)

    def _variable_lease_token_ids(self, t_id: str, ordinary_ids: Sequence[int], reserved: set[int], *, allowed_token_ids: Optional[set[int]]=None) -> Optional[tuple[list[int], list[int]]]:
        from ._marking import selection as _selection
        return _selection._variable_lease_token_ids(self, t_id, ordinary_ids, reserved, allowed_token_ids=allowed_token_ids)

    @staticmethod
    def _lease_accesses_compatible(left: Sequence[tuple[VersionRef, str]], right: Sequence[tuple[VersionRef, str]]) -> bool:
        from ._marking import selection as _selection
        return _selection._lease_accesses_compatible(left, right)

    def enabled_transitions(self, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> list[str]:
        from ._marking import selection as _selection
        return _selection.enabled_transitions(self, timed_wait_guard_states=timed_wait_guard_states)

    # ── §36 — the COUNTER-PLACE RESET + VERDICT-GUARD family (live in the explicit-review Petri subnet) ──

    def clear_counter_place(self, place: str) -> int:
        from ._marking import token_state as _token_state
        return _token_state.clear_counter_place(self, place)

    def deposit_count_token(self, place: str) -> None:
        from ._marking import token_state as _token_state
        return _token_state.deposit_count_token(self, place)

    def consume_place_tokens(self, place: str) -> int:
        from ._marking import token_state as _token_state
        return _token_state.consume_place_tokens(self, place)

    def verdict_guard_enabled(self, place: str, expected: bool | str, t_id: Optional[str]=None) -> bool:
        from ._marking import selection as _selection
        return _selection.verdict_guard_enabled(self, place, expected, t_id)

    # ── §34.20 G1 — the PER-TRANSITION attempt counter (TOP-LEVEL marking field) ──
    #
    # ``k`` is a content-blind monotonic attempt index PER TRANSITION (NOT a per-token
    # field). The executor issues one ``k`` per firing so each round registers its product
    # under a distinct round-stamped lineage key (round N never overwritten by N+1). It is
    # registered as a typed checkpoint counter (alongside epoch / next_token_id) so a
    # fresh-process resume continues ``k`` past the highest retained attempt. Atomic under the
    # ledger lock.

    def attempt(self, t_id: str) -> int:
        from ._marking import token_state as _token_state
        return _token_state.attempt(self, t_id)

    def next_attempt(self, t_id: str) -> int:
        from ._marking import token_state as _token_state
        return _token_state.next_attempt(self, t_id)

    def record_settled_attempt(self, transition_id: str, attempt: int) -> int:
        from ._marking import token_state as _token_state
        return _token_state.record_settled_attempt(self, transition_id, attempt)

    def reconcile_attempt(self, t_id: str, highest_retained: int) -> int:
        from ._marking import token_state as _token_state
        return _token_state.reconcile_attempt(self, t_id, highest_retained)

    def marking_snapshot(self) -> dict:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints.marking_snapshot(self)

    # ── §34.17-§6.2 — private structural normalization for typed Registry closure ──
    #
    # Registry owns the immutable checkpoint and one independently versioned record per token.
    # This section retains only the closed local schema validator used while translating between
    # those typed DTOs and the in-process ledger. No mapping is public recovery authority.

    # The Token DATA fields normalized (no live handle — a Token is plain data; the
    # ``shared`` property is derived from ``consumer`` and is NOT recorded). §40.12 R21 —
    # ``verdict`` (the reserved decision COLOR) is persisted too, so a cross-process resume
    # keeps a coloured token's routing color. ``override_warning`` is part of the token state:
    # omitting it would silently erase the mandatory warning on an accepted override. Recovery
    # is greenfield-strict: every listed field is required; there is no legacy defaulting.
    _TOKEN_FIELDS = (
        "token_ref", "token_id", "place", "epoch", "producer", "consumer",
        "resource_ref", "work_resource_ref", "kind", "consumed_by",
        "override_warning", "verdict", "continuation",
        "lease_identity_ref", "lease_claims")

    _STATE_FIELDS = frozenset({"epoch", "next_id", "attempts", "tokens"})

    @staticmethod
    def _state_int(value: object, path: str, *, minimum: int=0) -> int:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._state_int(value, path, minimum=minimum)

    @staticmethod
    def _optional_string(value: object, path: str) -> Optional[str]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._optional_string(value, path)

    @staticmethod
    def _token_ref_payload(value: Optional[VersionRef], path: str) -> Optional[dict[str, str]]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._token_ref_payload(value, path)

    @classmethod
    def _token_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[VersionRef]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._token_ref_state(cls, value, path, required=required)

    @staticmethod
    def _resource_ref_payload(value: Optional[ResourceVersionRef], path: str) -> Optional[dict[str, str]]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._resource_ref_payload(value, path)

    @classmethod
    def _resource_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[ResourceVersionRef]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._resource_ref_state(cls, value, path, required=required)

    @staticmethod
    def _lease_identity_ref_payload(value: Optional[VersionRef], path: str) -> Optional[dict[str, str]]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._lease_identity_ref_payload(value, path)

    @classmethod
    def _lease_identity_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[VersionRef]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._lease_identity_ref_state(cls, value, path, required=required)

    @classmethod
    def _lease_claims_state(cls, value: object, path: str) -> tuple[dict, ...]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._lease_claims_state(cls, value, path)

    @classmethod
    def _runtime_lease_claims(cls, value: object, path: str) -> tuple[dict, ...]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._runtime_lease_claims(cls, value, path)

    @classmethod
    def _continuation_state(cls, value: object, path: str) -> Optional[dict]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._continuation_state(cls, value, path)

    @staticmethod
    def _runtime_continuation(value: object, path: str) -> Optional[dict]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._runtime_continuation(value, path)

    @classmethod
    def _validate_runtime_token_refs(cls, tokens: list[Token], path: str, *, require_token_ref: bool=False) -> None:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._validate_runtime_token_refs(cls, tokens, path, require_token_ref=require_token_ref)

    def _token_payload(self, token: Token) -> dict:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._token_payload(self, token)

    def _override_warning_state(self, value: object, path: str, *, place: str) -> Optional[dict]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._override_warning_state(self, value, path, place=place)

    def _validated_state(self, data: object) -> tuple[int, int, dict[str, int], list[Token]]:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._validated_state(self, data)

    def _validated_candidate_mapping(self) -> dict:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._validated_candidate_mapping(self)

    def _restore_candidate_mapping(self, data: object) -> int:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._restore_candidate_mapping(self, data)

    def carry_to(self, net: 'MarkingNetStructure') -> 'TeamNetMarking':
        from ._marking import checkpoints as _checkpoints
        return _checkpoints.carry_to(self, net)

    @classmethod
    def from_authority(cls, net: 'MarkingNetStructure', authority: TypedMarkingAuthority) -> 'TeamNetMarking':
        from ._marking import checkpoints as _checkpoints
        return _checkpoints.from_authority(cls, net, authority)

    def typed_snapshot(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator) -> TypedMarkingSnapshot:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints.typed_snapshot(self, executable, registry)

    def pure_typed_snapshot(self, executable: 'ExecutableNetAuthority') -> 'TypedMarkingSnapshot':
        from ._marking import checkpoints as _checkpoints
        return _checkpoints.pure_typed_snapshot(self, executable)

    def _typed_snapshot_with_active_timed_claims(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator) -> TypedMarkingSnapshot:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._typed_snapshot_with_active_timed_claims(self, executable, registry)

    def _adopt_snapshot_token_refs(self, snapshot: object) -> None:
        from ._marking import checkpoints as _checkpoints
        return _checkpoints._adopt_snapshot_token_refs(self, snapshot)

    # ── seeding M₀ ────────────────────────────────────────────────────────────

    def seed_initial(self, initial_marking: Optional[dict]=None) -> int:
        from ._marking import token_state as _token_state
        return _token_state.seed_initial(self, initial_marking)
    def deposit_resource_token(self, place: str, resource_ref: ResourceVersionRef, kind: Optional[str]=None) -> int:
        from ._marking import token_state as _token_state
        return _token_state.deposit_resource_token(self, place, resource_ref, kind)

    # ── timed exhaustive bindings and firing-keyed claims ────────────────────

    @staticmethod
    def _bound_input(token: Token) -> BoundInputToken:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._bound_input(token)

    @staticmethod
    def _make_timed_binding(transition_id: str, claim_epoch: int, tokens: tuple[Token, ...], resource_demands: tuple[ResourceDemand, ...]) -> TimedBinding:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._make_timed_binding(transition_id, claim_epoch, tokens, resource_demands)

    def _enumerate_timed_bindings_locked(self, transition_id: str) -> tuple[TimedBinding, ...]:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._enumerate_timed_bindings_locked(self, transition_id)

    def _timed_binding_count_locked(self, transition_id: str) -> int:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._timed_binding_count_locked(self, transition_id)

    def enumerate_timed_bindings(self, transition_id: str, *, queue_bound: Optional[int]=None) -> BindingEnumeration:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.enumerate_timed_bindings(self, transition_id, queue_bound=queue_bound)

    @staticmethod
    def _canonical_firing_id(firing_id: object) -> str:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._canonical_firing_id(firing_id)

    def claim_timed_binding(self, binding: TimedBinding, *, firing_id: str, resource_claims: Optional[Mapping[str, Sequence[str]]]=None) -> ActiveFiringClaim:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.claim_timed_binding(self, binding, firing_id=firing_id, resource_claims=resource_claims)

    def active_timed_claim(self, firing_id: str) -> ActiveFiringClaim:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.active_timed_claim(self, firing_id)

    def active_timed_claims(self) -> tuple[ActiveFiringClaim, ...]:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.active_timed_claims(self)

    def runtime_resource_availability(self, transition_id: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.runtime_resource_availability(self, transition_id)

    def timed_claim_snapshot(self) -> dict:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.timed_claim_snapshot(self)

    def _local_state_locked(self) -> dict[str, object]:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._local_state_locked(self)

    def local_state(self) -> dict[str, object]:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.local_state(self)

    def timed_checkpoint_authority(self, marking_authority: object) -> TimedMarkingCheckpointAuthority:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.timed_checkpoint_authority(self, marking_authority)

    @classmethod
    def from_timed_checkpoint_authority(cls, net: 'MarkingNetStructure', authority: TimedMarkingCheckpointAuthority) -> 'TeamNetMarking':
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.from_timed_checkpoint_authority(cls, net, authority)

    def restore_timed_claim_snapshot(self, snapshot: object) -> None:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.restore_timed_claim_snapshot(self, snapshot)

    def release_timed_claim(self, firing_id: str) -> ActiveFiringClaim:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.release_timed_claim(self, firing_id)

    def release_timed_attempt_resources(self, logical_firing_id: str) -> ActiveFiringClaim:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.release_timed_attempt_resources(self, logical_firing_id)

    def reclaim_timed_retry_resources(self, logical_firing_id: str, *, transition_id: str, binding_key: str, resource_claims: Mapping[str, Sequence[str]]) -> ActiveFiringClaim:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.reclaim_timed_retry_resources(self, logical_firing_id, transition_id=transition_id, binding_key=binding_key, resource_claims=resource_claims)

    @staticmethod
    def _timed_claim_snapshot_for(epoch: int, claims: tuple[ActiveFiringClaim, ...]) -> dict:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._timed_claim_snapshot_for(epoch, claims)

    @staticmethod
    def _terminal_output_from_validated(spec: Mapping[str, Any]) -> TimedTerminalOutput:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._terminal_output_from_validated(spec)

    def _deposit_timed_terminal_outputs(self, claim: ActiveFiringClaim, outputs: tuple[TimedTerminalOutput, ...]) -> None:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed._deposit_timed_terminal_outputs(self, claim, outputs)


    def prepare_timed_terminal_proposal(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator, base_marking_authority: TypedMarkingAuthority, *, logical_firing_id: str, terminal_status: str, produced: Optional[Sequence[Mapping[str, Any]]]=None, retain_logical_claim_for_retry: bool=False) -> TimedTerminalProposal:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.prepare_timed_terminal_proposal(self, executable, registry, base_marking_authority, logical_firing_id=logical_firing_id, terminal_status=terminal_status, produced=produced, retain_logical_claim_for_retry=retain_logical_claim_for_retry)

    def install_timed_terminal_successor(self, proposal: TimedTerminalProposal, receipt: object) -> TypedMarkingAuthority:
        from ._marking import legacy_timed as _legacy_timed
        return _legacy_timed.install_timed_terminal_successor(self, proposal, receipt)

    # ── the ATOMIC conflict-free claim (consume-on-fire, §3.2 step 0) ──────────

    @staticmethod
    def _require_exact_registered_claim_refs(transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> tuple[VersionRef, ...]:
        from ._marking import claims as _claims
        return _claims._require_exact_registered_claim_refs(transition_id, claimed_token_refs)

    def _install_exact_registered_claim_locked(self, transition_id: str, expected_refs: tuple[VersionRef, ...], *, verify_enabled: bool, allow_siblings: bool=False) -> int:
        from ._marking import claims as _claims
        return _claims._install_exact_registered_claim_locked(self, transition_id, expected_refs, verify_enabled=verify_enabled, allow_siblings=allow_siblings)

    def claim_exact_registered_firing(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
        from ._marking import claims as _claims
        return _claims.claim_exact_registered_firing(self, transition_id, claimed_token_refs)

    def install_registered_firing_claim(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
        from ._marking import claims as _claims
        return _claims.install_registered_firing_claim(self, transition_id, claimed_token_refs)

    def install_registered_active_firing_claim(self, transition_id: str, claimed_token_refs: tuple[VersionRef, ...]) -> int:
        from ._marking import claims as _claims
        return _claims.install_registered_active_firing_claim(self, transition_id, claimed_token_refs)

    def claim_firing_set(self, max_count: Optional[int]=None, allowed: Optional['set[str]']=None, *, timed_wait_guard_states: Sequence[TimedWaitGuardState]=()) -> tuple[list[str], int]:
        from ._marking import claims as _claims
        return _claims.claim_firing_set(self, max_count, allowed, timed_wait_guard_states=timed_wait_guard_states)

    def firing_allocation_evidence(self) -> FiringAllocationEvidence:
        from ._marking import claims as _claims
        return _claims.firing_allocation_evidence(self)

    def bind_firing_allocation_authority(self, evidence: FiringAllocationEvidence, authority: object) -> None:
        from ._marking import claims as _claims
        return _claims.bind_firing_allocation_authority(self, evidence, authority)

    def require_published_firing_occurrence(self, occurrence_key: int | VersionRef, transition_id: str, claim_epoch: int) -> object:
        from ._marking import claims as _claims
        return _claims.require_published_firing_occurrence(self, occurrence_key, transition_id, claim_epoch)

    def active_claim_occurrences(self, claim_epoch: int) -> tuple[FiringClaimOccurrence, ...]:
        from ._marking import claims as _claims
        return _claims.active_claim_occurrences(self, claim_epoch)

    def active_claim_key(self, t_id: str, claim_epoch: int) -> int | VersionRef:
        from ._marking import claims as _claims
        return _claims.active_claim_key(self, t_id, claim_epoch)

    def bind_active_claim(self, local_key: int | VersionRef, firing_ref: VersionRef) -> VersionRef:
        from ._marking import claims as _claims
        return _claims.bind_active_claim(self, local_key, firing_ref)

    def _active_claim(self, t_id: str, claim_epoch: int, instance_key: int | VersionRef | None=None) -> tuple[int | VersionRef, _ActiveClaim]:
        from ._marking import claims as _claims
        return _claims._active_claim(self, t_id, claim_epoch, instance_key)

    def derive_firing_resource_access(self, t_id: str, claim_epoch: int, firing_ref: VersionRef, requested: ResourceVersionRef, access_mode: str) -> PetriFiringResourceAccess:
        from ._marking import claims as _claims
        return _claims.derive_firing_resource_access(self, t_id, claim_epoch, firing_ref, requested, access_mode)

    def apply_firing_resource_access(self, access: PetriFiringResourceAccess) -> None:
        from ._marking import claims as _claims
        return _claims.apply_firing_resource_access(self, access)

    def _try_reserve(self, t_id: str, reserved: set[int], *, allowed_token_ids: Optional[set[int]]=None) -> Optional[_PendingClaim]:
        from ._marking import claims as _claims
        return _claims._try_reserve(self, t_id, reserved, allowed_token_ids=allowed_token_ids)

    def claimed_tokens(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> list[Token]:
        from ._marking import claims as _claims
        return _claims.claimed_tokens(self, t_id, claim_epoch, instance_key=instance_key)

    def claimed_token_refs(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> tuple[VersionRef, ...]:
        from ._marking import claims as _claims
        return _claims.claimed_token_refs(self, t_id, claim_epoch, instance_key=instance_key)

    def claimed_operation_tokens(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> list[Token]:
        from ._marking import claims as _claims
        return _claims.claimed_operation_tokens(self, t_id, claim_epoch, instance_key=instance_key)

    def verify_active_claim(self, t_id: str, claim_epoch: int, claimed_token_refs: tuple[VersionRef, ...], *, instance_key: int | VersionRef | None=None) -> None:
        from ._marking import claims as _claims
        return _claims.verify_active_claim(self, t_id, claim_epoch, claimed_token_refs, instance_key=instance_key)

    def clear_active_claim(self, t_id: str, claim_epoch: int, *, instance_key: int | VersionRef | None=None) -> None:
        from ._marking import claims as _claims
        return _claims.clear_active_claim(self, t_id, claim_epoch, instance_key=instance_key)

    def singleton_settlement_view(self, instance_key: VersionRef) -> 'TeamNetMarking':
        from ._marking import claims as _claims
        return _claims.singleton_settlement_view(self, instance_key)

    def verify_exclusive_active_claim(self, instance_key: VersionRef) -> None:
        from ._marking import claims as _claims
        return _claims.verify_exclusive_active_claim(self, instance_key)

    def carry_active_claims_to(self, successor: 'TeamNetMarking', *, settled_instance_key: VersionRef) -> None:
        from ._marking import claims as _claims
        return _claims.carry_active_claims_to(self, successor, settled_instance_key=settled_instance_key)

    def _validated_output_specs(self, produced: object, *, selected_route_places=frozenset()) -> list[dict]:
        from ._marking import outputs as _outputs
        return _outputs._validated_output_specs(self, produced, selected_route_places=selected_route_places)

    # ── PRODUCE — deposit fresh output tokens (§3.2 step 3) ────────────────────

    def _prevalidate_declared_output_colours(self, t_id: str, proposed: object, *, claim_epoch: int) -> object:
        from ._marking import outputs as _outputs
        return _outputs._prevalidate_declared_output_colours(self, t_id, proposed, claim_epoch=claim_epoch)


    def deposit_outputs(self, t_id: str, produced: list[dict], *, claim_epoch: int) -> tuple[int, int]:
        from ._marking import outputs as _outputs
        return _outputs.deposit_outputs(self, t_id, produced, claim_epoch=claim_epoch)

    # ── re-marking primitives (the ledger side of §5 / T3) ─────────────────────
    #
    # The full faulted-artifact → declared-producer → re-mark ROUTING is T3 (it
    # needs the reviewer/finalization veto + the declared place→producer map). T2
    # provides the LEDGER primitives a re-marking transaction is built from, so the
    # ``epoch`` key field is meaningful and exercised. All are atomic under the
    # ledger lock; none mutates Registry-v1.

    def bump_epoch(self, *, carry_forward_except: Optional[set[str]]=None) -> int:
        from ._marking import revisions as _revisions
        return _revisions.bump_epoch(self, carry_forward_except=carry_forward_except)

    @staticmethod
    def downstream_closure(net: 'MarkingNetStructure', faulted_place: str) -> set[str]:
        from ._marking import revisions as _revisions
        return _revisions.downstream_closure(net, faulted_place)

    def invalidate_places(self, places: set[str]) -> int:
        from ._marking import revisions as _revisions
        return _revisions.invalidate_places(self, places)

    def _resource_tokens_for_remark(self, place: str, t_id: str) -> list[Token]:
        from ._marking import revisions as _revisions
        return _revisions._resource_tokens_for_remark(self, place, t_id)

    def remark_inputs(self, t_id: str, *, continuation: Optional[dict]=None, additional_lease_claims: tuple[dict, ...]=(), direct_route_resource: Optional[tuple[str, ResourceVersionRef, str]]=None) -> int:
        from ._marking import revisions as _revisions
        return _revisions.remark_inputs(self, t_id, continuation=continuation, additional_lease_claims=additional_lease_claims, direct_route_resource=direct_route_resource)

    def would_remark_reenable(self, t_id: str) -> bool:
        from ._marking import revisions as _revisions
        return _revisions.would_remark_reenable(self, t_id)

    def remark_join_siblings(self, closure: set[str], refired_producer: str, *, settled_join_inputs: Sequence[tuple[str, 'PetriTokenState']] | None=None) -> int:
        from ._marking import revisions as _revisions
        return _revisions.remark_join_siblings(self, closure, refired_producer, settled_join_inputs=settled_join_inputs)

    def restore_declared_read_lanes(self, closure: set[str], *, excluded_places: Sequence[str]=()) -> int:
        from ._marking import revisions as _revisions
        return _revisions.restore_declared_read_lanes(self, closure, excluded_places=excluded_places)


def validate_typed_marking_state(net: 'MarkingNetStructure', *, epoch: int, next_token_id: int, attempts: tuple['AttemptCounterAuthority', ...], tokens: tuple['PetriTokenState', ...], require_token_refs: bool) -> None:
    from ._marking import checkpoints as _checkpoints
    return _checkpoints.validate_typed_marking_state(net, epoch=epoch, next_token_id=next_token_id, attempts=attempts, tokens=tokens, require_token_refs=require_token_refs)


__all__ = [
    "ActiveFiringClaim", "BindingEnumeration", "BindingQueueOverflow",
    "BoundInputToken", "MarkingResourceRefError", "MarkingStateError",
    "RemarkResult", "ResourceDemand", "TimedBinding", "TimedWaitGuardState",
    "TimedMarkingCheckpointAuthority", "TimedMarkingDeltaMaterial",
    "TimedTerminalOutput", "TimedTerminalProposal", "Token",
    "TeamNetMarking", "exact_token_identity",
    "validate_typed_marking_state",
]
