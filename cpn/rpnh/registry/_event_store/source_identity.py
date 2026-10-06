"""One opt-in local source identity and source-aware reference visibility.

Only a canonical owner-registration fact establishes the local source. A
qualified reference describes identity; it cannot establish this association.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Mapping, Sequence

from ..identities import TypedId
from ..schema_catalog import SchemaGovernanceError, canonical_text


SOURCE_BINDING_TYPE = "collaboration_source_binding/v1"
SOURCE_BINDING_SCHEMA = f"registry_v1/{SOURCE_BINDING_TYPE}"
AUTHOR_REVISION_TYPE = "collaboration_net_revision/v1"
_OBJECT_REF_SCHEMA = "rpnh/collaboration/source_version_ref/v1"
_RESOURCE_REF_SCHEMA = "rpnh/collaboration/source_resource_ref/v1"
_ENTITY = re.compile(r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*/v[1-9][0-9]*")


def source_binding_id(task_id: object) -> TypedId:
    return TypedId("resource", uuid.uuid5(uuid.NAMESPACE_URL, f"rpnh:collaboration-source:{task_id}").hex)


def source_binding_version_id(task_id: object, command_id: str) -> TypedId:
    return TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL,
        canonical_text({"scope": "rpnh:collaboration-source", "task_id": str(task_id), "command_id": command_id})).hex)


def source_command_key(command_id: str) -> str:
    return "collaboration-source:" + canonical_text({"command_id": command_id})


def source_stream(task_id: object) -> str:
    return f"object:{source_binding_id(task_id)}"


def uses_source_binding_namespace(objects, events, task_id) -> bool:
    logical_id = str(source_binding_id(task_id))
    return (any(item.object_type == SOURCE_BINDING_TYPE or str(item.logical_id) == logical_id for item in objects)
            or any(getattr(event, "stream_id", None) == source_stream(task_id)
                   or (event.event_type == "object_version_published/v1"
                       and (event.payload.get("object_type") == SOURCE_BINDING_TYPE
                            or event.payload.get("logical_id") == logical_id))
                   for event in events))


def _canonical_commit_event(db, transaction_id, task_id):
    rows = db.execute("SELECT event_id,criticality,task_id,producer_invocation_id FROM events "
                      "WHERE transaction_id=? AND event_type='transaction_committed/v1'", (transaction_id,)).fetchall()
    if (len(rows) != 1 or rows[0]["criticality"] != "authoritative"
            or rows[0]["task_id"] != str(task_id) or rows[0]["producer_invocation_id"] is not None
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE member_kind='event' AND member_identity=?",
                          (rows[0]["event_id"],)).fetchone() is not None):
        return None
    return rows[0]


def _exact_static_authority(db, catalog, ref, *, expected_type: str, task_id):
    from ..event_store import RegistryConflict

    if (not isinstance(ref, Mapping) or set(ref) != {"entity_type", "logical_id", "version_id"}
            or ref["entity_type"] != expected_type):
        raise RegistryConflict("source binding lacks an exact owner-registration authority")
    row = db.execute(
        "SELECT o.*, t.status AS tx_status, e.event_type AS publication_type, "
        "e.transaction_id AS publication_transaction, e.payload_json AS publication_json, "
        "e.criticality AS publication_criticality, e.task_id AS publication_task_id "
        "FROM objects o JOIN transactions t "
        "ON t.transaction_id=o.transaction_id JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type=? AND o.logical_id=? AND o.version_id=?",
        (expected_type, ref["logical_id"], ref["version_id"]),
    ).fetchone()
    if (row is None or row["tx_status"] != "committed"
            or row["publication_type"] != "object_version_published/v1"
            or row["publication_criticality"] != "authoritative"
            or row["publication_task_id"] != str(task_id)
            or row["publication_transaction"] != row["transaction_id"]
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE "
                          "(member_kind='object' AND member_identity=?) OR "
                          "(member_kind='event' AND member_identity=?)",
                          (ref["version_id"], row["published_event_id"])).fetchone() is not None):
        raise RegistryConflict("source binding authority must already be canonical owner data")
    body = json.loads(row["metadata_json"])
    publication = json.loads(row["publication_json"])
    if (publication.get("object_type") != expected_type
            or publication.get("logical_id") != ref["logical_id"]
            or publication.get("version_id") != ref["version_id"]
            or publication.get("schema_ref") != row["schema_ref"]
            or publication.get("metadata") != body
            or _canonical_commit_event(db, row["transaction_id"], task_id) is None):
        raise RegistryConflict("source binding authority lacks its canonical publication/commit closure")
    catalog.validate_instance(expected_type, category="object", instance=body)
    kind = {"task/v1": "task", "native_run_identity/v1": "run",
            "bootstrap_command/v1": "bootstrap_command"}[expected_type]
    if body.get(f"{kind}_id") != ref["logical_id"] or body.get(f"{kind}_version_id") != ref["version_id"]:
        raise RegistryConflict("source binding authority self-identity differs from its ref")
    return body


def _validate_binding_payload(db, catalog, task_id, payload):
    from ..event_store import RegistryConflict

    catalog.validate_instance(SOURCE_BINDING_TYPE, category="object", instance=payload)
    expected = {}
    for key in ("task_ref", "native_run_ref", "bootstrap_command_ref"):
        row = db.execute("SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()
        if row is None:
            raise RegistryConflict("source binding requires the existing native owner registration")
        expected[key] = json.loads(row["value"])
    if any(payload[key] != expected[key] for key in expected):
        raise RegistryConflict("source binding differs from this Registry's owner registration")
    task = _exact_static_authority(db, catalog, payload["task_ref"], expected_type="task/v1", task_id=task_id)
    run = _exact_static_authority(db, catalog, payload["native_run_ref"], expected_type="native_run_identity/v1", task_id=task_id)
    _exact_static_authority(db, catalog, payload["bootstrap_command_ref"], expected_type="bootstrap_command/v1", task_id=task_id)
    if task["task_id"] != str(task_id) or run["task_ref"] != payload["task_ref"]:
        raise RegistryConflict("source binding owner is outside the current task")


def read_source_binding(db, catalog, task_id):
    """Read only an already committed, non-provisional owner binding object."""
    from ..event_store import RegistryCorruptError

    rows = db.execute(
        "SELECT e.*, o.logical_id AS binding_logical_id, o.version_id AS binding_version_id, "
        "o.metadata_json AS binding_metadata_json, o.schema_ref AS binding_schema_ref, "
        "o.transaction_id AS binding_transaction_id, t.status AS tx_status "
        "FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE e.task_id=? AND o.object_type=? ORDER BY e.ordinal",
        (str(task_id), SOURCE_BINDING_TYPE),
    ).fetchall()
    head = db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?", (source_stream(task_id),)).fetchone()
    if not rows:
        if head is not None and int(head["sequence"]) != 0:
            raise RegistryCorruptError("source binding stream has no canonical binding fact")
        return None
    if len(rows) != 1:
        raise RegistryCorruptError("local source identity has multiple binding objects")
    row = rows[0]
    payload = json.loads(row["binding_metadata_json"])
    publication = json.loads(row["payload_json"])
    if (row["tx_status"] != "committed" or row["stream_id"] != source_stream(task_id)
            or row["stream_sequence"] != 1 or head is None or int(head["sequence"]) != 1
            or row["aggregate_id"] != str(source_binding_id(task_id))
            or row["aggregate_type"] != SOURCE_BINDING_TYPE
            or row["event_type"] != "object_version_published/v1"
            or row["producer_principal"] != "framework" or row["producer_invocation_id"] is not None
            or row["net_instance_id"] is not None or row["task_round_id"] is not None
            or row["criticality"] != "authoritative"
            or row["payload_schema_ref"] != "registry_v1/object_version_published/v1"
            or row["binding_schema_ref"] != SOURCE_BINDING_SCHEMA
            or row["command_id"] != source_command_key(payload.get("command_id"))
            or row["idempotency_key"] != row["command_id"]
            or row["binding_transaction_id"] != row["transaction_id"]
            or row["binding_logical_id"] != str(source_binding_id(task_id))
            or row["binding_version_id"] != str(source_binding_version_id(task_id, payload.get("command_id")))
            or payload.get("binding_ref") != {"entity_type": SOURCE_BINDING_TYPE,
                "logical_id": row["binding_logical_id"], "version_id": row["binding_version_id"]}
            or publication.get("metadata") != payload
            or publication.get("logical_id") != row["binding_logical_id"]
            or publication.get("version_id") != row["binding_version_id"]
            or publication.get("object_type") != SOURCE_BINDING_TYPE
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE "
                          "(member_kind='event' AND member_identity=?) OR "
                          "(member_kind='object' AND member_identity=?)",
                          (row["event_id"], row["binding_version_id"])).fetchone() is not None
            or _canonical_commit_event(db, row["transaction_id"], task_id) is None):
        raise RegistryCorruptError("local source binding is not a canonical owner-registration object")
    _validate_binding_payload(db, catalog, task_id, payload)
    return row


def validate_source_binding(context) -> None:
    """Validate the one-time registration in the caller's BEGIN IMMEDIATE."""
    from ..event_store import RegistryConflict

    if not uses_source_binding_namespace(context.objects, context.events, context.task_id):
        return
    objects = [item for item in context.objects if item.object_type == SOURCE_BINDING_TYPE]
    events = [event for event in context.events if event.event_type == "object_version_published/v1"
              and event.payload.get("object_type") == SOURCE_BINDING_TYPE]
    if len(objects) != 1 or len(events) != 1:
        raise RegistryConflict("source identity requires one object and exact publication fact")
    obj, event = objects[0], events[0]
    payload = obj.metadata
    terminals = [item for item in context.events if item.event_type == "transaction_committed/v1"]
    if len(context.events) != 2 or len(terminals) != 1:
        raise RegistryConflict("source binding permits only its publication and one commit fact")
    terminal = terminals[0]
    if (len(context.objects) != 1 or context.relations
            or context.task_round_id is not None or context.net_instance_id is not None
            or obj.producer_invocation_id is not None or event.producer_invocation_id is not None
            or event.producer_principal != "framework" or event.stream_id != source_stream(context.task_id)
            or event.aggregate_id != str(source_binding_id(context.task_id))
            or event.aggregate_type != SOURCE_BINDING_TYPE
            or event.criticality != "authoritative" or event.task_control
            or event.payload_schema_ref != "registry_v1/object_version_published/v1"
            or obj.schema_ref != SOURCE_BINDING_SCHEMA
            or event.command_id != context.idempotency_key or event.idempotency_key != context.idempotency_key
            or context.idempotency_key != source_command_key(payload.get("command_id"))
            or str(obj.logical_id) != str(source_binding_id(context.task_id))
            or str(obj.version_id) != str(source_binding_version_id(context.task_id, payload.get("command_id")))
            or payload.get("binding_ref") != {"entity_type": SOURCE_BINDING_TYPE,
                "logical_id": str(obj.logical_id), "version_id": str(obj.version_id)}
            or dict(event.payload) != {
                "logical_id": str(obj.logical_id), "version_id": str(obj.version_id),
                "object_type": SOURCE_BINDING_TYPE, "size": obj.size, "media_type": obj.media_type,
                "schema_ref": obj.schema_ref, "storage_locator": obj.storage_locator, "metadata": dict(payload)}
            or terminal.stream_id != f"transaction:{context.transaction_id}"
            or terminal.aggregate_id != str(context.transaction_id) or terminal.aggregate_type != "transaction"
            or terminal.criticality != "authoritative" or terminal.task_control
            or terminal.producer_principal != "framework" or terminal.producer_invocation_id is not None
            or terminal.command_id != context.idempotency_key or terminal.idempotency_key != context.idempotency_key
            or terminal.payload_schema_ref != "registry_v1/transaction_committed/v1"
            or dict(terminal.payload) != {"object_count": 1, "relation_count": 0, "fact_count": 1}):
        raise RegistryConflict("source binding requires the separate trusted owner-registration boundary")
    if read_source_binding(context.db, context.event_store.catalog, context.task_id) is not None:
        raise RegistryConflict("local source identity is already bound and immutable")
    _validate_binding_payload(context.db, context.event_store.catalog, context.task_id, payload)
    for row in context.db.execute("SELECT metadata_json FROM objects WHERE object_type=?", (AUTHOR_REVISION_TYPE,)):
        document = json.loads(row["metadata_json"])
        if document.get("revision_ref", {}).get("source_id") != payload["source_id"]:
            raise RegistryConflict("existing author revision source differs; explicit migration is required")


def has_qualified_references(value: object) -> bool:
    if isinstance(value, Mapping):
        version = value.get("schema_version")
        if isinstance(version, str) and version.startswith("rpnh/collaboration/source_"):
            return True
        return any(has_qualified_references(nested) for nested in value.values())
    return (isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            and any(has_qualified_references(nested) for nested in value))


def _qualified_source(value: Mapping[str, Any], catalog) -> str | None:
    """Only exact, supported v1 wrappers can change local reference scanning."""
    version = value.get("schema_version")
    if (not isinstance(version, str) or version not in {_OBJECT_REF_SCHEMA, _RESOURCE_REF_SCHEMA}
            or set(value) != {"schema_version", "source_id", "ref"}):
        return None
    source, ref = value.get("source_id"), value.get("ref")
    if (not isinstance(source, str) or not source or source != source.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in source)
            or not isinstance(ref, Mapping)):
        return None
    try:
        if version == _OBJECT_REF_SCHEMA:
            if (set(ref) != {"entity_type", "logical_id", "version_id"}
                    or not isinstance(ref["entity_type"], str) or _ENTITY.fullmatch(ref["entity_type"]) is None):
                return None
            TypedId.parse(ref["logical_id"])
            TypedId.parse(ref["version_id"])
        else:
            if set(ref) != {"resource_id", "resource_version_id"}:
                return None
            TypedId.parse(ref["resource_id"], expected="resource")
            TypedId.parse(ref["resource_version_id"], expected="resource_version")
        catalog.validate_schema_ref(version, value)
    except (ValueError, TypeError, AttributeError, SchemaGovernanceError):
        return None
    return source


def source_aware_version_ids(value: object, *, local_source_id: str, catalog) -> set[str]:
    from .proposal import TransactionValidationContext

    if isinstance(value, Mapping):
        source = _qualified_source(value, catalog)
        if source is not None and source != local_source_id:
            return set()
        direct = {key: val for key, val in value.items() if not isinstance(val, (Mapping, list, tuple))}
        result = TransactionValidationContext.referenced_version_ids(direct)
        for nested in value.values():
            result.update(source_aware_version_ids(nested, local_source_id=local_source_id, catalog=catalog))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result: set[str] = set()
        for nested in value:
            result.update(source_aware_version_ids(nested, local_source_id=local_source_id, catalog=catalog))
        return result
    return set()
