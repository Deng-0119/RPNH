from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
