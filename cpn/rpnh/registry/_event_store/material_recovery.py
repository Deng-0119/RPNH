"""Exact retained responsibilities for material completion recovery.

The caller supplies the original SQLite cut. Nothing here opens a writer,
creates a queue, changes a lease, or makes another firing's products visible.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict
from ..execution_child_closure import ClosureSnapshot
from ..schema_catalog import canonical_json
from .accounting import _CANONICAL_EVENT_SQL
from .material_control import (
    _committed_descriptor, canonical_rows, read_accepted_material_command,
    read_committed_material_admission, reference, row_ref,
)


def _fail(message):
    raise RegistryConflict("material recovery: " + message)


def _object(db, ref, kind):
    reference(ref, kind)
    row = db.execute("SELECT o.* FROM objects o JOIN transactions t "
        "ON t.transaction_id=o.transaction_id WHERE o.object_type=? "
        "AND o.logical_id=? AND o.version_id=? AND t.status='committed'",
        (kind, ref["logical_id"], ref["version_id"])).fetchone()
    if row is None:
        _fail("an original exact object is unavailable")
    return row


def current_marking(store, db, task_id):
    rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not rows or len({row["logical_id"] for row in rows}) != 1:
        _fail("one current original run is required")
    snapshot = ClosureSnapshot(store, db, task_id)
    run = snapshot.read(row_ref(rows[-1]), "run_execution_authority/v1")
    if run["status"] == "terminal" and run.get("terminal_evidence_ref") is not None:
        from .terminal_management import _original_terminal
        terminal = _original_terminal(store, db, task_id, _verify_physical_heads=False)
        return terminal["authority"], terminal["checkpoint"], terminal["tokens"]
    checkpoint = snapshot.read(run["latest_checkpoint_ref"], "marking_checkpoint/v1")
    adoption = db.execute("SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND " + _CANONICAL_EVENT_SQL +
        " ORDER BY e.ordinal DESC LIMIT 1", (str(task_id),)).fetchone()
    if (run["status"] != "running" or run.get("terminal_evidence_ref") is not None
            or adoption is None
            or json.loads(adoption[0])["net_instance_ref"] != checkpoint["net_instance_ref"]):
        _fail("recovery requires the actual running adopted execution")
    tokens = [snapshot.read(ref, "petri_token/v1") for ref in checkpoint["token_refs"]]
    if any(value["net_instance_ref"] != checkpoint["net_instance_ref"]
           or value["consumed_by"] is not None for value in tokens):
        _fail("the current marking contains an unavailable token")
    return run, checkpoint, tokens


def active_claim_records(store, db, task_id, checkpoint):
    """Read every original open claim and its exact Start/input facts."""
    roots = db.execute("SELECT p.* FROM firing_publications p JOIN transactions t "
        "ON t.transaction_id=p.opened_transaction_id WHERE p.state='PROVISIONAL' "
        "AND p.net_version_id=? AND t.task_id=? AND t.status='committed' "
        "ORDER BY p.firing_version_id",
        (checkpoint["net_instance_ref"]["version_id"], str(task_id))).fetchall()
    present = {canonical_json(ref) for ref in checkpoint["token_refs"]}
    records, claim_sets = [], []
    for root in roots:
        firing_ref = {"entity_type": "transition_firing/v1",
            "logical_id": root["firing_logical_id"], "version_id": root["firing_version_id"]}
        firing_row = _object(db, firing_ref, "transition_firing/v1")
        firing = _committed_descriptor(store, firing_row)
        invocation_ref = {"entity_type": "invocation/v1", "logical_id": root["invocation_logical_id"],
            "version_id": root["invocation_version_id"]}
        parent = {"parent_invocation_ref": invocation_ref,
            "parent_business_firing_ref": firing_ref,
            "parent_business_net_ref": firing["net_instance_ref"],
            "parent_business_checkpoint_ref": firing["admission_marking_checkpoint_ref"]}
        snapshot = ClosureSnapshot(store, db, task_id, provisional_parent=parent)
        firing = snapshot.read(firing_ref, "transition_firing/v1", parent=parent)
        invocation = snapshot.read(invocation_ref, "invocation/v1", parent=parent)
        admission_row = _object(db, firing["firing_admission_ref"], "firing_admission/v1")
        admission = read_committed_material_admission(store, db, task_id, admission_row)
        lease = snapshot.read(admission["operation_execution_lease_ref"],
                              "operation_execution_lease/v1", parent=parent)
        claim = snapshot.read(firing["claim_marking_delta_ref"], "marking_delta/v1", parent=parent)
        claimed = {canonical_json(ref) for ref in firing["claimed_input_refs"]}
        consumed = {canonical_json(ref) for ref in claim["consumed_refs"]}
        if (root["opened_transaction_id"] != admission_row["transaction_id"]
                or firing["transition_firing_ref"] != firing_ref
                or firing["net_instance_ref"] != checkpoint["net_instance_ref"]
                or firing["task_ref"]["logical_id"] != str(task_id)
                or admission["transition_firing_ref"] != firing_ref
                or admission["invocation_ref"] != invocation_ref
                or invocation["own_transition_firing_ref"] != firing_ref
                or invocation["operation_execution_lease_ref"] != lease["operation_execution_lease_ref"]
                or lease["invocation_ref"] != invocation_ref
                or lease["writer_fencing_epoch"] != admission["writer_fencing_epoch"]
                or claim["phase"] != "claim"
                or claim["net_instance_ref"] != firing["net_instance_ref"]
                or claim["transition_firing_refs"] != [firing_ref]
                or claim["operation_binding_refs"] != [firing["operation_binding_ref"]]
                or not claimed <= present or not consumed <= claimed
                or len(claimed) != len(firing["claimed_input_refs"])):
            _fail("an active firing does not retain its original claim/lease")
        events = db.execute("SELECT e.* FROM events e JOIN transactions t "
            "ON t.transaction_id=e.transaction_id JOIN firing_temporary_members m "
            "ON m.member_kind='event' AND m.member_identity=e.event_id "
            "WHERE m.firing_version_id=? AND t.status='committed' ORDER BY e.ordinal",
            (root["firing_version_id"],)).fetchall()
        starts = [row for row in events if row["event_type"] == "operation_execution_started/v1"]
        if len(starts) > 1 or any(row["event_type"] in {
                "operation_terminal_ready/v1", "transition_firing_settled/v1",
                "transition_firing_rejected/v1", "operation_execution_rejected/v1",
                "transition_firing_superseded_by_native_resume/v1",
                "transition_firing_superseded_by_growth_recovery/v1"} for row in events):
            _fail("an active firing has an incompatible terminal or Start")
        start = None
        if starts:
            start = snapshot.event(starts[0], parent=parent)
            if (start["transition_firing_ref"] != firing_ref
                    or start["invocation_ref"] != invocation_ref
                    or start["operation_execution_lease_ref"] != lease["operation_execution_lease_ref"]
                    or start["claimed_input_refs"] != firing["claimed_input_refs"]):
                _fail("a retained Start changed its original input claim")
        members = db.execute("SELECT m.member_kind,m.member_identity,m.transaction_id "
            "FROM firing_temporary_members m JOIN transactions t "
            "ON t.transaction_id=m.transaction_id WHERE m.firing_version_id=? "
            "AND t.status='committed' ORDER BY m.member_kind,m.member_identity",
            (root["firing_version_id"],)).fetchall()
        records.append({"root": dict(root), "firing": firing, "invocation": invocation,
            "admission": admission, "lease": lease, "claim": claim, "start": start,
            "members": [dict(row) for row in members],
            "events": [{key: row[key] for key in ("event_id", "ordinal", "event_type",
                "transaction_id", "writer_fencing_epoch", "payload_json")} for row in events]})
        claim_sets.append((claimed, consumed))
    for index, (claimed, consumed) in enumerate(claim_sets):
        for other_claimed, other_consumed in claim_sets[index + 1:]:
            if consumed & other_claimed or claimed & other_consumed:
                _fail("the original active claim set is incompatible")
    return records


def recovery_facts(store, db, *, task_id, owner_command_ref, firing_ref, prepared=True):
    """Re-derive a selected material boundary without granting a new lease."""
    reference(owner_command_ref, "resource_version/v1")
    reference(firing_ref, "transition_firing/v1")
    command = read_accepted_material_command(store, db, task_id, owner_command_ref, current=False)
    run, checkpoint, tokens = current_marking(store, db, task_id)
    if run["status"] == "terminal":
        from .terminal_management import terminal_management_scope, require_single_terminal_firing
        terminal_management_scope(store, db, task_id, target=command["target"],
            operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
        require_single_terminal_firing(store, db, task_id, allowed_firing_ref=firing_ref)
    records = active_claim_records(store, db, task_id, checkpoint)
    matches = [record for record in records if record["firing"]["transition_firing_ref"] == firing_ref]
    if len(matches) != 1:
        _fail("the exact selected firing is not an open original responsibility")
    selected, = matches
    admission, firing = selected["admission"], selected["firing"]
    if (admission.get("owner_command_ref") != owner_command_ref
            or command["target"] != firing["transition_id"]
            or command["data"]["execution"] != {
                key: firing[key] for key in ("task_ref", "task_round_ref", "net_instance_ref")}):
        _fail("the exact command and selected firing differ")
    binding = ClosureSnapshot(store, db, task_id).read(
        firing["operation_binding_ref"], "operation_binding/v1")
    spec = ClosureSnapshot(store, db, task_id).read(binding["operation_spec_ref"], "operation_spec/v1")
    if (spec.get("transport") != "deterministic"
            or spec.get("implementation_contracts", {}).get("owner_material_commands") != "rpnh/material_command/v1"
            or binding.get("llm_input_target_ref") is not None):
        _fail("only the original non-model material operation may be selected")
    from .material_settlement import completion_evidence
    completion = completion_evidence(store, db, task_id, admission)
    if prepared:
        from .firing_rejection import prepared_snapshot
        prepared_snapshot(store, db, task_id=task_id, firing_ref=firing_ref,
            invocation_ref=admission["invocation_ref"], owner_command_ref=owner_command_ref,
            retained_checkpoint_ref=run["latest_checkpoint_ref"],
            registered_completion_event_id=str(completion.event_id))
    return {"run": run, "checkpoint": checkpoint, "tokens": tokens,
        "command": command, "owner_command_ref": owner_command_ref, "firing_ref": firing_ref,
        "completion_event_id": str(completion.event_id),
        "completion_payload": dict(completion.payload), "active": records}


def material_budget_event_inventory(store, db, *, task_id, owner_command_ref, firing_ref):
    """Return only the original command's fully verified budget control facts.

    The attempt reader checks every object, relation, event and transaction
    backpointer. This is an exact identity allowlist, not an event-type rule.
    """
    from .material_publication_attempts import read_attempt_state

    state = read_attempt_state(store, db, task_id=task_id,
        owner_command_ref=owner_command_ref, firing_ref=firing_ref)
    inventory = {}
    for attempt in state["attempts"]:
        for prefix in ("reservation", "conflict"):
            transaction_id = attempt[prefix + "_transaction_id"]
            event_ids = attempt[prefix + "_event_ids"]
            if transaction_id is not None:
                for event_id in event_ids:
                    if event_id in inventory:
                        _fail("material budget repeats an original control identity")
                    inventory[event_id] = transaction_id
    return state, inventory


def has_unrelated_later_writer_events(store, db, *, task_id, stale_epoch, firing_ref=None):
    """Retain the old lease fence except for exact material budget controls."""
    later = db.execute("SELECT event_id,transaction_id,task_id FROM events "
        "WHERE writer_fencing_epoch>?", (stale_epoch,)).fetchall()
    if not later:
        return False
    if firing_ref is None:
        return True
    from .material_settlement import material_admission

    found = material_admission(store, db, task_id, firing_ref)
    if found is None:
        return True
    _state, inventory = material_budget_event_inventory(store, db, task_id=task_id,
        owner_command_ref=found[0]["owner_command_ref"], firing_ref=firing_ref)
    return any(row["task_id"] != str(task_id)
        or inventory.get(row["event_id"]) != row["transaction_id"] for row in later)


def validate_material_recovery_at_promotion(store, db, *, task_id, root, publication, transaction_id):
    """Keep every other original root and input intact at the actual write lock."""
    firing_ref = {"entity_type": "transition_firing/v1", "logical_id": root["firing_logical_id"],
                  "version_id": root["firing_version_id"]}
    # The unconditional pre-INSERT material-result gate has already verified
    # the complete accepted command, current authority and action-specific
    # drain under this same write lock. At promotion the appended events and
    # members precede their global heads: do not reinterpret availability or
    # delivery semantics through that transient cut. Only the original exact
    # admission identity and writer fence are needed for this late gate.
    rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE o.object_type='firing_admission/v1' AND t.task_id=? AND t.status='committed' "
        "AND json_extract(o.metadata_json,'$.transition_firing_ref.version_id')=?",
        (str(task_id), root["firing_version_id"])).fetchall()
    if len(rows) != 1:
        _fail("promotion lost its exact original admission")
    admission = _committed_descriptor(store, rows[0])
    if admission.get("owner_command_ref") is None:
        return
    if (rows[0]["transaction_id"] != root["opened_transaction_id"]
            or admission["transition_firing_ref"] != firing_ref
            or admission["invocation_ref"] != {
                "entity_type": "invocation/v1", "logical_id": root["invocation_logical_id"],
                "version_id": root["invocation_version_id"]}
            or publication["invocation_version_id"] != root["invocation_version_id"]):
        _fail("promotion changed its original admission or invocation")
    epoch = int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    if admission["writer_fencing_epoch"] == epoch:
        return
    tx = db.execute("SELECT writer_epoch FROM transactions WHERE transaction_id=?",
                    (str(transaction_id),)).fetchone()
    if tx is None or tx[0] != epoch or admission["writer_fencing_epoch"] >= epoch:
        _fail("settlement is not owned by the sole fresh writer")
    # The unconditional material-result gate already checked the exact command,
    # registered completion and full prepared inventory before inserting this
    # transaction, under this same BEGIN IMMEDIATE. At promotion the new events
    # and members exist but their global stream/task heads are not updated yet.
    # Re-read the original marking and claims needed here, without asking a
    # persisted-completion reader to interpret this in-progress transaction.
    _run, before, _tokens = current_marking(store, db, task_id)
    active = active_claim_records(store, db, task_id, before)
    selected = [record for record in active
        if record["firing"]["transition_firing_ref"] == firing_ref]
    if (len(selected) != 1 or selected[0]["admission"] != admission
            or selected[0]["root"] != dict(root)):
        _fail("promotion changed the exact selected original responsibility")
    checkpoint_row = db.execute("SELECT * FROM objects WHERE object_type='marking_checkpoint/v1' "
        "AND version_id=? AND transaction_id=?",
        (publication["marking_checkpoint_version_id"], str(transaction_id))).fetchone()
    if checkpoint_row is None:
        _fail("recovery has no same-transaction real successor")
    successor = json.loads(checkpoint_row["metadata_json"])
    if (successor["previous_checkpoint_ref"] != before["marking_checkpoint_ref"]
            or successor["net_instance_ref"] != before["net_instance_ref"]
            or successor["transition_firing_refs"] != [firing_ref]):
        _fail("recovery changed the selected marking boundary")
    after_refs = {canonical_json(ref) for ref in successor["token_refs"]}
    for other in active:
        if other["firing"]["transition_firing_ref"] == firing_ref:
            continue
        retained = {canonical_json(ref) for ref in other["firing"]["claimed_input_refs"]}
        if not retained <= after_refs:
            _fail("recovery would replace another firing's exact input")
        changed = db.execute("SELECT 1 FROM firing_temporary_members WHERE firing_version_id=? "
            "AND transaction_id=? LIMIT 1",
            (other["root"]["firing_version_id"], str(transaction_id))).fetchone()
        produced = db.execute("SELECT 1 FROM events WHERE transaction_id=? "
            "AND producer_invocation_id=? LIMIT 1",
            (str(transaction_id), other["root"]["invocation_logical_id"])).fetchone()
        if changed is not None or produced is not None:
            _fail("recovery transaction changes another original firing")

