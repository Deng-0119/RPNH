"""Real Linux isolation plus registered owner/broker; providers/data synthetic."""
from dataclasses import asdict
import json

import pytest

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.controlled_script import IsolatedProgramBudget
from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.registry._registry import _RegistryCore
from test_optional_context_compaction import _configure_offline_task, _response
from test_managed_plugin_tools import _managed_receipts

INTEGER = {"type": "object", "additionalProperties": False,
           "properties": {"value": {"type": "integer"}}, "required": ["value"]}
EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def double(context, arguments):
    return {"value": arguments["value"] * 2,
            "createdUniqueId": "PROGRAM-CREATED-" + str(arguments["value"]),
            "body": "unfiltered-business-output" * 150}


def save(context, arguments):
    return {"createdUniqueId": "DEPENDENT-CREATED", "value": arguments["value"]}


def known_failure(context, arguments):
    raise ValueError("synthetic known worker failure")


class ProgramProvider:
    def __init__(self, source):
        self.source, self.requests, self.pages = source, [], []
        self.parent = None

    def request_once(self, attempt):
        request = json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        if len(self.requests) == 1:
            return _response(tool_calls=[{"id": "program-parent", "name": "run_tool_program",
                "arguments": json.dumps({"source": self.source, "arguments": {}})}], finish_reason="tool_calls")
        results = [json.loads(m["content"]) for m in request["messages"] if m.get("role") == "tool"]
        self.parent = next(r for r in results if r["kind"] == "tool_program_result/v1")
        pages = [r for r in results if r["kind"] == "tool_program_output_page/v1"]
        if pages:
            self.pages = pages
            assert pages[-1]["source_status"] == self.parent["status"]
        offset = pages[-1]["next_offset_chars"] if pages else 0
        if not pages or offset is not None:
            return _response(tool_calls=[{"id": "read-parent-" + str(offset), "name": "read_tool_program_output",
                "arguments": json.dumps({"agent_action_ref": self.parent["agent_action_ref"],
                                          "output_resource_ref": self.parent["output_resource_ref"],
                                          "offset_chars": offset, "max_bytes": 10000})}], finish_reason="tool_calls")
        return _response(tool_calls=[{"id": "write-result", "name": "write_file", "arguments": json.dumps({
            "path": "result.json", "description": "Synthetic isolated-program observation",
            "content": "isolated program observed", "output_port_id": "team.result", "outcome_id": "complete"})},
            {"id": "complete-result", "name": "complete_interaction", "arguments": "{}"}], finish_reason="tool_calls")

    def close(self):
        pass


def _run(tmp_path, monkeypatch, source, *, expected_stop="terminal", external_write=False,
         max_frame_bytes=1800):
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowEndpoint as E, AgentWorkflowExecution,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    port = ProgramProvider(source)
    selected = PluginCatalog((BoundPlugin(PluginDefinition("program", "1", (
        PluginOperation("double", "double", INTEGER, {}, double, max_result_bytes=65536),
        PluginOperation("save", "save", INTEGER, {}, save,
                        effect="external_write" if external_write else "pure"),
        PluginOperation("fail", "known failure", EMPTY, {}, known_failure),
    )), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _: selected)
    effects = ["pure", "external_write"] if external_write else ["pure"]
    policy = {"policy_id": "managed_pure_parallel/v1", "max_in_flight": 2}
    if external_write:
        from cpn.plugins.managed_tools import ManagedPluginToolCatalog
        managed = ManagedPluginToolCatalog(selected, {
            "double_value": "program/double", "save_summary": "program/save",
            "known_failure": "program/fail"}, admitted_effects=effects)
        policy = {"policy_id": "managed_conflict_domains/v1", "max_in_flight": 2,
                  "conflict_domains": [
                      {"registration_key": d.registration_key, "effect": d.effect,
                       "reads": [], "writes": ["synthetic-result"] if d.effect == "external_write" else [],
                       "unknown": False} for d in managed.tools]}
    config = _configure_offline_task(tmp_path, monkeypatch, port)
    graph = AgentWorkflowGraph((N("main", "Run the admitted synthetic program.",
        (P("request", "task"),), (P("result", "result"),),
        AgentWorkflowExecution(tools=("complete_interaction", "read_tool_program_output",
                                     "run_tool_program", "write_file"))),),
        (), E("main", "request"), E("main", "result"))
    spec = AgentTaskSpec(tmp_path / "run", "Finish synthetic program results.", (), config,
        workflow_graph=graph, max_attempts_per_stage=40, plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {
            "double_value": {"selector": "program/double"},
            "save_summary": {"selector": "program/save"},
            "known_failure": {"selector": "program/fail"}}, "admitted_effects": effects}},
        managed_tool_policy=policy,
        tool_program_policy={"profile_id": "linux_isolated_python/v1",
            "tools": ["double_value", "known_failure", "save_summary"],
            "budget": asdict(IsolatedProgramBudget(wall_seconds=60, max_frame_bytes=max_frame_bytes,
                                                  max_calls=64, max_output_bytes=524288))})
    assert AgentTaskSpec.from_worker_document(spec.as_worker_document()) == spec
    assert AgentTaskSpec.from_worker_document(spec.as_worker_document(document_root=tmp_path), document_root=tmp_path) == spec
    result = run_agent_task(spec)
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    children = [json.loads(row["metadata_json"]) for row in core.event_store.object_rows_by_type(
        "agent_tool_program_call/v1")]
    program = [json.loads(row["metadata_json"]) for row in core.event_store.object_rows_by_type(
        "agent_tool_program_invocation/v1")]
    evidence = {"result": result, "requests": port.requests, "children": children,
                "program": program, "receipts": _managed_receipts(tmp_path / "run")}
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2, default=str))
    assert result["stop_reason"] == expected_stop, result
    if port.pages:
        output = json.loads("".join(p["content"] for p in port.pages))
    else:
        final = next(p for p in program if p["status"] != "admitted")
        registered = core.get_version(final["output_resource_ref"]["resource_version_id"])
        output = json.loads(core.object_store.read_registered(registered))
    return output, port, evidence


COMPOSE = '''import json
def full(value):
    if not isinstance(value, dict) or value.get('kind') != 'agent_tool_program_child_output_page/v1':
        return value
    parts = [value['content']]
    while value['next_offset_chars'] is not None:
        value = tools.read_result(value, value['next_offset_chars'], max_bytes=1800)
        parts.append(value['content'])
    return json.loads(''.join(parts))
rows = tools.parallel([{'key':'query-a','name':'double_value','args':{'value':1}},
                       {'key':'query-b','name':'double_value','args':{'value':3}}])
values = [full(row['value']) for row in rows if row['ok']]
selected = [v for v in values if v['value'] > 2]
saved = tools.call('dependent', 'save_summary', {'value':sum(v['value'] for v in selected)})
result({'selected':[v['createdUniqueId'] for v in selected], 'saved':saved})
'''


def test_actual_isolation_parallel_filter_dependent_and_complete_result_readback(tmp_path, monkeypatch):
    output, port, evidence = _run(tmp_path, monkeypatch, COMPOSE)
    assert output["status"] == "returned"
    assert output["aggregate"] == {"selected": ["PROGRAM-CREATED-3"],
                                   "saved": {"createdUniqueId": "DEPENDENT-CREATED", "value": 6}}
    assert len(output["children"]) == 3
    assert {c["logical_key"] for c in output["children"]} == {"query-a", "query-b", "dependent"}
    assert all(c["terminal_receipt_ref"] and c["status"] == "returned" for c in output["children"])
    assert all(c["output"]["body"] for c in output["children"] if c["logical_key"].startswith("query"))
    assert output["runtime_identity"]["profile_id"] == "linux_isolated_python/v1"
    assert len(port.pages) > 1
    assert len(evidence["program"]) == 2


def test_actual_isolation_known_failure_caught_then_dependent_call(tmp_path, monkeypatch):
    output, _, _ = _run(tmp_path, monkeypatch, '''try:
    tools.call('known', 'known_failure', {})
except ToolCallError as error:
    saved = tools.call('after-known', 'save_summary', {'value':7})
    result({'known_error':error.code, 'saved':saved})
''')
    assert output["status"] == "returned"
    assert output["aggregate"]["known_error"]
    assert [c["status"] for c in output["children"]] == ["failed", "returned"]


def test_actual_isolation_exception_retains_completed_child_and_readable_partial_result(tmp_path, monkeypatch):
    output, port, evidence = _run(tmp_path, monkeypatch, '''tools.call('before-exception', 'save_summary', {'value':9})
raise RuntimeError('synthetic script exception')
''')
    assert output["status"] == "failed"
    assert output["children"][0]["output"]["createdUniqueId"] == "DEPENDENT-CREATED"
    assert port.parent["status"] == "failed" and port.pages[-1]["source_status"] == "failed"
    assert len([c for c in evidence["children"] if c["status"] == "returned"]) == 1


@pytest.mark.parametrize("source", [
    "import socket\nsocket.socket()\nresult('impossible')",
    f"open({str(Path(__file__).resolve().parents[1] / 'README.md')!r}).read()\nresult('impossible')",
    "tools.call('illegal', 'workspace', {})\nresult('impossible')",
])
def test_actual_isolation_rejects_direct_network_host_file_and_undeclared_tool(tmp_path, monkeypatch, source):
    output, _, _ = _run(tmp_path, monkeypatch, source)
    assert output["status"] == "failed" and not output["children"]
    assert output["aggregate"] is None


def test_registered_program_unknown_blocks_new_calls_and_retains_every_accepted_child(tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    executed = []
    def worker(handler, packet, **kwargs):
        executed.append(packet["context"]["call_id"])
        if "uncertain" in packet["context"]["call_id"]:
            raise WorkerFailure("worker_observation_lost", observation_lost=True)
        return handler(None, packet["arguments"])
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    output, port, evidence = _run(tmp_path, monkeypatch, '''tools.parallel([
    {'key':'uncertain','name':'save_summary','args':{'value':1}},
    {'key':'independent','name':'double_value','args':{'value':2}}])
tools.call('after-unknown', 'save_summary', {'value':3})
result('impossible')
''', expected_stop="blocked_or_waiting", external_write=True)
    assert output["status"] == "outcome_unknown" and len(port.requests) == 1
    assert any(c["status"] == "outcome_unknown" for c in output["children"])
    assert {c["logical_key"] for c in output["children"]} == {"uncertain", "independent"}
    assert all(c["status"] not in {"planned", "started"} for c in output["children"])
    assert not any("after-unknown" in call for call in executed)
    assert len([p for p in evidence["program"] if p["status"] == "outcome_unknown"]) == 1


def test_registered_program_owner_stop_after_receipt_keeps_success_and_closed_parent(tmp_path, monkeypatch):
    import os
    import signal
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    finish = ManagedPluginInvocationService.finish
    def stopped(self, *args, **kwargs):
        result = finish(self, *args, **kwargs)
        os.kill(os.getpid(), signal.SIGINT)
        return result
    monkeypatch.setattr(ManagedPluginInvocationService, "finish", stopped)
    output, port, evidence = _run(tmp_path, monkeypatch, '''tools.call('before-stop', 'save_summary', {'value':9})
while True:
    pass
''', expected_stop="stopped_by_owner")
    assert output["status"] == "cancelled" and len(port.requests) == 1
    assert len(output["children"]) == 1
    assert output["children"][0]["status"] == "returned"
    assert output["children"][0]["output"]["createdUniqueId"] == "DEPENDENT-CREATED"
    assert len([p for p in evidence["program"] if p["status"] == "cancelled"]) == 1


@pytest.mark.parametrize("key", ["small-frame", "查" * 115])
def test_registered_program_small_page_budget_is_known_error_with_real_return_preserved(tmp_path, monkeypatch, key):
    source = '''try:
    tools.call(KEY, 'double_value', {'value':1})
except ToolCallError as error:
    result({'error':error.code})
'''.replace("KEY", repr(key))
    output, _, _ = _run(tmp_path, monkeypatch, source, max_frame_bytes=768)
    assert output["status"] == "returned"
    assert output["aggregate"] == {"error": "program_result_budget_too_small"}
    child, = output["children"]
    assert child["status"] == "returned" and child["terminal_receipt_ref"]
    assert child["error"] is None and child["output"]["createdUniqueId"] == "PROGRAM-CREATED-1"
