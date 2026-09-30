"""Launcher-only selection for one private LLM adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
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


SCHEMA_VERSION = "llm_execution_selection/v2"
LEGACY_SCHEMA_VERSION = "llm_execution_selection/v1"
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*")


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


def _optional_identifier(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value, label=label)
    if _IDENTIFIER.fullmatch(text) is None:
        raise LLMExecutionConfigError(f"{label} must be a safe identifier")
    return text


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
    physical_profile: str | None = None
    logical_selection_id: str | None = None
    reasoning_effort: str | None = None
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None

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
        private_effort = private.get("reasoning_effort")
        if ("reasoning_effort" in private
                and private_effort != self.reasoning_effort):
            raise LLMExecutionConfigError(
                "adapter reasoning effort differs from selection")
        if ("reasoning_effort" not in private
                and self.reasoning_effort is not None):
            raise LLMExecutionConfigError(
                "adapter reasoning effort is absent from selection")
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
        policy: dict[str, object] = {
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
        if self.physical_profile is not None:
            policy["profile_identity"] = {
                "physical_profile": self.physical_profile,
                "logical_selection_id": self.logical_selection_id,
            }
        if self.reasoning_effort is not None:
            policy["reasoning_effort"] = {
                "selected": self.reasoning_effort,
                "supported": list(self.supported_reasoning_efforts),
                "default": self.default_reasoning_effort,
            }
        return policy

    @classmethod
    def from_mapping(cls, value: object) -> "LLMExecutionSelection":
        required_fields = {
            "schema_version", "adapter_kind", "model_condition",
            "adapter_config_path", "timeout_seconds", "max_output_tokens",
            "max_response_bytes",
        }
        legacy_optional_fields = {
            "context_window_tokens", "context_compaction_retained_tokens",
            "runtime"}
        current_fields = {
            "physical_profile", "logical_selection_id", "reasoning_effort",
            "supported_reasoning_efforts", "default_reasoning_effort"}
        if not isinstance(value, Mapping):
            raise LLMExecutionConfigError(
                "LLM execution selection fields are not current")
        version = value.get("schema_version")
        if version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
            raise LLMExecutionConfigError(
                "LLM execution selection schema_version is unsupported")
        allowed_fields = required_fields | legacy_optional_fields
        if version == SCHEMA_VERSION:
            allowed_fields |= current_fields
        if (not required_fields.issubset(value)
                or set(value) - allowed_fields
                or (version == SCHEMA_VERSION
                    and not current_fields.issubset(value))):
            raise LLMExecutionConfigError(
                "LLM execution selection fields are not current")
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
        physical_profile = None
        logical_selection_id = None
        reasoning_effort = None
        supported_reasoning_efforts: tuple[str, ...] = ()
        default_reasoning_effort = None
        if version == SCHEMA_VERSION:
            physical_profile = _optional_identifier(
                value["physical_profile"], label="physical_profile")
            logical_selection_id = _optional_identifier(
                value["logical_selection_id"],
                label="logical_selection_id")
            if physical_profile is None or logical_selection_id is None:
                raise LLMExecutionConfigError(
                    "profile identities must be safe identifiers")
            reasoning_effort = _optional_identifier(
                value["reasoning_effort"], label="reasoning_effort")
            raw_supported = value["supported_reasoning_efforts"]
            if not isinstance(raw_supported, list):
                raise LLMExecutionConfigError(
                    "supported_reasoning_efforts must be an array")
            supported_reasoning_efforts = tuple(
                _optional_identifier(item, label="supported_reasoning_effort")
                for item in raw_supported)
            if (any(item is None for item in supported_reasoning_efforts)
                    or len(set(supported_reasoning_efforts))
                    != len(supported_reasoning_efforts)):
                raise LLMExecutionConfigError(
                    "supported_reasoning_efforts must contain unique safe identifiers")
            default_reasoning_effort = _optional_identifier(
                value["default_reasoning_effort"],
                label="default_reasoning_effort")
            if supported_reasoning_efforts:
                if (reasoning_effort not in supported_reasoning_efforts
                        or default_reasoning_effort
                        not in supported_reasoning_efforts):
                    raise LLMExecutionConfigError(
                        "reasoning effort selection/default is unsupported")
            elif (reasoning_effort is not None
                    or default_reasoning_effort is not None):
                raise LLMExecutionConfigError(
                    "reasoning effort metadata is inconsistent")
        return cls(
            input_target=input_target,
            adapter_kind=adapter_kind,
            adapter_config_path=path,
            timeout_seconds=_positive(
                value["timeout_seconds"], label="timeout_seconds"),
            runtime_policy=runtime_policy_from_document(value.get("runtime")),
            physical_profile=physical_profile,
            logical_selection_id=logical_selection_id,
            reasoning_effort=reasoning_effort,
            supported_reasoning_efforts=tuple(
                item for item in supported_reasoning_efforts
                if item is not None),
            default_reasoning_effort=default_reasoning_effort,
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
                if (document.get("schema_version") == SCHEMA_VERSION
                        and re.fullmatch(
                            r"\.\./adapters/[a-z0-9][a-z0-9._-]*\.json",
                            raw_adapter_path) is None):
                    raise LLMExecutionConfigError(
                        "current adapter_config_path relative layout is invalid")
                document = dict(document)
                document["adapter_config_path"] = str(
                    (path.resolve().parent / adapter_path).resolve())
    return LLMExecutionSelection.from_mapping(document)


__all__ = [
    "LEGACY_SCHEMA_VERSION", "LLMExecutionConfigError",
    "LLMExecutionSelection", "SCHEMA_VERSION",
    "load_llm_execution_selection",
]
