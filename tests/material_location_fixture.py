"""Real original Module/Registry fixture for scoped address operations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from material_management_fixture import MaterialWorld, TEXT, TERMINAL, _no_dispatch
from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.material_locations import (
    LOCATION_CONFIG_SCHEMA, LOCATION_EXECUTOR, register_location_execution, location_host_bindings,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.resources import BindResourceAddress, ResourceAddress
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run


MODEL = "offline-material-location"
ORDINARY = "test/location-producer/v1"


def location_world(path, *, competitor=False):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {"$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "string"})
    register_location_execution(registration)
    registration.register_executor(ORDINARY, _no_dispatch,
        identity={"implementation_id": "test.location_producer", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.viewer_material_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    budget = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    document = {
        "schema_version": "rpnh/module_declaration/v1", "name": "MaterialLocations",
        "components": [
            {"name": "producer", "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
             "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                       {"name": "result", "direction": "output", "schema": TEXT}],
             "operations": [{"name": "run", "executor": ORDINARY, "inputs": ["request"],
                "outputs": ["result"], "request_port": None, "tools": [], "config": {},
                "budget_binding": budget,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]},
            {"name": "location", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
             "config": {"input_modes": {"request": "read"}},
             "ports": [{"name": "request", "direction": "input", "schema": TEXT}],
             "operations": [{"name": "run", "executor": LOCATION_EXECUTOR, "inputs": ["request"],
                "outputs": [], "request_port": None, "tools": [], "config": {"outcome_id": "complete"},
                "budget_binding": budget, "outcomes": [{"name": "complete", "products": []}]}]},
        ],
        "links": [{"source": {"component": "producer", "port": "result"},
                   "target": {"component": "location", "port": "request"}}],
        "entry": {"request": {"component": "producer", "port": "request"}},
        "exit": {"result": {"component": "producer", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "producer", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT, LOCATION_CONFIG_SCHEMA], "budgets": {},
        "budget_buckets": [{**budget, "max_attempts": 12}],
    }
    if competitor:
        def lower(config, context):
            original = lower_operation(config, context)
            return replace(original, arcs=tuple(replace(arc, weight=2)
                if arc.direction == "output" else arc for arc in original.arcs))
        registration.register_component("location_race_producer", lower,
            identity={"implementation_id": "test.location_race_producer", "revision": "v1"},
            contracts={"config_schema": CONFIG_SCHEMA_ID})
        document["components"][0]["key"] = "location_race_producer"
        contender = deepcopy(document["components"][1])
        contender["name"] = "contender"
        document["components"].append(contender)
        document["links"].append({"source": {"component": "producer", "port": "result"},
                                  "target": {"component": "contender", "port": "request"}})
    schemas, types, paths = user_collaboration_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    task = OwnerInput(TEXT, canonical_json("location task"), "Native location fixture")
    owner = start_run(ModuleDeclaration.from_dict(document), registration, run_dir=path, catalog=catalog,
        task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 12, 0, 12, 0),
        model_condition=MODEL, owner_statement="Native location actions",
        host_execution_bindings=location_host_bindings, command_id="location:start")
    owner.schema_gateway.bind_source_identity(source_id="location-fixture", command_id="location:source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref,
                                  command_id="location:session")
    return MaterialWorld(owner, host, session)


def publish_initial(world):
    owner = world.owner
    admitted = owner.admit("producer.run", logical_tau=0, command_id="source:admit")
    execution = owner.start(admitted, command_id="source:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"producer.result": (canonical_json("one exact canonical body"),)},
        command_id="source:products")
    resource = outputs.outputs[0].resource_ref
    kernel, _ = owner.operation_repository()
    context = execution.operation.canonical.context
    for name in ("documents/A", "documents/B"):
        kernel.bind_address(context, BindResourceAddress(ResourceAddress(context.task_ref, name),
            resource, context.operation_binding_ref, None, "source:bind:" + name), reference_only=True)
    owner.succeed(outputs, command_id="source:success")
    return resource


def binding_at(world, name):
    kernel, _ = world.owner.operation_repository()
    return kernel._current_binding(ResourceAddress(world.owner.identity.task_ref, name),
        through_ordinal=world.owner._core.event_store.max_ordinal(), include_tombstone=True)


def submit_location(world, key, *, action, name, expected=None, resource=None, target="location.run"):
    owner, host, session = world.owner, world.host, world.session
    executable, _structure, _marking = hydrate_module_runtime(owner._core)
    params = {"protocol": "rpnh/material_command/v1", "operation": action, "target": target,
        "execution": {"task_ref": ref_payload(owner.identity.task_ref),
                      "task_round_ref": ref_payload(owner.task_round_ref),
                      "net_instance_ref": ref_payload(executable.net_ref)},
        "address": {"scope_ref": ref_payload(owner.identity.task_ref), "opaque_name": name},
        "expected_binding_ref": None if expected is None else ref_payload(expected.as_version_ref())}
    if action == "bind":
        params["target_resource_ref"] = ref_payload(resource.as_version_ref())
    original = host.material_action(session, owner_command="material_submit", command_id=key, params=params)
    return owner.command("material_submit", params, command_id=key, collaboration_action=original)

