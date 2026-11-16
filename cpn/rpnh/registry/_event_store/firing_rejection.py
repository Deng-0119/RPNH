"""Exact nonpublication closure for completed deterministic material firings.

All authoritative checks use the caller's original SQLite write snapshot.
Only the closure transaction becomes canonical; its prior candidate root does
not. Unknown external work remains an unresolved execution responsibility.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from ..event_store import RegistryConflict, FiringRejectionRecoveryRequired
from ..identities import TypedId
from ..models import VersionRef
from ..schema_catalog import canonical_json
from .accounting import _CANONICAL_EVENT_SQL
from .collaboration_descriptors import descriptor_store, exact_descriptor, readable_descriptor
from .material_control import (
    _committed_descriptor, canonical_rows, control_body,
    read_accepted_material_command, read_committed_material_admission,
    reference, row_ref,
)


REJECTION = "firing_rejection/v1"
FIRING_EVENT = "transition_firing_rejected/v1"
OPERATION_EVENT = "operation_execution_rejected/v1"
COMPLETION_EVENT = "registered_operation_completion_recorded/v1"
COMMAND_FIELDS = frozenset({"firing_version_id", "invocation_version_id",
    "owner_command_version_id", "expected_parent_checkpoint_version_id",
    "registered_completion_event_id", "rejection_version_id"})


def recovery_required(message):
    raise FiringRejectionRecoveryRequired("RECOVERY_REQUIRED: " + message)


def validate_commands(commands):
    if (len(commands) > 1 or any(set(item) != COMMAND_FIELDS
            or any(not isinstance(value, str) or not value for value in item.values())
            for item in commands)):
        raise RegistryConflict("rejection requires one exact native command")
    for item in commands:
        TypedId.parse(item["registered_completion_event_id"], expected="event")


def _row(db, version, kind):
    row = db.execute("SELECT * FROM objects WHERE version_id=? AND object_type=?",
                     (version, kind)).fetchone()
    if row is None:
        recovery_required("missing original " + kind)
    return row


def _member_events(db, firing_id):
    return db.execute("SELECT e.* FROM firing_temporary_members m JOIN events e "
        "ON e.event_id=m.member_identity WHERE m.firing_version_id=? "
        "AND m.member_kind='event' ORDER BY e.ordinal", (firing_id,)).fetchall()


def _children(snapshot, parent, completion_ordinal, *, purge_definition=None):
    """Recheck the original completed child inventory and permitted definitions."""
    from ...file_execution_net import FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET
    from ..execution_runtime import ExecutionRuntime

    db = snapshot.db
    invocation = parent["parent_invocation_ref"]["logical_id"]
    firing = parent["parent_business_firing_ref"]
    rows = db.execute("SELECT o.* FROM objects o WHERE o.object_type='execution_instance/v1' "
        "AND (o.producer_invocation_id=? OR json_extract(o.metadata_json,"
        "'$.parent_business_firing_ref.version_id')=?)", (invocation, firing["version_id"])).fetchall()
    attaches = db.execute("SELECT * FROM events WHERE event_type='execution_instance_attached/v1' "
        "AND (producer_invocation_id=? OR stream_id=?) ORDER BY ordinal",
        (invocation, "execution-children:" + firing["version_id"])).fetchall()
    attached = {}
    for row in attaches:
        value = snapshot.event(row, parent=parent)
        ref = value.get("execution_instance_ref")
        if (row["ordinal"] >= completion_ordinal
                or row["producer_invocation_id"] != invocation
                or row["stream_id"] != "execution-children:" + firing["version_id"]
                or value.get("parent_invocation_ref") != parent["parent_invocation_ref"]
                or value.get("parent_business_firing_ref") != firing
                or not isinstance(ref, Mapping) or ref["version_id"] in attached):
            recovery_required("child attach inventory differs from original completion")
        attached[ref["version_id"]] = value
    if {row["version_id"] for row in rows} != set(attached) or len(rows) != len(attached):
        recovery_required("child inventory is not exactly accounted for")
    result = []
    for row in sorted(rows, key=lambda value: value["version_id"]):
        ref = row_ref(row)
        item = snapshot.read(ref, "execution_instance/v1", parent=parent)
        if any(item.get(key) != value for key, value in parent.items()):
            recovery_required("child belongs to another execution")
        definition_ref = item["execution_net_definition_ref"]
        definition = snapshot.read(definition_ref, "execution_net_definition/v1", parent=parent)
        native = ExecutionRuntime._definition_from_metadata(definition)
        permitted = ((purge_definition,) if purge_definition is not None
                     else (FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET))
        if native not in permitted:
            recovery_required("unsupported execution child responsibility")
        checkpoints = db.execute("SELECT o.*,e.ordinal AS publication_ordinal FROM objects o "
            "JOIN events e ON e.event_id=o.published_event_id WHERE o.object_type='execution_checkpoint/v1' "
            "AND json_extract(o.metadata_json,'$.execution_instance_ref.version_id')=? "
            "ORDER BY e.ordinal", (ref["version_id"],)).fetchall()
        previous = None
        for checkpoint in checkpoints:
            value = snapshot.read(row_ref(checkpoint), "execution_checkpoint/v1", parent=parent)
            if (checkpoint["publication_ordinal"] >= completion_ordinal
                    or value["execution_instance_ref"] != ref
                    or value["execution_net_definition_ref"] != definition_ref
                    or value["sequence"] != (previous["sequence"] + 1 if previous else 0)
                    or value["previous_checkpoint_ref"] != (previous["execution_checkpoint_ref"] if previous else None)):
                recovery_required("execution child changed after completion or lost its checkpoint chain")
            previous = value
        if (previous is None or previous["status"] != "map_ready"
                or previous["active_firing_refs"] or not previous["token_refs"]
                or not previous["evidence_refs"]
                or attached[ref["version_id"]]["initial_checkpoint_ref"] != row_ref(checkpoints[0])):
            recovery_required("execution child is not completely prepared")
        for token_ref in previous["token_refs"]:
            token = snapshot.read(token_ref, "execution_token/v1", parent=parent)
            if token["execution_instance_ref"] != ref or token["place"] not in native.terminal_places:
                recovery_required("execution child still owns a nonterminal token")
        for evidence in previous["evidence_refs"]:
            if evidence["entity_type"] not in {"resource_version/v1", "workspace_revision_candidate/v1"}:
                recovery_required("execution evidence requires an unsupported effect validator")
            # Workspace candidates contain the original tar archive; resource
            # evidence retains its own declared payload codec. Neither is a
            # JSON descriptor merely because its immutable metadata is JSON.
            snapshot.read(evidence, parent=parent, descriptor=False)
        result.append({"execution_instance_ref": ref,
                       "terminal_checkpoint_ref": previous["execution_checkpoint_ref"]})
    return result


def prepared_snapshot(store, db, *, task_id, firing_ref, invocation_ref,
                      owner_command_ref, retained_checkpoint_ref,
                      registered_completion_event_id):
    """Read the original completed responsibility set without granting reuse."""
    from ..execution_child_closure import ClosureSnapshot

    firing_ref = reference(firing_ref, "transition_firing/v1")
    invocation_ref = reference(invocation_ref, "invocation/v1")
    owner_command_ref = reference(owner_command_ref, "resource_version/v1")
    retained_checkpoint_ref = reference(retained_checkpoint_ref, "marking_checkpoint/v1")
    root = db.execute("SELECT p.*,t.status,t.task_id FROM firing_publications p "
        "JOIN transactions t ON t.transaction_id=p.opened_transaction_id "
        "WHERE p.firing_version_id=?", (firing_ref["version_id"],)).fetchone()
    if (root is None or root["state"] != "PROVISIONAL" or root["status"] != "committed"
            or root["task_id"] != str(task_id) or root["firing_logical_id"] != firing_ref["logical_id"]
            or root["invocation_logical_id"] != invocation_ref["logical_id"]
            or root["invocation_version_id"] != invocation_ref["version_id"]):
        recovery_required("firing is not the exact open material responsibility")
    firing = _committed_descriptor(store, _row(db, firing_ref["version_id"], "transition_firing/v1"))
    parent = {"parent_invocation_ref": invocation_ref, "parent_business_firing_ref": firing_ref,
              "parent_business_net_ref": firing["net_instance_ref"],
              "parent_business_checkpoint_ref": firing["admission_marking_checkpoint_ref"]}
    snapshot = ClosureSnapshot(store, db, task_id, provisional_parent=parent)
    firing = snapshot.read(firing_ref, "transition_firing/v1", parent=parent)
    invocation = snapshot.read(invocation_ref, "invocation/v1", parent=parent)
    admission_row = _row(db, firing["firing_admission_ref"]["version_id"], "firing_admission/v1")
    admission = read_committed_material_admission(store, db, task_id, admission_row, owner_command_ref)
    command = read_accepted_material_command(store, db, task_id, owner_command_ref, current=False)
    from .material_locations import is_location, location_bindings
    location = is_location(command["data"])
    from .material_availability import is_availability, is_metadata_operation, availability_completion
    metadata_operation = is_metadata_operation(command["data"])
    binding = snapshot.read(firing["operation_binding_ref"], "operation_binding/v1")
    spec = snapshot.read(binding["operation_spec_ref"], "operation_spec/v1")
    if (spec.get("transport") != "deterministic"
            or spec.get("implementation_contracts", {}).get("owner_material_commands") != "rpnh/material_command/v1"
            or binding.get("llm_input_target_ref") is not None
            or command["target"] != firing["transition_id"]
            or command["data"]["execution"] != {key: firing[key] for key in ("task_ref", "task_round_ref", "net_instance_ref")}
            or invocation.get("own_transition_firing_ref") != firing_ref
            or invocation.get("operation_binding_ref") != firing["operation_binding_ref"]
            or admission.get("invocation_ref") != invocation_ref):
        recovery_required("rejection is limited to the exact deterministic material operation")
    run_rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not run_rows:
        recovery_required("original run authority is unavailable")
    run = snapshot.read(row_ref(run_rows[-1]), "run_execution_authority/v1")
    checkpoint = snapshot.read(retained_checkpoint_ref, "marking_checkpoint/v1")
    active_net = db.execute("SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND " + _CANONICAL_EVENT_SQL +
        " ORDER BY e.ordinal DESC LIMIT 1", (str(task_id),)).fetchone()
    terminal = run.get("status") == "terminal" and run.get("terminal_evidence_ref") is not None
    if terminal:
        from .terminal_management import terminal_management_scope, require_single_terminal_firing
        terminal_management_scope(store, db, task_id, target=command["target"],
            operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
        require_single_terminal_firing(store, db, task_id, allowed_firing_ref=firing_ref)
    if ((not terminal and (run.get("status") != "running" or run.get("terminal_evidence_ref") is not None))
            or run.get("latest_checkpoint_ref") != retained_checkpoint_ref
            or checkpoint.get("net_instance_ref") != firing["net_instance_ref"]
            or active_net is None or json.loads(active_net[0]).get("net_instance_ref") != firing["net_instance_ref"]
            or not set(firing["claimed_input_version_ids"]).issubset(
                {ref["version_id"] for ref in checkpoint["token_refs"]})):
        recovery_required("current run/marking no longer retains the original claim")
    lease_ref = invocation["operation_execution_lease_ref"]
    lease = snapshot.read(lease_ref, "operation_execution_lease/v1", parent=parent)
    rows = _member_events(db, firing_ref["version_id"])
    completions = [row for row in rows if row["event_type"] == COMPLETION_EVENT]
    starts = [row for row in rows if row["event_type"] == "operation_execution_started/v1"]
    if (len(completions) != 1 or len(starts) != 1
            or completions[0]["event_id"] != registered_completion_event_id):
        recovery_required("exact registered completion and real Start are required")
    completion = snapshot.event(completions[0], parent=parent)
    start = snapshot.event(starts[0], parent=parent)
    if (completion.get("invocation_ref") != invocation_ref
            or completion.get("transition_firing_ref") != firing_ref
            or completion.get("operation_execution_lease_ref") != lease_ref
            or completion.get("operation_start_event_id") != starts[0]["event_id"]
            or completion.get("operation_binding_ref") != firing["operation_binding_ref"]
            or completion.get("operation_spec_ref") != binding["operation_spec_ref"]
            or completion.get("firing_admission_ref") != firing["firing_admission_ref"]
            or (completion.get("workspace_revision_candidate_ref") is None and not metadata_operation)
            or start.get("invocation_ref") != invocation_ref
            or start.get("transition_firing_ref") != firing_ref
            or start.get("operation_execution_lease_ref") != lease_ref
            or lease.get("operation_execution_lease_ref") != lease_ref):
        recovery_required("registered completion differs from its original execution")
    forbidden = db.execute("SELECT o.object_type FROM objects o JOIN firing_temporary_members m "
        "ON m.member_kind='object' AND m.member_identity=o.version_id WHERE m.firing_version_id=? "
        "AND (o.object_type LIKE 'llm_%' OR o.object_type LIKE 'provider_%' "
        "OR o.object_type IN ('agent_loop/v1','agent_action/v2','operation_result/v1','firing_completion/v2')) LIMIT 1",
        (firing_ref["version_id"],)).fetchone()
    if forbidden or any(row["event_type"] in {"operation_terminal_ready/v1", "execution_children_sealed/v1",
                "transition_firing_settled/v1", FIRING_EVENT, OPERATION_EVENT} for row in rows):
        recovery_required("firing has an unsupported or already terminal responsibility")
    deliveries = {}
    for row in rows:
        if row["event_type"] in {"resource_delivery_prepared/v1", "resource_release_authorized/v1",
                "resource_delivery_acknowledged/v1", "resource_delivery_failed/v1", "resource_delivery_unknown/v1"}:
            snapshot.event(row, parent=parent)
            deliveries.setdefault(row["aggregate_id"], []).append(row["event_type"])
    if any(items[0] != "resource_delivery_prepared/v1"
           or items[-1] not in {"resource_delivery_acknowledged/v1", "resource_delivery_failed/v1"}
           for items in deliveries.values()):
        recovery_required("input delivery is unresolved or externally uncertain")
    candidate_ref = completion["workspace_revision_candidate_ref"]
    from .human_viewer import is_read_material, read_completion
    from .material_purge import is_purge, purge_completion
    if is_availability(command["data"]):
        availability_completion(store, db, task_id, admission, command, store._row_to_envelope(completions[0]))
    elif is_read_material(command["data"]):
        read_completion(store, db, task_id, admission, command, store._row_to_envelope(completions[0]))
    elif is_purge(command["data"]):
        purge_completion(store, db, task_id, admission, command, store._row_to_envelope(completions[0]))
    elif location:
        location_bindings(store, db, task_id, admission, command,
                          store._row_to_envelope(completions[0]))
    else:
        snapshot.read(candidate_ref, "workspace_revision_candidate/v1", parent=parent, descriptor=False)
        candidate = json.loads(snapshot.rows[candidate_ref["version_id"]]["metadata_json"])
        if candidate.get("transition_firing_ref") != firing_ref or candidate.get("invocation_ref") != invocation_ref:
            recovery_required("workspace candidate belongs to another firing")
    for output in completion["ordered_outputs"]:
        resource = output["resource_ref"]
        snapshot.read({"entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
                       "version_id": resource["resource_version_id"]}, parent=parent, descriptor=False)
        snapshot.read(output["output_binding_ref"], "output_binding/v1")
    # purge_completion above has proved this exact original Phase B, its
    # single registered child and physical result. Its definition is local
    # to that action; other material operations retain the original whitelist.
    purge_definition = None
    if command["data"].get("operation") == "purge_carrier":
        from ...material_purge import PURGE_NET
        purge_definition = PURGE_NET
    children = _children(snapshot, parent, completions[0]["ordinal"],
                         purge_definition=purge_definition)
    live = []
    lifecycle_rows = db.execute("SELECT o.* FROM objects o WHERE o.object_type='resource_access_lifecycle/v1' "
        "AND o.rowid=(SELECT MAX(h.rowid) FROM objects h WHERE h.object_type=o.object_type AND h.logical_id=o.logical_id)").fetchall()
    for row in lifecycle_rows:
        value = json.loads(row["metadata_json"])
        if value.get("transition_firing_ref") == firing_ref and value.get("state") != "sealed":
            live.append(snapshot.read(row_ref(row), "resource_access_lifecycle/v1", parent=parent))
    return {"root": dict(root), "firing": firing, "invocation": invocation, "command": command,
            "lease_ref": lease_ref, "children": children, "live_lifecycles": live,
            "completion": completion}


def validate_commit(store, db, *, task_id, task_round_id, net_instance_id,
                    transaction_id, idempotency_key, objects, events, relations, commands):
    """Return the exact relations permitted outside the hidden rejected root."""
    validate_commands(commands)
    closures = [item for item in objects if item.object_type == REJECTION]
    closing_events = [event for event in events if event.event_type in {FIRING_EVENT, OPERATION_EVENT}]
    if not commands:
        rejected_seals = [item for item in objects if item.object_type == "resource_access_lifecycle/v1"
            and isinstance(item.metadata.get("settlement_ref"), Mapping)
            and item.metadata["settlement_ref"].get("entity_type") == REJECTION]
        if closures or closing_events or rejected_seals:
            raise RegistryConflict("rejection closure requires its native transaction command")
        return set()
    command, = commands
    if len(closures) != 1:
        raise RegistryConflict("rejection requires one same-transaction closure")
    closure, = closures
    value = dict(closure.metadata)
    closure_ref = row_ref({"object_type": closure.object_type, "logical_id": str(closure.logical_id),
                           "version_id": str(closure.version_id)})
    if (closure.producer_invocation_id is not None or value.get("firing_rejection_ref") != closure_ref
            or str(closure.version_id) != command["rejection_version_id"]
            or value["transition_firing_ref"]["version_id"] != command["firing_version_id"]
            or value["invocation_ref"]["version_id"] != command["invocation_version_id"]
            or value["owner_command_ref"]["version_id"] != command["owner_command_version_id"]
            or value["retained_checkpoint_ref"]["version_id"] != command["expected_parent_checkpoint_version_id"]
            or value["registered_completion_event_id"] != command["registered_completion_event_id"]
            or readable_descriptor(descriptor_store(store), closure) != value):
        raise RegistryConflict("rejection command differs from its exact closure")
    state = prepared_snapshot(store, db, task_id=task_id,
        firing_ref=value["transition_firing_ref"], invocation_ref=value["invocation_ref"],
        owner_command_ref=value["owner_command_ref"], retained_checkpoint_ref=value["retained_checkpoint_ref"],
        registered_completion_event_id=value["registered_completion_event_id"])
    if state["command"]["data"].get("operation") == "purge_carrier":
        # The original completed Phase B may already have removed bytes.
        # Business rejection cannot roll that effect back or release its
        # authorization. Enforce this at the final write, including raw staging.
        recovery_required("physical purge retains its original authorization and execution responsibility")
    if (str(task_round_id) != state["firing"]["task_round_ref"]["logical_id"]
            or str(net_instance_id) != state["firing"]["net_instance_ref"]["logical_id"]
            or value["operation_execution_lease_ref"] != state["lease_ref"]
            or value["execution_children"] != state["children"]):
        recovery_required("closure scope or child terminal set changed")
    seals = [item for item in objects if item.object_type == "resource_access_lifecycle/v1"]
    proposed = {str(item.logical_id): item for item in seals}
    live = {item["lifecycle_ref"]["logical_id"]: item for item in state["live_lifecycles"]}
    if len(proposed) != len(seals) or set(proposed) != set(live):
        recovery_required("resource responsibilities were not sealed exactly")
    for logical_id, prior in live.items():
        item = proposed[logical_id]
        expected = {**prior, "lifecycle_ref": row_ref({"object_type": item.object_type,
            "logical_id": str(item.logical_id), "version_id": str(item.version_id)}),
            "state": "sealed", "seal_ref": item.metadata.get("seal_ref"),
            "settlement_ref": closure_ref, "settlement_disposition": "abandon"}
        if (dict(item.metadata) != expected or not expected["seal_ref"]
                or str(item.producer_invocation_id) != value["invocation_ref"]["logical_id"]
                or readable_descriptor(descriptor_store(store), item) != expected):
            recovery_required("resource seal changed an original responsibility")
    if sorted(value["sealed_resource_lifecycle_refs"], key=lambda ref: ref["version_id"]) != sorted(
            [dict(item.metadata)["lifecycle_ref"] for item in seals], key=lambda ref: ref["version_id"]):
        raise RegistryConflict("rejection closure omits a resource seal")
    controls = [item for item in objects if item.object_type == "resource_version/v1"]
    if len(controls) != 1:
        raise RegistryConflict("rejection requires its single original OwnerControl result")
    result = control_body(store, controls[0])
    if (result["kind"] != "result" or result["data"].get("protocol") != "rpnh/material_result/v1"
            or result["data"].get("result_kind") != "publish_rejected"
            or result["data"].get("rejection_ref") != closure_ref
            or result["data"].get("command_ref") != value["owner_command_ref"]
            or result["data"].get("firing_ref") != value["transition_firing_ref"]
            or result["data"].get("registered_completion_event_id") != value["registered_completion_event_id"]
            or result["data"].get("error") != value["reason"]):
        raise RegistryConflict("rejected result differs from its same-transaction native closure")
    allowed_objects = {REJECTION, "resource_access_lifecycle/v1", "resource_version/v1"}
    allowed_events = {FIRING_EVENT, OPERATION_EVENT, "object_version_published/v1",
                      "relation_published/v1", "transaction_committed/v1"}
    if any(item.object_type not in allowed_objects for item in objects) or any(
            event.event_type not in allowed_events for event in events):
        raise RegistryConflict("rejection cannot publish candidates or mutate business marking")
    for kind, ref, stream, aggregate_type in (
            (FIRING_EVENT, value["transition_firing_ref"], "transition-firing:", "transition_firing"),
            (OPERATION_EVENT, value["operation_execution_lease_ref"], "operation-execution:", "operation_execution_lease")):
        matches = [event for event in closing_events if event.event_type == kind]
        if (len(matches) != 1 or dict(matches[0].payload) != value
                or str(matches[0].producer_invocation_id) != value["invocation_ref"]["logical_id"]
                or matches[0].producer_principal != "framework"
                or matches[0].stream_id != stream + ref["logical_id"]
                or matches[0].aggregate_id != ref["logical_id"]
                or matches[0].aggregate_type != aggregate_type):
            raise RegistryConflict("rejection lacks its exact firing and operation termination facts")
    def identity(ref):
        return (ref["entity_type"], ref["logical_id"], ref["version_id"])
    def exact_endpoint(ref):
        if not isinstance(ref, VersionRef):
            raise RegistryConflict("rejection relation requires exact version endpoints")
        return (ref.entity_type, str(ref.entity_id), str(ref.version_id))
    closure_targets = [value[name] for name in (
        "owner_command_ref", "transition_firing_ref", "invocation_ref",
        "operation_execution_lease_ref", "retained_checkpoint_ref")]
    closure_targets.extend(child[name] for child in value["execution_children"]
                           for name in ("execution_instance_ref", "terminal_checkpoint_ref"))
    closure_targets.extend(value["sealed_resource_lifecycle_refs"])
    result_ref = (controls[0].object_type, str(controls[0].logical_id), str(controls[0].version_id))
    expected_relations = {(identity(closure_ref), "derived_from", identity(ref)) for ref in closure_targets}
    expected_relations.update({(result_ref, "produced_by", identity(controls[0].metadata["producer_ref"])),
                               (result_ref, "derived_from", identity(value["owner_command_ref"]))})
    allowed = set()
    actual_relations = []
    for relation in relations:
        if (not relation.system_owned or relation.producer_invocation_id is not None
                or relation.strength != "strong"):
            raise RegistryConflict("rejection control relation exceeds its exact closure")
        actual_relations.append((exact_endpoint(relation.source), relation.relation_type,
                                 exact_endpoint(relation.target)))
        allowed.add(str(relation.relation_id))
    if len(actual_relations) != len(expected_relations) or set(actual_relations) != expected_relations:
        raise RegistryConflict("rejection control relations differ from the whole exact closure")
    # Whitelisting event type names alone would let a caller append an old
    # candidate's publication fact into this canonical control transaction.
    # Close the complete automatically generated inventory, including counts.
    inventory = {}
    for event in closing_events:
        inventory[(event.event_type, event.aggregate_id)] = (
            event.stream_id, event.aggregate_type, value, value["invocation_ref"]["logical_id"])
    for item in objects:
        inventory[("object_version_published/v1", str(item.logical_id))] = (
            "object:" + str(item.logical_id), item.object_type,
            {"logical_id": str(item.logical_id), "version_id": str(item.version_id),
             "object_type": item.object_type, "size": item.size, "media_type": item.media_type,
             "schema_ref": item.schema_ref, "storage_locator": item.storage_locator,
             "metadata": dict(item.metadata)},
            str(item.producer_invocation_id) if item.producer_invocation_id is not None else None)
    def endpoint(ref):
        return {"entity_type": ref.entity_type, "entity_id": str(ref.entity_id),
                "version_id": str(ref.version_id)}
    for relation in relations:
        inventory[("relation_published/v1", str(relation.relation_id))] = (
            "relation:" + str(relation.relation_id), "typed_relation/v1",
            {"relation_id": str(relation.relation_id), "relation_type": relation.relation_type,
             "source": endpoint(relation.source), "target": endpoint(relation.target),
             "strength": relation.strength, "metadata": dict(relation.metadata)}, None)
    inventory[("transaction_committed/v1", str(transaction_id))] = (
        "transaction:" + str(transaction_id), "transaction",
        {"object_count": len(objects), "relation_count": len(relations), "fact_count": len(inventory)}, None)
    if len(events) != len(inventory) or len({(event.event_type, event.aggregate_id) for event in events}) != len(events):
        raise RegistryConflict("rejection control event inventory has extra or repeated facts")
    for event in events:
        expected = inventory.get((event.event_type, event.aggregate_id))
        if (expected is None or event.stream_id != expected[0]
                or event.aggregate_type != expected[1] or canonical_json(event.payload) != canonical_json(expected[2])
                or (str(event.producer_invocation_id) if event.producer_invocation_id is not None else None) != expected[3]
                or event.criticality != "authoritative" or event.producer_principal != "framework"
                or event.payload_schema_ref != "registry_v1/" + event.event_type
                or event.idempotency_key != idempotency_key or event.command_id != idempotency_key
                or event.task_control or event.causation_event_id is not None or event.parent_event_ids):
            raise RegistryConflict("rejection control fact differs from its exact closure inventory")
    return allowed


def apply(store, db, *, commands, transaction_id):
    for command in commands:
        firing_id = command["firing_version_id"]
        store._before_firing_authority_mutation(db, firing_version_id=firing_id,
                                              transaction_id=str(transaction_id))
        db.execute("UPDATE firing_publications SET state='REJECTED',published_transaction_id=? "
            "WHERE firing_version_id=? AND invocation_version_id=? AND state='PROVISIONAL'",
            (str(transaction_id), firing_id, command["invocation_version_id"]))
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise RegistryConflict("rejection SQL CAS did not close exactly one original root")
        store._after_firing_authority_mutation(db, firing_version_id=firing_id,
                                             transaction_id=str(transaction_id))


def read_rejection(store, db, root):
    rows = db.execute("SELECT * FROM objects WHERE object_type=? AND transaction_id=?",
                      (REJECTION, root["published_transaction_id"])).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("rejected root lacks its single canonical closure")
    value = exact_descriptor(db, descriptor_store(store),
        db.execute("SELECT task_id FROM transactions WHERE transaction_id=?",
                   (root["published_transaction_id"],)).fetchone()[0], row_ref(rows[0]))
    if (value["transition_firing_ref"]["version_id"] != root["firing_version_id"]
            or value["invocation_ref"]["version_id"] != root["invocation_version_id"]):
        raise RegistryConflict("rejected root closure belongs to another firing")
    events = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type IN (?,?) ORDER BY ordinal",
        (root["published_transaction_id"], FIRING_EVENT, OPERATION_EVENT)).fetchall()
    if (len(events) != 2 or {event["event_type"] for event in events} != {FIRING_EVENT, OPERATION_EVENT}
            or any(json.loads(event["payload_json"]) != value for event in events)):
        raise RegistryConflict("rejected root lacks exact canonical termination facts")
    return value, tuple(store._row_to_envelope(row) for row in events)


def validate_replay(store, db, commands, transaction_id):
    for command in commands:
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                          (command["firing_version_id"],)).fetchone()
        if (root is None or root["state"] != "REJECTED"
                or root["published_transaction_id"] != transaction_id
                or root["invocation_version_id"] != command["invocation_version_id"]):
            raise RegistryConflict("rejection replay differs from its original closed root")
        value, _events = read_rejection(store, db, root)
        if (value["firing_rejection_ref"]["version_id"] != command["rejection_version_id"]
                or value["owner_command_ref"]["version_id"] != command["owner_command_version_id"]
                or value["retained_checkpoint_ref"]["version_id"] != command["expected_parent_checkpoint_version_id"]
                or value["registered_completion_event_id"] != command["registered_completion_event_id"]):
            raise RegistryConflict("rejection replay changed immutable closure material")
