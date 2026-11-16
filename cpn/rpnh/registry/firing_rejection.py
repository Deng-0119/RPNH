"""Stage one original prepared-material rejection beside its Owner result."""
from __future__ import annotations

from collections.abc import Mapping

from ._registry import _RegistryCore
from .agent_resource_broker import stage_firing_resource_lifecycle_seals
from .models import PendingEvent, TypedRelation, VersionRef
from .publication import _ref_payload, _stable_id, _version_from_payload
from .schema_catalog import canonical_json
from .transaction import RegistryTransaction
from ._event_store.firing_rejection import (
    REJECTION, FIRING_EVENT, OPERATION_EVENT, prepared_snapshot,
)


def stage_material_firing_rejection(
        core, tx, *, firing_ref, invocation_ref, owner_command_ref,
        expected_parent_checkpoint_ref, registered_completion_event_id, reason):
    """Stage exact nonpublication facts; the caller stages its result and commits.

    The read here prepares immutable metadata only. Every responsibility and
    predecessor is rechecked by EventStore inside its original write lock.
    """
    if (not isinstance(core, _RegistryCore) or not isinstance(tx, RegistryTransaction)
            or tx.event_store is not core.event_store
            or not isinstance(reason, Mapping) or set(reason) != {"code", "message"}
            or any(not isinstance(value, str) or not value for value in reason.values())):
        raise TypeError("material rejection requires original transaction authority and exact reason")
    refs = ((firing_ref, "transition_firing/v1"), (invocation_ref, "invocation/v1"),
            (owner_command_ref, "resource_version/v1"),
            (expected_parent_checkpoint_ref, "marking_checkpoint/v1"))
    if any(not isinstance(ref, VersionRef) or ref.entity_type != kind for ref, kind in refs):
        raise TypeError("material rejection requires exact typed authority refs")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        state = prepared_snapshot(core.event_store, db, task_id=core.task_id,
            firing_ref=_ref_payload(firing_ref), invocation_ref=_ref_payload(invocation_ref),
            owner_command_ref=_ref_payload(owner_command_ref),
            retained_checkpoint_ref=_ref_payload(expected_parent_checkpoint_ref),
            registered_completion_event_id=registered_completion_event_id)
    rejection_ref = VersionRef(REJECTION,
        _stable_id("firing_rejection", str(firing_ref.version_id)),
        _stable_id("firing_rejection_version", str(firing_ref.version_id), tx.idempotency_key))
    seals = stage_firing_resource_lifecycle_seals(core, tx,
        firing_ref=firing_ref, settlement_ref=rejection_ref, disposition="abandon",
        idempotency_key=tx.idempotency_key, producer_invocation_id=invocation_ref.entity_id)
    value = {"firing_rejection_ref": _ref_payload(rejection_ref),
        "owner_command_ref": _ref_payload(owner_command_ref),
        "transition_firing_ref": _ref_payload(firing_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_execution_lease_ref": state["lease_ref"],
        "retained_checkpoint_ref": _ref_payload(expected_parent_checkpoint_ref),
        "registered_completion_event_id": registered_completion_event_id,
        "reason": dict(reason), "disposition": "abandon",
        "execution_children": state["children"],
        "sealed_resource_lifecycle_refs": [_ref_payload(item.lifecycle_ref) for item in seals]}
    core.catalog.validate_instance(REJECTION, category="object", instance=value)
    tx.prewrite(object_type=REJECTION, logical_id=rejection_ref.entity_id,
        version_id=rejection_ref.version_id, payload=canonical_json(value), metadata=value,
        media_type="application/json", schema_ref="registry_v1/" + REJECTION)
    lease_ref = _version_from_payload(state["lease_ref"])
    for kind, ref, stream, aggregate_type in (
            (FIRING_EVENT, firing_ref, "transition-firing:", "transition_firing"),
            (OPERATION_EVENT, lease_ref, "operation-execution:", "operation_execution_lease")):
        tx.append(PendingEvent(event_type=kind, criticality="authoritative",
            stream_id=stream + str(ref.entity_id), aggregate_id=str(ref.entity_id),
            aggregate_type=aggregate_type, idempotency_key=tx.idempotency_key,
            command_id=tx.idempotency_key, payload=value,
            payload_schema_ref="registry_v1/" + kind,
            producer_invocation_id=invocation_ref.entity_id))
    lineage = [owner_command_ref, firing_ref, invocation_ref, lease_ref, expected_parent_checkpoint_ref]
    lineage.extend(_version_from_payload(child[name]) for child in state["children"]
                   for name in ("execution_instance_ref", "terminal_checkpoint_ref"))
    lineage.extend(item.lifecycle_ref for item in seals)
    for index, ref in enumerate(lineage):
        tx.relate(TypedRelation(_stable_id("relation", tx.idempotency_key, "rejection", str(index)),
            "derived_from", rejection_ref, ref, "strong", {"role": "rejection_closure"}), system_owned=True)
    # The locked inventory validation detects any child continuation. The
    # closed root then remains the original lasting I/O gate.
    tx.reject_firing(firing_ref=firing_ref, invocation_ref=invocation_ref,
        owner_command_ref=owner_command_ref,
        expected_parent_checkpoint_ref=expected_parent_checkpoint_ref,
        registered_completion_event_id=registered_completion_event_id,
        rejection_ref=rejection_ref)
    return rejection_ref


__all__ = ("stage_material_firing_rejection",)
