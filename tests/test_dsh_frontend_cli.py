from pathlib import Path

import pytest

from cpn import rpnh_cli
from cpn.frontend import dsh_launcher
from cpn.rpnh.frontend_config import save_frontend_preferences
from cpn.rpnh.main_session import MainSession
from test_main_session_registry import _write_execution_profile


@pytest.fixture(autouse=True)
def local_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config/config.json"))


def _configured(monkeypatch, execution):
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", lambda *a, **kw: execution)
    monkeypatch.setattr(rpnh_cli, "missing_credentials", lambda *a: ())


def test_dsh_is_exposed_in_installed_cli_help(capsys):
    with pytest.raises(SystemExit) as raised:
        rpnh_cli.main(["--help"])
    assert raised.value.code == 0
    assert "dsh" in capsys.readouterr().out


def test_frontend_preference_does_not_require_model_setup(tmp_path, capsys):
    source = tmp_path / "future-dsh-install"
    assert rpnh_cli.main(["config", "frontend", "dsh", "--dsh-source", str(source)]) == 0
    assert '"frontend": "dsh"' in capsys.readouterr().out
    assert not (tmp_path / "config/config.json").exists()
    assert rpnh_cli.main(["config", "frontend"]) == 0
    assert str(source) in capsys.readouterr().out


def test_saved_dsh_and_explicit_frontend_override_use_one_entry(tmp_path, monkeypatch):
    execution = tmp_path / "execution.json"
    root = tmp_path / "session"
    _configured(monkeypatch, execution)
    save_frontend_preferences("dsh")
    calls = []
    monkeypatch.setattr(dsh_launcher, "run_dsh_frontend", lambda r, e, *, resume:
                        calls.append((r, e, resume)) or 7)
    assert rpnh_cli.main(["--session-dir", str(root)]) == 7
    assert calls == [(root, execution, False)]
    monkeypatch.setattr(rpnh_cli, "_run_basic_frontend", lambda *a: 11)
    assert rpnh_cli.main(["--frontend", "basic", "--session-dir", str(root)]) == 11
    assert len(calls) == 1


def test_dsh_resume_uses_original_main_session_and_persisted_model(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "shared-session"
    MainSession(root, execution)
    monkeypatch.setattr(rpnh_cli, "missing_credentials", lambda *a: ())
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", lambda *a, **kw:
                        pytest.fail("resume must not select today's default model"))
    calls = []
    monkeypatch.setattr(dsh_launcher, "run_dsh_frontend", lambda r, e, *, resume:
                        calls.append((r, e, resume)) or 13)
    assert rpnh_cli.main(["--frontend", "dsh", "--resume", str(root)]) == 13
    assert calls == [(root, execution.resolve(), True)]


def test_one_shot_explicit_dsh_rejected_before_profile_or_session(tmp_path, monkeypatch):
    monkeypatch.setattr(rpnh_cli, "resolve_execution_path", lambda *a, **kw:
                        pytest.fail("unsupported input reached profile selection"))
    with pytest.raises(SystemExit) as raised:
        rpnh_cli.main(["--frontend", "dsh", "--prompt", "one shot"])
    assert raised.value.code == 2


def test_saved_frontend_can_be_reset_to_auto():
    assert rpnh_cli.main(["config", "frontend", "dsh"]) == 0
    assert rpnh_cli.main(["config", "frontend", "auto"]) == 0
    from cpn.rpnh.frontend_config import read_frontend_preferences
    assert read_frontend_preferences().frontend == "auto"
