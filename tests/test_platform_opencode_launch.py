"""Explicit fake HTTP/Owner/client boundaries around real launch resolution."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from cpn.frontend import opencode_launcher as launcher
from cpn.frontend import opencode_v2_protocol


class OpenCodeSpecLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rpnh-opencode-spec-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.node = self.bin / "node"
        self.node.write_bytes(b"native runtime marker; process execution is mocked\n")
        self.node.chmod(0o700)
        self.environment = mock.patch.dict(os.environ, {
            "PATH": str(self.bin), "RPNH_FRONTEND_CONFIG": str(self.root / "prefs.json"),
            "HOME": str(self.root / "private-user-home"), "OPENAI_API_KEY": "synthetic-parent-only",
            "NODE_OPTIONS": "synthetic-injection", "RPNH_EXECUTION_CONFIG": "synthetic-provider-path",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_fixture(self, version, *, bad_version=False, spawn_error=False):
        package = self.root / ("pkg-" + version)
        (package / "bin").mkdir(parents=True)
        entry = package / "bin/opencode.js"
        entry.write_text("#!/usr/bin/env node\n// fixture; no upstream client is executed\n")
        entry.chmod(0o700)
        (package / "package.json").write_text(json.dumps({
            "name": "@opencode/cli" if version == "2.2.6" else "opencode-ai",
            "version": version, "bin": {"opencode": "bin/opencode.js"}}))
        gateway = mock.Mock()
        gateway.call.return_value = [{"id": "ses_" + "1" * 32}]
        server = mock.Mock(url="http://127.0.0.1:1", password="synthetic-auth")
        protocol = mock.Mock()
        events = []
        calls = []
        def run(argv, **options):
            calls.append((tuple(argv), options))
            if argv[-1] == "--version":
                events.append("probe")
                # Changing ambient PATH after resolution must not change the
                # actual runtime selected for the subsequent attach command.
                os.environ["PATH"] = str(self.root / "different-node-path")
                reported = "0.0.0" if bad_version else (
                    "opencode v2.2.6" if version == "2.2.6" else "1.19.4")
                return SimpleNamespace(returncode=0, stdout=reported, stderr="")
            events.append("spawn")
            if spawn_error:
                raise OSError("explicit fixture spawn failure")
            return SimpleNamespace(returncode=0)
        def owner(*_args, **_kwargs):
            events.append("owner")
            return gateway
        with mock.patch.object(launcher, "FrontendGateway", side_effect=owner) as factory, \
                mock.patch.object(launcher, "OpenCodeHTTPServer", return_value=server), \
                mock.patch.object(launcher, "OpenCodeProtocol", return_value=protocol), \
                mock.patch.object(opencode_v2_protocol, "OpenCodeV2Protocol", return_value=protocol), \
                mock.patch.object(launcher.subprocess, "run", side_effect=run), redirect_stdout(io.StringIO()):
            try:
                status = launcher.run_opencode_frontend(self.root / "session", self.root / "execution.json",
                    opencode_binary=str(entry), compatibility_profile=None if version == "1.19.4" else version)
            finally:
                self.calls, self.events = calls, events
                self.gateway, self.server, self.factory = gateway, server, factory
        return status, entry

    def test_both_profiles_probe_and_launch_with_the_same_fixed_node_wrapper(self):
        for version in ("1.19.4", "2.2.6"):
            with self.subTest(version=version):
                os.environ["PATH"] = str(self.bin)
                status, entry = self.run_fixture(version)
                self.assertEqual(status, 0)
                probe, spawn = self.calls
                self.assertEqual(probe[0], (str(self.node), str(entry), "--version"))
                self.assertEqual(spawn[0][:2], (str(self.node), str(entry)))
                self.assertEqual(probe[1]["cwd"], spawn[1]["cwd"])
                self.assertEqual(probe[1]["env"]["PATH"], str(self.bin))
                self.assertEqual(spawn[1]["env"]["PATH"], str(self.bin))
                self.assertFalse(set(spawn[1]["env"]) & {"OPENAI_API_KEY", "NODE_OPTIONS", "RPNH_EXECUTION_CONFIG"})
                self.assertEqual(self.events, ["probe", "owner", "spawn"])
                self.assertFalse(Path(spawn[1]["env"]["HOME"]).parent.exists())
                self.server.close.assert_called_once()
                self.gateway.close.assert_called_once()

    def test_version_mismatch_rejects_before_owner(self):
        with self.assertRaisesRegex(RuntimeError, "requires exactly"):
            self.run_fixture("2.2.6", bad_version=True)
        self.assertEqual(self.events, ["probe"])
        self.factory.assert_not_called()
        self.assertFalse(Path(self.calls[0][1]["env"]["HOME"]).parent.exists())

    def test_spawn_failure_cleans_server_owner_and_ui_home(self):
        with self.assertRaisesRegex(OSError, "fixture spawn failure"):
            self.run_fixture("2.2.6", spawn_error=True)
        self.server.close.assert_called_once()
        self.gateway.close.assert_called_once()
        self.assertFalse(Path(self.calls[-1][1]["env"]["HOME"]).parent.exists())


if __name__ == "__main__":
    unittest.main()
