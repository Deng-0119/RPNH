"""Same-transaction mechanical publication of one closed firing success.

Current prepares semantic dispositions and workspace plans, and may stage an
adopted net between material staging and publication. This module neither
interprets those policies nor commits/opens a Registry. The completion seam
leaves ordinary occurrence closure in its original transaction/order.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from types import MappingProxyType
from typing import Any, Mapping

from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .firing_authority import verify_transition_firing
from .models import PendingEvent, TypedRelation, VersionRef
from .operations import RegisteredOperationOutputsAuthority
from .operation_output_contract import validate_compiled_output_bundle
from ..executable_net import load_compiled_net
from .publication import _ref_payload, _resource_from_payload, _stable_id, _version_from_payload
from .resource_service import _ResourceServiceKernel
from .resources import ExecutableNetAuthority, FiringSettlementAuthority, PetriTokenState, TypedMarkingAuthority
from .run_authority import stage_run_execution_checkpoint
from .schema_catalog import canonical_json
from .settlement_material import petri_token_metadata, register_firing_production_inventory
from .transaction import RegistryTransaction
from ..marking import (
    PetriMarkingDelta,
    derive_petri_marking_delta,
    verify_petri_marking_delta,
)


@dataclass(frozen=True, slots=True)
class SuccessMaterial:
    settlement_delta_ref: VersionRef
    checkpoint_ref: VersionRef
    checkpoint: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SuccessPublication:
    completion_ref: VersionRef
    completion: Mapping[str, Any]
    event_ref: VersionRef
    transaction_ref: VersionRef
    workspace_revision_ref: VersionRef | None


def _check_success_authority(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        tx: RegistryTransaction, executable: ExecutableNetAuthority,
        prior_marking: TypedMarkingAuthority, projected: TypedMarkingAuthority,
        new_tokens: tuple[tuple[VersionRef, PetriTokenState], ...],
        formal_delta: PetriMarkingDelta,
        settlement: FiringSettlementAuthority,
        operation_outputs: RegisteredOperationOutputsAuthority,
        result_metadata: Mapping[str, Any], terminal_ready_payload: Mapping[str, Any],
        *, idempotency_key: str,
) -> None:
    """Check mechanical authority only; colours/dispositions are prepared data."""
    operation = operation_outputs.execution.operation
    context = settlement.canonical.context
    firing = operation.firing
    exact = verify_transition_firing(core, kernel, settlement.canonical)
    prior = kernel._exact_object(
        prior_marking.checkpoint_ref, expected_type="marking_checkpoint/v1")
    before = set(prior_marking.token_refs)
    after = set(projected.token_refs)
    deposited = tuple(ref for ref, _state in new_tokens)
    consumed = before - after
    expected_delta = derive_petri_marking_delta(
        prior_marking, projected, new_tokens)
    verify_petri_marking_delta(
        prior_marking, projected, new_tokens, formal_delta)
    if (replace(firing, verified_at_head=exact.verified_at_head) != exact
            or operation.canonical.context != context
            or context.task_ref.entity_id != core.task_id
            or tx.task_id != core.task_id
            or tx.task_round_id != context.task_round_ref.entity_id
            or tx.net_instance_id != executable.net_ref.entity_id
            or tx.idempotency_key != idempotency_key
            or context.net_instance_ref != executable.net_ref
            or prior_marking.net_ref != executable.net_ref
            or projected.net_ref != executable.net_ref
            or prior.metadata.get("marking_checkpoint_ref")
            != _ref_payload(prior_marking.checkpoint_ref)
            or prior.metadata.get("net_instance_ref") != _ref_payload(executable.net_ref)
            or prior.metadata.get("token_refs")
            != [_ref_payload(ref) for ref in prior_marking.token_refs]
            or len(before) != len(prior_marking.token_refs)
            or len(after) != len(projected.token_refs)
            or len(set(deposited)) != len(deposited)
            or set(deposited) != after - before
            or (before - consumed) | set(deposited) != after
            or set(item.token_ref for item in projected.tokens) != after
            or any(state.token_ref != ref for ref, state in new_tokens)):
        raise ResourceIntegrityFault("success material differs from exact firing/marking authority")
    if formal_delta != expected_delta:
        raise ResourceIntegrityFault(
            "success material differs from its canonical formal Petri delta")
    binding = operation.operation_binding
    if (binding.operation_binding_ref != firing.operation_binding_ref
            or binding.operation_spec_ref != operation.spec.operation_spec_ref):
        raise ResourceIntegrityFault("success operation differs from its exact binding/spec")
    kernel._exact_object(binding.operation_binding_ref, expected_type="operation_binding/v1")
    kernel._exact_object(binding.operation_spec_ref, expected_type="operation_spec/v1")
    ports = {port.port_id: port for port in operation.spec.output_ports}
    bindings = {item.port_id: item for item in binding.output_port_bindings}
    counts = dict.fromkeys(ports, 0)
    for item in operation_outputs.outputs:
        port = ports.get(item.port_id)
        bound = bindings.get(item.port_id)
        resource = kernel._exact_object(
            item.resource_ref.as_version_ref(), expected_type="resource_version/v1")
        output = kernel._exact_object(
            item.output_binding_ref, expected_type="output_binding/v1")
        kernel._exact_object(item.schema_ref)
        origin = resource.metadata.get("origin", {})
        if (port is None or bound is None
                or item.schema_ref != port.schema_ref
                or item.output_binding_ref != bound.output_binding_ref
                or item.place != bound.place or item.place_ref != bound.place_ref
                or item.artifact.header.content_schema_ref != port.content_schema_id
                or item.artifact.header.task_ref != context.task_ref
                or resource.metadata.get("content_schema_ref") != port.content_schema_id
                or resource.metadata.get("content_schema_authority_ref")
                != output.metadata.get("content_schema_ref")
                or resource.metadata.get("producer_ref") != _ref_payload(context.invocation_ref)
                or origin.get("kind") != "petri_output"
                or origin.get("primary_ref") != _ref_payload(item.output_binding_ref)
                or output.metadata.get("place") != item.place
                or output.metadata.get("place_ref") != _ref_payload(item.place_ref)):
            raise ResourceIntegrityFault("success product violates exact output interface")
        counts[item.port_id] += 1
    if any(
            count and not ports[port_id].minimum <= count <= ports[port_id].maximum
            for port_id, count in counts.items()):
        raise ResourceIntegrityFault("success products violate declared cardinality")
    declared_binding = kernel._exact_object(operation.transition.binding_ref,
        expected_type="executable_transition_binding/v1")
    declaration_ref = _resource_from_payload(declared_binding.metadata["declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(kernel._read_firing_registered(context, declaration_ref)))
    declared_transition = next(item for item in compiled.symbolic.transitions if item.name == firing.transition_id)
    declared_operation = next(item for item in compiled.operations if item.declaration.name == declared_transition.operation)
    symbolic_ports = {port.port_id: port.name for port in compiled.ports}
    try:
        selected = validate_compiled_output_bundle(declared_operation.declaration,
            {symbolic_ports[port_id]: count for port_id, count in counts.items()},
            declared_outcomes=tuple(item.verdict for item in operation_outputs.outputs),
            selected_outcome_id=operation_outputs.selected_outcome_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault("Success violates the exact registered whole output bundle") from exc
    if (selected != operation_outputs.selected_outcome_id
            or declared_operation.operation_id != operation.spec.operation_id
            or declared_operation.executor_key != operation.spec.executor_key
            or declared_binding.metadata["declaration_schema_ref"] != compiled.schema_version):
        raise ResourceIntegrityFault("Success bundle differs from executable/spec declaration identity")
    output_refs = tuple(sorted(
        (item.resource_ref.as_version_ref() for item in operation_outputs.outputs),
        key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    expected = {
        "operation_result_ref": _ref_payload(settlement.operation_result_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(firing.transition_firing_ref),
        "output_resource_refs": [_ref_payload(ref) for ref in output_refs],
    }
    if (len(set(output_refs)) != len(output_refs)
            or any(result_metadata.get(key) != value for key, value in expected.items())
            or any(terminal_ready_payload.get(key) != value for key, value in expected.items()
                   if key != "transition_firing_ref")
            or terminal_ready_payload.get("operation_execution_lease_ref")
            != _ref_payload(context.operation_execution_lease_ref)
            or any(terminal_ready_payload.get(key) != result_metadata.get(key)
                   for key in ("business_outcome", "workspace_access_set_ref",
                               "provider_attempt_evidence_refs"))):
        raise ResourceIntegrityFault("success result/terminal-ready differ from exact products")
    core.catalog.validate_instance(
        "operation_terminal_ready/v1", category="event", instance=dict(terminal_ready_payload))


def stage_success_material(
        core: _RegistryCore, kernel: _ResourceServiceKernel, tx: RegistryTransaction, *,
        executable: ExecutableNetAuthority, prior_marking: TypedMarkingAuthority,
        projected: TypedMarkingAuthority,
        new_tokens: tuple[tuple[VersionRef, PetriTokenState], ...],
        formal_delta: PetriMarkingDelta,
        settlement: FiringSettlementAuthority,
        operation_outputs: RegisteredOperationOutputsAuthority,
        result_metadata: Mapping[str, Any], terminal_ready_payload: Mapping[str, Any],
        workspace_plans: tuple[Mapping[str, Any], ...],
        disposition_relations: tuple[TypedRelation, ...],
        publish_checkpoint: bool, idempotency_key: str,
        declared_effects: Mapping[str, Any] | None = None,
        revision=None,
        historical_lease_writer_epoch: int | None = None,
) -> SuccessMaterial:
    _check_success_authority(
        core, kernel, tx, executable, prior_marking, projected, new_tokens,
        formal_delta,
        settlement, operation_outputs, result_metadata, terminal_ready_payload,
        idempotency_key=idempotency_key)
    canonical = settlement.canonical
    context = canonical.context
    firing = operation_outputs.execution.operation.firing
    settlement_delta_ref = VersionRef(
        "marking_delta/v1",
        _stable_id("marking_delta", idempotency_key, "batch"),
        _stable_id("marking_delta_version", idempotency_key, "batch"))
    checkpoint_ref = VersionRef(
        "marking_checkpoint/v1",
        _stable_id("marking_checkpoint", idempotency_key, "batch"),
        _stable_id(
            "marking_checkpoint_version", idempotency_key, "batch"))
    ordinary_projected = projected
    if revision is not None:
        if revision.ordinary_projected != projected or revision.ordinary_new_tokens != new_tokens:
            raise ResourceIntegrityFault("revision differs from its ordinary PN Success projection")
        projected = revision.projected
    final_refs = ordinary_projected.token_refs
    prior_refs = set(prior_marking.token_refs)
    final_ref_set = set(final_refs)
    workspace_revision_refs = tuple(
        plan["final_ref"] for plan in workspace_plans)
    if not workspace_plans:
        predecessor = kernel._exact_object(
            prior_marking.checkpoint_ref,
            expected_type="marking_checkpoint/v1")
        workspace_revision_refs = tuple(
            _version_from_payload(value)
            for value in predecessor.metadata["workspace_revision_refs"])
    delta = {
        "marking_delta_ref": _ref_payload(settlement_delta_ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "transition_firing_refs": [
            _ref_payload(firing.transition_firing_ref)],
        "operation_binding_refs": [
            _ref_payload(firing.operation_binding_ref)],
        "consumed_refs": [
            _ref_payload(ref) for ref in formal_delta.consumed_token_refs],
        "deposited_refs": [
            _ref_payload(ref) for ref in formal_delta.deposited_token_refs],
        "phase": "settlement",
    }
    if declared_effects is not None:
        delta["declared_effects"] = dict(declared_effects)
        if revision is not None:
            delta["declared_effects"]["effects"] = [
                dict(effect,
                    activation_witnesses=list(revision.activation_witnesses),
                    petri_structure_delta=revision.witness["petri_structure_delta"])
                if effect["effect_kind"] == "structural_revision" else effect
                for effect in declared_effects["effects"]]
    checkpoint = {
        "marking_checkpoint_ref": _ref_payload(checkpoint_ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "team_design_root_ref": _ref_payload(
            executable.team_design_root_ref),
        "epoch": projected.epoch,
        "next_token_id": projected.next_token_id,
        "attempts": [{
            "transition_id": item.transition_id,
            "highest_issued": item.highest_issued,
        } for item in projected.attempts],
        "token_refs": [_ref_payload(ref) for ref in final_refs],
        "settled": True,
        "previous_checkpoint_ref": _ref_payload(
            prior_marking.checkpoint_ref),
        "settlement_delta_ref": _ref_payload(settlement_delta_ref),
        "transition_firing_refs": [
            _ref_payload(firing.transition_firing_ref)],
        "workspace_revision_refs": [
            _ref_payload(ref) for ref in workspace_revision_refs],
    }
    if revision is not None:
        checkpoint.update(net_instance_ref=_ref_payload(revision.executable.net_ref),
            team_design_root_ref=_ref_payload(revision.executable.team_design_root_ref),
            token_refs=[_ref_payload(ref) for ref in projected.token_refs])
    core.catalog.validate_instance(
        "marking_delta/v1", category="object", instance=delta)
    core.catalog.validate_instance(
        "marking_checkpoint/v1", category="object", instance=checkpoint)
    result_ref = settlement.operation_result_ref
    result_metadata = dict(result_metadata)
    core.catalog.validate_instance(
        "operation_result/v1", category="object",
        instance=result_metadata)
    tx.prewrite(
        object_type="operation_result/v1",
        logical_id=result_ref.entity_id,
        version_id=result_ref.version_id,
        payload=canonical_json(result_metadata),
        metadata=result_metadata,
        media_type="application/json",
        schema_ref="registry_v1/operation_result/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id(
            "relation", idempotency_key, "atomic-terminal-result"),
        "terminal_result_of_invocation", result_ref,
        context.invocation_ref),
        producer_invocation_id=context.invocation_ref.entity_id)
    for index, (label, value) in enumerate(((
            "workspace_access_set",
            result_metadata["workspace_access_set_ref"]),)):
        if value is None:
            continue
        tx.relate(TypedRelation(
            _stable_id(
                "relation", idempotency_key,
                "atomic-terminal-strong", index),
            "derived_from", result_ref, _version_from_payload(value),
            metadata={"role": label}),
            producer_invocation_id=context.invocation_ref.entity_id)
    ready_event: PendingEvent = PendingEvent(
        event_type="operation_terminal_ready/v1",
        criticality="authoritative",
        stream_id=f"invocation:{context.invocation_ref.entity_id}",
        aggregate_id=str(context.invocation_ref.entity_id),
        aggregate_type="invocation",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=dict(terminal_ready_payload),
        payload_schema_ref="registry_v1/operation_terminal_ready/v1",
        task_control=True,
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id)
    if historical_lease_writer_epoch is not None:
        if (isinstance(historical_lease_writer_epoch, bool)
                or historical_lease_writer_epoch < 1
                or context.own_transition_firing_ref is None):
            raise ResourceIntegrityFault(
                "historical settlement requires one exact stale firing lease")
        ready_event = (
            core.event_store
            ._authorize_historical_mechanical_terminal_ready_event(
                ready_event,
                task_id=tx.task_id,
                branch_id=tx.branch_id,
                task_round_id=context.task_round_ref.entity_id,
                net_instance_id=context.net_instance_ref.entity_id,
                transaction_id=tx.transaction_id,
                writer_epoch=tx.writer_epoch,
                lease_writer_epoch=historical_lease_writer_epoch,
                task_ref=context.task_ref,
                task_round_ref=context.task_round_ref,
                net_instance_ref=context.net_instance_ref,
                invocation_ref=context.invocation_ref,
                firing_ref=context.own_transition_firing_ref,
                lease_ref=context.operation_execution_lease_ref,
                operation_result_ref=result_ref,
            ))
    tx.append(ready_event)
    for plan in workspace_plans:
        if plan["final_payload"] is None:
            continue
        final_ref = plan["final_ref"]
        final_metadata = plan["final_metadata"]
        tx.prewrite(
            object_type="workspace_revision/v1",
            logical_id=final_ref.entity_id,
            version_id=final_ref.version_id,
            payload=plan["final_payload"],
            metadata=final_metadata,
            media_type="application/x-tar",
            schema_ref="registry_v1/workspace_revision/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.relate(TypedRelation(
            _stable_id(
                "relation", idempotency_key, "workspace-final-parent",
                final_ref.version_id),
            "derived_from", final_ref, plan["expected_head_ref"]),
            producer_invocation_id=context.invocation_ref.entity_id)
        for label, values in (
                ("workspace-final-semantic",
                 final_metadata["semantic_output_refs"]),
                ("workspace-final-trace",
                 final_metadata["trace_summary_refs"])):
            for value in values:
                resource_ref = _resource_from_payload(value)
                tx.relate(TypedRelation(
                    _stable_id(
                        "relation", idempotency_key, label,
                        resource_ref.resource_version_id),
                    "derived_from", final_ref,
                    resource_ref.as_version_ref()),
                    producer_invocation_id=context.invocation_ref.entity_id)
    for ref, state in new_tokens:
        metadata = petri_token_metadata(executable, state, ref)
        core.catalog.validate_instance(
            "petri_token/v1", category="object", instance=metadata)
        tx.prewrite(
            object_type="petri_token/v1",
            logical_id=ref.entity_id,
            version_id=ref.version_id,
            payload=canonical_json(metadata),
            metadata=metadata,
            media_type="application/json",
            schema_ref="registry_v1/petri_token/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
    if revision is not None:
        for ref, state in revision.new_tokens:
            metadata = petri_token_metadata(revision.executable, state, ref)
            core.catalog.validate_instance("petri_token/v1", category="object", instance=metadata)
            tx.prewrite(object_type="petri_token/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/petri_token/v1", producer_invocation_id=context.invocation_ref.entity_id)
        for relation in revision.relations:
            tx.relate(relation, producer_invocation_id=context.invocation_ref.entity_id)
    for relation in disposition_relations:
        tx.relate(relation)
    checkpoint_objects = (
        (("marking_delta/v1", settlement_delta_ref, delta),)
        if not publish_checkpoint else
        (("marking_delta/v1", settlement_delta_ref, delta),
         ("marking_checkpoint/v1", checkpoint_ref, checkpoint)))
    for object_type, ref, metadata in checkpoint_objects:
        tx.prewrite(
            object_type=object_type,
            logical_id=ref.entity_id,
            version_id=ref.version_id,
            payload=canonical_json(metadata),
            metadata=metadata,
            media_type="application/json",
            schema_ref=f"registry_v1/{object_type}",
            producer_invocation_id=context.invocation_ref.entity_id)
    return SuccessMaterial(
        settlement_delta_ref, checkpoint_ref, MappingProxyType(checkpoint))


def stage_success_publication(
        core: _RegistryCore, tx: RegistryTransaction, *,
        executable: ExecutableNetAuthority, prior_marking: TypedMarkingAuthority,
        settlement: FiringSettlementAuthority,
        operation_outputs: RegisteredOperationOutputsAuthority,
        result_metadata: Mapping[str, Any], material: SuccessMaterial,
        checkpoint_ref: VersionRef, checkpoint: Mapping[str, Any],
        workspace_plans: tuple[Mapping[str, Any], ...],
        idempotency_key: str,
) -> SuccessPublication:
    context = settlement.canonical.context
    firing = operation_outputs.execution.operation.firing
    result_ref = settlement.operation_result_ref
    settlement_delta_ref = material.settlement_delta_ref
    workspace_revision_refs = tuple(plan["final_ref"] for plan in workspace_plans)
    checkpoint = dict(checkpoint)
    core.catalog.validate_instance("marking_checkpoint/v1", category="object", instance=checkpoint)
    if (checkpoint.get("marking_checkpoint_ref") != _ref_payload(checkpoint_ref)
            or checkpoint.get("previous_checkpoint_ref") != _ref_payload(prior_marking.checkpoint_ref)
            or checkpoint.get("settlement_delta_ref") != _ref_payload(settlement_delta_ref)
            or checkpoint.get("transition_firing_refs") != [_ref_payload(firing.transition_firing_ref)]):
        raise ResourceIntegrityFault("success publication differs from its final checkpoint")
    tx.relate(TypedRelation(
        _stable_id("relation", idempotency_key, "batch-delta"),
        "derived_from", settlement_delta_ref,
        prior_marking.checkpoint_ref),
        producer_invocation_id=context.invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", idempotency_key, "batch-checkpoint"),
        "derived_from", checkpoint_ref, settlement_delta_ref),
        producer_invocation_id=context.invocation_ref.entity_id)
    for revision_ref in workspace_revision_refs:
        tx.relate(TypedRelation(
            _stable_id(
                "relation", idempotency_key, "workspace-revision",
                revision_ref.version_id),
            "derived_from", checkpoint_ref, revision_ref),
            producer_invocation_id=context.invocation_ref.entity_id)
    workspace_plan = workspace_plans[0] if workspace_plans else None
    workspace_revision_ref = (
        workspace_plan["final_ref"]
        if workspace_plan is not None else None)
    from ..file_execution_net import (
        execution_parent,
        stage_execution_terminal_mappings,
    )
    stage_execution_terminal_mappings(
        core, tx,
        parent=execution_parent(context),
        operation_result_ref=result_ref,
        successor_checkpoint_ref=checkpoint_ref,
        workspace_revision_ref=workspace_revision_ref,
        business_outcome=str(result_metadata["business_outcome"]),
        idempotency_key=idempotency_key,
    )
    tx.publish_firing(
        firing_ref=firing.transition_firing_ref,
        invocation_ref=context.invocation_ref,
        admission_checkpoint_ref=(
            context.admission_marking_checkpoint_ref),
        expected_parent_checkpoint_ref=prior_marking.checkpoint_ref,
        operation_result_ref=result_ref,
        workspace_lineage_ref=workspace_revision_ref,
        expected_workspace_head_ref=(
            workspace_plan["expected_head_ref"]
            if workspace_plan is not None else None),
        workspace_revision_ref=workspace_revision_ref,
        marking_checkpoint_ref=checkpoint_ref,
        marking_net_ref=_version_from_payload(
            checkpoint["net_instance_ref"]))
    checkpoint_payload = {
        "checkpoint_ref": _ref_payload(checkpoint_ref),
        "net_instance_ref": checkpoint["net_instance_ref"],
        "team_design_root_ref": checkpoint["team_design_root_ref"],
        "previous_checkpoint_ref": checkpoint[
            "previous_checkpoint_ref"],
        "settlement_delta_ref": checkpoint["settlement_delta_ref"],
        "transition_firing_refs": checkpoint[
            "transition_firing_refs"],
        "workspace_revision_refs": checkpoint[
            "workspace_revision_refs"],
        "settled": True,
    }
    tx.append(PendingEvent(
        event_type="marking_checkpoint_committed/v1",
        criticality="authoritative",
        stream_id=(
            "marking:"
            f"{checkpoint['net_instance_ref']['logical_id']}"),
        aggregate_id=str(
            checkpoint["net_instance_ref"]["logical_id"]),
        aggregate_type="marking_checkpoint",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=checkpoint_payload,
        payload_schema_ref="registry_v1/marking_checkpoint_committed/v1",
        task_control=True,
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    completion_ref = VersionRef(
        "firing_completion/v2",
        _stable_id(
            "firing_completion", idempotency_key,
            firing.transition_firing_ref.version_id),
        _stable_id(
            "firing_completion_version", idempotency_key,
            firing.transition_firing_ref.version_id))
    transaction_ref = VersionRef(
        "transaction/v1", tx.transaction_id,
        _stable_id("transaction_version", idempotency_key))
    event_ref = VersionRef(
        "fact_event/v1",
        _stable_id(
            "fact_event", idempotency_key,
            firing.transition_firing_ref.version_id),
        _stable_id(
            "fact_event_version", idempotency_key,
            firing.transition_firing_ref.version_id))
    completion = {
        "firing_completion_ref": _ref_payload(completion_ref),
        "transition_firing_ref": _ref_payload(
            firing.transition_firing_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_result_ref": result_metadata[
            "operation_result_ref"],
        "business_outcome": result_metadata["business_outcome"],
        "workspace_access_set_ref": result_metadata[
            "workspace_access_set_ref"],
        "workspace_revision_ref": (
            _ref_payload(workspace_revision_ref)
            if workspace_revision_ref is not None else None),
        "marking_delta_ref": _ref_payload(settlement_delta_ref),
        "successor_checkpoint_ref": _ref_payload(checkpoint_ref),
        "settled_sequence": tx.next_stream_sequence(
            f"firing-claims:{executable.net_ref.version_id}"),
    }
    register_firing_production_inventory(
        core, tx, invocation_ref=context.invocation_ref,
        firing_ref=firing.transition_firing_ref,
        settlement_ref=completion_ref, checkpoint_ref=checkpoint_ref,
        idempotency_key=idempotency_key)
    return SuccessPublication(
        completion_ref, MappingProxyType(completion), event_ref, transaction_ref,
        workspace_revision_ref)


def stage_success_completion(
        core: _RegistryCore, kernel: _ResourceServiceKernel, tx: RegistryTransaction, *,
        executable: ExecutableNetAuthority, settlement: FiringSettlementAuthority,
        operation_outputs: RegisteredOperationOutputsAuthority,
        result_metadata: Mapping[str, Any], material: SuccessMaterial,
        checkpoint_ref: VersionRef, publication: SuccessPublication,
        idempotency_key: str,
        revision=None,
) -> None:
    context = settlement.canonical.context
    firing = operation_outputs.execution.operation.firing
    settlement_delta_ref = material.settlement_delta_ref
    completion_ref = publication.completion_ref
    completion = dict(publication.completion)
    event_ref = publication.event_ref
    transaction_ref = publication.transaction_ref
    workspace_revision_ref = publication.workspace_revision_ref
    tx.prewrite(
        object_type="firing_completion/v2",
        logical_id=completion_ref.entity_id,
        version_id=completion_ref.version_id,
        payload=canonical_json(completion),
        metadata=completion,
        media_type="application/json",
        schema_ref="registry_v1/firing_completion/v2",
        producer_invocation_id=context.invocation_ref.entity_id)
    settlement_payload = {
        "event_ref": _ref_payload(event_ref),
        "transition_firing_ref": _ref_payload(
            firing.transition_firing_ref),
        "firing_completion_ref": _ref_payload(completion_ref),
        "operation_result_ref": result_metadata[
            "operation_result_ref"],
        "business_outcome": result_metadata["business_outcome"],
        "workspace_access_set_ref": result_metadata[
            "workspace_access_set_ref"],
        "workspace_revision_ref": (
            _ref_payload(workspace_revision_ref)
            if workspace_revision_ref is not None else None),
        "marking_delta_ref": _ref_payload(settlement_delta_ref),
        "successor_checkpoint_ref": _ref_payload(checkpoint_ref),
        "transaction_ref": _ref_payload(transaction_ref),
    }
    tx.append(PendingEvent(
        event_type="transition_firing_settled/v1",
        criticality="authoritative",
        stream_id=f"firing-claims:{executable.net_ref.version_id}",
        aggregate_id=str(firing.transition_firing_ref.entity_id),
        aggregate_type="transition_firing",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=settlement_payload,
        payload_schema_ref="registry_v1/transition_firing_settled/v1",
        task_control=True,
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    stage_run_execution_checkpoint(
        core, kernel, tx, checkpoint_ref,
        idempotency_key=idempotency_key,
        producer_invocation_id=context.invocation_ref.entity_id,
        declaration_ref=None if revision is None else revision.executable.declaration_resource_ref.as_version_ref(),
        declaration_schema_ref=None if revision is None else "rpnh/executable_net/v1",
        mutable_stage_ref=None if revision is None else revision.executable.declaration_resource_ref.as_version_ref(),
    )
