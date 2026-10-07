"""Run an admitted program outside the owner through its existing managed pool."""
from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import time

from cpn.plugins.api import PluginError, json_copy
from cpn.plugins.controlled_script import IsolatedProgramResult, ProgramBrokerReply, run_isolated_program
from cpn.plugins.managed_scheduler import ManagedToolCall
from cpn.plugins.managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationReconciliationRequired,
)
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload


class ToolProgramNotAdmitted(PluginError):
    """An input rejected before publishing the parent program admission."""


def execute_registered_tool_program(registry, execution, loop, turn, action, *,
                                    settlement_key, cancelled):
    policy = registry.current_tool_program_policy_v1(execution, loop)
    selected = registry.managed_agent_scheduler_v1(execution, loop)
    if policy is None or selected is None:
        raise ToolProgramNotAdmitted("tool program requires its explicitly registered HOST policy")
    source = action.validation.arguments["source"]
    if not source.strip() or len(source.encode("utf-8")) > policy["budget"]["max_source_bytes"]:
        raise ToolProgramNotAdmitted("tool program source exceeds its explicitly selected budget")
    scheduler, bindings = selected
    program = registry.begin_tool_program_v1(
        execution, loop, turn, action, policy, settlement_key)
    operation_key = str(execution.operation_execution_lease_ref.version_id)

    def checked_reply(reply):
        if not isinstance(reply, ProgramBrokerReply):
            raise ResourceIntegrityFault("program child lacks its owner-observed reply")
        if reply.outcome_unknown:
            raise ManagedPluginInvocationReconciliationRequired(
                "program child requires reconciliation", evidence={"program_reply": {
                    "value": json_copy(reply.value), "error_code": reply.error_code,
                    "outcome_unknown": True}})
        return reply

    def broker(call):
        pending = {}
        binding = bindings[call.tool]
        scheduled = ManagedToolCall(
            0, call.tool, f"tool-program-child/v1:{program.program_ref.entity_id}:{call.key}",
            call.arguments, binding["registration_key"])
        stopped = lambda: (cancelled() or call.cancelled()
                           or time.monotonic() >= call.deadline_monotonic)

        def prepare(_call):
            child = registry.prepare_program_child_v1(
                execution, loop, turn, program.program_ref, call)
            pending["child"] = child
            if child.closed_reply is not None:
                future = Future()
                try:
                    future.set_result(checked_reply(child.closed_reply))
                except ManagedPluginInvocationReconciliationRequired as exc:
                    future.set_exception(exc)
                return ManagedInvocationObservation(future)
            return child.managed_prepared

        def finish(_prepared, completion):
            return checked_reply(registry.finish_program_child_v1(
                execution, loop, turn, program.program_ref, call,
                pending["child"], completion))

        batch = scheduler.run((scheduled,), operation_key=operation_key,
                              prepare=prepare, finish=finish, cancelled=stopped)
        outcome, = batch.outcomes
        if outcome.error is not None:
            if isinstance(outcome.error, ManagedPluginInvocationReconciliationRequired):
                evidence = outcome.error.evidence or {}
                if "program_reply" in evidence:
                    return ProgramBrokerReply(**evidence["program_reply"])
            if outcome.not_started or isinstance(outcome.error, PluginError):
                reply = registry.reject_program_child_v1(
                    execution, loop, turn, program.program_ref, call,
                    error_code="program_child_not_dispatched")
                return reply
            raise outcome.error
        if isinstance(outcome.result, ProgramBrokerReply):
            return outcome.result
        # The SDK prohibits duplicate accepted keys. A durable same-key
        # observation, when supplied by another observer, still closes through
        # the owner rather than executing a worker twice.
        return registry.finish_program_child_v1(
            execution, loop, turn, program.program_ref, call,
            pending["child"], outcome.result)

    def read_result(request):
        locator = json_copy(request.locator)
        if locator.get("program_invocation_ref") != _ref_payload(program.program_ref):
            raise ResourceIntegrityFault("program child reader crossed its admitted parent")
        return registry.read_program_child_output_v1(
            execution, loop, turn, program.program_ref,
            _version_from_payload(locator["program_call_ref"]),
            offset_chars=request.offset_chars, max_bytes=request.max_bytes)

    work_root = Path(registry.tool_program_work_root_v1(execution, loop))
    try:
        if work_root.resolve() != work_root.absolute():
            raise ValueError("program preparation root crosses a filesystem link")
        work_root.mkdir(parents=True, exist_ok=True)
        result = run_isolated_program(
            source=program.source, arguments=program.arguments,
            parent_identity=program.parent_identity, allowlist=program.allowlist,
            budget=program.budget, broker=broker, read_result=read_result,
            work_root=work_root, cancelled=cancelled)
    except (OSError, ValueError) as exc:
        result = IsolatedProgramResult(
            "setup_failed", None, "", "", (),
            {"phase": "program-input-or-preparation", "errno": getattr(exc, "errno", None),
             "error": str(exc)}, {"profile_id": policy["profile_id"]})
    return registry.complete_tool_program_v1(
        execution, loop, turn, program.program_ref, result,
        settlement_key=settlement_key)
