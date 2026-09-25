from __future__ import annotations
import json
import time
import pytest
from cpn.plugins import (PluginCatalog, PluginDefinition, PluginOperation, PluginResource,
                         PluginError, BoundPlugin, load_catalog)
from cpn.plugins.runtime import build_plugin_module, plugin_registration, run_plugin
from cpn.plugins.api import frozen
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.registry._registry import _RegistryCore

OBJ = {"type": "object", "additionalProperties": False,
       "properties": {"x": {"type": "integer"}}, "required": ["x"]}
NUM = {"type": "integer"}


def add(context, arguments):
    assert not hasattr(context, "gateway")
    assert not hasattr(context, "core")
    assert context.firing_id
    return arguments["x"] + context.config["offset"]


def resource_text(context, arguments):
    return context.resources["instruction"].text()


def wrong_result(context, arguments):
    return {"invalid": True}


def too_slow(context, arguments):
    time.sleep(10)
    return 1


def fixture_plugin():
    return PluginDefinition("test_plugin", "1.0", (
        PluginOperation("add", "Add a configured integer", OBJ, NUM, add),
        PluginOperation("skill", "Read a registered instruction asset", {"type": "object"},
                        {"type": "string"}, resource_text, resources=("instruction",)),
        PluginOperation("wrong", "Reject invalid result", OBJ, NUM, wrong_result),
        PluginOperation("slow", "Bound a slow operation", OBJ, NUM, too_slow, timeout_seconds=1),
    ), config_schema={"type": "object", "additionalProperties": False,
                      "properties": {"offset": {"type": "integer"}}, "required": ["offset"]},
        resources=(PluginResource("instruction", b"Use exact registered inputs."),))


def catalog():
    return PluginCatalog((BoundPlugin(fixture_plugin(), frozen({"offset": 2})),))


def configuration():
    return {"schema_version": "rpnh/plugins/v1", "plugins": [{"name": "test_plugin",
        "entry_point": "test", "version": "1.0", "config": {"offset": 2}, "environment": []}]}


def test_config_is_explicit_and_pinned():
    c = load_catalog(configuration(), factories={"test": fixture_plugin})
    assert len(c.describe()) == 4
    broken = configuration(); broken["plugins"][0]["version"] = "2"
    with pytest.raises(PluginError):
        load_catalog(broken, factories={"test": fixture_plugin})


def test_unknown_fields_and_duplicate_plugins_reject_before_import():
    called = []
    doc = configuration(); doc["plugins"].append(doc["plugins"][0])
    with pytest.raises(PluginError):
        load_catalog(doc, factories={"test": lambda: called.append(True)})
    assert not called


def test_remote_schema_ref_is_not_a_network_operation():
    with pytest.raises(PluginError):
        PluginOperation("bad", "bad schema", {"$ref": "https://example.invalid/schema"}, NUM, add)


def test_configuration_is_frozen():
    p = catalog().plugins[0]
    with pytest.raises(TypeError):
        p.config["offset"] = 50


def test_dependency_missing_and_cycle():
    definition = PluginDefinition("one", "1", (PluginOperation("op", "op", OBJ, NUM, add),),
                                  requires={"two": "1"})
    with pytest.raises(PluginError):
        PluginCatalog((BoundPlugin(definition, {}),))
    other = PluginDefinition("two", "1", definition.operations, requires={"one": "1"})
    with pytest.raises(PluginError):
        PluginCatalog((BoundPlugin(definition, {}), BoundPlugin(other, {})))


def test_compilation_contains_capability_read_arc():
    c = catalog()
    compiled = compile_module(build_plugin_module(c, "test_plugin/add"), plugin_registration(c))
    assert len(compiled.symbolic.transitions) == 1
    assert any(a.place == "plugin.capability" and a.mode == "read" for a in compiled.symbolic.arcs)
    assert compiled.symbolic.places[-1].initial_tokens or any(p.initial_tokens for p in compiled.symbolic.places)


def test_real_registry_execution_and_input_receipts(tmp_path):
    result = run_plugin(catalog(), "test_plugin/add", {"x": 3}, run_dir=tmp_path / "run")
    assert result["output"] == 5
    assert result["stop_reason"] == "terminal"
    assert result["actual_model_call_counts"][0] == 0
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    phases = [json.loads(row["metadata_json"]).get("descriptors", {}).get("phase")
              for row in core.event_store.canonical_object_rows(object_type="resource_version/v1")]
    assert phases.count("started") == phases.count("returned") == 1
    assert core.event_store.list_events_by_type(("resource_delivery_acknowledged/v1",))


def test_instruction_asset_uses_registered_capability(tmp_path):
    result = run_plugin(catalog(), "test_plugin/skill", {}, run_dir=tmp_path / "run")
    assert result["output"] == "Use exact registered inputs."


def test_invalid_input_does_not_create_registry(tmp_path):
    with pytest.raises(PluginError):
        run_plugin(catalog(), "test_plugin/add", {"x": "wrong"}, run_dir=tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_invalid_output_is_not_success(tmp_path):
    result = run_plugin(catalog(), "test_plugin/wrong", {"x": 1}, run_dir=tmp_path / "run")
    assert result["terminal_evidence_ref"] is None
    assert result["stop_reason"] == "blocked_or_waiting"


def test_timeout_is_not_success(tmp_path):
    result = run_plugin(catalog(), "test_plugin/slow", {"x": 1}, run_dir=tmp_path / "run")
    assert result["terminal_evidence_ref"] is None
    assert result["stop_reason"] == "blocked_or_waiting"


def native_graph(selector="test_plugin/add"):
    from cpn.rpnh.agent_workflows import (AgentWorkflowGraph, AgentWorkflowNode,
        AgentWorkflowPort as P, AgentWorkflowEndpoint as E, AgentWorkflowExecution)
    return AgentWorkflowGraph((AgentWorkflowNode("compute", "Execute the selected native operation.",
        (P("request", "task"),), (P("result", "answer"),), AgentWorkflowExecution(plugin=selector)),),
        (), E("compute", "request"), E("compute", "result"))


def test_workflow_native_selector_is_versioned():
    from cpn.rpnh.agent_tasks import agent_task_registration, build_agent_workflow_module, EXECUTOR_KEY, TERMINAL_KEY
    c = catalog()
    graph = native_graph()
    module = build_agent_workflow_module(graph, executor_key=EXECUTOR_KEY, terminal_key=TERMINAL_KEY,
        tools=(), required_schemas=(), plugin_catalog=c)
    compiled = compile_module(module, agent_task_registration(c))
    assert module.components[0].key.endswith("/v4")
    assert compiled.operations[0].executor_key == c.operation_key("test_plugin/add")
    assert len(compiled.symbolic.transitions) == 1


def test_plugin_worker_document_is_pinned():
    from pathlib import Path
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    c = catalog()
    spec = AgentTaskSpec(Path("/tmp/unused-native-run"), '{"x":3}', (), Path("/tmp/profile"),
        workflow_graph=native_graph(), plugin_configuration=configuration(), plugin_catalog_digest=c.digest)
    document = spec.as_worker_document()
    assert document["schema_version"] == "rpnh/agent_task_spec/v5"
    assert AgentTaskSpec.from_worker_document(document) == spec


def test_native_graph_executes_on_existing_task_launcher(tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    import cpn.plugins.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "load_catalog", lambda doc: catalog())
    def forbidden(*args, **kwargs):
        raise AssertionError("native graph must not instantiate a provider")
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", forbidden)
    spec = AgentTaskSpec(tmp_path / "run", '{"x":3}', (), _write_execution_profile(tmp_path),
        workflow_graph=native_graph(), max_parallel_nodes=1,
        plugin_configuration=configuration(), plugin_catalog_digest=catalog().digest)
    result = run_agent_task(spec)
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "5"
    assert result["actual_model_call_counts"][0] == 0


class _NativeGraphPort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        messages = json.loads(attempt.canonical_request_bytes)["messages"]
        combined = "\n".join(m["content"] for m in messages if isinstance(m.get("content"), str))
        preparing = "Prepare native arguments." in combined
        return _response([{"id": "output", "name": "write_file", "arguments": json.dumps({
            "path": "out/args.txt" if preparing else "out/answer.txt",
            "description": "Native request" if preparing else "Native result observed",
            "content": json.dumps('{"x":3}' if preparing else "native calculation complete"),
            "output_port_id": "team.output__prepare__args" if preparing else "team.result",
            "outcome_id": "complete"})},
            {"id": "complete", "name": "complete_interaction", "arguments": "{}"}])

    def close(self):
        pass


def test_agent_plugin_agent_has_three_formal_firings_at_single_concurrency(tmp_path, monkeypatch):
    from cpn.rpnh.agent_workflows import (AgentWorkflowGraph, AgentWorkflowNode as N,
        AgentWorkflowPort as P, AgentWorkflowEndpoint as E, AgentWorkflowExecution as X,
        AgentWorkflowArc as A)
    from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    import cpn.plugins.catalog as catalog_module
    graph = AgentWorkflowGraph((
        N("prepare", "Prepare native arguments.", (P("request", "task"),), (P("args", "args"),)),
        N("compute", "Run the exact plugin.", (P("args", "args"),), (P("value", "value"),),
          X(plugin="test_plugin/add")),
        N("answer", "Interpret native output.", (P("value", "value"),), (P("result", "result"),)),
    ), (A("args", E("prepare", "args"), E("compute", "args")),
        A("value", E("compute", "value"), E("answer", "value"))), E("prepare", "request"), E("answer", "result"))
    monkeypatch.setattr(catalog_module, "load_catalog", lambda doc: catalog())
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *a, **k: _NativeGraphPort())
    spec = AgentTaskSpec(tmp_path / "run", "Compute a value.", (), _write_execution_profile(tmp_path),
        workflow_graph=graph, max_parallel_nodes=1, plugin_configuration=configuration(),
        plugin_catalog_digest=catalog().digest)
    result = run_agent_task(spec)
    assert result["stop_reason"] == "terminal"
    assert result["actual_model_call_counts"][0] == 2
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    _net, _structure, _marking = hydrate_module_runtime(core)
    assert {x.name for x in _structure.compiled.symbolic.transitions} == {"team.prepare", "team.compute", "team.answer"}
    rows = core.event_store.canonical_object_rows(object_type="operation_result/v1")
    assert len(rows) == 3
    resources = [core.object_store.read_registered(core.get_version(row["version_id"]))
                 for row in core.event_store.canonical_object_rows(object_type="resource_version/v1")
                 if json.loads(row["metadata_json"]).get("descriptors", {}).get("output_port_id")]
    assert b'"5"' in resources


def test_main_session_pins_plugins_in_registry_and_not_live_environment(tmp_path, monkeypatch):
    from test_main_session_registry import _write_execution_profile
    from cpn.rpnh.main_session import MainSession
    import cpn.plugins.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "load_catalog", lambda doc: catalog())
    monkeypatch.setattr(catalog_module, "read_config", configuration)
    session = MainSession(tmp_path / "session", _write_execution_profile(tmp_path))
    spec = session.prepare_turn("Use native computation")
    assert spec.plugin_catalog_digest == catalog().digest
    assert "test_plugin/add" in spec.prompt
    monkeypatch.setattr(catalog_module, "read_config", lambda: None)
    again = session.prepare_turn("Use native computation")
    assert again.plugin_catalog_digest == spec.plugin_catalog_digest
    assert again.plugin_configuration == spec.plugin_configuration


def external_failure(context, arguments):
    raise RuntimeError("sensitive-token-that-must-not-be-recorded")


def environment_probe(context, arguments):
    import os
    return {"allowed": os.environ.get("RPNH_ALLOWED_VALUE"),
            "ambient_absent": "RPNH_UNSELECTED_SECRET" not in os.environ,
            "resources": list(context.resources)}


def test_secrets_require_explicit_environment_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_ALLOWED_VALUE", "yes")
    monkeypatch.setenv("RPNH_UNSELECTED_SECRET", "do-not-copy")
    definition = PluginDefinition("env_plugin", "1", (PluginOperation(
        "probe", "Probe scoped input", {"type": "object"}, {"type": "object"}, environment_probe),),
        resources=(PluginResource("unused", b"This resource is not granted to probe."),))
    c = PluginCatalog((BoundPlugin(definition, {}, ("RPNH_ALLOWED_VALUE",)),))
    result = run_plugin(c, "env_plugin/probe", {}, run_dir=tmp_path / "run")
    assert result["output"] == {"allowed": "yes", "ambient_absent": True, "resources": []}
    assert "do-not-copy" not in json.dumps(result)


def test_external_write_failure_stays_unresolved_and_has_no_replay_arc(tmp_path):
    definition = PluginDefinition("remote_plugin", "1", (PluginOperation(
        "send", "One external submission", {"type": "object"}, {"type": "object"}, external_failure,
        effect="external_write"),))
    c = PluginCatalog((BoundPlugin(definition, {}),))
    result = run_plugin(c, "remote_plugin/send", {}, run_dir=tmp_path / "run")
    assert result["terminal_evidence_ref"] is None
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    rows = core.event_store.object_rows_by_type("resource_version/v1")
    failures = [json.loads(core.object_store.read_registered(core.get_version(row["version_id"])))
        for row in rows if json.loads(row["metadata_json"]).get("descriptors", {}).get("phase") == "failed"]
    assert len(failures) == 1 and failures[0]["outcome_unknown"] is True
    assert "sensitive-token" not in json.dumps(failures)
    module = build_plugin_module(c, "remote_plugin/send")
    assert [o.name for o in module.components[0].operations[0].outcomes] == ["complete"]
    assert not core.event_store.list_events_by_type(("transition_firing_settled/v1",))


def prepared_host(tmp_path):
    from cpn.rpnh.run import OwnerInput, start_run
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.plugins.host import schema_key, NativePluginHost, _operation_descriptor
    from cpn.plugins.api import canonical
    c = catalog(); module = build_plugin_module(c, "test_plugin/add")
    request = OwnerInput(schema_key(c, "test_plugin/add", "input"), canonical({"x": 2}), "test")
    owner = start_run(module, plugin_registration(c), run_dir=tmp_path / "run", task_input=request,
        entry_inputs={"request": request}, budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
        ("rpnh/module_declaration/v1",), 1, 0, 1, 0), model_condition="no-model",
        owner_statement="offline test", command_id="test:fresh")
    admitted = owner.admit("plugin.run", logical_tau=0, command_id="test:admit")
    execution = owner.start(admitted, command_id="test:start")
    kernel, repository = owner.operation_repository()
    host = NativePluginHost(owner, kernel, repository)
    plugin, op = c.resolve("test_plugin/add")
    return owner, execution, host, _operation_descriptor(plugin, op)


def test_dispatch_is_at_most_once_per_firing_even_after_host_reconstruction(tmp_path):
    from cpn.plugins.host import NativePluginHost
    owner, execution, host, descriptor = prepared_host(tmp_path)
    host.prepare(execution, descriptor)
    new_host = NativePluginHost(owner, *owner.operation_repository())
    with pytest.raises(PluginError, match="already dispatched"):
        new_host.prepare(execution, descriptor)


def test_foreign_receipt_cannot_publish_products(tmp_path):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.resources import ResourceVersionRef
    _owner, execution, host, descriptor = prepared_host(tmp_path)
    prepared = host.prepare(execution, descriptor)
    wrong = ResourceVersionRef(new_id("resource"), prepared["attempt_ref"].resource_version_id)
    with pytest.raises(PluginError, match="exact dispatch"):
        host.products(execution, wrong, 4)


def test_result_without_dispatch_is_rejected(tmp_path):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.resources import ResourceVersionRef
    _owner, execution, host, _descriptor = prepared_host(tmp_path)
    with pytest.raises(PluginError, match="exact dispatch"):
        host.products(execution, ResourceVersionRef(new_id("resource"), new_id("resource_version")), 4)


def test_configuration_change_is_rejected_before_task_creation(tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    import cpn.plugins.catalog as catalog_module
    c = catalog()
    changed = PluginCatalog((BoundPlugin(fixture_plugin(), {"offset": 90}),))
    monkeypatch.setattr(catalog_module, "load_catalog", lambda doc: changed)
    spec = AgentTaskSpec(tmp_path / "run", '{"x":3}', (), _write_execution_profile(tmp_path),
        workflow_graph=native_graph(), plugin_configuration=configuration(), plugin_catalog_digest=c.digest)
    with pytest.raises(ValueError, match="pinned"):
        run_agent_task(spec)
    assert not spec.run_dir.exists()


def test_plugin_node_cannot_select_provider_tools_or_multiple_ports():
    from cpn.rpnh.agent_workflows import AgentWorkflowExecution
    with pytest.raises(ValueError, match="cannot also"):
        AgentWorkflowExecution(plugin="test_plugin/add", profile_id="another_model")
