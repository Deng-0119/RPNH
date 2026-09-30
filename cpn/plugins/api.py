"""Native plugin author API. No Registry handles or executable JSON locators.

Handlers are trusted, importable Python functions. One handler invocation is one
admitted Petri operation, not a private agent loop. Compose additional actions as
operations; do not call other handlers or perform hidden retries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import pickle
from pathlib import Path
import json
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator

API_VERSION = "rpnh/plugin/v1"
_SYMBOL = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class PluginError(ValueError):
    """Invalid plugin/configuration; no execution is authorized by this error."""


def json_copy(value: Any) -> Any:
    """JSON round trip without coercing keys, bytes, NaN or arbitrary objects."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise PluginError("JSON keys must be strings")
        value = {key: json_copy(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [json_copy(item) for item in value]
    elif value is not None and type(value) not in (str, bool, int, float):
        raise PluginError("plugin declarations and values must be JSON data")
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise PluginError("plugin values must be finite JSON") from exc


def canonical(value: Any) -> bytes:
    return json.dumps(json_copy(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def frozen(value: Any) -> Any:
    value = json_copy(value)
    if isinstance(value, dict):
        return MappingProxyType({k: frozen(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(frozen(v) for v in value)
    return value


def symbol(value: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise PluginError("plugin/operation/resource name must be a lowercase identifier")
    return value


def version(value: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise PluginError("version must be an explicit nonempty version identifier")
    return value


def checked_schema(value: Mapping[str, Any]) -> Mapping[str, Any]:
    document = json_copy(value)
    if not isinstance(document, dict):
        raise PluginError("schema must be a JSON object")
    if document.get("$schema", "http://json-schema.org/draft-07/schema#") != (
            "http://json-schema.org/draft-07/schema#"):
        raise PluginError("native plugin v1 requires JSON Schema draft-07")
    def walk(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key == "$ref" and (not isinstance(item, str) or not item.startswith("#/")):
                    raise PluginError("external schema references are not supported")
                if key in {"$dynamicRef", "$recursiveRef"}:
                    raise PluginError("unsupported schema reference dialect")
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(document)
    try:
        Draft7Validator.check_schema(document)
    except Exception as exc:
        raise PluginError("invalid plugin JSON schema") from exc
    return frozen(document)


def validate(schema: Mapping[str, Any], value: Any) -> Any:
    result = json_copy(value)
    try:
        Draft7Validator(json_copy(schema)).validate(result)
    except Exception as exc:
        # Do not include a credential-bearing instance in diagnostics.
        raise PluginError("value does not satisfy the declared plugin schema") from exc
    return result


@dataclass(frozen=True, slots=True)
class PluginResource:
    name: str
    payload: bytes
    media_type: str = "text/plain"

    def __post_init__(self):
        symbol(self.name)
        if not isinstance(self.payload, bytes) or not self.payload:
            raise PluginError("resource payload must be nonempty immutable bytes")
        if not isinstance(self.media_type, str) or "/" not in self.media_type:
            raise PluginError("resource media type is required")

    def descriptor(self) -> dict[str, Any]:
        return {"name": self.name, "media_type": self.media_type,
                "size_bytes": len(self.payload),
                "sha256": hashlib.sha256(self.payload).hexdigest()}


def implementation_identity(handler):
    """Pin the handler module bytes, not an executable locator from task JSON.

    Transitive dependencies still belong to the operator's installed environment.
    This detects same-version edits to the actual handler module on resume/spawn.
    """
    if not inspect.isfunction(handler) or "<" in handler.__qualname__:
        raise PluginError("handler must be an importable Python function")
    try:
        pickle.dumps(handler)
        source = inspect.getsourcefile(handler)
        payload = Path(source).read_bytes() if source else None
    except (TypeError, OSError, pickle.PicklingError, AttributeError) as exc:
        raise PluginError("handler requires an installed importable source module") from exc
    if payload is None:
        raise PluginError("handler source module is unavailable")
    return {"identity": handler.__module__ + "." + handler.__qualname__,
            "module_sha256": hashlib.sha256(payload).hexdigest()}


@dataclass(frozen=True, slots=True)
class PluginOperation:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    handler: Callable[..., Any] = field(repr=False, compare=False)
    resources: tuple[str, ...] = ()
    effect: str = "pure"
    timeout_seconds: int = 60
    max_result_bytes: int = 1024 * 1024
    implementation: Mapping[str, str] = field(init=False, repr=False)

    def __post_init__(self):
        symbol(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            raise PluginError("operation description is required")
        if (not callable(self.handler) or not getattr(self.handler, "__module__", None)
                or "<" in getattr(self.handler, "__qualname__", "<")):
            raise PluginError("handler must be an importable top-level Python function")
        if self.effect not in {"pure", "external_read", "external_write"}:
            raise PluginError("unknown effect declaration")
        if (type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 7200
                or type(self.max_result_bytes) is not int
                or not 1 <= self.max_result_bytes <= 16 * 1024 * 1024):
            raise PluginError("invalid registered execution limits")
        if (not isinstance(self.resources, tuple)
                or len(set(self.resources)) != len(self.resources)):
            raise PluginError("operation resources must be distinct names")
        for name in self.resources:
            symbol(name)
        object.__setattr__(self, "implementation", frozen(implementation_identity(self.handler)))
        object.__setattr__(self, "resources", tuple(sorted(self.resources)))
        object.__setattr__(self, "input_schema", checked_schema(self.input_schema))
        object.__setattr__(self, "output_schema", checked_schema(self.output_schema))

    def descriptor(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "implementation": json_copy(self.implementation),
                "input_schema": json_copy(self.input_schema),
                "output_schema": json_copy(self.output_schema),
                "resources": list(self.resources), "effect": self.effect,
                "timeout_seconds": self.timeout_seconds,
                "max_result_bytes": self.max_result_bytes}


@dataclass(frozen=True, slots=True)
class PluginDefinition:
    name: str
    version: str
    operations: tuple[PluginOperation, ...]
    config_schema: Mapping[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False})
    resources: tuple[PluginResource, ...] = ()
    requires: Mapping[str, str] = field(default_factory=dict)
    api_version: str = API_VERSION

    def __post_init__(self):
        symbol(self.name)
        version(self.version)
        if self.api_version != API_VERSION:
            raise PluginError("unsupported native plugin API version")
        for items, cls, label in ((self.operations, PluginOperation, "operations"),
                                  (self.resources, PluginResource, "resources")):
            if (not isinstance(items, tuple) or any(not isinstance(x, cls) for x in items)
                    or len({x.name for x in items}) != len(items)):
                raise PluginError(f"plugin {label} must have distinct typed entries")
        if not self.operations:
            raise PluginError("plugin must contribute an operation")
        available = {x.name for x in self.resources}
        if any(set(op.resources) - available for op in self.operations):
            raise PluginError("operation refers to an undeclared resource")
        dependencies = json_copy(self.requires)
        if not isinstance(dependencies, dict):
            raise PluginError("requires must map plugin names to exact versions")
        for name, revision in dependencies.items():
            symbol(name); version(revision)
        if self.name in dependencies:
            raise PluginError("plugin cannot depend on itself")
        object.__setattr__(self, "requires", frozen(dependencies))
        object.__setattr__(self, "config_schema", checked_schema(self.config_schema))

    def descriptor(self) -> dict[str, Any]:
        return {"api_version": self.api_version, "name": self.name,
                "version": self.version, "requires": json_copy(self.requires),
                "config_schema": json_copy(self.config_schema),
                "operations": [x.descriptor() for x in sorted(self.operations, key=lambda x: x.name)],
                "resources": [x.descriptor() for x in sorted(self.resources, key=lambda x: x.name)]}


@dataclass(frozen=True, slots=True)
class ResourceView:
    """Already acknowledged exact bytes; never an arbitrary host path."""
    name: str
    payload: bytes
    media_type: str
    resource_id: str
    resource_version_id: str

    def text(self) -> str:
        return self.payload.decode("utf-8")


class PluginContext:
    """Worker-local inputs and cooperative cancellation; no owner or gateway."""
    __slots__ = ("config", "resources", "operation_id", "invocation_id",
                 "firing_id", "call_id", "_cancel", "_deadline")

    def __init__(self, *, config, resources, operation_id, invocation_id,
                 firing_id, call_id, cancel, deadline):
        self.config = frozen(config)
        self.resources = MappingProxyType({r.name: r for r in resources})
        self.operation_id = operation_id
        self.invocation_id = invocation_id
        self.firing_id = firing_id
        self.call_id = call_id
        self._cancel, self._deadline = cancel, deadline

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def remaining_seconds(self) -> float:
        import time
        return max(0.0, self._deadline - time.monotonic())

    def check_cancelled(self) -> None:
        if self.cancelled or self.remaining_seconds <= 0:
            raise PluginError("plugin execution cancelled or deadline exceeded")
