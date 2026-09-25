"""Generic firing-local executor and AgentLoop policy.

HOST registration selects this component explicitly. Ordinary products remain
OperationExecutionResult values; only the generic dispatcher registers outputs.
Resource continuation retains this component's exact execution and P0 grants.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

from cpn.components.registered_operation_dispatcher import (
    RegisteredOperationAuthorityRequiredError,
    RegisteredOperationCompletion,
    RegisteredOperationContext,
    RegisteredOperationExecutionBlock,
    RegisteredOperationResourceWait,
    RegisteredOperationTerminalHandoff,
)

if TYPE_CHECKING:
    from cpn.rpnh.registry.operations import (
        OperationExecutionAuthority, OperationExecutionResult,
    )

_LOGGER = logging.getLogger(__name__)


def _semantic_outcome_id(outputs: tuple[object, ...]) -> str | None:
    """Read one Registry-safe output route descriptor, when declared."""
    values = {
        value
        for output in outputs
        for descriptor in output.header.descriptor_labels
        if descriptor.name == "output_outcome_id"
        for value in descriptor.values
    }
    if not values:
        return None
    if len(values) != 1:
        raise RegisteredOperationAuthorityRequiredError(
            "operation products mix declared semantic outcomes")
    selected, = values
    if not isinstance(selected, str) or not selected:
        raise RegisteredOperationAuthorityRequiredError(
            "operation product outcome descriptor is invalid")
    return selected


def _task_model_call_limit_exceeded_type() -> type[BaseException]:
    from cpn.rpnh.registry.event_store import TaskModelCallLimitExceeded
    return TaskModelCallLimitExceeded


def _optional_workspace_runner(interruption_requested=None):
    from cpn.components.agent_loop import optional_execution
    if interruption_requested is None:
        return optional_execution.execute_bounded_workspace_tool

    def run(**kwargs):
        return optional_execution.execute_bounded_workspace_tool(
            **kwargs, interruption_requested=interruption_requested)

    return run


class DefaultAgentExecutor:
    """One default execution, including its optional same-firing continuation."""

    def __init__(self, *, execution, gateway, resources, host_context):
        from cpn.rpnh.llm_contracts import LLMInputPort
        if not isinstance(host_context._llm_input_port, LLMInputPort):
            raise RegisteredOperationAuthorityRequiredError(
                "default AgentLoop executor requires its configured LLM input port")
        if (host_context._registry is not gateway
                or host_context._resources is not resources
                or host_context._execution is not execution):
            raise RegisteredOperationAuthorityRequiredError(
                "default executor lost its exact Registry context")
        self._execution = execution
        # Configuration is declaration-owned data, not a field on the generic
        # operation-binding DTO or an interpretation of its opaque runtime ID.
        from cpn.rpnh.executable_net import load_compiled_net
        import json
        executable = host_context._executable_net
        if executable.net_ref != execution.operation.firing.net_ref:
            raise RegisteredOperationAuthorityRequiredError(
                "default executor declaration differs from its firing net")
        compiled = load_compiled_net(json.loads(executable.declaration.payload))
        transition = next(item for item in compiled.symbolic.transitions
                          if item.name == execution.operation.firing.transition_id)
        operation = next(item for item in compiled.operations
                         if item.declaration.name == transition.operation)
        if (operation.executor_key != execution.operation.spec.executor_key
                or operation.operation_id != execution.operation.spec.operation_id):
            raise RegisteredOperationAuthorityRequiredError(
                "default executor config differs from registered executor")
        self._native_node_config = operation.declaration.config
        self._registry = gateway
        self._resources = resources
        self._llm_input_port = host_context._llm_input_port
        self._resource_wait = None
        self._execution_block = None
        self._block_condition = host_context._block_condition
        self._block_bottom_error = host_context._block_bottom_error
        host_context.install_resource_resume(execution, self.resume_resource_grant)

    def _hold_resource_wait(
        self, execution: "OperationExecutionAuthority",
        wait: "AgentLoopResourceWait",
    ) -> RegisteredOperationResourceWait:
        from cpn.components.agent_loop.models import AgentLoopResourceWait, AgentLoopState

        if (execution is not self._execution
                or not isinstance(wait, AgentLoopResourceWait)
                or wait.loop.state != AgentLoopState.WAITING_RESOURCE):
            raise RegisteredOperationAuthorityRequiredError(
                "resource wait lacks the exact component wait state")
        context = self._loop_context(execution, wait.loop)
        held = RegisteredOperationResourceWait(execution, wait, context)
        self._resource_wait = held
        return held

    def _hold_execution_block(
            self, execution: "OperationExecutionAuthority", block: object,
            *, timing_observation: object | None = None,
    ) -> RegisteredOperationExecutionBlock:
        from cpn.components.agent_loop.models import AgentLoopExecutionBlock
        from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
        authority = (
            block.block_authority
            if isinstance(block, AgentLoopExecutionBlock) else block)
        timing = (
            block.timing_observation
            if isinstance(block, AgentLoopExecutionBlock)
            else timing_observation)
        if (execution is not self._execution
                or not isinstance(authority, OperationExecutionBlockAuthority)
                or authority.execution is not execution):
            raise RegisteredOperationAuthorityRequiredError(
                "execution block differs from the canonical execution")
        context = None
        if isinstance(block, AgentLoopExecutionBlock):
            if block.block_authority is not authority:
                raise RegisteredOperationAuthorityRequiredError(
                    "execution block lost its concrete component authority")
            context = self._loop_context(execution, block.loop)
        held = RegisteredOperationExecutionBlock(
            execution, authority,
            agent_loop_block=(
                block if isinstance(block, AgentLoopExecutionBlock) else None),
            timing_observation=timing, context=context)
        self._execution_block = held
        return held

    def _loop_context(self, execution, loop) -> RegisteredOperationContext:
        from cpn.components.agent_loop.models import AgentLoopSnapshot

        if execution is not self._execution or not isinstance(loop, AgentLoopSnapshot):
            raise RegisteredOperationAuthorityRequiredError(
                "component context requires exact execution and loop")
        context = RegisteredOperationContext(
            execution.operation.firing.transition_firing_ref,
            loop.invocation_ref,
            loop.operation_binding_ref,
            execution.operation_execution_lease_ref,
            execution.admission_head.writer_fencing_epoch)
        context.verify_execution(execution)
        return context

    def _interrupted_completion(self, execution, completion):
        """Close a requested interruption without publishing semantic output."""
        if not self._registry.operation_interruption_requested(execution):
            return None
        from cpn.rpnh.registry.operations import OperationExecutionResult
        return self._close_execution_result(
            execution,
            OperationExecutionResult(
                outputs=(), selected_outcome_id="interrupted"),
            timing_observation=getattr(completion, "timing_observation", None),
            selected_outcome_id="interrupted",
        )

    def _checkpoint_interruption(self, execution, completion):
        """Persist settled workspace state, then close the stop outcome."""

        try:
            self._finalize_workspace(execution, completion.loop)
        except Exception as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="workspace_interruption_checkpoint",
                idempotency_suffix="workspace-interruption-checkpoint")
        interrupted = self._interrupted_completion(execution, completion)
        if interrupted is None:
            return self._block_condition(
                execution,
                block_kind="framework_repair",
                error_code="interruption_checkpoint_without_owner_stop",
                error_message=None,
                boundary="owner_interruption",
                exact_error_ref=execution.operation_execution_lease_ref)
        return interrupted

    def _terminal_handoff(
            self, execution: "OperationExecutionAuthority",
            completion: object,
    ) -> RegisteredOperationTerminalHandoff:
        """Return one exact mechanical task-cap disposition to the harness."""
        from cpn.components.agent_loop.models import AgentTaskModelCallCapHandoffCompletion
        firing_ref = execution.operation.firing.transition_firing_ref
        if (not isinstance(
                    completion, AgentTaskModelCallCapHandoffCompletion)
                or execution is not self._execution
                or completion.agent_loop_role not in {
                    "actor", "critic", "finalization_reviewer"}
                or completion.transition_firing_ref != firing_ref
                or completion.loop.invocation_ref
                != execution.operation.canonical.context.invocation_ref
                or completion.loop.operation_binding_ref
                != execution.operation.operation_binding.operation_binding_ref):
            raise RegisteredOperationAuthorityRequiredError(
                "terminal handoff differs from its exact Registry firing")
        self._resource_wait = None
        return RegisteredOperationTerminalHandoff(
            execution, completion, self._loop_context(execution, completion.loop))

    def resume_resource_grant(
        self, exact_grant: object,
    ) -> (RegisteredOperationCompletion | RegisteredOperationResourceWait
          | RegisteredOperationExecutionBlock
          | RegisteredOperationTerminalHandoff):
        """Resume only the held loop/firing named by one P0 grant authority."""
        from cpn.components.agent_loop.models import (
            AgentCapReviewCompletion,
            AgentLoopCompletion,
            AgentLoopExecutionBlock,
            AgentLoopInterruptionCheckpoint,
            AgentLoopResourceWait,
            AgentTaskModelCallCapHandoffCompletion,
            VerifiedAgentLoopResourceGrant,
        )
        from cpn.rpnh.registry.resources import AgentLoopResourceGrantAuthority
        if not isinstance(exact_grant, AgentLoopResourceGrantAuthority):
            raise RegisteredOperationAuthorityRequiredError(
                "resource resume requires one typed P0-verified grant")
        held = self._resource_wait
        if held is None:
            raise RegisteredOperationAuthorityRequiredError(
                "resource grant has no held AgentLoop firing")
        execution = held.execution
        operation = execution.operation
        if (execution is not self._execution
                or exact_grant.transition_firing_ref
                != operation.firing.transition_firing_ref
                or exact_grant.invocation_ref
                != operation.canonical.context.invocation_ref
                or exact_grant.operation_execution_lease_ref
                != execution.operation_execution_lease_ref
                or exact_grant.operation_binding_ref
                != execution.operation.operation_binding.operation_binding_ref
                or exact_grant.writer_fencing_epoch
                != execution.admission_head.writer_fencing_epoch):
            raise RegisteredOperationAuthorityRequiredError(
                "resource grant differs from the held firing/invocation/operation")
        grant = VerifiedAgentLoopResourceGrant(
            registry_authority=exact_grant,
            transition_firing_ref=exact_grant.transition_firing_ref,
            invocation_ref=exact_grant.invocation_ref,
            operation_execution_lease_ref=(
                exact_grant.operation_execution_lease_ref),
            operation_binding_ref=exact_grant.operation_binding_ref,
            queue_entry_id=exact_grant.queue_entry_id,
            resource_ref=exact_grant.resource_ref,
            writer_fencing_epoch=exact_grant.writer_fencing_epoch,
            agent_loop_ref=exact_grant.agent_loop_ref,
            agent_turn_ref=exact_grant.agent_turn_ref,
            agent_action_ref=exact_grant.agent_action_ref,
            waiting_loop=held.wait.loop,
            waiting_turn=held.wait.turn,
            waiting_action=held.wait.action,
        )

        from cpn.components.agent_loop.service import (
            AgentLoopService,
            RegistryAgentLoopLLMPort,
        )
        from cpn.rpnh.registry.operations import OperationExecutionResult
        try:
            llm = RegistryAgentLoopLLMPort(
                self._registry, self._llm_input_port)
            completion = AgentLoopService(
                self._registry, llm=llm,
                tool_catalog=self._registry.current_agent_tool_catalog_v1(
                    execution),
                workspace_runner=_optional_workspace_runner(
                    lambda: self._registry
                    .operation_interruption_requested(execution)),
            ).resume_resource_grant(
                execution, grant,
                idempotency_key=(
                    "registered-operation-resource-grant:"
                    f"{grant.queue_entry_id}:"
                    f"{grant.writer_fencing_epoch}"),
            )
        except Exception as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="resource_grant_continuation",
                idempotency_suffix="resource-grant-continuation")
        if isinstance(completion, AgentLoopResourceWait):
            return self._hold_resource_wait(execution, completion)
        if isinstance(completion, AgentLoopExecutionBlock):
            return self._hold_execution_block(execution, completion)
        if isinstance(completion, AgentTaskModelCallCapHandoffCompletion):
            return self._terminal_handoff(execution, completion)
        if isinstance(completion, AgentLoopInterruptionCheckpoint):
            self._resource_wait = None
            return self._checkpoint_interruption(execution, completion)
        if isinstance(completion, AgentCapReviewCompletion):
            completion_resource_refs = (
                completion.cap_review_resource_ref,)
        elif isinstance(completion, AgentLoopCompletion):
            completion_resource_refs = completion.written_resource_refs
        else:
            return self._block_condition(
                execution,
                block_kind="framework_repair",
                error_code="resource_grant_return_contract_mismatch",
                error_message=None,
                boundary="resource_grant_continuation",
                exact_error_ref=execution.operation_execution_lease_ref)
        if self._registry.operation_interruption_requested(execution):
            self._resource_wait = None
            return self._checkpoint_interruption(execution, completion)
        if isinstance(completion, AgentLoopCompletion):
            try:
                self._finalize_workspace(execution, completion.loop)
            except Exception as exc:
                return self._block_bottom_error(
                    execution, exc, block_kind="framework_repair",
                    boundary="workspace_finalization",
                    idempotency_suffix="workspace-finalization")
        try:
            outputs = tuple(
                self._registry.verify_resource(
                    execution.operation.canonical, resource_ref)
                for resource_ref in completion_resource_refs)
        except Exception as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="resource_grant_output_verification",
                idempotency_suffix="resource-grant-output-verification")
        closed = self._close_execution_result(
            execution, OperationExecutionResult(outputs=outputs),
            timing_observation=completion.timing_observation,
            selected_outcome_id=_semantic_outcome_id(outputs))
        self._resource_wait = None
        return closed


    def execute(
        self, execution: OperationExecutionAuthority,
    ) -> object:
        """Execute the generic AgentLoop selected by HOST registration."""
        if execution is not self._execution:
            raise RegisteredOperationAuthorityRequiredError(
                "component execution differs from retained firing authority")
        from cpn.rpnh.registry.operations import (
            OperationExecutionAuthority,
            OperationExecutionResult,
        )
        try:
            operation_timing_origin_ns = time.monotonic_ns()
        except Exception:
            operation_timing_origin_ns = None
        identity = (
            f"registered-operation:{execution.operation.canonical.context.invocation_ref.version_id}:"
            f"{execution.operation.firing.transition_firing_ref.version_id}:"
            f"{execution.operation.spec.operation_spec_ref.version_id}"
        )
        task_model_call_limit_exceeded = (
            _task_model_call_limit_exceeded_type())
        from cpn.components.agent_loop.models import (
            AgentCapReviewCompletion,
            AgentLoopCompletion,
            AgentLoopExecutionBlock,
            AgentLoopInterruptionCheckpoint,
            AgentLoopLocalTurnLimitExhausted,
            AgentLoopResourceWait,
            AgentTaskModelCallCapHandoffCompletion,
            WorkspaceRevisionConflict,
        )
        from cpn.components.external_kb import ExternalKBFrameworkFault
        from cpn.components.tool_executors import RuntimeEnvironmentFrameworkFault
        try:
            from cpn.components.agent_loop.service import (
                AgentLoopService, RegistryAgentLoopLLMPort,
            )
            llm = RegistryAgentLoopLLMPort(
                self._registry, self._llm_input_port,
                timing_origin_ns=operation_timing_origin_ns)
            completion = AgentLoopService(
                self._registry, llm=llm,
                tool_catalog=self._registry.current_agent_tool_catalog_v1(
                    execution),
                timing_origin_ns=operation_timing_origin_ns,
                workspace_runner=_optional_workspace_runner(
                    lambda: self._registry
                    .operation_interruption_requested(execution)),
            ).run(
                execution,
                idempotency_key=f"{identity}:agent-loop-v2",
            )
        except AttributeError as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="agent_loop_contract",
                idempotency_suffix="agent-loop-attribute-error")
        except task_model_call_limit_exceeded:
            raise
        except AgentLoopLocalTurnLimitExhausted as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="agent_loop_turn_limit",
                idempotency_suffix="agent-loop-turn-limit")
        except WorkspaceRevisionConflict as exc:
            _LOGGER.warning(
                "workspace_revision_conflict transition=%s evidence=%s paths=%s",
                execution.operation.firing.transition_id,
                exc.conflict_ref.version_id, ",".join(exc.paths))
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="workspace_revision",
                idempotency_suffix="workspace-conflict")
        except (ExternalKBFrameworkFault,
                RuntimeEnvironmentFrameworkFault) as exc:
            _LOGGER.error(
                "registered_agent_tool_framework_fault "
                "transition=%s type=%s detail=%s",
                execution.operation.firing.transition_id,
                type(exc).__name__, str(exc), exc_info=True)
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="agent_tool_framework",
                idempotency_suffix="agent-tool-framework")
        except Exception as exc:
            _LOGGER.error(
                "registered_agent_loop_failed transition=%s type=%s detail=%s diagnostic=%s",
                execution.operation.firing.transition_id,
                type(exc).__name__, str(exc), str(exc))
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="agent_loop_execution",
                idempotency_suffix="agent-loop-error")
        if isinstance(completion, AgentLoopResourceWait):
            return self._hold_resource_wait(execution, completion)
        if isinstance(completion, AgentLoopExecutionBlock):
            return self._hold_execution_block(execution, completion)
        if isinstance(completion, AgentTaskModelCallCapHandoffCompletion):
            return self._terminal_handoff(execution, completion)
        if isinstance(completion, AgentLoopInterruptionCheckpoint):
            return self._checkpoint_interruption(execution, completion)
        if isinstance(completion, AgentCapReviewCompletion):
            completion_resource_refs = (completion.cap_review_resource_ref,)
        elif isinstance(completion, AgentLoopCompletion):
            completion_resource_refs = completion.written_resource_refs
        else:
            return self._block_condition(
                execution,
                block_kind="framework_repair",
                error_code="agent_loop_return_contract_mismatch",
                error_message=None,
                boundary="agent_loop_completion",
                exact_error_ref=execution.operation_execution_lease_ref)
        if self._registry.operation_interruption_requested(execution):
            return self._checkpoint_interruption(execution, completion)
        if isinstance(completion, AgentLoopCompletion):
            try:
                self._finalize_workspace(execution, completion.loop)
            except Exception as exc:
                return self._block_bottom_error(
                    execution, exc, block_kind="framework_repair",
                    boundary="workspace_finalization",
                    idempotency_suffix="workspace-finalization")
        try:
            outputs = tuple(
                self._registry.verify_resource(
                    execution.operation.canonical, resource_ref)
                for resource_ref in completion_resource_refs)
        except Exception as exc:
            return self._block_bottom_error(
                execution, exc, block_kind="framework_repair",
                boundary="agent_loop_output_verification",
                idempotency_suffix="agent-loop-output-verification")
        return self._close_execution_result(
            execution, OperationExecutionResult(outputs=outputs),
            timing_observation=completion.timing_observation,
            selected_outcome_id=_semantic_outcome_id(outputs))

    def _finalize_workspace(self, execution, loop) -> None:
        finalizer = getattr(
            self._registry, "finalize_agent_workspace_v1", None)
        if finalizer is None:
            return
        key = (
            "registered-operation-workspace-finalization:"
            f"{execution.operation.firing.transition_firing_ref.version_id}")
        try:
            finalizer(execution, loop, idempotency_key=key)
        except sqlite3.OperationalError as exc:
            error_code = getattr(exc, "sqlite_errorcode", None)
            is_io_error = (
                (isinstance(error_code, int)
                 and error_code & 0xff == sqlite3.SQLITE_IOERR)
                or str(exc).lower() == "disk i/o error")
            if not is_io_error:
                raise
            _LOGGER.warning(
                "workspace_finalization_retry transition=%s error_code=%s",
                execution.operation.firing.transition_id,
                type(exc).__name__,
            )
            # Workspace publication uses stable per-firing idempotency keys.
            # One immediate retry can therefore recover a transient Registry
            # read/write I/O fault without replaying the AgentLoop or provider.
            finalizer(execution, loop, idempotency_key=key)

    def _close_execution_result(
        self, authority, result, *, generic_critic_selection=None,
        timing_observation=None, selected_outcome_id=None,
    ) -> RegisteredOperationCompletion:
        """Return ordinary products; never settle or register operation outputs."""
        if authority is not self._execution:
            raise RegisteredOperationAuthorityRequiredError(
                "component completion differs from retained execution")
        return RegisteredOperationCompletion(
            authority, result,
            generic_critic_selection=generic_critic_selection,
            timing_observation=timing_observation,
            selected_outcome_id=selected_outcome_id)


__all__ = ("DefaultAgentExecutor",)
