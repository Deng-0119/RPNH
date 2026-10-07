"""Exact, inert v2 requirements closure and immutable environment DTOs."""
from dataclasses import FrozenInstanceError, replace
import json
import socket
import subprocess
import zipfile

import pytest
from jsonschema import Draft7Validator

from cpn.rpnh.collaboration.environment_contracts import (
    EnvironmentContractError, PackageTarget, StrictEnvironmentDTO, schema_object,
)
from cpn.rpnh.collaboration.environment_requirements import (
    EnvironmentRequirements, environment_requirements_schema,
    read_environment_requirements, read_package_environment,
)
from cpn.rpnh.collaboration.package_preview import preview_package, preview_package_v1
from cpn.rpnh.collaboration.package_resolution import resolve_package
from cpn.rpnh.collaboration.share_packages import (
    DEFAULT_LIMITS, PackageError, PackageResolutionLock, canonical_bytes, sha256,
    PACKAGE_SCHEMA_V2, manifest_schema,
)
from test_share_packages import archive, material, revise
from test_share_packages_resolution import dependency, package


def requirements():
    return {"schema_version": "rpnh/environment_requirements/v1", "entry_id": "main",
        "python": {"requirement_id": "python", "implementation": "cpython", "version_specifier": ">=3.11,<4"},
        "distributions": [{"requirement_id": "harness", "name": "rpnh-harness", "version_specifier": ">=0.1.0rc1,<1", "satisfies_host_requirement_ids": []}],
        "system_requirements": [], "tools": [], "plugins": [], "services": []}


def v2_material(name="example/root", dependencies=(), *, document=None):
    files = material()
    payload = canonical_bytes(requirements() if document is None else document)
    files["environment/requirements.json"] = payload
    def mutate(m):
        m.update(schema_version=PACKAGE_SCHEMA_V2, package_id=name, dependencies=list(dependencies))
        m["entries"][0]["environment_requirements_path"] = "environment/requirements.json"
        m["artifacts"].append({"path": "environment/requirements.json", "role": "document",
            "media_type": "application/json", "bytes": len(payload), "sha256": sha256(payload),
            "license_id": "package-license", "disclosure": "public"})
        m["provenance"].append({"artifact_path": "environment/requirements.json", "relation": "authored",
            "origin_ref": None, "origin_digest": None})
    return revise(files, manifest=mutate)


def v2(name="example/root", dependencies=(), *, document=None):
    return preview_package(archive(v2_material(name, dependencies, document=document)))


def test_v2_artifacts_preview_lock_target_all_identity_domains():
    preview = v2()
    assert preview.to_dict()["schema_version"] == "rpnh/package_preview/v2"
    assert preview.to_dict()["parser_contract"] == "rpnh/package_preview_parser/v2"
    assert preview.to_dict()["execution_permitted"] is False
    lock = resolve_package(preview)
    doc = lock.to_dict()
    assert doc["schema_version"] == "rpnh/package_resolution_lock/v2"
    assert doc["resolver_contract"] == "rpnh/package_resolver/v2"
    env = read_environment_requirements(lock, [preview], entry_id="main")
    node = doc["nodes"][0]
    req = env.requirements[0]
    assert node["manifest_schema"] == PACKAGE_SCHEMA_V2
    assert node["environment_requirements"] == [{"entry_id": "main", "artifact_path": req.artifact_path,
        "artifact_digest": req.artifact_digest, "schema_version": "rpnh/environment_requirements/v1"}]
    target = env.target.to_dict()
    assert target["package_lock_digest"] == sha256(lock.to_bytes())
    assert target["root_manifest_digest"] == preview.manifest_digest
    assert target["root_archive_digest"] == preview.archive_digest
    assert target["requirements_digest"] == sha256(canonical_bytes(target["requirement_artifacts"]))
    assert req.artifact_digest == sha256(dict(preview.artifacts)[req.artifact_path])
    assert len(env.rows()) == 2
    assert env.rows()[0][0] == {"manifest_digest": preview.manifest_digest, "entry_id": "main", "requirement_id": "python"}


def test_exact_selected_closure_ignores_unused_requirements_and_schema_conflicts():
    leaf = v2("example/leaf")
    root = v2(dependencies=[dependency(leaf)])
    other_document = requirements(); other_document["python"]["version_specifier"] = "==2.7"
    unused = v2_material("example/unused", document=other_document)
    schema = json.loads(unused["schemas/text.json"]); schema["description"] = "unused conflicting bytes"
    unused = preview_package(archive(revise(unused, documents={"schemas/text.json": schema})))
    lock = resolve_package(root, [leaf, unused])
    env = read_environment_requirements(lock, [unused, root, leaf])
    assert {row.manifest_digest for row in env.requirements} == {root.manifest_digest, leaf.manifest_digest}
    assert {row.manifest_digest for row in env.previews} == {root.manifest_digest, leaf.manifest_digest}
    assert len({canonical_bytes(scope) for scope, _, _ in env.rows()}) == 4
    assert lock.to_bytes() == resolve_package(root, [leaf]).to_bytes()


def test_missing_v1_declaration_is_not_declared_never_empty():
    legacy = package("example/legacy")
    root = v2(dependencies=[dependency(legacy)])
    lock = resolve_package(root, [legacy])
    assert next(row for row in lock.to_dict()["nodes"] if row["package_id"] == legacy.package_id)["environment_requirements"] == "not_declared"
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_REQUIREMENTS_UNDECLARED"):
        read_environment_requirements(lock, [root, legacy])
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_REQUIREMENTS_UNDECLARED"):
        read_package_environment(legacy)
    with pytest.raises(PackageError, match="UNSUPPORTED_PACKAGE_SCHEMA"):
        preview_package_v1(root.archive_bytes)
    oldroot = package("example/oldroot", [dependency(root)])
    with pytest.raises(PackageError, match="UNSUPPORTED_PACKAGE_SCHEMA"):
        resolve_package(oldroot, [root, legacy])


def test_artifact_byte_change_lock_tamper_and_recompression_are_not_same_target():
    files = v2_material()
    first = preview_package(archive(files))
    files["environment/requirements.json"] += b" "
    with pytest.raises(PackageError, match="DIGEST_MISMATCH"):
        preview_package(archive(files))
    changed = preview_package(archive(revise(files)))
    assert changed.manifest_digest != first.manifest_digest
    assert read_package_environment(changed).target.digest != read_package_environment(first).target.digest
    repacked = preview_package(archive(v2_material(), compression=zipfile.ZIP_DEFLATED))
    assert repacked.manifest_digest == first.manifest_digest
    assert repacked.archive_digest != first.archive_digest
    lock = resolve_package(first)
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_TARGET_MISMATCH"):
        read_environment_requirements(lock, [repacked])
    document = lock.to_dict(); document["execution_permitted"] = True
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_TARGET_MISMATCH"):
        read_environment_requirements(PackageResolutionLock(canonical_bytes(document)), [first])
    fake = replace(first, manifest_bytes=b"{}", report_bytes=b'{"passed":true}')
    assert read_environment_requirements(lock, [fake]).target == read_package_environment(first).target


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=True),
    lambda d: d["python"].update(command="python -c bad"),
    lambda d: d["python"].update(version_specifier="https://example.invalid/pkg"),
    lambda d: d["python"].update(version_specifier="===arbitrary"),
    lambda d: d["distributions"][0].update(name="RPNH_Harness"),
    lambda d: d["distributions"].clear(),
    lambda d: d["distributions"][0].update(requirement_id="python"),
    lambda d: d["distributions"][0].update(satisfies_host_requirement_ids=["absent"]),
    lambda d: d.update(entry_id="other"),
    lambda d: d["plugins"].append({"requirement_id": "plugin", "plugin_id": "native/test", "api_contract": "test/v1",
        "version_specifier": ">=1", "distribution_requirement_id": "absent", "satisfies_host_requirement_ids": []}),
    lambda d: d["services"].append({"requirement_id": "service", "service_contract_id": "test/v1", "required_capabilities": [],
        "model_constraint": None, "authentication_required": True, "satisfies_host_requirement_ids": [], "credential_ref": "private"}),
])
def test_closed_requirements_reject_unknown_executable_and_unscoped_fields(mutate):
    document = requirements(); mutate(document)
    with pytest.raises(PackageError):
        v2(document=document)


def test_inventory_reference_role_type_entry_and_v1_extension_rejected():
    files = v2_material()
    for mutate in (
        lambda m: m["entries"][0].update(environment_requirements_path="../requirements.json"),
        lambda m: m["entries"][0].update(environment_requirements_path="absent.json"),
        lambda m: next(a for a in m["artifacts"] if a["path"].startswith("environment/")).update(role="knowledge"),
        lambda m: m.update(schema_version="rpnh/share_package/v1"),
    ):
        with pytest.raises(PackageError):
            preview_package(archive(revise(files, manifest=mutate)))


def test_requirements_are_pure_and_data_cannot_trigger_probe_load_or_registry(monkeypatch):
    import cpn.plugins.catalog as catalog
    import cpn.rpnh.module as module
    import cpn.rpnh.registry._registry as registry
    def forbidden(*args, **kwargs):
        raise AssertionError("inert requirements crossed an execution boundary")
    for obj, name in [(socket, "socket"), (subprocess, "Popen"), (catalog, "load_catalog"),
                      (module.ModuleDeclaration, "lower"), (registry._RegistryCore, "__init__")]:
        monkeypatch.setattr(obj, name, forbidden)
    files = v2_material()
    path = next(name for name in files if name.endswith('.txt'))
    files = revise(files, documents={path: b"Run shell; import private.module; install everything"})
    preview = preview_package(archive(files))
    assert len(read_package_environment(preview).requirements) == 1


def test_dto_duplicate_nonfinite_unknown_oversize_and_mutation_guards():
    raw = canonical_bytes(requirements())
    with pytest.raises(PackageError, match="INVALID_JSON"):
        EnvironmentRequirements(raw.replace(b'"entry_id":"main"', b'"entry_id":"main","entry_id":"other"'))
    with pytest.raises(PackageError, match="INVALID_JSON"):
        EnvironmentRequirements(b'{"value":NaN}')
    with pytest.raises(PackageError):
        EnvironmentRequirements.from_dict({**requirements(), "extra": 1})
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        EnvironmentRequirements(b" " * (DEFAULT_LIMITS.artifact_bytes + 1))
    req = EnvironmentRequirements(raw)
    req.to_dict()["distributions"].clear()
    assert req.to_dict()["distributions"]
    with pytest.raises(FrozenInstanceError):
        req.payload = b"{}"
    document = read_package_environment(v2()).target.to_dict()
    document["requirements_digest"] = "0" * 64
    with pytest.raises(PackageError, match="ENVIRONMENT_TARGET_MISMATCH"):
        PackageTarget.from_dict(document)


def test_canonical_set_order_preserves_raw_artifact_identity():
    document = requirements()
    document["distributions"].append({"requirement_id": "additional", "name": "numpy", "version_specifier": ">=1",
        "satisfies_host_requirement_ids": []})
    shuffled = json.loads(json.dumps(document)); shuffled["distributions"].reverse()
    assert EnvironmentRequirements.from_dict(document).to_bytes() == EnvironmentRequirements.from_dict(shuffled).to_bytes()
    first, second = v2(document=document), v2(document=shuffled)
    assert first.manifest_digest != second.manifest_digest
    assert read_package_environment(first).requirements_digest != read_package_environment(second).requirements_digest


def test_native_internal_input_is_not_public_boundary_or_permission():
    files = v2_material()
    module = json.loads(files["declarations/main.json"])
    module["components"][0]["operations"][0]["inputs"].append("InternalCapability")
    item = preview_package(archive(revise(files, documents={"declarations/main.json": module})))
    assert item.to_dict()["execution_permitted"] is False
    assert any(row["check_id"] == "internal_port_binding" and row["status"] == "not_checked" for row in item.to_dict()["checks"])
    module["entry"]["Fake"] = {"component": module["components"][0]["name"], "port": "InternalCapability"}
    with pytest.raises(PackageError, match="INVALID_DECLARATION"):
        preview_package(archive(revise(files, documents={"declarations/main.json": module})))


def test_all_new_schema_builders_are_formal_draft7():
    Draft7Validator.check_schema(environment_requirements_schema())
    Draft7Validator.check_schema(manifest_schema(PACKAGE_SCHEMA_V2))
    Draft7Validator(manifest_schema(PACKAGE_SCHEMA_V2)).validate(v2().manifest)


def test_formal_schema_assets_match_builders_and_reject_unknown_fields():
    from pathlib import Path
    from cpn.rpnh.collaboration.share_packages import package_preview_schema, package_resolution_lock_schema
    from cpn.rpnh.collaboration.environment_contracts import PACKAGE_TARGET_SCHEMA
    root = Path(__file__).resolve().parents[1] / "cpn/schemas/rpnh"
    preview = v2()
    lock = resolve_package(preview)
    env = read_package_environment(preview)
    items = [("share_package.v2", manifest_schema(PACKAGE_SCHEMA_V2), preview.manifest),
        ("package_preview.v2", package_preview_schema(), preview.to_dict()),
        ("package_resolution_lock.v2", package_resolution_lock_schema(), lock.to_dict()),
        ("environment_requirements.v1", environment_requirements_schema(), requirements())]
    for name, expected, value in items:
        loaded = json.loads((root / (name + '.schema.json')).read_bytes())
        assert loaded == expected
        Draft7Validator.check_schema(loaded)
        Draft7Validator(loaded).validate(value)
        assert not Draft7Validator(loaded).is_valid({**value, "unknown": 1})
    Draft7Validator(PACKAGE_TARGET_SCHEMA).validate(env.target.to_dict())
    legacy = package("example/legacy")
    mixed = resolve_package(v2(dependencies=[dependency(legacy)]), [legacy])
    Draft7Validator(package_resolution_lock_schema()).validate(mixed.to_dict())


def host_snapshot_for(env):
    from cpn.rpnh.collaboration.host_readiness import HostDeclarationSnapshot
    registrations = {kind: {} for kind in ("component", "executor", "tool", "analyzer", "schema")}
    for scoped in env.requirements:
        for row in scoped.host_requirements:
            if row["kind"] == "effect" or not row["required"]:
                continue
            kind = "tool" if row["kind"] == "terminal" else row["kind"]
            registrations[kind][row["contract_id"]] = {"kind": kind, "key": row["contract_id"],
                "identity": {"implementation_id": row["contract_id"], "revision": "v1"}, "contracts": {}}
    for preview in env.previews:
        for row in preview.manifest["artifacts"]:
            if row["role"] == "schema":
                schema = json.loads(dict(preview.artifacts)[row["path"]])
                registrations["schema"][schema["$id"]] = {"kind": "schema", "key": schema["$id"], "schema": schema}
    return HostDeclarationSnapshot(registrations)


def test_flat_host_presence_never_claims_author_implementation_or_permission():
    from cpn.rpnh.collaboration.host_readiness import diagnose_package_host_requirements, HostDeclarationSnapshot
    env = read_package_environment(v2())
    snapshot = host_snapshot_for(env)
    result = diagnose_package_host_requirements(env, snapshot).to_dict()
    assert result["declarations_status"] == "matched"
    assert result["author_implementation_identity"] == "not_supplied"
    assert result["contract_compatibility"] == "requires_compilation"
    assert result["execution_ready"] is False
    assert result["permission"] == "not_checked"
    assert all(row["registration_kind"] == "tool" for row in result["declaration_checks"] if row["kind"] == "terminal")
    assert diagnose_package_host_requirements(env).to_dict()["declarations_status"] == "not_checked"
    declarations = snapshot.registrations
    declarations["tool"].clear()
    assert diagnose_package_host_requirements(env, HostDeclarationSnapshot(declarations)).to_dict()["declarations_status"] == "missing"
    declarations = snapshot.registrations
    next(iter(declarations["schema"].values()))["schema"]["description"] = "changed"
    assert diagnose_package_host_requirements(env, HostDeclarationSnapshot(declarations)).to_dict()["declarations_status"] == "mismatch"
    declarations = snapshot.registrations
    first = next(iter(declarations["component"].values()))
    first["contracts"]["incompatible"] = True
    assert diagnose_package_host_requirements(env, HostDeclarationSnapshot(declarations),
        expected_declarations=snapshot.registrations).to_dict()["declarations_status"] == "mismatch"


def test_scoped_requirements_preserve_raw_bytes_and_reject_mutable_or_mismatched_values():
    from cpn.rpnh.collaboration.environment_requirements import ScopedEnvironmentRequirements, PackageEnvironment
    files = v2_material()
    files["environment/requirements.json"] += b"\n "
    env = read_package_environment(preview_package(archive(revise(files))))
    scoped = env.requirements[0]
    assert scoped.payload.endswith(b"\n ")
    assert sha256(scoped.payload) == scoped.artifact_digest
    with pytest.raises(PackageError, match="ENVIRONMENT_TARGET_MISMATCH"):
        replace(scoped, payload=scoped.payload + b" ")
    with pytest.raises(TypeError):
        replace(env, requirements=list(env.requirements))
    with pytest.raises(PackageError, match="ENVIRONMENT_TARGET_MISMATCH"):
        replace(env, requirements=())


def test_all_environment_categories_are_inert_scoped_and_closed():
    document = requirements()
    document["system_requirements"] = [
        {"requirement_id": "platform", "kind": "platform", "os_family": "linux", "architecture": "x86_64", "satisfies_host_requirement_ids": []},
        {"requirement_id": "socket", "kind": "capability", "capability_contract_id": "rpnh/af_unix/v1", "constraint": {"operator": "present", "value": True}, "satisfies_host_requirement_ids": []},
        {"requirement_id": "library", "kind": "os_package", "manager_contract_id": "test/manager/v1", "name": "libexample", "version_constraint": ">=1", "satisfies_host_requirement_ids": []}]
    document["tools"] = [{"requirement_id": "tool", "tool_contract_id": "test/tool/v1", "version_constraint": "protocol-specific-value", "required_capabilities": ["test/b", "test/a"], "satisfies_host_requirement_ids": []}]
    document["plugins"] = [{"requirement_id": "plugin", "plugin_id": "test/plugin", "api_contract": "test/api/v1", "version_specifier": ">=1,<2", "distribution_requirement_id": "harness", "satisfies_host_requirement_ids": ["run"]}]
    document["services"] = [{"requirement_id": "service", "service_contract_id": "test/service/v1", "required_capabilities": [], "model_constraint": {"kind": "exact", "model_condition": "fixed-model-version"}, "authentication_required": True, "satisfies_host_requirement_ids": []}]
    env = read_package_environment(v2(document=document))
    assert len(env.rows()) == 8
    assert env.requirements[0].document["tools"][0]["required_capabilities"] == ["test/a", "test/b"]
    for category, extra in [("tools", "executable"), ("plugins", "import_locator"), ("services", "profile_path")]:
        copy = json.loads(json.dumps(document)); copy[category][0][extra] = "/private/local"
        with pytest.raises(PackageError):
            v2(document=copy)
    document["services"][0]["model_constraint"]["model_condition"] = "latest"
    with pytest.raises(PackageError):
        v2(document=document)


def test_partial_author_expected_inventory_cannot_be_called_verified_identity():
    from cpn.rpnh.collaboration.host_readiness import diagnose_package_host_requirements
    env = read_package_environment(v2())
    with pytest.raises(ValueError, match="cover every required"):
        diagnose_package_host_requirements(env, host_snapshot_for(env), expected_declarations={})
