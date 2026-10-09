"""Independent original Registry negatives; SQL damage is never a legal lifecycle.

Native evidence fixture allocation is explicitly test-only. No process, socket,
worker, receipt-delivery, physical reservation, or native success is exercised.
"""
from dataclasses import asdict, replace
import json
import pytest

from parent_child_fixtures import accepted_parent, parent_owner, h7
from test_acceptance_history import assertion_for, database_contents
from cpn.rpnh.registry.acceptance_history import VALID, INVALID, UNAVAILABLE
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.publication import _ref_payload


def fixture(tmp_path):
    values = accepted_parent(tmp_path / 'parent')
    owner, *_, accepted = values
    store = owner._core.event_store
    query = assertion_for(owner, accepted)
    return values, store, query


@pytest.mark.parametrize('phase', range(4))
def test_actual_source_head_damage_is_not_rebuilt_from_cut(tmp_path, phase):
    values, store, query = fixture(tmp_path)
    ref = values[4 + phase]
    with store.connect() as db:
        db.execute('UPDATE stream_heads SET sequence=sequence+1 WHERE stream_id=?', ('h7:' + str(ref.entity_id),))
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (phase, result)


@pytest.mark.parametrize('phase', range(3))
def test_each_phase_original_epoch_is_validated(tmp_path, phase):
    values, store, query = fixture(tmp_path)
    ref = values[4 + phase]
    with store.connect() as db:
        tx = db.execute('SELECT transaction_id FROM objects WHERE version_id=?', (str(ref.version_id),)).fetchone()[0]
        db.execute('UPDATE transactions SET writer_epoch=writer_epoch+1 WHERE transaction_id=?', (tx,))
        db.execute('UPDATE events SET writer_fencing_epoch=writer_fencing_epoch+1 WHERE transaction_id=?', (tx,))
        db.execute('UPDATE outbox SET writer_epoch=writer_epoch+1 WHERE transaction_id=?', (tx,))
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (phase, result)


@pytest.mark.parametrize('event_type', ['object_version_published/v1', 'relation_published/v1', 'transaction_committed/v1'])
@pytest.mark.parametrize('field', ['command_id', 'idempotency_key', 'causation_event_id', 'parent_event_ids_json'])
def test_complete_phase_event_envelope_is_checked(tmp_path, event_type, field):
    _, store, query = fixture(tmp_path)
    value = str(new_id('event')) if field == 'causation_event_id' else (json.dumps([str(new_id('event'))]) if field == 'parent_event_ids_json' else 'foreign-history-command')
    with store.connect() as db:
        db.execute('UPDATE events SET ' + field + '=? WHERE transaction_id=? AND event_type=?', (value, str(query.transaction_id), event_type))
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (event_type, field, result)


@pytest.mark.parametrize('damage', ['published_flag', 'published_and_digest', 'published_and_member'])
def test_unsupported_lifecycle_does_not_mask_corruption(tmp_path, damage):
    _, store, query = fixture(tmp_path)
    with store.connect() as db:
        # Intentionally corrupt state, not a claim that H7 can really settle.
        db.execute("UPDATE firing_publications SET state='PUBLISHED'")
        if damage == 'published_and_member':
            db.execute("DELETE FROM firing_temporary_members WHERE member_kind='object' AND member_identity=?", (str(query.acceptance_ref.version_id),))
    if damage == 'published_and_digest':
        query = replace(query, acceptance_sha256='f' * 64)
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (damage, result)


def test_foreign_original_registry_cannot_use_copied_assertion(tmp_path):
    _, _, query = fixture(tmp_path)
    other, *_ = accepted_parent(tmp_path / 'foreign')
    assert other._core.event_store.classify_child_acceptance_history(query).status == INVALID


@pytest.mark.parametrize('later', ['writer_only', 'durable_stop', 'terminal_ready'])
def test_real_later_authority_change_keeps_same_scope_proof_read_only(tmp_path, later):
    values, store, query = fixture(tmp_path)
    owner, admitted, execution = values[:3]
    if later == 'writer_only':
        store.acquire_writer()
    elif later == 'durable_stop':
        owner.record_owner_stop(idempotency_key='independent:stop')
    else:
        from cpn.rpnh.registry.invocations import InvocationLifecycle, TerminalResultPackage
        outputs = owner.products(execution, outcome_id='complete', products={'step.result': (b'"provisional secret result"',)}, command_id='independent:products')
        InvocationLifecycle(owner._core).mark_operation_terminal_ready(execution.operation.canonical.context,
            TerminalResultPackage('completed', tuple(o.resource_ref.as_version_ref() for o in outputs.outputs)), idempotency_key='independent:ready')
    before = database_contents(store)
    result = store.classify_child_acceptance_history(query)
    assert result.status == VALID
    assert result.proof.writer_epoch_at_acceptance == execution.admission_head.writer_fencing_epoch
    assert result.proof.commit_ordinal == query.commit_ordinal
    assert database_contents(store) == before
    assert store.object_row_for_view(store.canonical_view(), version_id=query.acceptance_ref.version_id) is None
    output = json.dumps(asdict(result), default=str)
    for secret in ('allowed_action', 'fresh_bound_bootstrap_once', 'acceptance_json', 'intent_json', 'provisional secret result', 'normalized_request', 'definition', 'public_configuration', 'offline-only', '12345'):
        assert secret not in output


def test_caller_writer_handle_and_reader_memo_do_not_authorize_history(tmp_path):
    values, store, query = fixture(tmp_path)
    read_only = EventStore(store.path, store.catalog, read_only=True)
    store.acquire_writer()
    assert read_only.classify_child_acceptance_history(query).status == VALID
    # This corruption must be seen even after one valid result.
    with store.connect() as db:
        db.execute("DELETE FROM firing_temporary_members WHERE member_kind='transaction' AND member_identity=?", (str(query.transaction_id),))
    assert read_only.classify_child_acceptance_history(query).status == INVALID
