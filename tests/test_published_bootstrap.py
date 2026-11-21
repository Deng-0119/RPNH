"""Offline published-bootstrap transport fixtures and original local execution.

Only published_install.get_json and published_install._open_wheel are replaced.
The official-looking coordinates and native/pass labels below are synthetic
metadata, never evidence that an actual release, platform or frontend passed.
Execution fixtures follow the pre-existing test_install_executor arrangement:
production installation modules, a tiny presentation entry, and its explicitly
temporary packaging dependency. They are not the RPNH fixed dependency closure.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from importlib import metadata
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cpn.rpnh import install_contract as contract
from cpn.rpnh import install_materials, install_v2, published_qualification, updates
from cpn.rpnh import install_executor, published_install as bootstrap
from cpn.rpnh.frontend_launch import observe_runtime
from cpn.rpnh.platform_support import platform_report
from tests.test_install_materials import fixture_wheel
from tests import test_install_executor as executor_fixtures


SOURCE = Path(__file__).resolve().parents[1]
SUPPORT_NAME = "update_support.v3.json"
VALIDATION_NAME = "update_validation.v3.json"


class WheelResponse(BytesIO):
    """Bounded byte stream returned by the one permitted asset transport seam."""
    status = 200

    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class BootstrapFixture:
    """One immutable-looking official index backed only by local fixture bytes."""
    def __init__(self, directory: Path, *, runnable=False, failure=None, extras=()):
        self.directory = directory
        self.inputs = directory / "fixture-inputs"
        self.inputs.mkdir(mode=0o700)
        self.version = "0.2.0"
        self.tag = "v" + self.version
        self.release_directory = f"https://github.com/{updates.REPOSITORY}/releases/download/{self.tag}/"
        observation = observe_runtime("python", Path(sys.executable).resolve())
        self.runtimes = {"python": {"path": str(observation.path), "version": observation.version,
            "implementation": observation.implementation, "architecture": observation.architecture}}
        host = platform_report()
        self.platform = {"os": host["platform"], "architecture": host["machine"],
                         "os_release": host["os_release"], "libc": host["libc"], "emulated": False}
        members = {name: (SOURCE / name).read_bytes() for name in executor_fixtures.CATALOG_FILES}
        if runnable:
            # This is the existing executor test's reduced package arrangement,
            # not a substitute for unavailable project/release dependencies.
            distribution = metadata.distribution("packaging")
            package_members = {str(item): distribution.locate_file(item).read_bytes()
                for item in distribution.files
                if str(item).startswith("packaging/") and "__pycache__" not in str(item)
                and distribution.locate_file(item).is_file()}
            dependency = fixture_wheel(self.inputs, "packaging", distribution.version,
                                       extra_members=package_members)
            members.update({name: (SOURCE / name).read_bytes()
                            for name in executor_fixtures.PRODUCTION_MODULES})
            for name in ("published_install", "published_releases", "published_qualification", "published_download", "published_frontends"):
                path = f"cpn/rpnh/{name}.py"
                members[path] = (SOURCE / path).read_bytes()
            members.update({
                "cpn/rpnh/__init__.py": "# Explicit reduced test package.\n",
                "cpn/fixture_entry.py": "def main():\n    print('bootstrap fixture only')\n    return 0\n",
                f"rpnh_harness-{self.version}.dist-info/entry_points.txt":
                    "[console_scripts]\nrpnh = cpn.fixture_entry:main\n",
            })
            if failure == "prepare":
                members["cpn/__init__.py"] = "__version__ = '0.3.0'  # Deliberately inconsistent fixture.\n"
            elif failure == "probe":
                # Basic has no external --version probe. Its real LaunchSpec
                # must still reject a missing installed presentation entry.
                del members[f"rpnh_harness-{self.version}.dist-info/entry_points.txt"]
            requirements = [f"packaging=={distribution.version}"]
        else:
            dependency = fixture_wheel(self.inputs, "fixture-dependency", "1.0.0")
            requirements = ["fixture-dependency>=1,<3"]
        optional = [fixture_wheel(self.inputs, "fixture-optional-" + extra, "1.0.0")
                    for extra in sorted(extras)]
        requirements += [f"{row['name']}==1.0.0; extra == '{extra}'"
                         for extra, row in zip(sorted(extras), optional)]
        wheel = fixture_wheel(self.inputs, "rpnh-harness", self.version, requirements,
                              provides_extras=tuple(sorted(extras)), extra_members=members)
        self.wheel_rows = [wheel, dependency, *optional]
        self.payloads = {}
        assets = []
        selected = []
        for identity, row in enumerate(self.wheel_rows, 701):
            path = Path(row["path"])
            payload = path.read_bytes()
            self.payloads[identity] = payload
            asset = self.asset(identity, path.name, len(payload))
            assets.append(asset)
            selected.append({"name": row["name"], "version": row["version"], "filename": path.name,
                "asset": {"id": identity, "size": len(payload), "url": asset["browser_download_url"]}})
        self.materials = {"root_extras": sorted(extras), "python_wheels": selected}
        self.source = {"kind": "published_release", "rpnh_version": self.version,
            "release": self.tag, "channel": "stable",
            "artifact": {"filename": Path(wheel["path"]).name,
                         "location": assets[0]["browser_download_url"]}}
        components = {"basic": {"profile": "builtin", "artifact": {
            "kind": "wheel", "name": "rpnh-harness", "version": self.version,
            "variant": "py3-none-any", "architecture": "any"}}}
        scope = sorted(published_qualification.CASE_TITLES)
        combination = {"id": "basic-native-fixture", "platform": deepcopy(self.platform),
            "runtimes": contract.runtime_signature(self.runtimes), "components": components,
            "scope": scope, "evidence": ["fixture-installation"], "materials": deepcopy(self.materials)}
        result = {"id": "fixture-installation", "combination": combination["id"],
            "platform": deepcopy(self.platform), "runtime_bindings": deepcopy(self.runtimes),
            "components": deepcopy(components), "operation": "install", "from_version": None,
            "to_version": self.version, "capabilities": [], "mode": "native", "status": "pass",
            "scope": list(scope), "evidence": ["fixtures/synthetic-bootstrap-not-native.json"],
            "materials": deepcopy(self.materials)}
        self.support = {"schema_version": contract.UPDATE_SUPPORT_V3_SCHEMA,
            "protocol": contract.V2_PROTOCOL, "source": deepcopy(self.source),
            "combinations": [combination], "upgrade_from": [], "rollback_to": []}
        self.validation = {"schema_version": contract.UPDATE_VALIDATION_V3_SCHEMA,
            "protocol": contract.V2_PROTOCOL, "source": deepcopy(self.source), "results": [result]}
        assets += [self.asset(801, SUPPORT_NAME, len(json.dumps(self.support).encode())),
                   self.asset(802, VALIDATION_NAME, len(json.dumps(self.validation).encode()))]
        self.release = {"id": 91, "tag_name": self.tag, "draft": False, "prerelease": False,
            "published_at": "2026-01-01T00:00:00Z",
            "url": f"https://api.github.com/repos/{updates.REPOSITORY}/releases/91",
            "html_url": f"https://github.com/{updates.REPOSITORY}/releases/tag/{self.tag}", "assets": assets}
        self.data = {updates.RELEASES_URL: [self.release],
                     self.release_directory + SUPPORT_NAME: self.support,
                     self.release_directory + VALIDATION_NAME: self.validation}
        self.json_calls = []
        self.wheel_calls = []

    def asset(self, identity, filename, size):
        return {"id": identity, "name": filename, "size": size, "state": "uploaded",
            "url": f"https://api.github.com/repos/{updates.REPOSITORY}/releases/assets/{identity}",
            "browser_download_url": self.release_directory + filename}

    def get_json(self, url):
        self.json_calls.append(url)
        if url not in self.data:
            raise AssertionError(f"unexpected metadata coordinate: {url}")
        return deepcopy(self.data[url])

    def open_wheel(self, asset):
        self.wheel_calls.append(deepcopy(asset))
        if asset["id"] not in self.payloads:
            raise AssertionError("unexpected asset identity")
        return WheelResponse(self.payloads[asset["id"]])

    def inspect(self, **changes):
        from cpn.rpnh import published_install
        options = {"release": self.tag, "channel": "stable", "platform": deepcopy(self.platform),
                   "runtime_bindings": deepcopy(self.runtimes)}
        options.update(changes)
        return published_install.inspect_bootstrap(**options)


class BootstrapTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-bootstrap-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "managed"
        self.fixture_count = 0
        self.fixture = self.new_fixture()

    def new_fixture(self, **options):
        self.fixture_count += 1
        path = self.base / f"fixture-{self.fixture_count}"
        path.mkdir(mode=0o700)
        return BootstrapFixture(path, **options)

    @contextmanager
    def transports(self, fixture=None, *, get_json=None, open_wheel=None):
        fixture = fixture or self.fixture
        with mock.patch.object(bootstrap, "get_json", side_effect=get_json or fixture.get_json), \
                mock.patch.object(bootstrap, "_open_wheel", side_effect=open_wheel or fixture.open_wheel):
            yield

    def snapshot(self):
        return {str(path.relative_to(self.base)): path.read_bytes()
                for path in self.base.rglob("*") if path.is_file()}


class BootstrapMetadataTests(BootstrapTestCase):
    def test_inspection_is_readonly_and_has_no_fabricated_current(self):
        before = self.snapshot()
        with self.transports():
            request = self.fixture.inspect()
        document = request.to_document()
        self.assertEqual(document["schema_version"], "rpnh/published_bootstrap/v1")
        self.assertEqual(document["operation"], "bootstrap")
        self.assertEqual(document["source"], self.fixture.source)
        self.assertEqual(document["runtime_bindings"], self.fixture.runtimes)
        self.assertEqual(document["combination"], "basic-native-fixture")
        self.assertEqual(document["materials"], contract.validate_material_selection(
            self.fixture.materials, source=self.fixture.source))
        self.assertEqual(document["qualification_gaps"]["context"], "bootstrap")
        self.assertFalse(document["qualification_gaps"]["qualification_granted"])
        self.assertNotIn("current", document)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.fixture.wheel_calls, [])
        self.assertEqual(self.fixture.json_calls, [updates.RELEASES_URL,
            self.fixture.release_directory + SUPPORT_NAME,
            self.fixture.release_directory + VALIDATION_NAME])
        self.assertEqual(self.snapshot(), before)

    def test_review_document_is_an_independent_copy_not_mutable_authority(self):
        with self.transports():
            request = self.fixture.inspect()
            original = request.to_document()
            edited = request.to_document()
            edited["materials"]["python_wheels"][0]["asset"]["id"] += 90
            self.assertEqual(request.to_document(), original)
            edited["qualification_gaps"]["qualification_granted"] = True
            forged = bootstrap.BootstrapRequest(json.dumps(edited))
            with self.assertRaises(bootstrap.PublishedBootstrapError):
                bootstrap.bootstrap_published(self.root, forged)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.fixture.wheel_calls, [])

    def test_extras_incomplete_or_non_native_evidence_never_reaches_acquisition(self):
        changes = [
            lambda f: f.validation["results"][0].update(mode="fixture"),
            lambda f: f.validation["results"][0].update(status="fail"),
            lambda f: f.validation["results"][0].update(status="blocked"),
            lambda f: f.validation["results"][0].update(scope=["T02", "T20"]),
            lambda f: f.support["combinations"][0].update(evidence=["missing-result"]),
        ]
        for index, change in enumerate(changes):
            fixture = self.new_fixture(extras=("mcp",))
            change(fixture)
            with self.subTest(case=index), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect(root_extras=["mcp"])
            self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_basic_non_native_history_is_not_installation_qualification(self):
        changes = [
            lambda f: f.validation["results"][0].update(mode="fixture"),
            lambda f: f.validation["results"][0].update(status="fail"),
            lambda f: f.validation["results"][0].update(status="blocked"),
            lambda f: f.validation["results"][0].update(scope=["T02", "T20"]),
        ]
        for index, change in enumerate(changes):
            fixture = self.new_fixture()
            change(fixture)
            with self.subTest(case=index), self.transports(fixture):
                document = fixture.inspect().to_document()
                self.assertFalse(document["qualification_gaps"]["qualification_granted"])
            self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_other_frontends_and_implicit_extras_are_rejected(self):
        for kind in ("frontend", "extras"):
            fixture = self.new_fixture()
            rows = (fixture.support["combinations"][0], fixture.validation["results"][0])
            for row in rows:
                if kind == "extras":
                    row["materials"]["root_extras"] = ["mcp"]
                else:
                    row["components"]["codex"] = {"profile": "pinned-0.163.0", "artifact": {
                        "kind": "native", "name": "@openai/codex", "version": "0.163.0",
                        "variant": "linux-x64", "architecture": "x86_64"}}
            self.assertEqual(len(contract.assess_update_support(
                fixture.support, fixture.validation)["evidence_backed_combinations"]), 1)
            with self.subTest(kind=kind), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect()
            self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_every_declared_dependency_must_match_one_actual_official_asset(self):
        changes = [lambda f: f.release["assets"].pop(1),
                   lambda f: f.release["assets"][1].update(size=f.release["assets"][1]["size"] + 1),
                   lambda f: f.release["assets"][1].update(browser_download_url="https://example.invalid/dependency.whl"),
                   lambda f: f.release["assets"].append(deepcopy(f.release["assets"][1]))]
        for index, change in enumerate(changes):
            fixture = self.new_fixture()
            change(fixture)
            with self.subTest(case=index), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect()
            self.assertEqual(fixture.wheel_calls, [])

    def test_structurally_valid_other_origin_is_not_official(self):
        fixture = self.fixture
        for sidecar in (fixture.support, fixture.validation):
            sidecar["source"]["artifact"]["location"] = sidecar["source"]["artifact"]["location"].replace(
                "github.com", "example.invalid")
            rows = sidecar.get("combinations", sidecar.get("results"))
            for row in rows:
                for wheel in row["materials"]["python_wheels"]:
                    wheel["asset"]["url"] = wheel["asset"]["url"].replace("github.com", "example.invalid")
        self.assertTrue(contract.assess_update_support(fixture.support, fixture.validation)["evidence_backed_combinations"])
        with self.transports(), self.assertRaises(bootstrap.PublishedBootstrapError):
            fixture.inspect()
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_v2_metadata_cannot_authorize_new_v3_bootstrap(self):
        fixture = self.fixture
        fixture.support["schema_version"] = contract.UPDATE_SUPPORT_SCHEMA
        fixture.validation["schema_version"] = contract.UPDATE_VALIDATION_SCHEMA
        del fixture.support["combinations"][0]["materials"]
        del fixture.validation["results"][0]["materials"]
        for asset in fixture.release["assets"]:
            if asset["name"] in (SUPPORT_NAME, VALIDATION_NAME):
                old_url = asset["browser_download_url"]
                asset["name"] = asset["name"].replace(".v3.", ".v2.")
                asset["browser_download_url"] = fixture.release_directory + asset["name"]
                fixture.data[asset["browser_download_url"]] = fixture.data.pop(old_url)
        with self.transports(), self.assertRaises(bootstrap.PublishedBootstrapError):
            fixture.inspect()
        self.assertEqual(fixture.wheel_calls, [])

    def test_ambiguous_combination_requires_an_explicit_selection(self):
        fixture = self.fixture
        other = deepcopy(fixture.support["combinations"][0])
        other.update(id="another-combination", evidence=["another-result"])
        result = deepcopy(fixture.validation["results"][0])
        result.update(id="another-result", combination=other["id"])
        fixture.support["combinations"].append(other)
        fixture.validation["results"].append(result)
        with self.transports():
            with self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect()
            request = fixture.inspect(combination=other["id"])
        self.assertEqual(request.to_document()["combination"], other["id"])
        self.assertEqual(fixture.wheel_calls, [])

    def test_basic_inspection_with_unknown_emulation_does_not_grant_native_qualification(self):
        platform = dict(self.fixture.platform, emulated=None)
        with self.transports():
            document = self.fixture.inspect(platform=platform).to_document()
        self.assertIsNone(document["platform"]["emulated"])
        self.assertFalse(document["qualification_gaps"]["qualification_granted"])
        self.assertEqual(self.fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_basic_inspection_rejects_emulation_and_additional_runtimes_before_metadata(self):
        platform = dict(self.fixture.platform, emulated=True)
        with self.transports(), self.assertRaises(bootstrap.PublishedBootstrapError):
            self.fixture.inspect(platform=platform)
        runtimes = deepcopy(self.fixture.runtimes)
        runtimes["node"] = dict(runtimes["python"], implementation="node")
        with self.transports(), self.assertRaises(bootstrap.PublishedBootstrapError):
            self.fixture.inspect(runtime_bindings=runtimes)
        self.assertEqual(self.fixture.json_calls, [])

    def test_execution_reobserves_host_and_python_before_download_or_root(self):
        for target in ("host", "python"):
            fixture = self.new_fixture()
            if target == "host":
                fixture.platform["os_release"] = "different-kernel-fixture"
                for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
                    row["platform"] = deepcopy(fixture.platform)
            else:
                fixture.runtimes["python"]["path"] = str(self.base / "missing-python")
            with self.subTest(target=target), self.transports(fixture):
                request = fixture.inspect()
                with self.assertRaises((OSError, ValueError, RuntimeError)):
                    bootstrap.bootstrap_published(self.root, request)
            self.assertEqual(fixture.wheel_calls, [])
            self.assertFalse(self.root.exists())

    def test_changed_release_identity_rejects_before_acquisition(self):
        with self.transports():
            request = self.fixture.inspect()
            self.fixture.release["id"] = 92
            self.fixture.release["url"] = f"https://api.github.com/repos/{updates.REPOSITORY}/releases/92"
            with self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "changed after confirmation"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(self.fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_changed_exact_dependency_identity_rejects_before_acquisition(self):
        fixture = self.fixture
        with self.transports():
            request = fixture.inspect()
            fixture.release["assets"][1]["id"] = 703
            fixture.release["assets"][1]["url"] = f"https://api.github.com/repos/{updates.REPOSITORY}/releases/assets/703"
            for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
                next(w for w in row["materials"]["python_wheels"] if w["name"] == "fixture-dependency")["asset"]["id"] = 703
            with self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "changed after confirmation"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_changed_sidecar_content_is_rechecked_even_with_unchanged_asset_ids(self):
        with self.transports():
            request = self.fixture.inspect()
            # T03 is not required for a fresh bootstrap; this remains a legal
            # sufficient declaration, but is no longer the confirmed document.
            self.fixture.support["combinations"][0]["scope"].remove("T03")
            with self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "changed after confirmation"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(self.fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_metadata_changes_after_download_are_rejected_before_root_creation(self):
        fixture = self.fixture
        def transfer(asset):
            response = fixture.open_wheel(asset)
            fixture.release["id"] = 92
            fixture.release["url"] = f"https://api.github.com/repos/{updates.REPOSITORY}/releases/92"
            return response
        with self.transports(open_wheel=transfer):
            request = fixture.inspect()
            with self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "changed after confirmation"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 2)
        self.assertFalse(self.root.exists())

    def test_metadata_is_refreshed_under_ex_before_a_stage_is_created(self):
        import fcntl
        fixture = self.fixture
        held_ex = []
        def metadata_reader(url):
            count = fixture.json_calls.count(updates.RELEASES_URL) + 1
            if url == updates.RELEASES_URL and count == 4:
                with (self.root / "lifecycle.lock").open("rb") as stream:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                held_ex.append(True)
                fixture.release["id"] = 92
                fixture.release["url"] = f"https://api.github.com/repos/{updates.REPOSITORY}/releases/92"
            return fixture.get_json(url)
        with self.transports(get_json=metadata_reader):
            request = fixture.inspect()
            with self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "changed after confirmation"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(held_ex, [True])
        self.assertEqual(list((self.root / "versions").iterdir()), [])
        self.assertIsNone(install_v2.read_target(self.root, allow_missing=True))

    def test_existing_or_aliased_root_is_never_adopted(self):
        with self.transports():
            request = self.fixture.inspect()
            self.root.mkdir(mode=0o700)
            sentinel = self.root / "keep.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            with self.assertRaises(bootstrap.PublishedBootstrapError):
                bootstrap.bootstrap_published(self.root, request)
            alias = self.base / "alias"
            alias.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(bootstrap.PublishedBootstrapError):
                bootstrap.bootstrap_published(alias / "child", request)
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(list(self.root.iterdir()), [sentinel])
        self.assertEqual(self.fixture.wheel_calls, [])

    def test_truncated_or_encoded_download_cannot_create_root(self):
        for kind in ("truncated", "encoded"):
            fixture = self.new_fixture()
            def transfer(asset):
                response = fixture.open_wheel(asset)
                if kind == "truncated":
                    response = WheelResponse(response.getvalue()[:-1])
                else:
                    response.headers["Content-Encoding"] = "gzip"
                return response
            with self.subTest(kind=kind), self.transports(fixture, open_wheel=transfer):
                request = fixture.inspect()
                with self.assertRaises(bootstrap.PublishedBootstrapError):
                    bootstrap.bootstrap_published(self.root, request)
            self.assertFalse(self.root.exists())

    def test_download_receipt_or_custom_verifier_cannot_replace_acquired_context(self):
        fixture = self.fixture
        catalog = contract.load_profile_catalog(wheel=Path(fixture.wheel_rows[0]["path"]))
        plan = contract.build_install_plan(fixture.source, fixture.platform, fixture.runtimes, catalog=catalog)
        document = {"schema_version": install_materials.PUBLISHED_MATERIALS_SCHEMA,
            "platform": fixture.platform, "runtime_bindings": fixture.runtimes,
            "root_extras": [], "python_wheels": fixture.wheel_rows, "frontends": {},
            "published_selection": contract.validate_material_selection(fixture.materials, source=fixture.source)}
        path = self.base / "materials.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        receipt = {"schema_version": "rpnh/published_artifact_observation/v1",
                   "source": fixture.source, "qualification": {"official_payload_observed": True}}
        class PretendAcquisition:
            def verify(self, *_args):
                raise AssertionError("a caller verifier must never be invoked")
        for context in (receipt, PretendAcquisition()):
            with self.subTest(context=type(context).__name__), \
                    self.assertRaisesRegex(bootstrap.PublishedBootstrapError, "current official asset acquisition"):
                install_executor.execute_install(self.root, plan, path, published_context=context)
        with self.assertRaises(bootstrap.PublishedBootstrapError):
            bootstrap.bootstrap_published(self.root, receipt)
        self.assertFalse(self.root.exists())

    def test_declared_asset_identity_does_not_replace_actual_wheel_metadata(self):
        fixture = self.fixture
        other = fixture_wheel(fixture.inputs, "fixture-dependency", "2.0.0")
        data = Path(other["path"]).read_bytes()
        fixture.payloads[702] = data
        fixture.release["assets"][1]["size"] = len(data)
        for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
            dependency = next(w for w in row["materials"]["python_wheels"] if w["name"] == "fixture-dependency")
            dependency["asset"]["size"] = len(data)
        with self.transports():
            request = fixture.inspect()
            with self.assertRaisesRegex(ValueError, "metadata|METADATA|identity|version"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 2)
        self.assertFalse(self.root.exists())

    def test_backed_declaration_does_not_replace_requires_dist_closure(self):
        fixture = self.fixture
        members = {name: (SOURCE / name).read_bytes() for name in executor_fixtures.CATALOG_FILES}
        row = fixture_wheel(fixture.inputs, "rpnh-harness", fixture.version,
            ["fixture-dependency>=1,<3", "undeclared-dependency==4.0.0"], extra_members=members)
        data = Path(row["path"]).read_bytes()
        fixture.payloads[701] = data
        fixture.release["assets"][0]["size"] = len(data)
        for row in (fixture.support["combinations"][0], fixture.validation["results"][0]):
            package = next(w for w in row["materials"]["python_wheels"] if w["name"] == "rpnh-harness")
            package["asset"]["size"] = len(data)
        with self.transports():
            request = fixture.inspect()
            with self.assertRaisesRegex(ValueError, "closure"):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 2)
        self.assertFalse(self.root.exists())


class BootstrapExecutionTests(BootstrapTestCase):
    """Only these methods run reduced fixture venvs and real candidate children."""
    def test_success_preserves_published_url_and_retained_probe_needs_no_downloads(self):
        fixture = self.new_fixture(runnable=True)
        with self.transports(fixture):
            request = fixture.inspect()
            result = self.execute(request)
        target, directory, installed = install_v2.read_target(self.root)
        self.assertEqual(result.activation.current, target)
        self.assertTrue(result.activation.committed)
        self.assertIsNone(result.activation.previous)
        self.assertEqual(installed["source"], fixture.source)
        self.assertEqual(set(installed["components"]), {"basic"})
        self.assertEqual(result.to_document()["source_qualification"], "official_release_assets_observed")
        self.assertEqual(result.to_document()["native_qualification"], "not_awarded")
        self.assertFalse(result.to_document()["tasks_resumed"])
        material_path = directory / "materials.json"
        document = json.loads(material_path.read_text())
        for wheel in document["python_wheels"]:
            self.assertFalse(Path(wheel["path"]).exists())
        self.assertEqual(document["published_selection"], request.to_document()["materials"])
        shutil.rmtree(fixture.inputs)
        # A host package at another R must not resolve this I. Recheck from
        # this actual installed Python while preserving the original EX. The
        # current pointer is untouched; this is a material health probe, not a
        # fabricated previous installation or a rollback transaction.
        script = """
import json, os, sys
from pathlib import Path
from cpn.rpnh.install_candidate import inherited_exclusive_lease
from cpn.rpnh.install_contract import read_installed, read_plan
from cpn.rpnh.install_materials import load_materials, probe_installation
from cpn.rpnh.install_v2 import check_root
directory = Path(sys.argv[1])
root = check_root(directory.parent.parent)
lease = inherited_exclusive_lease(root, int(sys.argv[2]))
try:
    materials = load_materials(directory / 'materials.json', read_plan(directory), installed=True)
    print(json.dumps(probe_installation(directory, read_installed(directory), materials, retained=True)))
finally:
    os.close(lease)
"""
        with install_v2.management_lease(self.root) as lease:
            process = subprocess.run([str(directory / "venv/bin/python"), "-I", "-B", "-c", script,
                str(directory), str(lease.fileno())], cwd=self.base, pass_fds=(lease.fileno(),),
                env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        observed = json.loads(process.stdout)
        self.assertEqual(observed["python"]["root_extras"], [])
        self.assertEqual(install_v2.read_target(self.root)[0], target)
        self.assertEqual(len(fixture.wheel_calls), 2)
        self.assertEqual(fixture.json_calls.count(updates.RELEASES_URL), 4)

    def test_prepare_failure_retains_a_stage_without_complete_or_current(self):
        self._failed_candidate("prepare")

    def test_probe_failure_retains_prepared_stage_without_complete_or_current(self):
        self._failed_candidate("probe")

    def _failed_candidate(self, operation):
        fixture = self.new_fixture(runnable=True, failure=operation)
        with self.transports(fixture):
            request = fixture.inspect()
            with self.assertRaisesRegex(RuntimeError, "candidate " + operation + " failed"):
                self.execute(request)
        self.assertIsNone(install_v2.read_target(self.root, allow_missing=True))
        stages = list((self.root / "versions").iterdir())
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertTrue((stage / "plan.json").is_file())
        self.assertFalse((stage / "installed.json").exists())
        self.assertEqual((stage / "prepared.json").exists(), operation == "probe")
        self.assertTrue((stage / f"candidate-{operation}.err").is_file())

    def execute(self, request):
        try:
            return bootstrap.bootstrap_published(self.root, request)
        except Exception:
            # Capture actual child diagnostics before the test's own temporary
            # directory is removed, including an unexpected earlier-stage error.
            for path in sorted((self.root / "versions").glob("*/candidate-*.err")):
                print(path.name + ":\n" + path.read_text(encoding="utf-8"), file=sys.stderr)
            raise


if __name__ == "__main__":
    unittest.main()
