"""Offline substitutes at explicit seams; never an owner, Docker or model run."""
from pathlib import Path
import json
from types import SimpleNamespace as NS
import threading
import time

import pytest

from cpn.plugins.catalog import load_catalog
from cpn.plugins.managed_tools import ManagedPluginToolCatalog
from examples.slopcodebench.broker import SessionCommandBroker
from examples.slopcodebench.pilot import (
    CheckpointPilot, DevelopmentCondition, graph, invoke_rpnh, validate_session,
)
from examples.slopcodebench.plugin import bindings, configuration, factory


def request(command="echo hello", call_id="call-1"):
    return dict(checkpoint_key="checkpoint_1", operation_id="op", invocation_id="inv",
                firing_id="firing", call_id=call_id, command=command)


class Runtime:
    def __init__(self, *, timed_out=False, exit_code=0, fail=False):
        self.calls = []
        self.cleanup_count = self.kills = 0
        self.timed_out, self.exit_code, self.fail = timed_out, exit_code, fail

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("synthetic transport failure")
        yield NS(kind="stdout", text="hello")
        yield NS(kind="finished", result=NS(stdout="hello", stderr="diagnostic",
                 exit_code=self.exit_code, timed_out=self.timed_out, elapsed=0.125))

    def kill(self):
        self.kills += 1

    def cleanup(self):
        self.cleanup_count += 1


def broker(tmp_path, runtime=None):
    return SessionCommandBroker(runtime or Runtime(), checkpoint_key="checkpoint_1", evidence_dir=tmp_path)


def test_one_exact_admission_executes_once(tmp_path):
    bridge = broker(tmp_path)
    first = bridge.call(request())
    assert first["ok"] and first["result"]["stdout"] == "hello"
    assert bridge.call(request()) == first
    assert len(bridge.runtime.calls) == 1
    assert bridge.runtime.calls[0] == {"command": "echo hello", "env": {}, "timeout": 60}
    assert not bridge.call(request("different payload"))["ok"]
    assert len(bridge.runtime.calls) == 1


@pytest.mark.parametrize("change", [{"checkpoint_key": "checkpoint_2"}, {"command": ""}, {"unbound": 1}])
def test_unbound_request_never_executes(tmp_path, change):
    bridge = broker(tmp_path)
    assert not bridge.call({**request(), **change})["ok"]
    assert bridge.runtime.calls == []


@pytest.mark.parametrize("runtime", [Runtime(timed_out=True), Runtime(fail=True)])
def test_uncertain_writes_are_not_success_or_replayed(tmp_path, runtime):
    bridge = broker(tmp_path, runtime)
    result = bridge.call(request())
    assert result["ok"] is False and result["error"] == "runtime_effect_unknown"
    assert bridge.poisoned and runtime.kills
    assert bridge.call(request()) == result
    assert bridge.call(request(call_id="next"))["ok"] is False
    assert len(runtime.calls) == 1


def test_observed_nonzero_exit_is_available_to_model(tmp_path):
    bridge = broker(tmp_path, Runtime(exit_code=2))
    result = bridge.call(request())
    assert result["ok"] and result["result"]["exit_code"] == 2
    assert not bridge.poisoned


def test_oversized_output_is_unknown_not_a_truncated_success(tmp_path, monkeypatch):
    monkeypatch.setattr("examples.slopcodebench.broker.FRAME_LIMIT", 4100)
    bridge = broker(tmp_path)
    value = bridge.call(request())
    assert value["error"] == "runtime_effect_unknown" and value["ok"] is False
    assert bridge.poisoned and bridge.runtime.kills
    assert "result" not in value


def test_command_handler_transport_loss_cannot_return_success(monkeypatch):
    from examples.slopcodebench.plugin import command_handler
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def settimeout(self, _value): pass
        def connect(self, _endpoint): pass
        def sendall(self, _data): pass
        def recv(self, _length): return b""
    monkeypatch.setattr("examples.slopcodebench.plugin.socket.socket", lambda *_args: Connection())
    context = NS(check_cancelled=lambda: None, remaining_seconds=180,
                 config={"endpoint": "/not-opened.sock", "checkpoint_key": "checkpoint_1"},
                 operation_id="op", invocation_id="inv", firing_id="fire", call_id="call")
    with pytest.raises(EOFError):
        command_handler(context, {"command": "synthetic write"})


def test_whole_runtime_cleanup_runs_before_and_after_drain(tmp_path):
    order = []
    runtime = Runtime()
    runtime.cleanup = lambda: order.append("cleanup")
    bridge = broker(tmp_path, runtime)
    bridge._server = NS(shutdown=lambda: order.append("shutdown"),
                        server_close=lambda: order.append("close"))
    bridge._thread = NS(join=lambda _: order.append("join"), is_alive=lambda: False)
    bridge.__exit__()
    assert order == ["cleanup", "shutdown", "close", "join", "cleanup"]
    assert bridge.quiescent


def test_blocked_shutdown_is_bounded_and_not_quiescent(tmp_path):
    event = threading.Event()
    try:
        with pytest.raises(RuntimeError, match="deadline"):
            SessionCommandBroker._bounded(event.wait, 0.001)
    finally:
        event.set()


def test_cleanup_failure_never_claims_quiescence(tmp_path):
    runtime = Runtime()
    def fail():
        raise RuntimeError("cleanup unavailable")
    runtime.cleanup = fail
    bridge = broker(tmp_path, runtime)
    with pytest.raises(RuntimeError, match="snapshot prohibited"):
        bridge.__exit__()
    assert not bridge.quiescent


@pytest.mark.parametrize("owner_fails", [True, False])
def test_cleanup_failure_closes_real_socket_and_preserves_primary(tmp_path, owner_fails):
    runtime = Runtime()
    def fail_cleanup():
        raise OSError("synthetic cleanup-secondary")
    runtime.cleanup = fail_cleanup
    primary = ValueError("synthetic owner-primary")
    bridge = broker(tmp_path, runtime)
    with pytest.raises(ValueError if owner_fails else RuntimeError) as caught:
        with bridge:
            endpoint = Path(bridge.endpoint)
            assert endpoint.exists()
            if owner_fails:
                raise primary
    if owner_fails:
        assert caught.value is primary
    else:
        assert isinstance(caught.value.__cause__, OSError)
    assert any("cleanup-secondary" in note for note in caught.value.__notes__)
    assert bridge.closed and bridge.poisoned and not bridge.quiescent
    assert not endpoint.exists() and not bridge._thread.is_alive()
    assert bridge._server.socket.fileno() == -1


def test_failed_cleanup_still_closes_resources_with_bounded_shutdown(tmp_path):
    runtime = Runtime()
    def fail_cleanup():
        raise OSError("synthetic cleanup failure")
    runtime.cleanup = fail_cleanup
    bridge = broker(tmp_path, runtime)
    release = threading.Event()
    closed = []
    bridge._server = NS(shutdown=release.wait, server_close=lambda: closed.append("socket"))
    bridge._temporary = NS(cleanup=lambda: closed.append("temporary"))
    bounded = bridge._bounded
    budgets = []
    def short_wait(action, seconds):
        budgets.append(seconds)
        bounded(action, min(seconds, 0.02))
    bridge._bounded = short_wait
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError):
            bridge.__exit__()
    finally:
        release.set()
    assert time.monotonic() - started < 1
    assert all(0 <= value <= 30 for value in budgets)
    assert closed == ["socket", "temporary"]
    assert not bridge.quiescent


def catalog_loader(document):
    return load_catalog(document, factories={"scb_session": factory})


def test_real_plugin_declaration_and_full_sandbox_command_tool():
    selected = catalog_loader(configuration("/not-opened.sock", "checkpoint_1"))
    declared = bindings()["solve"]
    catalog = ManagedPluginToolCatalog(selected, declared["tools"], admitted_effects=declared["admitted_effects"])
    assert [tool.name for tool in catalog.tools] == ["session_command"]
    assert selected.resolve("scb_session/command")[1].effect == "external_write"
    tools = graph().nodes[0].execution.tools
    assert "read_managed_output" in tools
    assert not {"run_workspace", "delegate_leaf", "run_tool_program"} & set(tools)


@pytest.mark.parametrize("caps", [{"cost_limit": 1}, {"net_cost_limit": 1}, {"step_limit": 1},
                                  {"max_model_calls": 0}, {"checkpoint_timeout_seconds": 0},
                                  {"solver_network": "bridge"}])
def test_no_silent_budget_or_network_changes(caps):
    with pytest.raises(ValueError):
        DevelopmentCondition(**caps)


class Snapshot:
    def __init__(self, work):
        # Synthetic implementation of a preselected native snapshot. This test
        # does not claim to test upstream's own glob-matching implementation.
        self.files = {p.relative_to(work): p.read_bytes() for p in work.glob("*.py")}

    def extract_contents(self):
        return self.files


class Session:
    def __init__(self, root):
        self.working_dir = root / "solver"
        self.working_dir.mkdir()
        self.is_agent_infer = True
        self.static_assets = {}
        self.spec = NS(type="docker", docker=NS(extra_mounts={}, mount_workspace=True, network="none"),
                       environment=NS(include_os_env=False), get_setup_commands=lambda **_kw: [])
        self.workspace = NS(initial_snapshot=Snapshot(self.working_dir))
        self.runtimes = []
        self.finished = []

    def spawn(self, **kwargs):
        assert kwargs["disable_setup"] and kwargs["image"] == "sha256:" + "a" * 64
        runtime = Runtime()
        self.runtimes.append(runtime)
        return runtime

    def finish_checkpoint(self, path):
        assert self.runtimes[-1].cleanup_count == 2
        path.mkdir()
        self.workspace.initial_snapshot = Snapshot(self.working_dir)
        for name, content in self.workspace.initial_snapshot.files.items():
            (path / name).write_bytes(content)
        self.finished.append(path)


class NoSocketBroker(SessionCommandBroker):
    def __enter__(self):
        self.endpoint = "/not-opened.sock"
        return self


def setup_pilot(tmp_path, **overrides):
    session = Session(tmp_path)
    calls = []
    def fake_invoke(spec, _control_dir, timeout):
        calls.append(spec)
        assert timeout == 7200
        (session.working_dir / "code_search.py").write_text(f"# synthetic checkpoint {len(calls)}")
        return {"task_id": "synthetic-no-registry", "run_outcome": "complete",
                "terminal_evidence_ref": {"fixture": True},
                "terminal_result_ref": {"fixture": True}, "actual_model_call_counts": [0, 0]}
    def capture(value):
        from examples.slopcodebench.pilot import selected_snapshot_manifest
        return selected_snapshot_manifest(Snapshot(value.working_dir))
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({"schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process", "model_condition": "synthetic-scb",
        "argv": ["{python}", "-c", "raise SystemExit('fixture must not run')"],
        "probe_argv": ["{python}", "--version"], "env": {}, "inherit_env": []}))
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": "synthetic-scb", "adapter_config_path": str(adapter), "timeout_seconds": 30,
        "max_output_tokens": 2048, "max_response_bytes": 262144}))
    pilot = CheckpointPilot(session, execution=selection, evidence_root=tmp_path / "evidence",
        condition=DevelopmentCondition(), solver_image_id="sha256:" + "a" * 64,
        _invoke=overrides.pop("_invoke", fake_invoke), _broker=overrides.pop("_broker", NoSocketBroker),
        _capture=capture, _catalog_loader=catalog_loader, **overrides)
    return pilot, session, calls


def test_spawn_failure_retains_failure_and_lineage_without_replay(tmp_path):
    pilot, session, calls = setup_pilot(tmp_path)
    failure = RuntimeError("synthetic spawn failure")
    def fail_spawn(**kwargs):
        raise failure
    session.spawn = fail_spawn
    with pytest.raises(RuntimeError) as caught:
        pilot.run("checkpoint_1", "CURRENT")
    assert caught.value is failure
    evidence = json.loads((pilot.root / "checkpoint_1/failure.json").read_text())
    assert evidence["status"] == "failed" and evidence["runtime_returned"] is False
    assert evidence["quiescent"] is False
    assert pilot.ledger.records()[0]["handoff_status"] == "failed"
    assert session.finished == [] and calls == []
    with pytest.raises(ValueError):
        pilot.run("checkpoint_1", "REPLAY")


def test_broker_constructor_failure_cleans_returned_runtime_preserves_error(tmp_path):
    failure = ValueError("synthetic broker construction failure")
    def fail_broker(*args, **kwargs):
        raise failure
    pilot, session, calls = setup_pilot(tmp_path, _broker=fail_broker)
    with pytest.raises(ValueError) as caught:
        pilot.run("checkpoint_1", "CURRENT")
    assert caught.value is failure
    assert session.runtimes[0].cleanup_count == 1
    assert pilot.ledger.records()[0]["handoff_status"] == "failed"
    assert json.loads((pilot.root / "checkpoint_1/failure.json").read_text())["runtime_returned"] is True
    assert session.finished == [] and calls == []


def test_coordinator_retains_source_but_uses_fresh_definition_instance(tmp_path):
    pilot, session, calls = setup_pilot(tmp_path)
    first = pilot.run("checkpoint_1", "CURRENT ONE")
    # This otherwise troublesome symlink is outside the native snapshot's file
    # selection. The coordinator never walks it or follows it to the host.
    (session.working_dir / ".venv").mkdir()
    (session.working_dir / ".venv/python").symlink_to("/not-readable")
    second = pilot.run("checkpoint_2", "CURRENT TWO")
    assert first["after_sha256"] == second["before_sha256"]
    assert calls[0].prompt == "CURRENT ONE" and calls[1].prompt == "CURRENT TWO"
    assert calls[0].run_dir != calls[1].run_dir
    assert calls[0].workflow_graph == calls[1].workflow_graph
    assert first["usage"]["cost_usd"] is None and second["usage"]["tokens"] is None
    assert first["official_agent_runner"] == "not_run"
    assert len(session.finished) == 2
    assert first["original_evaluation"] == "not_collected"


def test_skipped_checkpoint_does_not_spawn(tmp_path):
    pilot, session, _ = setup_pilot(tmp_path)
    with pytest.raises(ValueError, match="next original"):
        pilot.run("checkpoint_2", "SHOULD NOT REVEAL")
    assert session.runtimes == []


def test_workspace_carry_forward_is_checked_not_inferred(tmp_path):
    pilot, session, _ = setup_pilot(tmp_path)
    first = pilot.run("checkpoint_1", "CURRENT")
    assert pilot.ledger.records()[0]["settled_workspace_sha256"] == first["after_sha256"]
    (session.working_dir / "code_search.py").write_text("external untracked edit")
    with pytest.raises(ValueError, match="settled predecessor"):
        pilot.run("checkpoint_2", "NEXT")
    assert len(session.runtimes) == 1


def test_owner_failure_is_not_snapshotted_or_retried(tmp_path):
    def fail(*_args):
        raise RuntimeError("synthetic owner failure")
    pilot, session, _ = setup_pilot(tmp_path, _invoke=fail)
    with pytest.raises(RuntimeError):
        pilot.run("checkpoint_1", "CURRENT")
    assert session.finished == []
    assert pilot.ledger.records()[0]["handoff_status"] == "failed"


def test_owner_and_cleanup_failure_retains_original_private_diagnostics(tmp_path):
    primary = ValueError("synthetic owner-primary")
    def fail_owner(*args):
        raise primary
    pilot, session, _ = setup_pilot(tmp_path, _invoke=fail_owner)
    spawn = session.spawn
    def spawn_with_cleanup_failure(**kwargs):
        runtime = spawn(**kwargs)
        def fail_cleanup():
            raise OSError("synthetic cleanup-secondary")
        runtime.cleanup = fail_cleanup
        return runtime
    session.spawn = spawn_with_cleanup_failure
    with pytest.raises(ValueError) as caught:
        pilot.run("checkpoint_1", "CURRENT")
    assert caught.value is primary
    path = pilot.root / "checkpoint_1/failure.json"
    failure = json.loads(path.read_text())
    assert failure["error_type"] == "ValueError" and failure["error_message"] == str(primary)
    assert any("cleanup-secondary" in note for note in failure["error_notes"])
    assert failure["visibility"] == "private_unreviewed" and failure["public_safe"] is False
    assert path.stat().st_mode & 0o777 == 0o600
    assert failure["quiescent"] is False and session.finished == []
    assert pilot.ledger.records()[0]["handoff_status"] == "failed"
    with pytest.raises(ValueError):
        pilot.run("checkpoint_1", "NO REPLAY")


def test_late_poison_after_broker_exit_blocks_snapshot(tmp_path):
    class LatePoison(NoSocketBroker):
        def __exit__(self, *args):
            super().__exit__(*args)
            self.poisoned = True
    pilot, session, _ = setup_pilot(tmp_path, _broker=LatePoison)
    with pytest.raises(RuntimeError, match="quiescence"):
        pilot.run("checkpoint_1", "CURRENT")
    assert session.finished == []


@pytest.mark.parametrize("unsafe", ["network", "mount", "assets", "eval", "local", "env", "setup"])
def test_unsafe_session_rejected_before_execution(tmp_path, unsafe):
    session = Session(tmp_path)
    if unsafe == "network": session.spec.docker.network = "bridge"
    if unsafe == "mount": session.spec.docker.extra_mounts = {"/grader": "/oracle"}
    if unsafe == "assets": session.static_assets = {"oracle": "no"}
    if unsafe == "eval": session.is_agent_infer = False
    if unsafe == "local": session.spec.type = "local"
    if unsafe == "env": session.spec.environment.include_os_env = True
    if unsafe == "setup": session.spec.get_setup_commands = lambda **_kw: ["install something"]
    with pytest.raises(ValueError):
        validate_session(session, tmp_path / "evidence")


def test_public_taskcontrol_boundary_is_used_without_starting_owner(tmp_path, monkeypatch):
    events = []
    class Control:
        def __init__(self, path): events.append(("init", path))
        def start(self, spec):
            events.append(("start", spec))
            return NS(task_id="mock-task", process=NS(wait=lambda timeout: 0))
        def result(self, task_id):
            events.append(("result", task_id))
            return {"run_outcome": "complete", "task_id": task_id}
    monkeypatch.setattr("cpn.rpnh.task_control.TaskControl", Control)
    value = invoke_rpnh("synthetic-spec", tmp_path, 5)
    assert value["task_id"] == "mock-task"
    assert [event[0] for event in events] == ["init", "start", "result"]
