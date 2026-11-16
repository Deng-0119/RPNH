"""Exact pre-admission material termination in original Registry facts.

The sole terminal authority is one canonical OwnerControl result together
with its original publication and committed transaction. There is no command
state table, firing, replacement admission, cancellation action, or queue.
"""
from __future__ import annotations

import json
import uuid

from ..event_store import RegistryConflict
from ..identities import TypedId
from ..models import TypedRelation, VersionRef
from ..publication import _stable_id, _version_from_payload
from ..schema_catalog import canonical_json
from .collaboration_descriptors import descriptor_store, exact_prepared
from .material_control import (
    OWNER_SCHEMA, canonical_rows, control_body, read_accepted_material_command,
    read_committed_material_admission, reference, row_ref, validate_active_target,
)


RESULT_PROTOCOL = "rpnh/material_result/v1"
REASONS = {
    "ACTOR_NOT_AUTHORIZED": "The original material actor is no longer authorized before admission",
    "EXECUTION_CHANGED": "The accepted material command no longer names the current execution",
    "TARGET_UNAVAILABLE": "The original material target is no longer executable before admission",
}


def _fail(message):
    raise RegistryConflict("material not_admitted: " + message)


def transaction_key(command_ref, code):
    reference(command_ref, "resource_version/v1")
    if code not in REASONS:
        _fail("unsupported pre-admission reason")
    return "material-not-admitted:" + command_ref["version_id"] + ":" + code


def _transaction_id(task_id, key):
    return str(TypedId("transaction", uuid.uuid5(uuid.NAMESPACE_URL,
        f"d1-c:transaction:{task_id}:{key}").hex))


def assert_unadmitted(store, db, task_id, command_ref):
    """A committed admission is permanent responsibility, even after closing."""
    rows = db.execute(
        "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='firing_admission/v1' AND t.status='committed' AND e.task_id=? "
        "AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=?",
        (str(task_id), command_ref["version_id"])).fetchall()
    for row in rows:
        read_committed_material_admission(store, db, task_id, row, command_ref)
    if rows:
        raise RegistryConflict("COMMAND_IN_PROGRESS: material command already has its original admission")


def current_reason(store, db, task_id, command_ref):
    """Classify only proved current actor/target/execution failures.

    Capacity, disabled PN transitions, transport/storage errors and malformed
    authority are not terminal reasons. The caller's error text is never used.
    """
    read_accepted_material_command(store, db, task_id, command_ref, current=False)
    try:
        command = read_accepted_material_command(store, db, task_id, command_ref, current=True)
        # Missing or ambiguous current authority is recovery work, not proof
        # that a known different execution has legitimately superseded D1.
        authorities = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
        if not authorities or len({row["logical_id"] for row in authorities}) != 1:
            _fail("current run authority is unavailable; no terminal reason is proved")
        params = {key: value for key, value in command["data"].items() if key != "user_action"}
        params["target"] = command["target"]
        validate_active_target(store, db, task_id, params, command["source"])
    except RegistryConflict as exc:
        code = str(exc).split(":", 1)[0]
        if code not in REASONS:
            raise
        return {"code": code, "message": REASONS[code]}
    raise RegistryConflict("COMMAND_STILL_ELIGIBLE: no supported pre-admission terminal reason")


def _original_schema(store, db, task_id, item, body, command_ref, command):
    """A newer process cannot widen this accepted command's frozen schema."""
    from jsonschema import Draft7Validator, ValidationError
    from ..execution_child_closure import ClosureSnapshot, _evidence_schema

    original = exact_prepared(db, descriptor_store(store), task_id, command_ref)
    authority = original.metadata.get("content_schema_authority_ref")
    if (item.metadata.get("content_schema_ref") != OWNER_SCHEMA
            or item.metadata.get("content_schema_authority_ref") != authority):
        _fail("result does not use its command's exact original OwnerControl schema")
    publication = db.execute("SELECT e.ordinal FROM objects o JOIN events e "
        "ON e.event_id=o.published_event_id WHERE o.version_id=?",
        (command_ref["version_id"],)).fetchone()
    document = _evidence_schema(ClosureSnapshot(store, db, task_id), authority,
        OWNER_SCHEMA, evidence_ordinal=publication["ordinal"],
        task_ref=command["data"]["execution"]["task_ref"])
    try:
        Draft7Validator(document).validate(body)
    except ValidationError as exc:
        raise RegistryConflict(
            "material not_admitted: original frozen OwnerControl schema does not allow this result") from exc


def _checked_result(store, db, task_id, item, body, command_ref, transaction_id, key):
    command = read_accepted_material_command(store, db, task_id, command_ref, current=False)
    data = body["data"]
    reason = data.get("error", {})
    code = reason.get("code")
    expected = {"protocol": RESULT_PROTOCOL, "result_kind": "not_admitted",
        "command_ref": command_ref, "rejection_transaction_id": str(transaction_id),
        "error": {"code": code, "message": REASONS.get(code)}}
    bootstrap_row = db.execute("SELECT value FROM registry_meta WHERE key='bootstrap_command_ref'").fetchone()
    bootstrap = None if bootstrap_row is None else json.loads(bootstrap_row[0])
    if (code not in REASONS or data != expected
            or key != transaction_key(command_ref, code)
            or str(transaction_id) != _transaction_id(task_id, key)
            or body["kind"] != "result" or body["command_id"] != command["command_id"]
            or body["target"] != command["target"] or body["source"] != command["source"]
            or body["lineage"] != [command_ref]
            or item.producer_invocation_id is not None
            or item.metadata.get("producer_ref") != bootstrap
            or item.metadata.get("lifetime_ref") != bootstrap
            or item.metadata.get("origin_kind") != "private_system"
            or item.metadata.get("task_ref") != command["data"]["execution"]["task_ref"]
            or item.metadata.get("round_ref") is not None or item.metadata.get("net_ref") is not None
            or item.metadata.get("descriptors") != {
                "owner_control_kind": "result", "command_id": command["command_id"]}):
        _fail("result is not the exact original command/Owner/transaction closure")
    _original_schema(store, db, task_id, item, body, command_ref, command)
    return command, bootstrap


def _inventory(item, command_ref, bootstrap, transaction_id, key, relations, events):
    """Permit only this result's original two relations and four auto facts."""
    result_ref = VersionRef(item.object_type, item.logical_id, item.version_id)
    expected_relations = (
        TypedRelation(_stable_id("relation", key, item.version_id, "produced_by"),
            "produced_by", result_ref, _version_from_payload(bootstrap), system_owned=True),
        TypedRelation(_stable_id("relation", key, item.version_id, "derived_from_resource", 0),
            "derived_from", result_ref, _version_from_payload(command_ref), system_owned=True),
    )
    if len(relations) != 2 or set(rel.relation_id for rel in relations) != set(
            rel.relation_id for rel in expected_relations):
        _fail("control transaction has unrelated or repeated relations")
    wanted = {rel.relation_id: rel for rel in expected_relations}
    if any(rel != wanted[rel.relation_id] for rel in relations):
        _fail("control relation differs from the original exact publication")

    def endpoint(ref):
        return {"entity_type": ref.entity_type, "entity_id": str(ref.entity_id),
                "version_id": str(ref.version_id)}

    inventory = {("object_version_published/v1", str(item.logical_id)): (
        "object:" + str(item.logical_id), item.object_type,
        {"logical_id": str(item.logical_id), "version_id": str(item.version_id),
         "object_type": item.object_type, "size": item.size, "media_type": item.media_type,
         "schema_ref": item.schema_ref, "storage_locator": item.storage_locator,
         "metadata": dict(item.metadata)})}
    for rel in expected_relations:
        inventory[("relation_published/v1", str(rel.relation_id))] = (
            "relation:" + str(rel.relation_id), "typed_relation/v1",
            {"relation_id": str(rel.relation_id), "relation_type": rel.relation_type,
             "source": endpoint(rel.source), "target": endpoint(rel.target),
             "strength": rel.strength, "metadata": {}})
    inventory[("transaction_committed/v1", str(transaction_id))] = (
        "transaction:" + str(transaction_id), "transaction",
        {"object_count": 1, "relation_count": 2, "fact_count": 3})
    if len(events) != 4 or len({(event.event_type, event.aggregate_id) for event in events}) != 4:
        _fail("control transaction has unrelated or repeated publication facts")
    for event in events:
        expected = inventory.get((event.event_type, event.aggregate_id))
        if (expected is None or event.stream_id != expected[0] or event.aggregate_type != expected[1]
                or canonical_json(event.payload) != canonical_json(expected[2])
                or event.criticality != "authoritative" or event.producer_principal != "framework"
                or event.producer_invocation_id is not None
                or event.payload_schema_ref != "registry_v1/" + event.event_type
                or event.idempotency_key != key or event.command_id != key
                or getattr(event, "task_control", False)
                or getattr(event, "task_control_sequence", None) is not None
                or event.causation_event_id is not None or event.parent_event_ids):
            _fail("control fact differs from the exact result publication inventory")


def read_not_admitted(store, db, task_id, command_ref):
    """Reconstruct the original terminal without consulting today's eligibility."""
    command = read_accepted_material_command(store, db, task_id, command_ref, current=False)
    rows = canonical_rows(store, db, task_id, conditions=
        "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
        "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='result' "
        "AND json_extract(o.metadata_json,'$.descriptors.command_id')=?",
        parameters=(OWNER_SCHEMA, command["command_id"]))
    found = []
    for row in rows:
        item = exact_prepared(db, descriptor_store(store), task_id, row_ref(row))
        body = control_body(store, item)
        if body["data"].get("protocol") == RESULT_PROTOCOL and body["data"].get("result_kind") == "not_admitted":
            found.append((row, item, body))
    if not found:
        return None
    if len(found) != 1:
        _fail("command has multiple pre-admission terminal results")
    assert_unadmitted(store, db, task_id, command_ref)
    row, item, body = found[0]
    tx = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (row["transaction_id"],)).fetchone()
    objects = db.execute("SELECT version_id FROM objects WHERE transaction_id=?", (row["transaction_id"],)).fetchall()
    if (tx is None or tx["status"] != "committed" or tx["task_id"] != str(task_id)
            or len(objects) != 1 or objects[0]["version_id"] != str(item.version_id)):
        _fail("terminal result lacks its one original committed control transaction")
    _command, bootstrap = _checked_result(store, db, task_id, item, body, command_ref,
                                          row["transaction_id"], tx["idempotency_key"])
    from ..execution_child_closure import ClosureSnapshot
    snapshot = ClosureSnapshot(store, db, task_id)
    if snapshot.read(row_ref(row), "resource_version/v1", descriptor=False) != canonical_json(body):
        _fail("terminal result bytes differ from the exact original body")
    relation_rows = db.execute("SELECT * FROM relations WHERE transaction_id=?", (row["transaction_id"],)).fetchall()

    def endpoint(raw):
        data = json.loads(raw)
        return VersionRef(data["entity_type"], TypedId.parse(data.get("logical_id", data.get("entity_id"))),
                          TypedId.parse(data["version_id"]))

    relations = tuple(TypedRelation(TypedId.parse(rel["relation_id"]), rel["relation_type"],
        endpoint(rel["source_json"]), endpoint(rel["target_json"]), rel["strength"],
        json.loads(rel["metadata_json"]), system_owned=True) for rel in relation_rows)
    event_rows = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
                           (row["transaction_id"],)).fetchall()
    events = tuple(store._row_to_envelope(event) for event in event_rows)
    branch = db.execute("SELECT value FROM registry_meta WHERE key='branch_id'").fetchone()
    for event in event_rows:
        snapshot.event(event)
        if (event["task_round_id"] is not None or event["net_instance_id"] is not None
                or branch is None or event["branch_id"] != branch[0]
                or event["writer_fencing_epoch"] != tx["writer_epoch"]):
            _fail("terminal control acquired an execution scope or a different writer")
    _inventory(item, command_ref, bootstrap, row["transaction_id"], tx["idempotency_key"], relations, events)
    for relation in relation_rows:
        publications = [event for event in event_rows
            if event["event_type"] == "relation_published/v1"
            and json.loads(event["payload_json"]).get("relation_id") == relation["relation_id"]]
        if (len(publications) != 1
                or relation["published_event_id"] != publications[0]["event_id"]
                or relation["transaction_id"] != publications[0]["transaction_id"]):
            _fail("terminal relation publication backpointer differs from its original fact")
    if not event_rows or event_rows[-1]["event_type"] != "transaction_committed/v1":
        _fail("terminal control lacks its original final transaction fact")
    outbox = db.execute("SELECT * FROM outbox WHERE transaction_id=?", (row["transaction_id"],)).fetchone()
    if (outbox is None or outbox["task_id"] != str(task_id) or outbox["writer_epoch"] != tx["writer_epoch"]
            or json.loads(outbox["event_ids_json"]) != [event["event_id"] for event in event_rows]):
        _fail("terminal control lacks its exact original transaction outbox")
    members = [("object", str(item.version_id)), ("transaction", row["transaction_id"])]
    members += [("relation", rel["relation_id"]) for rel in relation_rows]
    members += [("event", event["event_id"]) for event in event_rows]
    if any(db.execute("SELECT 1 FROM firing_temporary_members WHERE member_kind=? AND member_identity=?",
                      member).fetchone() is not None for member in members):
        _fail("pre-admission control cannot belong to any firing root")
    return row_ref(row), body["data"]


def validate_not_admitted_results(store, db, *, task_id, transaction_id, idempotency_key,
                                  task_round_id, net_instance_id, objects, events, relations):
    """Authorize only one exact control result inside the original write lock."""
    results = [(item, control_body(store, item)) for item in objects
        if item.object_type == "resource_version/v1" and item.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    results = [(item, body) for item, body in results
        if body["data"].get("protocol") == RESULT_PROTOCOL and body["data"].get("result_kind") == "not_admitted"]
    if not results:
        return {}
    if len(results) != 1 or len(objects) != 1 or task_round_id is not None or net_instance_id is not None:
        _fail("pre-admission termination permits one unscoped Owner result only")
    item, body = results[0]
    command_ref = reference(body["data"].get("command_ref"), "resource_version/v1")
    command, bootstrap = _checked_result(store, db, task_id, item, body, command_ref,
                                          transaction_id, idempotency_key)
    assert_unadmitted(store, db, task_id, command_ref)
    if read_not_admitted(store, db, task_id, command_ref) is not None:
        raise RegistryConflict("COMMAND_NOT_ADMITTED: original material command is already terminal")
    if body["data"]["error"] != current_reason(store, db, task_id, command_ref):
        _fail("pre-admission reason changed before the original write lock")
    _inventory(item, command_ref, bootstrap, transaction_id, idempotency_key, relations, events)
    return {str(item.version_id): ("owner:" + command["command_id"] + ":result:material-not-admitted:" + idempotency_key, None)}


def receipt_view(command, command_ref, terminal):
    result_ref, data = terminal
    return {"protocol": "rpnh/material_receipt_view/v1", "command_id": command["command_id"],
        "command_ref": command_ref, "status": "REJECTED", "result_ref": result_ref,
        "result_kind": "not_admitted", "rejection_transaction_id": data["rejection_transaction_id"],
        "error": data["error"]}


__all__ = ("REASONS", "RESULT_PROTOCOL", "assert_unadmitted", "current_reason", "transaction_key",
           "read_not_admitted", "validate_not_admitted_results", "receipt_view")
