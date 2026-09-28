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
from .registry._registry import RegistryReadError
from .registry.identities import TypedId
from .registry.models import TypedRelation, VersionRef
from .registry.publication import (
    _ref_payload,
    _resource_from_payload,
    _stable_id,
    _version_from_payload,
)
from .registry.resource_service import (
    _resolve_registry_workspace_root,
    _resource_payload,
)
from .registry.resources import ResourceVersionRef
from .registry.schema_catalog import canonical_json


_MAX_PATH_DELTA_SUMMARY_CHARS = 600


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


def _archive_payload_files(payload: bytes) -> dict[str, tuple[bytes, int]]:
    """Decode one immutable full-workspace archive without ambient state."""

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
            "workspace archive is malformed") from exc
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


def _exact_workspace_resource(
        core, resource_ref: ResourceVersionRef, path: str,
) -> tuple[bytes, str]:
    """Resolve one canonical workspace resource and its bounded summary."""

    prepared = core.get_version(resource_ref.resource_version_id)
    metadata = prepared.metadata
    if (prepared.object_type != "resource_version/v1"
            or prepared.logical_id != resource_ref.resource_id
            or core.event_store.canonical_object_row(
                resource_ref.resource_version_id) is None
            or metadata.get("origin_kind") != "workspace_write"
            or metadata.get("descriptors", {}).get("workspace_path") != path):
        raise ResourceIntegrityFault(
            "workspace history ref is not one exact accepted path resource")
    summary = metadata.get("summary")
    if (not isinstance(summary, str)
            or len(summary) > _MAX_PATH_DELTA_SUMMARY_CHARS):
        raise ResourceIntegrityFault(
            "workspace history resource summary is not bounded text")
    return core.object_store.read_registered(prepared), summary


def _workspace_resource_state(
        core, revision_ref: VersionRef,
) -> dict[str, ResourceVersionRef]:
    """Replay canonical deltas into the exact path-resource state at a head."""

    revisions: list[dict] = []
    cursor: VersionRef | None = revision_ref
    seen: set[VersionRef] = set()
    while cursor is not None:
        if cursor in seen:
            raise ResourceIntegrityFault(
                "workspace revision parent chain contains a cycle")
        seen.add(cursor)
        revision = _revision_metadata(core, cursor)
        revisions.append(revision)
        parent = revision.get("parent_revision_ref")
        cursor = (
            _version_from_payload(parent)
            if isinstance(parent, dict) else None)
    revisions.reverse()

    resources: dict[str, ResourceVersionRef] = {}
    for revision in revisions:
        deltas = revision.get("path_deltas")
        if not isinstance(deltas, list):
            raise ResourceIntegrityFault(
                "workspace revision lacks canonical path deltas")
        paths = [item.get("path") for item in deltas
                 if isinstance(item, dict)]
        if (len(paths) != len(deltas)
                or paths != sorted(paths)
                or len(set(paths)) != len(paths)):
            raise ResourceIntegrityFault(
                "workspace revision path deltas are not canonical")
        for delta in deltas:
            path = _strict_workspace_path(delta["path"]).as_posix()
            kind = delta.get("change_kind")
            before_raw = delta.get("before_resource_ref")
            after_raw = delta.get("after_resource_ref")
            before = (
                _resource_from_payload(before_raw)
                if isinstance(before_raw, dict) else None)
            after = (
                _resource_from_payload(after_raw)
                if isinstance(after_raw, dict) else None)
            if resources.get(path) != before:
                raise ResourceIntegrityFault(
                    "workspace path delta does not continue exact provenance")
            if kind == "create" and before is None and after is not None:
                resources[path] = after
            elif kind == "update" and before is not None and after is not None:
                resources[path] = after
            elif kind == "delete" and before is not None and after is None:
                resources.pop(path)
            else:
                raise ResourceIntegrityFault(
                    "workspace path delta has inconsistent ref endpoints")
    return resources


def _firing_workspace_resource_candidates(
        core, kernel, context,
) -> dict[str, list[tuple[ResourceVersionRef, dict, bytes]]]:
    """Return exact path resources staged under this firing's authority."""

    candidates: dict[
        str, list[tuple[ResourceVersionRef, dict, bytes]]
    ] = {}
    rows = core.event_store.provisional_firing_object_rows(
        context.own_transition_firing_ref.version_id)
    for row in rows:
        if row["object_type"] != "resource_version/v1":
            continue
        metadata = json.loads(row["metadata_json"])
        path = metadata.get("descriptors", {}).get("workspace_path")
        if (metadata.get("origin_kind") != "workspace_write"
                or metadata.get("task_ref") != _ref_payload(context.task_ref)
                or metadata.get("producer_ref")
                != _ref_payload(context.invocation_ref)
                or not isinstance(path, str)):
            continue
        path = _strict_workspace_path(path).as_posix()
        ref = ResourceVersionRef(
            TypedId.parse(str(row["logical_id"]), expected="resource"),
            TypedId.parse(
                str(row["version_id"]), expected="resource_version"),
        )
        prepared = kernel._firing_prepared(context, ref)
        if dict(prepared.metadata) != metadata:
            raise ResourceIntegrityFault(
                "workspace history candidate differs from exact firing resource")
        summary = metadata.get("summary")
        if (not isinstance(summary, str)
                or len(summary) > _MAX_PATH_DELTA_SUMMARY_CHARS):
            raise ResourceIntegrityFault(
                "workspace history resource summary is not bounded text")
        candidates.setdefault(path, []).append((
            ref, metadata,
            kernel._read_firing_registered(context, ref),
        ))
    return candidates


def _candidate_leaf(
        path: str,
        candidates: list[tuple[ResourceVersionRef, dict, bytes]],
) -> tuple[ResourceVersionRef, dict, bytes] | None:
    """Resolve a path's exact staged leaf from immutable supersedes refs."""

    superseded = set()
    for _ref, metadata, _payload in candidates:
        raw = metadata.get("reference_provenance", {}).get(
            "supersedes_ref")
        if not isinstance(raw, dict):
            continue
        version_ref = _version_from_payload(raw)
        if version_ref.entity_type != "resource_version/v1":
            raise ResourceIntegrityFault(
                "workspace resource supersedes ref has the wrong type")
        superseded.add(ResourceVersionRef(
            version_ref.entity_id, version_ref.version_id))
    leaves = [item for item in candidates if item[0] not in superseded]
    if not leaves:
        return None
    if len(leaves) != 1:
        raise ResourceIntegrityFault(
            f"workspace path {path!r} has ambiguous accepted resources")
    return leaves[0]


def _workspace_path_deltas(
        core, kernel, context, *, parent_ref: VersionRef,
        parent_files: dict[str, tuple[bytes, int]],
        final_files: dict[str, tuple[bytes, int]],
        changed_paths: list[str], deleted_paths: list[str],
) -> list[dict]:
    """Build sorted deltas from accepted resources and exact archive bytes."""

    parent_resources = _workspace_resource_state(core, parent_ref)
    candidates = _firing_workspace_resource_candidates(
        core, kernel, context)
    deltas = []
    for path in sorted((*changed_paths, *deleted_paths)):
        before_ref = parent_resources.get(path)
        before_payload = None
        before_summary = None
        if path in parent_files:
            if before_ref is None:
                raise ResourceIntegrityFault(
                    "workspace update lacks exact parent resource provenance")
            before_payload, before_summary = _exact_workspace_resource(
                core, before_ref, path)
            if before_payload != parent_files[path][0]:
                raise ResourceIntegrityFault(
                    "workspace parent resource differs from archive bytes")
        elif before_ref is not None:
            raise ResourceIntegrityFault(
                "workspace parent resource is absent from its archive")

        if path in deleted_paths:
            if before_ref is None:
                raise ResourceIntegrityFault(
                    "workspace deletion lacks exact parent resource provenance")
            after_ref = None
            after_summary = None
            change_kind = "delete"
        else:
            after_payload = final_files[path][0]
            if before_ref is not None and before_payload == after_payload:
                # A mode-only update or convergent merge reuses head provenance.
                after_ref = before_ref
                after_summary = before_summary
            else:
                leaf = _candidate_leaf(path, candidates.get(path, []))
                if leaf is None or leaf[2] != after_payload:
                    raise ResourceIntegrityFault(
                        "workspace revision path lacks one exact accepted resource")
                after_ref, after_metadata, _payload = leaf
                after_summary = after_metadata["summary"]
            change_kind = "create" if before_ref is None else "update"
        deltas.append({
            "path": path,
            "change_kind": change_kind,
            "before_resource_ref": (
                _resource_payload(before_ref)
                if before_ref is not None else None),
            "after_resource_ref": (
                _resource_payload(after_ref)
                if after_ref is not None else None),
            "before_summary": before_summary,
            "after_summary": after_summary,
        })
    return deltas


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
        "path_deltas": [],
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


def _candidate_ref(instance_ref: VersionRef) -> VersionRef:
    return VersionRef(
        "workspace_revision_candidate/v1",
        _stable_id(
            "workspace_revision_candidate", instance_ref.version_id),
        _stable_id(
            "workspace_revision_candidate_version", instance_ref.version_id),
    )


def _load_workspace_candidate(
        core, kernel, *, context, state, candidate_ref: VersionRef,
) -> tuple[dict, dict[str, tuple[bytes, int]]]:
    """Validate one candidate solely from exact Registry material."""

    prepared = core.get_version(candidate_ref.version_id)
    metadata = dict(prepared.metadata)
    view_ref, view = _firing_workspace_view(core, kernel, context)
    base_ref = _version_from_payload(view["base_revision_ref"])
    lineage_ref = _version_from_payload(view["workspace_lineage_ref"])
    expected = {
        "workspace_revision_candidate_ref": _ref_payload(candidate_ref),
        "execution_instance_ref": _ref_payload(state.instance_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(
            context.own_transition_firing_ref),
        "task_ref": _ref_payload(context.task_ref),
        "net_instance_ref": _ref_payload(context.net_instance_ref),
        "admission_marking_checkpoint_ref": _ref_payload(
            context.admission_marking_checkpoint_ref),
        "workspace_lineage_ref": _ref_payload(lineage_ref),
        "base_revision_ref": _ref_payload(base_ref),
        "firing_workspace_binding_ref": _ref_payload(view_ref),
        "payload_kind": "full_workspace_tar",
    }
    if (prepared.object_type != "workspace_revision_candidate/v1"
            or prepared.logical_id != candidate_ref.entity_id
            or prepared.media_type != "application/x-tar"
            or prepared.producer_invocation_id
            != context.invocation_ref.entity_id
            or any(metadata.get(key) != value
                   for key, value in expected.items())):
        raise ResourceIntegrityFault(
            "workspace candidate crosses its exact parent authority")
    payload = core.object_store.read_registered(prepared)
    files = _archive_payload_files(payload)
    base_files = _archive_files(core, base_ref)
    changed_paths = sorted(
        path for path, value in files.items()
        if base_files.get(path) != value)
    deleted_paths = sorted(set(base_files) - set(files))
    expected_deltas = _workspace_path_deltas(
        core, kernel, context, parent_ref=base_ref,
        parent_files=base_files, final_files=files,
        changed_paths=changed_paths, deleted_paths=deleted_paths)
    if (metadata.get("changed_paths") != changed_paths
            or metadata.get("deleted_paths") != deleted_paths
            or metadata.get("inventory_paths") != sorted(files)
            or metadata.get("path_deltas") != expected_deltas):
        raise ResourceIntegrityFault(
            "workspace candidate differs from its archive or exact resources")
    core.catalog.validate_instance(
        "workspace_revision_candidate/v1", category="object",
        instance=metadata)
    return metadata, files


def workspace_finalization_candidate(
        core, kernel, context, *, required: bool = False,
):
    """Return the one map-ready immutable candidate for a parent firing."""

    from .file_execution_net import (
        WORKSPACE_FINALIZATION_NET,
        execution_parent,
        execution_states_for_parent,
    )

    parent = execution_parent(context)
    states = tuple(
        state for state in execution_states_for_parent(core, parent)
        if state.definition.definition_key
        == WORKSPACE_FINALIZATION_NET.definition_key)
    if not states:
        if required:
            raise ResourceIntegrityFault(
                "workspace recovery lacks a finalization execution instance")
        return None
    if len(states) != 1:
        raise ResourceIntegrityFault(
            "parent firing has ambiguous workspace finalization executions")
    state, = states
    if (not state.checkpoint.map_ready
            or len(state.checkpoint.evidence_refs) != 1
            or state.checkpoint.evidence_refs[0].entity_type
            != "workspace_revision_candidate/v1"):
        raise ResourceIntegrityFault(
            "workspace finalization is not map-ready with one candidate")
    candidate_ref, = state.checkpoint.evidence_refs
    metadata, files = _load_workspace_candidate(
        core, kernel, context=context, state=state,
        candidate_ref=candidate_ref)
    return state, candidate_ref, metadata, files


def finalize_firing_workspace_candidate(
        core, kernel, execution, loop, *, idempotency_key: str,
        synchronize,
) -> VersionRef:
    """Register all tree changes, publish a candidate, and make it map-ready."""

    context = execution.operation.canonical.context
    from .file_execution_net import (
        WORKSPACE_FINALIZATION_NET,
        execute_idempotent_materialization,
    )

    def materialize(state):
        candidate_ref = _candidate_ref(state.instance_ref)
        try:
            core.get_version(candidate_ref.version_id)
        except RegistryReadError:
            synchronize()
            view_ref, view = _firing_workspace_view(core, kernel, context)
            base_ref = _version_from_payload(view["base_revision_ref"])
            lineage_ref = _version_from_payload(view["workspace_lineage_ref"])
            files = _tree_files(_resolve_registry_workspace_root(
                core, view["allowed_root"]))
            base_files = _archive_files(core, base_ref)
            changed_paths = sorted(
                path for path, value in files.items()
                if base_files.get(path) != value)
            deleted_paths = sorted(set(base_files) - set(files))
            metadata = {
                "workspace_revision_candidate_ref": _ref_payload(
                    candidate_ref),
                "execution_instance_ref": _ref_payload(state.instance_ref),
                "invocation_ref": _ref_payload(context.invocation_ref),
                "transition_firing_ref": _ref_payload(
                    context.own_transition_firing_ref),
                "task_ref": _ref_payload(context.task_ref),
                "net_instance_ref": _ref_payload(context.net_instance_ref),
                "admission_marking_checkpoint_ref": _ref_payload(
                    context.admission_marking_checkpoint_ref),
                "workspace_lineage_ref": _ref_payload(lineage_ref),
                "base_revision_ref": _ref_payload(base_ref),
                "firing_workspace_binding_ref": _ref_payload(view_ref),
                "changed_paths": changed_paths,
                "deleted_paths": deleted_paths,
                "path_deltas": _workspace_path_deltas(
                    core, kernel, context, parent_ref=base_ref,
                    parent_files=base_files, final_files=files,
                    changed_paths=changed_paths,
                    deleted_paths=deleted_paths),
                "inventory_paths": sorted(files),
                "semantic_output_refs": [
                    _resource_payload(ref)
                    for ref in loop.written_resource_refs],
                "payload_kind": "full_workspace_tar",
            }
            core.catalog.validate_instance(
                "workspace_revision_candidate/v1", category="object",
                instance=metadata)
            tx = core.begin(
                idempotency_key=f"{idempotency_key}:candidate",
                task_round_id=context.task_round_ref.entity_id,
                net_instance_id=context.net_instance_ref.entity_id)
            tx.prewrite(
                object_type="workspace_revision_candidate/v1",
                logical_id=candidate_ref.entity_id,
                version_id=candidate_ref.version_id,
                payload=_full_archive(files), metadata=metadata,
                media_type="application/x-tar",
                schema_ref="registry_v1/workspace_revision_candidate/v1",
                producer_invocation_id=context.invocation_ref.entity_id)
            for ordinal, target in enumerate((
                    state.instance_ref, base_ref, view_ref,
                    context.own_transition_firing_ref)):
                tx.relate(TypedRelation(
                    _stable_id(
                        "relation", idempotency_key,
                        "workspace-candidate", ordinal),
                    "derived_from", candidate_ref, target),
                    producer_invocation_id=context.invocation_ref.entity_id)
            tx.commit()
        _load_workspace_candidate(
            core, kernel, context=context, state=state,
            candidate_ref=candidate_ref)
        return (candidate_ref,)

    _state, evidence_refs = execute_idempotent_materialization(
        core, context=context,
        definition=WORKSPACE_FINALIZATION_NET,
        transition_id="finalize_workspace",
        identity_key=idempotency_key,
        materialize=materialize,
    )
    candidate_ref, = evidence_refs
    final = workspace_finalization_candidate(
        core, kernel, context, required=True)
    if final is None or final[1] != candidate_ref:
        raise ResourceIntegrityFault(
            "workspace finalization candidate evidence changed")
    return candidate_ref


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
    candidate = workspace_finalization_candidate(core, kernel, context)
    if candidate is None:
        root = _resolve_registry_workspace_root(core, view["allowed_root"])
        files = _tree_files(root)
    else:
        _state, _candidate_ref_value, candidate_metadata, files = candidate
        if (candidate_metadata["base_revision_ref"]
                != _ref_payload(base_ref)
                or candidate_metadata["workspace_lineage_ref"]
                != _ref_payload(lineage_ref)
                or candidate_metadata["firing_workspace_binding_ref"]
                != _ref_payload(view_ref)):
            raise ResourceIntegrityFault(
                "workspace candidate differs from the current firing view")
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
    parent_files = base_files
    if expected_head_ref != base_ref:
        intervening = _intervening_paths(
            core, base_ref, expected_head_ref)
        head_files = _archive_files(core, expected_head_ref)
        parent_files = head_files
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
    path_deltas = _workspace_path_deltas(
        core, kernel, context, parent_ref=expected_head_ref,
        parent_files=parent_files, final_files=merged_files,
        changed_paths=changed_paths, deleted_paths=deleted_paths)
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
        "path_deltas": path_deltas,
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


__all__ = (
    "finalize_firing_workspace_candidate",
    "prepare_firing_workspace_plans",
    "workspace_finalization_candidate",
)
