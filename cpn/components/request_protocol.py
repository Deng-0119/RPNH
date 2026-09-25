"""Optional LLM component validation for registered authored request prompts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Mapping


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_llm_request_message_history(
        messages: Sequence[Mapping[str, object]],
) -> None:
    """Reject only broken assistant/tool-call closure before submission."""

    if not isinstance(messages, Sequence):
        raise ValueError("LLM request history must be a message sequence")
    index = 0
    while index < len(messages):
        message = messages[index]
        if not isinstance(message, Mapping):
            raise ValueError("LLM request history contains a non-object message")
        role = message.get("role")
        if role == "tool":
            raise ValueError("LLM request history contains an orphan tool result")
        calls = message.get("tool_calls") if role == "assistant" else None
        if calls is None:
            index += 1
            continue
        if calls == []:
            index += 1
            continue
        if not isinstance(calls, list):
            raise ValueError("assistant tool_calls must be an array")
        call_ids: list[str] = []
        for call in calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            call_id = call.get("id") if isinstance(call, Mapping) else None
            if (not isinstance(call, Mapping)
                    or set(call) != {"id", "type", "function"}
                    or call.get("type") != "function"
                    or not isinstance(call_id, str) or not call_id
                    or not isinstance(function, Mapping)
                    or set(function) != {"name", "arguments"}
                    or not isinstance(function.get("name"), str)
                    or not isinstance(function.get("arguments"), str)):
                raise ValueError("assistant tool call is malformed")
            call_ids.append(call_id)
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("assistant tool call identities are duplicated")
        result_ids: list[str] = []
        cursor = index + 1
        while (cursor < len(messages)
               and isinstance(messages[cursor], Mapping)
               and messages[cursor].get("role") == "tool"):
            result_id = messages[cursor].get("tool_call_id")
            if not isinstance(result_id, str) or not result_id:
                raise ValueError("tool result lacks its action identity")
            result_ids.append(result_id)
            cursor += 1
        if (len(result_ids) != len(call_ids)
                or len(set(result_ids)) != len(result_ids)
                or set(result_ids) != set(call_ids)):
            raise ValueError(
                "assistant tool calls lack one contiguous matching result each")
        index = cursor


def validate_authored_llm_prompt_payload(
        payload: bytes, *, expected_model_condition: str,
        expected_max_output_tokens: int) -> Mapping[str, object]:
    """Validate one canonical authored prompt against generic LLM bounds."""
    if (not isinstance(expected_model_condition, str)
            or not expected_model_condition
            or expected_model_condition != expected_model_condition.strip()):
        raise ValueError("LLM prompt model condition is not canonical")
    if (isinstance(expected_max_output_tokens, bool)
            or not isinstance(expected_max_output_tokens, int)
            or expected_max_output_tokens < 1):
        raise ValueError("LLM prompt output-token bound must be positive")
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("LLM prompt must be nonempty bytes")

    def unique_object(
            pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("LLM prompt repeats a JSON field")
            result[name] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LLM prompt is not canonical UTF-8 JSON") from exc
    if (not isinstance(decoded, dict)
            or _canonical_json(decoded) != payload
            or not {
                "model_condition", "messages", "max_output_tokens",
            }.issubset(decoded)
            or not set(decoded).issubset(
                {"model_condition", "messages", "max_output_tokens",
                 "tools", "tool_choice"})
            or decoded.get("model_condition") != expected_model_condition
            or decoded.get("max_output_tokens")
            != expected_max_output_tokens):
        raise ValueError(
            "LLM prompt differs from its registered model condition or bounds")
    messages = decoded.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("LLM prompt requires nonempty messages")
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else None
        allowed_fields = {"role", "content", "tool_calls", "tool_call_id"}
        if role == "assistant":
            allowed_fields.add("reasoning_content")
        if (not isinstance(message, dict)
                or role not in {"system", "user", "assistant", "tool"}
                or not isinstance(message.get("content"), str)
                or (role != "assistant" and not message["content"].strip())
                or not set(message).issubset(allowed_fields)
                or ("reasoning_content" in message
                    and not isinstance(message["reasoning_content"], str))):
            raise ValueError("LLM prompt contains an invalid message")
    validate_llm_request_message_history(messages)
    tools = decoded.get("tools")
    if tools is not None:
        if (not isinstance(tools, list) or not tools
                or decoded.get("tool_choice") != "auto"):
            raise ValueError("LLM prompt tool selection is invalid")
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if (not isinstance(tool, dict)
                    or set(tool) != {"type", "function"}
                    or tool["type"] != "function"
                    or not isinstance(function, dict)
                    or set(function) != {"name", "description", "parameters"}
                    or not isinstance(function["name"], str)
                    or not isinstance(function["description"], str)
                    or not isinstance(function["parameters"], dict)
                    or function["parameters"].get("type") != "object"):
                raise ValueError("LLM prompt contains an invalid tool")
    return decoded


__all__ = [
    "validate_authored_llm_prompt_payload",
    "validate_llm_request_message_history",
]
