"""AgentLoop compaction mechanics.

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


class CompactionRecordsMechanicsMixin:
    def begin_context_compaction(
            self, loop: AgentLoopSnapshot, *, idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> AgentLoopSnapshot:
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,))
        return self.transition_loop(
            current, AgentLoopState.COMPACTING,
            idempotency_key=idempotency_key, stage=stage, events=events)

    def complete_context_compaction(
            self, *, before: AgentLoopSnapshot, attempt: LLMCallAttempt,
            compaction_ref: VersionRef,
            document_factory: Callable[
                [AgentLoopSnapshot, VersionRef], Mapping[str, Any]],
            response_ref: ResourceVersionRef, response_size: int,
            stage_response: Callable[[Any], None],
            idempotency_key: str,
    ) -> tuple[AgentLoopSnapshot, VersionRef]:
        """Atomically close one compaction attempt and resume its Loop."""
        if (not isinstance(compaction_ref, VersionRef)
                or compaction_ref.entity_type
                != "agent_context_compaction/v3"
                or not isinstance(attempt, LLMCallAttempt)
                or not isinstance(response_ref, ResourceVersionRef)
                or isinstance(response_size, bool)
                or not isinstance(response_size, int) or response_size < 1
                or not callable(document_factory)
                or not callable(stage_response)):
            raise AgentLoopMechanicalLifecycleError(
                "context compaction completion authority is incomplete")
        existing = self.core.event_store.object_row(compaction_ref.version_id)
        if existing is not None:
            value = dict(self.kernel._exact_object(
                compaction_ref,
                expected_type="agent_context_compaction/v3").metadata)
            waiting = self.hydrate_loop(
                _version_from_payload(value["agent_loop_ref"]))
            expected = dict(document_factory(waiting, compaction_ref))
            if (value != expected
                    or value.get("llm_invocation_ref")
                    != _ref_payload(attempt.invocation_ref)
                    or value.get("llm_invocation_attempt_ref")
                    != _ref_payload(attempt.attempt_ref)):
                raise AgentLoopMechanicalLifecycleError(
                    "compaction replay differs from its exact authority")
            return waiting, compaction_ref
        current = self.require_current_revision(
            before, allowed_states=(AgentLoopState.COMPACTING,))
        self._validate_attempt_for_loop(
            current, attempt, invocation_kinds=("context_compaction",))
        waiting = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            state=AgentLoopState.WAITING_FOR_LLM,
            revision=current.revision + 1)
        document = dict(document_factory(waiting, compaction_ref))
        if (document.get("agent_context_compaction_ref")
                != _ref_payload(compaction_ref)
                or document.get("agent_loop_ref")
                != _ref_payload(self.loop_ref(waiting))
                or document.get("llm_invocation_ref")
                != _ref_payload(attempt.invocation_ref)
                or document.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt.attempt_ref)):
            raise AgentLoopMechanicalLifecycleError(
                "compaction document crosses its exact completion identities")

        def stage(tx: Any) -> None:
            stage_response(tx)
            self.prewrite_object(
                tx, compaction_ref, document,
                producer_invocation_id=current.invocation_ref.entity_id)

        registered = {
            "llm_invocation_ref": _ref_payload(attempt.invocation_ref),
            "llm_invocation_attempt_ref": _ref_payload(attempt.attempt_ref),
            "response_resource_ref": _resource_payload(response_ref),
            "response_size": response_size,
        }
        events = (
            ("llm_response_registered/v1", str(attempt.attempt_ref.entity_id),
             "llm_invocation_attempt", registered),
            ("llm_invocation_succeeded/v1",
             str(attempt.attempt_ref.entity_id), "llm_invocation_attempt", {
                 **{key: value for key, value in registered.items()
                    if key != "response_size"},
                 "agent_context_compaction_ref": _ref_payload(compaction_ref),
                 "turn_sequence": current.next_turn_sequence,
             }),
        )
        committed = self.commit_compaction_suffix(
            before=current, after=waiting, idempotency_key=idempotency_key,
            stage=stage, events=events)
        return committed, compaction_ref

    def _validate_attempt_for_loop(
            self, loop: AgentLoopSnapshot, attempt: LLMCallAttempt, *,
            invocation_kinds: Sequence[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        invocation = self.exact_object_document(
            attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1")
        attempt_document = self.exact_object_document(
            attempt.attempt_ref,
            expected_type="llm_invocation_attempt/v1")
        invocation_kind = invocation.get("invocation_kind")
        required_state = {
            "normal_turn": AgentLoopState.WAITING_FOR_LLM,
            "delegated_subtask": AgentLoopState.TURN_STORED,
            "context_compaction": AgentLoopState.COMPACTING,
        }.get(invocation_kind)
        if (invocation.get("agent_loop_ref")
                != _ref_payload(self.loop_ref(loop))
                or invocation_kind not in tuple(invocation_kinds)
                or required_state is None
                or loop.state != required_state
                or invocation.get("model_condition") != loop.model_condition
                or attempt_document.get("llm_invocation_ref")
                != _ref_payload(attempt.invocation_ref)
                or attempt_document.get("attempt_ordinal")
                != attempt.attempt_ordinal
                or attempt.model_condition != loop.model_condition):
            raise AgentLoopMechanicalLifecycleError(
                "attempt differs from its exact Loop authority")
        return invocation, attempt_document

    def record_context_compaction_failure(
            self, *, prepared: Any, disposition: str,
            maximum_attempts: int, idempotency_key: str,
            failure_code: str | None = None,
            submission_state: str | None = None,
            events: Sequence[
                tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> tuple[bool, str]:
        loop = getattr(prepared, "loop", None)
        attempt = getattr(prepared, "attempt", None)
        compaction_id = getattr(prepared, "compaction_id", None)
        if (not isinstance(loop, AgentLoopSnapshot)
                or not isinstance(attempt, LLMCallAttempt)
                or not isinstance(compaction_id, str) or not compaction_id):
            raise AgentLoopMechanicalLifecycleError(
                "compaction failure lacks prepared authority")
        allowed = self.record_invocation_failure(
            loop=loop, attempt=attempt, disposition=disposition,
            maximum_attempts=maximum_attempts,
            idempotency_key=idempotency_key,
            invocation_kinds=("context_compaction",),
            allowed_states=(AgentLoopState.COMPACTING,),
            failure_code=failure_code, submission_state=submission_state,
            events=events)
        return allowed, compaction_id

    def commit_compaction_suffix(
            self, *, before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> AgentLoopSnapshot:
        if (before.state, after.state) not in {
                (AgentLoopState.WAITING_FOR_LLM, AgentLoopState.COMPACTING),
                (AgentLoopState.COMPACTING, AgentLoopState.WAITING_FOR_LLM)}:
            raise AgentLoopMechanicalLifecycleError(
                "compaction suffix has the wrong Loop states")
        return self.commit_loop_successor(
            before, after, idempotency_key=idempotency_key, stage=stage,
            events=events)


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




__all__ = ["CompactionRecordsMechanicsMixin"]


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


class CompactionExecutionMixin:
    def agent_context_pressure_requires_compaction_v1(self, execution, loop, catalog, prepared_context, *, pressure_policy):
        self._execution(execution, loop)
        if (prepared_context.loop != loop
                or prepared_context.target.context_window_tokens
                != pressure_policy.context_window_tokens
                or prepared_context.target.max_output_tokens
                != pressure_policy.reserved_output_tokens):
            raise ResourceIntegrityFault("optional pressure decision crossed prepared context")
        if not prepared_context.semantic_context_turn_refs:
            return False
        previous_request_bytes = previous_input_tokens = None
        events = self.mechanical_lifecycle.turn_events(loop)
        if events:
            attempt_ref = _version_from_payload(
                events[-1].payload["llm_invocation_attempt_ref"])
            try:
                _provider, _raw_event, _raw_ref, raw_payload = (
                    self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                        attempt_ref,
                        read_response=lambda ref: self.kernel._read_firing_registered(
                            self._context(loop), ref)))
                raw_document = json.loads(raw_payload)
                usage = raw_document.get("usage")
                if isinstance(usage, Mapping):
                    value = usage.get("input_tokens")
                    if (isinstance(value, int)
                            and not isinstance(value, bool) and value >= 0):
                        previous_input_tokens = value
                invocation = self.mechanical_lifecycle.exact_object_document(
                    _version_from_payload(
                        events[-1].payload["llm_invocation_ref"]),
                    expected_type="llm_invocation_spec/v1")
                previous_request_bytes = len(
                    self.kernel._read_firing_registered(
                        self._context(loop),
                        _resource_from_payload(
                            invocation["request_resource_ref"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                previous_request_bytes = previous_input_tokens = None
        return should_compact(
            pressure_policy, self._envelope(prepared_context, catalog),
            previous_request_bytes=previous_request_bytes,
            previous_input_tokens=previous_input_tokens)

    @staticmethod
    def _compaction_ref(compaction_id):
        return VersionRef(
            "agent_context_compaction/v3",
            TypedId.parse(compaction_id, expected="agent_context_compaction"),
            _stable_id("agent_context_compaction_version", compaction_id))

    def _publish_compaction_request(
            self, loop, envelope, prompt_ref, input_refs, *, compaction_id,
            idempotency_key):
        context = self._context(loop)
        recipe = dict(envelope)
        recipe.pop("protocol")
        recipe["schema_version"] = "logical_provider_request_recipe/v1"
        recipe["model"] = recipe.pop("model_condition")
        recipe["max_tokens"] = recipe.pop("max_output_tokens")
        payload = canonical_json(recipe)
        self.core.catalog.validate_schema_ref(
            "registry_v1/logical_provider_request_recipe/v1", recipe)
        ref = ResourceVersionRef(
            _stable_id("resource", "optional-context-compaction", compaction_id),
            _stable_id(
                "resource_version", "optional-context-compaction",
                compaction_id))
        inputs = tuple(input_refs)

        def stage_request(tx):
            _append_direct_resource_version_publication(
                tx, ref=ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(
                    self.core, ref=ref, origin_kind="provider_request",
                    primary=context.operation_binding_ref,
                    secondary=self.mechanical_lifecycle.loop_ref(loop), task_ref=context.task_ref,
                    round_ref=context.task_round_ref,
                    net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json",
                    content_schema_ref=(
                        "registry_v1/logical_provider_request_recipe/v1"),
                    content_schema_authority_ref=_ref_payload(
                        _registry_type_catalog_ref(self.core)),
                    summary="Optional AgentLoop context compaction request",
                    descriptors={
                        "content_role": "optional_agent_compaction_request"},
                    extensions={}, input_resource_refs=tuple(sorted(
                        inputs,
                        key=lambda item: canonical_json(
                            _resource_payload(item)))),
                    intended_boundary="llm_prompt"),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=idempotency_key,
                direct_owners=(context.operation_binding_ref, self.mechanical_lifecycle.loop_ref(loop)),
                input_resources=inputs)

        def verify_replay(candidate):
            if (candidate != ref
                    or self.kernel._read_firing_registered(
                        context, candidate) != payload):
                raise ResourceIntegrityFault(
                    "compaction request replay differs from exact bytes")

        self.mechanical_lifecycle.commit_request_authority(
            idempotency_key=idempotency_key, stage_request=stage_request,
            loop=loop, request_ref=ref, verify_replay=verify_replay)
        return ref

    def _compaction_invocation_attempts(self, invocation_ref, envelope, target):
        values = []
        for row in self.core.event_store.object_rows_by_type(
                "llm_invocation_attempt/v1"):
            value = json.loads(str(row["metadata_json"]))
            if value.get("llm_invocation_ref") != _ref_payload(invocation_ref):
                continue
            ref = _version_from_payload(value["llm_invocation_attempt_ref"])
            values.append((int(value["attempt_ordinal"]), LLMCallAttempt(
                invocation_ref, ref, int(value["attempt_ordinal"]),
                target.model_condition, envelope,
                target.max_response_bytes)))
        values.sort(key=lambda item: item[0])
        return tuple(attempt for _ordinal, attempt in values)

    def _compaction_envelope(
            self, execution, loop, catalog, reduction_settings):
        context = self._context(loop)
        initialization = self.prepare_agent_system_initialization_v1(
            execution, loop, catalog)
        prompt_ref, _prompt_prepared, prompt_payload = self._static(
            context, "optional_agent_prompt")
        catalog_ref, _catalog_prepared, _catalog_payload = self._static(
            context, "optional_agent_tool_catalog")
        if catalog_ref != loop.tool_catalog_ref:
            raise ResourceIntegrityFault(
                "compaction crossed its exact tool catalog")

        def render_tool_result(action, action_ref):
            if action.tool_error_ref is not None:
                return self.kernel._exact_object(
                    action.tool_error_ref).metadata
            if not isinstance(action.result_metadata, Mapping):
                raise ResourceIntegrityFault(
                    "compaction history action lacks result metadata")
            from .compact import prior_tool_result_reference
            try:
                return prior_tool_result_reference(
                    action.result_metadata, _ref_payload(action_ref))
            except ValueError:
                return action.result_metadata

        history = self.mechanical_lifecycle.history_messages(
            loop,
            read_response=lambda ref: self.kernel._read_firing_registered(
                context, ref),
            render_tool_result=render_tool_result)
        history = reduce_tool_messages(
            history, byte_limit=reduction_settings.tool_output_byte_limit)
        prompt = json.loads(prompt_payload)
        target = self._target(context)
        envelope = materialize_agent_request_envelope(
            model_condition=target.model_condition,
            max_output_tokens=target.max_output_tokens,
            system_content=initialization.payload.decode("utf-8"),
            prompt_messages=prompt["messages"], history_messages=history,
            tool_descriptors=catalog.tool_descriptors,
            source_prompt_ref=_resource_payload(prompt_ref),
            tool_catalog_ref=_resource_payload(loop.tool_catalog_ref),
            checkpoint_prompt=reduction_settings.checkpoint_prompt)
        self.core.catalog.validate_schema_ref(
            "runtime/llm_request_envelope/v1", envelope)
        return canonical_json(envelope), prompt_ref, initialization, target

    def prepare_agent_context_compaction_v1(self, execution, loop, catalog, *, trigger_reason, force,
            reduction_settings, pressure_policy, compaction_id, idempotency_key):
        execution = self._execution(execution)
        if (trigger_reason not in {
                "context_pressure", "response_length", "turn_cap"}
                or not isinstance(force, bool)
                or force != (trigger_reason in {"response_length", "turn_cap"})
                or compaction_id is not None
                and not isinstance(compaction_id, str)):
            raise ResourceIntegrityFault(
                "optional compaction preparation arguments are invalid")
        lifecycle_id = compaction_id or str(_stable_id(
            "agent_context_compaction", loop.loop_id, idempotency_key))
        compaction_ref = self._compaction_ref(lifecycle_id)
        existing = self.core.event_store.object_row(compaction_ref.version_id)
        if existing is not None:
            document = self.mechanical_lifecycle.exact_object_document(
                compaction_ref,
                expected_type="agent_context_compaction/v3")
            if (document.get("trigger_reason") != trigger_reason
                    or document.get("agent_context_compaction_id")
                    != lifecycle_id):
                raise ResourceIntegrityFault(
                    "completed compaction replay differs from its request")
            waiting = self.mechanical_lifecycle.hydrate_loop(
                _version_from_payload(document["agent_loop_ref"]))
            return CompletedAgentContextCompaction(waiting, compaction_ref)

        current = self.mechanical_lifecycle.latest_loop(loop.loop_id)
        if (current is None or current.invocation_ref != loop.invocation_ref
                or execution.operation.canonical.context
                != self._context(current)):
            raise ResourceIntegrityFault(
                "optional compaction crossed its exact execution")
        if current.state == AgentLoopState.WAITING_FOR_LLM:
            current = self.mechanical_lifecycle.begin_context_compaction(
                current,
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:begin"))
        elif current.state != AgentLoopState.COMPACTING:
            raise StaleAuthorityHead(
                "optional compaction requires a waiting or compacting loop")

        covered = self.mechanical_lifecycle.prior_turn_refs(current)
        if trigger_reason in {"context_pressure", "turn_cap"} and not covered:
            raise ResourceIntegrityFault(
                "optional compaction requires nonempty contiguous turn closure")
        interruption = self.mechanical_lifecycle.hydrate_length_interruption(
            current, sequence=current.next_turn_sequence,
            read_response=lambda ref: self.kernel._read_firing_registered(
                self._context(current), ref))
        if ((trigger_reason == "response_length")
                != isinstance(interruption, AgentLengthInterruptionRecord)):
            raise ResourceIntegrityFault(
                "response-length compaction requires its exact interruption")

        envelope, prompt_ref, initialization, target = (
            self._compaction_envelope(
                execution, current, catalog, reduction_settings))
        context = self._context(current)
        binding, _, _ = self._declared(context)
        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        if context.agent_ref is None:
            raise ResourceIntegrityFault(
                "optional compaction requires one exact registered agent")
        invocation_ref = VersionRef(
            "llm_invocation_spec/v1",
            _stable_id("llm_invocation", lifecycle_id),
            _stable_id("llm_invocation_version", lifecycle_id))
        existing_attempts = self._compaction_invocation_attempts(
            invocation_ref, envelope, target)
        if existing_attempts:
            prior_attempt = existing_attempts[-1]
            terminals = tuple(
                event for event in
                self.core.event_store.list_events_by_aggregate(
                    str(prior_attempt.attempt_ref.entity_id))
                if event.event_type in {
                    "llm_invocation_failed/v1",
                    "llm_invocation_interrupted/v1",
                    "llm_invocation_owner_interrupted/v1",
                    "llm_invocation_succeeded/v1"})
            if not terminals:
                self.provider_attempt_for_agent_llm_v1(prior_attempt)
                return PreparedAgentContextCompaction(
                    prior_attempt, current, lifecycle_id, trigger_reason,
                    force, reduction_settings, pressure_policy, covered,
                    context.agent_ref, context.agent_ref,
                    interruption.llm_invocation_ref if interruption else None,
                    interruption.llm_invocation_attempt_ref
                    if interruption else None,
                    interruption.response_resource_ref
                    if interruption else None)
            if terminals[-1].event_type != "llm_invocation_failed/v1":
                raise ResourceIntegrityFault(
                    "settled compaction attempt lacks its completed document")
            prior_provider = self._provider_for_ref(
                prior_attempt.attempt_ref)
            prior_call = prior_provider.call
            ordinal = prior_attempt.attempt_ordinal + 1
            # A protocol-rejected compaction response is a completed provider
            # call whose bytes failed the neutral AgentLoop contract.  It is
            # not a transport retry of that provider call, so reserve a fresh
            # logical call while retaining the same compaction invocation and
            # request recipe.
            call = self.ledger.create_call_v2(
                context=context,
                loop_ref=prior_call.loop_ref,
                turn_sequence=prior_call.turn_sequence,
                request_resource_ref=prior_call.request_resource_ref,
                terminal_delivery_ref=prior_call.terminal_delivery_ref,
                semantic_prompt_resource_ref=(
                    prior_call.semantic_prompt_resource_ref),
                llm_execution_target_ref=prior_call.llm_execution_target_ref,
                backend=prior_call.backend, model=prior_call.model,
                transport_contract_ref=prior_call.transport_contract_ref,
                interaction_protocol_ref=prior_call.interaction_protocol_ref,
                response_adapter_ref=prior_call.response_adapter_ref,
                tool_catalog_ref=prior_call.tool_catalog_ref,
                prior_turn_refs=prior_call.prior_turn_refs,
                timeout_seconds=prior_call.timeout_seconds,
                max_response_bytes=prior_call.max_response_bytes,
                call_id=_stable_id("llm_call", lifecycle_id, ordinal),
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:"
                    f"call:{ordinal}"))
            provider = self.ledger.reserve_v2(
                context=context, call=call, prior_attempt=None,
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:"
                    f"provider:{ordinal}"))
            request_ref = call.request_resource_ref
        else:
            inputs = (
                prompt_ref, current.tool_catalog_ref,
                *(item.resource_ref for item in initialization.located_inputs))
            request_ref = self._publish_compaction_request(
                current, json.loads(envelope), prompt_ref, inputs,
                compaction_id=lifecycle_id,
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:request"))
            delivery, _, _ = self._deliver(
                context, request_ref, "llm_prompt",
                f"optional-context-compaction:{lifecycle_id}:delivery")
            backend_ref, _, backend_payload = self._static(
                context, "optional_agent_backend")
            transport_ref, _, transport_payload = self._static(
                context, "optional_agent_transport")
            backend, transport = (
                json.loads(backend_payload), json.loads(transport_payload))
            prior_calls = tuple(
                self._provider_for_ref(_version_from_payload(
                    event.payload["llm_invocation_attempt_ref"])).call.ref
                for event in self.mechanical_lifecycle.turn_events(current))
            call = self.ledger.create_call_v2(
                context=context, loop_ref=_ref_payload(self.mechanical_lifecycle.loop_ref(current)),
                turn_sequence=current.next_turn_sequence,
                request_resource_ref=request_ref,
                terminal_delivery_ref=delivery.delivery_ref,
                semantic_prompt_resource_ref=prompt_ref,
                llm_execution_target_ref=backend_ref,
                backend=backend["backend"], model=backend["model"],
                transport_contract_ref=transport_ref,
                interaction_protocol_ref=transport[
                    "interaction_protocol_ref"],
                response_adapter_ref=transport["response_adapter_ref"],
                tool_catalog_ref=current.tool_catalog_ref,
                prior_turn_refs=prior_calls,
                timeout_seconds=backend["timeout_seconds"],
                max_response_bytes=target.max_response_bytes,
                call_id=_stable_id("llm_call", lifecycle_id),
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:call"))
            provider = self.ledger.reserve_v2(
                context=context, call=call, prior_attempt=None,
                idempotency_key=(
                    f"optional-context-compaction:{lifecycle_id}:provider:0"))

        invocation = {
            "llm_invocation_id": str(invocation_ref.entity_id),
            "llm_invocation_version_id": str(invocation_ref.version_id),
            "llm_invocation_ref": _ref_payload(invocation_ref),
            "invocation_ref": _ref_payload(context.invocation_ref),
            "operation_binding_ref": _ref_payload(
                context.operation_binding_ref),
            "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(current)),
            "subject_agent_ref": _ref_payload(context.agent_ref),
            "execution_agent_ref": _ref_payload(context.agent_ref),
            "invocation_kind": "context_compaction",
            "turn_sequence": current.next_turn_sequence,
            "request_resource_ref": _resource_payload(request_ref),
            "semantic_prompt_resource_ref": _resource_payload(prompt_ref),
            "llm_input_target_ref": _resource_payload(target_ref),
            "model_condition": current.model_condition,
            "tool_catalog_ref": _resource_payload(current.tool_catalog_ref),
            "prior_turn_refs": [_ref_payload(ref) for ref in covered],
            "max_response_bytes": target.max_response_bytes,
        }
        attempt = self.mechanical_lifecycle.reserve_invocation_attempt(
            loop=current, invocation_ref=invocation_ref,
            invocation_document=invocation,
            producer_invocation_id=context.invocation_ref.entity_id,
            reservation_class=provider.reservation_class,
            finalization_scope=provider.finalization_scope,
            model_condition=current.model_condition,
            canonical_request_bytes=envelope,
            max_response_bytes=target.max_response_bytes,
            maximum_attempts=2, provider_attempt_ref=provider.ref,
            idempotency_key=(
                f"optional-context-compaction:{lifecycle_id}:neutral"))
        return PreparedAgentContextCompaction(
            attempt, current, lifecycle_id, trigger_reason, force,
            reduction_settings, pressure_policy, covered,
            context.agent_ref, context.agent_ref,
            interruption.llm_invocation_ref if interruption else None,
            interruption.llm_invocation_attempt_ref if interruption else None,
            interruption.response_resource_ref if interruption else None)

    def complete_agent_context_compaction_v1(self, prepared, response_bytes, *, idempotency_key):
        if not isinstance(prepared, PreparedAgentContextCompaction):
            raise TypeError("optional compaction completion requires preparation")
        payload = canonicalize_llm_response_payload(response_bytes)
        if (payload != response_bytes
                or len(payload) > prepared.attempt.max_response_bytes):
            raise ResourceIntegrityFault(
                "compaction response is not its canonical bounded envelope")
        provider, raw_event, raw_response_ref, _raw_payload = (
            self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                prepared.attempt.attempt_ref,
                read_response=lambda ref: self.kernel._read_firing_registered(
                    self._context(prepared.loop), ref)))
        response_ref = ResourceVersionRef(
            _stable_id(
                "resource", "optional-compaction-response",
                prepared.compaction_id,
                prepared.attempt.attempt_ref.version_id),
            _stable_id(
                "resource_version", "optional-compaction-response",
                prepared.compaction_id,
                prepared.attempt.attempt_ref.version_id))
        observed = observe_registered_llm_response(
            payload, PublishedLLMResponse(
                response_ref, prepared.attempt.attempt_ref,
                prepared.attempt.invocation_ref,
                prepared.loop.model_condition, len(payload),
                "llm_response_envelope/v1"))
        if (not isinstance(observed.text, str)
                or not observed.text.strip() or observed.tool_calls
                or observed.finish_reason == "length"):
            raise ResourceIntegrityFault(
                "compaction response requires one complete text summary")
        compaction_ref = self._compaction_ref(prepared.compaction_id)
        context = self._context(prepared.loop)
        invocation = self.mechanical_lifecycle.exact_object_document(
            prepared.attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1")
        request_messages = json.loads(
            prepared.attempt.canonical_request_bytes)["messages"]
        prompt = json.loads(self.kernel._read_firing_registered(
            context, _resource_from_payload(
                invocation["semantic_prompt_resource_ref"])))
        prompt_messages = prompt.get("messages")
        if (not isinstance(prompt_messages, list)
                or len(request_messages) < len(prompt_messages) + 2
                or request_messages[-1] != {
                    "role": "system",
                    "content": prepared.reduction_settings.checkpoint_prompt,
                }):
            raise ResourceIntegrityFault(
                "compaction request cannot recover its effective history")
        effective_history = request_messages[
            1 + len(prompt_messages):-1]
        previous_overlay = (
            self.mechanical_lifecycle.latest_context_overlay(prepared.loop))
        source_session_ordinal = (
            previous_overlay.context_session_ordinal
            if previous_overlay is not None else 0)
        if previous_overlay is not None:
            prior_prefix = list(previous_overlay.model_visible_messages[:2])
            if effective_history[:2] != prior_prefix:
                raise ResourceIntegrityFault(
                    "compaction history differs from its current session")
            retainable_history = effective_history[2:]
        else:
            retainable_history = effective_history

        def document_factory(waiting, exact_ref):
            first_sequence = 0
            last_sequence = len(prepared.semantic_context_turn_refs) - 1
            capsule = {
                "kind": "agent_context_fact_capsule",
                "schema_version": "agent_context_fact_capsule/v1",
                "task_ref": _ref_payload(context.task_ref),
                "task_round_ref": _ref_payload(context.task_round_ref),
                "net_instance_ref": _ref_payload(context.net_instance_ref),
                "transition_firing_ref": (
                    _ref_payload(context.own_transition_firing_ref)
                    if context.own_transition_firing_ref is not None else None),
                "invocation_ref": _ref_payload(context.invocation_ref),
                "operation_binding_ref": _ref_payload(
                    context.operation_binding_ref),
                "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(waiting)),
                "subject_agent_ref": _ref_payload(
                    prepared.subject_agent_ref),
                "execution_agent_ref": _ref_payload(
                    prepared.execution_agent_ref),
                "source_context_session_ordinal": source_session_ordinal,
                "context_session_ordinal": source_session_ordinal + 1,
                "loop_revision": waiting.revision,
                "loop_state": waiting.state.value,
                "trigger_reason": prepared.trigger_reason,
                "first_turn_sequence": first_sequence,
                "last_turn_sequence": last_sequence,
                "covered_turn_count": len(
                    prepared.semantic_context_turn_refs),
                "covered_turn_refs": [
                    _ref_payload(ref)
                    for ref in prepared.semantic_context_turn_refs],
                "semantic_prompt_ref": invocation["semantic_prompt_resource_ref"],
                "located_inputs": [], "settled_actions": [],
                "current_candidate_ref": (
                    _ref_payload(waiting.current_candidate_ref)
                    if waiting.current_candidate_ref is not None else None),
                "adopted_candidate_ref": (
                    _ref_payload(waiting.adopted_candidate_ref)
                    if waiting.adopted_candidate_ref is not None else None),
                "output_resource_refs": [
                    _resource_payload(ref)
                    for ref in waiting.written_resource_refs],
                "disposition_ref": (
                    _ref_payload(waiting.disposition_ref)
                    if waiting.disposition_ref is not None else None),
                "workspace_binding_ref": (
                    _ref_payload(waiting.workspace_binding_ref)
                    if waiting.workspace_binding_ref is not None else None),
                "workspace_lineage_ref": (
                    _ref_payload(waiting.workspace_lineage_ref)
                    if waiting.workspace_lineage_ref is not None else None),
                "workspace_base_revision_ref": (
                    _ref_payload(waiting.workspace_base_revision_ref)
                    if waiting.workspace_base_revision_ref is not None else None),
                "workspace_revision_ref": (
                    _ref_payload(waiting.workspace_revision_ref)
                    if waiting.workspace_revision_ref is not None else None),
                "workspace_changed_paths": [],
                "workspace_deleted_paths": [],
            }
            replacement = build_replacement_history(
                retainable_history, observed.text,
                retained_history_token_limit=(
                    prepared.reduction_settings.retained_history_token_limit),
                fact_capsule=capsule)
            document = {
                "agent_context_compaction_id": prepared.compaction_id,
                "agent_context_compaction_version_id": str(
                    exact_ref.version_id),
                "agent_context_compaction_ref": _ref_payload(exact_ref),
                "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(waiting)),
                "subject_agent_ref": _ref_payload(
                    prepared.subject_agent_ref),
                "execution_agent_ref": _ref_payload(
                    prepared.execution_agent_ref),
                "source_context_session_ordinal": source_session_ordinal,
                "context_session_ordinal": source_session_ordinal + 1,
                "trigger_reason": prepared.trigger_reason,
                "first_turn_sequence": first_sequence,
                "last_turn_sequence": last_sequence,
                "covered_turn_refs": [
                    _ref_payload(ref)
                    for ref in prepared.semantic_context_turn_refs],
                "llm_invocation_ref": _ref_payload(
                    prepared.attempt.invocation_ref),
                "llm_invocation_attempt_ref": _ref_payload(
                    prepared.attempt.attempt_ref),
                "replacement_history": list(replacement),
            }
            if prepared.interrupted_invocation_ref is not None:
                document.update(
                    interrupted_llm_invocation_ref=_ref_payload(
                        prepared.interrupted_invocation_ref),
                    interrupted_llm_invocation_attempt_ref=_ref_payload(
                        prepared.interrupted_invocation_attempt_ref),
                    interrupted_response_resource_ref=_resource_payload(
                        prepared.interrupted_response_resource_ref))
            return document

        def stage_response(tx):
            _append_direct_resource_version_publication(
                tx, ref=response_ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(
                    self.core, ref=response_ref, origin_kind="llm_response",
                    primary=prepared.attempt.attempt_ref,
                    secondary=prepared.attempt.invocation_ref,
                    task_ref=context.task_ref,
                    round_ref=context.task_round_ref,
                    net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(prepared.loop),
                    payload_size=size, media_type="application/json",
                    content_schema_ref="runtime/llm_response_envelope/v1",
                    content_schema_authority_ref=_ref_payload(
                        _registry_type_catalog_ref(self.core)),
                    summary="Optional AgentLoop context compaction summary",
                    descriptors={
                        "content_role": "agent_context_compaction_summary"},
                    extensions={"registry.llm_response/v1": {
                        "llm_invocation_ref": _ref_payload(
                            prepared.attempt.invocation_ref),
                        "llm_invocation_attempt_ref": _ref_payload(
                            prepared.attempt.attempt_ref),
                        "model_condition": prepared.loop.model_condition}},
                    input_resource_refs=(
                        _resource_from_payload(
                            invocation["request_resource_ref"]),),
                    intended_boundary="not_applicable"),
                media_type="application/json",
                producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=idempotency_key,
                direct_owners=(prepared.attempt.invocation_ref,
                               prepared.attempt.attempt_ref),
                input_resources=(_resource_from_payload(
                    invocation["request_resource_ref"]),))
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
                        "external_request_id"),
                },
                producer_invocation_id=context.invocation_ref.entity_id)

        waiting, committed_ref = (
            self.mechanical_lifecycle.complete_context_compaction(
                before=prepared.loop, attempt=prepared.attempt,
                compaction_ref=compaction_ref,
                document_factory=document_factory,
                response_ref=response_ref, response_size=len(payload),
                stage_response=stage_response,
                idempotency_key=idempotency_key))
        return CompletedAgentContextCompaction(waiting, committed_ref)

    def record_agent_context_compaction_failure_v1(self, prepared, disposition, *, idempotency_key,
            failure_code=None, submission_state=None):
        if not isinstance(prepared, PreparedAgentContextCompaction):
            raise TypeError("optional compaction failure requires preparation")
        provider = self.provider_attempt_for_agent_llm_v1(prepared.attempt)
        events = ()
        if (disposition == "protocol_rejected"
                and submission_state == "response_observed"):
            provider, raw_event, raw_response_ref, _raw_payload = (
                self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                    prepared.attempt.attempt_ref,
                    read_response=lambda ref: self.kernel._read_firing_registered(
                        self._context(prepared.loop), ref)))
            events = ((
                "provider_attempt_completed/v1",
                str(provider.attempt_id), "provider_attempt", {
                    "provider_attempt_id": str(provider.attempt_id),
                    "response_version_id": str(
                        raw_response_ref.resource_version_id),
                    "response_observed_event_id": str(raw_event.event_id),
                    "finish_reason": raw_event.payload.get("finish_reason"),
                    "external_request_id": raw_event.payload.get(
                        "external_request_id"),
                },
            ),)
        return self.mechanical_lifecycle.record_context_compaction_failure(
            prepared=prepared, disposition=disposition,
            maximum_attempts=2, idempotency_key=idempotency_key,
            failure_code=failure_code, submission_state=submission_state,
            events=events)

def compact_agent_context_v1(
    self, execution: object, loop: AgentLoopSnapshot,
    catalog: AgentToolCatalog, *, trigger_reason: str, force: bool,
    idempotency_key: str,
) -> CompletedAgentContextCompaction:
    """Run the sole prepare/dispatch/complete compaction lifecycle."""
    if (not isinstance(loop, AgentLoopSnapshot)
            or loop.state != AgentLoopState.WAITING_FOR_LLM
            or trigger_reason not in {
                "context_pressure", "response_length", "turn_cap"}
            or not isinstance(force, bool)
            or force != (trigger_reason in {
                "response_length", "turn_cap"})
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise AgentLoopProtocolError(
            "context compaction bridge arguments are invalid")
    prepared_target = self._registry.prepare_agent_turn_context_v1(
        execution, loop, catalog,
        tool_output_byte_limit=(
            self._reduction_settings.tool_output_byte_limit)).target
    loop_context_window = prepared_target.context_window_tokens
    pressure_policy = (
        ContextPressurePolicy(
            loop_context_window, prepared_target.max_output_tokens,
            self._context_pressure_trigger_ratio)
        if (trigger_reason == "context_pressure"
            and loop_context_window is not None)
        else None)
    if trigger_reason == "context_pressure" and pressure_policy is None:
        raise AgentLoopProtocolError(
            "context pressure requires registered model capacity")
    retained_tokens = prepared_target.context_compaction_retained_tokens
    if retained_tokens is None:
        retained_tokens = (
            min(self._reduction_settings.retained_history_token_limit,
                max(1, loop_context_window // 4))
            if loop_context_window is not None else
            self._reduction_settings.retained_history_token_limit)
    reduction_settings = ContextReductionSettings(
        checkpoint_prompt=self._reduction_settings.checkpoint_prompt,
        tool_output_byte_limit=(
            self._reduction_settings.tool_output_byte_limit),
        retained_history_token_limit=retained_tokens)
    lifecycle_id: str | None = None
    failed_attempt: LLMCallAttempt | None = None
    current_loop = loop
    while True:
        prepared = self._registry.prepare_agent_context_compaction_v1(
            execution, current_loop, catalog,
            trigger_reason=trigger_reason, force=force,
            reduction_settings=reduction_settings,
            pressure_policy=pressure_policy,
            compaction_id=lifecycle_id,
            idempotency_key=(
                f"{idempotency_key}:prepare"
                if lifecycle_id is None else
                f"{idempotency_key}:reprepare:"
                f"{failed_attempt.attempt_ref.version_id}"))
        if isinstance(prepared, CompletedAgentContextCompaction):
            if lifecycle_id is not None:
                raise AgentLoopProtocolError(
                    "compaction retry did not reserve a fresh attempt")
            return prepared
        if not isinstance(prepared, PreparedAgentContextCompaction):
            raise AgentLoopProtocolError(
                "Registry returned no prepared current compaction")
        if (prepared.trigger_reason != trigger_reason
                or prepared.force != force
                or prepared.reduction_settings != reduction_settings
                or prepared.pressure_policy != pressure_policy
                or (lifecycle_id is not None
                    and prepared.compaction_id != lifecycle_id)):
            raise AgentLoopProtocolError(
                "prepared compaction differs from bridge request")
        if failed_attempt is not None:
            if (prepared.attempt.invocation_ref
                    != failed_attempt.invocation_ref
                    or prepared.attempt.attempt_ref
                    == failed_attempt.attempt_ref
                    or prepared.attempt.attempt_ordinal
                    <= failed_attempt.attempt_ordinal):
                raise AgentLoopProtocolError(
                    "compaction retry reused its failed prepared attempt")
        attempt = prepared.attempt
        current_loop = prepared.loop
        failure: LLMInputPortFailure | None = None
        try:
            response_bytes = self._input_port.request_once(attempt)
        except LLMInputPortFailure as caught:
            failure = caught
            response_bytes = None
        disposition = (
            failure.disposition if failure is not None else "success")
        failure_code = (
            failure.failure_code if failure is not None else None)
        submission_state = (
            failure.submission_state if failure is not None else None)
        if disposition != "success" and response_bytes is None:
            canonical_response = None
        elif not isinstance(response_bytes, bytes):
            raise AgentLoopProtocolError(
                "LLM input port returned no successful response bytes")
        else:
            try:
                canonical_response = canonicalize_llm_response_payload(
                    response_bytes)
                response = json.loads(canonical_response)
                if (canonical_response != response_bytes
                        or not isinstance(response.get("text"), str)
                        or not response["text"].strip()
                        or response.get("tool_calls") != []
                        or response.get("finish_reason") == "length"):
                    raise LLMResponseProtocolError(
                        "compaction requires one canonical text response")
                disposition = "success"
            except (LLMResponseProtocolError, json.JSONDecodeError):
                disposition = "protocol_rejected"
                failure_code = "framework_compaction_response_invalid"
                submission_state = "response_observed"
                canonical_response = None
        if disposition == "success":
            completed = (
                self._registry.complete_agent_context_compaction_v1(
                    prepared, canonical_response,
                    idempotency_key=f"{idempotency_key}:complete"))
            if not isinstance(completed, CompletedAgentContextCompaction):
                raise AgentLoopProtocolError(
                    "Registry returned no completed current compaction")
            return completed
        failure = (
            self._registry.record_agent_context_compaction_failure_v1(
                prepared, disposition,
                failure_code=failure_code,
                submission_state=submission_state,
                idempotency_key=(
                    f"{idempotency_key}:attempt:"
                    f"{attempt.attempt_ref.version_id}:{disposition}")))
        if (not isinstance(failure, tuple) or len(failure) != 2
                or not isinstance(failure[0], bool)
                or failure[1] != prepared.compaction_id):
            raise AgentLoopProtocolError(
                "Registry returned invalid compaction failure authority")
        retry_allowed, lifecycle_id = failure
        if retry_allowed:
            failed_attempt = attempt
            continue
        block_kind = (
            "submission_reconciliation"
            if disposition == "submission_unknown" else "llm_repair")
        block = self._registry.register_operation_execution_block(
            execution, block_kind=block_kind,
            operation_or_tool_identity=str(
                current_loop.operation_binding_ref.entity_id),
            error_code=failure_code or disposition, error_message=None,
            boundary="agent_context_compaction",
            consecutive_count=attempt.attempt_ordinal + 1,
            exact_error_ref=attempt.attempt_ref,
            retry_not_before_utc=None)
        raise _LLMExecutionBlock(block)
