from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh.main_session import MainSessionExecutionFailed
from cpn.rpnh_cli import (
    _BasicFrontendState, _route_selected_input, _run_turn, _task_command,
)


class _Control:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.handles = {
            "task-one": SimpleNamespace(
                task_id="task-one", kind="single_agent",
                run_dir=root / "one"),
            "task-two": SimpleNamespace(
                task_id="task-two", kind="single_agent",
                run_dir=root / "two"),
            "task-flow": SimpleNamespace(
                task_id="task-flow", kind="workflow",
                run_dir=root / "flow"),
        }
        self.messages: list[tuple[str, str, str | None]] = []
        self.resumed: list[str] = []

    def get(self, task_id: str):
        if task_id not in self.handles:
            raise ValueError(f"unknown task: {task_id}")
        return self.handles[task_id]

    def status(self, task_id: str):
        return {"task_id": self.get(task_id).task_id, "status": "RUNNING"}

    def list(self):
        return tuple(self.status(task_id) for task_id in self.handles)

    def net(self, task_id: str):
        self.get(task_id)
        return {
            "source": {"mode": "registry_current"},
            "summary": {"transition_count": 1},
        }

    def message(self, task_id: str, body: str, *, target=None):
        self.get(task_id)
        self.messages.append((task_id, body, target))
        return {"task_id": task_id, "target": target, "queued": True}

    def resume(self, task_id: str):
        self.get(task_id)
        self.resumed.append(task_id)
        return {"task_id": task_id, "status": "RESUME_STARTED"}


def _session(tmp_path: Path):
    reconciliations = []
    main_actions = []
    return SimpleNamespace(
        task_control=_Control(tmp_path),
        reconciliation_calls=reconciliations,
        main_actions=main_actions,
        active_turn_snapshot=lambda: SimpleNamespace(
            state="stopped_by_owner"),
        reconcile_child_registry_links=(
            lambda: reconciliations.append("reconciled")),
        resume_paused_turn=lambda: (
            main_actions.append("resume") or
            SimpleNamespace(reply="continued", protocol_valid=True),
            None,
        ),
        rollback_paused_turn=lambda: main_actions.append("rollback"),
    )


def test_tasks_reconciles_main_registry_child_indexes(
        tmp_path: Path, capsys,
) -> None:
    session = _session(tmp_path)

    assert _task_command(session, "/tasks")

    assert session.reconciliation_calls == ["reconciled"]
    assert '"task_id": "task-one"' in capsys.readouterr().out


def test_noninteractive_main_failure_is_controlled_and_nonzero(capsys) -> None:
    session = SimpleNamespace(turn=lambda _text: (_ for _ in ()).throw(
        MainSessionExecutionFailed("child Registry remains nonterminal")))

    assert _run_turn(session, "question") is False

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "RPNH main turn failed" in captured.err
    assert "child Registry remains nonterminal" in captured.err
    assert "Traceback" not in captured.err


def test_switches_between_multiple_agents_and_main(
        tmp_path: Path, capsys,
) -> None:
    session = _session(tmp_path)
    state = _BasicFrontendState()

    assert _task_command(session, "/switch task-one", state)
    assert state.selected_task_id == "task-one"
    _route_selected_input(session, state, "first follow-up")
    assert _task_command(session, "/switch task-two", state)
    _route_selected_input(session, state, "second follow-up")
    assert _task_command(session, "/status", state)
    assert _task_command(session, "/switch main", state)

    assert state.selected_task_id is None
    assert session.task_control.messages == [
        ("task-one", "first follow-up", None),
        ("task-two", "second follow-up", None),
    ]
    assert "selected main" in capsys.readouterr().out


def test_resume_and_rollback_are_scoped_by_current_focus(
        tmp_path: Path, capsys,
) -> None:
    session = _session(tmp_path)
    state = _BasicFrontendState("task-one")

    assert _task_command(session, "/resume", state)
    assert session.task_control.resumed == ["task-one"]
    assert session.main_actions == []

    assert _task_command(session, "/switch main", state)
    assert _task_command(session, "/resume", state)
    assert session.main_actions == ["resume"]
    assert "continued" in capsys.readouterr().out

    assert _task_command(session, "/rollback", state)
    assert session.main_actions == ["resume", "rollback"]


def test_selected_workflow_requires_an_explicit_target(
        tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    state = _BasicFrontendState("task-flow")

    try:
        _route_selected_input(session, state, "untargeted")
    except ValueError as exc:
        assert "TARGET :: TEXT" in str(exc)
    else:
        raise AssertionError("workflow input accepted without a target")

    _route_selected_input(session, state, "team.calc :: inspect this")
    assert session.task_control.messages == [
        ("task-flow", "inspect this", "team.calc")]


def test_slash_net_view_opens_selected_registry_run(
        tmp_path: Path, monkeypatch,
) -> None:
    session = _session(tmp_path)
    state = _BasicFrontendState("task-flow")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "cpn.rpnh_cli._net_command",
        lambda arguments: calls.append(list(arguments)) or 0)

    assert _task_command(
        session, "/net view --show-resources --no-open", state)
    assert calls == [[
        "--run", str(tmp_path / "flow"), "--view",
        "--show-resources", "--no-open",
    ]]


def test_explicit_task_net_view_works_without_selection(
        tmp_path: Path, monkeypatch,
) -> None:
    session = _session(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "cpn.rpnh_cli._net_command",
        lambda arguments: calls.append(list(arguments)) or 0)

    assert _task_command(
        session, "/task task-one net view --no-open")
    assert calls == [[
        "--run", str(tmp_path / "one"), "--view", "--no-open",
    ]]


# Real session adoption with synthetic pending work; no provider is constructed.
def _pending_basic_session(tmp_path, monkeypatch):
    from cpn.rpnh.task_control import TaskControl
    from test_main_session_registry import _install_child_observation, _new_session

    monkeypatch.setattr("cpn.plugins.catalog.read_config", lambda: None)

    def pending_spawn(self, *args, launch_lock=None, **kwargs):
        if launch_lock is not None:
            launch_lock.close()
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(TaskControl, "_spawn", pending_spawn)
    with _new_session(tmp_path) as (session, root, execution, _lease):
        session.prepare_turn("Delegate one synthetic task.")
        _install_child_observation(monkeypatch, session, "terminal")
        _decision, handle = session.complete_turn("Delegate one synthetic task.", {
            "reply": "Synthetic committed launch.",
            "task": {"kind": "single_agent", "prompt": "Never call a provider.",
                     "instruction": "Return a result."},
        })
        assert handle is not None
        manifest = session.task_control.root / "manifests" / f"{handle.task_id}.json"

    def forbidden(*args, **kwargs):
        raise AssertionError("observational resume must not spawn or call a model")

    monkeypatch.setattr(TaskControl, "_spawn", forbidden)
    monkeypatch.setattr("cpn.rpnh.main_session.run_agent_task", forbidden)
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", forbidden)
    return root, execution, manifest


def test_basic_readonly_resume_does_not_recover_pending(tmp_path, monkeypatch, capsys):
    import json
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh_cli import _run_basic_frontend

    root, execution, manifest = _pending_basic_session(tmp_path, monkeypatch)
    before = json.loads(manifest.read_text())
    assert before["launch_state"] == "pending"
    assert before["pid"] is None

    def forbidden_activation(self):
        raise AssertionError("read-only commands must not activate execution")

    monkeypatch.setattr(MainSession, "activate_for_execution", forbidden_activation)
    # Reopening later must remain observational as well.
    for _ in range(2):
        commands = iter(["/status", "/history", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))
        assert _run_basic_frontend(
            SimpleNamespace(resume=str(root), prompt=None), root, execution) == 0
        assert json.loads(manifest.read_text()) == before
    assert "Turn      ready" in capsys.readouterr().out


@pytest.mark.parametrize("one_shot", [False, True])
@pytest.mark.parametrize("pending_state", [
    "recoverable", "locked", "unknown_runtime", "runtime_changed_after_adoption",
])
def test_basic_resume_explicit_input_activates_execution(
        tmp_path, monkeypatch, one_shot, pending_state):
    import json
    import os
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh.task_control import (
        TaskControl, _acquire_task_launch_lock, claim_task_worker_launch,
    )
    from cpn.rpnh.worker_runtime import WorkerCompatibilityError
    from cpn.rpnh.business_admission import BusinessAdmissionError
    from cpn.rpnh_cli import _run_basic_frontend

    root, execution, manifest = _pending_basic_session(tmp_path, monkeypatch)
    before = json.loads(manifest.read_text())
    task_id = before["task_id"]
    control_root = manifest.parent.parent
    held_locks = []
    if pending_state == "locked":
        lock = _acquire_task_launch_lock(control_root, task_id)
        assert lock is not None
        held_locks.append(lock)
    elif pending_state == "unknown_runtime":
        before["launch_runtime"] = None
        manifest.write_text(json.dumps(before))
    events = []
    activate = MainSession.activate_for_execution
    reconcile = MainSession.reconcile_committed_launches

    def observed_activation(self):
        events.append("activate")
        if pending_state == "runtime_changed_after_adoption":
            before["launch_runtime"] = None
            manifest.write_text(json.dumps(before))
        return activate(self)

    def observed_reconcile(self):
        events.append("reconcile")
        return reconcile(self)

    def claim_pending(self, identity, spec_path, log_path, *, launch_lock, **kwargs):
        events.append("recover")
        assert pending_state == "recoverable"
        assert identity == task_id
        spec = AgentTaskSpec.from_worker_document(
            json.loads(spec_path.read_text()), document_root=spec_path.parent)
        # Simulate only the worker boundary; durable identity/lock claiming is real.
        try:
            claim = claim_task_worker_launch(
                spec_path, spec, resume=kwargs["resume"],
                inherited_lock_fd=os.dup(launch_lock.fileno()))
            assert claim is not None
            held_locks.append(claim)
        finally:
            launch_lock.close()
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)

    def execution_boundary(spec):
        events.append("execute")
        assert spec.prompt
        raise MainSessionExecutionFailed("offline execution boundary reached")

    monkeypatch.setattr(TaskControl, "_spawn", claim_pending)
    monkeypatch.setattr(MainSession, "activate_for_execution", observed_activation)
    monkeypatch.setattr(MainSession, "reconcile_committed_launches", observed_reconcile)
    monkeypatch.setattr("cpn.rpnh.main_session.run_agent_task", execution_boundary)
    commands = iter(["explicit work", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))
    try:
        args = SimpleNamespace(resume=str(root), prompt="explicit work" if one_shot else None)
        if pending_state == "unknown_runtime":
            with pytest.raises(BusinessAdmissionError, match="runtime observation"):
                _run_basic_frontend(args, root, execution)
        elif pending_state == "runtime_changed_after_adoption" and one_shot:
            with pytest.raises(WorkerCompatibilityError, match="runtime observation"):
                _run_basic_frontend(args, root, execution)
        else:
            assert _run_basic_frontend(args, root, execution) == (1 if one_shot else 0)
        if pending_state == "unknown_runtime":
            assert events == []
        elif pending_state == "runtime_changed_after_adoption":
            assert events == ["activate"]
        else:
            assert events == (["activate", "recover", "reconcile", "execute"]
                              if pending_state == "recoverable"
                              else ["activate", "reconcile", "execute"])
        after = json.loads(manifest.read_text())
        assert after["task_id"] == task_id
        if pending_state == "recoverable":
            assert after["launch_state"] == "active"
            assert after["pid"] == os.getpid()
            # The same explicitly recovered identity cannot be launched twice.
            TaskControl(control_root, recover_pending_launches=False)._recover_pending_launches()
            assert events.count("recover") == 1
        else:
            assert after == before
    finally:
        for lock in held_locks:
            lock.close()
