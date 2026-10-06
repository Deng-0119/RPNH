"""Declaration-only diagnostics must not manufacture execution authority."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import socket
import subprocess
import urllib.request

import pytest

from cpn.rpnh.collaboration.host_readiness import (
    HostDeclarationSnapshot, HostReadinessDiagnostic, diagnose_candidate_plan,
    diagnose_host_requirements, snapshot_host_declarations,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE, freeze_candidate_document
from cpn.rpnh.registry.strict_contracts import ref_payload
from test_candidate_plan_persistence import plan_fixture, authored, candidate_request, _replace_record
from test_preserved_candidate_plan_reads import preserved_plan_fixture, publish_fixture_plan


SCHEMA = "application/host_readiness_test/v1"


def declaration(kind="executor", key="example/v1", revision="v1"):
    return {"kind": kind, "key": key, "identity": {"implementation_id": key, "revision": revision},
        "contracts": {"nested": {"ports": ["in", "out"]}}}


def requirements():
    return {"executor": {"example/v1": declaration()}}


def digest(value):
    return hashlib.sha256(freeze_candidate_document(value).encode("utf-8")).hexdigest()


def assert_not_ready(report):
    assert report["execution_ready"] is False
    assert report["purpose"] == "diagnostic"
    assert report["evidence_scope"] == "declarations_only"
    for field in ("permission", "capacity", "callable_identity", "lowering", "runtime_schema_authority"):
        assert report[field] == "not_checked"
    assert report["reservation"] == "not_reserved"


def forbid_runtime(monkeypatch, *, core=None):
    from cpn.plugins import catalog
    from cpn.rpnh import compiler, diagnostics
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("declaration diagnostic attempted a HOST call, I/O, or publication")
    for target, name in ((Registration, "resolve"), (Registration, "bind_gateway"),
                         (Registration, "bind_schema_catalog"), (compiler, "compile_module"),
                         (diagnostics, "diagnose"), (catalog, "load_catalog"),
                         (catalog.metadata, "entry_points"), (socket, "socket"),
                         (subprocess, "Popen"), (urllib.request, "Request"), (urllib.request, "urlopen")):
        monkeypatch.setattr(target, name, forbidden)
    if core is not None:
        monkeypatch.setattr(core, "begin", forbidden)
        monkeypatch.setattr(core, "publish_bytes", forbidden)
    return calls


def test_snapshot_and_diagnostic_never_call_sentinel_factory_or_host(monkeypatch):
    factory_calls = []
    def sentinel_factory(*args, **kwargs):
        factory_calls.append((args, kwargs))
        raise AssertionError("sentinel implementation/factory must remain inert")
    registration = Registration()
    registration.register_schema(SCHEMA, {"$id": SCHEMA,
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    for kind in ("component", "executor", "tool", "analyzer"):
        item = declaration(kind)
        getattr(registration, "register_" + kind)(item["key"], sentinel_factory,
            identity=item["identity"], contracts=item["contracts"])
    calls = forbid_runtime(monkeypatch)
    snapshot = snapshot_host_declarations(registration)
    report = diagnose_host_requirements(snapshot.registrations, snapshot).to_dict()
    assert report["declarations_status"] == "matched"
    assert len(report["declaration_checks"]) == 5
    assert {item["status"] for item in report["declaration_checks"]} == {"matched"}
    assert calls == factory_calls == []
    assert_not_ready(report)


def test_no_snapshot_and_empty_snapshot_are_different():
    unchecked = diagnose_host_requirements(requirements()).to_dict()
    missing = diagnose_host_requirements(requirements(), HostDeclarationSnapshot({})).to_dict()
    assert unchecked["declarations_status"] == "not_checked"
    assert unchecked["host_declarations_digest"] is None
    assert unchecked["declaration_checks"][0]["reason"] == "no_host_snapshot"
    assert missing["declarations_status"] == "missing"
    assert missing["declaration_checks"][0]["reason"] == "missing_host_declaration"
    assert missing["declaration_checks"][0]["host_declaration_digest"] is None
    for report in (unchecked, missing):
        assert_not_ready(report)


def test_full_identity_and_contracts_are_compared_with_explicit_reasons():
    required = requirements()
    required["tool"] = {"absent": declaration("tool", "absent")}
    present = requirements()
    present["executor"]["example/v1"]["identity"]["revision"] = "v2"
    result = diagnose_host_requirements(required, HostDeclarationSnapshot(present)).to_dict()
    assert result["declarations_status"] == "mismatch"
    executor, tool = result["declaration_checks"]
    assert executor["status"] == "mismatch"
    assert executor["reason"] == "declaration_digest_mismatch"
    assert executor["required_declaration_digest"] != executor["host_declaration_digest"]
    assert tool["status"] == "missing"
    assert_not_ready(result)


@pytest.mark.parametrize("field,value", [("identity", 1), ("identity", 1.0), ("identity", False),
                                         ("contracts", ["out", "in"])])
def test_exact_digest_distinguishes_types_and_array_order(field, value):
    required = requirements()
    if field == "identity":
        required["executor"]["example/v1"][field]["flag"] = True
    actual = deepcopy(required)
    if field == "identity":
        actual["executor"]["example/v1"][field]["flag"] = value
    else:
        actual["executor"]["example/v1"][field]["nested"]["ports"] = value
    report = diagnose_host_requirements(required, HostDeclarationSnapshot(actual)).to_dict()
    assert report["declarations_status"] == "mismatch"


def test_digests_cover_exact_canonical_declaration_data_not_callables():
    required = requirements()
    snapshot = HostDeclarationSnapshot(required)
    report = diagnose_host_requirements(required, snapshot).to_dict()
    assert report["required_declarations_digest"] == digest(required)
    assert report["host_declarations_digest"] == snapshot.declarations_digest == digest(required)
    assert report["declaration_checks"][0]["required_declaration_digest"] == digest(declaration())
    other = Registration()
    item = declaration()
    other.register_executor(item["key"], lambda: "different Python implementation",
        identity=item["identity"], contracts=item["contracts"])
    report = diagnose_host_requirements(required, snapshot_host_declarations(other)).to_dict()
    assert report["declarations_status"] == "matched"
    assert report["callable_identity"] == "not_checked"
    assert_not_ready(report)


def test_immutable_snapshot_and_result_are_deeply_detached():
    source = requirements()
    snapshot = HostDeclarationSnapshot(source)
    report = diagnose_host_requirements(source, snapshot)
    original_snapshot, original_report = snapshot.to_dict(), report.to_dict()
    source["executor"]["example/v1"]["contracts"]["nested"]["ports"].append("mutated")
    snapshot.to_dict()["registrations"]["executor"].clear()
    snapshot.registrations["executor"]["example/v1"]["identity"].clear()
    report.to_dict()["declaration_checks"][0]["status"] = "invented"
    assert snapshot.to_dict() == original_snapshot
    assert report.to_dict() == original_report
    assert report.execution_ready is False
    for obj, attr in ((snapshot, "_registrations_json"), (report, "_document_json")):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attr, "{}")


def test_later_registration_does_not_change_snapshot():
    registration = Registration()
    snapshot = snapshot_host_declarations(registration)
    item = declaration()
    registration.register_executor(item["key"], lambda: None,
        identity=item["identity"], contracts=item["contracts"])
    assert diagnose_host_requirements(requirements(), snapshot).to_dict()["declarations_status"] == "missing"
    assert diagnose_host_requirements(requirements(), snapshot_host_declarations(registration)).to_dict()["declarations_status"] == "matched"


def test_empty_requirements_never_infer_execution_authority():
    assert diagnose_host_requirements({}).to_dict()["declarations_status"] == "not_checked"
    report = diagnose_host_requirements({}, HostDeclarationSnapshot(requirements())).to_dict()
    assert report["declarations_status"] == "matched"
    assert report["declaration_checks"] == []
    assert_not_ready(report)


def test_extra_snapshot_declarations_do_not_create_extra_requirements():
    actual = requirements()
    actual["analyzer"] = {"extra": declaration("analyzer", "extra")}
    report = diagnose_host_requirements(requirements(), HostDeclarationSnapshot(actual)).to_dict()
    assert report["declarations_status"] == "matched"
    assert len(report["declaration_checks"]) == 1
    assert report["host_declarations_digest"] != report["required_declarations_digest"]


@pytest.mark.parametrize("damage", ["unknown_kind", "non_object_entries", "non_object_declaration", "empty_key",
    "extra_field", "wrong_key", "wrong_kind", "missing_field", "empty_identity", "bad_contracts",
    "locator", "nan", "python_object", "nonstring_key", "cycle", "dict_subclass"])
def test_malformed_requirements_and_snapshots_are_rejected(damage):
    value = requirements()
    item = value["executor"]["example/v1"]
    if damage == "unknown_kind":
        value["provider"] = {}
    elif damage == "non_object_entries":
        value["executor"] = []
    elif damage == "non_object_declaration":
        value["executor"]["example/v1"] = []
    elif damage == "empty_key":
        value["executor"][""] = value["executor"].pop("example/v1")
        item["key"] = ""
    elif damage == "extra_field":
        item["execution_ready"] = True
    elif damage == "wrong_key":
        item["key"] = "different"
    elif damage == "wrong_kind":
        item["kind"] = "tool"
    elif damage == "missing_field":
        item.pop("contracts")
    elif damage == "empty_identity":
        item["identity"] = {}
    elif damage == "bad_contracts":
        item["contracts"] = []
    elif damage == "locator":
        item["identity"]["nested"] = [{"import_path": "never.load:this"}]
    elif damage == "nan":
        item["contracts"]["bad"] = float("nan")
    elif damage == "python_object":
        item["identity"]["bad"] = object()
    elif damage == "nonstring_key":
        item["contracts"][1] = "bad"
    elif damage == "cycle":
        item["contracts"]["cycle"] = value
    else:
        class CustomDict(dict):
            def items(self):
                raise AssertionError("custom method must not be called")
        value = CustomDict(value)
    for factory in (HostDeclarationSnapshot, diagnose_host_requirements):
        with pytest.raises((TypeError, ValueError)):
            factory(value)


@pytest.mark.parametrize("damage", ["list", "id", "dialect"])
def test_malformed_schema_declarations_rejected(damage):
    schema = {"$id": SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"}
    if damage == "list":
        schema = []
    elif damage == "id":
        schema["$id"] = "application/other/v1"
    else:
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    with pytest.raises((TypeError, ValueError)):
        diagnose_host_requirements({"schema": {SCHEMA: {"kind": "schema", "key": SCHEMA, "schema": schema}}})


def test_schema_reference_is_never_fetched_or_claimed_runtime_compatible(monkeypatch):
    calls = forbid_runtime(monkeypatch)
    schema = {"kind": "schema", "key": SCHEMA, "schema": {"$id": SCHEMA,
        "$schema": "http://json-schema.org/draft-07/schema#", "$ref": "https://invalid.example.invalid/schema"}}
    required = {"schema": {SCHEMA: schema}}
    report = diagnose_host_requirements(required, HostDeclarationSnapshot(required)).to_dict()
    assert report["declarations_status"] == "matched"
    assert_not_ready(report)
    assert calls == []


def test_snapshot_requires_prepared_standard_registration_and_explicit_inert_dto():
    class Fake:
        def declarations(self):
            raise AssertionError("untrusted declaration provider must not be called")
    class Subclass(Registration):
        def declarations(self):
            raise AssertionError("custom declaration provider must not be called")
    for value in ({}, Fake(), Subclass()):
        with pytest.raises(TypeError, match="standard Registration"):
            snapshot_host_declarations(value)
    with pytest.raises(TypeError, match="inert HostDeclarationSnapshot"):
        diagnose_host_requirements(requirements(), requirements())


def test_v1_candidate_uses_existing_exact_reader_with_no_host_or_writes(plan_fixture, monkeypatch):
    from cpn.rpnh.registry._registry import _RegistryCore
    f = plan_fixture
    plan = f[-1].publish(**candidate_request(f, authored(f)))
    snapshot = snapshot_host_declarations(f[6])
    core = _RegistryCore(f[0].run_dir, create=False, read_only=True, catalog=f[0].catalog)
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    calls = forbid_runtime(monkeypatch, core=core)
    report = diagnose_candidate_plan(core, plan.plan_ref, snapshot).to_dict()
    assert report["plan_ref"] == ref_payload(plan.plan_ref)
    assert report["plan_sha256"] == digest(plan.plan)
    assert report["required_declarations_digest"] == digest(plan.plan["host_requirements"]["registrations"])
    assert report["declaration_checks"]
    assert_not_ready(report)
    assert before == (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    assert calls == []


def test_v1_corrupted_candidate_is_rejected_not_reported_ready(plan_fixture, monkeypatch):
    from cpn.rpnh.registry.event_store import RegistryConflict
    f = plan_fixture
    plan = f[-1].publish(**candidate_request(f, authored(f)))
    _replace_record(f[0], plan.plan_ref, lambda body: body["host_requirements"]["registrations"]["executor"].clear())
    calls = forbid_runtime(monkeypatch, core=f[0])
    with pytest.raises((RegistryConflict, ValueError)):
        diagnose_candidate_plan(f[0], plan.plan_ref)
    assert calls == []


def test_v2_candidate_delegates_historical_reader_without_upgrading(preserved_plan_fixture, monkeypatch):
    owner, material, plan, basis = preserved_plan_fixture
    core = owner._core
    reference = publish_fixture_plan(core, plan)
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    snapshot = snapshot_host_declarations(owner.registration)
    calls = forbid_runtime(monkeypatch, core=core)
    report = diagnose_candidate_plan(core, reference, snapshot).to_dict()
    assert report["plan_ref"] == ref_payload(reference)
    assert report["plan_ref"]["entity_type"] == "collaboration_candidate_plan/v2"
    assert report["plan_sha256"] == digest(plan)
    assert_not_ready(report)
    assert before == (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    assert calls == []


@pytest.mark.parametrize("value", [None, {}, "latest", "collaboration_candidate_plan/v1"])
def test_plan_aliases_or_untyped_refs_are_rejected_before_core_read(value):
    with pytest.raises(TypeError, match="standard exact VersionRef"):
        diagnose_candidate_plan(object(), value)


def test_unknown_plan_version_is_explicitly_unsupported_before_core_read():
    reference = VersionRef("collaboration_candidate_plan/v3", new_id("resource"), new_id("resource_version"))
    with pytest.raises(ValueError, match="unsupported candidate plan version"):
        diagnose_candidate_plan(object(), reference)


def test_plan_ref_typed_ids_are_checked_before_core_read():
    reference = VersionRef(PLAN_TYPE, new_id("resource"), new_id("resource_version"))
    object.__setattr__(reference, "entity_id", TypedId("event", "a" * 32))
    with pytest.raises(TypeError, match="TypedId"):
        diagnose_candidate_plan(object(), reference)


def test_diagnostic_plan_context_requires_exact_ref_and_digest_together():
    reference = VersionRef(PLAN_TYPE, new_id("resource"), new_id("resource_version"))
    for kwargs in ({"plan_ref": reference}, {"plan_sha256": "a" * 64},
                   {"plan_ref": reference, "plan_sha256": "A" * 64}):
        with pytest.raises(ValueError):
            HostReadinessDiagnostic(requirements(), **kwargs)
