"""Finite data contracts for opt-in static candidate preparation.

These values freeze JSON; they do not establish Registry authority, prepare a
provider, or authorize graph publication. The fixed same-cut consumer must
validate the complete records and their real dependencies before use.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math

from .models import VersionRef
from .identities import TypedId
from .schema_catalog import canonical_json
from .strict_contracts import ref_payload


PLAN_TYPE = "collaboration_candidate_plan/v1"
MANIFEST_TYPE = "runtime_binding_manifest/v1"
READINESS_TYPE = "binding_readiness/v1"
PLAN_SCHEMA = "registry_v1/" + PLAN_TYPE
MANIFEST_SCHEMA = "registry_v1/" + MANIFEST_TYPE
READINESS_SCHEMA = "registry_v1/" + READINESS_TYPE
MANIFEST_FAMILY = "runtime_binding_manifest/"


def freeze_candidate_document(document):
    """Encode a detached standard-JSON object, without default=str coercions.

    Fixed API fields must first explicitly serialize their typed refs/tuples.
    Arbitrary Python objects, non-string keys and non-finite numbers are never
    converted by caller-defined methods. Aliased JSON subtrees are allowed;
    cyclic Python containers are not JSON.
    """
    if type(document) is not dict:
        raise TypeError("candidate document must be a standard JSON object")
    active = set()
    def checked_copy(value):
        kind = type(value)
        if value is None or kind is str or kind is bool or kind is int:
            return value
        if kind is float:
            if not math.isfinite(value):
                raise ValueError("candidate JSON numbers must be finite")
            return value
        if kind is not dict and kind is not list:
            raise TypeError("candidate JSON contains an unsupported Python value")
        identity = id(value)
        if identity in active:
            raise ValueError("candidate JSON cannot contain cyclic containers")
        active.add(identity)
        try:
            if kind is dict:
                result = {}
                for key, child in value.items():
                    if type(key) is not str:
                        raise TypeError("candidate JSON object keys must be strings")
                    result[key] = checked_copy(child)
                return result
            else:
                return [checked_copy(child) for child in value]
        finally:
            active.remove(identity)
    detached = checked_copy(document)
    # Every encoded value belongs to our checked builtin tree. A concurrent
    # mutation of the caller's tree cannot insert an unchecked value between
    # validation and encoding. This is not an atomic snapshot of the caller.
    return json.dumps(detached, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False)


def _decode_candidate_document(text):
    if type(text) is not str:
        raise TypeError("candidate encoded document must be a JSON string")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("candidate JSON cannot contain duplicate keys")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("candidate JSON numbers must be finite")
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    return json.loads(freeze_candidate_document(value))


def is_runtime_manifest_type(entity_type):
    """Classify the reserved family independently of supported-version decode."""
    return isinstance(entity_type, str) and entity_type.startswith(MANIFEST_FAMILY)


def _copy_record_ref(ref, expected_type):
    """Validate concrete immutable ref data before any ref string conversion."""
    if type(ref) is not VersionRef or type(ref.entity_type) is not str or ref.entity_type != expected_type:
        raise TypeError("runtime draft requires a standard exact VersionRef")
    ids = []
    for identifier, expected_kind in ((ref.entity_id, "resource"), (ref.version_id, "resource_version")):
        if (type(identifier) is not TypedId or type(identifier.kind) is not str
                or type(identifier.value) is not str or identifier.kind != expected_kind):
            raise TypeError("runtime draft requires standard exact TypedId fields")
        ids.append(TypedId(identifier.kind, identifier.value))
    return VersionRef(ref.entity_type, *ids)


@dataclass(frozen=True, slots=True)
class RuntimeBindingManifestDraft:
    """One finite graph attachment; immutable data, never a Core callback.

    Shape/authority validation is still required through the opt-in catalog
    and fixed transaction gate. Construction alone is not a ready candidate.
    """
    plan_ref: VersionRef
    manifest_ref: VersionRef
    _plan_json: str

    def __post_init__(self):
        plan_ref = _copy_record_ref(self.plan_ref, PLAN_TYPE)
        manifest_ref = _copy_record_ref(self.manifest_ref, MANIFEST_TYPE)
        object.__setattr__(self, "plan_ref", plan_ref)
        object.__setattr__(self, "manifest_ref", manifest_ref)
        document = _decode_candidate_document(self._plan_json)
        if (document.get("schema_version") != PLAN_SCHEMA
                or canonical_json(document.get("plan_ref")) != canonical_json(ref_payload(self.plan_ref))
                or canonical_json(document.get("manifest_ref")) != canonical_json(ref_payload(self.manifest_ref))):
            raise ValueError("runtime draft differs from its exact frozen plan/manifest refs")
        object.__setattr__(self, "_plan_json", freeze_candidate_document(document))

    @classmethod
    def from_document(cls, plan_ref, manifest_ref, document):
        return cls(plan_ref, manifest_ref, freeze_candidate_document(document))

    @property
    def plan(self):
        return json.loads(self._plan_json)
