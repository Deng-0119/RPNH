"""Original Registry/PN fixture with separate material and control inputs."""
from __future__ import annotations

from copy import deepcopy

from material_management_fixture import MaterialWorld, TEXT, TERMINAL, _no_dispatch, command_ref
from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.material_availability import (
    register_material_availability, availability_host_bindings, run_availability_operation,
    CONFIG_SCHEMA, PLAN_SCHEMA, EFFECT_SCHEMA, EXECUTOR, PLAN_PROTOCOL,
)
from cpn.rpnh.material_locations import register_location_execution, LOCATION_EXECUTOR, LOCATION_CONFIG_SCHEMA
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.resources import BindResourceAddress, ResourceAddress
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run

MODEL = "offline-material-availability"
PRODUCER = "test/availability-producer/v1"


def availability_world(path, *, literal=False, ordinary_effect=False, human_read=False):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {"$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    register_material_availability(registration)
    register_location_execution(registration)
    if literal:
        from cpn.rpnh.material_execution import register_material_execution
        register_material_execution(registration)
    if human_read:
        from cpn.rpnh.human_viewer import register_human_viewer_read
        register_human_viewer_read(registration)
    registration.register_executor(PRODUCER, _no_dispatch,
        identity={"implementation_id": "test.availability_producer", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict, identity={"implementation_id": "test.availability_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    budget = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    producer = {"name": "producer", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
        "config": {"input_modes": {"request": "read"}},
        "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                  {"name": "result", "direction": "output", "schema": TEXT}],
        "operations": [{"name": "run", "executor": PRODUCER, "inputs": ["request"], "outputs": ["result"],
            "request_port": None, "tools": [], "config": {}, "budget_binding": budget,
            "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]}
    manager = {"name": "manager", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
        "config": {"input_modes": {"control": "read"}},
        "ports": [{"name": "control", "direction": "input", "schema": TEXT},
                  {"name": "effect", "direction": "output", "schema": EFFECT_SCHEMA}],
        "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["control"], "outputs": ["effect"],
            "request_port": None, "tools": [], "config": {"outcome_id": "complete", "output_port_id": "manager.effect"},
            "budget_binding": budget, "outcomes": [{"name": "complete", "products": [{"port": "effect"}]}]}]}
    location = {"name": "location", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
        "config": {"input_modes": {"control": "read"}},
        "ports": [{"name": "control", "direction": "input", "schema": TEXT}],
        "operations": [{"name": "run", "executor": LOCATION_EXECUTOR, "inputs": ["control"], "outputs": [],
            "request_port": None, "tools": [], "config": {"outcome_id": "complete"},
            "budget_binding": budget, "outcomes": [{"name": "complete", "products": []}]}]}
    reader = deepcopy(producer)
    reader["name"] = "reader"
    if ordinary_effect:
        reader["ports"][1]["schema"] = EFFECT_SCHEMA
    if literal:
        from cpn.rpnh.material_execution import MATERIAL_EXECUTOR, MATERIAL_TOOL, MATERIAL_CONFIG_SCHEMA
        producer["operations"][0].update(executor=MATERIAL_EXECUTOR, tools=[MATERIAL_TOOL],
            config={"workspace_relative_path": "material/note.txt",
                    "output_port_id": "producer.result", "outcome_id": "complete"})
    document = {"schema_version": "rpnh/module_declaration/v1", "name": "MaterialAvailability",
        "components": [producer, manager, location, reader],
        "links": [{"source": {"component": "producer", "port": "result"},
                   "target": {"component": "reader", "port": "request"}}],
        "entry": {"request": {"component": "producer", "port": "request"},
                  "control": {"component": "manager", "port": "control"},
                  "location": {"component": "location", "port": "control"}},
        "exit": {"result": {"component": "reader", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "reader", "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT, CONFIG_SCHEMA, PLAN_SCHEMA, EFFECT_SCHEMA, LOCATION_CONFIG_SCHEMA],
        "budgets": {}, "budget_buckets": [{**budget, "max_attempts": 30}]}
    if literal:
        document["required_schemas"].append(MATERIAL_CONFIG_SCHEMA)
    if human_read:
        from cpn.rpnh.human_viewer import READ_EXECUTOR, READ_CONFIG_SCHEMA
        viewer = deepcopy(location)
        viewer["name"] = "viewer_read"
        viewer["operations"][0]["executor"] = READ_EXECUTOR
        document["components"].append(viewer)
        document["entry"]["viewer_read_control"] = {"component": "viewer_read", "port": "control"}
        document["required_schemas"].append(READ_CONFIG_SCHEMA)
    schemas, types, paths = user_collaboration_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    task = OwnerInput(TEXT, canonical_json("control permission, not a target body"), "Native availability fixture")
    entries = {"request": task, "control": task, "location": task}
    bindings = availability_host_bindings
    if human_read:
        from cpn.rpnh.human_viewer import human_viewer_host_bindings
        entries["viewer_read_control"] = task
        bindings = human_viewer_host_bindings
    owner = start_run(ModuleDeclaration.from_dict(document), registration, run_dir=path, catalog=catalog,
        task_input=task, entry_inputs=entries,
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 30, 0, 30, 0),
        model_condition=MODEL, owner_statement="Native finite availability", host_execution_bindings=bindings,
        command_id="availability:start")
    owner.schema_gateway.bind_source_identity(source_id="availability-fixture", command_id="availability:source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref, command_id="availability:session")
    return MaterialWorld(owner, host, session)


def publish_initial(world, *, key="source", value="one exact canonical body", address="documents/A"):
    owner = world.owner
    admitted = owner.admit("producer.run", logical_tau=0, command_id=key + ":admit")
    execution = owner.start(admitted, command_id=key + ":start")
    outputs = owner.products(execution, outcome_id="complete", products={"producer.result": (canonical_json(value),)},
        command_id=key + ":products")
    resource = outputs.outputs[0].resource_ref
    if address is not None:
        kernel, _ = owner.operation_repository()
        context = execution.operation.canonical.context
        original = kernel._current_binding(ResourceAddress(context.task_ref, address), through_ordinal=owner._core.event_store.max_ordinal(), include_tombstone=True)
        kernel.bind_address(context, BindResourceAddress(ResourceAddress(context.task_ref, address), resource,
            context.operation_binding_ref, original, key + ":bind"), reference_only=True)
    owner.succeed(outputs, command_id=key + ":success")
    return resource


def prepare_plan(world, refs, key="disable:plan"):
    params = {"protocol": PLAN_PROTOCOL, "target_resource_refs": [ref_payload(r.as_version_ref()) if hasattr(r, "as_version_ref") else r for r in refs]}
    action = world.host.material_action(world.session, owner_command="prepare_material_disable_plan", command_id=key, params=params)
    return world.owner.command("prepare_material_disable_plan", params, command_id=key, collaboration_action=action)


def submit_disable(world, plan, key="disable"):
    owner = world.owner
    executable, _, _ = hydrate_module_runtime(owner._core)
    params = {"protocol": "rpnh/material_command/v1", "operation": "disable_versions", "target": "manager.run",
        "execution": {"task_ref": ref_payload(owner.identity.task_ref), "task_round_ref": ref_payload(owner.task_round_ref),
                      "net_instance_ref": ref_payload(executable.net_ref)}, "confirmed_plan_ref": plan["plan_ref"]}
    action = world.host.material_action(world.session, owner_command="material_submit", command_id=key, params=params)
    return owner.command("material_submit", params, command_id=key, collaboration_action=action)


def prepare_disable(world, accepted, key="disable"):
    owner = world.owner
    admitted = owner.admit("manager.run", logical_tau=0, command_id=key + ":admit", owner_command_ref=command_ref(accepted))
    execution = owner.start(admitted, command_id=key + ":start")
    return run_availability_operation(owner, execution)


def disable(world, refs, key="disable"):
    plan = prepare_plan(world, refs, key + ":plan")
    accepted = submit_disable(world, plan, key)
    outputs = prepare_disable(world, accepted, key)
    world.owner.succeed(outputs, command_id=key + ":success")
    return accepted, outputs

