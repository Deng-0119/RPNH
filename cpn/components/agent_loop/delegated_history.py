"""Pure delegated child-session history transformations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .compact import ContextReductionSettings, reduce_tool_messages


def compact_delegated_subtask_history_after_length(
        history_messages: tuple[Mapping[str, Any], ...], *,
        reduction_settings: ContextReductionSettings | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Replace only process-local child history before replaying one turn."""

    if (not isinstance(history_messages, tuple)
            or any(not isinstance(message, Mapping)
                   for message in history_messages)):
        raise TypeError("delegated length compaction history is invalid")
    settings = reduction_settings or ContextReductionSettings()
    reduced = reduce_tool_messages(
        history_messages, byte_limit=settings.tool_output_byte_limit)
    return (*reduced, {
        "role": "system",
        "content": (
            "The preceding child-session response ended because of the response "
            "length limit. That incomplete response was rolled back. Continue "
            "the same atomic task and replay the same local turn; do not assume "
            "that any tool action from the incomplete response executed."),
    })


# Keep the established public import's introspection and pickle identity while
# service re-exports this exact function object.
compact_delegated_subtask_history_after_length.__module__ = (
    "cpn.components.agent_loop.service")
