"""Built-in execution nets that authorize durable workspace effects."""

from __future__ import annotations

import json
from collections.abc import Callable

from .registry.errors import ResourceIntegrityFault
from .registry.execution_net import (
    ExecutionInputArc,
    ExecutionNetDefinition,
    ExecutionOutputArc,
    ExecutionParentAuthority,
    ExecutionState,
    ExecutionTransition,
    execution_child_stream_id,
)
from .registry.execution_runtime import ExecutionRuntime
from .registry.identities import TypedId
from .registry.models import PendingEvent, TypedRelation, VersionRef
from .registry.publication import _ref_payload, _stable_id, _version_from_payload
from .registry.schema_catalog import canonical_json


FILE_MATERIALIZATION_NET = ExecutionNetDefinition(
    definition_key="file-materialization-v1",
    places=("map_ready", "pending"),
    transitions=(ExecutionTransition(
        "materialize_file", "idempotent_materialization"),),
    input_arcs=(ExecutionInputArc("pending", "materialize_file", 1),),
    output_arcs=(ExecutionOutputArc("materialize_file", "map_ready", 1),),
    initial_place="pending",
    initial_tokens=1,
    terminal_places=("map_ready",),
)

WORKSPACE_FINALIZATION_NET = ExecutionNetDefinition(
    definition_key="workspace-finalization-v1",
    places=("map_ready", "pending"),
    transitions=(ExecutionTransition(
        "finalize_workspace", "idempotent_materialization"),),
    input_arcs=(ExecutionInputArc("pending", "finalize_workspace", 1),),
    output_arcs=(ExecutionOutputArc(
        "finalize_workspace", "map_ready", 1),),
    initial_place="pending",
    initial_tokens=1,
    terminal_places=("map_ready",),
)


def execution_parent(context: object) -> ExecutionParentAuthority:
    """Bind an execution instance to one exact open business firing."""

    try:
        return ExecutionParentAuthority(
            invocation_ref=context.invocation_ref,
            business_firing_ref=context.own_transition_firing_ref,
            business_net_ref=context.net_instance_ref,
            business_checkpoint_ref=context.admission_marking_checkpoint_ref,
        )
    except AttributeError as exc:
        raise ResourceIntegrityFault(
            "workspace execution lacks exact parent firing authority") from exc


def execute_idempotent_materialization(
        core: object, *, context: object, definition: ExecutionNetDefinition,
        transition_id: str, identity_key: str,
        materialize: Callable[[ExecutionState], tuple[VersionRef, ...]],
) -> tuple[ExecutionState, tuple[VersionRef, ...]]:
    """Recover or run one materialization and settle only with exact evidence."""

    if not isinstance(identity_key, str) or not identity_key:
        raise ResourceIntegrityFault("workspace execution identity is required")
    parent = execution_parent(context)
    runtime = ExecutionRuntime(core)  # type: ignore[arg-type]
    state = runtime.instantiate(
        parent=parent, definition=definition,
        idempotency_key=identity_key)
    if state.checkpoint.map_ready:
        if not state.checkpoint.evidence_refs:
            raise ResourceIntegrityFault(
                "map-ready workspace execution lacks exact evidence")
        return state, state.checkpoint.evidence_refs
    if not state.checkpoint.active_firing_refs:
        state = runtime.start(
            instance_ref=state.instance_ref,
            parent=parent,
            checkpoint_ref=state.checkpoint.checkpoint_ref,
            transition_id=transition_id,
            idempotency_key=f"{identity_key}:start",
            materialization_key=identity_key,
        )
    recoveries = runtime.classify_active_firings(state)
    if (len(recoveries) != 1
            or recoveries[0].transition_id != transition_id
            or recoveries[0].action != "retry_same_materialization"
            or recoveries[0].materialization_key != identity_key):
        raise ResourceIntegrityFault(
            "workspace execution cannot recover a different materialization")
    evidence_refs = materialize(state)
    if (not isinstance(evidence_refs, tuple) or not evidence_refs
            or any(not isinstance(ref, VersionRef) for ref in evidence_refs)
            or len(set(evidence_refs)) != len(evidence_refs)):
        raise ResourceIntegrityFault(
            "workspace materialization requires unique exact evidence")
    state = runtime.settle(
        instance_ref=state.instance_ref,
        parent=parent,
        checkpoint_ref=state.checkpoint.checkpoint_ref,
        firing_ref=recoveries[0].firing_ref,
        idempotency_key=f"{identity_key}:settle",
        evidence_refs=evidence_refs,
    )
    if (not state.checkpoint.map_ready
            or state.checkpoint.evidence_refs != evidence_refs):
        raise ResourceIntegrityFault(
            "workspace execution did not settle with its exact evidence")
    return state, evidence_refs


def execution_states_for_parent(
        core: object, parent: ExecutionParentAuthority,
) -> tuple[ExecutionState, ...]:
    """Hydrate every execution instance owned by one parent firing."""

    rows = core.event_store.object_rows_by_producer(  # type: ignore[attr-defined]
        parent.invocation_ref.entity_id,
        object_types=("execution_instance/v1",))
    refs: list[VersionRef] = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if metadata.get("parent_business_firing_ref") != _ref_payload(
                parent.business_firing_ref):
            continue
        ref = _version_from_payload(metadata.get("execution_instance_ref"))
        if (ref.entity_type != "execution_instance/v1"
                or str(ref.entity_id) != str(row["logical_id"])
                or str(ref.version_id) != str(row["version_id"])):
            raise ResourceIntegrityFault(
                "execution instance row differs from its exact self reference")
        refs.append(ref)
    if len(set(refs)) != len(refs):
        raise ResourceIntegrityFault("parent firing repeats an execution instance")
    runtime = ExecutionRuntime(core)  # type: ignore[arg-type]
    return tuple(runtime.hydrate(ref, parent=parent) for ref in sorted(
        refs, key=lambda value: str(value.version_id)))


def stage_execution_terminal_mappings(
        core: object, tx: object, *, parent: ExecutionParentAuthority,
        operation_result_ref: VersionRef,
        successor_checkpoint_ref: VersionRef,
        workspace_revision_ref: VersionRef | None,
        business_outcome: str,
        idempotency_key: str,
        _normal_snapshot=None,
) -> tuple[VersionRef, ...]:
    """Require map-ready children and stage their mappings in Success."""

    child_stream = execution_child_stream_id(parent)
    tx.expect_current_stream_head(child_stream)
    children = []
    if _normal_snapshot is None:
        for state in execution_states_for_parent(core, parent):
            if not state.checkpoint.map_ready or not state.checkpoint.evidence_refs:
                raise ResourceIntegrityFault(
                    "business Success requires every execution instance map-ready")
            children.append({"execution_instance_ref": _ref_payload(state.instance_ref),
                "execution_checkpoint_ref": _ref_payload(state.checkpoint.checkpoint_ref),
                "evidence_refs": [_ref_payload(ref) for ref in state.checkpoint.evidence_refs]})
    else:
        children = _normal_snapshot["children"]
    mapping_refs: list[VersionRef] = []
    for child in children:
        instance_ref = _version_from_payload(child["execution_instance_ref"])
        mapping_ref = VersionRef(
            "execution_terminal_mapping/v1",
            _stable_id(
                "execution_terminal_mapping", idempotency_key,
                instance_ref.version_id),
            _stable_id(
                "execution_terminal_mapping_version", idempotency_key,
                instance_ref.version_id),
        )
        metadata = {
            "execution_terminal_mapping_ref": _ref_payload(mapping_ref),
            "execution_instance_ref": child["execution_instance_ref"],
            "execution_checkpoint_ref": child["execution_checkpoint_ref"],
            "evidence_refs": child["evidence_refs"],
            "parent_invocation_ref": _ref_payload(parent.invocation_ref),
            "parent_business_firing_ref": _ref_payload(
                parent.business_firing_ref),
            "operation_result_ref": _ref_payload(operation_result_ref),
            "successor_business_checkpoint_ref": _ref_payload(
                successor_checkpoint_ref),
            "workspace_revision_ref": (
                _ref_payload(workspace_revision_ref)
                if workspace_revision_ref is not None else None),
            "business_outcome": business_outcome,
        }
        core.catalog.validate_instance(  # type: ignore[attr-defined]
            "execution_terminal_mapping/v1", category="object",
            instance=metadata)
        tx.prewrite(
            object_type="execution_terminal_mapping/v1",
            logical_id=mapping_ref.entity_id,
            version_id=mapping_ref.version_id,
            payload=canonical_json(metadata), metadata=metadata,
            media_type="application/json",
            schema_ref="registry_v1/execution_terminal_mapping/v1",
            producer_invocation_id=parent.invocation_ref.entity_id,
        )
        targets = [
            ("instance", instance_ref),
            ("checkpoint", _version_from_payload(child["execution_checkpoint_ref"])),
            ("operation-result", operation_result_ref),
            ("successor-checkpoint", successor_checkpoint_ref),
        ]
        targets[2:2] = [
            ("evidence", _version_from_payload(ref)) for ref in child["evidence_refs"]]
        if workspace_revision_ref is not None:
            targets.append(("workspace-revision", workspace_revision_ref))
        for ordinal, (role, target) in enumerate(targets):
            tx.relate(TypedRelation(
                _stable_id(
                    "relation", idempotency_key, "execution-mapping",
                    instance_ref.version_id, ordinal),
                "derived_from", mapping_ref, target,
                metadata={"execution_mapping_role": role}),
                producer_invocation_id=parent.invocation_ref.entity_id)
        mapping_refs.append(mapping_ref)
    tx.append(PendingEvent(
        event_type="execution_children_sealed/v1",
        criticality="authoritative",
        stream_id=child_stream,
        aggregate_id=str(parent.business_firing_ref.entity_id),
        aggregate_type="execution_child_set",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={
            "parent_invocation_ref": _ref_payload(parent.invocation_ref),
            "parent_business_firing_ref": _ref_payload(
                parent.business_firing_ref),
            "execution_instance_refs": [
                child["execution_instance_ref"] for child in children],
            "execution_checkpoint_refs": [
                child["execution_checkpoint_ref"] for child in children],
            "execution_terminal_mapping_refs": [
                _ref_payload(ref) for ref in mapping_refs],
        },
        payload_schema_ref="registry_v1/execution_children_sealed/v1",
        producer_invocation_id=parent.invocation_ref.entity_id,
    ))
    return tuple(mapping_refs)


def stage_normal_execution_child_closure(core, tx, *, parent,
        operation_result_ref, successor_checkpoint_ref, workspace_revision_ref,
        business_outcome, idempotency_key):
    """Opt-in object proof from the original mapping/event producer and TX."""
    from .registry.execution_child_closure import capture_child_snapshot, stage_child_seal
    snapshot = capture_child_snapshot(core, parent)
    mappings = stage_execution_terminal_mappings(core, tx, parent=parent,
        operation_result_ref=operation_result_ref, successor_checkpoint_ref=successor_checkpoint_ref,
        workspace_revision_ref=workspace_revision_ref, business_outcome=business_outcome,
        idempotency_key=idempotency_key, _normal_snapshot=snapshot)
    return stage_child_seal(core, tx, snapshot, mappings, result_ref=operation_result_ref,
        checkpoint_ref=successor_checkpoint_ref, command_id=idempotency_key)


__all__ = (
    "FILE_MATERIALIZATION_NET",
    "WORKSPACE_FINALIZATION_NET",
    "execute_idempotent_materialization",
    "execution_parent",
    "execution_states_for_parent",
    "stage_execution_terminal_mappings",
)
