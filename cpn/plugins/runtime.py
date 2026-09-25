"""Public native-plugin composition/launch helpers over the existing Harness."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import signal
import threading
from pathlib import Path

from .api import canonical, validate
from .catalog import PluginCatalog
from .host import (BINDING_SCHEMA, COMPONENT_KEY, TERMINAL_KEY,
                   operation_config, register_plugins, schema_key)


def build_plugin_module(catalog: PluginCatalog, selector: str):
    from cpn.rpnh.module import ModuleDeclaration
    catalog.resolve(selector)
    config = operation_config(catalog, selector)
    request_schema = schema_key(catalog, selector, "input")
    result_schema = schema_key(catalog, selector, "output")
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "NativePluginTask",
        "components": [{"name": "plugin", "key": COMPONENT_KEY,
            "config_schema": BINDING_SCHEMA, "config": config,
            "ports": [{"name": "request", "direction": "input", "schema": request_schema},
                      {"name": "result", "direction": "output", "schema": result_schema}],
            "operations": [{"name": "run", "executor": catalog.operation_key(selector),
                "inputs": ["request", "capability"], "outputs": ["result"],
                "request_port": None, "tools": [], "config": config,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "plugin", "budget_scope": "module",
                                   "finalization_scope": None}}]}],
        "links": [], "entry": {"request": {"component": "plugin", "port": "request"}},
        "exit": {"result": {"component": "plugin", "port": "result"}},
        "terminal": {"key": TERMINAL_KEY, "source": {"component": "plugin", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [BINDING_SCHEMA, request_schema, result_schema,
                             schema_key(catalog, selector, "capability")],
        "budgets": {}, "budget_buckets": [{"bucket_id": "plugin", "budget_scope": "module",
                                           "finalization_scope": None, "max_attempts": 1}],
    })


def plugin_registration(catalog):
    from cpn.rpnh.registration import Registration
    registration = Registration()
    register_plugins(registration, catalog)
    return registration


def run_plugin(catalog: PluginCatalog, selector: str, arguments, *, run_dir: Path):
    """Fresh native task. Exceptions/unknown results never become success or replay."""
    from cpn.rpnh.run import OwnerInput, start_run
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.control_server import OwnerEventLoop
    from cpn.components.execution_services import ExecutionServices
    from cpn.orchestrator.runner import Orchestrator
    from cpn.rpnh.registry.publication import _version_from_payload
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.strict_contracts import ref_payload
    if run_dir.exists():
        raise ValueError("native plugin run requires an absent run directory")
    _plugin, operation = catalog.resolve(selector)
    arguments = validate(operation.input_schema, arguments)
    module = build_plugin_module(catalog, selector)
    request = OwnerInput(schema_key(catalog, selector, "input"), canonical(arguments), "Native plugin request")
    owner = start_run(module, plugin_registration(catalog), run_dir=run_dir,
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="native-plugin-no-model", owner_statement="Owner-selected native plugin operation",
        command_id="rpnh:native-plugin:fresh")
    loop = OwnerEventLoop(owner, run_dir / "owner.sock")
    stop = threading.Event()
    runner = None
    def request_stop(_signal, _frame):
        stop.set()
        if runner is not None:
            runner.executor.request_owner_stop()
    previous = (signal.signal(signal.SIGINT, request_stop)
                if threading.current_thread() is threading.main_thread() else None)
    try:
        services = ExecutionServices(owner=owner, event_loop=loop, interruption_requested=stop.is_set)
        with ThreadPoolExecutor(max_workers=1) as pool:
            runner = Orchestrator(owner=owner, event_loop=loop,
                prepare_dispatcher=services.prepare_dispatcher, submit_operation=pool.submit,
                max_in_flight=1)
            if stop.is_set():
                runner.executor.request_owner_stop()
            result = runner.run()
        output = None
        if result.terminal_evidence_ref is not None:
            evidence = owner._core.get_version(result.terminal_evidence_ref.version_id).metadata
            ref = _version_from_payload(evidence["terminal_result_ref"])
            kernel, _ = owner.operation_repository()
            output = json.loads(kernel._read_registered(ResourceVersionRef(ref.entity_id, ref.version_id)))
        return {"schema_version": "rpnh/native_plugin_task_result/v1", "selector": selector,
                "catalog_digest": catalog.digest, "run_dir": str(run_dir),
                "stop_reason": result.stop_reason, "output": output,
                "terminal_evidence_ref": None if result.terminal_evidence_ref is None else ref_payload(result.terminal_evidence_ref),
                "actual_model_call_counts": list(owner._core.event_store.actual_model_call_counts())}
    finally:
        loop.close()
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
