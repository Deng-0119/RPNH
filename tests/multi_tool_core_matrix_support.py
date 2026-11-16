"""Original Registry/Owner setup for the session's stdlib scheduler matrix.

The setup follows tests/test_managed_plugin_tools.py::_prepared_invocation, but
is independently importable without pytest. No original fixture is rewritten.
The pipe fixture replaces only unavailable AF_UNIX wake/listener construction.
Tool handlers run in real spawned processes; marker files are test instruments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import get_ident
import time

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.plugins import (
    BoundPlugin, ManagedPluginInvocationService, ManagedPluginToolAdapter,
    ManagedPluginToolCatalog, PluginCatalog, PluginDefinition, PluginOperation,
)
from cpn.plugins.api import canonical
from cpn.plugins.managed_scheduler import ManagedConflictDomain, ManagedSchedulerPolicy
from cpn.rpnh.control_server import RegistryGateway
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.run import OwnerInput, start_run
from multi_tool_offline_support import SessionPipeOwnerEventLoop


INPUT_SCHEMA = "application/session_matrix_input/v1"
OUTPUT_SCHEMA = "application/session_matrix_output/v1"
EXECUTOR = "tests/session-matrix-executor/v1"
TERMINAL = "tests/session-matrix-terminal/v1"


def observed_tool(context, arguments):
    """A real child with deterministic test gates and bounded arithmetic."""
    gate = Path(context.config["gate"])
    started = {"call_id": context.call_id, "pid": os.getpid(),
               "at_ns": time.monotonic_ns()}
    (gate / f"{context.call_id}.started.json").write_text(json.dumps(started))
    deadline = time.monotonic() + 15
    for name in arguments.get("wait_for", ()):
        while not (gate / name).exists():
            context.check_cancelled()
            if time.monotonic() > deadline:
                raise RuntimeError("test tool gate did not progress")
            time.sleep(0.005)
    if arguments.get("uncooperative"):
        # Exercise the original parent's cancellation and process-group reap.
        # No tool result is buffered and no cooperative cancellation reply races.
        while True:
            time.sleep(0.05)
    result = {"call_id": context.call_id, "value": arguments["value"],
              "pid": os.getpid(), "started_ns": started["at_ns"],
              "finished_ns": time.monotonic_ns()}
    (gate / f"{context.call_id}.finished.json").write_text(json.dumps(result))
    return result


def _unused_executor(**_kwargs):
    raise AssertionError("matrix drives admitted registered tools, not a model")


def _registration(managed):
    result = Registration()
    register_basic_components(result)
    result.register_schema(INPUT_SCHEMA, {
        "$schema": "http://json-schema.org/draft-07/schema#", "$id": INPUT_SCHEMA,
        "type": "object", "additionalProperties": False,
        "properties": {"marker": {"type": "string"}}, "required": ["marker"],
    })
    result.register_schema(OUTPUT_SCHEMA, {
        "$schema": "http://json-schema.org/draft-07/schema#", "$id": OUTPUT_SCHEMA,
        "type": "object",
    })
    result.register_executor(EXECUTOR, _unused_executor,
        identity={"implementation_id": "tests.session_matrix", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None})
    result.register_tool(TERMINAL, dict,
        identity={"implementation_id": "tests.session_matrix_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    ManagedPluginToolAdapter(managed).register(result)
    return result


def _module(managed, names):
    binding = {"bucket_id": "matrix", "budget_scope": "module",
               "finalization_scope": None}
    components = [{
        "name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
        "config": {},
        "ports": [
            {"name": "request", "direction": "input", "schema": INPUT_SCHEMA},
            {"name": "result", "direction": "output", "schema": OUTPUT_SCHEMA},
        ],
        "operations": [{
            "name": "run", "executor": EXECUTOR, "inputs": ["request"],
            "outputs": ["result"], "request_port": None,
            "tools": [tool.registration_key for tool in managed.tools],
            "config": {}, "budget_binding": binding,
            "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
        }],
    } for name in names]
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "SessionCoreMatrix",
        "components": components, "links": [],
        "entry": {name: {"component": name, "port": "request"} for name in names},
        "exit": {f"{name}_result": {"component": name, "port": "result"} for name in names},
        "terminal": {"key": TERMINAL,
            "source": {"component": names[0], "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, INPUT_SCHEMA, OUTPUT_SCHEMA],
        "budgets": {}, "budget_buckets": [{**binding, "max_attempts": len(names)}],
    })


class MatrixHost:
    def __init__(self, root, *, names=("caller",)):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.gate = self.root / "gate"
        self.gate.mkdir()
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "value": {"type": "integer"},
                "wait_for": {"type": "array", "items": {"type": "string"}},
                "uncooperative": {"type": "boolean"},
            }, "required": ["value"],
        }
        operations = tuple(PluginOperation(name, name, schema, {"type": "object"},
            observed_tool, effect=effect, timeout_seconds=20, max_result_bytes=2048)
            for name, effect in (
                ("read", "external_read"), ("write", "external_write"),
                ("other", "external_write"), ("unknown", "external_write"),
                ("good", "pure")))
        definition = PluginDefinition("session_matrix", "1", operations,
            config_schema={"type": "object", "additionalProperties": False,
                "properties": {"gate": {"type": "string"}}, "required": ["gate"]})
        self.selected = PluginCatalog((BoundPlugin(definition, {"gate": str(self.gate)}),))
        self.managed = ManagedPluginToolCatalog(self.selected,
            {f"{name}_value": f"session_matrix/{name}" for name in (
                "read", "write", "other", "unknown", "good")},
            admitted_effects=("pure", "external_read", "external_write"))
        entries = (
            ManagedConflictDomain(self.key("read_value"), "external_read", reads=("A",)),
            ManagedConflictDomain(self.key("write_value"), "external_write", writes=("A",)),
            ManagedConflictDomain(self.key("other_value"), "external_write", writes=("B",)),
            ManagedConflictDomain(self.key("good_value"), "pure"),
        )
        self.policy = ManagedSchedulerPolicy("managed_conflict_domains/v1", 2, entries)
        module = _module(self.managed, names)
        request = OwnerInput(INPUT_SCHEMA, canonical({"marker": "session-offline"}),
                             "session matrix registered input")
        self.owner = start_run(module, _registration(self.managed), run_dir=self.root / "run",
            task_input=request, entry_inputs={name: request for name in names},
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
                ("rpnh/module_declaration/v1",), len(names), 0, len(names), 0),
            model_condition="no-model", owner_statement="offline core matrix",
            command_id="session-matrix:fresh")
        self.executions = tuple(self.owner.start(self.owner.admit(
            f"{name}.run", logical_tau=0, command_id=f"matrix:{name}:admit"),
            command_id=f"matrix:{name}:start") for name in names)
        self.kernel, self.repository = self.owner.operation_repository()
        self.service = ManagedPluginInvocationService(
            self.owner, self.kernel, self.repository, self.managed)
        self.owner_thread = get_ident()
        self.callbacks = []
        self.after_finish = None
        self.loop = SessionPipeOwnerEventLoop(self.owner, self.root / "owner.sock")
        methods = {f"prepare_{index}": self._prepare(index) for index in range(len(names))}
        methods["finish"] = self.finish
        self.gateway = RegistryGateway(self.loop, methods)

    def key(self, name):
        return self.managed.declaration(name).registration_key

    def _prepare(self, index):
        def prepare(call):
            self.callbacks.append(("prepare", get_ident(), call.call_id))
            return self.service.prepare(call.name, execution=self.executions[index],
                call_id=call.call_id, arguments=call.arguments,
                registration_key=call.registration_key, scheduling_policy=self.policy)
        return prepare

    def finish(self, prepared, completion):
        call_id = json.loads(prepared.receipt_material)["call_id"]
        self.callbacks.append(("finish", get_ident(), call_id))
        result = self.service.finish(prepared, completion)
        if self.after_finish is not None:
            self.after_finish(call_id, result)
        return result

    def receipts(self):
        result = []
        core = self.owner._core
        for row in core.event_store.object_rows_by_type("resource_version/v1"):
            metadata = json.loads(row["metadata_json"])
            if "managed_plugin_call" in metadata.get("descriptors", {}):
                result.append(json.loads(core.object_store.read_registered(
                    core.get_version(row["version_id"]))))
        return result

    def started(self, call_id):
        return (self.gate / f"{call_id}.started.json").exists()

    def release(self, name):
        (self.gate / name).write_text("released")

    def close(self):
        self.loop.close()


def pump(loop, predicate, *, timeout=45):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("actual Owner/scheduler did not reach the test boundary")
        loop.dispatch_ready(timeout=0.01)


