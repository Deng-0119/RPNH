"""Two durable metadata attempts in the original Registry.

The stable slot is persisted; the single-use planning handle is not. Losing a
handle leaves its original pending reservation and never grants a fresh plan.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .event_store import RegistryConflict, RegistryOrdinalConflict
from .models import PendingEvent, TypedRelation, VersionRef
from .publication import _stable_id, _version_from_payload
from .schema_catalog import canonical_json
from .strict_contracts import ref_payload


class MaterialAttemptRecoveryRequired(RegistryConflict):
    pass


class MaterialAttemptBudgetExhausted(RegistryConflict):
    pass


_SEAL = object()
_ROUTE = ContextVar("material_publication_attempt", default=None)
_CONTROL = ContextVar("material_publication_attempt_control", default=None)


@dataclass(frozen=True, slots=True)
class MaterialPublicationAttempt:
    attempt_ref: VersionRef
    slot: int
    writer_epoch: int
    reservation_transaction_id: str
    reservation_event_id: str
    expected_registry_ordinal: int
    proposal_key: str
    owner_command_ref: VersionRef
    firing_ref: VersionRef
    _core: object = field(repr=False, compare=False)
    _state: dict = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self):
        if self._seal is not _SEAL:
            raise TypeError("material attempt requires its newly committed native reservation")


class MaterialAttemptOrdinalConflict(RegistryOrdinalConflict):
    """A process-local witness issued only by the original ordinal lock."""

    def __init__(self, attempt, observed_ordinal, *, _seal=None):
        if _seal is not _SEAL:
            raise TypeError("material ordinal conflict requires the original locked comparison")
        self._attempt = attempt
        self._observed_registry_ordinal = observed_ordinal
        super().__init__("expected Registry head changed for the original material attempt")

    @property
    def attempt(self):
        return self._attempt

    @property
    def observed_registry_ordinal(self):
        return self._observed_registry_ordinal


@dataclass(slots=True)
class _ControlWrite:
    core: object
    mode: str
    body: dict
    attempt: object = None
    conflict: object = None
    validated_new: bool = False
    validated_db: object = None
    validated_transaction_id: str | None = None


def _token(attempt, *, core=None):
    if (type(attempt) is not MaterialPublicationAttempt or attempt._seal is not _SEAL
            or attempt._state.get("owner") is not attempt
            or (core is not None and attempt._core is not core)):
        raise TypeError("material attempt token belongs to another original Owner")
    return attempt


@contextmanager
def material_publication_attempt(attempt):
    """Enter the sole planning/submission use of a newly reserved slot."""
    _token(attempt)
    if attempt._state.get("entered") or _ROUTE.get() is not None:
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: material slot cannot be planned twice")
    attempt._state["entered"] = True
    route = _ROUTE.set(attempt)
    try:
        yield attempt
    finally:
        _ROUTE.reset(route)


def _locked_conflict(attempt, observed_ordinal):
    _token(attempt)
    if observed_ordinal <= attempt.expected_registry_ordinal:
        raise RegistryConflict("material conflict does not prove a later original ordinal")
    existing = attempt._state.get("conflict")
    if existing is not None:
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original comparison already failed")
    result = MaterialAttemptOrdinalConflict(attempt, observed_ordinal, _seal=_SEAL)
    attempt._state["conflict"] = result
    return result


def read_material_publication_attempt_state(core, *, owner_command_ref, firing_ref):
    from ._event_store.material_publication_attempts import read_attempt_state
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return read_attempt_state(core.event_store, db, task_id=core.task_id,
            owner_command_ref=ref_payload(owner_command_ref), firing_ref=ref_payload(firing_ref))


def reserve_material_publication_attempt(core, *, owner_command_ref, firing_ref):
    from ._registry import _RegistryCore
    from ._event_store.material_publication_attempts import (
        ATTEMPT, RESERVED, attempt_body, read_attempt_state, reservation_key,
    )
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(owner_command_ref, VersionRef)
            or owner_command_ref.entity_type != "resource_version/v1"
            or not isinstance(firing_ref, VersionRef)
            or firing_ref.entity_type != "transition_firing/v1"):
        raise TypeError("material reservation requires the original writer and exact command/firing")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        state = read_attempt_state(core.event_store, db, task_id=core.task_id,
            owner_command_ref=ref_payload(owner_command_ref), firing_ref=ref_payload(firing_ref))
        if state["status"] == "EXHAUSTED":
            raise MaterialAttemptBudgetExhausted("METADATA_RETRY_EXHAUSTED: both original slots are closed")
        if state["status"] not in {"EMPTY", "RETRY_AVAILABLE"}:
            raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original material reservation is not fresh")
        slot = len(state["attempts"])
        prior = state["attempts"][-1]["conflict_event_id"] if slot else None
        body = attempt_body(core.event_store, db, task_id=core.task_id,
            owner_command_ref=ref_payload(owner_command_ref), firing_ref=ref_payload(firing_ref),
            slot=slot, prior_conflict_event_id=prior, require_prepared=True)
    key = reservation_key(core.task_id, body["owner_command_ref"], body["transition_firing_ref"], slot)
    tx = core.begin(idempotency_key=key)
    attempt_ref = _version_from_payload(body["material_publication_attempt_ref"])
    tx.prewrite(object_type=ATTEMPT, logical_id=attempt_ref.entity_id,
        version_id=attempt_ref.version_id, payload=canonical_json(body), metadata=body,
        media_type="application/json", schema_ref="registry_v1/" + ATTEMPT)
    tx.relate(TypedRelation(_stable_id("relation", key, "accepted-command"), "derived_from",
        attempt_ref, owner_command_ref, system_owned=True))
    tx.append(PendingEvent(event_type=RESERVED, criticality="authoritative",
        stream_id="material-publication-attempt:" + str(attempt_ref.entity_id),
        aggregate_id=str(attempt_ref.entity_id), aggregate_type=ATTEMPT,
        idempotency_key=key, command_id=key, payload=body,
        payload_schema_ref="registry_v1/" + RESERVED))
    control = _ControlWrite(core, "reserve", body)
    context = _CONTROL.set(control)
    try:
        tx.commit()
    finally:
        _CONTROL.reset(context)
    # Exact transaction replay returns before the new-write gate. It cannot
    # manufacture another planning capability after a lost result or restart.
    if not control.validated_new:
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original reservation was already committed")
    state = read_material_publication_attempt_state(core,
        owner_command_ref=owner_command_ref, firing_ref=firing_ref)
    item = state["attempts"][-1]
    if (state["status"] != "PENDING" or item["body"] != body
            or item["writer_epoch"] != core.writer_epoch):
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original reservation changed before planning")
    result = MaterialPublicationAttempt(attempt_ref, slot, item["writer_epoch"],
        item["reservation_transaction_id"], item["reservation_event_id"],
        item["reservation_end_ordinal"], body["proposal_key"], owner_command_ref, firing_ref,
        core, {}, _SEAL)
    result._state["owner"] = result
    return result


def check_material_publication_attempt_cut(core, attempt):
    """Check before planning under the same native writer/ordinal lock."""
    from ._event_store.material_publication_attempts import require_live_attempt
    _token(attempt, core=core)
    if (_ROUTE.get() is not attempt or attempt._state.get("submitted")
            or attempt._state.get("conflict") is not None):
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: material planning comparison is not fresh")
    with core.event_store._lock, core.event_store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        require_live_attempt(core.event_store, db, task_id=core.task_id,
            writer_epoch=core.writer_epoch, attempt=attempt)
        attempt._state["checked"] = True
        ordinal = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
        if ordinal != attempt.expected_registry_ordinal:
            raise _locked_conflict(attempt, ordinal)
        return ordinal


def mark_material_publication_ordinal_conflict(core, attempt, conflict):
    """Persist only the actual failed original locked comparison."""
    from ._event_store.material_publication_attempts import CONFLICTED, conflict_body, conflict_key
    _token(attempt, core=core)
    if (type(conflict) is not MaterialAttemptOrdinalConflict or conflict.attempt is not attempt
            or attempt._state.get("conflict") is not conflict):
        raise TypeError("material conflict recording needs the actual sealed ordinal failure")
    body = conflict_body(core.task_id, attempt, conflict.observed_registry_ordinal)
    key = conflict_key(core.task_id, ref_payload(attempt.owner_command_ref),
                       ref_payload(attempt.firing_ref), attempt.slot)
    tx = core.begin(idempotency_key=key)
    tx.append(PendingEvent(event_type=CONFLICTED, criticality="authoritative",
        stream_id="material-publication-attempt:" + str(attempt.attempt_ref.entity_id),
        aggregate_id=str(attempt.attempt_ref.entity_id), aggregate_type="material_publication_attempt/v1",
        idempotency_key=key, command_id=key, payload=body,
        payload_schema_ref="registry_v1/" + CONFLICTED))
    control = _ControlWrite(core, "conflict", body, attempt, conflict)
    context = _CONTROL.set(control)
    try:
        tx.commit()
    finally:
        _CONTROL.reset(context)
    return read_material_publication_attempt_state(core,
        owner_command_ref=attempt.owner_command_ref, firing_ref=attempt.firing_ref)


__all__ = ("MaterialAttemptRecoveryRequired", "MaterialAttemptBudgetExhausted",
    "MaterialPublicationAttempt", "MaterialAttemptOrdinalConflict", "material_publication_attempt",
    "read_material_publication_attempt_state", "reserve_material_publication_attempt",
    "check_material_publication_attempt_cut", "mark_material_publication_ordinal_conflict")
