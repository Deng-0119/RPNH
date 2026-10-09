"""Bounded private foundation checks, using original offline owner publication."""
from dataclasses import replace
import json
from types import MappingProxyType

import pytest

import test_registry_read_session as read_fixture
import test_source_set_query as owner_fixture
from cpn.rpnh.collaboration._snapshot_producer_proof import (
    _ProofReservation, _canonical_snapshot_producer)
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits, RegistryReadSessionError
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json


@pytest.fixture(scope='module')
def foundation(tmp_path_factory):
    original = owner_fixture.source_observation_schema_data
    def composite():
        schemas, types, paths = original()
        add_schemas, add_types, add_paths = read_fixture.observer_access_schema_data()
        return {**schemas, **add_schemas}, tuple({x.name: x for x in (*types, *add_types)}.values()), {**paths, **add_paths}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(owner_fixture, 'source_observation_schema_data', composite)
        owner, _ = owner_fixture._owner(tmp_path_factory.mktemp('origin') / 'run', 'origin-source')
    core = owner._core
    bootstrap = read_fixture._version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    world = (core, owner.schema_gateway, owner.identity, bootstrap, owner.principal_ref, 'origin-source')
    execution = owner.start(owner.query_admissions['main'], command_id='origin:start')
    products = owner.products(execution, outcome_id='complete',
        products={'left.result': (canonical_json('offline origin output'),)}, command_id='origin:products')
    before = read_fixture.session(world, read_fixture.issue(world, command='origin:before'))
    old_cut = before.capture_cut(world[-1])
    owner.succeed(products, command_id='origin:success')
    reader = read_fixture.session(world, read_fixture.issue(world, command='origin:after'))
    cut = reader.capture_cut(world[-1])
    snapshot = reader._cuts[cut.cut_id][1]
    roots = [row for row in snapshot.objects.values() if row['object_type'] == 'resource_version/v1'
        and json.loads(row['metadata_json']).get('origin_kind') == 'petri_output']
    assert len(roots) == 1
    root = roots[0]
    metadata = json.loads(root['metadata_json'])
    resource = ResourceVersionRef(TypedId.parse(root['logical_id']), TypedId.parse(root['version_id']))
    p = metadata['producer_ref']
    producer = VersionRef(p['entity_type'], TypedId.parse(p['logical_id']), TypedId.parse(p['version_id']))
    return world, before, old_cut, reader, cut, snapshot, root, resource, producer


def budget(snapshot):
    return _ProofReservation(ReadLimits(), snapshot.budget_bytes)


def test_original_key_survives_promotion_and_remains_private(foundation):
    world, before, old_cut, reader, cut, snapshot, root, resource, producer = foundation
    old = before._cuts[old_cut.cut_id][1]
    assert root['version_id'] not in old.objects
    assert root['transaction_id'] not in old.publication_transaction_keys
    with world[0].event_store.connect() as db:
        row = db.execute('SELECT idempotency_key FROM transactions WHERE transaction_id=?', (root['transaction_id'],)).fetchone()
    assert snapshot.publication_transaction_keys[root['transaction_id']] == row[0]
    commit = next(e for e in snapshot.events if e.event_type == 'transaction_committed/v1' and str(e.transaction_id) == root['transaction_id'])
    assert snapshot.publication_ordinals[root['version_id']] > commit.ordinal
    with pytest.raises(TypeError):
        snapshot.publication_transaction_keys[root['transaction_id']] = 'replacement'
    state = budget(snapshot)
    before_counts = read_fixture.counts(world)
    assert _canonical_snapshot_producer(snapshot, resource, producer, state) is None
    assert state.relations == 1 and state.scratch_bytes == 0
    public = reader.read_exact(entry_ref=read_fixture.SourceQualifiedResourceRef(world[-1], resource), at_cut=cut)
    assert set(public['record']) <= set(reader._catalog.default_record_projection('resource_version/v1'))
    assert 'producer_ref' not in public['record']
    assert 'publication_transaction_keys' not in json.dumps(public)
    assert row[0] not in json.dumps(public)
    assert read_fixture.counts(world) == before_counts


@pytest.mark.parametrize('damage', ['weak', 'wrong_target', 'duplicate', 'metadata', 'stable_id', 'transaction',
    'missing_key', 'logical_source', 'unregistered_sibling', 'malformed_target', 'publication', 'producer', 'producer_schema'])
def test_rejected_candidates_are_charged_and_errors_are_narrow(foundation, damage):
    *_, snapshot, root, resource, producer = foundation
    relation = next(r for r in snapshot.relations if r['relation_type'] == 'produced_by'
        and json.loads(r['source_json']).get('version_id') == root['version_id'])
    changed = dict(relation)
    if damage == 'weak': changed['strength'] = 'weak'
    elif damage == 'wrong_target': changed['target_json'] = json.dumps({**json.loads(changed['target_json']), 'version_id': str(new_id('invocation_version'))})
    elif damage == 'metadata': changed['metadata_json'] = '{"hidden":"private-value"}'
    elif damage == 'stable_id': changed['relation_id'] = str(new_id('relation'))
    elif damage == 'transaction': changed['transaction_id'] = str(new_id('transaction'))
    elif damage == 'logical_source': changed['source_json'] = json.dumps({k: v for k, v in json.loads(changed['source_json']).items() if k != 'version_id'})
    elif damage == 'unregistered_sibling': changed['source_json'] = json.dumps({**json.loads(changed['source_json']), 'version_id': str(new_id('resource_version'))})
    elif damage == 'malformed_target': changed['target_json'] = 'private-value invalid json'
    elif damage == 'publication': changed['published_event_id'] = str(new_id('event'))
    relations = tuple(changed if r is relation else r for r in snapshot.relations)
    if damage == 'duplicate': relations += (dict(relation),)
    modified = replace(snapshot, relations=relations)
    if damage == 'missing_key': modified = replace(modified, publication_transaction_keys=MappingProxyType({}))
    if damage == 'producer':
        objects = dict(snapshot.objects)
        objects[root['version_id']] = {**root, 'producer_invocation_id': str(new_id('invocation'))}
        modified = replace(modified, objects=objects)
    if damage == 'producer_schema':
        objects = dict(modified.objects)
        objects[str(producer.version_id)] = {**objects[str(producer.version_id)], 'schema_ref': 'registry_v1/wrong/v1'}
        modified = replace(modified, objects=objects)
    state = budget(snapshot)
    with pytest.raises(RegistryReadSessionError) as caught:
        _canonical_snapshot_producer(modified, resource, producer, state)
    assert caught.value.code == 'INTEGRITY_FAILED'
    assert 'private-value' not in str(caught.value.to_dict())
    assert state.relations == (0 if damage in ('missing_key', 'producer', 'producer_schema') else 2 if damage == 'duplicate' else 1)
    assert state.scratch_bytes == 0


def test_valid_sibling_stops_after_exact_source_identity(foundation):
    *_, snapshot, root, resource, producer = foundation
    version = str(new_id('resource_version'))
    sibling = {**root, 'version_id': version, 'metadata_json': 'deliberately invalid business metadata'}
    relation = next(r for r in snapshot.relations if r['relation_type'] == 'produced_by'
        and json.loads(r['source_json']).get('version_id') == root['version_id'])
    unrelated_business = {**relation, 'source_json': json.dumps({'entity_type': 'resource_version/v1',
        'entity_id': root['logical_id'], 'version_id': version}), 'target_json': 'invalid sibling target',
        'metadata_json': 'invalid sibling metadata', 'strength': 'weak'}
    modified = replace(snapshot, objects={**snapshot.objects, version: sibling}, relations=(*snapshot.relations, unrelated_business))
    state = budget(snapshot)
    _canonical_snapshot_producer(modified, resource, producer, state)
    assert state.relations == 2


def test_proof_reserves_before_decode_and_rejected_duplicate_work(foundation, monkeypatch):
    *_, snapshot, root, resource, producer = foundation
    import cpn.rpnh.collaboration._snapshot_producer_proof as adapter
    state = _ProofReservation(replace(ReadLimits(), max_scan_bytes=snapshot.budget_bytes + 1), snapshot.budget_bytes)
    with monkeypatch.context() as patch:
        patch.setattr(adapter.json, 'loads', lambda *_: pytest.fail('decoded before reservation'))
        with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
            _canonical_snapshot_producer(snapshot, resource, producer, state)
    state = _ProofReservation(replace(ReadLimits(), max_scan_rows=2), snapshot.budget_bytes)
    with pytest.raises(RegistryReadSessionError) as caught:
        _canonical_snapshot_producer(snapshot, resource, producer, state)
    assert caught.value.code == 'LIMIT_EXCEEDED'


@pytest.mark.parametrize('dimension', ['rows', 'bytes'])
def test_key_capture_reservation_precedes_hydration(foundation, monkeypatch, dimension):
    from cpn.rpnh.collaboration.registry_read_session import _budget_preflight, _publication_key_capture_query
    world, _, _, original, _, snapshot, *_ = foundation
    reader = read_fixture.session(world, original._sources[world[-1]].authority.context)
    source = reader._sources[world[-1]]
    store = source.resolved.core.event_store
    with store.connect() as db:
        db.execute('BEGIN')
        upper = int(db.execute('SELECT max(ordinal) FROM events').fetchone()[0])
        base_rows, base_bytes = _budget_preflight(source.resolved.core, reader.limits, db=db, ordinal=upper)
        extra = db.execute('SELECT count(*)+count(terminal_event_id),sum(capture_bytes) FROM ('
            + _publication_key_capture_query(store) + ')', (upper,) * 9).fetchone()
    total_rows = base_rows + extra[0]
    total_bytes = base_bytes + extra[1] + total_rows * 256
    assert extra[0] > 0 and extra[1] > 0
    reader.limits = replace(reader.limits, **({'max_scan_rows': total_rows - 1} if dimension == 'rows'
        else {'max_scan_bytes': total_bytes - 1}))
    with monkeypatch.context() as patch:
        patch.setattr(store, '_row_to_envelope', lambda *_: pytest.fail('hydrated before key reservation'))
        with pytest.raises(RegistryReadSessionError) as caught:
            reader.capture_cut(world[-1])
    assert caught.value.code == 'LIMIT_EXCEEDED' and not reader._cuts
    reader.limits = replace(reader.limits, max_scan_rows=total_rows, max_scan_bytes=total_bytes)
    cut = reader.capture_cut(world[-1])
    assert reader._cuts[cut.cut_id][1].budget_bytes == total_bytes
    with monkeypatch.context() as patch:
        patch.setattr(store, '_row_to_envelope', lambda *_: pytest.fail('second cut hydrated before retained reservation'))
        with pytest.raises(RegistryReadSessionError) as caught:
            reader.capture_cut(world[-1])
    assert caught.value.code == 'LIMIT_EXCEEDED' and len(reader._cuts) == 1


def test_escaped_publication_key_is_reserved_before_capture(tmp_path, monkeypatch):
    from cpn.rpnh.collaboration.registry_read_session import _budget_preflight, _publication_key_capture_query
    world = read_fixture.owner(tmp_path / 'escaped-key')
    secret_key = '\x01' * 4096
    read_fixture.publish(world, 'escaped-key fixture', secret_key)
    reader = read_fixture.session(world, read_fixture.issue(world))
    store = reader._sources[world[-1]].resolved.core.event_store
    with store.connect() as db:
        db.execute('BEGIN')
        upper = db.execute('SELECT max(ordinal) FROM events').fetchone()[0]
        rows, size = _budget_preflight(reader._sources[world[-1]].resolved.core, reader.limits, db=db, ordinal=upper)
        records = db.execute(_publication_key_capture_query(store), (upper,) * 9).fetchall()
        retained_map = {r['transaction_id']: r['idempotency_key'] for r in records}
        extra_size = sum(r['capture_bytes'] for r in records)
    assert secret_key in retained_map.values()
    assert extra_size >= len(canonical_json(retained_map))
    total = size + extra_size + (rows + 2 * len(records)) * 256
    # This threshold fits the former raw-key calculation but not the escaped
    # retained representation. Rejection must happen before event hydration.
    reader.limits = replace(reader.limits, max_scan_bytes=total - len(secret_key) * 4)
    with monkeypatch.context() as patch:
        patch.setattr(store, '_row_to_envelope', lambda *_: pytest.fail('escaped key copied before reservation'))
        with pytest.raises(RegistryReadSessionError) as caught:
            reader.capture_cut(world[-1])
    assert caught.value.code == 'LIMIT_EXCEEDED' and not reader._cuts
