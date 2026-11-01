"""Launcher regression only; native PTY evidence lives in the local task."""
from __future__ import annotations

import asyncio

import pytest

import cpn.frontend.codex_app_server as frontend
from cpn.frontend.codex_history import codex_thread_id
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.session_access import inspect_main_session_root, stable_frontend_session_id
from test_codex_compat import _local_profile


@pytest.mark.parametrize("compatibility_profile", [None, "candidate-0.166.0"])
@pytest.mark.parametrize("resume", [True, False])
def test_launcher_attaches_existing_thread_without_bootstrapping_a_new_one(
        tmp_path, monkeypatch, resume, compatibility_profile):
    execution = _local_profile(tmp_path / "profiles")
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(tmp_path / "installed-profiles"))
    root = tmp_path / "session"
    expected_thread = None
    if resume:
        MainSession(root, execution)
        expected_thread = codex_thread_id(stable_frontend_session_id(
            inspect_main_session_root(root)))
    calls = []

    class Client:
        async def wait(self):
            return 0

    async def spawn(*argv):
        calls.append(argv)
        return Client()

    monkeypatch.setattr(frontend, "resolve_codex_binary", lambda *a, **kw: "/native/codex")
    monkeypatch.setattr(frontend.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(frontend._run_codex_frontend_async(
        root, execution, codex_binary="/native/codex", resume=resume,
        compatibility_profile=compatibility_profile)) == 0
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "/native/codex" and argv[1] == "--remote"
    assert argv[2].startswith("unix://")
    if resume:
        assert argv[-2:] == ("resume", expected_thread)
    else:
        assert "resume" not in argv
        assert not root.exists()
