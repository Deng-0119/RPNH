"""Public shipped example contracts; no provider, owner socket or fake worker.

Synthetic result projections below test the sample checker only. They are not
business execution evidence and are never saved as example run artifacts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import zipfile

import jsonschema
import pytest

from cpn.plugins.catalog import load_catalog
from cpn.plugins.runtime import build_plugin_module, plugin_registration
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection, LocalEnvironmentBinding, PreparationReceipt
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock, canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples/package_reuse"
NATIVE = ROOT / "examples/native_plugin"


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def real_catalog():
    demo = module_at("rpnh_demo", NATIVE / "rpnh_demo.py")
    return load_catalog(json.loads((SAMPLE / "plugins.json").read_text()), factories={"demo": demo.plugin})


def requirements():
    return read_package_environment(SAMPLE / "native-add-v2.zip",
                                   package_lock=PackageResolutionLock((SAMPLE / "native-add-v2.lock.json").read_bytes()))


def test_shipped_v2_package_is_the_actual_native_add_module():
    archive = SAMPLE / "native-add-v2.zip"
    preview = preview_package(archive)
    assert preview.manifest["schema_version"] == "rpnh/share_package/v2"
    assert preview.manifest["dependencies"] == []
    assert resolve_package(archive).to_bytes() == (SAMPLE / "native-add-v2.lock.json").read_bytes()
    actual = json.loads(dict(preview.artifacts)["declarations/main.json"])
    catalog = real_catalog()
    assert actual == build_plugin_module(catalog, "demo/add").to_dict()
    compile_module(ModuleDeclaration.from_dict(actual), plugin_registration(catalog))
    req = requirements()
    assert {r["name"]: r["version_specifier"] for r in req.requirements[0].document["distributions"]} == {
        "rpnh-harness": ">=0.1.0rc2,<1", "rpnh-native-demo": "==0.3.0"}
    assert req.requirements[0].document["services"] == []
    assert req.requirements[0].document["tools"] == []
    assert not any(name.endswith((".py", ".whl")) for name, _ in preview.artifacts)


def test_stock_author_rebuild_is_byte_identical_using_public_real_catalog(tmp_path, monkeypatch):
    author = module_at("public_package_author", SAMPLE / "build_package.py")
    # This is the real source demo factory through the public catalog contract,
    # not an equivalent implementation or an executed business operation.
    monkeypatch.setattr(author, "load_catalog", lambda document: real_catalog())
    output = tmp_path / "authored"
    author.build(output)
    for name in ("native-add-v2.zip", "native-add-v2.lock.json", "owner-request.json"):
        assert (output / name).read_bytes() == (SAMPLE / name).read_bytes()


def test_complete_owner_request_matches_exact_packaged_input_schema_and_budget():
    req = json.loads((SAMPLE / "owner-request.json").read_text())
    assert set(req) == {"task_input", "entry_inputs", "resource_inputs", "inventory_input", "budgets",
                        "model_condition", "owner_statement", "owner_name", "command_id"}
    assert req["entry_inputs"] == {"request": req["task_input"]}
    assert req["task_input"]["payload"] == {"left": 2, "right": 3}
    registration = plugin_registration(real_catalog())
    schema = registration.declaration("schema", req["task_input"]["schema_id"])["schema"]
    jsonschema.validate(req["task_input"]["payload"], schema)
    jsonschema.validate({"left": 12, "right": 8}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"left": 1_000_000_001, "right": 3}, schema)
    assert req["budgets"]["task_total_hard_cap"] == 1
    assert req["resource_inputs"] == {} and req["inventory_input"] is None


def test_three_routes_and_full_templates_select_identical_package_target(tmp_path):
    selection = module_at("public_package_selection", SAMPLE / "select_environment.py")
    routes = [selection.make_selection(executable=sys.executable, prefix=prefix) for prefix in
              (None, tmp_path / "new-venv", tmp_path / "setup-document")]
    assert all(value.to_dict()["target"] == requirements().target.to_dict() for value in routes)
    assert [value.to_dict()["mode"] for value in routes] == ["existing", "new_venv", "new_venv"]
    assert not (tmp_path / "new-venv").exists()
    for name in ("selection-existing.template.json", "selection-new-venv.template.json"):
        template = EnvironmentSelection.from_bytes((SAMPLE / name).read_bytes()).to_dict()
        assert template["target"] == routes[0].to_dict()["target"]
        assert template["plugins"][0]["scoped_requirement_id"] == routes[0].to_dict()["plugins"][0]["scoped_requirement_id"]
        assert template["host_profile_id"] == "rpnh-native/v1"


def test_native_wheel_metadata_matches_real_plugin_and_declared_entrypoint():
    descriptor = json.loads((NATIVE / "rpnh_environment_plugins.json").read_text())
    demo = real_catalog().resolve("demo/add")[0].definition
    assert descriptor == {"schema_version": "rpnh/installed_plugin_metadata/v1", "plugins": [{
        "plugin_id": demo.name, "version": demo.version, "api_contract": demo.api_version, "entry_point": "demo"}]}
    assert "rpnh_environment_plugins.json" in (NATIVE / "MANIFEST.in").read_text()
    assert 'demo = "rpnh_demo:plugin"' in (NATIVE / "pyproject.toml").read_text()
    assert 'setuptools>=77' in (NATIVE / "pyproject.toml").read_text()


def synthetic_projection():
    """A checker fixture, deliberately not a business result."""
    def ref(kind, name):
        return {"entity_type": kind, "logical_id": "resource:" + name, "version_id": "resource_version:" + name}
    evidence = ref("run_terminal_evidence/v1", "synthetic-evidence")
    payload = b'{"value":5}'
    return {"schema_version": "rpnh/package_run_result/v1", "target": requirements().target.to_dict(),
        "run_ref": ref("native_run_identity/v1", "synthetic-run"), "task_ref": ref("task/v1", "synthetic-task"),
        "net_ref": ref("net_instance/v1", "synthetic-net"), "terminal_evidence_ref": evidence,
        "stop_reason": "terminal", "actual_model_call_counts": [0, 0],
        "terminal_result": {"status": "available", "terminal_result_ref": ref("resource_version/v1", "synthetic-result"),
            "terminal_evidence_ref": evidence, "run_execution_authority_ref": ref("run_execution_authority/v1", "synthetic-authority"),
            "final_checkpoint_ref": ref("marking_checkpoint/v1", "synthetic-checkpoint"), "run_outcome": "complete",
            "media_type": "application/json", "byte_count": len(payload), "content_sha256": hashlib.sha256(payload).hexdigest(),
            "output": {"value": 5}}}


def test_result_checker_accepts_only_matching_projection_contract():
    checker = module_at("public_package_result_check", SAMPLE / "verify_result.py")
    value = synthetic_projection()
    assert checker.verify(value, {"value": 5}, requirements().target.to_dict())["status"] == "PASS"
    changed = deepcopy(value)
    changed["terminal_result"]["output"] = {"value": 20}
    changed["terminal_result"]["byte_count"] = len(b'{"value":20}')
    changed["terminal_result"]["content_sha256"] = hashlib.sha256(b'{"value":20}').hexdigest()
    assert checker.verify(changed, {"value": 20}, requirements().target.to_dict())["output"] == {"value": 20}
    with pytest.raises(ValueError):
        checker.verify(changed, {"value": 5}, requirements().target.to_dict())


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(terminal_evidence_ref=None),
    lambda d: d.update(stop_reason="quiescent_marking"),
    lambda d: d.update(actual_model_call_counts=[1, 0]),
    lambda d: d["target"].update(entry_id="other"),
    lambda d: d["terminal_result"].update(status="not_terminal"),
    lambda d: d["terminal_result"].update(content_sha256="0" * 64),
    lambda d: d["terminal_result"].update(byte_count=999),
    lambda d: d["terminal_result"].update(terminal_evidence_ref=None),
    lambda d: d["terminal_result"].update(terminal_result_ref=None),
    lambda d: d["terminal_result"].update(run_outcome="failed"),
    lambda d: d["terminal_result"].update(output={"value": True}),
])
def test_result_checker_never_accepts_preparation_or_wrong_terminal(mutate):
    checker = module_at("public_package_result_check_bad", SAMPLE / "verify_result.py")
    value = synthetic_projection()
    mutate(value)
    with pytest.raises(ValueError):
        checker.verify(value, {"value": 5}, requirements().target.to_dict())


def test_bilingual_walkthrough_has_identical_public_commands():
    en, zh = [(SAMPLE / name).read_text() for name in ("README.md", "README_ZH.md")]
    assert re.findall(r"```bash\n.*?```", en, re.S) == re.findall(r"```bash\n.*?```", zh, re.S)
    for text in (en, zh):
        for value in ("--example package_reuse", "--include-terminal-result", "setup-instructions", "--expected",
                      "BUSINESS_TERMINAL_NOT_VERIFIED", "--state-dir", "--resolved-selections"):
            assert value in text
        assert "ROOT.zip" not in text
        assert "tests/" not in text


def test_binding_reader_selects_receipt_exact_digest_and_rejects_failure(tmp_path):
    reader = module_at("public_prepared_binding_reader", SAMPLE / "prepared_binding.py")
    binding = LocalEnvironmentBinding.from_dict({
        "schema_version": "rpnh/local_environment_binding/v1", "binding_id": "synthetic-binding",
        "binding_revision": "synthetic-revision", "target": requirements().target.to_dict(), "resolution_digest": "1" * 64,
        "mode": "existing", "python": {"executable_realpath": "/synthetic/python", "prefix_realpath": "/synthetic"},
        "tools": [], "plugins": [], "services": [], "host_profile_id": "rpnh-native/v1"})
    receipt = {"schema_version": "rpnh/environment_preparation_receipt/v1", "target": binding.to_dict()["target"],
        "result_binding_revision": "synthetic-revision", "result_binding_digest": binding.digest,
        "plan_digest": "2" * 64, "before_check_digest": "3" * 64, "action_results": [],
        "after_check_digest": "4" * 64, "host_declarations_digest": "5" * 64,
        "failure": None, "completed_at": "2026-10-07T00:00:00+00:00"}
    path = tmp_path / ("binding-" + binding.digest + ".json")
    path.write_bytes(binding.to_bytes())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(PreparationReceipt.from_dict(receipt).to_bytes())
    assert reader.prepared_binding(receipt_path, tmp_path) == path
    receipt["failure"] = "ENVIRONMENT_PREPARATION_INCOMPLETE"
    receipt_path.write_bytes(PreparationReceipt.from_dict(receipt).to_bytes())
    with pytest.raises(ValueError, match="did not complete"):
        reader.prepared_binding(receipt_path, tmp_path)


def test_result_projection_reference_types_exist_in_real_registry_catalog():
    # This catches plausible but nonexistent names such as run/v1. It does not
    # create a Registry or turn this synthetic projection into execution proof.
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    catalog = SchemaCatalog()
    value = synthetic_projection()
    for reference in (*value.values(), *value["terminal_result"].values()):
        if type(reference) is dict and "entity_type" in reference:
            catalog.require(reference["entity_type"], category="object")
    assert value["run_ref"]["entity_type"] == "native_run_identity/v1"
