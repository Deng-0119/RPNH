"""Initial marking publication shared by Module and the current launch path.

The owner supplies real registered token occurrences, not a reconstructed
checkpoint projection. No firing, settlement or terminal authority is created.
"""
from __future__ import annotations

from ._registry import _RegistryCore
from .event_store import validate_registered_net_closure, verified_checkpoint_head
from .models import PendingEvent, VersionRef
from .schema_catalog import canonical_json
from .strict_contracts import _registered, ref_payload


def publish_initial_checkpoint(core: _RegistryCore, *, net_ref: VersionRef,
                               root_ref: VersionRef,
                               checkpoint_ref: VersionRef,
                               token_refs: tuple[VersionRef, ...],
                               workspace_revision_refs: tuple[VersionRef, ...],
                               idempotency_key: str) -> VersionRef:
    """Commit one exact initial checkpoint through normal Registry authority."""
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("initial checkpoint requires the execution owner's Registry")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("initial checkpoint requires an explicit command key")
    net = validate_registered_net_closure(core.event_store, core.catalog, net_ref)
    if net["team_design_root_ref"] != ref_payload(root_ref):
        raise ValueError("initial checkpoint root differs from exact net closure")
    if len(set(token_refs)) != len(token_refs):
        raise ValueError("initial checkpoint contains duplicate occurrences")
    ordinals = set()
    for ref in token_refs:
        _, token = _registered(core, ref, "petri_token/v1")
        if (token["petri_token_ref"] != ref_payload(ref)
                or token["net_instance_ref"] != ref_payload(net_ref)
                or token["epoch"] != 0 or token["consumed_by"] is not None
                or token["token_id"] in ordinals):
            raise ValueError("initial token identity/net/epoch/ordinal differs")
        ordinals.add(token["token_id"])
    if ordinals != set(range(len(token_refs))):
        raise ValueError("initial tokens require exact contiguous owner allocations")
    for ref in workspace_revision_refs:
        _registered(core, ref, "workspace_revision/v1")
    if (not isinstance(checkpoint_ref, VersionRef)
            or checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or checkpoint_ref.entity_id.kind != "marking_checkpoint"
            or checkpoint_ref.version_id.kind != "marking_checkpoint_version"):
        raise ValueError("initial checkpoint requires an exact owner allocation")
    sorted_tokens = sorted((ref_payload(ref) for ref in token_refs), key=canonical_json)
    # Preserve explicit workspace order: it is a current execution authority,
    # not a schema or business topology selector.
    workspaces = [ref_payload(ref) for ref in workspace_revision_refs]
    checkpoint = {"marking_checkpoint_ref": ref_payload(checkpoint_ref),
        "net_instance_ref": ref_payload(net_ref), "team_design_root_ref": ref_payload(root_ref),
        "epoch": 0, "next_token_id": len(token_refs), "attempts": [],
        "token_refs": sorted_tokens, "settled": True, "previous_checkpoint_ref": None,
        "settlement_delta_ref": None, "transition_firing_refs": [],
        "workspace_revision_refs": workspaces}
    core.catalog.validate_instance("marking_checkpoint/v1", category="object", instance=checkpoint)
    tx = core.begin(idempotency_key=idempotency_key, net_instance_id=net_ref.entity_id)
    tx.prewrite(object_type="marking_checkpoint/v1", logical_id=checkpoint_ref.entity_id,
        version_id=checkpoint_ref.version_id, payload=canonical_json(checkpoint),
        metadata=checkpoint, media_type="application/json", schema_ref="registry_v1/marking_checkpoint/v1")
    payload = {key: value for key, value in checkpoint.items()
               if key not in {"marking_checkpoint_ref", "epoch", "next_token_id", "attempts", "token_refs"}}
    payload["checkpoint_ref"] = ref_payload(checkpoint_ref)
    tx.append(PendingEvent(event_type="marking_checkpoint_committed/v1", criticality="authoritative",
        stream_id=f"marking:{net_ref.entity_id}", aggregate_id=str(net_ref.entity_id),
        aggregate_type="marking_checkpoint", idempotency_key=idempotency_key,
        command_id=idempotency_key, payload=payload,
        payload_schema_ref="registry_v1/marking_checkpoint_committed/v1", task_control=True))
    tx.commit()
    if verified_checkpoint_head(core.event_store, core.catalog, core.task_id, net_ref) != checkpoint_ref:
        raise ValueError("initial checkpoint differs from exact Registry head")
    return checkpoint_ref


__all__ = ("publish_initial_checkpoint",)
