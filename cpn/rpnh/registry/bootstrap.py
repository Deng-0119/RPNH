"""Exact fresh Registry identity publication, independent of default execution.

This creates no firing or terminal authority. Existing lineage/reference and
single writer transaction checks remain the same as the active launch producer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ._registry import _RegistryCore
from .errors import IncompleteNativeRun
from .identities import TypedId, new_id
from .models import VersionRef
from .schema_catalog import canonical_json
from .publication import _ref_payload

@dataclass(frozen=True, slots=True)
class NativeBootstrapManifest:
    protocol_versions: tuple[str, ...]
    branch_id: str = "main"

    def __post_init__(self) -> None:
        if not self.protocol_versions or len(set(self.protocol_versions)) != len(
                self.protocol_versions):
            raise ValueError("native bootstrap must pin unique protocol versions")
        if not self.branch_id:
            raise ValueError("native bootstrap branch cannot be empty")


@dataclass(frozen=True, slots=True)
class NativeRunIdentity:
    run_ref: VersionRef
    task_ref: VersionRef
    task_branch_ref: VersionRef
    genesis_manifest_ref: VersionRef
    branch_id: str
    protocol_versions: tuple[str, ...]


def _bootstrap_identity(
        core: _RegistryCore, manifest: NativeBootstrapManifest, *,
        purpose: str = "create D1-C greenfield native authority",
        exact_task_ref: VersionRef | None = None,
        exact_task_branch_ref: VersionRef | None = None,
        project_identity_objects: tuple[object, ...] = (),
) -> NativeRunIdentity:
    catalog_rows = [
        row for row in core.event_store.object_rows()
        if row["object_type"] == "registry_type_catalog/v1"
    ]
    if len(catalog_rows) != 1:
        raise IncompleteNativeRun("native schema catalog publication is incomplete")
    catalog_ref = VersionRef(
        "registry_type_catalog/v1",
        TypedId.parse(catalog_rows[0]["logical_id"], expected="schema"),
        TypedId.parse(catalog_rows[0]["version_id"], expected="resource_version"))
    task_version = new_id("task_version")
    task_ref = (exact_task_ref or VersionRef(
        "task/v1", core.task_id, task_version))
    branch_id = new_id("task_branch")
    branch_version = new_id("task_branch_version")
    branch_ref = (exact_task_branch_ref or VersionRef(
        "task_branch/v1", branch_id, branch_version))
    if (task_ref.entity_type != "task/v1"
            or task_ref.entity_id != core.task_id
            or branch_ref.entity_type != "task_branch/v1"
            or (exact_task_branch_ref is not None
                and exact_task_ref is None)
            or bool(project_identity_objects) != bool(exact_task_ref)):
        raise TypeError("project bootstrap identity override is incomplete")
    run_id = new_id("run")
    run_version = new_id("run_version")
    run_ref = VersionRef("native_run_identity/v1", run_id, run_version)
    bootstrap_id = new_id("bootstrap_command")
    bootstrap_version = new_id("bootstrap_command_version")
    bootstrap_ref = VersionRef(
        "bootstrap_command/v1", bootstrap_id, bootstrap_version)
    genesis_id = new_id("native_genesis")
    genesis_version = new_id("native_genesis_version")
    genesis_ref = VersionRef(
        "native_genesis_manifest/v1", genesis_id, genesis_version)
    tx = core.begin(idempotency_key=f"native-bootstrap:{run_id}")
    task_meta = {"task_id": str(core.task_id), "task_version_id": str(task_version)}
    branch_meta = {
        "task_branch_id": str(branch_id),
        "task_branch_version_id": str(branch_version),
        "task_ref": _ref_payload(task_ref), "branch_name": manifest.branch_id,
    }
    bootstrap_meta = {
        "bootstrap_command_id": str(bootstrap_id),
        "bootstrap_command_version_id": str(bootstrap_version),
        "purpose": purpose,
    }
    run_meta = {
        "run_id": str(run_id), "run_version_id": str(run_version),
        "task_ref": _ref_payload(task_ref),
        "task_branch_ref": _ref_payload(branch_ref),
        "branch_id": manifest.branch_id,
        "protocol_versions": list(manifest.protocol_versions),
    }
    genesis_meta = {
        "genesis_id": str(genesis_id),
        "genesis_version_id": str(genesis_version),
        "run_identity_ref": _ref_payload(run_ref),
        "type_catalog_ref": _ref_payload(catalog_ref),
        "bootstrap_transaction_id": str(tx.transaction_id),
        "format": "d1-c-greenfield/v1",
    }
    identity_rows = list((
        ("bootstrap_command/v1", bootstrap_id, bootstrap_version, bootstrap_meta),
        ("native_run_identity/v1", run_id, run_version, run_meta),
        ("native_genesis_manifest/v1", genesis_id, genesis_version, genesis_meta),
    ))
    if exact_task_ref is None:
        identity_rows.insert(
            0, ("task/v1", core.task_id, task_version, task_meta))
    if exact_task_branch_ref is None:
        identity_rows.insert(
            1 if exact_task_ref is None else 0,
            ("task_branch/v1", branch_id, branch_version, branch_meta))
    expected_project_refs = {
        ref for ref in (exact_task_ref, exact_task_branch_ref)
        if ref is not None}
    if {getattr(item, "source_ref", None)
            for item in project_identity_objects} != expected_project_refs:
        raise TypeError("project bootstrap identity objects are incomplete")
    for item in project_identity_objects:
        if (getattr(item, "source_ref", None) not in expected_project_refs
                or getattr(item, "object_type", None)
                != getattr(item, "source_ref").entity_type):
            raise TypeError("project bootstrap identity object is foreign")
        tx.prewrite(
            object_type=item.object_type,
            logical_id=item.source_ref.entity_id,
            version_id=item.source_ref.version_id,
            payload=item.payload,
            metadata=item.metadata,
            media_type=item.media_type,
            schema_ref=item.schema_ref,
            producer_invocation_id=item.producer_invocation_id,
        )
    for object_type, logical_id, version_id, metadata in identity_rows:
        tx.prewrite(
            object_type=object_type, logical_id=logical_id, version_id=version_id,
            payload=canonical_json(metadata), metadata=metadata,
            media_type="application/json", schema_ref=f"registry_v1/{object_type}")
    tx.commit()
    core.event_store.get_or_create_meta("native_run_ref", json.dumps(_ref_payload(run_ref)))
    core.event_store.get_or_create_meta(
        "native_genesis_ref", json.dumps(_ref_payload(genesis_ref)))
    core.event_store.get_or_create_meta(
        "bootstrap_command_ref", json.dumps(_ref_payload(bootstrap_ref)))
    core.event_store.get_or_create_meta("task_ref", json.dumps(_ref_payload(task_ref)))
    core.event_store.get_or_create_meta(
        "task_branch_ref", json.dumps(_ref_payload(branch_ref)))
    return NativeRunIdentity(
        run_ref, task_ref, branch_ref, genesis_ref, manifest.branch_id,
        manifest.protocol_versions)
