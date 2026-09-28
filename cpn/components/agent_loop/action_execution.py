"""Agent action observation, validation, execution, and settlement planning."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import stat

from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputTarget
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, canonicalize_llm_response_payload,
    observe_registered_llm_response,
)
from cpn.rpnh.registry.errors import (
    ResourceIntegrityFault, ResourcePayloadSchemaViolation,
    StaleAuthorityHead, UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.firing_authority import canonical_invocation
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.agent_resource_broker import (
    AgentLoopResourceRequest, prepare_agent_resource_request,
    stage_resumed_agent_resource_lifecycle,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.publication import (
    _append_direct_resource_version_publication, _direct_resource_metadata,
    _provider_request_resource_metadata, _ref_payload, _registry_type_catalog_ref,
    _resource_from_payload, _stable_id,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.resources import (
    AcknowledgeResourceDelivery, AddressBindingIntent,
    AgentLoopResourceGrantAuthority, AgentLoopResourceLifecycleAuthority,
    AuthorizeResourceRelease, PetriOutputOrigin, PrepareResourceDelivery,
    PublishResource, ResourceAddress, ResourceVersionRef,
    UnbindResourceAddress, WorkspaceWriteOrigin,
)
from cpn.rpnh.registry.schema_catalog import TypeDefinition, canonical_json

from .compact import build_replacement_history, reduce_tool_messages, should_compact
from .request_envelope_materialization import materialize_agent_request_envelope
from .models import (
    AgentActionRecord, AgentLoopSnapshot, AgentLoopState, AgentTurnRecord,
    LocatedAgentInput,
)
from .service import (
    AgentLengthInterruptionRecord, AgentLoopRegistryPort,
    CompletedAgentContextCompaction, PreparedAgentContextCompaction,
    ParentOwnedDelegatedSubtaskLengthReplay, ParentOwnedDelegatedSubtaskRequest,
    ParentOwnedDelegatedSubtaskResult, ParentOwnedDelegatedSubtaskToolStep,
    PreparedAgentLLMTurn, PreparedAgentTurnContext,
    PreparedParentOwnedDelegatedSubtaskCall, StartAgentLoopCommand,
    _PreparedParentOwnedDelegatedSubtaskContext,
)
from .delegated_history import compact_delegated_subtask_history_after_length
from .tool_catalog import (
    TOOL_ARGUMENT_SCHEMAS, AgentToolCatalog, build_agent_tool_catalog,
    derive_atomic_subtask_tools, parse_agent_tool_catalog,
)
from .tool_validation import (
    AgentToolSyntaxError, ValidatedAgentToolAction,
    validate_agent_tool_observation,
)
from .tool_projection import (
    bounded_agent_action_output_projection, bounded_agent_text_search_projection,
)
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity, NumericalToolProfile,
    query_execution_environment_resources,
)


class OptionalAgentCapabilityUnavailable(ResourceIntegrityFault):
    """Typed framework block, not a fabricated successful capability result."""


class UnresolvedWorkspaceFailure(ValueError):
    """Model-correctable refusal to settle over failed workspace evidence."""


def _workspace_execution_failed(metadata: Mapping | None) -> bool:
    return bool(
        metadata
        and metadata.get("kind") == "workspace_execution/v1"
        and (metadata.get("status") != "completed"
             or metadata.get("exit_code") != 0))


def _settled_workspace_action_history(service, loop):
    """Return exact applied workspace actions for one immutable AgentLoop."""

    history = []
    events = service.core.event_store.list_events_by_aggregate(
        loop.loop_id, event_types=("agent_action_settled/v1",))
    for event in events:
        if (event.payload.get("tool_name") != "workspace"
                or event.payload.get("settlement")
                != AgentLoopState.ACTION_APPLIED.value):
            continue
        action_id = TypedId.parse(
            str(event.payload["agent_action_id"]), expected="agent_action")
        row = service.core.event_store.latest_object_row(
            action_id, object_type="agent_action/v2")
        if row is None:
            raise ResourceIntegrityFault(
                "workspace settlement lacks its exact action object")
        action_ref = VersionRef(
            "agent_action/v2", action_id,
            TypedId.parse(
                str(row["version_id"]), expected="agent_action_version"))
        action = service.mechanical_lifecycle.hydrate_action(action_ref)
        if (action.loop_id != loop.loop_id
                or action.tool_name != "workspace"
                or action.tool_call_ordinal
                != event.payload.get("tool_call_ordinal")):
            raise ResourceIntegrityFault(
                "workspace action history crossed its exact AgentLoop")
        history.append((action_ref, action))
    return tuple(sorted(
        history,
        key=lambda item: (
            item[1].turn_sequence, item[1].tool_call_ordinal,
            str(item[0].entity_id)),
    ))


def _validate_workspace_failure_closure(
        *, service, execution, loop) -> None:
    """Require causal closure for every failed workspace action."""

    history = _settled_workspace_action_history(service, loop)
    failed = tuple(
        action for _ref, action in history
        if _workspace_execution_failed(action.result_metadata)
    )
    if not failed:
        return

    _binding, _compiled, operation = service._declared(
        execution.operation.canonical.context)
    policy = operation.declaration.config.get(
        "workspace_failure_policy", "require_resolved")
    if policy not in {"require_resolved", "allow_explicit_diagnostic"}:
        raise ResourceIntegrityFault(
            "operation declares an invalid workspace failure policy")
    if policy == "allow_explicit_diagnostic":
        return
    successful = tuple(
        action for _ref, action in history
        if not _workspace_execution_failed(action.result_metadata))
    for failed_action in failed:
        if not any(
                (resolution.turn_sequence, resolution.tool_call_ordinal)
                > (failed_action.turn_sequence,
                   failed_action.tool_call_ordinal)
                for resolution in successful):
            raise UnresolvedWorkspaceFailure(
                "complete_interaction requires a later completed zero-exit "
                "workspace action after every timed-out or nonzero-exit action")


def _read_file(*, service, execution, loop, turn, arguments, idempotency_key):
    return service._read_input(execution, loop, arguments, idempotency_key)

def _write_file(*, service, execution, loop, turn, arguments, idempotency_key):
    return service._write_product(execution, loop, arguments, idempotency_key)

def _workspace(*, service, execution, loop, turn, arguments, idempotency_key):
    return service._run_workspace(execution, loop, arguments, idempotency_key)

def _query_environment_resources(
        *, service, execution, loop, turn, arguments, idempotency_key):
    return service._query_environment_resources(
        execution, loop, arguments, idempotency_key)

def _search_text(
        *, service, execution, loop, turn, arguments, idempotency_key):
    return service._search_text(
        execution, loop, arguments, idempotency_key)

def _read_action_output(
        *, service, execution, loop, turn, arguments, idempotency_key):
    return service._read_action_output(
        execution, loop, turn, arguments, idempotency_key)

def _query_registry_resources(
        *, service, execution, loop, turn, arguments, idempotency_key):
    return service._query_registry_resources(
        execution, loop, arguments, idempotency_key)

def _delegate_leaf(**_kwargs):
    raise ResourceIntegrityFault(
        "delegate_leaf must be resolved by the parent-owned local lifecycle")

def _request_resource(**_kwargs):
    raise ResourceIntegrityFault(
        "request_resource must be resolved by the Registry resource lifecycle")

def _complete_interaction(*, service, execution, loop, turn, arguments, idempotency_key):
    _validate_workspace_failure_closure(
        service=service, execution=execution, loop=loop)
    if not loop.written_resource_refs:
        raise ValueError("complete_interaction requires a registered semantic product")
    artifacts = tuple(verify_resource(service.core, service.kernel, execution.operation.canonical, ref)
                      for ref in loop.written_resource_refs)
    outcome_ids = {
        value
        for artifact in artifacts
        for descriptor in artifact.header.descriptor_labels
        if descriptor.name == "output_outcome_id"
        for value in descriptor.values
    }
    if len(outcome_ids) != 1:
        raise ValueError(
            "complete_interaction requires one exact semantic outcome")
    selected_outcome_id, = outcome_ids
    if not isinstance(selected_outcome_id, str):
        raise ValueError("semantic outcome descriptor is not text")
    # Validate the actual declared bundle before accepting completion; the
    # dispatcher, not this tool, subsequently registers operation outputs.
    from cpn.rpnh.registry.operations import register_operation_outputs
    register_operation_outputs(service.repository, execution, artifacts,
                               idempotency_key=idempotency_key + ":validate-bundle",
                               selected_outcome_id=selected_outcome_id)
    return (), {
                "kind": "interaction_completion/v1", "completed": True,
                "selected_outcome_id": selected_outcome_id,
                "written_resource_refs": [_resource_payload(ref) for ref in loop.written_resource_refs]}

_SYNC_WORKSPACE_SCHEMA = deepcopy(TOOL_ARGUMENT_SCHEMAS["workspace"])
_SYNC_WORKSPACE_SCHEMA["properties"]["execution_mode"]["enum"] = ["sync"]
OPTIONAL_TOOL_BINDINGS = {
    name: (implementation, {
        "identity": {"implementation_id": "components.agent_loop.optional_execution." + name,
                     "revision": "v1"},
        "contracts": {"binding_protocol": "optional_agent_tool/v1",
                      "description": description, "arguments": schema},
    })
    for name, implementation, description, schema in (
        ("read_file", _read_file, "Read an exact registered input path; returns a bounded decoded view.",
         TOOL_ARGUMENT_SCHEMAS["read_file"]),
        ("write_file", _write_file,
         "Register one workspace file and its exact semantic Petri output. Use content or one exact visible source_resource_ref. For a text output, content is direct text; for a structured output, content is one JSON document. output_port_id is the declared symbolic output port; outcome_id is required when the operation declares multiple semantic outcomes.",
        TOOL_ARGUMENT_SCHEMAS["write_file"]),
        ("workspace", _workspace,
         "Run one bounded, network-disabled shell script in this firing's persistent private workspace. Regular files created or changed by the command are versioned in Registry and projected to later agents after this firing settles.",
         _SYNC_WORKSPACE_SCHEMA),
        ("query_environment_resources", _query_environment_resources,
         "Query the immutable package inventory captured for this execution environment.",
         TOOL_ARGUMENT_SCHEMAS["query_environment_resources"]),
        ("search_text", _search_text,
         "Search one exact registered UTF-8 input using bounded literal-match pages.",
         TOOL_ARGUMENT_SCHEMAS["search_text"]),
        ("read_action_output", _read_action_output,
         "Read a bounded stdout or stderr page from an earlier workspace action in this firing.",
         TOOL_ARGUMENT_SCHEMAS["read_action_output"]),
        ("query_registry_resources", _query_registry_resources,
         "Search body-free metadata for workspace resources visible to this firing.",
         TOOL_ARGUMENT_SCHEMAS["query_registry_resources"]),
        ("request_resource", _request_resource,
         "Request same-firing read or edit access to one exact Registry resource.",
         TOOL_ARGUMENT_SCHEMAS["request_resource"]),
        ("delegate_leaf", _delegate_leaf,
         "Run one depth-one parent-owned subtask with a nonrecursive tool catalog.",
         TOOL_ARGUMENT_SCHEMAS["delegate_leaf"]),
        ("complete_interaction", _complete_interaction,
         "Complete after registered write_file products satisfy the declared output bundle. Must be the final tool call.",
         TOOL_ARGUMENT_SCHEMAS["complete_interaction"]),
    )
}

EXECUTION_PROVENANCE_SCHEMA = "component/optional_agent_execution_provenance/v1"
EXECUTION_PROVENANCE_DOCUMENT = {
    "$id": EXECUTION_PROVENANCE_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "properties": {"schema_version": {"const": "optional_agent_execution_provenance/v1"},
        "model": {"type": "string", "minLength": 1}, "backend": {"type": "string", "minLength": 1},
        "timeout_seconds": {"type": "integer", "minimum": 1}, "selection": {"type": "object"},
        "transport_kind": {"type": "string", "minLength": 1},
        "response_protocol": {"type": "string", "minLength": 1}},
    "required": ["schema_version", "model", "backend", "timeout_seconds", "selection",
                 "transport_kind", "response_protocol"],
}


class ActionExecutionMixin:
    def prepare_agent_turn_actions_v1(self, loop, turn, *, timing_origin_ns=None):
        self._current(loop)
        return self.mechanical_lifecycle.prepare_turn_actions(
            loop, turn,
            read_response=lambda ref: self.kernel._read_firing_registered(
                self._context(loop), ref),
            tool_argument_schemas=(
                parse_agent_tool_catalog(
                    self._static(
                        self._context(loop),
                        "optional_agent_tool_catalog")[2]
                ).argument_schemas),
            timing_origin_ns=timing_origin_ns)

    def settle_agent_turn_actions_v1(self, loop, turn, actions, *, permitted_tool_names, idempotency_key,
                                    execution=None, timing_evidence=None, delegated_subtask_results=None,
                                    external_tool_results=None):
        execution = self._execution(execution, loop)
        catalog = self.current_agent_tool_catalog_v1(execution)
        if permitted_tool_names != catalog.tool_names or actions != self.prepare_agent_turn_actions_v1(loop, turn):
            # Timing evidence is deliberately excluded from semantic identity.
            if permitted_tool_names != catalog.tool_names or tuple((item.tool_call, item.validation) for item in actions) != tuple(
                    (item.tool_call, item.validation) for item in self.prepare_agent_turn_actions_v1(loop, turn)):
                raise ResourceIntegrityFault("optional action batch differs from exact registered raw turn")
        delegated_subtask_results = dict(delegated_subtask_results or {})
        delegated_action_ids = {
            item.validation.action_id for item in actions
            if isinstance(item.validation, ValidatedAgentToolAction)
            and item.validation.tool_name == "delegate_leaf"}
        if set(delegated_subtask_results) != delegated_action_ids:
            raise ResourceIntegrityFault(
                "delegated results differ from the exact parent action batch")
        event, = [
            event for event in self.mechanical_lifecycle.turn_events(loop)
            if event.payload["sequence"] == turn.sequence]
        turn_ref = self.mechanical_lifecycle.turn_ref_from_event(event)
        resource_actions = tuple(
            item for item in actions
            if isinstance(item.validation, ValidatedAgentToolAction)
            and item.validation.tool_name == "request_resource")
        if len(actions) == 1 and len(resource_actions) == 1:
            return self._settle_agent_resource_request(
                execution, loop, turn, resource_actions[0],
                turn_ref=turn_ref, idempotency_key=idempotency_key)
        records = []
        error_documents = []
        delegated_terminal_responses = {}
        current = loop
        unobserved_workspace_failure = any(
            _workspace_execution_failed({
                "kind": "workspace_execution/v1",
                **dict(result),
            })
            for result in (external_tool_results or {}).values())
        for ordinal, prepared in enumerate(actions):
            validation = prepared.validation
            call = prepared.tool_call
            if current.state == AgentLoopState.COMPLETED:
                raise ValueError("complete_interaction must be the final tool call")
            refs, metadata, error_ref = (), None, None
            state = AgentLoopState.ACTION_APPLIED
            error = None
            error_code = "arguments_invalid"
            if isinstance(validation, AgentToolSyntaxError):
                error = validation.detail
            elif validation.tool_name not in catalog.tool_names:
                error = "tool not declared in this optional surface"
            elif validation.tool_name == "request_resource":
                error = "request_resource must be the only tool call in its turn"
            elif validation.tool_name == "delegate_leaf":
                result = delegated_subtask_results.get(validation.action_id)
                if (not isinstance(result, ParentOwnedDelegatedSubtaskResult)
                        or result.parent_invocation_ref != loop.invocation_ref
                        or result.parent_agent_loop_ref != self.mechanical_lifecycle.loop_ref(loop)
                        or result.parent_agent_turn_ref != turn_ref
                        or result.action_id != validation.action_id
                        or result.tool_call_id != call.tool_call_id
                        or result.tool_call_ordinal != call.tool_call_ordinal
                        or result.raw_arguments != call.raw_arguments
                        or result.parent_action_ref
                        != self.mechanical_lifecycle.action_ref(
                            validation.action_id, idempotency_key)):
                    raise ResourceIntegrityFault(
                        "delegated result crossed its exact parent action")
                invocation = self.kernel._exact_object(
                    result.llm_invocation_ref,
                    expected_type="llm_invocation_spec/v1").metadata
                attempt = self.kernel._exact_object(
                    result.llm_invocation_attempt_ref,
                    expected_type="llm_invocation_attempt/v1").metadata
                successes = tuple(
                    event for event in
                    self.core.event_store.list_events_by_aggregate(
                        str(result.llm_invocation_attempt_ref.entity_id))
                    if event.event_type == "llm_invocation_succeeded/v1")
                if (len(successes) != 1
                        or invocation.get("invocation_kind")
                        != "delegated_subtask"
                        or invocation.get("child_session_id")
                        != result.child_session_id
                        or invocation.get("local_sequence")
                        != result.local_sequence
                        or attempt.get("llm_invocation_ref")
                        != _ref_payload(result.llm_invocation_ref)
                        or successes[0].payload.get("llm_invocation_ref")
                        != _ref_payload(result.llm_invocation_ref)
                        or successes[0].payload.get(
                            "llm_invocation_attempt_ref")
                        != _ref_payload(result.llm_invocation_attempt_ref)):
                    raise ResourceIntegrityFault(
                        "delegated result lacks its exact successful call closure")
                response_ref = _resource_from_payload(
                    successes[0].payload["response_resource_ref"])
                response_payload = self.kernel._read_firing_registered(
                    self._context(loop), response_ref)
                observed = observe_registered_llm_response(
                    response_payload, PublishedLLMResponse(
                        response_ref, result.llm_invocation_attempt_ref,
                        result.llm_invocation_ref, loop.model_condition,
                        len(response_payload), "llm_response_envelope/v1"))
                if (observed.tool_calls
                        or not isinstance(observed.text, str)
                        or not observed.text.strip()):
                    raise ResourceIntegrityFault(
                        "delegated parent result is not concrete terminal text")
                delegated_terminal_responses[validation.action_id] = (
                    result, response_ref, observed.text)
                refs = (result.result_resource_ref.as_version_ref(),)
                metadata = {
                    "kind": "parent_owned_delegated_subtask_result/v1",
                    "result_resource_ref": _resource_payload(
                        result.result_resource_ref),
                    "child_session_id": result.child_session_id,
                    "local_sequence": result.local_sequence,
                    "llm_invocation_ref": _ref_payload(
                        result.llm_invocation_ref),
                    "llm_invocation_attempt_ref": _ref_payload(
                        result.llm_invocation_attempt_ref),
                }
            elif validation.tool_name == "workspace":
                result = (external_tool_results or {}).get(
                    validation.action_id)
                expected_fields = {
                    "status", "exit_code", "stdout", "stderr",
                    "output_truncated", "command_started",
                }
                if (not isinstance(result, Mapping)
                        or set(result) != expected_fields):
                    raise ResourceIntegrityFault(
                        "workspace action lacks its worker execution result")
                refs, metadata = (), {
                    "kind": "workspace_execution/v1", **dict(result)}
                unobserved_workspace_failure = (
                    unobserved_workspace_failure
                    or _workspace_execution_failed(metadata))
            else:
                registered = self.owner.registration.declaration("tool", validation.tool_name)
                # ``current`` is the in-memory projection of actions already
                # evaluated in this batch.  It is not a committed AgentLoop
                # head, so tools which revalidate their loop authority must
                # receive the exact stored loop.  Only the semantic output
                # tools need the staged written-resource projection.
                tool_loop = (
                    current
                    if validation.tool_name in {
                        "write_file", "complete_interaction"}
                    else loop)
                try:
                    if (validation.tool_name == "complete_interaction"
                            and (unobserved_workspace_failure or any(
                                record.tool_name == "workspace"
                                and _workspace_execution_failed(
                                    record.result_metadata)
                                for record in records))):
                        raise UnresolvedWorkspaceFailure(
                            "a workspace failure from this same tool-call batch "
                            "must be observed and resolved in a later turn before "
                            "completion")
                    refs, metadata = self.invoke_tool(execution, validation.tool_name,
                        identity=registered["identity"], contracts=registered["contracts"], kwargs={
                            "service": self, "execution": execution, "loop": tool_loop, "turn": turn,
                            "arguments": validation.arguments, "idempotency_key": f"{idempotency_key}:tool:{ordinal}"})
                    if validation.tool_name == "complete_interaction":
                        state = AgentLoopState.COMPLETED
                except (ValueError, ResourcePayloadSchemaViolation) as exc:
                    error = str(exc)
            if error:
                record, _action_ref, error_document = (
                    self.mechanical_lifecycle.model_correctable_rejection(
                        loop=loop, turn_ref=turn_ref,
                        turn_sequence=turn.sequence,
                        prepared_action=prepared, detail=error,
                        idempotency_key=idempotency_key,
                        error_code=error_code))
                state = record.state
                error_ref = record.tool_error_ref
                error_documents.append((error_ref, error_document))
            else:
                arguments = (
                    None if isinstance(validation, AgentToolSyntaxError)
                    else validation.arguments)
                record = AgentActionRecord(
                    validation.action_id, loop.loop_id, turn.sequence,
                    ordinal, call.tool_call_id, call.action_identity_kind,
                    call.action_identity_key, call.tool_name,
                    call.raw_arguments, arguments, loop.revision, state,
                    tuple(refs), error_ref, metadata)
            written = current.written_resource_refs
            if metadata and metadata["kind"] == "registered_file_write/v1":
                written += (_resource_from_payload(metadata["resource_ref"]),)
            current = replace(current, state=state, written_resource_refs=written)
            records.append(record)

        final_state = (AgentLoopState.COMPLETED
                       if records[-1].state == AgentLoopState.COMPLETED
                       else AgentLoopState.WAITING_FOR_LLM)

        def stage_delegated_result(tx, record, action_ref):
            terminal = delegated_terminal_responses.get(record.action_id)
            if terminal is None:
                return
            result, response_ref, text = terminal
            if action_ref != result.parent_action_ref:
                raise ResourceIntegrityFault(
                    "delegated result action version changed at settlement")
            payload = canonical_json(text)
            context = self._context(loop)
            _append_direct_resource_version_publication(
                tx, ref=result.result_resource_ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(
                    self.core, ref=result.result_resource_ref,
                    origin_kind="parent_owned_delegated_subtask_result",
                    primary=result.llm_invocation_attempt_ref,
                    secondary=result.llm_invocation_ref,
                    task_ref=context.task_ref, round_ref=context.task_round_ref,
                    net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json", content_schema_ref=None,
                    content_schema_authority_ref=None,
                    summary="Terminal parent-owned delegated subtask result",
                    descriptors={"content_role": "delegated_subtask_result"},
                    extensions={
                        "registry.parent_owned_delegated_subtask/v1": {
                            "parent_action_ref": _ref_payload(action_ref),
                            "llm_invocation_ref": _ref_payload(
                                result.llm_invocation_ref),
                            "llm_invocation_attempt_ref": _ref_payload(
                                result.llm_invocation_attempt_ref),
                            "child_session_id": result.child_session_id,
                            "local_sequence": result.local_sequence}},
                    input_resource_refs=(response_ref,),
                    intended_boundary="parent_agent_action_result"),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=idempotency_key,
                direct_owners=(result.llm_invocation_ref,
                               result.llm_invocation_attempt_ref, action_ref),
                input_resources=(response_ref,))

        committed_loop, committed_records = (
            self.mechanical_lifecycle.settle_action_batch(
                idempotency_key=idempotency_key,
                loop=loop,
                turn_ref=turn_ref, turn_id=turn.turn_id,
                records=records,
                error_documents={ref: data for ref, data in error_documents},
                written_resource_refs=current.written_resource_refs,
                final_state=final_state,
                terminal_reason=("complete_interaction"
                                 if final_state == AgentLoopState.COMPLETED
                                 else None),
                after_action_prewrite=stage_delegated_result))
        return committed_loop, committed_records

    def interrupt_agent_turn_actions_v1(
            self, loop, turn, actions, *, permitted_tool_names,
            idempotency_key, execution):
        """Reject one uncommitted action batch at an owner stop boundary."""

        self._execution(execution, loop)
        catalog = self.current_agent_tool_catalog_v1(execution)
        exact = self.prepare_agent_turn_actions_v1(loop, turn)
        if (permitted_tool_names != catalog.tool_names
                or tuple((item.tool_call, item.validation)
                         for item in actions)
                != tuple((item.tool_call, item.validation)
                         for item in exact)):
            raise ResourceIntegrityFault(
                "owner interruption differs from the exact registered turn")
        event, = [
            event for event in self.mechanical_lifecycle.turn_events(loop)
            if event.payload["sequence"] == turn.sequence]
        turn_ref = self.mechanical_lifecycle.turn_ref_from_event(event)
        records = []
        errors = {}
        action_versions = {}
        for ordinal, prepared in enumerate(actions):
            record, action_ref, document = (
                self.mechanical_lifecycle.owner_interrupted_rejection(
                    loop=loop, turn_ref=turn_ref,
                    turn_sequence=turn.sequence,
                    prepared_action=prepared,
                    idempotency_key=f"{idempotency_key}:{ordinal}"))
            records.append(record)
            errors[record.tool_error_ref] = document
            action_versions[record.action_id] = action_ref.version_id
        return self.mechanical_lifecycle.settle_action_batch(
            loop=loop, turn_ref=turn_ref, turn_id=turn.turn_id,
            records=tuple(records), error_documents=errors,
            written_resource_refs=loop.written_resource_refs,
            final_state=AgentLoopState.WAITING_FOR_LLM,
            idempotency_key=idempotency_key,
            action_version=lambda record: action_versions[record.action_id])

    def register_operation_execution_block(self, execution, **kwargs):
        execution = self._execution(execution)
        return self.mechanical_lifecycle.execution_block(
            execution, validate_error_ref=lambda ref: self.kernel._exact_object(
                ref), **kwargs)


__all__ = [
    "ActionExecutionMixin", "EXECUTION_PROVENANCE_DOCUMENT",
    "EXECUTION_PROVENANCE_SCHEMA", "OPTIONAL_TOOL_BINDINGS",
    "OptionalAgentCapabilityUnavailable",
]
