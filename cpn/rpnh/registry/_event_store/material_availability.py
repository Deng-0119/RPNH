"""Current material availability from original command/output/Success facts.

No independently writable state, cache, lifecycle table or alternate Registry
is authoritative here. The ordinary effect output is meaningful only together
with its exact trusted command and original registered operation closure.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from ..event_store import RegistryConflict, RegistryCorruptError
from ..schema_catalog import canonical_json, SchemaGovernanceError
from ..object_store import ObjectIntegrityError
from .collaboration_descriptors import descriptor_store, exact_prepared, exact_descriptor


PROTOCOL = "rpnh/material_availability_effect/v1"
EFFECT_SCHEMA = "application/rpnh_material_availability_effect/v1"
PLAN_PROTOCOL = "rpnh/material_disable_plan/v1"
PLAN_SCHEMA = "application/rpnh_material_disable_plan/v1"
PLAN_ACTION = "prepare_material_disable_plan"
PLAN_ROLE = "viewer_material_disable_plan"
EXECUTOR = "rpnh/material-availability/v1"
IDENTITY = {"implementation_id": "rpnh.material_availability", "revision": "v1"}
CONFIG_SCHEMA = "application/rpnh_material_availability_config/v1"
CONTRACTS = {"transport": "deterministic", "input_ports": None, "output_ports": None,
    "config_schema": CONFIG_SCHEMA, "owner_material_commands": "rpnh/material_command/v1",
    "owner_material_availability": PROTOCOL}


def is_availability(data):
    return data.get("operation") == "disable_versions"


def is_metadata_operation(data):
    from .material_locations import is_location
    from .human_viewer import is_read_material
    from .material_purge import is_purge
    return is_location(data) or is_availability(data) or is_read_material(data) or is_purge(data)


def result_kind(data):
    from .material_locations import is_location
    from .human_viewer import is_read_material
    from .material_purge import is_purge, purge_result_kind
    if is_purge(data):
        return purge_result_kind(data)
    return ("versions_disabled" if is_availability(data) else "read_completed" if is_read_material(data)
            else "location_changed" if is_location(data) else "saved")


def exact_ref(value):
    from .material_control import reference
    from ..strict_contracts import ref_payload
    if hasattr(value, "as_version_ref"):
        value = value.as_version_ref()
    if not isinstance(value, Mapping):
        value = ref_payload(value)
    if "resource_id" in value:
        value = {"entity_type": "resource_version/v1", "logical_id": value["resource_id"],
                 "version_id": value["resource_version_id"]}
    return reference(dict(value), "resource_version/v1")


def _targets(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise RegistryConflict("INVALID_COMMAND: disable requires 1 to 64 exact versions")
    values = [exact_ref(value) for value in values]
    if len({canonical_json(ref) for ref in values}) != len(values):
        raise RegistryConflict("INVALID_COMMAND: disable targets must be unique exact versions")
    return sorted(values, key=canonical_json)


def validate_plan_params(params):
    if (not isinstance(params, dict) or set(params) != {"protocol", "target_resource_refs"}
            or params.get("protocol") != PLAN_PROTOCOL):
        raise RegistryConflict("INVALID_COMMAND: exact finite disable plan request required")
    _targets(params["target_resource_refs"])


def validate_availability_params(params):
    from .material_control import COMMAND_PROTOCOL, reference
    if (set(params) != {"protocol", "operation", "target", "execution", "confirmed_plan_ref"}
            or params.get("protocol") != COMMAND_PROTOCOL or not is_availability(params)
            or not isinstance(params.get("target"), str) or not params["target"]):
        raise RegistryConflict("INVALID_COMMAND: unsupported disable command or fields")
    execution = params["execution"]
    if not isinstance(execution, dict) or set(execution) != {"task_ref", "task_round_ref", "net_instance_ref"}:
        raise RegistryConflict("INVALID_COMMAND: exact disable execution required")
    for name, kind in (("task_ref", "task/v1"), ("task_round_ref", "task_round/v1"),
                       ("net_instance_ref", "net_instance/v1")):
        reference(execution[name], kind)
    reference(params["confirmed_plan_ref"], "resource_version/v1")


def _target(store, db, task_id, reference):
    """Only canonical ordinary task text products enter this first contract."""
    from .material_control import TEXT_SCHEMA
    item = exact_prepared(db, descriptor_store(store), task_id, exact_ref(reference))
    metadata = item.metadata
    if (metadata.get("content_schema_ref") != TEXT_SCHEMA or item.media_type != "application/json"
            or metadata.get("origin_kind") != "petri_output" or item.producer_invocation_id is None
            or metadata.get("task_ref", {}).get("logical_id") != str(task_id)
            or metadata.get("descriptors", {}).get("owner_input_role") is not None):
        raise RegistryConflict("PROTECTED_RESOURCE: disable permits only canonical ordinary task text products")
    roots = db.execute("SELECT p.* FROM firing_publications p JOIN firing_temporary_members m "
        "ON m.firing_version_id=p.firing_version_id WHERE m.member_kind='object' AND m.member_identity=?",
        (str(item.version_id),)).fetchall()
    if len(roots) != 1 or roots[0]["state"] != "PUBLISHED":
        raise RegistryConflict("PROTECTED_RESOURCE: target requires its actual normal Success")
    root = roots[0]
    record = store.ordered_firing_record(root["firing_version_id"], _db=db)
    if (root["invocation_logical_id"] != str(item.producer_invocation_id)
            or root["operation_result_version_id"] is None):
        raise RegistryConflict("PROTECTED_RESOURCE: target producer closure is not exact")
    result = db.execute("SELECT * FROM objects WHERE version_id=?", (root["operation_result_version_id"],)).fetchone()
    from .material_control import row_ref
    data = exact_descriptor(db, descriptor_store(store), task_id, row_ref(result)) if result is not None else None
    if (data is None or reference not in data.get("output_resource_refs", ())
            or data.get("business_outcome") != "completed"
            or result["transaction_id"] != root["published_transaction_id"]):
        raise RegistryConflict("PROTECTED_RESOURCE: target is not an exact completed semantic product")
    return item


def _addresses(store, db, task_id, targets):
    from .material_control import canonical_rows, row_ref
    keys = {canonical_json(ref) for ref in targets}
    heads = {}
    for row in canonical_rows(store, db, task_id, object_type="resource_address_binding/v1"):
        value = exact_descriptor(db, descriptor_store(store), task_id, row_ref(row))
        heads[(canonical_json(value["scope_ref"]), value["opaque_name"])] = (row_ref(row), value)
    result = []
    for ref, value in heads.values():
        resource = value.get("resource_ref")
        if resource is None or value["lifecycle_state"] != "bound":
            continue
        target = {"entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
                  "version_id": resource["resource_version_id"]}
        if canonical_json(target) in keys:
            result.append({"address": {"scope_ref": value["scope_ref"], "opaque_name": value["opaque_name"]},
                "expected_binding_ref": ref, "target_resource_ref": target})
    return sorted(result, key=canonical_json)


def _active_uses(store, db, task_id, targets):
    """Original claims and firing inputs remain responsibility until terminal."""
    from .material_control import _committed_descriptor, row_ref
    keys = {canonical_json(ref) for ref in targets}
    uses = []
    for root in db.execute("SELECT p.* FROM firing_publications p JOIN transactions t ON t.transaction_id=p.opened_transaction_id WHERE t.task_id=?", (str(task_id),)).fetchall():
        if root["state"] in {"PUBLISHED", "REJECTED"}:
            continue
        row = db.execute("SELECT * FROM objects WHERE version_id=?", (root["firing_version_id"],)).fetchone()
        if row is None:
            raise RegistryConflict("MATERIAL_USE_UNKNOWN: original firing responsibility is incomplete")
        firing = _committed_descriptor(store, row)
        for ref in firing["claimed_input_refs"]:
            token = exact_prepared(db, descriptor_store(store), task_id, ref)
            resource = token.metadata.get("resource_ref") or token.metadata.get("state", {}).get("resource_ref")
            if resource is not None:
                if "resource_id" in resource:
                    resource = {"entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
                        "version_id": resource["resource_version_id"]}
                if canonical_json(resource) in keys:
                    uses.append(row_ref(row))
                    break
    return sorted(uses, key=canonical_json)


def build_plan(store, db, task_id, targets):
    from .material_control import canonical_rows, row_ref
    targets = _targets(targets)
    items = [_target(store, db, task_id, ref) for ref in targets]
    heads = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not heads or len({row["logical_id"] for row in heads}) != 1:
        raise RegistryConflict("EXECUTION_CHANGED: disable plan needs its unique original run")
    run = exact_descriptor(db, descriptor_store(store), task_id, row_ref(heads[-1]))
    return {"protocol": PLAN_PROTOCOL, "source_task_ref": dict(items[0].metadata["task_ref"]),
        "source_cut": int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]),
        "target_resource_refs": targets, "address_bindings": _addresses(store, db, task_id, targets),
        "observed_active_firing_refs": _active_uses(store, db, task_id, targets),
        "latest_checkpoint_ref": run["latest_checkpoint_ref"],
        "carrier_refs": targets, "carrier_inventory_complete": False, "purge_requested": False}


def _plan_body(store, item):
    value = json.loads(descriptor_store(store).read_registered(item))
    store.catalog.validate_schema_ref(PLAN_SCHEMA, value)
    return value


def read_plan(store, db, task_id, ref, *, action=None, current=False, validate_targets=True):
    from .material_control import _original_action, _historical_actor
    item = exact_prepared(db, descriptor_store(store), task_id, exact_ref(ref))
    key, original = _original_action(db, item, PLAN_ACTION)
    session = _historical_actor(store, db, task_id, original, current=current)
    value = _plan_body(store, item)
    if (item.metadata.get("content_schema_ref") != PLAN_SCHEMA
            or item.metadata.get("origin_kind") != "private_system" or item.producer_invocation_id is not None
            or item.metadata.get("descriptors") != {"owner_input_command_id": key,
                "owner_input_role": PLAN_ROLE, "owner_input_actor": original["actor"],
                "owner_input_session_version_id": original["session_ref"]["version_id"]}
            or item.metadata.get("task_ref") != session["owner_task_ref"]
            or value["source_task_ref"] != session["owner_task_ref"]
            or (action is not None and (action["actor"] != original["actor"]
                                       or action["session_ref"] != original["session_ref"]))
            or value["target_resource_refs"] != _targets(value["target_resource_refs"])):
        raise RegistryConflict("INVALID_PLAN: plan differs from its original Owner input and actor")
    if validate_targets:
        for target in value["target_resource_refs"]:
            _target(store, db, task_id, target)
    row = db.execute("SELECT e.ordinal,t.command_json FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "JOIN transactions t ON t.transaction_id=o.transaction_id WHERE o.version_id=?", (ref["version_id"],)).fetchone()
    material = json.loads(row["command_json"])
    recorded = material.get("objects", ())
    if (value["source_cut"] >= row["ordinal"] or len(recorded) != 1
            or recorded[0].get("metadata") != dict(item.metadata)
            or recorded[0].get("version_id") != str(item.version_id)
            or material.get("user_collaboration") != original):
        raise RegistryConflict("INVALID_PLAN: original plan transaction inventory differs")
    if current and value["address_bindings"] != _addresses(store, db, task_id, value["target_resource_refs"]):
        raise RegistryConflict("MATERIAL_SCOPE_CHANGED: target address scope changed after confirmation")
    return value


def validate_plan_action(store, db, *, task_id, idempotency_key, objects, action, existing):
    plans = [item for item in objects if item.object_type == "resource_version/v1"
             and item.metadata.get("descriptors", {}).get("owner_input_role") == PLAN_ROLE]
    if not plans and (action is None or action.kind != PLAN_ACTION):
        return False
    from ..user_collaboration import check_handle
    from .material_control import require_session
    from .user_collaboration import check_replay
    check_handle(store, action)
    session = require_session(store, db, task_id, action.session)
    if action.kind != PLAN_ACTION or action.creation is None:
        raise RegistryConflict("INVALID_PLAN: exact trusted plan preparation action required")
    request = json.loads(action.creation)
    if (set(request) != {"owner_command", "command_id", "params"}
            or request["owner_command"] != PLAN_ACTION or request["command_id"] != idempotency_key):
        raise RegistryConflict("INVALID_PLAN: plan preparation changed its sealed request")
    validate_plan_params(request["params"])
    if existing is not None:
        check_replay(store, db, task_id, existing, action)
        return True
    if len(plans) != 1 or len(objects) != 1:
        raise RegistryConflict("INVALID_PLAN: one original plan input required")
    item, = plans
    expected = build_plan(store, db, task_id, request["params"]["target_resource_refs"])
    if (item.metadata.get("content_schema_ref") != PLAN_SCHEMA
            or item.metadata.get("origin_kind") != "private_system"
            or item.metadata.get("task_ref") != session["owner_task_ref"]
            or item.metadata.get("descriptors") != {"owner_input_command_id": idempotency_key,
                "owner_input_role": PLAN_ROLE, "owner_input_actor": action.session.actor,
                "owner_input_session_version_id": str(action.session.ref.version_id)}
            or descriptor_store(store).read_registered(item) != canonical_json(expected)):
        raise RegistryConflict("INVALID_PLAN: original finite plan changed before its write cut")
    return True


def validate_availability_spec(spec, binding):
    if (spec.get("executor_key") != EXECUTOR or spec.get("implementation_identity") != IDENTITY
            or spec.get("implementation_contracts") != CONTRACTS or spec.get("transport") != "deterministic"
            or spec.get("allowed_tool_ids") or binding.get("workspace_binding_ref") is not None
            or binding.get("llm_input_target_ref") is not None or len(binding.get("output_binding_refs", ())) != 1):
        raise RegistryConflict("TARGET_UNAVAILABLE: original availability operation contract is not exact")


def availability_definition(store, db, task_id, admission, command):
    from ...executable_net import load_compiled_net
    from .material_control import _committed_descriptor
    row = db.execute("SELECT * FROM objects WHERE version_id=?", (admission["transition_firing_ref"]["version_id"],)).fetchone()
    if row is None:
        raise RegistryConflict("availability requires its original firing")
    firing = _committed_descriptor(store, row)
    objects = descriptor_store(store)
    binding = exact_descriptor(db, objects, task_id, firing["operation_binding_ref"])
    spec = exact_descriptor(db, objects, task_id, binding["operation_spec_ref"])
    validate_availability_spec(spec, binding)
    net = exact_descriptor(db, objects, task_id, firing["net_instance_ref"])
    matches = [exact_descriptor(db, objects, task_id, ref) for ref in net["executable_transition_binding_refs"]]
    matches = [item for item in matches if item["transition_id"] == command["target"]
               and item["operation_binding_ref"] == firing["operation_binding_ref"]]
    if len(matches) != 1 or matches[0].get("agent_ref") != firing.get("agent_ref"):
        raise RegistryConflict("availability operation differs from the actual adopted transition")
    resource = matches[0]["declaration_resource_ref"]
    declaration = exact_prepared(db, objects, task_id, {"entity_type": "resource_version/v1",
        "logical_id": resource["resource_id"], "version_id": resource["resource_version_id"]})
    compiled = load_compiled_net(json.loads(objects.read_registered(declaration)))
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        raise RegistryConflict("availability lacks its exact registered definition")
    defined, = definitions
    config = dict(defined.declaration.config)
    store.catalog.validate_schema_ref(CONFIG_SCHEMA, config)
    outcomes = [item for item in defined.declaration.outcomes if item.name == config["outcome_id"]]
    ports = [item for item in compiled.ports if item.name == config["output_port_id"]]
    if (defined.executor_declaration.get("identity") != IDENTITY
            or defined.executor_declaration.get("contracts") != CONTRACTS
            or defined.declaration.executor != EXECUTOR or defined.declaration.tools
            or len(outcomes) != 1 or outcomes[0].effects or len(outcomes[0].products) != 1
            or len(ports) != 1 or ports[0].schema != EFFECT_SCHEMA
            or tuple(defined.declaration.outputs) != (ports[0].name,)
            or outcomes[0].products[0].port != ports[0].name
            or outcomes[0].products[0].minimum != 1 or outcomes[0].products[0].maximum != 1):
        raise RegistryConflict("availability requires its exact one-effect normal outcome")
    config["output_port_id"] = compiled.port_handles[config["output_port_id"]]
    return firing, binding, spec, defined, config


def validate_availability_baseline(store, db, task_id, params, *, current=False):
    plan = read_plan(store, db, task_id, params["confirmed_plan_ref"], action=params.get("user_action"), current=current)
    if plan["source_task_ref"] != params["execution"]["task_ref"]:
        raise RegistryConflict("INVALID_PLAN: confirmation belongs to another exact task")
    return plan


def availability_completion(store, db, task_id, admission, command, completion):
    """No workspace, hidden product, late mutation, or borrowed effect is allowed."""
    from .material_control import row_ref, OWNER_SCHEMA, control_body
    from .material_locations import _original_member_objects, _completed_petri_inputs
    from ..execution_child_closure import ClosureSnapshot
    firing, binding, spec, defined, config = availability_definition(store, db, task_id, admission, command)
    plan = validate_availability_baseline(store, db, task_id, command["data"])
    data = completion.payload
    if (data["workspace_revision_candidate_ref"] is not None or len(data["ordered_outputs"]) != 1
            or data["selected_outcome_id"] != config["outcome_id"]
            or data["operation_binding_ref"] != firing["operation_binding_ref"]
            or data["operation_spec_ref"] != spec["operation_spec_ref"]):
        raise RegistryConflict("availability completion differs from its exact registered output")
    output, = data["ordered_outputs"]
    effect_ref = {"entity_type": "resource_version/v1", "logical_id": output["resource_ref"]["resource_id"],
                  "version_id": output["resource_ref"]["resource_version_id"]}
    # Original inventory remains readable after PUBLISHED or REJECTED. This
    # is proof verification, never an ordinary body-delivery authorization.
    rows = _original_member_objects(store, db, task_id, admission)
    matches = [item for item in rows if row_ref(item) == effect_ref]
    if len(matches) != 1:
        raise RegistryConflict("availability completion has no exact original effect member")
    row, = matches
    from ..models import PreparedObject
    from ..identities import TypedId
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"], row["schema_ref"],
        TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
        row["storage_locator"], json.loads(row["metadata_json"]))
    descriptor_store(store).validate_envelope(prepared)
    store.catalog.validate_instance(prepared.object_type, category="object", instance=prepared.metadata)
    payload = descriptor_store(store).read_registered(prepared)
    effect = json.loads(payload)
    store.catalog.validate_schema_ref(EFFECT_SCHEMA, effect)
    metadata = json.loads(row["metadata_json"])
    wanted = {"protocol": PROTOCOL, "action": "disable", "target_resource_refs": plan["target_resource_refs"],
              "confirmed_plan_ref": command["data"]["confirmed_plan_ref"]}
    if (effect != wanted or output["port_id"] != config["output_port_id"]
            or output["output_binding_ref"] not in binding["output_binding_refs"]
            or metadata.get("origin_kind") != "petri_output"
            or metadata.get("content_schema_ref") != EFFECT_SCHEMA
            or metadata.get("origin", {}).get("primary_ref") != output["output_binding_ref"]
            or metadata.get("producer_ref") != admission["invocation_ref"]
            or row["producer_invocation_id"] != admission["invocation_ref"]["logical_id"]
            or metadata.get("task_ref") != command["data"]["execution"]["task_ref"]):
        raise RegistryConflict("availability effect differs from its exact producer, output or confirmation")
    links = db.execute("SELECT * FROM relations WHERE relation_type='derived_from' "
        "AND json_extract(source_json,'$.version_id')=?", (effect_ref["version_id"],)).fetchall()
    def endpoint(value):
        raw = json.loads(value)
        return {"entity_type": raw["entity_type"], "logical_id": raw.get("logical_id", raw.get("entity_id")),
                "version_id": raw.get("version_id")}
    if (len(links) != len(plan["target_resource_refs"])
            or sorted((endpoint(link["target_json"]) for link in links), key=canonical_json) != plan["target_resource_refs"]
            or any(endpoint(link["source_json"]) != effect_ref or link["strength"] != "strong"
                   or link["transaction_id"] != row["transaction_id"] for link in links)):
        raise RegistryConflict("availability effect exact target relationships are incomplete")
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                      (admission["transition_firing_ref"]["version_id"],)).fetchone()
    rows = [item for item in rows if item["transaction_id"] != root["published_transaction_id"]]
    if any(item["ordinal"] >= completion.ordinal for item in rows):
        raise RegistryConflict("availability preparation changed after its registered completion")
    inputs = _completed_petri_inputs(store, db, admission, completion, rows)
    allowed = {"transition_firing/v1", "invocation/v1", "firing_admission/v1", "operation_execution_lease/v1"}
    for item in rows:
        if item["object_type"] in allowed or item["version_id"] in inputs or row_ref(item) == effect_ref:
            continue
        if item["object_type"] == "marking_delta/v1" and row_ref(item) == admission["claim_marking_delta_ref"]:
            continue
        if item["object_type"] == "resource_version/v1" and json.loads(item["metadata_json"]).get("content_schema_ref") == OWNER_SCHEMA:
            from ..models import PreparedObject
            from ..identities import TypedId
            prepared = PreparedObject(item["object_type"], TypedId.parse(item["logical_id"]),
                TypedId.parse(item["version_id"]), item["size"], item["media_type"], item["schema_ref"],
                None, item["storage_locator"], json.loads(item["metadata_json"]))
            body = control_body(store, prepared)
            if (body["data"].get("protocol") == "rpnh/material_admission/v1"
                    and body["data"].get("command_ref") == admission["owner_command_ref"]
                    and body["data"].get("admission_ref") == admission["firing_admission_ref"]):
                continue
        raise RegistryConflict("availability operation contains an undeclared body or responsibility")
    return effect_ref


def validate_availability_promotion(store, db, task_id, admission, command):
    plan = validate_availability_baseline(store, db, task_id, command["data"], current=True)
    # A live invocation may already hold workspace/archive bytes without a
    # body-specific delivery; do not infer that unrelated claims prove drain.
    roots = db.execute("SELECT p.*,t.task_id AS source_task_id FROM firing_publications p "
        "LEFT JOIN transactions t ON t.transaction_id=p.opened_transaction_id").fetchall()
    for root in roots:
        if root["source_task_id"] is None:
            raise RegistryConflict("MATERIAL_USE_UNKNOWN: original source responsibility has no opening transaction")
        if (root["source_task_id"] != str(task_id)
                or root["firing_version_id"] == admission["transition_firing_ref"]["version_id"]):
            continue
        if root["state"] not in {"PUBLISHED", "REJECTED"}:
            raise RegistryConflict("MATERIAL_IN_USE: another original source firing is still active")
        try:
            # A closed projection alone cannot erase a live workspace/body
            # holder. Reconstruct its original Success or exact rejection.
            store.ordered_firing_record(root["firing_version_id"], _db=db)
        except (RegistryConflict, RegistryCorruptError, SchemaGovernanceError,
                ObjectIntegrityError, sqlite3.DatabaseError, KeyError, TypeError, ValueError, OSError) as exc:
            raise RegistryConflict("MATERIAL_USE_UNKNOWN: original source responsibility is not terminal") from exc
    if _active_uses(store, db, task_id, plan["target_resource_refs"]):
        raise RegistryConflict("MATERIAL_IN_USE: original target or other source firing is still active")
    from .material_delivery_drain import assert_material_deliveries_drained
    from ...material_body_policy import expanded_material_carrier_refs
    assert_material_deliveries_drained(store, db, task_id,
        expanded_material_carrier_refs(store, db, task_id, plan["target_resource_refs"]))


def _publication_state(store, db, root):
    successes = db.execute("SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
        "WHERE e.event_type='transition_firing_settled/v1' AND t.status='committed' "
        "AND json_extract(e.payload_json,'$.transition_firing_ref.version_id')=?",
        (root["firing_version_id"],)).fetchall()
    if root["state"] != "PUBLISHED" and successes:
        raise RegistryConflict("availability root state disagrees with its original normal Success")
    if root["state"] == "PUBLISHED" and (len(successes) != 1
            or successes[0]["transaction_id"] != root["published_transaction_id"]):
        raise RegistryConflict("availability root lost its original normal Success")
    return root["state"]


def _disable_command_candidates(store, db, task_id):
    """Union original objects, publication events and sealed transaction material."""
    from .material_control import row_ref
    result = {}
    def trusted_transaction(identity):
        tx = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (identity,)).fetchone()
        action = None if tx is None else json.loads(tx["command_json"]).get("user_collaboration")
        return (tx is not None and tx["task_id"] == str(task_id)
                and isinstance(action, dict) and action.get("kind") == "material_submit")
    for row in db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.task_id=? AND json_extract(o.metadata_json,'$.descriptors.owner_material_operation')='disable_versions'",
            (str(task_id),)).fetchall():
        if trusted_transaction(row["transaction_id"]):
            result[row["version_id"]] = row_ref(row)
    for row in db.execute("SELECT * FROM events WHERE task_id=? AND event_type='object_version_published/v1' "
            "AND json_extract(payload_json,'$.metadata.descriptors.owner_material_operation')='disable_versions'",
            (str(task_id),)).fetchall():
        if not trusted_transaction(row["transaction_id"]):
            continue
        body = json.loads(row["payload_json"])
        result[body["version_id"]] = {key: body[key] for key in ("logical_id", "version_id")}
        result[body["version_id"]]["entity_type"] = body["object_type"]
    rows = db.execute("SELECT * FROM transactions WHERE task_id=? AND "
        "json_extract(command_json,'$.user_collaboration.kind')='material_submit'", (str(task_id),)).fetchall()
    for row in rows:
        material = json.loads(row["command_json"])
        for item in material.get("objects", ()):
            if item.get("metadata", {}).get("descriptors", {}).get("owner_material_operation") == "disable_versions":
                if row["status"] != "committed":
                    published = db.execute("SELECT 1 FROM events WHERE transaction_id=? LIMIT 1",
                        (row["transaction_id"],)).fetchone()
                    if row["status"] == "aborted" and published is None:
                        continue
                    raise RegistryConflict("availability has unresolved original command material")
                result[item["version_id"]] = {"entity_type": item["object_type"],
                    "logical_id": item["logical_id"], "version_id": item["version_id"]}
    # Genuine effect outputs independently recover command authority if a
    # command or admission projection has been damaged. Ordinary same-schema
    # products never gain authority: their actual spec is checked first.
    effects = {}
    for row in db.execute("SELECT * FROM objects WHERE object_type='resource_version/v1' "
            "AND json_extract(metadata_json,'$.content_schema_ref')=?", (EFFECT_SCHEMA,)).fetchall():
        effects[row["version_id"]] = json.loads(row["metadata_json"])
    for row in db.execute("SELECT payload_json FROM events WHERE task_id=? "
            "AND event_type='object_version_published/v1' AND json_extract(payload_json,'$.metadata.content_schema_ref')=?",
            (str(task_id), EFFECT_SCHEMA)).fetchall():
        body = json.loads(row["payload_json"])
        effects[body["version_id"]] = body["metadata"]
    for metadata in effects.values():
        producer = metadata.get("producer_ref", {})
        binding_ref = metadata.get("reference_provenance", {}).get("operation_binding_ref")
        if producer.get("entity_type") != "invocation/v1" or binding_ref is None:
            continue
        binding = exact_descriptor(db, descriptor_store(store), task_id, binding_ref)
        spec = exact_descriptor(db, descriptor_store(store), task_id, binding["operation_spec_ref"])
        if (spec.get("executor_key") != EXECUTOR
                and "owner_material_availability" not in spec.get("implementation_contracts", {})):
            continue
        validate_availability_spec(spec, binding)
        roots = db.execute("SELECT * FROM firing_publications WHERE invocation_version_id=?",
                           (producer["version_id"],)).fetchall()
        if len(roots) != 1:
            raise RegistryConflict("trusted availability effect has no unique original root")
        root, = roots
        if _publication_state(store, db, root) != "PUBLISHED":
            continue
        firing_row = db.execute("SELECT * FROM objects WHERE version_id=?", (root["firing_version_id"],)).fetchone()
        if firing_row is None:
            raise RegistryConflict("trusted availability root lost its original firing")
        firing = json.loads(firing_row["metadata_json"])
        admission_row = db.execute("SELECT * FROM objects WHERE version_id=?",
                                   (firing["firing_admission_ref"]["version_id"],)).fetchone()
        if admission_row is None:
            raise RegistryConflict("trusted availability root lost its original admission")
        admission = json.loads(admission_row["metadata_json"])
        command_ref = exact_ref(admission.get("owner_command_ref"))
        result[command_ref["version_id"]] = command_ref
    return tuple(result[key] for key in sorted(result))


def source_governs_materials_at(store, db, task_id):
    """Original adopted history and commands cannot silently become legacy."""
    from .material_control import read_accepted_material_command
    from .collaboration_descriptors import _canonical_closure
    # The original committed transaction inventory also locates an adoption
    # whose event or object index is missing; do not treat that as empty history.
    adopted = {}
    for row in db.execute("SELECT * FROM events WHERE task_id=? AND event_type='net_adopted/v1' ORDER BY ordinal",
                          (str(task_id),)).fetchall():
        if not _canonical_closure(db, task_id, transaction_id=row["transaction_id"],
                                  members=(("event", row["event_id"]),)):
            continue
        value = json.loads(row["payload_json"])
        adopted[canonical_json(value["net_instance_ref"])] = value["net_instance_ref"]
    for row in db.execute("SELECT t.* FROM transactions t WHERE t.task_id=? AND EXISTS "
            "(SELECT 1 FROM json_each(t.command_json,'$.events') e WHERE json_extract(e.value,'$.event_type')='net_adopted/v1')",
            (str(task_id),)).fetchall():
        material = json.loads(row["command_json"])
        for entry in material.get("events", ()):
            if entry.get("event_type") != "net_adopted/v1":
                continue
            value = entry["payload"]
            matches = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='net_adopted/v1'",
                                 (row["transaction_id"],)).fetchall()
            matching = [e for e in matches if json.loads(e["payload_json"]) == value]
            if len(matching) != 1:
                raise RegistryConflict("material source adoption inventory is incomplete")
            if _canonical_closure(db, task_id, transaction_id=row["transaction_id"],
                                  members=(("event", matching[0]["event_id"]),)):
                adopted[canonical_json(value["net_instance_ref"])] = value["net_instance_ref"]
    for ref in adopted.values():
        net = exact_descriptor(db, descriptor_store(store), task_id, ref)
        for binding_ref in net["operation_binding_refs"]:
            binding = exact_descriptor(db, descriptor_store(store), task_id, binding_ref)
            spec = exact_descriptor(db, descriptor_store(store), task_id, binding["operation_spec_ref"])
            contracts = spec.get("implementation_contracts", {})
            if "owner_material_availability" in contracts or spec.get("executor_key") == EXECUTOR:
                validate_availability_spec(spec, binding)
                return True
            from .human_viewer import READ_EXECUTOR, validate_read_spec
            if "owner_material_reads" in contracts or spec.get("executor_key") == READ_EXECUTOR:
                validate_read_spec(spec, binding)
                return True
    for ref in _disable_command_candidates(store, db, task_id):
        read_accepted_material_command(store, db, task_id, ref, current=False)
        return True
    return False


def resolve_availability(store, db, task_id, resource_ref):
    """Resolve one exact identity at the caller's current original DB cut.

This is an availability gate, never a visibility/grant gate. It can therefore
classify an original committed provisional product whose visibility remains
the responsibility of the existing firing-specific authorization path.
"""
    from .material_control import canonical_rows, row_ref, control_body, OWNER_SCHEMA, read_accepted_material_command
    from .material_settlement import material_admission, terminal_result
    reference = exact_ref(resource_ref)
    upper = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
    result = {"availability": "available", "availability_as_of": upper, "effect_refs": [], "proof_refs": []}
    row = db.execute("SELECT * FROM objects WHERE object_type='resource_version/v1' AND logical_id=? AND version_id=?",
                      (reference["logical_id"], reference["version_id"])).fetchone()
    if row is None:
        return {**result, "availability": "unknown", "public_reason": "Exact resource is unavailable"}
    try:
        metadata = json.loads(row["metadata_json"])
        if metadata.get("task_ref", {}).get("logical_id") != str(task_id):
            raise RegistryConflict("exact resource is outside this source")
        # Union the original command inventory with canonical effect candidates.
        # The command's finite plan still locates a damaged/missing effect or
        # relationship index; a cache or empty endpoint query is never authority.
        commands = _disable_command_candidates(store, db, task_id)
        for command_ref in commands:
            item = exact_prepared(db, descriptor_store(store), task_id, command_ref)
            body = control_body(store, item)
            if body.get("kind") != "command" or not is_availability(body.get("data", {})):
                continue
            plan = read_plan(store, db, task_id, body["data"]["confirmed_plan_ref"],
                action=body["data"]["user_action"], validate_targets=False)
            if reference not in plan["target_resource_refs"]:
                continue
            command = read_accepted_material_command(store, db, task_id, command_ref, current=False)
            admissions = db.execute("SELECT * FROM objects WHERE object_type='firing_admission/v1' "
                "AND json_extract(metadata_json,'$.owner_command_ref.version_id')=?", (command_ref["version_id"],)).fetchall()
            if len(admissions) > 1:
                raise RegistryConflict("availability command has ambiguous original admissions")
            if not admissions:
                continue
            admitted = json.loads(admissions[0]["metadata_json"])
            root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                              (admitted["transition_firing_ref"]["version_id"],)).fetchone()
            if root is None:
                raise RegistryConflict("availability command lost its original publication root")
            if _publication_state(store, db, root) != "PUBLISHED":
                continue
            admission, command = material_admission(store, db, task_id, admitted["transition_firing_ref"])
            result_ref, data = terminal_result(store, db, task_id, admission, command, root)
            if data["result_kind"] != "versions_disabled" or len(data["output_refs"]) != 1:
                raise RegistryConflict("availability original result is inconsistent")
            result["effect_refs"].extend(data["output_refs"])
            result["proof_refs"].extend((command_ref, command["data"]["confirmed_plan_ref"],
                                         data["operation_result_ref"], data["completion_ref"], result_ref))
        if result["effect_refs"]:
            result["availability"] = "disabled"
            result["public_reason"] = "This exact material version is disabled"
        return result
    except (RegistryConflict, RegistryCorruptError, SchemaGovernanceError, ObjectIntegrityError,
            sqlite3.DatabaseError, ValueError, KeyError, TypeError, OSError):
        return {"availability": "unknown", "availability_as_of": upper, "effect_refs": [], "proof_refs": [],
                "public_reason": "Current material authority is incomplete"}


def require_available_at(store, db, task_id, resource_ref, *, purpose):
    value = resolve_availability(store, db, task_id, resource_ref)
    if value["availability"] != "available":
        raise RegistryConflict("MATERIAL_UNAVAILABLE: " + value["availability"] + " for " + purpose)
    return value
