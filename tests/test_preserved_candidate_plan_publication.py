"""Real v2 inert publication and fixed direct-Core first/replay boundaries."""
import pytest
from copy import deepcopy
import json

from cpn.rpnh.collaboration.preserved_candidate_publisher import PreservedCandidatePlanPublisher
from cpn.rpnh.collaboration import preserved_candidate_publisher as publisher_module
from cpn.rpnh.net_operations import apply_replacement, prepare_replacement
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, PLAN_V2_SCHEMA
from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
from cpn.rpnh.registry.runtime_binding_contracts import freeze_candidate_document
from test_preserved_candidate_plan_reads import preserved_plan_fixture, publish_fixture_plan
from test_preserved_slot_projection import _slot_module
from test_candidate_plan_persistence import plan_fixture, authored, candidate_request


def publisher_request(fixture):
    owner, material, plan, basis = fixture
    publisher = PreservedCandidatePlanPublisher(owner.schema_gateway, owner.registration, material.revision.producer_principal_ref)
    selected = {slot.schema for slot in material.compiled.symbolic.logical_slots if slot.name in plan['preserved_slot_refs']}
    return publisher, dict(author_ref=material.revision.revision_ref, identity=owner.identity,
        task_round_ref=owner.task_round_ref, authority_decision_ref=_version_from_payload(plan['authority_decision_ref']),
        command_id=plan['command_id'], command_context=plan['command_context'], preserved_basis=basis,
        selected_slot_refs={name: _version_from_payload(ref) for name, ref in plan['preserved_slot_refs'].items()},
        selected_schema_refs={name: _resource_from_payload(plan['schema_refs'][name]) for name in selected},
        owner_resource_inputs={name: _resource_from_payload(ref) for name, ref in plan['owner_resource_inputs'].items()})


def advance_owner(fixture, command='fixture:after-plan'):
    owner, _, plan, _ = fixture
    outcome = apply_replacement(owner, prepare_replacement(owner, _slot_module('AfterPlan', bool(plan['owner_resource_inputs']))),
        command_id=command)
    assert outcome['status'] == 'ADOPTED'


def test_real_v2_producer_persists_only_plan_and_replays_fixed_history(preserved_plan_fixture, monkeypatch):
    owner, _, plan, basis = preserved_plan_fixture
    publisher, request = publisher_request(preserved_plan_fixture)
    before = len(owner._core.event_store.object_rows()), len(owner._core.event_store.list_events_by_type(('net_adopted/v1',)))
    actual = publisher.publish(**request)
    assert actual.plan == plan and actual.preserved_basis == basis
    assert len(owner._core.event_store.object_rows()) == before[0] + 1
    assert len(owner._core.event_store.list_events_by_type(('net_adopted/v1',))) == before[1]
    for kind in ('runtime_binding_manifest/v2', 'binding_readiness/v2', 'llm_invocation_attempt/v1', 'operation_execution_lease/v1'):
        assert owner._core.event_store.object_rows_by_type(kind) == ()
    advance_owner(preserved_plan_fixture)
    def forbidden(*args, **kwargs):
        raise AssertionError('successful replay must not compile HOST again')
    monkeypatch.setattr(publisher_module, 'validate_closed_revision', forbidden)
    assert publisher.publish(**request) == actual


def test_direct_core_exact_replay_and_metadata_type_difference(preserved_plan_fixture):
    owner, _, plan, _ = preserved_plan_fixture
    ref = publish_fixture_plan(owner._core, plan)
    advance_owner(preserved_plan_fixture)
    assert publish_fixture_plan(owner._core, plan) == ref
    changed = dict(plan, command_context={'extra': 1})
    from cpn.rpnh.collaboration.candidate_plans import _command_key
    with pytest.raises(RegistryConflict, match='prospective bytes/metadata'):
        owner._core.publish_bytes(object_type=PLAN_V2_TYPE, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=freeze_candidate_document(plan).encode(), metadata=changed, media_type='application/json',
            schema_ref=PLAN_V2_SCHEMA, idempotency_key=_command_key(owner._core.task_id, plan['source_id'], plan['command_id']) + ':plan')


def test_direct_core_first_stale_basis_rejects_before_publication(preserved_plan_fixture):
    owner, _, plan, _ = preserved_plan_fixture
    advance_owner(preserved_plan_fixture)
    before = len(owner._core.event_store.object_rows()), len(owner._core.event_store.list_events())
    with pytest.raises(RegistryConflict, match='stale at first'):
        publish_fixture_plan(owner._core, plan)
    assert (len(owner._core.event_store.object_rows()), len(owner._core.event_store.list_events())) == before


def test_explicit_entry_replays_existing_v1_in_its_original_format(plan_fixture, monkeypatch):
    f = plan_fixture
    request = candidate_request(f, authored(f), command_context={'original': True})
    original = f[-1].publish(**request)
    publisher = PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer)
    def forbidden(*args, **kwargs):
        raise AssertionError('stored v1 format must not recompile or upgrade')
    monkeypatch.setattr(publisher_module, 'validate_closed_revision', forbidden)
    assert publisher.publish(**request) == original
    with pytest.raises(RegistryConflict, match='complete frozen request'):
        publisher.publish(**dict(request, command_context={'original': 1}))
    assert len(f[0].event_store.object_rows_by_type('collaboration_candidate_plan/v1')) == 1
    assert f[0].event_store.object_rows_by_type(PLAN_V2_TYPE) == ()


def stage(tx, document, *, kind=PLAN_V2_TYPE):
    ref = _version_from_payload(document['plan_ref'])
    return tx.prewrite(object_type=kind, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=freeze_candidate_document(document).encode(), metadata=document, media_type='application/json',
        schema_ref='registry_v1/' + kind)


def stage_principal(tx):
    from cpn.rpnh.registry.identities import new_id
    logical, version = new_id('principal'), new_id('principal_version')
    data = {'principal_id': str(logical), 'principal_version_id': str(version), 'display_name': 'extra fixture principal'}
    tx.prewrite(object_type='principal/v1', logical_id=logical, version_id=version,
        payload=freeze_candidate_document(data).encode(), metadata=data, media_type='application/json', schema_ref='registry_v1/principal/v1')


@pytest.mark.parametrize('mixed', [False, True])
def test_ordinary_generic_v1_keeps_isolated_and_mixed_contract(plan_fixture, mixed):
    from cpn.rpnh.registry.identities import new_id
    f = plan_fixture
    document = f[-1].publish(**candidate_request(f, authored(f))).plan
    document['plan_ref'].update(logical_id=str(new_id('resource')), version_id=str(new_id('resource_version')))
    tx = f[0].begin(idempotency_key='fixture:ordinary-v1')
    stage(tx, document, kind='collaboration_candidate_plan/v1')
    if mixed:
        stage_principal(tx)
    tx.commit()
    assert len(f[0].event_store.object_rows_by_type('collaboration_candidate_plan/v1')) == 2


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('axis', ['ordinary', 'empty', 'mixed'])
def test_v2_and_reserved_command_cannot_escape_the_fixed_gate(preserved_plan_fixture, axis):
    from cpn.rpnh.collaboration.candidate_plans import _command_key
    owner, _, plan, _ = preserved_plan_fixture
    core = owner._core
    key = _command_key(core.task_id, plan['source_id'], plan['command_id']) + ':plan'
    tx = core.begin(idempotency_key='fixture:ordinary-v2' if axis == 'ordinary' else key)
    if axis != 'empty':
        stage(tx, plan)
    if axis == 'mixed':
        stage_principal(tx)
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    with pytest.raises(RegistryConflict):
        tx.commit()
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('axis', ['context', 'entry', 'owner_marking', 'owner_resource', 'author',
    'round', 'authority', 'identity', 'slots', 'schemas', 'extra_schema', 'basis'])
def test_complete_request_replay_rejects_every_explicit_input_axis(preserved_plan_fixture, axis):
    from dataclasses import replace
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.collaboration import SourceQualifiedVersionRef
    from test_preserved_basis_reads import basis_for
    owner, _, _, _ = preserved_plan_fixture
    publisher, request = publisher_request(preserved_plan_fixture)
    original = publisher.publish(**request)
    changed = deepcopy(request)
    if axis == 'context':
        changed['command_context']['extra'] = 1
    elif axis == 'entry':
        changed['entry_inputs'] = {'request': []}
    elif axis == 'owner_marking':
        changed['owner_input_resources'] = [owner.original_input_ref]
    elif axis == 'owner_resource':
        changed['owner_resource_inputs'] = {'unselected': owner.original_input_ref}
    elif axis == 'author':
        changed['author_ref'] = SourceQualifiedVersionRef('slot-source', VersionRef('collaboration_net_revision/v1',
            new_id('resource'), new_id('resource_version')))
    elif axis in ('round', 'authority'):
        kind = 'task_round' if axis == 'round' else 'user_authority_decision'
        field = 'task_round_ref' if axis == 'round' else 'authority_decision_ref'
        changed[field] = VersionRef(kind + '/v1', new_id(kind), new_id(kind + '_version'))
    elif axis == 'identity':
        changed['identity'] = replace(owner.identity, branch_id='another-branch')
    elif axis == 'slots':
        name = next(iter(changed['selected_slot_refs']))
        changed['selected_slot_refs'][name] = VersionRef('logical_artifact_slot/v1', new_id('logical_slot'), new_id('logical_slot_version'))
    elif axis in ('schemas', 'extra_schema'):
        name = next(iter(changed['selected_schema_refs'])) if axis == 'schemas' else 'application/extra/v1'
        changed['selected_schema_refs'][name] = ResourceVersionRef(new_id('resource'), new_id('resource_version'))
    else:
        advance_owner(preserved_plan_fixture)
        changed['preserved_basis'] = basis_for(owner._core.event_store.list_events_by_type(('net_adopted/v1',))[-1])
    with pytest.raises(RegistryConflict, match='complete frozen request'):
        publisher.publish(**changed)
    assert publisher.publish(**request) == original
    assert len(owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE)) == 1


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_request_is_detached_before_actual_host_compile(preserved_plan_fixture, monkeypatch):
    publisher, request = publisher_request(preserved_plan_fixture)
    owner, material, _, _ = preserved_plan_fixture
    key = material.compiled.source.components[0].key
    original, expected = owner.registration.resolve('component', key), deepcopy(request)
    calls = []
    def mutate_then_compile(*args, **kwargs):
        calls.append(key)
        request['command_context']['extra'] = False
        request['selected_slot_refs'].clear()
        request['selected_schema_refs'].clear()
        return original(*args, **kwargs)
    monkeypatch.setitem(owner.registration._callables['component'], key, mutate_then_compile)
    result = publisher.publish(**request)
    assert calls
    assert result.plan['command_context'] == expected['command_context']
    assert result.plan['preserved_slot_refs']


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('cut', ['after_prewrite', 'after_commit', 'prewrite_stale', 'prewrite_changed'])
def test_exact_command_recovers_interruption_without_promoting_orphan_to_success(preserved_plan_fixture, monkeypatch, cut):
    from cpn.rpnh.registry.transaction import RegistryTransaction
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    owner, material, plan, _ = preserved_plan_fixture
    publisher, request = publisher_request(preserved_plan_fixture)
    original = RegistryTransaction.commit
    def crash(tx):
        if any(item.object_type == PLAN_V2_TYPE for item in tx._objects):
            if cut == 'after_commit':
                original(tx)
            raise RuntimeError('simulated v2 interruption')
        return original(tx)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', crash)
        with pytest.raises(RuntimeError, match='v2 interruption'):
            publisher.publish(**request)
    ref = _version_from_payload(plan['plan_ref'])
    path = owner._core.object_store.path_for_version(ref.version_id)
    orphan_bytes = path.read_bytes()
    assert len(owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE)) == (cut == 'after_commit')
    if cut == 'prewrite_stale':
        advance_owner(preserved_plan_fixture)
    reopened = _RegistryCore(owner._core.run_dir, create=False, catalog=owner._core.catalog)
    gateway = RegistryRegistrationGateway(reopened, owner.identity.task_ref, owner.bootstrap_ref)
    retry = PreservedCandidatePlanPublisher(gateway, owner.registration, material.revision.producer_principal_ref)
    if cut == 'prewrite_stale':
        with pytest.raises(RegistryConflict, match='stale at first'):
            retry.publish(**request)
        assert reopened.event_store.object_rows_by_type(PLAN_V2_TYPE) == ()
    elif cut == 'prewrite_changed':
        with pytest.raises(RegistryConflict, match='immutable complete plan'):
            retry.publish(**dict(request, command_context={'extra': 1}))
        assert reopened.event_store.object_rows_by_type(PLAN_V2_TYPE) == ()
        assert retry.publish(**request).plan_ref == ref
    else:
        assert retry.publish(**request).plan_ref == ref
    assert path.read_bytes() == orphan_bytes


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_two_complete_commands_race_to_one_exact_winner(preserved_plan_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    owner, _, _, _ = preserved_plan_fixture
    publisher, request = publisher_request(preserved_plan_fixture)
    barrier = Barrier(2)
    def submit(value):
        barrier.wait(timeout=20)
        try:
            return publisher.publish(**dict(request, command_context={'extra': value}))
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, (True, 1)))
    assert sum(isinstance(value, RegistryConflict) for value in outcomes) == 1
    winner = next(value for value in outcomes if not isinstance(value, Exception))
    assert publisher.publish(**dict(request, command_context=winner.plan['command_context'])) == winner
    assert len(owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE)) == 1


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_real_head_advance_between_collection_and_commit_is_stale(preserved_plan_fixture, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    owner, _, _, _ = preserved_plan_fixture
    publisher, request = publisher_request(preserved_plan_fixture)
    ready, advanced = Event(), Event()
    original = owner._core.publish_bytes
    def paused(**kwargs):
        if kwargs['object_type'] == PLAN_V2_TYPE:
            ready.set()
            assert advanced.wait(timeout=30)
        return original(**kwargs)
    monkeypatch.setattr(owner._core, 'publish_bytes', paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(publisher.publish, **request)
        assert ready.wait(timeout=30)
        try:
            advance_owner(preserved_plan_fixture)
        finally:
            advanced.set()
        with pytest.raises(RegistryConflict, match='stale at first'):
            result.result(timeout=30)
    assert owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE) == ()


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_gate_recloses_in_the_existing_cut_without_host_or_another_connection(preserved_plan_fixture, monkeypatch):
    from cpn.rpnh.registry._event_store import preserved_plan_commit as gate
    from cpn.rpnh.registration import Registration
    owner, _, plan, _ = preserved_plan_fixture
    actual, original = [], gate.validate_preserved_plan_commit
    def forbidden(*args, **kwargs):
        raise AssertionError('fixed plan gate cannot open another connection or resolve HOST')
    def checked(store, db, **kwargs):
        assert db.in_transaction
        with monkeypatch.context() as patch:
            patch.setattr(store, 'connect', forbidden)
            patch.setattr(Registration, 'resolve', forbidden)
            patch.setattr(Registration, 'declaration', forbidden)
            result = original(store, db, **kwargs)
        actual.append(kwargs['existing'] is not None)
        return result
    monkeypatch.setattr(gate, 'validate_preserved_plan_commit', checked)
    publish_fixture_plan(owner._core, plan)
    publish_fixture_plan(owner._core, plan)
    assert actual == [False, True]


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_raw_replay_command_comparison_distinguishes_boolean_and_number(preserved_plan_fixture):
    owner, _, plan, _ = preserved_plan_fixture
    ref = publish_fixture_plan(owner._core, plan)
    with owner._core.event_store.connect() as db:
        transaction = db.execute('SELECT transaction_id FROM objects WHERE version_id=?', (str(ref.version_id),)).fetchone()[0]
        saved = db.execute('SELECT command_json FROM transactions WHERE transaction_id=?', (transaction,)).fetchone()[0]
        command = json.loads(saved)
        command['objects'][0]['metadata']['command_context']['extra'] = 1
        assert command == json.loads(saved)  # The old generic Python equality conflates this.
        db.execute('UPDATE transactions SET command_json=? WHERE transaction_id=?', (json.dumps(command), transaction))
    with pytest.raises(RegistryConflict, match='complete exact command'):
        publish_fixture_plan(owner._core, plan)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_unknown_candidate_family_is_not_generic_legacy(preserved_plan_fixture):
    from dataclasses import replace
    from cpn.rpnh.registry.identities import new_id
    owner, _, _, _ = preserved_plan_fixture
    core = owner._core
    kind, schema = 'collaboration_candidate_plan/v99', 'registry_v1/collaboration_candidate_plan/v99'
    core.catalog.register_schema(schema, {'$schema': 'http://json-schema.org/draft-07/schema#', '$id': schema, 'type': 'object'})
    core.catalog.register_type(replace(core.catalog.require(PLAN_V2_TYPE, category='object'), name=kind, schema_ref=schema))
    with pytest.raises(RegistryConflict, match='unsupported candidate plan record version'):
        core.publish_bytes(object_type=kind, logical_id=new_id('resource'), version_id=new_id('resource_version'),
            payload=b'{}', metadata={}, media_type='application/json', schema_ref=schema, idempotency_key='fixture:future-generic')
    assert core.event_store.object_rows_by_type(kind) == ()


def test_empty_v2_and_original_v1_share_one_command_namespace(tmp_path, monkeypatch):
    import cpn.rpnh.collaboration as collaboration
    with monkeypatch.context() as patch:
        patch.setattr(collaboration, 'candidate_schema_data', collaboration.candidate_v2_schema_data)
        f = plan_fixture.__wrapped__(tmp_path)
    request = candidate_request(f, authored(f))
    publisher = PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer)
    result = publisher.publish(**request)
    assert result.plan_ref.entity_type == PLAN_V2_TYPE and result.preserved_basis is None
    assert result.plan['preserved_slot_refs'] == {}
    assert publisher.publish(**request) == result
    with pytest.raises(RegistryConflict, match='immutable complete plan'):
        f[-1].publish(**request)
    assert f[0].event_store.object_rows_by_type('collaboration_candidate_plan/v1') == ()
    assert len(f[0].event_store.object_rows_by_type(PLAN_V2_TYPE)) == 1


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_raw_first_rejects_wrong_command_object_occupancy(preserved_plan_fixture):
    from test_preserved_candidate_plan_reads import inject_historical_plan_specimen
    owner, _, plan, _ = preserved_plan_fixture
    core = owner._core
    inject_historical_plan_specimen(core, plan, carrier_key='fixture:historical-wrong-command')
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    with pytest.raises(RegistryConflict, match='incomplete or conflicting plan occupancy'):
        publish_fixture_plan(core, plan)
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


def test_existing_generic_workspace_head_command_replays_and_stale_still_rejects(tmp_path, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.registry.transaction import RegistryTransaction
    from test_execution_net_registry import test_generic_workspace_head_advance_is_atomic_against_a_stale_head
    original, replayed = RegistryTransaction.commit, []
    def commit_and_replay(tx):
        result = original(tx)
        if tx.idempotency_key == 'workspace-cas:success':
            replay = RegistryTransaction(event_store=tx.event_store, object_store=tx.object_store,
                task_id=tx.task_id, branch_id=tx.branch_id, task_round_id=tx.task_round_id,
                net_instance_id=tx.net_instance_id, idempotency_key=tx.idempotency_key, writer_epoch=tx.writer_epoch)
            replay._objects = list(tx._objects)
            replay._relations = list(tx._relations)
            replay._workspace_head_advances = deepcopy(tx._workspace_head_advances)
            before = len(tx.event_store.list_events())
            recovered = original(replay)
            # Ordinal is assigned only by persisted reading. All actual event
            # identities/envelopes and the Registry event count remain exact.
            assert all(type(event.ordinal) is int for event in recovered)
            assert tuple(replace(event, ordinal=None) for event in recovered) == result
            assert len(tx.event_store.list_events()) == before
            replayed.append(tx.idempotency_key)
        return result
    monkeypatch.setattr(RegistryTransaction, 'commit', commit_and_replay)
    test_generic_workspace_head_advance_is_atomic_against_a_stale_head(tmp_path, monkeypatch)
    assert replayed == ['workspace-cas:success']


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_known_graph_source_is_rejected_by_producer_and_direct_gate(preserved_plan_fixture, version):
    import uuid
    from cpn.rpnh.collaboration import ClosedModuleAuthor
    from cpn.rpnh.collaboration.materials import _elements
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.collaboration.candidate_plans import _operation_refs
    owner, material, plan, _ = preserved_plan_fixture
    key = f'rpnh/agent-workflow-graph/v{version}'
    owner.registration.register_component(key, owner.registration.resolve('component', 'slot_operation'),
        identity={'implementation_id': 'test.explicit_graph_gate', 'revision': str(version)},
        contracts=owner.registration.declaration('component', 'slot_operation')['contracts'])
    body = material.compiled.source.to_dict()
    body['components'][0]['key'] = key
    module = ModuleDeclaration.from_dict(body)
    author = ClosedModuleAuthor(owner.schema_gateway, owner.registration, material.revision.producer_principal_ref)
    graph = author.publish(module=module, element_ids={locator: 'element:' + uuid.uuid4().hex for locator in _elements(module)},
        command_id='fixture:graph-author')
    publisher, request = publisher_request(preserved_plan_fixture)
    request['author_ref'] = graph.revision.revision_ref
    with pytest.raises(RegistryConflict, match='graph-authoritative'):
        publisher.publish(**request)
    plan['author_ref'] = graph.revision.revision_ref.to_dict()
    plan['compiled'], plan['host_requirements'] = graph.compiled.to_dict(), graph.host_requirements
    plan['operation_refs'] = _operation_refs(graph.compiled, plan['operation_command_key'])
    with pytest.raises(RegistryConflict, match='graph-authoritative'):
        publish_fixture_plan(owner._core, plan)
    assert owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE) == ()
