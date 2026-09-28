from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh.main_session import MainSession, MainTurnReconciliation
from cpn.rpnh.session_access import MainSessionOwnerLease
from cpn.rpnh_cli import main
from test_main_session_registry import _write_execution_profile


class _ResumeSession:
    def __init__(self, root: Path, state: str) -> None:
        self.root = root
        self.state = state
        self.snapshot_calls = 0

    def reconcile_active_turn(self) -> MainTurnReconciliation:
        return MainTurnReconciliation(self.state, None)

    def active_turn_snapshot(self):
        self.snapshot_calls += 1
        if self.state in {"idle", "committed", "interrupted"}:
            return None
        return SimpleNamespace(
            state=("stopped_by_owner" if self.state == "paused" else self.state))


class _Lease:
    def __init__(self, _record) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _mock_session_access(
        monkeypatch: pytest.MonkeyPatch, root: Path, execution: Path,
) -> None:
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession._persisted_execution_config_path",
        lambda actual: execution if actual == root else None)
    monkeypatch.setattr(
        "cpn.rpnh.session_access.inspect_main_session_root",
        lambda actual: SimpleNamespace(
            root=actual.resolve(), execution_config_path=execution.resolve()),
    )
    monkeypatch.setattr(
        "cpn.rpnh.session_access.MainSessionOwnerLease", _Lease)


@pytest.mark.parametrize("state", ["committed", "interrupted", "paused", "terminal"])
def test_basic_cli_resume_observes_without_reconciling_before_input(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    execution = tmp_path / "execution.json"
    session = _ResumeSession(tmp_path / "session", state)
    observed_before_input = False
    _mock_session_access(monkeypatch, session.root, execution)

    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: execution)
    monkeypatch.setattr(
        "cpn.rpnh_cli.missing_credentials", lambda _path: ())
    monkeypatch.setattr(
        "cpn.rpnh_cli.MainSession.resume",
        lambda root, selected: session)

    def read_input(_prompt: str) -> str:
        nonlocal observed_before_input
        observed_before_input = session.snapshot_calls == 1
        return "/quit"

    monkeypatch.setattr("builtins.input", read_input)

    assert main([
        "--frontend", "basic",
        "--resume", str(session.root),
        "--execution", str(execution),
    ]) == 0
    assert observed_before_input


@pytest.mark.parametrize("state", ["accepted", "pending_start", "running"])
def test_basic_cli_resume_blocks_input_while_registry_turn_is_active(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    execution = tmp_path / "execution.json"
    session = _ResumeSession(tmp_path / "session", state)
    _mock_session_access(monkeypatch, session.root, execution)
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
    assert session.snapshot_calls == 1


def test_basic_cli_resume_without_execution_uses_persisted_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = tmp_path / "persisted-execution.json"
    session = _ResumeSession(tmp_path / "session", "idle")
    _mock_session_access(monkeypatch, session.root, persisted)

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


def test_basic_cli_holds_shared_owner_lease_for_frontend_lifetime(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "owned-session"
    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: execution)
    monkeypatch.setattr(
        "cpn.rpnh_cli.missing_credentials", lambda _path: ())

    def read_input(_prompt: str) -> str:
        with pytest.raises(RuntimeError, match="owner lease"):
            MainSessionOwnerLease(root)
        return "/quit"

    monkeypatch.setattr("builtins.input", read_input)

    assert main([
        "--frontend", "basic", "--execution", str(execution),
        "--session-dir", str(root),
    ]) == 0
    with MainSessionOwnerLease(root):
        pass


def test_resume_rejects_a_different_explicit_profile_before_frontend(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_root = tmp_path / "original"
    replacement_root = tmp_path / "replacement"
    original_root.mkdir()
    replacement_root.mkdir()
    original = _write_execution_profile(original_root)
    replacement = _write_execution_profile(replacement_root)
    root = tmp_path / "session"
    MainSession(root, original)
    monkeypatch.setattr(
        "cpn.rpnh_cli.resolve_execution_path",
        lambda *_args, **_kwargs: replacement)

    with pytest.raises(SystemExit) as raised:
        main([
            "--frontend", "basic", "--resume", str(root),
            "--execution", str(replacement),
        ])

    assert raised.value.code == 2
