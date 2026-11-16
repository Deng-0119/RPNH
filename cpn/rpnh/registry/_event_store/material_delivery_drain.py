"""Current material policy and conservative drain proofs in one original DB cut.

The release witness remains the once-only I/O linearization point.  This module
never revokes a committed witness and never interprets expiry, failure, writer
replacement, or a local adapter acknowledgement as external transport closure.
No derived queue, delivery table, or persistent availability cache is created.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict, RegistryCorruptError
from ..identities import TypedId
from .material_control import _committed_descriptor, reference, row_ref


_DELIVERY = "resource_delivery/v1"
_WITNESS = "resource_release_witness/v1"


def validate_material_delivery_event(store, db, task_id, event_type, payload):
    """Final gate for a NEW prepare or witness, inside original BEGIN IMMEDIATE.

    Existing witnesses are intentionally absent from this gate.  An acknowledged
    read or a late boundary receipt is evidence about already approved I/O, not
    a new request to use its body.
    """
    if event_type not in {"resource_delivery_prepared/v1", "resource_release_authorized/v1"}:
        return
    resource = payload["resource_ref"]
    exact = {"entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
             "version_id": resource["resource_version_id"]}
    from ...material_body_policy import resolve_body_availability
    result = resolve_body_availability(store, db, task_id, exact)
    if result["availability"] != "available":
        raise RegistryConflict("MATERIAL_UNAVAILABLE: new delivery requires current available body")


class _Cut:
    """Read original committed descriptors, including unresolved firing roots."""

    def __init__(self, store, db, task_id):
        self.store, self.db, self.task_id = store, db, str(task_id)
        self.task_identity = TypedId.parse(str(task_id), expected="task")
        self.transactions = set()
        self.objects = {}

    def event(self, row):
        if row is None or row["task_id"] != self.task_id or row["criticality"] != "authoritative":
            raise RegistryConflict("DELIVERY_UNKNOWN: original delivery event is missing or cross-source")
        if row["transaction_id"] not in self.transactions:
            transaction = self.db.execute("SELECT status FROM transactions WHERE transaction_id=?",
                (row["transaction_id"],)).fetchone()
            if transaction is None or transaction["status"] != "committed":
                raise RegistryConflict("DELIVERY_UNKNOWN: delivery transaction is not committed")
            try:
                self.store._verified_persisted_event_record(self.db, row)
            except RegistryCorruptError as exc:
                raise RegistryConflict("DELIVERY_UNKNOWN: original delivery transaction is incomplete") from exc
            self.transactions.add(row["transaction_id"])
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise RegistryConflict("DELIVERY_UNKNOWN: malformed original delivery event") from exc
        if not isinstance(payload, dict):
            raise RegistryConflict("DELIVERY_UNKNOWN: malformed original delivery event")
        self.store.catalog.validate_schema_ref(row["payload_schema_ref"], payload)
        return payload

    def obj(self, ref):
        reference(ref, ref["entity_type"])
        key = ref["version_id"]
        if key in self.objects:
            row, data = self.objects[key]
            if row_ref(row) != ref:
                raise RegistryConflict("DELIVERY_UNKNOWN: original delivery object identity differs")
            return row, data
        row = self.db.execute("SELECT * FROM objects WHERE version_id=?", (key,)).fetchone()
        if row is None or row_ref(row) != ref:
            raise RegistryConflict("DELIVERY_UNKNOWN: original delivery object is missing")
        publication = self.db.execute("SELECT * FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
        payload = self.event(publication)
        data = _committed_descriptor(self.store, row)
        expected = {name: row[name] for name in ("object_type", "logical_id", "version_id", "size",
                                                "media_type", "schema_ref", "storage_locator")}
        expected["metadata"] = data
        if (publication["event_type"] != "object_version_published/v1"
                or publication["stream_id"] != "object:" + row["logical_id"]
                or publication["aggregate_id"] != row["logical_id"]
                or publication["aggregate_type"] != row["object_type"]
                or publication["transaction_id"] != row["transaction_id"]
                or publication["producer_invocation_id"] != row["producer_invocation_id"]
                or payload != expected):
            raise RegistryConflict("DELIVERY_UNKNOWN: original object/publication pairing differs")
        self.objects[key] = row, data
        return row, data

    def owner(self, row):
        """Original object, publication and transaction share exactly one root."""
        owners = []
        for kind, identity in (("object", row["version_id"]), ("event", row["published_event_id"]),
                               ("transaction", row["transaction_id"])):
            values = self.db.execute("SELECT firing_version_id,transaction_id FROM firing_temporary_members "
                "WHERE member_kind=? AND member_identity=?", (kind, identity)).fetchall()
            if len(values) != 1 or values[0]["transaction_id"] != row["transaction_id"]:
                raise RegistryConflict("DELIVERY_UNKNOWN: delivery firing membership is incomplete")
            owners.append(values[0][0])
        if len(set(owners)) != 1:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery firing memberships disagree")
        root = self.db.execute("SELECT state FROM firing_publications WHERE firing_version_id=?", (owners[0],)).fetchone()
        if root is None or root["state"] not in {"PROVISIONAL", "PUBLISHED", "SUPERSEDED", "REJECTED"}:
            raise RegistryConflict("DELIVERY_UNKNOWN: original delivery firing root is missing")
        return owners[0]


def _resource_key(value):
    if not isinstance(value, dict) or set(value) != {"resource_id", "resource_version_id"}:
        raise RegistryConflict("DELIVERY_UNKNOWN: delivery has no exact resource identity")
    return value["resource_id"], value["resource_version_id"]


def _discover(cut, targets):
    """Use original objects AND event facts; damaged index entries cannot hide I/O."""
    delivery_ids = set()
    target_versions = {version for _logical, version in targets}

    def match(data):
        value = data.get("resource_ref")
        if value is None:
            return False
        key = _resource_key(value)
        if key[1] in target_versions and key not in targets:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery target version has another resource identity")
        return key in targets

    def observe(kind, ref, data):
        if kind not in {_DELIVERY, _WITNESS} or not match(data):
            return
        row, actual = cut.obj(ref)
        if actual != data:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery index and original publication disagree")
        if kind == _DELIVERY:
            if data["delivery_id"] != ref["logical_id"]:
                raise RegistryConflict("DELIVERY_UNKNOWN: delivery identity differs")
            delivery_ids.add(data["delivery_id"])
        else:
            delivery_ids.add(data["delivery_ref"]["logical_id"])

    rows = cut.db.execute("SELECT * FROM objects WHERE object_type IN (?,?)", (_DELIVERY, _WITNESS)).fetchall()
    for row in rows:
        try:
            data = json.loads(row["metadata_json"])
        except (TypeError, ValueError) as exc:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery index is malformed") from exc
        if not isinstance(data, dict):
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery index is malformed")
        observe(row["object_type"], row_ref(row), data)
    # An omitted object or changed type in the physical projection still has
    # its original publication/event.  These are all original facts, including
    # PROVISIONAL and REJECTED members; canonical filtering would be unsafe.
    for row in cut.db.execute("SELECT * FROM events WHERE event_type='object_version_published/v1' ORDER BY ordinal"):
        value = json.loads(row["payload_json"])
        if value.get("object_type") in {_DELIVERY, _WITNESS} and match(value["metadata"]):
            cut.event(row)
            observe(value["object_type"], {"entity_type": value["object_type"], "logical_id": value["logical_id"],
                     "version_id": value["version_id"]}, value["metadata"])
    for row in cut.db.execute("SELECT * FROM events WHERE event_type IN "
            "('resource_delivery_prepared/v1','resource_release_authorized/v1','observed_read/v1') ORDER BY ordinal"):
        value = json.loads(row["payload_json"])
        if match(value):
            cut.event(row)
            delivery_ids.add(value.get("delivery_id", value.get("delivery_ref", {}).get("logical_id")))
    if None in delivery_ids:
        raise RegistryConflict("DELIVERY_UNKNOWN: delivery event has no exact identity")
    return sorted(delivery_ids)


def _delivery_chain(cut, delivery_id, targets):
    """Verify the original exact delivery independently of business terminal.

    This shared proof lets the registered human read completion validate its
    actual transport before Success. Drain separately requires the consumer's
    terminal, so pre-Success receipt validation never self-exempts active use.
    """
    rows = cut.db.execute("SELECT * FROM objects WHERE object_type=? AND logical_id=?", (_DELIVERY, delivery_id)).fetchall()
    stages = {}
    owner = None
    for row in rows:
        row, data = cut.obj(row_ref(row))
        if data["state"] in stages or _resource_key(data["resource_ref"]) not in targets:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery stages repeat or change exact target")
        if data["delivery_id"] != delivery_id or data["delivery_version_id"] != row["version_id"]:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery stage identity differs")
        root = cut.owner(row)
        if owner is not None and root != owner:
            raise RegistryConflict("DELIVERY_UNKNOWN: delivery stages cross firing roots")
        owner = root
        stages[data["state"]] = (row, data)
    events = cut.db.execute("SELECT * FROM events WHERE aggregate_id=? AND event_type IN "
        "('resource_delivery_prepared/v1','resource_release_authorized/v1','resource_delivery_acknowledged/v1',"
        "'resource_delivery_failed/v1','resource_delivery_unknown/v1','observed_read/v1') ORDER BY ordinal",
        (delivery_id,)).fetchall()
    facts = {}
    for row in events:
        value = cut.event(row)
        members = cut.db.execute("SELECT firing_version_id,transaction_id FROM firing_temporary_members "
            "WHERE member_kind='event' AND member_identity=?", (row["event_id"],)).fetchall()
        if (len(members) != 1 or members[0]["firing_version_id"] != owner
                or members[0]["transaction_id"] != row["transaction_id"]):
            raise RegistryConflict("DELIVERY_UNKNOWN: original delivery event membership differs")
        if row["event_type"] in facts or row["stream_id"] not in {
                "resource-delivery:" + delivery_id,
                "observed-read:" + str(row["producer_invocation_id"])}:
            raise RegistryConflict("DELIVERY_UNKNOWN: original delivery events repeat or disagree")
        facts[row["event_type"]] = (row, value)
    if "prepared" not in stages or "resource_delivery_prepared/v1" not in facts:
        raise RegistryConflict("DELIVERY_UNKNOWN: delivery preparation is missing")
    prepared, p = stages["prepared"]
    if any(row["producer_invocation_id"] != p["context_ref"]["logical_id"] for row in (*rows, *events)):
        raise RegistryConflict("DELIVERY_UNKNOWN: original event producer differs from delivery context")
    prepared_event, pe = facts["resource_delivery_prepared/v1"]
    expected_prepare = {name: p[name] for name in ("resource_ref", "resource_byte_count", "authorization_ref", "context_ref", "boundary")}
    expected_prepare["delivery_ref"] = row_ref(prepared)
    if (pe != expected_prepare or prepared_event["transaction_id"] != prepared["transaction_id"]
            or any(p[name] is not None for name in ("previous_delivery_ref", "witness_ref", "boundary_receipt_ref"))):
        raise RegistryConflict("DELIVERY_UNKNOWN: delivery preparation lacks original exact event")
    witnesses = cut.db.execute("SELECT * FROM objects WHERE object_type=? AND "
        "json_extract(metadata_json,'$.delivery_ref.logical_id')=?", (_WITNESS, delivery_id)).fetchall()
    if set(stages) == {"prepared"} and set(facts) == {"resource_delivery_prepared/v1"} and not witnesses:
        # Preparation did not authorize I/O. The final new-witness gate is
        # serialized with disable; old handles cannot acquire a later witness.
        return {"prepared_only": True, "proof_refs": (row_ref(prepared),)}
    if (set(stages) != {"prepared", "release_authorized", "acknowledged"}
            or set(facts) != {"resource_delivery_prepared/v1", "resource_release_authorized/v1",
                              "resource_delivery_acknowledged/v1", "observed_read/v1"}
            or len(witnesses) != 1):
        raise RegistryConflict("TARGET_IN_USE: original delivery has no proven completed boundary")
    if p["boundary"] not in {"petri_input", "parent_receipt", "human_viewer"}:
        raise RegistryConflict("TARGET_IN_USE: local receipt does not prove downstream transport finished")
    released, r = stages["release_authorized"]
    terminal, t = stages["acknowledged"]
    witness, w = cut.obj(r["witness_ref"])
    boundary, b = cut.obj(t["boundary_receipt_ref"])
    if (row_ref(witness) != row_ref(witnesses[0])
            or w["witness_id"] != witness["logical_id"] or w["witness_version_id"] != witness["version_id"]
            or b["receipt_id"] != boundary["logical_id"] or b["receipt_version_id"] != boundary["version_id"]
            or any(row["producer_invocation_id"] != p["context_ref"]["logical_id"] for row in (witness, boundary))
            or any(cut.owner(row) != owner for row in (witness, boundary))
            or any(data[name] != p[name] for data in (r, t)
                for name in ("resource_ref", "resource_byte_count", "authorization_ref", "context_ref", "accounting_parent_ref", "boundary", "purpose"))
            or r["previous_delivery_ref"] != row_ref(prepared)
            or r["boundary_receipt_ref"] is not None
            or t["previous_delivery_ref"] != row_ref(released)
            or w["delivery_ref"] != row_ref(prepared)
            or t["witness_ref"] != row_ref(witness)
            or any(w[name] != p[name] for name in ("resource_ref", "resource_byte_count", "authorization_ref", "context_ref", "accounting_parent_ref", "boundary"))
            or b["delivery_ref"] != row_ref(released)
            or b["witness_ref"] != row_ref(witness)
            or b["boundary"] != p["boundary"] or b["outcome"] != "acknowledged"
            or isinstance(b["positive_byte_count"], bool)
            or b["positive_byte_count"] != p["resource_byte_count"]
            or b["resource_byte_count"] != p["resource_byte_count"]):
        raise RegistryConflict("DELIVERY_UNKNOWN: original acknowledged delivery chain differs")
    release_event, re = facts["resource_release_authorized/v1"]
    terminal_event, te = facts["resource_delivery_acknowledged/v1"]
    observed_event, oe = facts["observed_read/v1"]
    if (re != {"delivery_ref": row_ref(prepared), "witness_ref": row_ref(witness),
                "resource_ref": p["resource_ref"], "resource_byte_count": p["resource_byte_count"],
                "boundary": p["boundary"], "expires_at": w["expires_at"]}
            or te != {"delivery_ref": row_ref(released), "terminal_delivery_ref": row_ref(terminal),
                       "witness_ref": row_ref(witness), "boundary_receipt_ref": row_ref(boundary),
                       "outcome": "acknowledged"}
            or release_event["transaction_id"] != released["transaction_id"]
            or witness["transaction_id"] != released["transaction_id"]
            or terminal_event["transaction_id"] != terminal["transaction_id"]
            or observed_event["transaction_id"] != terminal["transaction_id"]
            or oe.get("transaction_id") != terminal["transaction_id"]
            or oe.get("resource_ref") != p["resource_ref"]
            or oe.get("resource_byte_count") != p["resource_byte_count"]
            or oe.get("boundary") != p["boundary"]
            or oe.get("authorization_ref") != p["authorization_ref"]
            or oe.get("actor_invocation_ref") != p["context_ref"]
            or oe.get("accounting_parent_ref") != p["accounting_parent_ref"]
            or oe.get("delivery_id") != delivery_id
            or oe.get("witness_ref") != row_ref(witness)
            or oe.get("boundary_receipt_ref") != row_ref(boundary)):
        raise RegistryConflict("DELIVERY_UNKNOWN: original delivery events do not close its exact bytes")
    return {"prepared_only": False, "owner": owner, "stages": stages, "facts": facts,
        "witness": (witness, w), "boundary": (boundary, b),
        "proof_refs": tuple(row_ref(row) for row in (prepared, released, witness, boundary, terminal))}


def _completed_delivery(cut, delivery_id, targets):
    chain = _delivery_chain(cut, delivery_id, targets)
    if chain["prepared_only"]:
        return chain["proof_refs"]
    owner, stages, facts = chain["owner"], chain["stages"], chain["facts"]
    prepared, p = stages["prepared"]
    witness, _w = chain["witness"]
    boundary, b = chain["boundary"]
    observed_event, oe = facts["observed_read/v1"]
    record = cut.store.ordered_firing_record(owner, _db=cut.db)
    if record["state"] not in {"PUBLISHED", "REJECTED"}:
        raise RegistryConflict("TARGET_IN_USE: original Petri input consumer is still active")
    firing = record["firing"]
    invocation_row, invocation = cut.obj(p["context_ref"])
    root = cut.db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?", (owner,)).fetchone()
    if (root is None or root["invocation_version_id"] != p["context_ref"]["version_id"]
            or cut.owner(invocation_row) != owner
            or invocation.get("invocation_ref") != p["context_ref"]
            or invocation.get("accounting_parent_invocation_ref") != p["accounting_parent_ref"]
            or invocation.get("own_transition_firing_ref") != firing["transition_firing_ref"]
            or oe.get("firing_ref") != firing["transition_firing_ref"]
            or invocation.get("operation_binding_ref") != firing["operation_binding_ref"]):
        raise RegistryConflict("DELIVERY_UNKNOWN: completed delivery belongs to another operation")
    starts = [event for event in record["events"] if event.event_type == "operation_execution_started/v1"]
    if (len(starts) != 1 or starts[0].payload["invocation_ref"] != p["context_ref"]
            or starts[0].payload["transition_firing_ref"] != firing["transition_firing_ref"]):
        raise RegistryConflict("DELIVERY_UNKNOWN: receipt has no original exact operation Start")
    if p["boundary"] == "petri_input":
        if (firing["operation_binding_ref"] != p["authorization_ref"]
                or p["resource_ref"] not in starts[0].payload["input_resource_refs"]
                or observed_event["ordinal"] >= starts[0].ordinal):
            raise RegistryConflict("DELIVERY_UNKNOWN: receipt is not an original completed Start input")
    elif p["boundary"] == "parent_receipt":
        _literal_parent_input_completed(cut, record, p, b, stages,
            witness, boundary, observed_event, starts[0])
    else:
        from .human_viewer import read_completion
        from .material_settlement import material_admission, completion_evidence
        found = material_admission(cut.store, cut.db, cut.task_identity, firing["transition_firing_ref"])
        if found is None:
            raise RegistryConflict("DELIVERY_UNKNOWN: human viewer lacks its original read admission")
        admission, command = found
        completion = completion_evidence(cut.store, cut.db, cut.task_identity, admission)
        proof = read_completion(cut.store, cut.db, cut.task_identity, admission, command, completion)
        if proof["delivery_id"] != delivery_id:
            raise RegistryConflict("DELIVERY_UNKNOWN: human viewer completion names another delivery")
    # Check actual terminal publication and the complete original transaction,
    # rather than accepting a mutable root state or an isolated completion row.
    terminal_kind = ("transition_firing_settled/v1" if record["state"] == "PUBLISHED"
                     else "transition_firing_rejected/v1")
    closings = [event for event in record["events"] if event.event_type == terminal_kind]
    if len(closings) != 1:
        raise RegistryConflict("DELIVERY_UNKNOWN: input consumer lacks one original terminal")
    closing_row = cut.db.execute("SELECT * FROM events WHERE event_id=?", (str(closings[0].event_id),)).fetchone()
    cut.event(closing_row)
    return chain["proof_refs"]


def _literal_parent_input_completed(cut, record, prepared, receipt, stages,
                                    witness_row, boundary_row, observed, start):
    """Close ONLY the real deterministic literal executor's dedicated D1 use.

    This local transfer finishes inside the registered literal FILE/workspace
    operation. An arbitrary parent_receipt, generic terminal, matching text or
    adapter string does not establish this closure.
    """
    from ...material_execution import is_literal_producer_spec
    from ...material_carriers import inspect_material_carriers_at
    from .material_settlement import material_admission, completion_evidence
    from .material_control import read_accepted_material_input
    if record["state"] != "PUBLISHED" or prepared["purpose"] != "owner request input":
        raise RegistryConflict("TARGET_IN_USE: local receipt does not prove downstream transport finished")
    firing = record["firing"]
    binding_row, binding = cut.obj(firing["operation_binding_ref"])
    spec_row, spec = cut.obj(binding["operation_spec_ref"])
    if (not is_literal_producer_spec(spec)
            or binding.get("llm_input_target_ref") is not None):
        raise RegistryConflict("TARGET_IN_USE: local receipt does not prove downstream transport finished")
    found = material_admission(cut.store, cut.db, cut.task_identity, firing["transition_firing_ref"])
    if found is None:
        raise RegistryConflict("DELIVERY_UNKNOWN: literal input lost its original command admission")
    admission, command = found
    target = {"entity_type": "resource_version/v1", "logical_id": prepared["resource_ref"]["resource_id"],
              "version_id": prepared["resource_ref"]["resource_version_id"]}
    if (command["data"].get("operation") not in {"create_text", "replace_text"}
            or command["data"].get("input_ref") != target
            or admission["invocation_ref"] != prepared["context_ref"]
            or admission["transition_firing_ref"] != firing["transition_firing_ref"]
            or receipt.get("consumer_evidence") != "request builder consumed registered owner input"):
        raise RegistryConflict("DELIVERY_UNKNOWN: parent receipt is not the exact literal command input")
    read_accepted_material_input(cut.store, cut.db, cut.task_identity, target)
    grant_row, grant = cut.obj(prepared["authorization_ref"])
    if (grant_row["object_type"] != "capability_grant/v1"
            or grant.get("target_invocation_id") != admission["invocation_ref"]["logical_id"]
            or grant.get("operation_binding_ref") != firing["operation_binding_ref"]
            or grant.get("resource_version_ids") != [target["version_id"]]):
        raise RegistryConflict("DELIVERY_UNKNOWN: literal input grant differs from its original delivery")
    key = "material-input:" + admission["invocation_ref"]["version_id"]
    expected_keys = ((stages["prepared"][0], key + ":prepare"),
                     (stages["release_authorized"][0], key + ":release"),
                     (stages["acknowledged"][0], key + ":acknowledge"),
                     (witness_row, key + ":release"),
                     (boundary_row, key + ":boundary:boundary-receipt"))
    for row, expected in expected_keys:
        tx = cut.db.execute("SELECT idempotency_key FROM transactions WHERE transaction_id=?",
                            (row["transaction_id"],)).fetchone()
        if tx is None or tx["idempotency_key"] != expected:
            raise RegistryConflict("DELIVERY_UNKNOWN: parent receipt differs from original material input transaction")
    completed = completion_evidence(cut.store, cut.db, cut.task_identity, admission)
    if not start.ordinal < observed["ordinal"] < completed.ordinal:
        raise RegistryConflict("DELIVERY_UNKNOWN: literal input read is outside its actual execution")
    inspection = inspect_material_carriers_at(cut.store, cut.db, cut.task_identity,
        material_command_ref=admission["owner_command_ref"])
    inputs = [item["carrier_ref"] for item in inspection["carriers"] if item["role"] == "dedicated_input"]
    if inputs != [target]:
        raise RegistryConflict("DELIVERY_UNKNOWN: literal completion does not map its exact dedicated input")
    # No extra external adapter, unknown delivery, or LLM responsibility may
    # be hidden behind the trusted local implementation's ordinary Success.
    for row in cut.db.execute("SELECT o.* FROM objects o JOIN firing_temporary_members m "
            "ON m.member_kind='object' AND m.member_identity=o.version_id WHERE m.firing_version_id=?",
            (record["firing_version_id"],)).fetchall():
        if row["object_type"].startswith("llm_"):
            raise RegistryConflict("TARGET_IN_USE: literal input has an additional LLM responsibility")
        if row["object_type"] == _DELIVERY:
            _row, data = cut.obj(row_ref(row))
            if data["boundary"] != "petri_input" and data["delivery_id"] != prepared["delivery_id"]:
                raise RegistryConflict("TARGET_IN_USE: literal input has an additional downstream delivery")


def assert_material_deliveries_drained(store, db, task_id, target_resource_refs):
    """Prove every committed delivery of these exact refs has no pending I/O.

    Callers pass finite original target and proven carrier refs, at the same
    original write lock that will publish the effect.  All original roots are
    inspected, including failed/rejected/provisional and old-writer roots.
    Supported local closures: the genuine original Petri Start of a terminal
    consumer, or the exact dedicated input of a proven completed deterministic
    literal FILE/workspace operation. Other boundaries remain blocked without
    original downstream transport proof.
    """
    targets = set()
    for ref in target_resource_refs:
        reference(ref, "resource_version/v1")
        targets.add((ref["logical_id"], ref["version_id"]))
    if not targets:
        raise RegistryConflict("INVALID_COMMAND: drain requires finite exact targets")
    cut = _Cut(store, db, task_id)
    try:
        proofs = []
        for identity in _discover(cut, targets):
            proofs.extend(_completed_delivery(cut, identity, targets))
    except RegistryConflict:
        raise
    except (RegistryCorruptError, KeyError, TypeError, ValueError) as exc:
        raise RegistryConflict("DELIVERY_UNKNOWN: original delivery proof is incomplete") from exc
    return tuple(proofs)


__all__ = ("assert_material_deliveries_drained", "validate_material_delivery_event")
