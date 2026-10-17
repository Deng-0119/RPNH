"""Task-cap boundaries through the actual service, Registry and dispatcher.

All input responses and output bodies are local synthetic fixtures.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
from cpn.components.agent_loop.compaction import CompactionExecutionMixin
from cpn.components.agent_loop.loop_state import LoopStateMechanicsMixin
from cpn.components.agent_loop.models import (
    AgentLoopCompletion, AgentLoopExecutionBlock, AgentLoopState,
    AgentTaskModelCallCapHandoffCompletion,
)
from cpn.components.agent_loop.service import AgentLoopService
from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import TaskModelCallLimitExceeded
from test_optional_context_compaction import (
    _configure_offline_task, _objects, _response,
)
from test_managed_result_readback import _configure_pressure, _explicit_graph, _finish


@pytest.mark.parametrize("boundary", ["precheck", "compaction"])
@pytest.mark.parametrize("written", [False, True], ids=["empty", "written"])
def test_global_cap_preserves_unfinished_outputs(tmp_path, monkeypatch, boundary, written):
    _run_boundary(tmp_path, monkeypatch, boundary, written=written)


def test_normal_completion_at_last_call_remains_supported(tmp_path, monkeypatch):
    _run_boundary(tmp_path, monkeypatch, "normal", written=True)


def test_local_turn_cap_existing_output_control(tmp_path, monkeypatch):
    _run_boundary(tmp_path, monkeypatch, "local", written=True)


def _run_boundary(tmp_path, monkeypatch, boundary, *, written):
    class SyntheticInput:
        def __init__(self):
            self.requests = []

        def request_once(self, attempt):
            envelope = json.loads(attempt.canonical_request_bytes)
            self.requests.append(envelope)
            if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
                return _response(text="Synthetic partial work remains unfinished.", finish_reason="stop")
            assert len(self.requests) == 1, "cap must prevent another actor request"
            response = _finish()
            payload = json.loads(response)
            if boundary == "local":
                arguments = json.loads(payload["tool_calls"][0]["arguments"])
                arguments["output_port_id"] = "team.output__main__result"
                payload["tool_calls"][0]["arguments"] = json.dumps(arguments)
            calls = payload["tool_calls"] if written else []
            if boundary != "normal":
                calls = [call for call in calls if call["name"] != "complete_interaction"]
            return _response(tool_calls=calls, text="Synthetic draft only.", finish_reason="tool_calls" if calls else "stop")

        def close(self):
            pass

    port = SyntheticInput()
    config = (_configure_pressure(tmp_path, monkeypatch, port) if boundary == "compaction"
              else _configure_offline_task(tmp_path, monkeypatch, port))
    graph = _explicit_graph(False)
    if boundary == "local":
        from cpn.rpnh.agent_workflows import AgentWorkflowArc, AgentWorkflowEndpoint
        # A supported two-node graph provides two task calls, one turn per actor.
        # Forced turn-cap compaction uses the second call before local handoff.
        endpoint = AgentWorkflowEndpoint
        graph = replace(
            graph, nodes=(*graph.nodes, replace(
                graph.nodes[0], node_id="next",
                input_ports=(replace(graph.nodes[0].input_ports[0], artifact_id="result"),))),
            arcs=(AgentWorkflowArc("review", endpoint("main", "result"),
                                   endpoint("next", "request")),),
            egress=endpoint("next", "result"))
    spec = AgentTaskSpec(tmp_path / "run", "Synthetic cap check.", (), config,
                        workflow_graph=graph,
                        max_attempts_per_stage=2 if boundary == "compaction" else 1)
    returns, failures, executions = [], [], []
    settled_loops, compacted_loops = [], []
    original = AgentLoopService.run
    settle = AgentLoopService._settle_stored_turn
    compact = CompactionExecutionMixin.complete_agent_context_compaction_v1

    def observed_settlement(self, *args, **kwargs):
        result = settle(self, *args, **kwargs)
        settled_loops.append(result[0])
        return result

    def observed_compaction(self, *args, **kwargs):
        result = compact(self, *args, **kwargs)
        compacted_loops.append(result.waiting_loop)
        return result

    def observed(self, execution, **kwargs):
        executions.append(execution)
        try:
            completion = original(self, execution, **kwargs)
        except Exception as exc:
            failures.append({"type": type(exc).__name__, "message": str(exc)})
            raise
        returns.append(completion)
        return completion

    monkeypatch.setattr(AgentLoopService, "run", observed)
    monkeypatch.setattr(AgentLoopService, "_settle_stored_turn", observed_settlement)
    monkeypatch.setattr(CompactionExecutionMixin, "complete_agent_context_compaction_v1", observed_compaction)
    result = run_agent_task(spec)
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    events = [dict(event_type=e.event_type, payload=e.payload) for e in
              core.event_store.list_events_by_type(("agent_loop_terminal/v1",
                  "registered_operation_completion_recorded/v1", "run_terminal_evidence_recorded/v1",
                  "transition_firing_settled/v1"))]
    loops = _objects(core, "agent_loop/v1")
    actions = _objects(core, "agent_action/v2")
    publication = dict(core.event_store.firing_publication_row(
        executions[0].operation.firing.transition_firing_ref.version_id))
    counts = list(core.event_store.actual_model_call_counts())
    terminals = _objects(core, "run_terminal_evidence/v1")
    compactions = _objects(core, "agent_context_compaction/v3")
    evidence = {"boundary": boundary, "written": written, "result": result,
                "returns": [type(value).__name__ for value in returns], "failures": failures,
                "loops": loops, "actions": actions, "events": events,
                "publication": publication, "call_counts": counts,
                "request_count": len(port.requests), "terminal_evidence": terminals,
                "ordinary_call_limit": core.event_store.ordinary_model_call_limit(),
                "compactions": compactions,
                "settled_loop_versions": [value.loop_version_id for value in settled_loops],
                "compacted_loop_versions": [value.loop_version_id for value in compacted_loops]}
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    expected_calls = 2 if boundary in {"compaction", "local"} else 1
    assert len(port.requests) == counts[0] == expected_calls, evidence
    assert counts[1] == 0
    assert core.event_store.ordinary_model_call_limit() == expected_calls
    if boundary in {"compaction", "local"}:
        compaction, = compactions
        assert compaction["trigger_reason"] == ("turn_cap" if boundary == "local" else "context_pressure")
        assert port.requests[1]["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
    else:
        assert not compactions
    assert not failures, failures
    if boundary == "normal":
        assert isinstance(returns[0], AgentLoopCompletion)
        assert result["stop_reason"] == "terminal"
        assert terminals
        assert [a["tool_name"] for a in actions] == ["write_file", "complete_interaction"]
    elif boundary == "local":
        assert isinstance(returns[0], AgentLoopCompletion)
        assert returns[0].written_resource_refs
        assert any(e["payload"].get("terminal_reason") == "llm_turn_cap" for e in events)
        assert isinstance(returns[1], AgentTaskModelCallCapHandoffCompletion)
        assert result["stop_reason"] == "task_model_call_cap"
        assert not terminals
    elif written:
        completion, = returns
        assert isinstance(completion, AgentLoopExecutionBlock)
        assert completion.loop.state == AgentLoopState.WAITING_FOR_LLM
        loop_ref = LoopStateMechanicsMixin.loop_ref(completion.loop)
        current = core.event_store.latest_object_row(loop_ref.entity_id, object_type="agent_loop/v1")
        assert current["version_id"] == completion.loop.loop_version_id
        assert json.loads(current["metadata_json"]) == LoopStateMechanicsMixin.loop_document(completion.loop)
        assert completion.loop.written_resource_refs == settled_loops[0].written_resource_refs
        assert completion.loop.written_resource_refs
        if boundary == "compaction":
            assert completion.loop == compacted_loops[0]
            assert completion.loop.loop_version_id != settled_loops[0].loop_version_id
        else:
            assert completion.loop == settled_loops[0]
        assert completion.block_authority.boundary == "task_model_call_cap"
        assert completion.block_authority.block_kind == "framework_repair"
        assert isinstance(completion.bottom_error, TaskModelCallLimitExceeded)
        assert completion.block_authority.exact_error_ref == executions[0].operation_execution_lease_ref
        assert completion.block_authority.retry_not_before_utc is None
        assert loops[-1]["agent_loop_version_id"] == completion.loop.loop_version_id
        assert loops[-1]["written_resource_refs"]
        assert publication["state"] == "PROVISIONAL"
        assert not events and not terminals
        assert result["stop_reason"] == "blocked_or_waiting"
        assert [a["tool_name"] for a in actions] == ["write_file"]
    else:
        completion, = returns
        assert isinstance(completion, AgentTaskModelCallCapHandoffCompletion)
        assert not completion.loop.written_resource_refs
        assert result["stop_reason"] == "task_model_call_cap"
        assert not terminals
        assert any(e["payload"].get("terminal_reason") == "task_model_call_cap" for e in events)
