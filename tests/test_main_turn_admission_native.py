"""Real admission -> serialized worker -> native plugin execution, offline.

Only installed-plugin discovery and the LLM input port are test fixtures. All
TaskControl launch locks, task_worker entry, owners, tools and Registries run
normally, including the plugin's spawned handler. Frontend entry is exercised
at its Python protocol boundary; this is not stock-client UI certification.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

from cpn.frontend.codex_app_server import CodexAppServer
from cpn.frontend.dsh_rpc import DshFrontendRPC, PROTOCOL
from cpn.frontend.opencode_protocol import OpenCodeProtocol
from cpn.plugins import catalog as plugins
from cpn.plugins.api import json_copy
from cpn.rpnh.agent_tasks import AgentTaskSpec
from cpn.rpnh.frontend_application import RegistryFrontendApplication
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.task_control import TaskControl
from test_codex_compat import _FakeWebSocket
from test_main_session_registry import _TerminalPort, _response, _write_execution_profile
from test_native_plugins import _NativeGraphPort, configuration, fixture_plugin


def _graph():
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
        AgentWorkflowEndpoint as E, AgentWorkflowExecution as X,
        AgentWorkflowArc as A,
    )
    return AgentWorkflowGraph((
        N("prepare", "Prepare native arguments.", (P("request", "task"),),
          (P("args", "args"),)),
        N("compute", "Run the exact plugin.", (P("args", "args"),),
          (P("value", "value"),), X(plugin="test_plugin/add")),
        N("answer", "Interpret native output.", (P("value", "value"),),
          (P("result", "result"),)),
    ), (
        A("args", E("prepare", "args"), E("compute", "args")),
        A("value", E("compute", "value"), E("answer", "value")),
    ), E("prepare", "request"), E("answer", "result"))


def _worker():
    """Test-only bootstrap; leave the production worker's spec/claim untouched."""
    import sys
    import cpn.rpnh.agent_tasks as tasks
    from cpn.rpnh.task_worker import main

    original_load = plugins.load_catalog
    plugins.load_catalog = lambda document: original_load(
        document, factories={"test": fixture_plugin})
    spec_path = Path(sys.argv[-1])
    spec = AgentTaskSpec.from_worker_document(
        json.loads(spec_path.read_text()), document_root=spec_path.parent)
    main_turn = spec.workflow_graph is None
    delegate = (_TerminalPort({
        "reply": "offline native plan", "task": {
            "kind": "workflow", "prompt": "Compute using the admitted plugin.",
            "graph": _graph().to_dict(),
        },
    }) if main_turn else _NativeGraphPort())

    class Port:
        def __init__(self):
            self.calls = 0

        def request_once(self, attempt):
            with (spec.run_dir / "fixture-requests.jsonl").open("ab") as stream:
                stream.write(attempt.canonical_request_bytes + b"\n")
            self.calls += 1
            if main_turn and self.calls == 1:
                messages = json.loads(attempt.canonical_request_bytes)["messages"]
                located = [json.loads(line[2:])
                           for message in messages
                           if isinstance(message.get("content"), str)
                           for line in message["content"].splitlines()
                           if line.startswith("- {")]
                paths = [item["sandbox_path"] for item in located
                         if item.get("summary") == "RPNH agent task request"]
                assert len(paths) == 1
                return _response([{"id": "read-task", "name": "read_file",
                    "arguments": json.dumps({"path": paths[0]})}])
            return delegate.request_once(attempt)

        def close(self):
            delegate.close()

    tasks.build_llm_input_port = lambda *_args, **_kw: Port()
    raise SystemExit(main())


@pytest.fixture
def native_workers(tmp_path, monkeypatch):
    selected = tmp_path / "plugins.json"
    state = NS(reads=0, controls=[], handles=[])

    def select(offset):
        document = configuration()
        document["plugins"][0]["config"]["offset"] = offset
        selected.write_text(json.dumps(document))
        return document

    state.select = select
    select(2)
    monkeypatch.setenv("RPNH_PLUGIN_CONFIG", str(selected))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    original_read, original_load = plugins.read_config, plugins.load_catalog

    def read_config(*args, **kwargs):
        state.reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(plugins, "read_config", read_config)
    monkeypatch.setattr(plugins, "load_catalog", lambda document: original_load(
        document, factories={"test": fixture_plugin}))
    original_init = TaskControl.__init__

    def spawn(argv, **kwargs):
        assert argv[1:3] == ["-m", "cpn.rpnh.task_worker"]
        # Inject deterministic fixtures inside a real new Python process. Keep
        # production arguments, inherited launch descriptor and environment.
        env = kwargs["env"]
        env["PYTHONPATH"] += ":" + str(Path(__file__).resolve().parent)
        process = subprocess.Popen([
            argv[0], "-c",
            "from test_main_turn_admission_native import _worker; _worker()",
            *argv[3:],
        ], **kwargs)
        state.handles.append(process)
        return process

    def initialize(control, root, **kwargs):
        kwargs["popen_factory"] = spawn
        original_init(control, root, **kwargs)
        state.controls.append(control)

    monkeypatch.setattr(TaskControl, "__init__", initialize)
    yield state
    # Close only processes created by this fixture, also on assertion failure.
    for process in state.handles:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _wait(handle):
    # A three-firing child performs durable Registry/workspace settlement.
    # Its process deadline is separate from model/operation/task budgets.
    code = handle.process.wait(timeout=180)
    log = handle.log_path.read_text()
    assert code == 0, log
    result = json.loads(log.splitlines()[-1])
    assert result["stop_reason"] == "terminal", result
    assert result["terminal_evidence_ref"] is not None
    return result


def _accepted(session, ordinal):
    lineages = session._main_thread._turn_lineages(session._thread_ref.entity_id)
    matches = [lineage[0] for lineage in lineages
               if lineage[0][1]["ordinal"] == ordinal]
    assert len(matches) == 1
    return deepcopy(matches[0][1])


def _worker_spec(control, handle, native):
    path = control.root / "specs" / f"{handle.task_id}.json"
    document = json.loads(path.read_text())
    spec = AgentTaskSpec.from_worker_document(document, document_root=path.parent)
    assert spec == handle.spec
    assert json_copy(spec.plugin_configuration) == native["configuration"]
    assert spec.plugin_catalog_digest == native["catalog_digest"]
    return spec


@pytest.mark.parametrize("entry", ["basic", "codex", "opencode", "dsh"])
def test_admitted_plugins_reach_real_workers_on_first_and_later_turns(
        tmp_path, monkeypatch, native_workers, entry):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    owner = None
    if entry == "basic":
        session = MainSession(root, execution)
        control = TaskControl(session.child_path_root / "main-turn-control")
    elif entry == "codex":
        owner = CodexAppServer(root, execution)
        # Drive the real reconciliation/finalization explicitly after process
        # exit, avoiding an unrelated websocket background polling loop.
        monkeypatch.setattr(owner, "_ensure_active_tracking", lambda *_args: None)
        socket = _FakeWebSocket([])
        asyncio.run(owner._start_thread(socket, 1, {}))
        state = next(iter(owner._threads.values()))
        session, control = state.session, state.main_turn_control
    else:
        owner = RegistryFrontendApplication(root, execution)
        sid = owner.create_session()
        state = owner._sessions[sid]
        session, control = state.session, state.main_control
        gateway = NS(call=lambda name, *a, **kw: getattr(owner, name)(*a, **kw))
        protocol = (OpenCodeProtocol(gateway, str(tmp_path)) if entry == "opencode"
                    else DshFrontendRPC(gateway, sid))

    try:
        evidence = []
        for ordinal, offset in ((1, 2), (2, 7)):
            selected = native_workers.select(offset)
            text = f"Native computation turn {ordinal}"
            key = f"native-{ordinal}"
            if entry == "basic":
                control.start(session.prepare_turn(text))
            elif entry == "codex":
                asyncio.run(owner._start_turn(socket, ordinal + 1, {
                    "threadId": state.thread_id,
                    "input": [{"type": "text", "text": text}],
                }))
            elif entry == "opencode":
                assert protocol.route("POST", f"/session/{sid}/prompt_async", {
                    "parts": [{"type": "text", "text": text}],
                    "messageID": key,
                }).status == 204
            else:
                reply = protocol.handle({"protocol": PROTOCOL, "id": key,
                    "method": "submit", "params": {"text": text, "requestId": key}})
                assert reply["result"] == {"ordinal": ordinal}

            accepted = _accepted(session, ordinal)
            native = accepted["user_input"]["native_plugins"]
            assert native["configuration"] == selected
            handle = next(h for h in control._tasks.values()
                          if h.run_dir == session.active_turn_snapshot().attempt_path)
            spec = _worker_spec(control, handle, native)
            hints = plugins.load_catalog(native["configuration"]).describe()
            assert json.dumps(hints, ensure_ascii=False, sort_keys=True) in spec.prompt
            assert spec.execution_config_path == execution

            # Drift after acceptance must affect neither this worker nor the
            # child launched from its committed decision.
            native_workers.select(99)
            result = _wait(handle)
            assert result["actual_model_call_counts"][0] == 2  # fixture port
            requests = [json.loads(row) for row in
                        (spec.run_dir / "fixture-requests.jsonl").read_text().splitlines()]
            assert len(requests) == 2
            # The prompt is a Registry Located input, not an inlined message.
            # Verify the real read_file response delivered the full admitted
            # task, including user intent and the exact operation descriptors.
            reads = [json.loads(message["content"])
                     for message in requests[1]["messages"]
                     if message.get("role") == "tool"
                     and message.get("tool_call_id") == "read-task"]
            assert len(reads) == 1
            assert reads[0]["kind"] == "registered_file_read/v1"
            assert reads[0]["content"] == spec.prompt
            assert text in reads[0]["content"]
            assert json.dumps(hints, ensure_ascii=False, sort_keys=True) in reads[0]["content"]
            if entry in {"opencode", "dsh"}:
                launches = len(native_workers.handles)
                assert owner.submit(sid, text, "explicit:" + key) == ordinal
                assert len(native_workers.handles) == launches
            assert native_workers.reads == ordinal
            assert _accepted(session, ordinal) == accepted
            if entry in {"opencode", "dsh"}:
                owner.tick()
            else:
                reconciliation = session.reconcile_active_turn()
                assert reconciliation.state == "committed"
                if entry == "codex":
                    asyncio.run(owner._finalize_active_turn(
                        None, state, state.active, reconciliation))
            assert session.active_turn_snapshot() is None
            children = [h for h in session.task_control._tasks.values()
                        if h.run_dir.name == f"main-turn-{ordinal:04d}-child"]
            assert len(children) == 1
            child = children[0]
            child_spec = _worker_spec(session.task_control, child, native)
            assert child_spec.workflow_graph == _graph()
            child_result = _wait(child)
            assert child_result["actual_model_call_counts"][0] == 2  # fixture ports
            core = _RegistryCore(child.run_dir, create=False, read_only=True)
            resources = [core.object_store.read_registered(core.get_version(row["version_id"]))
                         for row in core.event_store.canonical_object_rows(
                             object_type="resource_version/v1")
                         if json.loads(row["metadata_json"]).get(
                             "descriptors", {}).get("output_port_id")]
            assert json.dumps(str(3 + offset)).encode() in resources
            assert len(core.event_store.canonical_object_rows(
                object_type="operation_result/v1")) == 3
            assert native_workers.reads == ordinal
            assert _accepted(session, ordinal) == accepted
            evidence.append({
                "ordinal": ordinal, "accepted_user_input": accepted["user_input"],
                "main_worker_spec": str(control.root / "specs" / f"{handle.task_id}.json"),
                "main_run": str(handle.run_dir),
                "child_worker_spec": str(session.task_control.root / "specs" / f"{child.task_id}.json"),
                "child_run": str(child.run_dir),
                "plugin_selector": "test_plugin/add", "plugin_output": 3 + offset,
                "formal_child_operations": 3, "actual_provider_calls": 0,
                "fixture_port_calls": {"main": 2, "child": 2},
            })

        assert len(native_workers.handles) == 4
        assert len(session._main_thread.recover_thread()["committed_history"]) == 2
        expected_history = session.history
        if owner is not None:
            owner.close()
        reopened = MainSession.resume(root)
        assert reopened.history == expected_history
        assert reopened.active_turn_snapshot() is None
        assert len(native_workers.handles) == 4
        assert native_workers.reads == 2
        (tmp_path / "admission-evidence.json").write_text(json.dumps({
            "entry": entry, "status": "passed", "turns": evidence,
            "reopen": "observed two committed turns without redispatch or discovery",
            "scope": "Python protocol entry, real worker/owner/Registry/native plugin; no stock UI",
        }, indent=2) + "\n")
    finally:
        if owner is not None:
            owner.close()
