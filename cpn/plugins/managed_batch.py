"""Owner gateway for managed calls in one registered operation firing.

This is a binding adapter for the existing InvocationService and scheduler.
It creates no AgentLoop, permission ledger, scheduling policy or settlement.
The registered operation and its exact input resources remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from .api import PluginError, json_copy
from .managed_scheduler import ManagedSchedulerPolicy, ManagedToolCall
from .managed_tools import ManagedPluginInvocationService


@dataclass(frozen=True, slots=True, eq=False)
class ManagedOperationBatch:
    """An immutable, owner-derived view; never an alternative authority."""

    execution: object
    policy: ManagedSchedulerPolicy
    calls: tuple[ManagedToolCall, ...]
    input_payloads: tuple[bytes, ...]


class ManagedOperationBatchService:
    """Expose original prepare/finish through the existing owner gateway.

    ``project_calls`` is registered HOST code. It receives the verified
    execution and original input bytes read by exact Registry references,
    never a worker-supplied state document. The operation config owns
    ``managed_tool_policy``. The small
    live binding cache avoids reparsing the same immutable input for each call;
    it is discarded after the batch and is never used for recovery.
    """

    def __init__(self, invocations: ManagedPluginInvocationService,
                 project_calls: Callable):
        if (not isinstance(invocations, ManagedPluginInvocationService)
                or not callable(project_calls)):
            raise TypeError('managed batch requires owner invocations and HOST projection')
        self.invocations = invocations
        self.owner = invocations.owner
        self._project_calls = project_calls
        self._bindings = {}

    def gateway_methods(self):
        return {
            'bind_managed_operation_batch_v1': self.bind,
            'prepare_managed_operation_call_v1': self.prepare,
            'finish_managed_operation_call_v1': self.finish,
            'release_managed_operation_batch_v1': self.release,
        }

    @staticmethod
    def _key(execution):
        return (execution.operation_execution_lease_ref,
                execution.operation.firing.transition_firing_ref)

    def bind(self, execution):
        from cpn.components.agent_loop.managed_execution import (
            managed_scheduler_policy_from_document,
        )
        from cpn.rpnh.executable_net import load_compiled_net
        from cpn.rpnh.registry.operation_execution import verify_operation_execution
        from cpn.rpnh.registry.publication import _resource_from_payload

        service = self.invocations
        execution = verify_operation_execution(
            service.core, service.kernel, service.repository, execution)
        key = self._key(execution)
        previous = self._bindings.get(key)
        if previous is not None:
            if previous.execution != execution:
                raise PluginError('managed batch execution differs from its original input closure')
            return previous
        operation = execution.operation
        binding = service.kernel._exact_object(
            operation.transition.binding_ref,
            expected_type='executable_transition_binding/v1')
        source = _resource_from_payload(
            binding.metadata['declaration_resource_ref'])
        compiled = load_compiled_net(json.loads(
            service.kernel._read_firing_registered(
                operation.canonical.context, source)))
        declared = next(item for item in compiled.operations
                        if item.operation_id == operation.spec.operation_id)
        document = declared.declaration.config.get('managed_tool_policy')
        if document is None:
            raise PluginError('operation has no registered managed scheduling policy')
        policy = managed_scheduler_policy_from_document(document)
        # Typed execution identity does not make a copied DTO payload into
        # registered bytes. Read each original resource once on the owner.
        input_payloads = tuple(service.kernel._read_firing_registered(
            operation.canonical.context, item.resource_ref)
            for item in operation.inputs)
        calls = tuple(self._project_calls(execution, input_payloads))
        if (not calls
                or any(not isinstance(call, ManagedToolCall) for call in calls)
                or tuple(call.ordinal for call in calls) != tuple(range(len(calls)))
                or len({call.call_id for call in calls}) != len(calls)):
            raise PluginError('registered batch requires original unique ordered calls')
        for call in calls:
            declaration = service.catalog.declaration_for_registration_key(
                call.registration_key)
            if (declaration.name != call.name
                    or call.registration_key not in operation.spec.allowed_tool_ids):
                raise PluginError('batch call differs from its registered operation tools')
            policy.validate_declaration(declaration)
        scope = ManagedOperationBatch(execution, policy, calls, input_payloads)
        self._bindings[key] = scope
        return scope

    def _scope(self, scope):
        if (not isinstance(scope, ManagedOperationBatch)
                or self._bindings.get(self._key(scope.execution)) is not scope):
            raise PluginError('managed batch is not this owner-derived live binding')
        return scope

    def prepare(self, scope, ordinal):
        scope = self._scope(scope)
        if type(ordinal) is not int or not 0 <= ordinal < len(scope.calls):
            raise PluginError('managed batch ordinal is outside its original calls')
        call = scope.calls[ordinal]
        return self.invocations.prepare(
            call.name, execution=scope.execution, call_id=call.call_id,
            arguments=json_copy(call.arguments),
            registration_key=call.registration_key,
            allowed_effects=scope.policy.admitted_effects,
            scheduling_policy=scope.policy)

    def finish(self, scope, prepared, completion):
        from cpn.rpnh.registry.strict_contracts import ref_payload

        scope = self._scope(scope)
        material = json.loads(prepared.receipt_material)
        call = next((call for call in scope.calls
                     if call.call_id == material.get('call_id')), None)
        execution = scope.execution
        if (call is None
                or material.get('caller_execution_ref')
                != ref_payload(execution.operation_execution_lease_ref)
                or material.get('caller_firing_ref')
                != ref_payload(execution.operation.firing.transition_firing_ref)
                or material['registration']['key'] != call.registration_key
                or material['arguments'] != json_copy(call.arguments)):
            raise PluginError('managed completion crossed its original batch scope')
        return self.invocations.finish(prepared, completion)

    def release(self, scope):
        scope = self._scope(scope)
        self._bindings.pop(self._key(scope.execution))


__all__ = ['ManagedOperationBatch', 'ManagedOperationBatchService']
