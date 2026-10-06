"""Exact local authority and immutable-output pins for Assembly merge v6."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ..registry._event_store.collaboration_descriptors import exact_descriptor, exact_prepared, readable_payload
from ..registry.event_store import RegistryConflict
from ..registry.identities import TypedId
from ..registry.publication import _version_from_payload
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.resource_service import _publish_private_system
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json
from .assembly_v2 import _material_ref
from .materials import _material, _private_document, _same_json
from .plain_merge_result import _metadata, _resource_bytes, _schema_authority_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef


def owner_ref(binding, core):
    return SourceQualifiedVersionRef.from_dict({"schema_version": "rpnh/collaboration/source_version_ref/v1",
        "source_id": binding["source_id"], "ref": binding["task_ref"]}, catalog=core.catalog)


def canonical_descriptor_at(db, core, reference):
    """Local stronger contract; legacy descriptor readers keep their semantics."""
    raw_ref = reference.to_dict()["ref"] if isinstance(reference, SourceQualifiedVersionRef) else reference
    prepared = exact_prepared(db, core.object_store, core.task_id, raw_ref)
    raw = readable_payload(core.object_store, prepared, media_type="application/json")
    document = exact_descriptor(db, core.object_store, core.task_id, raw_ref)
    if raw != canonical_json(document):
        raise RegistryConflict("Assembly merge requires canonical actual descriptor bytes")
    return document, {"ref": deepcopy(raw_ref), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "metadata": deepcopy(dict(prepared.metadata))}


def input_material_at(db, core, binding, reference):
    """Read a proved resource and freeze its actual bytes and complete metadata."""
    if reference.source_id != binding["source_id"]:
        raise RegistryConflict("Assembly merge input material source differs")
    raw_ref = {"entity_type": "resource_version/v1", "logical_id": str(reference.ref.resource_id),
        "version_id": str(reference.ref.resource_version_id)}
    prepared = exact_prepared(db, core.object_store, core.task_id, raw_ref)
    media_type = prepared.metadata["media_type"]
    if media_type not in {"application/json", "application/schema+json"}:
        raise RegistryConflict("Assembly merge input proof requires a supported JSON material")
    document, metadata = _private_document(db, core, reference, binding, media_type=media_type)
    schema, authority = metadata["content_schema_ref"], metadata["content_schema_authority_ref"]
    if (schema is None) != (authority is None):
        raise RegistryConflict("Assembly merge input requires its complete selected schema authority")
    if schema is not None:
        _schema_authority_at(db, core, binding, schema, authority)
    raw = readable_payload(core.object_store, prepared, media_type=media_type)
    if raw != canonical_json(document):
        raise RegistryConflict("Assembly merge requires canonical actual input material bytes")
    return document, {"resource_ref": reference.to_dict(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "metadata": deepcopy(metadata)}


def principal_at(db, core, binding, producer):
    if (not isinstance(producer, SourceQualifiedVersionRef) or producer.source_id != binding["source_id"]
            or producer.ref.entity_type != "principal/v1"):
        raise RegistryConflict("Assembly merge producer must be exact and local")
    body = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if body["principal_id"] != str(producer.ref.entity_id) or body["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("Assembly merge producer identity differs")


def catalog_ref(db):
    rows = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
    if len(rows) != 1:
        raise RegistryConflict("Assembly merge requires one exact frozen catalog")
    return {"entity_type": "registry_type_catalog/v1", "logical_id": rows[0]["logical_id"], "version_id": rows[0]["version_id"]}


def select_authorities(db, schemas):
    protected = catalog_ref(db)
    return {schema: deepcopy(protected) if ref is None else
        {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}
        for schema, ref in schemas.items()}


def authority_pins_at(db, core, binding, authorities, expected_schemas):
    if set(authorities) != set(expected_schemas):
        raise RegistryConflict("Assembly merge command must lock every schema authority")
    pins = {}
    for schema, authority in sorted(authorities.items()):
        digest = _schema_authority_at(db, core, binding, schema, authority)
        object_ref = authority if "entity_type" in authority else {"entity_type": "resource_version/v1",
            "logical_id": authority["resource_id"], "version_id": authority["resource_version_id"]}
        prepared = exact_prepared(db, core.object_store, core.task_id, object_ref)
        payload = readable_payload(core.object_store, prepared,
            media_type="application/json" if schema in PROTECTED_SCHEMA_REFS else "application/schema+json")
        # Exact bytes as well as the interpreted schema are an immutable input.
        document = json.loads(payload)
        if payload != canonical_json(document):
            raise RegistryConflict("Assembly merge schema authority bytes are not canonical")
        pins[schema] = {"authority_ref": deepcopy(authority), "bytes": len(payload), "sha256": digest}
    return pins


def object_spec_at(db, core, role, object_type, schema, reference, document):
    authority = catalog_ref(db)
    catalog = exact_descriptor(db, core.object_store, core.task_id, authority)
    entry = catalog["schemas"].get(schema)
    expected = json.loads(core.catalog.schema_path(schema).read_text(encoding="utf-8"))
    if not isinstance(entry, dict) or entry.get("schema_id") != schema or not _same_json(json.loads(entry["source"]), expected):
        raise RegistryConflict("Assembly merge object schema differs from exact catalog")
    payload = readable_payload(core.object_store, exact_prepared(db, core.object_store, core.task_id, authority), media_type="application/json")
    if payload != canonical_json(catalog):
        raise RegistryConflict("Assembly merge catalog is not canonical")
    core.catalog.validate_instance(object_type, category="object", instance=document)
    return {"role": role, "object_type": object_type, "schema": schema, "revision_ref": reference.to_dict(),
        "document": deepcopy(document), "metadata": deepcopy(document), "media_type": "application/json",
        "schema_authority": {"authority_ref": authority, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}


def material_spec(core, binding, role, key, schema, authority, document, summary, descriptors):
    reference = _material_ref(core, binding, key)
    payload = canonical_json(document)
    core.catalog.validate_schema_ref(schema, document)
    return {"role": role, "key": key, "schema": schema, "resource_ref": reference.to_dict(),
        "document": deepcopy(document), "metadata": _metadata(core, binding, reference, schema, authority, document, summary, descriptors),
        "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def check_material_at(db, core, binding, spec):
    reference = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
    document, metadata = _material(db, core, reference, binding, spec["schema"])
    if (_resource_bytes(db, core, reference) != canonical_json(spec["document"])
            or not _same_json(document, spec["document"]) or not _same_json(metadata, spec["metadata"])
            or reference != _material_ref(core, binding, spec["key"])):
        raise RegistryConflict("Assembly merge output bytes or complete metadata differ from first command")


def publish_spec(publisher, spec):
    meta = spec["metadata"]
    authority = meta["content_schema_authority_ref"]
    selected = (_version_from_payload(authority) if "entity_type" in authority else ResourceVersionRef(
        TypedId.parse(authority["resource_id"], expected="resource"), TypedId.parse(authority["resource_version_id"], expected="resource_version")))
    actual = _publish_private_system(publisher.core, publisher.gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(publisher.gateway._bootstrap_ref), payload=canonical_json(spec["document"]),
        media_type=meta["media_type"], content_schema_ref=spec["schema"], content_schema_authority_ref=selected,
        summary=meta["summary"], descriptors=meta["descriptors"], extensions=meta["extensions"],
        lifetime_ref=publisher.gateway._bootstrap_ref, idempotency_key=spec["key"]))
    if SourceQualifiedResourceRef(publisher.binding["source_id"], actual).to_dict() != spec["resource_ref"]:
        raise RegistryConflict("Assembly merge output identity differs from first command")
    return SourceQualifiedResourceRef(publisher.binding["source_id"], actual)
