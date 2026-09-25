"""Canonical framework LLM response envelope protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .registry.models import VersionRef
from .registry.resources import ResourceVersionRef


_PROTOCOL_REF = "llm_response_envelope/v1"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_TOP_LEVEL_FIELDS = frozenset({
    "protocol", "text", "tool_calls", "reasoning_content", "finish_reason",
    "usage",
})
_TOOL_CALL_FIELDS = frozenset({"id", "name", "arguments"})


class LLMResponseProtocolError(RuntimeError):
    """A response does not satisfy the canonical LLM response envelope."""


def _decode_unique_json(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload:
        raise LLMResponseProtocolError("response must be nonempty UTF-8 JSON bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LLMResponseProtocolError(f"response repeats JSON field {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except LLMResponseProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMResponseProtocolError("response is not one UTF-8 JSON document") from exc
    if not isinstance(document, dict):
        raise LLMResponseProtocolError("response must be one JSON object")
    return document


def _nonempty_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LLMResponseProtocolError(f"{label} must be nonempty trimmed text")
    return value


def _validate_json_value(value: Any, *, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LLMResponseProtocolError(f"{label} must be JSON-compatible") from exc


def _canonical_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise LLMResponseProtocolError("response must be a JSON object")
    unknown = set(document) - _TOP_LEVEL_FIELDS
    if unknown:
        raise LLMResponseProtocolError("response has unknown fields")
    if document.get("protocol") != _PROTOCOL_REF:
        raise LLMResponseProtocolError("response protocol is not llm_response_envelope/v1")

    text = document.get("text")
    if text is not None and not isinstance(text, str):
        raise LLMResponseProtocolError("text must be text or null")
    calls_value = document.get("tool_calls", [])
    if not isinstance(calls_value, list):
        raise LLMResponseProtocolError("tool_calls must be an array")
    calls: list[dict[str, str]] = []
    call_ids: set[str] = set()
    for ordinal, call in enumerate(calls_value):
        if not isinstance(call, Mapping) or set(call) != _TOOL_CALL_FIELDS:
            raise LLMResponseProtocolError(f"tool_calls[{ordinal}] is malformed")
        call_id = _nonempty_text(call["id"], label=f"tool_calls[{ordinal}].id")
        if not _IDENTITY.fullmatch(call_id) or call_id in call_ids:
            raise LLMResponseProtocolError(f"tool_calls[{ordinal}] has an invalid action identity")
        name = _nonempty_text(call["name"], label=f"tool_calls[{ordinal}].name")
        arguments = call["arguments"]
        if not isinstance(arguments, str):
            raise LLMResponseProtocolError(f"tool_calls[{ordinal}].arguments must be text")
        call_ids.add(call_id)
        calls.append({"id": call_id, "name": name, "arguments": arguments})
    result: dict[str, Any] = {"protocol": _PROTOCOL_REF, "tool_calls": calls}
    if text is not None:
        result["text"] = text
    for field in ("reasoning_content", "finish_reason"):
        value = document.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise LLMResponseProtocolError(f"{field} must be text or null")
            result[field] = value
    if (text is None and not calls
            and result.get("finish_reason") != "length"):
        raise LLMResponseProtocolError(
            "response requires text or a tool call unless generation stopped at length")
    usage = document.get("usage")
    if usage is not None:
        if not isinstance(usage, Mapping):
            raise LLMResponseProtocolError("usage must be an object or null")
        _validate_json_value(usage, label="usage")
        result["usage"] = dict(usage)
    return result


def canonicalize_llm_response_payload(payload: Mapping[str, Any] | bytes) -> bytes:
    """Validate an existing canonical envelope and return its canonical bytes."""
    document = _decode_unique_json(payload) if isinstance(payload, bytes) else payload
    return json.dumps(
        _canonical_document(document), ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PublishedRawLLMResponse:
    """Registry authority proving raw response bytes were published first."""

    response_resource_ref: ResourceVersionRef
    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef
    size: int
    backend: str
    model: str
    interaction_protocol_ref: str
    response_adapter_ref: str
    payload_digest: str | None = None
    response_adapter_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.response_resource_ref, ResourceVersionRef):
            raise TypeError("raw response authority requires an exact resource ref")
        if (not isinstance(self.provider_attempt_ref, VersionRef)
                or self.provider_attempt_ref.entity_type
                != "provider_attempt_spec/v1"):
            raise TypeError("raw response authority requires an exact attempt ref")
        if (not isinstance(self.llm_call_ref, VersionRef)
                or self.llm_call_ref.entity_type != "llm_call_spec/v2"):
            raise TypeError("raw response authority requires an exact v2 call ref")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("raw response authority size is invalid")
        for value in (self.backend, self.model, self.interaction_protocol_ref,
                      self.response_adapter_ref):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError("raw response authority identity is invalid")
        for digest in (self.payload_digest, self.response_adapter_digest):
            if digest is not None and (
                    not isinstance(digest, str)
                    or not digest or digest != digest.strip()):
                raise ValueError("raw response authority digest is invalid")


@dataclass(frozen=True, slots=True)
class PublishedLLMResponse:
    """Registry authority for one published canonical response."""

    response_resource_ref: ResourceVersionRef
    llm_invocation_attempt_ref: VersionRef
    llm_invocation_ref: VersionRef
    model_condition: str
    size: int
    response_protocol_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.response_resource_ref, ResourceVersionRef):
            raise TypeError("response authority requires an exact resource ref")
        if (not isinstance(self.llm_invocation_attempt_ref, VersionRef)
                or self.llm_invocation_attempt_ref.entity_type != "llm_invocation_attempt/v1"):
            raise TypeError("response authority requires an exact invocation-attempt ref")
        if (not isinstance(self.llm_invocation_ref, VersionRef)
                or self.llm_invocation_ref.entity_type != "llm_invocation_spec/v1"):
            raise TypeError("response authority requires an exact invocation ref")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("response authority size is invalid")
        _nonempty_text(self.model_condition, label="model_condition")
        if self.response_protocol_ref != _PROTOCOL_REF:
            raise ValueError("response authority protocol is invalid")


@dataclass(frozen=True, slots=True)
class LLMToolCallObservation:
    tool_call_ordinal: int
    tool_call_id: str
    tool_name: str
    raw_arguments: str
    syntax_errors: tuple[str, ...]
    action_identity_kind: str
    action_identity_key: str

    def __post_init__(self) -> None:
        if (isinstance(self.tool_call_ordinal, bool)
                or not isinstance(self.tool_call_ordinal, int)
                or self.tool_call_ordinal < 0):
            raise ValueError("tool-call ordinal is invalid")
        if not all(isinstance(value, str) for value in (
                self.tool_call_id, self.tool_name, self.raw_arguments, self.action_identity_key)):
            raise TypeError("tool-call observation fields must be text")
        if not _IDENTITY.fullmatch(self.tool_call_id):
            raise ValueError("tool-call action identity is invalid")
        if self.syntax_errors or self.action_identity_kind != "tool_call_id" \
                or self.action_identity_key != self.tool_call_id:
            raise ValueError("canonical tool calls have one exact action identity")


@dataclass(frozen=True, slots=True)
class LLMTurnObservation:
    raw_response: PublishedLLMResponse
    text: str | None
    tool_calls: tuple[LLMToolCallObservation, ...]
    finish_reason: str | None
    reasoning_content: str | None = None
    usage: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_response, PublishedLLMResponse):
            raise TypeError("turn observation requires response publication authority")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("observed text must be text or None")
        if self.reasoning_content is not None and not isinstance(self.reasoning_content, str):
            raise TypeError("observed reasoning must be text or None")
        if self.finish_reason is not None and not isinstance(self.finish_reason, str):
            raise TypeError("finish_reason must be text or None")
        if any(not isinstance(call, LLMToolCallObservation) for call in self.tool_calls):
            raise TypeError("tool calls must be observations")
        if tuple(call.tool_call_ordinal for call in self.tool_calls) != tuple(range(len(self.tool_calls))):
            raise LLMResponseProtocolError("tool-call observations lost envelope order")
        if (self.text is None and not self.tool_calls
                and self.finish_reason != "length"):
            raise LLMResponseProtocolError(
                "turn observation requires content unless stopped at length")
        if self.usage is not None:
            if not isinstance(self.usage, Mapping):
                raise TypeError("usage must be a mapping or None")
            _validate_json_value(self.usage, label="usage")


def _observe_envelope(payload: bytes, authority: PublishedLLMResponse) -> LLMTurnObservation:
    document = _canonical_document(_decode_unique_json(payload))
    calls = tuple(LLMToolCallObservation(
        tool_call_ordinal=ordinal,
        tool_call_id=call["id"],
        tool_name=call["name"],
        raw_arguments=call["arguments"],
        syntax_errors=(),
        action_identity_kind="tool_call_id",
        action_identity_key=call["id"],
    ) for ordinal, call in enumerate(document["tool_calls"]))
    return LLMTurnObservation(
        raw_response=authority, text=document.get("text"), tool_calls=calls,
        finish_reason=document.get("finish_reason"),
        reasoning_content=document.get("reasoning_content"), usage=document.get("usage"),
    )


def observe_registered_llm_response(
        payload: bytes, authority: PublishedLLMResponse,
) -> LLMTurnObservation:
    """Directly observe one published canonical response envelope."""
    if not isinstance(authority, PublishedLLMResponse):
        raise TypeError("response observation requires publication authority")
    if authority.response_protocol_ref != _PROTOCOL_REF:
        raise LLMResponseProtocolError("response protocol differs from publication authority")
    if not isinstance(payload, bytes):
        raise TypeError("response payload must be exact bytes")
    if len(payload) != authority.size:
        raise LLMResponseProtocolError("response bytes differ from publication authority")
    return _observe_envelope(payload, authority)


__all__ = [
    "LLMResponseProtocolError",
    "LLMToolCallObservation", "LLMTurnObservation", "PublishedLLMResponse",
    "PublishedRawLLMResponse",
    "canonicalize_llm_response_payload", "observe_registered_llm_response",
]
