"""Exact material results on the original firing publication boundary.

The result is an OwnerControl resource in the original settlement transaction.
It never substitutes for a registered executor return, a normal completion, or
the kernel's unpublished rejection. All reads use the caller's SQLite cut.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict
from ..identities import TypedId
from ..models import PreparedObject
from ..schema_catalog import canonical_json
from .collaboration_descriptors import descriptor_store, exact_prepared
from .material_control import (
    OWNER_SCHEMA, _committed_descriptor, canonical_rows, control_body,
    read_accepted_material_command, read_accepted_material_input,
    read_committed_material_admission, row_ref, validate_active_target, validate_material_baseline,
)

RESULT_PROTOCOL = "rpnh/material_result/v1"
COMPLETION_EVENT = "registered_operation_completion_recorded/v1"


def material_admission(store, db, task_id, firing_ref, *, current=False):
    rows = db.execute(
        "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='firing_admission/v1' AND t.status='committed' AND e.task_id=? "
        "AND json_extract(o.metadata_json,'$.transition_firing_ref.version_id')=?",
        (str(task_id), firing_ref["version_id"])).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("material settlement requires one exact original admission")
    metadata = json.loads(rows[0]["metadata_json"])
    if metadata.get("owner_command_ref") is None:
        return None
    admission = read_committed_material_admission(store, db, task_id, rows[0])
    if admission["transition_firing_ref"] != firing_ref:
        raise RegistryConflict("material settlement firing differs from its admission")
    command = read_accepted_material_command(
        store, db, task_id, admission["owner_command_ref"], current=current)
    return admission, command


def completion_evidence(store, db, task_id, admission):
    firing_ref = admission["transition_firing_ref"]
    rows = db.execute(
        "SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
        "JOIN firing_temporary_members m ON m.member_kind='event' AND m.member_identity=e.event_id "
        "WHERE t.status='committed' AND e.task_id=? AND m.firing_version_id=? "
        "AND e.event_type=?", (str(task_id), firing_ref["version_id"], COMPLETION_EVENT)).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("material settlement requires its exact registered executor completion")
    event = store._row_to_envelope(rows[0])
    body = dict(event.payload)
    store.catalog.validate_instance(COMPLETION_EVENT, category="event", instance=body)
    for key in ("transition_firing_ref", "invocation_ref", "firing_admission_ref",
                "operation_execution_lease_ref", "admission_marking_checkpoint_ref"):
        if body[key] != admission[key]:
            raise RegistryConflict("material executor completion differs from its original admission")
    if str(event.producer_invocation_id) != admission["invocation_ref"]["logical_id"]:
        raise RegistryConflict("material executor completion has another exact producer")
    from .material_locations import is_location, location_bindings
    command = read_accepted_material_command(store, db, task_id,
        admission["owner_command_ref"], current=False)
    if is_location(command["data"]):
        location_bindings(store, db, task_id, admission, command, event)
        return event
    from .material_availability import is_availability, availability_completion
    if is_availability(command["data"]):
        availability_completion(store, db, task_id, admission, command, event)
        return event
    from .human_viewer import is_read_material, read_completion
    if is_read_material(command["data"]):
        read_completion(store, db, task_id, admission, command, event)
        return event
    from .material_purge import is_purge, purge_completion
    if is_purge(command["data"]):
        purge_completion(store, db, task_id, admission, command, event)
        return event
    candidate_ref = body["workspace_revision_candidate_ref"]
    if candidate_ref is None or not body["ordered_outputs"]:
        raise RegistryConflict("material settlement requires complete original workspace preparation")
    candidate_row = db.execute(
        "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN firing_temporary_members m ON m.member_kind='object' AND m.member_identity=o.version_id "
        "WHERE t.status='committed' AND m.firing_version_id=? AND o.object_type=? "
        "AND o.logical_id=? AND o.version_id=?", (firing_ref["version_id"],
        "workspace_revision_candidate/v1", candidate_ref["logical_id"], candidate_ref["version_id"])).fetchone()
    if candidate_row is None:
        raise RegistryConflict("material completion lacks its exact original workspace candidate")
    # A workspace candidate's registered payload is an archive, not a JSON
    # descriptor. Validate its original envelope and bytes without retyping it.
    candidate = json.loads(candidate_row["metadata_json"])
    prepared = PreparedObject(candidate_row["object_type"], TypedId.parse(candidate_row["logical_id"]),
        TypedId.parse(candidate_row["version_id"]), candidate_row["size"], candidate_row["media_type"],
        candidate_row["schema_ref"], TypedId.parse(candidate_row["producer_invocation_id"]),
        candidate_row["storage_locator"], candidate)
    original_store = descriptor_store(store)
    original_store.validate_envelope(prepared)
    store.catalog.validate_instance(prepared.object_type, category="object", instance=candidate)
    original_store.read_registered(prepared)
    if (candidate["workspace_revision_candidate_ref"] != candidate_ref
            or candidate["transition_firing_ref"] != firing_ref
            or candidate["invocation_ref"] != admission["invocation_ref"]
            or candidate["semantic_output_refs"] != [item["resource_ref"] for item in body["ordered_outputs"]]):
        raise RegistryConflict("material candidate differs from the exact registered executor return")
    return event


def semantic_bindings(store, db, task_id, admission, command, completion):
    """Exact command baseline and binding chain; promotion checks its live head."""
    from .material_locations import is_location, location_bindings
    if is_location(command["data"]):
        return location_bindings(store, db, task_id, admission, command, completion)
    from .material_purge import is_purge
    if is_purge(command["data"]):
        return []
    from .material_availability import is_availability
    if is_availability(command["data"]):
        # The caller's completion_evidence already verifies the entire exact
        # effect inventory. This operation publishes no address binding.
        return []
    from .human_viewer import is_read_material
    if is_read_material(command["data"]):
        # Its completed transport is proved by completion_evidence. A human
        # read has no address mutation or business output resource.
        return []
    rows = db.execute(
        "SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN firing_temporary_members m ON m.member_kind='object' AND m.member_identity=o.version_id "
        "WHERE t.status='committed' AND e.task_id=? AND m.firing_version_id=? "
        "AND o.object_type='resource_address_binding/v1' ORDER BY e.ordinal",
        (str(task_id), admission["transition_firing_ref"]["version_id"])).fetchall()
    selected = [(row_ref(row), _committed_descriptor(store, row)) for row in rows
        if {key: json.loads(row["metadata_json"])[key] for key in ("scope_ref", "opaque_name")}
        == command["data"]["address"]]
    expected = command["data"]["expected_binding_ref"]
    if not selected or selected[0][1]["previous_binding_ref"] != expected:
        raise RegistryConflict("material bindings differ from the immutable command baseline")
    for index, (reference, metadata) in enumerate(selected):
        if (metadata["lifecycle_state"] != "bound"
                or metadata["authorization_ref"] != completion.payload["operation_binding_ref"]
                or (index and metadata["previous_binding_ref"] != selected[index - 1][0])):
            raise RegistryConflict("material address binding chain is not exact")
    outputs = [item["resource_ref"] for item in completion.payload["ordered_outputs"]]
    if len(outputs) != 1 or selected[-1][1]["resource_ref"] != outputs[0]:
        raise RegistryConflict("material address does not name its one exact registered output")
    validate_material_baseline(store, db, task_id, command["data"])
    return [selected[-1][0]]


def result_lineage(data):
    refs = [data["command_ref"], data["firing_ref"]]
    if data["result_kind"] in ("saved", "location_changed", "versions_disabled", "read_completed", "purge_authorized", "carrier_purged"):
        refs += [data["operation_result_ref"], data["completion_ref"],
                 *data["binding_refs"]]
    else:
        refs.append(data["rejection_ref"])
    return refs


def _validate_result(store, db, task_id, result, body, closure, operation_result=None):
    data = body["data"]
    found = material_admission(store, db, task_id, data["firing_ref"])
    if found is None:
        raise RegistryConflict("material result cannot close an ordinary firing")
    admission, command = found
    completion = completion_evidence(store, db, task_id, admission)
    if (body["kind"] != "result" or body["command_id"] != command["command_id"]
            or body["source"] != command["source"] or body["target"] != command["target"]
            or data["command_ref"] != admission["owner_command_ref"]
            or body["lineage"] != result_lineage(data)):
        raise RegistryConflict("material result differs from its exact command and original facts")
    bootstrap = db.execute("SELECT value FROM registry_meta WHERE key='bootstrap_command_ref'").fetchone()
    if bootstrap is None or result.metadata.get("producer_ref") != json.loads(bootstrap[0]):
        raise RegistryConflict("material result is not an original Owner publication")
    from .material_locations import is_location
    location = is_location(command["data"])
    from .material_availability import is_metadata_operation, result_kind
    metadata_operation = is_metadata_operation(command["data"])
    if metadata_operation:
        from .material_locations import validate_location_control_schema
        validate_location_control_schema(store, db, task_id, result, body,
                                         command_ref=admission["owner_command_ref"])
    if data["result_kind"] in ("saved", "location_changed", "versions_disabled", "read_completed", "purge_authorized", "carrier_purged"):
        outputs = [{"entity_type": "resource_version/v1", "logical_id": item["resource_ref"]["resource_id"],
            "version_id": item["resource_ref"]["resource_version_id"]} for item in completion.payload["ordered_outputs"]]
        if (closure["firing_completion_ref"] != data["completion_ref"]
                or closure["transition_firing_ref"] != data["firing_ref"]
                or closure["invocation_ref"] != admission["invocation_ref"]
                or closure["operation_result_ref"] != data["operation_result_ref"]
                or data["result_kind"] != result_kind(command["data"])
                or closure["business_outcome"] != "completed"
                or (closure["workspace_revision_ref"] is None) != metadata_operation
                or (metadata_operation and closure["workspace_access_set_ref"] is not None)
                or operation_result is None
                or operation_result["operation_result_ref"] != data["operation_result_ref"]
                or operation_result["business_outcome"] != "completed"
                or operation_result["selected_outcome_id"] != completion.payload["selected_outcome_id"]
                or operation_result["invocation_ref"] != admission["invocation_ref"]
                or operation_result["transition_firing_ref"] != data["firing_ref"]
                or set(map(canonical_json, operation_result["output_resource_refs"])) != set(map(canonical_json, outputs))
                or data["output_refs"] != outputs
                or data["binding_refs"] != semantic_bindings(store, db, task_id, admission, command, completion)
                or data["error"] is not None):
            raise RegistryConflict("material saved result lacks its exact original Success closure")
    elif data["result_kind"] == "publish_rejected":
        if (closure["firing_rejection_ref"] != data["rejection_ref"]
                or closure["owner_command_ref"] != data["command_ref"]
                or closure["transition_firing_ref"] != data["firing_ref"]
                or closure["invocation_ref"] != admission["invocation_ref"]
                or closure["registered_completion_event_id"] != str(completion.event_id)
                or data["registered_completion_event_id"] != str(completion.event_id)
                or data["error"] != closure["reason"] or closure["disposition"] != "abandon"):
            raise RegistryConflict("material rejected result lacks its exact original rejection closure")
    else:
        raise RegistryConflict("material result kind is unsupported")
    return admission, command


def validate_material_results(store, db, *, task_id, objects, events, idempotency_key):
    """Validate reserved result bodies before granting scoped publication keys."""
    del events
    results = [(item, control_body(store, item)) for item in objects
        if item.object_type == "resource_version/v1" and item.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    results = [(item, body) for item, body in results
        if body["data"].get("protocol") == RESULT_PROTOCOL
        and body["data"].get("result_kind") != "not_admitted"]
    matched, keys = set(), {}
    by_ref = {(item.object_type, str(item.logical_id), str(item.version_id)): item for item in objects}
    for closure in objects:
        if closure.object_type not in ("firing_completion/v2", "firing_rejection/v1"):
            continue
        found = material_admission(store, db, task_id, closure.metadata["transition_firing_ref"])
        if found is None:
            continue
        admission, command = found
        from .material_locations import is_location
        from .material_availability import result_kind
        kind = (result_kind(command["data"])
                if closure.object_type == "firing_completion/v2" else "publish_rejected")
        matches = [(item, body) for item, body in results
            if body["data"]["firing_ref"] == admission["transition_firing_ref"]
            and body["data"]["result_kind"] == kind]
        if len(matches) != 1:
            raise RegistryConflict("material settlement requires one same-transaction Owner result")
        result, body = matches[0]
        from .human_viewer import is_read_material
        if is_location(command["data"]) or is_read_material(command["data"]):
            if any((item.object_type == "resource_version/v1" and item is not result)
                   or item.object_type in {"resource_address_binding/v1", "workspace_revision/v1",
                       "workspace_revision_candidate/v1", "execution_instance/v1"}
                   for item in objects):
                raise RegistryConflict("empty material settlement cannot add a body or another prepared mutation")
        operation_result = None
        if kind in ("saved", "location_changed", "versions_disabled", "read_completed", "purge_authorized", "carrier_purged"):
            reference = body["data"]["operation_result_ref"]
            original = by_ref.get(tuple(reference[key] for key in ("entity_type", "logical_id", "version_id")))
            operation_result = None if original is None else original.metadata
        admission, command = _validate_result(
            store, db, task_id, result, body, closure.metadata, operation_result)
        if closure.object_type == "firing_completion/v2":
            _validate_current_success_authority(store, db, task_id, admission, command)
        matched.add(str(result.version_id))
        keys[str(result.version_id)] = ("owner:" + command["command_id"] + ":result:material-result:" + idempotency_key,
            (command["data"]["execution"]["task_round_ref"]["logical_id"],
             command["data"]["execution"]["net_instance_ref"]["logical_id"]))
    if matched != {str(item.version_id) for item, _body in results}:
        raise RegistryConflict("material result has no same-transaction original settlement")
    return keys


def _validate_current_success_authority(store, db, task_id, admission, command):
    """Unconditional pre-INSERT gate in the original BEGIN IMMEDIATE."""
    current = read_accepted_material_command(
        store, db, task_id, admission["owner_command_ref"], current=True)
    if current != command:
        raise RegistryConflict("material Success changed its original accepted command")
    params = {key: value for key, value in command["data"].items() if key != "user_action"}
    params["target"] = command["target"]
    validate_active_target(store, db, task_id, params, command["source"])
    from .material_locations import is_location
    from .material_availability import is_metadata_operation, is_availability, validate_availability_promotion
    if not is_metadata_operation(command["data"]):
        read_accepted_material_input(store, db, task_id, command["data"]["input_ref"], current=True)
    if is_availability(command["data"]):
        validate_availability_promotion(store, db, task_id, admission, command)
    from .material_purge import is_purge, validate_purge_promotion
    if is_purge(command["data"]):
        validate_purge_promotion(store, db, task_id, admission, command)


def validate_material_success_at_promotion(store, db, *, task_id, root, publication, transaction_id):
    """Check final identities without reinterpreting intermediate stream heads.

    validate_material_results unconditionally checks the full original proof,
    current authority and drain before INSERT, under this same write lock.
    Original immutable rows cannot change in the intervening append. The late
    original publication gate retains its writer, root and member checks.
    """
    rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE o.object_type='firing_admission/v1' AND t.task_id=? AND t.status='committed' "
        "AND json_extract(o.metadata_json,'$.transition_firing_ref.version_id')=?",
        (str(task_id), root["firing_version_id"])).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("material promotion lost its exact original admission")
    admission = json.loads(rows[0]["metadata_json"])
    command_ref = admission.get("owner_command_ref")
    if command_ref is None:
        return
    command_row = db.execute("SELECT * FROM objects WHERE object_type='resource_version/v1' "
        "AND logical_id=? AND version_id=?", (command_ref["logical_id"], command_ref["version_id"])).fetchone()
    if command_row is None:
        raise RegistryConflict("material promotion lost its exact original command")
    metadata = json.loads(command_row["metadata_json"])
    command_id = metadata.get("descriptors", {}).get("command_id")
    if (command_id is None
            or admission["transition_firing_ref"] != {
                "entity_type": "transition_firing/v1", "logical_id": root["firing_logical_id"],
                "version_id": root["firing_version_id"]}
            or admission["invocation_ref"]["logical_id"] != root["invocation_logical_id"]):
        raise RegistryConflict("material promotion changed its original command or firing")
    rows = db.execute("SELECT o.* FROM objects o WHERE o.transaction_id=? "
        "AND o.object_type='resource_version/v1' "
        "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
        "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='result' "
        "AND json_extract(o.metadata_json,'$.descriptors.command_id')=?",
        (str(transaction_id), OWNER_SCHEMA, command_id)).fetchall()
    if len(rows) != 1 or publication["invocation_version_id"] != admission["invocation_ref"]["version_id"]:
        raise RegistryConflict("material promotion lost its exact same-transaction result")


def terminal_result(store, db, task_id, admission, command, root):
    """Historical receipts require the whole canonical closure at one cut."""
    rows = canonical_rows(store, db, task_id, conditions=
        "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
        "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='result' "
        "AND json_extract(o.metadata_json,'$.descriptors.command_id')=?",
        parameters=(OWNER_SCHEMA, command["command_id"]))
    rows = [row for row in rows if row["transaction_id"] == root["published_transaction_id"]]
    if len(rows) != 1:
        raise RegistryConflict("material terminal root lacks its canonical same-transaction result")
    result = exact_prepared(db, descriptor_store(store), task_id, row_ref(rows[0]))
    body = control_body(store, result)
    data = body["data"]
    if data.get("protocol") != RESULT_PROTOCOL:
        raise RegistryConflict("material terminal result protocol differs")
    from .material_locations import is_location
    published = root["state"] == "PUBLISHED"
    from .material_availability import result_kind
    wanted = result_kind(command["data"]) if published else "publish_rejected"
    if data["result_kind"] != wanted:
        raise RegistryConflict("material terminal result differs from publication state")
    reference = data["completion_ref"] if published else data["rejection_ref"]
    closure = exact_prepared(db, descriptor_store(store), task_id, reference)
    operation_result = (exact_prepared(db, descriptor_store(store), task_id,
        data["operation_result_ref"]).metadata if published else None)
    _validate_result(store, db, task_id, result, body, closure.metadata, operation_result)
    for same_transaction in (reference, *([data["operation_result_ref"]] if published else [])):
        original = db.execute("SELECT transaction_id FROM objects WHERE version_id=?",
                              (same_transaction["version_id"],)).fetchone()
        if original is None or original["transaction_id"] != root["published_transaction_id"]:
            raise RegistryConflict("material terminal facts do not share the original settlement transaction")
    for reference in (reference, *data.get("output_refs", ()), *data.get("binding_refs", ())):
        exact_prepared(db, descriptor_store(store), task_id, reference)
    if (published and (root["operation_result_version_id"] != data["operation_result_ref"]["version_id"]
            or root["marking_checkpoint_version_id"] != closure.metadata["successor_checkpoint_ref"]["version_id"])):
        raise RegistryConflict("material result differs from the original published root")
    return row_ref(rows[0]), data
