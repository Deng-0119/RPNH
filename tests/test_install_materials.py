"""Offline material checks and real pip/venv tests with tiny fixture wheels.

The fixture distributions are not a real RPNH dependency closure or native
frontend acceptance.  The two filesystem recipes exercise actual Python venv,
pip, isolation, installed metadata and a failed-wheel path without networking.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from cpn.rpnh.frontend_launch import observe_runtime
from cpn.rpnh.install_contract import (
    ProfileCatalog, ProfileSpec, build_install_plan,
)
from cpn.rpnh.install_materials import (
    MATERIALS_SCHEMA, _copy_tree, _frontend_materials, _inventory,
    _native_header, _tree_check, _verify_python, describe_basic_materials,
    ensure_explicit_reconfigure_extras, load_materials, prepare_python_environment,
    selected_root_extras,
)
from cpn.rpnh.platform_support import platform_report


def fixture_wheel(directory, name, version, requirements=(), *, requires_python=None, provides_extras=(),
                  tag="py3-none-any", extra_members=None):
    normalized = name.replace("-", "_")
    path = directory / f"{normalized}-{version}-{tag}.whl"
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if requires_python:
        metadata += f"Requires-Python: {requires_python}\n"
    metadata += "".join(f"Provides-Extra: {extra}\n" for extra in provides_extras)
    metadata += "".join(f"Requires-Dist: {item}\n" for item in requirements) + "\n"
    entries = {
        dist_info + "/METADATA": metadata,
        dist_info + "/WHEEL": f"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: {tag}\n",
        normalized + ".py": "VALUE = 'offline fixture'\n",
        **(extra_members or {}),
    }
    entries[dist_info + "/RECORD"] = "".join(f"{entry},,\n" for entry in entries) + dist_info + "/RECORD,,\n"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for entry, data in entries.items():
            archive.writestr(entry, data)
    return {"path": str(path), "name": name, "version": version}


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-material-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.material_file = self.root / "materials.json"
        self.version = "0.1.0rc2"
        self.wheel = fixture_wheel(self.root, "rpnh-harness", self.version)
        observed = observe_runtime("python", Path(sys.executable).resolve())
        self.runtimes = {"python": {"path": str(observed.path), "version": observed.version,
                         "implementation": observed.implementation, "architecture": observed.architecture}}
        host = platform_report()
        self.platform = {"os": host["platform"], "architecture": host["machine"],
                         "os_release": host["os_release"], "libc": host["libc"], "emulated": None}
        self.catalog = ProfileCatalog(self.version, {"basic": "builtin"}, {
            "basic": {"builtin": ProfileSpec("basic", "builtin", "builtin", {"kind": "builtin"})}})
        self.source = {"kind": "local_candidate", "rpnh_version": self.version, "release": None,
                       "artifact": {"filename": Path(self.wheel["path"]).name, "location": self.wheel["path"]},
                       "channel": None}
        self.plan = build_install_plan(self.source, self.platform, self.runtimes, catalog=self.catalog,
                                      installation_id="i-" + "1" * 32)
        self.document = {"schema_version": MATERIALS_SCHEMA, "platform": self.platform,
                         "runtime_bindings": self.runtimes, "python_wheels": [self.wheel], "frontends": {}}

    def load(self, **kwargs):
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        return load_materials(self.material_file, self.plan, catalog=self.catalog, **kwargs)

    def test_exact_transitive_closure_and_extra_markers(self):
        self.document["python_wheels"][0] = fixture_wheel(self.root, "rpnh-harness", self.version,
            ["fixture-dep[feature]>=1,<2", "absent-package; python_version < '3.0'"])
        self.document["python_wheels"] += [
            fixture_wheel(self.root, "fixture-dep", "1.2.0", ["fixture-extra==2.0.0; extra == 'feature'"],
                          provides_extras=("feature",)),
            fixture_wheel(self.root, "fixture-extra", "2.0.0"),
        ]
        material = self.load()
        self.assertEqual({item["name"] for item in material.wheels}, {"rpnh-harness", "fixture-dep", "fixture-extra"})

    def test_missing_transitive_dependency_rejected_before_commands(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, ["fixture-dep>=1"])
        with patch("subprocess.run", side_effect=AssertionError("preflight must not execute")):
            with self.assertRaisesRegex(ValueError, "closure"):
                self.load()

    def test_wrong_transitive_version_rejected(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, ["fixture-dep<2"])
        self.document["python_wheels"].append(fixture_wheel(self.root, "fixture-dep", "2.0.0"))
        with self.assertRaisesRegex(ValueError, "closure"):
            self.load()

    def test_unneeded_distribution_rejected(self):
        self.document["python_wheels"].append(fixture_wheel(self.root, "unneeded", "1.0.0"))
        with self.assertRaisesRegex(ValueError, "outside"):
            self.load()

    def test_duplicate_distribution_rejected(self):
        self.document["python_wheels"].append(deepcopy(self.wheel))
        with self.assertRaisesRegex(ValueError, "repeats"):
            self.load()

    def test_root_extras_select_the_exact_optional_closure(self):
        fixture_wheel(self.root, "rpnh-harness", self.version,
            ["fixture-skill[feature]==1.0.0; extra == 'skills'", "absent-mcp==2.0.0; extra == 'mcp'"],
            provides_extras=("skills", "mcp"))
        self.document["root_extras"] = ["skills"]
        self.document["python_wheels"] += [
            fixture_wheel(self.root, "fixture-skill", "1.0.0", ["fixture-extra==2.0.0; extra == 'feature'"],
                          provides_extras=("feature",)),
            fixture_wheel(self.root, "fixture-extra", "2.0.0"),
        ]
        material = self.load()
        self.assertEqual(selected_root_extras(material.document), ("skills",))
        self.assertEqual({item["name"] for item in material.wheels},
                         {"rpnh-harness", "fixture-skill", "fixture-extra"})
        self.document["root_extras"] = []
        with self.assertRaisesRegex(ValueError, "outside"):
            self.load()

    def test_root_extras_reject_unknown_duplicate_and_noncanonical_names(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, provides_extras=("skills",))
        for value in (["unknown"], ["skills", "skills"], ["Skills"], ["../skills"], "skills", None):
            with self.subTest(root_extras=value):
                self.document["root_extras"] = value
                with self.assertRaises(ValueError):
                    self.load()

    def test_undeclared_transitive_extra_is_rejected(self):
        fixture_wheel(self.root, "rpnh-harness", self.version,
                      ["fixture-dep[unknown]==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        self.document["root_extras"] = ["skills"]
        self.document["python_wheels"].append(fixture_wheel(self.root, "fixture-dep", "1.0.0"))
        with self.assertRaisesRegex(ValueError, "undeclared extra"):
            self.load()

    def test_missing_requested_extra_dependency_is_not_silently_basic(self):
        fixture_wheel(self.root, "rpnh-harness", self.version,
                      ["fixture-skill==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        legacy = self.load()
        self.assertNotIn("root_extras", legacy.document)
        self.document["root_extras"] = ["skills"]
        with self.assertRaisesRegex(ValueError, "closure"):
            self.load()

    def test_reconfiguration_requires_explicit_extra_selection(self):
        ensure_explicit_reconfigure_extras({}, {})
        ensure_explicit_reconfigure_extras({"root_extras": []}, {"root_extras": ["skills"]})
        ensure_explicit_reconfigure_extras({"root_extras": ["mcp"]}, {"root_extras": ["skills"]})
        with self.assertRaisesRegex(ValueError, "explicit root_extras"):
            ensure_explicit_reconfigure_extras({}, {"root_extras": ["skills"]})

    def test_describe_root_extras_are_explicit_and_never_inferred_from_wheels(self):
        fixture_wheel(self.root, "rpnh-harness", self.version,
                      ["fixture-skill==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        fixture_wheel(self.root, "fixture-skill", "1.0.0")
        with patch("cpn.rpnh.install_materials.load_profile_catalog", return_value=self.catalog):
            with self.assertRaisesRegex(ValueError, "outside"):
                describe_basic_materials(Path(self.wheel["path"]), self.root)
            described = describe_basic_materials(Path(self.wheel["path"]), self.root, root_extras=("skills",))
        self.assertEqual(described["root_extras"], ["skills"])
        self.assertFalse((self.root / "versions").exists())

    def test_retained_extra_closure_uses_actual_metadata_after_source_wheels_deleted(self):
        fixture_wheel(self.root, "rpnh-harness", self.version,
                      ["fixture-skill==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        self.document["root_extras"] = ["skills"]
        self.document["python_wheels"].append(fixture_wheel(self.root, "fixture-skill", "1.0.0"))
        material = self.load()
        stage = self.root / self.plan.installation_id
        stage.mkdir(mode=0o700)
        prepare_python_environment(stage, self.plan, material)
        for wheel in self.document["python_wheels"]:
            Path(wheel["path"]).unlink()
        retained = load_materials(self.material_file, self.plan, catalog=self.catalog, installed=True)
        observed = _verify_python(stage, retained)
        self.assertEqual(observed["root_extras"], ["skills"])
        self.assertEqual(observed["distributions"]["fixture-skill"], "1.0.0")
        self.assertNotIn("jsonschema", _inventory(stage / "venv/bin/python", stage))
        metadata = next((stage / "venv/lib").glob("python*/site-packages/rpnh_harness-*.dist-info/METADATA"))
        original = metadata.read_text()
        metadata.write_text(original.replace("Provides-Extra: skills\n", ""))
        with self.assertRaisesRegex(ValueError, "not declared"):
            _verify_python(stage, retained)
        metadata.write_text(original.replace("fixture-skill==1.0.0", "fixture-skill==2.0.0"))
        with self.assertRaisesRegex(ValueError, "closure"):
            _verify_python(stage, retained)
        metadata.write_text(original)

    def test_url_dependency_rejected_without_network(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, ["fixture-dep @ https://example.invalid/dep.whl"])
        with self.assertRaisesRegex(ValueError, "URL"):
            self.load()

    def test_requires_python_checked_against_selected_interpreter(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, requires_python=">=99")
        with self.assertRaisesRegex(ValueError, "Python"):
            self.load()

    def test_metadata_name_and_filename_must_correspond(self):
        with ZipFile(self.wheel["path"], "a") as archive:
            archive.writestr("other-1.dist-info/METADATA", "Name: other\nVersion: 1\n")
        with self.assertRaisesRegex(ValueError, "METADATA"):
            self.load()

    def test_unsafe_wheel_member_rejected(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, extra_members={"../outside": "bad"})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.load()

    def test_runtime_and_platform_drift_rejected(self):
        self.document = deepcopy(self.document)
        self.document["runtime_bindings"]["python"]["version"] = "3.99.0"
        with self.assertRaisesRegex(ValueError, "different platform/runtime"):
            self.load()

    def test_extra_material_command_field_rejected(self):
        self.document["command"] = ["sh", "-c", "ignored"]
        with self.assertRaisesRegex(ValueError, "fields"):
            self.load()

    def test_missing_selected_frontend_is_not_silently_dropped(self):
        self.document["frontends"] = {"codex": {}}
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            self.load()

    def test_retained_selection_does_not_need_deleted_inputs(self):
        self.load()
        Path(self.wheel["path"]).unlink()
        material = load_materials(self.material_file, self.plan, catalog=self.catalog, installed=True)
        self.assertTrue(material.retained_probe_only)
        stage = self.root / self.plan.installation_id
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "retained"):
            prepare_python_environment(stage, self.plan, material)
        self.assertFalse((stage / "venv").exists())

    def test_tree_import_keeps_relative_links_and_breaks_source_hardlinks(self):
        source, target = self.root / "source", self.root / "target"
        source.mkdir()
        (source / "item").write_text("original")
        os.link(source / "item", source / "hardlink")
        (source / "relative").symlink_to("item")
        _copy_tree(source, target)
        (target / "item").write_text("changed")
        self.assertEqual((source / "item").read_text(), "original")
        self.assertEqual((target / "hardlink").read_text(), "original")
        self.assertEqual((target / "relative").read_text(), "changed")

    def test_tree_rejects_external_symlink(self):
        source = self.root / "source"
        source.mkdir()
        (source / "escape").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "outside"):
            _tree_check(source)

    def test_tree_rejects_special_file(self):
        source = self.root / "source"
        source.mkdir()
        os.mkfifo(source / "fifo")
        with self.assertRaisesRegex(ValueError, "special"):
            _tree_check(source)

    def test_native_header_checks_real_machine_format(self):
        binary = self.root / "native"
        content = bytearray(64)
        content[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", content, 18, 62)
        binary.write_bytes(content)
        _native_header(binary, {"os": "linux", "architecture": "x86_64"})
        with self.assertRaisesRegex(ValueError, "architecture"):
            _native_header(binary, {"os": "linux", "architecture": "arm64"})

    def test_npm_root_lock_mismatch_is_rejected(self):
        source = self.root / "npm-input"
        source.mkdir()
        (source / ".rpnh-npm-cache").mkdir()
        (source / "package.json").write_text(json.dumps({"private": True, "dependencies": {"@openai/codex": "0.163.0"}}))
        (source / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": {"dependencies": {"@openai/codex": "0.155.0"}}}}))
        from cpn.rpnh.install_materials import _npm_root
        specification = ProfileSpec("codex", "pinned-0.163.0", "0.163.0", {"kind": "npm", "package": "@openai/codex"})
        with self.assertRaisesRegex(ValueError, "same exact frontend"):
            _npm_root(source, specification)

    def test_timeout_terminates_actual_grandchild_before_delayed_write(self):
        from cpn.rpnh.install_materials import _run
        marker = self.root / "late-write"
        grandchild = f"import time,pathlib; time.sleep(0.6); pathlib.Path({str(marker)!r}).write_text('late')"
        leader = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(grandchild)
                  + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(10)")
        with self.assertRaises(subprocess.TimeoutExpired):
            _run([sys.executable, "-I", "-c", leader], directory=self.root, timeout=0.2)
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_successful_leader_with_actual_background_writer_is_rejected(self):
        from cpn.rpnh.install_materials import _run
        marker = self.root / "late-write"
        grandchild = f"import time,pathlib; time.sleep(0.6); pathlib.Path({str(marker)!r}).write_text('late')"
        leader = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(grandchild)
                  + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
        with self.assertRaisesRegex(RuntimeError, "unfinished descendants"):
            _run([sys.executable, "-I", "-c", leader], directory=self.root)
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_pnpm_fixed_recipe_disables_both_scripts_and_pnpmfile(self):
        from cpn.rpnh.install_materials import _consume_lock
        source = self.root / "pnpm"
        source.mkdir()
        (source / "package.json").write_text("{}")
        (source / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
        (source / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        bindings = {"node": {"path": "/declared/node"}, "pnpm": {"path": "/declared/pnpm.cjs"}}
        with patch("cpn.rpnh.install_materials._run", return_value="") as run:
            _consume_lock(source, "dsh_tree", bindings, None)
        command = run.call_args.args[0]
        self.assertIn("--ignore-scripts", command)
        self.assertIn("--ignore-pnpmfile", command)
        self.assertIn("--offline", command)
        self.assertIn("--frozen-lockfile", command)
        self.assertIn("--virtual-store-dir", command)
        self.assertIn("--modules-dir", command)
        self.assertIn("--pm-on-fail=error", command)

    def test_pnpm_external_importer_dependency_is_rejected_before_command(self):
        from cpn.rpnh.install_materials import _pnpm_inputs
        source = self.root / "pnpm"
        source.mkdir()
        (source / "package.json").write_text("{}")
        (source / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
        (source / "pnpm-lock.yaml").write_text(
            "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      other:\n        version: link:../../outside\n")
        with self.assertRaisesRegex(ValueError, "outside"):
            _pnpm_inputs(source)

    def test_pnpm_internal_relative_importer_paths_are_retained(self):
        from cpn.rpnh.install_materials import _pnpm_inputs
        source = self.root / "pnpm"
        source.mkdir()
        (source / "package.json").write_text("{}")
        (source / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\noverrides:\n  thing: link:vendor/thing\n")
        (source / "pnpm-lock.yaml").write_text(
            "lockfileVersion: '9.0'\nimporters:\n  packages/one:\n    dependencies:\n      other:\n        version: link:../../vendor/thing\n")
        _pnpm_inputs(source)

    def test_pnpm_yaml_indirection_and_workspace_escape_are_rejected(self):
        from cpn.rpnh.install_materials import _pnpm_inputs
        source = self.root / "pnpm"
        source.mkdir()
        (source / "package.json").write_text("{}")
        (source / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        for workspace in ("packages:\n  - ../outside/*\n", "packages: &outside\n  - packages/*\n",
                          "packages:\n  - packages/*\nuseNodeVersion: '26.0.0'\n",
                          "packages:\n  - '{packages,../../outside}'\n"):
            (source / "pnpm-workspace.yaml").write_text(workspace)
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                _pnpm_inputs(source)

    def test_project_manager_location_override_is_rejected(self):
        from cpn.rpnh.install_materials import _manager_config
        (self.root / ".npmrc").write_text("store-dir=/outside\n")
        with self.assertRaisesRegex(ValueError, "does not permit"):
            _manager_config(self.root)
        (self.root / ".npmrc").write_text("strict-peer-dependencies=false\nnode-linker=hoisted\n")
        _manager_config(self.root)

    def test_describe_basic_materials_uses_entire_exact_directory(self):
        with patch("cpn.rpnh.install_materials.load_profile_catalog", return_value=self.catalog):
            described = describe_basic_materials(Path(self.wheel["path"]), self.root)
        self.assertEqual(described["python_wheels"], [self.wheel])
        self.assertFalse((self.root / "versions").exists())
        fixture_wheel(self.root, "unneeded", "1.0.0")
        with patch("cpn.rpnh.install_materials.load_profile_catalog", return_value=self.catalog):
            with self.assertRaisesRegex(ValueError, "outside"):
                describe_basic_materials(Path(self.wheel["path"]), self.root)

    def test_real_venv_pip_isolated_fixture_install_and_metadata_drift(self):
        fixture_wheel(self.root, "rpnh-harness", self.version, ["fixture-dep==1.0.0"])
        self.document["python_wheels"].append(fixture_wheel(self.root, "fixture-dep", "1.0.0"))
        material = self.load()
        stage = self.root / self.plan.installation_id
        stage.mkdir(mode=0o700)
        observations = prepare_python_environment(stage, self.plan, material)
        self.assertEqual(observations["python"]["path"], str(stage / "venv/bin/python"))
        inventory = _inventory(stage / "venv/bin/python", stage)
        self.assertEqual(inventory["fixture-dep"], "1.0.0")
        self.assertNotIn("jsonschema", inventory)  # Present in the manager; absent from the clean fixture venv.
        self.assertTrue(_verify_python(stage, material)["pip_check"] == "passed")
        metadata = next((stage / "venv/lib").glob("python*/site-packages/fixture_dep-1.0.0.dist-info/METADATA"))
        metadata.write_text(metadata.read_text().replace("Version: 1.0.0", "Version: 2.0.0"))
        with self.assertRaisesRegex(ValueError, "versions differ"):
            _verify_python(stage, material)
        with self.assertRaisesRegex(ValueError, "never reuses"):
            prepare_python_environment(stage, self.plan, material)

    def test_incompatible_wheel_tag_never_installs_distribution(self):
        old = Path(self.wheel["path"])
        old.unlink()
        self.wheel = fixture_wheel(self.root, "rpnh-harness", self.version, tag="cp399-cp399-linux_x86_64")
        # The source contract intentionally requires the universal RPNH wheel;
        # use an incompatible transitive wheel to exercise actual target tags.
        Path(self.wheel["path"]).unlink()
        self.wheel = fixture_wheel(self.root, "rpnh-harness", self.version, ["fixture-dep==1.0.0"])
        self.document["python_wheels"] = [self.wheel, fixture_wheel(self.root, "fixture-dep", "1.0.0", tag="cp399-cp399-linux_x86_64")]
        material = self.load()
        stage = self.root / self.plan.installation_id
        stage.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "incompatible"):
            prepare_python_environment(stage, self.plan, material)
        self.assertNotIn("rpnh-harness", _inventory(stage / "venv/bin/python", stage))


if __name__ == "__main__":
    unittest.main()
