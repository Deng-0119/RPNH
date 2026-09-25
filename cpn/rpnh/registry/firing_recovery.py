"""Durable executor-return authority and one-firing interruption recovery."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Callable, Mapping

from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .firing_authority import canonical_invocation
from .models import EventEnvelope, PendingEvent, VersionRef
from .operation_execution import operation_start_payload, verify_operation_execution
from .operations import (
    OperationExecutionAuthority,
    RegisteredOperationOutputsAuthority,
    hydrate_operation_authority,
    plan_registered_operation_inputs,
    register_operation_outputs,
)
from .operation_repository import OperationInputResourceSubstitution
from .publication import _ref_payload, _resource_from_payload, _version_from_payload
from .resource_service import _ResourceServiceKernel, _resource_payload
from .resources import HistoricalPetriInputArtifact, ResourceVersionRef


COMPLETION_EVENT_TYPE = "registered_operation_completion_recorded/v1"
COMPLETION_SCHEMA_REF = (
    "registry_v1/registered_operation_completion_recorded/v1")


@dataclass(frozen=True, slots=True)
class RegisteredOperationFiringRecovery:
    """Read-only-classified stale completion, ready for one new writer."""

    outputs: RegisteredOperationOutputsAuthority
    completion_event_id: object
    completion_payload: Mapping[str, object]
    stale_writer_fencing_epoch: int


def registered_operation_completion_payload(
        core: _RegistryCore,
        outputs: RegisteredOperationOutputsAuthority,
) -> dict[str, object]:
    """Canonical material recorded after complete output-bundle validation."""

    execution = outputs.execution
    operation = execution.operation
    context = operation.canonical.context
    raw_run_ref = core.event_store.get_meta("native_run_ref")
    if raw_run_ref is None:
        raise ResourceIntegrityFault(
            "registered-operation completion lacks native run identity")
    try:
        run_ref = _version_from_payload(json.loads(raw_run_ref))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResourceIntegrityFault(
            "registered-operation completion run identity is malformed") from exc
    return {
        "run_ref": _ref_payload(run_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(
            operation.firing.transition_firing_ref),
        "operation_execution_lease_ref": _ref_payload(
            execution.operation_execution_lease_ref),
        "operation_start_event_id": str(execution.start_event_id),
        "firing_admission_ref": _ref_payload(
            operation.firing.firing_admission_ref),
        "net_instance_ref": _ref_payload(context.net_instance_ref),
        "admission_marking_checkpoint_ref": _ref_payload(
            context.admission_marking_checkpoint_ref),
        "operation_spec_ref": _ref_payload(
            operation.spec.operation_spec_ref),
        "operation_binding_ref": _ref_payload(
            operation.operation_binding.operation_binding_ref),
        "selected_outcome_id": outputs.selected_outcome_id,
        "ordered_outputs": [{
            "ordinal": ordinal,
            "port_id": output.port_id,
            "output_binding_ref": _ref_payload(output.output_binding_ref),
            "resource_ref": _resource_payload(output.resource_ref),
        } for ordinal, output in enumerate(outputs.outputs)],
        "admission_writer_fencing_epoch": (
            execution.admission_head.writer_fencing_epoch),
    }


def record_registered_operation_completion(
        core: _RegistryCore, kernel: _ResourceServiceKernel, repository,
        supplied: RegisteredOperationOutputsAuthority, *,
        idempotency_key: str,
) -> EventEnvelope:
    """Record the exact executor-return proof before products leave dispatch."""

    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or not isinstance(supplied, RegisteredOperationOutputsAuthority)
            or not isinstance(idempotency_key, str) or not idempotency_key):
        raise TypeError(
            "registered-operation completion requires owner authorities/key")
    from .firing_success import _verify_closed_operation_outputs

    execution = verify_operation_execution(
        core, kernel, repository, supplied.execution)
    outputs = _verify_closed_operation_outputs(
        kernel, repository, execution, supplied)
    payload = registered_operation_completion_payload(core, outputs)
    core.catalog.validate_instance(
        COMPLETION_EVENT_TYPE, category="event", instance=payload)
    lease_id = str(execution.operation_execution_lease_ref.entity_id)
    prior = tuple(core.event_store.list_events_by_aggregate(
        lease_id, event_types=(COMPLETION_EVENT_TYPE,)))
    if prior:
        if (len(prior) == 1 and dict(prior[0].payload) == payload
                and prior[0].producer_invocation_id
                == execution.operation.canonical.context.invocation_ref.entity_id):
            return prior[0]
        raise ResourceIntegrityFault(
            "registered-operation completion conflicts with durable material")
    context = execution.operation.canonical.context
    tx = core.begin(
        idempotency_key=idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.append(PendingEvent(
        event_type=COMPLETION_EVENT_TYPE,
        criticality="authoritative",
        stream_id=f"operation-execution:{lease_id}",
        aggregate_id=lease_id,
        aggregate_type="operation_execution_lease",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=payload,
        payload_schema_ref=COMPLETION_SCHEMA_REF,
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id,
    ))
    tx.commit()
    recorded = tuple(core.event_store.list_events_by_aggregate(
        lease_id, event_types=(COMPLETION_EVENT_TYPE,)))
    if len(recorded) != 1 or dict(recorded[0].payload) != payload:
        raise ResourceIntegrityFault(
            "registered-operation completion was not recorded exactly once")
    return recorded[0]


def _completion_events(core: _RegistryCore) -> tuple[EventEnvelope, ...]:
    return tuple(event for event in core.event_store.list_events()
                 if event.event_type == COMPLETION_EVENT_TYPE)


def _reject_conflicting_lifecycle(
        core: _RegistryCore, *, invocation_ref: VersionRef,
        firing_ref: VersionRef, lease_ref: VersionRef,
) -> None:
    terminal_types = {
        "operation_terminal_ready/v1", "invocation_settled/v1",
        "transition_firing_settled/v1",
        "invocation_superseded_by_growth_recovery/v1",
        "transition_firing_superseded_by_growth_recovery/v1",
        "operation_dispatch_superseded_by_growth_recovery/v1",
        "invocation_superseded_by_native_resume/v1",
        "transition_firing_superseded_by_native_resume/v1",
        "operation_dispatch_superseded_by_native_resume/v1",
    }
    aggregate_ids = {
        str(invocation_ref.entity_id), str(firing_ref.entity_id),
        str(lease_ref.entity_id),
    }
    for event in core.event_store.list_events():
        if (event.event_type in terminal_types
                and event.aggregate_id in aggregate_ids):
            raise ResourceIntegrityFault(
                "registered-operation recovery has a terminal conflict")


def _has_later_writer_events(core: _RegistryCore, stale_epoch: int) -> bool:
    """Reject a consumed writer generation only when it published facts."""

    return any(
        isinstance(event.writer_fencing_epoch, int)
        and event.writer_fencing_epoch > stale_epoch
        for event in core.event_store.list_events())


def _validate_recovery_shape(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        current_run: Mapping[str, object], expected_event_payload=None,
        expected_firing=None,
) -> tuple[object, EventEnvelope, int]:
    from .module_execution import active_module_firings
    from .module_runtime import hydrate_module_runtime

    executable, _structure, marking = hydrate_module_runtime(core)
    if expected_firing is None:
        active = active_module_firings(
            core, executable.net_ref, require_current_writer=False)
    else:
        with core.event_store.connect() as db:
            rows = db.execute(
                "SELECT firing_version_id FROM firing_publications "
                "WHERE state='PROVISIONAL' AND net_version_id=?",
                (str(executable.net_ref.version_id),),
            ).fetchall()
        active = ((expected_firing,) if (
            len(rows) == 1
            and str(rows[0]["firing_version_id"])
            == str(expected_firing.transition_firing_ref.version_id)) else ())
    if len(active) != 1:
        raise ResourceIntegrityFault(
            "running run recovery requires exactly one active firing")
    firing = active[0]
    invocation_ref = None
    lease_ref = None
    admission = kernel._exact_object(
        firing.firing_admission_ref, expected_type="firing_admission/v1")
    try:
        invocation_ref = _version_from_payload(admission.metadata["invocation_ref"])
        lease_ref = _version_from_payload(
            admission.metadata["operation_execution_lease_ref"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "active firing admission has malformed execution identities") from exc
    lease = kernel._exact_object(
        lease_ref, expected_type="operation_execution_lease/v1")
    stale_epoch = admission.metadata.get("writer_fencing_epoch")
    if (isinstance(stale_epoch, bool) or not isinstance(stale_epoch, int)
            or lease.metadata.get("writer_fencing_epoch") != stale_epoch):
        raise ResourceIntegrityFault(
            "active firing lacks one exact stale lease fence")
    events = tuple(event for event in _completion_events(core)
                   if event.payload.get("transition_firing_ref")
                   == _ref_payload(firing.transition_firing_ref))
    if len(events) != 1:
        raise ResourceIntegrityFault(
            "active firing lacks one exact registered-operation completion")
    event = events[0]
    payload = dict(event.payload)
    if (expected_event_payload is not None
            and payload != dict(expected_event_payload)):
        raise ResourceIntegrityFault(
            "registered-operation recovery material changed after preflight")
    current_epoch = core.event_store.writer_epoch
    epoch_valid = (
        stale_epoch <= current_epoch if core.read_only
        else stale_epoch < current_epoch)
    if (not epoch_valid or _has_later_writer_events(core, stale_epoch)
            or event.writer_fencing_epoch != stale_epoch
            or event.aggregate_id != str(lease_ref.entity_id)
            or event.producer_invocation_id != invocation_ref.entity_id
            or payload.get("invocation_ref") != _ref_payload(invocation_ref)
            or payload.get("transition_firing_ref")
            != _ref_payload(firing.transition_firing_ref)
            or payload.get("operation_execution_lease_ref")
            != _ref_payload(lease_ref)
            or payload.get("firing_admission_ref")
            != _ref_payload(firing.firing_admission_ref)
            or payload.get("net_instance_ref")
            != _ref_payload(executable.net_ref)
            or payload.get("admission_marking_checkpoint_ref")
            != _ref_payload(firing.admission_marking_checkpoint_ref)
            or payload.get("admission_writer_fencing_epoch") != stale_epoch
            or current_run.get("latest_checkpoint_ref")
            != _ref_payload(marking.checkpoint_ref)
            or marking.checkpoint_ref != firing.admission_marking_checkpoint_ref
            or current_run.get("declaration_ref")
            != _ref_payload(
                executable.declaration_resource_ref.as_version_ref())):
        raise ResourceIntegrityFault(
            "registered-operation recovery changed run/net/marking/lease authority")
    current_tokens = {item.token_ref: item for item in marking.tokens}
    if (not set(firing.claimed_input_refs) <= set(current_tokens)
            or any(current_tokens[ref].state.consumed_by is not None
                   for ref in firing.claimed_input_refs)):
        raise ResourceIntegrityFault(
            "registered-operation recovery changed its exact Petri claims")
    _reject_conflicting_lifecycle(
        core, invocation_ref=invocation_ref,
        firing_ref=firing.transition_firing_ref, lease_ref=lease_ref)
    return firing, event, stale_epoch


def classify_registered_operation_recovery(
        core: _RegistryCore, kernel: _ResourceServiceKernel, repository, *,
        current_run: Mapping[str, object],
        historical_input: Callable[
            [VersionRef, ResourceVersionRef], HistoricalPetriInputArtifact],
) -> RegisteredOperationFiringRecovery:
    """Classify and fully hydrate recoverable material on a read-only writer."""

    if (not isinstance(core, _RegistryCore) or not core.read_only
            or current_run.get("status") != "running"
            or current_run.get("terminal_evidence_ref") is not None
            or not callable(historical_input)):
        raise ResourceIntegrityFault(
            "registered-operation recovery requires one running read-only run")
    firing, event, stale_epoch = _validate_recovery_shape(
        core, kernel, current_run=current_run)
    payload = dict(event.payload)
    invocation_ref = _version_from_payload(payload["invocation_ref"])
    canonical = canonical_invocation(
        core, kernel, invocation_ref, require_current_writer=False)
    from .module_runtime import hydrate_module_runtime
    executable, _structure, _marking = hydrate_module_runtime(core)
    transitions = tuple(
        item for item in executable.transitions
        if (item.transition_id == firing.transition_id
            and item.operation_binding_ref == firing.operation_binding_ref))
    if len(transitions) != 1:
        raise ResourceIntegrityFault(
            "recoverable firing lacks one exact executable transition")
    transition = transitions[0]
    start_events = tuple(core.event_store.list_events_by_aggregate(
        str(payload["operation_execution_lease_ref"]["logical_id"]),
        event_types=("operation_execution_started/v1",)))
    if len(start_events) != 1:
        raise ResourceIntegrityFault(
            "recoverable firing lacks one exact operation Start")
    start = start_events[0]
    plan = plan_registered_operation_inputs(
        repository, canonical=canonical, firing=firing,
        transition=transition)
    start_resources = tuple(_resource_from_payload(value)
                            for value in start.payload["input_resource_refs"])
    if tuple(claim.resource_ref for claim in plan.claims) != start_resources:
        start_bindings = tuple(
            _version_from_payload(value)
            for value in start.payload["input_binding_refs"])
        if (len(start_bindings) != len(start_resources)
                or firing.activation_ref is None):
            raise ResourceIntegrityFault(
                "recoverable operation Start has unexplained input material")
        activation = repository.exact_metadata(
            firing.activation_ref,
            expected_type=firing.activation_ref.entity_type)
        substitutions = []
        for binding_ref, resource_ref in zip(start_bindings, start_resources):
            matches = tuple(
                claim for claim in plan.claims
                if binding_ref in {
                    claim.claimed_token_ref,
                    claim.resource_ref.as_version_ref(),
                })
            if len(matches) != 1:
                raise ResourceIntegrityFault(
                    "recoverable operation Start input binding is not exact")
            claim = matches[0]
            if resource_ref == claim.resource_ref:
                continue
            witness_members = tuple(
                name for name, value in activation.items()
                if value == _ref_payload(resource_ref.as_version_ref()))
            header = kernel._firing_header(canonical.context, resource_ref)
            if (len(witness_members) != 1
                    or not isinstance(header.content_schema_ref, str)):
                raise ResourceIntegrityFault(
                    "recoverable operation input substitution lacks exact authority")
            substitutions.append(OperationInputResourceSubstitution(
                claim.port.port_id, resource_ref, witness_members[0],
                header.content_schema_ref))
        plan = plan_registered_operation_inputs(
            repository, canonical=canonical, firing=firing,
            transition=transition,
            input_resource_substitutions=tuple(substitutions))
        if tuple(claim.resource_ref for claim in plan.claims) != start_resources:
            raise ResourceIntegrityFault(
                "recoverable operation Start input material is not reproducible")
    artifacts = tuple(historical_input(invocation_ref, claim.resource_ref)
                      for claim in plan.claims)
    operation = hydrate_operation_authority(
        repository, input_plan=plan, petri_inputs=artifacts)
    admission_head = kernel._head(
        ordinal=int(start.payload["admission_registry_ordinal"]))
    admission_canonical = replace(canonical, verified_at_head=admission_head)
    operation = replace(
        operation,
        input_plan=replace(operation.input_plan, canonical=admission_canonical),
        canonical=admission_canonical,
    )
    execution = OperationExecutionAuthority(
        operation=operation,
        operation_execution_lease_ref=_version_from_payload(
            payload["operation_execution_lease_ref"]),
        start_event_id=start.event_id,
        declaration_terminal_delivery_ref=(
            _version_from_payload(start.payload["declaration_terminal_delivery_ref"])
            if start.payload["declaration_terminal_delivery_ref"] is not None
            else None),
        admission_head=admission_head,
        verified_at_head=kernel._head(),
    )
    if (dict(start.payload) != operation_start_payload(
            operation, admission_head,
            execution.declaration_terminal_delivery_ref)
            or str(start.event_id) != payload["operation_start_event_id"]):
        raise ResourceIntegrityFault(
            "recoverable operation Start differs from completion authority")
    from .resource_verification import verify_resource
    output_artifacts = tuple(verify_resource(
        core, kernel, canonical,
        _resource_from_payload(item["resource_ref"]),
        native_resume=True)
        for item in payload["ordered_outputs"])
    outputs = register_operation_outputs(
        repository, execution, output_artifacts,
        selected_outcome_id=str(payload["selected_outcome_id"]),
        idempotency_key=(
            f"recovery-classify:{execution.operation_execution_lease_ref.version_id}"))
    if registered_operation_completion_payload(core, outputs) != payload:
        raise ResourceIntegrityFault(
            "recoverable outputs/outcome differ from completion authority")
    _compiled, declared = repository.registered_compiled_operation(operation)
    selected = tuple(
        item for item in declared.declaration.outcomes
        if item.name == outputs.selected_outcome_id)
    binding = kernel._exact_object(
        canonical.context.operation_binding_ref,
        expected_type="operation_binding/v1").metadata
    if len(selected) != 1:
        raise ResourceIntegrityFault(
            "recoverable completion lacks one exact declared outcome")
    if selected[0].effects:
        raise ResourceIntegrityFault(
            "recoverable completion has unrecorded declared HOST effects")
    if isinstance(binding.get("workspace_binding_ref"), Mapping):
        raise ResourceIntegrityFault(
            "recoverable completion has an unsettled workspace revision")
    return RegisteredOperationFiringRecovery(
        outputs=outputs,
        completion_event_id=event.event_id,
        completion_payload=payload,
        stale_writer_fencing_epoch=stale_epoch,
    )


def recover_registered_operation_completion(
        core: _RegistryCore, kernel: _ResourceServiceKernel, repository,
        recovery: RegisteredOperationFiringRecovery, *,
        current_run: Mapping[str, object], idempotency_key: str,
        registration=None, candidate_publisher=None,
        workspace_plans=(),
):
    """Settle one exact stale completion under the sole new writer."""

    if (not isinstance(recovery, RegisteredOperationFiringRecovery)
            or core.read_only
            or core.writer_epoch <= recovery.stale_writer_fencing_epoch
            or _has_later_writer_events(
                core, recovery.stale_writer_fencing_epoch)
            or current_run.get("status") != "running"):
        raise ResourceIntegrityFault(
            "registered-operation recovery requires the immediate new writer")
    _firing, event, stale_epoch = _validate_recovery_shape(
        core, kernel, current_run=current_run,
        expected_event_payload=recovery.completion_payload,
        expected_firing=recovery.outputs.execution.operation.firing)
    if (event.event_id != recovery.completion_event_id
            or stale_epoch != recovery.stale_writer_fencing_epoch
            or registered_operation_completion_payload(
                core, recovery.outputs) != dict(recovery.completion_payload)):
        raise ResourceIntegrityFault(
            "registered-operation recovery crossed its preflight authority")
    from .firing_success import (
        _succeed_verified_module_operation,
        _verify_closed_operation_outputs,
    )
    outputs = _verify_closed_operation_outputs(
        kernel, repository, recovery.outputs.execution, recovery.outputs)
    return _succeed_verified_module_operation(
        core, kernel, repository, outputs,
        idempotency_key=idempotency_key,
        registration=registration,
        candidate_publisher=candidate_publisher,
        workspace_plans=tuple(workspace_plans),
        resource_access_writer_epoch=stale_epoch,
    )


__all__ = (
    "COMPLETION_EVENT_TYPE", "RegisteredOperationFiringRecovery",
    "classify_registered_operation_recovery",
    "record_registered_operation_completion",
    "recover_registered_operation_completion",
    "registered_operation_completion_payload",
)
