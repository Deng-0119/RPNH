"""Launcher/pipe ownership tests; the helper child is not a DSH UI certificate."""
import json
from pathlib import Path
import sys

import pytest

from cpn.frontend import dsh_launcher
from cpn.rpnh.frontend_config import save_frontend_preferences
from cpn.rpnh.session_access import MainSessionOwnerLease, inspect_main_session_root
from cpn.rpnh.session_frontend import read_session_frontend
from test_main_session_registry import _write_execution_profile


@pytest.fixture(autouse=True)
def private_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config/config.json"))
    monkeypatch.delenv("DSH_SOURCE", raising=False)


def test_explicit_environment_and_saved_checkout_precedence(tmp_path, monkeypatch):
    saved, env, explicit = [tmp_path / name for name in ("saved", "env", "explicit")]
    save_frontend_preferences("dsh", dsh_source=saved)
    assert dsh_launcher.resolve_dsh_source() == saved
    monkeypatch.setenv("DSH_SOURCE", str(env))
    assert dsh_launcher.resolve_dsh_source() == env
    assert dsh_launcher.resolve_dsh_source(explicit) == explicit


def test_missing_checkout_fails_before_creating_main_session(tmp_path):
    root = tmp_path / "not-created"
    with pytest.raises(RuntimeError, match="Configure the DSH checkout"):
        dsh_launcher.run_dsh_frontend(root, tmp_path / "missing-profile.json")
    assert not root.exists()


def test_client_environment_keeps_display_but_excludes_execution_configuration(tmp_path):
    env = dsh_launcher.isolated_environment(tmp_path, {
        "PATH": "/bin", "TERM": "xterm", "DISPLAY": ":0", "WSL_DISTRO_NAME": "Ubuntu",
        "OPENAI_API_KEY": "synthetic-key", "NODE_OPTIONS": "--require untrusted", "DSH_HOME": "/user-home",
        "RPNH_EXECUTION_CONFIG": "/private/profile.json", "PYTHONPATH": "/private/python",
    })
    assert env["DISPLAY"] == ":0" and env["WSL_DISTRO_NAME"] == "Ubuntu"
    assert not set(env) & {"OPENAI_API_KEY", "NODE_OPTIONS", "DSH_HOME", "RPNH_EXECUTION_CONFIG", "PYTHONPATH"}
    assert all(Path(env[key]).is_relative_to(tmp_path) for key in (
        "HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"))


def _pipe_helper(tmp_path, monkeypatch, *, return_code=0, missing=False):
    script = tmp_path / "pipe-helper"
    script.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
assert 'RPNH_TEST_PRIVATE_VALUE' not in os.environ
reader = os.fdopen(int(sys.argv[sys.argv.index('--read-fd') + 1]), 'rb')
writer = os.fdopen(int(sys.argv[sys.argv.index('--write-fd') + 1]), 'wb')
for identity, method in enumerate(['initialize', 'snapshot']):
    writer.write((json.dumps({'protocol':'rpnh/dsh_frontend/v1', 'id':identity, 'method':method, 'params':{}})+'\\n').encode())
    writer.flush()
    result = json.loads(reader.readline())
    assert result['id'] == identity and 'error' not in result, result
    row = result['result']['session'] if method == 'initialize' else result['result']
    assert row['id'].startswith('ses_') and row['turns'] == []
''' + f"\nsys.exit({return_code})\n")
    script.chmod(0o700)
    monkeypatch.setenv("RPNH_TEST_PRIVATE_VALUE", "parent-only")
    monkeypatch.setattr(dsh_launcher, "prepare_dsh_frontend",
                        lambda source, env: (str(script if not missing else tmp_path / "missing-child"), tmp_path / "unused-entry.ts"))


def test_real_private_fds_share_and_release_the_direct_main_session(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    _pipe_helper(tmp_path, monkeypatch)
    assert dsh_launcher.run_dsh_frontend(root, execution, source=tmp_path, open_browser=False) == 0
    assert read_session_frontend(root) == "dsh"
    record = inspect_main_session_root(root)
    with MainSessionOwnerLease.acquire(record):
        pass
    assert dsh_launcher.run_dsh_frontend(root, execution, resume=True, source=tmp_path, open_browser=False) == 0
    assert read_session_frontend(root) == "dsh"
    assert inspect_main_session_root(root) == record


def test_failed_child_spawn_releases_the_same_owner(tmp_path, monkeypatch):
    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "session"
    _pipe_helper(tmp_path, monkeypatch, missing=True)
    with pytest.raises(FileNotFoundError):
        dsh_launcher.run_dsh_frontend(root, execution, source=tmp_path, open_browser=False)
    with MainSessionOwnerLease.acquire(inspect_main_session_root(root)):
        pass
