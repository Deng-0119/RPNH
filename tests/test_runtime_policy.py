from __future__ import annotations

import pytest

from cpn.components.default_agent_executor import _agent_context_policy
from cpn.rpnh.runtime_policy import (
    RuntimePolicy, WorkspacePolicy, runtime_policy_from_document,
)


def test_runtime_policy_round_trip_and_agent_context_consumption() -> None:
    policy = RuntimePolicy(
        max_turns_per_node=6,
        max_parallel_nodes=2,
        main_history_message_limit=10,
        context_pressure_trigger_ratio=0.72,
        context_tool_output_byte_limit=4096,
        workspace=WorkspacePolicy(
            timeout_seconds=60,
            memory_bytes=1073741824,
            process_limit=16,
            source_size_bytes=2097152,
            input_size_bytes=4194304,
        ),
    )
    assert runtime_policy_from_document(policy.as_document()) == policy

    class Port:
        execution_policy = {"runtime": policy.as_document()}

    reduction, ratio = _agent_context_policy(Port())
    assert ratio == 0.72
    assert reduction.tool_output_byte_limit == 4096
    assert reduction.retained_history_token_limit == 20000


def test_runtime_policy_defaults_and_invalid_shapes_fail_loud() -> None:
    assert runtime_policy_from_document(None) == RuntimePolicy()
    with pytest.raises(ValueError, match="fields are not current"):
        runtime_policy_from_document({"max_turns_per_node": 1})
    with pytest.raises(ValueError, match="between zero and one"):
        RuntimePolicy(context_pressure_trigger_ratio=1.0)
    with pytest.raises(ValueError, match="at least 128"):
        RuntimePolicy(context_tool_output_byte_limit=127)


def test_runtime_policy_accepts_explicit_unmetered_agent_turns() -> None:
    policy = RuntimePolicy(max_turns_per_node=None)

    assert policy.as_document()["max_turns_per_node"] is None
    assert runtime_policy_from_document(policy.as_document()) == policy
