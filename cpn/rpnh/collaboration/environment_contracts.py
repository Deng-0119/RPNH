"""Closed immutable data contracts for local environment preparation.

These are inert values, never approval, Registry references or HOST authority.
Every digest is SHA-256 of canonical bytes and is kept outside its own document.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from jsonschema import Draft7Validator, validators

from .share_packages import (
    DEFAULT_LIMITS, DIGEST, DRAFT7, PackageError, canonical_bytes, sha256, strict_json,
)


class EnvironmentContractError(PackageError):
    def __init__(self, reason_code: str, message: str, *, artifact_path=None):
        super().__init__(reason_code, message, artifact_path=artifact_path)
        self.reason_code = reason_code


StrictValidator = validators.extend(Draft7Validator, type_checker=
    Draft7Validator.TYPE_CHECKER.redefine("integer", lambda checker, value: type(value) is int))


def schema_object(properties, *, required=None):
    return {"type": "object", "additionalProperties": False,
            "properties": properties,
            "required": list(properties) if required is None else required}


def schema_array(items, *, maximum=128, minimum=0, unique=False):
    result = {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
    if unique:
        result["uniqueItems"] = True
    return result


def nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


DIGEST_SCHEMA = {"type": "string", "pattern": DIGEST}
KEY_SCHEMA = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "maxLength": 120}
TEXT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 1024}
SCOPED_REQUIREMENT_SCHEMA = schema_object({
    "manifest_digest": DIGEST_SCHEMA, "entry_id": KEY_SCHEMA, "requirement_id": KEY_SCHEMA,
})
REQUIREMENT_ARTIFACT_SCHEMA = schema_object({
    "manifest_digest": DIGEST_SCHEMA, "entry_id": KEY_SCHEMA,
    "artifact_path": {"type": "string", "minLength": 1, "maxLength": 240},
    "artifact_digest": DIGEST_SCHEMA,
})
PACKAGE_TARGET_SCHEMA = schema_object({
    "package_lock_digest": DIGEST_SCHEMA, "root_manifest_digest": DIGEST_SCHEMA,
    "root_archive_digest": DIGEST_SCHEMA, "entry_id": KEY_SCHEMA,
    "requirement_artifacts": schema_array(REQUIREMENT_ARTIFACT_SCHEMA, minimum=1, maximum=64, unique=True),
    "requirements_digest": DIGEST_SCHEMA,
})


def validate_document(document, schema, *, code="INVALID_ENVIRONMENT_CONTRACT"):
    error = next(StrictValidator(schema).iter_errors(document), None)
    if error is not None:
        raise EnvironmentContractError(code, "document does not satisfy its closed environment contract")


def _plain_bytes(document):
    # Reject custom mappings/callbacks and oversized structures before encoding.
    stack, count = [(document, 0)], 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > DEFAULT_LIMITS.json_nodes or depth > DEFAULT_LIMITS.json_depth:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "environment JSON structure limit")
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "JSON keys must be strings")
            stack.extend((child, depth + 1) for child in value.values())
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "only standard JSON values are accepted")
    try:
        return canonical_bytes(document)
    except (ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "expected finite JSON data") from exc


@dataclass(frozen=True, slots=True)
class StrictEnvironmentDTO:
    """Subclasses supply a closed Draft7 SCHEMA and optional _validate hook."""
    payload: bytes
    SCHEMA: ClassVar[dict[str, Any] | None] = None

    def __post_init__(self):
        if type(self.payload) is not bytes:
            raise TypeError("environment DTO payload must be immutable bytes")
        if len(self.payload) > DEFAULT_LIMITS.artifact_bytes:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "environment document byte limit")
        if self.SCHEMA is None:
            raise TypeError("a concrete closed environment schema is required")
        try:
            document = strict_json(self.payload, path="environment-document")
        except PackageError as exc:
            raise EnvironmentContractError(exc.code, exc.message) from exc
        validate_document(document, self.SCHEMA)
        self._validate(document)
        object.__setattr__(self, "payload", canonical_bytes(document))

    def _validate(self, document):
        pass

    @classmethod
    def from_dict(cls, document):
        return cls(_plain_bytes(document))

    @classmethod
    def from_bytes(cls, payload):
        return cls(payload)

    def to_dict(self):
        return strict_json(self.payload, path="environment-document")

    def to_bytes(self):
        return self.payload

    @property
    def digest(self):
        return sha256(self.payload)


class PackageTarget(StrictEnvironmentDTO):
    SCHEMA = PACKAGE_TARGET_SCHEMA
    __slots__ = ()

    def _validate(self, document):
        from .share_packages import safe_path
        rows = document["requirement_artifacts"]
        ordered = sorted(rows, key=lambda row: (row["manifest_digest"], row["entry_id"], row["artifact_path"]))
        if rows != ordered or len({(row["manifest_digest"], row["entry_id"]) for row in rows}) != len(rows):
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "requirements inventory is not unique and canonical")
        for row in rows:
            safe_path(row["artifact_path"])
        if document["requirements_digest"] != sha256(canonical_bytes(rows)):
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "requirements inventory digest differs")

    @property
    def requirements_digest(self):
        return self.to_dict()["requirements_digest"]
