"""Private include closure: original offline owner facts and labelled corruptions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

import test_registry_read_session as rf
import test_source_set_query as owners
from test_product_origin_core import (replace_descriptor, snapshot_of, set_snapshot, descriptor_ref,
    extended_world)
from cpn.rpnh.collaboration._origin_core_contract import (record_fields, CORE_INDEX_FIELDS,
    INCLUDE_ORDER, INCLUDE_RECORD_FIELDS, START_FIELDS)
from cpn.rpnh.collaboration._product_origin_includes import _verify_origin_includes
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.identities import new_id


def grant(world, include=INCLUDE_ORDER, *, resource_root=True, remove=None, command='includes:grant'):
    records = dict(record_fields(resource_root, include))
    if remove:
        kind, field = remove
        records[kind] = tuple(value for value in records[kind] if value != field)
    return issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope(dict(CORE_INDEX_FIELDS), records), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id=command)


def _make_world(tmp_path_factory, *, inputless=False):
    """Two real token claims/Start inputs: one consumed and one non-consuming."""
    from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.run import OwnerInput, start_run
    schemas, types, paths = owners.source_observation_schema_data()
    extra, more, extra_paths = rf.observer_access_schema_data()
    catalog = SchemaCatalog(schemas={**schemas, **extra},
        types=tuple({item.name: item for item in (*types, *more)}.values()), schema_paths={**paths, **extra_paths})
    registration = Registration(); register_basic_components(registration)
    registration.register_schema(owners.TEXT, {'$id': owners.TEXT, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
    registration.register_executor(owners.EXECUTOR, owners.forbidden_executor,
        identity={'implementation_id': 'tests.origin_includes', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'input_ports': None, 'output_ports': None, 'config_schema': CONFIG_SCHEMA_ID})
    registration.register_tool(owners.TERMINAL, owners.forbidden_executor,
        identity={'implementation_id': 'tests.origin_includes_terminal', 'revision': 'v1'},
        contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    data = owners._module().to_dict()
    data['components'] = [data['components'][0]]
    component = data['components'][0]
    component['config']['input_modes'] = {'request': 'consume', 'reference': 'read'}
    component['ports'].append({'name': 'reference', 'direction': 'input', 'schema': owners.TEXT})
    component['operations'][0]['inputs'].append('reference')
    data['entry'] = {'left': {'component': 'left', 'port': 'request'}, 'reference': {'component': 'left', 'port': 'reference'}}
    if inputless:
        component['config'] = {}
        component['ports'] = [port for port in component['ports'] if port['direction'] == 'output']
        component['operations'][0]['inputs'] = []
        data['entry'] = {}
    module = ModuleDeclaration.from_dict(data)
    values = {name: OwnerInput(owners.TEXT, canonical_json(name), 'Synthetic private input') for name in ('left', 'reference')}
    owner = start_run(module, registration, run_dir=tmp_path_factory.mktemp('origin-includes') / 'run',
        task_input=values['left'], entry_inputs={} if inputless else values,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']), ('rpnh/module_declaration/v1',), 4, 0, 4, 0),
        model_condition='offline-origin-includes-no-model', owner_statement='Synthetic offline query fixture; no business execution',
        command_id='includes:fixture', catalog=catalog)
    owner.schema_gateway.bind_source_identity(source_id='includes-source', command_id='includes:bind')
    admitted = owner.admit('left.run', logical_tau=0, command_id='includes:admit')
    execution = owner.start(admitted, command_id='includes:start')
    products = owner.products(execution, outcome_id='complete', products={'left.result': (canonical_json('Synthetic output'),)}, command_id='includes:products')
    owner.succeed(products, command_id='includes:success')
    core = owner._core
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    world = (core, owner.schema_gateway, owner.identity, bootstrap, owner.principal_ref, 'includes-source')
    return world, admitted.admission.context


@pytest.fixture(scope='module')
def includes_world(tmp_path_factory):
    return _make_world(tmp_path_factory)


@pytest.fixture(scope='module')
def inputless_world(tmp_path_factory):
    return _make_world(tmp_path_factory, inputless=True)


@pytest.fixture(scope='module')
def full_grant(includes_world):
    return grant(includes_world[0])


@pytest.fixture
def include_reader(includes_world, full_grant):
    with rf.session(includes_world[0], full_grant) as reader:
        cut = reader.capture_cut(includes_world[0][-1])
        yield reader, cut


def root(reader, cut, kind='result'):
    if kind == 'result':
        ref = descriptor_ref(reader, cut, 'operation_result/v1')
        return SourceQualifiedVersionRef(cut.source_id, _version_from_payload(ref))
    row = next(row for row in snapshot_of(reader, cut).objects.values() if row['object_type'] == 'resource_version/v1'
        and json.loads(row['metadata_json'])['origin_kind'] == 'petri_output')
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.identities import TypedId
    return SourceQualifiedResourceRef(cut.source_id, ResourceVersionRef(TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))


def start_event(reader, cut):
    return next(event for event in snapshot_of(reader, cut).events if event.event_type == 'operation_execution_started/v1')


def change_start(reader, cut, change):
    snapshot = snapshot_of(reader, cut)
    event = start_event(reader, cut)
    modified = change(event)
    set_snapshot(reader, cut, replace(snapshot, events=tuple(modified if item is event else item for item in snapshot.events)))


@pytest.mark.parametrize('kind', ['result', 'resource'])
def test_default_fixed_rows_and_real_owner_zero_writes(includes_world, include_reader, kind):
    reader, cut = include_reader
    before = rf.counts(includes_world[0]); snapshot = snapshot_of(reader, cut)
    result = _verify_origin_includes(reader, root=root(reader, cut, kind), at_cut=cut)
    assert set(result) == {'root_proof', 'rows'} and len(result['root_proof']) == 6
    rows = result['rows']
    assert [row['role'] for row in rows] == ['start_input', 'start_input', 'claim', 'claim']
    assert {row['classification'] for row in rows[2:]} == {'consumed_claim', 'non_consuming_claim'}
    assert [row['position'] for row in rows[:2]] == [0, 1]
    for row in rows[:2]:
        assert set(row) == {'role', 'position', 'input_binding_ref', 'resource_ref', 'evidence', 'verification'}
        assert set(row['evidence']) == set(START_FIELDS[:3])
        assert set(row['input_binding_ref']['ref']) == {'entity_type', 'logical_id', 'version_id'}
        assert set(row['resource_ref']['ref']) == {'resource_id', 'resource_version_id'}
        assert row['verification'] == {'binding_identity': 'exact_at_cut', 'resource_identity': 'exact_at_cut',
            'target_record': 'not_requested', 'material': 'not_read'}
    assert all(row['verification'] == {'token_record': 'verified_at_cut', 'resource_target': 'not_requested', 'material': 'not_read'} for row in rows[2:])
    assert not reader._cursors and snapshot_of(reader, cut) is snapshot and snapshot._origin_context is None
    assert callable(reader.query_product_origin_v1) and rf.counts(includes_world[0]) == before
    assert result == _verify_origin_includes(reader, root=root(reader, cut, kind), at_cut=cut, include=tuple(reversed(INCLUDE_ORDER)))


@pytest.mark.parametrize('include', [('producer_execution',), ('producer_execution', 'claims'),
    ('producer_execution', 'start_inputs'), INCLUDE_ORDER])
def test_include_isolation_and_minimal_permissions(includes_world, include):
    world = includes_world[0]
    permission = grant(world, include, resource_root=False, command='includes:minimal:' + ':'.join(include))
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        if 'start_inputs' not in include:
            change_start(reader, cut, lambda event: replace(event, payload={}))
        result = _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut, include=include)
        assert [row['role'] for row in result['rows']] == (['start_input'] * 2 if 'start_inputs' in include else []) + (['claim'] * 2 if 'claims' in include else [])


_NEW_FIELDS = [(kind, field) for _relation, table in INCLUDE_RECORD_FIELDS for kind, fields in table for field in fields]
@pytest.mark.parametrize('remove', _NEW_FIELDS)
def test_all_new_permissions_precede_object_reads(includes_world, remove, monkeypatch):
    from cpn.rpnh.collaboration._product_origin_core import _CoreProof
    world = includes_world[0]
    permission = grant(world, remove=remove, command='includes:missing:' + ':'.join(remove))
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1]); requested = root(reader, cut)
        monkeypatch.setattr(_CoreProof, 'exact', lambda *_a, **_k: pytest.fail('read before full preauthorization'))
        with pytest.raises(RegistryReadSessionError) as caught:
            _verify_origin_includes(reader, root=requested, at_cut=cut)
        assert caught.value.code == 'NOT_DISCLOSED'


@pytest.mark.parametrize('include,code', [((), 'INVALID_QUERY'), (['claims'], 'INVALID_QUERY'),
    (['producer_execution', 'producer_execution'], 'INVALID_QUERY'), ('claims', 'INVALID_QUERY'),
    (['producer_execution', 1], 'INVALID_QUERY'), (['producer_execution', 'direct_derivations'], 'UNSUPPORTED_RELATION')])
def test_invalid_include_before_reads(include_reader, monkeypatch, include, code):
    reader, cut = include_reader; requested = root(reader, cut)
    monkeypatch.setattr(reader, 'validate_cut', lambda *_: pytest.fail('static include must precede cut'))
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=requested, at_cut=cut, include=include)
    assert caught.value.code == code


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'schema_label', 'type_label', 'wrong_version',
    'aggregate', 'stream', 'principal', 'ordinal_bool', 'claims_order', 'lengths', 'binding_duplicate',
    'binding_kind', 'resource_shape', 'I', 'F', 'L', 'B', 'counter_bool'])
def test_start_conflicts_and_pairing_are_safe(include_reader, damage):
    reader, cut = include_reader; requested = root(reader, cut); snapshot = snapshot_of(reader, cut)
    event = start_event(reader, cut)
    if damage == 'missing':
        events = tuple(item for item in snapshot.events if item is not event)
    elif damage in ('duplicate', 'schema_label', 'type_label', 'wrong_version'):
        extra = replace(event, event_id=new_id('event'))
        if damage == 'schema_label': extra = replace(extra, event_type='bad_type/v1', aggregate_type='wrong')
        if damage == 'type_label': extra = replace(extra, payload_schema_ref='registry_v1/bad_type/v1', aggregate_id='wrong')
        if damage == 'wrong_version':
            extra = replace(extra, aggregate_id='wrong', stream_id='wrong', payload={**extra.payload,
                'invocation_ref': {**extra.payload['invocation_ref'], 'version_id': str(new_id('invocation_version'))}})
        events = (*snapshot.events, extra)
    else:
        payload = dict(event.payload); changed = event
        if damage in ('I', 'F', 'L', 'B'):
            key = {'I': 'invocation_ref', 'F': 'transition_firing_ref', 'L': 'operation_execution_lease_ref', 'B': 'operation_binding_ref'}[damage]
            payload[key] = {**payload[key], 'version_id': str(new_id(payload[key]['entity_type'].split('/')[0] + '_version'))}
        elif damage == 'aggregate': changed = replace(event, aggregate_type='wrong')
        elif damage == 'stream': changed = replace(event, stream_id='wrong')
        elif damage == 'principal': changed = replace(event, producer_principal=str(new_id('principal')))
        elif damage == 'ordinal_bool': changed = replace(event, ordinal=True)
        elif damage == 'claims_order': payload['claimed_input_refs'] = list(reversed(payload['claimed_input_refs']))
        elif damage == 'lengths': payload['input_resource_refs'] = payload['input_resource_refs'][:1]
        elif damage == 'binding_duplicate': payload['input_binding_refs'] = [payload['input_binding_refs'][0]] * 2
        elif damage == 'binding_kind': payload['input_binding_refs'] = [{**payload['input_binding_refs'][0], 'entity_type': 'principal/v1'}, *payload['input_binding_refs'][1:]]
        elif damage == 'resource_shape': payload['input_resource_refs'] = [payload['input_binding_refs'][0], *payload['input_resource_refs'][1:]]
        elif damage == 'counter_bool': payload['admission_registry_ordinal'] = True
        changed = replace(changed, payload=payload)
        events = tuple(changed if item is event else item for item in snapshot.events)
    set_snapshot(reader, cut, replace(snapshot, events=events))
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=requested, at_cut=cut)
    assert caught.value.code == 'INTEGRITY_FAILED'
    assert set(caught.value.to_dict()) == {'code', 'message', 'reopen_session'} and not reader._cursors


@pytest.mark.parametrize('damage', ['phase', 'net', 'F', 'B', 'deposited', 'consumed_duplicate', 'consumed_outside',
    'consumed_same_version', 'version_summary', 'claim_duplicate', 'token_net', 'token_self', 'token_publication', 'delta_producer', 'delta_transaction'])
def test_claim_semantics_full_identity(include_reader, request, damage):
    reader, cut = include_reader; requested = root(reader, cut)
    Fref = descriptor_ref(reader, cut, 'transition_firing/v1')
    F = json.loads(snapshot_of(reader, cut).objects[Fref['version_id']]['metadata_json'])
    Dc = F['claim_marking_delta_ref']; token = F['claimed_input_refs'][0]
    target = Fref if damage in ('version_summary', 'claim_duplicate') else token if damage.startswith('token') else Dc
    if damage in ('delta_producer', 'delta_transaction', 'token_publication'):
        snapshot = snapshot_of(reader, cut); row = snapshot.objects[target['version_id']]
        key = 'producer_invocation_id' if damage == 'delta_producer' else 'transaction_id' if damage == 'delta_transaction' else 'published_event_id'
        kind = 'invocation' if damage == 'delta_producer' else 'transaction' if damage == 'delta_transaction' else 'event'
        set_snapshot(reader, cut, replace(snapshot, objects={**snapshot.objects, target['version_id']: {**row, key: str(new_id(kind))}}))
    else:
        def mutate(body):
            if damage == 'phase': body['phase'] = 'settlement'
            elif damage in ('net', 'token_net'): body['net_instance_ref'] = {**body['net_instance_ref'], 'version_id': str(new_id('net_instance_version'))}
            elif damage in ('F', 'B'):
                key = 'transition_firing_refs' if damage == 'F' else 'operation_binding_refs'; body[key] = []
            elif damage == 'deposited': body['deposited_refs'] = [token]
            elif damage == 'consumed_duplicate': body['consumed_refs'] = [token, token]
            elif damage == 'consumed_outside': body['consumed_refs'] = [{**token, 'version_id': str(new_id('petri_token_version'))}]
            elif damage == 'consumed_same_version': body['consumed_refs'] = [{**token, 'logical_id': str(new_id('petri_token'))}]
            elif damage == 'version_summary': body['claimed_input_version_ids'] = []
            elif damage == 'claim_duplicate': body['claimed_input_refs'] = [token, token]
            elif damage == 'token_self': body['petri_token_ref'] = {**token, 'logical_id': str(new_id('petri_token'))}
        replace_descriptor(reader, cut, target, mutate, request)
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=requested, at_cut=cut, include=('producer_execution', 'claims'))
    assert caught.value.code == 'INTEGRITY_FAILED' and set(caught.value.to_dict()) == {'code', 'message', 'reopen_session'}


def test_no_resource_body_or_unlisted_dependency_reads(include_reader, monkeypatch):
    from cpn.rpnh.collaboration import registry_typed_readers as readers
    from cpn.rpnh.collaboration._product_origin_core import _CoreProof
    reader, cut = include_reader; requested = root(reader, cut)
    original_payload = readers.payload_at
    def payload(core, reference, snapshot, **kwargs):
        assert readers.wire_ref(reference)[1]['entity_type'] != 'resource_version/v1'
        return original_payload(core, reference, snapshot, **kwargs)
    monkeypatch.setattr(readers, 'payload_at', payload)
    original_exact = _CoreProof.exact
    forbidden = {'operation_spec/v1', 'executable_transition_binding/v1', 'principal/v1', 'user_authority_decision/v1', 'agent/v1', 'resource_delivery/v1'}
    def exact(self, ref, expected, **kwargs):
        assert expected not in forbidden
        return original_exact(self, ref, expected, **kwargs)
    monkeypatch.setattr(_CoreProof, 'exact', exact)
    _verify_origin_includes(reader, root=requested, at_cut=cut)


def test_one_owner_one_core_and_actual_shared_array_cache(include_reader, monkeypatch):
    from cpn.rpnh.collaboration import _product_origin_core as core
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    reader, cut = include_reader
    owners_seen, runs, collections, array_loads, closes = [], [], [], [], []
    init, run, collect, array, close = _OriginQueryContext.__init__, core._CoreProof.run, reader._collect_index, _OriginQueryContext.semantic_array, _OriginQueryContext.close
    def initialize(self, *args, **kwargs):
        owners_seen.append(self); return init(self, *args, **kwargs)
    def evaluate(self, *args): runs.append(self); return run(self, *args)
    def collection(*args, **kwargs): collections.append(True); return collect(*args, **kwargs)
    def semantic(self, values, category, contract, validator):
        def checked(items): array_loads.append((id(items), category, contract)); return validator(items)
        return array(self, values, category, contract, checked)
    def closing(self):
        closes.append(self); close(self)
        assert self.reservation.scratch_bytes == 0 and not self._cache and self._event_index is None
    monkeypatch.setattr(_OriginQueryContext, '__init__', initialize); monkeypatch.setattr(core._CoreProof, 'run', evaluate)
    monkeypatch.setattr(reader, '_collect_index', collection); monkeypatch.setattr(_OriginQueryContext, 'semantic_array', semantic)
    monkeypatch.setattr(_OriginQueryContext, 'close', closing)
    _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)
    assert len(owners_seen) == len(runs) == len(collections) == len(closes) == 1
    assert sum(isinstance(contract, tuple) and contract[0] == 'whole-firing-claims/v1' for _, _, contract in array_loads) == 1


@pytest.mark.parametrize('damage', ['forged', 'missing', 'extra', 'pairing'])
def test_custom_catalog_cannot_forge_start(include_reader, monkeypatch, damage):
    reader, cut = include_reader; original = reader._catalog.read_exact
    def read(core, entry_ref, *, snapshot, projection=None):
        result = original(core, entry_ref, snapshot=snapshot, projection=projection)
        if projection and 'start_event_id' in projection:
            if damage == 'forged': result['start_event_id'] = str(new_id('event'))
            elif damage == 'missing': result.pop('start_event_id')
            elif damage == 'extra': result['arbitrary'] = 'hidden'
            elif damage == 'pairing': result['start_input_resource_refs'].reverse()
        return result
    monkeypatch.setattr(reader._catalog, 'read_exact', read)
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)
    assert caught.value.code == 'INTEGRITY_FAILED'


def test_workspace_private_includes_smoke(extended_world):
    world, _, workspace_root, _ = extended_world
    permission = grant(world, command='includes:workspace:grant')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        result = _verify_origin_includes(reader, root=workspace_root, at_cut=cut)
        assert result['root_proof']['root_role'] == 'invocation_produced_resource'
        assert {row['role'] for row in result['rows']} == {'start_input', 'claim'}


def test_custom_catalog_rejects_numeric_type_forgery(include_reader, monkeypatch):
    reader, cut = include_reader; original = reader._catalog.read_exact
    def read(core, entry_ref, *, snapshot, projection=None):
        result = original(core, entry_ref, snapshot=snapshot, projection=projection)
        if 'start_ordinal' in result:
            result['start_ordinal'] = float(result['start_ordinal'])
        return result
    monkeypatch.setattr(reader._catalog, 'read_exact', read)
    with pytest.raises(RegistryReadSessionError, match='integrity failed'):
        _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)


def test_all_rows_scan_budget_not_temporary_response_cap(include_reader):
    reader, cut = include_reader
    reader.limits = replace(reader.limits, max_response_bytes=32)
    # This private complete handoff is not a public page. Rows stay scan-
    # accounted; the future page owns the response limit.
    result = _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)
    assert len(canonical_json(result)) > reader.limits.max_response_bytes


@pytest.mark.parametrize('limit_delta', [0, -1])
def test_combined_work_boundary_is_atomic(include_reader, monkeypatch, limit_delta):
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    reader, cut = include_reader; totals = []; closing = _OriginQueryContext.close
    def close(self): totals.append(self.reservation.work); closing(self)
    monkeypatch.setattr(_OriginQueryContext, 'close', close)
    requested = root(reader, cut)
    baseline = _verify_origin_includes(reader, root=requested, at_cut=cut)
    limit = totals[-1] + limit_delta
    original = _OriginQueryContext.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.reservation.limits = replace(self.reservation.limits, max_scan_rows=limit)
    monkeypatch.setattr(_OriginQueryContext, '__init__', initialize)
    if limit_delta:
        with pytest.raises(RegistryReadSessionError) as caught:
            _verify_origin_includes(reader, root=requested, at_cut=cut)
        assert caught.value.code == 'LIMIT_EXCEEDED' and set(caught.value.to_dict()) == {'code', 'message', 'reopen_session'}
    else:
        assert baseline == _verify_origin_includes(reader, root=requested, at_cut=cut)


def test_start_array_reservation_precedes_event_validation(include_reader, monkeypatch):
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    from cpn.rpnh.collaboration._product_origin_core import _CoreProof
    reader, cut = include_reader; reserve = _OriginQueryContext.reserve; envelope = _CoreProof._event_envelope
    def limited(self, category=None, **kwargs):
        if category == 'S':
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        return reserve(self, category, **kwargs)
    def checked(self, event):
        assert event.event_type != 'operation_execution_started/v1', 'Start expanded before S reservation'
        return envelope(self, event)
    monkeypatch.setattr(_OriginQueryContext, 'reserve', limited); monkeypatch.setattr(_CoreProof, '_event_envelope', checked)
    with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
        _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)


def test_every_candidate_charged_before_first_validation(include_reader, monkeypatch):
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    reader, cut = include_reader; snapshot = snapshot_of(reader, cut); event = start_event(reader, cut)
    set_snapshot(reader, cut, replace(snapshot, events=(*snapshot.events, replace(event, event_id=new_id('event')))))
    original = _OriginQueryContext.validate_events; observed = []
    def validate(self, events, contract, validator):
        before = self.reservation.work
        def checked(item):
            if contract[0] == 'start-candidate/v1':
                observed.append(self.reservation.work - before)
                assert self.reservation.work - before >= len(events)
            return validator(item)
        return original(self, events, contract, checked)
    monkeypatch.setattr(_OriginQueryContext, 'validate_events', validate)
    with pytest.raises(RegistryReadSessionError, match='integrity failed'):
        _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)
    assert len(observed) == 2


@pytest.mark.parametrize('pending_error', [False, True])
def test_final_recheck_wins_and_failure_caches_remain_charged(include_reader, monkeypatch, pending_error):
    from cpn.rpnh.collaboration import _product_origin_core as core
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    reader, cut = include_reader; requested = root(reader, cut)
    if pending_error:
        change_start(reader, cut, lambda event: replace(event, payload={**event.payload, 'input_resource_refs': []}))
    live = []; closed = []; init = _OriginQueryContext.__init__; close = _OriginQueryContext.close
    def initialize(self, *args, **kwargs): init(self, *args, **kwargs); live.append(self)
    def closing(self): close(self); closed.append(self)
    monkeypatch.setattr(_OriginQueryContext, '__init__', initialize); monkeypatch.setattr(_OriginQueryContext, 'close', closing)
    original_json = core.canonical_json; serialization = []
    def encoded(value):
        if isinstance(value, dict) and ('rows' in value or value.get('code') == 'INTEGRITY_FAILED'):
            serialization.append(value)
        return original_json(value)
    monkeypatch.setattr(core, 'canonical_json', encoded)
    original_check = reader.final_recheck
    def check(sources):
        if serialization:
            assert live[-1].reservation.scratch_bytes > 0 and live[-1]._cache and not closed
            raise RegistryReadSessionError('ACCESS_CHANGED')
        return original_check(sources)
    monkeypatch.setattr(reader, 'final_recheck', check)
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=requested, at_cut=cut)
    assert caught.value.code == 'ACCESS_CHANGED' and len(closed) == 1
    assert not live[-1]._cache and live[-1].reservation.scratch_bytes == 0


def test_unique_real_inputless_start_is_empty_success(inputless_world):
    world = inputless_world[0]
    permission = grant(world, command='includes:inputless:grant')
    with rf.session(world, permission) as reader:
        cut = reader.capture_cut(world[-1])
        result = _verify_origin_includes(reader, root=root(reader, cut), at_cut=cut)
        assert result['rows'] == [] and len(result['root_proof']) == 6
        assert start_event(reader, cut).payload['input_binding_refs'] == []


def test_claim_resource_refs_never_dereference_their_targets(include_reader, monkeypatch):
    from cpn.rpnh.collaboration._product_origin_core import _CoreProof
    from cpn.rpnh.collaboration import _product_origin_includes as relations
    reader, cut = include_reader; requested = root(reader, cut); original = _CoreProof.exact
    def exact(self, ref, expected, **kwargs):
        assert expected != 'resource_version/v1', 'claim reference does not authorize target metadata'
        return original(self, ref, expected, **kwargs)
    monkeypatch.setattr(_CoreProof, 'exact', exact)
    monkeypatch.setattr(relations, '_validated_start_fields', lambda *_: pytest.fail('claims triggered Start'))
    result = _verify_origin_includes(reader, root=requested, at_cut=cut, include=('producer_execution', 'claims'))
    assert len(result['rows']) == 2 and all(row['resource_ref'] is not None for row in result['rows'])


@pytest.mark.parametrize('failure', ['candidate', 'final_delivery'])
def test_returned_safe_error_does_not_retain_private_frames(include_reader, monkeypatch, failure):
    from cpn.rpnh.collaboration import _product_origin_core as core
    from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
    reader, cut = include_reader; requested = root(reader, cut)
    owners_seen = []; init = _OriginQueryContext.__init__
    def initialize(self, *args, **kwargs): init(self, *args, **kwargs); owners_seen.append(self)
    monkeypatch.setattr(_OriginQueryContext, '__init__', initialize)
    if failure == 'candidate':
        change_start(reader, cut, lambda event: replace(event, payload={**event.payload, 'private_sentinel': 'not in returned error'}))
    else:
        serialized = []; original = core.canonical_json; recheck = reader.final_recheck
        def encode(value):
            if isinstance(value, dict) and 'rows' in value: serialized.append(True)
            return original(value)
        def check(sources):
            if serialized: raise RegistryReadSessionError('SESSION_EXPIRED')
            return recheck(sources)
        monkeypatch.setattr(core, 'canonical_json', encode); monkeypatch.setattr(reader, 'final_recheck', check)
    with pytest.raises(RegistryReadSessionError) as caught:
        _verify_origin_includes(reader, root=requested, at_cut=cut)
    error = caught.value  # Retain the actual returned error beyond owner close.
    assert error.__context__ is None and error.__cause__ is None
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if '/collaboration/' in frame.f_code.co_filename:
            assert not {'result', 'verifier', 'payload', 'tokens', 'candidates', 'envelope', 'derived'}.intersection(frame.f_locals)
        traceback = traceback.tb_next
    assert all(owner.snapshot is None and owner._session is None and not owner._cache
        and owner.reservation.scratch_bytes == 0 for owner in owners_seen)
