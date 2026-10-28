"""Real publication connection ownership, with original transactions intact."""
import gc
from dataclasses import replace
from pathlib import Path

import pytest

from cpn.rpnh.registry.event_store import EventStore, RegistryConflict, StaleWriterError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import PendingEvent, VersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from test_event_store_owned_write_scope import handles


def command(store, key='key'):
    task = new_id('task')
    transaction = new_id('transaction')
    stream = f'transaction:{transaction}'
    event = PendingEvent(event_type='transaction_committed/v1',
        criticality='authoritative', stream_id=stream,
        aggregate_id=str(transaction), aggregate_type='transaction',
        idempotency_key=key, command_id=key,
        payload={'object_count':0, 'relation_count':0, 'fact_count':0},
        payload_schema_ref='registry_v1/transaction_committed/v1')
    return dict(task_id=task, branch_id='main', task_round_id=None,
        net_instance_id=None, transaction_id=transaction, idempotency_key=key,
        writer_epoch=store.writer_epoch, objects=(), events=(event,), relations=(),
        expected_heads={stream:0})


def test_real_commit_duplicate_and_conflict_close(tmp_path, handles):
    store = EventStore(tmp_path/'db', SchemaCatalog())
    kwargs = command(store)
    first = store.publish_batch(**kwargs)
    again = store.publish_batch(**kwargs)
    assert first[0].ordinal is None
    assert again == tuple(replace(event, ordinal=1) for event in first)
    assert len(store.list_events()) == 1
    with pytest.raises(RegistryConflict):
        store.publish_batch(**{**kwargs, 'branch_id':'different'})
    assert len(store.list_events()) == 1
    assert all(db.closed for db in handles)


def test_stale_writer_rollback_closes(tmp_path, handles):
    store = EventStore(tmp_path/'db', SchemaCatalog())
    kwargs = command(store)
    store.rotate_writer(expected_epoch=kwargs['writer_epoch'])
    with pytest.raises(StaleWriterError):
        store.publish_batch(**kwargs)
    assert store.list_events() == ()
    assert all(db.closed for db in handles)


def test_six_materialized_event_reads_close(tmp_path, handles):
    store = EventStore(tmp_path/'db', SchemaCatalog())
    kwargs = command(store)
    published = store.publish_batch(**kwargs)
    expected = tuple(replace(event, ordinal=1) for event in published)
    assert store.list_events_by_transaction(str(kwargs['transaction_id'])) == expected
    assert store.list_events_by_idempotency_key(kwargs['idempotency_key']) == expected
    net = VersionRef('petri_net/v1', new_id('execution_net_definition'), new_id('execution_net_definition_version'))
    assert store.firing_lifecycle_events_for_net(net) == ()
    turn = VersionRef('agent_turn/v1', new_id('agent_turn'), new_id('agent_turn_version'))
    assert store.agent_turn_events_for_ref(turn) == ()
    assert store.agent_turn_events_for_closure(call_version_id='missing',
        attempt_version_id='missing', response_version_id='missing',
        loop_id='missing', sequence=1) == ()
    assert store.adopted_call_events_for_invocation('missing') == ()
    assert all(db.closed for db in handles)


def test_repeated_real_publications_fd_stable(tmp_path, handles):
    store = EventStore(tmp_path/'db', SchemaCatalog())
    fd = Path('/proc/self/fd')
    if not fd.is_dir():
        pytest.skip('Linux fd observation unavailable')
    before = len(list(fd.iterdir()))
    enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(64):
            store.publish_batch(**command(store, key=f'publication-{index}'))
        assert all(db.closed for db in handles)
        assert len(list(fd.iterdir())) == before
        assert len(store.list_events()) == 64
    finally:
        if enabled:
            gc.enable()
