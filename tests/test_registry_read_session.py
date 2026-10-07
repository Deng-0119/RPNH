"""Deterministic functional read-session regressions; no external credentials.

Synthetic owner fixtures call the actual canonical issuer. This is ordinary
implementation coverage, not the separate independent adversarial authority audit.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PublishResource, PrivateSystemOrigin
from cpn.rpnh.registry.observer_access import (ObserverReadScope, RegistryReadObserverContext,
    issue_observer_access, revoke_observer_access, observer_access_schema_data)
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.registry_read_contracts import (ReadSessionRequest, ExplicitSources, SourceSelection,
    ReadLimits, IndexQuery, TypedIndexClause, TypedPredicate, RegistryReadSessionError)
from cpn.rpnh.collaboration.registry_read_session import (RegistryReadHostBinding, ExistingReadAuthorityProvider,
    open_registry_session, open_readonly_source)


def catalog():
    schemas, types, paths = observer_access_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


def owner(path, source='source-a'):
    core = _RegistryCore(path, create=True, catalog=catalog())
    identity = _bootstrap_identity(core, NativeBootstrapManifest(('registry-read-functional/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id=source, command_id='bind')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    value = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Synthetic observer'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(value), metadata=value, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='principal')
    return core, gateway, identity, bootstrap, principal, source


def publish(world, text, key):
    core, gateway, identity, bootstrap, principal, source = world
    payload = text if isinstance(text, bytes) else text.encode()
    ref = _publish_private_system(core, identity.task_ref, PublishResource(origin=PrivateSystemOrigin(bootstrap),
        payload=payload, media_type='text/plain', content_schema_ref=None,
        summary=text if isinstance(text, str) else 'Synthetic binary material', lifetime_ref=bootstrap,
        descriptors={'content_sha256': hashlib.sha256(payload).hexdigest()}, idempotency_key=key))
    return SourceQualifiedResourceRef(source, ref)


def issue(world, *, index=True, record=True, material=(), export=(), destinations=(), seconds=3600, command='issue'):
    core, gateway, identity, bootstrap, principal, source = world
    fields = tuple(TypedReaderCatalog().fields('resource_version/v1'))
    scope = ObserverReadScope({'resource_version/v1': fields} if index else {},
        {'resource_version/v1': fields} if record else {}, tuple(material), tuple(export), tuple(destinations))
    return issue_observer_access(gateway, principal_ref=principal, scope=scope, purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(), command_id=command)


def session(world, context, *, limits=ReadLimits(), reader_catalog=None, generation=None):
    core, gateway, identity, bootstrap, principal, source = world
    selection = SourceSelection(SourceQualifiedVersionRef(source, identity.task_ref), 'local')
    def resolver(source_id, access_path):
        assert (source_id, access_path) == (source, 'local')
        return open_readonly_source(core.run_dir, catalog=core.catalog, binding_generation='1' if generation is None else generation[0])
    host = RegistryReadHostBinding('verified:test', resolver,
        ExistingReadAuthorityProvider({('verified:test', source, 'local'): context}), reader_catalog or TypedReaderCatalog(), limits=limits)
    return open_registry_session(ReadSessionRequest(ExplicitSources((selection,)), 'inspect', limits), host=host)


def counts(world):
    core = world[0]
    with core.event_store.connect() as db:
        return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                     ('objects', 'events', 'transactions', 'relations')) + (core.event_store.writer_epoch,)


@pytest.fixture
def world(tmp_path):
    result = owner(tmp_path / 'registry')
    for i in range(4):
        publish(result, 'item-' + str(i), 'item:' + str(i))
    return result


def query(world, size=2):
    return IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1', projection=('summary', 'byte_count')),), size)


def test_independent_issuer_query_paging_replay_close_zero_writes(world):
    context = issue(world)
    before = counts(world)
    reader = session(world, context)
    assert reader.describe()['global_atomic_snapshot'] is False
    first = reader.query_index(query(world))
    assert len(first['entries']) == 2 and first['continuation']
    second = reader.query_index(query(world), cursor=first['continuation'])
    assert second == reader.query_index(query(world), cursor=first['continuation'])
    assert second['continuation'] is None
    assert {x['entry_ref']['ref']['resource_version_id'] for x in first['entries']}.isdisjoint(
        x['entry_ref']['ref']['resource_version_id'] for x in second['entries'])
    assert counts(world) == before
    assert reader.close() == reader.close() == {'status': 'closed'}
    with pytest.raises(RegistryReadSessionError, match='session closed'):
        reader.query_index(query(world))
    assert counts(world) == before


def test_append_preserves_cut_and_fresh_query_sees_new(world):
    reader = session(world, issue(world))
    first = reader.query_index(query(world))
    old_cut = reader.validate_cut(first['entries'][0]['source_cut'])
    added = publish(world, 'after-cut', 'later')
    second = reader.query_index(query(world), cursor=first['continuation'])
    assert second['entries'][0]['source_cut'] == old_cut.to_dict()
    assert second['coverage']['total_count'] == 4
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=added, at_cut=old_cut)
    assert error.value.code == 'NOT_PRESENT_AT_CUT'
    fresh = reader.query_index(query(world, 100))
    assert fresh['coverage']['total_count'] == 5


def test_permissions_are_independent_and_material_bounded(world):
    ref = publish(world, 'body', 'body')
    reader = session(world, issue(world, record=False))
    cut = reader.capture_cut(world[-1])
    assert reader.query_index(query(world))['entries']
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=ref, at_cut=cut)
    assert error.value.code == 'NOT_DISCLOSED'
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut)
    assert error.value.code == 'MATERIAL_ACCESS_NOT_GRANTED'
    body_reader = session(world, issue(world, index=False, record=False, material=(ref,), command='body-grant'))
    body_cut = body_reader.capture_cut(world[-1])
    assert body_reader.read_material(resource_ref=ref, at_cut=body_cut)['bytes'] == b'body'
    with pytest.raises(RegistryReadSessionError) as error:
        body_reader.read_material(resource_ref=ref, at_cut=body_cut, max_bytes=3)
    assert error.value.code == 'MATERIAL_TOO_LARGE'
    with pytest.raises(RegistryReadSessionError) as error:
        body_reader.authorize_export((ref,), destination='local-package', at_cuts={world[-1]: body_cut})
    assert error.value.code == 'EXPORT_ACCESS_NOT_GRANTED'


def test_material_utf8_preserves_default_bytes_and_byte_bounds(world):
    text = 'Registry: \u03c0 / \u4e2d\u6587 / \U0001f680\n'
    ref = publish(world, text, 'utf8-material')
    reader = session(world, issue(world, material=(ref,)))
    cut = reader.capture_cut(world[-1])
    before = counts(world)
    legacy = reader.read_material(resource_ref=ref, at_cut=cut)
    assert reader.read_material(resource_ref=ref, at_cut=cut, representation='bytes') == legacy
    assert legacy['bytes'] == text.encode('utf-8')
    assert legacy['representation'] == 'bytes' and legacy['body'] == legacy['bytes']
    assert legacy['material_digest'] == legacy['sha256'] == hashlib.sha256(text.encode('utf-8')).hexdigest()
    assert legacy['content_schema_ref'] is None
    decoded = reader.read_material(resource_ref=ref, at_cut=cut,
        representation='utf8', max_bytes=len(legacy['bytes']))
    assert decoded['body'] == text and decoded['representation'] == 'utf8'
    assert decoded['bytes'] == legacy['bytes']
    assert {key: value for key, value in decoded.items() if key not in {'body', 'representation'}} == {
        key: value for key, value in legacy.items() if key not in {'body', 'representation'}}
    assert decoded['byte_count'] == len(text.encode('utf-8')) > len(text)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut, representation='utf8',
            max_bytes=len(legacy['bytes']) - 1)
    assert error.value.code == 'MATERIAL_TOO_LARGE'
    assert counts(world) == before


@pytest.mark.parametrize('payload', [b'\xff', b'\xe4\xb8'])
def test_material_utf8_invalid_encoding_is_typed_error_not_replacement(world, payload):
    ref = publish(world, payload, 'invalid-utf8-material')
    reader = session(world, issue(world, material=(ref,)))
    cut = reader.capture_cut(world[-1])
    before = counts(world)
    binary = reader.read_material(resource_ref=ref, at_cut=cut)
    assert binary['body'] == binary['bytes'] == payload
    assert binary['material_digest'] == binary['sha256'] == hashlib.sha256(payload).hexdigest()
    assert binary['content_schema_ref'] is None
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut, representation='utf8')
    assert error.value.code == 'INVALID_UTF8'
    assert error.value.to_dict()['message'] == 'invalid utf8'
    assert counts(world) == before


@pytest.mark.parametrize('representation', ['latin1', 'UTF8', None, 1, []])
def test_material_invalid_representation_rejected_before_payload_read(world, monkeypatch, representation):
    import cpn.rpnh.collaboration.registry_typed_readers as readers
    ref = publish(world, 'body', 'representation-material')
    reader = session(world, issue(world, material=(ref,)))
    cut = reader.capture_cut(world[-1])
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid representation must not read payload')
    monkeypatch.setattr(readers, 'payload_at', forbidden)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut, representation=representation)
    assert error.value.code == 'INVALID_REPRESENTATION'


def test_material_body_contract_uses_registered_content_schema(world):
    from cpn.rpnh.collaboration.share_packages import DIGEST
    import re
    ref = SourceQualifiedResourceRef(world[-1], world[1].bind_builtin_schema('rpnh/module_declaration/v1'))
    reader = session(world, issue(world, material=(ref,)))
    cut = reader.capture_cut(world[-1])
    before = counts(world)
    header = reader.read_exact(entry_ref=ref, at_cut=cut)['record']
    for representation in ('bytes', 'utf8'):
        result = reader.read_material(resource_ref=ref, at_cut=cut, representation=representation)
        assert result['content_schema_ref'] == header['content_schema_ref'] == 'registry_v1/registry_type_catalog/v1'
        assert result['content_schema_ref'] != 'registry_v1/resource_version/v1'
        assert re.fullmatch(DIGEST, result['material_digest'])
        assert result['material_digest'] == result['sha256'] == hashlib.sha256(result['bytes']).hexdigest()
        assert json.loads(result['body'])['$id'] == 'rpnh/module_declaration/v1'
    assert counts(world) == before


def test_material_utf8_preserves_resource_type_permission_and_integrity_checks(world):
    ref = publish(world, 'body', 'utf8-authority-material')
    metadata = session(world, issue(world))
    metadata_cut = metadata.capture_cut(world[-1])
    with pytest.raises(RegistryReadSessionError) as error:
        metadata.read_material(resource_ref=ref, at_cut=metadata_cut, representation='utf8')
    assert error.value.code == 'MATERIAL_ACCESS_NOT_GRANTED'
    reader = session(world, issue(world, material=(ref,), command='utf8-body-grant'))
    cut = reader.capture_cut(world[-1])
    with pytest.raises(TypeError, match='source-qualified resource reference'):
        reader.read_material(resource_ref=SourceQualifiedVersionRef(world[-1], world[2].task_ref),
            at_cut=cut, representation='utf8')
    # Corrupt bytes must fail integrity before any UTF-8 decoding is attempted.
    world[0].object_store.path_for_version(ref.ref.resource_version_id).write_bytes(b'\xffody')
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut, representation='utf8')
    assert error.value.code == 'INTEGRITY_FAILED'


def test_forged_context_missing_authority_and_field_query_fail_closed(world):
    context = issue(world)
    with pytest.raises(RegistryReadSessionError):
        session(world, replace(context, reader_fence='self-signed'))
    reader = session(world, context)
    malformed = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1',
        (TypedPredicate('storage_locator', 'eq', '/secret'),), ('summary',)),))
    with pytest.raises(RegistryReadSessionError) as error:
        reader.query_index(malformed)
    assert error.value.code == 'INVALID_PROJECTION'
    page = reader.query_index(query(world))
    with pytest.raises(RegistryReadSessionError) as error:
        reader.query_index(query(world, 3), cursor=page['continuation'])
    assert error.value.code == 'CURSOR_MISMATCH'
    altered = dict(page['entries'][0]['source_cut']); altered['head'] = {**altered['head'], 'ordinal': 0}
    with pytest.raises(RegistryReadSessionError):
        reader.validate_cut(altered)


@pytest.mark.parametrize('change', ['revoke', 'fence', 'binding', 'expiry', 'catalog'])
def test_final_recheck_discard_during_callback(world, change):
    context = issue(world)
    generation = ['1']
    cat = TypedReaderCatalog()
    reader = session(world, context, reader_catalog=cat, generation=generation)
    first = reader.query_index(query(world))
    ref = SourceQualifiedResourceRef.from_dict(first['entries'][0]['entry_ref'], catalog=world[0].catalog)
    original = cat.read_exact
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        if change == 'revoke': revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='revoke')
        elif change == 'fence': world[0].rotate_writer()
        elif change == 'binding': generation[0] = '2'
        elif change == 'expiry': reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        else: cat.version = 'changed'
        return value
    cat.read_exact = changed
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=ref, at_cut=first['entries'][0]['source_cut'])
    assert error.value.code in {'ACCESS_CHANGED', 'SESSION_EXPIRED'}
    assert not reader._cuts and not reader._cursors


def test_budget_applied_before_history_hydration(world, monkeypatch):
    context = issue(world)
    def forbidden(*args, **kwargs):
        raise AssertionError('must reject before legacy unbounded hydration')
    monkeypatch.setattr(world[0].event_store, 'canonical_events', forbidden)
    with pytest.raises(RegistryReadSessionError) as error:
        session(world, context, limits=ReadLimits(max_scan_rows=1))
    assert error.value.code == 'LIMIT_EXCEEDED'


def test_second_process_reopen_from_existing_context_zero_writes(world, tmp_path):
    context = issue(world)
    before = counts(world)
    config = tmp_path / 'context.json'
    config.write_text(json.dumps({'path': str(world[0].run_dir), 'context': context.to_dict(),
        'source': world[-1], 'task': SourceQualifiedVersionRef(world[-1], world[2].task_ref).to_dict()}))
    code = '''import json,sys
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from cpn.rpnh.registry.observer_access import observer_access_schema_data,RegistryReadObserverContext
from cpn.rpnh.collaboration.registry_read_session import *
from cpn.rpnh.collaboration.registry_read_contracts import *
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
v=json.load(open(sys.argv[1])); s,t,p=observer_access_schema_data(); cat=SchemaCatalog(schemas=s,types=t,schema_paths=p)
source=SourceQualifiedVersionRef.from_dict(v['task'],catalog=cat)
host=RegistryReadHostBinding('verified:test',lambda a,b:open_readonly_source(v['path'],catalog=cat),ExistingReadAuthorityProvider({('verified:test',v['source'],'local'):RegistryReadObserverContext.from_dict(v['context'])}),TypedReaderCatalog())
reader=open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(source,'local'),)),'inspect'),host=host)
page=reader.query_index(IndexQuery((v['source'],),(TypedIndexClause('resource_version/v1'),)))
print(json.dumps({'count':len(page['entries']),'complete':page['coverage']['state']}));reader.close()
'''
    result = subprocess.run([sys.executable, '-c', code, str(config)], capture_output=True, text=True,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'count': 4, 'complete': 'complete'}
    assert counts(world) == before


def test_current_schema_fingerprint_and_final_ttl_rechecked(world):
    context = issue(world)
    reader = session(world, context)
    cut = reader.capture_cut(world[-1])
    original = reader._host.authority_provider.resolve_existing
    def expire_at_last_revalidation(*args, **kwargs):
        result = original(*args, **kwargs)
        reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return result
    reader._host.authority_provider.resolve_existing = expire_at_last_revalidation
    with pytest.raises(RegistryReadSessionError) as error:
        reader.final_recheck()
    assert error.value.code == 'SESSION_EXPIRED'
    other = session(world, context)
    schema = world[0].catalog._validator('registry_v1/registry_observer_profile/v2').schema
    schema['description'] = 'Changed reader contract bytes'
    with pytest.raises(RegistryReadSessionError) as error:
        other.validate_cut(other.capture_cut(world[-1]))
    assert error.value.code == 'ACCESS_CHANGED'


def test_scope_field_filter_cannot_probe_undisclosed_values(world):
    scope = ObserverReadScope({'resource_version/v1': ('byte_count',)}, {'resource_version/v1': ('byte_count',)})
    context = issue_observer_access(world[1], principal_ref=world[4], scope=scope, purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='narrow')
    reader = session(world, context)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.query_index(IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1',
            (TypedPredicate('summary', 'eq', 'item-0'),), ('byte_count',)),)))
    assert error.value.code == 'NOT_DISCLOSED'
    cut = reader.capture_cut(world[-1])
    page = reader.query_index(IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1', projection=('byte_count',)),)))
    ref = SourceQualifiedResourceRef.from_dict(page['entries'][0]['entry_ref'], catalog=world[0].catalog)
    exact = reader.read_exact(entry_ref=ref, at_cut=cut, projection=('byte_count',))
    assert set(exact['record']) == {'byte_count'}


def test_corrupt_material_never_returns_payload(world):
    ref = publish(world, 'body', 'checksum-body')
    reader = session(world, issue(world, material=(ref,)))
    cut = reader.capture_cut(world[-1])
    path = world[0].object_store.path_for_version(ref.ref.resource_version_id)
    path.write_bytes(b'fail')
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


def test_explicit_export_permission_is_destination_bound(world):
    ref = publish(world, 'body', 'export-body')
    reader = session(world, issue(world, material=(ref,), export=(ref,), destinations=('local-package',)))
    cut = reader.capture_cut(world[-1])
    assert reader.authorize_export((ref,), destination='local-package', at_cuts={world[-1]: cut}) is None
    with pytest.raises(RegistryReadSessionError) as error:
        reader.authorize_export((ref,), destination='different-place', at_cuts={world[-1]: cut})
    assert error.value.code == 'EXPORT_ACCESS_NOT_GRANTED'
    with pytest.raises(RegistryReadSessionError) as error:
        reader.capture_observation()
    assert error.value.code == 'UNSUPPORTED_OBSERVATION_CAPTURE'


def test_partial_sources_keep_gaps_not_zero_and_revoke_discards_source(world, tmp_path):
    second = owner(tmp_path / 'other', 'source-b')
    publish(second, 'other', 'item')
    contexts = {world[-1]: issue(world), second[-1]: issue(second)}
    owners = {world[-1]: world, second[-1]: second}
    unavailable = set()
    selections = tuple(SourceSelection(SourceQualifiedVersionRef(s, w[2].task_ref), 'local') for s,w in owners.items())
    def resolve(source, access_path):
        if source in unavailable:
            raise RegistryReadSessionError('SOURCE_UNAVAILABLE')
        return open_readonly_source(owners[source][0].run_dir, catalog=owners[source][0].catalog)
    host = RegistryReadHostBinding('verified:test', resolve,
        ExistingReadAuthorityProvider({('verified:test', s, 'local'): c for s,c in contexts.items()}), TypedReaderCatalog())
    reader = open_registry_session(ReadSessionRequest(ExplicitSources(selections), 'inspect'), host=host)
    complete = reader.query_index(IndexQuery(tuple(owners), (TypedIndexClause('resource_version/v1'),), 100))
    assert complete['coverage']['state'] == 'complete' and complete['global_atomic_snapshot'] is False
    unavailable.add(world[-1])
    page = reader.query_index(IndexQuery(tuple(owners), (TypedIndexClause('resource_version/v1'),), 100))
    assert page['coverage']['state'] == 'partial' and page['coverage']['total_count'] is None
    assert {x['entry_ref']['source_id'] for x in page['entries']} == {second[-1]}
    failed = next(x for x in page['source_results'] if x['source_id'] == world[-1])
    assert failed['coverage']['loaded_count'] is failed['coverage']['total_count'] is None
    assert failed['cut'] is failed['access_revision'] is None


def test_owner_issuer_is_idempotent_and_namespace_cannot_be_retyped(world):
    from cpn.rpnh.registry.event_store import RegistryConflict
    context = issue(world)
    scope = ObserverReadScope({'resource_version/v1': tuple(TypedReaderCatalog().fields('resource_version/v1'))},
                              {'resource_version/v1': tuple(TypedReaderCatalog().fields('resource_version/v1'))})
    before = counts(world)
    repeated = issue_observer_access(world[1], principal_ref=world[4], scope=scope, purpose='inspect',
        expires_at=context.expires_at, command_id='issue')
    assert repeated == context and counts(world) == before
    from cpn.rpnh.registry.schema_catalog import TypeDefinition
    probe = 'observer_namespace_probe/v1'
    schema = 'registry_v1/' + probe
    world[0].catalog.register_schema(schema, {'$schema': 'http://json-schema.org/draft-07/schema#',
        '$id': schema, 'type': 'object', 'additionalProperties': False})
    world[0].catalog.register_type(TypeDefinition(probe, 'object', 'test', schema, None,
        'task-scoped', 'permanent', 'test', 'test'))
    with pytest.raises(RegistryConflict):
        world[0].publish_bytes(object_type=probe, logical_id=context.grant_ref.entity_id,
            version_id=new_id('resource_version'), payload=b'{}', metadata={},
            media_type='application/json', schema_ref=schema, idempotency_key='retype-observer')
    assert counts(world) == before


def test_selected_source_set_is_scope_only_and_mixed_index(tmp_path):
    from cpn.rpnh.collaboration.schema_catalog import source_set_schema_data
    from cpn.rpnh.collaboration.source_sets import SourceMember
    from cpn.rpnh.collaboration.registry_read_contracts import SelectedSourceSet
    schemas, types, paths = observer_access_schema_data()
    other_schemas, other_types, other_paths = source_set_schema_data()
    schemas.update(other_schemas); paths.update(other_paths)
    combined = SchemaCatalog(schemas=schemas, types=tuple({x.name: x for x in (*types,*other_types)}.values()), schema_paths=paths)
    core = _RegistryCore(tmp_path / 'manifest', create=True, catalog=combined)
    identity = _bootstrap_identity(core, NativeBootstrapManifest(('read-source-set-fixture/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id='manifest', command_id='bind')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    value = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Observer'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(value), metadata=value, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='principal')
    selected = SourceSelection(SourceQualifiedVersionRef('manifest', identity.task_ref), 'local')
    source_set = gateway.publish_source_set(members=(SourceMember(selected.source_ref, ('local',)),), command_id='set')
    local_world = (core,gateway,identity,bootstrap,principal,'manifest')
    publish(local_world,'body','body')
    cat = TypedReaderCatalog()
    types = ('resource_version/v1', 'collaboration_source_set/v1')
    scope = ObserverReadScope({x: tuple(cat.fields(x)) for x in types}, {x: tuple(cat.fields(x)) for x in types})
    context = issue_observer_access(gateway, principal_ref=principal, scope=scope, purpose='inspect',
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(), command_id='issue')
    host = RegistryReadHostBinding('caller', lambda *_:open_readonly_source(core.run_dir,catalog=core.catalog),
        ExistingReadAuthorityProvider({('caller','manifest','local'):context}), cat,
        source_set_resolver=lambda _:open_readonly_source(core.run_dir,catalog=core.catalog))
    reader = open_registry_session(ReadSessionRequest(SelectedSourceSet(source_set.source_set_ref,(selected,)), 'inspect'), host=host)
    before = counts(local_world)
    page = reader.query_index(IndexQuery(('manifest',), tuple(TypedIndexClause(x) for x in types)))
    assert {x['entry_type'] for x in page['entries']} == set(types)
    assert counts(local_world) == before
    outsider = replace(selected, access_path='outside')
    with pytest.raises(RegistryReadSessionError) as error:
        open_registry_session(ReadSessionRequest(SelectedSourceSet(source_set.source_set_ref,(outsider,)), 'inspect'), host=host)
    assert error.value.code == 'NOT_DISCLOSED'


def test_public_config_opens_real_context_in_second_process(world, tmp_path):
    context = issue(world)
    config = tmp_path / 'trusted-host.json'
    config.write_text(json.dumps({'schema_version': 'rpnh/registry_read_host_config/v1', 'purpose': 'inspect', 'sources': [{
        'source_ref': SourceQualifiedVersionRef(world[-1], world[2].task_ref).to_dict(), 'access_path': 'local',
        'registry_root': str(world[0].run_dir), 'binding_generation': '1', 'observer_context': context.to_dict()}]}))
    config.chmod(0o600)
    code = '''from cpn.rpnh.collaboration.read_host_config import open_read_host_session
from cpn.rpnh.collaboration.registry_read_contracts import IndexQuery,TypedIndexClause
import json,sys
s=open_read_host_session(sys.argv[1]);p=s.query_index(IndexQuery(('source-a',),(TypedIndexClause('resource_version/v1'),)))
print(json.dumps({'count':len(p['entries']),'state':p['coverage']['state']}));s.close()
'''
    before = counts(world)
    result = subprocess.run([sys.executable, '-c', code, str(config)], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'count':4,'state':'complete'}
    assert counts(world) == before


def test_cursor_replay_does_not_consume_another_cursor_slot(world):
    reader = session(world, issue(world), limits=ReadLimits(max_cursors=2))
    first = reader.query_index(query(world, 1))
    second = reader.query_index(query(world, 1), cursor=first['continuation'])
    assert second == reader.query_index(query(world, 1), cursor=first['continuation'])


def test_read_contract_wire_schemas_are_inert_and_exact(world):
    from cpn.rpnh.collaboration.registry_read_contracts import registry_read_contract_schema_data
    schemas, types, paths = registry_read_contract_schema_data()
    assert not types
    contract_catalog = SchemaCatalog(schemas=schemas, schema_paths=paths)
    reader = session(world, issue(world))
    cut = reader.capture_cut(world[-1])
    contract_catalog.validate_schema_ref('rpnh/registry_source_cut/v1', cut.to_dict())
    contract_catalog.validate_schema_ref('rpnh/registry_read_session_request/v1', reader._request.to_dict())
    contract_catalog.validate_schema_ref('rpnh/registry_index_query/v1', query(world).to_dict())
    assert set(cut.to_dict()['head']) == {'ordinal', 'writer_fencing_epoch'}
    assert 'task_control_sequence' not in json.dumps(reader.describe())
    assert str(world[0].run_dir) not in json.dumps(reader.describe())


def test_retained_snapshot_budget_rejects_before_second_fetch(world, monkeypatch):
    context = issue(world)
    reader = session(world, context)
    cut = reader.capture_cut(world[-1])
    size = reader._cuts[cut.cut_id][1].budget_bytes
    reader.limits = replace(reader.limits, max_scan_bytes=size + 1)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.capture_cut(world[-1])
    assert error.value.code == 'LIMIT_EXCEEDED'
    assert len(reader._cuts) == 1


def test_late_success_promotion_cannot_rewrite_historical_cut(tmp_path, monkeypatch):
    """Actual synthetic Start/products/Success; no executor/provider invocation."""
    import test_source_set_query as fixture_module
    original_schema_data = fixture_module.source_observation_schema_data
    def composite():
        schemas, types, paths = original_schema_data()
        add_schemas, add_types, add_paths = observer_access_schema_data()
        schemas.update(add_schemas); paths.update(add_paths)
        return schemas, tuple({x.name:x for x in (*types,*add_types)}.values()), paths
    monkeypatch.setattr(fixture_module, 'source_observation_schema_data', composite)
    run_owner, contexts = fixture_module._owner(tmp_path / 'run', 'promoted-source')
    core = run_owner._core
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    synthetic = (core, run_owner.schema_gateway, run_owner.identity, bootstrap, run_owner.principal_ref, 'promoted-source')
    execution = run_owner.start(run_owner.query_admissions['main'], command_id='synthetic:start')
    products = run_owner.products(execution, outcome_id='complete',
        products={'left.result': (canonical_json('synthetic late product'),)}, command_id='synthetic:products')
    reader = session(synthetic, issue(synthetic, command='before-success'))
    before_page = reader.query_index(query(synthetic, 1000))
    old_cut = reader.validate_cut(before_page['source_results'][0]['cut'])
    before_refs = {canonical_json(x['entry_ref']) for x in before_page['entries']}
    run_owner.succeed(products, command_id='synthetic:success')
    refreshed = session(synthetic, issue(synthetic, command='after-success'))
    old_again = refreshed.capture_cut(synthetic[-1], ordinal=old_cut.head.ordinal)
    old_query = replace(query(synthetic, 1000), cuts={synthetic[-1]:old_again})
    historical = refreshed.query_index(old_query)
    assert {canonical_json(x['entry_ref']) for x in historical['entries']} == before_refs
    current = refreshed.query_index(query(synthetic, 1000))
    assert len(current['entries']) > len(historical['entries'])


def test_public_commit_ordinal_is_the_committed_transaction(world):
    reader = session(world, issue(world))
    page = reader.query_index(IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1', projection=('commit_ordinal',)),)))
    with world[0].event_store.connect() as db:
        for entry in page['entries']:
            version = entry['entry_ref']['ref']['resource_version_id']
            row = db.execute("SELECT e.ordinal FROM objects o JOIN events e ON e.transaction_id=o.transaction_id WHERE o.version_id=? AND e.event_type='transaction_committed/v1'", (version,)).fetchone()
            assert entry['fields']['commit_ordinal'] == row[0]


def test_index_final_revoke_discards_cached_rows_and_counts(world):
    context = issue(world)
    cat = TypedReaderCatalog()
    reader = session(world, context, reader_catalog=cat)
    original = cat.read_index
    revoked = []
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        if not revoked:
            revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='index-revoke')
            revoked.append(True)
        return value
    cat.read_index = changed
    page = reader.query_index(query(world))
    assert not page['entries'] and page['continuation'] is None
    assert page['coverage']['state'] == 'partial' and page['coverage']['total_count'] is None
    assert page['source_results'][0]['coverage']['loaded_count'] is None
    assert not reader._cuts and not reader._cursors


@pytest.mark.parametrize('representation', ['bytes', 'utf8'])
def test_material_final_revoke_does_not_deliver_bytes(world, monkeypatch, representation):
    import cpn.rpnh.collaboration.registry_typed_readers as readers
    ref = publish(world, 'secret', 'material-final')
    context = issue(world, material=(ref,))
    reader = session(world, context)
    cut = reader.capture_cut(world[-1])
    original = readers.payload_at
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        revoke_observer_access(world[1], grant_ref=context.grant_ref, command_id='material-revoke')
        return value
    monkeypatch.setattr(readers, 'payload_at', changed)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=cut, representation=representation)
    assert error.value.code == 'ACCESS_CHANGED'


def test_missing_authority_configuration_never_falls_back_to_files(world):
    selected = SourceSelection(SourceQualifiedVersionRef(world[-1], world[2].task_ref), 'local')
    host = RegistryReadHostBinding('caller', lambda *_: open_readonly_source(world[0].run_dir, catalog=world[0].catalog),
        ExistingReadAuthorityProvider({}), TypedReaderCatalog())
    before = counts(world)
    with pytest.raises(RegistryReadSessionError) as error:
        open_registry_session(ReadSessionRequest(ExplicitSources((selected,)), 'inspect'), host=host)
    assert error.value.code == 'AUTHORITY_NOT_CONFIGURED'
    assert counts(world) == before


def test_wrong_exact_identity_and_nonregistry_identity_are_rejected(world):
    ref = publish(world, 'original', 'exact-original')
    reader = session(world, issue(world))
    cut = reader.capture_cut(world[-1])
    from cpn.rpnh.registry.resources import ResourceVersionRef
    wrong = SourceQualifiedResourceRef(world[-1], ResourceVersionRef(new_id('resource'), ref.ref.resource_version_id))
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=wrong, at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=SourceQualifiedResourceRef('other-source', ref.ref), at_cut=cut)
    assert error.value.code == 'NOT_DISCLOSED'
    for invalid in ('/some/path', 'sha256:' + '0' * 64, {'entry_id': 'package-entry'}):
        with pytest.raises(TypeError):
            reader.read_exact(entry_ref=invalid, at_cut=cut)


def test_managed_invocation_cannot_bypass_governed_material_delivery(tmp_path):
    import test_source_set_query as fixture_module
    run_owner, contexts = fixture_module._owner(tmp_path / 'run', 'managed-source')
    core = run_owner._core
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    synthetic = (core, run_owner.schema_gateway, run_owner.identity, bootstrap, run_owner.principal_ref, 'managed-source')
    reader = session(synthetic, contexts['main'])
    page = reader.query_index(query(synthetic, 1000))
    assert page['entries']
    ref = SourceQualifiedResourceRef.from_dict(page['entries'][0]['entry_ref'], catalog=core.catalog)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_material(resource_ref=ref, at_cut=page['entries'][0]['source_cut'])
    assert error.value.code == 'GOVERNED_DELIVERY_REQUIRED'


@pytest.mark.parametrize('kind', ['entry_type', 'projection', 'predicate'])
def test_unsupported_query_contract_rejected_before_reader(world, monkeypatch, kind):
    cat = TypedReaderCatalog()
    reader = session(world, issue(world), reader_catalog=cat)
    monkeypatch.setattr(cat, 'read_index', lambda *_a, **_k: pytest.fail('invalid query reached reader'))
    if kind == 'entry_type':
        clause = TypedIndexClause('unregistered_type/v1')
    elif kind == 'projection':
        clause = TypedIndexClause('resource_version/v1', projection=('raw_metadata',))
    else:
        clause = TypedIndexClause('resource_version/v1', (TypedPredicate('summary', 'gt', 'hidden'),), ('summary',))
    with pytest.raises(RegistryReadSessionError) as error:
        reader.query_index(IndexQuery((world[-1],), (clause,)))
    assert error.value.code in {'UNSUPPORTED_ENTRY_TYPE', 'INVALID_PROJECTION', 'UNSUPPORTED_PREDICATE'}
    assert not reader._cuts


@pytest.mark.parametrize('field,kind', [('principal_id', 'principal'), ('principal_version_id', 'principal_version')])
def test_observer_principal_self_identity_matches_exact_ref(world, field, kind):
    """Canonical owner-produced principal data still needs exact self fields."""
    from cpn.rpnh.registry.event_store import RegistryConflict
    core, gateway, identity, bootstrap, principal, source = world
    wrong = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(wrong.entity_id), 'principal_version_id': str(wrong.version_id), 'display_name': 'Inconsistent fixture principal'}
    body[field] = str(new_id(kind))
    core.publish_bytes(object_type=wrong.entity_type, logical_id=wrong.entity_id, version_id=wrong.version_id,
        payload=canonical_json(body), metadata=body, media_type='application/json',
        schema_ref='registry_v1/principal/v1', idempotency_key='inconsistent-principal:' + field)
    before = counts(world)
    with pytest.raises(RegistryConflict, match='self-identity'):
        issue_observer_access(gateway, principal_ref=wrong, scope=ObserverReadScope({}, {}),
            purpose='inspect', expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            command_id='reject-inconsistent-principal:' + field)
    assert counts(world) == before


def test_existing_observer_verification_rechecks_principal_self_identity(world, monkeypatch):
    """Narrow validator unit fault injection, not a replacement authority audit."""
    import cpn.rpnh.registry.observer_access as access
    from cpn.rpnh.registry.event_store import RegistryConflict
    context = issue(world)
    original = access.exact_descriptor
    def inconsistent(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3]['entity_type'] == 'principal/v1':
            return {**result, 'principal_id': str(new_id('principal'))}
        return result
    monkeypatch.setattr(access, 'exact_descriptor', inconsistent)
    before = counts(world)
    with pytest.raises(RegistryConflict, match='self-identity'):
        access.verify_observer_access(world[0], context, purpose='inspect')
    assert counts(world) == before


@pytest.mark.parametrize('changed', ['filter', 'projection', 'page_size', 'explicit_cut'])
def test_cursor_binds_entire_public_query_contract(world, changed):
    """R-A10: valid changed query data cannot borrow an earlier continuation."""
    reader = session(world, issue(world))
    original = query(world, 1)
    first = reader.query_index(original)
    assert first['continuation'] is not None
    cut = reader.validate_cut(first['source_results'][0]['cut'])
    if changed == 'filter':
        mutation = replace(original, clauses=(TypedIndexClause('resource_version/v1',
            (TypedPredicate('summary', 'eq', 'item-0'),), ('summary', 'byte_count')),))
    elif changed == 'projection':
        mutation = replace(original, clauses=(TypedIndexClause('resource_version/v1', projection=('summary',)),))
    elif changed == 'page_size':
        mutation = replace(original, page_size=2)
    else:
        mutation = replace(original, cuts={world[-1]:cut})
    before = counts(world)
    with pytest.raises(RegistryReadSessionError) as error:
        reader.query_index(mutation, cursor=first['continuation'])
    assert error.value.code == 'CURSOR_MISMATCH'
    assert counts(world) == before
    # Rejection does not consume or silently reinterpret the original cursor.
    assert reader.query_index(original, cursor=first['continuation'])['entries']


def test_cursor_rejects_other_selected_source_and_access_path(world, tmp_path):
    """R-A10: a cursor remains bound even if the caller has both sources/paths."""
    second = owner(tmp_path/'second-source', 'source-b')
    publish(second, 'other-one', 'other:1')
    publish(second, 'other-two', 'other:2')
    owners = {world[-1]:world, second[-1]:second}
    contexts = {source:issue(value) for source,value in owners.items()}
    selections = tuple(SourceSelection(SourceQualifiedVersionRef(source,value[2].task_ref), 'local')
                       for source,value in owners.items())
    resolver_calls = []
    def resolve(source, access_path):
        assert access_path in ('local', 'alternate')
        resolver_calls.append((source, access_path))
        return open_readonly_source(owners[source][0].run_dir, catalog=owners[source][0].catalog,
                                    binding_generation=access_path)
    host = RegistryReadHostBinding('verified:test', resolve,
        ExistingReadAuthorityProvider({('verified:test',source,path):context
            for source,context in contexts.items() for path in ('local','alternate')}), TypedReaderCatalog())
    reader = open_registry_session(ReadSessionRequest(ExplicitSources(selections), 'inspect'),host=host)
    original = query(world,1)
    first = reader.query_index(original)
    before = {source:counts(value) for source,value in owners.items()}
    for ids in ((second[-1],),(world[-1],second[-1])):
        with pytest.raises(RegistryReadSessionError) as error:
            reader.query_index(replace(original,source_ids=ids),cursor=first['continuation'])
        assert error.value.code == 'CURSOR_MISMATCH'
    alternate = open_registry_session(ReadSessionRequest(ExplicitSources((replace(selections[0],access_path='alternate'),)),
        'inspect'),host=host)
    with pytest.raises(RegistryReadSessionError) as error:
        alternate.query_index(original,cursor=first['continuation'])
    assert error.value.code == 'CURSOR_MISMATCH'
    assert (world[-1],'alternate') in resolver_calls
    assert {source:counts(value) for source,value in owners.items()} == before


@pytest.mark.parametrize('non_registry', [
    'sha256:' + 'a'*64,
    {'manifest_digest':'a'*64,'entry_id':'main'},
    {'artifact_digest':'b'*64,'artifact_id':'module-definition'},
    {'schema_version':'rpnh/package_target/v1','package_lock_digest':'c'*64,'entry_id':'main'},
    '/absolute/local/module.json',
    Path('relative-package.zip'),
])
def test_package_identity_cannot_enter_registry_exact_or_selection(world, non_registry):
    """R-A17: real existing read authority does not turn package identity into refs."""
    reader = session(world,issue(world))
    cut = reader.capture_cut(world[-1])
    before = counts(world)
    with pytest.raises(TypeError):
        reader.read_exact(entry_ref=non_registry,at_cut=cut)
    with pytest.raises(TypeError):
        SourceSelection(non_registry,'local')
    with pytest.raises((TypeError,ValueError)):
        SourceQualifiedVersionRef(world[-1],non_registry)
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    for parser in (SourceQualifiedVersionRef.from_dict, SourceQualifiedResourceRef.from_dict):
        with pytest.raises((TypeError,ValueError,SchemaGovernanceError)):
            parser(non_registry,catalog=world[0].catalog)
    assert counts(world) == before


def test_literal_same_exact_ids_remain_distinct_across_canonical_sources(tmp_path):
    """R-A16: supported project-task identity and owner SourceSet commands.

    No database cloning, direct table writes, patched ID generator, or bypassed
    publication validator is used. Each source has its own canonical bootstrap,
    source binding, observer principal/profile/grant, and SourceSet publication.
    """
    from cpn.rpnh.collaboration.schema_catalog import source_set_schema_data
    from cpn.rpnh.collaboration.source_sets import SourceMember
    shared_project_task = new_id('task')
    worlds, references, contexts = {}, {}, {}
    cat = TypedReaderCatalog()
    source_type = 'collaboration_source_set/v1'
    field_names = tuple(cat.fields(source_type))
    for source in ('literal-left','literal-right'):
        schemas,types,paths = observer_access_schema_data()
        additional,extra_types,extra_paths = source_set_schema_data()
        schemas.update(additional);paths.update(extra_paths)
        source_catalog = SchemaCatalog(schemas=schemas,
            types=tuple({x.name:x for x in (*types,*extra_types)}.values()),schema_paths=paths)
        core = _RegistryCore(tmp_path/source,create=True,catalog=source_catalog,project_task_id=shared_project_task)
        identity = _bootstrap_identity(core,NativeBootstrapManifest(('literal-cross-source-functional/v1',)))
        bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
        gateway = RegistryRegistrationGateway(core,identity.task_ref,bootstrap)
        gateway.bind_source_identity(source_id=source,command_id='bind')
        principal = VersionRef('principal/v1',new_id('principal'),new_id('principal_version'))
        body = {'principal_id':str(principal.entity_id),'principal_version_id':str(principal.version_id),
                'display_name':'Synthetic '+source}
        core.publish_bytes(object_type=principal.entity_type,logical_id=principal.entity_id,version_id=principal.version_id,
            payload=canonical_json(body),metadata=body,media_type='application/json',schema_ref='registry_v1/principal/v1',
            idempotency_key='principal')
        member = SourceMember(SourceQualifiedVersionRef(source,identity.task_ref),('local',),(source+'-alias',))
        value = gateway.publish_source_set(members=(member,),command_id='same-command-in-each-source')
        references[source] = value.source_set_ref
        worlds[source] = (core,gateway,identity,bootstrap,principal,source)
        contexts[source] = issue_observer_access(gateway,principal_ref=principal,
            scope=ObserverReadScope({source_type:field_names},
                {source_type:field_names,'collaboration_branch/v1':tuple(cat.fields('collaboration_branch/v1'))}),purpose='inspect',
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),command_id='observer')
    left,right = (references[key] for key in worlds)
    assert left.ref == right.ref and left.source_id != right.source_id
    assert worlds[left.source_id][2].task_ref.entity_id == worlds[right.source_id][2].task_ref.entity_id
    assert worlds[left.source_id][2].task_ref.version_id != worlds[right.source_id][2].task_ref.version_id
    def resolve(source,path):
        assert path == 'local'
        return open_readonly_source(worlds[source][0].run_dir,catalog=worlds[source][0].catalog)
    host = RegistryReadHostBinding('verified:test',resolve,
        ExistingReadAuthorityProvider({('verified:test',source,'local'):context for source,context in contexts.items()}),cat)
    selections = tuple(SourceSelection(SourceQualifiedVersionRef(source,value[2].task_ref),'local')
                       for source,value in worlds.items())
    reader = open_registry_session(ReadSessionRequest(ExplicitSources(selections),'inspect'),host=host)
    before = {source:counts(value) for source,value in worlds.items()}
    page = reader.query_index(IndexQuery(tuple(worlds),(TypedIndexClause(source_type,projection=('source_set_ref','members')),)))
    assert len(page['entries']) == 2 and page['coverage']['total_count'] == 2
    assert {canonical_json(x['entry_ref']) for x in page['entries']} == {canonical_json(x.to_dict()) for x in (left,right)}
    cuts = {value['source_id']:reader.validate_cut(value['cut']) for value in page['source_results']}
    records = {source:reader.read_exact(entry_ref=reference,at_cut=cuts[source])['record']
               for source,reference in references.items()}
    for source,record in records.items():
        assert record['source_set_ref'] == references[source].to_dict()
        assert record['owner_task_ref']['source_id'] == source
        assert record['members'][0]['aliases'] == [source+'-alias']
    assert records[left.source_id] != records[right.source_id]
    wrong_type = SourceQualifiedVersionRef(left.source_id,
        VersionRef('collaboration_branch/v1',left.ref.entity_id,left.ref.version_id))
    with pytest.raises(RegistryReadSessionError) as type_error:
        reader.read_exact(entry_ref=wrong_type,at_cut=cuts[left.source_id])
    assert type_error.value.code == 'INTEGRITY_FAILED'
    with pytest.raises(RegistryReadSessionError) as error:
        reader.read_exact(entry_ref=left,at_cut=cuts[right.source_id])
    assert error.value.code == 'NOT_DISCLOSED'
    assert {source:counts(value) for source,value in worlds.items()} == before
