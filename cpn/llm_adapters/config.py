"""Launcher-only selection for one private LLM adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.runtime_policy import (
    RuntimePolicy, runtime_policy_from_document,
)

from ._external_provider_recovery import (
    ExternalProviderRecoveryConfigError,
    recovery_policy_from_document,
)


SCHEMA_VERSION = "llm_execution_selection/v1"


class LLMExecutionConfigError(RuntimeError):
    """The adapter selection is absent, malformed, or inconsistent."""


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LLMExecutionConfigError(f"{label} must be nonempty text")
    return value


def _positive(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LLMExecutionConfigError(f"{label} must be a positive integer")
    return value


def _path_free_argv_profile(value: object, *, label: str) -> list[str]:
    """Retain non-secret CLI semantics while excluding host-local paths."""

    if not isinstance(value, list) or not value:
        raise LLMExecutionConfigError(f"{label} must be a nonempty array")
    result: list[str] = []
    for raw in value:
        token = _text(raw, label=f"{label} item")
        result.append("<local-path>" if Path(token).is_absolute() else token)
    return result


@dataclass(frozen=True, slots=True)
class LLMExecutionSelection:
    input_target: LLMInputTarget
    adapter_kind: str
    adapter_config_path: Path
    timeout_seconds: int
    runtime_policy: RuntimePolicy = RuntimePolicy()

    def as_registry_policy(self) -> dict[str, object]:
        """Return non-secret execution policy/provenance without local paths."""

        try:
            private: Any = json.loads(self.adapter_config_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMExecutionConfigError(
                "adapter private configuration is unavailable or invalid") from exc
        if (not isinstance(private, Mapping)
                or private.get("adapter_kind") != self.adapter_kind
                or private.get("model_condition")
                != self.input_target.model_condition):
            raise LLMExecutionConfigError(
                "adapter private configuration differs from selection")
        try:
            adapter_stat = self.adapter_config_path.stat()
        except OSError as exc:
            raise LLMExecutionConfigError(
                "adapter private configuration is unavailable") from exc
        config_revision = {
            "byte_count": adapter_stat.st_size,
            "modified_ns": adapter_stat.st_mtime_ns,
        }
        provenance: list[dict[str, object]] = []
        schema_version = _text(
            private.get("schema_version"), label="adapter schema_version")
        if self.adapter_kind == "external_provider":
            routes = private.get("routes")
            if not isinstance(routes, list) or len(routes) != 1:
                raise LLMExecutionConfigError(
                    "external adapter provenance requires one route")
            for route in routes:
                if not isinstance(route, Mapping):
                    raise LLMExecutionConfigError(
                        "external adapter provenance route is malformed")
                provenance.append({
                    "route_id": _text(route.get("route_id"), label="route_id"),
                    "provider": _text(route.get("provider"), label="provider"),
                    "backend": _text(route.get("backend"), label="backend"),
                    "protocol": _text(route.get("protocol"), label="protocol"),
                    "endpoint": _text(route.get("endpoint"), label="endpoint"),
                    "transport": urlsplit(_text(
                        route.get("endpoint"), label="endpoint")).scheme,
                    "outbound_model": _text(
                        route.get("outbound_model"), label="outbound_model"),
                    "credential_binding": (
                        dict(route["credential"])
                        if isinstance(route.get("credential"), Mapping)
                        else None),
                    "headers": (
                        dict(route["headers"])
                        if isinstance(route.get("headers"), Mapping)
                        else None),
                })
            try:
                recovery = recovery_policy_from_document(
                    private.get("recovery"))
            except ExternalProviderRecoveryConfigError as exc:
                raise LLMExecutionConfigError(str(exc)) from exc
            adapter_profile: dict[str, object] = {
                "config_schema_version": schema_version,
                "config_revision": config_revision,
                "route_count": len(provenance),
                "recovery": recovery.as_document(),
            }
        else:
            env = private.get("env")
            inherited = private.get("inherit_env")
            if (not isinstance(env, Mapping)
                    or not isinstance(inherited, list)
                    or any(not isinstance(name, str) or not name
                           for name in (*env.keys(), *inherited))):
                raise LLMExecutionConfigError(
                    "local adapter environment provenance is malformed")
            provenance.append({
                "route_id": "local-process",
                "provider": "local",
                "backend": "local_process",
                "transport": "subprocess",
            })
            adapter_profile = {
                "config_schema_version": schema_version,
                "config_revision": config_revision,
                "argv_profile": _path_free_argv_profile(
                    private.get("argv"), label="argv"),
                "probe_argv_profile": _path_free_argv_profile(
                    private.get("probe_argv"), label="probe_argv"),
                "fixed_env_names": sorted(str(name) for name in env),
                "inherited_env_names": [str(name) for name in inherited],
            }
        return {
            "adapter_kind": self.adapter_kind,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.input_target.max_output_tokens,
            "max_response_bytes": self.input_target.max_response_bytes,
            **({"context_window_tokens":
                self.input_target.context_window_tokens}
               if self.input_target.context_window_tokens is not None else {}),
            **({"context_compaction_retained_tokens":
                self.input_target.context_compaction_retained_tokens}
               if (self.input_target.context_compaction_retained_tokens
                   is not None) else {}),
            "route_provenance": provenance,
            "adapter_profile": adapter_profile,
            "runtime": self.runtime_policy.as_document(),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "LLMExecutionSelection":
        required_fields = {
            "schema_version", "adapter_kind", "model_condition",
            "adapter_config_path", "timeout_seconds", "max_output_tokens",
            "max_response_bytes",
        }
        optional_fields = {
            "context_window_tokens", "context_compaction_retained_tokens",
            "runtime"}
        if (not isinstance(value, Mapping)
                or not required_fields.issubset(value)
                or set(value) - required_fields - optional_fields):
            raise LLMExecutionConfigError(
                "LLM execution selection fields are not current")
        if value["schema_version"] != SCHEMA_VERSION:
            raise LLMExecutionConfigError(
                "LLM execution selection schema_version is not current")
        adapter_kind = _text(value["adapter_kind"], label="adapter_kind")
        if adapter_kind not in {"external_provider", "local_process"}:
            raise LLMExecutionConfigError("adapter_kind is unsupported")
        path = Path(_text(
            value["adapter_config_path"], label="adapter_config_path"))
        if not path.is_absolute():
            raise LLMExecutionConfigError(
                "adapter_config_path must be absolute")
        try:
            input_target = LLMInputTarget(
                model_condition=_text(
                    value["model_condition"], label="model_condition"),
                max_output_tokens=_positive(
                    value["max_output_tokens"], label="max_output_tokens"),
                max_response_bytes=_positive(
                    value["max_response_bytes"], label="max_response_bytes"),
                context_window_tokens=(
                    _positive(
                        value["context_window_tokens"],
                        label="context_window_tokens")
                    if "context_window_tokens" in value else None),
                context_compaction_retained_tokens=(
                    _positive(
                        value["context_compaction_retained_tokens"],
                        label="context_compaction_retained_tokens")
                    if "context_compaction_retained_tokens" in value
                    else None),
            )
        except TypeError as exc:
            raise LLMExecutionConfigError(str(exc)) from exc
        return cls(
            input_target=input_target,
            adapter_kind=adapter_kind,
            adapter_config_path=path,
            timeout_seconds=_positive(
                value["timeout_seconds"], label="timeout_seconds"),
            runtime_policy=runtime_policy_from_document(value.get("runtime")),
        )


def load_llm_execution_selection(path: Path) -> LLMExecutionSelection:
    if not isinstance(path, Path):
        raise TypeError("LLM execution config path must be pathlib.Path")
    try:
        document: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMExecutionConfigError(
            "LLM execution selection is unavailable or invalid") from exc
    if isinstance(document, Mapping):
        raw_adapter_path = document.get("adapter_config_path")
        if isinstance(raw_adapter_path, str):
            adapter_path = Path(raw_adapter_path).expanduser()
            if not adapter_path.is_absolute():
                document = dict(document)
                document["adapter_config_path"] = str(
                    (path.resolve().parent / adapter_path).resolve())
    return LLMExecutionSelection.from_mapping(document)


__all__ = [
    "LLMExecutionConfigError", "LLMExecutionSelection",
    "load_llm_execution_selection",
]
