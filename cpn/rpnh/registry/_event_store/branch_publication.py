"""Opt-in author Branch compare-and-append in the existing writer snapshot."""

from __future__ import annotations

import json
import uuid

from ..identities import TypedId
from ..schema_catalog import canonical_text
from .source_identity import SOURCE_BINDING_TYPE, _canonical_commit_event, read_source_binding
from .collaboration_descriptors import descriptor_store, exact_descriptor, readable_descriptor


BRANCH_TYPE = "collaboration_branch/v1"
BRANCH_SCHEMA = f"registry_v1/{BRANCH_TYPE}"
GRAPH_BRANCH_TYPE = "collaboration_branch/v2"
GRAPH_BRANCH_SCHEMA = f"registry_v1/{GRAPH_BRANCH_TYPE}"
GRAPH_MERGE_BRANCH_TYPE = "collaboration_branch/v3"
GRAPH_MERGE_BRANCH_SCHEMA = f"registry_v1/{GRAPH_MERGE_BRANCH_TYPE}"
BRANCH_TYPES = (BRANCH_TYPE, GRAPH_BRANCH_TYPE, GRAPH_MERGE_BRANCH_TYPE)


def branch_command_key(command_id):
    return "collaboration-branch:" + canonical_text({"command_id": command_id})


def branch_id_for_command(task_id, source_id, command_id):
    return TypedId("resource", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "kind": "rpnh-author-branch", "task": str(task_id), "source": source_id, "command": command_id})).hex)


def branch_version_id(task_id, command_id):
    return TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "kind": "rpnh-author-branch-version", "task": str(task_id), "command": command_id})).hex)


def _local_binding(db, catalog, task_id, store):
    from ..event_store import RegistryConflict
    binding = read_source_binding(db, catalog, task_id)
    if binding is None:
        raise RegistryConflict("Branch publication requires a canonical local source binding")
    # Keep the stricter static-owner closure above. The general descriptor
    # reader also permits canonically PUBLISHED members, which must not widen
    # the source-binding/bootstrap trust boundary. Only after that check, read
    # their exact immutable payloads through this same caller-owned snapshot.
    payload = exact_descriptor(db, store, task_id, {
        "entity_type": SOURCE_BINDING_TYPE,
        "logical_id": binding["binding_logical_id"], "version_id": binding["binding_version_id"],
    })
    exact_descriptor(db, store, task_id, payload["bootstrap_command_ref"])
    return payload


def exact_branch_document(db, catalog, task_id, reference, store):
    from ..event_store import RegistryConflict
    from ...collaboration.branches import _branch_record_type

    ref = reference["ref"]
    record = _branch_record_type(ref["entity_type"])
    branch_type, branch_schema = record._branch_type, record._branch_schema
    row = db.execute("SELECT o.*, e.task_id AS publication_task, e.event_type AS publication_type, "
        "e.transaction_id AS publication_transaction, e.stream_id AS publication_stream, "
        "e.stream_sequence AS publication_sequence, e.command_id AS publication_command, "
        "e.criticality AS publication_criticality, e.payload_schema_ref AS publication_schema, "
        "e.producer_invocation_id AS publication_invocation, e.producer_principal AS publication_principal, "
        "e.payload_json AS publication_json, t.status AS tx_status FROM objects o "
        "JOIN events e ON e.event_id=o.published_event_id JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE o.object_type=? AND o.logical_id=? AND o.version_id=?",
        (branch_type, ref["logical_id"], ref["version_id"])).fetchone()
    if (row is None or row["tx_status"] != "committed" or row["publication_task"] != str(task_id)
            or row["schema_ref"] != branch_schema or row["media_type"] != "application/json"
            or row["publication_type"] != "object_version_published/v1"
            or row["publication_transaction"] != row["transaction_id"]
            or row["publication_stream"] != f"object:{ref['logical_id']}"
            or row["publication_criticality"] != "authoritative"
            or row["publication_schema"] != "registry_v1/object_version_published/v1"
            or row["publication_principal"] != "framework" or row["publication_invocation"] is not None
            or _canonical_commit_event(db, row["transaction_id"], task_id) is None
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE "
                          "(member_kind='object' AND member_identity=?) OR (member_kind='event' AND member_identity=?)",
                          (ref["version_id"], row["published_event_id"])).fetchone() is not None):
        raise RegistryConflict("Branch ref is not an exact canonical owner publication")
    document = exact_descriptor(db, store, task_id, ref)
    value = record.from_dict(document, catalog=catalog)
    binding = _local_binding(db, catalog, task_id, store)
    if (value.branch_ref.to_dict() != reference or value.branch_ref.source_id != binding["source_id"]
            or value.owner_task_ref.to_dict()["ref"] != binding["task_ref"]
            or value.publisher_bootstrap_ref.to_dict()["ref"] != binding["bootstrap_command_ref"]
            or row["publication_sequence"] != value.sequence
            or row["publication_command"] != branch_command_key(value.command_id)
            or json.loads(row["publication_json"]) != {
                "object_type": branch_type, "logical_id": ref["logical_id"], "version_id": ref["version_id"],
                "schema_ref": row["schema_ref"], "storage_locator": row["storage_locator"],
                "size": row["size"], "media_type": row["media_type"], "metadata": document}):
        raise RegistryConflict("Branch self/source/owner differs from its canonical publication")
    _branch_target(db, store, task_id, value)
    return document


def current_branch_document(db, catalog, task_id, logical_id, store):
    from ..event_store import RegistryConflict

    stream = f"object:{logical_id}"
    row = db.execute("SELECT * FROM events WHERE stream_id=? ORDER BY stream_sequence DESC LIMIT 1", (stream,)).fetchone()
    head = db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?", (stream,)).fetchone()
    if row is None:
        if head is not None and head["sequence"] != 0:
            raise RegistryConflict("Branch stream head has no publication")
        return None
    payload = json.loads(row["payload_json"])
    if (row["event_type"] != "object_version_published/v1" or payload.get("object_type") not in BRANCH_TYPES
            or row["aggregate_id"] != logical_id or row["aggregate_type"] != payload.get("object_type")
            or head is None or head["sequence"] != row["stream_sequence"]):
        raise RegistryConflict("Branch identity/stream is occupied by an incompatible publication")
    reference = payload.get("metadata", {}).get("branch_ref", {})
    ref = reference.get("ref", {})
    if (ref.get("entity_type") != payload.get("object_type")
            or ref.get("logical_id") != logical_id or payload.get("logical_id") != logical_id
            or payload.get("version_id") != ref.get("version_id")):
        raise RegistryConflict("Branch current publication cannot redirect its requested identity")
    document = exact_branch_document(db, catalog, task_id, reference, store)
    exact = db.execute("SELECT published_event_id FROM objects WHERE version_id=?", (ref["version_id"],)).fetchone()
    if (exact is None or exact["published_event_id"] != row["event_id"]
            or payload["metadata"] != document or document["sequence"] != row["stream_sequence"]):
        raise RegistryConflict("Branch version sequence differs from its exact stream")
    return document


def uses_branch_namespace(context):
    if (any(item.object_type in BRANCH_TYPES for item in context.objects)
            or any(event.event_type == "object_version_published/v1" and event.payload.get("object_type") in BRANCH_TYPES
                   for event in context.events)):
        return True
    logical_ids = {str(item.logical_id) for item in context.objects}
    logical_ids.update(event.stream_id.removeprefix("object:") for event in context.events
                       if event.stream_id.startswith("object:"))
    logical_ids.update(event.payload["logical_id"] for event in context.events
                       if event.event_type == "object_version_published/v1" and isinstance(event.payload.get("logical_id"), str))
    if not logical_ids:
        return False
    placeholders = ",".join("?" for _ in logical_ids)
    types = ",".join("?" for _ in BRANCH_TYPES)
    return context.db.execute(f"SELECT 1 FROM objects WHERE object_type IN ({types}) AND logical_id IN ({placeholders}) LIMIT 1",
                              (*BRANCH_TYPES, *sorted(logical_ids))).fetchone() is not None


def validate_branch_publication(context):
    from ..event_store import RegistryConflict
    from ...collaboration.branches import _branch_record_type

    if not uses_branch_namespace(context):
        return
    objects = [item for item in context.objects if item.object_type in BRANCH_TYPES]
    if len(objects) != 1 or len(context.objects) != 1 or context.relations or len(context.events) != 2:
        raise RegistryConflict("Branch publication requires one immutable successor and its exact facts")
    obj = objects[0]
    store = descriptor_store(context.event_store)
    document = readable_descriptor(store, obj)
    record = _branch_record_type(obj.object_type)
    value = record.from_dict(document, catalog=context.event_store.catalog)
    binding = _local_binding(context.db, context.event_store.catalog, context.task_id, store)
    publications = [event for event in context.events if event.event_type == "object_version_published/v1"]
    terminals = [event for event in context.events if event.event_type == "transaction_committed/v1"]
    if len(publications) != 1 or len(terminals) != 1:
        raise RegistryConflict("Branch publication requires its unique publication/commit facts")
    event, terminal = publications[0], terminals[0]
    if (context.task_round_id is not None or context.net_instance_id is not None
            or obj.producer_invocation_id is not None or event.producer_invocation_id is not None
            or value.branch_ref.source_id != binding["source_id"]
            or value.owner_task_ref.to_dict()["ref"] != binding["task_ref"]
            or value.publisher_bootstrap_ref.to_dict()["ref"] != binding["bootstrap_command_ref"]
            or str(obj.logical_id) != str(value.branch_ref.ref.entity_id)
            or str(obj.version_id) != str(branch_version_id(context.task_id, value.command_id))
            or value.branch_ref.ref.version_id != obj.version_id
            or obj.schema_ref != record._branch_schema or context.idempotency_key != branch_command_key(value.command_id)
            or event.stream_id != f"object:{obj.logical_id}" or event.aggregate_id != str(obj.logical_id)
            or event.aggregate_type != obj.object_type or event.producer_principal != "framework"
            or event.task_control or event.criticality != "authoritative"
            or event.payload_schema_ref != "registry_v1/object_version_published/v1"
            or event.command_id != context.idempotency_key or event.idempotency_key != context.idempotency_key
            or dict(event.payload) != {"logical_id": str(obj.logical_id), "version_id": str(obj.version_id),
                "object_type": obj.object_type, "size": obj.size, "media_type": obj.media_type,
                "schema_ref": obj.schema_ref, "storage_locator": obj.storage_locator, "metadata": dict(obj.metadata)}
            or terminal.stream_id != f"transaction:{context.transaction_id}"
            or terminal.aggregate_id != str(context.transaction_id) or terminal.aggregate_type != "transaction"
            or terminal.criticality != "authoritative" or terminal.task_control
            or terminal.producer_principal != "framework" or terminal.producer_invocation_id is not None
            or terminal.command_id != context.idempotency_key or terminal.idempotency_key != context.idempotency_key
            or terminal.payload_schema_ref != "registry_v1/transaction_committed/v1"
            or dict(terminal.payload) != {"object_count": 1, "relation_count": 0, "fact_count": 1}):
        raise RegistryConflict("Branch command/owner/identity publication is inconsistent")
    previous_doc = current_branch_document(context.db, context.event_store.catalog, context.task_id, str(obj.logical_id), store)
    if value.sequence == 1:
        if (previous_doc is not None or value.fork_base_revision_ref != value.head_revision_ref
                or obj.logical_id != branch_id_for_command(context.task_id, binding["source_id"], value.command_id)):
            raise RegistryConflict("Branch creation conflicts with its initial identity or fork base")
        if value.upstream_branch_ref is not None:
            upstream = exact_branch_document(context.db, context.event_store.catalog, context.task_id,
                                              value.upstream_branch_ref.to_dict(), store)
            if upstream["head_revision_ref"] != value.head_revision_ref.to_dict():
                raise RegistryConflict("Branch fork base differs from exact upstream version")
    else:
        if (previous_doc is None
                or previous_doc["branch_ref"] != value.predecessor_branch_ref.to_dict()
                or previous_doc["head_revision_ref"] != value.expected_head_revision_ref.to_dict()
                or previous_doc["sequence"] != value.expected_stream_head):
            raise RegistryConflict("stale Branch version, revision head, or stream expectation")
        if any(previous_doc[field] != value.to_dict()[field] for field in (
                "owner_task_ref", "publisher_bootstrap_ref", "fork_base_revision_ref", "upstream_branch_ref")):
            raise RegistryConflict("Branch advancement cannot change its scope or fork provenance")
    revision = _branch_target(context.db, store, context.task_id, value)
    if value.sequence > 1:
        if value.expected_head_revision_ref not in revision.parent_revision_refs:
            raise RegistryConflict("only direct-descendant Branch advance is supported; reset is not enabled")
        # Descriptor parents are not yet a validated DAG. Do not let a cycle
        # in that input enable a reset through the direct-parent predicate.
        _reject_reused_head(context.db, store, context.task_id, previous_doc, value.head_revision_ref)


def _branch_target(db, store, task_id, value):
    from ..event_store import RegistryConflict
    from ...collaboration.authoring import NetRevision

    from ...collaboration.graph_authoring import GraphNetRevision

    revision_type = value.head_revision_ref.ref.entity_type
    if revision_type == "collaboration_net_revision/v1" and value.branch_ref.ref.entity_type == BRANCH_TYPE:
        record = NetRevision
    elif revision_type == "collaboration_net_revision/v2" and value.branch_ref.ref.entity_type in (GRAPH_BRANCH_TYPE, GRAPH_MERGE_BRANCH_TYPE):
        record = GraphNetRevision
    elif revision_type == "collaboration_net_revision/v3" and value.branch_ref.ref.entity_type == GRAPH_MERGE_BRANCH_TYPE:
        from ...collaboration.graph_merge import GraphMergeNetRevision
        record = GraphMergeNetRevision
    else:
        raise RegistryConflict("Branch target protocol differs from its Branch version")
    revision = record.from_dict(exact_descriptor(db, store, task_id,
        value.head_revision_ref.to_dict()["ref"]), catalog=store.catalog)
    if revision.revision_ref != value.head_revision_ref or revision.owner_task_ref != value.owner_task_ref:
        raise RegistryConflict("Branch target differs from its exact revision owner/self identity")
    for authority, kind in ((revision.owner_task_ref, "task"), (revision.producer_principal_ref, "principal")):
        ref = authority.to_dict()["ref"]
        document = exact_descriptor(db, store, task_id, ref)
        if document[f"{kind}_id"] != ref["logical_id"] or document[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("Branch target revision authority self-identity differs")
    return revision


def _reject_reused_head(db, store, task_id, previous_doc, target):
    from ..event_store import RegistryConflict

    seen = set()
    current = previous_doc
    while current is not None:
        identity = current["branch_ref"]["ref"]
        if identity["version_id"] in seen:
            raise RegistryConflict("Branch predecessor lineage contains a cycle")
        seen.add(identity["version_id"])
        if current["head_revision_ref"] == target.to_dict():
            raise RegistryConflict("direct-descendant Branch advance cannot reset to a prior exact head")
        predecessor = current["predecessor_branch_ref"]
        if predecessor is None:
            if current["sequence"] != 1:
                raise RegistryConflict("Branch predecessor lineage is incomplete")
            break
        prior = exact_branch_document(db, store.catalog, task_id, predecessor, store)
        if (prior["branch_ref"]["ref"]["entity_type"] != identity["entity_type"]
                or prior["branch_ref"]["source_id"] != current["branch_ref"]["source_id"]
                or prior["branch_ref"]["ref"]["logical_id"] != identity["logical_id"]
                or prior["sequence"] != current["sequence"] - 1
                or prior["head_revision_ref"] != current["expected_head_revision_ref"]):
            raise RegistryConflict("Branch predecessor lineage is inconsistent")
        current = prior
