"""Pure materialization of AgentLoop LLM request envelopes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("ambiguous duplicate JSON object key")
        value[key] = item
    return value


def _json_number_tokens(content: str) -> Any:
    # JSON arrays decode as lists, so tuples distinguish number lexemes from
    # every JSON value (including numeric-looking strings). Dict comparison
    # ignores presentation key order while preserving each number's location.
    return json.loads(content, parse_int=lambda token: (token,),
                      parse_float=lambda token: (token,))


def _readable_tool_message(message: Mapping[str, Any]) -> Mapping[str, Any]:
    """Present complete bounded tool objects without changing caller history."""
    content = message.get("content")
    if message.get("role") != "tool" or not isinstance(content, str):
        return message
    try:
        value = json.loads(content, object_pairs_hook=_unique_json_object)
        if not isinstance(value, dict):
            return message
        readable = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
        # Float conversion may round or underflow, and dumping can normalize
        # exponents or negative zero. Only accept identical numeric lexemes at
        # the same structural positions; otherwise retain the original bytes.
        if _json_number_tokens(content) != _json_number_tokens(readable):
            return message
        # Both limits already ran in the callers. Account for inner UTF-8 and
        # the ASCII canonical outer envelope's escaping before making a copy.
        if (len(readable.encode("utf-8")) > len(content.encode("utf-8"))
                or len(json.dumps(readable, ensure_ascii=True))
                > len(json.dumps(content, ensure_ascii=True))):
            return message
    except (ValueError, UnicodeError, RecursionError):
        # Plain/truncated text, ambiguous objects and unencodable Unicode keep
        # their original representation. Never parse strings inside the object.
        return message
    if readable == content:
        return message
    return dict(message, content=readable)


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
        *(_readable_tool_message(message) for message in history_messages),
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

