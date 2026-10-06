"""Complete local normal-child RootTerminal verification in one DB snapshot.

This reader establishes the target Registry's Workset/Success/child closure.
It does not discover source Registries or claim to re-audit remote deliveries.
The internal entry also serves strict completion replay on an existing writer.
"""
from __future__ import annotations

import hashlib
import json

from ..registry.event_store import RegistryConflict
from ..registry.execution_child_closure import (
    CHILD_OBJECTS, PROFILE, ROOT_V2, SEAL, ClosureSnapshot, child_seal_ref, child_snapshot,
    exact_ref, validate_seal_mappings,
)
from ..registry.identities import TypedId
from ..registry.models import PreparedObject
from ..registry.schema_catalog import canonical_text
from ..registry._event_store.queries import _row_to_envelope
from ..registry._event_store.workset_publication import _success, _validate_terminal_binding
from .references import SourceQualifiedVersionRef
from .worksets import (
    ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, WORKSET, acceptance_identity,
    command_key, qualified, record_ref,
)


def _fail(message):
    raise RegistryConflict("root terminal closure: " + message)


def _row_ref(row):
    return {"entity_type": row["object_type"], "logical_id": row["logical_id"],
            "version_id": row["version_id"]}


def _prepared(row):
    return PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"],
        row["schema_ref"], TypedId.parse(row["producer_invocation_id"])
        if row["producer_invocation_id"] else None,
        row["storage_locator"], json.loads(row["metadata_json"]))


def _qualified(snapshot, reference, kind=None):
    if (not isinstance(reference, dict)
            or set(reference) != {"schema_version", "source_id", "ref"}
            or reference["schema_version"] != "rpnh/collaboration/source_version_ref/v1"
            or reference["source_id"] != snapshot.source()["source_id"]):
        _fail("reference is not an exact qualified local reference")
    return exact_ref(reference["ref"], kind)


def _document(snapshot, reference, kind):
    ref = _qualified(snapshot, reference, kind)
    document = snapshot.read(ref, kind)
    source = snapshot.source()
    if (document["record_ref"] != reference
            or document["owner_task_ref"] != {
                "schema_version": "rpnh/collaboration/source_version_ref/v1",
                "source_id": source["source_id"], "ref": source["task_ref"]}
            or document["schema_version"] != "registry_v1/" + kind):
        _fail("record self, owner or schema identity differs")
    return document


def _transaction(snapshot, transaction_id):
    """Verify a persisted batch without using cached or connection-opening APIs."""
    db = snapshot.db
    row = db.execute("SELECT * FROM transactions WHERE transaction_id=?",
                     (transaction_id,)).fetchone()
    events = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
                        (transaction_id,)).fetchall()
    outbox = db.execute("SELECT * FROM outbox WHERE transaction_id=?",
                        (transaction_id,)).fetchone()
    if (row is None or row["status"] != "committed"
            or row["task_id"] != str(snapshot.task_id) or not events
            or events[-1]["event_type"] != "transaction_committed/v1"
            or sum(e["event_type"] == "transaction_committed/v1" for e in events) != 1
            or any(e["event_type"] == "transaction_aborted/v1" for e in events)
            or events[-1]["ordinal"] > snapshot.cut
            or [e["ordinal"] for e in events] != list(range(events[0]["ordinal"], events[-1]["ordinal"] + 1))
            or outbox is None or outbox["task_id"] != str(snapshot.task_id)
            or outbox["writer_epoch"] != row["writer_epoch"]
            or json.loads(outbox["event_ids_json"]) != [e["event_id"] for e in events]):
        _fail("original committed transaction/outbox/cut differs")
    for event in events:
        snapshot.event(event)
        if (event["writer_fencing_epoch"] != row["writer_epoch"]
                or event["idempotency_key"] != row["idempotency_key"]
                or event["command_id"] != row["idempotency_key"]
                or event["event_schema_version"] != event["event_type"].rsplit("/", 1)[-1]
                or event["payload_schema_ref"] != "registry_v1/" + event["event_type"]):
            _fail("transaction fact command/schema/fence differs")
        sequence = db.execute("SELECT COUNT(*) FROM events WHERE stream_id=? AND ordinal<=?",
                              (event["stream_id"], event["ordinal"])).fetchone()[0]
        if event["stream_sequence"] != sequence or event["aggregate_version"] != sequence:
            _fail("original fact stream order differs")
    objects = db.execute("SELECT * FROM objects WHERE transaction_id=?", (transaction_id,)).fetchall()
    pubs = [e for e in events if e["event_type"] == "object_version_published/v1"]
    if {e["event_id"] for e in pubs} != {o["published_event_id"] for o in objects} or len(pubs) != len(objects):
        _fail("transaction object publication inventory differs")
    relations = db.execute("SELECT * FROM relations WHERE transaction_id=?", (transaction_id,)).fetchall()
    published_relations = {event["event_id"]: event for event in events if event["event_type"] == "relation_published/v1"}
    if set(published_relations) != {relation["published_event_id"] for relation in relations}:
        _fail("transaction relation publication inventory differs")
    for relation in relations:
        event = published_relations[relation["published_event_id"]]
        expected = {"relation_id": relation["relation_id"], "relation_type": relation["relation_type"],
            "source": json.loads(relation["source_json"]), "target": json.loads(relation["target_json"]),
            "strength": relation["strength"], "metadata": json.loads(relation["metadata_json"])}
        if (json.loads(event["payload_json"]) != expected
                or event["stream_id"] != "relation:" + relation["relation_id"]
                or event["aggregate_id"] != relation["relation_id"]
                or event["aggregate_type"] != "typed_relation/v1"):
            _fail("persisted relation differs from its original publication")
    terminal = json.loads(events[-1]["payload_json"])
    if terminal != {"object_count": len(objects), "relation_count": len(relations), "fact_count": len(events) - 1}:
        _fail("transaction terminal counts differ from its complete batch")
    return row, events, objects


class _ReadSuccess:
    """Read-context adapter for the existing ordinary Success validators."""

    def __init__(self, snapshot, document):
        self.event_store, self.db, self.task_id = snapshot.event_store, snapshot.db, snapshot.task_id
        ref = document["record_ref"]["ref"]
        snapshot.read(ref, ref["entity_type"])
        object_row = snapshot.rows[ref["version_id"]]
        self.published_transaction_id = object_row["transaction_id"]
        self.transaction_id = TypedId.parse(self.published_transaction_id)
        tx, events, rows = _transaction(snapshot, self.published_transaction_id)
        self.historical_cut = events[0]["ordinal"] - 1
        self.snapshot = ClosureSnapshot(self.event_store, self.db, self.task_id, cut=events[-1]["ordinal"])
        self.idempotency_key = tx["idempotency_key"]
        self.transaction_writer_epoch = tx["writer_epoch"]
        self.events = tuple(_row_to_envelope(event) for event in events)
        self.objects = tuple(_prepared(row) for row in rows)
        self._metadata = {}
        if document["command_id"] != self.idempotency_key:
            _fail("business record command differs from its original Success")
        body = document["body"]
        firing = self.metadata(body["firing_ref"], "transition_firing/v1")
        invocation = self.metadata(body["invocation_ref"], "invocation/v1")
        self.parent = {"parent_invocation_ref": body["invocation_ref"],
            "parent_business_firing_ref": body["firing_ref"],
            "parent_business_net_ref": firing["net_instance_ref"],
            "parent_business_checkpoint_ref": firing["admission_marking_checkpoint_ref"]}
        root = self.db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                               (body["firing_ref"]["version_id"],)).fetchone()
        if (root is None or root["state"] != "PUBLISHED"
                or root["published_transaction_id"] != self.published_transaction_id
                or root["firing_logical_id"] != body["firing_ref"]["logical_id"]
                or root["invocation_version_id"] != body["invocation_ref"]["version_id"]
                or root["invocation_logical_id"] != body["invocation_ref"]["logical_id"]
                or root["net_version_id"] != firing["net_instance_ref"]["version_id"]
                or root["operation_binding_version_id"] != firing["operation_binding_ref"]["version_id"]
                or root["admission_checkpoint_version_id"] != firing["admission_marking_checkpoint_ref"]["version_id"]
                or invocation["own_transition_firing_ref"] != body["firing_ref"]
                or invocation["invocation_ref"] != body["invocation_ref"]
                or firing["transition_firing_ref"] != body["firing_ref"]
                or invocation["net_instance_ref"] != firing["net_instance_ref"]
                or invocation["admission_marking_checkpoint_ref"] != firing["admission_marking_checkpoint_ref"]):
            _fail("original PUBLISHED parent authority differs")
        self.publication = root
        _transaction(self.snapshot, root["opened_transaction_id"])
        for reference in (body["firing_ref"], body["invocation_ref"], ref):
            self.snapshot.read(reference, parent=self.parent)
        for event in events:
            self.snapshot.event(event, parent=self.parent)
            if event["event_type"] == "relation_published/v1":
                self.snapshot.member("relation", json.loads(event["payload_json"])["relation_id"], self.parent)
            if (event["task_round_id"] != invocation["task_round_ref"]["logical_id"]
                    or event["net_instance_id"] != firing["net_instance_ref"]["logical_id"]):
                _fail("original Success fact belongs to another round or net")
        for obj in self.objects:
            reference = {"entity_type": obj.object_type, "logical_id": str(obj.logical_id),
                         "version_id": str(obj.version_id)}
            self.metadata(reference)
            self.snapshot.member("object", reference["version_id"], self.parent)

    def metadata(self, reference, kind=None):
        exact_ref(reference, kind)
        version = reference["version_id"]
        key = canonical_text(reference)
        if key not in self._metadata:
            resource = reference["entity_type"] == "resource_version/v1"
            value = self.snapshot.read(reference, descriptor=not resource)
            row = self.snapshot.rows[version]
            self._metadata[key] = json.loads(row["metadata_json"]) if resource else value
            # Dependencies must be canonical by this original Success, not
            # promoted by an unrelated transaction that happened afterwards.
            promotions = self.db.execute("SELECT p.published_transaction_id FROM firing_temporary_members m "
                "JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind='object' AND m.member_identity=?", (version,)).fetchall()
            for promotion in promotions:
                terminal = self.db.execute("SELECT ordinal FROM events WHERE transaction_id=? "
                    "AND event_type='transaction_committed/v1'", (promotion[0],)).fetchall()
                if len(terminal) != 1 or terminal[0][0] > self.snapshot.cut:
                    _fail("dependency was not canonical at the original Success")
        return self._metadata[key]

    def exact_ref_exists(self, reference, kind=None):
        self.metadata(reference, kind)
        return True

    def version_metadata(self, version_id, object_type):
        row = self.db.execute("SELECT * FROM objects WHERE version_id=? AND object_type=?",
                              (str(version_id), object_type)).fetchone()
        if row is None:
            _fail("exact Success dependency is absent")
        return self.metadata(_row_ref(row), object_type)

    def version_transaction(self, version_id):
        row = self.db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (str(version_id),)).fetchone()
        return None if row is None else row[0]

    def persisted_member_visible(self, kind, identity):
        if kind != "event":
            _fail("unsupported persisted member lookup")
        row = self.db.execute("SELECT * FROM events WHERE event_id=?", (str(identity),)).fetchone()
        self.snapshot.event(row, parent=self.parent)
        return True


def _success_record(snapshot, document):
    context = _ReadSuccess(snapshot, document)
    body = document["body"]
    reference = document["record_ref"]["ref"]
    obj = next(obj for obj in context.objects if str(obj.version_id) == reference["version_id"])
    firing, delta = _success(context, obj, body)
    completion = context.metadata(body["completion_ref"], "firing_completion/v2")
    result_ref = completion["operation_result_ref"]
    result = context.metadata(result_ref, "operation_result/v1")
    checkpoint = context.metadata(body["checkpoint_ref"], "marking_checkpoint/v1")
    invocation = context.metadata(body["invocation_ref"], "invocation/v1")
    if (completion["firing_completion_ref"] != body["completion_ref"]
            or completion["business_outcome"] != "completed"
            or completion["successor_checkpoint_ref"] != body["checkpoint_ref"]
            or result["operation_result_ref"] != result_ref
            or result["output_resource_refs"] != sorted(result["output_resource_refs"],
                key=lambda ref: (ref["entity_type"], ref["logical_id"], ref["version_id"]))
            or context.publication["operation_result_version_id"] != result_ref["version_id"]
            or context.publication["marking_checkpoint_version_id"] != body["checkpoint_ref"]["version_id"]
            or checkpoint["marking_checkpoint_ref"] != body["checkpoint_ref"]
            or checkpoint["net_instance_ref"] != firing["net_instance_ref"]
            or checkpoint["settled"] is not True
            or checkpoint["settlement_delta_ref"] != completion["marking_delta_ref"]
            or checkpoint["transition_firing_refs"] != [body["firing_ref"]]
            or delta["marking_delta_ref"] != completion["marking_delta_ref"]
            or delta["net_instance_ref"] != firing["net_instance_ref"]
            or delta["phase"] != "settlement"
            or delta["transition_firing_refs"] != [body["firing_ref"]]
            or delta["operation_binding_refs"] != [firing["operation_binding_ref"]]
            or completion["workspace_revision_ref"] is not None
            or completion["workspace_access_set_ref"] is not None
            or result["workspace_access_set_ref"] is not None
            or checkpoint["workspace_revision_refs"]
            or context.publication["workspace_lineage_id"] is not None
            or context.publication["workspace_revision_version_id"] is not None):
        _fail("result/completion/delta/checkpoint closure differs")
    for ref in (reference, body["completion_ref"], body["checkpoint_ref"], body["occurrence_ref"],
                result_ref, completion["marking_delta_ref"]):
        if context.version_transaction(ref["version_id"]) != context.published_transaction_id:
            _fail("Success closure was not published in its original transaction")
        if context.snapshot.rows[ref["version_id"]]["producer_invocation_id"] != body["invocation_ref"]["logical_id"]:
            _fail("Success closure object belongs to another producer")
    for kind in ("operation_result/v1", "firing_completion/v2", "marking_delta/v1", "marking_checkpoint/v1"):
        if sum(item.object_type == kind for item in context.objects) != 1:
            _fail("ordinary Success closure object cardinality differs")
    settled = [event for event in context.events if event.event_type == "transition_firing_settled/v1"]
    event = settled[0]
    owned_events = context.db.execute("SELECT e.* FROM firing_temporary_members m JOIN events e "
        "ON e.event_id=m.member_identity WHERE m.firing_version_id=? AND m.member_kind='event' ORDER BY e.ordinal",
        (body["firing_ref"]["version_id"],)).fetchall()
    ordered = [{"event_id": row["event_id"], "ordinal": row["ordinal"], "event_type": row["event_type"]}
               for row in owned_events]
    if (event.stream_id != "firing-claims:" + firing["net_instance_ref"]["version_id"]
            or event.aggregate_id != body["firing_ref"]["logical_id"]
            or event.aggregate_type != "transition_firing"
            or str(event.producer_invocation_id) != body["invocation_ref"]["logical_id"]
            or event.producer_principal != invocation["principal_ref"]["logical_id"]
            or event.stream_sequence != completion["settled_sequence"]
            or event.payload.get("settlement_event_id") != str(event.event_id)
            or event.payload.get("ordered_event_records") != ordered
            or any(event.payload[key] != completion[key] for key in
                ("transition_firing_ref", "firing_completion_ref", "operation_result_ref", "business_outcome",
                 "workspace_access_set_ref", "workspace_revision_ref", "marking_delta_ref", "successor_checkpoint_ref"))
            or event.payload["transaction_ref"]["logical_id"] != context.published_transaction_id):
        _fail("original settled fact differs from completion")
    ready = [event for event in context.events if event.event_type == "operation_terminal_ready/v1"]
    if (len(ready) != 1 or ready[0].stream_id != "invocation:" + body["invocation_ref"]["logical_id"]
            or ready[0].aggregate_id != body["invocation_ref"]["logical_id"]
            or ready[0].aggregate_type != "invocation"
            or str(ready[0].producer_invocation_id) != body["invocation_ref"]["logical_id"]
            or ready[0].producer_principal != invocation["principal_ref"]["logical_id"]
            or ready[0].payload["invocation_ref"] != body["invocation_ref"]
            or ready[0].payload["operation_execution_lease_ref"] != invocation["operation_execution_lease_ref"]
            or any(ready[0].payload[key] != result[key] for key in
                ("operation_result_ref", "business_outcome", "output_resource_refs",
                 "workspace_access_set_ref", "provider_attempt_evidence_refs"))):
        _fail("original operation terminal-ready fact differs from result")
    _checkpoint_fact(context, checkpoint, invocation)
    return context, delta


def _checkpoint_fact(context, checkpoint, invocation):
    events = [event for event in context.events if event.event_type == "marking_checkpoint_committed/v1"]
    payload = {key: checkpoint[key] for key in ("net_instance_ref", "team_design_root_ref", "previous_checkpoint_ref",
        "settlement_delta_ref", "transition_firing_refs", "workspace_revision_refs", "settled")}
    payload["checkpoint_ref"] = checkpoint["marking_checkpoint_ref"]
    settled = next(event for event in context.events if event.event_type == "transition_firing_settled/v1")
    payload["settlement_event_id"] = str(settled.event_id)
    if (len(events) != 1 or dict(events[0].payload) != payload
            or events[0].stream_id != "marking:" + checkpoint["net_instance_ref"]["logical_id"]
            or events[0].aggregate_id != checkpoint["net_instance_ref"]["logical_id"]
            or events[0].aggregate_type != "marking_checkpoint"
            or str(events[0].producer_invocation_id) != invocation["invocation_ref"]["logical_id"]
            or events[0].producer_principal != invocation["principal_ref"]["logical_id"]):
        _fail("business checkpoint commit fact differs")
    predecessors = context.db.execute("SELECT * FROM events WHERE event_type='marking_checkpoint_committed/v1' "
        "AND stream_id=? AND ordinal<=? ORDER BY ordinal DESC LIMIT 1",
        (events[0].stream_id, context.historical_cut)).fetchall()
    if (len(predecessors) != 1
            or context.snapshot.event(predecessors[0])["checkpoint_ref"] != checkpoint["previous_checkpoint_ref"]):
        _fail("Success did not advance the exact previous marking head")


def _scope_dependency(snapshot, reference):
    ref = _qualified(snapshot, reference)
    snapshot.read(ref, descriptor=ref["entity_type"] != "resource_version/v1")
    rows = snapshot.db.execute("SELECT e.ordinal FROM firing_temporary_members m "
        "JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
        "JOIN events e ON e.transaction_id=p.published_transaction_id "
        "AND e.event_type='transaction_committed/v1' WHERE m.member_kind='object' AND m.member_identity=?",
        (ref["version_id"],)).fetchall()
    if any(row[0] > snapshot.cut for row in rows):
        _fail("Workset scope was not canonical when the collection was created")


def _workset_history(snapshot, root, root_context):
    expected = root["body"]["expected"]
    previous_ref = _qualified(snapshot, expected["record_ref"], WORKSET)
    rows = snapshot.db.execute("SELECT o.*,e.stream_sequence,e.ordinal FROM objects o "
        "JOIN events e ON e.event_id=o.published_event_id WHERE o.logical_id=? ORDER BY e.ordinal",
        (previous_ref["logical_id"],)).fetchall()
    stream = "object:" + previous_ref["logical_id"]
    events = snapshot.db.execute("SELECT * FROM events WHERE stream_id=? ORDER BY ordinal", (stream,)).fetchall()
    head = snapshot.db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?", (stream,)).fetchone()
    if (not rows or len(rows) != len(events) or head is None or head[0] != len(rows)
            or [row["stream_sequence"] for row in rows] != list(range(1, len(rows) + 1))
            or [event["event_id"] for event in events] != [row["published_event_id"] for row in rows]):
        _fail("Workset immutable history/stream head differs")
    previous = None
    source = snapshot.source()["source_id"]
    for row in rows:
        reference = {"schema_version": "rpnh/collaboration/source_version_ref/v1",
                     "source_id": source, "ref": _row_ref(row)}
        document = _document(snapshot, reference, WORKSET)
        body = document["body"]
        tx, transaction_events, batch = _transaction(snapshot, row["transaction_id"])
        if (body["sequence"] != row["stream_sequence"] or body["required_child_seal_ref"] is not None
                or tx["idempotency_key"] not in {document["command_id"], command_key(document["command_id"])}
                or len([obj for obj in batch if obj["object_type"] == WORKSET]) != 1):
            _fail("Workset version/source/command/None-child invariant differs")
        wanted_version = record_ref(WORKSET, snapshot.task_id, "unused", document["command_id"]).version_id
        if row["version_id"] != str(wanted_version):
            _fail("Workset version is not bound to its command")
        dependencies = [obj for obj in batch if obj["object_type"] in {ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, ROOT_V2}]
        if previous is None:
            if (body["action"] != "create" or body["expected"] is not None or body["sequence"] != 1
                    or body["collection_version"] != 1 or body["state"] != "open"
                    or body["acceptances"] or body["contributions"] or body["terminal_ref"] is not None
                    or row["producer_invocation_id"] is not None or dependencies
                    or row["logical_id"] != str(record_ref(WORKSET, snapshot.task_id,
                        document["command_id"], document["command_id"]).entity_id)):
                _fail("Workset creation scope differs")
            scope_cut = ClosureSnapshot(snapshot.event_store, snapshot.db, snapshot.task_id,
                                        cut=transaction_events[0]["ordinal"] - 1)
            _scope_dependency(scope_cut, body["requirements_ref"])
            _scope_dependency(scope_cut, body["input_binding_ref"])
        else:
            before = previous["body"]
            expectation = {"record_ref": previous["record_ref"], "stream_head": before["sequence"],
                           "command_id": previous["command_id"]}
            if body["expected"] != expectation or before["state"] == "completed":
                _fail("Workset history has a stale CAS or reopens completion")
            allowed = {"action", "expected", "sequence"}
            action = body["action"]
            if action == "grow":
                if (before["state"] != "open" or body["collection_version"] != before["collection_version"] + 1
                        or not set(before["expected_slots"]) < set(body["expected_slots"])
                        or before["acceptances"] or before["contributions"] or dependencies
                        or body["acceptances"] or body["contributions"] or row["producer_invocation_id"] is not None):
                    _fail("Workset growth differs from its open empty scope")
                allowed |= {"collection_version", "expected_slots"}
            elif action == "seal":
                if before["state"] != "open" or body["state"] != "sealed" or dependencies or row["producer_invocation_id"] is not None:
                    _fail("Workset collection seal differs")
                allowed.add("state")
            elif action in {"accept", "contribute", "complete"}:
                kind = {"accept": ACCEPTANCE, "contribute": CONTRIBUTION, "complete": ROOT_V2}[action]
                if len(dependencies) != 1 or dependencies[0]["object_type"] != kind:
                    _fail("Workset action lacks its unique same-Success record")
                business_ref = {"schema_version": "rpnh/collaboration/source_version_ref/v1",
                    "source_id": source, "ref": _row_ref(dependencies[0])}
                record = _document(snapshot, business_ref, kind)
                if (record["body"]["expected"] != expectation or record["command_id"] != document["command_id"]
                        or dependencies[0]["producer_invocation_id"] != row["producer_invocation_id"]):
                    _fail("Workset and business record compare different owner authority")
                if action == "complete":
                    context = root_context
                    completion = context.metadata(root["body"]["completion_ref"])
                    delta = context.metadata(completion["marking_delta_ref"])
                else:
                    context, delta = _success_record(snapshot, record)
                _business_history(snapshot, action, previous, document, record, context, delta, root)
                allowed |= {"acceptances"} if action == "accept" else {"contributions"} if action == "contribute" else {"state", "terminal_ref"}
            else:
                _fail("unsupported Workset history action")
            if any(body[key] != before[key] for key in body if key not in allowed):
                _fail("Workset history changed unrelated scope")
        previous = document
    if (previous["body"]["action"] != "complete" or previous["body"]["terminal_ref"] != root["record_ref"]
            or previous["body"]["expected"] != expected
            or rows[-1]["transaction_id"] != root_context.published_transaction_id):
        _fail("root does not close the exact final Workset successor")
    return previous


def _business_history(snapshot, action, previous, after, record, context, delta, root):
    before, body, successor = previous["body"], record["body"], after["body"]
    count = snapshot.db.execute("SELECT COUNT(*) FROM objects WHERE logical_id=?",
        (record["record_ref"]["ref"]["logical_id"],)).fetchone()[0]
    if count != 1:
        _fail("business collaboration record is not immutable")
    if action == "accept":
        slot = body["slot"]
        identity = acceptance_identity(body["logical_delivery_ref"], previous, slot)
        wanted = record_ref(ACCEPTANCE, snapshot.task_id, identity, record["command_id"])
        if (slot not in before["expected_slots"] or slot in before["acceptances"]
                or body["decision"] not in {"new", "carry"}
                or record["record_ref"] != qualified(snapshot.source()["source_id"], wanted)
                or any(body[key] != value for key, value in identity.items())
                or body["physical_delivery_ref"]["source_id"] != body["logical_delivery_ref"]["source_id"]
                or successor["acceptances"] != {**before["acceptances"], slot: record["record_ref"]}
                or hashlib.sha256(context.snapshot.read(body["output_resource_ref"], descriptor=False)).hexdigest() != body["content_digest"]):
            _fail("local acceptance identity/slot/bytes differ")
    elif action == "contribute":
        slot = body["slot"]
        accepted = _document(context.snapshot, body["acceptance_ref"], ACCEPTANCE)
        if (slot not in before["expected_slots"] or before["acceptances"].get(slot) != body["acceptance_ref"]
                or slot in before["contributions"] or accepted["body"]["slot"] != slot
                or accepted["body"]["occurrence_ref"] not in delta["consumed_refs"]
                or successor["contributions"] != {**before["contributions"], slot: record["record_ref"]}
                or record["record_ref"] != qualified(snapshot.source()["source_id"],
                    record_ref(CONTRIBUTION, snapshot.task_id, record["command_id"], record["command_id"]))):
            _fail("contribution did not consume its current accepted occurrence")
    else:
        if (record != root or before["state"] != "sealed"
                or set(before["expected_slots"]) != set(before["acceptances"])
                or set(before["expected_slots"]) != set(before["contributions"])
                or body["contribution_refs"] != list(before["contributions"].values())
                or successor["state"] != "completed" or successor["terminal_ref"] != root["record_ref"]):
            _fail("root does not complete every sealed Workset slot")
        for slot, reference in before["contributions"].items():
            contribution = _document(context.snapshot, reference, CONTRIBUTION)
            if (contribution["body"]["slot"] != slot
                    or contribution["body"]["occurrence_ref"] not in delta["consumed_refs"]):
                _fail("root did not actually consume every contribution")


def _root_request(context, root):
    body = root["body"]
    completion = context.metadata(body["completion_ref"])
    result = context.metadata(completion["operation_result_ref"])
    bundle, outcomes = [], set()
    for ref in result["output_resource_refs"]:
        resource = context.metadata(ref, "resource_version/v1")
        bundle.append({"port_id": resource["descriptors"]["output_port_id"],
            "output_binding_ref": resource["origin"]["primary_ref"], "resource_ref": ref})
        outcomes.add(resource["descriptors"]["output_outcome_id"])
    bundle.sort(key=lambda value: (value["port_id"], value["resource_ref"]["version_id"],
                                   value["output_binding_ref"]["version_id"]))
    request = body["completion_request"]
    selected = [item for item in bundle if item["port_id"] == request["output_port"]]
    if (request["output_bundle"] != bundle or outcomes != {request["selected_outcome_id"]}
            or len({canonical_text(item["resource_ref"]) for item in bundle}) != len(bundle)
            or len(selected) != 1 or selected[0]["resource_ref"] != body["output_resource_ref"]):
        _fail("completion request differs from the complete registered product bundle")
    firing = context.metadata(body["firing_ref"])
    rows = context.db.execute("SELECT * FROM objects WHERE object_type='executable_transition_binding/v1'").fetchall()
    matches = [row for row in rows if json.loads(row["metadata_json"]).get("operation_binding_ref") == firing["operation_binding_ref"]
               and json.loads(row["metadata_json"]).get("net_instance_ref") == firing["net_instance_ref"]]
    if len(matches) != 1:
        _fail("root lacks one exact executable terminal binding")
    context.metadata(_row_ref(matches[0]), "executable_transition_binding/v1")
    _validate_terminal_binding(context, body)


def _closed_parent(context, seal):
    """Prove historical active roots and reject late same-parent lifecycle."""
    db, cut = context.db, context.historical_cut
    active = db.execute("SELECT p.* FROM firing_publications p JOIN events opened "
        "ON opened.transaction_id=p.opened_transaction_id AND opened.event_type='transaction_committed/v1' "
        "LEFT JOIN events published ON published.transaction_id=p.published_transaction_id "
        "AND published.event_type='transaction_committed/v1' WHERE opened.ordinal<=? "
        "AND (published.ordinal IS NULL OR published.ordinal>?)", (cut, cut)).fetchall()
    if len(active) != 1 or active[0]["firing_version_id"] != seal["parent_business_firing_ref"]["version_id"]:
        _fail("root bypassed another active ordinary firing at its historical cut")
    instances = {child["execution_instance_ref"]["version_id"] for child in seal["children"]}
    firing_id = seal["parent_business_firing_ref"]["version_id"]
    inv = seal["parent_invocation_ref"]["logical_id"]
    for row in db.execute("SELECT o.*,e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id "
                         "WHERE o.object_type IN ('execution_instance/v1','execution_net_definition/v1',"
                         "'execution_checkpoint/v1','execution_token/v1','execution_transition_firing/v1')").fetchall():
        data = json.loads(row["metadata_json"])
        owned = (row["producer_invocation_id"] == inv
            or data.get("parent_business_firing_ref", {}).get("version_id") == firing_id
            or data.get("execution_instance_ref", {}).get("version_id") in instances
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE firing_version_id=? "
                "AND member_kind='object' AND member_identity=?", (firing_id, row["version_id"])).fetchone() is not None)
        if owned and row["ordinal"] > cut:
            _fail("child lifecycle changed within or after original sealing Success")
        if owned and row["object_type"] == "execution_checkpoint/v1":
            child = next((item for item in seal["children"] if item["execution_instance_ref"] == data["execution_instance_ref"]), None)
            if child is None or row["logical_id"] != child["execution_checkpoint_ref"]["logical_id"]:
                _fail("child checkpoint inventory escaped the sealed history")
    late = db.execute("SELECT * FROM events WHERE event_type='execution_instance_attached/v1' AND ordinal>?",
                      (cut,)).fetchall()
    for event in late:
        body = json.loads(event["payload_json"])
        if (event["producer_invocation_id"] == inv or event["stream_id"] == seal["child_stream_id"]
                or event["aggregate_id"] == seal["parent_business_firing_ref"]["logical_id"]
                or body.get("parent_business_firing_ref") == seal["parent_business_firing_ref"]
                or body.get("execution_instance_ref", {}).get("version_id") in instances):
            _fail("closed parent received a late child attachment")


def _seal(context, root):
    body = root["body"]
    reference = _qualified(context.snapshot, body["required_child_seal_ref"], SEAL)
    stable = child_seal_ref(context.task_id, root["command_id"])
    if reference != {"entity_type": stable.entity_type, "logical_id": str(stable.entity_id),
                     "version_id": str(stable.version_id)}:
        _fail("child seal identity differs from the original Success command")
    seal = context.metadata(reference, SEAL)
    if (seal["execution_child_seal_ref"] != reference or body["closure_profile"] != PROFILE
            or seal["closure_profile"] != PROFILE
            or seal["success_command_id"] != root["command_id"]
            or seal["success_transaction_id"] != context.published_transaction_id
            or context.version_transaction(reference["version_id"]) != context.published_transaction_id
            or context.snapshot.rows[reference["version_id"]]["producer_invocation_id"] != body["invocation_ref"]["logical_id"]
            or seal["parent_invocation_ref"] != body["invocation_ref"]
            or seal["parent_business_firing_ref"] != body["firing_ref"]
            or seal["successor_business_checkpoint_ref"] != body["checkpoint_ref"]
            or seal["operation_result_ref"] != context.metadata(body["completion_ref"])["operation_result_ref"]
            or any(seal[key] != value for key, value in context.parent.items())):
        _fail("root child seal differs from original Success authority")
    if sum(obj.object_type == SEAL for obj in context.objects) != 1 or sum(obj.object_type == ROOT_V2 for obj in context.objects) != 1:
        _fail("original Success does not contain one root and one child seal")
    if any(obj.object_type in CHILD_OBJECTS for obj in context.objects):
        _fail("original Success contains a new child lifecycle object")
    prior = ClosureSnapshot(context.event_store, context.db, context.task_id, cut=context.historical_cut)
    expected = child_snapshot(prior, context.parent)
    children = [{key: child[key] for key in ("execution_instance_ref", "execution_net_definition_ref",
                 "execution_checkpoint_ref", "evidence_refs")} for child in seal["children"]]
    if (children != expected["children"]
            or any(seal[key] != value for key, value in expected.items() if key != "children")):
        _fail("seal differs from the exhaustive historical child snapshot")
    validate_seal_mappings(seal, context.objects, context.events)
    if any(obj.metadata["workspace_revision_ref"] is not None for obj in context.objects
           if obj.object_type == "execution_terminal_mapping/v1"):
        _fail("normal ordinary child mapping invents a workspace successor")
    stream = context.db.execute("SELECT * FROM events WHERE stream_id=? ORDER BY stream_sequence",
                                (seal["child_stream_id"],)).fetchall()
    head = context.db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?", (seal["child_stream_id"],)).fetchone()
    if (head is None or head[0] != len(stream) or len(stream) != seal["pre_seal_stream_head"] + 1
            or [event["stream_sequence"] for event in stream] != list(range(1, len(stream) + 1))
            or stream[-1]["event_type"] != "execution_children_sealed/v1"
            or stream[-1]["transaction_id"] != context.published_transaction_id):
        _fail("child stream head/seal order or late lifecycle differs")
    _closed_parent(context, seal)
    return seal


def read_root_terminal_snapshot(core, db, reference):
    """Verify a full SQ root/v2 ref using the caller's already-open snapshot."""
    if not db.in_transaction:
        _fail("a caller-owned SQLite snapshot is required")
    snapshot = ClosureSnapshot(core.event_store, db, core.task_id)
    root = _document(snapshot, reference, ROOT_V2)
    source = snapshot.source()
    snapshot.read(source["binding_ref"], "collaboration_source_binding/v1")
    for name in ("task_ref", "native_run_ref", "bootstrap_command_ref"):
        snapshot.read(source[name])
    if root["record_ref"] != qualified(source["source_id"],
            record_ref(ROOT_V2, core.task_id, root["command_id"], root["command_id"])):
        _fail("root identity is not bound to its original command")
    context, _delta = _success_record(snapshot, root)
    workset = _workset_history(snapshot, root, context)
    _root_request(context, root)
    seal = _seal(context, root)
    return {"root": root, "workset": workset, "seal": seal,
            "successor_checkpoint_ref": root["body"]["checkpoint_ref"],
            "verified_at_cut": snapshot.current_cut}


def read_root_terminal(core, reference):
    """Cold public read: one read-only Core, one BEGIN, no runtime hydration."""
    if not getattr(core, "read_only", False) or not core.event_store.read_only or not core.object_store.read_only:
        _fail("public root reads require a read-only Registry Core")
    reference = reference.to_dict() if isinstance(reference, SourceQualifiedVersionRef) else reference
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return read_root_terminal_snapshot(core, db, reference)
