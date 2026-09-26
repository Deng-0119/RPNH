"""Optional external executor services on the existing sole owner/event loop.

Construct on the owner side and pass the explicit gateway to trusted HOST
executors. Calls enqueue through RegistryGateway; production workers call it
while the owner dispatches. No Current/default executor or writer is created.
Provider commands are the existing ledger, not a provider transport.
"""
from __future__ import annotations

import json
from typing import Mapping

from cpn.rpnh.control_server import OwnerEventLoop, RegistryGateway
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.run import RunOwner
from cpn.rpnh.registry.firing_authority import canonical_invocation
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.operations import (
    OperationAuthorityError, OperationExecutionAuthority,
    OperationExecutionBlockAuthority, register_operation_outputs,
)
from cpn.rpnh.registry.provider_calls import ProviderAttemptLedger
from cpn.rpnh.registry.publication import _resource_from_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.resources import CanonicalInvocationAuthority
from cpn.rpnh.registry.schema_catalog import canonical_json


class _RegisteredOptionalInputBinding:
    """Per-firing accounting around the SAME configured neutral input port.

    This binding selects no model/route and performs no retry. Only authorized
    real workers call request_once; finite proofs invoke the owner authorities
    directly with registered fake raw bytes, never a fake transport.
    """
    def __init__(
            self, gateway, input_port, execution,
            interruption_requested=None):
        self.gateway = gateway
        self.input_port = input_port
        self.execution = execution
        self.interruption_requested = (
            interruption_requested or (lambda: False))

    def request_once(self, attempt):
        self.gateway.prepare_optional_input_submission(self.execution, attempt)
        interruptible = getattr(
            self.input_port, "request_once_interruptible", None)
        try:
            response = (
                interruptible(
                    attempt,
                    interruption_requested=self.interruption_requested)
                if callable(interruptible) else
                self.input_port.request_once(attempt))
        except Exception as exc:
            from cpn.rpnh.llm_contracts import LLMInputPortInterrupted
            if not isinstance(exc, LLMInputPortInterrupted):
                raise
            self.gateway.record_optional_input_owner_interruption(
                self.execution, attempt, exc.submission_state)
            raise
        self.gateway.register_optional_input_return(
            self.execution, attempt, bytes(response),
            status_code=getattr(response, "status_code", None),
            external_request_id=getattr(
                response, "external_request_id", None))
        return response

    @property
    def execution_policy(self):
        """Forward the exact non-secret policy of the shared bound port."""

        return self.input_port.execution_policy

    def close(self):
        # No transport is owned by this per-firing, stateless binding. The
        # supplied HOST owns its shared configured port's lifetime.
        pass


def invoke_registered_tool(owner, kernel, repository, execution, tool_name, *,
                           identity, contracts, args=(), kwargs=None):
    """Exact HOST tool ABI shared by owner dispatch and synchronous boundaries."""
    execution = verify_operation_execution(owner._core, kernel, repository, execution)
    operation = execution.operation
    if tool_name not in operation.spec.allowed_tool_ids:
        raise OperationAuthorityError("tool is outside this operation's declared tools")
    binding = kernel._exact_object(operation.transition.binding_ref,
                                  expected_type="executable_transition_binding/v1")
    declaration_ref = _resource_from_payload(binding.metadata["declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(kernel._read_firing_registered(
        operation.canonical.context, declaration_ref)))
    declared = next(item for item in compiled.operations if item.operation_id == operation.spec.operation_id)
    if tuple(declared.declaration.tools) != operation.spec.allowed_tool_ids:
        raise OperationAuthorityError("tool inventory differs from the admitted operation")
    registered = owner.registration.declaration("tool", tool_name)
    if (compiled.registrations["tool"].get(tool_name) != registered
            or identity != registered["identity"] or contracts != registered["contracts"]):
        raise OperationAuthorityError("tool identity/contracts differ from exact HOST registration")
    if (not isinstance(args, tuple) or (kwargs is not None and (not isinstance(kwargs, Mapping)
            or any(not isinstance(key, str) for key in kwargs)))):
        raise TypeError("tool arguments require the declared positional/keyword ABI")
    tool = owner.registration.resolve("tool", tool_name)
    return tool(*args, **({} if kwargs is None else kwargs))


class ExecutionServices:
    """Bind generic publication, verification, tools and typed attempt commands.

    Start, Success, adoption and terminal publication are intentionally absent.
    The ledger delegates reserve/raw-response publication to the sole Core;
    these services do not implement or bypass that mechanical authority.
    """

    def __init__(self, *, owner: RunOwner, event_loop: OwnerEventLoop,
                 provider_attempts: ProviderAttemptLedger | None = None,
                 llm_input_port=None, llm_input_ports_by_transition=None,
                 interruption_requested=None) -> None:
        if not isinstance(owner, RunOwner):
            raise TypeError("execution services require the existing RunOwner")
        if not isinstance(event_loop, OwnerEventLoop) or event_loop.owner is not owner:
            raise TypeError("execution services require this owner's existing event loop")
        if provider_attempts is None:
            provider_attempts = ProviderAttemptLedger(owner._core)
        if (not isinstance(provider_attempts, ProviderAttemptLedger)
                or provider_attempts.service is not owner._core):
            raise TypeError("provider ledger must be bound to this owner's sole Core")
        self._owner = owner
        self._event_loop = event_loop
        self._llm_input_port = llm_input_port
        if (llm_input_ports_by_transition is not None
                and (not isinstance(llm_input_ports_by_transition, Mapping)
                     or any(not isinstance(key, str) or not key or value is None
                            for key, value
                            in llm_input_ports_by_transition.items()))):
            raise TypeError(
                "transition input ports require a nonempty string mapping")
        self._llm_input_ports_by_transition = dict(
            llm_input_ports_by_transition or {})
        if (interruption_requested is not None
                and not callable(interruption_requested)):
            raise TypeError("interruption request probe must be callable")
        self._interruption_requested = (
            interruption_requested or (lambda: False))
        self._provider_ledger = provider_attempts
        self._kernel, self._repository = owner.operation_repository()
        methods = {
            "head": self._kernel.head,
            "get_header": self._kernel.get_header,
            "publish_bytes": self._kernel.publish_bytes,
            "verify_resource": self._verify_resource,
            "register_operation_outputs": self._register_operation_outputs,
            "record_registered_operation_completion": (
                self._record_registered_operation_completion),
            "register_operation_execution_block": self._register_operation_execution_block,
            "create_call_v2": provider_attempts.create_call_v2,
            "reserve_v2": provider_attempts.reserve_v2,
            "record_materialization": provider_attempts.record_materialization,
            "dispatch_started": provider_attempts.dispatch_started,
            "submission_permitted": provider_attempts.submission_permitted,
            "complete_v2": provider_attempts.complete_v2,
            "invoke_registered_tool": self._invoke_registered_tool,
            "operation_interruption_requested": (
                self._operation_interruption_requested),
        }
        # Native plugin operations use the same owner and verified product gateway.
        from cpn.plugins.host import NativePluginHost
        plugin_host = NativePluginHost(owner, self._kernel, self._repository)
        methods.update({"native_plugin_prepare": plugin_host.prepare,
                        "native_plugin_products": plugin_host.products,
                        "native_plugin_failed": plugin_host.failed})
        self._optional_agent_service = None
        self._registered_host_llm_service = None
        if llm_input_port is not None or self._llm_input_ports_by_transition:
            from .agent_loop.optional_execution import OptionalAgentLoopRegistryService
            self._optional_agent_service = OptionalAgentLoopRegistryService(owner=owner,
                kernel=self._kernel, repository=self._repository, provider_attempts=provider_attempts,
                invoke_tool=self._invoke_registered_tool)
            methods.update(self._optional_agent_service.gateway_methods())
            methods["prepare_optional_input_submission"] = self._prepare_optional_input_submission
            methods["register_optional_input_return"] = self._register_optional_input_return
            methods["record_optional_input_owner_interruption"] = (
                self._record_optional_input_owner_interruption)
            from .registered_host_llm import RegisteredHostLLMRegistryService
            self._registered_host_llm_service = RegisteredHostLLMRegistryService(
                owner=owner, kernel=self._kernel,
                repository=self._repository,
                provider_attempts=provider_attempts)
            methods.update(
                self._registered_host_llm_service.gateway_methods())
        self.gateway = RegistryGateway(event_loop, methods)

    def prepare_dispatcher(self, *, execution, executable, claimed_inputs, event_loop):
        """Prepare the actual optional scheduler shell on this owner's services.

        Custom executors use their own declared ABI; a default AgentLoop port
        is supplied only when that explicitly registered executor needs one.
        """
        if event_loop is not self._event_loop:
            raise ValueError("dispatcher preparation crossed the owner's channel")
        execution = self._execution(execution)
        from .registered_operation_dispatcher import RegisteredOperationDispatcher
        from .executors import execute_default_operation
        from cpn.rpnh.registry.operations import (
            registered_operation_host_protocols,
        )
        from .registered_host_llm import HOST_PROTOCOL, RegisteredHostLLM
        port = self._llm_input_ports_by_transition.get(
            execution.operation.firing.transition_id,
            self._llm_input_port)
        executor = self._owner.registration.resolve(
            "executor", execution.operation.spec.executor_key)
        registered_llm = None
        if port is not None and executor is execute_default_operation:
            port = _RegisteredOptionalInputBinding(
                self.gateway, port, execution,
                self._interruption_requested)
        elif HOST_PROTOCOL in registered_operation_host_protocols(
                execution.operation.spec.executor_key):
            if port is None:
                raise OperationAuthorityError(
                    "registered HOST LLM executor lacks a configured input port")
            registered_llm = RegisteredHostLLM(
                execution=execution, gateway=self.gateway,
                input_port=port,
                interruption_requested=self._interruption_requested)
            port = None
        dispatcher = RegisteredOperationDispatcher(execution=execution, executable=executable,
            claimed_inputs=claimed_inputs, registry=self.gateway, resources=self.resources,
            llm_input_port=port, registered_llm=registered_llm)
        from .harness_adapter import adapt_registered_dispatcher
        return adapt_registered_dispatcher(execution, dispatcher)

    def _optional_input_authority(self, execution, attempt):
        execution = self._execution(execution)
        provider = self._optional_agent_service.provider_attempt_for_agent_llm_v1(attempt)
        context = execution.operation.canonical.context
        if (provider.call.invocation_ref != context.invocation_ref
                or provider.call.operation_binding_ref != context.operation_binding_ref):
            raise OperationAuthorityError("neutral input crossed its exact firing Start")
        invocation = self._kernel._exact_object(attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1").metadata
        recipe = json.loads(self._kernel._read_firing_registered(
            context, provider.call.request_resource_ref))
        envelope = {"protocol": "llm_request_envelope/v1", "model_condition": recipe["model"],
            "max_output_tokens": recipe["max_tokens"], "messages": recipe["messages"],
            "tools": recipe["tools"], "tool_choice": recipe["tool_choice"],
            "source_prompt_ref": recipe["source_prompt_ref"], "tool_catalog_ref": recipe["tool_catalog_ref"],
            "placeholders": recipe["placeholders"]}
        if (canonical_json(envelope) != attempt.canonical_request_bytes
                or invocation["model_condition"] != attempt.model_condition
                or invocation["max_response_bytes"] != attempt.max_response_bytes
                or provider.call.model != attempt.model_condition):
            raise OperationAuthorityError("neutral input DTO differs from registered call/recipe/model")
        return execution, provider

    def _prepare_optional_input_submission(self, execution, attempt):
        execution, provider = self._optional_input_authority(execution, attempt)
        key = "optional-input:" + str(attempt.attempt_ref.version_id)
        materialized = self._provider_ledger.record_materialization(
            context=execution.operation.canonical.context, attempt=provider,
            request_payload=attempt.canonical_request_bytes, idempotency_key=key + ":materialization")
        self._provider_ledger.dispatch_started(provider,
            materialization_receipt_ref=materialized.receipt.provider_payload_materialization_receipt_ref,
            request_byte_count=len(attempt.canonical_request_bytes), idempotency_key=key + ":dispatch")
        dispatches = [event for event in self._owner._core.event_store.list_events_by_aggregate(
            str(provider.attempt_id), event_types=("provider_attempt_dispatch_started/v2",))
            if event.idempotency_key == key + ":dispatch"]
        if len(dispatches) != 1:
            raise OperationAuthorityError("neutral input lacks its exact registered dispatch fact")
        dispatch = dispatches[0]
        return self._provider_ledger.submission_permitted(provider,
            dispatch_event_id=dispatch.event_id,
            operation_execution_lease_ref=execution.operation_execution_lease_ref,
            operation_start_event_id=execution.start_event_id,
            lifecycle_observation_event_id=dispatch.event_id, evidence_receipt_id=None,
            idempotency_key=key + ":permit")

    def _register_optional_input_return(
            self, execution, attempt, response, *, status_code,
            external_request_id,
    ):
        _, provider = self._optional_input_authority(execution, attempt)
        return self._provider_ledger.complete_v2(provider,
            response=response, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key="optional-input:" + str(attempt.attempt_ref.version_id) + ":raw-return")

    def _record_optional_input_owner_interruption(
            self, execution, attempt, submission_state):
        execution = self._execution(execution)
        return self._optional_agent_service.record_llm_input_owner_interruption_v1(
            execution, attempt, submission_state,
            idempotency_key=(
                "optional-input:" + str(attempt.attempt_ref.version_id)
                + ":owner-interrupted"))

    @property
    def resources(self):
        """Same owner gateway, not a separately writable resource service."""
        return self.gateway

    @property
    def provider_attempts(self):
        return self.gateway

    @property
    def tools(self):
        return self.gateway

    def _execution(self, execution: OperationExecutionAuthority):
        return verify_operation_execution(self._owner._core, self._kernel,
                                          self._repository, execution)

    def _verify_resource(self, canonical, ref):
        if not isinstance(canonical, CanonicalInvocationAuthority):
            raise TypeError("resource verification requires exact canonical invocation authority")
        current = canonical_invocation(self._owner._core, self._kernel, canonical.context.invocation_ref)
        if current.context != canonical.context:
            raise OperationAuthorityError("resource verification crossed its exact invocation view")
        return verify_resource(self._owner._core, self._kernel,
                               current, ref)

    def _register_operation_outputs(self, execution, outputs, *, idempotency_key,
                                    selected_outcome_id=None):
        execution = self._execution(execution)
        return register_operation_outputs(self._repository, execution, outputs,
            idempotency_key=idempotency_key, selected_outcome_id=selected_outcome_id)

    def _record_registered_operation_completion(
            self, outputs, *, idempotency_key):
        from cpn.rpnh.registry.firing_recovery import (
            record_registered_operation_completion,
        )
        return record_registered_operation_completion(
            self._owner._core, self._kernel, self._repository, outputs,
            idempotency_key=idempotency_key)

    def _operation_interruption_requested(self, execution) -> bool:
        self._execution(execution)
        return bool(self._interruption_requested())

    def _register_operation_execution_block(self, execution, *, block_kind,
            operation_or_tool_identity, error_code, error_message, boundary,
            consecutive_count, exact_error_ref, retry_not_before_utc):
        execution = self._execution(execution)
        self._kernel._exact_object(exact_error_ref,
                                  expected_type=exact_error_ref.entity_type)
        # Core's process-local disposition is not Success or resumable state.
        return OperationExecutionBlockAuthority(execution, block_kind,
            operation_or_tool_identity, error_code, error_message, boundary,
            consecutive_count, exact_error_ref, retry_not_before_utc)

    def _invoke_registered_tool(self, execution, tool_name, *, identity, contracts,
                                args=(), kwargs=None):
        return invoke_registered_tool(self._owner, self._kernel, self._repository,
            execution, tool_name, identity=identity, contracts=contracts, args=args, kwargs=kwargs)


__all__ = ("ExecutionServices", "invoke_registered_tool")
