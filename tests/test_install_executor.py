"""Real offline executor transactions using an explicitly reduced fixture app.

The wheels include the production installation modules and a tiny placeholder
application, plus a locally repacked installed packaging module. This exercises
real clean venv/pip, candidate processes, inherited flock and pointer commits.
It is not the complete RPNH dependency closure or native frontend acceptance.
"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from importlib import metadata
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from cpn.rpnh import install_executor as executor
from cpn.rpnh.install_contract import build_install_plan, load_profile_catalog, read_frontend_view
from cpn.rpnh.install_v2 import ActivationError, marker, read_target, recover
from cpn.rpnh.platform_support import platform_report
from cpn.rpnh.frontend_launch import observe_runtime
from tests.test_install_materials import fixture_wheel


SOURCE = Path(__file__).resolve().parents[1]
PRODUCTION_MODULES = (
    "cpn/__init__.py", "cpn/update_context.py",
    "cpn/process_supervision.py", "cpn/_process_supervisor.py",
    "cpn/rpnh/install_contract.py", "cpn/rpnh/install_v2.py",
    "cpn/rpnh/install_candidate.py", "cpn/rpnh/install_executor.py",
    "cpn/rpnh/install_materials.py", "cpn/rpnh/frontend_config.py",
    "cpn/rpnh/frontend_launch.py", "cpn/rpnh/platform_support.py",
    "cpn/rpnh/update_install.py", "cpn/rpnh/updates.py",
)
CATALOG_FILES = (
    "cpn/frontend/codex_compatibility.v1.json",
    "cpn/frontend/opencode_compatibility.v1.json",
    "integrations/dsh/UPSTREAM.json", "integrations/dsh/UPSTREAM_CANDIDATE.json",
    "integrations/dsh/frontend/web-packages.json",
    "integrations/dsh/frontend/web-packages.rc1.json",
)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-executor-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "managed"
        self.material_file = self.directory / "materials.json"
        self.version = "0.1.0rc2"
        self.packaging = metadata.distribution("packaging")
        # Repack only this installed pure-Python distribution for the isolated
        # test dependency. Its origin is explicit; it is not a release asset.
        package_members = {str(item): self.packaging.locate_file(item).read_bytes()
                           for item in self.packaging.files
                           if str(item).startswith("packaging/") and "__pycache__" not in str(item)
                           and self.packaging.locate_file(item).is_file()}
        dependency = fixture_wheel(self.directory, "packaging", self.packaging.version,
                                   extra_members=package_members)
        self.fixture_members = {name: (SOURCE / name).read_bytes()
                                for name in (*PRODUCTION_MODULES, *CATALOG_FILES)}
        self.fixture_members.update({
            "cpn/rpnh/__init__.py": "# Reduced fixture app; installation modules are production sources.\n",
            "cpn/fixture_entry.py": "def main():\n    print('fixture presentation only')\n    return 0\n",
            "cpn/fixture_marker.txt": "original\n",
            f"rpnh_harness-{self.version}.dist-info/entry_points.txt":
                "[console_scripts]\nrpnh = cpn.fixture_entry:main\n",
        })
        self.wheel = self._wheel()
        observed = observe_runtime("python", Path(sys.executable).resolve())
        runtime = {"python": {"path": str(observed.path), "version": observed.version,
                    "implementation": observed.implementation, "architecture": observed.architecture}}
        host = platform_report()
        target = {"os": host["platform"], "architecture": host["machine"],
                  "os_release": host["os_release"], "libc": host["libc"], "emulated": None}
        self.document = {"schema_version": "rpnh/install_materials/v1", "platform": target,
                         "runtime_bindings": runtime, "python_wheels": [self.wheel, dependency],
                         "frontends": {}}
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")

    def _wheel(self):
        return fixture_wheel(self.directory, "rpnh-harness", self.version,
                             ["packaging==" + self.packaging.version,
                              "fixture-addon==1.0.0; extra == 'feature'"],
                             provides_extras=("feature",), extra_members=self.fixture_members)

    def plan(self):
        return executor.plan_local_install(Path(self.wheel["path"]), self.material_file, root=self.root)

    def install(self, **options):
        plan = self.plan()
        return plan, executor.execute_install(self.root, plan, self.material_file, **options)

    def test_real_bootstrap_reconfigure_broken_current_rollback_and_deleted_inputs(self):
        addon = fixture_wheel(self.directory, "fixture-addon", "1.0.0")
        self.document["root_extras"] = ["feature"]
        self.document["python_wheels"].append(addon)
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        first, result = self.install(selected_frontend="auto")
        self.assertTrue(result.activation.committed)
        self.assertEqual(read_frontend_view(self.root / first.target)["frontend"], "auto")
        self.assertIsNone(result.activation.previous)
        self.assertTrue((self.root / first.target / "probe.json").is_file())
        first_probe = json.loads((self.root / first.target / "probe.json").read_text())
        self.assertEqual(first_probe["python"]["root_extras"], ["feature"])
        self.assertIn("fixture-addon", first_probe["python"]["distributions"])
        self.assertFalse(result.to_document()["tasks_resumed"])
        mislabeled = replace(self.plan(), operation="upgrade")
        with self.assertRaisesRegex(ValueError, "operation does not match"):
            executor.execute_install(self.root, mislabeled, self.material_file)
        self.assertFalse((self.root / mislabeled.target).exists())
        self.document.pop("root_extras")
        self.document["python_wheels"].remove(addon)
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "explicit"):
            self.plan()
        self.document["root_extras"] = []
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        second, updated = self.install()
        self.assertEqual(second.operation, "reconfigure")
        self.assertEqual(updated.activation.previous, first.target)
        self.assertEqual(read_frontend_view(self.root / second.target)["frontend"], "auto")
        self.assertTrue(executor.same_release_payload(self.root / first.target, self.root / second.target))
        second_probe = json.loads((self.root / second.target / "probe.json").read_text())
        self.assertEqual(second_probe["python"]["root_extras"], [])
        self.assertNotIn("fixture-addon", second_probe["python"]["distributions"])
        # A broken outgoing interpreter and deleted input wheels do not make a
        # healthy retained destination unavailable to an independent manager.
        (self.root / second.target / "venv/bin/python").unlink()
        for path in self.directory.glob("*.whl"):
            path.unlink()
        restored = executor.rollback_local(self.root)
        self.assertEqual(restored.activation.current, first.target)
        self.assertEqual(restored.activation.previous, first.target)
        restored_probe = json.loads((self.root / first.target / "probe.json").read_text())
        self.assertEqual(restored_probe["python"]["root_extras"], ["feature"])
        self.assertIn("fixture-addon", restored_probe["python"]["distributions"])
        repeated = executor.rollback_local(self.root)
        self.assertEqual(repeated.activation, restored.activation)
        self.assertIsNone(marker(self.root)["pending_activation"])

    def test_same_version_different_payload_never_gets_complete_or_current(self):
        first, _ = self.install()
        self.fixture_members["cpn/fixture_marker.txt"] = "changed under the same R\n"
        self._wheel()
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "changed the RPNH payload"):
            executor.execute_install(self.root, plan, self.material_file)
        self.assertEqual(read_target(self.root)[0], first.target)
        self.assertFalse((self.root / plan.target / "installed.json").exists())
        self.assertTrue((self.root / plan.target / "prepared.json").exists())

    def test_failed_stage_is_retained_and_a_fresh_identity_can_retry(self):
        plan = self.plan()
        with patch("cpn.rpnh.install_materials.prepare_python_environment", side_effect=RuntimeError("fixture interruption")):
            with self.assertRaisesRegex(RuntimeError, "fixture interruption"):
                executor.execute_install(self.root, plan, self.material_file)
        self.assertTrue((self.root / plan.target / "plan.json").exists())
        self.assertIsNone(read_target(self.root, allow_missing=True))
        with self.assertRaises(FileExistsError):
            executor.execute_install(self.root, plan, self.material_file)
        fresh = self.plan()
        self.assertNotEqual(fresh.installation_id, plan.installation_id)
        result = executor.execute_install(self.root, fresh, self.material_file)
        self.assertEqual(result.activation.current, fresh.target)
        self.assertFalse((self.root / plan.target / "installed.json").exists())

    def test_post_commit_failure_reports_commit_and_metadata_recovery(self):
        plan = self.plan()
        def fail(stage):
            if stage == "current":
                raise OSError("injected after current replacement")
        with self.assertRaises(ActivationError) as raised:
            executor.execute_install(self.root, plan, self.material_file, event=fail)
        self.assertIs(raised.exception.committed, True)
        self.assertEqual(raised.exception.current, plan.target)
        self.assertIsNotNone(marker(self.root)["pending_activation"])
        # Recovery only reads complete software records. It does not probe a
        # subsequently damaged environment or restart the fixture application.
        (self.root / plan.target / "venv/bin/python").unlink()
        result = recover(self.root)
        self.assertTrue(result.recovered)
        self.assertTrue(result.committed)
        self.assertEqual(result.current, plan.target)
        self.assertIsNone(marker(self.root)["pending_activation"])

    def test_incomplete_candidate_context_cannot_create_root(self):
        plan = self.plan()
        with patch.dict(os.environ, {"RPNH_CANDIDATE_ID": ""}):
            with self.assertRaisesRegex(ValueError, "fixed prepare/probe"):
                executor.execute_install(self.root, plan, self.material_file)
        self.assertFalse(self.root.exists())

    def test_missing_material_never_creates_root(self):
        plan = self.plan()
        Path(self.wheel["path"]).unlink()
        with self.assertRaises((ValueError, OSError)):
            executor.execute_install(self.root, plan, self.material_file)
        self.assertFalse(self.root.exists())

    def test_material_selection_changed_during_confirmation_cannot_create_root(self):
        plan = self.plan()
        confirmed = deepcopy(self.document)
        self.document["root_extras"] = ["feature"]
        self.document["python_wheels"].append(fixture_wheel(self.directory, "fixture-addon", "1.0.0"))
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after confirmation"):
            executor.execute_install(self.root, plan, self.material_file, expected_materials=confirmed)
        self.assertFalse(self.root.exists())

    def test_cli_keeps_post_commit_fact_in_error(self):
        from cpn.rpnh.update_cli import main
        plan = self.plan()
        output = io.StringIO()
        with patch.object(executor, "plan_local_install", return_value=plan), \
                patch.object(executor, "execute_install", side_effect=ActivationError(current=plan.target, committed=True)), \
                patch("cpn.rpnh.update_cli._confirm"), patch("sys.stdout", output):
            code = main(["apply", "--root", str(self.root), "--candidate-wheel", self.wheel["path"],
                         "--materials", str(self.material_file), "--json"])
        self.assertEqual(code, 2)
        document = json.loads(output.getvalue())
        self.assertTrue(document["committed"])
        self.assertEqual(document["activation_status"], "committed_recovery_required")
        self.assertFalse(self.root.exists())

    def test_cli_describes_materials_without_installing_or_overwriting(self):
        from cpn.rpnh.update_cli import main
        output = self.directory / "described-materials.json"
        argv = ["materials", "--candidate-wheel", self.wheel["path"],
                "--wheelhouse", str(self.directory), "--python", str(Path(sys.executable).resolve()),
                "--output", str(output)]
        with patch("sys.stdout", io.StringIO()):
            self.assertEqual(main(argv), 0)
        document = json.loads(output.read_text())
        self.assertEqual(document["schema_version"], "rpnh/install_materials/v1")
        self.assertEqual(document["root_extras"], [])
        self.assertEqual({row["name"] for row in document["python_wheels"]}, {"rpnh-harness", "packaging"})
        self.assertFalse(self.root.exists())
        content = output.read_bytes()
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(argv), 2)
        self.assertEqual(output.read_bytes(), content)
        fixture_wheel(self.directory, "fixture-addon", "1.0.0")
        with_extras = self.directory / "with-extras.json"
        argv[-1] = str(with_extras)
        with patch("sys.stdout", io.StringIO()):
            self.assertEqual(main([*argv, "--extra", "feature"]), 0)
        selected = json.loads(with_extras.read_text())
        self.assertEqual(selected["root_extras"], ["feature"])
        self.assertEqual({row["name"] for row in selected["python_wheels"]},
                         {"rpnh-harness", "packaging", "fixture-addon"})


if __name__ == "__main__":
    unittest.main()
