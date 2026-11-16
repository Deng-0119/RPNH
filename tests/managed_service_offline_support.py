"""Original managed-service fixtures independent of a test framework.

This follows the real Registration/Module/Owner setup in
test_managed_plugin_tools. It creates ordinary Registry authority and invokes
the original registered-tool entry; it does not emulate pytest or grant a new
permission. Callers choose deterministic test handlers, never real services.
"""
from __future__ import annotations

import json
from pathlib import Path

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.components.execution_services import invoke_registered_tool
from cpn.plugins import (
    BoundPlugin, ManagedPluginInvocationService, ManagedPluginToolAdapter,
    ManagedPluginToolCatalog, ManagedToolSelector, PluginCatalog,
    PluginDefinition, PluginOperation,
)
from cpn.plugins.api import canonical, json_copy
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.run import OwnerInput, start_run


OBJECT_INPUT = {
    "type": "object", "additionalProperties": False,
    "properties": {"value": {"type": "integer"}}, "required": ["value"],
}
INTEGER_OUTPUT = {"type": "integer"}
CALLER_INPUT = "application/managed_tool_caller_input/v1"
CALLER_OUTPUT = "application/managed_tool_caller_output/v1"
CALLER_EXECUTOR = "tests/managed-tool-caller-executor/v1"
CALLER_TERMINAL = "tests/managed-tool-caller-terminal/v1"


def managed_double(context, arguments):
    assert context.operation_id == "synthetic/double"
    assert context.call_id
    return arguments["value"] * 2


def _unused_caller_executor(**_kwargs):
    raise AssertionError("focused fixtures drive original Owner/tool commands")


def prepared_invocation(
        root, *, effect="pure", handler=managed_double,
        caller_input=None, caller_output=None, source_id=None,
        managed_catalog=None):
    """Admit and start one real original operation with a selected test tool.

    caller_input, when supplied, is (schema_id, schema, immutable JSON bytes).
    caller_output is (schema_id, schema). These select real declared module
    ports for publication tests; they never replace Registry verification.
    """
    if caller_input is None:
        caller_input = (CALLER_INPUT, {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": CALLER_INPUT, "type": "object", "additionalProperties": False,
            "properties": {"caller_marker": {"type": "string"}},
            "required": ["caller_marker"],
        }, canonical({"caller_marker": "not native plugin arguments"}))
    if caller_output is None:
        caller_output = (CALLER_OUTPUT, {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": CALLER_OUTPUT, "type": "object",
        })
    input_id, input_schema, input_payload = caller_input
    output_id, output_schema = caller_output

    if managed_catalog is None:
        definition = PluginDefinition("synthetic", "1", (
            PluginOperation("double", "Deterministic managed fixture",
                OBJECT_INPUT, INTEGER_OUTPUT, handler, effect=effect),
        ))
        selected = PluginCatalog((BoundPlugin(definition, {}),))
        managed = ManagedPluginToolCatalog(selected, (
            ManagedToolSelector("double_value", "synthetic/double"),
        ), admitted_effects=(effect,))
    else:
        managed = managed_catalog
        selected = None
    declaration = managed.declaration("double_value")
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(input_id, input_schema)
    registration.register_schema(output_id, output_schema)
    registration.register_executor(CALLER_EXECUTOR, _unused_caller_executor,
        identity={"implementation_id": "tests.managed_tool_caller", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None})
    registration.register_tool(CALLER_TERMINAL, dict,
        identity={"implementation_id": "tests.managed_tool_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    ManagedPluginToolAdapter(managed).register(registration)

    module = ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "ManagedToolCaller",
        "components": [{
            "name": "caller", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": input_id},
                {"name": "result", "direction": "output", "schema": output_id},
            ],
            "operations": [{
                "name": "run", "executor": CALLER_EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "request_port": None, "tools": [declaration.registration_key],
                "config": {},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "caller", "budget_scope": "module",
                                   "finalization_scope": None},
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "caller", "port": "request"}},
        "exit": {"result": {"component": "caller", "port": "result"}},
        "terminal": {"key": CALLER_TERMINAL,
            "source": {"component": "caller", "port": "result"},
            "operation": "run", "outcome": "complete",
            "config": {"run_outcome": "complete"}},
        "required_schemas": list(dict.fromkeys((CONFIG_SCHEMA_ID, input_id, output_id))),
        "budgets": {},
        "budget_buckets": [{"bucket_id": "caller", "budget_scope": "module",
                            "finalization_scope": None, "max_attempts": 1}],
    })
    catalog = None
    if source_id is not None:
        from cpn.rpnh.collaboration.schema_catalog import source_identity_schema_data
        from cpn.rpnh.registry.schema_catalog import SchemaCatalog
        schemas, types, paths = source_identity_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    request = OwnerInput(input_id, input_payload, "managed offline caller input")
    owner = start_run(module, registration, run_dir=Path(root) / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="no-model", owner_statement="managed offline interface fixture",
        command_id="test:managed:fresh", catalog=catalog)
    if source_id is not None:
        owner.schema_gateway.bind_source_identity(
            source_id=source_id, command_id="test:managed:source")
    admitted = owner.admit("caller.run", logical_tau=0, command_id="test:managed:admit")
    execution = owner.start(admitted, command_id="test:managed:start")
    kernel, repository = owner.operation_repository()
    service = ManagedPluginInvocationService(owner, kernel, repository, managed)
    return {"owner": owner, "execution": execution, "kernel": kernel,
            "repository": repository, "selected": selected, "managed": managed,
            "service": service, "declaration": declaration}


def invoke(prepared, *, service=None, call_id="provider-call-1", arguments=None):
    declaration = prepared["declaration"]
    return invoke_registered_tool(prepared["owner"], prepared["kernel"],
        prepared["repository"], prepared["execution"], declaration.registration_key,
        identity=json_copy(declaration.identity), contracts=json_copy(declaration.contracts),
        kwargs={"service": service or prepared["service"],
                "execution": prepared["execution"], "call_id": call_id,
                "arguments": {"value": 4} if arguments is None else arguments})


def receipts(run_dir):
    """Read only original managed receipt resources from the test Registry."""
    core = _RegistryCore(run_dir, create=False, read_only=True)
    documents = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        if "managed_plugin_call" not in metadata.get("descriptors", {}):
            continue
        registered = core.get_version(row["version_id"])
        documents.append(json.loads(core.object_store.read_registered(registered)))
    return documents

