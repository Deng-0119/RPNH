"""Operation-owned SQLite lifetime; no change to transaction or cut semantics."""
from contextlib import contextmanager
import gc
import os
import sqlite3

import pytest

from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry.identities import new_id
from test_canonical_head_projection import add_event, mark_firing


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "registry.sqlite3", None)


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_native_connect_contract_is_unchanged(store):
    connection = store.connect()
    try:
        assert isinstance(connection, sqlite3.Connection)
        with connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.execute("SELECT 2").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("fail", [False, True])
def test_scope_commits_or_rolls_back_before_close(store, fail):
    # Native connections use isolation_level=None; explicit BEGIN is needed to
    # test actual rollback, not merely successful autocommit statements.
    try:
        with store.connection_scope() as connection:
            connection.execute("BEGIN")
            connection.execute("INSERT INTO registry_meta VALUES ('scope-test', 'yes')")
            if fail:
                raise ValueError("rollback witness")
    except ValueError:
        assert fail
    assert_closed(connection)
    assert store.get_meta("scope-test") == (None if fail else "yes")


def test_scope_closes_after_commit_failure(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection_scope() as connection:
            connection.execute("BEGIN")
            connection.execute("PRAGMA defer_foreign_keys=ON")
            connection.execute(
                "INSERT INTO outbox VALUES ('missing', 'task', 1, '[]', 'now')")
    assert_closed(connection)
    with store.connection_scope() as verify:
        assert verify.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_generator_consumes_inside_scope_and_closes_on_abandonment(store):
    handles = []

    def values():
        with store.connection_scope() as connection:
            handles.append(connection)
            yield from connection.execute("SELECT 1 UNION ALL SELECT 2")

    iterator = values()
    assert next(iterator)[0] == 1
    assert handles[-1].execute("SELECT 3").fetchone()[0] == 3
    assert next(iterator)[0] == 2
    with pytest.raises(StopIteration):
        next(iterator)
    assert_closed(handles[-1])
    iterator = values()
    next(iterator)
    iterator.close()
    assert_closed(handles[-1])


def test_materialized_reads_match_legacy_scope_and_keep_fixed_cut(store, monkeypatch):
    task = new_id("task")
    first, _ = add_event(store, task, 1)
    early, early_tx = add_event(store, task, 2)
    _, success_tx = add_event(store, task, 7, event_type="transaction_committed/v1")
    mark_firing(store, early, early_tx, publication_tx=success_tx)
    handles = []
    original = store.connect

    def tracked():
        connection = original()
        handles.append(connection)
        return connection

    monkeypatch.setattr(store, "connect", tracked)

    def read():
        return (
            store.writer_epoch, store.get_meta("writer_epoch"),
            store.stream_heads(), store.stream_head_at("main", through_ordinal=1),
            store.max_ordinal(), store.list_events(), store.event_by_id(first),
            store.first_and_last_events(), store.has_event_idempotency_prefix(str(first)),
            store.list_events_by_type(("fixture/v1",)),
            store.list_events_by_aggregate("main"), store.list_events_by_producer("absent"),
            store.canonical_events(through_ordinal=2),
            store.canonical_events(through_ordinal=7), store.canonical_view(through_ordinal=2),
            store.canonical_object_row(new_id("resource_version"), through_ordinal=2),
            store.canonical_object_rows(through_ordinal=2),
            store.canonical_relation_rows(through_ordinal=2),
        )

    result = read()
    assert [event.ordinal for event in result[12]] == [1]
    assert [event.ordinal for event in result[13]] == [1, 2, 7]
    assert len(handles) == 18
    for connection in handles:
        assert_closed(connection)
    handles.clear()

    @contextmanager
    def legacy_scope():
        with tracked() as connection:
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(store, "connection_scope", legacy_scope)
        try:
            assert read() == result
        finally:
            for connection in handles:
                connection.close()


def test_read_exception_closes_owned_connection(store, monkeypatch):
    handles = []
    original = store.connect

    def broken():
        connection = original()
        connection.execute("DROP TABLE events")
        handles.append(connection)
        return connection

    monkeypatch.setattr(store, "connect", broken)
    with pytest.raises(sqlite3.OperationalError):
        store.max_ordinal()
    assert_closed(handles[-1])


def test_read_only_scope_observes_new_commits_without_writes(store):
    reader = EventStore(store.path, None, read_only=True)
    assert reader.get_meta("new-key") is None
    with store.connection_scope() as connection:
        connection.execute("INSERT INTO registry_meta VALUES ('new-key', 'new-value')")
    assert reader.get_meta("new-key") == "new-value"
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with reader.connection_scope() as connection:
            connection.execute("INSERT INTO registry_meta VALUES ('denied', 'yes')")
    assert_closed(connection)
    assert store.get_meta("denied") is None


def test_repeated_reads_close_even_with_gc_disabled_and_retained_handles(store, monkeypatch):
    if not os.path.isdir('/proc/self/fd'):
        pytest.skip('FD observation requires procfs')
    gc.collect()
    original = store.connect
    handles = []

    def tracked():
        connection = original()
        handles.append(connection)
        return connection

    monkeypatch.setattr(store, "connect", tracked)
    before = len(os.listdir('/proc/self/fd'))
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(256):
            assert store.max_ordinal() == 0
            assert store.get_meta('writer_epoch') == '1'
        after = len(os.listdir('/proc/self/fd'))
        assert len(handles) == 512
        for connection in handles:
            assert_closed(connection)
        assert after <= before + 2
    finally:
        for connection in handles:
            connection.close()
        if enabled:
            gc.enable()
