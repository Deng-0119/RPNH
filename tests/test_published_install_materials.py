"""Published/local material correspondence, using offline wheel fixtures.

These checks exercise the original ZIP readers and dependency closure.  The
retained case also uses a real local venv/pip and installed METADATA, without
network access.  Fixture wheels and a declared asset URL confer no official
release qualification or native frontend acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from cpn.rpnh.frontend_launch import observe_runtime
from cpn.rpnh.install_contract import ProfileCatalog, ProfileSpec, build_install_plan
from cpn.rpnh.install_materials import (
    _verify_python, load_materials, prepare_python_environment,
)
from cpn.rpnh.platform_support import platform_report
from tests.test_install_materials import fixture_wheel


SOURCE = Path(__file__).resolve().parents[1]
CATALOG_FILES = (
    "cpn/frontend/codex_compatibility.v1.json", "cpn/frontend/opencode_compatibility.v1.json",
    "integrations/dsh/UPSTREAM.json", "integrations/dsh/UPSTREAM_CANDIDATE.json",
    "integrations/dsh/frontend/web-packages.json", "integrations/dsh/frontend/web-packages.rc1.json",
)


class PublishedMaterialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-published-materials-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.inputs = self.root / "downloaded"
        self.inputs.mkdir(mode=0o700)
        self.material_file = self.root / "materials.json"
        self.version = "0.2.0"
        self.wheel = fixture_wheel(self.inputs, "rpnh-harness", self.version,
                                   ["fixture-dep>=1,<3"])
        self.dependency = fixture_wheel(self.inputs, "fixture-dep", "1.0.0")
        observation = observe_runtime("python", Path(sys.executable).resolve())
        self.runtimes = {"python": {"path": str(observation.path), "version": observation.version,
                         "implementation": observation.implementation, "architecture": observation.architecture}}
        host = platform_report()
        self.platform = {"os": host["platform"], "architecture": host["machine"],
                         "os_release": host["os_release"], "libc": host["libc"], "emulated": None}
        self.catalog = ProfileCatalog(self.version, {"basic": "builtin"}, {
            "basic": {"builtin": ProfileSpec("basic", "builtin", "builtin", {"kind": "builtin"})}})
        self.release_directory = "https://github.com/Deng-0119/RPNH/releases/download/v" + self.version + "/"
        self.source = {"kind": "published_release", "rpnh_version": self.version,
                       "release": "v" + self.version, "channel": "stable",
                       "artifact": {"filename": Path(self.wheel["path"]).name,
                                    "location": self.release_directory + Path(self.wheel["path"]).name}}
        self.plan = self.make_plan(self.source)
        self.document = {"schema_version": "rpnh/install_materials/v2", "platform": self.platform,
                         "runtime_bindings": self.runtimes, "python_wheels": [self.wheel, self.dependency],
                         "frontends": {}, "root_extras": []}
        self.bind_selection()

    def make_plan(self, source):
        return build_install_plan(source, self.platform, self.runtimes, catalog=self.catalog,
                                  installation_id="i-" + "9" * 32)

    def bind_selection(self):
        self.document["published_selection"] = {
            "root_extras": list(self.document["root_extras"]),
            "python_wheels": [{"name": row["name"], "version": row["version"],
                "filename": Path(row["path"]).name, "asset": {
                    "id": 101 + index, "size": Path(row["path"]).stat().st_size,
                    "url": self.release_directory + Path(row["path"]).name}}
                for index, row in enumerate(self.document["python_wheels"])],
        }

    def load(self, *, installed=False, catalog=True):
        self.material_file.write_text(json.dumps(self.document), encoding="utf-8")
        return load_materials(self.material_file, self.plan,
                              catalog=self.catalog if catalog else None, installed=installed)

    def local_plan(self):
        source = {"kind": "local_candidate", "rpnh_version": self.version,
                  "release": None, "channel": None,
                  "artifact": {"filename": Path(self.wheel["path"]).name, "location": self.wheel["path"]}}
        return self.make_plan(source)

    def test_published_source_stays_a_url_and_local_materials_are_independent(self):
        original = self.plan.to_document()
        with patch("subprocess.Popen", side_effect=AssertionError("material reads execute no commands")):
            loaded = self.load()
        self.assertEqual(loaded.plan, original)
        self.assertEqual(loaded.plan["source"], self.source)
        self.assertEqual(loaded.document["published_selection"]["root_extras"], [])
        self.assertEqual({row["name"] for row in loaded.wheels}, {"rpnh-harness", "fixture-dep"})
        self.document["published_selection"]["python_wheels"][0]["asset"]["id"] = 999
        self.assertNotEqual(loaded.document["published_selection"], self.document["published_selection"])
        self.assertEqual(self.plan.to_document(), original)

    def test_original_local_v1_closure_accepts_either_in_range_exact_selection(self):
        self.plan = self.local_plan()
        self.document["schema_version"] = "rpnh/install_materials/v1"
        del self.document["published_selection"]
        del self.document["root_extras"]
        self.assertEqual(self.load().wheels[1]["version"], "1.0.0")
        self.document["python_wheels"][1] = fixture_wheel(self.inputs, "fixture-dep", "2.0.0")
        self.assertEqual(self.load().wheels[1]["version"], "2.0.0")

    def test_different_but_in_range_dependency_does_not_reuse_published_selection(self):
        self.document["python_wheels"][1] = fixture_wheel(self.inputs, "fixture-dep", "2.0.0")
        with self.assertRaisesRegex(ValueError, "published.*selection|selection.*published"):
            self.load()

    def test_v1_published_and_v2_local_sources_are_rejected(self):
        original = deepcopy(self.document)
        self.document["schema_version"] = "rpnh/install_materials/v1"
        del self.document["published_selection"]
        with self.assertRaisesRegex(ValueError, "local|published|v2"):
            self.load()
        self.document = original
        self.plan = self.local_plan()
        with self.assertRaisesRegex(ValueError, "local|published"):
            self.load()

    def test_published_selection_and_root_extras_are_both_required(self):
        original = deepcopy(self.document)
        for field in ("published_selection", "root_extras"):
            with self.subTest(field=field):
                self.document = deepcopy(original)
                del self.document[field]
                with self.assertRaisesRegex(ValueError, "fields"):
                    self.load()
        self.document = original
        del self.document["published_selection"]["root_extras"]
        with self.assertRaisesRegex(ValueError, "fields"):
            self.load()

    def test_duplicate_local_distribution_is_rejected(self):
        self.document["python_wheels"].append(deepcopy(self.dependency))
        with self.assertRaisesRegex(ValueError, "repeats"):
            self.load()

    def test_missing_and_extra_local_wheels_do_not_match_the_published_set(self):
        original = deepcopy(self.document)
        self.document["python_wheels"].pop()
        with self.assertRaisesRegex(ValueError, "published.*selection|selection.*published"):
            self.load()
        self.document = original
        self.document["python_wheels"].append(fixture_wheel(self.inputs, "unselected", "1.0.0"))
        with self.assertRaisesRegex(ValueError, "published.*selection|selection.*published"):
            self.load()

    def test_same_name_version_with_a_different_wheel_variant_is_rejected(self):
        self.document["python_wheels"][1] = fixture_wheel(self.inputs, "fixture-dep", "1.0.0",
                                                         tag="cp312-none-manylinux_2_17_x86_64")
        with self.assertRaisesRegex(ValueError, "published.*selection|selection.*published"):
            self.load()

    def test_selected_asset_size_must_match_local_wheel_before_preparation(self):
        self.document["published_selection"]["python_wheels"][1]["asset"]["size"] += 1
        with self.assertRaisesRegex(ValueError, "size"):
            self.load()

    def test_source_release_and_rpnh_identity_must_match_selection(self):
        original = deepcopy(self.document)
        self.document["published_selection"]["python_wheels"][0]["asset"]["url"] = (
            self.release_directory.replace("v0.2.0", "v0.3.0") + Path(self.wheel["path"]).name)
        with self.assertRaisesRegex(ValueError, "coordinate|source"):
            self.load()
        self.document = original
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", "0.3.0")
        with self.assertRaisesRegex(ValueError, "selected|selection"):
            self.load()

    def test_published_url_is_never_a_local_material_path(self):
        self.document["python_wheels"][0]["path"] = self.source["artifact"]["location"]
        with self.assertRaisesRegex(ValueError, "local wheels"):
            self.load()

    def test_explicit_extras_must_match_selection_and_actual_optional_closure(self):
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", self.version,
            ["fixture-dep==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        self.document["root_extras"] = ["skills"]
        self.bind_selection()
        self.assertEqual(self.load().document["root_extras"], ["skills"])
        self.document["root_extras"] = []
        with self.assertRaisesRegex(ValueError, "root_extras"):
            self.load()
        self.document["published_selection"]["root_extras"] = []
        with self.assertRaisesRegex(ValueError, "outside"):
            self.load()

    def test_exact_published_set_does_not_replace_transitive_metadata_validation(self):
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", self.version,
            ["fixture-dep==1.0.0", "missing-transitive==3.0.0"])
        self.bind_selection()
        with self.assertRaisesRegex(ValueError, "closure"):
            self.load()
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", self.version)
        self.bind_selection()
        with self.assertRaisesRegex(ValueError, "outside"):
            self.load()

    def test_catalog_is_read_from_the_selected_local_wheel_without_rewriting_source(self):
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", self.version,
            ["fixture-dep>=1,<3"], extra_members={name: (SOURCE / name).read_bytes() for name in CATALOG_FILES})
        self.bind_selection()
        with patch("subprocess.Popen", side_effect=AssertionError("catalog reads execute no commands")):
            material = self.load(catalog=False)
        self.assertEqual(material.plan["source"], self.source)

    def test_retained_declarations_are_checked_without_reopening_deleted_input_tree(self):
        self.load()
        shutil.rmtree(self.inputs)
        with patch("cpn.rpnh.install_materials._path", side_effect=AssertionError("no input filesystem reads")), \
                patch("cpn.rpnh.install_materials.ZipFile", side_effect=AssertionError("no original wheel reads")):
            retained = self.load(installed=True)
        self.assertTrue(retained.retained_probe_only)
        stage = self.root / "stage"
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "retained"):
            prepare_python_environment(stage, self.plan, retained)
        self.assertFalse((stage / "venv").exists())
        self.document["python_wheels"][1]["path"] = str(self.inputs / "fixture_dep-1.0.0-cp312-none-any.whl")
        with self.assertRaisesRegex(ValueError, "published.*selection|selection.*published"):
            self.load(installed=True)

    def test_real_installed_metadata_is_rechecked_after_published_inputs_are_deleted(self):
        self.document["python_wheels"][0] = fixture_wheel(self.inputs, "rpnh-harness", self.version,
            ["fixture-dep==1.0.0; extra == 'skills'"], provides_extras=("skills",))
        self.document["root_extras"] = ["skills"]
        self.bind_selection()
        material = self.load()
        stage = self.root / "stage"
        stage.mkdir(mode=0o700)
        prepare_python_environment(stage, self.plan, material)
        shutil.rmtree(self.inputs)
        retained = self.load(installed=True)
        observed = _verify_python(stage, retained)
        self.assertEqual(observed["root_extras"], ["skills"])
        self.assertEqual(observed["distributions"], {"rpnh-harness": self.version, "fixture-dep": "1.0.0"})
        metadata = next((stage / "venv/lib").glob("python*/site-packages/rpnh_harness-*.dist-info/METADATA"))
        original = metadata.read_text(encoding="utf-8")
        metadata.write_text(original.replace("fixture-dep==1.0.0", "fixture-dep==2.0.0"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "closure"):
            _verify_python(stage, retained)
        metadata.write_text(original.replace("Provides-Extra: skills\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not declared"):
            _verify_python(stage, retained)


if __name__ == "__main__":
    unittest.main()
