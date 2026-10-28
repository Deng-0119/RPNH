"""Real SQLite query fusion; original full archive validation remains the oracle."""
from collections.abc import Mapping
import json
import pytest
from cpn.components.agent_loop.result_archive import _entries, ARCHIVE_READERS
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from test_result_archive import History
from test_canonical_head_projection import ledger, add_event


def sqlite_history(tmp_path, count=12):
    h = History()
    for i in range(count):
        h.add(sequence=i, kind='managed' if i % 2 else 'program')
    store, task, _ = ledger.__wrapped__(tmp_path)
    ordinal = 0
    for typ, events in [('agent_turn_recorded/v2', h.turns), ('agent_action_settled/v1', h.settlements)]:
        for event in events:
            ordinal += 1
            add_event(store, task, ordinal, stream=h.loop.loop_id, payload=event.payload, event_type=typ)
    for row in h.rows:
        ordinal += 1
        event, tx = add_event(store, task, ordinal)
        doc = json.loads(row['metadata_json'])
        with store.connection_scope() as db:
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                row['logical_id'], doc['agent_action_ref']['version_id'], row['object_type'], 0,
                'application/json', 'fixture', None, 'fixture', row['metadata_json'], str(tx), str(event)))
    h.lifecycle.core.event_store = store
    return h, store


# Frozen pre-fusion algorithm, including complete parsing and exact hydration.
def legacy_entries(lifecycle, anchor, cutoff, tool_names):
    """Resolve settlement versions, never mutable latest action heads."""
    turns = {event.payload["agent_turn_id"]: event
             for event in lifecycle.turn_events(anchor)
             if event.payload["sequence"] < cutoff and event.payload["revision"] <= anchor.revision}
    entries = []
    for event in lifecycle.core.event_store.list_events_by_aggregate(
            anchor.loop_id, event_types=("agent_action_settled/v1",)):
        settled = event.payload
        turn_event = turns.get(settled["agent_turn_id"])
        if settled["revision"] > anchor.revision or turn_event is None:
            continue
        if (settled["agent_loop_id"] != anchor.loop_id
                or turn_event.payload["agent_loop_id"] != anchor.loop_id):
            raise ValueError("result archive history crossed its anchored loop")
        turn_ref = lifecycle.turn_ref_from_event(turn_event)
        candidates = []
        for action_type in ("agent_action/v3", "agent_action/v2"):
            for row in lifecycle.core.event_store.object_rows_by_logical(
                    settled["agent_action_id"], object_type=action_type):
                document = json.loads(row["metadata_json"])
                if (action_type == "agent_action/v2"
                        and (document.get("tool_name") != "run_tool_program"
                             or not isinstance(document.get("result_metadata"), Mapping)
                             or document["result_metadata"].get("kind") != "tool_program_result/v1")):
                    continue
                if (document["agent_turn_ref"] != _ref_payload(turn_ref)
                        or document["tool_call_ordinal"] != settled["tool_call_ordinal"]
                        or document["expected_revision"] != settled["expected_revision"]
                        or document["state"] != settled["settlement"]):
                    continue
                source_loop = lifecycle.hydrate_loop(_version_from_payload(document["agent_loop_ref"]))
                if source_loop.loop_id == anchor.loop_id and source_loop.revision == settled["revision"]:
                    candidates.append(document)
        if not candidates:
            continue  # Other tools and output read pages are not original results.
        if len(candidates) != 1:
            raise ValueError("result archive outcome lacks one exact settlement version")
        document, = candidates
        ref = _version_from_payload(document["agent_action_ref"])
        action = lifecycle.hydrate_action(ref)
        if (action.loop_id != anchor.loop_id or action.turn_sequence != turn_event.payload["sequence"]
                or action.tool_call_id != settled["tool_call_id"] or action.tool_name != settled["tool_name"]):
            raise ValueError("result archive outcome differs from its immutable turn/settlement")
        managed = ref.entity_type == "agent_action/v3"
        metadata = action.managed_action if managed else action.result_metadata
        status = metadata["outcome" if managed else "status"]
        reader = ARCHIVE_READERS[0] if managed else ARCHIVE_READERS[1]
        resource_key = "terminal_receipt_ref" if managed else "output_resource_ref"
        entries.append({
            "agent_action_ref": _ref_payload(ref), resource_key: metadata[resource_key],
            "tool_name": action.tool_name, "status": status,
            "reader": reader if reader in tool_names and (not managed or status == "returned") else None,
            "turn_sequence": action.turn_sequence, "tool_call_id": action.tool_call_id,
            "tool_call_ordinal": action.tool_call_ordinal,
        })
    return sorted(entries, key=lambda item: (item["turn_sequence"], item["tool_call_ordinal"]))



def test_archive_real_sqlite_roundtrips_halved_identical_directory(tmp_path, monkeypatch):
    h, store = sqlite_history(tmp_path)
    statements = []
    connect = store.connect
    def traced():
        db = connect()
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(store, 'connect', traced)
    before = store.max_ordinal()
    expected = legacy_entries(h.lifecycle, h.loop, 20, ARCHIVE_READERS)
    old_queries = sum(sql.startswith('SELECT * FROM objects WHERE logical_id=') for sql in statements)
    statements.clear()
    actual = _entries(h.lifecycle, h.loop, 20, ARCHIVE_READERS)
    new_queries = sum(sql.startswith('SELECT * FROM objects WHERE logical_id=') for sql in statements)
    assert actual == expected and len(actual) == 12
    assert (old_queries, new_queries) == (24, 12)
    assert store.max_ordinal() == before


@pytest.mark.parametrize('types', ((), [], 'agent_action/v3', ('',), (1,), ('x', 'x'), (True,)))
def test_exact_types_selector_rejects_invalid_forms(tmp_path, types):
    _, store = sqlite_history(tmp_path, 1)
    with pytest.raises(TypeError):
        store.object_rows_by_logical('any', object_types=types)


def test_selector_preserves_original_modes_and_sql_parameters(tmp_path):
    h, store = sqlite_history(tmp_path, 1)
    logical = h.rows[0]['logical_id']
    before = store.object_rows_by_logical(logical)
    selected = store.object_rows_by_logical(logical, object_types=('agent_action/v3', 'agent_action/v2'))
    assert [dict(r) for r in selected] == [dict(r) for r in before]
    assert store.object_rows_by_logical(logical, object_types=("x') OR 1=1 --",)) == ()
    with pytest.raises(TypeError):
        store.object_rows_by_logical(logical, object_type='agent_action/v2', object_types=('agent_action/v3',))


@pytest.mark.parametrize('change', ('bad_v3_first', 'bad_v2', 'duplicate', 'revision', 'wrong_loop'))
def test_complete_candidate_validation_matches_frozen_original(tmp_path, change):
    h, store = sqlite_history(tmp_path, 2)
    target = h.rows[0]['logical_id']
    with store.connection_scope() as db:
        row = db.execute('SELECT * FROM objects WHERE logical_id=?', (target,)).fetchone()
        if change in ('bad_v3_first', 'bad_v2', 'duplicate'):
            values = dict(row)
            values['version_id'] = 'fixture-extra-version'
            if change == 'bad_v3_first':
                values.update(object_type='agent_action/v3', metadata_json='{}')
                malformed_v2 = json.loads(row['metadata_json'])
                del malformed_v2['tool_call_ordinal']
                db.execute('UPDATE objects SET metadata_json=? WHERE logical_id=?', (json.dumps(malformed_v2), target))
            elif change == 'bad_v2':
                malformed_v2 = json.loads(row['metadata_json'])
                del malformed_v2['tool_call_ordinal']
                values['metadata_json'] = json.dumps(malformed_v2)
            columns = tuple(values)
            db.execute(f"INSERT INTO objects ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(values.values()))
        else:
            document = json.loads(row['metadata_json'])
            if change == 'revision':
                document['expected_revision'] += 1
            else:
                document['agent_loop_ref']['version_id'] = 'agent_loop_version:' + 'f'*32
            db.execute('UPDATE objects SET metadata_json=? WHERE logical_id=?', (json.dumps(document), target))
    results = []
    for method in (legacy_entries, _entries):
        try:
            results.append(('ok', method(h.lifecycle, h.loop, 20, ARCHIVE_READERS)))
        except Exception as exc:
            results.append((type(exc), str(exc)))
    assert results[0] == results[1]
    if change == 'bad_v3_first':
        assert results[0][0] is KeyError
        # v3 object appears later in rowid order but must still fail first.
        assert results[0][1] == "'agent_turn_ref'"
