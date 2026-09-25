"""Execution implementation for the InvocationLifecycle facade."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, Mapping, Sequence

from ...budgets import BudgetContractError, validate_budget_binding, validate_budget_scopes
from ..event_store import RegistryConflict, RegistryCorruptError, StaleWriterError, validate_registered_net_closure, verified_adoption_head, verified_checkpoint_head
from ..identities import TypedId
from ..models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
from ..admission_publication import PreparedFiringAdmissionPublications
from ..invocations import (
    FiringAdmission,
    FiringClaim,
    InvocationAdmissionError,
    InvocationClosedError,
    InvocationContext,
    TerminalResultPackage,
    ToolExecutionContext,
    _AgentTurnAcceptanceClosure,
    _HISTORICAL_MECHANICAL_TERMINAL_READY,
    _HistoricalMechanicalTerminalReadyRequest,
    _ref_from_payload,
    _ref_payload,
    _scope_firing_idempotency_key,
    _stable_id,
    terminal_descendant_event_ids,
)

def revalidate_io(lifecycle, context: InvocationContext, *, boundary: str) -> None:
    """Re-authorize an immutable DTO from current D1-C heads before I/O."""
    if not isinstance(context, InvocationContext):
        raise TypeError("governed I/O requires an explicit InvocationContext")
    if lifecycle.service.writer_epoch != lifecycle.service.event_store.writer_epoch:
        raise StaleWriterError("invocation context is held by a stale writer")
    canonical = lifecycle.hydrate_context(context.invocation_ref)
    if canonical != context:
        raise InvocationAdmissionError("InvocationContext differs from canonical refs")
    if lifecycle._current_active_net_ref() != context.net_instance_ref:
        raise InvocationAdmissionError("invocation does not belong to the active net head")
    if lifecycle._current_budget_ref() != context.budget_witness_ref:
        raise InvocationAdmissionError("invocation carries a stale budget head")
    current_marking_ref = lifecycle._current_marking_ref(context.net_instance_ref)
    lifecycle._require_ref(
        current_marking_ref, "marking_checkpoint/v1",
        "marking_checkpoint_version")
    admission_marking = lifecycle._require_ref(
        context.admission_marking_checkpoint_ref, "marking_checkpoint/v1",
        "marking_checkpoint_version")
    if admission_marking.get("net_instance_ref") != _ref_payload(
            context.net_instance_ref):
        raise InvocationAdmissionError(
            "invocation admission marking belongs to another net")
    invocation_events = list(lifecycle.service.event_store.list_events_by_aggregate(
        str(context.invocation_ref.entity_id),
        event_types=(
            "invocation_started/v1",
            "operation_terminal_ready/v1",
        )))
    if len([event for event in invocation_events
            if event.event_type == "invocation_started/v1"]) != 1:
        raise InvocationAdmissionError("invocation has no unique canonical start")
    if any(event.event_type == "operation_terminal_ready/v1"
           for event in invocation_events):
        raise InvocationClosedError(
            f"invocation is closed to {boundary} I/O")
    if context.origin == "petri_operation":
        firing_events = list(lifecycle.service.event_store.list_events_by_aggregate(
            str(context.own_transition_firing_ref.entity_id),
            event_types=(
                "firing_admitted/v1",
                "transition_firing_settled/v1",
            )))
        if len([event for event in firing_events
                if event.event_type == "firing_admitted/v1"]) != 1:
            raise InvocationAdmissionError(
                "Petri invocation has no unique active firing admission")
        if any(event.event_type == "transition_firing_settled/v1"
               for event in firing_events):
            raise InvocationClosedError("Petri firing is already settled")
        firing_metadata = lifecycle._require_ref(
            context.own_transition_firing_ref, "transition_firing/v1",
            "transition_firing_version")
        current_marking = lifecycle._require_ref(
            current_marking_ref, "marking_checkpoint/v1",
            "marking_checkpoint_version")
        present = {
            str(ref.version_id) for ref in lifecycle._marking_token_refs(current_marking)}
        claimed = set(firing_metadata.get("claimed_input_version_ids", []))
        if not claimed.issubset(present):
            raise InvocationAdmissionError(
                "active firing claim is no longer present in the current marking head")
    for activation_ref in (context.activation_ref,
                           context.authorization_lifetime_activation_ref):
        if activation_ref is None:
            continue
        lifecycle._require_ref(
            activation_ref, activation_ref.entity_type,
            activation_ref.version_id.kind)

def _begin_registered_operation_execution(
        lifecycle, context: InvocationContext, *,
        operation_spec_ref: VersionRef,
        transition_firing_ref: VersionRef,
        executable_transition_binding_ref: VersionRef,
        declaration_terminal_delivery_ref: VersionRef | None,
        input_binding_refs: tuple[VersionRef, ...],
        input_resource_refs: tuple[ResourceVersionRef, ...],
        claimed_input_refs: tuple[VersionRef, ...],
        admission_registry_ordinal: int,
        admission_writer_fencing_epoch: int,
        admission_task_control_sequence: int,
        idempotency_key: str,
) -> EventEnvelope:
    """Registry-internal writer used only by the public Facade start."""
    if (not idempotency_key
            or any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 0 for value in (
                       admission_registry_ordinal,
                       admission_writer_fencing_epoch,
                       admission_task_control_sequence))):
        raise InvocationAdmissionError(
            "operation start requires exact admission ordinals and fence")
    lifecycle.revalidate_io(context, boundary="operation-start")
    for ref, expected_type, version_kind in (
            (operation_spec_ref, "operation_spec/v1", "operation_spec_version"),
            (transition_firing_ref, "transition_firing/v1",
             "transition_firing_version"),
            (executable_transition_binding_ref,
             "executable_transition_binding/v1",
             "executable_transition_binding_version")):
        lifecycle._require_ref(ref, expected_type, version_kind)
    if declaration_terminal_delivery_ref is not None:
        lifecycle._require_ref(
            declaration_terminal_delivery_ref,
            "resource_delivery/v1", "resource_delivery_version")
    for ref in (*input_binding_refs, *claimed_input_refs):
        lifecycle._require_ref(ref, ref.entity_type, ref.version_id.kind)
    for ref in input_resource_refs:
        if not isinstance(ref, ResourceVersionRef):
            raise InvocationAdmissionError(
                "operation start input resources require exact refs")
        lifecycle._require_ref(
            ref.as_version_ref(), "resource_version/v1",
            "resource_version")
    # Inputless registered PN sources have no token claim. Emptiness is
    # accepted only against the actual adopted declaration and admitted
    # firing, not from a caller's empty tuple alone.
    firing_metadata = lifecycle._require_ref(
        transition_firing_ref, "transition_firing/v1", "transition_firing_version")
    if tuple(_ref_from_payload(value) for value in firing_metadata["claimed_input_refs"]) != claimed_input_refs:
        raise InvocationAdmissionError("operation Start claims differ from exact admitted firing")
    if not claimed_input_refs:
        from ..module_runtime import hydrate_module_runtime
        executable, structure, _marking = hydrate_module_runtime(lifecycle.service)
        if (executable.net_ref != context.net_instance_ref
                or firing_metadata["transition_id"] not in structure.transitions
                or structure.inputs_of(firing_metadata["transition_id"])):
            raise InvocationAdmissionError("empty Start claim is not a declared PN source")
    if (context.own_transition_firing_ref != transition_firing_ref
            or len(set(input_binding_refs)) != len(input_binding_refs)
            or len(set(claimed_input_refs)) != len(claimed_input_refs)):
        raise InvocationAdmissionError(
            "operation start refs differ from the admitted invocation")
    payload = {
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_execution_lease_ref": _ref_payload(
            context.operation_execution_lease_ref),
        "operation_spec_ref": _ref_payload(operation_spec_ref),
        "operation_binding_ref": _ref_payload(
            context.operation_binding_ref),
        "transition_firing_ref": _ref_payload(transition_firing_ref),
        "executable_transition_binding_ref": _ref_payload(
            executable_transition_binding_ref),
        "agent_ref": (
            _ref_payload(context.agent_ref)
            if context.agent_ref is not None else None),
        "declaration_terminal_delivery_ref": (
            _ref_payload(declaration_terminal_delivery_ref)
            if declaration_terminal_delivery_ref is not None else None),
        "principal_ref": _ref_payload(context.principal_ref),
        "authority_decision_ref": _ref_payload(
            context.authority_decision_ref),
        "input_binding_refs": [
            _ref_payload(ref) for ref in input_binding_refs],
        "input_resource_refs": [{
            "resource_id": str(ref.resource_id),
            "resource_version_id": str(ref.resource_version_id),
        } for ref in input_resource_refs],
        "claimed_input_refs": [
            _ref_payload(ref) for ref in claimed_input_refs],
        "admission_registry_ordinal": admission_registry_ordinal,
        "admission_writer_fencing_epoch": admission_writer_fencing_epoch,
        "admission_task_control_sequence": admission_task_control_sequence,
    }
    prior = list(lifecycle.service.event_store.list_events_by_aggregate(
        str(context.operation_execution_lease_ref.entity_id),
        event_types=("operation_execution_started/v1",)))
    if prior:
        if (len(prior) != 1 or prior[0].idempotency_key != idempotency_key
                or dict(prior[0].payload) != payload):
            raise InvocationAdmissionError(
                "operation execution lease already has another start")
        return prior[0]
    tx = lifecycle.service.begin(
        idempotency_key=idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.append(PendingEvent(
        event_type="operation_execution_started/v1", criticality="authoritative",
        stream_id=f"operation-lease:{context.operation_execution_lease_ref.entity_id}",
        aggregate_id=str(context.operation_execution_lease_ref.entity_id),
        aggregate_type="operation_execution_lease",
        idempotency_key=idempotency_key, command_id=idempotency_key,
        payload=payload,
        payload_schema_ref="registry_v1/operation_execution_started/v1",
        task_control=True, producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    committed = tx.commit()
    started = tuple(
        event for event in committed
        if event.event_type == "operation_execution_started/v1")
    if len(started) != 1:
        raise InvocationAdmissionError(
            "operation start did not commit one authoritative event")
    return started[0]
