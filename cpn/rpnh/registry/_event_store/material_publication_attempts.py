"""Original canonical control facts for the two material metadata slots."""
from __future__ import annotations

import json
import uuid

from ..event_store import RegistryConflict, StaleWriterError
from ..execution_child_closure import ClosureSnapshot
from ..identities import TypedId
from ..models import PreparedObject, TypedRelation, VersionRef
from ..publication import _stable_id, _version_from_payload
from ..schema_catalog import canonical_json
from .collaboration_descriptors import descriptor_store
from .material_control import canonical_rows, reference, row_ref
from .material_settlement import material_admission, completion_evidence, terminal_result


ATTEMPT = "material_publication_attempt/v1"
RESERVED = "material_publication_attempt_reserved/v1"
CONFLICTED = "material_publication_attempt_ordinal_conflicted/v1"


def _fail(message):
    raise RegistryConflict("material publication attempt: " + message)


def reservation_key(task_id, command_ref, firing_ref, slot):
    reference(command_ref, "resource_version/v1")
    reference(firing_ref, "transition_firing/v1")
    if type(slot) is not int or slot not in {0, 1}:
        _fail("only the original slots zero and one exist")
    return f"material-publication-attempt:{task_id}:{command_ref['version_id']}:{firing_ref['version_id']}:{slot}"


def conflict_key(task_id, command_ref, firing_ref, slot):
    return reservation_key(task_id, command_ref, firing_ref, slot) + ":ordinal-conflicted"


def _transaction_id(task_id, key):
    return str(TypedId("transaction", uuid.uuid5(uuid.NAMESPACE_URL,
        f"d1-c:transaction:{task_id}:{key}").hex))


def _attempt_ref(task_id, command_ref, firing_ref, slot):
    key = reservation_key(task_id, command_ref, firing_ref, slot)
    return {"entity_type": ATTEMPT,
        "logical_id": str(_stable_id("material_publication_attempt", key)),
        "version_id": str(_stable_id("material_publication_attempt_version", key))}


def _catalog(store, db, task_id):
    """Reuse the original genesis/catalog proof, then read its fixed sources."""
    from ..normal_root_token_allocation import _load_original_token_schema
    from ..content_schemas import _validate_schema_bytes
    from ..material_publication_attempts import MaterialAttemptRecoveryRequired
    cut = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
    _load_original_token_schema(store, db, task_id=task_id, cut=cut,
        require_source_binding=False, require_normal_capability=False)
    locators = {row["key"]: row["value"] for row in db.execute(
        "SELECT key,value FROM registry_meta WHERE key IN ('type_catalog_logical_id','type_catalog_version_id')")}
    ref = {"entity_type": "registry_type_catalog/v1", "logical_id": locators["type_catalog_logical_id"],
           "version_id": locators["type_catalog_version_id"]}
    bundle = ClosureSnapshot(store, db, task_id).read(ref, "registry_type_catalog/v1")
    schemas = {}
    for kind, category in ((ATTEMPT, "object"), (RESERVED, "event"), (CONFLICTED, "event")):
        schema_id = "registry_v1/" + kind
        definitions = [value for value in bundle["definitions"] if value.get("name") == kind]
        entry = bundle["schemas"].get(schema_id)
        index = {"schema_id": schema_id, "path": "registry_v1/" + kind.replace("/", ".") + ".schema.json"}
        if not definitions and entry is None and index not in bundle["schema_index"]:
            raise MaterialAttemptRecoveryRequired(
                "RECOVERY_REQUIRED: original frozen catalog does not support material attempt reservations")
        expected = {"name": kind, "category": category, "owner": "registry", "schema_ref": schema_id,
            "criticality": None if category == "object" else "authoritative",
            "permission": "task-scoped" if category == "object" else "writer-only",
            "retention": "run-fact" if category == "object" else "permanent",
            "recovery_rule": "registry-reference-replay" if category == "object" else "fail-closed",
            "integrity_rule": "exact-schema-and-reference-validation"}
        if (definitions != [expected]
                or [value for value in bundle["schema_index"] if value.get("schema_id") == schema_id] != [index]
                or not isinstance(entry, dict) or set(entry) != {"schema_id", "path", "source"}
                or {key: entry[key] for key in index} != index):
            _fail("original attempt definition/index/source differs")
        actual, document = _validate_schema_bytes(entry["source"].encode("utf-8"))
        if actual != schema_id:
            _fail("original attempt source names another schema")
        schemas[kind] = document
    return ref, schemas


def _validate_schema(schemas, kind, body):
    from jsonschema import Draft7Validator, ValidationError
    try:
        Draft7Validator(schemas[kind]).validate(body)
    except ValidationError as exc:
        raise RegistryConflict("material publication attempt violates its original frozen schema") from exc


def _identity(store, db, task_id, command_ref, firing_ref):
    reference(command_ref, "resource_version/v1")
    reference(firing_ref, "transition_firing/v1")
    found = material_admission(store, db, task_id, firing_ref)
    if found is None or found[0]["owner_command_ref"] != command_ref:
        _fail("command and firing are not the original material admission")
    admission, command = found
    completion = completion_evidence(store, db, task_id, admission)
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                      (firing_ref["version_id"],)).fetchone()
    if root is None or root["state"] not in {"PROVISIONAL", "PUBLISHED", "REJECTED"}:
        _fail("original material publication responsibility is unavailable")
    return admission, command, completion, root


def attempt_body(store, db, *, task_id, owner_command_ref, firing_ref,
                 slot, prior_conflict_event_id, require_prepared=False, catalog=None):
    admission, command, completion, root = _identity(store, db, task_id, owner_command_ref, firing_ref)
    catalog_ref, schemas = _catalog(store, db, task_id) if catalog is None else catalog
    if require_prepared:
        from .firing_rejection import prepared_snapshot
        run_rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
        if root["state"] != "PROVISIONAL" or not run_rows:
            _fail("only an open original prepared firing can reserve a slot")
        run = ClosureSnapshot(store, db, task_id).read(row_ref(run_rows[-1]), "run_execution_authority/v1")
        prepared_snapshot(store, db, task_id=task_id, firing_ref=firing_ref,
            invocation_ref=admission["invocation_ref"], owner_command_ref=owner_command_ref,
            retained_checkpoint_ref=run["latest_checkpoint_ref"],
            registered_completion_event_id=str(completion.event_id))
    key = reservation_key(task_id, owner_command_ref, firing_ref, slot)
    body = {"material_publication_attempt_ref": _attempt_ref(task_id, owner_command_ref, firing_ref, slot),
        "task_ref": command["data"]["execution"]["task_ref"], "owner_command_ref": owner_command_ref,
        "transition_firing_ref": firing_ref, "invocation_ref": admission["invocation_ref"],
        "firing_admission_ref": admission["firing_admission_ref"],
        "registered_completion_event_id": str(completion.event_id),
        "workspace_revision_candidate_ref": completion.payload["workspace_revision_candidate_ref"],
        "slot": slot, "prior_conflict_event_id": prior_conflict_event_id,
        "original_catalog_ref": catalog_ref, "proposal_key": key + ":publish"}
    _validate_schema(schemas, ATTEMPT, body)
    _validate_schema(schemas, RESERVED, body)
    return body


def conflict_body(task_id, attempt, observed_ordinal):
    from ..strict_contracts import ref_payload
    return {"material_publication_attempt_ref": ref_payload(attempt.attempt_ref),
        "reservation_event_id": attempt.reservation_event_id,
        "proposal_key": attempt.proposal_key,
        "proposal_transaction_id": _transaction_id(task_id, attempt.proposal_key),
        "expected_registry_ordinal": attempt.expected_registry_ordinal,
        "observed_registry_ordinal": observed_ordinal}


def _inventory(objects, events, relations, *, task_id, transaction_id, key, body, mode):
    """Only one exact native control publication; no business payloads."""
    is_reserve = mode == "reserve"
    attempt_ref = body["material_publication_attempt_ref"]
    kind = RESERVED if is_reserve else CONFLICTED
    if (len(objects) != int(is_reserve) or len(relations) != int(is_reserve)
            or len(events) != (4 if is_reserve else 2)):
        _fail("control transaction inventory is not exact")
    expected = {(kind, attempt_ref["logical_id"]): (
        "material-publication-attempt:" + attempt_ref["logical_id"], ATTEMPT, body)}
    if is_reserve:
        item, = objects
        actual_ref = {"entity_type": item.object_type, "logical_id": str(item.logical_id), "version_id": str(item.version_id)}
        if (item.object_type != ATTEMPT or actual_ref != attempt_ref
                or item.schema_ref != "registry_v1/" + ATTEMPT
                or item.media_type != "application/json" or item.producer_invocation_id is not None
                or dict(item.metadata) != body):
            _fail("reservation object is not its exact immutable descriptor")
        relation = TypedRelation(_stable_id("relation", key, "accepted-command"), "derived_from",
            _version_from_payload(attempt_ref), _version_from_payload(body["owner_command_ref"]), system_owned=True)
        if relations[0] != relation:
            _fail("reservation relation is not its original accepted command")
        expected[("object_version_published/v1", str(item.logical_id))] = (
            "object:" + str(item.logical_id), ATTEMPT,
            {"logical_id": str(item.logical_id), "version_id": str(item.version_id), "object_type": ATTEMPT,
             "size": item.size, "media_type": item.media_type, "schema_ref": item.schema_ref,
             "storage_locator": item.storage_locator, "metadata": body})
        def endpoint(ref):
            return {"entity_type": ref.entity_type, "entity_id": str(ref.entity_id), "version_id": str(ref.version_id)}
        expected[("relation_published/v1", str(relation.relation_id))] = (
            "relation:" + str(relation.relation_id), "typed_relation/v1",
            {"relation_id": str(relation.relation_id), "relation_type": "derived_from",
             "source": endpoint(relation.source), "target": endpoint(relation.target), "strength": "strong", "metadata": {}})
    expected[("transaction_committed/v1", str(transaction_id))] = (
        "transaction:" + str(transaction_id), "transaction",
        {"object_count": len(objects), "relation_count": len(relations), "fact_count": len(events) - 1})
    if len({(event.event_type, event.aggregate_id) for event in events}) != len(expected):
        _fail("control events are missing or repeated")
    for event in events:
        wanted = expected.get((event.event_type, event.aggregate_id))
        if (wanted is None or event.stream_id != wanted[0] or event.aggregate_type != wanted[1]
                or dict(event.payload) != wanted[2] or event.criticality != "authoritative"
                or event.producer_principal != "framework" or event.producer_invocation_id is not None
                or event.payload_schema_ref != "registry_v1/" + event.event_type
                or event.idempotency_key != key or event.command_id != key
                or getattr(event, "task_control", False) or getattr(event, "task_control_sequence", None) is not None
                or event.causation_event_id is not None or event.parent_event_ids):
            _fail("control event differs from its original typed inventory")


def _transaction(store, db, task_id, transaction_id, *, key, body, mode):
    tx = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (transaction_id,)).fetchone()
    event_rows = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal", (transaction_id,)).fetchall()
    object_rows = db.execute("SELECT * FROM objects WHERE transaction_id=?", (transaction_id,)).fetchall()
    relation_rows = db.execute("SELECT * FROM relations WHERE transaction_id=?", (transaction_id,)).fetchall()
    if (tx is None or tx["status"] != "committed" or tx["task_id"] != str(task_id)
            or tx["idempotency_key"] != key or transaction_id != _transaction_id(task_id, key)
            or not event_rows or event_rows[-1]["event_type"] != "transaction_committed/v1"
            or [row["ordinal"] for row in event_rows] != list(range(event_rows[0]["ordinal"], event_rows[-1]["ordinal"] + 1))):
        _fail("control transaction lacks its original committed ordering")
    snapshot = ClosureSnapshot(store, db, task_id)
    branch = db.execute("SELECT value FROM registry_meta WHERE key='branch_id'").fetchone()[0]
    events = []
    for row in event_rows:
        snapshot.event(row)
        if (row["branch_id"] != branch or row["writer_fencing_epoch"] != tx["writer_epoch"]
                or row["task_round_id"] is not None or row["net_instance_id"] is not None
                or row["correlation_id"] != transaction_id):
            _fail("control event acquired another writer or execution scope")
        events.append(store._row_to_envelope(row))
    objects = []
    for row in object_rows:
        raw = snapshot.read(row_ref(row), ATTEMPT, descriptor=False)
        metadata = json.loads(row["metadata_json"])
        if raw != canonical_json(metadata):
            _fail("reservation bytes differ from their original descriptor")
        objects.append(PreparedObject(ATTEMPT, TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
            row["size"], row["media_type"], row["schema_ref"],
            TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
            row["storage_locator"], metadata))
    def endpoint(raw):
        value = json.loads(raw)
        return VersionRef(value["entity_type"], TypedId.parse(value.get("logical_id", value.get("entity_id"))), TypedId.parse(value["version_id"]))
    relations = [TypedRelation(TypedId.parse(row["relation_id"]), row["relation_type"],
        endpoint(row["source_json"]), endpoint(row["target_json"]), row["strength"],
        json.loads(row["metadata_json"]), system_owned=True) for row in relation_rows]
    _inventory(objects, events, relations, task_id=task_id, transaction_id=transaction_id, key=key, body=body, mode=mode)
    for row in relation_rows:
        matched = [event for event in events if event.event_type == "relation_published/v1"
                   and event.payload["relation_id"] == row["relation_id"]]
        if len(matched) != 1 or str(matched[0].event_id) != row["published_event_id"]:
            _fail("control relation publication backpointer differs")
    outbox = db.execute("SELECT * FROM outbox WHERE transaction_id=?", (transaction_id,)).fetchone()
    if (outbox is None or outbox["task_id"] != str(task_id) or outbox["writer_epoch"] != tx["writer_epoch"]
            or json.loads(outbox["event_ids_json"]) != [row["event_id"] for row in event_rows]):
        _fail("control transaction outbox differs")
    members = [("transaction", transaction_id)]
    members += [("event", row["event_id"]) for row in event_rows]
    members += [("object", row["version_id"]) for row in object_rows]
    members += [("relation", row["relation_id"]) for row in relation_rows]
    if any(db.execute("SELECT 1 FROM firing_temporary_members WHERE member_kind=? AND member_identity=?", member).fetchone()
           is not None for member in members):
        _fail("attempt budget cannot be a firing temporary member")
    return tx, event_rows


def read_attempt_state(store, db, *, task_id, owner_command_ref, firing_ref):
    """Reconstruct both slots through original canonical object/event indices."""
    if not db.in_transaction:
        _fail("attempt reads require one explicit original SQLite cut")
    admission, command, _completion, root = _identity(store, db, task_id, owner_command_ref, firing_ref)
    catalog = _catalog(store, db, task_id)
    # Discover through the original transaction task and stable slot locators.
    # Never join a possibly damaged publication pointer to decide whether the
    # budget exists. A changed descriptor likewise cannot hide either known
    # slot: their original version and transaction identities remain locators.
    keys = [reservation_key(task_id, owner_command_ref, firing_ref, slot) for slot in (0, 1)]
    versions = [_attempt_ref(task_id, owner_command_ref, firing_ref, slot)["version_id"] for slot in (0, 1)]
    rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE t.status='committed' AND ((t.task_id=? AND o.object_type=? "
        "AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=?) "
        "OR o.version_id IN (?,?) OR t.idempotency_key IN (?,?))",
        (str(task_id), ATTEMPT, owner_command_ref["version_id"], *versions, *keys)).fetchall()
    known = db.execute("SELECT * FROM transactions WHERE status='committed' AND "
        "(idempotency_key IN (?,?) OR transaction_id IN (?,?))",
        (*keys, *(_transaction_id(task_id, key) for key in keys))).fetchall()
    if any(sum(row["transaction_id"] == tx["transaction_id"] for row in rows) != 1 for tx in known):
        _fail("committed original slot transaction lacks its one exact reservation object")
    try:
        if any(row["object_type"] != ATTEMPT or type(json.loads(row["metadata_json"]).get("slot")) is not int for row in rows):
            _fail("original slot locator has a malformed reservation descriptor")
        ordered = sorted(rows, key=lambda row: json.loads(row["metadata_json"])["slot"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise RegistryConflict("material publication attempt: original slot descriptor is malformed") from exc
    attempts = []
    for row in ordered:
        body = json.loads(row["metadata_json"])
        slot = len(attempts)
        prior = attempts[-1]["conflict_event_id"] if slot else None
        if slot > 1 or (slot and prior is None):
            _fail("slot one lacks the original failed first comparison")
        expected = attempt_body(store, db, task_id=task_id, owner_command_ref=owner_command_ref,
            firing_ref=firing_ref, slot=slot, prior_conflict_event_id=prior, catalog=catalog)
        if body != expected or row_ref(row) != body["material_publication_attempt_ref"]:
            _fail("attempt changed its exact original command/slot/completion")
        key = reservation_key(task_id, owner_command_ref, firing_ref, slot)
        tx, events = _transaction(store, db, task_id, row["transaction_id"], key=key, body=body, mode="reserve")
        reserved = next(event for event in events if event["event_type"] == RESERVED)
        item = {"body": body, "writer_epoch": tx["writer_epoch"],
            "reservation_transaction_id": row["transaction_id"], "reservation_event_id": reserved["event_id"],
            "reservation_base_ordinal": events[0]["ordinal"] - 1,
            "reservation_end_ordinal": events[-1]["ordinal"],
            "reservation_event_ids": [event["event_id"] for event in events],
            "conflict_event_id": None, "conflict_transaction_id": None,
            "conflict_end_ordinal": None, "conflict_event_ids": []}
        if slot and attempts[-1]["conflict_end_ordinal"] > item["reservation_base_ordinal"]:
            _fail("retry slot predates the original conflict closure")
        conflicts = db.execute("SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
            "WHERE t.status='committed' AND e.task_id=? AND e.event_type=? "
            "AND json_extract(e.payload_json,'$.material_publication_attempt_ref.version_id')=?",
            (str(task_id), CONFLICTED, row["version_id"])).fetchall()
        if len(conflicts) > 1:
            _fail("slot has repeated ordinal-conflict facts")
        if conflicts:
            conflict = conflicts[0]
            value = json.loads(conflict["payload_json"])
            expected_conflict = {"material_publication_attempt_ref": body["material_publication_attempt_ref"],
                "reservation_event_id": item["reservation_event_id"], "proposal_key": body["proposal_key"],
                "proposal_transaction_id": _transaction_id(task_id, body["proposal_key"]),
                "expected_registry_ordinal": item["reservation_end_ordinal"],
                "observed_registry_ordinal": value.get("observed_registry_ordinal")}
            _validate_schema(catalog[1], CONFLICTED, value)
            if (value != expected_conflict or value["observed_registry_ordinal"] <= item["reservation_end_ordinal"]
                    or db.execute("SELECT 1 FROM transactions WHERE transaction_id=? AND status='committed'",
                        (value["proposal_transaction_id"],)).fetchone() is not None):
                _fail("conflict is not an uncommitted original proposal with a later head")
            conflict_tx, conflict_events = _transaction(store, db, task_id, conflict["transaction_id"],
                key=conflict_key(task_id, owner_command_ref, firing_ref, slot), body=value, mode="conflict")
            if (conflict_tx["writer_epoch"] != item["writer_epoch"]
                    or value["observed_registry_ordinal"] >= conflict_events[0]["ordinal"]):
                _fail("conflict differs from its original comparison writer/cut")
            item.update(conflict_event_id=conflict["event_id"], conflict_transaction_id=conflict["transaction_id"],
                conflict_end_ordinal=conflict_events[-1]["ordinal"],
                conflict_event_ids=[event["event_id"] for event in conflict_events])
        attempts.append(item)
    if root["state"] in {"PUBLISHED", "REJECTED"}:
        result_ref, result = terminal_result(store, db, task_id, admission, command, root)
        return {"status": "TERMINAL", "attempts": attempts, "result_ref": result_ref, "result": result}
    status = "EMPTY" if not attempts else ("PENDING" if attempts[-1]["conflict_event_id"] is None
        else "RETRY_AVAILABLE" if len(attempts) == 1 else "EXHAUSTED")
    return {"status": status, "attempts": attempts}


def require_live_attempt(store, db, *, task_id, writer_epoch, attempt):
    from ..material_publication_attempts import _token, MaterialAttemptRecoveryRequired
    from ..strict_contracts import ref_payload
    _token(attempt)
    current = int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    if (attempt._core.event_store is not store or attempt._core.task_id != task_id
            or writer_epoch != current or attempt.writer_epoch != current):
        raise StaleWriterError("material attempt belongs to another original writer generation")
    state = read_attempt_state(store, db, task_id=task_id,
        owner_command_ref=ref_payload(attempt.owner_command_ref), firing_ref=ref_payload(attempt.firing_ref))
    if state["status"] != "PENDING":
        raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original material slot is no longer pending")
    item = state["attempts"][-1]
    if (item["body"]["material_publication_attempt_ref"] != ref_payload(attempt.attempt_ref)
            or item["body"]["slot"] != attempt.slot or item["body"]["proposal_key"] != attempt.proposal_key
            or item["reservation_end_ordinal"] != attempt.expected_registry_ordinal
            or item["reservation_event_id"] != attempt.reservation_event_id
            or item["reservation_transaction_id"] != attempt.reservation_transaction_id
            or item["writer_epoch"] != attempt.writer_epoch):
        _fail("planning handle differs from its original reservation")
    return item


def validate_material_attempt_batch(store, db, *, task_id, transaction_id, idempotency_key,
        writer_epoch, task_round_id, net_instance_id, objects, events, relations,
        publication_commands, rejection_commands, supersession_commands, workspace_head_commands,
        expected_registry_ordinal):
    """Run after exact replay and before the native final ordinal comparison."""
    from ..material_publication_attempts import (_CONTROL, _ROUTE, MaterialAttemptRecoveryRequired,
        MaterialAttemptOrdinalConflict)
    from ..strict_contracts import ref_payload
    controls = [event for event in events if event.event_type in {RESERVED, CONFLICTED}]
    if controls or any(item.object_type == ATTEMPT for item in objects):
        if len(controls) != 1 or publication_commands or rejection_commands or supersession_commands or workspace_head_commands:
            _fail("attempt controls cannot carry another original publication action")
        event, = controls
        mode = "reserve" if event.event_type == RESERVED else "conflict"
        binding = _CONTROL.get()
        body = dict(event.payload)
        if (binding is None or binding.core.event_store is not store or binding.core.task_id != task_id
                or binding.core.writer_epoch != writer_epoch or binding.mode != mode or binding.body != body
                or task_round_id is not None or net_instance_id is not None or expected_registry_ordinal is not None):
            _fail("control lacks its original typed stager or acquired execution scope")
        if mode == "reserve":
            state = read_attempt_state(store, db, task_id=task_id,
                owner_command_ref=body["owner_command_ref"], firing_ref=body["transition_firing_ref"])
            if state["status"] not in {"EMPTY", "RETRY_AVAILABLE"}:
                raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: this original slot was already reserved")
            slot = len(state["attempts"])
            prior = state["attempts"][-1]["conflict_event_id"] if slot else None
            expected = attempt_body(store, db, task_id=task_id, owner_command_ref=body["owner_command_ref"],
                firing_ref=body["transition_firing_ref"], slot=slot, prior_conflict_event_id=prior, require_prepared=True)
            key = reservation_key(task_id, body["owner_command_ref"], body["transition_firing_ref"], slot)
            if (len(objects) != 1 or body != expected
                    or descriptor_store(store).read_registered(objects[0]) != canonical_json(body)):
                _fail("reserved slot differs from its original prepared execution")
        else:
            attempt, error = binding.attempt, binding.conflict
            require_live_attempt(store, db, task_id=task_id, writer_epoch=writer_epoch, attempt=attempt)
            if (type(error) is not MaterialAttemptOrdinalConflict or error.attempt is not attempt
                    or attempt._state.get("conflict") is not error
                    or body != conflict_body(task_id, attempt, error.observed_registry_ordinal)):
                _fail("conflict lacks its actual original locked failure proof")
            key = conflict_key(task_id, ref_payload(attempt.owner_command_ref), ref_payload(attempt.firing_ref), attempt.slot)
            if (body["observed_registry_ordinal"] > int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
                    or db.execute("SELECT 1 FROM transactions WHERE transaction_id=? AND status='committed'",
                        (body["proposal_transaction_id"],)).fetchone() is not None):
                _fail("the failed proposal already committed or its observed head is unavailable")
            _validate_schema(_catalog(store, db, task_id)[1], CONFLICTED, body)
        if idempotency_key != key or str(transaction_id) != _transaction_id(task_id, key):
            _fail("control identity is not derived from its original slot")
        _inventory(objects, events, relations, task_id=task_id, transaction_id=transaction_id, key=key, body=body, mode=mode)
        binding.validated_new = True
        binding.validated_db = db
        binding.validated_transaction_id = str(transaction_id)
        return None
    for publication in publication_commands:
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
            (publication["firing_version_id"],)).fetchone()
        if root is None:
            continue  # The original publication validator reports the missing root.
        firing_ref = {"entity_type": "transition_firing/v1", "logical_id": root["firing_logical_id"],
                      "version_id": root["firing_version_id"]}
        found = material_admission(store, db, task_id, firing_ref)
        if found is None:
            continue
        attempt = _ROUTE.get()
        if attempt is None:
            raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: material Success needs its fresh original attempt")
        require_live_attempt(store, db, task_id=task_id, writer_epoch=writer_epoch, attempt=attempt)
        if (ref_payload(attempt.firing_ref) != firing_ref
                or ref_payload(attempt.owner_command_ref) != found[0]["owner_command_ref"]
                or attempt.proposal_key != idempotency_key
                or expected_registry_ordinal != attempt.expected_registry_ordinal
                or not attempt._state.get("checked") or attempt._state.get("submitted")
                or attempt._state.get("conflict") is not None):
            raise MaterialAttemptRecoveryRequired("RECOVERY_REQUIRED: original material slot cannot be submitted again")
        attempt._state["submitted"] = True
        return attempt
    return None


def attempt_visibility_exceptions(context):
    """Four exact body refs after this same locked pure-control proof.

    No relation endpoint or unrelated payload receives a visibility exception.
    The reservation remains canonical framework control outside every root.
    """
    from ..material_publication_attempts import _CONTROL
    binding = _CONTROL.get()
    if (binding is None or binding.mode != "reserve" or not binding.validated_new
            or binding.validated_db is not context.db
            or binding.validated_transaction_id != str(context.transaction_id)
            or binding.core.event_store is not context.event_store):
        return {}
    _inventory(context.objects, context.events, context.relations, task_id=context.task_id,
        transaction_id=context.transaction_id, key=context.idempotency_key, body=binding.body, mode="reserve")
    root = binding.body["transition_firing_ref"]["version_id"]
    return {binding.body[name]["version_id"]: root for name in (
        "transition_firing_ref", "invocation_ref", "firing_admission_ref", "workspace_revision_candidate_ref")
        if binding.body[name] is not None}


__all__ = ("ATTEMPT", "RESERVED", "CONFLICTED", "read_attempt_state", "attempt_body",
    "reservation_key", "conflict_key", "conflict_body", "require_live_attempt", "validate_material_attempt_batch",
    "attempt_visibility_exceptions")
