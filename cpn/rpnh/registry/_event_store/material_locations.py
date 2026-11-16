"""Exact location actions interpreted from original Owner and firing facts.

These actions publish ordinary address bindings.  They neither copy a body nor
change a resource's availability.  A location operation has no body input,
workspace, tool, or business output resource; its declared empty outcome still
passes through the original registered completion and normal Success.
"""
from __future__ import annotations

import json

from ..event_store import RegistryConflict
from .collaboration_descriptors import descriptor_store, exact_prepared, exact_descriptor


LOCATION_PROTOCOL = "rpnh/material_location/v1"
LOCATION_ACTIONS = frozenset(("bind", "unbind"))
LOCATION_CONFIG_SCHEMA = "application/rpnh_material_location_config/v1"
LOCATION_EXECUTOR = "rpnh/material-location/v1"
LOCATION_IDENTITY = {"implementation_id": "rpnh.material_location", "revision": "v1"}


def is_location(data):
    return data.get("operation") in LOCATION_ACTIONS


def command_lineage(params):
    from .material_purge import is_purge, purge_lineage
    if is_purge(params):
        return purge_lineage(params)
    from .material_availability import is_availability
    from .human_viewer import is_read_material
    if is_availability(params):
        return [params["confirmed_plan_ref"]]
    if is_read_material(params):
        return [params["target_resource_ref"]]
    if not is_location(params):
        return [params["input_ref"]]
    return ([params["target_resource_ref"]] if params["operation"] == "bind" else []) + (
        [params["expected_binding_ref"]] if params["expected_binding_ref"] is not None else [])


def command_descriptors(command_id, params):
    from .material_control import COMMAND_PROTOCOL
    result = {"owner_control_kind": "command", "command_id": command_id,
              "owner_material_protocol": COMMAND_PROTOCOL}
    from .material_availability import is_metadata_operation
    if is_metadata_operation(params):
        result["owner_material_operation"] = params["operation"]
    else:
        result["owner_material_input_version_id"] = params["input_ref"]["version_id"]
    return result


def validate_location_control_schema(store, db, task_id, item, body, *, command_ref=None):
    """New actions use their actual frozen source, never the newer process schema."""
    from jsonschema import Draft7Validator, ValidationError
    from ..execution_child_closure import ClosureSnapshot, _evidence_schema
    from .material_control import OWNER_SCHEMA
    original = item if command_ref is None else exact_prepared(
        db, descriptor_store(store), task_id, command_ref)
    authority = original.metadata.get("content_schema_authority_ref")
    if (item.metadata.get("content_schema_ref") != OWNER_SCHEMA
            or item.metadata.get("content_schema_authority_ref") != authority):
        raise RegistryConflict("location control differs from its exact original schema authority")
    publication = db.execute("SELECT e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.version_id=?", (str(original.version_id),)).fetchone()
    ordinal = (publication["ordinal"] if publication is not None
               else db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
    document = _evidence_schema(ClosureSnapshot(store, db, task_id), authority, OWNER_SCHEMA,
        evidence_ordinal=ordinal, task_ref=original.metadata["task_ref"])
    protocol = body["data"]["protocol"]
    rules = [rule for rule in document.get("allOf", ())
        if rule.get("if", {}).get("properties", {}).get("data", {}).get("properties", {}).get("protocol", {}).get("const") == protocol]
    if len(rules) != 1:
        raise RegistryConflict("original frozen OwnerControl schema does not declare this location control")
    data = rules[0].get("then", {}).get("properties", {}).get("data", {})
    branches = data.get("oneOf", (data,))
    key = "operation" if body["kind"] == "command" else "result_kind"
    discriminator = body["data"][key]
    matches = [branch for branch in branches
        if (branch.get("properties", {}).get(key, {}).get("const") == discriminator
            or discriminator in branch.get("properties", {}).get(key, {}).get("enum", ()))]
    if len(matches) != 1:
        raise RegistryConflict("original frozen OwnerControl schema does not allow this location control")
    try:
        Draft7Validator(document).validate(body)
    except ValidationError as exc:
        raise RegistryConflict("original frozen OwnerControl schema rejects this location control") from exc


def validate_location_params(params):
    from .material_control import COMMAND_PROTOCOL, reference
    fields = {"protocol", "operation", "target", "execution", "address", "expected_binding_ref"}
    if params.get("operation") == "bind":
        fields.add("target_resource_ref")
    if (set(params) != fields or params.get("protocol") != COMMAND_PROTOCOL
            or params.get("operation") not in LOCATION_ACTIONS
            or not isinstance(params.get("target"), str) or not params["target"]):
        raise RegistryConflict("INVALID_COMMAND: unsupported location action or fields")
    execution = params["execution"]
    if not isinstance(execution, dict) or set(execution) != {"task_ref", "task_round_ref", "net_instance_ref"}:
        raise RegistryConflict("INVALID_COMMAND: exact location execution is required")
    for name, kind in (("task_ref", "task/v1"), ("task_round_ref", "task_round/v1"),
                       ("net_instance_ref", "net_instance/v1")):
        reference(execution[name], kind)
    address = params["address"]
    if (not isinstance(address, dict) or set(address) != {"scope_ref", "opaque_name"}
            or address["scope_ref"] != execution["task_ref"]
            or not isinstance(address["opaque_name"], str) or not address["opaque_name"]
            or "\x00" in address["opaque_name"]):
        raise RegistryConflict("SCOPE_NOT_ALLOWED: location action requires its exact task address")
    expected = params["expected_binding_ref"]
    if expected is not None:
        reference(expected, "resource_address_binding/v1")
    elif params["operation"] == "unbind":
        raise RegistryConflict("INVALID_COMMAND: unbind requires an exact live predecessor")
    if params["operation"] == "bind":
        reference(params["target_resource_ref"], "resource_version/v1")


def validate_location_baseline(store, db, task_id, params):
    """Read only the frozen exact baseline, never replace it with a new head."""
    from .material_control import TEXT_SCHEMA
    expected = params["expected_binding_ref"]
    previous = None
    if expected is not None:
        previous = exact_prepared(db, descriptor_store(store), task_id, expected).metadata
        if {name: previous[name] for name in ("scope_ref", "opaque_name")} != params["address"]:
            raise RegistryConflict("INVALID_COMMAND: location predecessor names another address")
    if params["operation"] == "unbind":
        if previous is None or previous["lifecycle_state"] != "bound":
            raise RegistryConflict("INVALID_COMMAND: unbind requires its exact live predecessor")
        resource = previous["resource_ref"]
        target = {"entity_type": "resource_version/v1", "logical_id": resource["resource_id"],
                  "version_id": resource["resource_version_id"]}
    else:
        target = params["target_resource_ref"]
    original = exact_prepared(db, descriptor_store(store), task_id, target)
    if (original.metadata.get("task_ref") != params["execution"]["task_ref"]
            or original.metadata.get("content_schema_ref") != TEXT_SCHEMA
            or original.media_type != "application/json"
            or original.metadata.get("origin_kind") != "petri_output"
            or original.producer_invocation_id is None
            or original.metadata.get("descriptors", {}).get("owner_input_role") is not None):
        raise RegistryConflict("SCOPE_NOT_ALLOWED: location target is not a canonical task text product")
    return target


def validate_location_spec(spec, binding):
    from .material_control import COMMAND_PROTOCOL
    from ...terminal_management import TERMINAL_EXECUTOR, validate_terminal_location_spec
    if spec.get("executor_key") == TERMINAL_EXECUTOR:
        validate_terminal_location_spec(spec, binding)
        return
    contracts = spec.get("implementation_contracts", {})
    if (spec.get("executor_key") != LOCATION_EXECUTOR
            or spec.get("implementation_identity") != LOCATION_IDENTITY
            or spec.get("transport") != "deterministic"
            or contracts.get("owner_material_commands") != COMMAND_PROTOCOL
            or contracts.get("owner_location_actions") != LOCATION_PROTOCOL
            or contracts.get("config_schema") != LOCATION_CONFIG_SCHEMA
            or spec.get("output_ports") or spec.get("allowed_tool_ids")
            or binding.get("workspace_binding_ref") is not None
            or binding.get("llm_input_target_ref") is not None
            or binding.get("output_binding_refs")):
        raise RegistryConflict("TARGET_UNAVAILABLE: location action lacks its exact registered empty operation")


def location_definition(store, db, task_id, admission, command):
    """Recover the configured outcome from the original adopted declaration."""
    from ...executable_net import load_compiled_net
    from .material_control import _committed_descriptor
    firing_row = db.execute("SELECT * FROM objects WHERE version_id=?",
        (admission["transition_firing_ref"]["version_id"],)).fetchone()
    if firing_row is None:
        raise RegistryConflict("location operation lacks its original firing")
    firing = _committed_descriptor(store, firing_row)
    objects = descriptor_store(store)
    binding = exact_descriptor(db, objects, task_id, firing["operation_binding_ref"])
    spec = exact_descriptor(db, objects, task_id, binding["operation_spec_ref"])
    validate_location_spec(spec, binding)
    net = exact_descriptor(db, objects, task_id, firing["net_instance_ref"])
    transitions = [exact_descriptor(db, objects, task_id, ref)
                   for ref in net["executable_transition_binding_refs"]]
    matching = [item for item in transitions if item["transition_id"] == command["target"]
                and item["operation_binding_ref"] == firing["operation_binding_ref"]]
    if len(matching) != 1 or matching[0].get("agent_ref") != firing.get("agent_ref"):
        raise RegistryConflict("location operation differs from its actual adopted transition")
    resource = matching[0]["declaration_resource_ref"]
    prepared = exact_prepared(db, objects, task_id, {"entity_type": "resource_version/v1",
        "logical_id": resource["resource_id"], "version_id": resource["resource_version_id"]})
    compiled = load_compiled_net(json.loads(objects.read_registered(prepared)))
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        raise RegistryConflict("location operation lacks its original compiled definition")
    defined, = definitions
    config = dict(defined.declaration.config)
    from ...terminal_management import TERMINAL_EXECUTOR, TERMINAL_IDENTITY, TERMINAL_CONFIG_SCHEMA
    terminal_variant = spec.get("executor_key") == TERMINAL_EXECUTOR
    identity = TERMINAL_IDENTITY if terminal_variant else LOCATION_IDENTITY
    executor = TERMINAL_EXECUTOR if terminal_variant else LOCATION_EXECUTOR
    config_schema = TERMINAL_CONFIG_SCHEMA if terminal_variant else LOCATION_CONFIG_SCHEMA
    store.catalog.validate_schema_ref(config_schema, config)
    outcomes = [item for item in defined.declaration.outcomes if item.name == config["outcome_id"]]
    if (defined.executor_declaration.get("identity") != identity
            or defined.executor_declaration.get("contracts") != spec["implementation_contracts"]
            or defined.declaration.executor != executor
            or defined.declaration.tools or defined.declaration.outputs
            or len(outcomes) != 1 or outcomes[0].products or outcomes[0].effects):
        raise RegistryConflict("location action requires its exact empty declared outcome")
    return firing, binding, spec, defined, config


def location_bindings(store, db, task_id, admission, command, completion):
    """Prove the one exact binding, without using any current display head."""
    from .material_control import _committed_descriptor, row_ref
    firing, _binding, spec, _defined, config = location_definition(store, db, task_id, admission, command)
    data = command["data"]
    validate_location_baseline(store, db, task_id, data)
    body = completion.payload
    if (body["ordered_outputs"] or body["workspace_revision_candidate_ref"] is not None
            or body["selected_outcome_id"] != config["outcome_id"]
            or body["operation_binding_ref"] != firing["operation_binding_ref"]
            or body["operation_spec_ref"] != spec["operation_spec_ref"]):
        raise RegistryConflict("location completion is not its original empty registered outcome")
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
        (admission["transition_firing_ref"]["version_id"],)).fetchone()
    rows = _original_member_objects(store, db, task_id, admission)
    rows = [row for row in rows if row["transaction_id"] != root["published_transaction_id"]]
    if any(row["ordinal"] >= completion.ordinal for row in rows):
        raise RegistryConflict("location preparation changed after its registered completion")
    bindings = [row for row in rows if row["object_type"] == "resource_address_binding/v1"]
    if len(bindings) != 1:
        raise RegistryConflict("location action requires one exact original address mutation")
    row, = bindings
    metadata = _committed_descriptor(store, row)
    resource = (None if data["operation"] == "unbind" else {
        "resource_id": data["target_resource_ref"]["logical_id"],
        "resource_version_id": data["target_resource_ref"]["version_id"]})
    if ({key: metadata[key] for key in ("scope_ref", "opaque_name")} != data["address"]
            or metadata["previous_binding_ref"] != data["expected_binding_ref"]
            or metadata["resource_ref"] != resource
            or metadata["lifecycle_state"] != ("tombstoned" if resource is None else "bound")
            or metadata["authorization_ref"] != firing["operation_binding_ref"]
            or row["producer_invocation_id"] != admission["invocation_ref"]["logical_id"]):
        raise RegistryConflict("location address mutation differs from its immutable command")
    # Location actions have no body, tool, workspace or child execution.  The
    # sole resource in the original admission is its scoped Owner receipt.
    allowed = {"transition_firing/v1", "invocation/v1", "firing_admission/v1",
               "operation_execution_lease/v1", "resource_address_binding/v1"}
    input_objects = _completed_petri_inputs(store, db, admission, completion, rows)
    from .material_control import OWNER_SCHEMA, control_body
    for item in rows:
        if item["object_type"] in allowed:
            continue
        if (item["object_type"] == "marking_delta/v1"
                and row_ref(item) == admission["claim_marking_delta_ref"]):
            continue
        if item["version_id"] in input_objects:
            continue
        if item["object_type"] == "resource_version/v1" and json.loads(item["metadata_json"]).get("content_schema_ref") == OWNER_SCHEMA:
            from ..models import PreparedObject
            from ..identities import TypedId
            prepared = PreparedObject(item["object_type"], TypedId.parse(item["logical_id"]),
                TypedId.parse(item["version_id"]), item["size"], item["media_type"], item["schema_ref"],
                None, item["storage_locator"], json.loads(item["metadata_json"]))
            receipt = control_body(store, prepared)
            if (receipt["data"].get("protocol") == "rpnh/material_admission/v1"
                    and receipt["data"].get("command_ref") == admission["owner_command_ref"]
                    and receipt["data"].get("admission_ref") == admission["firing_admission_ref"]):
                continue
        raise RegistryConflict("location operation contains an undeclared body or execution responsibility")
    events = db.execute("SELECT e.* FROM events e JOIN firing_temporary_members m "
        "ON m.member_kind='event' AND m.member_identity=e.event_id "
        "WHERE m.firing_version_id=? AND e.transaction_id=? "
        "AND e.event_type IN ('resource_address_bound/v1','resource_address_unbound/v1')",
        (admission["transition_firing_ref"]["version_id"], row["transaction_id"])).fetchall()
    from .address_promotion import _binding_event
    if len(events) != 1 or _binding_event(db, events[0])[0] != row_ref(row):
        raise RegistryConflict("location binding lacks its exact original address event")
    return [row_ref(row)]


def _original_member_objects(store, db, task_id, admission):
    """Discover the full original set before following any publication pointer."""
    from ..event_store import RegistryCorruptError
    firing_id = admission["transition_firing_ref"]["version_id"]
    members = db.execute("SELECT member_identity,transaction_id FROM firing_temporary_members "
        "WHERE firing_version_id=? AND member_kind='object'", (firing_id,)).fetchall()
    rows, verified_transactions = [], set()
    for member in members:
        row = db.execute("SELECT * FROM objects WHERE version_id=?", (member["member_identity"],)).fetchone()
        event = (None if row is None else db.execute("SELECT * FROM events WHERE event_id=?",
                 (row["published_event_id"],)).fetchone())
        transaction = (None if row is None else db.execute("SELECT * FROM transactions WHERE transaction_id=?",
                       (row["transaction_id"],)).fetchone())
        if (row is None or event is None or transaction is None
                or transaction["status"] != "committed" or transaction["task_id"] != str(task_id)
                or member["transaction_id"] != row["transaction_id"]):
            raise RegistryConflict("location original member publication is incomplete")
        metadata = json.loads(row["metadata_json"])
        expected = {key: row[key] for key in ("object_type", "logical_id", "version_id", "size",
                                            "media_type", "schema_ref", "storage_locator")}
        expected["metadata"] = metadata
        if (event["event_type"] != "object_version_published/v1" or event["task_id"] != str(task_id)
                or event["payload_schema_ref"] != "registry_v1/object_version_published/v1"
                or event["stream_id"] != "object:" + row["logical_id"]
                or event["aggregate_id"] != row["logical_id"] or event["aggregate_type"] != row["object_type"]
                or event["producer_principal"] != "framework"
                or event["producer_invocation_id"] != row["producer_invocation_id"]
                or event["transaction_id"] != row["transaction_id"]
                or json.loads(event["payload_json"]) != expected):
            raise RegistryConflict("location original member publication differs from its exact object")
        if row["transaction_id"] not in verified_transactions:
            try:
                store._verified_persisted_event_record(db, event)
            except RegistryCorruptError as exc:
                raise RegistryConflict("location original member transaction is not exact: " + str(exc)) from exc
            verified_transactions.add(row["transaction_id"])
        for kind, identity in (("object", row["version_id"]), ("event", event["event_id"]),
                               ("transaction", row["transaction_id"])):
            owners = db.execute("SELECT firing_version_id FROM firing_temporary_members "
                "WHERE member_kind=? AND member_identity=?", (kind, identity)).fetchall()
            if [owner[0] for owner in owners] != [firing_id]:
                raise RegistryConflict("location original member has inconsistent firing ownership")
        rows.append({**dict(row), "ordinal": event["ordinal"]})
    # Neither a missing object member nor a damaged publication pointer can
    # hide a body produced by this invocation or by one of its transactions.
    physical = db.execute("SELECT version_id FROM objects WHERE producer_invocation_id=? "
        "OR transaction_id IN (SELECT member_identity FROM firing_temporary_members "
        "WHERE firing_version_id=? AND member_kind='transaction')",
        (admission["invocation_ref"]["logical_id"], firing_id)).fetchall()
    if {row[0] for row in physical} != {row["version_id"] for row in rows}:
        raise RegistryConflict("location original object membership does not cover its complete facts")
    return rows


def _completed_petri_inputs(store, db, admission, completion, rows):
    """Retain the original Start's acknowledged PN inputs, never new I/O."""
    from .material_control import _committed_descriptor, row_ref
    start = db.execute("SELECT * FROM events WHERE event_id=?",
        (completion.payload["operation_start_event_id"],)).fetchone()
    if start is None or start["event_type"] != "operation_execution_started/v1":
        raise RegistryConflict("location completion lacks its actual Start")
    value = json.loads(start["payload_json"])
    if (value["transition_firing_ref"] != admission["transition_firing_ref"]
            or value["invocation_ref"] != admission["invocation_ref"]
            or value["operation_execution_lease_ref"] != admission["operation_execution_lease_ref"]):
        raise RegistryConflict("location input delivery belongs to another Start")
    kinds = {"resource_delivery/v1", "resource_release_witness/v1", "delivery_boundary_receipt/v1"}
    inputs = {row["version_id"]: (row, _committed_descriptor(store, row))
              for row in rows if row["object_type"] in kinds}
    if any(row["ordinal"] >= start["ordinal"] for row, _ in inputs.values()):
        raise RegistryConflict("location action cannot add a delivery after Start")
    deliveries = {}
    for row, data in inputs.values():
        if data["boundary"] != "petri_input":
            raise RegistryConflict("location action cannot deliver a material body")
        if row["object_type"] == "resource_delivery/v1":
            if (data["context_ref"] != admission["invocation_ref"]
                    or data["authorization_ref"] != value["operation_binding_ref"]
                    or data["resource_ref"] not in value["input_resource_refs"]):
                raise RegistryConflict("location delivery is outside its original PN inputs")
            deliveries.setdefault(data["delivery_id"], []).append((row, data))
    matched = set()
    for group in deliveries.values():
        stages = {data["state"]: (row, data) for row, data in group}
        if len(group) != 3 or set(stages) != {"prepared", "release_authorized", "acknowledged"}:
            raise RegistryConflict("location PN input has unresolved original delivery")
        prepared, p = stages["prepared"]
        released, r = stages["release_authorized"]
        terminal, t = stages["acknowledged"]
        witness = inputs.get(r["witness_ref"]["version_id"])
        boundary = inputs.get(t["boundary_receipt_ref"]["version_id"])
        if (witness is None or boundary is None
                or any(data[name] != p[name] for _, data in group
                       for name in ("context_ref", "resource_ref", "authorization_ref", "resource_byte_count", "purpose"))
                or r["previous_delivery_ref"] != row_ref(prepared)
                or t["previous_delivery_ref"] != row_ref(released)
                or witness[1]["delivery_ref"] != row_ref(prepared)
                or witness[1]["resource_ref"] != p["resource_ref"]
                or witness[1]["context_ref"] != p["context_ref"]
                or boundary[1]["delivery_ref"] != row_ref(released)
                or boundary[1]["witness_ref"] != row_ref(witness[0])
                or boundary[1]["outcome"] != "acknowledged"
                or boundary[1]["positive_byte_count"] != p["resource_byte_count"]):
            raise RegistryConflict("location PN input lacks its exact acknowledged original chain")
        matched.update(row["version_id"] for row, _ in group)
        matched.update((witness[0]["version_id"], boundary[0]["version_id"]))
    if matched != set(inputs):
        raise RegistryConflict("location PN input contains an unrelated release or receipt")
    return matched


__all__ = ("LOCATION_ACTIONS", "LOCATION_PROTOCOL", "LOCATION_EXECUTOR", "LOCATION_IDENTITY",
    "LOCATION_CONFIG_SCHEMA", "is_location", "command_lineage", "command_descriptors",
    "validate_location_params", "validate_location_baseline", "validate_location_spec",
    "location_definition", "location_bindings")
