from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from jsonschema import Draft7Validator
import pytest

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.workspace_settlement import (
    _convergent_delta,
    _restore_tree,
    _tree_files,
    _workspace_path_deltas,
    _workspace_resource_state,
)


def _id(kind: str, value: int) -> TypedId:
    return TypedId.parse(f"{kind}:{value:032x}")


def _version_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _resource_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


def test_restore_tree_replaces_fifo_with_snapshot_regular_file(
        tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    registered = root / "registered_resources"
    registered.mkdir()
    preserved = registered / "evidence.bin"
    preserved.write_bytes(b"registry evidence")
    target = root / "saved.txt"
    os.mkfifo(target, 0o600)
    # Keep a nonblocking reader open so the historical direct write-to-FIFO
    # implementation would return instead of hanging this regression test.
    reader = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    try:
        _restore_tree(root, {"saved.txt": (b"ORIGINAL", 0o640)})
    finally:
        os.close(reader)

    assert stat.S_ISREG(target.lstat().st_mode)
    assert target.read_bytes() == b"ORIGINAL"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert _tree_files(root) == {"saved.txt": (b"ORIGINAL", 0o640)}
    assert preserved.read_bytes() == b"registry evidence"
    assert not tuple(root.rglob(".rpnh-restore-stage-*.tmp"))


class _EventStore:
    def __init__(self) -> None:
        self.canonical: set[str] = set()
        self.provisional_rows: list[dict] = []

    def canonical_object_row(self, version_id):
        return {} if str(version_id) in self.canonical else None

    def provisional_firing_object_rows(self, _firing_version_id):
        return tuple(self.provisional_rows)


class _Core:
    def __init__(self) -> None:
        self.event_store = _EventStore()
        self.versions: dict[str, SimpleNamespace] = {}
        self.payloads: dict[str, bytes] = {}
        self.object_store = SimpleNamespace(
            read_registered=lambda prepared: self.payloads[
                str(prepared.version_id)])

    def get_version(self, version_id):
        return self.versions[str(version_id)]


class _Kernel:
    def __init__(self, core: _Core) -> None:
        self.core = core

    def _firing_prepared(self, _context, resource_ref):
        return self.core.get_version(resource_ref.resource_version_id)

    def _read_firing_registered(self, _context, resource_ref):
        return self.core.payloads[str(resource_ref.resource_version_id)]


class _HistoryFixture:
    def __init__(self) -> None:
        self.core = _Core()
        self.kernel = _Kernel(self.core)
        self.lineage_id = _id("workspace_lineage", 1)
        self.task_ref = VersionRef(
            "task/v1", _id("task", 1), _id("task_version", 1))
        self.invocation_ref = VersionRef(
            "invocation/v1", _id("invocation", 1),
            _id("invocation_version", 1))
        self.firing_ref = VersionRef(
            "transition_firing/v1", _id("transition_firing", 1),
            _id("transition_firing_version", 1))
        self.context = SimpleNamespace(
            task_ref=self.task_ref,
            invocation_ref=self.invocation_ref,
            own_transition_firing_ref=self.firing_ref,
        )
        self.next_revision = 1
        self.next_resource_version = 1
        self.resource_id = _id("resource", 1)

    def new_context(self, value: int) -> None:
        self.invocation_ref = VersionRef(
            "invocation/v1", _id("invocation", value),
            _id("invocation_version", value))
        self.firing_ref = VersionRef(
            "transition_firing/v1", _id("transition_firing", value),
            _id("transition_firing_version", value))
        self.context = SimpleNamespace(
            task_ref=self.task_ref,
            invocation_ref=self.invocation_ref,
            own_transition_firing_ref=self.firing_ref,
        )
        self.core.event_store.provisional_rows = []

    def revision(
            self, *, parent: VersionRef | None, disposition: str,
            path_deltas: list[dict],
    ) -> VersionRef:
        ref = VersionRef(
            "workspace_revision/v1", self.lineage_id,
            _id("workspace_revision", self.next_revision))
        self.next_revision += 1
        metadata = {
            "disposition": disposition,
            "parent_revision_ref": (
                _version_payload(parent) if parent is not None else None),
            "path_deltas": path_deltas,
        }
        self.core.versions[str(ref.version_id)] = SimpleNamespace(
            object_type="workspace_revision/v1",
            logical_id=ref.entity_id,
            version_id=ref.version_id,
            metadata=metadata,
        )
        return ref

    def resource(
            self, path: str, payload: bytes, summary: str, *,
            canonical: bool = False,
            supersedes: ResourceVersionRef | None = None,
    ) -> ResourceVersionRef:
        ref = ResourceVersionRef(
            self.resource_id,
            _id("resource_version", self.next_resource_version))
        self.next_resource_version += 1
        metadata = {
            "origin_kind": "workspace_write",
            "task_ref": _version_payload(self.task_ref),
            "producer_ref": _version_payload(self.invocation_ref),
            "summary": summary,
            "descriptors": {"workspace_path": path},
            "reference_provenance": {
                "supersedes_ref": (
                    _version_payload(supersedes.as_version_ref())
                    if supersedes is not None else None),
            },
        }
        prepared = SimpleNamespace(
            object_type="resource_version/v1",
            logical_id=ref.resource_id,
            version_id=ref.resource_version_id,
            metadata=metadata,
        )
        self.core.versions[str(ref.resource_version_id)] = prepared
        self.core.payloads[str(ref.resource_version_id)] = payload
        if canonical:
            self.core.event_store.canonical.add(
                str(ref.resource_version_id))
        else:
            self.core.event_store.provisional_rows.append({
                "object_type": "resource_version/v1",
                "logical_id": str(ref.resource_id),
                "version_id": str(ref.resource_version_id),
                "metadata_json": json.dumps(metadata),
            })
        return ref


def test_create_update_delete_history_uses_exact_refs_bytes_and_summaries() -> None:
    history = _HistoryFixture()
    genesis = history.revision(
        parent=None, disposition="genesis", path_deltas=[])

    created_ref = history.resource(
        "notes/item.txt", b"first", "created summary")
    created = _workspace_path_deltas(
        history.core, history.kernel, history.context,
        parent_ref=genesis, parent_files={},
        final_files={"notes/item.txt": (b"first", 0o644)},
        changed_paths=["notes/item.txt"], deleted_paths=[])
    assert created == [{
        "path": "notes/item.txt",
        "change_kind": "create",
        "before_resource_ref": None,
        "after_resource_ref": _resource_payload(created_ref),
        "before_summary": None,
        "after_summary": "created summary",
    }]

    history.core.event_store.canonical.add(
        str(created_ref.resource_version_id))
    created_revision = history.revision(
        parent=genesis, disposition="committed", path_deltas=created)
    history.new_context(2)
    intermediate_ref = history.resource(
        "notes/item.txt", b"intermediate", "intermediate summary",
        supersedes=created_ref)
    updated_ref = history.resource(
        "notes/item.txt", b"second", "updated summary",
        supersedes=intermediate_ref)
    updated = _workspace_path_deltas(
        history.core, history.kernel, history.context,
        parent_ref=created_revision,
        parent_files={"notes/item.txt": (b"first", 0o644)},
        final_files={"notes/item.txt": (b"second", 0o644)},
        changed_paths=["notes/item.txt"], deleted_paths=[])
    assert updated == [{
        "path": "notes/item.txt",
        "change_kind": "update",
        "before_resource_ref": _resource_payload(created_ref),
        "after_resource_ref": _resource_payload(updated_ref),
        "before_summary": "created summary",
        "after_summary": "updated summary",
    }]

    history.core.event_store.canonical.add(
        str(updated_ref.resource_version_id))
    updated_revision = history.revision(
        parent=created_revision, disposition="committed", path_deltas=updated)
    history.new_context(3)
    deleted = _workspace_path_deltas(
        history.core, history.kernel, history.context,
        parent_ref=updated_revision,
        parent_files={"notes/item.txt": (b"second", 0o644)},
        final_files={}, changed_paths=[], deleted_paths=["notes/item.txt"])
    assert deleted == [{
        "path": "notes/item.txt",
        "change_kind": "delete",
        "before_resource_ref": _resource_payload(updated_ref),
        "after_resource_ref": None,
        "before_summary": "updated summary",
        "after_summary": None,
    }]


def test_path_deltas_are_sorted_deterministically() -> None:
    history = _HistoryFixture()
    genesis = history.revision(
        parent=None, disposition="genesis", path_deltas=[])
    history.resource("z.txt", b"z", "z summary")
    history.resource("a.txt", b"a", "a summary")

    deltas = _workspace_path_deltas(
        history.core, history.kernel, history.context,
        parent_ref=genesis, parent_files={},
        final_files={"z.txt": (b"z", 0o644), "a.txt": (b"a", 0o644)},
        changed_paths=["z.txt", "a.txt"], deleted_paths=[])

    assert [item["path"] for item in deltas] == ["a.txt", "z.txt"]


def test_identical_content_convergence_inherits_head_provenance() -> None:
    history = _HistoryFixture()
    genesis = history.revision(
        parent=None, disposition="genesis", path_deltas=[])
    head_ref = history.resource(
        "same.txt", b"same", "head summary", canonical=True)
    head = history.revision(parent=genesis, disposition="committed", path_deltas=[{
        "path": "same.txt",
        "change_kind": "create",
        "before_resource_ref": None,
        "after_resource_ref": _resource_payload(head_ref),
        "before_summary": None,
        "after_summary": "head summary",
    }])
    sibling_ref = history.resource(
        "same.txt", b"same", "sibling summary")

    changed, deleted = _convergent_delta(
        ["same.txt"], [], {"same.txt": (b"same", 0o644)},
        {"same.txt": (b"same", 0o644)}, {"same.txt"})
    assert changed == []
    assert deleted == []
    assert sibling_ref != head_ref

    merged_deltas = _workspace_path_deltas(
        history.core, history.kernel, history.context,
        parent_ref=head, parent_files={"same.txt": (b"same", 0o644)},
        final_files={"same.txt": (b"same", 0o644)},
        changed_paths=changed, deleted_paths=deleted)
    merged = history.revision(
        parent=head, disposition="merged", path_deltas=merged_deltas)
    assert merged_deltas == []
    assert _workspace_resource_state(history.core, merged) == {
        "same.txt": head_ref}


def _schema_metadata(disposition: str, path_deltas: list[dict]) -> dict:
    ref = VersionRef(
        "workspace_revision/v1", _id("workspace_lineage", 9),
        _id("workspace_revision", 9))
    nullable = disposition == "genesis"
    conflict = disposition == "conflict"
    return {
        "workspace_lineage_id": str(ref.entity_id),
        "workspace_revision_id": str(ref.version_id),
        "workspace_revision_ref": _version_payload(ref),
        "run_ref": _version_payload(VersionRef(
            "run/v1", _id("run", 9), _id("run_version", 9))),
        "task_ref": _version_payload(VersionRef(
            "task/v1", _id("task", 9), _id("task_version", 9))),
        "net_instance_ref": _version_payload(VersionRef(
            "net_instance/v1", _id("net_instance", 9),
            _id("net_instance_version", 9))),
        "parent_revision_ref": None if nullable else _version_payload(ref),
        "base_revision_ref": None if nullable else _version_payload(ref),
        "producer_invocation_ref": None if nullable else _version_payload(
            VersionRef("invocation/v1", _id("invocation", 9),
                       _id("invocation_version", 9))),
        "transition_firing_ref": None if nullable else _version_payload(
            VersionRef("transition_firing/v1", _id("transition_firing", 9),
                       _id("transition_firing_version", 9))),
        "firing_workspace_binding_ref": None if nullable else _version_payload(
            VersionRef("workspace_binding/v1", _id("workspace_binding", 9),
                       _id("workspace_binding_version", 9))),
        "reopen_authorization_ref": None,
        "disposition": disposition,
        "changed_paths": [],
        "deleted_paths": [],
        "path_deltas": path_deltas,
        "inventory_paths": [],
        "conflict_paths": ["conflicted.txt"] if conflict else [],
        "semantic_output_refs": [],
        "trace_summary_refs": [],
        "payload_kind": (
            "conflict_evidence" if conflict else "full_workspace_tar"),
        "settled": not conflict,
    }


def test_genesis_and_conflict_require_empty_path_deltas() -> None:
    schema = json.loads((
        Path(__file__).parents[1]
        / "cpn/schemas/registry_v1/workspace_revision.v1.schema.json"
    ).read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)

    validator.validate(_schema_metadata("genesis", []))
    validator.validate(_schema_metadata("conflict", []))

    ref = ResourceVersionRef(
        _id("resource", 9), _id("resource_version", 9))
    false_delta = [{
        "path": "conflicted.txt",
        "change_kind": "create",
        "before_resource_ref": None,
        "after_resource_ref": _resource_payload(ref),
        "before_summary": None,
        "after_summary": "not settled",
    }]
    with pytest.raises(Exception):
        validator.validate(_schema_metadata("conflict", false_delta))
