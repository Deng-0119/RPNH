"""Mechanical recovery validation for committed agent-loop records."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AgentActionRecord,
    AgentContextCompaction,
    AgentLoopProtocolError,
    AgentLoopSnapshot,
    AgentLoopState,
    AgentTurnRecord,
    stable_action_id,
)


@dataclass(frozen=True, slots=True)
class AgentLoopRecoveryBundle:
    loop: AgentLoopSnapshot
    turns: tuple[AgentTurnRecord, ...]
    actions: tuple[AgentActionRecord, ...]
    compactions: tuple[AgentContextCompaction, ...]


@dataclass(frozen=True, slots=True)
class RecoveredAgentLoop:
    loop: AgentLoopSnapshot
    turns: tuple[AgentTurnRecord, ...]
    actions: tuple[AgentActionRecord, ...]
    compactions: tuple[AgentContextCompaction, ...]


def recover_agent_loop(bundle: AgentLoopRecoveryBundle) -> RecoveredAgentLoop:
    """Rehydrate committed lineage; never infer or repair missing records."""
    if not isinstance(bundle, AgentLoopRecoveryBundle):
        raise TypeError("agent-loop recovery requires a committed bundle")
    loop = bundle.loop
    if not isinstance(loop, AgentLoopSnapshot):
        raise TypeError("agent-loop recovery bundle lacks a loop snapshot")
    turns = tuple(bundle.turns)
    actions = tuple(bundle.actions)
    compactions = tuple(bundle.compactions)
    if any(turn.loop_id != loop.loop_id for turn in turns):
        raise AgentLoopProtocolError("recovered turn belongs to another loop")
    sequences = tuple(turn.sequence for turn in turns)
    if sequences != tuple(range(len(turns))):
        raise AgentLoopProtocolError("recovered turns are not contiguous")
    if len(turns) != loop.llm_turns_used:
        raise AgentLoopProtocolError("recovered turn usage differs from loop")
    seen_actions: set[str] = set()
    action_positions: set[tuple[int, int]] = set()
    for action in actions:
        if action.loop_id != loop.loop_id or action.turn_sequence >= len(turns):
            raise AgentLoopProtocolError("recovered action has no exact turn")
        if action.action_identity_kind == "tool_call_id":
            if action.action_id != stable_action_id(
                    loop.loop_id, action.turn_sequence,
                    action.action_identity_key):
                raise AgentLoopProtocolError("recovered action id is unstable")
        if action.action_id in seen_actions:
            raise AgentLoopProtocolError("recovered action is duplicated")
        seen_actions.add(action.action_id)
        position = (action.turn_sequence, action.tool_call_ordinal)
        if position in action_positions:
            raise AgentLoopProtocolError(
                "recovered actions duplicate one response position")
        action_positions.add(position)
        facts = turns[action.turn_sequence].tool_calls
        if (action.tool_call_ordinal >= len(facts)
                or facts[action.tool_call_ordinal].tool_call_id
                != action.tool_call_id
                or facts[action.tool_call_ordinal].action_identity_kind
                != action.action_identity_kind
                or facts[action.tool_call_ordinal].action_identity_key
                != action.action_identity_key
                or facts[action.tool_call_ordinal].tool_name != action.tool_name
                or facts[action.tool_call_ordinal].raw_arguments
                != action.raw_arguments):
            raise AgentLoopProtocolError(
                "recovered action differs from its response-ref structural facts")
    for compaction in compactions:
        if (compaction.loop_id != loop.loop_id
                or compaction.last_turn_sequence >= len(turns)):
            raise AgentLoopProtocolError(
                "recovered compaction has no contiguous turn lineage")
        exact = turns[
            compaction.first_turn_sequence:compaction.last_turn_sequence + 1]
        if len(exact) != len(compaction.covered_turn_refs):
            raise AgentLoopProtocolError(
                "recovered compaction coverage differs from turns")
    if loop.state == AgentLoopState.CANDIDATE_ADOPTED:
        if loop.adopted_candidate_ref is None:
            raise AgentLoopProtocolError("adopted state lacks exact candidate")
    if loop.state == AgentLoopState.DISPOSITION_RECORDED:
        if loop.disposition_ref is None:
            raise AgentLoopProtocolError("disposition state lacks exact record")
    return RecoveredAgentLoop(loop, turns, actions, compactions)


__all__ = [
    "AgentLoopRecoveryBundle",
    "RecoveredAgentLoop",
    "recover_agent_loop",
]
