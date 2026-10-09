"""Focused fixed public delivery tests; synthetic offline Registry facts only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

import test_registry_read_session as rf
from test_product_origin_includes import (includes_world, inputless_world, full_grant,
    include_reader, grant, root, change_start)
from test_product_origin_core import extended_world, snapshot_of, set_snapshot, descriptor_ref
from cpn.rpnh.collaboration import _product_origin_query as public
from cpn.rpnh.collaboration._origin_core_contract import INCLUDE_ORDER, CORE_INDEX_FIELDS, record_fields
from cpn.rpnh.collaboration.registry_read_contracts import (ReadLimits, RegistryReadSessionError,
    IndexQuery, TypedIndexClause, registry_read_contract_schema_data)
from cpn.rpnh.collaboration.registry_read_session import _Query
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef


def code(expected, call):
    with pytest.raises(RegistryReadSessionError) as caught:
        call()
    assert caught.value.code == expected
    assert set(caught.value.to_dict()) == {'code', 'message', 'reopen_session'}
    assert str(caught.value) == expected.replace('_', ' ').lower()
    return caught.value


def query(reader, cut, **kwargs):
    return reader.query_product_origin_v1(root(reader, cut), cut, **kwargs)


@pytest.mark.parametrize('kind', ['result', 'resource'])
def test_public_exact_shape_default_and_zero_writes(includes_world, include_reader, kind):
    reader, cut = include_reader
    before = rf.counts(includes_world[0])
    page = reader.query_product_origin_v1(root(reader, cut, kind), cut)
    assert set(page) == {'schema_version', 'profile', 'contract_revision', 'root', 'source_cut',
        'access_revision', 'root_proof', 'rows', 'coverage', 'continuation'}
    assert page['schema_version'] == 'rpnh/product_origin_page/v1'
    assert page['profile'] == 'product_origin_v1' and page['contract_revision'] == 2
    assert len(page['root_proof']) == 6
    assert page['root_proof']['root_role'] == ('operation_result' if kind == 'result' else 'registered_output')
    assert [row['role'] for row in page['rows']] == ['start_input', 'start_input', 'claim', 'claim']
    assert page['coverage']['state'] == 'complete' and page['continuation'] is None
    assert set(page['coverage']) == {'scope', 'state', 'relations'}
    assert page['coverage']['scope'] == 'authorized_root_at_cut'
    for name, relation in page['coverage']['relations'].items():
        assert relation == ({'state': 'complete', 'witness': 'verified_at_cut'} if name in INCLUDE_ORDER else {'state': 'not_in_profile'})
    schemas, types, paths = registry_read_contract_schema_data()
    import jsonschema
    jsonschema.validate(page, schemas['rpnh/product_origin_page/v1'])
    assert not reader._cursors and rf.counts(includes_world[0]) == before


def test_workspace_root_default_public_profile(extended_world):
    world, _permission, workspace, _sibling = extended_world
    permission = grant(world, command='public:workspace')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        page = reader.query_product_origin_v1(workspace, cut)
        assert page['root_proof']['root_role'] == 'invocation_produced_resource'
        assert page['root'] == workspace.to_dict() and page['coverage']['state'] == 'complete'


@pytest.mark.parametrize('include', [('producer_execution',), ('producer_execution', 'start_inputs'),
    ('producer_execution', 'claims'), INCLUDE_ORDER])
def test_minimal_permissions_and_no_unrequested_start(includes_world, include, monkeypatch):
    world = includes_world[0]
    permission = grant(world, include, resource_root=False, command='public:minimal:' + ':'.join(include))
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        if 'start_inputs' not in include:
            change_start(reader, cut, lambda event: replace(event, payload={}))
        result = query(reader, cut, include=include)
        assert [row['role'] for row in result['rows']] == (['start_input'] * 2 if 'start_inputs' in include else []) + (['claim'] * 2 if 'claims' in include else [])
        assert result == query(reader, cut, include=list(reversed(include)))
        for name in ('start_inputs', 'claims'):
            if name not in include:
                assert result['coverage']['relations'][name] == {'state': 'not_in_profile'}


@pytest.mark.parametrize('include,expected', [([], 'INVALID_QUERY'), ('claims', 'INVALID_QUERY'),
    (['claims'], 'INVALID_QUERY'), (['producer_execution', 'producer_execution'], 'INVALID_QUERY'),
    (['producer_execution', 1], 'INVALID_QUERY'), (['direct_derivations'], 'UNSUPPORTED_RELATION'),
    (['claims', 'claims', 'unknown'], 'UNSUPPORTED_RELATION')])
def test_include_errors_are_static(include_reader, monkeypatch, include, expected):
    reader, cut = include_reader
    monkeypatch.setattr(public, '_evaluate_origin_includes', lambda *a: pytest.fail('protected evaluation'))
    code(expected, lambda: query(reader, cut, include=include))
    assert not reader._cursors


@pytest.mark.parametrize('size,expected', [(True, 'INVALID_QUERY'), (False, 'INVALID_QUERY'), (0, 'INVALID_QUERY'),
    (-1, 'INVALID_QUERY'), (1.0, 'INVALID_QUERY'), ('1', 'INVALID_QUERY'), (101, 'LIMIT_EXCEEDED')])
def test_page_size_strict_static(include_reader, size, expected):
    reader, cut = include_reader
    code(expected, lambda: query(reader, cut, page_size=size))
    assert not reader._cursors


def test_page_default_tightening_and_legacy_defaults(include_reader):
    reader, cut = include_reader
    reader.limits = replace(reader.limits, max_page_size=2)
    page = query(reader, cut)
    assert len(page['rows']) == 2 and next(iter(reader._cursors.values()))[0].data['page_size'] == 2
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=3))
    assert ReadLimits().max_page_size == 1000
    assert IndexQuery((cut.source_id,), (TypedIndexClause('resource_version/v1'),)).page_size == 100


def test_invalid_root_aliases_and_cut_identity(include_reader):
    reader, cut = include_reader
    resource = root(reader, cut, 'resource')
    generic = SourceQualifiedVersionRef(cut.source_id, resource.ref.as_version_ref())
    wrong_kind = SourceQualifiedVersionRef(cut.source_id, VersionRef('operation_result/v1', new_id('invocation'), new_id('invocation_version')))
    for value in (resource.to_dict(), generic, wrong_kind, 'latest', None):
        code('INVALID_ROOT', lambda: reader.query_product_origin_v1(value, cut))
    code('CURSOR_MISMATCH', lambda: reader.query_product_origin_v1(root(reader, cut), replace(cut, cut_id='edited')))
    value = SourceQualifiedVersionRef('other', root(reader, cut).ref)
    code('INVALID_ROOT', lambda: reader.query_product_origin_v1(value, cut))


def test_complete_preauthorization_precedes_missing_root(includes_world, monkeypatch):
    world = includes_world[0]
    permission = grant(world, remove=('petri_token/v1', 'resource_ref'), command='public:missing-permission')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        requested = SourceQualifiedVersionRef(cut.source_id, VersionRef('operation_result/v1', new_id('operation_result'), new_id('operation_result_version')))
        monkeypatch.setattr(public, '_evaluate_origin_includes', lambda *a: pytest.fail('must preauthorize'))
        code('NOT_DISCLOSED', lambda: reader.query_product_origin_v1(requested, cut, page_size=1))


def test_late_claim_failure_prevents_page_one(include_reader):
    reader, cut = include_reader
    snap = snapshot_of(reader, cut)
    firing = json.loads(snap.objects[descriptor_ref(reader, cut, 'transition_firing/v1')['version_id']]['metadata_json'])
    missing = firing['claimed_input_refs'][-1]['version_id']
    objects = dict(snap.objects); objects.pop(missing)
    set_snapshot(reader, cut, replace(snap, objects=objects))
    code('INTEGRITY_FAILED', lambda: query(reader, cut, page_size=1))
    assert not reader._cursors


def test_missing_root_and_dependency_codes(include_reader, monkeypatch):
    reader, cut = include_reader
    missing = SourceQualifiedVersionRef(cut.source_id, VersionRef('operation_result/v1', new_id('operation_result'), new_id('operation_result_version')))
    code('NOT_PRESENT_AT_CUT', lambda: reader.query_product_origin_v1(missing, cut))
    # The evaluator is the protected boundary; this models a configured reader
    # reporting a dependent miss after the requested root was found.
    monkeypatch.setattr(public, '_evaluate_origin_includes', lambda *args: (_ for _ in ()).throw(RegistryReadSessionError('NOT_PRESENT_AT_CUT')))
    code('INTEGRITY_FAILED', lambda: query(reader, cut))


def test_paging_replay_mutation_isolation_and_shared_state(include_reader, monkeypatch):
    reader, cut = include_reader
    first = query(reader, cut, page_size=1)
    token = first['continuation']; original_record = reader._cursors[token]
    duplicate = query(reader, cut, page_size=1)
    assert duplicate == first and reader._cursors[token] is original_record
    monkeypatch.setattr(public, '_evaluate_origin_includes', lambda *a: pytest.fail('replay evaluated closure'))
    first['root_proof'].clear(); first['rows'][0]['evidence'].clear()
    second = query(reader, cut, include=list(reversed(INCLUDE_ORDER)), page_size=1, cursor=token)
    assert second['root_proof'] == duplicate['root_proof']
    assert second['coverage']['relations']['start_inputs']['state'] == 'complete'
    assert second['coverage']['relations']['claims']['state'] == 'partial'
    slots = len(reader._cursors)
    assert query(reader, cut, page_size=1, cursor=token) == second and len(reader._cursors) == slots
    assert len({id(state) for state, offset in reader._cursors.values()}) == 1
    third = query(reader, cut, page_size=1, cursor=second['continuation'])
    fourth = query(reader, cut, page_size=1, cursor=third['continuation'])
    assert fourth['continuation'] is None and fourth['coverage']['state'] == 'complete'
    assert len(reader._cursors) == 3
    assert query(reader, cut, page_size=1, cursor=third['continuation']) == fourth


def test_empty_requested_coverage(inputless_world):
    world = inputless_world[0]
    permission = grant(world, command='public:inputless')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1]); page = query(reader, cut, page_size=1)
        assert page['rows'] == [] and page['continuation'] is None
        assert all(page['coverage']['relations'][name]['state'] == 'complete' for name in INCLUDE_ORDER)
    # Mechanical delivery boundary, not a claimed static-binding history.
    coverage = public._coverage(INCLUDE_ORDER, ((0, 3), (3, 3)), 1, 3)
    assert coverage['relations']['claims']['state'] == 'complete'
    assert coverage['relations']['start_inputs']['state'] == 'partial'


@pytest.mark.parametrize('change', ['size', 'include', 'root', 'cut'])
def test_cursor_request_mismatch(include_reader, change):
    reader, cut = include_reader
    token = query(reader, cut, page_size=1)['continuation']
    kwargs = {'page_size': 1, 'cursor': token}
    requested, selected = root(reader, cut), cut
    if change == 'size': kwargs['page_size'] = 2
    if change == 'include': kwargs['include'] = ('producer_execution', 'claims')
    if change == 'root': requested = root(reader, cut, 'resource')
    if change == 'cut': selected = reader.capture_cut(cut.source_id)
    code('CURSOR_MISMATCH', lambda: reader.query_product_origin_v1(requested, selected, **kwargs))
    assert token in reader._cursors


def test_reciprocal_domains_and_shared_pool(includes_world, monkeypatch):
    world = includes_world[0]
    indexes = dict(CORE_INDEX_FIELDS)
    indexes['resource_version/v1'] = ('summary',)
    indexes['transition_firing/v1'] = ('start_event_id',)
    permission = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(indexes, dict(record_fields(True, INCLUDE_ORDER))), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='public:pool')
    with rf.session(world, permission, limits=ReadLimits(max_cursors=2)) as reader:
        cut = reader.capture_cut(world[-1])
        spec = IndexQuery((cut.source_id,), (TypedIndexClause('resource_version/v1', projection=('summary',)),), 1, {cut.source_id: cut})
        index_token = reader.query_index(spec)['continuation']
        origin_token = query(reader, cut, page_size=1)['continuation']
        assert len(reader._cursors) == 2
        code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1, cursor=index_token))
        code('CURSOR_MISMATCH', lambda: reader.query_index(spec, cursor=origin_token))
        start_spec = IndexQuery((cut.source_id,), (TypedIndexClause('transition_firing/v1', projection=('start_event_id',)),), 1)
        import cpn.rpnh.collaboration.registry_read_session as sessions
        monkeypatch.setattr(sessions._StartReadOwners, '__init__', lambda *a: pytest.fail('wrong-kind Start owner'))
        code('CURSOR_MISMATCH', lambda: reader.query_index(start_spec, cursor=origin_token))
        code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=1, cursor=origin_token))
        assert len(reader._cursors) == 2 and origin_token in reader._cursors and index_token in reader._cursors


def test_whole_envelope_byte_fit_and_failure_rollback(include_reader):
    reader, cut = include_reader
    one = query(reader, cut, page_size=1)
    reader._cursors.clear()
    limit = len(canonical_json(one))
    reader.limits = replace(reader.limits, max_response_bytes=limit)
    page = query(reader, cut, page_size=4)
    assert len(page['rows']) == 1 and len(canonical_json(page)) <= limit
    reader._cursors.clear()
    reader.limits = replace(reader.limits, max_response_bytes=limit - 1)
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=4))
    assert not reader._cursors
    reader.limits = replace(reader.limits, max_response_bytes=100)
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, include=('producer_execution',)))
    assert not reader._cursors


def test_state_and_record_totals_do_not_reserialize(include_reader, monkeypatch):
    reader, cut = include_reader
    before = reader._retained_bytes()
    first = query(reader, cut, page_size=1)
    state = reader._cursors[first['continuation']][0]
    second = query(reader, cut, page_size=1, cursor=first['continuation'])
    expected = before + state.budget_bytes + sum(public._cursor_record_bytes(token, offset) for token, (_state, offset) in reader._cursors.items())
    monkeypatch.setattr(public, 'canonical_json', lambda *a: pytest.fail('accounting serialization'))
    assert reader._retained_bytes() == expected


@pytest.mark.parametrize('mode', ['first', 'replay', 'protected_error'])
@pytest.mark.parametrize('change', ['expiry', 'catalog', 'binding'])
def test_post_serialization_current_authority_wins(includes_world, full_grant, monkeypatch, mode, change):
    world = includes_world[0]; generation = ['1']
    with rf.session(world, full_grant, generation=generation) as reader:
        cut = reader.capture_cut(world[-1]); kwargs = {'page_size': 1}
        if mode == 'replay': kwargs['cursor'] = query(reader, cut, **kwargs)['continuation']
        if mode == 'protected_error':
            change_start(reader, cut, lambda event: replace(event, payload={}))
        original = public.canonical_json
        hit = []
        def encode(value):
            result = original(value)
            trigger = isinstance(value, dict) and (value.get('schema_version') == 'rpnh/product_origin_page/v1'
                if mode != 'protected_error' else value.get('code') == 'INTEGRITY_FAILED')
            if trigger and not hit:
                hit.append(True)
                if change == 'expiry': reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                elif change == 'binding': generation[0] = 'changed'
                else: monkeypatch.setattr(reader._catalog, 'fingerprint', lambda: 'changed-catalog')
            return result
        monkeypatch.setattr(public, 'canonical_json', encode)
        code('SESSION_EXPIRED' if change == 'expiry' else 'ACCESS_CHANGED', lambda: query(reader, cut, **kwargs))
        assert hit and not reader._cursors and not reader._cuts


def test_full_delivery_contract_in_fingerprint(include_reader, monkeypatch):
    reader, cut = include_reader
    token = query(reader, cut, page_size=1)['continuation']
    old = public.product_origin_delivery_contract
    def changed():
        data = old(); data['extra_contract_fact'] = 'same-label-changed-content'; return data
    monkeypatch.setattr(public, 'product_origin_delivery_contract', changed)
    code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1, cursor=token))


@pytest.mark.parametrize('replay', [False, True])
def test_invalidation_retains_pinned_union_through_safe_error(include_reader, monkeypatch, replay):
    reader, cut = include_reader
    snapshot = snapshot_of(reader, cut)
    kwargs = {'page_size': 1}
    if replay: kwargs['cursor'] = query(reader, cut, **kwargs)['continuation']
    original_encode, original_check = public.canonical_json, reader.final_recheck
    marker = []; observed = []; expected = []
    original_reserve = public._OriginQueryContext.reserve
    def reserve(context, *args, **kw):
        try:
            return original_reserve(context, *args, **kw)
        finally:
            if expected:
                observed.append((context.reservation.retained_bytes, context.reservation.scratch_bytes))
    def encode(value):
        result = original_encode(value)
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/product_origin_page/v1': marker.append(True)
        return result
    def final(sources=None):
        if marker and not expected:
            states = {id(state): state for state, offset in reader._cursors.values()}
            records = sum(public._cursor_record_bytes(token, offset) for token, (state, offset) in reader._cursors.items())
            reader._invalidate(cut.source_id)
            other = _Query('unrelated', ({'opaque': True},), {}, (), {}, {'other': 12345})
            reader._cursors['unrelated'] = (other, 0)
            expected.append(snapshot.budget_bytes + sum(state.budget_bytes for state in states.values()) + records + 12345)
        return original_check(sources)
    monkeypatch.setattr(public, 'canonical_json', encode)
    monkeypatch.setattr(public._OriginQueryContext, 'reserve', reserve)
    monkeypatch.setattr(reader, 'final_recheck', final)
    code('ACCESS_CHANGED', lambda: query(reader, cut, **kwargs))
    assert observed and all(retained >= expected[0] and scratch > 0 for retained, scratch in observed)
    assert set(reader._cursors) == {'unrelated'}


def test_failure_rollback_never_removes_existing_replay_token(include_reader, monkeypatch):
    reader, cut = include_reader
    token = query(reader, cut, page_size=1)['continuation']; resident = reader._cursors[token]
    original = public.canonical_json
    def bad_response(value):
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/product_origin_page/v1':
            raise RuntimeError('private payload error')
        return original(value)
    monkeypatch.setattr(public, 'canonical_json', bad_response)
    code('INTEGRITY_FAILED', lambda: query(reader, cut, page_size=1, cursor=token))
    assert reader._cursors == {token: resident} and reader._cursors[token] is resident


def test_public_lazy_convenience_export(include_reader):
    from cpn.rpnh.collaboration import query_product_origin_v1
    reader, cut = include_reader
    assert query_product_origin_v1(reader, root(reader, cut), cut, ('producer_execution',))['rows'] == []


@pytest.mark.parametrize('terminal', ['expiry', 'catalog'])
def test_terminal_error_stays_stable_below_error_byte_cap(include_reader, monkeypatch, terminal):
    reader, cut = include_reader
    reader.limits = replace(reader.limits, max_response_bytes=1)
    change_start(reader, cut, lambda event: replace(event, payload={}))
    original = public.canonical_json
    calls = []
    def encode(value):
        result = original(value)
        if isinstance(value, dict) and 'code' in value:
            calls.append(value['code'])
            assert len(calls) < 8, 'safe error code oscillated'
            if terminal == 'expiry':
                reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            else:
                monkeypatch.setattr(reader._catalog, 'fingerprint', lambda: 'changed')
        return result
    monkeypatch.setattr(public, 'canonical_json', encode)
    expected = 'SESSION_EXPIRED' if terminal == 'expiry' else 'ACCESS_CHANGED'
    code(expected, lambda: query(reader, cut))
    assert calls[-1] == expected and not reader._cursors


def test_removed_success_continuation_fails_without_reinsertion(include_reader, monkeypatch):
    reader, cut = include_reader
    original_encode, original_check = public.canonical_json, reader.final_recheck
    serialized = []; removed = []
    def encode(value):
        result = original_encode(value)
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/product_origin_page/v1':
            serialized.append(True)
        return result
    def check(sources=None):
        original_check(sources)
        if serialized and not removed:
            removed.extend(reader._cursors)
            reader._cursors.clear()
    monkeypatch.setattr(public, 'canonical_json', encode)
    monkeypatch.setattr(reader, 'final_recheck', check)
    code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1))
    assert removed and not reader._cursors


def test_replaced_cursor_is_not_removed_by_failed_call(include_reader, monkeypatch):
    reader, cut = include_reader
    original_encode, original_check = public.canonical_json, reader.final_recheck
    serialized = []; replacement = {}
    def encode(value):
        result = original_encode(value)
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/product_origin_page/v1': serialized.append(True)
        return result
    def check(sources=None):
        original_check(sources)
        if serialized and not replacement:
            token, record = next(iter(reader._cursors.items()))
            replacement[token] = (record[0], record[1])
            reader._cursors[token] = replacement[token]
    monkeypatch.setattr(public, 'canonical_json', encode)
    monkeypatch.setattr(reader, 'final_recheck', check)
    code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1))
    assert reader._cursors == replacement
    assert all(reader._cursors[token] is record for token, record in replacement.items())


def test_new_state_separate_tag_and_callback_free_transfer(include_reader, monkeypatch):
    reader, cut = include_reader
    snapshots = reader._retained_bytes(); observed = []
    original_detach, original_stage = public._detach_state, public._stage
    def detach(fingerprint, data, context):
        before = context.reservation.scratch_bytes
        state = original_detach(fingerprint, data, context)
        assert context.reservation.scratch_bytes == before + state.budget_bytes
        assert reader._retained_bytes() == snapshots
        observed.append((before, state.budget_bytes))
        return state
    def stage(owner, fingerprint, data, end, token, state):
        before = owner.context.reservation.scratch_bytes
        result = original_stage(owner, fingerprint, data, end, token, state)
        # Detached state's local charge moved to the session; evaluator and
        # response allocations remain entirely in the same local reservation.
        assert owner.context.reservation.scratch_bytes == before
        assert reader._retained_bytes() == snapshots + result.budget_bytes + public._cursor_record_bytes(token, end)
        return result
    monkeypatch.setattr(public, '_detach_state', detach)
    monkeypatch.setattr(public, '_stage', stage)
    assert query(reader, cut, page_size=1)['continuation']
    assert observed and observed[0][0] > observed[0][1]


def test_final_retained_growth_uses_preowned_safe_error(include_reader, monkeypatch):
    reader, cut = include_reader
    owners = []; safe_codes = []; added = []
    original_init, original_encode, original_check = public._OriginDelivery.__init__, public.canonical_json, reader.final_recheck
    def begin(owner, *args):
        original_init(owner, *args); owners.append(owner)
    def encode(value):
        result = original_encode(value)
        if isinstance(value, dict) and 'code' in value:
            safe_codes.append(value['code'])
            assert owners[-1].context.reservation.scratch_bytes > public._ERROR_BUFFER_BYTES
        return result
    def check(sources=None):
        original_check(sources)
        owner = owners[-1] if owners else None
        if owner and owner.created and not added:
            # Mechanical retained-ledger boundary: model unrelated index bytes
            # already charged by its collector, without broad fixture history.
            needed = reader.limits.max_scan_bytes - reader._retained_bytes() - owner.context.reservation.scratch_bytes + 1
            assert needed > 0
            reader._cursors['unrelated'] = (_Query('other', ({'opaque': True},), {}, (), {}, {'other': needed}), 0)
            added.append(True)
    monkeypatch.setattr(public._OriginDelivery, '__init__', begin)
    monkeypatch.setattr(public, 'canonical_json', encode)
    monkeypatch.setattr(reader, 'final_recheck', check)
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=1))
    assert added and safe_codes[-1] == 'LIMIT_EXCEEDED'
    assert set(reader._cursors) == {'unrelated'}


def test_preallocation_failure_leaves_no_new_cursor(include_reader, monkeypatch):
    reader, cut = include_reader
    original = public._detach_state
    reached = []
    def detach(fingerprint, data, context):
        bound = public._bytes_bound(data) + public._bytes_bound(fingerprint) + 256
        limit = reader._retained_bytes() + context.reservation.scratch_bytes + 3 * bound - 1
        limits = replace(reader.limits, max_scan_bytes=limit)
        reader.limits = limits; context.reservation.limits = limits
        reached.append(True)
        return original(fingerprint, data, context)
    monkeypatch.setattr(public, '_detach_state', detach)
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=1))
    assert reached and not reader._cursors


def test_request_bytes_and_per_type_projection_bound(include_reader, monkeypatch):
    reader, cut = include_reader
    reader.limits = replace(reader.limits, max_projection_fields=20)
    # The full profile has more than 20 fields in aggregate, but no individual
    # record/index projection exceeds 20. No new aggregate cap is introduced.
    assert query(reader, cut)['coverage']['state'] == 'complete'
    reader.limits = replace(reader.limits, max_query_bytes=1)
    monkeypatch.setattr(public, '_evaluate_origin_includes', lambda *a: pytest.fail('oversize request read data'))
    code('LIMIT_EXCEEDED', lambda: query(reader, cut))


def test_retained_public_error_detaches_private_frames(include_reader, monkeypatch):
    import gc
    import weakref
    reader, cut = include_reader
    candidates, contexts = [], []
    class PrivateCandidate:
        pass
    def fail(verifier, *args):
        verifier.context.reserve(size=4096)
        candidate = PrivateCandidate()
        candidates.append(weakref.ref(candidate)); contexts.append(verifier.context)
        raise RuntimeError('private closure candidate')
    monkeypatch.setattr(public, '_evaluate_origin_includes', fail)
    error = code('INTEGRITY_FAILED', lambda: query(reader, cut, page_size=1))
    assert error.__cause__ is None and error.__context__ is None
    gc.collect()
    assert candidates and all(candidate() is None for candidate in candidates)
    assert all(context.snapshot is None and context._session is None and not context._cache
        and context.reservation.scratch_bytes == 0 for context in contexts)
    traceback = error.__traceback__
    while traceback:
        if '/collaboration/' in traceback.tb_frame.f_code.co_filename:
            assert not {'candidate', 'snapshot', 'source', 'state', 'data', 'response', 'context', 'owner'}.intersection(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_canonical_size_matches_encoding_without_buffer():
    for value in ('abc', '\\"\b\f\n\r\t\x00\x1f\x7f', '中文😀', None, True, False, 0, -123,
            {'z': [1, '😀'], 'a': ('x', None)}):
        assert public._canonical_size(value) == len(canonical_json(value))


def test_public_host_configuration_uses_same_session(includes_world, full_grant, tmp_path):
    from cpn.rpnh.collaboration import open_read_host_session
    world = includes_world[0]
    source_ref = SourceQualifiedVersionRef(world[-1], world[2].task_ref)
    document = {'schema_version': 'rpnh/registry_read_host_config/v1', 'purpose': 'inspect', 'sources': [{
        'source_ref': source_ref.to_dict(), 'access_path': 'local', 'registry_root': str(world[0].run_dir),
        'binding_generation': '1', 'observer_context': full_grant.to_dict()}]}
    path = tmp_path / 'host.json'; path.write_bytes(canonical_json(document)); path.chmod(0o600)
    with open_read_host_session(path) as reader:
        cut = reader.capture_cut(world[-1])
        result = query(reader, cut, page_size=1)
        assert result['schema_version'] == 'rpnh/product_origin_page/v1' and result['continuation']


def test_replay_cleared_during_validate_cut_has_no_stale_owner(include_reader, monkeypatch):
    reader, cut = include_reader
    token = query(reader, cut, page_size=1)['continuation']
    original = reader.final_recheck
    cleared = []
    def check(sources=None):
        original(sources)
        if not cleared:
            reader._cursors.clear()
            cleared.append(True)
    monkeypatch.setattr(reader, 'final_recheck', check)
    monkeypatch.setattr(public._OriginDelivery, '__init__', lambda *a: pytest.fail('stale replay owner'))
    code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1, cursor=token))
    assert cleared and not reader._cursors


def test_constructor_failure_closes_allocated_context(include_reader, monkeypatch):
    reader, cut = include_reader
    reader.limits = replace(reader.limits, max_scan_bytes=reader._retained_bytes() + 512)
    closed = []
    original = public._OriginQueryContext.close
    def close(context):
        original(context)
        closed.append((context.snapshot, context._session, context._retained_extra,
            context.reservation.scratch_bytes))
    monkeypatch.setattr(public._OriginQueryContext, 'close', close)
    code('LIMIT_EXCEEDED', lambda: query(reader, cut, page_size=1))
    assert closed == [(None, None, None, 0)] and not reader._cursors


def test_legacy_invalid_query_precedes_unknown_cursor(include_reader):
    reader, _cut = include_reader
    for cursor in ('unknown', 123, []):
        with pytest.raises(TypeError, match='typed IndexQuery'):
            reader.query_index(None, cursor=cursor)


@pytest.mark.parametrize('unsafe_code', [[], {'private': 'value'}, 'PRIVATE_UNLISTED_DETAIL'])
def test_untrusted_exception_code_is_whitelisted(include_reader, monkeypatch, unsafe_code):
    reader, cut = include_reader
    class PrivateFailure(Exception):
        pass
    def fail(*args):
        error = PrivateFailure('private dependency payload')
        error.code = unsafe_code
        raise error
    monkeypatch.setattr(public, '_evaluate_origin_includes', fail)
    error = code('INTEGRITY_FAILED', lambda: query(reader, cut, page_size=1))
    assert error.__context__ is None and not reader._cursors


@pytest.mark.parametrize('mode', ['replay', 'final_page', 'first_snapshot'])
def test_serialization_cannot_resurrect_invalidated_pins(include_reader, monkeypatch, mode):
    reader, cut = include_reader
    kwargs = {'page_size': 1}
    if mode != 'first_snapshot':
        page = query(reader, cut, **kwargs)
        if mode == 'final_page':
            page = query(reader, cut, page_size=1, cursor=page['continuation'])
            page = query(reader, cut, page_size=1, cursor=page['continuation'])
        kwargs['cursor'] = page['continuation']
    original = public.canonical_json
    cleared = []
    def encode(value):
        result = original(value)
        if isinstance(value, dict) and value.get('schema_version') == 'rpnh/product_origin_page/v1':
            reader._cursors.clear()
            if mode == 'first_snapshot': reader._cuts.clear()
            cleared.append(True)
        return result
    monkeypatch.setattr(public, 'canonical_json', encode)
    code('CURSOR_MISMATCH', lambda: query(reader, cut, **kwargs))
    assert cleared and not reader._cursors


def test_state_serialization_cannot_publish_after_snapshot_removal(include_reader, monkeypatch):
    reader, cut = include_reader
    cleared = []
    class GuardedPool(dict):
        def __setitem__(self, key, value):
            assert not cleared, 'inserted a cursor after state-serialization invalidation'
            return super().__setitem__(key, value)
    reader._cursors = GuardedPool(reader._cursors)
    original = public.canonical_json
    def encode(value):
        result = original(value)
        if isinstance(value, dict) and 'boundaries' in value and 'row_bytes' in value:
            reader._cuts.clear(); reader._cursors.clear(); cleared.append(True)
        return result
    monkeypatch.setattr(public, 'canonical_json', encode)
    code('CURSOR_MISMATCH', lambda: query(reader, cut, page_size=1))
    assert cleared and not reader._cursors
