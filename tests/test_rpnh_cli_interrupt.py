from __future__ import annotations

import os
from pathlib import Path
import signal

import pytest

from cpn.rpnh.main_session import MainSession
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.session_access import MainSessionOwnerLease
from cpn.rpnh_cli import main
from test_main_session_registry import (
    _install_child_observation, _new_session, _write_execution_profile,
)


def test_basic_ctrl_c_at_input_cancels_then_quit_releases_owner(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    input_calls = 0

    def interrupt_input(_prompt):
        nonlocal input_calls
        input_calls += 1
        if input_calls == 1:
            os.kill(os.getpid(), signal.SIGINT)
        # Cancellation keeps the same session owned until explicit /quit.
        with pytest.raises(RuntimeError, match="already has an owner lease"):
            MainSessionOwnerLease(root)
        return "/quit"

    monkeypatch.setattr("builtins.input", interrupt_input)

    assert main([
        "--frontend", "basic", "--execution", str(execution),
        "--session-dir", str(root),
    ]) == 0
    assert input_calls == 2
    captured = capsys.readouterr()
    assert "Use /quit to leave; Ctrl+C interrupted only this input." in captured.out
    assert "Traceback" not in captured.err
    with MainSessionOwnerLease(root):
        pass
    session = MainSession.resume(root, execution)
    assert session.active_turn_snapshot() is None
    assert session.history == []

    # Reopening the same persisted session must still work after cancellation
    # followed by explicit exit, with no registered turn created by the signal.
    monkeypatch.setattr("builtins.input", lambda _prompt: "/quit")
    assert main([
        "--frontend", "basic", "--execution", str(execution),
        "--resume", str(root),
    ]) == 0
    with MainSessionOwnerLease(root):
        pass
    resumed = MainSession.resume(root, execution)
    assert resumed.active_turn_snapshot() is None
    assert resumed.history == []


@pytest.mark.parametrize("interactive", [False, True])
def test_basic_ctrl_c_during_failure_fsync_retains_registry_and_releases_owner(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
        interactive: bool,
) -> None:
    session, root, execution = _new_session(tmp_path)
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession.resume", lambda *_args: session)
    monkeypatch.setattr("builtins.input", lambda _prompt: "question")

    def return_nonterminal(spec):
        _RegistryCore(spec.run_dir, create=True)
        _install_child_observation(monkeypatch, session, "running")
        return {"stop_reason": "blocked_or_waiting", "output": None}

    monkeypatch.setattr(
        "cpn.rpnh.main_session.run_agent_task", return_nonterminal)
    commit_failure = session._main_thread.commit_execution_failure
    real_fsync = os.fsync
    armed = False
    interruptions = 0
    before = None

    def arm_failure_commit(**kwargs):
        nonlocal armed, before
        before = session._main_thread.recover_thread()
        armed = True
        return commit_failure(**kwargs)

    def interrupt_failure_fsync(descriptor):
        nonlocal armed, interruptions
        if armed:
            armed = False
            interruptions += 1
            os.kill(os.getpid(), signal.SIGINT)
        real_fsync(descriptor)

    monkeypatch.setattr(
        session._main_thread, "commit_execution_failure", arm_failure_commit)
    monkeypatch.setattr(os, "fsync", interrupt_failure_fsync)
    arguments = [
        "--frontend", "basic", "--execution", str(execution),
        "--resume", str(root),
    ]
    if not interactive:
        arguments.extend(("--prompt", "question"))

    try:
        result = main(arguments)
    except KeyboardInterrupt:
        pytest.fail("Basic frontend leaked KeyboardInterrupt during Registry fsync")

    assert result == 130
    assert interruptions == 1
    assert "Traceback" not in capsys.readouterr().err
    with MainSessionOwnerLease(root):
        pass
    assert session._main_thread.recover_thread() == before
    assert session.history == []
    assert session.active_turn_snapshot().state == "running"
    child = root / "main" / "turn-0001" / ".registry_v1" / "registry.sqlite3"
    assert child.is_file()

    # An explicit retry can settle the same main turn after exit; the CLI
    # must not forge a terminal answer or an interruption receipt itself.
    monkeypatch.setattr(
        session._main_thread, "commit_execution_failure", commit_failure)
    assert session.fail_active_turn().state == "failed"
    assert session._main_thread.recover_thread()["active_turn_ref"] is None
    assert child.is_file()
