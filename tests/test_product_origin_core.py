"""Private Core regression boundary; deterministic original owner facts only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

import test_registry_read_session as rf
from test_snapshot_producer_proof import foundation
from cpn.rpnh.collaboration._origin_core_contract import CORE_RECORD_FIELDS, CORE_INDEX_FIELDS, RESOURCE_ROOT_FIELDS, RESOURCE_OUTPUT_FIELDS
from cpn.rpnh.collaboration._product_origin_core import _verify_origin_core
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError


@pytest.fixture(scope='module')
def permission(foundation):
    world, *_ = foundation
    records = dict(CORE_RECORD_FIELDS)
    records['resource_version/v1'] = RESOURCE_ROOT_FIELDS
    records['operation_result/v1'] += RESOURCE_OUTPUT_FIELDS
    return issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), records), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='origin:core:grant')


@pytest.fixture
def core_reader(foundation, permission):
    world, *_ = foundation
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        yield reader, cut


def root_resource(foundation):
    return SourceQualifiedResourceRef(foundation[0][-1], foundation[-2])


def root_result(foundation):
    snapshot = foundation[5]
    row = next(row for row in snapshot.objects.values() if row['object_type'] == 'operation_result/v1')
    return SourceQualifiedVersionRef(foundation[0][-1], _version_from_payload(json.loads(row['metadata_json'])['operation_result_ref']))


@pytest.mark.parametrize('kind', ['resource', 'result'])
def test_complete_original_owner_core(foundation, core_reader, kind):
    reader, cut = core_reader
    before = rf.counts(foundation[0])
    snapshot = reader._cuts[cut.cut_id][1]
    root = root_resource(foundation) if kind == 'resource' else root_result(foundation)
    result = _verify_origin_core(reader, root=root, at_cut=cut)
    assert set(result) == {'producer_invocation_ref', 'transition_firing_ref', 'firing_completion_ref',
        'operation_result_ref', 'operation_binding_ref', 'root_role'}
    assert result['root_role'] == ('registered_output' if kind == 'resource' else 'operation_result')
    assert result['producer_invocation_ref']['ref']['version_id'] == str(foundation[-1].version_id)
    assert not reader._cursors and reader._cuts[cut.cut_id][1] is snapshot
    assert snapshot._origin_context is None
    assert callable(reader.query_product_origin_v1)
    assert rf.counts(foundation[0]) == before


@pytest.fixture(scope='module')
def extended_world(tmp_path_factory):
    """Original Registry publication/owner Success, including a workspace root.

    The extra workspace publication uses the existing internal publication
    primitive with synthetic bytes while its invocation remains open. It does
    not exercise workspace delivery or execute an operation/provider.
    """
    import test_source_set_query as owners
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.publication import (_fresh_reference_resource_ref,
        _fresh_reference_resource_metadata, _append_direct_resource_version_publication, _ref_payload)
    from cpn.rpnh.registry.schema_catalog import canonical_json
    original = owners.source_observation_schema_data
    def composite():
        schemas, types, paths = original()
        extra, more, more_paths = rf.observer_access_schema_data()
        return {**schemas, **extra}, tuple({x.name: x for x in (*types, *more)}.values()), {**paths, **more_paths}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(owners, 'source_observation_schema_data', composite)
        owner, _ = owners._owner(tmp_path_factory.mktemp('origin-core-extended') / 'run', 'extended-source')
    core = owner._core
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    world = (core, owner.schema_gateway, owner.identity, bootstrap, owner.principal_ref, 'extended-source')
    left = owner.start(owner.query_admissions['main'], command_id='extended:start:left')
    left_products = owner.products(left, outcome_id='complete',
        products={'left.result': (canonical_json('left'),)}, command_id='extended:products:left')
    ctx = owner.query_admissions['main'].admission.context
    intent = VersionRef('workspace_write_intent/v1', new_id('write_intent'), new_id('write_intent_version'))
    intent_body = {'write_intent_id': str(intent.entity_id), 'write_intent_version_id': str(intent.version_id),
        'operation_binding_ref': _ref_payload(ctx.operation_binding_ref),
        'source_binding_ref': _ref_payload(VersionRef('workspace_binding/v1', new_id('workspace_binding'), new_id('workspace_binding_version'))),
        'allowed_relative_root': '', 'one_lineage': True}
    key = 'extended:workspace'
    resource = _fresh_reference_resource_ref(task_id=core.task_id, task_round_ref=ctx.task_round_ref,
        net_instance_ref=ctx.net_instance_ref, kind='workspace_write', primary=ctx.operation_binding_ref,
        secondary=intent, idempotency_key=key, invocation_ref=ctx.invocation_ref,
        operation_binding_ref=ctx.operation_binding_ref)
    tx = core.begin(idempotency_key=key, task_round_id=ctx.task_round_ref.entity_id,
        net_instance_id=ctx.net_instance_ref.entity_id)
    tx.prewrite(object_type=intent.entity_type, logical_id=intent.entity_id, version_id=intent.version_id,
        payload=canonical_json(intent_body), metadata=intent_body, media_type='application/json',
        schema_ref='registry_v1/workspace_write_intent/v1')
    _append_direct_resource_version_publication(tx, ref=resource, payload=b'synthetic workspace output',
        metadata_factory=lambda size: _fresh_reference_resource_metadata(core, ref=resource,
            origin_kind='workspace_write', primary=ctx.operation_binding_ref, secondary=intent, context=ctx,
            producer_ref=ctx.invocation_ref, lifetime_ref=ctx.invocation_ref, payload_size=size, media_type='text/plain',
            content_schema_ref=None, content_schema_authority_ref=None, summary='Synthetic workspace proof',
            descriptors={}, extensions={}, derived_from=()), media_type='text/plain', producer_ref=ctx.invocation_ref,
        producer_invocation_id=ctx.invocation_ref.entity_id, relation_key=key)
    tx.commit()
    right = owner.start(owner.query_admissions['other'], command_id='extended:start:right')
    right_products = owner.products(right, outcome_id='complete',
        products={'right.result': (canonical_json('right'),)}, command_id='extended:products:right')
    owner.succeed(left_products, command_id='extended:success:left')
    owner.succeed(right_products, command_id='extended:success:right')
    records = dict(CORE_RECORD_FIELDS)
    records['resource_version/v1'] = RESOURCE_ROOT_FIELDS
    records['operation_result/v1'] += RESOURCE_OUTPUT_FIELDS
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), records), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='extended:grant')
    return world, permission, SourceQualifiedResourceRef(world[-1], resource), owner.query_admissions['other'].admission.context


def test_workspace_root_and_original_sibling_success(extended_world):
    world, permission, workspace_root, sibling = extended_world
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = reader._cuts[cut.cut_id][1]
        before = rf.counts(world)
        proof = _verify_origin_core(reader, root=workspace_root, at_cut=cut)
        assert proof['root_role'] == 'invocation_produced_resource'
        row = next(row for row in snapshot.objects.values() if row['object_type'] == 'operation_result/v1'
            and json.loads(row['metadata_json'])['invocation_ref']['version_id'] == str(sibling.invocation_ref.version_id))
        result_root = SourceQualifiedVersionRef(world[-1], _version_from_payload(json.loads(row['metadata_json'])['operation_result_ref']))
        proof = _verify_origin_core(reader, root=result_root, at_cut=cut)
        C = json.loads(snapshot.objects[proof['firing_completion_ref']['ref']['version_id']]['metadata_json'])
        Ks = json.loads(snapshot.objects[C['successor_checkpoint_ref']['version_id']]['metadata_json'])
        assert Ks['previous_checkpoint_ref']['version_id'] != str(sibling.admission_marking_checkpoint_ref.version_id)
        assert proof['root_role'] == 'operation_result' and not reader._cursors
        assert rf.counts(world) == before


def snapshot_of(reader, cut):
    return reader._cuts[cut.cut_id][1]


def set_snapshot(reader, cut, snapshot):
    reader._cuts[cut.cut_id] = (cut, snapshot)


def descriptor_ref(reader, cut, kind):
    row = next(row for row in snapshot_of(reader, cut).objects.values() if row['object_type'] == kind)
    return {'entity_type': kind, 'logical_id': row['logical_id'], 'version_id': row['version_id']}


def replace_descriptor(reader, cut, ref, change, request):
    """Fault-inject a coherent frozen row/publication/immutable-file triple.

    This edits only a synthetic test store and its in-memory cut, never Registry
    SQL or product source. It lets a test isolate later semantic predicates from
    earlier checksum/byte/metadata defenses; bytes are restored at teardown.
    """
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.schema_catalog import canonical_json
    snapshot = snapshot_of(reader, cut)
    row = snapshot.objects[ref['version_id']]
    body = json.loads(row['metadata_json'])
    change(body)
    raw = canonical_json(body)
    store = reader._sources[cut.source_id].resolved.core.object_store
    path = store.path_for_version(TypedId.parse(ref['version_id']))
    previous = path.read_bytes()
    path.write_bytes(raw)
    request.addfinalizer(lambda: path.write_bytes(previous))
    changed = {**row, 'metadata_json': raw.decode(), 'size': len(raw)}
    events = tuple(replace(event, payload={**event.payload, 'metadata': body, 'size': len(raw)})
        if str(event.event_id) == row['published_event_id'] else event for event in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, objects={**snapshot.objects, ref['version_id']: changed}, events=events))
    return body


def clone_descriptor(reader, cut, ref, request):
    from cpn.rpnh.registry.identities import new_id, TypedId
    from cpn.rpnh.registry.schema_catalog import canonical_json
    snapshot = snapshot_of(reader, cut)
    original = snapshot.objects[ref['version_id']]
    stem = ref['entity_type'].split('/')[0]
    new_ref = {**ref, 'logical_id': str(new_id(stem)), 'version_id': str(new_id(stem + '_version'))}
    body = json.loads(original['metadata_json'])
    body[stem + '_ref'] = new_ref
    raw = canonical_json(body)
    store = reader._sources[cut.source_id].resolved.core.object_store
    version = TypedId.parse(new_ref['version_id'])
    path = store.path_for_version(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    request.addfinalizer(path.unlink)
    event_id = new_id('event')
    row = {**original, 'logical_id': new_ref['logical_id'], 'version_id': new_ref['version_id'],
        'metadata_json': raw.decode(), 'size': len(raw), 'published_event_id': str(event_id),
        'storage_locator': store.locator_for_version(version)}
    publication = next(event for event in snapshot.events if str(event.event_id) == original['published_event_id'])
    event = replace(publication, event_id=event_id, aggregate_id=row['logical_id'], stream_id='object:' + row['logical_id'],
        payload={name: row[name] for name in ('logical_id', 'version_id', 'object_type', 'size', 'media_type',
            'schema_ref', 'storage_locator')} | {'metadata': body})
    set_snapshot(reader, cut, replace(snapshot, objects={**snapshot.objects, new_ref['version_id']: row},
        events=(*snapshot.events, event), publication_ordinals={**snapshot.publication_ordinals,
            new_ref['version_id']: snapshot.publication_ordinals[ref['version_id']]}))
    return new_ref


@pytest.mark.parametrize('damage', ['missing_completion', 'duplicate_completion', 'late_completion_failure'])
def test_complete_collection_never_accepts_early_match(foundation, core_reader, request, monkeypatch, damage):
    from cpn.rpnh.collaboration.registry_typed_readers import TypedReadError
    reader, cut = core_reader
    C = descriptor_ref(reader, cut, 'firing_completion/v2')
    if damage == 'missing_completion':
        snapshot = snapshot_of(reader, cut)
        set_snapshot(reader, cut, replace(snapshot, objects={key: value for key, value in snapshot.objects.items()
            if key != C['version_id']}))
    else:
        clone_descriptor(reader, cut, C, request)
    if damage == 'late_completion_failure':
        original = reader._catalog.read_index
        calls = []
        def callback(core, entry_ref, *, snapshot, projection=None):
            calls.append(entry_ref)
            if len(calls) == 2:
                raise TypedReadError('SOURCE_UNAVAILABLE')
            return original(core, entry_ref, snapshot=snapshot, projection=projection)
        monkeypatch.setattr(reader._catalog, 'read_index', callback)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
    assert error.value.code == ('SOURCE_UNAVAILABLE' if damage == 'late_completion_failure' else 'INTEGRITY_FAILED')
    assert not reader._cursors and set(error.value.to_dict()) == {'code', 'message', 'reopen_session'}


@pytest.mark.parametrize('target,field', [
    ('invocation/v1', 'task_ref'), ('transition_firing/v1', 'node_ref'),
    ('firing_admission/v1', 'invocation_ref'), ('operation_execution_lease/v1', 'invocation_ref'),
    ('firing_completion/v2', 'invocation_ref'), ('operation_result/v1', 'transition_firing_ref'),
    ('marking_delta/v1', 'operation_binding_refs')])
def test_exact_admission_and_settlement_associations(foundation, core_reader, request, target, field):
    from cpn.rpnh.registry.identities import new_id
    reader, cut = core_reader
    ref = descriptor_ref(reader, cut, target)
    if target == 'marking_delta/v1':
        snapshot = snapshot_of(reader, cut)
        row = next(row for row in snapshot.objects.values() if row['object_type'] == target
            and json.loads(row['metadata_json'])['phase'] == 'settlement')
        ref = json.loads(row['metadata_json'])['marking_delta_ref']
    def damage(body):
        value = body[field][0] if type(body[field]) is list else body[field]
        value['version_id'] = str(new_id(value['version_id'].split(':')[0]))
    replace_descriptor(reader, cut, ref, damage, request)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


@pytest.mark.parametrize('damage', ['wrong_producer', 'schema_label', 'conflict_by_result', 'duplicate'])
def test_related_settled_conflicts_are_not_prefiltered(foundation, core_reader, damage):
    from cpn.rpnh.registry.identities import new_id
    reader, cut = core_reader
    snapshot = snapshot_of(reader, cut)
    original = next(event for event in snapshot.events if event.event_type == 'transition_firing_settled/v1')
    if damage == 'wrong_producer':
        changed = replace(original, producer_invocation_id=new_id('invocation'))
    elif damage == 'schema_label':
        changed = replace(original, event_type='operation_execution_started/v1')
    elif damage == 'conflict_by_result':
        changed = replace(original, event_id=new_id('event'), aggregate_id=str(new_id('transition_firing')),
            payload={**original.payload, 'transition_firing_ref': {**original.payload['transition_firing_ref'],
                'version_id': str(new_id('transition_firing_version'))}})
    else:
        changed = replace(original, event_id=new_id('event'))
    events = (*snapshot.events, changed) if damage in ('conflict_by_result', 'duplicate') else tuple(
        changed if event is original else event for event in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, events=events))
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


def test_result_can_have_earlier_original_transaction(foundation, core_reader):
    reader, cut = core_reader
    snapshot = snapshot_of(reader, cut)
    R = descriptor_ref(reader, cut, 'operation_result/v1')
    row = snapshot.objects[R['version_id']]
    rootrow = snapshot.objects[str(foundation[-2].resource_version_id)]
    earlier = rootrow['transaction_id']
    assert earlier != row['transaction_id']
    commit = next(event for event in snapshot.events if event.event_type == 'transaction_committed/v1'
        and str(event.transaction_id) == earlier)
    from cpn.rpnh.registry.identities import TypedId
    events = tuple(replace(event, transaction_id=TypedId.parse(earlier), ordinal=commit.ordinal - 1)
        if str(event.event_id) == row['published_event_id'] else event for event in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, objects={**snapshot.objects, R['version_id']: {**row, 'transaction_id': earlier}}, events=events))
    assert _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)['root_role'] == 'operation_result'


@pytest.mark.parametrize('damage', [False, True, 'successor_net_self'])
def test_changed_net_is_unsupported_only_after_common_integrity(foundation, core_reader, request, damage):
    reader, cut = core_reader
    N = descriptor_ref(reader, cut, 'net_instance/v1')
    successor_net = clone_descriptor(reader, cut, N, request)
    C = json.loads(snapshot_of(reader, cut).objects[descriptor_ref(reader, cut, 'firing_completion/v2')['version_id']]['metadata_json'])
    Ks = C['successor_checkpoint_ref']
    replace_descriptor(reader, cut, Ks, lambda body: body.update(net_instance_ref=successor_net), request)
    snapshot = snapshot_of(reader, cut)
    events = tuple(replace(event, aggregate_id=successor_net['logical_id'], stream_id='marking:' + successor_net['logical_id'],
        payload={**event.payload, 'net_instance_ref': successor_net})
        if event.event_type == 'marking_checkpoint_committed/v1' and event.payload['checkpoint_ref'] == Ks else event
        for event in snapshot.events)
    objects = dict(snapshot.objects)
    if damage == 'successor_net_self':
        set_snapshot(reader, cut, replace(snapshot, events=events, objects=objects))
        replace_descriptor(reader, cut, successor_net, lambda body: body.update(net_instance_ref=N), request)
        snapshot = snapshot_of(reader, cut)
        objects, events = dict(snapshot.objects), snapshot.events
    elif damage:
        objects[Ks['version_id']] = {**objects[Ks['version_id']], 'schema_ref': 'registry_v1/invalid/v1'}
    set_snapshot(reader, cut, replace(snapshot, events=events, objects=objects))
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
    assert error.value.code == ('INTEGRITY_FAILED' if damage else 'UNSUPPORTED_SETTLEMENT_SHAPE')


@pytest.mark.parametrize('state', ['present', 'missing', 'corrupt'])
def test_full_permissions_precede_root_existence(foundation, monkeypatch, state):
    world, *_ = foundation
    records = dict(CORE_RECORD_FIELDS)
    records['resource_version/v1'] = RESOURCE_ROOT_FIELDS
    records['operation_result/v1'] += RESOURCE_OUTPUT_FIELDS
    records['marking_checkpoint/v1'] = ('marking_checkpoint_ref',)
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), records), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='denied:core:' + state)
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        version = str(foundation[-2].resource_version_id)
        objects = dict(snapshot.objects)
        if state == 'missing':
            del objects[version]
        elif state == 'corrupt':
            objects[version] = {**objects[version], 'metadata_json': 'private bad data'}
        set_snapshot(reader, cut, replace(snapshot, objects=objects))
        monkeypatch.setattr(reader._catalog, 'read_exact', lambda *args, **kwargs: pytest.fail('read before full authorization'))
        with pytest.raises(RegistryReadSessionError) as error:
            _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
        assert error.value.code == 'NOT_DISCLOSED'


def test_result_root_needs_no_output_permission_or_output_semantics(foundation, request):
    world, *_ = foundation
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), dict(CORE_RECORD_FIELDS)), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='core:result-minimum')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        R = descriptor_ref(reader, cut, 'operation_result/v1')
        # Generic-ref schema is valid but it is not a resource output type. Its
        # semantics must not be expanded by this result-root Core request.
        replace_descriptor(reader, cut, R, lambda body: body.update(output_resource_refs=[body['invocation_ref']]), request)
        assert _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)['root_role'] == 'operation_result'
        with pytest.raises(RegistryReadSessionError) as error:
            _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
        assert error.value.code == 'NOT_DISCLOSED'


def test_membership_checks_every_output_without_dereferencing_other_output(foundation, core_reader, request):
    from cpn.rpnh.registry.identities import new_id
    reader, cut = core_reader
    R = descriptor_ref(reader, cut, 'operation_result/v1')
    absent = {'entity_type': 'resource_version/v1', 'logical_id': str(new_id('resource')),
        'version_id': str(new_id('resource_version'))}
    replace_descriptor(reader, cut, R, lambda body: body['output_resource_refs'].append(absent), request)
    assert _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)['root_role'] == 'registered_output'
    replace_descriptor(reader, cut, R, lambda body: body['output_resource_refs'][-1].update(entity_type='invocation/v1'), request)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


@pytest.mark.parametrize('pending', ['success', 'integrity', 'memory'])
def test_final_ttl_check_wins_after_safe_serialization(foundation, core_reader, monkeypatch, pending):
    from cpn.rpnh.collaboration import _product_origin_core as core
    reader, cut = core_reader
    if pending == 'integrity':
        snapshot = snapshot_of(reader, cut)
        version = str(foundation[-2].resource_version_id)
        set_snapshot(reader, cut, replace(snapshot, objects={**snapshot.objects, version:
            {**snapshot.objects[version], 'producer_invocation_id': None}}))
    elif pending == 'memory':
        reader.limits = replace(reader.limits, max_scan_bytes=reader._retained_bytes() + 1)
    original = core.canonical_json
    seen = []
    def serialize(value):
        encoded = original(value)
        if isinstance(value, dict) and ('root_role' in value or 'code' in value):
            seen.append(value)
            reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return encoded
    monkeypatch.setattr(core, 'canonical_json', serialize)
    if pending == 'memory':
        original_check = reader.final_recheck
        calls = []
        def final(source_ids=None):
            calls.append(True)
            if len(calls) > 1:
                reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            return original_check(source_ids)
        monkeypatch.setattr(reader, 'final_recheck', final)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
    assert error.value.code == 'SESSION_EXPIRED'
    assert pending == 'memory' or len(seen) == 1
    assert not reader._cuts and not reader._cursors


def test_custom_callback_snapshot_and_final_revocation(foundation, permission):
    from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
    from cpn.rpnh.registry.observer_access import revoke_observer_access
    world, *_ = foundation
    seen = []
    class Catalog(TypedReaderCatalog):
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            seen.append(snapshot)
            value = super().read_index(core, entry_ref, snapshot=snapshot, projection=projection)
            revoke_observer_access(world[1], grant_ref=permission.grant_ref, command_id='core:revoke')
            return value
    # Use a dedicated grant so the shared fixture is not revoked for other tests.
    records = dict(CORE_RECORD_FIELDS)
    records['resource_version/v1'] = RESOURCE_ROOT_FIELDS
    records['operation_result/v1'] += RESOURCE_OUTPUT_FIELDS
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), records), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='core:callback-grant')
    with rf.session(world, permission, reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        original = snapshot_of(reader, cut)
        with pytest.raises(RegistryReadSessionError) as error:
            _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
        assert error.value.code == 'ACCESS_CHANGED'
        assert len(seen) == 1 and type(seen[0]) is type(original)
        assert seen[0].objects is original.objects and original._origin_context is None
        assert not reader._cuts and not reader._cursors


def test_logical_output_work_limit_is_independent_and_all_or_nothing(foundation, core_reader, request):
    from cpn.rpnh.registry.identities import TypedId
    reader, cut = core_reader
    R = descriptor_ref(reader, cut, 'operation_result/v1')
    outputs = [{'entity_type': 'resource_version/v1', 'logical_id': str(TypedId('resource', format(i, '032x'))),
        'version_id': str(TypedId('resource_version', format(i, '032x')))} for i in range(1, 2101)]
    replace_descriptor(reader, cut, R, lambda body: body['output_resource_refs'].extend(outputs), request)
    reader.limits = replace(reader.limits, max_scan_rows=2000)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
    assert error.value.code == 'LIMIT_EXCEEDED' and not reader._cursors


def test_core_does_not_read_start_claims_or_resource_material(foundation, core_reader, monkeypatch):
    from cpn.rpnh.collaboration import registry_typed_readers as readers
    reader, cut = core_reader
    snapshot = snapshot_of(reader, cut)
    I = json.loads(snapshot.objects[str(foundation[-1].version_id)]['metadata_json'])
    F = json.loads(snapshot.objects[I['own_transition_firing_ref']['version_id']]['metadata_json'])
    Dc = F['claim_marking_delta_ref']['version_id']
    original = readers.payload_at
    reads = []
    def payload(core, reference, snapshot, **kwargs):
        source, ref = readers.wire_ref(reference)
        assert ref['entity_type'] not in ('resource_version/v1', 'petri_token/v1')
        assert ref['version_id'] != Dc
        reads.append(ref['entity_type'])
        return original(core, reference, snapshot, **kwargs)
    monkeypatch.setattr(readers, 'payload_at', payload)
    assert _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)['root_role'] == 'registered_output'
    assert 'operation_result/v1' in reads and 'marking_delta/v1' in reads


def test_bad_common_settled_envelope_is_integrity_failure(foundation, core_reader):
    reader, cut = core_reader
    snapshot = snapshot_of(reader, cut)
    events = tuple(replace(event, branch_id='') if event.event_type == 'transition_firing_settled/v1' else event
        for event in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, events=events))
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_result(foundation), at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


def test_incompatible_typed_catalog_fails_before_object_read(foundation, permission):
    from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
    class Catalog(TypedReaderCatalog):
        def fields(self, kind):
            fields = super().fields(kind)
            return {**fields, 'net_ref': 'string'} if kind == 'marking_checkpoint/v1' else fields
        def read_exact(self, *args, **kwargs):
            pytest.fail('incompatible reader was called')
    world, *_ = foundation
    with rf.session(world, permission, reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        with pytest.raises(RegistryReadSessionError) as error:
            _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
        assert error.value.code == 'UNSUPPORTED_READER_VERSION'


@pytest.mark.parametrize('entity_type', ['', 'unversioned'])
def test_provenance_generic_refs_require_existing_exact_syntax(foundation, core_reader, request, entity_type):
    reader, cut = core_reader
    root = root_resource(foundation)
    ref = {'entity_type': 'resource_version/v1', 'logical_id': str(root.ref.resource_id),
        'version_id': str(root.ref.resource_version_id)}
    def damage(body):
        body['reference_provenance']['contributor_refs'] = [
            {**body['producer_ref'], 'entity_type': entity_type}]
    # Resource metadata is read without reading its material; update only the
    # snapshot row and publication, rather than replacing the resource payload.
    snapshot = snapshot_of(reader, cut)
    row = snapshot.objects[ref['version_id']]
    body = json.loads(row['metadata_json']); damage(body)
    from cpn.rpnh.registry.schema_catalog import canonical_json
    objects = {**snapshot.objects, ref['version_id']: {**row, 'metadata_json': canonical_json(body).decode()}}
    events = tuple(replace(event, payload={**event.payload, 'metadata': body})
        if str(event.event_id) == row['published_event_id'] else event for event in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, objects=objects, events=events))
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root, at_cut=cut)
    assert error.value.code == 'INTEGRITY_FAILED'


def test_terminal_callback_retention_cannot_overrun_delivery_budget(foundation, core_reader, monkeypatch):
    from cpn.rpnh.collaboration import _product_origin_core as core
    reader, cut = core_reader
    original_factory = core._OriginQueryContext
    contexts = []
    def context_factory(session, snapshot):
        context = original_factory(session, snapshot)
        contexts.append(context)
        return context
    monkeypatch.setattr(core, '_OriginQueryContext', context_factory)
    original_json = core.canonical_json
    ready = []
    def serialize(value):
        data = original_json(value)
        if isinstance(value, dict) and 'root_role' in value:
            context = contexts[0]
            allowance = reader._retained_bytes() + snapshot_of(reader, cut).budget_bytes + context.reservation.scratch_bytes // 2
            reader.limits = replace(reader.limits, max_scan_bytes=allowance)
            context.reservation.limits = reader.limits
            ready.append(True)
        return data
    monkeypatch.setattr(core, 'canonical_json', serialize)
    original_check = reader.final_recheck
    added = []
    def check(source_ids=None):
        original_check(source_ids)
        if ready and not added:
            added.append(True)  # guard capture_cut's own final checks
            reader.capture_cut(cut.source_id)
    monkeypatch.setattr(reader, 'final_recheck', check)
    with pytest.raises(RegistryReadSessionError) as error:
        _verify_origin_core(reader, root=root_resource(foundation), at_cut=cut)
    assert error.value.code == 'LIMIT_EXCEEDED'
    assert added == [True] and len(reader._cuts) == 2 and not reader._cursors
    assert contexts[0].reservation.scratch_bytes == 0
