"""Session UX memory and launcher binding; no model or browser calls."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cpn.frontend import codex_app_server as codex, opencode_launcher as opencode
from cpn.rpnh.frontend_application import FrontendError, RegistryFrontendApplication
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.session_access import MainSessionOwnerLease
from cpn.rpnh import session_frontend as memory
from test_codex_compat import _FakeWebSocket, _local_profile


@pytest.fixture
def execution(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "user/config.json"))
    monkeypatch.delenv("RPNH_CODEX_BIN", raising=False)
    return _local_profile(tmp_path / "profiles")


@pytest.fixture
def root(tmp_path, execution):
    root = tmp_path / "session"
    MainSession(root, execution, task_control=object())
    return root


def test_missing_sidecar_read_does_not_create_memory_or_owner(root):
    assert memory.read_session_frontend(root) is None
    assert not (root / memory.SESSION_FRONTEND_FILE).exists()
    assert not (root / MainSessionOwnerLease.LOCK_FILE).exists()


@pytest.mark.parametrize("frontend", memory.SESSION_FRONTENDS)
def test_concrete_choice_roundtrips_without_rewriting(root, frontend, monkeypatch):
    with MainSessionOwnerLease.acquire(root) as lease:
        memory.remember_session_frontend(root, frontend, lease=lease)
        path = root / memory.SESSION_FRONTEND_FILE
        assert json.loads(path.read_text()) == {
            "schema_version": memory.SESSION_FRONTEND_SCHEMA, "frontend": frontend}
        assert memory.read_session_frontend(root) == frontend
        monkeypatch.setattr(memory.tempfile, "NamedTemporaryFile",
                            lambda **kw: pytest.fail("same choice must not write"))
        memory.remember_session_frontend(root, frontend, lease=lease)


@pytest.mark.parametrize("content", ["{", "[]", "null", "x" * 1025,
    '{"schema_version":"future","frontend":"codex"}',
    '{"schema_version":"rpnh/session_frontend/v1","frontend":"auto"}',
    '{"schema_version":"rpnh/session_frontend/v1","frontend":[]}', "\xff"])
def test_malformed_or_unsupported_memory_is_unknown(root, content):
    path = root / memory.SESSION_FRONTEND_FILE
    path.write_bytes(content.encode("latin-1"))
    modified = path.stat().st_mtime_ns
    assert memory.read_session_frontend(root) is None
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("frontend", ["auto", "", "unknown", None])
def test_only_explicit_concrete_choices_can_be_remembered(root, frontend):
    with MainSessionOwnerLease.acquire(root) as lease:
        with pytest.raises(ValueError, match="session frontend"):
            memory.remember_session_frontend(root, frontend, lease=lease)
    assert not (root / memory.SESSION_FRONTEND_FILE).exists()


def test_writes_require_live_lease_bound_to_canonical_root(root, tmp_path, execution):
    other = tmp_path / "other"
    MainSession(other, execution, task_control=object())
    with MainSessionOwnerLease.acquire(root) as lease:
        with pytest.raises(ValueError, match="does not match"):
            memory.remember_session_frontend(other, "basic", lease=lease)
    with pytest.raises(ValueError, match="owner binding"):
        memory.remember_session_frontend(root, "basic", lease=lease)
    assert not (other / memory.SESSION_FRONTEND_FILE).exists()
    assert not (root / memory.SESSION_FRONTEND_FILE).exists()


def test_symlink_root_and_sidecar_are_rejected(root, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        memory.read_session_frontend(alias)
    target = tmp_path / "other.json"
    target.write_text("{}")
    (root / memory.SESSION_FRONTEND_FILE).symlink_to(target)
    with MainSessionOwnerLease.acquire(root) as lease:
        with pytest.raises(ValueError, match="symlink"):
            memory.remember_session_frontend(root, "codex", lease=lease)
    assert json.loads(target.read_text()) == {}


def test_failed_atomic_replace_retains_previous_choice_and_cleans_temporary(root, monkeypatch):
    with MainSessionOwnerLease.acquire(root) as lease:
        memory.remember_session_frontend(root, "basic", lease=lease)
        def fail_replace(source, destination):
            lease.assert_held()
            assert Path(source).parent == root
            assert memory.read_session_frontend(root) == "basic"
            raise OSError("synthetic replace failure")
        monkeypatch.setattr(memory.os, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic"):
            memory.remember_session_frontend(root, "dsh", lease=lease)
        assert memory.read_session_frontend(root) == "basic"
        assert not list(root.glob(".frontend-*.tmp"))


def test_lease_rechecked_before_atomic_publication(root, monkeypatch):
    with MainSessionOwnerLease.acquire(root) as lease:
        original = lease.assert_held
        def check():
            # The staged file is complete only after the stream has closed.
            staged = list(root.glob(".frontend-*.tmp"))
            if staged and staged[0].stat().st_size:
                lease.close()
            original()
        monkeypatch.setattr(lease, "assert_held", check)
        with pytest.raises(ValueError, match="owner binding"):
            memory.remember_session_frontend(root, "dsh", lease=lease)
        assert memory.read_session_frontend(root) is None
        assert not list(root.glob(".frontend-*.tmp"))


@pytest.mark.parametrize("frontend", [None, "opencode", "dsh"])
def test_shared_application_remembers_only_on_create_and_adoption(tmp_path, execution, frontend):
    root = tmp_path / "application"
    app = RegistryFrontendApplication(root, execution, frontend=frontend)
    assert not root.exists()
    try:
        app.create_session()
        assert memory.read_session_frontend(root) == frontend
        with pytest.raises(FrontendError, match="writable owner"):
            RegistryFrontendApplication(root, execution, resume=True, frontend="basic")
        assert memory.read_session_frontend(root) == frontend
    finally:
        app.close()
    adopted = RegistryFrontendApplication(root, execution, resume=True, frontend="dsh")
    try:
        assert memory.read_session_frontend(root) == "dsh"
        modified = (root / memory.SESSION_FRONTEND_FILE).stat().st_mtime_ns
        assert adopted.snapshot()[0]["turns"] == []
        assert (root / memory.SESSION_FRONTEND_FILE).stat().st_mtime_ns == modified
    finally:
        adopted.close()


def test_generic_adoption_does_not_replace_known_choice(root, execution):
    with MainSessionOwnerLease.acquire(root) as lease:
        memory.remember_session_frontend(root, "basic", lease=lease)
    app = RegistryFrontendApplication(root, execution, resume=True)
    try:
        assert memory.read_session_frontend(root) == "basic"
    finally:
        app.close()


def test_profile_rejection_does_not_remember_frontend(root, execution, tmp_path):
    other = _local_profile(tmp_path / "other-profiles", model="other")
    with pytest.raises(FrontendError):
        RegistryFrontendApplication(root, other, resume=True, frontend="dsh")
    with pytest.raises(ValueError, match="model is unavailable"):
        codex.CodexAppServer(root, other)
    assert memory.read_session_frontend(root) is None


def test_codex_fresh_thread_and_adoption_remember_without_history_writes(tmp_path, execution, monkeypatch):
    root = tmp_path / "codex"
    server = codex.CodexAppServer(root, execution)
    assert not root.exists()
    socket = _FakeWebSocket([])
    try:
        asyncio.run(server._start_thread(socket, 1, {}))
        assert memory.read_session_frontend(root) == "codex"
        memory.remember_session_frontend(root, "basic", lease=server._lease)
        with pytest.raises(RuntimeError, match="owner lease"):
            codex.CodexAppServer(root, execution)
        assert memory.read_session_frontend(root) == "basic"
    finally:
        server.close()
    adopted = codex.CodexAppServer(root, execution)
    try:
        assert memory.read_session_frontend(root) == "codex"
        state = next(iter(adopted._threads.values()))
        adopted._initialized_connections.add(id(socket))
        monkeypatch.setattr(codex, "remember_session_frontend",
                            lambda *a, **kw: pytest.fail("history must not remember"))
        for method in ("thread/read", "thread/turns/list", "thread/resume"):
            asyncio.run(adopted._handle_request(socket, 2, method, {"threadId": state.thread_id}))
            assert "result" in socket.sent[-1]
    finally:
        adopted.close()


@pytest.mark.parametrize("resume", [False, True], ids=["start", "adopt"])
def test_codex_setup_interrupt_releases_lease(tmp_path, execution, monkeypatch, resume):
    root = tmp_path / "interrupted-codex"
    if resume:
        MainSession(root, execution, task_control=object())
    server = None if resume else codex.CodexAppServer(root, execution)
    leases = []
    def interrupt(root, frontend, *, lease):
        lease.assert_held()
        leases.append(lease)
        raise KeyboardInterrupt("synthetic setup interrupt")
    monkeypatch.setattr(codex, "remember_session_frontend", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt, match="synthetic setup interrupt"):
            if resume:
                codex.CodexAppServer(root, execution)
            else:
                asyncio.run(server._start_thread(_FakeWebSocket([]), 1, {}))
        assert len(leases) == 1
        with pytest.raises(ValueError, match="owner binding"):
            leases[0].assert_held()
        with MainSessionOwnerLease.acquire(root):
            pass
        if server is not None:
            assert server._lease is None
            assert not server._threads
        assert memory.read_session_frontend(root) is None
    finally:
        if server is not None:
            server.close()
        for lease in leases:
            lease.close()


@pytest.mark.parametrize("explicit,environment,saved,expected", [
    ("/explicit/codex", "/env/codex", "/saved/codex", "/explicit/codex"),
    (None, "/env/codex", "/saved/codex", "/env/codex"),
    (None, None, "/saved/codex", "/saved/codex"),
    (None, None, None, "codex"),
])
def test_codex_binary_precedence_and_exact_version(monkeypatch, explicit, environment, saved, expected):
    monkeypatch.delenv("RPNH_CODEX_BIN", raising=False)
    if environment:
        monkeypatch.setenv("RPNH_CODEX_BIN", environment)
    monkeypatch.setattr(codex, "read_frontend_preferences", lambda: NS(codex_binary=Path(saved) if saved else None))
    selections, probes = [], []
    monkeypatch.setattr(codex.shutil, "which", lambda value: selections.append(value) or "/resolved/codex")
    def probe(argv, **kwargs):
        probes.append(argv)
        return NS(returncode=0, stdout=codex._compatibility_profile().reported_cli_version, stderr="")
    monkeypatch.setattr(codex.subprocess, "run", probe)
    assert codex.resolve_codex_binary(explicit) == "/resolved/codex"
    assert selections == [expected]
    assert probes == [["/resolved/codex", "--version"]]
    monkeypatch.setattr(codex.subprocess, "run", lambda *a, **kw: NS(returncode=0, stdout="codex-cli 0.0.0", stderr=""))
    with pytest.raises(codex.CodexCompatibilityError, match="requires"):
        codex.resolve_codex_binary(explicit)


@pytest.mark.parametrize("saved", [None, Path("/saved/opencode")])
def test_opencode_saved_binary_keeps_profile_isolation_and_session_memory(tmp_path, execution, monkeypatch, saved):
    root = tmp_path / "opencode"
    selections, launches = [], []
    monkeypatch.setattr(opencode, "read_frontend_preferences", lambda: NS(opencode_binary=saved))
    monkeypatch.setattr(opencode.shutil, "which", lambda value: selections.append(value) or "/resolved/opencode")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-value")
    def run(argv, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert Path(kwargs["env"]["HOME"]).is_relative_to(Path(opencode.tempfile.gettempdir()))
        launches.append(argv)
        return NS(returncode=0, stdout=opencode.DEFAULT_PROFILE.version)
    monkeypatch.setattr(opencode.subprocess, "run", run)
    class Gateway:
        def __init__(self, factory, **kwargs):
            self.app = factory()
            self.app.create_session()
        def close(self):
            self.app.close()
    monkeypatch.setattr(opencode, "FrontendGateway", Gateway)
    monkeypatch.setattr(opencode, "OpenCodeProtocol", lambda *a, **kw: NS())
    monkeypatch.setattr(opencode, "OpenCodeHTTPServer", lambda protocol: NS(
        url="http://127.0.0.1:1", password="synthetic", start=lambda: None, close=lambda: None))
    assert opencode.run_opencode_frontend(root, execution) == 0
    assert selections == [str(saved) if saved else "opencode"]
    assert launches[0] == ["/resolved/opencode", "--version"]
    assert launches[1][0:2] == ["/resolved/opencode", "attach"]
    assert memory.read_session_frontend(root) == "opencode"
    with MainSessionOwnerLease.acquire(root):
        pass


def test_opencode_saved_wrong_version_fails_before_owner(tmp_path, execution, monkeypatch):
    root = tmp_path / "rejected"
    monkeypatch.setattr(opencode, "read_frontend_preferences", lambda: NS(opencode_binary=Path("/saved/opencode")))
    monkeypatch.setattr(opencode.shutil, "which", lambda value: value)
    monkeypatch.setattr(opencode.subprocess, "run", lambda *a, **kw: NS(returncode=0, stdout="0.0.0"))
    with pytest.raises(RuntimeError, match="requires exactly"):
        opencode.run_opencode_frontend(root, execution)
    assert not root.exists()
