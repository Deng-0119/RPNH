"""AgentLoop delegation mechanics.

Methods operate on the owning mechanical lifecycle instance; they do not
own Registry state or create a second transaction coordinator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Mapping, Sequence

from cpn.rpnh.llm_contracts import LLMCallAttempt
from cpn.rpnh.registry.event_store import PendingEvent
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.models import TypedRelation, VersionRef
from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
from cpn.rpnh.registry.provider_calls import LLMCallV2, ProviderAttemptV2
from cpn.rpnh.registry.publication import (
    _ref_payload, _resource_from_payload, _stable_id, _version_from_payload,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, observe_registered_llm_response,
)

from .models import (
    AgentActionRecord, AgentContextOverlay, AgentLoopSnapshot,
    AgentLoopState, AgentToolCallFact, AgentTurnRecord,
    require_state_transition,
)


from .mechanical_contracts import (
    AgentLoopMechanicalLifecycleError,
    _LLM_FAILURE_DISPOSITIONS,
    _LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL,
    _LLM_SUBMISSION_STATES,
    _OWNER_INTERRUPTION_SUBMISSION_STATES,
    _RETRYABLE_LLM_FAILURE_DISPOSITIONS,
)


class DelegationRecordsMechanicsMixin:
    def commit_delegated_step(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            action_ref: VersionRef, idempotency_key: str,
            stage: Callable[[Any], None],
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> None:
        """Commit policy-prepared child-step facts under one open parent action."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,))
        if (turn_ref.entity_type != "agent_turn/v1"
                or action_ref.entity_type != "agent_action/v2"
                or not callable(stage)):
            raise AgentLoopMechanicalLifecycleError(
                "delegated step lacks exact parent turn/action authority")
        exact_turn = self.turn_ref_for(
            current.loop_id, current.next_turn_sequence - 1)
        if exact_turn != turn_ref:
            raise AgentLoopMechanicalLifecycleError(
                "delegated step crossed its parent turn")
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(current))
        stage(tx)
        for event_type, aggregate_id, aggregate_type, payload in events:
            self._event(
                tx, event_type=event_type, aggregate_id=aggregate_id,
                aggregate_type=aggregate_type, payload=payload,
                producer_invocation_id=current.invocation_ref.entity_id)
        tx.commit()

    def begin_delegated_policy_transaction(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            action_ref: VersionRef, idempotency_key: str,
            transaction_scope: Mapping[str, Any] | None = None,
    ) -> Any:
        """Open one parent-scoped transaction for delegated policy effects."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,))
        if (turn_ref.entity_type != "agent_turn/v1"
                or action_ref.entity_type != "agent_action/v2"
                or self.turn_ref_for(
                    current.loop_id,
                    current.next_turn_sequence - 1) != turn_ref
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise AgentLoopMechanicalLifecycleError(
                "delegated policy transaction crossed its parent authority")
        tx = self.core.begin(
            idempotency_key=idempotency_key,
            **dict(transaction_scope or {}))
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(current))
        return tx

    @staticmethod
    def finish_delegated_policy_transaction(tx: Any) -> None:
        """Commit a transaction opened for delegated policy effects."""
        tx.commit()

    def commit_delegated_response(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            action_ref: VersionRef, attempt: LLMCallAttempt,
            response_ref: ResourceVersionRef, response_size: int,
            child_session_id: str, local_sequence: int,
            length_interrupted: bool, idempotency_key: str,
            stage_response: Callable[[Any], None],
    ) -> None:
        """Commit one local delegated response under its open parent action."""
        if (not isinstance(attempt, LLMCallAttempt)
                or not isinstance(response_ref, ResourceVersionRef)
                or isinstance(response_size, bool)
                or not isinstance(response_size, int) or response_size < 1
                or not isinstance(child_session_id, str)
                or not child_session_id
                or isinstance(local_sequence, bool)
                or not isinstance(local_sequence, int)
                or local_sequence < 0):
            raise AgentLoopMechanicalLifecycleError(
                "delegated response lacks exact local-call authority")
        registered = {
            "llm_invocation_ref": _ref_payload(attempt.invocation_ref),
            "llm_invocation_attempt_ref": _ref_payload(
                attempt.attempt_ref),
            "response_resource_ref": _resource_payload(response_ref),
            "response_size": response_size,
        }
        terminal_type = (
            "llm_invocation_interrupted/v1" if length_interrupted
            else "llm_invocation_succeeded/v1")
        terminal = ({
            **registered,
            "finish_reason": "length",
            "child_session_id": child_session_id,
            "local_sequence": local_sequence,
        } if length_interrupted else {
            **{key: value for key, value in registered.items()
               if key != "response_size"},
            "turn_sequence": local_sequence,
        })
        self.commit_delegated_step(
            loop=loop, turn_ref=turn_ref, action_ref=action_ref,
            idempotency_key=idempotency_key, stage=stage_response,
            events=(
                ("llm_response_registered/v1",
                 str(attempt.attempt_ref.entity_id),
                 "llm_invocation_attempt", registered),
                (terminal_type, str(attempt.attempt_ref.entity_id),
                 "llm_invocation_attempt", terminal),
            ))


__all__ = ["DelegationRecordsMechanicsMixin"]


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
    _resource_from_payload, _version_from_payload, _stable_id,
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
from .action_execution import (
    EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
    OPTIONAL_TOOL_BINDINGS, OptionalAgentCapabilityUnavailable,
)


class DelegationExecutionMixin:
    def prepare_parent_owned_delegated_subtask_call_v1(self, execution, loop, turn, action, *, parent_tool_catalog,
            idempotency_key, parent_settlement_idempotency_key, leaf_turn_sequence=0, prior_leaf_steps=(), history_messages=()):
        execution = self._execution(execution, loop)
        self._current(loop)
        if loop.state != AgentLoopState.TURN_STORED:
            raise StaleAuthorityHead(
                "delegated subtask requires the open parent turn")
        if (turn != self.hydrate_current_agent_turn_v1(loop)
                or action not in self.prepare_agent_turn_actions_v1(loop, turn)):
            raise ResourceIntegrityFault(
                "delegated subtask crossed its exact parent turn/action")
        validation = getattr(action, "validation", None)
        call = getattr(action, "tool_call", None)
        if (not isinstance(validation, ValidatedAgentToolAction)
                or validation.tool_name != "delegate_leaf"
                or not isinstance(idempotency_key, str) or not idempotency_key
                or not isinstance(parent_settlement_idempotency_key, str)
                or not parent_settlement_idempotency_key
                or isinstance(leaf_turn_sequence, bool)
                or not isinstance(leaf_turn_sequence, int)
                or leaf_turn_sequence < 0
                or not isinstance(prior_leaf_steps, tuple)
                or not isinstance(history_messages, tuple)):
            raise ResourceIntegrityFault(
                "delegated subtask preparation arguments are invalid")
        if (len(prior_leaf_steps) != leaf_turn_sequence
                or tuple(step.leaf_turn_sequence for step in prior_leaf_steps)
                != tuple(range(leaf_turn_sequence))
                or (leaf_turn_sequence == 0 and history_messages)
                or (leaf_turn_sequence > 0
                    and history_messages
                    != prior_leaf_steps[-1].history_messages)):
            raise ResourceIntegrityFault(
                "delegated subtask history is not one contiguous child prefix")

        context = self._context(loop)
        binding, _compiled, operation = self._declared(context)
        self._catalog(context, parent_tool_catalog)
        turn_event, = [
            event for event in self.mechanical_lifecycle.turn_events(loop)
            if event.payload["sequence"] == turn.sequence]
        parent_turn_ref = self.mechanical_lifecycle.turn_ref_from_event(
            turn_event)
        parent_action_ref = self.mechanical_lifecycle.action_ref(
            validation.action_id, parent_settlement_idempotency_key)
        child_session_id = str(_stable_id(
            "delegated_leaf_session", context.invocation_ref.version_id,
            parent_action_ref.version_id))

        def resource_refs(name):
            values = []
            for raw in binding[name]:
                ref = _version_from_payload(raw)
                if ref.entity_type == "resource_version/v1":
                    values.append(ResourceVersionRef(
                        ref.entity_id, ref.version_id))
            return tuple(values)

        parent_readable = resource_refs("readable_resource_refs")
        parent_discoverable = resource_refs("discoverable_resource_refs")
        selected = tuple(
            _resource_from_payload(raw)
            for raw in validation.arguments["resource_refs"])
        if (len(set(selected)) != len(selected)
                or any(ref not in parent_readable for ref in selected)
                or any(ref not in parent_discoverable for ref in selected)):
            raise ResourceIntegrityFault(
                "delegated resources exceed the parent Registry grants")

        child_tools = derive_atomic_subtask_tools(parent_tool_catalog)
        child_tool_names = tuple(str(item["name"]) for item in child_tools)
        if (not child_tool_names
                or {"delegate_leaf", "request_resource"}
                & set(child_tool_names)
                or set(child_tool_names) - set(parent_tool_catalog.tool_names)):
            raise ResourceIntegrityFault(
                "delegated child catalog contains a parent-only tool")
        parent_catalog_document = json.loads(parent_tool_catalog.payload)
        child_catalog_payload = canonical_json({
            **parent_catalog_document, "tools": list(child_tools)})
        child_catalog_ref = ResourceVersionRef(
            _stable_id("resource", "delegated-child-tool-catalog",
                       child_session_id),
            _stable_id("resource_version", "delegated-child-tool-catalog",
                       child_session_id))
        child_catalog_key = "delegated-child-catalog:" + child_session_id

        def stage_catalog(tx):
            _append_direct_resource_version_publication(
                tx, ref=child_catalog_ref, payload=child_catalog_payload,
                metadata_factory=lambda size: _direct_resource_metadata(
                    self.core, ref=child_catalog_ref,
                    origin_kind="parent_owned_delegated_subtask_tool_catalog",
                    primary=context.operation_binding_ref,
                    secondary=self.mechanical_lifecycle.loop_ref(loop),
                    task_ref=context.task_ref, round_ref=context.task_round_ref,
                    net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json", content_schema_ref=None,
                    content_schema_authority_ref=None,
                    summary="Nonrecursive parent-owned delegated tool catalog",
                    descriptors={"content_role": "delegated_agent_tool_catalog"},
                    extensions={
                        "registry.parent_owned_delegated_subtask/v1": {
                            "parent_action_id": validation.action_id,
                            "child_session_id": child_session_id}},
                    input_resource_refs=(loop.tool_catalog_ref,),
                    intended_boundary="not_applicable"),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=child_catalog_key,
                direct_owners=(context.operation_binding_ref, self.mechanical_lifecycle.loop_ref(loop)),
                input_resources=(loop.tool_catalog_ref,))

        def verify_catalog(candidate):
            if (candidate != child_catalog_ref
                    or self.core.object_store.read_registered(
                        self.kernel._exact_object(
                            child_catalog_ref.as_version_ref(),
                            expected_type="resource_version/v1"))
                    != child_catalog_payload):
                raise ResourceIntegrityFault(
                    "delegated child catalog replay changed exact bytes")

        self.mechanical_lifecycle.commit_request_authority(
            idempotency_key=child_catalog_key,
            stage_request=stage_catalog, loop=loop,
            request_ref=child_catalog_ref, verify_replay=verify_catalog)

        prompt_ref, prompt_prepared, prompt_payload = self._static(
            context, "optional_agent_prompt")
        target = self._target(context)
        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        target_prepared = self.kernel._firing_prepared(context, target_ref)
        messages = [
            {"role": "system", "content": (
                "You are a depth-one child session owned by the current parent "
                "agent action. Complete exactly the delegated instruction. "
                "Use only the supplied atomic tool catalog; delegate_leaf and "
                "request_resource are unavailable. Use only the resource refs "
                "supplied by the parent. Return a concrete final text result "
                "after any needed tool turns.")},
            {"role": "user", "content": canonical_json({
                "instruction": validation.arguments["instruction"],
                "readable_resource_refs": [
                    _resource_payload(ref) for ref in selected],
            }).decode("utf-8")},
            *history_messages,
        ]
        from cpn.components.request_protocol import (
            validate_llm_request_message_history,
        )
        validate_llm_request_message_history(messages)
        envelope = {
            "protocol": "llm_request_envelope/v1",
            "model_condition": target.model_condition,
            "max_output_tokens": target.max_output_tokens,
            "messages": messages,
            "tools": [{"type": "function", "function": {
                "name": item["name"], "description": item["description"],
                "parameters": item["arguments"]}}
                for item in child_tools],
            "tool_choice": "auto",
            "source_prompt_ref": _resource_payload(prompt_ref),
            "tool_catalog_ref": _resource_payload(child_catalog_ref),
            "placeholders": [],
        }
        self.core.catalog.validate_schema_ref(
            "runtime/llm_request_envelope/v1", envelope)
        canonical_envelope = canonical_json(envelope)
        recipe = dict(envelope)
        recipe.pop("protocol")
        recipe["schema_version"] = "logical_provider_request_recipe/v1"
        recipe["model"] = recipe.pop("model_condition")
        recipe["max_tokens"] = recipe.pop("max_output_tokens")
        request_payload = canonical_json(recipe)
        self.core.catalog.validate_schema_ref(
            "registry_v1/logical_provider_request_recipe/v1", recipe)
        request_ref = ResourceVersionRef(
            _stable_id("resource", "delegated-child-request",
                       child_session_id, leaf_turn_sequence, idempotency_key),
            _stable_id("resource_version", "delegated-child-request",
                       child_session_id, leaf_turn_sequence, idempotency_key))
        request_inputs = tuple(dict.fromkeys((
            prompt_ref, child_catalog_ref, *selected,
            *(step.tool_step_evidence_ref for step in prior_leaf_steps))))

        def stage_request(tx):
            _append_direct_resource_version_publication(
                tx, ref=request_ref, payload=request_payload,
                metadata_factory=lambda size: _provider_request_resource_metadata(
                    self.core, ref=request_ref, context=context,
                    consumer_authority_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json",
                    content_schema_ref=(
                        "registry_v1/logical_provider_request_recipe/v1"),
                    summary="Parent-owned delegated subtask request",
                    descriptors={"content_role": "delegated_agent_request"},
                    extensions={
                        "registry.parent_owned_delegated_subtask/v1": {
                            "parent_action_id": validation.action_id,
                            "child_session_id": child_session_id,
                            "local_sequence": leaf_turn_sequence}},
                    input_resource_refs=request_inputs),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=idempotency_key + ":request",
                direct_owners=(context.operation_binding_ref, self.mechanical_lifecycle.loop_ref(loop)),
                input_resources=request_inputs)

        def verify_request(candidate):
            if (candidate != request_ref
                    or self.kernel._read_firing_registered(
                        context, candidate) != request_payload):
                raise ResourceIntegrityFault(
                    "delegated request replay changed exact bytes")

        self.mechanical_lifecycle.commit_request_authority(
            idempotency_key=idempotency_key + ":request",
            stage_request=stage_request, loop=loop,
            request_ref=request_ref, verify_replay=verify_request)
        delivery, _receipt, _payload = self._deliver(
            context, request_ref, "llm_prompt",
            idempotency_key + ":request-delivery")
        request_prepared = self.kernel._firing_prepared(context, request_ref)
        backend_ref, _backend_prepared, backend_payload = self._static(
            context, "optional_agent_backend")
        transport_ref, _transport_prepared, transport_payload = self._static(
            context, "optional_agent_transport")
        backend = json.loads(backend_payload)
        transport = json.loads(transport_payload)
        if backend["model"] != loop.model_condition:
            raise ResourceIntegrityFault(
                "delegated provider model differs from the parent loop")
        prior_invocation_refs = tuple(
            step.llm_invocation_ref for step in prior_leaf_steps)
        call_authority = self.ledger.create_call_v2(
            context=context, loop_ref=_ref_payload(self.mechanical_lifecycle.loop_ref(loop)),
            turn_sequence=leaf_turn_sequence,
            request_resource_ref=request_ref,
            terminal_delivery_ref=delivery.delivery_ref,
            semantic_prompt_resource_ref=prompt_ref,
            llm_execution_target_ref=backend_ref,
            backend=backend["backend"], model=backend["model"],
            transport_contract_ref=transport_ref,
            interaction_protocol_ref=transport["interaction_protocol_ref"],
            response_adapter_ref=transport["response_adapter_ref"],
            tool_catalog_ref=child_catalog_ref,
            prior_turn_refs=prior_invocation_refs,
            timeout_seconds=backend["timeout_seconds"],
            max_response_bytes=target.max_response_bytes,
            call_id=_stable_id("llm_call", "delegated-child",
                               child_session_id, idempotency_key),
            idempotency_key=idempotency_key + ":call")
        provider = self.ledger.reserve_v2(
            context=context, call=call_authority, prior_attempt=None,
            idempotency_key=idempotency_key + ":provider")
        if context.agent_ref is None:
            raise ResourceIntegrityFault(
                "delegated subtask requires the registered parent agent")
        invocation_ref = VersionRef(
            "llm_invocation_spec/v1",
            _stable_id("llm_invocation", "delegated-child",
                       child_session_id, idempotency_key),
            _stable_id("llm_invocation_version", "delegated-child",
                       child_session_id, idempotency_key))
        invocation = {
            "llm_invocation_id": str(invocation_ref.entity_id),
            "llm_invocation_version_id": str(invocation_ref.version_id),
            "llm_invocation_ref": _ref_payload(invocation_ref),
            "invocation_ref": _ref_payload(context.invocation_ref),
            "operation_binding_ref": _ref_payload(
                context.operation_binding_ref),
            "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(loop)),
            "subject_agent_ref": _ref_payload(context.agent_ref),
            "execution_agent_ref": _ref_payload(context.agent_ref),
            "invocation_kind": "delegated_subtask",
            "turn_sequence": leaf_turn_sequence,
            "request_resource_ref": _resource_payload(request_ref),
            "semantic_prompt_resource_ref": _resource_payload(prompt_ref),
            "llm_input_target_ref": _resource_payload(target_ref),
            "model_condition": loop.model_condition,
            "tool_catalog_ref": _resource_payload(child_catalog_ref),
            "prior_turn_refs": [
                _ref_payload(ref) for ref in prior_invocation_refs],
            "child_session_id": child_session_id,
            "local_sequence": leaf_turn_sequence,
            "max_response_bytes": target.max_response_bytes,
        }
        attempt = self.mechanical_lifecycle.reserve_invocation_attempt(
            loop=loop, invocation_ref=invocation_ref,
            invocation_document=invocation,
            producer_invocation_id=context.invocation_ref.entity_id,
            reservation_class=provider.reservation_class,
            finalization_scope=provider.finalization_scope,
            model_condition=loop.model_condition,
            canonical_request_bytes=canonical_envelope,
            max_response_bytes=target.max_response_bytes,
            maximum_attempts=1, provider_attempt_ref=provider.ref,
            idempotency_key=idempotency_key + ":neutral-attempt")
        bounds = operation.declaration.config.get("resource_bounds", {})
        leaf_turn_limit = operation.declaration.config.get(
            "delegate_leaf_turn_limit", bounds.get("max_tool_turns"))
        if (isinstance(leaf_turn_limit, bool)
                or not isinstance(leaf_turn_limit, int)
                or leaf_turn_limit < 1):
            raise ResourceIntegrityFault(
                "delegated subtask requires a declared positive turn limit")
        sponsoring_ref = (
            context.sponsoring_transition_firing_ref
            or context.own_transition_firing_ref)
        if sponsoring_ref is None:
            raise ResourceIntegrityFault(
                "delegated subtask lacks its sponsoring firing")
        result_ref = ResourceVersionRef(
            _stable_id("resource", "delegated-child-result",
                       child_session_id),
            _stable_id("resource_version", "delegated-child-result",
                       child_session_id))
        request = ParentOwnedDelegatedSubtaskRequest(
            action_id=validation.action_id,
            parent_invocation_ref=context.invocation_ref,
            sponsoring_transition_firing_ref=sponsoring_ref,
            accounting_parent_invocation_ref=context.invocation_ref,
            parent_operation_binding_ref=context.operation_binding_ref,
            parent_operation_execution_lease_ref=(
                context.operation_execution_lease_ref),
            parent_a2c_activation_ref=context.activation_ref,
            parent_budget_witness_ref=context.budget_witness_ref,
            parent_agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop),
            parent_agent_turn_ref=parent_turn_ref,
            parent_action_ref=parent_action_ref,
            tool_call_id=call.tool_call_id,
            tool_call_ordinal=call.tool_call_ordinal,
            raw_arguments=call.raw_arguments,
            budget_scope=context.budget_scope,
            finalization_scope=context.finalization_scope,
            instruction=validation.arguments["instruction"],
            readable_resource_refs=selected,
            discoverable_resource_refs=selected,
            parent_readable_resource_refs=parent_readable,
            parent_discoverable_resource_refs=parent_discoverable,
            allowed_tool_names=child_tool_names,
            parent_tool_names=parent_tool_catalog.tool_names,
            leaf_turn_limit=leaf_turn_limit,
            child_session_id=child_session_id,
            result_resource_ref=result_ref,
            llm_invocation_ref=attempt.invocation_ref,
            llm_invocation_attempt_ref=attempt.attempt_ref)
        prepared_context = _PreparedParentOwnedDelegatedSubtaskContext(
            parent_context=context, loop=loop,
            parent_loop_ref=self.mechanical_lifecycle.loop_ref(loop),
            leaf_turn_sequence=leaf_turn_sequence,
            leaf_request=request,
            callsite={
                "parent_action_ref": _ref_payload(parent_action_ref),
                "tool_call_id": call.tool_call_id,
                "tool_call_ordinal": call.tool_call_ordinal},
            target=target, target_ref=target_ref,
            target_prepared=target_prepared,
            request_ref=request_ref, request_prepared=request_prepared,
            request_payload=request_payload,
            prompt_ref=prompt_ref, prompt_prepared=prompt_prepared,
            prompt_payload=prompt_payload,
            tool_catalog_ref=child_catalog_ref,
            tool_catalog_prepared=self.kernel._exact_object(
                child_catalog_ref.as_version_ref(),
                expected_type="resource_version/v1"),
            tool_catalog_payload=child_catalog_payload)
        return PreparedParentOwnedDelegatedSubtaskCall(
            request=request, attempt=attempt,
            prepared_context=prepared_context,
            leaf_turn_sequence=leaf_turn_sequence,
            history_messages=history_messages,
            prior_llm_invocation_refs=prior_invocation_refs)

    def settle_parent_owned_delegated_subtask_tool_step_v1(self, execution, loop, turn, action, prepared, response_bytes, *,
            prior_leaf_steps, idempotency_key):
        execution = self._execution(execution, loop)
        self._current(loop)
        if (loop.state != AgentLoopState.TURN_STORED
                or turn != self.hydrate_current_agent_turn_v1(loop)
                or action not in self.prepare_agent_turn_actions_v1(loop, turn)
                or not isinstance(
                    prepared, PreparedParentOwnedDelegatedSubtaskCall)
                or prepared.prepared_context.loop != loop
                or prepared.prepared_context.leaf_request != prepared.request
                or prepared.request.parent_invocation_ref != loop.invocation_ref
                or prepared.request.parent_agent_turn_ref
                != self.mechanical_lifecycle.turn_ref_for(
                    loop.loop_id, turn.sequence)
                or prepared.request.action_id
                != action.validation.action_id
                or not isinstance(prior_leaf_steps, tuple)
                or tuple(step.llm_invocation_ref for step in prior_leaf_steps)
                != prepared.prior_llm_invocation_refs
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise ResourceIntegrityFault(
                "delegated settlement crossed its prepared parent authority")
        payload = canonicalize_llm_response_payload(response_bytes)
        if (payload != response_bytes
                or len(payload) > prepared.attempt.max_response_bytes):
            raise ResourceIntegrityFault(
                "delegated response is not its canonical bounded return")
        context = self._context(loop)
        invocation = self.kernel._exact_object(
            prepared.attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1").metadata
        if (invocation.get("invocation_kind") != "delegated_subtask"
                or invocation.get("child_session_id")
                != prepared.request.child_session_id
                or invocation.get("local_sequence")
                != prepared.leaf_turn_sequence
                or invocation.get("request_resource_ref")
                != _resource_payload(
                    prepared.prepared_context.request_ref)):
            raise ResourceIntegrityFault(
                "delegated response differs from its child invocation")
        provider = self.provider_attempt_for_agent_llm_v1(prepared.attempt)
        try:
            provider, raw_event, raw_response_ref, _raw_payload = (
                self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                    prepared.attempt.attempt_ref,
                    read_response=lambda ref: self.kernel._read_firing_registered(
                        context, ref)))
        except Exception as exc:
            raise OptionalAgentCapabilityUnavailable(
                "delegated input port has not committed the exact raw return") from exc
        response_ref = ResourceVersionRef(
            _stable_id("resource", "delegated-child-response",
                       prepared.attempt.attempt_ref.version_id),
            _stable_id("resource_version", "delegated-child-response",
                       prepared.attempt.attempt_ref.version_id))
        observed = observe_registered_llm_response(
            payload, PublishedLLMResponse(
                response_ref, prepared.attempt.attempt_ref,
                prepared.attempt.invocation_ref, loop.model_condition,
                len(payload), "llm_response_envelope/v1"))

        def stage_response(tx):
            _append_direct_resource_version_publication(
                tx, ref=response_ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(
                    self.core, ref=response_ref, origin_kind="llm_response",
                    primary=prepared.attempt.attempt_ref,
                    secondary=prepared.attempt.invocation_ref,
                    task_ref=context.task_ref, round_ref=context.task_round_ref,
                    net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json",
                    content_schema_ref="runtime/llm_response_envelope/v1",
                    content_schema_authority_ref=_ref_payload(
                        _registry_type_catalog_ref(self.core)),
                    summary="Parent-owned delegated LLM response",
                    descriptors={"content_role": "llm_response_envelope"},
                    extensions={"registry.llm_response/v1": {
                        "llm_invocation_ref": _ref_payload(
                            prepared.attempt.invocation_ref),
                        "llm_invocation_attempt_ref": _ref_payload(
                            prepared.attempt.attempt_ref),
                        "model_condition": loop.model_condition}},
                    input_resource_refs=(
                        prepared.prepared_context.request_ref,),
                    intended_boundary="not_applicable"),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=idempotency_key,
                direct_owners=(prepared.attempt.invocation_ref,
                               prepared.attempt.attempt_ref),
                input_resources=(prepared.prepared_context.request_ref,))
            self.mechanical_lifecycle._event(
                tx, event_type="provider_attempt_completed/v1",
                aggregate_id=str(provider.attempt_id),
                aggregate_type="provider_attempt",
                payload={
                    "provider_attempt_id": str(provider.attempt_id),
                    "response_version_id": str(
                        raw_response_ref.resource_version_id),
                    "response_observed_event_id": str(raw_event.event_id),
                    "finish_reason": raw_event.payload.get("finish_reason"),
                    "external_request_id": raw_event.payload.get(
                        "external_request_id")},
                producer_invocation_id=context.invocation_ref.entity_id)

        if observed.finish_reason == "length":
            self.mechanical_lifecycle.commit_delegated_response(
                loop=loop,
                turn_ref=prepared.request.parent_agent_turn_ref,
                action_ref=prepared.request.parent_action_ref,
                attempt=prepared.attempt, response_ref=response_ref,
                response_size=len(payload),
                child_session_id=prepared.request.child_session_id,
                local_sequence=prepared.leaf_turn_sequence,
                length_interrupted=True, idempotency_key=idempotency_key,
                stage_response=stage_response)
            return ParentOwnedDelegatedSubtaskLengthReplay(
                local_sequence=prepared.leaf_turn_sequence,
                history_messages=compact_delegated_subtask_history_after_length(
                    prepared.history_messages))

        if observed.tool_calls:
            child_schemas = {
                str(item["name"]): item["arguments"]
                for item in derive_atomic_subtask_tools(
                    parse_agent_tool_catalog(
                        prepared.prepared_context.tool_catalog_payload))}
            tool_results = []
            history_tool_messages = []
            result_candidate_ref = None
            for tool_call in observed.tool_calls:
                validation = validate_agent_tool_observation(
                    loop_id=loop.loop_id,
                    turn_sequence=prepared.leaf_turn_sequence,
                    expected_revision=loop.revision,
                    observation=tool_call,
                    tool_argument_schemas=child_schemas)
                refs = ()
                metadata = None
                if isinstance(validation, AgentToolSyntaxError):
                    result = {
                        "kind": "delegated_tool_error/v1",
                        "code": validation.code,
                        "detail": validation.detail}
                    status = "rejected"
                else:
                    if validation.tool_name == "delegate_leaf":
                        raise ResourceIntegrityFault(
                            "delegated child attempted recursive delegation")
                    registered = self.owner.registration.declaration(
                        "tool", validation.tool_name)
                    try:
                        refs, metadata = self.invoke_tool(
                            execution, validation.tool_name,
                            identity=registered["identity"],
                            contracts=registered["contracts"], kwargs={
                                "service": self, "execution": execution,
                                "loop": loop, "turn": turn,
                                "arguments": validation.arguments,
                                "idempotency_key": (
                                    f"{idempotency_key}:tool:"
                                    f"{tool_call.tool_call_ordinal}")})
                        refs = tuple(refs)
                        result = {
                            "kind": "delegated_tool_result/v1",
                            "result_refs": [
                                _ref_payload(ref) for ref in refs],
                            "result_metadata": metadata}
                        status = "applied"
                        if (isinstance(metadata, Mapping)
                                and metadata.get("kind")
                                == "registered_file_write/v1"):
                            result_candidate_ref = _resource_from_payload(
                                metadata["resource_ref"])
                    except (ValueError, ResourcePayloadSchemaViolation) as exc:
                        result = {
                            "kind": "delegated_tool_error/v1",
                            "code": "tool_rejected",
                            "detail": str(exc)}
                        status = "rejected"
                exact_result = {
                    "tool_call_id": tool_call.tool_call_id,
                    "tool_call_ordinal": tool_call.tool_call_ordinal,
                    "tool_name": tool_call.tool_name,
                    "raw_arguments": tool_call.raw_arguments,
                    "status": status, "result": result}
                tool_results.append(exact_result)
                history_tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.tool_call_id,
                    "content": canonical_json(result).decode("utf-8")})
            assistant_message = {
                "role": "assistant",
                "content": observed.text or "",
                "tool_calls": [{
                    "id": item.tool_call_id, "type": "function",
                    "function": {"name": item.tool_name,
                                 "arguments": item.raw_arguments}}
                    for item in observed.tool_calls],
            }
            if observed.reasoning_content is not None:
                assistant_message["reasoning_content"] = (
                    observed.reasoning_content)
            next_history = (
                *prepared.history_messages, assistant_message,
                *history_tool_messages)
            from cpn.components.request_protocol import (
                validate_llm_request_message_history,
            )
            validate_llm_request_message_history(next_history)
            evidence_ref = ResourceVersionRef(
                _stable_id("resource", "delegated-child-tool-step",
                           prepared.attempt.attempt_ref.version_id),
                _stable_id("resource_version", "delegated-child-tool-step",
                           prepared.attempt.attempt_ref.version_id))
            evidence_payload = canonical_json({
                "schema_version": "parent_owned_delegated_tool_step/v1",
                "child_session_id": prepared.request.child_session_id,
                "local_sequence": prepared.leaf_turn_sequence,
                "parent_action_ref": _ref_payload(
                    prepared.request.parent_action_ref),
                "llm_invocation_ref": _ref_payload(
                    prepared.attempt.invocation_ref),
                "llm_invocation_attempt_ref": _ref_payload(
                    prepared.attempt.attempt_ref),
                "response_resource_ref": _resource_payload(response_ref),
                "tool_results": tool_results})
            original_stage_response = stage_response

            def stage_response_with_evidence(tx):
                original_stage_response(tx)
                _append_direct_resource_version_publication(
                    tx, ref=evidence_ref, payload=evidence_payload,
                    metadata_factory=lambda size: _direct_resource_metadata(
                        self.core, ref=evidence_ref,
                        origin_kind=(
                            "parent_owned_delegated_subtask_tool_step"),
                        primary=prepared.attempt.attempt_ref,
                        secondary=prepared.attempt.invocation_ref,
                        task_ref=context.task_ref,
                        round_ref=context.task_round_ref,
                        net_ref=context.net_instance_ref,
                        producer_ref=context.invocation_ref,
                        lifetime_ref=context.operation_execution_lease_ref,
                        operation_binding_ref=context.operation_binding_ref,
                        agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                        media_type="application/json",
                        content_schema_ref=None,
                        content_schema_authority_ref=None,
                        summary="Delegated child tool-step evidence",
                        descriptors={"content_role": (
                            "delegated_subtask_tool_step")},
                        extensions={
                            "registry.parent_owned_delegated_subtask/v1": {
                                "parent_action_id": prepared.request.action_id,
                                "child_session_id": (
                                    prepared.request.child_session_id),
                                "local_sequence": (
                                    prepared.leaf_turn_sequence)}},
                        input_resource_refs=(response_ref,),
                        intended_boundary="not_applicable"),
                    media_type="application/json",
                    producer_ref=context.invocation_ref,
                    producer_invocation_id=context.invocation_ref.entity_id,
                    relation_key=idempotency_key,
                    direct_owners=(prepared.attempt.invocation_ref,
                                   prepared.attempt.attempt_ref),
                    input_resources=(response_ref,))

            self.mechanical_lifecycle.commit_delegated_response(
                loop=loop,
                turn_ref=prepared.request.parent_agent_turn_ref,
                action_ref=prepared.request.parent_action_ref,
                attempt=prepared.attempt, response_ref=response_ref,
                response_size=len(payload),
                child_session_id=prepared.request.child_session_id,
                local_sequence=prepared.leaf_turn_sequence,
                length_interrupted=False, idempotency_key=idempotency_key,
                stage_response=stage_response_with_evidence)
            return ParentOwnedDelegatedSubtaskToolStep(
                leaf_turn_sequence=prepared.leaf_turn_sequence,
                llm_invocation_ref=prepared.attempt.invocation_ref,
                llm_invocation_attempt_ref=prepared.attempt.attempt_ref,
                response_resource_ref=response_ref,
                tool_step_evidence_ref=evidence_ref,
                history_messages=next_history,
                result_candidate_ref=result_candidate_ref)

        if not isinstance(observed.text, str) or not observed.text.strip():
            raise ResourceIntegrityFault(
                "delegated terminal result requires concrete response text")
        self.mechanical_lifecycle.commit_delegated_response(
            loop=loop, turn_ref=prepared.request.parent_agent_turn_ref,
            action_ref=prepared.request.parent_action_ref,
            attempt=prepared.attempt, response_ref=response_ref,
            response_size=len(payload),
            child_session_id=prepared.request.child_session_id,
            local_sequence=prepared.leaf_turn_sequence,
            length_interrupted=False, idempotency_key=idempotency_key,
            stage_response=stage_response)
        return ParentOwnedDelegatedSubtaskResult(
            parent_invocation_ref=prepared.request.parent_invocation_ref,
            sponsoring_transition_firing_ref=(
                prepared.request.sponsoring_transition_firing_ref),
            accounting_parent_invocation_ref=(
                prepared.request.accounting_parent_invocation_ref),
            parent_operation_binding_ref=(
                prepared.request.parent_operation_binding_ref),
            parent_operation_execution_lease_ref=(
                prepared.request.parent_operation_execution_lease_ref),
            parent_agent_loop_ref=prepared.request.parent_agent_loop_ref,
            parent_agent_turn_ref=prepared.request.parent_agent_turn_ref,
            parent_action_ref=prepared.request.parent_action_ref,
            action_id=prepared.request.action_id,
            tool_call_id=prepared.request.tool_call_id,
            tool_call_ordinal=prepared.request.tool_call_ordinal,
            raw_arguments=prepared.request.raw_arguments,
            llm_invocation_ref=prepared.attempt.invocation_ref,
            llm_invocation_attempt_ref=prepared.attempt.attempt_ref,
            result_resource_ref=prepared.request.result_resource_ref,
            local_sequence=prepared.leaf_turn_sequence,
            child_session_id=prepared.request.child_session_id)


import time

from cpn.rpnh.llm_contracts import LLMInputPortFailure
from cpn.rpnh.response_protocol import LLMResponseProtocolError
from .compact import ContextPressurePolicy, ContextReductionSettings
from .models import (
    AgentLoopProtocolError, AgentTurnTimingEvidence,
)
from .service import (
    CompletedAgentContextCompaction, CompletedAgentLLMInvocation,
    PreparedAgentContextCompaction, PreparedAgentLLMTurn,
    PreparedParentOwnedDelegatedSubtaskCall, _LLMExecutionBlock,
)


def request_delegated_subtask_turn_v1(
        self, prepared: PreparedParentOwnedDelegatedSubtaskCall, *,
        idempotency_key: str) -> bytes | None:
    """Dispatch one local subtask call without constructing an AgentLoop."""

    if (not isinstance(prepared, PreparedParentOwnedDelegatedSubtaskCall)
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise AgentLoopProtocolError(
            "delegated subtask dispatch requires one prepared local call")
    attempt = prepared.attempt
    failure: LLMInputPortFailure | None = None
    try:
        with self._registry.prepared_agent_turn_context_scope_v1(
                prepared.prepared_context):
            response_bytes = self._input_port.request_once(attempt)
    except LLMInputPortFailure as caught:
        failure = caught
        response_bytes = None
    disposition = failure.disposition if failure is not None else "success"
    failure_code = failure.failure_code if failure is not None else None
    submission_state = (
        failure.submission_state if failure is not None else None)
    canonical = None
    if disposition != "success" and response_bytes is None:
        pass
    elif not isinstance(response_bytes, bytes):
        raise AgentLoopProtocolError(
            "delegated subtask input returned no successful response bytes")
    else:
        try:
            canonical = canonicalize_llm_response_payload(response_bytes)
            if canonical != response_bytes:
                raise LLMResponseProtocolError(
                    "delegated subtask response is not canonical")
        except LLMResponseProtocolError:
            disposition = "protocol_rejected"
            failure_code = "framework_response_protocol_invalid"
            submission_state = "response_observed"
    if disposition != "success":
        retry = self._registry.record_llm_invocation_failure_v1(
            prepared.prepared_context.loop, attempt, disposition,
            failure_code=failure_code,
            submission_state=submission_state,
            idempotency_key=(
                f"{idempotency_key}:attempt:"
                f"{attempt.attempt_ref.version_id}:{disposition}"))
        if retry:
            return None
        raise AgentLoopProtocolError(
            failure_code or "delegated subtask LLM attempt is blocked")
    if canonical is None:
        raise AgentLoopProtocolError(
            "delegated subtask canonical response is unavailable")
    return canonical
