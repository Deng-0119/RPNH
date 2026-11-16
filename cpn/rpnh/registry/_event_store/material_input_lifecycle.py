"""Explicit termination of an unused original Viewer input.

This is an Owner input-protocol control, not a material disable or purge.
The input and its original acceptance remain immutable. No new state table,
business firing, deletion authority, or background cleanup is introduced.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict
from ..identities import TypedId
from ..models import TypedRelation, VersionRef
from ..schema_catalog import canonical_json
from ..strict_contracts import ref_payload
from .collaboration_descriptors import descriptor_store, exact_prepared
from .material_control import (
    OWNER_SCHEMA, COMMAND_PROTOCOL, control_body, reference, row_ref,
    read_accepted_material_input, read_accepted_material_command,
    _historical_actor, _original_action, require_session,
)
from .material_not_admitted import _inventory, _transaction_id


PROTOCOL = "rpnh/material_input_abandon/v1"
ACTION = "abandon_material_input"


def _fail(message):
    raise RegistryConflict("material input abandonment: " + message)


def transaction_key(input_ref):
    reference(input_ref, "resource_version/v1")
    return "material-input-abandon:" + input_ref["version_id"]


def validate_params(params):
    if (not isinstance(params, dict)
            or set(params) != {"protocol", "input_command_id", "input_ref"}
            or params["protocol"] != PROTOCOL
            or not isinstance(params["input_command_id"], str)
            or not params["input_command_id"]):
        _fail("expected one exact original input and preparation identity")
    reference(params["input_ref"], "resource_version/v1")


def _original_preparation(store, db, task_id, input_ref):
    original = read_accepted_material_input(store, db, task_id, input_ref, current=False)
    input_key, action = _original_action(db, original, "prepare_material_input")
    session = _historical_actor(store, db, task_id, action, current=False)
    row = db.execute("SELECT transaction_id FROM objects WHERE version_id=?",
                     (input_ref["version_id"],)).fetchone()
    rows = db.execute("SELECT * FROM objects WHERE transaction_id=? "
        "AND object_type='resource_version/v1' AND version_id<>?",
        (row["transaction_id"], input_ref["version_id"])).fetchall()
    if len(rows) != 1:
        _fail("input has no unique original acceptance receipt")
    receipt = exact_prepared(db, descriptor_store(store), task_id, row_ref(rows[0]))
    body = control_body(store, receipt)
    if (body["kind"] != "receipt" or body["command_id"] != input_key
            or body["target"] is not None or body["lineage"] != [input_ref]
            or body["source"] != session["data"]["target_principal_ref"]
            or body["data"] != {"protocol": "rpnh/material_input_receipt/v1",
                "input_command_id": input_key, "input_ref": input_ref,
                "status": "INPUT_ACCEPTED"}):
        _fail("input acceptance receipt differs from its original preparation")
    return original, receipt, input_key, action


def assert_unused(store, db, task_id, input_ref):
    """Original accepted or unresolved responsibility prevents abandonment.

    Existing exact endpoint indexes find candidates. Validate the original
    object, relationship and transaction material before concluding no use.
    """
    refs = {r["version_id"]: row_ref(r) for r in db.execute(
        "SELECT * FROM objects WHERE object_type='resource_version/v1' "
        "AND json_extract(metadata_json,'$.descriptors.owner_material_input_version_id')=?",
        (input_ref["version_id"],)).fetchall()}
    links = db.execute("SELECT * FROM relations WHERE relation_type='derived_from' "
        "AND json_extract(target_json,'$.version_id')=?", (input_ref["version_id"],)).fetchall()
    for link in links:
        target = json.loads(link["target_json"])
        if (target.get("entity_type") != input_ref["entity_type"]
                or target.get("entity_id", target.get("logical_id")) != input_ref["logical_id"]):
            _fail("input relationship index has an inconsistent exact endpoint")
        source = json.loads(link["source_json"])
        if source.get("entity_type") == "resource_version/v1":
            ref = {"entity_type": "resource_version/v1",
                "logical_id": source.get("entity_id", source.get("logical_id")),
                "version_id": source.get("version_id")}
            refs[ref["version_id"]] = reference(ref, "resource_version/v1")
    for ref in refs.values():
        item = exact_prepared(db, descriptor_store(store), task_id, ref)
        if item.metadata.get("content_schema_ref") != OWNER_SCHEMA:
            _fail("input has an unsupported original consumer relationship")
        body = control_body(store, item)
        if body["data"].get("protocol") != COMMAND_PROTOCOL:
            continue
        command = read_accepted_material_command(store, db, task_id, ref, current=False)
        row = db.execute("SELECT t.status,t.command_json FROM objects o JOIN transactions t "
            "ON t.transaction_id=o.transaction_id WHERE o.version_id=?", (ref["version_id"],)).fetchone()
        recorded = [o for o in json.loads(row["command_json"]).get("objects", ())
            if o.get("version_id") == ref["version_id"]]
        if (row["status"] != "committed" or len(recorded) != 1
                or recorded[0].get("metadata") != dict(item.metadata)
                or command["data"].get("input_ref") != input_ref):
            _fail("input command differs from its original transaction responsibility")
        raise RegistryConflict("INPUT_IN_USE: input already belongs to an accepted material command")
    # No legitimate delivery precedes the input's own command and Start.
    # Existing or damaged delivery facts must still prevent a false unused claim.
    used = db.execute("SELECT 1 FROM relations WHERE json_extract(target_json,'$.version_id')=? "
        "AND json_extract(source_json,'$.entity_type') IN ('resource_delivery/v1','resource_release_witness/v1') "
        "LIMIT 1", (input_ref["version_id"],)).fetchone()
    if used is not None:
        _fail("input has original delivery responsibility")


def _original_schema(store, db, task_id, item, body, acceptance):
    from jsonschema import Draft7Validator, ValidationError
    from ..execution_child_closure import ClosureSnapshot, _evidence_schema
    authority = acceptance.metadata.get("content_schema_authority_ref")
    if item.metadata.get("content_schema_authority_ref") != authority:
        _fail("abandonment must use the input acceptance's original schema authority")
    row = db.execute("SELECT e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id "
                     "WHERE o.version_id=?", (str(acceptance.version_id),)).fetchone()
    document = _evidence_schema(ClosureSnapshot(store, db, task_id), authority, OWNER_SCHEMA,
        evidence_ordinal=row["ordinal"], task_ref=acceptance.metadata["task_ref"])
    declared = [rule for rule in document.get("allOf", ())
        if rule.get("if", {}).get("properties", {}).get("data", {}).get("properties", {}).get("protocol", {}).get("const") == PROTOCOL]
    if len(declared) != 1:
        _fail("original frozen OwnerControl schema does not declare input abandonment")
    try:
        Draft7Validator(document).validate(body)
    except ValidationError as exc:
        raise RegistryConflict("original frozen input lifecycle schema rejects this result") from exc


def _checked_result(store, db, task_id, item, body, input_ref, transaction_id, key, action_material):
    original, acceptance, input_key, preparation_action = _original_preparation(store, db, task_id, input_ref)
    action = body["data"].get("user_action")
    _historical_actor(store, db, task_id, action, current=False)
    bootstrap_row = db.execute("SELECT value FROM registry_meta WHERE key='bootstrap_command_ref'").fetchone()
    bootstrap = None if bootstrap_row is None else json.loads(bootstrap_row[0])
    expected_data = {"protocol": PROTOCOL, "input_ref": input_ref, "input_command_id": input_key,
        "status": "ABANDONED", "abandonment_transaction_id": str(transaction_id), "user_action": action}
    if (key != transaction_key(input_ref) or str(transaction_id) != _transaction_id(task_id, key)
            or body["data"] != expected_data or body["kind"] != "result"
            or body["command_id"] != key or body["target"] is not None
            or body["source"] != control_body(store, acceptance)["source"]
            or body["lineage"] != [input_ref] or action != action_material
            or action["kind"] != ACTION or action["action_ref"] is not None
            or action["actor"] != preparation_action["actor"]
            or action["session_ref"] != preparation_action["session_ref"]
            or item.producer_invocation_id is not None
            or item.metadata.get("producer_ref") != bootstrap
            or item.metadata.get("lifetime_ref") != bootstrap
            or item.metadata.get("origin_kind") != "private_system"
            or item.metadata.get("task_ref") != original.metadata["task_ref"]
            or item.metadata.get("round_ref") is not None or item.metadata.get("net_ref") is not None
            or item.metadata.get("descriptors") != {"owner_control_kind": "result", "command_id": key,
                "owner_abandoned_input_version_id": input_ref["version_id"]}):
        _fail("result differs from its exact original input, actor or Owner transaction")
    _original_schema(store, db, task_id, item, body, acceptance)
    return bootstrap


def read_abandonment(store, db, task_id, input_ref):
    """Reconstruct the one original input terminal from its stable transaction."""
    key = transaction_key(input_ref)
    txid = _transaction_id(task_id, key)
    rows = db.execute("SELECT * FROM transactions WHERE transaction_id=? OR (task_id=? AND idempotency_key=?)",
                      (txid, str(task_id), key)).fetchall()
    if not rows:
        return None
    if (len(rows) != 1 or rows[0]["status"] != "committed" or rows[0]["idempotency_key"] != key
            or rows[0]["transaction_id"] != txid or rows[0]["task_id"] != str(task_id)):
        _fail("original abandonment transaction is unresolved")
    tx = rows[0]
    objects = db.execute("SELECT * FROM objects WHERE transaction_id=?", (txid,)).fetchall()
    if len(objects) != 1:
        _fail("original abandonment result is missing or ambiguous")
    item = exact_prepared(db, descriptor_store(store), task_id, row_ref(objects[0]))
    body = control_body(store, item)
    material = json.loads(tx["command_json"])
    if material.get("task_round_id") is not None or material.get("net_instance_id") is not None:
        _fail("input terminal acquired a business execution scope")
    bootstrap = _checked_result(store, db, task_id, item, body, input_ref, txid, key,
                                material.get("user_collaboration"))
    assert_unused(store, db, task_id, input_ref)
    from ..execution_child_closure import ClosureSnapshot
    snapshot = ClosureSnapshot(store, db, task_id)
    if snapshot.read(row_ref(objects[0]), "resource_version/v1", descriptor=False) != canonical_json(body):
        _fail("terminal bytes differ from their original publication")
    relation_rows = db.execute("SELECT * FROM relations WHERE transaction_id=? ORDER BY rowid", (txid,)).fetchall()
    def endpoint(raw):
        value = json.loads(raw)
        return VersionRef(value["entity_type"], TypedId.parse(value.get("logical_id", value.get("entity_id"))),
                          TypedId.parse(value["version_id"]))
    relations = tuple(TypedRelation(TypedId.parse(r["relation_id"]), r["relation_type"],
        endpoint(r["source_json"]), endpoint(r["target_json"]), r["strength"],
        json.loads(r["metadata_json"]), system_owned=True) for r in relation_rows)
    event_rows = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal", (txid,)).fetchall()
    events = tuple(store._row_to_envelope(e) for e in event_rows)
    branch = db.execute("SELECT value FROM registry_meta WHERE key='branch_id'").fetchone()
    for e in event_rows:
        snapshot.event(e)
        if (e["task_round_id"] is not None or e["net_instance_id"] is not None
                or branch is None or e["branch_id"] != branch[0] or e["writer_fencing_epoch"] != tx["writer_epoch"]):
            _fail("terminal event differs from its original unscoped transaction")
    _inventory(item, input_ref, bootstrap, txid, key, relations, events)
    for r in relation_rows:
        matches = [e for e in event_rows if e["event_type"] == "relation_published/v1"
                   and json.loads(e["payload_json"]).get("relation_id") == r["relation_id"]]
        if len(matches) != 1 or r["published_event_id"] != matches[0]["event_id"]:
            _fail("terminal relation differs from its original publication backpointer")
    outbox = db.execute("SELECT * FROM outbox WHERE transaction_id=?", (txid,)).fetchone()
    if (not event_rows or event_rows[-1]["event_type"] != "transaction_committed/v1"
            or outbox is None or outbox["task_id"] != str(task_id) or outbox["writer_epoch"] != tx["writer_epoch"]
            or json.loads(outbox["event_ids_json"]) != [e["event_id"] for e in event_rows]):
        _fail("terminal lacks its exact original transaction outbox")
    from ..event_store import _transaction_command_material, _ref_json
    expected_material = _transaction_command_material(
        branch_id=branch[0], task_round_id=None, net_instance_id=None,
        objects=({"object_type": item.object_type, "logical_id": str(item.logical_id),
            "version_id": str(item.version_id), "metadata": dict(item.metadata),
            "producer_invocation_id": None},),
        events=tuple({"event_type": e.event_type, "stream_id": e.stream_id,
            "aggregate_id": e.aggregate_id, "aggregate_type": e.aggregate_type,
            "criticality": e.criticality, "payload": e.payload, "payload_schema_ref": e.payload_schema_ref,
            "task_control": False, "producer_principal": e.producer_principal,
            "producer_invocation_id": None} for e in events),
        relations=tuple({"relation_id": str(r.relation_id), "relation_type": r.relation_type,
            "source": _ref_json(r.source), "target": _ref_json(r.target), "strength": r.strength,
            "metadata": r.metadata, "producer_invocation_id": None, "system_owned": True} for r in relations),
        expected_snapshot_predecessors={}, expected_dependency_root_predecessor=None)
    expected_material["user_collaboration"] = body["data"]["user_action"]
    if canonical_json(material) != canonical_json(expected_material):
        _fail("terminal transaction material differs from its exact original inventory")
    members = [("object", str(item.version_id)), ("transaction", txid)]
    members += [("relation", r["relation_id"]) for r in relation_rows]
    members += [("event", e["event_id"]) for e in event_rows]
    if any(db.execute("SELECT 1 FROM firing_temporary_members WHERE member_kind=? AND member_identity=?", member).fetchone()
           is not None for member in members):
        _fail("input protocol control cannot belong to a business firing")
    return row_ref(objects[0]), body["data"]


def assert_available(store, db, task_id, input_ref):
    if read_abandonment(store, db, task_id, input_ref) is not None:
        raise RegistryConflict("INPUT_ABANDONED: original input no longer permits submission or consumption")


def validate_action(store, db, *, task_id, transaction_id, idempotency_key,
                    objects, events, relations, action, existing):
    """Trusted HOST input action validation, including exact replay."""
    controls = [(o, control_body(store, o)) for o in objects if o.object_type == "resource_version/v1"
                and o.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    results = [(o, b) for o, b in controls if b["data"].get("protocol") == PROTOCOL]
    if not results and (action is None or action.kind != ACTION):
        return False
    from ..user_collaboration import check_handle
    from .user_collaboration import check_replay
    check_handle(store, action)
    require_session(store, db, task_id, action.session)
    if action.kind != ACTION or action.creation is None:
        _fail("requires the exact trusted HOST action")
    request = json.loads(action.creation)
    if (set(request) != {"owner_command", "command_id", "params"}
            or request["owner_command"] != ACTION or request["command_id"] != idempotency_key):
        _fail("HOST request differs from the original input terminal identity")
    params = request["params"]
    validate_params(params)
    if idempotency_key != transaction_key(params["input_ref"]):
        _fail("requires the stable original input termination identity")
    if existing is not None:
        check_replay(store, db, task_id, existing, action)
        terminal = read_abandonment(store, db, task_id, params["input_ref"])
        if terminal is None or terminal[1]["input_command_id"] != params["input_command_id"]:
            _fail("replay changed its original input")
        return True
    if len(results) != 1 or len(objects) != 1:
        _fail("permits exactly one original Owner input result")
    item, body = results[0]
    bootstrap = _checked_result(store, db, task_id, item, body, params["input_ref"],
                                 transaction_id, idempotency_key, action.material())
    if body["data"]["input_command_id"] != params["input_command_id"]:
        _fail("HOST preparation identity differs from the exact input")
    assert_unused(store, db, task_id, params["input_ref"])
    if read_abandonment(store, db, task_id, params["input_ref"]) is not None:
        _fail("input already has its original terminal")
    _inventory(item, params["input_ref"], bootstrap, transaction_id, idempotency_key, relations, events)
    return True


def validate_publication(store, db, *, task_id, transaction_id, idempotency_key,
                         task_round_id, net_instance_id, objects, events, relations):
    results = [(o, control_body(store, o)) for o in objects if o.object_type == "resource_version/v1"
               and o.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    results = [(o, b) for o, b in results if b["data"].get("protocol") == PROTOCOL]
    if not results:
        return {}
    if len(results) != 1 or len(objects) != 1 or task_round_id is not None or net_instance_id is not None:
        _fail("requires one unscoped original Owner input transaction")
    item, body = results[0]
    ref = reference(body["data"].get("input_ref"), "resource_version/v1")
    bootstrap = _checked_result(store, db, task_id, item, body, ref, transaction_id,
                                 idempotency_key, body["data"].get("user_action"))
    assert_unused(store, db, task_id, ref)
    _inventory(item, ref, bootstrap, transaction_id, idempotency_key, relations, events)
    return {str(item.version_id): ("owner:" + idempotency_key + ":result:material-input-abandon", None)}


def decorate_input_receipt(store, db, task_id, receipt):
    terminal = read_abandonment(store, db, task_id, receipt["input_ref"])
    if terminal is None:
        return receipt
    return {**receipt, "current_state": "ABANDONED", "submission_allowed": False,
            "abandonment_ref": terminal[0]}


def abandonment_receipt(terminal):
    ref, data = terminal
    return {"protocol": "rpnh/material_input_abandon_receipt/v1", "status": "ABANDONED",
        "input_ref": data["input_ref"], "input_command_id": data["input_command_id"],
        "abandonment_ref": ref, "abandonment_transaction_id": data["abandonment_transaction_id"]}
