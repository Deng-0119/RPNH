"""AgentLoop turn preparation and provider execution."""

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
from .action_records import AgentActionSettlementPlan
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


class TurnExecutionMixin:

    def _publish_request(self, loop, prepared, catalog, key):
        context = self._context(loop)
        envelope = json.loads(self._envelope(prepared, catalog))
        recipe = dict(envelope)
        recipe.pop("protocol")
        recipe["schema_version"] = "logical_provider_request_recipe/v1"
        recipe["model"] = recipe.pop("model_condition")
        recipe["max_tokens"] = recipe.pop("max_output_tokens")
        payload = canonical_json(recipe)
        self.core.catalog.validate_schema_ref("registry_v1/logical_provider_request_recipe/v1", recipe)
        ref = ResourceVersionRef(
            _stable_id("resource", "optional-agent-request", key),
            _stable_id("resource_version", "optional-agent-request", key))
        inputs = (prepared.prompt_ref, loop.tool_catalog_ref,
                  *(item.resource_ref for item in prepared.initialization.located_inputs))
        def stage_request(tx):
            _append_direct_resource_version_publication(tx, ref=ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(self.core, ref=ref,
                    origin_kind="provider_request", primary=context.operation_binding_ref, secondary=self.mechanical_lifecycle.loop_ref(loop),
                    task_ref=context.task_ref, round_ref=context.task_round_ref, net_ref=context.net_instance_ref,
                    producer_ref=context.invocation_ref, lifetime_ref=context.operation_execution_lease_ref,
                    operation_binding_ref=context.operation_binding_ref, agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size,
                    media_type="application/json", content_schema_ref="registry_v1/logical_provider_request_recipe/v1",
                    content_schema_authority_ref=_ref_payload(
                        _registry_type_catalog_ref(self.core)),
                    summary="Optional declared agent logical request", descriptors={"content_role": "optional_agent_request"},
                    extensions={}, input_resource_refs=tuple(sorted(inputs, key=lambda item: canonical_json(_resource_payload(item)))),
                    intended_boundary="llm_prompt"), media_type="application/json",
                producer_ref=context.invocation_ref, producer_invocation_id=context.invocation_ref.entity_id,
                relation_key=key,
                direct_owners=(context.operation_binding_ref, self.mechanical_lifecycle.loop_ref(loop)),
                input_resources=inputs)

        def verify_replay(candidate):
            if (candidate != ref
                    or self.kernel._read_firing_registered(
                        context, candidate) != payload):
                raise ResourceIntegrityFault(
                    "optional request replay differs from exact bytes")

        self.mechanical_lifecycle.commit_request_authority(
            idempotency_key=key, stage_request=stage_request,
            loop=loop, request_ref=ref, verify_replay=verify_replay)
        return ref

    def _deliver(self, context, ref, boundary, key):
        prepared = self.kernel.prepare_delivery(context, PrepareResourceDelivery(ref, context.operation_binding_ref,
            new_id("resource_delivery"), key + ":prepare", boundary, "Optional declared capability exact bytes"))
        release = self.kernel.authorize_release(context, AuthorizeResourceRelease(prepared.delivery_ref, boundary,
            new_id("release_nonce"), key + ":release"))
        payload = self.kernel._consume_authorized_release(context, release)
        receipt = self.kernel._record_boundary_receipt(context, release, outcome="acknowledged",
            positive_byte_count=len(payload), consumer_evidence="Optional capability consumed exact registered bytes",
            idempotency_key=key + ":receipt")
        terminal = self.kernel.acknowledge_delivery(context, AcknowledgeResourceDelivery(release.delivery_ref,
            receipt, "acknowledged", key + ":ack"))
        return terminal, receipt, payload

    def prepare_agent_llm_turn_v1(self, execution, loop, catalog, initialization, *, idempotency_key, prepared_context=None):
        execution = self._execution(execution, loop)
        if loop.state != AgentLoopState.WAITING_FOR_LLM or loop.llm_turns_used >= loop.llm_turn_budget:
            raise OptionalAgentCapabilityUnavailable("optional turn budget closed; no declared cap handoff")
        prepared = prepared_context or self.prepare_agent_turn_context_v1(execution, loop, catalog)
        if prepared.loop != loop or prepared.initialization != initialization:
            raise ResourceIntegrityFault("optional turn differs from its prepared immutable context")
        context = self._context(loop)
        backend_ref, _, backend_payload = self._static(context, "optional_agent_backend")
        transport_ref, _, transport_payload = self._static(context, "optional_agent_transport")
        backend, transport = json.loads(backend_payload), json.loads(transport_payload)
        if backend["model"] != loop.model_condition:
            raise ResourceIntegrityFault("optional provider provenance changed the exact model")
        overlay = self.mechanical_lifecycle.latest_context_overlay(loop)
        dispatch_key = idempotency_key
        if (overlay is not None
                and overlay.trigger_reason == "response_length"):
            dispatch_key += ":after-compaction:" + str(
                overlay.compaction_ref.version_id)
        request_ref = self._publish_request(
            loop, prepared, catalog, dispatch_key + ":recipe")
        delivery, _, _ = self._deliver(
            context, request_ref, "llm_prompt",
            dispatch_key + ":recipe-delivery")
        prior_calls = []
        for event in self.mechanical_lifecycle.turn_events(loop):
            linked = self._provider_for_ref(_version_from_payload(event.payload["llm_invocation_attempt_ref"]))
            prior_calls.append(linked.call.ref)
        call = self.ledger.create_call_v2(context=context, loop_ref=_ref_payload(self.mechanical_lifecycle.loop_ref(loop)),
            turn_sequence=loop.next_turn_sequence, request_resource_ref=request_ref,
            terminal_delivery_ref=delivery.delivery_ref, semantic_prompt_resource_ref=prepared.prompt_ref,
            llm_execution_target_ref=backend_ref, backend=backend["backend"], model=backend["model"],
            transport_contract_ref=transport_ref, interaction_protocol_ref=transport["interaction_protocol_ref"],
            response_adapter_ref=transport["response_adapter_ref"], tool_catalog_ref=loop.tool_catalog_ref,
            prior_turn_refs=tuple(prior_calls), timeout_seconds=backend["timeout_seconds"],
            max_response_bytes=prepared.target.max_response_bytes, call_id=new_id("llm_call"),
            idempotency_key=dispatch_key + ":call")
        try:
            provider = self.ledger.reserve_v2(context=context, call=call, prior_attempt=None,
                                              idempotency_key=dispatch_key + ":provider")
        except (ResourceIntegrityFault, ValueError) as exc:
            raise OptionalAgentCapabilityUnavailable(
                "Core provider reservation cannot consume the registered optional execution provenance: "
                + str(exc)) from exc
        agent = context.agent_ref
        if agent is None:
            raise ResourceIntegrityFault("optional agent invocation lacks its exact registered agent")
        invocation_ref = VersionRef("llm_invocation_spec/v1", new_id("llm_invocation"), new_id("llm_invocation_version"))
        invocation = {"llm_invocation_id": str(invocation_ref.entity_id), "llm_invocation_version_id": str(invocation_ref.version_id),
            "llm_invocation_ref": _ref_payload(invocation_ref), "invocation_ref": _ref_payload(context.invocation_ref),
            "operation_binding_ref": _ref_payload(context.operation_binding_ref), "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(loop)),
            "subject_agent_ref": _ref_payload(agent), "execution_agent_ref": _ref_payload(agent),
            "invocation_kind": "normal_turn", "turn_sequence": loop.next_turn_sequence,
            "request_resource_ref": _resource_payload(request_ref), "semantic_prompt_resource_ref": _resource_payload(prepared.prompt_ref),
            "llm_input_target_ref": _resource_payload(prepared.target_ref), "model_condition": loop.model_condition,
            "tool_catalog_ref": _resource_payload(loop.tool_catalog_ref),
            "prior_turn_refs": [_ref_payload(ref) for ref in prepared.prior_turn_refs],
            "max_response_bytes": prepared.target.max_response_bytes}
        if (overlay is not None
                and overlay.trigger_reason == "response_length"):
            compaction = self.mechanical_lifecycle.exact_object_document(
                overlay.compaction_ref,
                expected_type="agent_context_compaction/v3")
            interrupted_ref = compaction.get(
                "interrupted_llm_invocation_ref")
            interrupted_response = compaction.get(
                "interrupted_response_resource_ref")
            if (isinstance(interrupted_ref, Mapping)
                    and isinstance(interrupted_response, Mapping)):
                interrupted = self.mechanical_lifecycle.exact_object_document(
                    _version_from_payload(interrupted_ref),
                    expected_type="llm_invocation_spec/v1")
                if interrupted.get("turn_sequence") == loop.next_turn_sequence:
                    invocation.update(
                        replay_parent_invocation_ref=dict(interrupted_ref),
                        replay_after_compaction_ref=_ref_payload(
                            overlay.compaction_ref),
                        replay_interrupted_response_ref=dict(
                            interrupted_response))
        attempt = self.mechanical_lifecycle.reserve_invocation_attempt(
            loop=loop,
            invocation_ref=invocation_ref,
            invocation_document=invocation,
            producer_invocation_id=context.invocation_ref.entity_id,
            reservation_class=provider.reservation_class,
            finalization_scope=provider.finalization_scope,
            model_condition=loop.model_condition,
            canonical_request_bytes=self._envelope(prepared, catalog),
            max_response_bytes=prepared.target.max_response_bytes,
            maximum_attempts=1,
            provider_attempt_ref=provider.ref,
            idempotency_key=dispatch_key + ":neutral-attempt")
        return PreparedAgentLLMTurn(attempt)

    def _provider_for_ref(self, attempt_ref):
        return self.mechanical_lifecycle.provider_attempt_for_invocation_attempt(
            attempt_ref)

    def provider_attempt_for_agent_llm_v1(self, attempt):
        exact = self.kernel._exact_object(attempt.attempt_ref, expected_type="llm_invocation_attempt/v1").metadata
        if exact["llm_invocation_ref"] != _ref_payload(attempt.invocation_ref):
            raise ResourceIntegrityFault("optional transport crossed its neutral invocation")
        provider = self._provider_for_ref(attempt.attempt_ref)
        self.ledger._revalidate_call_v2_context(provider.call, boundary="optional-input-port-hydration")
        return provider


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


def request_agent_turn_v1(
    self, execution: object, loop: AgentLoopSnapshot,
    catalog: AgentToolCatalog, *, idempotency_key: str,
) -> CompletedAgentLLMInvocation:
    current_loop = loop
    turn_context = self._registry.prepare_agent_turn_context_v1(
        execution, current_loop, catalog)
    if turn_context.forced_compaction_reason == "response_length":
        compacted = self.compact_agent_context_v1(
            execution, current_loop, catalog,
            trigger_reason="response_length", force=True,
            idempotency_key=f"{idempotency_key}:response-length")
        current_loop = compacted.waiting_loop
        turn_context = self._registry.prepare_agent_turn_context_v1(
            execution, current_loop, catalog)
        if turn_context.forced_compaction_reason is not None:
            raise AgentLoopProtocolError(
                "response-length compaction did not close its trigger")
    context_window_tokens = turn_context.target.context_window_tokens
    if context_window_tokens is not None:
        pressure_policy = ContextPressurePolicy(
            context_window_tokens,
            turn_context.target.max_output_tokens)
        pressure_required = (
            self._registry.agent_context_pressure_requires_compaction_v1(
                execution, current_loop, catalog, turn_context,
                pressure_policy=pressure_policy))
        if not isinstance(pressure_required, bool):
            raise AgentLoopProtocolError(
                "Registry returned no context-pressure decision")
        if pressure_required:
            compacted = self.compact_agent_context_v1(
                execution, current_loop, catalog,
                trigger_reason="context_pressure", force=False,
                idempotency_key=f"{idempotency_key}:context-pressure")
            current_loop = compacted.waiting_loop
            turn_context = self._registry.prepare_agent_turn_context_v1(
                execution, current_loop, catalog)
    while True:
        prepared = self._registry.prepare_agent_llm_turn_v1(
            execution, current_loop, catalog,
            turn_context.initialization,
            idempotency_key=idempotency_key,
            prepared_context=turn_context)
        if not isinstance(prepared, PreparedAgentLLMTurn):
            raise AgentLoopProtocolError(
                "Registry returned no generic LLM attempt")
        attempt = prepared.attempt
        reservation_return = (
            time.monotonic_ns() - self._timing_origin_ns
            if self._timing_origin_ns is not None else None)
        with self._registry.prepared_agent_turn_context_scope_v1(
                turn_context):
            try:
                response_bytes = self._input_port.request_once(attempt)
            except LLMInputPortFailure as failure:
                disposition = failure.disposition
                failure_code = failure.failure_code
                submission_state = failure.submission_state
                response_bytes = None
            else:
                disposition = "success"
                failure_code = None
                submission_state = None
        if disposition != "success" and response_bytes is None:
            canonical_response = None
        elif not isinstance(response_bytes, bytes):
            raise AgentLoopProtocolError(
                "LLM input port returned no successful response bytes")
        else:
            try:
                canonical_response = canonicalize_llm_response_payload(
                    response_bytes)
                if canonical_response != response_bytes:
                    raise LLMResponseProtocolError(
                        "LLM response bytes are not canonical serialization")
                disposition = "success"
            except LLMResponseProtocolError:
                disposition = "protocol_rejected"
                failure_code = "framework_response_protocol_invalid"
                submission_state = "response_observed"
                canonical_response = None
        if disposition != "success":
            next_attempt_allowed = (
                self._registry.record_llm_invocation_failure_v1(
                    current_loop, attempt, disposition,
                    failure_code=failure_code,
                    submission_state=submission_state,
                    idempotency_key=(
                        f"{idempotency_key}:attempt:"
                        f"{attempt.attempt_ref.version_id}:"
                        f"{disposition}")))
            if next_attempt_allowed:
                continue
            block_kind = (
                "submission_reconciliation"
                if disposition == "submission_unknown" else "llm_repair")
            block = self._registry.register_operation_execution_block(
                execution,
                block_kind=block_kind,
                operation_or_tool_identity=str(
                    current_loop.operation_binding_ref.entity_id),
                error_code=failure_code or disposition,
                error_message=None,
                boundary="llm_input",
                consecutive_count=attempt.attempt_ordinal + 1,
                exact_error_ref=attempt.attempt_ref,
                retry_not_before_utc=None)
            raise _LLMExecutionBlock(block)
        timing = AgentTurnTimingEvidence(
            agent_loop_id=current_loop.loop_id,
            turn_sequence=current_loop.next_turn_sequence,
            agent_turn_id=None,
            llm_invocation_attempt_ref=attempt.attempt_ref,
            timing_origin_ns=self._timing_origin_ns,
            llm_attempt_reservation_return=reservation_return)
        return CompletedAgentLLMInvocation(
            attempt=attempt, response_bytes=canonical_response,
            waiting_loop=current_loop, timing_evidence=timing)
