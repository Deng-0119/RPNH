"""Fixed-cut mechanical seam, with no v2 writer or reader activation."""
import hashlib
import json
import sqlite3

import pytest

from cpn.rpnh.collaboration.candidate_plans import (
    _command_key, _existing_plan_ref_at, _read_candidate_plan_at, _record_ref, read_candidate_plan,
)
from cpn.rpnh.registry._candidate_read_context import _CandidateReadContext
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE
from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE
from test_candidate_plan_persistence import plan_fixture, authored, candidate_request


def _published(f):
    request = candidate_request(f, authored(f))
    return f[-1].publish(**request)


def test_event_store_context_reads_existing_v1_with_identical_bytes_and_no_core_access(plan_fixture, monkeypatch):
    core = plan_fixture[0]
    result = _published(plan_fixture)
    actual, original = [], ObjectStore.read_registered
    def record(store, prepared):
        payload = original(store, prepared)
        actual.append((prepared.object_type, str(prepared.logical_id), str(prepared.version_id),
            hashlib.sha256(payload).hexdigest()))
        return payload
    monkeypatch.setattr(ObjectStore, 'read_registered', record)
    assert read_candidate_plan(core, result.plan_ref) == result
    expected = tuple(actual)
    actual.clear()
    def forbidden(*args, **kwargs):
        raise AssertionError('fixed reader cannot open a second connection or use a writer')
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        context = _CandidateReadContext.from_event_store(core.event_store, db,
            task_id=core.task_id, branch_id=core.branch_id)
        assert context.object_store.read_only
        with monkeypatch.context() as patch:
            patch.setattr(core.event_store, 'connect', forbidden)
            patch.setattr(core, 'get_version', forbidden)
            patch.setattr(core, 'begin', forbidden)
            assert _read_candidate_plan_at(context, result.plan_ref) == result
        assert db.in_transaction
    assert tuple(actual) == expected


@pytest.mark.parametrize('axis', ['inactive', 'foreign', 'task', 'branch', 'finished'])
def test_fixed_context_rejects_wrong_or_finished_cut(plan_fixture, tmp_path, axis):
    core = plan_fixture[0]
    with core.event_store.connect() as db:
        if axis != 'inactive':
            db.execute('BEGIN')
        if axis == 'finished':
            context = _CandidateReadContext.from_core(core, db)
            db.rollback()
            with pytest.raises(TypeError, match='existing SQLite cut'):
                _existing_plan_ref_at(context, _command_key(core.task_id, 'source-a', 'new'))
        elif axis == 'foreign':
            with sqlite3.connect(tmp_path / 'other.sqlite3') as other:
                other.row_factory = sqlite3.Row
                other.execute('BEGIN')
                with pytest.raises(RegistryConflict, match='another Registry'):
                    _CandidateReadContext.from_core(core, other)
        else:
            error = TypeError if axis == 'inactive' else RegistryConflict
            with pytest.raises(error):
                _CandidateReadContext.from_event_store(core.event_store, db,
                    task_id=new_id('task') if axis == 'task' else core.task_id,
                    branch_id='other' if axis == 'branch' else core.branch_id)


def test_version_discovery_returns_exact_v1_and_absence_without_publishing(plan_fixture):
    core = plan_fixture[0]
    result = _published(plan_fixture)
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    key = _command_key(core.task_id, result.plan['source_id'], result.plan['command_id'])
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        context = _CandidateReadContext.from_core(core, db)
        assert _existing_plan_ref_at(context, key) == result.plan_ref
        assert _existing_plan_ref_at(context, key + ':absent') is None
        assert _record_ref(PLAN_V2_TYPE, key + ':plan').version_id == result.plan_ref.version_id
        assert _record_ref(PLAN_V2_TYPE, key + ':plan').entity_id == result.plan_ref.entity_id
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


@pytest.mark.parametrize('axis', ['unknown_type', 'logical_id', 'transaction', 'aborted', 'missing_object', 'missing_key'])
def test_incomplete_command_occupancy_is_not_absence(plan_fixture, axis):
    core = plan_fixture[0]
    result = _published(plan_fixture)
    key = _command_key(core.task_id, result.plan['source_id'], result.plan['command_id'])
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        version = str(result.plan_ref.version_id)
        transaction = db.execute('SELECT transaction_id FROM objects WHERE version_id=?', (version,)).fetchone()[0]
        if axis == 'unknown_type':
            db.execute("UPDATE objects SET object_type='collaboration_candidate_plan/v99' WHERE version_id=?", (version,))
        elif axis == 'logical_id':
            db.execute('UPDATE objects SET logical_id=? WHERE version_id=?', (str(new_id('resource')), version))
        elif axis == 'transaction':
            other = db.execute('SELECT transaction_id FROM transactions WHERE transaction_id<>? LIMIT 1', (transaction,)).fetchone()[0]
            db.execute('UPDATE objects SET transaction_id=? WHERE version_id=?', (other, version))
        elif axis == 'aborted':
            db.execute("UPDATE transactions SET status='aborted' WHERE transaction_id=?", (transaction,))
        elif axis == 'missing_object':
            db.execute('DELETE FROM objects WHERE version_id=?', (version,))
        else:
            db.execute("UPDATE transactions SET idempotency_key=idempotency_key || ':other' WHERE transaction_id=?", (transaction,))
        context = _CandidateReadContext.from_core(core, db)
        with pytest.raises(RegistryConflict, match='occupancy'):
            _existing_plan_ref_at(context, key)
        db.rollback()


def test_discovery_observes_actual_type_but_never_certifies_a_v2_record(plan_fixture):
    core = plan_fixture[0]
    result = _published(plan_fixture)
    key = _command_key(core.task_id, result.plan['source_id'], result.plan['command_id'])
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        # Deliberately inconsistent storage metadata: discovery locates a format,
        # not canonical authority. The original v1 reader must still reject it.
        db.execute('UPDATE objects SET object_type=? WHERE version_id=?', (PLAN_V2_TYPE, str(result.plan_ref.version_id)))
        context = _CandidateReadContext.from_core(core, db)
        ref = _existing_plan_ref_at(context, key)
        assert ref == VersionRef(PLAN_V2_TYPE, result.plan_ref.entity_id, result.plan_ref.version_id)
        with pytest.raises(TypeError, match='VersionRef'):
            _read_candidate_plan_at(context, ref)
        with pytest.raises(RegistryConflict):
            _read_candidate_plan_at(context, result.plan_ref)
        db.rollback()
