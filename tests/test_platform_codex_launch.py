"""Codex orchestration tests with explicit fake IPC, owner and process boundaries.

These tests exercise the real launch/probe/environment helpers and temporary
directories. They do not bind a socket, create a Registry, run a native TUI or
send a provider request. The fake socket endpoint is an ordinary marker file.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cpn.frontend import codex_app_server as codex


class CodexLaunchBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpnh-codex-launch-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.binary = self.root / "selected presentation client"
        self.binary.write_bytes(b"native executable marker; execution is explicitly mocked\n")
        self.binary.chmod(0o700)
        self.execution = self.root / "private execution configuration.json"
        self.profile = codex._compatibility_profile(None)
        self.events = []
        self.probe = None
        self.spawn = None
        self.transport = None
        self.owner = SimpleNamespace(
            frontend_model_id="fixture-model", _threads={}, handle=object(),
            close=Mock(), stop_active_turns=AsyncMock())
        self.owner_factory = Mock(return_value=self.owner)
        self.process = SimpleNamespace(wait=AsyncMock(return_value=0))

    async def run_fixture(self, *, probe_output=None, launch_error=None):
        def probe(argv, **kwargs):
            self.events.append("probe")
            self.probe = (argv, kwargs)
            return SimpleNamespace(
                returncode=0, stderr="", stdout=(
                    self.profile.reported_cli_version if probe_output is None else probe_output))

        def owner(*args, **kwargs):
            self.events.append("owner")
            return self.owner_factory(*args, **kwargs)

        @contextmanager
        def address(path, *, visible_to_child_process=False):
            self.assertTrue(visible_to_child_process)
            self.transport = Path(path)
            self.events.append("transport-enter")
            try:
                yield str(path)
            finally:
                self.events.append("transport-exit")

        @asynccontextmanager
        async def serve(handler, address):
            self.assertIs(handler, self.owner.handle)
            self.assertEqual(Path(address), self.transport)
            self.transport.write_text("fake endpoint; not an AF_UNIX socket", encoding="utf-8")
            self.events.append("server-enter")
            try:
                yield object()
            finally:
                self.events.append("server-exit")

        async def spawn(*argv, **kwargs):
            self.events.append("spawn")
            self.spawn = (argv, kwargs)
            if launch_error is not None:
                raise launch_error
            return self.process

        inherited = {
            "OPENAI_API_KEY": "synthetic-provider-secret",
            "RPNH_EXECUTION_CONFIG": str(self.execution),
            "CODEX_HOME": str(self.root / "user-codex-home"),
            "HOME": str(self.root / "user-home"),
            "NODE_OPTIONS": "--require synthetic-untrusted-plugin",
            "PYTHONPATH": str(self.root / "untrusted-module-path"),
            "RPNH_CODEX_COMPATIBILITY_PROFILE": "unpackaged-profile",
            "RPNH_FRONTEND_CONFIG": str(self.root / "frontend.json"),
        }
        with patch.dict(os.environ, inherited), \
                patch.dict(sys.modules, {"websockets": SimpleNamespace(unix_serve=serve)}), \
                patch("cpn.rpnh.platform_support.require_platform_support", return_value={"eligible": True}), \
                patch.object(codex.shutil, "which", return_value=str(self.binary)), \
                patch.object(codex.subprocess, "run", side_effect=probe), \
                patch.object(codex, "CodexAppServer", side_effect=owner), \
                patch.object(codex, "unix_socket_address", side_effect=address), \
                patch.object(codex.asyncio, "create_subprocess_exec", side_effect=spawn):
            return await codex._run_codex_frontend_async(
                self.root / "session", self.execution, codex_binary=str(self.binary))

    def assert_environment_boundary(self):
        probe_argv, probe_options = self.probe
        spawn_argv, spawn_options = self.spawn
        self.assertEqual(tuple(probe_argv), (str(self.binary), "--version"))
        self.assertEqual(spawn_argv[0], str(self.binary))
        self.assertEqual(probe_options["env"], spawn_options["env"])
        self.assertEqual(probe_options["cwd"], spawn_options["cwd"])
        environment = spawn_options["env"]
        self.assertFalse(set(environment) & {
            "OPENAI_API_KEY", "RPNH_EXECUTION_CONFIG", "NODE_OPTIONS", "PYTHONPATH",
            "RPNH_CODEX_COMPATIBILITY_PROFILE"})
        scratch = Path(environment["HOME"]).parent
        self.assertEqual(Path(environment["CODEX_HOME"]), scratch / "codex")
        self.assertEqual(spawn_options["cwd"], scratch / "display")
        self.assertNotIn(str(self.execution), spawn_argv)
        self.assertIn("--remote", spawn_argv)
        self.assertIn("unix://" + str(self.transport), spawn_argv)
        self.owner_factory.assert_called_once_with(
            (self.root / "session").resolve(), self.execution,
            compatibility_profile=self.profile.name)
        return scratch

    async def test_probe_and_launch_share_isolation_and_fixed_profile_then_clean_up(self):
        self.assertEqual(await self.run_fixture(), 0)
        scratch = self.assert_environment_boundary()
        self.assertEqual(self.events, [
            "probe", "owner", "transport-enter", "server-enter", "spawn",
            "server-exit", "transport-exit"])
        self.process.wait.assert_awaited_once()
        self.owner.stop_active_turns.assert_awaited_once()
        self.owner.close.assert_called_once()
        self.assertFalse(scratch.exists())
        self.assertFalse((self.root / "session").exists())

    async def test_wrong_probe_version_fails_before_owner_and_removes_temporary_home(self):
        with self.assertRaisesRegex(codex.CodexCompatibilityError, "RPNH requires"):
            await self.run_fixture(probe_output="codex-cli 0.0.0")
        self.assertEqual(self.events, ["probe"])
        self.owner_factory.assert_not_called()
        self.assertIsNone(self.spawn)
        self.assertIsNone(self.transport)
        self.assertFalse(Path(self.probe[1]["env"]["HOME"]).parent.exists())
        self.assertFalse((self.root / "session").exists())

    async def test_process_launch_failure_closes_owner_server_and_temporary_home(self):
        with self.assertRaisesRegex(OSError, "synthetic launch failure"):
            await self.run_fixture(launch_error=OSError("synthetic launch failure"))
        scratch = self.assert_environment_boundary()
        self.assertEqual(self.events[-2:], ["server-exit", "transport-exit"])
        self.process.wait.assert_not_awaited()
        self.owner.close.assert_called_once()
        self.assertFalse(scratch.exists())


if __name__ == "__main__":
    unittest.main()
