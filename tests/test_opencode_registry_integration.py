"""Real Registry tests with the repository's existing deterministic LLM test port.

Requires the full harness source. Skipping this module is NOT core acceptance.
No configured API key or external provider transport is used.
"""
from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace as NS

import pytest

import importlib.util
if importlib.util.find_spec("cpn.rpnh.main_session") is None:
    pytest.skip("Full RPNH source is required for real Registry integration", allow_module_level=True)
from cpn.cli import _filter_projection
from cpn.rpnh.agent_tasks import agent_task_catalog, run_agent_task
from cpn.rpnh.frontend_application import FrontendError, RegistryFrontendApplication
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.session_access import stable_frontend_session_id
from cpn.rpnh.task_control import TaskControl
from cpn.frontend.opencode_protocol import OpenCodeProtocol
from cpn.frontend.codex_app_server import CodexAppServer
from test_main_session_registry import _TerminalPort, _write_execution_profile


class CaptureDispatch:
    """Do not spawn a worker; the test runs the captured spec synchronously."""
    def __init__(self):
        self.handles = {}
        self.spec = None
        self.starts = 0

    def list(self):
        return [{"task_id": key, "run_dir": str(value.run_dir)} for key, value in self.handles.items()]

    def get(self, key):
        return self.handles[key]

    def start(self, spec):
        self.starts += 1
        self.spec = spec
        identity = f"task-captured-{self.starts}"
        value = NS(task_id=identity, kind="single_agent", run_dir=spec.run_dir,
                   process=NS(poll=lambda: None), spec=spec)
        self.handles[identity] = value
        return value


class CountingTerminalPort(_TerminalPort):
    def __init__(self, output):
        super().__init__(output)
        self.calls = 0

    def request_once(self, attempt):
        self.calls += 1
        return super().request_once(attempt)


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    port = _TerminalPort({"reply": "registered answer", "task": None})
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)
    app = RegistryFrontendApplication(tmp_path / "frontend", execution)
    sid = app.create_session()
    control = CaptureDispatch()
    app._sessions[sid].main_control = control
    yield app, sid, control, execution
    app.close()


def test_actual_registry_submission_answer_and_reopen(real_app):
    app, sid, control, execution = real_app
    assert app.submit(sid, "request", "explicit:one") == 1
    assert app.submit(sid, "request", "explicit:one") == 1
    assert control.starts == 1
    state = app._sessions[sid]
    assert state.session.active_turn_snapshot().registered_output is None
    run_agent_task(control.spec)
    assert state.session.active_turn_snapshot().state == "terminal"
    app.tick()
    turn = app.snapshot()[0]["turns"][0]
    assert turn["state"] == "committed" and turn["answer"]["reply"] == "registered answer"
    root = app.root
    assert not (root / "threads").exists()
    with pytest.raises(FrontendError, match="already bound"):
        app.create_session()
    app.close()
    reopened = RegistryFrontendApplication(root, execution, resume=True)
    try:
        assert reopened.submit(sid, "request", "explicit:one") == 1
        assert reopened.snapshot()[0]["turns"][0] == turn
        assert len(reopened._sessions[sid].session._main_thread.recover_thread()["committed_history"]) == 1
        with pytest.raises(FrontendError, match="different input"):
            reopened.submit(sid, "different", "explicit:one")
    finally:
        reopened.close()


def test_basic_direct_session_projects_and_continues_without_resume_effects(
        tmp_path, monkeypatch,
):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "shared-main-session"
    port = CountingTerminalPort({"reply": "basic answer", "task": None})
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)
    basic = MainSession(root, execution)
    basic_spec = basic.prepare_turn("created by Basic")
    run_agent_task(basic_spec)
    basic.reconcile_active_turn()
    assert port.calls == 1

    codex_sidecar = root / ".frontends" / "codex.json"
    codex_bytes = b'{"schema_version":"rpnh/codex_frontend_metadata/v1","opaque":true}\n'
    codex_sidecar.parent.mkdir()
    codex_sidecar.write_bytes(codex_bytes)
    worker_starts = []

    def unexpected_spawn(*args, **kwargs):
        worker_starts.append((args, kwargs))
        raise AssertionError("resume/list must not compensate a worker launch")

    monkeypatch.setattr(TaskControl, "_spawn", unexpected_spawn)
    app = RegistryFrontendApplication(root, execution, resume=True)
    sid = stable_frontend_session_id(root)
    try:
        gateway = NS(call=lambda name, *args, **kwargs: getattr(app, name)(*args, **kwargs))
        protocol = OpenCodeProtocol(gateway, str(tmp_path / "display"))
        documents = protocol.route("GET", "/session").body
        view = app.snapshot()[0]

        assert [document["id"] for document in documents] == [sid]
        assert view["turns"][0]["text"] == "created by Basic"
        assert view["turns"][0]["key"] is None
        assert view["turns"][0]["model_evidence"] == "unavailable"
        messages = protocol.route("GET", f"/session/{sid}/message").body
        assert messages[0]["parts"][0]["metadata"][
            "rpnh_turn_model_evidence"] == "unavailable"
        assert messages[0]["parts"][0]["metadata"][
            "rpnh_turn_model_display_basis"] == "session_current_selection"
        assert port.calls == 1
        assert worker_starts == []
        assert codex_sidecar.read_bytes() == codex_bytes
        assert not (root / "threads").exists()

        with pytest.raises(FrontendError, match="writable owner"):
            RegistryFrontendApplication(root, execution, resume=True)
        with pytest.raises(FrontendError, match="already bound"):
            app.create_session()

        control = CaptureDispatch()
        app._sessions[sid].main_control = control
        assert app.submit(sid, "continued by OpenCode", "explicit:opencode") == 2
        run_agent_task(control.spec)
        app.tick()
        turns = app.snapshot()[0]["turns"]
        assert [turn["text"] for turn in turns] == [
            "created by Basic", "continued by OpenCode"]
        assert turns[1]["model_evidence"] == "frontend-request/v3"
        assert len(app._sessions[sid].session._main_thread.recover_thread()[
            "committed_history"]) == 2
        assert port.calls == 2
        assert worker_starts == []
        assert codex_sidecar.read_bytes() == codex_bytes
    finally:
        app.close()

    codex = CodexAppServer(root, execution)
    try:
        codex_turns = next(iter(codex._threads.values())).turns
        assert [
            turn["items"][0]["content"][0]["text"]
            for turn in codex_turns
        ] == ["created by Basic", "continued by OpenCode"]
        assert port.calls == 2
        assert not (root / "threads").exists()
    finally:
        codex.close()

    reopened = RegistryFrontendApplication(root, execution, resume=True)
    try:
        assert len(reopened.snapshot()[0]["turns"]) == 2
    finally:
        reopened.close()

    basic_again = MainSession.resume(root)
    assert basic_again.history == [
        ("user", "created by Basic"),
        ("assistant", "basic answer"),
        ("user", "continued by OpenCode"),
        ("assistant", "basic answer"),
    ]
    assert port.calls == 2


def test_foreign_terminal_turn_waits_for_explicit_opencode_reconciliation(
        tmp_path, monkeypatch,
):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "terminal-shared-session"
    port = CountingTerminalPort({"reply": "terminal answer", "task": None})
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)
    basic = MainSession(root, execution)
    spec = basic.prepare_turn("finish before frontend attach")
    run_agent_task(spec)
    assert basic.active_turn_snapshot().state == "terminal"
    assert basic._main_thread.recover_thread()["committed_history"] == []

    app = RegistryFrontendApplication(root, execution, resume=True)
    sid = stable_frontend_session_id(root)
    try:
        app.tick()
        assert app.snapshot()[0]["turns"][0]["state"] == "terminal"
        assert app._sessions[sid].session._main_thread.recover_thread()[
            "committed_history"] == []

        result = app.command(sid, "rpnh-resume", "", "explicit:settle")
        assert result == {"status": "main_terminal_evidence_committed"}
        assert app.snapshot()[0]["turns"][0]["state"] == "committed"
        assert len(app._sessions[sid].session._main_thread.recover_thread()[
            "committed_history"]) == 1
        assert port.calls == 1
    finally:
        app.close()


def test_snapshot_retains_owner_during_transient_unreadable_child(real_app):
    app, sid, control, _execution = real_app
    app.submit(sid, "request", "explicit:transient-child")
    registry = control.spec.run_dir / ".registry_v1"
    registry.mkdir(parents=True)
    (registry / "registry.sqlite3").write_text(
        "child creation is not complete", encoding="utf-8")

    app.tick()
    view = app.snapshot()[0]
    assert view["error"] == "reconciliation_required"
    assert view["turns"][0]["state"] == "running"
    assert control.starts == 1

    shutil.rmtree(control.spec.run_dir)
    run_agent_task(control.spec)
    app.tick()
    settled = app.snapshot()[0]
    assert settled["error"] is None
    assert settled["turns"][0]["state"] == "committed"
    assert settled["turns"][0]["answer"]["reply"] == "registered answer"
    assert control.starts == 1


@pytest.mark.parametrize("flag,show,only", [("", False, False), ("--show-resources", True, False), ("--resources-only", False, True)])
def test_actual_registry_net_matches_existing_inspection(real_app, flag, show, only):
    app, sid, control, _ = real_app
    app.submit(sid, "request", "explicit:net")
    run_agent_task(control.spec)
    app.tick()
    expected = _filter_projection(project_registry_net(control.spec.run_dir, catalog=agent_task_catalog()),
                                  show_resources=show, resources_only=only, node_id=None)
    actual = app.command(sid, "rpnh-net", flag, "read")
    assert actual == expected  # No fabricated resource node is required for PASS.
    assert control.starts == 1
