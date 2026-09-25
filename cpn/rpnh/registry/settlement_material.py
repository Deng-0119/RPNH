"""Exact token material and produced-resource settlement inventory."""
from __future__ import annotations

import json
from typing import Any
from ._registry import _RegistryCore
from .identities import TypedId
from .models import VersionRef, TypedRelation
from .publication import _ref_payload, _stable_id
from .resource_service import _resource_payload
from .resources import (ExecutableNetAuthority, PetriTokenState, ResourceVersionRef)


def petri_token_metadata(
        executable: ExecutableNetAuthority,
        state: PetriTokenState,
        ref: VersionRef,
) -> dict[str, Any]:
    def resource(
            value: ResourceVersionRef | None,
    ) -> dict[str, str] | None:
        return _resource_payload(value) if value is not None else None

    warning = state.override_warning
    continuation = state.continuation
    return {
        "petri_token_ref": _ref_payload(ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "token_id": state.token_id,
        "place": state.place,
        "epoch": state.epoch,
        "producer": state.producer,
        "consumer": state.consumer,
        "resource_ref": resource(state.resource_ref),
        "work_resource_ref": resource(state.work_resource_ref),
        "kind": state.kind,
        "consumed_by": state.consumed_by,
        "override_warning": ({
            "reason": warning.reason,
            "gate_transition_id": warning.gate_transition_id,
            "gate_peer_transition_id": warning.gate_peer_transition_id,
            "overridden_attempt": warning.overridden_attempt,
            "escalation_provenance": warning.escalation_provenance,
            "gate_output_place": warning.gate_output_place,
        } if warning is not None else None),
        "verdict": state.verdict,
        "continuation": ({
            "round": continuation.round,
            "source_ref": resource(continuation.source_ref),
        } if continuation is not None else None),
        "lease_identity_ref": (
            _ref_payload(state.lease_identity_ref)
            if state.lease_identity_ref is not None else None),
        "lease_claims": [{
            "lease_identity_ref": _ref_payload(
                claim.lease_identity_ref),
            "expected_resource_ref": resource(
                claim.expected_resource_ref),
            "access_mode": claim.access_mode,
            **({"staging_place": claim.staging_place}
               if claim.staging_place is not None else {}),
        } for claim in state.lease_claims],
    }


def register_firing_production_inventory(
        core: _RegistryCore, transaction: Any, *, invocation_ref: VersionRef,
        firing_ref: VersionRef, settlement_ref: VersionRef,
        checkpoint_ref: VersionRef, idempotency_key: str,
) -> tuple[ResourceVersionRef, ...]:
    """Attach every immutable Petri-produced version to the settlement."""

    produced: list[ResourceVersionRef] = []
    for row in core.event_store.object_rows_by_producer(
            invocation_ref.entity_id,
            object_types=("resource_version/v1",)):
        metadata = json.loads(str(row["metadata_json"]))
        if (metadata.get("origin_kind") != "petri_output"
                or metadata.get("producer_ref")
                != _ref_payload(invocation_ref)
                or metadata.get("net_ref") is None):
            continue
        produced.append(ResourceVersionRef(
            TypedId.parse(str(row["logical_id"]), expected="resource"),
            TypedId.parse(
                str(row["version_id"]), expected="resource_version")))
    inventory = tuple(sorted(set(produced), key=lambda ref: (
        str(ref.resource_id), str(ref.resource_version_id))))
    for resource_ref in inventory:
        for source, role in (
                (settlement_ref, "firing_settlement"),
                (checkpoint_ref, "successor_checkpoint")):
            transaction.relate(TypedRelation(
                _stable_id(
                    "relation", idempotency_key, role,
                    "produced-resource-occurrence",
                    resource_ref.resource_version_id),
                "derived_from", source,
                resource_ref.as_version_ref(), metadata={
                    "transition_firing_ref": _ref_payload(firing_ref),
                    "logical_resource_id": str(resource_ref.resource_id),
                    "exact_resource_version": True,
                }), producer_invocation_id=invocation_ref.entity_id)
    return inventory


__all__ = ('petri_token_metadata', 'register_firing_production_inventory')
