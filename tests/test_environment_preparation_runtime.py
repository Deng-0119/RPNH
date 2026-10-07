"""Receiver preparation boundaries. No provider, fake worker, or business terminal."""
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import sys
import zipfile

import pytest

from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.environment_local_contracts import (
    EnvironmentSelection, EnvironmentCheckReport, EnvironmentPreparationPlan,
    LocalEnvironmentBinding, EnvironmentResolutionLock, public_check_summary,
)
from cpn.rpnh.collaboration.environment_check import (
    ProbePolicy, check_environment, observed_resolution, probe_python, selected_python,
)
from cpn.rpnh.collaboration.environment_plan import (
    inspect_wheel, plan_environment, ConcreteSelections, _action, wheel_profile_fingerprint,
)
from cpn.rpnh.collaboration.environment_prepare import PreparationExecutionContext, prepare_environment
from cpn.rpnh.collaboration.environment_host import (
    NATIVE_PROFILE, profile_fingerprints, HostProfile, load_host_profile, _distribution_fingerprint,
)
from cpn.rpnh.collaboration.environment_setup import render_environment_setup
from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256
from test_package_environment_requirements import v2, requirements as requirements_document


@pytest.fixture(autouse=True)
def isolate_default_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "private-config/config.json"))


def requirements(tmp_path, document=None):
    preview = v2(document=document)
    path = tmp_path / "package.zip"
    path.write_bytes(preview.archive_bytes)
    return read_package_environment(path), path


def selection(requirements, *, prefix=None, profile=NATIVE_PROFILE):
    return EnvironmentSelection.from_dict({"schema_version": "rpnh/environment_selection/v1", "selection_id": "selected",
        "target": requirements.target.to_dict(), "mode": "existing" if prefix is None else "new_venv",
        "python_selection": {"executable": sys.executable} if prefix is None else {"base_executable": sys.executable, "prefix": str(prefix)},
        "tools": [], "plugins": [], "services": [], "host_profile_id": profile})


def policy():
    return ProbePolicy(host_profiles=profile_fingerprints((NATIVE_PROFILE,)))


def bind(selection, resolution):
    s = selection.to_dict()
    actual = probe_python(selected_python(selection))
    return LocalEnvironmentBinding.from_dict({"schema_version": "rpnh/local_environment_binding/v1", "binding_id": "env", "binding_revision": "rev1",
        "target": s["target"], "resolution_digest": resolution.digest, "mode": s["mode"],
        "python": {k: actual[k] for k in ("executable_realpath", "prefix_realpath")}, "tools": [], "plugins": [], "services": [], "host_profile_id": s["host_profile_id"]})


def wheel(tmp_path, name="example", version="1.0", python=">=3.11", requires=(), files=None, entrypoints=None):
    path = tmp_path / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: {python}\n"
        metadata += "".join("Requires-Dist: " + r + "\n" for r in requires)
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata + "\n")
        for key, value in (files or {}).items():
            archive.writestr(key, value)
        if entrypoints:
            archive.writestr(f"{name}-{version}.dist-info/entry_points.txt", entrypoints)
    return path


def test_readonly_existing_check_and_actual_transitive_inventory(tmp_path):
    req, archive = requirements(tmp_path)
    s = selection(req)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    check = check_environment(req, s, probe_policy=policy())
    assert check.to_dict()["aggregate"] == "passed_for_checked_scope"
    assert check.to_dict()["execution_permitted"] is False
    names = {row["observed"]["distribution"]["name"] for row in check.to_dict()["checks"] if row["observed"]["distribution"]}
    assert {"rpnh-harness", "jsonschema", "attrs", "referencing", "rpds-py", "websockets"} <= names
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    resolution = observed_resolution(req, check)
    assert names == {r["identity"]["name"] for r in resolution.to_dict()["selections"] if r["kind"] == "distribution"}
    assert all(row["identity"]["artifact_digest"] is None for row in resolution.to_dict()["selections"] if row["kind"] == "distribution")


def test_bound_check_rechecks_actual_interpreter_prefix_and_lock(tmp_path):
    req, _ = requirements(tmp_path)
    s = selection(req); check = check_environment(req, s, probe_policy=policy())
    resolution = observed_resolution(req, check); binding = bind(s, resolution)
    assert check_environment(req, binding, probe_policy=policy(), resolution=resolution).to_dict()["aggregate"] == "passed_for_checked_scope"
    d = binding.to_dict(); d["python"]["prefix_realpath"] = str(tmp_path / "other")
    changed = LocalEnvironmentBinding.from_dict(d)
    assert check_environment(req, changed, probe_policy=policy(), resolution=resolution).to_dict()["aggregate"] == "stale"


def test_locked_distribution_drift_rejects_same_check_reuse(tmp_path):
    req, _ = requirements(tmp_path); s = selection(req)
    check = check_environment(req, s, probe_policy=policy()); resolution = observed_resolution(req, check)
    d = resolution.to_dict()
    next(r for r in d["selections"] if r["kind"] == "distribution")["identity"]["version"] = "999"
    changed = EnvironmentResolutionLock.from_dict(d)
    report = check_environment(req, bind(s, changed), probe_policy=policy(), resolution=changed)
    assert report.to_dict()["aggregate"] == "stale"
    assert any(r["reason_code"] == "ENVIRONMENT_BINDING_STALE" for r in report.to_dict()["checks"])


def test_pending_venv_never_checks_base_as_target(tmp_path, monkeypatch):
    req, _ = requirements(tmp_path); prefix = tmp_path / "new"
    s = selection(req, prefix=prefix)
    calls = []
    import cpn.rpnh.collaboration.environment_check as checks
    actual = checks.probe_python
    def capture(path, **kwargs):
        calls.append(path); return actual(path, **kwargs)
    monkeypatch.setattr(checks, "probe_python", capture)
    result = check_environment(req, s, probe_policy=policy())
    assert result.to_dict()["aggregate"] == "blocked"
    assert calls == [str(prefix / "bin/python")]
    assert not prefix.exists()
    assert not any(r["observed"]["python"] for r in result.to_dict()["checks"])


def test_missing_distribution_is_reported_without_install(tmp_path):
    d = requirements_document(); d["distributions"].append({"requirement_id": "missing", "name": "rpnh-absent-offline-fixture", "version_specifier": "==1.0", "satisfies_host_requirement_ids": []})
    req, _ = requirements(tmp_path, d)
    report = check_environment(req, selection(req), probe_policy=policy())
    assert report.to_dict()["aggregate"] == "blocked"
    assert any(r["status"] == "missing" and r["reason_code"] == "ENVIRONMENT_DISTRIBUTION_MISSING" for r in report.to_dict()["checks"])
    assert list(tmp_path.iterdir()) == [tmp_path / "package.zip"]


def test_metadata_resolution_identity_excludes_time_and_paths(tmp_path):
    req, _ = requirements(tmp_path); s = selection(req)
    a = check_environment(req, s, probe_policy=policy())
    d = a.to_dict(); d["checked_at"] = "2099-01-01T00:00:00Z"
    b = EnvironmentCheckReport.from_dict(d)
    assert a.digest != b.digest
    assert observed_resolution(req, a).digest == observed_resolution(req, b).digest
    assert os.fsencode(str(tmp_path)) not in observed_resolution(req, a).to_bytes()


@pytest.mark.parametrize("mutation", [lambda d:d.update(approved=True), lambda d:d["python_selection"].update(import_path="evil"), lambda d:d.update(selection_id=float("nan"))])
def test_receiver_dto_rejects_unknown_locators_and_nonfinite(tmp_path, mutation):
    req, _ = requirements(tmp_path); value = selection(req).to_dict(); mutation(value)
    with pytest.raises((EnvironmentContractError, ValueError)):
        EnvironmentSelection.from_dict(value)


def test_receiver_dto_duplicate_keys_immutable_detached(tmp_path):
    req, _ = requirements(tmp_path); s = selection(req)
    with pytest.raises(EnvironmentContractError):
        EnvironmentSelection.from_bytes(s.to_bytes().replace(b'"selection_id":"selected"',b'"selection_id":"selected","selection_id":"other"'))
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        s.payload = b"{}"
    s.to_dict()["python_selection"]["executable"] = "/other"
    assert s.to_dict()["python_selection"]["executable"] == sys.executable


def test_wheel_requires_python_is_enforced_before_plan(tmp_path):
    bad = wheel(tmp_path, python=">=99")
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_PYTHON_INCOMPATIBLE"):
        inspect_wheel(bad, probe_python(sys.executable))


def test_wheel_direct_url_dependency_is_not_executed_or_locked(tmp_path):
    bad = wheel(tmp_path, requires=("secret @ https://example.invalid/token",))
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_DEPENDENCY_SOURCE_UNSUPPORTED"):
        inspect_wheel(bad, probe_python(sys.executable))


def test_wheel_fingerprint_includes_package_initializers(tmp_path):
    path = wheel(tmp_path, files={"fixture/__init__.py": b"x=1\n", "fixture/host.py": b"def create(x): pass\n"},
                 entrypoints="[rpnh.environment_hosts]\nfixture/v1 = fixture.host:create\n")
    first = wheel_profile_fingerprint(path, "fixture/v1")
    path = wheel(tmp_path, files={"fixture/__init__.py": b"x=2\n", "fixture/host.py": b"def create(x): pass\n"},
                 entrypoints="[rpnh.environment_hosts]\nfixture/v1 = fixture.host:create\n")
    assert first != wheel_profile_fingerprint(path, "fixture/v1")


def test_installed_profile_fingerprint_includes_package_initializers(tmp_path):
    root = tmp_path / "fixture"; root.mkdir(); (root/"__init__.py").write_text("x=1\n"); (root/"host.py").write_text("x=1\n")
    class Distribution:
        files = [Path("fixture/__init__.py"), Path("fixture/host.py")]
        def locate_file(self, path): return tmp_path / path
    first = _distribution_fingerprint(Distribution(), "fixture.host")
    (root/"__init__.py").write_text("x=2\n")
    assert first != _distribution_fingerprint(Distribution(), "fixture.host")


def _cancel_material(tmp_path):
    req, archive = requirements(tmp_path)
    existing = selection(req); source_check = check_environment(req, existing, probe_policy=policy())
    resolution = observed_resolution(req, source_check)
    pending = selection(req, prefix=tmp_path / "new-venv")
    before = check_environment(req, pending, probe_policy=policy())
    # An approved create-only partial step is intentionally not a completed
    # preparation. Cancellation tests never manufacture an install or terminal.
    action = _action("create", "create_venv", "rpnh/venv_adapter/v1", path=str(tmp_path/"new-venv"), base_executable=sys.executable)
    plan = EnvironmentPreparationPlan.from_dict({"schema_version": "rpnh/environment_preparation_plan/v1", "plan_id": "cancel-test",
        "target": req.target.to_dict(), "selection_digest": pending.digest, "binding_revision": None,
        "resolution_digest": resolution.digest, "based_on_check_digest": before.digest, "mode": "new_venv",
        "actions": [action], "unresolved": [], "authorization_requirements": ["exact_plan_digest"]})
    return req, archive, pending, before, resolution, plan


def test_no_approval_document_can_execute_plan(tmp_path):
    req, archive, pending, before, resolution, plan = _cancel_material(tmp_path)
    context = PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:False,probe_policy=policy())
    with pytest.raises(EnvironmentContractError, match="PREPARATION_AUTHORIZATION_REQUIRED"):
        prepare_environment(plan, execution_context=context)
    assert not (tmp_path/"new-venv").exists()


def test_cancel_before_create_preserves_missing_target_fact(tmp_path):
    req, archive, pending, before, resolution, plan = _cancel_material(tmp_path)
    context = PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:True,probe_policy=policy(),cancelled=lambda:True)
    result = prepare_environment(plan, execution_context=context)
    assert result.binding is None
    assert result.receipt.to_dict()["failure"] == "ENVIRONMENT_PREPARATION_CANCELLED"
    assert result.after_check.to_dict()["aggregate"] == "blocked"
    assert not (tmp_path/"new-venv").exists()
    assert not any(r["observed"]["python"] for r in result.after_check.to_dict()["checks"])


def test_cancel_after_directory_reservation_keeps_partial_directory(tmp_path):
    req, archive, pending, before, resolution, plan = _cancel_material(tmp_path)
    context = PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:True,probe_policy=policy(),
                                         cancelled=lambda:(tmp_path/"new-venv").exists())
    result = prepare_environment(plan, execution_context=context)
    assert result.receipt.to_dict()["failure"] == "ENVIRONMENT_PREPARATION_CANCELLED"
    assert (tmp_path/"new-venv").is_dir()
    assert result.binding is None and result.after_check.to_dict()["aggregate"] == "blocked"


def test_existing_target_not_overwritten_even_after_approval(tmp_path):
    req, archive, pending, before, resolution, plan = _cancel_material(tmp_path)
    prefix = tmp_path/"new-venv";prefix.mkdir(); sentinel = prefix/"keep";sentinel.write_text("user")
    context = PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:True,probe_policy=policy())
    result = prepare_environment(plan, execution_context=context)
    assert result.receipt.to_dict()["failure"] == "ENVIRONMENT_TARGET_ALREADY_EXISTS"
    assert sentinel.read_text() == "user"


def test_plan_targets_exact_prior_check(tmp_path):
    req, _ = requirements(tmp_path); s = selection(req); check = check_environment(req,s,probe_policy=policy())
    altered = s.to_dict();altered["selection_id"]="other"
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_BINDING_STALE"):
        plan_environment(req,EnvironmentSelection.from_dict(altered),check)


def test_setup_document_has_exact_material_no_new_authority(tmp_path):
    *_, plan = _cancel_material(tmp_path)
    for format in ("text","json"):
        document = render_environment_setup(plan,format=format)
        assert plan.digest in document.content
        assert plan.to_dict()["target"]["root_archive_digest"] in document.content
        assert "No authorization" in document.content
        assert "same check_environment" in document.content
        assert "business terminal" in document.content


def test_default_public_check_projection_drops_paths_and_raw_metadata(tmp_path):
    req, _ = requirements(tmp_path); s = selection(req)
    result = check_environment(req,s,probe_policy=policy())
    public = canonical_bytes(public_check_summary(result))
    assert os.fsencode(sys.executable) not in public
    assert b"credential_ref" not in public and b"requires_dist" not in public
    assert b'"execution_permitted":false' in public


def test_unknown_custom_capability_is_incomplete_not_assumed(tmp_path):
    d=requirements_document();d["system_requirements"]=[{"requirement_id":"cap", "kind":"capability", "capability_contract_id":"example/unknown/v1", "constraint":True,"satisfies_host_requirement_ids":[]}]
    req,_=requirements(tmp_path,d)
    report=check_environment(req,selection(req),probe_policy=policy())
    assert report.to_dict()["aggregate"]=="incomplete"
    assert any(r["status"]=="unsupported" for r in report.to_dict()["checks"])


def test_services_check_only_config_exact_model_and_credential_reference(tmp_path):
    d=requirements_document();d["services"]=[{"requirement_id":"service", "service_contract_id":"example/service/v1", "required_capabilities":[],"model_constraint":{"kind":"exact","model_condition":"model-2026-01"},"authentication_required":True,"satisfies_host_requirement_ids":[]}]
    req,_=requirements(tmp_path,d);s=selection(req).to_dict();scope=req.requirements[0].scoped_id("service")
    s["services"]=[{"scoped_requirement_id":scope,"service_contract_id":"example/service/v1","profile_ref":"local-profile","credential_ref":"local-reference","model_condition":"model-2026-01"}]
    calls=[]
    def local_probe(**kwargs):
        calls.append(kwargs)
        return {"status":"satisfied","reason_code":"LOCAL_CONFIGURATION_OBSERVED","identity":{"service_contract_id":"example/service/v1","model_condition":"model-2026-01","profile_digest":"f"*64},"evidence_level":"configuration_observed"}
    p=ProbePolicy(services={"example/service/v1":local_probe},credential_present=lambda ref:False,host_profiles=policy().host_profiles)
    check=check_environment(req,EnvironmentSelection.from_dict(s),probe_policy=p)
    assert len(calls)==1
    assert check.to_dict()["aggregate"]=="blocked"
    assert "remote_service_availability" in check.to_dict()["not_checked"]
    assert any(r["reason_code"]=="ENVIRONMENT_CREDENTIAL_REFERENCE_MISSING" for r in check.to_dict()["checks"])


def test_reconstructed_resolution_rejects_nested_direct_source(tmp_path):
    req,_=requirements(tmp_path);s=selection(req)
    d=observed_resolution(req,check_environment(req,s,probe_policy=policy())).to_dict()
    next(r for r in d["selections"] if r["kind"]=="distribution")["identity"]["requires_dist"]=["hidden @ https://example.invalid/private-token"]
    with pytest.raises(EnvironmentContractError):
        EnvironmentResolutionLock.from_dict(d)


def test_missing_transitive_lock_rejected_before_action_or_approval(tmp_path):
    req,archive,pending,before,resolution,plan=_cancel_material(tmp_path)
    d=resolution.to_dict();d["selections"]=[r for r in d["selections"] if not (r["kind"]=="distribution" and r["identity"]["name"]=="attrs")]
    incomplete=EnvironmentResolutionLock.from_dict(d);p=plan.to_dict();p["resolution_digest"]=incomplete.digest
    actions=[]
    context=PreparationExecutionContext(req,pending,incomplete,before,archive,(),lambda *args:actions.append(args),probe_policy=policy())
    with pytest.raises(EnvironmentContractError,match="ENVIRONMENT_DISTRIBUTION_MISSING"):
        prepare_environment(EnvironmentPreparationPlan.from_dict(p),execution_context=context)
    assert not actions and not (tmp_path/"new-venv").exists()


def test_exact_archive_rechecked_before_prepare(tmp_path):
    req,archive,pending,before,resolution,plan=_cancel_material(tmp_path)
    archive.write_bytes(archive.read_bytes()+b"mutated")
    context=PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:True,probe_policy=policy())
    with pytest.raises(ValueError):
        prepare_environment(plan,execution_context=context)
    assert not (tmp_path/"new-venv").exists()


def test_neutral_frozen_packages_are_real_exact_v2_closed_modules(tmp_path):
    from environment_neutral_fixture import build_story,build_fixture_wheel,select_story,resolve_story_plugin
    from cpn.rpnh.compiler import compile_module
    from cpn.plugins.runtime import plugin_registration
    from environment_neutral_fixture import author_module
    hashes=[]
    for story in ("numbers","files"):
        for variant in ("P0","P1"):
            built=build_story(tmp_path,story,variant)
            root=built["preview"]
            rule=json.loads(dict(root.artifacts)["business/rule.json"])
            compiled=compile_module(built["module"],plugin_registration(author_module().catalog({"rule":rule})))
            assert compiled.operations
            assert root.to_dict()["execution_permitted"] is False
            assert built["requirements"].target.to_dict()["root_archive_digest"]==sha256(built["archive"].read_bytes())
            assert select_story(built,sys.executable).to_dict()["plugins"][0]["configuration_ref"]==str(built["archive"].absolute())
            hashes.append(root.archive_digest)
    assert len(set(hashes))==4
    one=build_fixture_wheel(tmp_path)
    assert wheel_profile_fingerprint(one,"rpnh-neutral-fixture/v1") is not None


def test_default_report_directory_is_private_config_scoped(tmp_path):
    req,archive,pending,before,resolution,plan=_cancel_material(tmp_path)
    context=PreparationExecutionContext(req,pending,resolution,before,archive,(),lambda *args:True,probe_policy=policy(),cancelled=lambda:True)
    assert context.report_directory.parent == tmp_path/"private-config/environment-preparation"
    result=prepare_environment(plan,execution_context=context)
    receipt=context.report_directory / ("receipt-"+result.receipt.digest+".json")
    assert receipt.read_bytes()==result.receipt.to_bytes()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert not list(context.report_directory.rglob("*.sqlite*"))


def test_immutable_report_same_bytes_reused_without_overwrite(tmp_path):
    from cpn.rpnh.collaboration.environment_prepare import save_immutable
    req,_=requirements(tmp_path);s=selection(req)
    file=save_immutable(tmp_path/"reports","selection.json",s)
    assert save_immutable(tmp_path/"reports","selection.json",s)==file
    file.write_bytes(b"tampered")
    with pytest.raises(EnvironmentContractError,match="ENVIRONMENT_BINDING_STALE"):
        save_immutable(tmp_path/"reports","selection.json",s)


def test_generic_wheel_metadata_resolves_fixture_plugin_without_factory(tmp_path,monkeypatch):
    from environment_neutral_fixture import build_fixture_wheel,build_story,select_story
    from cpn.rpnh.collaboration.environment_plan import wheel_plugin_identity
    built=build_story(tmp_path,"numbers","P0");artifact=build_fixture_wheel(tmp_path)
    req=built["requirements"].requirements[0];s=select_story(built,sys.executable)
    # Only the generic metadata reader is exercised; no acceptance-only JSON repair.
    metadata=wheel_plugin_identity(artifact,req.document["plugins"][0],s.to_dict()["plugins"][0],"rpnh-neutral-fixture")
    assert metadata["configuration_digest"]==sha256(built["archive"].read_bytes())
    assert metadata["distribution"]=="rpnh-neutral-fixture" and metadata["api_contract"]=="rpnh/plugin/v1"


def test_static_runtime_schemas_match_builders():
    from cpn.rpnh.collaboration.environment_local_contracts import SELECTION_SCHEMA,BINDING_SCHEMA,RESOLUTION_SCHEMA,CHECK_SCHEMA,PLAN_SCHEMA,RECEIPT_SCHEMA
    from cpn.rpnh.collaboration.environment_evidence import evidence_schema
    root=Path(__file__).resolve().parents[1]/"cpn/schemas/rpnh"
    for schema in [SELECTION_SCHEMA,BINDING_SCHEMA,RESOLUTION_SCHEMA,CHECK_SCHEMA,PLAN_SCHEMA,RECEIPT_SCHEMA,evidence_schema()]:
        assert json.loads((root/(schema["$id"].split("/")[1]+".v1.schema.json")).read_bytes())==schema
