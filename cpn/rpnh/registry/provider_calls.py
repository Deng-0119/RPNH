"""Typed LLM-call and CAP-B provider-attempt protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .event_store import RegistryConflict
from .identities import TypedId
from .invocations import InvocationContext
from .models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from .operations import OperationExecutionBlockAuthority
from .resources import (
    ProviderAttemptAuthority,
    ResourceVersionRef,
)

if TYPE_CHECKING:
    from ._registry import _RegistryCore


def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _resource_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


def _closed_external_ref(
        value: Mapping[str, str], *, entity_type: str,
        label: str) -> dict[str, str]:
    """Validate refs whose TypedId kinds await shared catalog integration."""
    if (not isinstance(value, Mapping)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or value.get("entity_type") != entity_type):
        raise ProviderAdmissionError(f"{label} ref is not closed")
    logical_id = value.get("logical_id")
    version_id = value.get("version_id")
    expected_logical = (
        "llm_call" if entity_type == "llm_call_spec/v2"
        else entity_type.split("/", 1)[0])
    expected_version = f"{expected_logical}_version"
    for candidate, prefix in ((logical_id, expected_logical),
                              (version_id, expected_version)):
        if (not isinstance(candidate, str)
                or not candidate.startswith(f"{prefix}:")
                or len(candidate) != len(prefix) + 33
                or any(character not in "0123456789abcdef"
                       for character in candidate.split(":", 1)[1])):
            raise ProviderAdmissionError(f"{label} ref id is invalid")
    return {"entity_type": entity_type, "logical_id": logical_id,
            "version_id": version_id}


def _closed_resource_ref(value: object, *, label: str) -> dict[str, str]:
    if (not isinstance(value, Mapping)
            or set(value) != {"resource_id", "resource_version_id"}):
        raise ProviderAdmissionError(f"{label} ref is not closed")
    try:
        resource_id = TypedId.parse(
            str(value["resource_id"]), expected="resource")
        version_id = TypedId.parse(
            str(value["resource_version_id"]), expected="resource_version")
    except (TypeError, ValueError) as exc:
        raise ProviderAdmissionError(f"{label} ref id is invalid") from exc
    return {
        "resource_id": str(resource_id),
        "resource_version_id": str(version_id),
    }


class ProviderAdmissionError(RegistryConflict):
    pass


class ProviderAttemptUnknown(ProviderAdmissionError):
    """U-A blocks the call until external transport evidence reconciles it."""


@dataclass(frozen=True, slots=True)
class ExternallyValidatedProviderRequestAuthority:
    """Workflow validation acknowledgement bound to exact Harness refs."""

    request_resource_ref: ResourceVersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef

    def __post_init__(self) -> None:
        if not isinstance(self.request_resource_ref, ResourceVersionRef):
            raise TypeError(
                "provider request authority requires one exact resource ref")
        if (not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "invocation/v1"
                or self.invocation_ref.entity_id.kind != "invocation"
                or self.invocation_ref.version_id.kind
                != "invocation_version"):
            raise TypeError(
                "provider request authority requires one exact invocation ref")
        if (not isinstance(self.operation_binding_ref, VersionRef)
                or self.operation_binding_ref.entity_type
                != "operation_binding/v1"
                or self.operation_binding_ref.entity_id.kind
                != "operation_binding"
                or self.operation_binding_ref.version_id.kind
                != "operation_binding_version"):
            raise TypeError(
                "provider request authority requires one exact binding ref")

    def matches(
            self, *, request_resource_ref: ResourceVersionRef,
            context: InvocationContext,
    ) -> bool:
        return (
            self.request_resource_ref == request_resource_ref
            and self.invocation_ref == context.invocation_ref
            and self.operation_binding_ref == context.operation_binding_ref)


@dataclass(frozen=True, slots=True)
class LLMCall:
    call_id: TypedId
    version_id: TypedId
    invocation_ref: VersionRef
    activation_id: TypedId | None
    context_digest: str
    operation_binding_ref: VersionRef
    request_resource_ref: ResourceVersionRef
    terminal_delivery_ref: VersionRef

    @property
    def ref(self) -> VersionRef:
        return VersionRef("llm_call_spec/v1", self.call_id, self.version_id)

    @property
    def invocation_id(self) -> TypedId:
        return self.invocation_ref.entity_id


@dataclass(frozen=True, slots=True)
class LLMCallV2:
    """Parallel agent-loop call; its response is evidence, never output."""

    call_id: TypedId
    version_id: TypedId
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    loop_ref: Mapping[str, str]
    turn_sequence: int
    request_resource_ref: ResourceVersionRef
    terminal_delivery_ref: VersionRef
    semantic_prompt_resource_ref: ResourceVersionRef
    llm_execution_target_ref: ResourceVersionRef
    backend: str
    model: str
    transport_contract_ref: ResourceVersionRef
    interaction_protocol_ref: str
    response_adapter_ref: str
    tool_catalog_ref: ResourceVersionRef
    prior_turn_refs: tuple[VersionRef, ...]
    timeout_seconds: int
    max_response_bytes: int

    @property
    def ref(self) -> VersionRef:
        return VersionRef("llm_call_spec/v2", self.call_id, self.version_id)

    @property
    def invocation_id(self) -> TypedId:
        return self.invocation_ref.entity_id


@dataclass(frozen=True, slots=True)
class LLMCallV3:
    """Source-neutral registered-HOST call; no AgentLoop state is implied."""

    call_id: TypedId
    version_id: TypedId
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    request_resource_ref: ResourceVersionRef
    terminal_delivery_ref: VersionRef
    semantic_prompt_resource_ref: ResourceVersionRef
    llm_input_target_ref: ResourceVersionRef
    llm_execution_target_ref: ResourceVersionRef
    backend: str
    model: str
    transport_contract_ref: ResourceVersionRef
    interaction_protocol_ref: str
    response_adapter_ref: str
    tool_catalog_ref: ResourceVersionRef
    timeout_seconds: int
    max_response_bytes: int
    invocation_kind: str = "registered_host"

    @property
    def ref(self) -> VersionRef:
        return VersionRef("llm_call_spec/v3", self.call_id, self.version_id)

    @property
    def invocation_id(self) -> TypedId:
        return self.invocation_ref.entity_id


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    attempt_id: TypedId
    version_id: TypedId
    call: LLMCall
    reservation_class: str
    finalization_scope: str | None

    @property
    def ref(self) -> VersionRef:
        return VersionRef("provider_attempt_spec/v1", self.attempt_id, self.version_id)


@dataclass(frozen=True, slots=True)
class ProviderAttemptV2:
    """Typed parallel reservation around one v2 call."""

    attempt_id: TypedId
    version_id: TypedId
    call: LLMCallV2
    reservation_class: str
    finalization_scope: str | None

    @property
    def ref(self) -> VersionRef:
        return VersionRef(
            "provider_attempt_spec/v1", self.attempt_id, self.version_id)


@dataclass(frozen=True, slots=True)
class ProviderAttemptV3:
    """Typed provider reservation around one registered-HOST v3 call."""

    attempt_id: TypedId
    version_id: TypedId
    call: LLMCallV3
    reservation_class: str
    finalization_scope: str | None

    @property
    def ref(self) -> VersionRef:
        return VersionRef(
            "provider_attempt_spec/v1", self.attempt_id, self.version_id)


@dataclass(frozen=True, slots=True)
class RegisteredHostTransportResponse:
    """Raw provider observation for a loop-free registered-HOST call."""

    response_resource_ref: ResourceVersionRef
    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef
    size: int
    backend: str
    model: str
    interaction_protocol_ref: str
    response_adapter_ref: str

    def __post_init__(self) -> None:
        if (not isinstance(self.response_resource_ref, ResourceVersionRef)
                or not isinstance(self.provider_attempt_ref, VersionRef)
                or self.provider_attempt_ref.entity_type
                != "provider_attempt_spec/v1"
                or not isinstance(self.llm_call_ref, VersionRef)
                or self.llm_call_ref.entity_type != "llm_call_spec/v3"
                or isinstance(self.size, bool)
                or not isinstance(self.size, int) or self.size < 0
                or any(not isinstance(value, str) or not value
                       or value != value.strip() for value in (
                           self.backend, self.model,
                           self.interaction_protocol_ref,
                           self.response_adapter_ref))):
            raise TypeError(
                "registered HOST transport response authority is invalid")


@dataclass(frozen=True, slots=True)
class ProviderCompletedCandidate:
    """Exact completed-response candidate retained by the provider consumer."""

    attempt: ProviderAttemptAuthority
    response_resource_ref: ResourceVersionRef

    def __post_init__(self) -> None:
        if (not isinstance(self.attempt, ProviderAttemptAuthority)
                or not isinstance(
                    self.response_resource_ref, ResourceVersionRef)):
            raise TypeError(
                "completed provider candidate requires exact attempt/response refs")


@dataclass(frozen=True, slots=True)
class ModelCallCapProjection:
    """Registry projection of normal and post-cap returned model calls."""

    actual_returned_calls: int
    limit: int
    dispatch_closed: bool
    extra_returned_calls: int = 0

    def __post_init__(self) -> None:
        if (isinstance(self.actual_returned_calls, bool)
                or not isinstance(self.actual_returned_calls, int)
                or self.actual_returned_calls < 0
                or isinstance(self.limit, bool)
                or not isinstance(self.limit, int)
                or self.limit < 1
                or isinstance(self.extra_returned_calls, bool)
                or not isinstance(self.extra_returned_calls, int)
                or self.extra_returned_calls < 0
                or self.extra_returned_calls
                != max(0, self.actual_returned_calls - self.limit)
                or self.dispatch_closed
                != (self.actual_returned_calls >= self.limit)):
            raise ValueError("actual-model-call cap projection is inconsistent")


@dataclass(frozen=True, slots=True)
class RegistryMonitorProjection:
    """Read-only Registry projection for execution and experiment reporting."""

    model_condition: str | None
    provider_routes: tuple[tuple[str, str], ...]
    execution_status: str
    experiment_result: str | None
    terminal_result_ref: ResourceVersionRef | None
    call_cap: ModelCallCapProjection
    blocking_status: str | None = None
    resource_cycle_evidence_ref: VersionRef | None = None
    execution_blocks: tuple[OperationExecutionBlockAuthority, ...] = ()

    def __post_init__(self) -> None:
        if (self.experiment_result not in {None, "success", "failure"}
                or self.blocking_status not in {
                    None, "BLOCKED_RESOURCE_CYCLE",
                    "BLOCKED_WAITING_OWNER"}
                or (self.blocking_status == "BLOCKED_RESOURCE_CYCLE")
                != (self.resource_cycle_evidence_ref is not None)
                or self.execution_status not in {
                    "RUNNING", "COMPLETED", "FAILED", "CANCELLED",
                    "UNCERTAIN", "UNSCORED_PROVIDER_INTERRUPTED",
                    "STOPPED_BY_OWNER", "BLOCKED",
                }
                or self.execution_status
                == "UNSCORED_PROVIDER_INTERRUPTED"
                and self.experiment_result is not None
                or (self.experiment_result is not None)
                != (self.terminal_result_ref is not None)
                or self.provider_routes != tuple(sorted(
                    set(self.provider_routes)))
                or any(not isinstance(
                    block, OperationExecutionBlockAuthority)
                    for block in self.execution_blocks)):
            raise ValueError("Registry monitor projection is inconsistent")


class ProviderAttemptLedger:
    """State-free provider-call command facade bound to the shared core."""

    def __init__(self, service: "_RegistryCore") -> None:
        self.service = service

    def recovery_manifest_ref(self) -> VersionRef:
        try:
            return self.service.recovery_manifest_ref()
        except Exception as exc:
            raise ProviderAdmissionError(
                "provider protocol requires the exact registered budget witness") from exc

    def _historical_create_call(
            self, *, context: InvocationContext,
            request_authority: ExternallyValidatedProviderRequestAuthority,
            tool_turn_sequence: int, request_resource_ref: ResourceVersionRef,
            terminal_delivery_ref: VersionRef, request_payload_digest: str,
            tool_catalog_digest: str, response_contract_digest: str,
            model_requirement: str, timeout_seconds: int, retry_limit: int,
            revision_reason: str = "initial",
            previous_call_version_id: TypedId | None = None,
            caused_by_response_ref: VersionRef | None = None,
            call_id: TypedId | None = None,
            idempotency_key: str) -> LLMCall:
        from ._provider_calls import legacy
        return legacy._historical_create_call(
            self, context=context, request_authority=request_authority,
            tool_turn_sequence=tool_turn_sequence,
            request_resource_ref=request_resource_ref,
            terminal_delivery_ref=terminal_delivery_ref,
            request_payload_digest=request_payload_digest,
            tool_catalog_digest=tool_catalog_digest,
            response_contract_digest=response_contract_digest,
            model_requirement=model_requirement,
            timeout_seconds=timeout_seconds, retry_limit=retry_limit,
            revision_reason=revision_reason,
            previous_call_version_id=previous_call_version_id,
            caused_by_response_ref=caused_by_response_ref,
            call_id=call_id, idempotency_key=idempotency_key)

    def create_call_v2(
            self, *, context: InvocationContext,
            loop_ref: Mapping[str, str], turn_sequence: int,
            request_resource_ref: ResourceVersionRef,
            terminal_delivery_ref: VersionRef,
            semantic_prompt_resource_ref: ResourceVersionRef,
            llm_execution_target_ref: ResourceVersionRef, backend: str, model: str,
            transport_contract_ref: ResourceVersionRef,
            interaction_protocol_ref: str,
            response_adapter_ref: str,
            tool_catalog_ref: ResourceVersionRef,
            prior_turn_refs: tuple[VersionRef, ...],
            timeout_seconds: int, max_response_bytes: int,
            call_id: TypedId | None = None,
            idempotency_key: str) -> LLMCallV2:
        from ._provider_calls import calls
        return calls.create_call_v2(
            self, context=context, loop_ref=loop_ref,
            turn_sequence=turn_sequence,
            request_resource_ref=request_resource_ref,
            terminal_delivery_ref=terminal_delivery_ref,
            semantic_prompt_resource_ref=semantic_prompt_resource_ref,
            llm_execution_target_ref=llm_execution_target_ref,
            backend=backend, model=model,
            transport_contract_ref=transport_contract_ref,
            interaction_protocol_ref=interaction_protocol_ref,
            response_adapter_ref=response_adapter_ref,
            tool_catalog_ref=tool_catalog_ref,
            prior_turn_refs=prior_turn_refs,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            call_id=call_id, idempotency_key=idempotency_key)

    def create_call_v3(
            self, *, context: InvocationContext,
            request_resource_ref: ResourceVersionRef,
            terminal_delivery_ref: VersionRef,
            semantic_prompt_resource_ref: ResourceVersionRef,
            llm_input_target_ref: ResourceVersionRef,
            llm_execution_target_ref: ResourceVersionRef, backend: str,
            model: str, transport_contract_ref: ResourceVersionRef,
            interaction_protocol_ref: str, response_adapter_ref: str,
            tool_catalog_ref: ResourceVersionRef, timeout_seconds: int,
            max_response_bytes: int, call_id: TypedId | None = None,
            idempotency_key: str = "") -> LLMCallV3:
        from ._provider_calls import calls
        return calls.create_call_v3(
            self, context=context,
            request_resource_ref=request_resource_ref,
            terminal_delivery_ref=terminal_delivery_ref,
            semantic_prompt_resource_ref=semantic_prompt_resource_ref,
            llm_input_target_ref=llm_input_target_ref,
            llm_execution_target_ref=llm_execution_target_ref,
            backend=backend, model=model,
            transport_contract_ref=transport_contract_ref,
            interaction_protocol_ref=interaction_protocol_ref,
            response_adapter_ref=response_adapter_ref,
            tool_catalog_ref=tool_catalog_ref,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            call_id=call_id, idempotency_key=idempotency_key)

    def reserve_v2(
            self, *, context: InvocationContext, call: LLMCallV2,
            prior_attempt: ProviderAttemptV2 | None,
            idempotency_key: str) -> ProviderAttemptV2:
        from ._provider_calls import attempts
        return attempts.reserve_v2(
            self, context=context, call=call,
            prior_attempt=prior_attempt, idempotency_key=idempotency_key)

    def reserve_v3(
            self, *, context: InvocationContext, call: LLMCallV3,
            idempotency_key: str) -> ProviderAttemptV3:
        from ._provider_calls import attempts
        return attempts.reserve_v3(
            self, context=context, call=call,
            idempotency_key=idempotency_key)

    def complete_v2(
            self, attempt: ProviderAttemptV2, *, response: bytes,
            status_code: int | None, external_request_id: str | None,
            idempotency_key: str):
        from ._provider_calls import attempts
        return attempts.complete_v2(
            self, attempt, response=response, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def complete_v3(
            self, attempt: ProviderAttemptV3, *, response: bytes,
            status_code: int | None, external_request_id: str | None,
            idempotency_key: str):
        from ._provider_calls import attempts
        return attempts.complete_v3(
            self, attempt, response=response, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def record_materialization(
            self, *, context: InvocationContext,
            attempt: ProviderAttemptV2 | ProviderAttemptV3,
            request_payload: bytes, idempotency_key: str):
        from ._provider_calls import attempts
        return attempts.record_materialization(
            self, context=context, attempt=attempt,
            request_payload=request_payload, idempotency_key=idempotency_key)

    def reconcile_v2(
            self, attempt: ProviderAttemptV2, *, proof_ref: VersionRef,
            idempotency_key: str) -> ProviderAttemptV2:
        from ._provider_calls import attempts
        return attempts.reconcile_v2(
            self, attempt, proof_ref=proof_ref,
            idempotency_key=idempotency_key)

    def _revalidate_call_v2_context(
            self, call: LLMCallV2, *, boundary: str,
            require_current_writer: bool = True) -> InvocationContext:
        from ._provider_calls import attempts
        return attempts._revalidate_call_v2_context(
            self, call, boundary=boundary,
            require_current_writer=require_current_writer)

    def _revalidate_call_v3_context(
            self, call: LLMCallV3, *, boundary: str,
            require_current_writer: bool = True) -> InvocationContext:
        from ._provider_calls import attempts
        return attempts._revalidate_call_v3_context(
            self, call, boundary=boundary,
            require_current_writer=require_current_writer)

    def _provider_request_schema(self, context: InvocationContext) -> str:
        from ._provider_calls import attempts
        return attempts._provider_request_schema(self, context)

    def _canonical_budget_scope(
            self, context: InvocationContext) -> tuple[str, str | None]:
        from ._provider_calls import attempts
        return attempts._canonical_budget_scope(self, context)

    def _historical_revalidate_call_context(
            self, call: LLMCall, *, boundary: str,
            require_current_writer: bool = True) -> InvocationContext:
        from ._provider_calls import legacy
        return legacy._historical_revalidate_call_context(
            self, call, boundary=boundary,
            require_current_writer=require_current_writer)

    def _historical_reserve(
            self, *, context: InvocationContext, call: LLMCall,
            request_authority: ExternallyValidatedProviderRequestAuthority,
            operation_binding_metadata: Mapping[str, Any],
            llm_execution_target_ref: VersionRef,
            request_resource_ref: ResourceVersionRef,
            terminal_delivery_ref: VersionRef,
            backend: str, model: str, transport_kind: str, timeout_seconds: int,
            response_protocol: str,
            request_payload_digest: str, retry_cause: str | None = None,
            prior_attempt: ProviderAttempt | None = None,
            resource_contract_ref: VersionRef | None = None,
            idempotency_key: str) -> ProviderAttempt:
        from ._provider_calls import legacy
        return legacy._historical_reserve(
            self, context=context, call=call,
            request_authority=request_authority,
            operation_binding_metadata=operation_binding_metadata,
            llm_execution_target_ref=llm_execution_target_ref,
            request_resource_ref=request_resource_ref,
            terminal_delivery_ref=terminal_delivery_ref,
            backend=backend, model=model, transport_kind=transport_kind,
            timeout_seconds=timeout_seconds,
            response_protocol=response_protocol,
            request_payload_digest=request_payload_digest,
            retry_cause=retry_cause, prior_attempt=prior_attempt,
            resource_contract_ref=resource_contract_ref,
            idempotency_key=idempotency_key)

    def _append(
            self, attempt: ProviderAttemptV2 | ProviderAttemptV3,
            event_type: str,
            payload: Mapping[str, Any], *, idempotency_key: str,
            relations: tuple[TypedRelation, ...] = ()) -> None:
        from ._provider_calls import attempts
        return attempts._append(
            self, attempt, event_type, payload,
            idempotency_key=idempotency_key, relations=relations)

    def dispatch_started(
            self, attempt: ProviderAttemptV2 | ProviderAttemptV3, *,
            materialization_receipt_ref: VersionRef,
            request_byte_count: int, idempotency_key: str) -> None:
        from ._provider_calls import attempts
        return attempts.dispatch_started(
            self, attempt,
            materialization_receipt_ref=materialization_receipt_ref,
            request_byte_count=request_byte_count,
            idempotency_key=idempotency_key)

    def submission_permitted(
            self, attempt: ProviderAttemptV2 | ProviderAttemptV3, *,
            dispatch_event_id: TypedId,
            operation_execution_lease_ref: VersionRef,
            operation_start_event_id: TypedId,
            lifecycle_observation_event_id: TypedId,
            evidence_receipt_id: str | None,
            idempotency_key: str) -> EventEnvelope:
        from ._provider_calls import attempts
        return attempts.submission_permitted(
            self, attempt, dispatch_event_id=dispatch_event_id,
            operation_execution_lease_ref=operation_execution_lease_ref,
            operation_start_event_id=operation_start_event_id,
            lifecycle_observation_event_id=lifecycle_observation_event_id,
            evidence_receipt_id=evidence_receipt_id,
            idempotency_key=idempotency_key)

    def submission_not_permitted(
            self, attempt: ProviderAttemptV2, *,
            dispatch_event_id: TypedId,
            closed_reason: str, idempotency_key: str,
    ) -> tuple[EventEnvelope, EventEnvelope]:
        from ._provider_calls import attempts
        return attempts.submission_not_permitted(
            self, attempt, dispatch_event_id=dispatch_event_id,
            closed_reason=closed_reason, idempotency_key=idempotency_key)

    def submission_unknown(
            self, attempt: ProviderAttemptV2, *,
            dispatch_event_id: TypedId | None = None,
            diagnostic_resource_ref: ResourceVersionRef,
            require_current_writer: bool = True,
            idempotency_key: str,
    ) -> tuple[EventEnvelope, EventEnvelope]:
        from ._provider_calls import attempts
        return attempts.submission_unknown(
            self, attempt, dispatch_event_id=dispatch_event_id,
            diagnostic_resource_ref=diagnostic_resource_ref,
            require_current_writer=require_current_writer,
            idempotency_key=idempotency_key)

    def close_before_dispatch(
            self, attempt: ProviderAttemptV2, *, closed_reason: str,
            require_current_writer: bool = True,
            idempotency_key: str) -> tuple[EventEnvelope, EventEnvelope]:
        from ._provider_calls import attempts
        return attempts.close_before_dispatch(
            self, attempt, closed_reason=closed_reason,
            require_current_writer=require_current_writer,
            idempotency_key=idempotency_key)

    def _historical_cancelled_before_submission(
            self, attempt: ProviderAttempt, *, reason: str,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_cancelled_before_submission(
            self, attempt, reason=reason, idempotency_key=idempotency_key)

    def _historical_failed(
            self, attempt: ProviderAttempt, *, failure: BaseException,
            retry_allowed: bool, idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_failed(
            self, attempt, failure=failure, retry_allowed=retry_allowed,
            idempotency_key=idempotency_key)

    def _historical_outcome_unknown(
            self, attempt: ProviderAttempt, *, fault: BaseException,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_outcome_unknown(
            self, attempt, fault=fault, idempotency_key=idempotency_key)

    def _historical_prove_not_submitted(
            self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_prove_not_submitted(
            self, attempt, proof_ref=proof_ref,
            idempotency_key=idempotency_key)

    def _historical_prove_submitted(
            self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_prove_submitted(
            self, attempt, proof_ref=proof_ref,
            idempotency_key=idempotency_key)

    def _historical_cancelled_after_submission(
            self, attempt: ProviderAttempt, *, reason: str,
            retry_allowed: bool, idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_cancelled_after_submission(
            self, attempt, reason=reason, retry_allowed=retry_allowed,
            idempotency_key=idempotency_key)

    def _historical_reconcile_failed(
            self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
            retry_allowed: bool, idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_reconcile_failed(
            self, attempt, proof_ref=proof_ref,
            retry_allowed=retry_allowed, idempotency_key=idempotency_key)

    def _historical_reconcile_cancelled_after_submission(
            self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
            retry_allowed: bool, idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_reconcile_cancelled_after_submission(
            self, attempt, proof_ref=proof_ref,
            retry_allowed=retry_allowed, idempotency_key=idempotency_key)

    def _require_proof(self, proof_ref: VersionRef) -> None:
        from ._provider_calls import legacy
        return legacy._require_proof(self, proof_ref)

    def reconcile_interrupted_attempts_on_resume(
            self, *,
            publish_permitted_attempt_diagnostic: Callable[
                [ProviderAttemptV2, EventEnvelope], ResourceVersionRef],
    ) -> tuple[str, ...]:
        from ._provider_calls import recovery
        return recovery.reconcile_interrupted_attempts_on_resume(
            self,
            publish_permitted_attempt_diagnostic=(
                publish_permitted_attempt_diagnostic))

    def _hydrate_recovery_attempt(
            self, attempt_ref: VersionRef, metadata: Mapping[str, Any],
    ) -> ProviderAttemptV2:
        from ._provider_calls import recovery
        return recovery._hydrate_recovery_attempt(
            self, attempt_ref, metadata)

    def _historical_adoption_event(
            self, attempt: ProviderAttempt, response_ref: VersionRef, *,
            selection_authority_ref: VersionRef,
            idempotency_key: str) -> PendingEvent:
        from ._provider_calls import legacy
        return legacy._historical_adoption_event(
            self, attempt, response_ref,
            selection_authority_ref=selection_authority_ref,
            idempotency_key=idempotency_key)

    def _historical_append_adoption(
            self, transaction: Any, attempt: ProviderAttempt,
            response_ref: VersionRef, *,
            selection_authority_ref: VersionRef,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_append_adoption(
            self, transaction, attempt, response_ref,
            selection_authority_ref=selection_authority_ref,
            idempotency_key=idempotency_key)

    def _historical_adopt(
            self, attempt: ProviderAttempt, response_ref: VersionRef, *,
            selection_authority_ref: VersionRef,
            idempotency_key: str) -> EventEnvelope:
        from ._provider_calls import legacy
        return legacy._historical_adopt(
            self, attempt, response_ref,
            selection_authority_ref=selection_authority_ref,
            idempotency_key=idempotency_key)

    def _historical_candidate_not_adopted_event(
            self, attempt: ProviderAttempt, response_ref: VersionRef, *,
            reason: str, idempotency_key: str) -> PendingEvent:
        from ._provider_calls import legacy
        return legacy._historical_candidate_not_adopted_event(
            self, attempt, response_ref, reason=reason,
            idempotency_key=idempotency_key)

    def _historical_append_candidate_not_adopted(
            self, transaction: Any, attempt: ProviderAttempt,
            response_ref: VersionRef, *, reason: str,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_append_candidate_not_adopted(
            self, transaction, attempt, response_ref, reason=reason,
            idempotency_key=idempotency_key)

    def _historical_candidate_not_adopted(
            self, attempt: ProviderAttempt, response_ref: VersionRef, *,
            reason: str, idempotency_key: str) -> EventEnvelope:
        from ._provider_calls import legacy
        return legacy._historical_candidate_not_adopted(
            self, attempt, response_ref, reason=reason,
            idempotency_key=idempotency_key)

    def _historical_fail_call(
            self, call: LLMCall, *, reason: str,
            idempotency_key: str) -> None:
        from ._provider_calls import legacy
        return legacy._historical_fail_call(
            self, call, reason=reason, idempotency_key=idempotency_key)


from ._provider_calls.calls import materialize_call_v2_publication
