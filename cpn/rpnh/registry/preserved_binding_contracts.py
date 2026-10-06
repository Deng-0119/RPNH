"""Opt-in v2 preservation data, without adoption/slot publication authority.

Draft7's integer grammar admits 1.0. The finite DTO additionally requires a
builtin positive int for task-control sequence, excluding bool and float. A
future fixed gate/reader must apply this typed contract after schema checking.
These DTOs neither resolve an event nor prove that its net was adopted/current.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .identities import TypedId
from .models import VersionRef
from .runtime_binding_contracts import (
    RuntimeBindingManifestDraft, _copy_record_ref, _decode_candidate_document, freeze_candidate_document,
)
from .schema_catalog import canonical_json
from .strict_contracts import ref_payload

PLAN_V2_TYPE = "collaboration_candidate_plan/v2"
MANIFEST_V2_TYPE = "runtime_binding_manifest/v2"
READINESS_V2_TYPE = "binding_readiness/v2"
PLAN_V2_SCHEMA = "registry_v1/" + PLAN_V2_TYPE
MANIFEST_V2_SCHEMA = "registry_v1/" + MANIFEST_V2_TYPE
READINESS_V2_SCHEMA = "registry_v1/" + READINESS_V2_TYPE


def _copy_id(identifier, kind):
    if (type(identifier) is not TypedId or type(identifier.kind) is not str or identifier.kind != kind
            or type(identifier.value) is not str):
        raise TypeError("preserved basis requires standard exact TypedId fields")
    return TypedId(kind, identifier.value)


def _copy_net_ref(ref):
    if type(ref) is not VersionRef or type(ref.entity_type) is not str or ref.entity_type != "net_instance/v1":
        raise TypeError("preserved basis requires a standard exact net VersionRef")
    return VersionRef("net_instance/v1", _copy_id(ref.entity_id, "net_instance"),
        _copy_id(ref.version_id, "net_instance_version"))


def _parse_ref(value, expected_type, logical_kind, version_kind):
    if (type(value) is not dict or set(value) != {"entity_type", "logical_id", "version_id"}
            or any(type(item) is not str for item in value.values()) or value["entity_type"] != expected_type):
        raise TypeError("preserved selection requires an exact supported ref shape")
    return VersionRef(expected_type, TypedId.parse(value["logical_id"], expected=logical_kind),
        TypedId.parse(value["version_id"], expected=version_kind))


@dataclass(frozen=True, slots=True)
class PreservedBasis:
    """One explicitly selected adoption event/net, with detached typed data."""
    net_ref: VersionRef
    event_id: TypedId
    transaction_id: TypedId
    task_control_sequence: int
    adoption_event_sha256: str

    def __post_init__(self):
        object.__setattr__(self, "net_ref", _copy_net_ref(self.net_ref))
        object.__setattr__(self, "event_id", _copy_id(self.event_id, "event"))
        object.__setattr__(self, "transaction_id", _copy_id(self.transaction_id, "transaction"))
        if type(self.task_control_sequence) is not int or self.task_control_sequence < 1:
            raise TypeError("preserved basis sequence requires a positive builtin integer")
        if type(self.adoption_event_sha256) is not str or re.fullmatch(r"[a-f0-9]{64}", self.adoption_event_sha256) is None:
            raise ValueError("preserved basis requires a canonical lowercase SHA-256")

    def to_dict(self):
        return {"kind": "exact_adopted_net", "net_ref": ref_payload(self.net_ref),
            "adoption_event": {"event_type": "net_adopted/v1", "event_id": str(self.event_id),
                "transaction_id": str(self.transaction_id), "task_control_sequence": self.task_control_sequence},
            "adoption_event_sha256": self.adoption_event_sha256}

    @classmethod
    def from_document(cls, document):
        value = json.loads(freeze_candidate_document(document))
        if set(value) != {"kind", "net_ref", "adoption_event", "adoption_event_sha256"} or value["kind"] != "exact_adopted_net":
            raise ValueError("preserved basis requires its closed exact-adopted-net shape")
        event = value["adoption_event"]
        if (type(event) is not dict or set(event) != {"event_type", "event_id", "transaction_id", "task_control_sequence"}
                or event["event_type"] != "net_adopted/v1"
                or type(event["event_id"]) is not str or type(event["transaction_id"]) is not str):
            raise ValueError("preserved basis requires one exact adoption event identity")
        return cls(_parse_ref(value["net_ref"], "net_instance/v1", "net_instance", "net_instance_version"),
            TypedId.parse(event["event_id"], expected="event"), TypedId.parse(event["transaction_id"], expected="transaction"),
            event["task_control_sequence"], value["adoption_event_sha256"])


def _validate_preserved_fields(document):
    if "preserved_basis" not in document or type(document.get("preserved_slot_refs")) is not dict:
        raise ValueError("v2 requires explicit preserved slots and basis fields")
    slots, basis = document["preserved_slot_refs"], document["preserved_basis"]
    for ref in slots.values():
        _parse_ref(ref, "logical_artifact_slot/v1", "logical_slot", "logical_slot_version")
    if not slots:
        if basis is not None:
            raise ValueError("empty preserved slots require null basis")
    else:
        if basis is None:
            raise ValueError("nonempty preserved slots require an explicit basis")
        PreservedBasis.from_document(basis)


@dataclass(frozen=True, slots=True)
class RuntimeBindingManifestDraftV2(RuntimeBindingManifestDraft):
    """Data-only v2 pair; not an enabled candidate producer or exact reader.

    This checks typed preservation data and the exact v2 self/ref pair. The
    complete schema, event digest/lineage, same-cut current-first rule and slot
    authority still require separate fixed consumers before any publication.
    """
    def __post_init__(self):
        plan = _copy_record_ref(self.plan_ref, PLAN_V2_TYPE)
        manifest = _copy_record_ref(self.manifest_ref, MANIFEST_V2_TYPE)
        document = _decode_candidate_document(self._plan_json)
        if (document.get("schema_version") != PLAN_V2_SCHEMA
                or canonical_json(document.get("plan_ref")) != canonical_json(ref_payload(plan))
                or canonical_json(document.get("manifest_ref")) != canonical_json(ref_payload(manifest))):
            raise ValueError("v2 draft differs from its exact plan/manifest refs")
        _validate_preserved_fields(document)
        object.__setattr__(self, "plan_ref", plan)
        object.__setattr__(self, "manifest_ref", manifest)
        object.__setattr__(self, "_plan_json", freeze_candidate_document(document))

    @property
    def preserved_basis(self):
        value = self.plan["preserved_basis"]
        return None if value is None else PreservedBasis.from_document(value)
