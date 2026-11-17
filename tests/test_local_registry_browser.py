"""Real local canonical stores and HTTP; no grants, providers or model calls."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.local_registry_browser import LocalRegistryBrowserProvider, LOCAL_REF_SCHEMA
from cpn.frontend.registry_browser import RegistryBrowserError
from cpn.frontend.server import handle_request
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import PublishResource, PrivateSystemOrigin
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.collaboration.sources import get_local_source_identity
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef
from test_registry_read_session import catalog, counts
from test_net_viewer import _running_viewer, _raw_request


def make_run(path, *, bound=False):
    core = _RegistryCore(path, create=True, catalog=catalog())
    identity = _bootstrap_identity(core, NativeBootstrapManifest(('local-browser-test/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    if bound:
        RegistryRegistrationGateway(core, identity.task_ref, bootstrap).bind_source_identity(
            source_id='actual-source', command_id='bind')
    return core, identity, bootstrap


def publish(world, payload=b'{"answer":"exact"}', key='body', descriptors=None):
    core, identity, bootstrap = world
    return _publish_private_system(core, identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(bootstrap), payload=payload, media_type='application/json',
        content_schema_ref=None, summary=key, lifetime_ref=bootstrap, descriptors=descriptors or {}, idempotency_key=key))


def request(provider, action, **kwargs):
    response = handle_request(provider, 'GET', '/api/registry-browser?' + urlencode({'action': action, **kwargs}))
    return response.status, json.loads(response.body)


def listing(provider, **kwargs):
    source = request(provider, 'catalog')[1]['sources'][0]['source_id']
    return request(provider, 'list', source_id=source, entry_type='resource_version/v1', **kwargs)


@pytest.mark.parametrize('bound', [False, True])
def test_dashboard_registry_http_exact_body_and_zero_writes(tmp_path, bound):
    world = make_run(tmp_path / 'run', bound=bound)
    raw = '{"answer":"中文", "format": [1, 2]}\n'.encode()
    ref = publish(world, raw)
    before = counts(world)
    provider = RegistryDashboard(world[0].run_dir, catalog=world[0].catalog)
    status, catalog_data = request(provider, 'catalog')
    assert status == 200
    source = catalog_data['sources'][0]
    assert source['access_mode'] == 'local_owner' and source['source_bound'] is bound
    assert 'task/v1' in source['entry_types'] and 'resource_version/v1' in source['entry_types']
    status, page = listing(provider)
    assert status == 200
    row = next(r for r in page['entries'] if r['entry_ref']['ref'].get('resource_version_id', r['entry_ref']['ref'].get('version_id')) == str(ref.resource_version_id))
    if bound:
        assert row['entry_ref'] == SourceQualifiedResourceRef(get_local_source_identity(world[0]).source_id, ref).to_dict()
    else:
        assert row['entry_ref']['schema_version'] == LOCAL_REF_SCHEMA
        assert row['entry_ref']['task_id'] == str(world[0].task_id)
        assert 'source_id' not in row['entry_ref']
    for action in ('record', 'material'):
        status, result = request(provider, action, handle=row['handle'])
        assert status == 200 and result['entry_ref'] == row['entry_ref']
        assert result['source_cut'] == page['source_cut']
    assert result['body'].encode() == raw and result['byte_count'] == len(raw)
    assert str(world[0].run_dir) not in json.dumps((catalog_data, page, result))
    assert counts(world) == before and provider._registry_browser._core.read_only
    with _running_viewer(provider) as httpd:
        headers = (('Host', httpd.authority), ('Origin', httpd.origin))
        status, body = _raw_request(httpd, headers=headers,
            path='/api/registry-browser?action=material&handle=' + row['handle'])
        assert status == 200 and json.loads(body)['body'].encode() == raw
        assert _raw_request(httpd, headers=(('Host', httpd.authority), ('Origin', 'https://foreign.example')),
            path='/api/registry-browser?action=catalog')[0] == 400
    assert handle_request(provider, 'POST', '/api/registry-browser?action=catalog').status == 405
    assert counts(world) == before


def test_local_clock_advance_and_reopen_have_no_grant_deadline(tmp_path, monkeypatch):
    import time
    import cpn.rpnh.collaboration.registry_read_session as external
    world = make_run(tmp_path / 'run', bound=True)
    publish(world)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    future = datetime.now(timezone.utc) + timedelta(days=7)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(external, 'datetime', Later)
    monkeypatch.setattr(time, 'time', lambda: future.timestamp())
    assert request(provider, 'material', handle=page['entries'][0]['handle'])[0] == 200
    reopened = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, fresh = listing(reopened)
    assert fresh['source_cut']['cut_id'] != page['source_cut']['cut_id']
    assert request(reopened, 'material', handle=fresh['entries'][0]['handle'])[0] == 200
    assert request(reopened, 'record', handle=page['entries'][0]['handle']) == (409, {'error': 'stale_observation'})


def test_pages_pin_cut_and_exclude_later_provisional_and_aborted_objects(tmp_path, monkeypatch):
    import cpn.frontend.local_registry_browser as local
    from test_canonical_head_projection import mark_firing
    monkeypatch.setattr(local, 'PAGE_SIZE', 2)
    world = make_run(tmp_path / 'run')
    refs = [publish(world, str(i).encode(), str(i)) for i in range(5)]
    store = world[0].event_store
    for ref, state in zip(refs[-2:], ('PROVISIONAL', 'SUPERSEDED')):
        row = store.canonical_object_row(ref.resource_version_id)
        firing = mark_firing(store, row['published_event_id'], row['transaction_id'])
        with store.connect() as db:
            db.execute('INSERT INTO firing_temporary_members VALUES (?,?,?,?)',
                (firing, 'object', str(ref.resource_version_id), row['transaction_id']))
            if state == 'SUPERSEDED':
                db.execute('UPDATE firing_publications SET state=?, published_transaction_id=?, marking_checkpoint_version_id=? WHERE firing_version_id=?',
                    (state, row['transaction_id'], 'marking_checkpoint_version:abort-fixture', firing))
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, first = listing(provider)
    assert len(first['entries']) == 2 and first['next_cursor']
    publish(world, b'later', 'later')
    _, second = listing(provider, cursor=first['next_cursor'])
    assert len(second['entries']) == 1 and second['next_cursor'] is None
    assert first['source_cut'] == second['source_cut']
    assert {r['entry_ref']['ref']['version_id'] for r in first['entries'] + second['entries']} == {str(r.resource_version_id) for r in refs[:3]}
    assert listing(provider, cursor=first['page_cursor']) == (200, first)
    assert request(provider, 'material', handle=first['entries'][0]['handle'])[1]['source_cut'] == first['source_cut']
    _, fresh = listing(provider)
    assert fresh['source_cut']['through_ordinal'] > first['source_cut']['through_ordinal']


@pytest.mark.parametrize('change', ['task', 'database', 'mode', 'source', 'binding', 'foreign_owner'])
def test_local_binding_changes_deny_cached_pages_and_body(tmp_path, monkeypatch, change):
    import cpn.frontend.local_registry_browser as local
    world = make_run(tmp_path / 'run')
    publish(world)
    allowed = [True]
    def binding():
        if not allowed[0]:
            raise RegistryBrowserError('access_changed')
    provider = RegistryDashboard(world[0].run_dir, catalog=world[0].catalog, binding=binding)
    _, page = listing(provider)
    db_path = world[0].event_store.path
    if change == 'task':
        with world[0].event_store.connect() as db:
            db.execute("UPDATE registry_meta SET value='task:other' WHERE key='task_id'")
    elif change == 'database':
        replacement = db_path.with_name('replacement.sqlite3')
        replacement.write_bytes(db_path.read_bytes())
        replacement.replace(db_path)
    elif change == 'mode':
        db_path.chmod(db_path.stat().st_mode ^ 0o040)
    elif change == 'source':
        RegistryRegistrationGateway(world[0], world[1].task_ref, world[2]).bind_source_identity(
            source_id='new-source', command_id='bind-after-open')
    elif change == 'binding':
        allowed[0] = False
    else:
        monkeypatch.setattr(local.os, 'getuid', lambda: db_path.stat().st_uid + 1)
    assert request(provider, 'material', handle=page['entries'][0]['handle']) == (403, {'error': 'access_changed'})
    assert not provider._registry_browser._items and not provider._registry_browser._pages


def test_foreign_owner_denied_before_registry_open(tmp_path, monkeypatch):
    import cpn.frontend.local_registry_browser as local
    world = make_run(tmp_path / 'run')
    monkeypatch.setattr(local.os, 'getuid', lambda: world[0].run_dir.stat().st_uid + 1)
    with pytest.raises(RegistryBrowserError, match='access_changed'):
        LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)


@pytest.mark.parametrize('replacement', [False, True])
def test_material_owner_or_replacement_change_denied(tmp_path, monkeypatch, replacement):
    world = make_run(tmp_path / 'run')
    ref = publish(world)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    path = world[0].object_store.path_for_version(ref.resource_version_id)
    if replacement:
        new = path.with_name('new-body')
        new.write_bytes(path.read_bytes())
        new.replace(path)
    else:
        original = Path.lstat
        def foreign(item, *args, **kwargs):
            value = original(item, *args, **kwargs)
            if item == path:
                return SimpleNamespace(st_uid=value.st_uid + 1)
            return value
        monkeypatch.setattr(Path, 'lstat', foreign)
    assert request(provider, 'material', handle=page['entries'][0]['handle']) == (403, {'error': 'access_changed'})


@pytest.mark.parametrize('raw,status,error', [(b'\xff', 415, 'invalid_utf8'), (b'x' * (256 * 1024 + 1), 413, 'scope_limit')])
def test_body_bounds(tmp_path, raw, status, error):
    world = make_run(tmp_path / 'run')
    publish(world, raw)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    assert request(provider, 'material', handle=page['entries'][0]['handle']) == (status, {'error': error})


def test_handles_source_type_and_version_are_exact(tmp_path):
    world = make_run(tmp_path / 'run', bound=True)
    publish(world)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    assert request(provider, 'list', source_id='foreign', entry_type='resource_version/v1') == (403, {'error': 'not_disclosed'})
    _, page = listing(provider)
    assert request(provider, 'list', source_id='actual-source', entry_type='task/v1', cursor=page['page_cursor']) == (409, {'error': 'stale_observation'})
    _, page = listing(provider)
    provider._items[page['entries'][0]['handle']]['ref']['source_id'] = 'foreign'
    assert request(provider, 'record', handle=page['entries'][0]['handle']) == (409, {'error': 'stale_observation'})


def test_recheck_after_body_does_not_return_changed_access(tmp_path, monkeypatch):
    world = make_run(tmp_path / 'run')
    publish(world)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    store = provider._core.object_store
    original = store.read_registered
    def read_then_change(*args, **kwargs):
        raw = original(*args, **kwargs)
        path = world[0].event_store.path
        path.chmod(path.stat().st_mode ^ 0o040)
        return raw
    monkeypatch.setattr(store, 'read_registered', read_then_change)
    assert request(provider, 'material', handle=page['entries'][0]['handle']) == (403, {'error': 'access_changed'})


def test_record_is_exact_registered_metadata_without_backend_envelope(tmp_path):
    world = make_run(tmp_path / 'run')
    ref = publish(world, descriptors={'path': '/domain/user/document', 'profile': 'user-authored profile'})
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=world[0].catalog)
    _, page = listing(provider)
    status, record = request(provider, 'record', handle=page['entries'][0]['handle'])
    expected = json.loads(world[0].event_store.canonical_object_row(ref.resource_version_id)['metadata_json'])
    assert status == 200 and record['fields'] == expected
    assert record['fields']['descriptors']['path'] == '/domain/user/document'
    assert record['fields']['descriptors']['profile'] == 'user-authored profile'
    assert 'storage_locator' not in record['fields']


def test_old_observer_types_are_explicitly_unsupported_by_ordinary_catalog(tmp_path):
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from test_registry_read_session import owner, issue
    world = owner(tmp_path / 'run')
    issue(world)
    provider = LocalRegistryBrowserProvider(world[0].run_dir, catalog=agent_task_catalog())
    status, data = request(provider, 'catalog')
    assert status == 200
    source = data['sources'][0]
    assert source['unsupported_entry_types']
    for kind in source['unsupported_entry_types']:
        assert kind not in source['entry_types']
        assert request(provider, 'list', source_id=source['source_id'], entry_type=kind) == (501, {'error': 'unsupported'})
    assert request(provider, 'list', source_id=source['source_id'], entry_type='task/v1')[0] == 200


def test_registry_owner_restriction_does_not_block_existing_pn_viewer(tmp_path, monkeypatch):
    import cpn.frontend.dashboard as dashboard
    import cpn.frontend.local_registry_browser as local
    from test_net_viewer import _projection
    world = make_run(tmp_path / 'run')
    attempts = []
    def denied(*args, **kwargs):
        attempts.append(True)
        raise RegistryBrowserError('access_changed')
    monkeypatch.setattr(local, 'LocalRegistryBrowserProvider', denied)
    monkeypatch.setattr(dashboard, 'project_registry_net', lambda *args, **kwargs: _projection())
    provider = RegistryDashboard(world[0].run_dir, catalog=world[0].catalog)
    assert not attempts
    assert handle_request(provider, 'GET', '/api/v1/net').status == 200
    assert not attempts
    assert request(provider, 'catalog') == (403, {'error': 'access_changed'})
    assert attempts == [True]
    assert handle_request(provider, 'GET', '/api/v1/net').status == 200


def test_lazy_browser_cannot_switch_dashboard_task_before_first_read(tmp_path):
    world = make_run(tmp_path / 'run')
    provider = RegistryDashboard(world[0].run_dir, catalog=world[0].catalog)
    from cpn.rpnh.registry.identities import new_id
    with world[0].event_store.connect() as db:
        db.execute("UPDATE registry_meta SET value=? WHERE key='task_id'", (str(new_id('task')),))
    assert request(provider, 'catalog') == (403, {'error': 'access_changed'})

