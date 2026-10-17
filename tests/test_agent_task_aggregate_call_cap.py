"""Synthetic native aggregate limits, persisted workers and accounting boundary."""
from dataclasses import replace
import json

import pytest

from cpn.rpnh import agent_tasks
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog
from cpn.rpnh.registry._registry import _RegistryCore


def _spec(tmp_path, **kwargs):
    return AgentTaskSpec(tmp_path / "run", "Synthetic request.",
        (AgentStage("main", "Complete the synthetic request."),),
        tmp_path / "selection.json", **kwargs)


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "2"])
def test_aggregate_requires_positive_integer(tmp_path, invalid):
    with pytest.raises(ValueError, match="aggregate_call_cap"):
        _spec(tmp_path, aggregate_call_cap=invalid)


@pytest.mark.parametrize("relative", [False, True])
def test_closed_worker_roundtrip_and_legacy_shape(tmp_path, relative):
    base = _spec(tmp_path)
    root = tmp_path if relative else None
    legacy = base.as_worker_document(document_root=root)
    assert legacy["schema_version"] == f"rpnh/agent_task_spec/v{6 if relative else 5}"
    assert "aggregate_call_cap" not in legacy
    assert AgentTaskSpec.from_worker_document(legacy, document_root=root) == base
    spec = replace(base, aggregate_call_cap=3)
    document = spec.as_worker_document(document_root=root)
    assert document == {**legacy, "aggregate_call_cap": 3,
        "schema_version": f"rpnh/agent_task_spec/v{15 if relative else 14}"}
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(document))
    assert AgentTaskSpec.from_worker_document(json.loads(path.read_text()), document_root=root) == spec
    for bad in ({**document, "unknown": True},
                {**legacy, "aggregate_call_cap": 3},
                {k: v for k, v in document.items() if k != "aggregate_call_cap"},
                {**document, "aggregate_call_cap": None},
                {**document, "aggregate_call_cap": True},
                {**document, "schema_version": f"rpnh/agent_task_spec/v{14 if relative else 15}"}):
        with pytest.raises(ValueError):
            AgentTaskSpec.from_worker_document(bad, document_root=root)
    if relative:
        with pytest.raises(ValueError, match="parent directory"):
            AgentTaskSpec.from_worker_document(document)
        with pytest.raises(ValueError, match="escapes"):
            AgentTaskSpec.from_worker_document(
                {**document, "owner_socket_relative_path": "../outside.sock"}, document_root=root)


def test_registered_v13_remains_exclusive_and_closed(tmp_path):
    spec = AgentTaskSpec(tmp_path / "run", "Synthetic public task.",
        (AgentStage("main", "Complete the task."),), None,
        max_parallel_nodes=1, owner_socket_path=tmp_path / "run/owner.sock",
        registered_execution_sources=(("default", "fixture"),))
    document = spec.as_worker_document(document_root=tmp_path)
    assert document["schema_version"] == "rpnh/agent_task_spec/v13"
    assert AgentTaskSpec.from_worker_document(document, document_root=tmp_path) == spec
    with pytest.raises(ValueError, match="exclusive"):
        replace(spec, aggregate_call_cap=1)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError, match="Additional properties"):
        AgentTaskSpec.from_worker_document({**document, "aggregate_call_cap": 1}, document_root=tmp_path)


@pytest.mark.parametrize("boundary,written", [
    ("precheck", False), ("precheck", True),
    ("compaction", False), ("compaction", True), ("normal", True)])
def test_persisted_aggregate_below_local_twelve_enforced_natively(
        tmp_path, monkeypatch, boundary, written):
    # Reuse the existing cap-closure assertions: current loop version, retained
    # provisional output, no false terminal publication, compaction accounting.
    import test_agent_loop_task_cap_existing_output as boundary_tests
    real_run = agent_tasks.run_agent_task
    real_start = agent_tasks.start_run
    observed = []
    cap = 2 if boundary == "compaction" else 1

    def start(module, registration, **kwargs):
        budget = kwargs["budgets"]
        assert [b["max_attempts"] for b in budget.budget_buckets] == [12]
        assert budget.ordinary_global_cap == budget.task_total_hard_cap == cap
        assert budget.terminal_quota == budget.finalization_budget == 0
        observed.append(budget)
        return real_start(module, registration, **kwargs)

    def persisted_run(original):
        spec = replace(original, max_attempts_per_stage=12, aggregate_call_cap=cap)
        path = tmp_path / "worker.json"
        path.write_text(json.dumps(spec.as_worker_document(document_root=tmp_path)))
        restored = AgentTaskSpec.from_worker_document(json.loads(path.read_text()), document_root=tmp_path)
        assert restored == spec
        return real_run(restored)

    monkeypatch.setattr(agent_tasks, "start_run", start)
    monkeypatch.setattr(boundary_tests, "run_agent_task", persisted_run)
    boundary_tests._run_boundary(tmp_path, monkeypatch, boundary, written=written)
    assert len(observed) == 1
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    assert core.event_store.actual_model_call_limit() == cap


@pytest.mark.parametrize("local,cap,effective", [(12, None, 12), (12, 99, 12), (None, 3, 3), (None, None, None)])
def test_effective_ceiling_only_tightens_native_budget(tmp_path, monkeypatch, local, cap, effective):
    from test_optional_context_compaction import _configure_offline_task, _completion_response

    class Port:
        def request_once(self, attempt):
            return _completion_response("Synthetic completion")
        def close(self):
            pass

    config = _configure_offline_task(tmp_path, monkeypatch, Port())
    spec = replace(_spec(tmp_path, max_attempts_per_stage=local, aggregate_call_cap=cap),
                   execution_config_path=config)
    result = agent_tasks.run_agent_task(spec)
    assert result["stop_reason"] == "terminal"
    core = _RegistryCore(spec.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    assert core.event_store.ordinary_model_call_limit() == effective
    assert core.event_store.actual_model_call_limit() == effective


def test_not_submitted_failure_is_registered_but_not_native_consumption(tmp_path, monkeypatch):
    from cpn.rpnh.llm_contracts import LLMInputPortFailure
    from cpn.rpnh.registry.checkpoint_reentry import committed_checkpoint_refs
    from test_optional_context_compaction import _configure_offline_task, _completion_response

    class Port:
        calls = 0
        def request_once(self, attempt):
            self.calls += 1
            if self.calls == 1:
                raise LLMInputPortFailure("adapter_not_submitted",
                    submission_state="not_submitted", failure_code="synthetic_unavailable")
            assert self.calls == 2
            return _completion_response("Synthetic recovery")
        def close(self):
            pass

    port = Port()
    config = _configure_offline_task(tmp_path, monkeypatch, port)
    spec = replace(_spec(tmp_path, aggregate_call_cap=1), execution_config_path=config)
    first = agent_tasks.run_agent_task(spec)
    assert first["stop_reason"] == "blocked_or_waiting"
    assert port.calls == 1  # No automatic retry of a not-submitted failure.
    core = _RegistryCore(spec.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    assert core.event_store.actual_model_call_counts() == (0, 0)
    failures = core.event_store.list_events_by_type(("llm_invocation_failed/v1",))
    assert len(failures) == 1 and failures[0].payload["submission_state"] == "not_submitted"
    selected = committed_checkpoint_refs(core)[0]
    second = agent_tasks.reopen_agent_task(spec, checkpoint_version_id=str(selected.version_id),
        command_id="synthetic-aggregate-recovery", reason="Explicit synthetic recovery of unsubmitted input")
    assert second["stop_reason"] == "terminal"
    core = _RegistryCore(spec.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    registered = sum(len(core.event_store.object_rows_by_type(f"llm_call_spec/v{v}")) for v in (1, 2, 3))
    evidence = {"first_result": first, "second_result": second, "registered_requests": registered,
        "native_counts": core.event_store.actual_model_call_counts(), "synthetic_port_calls": port.calls,
        "native_hard_cap": core.event_store.actual_model_call_limit(),
        "failures": [e.payload for e in failures]}
    (tmp_path / "accounting-boundary.json").write_text(json.dumps(evidence, indent=2, default=str))
    assert port.calls == registered == 2
    assert core.event_store.actual_model_call_counts() == (1, 0)
    # Existing explicit reentry grants another copy of the initial budget.
    # This field cannot enforce a cumulative registered-request experiment cap.
    assert core.event_store.actual_model_call_limit() == 2


@pytest.mark.parametrize("feature", ["plugins", "bindings", "program"])
@pytest.mark.parametrize("relative", [False, True])
def test_cap_worker_preserves_existing_authority_shapes(tmp_path, feature, relative):
    from test_tool_program_policy import _spec as program_spec
    if feature == "program":
        base = program_spec(tmp_path)
    elif feature == "bindings":
        base = replace(program_spec(tmp_path), tool_program_policy=None, managed_tool_policy=None)
    else:
        base = _spec(tmp_path, plugin_configuration={}, plugin_catalog_digest="0" * 64,
                     execution_profiles=(("secondary", tmp_path / "secondary.json"),))
    root = tmp_path if relative else None
    original = base.as_worker_document(document_root=root)
    spec = replace(base, aggregate_call_cap=5)
    doc = spec.as_worker_document(document_root=root)
    assert {k: v for k, v in doc.items() if k not in {"schema_version", "aggregate_call_cap"}} == {
        k: v for k, v in original.items() if k != "schema_version"}
    assert AgentTaskSpec.from_worker_document(json.loads(json.dumps(doc)), document_root=root) == spec
    if feature in {"bindings", "program"}:
        incomplete = {k: v for k, v in doc.items() if k != "plugin_catalog_digest"}
        with pytest.raises(ValueError):
            AgentTaskSpec.from_worker_document(incomplete, document_root=root)


def test_twenty_local_twelve_buckets_start_with_native_197_cap(tmp_path, monkeypatch):
    from cpn.rpnh.agent_workflows import AgentWorkflowArc as A, AgentWorkflowEndpoint as E
    from test_managed_result_readback import _explicit_graph
    from test_optional_context_compaction import _configure_offline_task

    class NoCalls:
        def request_once(self, attempt):
            raise AssertionError("startup-only fixture cannot call a model")
        def close(self):
            pass

    class StartupChecked(Exception):
        pass

    config = _configure_offline_task(tmp_path, monkeypatch, NoCalls())
    base = _explicit_graph(False)
    nodes = tuple(replace(base.nodes[0], node_id=f"n{i}",
        input_ports=(replace(base.nodes[0].input_ports[0], artifact_id=f"a{i}"),),
        output_ports=(replace(base.nodes[0].output_ports[0], artifact_id=f"a{i+1}"),)) for i in range(20))
    graph = replace(base, nodes=nodes,
        arcs=tuple(A(f"edge{i}", E(f"n{i}", "result"), E(f"n{i+1}", "request")) for i in range(19)),
        ingress=E("n0", "request"), egress=E("n19", "result"))
    spec = AgentTaskSpec(tmp_path / "run", "Synthetic twenty-node startup.", (), config,
        workflow_graph=graph, max_attempts_per_stage=12, aggregate_call_cap=197)
    original = agent_tasks.start_run
    observed = []

    def start(*args, **kwargs):
        budget = kwargs["budgets"]
        assert [b["max_attempts"] for b in budget.budget_buckets] == [12] * 20
        assert budget.ordinary_global_cap == budget.task_total_hard_cap == 197
        assert budget.terminal_quota == budget.finalization_budget == 0
        owner = original(*args, **kwargs)
        core = owner._core
        assert core.event_store.actual_model_call_limit() == 197
        assert core.event_store.ordinary_model_call_limit() == 197
        assert core.event_store.actual_model_call_counts() == (0, 0)
        owner.record_owner_stop(idempotency_key="synthetic-startup-checked")
        observed.append(True)
        raise StartupChecked

    monkeypatch.setattr(agent_tasks, "start_run", start)
    with pytest.raises(StartupChecked):
        agent_tasks.run_agent_task(spec)
    assert observed == [True]


@pytest.mark.parametrize("aggregate", [False, True], ids=["legacy", "aggregate"])
def test_compaction_retry_keeps_existing_closure_gate_and_accounting(tmp_path, monkeypatch, aggregate):
    from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
    from cpn.rpnh.llm_contracts import LLMInputPortFailure
    from test_managed_result_readback import _configure_pressure, _explicit_graph
    from test_optional_context_compaction import _response

    class Port:
        requests = 0
        def request_once(self, attempt):
            self.requests += 1
            envelope = json.loads(attempt.canonical_request_bytes)
            if self.requests == 1:
                return _response(text="Synthetic unfinished work.", finish_reason="stop")
            assert envelope["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
            if self.requests == 2:
                raise LLMInputPortFailure("provider_retryable", submission_state="not_submitted",
                                          failure_code="synthetic_retryable_compaction")
            assert self.requests == 3
            return _response(text="Synthetic compaction summary.", finish_reason="stop")
        def close(self):
            pass

    port = Port()
    config = _configure_pressure(tmp_path, monkeypatch, port)
    spec = AgentTaskSpec(tmp_path / "run", "Synthetic compaction retry.", (), config,
        workflow_graph=_explicit_graph(False), max_attempts_per_stage=12 if aggregate else 2,
        aggregate_call_cap=2 if aggregate else None)
    # This supported failed-input path already fails exact native settlement in
    # the legacy wrapper. Preserve its gate; repairing it is a separate change.
    from cpn.rpnh.registry.invocations import InvocationAdmissionError
    with pytest.raises(InvocationAdmissionError, match="failed compaction call lacks exact closure") as failure:
        agent_tasks.run_agent_task(spec)
    core = _RegistryCore(spec.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    registered = sum(len(core.event_store.object_rows_by_type(f"llm_call_spec/v{v}")) for v in (1, 2, 3))
    failures = core.event_store.list_events_by_type(("llm_invocation_failed/v1",))
    evidence = {"native_closure_error": str(failure.value), "aggregate": aggregate,
        "registered_requests": registered, "native_counts": core.event_store.actual_model_call_counts(),
        "native_hard_cap": core.event_store.actual_model_call_limit(),
        "synthetic_port_calls": port.requests, "failures": [e.payload for e in failures]}
    (tmp_path / "compaction-accounting.json").write_text(json.dumps(evidence, indent=2, default=str))
    assert not core.event_store.object_rows_by_type("run_terminal_evidence/v1")
    assert port.requests == registered == 3
    assert core.event_store.actual_model_call_counts() == (2, 0)
    assert core.event_store.actual_model_call_limit() == 2
    assert len(failures) == 1
    assert failures[0].payload["submission_state"] == "not_submitted"
