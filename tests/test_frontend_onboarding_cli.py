from __future__ import annotations

from pathlib import Path
import sys

import pytest

from cpn import rpnh_cli
from cpn.frontend import dsh_launcher, opencode_launcher
from cpn.rpnh import frontend_onboarding, frontend_setup
from cpn.rpnh.frontend_application import RegistryFrontendApplication
from cpn.rpnh.frontend_config import (
    read_frontend_preferences, save_frontend_preferences,
)
from cpn.rpnh.onboarding import SetupCancelled
from cpn.rpnh.session_frontend import read_session_frontend
from test_main_session_registry import _write_execution_profile


@pytest.fixture(autouse=True)
def isolated_user(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "user/config.json"))
    for name in ("RPNH_EXECUTION_CONFIG", "RPNH_PROFILE_DIR", "RPNH_PROVIDER_CATALOG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *a, **kw: pytest.fail("frontend selection must not call a model"))
    monkeypatch.setattr(frontend_setup, "frontend_status", lambda name: {
        "frontend": name, "available": name == "basic", "detail": "fixture"})


def _basic_session(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    monkeypatch.setattr("builtins.input", lambda _prompt: "/quit")
    assert rpnh_cli.main([
        "--frontend", "basic", "--execution", str(execution),
        "--session-dir", str(root),
    ]) == 0
    assert read_session_frontend(root) == "basic"
    return root, execution


def _adopt_in_dsh(monkeypatch, calls):
    def launch(root, execution, *, resume):
        app = RegistryFrontendApplication(root, execution, resume=resume, frontend="dsh")
        try:
            calls.append((root, execution, resume, app.configuration()))
            return 0
        finally:
            app.close()
    monkeypatch.setattr(dsh_launcher, "run_dsh_frontend", launch)


@pytest.mark.parametrize("answer, expected", [("1", "codex"), ("3", "dsh"), ("basic", "basic"), ("", "basic")])
def test_menu_connects_the_deliberately_selected_frontend(monkeypatch, answer, expected):
    configured = []
    monkeypatch.setattr(frontend_setup, "configure_frontend", lambda name, **kw: configured.append(name))
    output = []
    assert frontend_onboarding.choose_frontend(
        input_fn=lambda _prompt: answer, output_fn=output.append) == expected
    assert configured == [expected]
    assert any("Missing clients" in line for line in output)


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_menu_cancel_does_not_save_a_frontend(tmp_path, monkeypatch, exception):
    monkeypatch.setattr(frontend_setup, "configure_frontend", lambda *a, **kw: pytest.fail("cancelled selection installed a client"))
    def cancel(_prompt):
        raise exception
    with pytest.raises(SetupCancelled):
        frontend_onboarding.choose_frontend(input_fn=cancel, output_fn=lambda _: None)
    assert not (tmp_path / "user").exists()


def test_failed_setup_allows_choosing_another_frontend(monkeypatch):
    configured = []
    answers = iter(("codex", "basic"))
    def configure(name, **kwargs):
        configured.append(name)
        if name == "codex":
            raise RuntimeError("offline: choose an installed client")
    monkeypatch.setattr(frontend_setup, "configure_frontend", configure)
    assert frontend_onboarding.choose_frontend(
        input_fn=lambda _: next(answers), output_fn=lambda _: None) == "basic"
    assert configured == ["codex", "basic"]


def test_fresh_interactive_start_shows_choice_despite_saved_default(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    save_frontend_preferences("dsh")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    choices = []
    monkeypatch.setattr(frontend_onboarding, "choose_frontend", lambda: choices.append(True) or "opencode")
    launched = []
    monkeypatch.setattr(opencode_launcher, "run_opencode_frontend", lambda root, selected, *, resume: launched.append((selected, resume)) or 0)
    assert rpnh_cli.main([
        "--execution", str(execution), "--session-dir", str(tmp_path / "new-session"),
    ]) == 0
    assert choices == [True]
    assert launched == [(execution, False)]


def test_resume_uses_session_frontend_and_model_without_a_menu(tmp_path, monkeypatch):
    root, execution = _basic_session(tmp_path, monkeypatch)
    save_frontend_preferences("dsh")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(frontend_onboarding, "choose_frontend", lambda *a, **kw: pytest.fail("resume must not open the frontend menu"))
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", lambda *a, **kw: pytest.fail("resume must not choose today's default model"))
    assert rpnh_cli.main(["--resume", str(root)]) == 0
    assert read_session_frontend(root) == "basic"
    assert read_frontend_preferences().frontend == "dsh"


def test_explicit_resume_override_remembers_the_new_frontend(tmp_path, monkeypatch):
    root, execution = _basic_session(tmp_path, monkeypatch)
    calls = []
    _adopt_in_dsh(monkeypatch, calls)
    assert rpnh_cli.main(["--resume", str(root), "--frontend", "dsh"]) == 0
    assert calls[0][:3] == (root, execution.resolve(), True)
    assert read_session_frontend(root) == "dsh"


def test_basic_switch_releases_owner_and_opens_same_session(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "live-session"
    monkeypatch.setattr("builtins.input", lambda _: "/frontend dsh")
    monkeypatch.setattr(frontend_onboarding, "choose_frontend", lambda name: name)
    calls = []
    _adopt_in_dsh(monkeypatch, calls)
    assert rpnh_cli.main([
        "--frontend", "basic", "--execution", str(execution),
        "--session-dir", str(root),
    ]) == 0
    assert len(calls) == 1
    assert calls[0][:3] == (root, execution, True)
    assert read_session_frontend(root) == "dsh"


def test_switch_command_reopens_same_session_with_persisted_model(tmp_path, monkeypatch):
    root, execution = _basic_session(tmp_path, monkeypatch)
    calls = []
    _adopt_in_dsh(monkeypatch, calls)
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", lambda *a, **kw: pytest.fail("switch must retain the persisted model"))
    assert rpnh_cli.main([
        "frontend", "switch", "dsh", "--resume", str(root),
    ]) == 0
    assert calls[0][:3] == (root, execution.resolve(), True)
    assert read_session_frontend(root) == "dsh"


def test_frontend_setup_works_before_model_setup(tmp_path):
    assert rpnh_cli.main(["frontend", "setup", "basic"]) == 0
    assert read_frontend_preferences().frontend == "basic"
    assert not (tmp_path / "user/config.json").exists()


def test_noninteractive_unconfigured_start_has_actionable_error(tmp_path, capsys):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "must-not-open"
    with pytest.raises(SystemExit) as raised:
        rpnh_cli.main(["--execution", str(execution), "--session-dir", str(root)])
    assert raised.value.code == 2
    assert "rpnh frontend setup" in capsys.readouterr().err
    assert not root.exists()


def test_cancelled_startup_does_not_open_a_session(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "must-not-open"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    def cancel():
        raise SetupCancelled("cancelled")
    monkeypatch.setattr(frontend_onboarding, "choose_frontend", cancel)
    assert rpnh_cli.main(["--execution", str(execution), "--session-dir", str(root)]) == 130
    assert not root.exists()
