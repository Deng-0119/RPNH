"""Shared adapter-private parsing, envelope, and timing helpers."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError,
    canonicalize_llm_response_payload,
)
from cpn.components.request_protocol import (
    validate_llm_request_message_history,
)


class AdapterConfigError(RuntimeError):
    """An adapter-private configuration is malformed or inconsistent."""


class ResponseEnvelopeError(RuntimeError):
    """Response bytes cannot become the canonical framework envelope."""


_REQUEST_FIELDS = {
    "protocol", "model_condition", "max_output_tokens", "messages", "tools",
    "tool_choice",
}


def canonical_request_envelope_document(
    payload: bytes, *, expected_model_condition: str,
    expected_max_output_tokens: int,
) -> dict[str, Any]:
    """Validate canonical framework request bytes without route translation."""
    if not isinstance(payload, bytes) or not payload:
        raise ResponseEnvelopeError("LLM request envelope must be nonempty bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ResponseEnvelopeError(
                    f"LLM request envelope repeats JSON field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except ResponseEnvelopeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResponseEnvelopeError(
            "LLM request envelope must be one UTF-8 JSON document") from exc
    if not isinstance(value, dict):
        raise ResponseEnvelopeError("LLM request envelope must be one JSON object")
    document = value
    if not _REQUEST_FIELDS.issubset(document):
        raise ResponseEnvelopeError(
            "LLM request envelope lacks a required current field")
    if document.get("protocol") != "llm_request_envelope/v1":
        raise ResponseEnvelopeError(
            "LLM request envelope protocol is not current")
    if document.get("model_condition") != expected_model_condition:
        raise ResponseEnvelopeError(
            "LLM request envelope model_condition differs from target")
    request_max_tokens = document.get("max_output_tokens")
    if (isinstance(request_max_tokens, bool)
            or not isinstance(request_max_tokens, int)
            or request_max_tokens != expected_max_output_tokens):
        raise ResponseEnvelopeError(
            "LLM request envelope max_output_tokens differs from target")
    messages = document.get("messages")
    tools = document.get("tools")
    tool_choice = document.get("tool_choice")
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, Mapping) for message in messages)
            or not isinstance(tools, list)
            or any(not isinstance(tool, Mapping) for tool in tools)
            or not isinstance(tool_choice, (str, Mapping))
            or isinstance(tool_choice, str) and not tool_choice):
        raise ResponseEnvelopeError(
            "LLM request envelope messages/tools/tool_choice are invalid")
    try:
        validate_llm_request_message_history(messages)
    except ValueError as exc:
        raise ResponseEnvelopeError(str(exc)) from exc
    try:
        canonical = json.dumps(
            document, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if payload != canonical:
            raise ResponseEnvelopeError(
                "LLM request envelope is not canonical serialization")
        return document
    except ResponseEnvelopeError:
        raise
    except (TypeError, ValueError) as exc:
        raise ResponseEnvelopeError(
            "LLM request envelope is not finite JSON") from exc


def require_canonical_request_envelope(
    payload: bytes, *, expected_model_condition: str,
    expected_max_output_tokens: int,
) -> bytes:
    """Validate a local request without translating or exposing provenance."""
    canonical_request_envelope_document(
        payload,
        expected_model_condition=expected_model_condition,
        expected_max_output_tokens=expected_max_output_tokens,
    )
    return payload


def require_canonical_response_envelope(payload: bytes) -> bytes:
    """Validate that local stdout is already canonical envelope bytes."""
    try:
        canonical = canonicalize_llm_response_payload(payload)
    except LLMResponseProtocolError as exc:
        raise ResponseEnvelopeError(str(exc)) from exc
    if payload != canonical:
        raise ResponseEnvelopeError(
            "local response is not canonical envelope serialization")
    return payload


def timing_evidence(
    started_ns: int, completed_ns: int, timing_origin_ns: int | None,
) -> dict[str, int]:
    evidence = {
        "elapsed_ns": max(0, completed_ns - started_ns),
    }
    if (isinstance(timing_origin_ns, int)
            and not isinstance(timing_origin_ns, bool)
            and timing_origin_ns >= 0):
        evidence["started_offset_ns"] = max(0, started_ns - timing_origin_ns)
        evidence["completed_offset_ns"] = max(
            0, completed_ns - timing_origin_ns)
    return evidence


def monotonic_ns() -> int:
    return time.monotonic_ns()


__all__ = [
    "AdapterConfigError",
    "ResponseEnvelopeError",
    "canonical_request_envelope_document",
    "monotonic_ns",
    "require_canonical_request_envelope",
    "require_canonical_response_envelope",
    "timing_evidence",
]
