"""Independent registered-runtime acceptance; all providers/data are synthetic.

The execution barriers below establish ordering, never a wall-time speedup.
Evidence is saved beside each real Registry in pytest's assigned temp root.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Barrier, Event, Lock, Thread, get_ident
import time

import pytest

from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
from cpn.components.agent_loop.compaction import CompactionExecutionMixin
from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.managed_tools import ManagedPluginInvocationService
from cpn.plugins.worker import WorkerFailure
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.registry._registry import _RegistryCore
from test_managed_result_readback import (
    RecordingReadbackProvider, _configure_pressure, _explicit_graph, _finish,
    synthetic_output, synthetic_large_output,
)
from test_optional_context_compaction import _configure_offline_task, _response
from test_managed_plugin_tools import _managed_receipts


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
INTEGER = {"type": "object", "properties": {"value": {"type": "integer"}},
           "required": ["value"], "additionalProperties": False}
PURE = {"policy_id": "managed_pure_parallel/v1", "max_in_flight": 2}


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def _wait(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "synthetic barrier did not progress"
        time.sleep(0.005)


def barrier_output(context, arguments):
    """Real spawned handler. Gate files are test synchronization, not receipts."""
    gate = Path(context.config["gate"])
    (gate / (context.call_id + ".entered")).write_text(str(os.getpid()))
    _wait(lambda: (gate / (context.call_id + ".release")).exists())
    return (synthetic_output(None, {}) if context.call_id == "small-once"
            else synthetic_large_output(None, {}))


def identity_output(context, arguments):
    return arguments["value"]


def _catalog(gate=None, effects=("pure",)):
    if gate is not None:
        return PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "w7", tuple(
            PluginOperation(name, name, EMPTY, {}, barrier_output, max_result_bytes=65536)
            for name in ("small", "large")), config_schema={
                "type": "object", "properties": {"gate": {"type": "string"}},
                "required": ["gate"], "additionalProperties": False}), {"gate": str(gate)}),))
    return PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "w7", tuple(
        PluginOperation(effect, effect, INTEGER, {}, identity_output, effect=effect)
        for effect in effects)), {}),))


def _spec(tmp_path, monkeypatch, port, selected, *, pressure=False, policy=PURE,
          tools=None, effects=("pure",), graph=True):
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _: selected)
    config = (_configure_pressure(tmp_path, monkeypatch, port) if pressure else
              _configure_offline_task(tmp_path, monkeypatch, port))
    return AgentTaskSpec(
        tmp_path / "run", "Exercise exact synthetic registered results.",
        () if graph else (AgentStage("main", "Finish synthetic results."),), config,
        workflow_graph=_explicit_graph(True) if graph else None,
        max_attempts_per_stage=10, plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {name: {"selector": selector}
                                    for name, selector in (tools or {"pure": "synthetic/pure"}).items()},
                                    "admitted_effects": list(effects)}},
        managed_tool_policy=policy)


def _instrument(monkeypatch):
    evidence = {"threads": [], "settlements": [], "packets": [], "owners": [], "rebuilds": []}
    for cls, name in ((_RegistryCore, "begin"), (ManagedPluginInvocationService, "prepare"),
                      (ManagedPluginInvocationService, "finish")):
        original = getattr(cls, name)
        def call(self, *args, _original=original, _name=name, **kwargs):
            evidence["threads"].append((_name, get_ident()))
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, name, call)
    settle = AgentLoopMechanicalLifecycle.settle_action_batch
    def settled(self, *args, **kwargs):
        result = settle(self, *args, **kwargs)
        evidence["settlements"].append({
            "turn_id": kwargs["turn_id"], "loop_id": kwargs["loop"].loop_id,
            "before_revision": kwargs["loop"].revision, "after_revision": result[0].revision,
            "ordinals": [r.tool_call_ordinal for r in kwargs["records"]],
            "ids": [r.tool_call_id for r in kwargs["records"]]})
        return result
    monkeypatch.setattr(AgentLoopMechanicalLifecycle, "settle_action_batch", settled)
    init = OwnerEventLoop.__init__
    def initialized(self, *args, **kwargs):
        init(self, *args, **kwargs)
        evidence["owners"].append(self)
    monkeypatch.setattr(OwnerEventLoop, "__init__", initialized)
    complete = CompactionExecutionMixin.complete_agent_context_compaction_v1
    def rebuild(self, *args, **kwargs):
        result = complete(self, *args, **kwargs)
        fresh = AgentLoopMechanicalLifecycle(self.core, self.kernel)
        restored = fresh.hydrate_loop(fresh.loop_ref(result.waiting_loop))
        assert restored == result.waiting_loop
        assert fresh.latest_context_overlay(restored) is not None
        self.mechanical_lifecycle = fresh
        evidence["rebuilds"].append(restored.loop_id)
        return replace(result, waiting_loop=restored)
    monkeypatch.setattr(CompactionExecutionMixin, "complete_agent_context_compaction_v1", rebuild)
    return evidence


def _save(tmp_path, port, evidence, result):
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    objects = [{"ref": {"entity_type": r["object_type"], "logical_id": r["logical_id"],
                         "version_id": r["version_id"]}, "document": json.loads(r["metadata_json"])}
               for r in core.event_store.object_rows()]
    _write(tmp_path / "evidence.json", {
        "uid": os.getuid(), "python": __import__("sys").executable,
        "argv": __import__("sys").argv, "result": result,
        "requests": port.requests, "interrupted_requests": getattr(port, "interrupted_requests", []),
        "runtime": {k: v for k, v in evidence.items() if k != "owners"},
        "objects": objects, "receipts": _managed_receipts(tmp_path / "run"),
        "provider_interruptions": [e.payload for e in core.event_store.list_events_by_type(
            ("provider_attempt_owner_interrupted/v1",))]})
    return objects


def _actions(objects, kind="agent_action/v3"):
    return [o["document"] for o in objects if o["ref"]["entity_type"] == kind]


def _sole_writer(evidence):
    assert {thread for _, thread in evidence["threads"]} == {get_ident()}
    settlements = evidence["settlements"]
    assert len({s["turn_id"] for s in settlements}) == len(settlements)
    assert all(s["ordinals"] == list(range(len(s["ids"]))) for s in settlements)
    assert all(s["after_revision"] == s["before_revision"] + 1 for s in settlements)


@pytest.mark.parametrize("interrupt_state", [None, "not_submitted", "submission_unknown"])
def test_registered_process_overlap_pressure_pages_and_same_loop_rebuild(
        tmp_path, monkeypatch, caplog, interrupt_state):
    assert os.getuid() == 1000, "acceptance runs as normal WSL deng123"
    gate = tmp_path / "gate"
    gate.mkdir()
    selected = _catalog(gate)
    port = RecordingReadbackProvider(interrupt_state)
    spec = _spec(tmp_path, monkeypatch, port, selected, pressure=True,
                 tools={"small": "synthetic/small", "large": "synthetic/large"})
    evidence = _instrument(monkeypatch)
    from cpn.plugins import worker
    execute = worker.execute_worker
    def recorded(handler, packet, **kwargs):
        evidence["packets"].append(packet)
        return execute(handler, packet, **kwargs)
    monkeypatch.setattr(worker, "execute_worker", recorded)
    finish = ManagedPluginInvocationService.finish
    def release_first_after_second_receipt(self, prepared, completion):
        result = finish(self, prepared, completion)
        if json.loads(prepared.receipt_material)["call_id"] == "large-once":
            (gate / "small-once.release").touch()
        return result
    monkeypatch.setattr(ManagedPluginInvocationService, "finish", release_first_after_second_receipt)
    observer_errors = []
    def observe():
        try:
            # Startup includes real Registry initialization; only the worker
            # barriers (not setup latency) establish overlap.
            _wait(lambda: len(list(gate.glob("*.entered"))) == 2, seconds=90)
            owner, = evidence["owners"]
            def snapshot_during_wait():
                receipts = _managed_receipts(tmp_path / "run")
                assert {r["state"] for r in receipts} == {"started"}
                assert len(receipts) == 2
                return {"thread": get_ident(), "snapshot": owner.owner.snapshot()}
            evidence["responsive_owner"] = owner.submit_host(snapshot_during_wait).result(timeout=10)
            (gate / "large-once.release").touch()
        except BaseException as exc:
            observer_errors.append(repr(exc))
            for name in ("small-once", "large-once"):
                (gate / (name + ".release")).touch()
    thread = Thread(target=observe)
    thread.start()
    try:
        result = run_agent_task(spec)
    finally:
        for name in ("small-once", "large-once"):
            (gate / (name + ".release")).touch()
        thread.join(timeout=25)
    objects = _save(tmp_path, port, evidence, result)
    assert not thread.is_alive() and not observer_errors, observer_errors
    assert len({p.read_text() for p in gate.glob("*.entered")}) == 2
    assert Counter(p["context"]["call_id"] for p in evidence["packets"]) == {"small-once": 1, "large-once": 1}
    assert evidence["responsive_owner"]["thread"] == get_ident()
    _sole_writer(evidence)
    assert evidence["settlements"][0]["ids"] == ["small-once", "large-once"]
    receipts = _managed_receipts(tmp_path / "run")
    assert [r["call_id"] for r in receipts if r["state"] == "returned"] == ["large-once", "small-once"]
    assert port.requests[1]["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
    compactions = [o["document"] for o in objects if o["ref"]["entity_type"] == "agent_context_compaction/v3"]
    assert compactions[0]["trigger_reason"] == "context_pressure"
    assert evidence["rebuilds"]
    assert len(_actions(objects)) == 2
    assert all(a["outcome"] == "returned" for a in _actions(objects))
    if interrupt_state:
        assert result["stop_reason"] == "stopped_by_owner"
        assert len(port.requests) == 2
        assert port.interrupted_requests[0]["submission_state"] == interrupt_state
        interruption, = json.loads((tmp_path / "evidence.json").read_text())["provider_interruptions"]
        assert interruption["submission_state"] == interrupt_state
        assert "workspace_interruption_checkpoint" not in caplog.text
        if interrupt_state == "not_submitted":
            from cpn.rpnh.agent_tasks import resume_agent_task
            action_ref, receipt_ref = port.locators["large-once"]
            resumed_port = ScriptedProvider([[{
                "id": "cross-loop-read", "name": "read_managed_output", "arguments": json.dumps({
                    "agent_action_ref": action_ref, "terminal_receipt_ref": receipt_ref,
                    "offset_chars": 10000, "max_bytes": 10000})}]])
            monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *a, **k: resumed_port)
            resumed = resume_agent_task(spec)
            # Keep the initial stop evidence separate from new-invocation evidence.
            (tmp_path / "evidence.json").rename(tmp_path / "interruption-evidence.json")
            resumed_objects = _save(tmp_path, resumed_port, evidence, resumed)
            originals = _actions(resumed_objects)
            assert len(originals) == 2
            reads = [a for a in _actions(resumed_objects, "agent_action/v2") if a["tool_name"] == "read_managed_output"]
            assert len(reads) == 1
            assert reads[0]["agent_loop_ref"]["logical_id"] != originals[0]["agent_loop_ref"]["logical_id"]
            assert reads[0]["state"] == "ACTION_REJECTED"
            assert resumed["stop_reason"] == "terminal"
            assert len(evidence["packets"]) == 2
            assert len(_managed_receipts(tmp_path / "run")) == 4
    else:
        assert result["stop_reason"] == "terminal"
        assert port.normal_count == 3
        reads = [a for a in _actions(objects, "agent_action/v2") if a["tool_name"] == "read_managed_output"]
        assert len(reads) == 1 and "UNIQUE-LARGE-MIDDLE-ID" in reads[0]["result_metadata"]["content"]


def _call(value, *, name="pure", call_id=None):
    return {"id": call_id or f"call-{value}", "name": name, "arguments": json.dumps({"value": value})}


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
            return _response(text="Continue the explicit synthetic checks.", finish_reason="stop")
        if self.turns:
            return _response(tool_calls=self.turns.pop(0), finish_reason="tool_calls")
        return _finish()

    def close(self):
        pass


def _capacity_case(tmp_path, monkeypatch, *, graph):
    closing = json.loads(_finish())["tool_calls"]
    arguments = json.loads(closing[0]["arguments"])
    arguments["output_port_id"] = "team.result" if graph else "main.result"
    closing[0]["arguments"] = json.dumps(arguments)
    port = ScriptedProvider([[_call(i) for i in range(4)], closing])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), graph=graph)
    evidence = _instrument(monkeypatch)
    barrier = Barrier(2)
    active = 0
    peak = 0
    lock = Lock()
    dispatched = []
    def worker(handler, packet, **kwargs):
        nonlocal active, peak
        i = packet["arguments"]["value"]
        with lock:
            active += 1
            peak = max(active, peak)
            dispatched.append(i)
        barrier.wait(timeout=10)
        if i == 0:
            def inspect_pending():
                receipts = _managed_receipts(tmp_path / "run")
                assert {r["call_id"] for r in receipts} == {"call-0", "call-1"}
                assert {r["state"] for r in receipts} == {"started"}
                return get_ident()
            evidence["ping"] = evidence["owners"][0].submit_host(inspect_pending).result(timeout=10)
        barrier.wait(timeout=10)
        with lock:
            active -= 1
        return i
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "terminal"
    assert peak == 2 and sorted(dispatched) == [0, 1, 2, 3]
    assert evidence["ping"] == get_ident()
    assert [a["output"] for a in _actions(objects)] == [0, 1, 2, 3]
    _sole_writer(evidence)


def test_registered_capacity_ready_before_claim_and_owner_responsive(tmp_path, monkeypatch):
    _capacity_case(tmp_path, monkeypatch, graph=True)


def test_registered_stage_pure_capacity_and_sole_writer(tmp_path, monkeypatch):
    _capacity_case(tmp_path, monkeypatch, graph=False)


def test_registered_parameter_unknown_tool_known_failure_and_success(tmp_path, monkeypatch):
    port = ScriptedProvider([[_call("invalid"), _call(0, name="unregistered"), _call(1), _call(2)]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog())
    evidence = _instrument(monkeypatch)
    dispatched = []
    def worker(handler, packet, **kwargs):
        i = packet["arguments"]["value"]
        dispatched.append(i)
        if i == 1:
            raise WorkerFailure("handler_failed", may_have_executed=False)
        return i
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "terminal"
    assert sorted(dispatched) == [1, 2]
    assert [a["outcome"] for a in _actions(objects)] == ["rejected", "failed", "returned"]
    delivered = [m["tool_call_id"] for m in port.requests[1]["messages"] if m["role"] == "tool"]
    assert delivered == ["call-invalid", "call-0", "call-1", "call-2"]
    assert len(_managed_receipts(tmp_path / "run")) == 4
    _sole_writer(evidence)


@pytest.mark.parametrize("changed", [False, True])
def test_registered_cross_turn_provider_id_keeps_receipt_key(tmp_path, monkeypatch, caplog, changed):
    port = ScriptedProvider([[_call(1, call_id="reused")], [_call(2 if changed else 1, call_id="reused")]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog())
    evidence = _instrument(monkeypatch)
    dispatched = []
    def worker(handler, packet, **kwargs):
        dispatched.append(packet["arguments"])
        return packet["arguments"]["value"]
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    actions = _actions(objects)
    assert dispatched == [{"value": 1}]
    assert len(_managed_receipts(tmp_path / "run")) == 2
    if changed:
        assert result["stop_reason"] == "blocked_or_waiting"
        assert len(actions) == 1 and actions[0]["outcome"] == "returned"
        assert "ManagedPluginInvocationConflict" in caplog.text
        assert "managed call id was reused with different tool or arguments" in caplog.text
        assert len(port.requests) == 2
    else:
        assert result["stop_reason"] == "terminal"
        assert len(actions) == 2 and actions[0]["agent_action_id"] != actions[1]["agent_action_id"]
        assert actions[0]["terminal_receipt_ref"] == actions[1]["terminal_receipt_ref"]
        assert [a["output"] for a in actions] == [1, 1]
    _sole_writer(evidence)


@pytest.mark.parametrize("malformed", ["duplicate", "missing_name"])
def test_registered_malformed_native_identity_never_dispatches(tmp_path, monkeypatch, malformed):
    calls = [_call(1), _call(2)]
    if malformed == "duplicate":
        calls[1]["id"] = calls[0]["id"]
    else:
        del calls[1]["name"]
    port = ScriptedProvider([calls])
    spec = _spec(tmp_path, monkeypatch, port, _catalog())
    evidence = _instrument(monkeypatch)
    dispatched = []
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", lambda *a, **k: dispatched.append(1))
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert not dispatched and not _managed_receipts(tmp_path / "run")
    assert not _actions(objects)


def test_policy_worker_document_roundtrip_and_legacy_reader_catalog(tmp_path, monkeypatch):
    port = ScriptedProvider([])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), graph=False)
    for root, version in ((None, 9), (tmp_path, 10)):
        doc = spec.as_worker_document(document_root=root)
        assert doc["schema_version"] == f"rpnh/agent_task_spec/v{version}"
        rebuilt = AgentTaskSpec.from_worker_document(doc, document_root=root)
        assert rebuilt.as_worker_document(document_root=root) == doc
        legacy = replace(spec, managed_tool_policy=None).as_worker_document(document_root=root)
        assert legacy["schema_version"] == f"rpnh/agent_task_spec/v{version - 2}"
        assert "managed_tool_policy" not in legacy
        assert AgentTaskSpec.from_worker_document(legacy, document_root=root).as_worker_document(document_root=root) == legacy
    # Stage defaults preserve the old tool list even when policy is selected.
    from test_main_session_registry import _response as response
    def finish_stage(attempt):
        request = json.loads(attempt.canonical_request_bytes)
        port.requests.append(request)
        assert "read_managed_output" not in [t["function"]["name"] for t in request["tools"]]
        return response([{"id": "write", "name": "write_file", "arguments": json.dumps({
            "path": "out/result.txt", "description": "Synthetic", "content": json.dumps("done"),
            "output_port_id": "main.result", "outcome_id": "complete"})},
            {"id": "complete", "name": "complete_interaction", "arguments": "{}"}])
    monkeypatch.setattr(port, "request_once", finish_stage)
    assert run_agent_task(spec)["stop_reason"] == "terminal"


def test_registered_stage_unknown_lower_ordinal_retains_success_sibling(tmp_path, monkeypatch):
    from cpn.plugins.managed_tools import ManagedPluginInvocationReconciliationRequired
    port = ScriptedProvider([[_call(i) for i in range(4)]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), graph=False)
    evidence = _instrument(monkeypatch)
    barrier = Barrier(2)
    unknown = Event()
    dispatched = []
    def worker(handler, packet, **kwargs):
        i = packet["arguments"]["value"]
        dispatched.append(i)
        assert i < 2, "pending sibling must not dispatch after unknown"
        barrier.wait(timeout=10)
        if i == 0:
            raise RuntimeError("synthetic transport lost after claim")
        assert unknown.wait(10)
        return i
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    finish = ManagedPluginInvocationService.finish
    def finished(self, *args, **kwargs):
        try:
            return finish(self, *args, **kwargs)
        except ManagedPluginInvocationReconciliationRequired:
            unknown.set()
            raise
    monkeypatch.setattr(ManagedPluginInvocationService, "finish", finished)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "blocked_or_waiting"
    assert sorted(dispatched) == [0, 1] and len(port.requests) == 1
    actions = _actions(objects)
    assert [a["outcome"] for a in actions] == ["outcome_unknown", "returned", "rejected", "rejected"]
    assert actions[1]["output"] == 1 and actions[1]["terminal_receipt_ref"]
    assert all(a["started_receipt_ref"] is None and a["terminal_receipt_ref"] is None for a in actions[2:])
    assert len(_managed_receipts(tmp_path / "run")) == 4
    assert len(evidence["settlements"]) == 1
    _sole_writer(evidence)


def test_registered_stage_started_only_reconstruction_never_redispatches(tmp_path, monkeypatch):
    port = ScriptedProvider([[_call(0), _call(1)]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), graph=False,
                 policy=dict(PURE, max_in_flight=1))
    evidence = _instrument(monkeypatch)
    prepare = ManagedPluginInvocationService.prepare
    def lose_coordinator(self, *args, **kwargs):
        prepared = prepare(self, *args, **kwargs)
        with self._active_lock:
            self._active_calls.pop((id(self.core), prepared.receipt_key))
        rebuilt = ManagedPluginInvocationService(self.owner, self.kernel, self.repository, self.catalog)
        return prepare(rebuilt, *args, **kwargs)
    monkeypatch.setattr(ManagedPluginInvocationService, "prepare", lose_coordinator)
    dispatched = []
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", lambda *a, **k: dispatched.append(1))
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "blocked_or_waiting"
    assert dispatched == [] and len(port.requests) == 1
    assert [a["outcome"] for a in _actions(objects)] == ["outcome_unknown", "rejected"]
    assert [r["state"] for r in _managed_receipts(tmp_path / "run")] == ["started", "outcome_unknown"]
    _sole_writer(evidence)


def test_registered_stage_stop_after_terminal_preserves_success_and_cancels_pending(tmp_path, monkeypatch):
    import signal
    port = ScriptedProvider([[_call(i) for i in range(3)]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), graph=False,
                 policy=dict(PURE, max_in_flight=1))
    evidence = _instrument(monkeypatch)
    dispatched = []
    def worker(handler, packet, **kwargs):
        dispatched.append(packet["arguments"]["value"])
        return packet["arguments"]["value"]
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    finish = ManagedPluginInvocationService.finish
    def stop_after_receipt(self, *args, **kwargs):
        result = finish(self, *args, **kwargs)
        os.kill(os.getpid(), signal.SIGINT)
        return result
    monkeypatch.setattr(ManagedPluginInvocationService, "finish", stop_after_receipt)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "stopped_by_owner"
    assert dispatched == [0] and len(port.requests) == 1
    actions = _actions(objects)
    assert [a["outcome"] for a in actions] == ["returned", "rejected", "rejected"]
    assert actions[0]["output"] == 0 and actions[0]["terminal_receipt_ref"]
    assert all(a["started_receipt_ref"] is None for a in actions[1:])
    assert len(_managed_receipts(tmp_path / "run")) == 2
    _sole_writer(evidence)


@pytest.mark.parametrize("second", ["external_read", "external_write"])
def test_registered_conflict_domains_serialize_same_object_and_overlap_independent(tmp_path, monkeypatch, second):
    from cpn.plugins.managed_tools import ManagedPluginToolCatalog
    from cpn.plugins.managed_scheduler import ManagedConflictDomain, ManagedSchedulerPolicy
    effects = ("pure", "external_read", "external_write")
    selected = _catalog(effects=effects)
    tools = {e: "synthetic/" + e for e in effects}
    managed = ManagedPluginToolCatalog(selected, tools, admitted_effects=effects)
    policy = ManagedSchedulerPolicy("managed_conflict_domains/v1", 2, (
        ManagedConflictDomain(managed.declaration("external_write").registration_key,
                              "external_write", writes=("synthetic-object-A",)),
        ManagedConflictDomain(managed.declaration("external_read").registration_key,
                              "external_read", reads=("synthetic-object-A",)),
        ManagedConflictDomain(managed.declaration("pure").registration_key, "pure"),
    )).identity()
    port = ScriptedProvider([[_call(0, name="external_write"), _call(1, name=second), _call(2)]])
    spec = _spec(tmp_path, monkeypatch, port, selected, policy=policy, tools=tools, effects=effects)
    evidence = _instrument(monkeypatch)
    barrier = Barrier(2)
    returned = Event()
    starts = []
    def worker(handler, packet, **kwargs):
        i = packet["arguments"]["value"]
        starts.append(i)
        if i in (0, 2):
            barrier.wait(timeout=10)
        else:
            assert returned.is_set(), "conflicting reader/writer entered before first terminal"
        return i
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    finish = ManagedPluginInvocationService.finish
    def finished(self, prepared, completion):
        result = finish(self, prepared, completion)
        if json.loads(prepared.receipt_material)["call_id"] == "call-0":
            returned.set()
        return result
    monkeypatch.setattr(ManagedPluginInvocationService, "finish", finished)
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "terminal"
    assert starts.index(2) < starts.index(1)
    assert [a["output"] for a in _actions(objects)] == [0, 1, 2]
    states = [(r["call_id"], r["state"]) for r in _managed_receipts(tmp_path / "run")]
    assert states.index(("call-0", "returned")) < states.index(("call-1", "started"))
    _sole_writer(evidence)


@pytest.mark.parametrize("policy", [None, PURE], ids=["legacy", "selected"])
def test_mixed_managed_write_complete_preserves_serial_parity(tmp_path, monkeypatch, policy):
    from cpn.plugins.managed_scheduler import ManagedToolScheduler
    closing = json.loads(_finish())["tool_calls"]
    port = ScriptedProvider([[_call(7), *closing]])
    spec = _spec(tmp_path, monkeypatch, port, _catalog(), policy=policy)
    evidence = _instrument(monkeypatch)
    invoked = []
    invoke = ManagedPluginInvocationService.invoke
    def legacy(self, *args, **kwargs):
        invoked.append(args[0])
        return invoke(self, *args, **kwargs)
    monkeypatch.setattr(ManagedPluginInvocationService, "invoke", legacy)
    monkeypatch.setattr(ManagedToolScheduler, "run", lambda *a, **k: pytest.fail("mixed control turn entered scheduler"))
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", lambda handler, packet, **k: packet["arguments"]["value"])
    result = run_agent_task(spec)
    objects = _save(tmp_path, port, evidence, result)
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "managed survival verified"
    assert invoked == ["pure"] and len(port.requests) == 1
    assert evidence["settlements"][0]["ids"] == ["call-7", "write-result", "complete-result"]
    assert _actions(objects)[0]["output"] == 7
    assert [a["tool_name"] for a in _actions(objects, "agent_action/v2")] == ["write_file", "complete_interaction"]
    _sole_writer(evidence)


def test_pressure_compaction_consumes_last_call_budget_then_closes_current_head(tmp_path, monkeypatch):
    """One tool request + one compaction exhaust the real task call budget."""
    from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService

    selected = PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "w7cap", (
        PluginOperation("small", "Synthetic cap evidence", EMPTY, {}, synthetic_output),)), {}),))
    port = ScriptedProvider([[{"id": "cap-small-once", "name": "small", "arguments": "{}"}]])
    spec = replace(_spec(tmp_path, monkeypatch, port, selected, pressure=True,
                         tools={"small": "synthetic/small"}), max_attempts_per_stage=2)
    evidence = _instrument(monkeypatch)
    evidence["handler_counts"] = []
    evidence["cap_handoffs"] = []
    evidence["post_compaction_budget"] = []
    complete = CompactionExecutionMixin.complete_agent_context_compaction_v1
    def completed_compaction(self, *args, **kwargs):
        result = complete(self, *args, **kwargs)
        evidence["post_compaction_budget"].append({
            "loop_version_id": result.waiting_loop.loop_version_id,
            "revision": result.waiting_loop.revision,
            "actual_model_call_counts": self.core.event_store.actual_model_call_counts(),
            "ordinary_call_limit": self.core.event_store.ordinary_model_call_limit()})
        return result
    monkeypatch.setattr(CompactionExecutionMixin, "complete_agent_context_compaction_v1", completed_compaction)
    def worker(handler, packet, **kwargs):
        evidence["handler_counts"].append(packet["context"]["call_id"])
        return handler(None, packet["arguments"])
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    handoff = OptionalAgentLoopRegistryService.handoff_agent_task_model_call_cap_v1
    def observed_handoff(self, execution, loop, **kwargs):
        current = self.mechanical_lifecycle.latest_loop(loop.loop_id)
        evidence["cap_handoffs"].append({
            "passed_ref": dict(entity_type="agent_loop/v1", logical_id=loop.loop_id,
                               version_id=loop.loop_version_id),
            "passed_revision": loop.revision,
            "current_ref": dict(entity_type="agent_loop/v1", logical_id=current.loop_id,
                                version_id=current.loop_version_id),
            "current_revision": current.revision})
        return handoff(self, execution, loop, **kwargs)
    monkeypatch.setattr(OptionalAgentLoopRegistryService, "handoff_agent_task_model_call_cap_v1", observed_handoff)
    try:
        result = run_agent_task(spec)
    except Exception as exc:
        # Preserve the genuine failed root path before the acceptance assertion.
        import traceback
        result = {"stop_reason": "raised_exception", "exception_type": type(exc).__name__,
                  "exception_message": str(exc), "exception_traceback": traceback.format_exc()}
    objects = _save(tmp_path, port, evidence, result)
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    terminal_events = [e.payload for e in core.event_store.list_events_by_type(("agent_loop_terminal/v1",))]
    firing_settlements = [e.payload for e in core.event_store.list_events_by_type(("transition_firing_settled/v1",))]
    _write(tmp_path / "cap-evidence.json", {
        "public_spec": spec.as_worker_document(),
        "ordinary_call_limit": core.event_store.ordinary_model_call_limit(),
        "total_call_limit": core.event_store.actual_model_call_limit(),
        "actual_model_call_counts": core.event_store.actual_model_call_counts(),
        "terminal_events": terminal_events, "cap_handoffs": evidence["cap_handoffs"],
        "firing_settlements": firing_settlements,
        "post_compaction_budget": evidence["post_compaction_budget"],
        "result": result, "requests": port.requests,
        "handler_counts": evidence["handler_counts"]})
    assert core.event_store.ordinary_model_call_limit() == 2
    assert list(core.event_store.actual_model_call_counts()) == [2, 0]
    assert len(port.requests) == 2
    invocation_kinds = [o["document"]["invocation_kind"] for o in objects
                        if o["ref"]["entity_type"] == "llm_invocation_spec/v1"]
    assert invocation_kinds == ["normal_turn", "context_compaction"]
    assert sum(o["ref"]["entity_type"] == "llm_invocation_attempt/v1" for o in objects) == 2
    assert [r["state"] for r in _managed_receipts(tmp_path / "run")] == ["started", "returned"]
    assert port.requests[1]["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
    assert evidence["handler_counts"] == ["cap-small-once"]
    action, = _actions(objects)
    assert action["outcome"] == "returned"
    compaction, = [o["document"] for o in objects if o["ref"]["entity_type"] == "agent_context_compaction/v3"]
    assert compaction["trigger_reason"] == "context_pressure"
    observed, = evidence["cap_handoffs"]
    assert observed["current_ref"] == compaction["agent_loop_ref"]
    assert observed["passed_ref"] == observed["current_ref"], observed
    assert result["stop_reason"] == "task_model_call_cap", result
    terminal, = terminal_events
    assert terminal["terminal_reason"] == "task_model_call_cap"
    assert terminal["llm_turns_used"] == 1
    assert terminal["revision"] == observed["current_revision"] + 1
    assert len(firing_settlements) == 1
