"""Pure materialization of AgentLoop LLM request envelopes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def materialize_agent_request_envelope(
        *, model_condition: str, max_output_tokens: int,
        system_content: str, prompt_messages: Sequence[Mapping[str, Any]],
        history_messages: Sequence[Mapping[str, Any]],
        tool_descriptors: Sequence[Mapping[str, Any]],
        source_prompt_ref: Mapping[str, Any],
        tool_catalog_ref: Mapping[str, Any],
        checkpoint_prompt: str | None = None,
        owner_reentry_prompt: str | None = None,
        owner_messages: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one envelope from values already materialized by the caller."""
    if any(not isinstance(message, str) or not message.strip()
           for message in owner_messages):
        raise ValueError("owner messages must be non-empty text")
    messages = [
        {"role": "system", "content": system_content},
        *prompt_messages,
        *history_messages,
    ]
    if owner_reentry_prompt is not None:
        messages.append({"role": "system", "content": owner_reentry_prompt})
    messages.extend(
        {"role": "user", "content": message}
        for message in owner_messages)
    # A framework checkpoint instruction must remain the terminal instruction:
    # its response protocol deliberately differs from an ordinary agent turn.
    if checkpoint_prompt is not None:
        messages.append({"role": "system", "content": checkpoint_prompt})
    return {
        "protocol": "llm_request_envelope/v1",
        "model_condition": model_condition,
        "max_output_tokens": max_output_tokens,
        "messages": messages,
        "tools": [
            {"type": "function", "function": {
                "name": item["name"],
                "description": item["description"],
                "parameters": item["arguments"],
            }}
            for item in tool_descriptors
        ],
        "tool_choice": "auto",
        "source_prompt_ref": source_prompt_ref,
        "tool_catalog_ref": tool_catalog_ref,
        "placeholders": [],
    }
