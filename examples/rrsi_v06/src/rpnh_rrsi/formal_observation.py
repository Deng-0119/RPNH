"""Public-boundary observation for formal RRSI provider attempts.

The wrapper records the exact authority and bytes already handed to an
``LLMInputPort``.  It neither reads Registry internals nor invents usage when
the provider response does not report it.
"""
from __future__ import annotations

import json
from threading import RLock
from typing import Any

from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted


# Explicit public vocabulary only: an adapter may put arbitrary private text
# in failure_code, so unknown codes must be reconciled with its private audit.
_PUBLIC_FAILURE_CODES = frozenset({
    "connection_lost", "owner_interrupted", "ResponseEnvelopeError",
    "local_adapter_closed_or_model_mismatch", "local_request_protocol_invalid",
    "local_duplicate_submission_prevented",
    "external_adapter_closed_or_model_mismatch", "external_request_protocol_invalid",
    "external_duplicate_submission_prevented", "external_route_request_protocol_invalid",
    "external_provider_no_route_result", "external_recovery_cycle_limit_exhausted",
    "external_health_probe_budget_exhausted", "external_health_probe_attempts_exhausted",
} | {
    f"{phase}_{category}"
    for phase in ("credential_resolution", "connection", "request_write",
                  "response_headers", "response_body", "response_normalization")
    for category in ("timeout", "name_resolution_failure", "tls_failure",
                     "connection_failure", "http_protocol_failure",
                     "credential_failure", "response_protocol_invalid",
                     "response_size_limit", "owner_interrupted")
} | {f"http_status_{status}" for status in range(100, 600)})


def public_failure_code(value: str) -> str:
    return value if value in _PUBLIC_FAILURE_CODES else "input_port_failure"


def _ref(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    entity_type = getattr(value, "entity_type", None)
    entity_id = getattr(value, "entity_id", None)
    version_id = getattr(value, "version_id", None)
    if not all(item is not None for item in (entity_type, entity_id, version_id)):
        raise TypeError("attempt reference is not a Registry VersionRef")
    return {
        "entity_type": str(entity_type),
        "logical_id": str(entity_id),
        "version_id": str(version_id),
    }


def _document(value: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("observed provider bytes are not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("observed provider document is not an object")
    return parsed


def _normalized_usage(value: object) -> dict[str, Any] | None:
    """Normalize public provider usage without inventing unavailable values."""
    if not isinstance(value, dict):
        return None
    usage = dict(value)

    def count(name: str) -> int | None:
        item = usage.get(name)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        return item

    total = count("total_tokens")
    if total is None:
        usage.pop("total_tokens", None)
        input_tokens = count("input_tokens")
        output_tokens = count("output_tokens")
        if input_tokens is not None and output_tokens is not None:
            usage["total_tokens"] = input_tokens + output_tokens
    return usage


class ObservedInputPort:
    """Record one row per neutral input-port invocation, not transport call."""

    def __init__(self, input_port) -> None:
        if not callable(getattr(input_port, "request_once", None)):
            raise TypeError("observed input port requires request_once")
        self._input_port = input_port
        self._lock = RLock()
        self._rows: list[dict[str, Any]] = []
        self.execution_policy = input_port.execution_policy

    def _base_row(self, attempt) -> dict[str, Any]:
        return {
            "sequence": 0,
            "invocation_ref": _ref(attempt.invocation_ref),
            "attempt_ref": _ref(attempt.attempt_ref),
            "provider_attempt_ref": _ref(
                getattr(attempt, "provider_attempt_ref", None)),
            "attempt_ordinal": attempt.attempt_ordinal,
            "model_condition": attempt.model_condition,
            "max_response_bytes": attempt.max_response_bytes,
            "canonical_request": _document(attempt.canonical_request_bytes),
            "submission_state": None,
            "outcome": None,
            "canonical_response": None,
            "usage": None,
            "status_code": None,
            "external_request_id": None,
            "failure_code": None,
        }

    def _append(self, row: dict[str, Any]) -> None:
        with self._lock:
            row["sequence"] = len(self._rows) + 1
            self._rows.append(row)

    def _request(self, attempt, invoke):
        row = self._base_row(attempt)
        try:
            response = invoke()
            # Parsing belongs inside the observation boundary too: a malformed
            # response must not make an already invoked input port disappear.
            row["submission_state"] = "response_observed"
            document = _document(response)
            row["outcome"] = "success"
            row["canonical_response"] = document
            row["usage"] = _normalized_usage(document.get("usage"))
            row["status_code"] = getattr(response, "status_code", None)
            row["external_request_id"] = getattr(response, "external_request_id", None)
            return response
        except LLMInputPortInterrupted as exc:
            row["submission_state"] = exc.submission_state
            row["outcome"] = "owner_interrupted"
            row["failure_code"] = "owner_interrupted"
            row["error_type"] = type(exc).__name__
            raise
        except LLMInputPortFailure as exc:
            row["submission_state"] = exc.submission_state
            row["outcome"] = exc.disposition
            # Arbitrary adapter exception payloads belong in its private audit.
            row["failure_code"] = public_failure_code(exc.failure_code)
            row["error_type"] = type(exc).__name__
            raise
        except BaseException as exc:
            # Preserve unknown submission, including KeyboardInterrupt, rather
            # than inventing a safe retry or exporting the exception text.
            row["submission_state"] = row["submission_state"] or "unknown"
            row["outcome"] = "unclassified_exception"
            row["failure_code"] = type(exc).__name__
            row["error_type"] = type(exc).__name__
            raise
        finally:
            self._append(row)

    def request_once(self, attempt):
        return self._request(attempt, lambda: self._input_port.request_once(attempt))

    def request_once_interruptible(self, attempt, *, interruption_requested):
        if not callable(interruption_requested):
            raise TypeError("interruption_requested must be callable")

        def invoke():
            interruptible = getattr(self._input_port, "request_once_interruptible", None)
            if callable(interruptible):
                return interruptible(attempt, interruption_requested=interruption_requested)
            # Legacy ports remain supported. They can stop before submission;
            # only an interruptible underlying port can cancel an in-flight call.
            if interruption_requested():
                raise LLMInputPortInterrupted(submission_state="not_submitted")
            return self._input_port.request_once(attempt)

        return self._request(attempt, invoke)

    def observations(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(json.loads(json.dumps(row)) for row in self._rows)

    def close(self) -> None:
        self._input_port.close()


def usage_summary(rows) -> dict[str, Any]:
    """Aggregate final-response usage; adapter recovery/probe totals are unknown."""
    items = tuple(rows)
    submitted = [row for row in items
                 if row.get("submission_state") == "response_observed"]
    known = [row["usage"] for row in submitted
             if isinstance(row.get("usage"), dict)
             and isinstance(row["usage"].get("total_tokens"), int)
             and not isinstance(row["usage"]["total_tokens"], bool)
             and row["usage"]["total_tokens"] >= 0]
    return {
        "input_port_invocations": len(items),
        "physical_attempts": len(items),
        "physical_attempts_semantics": "deprecated_alias_of_input_port_invocations",
        "usage_scope": "visible_final_responses_only",
        "provider_physical_calls": None,
        "provider_physical_total_tokens": None,
        "provider_physical_cost_complete": False,
        "response_observed_attempts": len(submitted),
        "known_usage_attempts": len(known),
        "unknown_usage_attempts": len(submitted) - len(known),
        "total_tokens": (
            sum(row["total_tokens"] for row in known)
            if len(known) == len(submitted) == len(items) else None),
        "known_total_tokens_lower_bound": sum(
            row["total_tokens"] for row in known),
    }


__all__ = ("ObservedInputPort", "usage_summary")
