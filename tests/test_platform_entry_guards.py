"""Entry wiring tests; no model, native client, Registry, or socket is used."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from cpn import rpnh_cli
from cpn.frontend import dsh_assets, dsh_launcher
from cpn.rpnh import frontend_setup, install_v2, update_cli
from cpn.rpnh.frontend_config import save_frontend_preferences
from cpn.rpnh.main_session import MainSession
from tests.test_install_contract_v2 import installed_document, with_component


class EntryGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rpnh-entry-guards-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = mock.patch.dict(os.environ, {
            "RPNH_FRONTEND_CONFIG": str(self.root / "frontend.json")}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_candidate_rejected_before_every_early_cli_dispatch(self):
        with mock.patch.dict(os.environ, {"RPNH_CANDIDATE_FD": ""}), \
                mock.patch.object(rpnh_cli, "_main") as main, redirect_stderr(io.StringIO()):
            for command in ("update", "frontend", "_environment-host", "package", "origin", "plugins", "help"):
                self.assertEqual(rpnh_cli.main([command]), 2)
            main.assert_not_called()

    def test_candidate_cannot_construct_main_session_or_enter_software_helpers(self):
        from cpn.frontend import server
        with mock.patch.dict(os.environ, {"RPNH_CANDIDATE_ID": ""}), \
                mock.patch.object(frontend_setup, "_configure_frontend") as setup, \
                mock.patch.object(server, "_serve_projection") as serve:
            with self.assertRaises(ValueError):
                MainSession(self.root / "new-session", self.root / "missing-profile")
            with self.assertRaises(ValueError):
                frontend_setup.configure_frontend("basic")
            with self.assertRaises(ValueError):
                server.serve_projection(None)
            setup.assert_not_called()
            serve.assert_not_called()
        self.assertFalse((self.root / "new-session").exists())

    def test_dsh_profile_precedence_and_explicit_asset_profile_are_stable(self):
        save_frontend_preferences("dsh", dsh_compatibility_profile="0.2.2-rc.1")
        self.assertEqual(dsh_launcher.resolve_dsh_profile(), "0.2.2-rc.1")
        with mock.patch.dict(os.environ, {dsh_assets.DSH_CANDIDATE_ENV: "0.2.2-alpha.2"}):
            self.assertEqual(dsh_launcher.resolve_dsh_profile(), "0.2.2-alpha.2")
            self.assertEqual(dsh_launcher.resolve_dsh_profile("0.2.2-rc.1"), "0.2.2-rc.1")
            self.assertEqual(dsh_assets.official_web_profile("0.2.2-rc.1")[0]["version"], "0.2.2-rc.1")
        with mock.patch.dict(os.environ, {dsh_assets.DSH_CANDIDATE_ENV: "unknown"}):
            self.assertEqual(dsh_assets.official_web_profile("0.2.2-alpha.2")[0]["version"], "0.2.2-alpha.2")
            with self.assertRaises(RuntimeError):
                dsh_launcher.resolve_dsh_profile()

    def test_explicit_dsh_setup_passes_and_persists_one_profile_without_download(self):
        source = self.root / "dsh"
        source.mkdir()
        with mock.patch.object(frontend_setup, "_discover", return_value=source) as discover, \
                mock.patch.object(frontend_setup, "_install_dsh", side_effect=AssertionError("download")), \
                mock.patch.dict(os.environ, {dsh_assets.DSH_CANDIDATE_ENV: "0.2.2-alpha.2"}):
            selected = frontend_setup.configure_frontend("dsh", install=False, dsh_source=source,
                        dsh_compatibility_profile="0.2.2-rc.1", output_fn=lambda _message: None)
        self.assertEqual(selected.dsh_compatibility_profile, "0.2.2-rc.1")
        self.assertEqual(discover.call_args.kwargs["dsh_profile"], "0.2.2-rc.1")

    def make_wheel(self):
        repository = Path(__file__).resolve().parents[1]
        wheel = self.root / "rpnh_harness-0.1.0rc2-py3-none-any.whl"
        names = ["cpn/frontend/codex_compatibility.v1.json", "cpn/frontend/opencode_compatibility.v1.json",
                 "integrations/dsh/UPSTREAM.json", "integrations/dsh/UPSTREAM_CANDIDATE.json",
                 "integrations/dsh/frontend/web-packages.json", "integrations/dsh/frontend/web-packages.rc1.json"]
        with ZipFile(wheel, "w") as archive:
            archive.writestr("rpnh_harness-0.1.0rc2.dist-info/METADATA", "Name: rpnh-harness\nVersion: 0.1.0rc2\n")
            for name in names:
                archive.write(repository / name, name)
        return wheel

    def plan(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = rpnh_cli.main(["update", "plan", "--candidate-wheel", str(self.make_wheel()), "--json", *args])
        return result, json.loads(output.getvalue())

    def test_cli_plan_is_read_only_and_does_not_claim_installability(self):
        destination = self.root / "not-created"
        result, value = self.plan("--root", str(destination))
        self.assertEqual(result, 0)
        self.assertEqual(set(value["plan"]["components"]), {"basic"})
        self.assertFalse(destination.exists())
        self.assertEqual(value["qualification"]["activation"], "not_enabled_without_fixed_materials_and_admission")

    def test_cli_plan_keeps_all_selected_frontends_when_runtime_materials_are_missing(self):
        result, value = self.plan("--frontend", "codex", "--frontend", "opencode", "--frontend", "dsh")
        self.assertEqual(result, 1)
        self.assertEqual(set(value["plan"]["components"]), {"basic", "codex", "opencode", "dsh-web"})
        self.assertTrue(value["plan"]["conflicts"])
        self.assertEqual(value["plan"]["source"]["kind"], "local_candidate")

    def test_cli_plan_without_selection_preserves_existing_profiles_and_bindings(self):
        managed = install_v2.create_root(self.root / "managed")
        record = with_component(installed_document(), "codex", "candidate-0.167.0", external="/external/codex")
        target = "versions/" + record["installation_id"]
        directory = managed / target
        directory.mkdir(mode=0o700)
        install_v2._write_json(directory / "installed.json", record)
        install_v2._set_pointer(managed, "current", target)
        before = (directory / "installed.json").read_bytes()
        _, result = self.plan("--root", str(managed))
        self.assertEqual(result["plan"]["components"]["codex"], {
            "profile": "candidate-0.167.0", "binding": {"kind": "external", "path": "/external/codex"}})
        self.assertEqual((directory / "installed.json").read_bytes(), before)
        self.assertEqual(len(list((managed / "versions").iterdir())), 1)

    def test_bad_wheel_is_reported_as_input_error_without_creating_installation(self):
        wheel = self.root / "invalid.whl"
        wheel.write_text("not a wheel")
        output = io.StringIO()
        with redirect_stdout(output):
            result = update_cli.main(["plan", "--candidate-wheel", str(wheel), "--json"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["operation"], "plan")

    def test_v2_status_and_startup_never_consume_or_mutate_a_v1_offer_cache(self):
        from cpn.rpnh import updates
        managed = install_v2.create_root(self.root / "managed")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"RPNH_MANAGED_ROOT": str(managed)}), \
                mock.patch.object(updates, "refresh", side_effect=AssertionError("legacy refresh")), \
                mock.patch.object(updates, "status", side_effect=AssertionError("legacy cache")), \
                redirect_stdout(output):
            self.assertEqual(update_cli.main(["check", "--frontend", "codex", "--json"]), 0)
            self.assertFalse(updates.startup("codex", interactive=True))
        self.assertIsNone(json.loads(output.getvalue())["offer"])

    def test_legacy_bootstrap_nonexistent_root_still_reaches_legacy_discovery(self):
        from cpn.rpnh import updates
        with mock.patch.object(updates, "read_state", return_value={"channel": "prerelease"}), \
                mock.patch.object(updates, "discover", return_value=[]) as discover, redirect_stderr(io.StringIO()):
            result = update_cli.main(["install", "--root", str(self.root / "new-v1"),
                                      "--frontend", "basic", "--version", "1.0.0"])
        self.assertEqual(result, 2)
        discover.assert_called_once()
        self.assertFalse((self.root / "new-v1").exists())

    def test_managed_dsh_status_rejects_a_probe_that_changes_the_resolved_entry(self):
        from types import SimpleNamespace
        from cpn.rpnh import frontend_launch
        from cpn.rpnh.frontend_config import FrontendPreferences
        spec = SimpleNamespace(source=self.root, profile="0.2.2-rc.1", argv=("/fixed/node",),
                               entry_path=self.root / "frontend.ts")
        preferences = FrontendPreferences(frontend="dsh", installation_id="i-" + "1" * 32)
        with mock.patch.object(frontend_setup, "read_frontend_preferences", return_value=preferences), \
                mock.patch.object(frontend_launch, "resolve_current_launch_spec", return_value=spec), \
                mock.patch.object(dsh_launcher, "probe_dsh_frontend", return_value=("/different/node", spec.entry_path)):
            status = frontend_setup._frontend_status("dsh")
        self.assertFalse(status["available"])
        self.assertIn("changed after launch resolution", status["detail"])

    def test_mac_plan_uses_the_contract_os_name_without_awarding_native_qualification(self):
        with mock.patch("platform.system", return_value="Darwin"), \
                mock.patch("platform.machine", return_value="arm64"), \
                mock.patch("platform.libc_ver", return_value=("", "")):
            result, value = self.plan()
        self.assertEqual(result, 0)
        self.assertEqual(value["plan"]["platform"]["os"], "darwin")
        self.assertEqual(value["qualification"]["native_platform"], "not_verified")

    def test_auto_reports_an_explicit_stale_codex_binding_instead_of_falling_back(self):
        save_frontend_preferences("auto", codex_binary=self.root / "absent-explicit-client")
        with mock.patch.object(rpnh_cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(frontend_setup, "frontend_status", return_value={
                    "frontend": "codex", "available": False, "detail": "explicit client is unavailable"}):
            with self.assertRaisesRegex(RuntimeError, "explicit client is unavailable"):
                rpnh_cli._choose_frontend("auto")


if __name__ == "__main__":
    unittest.main()
