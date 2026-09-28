from __future__ import annotations

from pathlib import Path

import pytest

from cpn import rpnh_cli
from cpn.frontend import opencode_launcher
from cpn.rpnh.main_session import MainSession
from test_main_session_registry import _write_execution_profile


def _configured(monkeypatch: pytest.MonkeyPatch, execution: Path) -> None:
    monkeypatch.setattr(
        rpnh_cli, "resolve_execution_path",
        lambda *_args, **_kwargs: execution,
    )
    monkeypatch.setattr(rpnh_cli, "missing_credentials", lambda _path: ())


def test_installed_parser_advertises_opencode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        rpnh_cli.main(["--help"])
    assert error.value.code == 0
    assert "opencode" in capsys.readouterr().out


def test_cli_dispatches_opencode_without_replacing_other_frontends(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = tmp_path / "execution.json"
    root = tmp_path / "frontend"
    _configured(monkeypatch, execution)
    calls = []
    monkeypatch.setattr(
        opencode_launcher, "run_opencode_frontend",
        lambda actual_root, actual_execution, *, resume: (
            calls.append((actual_root, actual_execution, resume)) or 17),
    )

    assert rpnh_cli.main([
        "--frontend", "opencode", "--execution", str(execution),
        "--session-dir", str(root),
    ]) == 17
    assert calls == [(root, execution, False)]


def test_opencode_rejects_one_shot_prompt_before_owner_creation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = tmp_path / "execution.json"
    _configured(monkeypatch, execution)
    monkeypatch.setattr(
        opencode_launcher, "run_opencode_frontend",
        lambda *_args, **_kwargs: pytest.fail("launcher must not run"),
    )

    with pytest.raises(SystemExit) as error:
        rpnh_cli.main([
            "--frontend", "opencode", "--execution", str(execution),
            "--prompt", "not supported",
        ])
    assert error.value.code == 2


def test_cli_resumes_the_same_direct_session_root_with_opencode(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "shared-session"
    MainSession(root, execution)
    monkeypatch.setattr(rpnh_cli, "missing_credentials", lambda _path: ())
    calls = []
    monkeypatch.setattr(
        opencode_launcher, "run_opencode_frontend",
        lambda actual_root, actual_execution, *, resume: (
            calls.append((actual_root, actual_execution, resume)) or 23),
    )

    assert rpnh_cli.main([
        "--frontend", "opencode", "--resume", str(root),
    ]) == 23
    assert calls == [(root, execution.resolve(), True)]
