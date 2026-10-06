"""Inert portable-package contracts and bounded strict JSON primitives.

This module has no Registry, registration, plugin or execution dependency.
Digests name raw bytes; a manifest digest is never an archive digest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

PACKAGE_SCHEMA = "rpnh/share_package/v1"
PREVIEW_SCHEMA = "rpnh/package_preview/v1"
LOCK_SCHEMA = "rpnh/package_resolution_lock/v1"
PARSER_CONTRACT = "rpnh/package_preview_parser/v1"
RESOLVER_CONTRACT = "rpnh/package_resolver/v1"
DRAFT7 = "http://json-schema.org/draft-07/schema#"
MODULE_SCHEMA = "rpnh/module_declaration/v1"
SCHEMA_ID = r"^[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)+/v[1-9][0-9]*$"
DIGEST = r"^[0-9a-f]{64}$"
VERSION = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
MANIFEST_PATH = "manifest.json"


@dataclass(frozen=True, slots=True)
class PackageLimits:
    archive_bytes: int = 32 * 1024 * 1024
    artifact_bytes: int = 4 * 1024 * 1024
    expanded_bytes: int = 32 * 1024 * 1024
    members: int = 128
    compression_ratio: int = 200
    json_depth: int = 64
    json_nodes: int = 100_000
    packages: int = 64
    closure_bytes: int = 64 * 1024 * 1024
    closure_expanded_bytes: int = 64 * 1024 * 1024
    dependency_edges: int = 256
    dependency_depth: int = 16

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            if type(getattr(self, field)) is not int or getattr(self, field) < 1:
                raise ValueError("package limits must be positive integers")


DEFAULT_LIMITS = PackageLimits()


class PackageError(ValueError):
    """A stable diagnostic without echoing untrusted material contents."""
    def __init__(self, code: str, message: str, *, artifact_path: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.artifact_path = artifact_path

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message,
                "artifact_path": self.artifact_path}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    """Canonical output domain: sorted ASCII JSON, compact, no trailing LF."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def strict_json(payload: bytes, *, path: str, limits=DEFAULT_LIMITS) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackageError("INVALID_JSON", "duplicate JSON object key", artifact_path=path)
            result[key] = value
        return result

    def constant(_):
        raise PackageError("INVALID_JSON", "non-finite JSON number", artifact_path=path)

    try:
        # UTF-8 BOM is deliberately not accepted; no encoding guessing.
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError("INVALID_JSON", "expected strict UTF-8 JSON", artifact_path=path) from exc
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > limits.json_nodes or depth > limits.json_depth:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "JSON structure limit", artifact_path=path)
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise PackageError("INVALID_JSON", "unpaired Unicode surrogate", artifact_path=path) from exc
        elif type(item) is float and not math.isfinite(item):
            constant(None)
        elif type(item) is dict:
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
    return value


def safe_path(path: str) -> str:
    """Portable ASCII relative file names; reject rather than normalize."""
    if (not isinstance(path, str) or not path or len(path) > 240
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                   or part in {".", ".."} or part.endswith((".", " "))
                   or part.split(".")[0].upper() in
                   {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)},
                    *{f"LPT{i}" for i in range(1, 10)}}
                   for part in path.split("/"))):
        raise PackageError("UNSAFE_ARCHIVE_PATH", "noncanonical portable member path")
    return path


def _object(properties, *, required=None):
    return {"type": "object", "additionalProperties": False,
            "properties": properties,
            "required": list(properties) if required is None else required}


def _array(item, *, minimum=0, maximum=128, unique=False):
    result = {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}
    if unique:
        result["uniqueItems"] = True
    return result


def manifest_schema() -> dict[str, Any]:
    """Return a fresh complete schema for this deliberately bounded v1 slice.

    Unsupported future features (policy mutation, optional features, multiple
    entries) cannot be silently interpreted as supported by this parser.
    """
    text = {"type": "string", "minLength": 1, "maxLength": 1024}
    identity = {"type": "string", "pattern": r"^[a-z][a-z0-9_.-]*(?:/[a-z][a-z0-9_.-]*)+$", "maxLength": 160}
    key = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "maxLength": 120}
    digest = {"type": "string", "pattern": DIGEST}
    schema_id = {"type": "string", "pattern": SCHEMA_ID, "maxLength": 240}
    path = {"type": "string", "minLength": 1, "maxLength": 240}
    nullable = lambda value: {"anyOf": [value, {"type": "null"}]}
    source_ref = _object({
        "schema_version": {"const": "rpnh/collaboration/source_version_ref/v1"},
        "source_id": text,
        "ref": _object({"entity_type": text, "logical_id": text, "version_id": text}),
    })
    completion = _object({"success_exit": key, "failure_exits": _array(key, unique=True),
        "acceptor_requirement_ids": _array(key, unique=True),
        "open_obligations": {"type": "array", "maxItems": 0}})
    artifact = _object({"path": path, "media_type": {"enum": ["application/json", "application/schema+json", "text/plain", "text/markdown"]},
        "bytes": {"type": "integer", "minimum": 0, "maximum": DEFAULT_LIMITS.artifact_bytes},
        "sha256": digest, "role": {"enum": ["declaration", "schema", "knowledge", "fixture", "document", "license"]},
        "license_id": key, "disclosure": {"enum": ["public", "named_recipients"]}})
    dependency = _object({"dependency_id": key, "package_id": identity,
        "manifest_digest": nullable(digest), "version_range": nullable(text),
        "required": {"const": True}, "acquisition_hint": nullable(text)})
    dependency["anyOf"] = [{"properties": {"manifest_digest": digest}},
                            {"properties": {"version_range": text}}]
    properties = {
        "schema_version": {"const": PACKAGE_SCHEMA}, "package_id": identity,
        "version": {"type": "string", "pattern": VERSION, "maxLength": 64},
        "entries": _array(_object({"entry_id": key, "kind": {"const": "closed_module"},
            "declaration_path": path, "declaration_schema": {"const": MODULE_SCHEMA},
            "input_schema_ids": _array(schema_id, unique=True),
            "output_schema_ids": _array(schema_id, minimum=1, unique=True),
            "completion_contract": completion}), minimum=1, maximum=1),
        "artifacts": _array(artifact, minimum=1, maximum=127),
        "origin": _object({"repository_url": nullable(text), "commit": nullable(text),
            "publisher_claim": text, "source_refs": _array(source_ref)}),
        "provenance": _array(_object({"artifact_path": path,
            "relation": {"enum": ["authored", "copied", "derived"]},
            "origin_ref": nullable(source_ref), "origin_digest": nullable(digest)})),
        "dependencies": _array(dependency, maximum=64),
        "compatibility": _object({"declaration_schemas": _array(schema_id, minimum=1, unique=True),
            "runtime_contracts": _array(text, unique=True), "host_contracts": _array(text, unique=True)}),
        "requirements": _array(_object({"requirement_id": key,
            "kind": {"enum": ["component", "executor", "tool", "analyzer", "terminal", "effect"]},
            "contract_id": text, "required": {"type": "boolean"},
            "effects": _array(text, unique=True), "data_classes": _array(text, unique=True)})),
        "policy_surface": {"type": "array", "maxItems": 0},
        "licenses": _array(_object({"license_id": key, "expression": text,
            "text_paths": _array(path, minimum=1, unique=True),
            "notice_paths": _array(path, unique=True)}), minimum=1),
        "disclosure": _object({"classification": {"enum": ["public", "named_recipients"]},
            "intended_audience": text, "excluded_categories": _array(text, unique=True)}),
    }
    return {"$schema": DRAFT7, "$id": PACKAGE_SCHEMA, **_object(properties)}


@dataclass(frozen=True, slots=True)
class PackagePreview:
    """Immutable verified bytes and a detached report; never a capability."""
    manifest_bytes: bytes
    archive_bytes: bytes
    artifacts: tuple[tuple[str, bytes], ...]
    report_bytes: bytes

    def __post_init__(self):
        if (type(self.manifest_bytes) is not bytes or type(self.archive_bytes) is not bytes
                or type(self.report_bytes) is not bytes or type(self.artifacts) is not tuple
                or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
                       or type(row[1]) is not bytes for row in self.artifacts)):
            raise TypeError("PackagePreview fields must be immutable bytes and tuples")

    @property
    def archive_digest(self) -> str:
        return sha256(self.archive_bytes)

    @property
    def manifest_digest(self) -> str:
        return sha256(self.manifest_bytes)

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_bytes)

    @property
    def package_id(self) -> str:
        return self.manifest["package_id"]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.report_bytes)


@dataclass(frozen=True, slots=True)
class PackageResolutionLock:
    """Exact offline closure, not installation, authorization or readiness."""
    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes:
            raise TypeError("PackageResolutionLock payload must be immutable bytes")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.payload)

    def to_bytes(self) -> bytes:
        return self.payload

    @property
    def package_lock_digest(self) -> str:
        return sha256(self.payload)
