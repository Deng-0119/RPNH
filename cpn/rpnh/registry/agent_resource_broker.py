"""Registry-owned lifecycle for AgentLoop same-firing resource requests.

The optional AgentLoop adapter supplies only its exact parent identities, one
immutable resource version, and a semantic access mode.  This module derives
all Petri fields from the current firing and marking, persists immutable
lifecycle revisions, and keeps every live extension paired with the Harness
Petri access fact in the same transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Literal, Mapping

from ..firing_resource_access import (
    RegisteredPetriFiringResourceAccess,
    verify_registered_firing_resource_access_conflicts,
)
from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .firing_resource_access import (
    derive_current_firing_resource_access,
    stage_current_firing_resource_access,
)
from .identities import TypedId
from .models import PendingEvent, VersionRef
from .module_execution import active_module_firings
from .publication import _ref_payload, _stable_id, _version_from_payload
from .resources import (
    AgentLoopResourceGrantAuthority,
    AgentLoopResourceLifecycleAuthority,
    ResourceVersionRef,
    TransitionFiringAuthority,
)
from .schema_catalog import canonical_json


AccessMode = Literal["read", "edit", "upgrade"]
SettlementDisposition = Literal["success", "fault", "abandon"]


@dataclass(frozen=True, slots=True)
class AgentResourceWaitCandidate:
    """Process-local Harness candidate matched back to durable Registry facts."""

    firing: TransitionFiringAuthority
    operation_execution_lease_ref: VersionRef

    def __post_init__(self) -> None:
        if (not isinstance(self.firing, TransitionFiringAuthority)
                or not isinstance(
                    self.operation_execution_lease_ref, VersionRef)
                or self.operation_execution_lease_ref.entity_type
                != "operation_execution_lease/v1"):
            raise TypeError(
                "resource wait candidate requires exact firing/execution authority")


def _resource_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


def _resource_from_ref_payload(value: Mapping[str, Any]) -> ResourceVersionRef:
    ref = _version_from_payload(value)
    if (ref.entity_type != "resource_version/v1"
            or ref.entity_id.kind != "resource"
            or ref.version_id.kind != "resource_version"):
        raise ResourceIntegrityFault("lifecycle resource ref is malformed")
    return ResourceVersionRef(ref.entity_id, ref.version_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_utc(value: str | None) -> str:
    if value is None:
        return _utc_now()
    if not isinstance(value, str) or not value:
        raise TypeError("resource lifecycle observation requires UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TypeError("resource lifecycle observation is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TypeError("resource lifecycle observation must include a timezone")
    return value


@dataclass(frozen=True, slots=True)
class AgentLoopResourceRequest:
    """Exact parent/action request before Registry-owned Petri derivation."""

    firing: TransitionFiringAuthority
    invocation_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    operation_binding_ref: VersionRef
    agent_loop_ref: VersionRef
    agent_turn_ref: VersionRef
    agent_action_ref: VersionRef
    requester_agent_ref: VersionRef
    resource_ref: ResourceVersionRef
    access_mode: AccessMode
    llm_turns_used: int

    def __post_init__(self) -> None:
        typed = (
            (self.invocation_ref, "invocation/v1"),
            (self.operation_execution_lease_ref,
             "operation_execution_lease/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.agent_loop_ref, "agent_loop/v1"),
            (self.agent_turn_ref, "agent_turn/v1"),
            (self.agent_action_ref, "agent_action/v2"),
            (self.requester_agent_ref, "agent/v1"),
        )
        if (not isinstance(self.firing, TransitionFiringAuthority)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != expected
                       for ref, expected in typed)
                or self.operation_binding_ref
                != self.firing.operation_binding_ref
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or self.access_mode not in {"read", "edit", "upgrade"}
                or isinstance(self.llm_turns_used, bool)
                or not isinstance(self.llm_turns_used, int)
                or self.llm_turns_used < 1):
            raise TypeError("agent resource request authority is incomplete")


def _request_identity(request: AgentLoopResourceRequest) -> TypedId:
    return _stable_id(
        "firing_resource_use",
        request.firing.transition_firing_ref.version_id,
        request.agent_action_ref.version_id,
        request.resource_ref.resource_id,
        request.resource_ref.resource_version_id,
        request.access_mode,
    )


def _subref(
        lifecycle_id: TypedId, label: str, *,
        predecessor: VersionRef | None = None,
) -> VersionRef:
    parts: tuple[object, ...] = (lifecycle_id, label)
    if predecessor is not None:
        parts = (*parts, predecessor.version_id)
    return VersionRef(
        f"resource_access_{label}/v1",
        _stable_id("firing_resource_use", *parts),
        _stable_id("firing_resource_use_version", *parts),
    )


def _lifecycle_ref(
        lifecycle_id: TypedId, state: str, *,
        predecessor: VersionRef | None = None,
        settlement_ref: VersionRef | None = None,
) -> VersionRef:
    return VersionRef(
        "resource_access_lifecycle/v1",
        lifecycle_id,
        _stable_id(
            "firing_resource_use_version", lifecycle_id, state,
            None if predecessor is None else predecessor.version_id,
            None if settlement_ref is None else settlement_ref.version_id),
    )


def _formal_fields(
        authority: RegisteredPetriFiringResourceAccess,
) -> dict[str, Any]:
    access = authority.access
    return {
        "access_checkpoint_ref": _ref_payload(authority.checkpoint_ref),
        "access_net_ref": _ref_payload(authority.net_ref),
        "access_claim_epoch": access.claim_epoch,
        "resource_token_ref": _ref_payload(access.resource_token_ref),
        "lease_pool_place": access.lease_pool_place,
        "lease_identity_ref": _ref_payload(access.lease_identity_ref),
        "petri_input_arc_mode": access.input_arc_mode,
        "petri_output_arc_mode": access.output_arc_mode,
        "petri_arc_kind": access.input_arc_mode,
        "return_arc_required": access.output_arc_mode == "return",
    }


def _latest_lifecycle_documents(
        core: _RegistryCore,
) -> dict[str, dict[str, Any]]:
    heads: dict[str, Any] = {}
    for row in core.event_store.object_rows_by_type(
            "resource_access_lifecycle/v1"):
        heads[str(row["logical_id"])] = row
    result: dict[str, dict[str, Any]] = {}
    for logical_id, row in heads.items():
        ref = VersionRef(
            "resource_access_lifecycle/v1",
            TypedId.parse(logical_id, expected="firing_resource_use"),
            TypedId.parse(
                str(row["version_id"]),
                expected="firing_resource_use_version"),
        )
        prepared = core.get_version(ref.version_id)
        raw = core.object_store.read_registered(prepared)
        try:
            document = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "resource lifecycle bytes are not canonical JSON") from exc
        core.catalog.validate_instance(
            "resource_access_lifecycle/v1",
            category="object", instance=document)
        if (prepared.object_type != ref.entity_type
                or prepared.logical_id != ref.entity_id
                or prepared.metadata != document
                or raw != canonical_json(document)
                or document.get("lifecycle_ref") != _ref_payload(ref)):
            raise ResourceIntegrityFault(
                "resource lifecycle head differs from immutable Registry bytes")
        result[logical_id] = document
    return result


def _lifecycle_authority(
        document: Mapping[str, Any],
) -> AgentLoopResourceLifecycleAuthority:
    optional = lambda name: (
        None if document[name] is None
        else _version_from_payload(document[name]))
    return AgentLoopResourceLifecycleAuthority(
        lifecycle_ref=_version_from_payload(document["lifecycle_ref"]),
        request_ref=_version_from_payload(document["request_ref"]),
        queue_entry_ref=_version_from_payload(document["queue_entry_ref"]),
        transition_firing_ref=_version_from_payload(
            document["transition_firing_ref"]),
        invocation_ref=_version_from_payload(document["invocation_ref"]),
        operation_execution_lease_ref=_version_from_payload(
            document["operation_execution_lease_ref"]),
        operation_binding_ref=_version_from_payload(
            document["operation_binding_ref"]),
        agent_loop_ref=_version_from_payload(document["agent_loop_ref"]),
        agent_turn_ref=_version_from_payload(document["agent_turn_ref"]),
        agent_action_ref=_version_from_payload(document["agent_action_ref"]),
        requester_agent_ref=_version_from_payload(
            document["requester_agent_ref"]),
        logical_resource_id=TypedId.parse(
            str(document["logical_resource_id"]), expected="resource"),
        lock_resource_ref=_resource_from_ref_payload(
            document["lock_resource_ref"]),
        resource_ref=_resource_from_ref_payload(
            document["requested_resource_ref"]),
        llm_turns_used=int(document["llm_turns_used"]),
        access_mode=str(document["mode"]),
        access_checkpoint_ref=_version_from_payload(
            document["access_checkpoint_ref"]),
        access_net_ref=_version_from_payload(document["access_net_ref"]),
        access_claim_epoch=int(document["access_claim_epoch"]),
        resource_token_ref=_version_from_payload(
            document["resource_token_ref"]),
        lease_pool_place=str(document["lease_pool_place"]),
        lease_identity_ref=_version_from_payload(
            document["lease_identity_ref"]),
        petri_input_arc_mode=str(document["petri_input_arc_mode"]),
        petri_output_arc_mode=document["petri_output_arc_mode"],
        petri_arc_kind=str(document["petri_arc_kind"]),
        return_arc_required=bool(document["return_arc_required"]),
        state=str(document["state"]),
        writer_fencing_epoch=int(document["writer_fencing_epoch"]),
        heartbeat_ref=_version_from_payload(document["heartbeat_ref"]),
        heartbeat_at_utc=str(document["heartbeat_at_utc"]),
        grant_ref=optional("grant_ref"),
        resume_ref=optional("resume_ref"),
        release_ref=optional("release_ref"),
        cleanup_ref=optional("cleanup_ref"),
        lease_ref=optional("lease_ref"),
        seal_ref=optional("seal_ref"),
        settlement_ref=optional("settlement_ref"),
        settlement_disposition=document["settlement_disposition"],
    )


def _grant_authority(
        lifecycle: AgentLoopResourceLifecycleAuthority,
) -> AgentLoopResourceGrantAuthority:
    if lifecycle.grant_ref is None or lifecycle.lease_ref is None:
        raise ResourceIntegrityFault("resource lifecycle has no exact grant")
    return AgentLoopResourceGrantAuthority(
        transition_firing_ref=lifecycle.transition_firing_ref,
        invocation_ref=lifecycle.invocation_ref,
        operation_execution_lease_ref=(
            lifecycle.operation_execution_lease_ref),
        operation_binding_ref=lifecycle.operation_binding_ref,
        queue_entry_id=str(lifecycle.queue_entry_ref.version_id),
        resource_ref=lifecycle.resource_ref,
        writer_fencing_epoch=lifecycle.writer_fencing_epoch,
        agent_loop_ref=lifecycle.agent_loop_ref,
        agent_turn_ref=lifecycle.agent_turn_ref,
        agent_action_ref=lifecycle.agent_action_ref,
        access_mode=lifecycle.access_mode,
        access_checkpoint_ref=lifecycle.access_checkpoint_ref,
        access_net_ref=lifecycle.access_net_ref,
        access_claim_epoch=lifecycle.access_claim_epoch,
        resource_token_ref=lifecycle.resource_token_ref,
        lease_pool_place=lifecycle.lease_pool_place,
        lease_identity_ref=lifecycle.lease_identity_ref,
        petri_input_arc_mode=lifecycle.petri_input_arc_mode,
        petri_output_arc_mode=lifecycle.petri_output_arc_mode,
        petri_arc_kind=lifecycle.petri_arc_kind,
        return_arc_required=lifecycle.return_arc_required,
        lifecycle_ref=lifecycle.lifecycle_ref,
        request_ref=lifecycle.request_ref,
        heartbeat_ref=lifecycle.heartbeat_ref,
        grant_ref=lifecycle.grant_ref,
        lease_ref=lifecycle.lease_ref,
        llm_turns_used=lifecycle.llm_turns_used,
    )


def _stage_lifecycle(
        core: _RegistryCore, tx: Any, document: Mapping[str, Any], *,
        producer_invocation_id: TypedId,
) -> AgentLoopResourceLifecycleAuthority:
    core.catalog.validate_instance(
        "resource_access_lifecycle/v1",
        category="object", instance=document)
    ref = _version_from_payload(document["lifecycle_ref"])
    tx.prewrite(
        object_type="resource_access_lifecycle/v1",
        logical_id=ref.entity_id,
        version_id=ref.version_id,
        payload=canonical_json(document),
        metadata=document,
        media_type="application/json",
        schema_ref="registry_v1/resource_access_lifecycle/v1",
        producer_invocation_id=producer_invocation_id,
    )
    return _lifecycle_authority(document)


def _queue_entry(
        request: AgentLoopResourceRequest, request_ref: VersionRef,
        queue_entry_ref: VersionRef, ordinal: int,
) -> dict[str, Any]:
    return {
        "queue_entry_ref": _ref_payload(queue_entry_ref),
        "request_ref": _ref_payload(request_ref),
        "transition_firing_ref": _ref_payload(
            request.firing.transition_firing_ref),
        "invocation_ref": _ref_payload(request.invocation_ref),
        "mode": request.access_mode,
        "ordinal": ordinal,
        "writer_fencing_epoch": request.firing.verified_at_head.writer_fencing_epoch,
    }


def _waiting_documents(
        documents: Mapping[str, Mapping[str, Any]],
        resource_ref: ResourceVersionRef,
) -> tuple[Mapping[str, Any], ...]:
    exact = _ref_payload(resource_ref.as_version_ref())
    return tuple(sorted((
        value for value in documents.values()
        if (value.get("state") == "waiting_resource"
            and value.get("requested_resource_ref") == exact)
    ), key=lambda value: (
        int(next((entry.get("ordinal", 0)
                  for entry in value.get("stable_queue", ())
                  if entry.get("request_ref") == value.get("request_ref")), 0)),
        str(value.get("request_ref", {}).get("version_id", "")),
    )))


def _request_is_active(core: _RegistryCore, request: AgentLoopResourceRequest) -> None:
    if (core.read_only
            or core.writer_epoch
            != request.firing.verified_at_head.writer_fencing_epoch):
        raise ResourceIntegrityFault("resource request has a stale writer fence")
    active = active_module_firings(core, request.firing.net_ref)
    if not any(item.transition_firing_ref
               == request.firing.transition_firing_ref for item in active):
        raise ResourceIntegrityFault("resource request firing is not active")
    invocation = core.get_version(request.invocation_ref.version_id)
    if (invocation.object_type != "invocation/v1"
            or invocation.logical_id != request.invocation_ref.entity_id
            or invocation.metadata.get("invocation_ref")
            != _ref_payload(request.invocation_ref)
            or invocation.metadata.get("own_transition_firing_ref")
            != _ref_payload(request.firing.transition_firing_ref)
            or invocation.metadata.get("operation_execution_lease_ref")
            != _ref_payload(request.operation_execution_lease_ref)
            or invocation.metadata.get("operation_binding_ref")
            != _ref_payload(request.operation_binding_ref)):
        raise ResourceIntegrityFault(
            "resource request differs from its invocation authority")


def _base_document(
        core: _RegistryCore, request: AgentLoopResourceRequest,
        formal: RegisteredPetriFiringResourceAccess, *,
        lifecycle_ref: VersionRef, request_ref: VersionRef,
        queue_entry_ref: VersionRef, heartbeat_ref: VersionRef,
        observed_at_utc: str, stable_queue: list[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "lifecycle_ref": _ref_payload(lifecycle_ref),
        "request_ref": _ref_payload(request_ref),
        "queue_entry_ref": _ref_payload(queue_entry_ref),
        "heartbeat_ref": _ref_payload(heartbeat_ref),
        "grant_ref": None,
        "resume_ref": None,
        "release_ref": None,
        "cleanup_ref": None,
        "transition_firing_ref": _ref_payload(
            request.firing.transition_firing_ref),
        "transition_id": request.firing.transition_id,
        "invocation_ref": _ref_payload(request.invocation_ref),
        "operation_execution_lease_ref": _ref_payload(
            request.operation_execution_lease_ref),
        "operation_binding_ref": _ref_payload(
            request.operation_binding_ref),
        "agent_loop_ref": _ref_payload(request.agent_loop_ref),
        "agent_turn_ref": _ref_payload(request.agent_turn_ref),
        "agent_action_ref": _ref_payload(request.agent_action_ref),
        "requester_agent_ref": _ref_payload(request.requester_agent_ref),
        "logical_resource_id": str(request.resource_ref.resource_id),
        "lock_resource_ref": _ref_payload(
            request.resource_ref.as_version_ref()),
        "requested_resource_ref": _ref_payload(
            request.resource_ref.as_version_ref()),
        "llm_turns_used": request.llm_turns_used,
        **_formal_fields(formal),
        "same_firing_continuation": True,
        "mode": request.access_mode,
        "state": "waiting_resource",
        "stable_queue": stable_queue,
        "queue_policy": "no_reader_barging_past_writer_or_upgrade",
        "writer_fencing_epoch": core.writer_epoch,
        "heartbeat_at_utc": observed_at_utc,
        "lease_ref": None,
        "wake_reason": None,
        "resource_cycle_evidence_ref": None,
        "resource_wait_started_at_utc": observed_at_utc,
        "resource_wait_started_monotonic": None,
        "resource_wait_started_monotonic_segment_id": None,
        "resource_wait_ended_at_utc": None,
        "resource_wait_ended_monotonic": None,
        "resource_wait_ended_monotonic_segment_id": None,
        "blocked_started_at_utc": observed_at_utc,
        "blocked_started_monotonic": None,
        "blocked_started_monotonic_segment_id": None,
        "blocked_ended_at_utc": None,
        "blocked_ended_monotonic": None,
        "blocked_ended_monotonic_segment_id": None,
        "seal_ref": None,
        "settlement_ref": None,
        "settlement_disposition": None,
    }


def _stage_extension(
        core: _RegistryCore, tx: Any, request: AgentLoopResourceRequest,
        lifecycle: AgentLoopResourceLifecycleAuthority, *,
        formal: RegisteredPetriFiringResourceAccess,
        idempotency_key: str,
) -> None:
    access_mode = "read" if request.access_mode == "read" else "edit"
    stage_current_firing_resource_access(
        core, tx, request.firing, request.resource_ref, access_mode,
        idempotency_key=idempotency_key,
        expected=formal,
        producer_invocation_id=request.invocation_ref.entity_id,
    )
    if lifecycle.grant_ref is None or lifecycle.lease_ref is None:
        raise ResourceIntegrityFault("live extension lacks grant/lease authority")
    payload = {
        "transition_firing_ref": _ref_payload(
            request.firing.transition_firing_ref),
        "transition_id": request.firing.transition_id,
        "invocation_ref": _ref_payload(request.invocation_ref),
        "operation_execution_lease_ref": _ref_payload(
            request.operation_execution_lease_ref),
        "operation_binding_ref": _ref_payload(request.operation_binding_ref),
        "agent_loop_ref": _ref_payload(request.agent_loop_ref),
        "agent_turn_ref": _ref_payload(request.agent_turn_ref),
        "agent_action_ref": _ref_payload(request.agent_action_ref),
        "resource_use_occurrence_ref": _ref_payload(lifecycle.request_ref),
        "resource_access_lifecycle_ref": _ref_payload(
            lifecycle.lifecycle_ref),
        "resource_access_grant_ref": _ref_payload(lifecycle.grant_ref),
        "resource_access_lease_ref": _ref_payload(lifecycle.lease_ref),
        "logical_resource_id": str(request.resource_ref.resource_id),
        "lock_resource_ref": _ref_payload(
            request.resource_ref.as_version_ref()),
        "resource_ref": _resource_payload(request.resource_ref),
        "access_mode": access_mode,
        **_formal_fields(formal),
        "llm_turns_used": request.llm_turns_used,
        "writer_fencing_epoch": core.writer_epoch,
        "same_firing_continuation": True,
    }
    core.catalog.validate_instance(
        "live_firing_resource_extension/v1",
        category="event", instance=payload)
    tx.append(PendingEvent(
        event_type="live_firing_resource_extension/v1",
        criticality="authoritative",
        stream_id=(
            "transition-firing:"
            f"{request.firing.transition_firing_ref.entity_id}"),
        aggregate_id=str(request.firing.transition_firing_ref.entity_id),
        aggregate_type="transition_firing",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=payload,
        payload_schema_ref="registry_v1/live_firing_resource_extension/v1",
        task_control=True,
        producer_invocation_id=request.invocation_ref.entity_id,
    ))


def prepare_agent_resource_request(
        core: _RegistryCore, tx: Any, request: AgentLoopResourceRequest, *,
        idempotency_key: str,
        observed_at_utc: str | None = None,
) -> AgentLoopResourceGrantAuthority | AgentLoopResourceLifecycleAuthority:
    """Stage either an immediate grant or one durable waiting lifecycle.

    The caller owns the AgentLoop action transaction.  For an immediate grant,
    that transaction must also stage the exact applied action/loop closure; the
    EventStore enforces it.  No provider or adapter call occurs here.
    """

    if (not isinstance(core, _RegistryCore)
            or not isinstance(request, AgentLoopResourceRequest)
            or not isinstance(idempotency_key, str)
            or not idempotency_key
            or getattr(tx, "idempotency_key", None) != idempotency_key
            or getattr(tx, "event_store", None) is not core.event_store
            or getattr(tx, "net_instance_id", None)
            != request.firing.net_ref.entity_id):
        raise TypeError(
            "agent resource preparation requires its exact Registry transaction")
    observed = _require_utc(observed_at_utc)
    _request_is_active(core, request)
    lifecycle_id = _request_identity(request)
    documents = _latest_lifecycle_documents(core)
    existing = documents.get(str(lifecycle_id))
    if existing is not None:
        authority = _lifecycle_authority(existing)
        if (authority.transition_firing_ref
                != request.firing.transition_firing_ref
                or authority.invocation_ref != request.invocation_ref
                or authority.agent_action_ref != request.agent_action_ref
                or authority.resource_ref != request.resource_ref
                or authority.access_mode != request.access_mode):
            raise ResourceIntegrityFault(
                "resource request identity resolves to another lifecycle")
        if authority.state == "waiting_resource":
            return authority
        if authority.state in {"granted", "resumed"}:
            return _grant_authority(authority)
        raise ResourceIntegrityFault("resource request lifecycle is already closed")

    formal_mode = "read" if request.access_mode == "read" else "edit"
    formal = derive_current_firing_resource_access(
        core, request.firing, request.resource_ref, formal_mode)
    request_ref = _subref(lifecycle_id, "request")
    queue_entry_ref = _subref(lifecycle_id, "queue_entry")
    heartbeat_ref = _subref(lifecycle_id, "heartbeat")
    waiters = _waiting_documents(documents, request.resource_ref)
    ordinal = 1 + max((
        int(entry.get("ordinal", -1))
        for value in waiters
        for entry in value.get("stable_queue", ())
    ), default=-1)
    own_entry = _queue_entry(
        request, request_ref, queue_entry_ref, ordinal)
    stable_queue = [
        dict(entry)
        for value in waiters
        for entry in value.get("stable_queue", ())
        if entry.get("request_ref") == value.get("request_ref")
    ]
    stable_queue.append(own_entry)
    stable_queue.sort(key=lambda value: (
        int(value["ordinal"]), str(value["request_ref"]["version_id"])))

    contended = bool(waiters)
    if not contended:
        try:
            verify_registered_firing_resource_access_conflicts(core, formal)
        except (TypeError, ValueError):
            contended = True
    state = "waiting_resource" if contended else "granted"
    lifecycle_ref = _lifecycle_ref(lifecycle_id, state)
    document = _base_document(
        core, request, formal,
        lifecycle_ref=lifecycle_ref,
        request_ref=request_ref,
        queue_entry_ref=queue_entry_ref,
        heartbeat_ref=heartbeat_ref,
        observed_at_utc=observed,
        stable_queue=stable_queue if contended else [],
    )
    if contended:
        lifecycle = _stage_lifecycle(
            core, tx, document,
            producer_invocation_id=request.invocation_ref.entity_id)
        tx.validate_before_commit(
            lambda _objects: _request_is_active(core, request))
        return lifecycle

    grant_ref = _subref(lifecycle_id, "grant")
    lease_ref = _subref(lifecycle_id, "lease")
    document.update({
        "grant_ref": _ref_payload(grant_ref),
        "lease_ref": _ref_payload(lease_ref),
        "state": "granted",
        "wake_reason": "lock_compatible",
        "resource_wait_started_at_utc": None,
        "blocked_started_at_utc": None,
    })
    lifecycle = _stage_lifecycle(
        core, tx, document,
        producer_invocation_id=request.invocation_ref.entity_id)
    _stage_extension(
        core, tx, request, lifecycle, formal=formal,
        idempotency_key=idempotency_key)
    return _grant_authority(lifecycle)


def _request_from_lifecycle(
        firing: TransitionFiringAuthority,
        lifecycle: AgentLoopResourceLifecycleAuthority,
) -> AgentLoopResourceRequest:
    return AgentLoopResourceRequest(
        firing=firing,
        invocation_ref=lifecycle.invocation_ref,
        operation_execution_lease_ref=(
            lifecycle.operation_execution_lease_ref),
        operation_binding_ref=lifecycle.operation_binding_ref,
        agent_loop_ref=lifecycle.agent_loop_ref,
        agent_turn_ref=lifecycle.agent_turn_ref,
        agent_action_ref=lifecycle.agent_action_ref,
        requester_agent_ref=lifecycle.requester_agent_ref,
        resource_ref=lifecycle.resource_ref,
        access_mode=lifecycle.access_mode,
        llm_turns_used=lifecycle.llm_turns_used,
    )


def _assert_latest(
        core: _RegistryCore,
        expected: AgentLoopResourceLifecycleAuthority,
) -> Mapping[str, Any]:
    document = _latest_lifecycle_documents(core).get(
        str(expected.lifecycle_ref.entity_id))
    if document is None or _lifecycle_authority(document) != expected:
        raise ResourceIntegrityFault(
            "resource lifecycle authority is stale or mismatched")
    return document


def promote_waiting_agent_resource(
        core: _RegistryCore, firing: TransitionFiringAuthority,
        waiting: AgentLoopResourceLifecycleAuthority, *,
        idempotency_key: str,
        observed_at_utc: str | None = None,
        wake_reason: Literal[
            "lock_compatible", "owner_stop", "firing_settled",
            "firing_faulted", "firing_abandoned", "lease_expired",
            "request_cancelled"] = "lock_compatible",
) -> AgentLoopResourceGrantAuthority:
    """Atomically promote one exact waiting head and return its typed grant."""

    if (not isinstance(core, _RegistryCore)
            or not isinstance(firing, TransitionFiringAuthority)
            or not isinstance(waiting, AgentLoopResourceLifecycleAuthority)
            or waiting.state != "waiting_resource"
            or waiting.transition_firing_ref != firing.transition_firing_ref
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError("waiting promotion requires exact lifecycle authority")
    observed = _require_utc(observed_at_utc)
    prior = dict(_assert_latest(core, waiting))
    request = _request_from_lifecycle(firing, waiting)
    _request_is_active(core, request)
    documents = _latest_lifecycle_documents(core)
    waiters = _waiting_documents(documents, waiting.resource_ref)
    try:
        position = next(index for index, value in enumerate(waiters)
                        if value.get("lifecycle_ref")
                        == _ref_payload(waiting.lifecycle_ref))
    except StopIteration as exc:
        raise ResourceIntegrityFault(
            "waiting lifecycle is absent from the stable queue") from exc
    predecessors = waiters[:position]
    if (waiting.access_mode != "read"
            and predecessors
            or waiting.access_mode == "read"
            and any(value.get("mode") != "read" for value in predecessors)):
        raise ResourceIntegrityFault(
            "waiting lifecycle is not next under the stable queue policy")
    formal_mode = "read" if waiting.access_mode == "read" else "edit"
    formal = derive_current_firing_resource_access(
        core, firing, waiting.resource_ref, formal_mode)
    try:
        verify_registered_firing_resource_access_conflicts(core, formal)
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "waiting resource remains contended") from exc

    lifecycle_ref = _lifecycle_ref(
        waiting.lifecycle_ref.entity_id, "granted",
        predecessor=waiting.lifecycle_ref)
    grant_ref = _subref(
        waiting.lifecycle_ref.entity_id, "grant",
        predecessor=waiting.lifecycle_ref)
    lease_ref = _subref(
        waiting.lifecycle_ref.entity_id, "lease",
        predecessor=waiting.lifecycle_ref)
    heartbeat_ref = _subref(
        waiting.lifecycle_ref.entity_id, "heartbeat",
        predecessor=waiting.lifecycle_ref)
    remaining = [
        entry for value in waiters
        if value.get("lifecycle_ref") != _ref_payload(waiting.lifecycle_ref)
        for entry in value.get("stable_queue", ())
        if entry.get("request_ref") == value.get("request_ref")
    ]
    remaining.sort(key=lambda value: (
        int(value["ordinal"]), str(value["request_ref"]["version_id"])))
    prior.update({
        "lifecycle_ref": _ref_payload(lifecycle_ref),
        "heartbeat_ref": _ref_payload(heartbeat_ref),
        "grant_ref": _ref_payload(grant_ref),
        "lease_ref": _ref_payload(lease_ref),
        "state": "granted",
        "stable_queue": remaining,
        "heartbeat_at_utc": observed,
        "wake_reason": wake_reason,
        "resource_wait_ended_at_utc": observed,
        "blocked_ended_at_utc": observed,
        **_formal_fields(formal),
    })
    tx = core.begin(
        idempotency_key=idempotency_key,
        task_round_id=firing.task_round_ref.entity_id,
        net_instance_id=firing.net_ref.entity_id,
    )
    lifecycle = _stage_lifecycle(
        core, tx, prior,
        producer_invocation_id=waiting.invocation_ref.entity_id)
    _stage_extension(
        core, tx, request, lifecycle, formal=formal,
        idempotency_key=idempotency_key)
    tx.validate_before_commit(lambda _objects: _assert_latest(core, waiting))
    tx.commit()
    return _grant_authority(lifecycle)


def promote_one_waiting_agent_resource(
        core: _RegistryCore,
        candidates: tuple[AgentResourceWaitCandidate, ...], *,
        idempotency_key: str,
        observed_at_utc: str | None = None,
) -> AgentLoopResourceGrantAuthority | None:
    """Promote at most one retained wait from current durable queue facts.

    The Harness contributes only process-local executions for which it still
    owns continuations.  Selection, conflict verification, and grant creation
    remain Registry-owned.  No candidate is promoted merely because its
    process is waiting.
    """

    if (not isinstance(core, _RegistryCore)
            or not isinstance(candidates, tuple)
            or any(not isinstance(item, AgentResourceWaitCandidate)
                   for item in candidates)
            or len({item.operation_execution_lease_ref for item in candidates})
            != len(candidates)
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "resource wait promotion requires exact retained candidates")
    if not candidates:
        return None

    by_execution = {
        item.operation_execution_lease_ref: item for item in candidates}
    documents = _latest_lifecycle_documents(core)
    retained: list[tuple[tuple[object, ...], Mapping[str, Any],
                         AgentLoopResourceLifecycleAuthority,
                         AgentResourceWaitCandidate]] = []
    for document in documents.values():
        if document.get("state") != "waiting_resource":
            continue
        lifecycle = _lifecycle_authority(document)
        candidate = by_execution.get(
            lifecycle.operation_execution_lease_ref)
        if candidate is None:
            continue
        if (lifecycle.transition_firing_ref
                != candidate.firing.transition_firing_ref):
            raise ResourceIntegrityFault(
                "retained resource wait differs from its Registry firing")
        own_entries = tuple(
            entry for entry in document.get("stable_queue", ())
            if entry.get("request_ref") == document.get("request_ref"))
        if len(own_entries) != 1:
            raise ResourceIntegrityFault(
                "waiting lifecycle lacks one stable queue entry")
        entry = own_entries[0]
        retained.append((
            (int(entry["ordinal"]),
             str(lifecycle.resource_ref.resource_id),
             str(lifecycle.resource_ref.resource_version_id),
             str(lifecycle.request_ref.version_id)),
            document, lifecycle, candidate,
        ))

    for _key, document, lifecycle, candidate in sorted(
            retained, key=lambda item: item[0]):
        waiters = _waiting_documents(documents, lifecycle.resource_ref)
        try:
            position = next(
                index for index, value in enumerate(waiters)
                if value.get("lifecycle_ref")
                == document.get("lifecycle_ref"))
        except StopIteration as exc:
            raise ResourceIntegrityFault(
                "retained lifecycle is absent from its current queue") from exc
        predecessors = waiters[:position]
        if (lifecycle.access_mode != "read" and predecessors
                or lifecycle.access_mode == "read" and any(
                    value.get("mode") != "read" for value in predecessors)):
            continue

        request = _request_from_lifecycle(candidate.firing, lifecycle)
        _request_is_active(core, request)
        formal_mode = "read" if lifecycle.access_mode == "read" else "edit"
        formal = derive_current_firing_resource_access(
            core, candidate.firing, lifecycle.resource_ref, formal_mode)
        try:
            verify_registered_firing_resource_access_conflicts(core, formal)
        except (TypeError, ValueError):
            continue
        return promote_waiting_agent_resource(
            core, candidate.firing, lifecycle,
            idempotency_key=(
                f"{idempotency_key}:"
                f"{lifecycle.lifecycle_ref.entity_id}:"
                f"{lifecycle.lifecycle_ref.version_id}"),
            observed_at_utc=observed_at_utc,
            wake_reason="firing_settled",
        )
    return None


def stage_resumed_agent_resource_lifecycle(
        core: _RegistryCore, tx: Any,
        grant: AgentLoopResourceGrantAuthority, *,
        idempotency_key: str,
        observed_at_utc: str | None = None,
) -> AgentLoopResourceLifecycleAuthority:
    """Stage the immutable resumed successor for adapter consumption."""

    if (not isinstance(core, _RegistryCore)
            or not isinstance(grant, AgentLoopResourceGrantAuthority)
            or grant.lifecycle_ref is None
            or grant.grant_ref is None
            or grant.lease_ref is None
            or getattr(tx, "idempotency_key", None) != idempotency_key
            or getattr(tx, "event_store", None) is not core.event_store):
        raise TypeError("resource resume requires exact grant/transaction")
    documents = _latest_lifecycle_documents(core)
    prior = documents.get(str(grant.lifecycle_ref.entity_id))
    if prior is None:
        raise ResourceIntegrityFault("resource grant lifecycle is missing")
    current = _lifecycle_authority(prior)
    if (current.state != "granted"
            or current.lifecycle_ref != grant.lifecycle_ref
            or _grant_authority(current) != grant):
        raise ResourceIntegrityFault("resource grant is stale or mismatched")
    lifecycle_ref = _lifecycle_ref(
        current.lifecycle_ref.entity_id, "resumed",
        predecessor=current.lifecycle_ref)
    resume_ref = _subref(
        current.lifecycle_ref.entity_id, "resume",
        predecessor=current.lifecycle_ref)
    heartbeat_ref = _subref(
        current.lifecycle_ref.entity_id, "heartbeat",
        predecessor=current.lifecycle_ref)
    document = dict(prior)
    document.update({
        "lifecycle_ref": _ref_payload(lifecycle_ref),
        "resume_ref": _ref_payload(resume_ref),
        "heartbeat_ref": _ref_payload(heartbeat_ref),
        "heartbeat_at_utc": _require_utc(observed_at_utc),
        "state": "resumed",
    })
    result = _stage_lifecycle(
        core, tx, document,
        producer_invocation_id=current.invocation_ref.entity_id)
    tx.validate_before_commit(lambda _objects: _assert_latest(core, current))
    return result


def stage_firing_resource_lifecycle_seals(
        core: _RegistryCore, tx: Any, *,
        firing_ref: VersionRef,
        settlement_ref: VersionRef,
        disposition: SettlementDisposition,
        idempotency_key: str,
        producer_invocation_id: TypedId,
) -> tuple[AgentLoopResourceLifecycleAuthority, ...]:
    """Stage sealed successors for every live lifecycle of one firing.

    The helper is disposition-neutral so fault and abandon settlement paths can
    reuse it when they gain equivalent settlement integration.
    """

    if (not isinstance(core, _RegistryCore)
            or not isinstance(firing_ref, VersionRef)
            or firing_ref.entity_type != "transition_firing/v1"
            or not isinstance(settlement_ref, VersionRef)
            or disposition not in {"success", "fault", "abandon"}
            or not isinstance(idempotency_key, str)
            or not idempotency_key
            or not isinstance(producer_invocation_id, TypedId)
            or producer_invocation_id.kind != "invocation"
            or getattr(tx, "idempotency_key", None) != idempotency_key
            or getattr(tx, "event_store", None) is not core.event_store):
        raise TypeError("resource sealing requires exact settlement authority")
    documents = _latest_lifecycle_documents(core)
    live = tuple(
        value for value in documents.values()
        if (value.get("transition_firing_ref") == _ref_payload(firing_ref)
            and value.get("state") != "sealed"))
    sealed = []
    for prior in sorted(
            live, key=lambda value: value["lifecycle_ref"]["logical_id"]):
        predecessor = _version_from_payload(prior["lifecycle_ref"])
        lifecycle_ref = _lifecycle_ref(
            predecessor.entity_id, "sealed",
            predecessor=predecessor,
            settlement_ref=settlement_ref)
        seal_ref = _subref(
            predecessor.entity_id, "seal", predecessor=predecessor)
        document = dict(prior)
        document.update({
            "lifecycle_ref": _ref_payload(lifecycle_ref),
            "state": "sealed",
            "seal_ref": _ref_payload(seal_ref),
            "settlement_ref": _ref_payload(settlement_ref),
            "settlement_disposition": disposition,
        })
        sealed.append(_stage_lifecycle(
            core, tx, document,
            producer_invocation_id=producer_invocation_id))
    expected = tuple(_lifecycle_authority(value) for value in live)
    if expected:
        tx.validate_before_commit(
            lambda _objects: tuple(
                _assert_latest(core, authority) for authority in expected))
    return tuple(sealed)


__all__ = (
    "AgentResourceWaitCandidate",
    "AgentLoopResourceRequest",
    "prepare_agent_resource_request",
    "promote_one_waiting_agent_resource",
    "promote_waiting_agent_resource",
    "stage_firing_resource_lifecycle_seals",
    "stage_resumed_agent_resource_lifecycle",
)
