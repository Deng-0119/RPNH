"""Owner preparation and observation of explicitly selected managed scheduling."""
from __future__ import annotations

import json

from cpn.plugins.api import PluginError, json_copy
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from .models import AgentLoopState
from .tool_validation import ValidatedAgentToolAction


def managed_scheduler_policy_from_document(value):
    from cpn.plugins.managed_scheduler import ManagedConflictDomain, ManagedSchedulerPolicy
    if not isinstance(value, dict):
        value = dict(value)
    expected = {"policy_id", "max_in_flight"}
    if value.get("policy_id") == "managed_conflict_domains/v1":
        expected.add("conflict_domains")
    if set(value) != expected:
        raise ValueError("managed scheduler policy fields differ from its selected version")
    domains = tuple(ManagedConflictDomain(**dict(row))
                    for row in value.get("conflict_domains", ()))
    return ManagedSchedulerPolicy(value["policy_id"], value["max_in_flight"], domains)


class ManagedExecutionMixin:
    def _managed_scheduler_scope(self, execution, loop):
        self._execution(execution, loop)
        policy = getattr(self, "managed_scheduler_policy", None)
        if policy is None:
            return None
        if not self._managed_tool_bindings(self._context(loop)):
            return None
        _ref, _prepared, payload = self._static(
            self._context(loop), "optional_managed_tool_scheduler")
        if json.loads(payload) != policy.identity():
            raise ResourceIntegrityFault("managed scheduler differs from its registered HOST policy")
        return policy

    def managed_agent_scheduler_v1(self, execution, loop):
        policy = self._managed_scheduler_scope(execution, loop)
        if policy is None:
            return None
        from cpn.plugins.managed_scheduler import ManagedToolScheduler
        return (ManagedToolScheduler(policy, self.managed_run_capacity),
                self._managed_tool_bindings(self._context(loop)))

    def _managed_call_scope(self, execution, loop, turn, call):
        policy = self._managed_scheduler_scope(execution, loop)
        if policy is None or loop.state != AgentLoopState.TURN_STORED:
            raise ResourceIntegrityFault("managed preparation requires its selected stored turn")
        exact_turn = self.hydrate_current_agent_turn_v1(loop)
        if exact_turn != turn:
            raise ResourceIntegrityFault("managed scheduling crossed its exact turn")
        actions = self.prepare_agent_turn_actions_v1(loop, turn)
        if not 0 <= call.ordinal < len(actions):
            raise ResourceIntegrityFault("managed scheduling has an invalid ordinal")
        action = actions[call.ordinal]
        if (action.tool_call.action_identity_key != call.call_id
                or action.tool_call.tool_name != call.name):
            raise ResourceIntegrityFault("managed scheduling crossed its provider call identity")
        if not isinstance(action.validation, ValidatedAgentToolAction):
            raise PluginError("managed call was rejected before dispatch")
        if json_copy(action.validation.arguments) != json_copy(call.arguments):
            raise ResourceIntegrityFault("managed scheduling changed the registered arguments")
        bindings = self._managed_tool_bindings(self._context(loop))
        binding = bindings.get(call.name)
        if binding is None or binding["registration_key"] != call.registration_key:
            raise ResourceIntegrityFault("managed scheduling crossed its exact binding")
        _binding, _compiled, operation = self._declared(self._context(loop))
        service = self.managed_plugin_services[
            operation.declaration.config["semantic_node_id"]]
        policy.validate_declaration(service.catalog.declaration(call.name))
        return service, policy

    def prepare_managed_agent_call_v1(self, execution, loop, turn, call):
        service, policy = self._managed_call_scope(execution, loop, turn, call)
        return service.prepare(
            call.name, execution=execution, call_id=call.call_id,
            arguments=call.arguments, registration_key=call.registration_key,
            allowed_effects=policy.admitted_effects, scheduling_policy=policy)

    def finish_managed_agent_call_v1(self, execution, loop, turn, call,
                                    prepared, completion):
        service, _policy = self._managed_call_scope(execution, loop, turn, call)
        material = json.loads(prepared.receipt_material)
        if (material["call_id"] != call.call_id
                or material["registration"]["key"] != call.registration_key
                or material["arguments"] != json_copy(call.arguments)):
            raise ResourceIntegrityFault("managed completion crossed its admitted call")
        return service.finish(prepared, completion)
