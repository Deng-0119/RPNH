"""Real two-thread plan contention with deliberately ordered storage boundaries."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from threading import Barrier, Event, Lock, local

import pytest

from cpn.rpnh.collaboration import AssemblyAuthorV2, validate_assembly_revision
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_collaboration_assembly_v2_publication import assert_no_run, fixture, request
from test_collaboration_assembly_v2_recovery import _facts, _payload, _stages, _version
from test_collaboration_assembly_v2_recovery_lowlevel import _blobs
from test_collaboration_graph_materials import registration


CONFIGURATIONS = (('shared', False), ('shared', True),
                  ('independent_gateways', False), ('independent_gateways', True))


def _checked_reader(core, value):
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, registration())
    assert checked.revision == value.revision and checked.plan == value.plan
    assert checked.lowering_map == value.lowering_map
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert _facts(core) == before
    assert_no_run(reader)
    return checked


@pytest.mark.parametrize('configuration,different', CONFIGURATIONS,
    ids=('shared_same', 'shared_different_blob', 'gateways_same', 'gateways_different_committed'))
def test_real_ordered_threads_keep_one_original_command_result(fixture, monkeypatch, configuration, different):
    core, gateway, first, _, member = fixture
    second = first
    if configuration == 'independent_gateways':
        second_gateway = RegistryRegistrationGateway(core, gateway._task_ref, gateway._bootstrap_ref)
        second = AssemblyAuthorV2(second_gateway, registration(), first.producer)
        assert second is not first and second.gateway is not gateway
        assert second.core is first.core is core
    actors = {'A': first, 'B': second}
    base = request(member, command_id='assembly:concurrent-target')
    other = {**base, 'members': (replace(base['members'][0], display_name='Other legal label'), base['members'][1])} if different else dict(base)
    requests = {'A': base, 'B': other}
    # Both actual configured callers/inputs have successful fresh-command
    # controls before any target-plan gate is installed.
    control_refs = {}
    for actor in ('A', 'B'):
        value = actors[actor].publish(**{**requests[actor], 'command_id': 'assembly:concurrency-control:' + actor})
        _checked_reader(core, value)
        control_refs[actor] = value.revision.revision_ref.to_dict()
    before = _facts(core)
    before_ids = {row['version_id'] for row in core.event_store.object_rows()}
    stages = _stages(first, base['command_id'])
    plan_key, plan_ref = stages[0][1], stages[0][2]
    plan_version = _version(plan_ref).version_id
    target_ref = stages[-1][2]
    thread = local()
    trace, payloads, outcomes = [], {}, {}
    trace_lock = Lock()
    writes_ready, commits_ready = Barrier(2, timeout=20), Barrier(2, timeout=20)
    a_blob_written, a_plan_committed, b_public_done = Event(), Event(), Event()
    original_payload = core.object_store._publish_payload
    original_commit = RegistryTransaction.commit

    def note(event, **fields):
        with trace_lock:
            trace.append({'actor': thread.actor, 'event': event, **fields})

    def wait(event, boundary):
        assert event.wait(20), 'timed out at ' + boundary

    def actual_payload(payload, version_id):
        if version_id != plan_version:
            return original_payload(payload, version_id)
        actor = thread.actor
        assert actor in actors
        note('plan_write_attempt_ready', bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        with trace_lock:
            payloads[actor] = payload
        writes_ready.wait()
        if different and actor == 'B':
            if configuration == 'shared':
                wait(a_blob_written, 'A real blob before commit')
                assert core.event_store.object_row(plan_version) is None
                note('B_attempts_against_blob_without_canonical_plan')
            else:
                wait(a_plan_committed, 'A real plan commit')
                assert core.event_store.object_row(plan_version) is not None
                note('B_attempts_against_committed_plan')
        try:
            result = original_payload(payload, version_id)
        except ObjectIntegrityError:
            note('real_immutable_payload_collision', canonical_plan_present=core.event_store.object_row(plan_version) is not None)
            raise
        assert _payload(core, plan_ref) == payload
        note('original_payload_write_returned')
        if actor == 'A':
            a_blob_written.set()
        return result

    def actual_commit(transaction):
        if transaction.event_store is not core.event_store or transaction.idempotency_key != plan_key:
            return original_commit(transaction)
        actor = thread.actor
        assert actor in actors and len(transaction._objects) == 1
        assert transaction._objects[0].version_id == plan_version
        note('original_plan_commit_ready')
        if not different:
            # Both real prewrites have returned and both PreparedObjects are
            # attached before either original commit is allowed to run.
            commits_ready.wait()
            if actor == 'B':
                wait(a_plan_committed, 'A ordered same-input commit')
        elif configuration == 'shared':
            assert actor == 'A'
            wait(b_public_done, 'B actual public immutable conflict before A commit')
            with trace_lock:
                assert any(row['actor'] == 'B' and row['event'] == 'public_registry_conflict' for row in trace)
            assert core.event_store.object_row(plan_version) is None
        else:
            assert actor == 'A'
        result = original_commit(transaction)
        assert transaction._closed
        row = core.event_store.object_row(plan_version)
        assert row is not None and row['transaction_id'] == str(transaction.transaction_id)
        with core.event_store.connect() as db:
            assert db.execute('SELECT status FROM transactions WHERE transaction_id=?',
                (str(transaction.transaction_id),)).fetchone()[0] == 'committed'
        note('original_plan_commit_returned', transaction_id=str(transaction.transaction_id))
        if actor == 'A':
            a_plan_committed.set()
        return result

    def run(actor):
        thread.actor = actor
        try:
            value = actors[actor].publish(**requests[actor])
            note('public_success', reference=value.revision.revision_ref.to_dict())
            return value
        except RegistryConflict as error:
            note('public_registry_conflict', message=str(error))
            return error
        finally:
            if actor == 'B':
                b_public_done.set()

    with monkeypatch.context() as patch:
        patch.setattr(core.object_store, '_publish_payload', actual_payload)
        patch.setattr(RegistryTransaction, 'commit', actual_commit)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {actor: pool.submit(run, actor) for actor in ('A', 'B')}
            for actor in ('A', 'B'):
                outcomes[actor] = futures[actor].result(timeout=60)
    assert not isinstance(outcomes['A'], Exception)
    winner = outcomes['A']
    assert winner.revision.revision_ref == target_ref
    assert payloads.keys() == {'A', 'B'}
    assert _payload(core, plan_ref) == payloads['A']
    if different:
        assert isinstance(outcomes['B'], RegistryConflict)
        assert payloads['A'] != payloads['B']
    else:
        assert not isinstance(outcomes['B'], Exception)
        assert outcomes['B'].revision == winner.revision
        assert payloads['A'] == payloads['B']
    order = {(row['actor'], row['event']): index for index, row in enumerate(trace)}
    assert order['A', 'plan_write_attempt_ready'] < order['A', 'original_payload_write_returned']
    assert order['B', 'plan_write_attempt_ready'] < order['A', 'original_payload_write_returned']
    if not different:
        assert order['B', 'original_payload_write_returned'] < order['A', 'original_plan_commit_returned']
        assert order['A', 'original_plan_commit_returned'] < order['B', 'original_plan_commit_returned']
    elif configuration == 'shared':
        assert order['A', 'original_payload_write_returned'] < order['B', 'real_immutable_payload_collision']
        assert order['B', 'public_registry_conflict'] < order['A', 'original_plan_commit_returned']
        assert not next(row for row in trace if row['event'] == 'real_immutable_payload_collision')['canonical_plan_present']
    else:
        assert order['A', 'original_plan_commit_returned'] < order['B', 'real_immutable_payload_collision']
        assert next(row for row in trace if row['event'] == 'real_immutable_payload_collision')['canonical_plan_present']
    after = _facts(core)
    assert after == {'objects': before['objects'] + 9, 'events': before['events'] + 25,
                     'committed_transactions': before['committed_transactions'] + 9}
    created = [row for row in core.event_store.object_rows() if row['version_id'] not in before_ids]
    assert len(created) == 9 and sum(row['object_type'] == 'collaboration_assembly_revision/v2' for row in created) == 1
    assert winner.plan['members'][0]['display_name'] == base['members'][0].display_name
    _checked_reader(core, winner)
    assert first.publish(**base).revision.revision_ref == target_ref
    assert _facts(core) == after
    print('C_CONCURRENCY_TRACE=' + json.dumps({'configuration': configuration, 'different_inputs': different,
        'writer_epoch': core.writer_epoch, 'same_core': first.core is second.core,
        'control_refs': control_refs, 'ordered_events': trace, 'before': before, 'after': after,
        'successes': 1 if different else 2, 'conflicts': int(different),
        'one_original_result': True, 'fresh_readonly_full_consumer': True}, sort_keys=True))


def test_old_writer_is_rejected_after_new_epoch_and_new_owner_can_publish(fixture):
    core, gateway, old, _, member = fixture
    control = old.publish(**request(member, command_id='assembly:old-writer-control'))
    _checked_reader(core, control)
    args = request(member, command_id='assembly:after-owner-reopen')
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    current_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    current = AssemblyAuthorV2(current_gateway, registration(), old.producer)
    assert reopened is not core and reopened.writer_epoch > core.writer_epoch
    before, blobs_before = _facts(reopened), _blobs(reopened)
    with pytest.raises(StaleWriterError, match='stale owner writer'):
        old.publish(**args)
    assert _facts(reopened) == before and _blobs(reopened) == blobs_before
    value = current.publish(**args)
    _checked_reader(reopened, value)
    after = _facts(reopened)
    assert after == {'objects': before['objects'] + 9, 'events': before['events'] + 25,
                     'committed_transactions': before['committed_transactions'] + 9}
    print('C_STALE_TRACE=' + json.dumps({'old_epoch': core.writer_epoch, 'new_epoch': reopened.writer_epoch,
        'stale_entry_rejected_without_facts_or_blobs': True, 'new_owner_original_request_succeeded': True,
        'fresh_readonly_full_consumer': True, 'before': before, 'after': after}, sort_keys=True))
