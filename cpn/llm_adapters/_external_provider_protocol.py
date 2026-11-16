"""OpenAI-compatible wire translation private to the external adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError,
    canonicalize_llm_response_payload,
)
from ._common import (
    ResponseEnvelopeError,
    canonical_request_envelope_document,
)


@dataclass(frozen=True, slots=True)
class ChatRequestOptions:
    """Explicit wire choices, independent of provider or exact-model names."""

    token_limit_field: str = "max_tokens"
    service_tier: str | None = None
    prompt_cache_mode: str = "provider_default"
    # None preserves provider-default behavior and the existing wire shape.
    # A HOST opts in only for a route that supports this protocol field.
    parallel_tool_calls: bool | None = None

    def __post_init__(self) -> None:
        if self.token_limit_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("unsupported token_limit_field")
        if self.service_tier not in (None, "default"):
            raise ValueError("service_tier must be null or default")
        if self.prompt_cache_mode not in ("provider_default", "disabled"):
            raise ValueError("unsupported prompt_cache_mode")
        if (self.parallel_tool_calls is not None
                and type(self.parallel_tool_calls) is not bool):
            raise ValueError("parallel_tool_calls must be a boolean when selected")

    def fields(self, token_limit: int) -> dict[str, object]:
        fields: dict[str, object] = {self.token_limit_field: token_limit}
        if self.service_tier is not None:
            fields["service_tier"] = self.service_tier
        if self.prompt_cache_mode == "disabled":
            # Payload validation also rejects explicit wire breakpoints.
            fields["prompt_cache_options"] = {"mode": "explicit"}
        if self.parallel_tool_calls is not None:
            fields["parallel_tool_calls"] = self.parallel_tool_calls
        return fields

    def as_document(self) -> dict[str, str | bool | None]:
        """Return the explicit non-secret choices for execution provenance."""
        document: dict[str, str | bool | None] = {
            "token_limit_field": self.token_limit_field,
            "service_tier": self.service_tier,
            "prompt_cache_mode": self.prompt_cache_mode,
        }
        if self.parallel_tool_calls is not None:
            document["parallel_tool_calls"] = self.parallel_tool_calls
        return document


def chat_request_options_from_document(value: object) -> ChatRequestOptions:
    required = {"token_limit_field", "service_tier", "prompt_cache_mode"}
    if (not isinstance(value, Mapping) or not required.issubset(value)
            or set(value) - required - {"parallel_tool_calls"}):
        raise ValueError("request_options contains missing or unsupported fields")
    if "parallel_tool_calls" in value and type(value["parallel_tool_calls"]) is not bool:
        raise ValueError("parallel_tool_calls must be a boolean when selected")
    return ChatRequestOptions(**value)


def _reject_explicit_cache_breakpoints(document: Mapping[str, Any]) -> None:
    """Reject wire control fields, never matching text or tool-schema properties."""
    objects = list(document["messages"]) + list(document["tools"])
    for message in document["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            objects.extend(part for part in content if isinstance(part, Mapping))
    for tool in document["tools"]:
        function = tool.get("function")
        if isinstance(function, Mapping):
            objects.append(function)
    # Content-part breakpoints are documented. Message/tool/function-level
    # reserved fields are rejected defensively, not claimed as supported API.
    # Do not descend into parameters: schema property names are ordinary data.
    if any("prompt_cache_breakpoint" in value for value in objects):
        raise ResponseEnvelopeError(
            "disabled prompt cache forbids explicit wire breakpoints")


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
    request_options: ChatRequestOptions = ChatRequestOptions(),
) -> bytes:
    """Translate validated framework request bytes for one external route."""
    document = canonical_request_envelope_document(
        payload,
        expected_model_condition=expected_model_condition,
        expected_max_output_tokens=expected_max_output_tokens,
    )
    if request_options.prompt_cache_mode == "disabled":
        _reject_explicit_cache_breakpoints(document)
    try:
        request: dict[str, object] = {
            "model": outbound_model,
            **request_options.fields(document["max_output_tokens"]),
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
