"""Offline checks for platform reporting and presentation-only UI isolation."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import cpn
from cpn.frontend import codex_app_server as codex
from cpn.rpnh import updates


class RuntimeBoundaryTests(unittest.TestCase):
    def test_source_version_matches_build_and_update_identity(self):
        root = Path(cpn.__file__).resolve().parent.parent
        declared = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(cpn.__version__, declared)
        self.assertEqual(updates.current_version(), declared)

    def test_codex_ui_has_private_writable_config_without_execution_secrets(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            env = codex.isolated_codex_environment(root, {
                "PATH": "/usr/bin:/bin", "TERM": "xterm-256color", "LANG": "en_US.UTF-8",
                "HOME": "/unrelated", "CODEX_HOME": "/unrelated-codex",
                "OPENAI_API_KEY": "synthetic-secret", "NODE_OPTIONS": "--require bad",
                "PYTHONPATH": "/unrelated", "RPNH_EXECUTION_CONFIG": "/private-profile",
            })
            for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                        "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR"):
                directory = Path(env[key])
                self.assertTrue(directory.is_relative_to(root))
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(env["TERM"], "xterm-256color")
            self.assertFalse(set(env) & {"OPENAI_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "RPNH_EXECUTION_CONFIG"})
            (Path(env["CODEX_HOME"]) / "ui-state").write_text("display-only")

    def test_selected_codex_version_probe_uses_the_same_isolated_environment(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            env = codex.isolated_codex_environment(root, os.environ)
            binary = root / "selected client"
            profile = codex._compatibility_profile(None)
            binary.write_text("#!/bin/sh\n[ -z \"${OPENAI_API_KEY:-}\" ] || exit 9\n"
                              "[ -d \"$CODEX_HOME\" ] || exit 8\n"
                              f"printf '%s\\n' '{profile.reported_cli_version}'\n")
            binary.chmod(0o700)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-parent-secret"}):
                self.assertEqual(codex.resolve_codex_binary(
                    str(binary), environment=env, cwd=root / "display"), str(binary))

    def test_platform_doctor_does_not_need_provider_configuration(self):
        from cpn import rpnh_cli
        with tempfile.TemporaryDirectory() as name, patch.dict(os.environ, {
                "RPNH_CONFIG": str(Path(name) / "absent.json"), "RPNH_UPDATES": "off"}):
            output = io.StringIO()
            with redirect_stdout(output):
                status = rpnh_cli.main(["doctor", "--platform", "--json"])
            report = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertTrue(report["eligible"])
            self.assertEqual(report["native_validation"], "not_performed")
            self.assertFalse((Path(name) / "absent.json").exists())


class CodexPlatformHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_reports_actual_supported_os(self):
        # This is an exact protocol fixture, not a native TUI or socket test.
        for platform_name, expected in (("darwin", "macos"), ("linux", "linux")):
            server = codex.CodexAppServer.__new__(codex.CodexAppServer)
            server._compatibility_profile = codex._compatibility_profile(None)
            server.root = Path("/tmp/protocol-fixture")
            server._initialize_accepted_connections = set()
            server._notification_opt_outs = {}
            received = []
            async def result(*args):
                received.append(args[-1])
            server._result = result
            with patch.object(codex.sys, "platform", platform_name):
                await server._handle_request(object(), "fixture", "initialize", {
                    "clientInfo": {"name": "codex-tui", "version": server.compatibility_profile.version}})
            self.assertEqual(received[0]["platformFamily"], "unix")
            self.assertEqual(received[0]["platformOs"], expected)


if __name__ == "__main__":
    unittest.main()
