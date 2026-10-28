"""Bounded exact-row caching against real SQLite; no model/provider calls."""

from concurrent.futures import ThreadPoolExecutor
import gc
import json
import sqlite3
import weakref

import pytest

from cpn.rpnh.registry._event_store import exact_read_cache as cache_module
from cpn.rpnh.registry._event_store.exact_read_cache import (
    ExactReadCache, StoreNamespace)
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry.identities import new_id
from test_canonical_head_projection import add_event


def publish_row(store, *, version=None, text="value"):
    """Raw valid SQL publication fixture; not an admission/authority fixture."""
    version = version or new_id("resource_version")
    event, transaction = add_event(store, new_id("task"), store.max_ordinal() + 1)
    with store.connection_scope() as db:
        db.execute("INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            str(new_id("resource")), str(version), "resource/v1", len(text),
            "text/plain", "fixture/v1", None, "fixture/body", json.dumps({"text": text}),
            str(transaction), str(event)))
    return version


@pytest.fixture
def cache(monkeypatch):
    result = ExactReadCache(max_entries=4, max_estimated_bytes=65536)
    monkeypatch.setattr(cache_module, "PROCESS_EXACT_READ_CACHE", result)
    return result


@pytest.fixture
def store(tmp_path, cache):
    return EventStore(tmp_path / "registry.sqlite3", None)


def track_reads(store, monkeypatch):
    statements, connections = [], []
    original = store.connect

    def tracked():
        db = original()
        db.set_trace_callback(statements.append)
        connections.append(db)
        return db

    monkeypatch.setattr(store, "connect", tracked)
    return statements, connections


def object_queries(statements):
    return [text for text in statements if text.startswith("SELECT * FROM objects WHERE version_id=")]


def test_positive_hit_preserves_row_type_values_and_closes_read(store, cache, monkeypatch):
    version = publish_row(store)
    statements, connections = track_reads(store, monkeypatch)
    first = store.object_row(version)
    assert isinstance(first, sqlite3.Row)
    assert store.object_row(version) is first
    assert len(object_queries(statements)) == 1
    assert cache.statistics()["entries"] == 1
    detached = dict(first)
    detached["metadata_json"] = "mutated"
    assert store.object_row(version)["metadata_json"] != "mutated"
    with pytest.raises(TypeError):
        first["metadata_json"] = "mutated"
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_misses_are_not_cached_and_later_publication_is_visible(store, cache, monkeypatch):
    version = new_id("resource_version")
    statements, _ = track_reads(store, monkeypatch)
    assert store.object_row(version) is None
    assert store.object_row(version) is None
    assert cache.statistics()["entries"] == 0
    publish_row(store, version=version, text="later")
    assert json.loads(store.object_row(version)["metadata_json"])["text"] == "later"
    assert len(object_queries(statements)) == 3


def test_sources_and_new_reader_instances_never_share_rows(tmp_path, cache):
    a = EventStore(tmp_path / "a.sqlite3", None)
    b = EventStore(tmp_path / "b.sqlite3", None)
    version = publish_row(a, text="source-a")
    publish_row(b, version=version, text="source-b")
    assert a._object_row_cache is b._object_row_cache
    assert a._object_row_cache_namespace != b._object_row_cache_namespace
    assert a.object_row(version)["metadata_json"] != b.object_row(version)["metadata_json"]
    original = a.object_row(version)
    # A deliberate illegal rewrite models corruption outside the immutable
    # Registry contract. A new reader must not inherit an old warm row.
    with a.connection_scope() as db:
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps({"text": "tampered"}), str(version)))
    fresh = EventStore(a.path, None, read_only=True)
    assert fresh.object_row(version)["metadata_json"] == json.dumps({"text": "tampered"})
    assert a.object_row(version) is original
    assert a.object_row(version, use_cache=False)["metadata_json"] == json.dumps({"text": "tampered"})
    a.clear_exact_read_cache()
    assert a.object_row(version)["metadata_json"] == json.dumps({"text": "tampered"})


def test_global_entries_budget_evicts_across_stores_and_reloads_equal(tmp_path, cache, monkeypatch):
    stores = [EventStore(tmp_path / f"{i}.sqlite3", None) for i in range(3)]
    versions = [publish_row(store, text=str(i)) for i, store in enumerate(stores)]
    first = dict(stores[0].object_row(versions[0]))
    statements, _ = track_reads(stores[0], monkeypatch)
    for store, version in zip(stores[1:], versions[1:]):
        store.object_row(version)
        store.object_row(publish_row(store, text="extra"))
        assert cache.statistics()["entries"] <= 4
    assert dict(stores[0].object_row(versions[0])) == first
    assert len(object_queries(statements)) == 1  # cold again after global eviction


def test_lru_hit_refreshes_recency(store, cache):
    versions = [publish_row(store, text=str(i)) for i in range(5)]
    for version in versions[:4]:
        store.object_row(version)
    store.object_row(versions[0])
    store.object_row(versions[4])
    ns = store._object_row_cache_namespace
    assert cache.get(ns, str(versions[0])) is not None
    assert cache.get(ns, str(versions[1])) is None


def native_row(text="value"):
    db = sqlite3.connect(":memory:")
    try:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT ? AS body", (text,)).fetchone()
    finally:
        db.close()


def test_byte_budget_evicts_and_oversized_rows_bypass_without_rejection():
    ns = StoreNamespace("one-source")
    row = native_row()
    size = ExactReadCache.estimated_entry_bytes(ns, "v1", row)
    cache = ExactReadCache(max_entries=99, max_estimated_bytes=size)
    cache.put(ns, "v1", row)
    cache.put(ns, "v2", row)
    assert cache.get(ns, "v1") is None
    assert cache.get(ns, "v2") is row
    huge = native_row("x" * (size * 2))
    assert cache.put(ns, "v3", huge) is huge
    assert cache.get(ns, "v3") is None
    assert cache.statistics()["entries"] == 1
    assert cache.statistics()["estimated_bytes"] <= size


@pytest.mark.parametrize("limits", [dict(max_entries=0), dict(max_estimated_bytes=0)])
def test_zero_budget_is_cache_bypass_not_query_limit(limits):
    cache = ExactReadCache(**limits)
    ns, row = StoreNamespace("source"), native_row()
    assert cache.put(ns, "v", row) is row
    assert cache.get(ns, "v") is None
    assert cache.statistics()["entries"] == 0


@pytest.mark.parametrize("limits", [dict(max_entries=-1), dict(max_entries=True),
                                    dict(max_estimated_bytes=-1), dict(max_estimated_bytes="10")])
def test_invalid_budgets_are_rejected(limits):
    with pytest.raises(ValueError):
        ExactReadCache(**limits)


def test_cache_rejects_mutable_non_sqlite_rows():
    with pytest.raises(TypeError):
        ExactReadCache().put(StoreNamespace("source"), "v", {"body": "mutable"})


def test_collected_store_releases_entries_without_retaining_namespace(store, cache):
    store.object_row(publish_row(store))
    ns = store._object_row_cache_namespace
    assert cache.statistics()["entries"] == 1
    # Fixture itself holds store, so use another separately-owned reader.
    reader = EventStore(store.path, None, read_only=True)
    version = publish_row(store, text="another")
    reader.object_row(version)
    reader_ref = weakref.ref(reader)
    reader_ns = weakref.ref(reader._object_row_cache_namespace)
    del reader
    gc.collect()
    assert reader_ref() is None
    assert reader_ns() is None
    assert cache.statistics()["entries"] == 1
    store.clear_exact_read_cache()
    assert cache.statistics()["entries"] == 0
    assert cache.statistics()["estimated_bytes"] == 0


def test_sql_failure_propagates_and_failed_read_closes(store, monkeypatch):
    version = publish_row(store)
    store.object_row(version)
    with store.connection_scope() as db:
        db.execute("DROP TABLE objects")
    _, connections = track_reads(store, monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.object_row(version, use_cache=False)
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


def test_read_only_reads_do_not_write_database(store, monkeypatch):
    version = publish_row(store)
    reader = EventStore(store.path, None, read_only=True)
    statements, _ = track_reads(reader, monkeypatch)
    assert reader.object_row(version) is not None
    assert reader.object_row(version) is not None
    assert all(not sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for sql in statements)
    with store.connection_scope() as db:
        assert db.execute("SELECT count(*) FROM objects").fetchone()[0] == 1


def test_concurrent_cache_activity_stays_bounded_and_isolated():
    cache = ExactReadCache(max_entries=7, max_estimated_bytes=18000)
    namespaces = [StoreNamespace(f"source-{i}") for i in range(8)]

    def work(index):
        ns = namespaces[index]
        row = native_row(f"value-{index}")
        for number in range(100):
            key = str(number % 11)
            assert cache.put(ns, key, row)["body"] == f"value-{index}"
            found = cache.get(ns, key)
            assert found is None or found["body"] == f"value-{index}"
            if number % 13 == 0:
                cache.clear_namespace(ns)
            stats = cache.statistics()
            assert stats["entries"] <= 7
            assert 0 <= stats["estimated_bytes"] <= 18000

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    for ns in namespaces:
        cache.clear_namespace(ns)
    assert cache.statistics()["entries"] == cache.statistics()["estimated_bytes"] == 0
