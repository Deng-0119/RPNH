"""Material input and command authority at the original Registry write cut.

The existing objects, transactions and HOST session facts remain authoritative.
No material queue, content identifier or independent command store is introduced.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from ..event_store import RegistryConflict
from ..identities import TypedId
from ..models import PreparedObject
from ..schema_catalog import canonical_json
from ..strict_contracts import ref_payload
from .collaboration_descriptors import (
    descriptor_store, exact_prepared, exact_descriptor, readable_payload, readable_descriptor,
)

OWNER_SCHEMA = "rpnh/owner_control/v1"
TEXT_SCHEMA = "application/rpnh_agent_text/v1"
INPUT_PROTOCOL = "rpnh/material_input/v1"
COMMAND_PROTOCOL = "rpnh/material_command/v1"
INPUT_ROLE = "viewer_material"


def reference(value, kind):
    if (not isinstance(value, Mapping)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or value["entity_type"] != kind):
        raise RegistryConflict("material requires an exact " + kind)
    TypedId.parse(value["logical_id"])
    TypedId.parse(value["version_id"])
    return dict(value)


def require_session(store, db, task_id, session):
    from ..user_collaboration import check_session, SESSION, PROJECT
    from .user_collaboration import exact, facts, require_owner
    check_session(store, session)
    document = exact(store, db, task_id, ref_payload(session.ref), SESSION)
    if (document["data"]["actor"] != session.actor
            or document["data"]["owner"] is not True):
        raise RegistryConflict("ACTOR_NOT_AUTHORIZED: material requires the target owner session")
    for project in facts(store, db, task_id, PROJECT):
        require_owner(store, db, task_id, project, document)
    return document


def _historical_actor(store, db, task_id, material, *, current):
    from ..user_collaboration import SESSION, PROJECT
    from .user_collaboration import exact, facts, require_owner
    if (not isinstance(material, dict)
            or set(material) != {"session_ref", "actor", "kind", "action_ref"}):
        raise RegistryConflict("material lacks its original HOST action")
    session = exact(store, db, task_id,
        reference(material["session_ref"], SESSION), SESSION)
    if session["data"]["actor"] != material["actor"] or session["data"]["owner"] is not True:
        raise RegistryConflict("ACTOR_NOT_AUTHORIZED: original material actor is not the owner")
    if current:
        for project in facts(store, db, task_id, PROJECT):
            try:
                require_owner(store, db, task_id, project, session)
            except RegistryConflict as exc:
                if str(exc) not in {"collaboration requires the target owner A",
                                    "collaboration owner authority is revoked"}:
                    raise
                raise RegistryConflict("ACTOR_NOT_AUTHORIZED: original owner authority is no longer valid") from exc
    return session


def _original_action(db, prepared, expected_kind):
    row = db.execute(
        "SELECT t.idempotency_key,t.command_json,t.status FROM objects o "
        "JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE o.version_id=?", (str(prepared.version_id),)).fetchone()
    if row is None or row["status"] != "committed":
        raise RegistryConflict("material lacks its original committed transaction")
    material = json.loads(row["command_json"]).get("user_collaboration")
    if not isinstance(material, dict) or material.get("kind") != expected_kind:
        raise RegistryConflict("material was not accepted through its trusted HOST action")
    return row["idempotency_key"], material


def control_body(store, prepared):
    if (prepared.object_type != "resource_version/v1"
            or prepared.metadata.get("content_schema_ref") != OWNER_SCHEMA):
        raise RegistryConflict("material control is not an original OwnerControl resource")
    body = json.loads(readable_payload(descriptor_store(store), prepared,
        media_type="application/json"))
    store.catalog.validate_schema_ref(OWNER_SCHEMA, body)
    descriptors = prepared.metadata.get("descriptors", {})
    if (descriptors.get("owner_control_kind") != body["kind"]
            or descriptors.get("command_id") != body["command_id"]
            or prepared.producer_invocation_id is not None):
        raise RegistryConflict("material control descriptors differ from its exact content")
    return body


def canonical_rows(store, db, task_id, *, object_type="resource_version/v1",
                   conditions="", parameters=()):
    """Narrow SQL first, then exact publication checks, all at one read cut."""
    upper = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
    canonical = store._canonical_member_sql(member_kind="object",
        member_identity_sql="o.version_id", published_event_sql="e.ordinal")
    return db.execute(
        "SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "WHERE " + canonical + " AND o.object_type=? AND e.task_id=? "
        + conditions + " ORDER BY e.ordinal",
        (*((upper,) * 4), object_type, str(task_id), *parameters)).fetchall()


def row_ref(row):
    return {"entity_type": row["object_type"], "logical_id": row["logical_id"],
            "version_id": row["version_id"]}


def _committed_descriptor(store, row):
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"],
        row["schema_ref"], (TypedId.parse(row["producer_invocation_id"])
            if row["producer_invocation_id"] else None), row["storage_locator"],
        json.loads(row["metadata_json"]))
    descriptor_store(store).validate_envelope(prepared)
    store.catalog.validate_instance(prepared.object_type, category="object",
                                     instance=prepared.metadata)
    return readable_descriptor(descriptor_store(store), prepared)


def read_committed_material_admission(store, db, task_id, row, command_ref=None):
    """Read original firing authority, including its still-provisional controls.

    This never promotes provisional products into the canonical business view.
    The committed admission event and original firing membership are required.
    """
    admission = _committed_descriptor(store, row)
    if row["object_type"] != "firing_admission/v1":
        raise RegistryConflict("material admission must be an original firing admission")
    record = store.ordered_firing_record(
        admission["transition_firing_ref"]["version_id"], _db=db)
    matching = [event for event in record["events"]
                if event.event_type == "firing_admitted/v1"
                and event.payload.get("firing_admission_ref") == row_ref(row)
                and event.payload.get("transition_firing_ref") == admission["transition_firing_ref"]
                and event.payload.get("invocation_ref") == admission["invocation_ref"]
                and str(event.transaction_id) == row["transaction_id"]]
    invocation_ref = reference(admission["invocation_ref"], "invocation/v1")
    invocation_row = db.execute(
        "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='invocation/v1' AND o.logical_id=? AND o.version_id=? "
        "AND t.status='committed' AND e.task_id=?",
        (invocation_ref["logical_id"], invocation_ref["version_id"], str(task_id))).fetchone()
    if invocation_row is None:
        raise RegistryConflict("material admission lacks its original committed invocation")
    invocation = _committed_descriptor(store, invocation_row)
    if (len(matching) != 1 or matching[0].task_id != task_id
            or record["firing"]["firing_admission_ref"] != row_ref(row)
            or invocation.get("invocation_ref") != invocation_ref
            or invocation.get("own_transition_firing_ref") != admission["transition_firing_ref"]
            or invocation_row["transaction_id"] != row["transaction_id"]
            or ("object", row["version_id"]) not in record["temporary_members"]
            or ("object", invocation_ref["version_id"]) not in record["temporary_members"]
            or (command_ref is not None and admission.get("owner_command_ref") != command_ref)):
        raise RegistryConflict("material admission lacks its exact original firing closure")
    return admission


def validate_material_input_consumer(store, db, task_id, input_ref, invocation_ref):
    """Restrict a prepared input body to its command's original invocation.

    Called both before delivery and at the original prepare/release write cut.
    Existing grants remain necessary but cannot widen this one-command scope.
    """
    row = db.execute("SELECT metadata_json FROM objects WHERE object_type='resource_version/v1' "
        "AND logical_id=? AND version_id=?",
        (input_ref["logical_id"], input_ref["version_id"])).fetchone()
    if row is None or json.loads(row["metadata_json"]).get("descriptors", {}).get("owner_input_role") != INPUT_ROLE:
        return
    reference(invocation_ref, "invocation/v1")
    read_accepted_material_input(store, db, task_id, input_ref, current=True)
    rows = db.execute(
        "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
        "JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='firing_admission/v1' AND t.status='committed' AND e.task_id=? "
        "AND json_extract(o.metadata_json,'$.invocation_ref.version_id')=?",
        (str(task_id), invocation_ref["version_id"])).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("INPUT_UNAVAILABLE: input requires its exact command admission")
    admission = read_committed_material_admission(store, db, task_id, rows[0])
    command_ref = admission.get("owner_command_ref")
    if command_ref is None or admission["invocation_ref"] != invocation_ref:
        raise RegistryConflict("INPUT_UNAVAILABLE: invocation has no matching material command")
    command = read_accepted_material_command(store, db, task_id, command_ref)
    if command["data"]["input_ref"] != input_ref:
        raise RegistryConflict("INPUT_UNAVAILABLE: input belongs to another material command")
    starts = db.execute(
        "SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
        "WHERE e.event_type='operation_execution_started/v1' AND e.task_id=? "
        "AND t.status='committed' AND json_extract(e.payload_json,'$.invocation_ref.version_id')=?",
        (str(task_id), invocation_ref["version_id"])).fetchall()
    if len(starts) != 1:
        raise RegistryConflict("INPUT_UNAVAILABLE: material input requires its actual registered Start")
    start = json.loads(starts[0]["payload_json"])
    if (start["invocation_ref"] != invocation_ref
            or start["transition_firing_ref"] != admission["transition_firing_ref"]):
        raise RegistryConflict("INPUT_UNAVAILABLE: material Start differs from its command admission")
    from ..operation_execution import revalidate_started_operation_at
    revalidate_started_operation_at(store, db, task_id=task_id,
                                   start_event_id=starts[0]["event_id"])


def read_accepted_material_input(store, db, task_id, input_ref, *, current=False):
    prepared = exact_prepared(db, descriptor_store(store), task_id,
        reference(input_ref, "resource_version/v1"))
    meta = prepared.metadata
    descriptors = meta.get("descriptors", {})
    if (meta.get("content_schema_ref") != TEXT_SCHEMA
            or meta.get("media_type") != "application/json"
            or meta.get("origin_kind") != "private_system"
            or descriptors.get("owner_input_role") != INPUT_ROLE):
        raise RegistryConflict("INPUT_UNAVAILABLE: not an accepted complete material input")
    key, action = _original_action(db, prepared, "prepare_material_input")
    session = _historical_actor(store, db, task_id, action, current=current)
    if (key != descriptors.get("owner_input_command_id")
            or action["actor"] != descriptors.get("owner_input_actor")
            or action["session_ref"]["version_id"] != descriptors.get("owner_input_session_version_id")
            or meta["task_ref"] != session["owner_task_ref"]):
        raise RegistryConflict("material input differs from its original preparation")
    if current:
        from .material_input_lifecycle import assert_available
        assert_available(store, db, task_id, input_ref)
    return prepared


def read_accepted_material_command(store, db, task_id, command_ref, *, current=True):
    prepared = exact_prepared(db, descriptor_store(store), task_id,
        reference(command_ref, "resource_version/v1"))
    body = control_body(store, prepared)
    data = body["data"]
    if body["kind"] != "command" or data.get("protocol") != COMMAND_PROTOCOL:
        raise RegistryConflict("reference is not an accepted material command")
    key, action = _original_action(db, prepared, "material_submit")
    session = _historical_actor(store, db, task_id, action, current=current)
    if (key != body["command_id"] or data.get("user_action") != action
            or body["source"] != session["data"]["target_principal_ref"]
            or data["execution"]["task_ref"] != session["owner_task_ref"]
            or prepared.metadata["task_ref"] != session["owner_task_ref"]):
        raise RegistryConflict("material command differs from original actor or owner scope")
    from .material_locations import is_location, command_descriptors, command_lineage
    params = {name: value for name, value in data.items() if name != "user_action"}
    params["target"] = body["target"]
    validate_submit_params(params)
    if (prepared.metadata.get("descriptors", {}) != command_descriptors(key, data)
            or body["lineage"] != command_lineage(data)):
        raise RegistryConflict("material command index fields differ from its exact request")
    from .material_availability import is_metadata_operation
    if is_metadata_operation(data):
        from .material_locations import validate_location_control_schema
        validate_location_control_schema(store, db, task_id, prepared, body)
        from .human_viewer import is_read_material, validate_read_baseline
        if is_read_material(data):
            validate_read_baseline(store, db, task_id, data, current=current)
        else:
            validate_material_baseline(store, db, task_id, data)
        return body
    material = read_accepted_material_input(store, db, task_id, data["input_ref"], current=current)
    _, original = _original_action(db, material, "prepare_material_input")
    if (original["actor"] != action["actor"]
            or original["session_ref"] != action["session_ref"]):
        raise RegistryConflict("material input belongs to another preparation actor")
    return body


def validate_input_params(params):
    if (not isinstance(params, dict)
            or set(params) != {"protocol", "document_type", "value"}
            or params["protocol"] != INPUT_PROTOCOL
            or params["document_type"] != TEXT_SCHEMA
            or not isinstance(params["value"], str)):
        raise RegistryConflict("INVALID_COMMAND: expected one complete registered Agent text value")
    # Reject malformed Unicode before either the immutable store or editor
    # materialization can observe a different representation.
    params["value"].encode("utf-8")


def validate_submit_params(params):
    from .material_purge import is_purge, validate_purge_params
    if isinstance(params, dict) and is_purge(params):
        validate_purge_params(params)
        return
    from .human_viewer import is_read_material, validate_read_params
    if isinstance(params, dict) and is_read_material(params):
        validate_read_params(params)
        return
    from .material_availability import is_availability, validate_availability_params
    if isinstance(params, dict) and is_availability(params):
        validate_availability_params(params)
        return
    from .material_locations import is_location, validate_location_params
    if isinstance(params, dict) and is_location(params):
        validate_location_params(params)
        return
    required = {"protocol", "operation", "target", "execution", "address",
                "expected_binding_ref", "base_resource_ref", "input_ref"}
    if (not isinstance(params, dict) or set(params) != required
            or params["protocol"] != COMMAND_PROTOCOL
            or params["operation"] not in ("create_text", "replace_text")
            or not isinstance(params["target"], str) or not params["target"]):
        raise RegistryConflict("INVALID_COMMAND: unsupported material operation or fields")
    execution = params["execution"]
    if not isinstance(execution, dict) or set(execution) != {"task_ref", "task_round_ref", "net_instance_ref"}:
        raise RegistryConflict("INVALID_COMMAND: exact execution scope is required")
    for key, kind in (("task_ref", "task/v1"), ("task_round_ref", "task_round/v1"),
                      ("net_instance_ref", "net_instance/v1")):
        reference(execution[key], kind)
    address = params["address"]
    if (not isinstance(address, dict) or set(address) != {"scope_ref", "opaque_name"}
            or address["scope_ref"] != execution["task_ref"]
            or not isinstance(address["opaque_name"], str) or not address["opaque_name"]
            or "\x00" in address["opaque_name"]):
        raise RegistryConflict("SCOPE_NOT_ALLOWED: material requires an explicit task address")
    reference(params["input_ref"], "resource_version/v1")
    expected, base = params["expected_binding_ref"], params["base_resource_ref"]
    if expected is not None:
        reference(expected, "resource_address_binding/v1")
    if base is not None:
        reference(base, "resource_version/v1")
    if (params["operation"] == "create_text" and base is not None
            or params["operation"] == "replace_text" and (expected is None or base is None)):
        raise RegistryConflict("INVALID_COMMAND: create and replace have different exact baselines")


def validate_active_target(store, db, task_id, params, source):
    """Recheck actual run, adopted net and operation at this original cut."""
    rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not rows or len({row["logical_id"] for row in rows}) != 1:
        raise RegistryConflict("EXECUTION_CHANGED: no unique current run")
    obj_store = descriptor_store(store)
    run = exact_descriptor(db, obj_store, task_id, row_ref(rows[-1]))
    terminal = run["status"] == "terminal" and run.get("terminal_evidence_ref") is not None
    if not terminal and (run["status"] != "running" or run.get("terminal_evidence_ref") is not None):
        raise RegistryConflict("TARGET_UNAVAILABLE: terminal material management is not supported")
    checkpoint = exact_descriptor(db, obj_store, task_id, run["latest_checkpoint_ref"])
    execution = params["execution"]
    if (execution["task_ref"] != run["task_ref"]
            or execution["net_instance_ref"] != checkpoint["net_instance_ref"]):
        raise RegistryConflict("EXECUTION_CHANGED: command names another adopted execution")
    net = exact_descriptor(db, obj_store, task_id, execution["net_instance_ref"])
    root = exact_descriptor(db, obj_store, task_id, net["team_design_root_ref"])
    if (execution["task_round_ref"] != net["task_round_ref"]
            or root["owner_principal_ref"] != source):
        raise RegistryConflict("EXECUTION_CHANGED: command owner or round differs")
    candidates = [exact_descriptor(db, obj_store, task_id, ref)
                  for ref in net["executable_transition_binding_refs"]]
    candidates = [item for item in candidates if item["transition_id"] == params["target"]]
    if len(candidates) != 1 or candidates[0].get("agent_ref") is None:
        raise RegistryConflict("TARGET_UNAVAILABLE: exact management agent is not registered")
    binding = exact_descriptor(db, obj_store, task_id, candidates[0]["operation_binding_ref"])
    spec = exact_descriptor(db, obj_store, task_id, binding["operation_spec_ref"])
    if (spec.get("implementation_contracts", {}).get("owner_material_commands") != COMMAND_PROTOCOL
            or spec.get("transport") != "deterministic"):
        raise RegistryConflict("TARGET_UNAVAILABLE: operation does not accept material commands")
    from .material_locations import is_location, validate_location_spec
    from .material_availability import is_availability, validate_availability_spec
    from .human_viewer import is_read_material, validate_read_spec
    from .material_purge import is_purge, validate_purge_spec
    if is_purge(params):
        validate_purge_spec(spec, binding, operation=params["operation"])
    elif is_availability(params):
        validate_availability_spec(spec, binding)
    elif is_read_material(params):
        validate_read_spec(spec, binding)
    elif is_location(params):
        validate_location_spec(spec, binding)
    elif "owner_material_purge" in spec.get("implementation_contracts", {}):
        raise RegistryConflict("TARGET_UNAVAILABLE: purge operation cannot prepare text")
    elif "owner_material_reads" in spec.get("implementation_contracts", {}):
        raise RegistryConflict("TARGET_UNAVAILABLE: human read operation cannot prepare text")
    elif "owner_material_availability" in spec.get("implementation_contracts", {}):
        raise RegistryConflict("TARGET_UNAVAILABLE: availability operation cannot prepare text")
    elif "owner_location_actions" in spec.get("implementation_contracts", {}):
        raise RegistryConflict("TARGET_UNAVAILABLE: location operation cannot prepare text")
    if terminal:
        from .terminal_management import terminal_management_scope
        terminal_management_scope(store, db, task_id, target=params["target"],
            operation=params.get("operation"), source=source, execution=execution)
    return candidates[0]


def validate_material_baseline(store, db, task_id, params):
    """Check the supplied immutable baseline without refreshing its live head."""
    from .material_purge import is_purge, validate_purge_baseline
    if is_purge(params):
        validate_purge_baseline(store, db, task_id, params)
        return
    from .human_viewer import is_read_material, validate_read_baseline
    if is_read_material(params):
        validate_read_baseline(store, db, task_id, params)
        return
    from .material_availability import is_availability, validate_availability_baseline
    if is_availability(params):
        validate_availability_baseline(store, db, task_id, params)
        return
    from .material_locations import is_location, validate_location_baseline
    if is_location(params):
        validate_location_baseline(store, db, task_id, params)
        return
    expected, base = params["expected_binding_ref"], params["base_resource_ref"]
    if expected is None:
        return  # The closed request schema permits this only for creation.
    binding = exact_prepared(db, descriptor_store(store), task_id,
                             reference(expected, "resource_address_binding/v1")).metadata
    if {key: binding[key] for key in ("scope_ref", "opaque_name")} != params["address"]:
        raise RegistryConflict("INVALID_COMMAND: material baseline names another address")
    if params["operation"] == "create_text":
        if binding["lifecycle_state"] != "tombstoned" or base is not None:
            raise RegistryConflict("INVALID_COMMAND: create cannot replace a live material binding")
    else:
        if (base is None or binding["lifecycle_state"] != "bound"
                or binding["resource_ref"] != {"resource_id": base["logical_id"],
                                               "resource_version_id": base["version_id"]}):
            raise RegistryConflict("INVALID_COMMAND: replacement base differs from the exact bound version")
        original = exact_prepared(db, descriptor_store(store), task_id,
                                 reference(base, "resource_version/v1"))
        if (original.metadata.get("content_schema_ref") != TEXT_SCHEMA
                or original.media_type != "application/json"):
            raise RegistryConflict("INVALID_COMMAND: replacement base is outside the original text contract")


def validate_material_control(store, db, *, task_id, idempotency_key, objects, action, existing):
    """Return True only for an original material preparation/submission."""
    inputs = [obj for obj in objects if obj.object_type == "resource_version/v1"
              and obj.metadata.get("descriptors", {}).get("owner_input_role") == INPUT_ROLE]
    controls = [(obj, control_body(store, obj)) for obj in objects
                if obj.object_type == "resource_version/v1"
                and obj.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    commands = [(obj, body) for obj, body in controls
                if body["data"].get("protocol") == COMMAND_PROTOCOL]
    input_receipts = [(obj, body) for obj, body in controls
                      if body["data"].get("protocol") == "rpnh/material_input_receipt/v1"]
    material_action = action is not None and action.kind in ("prepare_material_input", "material_submit")
    if not inputs and not commands and not input_receipts and not material_action:
        return False
    from ..user_collaboration import check_handle
    from .user_collaboration import check_replay
    check_handle(store, action)
    session = require_session(store, db, task_id, action.session)
    if action.kind not in ("prepare_material_input", "material_submit") or action.creation is None:
        raise RegistryConflict("material requires its sealed complete HOST request")
    request = json.loads(action.creation)
    if (set(request) != {"owner_command", "command_id", "params"}
            or request["owner_command"] != action.kind or request["command_id"] != idempotency_key):
        raise RegistryConflict("material action differs from the original command")
    if existing is not None:
        check_replay(store, db, task_id, existing, action)
        return True
    params = request["params"]
    if action.kind == "prepare_material_input":
        validate_input_params(params)
        if (len(inputs) != 1 or commands or len(objects) != 2
                or len(controls) != 1 or len(input_receipts) != 1):
            raise RegistryConflict("material preparation requires one atomic input and receipt")
        prepared, = inputs
        desc = prepared.metadata["descriptors"]
        if (desc != {"owner_input_command_id": idempotency_key,
                     "owner_input_role": INPUT_ROLE, "owner_input_actor": action.session.actor,
                     "owner_input_session_version_id": str(action.session.ref.version_id)}
                or prepared.metadata.get("content_schema_ref") != TEXT_SCHEMA
                or prepared.metadata.get("origin_kind") != "private_system"
                or prepared.metadata.get("task_ref") != session["owner_task_ref"]
                or descriptor_store(store).read_registered(prepared) != canonical_json(params["value"])):
            raise RegistryConflict("material input differs from the exact frozen HOST input")
        input_ref = {"entity_type": "resource_version/v1",
                     "logical_id": str(prepared.logical_id), "version_id": str(prepared.version_id)}
        _receipt_obj, receipt = controls[0]
        expected = {"protocol": "rpnh/material_input_receipt/v1",
                    "input_command_id": idempotency_key, "input_ref": input_ref,
                    "status": "INPUT_ACCEPTED"}
        if (receipt["kind"] != "receipt" or receipt["command_id"] != idempotency_key
                or receipt["source"] != session["data"]["target_principal_ref"]
                or receipt["target"] is not None or receipt["data"] != expected
                or receipt["lineage"] != [input_ref]):
            raise RegistryConflict("material preparation receipt is not atomic with its input")
        return True
    validate_submit_params(params)
    if inputs or len(commands) != 1 or len(objects) != 1:
        raise RegistryConflict("material submission requires one original command resource")
    command_object, body = commands[0]
    from .material_locations import is_location, command_lineage, command_descriptors
    expected_data = {key: value for key, value in params.items() if key != "target"}
    expected_data["user_action"] = action.material()
    if (body["kind"] != "command" or body["command_id"] != idempotency_key
            or body["target"] != params["target"] or body["data"] != expected_data
            or body["source"] != session["data"]["target_principal_ref"]
            or body["lineage"] != command_lineage(params)):
        raise RegistryConflict("material command differs from the exact frozen HOST request")
    if command_object.metadata.get("descriptors") != command_descriptors(idempotency_key, params):
        raise RegistryConflict("material command lookup fields differ from its exact request")
    from .material_availability import is_metadata_operation, is_availability, validate_availability_baseline
    if is_metadata_operation(params):
        from .material_locations import validate_location_control_schema
        validate_location_control_schema(store, db, task_id, command_object, body)
        validate_active_target(store, db, task_id, params, body["source"])
        validate_material_baseline(store, db, task_id, params)
        from .material_purge import is_purge, validate_purge_baseline
        if is_purge(params):
            validate_purge_baseline(store, db, task_id, expected_data, current=True)
        if is_availability(params):
            validate_availability_baseline(store, db, task_id, expected_data, current=True)
        from .human_viewer import is_read_material, validate_read_baseline
        if is_read_material(params):
            validate_read_baseline(store, db, task_id, expected_data, current=True)
        return True
    prepared_input = read_accepted_material_input(store, db, task_id, params["input_ref"], current=True)
    _, input_action = _original_action(db, prepared_input, "prepare_material_input")
    if (input_action["actor"] != action.session.actor
            or input_action["session_ref"] != ref_payload(action.session.ref)):
        raise RegistryConflict("INPUT_UNAVAILABLE: material input belongs to another actor")
    validate_active_target(store, db, task_id, params, body["source"])
    validate_material_baseline(store, db, task_id, params)
    # A role-specific index over the original canonical controls is enough.
    # Inputs cannot be reused for a second command, even with identical text.
    prior = canonical_rows(store, db, task_id, conditions=
        "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
        "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='command' "
        "AND json_extract(o.metadata_json,'$.descriptors.owner_material_input_version_id')=?",
        parameters=(OWNER_SCHEMA, params["input_ref"]["version_id"]))
    for row in prior:
        candidate = control_body(store, exact_prepared(db, descriptor_store(store), task_id, row_ref(row)))
        if (candidate["data"].get("protocol") == COMMAND_PROTOCOL
                and candidate["data"].get("input_ref") == params["input_ref"]):
            raise RegistryConflict("INPUT_UNAVAILABLE: input already belongs to a material command")
    return True

