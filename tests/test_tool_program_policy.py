"""Public task declaration allows ordinary defaults beside an explicit program."""
from dataclasses import asdict, replace
import pytest
from cpn.plugins.controlled_script import IsolatedProgramBudget
from cpn.rpnh.agent_tasks import AgentTaskSpec
from cpn.rpnh.agent_workflows import (
    AgentWorkflowArc as A, AgentWorkflowEndpoint as E,
    AgentWorkflowExecution as X, AgentWorkflowGraph as G,
    AgentWorkflowNode as N, AgentWorkflowPort as P,
)


def _spec(tmp_path, *, paired=True):
    program_tools = (("complete_interaction", "read_tool_program_output", "run_tool_program", "write_file")
                     if paired else ("complete_interaction", "run_tool_program", "write_file"))
    graph = G((N("main", "Run an explicit program.", (P("request", "task"),),
                 (P("result", "result"),), X(tools=program_tools)),
               N("ordinary", "Keep the existing ordinary tool defaults.",
                 (P("request", "result"),), (P("result", "result"),))),
              (A("handoff", E("main", "result"), E("ordinary", "request")),),
              E("main", "request"), E("ordinary", "result"))
    return AgentTaskSpec(tmp_path / "run", "Declared program followed by ordinary actor.", (),
        tmp_path / "config.json", workflow_graph=graph, plugin_configuration={},
        plugin_catalog_digest="0" * 64,
        managed_bindings={"main": {"tools": {"echo": {"selector": "synthetic/echo"}}}},
        managed_tool_policy={"policy_id": "managed_pure_parallel/v1", "max_in_flight": 2},
        tool_program_policy={"profile_id": "linux_isolated_python/v1", "tools": ["echo"],
                             "budget": asdict(IsolatedProgramBudget())})


def test_program_policy_accepts_ordinary_default_node_and_roundtrips(tmp_path):
    spec = _spec(tmp_path)
    assert spec.workflow_graph.nodes[1].execution.tools is None
    assert AgentTaskSpec.from_worker_document(spec.as_worker_document()) == spec
    assert AgentTaskSpec.from_worker_document(
        spec.as_worker_document(document_root=tmp_path), document_root=tmp_path) == spec


def test_program_policy_requires_its_paired_callable_reader(tmp_path):
    with pytest.raises(ValueError, match="paired reader"):
        _spec(tmp_path, paired=False)


def test_program_policy_cannot_exceed_selected_shared_capacity(tmp_path):
    spec = _spec(tmp_path)
    with pytest.raises(ValueError, match="shared run capacity"):
        replace(spec, managed_tool_policy={"policy_id": "managed_pure_parallel/v1", "max_in_flight": 1})


def test_shared_scheduler_does_not_require_policy_resources_on_ordinary_node():
    from cpn.components.agent_loop.managed_execution import ManagedExecutionMixin
    from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy
    validated = []
    class OrdinaryNode(ManagedExecutionMixin):
        managed_scheduler_policy = ManagedSchedulerPolicy()
        def _execution(self, execution, loop):
            validated.append((execution, loop))
        def _context(self, loop):
            return loop
        def _managed_tool_bindings(self, context):
            return {}
        def _static(self, context, role):
            raise AssertionError("ordinary node has no managed HOST resource")
    assert OrdinaryNode().managed_agent_scheduler_v1("execution", "ordinary-loop") is None
    assert validated == [("execution", "ordinary-loop")]


def _echo(context, arguments):
    return arguments


def test_registered_mixed_graph_completes_ordinary_node_without_managed_resources(tmp_path, monkeypatch):
    import json
    from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
    from cpn.rpnh.agent_tasks import run_agent_task
    from test_optional_context_compaction import _configure_offline_task, _response
    class Provider:
        def __init__(self):
            self.requests = []
        def request_once(self, attempt):
            request = json.loads(attempt.canonical_request_bytes)
            self.requests.append(request)
            prefix = "Provider-writable semantic output_port_ids (exact): "
            port = next(line.removeprefix(prefix) for message in request["messages"]
                        if message["role"] == "system" for line in message["content"].splitlines()
                        if line.startswith(prefix))
            assert "," not in port and port != "none"
            return _response(tool_calls=[{"id":"write-"+str(len(self.requests)), "name":"write_file",
                "arguments":json.dumps({"path":"result.txt", "description":"Synthetic mixed-node result",
                    "content":"ordinary defaults verified", "output_port_id":port, "outcome_id":"complete"})},
                {"id":"done-"+str(len(self.requests)), "name":"complete_interaction", "arguments":"{}"}],
                finish_reason="tool_calls")
        def close(self):
            pass
    provider = Provider()
    selected = PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "1", (
        PluginOperation("echo", "echo", {"type":"object"}, {}, _echo),)), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _: selected)
    config = _configure_offline_task(tmp_path, monkeypatch, provider)
    spec = replace(_spec(tmp_path), execution_config_path=config, plugin_catalog_digest=selected.digest)
    result = run_agent_task(spec)
    (tmp_path/"evidence.json").write_text(json.dumps({"result":result,"requests":provider.requests},indent=2))
    assert result["stop_reason"] == "terminal", result
    assert len(provider.requests) == 2
    assert "run_tool_program" in [t["function"]["name"] for t in provider.requests[0]["tools"]]
    assert "run_tool_program" not in [t["function"]["name"] for t in provider.requests[1]["tools"]]
