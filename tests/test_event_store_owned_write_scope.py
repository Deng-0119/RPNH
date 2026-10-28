"""Owned backend/accounting lifetimes and setup-failure cleanup."""
import gc
import sqlite3

import pytest

from cpn.rpnh.registry.event_store import EventStore, RegistryCorruptError, StaleWriterError
from cpn.rpnh.registry._event_store import backend


class TrackedConnection(sqlite3.Connection):
    fail_sql = None
    fail_close = False
    closed = False

    def execute(self, sql, *args, **kwargs):
        if self.fail_sql and sql.startswith(self.fail_sql):
            raise RuntimeError("injected SQL failure")
        return super().execute(sql, *args, **kwargs)

    def close(self):
        super().close()
        self.closed = True
        if self.fail_close:
            raise RuntimeError("injected close failure")


@pytest.fixture
def handles(monkeypatch):
    original = sqlite3.connect
    opened = []
    def connect(*args, **kwargs):
        kwargs['factory'] = TrackedConnection
        db = original(*args, **kwargs)
        opened.append(db)
        return db
    monkeypatch.setattr(backend.sqlite3, 'connect', connect)
    return opened


def test_initializer_and_writes_close_without_gc(tmp_path, handles):
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        store = EventStore(tmp_path / 'db', None)
        assert handles and all(db.closed for db in handles)
        assert store.get_or_create_meta('test', 'one') == 'one'
        assert store.get_or_create_meta('test', 'two') == 'one'
        epoch = store.writer_epoch
        assert store.rotate_writer(expected_epoch=epoch) == epoch + 1
        with pytest.raises(StaleWriterError):
            store.rotate_writer(expected_epoch=epoch)
        assert store.acquire_writer() == epoch + 2
        assert store.current_task_recovery_manifest_row() is None
        assert all(db.closed for db in handles)
    finally:
        if was_enabled:
            gc.enable()


@pytest.mark.parametrize('operation', [
    'actual_model_call_counts', 'actual_model_call_limit',
    'ordinary_model_call_limit', 'task_model_call_terminal_phase_entered',
])
def test_accounting_errors_close(tmp_path, handles, operation):
    store = EventStore(tmp_path / 'db', None)
    with pytest.raises(RegistryCorruptError, match='task identity'):
        getattr(store, operation)()
    assert all(db.closed for db in handles)


@pytest.mark.parametrize('sql', ['PRAGMA foreign_keys', 'PRAGMA synchronous'])
def test_setup_failure_closes_and_preserves_primary(tmp_path, handles, monkeypatch, sql):
    monkeypatch.setattr(TrackedConnection, 'fail_sql', sql)
    monkeypatch.setattr(TrackedConnection, 'fail_close', True)
    with pytest.raises(RuntimeError, match='injected SQL failure'):
        EventStore(tmp_path / 'db', None)
    assert handles and all(db.closed for db in handles)


def test_begin_failure_closes_and_preserves_primary(tmp_path, handles, monkeypatch):
    store = EventStore(tmp_path / 'db', None)
    monkeypatch.setattr(TrackedConnection, 'fail_sql', 'BEGIN')
    monkeypatch.setattr(TrackedConnection, 'fail_close', True)
    with pytest.raises(RuntimeError, match='injected SQL failure'):
        store.get_or_create_meta('x', 'y')
    assert all(db.closed for db in handles)


def test_close_error_visible_on_success(tmp_path, handles, monkeypatch):
    store = EventStore(tmp_path / 'db', None)
    monkeypatch.setattr(TrackedConnection, 'fail_close', True)
    with pytest.raises(RuntimeError, match='injected close failure'):
        store.get_or_create_meta('x', 'y')
    assert all(db.closed for db in handles)


def test_commit_failure_keeps_sqlite_error_over_cleanup(tmp_path, handles, monkeypatch):
    store = EventStore(tmp_path / 'db', None)
    monkeypatch.setattr(TrackedConnection, 'fail_close', True)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection_scope() as db:
            db.execute('BEGIN')
            db.execute('PRAGMA defer_foreign_keys=ON')
            db.execute("INSERT INTO outbox VALUES ('missing', 'task', 1, '[]', 'now')")
    assert all(db.closed for db in handles)


def test_repeated_writes_keep_fd_stable_with_retained_handles(tmp_path, handles):
    from pathlib import Path
    fd = Path('/proc/self/fd')
    if not fd.is_dir():
        pytest.skip('Linux fd observation unavailable')
    store = EventStore(tmp_path / 'db', None)
    before = len(list(fd.iterdir()))
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for i in range(128):
            assert store.get_or_create_meta('reused', str(i)) == '0'
        after = len(list(fd.iterdir()))
        assert all(db.closed for db in handles)
        assert after == before
    finally:
        if was_enabled:
            gc.enable()
