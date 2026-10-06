"""Normal execution/v1 closure over one supplied SQLite snapshot.

No dispatcher, recovery, source discovery, or independent settlement writer.
The writer stages this object only beside the original Success mappings.
"""
from __future__ import annotations

import json
from collections import Counter

from .event_store import RegistryConflict
from .execution_net import (ExecutionInputArc, ExecutionNetDefinition,
                            ExecutionOutputArc, ExecutionTransition)
from .identities import TypedId
from .models import PreparedObject, VersionRef
from .publication import _ref_payload, _stable_id
from .schema_catalog import canonical_json, canonical_text
from ._event_store.collaboration_descriptors import (
    _canonical_closure, descriptor_store, readable_descriptor,
)
from ._event_store.source_identity import read_source_binding


SEAL = "execution_child_seal/v1"
ROOT_V2 = "collaboration_root_terminal/v2"
PROFILE = "execution-v1-normal-only"
CHILD_OBJECTS = frozenset({"execution_instance/v1", "execution_net_definition/v1",
    "execution_checkpoint/v1", "execution_token/v1", "execution_transition_firing/v1"})
# These exact v1 objects are written by ExecutionRuntime._prewrite as JSON
# descriptors. A claimed application/json media type is not a codec grant.
JSON_DESCRIPTOR_EVIDENCE_TYPES = frozenset({"execution_instance/v1", "execution_net_definition/v1",
    "execution_checkpoint/v1", "execution_token/v1", "execution_transition_firing/v1"})


def fail(message):
    raise RegistryConflict("normal child closure: " + message)


def exact_ref(value, kind=None):
    if (not isinstance(value, dict)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or (kind is not None and value["entity_type"] != kind)):
        fail("exact reference type differs")
    TypedId.parse(value["logical_id"])
    TypedId.parse(value["version_id"])
    return value


class ClosureSnapshot:
    """All lookups use this db; cut selects history, ownership checks stay exact."""

    def __init__(self, event_store, db, task_id, *, cut=None, provisional_parent=None):
        self.event_store, self.db, self.task_id = event_store, db, task_id
        self.store = descriptor_store(event_store)
        self.current_cut = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
        self.cut = self.current_cut if cut is None else cut
        if type(self.cut) is not int or not 0 <= self.cut <= self.current_cut:
            fail("historical cut is outside the current snapshot")
        self.provisional_parent = provisional_parent
        self.rows = {}

    def member(self, kind, identity, parent=None):
        if parent is None:
            return
        rows = self.db.execute("SELECT p.* FROM firing_temporary_members m JOIN firing_publications p "
            "ON p.firing_version_id=m.firing_version_id WHERE m.member_kind=? AND m.member_identity=?",
            (kind, identity)).fetchall()
        if (len(rows) != 1 or rows[0]["firing_version_id"] != parent["parent_business_firing_ref"]["version_id"]
                or rows[0]["invocation_version_id"] != parent["parent_invocation_ref"]["version_id"]
                or rows[0]["invocation_logical_id"] != parent["parent_invocation_ref"]["logical_id"]
                or rows[0]["net_version_id"] != parent["parent_business_net_ref"]["version_id"]):
            fail("member is outside its exact parent firing")
        expected = "PROVISIONAL" if self.provisional_parent is not None else "PUBLISHED"
        if rows[0]["state"] != expected:
            fail("member parent publication state differs")

    def event(self, row, *, parent=None):
        if row is None or row["task_id"] != str(self.task_id) or row["criticality"] != "authoritative":
            fail("fact envelope differs")
        if row["ordinal"] > self.cut:
            fail("fact is newer than the selected cut")
        tx = self.db.execute("SELECT status FROM transactions WHERE transaction_id=?", (row["transaction_id"],)).fetchone()
        ends = self.db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
            (row["transaction_id"],)).fetchall()
        if tx is None or tx[0] != "committed" or len(ends) != 1:
            fail("fact lacks its committed transaction")
        end = ends[0]
        if (end["task_id"] != str(self.task_id) or end["criticality"] != "authoritative"
                or end["stream_id"] != "transaction:" + row["transaction_id"]
                or end["aggregate_id"] != row["transaction_id"] or end["aggregate_type"] != "transaction"
                or end["payload_schema_ref"] != "registry_v1/transaction_committed/v1"
                or end["producer_principal"] != "framework" or end["producer_invocation_id"] is not None):
            fail("transaction terminal envelope differs")
        for kind, identity in (("event", row["event_id"]), ("event", end["event_id"]), ("transaction", row["transaction_id"])):
            self.member(kind, identity, parent)
        if (parent is None or self.provisional_parent is None) and not _canonical_closure(self.db, self.task_id,
                transaction_id=row["transaction_id"], members=(("event", row["event_id"]),)):
            fail("fact lacks canonical publication")
        payload = json.loads(row["payload_json"])
        self.event_store.catalog.validate_event_payload(row["event_type"], payload, criticality=row["criticality"])
        return payload

    def read(self, ref, kind=None, *, parent=None, descriptor=True):
        exact_ref(ref, kind)
        row = self.db.execute("SELECT * FROM objects WHERE object_type=? AND logical_id=? AND version_id=?",
            (ref["entity_type"], ref["logical_id"], ref["version_id"])).fetchone()
        if row is None:
            fail("referenced object is absent")
        event = self.db.execute("SELECT * FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
        payload = self.event(event, parent=parent)
        metadata = json.loads(row["metadata_json"])
        expected = {key: row[key] for key in ("object_type", "logical_id", "version_id", "size", "media_type", "schema_ref", "storage_locator")}
        expected["metadata"] = metadata
        if (event["event_type"] != "object_version_published/v1"
                or event["payload_schema_ref"] != "registry_v1/object_version_published/v1"
                or event["transaction_id"] != row["transaction_id"]
                or event["stream_id"] != "object:" + ref["logical_id"]
                or event["aggregate_id"] != ref["logical_id"] or event["aggregate_type"] != ref["entity_type"]
                or event["producer_principal"] != "framework"
                or event["producer_invocation_id"] != row["producer_invocation_id"]
                or payload != expected):
            fail("object publication differs from its exact bytes envelope")
        self.member("object", ref["version_id"], parent)
        if parent is not None and ref["entity_type"] in CHILD_OBJECTS | {"resource_version/v1"}:
            if row["producer_invocation_id"] != parent["parent_invocation_ref"]["logical_id"]:
                fail("child/evidence producer differs")
        prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
            row["size"], row["media_type"], row["schema_ref"],
            TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
            row["storage_locator"], metadata)
        self.event_store.catalog.validate_instance(row["object_type"], category="object", instance=metadata)
        result = readable_descriptor(self.store, prepared) if descriptor else self.store.read_registered(prepared)
        self.rows[ref["version_id"]] = row
        return result

    def source(self):
        row = read_source_binding(self.db, self.event_store.catalog, self.task_id)
        if row is None:
            fail("explicit local source binding is absent")
        return json.loads(row["binding_metadata_json"])


def parent_payload(parent):
    return {"parent_invocation_ref": _ref_payload(parent.invocation_ref),
        "parent_business_firing_ref": _ref_payload(parent.business_firing_ref),
        "parent_business_net_ref": _ref_payload(parent.business_net_ref),
        "parent_business_checkpoint_ref": _ref_payload(parent.business_checkpoint_ref)}


def _definition(data):
    return ExecutionNetDefinition(data["definition_key"], tuple(data["places"]),
        tuple(ExecutionTransition(**row) for row in data["transitions"]),
        tuple(ExecutionInputArc(**row) for row in data["input_arcs"]),
        tuple(ExecutionOutputArc(**row) for row in data["output_arcs"]), data["initial_place"],
        data["initial_tokens"], tuple(data["terminal_places"]))


def _published_ordinal(snapshot, ref):
    row = snapshot.rows[ref["version_id"]]
    event = snapshot.db.execute("SELECT ordinal FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
    return event[0]


def _schema_canonical_before(snapshot, schema_ref, evidence_ordinal):
    """Canonical schema visibility predates evidence, including every promotion."""
    row = snapshot.rows[schema_ref["version_id"]]
    pending_transactions = [row["transaction_id"]]
    pending_members = [("object", schema_ref["version_id"]), ("event", row["published_event_id"])]
    transactions, members = set(), set()
    while pending_transactions or pending_members:
        while pending_transactions:
            transaction_id = pending_transactions.pop()
            if transaction_id in transactions:
                continue
            terminals = snapshot.db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                (transaction_id,)).fetchall()
            if len(terminals) != 1 or terminals[0]["ordinal"] >= evidence_ordinal:
                fail("schema authority was not canonical before evidence registration")
            snapshot.event(terminals[0])
            transactions.add(transaction_id)
            pending_members.extend((("event", terminals[0]["event_id"]), ("transaction", transaction_id)))
        while pending_members:
            member = pending_members.pop()
            if member in members:
                continue
            members.add(member)
            roots = snapshot.db.execute("SELECT p.state,p.published_transaction_id FROM firing_temporary_members m "
                "LEFT JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind=? AND m.member_identity=?", member).fetchall()
            for root in roots:
                if root["state"] != "PUBLISHED" or root["published_transaction_id"] is None:
                    fail("schema authority has an unpublished firing owner")
                pending_transactions.append(root["published_transaction_id"])


def _evidence_schema(snapshot, authority, schema_id, *, evidence_ordinal, task_ref):
    """Read one exact, self-contained Draft7 source at this same snapshot/cut."""
    from .content_schemas import _validate_schema_bytes, ContentSchemaAuthorityError
    if not isinstance(authority, dict):
        fail("resource evidence requires exact content_schema_authority_ref")
    if set(authority) == {"resource_id", "resource_version_id"}:
        schema_ref = {"entity_type": "resource_version/v1", "logical_id": authority["resource_id"],
            "version_id": authority["resource_version_id"]}
        payload = snapshot.read(schema_ref, "resource_version/v1", descriptor=False)
        row = snapshot.rows[schema_ref["version_id"]]
        metadata = json.loads(row["metadata_json"])
        if (row["media_type"] != "application/schema+json" or metadata["media_type"] != row["media_type"]
                or metadata["size"] != len(payload) or metadata["task_ref"] != task_ref
                or metadata["resource_id"] != schema_ref["logical_id"]
                or metadata["resource_version_id"] != schema_ref["version_id"]):
            fail("resource evidence schema document header/source differs")
    else:
        schema_ref = exact_ref(authority, "registry_type_catalog/v1")
        catalog = snapshot.read(schema_ref, "registry_type_catalog/v1")
        entry = catalog.get("schemas", {}).get(schema_id)
        if not isinstance(entry, dict) or entry.get("schema_id") != schema_id or not isinstance(entry.get("source"), str):
            fail("resource evidence catalog lacks its exact schema source")
        payload = entry["source"].encode("utf-8")
    if _published_ordinal(snapshot, schema_ref) >= evidence_ordinal:
        fail("resource evidence schema authority was not registered before its payload")
    _schema_canonical_before(snapshot, schema_ref, evidence_ordinal)
    try:
        actual_id, document = _validate_schema_bytes(payload)
    except ContentSchemaAuthorityError as exc:
        fail("resource evidence needs a supported self-contained Draft7 schema validator: " + str(exc))
    if actual_id != schema_id:
        fail("resource evidence schema authority resolves another schema ID")
    return document


def validate_evidence(snapshot, parent, reference):
    """Known JSON descriptors and schema-backed JSON/text resources only.

    The new opt-in adapter never changes ExecutionRuntime.settle's v1 contract.
    Opaque nonresource types require their own validator before being supported.
    """
    exact_ref(reference)
    row = snapshot.db.execute("SELECT object_type,media_type FROM objects WHERE object_type=? AND logical_id=? AND version_id=?",
        (reference["entity_type"], reference["logical_id"], reference["version_id"])).fetchone()
    if row is None:
        fail("evidence object is absent")
    if reference["entity_type"] != "resource_version/v1":
        if reference["entity_type"] not in JSON_DESCRIPTOR_EVIDENCE_TYPES or row["media_type"] != "application/json":
            fail("unsupported evidence codec for " + reference["entity_type"] + "; an exact typed payload validator is required")
        snapshot.read(reference, parent=parent, descriptor=True)
        if snapshot.rows[reference["version_id"]]["producer_invocation_id"] != parent["parent_invocation_ref"]["logical_id"]:
            fail("JSON descriptor evidence producer differs")
        return
    from jsonschema import Draft7Validator
    payload = snapshot.read(reference, "resource_version/v1", parent=parent, descriptor=False)
    row = snapshot.rows[reference["version_id"]]
    metadata = json.loads(row["metadata_json"])
    invocation = snapshot.read(parent["parent_invocation_ref"], "invocation/v1", parent=parent)
    source = snapshot.source()
    if (metadata["resource_id"] != reference["logical_id"] or metadata["resource_version_id"] != reference["version_id"]
            or metadata["producer_ref"] != parent["parent_invocation_ref"] or metadata["task_ref"] != source["task_ref"]
            or metadata["task_ref"] != invocation["task_ref"] or metadata["round_ref"] != invocation["task_round_ref"]
            or metadata["net_ref"] != parent["parent_business_net_ref"]
            or metadata["size"] != len(payload) or metadata["media_type"] != row["media_type"]):
        fail("resource evidence self/producer/task/round/net/byte header differs")
    schema_id = metadata["content_schema_ref"]
    if not isinstance(schema_id, str) or not schema_id:
        fail("resource evidence needs a declared content schema and exact authority")
    document = _evidence_schema(snapshot, metadata["content_schema_authority_ref"], schema_id,
        evidence_ordinal=_published_ordinal(snapshot, reference), task_ref=metadata["task_ref"])
    media_type = metadata["media_type"]
    try:
        if media_type == "application/json":
            instance = json.loads(payload)
        elif media_type.startswith("text/"):
            instance = payload.decode("utf-8")
        else:
            fail("unsupported resource evidence codec " + media_type + "; a typed content validator is required")
        Draft7Validator(document).validate(instance)
    except (ValueError, UnicodeError) as exc:
        fail("resource evidence content cannot be decoded by its declared codec: " + str(exc))
    except Exception as exc:
        if isinstance(exc, RegistryConflict):
            raise
        fail("resource evidence bytes violate the exact registered content schema: " + str(exc))


def child_snapshot(snapshot, parent):
    """Enumerate the union of producer, firing and attach inventories, never filter damage."""
    invocation = snapshot.read(parent["parent_invocation_ref"], "invocation/v1", parent=parent)
    firing = snapshot.read(parent["parent_business_firing_ref"], "transition_firing/v1", parent=parent)
    for name, kind in (("parent_business_net_ref", "net_instance/v1"), ("parent_business_checkpoint_ref", "marking_checkpoint/v1")):
        snapshot.read(parent[name], kind)
    if (invocation["invocation_ref"] != parent["parent_invocation_ref"]
            or invocation["own_transition_firing_ref"] != parent["parent_business_firing_ref"]
            or invocation["net_instance_ref"] != parent["parent_business_net_ref"]
            or invocation["admission_marking_checkpoint_ref"] != parent["parent_business_checkpoint_ref"]
            or firing["transition_firing_ref"] != parent["parent_business_firing_ref"]
            or firing["net_instance_ref"] != parent["parent_business_net_ref"]
            or firing["admission_marking_checkpoint_ref"] != parent["parent_business_checkpoint_ref"]):
        fail("parent lineage differs")
    inv = parent["parent_invocation_ref"]["logical_id"]
    firing_ref = parent["parent_business_firing_ref"]
    stream = "execution-children:" + firing_ref["version_id"]
    attaches = snapshot.db.execute("SELECT * FROM events WHERE event_type='execution_instance_attached/v1' AND ordinal<=? "
        "AND (stream_id=? OR aggregate_id=? OR producer_invocation_id=?) ORDER BY ordinal",
        (snapshot.cut, stream, firing_ref["logical_id"], inv)).fetchall()
    attached = {}
    for event in attaches:
        body = snapshot.event(event, parent=parent)
        if (event["stream_id"] != stream or event["aggregate_id"] != firing_ref["logical_id"]
                or event["aggregate_type"] != "execution_child_set" or event["producer_invocation_id"] != inv
                or event["payload_schema_ref"] != "registry_v1/execution_instance_attached/v1"
                or body["parent_invocation_ref"] != parent["parent_invocation_ref"]
                or body["parent_business_firing_ref"] != firing_ref):
            fail("attach scope differs")
        key = canonical_text(exact_ref(body["execution_instance_ref"], "execution_instance/v1"))
        if key in attached:
            fail("instance has repeated attach facts")
        attached[key] = (body, event)
    rows = snapshot.db.execute("SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='execution_instance/v1' AND e.ordinal<=? AND (o.producer_invocation_id=? OR "
        "json_extract(o.metadata_json,'$.parent_business_firing_ref.version_id')=?)",
        (snapshot.cut, inv, firing_ref["version_id"])).fetchall()
    instances = {canonical_text({"entity_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"]}): row for row in rows}
    if (len(instances) != len(rows) or set(instances) != set(attached)
            or len({row["logical_id"] for row in rows}) != len(rows)):
        fail("complete child inventory and attach inventory differ")
    head = snapshot.db.execute("SELECT COALESCE(MAX(stream_sequence),0) FROM events WHERE stream_id=? AND ordinal<=?",
        (stream, snapshot.cut)).fetchone()[0]
    if head != len(attaches) or [event["stream_sequence"] for event in attaches] != list(range(1, head + 1)):
        fail("child set has already been sealed or has unexpected stream facts")
    entries = []
    for key in sorted(instances, key=lambda value: json.loads(value)["version_id"]):
        ref = json.loads(key)
        instance = snapshot.read(ref, "execution_instance/v1", parent=parent)
        attach, attach_event = attached[key]
        if (instance["execution_instance_ref"] != ref or any(instance[name] != value for name, value in parent.items())
                or instance["initial_checkpoint_ref"] != attach["initial_checkpoint_ref"]
                or snapshot.rows[ref["version_id"]]["transaction_id"] != attach_event["transaction_id"]):
            fail("instance and attach lineage differ")
        definition_ref = instance["execution_net_definition_ref"]
        definition_data = snapshot.read(definition_ref, "execution_net_definition/v1", parent=parent)
        if definition_data["execution_net_definition_ref"] != definition_ref:
            fail("definition self reference differs")
        definition = _definition(definition_data)
        initial_ref = instance["initial_checkpoint_ref"]
        checkpoints = snapshot.db.execute("SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.logical_id=? AND e.ordinal<=? ORDER BY e.ordinal", (initial_ref["logical_id"], snapshot.cut)).fetchall()
        all_checkpoints = snapshot.db.execute("SELECT o.version_id FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type='execution_checkpoint/v1' AND e.ordinal<=? "
            "AND json_extract(o.metadata_json,'$.execution_instance_ref.version_id')=?",
            (snapshot.cut, ref["version_id"])).fetchall()
        if {r["version_id"] for r in checkpoints} != {r["version_id"] for r in all_checkpoints}:
            fail("child has checkpoints outside its original checkpoint lineage")
        previous = None
        for row in checkpoints:
            cp_ref = {"entity_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"]}
            cp = snapshot.read(cp_ref, "execution_checkpoint/v1", parent=parent)
            if (cp["execution_checkpoint_ref"] != cp_ref or cp["execution_instance_ref"] != ref
                    or cp["execution_net_definition_ref"] != definition_ref
                    or cp["sequence"] != (0 if previous is None else previous["sequence"] + 1)
                    or cp["previous_checkpoint_ref"] != (None if previous is None else previous["execution_checkpoint_ref"])):
                fail("checkpoint sequence/lineage differs")
            tokens = []
            for token_ref in cp["token_refs"]:
                token = snapshot.read(token_ref, "execution_token/v1", parent=parent)
                if (token["execution_token_ref"] != token_ref or token["execution_instance_ref"] != ref
                        or token["place"] not in definition.places or token["ordinal"] >= cp["next_token_ordinal"]):
                    fail("checkpoint token differs")
                tokens.append(token)
            if len({token["ordinal"] for token in tokens}) != len(tokens):
                fail("checkpoint repeats token ordinals")
            ready = not cp["active_firing_refs"] and bool(tokens) and all(t["place"] in definition.terminal_places for t in tokens)
            if (cp["status"] == "map_ready") != ready:
                fail("checkpoint normal terminal classification differs")
            for active_ref in cp["active_firing_refs"]:
                active = snapshot.read(active_ref, "execution_transition_firing/v1", parent=parent)
                if (active["execution_transition_firing_ref"] != active_ref or active["status"] != "active"
                        or active["execution_instance_ref"] != ref or active["execution_net_definition_ref"] != definition_ref):
                    fail("active execution firing differs from checkpoint")
            if previous is None:
                if (cp_ref != initial_ref or cp["transition_firing_ref"] is not None or cp["active_firing_refs"]
                        or cp["evidence_refs"] or len(tokens) != definition.initial_tokens
                        or cp["next_token_ordinal"] != definition.initial_tokens
                        or sorted(t["ordinal"] for t in tokens) != list(range(definition.initial_tokens))
                        or row["transaction_id"] != attach_event["transaction_id"]
                        or snapshot.rows[definition_ref["version_id"]]["transaction_id"] != attach_event["transaction_id"]
                        or any(snapshot.rows[t["execution_token_ref"]["version_id"]]["transaction_id"] != attach_event["transaction_id"]
                               or t["place"] != definition.initial_place or t["produced_by_firing_ref"] is not None for t in tokens)):
                    fail("initial checkpoint differs from its definition")
            else:
                transition_ref = cp["transition_firing_ref"]
                transition = snapshot.read(transition_ref, "execution_transition_firing/v1", parent=parent)
                declared = definition.transition(transition["transition_id"])
                if (transition["execution_transition_firing_ref"] != transition_ref or transition["execution_instance_ref"] != ref
                        or transition["execution_net_definition_ref"] != definition_ref or transition["recovery_mode"] != declared.recovery_mode
                        or (declared.recovery_mode == "pure" and transition["materialization_key"] is not None)
                        or (declared.recovery_mode == "idempotent_materialization" and not transition["materialization_key"])
                        or snapshot.rows[transition_ref["version_id"]]["transaction_id"] != row["transaction_id"]):
                    fail("checkpoint transition scope differs")
                old_tokens = {canonical_text(t): t for t in previous["token_refs"]}
                new_tokens = {canonical_text(t): t for t in cp["token_refs"]}
                old_active = {canonical_text(t) for t in previous["active_firing_refs"]}
                new_active = {canonical_text(t) for t in cp["active_firing_refs"]}
                if transition["status"] == "active":
                    consumed = {canonical_text(t) for t in transition["input_token_refs"]}
                    if (transition["predecessor_checkpoint_ref"] != previous["execution_checkpoint_ref"]
                            or not consumed <= set(old_tokens) or set(new_tokens) != set(old_tokens) - consumed
                            or new_active != old_active | {canonical_text(transition_ref)}
                            or cp["evidence_refs"] != previous["evidence_refs"]
                            or cp["next_token_ordinal"] != previous["next_token_ordinal"]):
                        fail("execution Start does not consume its exact predecessor")
                    consumed_tokens = [snapshot.read(t, "execution_token/v1", parent=parent) for t in transition["input_token_refs"]]
                    if Counter(t["place"] for t in consumed_tokens) != Counter({a.place: a.weight for a in definition.inputs_for(declared.transition_id)}):
                        fail("execution Start input arcs differ")
                elif transition["status"] == "settled":
                    active_ref = transition["active_firing_ref"]
                    active = snapshot.read(active_ref, "execution_transition_firing/v1", parent=parent)
                    produced_refs = transition["output_token_refs"]
                    produced = [snapshot.read(t, "execution_token/v1", parent=parent) for t in produced_refs]
                    if (canonical_text(active_ref) not in old_active or active["status"] != "active"
                            or any(active[field] != transition[field] for field in ("execution_instance_ref", "execution_net_definition_ref",
                                "predecessor_checkpoint_ref", "transition_id", "recovery_mode", "materialization_key", "input_token_refs"))
                            or transition["settled_checkpoint_ref"] != cp_ref
                            or new_active != old_active - {canonical_text(active_ref)}
                            or set(new_tokens) != set(old_tokens) | {canonical_text(t) for t in produced_refs}
                            or not set(map(canonical_text, previous["evidence_refs"])) <= set(map(canonical_text, cp["evidence_refs"]))
                            or any(t["produced_by_firing_ref"] != transition_ref
                                or snapshot.rows[t["execution_token_ref"]["version_id"]]["transaction_id"] != row["transaction_id"] for t in produced)
                            or Counter(t["place"] for t in produced) != Counter({a.place: a.weight for a in definition.outputs_for(declared.transition_id)})
                            or [t["ordinal"] for t in produced] != list(range(previous["next_token_ordinal"], cp["next_token_ordinal"]))):
                        fail("execution settlement does not follow its active firing")
                else:
                    fail("unsupported execution outcome")
            previous = cp
        if previous is None or previous["status"] != "map_ready" or not previous["evidence_refs"]:
            fail("every child requires map_ready and nonempty evidence")
        for evidence in previous["evidence_refs"]:
            validate_evidence(snapshot, parent, evidence)
            evidence_event = snapshot.db.execute("SELECT ordinal FROM events WHERE event_id=?",
                (snapshot.rows[evidence["version_id"]]["published_event_id"],)).fetchone()
            checkpoint_event = snapshot.db.execute("SELECT ordinal FROM events WHERE event_id=?",
                (snapshot.rows[previous["execution_checkpoint_ref"]["version_id"]]["published_event_id"],)).fetchone()
            if evidence_event[0] >= checkpoint_event[0]:
                fail("evidence was not registered before child settlement")
        entries.append({"execution_instance_ref": ref, "execution_net_definition_ref": definition_ref,
            "execution_checkpoint_ref": previous["execution_checkpoint_ref"], "evidence_refs": previous["evidence_refs"]})
    source = snapshot.source()
    return {"source_id": source["source_id"], "task_ref": source["task_ref"], "run_ref": source["native_run_ref"],
        **parent, "child_stream_id": stream, "pre_seal_stream_head": head, "children": entries}


def capture_child_snapshot(core, parent):
    data = parent_payload(parent)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return child_snapshot(ClosureSnapshot(core.event_store, db, core.task_id, provisional_parent=data), data)


def child_seal_ref(task_id, command_id):
    return VersionRef(SEAL, _stable_id("resource", "normal-child-seal", task_id, command_id),
        _stable_id("resource_version", "normal-child-seal", task_id, command_id))


def stage_child_seal(core, tx, snapshot, mapping_refs, *, result_ref, checkpoint_ref, command_id):
    ref = child_seal_ref(core.task_id, command_id)
    data = {"execution_child_seal_ref": _ref_payload(ref), "closure_profile": PROFILE, **snapshot,
        "children": [dict(child, execution_terminal_mapping_ref=_ref_payload(mapping))
                     for child, mapping in zip(snapshot["children"], mapping_refs, strict=True)],
        "operation_result_ref": _ref_payload(result_ref), "successor_business_checkpoint_ref": _ref_payload(checkpoint_ref),
        "success_command_id": command_id, "success_transaction_id": str(tx.transaction_id)}
    core.catalog.validate_instance(SEAL, category="object", instance=data)
    tx.prewrite(object_type=SEAL, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(data), metadata=data, media_type="application/json", schema_ref="registry_v1/" + SEAL,
        producer_invocation_id=TypedId.parse(snapshot["parent_invocation_ref"]["logical_id"]))
    return ref


def validate_child_seal_publication(context):
    seals = [obj for obj in context.objects if obj.object_type == SEAL]
    roots = [obj for obj in context.objects if obj.object_type == ROOT_V2]
    if not seals and not roots:
        return
    if len(seals) != 1 or len(roots) != 1:
        fail("one seal and one root/v2 must share the original Success")
    if any(obj.object_type in CHILD_OBJECTS for obj in context.objects) or any(
            event.event_type == "execution_instance_attached/v1" for event in context.events):
        fail("final Success cannot attach or progress child lifecycle objects")
    store = descriptor_store(context.event_store)
    seal = readable_descriptor(store, seals[0])
    root = readable_descriptor(store, roots[0])
    body = root["body"]
    from ..collaboration.worksets import qualified, record_ref
    parent = {key: seal[key] for key in ("parent_invocation_ref", "parent_business_firing_ref", "parent_business_net_ref", "parent_business_checkpoint_ref")}
    snapshot = ClosureSnapshot(context.event_store, context.db, context.task_id, provisional_parent=parent)
    expected = child_snapshot(snapshot, parent)
    if (seal["closure_profile"] != PROFILE or body["closure_profile"] != PROFILE
            or seal["execution_child_seal_ref"] != _ref_payload(child_seal_ref(context.task_id, context.idempotency_key))
            or root["record_ref"] != qualified(expected["source_id"],
                record_ref(ROOT_V2, context.task_id, context.idempotency_key, context.idempotency_key))
            or body["required_child_seal_ref"]["source_id"] != expected["source_id"]
            or body["required_child_seal_ref"]["ref"] != seal["execution_child_seal_ref"]
            or seal["execution_child_seal_ref"] != {"entity_type": SEAL, "logical_id": str(seals[0].logical_id), "version_id": str(seals[0].version_id)}
            or body["firing_ref"] != parent["parent_business_firing_ref"] or body["invocation_ref"] != parent["parent_invocation_ref"]
            or seal["success_command_id"] != context.idempotency_key or root["command_id"] != context.idempotency_key
            or seal["success_transaction_id"] != str(context.transaction_id)
            or seals[0].producer_invocation_id != roots[0].producer_invocation_id
            or str(seals[0].producer_invocation_id) != parent["parent_invocation_ref"]["logical_id"]
            or any(seal[key] != value for key, value in expected.items() if key != "children")
            or [{key: child[key] for key in expected_child} for child, expected_child in zip(seal["children"], expected["children"])] != expected["children"]
            or len(seal["children"]) != len(expected["children"])):
        fail("seal identity, exhaustive child snapshot or root differs")
    completions = [obj for obj in context.objects if obj.object_type == "firing_completion/v2"
        and obj.metadata.get("transition_firing_ref") == body["firing_ref"]]
    if (len(completions) != 1 or completions[0].metadata["operation_result_ref"] != seal["operation_result_ref"]
            or completions[0].metadata["successor_checkpoint_ref"] != seal["successor_business_checkpoint_ref"]
            or body["checkpoint_ref"] != seal["successor_business_checkpoint_ref"]):
        fail("seal differs from original Success completion")
    for reference in (seal["operation_result_ref"], seal["successor_business_checkpoint_ref"]):
        if not any(obj.object_type == reference["entity_type"] and str(obj.logical_id) == reference["logical_id"]
                and str(obj.version_id) == reference["version_id"] for obj in context.objects):
            fail("seal result/checkpoint must be staged in this same Success")
    for obj in context.objects:
        if obj.object_type == "execution_terminal_mapping/v1":
            readable_descriptor(store, obj)
    validate_seal_mappings(seal, context.objects, context.events)


def validate_seal_mappings(seal, objects, events):
    mappings = [obj for obj in objects if obj.object_type == "execution_terminal_mapping/v1"]
    seals = [event for event in events if event.event_type == "execution_children_sealed/v1"]
    if len(seals) != 1 or len(mappings) != len(seal["children"]):
        fail("seal event or mapping cardinality differs")
    event = seals[0]
    expected_event = {key: seal[key] for key in ("parent_invocation_ref", "parent_business_firing_ref")}
    for name, field in (("execution_instance_refs", "execution_instance_ref"),
                        ("execution_checkpoint_refs", "execution_checkpoint_ref"),
                        ("execution_terminal_mapping_refs", "execution_terminal_mapping_ref")):
        expected_event[name] = [child[field] for child in seal["children"]]
    if (dict(event.payload) != expected_event or event.stream_id != seal["child_stream_id"]
            or event.aggregate_id != seal["parent_business_firing_ref"]["logical_id"]
            or event.aggregate_type != "execution_child_set" or event.idempotency_key != seal["success_command_id"]
            or event.command_id != seal["success_command_id"] or event.criticality != "authoritative"
            or event.payload_schema_ref != "registry_v1/execution_children_sealed/v1"
            or str(event.producer_invocation_id) != seal["parent_invocation_ref"]["logical_id"]):
        fail("original child seal event differs")
    by_ref = {canonical_text({"entity_type": obj.object_type, "logical_id": str(obj.logical_id), "version_id": str(obj.version_id)}): obj for obj in mappings}
    for child in seal["children"]:
        obj = by_ref.pop(canonical_text(child["execution_terminal_mapping_ref"]), None)
        if obj is None or str(obj.producer_invocation_id) != seal["parent_invocation_ref"]["logical_id"]:
            fail("mapping identity or producer differs")
        data = dict(obj.metadata)
        expected = {key: child[key] for key in ("execution_terminal_mapping_ref", "execution_instance_ref", "execution_checkpoint_ref", "evidence_refs")}
        expected.update({key: seal[key] for key in ("parent_invocation_ref", "parent_business_firing_ref", "operation_result_ref")})
        expected.update(successor_business_checkpoint_ref=seal["successor_business_checkpoint_ref"], business_outcome="completed")
        if any(data[key] != value for key, value in expected.items()) or data["workspace_revision_ref"] is not None:
            fail("mapping does not match its exact child/Success")
    if by_ref:
        fail("extra mapping outside required child set")
