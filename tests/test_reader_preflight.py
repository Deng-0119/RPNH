"""Finite synthetic P4 coverage; no real providers, owner execution or listener."""
from datetime import datetime, timedelta, timezone
import json
import socket

import pytest

from cpn.frontend.comparison_context import ComparisonProvider
from cpn.rpnh.collaboration import reader_preflight
from cpn.rpnh.collaboration.registry_read_contracts import (
    ExplicitSources, ReadSessionRequest, RegistryReadSessionError, SourceSelection,
)
from cpn.rpnh.collaboration.registry_read_session import (
    ExistingReadAuthorityProvider, RegistryReadHostBinding, RegistryReadSession,
    open_readonly_source, open_registry_session,
)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
from cpn.rpnh.registry.observer_access import (
    ObserverReadScope, issue_observer_access, revoke_observer_access,
)
from cpn.rpnh.registry._registry import _RegistryCore
from test_registry_read_session import owner, publish, issue, counts


def configured(tmp_path, *, kinds=('resource_version/v1',), record=True,
               material=False, export=False, unavailable=False):
    world = owner(tmp_path / 'registry')
    ref = publish(world, 'synthetic material', 'material')
    fields = {kind: tuple(TypedReaderCatalog().fields(kind)) for kind in kinds}
    context = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(fields, fields if record else {},
            (ref,) if material else (), (ref,) if export else (),
            ('synthetic-destination',) if export else ()), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='scope')
    source_ref = SourceQualifiedVersionRef(world[-1], world[2].task_ref)
    row = {'source_ref': source_ref.to_dict(), 'access_path': 'selected-observer',
           'registry_root': str(world[0].run_dir), 'binding_generation': '1',
           'observer_context': context.to_dict()}
    rows = [row]
    if unavailable:
        rows.append({**row, 'source_ref': {**row['source_ref'], 'source_id': 'configured-missing'},
                     'registry_root': str(tmp_path / 'does-not-exist')})
    path = tmp_path / 'read-host.json'
    path.write_text(json.dumps({'schema_version': 'rpnh/registry_read_host_config/v1',
                                'purpose': 'inspect', 'sources': rows}))
    path.chmod(0o600)
    return world, context, path


def forbid_product_actions(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('preflight queried objects, wrote Registry or bound a listener')
    for name in ('query_index', 'capture_cut', 'read_exact', 'read_public_projection',
                 'read_material', 'export_preflight'):
        if hasattr(RegistryReadSession, name):
            monkeypatch.setattr(RegistryReadSession, name, forbidden)
    for name in ('read_index', 'read_exact', 'read_public_projection'):
        monkeypatch.setattr(TypedReaderCatalog, name, forbidden)
    monkeypatch.setattr(_RegistryCore, 'publish_bytes', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    import cpn.rpnh.run as run
    import cpn.rpnh.registry.observer_access as access
    monkeypatch.setattr(run, 'start_run', forbidden)
    monkeypatch.setattr(access, 'issue_observer_access', forbidden)


@pytest.mark.parametrize('kind', ['resource_version/v1', 'collaboration_branch/v1'])
def test_selected_config_preflight_declarations_without_query_or_writes(tmp_path, monkeypatch, kind):
    world, _, path = configured(tmp_path, kinds=(kind,))
    before = counts(world)
    forbid_product_actions(monkeypatch)
    handles = []
    import cpn.rpnh.collaboration.read_host_config as config
    original = config.open_readonly_source
    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        handles.append(result)
        return result
    monkeypatch.setattr(config, 'open_readonly_source', tracked)
    emitted = []
    report = reader_preflight.preflight_read_host(path, emit=emitted.append)
    assert emitted == [report]
    state = report['description']['sources'][0]
    assert state['source_id'] == world[-1]
    assert state['access_path'] == 'selected-observer'
    assert state['coverage'] == {'state': 'not_queried', 'loaded_count': None, 'total_count': None}
    assert state['cut'] is None
    assert state['capabilities']['record'] == [kind]
    assert state['capabilities']['projection'] == []
    assert state['capabilities']['execution'] == report['execution'] == 'not_checked'
    assert handles and all(handle.closed for handle in handles)
    assert counts(world) == before


@pytest.mark.parametrize('record,material,export', [(False, False, False), (True, True, False), (False, False, True)])
def test_scopes_are_separate_and_unavailable_is_not_empty(tmp_path, record, material, export):
    world, _, path = configured(tmp_path, record=record, material=material, export=export, unavailable=True)
    before = counts(world)
    report = reader_preflight.preflight_read_host(path)
    state, missing = report['description']['sources']
    capabilities = state['capabilities']
    assert bool(capabilities['record']) is record
    assert capabilities['material'] is material
    assert capabilities['export'] is export
    assert capabilities['projection'] == []
    assert capabilities['execution'] == 'not_checked'
    assert missing == {'source_id': 'configured-missing', 'source_ref': None, 'cut': None,
        'access_revision': None, 'access_state': 'SOURCE_UNAVAILABLE',
        'coverage': {'state': 'unavailable', 'loaded_count': None, 'total_count': None}}
    assert counts(world) == before


def test_all_unavailable_preserves_existing_error_and_does_not_create_source(tmp_path):
    world, _, path = configured(tmp_path)
    document = json.loads(path.read_text())
    missing = tmp_path / 'missing'
    document['sources'][0]['registry_root'] = str(missing)
    path.write_text(json.dumps(document))
    emitted = []
    with pytest.raises(RegistryReadSessionError) as exc:
        reader_preflight.preflight_read_host(path, emit=emitted.append)
    assert exc.value.code == 'SOURCE_UNAVAILABLE'
    assert not emitted and not missing.exists()


@pytest.mark.parametrize('change', ['revoke', 'configuration'])
def test_change_after_describe_emits_nothing_and_closes_all(tmp_path, monkeypatch, change):
    world, context, path = configured(tmp_path)
    handles, opened = [], []
    import cpn.rpnh.collaboration.read_host_config as config
    original_open, original_describe = config.open_readonly_source, RegistryReadSession.describe
    def tracked(*args, **kwargs):
        result = original_open(*args, **kwargs)
        handles.append(result)
        return result
    def changed(session):
        opened.append(session)
        description = original_describe(session)
        if change == 'revoke':
            revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='revoke')
        else:
            document = json.loads(path.read_text())
            document['sources'][0]['binding_generation'] = '2'
            path.write_text(json.dumps(document))
        return description
    monkeypatch.setattr(config, 'open_readonly_source', tracked)
    monkeypatch.setattr(RegistryReadSession, 'describe', changed)
    emitted = []
    with pytest.raises(RegistryReadSessionError) as exc:
        reader_preflight.preflight_read_host(path, emit=emitted.append)
    assert exc.value.code == 'ACCESS_CHANGED'
    assert not emitted
    assert handles and all(handle.closed for handle in handles)
    assert opened[0]._closed and not opened[0]._cuts and not opened[0]._cursors


def test_output_failure_still_closes_session(tmp_path, monkeypatch):
    _, _, path = configured(tmp_path)
    opened = []
    original = reader_preflight.open_read_host_session
    def tracked(path):
        session = original(path)
        opened.append(session)
        return session
    monkeypatch.setattr(reader_preflight, 'open_read_host_session', tracked)
    def failed(_report):
        raise OSError('synthetic output failure')
    with pytest.raises(OSError, match='synthetic output failure'):
        reader_preflight.preflight_read_host(path, emit=failed)
    assert opened[0]._closed


def test_access_changed_during_initial_open_fails_entire_selection_and_cleans(tmp_path):
    first = owner(tmp_path / 'first', 'first')
    second = owner(tmp_path / 'second', 'second')
    worlds = {'first': first, 'second': second}
    contexts = {sid: issue(world) for sid, world in worlds.items()}
    handles = []
    def resolve(sid, _path):
        world = worlds[sid]
        result = open_readonly_source(world[0].run_dir, catalog=world[0].catalog)
        handles.append(result)
        if sid == 'second':
            revoke_observer_access(world[1], grant_ref=contexts[sid].grant_ref, command_id='initial-revoke')
        return result
    selected = tuple(SourceSelection(SourceQualifiedVersionRef(sid, world[2].task_ref), 'local')
                     for sid, world in worlds.items())
    host = RegistryReadHostBinding('test', resolve,
        ExistingReadAuthorityProvider({('test', sid, 'local'): ctx for sid, ctx in contexts.items()}), TypedReaderCatalog())
    with pytest.raises(RegistryReadSessionError) as exc:
        open_registry_session(ReadSessionRequest(ExplicitSources(selected), 'inspect'), host=host)
    assert exc.value.code == 'ACCESS_CHANGED'
    assert all(handle.closed for handle in handles)


def test_source_selection_contains_resource_and_branch_declarations_without_targets(tmp_path, monkeypatch):
    _, _, path = configured(tmp_path, kinds=('resource_version/v1', 'collaboration_branch/v1'), unavailable=True)
    session = reader_preflight.open_read_host_session(path)
    forbid_product_actions(monkeypatch)
    try:
        provider = ComparisonProvider(session)
        response = provider.comparison_selection()
        assert response['sources'] == session.describe()['sources']
        assert len(response['sources']) == 2
        assert response['targets'] == [] and response['source_cuts'] == {}
        assert response['sources'][0]['coverage']['loaded_count'] is None
    finally:
        session.close()


def test_source_without_drawable_target_still_final_rechecked_and_cache_cleared(tmp_path, monkeypatch):
    world, context, path = configured(tmp_path)
    session = reader_preflight.open_read_host_session(path)
    provider = ComparisonProvider(session)
    provider._selection_pages['retained'] = {'sensitive': 'synthetic'}
    original = session.describe
    def changed():
        result = original()
        revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='selection-revoke')
        return result
    monkeypatch.setattr(session, 'describe', changed)
    try:
        with pytest.raises(RegistryReadSessionError) as exc:
            provider.comparison_selection()
        assert exc.value.code == 'ACCESS_CHANGED'
        assert not provider._selection_pages
    finally:
        session.close()


@pytest.mark.parametrize('full_descriptor', [True, False])
def test_describe_projection_support_is_declared_scope_not_execution(tmp_path, full_descriptor):
    from test_comparison_context import runtime_world, reader
    world = runtime_world(tmp_path / 'runtime')
    if not full_descriptor:
        world['record_fields'] = {'net_instance/v1': ('net_instance_ref',)}
    session = reader(world)
    try:
        state = session.describe()['sources'][0]
        if full_descriptor:
            assert 'net_instance/v1' in state['capabilities']['projection']
            assert 'marking_checkpoint/v1' in state['capabilities']['projection']
        else:
            assert state['capabilities']['record_fields'] == {'net_instance/v1': ['net_instance_ref']}
            assert state['capabilities']['projection'] == []
        assert state['coverage']['state'] == 'not_queried'
        assert state['capabilities']['execution'] == 'not_checked'
    finally:
        session.close()


def test_actual_cli_preflight_json_has_no_query_listener_owner_or_grant(tmp_path, monkeypatch, capsys):
    from cpn import rpnh_cli
    import cpn.frontend.server as server
    world, _, path = configured(tmp_path, kinds=('resource_version/v1', 'collaboration_branch/v1'))
    before = counts(world)
    forbid_product_actions(monkeypatch)
    monkeypatch.setattr(server, 'serve_projection', lambda *_a, **_k: pytest.fail('started viewer'))
    opened = []
    original = reader_preflight.open_read_host_session
    def tracked(path):
        session = original(path)
        opened.append(session)
        return session
    monkeypatch.setattr(reader_preflight, 'open_read_host_session', tracked)
    assert rpnh_cli._net_command(['--read-host-config', str(path), '--preflight', '--format', 'json']) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert not captured.err
    assert report['schema_version'] == 'rpnh/reader_preflight/v1'
    assert report['description']['sources'][0]['coverage'] == {
        'state': 'not_queried', 'loaded_count': None, 'total_count': None}
    assert opened[0]._closed
    assert counts(world) == before


def test_actual_cli_preflight_revocation_has_empty_stdout_and_closed_session(tmp_path, monkeypatch, capsys):
    from cpn import rpnh_cli
    world, context, path = configured(tmp_path)
    opened = []
    original = RegistryReadSession.describe
    def changed(session):
        opened.append(session)
        result = original(session)
        revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='cli-revoke')
        return result
    monkeypatch.setattr(RegistryReadSession, 'describe', changed)
    with pytest.raises(RegistryReadSessionError) as exc:
        rpnh_cli._net_command(['--read-host-config', str(path), '--preflight', '--format', 'json'])
    assert exc.value.code == 'ACCESS_CHANGED'
    assert capsys.readouterr().out == ''
    assert opened[0]._closed


@pytest.mark.parametrize('options', [
    ['--show-resources'], ['--resources-only'], ['--node', 'synthetic'],
    ['--output', 'unused.json'], ['--no-open'], ['--host', '0.0.0.0'], ['--port', '8123'],
])
def test_actual_cli_rejects_projection_listener_and_output_options_before_open(tmp_path, monkeypatch, options):
    from cpn import rpnh_cli
    monkeypatch.setattr(reader_preflight, 'open_read_host_session', lambda *_a: pytest.fail('opened rejected config'))
    with pytest.raises(ValueError, match='viewer or resource-projection options'):
        rpnh_cli._net_command(['--read-host-config', str(tmp_path / 'unused.json'), '--preflight', *options])


@pytest.mark.parametrize('other_mode', ['--view', '--result-evidence'])
def test_actual_cli_preflight_mode_is_exclusive(tmp_path, other_mode):
    from cpn import rpnh_cli
    with pytest.raises(SystemExit) as exc:
        rpnh_cli._net_command(['--read-host-config', str(tmp_path / 'unused.json'), '--preflight', other_mode])
    assert exc.value.code == 2


def test_actual_cli_preflight_requires_selected_read_host(tmp_path):
    from cpn import rpnh_cli
    with pytest.raises(ValueError, match='requires --read-host-config'):
        rpnh_cli._net_command(['--run', str(tmp_path / 'unused'), '--preflight'])
