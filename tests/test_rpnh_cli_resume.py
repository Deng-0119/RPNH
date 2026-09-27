from __future__ import annotations

from pathlib import Path

import pytest

from cpn.rpnh.main_session import MainTurnReconciliation
from cpn.rpnh_cli import main


class _ResumeSession:
    def __init__(self, root: Path, state: str) -> None:
        self.root = root
        self.state = state
        self.reconcile_calls = 0

    def reconcile_active_turn(self) -> MainTurnReconciliation:
        self.reconcile_calls += 1
        return MainTurnReconciliation(self.state, None)


@pytest.mark.parametrize("state", ["committed", "interrupted", "paused"])
def test_basic_cli_resume_reconciles_before_reading_input(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    execution = tmp_path / "execution.json"
    session = _ResumeSession(tmp_path / "session", state)
    reconciled_before_input = False

    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: execution)
    monkeypatch.setattr(
        "cpn.rpnh_cli.missing_credentials", lambda _path: ())
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession.resume",
        lambda root, selected: session)

    def read_input(_prompt: str) -> str:
        nonlocal reconciled_before_input
        reconciled_before_input = session.reconcile_calls == 1
        return "/quit"

    monkeypatch.setattr("builtins.input", read_input)

    assert main([
        "--frontend", "basic",
        "--resume", str(session.root),
        "--execution", str(execution),
    ]) == 0
    assert reconciled_before_input


@pytest.mark.parametrize("state", ["accepted", "pending_start", "running"])
def test_basic_cli_resume_blocks_input_while_registry_turn_is_active(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    execution = tmp_path / "execution.json"
    session = _ResumeSession(tmp_path / "session", state)
    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: execution)
    monkeypatch.setattr(
        "cpn.rpnh_cli.missing_credentials", lambda _path: ())
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession.resume",
        lambda root, selected: session)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: pytest.fail("active resume must not accept input"))

    with pytest.raises(SystemExit) as raised:
        main([
            "--frontend", "basic",
            "--resume", str(session.root),
            "--execution", str(execution),
        ])

    assert raised.value.code == 2
    assert session.reconcile_calls == 1


def test_basic_cli_resume_without_execution_uses_persisted_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = tmp_path / "persisted-execution.json"
    session = _ResumeSession(tmp_path / "session", "idle")

    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: pytest.fail(
            "resume must not resolve the current default profile"))
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession._persisted_execution_config_path",
        lambda root: persisted if root == session.root else None)
    monkeypatch.setattr(
        "cpn.rpnh_cli.missing_credentials",
        lambda path: () if path == persisted else pytest.fail(
            "credential check must use the persisted profile"))
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession.resume",
        lambda root, selected: (
            session if (root, selected) == (session.root, persisted)
            else pytest.fail("resume received the wrong execution profile")))
    monkeypatch.setattr("builtins.input", lambda _prompt: "/quit")

    assert main(["--frontend", "basic", "--resume", str(session.root)]) == 0
