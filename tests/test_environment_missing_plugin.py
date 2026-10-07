"""Missing installed plugin metadata is resolvable; probe failures are not."""
from types import SimpleNamespace
import sys

import pytest

from cpn.rpnh.collaboration.environment_check import ProbePolicy, check_environment
from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError
from cpn.rpnh.collaboration.environment_host import NATIVE_PROFILE, _native_plugin_metadata
from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection
from cpn.rpnh.collaboration.environment_plan import plan_environment, resolve_local_wheels
from cpn.rpnh.collaboration.environment_prepare import PreparationExecutionContext, prepare_environment
from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256
from test_environment_preparation_runtime import requirements, selection, wheel
from test_package_environment_requirements import requirements as requirements_document


def material(tmp_path, *, second_plugin=False, wheel_metadata=True):
    document = requirements_document()
    document["distributions"].append({"requirement_id": "fixture", "name": "rpnh-absent-plugin-fixture",
        "version_specifier": "==1.0", "satisfies_host_requirement_ids": []})
    names = ["missing-demo", "other-demo"] if second_plugin else ["missing-demo"]
    document["plugins"] = [{"requirement_id": name, "plugin_id": name,
        "distribution_requirement_id": "fixture", "version_specifier": "==1.0",
        "api_contract": "rpnh/plugin/v1", "satisfies_host_requirement_ids": []} for name in names]
    req, archive = requirements(tmp_path, document)
    config = tmp_path / "plugins.json"
    config.write_bytes(canonical_bytes({"schema_version": "rpnh/plugins/v1", "plugins": [
        {"name": name, "entry_point": name, "version": "1.0", "config": {}, "environment": []}
        for name in names]}))
    selected = selection(req).to_dict()
    selected["plugins"] = [{"scoped_requirement_id": req.requirements[0].scoped_id(name),
        "plugin_id": name, "configuration_ref": str(config)} for name in names]
    selected = EnvironmentSelection.from_dict(selected)
    files = {"absent_plugin_fixture/__init__.py": "raise RuntimeError('factory must not load during resolution')\n"}
    if wheel_metadata:
        files["rpnh_absent_plugin_fixture-1.0.dist-info/rpnh_environment_plugins.json"] = canonical_bytes({
            "schema_version": "rpnh/installed_plugin_metadata/v1", "plugins": [{"plugin_id": "missing-demo",
                "version": "1.0", "api_contract": "rpnh/plugin/v1", "entry_point": "missing-demo"}]})
    artifact = wheel(tmp_path, name="rpnh_absent_plugin_fixture", files=files,
        entrypoints="[rpnh.plugins]\nmissing-demo = absent_plugin_fixture:definition\n")
    policy = ProbePolicy(plugins={name: _native_plugin_metadata for name in names},
        host_profiles={NATIVE_PROFILE: sha256(b"explicit trusted test profile")})
    return req, archive, selected, artifact, policy


def plugin_checks(check):
    return [row for row in check.to_dict()["checks"] if row["check_id"].startswith("plugins:")]


def test_absent_native_entrypoint_reports_missing_not_crashed(tmp_path, monkeypatch):
    req, _, selected, _, policy = material(tmp_path)
    monkeypatch.setattr("cpn.rpnh.collaboration.environment_host.metadata.entry_points", lambda **_: [])
    report = check_environment(req, selected, probe_policy=policy)
    row, = plugin_checks(report)
    assert row["status"] == "missing"
    assert row["reason_code"] == "ENVIRONMENT_PLUGIN_MISSING"
    assert row["evidence_level"] == "installed_metadata"
    assert row["observed"]["plugin"] is None
    assert report.to_dict()["aggregate"] == "blocked"
    assert report.to_dict()["execution_permitted"] is False


@pytest.mark.parametrize("failure", ["ambiguous", "crashed", "invalid_configuration"])
def test_unknown_native_probe_failures_remain_blocked_with_exact_wheel(tmp_path, monkeypatch, failure):
    req, archive, selected, artifact, policy = material(tmp_path)
    def entry_points(**_):
        if failure == "crashed":
            raise RuntimeError("unclassified installed metadata error")
        return [SimpleNamespace(name="missing-demo"), SimpleNamespace(name="missing-demo")]
    monkeypatch.setattr("cpn.rpnh.collaboration.environment_host.metadata.entry_points", entry_points)
    if failure == "invalid_configuration":
        (tmp_path / "plugins.json").write_bytes(b"invalid JSON")
    before = check_environment(req, selected, probe_policy=policy)
    row, = plugin_checks(before)
    assert (row["status"], row["reason_code"]) == ("failed", "ENVIRONMENT_PROBE_FAILED")
    concrete = resolve_local_wheels(req, selected, before, [artifact])
    expected = {"scoped_requirement_id": row["scoped_requirement_id"], "reason_code": "ENVIRONMENT_PROBE_FAILED"}
    assert expected in concrete.resolution.to_dict()["unresolved"]
    plan = plan_environment(req, selected, before, concrete_selections=concrete)
    assert expected in plan.to_dict()["unresolved"]
    authorization_calls = []
    context = PreparationExecutionContext(req, selected, concrete.resolution, before, archive, (),
        lambda *args: authorization_calls.append(args) or True, probe_policy=policy,
        report_directory=tmp_path / "reports")
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_SELECTION_UNRESOLVED"):
        prepare_environment(plan, execution_context=context)
    assert not authorization_calls


def test_exact_wheel_resolves_missing_plugin_without_changing_installed_check(tmp_path, monkeypatch):
    req, _, selected, artifact, policy = material(tmp_path)
    monkeypatch.setattr("cpn.rpnh.collaboration.environment_host.metadata.entry_points", lambda **_: [])
    before = check_environment(req, selected, probe_policy=policy)
    check_bytes = before.to_bytes()
    concrete = resolve_local_wheels(req, selected, before, [artifact])
    assert concrete.resolution.to_dict()["unresolved"] == []
    choice, = [r for r in concrete.resolution.to_dict()["selections"] if r["kind"] == "plugin"]
    assert choice["scoped_requirement_id"] == plugin_checks(before)[0]["scoped_requirement_id"]
    assert choice["source_contract"] == "rpnh/wheel_plugin_metadata/v1"
    assert choice["evidence_level"] == "artifact_verified"
    assert before.to_bytes() == check_bytes
    assert before.to_dict()["aggregate"] == "blocked"
    assert "absent_plugin_fixture" not in sys.modules
    plan = plan_environment(req, selected, before, concrete_selections=concrete).to_dict()
    assert plan["unresolved"] == []
    assert [r["kind"] for r in plan["actions"]] == ["install_distribution", "assemble_trusted_host"]
    assert plan["actions"][-1]["target"]["name"] == NATIVE_PROFILE


def test_wheel_without_plugin_declaration_cannot_clear_missing_check(tmp_path, monkeypatch):
    req, _, selected, artifact, policy = material(tmp_path, wheel_metadata=False)
    monkeypatch.setattr("cpn.rpnh.collaboration.environment_host.metadata.entry_points", lambda **_: [])
    before = check_environment(req, selected, probe_policy=policy)
    concrete = resolve_local_wheels(req, selected, before, [artifact])
    assert concrete.resolution.to_dict()["unresolved"] == [{
        "scoped_requirement_id": plugin_checks(before)[0]["scoped_requirement_id"],
        "reason_code": "ENVIRONMENT_PLUGIN_MISSING"}]


def test_wheel_metadata_only_clears_matching_missing_plugin_scope(tmp_path, monkeypatch):
    req, _, selected, artifact, policy = material(tmp_path, second_plugin=True)
    monkeypatch.setattr("cpn.rpnh.collaboration.environment_host.metadata.entry_points", lambda **_: [])
    before = check_environment(req, selected, probe_policy=policy)
    concrete = resolve_local_wheels(req, selected, before, [artifact])
    assert concrete.resolution.to_dict()["unresolved"] == [{
        "scoped_requirement_id": req.requirements[0].scoped_id("other-demo"),
        "reason_code": "ENVIRONMENT_PLUGIN_MISSING"}]
