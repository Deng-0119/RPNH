from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading

import pytest

from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.object_store import ObjectIntegrityError, ObjectStore
from cpn.rpnh.registry.schema_catalog import SchemaCatalog


def _prewrite_stream(
        store: ObjectStore, logical_id: TypedId, version_id: TypedId,
        payload: bytes, *, expected_size: int | None = None,
) -> None:
    with tempfile.TemporaryFile() as stream:
        stream.write(payload)
        stream.seek(0)
        store.prewrite_descriptor(
            object_type="task/v1",
            logical_id=logical_id,
            version_id=version_id,
            descriptor=stream.fileno(),
            expected_size=(
                len(payload) if expected_size is None else expected_size),
            metadata={
                "task_id": str(logical_id),
                "task_version_id": str(version_id),
            },
            media_type="application/json",
            schema_ref="registry_v1/task/v1",
        )


def test_prewrite_descriptor_requires_exact_bytes_for_existing_version(tmp_path) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    logical_id = new_id("task")
    version_id = new_id("task_version")

    _prewrite_stream(store, logical_id, version_id, b"same")
    _prewrite_stream(store, logical_id, version_id, b"same")
    assert store.path_for_version(version_id).read_bytes() == b"same"

    with pytest.raises(ObjectIntegrityError, match="immutable version collision"):
        _prewrite_stream(store, logical_id, version_id, b"diff")
    with pytest.raises(ObjectIntegrityError, match="immutable version collision"):
        _prewrite_stream(store, logical_id, version_id, b"longer")

    assert store.path_for_version(version_id).read_bytes() == b"same"
    assert not list(store.path_for_version(version_id).parent.glob("prewrite-stream-*"))


def test_prewrite_descriptor_rejects_declared_size_mismatch(tmp_path) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    logical_id = new_id("task")
    version_id = new_id("task_version")

    with pytest.raises(ObjectIntegrityError, match="declared byte count"):
        _prewrite_stream(
            store, logical_id, version_id, b"payload", expected_size=6)

    assert not store.path_for_version(version_id).exists()
    assert not list(store.path_for_version(version_id).parent.glob(
        "prewrite-stream-*"))


def test_concurrent_exact_version_publish_never_overwrites_winner(
        tmp_path,
) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    logical_id = new_id("task")
    version_id = new_id("task_version")
    barrier = threading.Barrier(2)

    def publish(payload: bytes) -> str:
        barrier.wait(timeout=5)
        try:
            _prewrite_stream(store, logical_id, version_id, payload)
        except ObjectIntegrityError:
            return "collision"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(publish, (b"AAAA", b"BBBB")))

    assert sorted(outcomes) == ["collision", "published"]
    assert store.path_for_version(version_id).read_bytes() in {b"AAAA", b"BBBB"}
    assert not list(store.path_for_version(version_id).parent.glob(
        "prewrite-stream-*"))


def test_exact_byte_replay_syncs_the_publication_directory(
        tmp_path, monkeypatch,
) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    logical_id = new_id("task")
    version_id = new_id("task_version")
    synchronized: list = []
    monkeypatch.setattr(
        store, "_fsync_directory", lambda path: synchronized.append(path))

    _prewrite_stream(store, logical_id, version_id, b"same")
    _prewrite_stream(store, logical_id, version_id, b"same")

    assert synchronized == [
        store.path_for_version(version_id).parent,
        store.path_for_version(version_id).parent,
    ]


def test_unregistered_exact_version_residue_is_not_replaced(tmp_path) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    logical_id = new_id("task")
    version_id = new_id("task_version")
    metadata = {
        "task_id": str(logical_id),
        "task_version_id": str(version_id),
    }
    store.prewrite(
        object_type="task/v1", logical_id=logical_id,
        version_id=version_id, payload=b"old!", metadata=metadata,
        media_type="application/json", schema_ref="registry_v1/task/v1")

    with pytest.raises(ObjectIntegrityError, match="immutable version collision"):
        store.prewrite(
            object_type="task/v1", logical_id=logical_id,
            version_id=version_id, payload=b"new!", metadata=metadata,
            media_type="application/json", schema_ref="registry_v1/task/v1")

    assert store.path_for_version(version_id).read_bytes() == b"old!"


def test_prewrite_descriptor_does_not_publish_when_stream_read_fails(
        tmp_path, monkeypatch) -> None:
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    version_id = new_id("task_version")
    with tempfile.TemporaryFile() as stream:
        def fail_read(_descriptor: int, _size: int) -> bytes:
            raise OSError("stream failed")

        monkeypatch.setattr("cpn.rpnh.registry.object_store.os.read", fail_read)
        with pytest.raises(OSError, match="stream failed"):
            _prewrite_stream_with_reader(store, version_id, stream.fileno())

    assert not store.path_for_version(version_id).exists()
    assert not list(store.path_for_version(version_id).parent.glob("prewrite-stream-*"))


def _prewrite_stream_with_reader(store: ObjectStore, version_id, reader: int) -> None:
    logical_id = new_id("task")
    store.prewrite_descriptor(
        object_type="task/v1",
        logical_id=logical_id,
        version_id=version_id,
        descriptor=reader,
        expected_size=0,
        metadata={
            "task_id": str(logical_id),
            "task_version_id": str(version_id),
        },
        media_type="application/json",
        schema_ref="registry_v1/task/v1",
    )
