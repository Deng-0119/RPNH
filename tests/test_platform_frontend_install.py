"""Offline platform prerequisites and profile-specific installation routing.

Darwin fixtures exercise the decision contract only. They never constitute
native macOS, upstream-client or package installation acceptance.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "rpnh_platform_preflight_test", _ROOT / "cpn/rpnh/platform_support.py")
platform_support = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(platform_support)


class PlatformPrerequisitesTests(unittest.TestCase):
    def test_report_does_not_open_a_socket_or_award_native_acceptance(self):
        with mock.patch.object(platform_support.socket, "socket", side_effect=AssertionError("socket opened")):
            report = platform_support.platform_report()
        self.assertEqual(report["native_validation"], "not_performed")
        self.assertEqual(report["eligible"], all(report["required_checks"].values()))

    def test_linux_interpreter_minimum(self):
        with mock.patch.object(platform_support.sys, "platform", "linux"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 10, 15)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
                platform_support.require_platform_support()
        with mock.patch.object(platform_support.sys, "platform", "linux"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 11, 0)):
            self.assertTrue(platform_support.platform_report()["required_checks"]["python_minimum"])

    def test_darwin_requires_python_313(self):
        with mock.patch.object(platform_support.sys, "platform", "darwin"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 12, 14)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.13"):
                platform_support.require_platform_support()

    def test_darwin_api_fixture_remains_a_candidate(self):
        with mock.patch.object(platform_support.sys, "platform", "darwin"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 13, 0)):
            report = platform_support.platform_report()
        self.assertEqual(report["support_level"], "candidate")
        self.assertEqual(report["native_validation"], "not_performed")
        self.assertTrue(report["required_checks"]["python_minimum"])

    def test_missing_waitid_is_rejected_even_on_a_new_python(self):
        with mock.patch.object(platform_support.sys, "platform", "darwin"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 13, 0)), \
                mock.patch.object(platform_support.os, "waitid", None, create=True):
            with self.assertRaisesRegex(RuntimeError, "waitid"):
                platform_support.require_platform_support()

    def test_unknown_platform_is_rejected(self):
        with mock.patch.object(platform_support.sys, "platform", "win32"):
            report = platform_support.platform_report()
        self.assertFalse(report["eligible"])
        self.assertEqual(report["support_level"], "unavailable")

    def test_optional_resource_api_is_reported_without_blocking_base_prerequisites(self):
        baseline = platform_support.platform_report()
        with mock.patch.object(platform_support, "resource", None):
            report = platform_support.platform_report()
        self.assertEqual(report["eligible"], baseline["eligible"])
        self.assertEqual(report["optional_checks"], {
            "address_space_limit_api": False, "process_limit_api": False})

    def test_json_cli_returns_nonzero_with_actionable_errors(self):
        output = io.StringIO()
        with mock.patch.object(platform_support.sys, "platform", "darwin"), \
                mock.patch.object(platform_support.sys, "version_info", (3, 12, 0)), \
                redirect_stdout(output):
            status = platform_support.main(["--json"])
        self.assertEqual(status, 2)
        report = json.loads(output.getvalue())
        self.assertFalse(report["eligible"])
        self.assertIn("Python 3.13", " ".join(report["errors"]))


class FrontendInstallationRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cpn.rpnh import frontend_setup
        from cpn.rpnh import frontend_config
        cls.setup = frontend_setup
        cls.config = frontend_config

    def test_profile_coordinates_preserve_defaults_and_explicit_candidates(self):
        expected = {
            ("codex", None): "@openai/codex@0.163.0",
            ("codex", "candidate-0.167.0"): "@openai/codex@0.167.0",
            ("opencode", None): "opencode-ai@1.19.4",
            ("opencode", "2.2.6"): "@opencode/cli@2.2.6",
        }
        for (frontend, profile), coordinate in expected.items():
            with self.subTest(frontend=frontend, profile=profile):
                self.assertEqual(self.setup._npm_package_spec(frontend, profile), coordinate)

    def test_unsupported_profile_has_no_install_fallback(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"RPNH_FRONTEND_CONFIG": str(Path(directory) / "frontend.json")}), \
                mock.patch.object(self.setup, "_npm_install") as install, \
                mock.patch.object(self.setup, "_discover") as discover:
            for profile in ("2.2.5", "99.99.99"):
                with self.subTest(profile=profile), self.assertRaises(ValueError):
                    self.setup.configure_frontend("opencode", opencode_compatibility_profile=profile)
            install.assert_not_called()
            discover.assert_not_called()
            self.assertFalse((Path(directory) / "frontend.json").exists())

    def test_missing_declared_package_is_rejected(self):
        # Resolve profiles before replacing just this manifest read, so this
        # fixture tests installation metadata rather than an alternate loader.
        self.setup._npm_package_spec("opencode", "2.2.6")
        manifest = _ROOT / "cpn/frontend/opencode_compatibility.v1.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        for candidate in document["protocol_candidates"]:
            if candidate["package_version"] == "2.2.6":
                candidate.pop("npm_package")
        original = Path.read_text
        def read_text(path, *args, **kwargs):
            if path == manifest:
                return json.dumps(document)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read_text), \
                self.assertRaisesRegex(ValueError, "No exact npm installation coordinate"):
            self.setup._npm_package_spec("opencode", "2.2.6")

    def test_candidate_setup_passes_its_declared_package_to_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "frontend.json"
            with mock.patch.dict(os.environ, {"RPNH_FRONTEND_CONFIG": str(preferences)}), \
                    mock.patch.object(self.setup, "_discover", side_effect=RuntimeError("not installed")), \
                    mock.patch.object(self.setup, "_npm_install") as install, \
                    mock.patch.object(self.setup, "_probe_binary", side_effect=lambda frontend, binary, profile: binary.resolve()):
                result = self.setup.configure_frontend(
                    "opencode", opencode_compatibility_profile="2.2.6", output_fn=lambda value: None)
                self.assertEqual(install.call_count, 1)
                self.assertEqual(install.call_args.args[0], "@opencode/cli@2.2.6")
                self.assertEqual(install.call_args.args[1], Path(directory) / "frontends/opencode-2.2.6")
                self.assertEqual(result.opencode_compatibility_profile, "2.2.6")
                self.assertEqual(self.config.read_frontend_preferences().opencode_binary, result.opencode_binary)
        from cpn.frontend.opencode_protocol import OPENCODE_VERSION
        self.assertEqual(OPENCODE_VERSION, "1.19.4")

    def test_invalid_platform_stops_before_native_probe_or_install(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"RPNH_FRONTEND_CONFIG": str(Path(directory) / "frontend.json")}), \
                mock.patch.object(self.setup, "require_platform_support", side_effect=RuntimeError("waitid is missing")), \
                mock.patch.object(self.setup, "_discover") as discover, \
                mock.patch.object(self.setup, "_npm_install") as install:
            with self.assertRaisesRegex(RuntimeError, "waitid"):
                self.setup.configure_frontend("codex")
            discover.assert_not_called()
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
