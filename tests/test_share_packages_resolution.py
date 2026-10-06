"""Exact local dependency closure, conflicting content and graph limits."""
import json
from dataclasses import replace

import pytest

from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import _walk, resolve_package
from cpn.rpnh.collaboration.share_packages import DEFAULT_LIMITS, PackageError, canonical_bytes, sha256
from test_share_packages import archive, check, material, revise


def dependency(preview, name="dep"):
    return {"dependency_id": name, "package_id": preview.package_id,
            "manifest_digest": preview.manifest_digest, "version_range": None,
            "required": True, "acquisition_hint": None}


def package(name, dependencies=(), *, version="1.0.0", documents=None):
    files = revise(material(), documents=documents, manifest=lambda m: m.update(
        package_id=name, version=version, dependencies=list(dependencies)))
    return preview_package(archive(files))


def test_exact_dependency_and_diamond_lock_are_order_independent():
    leaf = package("example/leaf")
    left = package("example/left", [dependency(leaf)])
    right = package("example/right", [dependency(leaf)])
    root = package("example/root", [dependency(right, "right"), dependency(left, "left")])
    first = resolve_package(root, [leaf, left, right])
    second = resolve_package(root, [right, left, leaf])
    assert first.to_bytes() == second.to_bytes()
    assert len(first.to_dict()["nodes"]) == 4
    assert len(first.to_dict()["edges"]) == 4
    assert all(node["archive_digest"] for node in first.to_dict()["nodes"])
    assert all("origin" in node and "provenance" in node for node in first.to_dict()["nodes"])


def test_missing_dependency_and_wrong_exact_identity_fail():
    leaf = package("example/leaf")
    root = package("example/root", [dependency(leaf)])
    with pytest.raises(PackageError, match="DEPENDENCY_UNRESOLVED"):
        resolve_package(root)
    selected = dependency(leaf); selected["package_id"] = "example/other"
    wrong = package("example/root", [selected])
    with pytest.raises(PackageError, match="DEPENDENCY_IDENTITY_MISMATCH"):
        resolve_package(wrong, [leaf])


@pytest.mark.parametrize("pinned", [False, True])
def test_range_only_or_exact_plus_unsupported_range_are_unresolved(pinned):
    leaf = package("example/leaf")
    selected = dependency(leaf)
    selected.update(version_range=">=1.0.0 <2.0.0", manifest_digest=leaf.manifest_digest if pinned else None)
    root = package("example/root", [selected])
    assert root.to_dict()["dependencies"][0]["version_range"] is not None
    with pytest.raises(PackageError, match="DEPENDENCY_RANGE_UNRESOLVED"):
        resolve_package(root, [leaf])


@pytest.mark.parametrize("other_version,code", [("1.0.0", "VERSION_CONTENT_REPLACED"), ("2.0.0", "DEPENDENCY_VERSION_CONFLICT")])
def test_same_package_different_content_or_versions_conflict(other_version, code):
    first = package("example/shared")
    changed = json.loads(material()["declarations/main.json"]); changed["name"] = "Other"
    second = package("example/shared", version=other_version, documents={"declarations/main.json": changed})
    left = package("example/left", [dependency(first)])
    right = package("example/right", [dependency(second)])
    root = package("example/root", [dependency(left, "left"), dependency(right, "right")])
    with pytest.raises(PackageError, match=code):
        resolve_package(root, [left, right, first, second])


def test_graph_cycle_guard_and_path_depth_are_independent_of_digest_acquisition():
    # A cryptographic self-referential ZIP is not fabricated here. Test traversal
    # directly with synthetic exact-node labels, never treat these as ZIP proof.
    a = {"package_id": "example/a", "version": "1.0.0", "dependencies": [
        {"dependency_id": "b", "package_id": "example/b", "manifest_digest": "B", "version_range": None}]}
    b = {"package_id": "example/b", "version": "1.0.0", "dependencies": [
        {"dependency_id": "a", "package_id": "example/a", "manifest_digest": "A", "version_range": None}]}
    with pytest.raises(PackageError, match="DEPENDENCY_CYCLE"):
        _walk("A", {"A": a, "B": b}, DEFAULT_LIMITS)
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        _walk("A", {"A": a, "B": b}, replace(DEFAULT_LIMITS, dependency_depth=1))


def test_resolver_package_total_depth_and_edge_limits():
    a = package("example/a")
    b = package("example/b", [dependency(a)])
    root = package("example/root", [dependency(a, "a"), dependency(b, "b")])
    for limits in [replace(DEFAULT_LIMITS, packages=1), replace(DEFAULT_LIMITS, closure_bytes=10),
                   replace(DEFAULT_LIMITS, dependency_edges=1), replace(DEFAULT_LIMITS, dependency_depth=1)]:
        with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
            resolve_package(root, [a, b], limits=limits)


@pytest.mark.parametrize("shared_has_child", [False, True])
@pytest.mark.parametrize("short_path_first", [False, True])
def test_depth_limit_covers_every_path_through_shared_dependencies(shared_has_child, short_path_first):
    leaf = package("example/leaf")
    shared = package("example/shared", [dependency(leaf)] if shared_has_child else [])
    branch = package("example/branch", [dependency(shared)])
    root = package("example/root", [
        dependency(shared, "a" if short_path_first else "z"),
        dependency(branch, "z" if short_path_first else "a"),
    ])
    candidates = [shared, branch, leaf]
    longest_path = 4 if shared_has_child else 3
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        resolve_package(root, candidates, limits=replace(DEFAULT_LIMITS, dependency_depth=longest_path - 1))
    lock = resolve_package(root, candidates, limits=replace(DEFAULT_LIMITS, dependency_depth=longest_path))
    assert len(lock.to_dict()["nodes"]) == longest_path


def test_same_schema_id_different_raw_bytes_is_conflict():
    schema = json.loads(material()["schemas/text.json"]); schema["description"] = "New bytes"
    leaf = package("example/leaf", documents={"schemas/text.json": schema})
    root = package("example/root", [dependency(leaf)])
    with pytest.raises(PackageError, match="SCHEMA_ID_CONTENT_CONFLICT"):
        resolve_package(root, [leaf])


def test_schema_material_supplied_by_exact_dependency_closes_cross_package_ref():
    files = material()
    extra = canonical_bytes({"$id": "application/dependency_text/v1", "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    files["schemas/dependency.json"] = extra

    def add_manifest(m):
        m["package_id"] = "example/leaf"
        m["artifacts"].append({"path": "schemas/dependency.json", "role": "schema", "media_type": "application/schema+json", "bytes": len(extra), "sha256": sha256(extra), "license_id": "package-license", "disclosure": "public"})
        m["provenance"].append({"artifact_path": "schemas/dependency.json", "relation": "authored", "origin_ref": None, "origin_digest": None})

    leaf = preview_package(archive(revise(files, manifest=add_manifest)))
    schema = json.loads(material()["schemas/text.json"]); schema["$ref"] = "application/dependency_text/v1"
    # Root and leaf cannot bind the same schema ID to different bytes. Give the
    # leaf the exact root schema too; its referenced schema is present locally.
    leaf_files = dict(leaf.artifacts); leaf_files["manifest.json"] = leaf.manifest_bytes
    leaf = preview_package(archive(revise(leaf_files, documents={"schemas/text.json": schema})))
    root = package("example/root", [dependency(leaf)], documents={"schemas/text.json": schema})
    assert check(root.to_dict(), "package_schema_closure_resolved")[0]["status"] == "missing"
    lock = resolve_package(root, [leaf]).to_dict()
    assert next(row for row in lock["compatibility_results"] if row["check_id"] == "package_schema_closure_resolved")["status"] == "satisfied"
    assert next(row for row in lock["compatibility_results"] if row["check_id"] == "runtime_schema_authority_supported")["status"] == "incompatible"


def test_provenance_is_preserved_without_remapping_source_or_digest():
    original = {"schema_version": "rpnh/collaboration/source_version_ref/v1", "source_id": "external-source",
                "ref": {"entity_type": "resource_version/v1", "logical_id": "original-resource", "version_id": "original-version"}}
    files = revise(material(), manifest=lambda m: (
        m["origin"].update(source_refs=[original]),
        m["provenance"][0].update(relation="derived", origin_ref=original, origin_digest="f" * 64)))
    root = preview_package(archive(files))
    expected = root.manifest["provenance"]
    assert resolve_package(root).to_dict()["nodes"][0]["provenance"] == expected
    assert expected[0]["origin_ref"]["source_id"] == "external-source"
    assert expected[0]["origin_digest"] == "f" * 64


def test_forged_preview_report_is_not_an_authority():
    preview = package("example/root")
    altered = replace(preview, report_bytes=b'{"execution_permitted":true}', manifest_bytes=b'{}')
    assert resolve_package(altered).to_bytes() == resolve_package(preview).to_bytes()


def test_aggregate_expanded_bytes_bound_even_unused_compressible_candidates():
    import zipfile
    # Unique JSON annotation adds highly compressible but bounded material. The
    # archives together fit 30 KiB; expanded candidates exceed 30 KiB.
    extras = []
    for index in range(4):
        schema = json.loads(material()["schemas/text.json"])
        schema["description"] = ("abcdefghij" * 800) + str(index)
        files = revise(material(), documents={"schemas/text.json": schema},
                       manifest=lambda m, index=index: m.update(package_id=f"example/unused-{index}"))
        extras.append(archive(files, compression=zipfile.ZIP_DEFLATED))
    root = archive(material(), compression=zipfile.ZIP_DEFLATED)
    assert sum(map(len, [root, *extras])) < 30_000
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        resolve_package(root, extras, limits=replace(DEFAULT_LIMITS,
            closure_bytes=30_000, closure_expanded_bytes=30_000))
