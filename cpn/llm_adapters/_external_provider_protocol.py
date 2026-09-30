"""OpenAI-compatible wire translation private to the external adapter."""

from __future__ import annotations

import json
from typing import Any, Mapping

from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError,
    canonicalize_llm_response_payload,
)
from ._common import (
    ResponseEnvelopeError,
    canonical_request_envelope_document,
)


def _provider_json_object(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload:
        raise ResponseEnvelopeError("provider response must be nonempty bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResponseEnvelopeError(
                    f"provider response repeats JSON field {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except ResponseEnvelopeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResponseEnvelopeError(
            "provider response must be one UTF-8 JSON document") from exc
    if not isinstance(document, dict):
        raise ResponseEnvelopeError("provider response must be one JSON object")
    return document


def provider_request_from_envelope(
    payload: bytes, *, expected_model_condition: str,
    expected_max_output_tokens: int, outbound_model: str,
    reasoning_effort: str | None = None,
) -> bytes:
    """Translate validated framework request bytes for one external route."""
    document = canonical_request_envelope_document(
        payload,
        expected_model_condition=expected_model_condition,
        expected_max_output_tokens=expected_max_output_tokens,
    )
    try:
        request: dict[str, object] = {
            "model": outbound_model,
            "max_tokens": document["max_output_tokens"],
            "messages": document["messages"],
            "tools": document["tools"],
            "tool_choice": document["tool_choice"],
            "stream": False,
        }
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
        return json.dumps(request, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResponseEnvelopeError(
            "LLM request envelope is not finite JSON") from exc


def _optional_text(value: object, *, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ResponseEnvelopeError(f"{label} must be text or null")
    return value


def _token_count(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResponseEnvelopeError(
            f"{label} must be a nonnegative integer or null")
    return value


def _openai_usage(usage: object) -> dict[str, int | None]:
    if usage is None:
        source: Mapping[str, object] = {}
    elif isinstance(usage, Mapping):
        source = usage
    else:
        raise ResponseEnvelopeError("provider usage must be an object or null")

    def alias(primary: str, alternate: str) -> int | None:
        primary_value = _token_count(
            source.get(primary), label=f"provider usage.{primary}")
        alternate_value = _token_count(
            source.get(alternate), label=f"provider usage.{alternate}")
        if (primary_value is not None and alternate_value is not None
                and primary_value != alternate_value):
            raise ResponseEnvelopeError(
                f"provider usage {primary}/{alternate} values disagree")
        return primary_value if primary_value is not None else alternate_value

    input_tokens = alias("prompt_tokens", "input_tokens")
    output_tokens = alias("completion_tokens", "output_tokens")
    total_tokens = _token_count(
        source.get("total_tokens"), label="provider usage.total_tokens")
    if (total_tokens is None and input_tokens is not None
            and output_tokens is not None):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def normalize_openai_compatible_response(vendor_payload: bytes) -> bytes:
    """Convert one OpenAI-compatible completion into the v1 envelope."""
    document = _provider_json_object(vendor_payload)
    choices = document.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ResponseEnvelopeError(
            "provider response requires exactly one completion choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ResponseEnvelopeError("provider choice must be an object")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ResponseEnvelopeError(
            "provider choice requires one assistant message")
    role = message.get("role")
    if role is not None and role != "assistant":
        raise ResponseEnvelopeError("provider message role is not assistant")

    content = _optional_text(
        message.get("content"), label="provider message.content")
    reasoning_content = _optional_text(
        message.get("reasoning_content"),
        label="provider message.reasoning_content")
    finish_reason = _optional_text(
        choice.get("finish_reason"), label="provider finish_reason")

    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise ResponseEnvelopeError("provider tool_calls must be an array")
    tool_calls: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            raise ResponseEnvelopeError("provider tool call must be an object")
        call_id = raw_call.get("id")
        call_type = raw_call.get("type", "function")
        function = raw_call.get("function")
        if (not isinstance(call_id, str) or not call_id
                or call_id in seen_ids or call_type != "function"
                or not isinstance(function, Mapping)):
            raise ResponseEnvelopeError(
                "provider tool call identity or type is invalid")
        name = function.get("name")
        arguments = function.get("arguments")
        if (not isinstance(name, str) or not name
                or not isinstance(arguments, str)):
            raise ResponseEnvelopeError(
                "provider tool call function is invalid")
        seen_ids.add(call_id)
        tool_calls.append({
            "id": call_id,
            "name": name,
            "arguments": arguments,
        })
    envelope: dict[str, object] = {
        "protocol": "llm_response_envelope/v1",
        "text": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "reasoning_content": reasoning_content,
        "usage": _openai_usage(document.get("usage")),
    }
    try:
        return canonicalize_llm_response_payload(envelope)
    except LLMResponseProtocolError as exc:
        raise ResponseEnvelopeError(str(exc)) from exc


__all__ = [
    "normalize_openai_compatible_response",
    "provider_request_from_envelope",
]
