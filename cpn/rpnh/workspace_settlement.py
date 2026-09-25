"""Prepare one firing-private workspace revision for ordinary Success.

The executor has already stopped mutating the private tree.  This module only
captures regular files, compares them with the immutable admission revision,
and returns data for the Registry's existing single-transaction Success seam.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

from .registry.errors import ResourceIntegrityFault
from .registry.identities import TypedId
from .registry.models import TypedRelation, VersionRef
from .registry.publication import (
    _ref_payload,
    _stable_id,
    _version_from_payload,
)
from .registry.resource_service import (
    _resolve_registry_workspace_root,
    _resource_payload,
)
from .registry.schema_catalog import canonical_json


def _strict_workspace_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] == "registered_resources"):
        raise ResourceIntegrityFault(
            "workspace revision contains an invalid relative path")
    return path


def _tree_files(root: Path) -> dict[str, tuple[bytes, int]]:
    files: dict[str, tuple[bytes, int]] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        directories[:] = [
            name for name in directories
            if not (current_path / name).is_symlink()
            and not (relative_dir == Path(".")
                     and name == "registered_resources")
        ]
        for name in names:
            candidate = current_path / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            relative = _strict_workspace_path(
                candidate.relative_to(root).as_posix()).as_posix()
            mode = stat.S_IMODE(candidate.stat().st_mode)
            files[relative] = (candidate.read_bytes(), mode)
    return files


def _restore_tree(
        root: Path, files: dict[str, tuple[bytes, int]],
) -> None:
    """Restore one firing-private tree, preserving Registry projections."""

    if not isinstance(root, Path) or not root.is_dir():
        raise ResourceIntegrityFault(
            "workspace restoration requires its exact private root")
    for relative in files:
        _strict_workspace_path(relative)
    for current, directories, names in os.walk(
            root, topdown=False, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        if (relative_dir == Path(".")
                and "registered_resources" in directories):
            directories.remove("registered_resources")
        if relative_dir.parts[:1] == ("registered_resources",):
            continue
        for name in names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink() or relative not in files:
                candidate.unlink(missing_ok=True)
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                candidate.unlink(missing_ok=True)
                continue
            try:
                candidate.rmdir()
            except OSError:
                pass
    for relative, (payload, mode) in files.items():
        target = root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        target.chmod(mode)


def _archive_files(core, revision_ref: VersionRef) -> dict[str, tuple[bytes, int]]:
    prepared = core.get_version(revision_ref.version_id)
    if (prepared.object_type != "workspace_revision/v1"
            or prepared.logical_id != revision_ref.entity_id
            or prepared.media_type != "application/x-tar"):
        raise ResourceIntegrityFault(
            "workspace base revision is not one exact full archive")
    payload = core.object_store.read_registered(prepared)
    files: dict[str, tuple[bytes, int]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ResourceIntegrityFault(
                        "workspace archive contains a non-regular member")
                relative = _strict_workspace_path(member.name).as_posix()
                if relative in files:
                    raise ResourceIntegrityFault(
                        "workspace archive repeats one path")
                source = archive.extractfile(member)
                if source is None:
                    raise ResourceIntegrityFault(
                        "workspace archive member has no payload")
                files[relative] = (
                    source.read(), stat.S_IMODE(member.mode))
    except (tarfile.TarError, OSError) as exc:
        raise ResourceIntegrityFault(
            "workspace base revision archive is malformed") from exc
    return files


def _full_archive(files: dict[str, tuple[bytes, int]]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative, (payload, mode) in sorted(files.items()):
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.mode = mode
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _revision_metadata(core, revision_ref: VersionRef) -> dict:
    prepared = core.get_version(revision_ref.version_id)
    if (prepared.object_type != "workspace_revision/v1"
            or prepared.logical_id != revision_ref.entity_id):
        raise ResourceIntegrityFault(
            "workspace revision differs from its exact lineage")
    return dict(prepared.metadata)


def _workspace_head_ref(core, lineage_ref: VersionRef) -> VersionRef:
    head = core.event_store.workspace_lineage_head(lineage_ref.entity_id)
    if head is None:
        raise ResourceIntegrityFault("workspace lineage has no settled head")
    return VersionRef(
        "workspace_revision/v1", lineage_ref.entity_id,
        TypedId.parse(
            str(head["workspace_revision_version_id"]),
            expected="workspace_revision"),
    )


def _intervening_paths(
        core, base_ref: VersionRef, head_ref: VersionRef,
) -> set[str]:
    """Return every path changed after ``base_ref`` on the parent chain."""

    touched: set[str] = set()
    cursor = head_ref
    seen: set[VersionRef] = set()
    while cursor != base_ref:
        if cursor in seen:
            raise ResourceIntegrityFault(
                "workspace revision parent chain contains a cycle")
        seen.add(cursor)
        metadata = _revision_metadata(core, cursor)
        touched.update(str(path) for path in metadata["changed_paths"])
        touched.update(str(path) for path in metadata["deleted_paths"])
        parent = metadata.get("parent_revision_ref")
        if not isinstance(parent, dict):
            raise ResourceIntegrityFault(
                "workspace firing base is not an ancestor of the head")
        cursor = _version_from_payload(parent)
    return touched


def _overlapping_paths(
        firing_paths: set[str], intervening_paths: set[str],
) -> tuple[str, ...]:
    conflicts: set[str] = set()
    for firing_path in firing_paths:
        for intervening_path in intervening_paths:
            if (firing_path == intervening_path
                    or firing_path.startswith(intervening_path + "/")
                    or intervening_path.startswith(firing_path + "/")):
                conflicts.update((firing_path, intervening_path))
    return tuple(sorted(conflicts))


def _convergent_delta(
        changed_paths: list[str], deleted_paths: list[str],
        firing_files: dict[str, tuple[bytes, int]],
        head_files: dict[str, tuple[bytes, int]],
        intervening_paths: set[str],
) -> tuple[list[str], list[str]]:
    """Drop exact same-state sibling changes that already reached the head."""

    def structurally_overlaps(path: str) -> bool:
        return any(
            other != path
            and (path.startswith(other + "/")
                 or other.startswith(path + "/"))
            for other in intervening_paths)

    changed = [
        path for path in changed_paths
        if (head_files.get(path) != firing_files[path]
            or structurally_overlaps(path))
    ]
    deleted = [
        path for path in deleted_paths
        if (path in head_files or structurally_overlaps(path))
    ]
    return changed, deleted


def _publish_conflict_evidence(
        core, *, context, view_ref: VersionRef, base_ref: VersionRef,
        head_ref: VersionRef, lineage_ref: VersionRef,
        changed_paths: list[str], deleted_paths: list[str],
        conflict_paths: tuple[str, ...], idempotency_key: str,
) -> VersionRef:
    conflict_ref = VersionRef(
        "workspace_revision/v1", lineage_ref.entity_id,
        _stable_id(
            "workspace_revision", idempotency_key,
            context.own_transition_firing_ref.version_id, "conflict"),
    )
    lineage = _revision_metadata(core, lineage_ref)
    metadata = {
        "workspace_lineage_id": str(conflict_ref.entity_id),
        "workspace_revision_id": str(conflict_ref.version_id),
        "workspace_revision_ref": _ref_payload(conflict_ref),
        "run_ref": lineage["run_ref"],
        "task_ref": _ref_payload(context.task_ref),
        "net_instance_ref": _ref_payload(context.net_instance_ref),
        "parent_revision_ref": _ref_payload(head_ref),
        "base_revision_ref": _ref_payload(base_ref),
        "producer_invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(
            context.own_transition_firing_ref),
        "firing_workspace_binding_ref": _ref_payload(view_ref),
        "disposition": "conflict",
        "changed_paths": changed_paths,
        "deleted_paths": deleted_paths,
        "inventory_paths": [],
        "conflict_paths": list(conflict_paths),
        "semantic_output_refs": [],
        "trace_summary_refs": [],
        "payload_kind": "conflict_evidence",
        "settled": False,
    }
    core.catalog.validate_instance(
        "workspace_revision/v1", category="object", instance=metadata)
    tx = core.begin(idempotency_key=f"{idempotency_key}:workspace-conflict")
    tx.expect_current_stream_head(f"object:{conflict_ref.entity_id}")
    tx.prewrite(
        object_type="workspace_revision/v1",
        logical_id=conflict_ref.entity_id,
        version_id=conflict_ref.version_id,
        payload=canonical_json(metadata), metadata=metadata,
        media_type="application/json",
        schema_ref="registry_v1/workspace_revision/v1",
        producer_invocation_id=context.invocation_ref.entity_id,
    )
    tx.relate(TypedRelation(
        _stable_id(
            "relation", idempotency_key, "workspace-conflict-head"),
        "derived_from", conflict_ref, head_ref),
        producer_invocation_id=context.invocation_ref.entity_id)
    if base_ref != head_ref:
        tx.relate(TypedRelation(
            _stable_id(
                "relation", idempotency_key, "workspace-conflict-base"),
            "derived_from", conflict_ref, base_ref),
            producer_invocation_id=context.invocation_ref.entity_id)
    tx.commit()
    return conflict_ref


def _firing_workspace_view(core, kernel, context) -> tuple[VersionRef, dict]:
    matches = []
    for row in core.event_store.object_rows_by_type("workspace_binding/v1"):
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("binding_kind") == "firing_view"
                and metadata.get("invocation_ref")
                == _ref_payload(context.invocation_ref)
                and metadata.get("transition_firing_ref")
                == _ref_payload(context.own_transition_firing_ref)):
            ref = VersionRef(
                "workspace_binding/v1",
                TypedId.parse(
                    str(row["logical_id"]), expected="workspace_binding"),
                TypedId.parse(
                    str(row["version_id"]),
                    expected="workspace_binding_version"),
            )
            exact = kernel._exact_object(
                ref, expected_type="workspace_binding/v1")
            matches.append((ref, dict(exact.metadata)))
    if len(matches) != 1:
        raise ResourceIntegrityFault(
            "completed firing lacks one exact private workspace view")
    return matches[0]


def prepare_firing_workspace_plans(
        core, kernel, operation_outputs, *, idempotency_key: str,
) -> tuple[dict, ...]:
    """Return zero or one workspace plans for the existing Success transaction."""

    execution = operation_outputs.execution
    context = execution.operation.canonical.context
    binding = kernel._exact_object(
        context.operation_binding_ref,
        expected_type="operation_binding/v1").metadata
    if not isinstance(binding.get("workspace_binding_ref"), dict):
        return ()
    view_ref, view = _firing_workspace_view(core, kernel, context)
    base_ref = _version_from_payload(view["base_revision_ref"])
    lineage_ref = _version_from_payload(view["workspace_lineage_ref"])
    if (base_ref.entity_type != "workspace_revision/v1"
            or lineage_ref.entity_type != "workspace_revision/v1"
            or base_ref.entity_id != lineage_ref.entity_id):
        raise ResourceIntegrityFault(
            "workspace firing view crosses its registered lineage")
    expected_head_ref = _workspace_head_ref(core, lineage_ref)

    root = _resolve_registry_workspace_root(core, view["allowed_root"])
    files = _tree_files(root)
    base_files = _archive_files(core, base_ref)
    changed_paths = sorted(
        path for path, value in files.items()
        if base_files.get(path) != value)
    deleted_paths = sorted(set(base_files) - set(files))
    interrupted = operation_outputs.selected_outcome_id == "interrupted"
    if interrupted and not changed_paths and not deleted_paths:
        return ()
    disposition = "committed"
    merged_files = dict(files)
    if expected_head_ref != base_ref:
        intervening = _intervening_paths(
            core, base_ref, expected_head_ref)
        head_files = _archive_files(core, expected_head_ref)
        changed_paths, deleted_paths = _convergent_delta(
            changed_paths, deleted_paths, files, head_files, intervening)
        conflict_paths = _overlapping_paths(
            set(changed_paths) | set(deleted_paths), intervening)
        if conflict_paths:
            from cpn.components.agent_loop.models import (
                WorkspaceRevisionConflict,
            )
            conflict_ref = _publish_conflict_evidence(
                core, context=context, view_ref=view_ref,
                base_ref=base_ref, head_ref=expected_head_ref,
                lineage_ref=lineage_ref,
                changed_paths=changed_paths,
                deleted_paths=deleted_paths,
                conflict_paths=conflict_paths,
                idempotency_key=idempotency_key,
            )
            raise WorkspaceRevisionConflict(
                conflict_ref, conflict_paths)
        merged_files = dict(head_files)
        for path in deleted_paths:
            merged_files.pop(path, None)
        for path in changed_paths:
            merged_files[path] = files[path]
        disposition = "merged"
    final_ref = VersionRef(
        "workspace_revision/v1", lineage_ref.entity_id,
        _stable_id(
            "workspace_revision", idempotency_key,
            context.own_transition_firing_ref.version_id),
    )
    base = kernel._exact_object(
        base_ref, expected_type="workspace_revision/v1").metadata
    metadata = {
        "workspace_lineage_id": str(final_ref.entity_id),
        "workspace_revision_id": str(final_ref.version_id),
        "workspace_revision_ref": _ref_payload(final_ref),
        "run_ref": base["run_ref"],
        "task_ref": _ref_payload(context.task_ref),
        "net_instance_ref": _ref_payload(context.net_instance_ref),
        "parent_revision_ref": _ref_payload(expected_head_ref),
        "base_revision_ref": _ref_payload(base_ref),
        "producer_invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(
            context.own_transition_firing_ref),
        "firing_workspace_binding_ref": _ref_payload(view_ref),
        "disposition": disposition,
        "changed_paths": changed_paths,
        "deleted_paths": deleted_paths,
        "inventory_paths": sorted(merged_files),
        "conflict_paths": [],
        "semantic_output_refs": [
            _resource_payload(item.resource_ref)
            for item in operation_outputs.outputs
        ] if not interrupted else [],
        "trace_summary_refs": [],
        "payload_kind": "full_workspace_tar",
        "settled": True,
    }
    core.catalog.validate_instance(
        "workspace_revision/v1", category="object", instance=metadata)
    return ({
        "final_ref": final_ref,
        "expected_head_ref": expected_head_ref,
        "final_metadata": metadata,
        "final_payload": _full_archive(merged_files),
    },)


__all__ = ("prepare_firing_workspace_plans",)
