"""Flat compatibility Protocols for the AgentLoop boundary.

The method sets intentionally remain declared directly on each Protocol because
existing gateways enumerate ``vars(protocol)``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


def _bind_protocol_annotation_types(namespace: dict[str, object]) -> None:
    """Install the canonical runtime types after service definitions exist."""
    globals().update(namespace)


@runtime_checkable
class AgentLoopLLMPort(Protocol):
    def request_agent_turn_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, idempotency_key: str,
    ) -> CompletedAgentLLMInvocation: ...

    def compact_agent_context_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, trigger_reason: str, force: bool,
        idempotency_key: str,
    ) -> CompletedAgentContextCompaction: ...

@runtime_checkable
class AgentLoopRegistryPort(Protocol):
    """Shared Registry methods consumed by the current AgentLoop boundary.

    Implementations must commit each method transactionally, enforce optimistic
    revision and exact-head checks, and return rehydrated committed records.
    """

    def start_agent_loop_v1(
        self, command: StartAgentLoopCommand,
    ) -> AgentLoopSnapshot: ...

    def prepare_agent_loop_start_v1(
        self, execution: object, catalog: AgentToolCatalog, *,
        idempotency_key: str,
    ) -> StartAgentLoopCommand: ...

    def prepare_agent_llm_turn_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, initialization: AgentSystemInitialization,
        *, idempotency_key: str,
        prepared_context: PreparedAgentTurnContext | None = None,
    ) -> PreparedAgentLLMTurn: ...

    def prepare_agent_turn_context_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, tool_output_byte_limit: int = 10_000,
    ) -> PreparedAgentTurnContext: ...

    def prepared_agent_turn_context_scope_v1(
        self, prepared_context: PreparedAgentTurnContext,
    ): ...

    def agent_context_pressure_requires_compaction_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog,
        prepared_context: PreparedAgentTurnContext, *,
        pressure_policy: ContextPressurePolicy,
    ) -> bool: ...

    def prepare_agent_context_compaction_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, trigger_reason: str, force: bool,
        reduction_settings: ContextReductionSettings,
        pressure_policy: ContextPressurePolicy | None,
        compaction_id: str | None, idempotency_key: str,
    ) -> PreparedAgentContextCompaction | CompletedAgentContextCompaction: ...

    def complete_agent_context_compaction_v1(
        self, prepared: PreparedAgentContextCompaction,
        response_bytes: bytes, *, idempotency_key: str,
    ) -> CompletedAgentContextCompaction: ...

    def record_agent_context_compaction_failure_v1(
        self, prepared: PreparedAgentContextCompaction,
        disposition: str, *, idempotency_key: str,
        failure_code: str | None = None,
        submission_state: str | None = None,
    ) -> tuple[bool, str]: ...

    def prepare_agent_system_initialization_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog,
    ) -> AgentSystemInitialization: ...

    def record_agent_llm_turn_v1(
        self, loop: AgentLoopSnapshot,
        attempt: LLMCallAttempt, response_bytes: bytes,
        *, idempotency_key: str,
    ) -> tuple[
        AgentLoopSnapshot,
        AgentTurnRecord | AgentLengthInterruptionRecord,
    ]: ...

    def prepare_agent_turn_actions_v1(
        self, loop: AgentLoopSnapshot, turn: AgentTurnRecord,
        *, timing_origin_ns: int | None = None,
    ) -> tuple[object, ...]: ...

    def prepare_workspace_action_execution_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        turn: AgentTurnRecord, action: object,
    ) -> Mapping[str, Any]: ...

    def prepare_parent_owned_delegated_subtask_call_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        turn: AgentTurnRecord, action: object, *,
        parent_tool_catalog: AgentToolCatalog, idempotency_key: str,
        parent_settlement_idempotency_key: str,
        leaf_turn_sequence: int = 0,
        prior_leaf_steps: tuple[ParentOwnedDelegatedSubtaskToolStep, ...] = (),
        history_messages: tuple[Mapping[str, Any], ...] = (),
    ) -> PreparedParentOwnedDelegatedSubtaskCall: ...

    def settle_parent_owned_delegated_subtask_tool_step_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        turn: AgentTurnRecord, action: object,
        prepared: PreparedParentOwnedDelegatedSubtaskCall,
        response_bytes: bytes, *,
        prior_leaf_steps: tuple[ParentOwnedDelegatedSubtaskToolStep, ...],
        idempotency_key: str,
    ) -> (ParentOwnedDelegatedSubtaskToolStep
          | ParentOwnedDelegatedSubtaskLengthReplay
          | ParentOwnedDelegatedSubtaskResult): ...

    def hydrate_current_agent_turn_v1(
        self, loop: AgentLoopSnapshot,
    ) -> AgentTurnRecord: ...

    def record_llm_invocation_failure_v1(
        self, loop: AgentLoopSnapshot, attempt: LLMCallAttempt,
        disposition: str, *,
        idempotency_key: str, failure_code: str | None = None,
        submission_state: str | None = None,
    ) -> bool: ...

    def register_operation_execution_block(
        self, execution: object, *, block_kind: str,
        operation_or_tool_identity: str,
        error_code: str, error_message: str | None, boundary: str,
        consecutive_count: int,
        exact_error_ref: VersionRef,
        retry_not_before_utc: str | None,
    ) -> OperationExecutionBlockAuthority: ...

    def mark_agent_loop_waiting_v1(
        self, loop: AgentLoopSnapshot, *, expected_revision: int,
        idempotency_key: str,
    ) -> AgentLoopSnapshot: ...

    def handoff_agent_llm_turn_cap_v1(
        self, execution: object, loop: AgentLoopSnapshot, *,
        idempotency_key: str,
    ) -> AgentLoopSnapshot | None: ...

    def handoff_agent_task_model_call_cap_v1(
        self, execution: object, loop: AgentLoopSnapshot, *,
        idempotency_key: str,
    ) -> AgentLoopSnapshot: ...

    def task_model_call_handoff_required_v1(
        self, execution: object,
    ) -> bool: ...

    def agent_loop_segment_role_v1(
        self, execution: object, loop: AgentLoopSnapshot,
    ) -> str: ...

    def continue_agent_loop_role_segment_v1(
        self, execution: object, loop: AgentLoopSnapshot, *,
        idempotency_key: str,
    ) -> AgentLoopSnapshot: ...

    def continue_after_text_turn_v1(
        self, loop: AgentLoopSnapshot, turn: AgentTurnRecord, *,
        expected_revision: int, idempotency_key: str,
    ) -> AgentLoopSnapshot: ...

    def continue_after_incomplete_length_turn_v1(
        self, loop: AgentLoopSnapshot,
        turn: AgentLengthInterruptionRecord, *,
        expected_revision: int, idempotency_key: str,
    ) -> AgentLoopSnapshot: ...

    def settle_agent_turn_actions_v1(
        self, loop: AgentLoopSnapshot, turn: AgentTurnRecord,
        actions: tuple[object, ...], *, permitted_tool_names: tuple[str, ...],
        idempotency_key: str, execution: object | None = None,
        timing_evidence: AgentTurnTimingEvidence | None = None,
        delegated_subtask_results: Mapping[
            str, ParentOwnedDelegatedSubtaskResult] | None = None,
        external_tool_results: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> object: ...

    def begin_monitored_workspace_action_v1(
        self, loop: AgentLoopSnapshot, turn: AgentTurnRecord,
        action: object, *, permitted_tool_names: tuple[str, ...],
        idempotency_key: str, execution: object,
    ) -> tuple[AgentLoopSnapshot, AgentActionRecord]: ...

    def finish_monitored_workspace_action_v1(
        self, loop: AgentLoopSnapshot, turn: AgentTurnRecord,
        action: object, pending_action: AgentActionRecord, *,
        permitted_tool_names: tuple[str, ...], idempotency_key: str,
        execution: object,
        timing_evidence: AgentTurnTimingEvidence | None = None,
    ) -> object: ...

    def hydrate_agent_loop_v1(self, loop_ref: VersionRef) -> AgentLoopSnapshot: ...
    def consume_agent_loop_resource_grant_v1(
        self, execution: object, registry_authority: object, *,
        waiting_loop: AgentLoopSnapshot,
        waiting_turn: AgentTurnRecord,
        waiting_action: AgentActionRecord,
        catalog: AgentToolCatalog,
        idempotency_key: str,
    ) -> AgentLoopSnapshot: ...



__all__ = ["AgentLoopLLMPort", "AgentLoopRegistryPort"]
