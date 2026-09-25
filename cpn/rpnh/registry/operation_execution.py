"""Sole ordinary operation Start producer shared with the current launcher."""
from __future__ import annotations

from dataclasses import replace
from ._registry import _RegistryCore
from .event_store import verified_adoption_head, validate_registered_net_closure
from .errors import ResourceIntegrityFault
from .invocations import InvocationLifecycle
from .models import VersionRef
from .operations import (RegisteredOperationAuthority, OperationExecutionAuthority,
    hydrate_operation_authority)
from .publication import _ref_payload
from .resource_service import _ResourceServiceKernel, _resource_payload
from .resources import (RegistryHead, ResourceVersionRef, ExecutableNetAuthority,
    PetriInputArtifact, HistoricalPetriInputArtifact, NativeLaunchRegisteredArtifact,
    StructuralGrowthRegisteredArtifact)


def operation_start_payload(
        authority: RegisteredOperationAuthority,
        admission_head: RegistryHead,
        declaration_terminal_delivery_ref: VersionRef | None,
) -> dict[str, object]:
    context = authority.canonical.context
    return {
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_execution_lease_ref": _ref_payload(
            context.operation_execution_lease_ref),
        "operation_spec_ref": _ref_payload(
            authority.spec.operation_spec_ref),
        "operation_binding_ref": _ref_payload(
            authority.operation_binding.operation_binding_ref),
        "transition_firing_ref": _ref_payload(
            authority.firing.transition_firing_ref),
        "executable_transition_binding_ref": _ref_payload(
            authority.transition.binding_ref),
        "agent_ref": (
            _ref_payload(authority.transition.agent_ref)
            if authority.transition.agent_ref is not None else None),
        "declaration_terminal_delivery_ref": (
            _ref_payload(declaration_terminal_delivery_ref)
            if declaration_terminal_delivery_ref is not None else None),
        "principal_ref": _ref_payload(
            authority.operation_binding.principal_ref),
        "authority_decision_ref": _ref_payload(
            authority.operation_binding.authority_decision_ref),
        "input_binding_refs": [
            _ref_payload(item.input_binding_ref)
            for item in authority.inputs],
        "input_resource_refs": [
            _resource_payload(item.resource_ref)
            for item in authority.inputs],
        "claimed_input_refs": [
            _ref_payload(ref) for ref in authority.firing.claimed_input_refs],
        "admission_registry_ordinal": admission_head.ordinal,
        "admission_writer_fencing_epoch": (
            admission_head.writer_fencing_epoch),
        "admission_task_control_sequence": (
            admission_head.task_control_sequence),
    }


def declaration_terminal_delivery_authority(
        declaration_resource_ref: ResourceVersionRef,
        declaration: (
            PetriInputArtifact
            | HistoricalPetriInputArtifact
            | NativeLaunchRegisteredArtifact
            | StructuralGrowthRegisteredArtifact),
) -> VersionRef | None:
    """Return the one declaration-delivery authority valid for Start.

    Receipt-backed declarations retain their exact terminal delivery.  The
    Registry-owned native-launch and structural-growth declarations are
    executable declaration authority, not Petri deliveries, so they carry
    no terminal delivery reference.
    """

    if isinstance(
            declaration, (PetriInputArtifact, HistoricalPetriInputArtifact)):
        receipt = declaration.receipt
        terminal_delivery_ref = receipt.terminal_delivery_ref
        if (declaration.resource.header.ref != declaration_resource_ref
                or receipt.exact_resource_ref != declaration_resource_ref
                or terminal_delivery_ref.entity_type
                != "resource_delivery/v1"):
            raise ResourceIntegrityFault(
                "operation declaration receipt differs from exact resource authority")
        return terminal_delivery_ref
    if isinstance(
            declaration,
            (NativeLaunchRegisteredArtifact,
             StructuralGrowthRegisteredArtifact)):
        if declaration.resource.header.ref != declaration_resource_ref:
            raise ResourceIntegrityFault(
                "operation declaration registered authority names another resource")
        return None
    raise ResourceIntegrityFault(
        "operation declaration lacks current Registry authority")


def start_registered_operation_execution(core: _RegistryCore, kernel: _ResourceServiceKernel,
        repository, authority: RegisteredOperationAuthority, *,
        executable: ExecutableNetAuthority, idempotency_key: str) -> OperationExecutionAuthority:
    """Recheck exact registered input/PN authority and write the normal Start fact."""
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or not isinstance(authority, RegisteredOperationAuthority)
            or not isinstance(executable, ExecutableNetAuthority)
            or not isinstance(idempotency_key, str) or not idempotency_key):
        raise TypeError("operation Start requires the execution owner's exact authorities/key")
    current = hydrate_operation_authority(repository, input_plan=authority.input_plan,
        petri_inputs=tuple(item.artifact for item in authority.inputs))
    if (current.spec != authority.spec or current.operation_binding != authority.operation_binding
            or current.transition != authority.transition):
        raise ResourceIntegrityFault("operation Start closure differs from immutable Registry authority")
    adopted = verified_adoption_head(core.event_store, core.catalog, core.task_id)
    net = validate_registered_net_closure(core.event_store, core.catalog, adopted)
    transitions = tuple(item for item in executable.transitions
        if item.binding_ref == current.transition.binding_ref
        and item.transition_id == current.firing.transition_id
        and item.operation_binding_ref == current.operation_binding.operation_binding_ref)
    if (executable.net_ref != adopted or current.firing.net_ref != adopted
            or current.canonical.context.net_instance_ref != adopted
            or len(transitions) != 1 or transitions[0] != current.transition
            or net["team_design_root_ref"] != _ref_payload(executable.team_design_root_ref)
            or net["team_net_declaration_resource_ref"] != _resource_payload(executable.declaration_resource_ref)):
        raise ResourceIntegrityFault("operation Start is not the exact adopted executable closure")
    declaration_terminal_delivery_ref = declaration_terminal_delivery_authority(
        executable.declaration_resource_ref, executable.declaration)
    context = current.canonical.context
    prior_starts = tuple(
        event for event in core.event_store.list_events_by_aggregate(
            str(context.operation_execution_lease_ref.entity_id),
            event_types=("operation_execution_started/v1",)))
    if (len(prior_starts) == 1
            and prior_starts[0].idempotency_key == idempotency_key):
        prior_payload = prior_starts[0].payload
        try:
            admission_writer_fencing_epoch = int(
                prior_payload["admission_writer_fencing_epoch"])
            admission_task_control_sequence = int(
                prior_payload["admission_task_control_sequence"])
            admission_head = kernel._head(
                ordinal=int(
                    prior_payload["admission_registry_ordinal"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "operation start replay has malformed admission authority") from exc
        if (admission_head.writer_fencing_epoch
                != admission_writer_fencing_epoch
                or admission_head.task_control_sequence
                != admission_task_control_sequence):
            raise ResourceIntegrityFault(
                "operation start replay differs from its admission head")
    else:
        admission_head = kernel._head()
    # The execution authority is the durable result of this admission,
    # not a record of whichever later head happened to rehydrate an
    # idempotent start.  Keep its nested canonical proof at the persisted
    # admission head so a replay reconstructs the same exact authority.
    admission_canonical = replace(
        current.canonical,
        verified_at_head=admission_head,
    )
    current = replace(
        current,
        input_plan=replace(
            current.input_plan,
            canonical=admission_canonical,
        ),
        canonical=admission_canonical,
    )
    event = InvocationLifecycle(
        core)._begin_registered_operation_execution(
        context,
        operation_spec_ref=current.spec.operation_spec_ref,
        transition_firing_ref=current.firing.transition_firing_ref,
        executable_transition_binding_ref=current.transition.binding_ref,
        declaration_terminal_delivery_ref=(
            declaration_terminal_delivery_ref),
        input_binding_refs=tuple(
            item.input_binding_ref for item in current.inputs),
        input_resource_refs=tuple(
            item.resource_ref for item in current.inputs),
        claimed_input_refs=current.firing.claimed_input_refs,
        admission_registry_ordinal=admission_head.ordinal,
        admission_writer_fencing_epoch=(
            admission_head.writer_fencing_epoch),
        admission_task_control_sequence=(
            admission_head.task_control_sequence),
        idempotency_key=idempotency_key,
    )
    return OperationExecutionAuthority(
        operation=current,
        operation_execution_lease_ref=(
            context.operation_execution_lease_ref),
        start_event_id=event.event_id,
        declaration_terminal_delivery_ref=(
            declaration_terminal_delivery_ref),
        admission_head=admission_head,
        verified_at_head=kernel._head(),
    )


def verify_operation_execution(core: _RegistryCore, kernel: _ResourceServiceKernel,
        repository, execution: OperationExecutionAuthority) -> OperationExecutionAuthority:
    """Reverify the exact durable Start fact before an ordinary operation I/O."""
    if not isinstance(execution, OperationExecutionAuthority):
        raise TypeError("operation action requires exact execution authority")
    supplied = execution.operation
    current = hydrate_operation_authority(repository, input_plan=supplied.input_plan,
        petri_inputs=tuple(item.artifact for item in supplied.inputs))
    if (current.spec != supplied.spec or current.operation_binding != supplied.operation_binding
            or current.transition != supplied.transition):
        raise ResourceIntegrityFault("operation action differs from immutable binding/spec authority")
    events = core.event_store.list_events_by_aggregate(
        str(execution.operation_execution_lease_ref.entity_id),
        event_types=("operation_execution_started/v1",))
    expected = operation_start_payload(current, execution.admission_head,
        execution.declaration_terminal_delivery_ref)
    if (len(events) != 1 or events[0].event_id != execution.start_event_id
            or dict(events[0].payload) != expected
            or execution.admission_head.writer_fencing_epoch != kernel._head().writer_fencing_epoch):
        raise ResourceIntegrityFault("operation action Start proof differs from exact Registry fact")
    InvocationLifecycle(core).revalidate_io(current.canonical.context,
        boundary="registered-operation-action")
    return execution


__all__ = ('start_registered_operation_execution', 'verify_operation_execution', 'operation_start_payload',
           'declaration_terminal_delivery_authority')
