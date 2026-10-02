"""Public-boundary observation for formal RRSI provider attempts.

The wrapper records the exact authority and bytes already handed to an
``LLMInputPort``.  It neither reads Registry internals nor invents usage when
the provider response does not report it.
"""
from __future__ import annotations

import json
from threading import RLock
from typing import Any

from cpn.rpnh.llm_contracts import LLMInputPortFailure


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
    """Record one row per physical input-port call using public objects only."""

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

    def request_once(self, attempt):
        row = self._base_row(attempt)
        try:
            response = self._input_port.request_once(attempt)
        except LLMInputPortFailure as exc:
            row["submission_state"] = exc.submission_state
            row["outcome"] = exc.disposition
            row["failure_code"] = exc.failure_code
            self._append(row)
            raise
        except Exception as exc:
            # Unknown exceptions are not reclassified as a known submission
            # state.  The row remains useful for reconciliation.
            row["submission_state"] = "unknown"
            row["outcome"] = "unclassified_exception"
            row["failure_code"] = type(exc).__name__
            self._append(row)
            raise
        document = _document(response)
        usage = _normalized_usage(document.get("usage"))
        row["submission_state"] = "response_observed"
        row["outcome"] = "success"
        row["canonical_response"] = document
        row["usage"] = usage
        row["status_code"] = getattr(response, "status_code", None)
        row["external_request_id"] = getattr(
            response, "external_request_id", None)
        self._append(row)
        return response

    def observations(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(json.loads(json.dumps(row)) for row in self._rows)

    def close(self) -> None:
        self._input_port.close()


def usage_summary(rows) -> dict[str, Any]:
    """Aggregate only provider-reported usage; unknown values stay explicit."""
    items = tuple(rows)
    submitted = [row for row in items
                 if row.get("submission_state") == "response_observed"]
    known = [row["usage"] for row in submitted
             if isinstance(row.get("usage"), dict)
             and isinstance(row["usage"].get("total_tokens"), int)
             and not isinstance(row["usage"]["total_tokens"], bool)
             and row["usage"]["total_tokens"] >= 0]
    return {
        "physical_attempts": len(items),
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
