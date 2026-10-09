"""Additional independent D0 checks. SQL mutations are corruptions, not API lifecycles."""
import json
import pytest
from test_history_independent import fixture
from cpn.rpnh.registry.acceptance_history import INVALID, UNAVAILABLE
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry.identities import new_id


@pytest.mark.parametrize('kind', ['object', 'event', 'relation', 'transaction'])
def test_original_membership_inventory_has_no_orphans(tmp_path, kind):
    values, store, query = fixture(tmp_path)
    body = values[0]._core.get_version(query.acceptance_ref.version_id).metadata
    with store.connect() as db:
        db.execute('INSERT INTO firing_temporary_members VALUES (?,?,?,?)',
            (body['execution']['firing_ref']['version_id'], kind, 'orphan-original-member', str(query.transaction_id)))
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (kind, result)


@pytest.mark.parametrize('field', ['command_id', 'idempotency_key', 'correlation_id', 'causation_event_id', 'parent_event_ids_json'])
def test_original_start_event_identity_and_causality_are_not_rebuilt(tmp_path, field):
    values, store, query = fixture(tmp_path)
    body = values[0]._core.get_version(query.acceptance_ref.version_id).metadata
    value = str(new_id('event')) if field == 'causation_event_id' else (json.dumps([str(new_id('event'))]) if field == 'parent_event_ids_json' else 'foreign-start-command')
    with store.connect() as db:
        db.execute('UPDATE events SET ' + field + '=? WHERE event_id=?', (value, body['execution']['start_event_id']))
    result = store.classify_child_acceptance_history(query)
    assert result.status == INVALID, (field, result)


def test_unreadable_database_is_classified_without_raising(tmp_path):
    _, store, query = fixture(tmp_path)
    broken = tmp_path / 'broken.sqlite3'
    broken.write_bytes(b'not an SQLite database' + b'\x00' * 512)
    reader = EventStore(broken, store.catalog, read_only=True)
    result = reader.classify_child_acceptance_history(query)
    assert result.status in (INVALID, UNAVAILABLE)
    assert result.proof is None
