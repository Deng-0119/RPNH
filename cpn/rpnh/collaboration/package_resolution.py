"""Exact local package closure. No acquisition, installation or execution."""
from __future__ import annotations

from dataclasses import replace

from .package_preview import inspect_materials, preview_package, schema_checks
from .share_packages import (
    DEFAULT_LIMITS, LOCK_SCHEMA, MANIFEST_PATH, RESOLVER_CONTRACT,
    PackageError, PackagePreview, PackageResolutionLock, canonical_bytes,
    PACKAGE_SCHEMA, PACKAGE_SCHEMA_V2, LOCK_SCHEMA_V2, RESOLVER_CONTRACT_V2,
)


def _walk(root_digest, manifests, limits):
    """Deterministic dependency traversal, separately testable with graph data."""
    selected = {}
    visiting = []
    subtree_depths = {}
    edges = []

    def visit(digest):
        if digest in visiting:
            raise PackageError("DEPENDENCY_CYCLE", "exact package dependency cycle")
        if digest in subtree_depths:
            # A shared subtree can be reached by a longer path after its first
            # visit. Count its full height without traversing its edges again.
            if len(visiting) + subtree_depths[digest] > limits.dependency_depth:
                raise PackageError("PACKAGE_LIMIT_EXCEEDED", "dependency depth limit")
            return subtree_depths[digest]
        if len(visiting) >= limits.dependency_depth:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "dependency depth limit")
        manifest = manifests.get(digest)
        if manifest is None:
            raise PackageError("DEPENDENCY_UNRESOLVED", "exact manifest is absent from local packages")
        previous = selected.get(manifest["package_id"])
        if previous is not None and previous != digest:
            code = ("VERSION_CONTENT_REPLACED" if manifests[previous]["version"] == manifest["version"]
                    else "DEPENDENCY_VERSION_CONFLICT")
            raise PackageError(code, "one package identity selects different exact content")
        selected[manifest["package_id"]] = digest
        if len(selected) > limits.packages:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "selected package count limit")
        visiting.append(digest)
        subtree_depth = 1
        for dependency in sorted(manifest["dependencies"], key=lambda value: value["dependency_id"]):
            if dependency["manifest_digest"] is None or dependency["version_range"] is not None:
                raise PackageError("DEPENDENCY_RANGE_UNRESOLVED", "v1 requires an exact digest without a version range; no version is guessed")
            target = dependency["manifest_digest"]
            candidate = manifests.get(target)
            if candidate is None:
                raise PackageError("DEPENDENCY_UNRESOLVED", "exact dependency manifest is absent locally")
            if candidate["package_id"] != dependency["package_id"]:
                raise PackageError("DEPENDENCY_IDENTITY_MISMATCH", "exact dependency has a different package identity")
            edges.append({"from_manifest_digest": digest, "dependency_id": dependency["dependency_id"],
                          "to_manifest_digest": target, "required": True})
            if len(edges) > limits.dependency_edges:
                raise PackageError("PACKAGE_LIMIT_EXCEEDED", "dependency edge limit")
            subtree_depth = max(subtree_depth, 1 + visit(target))
        visiting.pop()
        subtree_depths[digest] = subtree_depth
        return subtree_depth

    visit(root_digest)
    return set(subtree_depths), sorted(edges, key=lambda row: (row["from_manifest_digest"], row["dependency_id"]))


def resolve_package(root, local_packages=(), root_entry_id="main", *, limits=DEFAULT_LIMITS) -> PackageResolutionLock:
    """Freeze the chosen root and all required dependencies from local ZIPs.

    Inputs can be local paths, ZIP bytes, or PackagePreview values. A preview is
    re-read from its immutable archive bytes, never trusted as an authority.
    Range selection, optional features, HOST availability and execution remain
    outside inert package resolution. Complete cross-document schema closure can be locked
    while runtime compatibility is explicitly incompatible.
    """
    def preview(value, remaining):
        if remaining <= 0:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "local package aggregate expanded byte limit")
        bounded = replace(limits, expanded_bytes=min(limits.expanded_bytes, remaining))
        return preview_package(value.archive_bytes if isinstance(value, PackagePreview) else value, limits=bounded)

    def expanded_size(value):
        return len(value.manifest_bytes) + sum(len(data) for _, data in value.artifacts)

    root_preview = preview(root, limits.closure_expanded_bytes)
    if root_entry_id != root_preview.manifest["entries"][0]["entry_id"]:
        raise PackageError("ENTRY_NOT_FOUND", "root entry is not declared")
    candidates = {root_preview.manifest_digest: root_preview}
    total = len(root_preview.archive_bytes)
    expanded = expanded_size(root_preview)
    count = 1
    for source in local_packages:
        count += 1
        if count > limits.packages:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "local package count limit")
        item = preview(source, limits.closure_expanded_bytes - expanded)
        expanded += expanded_size(item)
        total += len(item.archive_bytes)
        if total > limits.closure_bytes:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "local package aggregate byte limit")
        previous = candidates.get(item.manifest_digest)
        # Equal manifests may have differently compressed archives. Root's exact
        # provided archive stays pinned; other equivalents select deterministically.
        if item.manifest_digest != root_preview.manifest_digest and (
                previous is None or item.archive_digest < previous.archive_digest):
            candidates[item.manifest_digest] = item
    if total > limits.closure_bytes:
        raise PackageError("PACKAGE_LIMIT_EXCEEDED", "local package aggregate byte limit")
    manifests = {digest: item.manifest for digest, item in candidates.items()}
    selected, edges = _walk(root_preview.manifest_digest, manifests, limits)
    is_v2 = root_preview.manifest["schema_version"] == PACKAGE_SCHEMA_V2
    if not is_v2 and any(manifests[digest]["schema_version"] != PACKAGE_SCHEMA for digest in selected):
        raise PackageError("UNSUPPORTED_PACKAGE_SCHEMA", "v1 root cannot silently select v2 requirements")
    schemas = {}
    schema_owners = {}
    required = set()
    nodes = []
    for digest in sorted(selected):
        item = candidates[digest]
        members = {MANIFEST_PATH: item.manifest_bytes, **dict(item.artifacts)}
        manifest, local_schemas, local_required = inspect_materials(item.manifest_bytes, members, limits)
        required.update(local_required)
        for identity, row in local_schemas.items():
            if identity in schemas and schemas[identity][1] != row[1]:
                raise PackageError("SCHEMA_ID_CONTENT_CONFLICT", "schema identity binds different material bytes")
            # Stable owner choice for identical shared schema material.
            if identity not in schemas:
                schemas[identity] = row
                schema_owners[identity] = digest
        nodes.append({"package_id": manifest["package_id"], "version": manifest["version"],
            "manifest_digest": digest, "archive_digest": item.archive_digest,
            "artifacts": [{"path": row["path"], "sha256": row["sha256"], "bytes": row["bytes"]}
                          for row in sorted(manifest["artifacts"], key=lambda row: row["path"])],
            "origin": manifest["origin"], "provenance": manifest["provenance"]})
        if is_v2:
            nodes[-1]["manifest_schema"] = manifest["schema_version"]
            nodes[-1]["environment_requirements"] = ([{
                "entry_id": entry["entry_id"], "artifact_path": entry["environment_requirements_path"],
                "artifact_digest": next(row["sha256"] for row in manifest["artifacts"]
                                        if row["path"] == entry["environment_requirements_path"]),
                "schema_version": "rpnh/environment_requirements/v1",
            } for entry in manifest["entries"]] if manifest["schema_version"] == PACKAGE_SCHEMA_V2
                else "not_declared")
    checks, schema_rows = schema_checks(schemas, required)
    closure = next(row for row in checks if row["check_id"] == "package_schema_closure_resolved")
    if closure["status"] != "satisfied":
        raise PackageError(closure["code"], "selected schema closure is not completely interpretable offline")
    for row in schema_rows:
        row["manifest_digest"] = schema_owners[row["schema_id"]]
    checks.extend([
        {"check_id": "host_readiness", "status": "not_checked", "code": "HOST_PREPARATION_REQUIRED"},
        {"check_id": "license_permissions", "status": "not_checked", "code": "LICENSE_REVIEW_REQUIRED"},
        {"check_id": "origin_authority", "status": "not_checked", "code": "ORIGIN_UNVERIFIED"},
        {"check_id": "revocation", "status": "not_checked", "code": "REVOCATION_UNKNOWN"},
    ])
    document = {"schema_version": LOCK_SCHEMA_V2 if is_v2 else LOCK_SCHEMA,
        "resolver_contract": RESOLVER_CONTRACT_V2 if is_v2 else RESOLVER_CONTRACT,
        "root_manifest_digest": root_preview.manifest_digest, "root_entry_id": root_entry_id,
        "nodes": sorted(nodes, key=lambda row: row["package_id"]), "edges": edges,
        "selected_features": [], "schema_closure": schema_rows,
        "compatibility_results": checks, "execution_permitted": False}
    return PackageResolutionLock(canonical_bytes(document))
