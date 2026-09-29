"""Registry closure for formal resource arcs on an active firing.

This is Harness code.  It knows no AgentLoop, reviewer, Finalization, or Current
workflow policy.  Callers may queue or present a resource choice however they
want; an actual grant is valid only if this module derives and revalidates the
same occurrence-local Petri authority.
"""
from __future__ import annotations

from typing import Any

from ..firing_resource_access import (
    RegisteredPetriFiringResourceAccess,
    derive_registered_firing_resource_access,
    registered_firing_resource_access_from_payload,
    registered_firing_resource_access_payload,
    verify_registered_firing_resource_access_conflicts,
)
from ..registry.publication import (
    _effective_firing_resource_access_events,
    _ref_payload,
)
from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .identities import TypedId
from .module_runtime import hydrate_module_runtime
from .models import PendingEvent
from .resources import ResourceVersionRef, TransitionFiringAuthority


def derive_current_firing_resource_access(
        core: _RegistryCore,
        firing: TransitionFiringAuthority,
        requested: ResourceVersionRef,
        access_mode: str,
) -> RegisteredPetriFiringResourceAccess:
    """Derive against the latest committed marking and prior firing arcs."""

    if (not isinstance(core, _RegistryCore)
            or core.read_only
            or not isinstance(firing, TransitionFiringAuthority)
            or not isinstance(requested, ResourceVersionRef)
            or access_mode not in {"read", "edit"}):
        raise TypeError(
            "current firing resource access requires writer/firing/resource/mode")
    executable, structure, marking = hydrate_module_runtime(core)
    if (firing.net_ref != executable.net_ref
            or firing.net_ref != marking.net_ref):
        raise ResourceIntegrityFault(
            "resource access firing differs from the adopted Petri net")
    prior_events = tuple(
        event for event in core.event_store.list_events_by_aggregate(
            str(firing.transition_firing_ref.entity_id),
            event_types=("petri_firing_resource_accessed/v1",))
        if (event.aggregate_id
            == str(firing.transition_firing_ref.entity_id)
            and event.writer_fencing_epoch == core.writer_epoch
            and event.payload.get("transition_firing_ref")
            == _ref_payload(firing.transition_firing_ref)))
    effective = _effective_firing_resource_access_events(prior_events)
    existing = tuple(
        registered_firing_resource_access_from_payload(event.payload)
        for event in effective)
    try:
        return derive_registered_firing_resource_access(
            structure, marking, firing, requested, access_mode,
            existing=existing)
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "resource request has no enabled formal Petri access arc") from exc


def verify_current_firing_resource_access(
        core: _RegistryCore,
        firing: TransitionFiringAuthority,
        requested: ResourceVersionRef,
        access_mode: str,
        expected: RegisteredPetriFiringResourceAccess,
) -> None:
    """Re-derive and conflict-check one proposed grant at precommit."""

    actual = derive_current_firing_resource_access(
        core, firing, requested, access_mode)
    if actual != expected:
        raise ResourceIntegrityFault(
            "resource access Petri preflight became stale before grant")
    try:
        verify_registered_firing_resource_access_conflicts(core, actual)
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "resource access conflicts with the active Petri marking") from exc


def stage_current_firing_resource_access(
        core: _RegistryCore, tx: Any,
        firing: TransitionFiringAuthority,
        requested: ResourceVersionRef, access_mode: str, *,
        idempotency_key: str,
        expected: RegisteredPetriFiringResourceAccess | None = None,
        producer_invocation_id: TypedId,
) -> RegisteredPetriFiringResourceAccess:
    """Stage one Harness-owned formal access fact in the caller transaction."""

    if (not isinstance(core, _RegistryCore)
            or core.read_only
            or not isinstance(firing, TransitionFiringAuthority)
            or not isinstance(requested, ResourceVersionRef)
            or access_mode not in {"read", "edit"}
            or not isinstance(idempotency_key, str)
            or not idempotency_key
            or getattr(tx, "idempotency_key", None) != idempotency_key
            or getattr(tx, "event_store", None) is not core.event_store
            or getattr(tx, "task_id", None) != core.task_id
            or getattr(tx, "writer_epoch", None) != core.writer_epoch
            or getattr(tx, "net_instance_id", None) != firing.net_ref.entity_id
            or not isinstance(producer_invocation_id, TypedId)
            or producer_invocation_id.kind != "invocation"
            or not callable(getattr(tx, "append", None))
            or not callable(getattr(tx, "validate_before_commit", None))):
        raise TypeError(
            "formal resource access requires its exact Registry transaction")
    actual = derive_current_firing_resource_access(
        core, firing, requested, access_mode)
    if expected is not None and expected != actual:
        raise ResourceIntegrityFault(
            "supplied formal resource access differs from current Petri state")
    authority = actual
    access = authority.access
    payload = {
        "transition_firing_ref": _ref_payload(
            firing.transition_firing_ref),
        "transition_id": firing.transition_id,
        "resource_ref": {
            "resource_id": str(requested.resource_id),
            "resource_version_id": str(requested.resource_version_id),
        },
        "access_mode": access_mode,
        **registered_firing_resource_access_payload(authority),
        "writer_fencing_epoch": core.writer_epoch,
    }
    tx.append(PendingEvent(
        event_type="petri_firing_resource_accessed/v1",
        criticality="authoritative",
        stream_id=(
            "transition-firing:"
            f"{firing.transition_firing_ref.entity_id}"),
        aggregate_id=str(firing.transition_firing_ref.entity_id),
        aggregate_type="transition_firing",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=payload,
        payload_schema_ref=(
            "registry_v1/petri_firing_resource_accessed/v1"),
        task_control=True,
        producer_invocation_id=producer_invocation_id))
    tx.validate_before_commit(
        lambda _objects: verify_current_firing_resource_access(
            core, firing, requested, access_mode, authority))
    return authority


def access_module_firing_resource(
        core: _RegistryCore, kernel: Any, repository: Any,
        execution: Any, requested: ResourceVersionRef, access_mode: str, *,
        idempotency_key: str,
) -> RegisteredPetriFiringResourceAccess:
    """Commit one Module-selected access through the Harness authority.

    The external Module supplies only its already-started execution, one exact
    immutable resource version, and ``read`` or ``edit``.  The Harness
    revalidates Start and derives every Petri identity before committing the
    firing-local access fact.
    """

    from .operation_execution import verify_operation_execution

    if (not isinstance(requested, ResourceVersionRef)
            or access_mode not in {"read", "edit"}
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "module resource access requires execution/resource/mode/key")
    current = verify_operation_execution(
        core, kernel, repository, execution)
    context = current.operation.canonical.context
    firing = current.operation.firing
    tx = core.begin(
        idempotency_key=idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id,
    )
    authority = stage_current_firing_resource_access(
        core,
        tx,
        firing,
        requested,
        access_mode,
        idempotency_key=idempotency_key,
        producer_invocation_id=context.invocation_ref.entity_id,
    )
    tx.commit()
    return authority


__all__ = (
    "access_module_firing_resource",
    "derive_current_firing_resource_access",
    "stage_current_firing_resource_access",
    "verify_current_firing_resource_access",
)
