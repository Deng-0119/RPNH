"""HarnessAudit JSON-judge calls over the configured RPNH local-process transport."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from typing import Any

from .jsonio import write_new


class JudgeTransportError(RuntimeError):
    """The configured judge did not return one JSON object."""


def _canonical_request(
        *, model: str, system_prompt: str, user_prompt: str,
        max_output_tokens: int) -> bytes:
    document = {
        "protocol": "llm_request_envelope/v1",
        "model_condition": model,
        "max_output_tokens": max_output_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [],
        "tool_choice": "none",
    }
    return json.dumps(
        document, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _judge_result(response: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JudgeTransportError("judge response envelope is not JSON") from exc
    if (not isinstance(envelope, dict)
            or envelope.get("protocol") != "llm_response_envelope/v1"
            or envelope.get("tool_calls") != []
            or not isinstance(envelope.get("text"), str)):
        raise JudgeTransportError("judge response envelope is not a text-only result")
    try:
        result = json.loads(envelope["text"])
    except json.JSONDecodeError as exc:
        raise JudgeTransportError("judge text is not JSON") from exc
    if not isinstance(result, dict):
        raise JudgeTransportError("judge JSON result must be an object")
    return result, envelope


class LocalProcessJudge:
    """Expose HarnessAudit's Responses-style helper over one local adapter."""

    def __init__(
            self, *, adapter_path: Path, output: Path, exact_model: str,
            configured_max_output_tokens: int = 512,
            timeout_seconds: int = 300, max_response_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.adapter_path = Path(adapter_path).resolve()
        self.output = Path(output).resolve()
        self.exact_model = exact_model
        self.configured_max_output_tokens = configured_max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._counter = 0
        self._counter_lock = threading.Lock()
        self.successful_calls: list[dict[str, Any]] = []
        self.failed_calls: list[dict[str, Any]] = []

    def _next_call(self) -> tuple[int, Path]:
        with self._counter_lock:
            self._counter += 1
            ordinal = self._counter
        call_root = self.output / "judge-calls" / f"call-{ordinal:03d}"
        call_root.mkdir(parents=True)
        return ordinal, call_root

    def _request_sync(
            self, *, model: str, system_prompt: str, user_prompt: str,
            max_output_tokens: int) -> dict[str, Any]:
        if model != self.exact_model:
            raise JudgeTransportError("judge requested a model outside the configured condition")
        if max_output_tokens != self.configured_max_output_tokens:
            raise JudgeTransportError(
                "judge max_output_tokens differs from the configured transport")
        ordinal, call_root = self._next_call()
        request = _canonical_request(
            model=model, system_prompt=system_prompt,
            user_prompt=user_prompt, max_output_tokens=max_output_tokens)
        write_new(call_root / "request.json", json.loads(request))
        try:
            from cpn.llm_adapters.local_process import LocalProcessInputPort
            from cpn.rpnh.llm_contracts import LLMCallAttempt
            from cpn.rpnh.registry.identities import new_id
            from cpn.rpnh.registry.models import VersionRef

            port = LocalProcessInputPort(
                model_condition=model,
                max_output_tokens=max_output_tokens,
                timeout_seconds=self.timeout_seconds,
                max_response_bytes=self.max_response_bytes,
                config_path=self.adapter_path,
                destination_run_root=call_root,
            )
            attempt = LLMCallAttempt(
                invocation_ref=VersionRef(
                    "llm_invocation_spec/v1", new_id("llm_invocation"),
                    new_id("llm_invocation_version")),
                attempt_ref=VersionRef(
                    "llm_invocation_attempt/v1",
                    new_id("llm_invocation_attempt"),
                    new_id("llm_invocation_attempt_version")),
                attempt_ordinal=0,
                model_condition=model,
                canonical_request_bytes=request,
                max_response_bytes=self.max_response_bytes,
            )
            try:
                response = bytes(port.request_once(attempt))
            finally:
                port.close()
            result, envelope = _judge_result(response)
            record = {
                "ordinal": ordinal,
                "model": model,
                "max_output_tokens": max_output_tokens,
                "result": result,
                "response_envelope": envelope,
            }
            write_new(call_root / "result.json", record)
            self.successful_calls.append(record)
            return result
        except Exception as exc:
            record = {
                "ordinal": ordinal,
                "model": model,
                "error": type(exc).__name__ + ": " + str(exc),
            }
            write_new(call_root / "failure.json", record)
            self.failed_calls.append(record)
            if isinstance(exc, JudgeTransportError):
                raise
            raise JudgeTransportError(record["error"]) from exc

    async def create_json_response_async(
            self, *, model: str, system_prompt: str, user_prompt: str,
            max_output_tokens: int = 1024) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request_sync,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=max_output_tokens,
        )


__all__ = (
    "JudgeTransportError", "LocalProcessJudge", "_canonical_request",
    "_judge_result",
)
