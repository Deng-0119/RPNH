"""Launcher composition for one private adapter and neutral input port."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from cpn.rpnh.llm_contracts import LLMInputPort, LLMInputTarget

from ._common import AdapterConfigError
from .config import (
    LLMExecutionConfigError,
    LLMExecutionSelection,
    load_llm_execution_selection,
)


class BoundLLMInputPort:
    """Neutral port paired with the exact non-secret policy that built it."""

    __slots__ = ("_port", "_policy_bytes")

    def __init__(self, port: LLMInputPort, policy: Mapping[str, object]) -> None:
        if not isinstance(policy, Mapping):
            raise TypeError("bound input port requires an execution policy")
        self._port = port
        self._policy_bytes = json.dumps(
            dict(policy), ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    @property
    def execution_policy(self) -> dict[str, object]:
        return json.loads(self._policy_bytes)

    def request_once(self, attempt):
        return self._port.request_once(attempt)

    def request_once_interruptible(self, attempt, *, interruption_requested):
        interruptible = getattr(self._port, "request_once_interruptible", None)
        if callable(interruptible):
            return interruptible(
                attempt, interruption_requested=interruption_requested)
        if interruption_requested():
            from cpn.rpnh.llm_contracts import LLMInputPortInterrupted
            raise LLMInputPortInterrupted(submission_state="not_submitted")
        return self._port.request_once(attempt)

    def close(self) -> None:
        self._port.close()


def bound_llm_execution_policy(port: LLMInputPort) -> dict[str, object]:
    policy = getattr(port, "execution_policy", None)
    if not isinstance(policy, Mapping):
        raise LLMExecutionConfigError(
            "registered HOST input port lacks exact execution policy")
    return json.loads(json.dumps(
        dict(policy), ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":")))


def _private_kind(path: Path) -> str:
    try:
        document: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterConfigError(
            "adapter private configuration is unavailable or invalid") from exc
    kind = document.get("adapter_kind") if isinstance(document, Mapping) else None
    if kind not in {"external_provider", "local_process"}:
        raise AdapterConfigError("adapter private configuration kind is invalid")
    return str(kind)


def build_llm_input_port(
        selection: LLMExecutionSelection, *, destination_run_root: Path,
) -> LLMInputPort:
    if not isinstance(selection, LLMExecutionSelection):
        raise TypeError("adapter factory requires LLMExecutionSelection")
    if not isinstance(destination_run_root, Path):
        raise TypeError("adapter factory requires a pathlib.Path run root")
    target = selection.input_target
    policy = selection.as_registry_policy()
    try:
        if _private_kind(selection.adapter_config_path) != selection.adapter_kind:
            raise AdapterConfigError(
                "adapter private configuration kind differs from selection")
        common = {
            "model_condition": target.model_condition,
            "max_output_tokens": target.max_output_tokens,
            "timeout_seconds": selection.timeout_seconds,
            "max_response_bytes": target.max_response_bytes,
            "config_path": selection.adapter_config_path,
            "destination_run_root": destination_run_root,
        }
        if selection.adapter_kind == "external_provider":
            from .external_provider import ExternalProviderInputPort
            port: LLMInputPort = ExternalProviderInputPort(
                **common)
        else:
            from .local_process import LocalProcessInputPort
            port = LocalProcessInputPort(**common)
    except AdapterConfigError as exc:
        raise LLMExecutionConfigError(str(exc)) from exc
    if selection.as_registry_policy() != policy:
        port.close()
        raise LLMExecutionConfigError(
            "adapter configuration changed while composing the input port")
    return BoundLLMInputPort(port, policy)


def load_llm_input_port(
        path: Path, *, destination_run_root: Path,
) -> tuple[LLMInputTarget, LLMInputPort]:
    selection = load_llm_execution_selection(path)
    port = build_llm_input_port(
        selection, destination_run_root=destination_run_root)
    return selection.input_target, port


__all__ = [
    "BoundLLMInputPort", "bound_llm_execution_policy",
    "build_llm_input_port", "load_llm_input_port",
]
