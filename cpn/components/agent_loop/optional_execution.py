"""Compatibility and owner-side coordination façade for optional AgentLoop execution."""

from __future__ import annotations

from cpn.components.tool_executors import execute_bounded_workspace_tool
from cpn.rpnh.registry.agent_resource_broker import (
    prepare_agent_resource_request,
)
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.operation_execution import verify_operation_execution

from .mechanical_lifecycle import AgentLoopMechanicalLifecycle
from .action_execution import (
    EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
    OPTIONAL_TOOL_BINDINGS, ActionExecutionMixin,
    OptionalAgentCapabilityUnavailable,
)
from .compaction import CompactionExecutionMixin
from .context import ContextExecutionMixin, optional_agent_loop_schema_data
from .delegation import DelegationExecutionMixin
from .loop_state import LoopStateExecutionMixin, loop_document, loop_ref
from .ports import AgentLoopRegistryPort
from .resource_wait import ResourceWaitExecutionMixin
from .turn_execution import TurnExecutionMixin
from .turn_records import TurnRecordsExecutionMixin
from .workspace import WorkspaceExecutionMixin


class OptionalAgentLoopRegistryService(ContextExecutionMixin, LoopStateExecutionMixin, TurnExecutionMixin, TurnRecordsExecutionMixin, ActionExecutionMixin, WorkspaceExecutionMixin, CompactionExecutionMixin, DelegationExecutionMixin, ResourceWaitExecutionMixin):
    """Sole owner-side gateway; feature bodies live in functional modules."""

    def __init__(self, *, owner, kernel, repository, provider_attempts, invoke_tool):
        self.owner, self.core = owner, owner._core
        self.kernel, self.repository = kernel, repository
        self.ledger, self.invoke_tool = provider_attempts, invoke_tool
        self.mechanical_lifecycle = AgentLoopMechanicalLifecycle(
            self.core, self.kernel)

    def agent_loop_mechanical_lifecycle_v1(self):
        """Expose the one shared mechanical implementation to composition checks."""
        return self.mechanical_lifecycle

    def gateway_methods(self):
        methods = {name: getattr(self, name) for name, member in vars(AgentLoopRegistryPort).items()
                   if not name.startswith("_") and callable(member)}
        methods["current_agent_tool_catalog_v1"] = self.current_agent_tool_catalog_v1
        methods["finalize_agent_workspace_v1"] = (
            self.finalize_agent_workspace_v1)
        methods["interrupt_agent_turn_actions_v1"] = (
            self.interrupt_agent_turn_actions_v1)
        methods["record_llm_input_owner_interruption_v1"] = (
            self.record_llm_input_owner_interruption_v1)
        # Exact immutable linkage for the parent's normal input port's existing
        # materialization/dispatch/permit hooks. This is hydration, not routing.
        methods["provider_attempt_for_agent_llm_v1"] = self.provider_attempt_for_agent_llm_v1
        return methods

    def _execution(self, execution, loop=None):
        execution = verify_operation_execution(self.core, self.kernel, self.repository, execution)
        if loop is not None:
            self._current(loop)
            if execution.operation.canonical.context != self._context(loop):
                raise ResourceIntegrityFault("optional action crossed its exact execution")
        return execution

    def _unsupported(self, name):
        raise OptionalAgentCapabilityUnavailable("Optional capability not declared/implemented: " + name)

    def begin_monitored_workspace_action_v1(self, loop, turn, action, *, permitted_tool_names, idempotency_key, execution):
        return self._unsupported("monitored_workspace")

    def finish_monitored_workspace_action_v1(self, loop, turn, action, pending_action, *, permitted_tool_names,
            idempotency_key, execution, timing_evidence=None):
        return self._unsupported("monitored_workspace")


__all__ = (
    "OptionalAgentLoopRegistryService", "OptionalAgentCapabilityUnavailable",
    "OPTIONAL_TOOL_BINDINGS", "optional_agent_loop_schema_data",
    "EXECUTION_PROVENANCE_SCHEMA",
)
