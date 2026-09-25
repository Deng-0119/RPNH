"""AgentLoop resource wait mechanics.

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


class ResourceWaitRecordsMechanicsMixin:
    def consume_resource_grant(
            self, *, before: AgentLoopSnapshot, turn_ref: VersionRef,
            action_ref: VersionRef, idempotency_key: str,
            validate_grant: Callable[[], None],
            stage_grant: Callable[[Any], None] | None = None,
            **changes: Any,
    ) -> AgentLoopSnapshot:
        """Validate one exact grant and resume the same waiting Loop."""
        current = self.require_current_revision(
            before, allowed_states=(AgentLoopState.WAITING_RESOURCE,))
        if (turn_ref.entity_type != "agent_turn/v1"
                or action_ref.entity_type != "agent_action/v2"
                or not callable(validate_grant)):
            raise AgentLoopMechanicalLifecycleError(
                "resource grant lacks exact turn/action authority")
        action = self.hydrate_action(action_ref)
        if (action.loop_id != current.loop_id
                or self.turn_ref_for(
                    current.loop_id, action.turn_sequence) != turn_ref
                or action.state != AgentLoopState.WAITING_RESOURCE):
            raise AgentLoopMechanicalLifecycleError(
                "resource grant action differs from its waiting Loop")
        validate_grant()
        if {"loop_id", "loop_version_id", "revision", "state"}.intersection(
                changes):
            raise AgentLoopMechanicalLifecycleError(
                "resource-grant policy cannot replace mechanical identity")
        waiting = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            state=AgentLoopState.WAITING_FOR_LLM,
            revision=current.revision + 1, **changes)
        return self.commit_resource_grant_suffix(
            before=current, after=waiting,
            idempotency_key=idempotency_key, stage=stage_grant)

    def commit_resource_grant_suffix(
            self, *, before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
    ) -> AgentLoopSnapshot:
        if (before.state != AgentLoopState.WAITING_RESOURCE
                or after.state != AgentLoopState.WAITING_FOR_LLM):
            raise AgentLoopMechanicalLifecycleError(
                "resource grant must resume its waiting Loop")
        return self.commit_loop_successor(
            before, after, idempotency_key=idempotency_key, stage=stage)

    @staticmethod
    def acknowledged_delivery_ref(receipt: Any) -> VersionRef:
        """Return the acknowledged terminal delivery authority, never a boundary receipt."""
        ref = getattr(receipt, "delivery_ref", None)
        if (not isinstance(ref, VersionRef)
                or ref.entity_type != "resource_delivery/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "acknowledged delivery lacks resource_delivery/v1 authority")
        return ref


__all__ = ["ResourceWaitRecordsMechanicsMixin"]


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


class ResourceWaitExecutionMixin:
    @classmethod
    def _resource_action_metadata(
            cls, execution,
            authority: (AgentLoopResourceGrantAuthority
                        | AgentLoopResourceLifecycleAuthority),
    ) -> dict[str, object]:
        request_ref = authority.request_ref
        lifecycle_ref = authority.lifecycle_ref
        grant_ref = authority.grant_ref
        lease_ref = authority.lease_ref
        llm_turns_used = authority.llm_turns_used
        if (request_ref is None or lifecycle_ref is None
                or llm_turns_used is None):
            raise ResourceIntegrityFault(
                "resource action lacks its exact lifecycle authority")
        firing = execution.operation.firing
        if authority.transition_firing_ref != firing.transition_firing_ref:
            raise ResourceIntegrityFault(
                "resource action crossed its exact transition firing")
        resource_ref = authority.resource_ref
        access_mode = (
            "edit" if authority.access_mode == "upgrade"
            else authority.access_mode)
        return {
            "kind": "live_firing_resource_extension/v1",
            "transition_firing_ref": _ref_payload(
                authority.transition_firing_ref),
            "transition_id": firing.transition_id,
            "invocation_ref": _ref_payload(authority.invocation_ref),
            "operation_execution_lease_ref": _ref_payload(
                authority.operation_execution_lease_ref),
            "operation_binding_ref": _ref_payload(
                authority.operation_binding_ref),
            "agent_loop_ref": _ref_payload(authority.agent_loop_ref),
            "agent_turn_ref": _ref_payload(authority.agent_turn_ref),
            "agent_action_ref": _ref_payload(authority.agent_action_ref),
            "resource_use_occurrence_ref": _ref_payload(request_ref),
            "resource_access_lifecycle_ref": _ref_payload(lifecycle_ref),
            "resource_access_grant_ref": (
                None if grant_ref is None else _ref_payload(grant_ref)),
            "resource_access_lease_ref": (
                None if lease_ref is None else _ref_payload(lease_ref)),
            "logical_resource_id": str(resource_ref.resource_id),
            "lock_resource_ref": _ref_payload(resource_ref.as_version_ref()),
            "resource_ref": _resource_payload(resource_ref),
            "access_mode": access_mode,
            "access_checkpoint_ref": _ref_payload(
                authority.access_checkpoint_ref),
            "access_net_ref": _ref_payload(authority.access_net_ref),
            "access_claim_epoch": authority.access_claim_epoch,
            "resource_token_ref": _ref_payload(
                authority.resource_token_ref),
            "lease_pool_place": authority.lease_pool_place,
            "lease_identity_ref": _ref_payload(
                authority.lease_identity_ref),
            "petri_input_arc_mode": authority.petri_input_arc_mode,
            "petri_output_arc_mode": authority.petri_output_arc_mode,
            "petri_arc_kind": authority.petri_arc_kind,
            "return_arc_required": authority.return_arc_required,
            "llm_turns_used": llm_turns_used,
            "writer_fencing_epoch": authority.writer_fencing_epoch,
            "same_firing_continuation": True,
            "available_on_next_turn": cls._input_path(resource_ref),
        }

    def _settle_agent_resource_request(
            self, execution, loop, turn, prepared, *, turn_ref,
            idempotency_key,
    ):
        validation = prepared.validation
        call = prepared.tool_call
        if (not isinstance(validation, ValidatedAgentToolAction)
                or validation.tool_name != "request_resource"):
            raise ResourceIntegrityFault(
                "resource settlement requires one validated request action")
        context = self._context(loop)
        if context.agent_ref is None:
            raise ResourceIntegrityFault(
                "resource request requires one exact registered agent")
        resource_ref = ResourceVersionRef(
            TypedId.parse(
                validation.arguments["resource_id"], expected="resource"),
            TypedId.parse(
                validation.arguments["resource_version_id"],
                expected="resource_version"),
        )
        action_ref = self.mechanical_lifecycle.action_ref(
            validation.action_id, idempotency_key)
        successor_loop_ref = self.mechanical_lifecycle.successor_loop_ref(
            loop, idempotency_key)
        request = AgentLoopResourceRequest(
            firing=execution.operation.firing,
            invocation_ref=context.invocation_ref,
            operation_execution_lease_ref=(
                execution.operation_execution_lease_ref),
            operation_binding_ref=context.operation_binding_ref,
            agent_loop_ref=successor_loop_ref,
            agent_turn_ref=turn_ref,
            agent_action_ref=action_ref,
            requester_agent_ref=context.agent_ref,
            resource_ref=resource_ref,
            access_mode=validation.arguments["access_mode"],
            llm_turns_used=loop.llm_turns_used,
        )
        tx, current = (
            self.mechanical_lifecycle.begin_action_policy_transaction(
                loop, idempotency_key=idempotency_key,
                transaction_scope={
                    "task_round_id": context.task_round_ref.entity_id,
                    "net_instance_id": context.net_instance_ref.entity_id,
                }))
        from . import optional_execution as _optional_execution
        authority = _optional_execution.prepare_agent_resource_request(
            self.core, tx, request, idempotency_key=idempotency_key)
        waiting = isinstance(
            authority, AgentLoopResourceLifecycleAuthority)
        if waiting and authority.state != "waiting_resource":
            raise ResourceIntegrityFault(
                "resource request returned a non-waiting lifecycle")
        if (not waiting
                and not isinstance(authority, AgentLoopResourceGrantAuthority)):
            raise ResourceIntegrityFault(
                "resource request returned no typed grant/lifecycle")
        metadata = self._resource_action_metadata(execution, authority)
        state = (
            AgentLoopState.WAITING_RESOURCE
            if waiting else AgentLoopState.ACTION_APPLIED)
        record = AgentActionRecord(
            validation.action_id, loop.loop_id, turn.sequence,
            call.tool_call_ordinal, call.tool_call_id,
            call.action_identity_kind, call.action_identity_key,
            call.tool_name, call.raw_arguments, validation.arguments,
            loop.revision, state, (), None, metadata)
        from .action_records import AgentActionSettlementPlan
        plan = AgentActionSettlementPlan(
            records=(record,), error_documents={},
            written_resource_refs=loop.written_resource_refs,
            final_state=(
                AgentLoopState.WAITING_RESOURCE
                if waiting else AgentLoopState.WAITING_FOR_LLM),
            action_versions={
                validation.action_id: action_ref.version_id},
        )
        return self.mechanical_lifecycle.finish_action_policy_transaction(
            tx=tx, current=current, turn_ref=turn_ref,
            turn_id=turn.turn_id, plan=plan)

    def consume_agent_loop_resource_grant_v1(self, execution, registry_authority, *, waiting_loop, waiting_turn,
            waiting_action, catalog, idempotency_key):
        execution = self._execution(execution, waiting_loop)
        current = self._current(waiting_loop)
        context = self._context(current)
        self._catalog(context, catalog)
        if (not isinstance(
                    registry_authority, AgentLoopResourceGrantAuthority)
                or current.state != AgentLoopState.WAITING_RESOURCE
                or waiting_turn
                != self.hydrate_current_agent_turn_v1(current)
                or not isinstance(waiting_action, AgentActionRecord)
                or waiting_action.state != AgentLoopState.WAITING_RESOURCE
                or waiting_action.tool_name != "request_resource"
                or not isinstance(waiting_action.arguments, Mapping)
                or not isinstance(waiting_action.result_metadata, Mapping)):
            raise ResourceIntegrityFault(
                "resource grant lacks its exact waiting loop/turn/action")
        turn_ref = self.mechanical_lifecycle.turn_ref_for(
            current.loop_id, waiting_turn.sequence)
        action_ref = registry_authority.agent_action_ref
        if self.mechanical_lifecycle.hydrate_action(action_ref) != waiting_action:
            raise ResourceIntegrityFault(
                "resource grant crossed its exact waiting action version")
        requested = ResourceVersionRef(
            TypedId.parse(
                str(waiting_action.arguments["resource_id"]),
                expected="resource"),
            TypedId.parse(
                str(waiting_action.arguments["resource_version_id"]),
                expected="resource_version"),
        )
        metadata = waiting_action.result_metadata

        def validate_grant() -> None:
            if (registry_authority.transition_firing_ref
                    != execution.operation.firing.transition_firing_ref
                    or registry_authority.invocation_ref
                    != context.invocation_ref
                    or registry_authority.operation_execution_lease_ref
                    != execution.operation_execution_lease_ref
                    or registry_authority.operation_binding_ref
                    != context.operation_binding_ref
                    or registry_authority.agent_loop_ref != self.mechanical_lifecycle.loop_ref(current)
                    or registry_authority.agent_turn_ref != turn_ref
                    or registry_authority.resource_ref != requested
                    or registry_authority.access_mode
                    != waiting_action.arguments["access_mode"]
                    or registry_authority.writer_fencing_epoch
                    != self.core.writer_epoch
                    or registry_authority.llm_turns_used
                    != current.llm_turns_used
                    or registry_authority.request_ref is None
                    or metadata.get("resource_use_occurrence_ref")
                    != _ref_payload(registry_authority.request_ref)
                    or metadata.get("resource_ref")
                    != _resource_payload(requested)
                    or metadata.get("resource_access_grant_ref") is not None
                    or metadata.get("resource_access_lease_ref") is not None
                    or metadata.get("agent_action_ref")
                    != _ref_payload(action_ref)
                    or metadata.get("agent_loop_ref")
                    != _ref_payload(self.mechanical_lifecycle.loop_ref(current))):
                raise ResourceIntegrityFault(
                    "resource grant differs from its durable waiting authority")

        def stage_grant(tx) -> None:
            stage_resumed_agent_resource_lifecycle(
                self.core, tx, registry_authority,
                idempotency_key=idempotency_key)

        return self.mechanical_lifecycle.consume_resource_grant(
            before=current, turn_ref=turn_ref, action_ref=action_ref,
            idempotency_key=idempotency_key,
            validate_grant=validate_grant,
            stage_grant=stage_grant)
