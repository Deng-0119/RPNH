"""Finite carrier authorization and results on the original publication path.

The authorization is an ordinary registered output.  It only becomes usable
after its original Success.  No file path, inventory cache or UI flag grants
permission to remove a payload.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict
from .collaboration_descriptors import descriptor_store, exact_prepared, exact_descriptor


PROTOCOL = "rpnh/material_purge/v1"
AUTH_SCHEMA = "application/rpnh_material_purge_authorization/v1"
RESULT_SCHEMA = "application/rpnh_material_purge_result/v1"
CONFIG_SCHEMA = "application/rpnh_material_purge_config/v1"
AUTH_EXECUTOR = "rpnh/material-purge-authorize/v1"
PURGE_EXECUTOR = "rpnh/material-purge-execute/v1"
PURGE_TOOL = "rpnh/material-purge-exact-blob/v1"
AUTH_IDENTITY = {"implementation_id": "rpnh.material_purge_authorize", "revision": "v1"}
PURGE_IDENTITY = {"implementation_id": "rpnh.material_purge_execute", "revision": "v1"}
TOOL_IDENTITY = {"implementation_id": "rpnh.material_purge_exact_blob", "revision": "v1"}
TOOL_CONTRACTS = {"binding_protocol": PROTOCOL, "scope": "one_exact_immutable_blob"}
COMMON_CONTRACTS = {"transport": "deterministic", "input_ports": None, "output_ports": None,
    "config_schema": CONFIG_SCHEMA, "owner_material_commands": "rpnh/material_command/v1"}
AUTH_CONTRACTS = {**COMMON_CONTRACTS, "owner_material_purge": "authorize_purge"}
PURGE_CONTRACTS = {**COMMON_CONTRACTS, "owner_material_purge": "purge_carrier"}
ACTIONS = frozenset(("authorize_purge", "purge_carrier"))
ROLES = frozenset(("semantic_output", "workspace_file"))


def is_purge(data):
    return data.get("operation") in ACTIONS


def purge_result_kind(data):
    return "purge_authorized" if data["operation"] == "authorize_purge" else "carrier_purged"


def purge_lineage(data):
    return ([data["material_command_ref"], data["carrier_ref"]]
            if data["operation"] == "authorize_purge" else [data["authorization_ref"]])


def validate_purge_params(params):
    from .material_control import COMMAND_PROTOCOL, reference
    if not isinstance(params, dict) or not is_purge(params):
        raise RegistryConflict("INVALID_COMMAND: finite purge action is required")
    fields = {"protocol", "operation", "target", "execution"}
    fields |= ({"material_command_ref", "carrier_ref"} if params["operation"] == "authorize_purge"
               else {"authorization_ref"})
    if (set(params) != fields or params.get("protocol") != COMMAND_PROTOCOL
            or not isinstance(params.get("target"), str) or not params["target"]):
        raise RegistryConflict("INVALID_COMMAND: purge command fields are not exact")
    execution = params["execution"]
    if not isinstance(execution, dict) or set(execution) != {"task_ref", "task_round_ref", "net_instance_ref"}:
        raise RegistryConflict("INVALID_COMMAND: purge execution is not exact")
    for key, kind in (("task_ref", "task/v1"), ("task_round_ref", "task_round/v1"), ("net_instance_ref", "net_instance/v1")):
        reference(execution[key], kind)
    for key in fields - {"protocol", "operation", "target", "execution"}:
        reference(params[key], "resource_version/v1")


def _selection(store, db, task_id, material_command_ref, carrier_ref):
    from ...material_carriers import inspect_material_carriers_at
    from .material_availability import resolve_availability
    inspection = inspect_material_carriers_at(store, db, task_id, material_command_ref=material_command_ref)
    matches = [entry for entry in inspection["carriers"] if entry["carrier_ref"] == carrier_ref]
    if len(matches) != 1 or matches[0]["role"] not in ROLES:
        raise RegistryConflict("PURGE_UNSUPPORTED: only one proven semantic/file immutable blob is supported")
    carrier = exact_prepared(db, descriptor_store(store), task_id, carrier_ref)
    if (carrier.object_type != "resource_version/v1"
            or carrier.storage_locator != descriptor_store(store).locator_for_version(carrier.version_id)):
        raise RegistryConflict("PURGE_UNSUPPORTED: carrier locator is not its exact immutable version")
    availability = resolve_availability(store, db, task_id, inspection["output_ref"])
    if availability["availability"] != "disabled" or not availability["effect_refs"]:
        raise RegistryConflict("PURGE_NOT_DISABLED: original semantic version is not proved disabled")
    return {"material_command_ref": material_command_ref, "target_resource_ref": inspection["output_ref"],
        "carrier_ref": carrier_ref, "carrier_role": matches[0]["role"],
        "disable_effect_refs": availability["effect_refs"]}, inspection


def read_purge_authorization(store, db, task_id, authorization_ref):
    from .material_control import row_ref
    from .material_settlement import material_admission, terminal_result
    item = exact_prepared(db, descriptor_store(store), task_id, authorization_ref)
    if item.metadata.get("content_schema_ref") != AUTH_SCHEMA or item.metadata.get("origin_kind") != "petri_output":
        raise RegistryConflict("PURGE_UNAUTHORIZED: not an original authorization output")
    roots = db.execute("SELECT p.* FROM firing_publications p JOIN firing_temporary_members m "
        "ON m.firing_version_id=p.firing_version_id WHERE m.member_kind='object' AND m.member_identity=?",
        (authorization_ref["version_id"],)).fetchall()
    if len(roots) != 1 or roots[0]["state"] != "PUBLISHED":
        raise RegistryConflict("PURGE_UNAUTHORIZED: authorization has no original normal Success")
    root = roots[0]
    firing = db.execute("SELECT * FROM objects WHERE version_id=?", (root["firing_version_id"],)).fetchone()
    if firing is None:
        raise RegistryConflict("PURGE_UNAUTHORIZED: authorization lost its original firing")
    found = material_admission(store, db, task_id, row_ref(firing))
    if found is None:
        raise RegistryConflict("PURGE_UNAUTHORIZED: producer has no original material admission")
    admission, command = found
    if command["data"].get("operation") != "authorize_purge":
        raise RegistryConflict("PURGE_UNAUTHORIZED: lookalike producer is not the authorization operation")
    _result_ref, result = terminal_result(store, db, task_id, admission, command, root)
    if result["result_kind"] != "purge_authorized" or result["output_refs"] != [authorization_ref]:
        raise RegistryConflict("PURGE_UNAUTHORIZED: authorization is outside its exact original result")
    body = json.loads(descriptor_store(store).read_registered(item))
    store.catalog.validate_schema_ref(AUTH_SCHEMA, body)
    return body, command


def validate_purge_baseline(store, db, task_id, params, *, current=False):
    # Historical proofs retain the original confirmation.  Live physical
    # work additionally requires that exact actor session to remain current.
    if current:
        from .material_control import _historical_actor
        action = params.get("user_action")
        if action is None:
            raise RegistryConflict("PURGE_UNAUTHORIZED: current exact HOST actor is required")
        _historical_actor(store, db, task_id, action, current=True)
    if params["operation"] == "authorize_purge":
        selection, _inspection = _selection(store, db, task_id, params["material_command_ref"], params["carrier_ref"])
        return selection
    authorization, original = read_purge_authorization(store, db, task_id, params["authorization_ref"])
    selection, _inspection = _selection(store, db, task_id, authorization["material_command_ref"], authorization["carrier_ref"])
    if (authorization["source_task_ref"] != params["execution"]["task_ref"]
            or authorization["target_resource_ref"] != selection["target_resource_ref"]
            or authorization["carrier_role"] != selection["carrier_role"]):
        raise RegistryConflict("PURGE_UNAUTHORIZED: exact carrier scope differs from its authorization")
    action = params.get("user_action")
    if action is not None and any(action[key] != original["data"]["user_action"][key] for key in ("actor", "session_ref")):
        raise RegistryConflict("PURGE_UNAUTHORIZED: this finite contract requires its original confirmation actor")
    return authorization


def validate_purge_spec(spec, binding, *, operation=None):
    phase = spec.get("implementation_contracts", {}).get("owner_material_purge")
    if phase not in ACTIONS or (operation is not None and phase != operation):
        raise RegistryConflict("TARGET_UNAVAILABLE: purge operation phase differs")
    execute = phase == "purge_carrier"
    executor, identity, contracts = ((PURGE_EXECUTOR, PURGE_IDENTITY, PURGE_CONTRACTS) if execute
                                    else (AUTH_EXECUTOR, AUTH_IDENTITY, AUTH_CONTRACTS))
    if (spec.get("executor_key") != executor or spec.get("implementation_identity") != identity
            or spec.get("implementation_contracts") != contracts or spec.get("transport") != "deterministic"
            or spec.get("allowed_tool_ids") != ([PURGE_TOOL] if execute else [])
            or binding.get("workspace_binding_ref") is not None or binding.get("llm_input_target_ref") is not None
            or len(binding.get("output_binding_refs", ())) != 1):
        raise RegistryConflict("TARGET_UNAVAILABLE: purge lacks its exact registered normal operation")


def purge_definition(store, db, task_id, admission, command):
    from ...executable_net import load_compiled_net
    from .material_control import _committed_descriptor
    row = db.execute("SELECT * FROM objects WHERE version_id=?", (admission["transition_firing_ref"]["version_id"],)).fetchone()
    if row is None:
        raise RegistryConflict("purge lacks its original firing")
    firing = _committed_descriptor(store, row)
    objects = descriptor_store(store)
    binding = exact_descriptor(db, objects, task_id, firing["operation_binding_ref"])
    spec = exact_descriptor(db, objects, task_id, binding["operation_spec_ref"])
    validate_purge_spec(spec, binding, operation=command["data"]["operation"])
    net = exact_descriptor(db, objects, task_id, firing["net_instance_ref"])
    matches = [exact_descriptor(db, objects, task_id, ref) for ref in net["executable_transition_binding_refs"]]
    matches = [item for item in matches if item["transition_id"] == command["target"]
               and item["operation_binding_ref"] == firing["operation_binding_ref"]]
    if len(matches) != 1 or matches[0].get("agent_ref") != firing.get("agent_ref"):
        raise RegistryConflict("purge operation differs from its adopted transition")
    ref = matches[0]["declaration_resource_ref"]
    declaration = exact_prepared(db, objects, task_id, {"entity_type": "resource_version/v1",
        "logical_id": ref["resource_id"], "version_id": ref["resource_version_id"]})
    compiled = load_compiled_net(json.loads(objects.read_registered(declaration)))
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        raise RegistryConflict("purge lacks its original compiled definition")
    defined, = definitions
    config = dict(defined.declaration.config)
    store.catalog.validate_schema_ref(CONFIG_SCHEMA, config)
    outcomes = [item for item in defined.declaration.outcomes if item.name == config["outcome_id"]]
    ports = [item for item in compiled.ports if item.name == config["output_port_id"]]
    execute = command["data"]["operation"] == "purge_carrier"
    schema = RESULT_SCHEMA if execute else AUTH_SCHEMA
    if execute:
        tool = compiled.registrations.get("tool", {}).get(PURGE_TOOL, {})
        inputs = [item for item in compiled.ports if item.name in defined.declaration.inputs]
        arcs = [item for item in compiled.symbolic.arcs
                if item.transition == command["target"] and item.direction == "input"]
        if (tool.get("identity") != TOOL_IDENTITY or tool.get("contracts") != TOOL_CONTRACTS
                or len(inputs) != 1 or tuple(defined.declaration.inputs) != (inputs[0].name,)
                or inputs[0].schema != AUTH_SCHEMA or inputs[0].minimum != 1 or inputs[0].maximum != 1
                or len(arcs) != 1 or arcs[0].place != inputs[0].place
                or arcs[0].mode != "consume" or arcs[0].weight != 1):
            raise RegistryConflict("purge requires one exact authorization input and original registered tool")
    if (defined.executor_declaration.get("identity") != spec["implementation_identity"]
            or defined.executor_declaration.get("contracts") != spec["implementation_contracts"]
            or defined.declaration.executor != spec["executor_key"]
            or list(defined.declaration.tools) != ([PURGE_TOOL] if execute else [])
            or len(outcomes) != 1 or outcomes[0].effects or len(outcomes[0].products) != 1
            or len(ports) != 1 or ports[0].schema != schema
            or tuple(defined.declaration.outputs) != (ports[0].name,)
            or outcomes[0].products[0].port != ports[0].name
            or outcomes[0].products[0].minimum != 1 or outcomes[0].products[0].maximum != 1):
        raise RegistryConflict("purge requires its exact one-output normal outcome")
    config["output_port_id"] = compiled.port_handles[config["output_port_id"]]
    return firing, binding, spec, defined, config


def assert_purge_ready(store, db, task_id, admission, command):
    """Current gate, used under the original write lock before physical work."""
    from .material_availability import _active_uses
    from .material_delivery_drain import assert_material_deliveries_drained
    from ...material_body_policy import expanded_material_carrier_refs
    baseline = validate_purge_baseline(store, db, task_id, command["data"], current=True)
    target = baseline["target_resource_ref"]
    roots = db.execute("SELECT p.*,t.task_id AS source_task_id FROM firing_publications p "
        "LEFT JOIN transactions t ON t.transaction_id=p.opened_transaction_id").fetchall()
    for root in roots:
        if root["source_task_id"] is None:
            raise RegistryConflict("PURGE_UNKNOWN: original responsibility lacks its opening transaction")
        if root["source_task_id"] != str(task_id) or root["firing_version_id"] == admission["transition_firing_ref"]["version_id"]:
            continue
        if root["state"] not in {"PUBLISHED", "REJECTED"}:
            raise RegistryConflict("PURGE_IN_USE: another source firing is still active")
        store.ordered_firing_record(root["firing_version_id"], _db=db)
    if _active_uses(store, db, task_id, [target]):
        raise RegistryConflict("PURGE_IN_USE: original target input remains active")
    assert_material_deliveries_drained(store, db, task_id,
        expanded_material_carrier_refs(store, db, task_id, [target]))
    return baseline


def purge_completion(store, db, task_id, admission, command, completion):
    """Prove one original output and the actual purge execution child, if any."""
    from .material_control import row_ref, _committed_descriptor, OWNER_SCHEMA, control_body
    from .material_locations import _original_member_objects, _completed_petri_inputs
    from ..models import PreparedObject
    from ..identities import TypedId
    firing, binding, spec, _defined, config = purge_definition(store, db, task_id, admission, command)
    data = completion.payload
    if (data["workspace_revision_candidate_ref"] is not None or len(data["ordered_outputs"]) != 1
            or data["selected_outcome_id"] != config["outcome_id"]
            or data["operation_binding_ref"] != firing["operation_binding_ref"]
            or data["operation_spec_ref"] != spec["operation_spec_ref"]):
        raise RegistryConflict("purge completion differs from its original declared output")
    rows = _original_member_objects(store, db, task_id, admission)
    output, = data["ordered_outputs"]
    resource = output["resource_ref"]
    output_ref = {"entity_type": "resource_version/v1", "logical_id": resource["resource_id"], "version_id": resource["resource_version_id"]}
    matches = [row for row in rows if row_ref(row) == output_ref]
    if len(matches) != 1:
        raise RegistryConflict("purge result is not its original firing member")
    row, = matches
    meta = json.loads(row["metadata_json"])
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
        row["size"], row["media_type"], row["schema_ref"], TypedId.parse(row["producer_invocation_id"]), row["storage_locator"], meta)
    body = json.loads(descriptor_store(store).read_registered(prepared))
    execute = command["data"]["operation"] == "purge_carrier"
    schema = RESULT_SCHEMA if execute else AUTH_SCHEMA
    store.catalog.validate_schema_ref(schema, body)
    if (output["port_id"] != config["output_port_id"] or output["output_binding_ref"] not in binding["output_binding_refs"]
            or meta.get("origin_kind") != "petri_output" or meta.get("content_schema_ref") != schema
            or meta.get("origin", {}).get("primary_ref") != output["output_binding_ref"]
            or meta.get("producer_ref") != admission["invocation_ref"]
            or meta.get("task_ref") != command["data"]["execution"]["task_ref"]):
        raise RegistryConflict("purge output differs from its actual producer")
    baseline = validate_purge_baseline(store, db, task_id, command["data"])
    common = {"protocol": PROTOCOL, "source_task_ref": command["data"]["execution"]["task_ref"],
        "carrier_ref": baseline["carrier_ref"], "scope": "one_exact_immutable_blob", "all_carriers_cleared": False}
    if execute:
        expected = {**common, "authorization_ref": command["data"]["authorization_ref"], "state": body["state"]}
    else:
        expected = {**common, "authorization_command_ref": admission["owner_command_ref"],
            "material_command_ref": baseline["material_command_ref"], "target_resource_ref": baseline["target_resource_ref"],
            "carrier_role": baseline["carrier_role"], "disable_effect_refs": body["disable_effect_refs"]}
        if (not body["disable_effect_refs"] or any(ref not in baseline["disable_effect_refs"] for ref in body["disable_effect_refs"])):
            raise RegistryConflict("purge authorization lost its original disable evidence")
    if body != expected:
        raise RegistryConflict("purge output changed its exact authorization scope")
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?", (admission["transition_firing_ref"]["version_id"],)).fetchone()
    before = [item for item in rows if item["transaction_id"] != root["published_transaction_id"]]
    if any(item["ordinal"] >= completion.ordinal for item in before):
        raise RegistryConflict("purge preparation changed after original completion")
    input_ids = _completed_petri_inputs(store, db, admission, completion, before)
    instances = [item for item in before if item["object_type"] == "execution_instance/v1"]
    if execute:
        from ...material_purge import PURGE_NET
        from ..execution_child_closure import _definition
        if len(instances) != 1:
            raise RegistryConflict("purge requires one actual execution PN instance")
        instance = _committed_descriptor(store, instances[0])
        definitions = [item for item in before if row_ref(item) == instance["execution_net_definition_ref"]]
        if len(definitions) != 1 or definitions[0]["object_type"] != "execution_net_definition/v1":
            raise RegistryConflict("purge execution definition is not its own original provisional member")
        definition = _committed_descriptor(store, definitions[0])
        checkpoints = [item for item in before if item["object_type"] == "execution_checkpoint/v1"
            and json.loads(item["metadata_json"]).get("execution_instance_ref") == row_ref(instances[0])]
        if not checkpoints:
            raise RegistryConflict("purge execution lacks its original checkpoint")
        checkpoint = _committed_descriptor(store, max(checkpoints, key=lambda item: item["ordinal"]))
        if (_definition(definition) != PURGE_NET or instance["parent_business_firing_ref"] != admission["transition_firing_ref"]
                or instance["parent_invocation_ref"] != admission["invocation_ref"]
                or instance["parent_business_net_ref"] != firing["net_instance_ref"]
                or instance["parent_business_checkpoint_ref"] != admission["admission_marking_checkpoint_ref"]
                or checkpoint["execution_instance_ref"] != row_ref(instances[0])
                or checkpoint["execution_net_definition_ref"] != row_ref(definitions[0])
                or checkpoint["status"] != "map_ready" or checkpoint["active_firing_refs"]
                or checkpoint["evidence_refs"] != [output_ref]):
            raise RegistryConflict("purge result lacks its exact map-ready execution proof")
        for child in before:
            if child["object_type"] in {"execution_checkpoint/v1", "execution_token/v1", "execution_transition_firing/v1"}:
                value = _committed_descriptor(store, child)
                if value["execution_instance_ref"] != row_ref(instances[0]):
                    raise RegistryConflict("purge child belongs to another execution instance")
            elif child["object_type"] == "execution_net_definition/v1" and row_ref(child) != row_ref(definitions[0]):
                raise RegistryConflict("purge contains an undeclared execution definition")
    elif any(item["object_type"] in {"execution_instance/v1", "execution_checkpoint/v1", "execution_token/v1",
             "execution_transition_firing/v1", "execution_net_definition/v1"} for item in before):
        raise RegistryConflict("purge authorization cannot perform physical work")
    allowed = {"transition_firing/v1", "invocation/v1", "firing_admission/v1", "operation_execution_lease/v1",
               "execution_instance/v1", "execution_checkpoint/v1", "execution_transition_firing/v1",
               "execution_net_definition/v1", "execution_token/v1"}
    for item in before:
        if item["object_type"] in allowed or item["version_id"] in input_ids or row_ref(item) == output_ref:
            continue
        if item["object_type"] == "marking_delta/v1" and row_ref(item) == admission["claim_marking_delta_ref"]:
            continue
        if item["object_type"] == "resource_version/v1" and json.loads(item["metadata_json"]).get("content_schema_ref") == OWNER_SCHEMA:
            metadata = json.loads(item["metadata_json"])
            original = PreparedObject(item["object_type"], TypedId.parse(item["logical_id"]), TypedId.parse(item["version_id"]),
                item["size"], item["media_type"], item["schema_ref"], None, item["storage_locator"], metadata)
            receipt = control_body(store, original)
            if (receipt["data"].get("protocol") == "rpnh/material_admission/v1"
                    and receipt["data"].get("command_ref") == admission["owner_command_ref"]):
                continue
        raise RegistryConflict("purge contains an undeclared body or execution responsibility")
    return output_ref


def validate_purge_promotion(store, db, task_id, admission, command):
    baseline = assert_purge_ready(store, db, task_id, admission, command)
    if command["data"]["operation"] == "purge_carrier":
        item = exact_prepared(db, descriptor_store(store), task_id, baseline["carrier_ref"])
        try:
            descriptor_store(store).path_for_version(item.version_id).lstat()
        except FileNotFoundError:
            return
        else:
            raise RegistryConflict("PURGE_UNKNOWN: exact payload still exists before result publication")

