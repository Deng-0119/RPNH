"""Finite B4 offline normal-root contract tests, with actual Registry receipts."""
from copy import deepcopy, copy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import traceback

import pytest
from jsonschema import ValidationError

from normal_child_root_matrix_fixture import prepare_two_owner_root, register_products
from cpn.rpnh.registry.execution_runtime import ExecutionRuntime
from cpn.rpnh.registry.execution_net import ExecutionNetDefinition, ExecutionTransition, ExecutionInputArc, ExecutionOutputArc, ExecutionNetError
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.schema_catalog import canonical_json, SchemaGovernanceError
from cpn.rpnh.registry.transaction import RegistryTransaction
from cpn.rpnh.registry._registry import _RegistryCore, RegistryReadError
from cpn.rpnh.collaboration.root_terminals import read_root_terminal
from cpn.rpnh.collaboration.worksets import CompleteWorkset, WorksetExpectation


def receipt(name, **data):
    root = os.environ.get('B4_RECEIPTS')
    if root:
        (Path(root) / (name + '.json')).write_text(json.dumps(data, indent=2, default=str) + '\n')


def authority(core):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return {table: [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('registry_meta', 'transactions', 'objects', 'events', 'relations',
                              'firing_publications', 'firing_temporary_members', 'stream_heads', 'snapshots')}


def reject(core, name, action, match=None):
    before = authority(core)
    try:
        action()
    except (RegistryConflict, ResourceIntegrityFault, ExecutionNetError, ObjectIntegrityError, RegistryReadError, SchemaGovernanceError, ValueError, ValidationError) as exc:
        if match:
            assert match in str(exc)
        after = authority(core)
        assert after == before
        receipt(name, status='EXPECTED_REJECTION', exception=type(exc).__name__, message=str(exc),
                frames=traceback.format_tb(exc.__traceback__), authority_before=before, authority_after=after)
        return exc
    raise AssertionError(name + ' unexpectedly accepted')


def definition():
    return ExecutionNetDefinition('b4.finite.child', ('done', 'pending'),
        (ExecutionTransition('finish', 'pure'),), (ExecutionInputArc('pending', 'finish', 1),),
        (ExecutionOutputArc('finish', 'done', 1),), 'pending', 1, ('done',))


def child(f, key, *, settle=True, evidence=None):
    c = f.runtime.instantiate(parent=f.parent, definition=definition(), idempotency_key=key)
    if not settle:
        return c
    c = f.runtime.start(instance_ref=c.instance_ref, parent=f.parent,
        checkpoint_ref=c.checkpoint.checkpoint_ref, transition_id='finish',
        idempotency_key=key + ':start', materialization_key=None)
    return f.runtime.settle(instance_ref=c.instance_ref, parent=f.parent,
        checkpoint_ref=c.checkpoint.checkpoint_ref, firing_ref=c.checkpoint.active_firing_refs[0],
        idempotency_key=key + ':settle', evidence_refs=(f.products.outputs[0].resource_ref.as_version_ref(),) if evidence is None else evidence)


@pytest.fixture
def ready(tmp_path):
    f = prepare_two_owner_root(tmp_path, prefix='b4')
    f.products = register_products(f.target, f.root, 'b4:root')
    f.runtime = ExecutionRuntime(f.target._core)
    f.first = child(f, 'b4:child:one')
    return f


def complete(f, **kwargs):
    args = dict(expected=f.expected, outputs=f.products, output_port=f.products.outputs[0].port_id,
                command_id='b4:root:success')
    args.update(kwargs)
    return f.target_worksets.complete_normal_children(**args)


def test_actual_root_retained_tokens(tmp_path, monkeypatch):
    import normal_child_root_matrix_fixture as helpers
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    original = helpers.new_declared_owner
    def with_retained(directory, **kwargs):
        if kwargs['label'].endswith('-target'):
            module = deepcopy(kwargs['module_document'])
            module['components'].append(helpers.operation_component('passive'))
            module['entry']['retained'] = {'component': 'passive', 'port': 'request'}
            kwargs['module_document'] = module
            kwargs['entry_texts'] = dict(kwargs['entry_texts'], retained='actual retained input')
        return original(directory, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(helpers, 'new_declared_owner', with_retained)
        f = ready.__wrapped__(tmp_path)
    core = f.target._core
    _, _, before = hydrate_module_runtime(core)
    retained = {str(t.token_ref.version_id): core.object_store.read_registered(core.get_version(t.token_ref.version_id))
                for t in before.tokens if t.token_ref not in f.root.operation.firing.claimed_input_refs}
    assert retained, 'fixture must contain actual retained occurrences'
    closed = complete(f)
    _, _, after = hydrate_module_runtime(core)
    after_ids = {str(t.token_ref.version_id) for t in after.tokens}
    assert set(retained) <= after_ids
    for version, payload in retained.items():
        assert core.object_store.read_registered(core.get_version(TypedId.parse(version))) == payload
    completion = core.get_version(TypedId.parse(closed['root']['body']['completion_ref']['version_id']))
    delta = core.get_version(TypedId.parse(completion.metadata['marking_delta_ref']['version_id']))
    assert delta.metadata['ordinary_token_ref_scheme'] == 'normal_root_firing_scoped/v1'
    receipt('retained-normal', closed=closed, retained_refs=list(retained),
            retained_bytes={k: v.decode() for k, v in retained.items()}, delta=delta.metadata,
            target_run_dir=str(core.run_dir))


def test_T01_distinct_actual_run_terminal(ready):
    f = ready
    core = f.target._core
    closed = complete(f)
    assert not core.event_store.object_rows_by_type('run_terminal_evidence/v1')
    root_tx = closed['seal']['success_transaction_id']
    terminal = f.target.terminal()
    rows = core.event_store.object_rows_by_type('run_terminal_evidence/v1')
    assert len(rows) == 1 and rows[0]['transaction_id'] != root_tx
    readonly = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_root_terminal(readonly, closed['root']['record_ref'])['root'] == closed['root']
    receipt('T01', closed=closed,
            terminal_result=terminal, terminal_row=dict(rows[0]), target_run_dir=str(core.run_dir))


def test_N01_unready_alongside_ready(ready):
    f = ready
    unready = child(f, 'b4:child:unready', settle=False)
    assert f.first.checkpoint.map_ready and not unready.checkpoint.map_ready
    reject(f.target._core, 'N01', lambda: complete(f), 'map_ready')


def test_N09_legacy_api_with_real_child(ready):
    f = ready
    reject(f.target._core, 'N09', lambda: f.target.succeed(f.products, command_id='b4:legacy',
           workset_action=CompleteWorkset(f.expected, f.products.outputs[0].port_id)))


@pytest.mark.parametrize('part', ['command', 'head', 'version'])
def test_N13_stale_expectation(ready, part):
    f = ready
    changes = {'command': dict(command_id='b4:wrong'), 'head': dict(stream_head=f.expected.stream_head - 1),
               'version': dict(record_ref=WorksetExpectation.from_record(f.target._core, f.sealed).record_ref)}
    reject(f.target._core, 'N13-' + part, lambda: complete(f, expected=replace(f.expected, **changes[part])))


@pytest.mark.parametrize('part', ['missing', 'logical', 'type', 'foreign'])
def test_N03_actual_evidence_ref_rejections(ready, part):
    f = ready
    c = child(f, 'b4:child:bad-evidence', settle=False)
    c = f.runtime.start(instance_ref=c.instance_ref, parent=f.parent,
        checkpoint_ref=c.checkpoint.checkpoint_ref, transition_id='finish',
        idempotency_key='bad:start', materialization_key=None)
    good = f.products.outputs[0].resource_ref.as_version_ref()
    refs = {'missing': replace(good, version_id=new_id('resource_version')),
            'logical': replace(good, entity_id=new_id('resource')),
            'type': replace(good, entity_type='execution_checkpoint/v1'),
            'foreign': f.contributed.outputs[0].resource_ref.as_version_ref()}
    reject(f.target._core, 'N03-' + part, lambda: f.runtime.settle(instance_ref=c.instance_ref,
        parent=f.parent, checkpoint_ref=c.checkpoint.checkpoint_ref,
        firing_ref=c.checkpoint.active_firing_refs[0], idempotency_key='bad:settle', evidence_refs=(refs[part],)))


class PreparedPause(Exception):
    pass


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    f = ready.__wrapped__(tmp_path_factory.mktemp('prepared'))
    f.second = child(f, 'b4:child:two')
    captured = []
    original = RegistryTransaction.commit
    def pause(tx):
        if tx.idempotency_key == 'b4:root:success':
            captured.append(tx)
            raise PreparedPause()
        return original(tx)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', pause)
        with pytest.raises(PreparedPause):
            complete(f)
    assert len(captured) == 1
    return f, captured[0]


def clone_tx(template):
    tx = copy(template)
    for name in ('_objects', '_events', '_relations', '_firing_publications',
                 '_expected_snapshot_predecessors', '_workspace_head_advances', '_initial_heads'):
        setattr(tx, name, deepcopy(getattr(template, name)))
    return tx


def descriptor_edit(tx, kind, mutate, restores):
    index = next(i for i, obj in enumerate(tx._objects) if obj.object_type == kind)
    obj = tx._objects[index]
    document = deepcopy(dict(obj.metadata))
    mutate(document)
    payload = canonical_json(document)
    path = tx.object_store.path_for_version(obj.version_id)
    restores.setdefault(path, path.read_bytes())
    path.write_bytes(payload)
    tx._objects[index] = replace(obj, metadata=document, size=len(payload))


MALFORMED = [
    'N04-omit', 'N04-duplicate', 'N04-extra',
    'N05-absent', 'N05-event', 'N05-wrong-type', 'N05-unrelated',
    *['N06-' + key for key in ('source_id', 'task_ref', 'run_ref', 'parent_invocation_ref',
       'parent_business_firing_ref', 'parent_business_net_ref', 'parent_business_checkpoint_ref')],
    'N07-old-checkpoint', 'N07-active-checkpoint', 'N07-foreign-checkpoint', 'N07-evidence', 'N07-result', 'N07-successor',
    'N07-mapping-checkpoint', 'N07-mapping-evidence', 'N07-mapping-result', 'N07-mapping-successor',
    'N08-seal-only', 'N08-command', 'N08-transaction', 'S01-profile',
    *['N15-' + kind for kind in ('execution_instance/v1', 'execution_net_definition/v1',
       'execution_checkpoint/v1', 'execution_token/v1', 'execution_transition_firing/v1', 'attach')],
]


@pytest.mark.parametrize('case', MALFORMED)
def test_finite_malformed_real_success(prepared, case):
    f, template = prepared
    tx = clone_tx(template)
    restores = {}
    seal_kind = 'execution_child_seal/v1'
    root_kind = 'collaboration_root_terminal/v2'
    def seal(change):
        descriptor_edit(tx, seal_kind, change, restores)
    try:
        if case.startswith('N04'):
            def alter(data):
                if case.endswith('omit'):
                    data['children'].pop()
                elif case.endswith('duplicate'):
                    data['children'][1] = deepcopy(data['children'][0])
                else:
                    extra = deepcopy(data['children'][0])
                    extra['execution_instance_ref']['version_id'] = str(new_id('execution_instance_version'))
                    data['children'].append(extra)
            seal(alter)
        elif case.startswith('N05'):
            def alter(data):
                ref = data['body']['required_child_seal_ref']['ref']
                if case.endswith('absent'):
                    ref['version_id'] = str(new_id('resource_version'))
                elif case.endswith('event'):
                    ref['version_id'] = str(f.target._core.event_store.list_events_by_type(('execution_instance_attached/v1',))[0].event_id)
                elif case.endswith('wrong-type'):
                    data['body']['required_child_seal_ref']['ref'] = _ref_payload(f.first.instance_ref)
                else:
                    data['body']['required_child_seal_ref']['ref'] = _ref_payload(f.products.outputs[0].resource_ref.as_version_ref())
            descriptor_edit(tx, root_kind, alter, restores)
        elif case.startswith('N06'):
            field = case[4:]
            def alter(data):
                if field == 'source_id':
                    data[field] = 'b4-other-source'
                else:
                    old = TypedId.parse(data[field]['version_id'])
                    data[field]['version_id'] = str(new_id(old.kind))
            seal(alter)
        elif case.startswith('N07'):
            if case.startswith('N07-mapping-'):
                def alter_mapping(data):
                    field = case[len('N07-mapping-'):]
                    if field == 'checkpoint':
                        data['execution_checkpoint_ref'] = f.target._core.get_version(f.first.instance_ref.version_id).metadata['initial_checkpoint_ref']
                    elif field == 'evidence':
                        data['evidence_refs'] = [_ref_payload(f.contributed.outputs[0].resource_ref.as_version_ref())]
                    elif field == 'result':
                        data['operation_result_ref']['version_id'] = str(new_id('operation_result_version'))
                    else:
                        data['successor_business_checkpoint_ref'] = _ref_payload(f.parent.business_checkpoint_ref)
                descriptor_edit(tx, 'execution_terminal_mapping/v1', alter_mapping, restores)
                reject(f.target._core, case, tx.commit)
                return
            def alter(data):
                if case == 'N07-old-checkpoint':
                    instance = f.target._core.get_version(TypedId.parse(data['children'][0]['execution_instance_ref']['version_id'])).metadata
                    data['children'][0]['execution_checkpoint_ref'] = instance['initial_checkpoint_ref']
                elif case == 'N07-active-checkpoint':
                    cp = next(json.loads(row['metadata_json']) for row in f.target._core.event_store.object_rows_by_type('execution_checkpoint/v1')
                        if json.loads(row['metadata_json'])['execution_instance_ref'] == data['children'][0]['execution_instance_ref']
                        and json.loads(row['metadata_json'])['sequence'] == 1)
                    assert cp['status'] == 'running' and cp['active_firing_refs']
                    data['children'][0]['execution_checkpoint_ref'] = cp['execution_checkpoint_ref']
                elif case == 'N07-foreign-checkpoint':
                    data['children'][0]['execution_checkpoint_ref'] = data['children'][1]['execution_checkpoint_ref']
                elif case == 'N07-evidence':
                    data['children'][0]['evidence_refs'] = [_ref_payload(f.contributed.outputs[0].resource_ref.as_version_ref())]
                elif case == 'N07-result':
                    data['operation_result_ref']['version_id'] = str(new_id('operation_result_version'))
                else:
                    data['successor_business_checkpoint_ref'] = _ref_payload(f.parent.business_checkpoint_ref)
            seal(alter)
        elif case == 'N08-seal-only':
            tx = f.target._core.begin(idempotency_key='b4:standalone')
            tx._objects = [obj for obj in template._objects if obj.object_type == seal_kind]
        elif case == 'N08-command':
            seal(lambda data: data.update(success_command_id='b4:foreign-command'))
        elif case == 'N08-transaction':
            seal(lambda data: data.update(success_transaction_id=str(new_id('transaction'))))
        elif case == 'S01-profile':
            seal(lambda data: data.update(closure_profile='execution-v1-unknown'))
        elif case == 'N15-attach':
            from cpn.rpnh.registry.models import PendingEvent
            event = f.target._core.event_store.list_events_by_type(('execution_instance_attached/v1',))[0]
            tx._events.append(PendingEvent(event_type=event.event_type, criticality=event.criticality,
                stream_id=event.stream_id, aggregate_id=event.aggregate_id, aggregate_type=event.aggregate_type,
                idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key, payload=event.payload,
                payload_schema_ref=event.payload_schema_ref, producer_invocation_id=event.producer_invocation_id))
        else:
            kind = case[4:]
            row = f.target._core.event_store.object_rows_by_type(kind)[0]
            tx._objects.append(f.target._core.get_version(TypedId.parse(row['version_id'])))
        reject(f.target._core, case.replace('/', '_'), tx.commit)
    finally:
        for path, payload in restores.items():
            path.write_bytes(payload)


def test_R01_actual_attach_before_prepared_success_commit(ready, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    f = ready
    original = RegistryTransaction.commit
    captured = []
    threads = {'proposer': threading.get_ident()}
    def race(tx):
        if tx.idempotency_key == 'b4:root:success':
            def competitor():
                threads['attacher'] = threading.get_ident()
                return child(f, 'b4:child:race')
            with ThreadPoolExecutor(max_workers=1) as pool:
                second = pool.submit(competitor).result(timeout=60)
            assert threads['attacher'] != threads['proposer']
            assert second.checkpoint.map_ready
            captured.append(_ref_payload(second.instance_ref))
            return reject(f.target._core, 'R01-old-proposal', lambda: original(tx))
        return original(tx)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', race)
        # The wrapper returns after proving rejection; facade cannot read an uncommitted root.
        with pytest.raises((RegistryConflict, ResourceIntegrityFault, KeyError)):
            complete(f)
    assert len(captured) == 1
    fresh = complete(f, command_id='b4:root:fresh')
    children = fresh['seal']['children']
    assert len(children) == 2 and captured[0] in [c['execution_instance_ref'] for c in children]
    receipt('R01-fresh', closed=fresh, attached=captured[0], threads=threads,
            interleaving='prepare old Success; independent thread commits attach+settle; old commit rejected; fresh Success includes both')


@pytest.mark.parametrize('part', ['expectation', 'firing', 'output', 'mixed', 'outcome'])
def test_N14_changed_actual_replay_bundle(ready, part):
    f = ready
    closed = complete(f)
    changes = {}
    if part == 'expectation':
        changes['expected'] = replace(f.expected, command_id='b4:changed')
    elif part == 'firing':
        changes['outputs'] = f.contributed
    elif part == 'output':
        changes['output_port'] = 'other.result'
    elif part == 'mixed':
        changes['outputs'] = replace(f.products, outputs=f.products.outputs + f.contributed.outputs)
    else:
        changes['outputs'] = replace(f.products, selected_outcome_id='unknown')
    reject(f.target._core, 'N14-' + part, lambda: complete(f, **changes))
    assert complete(f) == closed


@pytest.mark.parametrize('part', ['checkpoint-status', 'firing-outcome', 'typed-call'])
def test_S01_unsupported_execution_contract(prepared, part):
    f, _ = prepared
    core = f.target._core
    if part == 'typed-call':
        action = lambda: ExecutionTransition('typed', 'typed_call')
    else:
        kind = 'execution_checkpoint/v1' if part == 'checkpoint-status' else 'execution_transition_firing/v1'
        row = core.event_store.object_rows_by_type(kind)[-1]
        document = json.loads(row['metadata_json'])
        document['status'] = 'failed' if part == 'checkpoint-status' else 'aborted'
        action = lambda: core.catalog.validate_instance(kind, category='object', instance=document)
    reject(core, 'S01-' + part, action)


def test_R01_child_stream_compare_and_swap(prepared):
    f, template = prepared
    tx = clone_tx(template)
    seal = next(o.metadata for o in tx._objects if o.object_type == 'execution_child_seal/v1')
    assert seal['pre_seal_stream_head'] == 2
    tx._initial_heads[seal['child_stream_id']] = 1
    error = reject(f.target._core, 'R01-CAS', tx.commit)
    assert str(error) == f"compare-and-append conflict for {seal['child_stream_id']!r}: expected=1, actual=2"


def test_R02_success_wins_over_prepared_attach(ready, monkeypatch):
    """One actual attach prepares first, but the original root commits first."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    f = ready
    core = f.target._core
    original = RegistryTransaction.commit
    prepared_event, release_event = threading.Event(), threading.Event()
    caller_key = 'b4:child:success-wins'
    observed = {'root_thread': threading.get_ident()}

    def pause_attach(tx):
        if tx.event_store is core.event_store and tx.idempotency_key.endswith(':instantiate:' + caller_key):
            attaches = [event for event in tx._events if event.event_type == 'execution_instance_attached/v1']
            assert len(attaches) == 1
            event = attaches[0]
            assert event.payload['parent_business_firing_ref'] == _ref_payload(f.parent.business_firing_ref)
            observed.update(attach_thread=threading.get_ident(), attach_command=tx.idempotency_key,
                attach_transaction=str(tx.transaction_id), attach_payload=dict(event.payload),
                stream_id=event.stream_id, prepared_stream_head=tx._initial_stream_head(event.stream_id),
                prepared_objects=[{'entity_type': obj.object_type, 'logical_id': str(obj.logical_id),
                                   'version_id': str(obj.version_id)} for obj in tx._objects])
            assert observed['attach_thread'] != observed['root_thread']
            assert observed['prepared_stream_head'] == 1
            prepared_event.set()
            assert release_event.wait(timeout=60), 'root thread did not release prepared attach'
            error = reject(core, 'R02-prepared-attach-refusal', lambda: original(tx))
            assert type(error) is RegistryConflict
            expected_cas = f"compare-and-append conflict for {event.stream_id!r}: expected=1, actual=2"
            assert str(error) in {expected_cas, 'producer-attributed Registry batch targets a closed firing'}
            observed['refusal'] = str(error)
            raise error
        return original(tx)

    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', pause_attach)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(f.runtime.instantiate, parent=f.parent, definition=definition(),
                                 idempotency_key=caller_key)
            try:
                assert prepared_event.wait(timeout=30), 'actual attach did not reach its commit boundary'
                assert not future.done()
                # Prewritten attach objects are not committed children and must not enter this seal.
                before_success = authority(core)
                pending_version = observed['attach_payload']['execution_instance_ref']['version_id']
                assert all(row['version_id'] != pending_version for row in before_success['objects'])
                closed = complete(f)
                assert len(closed['seal']['children']) == 1
                assert closed['seal']['children'][0]['execution_instance_ref'] == _ref_payload(f.first.instance_ref)
                assert closed['workset']['body']['state'] == 'completed'
                after_success = authority(core)
                assert before_success != after_success
                assert core.event_store.stream_heads()[observed['stream_id']] == 2
            finally:
                release_event.set()
            with pytest.raises(RegistryConflict):
                future.result(timeout=30)
    after_refusal = authority(core)
    assert after_refusal == after_success
    readonly = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    verified = read_root_terminal(readonly, closed['root']['record_ref'])
    assert verified == closed
    assert authority(core) == after_refusal
    receipt('R02-success-wins', observed=observed, closed=closed,
            authority_before_success=before_success, authority_after_success=after_success,
            authority_after_refusal=after_refusal, verified=verified,
            test_source=str(Path(__file__).resolve()), target_run_dir=str(core.run_dir),
            interleaving='attach staged and paused; original root Success commits; earlier attach released and refused',
            arbitrary_scheduling_guarantee=False, prewritten_file_rollback_claimed=False)
