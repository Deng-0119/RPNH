"""Offline v2 software contract tests; fixtures confer no release qualification.

The small document helpers are also used by the adjacent transaction/context
tests. Their nonexistent runtime/artifact paths deliberately distinguish
structural completion from an executable, dependency-verified installation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from cpn.rpnh import install_contract as contract


from cpn import __version__ as VERSION
IDENTITY = "i-" + "1" * 32
SECOND_IDENTITY = "i-" + "2" * 32


def source_document(version=VERSION, *, published=False, channel="prerelease"):
    filename = f"rpnh_harness-{version}-py3-none-any.whl"
    return {"kind": "published_release" if published else "local_candidate", "rpnh_version": version,
            "release": "v" + version if published else None, "channel": channel if published else None,
            "artifact": {"filename": filename,
                         "location": f"https://example.invalid/releases/v{version}/{filename}" if published
                         else "/fixtures/" + filename}}


def platform_document():
    return {"os": "linux", "architecture": "x86_64", "os_release": "fixture",
            "libc": None, "emulated": False}


def runtime_bindings(*, all_tools=False):
    result = {"python": {"path": "/missing/python", "version": "3.12.14",
                         "implementation": "cpython", "architecture": "x86_64"}}
    if all_tools:
        for name, version in (("node", "24.22.1"), ("npm", "12.0.0"), ("pnpm", "11.28.5")):
            result[name] = {"path": "/missing/" + name, "version": version,
                            "implementation": name, "architecture": "x86_64"}
    return result


def installed_document(identity=IDENTITY, version=VERSION):
    return {"schema_version": contract.INSTALLED_SCHEMA, "protocol": contract.V2_PROTOCOL,
            "installation_id": identity, "rpnh_version": version, "complete": True,
            "source": source_document(version), "platform": platform_document(),
            "runtime_bindings": runtime_bindings(), "components": {
                "basic": {"profile": "builtin", "binding": {"kind": "managed"},
                          "launch": {"kind": "builtin", "scope": "installation", "path": "venv/bin/rpnh"},
                          "artifact": {"kind": "wheel", "name": "rpnh-harness", "version": version,
                                       "variant": "py3-none-any", "architecture": "any"}}}}


def with_component(document, entry, profile, *, external=None):
    value = deepcopy(document)
    specification = contract.get_profile(entry, profile)
    dsh = entry == "dsh-web"
    binding = {"kind": "external", "path": external} if external else {"kind": "managed"}
    path = external or f"components/{entry}/" + ("source" if dsh else "client")
    value["components"][entry] = {
        "profile": profile, "binding": binding,
        "launch": {"kind": "dsh_prepared" if dsh else "native",
                   "scope": "external" if external else "installation", "path": path},
        "artifact": {"kind": "dsh_source" if dsh else "native", "name": entry,
                     "version": specification.version, "variant": "fixture", "architecture": "x86_64"}}
    value["runtime_bindings"] = runtime_bindings(all_tools=True)
    return value


def qualification_documents():
    """Synthetic metadata only: the word native here is input to a parser test."""
    installed = installed_document()
    components = {entry: {key: value[key] for key in ("profile", "artifact")}
                  for entry, value in installed["components"].items()}
    support = {"schema_version": contract.UPDATE_SUPPORT_SCHEMA, "protocol": contract.V2_PROTOCOL,
               "source": source_document(), "combinations": [{
                   "id": "basic-fixture", "platform": platform_document(),
                   "runtimes": contract.runtime_signature(runtime_bindings()), "components": components,
                   "scope": ["installation", "startup"], "evidence": ["install-fixture"]}],
               "upgrade_from": [], "rollback_to": []}
    validation = {"schema_version": contract.UPDATE_VALIDATION_SCHEMA, "protocol": contract.V2_PROTOCOL,
                  "source": source_document(), "results": [{
                      "id": "install-fixture", "combination": "basic-fixture", "platform": platform_document(),
                      "runtime_bindings": runtime_bindings(), "components": deepcopy(components),
                      "operation": "install", "from_version": None, "to_version": VERSION, "capabilities": [],
                      "mode": "native", "status": "pass", "scope": ["installation", "startup"],
                      "evidence": ["fixtures/not-a-product-validation.log"]}]}
    return support, validation


class InstalledContractTests(unittest.TestCase):
    def test_completed_record_is_structural_not_a_runtime_probe(self):
        document = installed_document()
        with mock.patch.object(Path, "is_file", side_effect=AssertionError("health probe")), \
                mock.patch.object(os, "access", side_effect=AssertionError("health probe")):
            self.assertEqual(contract.validate_installed(document), document)
        self.assertEqual(document["runtime_bindings"]["python"]["path"], "/missing/python")

    def test_v1_unknown_fields_incomplete_and_identity_mismatch_are_rejected(self):
        for change in ({"protocol": "managed-side-by-side-v1"}, {"schema_version": "rpnh/installed/v1"},
                       {"complete": False}, {"complete": 1}, {"unexpected": True},
                       {"installation_id": "i-" + "A" * 32}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                contract.validate_installed({**installed_document(), **change})
        with self.assertRaisesRegex(ValueError, "identity"):
            contract.validate_installed(installed_document(), expected_id=SECOND_IDENTITY)

    def test_read_installed_checks_directory_identity_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / IDENTITY
            directory.mkdir()
            path = directory / "installed.json"
            path.write_text(json.dumps(installed_document()), encoding="utf-8")
            self.assertEqual(contract.read_installed(directory), installed_document())
            path.unlink()
            other = root / "elsewhere.json"
            other.write_text(json.dumps(installed_document()), encoding="utf-8")
            path.symlink_to(other)
            with self.assertRaisesRegex(ValueError, "regular file"):
                contract.read_installed(directory)

    def test_basic_must_exist_and_identify_the_same_release(self):
        document = installed_document()
        document["components"] = {}
        with self.assertRaises(ValueError):
            contract.validate_installed(document)
        document = installed_document()
        document["components"]["basic"]["artifact"]["version"] = "9.0.0"
        with self.assertRaisesRegex(ValueError, "same RPNH"):
            contract.validate_installed(document)

    def test_launch_cannot_escape_its_installation_or_change_external_binding(self):
        for path in ("../other/client", "/outside/client", "components/../other", "./client"):
            document = with_component(installed_document(), "codex", "pinned-0.163.0")
            document["components"]["codex"]["launch"]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                contract.validate_installed(document)
        document = with_component(installed_document(), "codex", "pinned-0.163.0", external="/external/codex")
        document["components"]["codex"]["launch"]["path"] = "/external/another-codex"
        with self.assertRaisesRegex(ValueError, "exact external binding"):
            contract.validate_installed(document)

    def test_dsh_source_input_does_not_become_its_launch_directory(self):
        document = with_component(installed_document(), "dsh-web", "0.2.2-rc.1")
        document["components"]["dsh-web"]["binding"] = {"kind": "source_import", "path": "/input/dsh"}
        self.assertEqual(contract.validate_installed(document)["components"]["dsh-web"]["launch"]["path"],
                         "components/dsh-web/source")
        document["components"]["dsh-web"]["launch"] = {
            "kind": "dsh_prepared", "scope": "external", "path": "/input/dsh"}
        with self.assertRaises(ValueError):
            contract.validate_installed(document)

    def test_runtime_versions_are_exact_and_node_backed_entry_requires_node(self):
        document = installed_document()
        document["runtime_bindings"]["python"]["version"] = ">=3.11"
        with self.assertRaisesRegex(ValueError, "complete version"):
            contract.validate_installed(document)
        document = with_component(installed_document(), "dsh-web", "0.2.2-alpha.2")
        del document["runtime_bindings"]["node"]
        with self.assertRaisesRegex(ValueError, "Node observation"):
            contract.validate_installed(document)

    def test_source_origin_does_not_claim_official_release_eligibility(self):
        source = source_document(published=True)
        # A structurally valid nonofficial URL is not authorized by this parser.
        self.assertEqual(contract.validate_source(source), source)
        with self.assertRaises(ValueError):
            contract.validate_source({**source_document(), "release": "v" + VERSION})
        with self.assertRaises(ValueError):
            contract.validate_source(source_document("1.0.0.dev1", published=True))

    def test_frontend_view_is_only_identity_and_selection(self):
        view = {"schema_version": contract.FRONTEND_SCHEMA, "installation_id": IDENTITY, "frontend": "dsh"}
        self.assertEqual(contract.validate_frontend_view(view, expected_id=IDENTITY,
                                                       selected_entries={"basic", "dsh-web"}), view)
        with self.assertRaisesRegex(ValueError, "reconfiguration"):
            contract.validate_frontend_view(view, selected_entries={"basic"})
        with self.assertRaises(ValueError):
            contract.validate_frontend_view({**view, "dsh_source": "/not-a-binding-authority"})

    def test_json_enum_containers_raise_stable_validation_errors(self):
        view = {"schema_version": contract.FRONTEND_SCHEMA, "installation_id": IDENTITY, "frontend": "basic"}
        for malformed in ([], {}, None, True):
            with self.subTest(value=malformed):
                with self.assertRaises(ValueError):
                    contract.validate_frontend_view({**view, "frontend": malformed})
                with self.assertRaises(ValueError):
                    contract.validate_platform({**platform_document(), "os": malformed})

    def test_shared_frontend_reader_rejects_duplicate_keys_and_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / IDENTITY
            directory.mkdir()
            path = directory / "frontend.json"
            view = {"schema_version": contract.FRONTEND_SCHEMA, "installation_id": IDENTITY, "frontend": "basic"}
            path.write_text(json.dumps(view), encoding="utf-8")
            self.assertEqual(contract.read_frontend_view(directory, selected_entries={"basic"}), view)
            path.write_text(json.dumps(view)[:-1] + ',"frontend":"codex"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                contract.read_frontend_view(directory)
            path.write_text('{"unused":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                contract.read_software_record(path)


class ActivationContractTests(unittest.TestCase):
    def test_bootstrap_upgrade_and_rollback_intents_have_one_commit_meaning(self):
        before, after = map(contract.installation_target, (IDENTITY, SECOND_IDENTITY))
        cases = [
            {"operation": "bootstrap", "from": None, "to": after, "old_previous": None, "previous_after": None},
            {"operation": "upgrade", "from": before, "to": after, "old_previous": None, "previous_after": before},
            {"operation": "reconfigure", "from": before, "to": after, "old_previous": before, "previous_after": before},
            {"operation": "rollback", "from": before, "to": after, "old_previous": after, "previous_after": after},
        ]
        for intent in cases:
            with self.subTest(operation=intent["operation"]):
                self.assertEqual(contract.validate_pending_activation(intent), intent)
                marker = {"schema_version": contract.MANAGED_SCHEMA, "protocol": contract.V2_PROTOCOL,
                          "pending_activation": intent}
                self.assertEqual(contract.validate_managed(marker), marker)

    def test_pending_does_not_allow_unknown_targets_or_rollback_toggle(self):
        before, after = map(contract.installation_target, (IDENTITY, SECOND_IDENTITY))
        intent = {"operation": "rollback", "from": before, "to": after,
                  "old_previous": after, "previous_after": after}
        for change in ({"previous_after": before}, {"from": None}, {"to": before},
                       {"old_previous": None}, {"to": "versions/../../user"}, {"operation": "guess-latest"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                contract.validate_pending_activation({**intent, **change})
        with self.assertRaises(ValueError):
            contract.validate_managed({"protocol": "managed-side-by-side-v1"})


class ProfileAndPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = contract.load_profile_catalog()

    def _plan(self, *, requested=None, current=None, source=None, runtimes=None, catalog=None):
        return contract.build_install_plan(
            source or source_document(), platform_document(),
            runtime_bindings(all_tools=True) if runtimes is None else runtimes,
            requested=requested, current=current, catalog=catalog or self.catalog,
            installation_id=SECOND_IDENTITY)

    def test_catalog_consumes_the_original_default_candidate_and_history(self):
        self.assertEqual(self.catalog.defaults, {
            "basic": "builtin", "codex": "pinned-0.163.0", "opencode": "1.19.4", "dsh-web": "0.2.2-alpha.2"})
        self.assertEqual(contract.get_profile("opencode", "2.2.6", catalog=self.catalog).coordinate["package"], "@opencode/cli")
        self.assertIn("candidate-0.166.0", self.catalog.profiles["codex"])
        self.assertIn("2.2.5", self.catalog.profiles["opencode"])
        with self.assertRaises(ValueError):
            contract.get_profile("opencode", "2.2.5", catalog=self.catalog)
        self.assertEqual(len(contract.get_profile("dsh-web", catalog=self.catalog).web_packages), 32)
        self.assertEqual(len(contract.get_profile("dsh-web", "0.2.2-rc.1", catalog=self.catalog).web_packages), 33)

    def test_no_request_preserves_all_selected_profiles_and_bindings(self):
        current = with_component(installed_document(), "codex", "candidate-0.167.0", external="/owner/codex")
        current = with_component(current, "opencode", "2.2.6")
        current = with_component(current, "dsh-web", "0.2.2-rc.1")
        before = deepcopy(current)
        plan = self._plan(current=current)
        self.assertTrue(plan.selection_resolved)
        self.assertEqual(set(plan.components), set(current["components"]))
        for entry, selected in current["components"].items():
            self.assertEqual(plan.components[entry], {key: selected[key] for key in ("profile", "binding")})
        self.assertEqual(plan.operation, "reconfigure")
        self.assertEqual(plan.expected_from, contract.installation_target(IDENTITY))
        self.assertEqual(current, before)

    def test_explicit_request_replaces_set_and_omitted_entry_fields_keep_its_binding(self):
        current = with_component(installed_document(), "codex", "candidate-0.167.0", external="/owner/codex")
        current = with_component(current, "opencode", "2.2.6")
        plan = self._plan(current=current, requested={"codex": {}})
        self.assertEqual(set(plan.components), {"basic", "codex"})
        self.assertEqual(plan.components["codex"], {
            "profile": "candidate-0.167.0", "binding": {"kind": "external", "path": "/owner/codex"}})
        self.assertEqual(set(self._plan(current=current, requested={}).components), {"basic"})

    def test_any_unavailable_profile_remains_selected_and_blocks_the_whole_plan(self):
        plan = self._plan(requested={"codex": {"profile": "candidate-0.167.0"},
                                     "opencode": {"profile": "2.2.5"}, "dsh-web": {"profile": "99.0.0"}})
        self.assertFalse(plan.selection_resolved)
        self.assertEqual(plan.components["opencode"]["profile"], "2.2.5")
        self.assertEqual(plan.components["dsh-web"]["profile"], "99.0.0")
        self.assertEqual({row["entry"] for row in plan.conflicts}, {"opencode", "dsh-web"})

    def test_target_catalog_and_channel_cannot_silently_reuse_other_defaults(self):
        plan = self._plan(source=source_document("1.0.0"))
        self.assertIn("target_catalog_mismatch", {row["code"] for row in plan.conflicts})
        catalog = replace(self.catalog, rpnh_version="1.0.0")
        plan = self._plan(source=source_document("1.0.0", published=True, channel="stable"),
                          catalog=catalog, requested={"dsh-web": {}})
        self.assertIn("prerelease_profile", {row["code"] for row in plan.conflicts})

    def test_runtime_requirements_are_reported_without_executing_or_dropping_entries(self):
        with mock.patch.object(Path, "mkdir", side_effect=AssertionError("write")):
            plan = self._plan(requested={"codex": {}, "opencode": {}, "dsh-web": {}}, runtimes={})
        self.assertEqual(set(plan.components), set(contract.PRIMARY_ENTRIES))
        self.assertFalse(plan.selection_resolved)
        self.assertTrue(all(row["code"] == "missing_runtime" for row in plan.conflicts))
        self.assertNotIn("supported", plan.to_document())
        self.assertNotIn("eligible", plan.to_document())

    def test_plan_json_roundtrip_preserves_identity_and_cannot_select_an_operation(self):
        plan = self._plan(requested={"dsh-web": {"profile": "0.2.2-rc.1"}})
        document = plan.to_document()
        self.assertEqual(contract.validate_plan(document), document)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / SECOND_IDENTITY
            directory.mkdir()
            path = directory / "plan.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(contract.read_plan(directory), document)
            path.write_text(json.dumps({**document, "installation_id": IDENTITY}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another installation"):
                contract.read_plan(directory)
        with self.assertRaises(ValueError):
            contract.validate_plan({**document, "operation": "run-task"})
        for malformed in ([], {}, True):
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                contract.validate_plan({**document, "conflicts": [
                    {"entry": malformed, "code": "invalid", "detail": "fixture"}]})

    def test_candidate_wheel_catalog_reads_target_payload_without_installing_it(self):
        root = Path(__file__).resolve().parents[1]
        names = ["cpn/frontend/codex_compatibility.v1.json", "cpn/frontend/opencode_compatibility.v1.json",
                 "integrations/dsh/UPSTREAM.json", "integrations/dsh/UPSTREAM_CANDIDATE.json",
                 "integrations/dsh/frontend/web-packages.json", "integrations/dsh/frontend/web-packages.rc1.json"]
        with tempfile.TemporaryDirectory() as raw:
            wheel = Path(raw) / "metadata-fixture.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("rpnh_harness-2.0.0.dist-info/METADATA", "Name: rpnh-harness\nVersion: 2.0.0\n")
                for name in names:
                    archive.write(root / name, name)
            catalog = contract.load_profile_catalog(wheel=wheel)
            self.assertEqual(catalog.rpnh_version, "2.0.0")
            self.assertTrue(self._plan(source=source_document("2.0.0"), catalog=catalog).selection_resolved)
            self.assertNotIn("0.2.2-rc.5", catalog.profiles["dsh-web"])
            rc5_pin = "integrations/dsh/UPSTREAM_RC5_CANDIDATE.json"
            with ZipFile(wheel, "a") as archive:
                archive.write(root / rc5_pin, rc5_pin)
            with self.assertRaises(ValueError):
                contract.load_profile_catalog(wheel=wheel)
            rc5_web = "integrations/dsh/frontend/web-packages.rc5.json"
            with ZipFile(wheel, "a") as archive:
                archive.write(root / rc5_web, rc5_web)
            catalog = contract.load_profile_catalog(wheel=wheel)
            self.assertEqual(catalog.defaults["dsh-web"], "0.2.2-alpha.2")
            self.assertEqual(contract.get_profile("dsh-web", "0.2.2-rc.5", catalog=catalog).web_packages,
                             tuple(json.loads((root / rc5_web).read_text())["packages"]))

    def test_duplicate_record_fields_are_not_last_writer_wins(self):
        plan = self._plan()
        text = json.dumps(plan.to_document())
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / SECOND_IDENTITY
            directory.mkdir()
            (directory / "plan.json").write_text(text[:-1] + ',"operation":"run-task"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                contract.read_plan(directory)


class UpdateSidecarContractTests(unittest.TestCase):
    def test_evidence_match_reports_only_named_scopes_and_no_publication_authority(self):
        support, validation = qualification_documents()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("log or network read")):
            report = contract.assess_update_support(support, validation)
        self.assertEqual(report["evidence_backed_combinations"], [{
            "id": "basic-fixture", "scope": ["installation", "startup"], "evidence": ["install-fixture"]}])
        self.assertEqual(report["source"]["kind"], "local_candidate")
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["upgrade_from"], [])
        self.assertEqual(report["rollback_to"], [])
        self.assertFalse({"eligible", "installable", "supported", "official"} & set(report))
        # An arbitrary HTTPS origin remains only a declaration, even when it
        # says published_release and carries matching metadata assertions.
        support["source"] = validation["source"] = source_document(published=True)
        self.assertEqual(contract.assess_update_support(support, validation)["source"]["kind"], "published_release")

    def test_empty_first_v2_ranges_and_unvalidated_candidate_are_representable(self):
        support, validation = qualification_documents()
        support["combinations"] = []
        validation["results"] = []
        report = contract.assess_update_support(support, validation)
        self.assertEqual(report["evidence_backed_combinations"], [])
        self.assertEqual(report["upgrade_from"], [])
        self.assertEqual(report["rollback_to"], [])
        support, validation = qualification_documents()
        support["combinations"][0]["evidence"] = []
        self.assertEqual(contract.assess_update_support(support, validation)["diagnostics"][0]["code"], "missing_evidence")

    def test_fixture_failure_and_blocked_results_do_not_back_native_scope(self):
        for mode, status in (("fixture", "pass"), ("native", "fail"), ("native", "blocked")):
            support, validation = qualification_documents()
            result = validation["results"][0]
            result.update(mode=mode, status=status)
            if status == "blocked":
                result["runtime_bindings"] = {}
                result["evidence"] = []
            with self.subTest(mode=mode, status=status):
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report["evidence_backed_combinations"], [])
                self.assertEqual(report["diagnostics"][0]["code"], "not_native_pass")

    def test_platform_runtime_profile_and_artifact_must_match_independently_of_id(self):
        changes = (("platform", "os_release", "another-release"),
                   ("platform", "architecture", "arm64"),
                   ("runtime_bindings", "python", {**runtime_bindings()["python"], "version": "3.13.12"}))
        for container, key, value in changes:
            support, validation = qualification_documents()
            validation["results"][0][container][key] = value
            with self.subTest(change=(container, key)):
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report["evidence_backed_combinations"], [])
                self.assertEqual(report["diagnostics"][0]["code"], "combination_mismatch")
        support, validation = qualification_documents()
        extra = with_component(installed_document(), "codex", "pinned-0.163.0")["components"]["codex"]
        for document in (support["combinations"][0], validation["results"][0]):
            document["components"]["codex"] = {key: deepcopy(extra[key]) for key in ("profile", "artifact")}
        validation["results"][0]["components"]["codex"]["artifact"]["variant"] = "different-cpu-baseline"
        self.assertEqual(contract.assess_update_support(support, validation)["evidence_backed_combinations"], [])
        validation["results"][0]["components"]["codex"]["artifact"]["variant"] = extra["artifact"]["variant"]
        validation["results"][0]["components"]["codex"]["profile"] = "candidate-0.167.0"
        self.assertEqual(contract.assess_update_support(support, validation)["evidence_backed_combinations"], [])

    def test_scope_unknown_emulation_and_missing_reference_remain_explicit(self):
        for scenario, code in (("scope", "incomplete_scope"), ("emulation", "unknown_emulation"),
                               ("reference", "missing_evidence")):
            support, validation = qualification_documents()
            if scenario == "scope":
                validation["results"][0]["scope"] = ["startup"]
            elif scenario == "emulation":
                support["combinations"][0]["platform"]["emulated"] = None
                validation["results"][0]["platform"]["emulated"] = None
            else:
                support["combinations"][0]["evidence"] = ["missing-result"]
            with self.subTest(scenario=scenario):
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report["evidence_backed_combinations"], [])
                self.assertEqual(report["diagnostics"][0]["code"], code)

    def _with_pair(self, operation, *, other="0.1.0rc1"):
        support, validation = qualification_documents()
        kind = "upgrade_from" if operation == "upgrade" else "rollback_to"
        capabilities = ["registry-schema/read", "registry-schema/write", "task-checkpoint/resume"]
        result = deepcopy(validation["results"][0])
        result.update(id="pair-fixture", operation=operation, capabilities=capabilities, scope=["release-pair"])
        result["from_version"], result["to_version"] = ((other, VERSION) if operation == "upgrade" else (VERSION, other))
        validation["results"].append(result)
        support[kind] = [{"version": other, "capabilities": capabilities, "evidence": ["pair-fixture"]}]
        return support, validation, kind

    def test_release_pair_requires_correct_direction_and_concrete_capability_scope(self):
        for operation in ("upgrade", "rollback"):
            support, validation, kind = self._with_pair(operation)
            with self.subTest(operation=operation):
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report[kind], [{"version": "0.1.0rc1", "combination": "basic-fixture",
                    "capabilities": support[kind][0]["capabilities"], "evidence": ["pair-fixture"]}])
                validation["results"][1]["capabilities"] = ["registry-schema/read"]
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report[kind], [])
                self.assertIn("incomplete_capabilities", {row["code"] for row in report["diagnostics"]})

    def test_startup_pass_cannot_be_reused_for_upgrade_or_rollback(self):
        for operation in ("upgrade", "rollback"):
            support, validation, kind = self._with_pair(operation)
            support[kind][0]["evidence"] = ["install-fixture"]
            with self.subTest(operation=operation):
                report = contract.assess_update_support(support, validation)
                self.assertEqual(report[kind], [])
                self.assertIn("operation_mismatch", {row["code"] for row in report["diagnostics"]})

    def test_release_pair_is_not_a_version_range_or_another_tested_predecessor(self):
        support, validation, kind = self._with_pair("rollback")
        validation["results"][1]["to_version"] = "0.1.0a1"
        report = contract.assess_update_support(support, validation)
        self.assertEqual(report[kind], [])
        self.assertIn("release_pair_mismatch", {row["code"] for row in report["diagnostics"]})
        support[kind][0]["version"] = ">=0.1.0a1"
        with self.assertRaises(ValueError):
            contract.validate_update_support(support)
        # A same-R reconfiguration may have an explicit rollback test; it must
        # still identify the rollback operation and its capability scope.
        support, validation, kind = self._with_pair("rollback", other=VERSION)
        self.assertEqual(len(contract.assess_update_support(support, validation)[kind]), 1)

    def test_release_pair_does_not_extend_to_other_combinations(self):
        support, validation, kind = self._with_pair("rollback")
        second = deepcopy(support["combinations"][0])
        second.update(id="another-platform", evidence=["another-install"])
        second["platform"]["os_release"] = "different-observed-release"
        support["combinations"].append(second)
        install = deepcopy(validation["results"][0])
        install.update(id="another-install", combination="another-platform", platform=second["platform"])
        validation["results"].append(install)
        report = contract.assess_update_support(support, validation)
        self.assertEqual(len(report["evidence_backed_combinations"]), 2)
        self.assertEqual([row["combination"] for row in report[kind]], ["basic-fixture"])

    def test_strict_sidecar_shapes_duplicate_ids_stable_channel_and_source_match(self):
        support, validation = qualification_documents()
        with self.assertRaises(ValueError):
            contract.validate_update_support({**support, "schema_version": "rpnh/update_support/v1"})
        with self.assertRaises(ValueError):
            contract.validate_update_support({**support, "arbitrary_shell": "not-executed"})
        for malformed in ([], {}, True):
            changed = deepcopy(validation)
            changed["results"][0]["status"] = malformed
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                contract.validate_update_validation(changed)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            contract.validate_update_validation({**validation, "results": validation["results"] * 2})
        changed = deepcopy(validation)
        changed["source"] = source_document(published=True)
        with self.assertRaisesRegex(ValueError, "different source"):
            contract.assess_update_support(support, changed)
        support["source"] = source_document("1.0.0", published=True, channel="stable")
        basic = support["combinations"][0]["components"]["basic"]
        basic["artifact"]["version"] = "1.0.0"
        dsh = with_component(installed_document(), "dsh-web", "0.2.2-rc.1")["components"]["dsh-web"]
        support["combinations"][0]["components"]["dsh-web"] = {key: dsh[key] for key in ("profile", "artifact")}
        with self.assertRaisesRegex(ValueError, "prerelease frontend"):
            contract.validate_update_support(support)

    def test_sidecar_files_reuse_strict_bounded_json_readers(self):
        support, validation = qualification_documents()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "update_support.v2.json"
            path.write_text(json.dumps(support), encoding="utf-8")
            self.assertEqual(contract.read_update_support(path), support)
            path.write_text(json.dumps(validation), encoding="utf-8")
            self.assertEqual(contract.read_update_validation(path), validation)
            path.write_text(json.dumps(support)[:-1] + ',"rollback_to":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                contract.read_update_support(path)


if __name__ == "__main__":
    unittest.main()
