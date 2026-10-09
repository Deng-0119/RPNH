"""Ordinary finite Start readers with original offline admission/Start facts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

import test_registry_read_session as rf
import test_source_set_query as owner_fixture
from test_snapshot_producer_proof import foundation
from test_product_origin_core import replace_descriptor, snapshot_of, set_snapshot
from cpn.rpnh.collaboration import registry_read_session as sessions
from cpn.rpnh.collaboration import registry_typed_readers as readers
from cpn.rpnh.collaboration.registry_read_contracts import (
    IndexQuery, TypedIndexClause, TypedPredicate, RegistryReadSessionError,
    ReadSessionRequest, ExplicitSources, SourceSelection)
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
from cpn.rpnh.registry.publication import _version_from_payload, _ref_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
from cpn.rpnh.registry.identities import new_id


@pytest.fixture(scope='module')
def start_worlds(tmp_path_factory):
    original = owner_fixture.source_observation_schema_data
    def composite():
        schemas, types, paths = original()
        extra, more, more_paths = rf.observer_access_schema_data()
        return {**schemas, **extra}, tuple({x.name: x for x in (*types, *more)}.values()), {**paths, **more_paths}
    worlds = []
    for source in ('start-a', 'start-b'):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(owner_fixture, 'source_observation_schema_data', composite)
            owner, contexts = owner_fixture._owner(tmp_path_factory.mktemp(source) / 'run', source)
        for name, output in (('main', 'left.result'), ('other', 'right.result')):
            execution = owner.start(owner.query_admissions[name], command_id='start-reader:' + name)
            products = owner.products(execution, outcome_id='complete',
                products={output: (canonical_json('Synthetic reader output'),)}, command_id='reader-products:' + name)
            owner.succeed(products, command_id='reader-success:' + name)
        core = owner._core
        bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
        world = (core, owner.schema_gateway, owner.identity, bootstrap, owner.principal_ref, source)
        worlds.append((world, contexts))
    return tuple(worlds)


def grant(world, fields, *, extra_index=None, command='start-grant'):
    index = {'transition_firing/v1': tuple(fields), **(extra_index or {})}
    return issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(index, {'transition_firing/v1': tuple(fields)}), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id=command)


def firing(world, contexts, name='main'):
    return SourceQualifiedVersionRef(world[-1], contexts[name].own_transition_firing_ref)


def query(source, cut, projection=('start_event_id',), predicates=(), extra=(), page_size=100):
    return IndexQuery((source,), (TypedIndexClause('transition_firing/v1', tuple(predicates), tuple(projection)), *extra),
        page_size, cuts={source: cut})


@pytest.mark.parametrize('field', readers._START_FIELDS)
def test_each_explicit_start_field_needs_only_its_grant(start_worlds, field, monkeypatch):
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, (field,), command='single:' + field)) as reader:
        cut = reader.capture_cut(world[-1])
        original = snapshot_of(reader, cut)
        before = rf.counts(world)
        from cpn.rpnh.collaboration._product_origin_core import _CoreProof
        monkeypatch.setattr(_CoreProof, 'run', lambda *args: pytest.fail('ordinary Start ran Core'))
        result = reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=(field,))
        event = next(event for event in original.events if event.event_type == 'operation_execution_started/v1'
            and event.payload['transition_firing_ref'] == firing(world, contexts).to_dict()['ref'])
        expected = {'start_event_id': str(event.event_id), 'start_transaction_id': str(event.transaction_id),
            'start_ordinal': event.ordinal, 'start_input_binding_refs': event.payload['input_binding_refs'],
            'start_input_resource_refs': event.payload['input_resource_refs']}[field]
        assert result['record'] == {field: expected}
        assert result['disclosure']['unprovided_fields'] == []
        assert snapshot_of(reader, cut) is original and original._origin_context is None
        assert not reader._cursors and rf.counts(world) == before


@pytest.mark.parametrize('predicate_only', [False, True])
def test_index_projection_and_predicate_dispatch_same_bounded_start(start_worlds, predicate_only):
    world, contexts = start_worlds[0]
    fields = ('start_event_id', 'transition_firing_ref') if predicate_only else ('start_event_id',)
    with rf.session(world, grant(world, fields, command='index:' + str(predicate_only))) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        event = next(event for event in snapshot.events if event.event_type == 'operation_execution_started/v1')
        projection = ('transition_firing_ref',) if predicate_only else ('start_event_id',)
        predicates = (TypedPredicate('start_event_id', 'eq', str(event.event_id)),) if predicate_only else ()
        result = reader.query_index(query(world[-1], cut, projection, predicates))
        assert len(result['entries']) == (1 if predicate_only else 2)
        assert all(set(entry['fields']) == set(projection) for entry in result['entries'])
        assert all(entry['disclosure']['unprovided_fields'] == [] for entry in result['entries'])
        assert result['source_results'][0]['access_state'] == 'readable'
        assert snapshot._origin_context is None


@pytest.mark.parametrize('state', ['present', 'missing', 'corrupt'])
def test_partial_start_grant_rejected_before_protected_read(start_worlds, state, monkeypatch):
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='denied:' + state)) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        target = str(contexts['main'].own_transition_firing_ref.version_id)
        objects = dict(snapshot.objects)
        if state == 'missing':
            objects.pop(target)
        elif state == 'corrupt':
            objects[target] = {**objects[target], 'metadata_json': 'private malformed bytes'}
        set_snapshot(reader, cut, replace(snapshot, objects=objects))
        monkeypatch.setattr(reader._catalog, 'read_exact', lambda *args, **kwargs: pytest.fail('protected read'))
        for operation in (lambda: reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut,
                projection=('start_transaction_id',)),
                lambda: reader.query_index(query(world[-1], cut, ('start_transaction_id',)))):
            with pytest.raises(RegistryReadSessionError) as error:
                operation()
            assert error.value.code == 'NOT_DISCLOSED'


def test_index_start_missing_is_source_failure_not_false_predicate(start_worlds):
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='missing:start')) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        set_snapshot(reader, cut, replace(snapshot, events=tuple(event for event in snapshot.events
            if event.event_type != 'operation_execution_started/v1')))
        result = reader.query_index(query(world[-1], cut, predicates=(TypedPredicate('start_event_id', 'eq', str(new_id('event'))),)))
        assert result['entries'] == [] and result['continuation'] is None
        assert result['source_results'][0]['access_state'] == 'INTEGRITY_FAILED'
        assert result['source_results'][0]['coverage']['total_count'] is None


def test_mixed_index_does_not_invoke_core_completion_contract(foundation, monkeypatch):
    world = foundation[0]
    fields = ('start_event_id',)
    permission = grant(world, fields, extra_index={'firing_completion/v2': ('firing_completion_ref',)}, command='mixed:start')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        # The unstarted independent admission is unrelated to this regression;
        # select only existing original Start firings in this frozen negative seam.
        snapshot = snapshot_of(reader, cut)
        started = {event.payload['transition_firing_ref']['version_id'] for event in snapshot.events
            if event.event_type == 'operation_execution_started/v1'}
        set_snapshot(reader, cut, replace(snapshot, objects={key: row for key, row in snapshot.objects.items()
            if row['object_type'] != 'transition_firing/v1' or key in started}))
        from cpn.rpnh.collaboration._product_origin_core import _CoreProof
        original = _CoreProof.exact
        def exact(self, ref, kind):
            assert kind != 'firing_completion/v2', 'ordinary collection invoked Core completion proof'
            return original(self, ref, kind)
        monkeypatch.setattr(_CoreProof, 'exact', exact)
        result = reader.query_index(query(world[-1], cut, extra=(TypedIndexClause('firing_completion/v2',
            projection=('firing_completion_ref',)),)))
        assert {entry['entry_type'] for entry in result['entries']} == {'transition_firing/v1', 'firing_completion/v2'}
        assert result['source_results'][0]['access_state'] == 'readable'


@pytest.mark.parametrize('damage', ['task_version', 'task_kind', 'round_missing', 'net_missing'])
def test_ordinary_start_requires_exact_selected_task_and_finite_dependencies(start_worlds, request, damage):
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='anchors:' + damage)) as reader:
        cut = reader.capture_cut(world[-1])
        F = firing(world, contexts).to_dict()['ref']
        I = _ref_payload(contexts['main'].invocation_ref)
        if damage.startswith('task'):
            task = _ref_payload(world[2].task_ref)
            task['version_id'] = str(new_id('task_version'))
            if damage == 'task_kind':
                task['entity_type'] = 'task_round/v1'
            for ref in (F, I):
                replace_descriptor(reader, cut, ref, lambda body: body.update(task_ref=task), request)
        else:
            snapshot = snapshot_of(reader, cut)
            key = 'task_round_ref' if damage == 'round_missing' else 'net_instance_ref'
            version = json.loads(snapshot.objects[I['version_id']]['metadata_json'])[key]['version_id']
            set_snapshot(reader, cut, replace(snapshot, objects={key: row for key, row in snapshot.objects.items() if key != version}))
        with pytest.raises(RegistryReadSessionError) as error:
            reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=('start_event_id',))
        assert error.value.code == 'INTEGRITY_FAILED'


def test_six_readers_defaults_and_explicit_fingerprint_contract(monkeypatch):
    catalog = readers.TypedReaderCatalog()
    expected = {'invocation/v1', 'transition_firing/v1', 'firing_admission/v1',
        'firing_completion/v2', 'operation_result/v1', 'marking_delta/v1'}
    assert set(readers._ORIGIN_FIELDS) == expected
    for kind in expected:
        assert catalog.default_record_projection(kind) == (kind.split('/')[0] + '_ref',)
        assert catalog.default_index_projection(kind) == catalog.default_record_projection(kind)
    assert catalog.default_record_projection('resource_version/v1') == (
        'resource_id', 'resource_version_id', 'media_type', 'content_schema_ref', 'byte_count', 'summary', 'commit_ordinal')
    assert catalog.default_record_projection('petri_token/v1') == (
        'petri_token_ref', 'net_instance_ref', 'place', 'kind', 'resource_ref', 'epoch', 'consumed_by', 'token_id', 'commit_ordinal')
    before = catalog.fingerprint()
    monkeypatch.setitem(readers._ORIGIN_SCALAR_FIELDS, 'start_ordinal', ('strict_integer', 1))
    assert catalog.fingerprint() != before


@pytest.mark.parametrize('field,value', [('start_ordinal', True), ('start_ordinal', -1),
    ('start_event_id', 'transaction:' + 'a' * 32), ('start_transaction_id', 'event:' + 'a' * 32),
    ('start_input_binding_refs', [{'entity_type': 'invocation/v1', 'logical_id': 'invocation:' + 'a' * 32,
        'version_id': 'invocation_version:' + 'b' * 32}]),
    ('start_input_resource_refs', [{'resource_id': 'petri_token:' + 'a' * 32, 'resource_version_id': 'resource_version:' + 'b' * 32}])])
def test_start_selected_strict_scalar_and_member_kinds(field, value):
    with pytest.raises(readers.TypedReadError):
        readers._validate_origin_projection('transition_firing/v1', {field: value}, (field,), None)


def test_nullable_token_resource_and_both_delta_phases(foundation):
    readers._validate_origin_projection('petri_token/v1', {'resource_ref': None}, ('resource_ref',), None)
    with pytest.raises(readers.TypedReadError):
        readers._validate_origin_projection('petri_token/v1', {'resource_ref': {
            'resource_id': 'petri_token:' + 'a' * 32, 'resource_version_id': 'resource_version:' + 'b' * 32}}, ('resource_ref',), None)
    world = foundation[0]
    fields = tuple(readers._ORIGIN_FIELDS['marking_delta/v1'])
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope({}, {'marking_delta/v1': fields}), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='delta:finite')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        phases = set()
        for row in snapshot_of(reader, cut).objects.values():
            if row['object_type'] == 'marking_delta/v1':
                ref = SourceQualifiedVersionRef(world[-1], _version_from_payload(json.loads(row['metadata_json'])['marking_delta_ref']))
                result = reader.read_exact(entry_ref=ref, at_cut=cut, projection=fields)
                phases.add(result['record']['phase'])
        assert phases == {'claim', 'settlement'}


@pytest.mark.parametrize('pending', ['success', 'error'])
def test_start_exact_charges_through_safe_serialization_and_final_ttl(start_worlds, monkeypatch, pending):
    from cpn.rpnh.collaboration import _origin_query_context as owners
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='ttl:' + pending)) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        if pending == 'error':
            set_snapshot(reader, cut, replace(snapshot, events=tuple(event for event in snapshot.events
                if event.event_type != 'operation_execution_started/v1')))
        retained = []
        original_context = owners._OriginQueryContext
        def factory(*args, **kwargs):
            context = original_context(*args, **kwargs)
            retained.append(context)
            return context
        monkeypatch.setattr(owners, '_OriginQueryContext', factory)
        original_json = sessions.canonical_json
        serialized = []
        def encode(value):
            data = original_json(value)
            if isinstance(value, dict) and ('record' in value or 'code' in value):
                assert retained[0].reservation.scratch_bytes > 0
                serialized.append(value)
                reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            return data
        monkeypatch.setattr(sessions, 'canonical_json', encode)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=('start_event_id',))
        assert error.value.code == 'SESSION_EXPIRED' and serialized
        assert retained[0].reservation.scratch_bytes == 0 and not retained[0]._cache


def multi_reader(start_worlds, command, catalog=None):
    worlds = [world for world, _contexts in start_worlds]
    permissions = {world[-1]: grant(world, ('start_event_id',), command=command) for world in worlds}
    by_source = {world[-1]: world for world in worlds}
    def resolve(source, access):
        world = by_source[source]
        return sessions.open_readonly_source(world[0].run_dir, catalog=world[0].catalog)
    selections = tuple(SourceSelection(SourceQualifiedVersionRef(world[-1], world[2].task_ref), 'local') for world in worlds)
    host = sessions.RegistryReadHostBinding('verified:multi-start', resolve,
        sessions.ExistingReadAuthorityProvider({('verified:multi-start', source, 'local'): permission
            for source, permission in permissions.items()}), catalog or readers.TypedReaderCatalog())
    return sessions.open_registry_session(ReadSessionRequest(ExplicitSources(selections), 'inspect'), host=host)


def test_multisource_start_owners_retain_all_scratch_until_serialized_handoff(start_worlds, monkeypatch):
    contexts, observed = [], []
    original_begin = sessions._StartReadOwners.begin
    def begin(self, snapshot, spec):
        context = original_begin(self, snapshot, spec)
        contexts.append(context)
        if len(contexts) == 2:
            assert contexts[0].reservation.scratch_bytes > 0 and contexts[0]._cache
            assert context.reservation.retained_bytes == self.session._retained_bytes() + contexts[0].reservation.scratch_bytes
            assert context.reservation.work == contexts[0].reservation.work > 0
        return context
    monkeypatch.setattr(sessions._StartReadOwners, 'begin', begin)
    original_json = sessions.canonical_json
    def encode(value):
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/registry_index_page/v1':
            assert len(contexts) == 2 and all(context.reservation.scratch_bytes > 0 for context in contexts)
            observed.append(True)
        return original_json(value)
    monkeypatch.setattr(sessions, 'canonical_json', encode)
    with multi_reader(start_worlds, 'multi:lifetime') as reader:
        cuts = {source: reader.capture_cut(source) for source in reader._sources}
        spec = IndexQuery(tuple(cuts), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), cuts=cuts)
        result = reader.query_index(spec)
        assert len(result['entries']) == 4 and observed
        assert all(context.reservation.scratch_bytes == 0 and not context._cache for context in contexts)
        assert all(snapshot._origin_context is None for _cut, snapshot in reader._cuts.values())


def test_multisource_cannot_drop_prior_source_retention_at_boundary(start_worlds, monkeypatch):
    contexts = []
    original_begin = sessions._StartReadOwners.begin
    def begin(self, snapshot, spec):
        if self.contexts:
            earlier = self.contexts[0]
            assert earlier.reservation.scratch_bytes > 0
            self.session.limits = replace(self.session.limits,
                max_scan_bytes=self.session._retained_bytes() + earlier.reservation.scratch_bytes - 1)
        result = original_begin(self, snapshot, spec)
        contexts.append(result)
        return result
    monkeypatch.setattr(sessions._StartReadOwners, 'begin', begin)
    with multi_reader(start_worlds, 'multi:bounded') as reader:
        cuts = {source: reader.capture_cut(source) for source in reader._sources}
        spec = IndexQuery(tuple(cuts), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), cuts=cuts)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.query_index(spec)
        assert error.value.code == 'LIMIT_EXCEEDED'
        assert len(contexts) == 1 and contexts[0].reservation.scratch_bytes == 0
        assert not reader._cursors


def test_source_local_owners_continue_one_logical_work_ledger(start_worlds):
    with multi_reader(start_worlds, 'multi:work') as reader:
        cuts = {source: reader.capture_cut(source) for source in reader._sources}
        spec = IndexQuery(tuple(cuts), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), cuts=cuts)
        group = sessions._StartReadOwners(reader)
        try:
            first = group.begin(snapshot_of(reader, cuts['start-a']), spec)
            first.reserve('A', rows=reader.limits.max_scan_rows - 1)
            second = group.begin(snapshot_of(reader, cuts['start-b']), spec)
            assert second.reservation.work == first.reservation.work
            with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
                second.reserve('S', rows=2)
        finally:
            group.close()


def test_start_cursor_cache_keeps_only_projected_entries_and_replays(start_worlds, monkeypatch):
    world, contexts = start_worlds[0]
    captured = []
    original_begin = sessions._StartReadOwners.begin
    def begin(self, snapshot, spec):
        owner = original_begin(self, snapshot, spec)
        captured.append(owner)
        return owner
    monkeypatch.setattr(sessions._StartReadOwners, 'begin', begin)
    with rf.session(world, grant(world, ('start_event_id',), command='start:cursor')) as reader:
        cut = reader.capture_cut(world[-1])
        spec = query(world[-1], cut, page_size=1)
        first = reader.query_index(spec)
        assert first['continuation'] and len(captured) == 1
        assert captured[0].reservation.scratch_bytes == 0 and not captured[0]._cache
        second = reader.query_index(spec, cursor=first['continuation'])
        assert second == reader.query_index(spec, cursor=first['continuation'])
        assert second['continuation'] is None and len(captured) == 3
        assert all(context.reservation.scratch_bytes == 0 and not context._cache for context in captured)
        assert first['entries'][0]['entry_ref'] != second['entries'][0]['entry_ref']
        assert all(not hasattr(query, '_origin_context') for query, _offset in reader._cursors.values())


def test_frozen_success_removal_is_only_a_no_success_dependency_regression(start_worlds, monkeypatch):
    """A labelled negative seam, not a genuine pre-Success canonical history.

    Current original admission writers keep these descriptors provisional until
    Success. Here we remove unrelated Success facts from an already captured
    canonical world only to detect accidental closure/dependency expansion.
    """
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='start:no-success-dependency')) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = snapshot_of(reader, cut)
        objects = {key: row for key, row in snapshot.objects.items()
            if row['object_type'] not in ('firing_completion/v2', 'operation_result/v1')}
        events = tuple(event for event in snapshot.events if event.event_type != 'transition_firing_settled/v1')
        set_snapshot(reader, cut, replace(snapshot, objects=objects, events=events))
        from cpn.rpnh.collaboration._product_origin_core import _CoreProof
        original = _CoreProof.exact
        def exact(self, ref, kind):
            assert kind not in ('firing_completion/v2', 'operation_result/v1', 'marking_delta/v1')
            return original(self, ref, kind)
        monkeypatch.setattr(_CoreProof, 'exact', exact)
        result = reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=('start_event_id',))
        assert set(result['record']) == {'start_event_id'}


def test_actual_provisional_admission_stays_invisible_before_success(foundation):
    # The fixture retained a real cut after original Start/products and before
    # Success. No synthetic promotion or current/live view is permitted.
    world, previous_reader, old_cut = foundation[:3]
    snapshot = snapshot_of(previous_reader, old_cut)
    assert not any(row['object_type'] == 'transition_firing/v1' for row in snapshot.objects.values())
    permission = grant(world, ('start_event_id',), command='start:provisional')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1], ordinal=old_cut.head.ordinal)
        invocation = json.loads(foundation[5].objects[str(foundation[-1].version_id)]['metadata_json'])
        ref = SourceQualifiedVersionRef(world[-1], _version_from_payload(invocation['own_transition_firing_ref']))
        with pytest.raises(RegistryReadSessionError) as error:
            reader.read_exact(entry_ref=ref, at_cut=cut, projection=('start_event_id',))
        assert error.value.code == 'NOT_PRESENT_AT_CUT'


def test_start_index_preserves_failed_source_handoff_when_authority_changes(start_worlds, monkeypatch):
    with multi_reader(start_worlds, 'multi:source-failure') as reader:
        cuts = {source: reader.capture_cut(source) for source in reader._sources}
        snapshot = snapshot_of(reader, cuts['start-a'])
        set_snapshot(reader, cuts['start-a'], replace(snapshot, events=tuple(event for event in snapshot.events
            if event.event_type != 'operation_execution_started/v1')))
        original = sessions.canonical_json
        pending = []
        def encode(value):
            data = original(value)
            if isinstance(value, dict) and value.get('schema_version') == 'rpnh/registry_index_page/v1':
                pending.append(True)
            return data
        monkeypatch.setattr(sessions, 'canonical_json', encode)
        check = reader.final_recheck
        def final(source_ids=None):
            if pending and source_ids == ('start-a',):
                reader._invalidate('start-a', 'ACCESS_CHANGED')
            return check(source_ids)
        monkeypatch.setattr(reader, 'final_recheck', final)
        spec = IndexQuery(tuple(cuts), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), cuts=cuts)
        result = reader.query_index(spec)
        assert [state['access_state'] for state in result['source_results']] == ['ACCESS_CHANGED', 'readable']
        assert len(result['entries']) == 2 and all(entry['entry_ref']['source_id'] == 'start-b' for entry in result['entries'])


@pytest.mark.parametrize('damage,expected', [('field', 'INVALID_PROJECTION'), ('predicate', 'INVALID_PREDICATE')])
def test_start_static_query_errors_keep_original_codes(start_worlds, damage, expected):
    world, _ = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='start:static:' + damage)) as reader:
        cut = reader.capture_cut(world[-1])
        spec = query(world[-1], cut, projection=('start_event_id', 'not_a_field')) if damage == 'field' else query(
            world[-1], cut, predicates=(TypedPredicate('start_event_id', 'eq', 7),))
        for call in (lambda: reader.query_index(spec), lambda: reader._collect_index(spec)):
            with pytest.raises(RegistryReadSessionError) as error:
                call()
            assert error.value.code == expected


def test_unknown_protected_callback_code_never_becomes_source_state(start_worlds):
    class Catalog(readers.TypedReaderCatalog):
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            raise RegistryReadSessionError('private callback detail')
    world, _ = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='start:safe-error'), reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        result = reader.query_index(query(world[-1], cut))
        assert result['source_results'][0]['access_state'] == 'INTEGRITY_FAILED'
        assert 'private' not in json.dumps(result)


def test_start_cached_page_rechecks_after_final_serialization(start_worlds, monkeypatch):
    world, _ = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='start:cached-final')) as reader:
        cut = reader.capture_cut(world[-1])
        spec = query(world[-1], cut, page_size=1)
        first = reader.query_index(spec)
        original = sessions.canonical_json
        calls = []
        def encode(value):
            data = original(value)
            if isinstance(value, dict) and value.get('schema_version') == 'rpnh/registry_index_page/v1':
                calls.append(True)
                if len(calls) == 2:
                    reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            return data
        monkeypatch.setattr(sessions, 'canonical_json', encode)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.query_index(spec, cursor=first['continuation'])
        assert error.value.code == 'SESSION_EXPIRED' and len(calls) == 2
        assert not reader._cursors


@pytest.mark.parametrize('boundary', [False, True])
def test_invalidated_prior_owner_snapshot_remains_accounted(start_worlds, monkeypatch, boundary):
    holder, contexts = {}, []
    class Catalog(readers.TypedReaderCatalog):
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            result = super().read_index(core, entry_ref, snapshot=snapshot, projection=projection)
            if snapshot.source_id == 'start-a' and not holder.get('invalidated'):
                holder['invalidated'] = True
                holder['reader']._invalidate('start-a', 'ACCESS_CHANGED')
            return result
    original_begin = sessions._StartReadOwners.begin
    def begin(self, snapshot, spec):
        if snapshot.source_id == 'start-b':
            prior = self.contexts[0]
            assert prior.snapshot.budget_bytes > 0 and prior.reservation.scratch_bytes > 0
            assert all(cut.source_id != 'start-a' for cut, _snapshot in self.session._cuts.values())
            if boundary:
                # Either missing A snapshot or A scratch would individually fit;
                # their simultaneous retention must fail the next reservation.
                allowance = self.session._retained_bytes() + prior.reservation.scratch_bytes + prior.snapshot.budget_bytes - 1
                self.session.limits = replace(self.session.limits, max_scan_bytes=allowance)
        result = original_begin(self, snapshot, spec)
        contexts.append(result)
        if snapshot.source_id == 'start-b':
            prior = self.contexts[0]
            assert result.reservation.retained_bytes == (
                self.session._retained_bytes() + prior.reservation.scratch_bytes + prior.snapshot.budget_bytes + self._delivery_bytes)
        return result
    monkeypatch.setattr(sessions._StartReadOwners, 'begin', begin)
    with multi_reader(start_worlds, 'multi:invalidated:' + str(boundary), Catalog()) as reader:
        holder['reader'] = reader
        spec = IndexQuery(tuple(reader._sources), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),))
        if boundary:
            with pytest.raises(RegistryReadSessionError) as error:
                reader.query_index(spec)
            # A was already invalidated. Its current authoritative source error
            # takes precedence over the pending later-source budget failure.
            assert error.value.code == 'ACCESS_CHANGED'
            assert len(contexts) == 1
        else:
            result = reader.query_index(spec)
            assert len(result['entries']) == 2
            assert [state['access_state'] for state in result['source_results']] == ['ACCESS_CHANGED', 'readable']
        assert all(context.reservation.scratch_bytes == 0 and not context._cache for context in contexts)


@pytest.mark.parametrize('mode', ['exact', 'index'])
@pytest.mark.parametrize('damage', ['forged', 'missing', 'extra', 'wrong_numeric_type'])
def test_custom_start_callback_matches_independent_summary(start_worlds, mode, damage):
    world, contexts = start_worlds[0]
    field = 'start_ordinal' if damage == 'wrong_numeric_type' else 'start_event_id'
    class Catalog(readers.TypedReaderCatalog):
        def read_exact(self, core, entry_ref, *, snapshot, projection=None):
            result = super().read_exact(core, entry_ref, snapshot=snapshot, projection=projection)
            if damage == 'forged':
                result[field] = str(new_id('event'))
            elif damage == 'missing':
                result.pop(field)
            elif damage == 'extra':
                result['start_transaction_id'] = str(new_id('transaction'))
            else:
                result[field] = float(result[field])
            return result
    with rf.session(world, grant(world, (field,), command='custom:' + mode + ':' + damage), reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        if mode == 'exact':
            with pytest.raises(RegistryReadSessionError) as error:
                reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=(field,))
            assert error.value.code == 'INTEGRITY_FAILED'
        else:
            result = reader.query_index(query(world[-1], cut, (field,)))
            assert result['entries'] == []
            assert result['source_results'][0]['access_state'] == 'INTEGRITY_FAILED'


@pytest.mark.parametrize('mode', ['exact', 'index', 'private_collection'])
@pytest.mark.parametrize('expire', [False, True])
def test_retained_start_exception_releases_completed_private_frames(start_worlds, mode, expire):
    import gc
    import weakref
    weak_candidates, captured, holder = [], [], {}
    class PrivateCandidate:
        pass
    class Catalog(readers.TypedReaderCatalog):
        def read_exact(self, core, entry_ref, *, snapshot, projection=None):
            snapshot._origin_context.reserve(size=4096)
            candidate = PrivateCandidate()
            weak_candidates.append(weakref.ref(candidate))
            captured.append(snapshot._origin_context)
            if expire:
                holder['reader'].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
    world, contexts = start_worlds[0]
    with rf.session(world, grant(world, ('start_event_id',), command='frames:' + mode + ':' + str(expire)),
            reader_catalog=Catalog()) as reader:
        holder['reader'] = reader
        cut = reader.capture_cut(world[-1])
        with pytest.raises(RegistryReadSessionError) as error:
            if mode == 'exact':
                reader.read_exact(entry_ref=firing(world, contexts), at_cut=cut, projection=('start_event_id',))
            elif mode == 'index':
                reader.query_index(query(world[-1], cut))
            else:
                reader._collect_index(query(world[-1], cut))
        assert error.value.code == ('SESSION_EXPIRED' if expire else 'LIMIT_EXCEEDED')
        assert error.value.__context__ is None and error.value.__cause__ is None
        gc.collect()
        assert weak_candidates and all(candidate() is None for candidate in weak_candidates)
        assert all(context.snapshot is None and context._session is None and not context._cache
            and context.reservation.scratch_bytes == 0 for context in captured)
        traceback = error.value.__traceback__
        while traceback:
            if '/collaboration/' in traceback.tb_frame.f_code.co_filename:
                assert not {'candidate', 'snapshot', 'source', 'values', 'row', 'result', 'context', 'owners'} .intersection(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next


@pytest.mark.parametrize('historical', [False, True])
def test_later_source_capture_reserves_live_owners_before_hydration(start_worlds, monkeypatch, historical):
    from cpn.rpnh.collaboration.registry_read_contracts import HistoricalCutRequest
    from cpn.rpnh.registry.event_store import EventStore
    holder, observed = {}, []
    original_capture = sessions.RegistryReadSession._capture_cut
    original_sql = sessions._publication_key_capture_query
    def capture(self, source_id, *, ordinal=None, _retained_extra=None, _capture_owner=None):
        if source_id == 'start-b':
            assert _retained_extra is not None and _retained_extra() > 0
            core = self._sources[source_id].resolved.core
            with core.event_store.connect() as db:
                db.execute('BEGIN')
                upper = core.event_store.max_ordinal() if ordinal is None else ordinal
                rows, size = sessions._budget_preflight(core, self.limits, db=db, ordinal=upper)
                key_sql = original_sql(core.event_store)
                extra_rows, extra_size = db.execute('SELECT count(*)+count(terminal_event_id),'
                    f'coalesce(sum(capture_bytes),0) FROM ({key_sql})', (upper,) * 9).fetchone()
                budget = size + int(extra_size) + (rows + int(extra_rows)) * 256
            self.limits = replace(self.limits,
                max_scan_bytes=self._retained_bytes() + _retained_extra() + budget - 1)
            observed.append('capture-boundary')
        try:
            return original_capture(self, source_id, ordinal=ordinal, _retained_extra=_retained_extra, _capture_owner=_capture_owner)
        finally:
            holder['forbid_hydration'] = False
    def key_sql(store):
        result = original_sql(store)
        if store.path == holder['reader']._sources['start-b'].resolved.core.event_store.path:
            holder['forbid_hydration'] = True
        return result
    original_decode = EventStore._row_to_envelope
    def decode(row):
        assert not holder.get('forbid_hydration'), 'B history hydrated before combined retention check'
        return original_decode(row)
    monkeypatch.setattr(sessions.RegistryReadSession, '_capture_cut', capture)
    monkeypatch.setattr(sessions, '_publication_key_capture_query', key_sql)
    monkeypatch.setattr(EventStore, '_row_to_envelope', staticmethod(decode))
    with multi_reader(start_worlds, 'multi:capture:' + str(historical)) as reader:
        holder['reader'] = reader
        cuts = ({source: HistoricalCutRequest(source, reader._sources[source].resolved.core.event_store.max_ordinal())
            for source in reader._sources} if historical else None)
        spec = IndexQuery(tuple(reader._sources), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), cuts=cuts)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.query_index(spec)
        assert error.value.code == 'LIMIT_EXCEEDED' and observed == ['capture-boundary']
        assert {cut.source_id for cut, _snapshot in reader._cuts.values()} == {'start-a'}
        assert not reader._cursors


@pytest.mark.parametrize('mode', ['public', 'private'])
@pytest.mark.parametrize('source,single_source', [('start-a', True), ('start-a', False), ('start-b', False)])
@pytest.mark.parametrize('code', ['LIMIT_EXCEEDED', 'SOURCE_UNAVAILABLE'])
def test_failed_pending_capture_stays_charged_through_delivery(start_worlds, monkeypatch, mode, source, single_source, code):
    import gc
    import weakref
    holder, observed = {}, []
    original_snapshot = sessions._Snapshot
    class TrackedSnapshot(original_snapshot):
        __slots__ = ('__weakref__',)
    def snapshot(*args, **kwargs):
        value = TrackedSnapshot(*args, **kwargs)
        if value.source_id == source:
            holder['pending_snapshot'] = weakref.ref(value)
            holder['pending_budget'] = value.budget_bytes
        return value
    monkeypatch.setattr(sessions, '_Snapshot', snapshot)
    reserve_capture = sessions._StartReadOwners.reserve_capture
    def reserve(self, size):
        holder['group'] = self
        reserve_capture(self, size)
    monkeypatch.setattr(sessions._StartReadOwners, 'reserve_capture', reserve)
    original_json = sessions.canonical_json
    def encode(value):
        encoded = original_json(value)
        if holder.get('failed') and isinstance(value, dict) and (
                'code' in value or value.get('schema_version') == 'rpnh/registry_index_page/v1'
                or 'entries' in value and 'failures' in value):
            if mode == 'private' and 'entries' in value and 'failures' in value:
                holder['late_authority_change'] = True
            group = holder['group']
            assert group.pending_capture_bytes == holder['pending_budget']
            assert holder['pending_snapshot']() is not None
            assert group.capture_retained_extra() >= group.pending_capture_bytes
            group.check_retained()
            observed.append('serialized')
        return encoded
    monkeypatch.setattr(sessions, 'canonical_json', encode)
    command = 'pending:' + source + ':' + code + ':' + str(single_source) + ':' + mode
    world = start_worlds[0][0]
    manager = (rf.session(world, grant(world, ('start_event_id',), command=command)) if single_source else
        multi_reader(start_worlds, command))
    with manager as reader:
        final = reader.final_recheck
        def check(source_ids=None):
            if source_ids == (source,) and holder.get('late_authority_change'):
                reader._invalidate(source, 'ACCESS_CHANGED')
                observed.append('target-final-after-handoff')
            if source_ids == (source,) and holder.get('pending_snapshot') and not holder.get('failed'):
                group = holder['group']
                assert group.error_reserved
                assert group.pending_capture_bytes == holder['pending_budget']
                assert all(cut.source_id != source for cut, _snapshot in reader._cuts.values())
                if source == 'start-a':
                    assert group.current is None and group._delivery_bytes > 0
                holder['failed'] = True
                holder['exception'] = RegistryReadSessionError(code)
                raise holder['exception']
            if holder.get('failed'):
                assert holder['group'].pending_capture_bytes >= holder['pending_budget']
                assert holder['pending_snapshot']() is not None
                observed.append('final')
            return final(source_ids)
        monkeypatch.setattr(reader, 'final_recheck', check)
        spec = IndexQuery(tuple(reader._sources), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),))
        operation = reader.query_index if mode == 'public' else reader._collect_index
        if code == 'LIMIT_EXCEEDED':
            with pytest.raises(RegistryReadSessionError) as error:
                operation(spec)
            assert error.value.code == code and error.value.__context__ is None
        else:
            result = operation(spec)
            if mode == 'public':
                states = {state['source_id']: state['access_state'] for state in result['source_results']}
                assert states[source] == 'SOURCE_UNAVAILABLE'
                entries = result['entries']
            else:
                assert result.failures[source] == 'ACCESS_CHANGED'
                assert 'target-final-after-handoff' in observed
                entries = result.entries
            assert len(entries) == (0 if single_source else 2)
        group = holder['group']
        assert 'serialized' in observed and 'final' in observed
        assert group.pending_capture_bytes == 0 and group._delivery_bytes == 0 and not group._capture_errors
        assert holder['exception'].__traceback__ is None
        gc.collect()
        assert holder['pending_snapshot']() is None
