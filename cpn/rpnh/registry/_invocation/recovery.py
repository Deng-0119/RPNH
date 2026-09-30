"""Recovery implementation for the InvocationLifecycle facade."""

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

def _revalidate_historical_mechanical_receipt_terminal(
        lifecycle, context: InvocationContext,
        package: TerminalResultPackage,
) -> int:
    """Authorize only the stale-lease write selected by the Facade gate."""

    if lifecycle.service.writer_epoch != lifecycle.service.event_store.writer_epoch:
        raise StaleWriterError(
            "historical mechanical recovery lost the current writer fence")
    persisted = lifecycle.hydrate_context(
        context.invocation_ref, require_current_writer=False)
    lease = lifecycle._require_ref(
        context.operation_execution_lease_ref,
        "operation_execution_lease/v1",
        "operation_execution_lease_version",
    )
    if (persisted != context
            or context.origin != "petri_operation"
            or context.own_transition_firing_ref is None
            or int(lease["writer_fencing_epoch"])
            >= lifecycle.service.event_store.writer_epoch
            or package.business_outcome != "completed"
            or len(package.output_resource_refs) != 1
            or package.observed_read_set_ref is not None
            or package.provenance_set_ref is not None):
        raise InvocationAdmissionError(
            "historical mechanical recovery requires one stale-lease "
            "completed receipt closure")
    if (lifecycle._current_active_net_ref() != context.net_instance_ref
            or lifecycle._current_marking_ref(context.net_instance_ref)
            != context.admission_marking_checkpoint_ref):
        raise InvocationAdmissionError(
            "historical mechanical recovery requires the unchanged "
            "active net and admission marking")

    invocation_payload = _ref_payload(context.invocation_ref)
    events = tuple(lifecycle.service.event_store.list_events())
    invocation_events = tuple(
        event for event in events
        if event.aggregate_id == str(context.invocation_ref.entity_id))
    firing_events = tuple(
        event for event in events
        if event.aggregate_id
        == str(context.own_transition_firing_ref.entity_id))
    lease_events = tuple(
        event for event in events
        if event.aggregate_id
        == str(context.operation_execution_lease_ref.entity_id))
    provider_events = tuple(
        event for event in events
        if event.event_type.startswith(("provider_attempt_", "provider_call_",
                                         "llm_call_"))
        and (event.producer_invocation_id
             == context.invocation_ref.entity_id
             or event.payload.get("invocation_ref")
             == invocation_payload))
    if (sum(event.event_type == "invocation_started/v1"
            for event in invocation_events) != 1
            or any(event.event_type == "operation_terminal_ready/v1"
                   for event in invocation_events)
            or sum(event.event_type == "firing_admitted/v1"
                   for event in firing_events) != 1
            or any(event.event_type == "transition_firing_settled/v1"
                   for event in firing_events)
            or sum(event.event_type == "operation_execution_started/v1"
                   for event in lease_events) != 1
            or provider_events):
        raise InvocationAdmissionError(
            "historical mechanical recovery lifecycle is not one open "
            "provider-free execution")
    return int(lease["writer_fencing_epoch"])

def _revalidate_committed_agent_terminal(
        lifecycle, context: InvocationContext,
        package: TerminalResultPackage,
) -> None:
    """Authorize only one atomically completed, not-yet-terminal agent loop."""
    if lifecycle.service.writer_epoch != lifecycle.service.event_store.writer_epoch:
        raise StaleWriterError("agent recovery lost the current writer fence")
    if (lifecycle.hydrate_context(
            context.invocation_ref, require_current_writer=False) != context
            or context.origin != "petri_operation"
            or context.own_transition_firing_ref is None
            or package.business_outcome != "completed"
            or not package.output_resource_refs):
        raise InvocationAdmissionError(
            "agent recovery requires one completed on-net output closure")
    if (lifecycle._current_active_net_ref() != context.net_instance_ref
            or lifecycle._current_marking_ref(context.net_instance_ref)
            != context.admission_marking_checkpoint_ref):
        raise InvocationAdmissionError(
            "agent recovery invocation is outside the unchanged active marking")
    events = tuple(lifecycle.service.event_store.list_events())
    invocation_events = tuple(
        event for event in events
        if event.aggregate_id == str(context.invocation_ref.entity_id))
    firing_events = tuple(
        event for event in events
        if event.aggregate_id
        == str(context.own_transition_firing_ref.entity_id))
    lease_events = tuple(
        event for event in events
        if event.aggregate_id
        == str(context.operation_execution_lease_ref.entity_id))
    if (sum(event.event_type == "invocation_started/v1"
            for event in invocation_events) != 1
            or any(event.event_type == "operation_terminal_ready/v1"
                   for event in invocation_events)
            or sum(event.event_type == "firing_admitted/v1"
                   for event in firing_events) != 1
            or any(event.event_type == "transition_firing_settled/v1"
                   for event in firing_events)
            or sum(event.event_type == "operation_execution_started/v1"
                   for event in lease_events) != 1
            or any(event.event_type in {
                "provider_attempt_submission_unknown/v1",
                "llm_call_submission_unknown/v1",
            } and event.producer_invocation_id
                == context.invocation_ref.entity_id for event in events)):
        raise InvocationAdmissionError(
            "agent recovery effects are terminal, superseded, or uncertain")

    context_ref = _ref_payload(context.invocation_ref)
    loop_rows = []
    for row in lifecycle.service.event_store.object_rows():
        if row["object_type"] != "agent_loop/v1":
            continue
        metadata = json.loads(str(row["metadata_json"]))
        if metadata.get("invocation_ref") == context_ref:
            loop_rows.append((row, metadata))
    if not loop_rows:
        raise InvocationAdmissionError(
            "agent recovery has no durable loop authority")
    highest = max(int(metadata["revision"])
                  for _row, metadata in loop_rows)
    current = tuple(
        (row, metadata) for row, metadata in loop_rows
        if int(metadata["revision"]) == highest)
    if len(current) != 1 or current[0][1].get("state") != "COMPLETED":
        raise InvocationAdmissionError(
            "agent recovery refuses an ambiguous or nonterminal loop")
    loop_row, loop = current[0]
    loop_id = str(loop["agent_loop_id"])
    terminal = tuple(
        event for event in events
        if event.event_type == "agent_loop_terminal/v1"
        and event.aggregate_id == loop_id
        and int(event.payload.get("revision", -1)) == highest)
    actions = tuple(sorted((
        event for event in events
        if event.event_type == "agent_action_settled/v1"
        and event.aggregate_id == loop_id),
        key=lambda event: event.stream_sequence))
    terminal_actions = tuple(
        event for event in actions
        if int(event.payload.get("revision", -1)) == highest)
    if (len(terminal) != 1 or not terminal_actions
            or tuple(int(event.payload.get("tool_call_ordinal", -1))
                     for event in terminal_actions)
            != tuple(range(len(terminal_actions)))
            or any(event.transaction_id != terminal[0].transaction_id
                   for event in terminal_actions)
            or str(loop_row["transaction_id"])
            != str(terminal[0].transaction_id)):
        raise InvocationAdmissionError(
            "agent recovery loop/action settlement is not one transaction")
    written = tuple(VersionRef(
        "resource_version/v1",
        TypedId.parse(str(value["resource_id"]), expected="resource"),
        TypedId.parse(str(value["resource_version_id"]),
                      expected="resource_version"))
        for value in loop.get("written_resource_refs", []))
    if (set(written) != set(package.output_resource_refs)
            or tuple(sorted(
                package.output_resource_refs,
                key=lambda ref: str(ref.version_id)))
            != package.output_resource_refs
            or any(ref.entity_type != "resource_version/v1"
                   for ref in written)):
        raise InvocationAdmissionError(
            "agent recovery outputs differ from the completed loop")
    object_rows = tuple(lifecycle.service.event_store.object_rows())
    action_rows: list[tuple[EventEnvelope, Mapping[str, Any]]] = []
    for event in actions:
        rows = tuple(
            row for row in object_rows
            if row["object_type"] in {"agent_action/v2", "agent_action/v3"}
            and row["logical_id"] == event.payload.get("agent_action_id"))
        if len(rows) != 1 or str(rows[0]["transaction_id"]) != str(
                event.transaction_id):
            raise InvocationAdmissionError(
                "agent recovery action event lacks one exact record")
        metadata = json.loads(str(rows[0]["metadata_json"]))
        result_refs = tuple(
            _ref_from_payload(value)
            for value in metadata.get("result_refs", []))
        if (metadata.get("agent_loop_ref", {}).get("logical_id") != loop_id
                or metadata.get("tool_name") != event.payload.get("tool_name")
                or metadata.get("state") != event.payload.get("settlement")
                or [str(ref.version_id) for ref in result_refs]
                != list(event.payload.get("result_version_ids", []))):
            raise InvocationAdmissionError(
                "agent recovery action record differs from its event")
        action_rows.append((event, metadata))
    completions = tuple(
        item for item in action_rows
        if item[1].get("tool_name") == "complete_interaction")
    if (len(completions) != 1
            or completions[0] != action_rows[-1]
            or completions[0][0] not in terminal_actions
            or completions[0][1].get("state") != "COMPLETED"
            or tuple(_ref_from_payload(value)
                     for value in completions[0][1].get("result_refs", []))
            != written):
        raise InvocationAdmissionError(
            "agent recovery lacks one exact completed interaction")
    for ref in written:
        row = lifecycle.service.event_store.object_row(ref.version_id)
        write_rows = tuple(
            item for item in action_rows
            if item[1].get("tool_name") == "write_file"
            and tuple(_ref_from_payload(value)
                      for value in item[1].get("result_refs", [])) == (ref,))
        if len(write_rows) != 1:
            raise InvocationAdmissionError(
                "agent recovery output lacks one exact write action")
        write_event, write = write_rows[0]
        transaction_id = str(write_event.transaction_id)
        result_metadata = write.get("result_metadata")
        relative = (result_metadata.get("path")
                    if isinstance(result_metadata, Mapping) else None)
        addresses = []
        for address_row in object_rows:
            if (address_row["object_type"] != "resource_address_binding/v1"
                    or str(address_row["transaction_id"]) != transaction_id):
                continue
            metadata = json.loads(str(address_row["metadata_json"]))
            if (metadata.get("scope_ref") == context_ref
                    and metadata.get("opaque_name") == relative
                    and metadata.get("resource_ref") == {
                        "resource_id": str(ref.entity_id),
                        "resource_version_id": str(ref.version_id),
                    }
                    and metadata.get("authorization_ref")
                    == loop.get("operation_binding_ref")
                    and metadata.get("lifecycle_state") == "bound"
                    and metadata.get("commit_transaction_id")
                    == transaction_id):
                addresses.append(address_row)
        if (not isinstance(relative, str)
                or row is None or row["object_type"] != "resource_version/v1"
                or str(row["transaction_id"]) != transaction_id
                or row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)
                or len(addresses) != 1):
            raise InvocationAdmissionError(
                "agent recovery output lacks its exact write closure")
