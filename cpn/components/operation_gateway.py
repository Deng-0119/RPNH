"""Structural HOST seams for the optional registered-operation dispatcher.

These contracts describe supplied services, not authority factories. The
dispatcher still requires exact Core DTOs and firing-local identity; only the
Registry gateway validates and publishes the whole registered product bundle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cpn.rpnh.registry.invocations import InvocationContext
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.operations import (
        OperationExecutionAuthority, OperationExecutionBlockAuthority,
        RegisteredOperationOutputsAuthority,
    )
    from cpn.rpnh.registry.resources import (
        RegistryObserverContext, RegistryHead,
        PublishResource, ResourceHeader, ResourceVersionRef, VerifiedResourceArtifact,
        ProviderRequestMaterialization,
    )
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.provider_calls import LLMCallV2, ProviderAttemptV2
    from cpn.rpnh.response_protocol import PublishedRawLLMResponse


def has_port_methods(value, protocol) -> bool:
    """Check the declared callable ABI, including the owner's dynamic gateway.

    This observes method availability only. Exact execution and publication
    authority are still verified independently by Core, never by this check.
    """
    return all(callable(getattr(value, name, None))
               for name, member in vars(protocol).items()
               if not name.startswith("_") and callable(member))


@runtime_checkable
class RegisteredOperationGateway(Protocol):
    """Exact publication/block closure used directly by the dispatcher."""

    def register_operation_outputs(
        self, execution: OperationExecutionAuthority,
        outputs: tuple[VerifiedResourceArtifact, ...], *,
        idempotency_key: str, selected_outcome_id: str | None = None,
    ) -> RegisteredOperationOutputsAuthority: ...

    def record_registered_operation_completion(
        self, outputs: RegisteredOperationOutputsAuthority, *,
        idempotency_key: str,
    ) -> object: ...

    def register_operation_execution_block(
        self, execution: OperationExecutionAuthority, *, block_kind: str,
        operation_or_tool_identity: str, error_code: str,
        error_message: str | None, boundary: str, consecutive_count: int,
        exact_error_ref: VersionRef, retry_not_before_utc: str | None,
    ) -> OperationExecutionBlockAuthority: ...


@runtime_checkable
class RegisteredOperationResources(Protocol):
    """Registry resource observation surface passed unchanged to HOST code.

The dispatcher itself performs no resource calls. Additional executor-specific
resource capabilities stay owned by that explicitly registered executor.
"""

    def head(
        self, context: InvocationContext | RegistryObserverContext, *,
        ordinal: int | None = None,
    ) -> RegistryHead: ...

    def get_header(
        self, context: InvocationContext | RegistryObserverContext,
        ref: ResourceVersionRef,
    ) -> ResourceHeader: ...

    def publish_bytes(
        self, context: InvocationContext, command: PublishResource,
    ) -> ResourceVersionRef: ...

@runtime_checkable
class RegisteredProviderAttempts(Protocol):
    """Existing typed ledger commands; no transport or replacement accounting."""

    def create_call_v2(
        self, *, context: InvocationContext, loop_ref: Mapping[str, str],
        turn_sequence: int, request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        semantic_prompt_resource_ref: ResourceVersionRef,
        llm_execution_target_ref: ResourceVersionRef, backend: str, model: str,
        transport_contract_ref: ResourceVersionRef, interaction_protocol_ref: str,
        response_adapter_ref: str, tool_catalog_ref: ResourceVersionRef,
        prior_turn_refs: tuple[VersionRef, ...], timeout_seconds: int,
        max_response_bytes: int, call_id: TypedId | None = None,
        idempotency_key: str,
    ) -> LLMCallV2: ...

    def reserve_v2(
        self, *, context: InvocationContext, call: LLMCallV2,
        prior_attempt: ProviderAttemptV2 | None, idempotency_key: str,
    ) -> ProviderAttemptV2: ...

    def complete_v2(
        self, attempt: ProviderAttemptV2, *, response: bytes,
        status_code: int | None,
        external_request_id: str | None, idempotency_key: str,
    ) -> PublishedRawLLMResponse: ...

    def record_materialization(
        self, *, context: InvocationContext, attempt: ProviderAttemptV2,
        request_payload: bytes, idempotency_key: str,
    ) -> ProviderRequestMaterialization: ...

    def dispatch_started(
        self, attempt: ProviderAttemptV2, *, materialization_receipt_ref: VersionRef,
        request_byte_count: int, idempotency_key: str,
    ) -> None: ...

    def submission_permitted(
        self, attempt: ProviderAttemptV2, *, dispatch_event_id: TypedId,
        operation_execution_lease_ref: VersionRef, operation_start_event_id: TypedId,
        lifecycle_observation_event_id: TypedId, evidence_receipt_id: str | None,
        idempotency_key: str,
    ) -> object: ...


@runtime_checkable
class RegisteredOperationTools(Protocol):
    """Trusted HOST tool calls retain their registered positional/keyword ABI."""

    def invoke_registered_tool(
        self, execution: OperationExecutionAuthority, tool_name: str, *,
        identity: Mapping[str, object], contracts: Mapping[str, object],
        args: tuple[object, ...] = (), kwargs: Mapping[str, object] | None = None,
    ) -> object: ...


__all__ = ["has_port_methods", "RegisteredOperationGateway", "RegisteredOperationResources",
           "RegisteredProviderAttempts", "RegisteredOperationTools"]
