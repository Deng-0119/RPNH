"""Real registered resources; no models or external services."""
import inspect
import sqlite3
import pytest
from cpn.frontend.local_registry_browser import LocalRegistryBrowserProvider
from cpn.frontend.registry_browser import RegistryBrowserError
from test_local_registry_browser import make_run, publish


def provider(world):
    return LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)


def params(p):
    return dict(action='list', source_id=p.source_id, entry_type='resource_version/v1')


def test_fixed_cut_view_is_checked_twice_per_new_page(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    refs = [publish(world, key=str(i)) for i in range(3)]
    p = provider(world)
    real = p._view
    cuts = []
    def observed(cut):
        cuts.append(dict(cut))
        return real(cut)
    monkeypatch.setattr(p, '_view', observed)
    page = p.registry_browser(params(p))
    assert len(cuts) == 2 and cuts[0] == cuts[1] == page['source_cut']
    assert {e['entry_ref']['ref']['version_id'] for e in page['entries']} == {str(r.resource_version_id) for r in refs}
    cuts.clear()
    assert p.registry_browser(dict(params(p), cursor=page['page_cursor'])) == page
    assert cuts == [page['source_cut']]


def test_changed_head_during_list_is_not_returned_or_retained(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    publish(world)
    p = provider(world)
    original = p._files
    def changed(row):
        files = original(row)
        # Model replacement of the fixed head after the selected row is read.
        with world[0].event_store.connection_scope() as db:
            db.execute("UPDATE events SET event_id='event:replaced' WHERE ordinal=(SELECT MAX(ordinal) FROM events)")
        return files
    monkeypatch.setattr(p, '_files', changed)
    with pytest.raises(RegistryBrowserError) as error:
        p.registry_browser(params(p))
    assert error.value.code == 'stale_observation'
    assert p._pages == {} and p._items == {}


def test_local_direct_queries_close_connections(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    publish(world)
    p = provider(world)
    store = p._core.event_store
    real = store.connect
    owned = []
    def observed():
        db = real()
        # connection_scope's caller survives through contextlib.__enter__.
        frames = inspect.stack()
        direct = frames[1]
        caller = frames[3] if direct.function == 'connection_scope' else direct
        if caller.filename.endswith('local_registry_browser.py'):
            owned.append(db)
        return db
    monkeypatch.setattr(store, 'connect', observed)
    p.registry_browser({'action': 'catalog'})
    page = p.registry_browser(params(p))
    p.registry_browser({'action': 'record', 'handle': page['entries'][0]['handle']})
    assert owned
    for db in owned:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')


def test_append_during_page_preserves_cut_and_excludes_new_object(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    initial = publish(world)
    p = provider(world)
    original = p._files
    added = []
    def append(row):
        files = original(row)
        if not added:
            added.append(publish(world, key='later'))
        return files
    monkeypatch.setattr(p, '_files', append)
    page = p.registry_browser(params(p))
    assert len(page['entries']) == 1
    assert page['entries'][0]['entry_ref']['ref']['version_id'] == str(initial.resource_version_id)
    assert world[0].event_store.max_ordinal() > page['source_cut']['through_ordinal']


def test_local_query_exception_closes_direct_connection(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    p = provider(world)
    store = p._core.event_store
    real = store.connect
    connections = []
    def observed():
        db = real()
        connections.append(db)
        return db
    monkeypatch.setattr(store, 'connect', observed)
    monkeypatch.setattr(p, '_predicate', lambda: 'invalid SQL syntax !!!')
    with pytest.raises(sqlite3.OperationalError):
        p._catalog()
    # Original current material governance, canonical_view, then direct query.
    assert len(connections) == 3
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')

