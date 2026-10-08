"""Registry descriptor strictness and bounded exact-byte reads, without owners."""
from dataclasses import replace
import pytest
from cpn.rpnh.registry._event_store.collaboration_descriptors import readable_descriptor
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import PreparedObject
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json


def material(tmp_path, raw=None, extra=None):
    store = ObjectStore(tmp_path / "objects", SchemaCatalog())
    task_id, version_id = new_id("task"), new_id("task_version")
    metadata = {"task_id": str(task_id), "task_version_id": str(version_id), **(extra or {})}
    raw = canonical_json(metadata) if raw is None else raw(metadata)
    path = store.path_for_version(version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    prepared = PreparedObject("task/v1", task_id, version_id, len(raw), "application/json",
        "registry_v1/task/v1", None, store.locator_for_version(version_id), metadata)
    return store, prepared


def test_strict_registered_descriptor_rejects_duplicate_keys(tmp_path):
    store, prepared = material(tmp_path, lambda d: b'{"task_id":"' + d["task_id"].encode() + b'",' + canonical_json(d)[1:])
    with pytest.raises(RuntimeError, match="readable exact immutable bytes"):
        readable_descriptor(store, prepared, strict=True)
    assert readable_descriptor(store, prepared) == dict(prepared.metadata)


@pytest.mark.parametrize("token", [b'NaN', b'Infinity', b'-Infinity', b'1e999'])
def test_strict_registered_descriptor_rejects_nonfinite(tmp_path, token):
    store, prepared = material(tmp_path, lambda d: b'{"unexpected":' + token + b',' + canonical_json(d)[1:])
    with pytest.raises(RuntimeError, match="readable exact immutable bytes"):
        readable_descriptor(store, prepared, strict=True)


def test_bounded_descriptor_refuses_enlarged_backing_file(tmp_path, monkeypatch):
    store, prepared = material(tmp_path)
    store.path_for_version(prepared.version_id).write_bytes(b' ' * 500)
    with pytest.raises(RuntimeError, match="readable exact immutable bytes"):
        readable_descriptor(store, prepared, max_bytes=prepared.size, strict=True)


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_registered_read_bound_is_typed(tmp_path, limit):
    store, prepared = material(tmp_path)
    with pytest.raises(TypeError, match="nonnegative integer"):
        store.read_registered(prepared, max_bytes=limit)
