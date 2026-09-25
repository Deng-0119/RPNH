from __future__ import annotations

import json

import pytest

from cpn.components.agent_loop.compact import (
    ContextReductionSettings,
    approximate_tokens,
    build_replacement_history,
)
from cpn.components.agent_loop.delegated_history import (
    compact_delegated_subtask_history_after_length as extracted_compaction,
)
from cpn.components.agent_loop.service import (
    compact_delegated_subtask_history_after_length,
)


FINAL_MESSAGE = {
    "role": "system",
    "content": (
        "The preceding child-session response ended because of the response "
        "length limit. That incomplete response was rolled back. Continue "
        "the same atomic task and replay the same local turn; do not assume "
        "that any tool action from the incomplete response executed."),
}


def test_service_reexports_the_extracted_delegated_history_function() -> None:
    assert compact_delegated_subtask_history_after_length is extracted_compaction
    assert compact_delegated_subtask_history_after_length.__module__ == (
        "cpn.components.agent_loop.service")


def test_delegated_length_history_reduces_tool_output_before_final_message() -> None:
    history = (
        {"role": "assistant", "content": "I will inspect the resource."},
        {"role": "tool", "tool_call_id": "read-1", "content": "x" * 256},
    )

    compacted = compact_delegated_subtask_history_after_length(
        history,
        reduction_settings=ContextReductionSettings(tool_output_byte_limit=128),
    )

    assert isinstance(compacted, tuple)
    assert compacted[0] == history[0]
    assert compacted[1]["role"] == "tool"
    assert compacted[1]["tool_call_id"] == "read-1"
    assert len(compacted[1]["content"].encode("utf-8")) <= 128
    assert "UTF-8 bytes omitted" in compacted[1]["content"]
    assert compacted[-1] == FINAL_MESSAGE


def test_delegated_length_history_uses_default_reduction_settings() -> None:
    compacted = compact_delegated_subtask_history_after_length((
        {"role": "tool", "tool_call_id": "read-1", "content": "x" * 10_001},
    ))

    assert len(compacted[0]["content"].encode("utf-8")) <= 10_000
    assert "UTF-8 bytes omitted" in compacted[0]["content"]
    assert compacted[-1] == FINAL_MESSAGE


def test_forty_turn_history_is_fully_summarized_with_one_recent_tail() -> None:
    history = tuple({
        "role": "assistant",
        "content": f"turn-{index:02d}-" + "x" * 100,
    } for index in range(40))
    one_group_tokens = approximate_tokens(json.dumps(
        (history[-1],), ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":")))

    replacement = build_replacement_history(
        history, "One summary covers the complete forty-turn prefix.",
        retained_history_token_limit=one_group_tokens * 5,
        fact_capsule={
            "kind": "agent_context_fact_capsule",
            "schema_version": "agent_context_fact_capsule/v1",
            "source_context_session_ordinal": 0,
            "context_session_ordinal": 1,
        },
    )

    assert replacement[0]["kind"] == "agent_context_fact_capsule"
    assert replacement[1] == {
        "kind": "compaction_summary",
        "content": "One summary covers the complete forty-turn prefix.",
    }
    retained = tuple(entry["message"] for entry in replacement[2:])
    assert retained == history[-5:]
    assert len(history) == 40


@pytest.mark.parametrize("history", [[], ("not-a-mapping",)])
def test_delegated_length_history_rejects_invalid_history(history: object) -> None:
    with pytest.raises(
            TypeError, match="^delegated length compaction history is invalid$"):
        compact_delegated_subtask_history_after_length(history)  # type: ignore[arg-type]
