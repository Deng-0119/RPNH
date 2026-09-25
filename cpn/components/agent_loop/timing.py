"""Process-local AgentLoop timing evidence.

Timing is observational only: failures to sample never affect execution,
admission, settlement, or terminal state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef


@dataclass(slots=True)
class AgentActionTimingEvidence:
    """Process-local phase offsets for one LLM-observed action."""

    action_id: str | None
    tool_call_ordinal: int
    tool_name: str | None
    settlement: str | None = None
    validation_prevented_dispatch: bool = False
    tool_validation_start: int | None = None
    tool_validation_finish: int | None = None
    tool_execution_start: int | None = None
    tool_execution_finish: int | None = None
    action_object_prewrite_start: int | None = None
    action_object_prewrite_finish: int | None = None
    workspace_projection_start: int | None = None
    workspace_projection_finish: int | None = None


@dataclass(slots=True)
class AgentTurnTimingEvidence:
    """Process-local phase offsets for one committed LLM turn."""

    agent_loop_id: str
    turn_sequence: int
    agent_turn_id: str | None
    llm_invocation_attempt_ref: VersionRef | None
    timing_origin_ns: int | None = field(
        default=None, repr=False, compare=False)
    expected_committed_action_count: int = 0
    llm_attempt_reservation_return: int | None = None
    response_registration_start: int | None = None
    response_registration_return: int | None = None
    agent_turn_commit_return: int | None = None
    action_batch_commit_start: int | None = None
    action_batch_commit_return: int | None = None
    action_settlement_return: int | None = None
    actions: list[AgentActionTimingEvidence] = field(default_factory=list)


@dataclass(slots=True)
class AgentOperationTimingEvidence:
    """Evidence-only operation timing retained solely in process memory."""

    operation_execution_ref: VersionRef
    operation_start_event_id: TypedId
    timing_origin_ns: int = field(repr=False, compare=False)
    agent_loop_id: str | None = None
    operation_start_return: int = 0
    operation_finish_return: int | None = None
    expected_committed_turn_count: int = 0
    expected_committed_action_count: int = 0
    turns: list[AgentTurnTimingEvidence] = field(default_factory=list)


def record_timing_offset(
        evidence: object | None, attribute: str,
        timing_origin_ns: int | None) -> None:
    """Best-effort monotonic observation that can never affect semantics."""
    try:
        if (evidence is None or isinstance(timing_origin_ns, bool)
                or not isinstance(timing_origin_ns, int)
                or timing_origin_ns < 0):
            return
        offset = time.monotonic_ns() - timing_origin_ns
        if offset < 0:
            return
        setattr(evidence, attribute, offset)
    except Exception:
        return


__all__ = [
    "AgentActionTimingEvidence",
    "AgentOperationTimingEvidence",
    "AgentTurnTimingEvidence",
    "record_timing_offset",
]
