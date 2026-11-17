"""Original Module with one ordinary terminal and one installed idle manager.

The manager borrows and returns one original reusable execution slot along
declared PN arcs. Capacity comes from the authored initial marking, never a
later injected token, separate run or execution-state table.
"""
from dataclasses import replace

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.material_locations import (
    LOCATION_CONFIG_SCHEMA, LOCATION_EXECUTOR, location_host_bindings, register_location_execution)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import ArcDeclaration, InitialTokenDeclaration, PlaceDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.resources import BindResourceAddress, ResourceAddress
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run
from material_management_fixture import MaterialWorld, TEXT, TERMINAL, _no_dispatch


MODEL = "offline-material-terminal"
BUSINESS = "test/material-terminal-business/v1"


def terminal_registration(*, terminal_capable=False, idle_capacity=1):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {"$id": TEXT,
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    register_location_execution(registration)
    if terminal_capable:
        from cpn.rpnh.terminal_management import register_terminal_location_execution
        register_terminal_location_execution(registration)
    registration.register_executor(BUSINESS, _no_dispatch,
        identity={"implementation_id": "test.material_terminal_business", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
            "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.material_terminal_tool", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})

    def lower_manager(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment,
            places=(*fragment.places, PlaceDeclaration("idle", TEXT,
                channel="control", token_kind="agent_resource", capacity=idle_capacity,
                reusable=True, initial_tokens=(InitialTokenDeclaration(count=idle_capacity),))),
            arcs=(*fragment.arcs,
                ArcDeclaration("idle", "run", "input", mode="borrow"),
                ArcDeclaration("idle", "run", "output", mode="return",
                    outcome="complete", emit="content_less")))

    registration.register_component("terminal_capacity_manager", lower_manager,
        identity={"implementation_id": "test.terminal_capacity_manager", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    return registration


def terminal_world(path, *, terminal_capable=False, idle_capacity=1, repeatable_business=False):
    registration = terminal_registration(terminal_capable=terminal_capable, idle_capacity=idle_capacity)
    if terminal_capable:
        from cpn.rpnh.terminal_management import (terminal_location_host_bindings,
            TERMINAL_EXECUTOR, TERMINAL_CONFIG_SCHEMA)
    budget = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    document = {"schema_version": "rpnh/module_declaration/v1", "name": "InstalledTerminalManager",
        "components": [
            {"name": "business", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
             "config": {"input_modes": {"request": "read"}} if repeatable_business else {},
             "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                       {"name": "result", "direction": "output", "schema": TEXT}],
             "operations": [{"name": "run", "executor": BUSINESS, "inputs": ["request"],
                "outputs": ["result"], "request_port": None, "tools": [], "config": {},
                "budget_binding": budget, "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]},
            {"name": "manage", "key": "terminal_capacity_manager", "config_schema": CONFIG_SCHEMA_ID,
             "config": {}, "ports": [],
             "operations": [{"name": "run", "executor": TERMINAL_EXECUTOR if terminal_capable else LOCATION_EXECUTOR, "inputs": [],
                "outputs": [], "request_port": None, "tools": [], "config": {
                    "outcome_id": "complete", **({"idle_place": "manage.idle"} if terminal_capable else {})},
                "budget_binding": budget, "outcomes": [{"name": "complete", "products": []}]}]},
        ], "links": [],
        "entry": {"business": {"component": "business", "port": "request"}},
        "exit": {"result": {"component": "business", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "business", "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT, LOCATION_CONFIG_SCHEMA], "budgets": {},
        "budget_buckets": [{**budget, "max_attempts": 12}]}
    if terminal_capable:
        document["required_schemas"].append(TERMINAL_CONFIG_SCHEMA)
    schemas, types, paths = user_collaboration_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    original = OwnerInput(TEXT, canonical_json("business task"), "Native business input")
    owner = start_run(ModuleDeclaration.from_dict(document), registration, run_dir=path, catalog=catalog,
        task_input=original, entry_inputs={"business": original},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 12, 0, 12, 0),
        model_condition=MODEL, owner_statement="Native business plus idle manager",
        host_execution_bindings=terminal_location_host_bindings if terminal_capable else location_host_bindings,
        command_id="terminal-manager:start")
    owner.schema_gateway.bind_source_identity(source_id="terminal-manager-fixture", command_id="terminal:source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref,
        command_id="terminal:session")
    return MaterialWorld(owner, host, session)


def finish_business(world):
    owner = world.owner
    admitted = owner.admit("business.run", logical_tau=0, command_id="business:admit")
    execution = owner.start(admitted, command_id="business:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"business.result": (canonical_json("original business terminal result"),)},
        command_id="business:products")
    resource = outputs.outputs[0].resource_ref
    kernel, _repository = owner.operation_repository()
    context = execution.operation.canonical.context
    kernel.bind_address(context, BindResourceAddress(ResourceAddress(context.task_ref, "documents/terminal"),
        resource, context.operation_binding_ref, None, "business:binding"), reference_only=True)
    owner.succeed(outputs, command_id="business:success")
    return resource, execution, outputs


def submit_terminal_location(world, resource, key="terminal:bind", *, address="documents/after-terminal",
                             operation="bind", expected_binding_ref=None):
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    owner = world.owner
    executable, _structure, _marking = hydrate_module_runtime(owner._core)
    params = {"protocol": "rpnh/material_command/v1", "operation": operation, "target": "manage.run",
        "execution": {"task_ref": ref_payload(owner.identity.task_ref), "task_round_ref": ref_payload(owner.task_round_ref),
            "net_instance_ref": ref_payload(executable.net_ref)},
        "address": {"scope_ref": ref_payload(owner.identity.task_ref), "opaque_name": address},
        "expected_binding_ref": ref_payload(expected_binding_ref.as_version_ref()) if expected_binding_ref is not None else None}
    if operation == "bind":
        params["target_resource_ref"] = ref_payload(resource.as_version_ref())
    action = world.host.material_action(world.session, owner_command="material_submit", command_id=key, params=params)
    return owner.command("material_submit", params, command_id=key, collaboration_action=action)
