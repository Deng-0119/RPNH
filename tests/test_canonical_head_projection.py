"""Bounded canonical-head reads against the real SQLite visibility predicate.

Raw fixtures deliberately isolate projection semantics, not admission validation.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cpn.rpnh.registry._resource_service import queries
from cpn.rpnh.registry.errors import StaleAuthorityHead
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.resources import RegistryHead


@pytest.fixture
def ledger(tmp_path):
    store = EventStore(tmp_path / "registry.sqlite3", None)
    task = new_id("task")
    core = SimpleNamespace(event_store=store, task_id=task)
    kernel = SimpleNamespace(_ResourceServiceKernel__core=core)
    return store, task, kernel


def add_event(store, task, ordinal, *, stream="main", sequence=None,
              control=None, payload=None, transaction=None,
              event_type="fixture/v1"):
    transaction = transaction or new_id("transaction")
    event_id = new_id("event")
    with store.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO transactions VALUES (?,?,?,?,?,?,?,?)",
            (str(transaction), str(task), "committed", str(transaction),
             "{}", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        record = dict(
            ordinal=ordinal, event_id=str(event_id), event_type=event_type,
            event_schema_version="v1", criticality="normal", task_id=str(task),
            branch_id="main", task_round_id=None, net_instance_id=None,
            stream_id=stream, aggregate_id=stream, aggregate_type="fixture",
            stream_sequence=ordinal if sequence is None else sequence,
            aggregate_version=ordinal, task_control_sequence=control,
            idempotency_key=str(event_id), command_id="fixture",
            correlation_id="fixture", causation_event_id=None,
            parent_event_ids_json="[]", producer_principal="fixture",
            producer_invocation_id=None, transaction_id=str(transaction),
            occurred_at="2026-01-01T00:00:00Z", recorded_at="2026-01-01T00:00:00Z",
            payload_schema_ref="fixture/v1", payload_json=json.dumps(payload or {}),
            writer_fencing_epoch=0,
        )
        db.execute(
            f"INSERT INTO events ({','.join(record)}) VALUES "
            f"({','.join('?' for _ in record)})", tuple(record.values()))
    return event_id, transaction


def mark_firing(store, event_id, member_tx, *, publication_tx=None):
    firing = str(new_id("transition_firing_version"))
    with store.connect() as db:
        db.execute(
            "INSERT INTO firing_publications (firing_version_id,"
            "firing_logical_id,invocation_version_id,invocation_logical_id,"
            "net_version_id,operation_binding_version_id,"
            "admission_checkpoint_version_id,state,opened_transaction_id,"
            "published_transaction_id,operation_result_version_id,"
            "marking_checkpoint_version_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (firing, firing + "-logical", firing + "-inv-version",
             firing + "-inv", "net", "binding", "checkpoint",
             "PUBLISHED" if publication_tx else "PROVISIONAL", str(member_tx),
             str(publication_tx) if publication_tx else None,
             "result" if publication_tx else None,
             "successor" if publication_tx else None),
        )
        db.execute("INSERT INTO firing_temporary_members VALUES (?,?,?,?)",
                   (firing, "event", str(event_id), str(member_tx)))
    return firing


def reference_heads(store, task, ordinals):
    """Pre-optimization valid-ledger reference; intentionally materializes."""
    values = tuple(dict.fromkeys(ordinals))
    if any(value is not None and type(value) is not int for value in values):
        raise TypeError("canonical Registry head ordinals must be integers")
    if not values:
        return {}
    current = store.canonical_events(through_ordinal=store.max_ordinal())
    last = current[-1].ordinal if current else 0
    requested = tuple(dict.fromkeys(last if value is None else value for value in values))
    if min(requested) < 0 or max(requested) > last:
        raise StaleAuthorityHead("canonical Registry head is outside published history")
    epoch = store.writer_epoch
    heads = {}
    for cut in requested:
        events = store.canonical_events(through_ordinal=cut)
        if cut and (not events or events[-1].ordinal != cut):
            raise StaleAuthorityHead("canonical Registry head snapshot changed during hydration")
        streams, control = {}, 0
        for event in events:
            streams[event.stream_id] = event.stream_sequence
            if event.task_id == task and event.task_control_sequence is not None:
                control = max(control, event.task_control_sequence)
        heads[cut] = RegistryHead(cut, epoch, control, dict(streams))
    if store.writer_epoch != epoch:
        raise StaleAuthorityHead("canonical Registry head snapshot changed during hydration")
    return heads


def test_empty_and_input_contract(ledger):
    store, task, kernel = ledger
    for cuts in [(), (None,), (0,), (None, 0, None)]:
        assert queries._canonical_heads(kernel, cuts) == reference_heads(store, task, cuts)
    for cuts in [(-1,), (1,), ("1",), (True,), (1.2,)]:
        with pytest.raises((TypeError, StaleAuthorityHead)) as actual:
            queries._canonical_heads(kernel, cuts)
        with pytest.raises(type(actual.value)) as expected:
            reference_heads(store, task, cuts)
        assert str(actual.value) == str(expected.value)


def test_exact_cuts_late_promotion_and_physical_head(ledger):
    store, task, kernel = ledger
    add_event(store, task, 1, sequence=9, control=3)
    early_id, early_tx = add_event(store, task, 2, stream="promoted", control=8)
    add_event(store, task, 4, sequence=2, control=1)
    _, success_tx = add_event(store, task, 7, stream="publication",
                              event_type="transaction_committed/v1")
    mark_firing(store, early_id, early_tx, publication_tx=success_tx)
    hidden_id, hidden_tx = add_event(store, task, 9, stream="hidden", control=99)
    mark_firing(store, hidden_id, hidden_tx)
    cuts = (None, 4, 1, 0, 7, None, 4)
    actual = queries._canonical_heads(kernel, cuts)
    assert actual == reference_heads(store, task, cuts)
    assert list(actual) == [7, 4, 1, 0]
    assert store.max_ordinal() == 9
    assert "promoted" not in actual[4].stream_heads
    assert "promoted" in actual[7].stream_heads
    assert "hidden" not in actual[7].stream_heads
    assert actual[4].stream_heads["main"] == 2  # last, deliberately not MAX
    assert actual[4].task_control_sequence == 3
    assert actual[7].task_control_sequence == 8
    for cut in (2, 3, 9):
        with pytest.raises(StaleAuthorityHead):
            queries._canonical_heads(kernel, (cut,))
        with pytest.raises(StaleAuthorityHead):
            reference_heads(store, task, (cut,))


def test_other_task_control_not_included_and_no_cached_head(ledger):
    store, task, kernel = ledger
    add_event(store, task, 1, control=2)
    add_event(store, new_id("task"), 2, stream="other", control=50)
    first = queries._canonical_heads(kernel, (None,))[2]
    assert first.task_control_sequence == 2
    add_event(store, task, 3, control=4)
    assert queries._canonical_heads(kernel, (None,))[3].task_control_sequence == 4
    epoch = store.rotate_writer(expected_epoch=store.writer_epoch)
    assert queries._canonical_heads(kernel, (2,))[2].writer_fencing_epoch == epoch


def test_live_epoch_change_during_projection_is_rejected(ledger, monkeypatch):
    store, task, kernel = ledger
    add_event(store, task, 1)
    original = EventStore.writer_epoch
    reads = 0

    def epoch(self):
        nonlocal reads
        reads += 1
        return original.fget(self) + (reads > 1)

    monkeypatch.setattr(EventStore, "writer_epoch", property(epoch))
    with pytest.raises(StaleAuthorityHead, match="snapshot changed"):
        queries._canonical_heads(kernel, (None,))
    assert reads == 2


def test_head_does_not_hydrate_envelopes(ledger, monkeypatch):
    store, task, kernel = ledger
    add_event(store, task, 1, payload={"large": "x" * 100_000})

    def forbidden(*args, **kwargs):
        pytest.fail("head projection must not decode full event envelopes")

    monkeypatch.setattr(store, "canonical_events", forbidden)
    monkeypatch.setattr(store, "_row_to_envelope", forbidden)
    assert queries._canonical_heads(kernel, (None,))[1].stream_heads == {"main": 1}


@pytest.mark.parametrize("column,value", [
    ("payload_json", "{"), ("parent_event_ids_json", "{"),
    ("event_id", "not-a-typed-event-id"),
])
def test_head_only_corruption_compatibility_boundary(ledger, column, value):
    store, task, kernel = ledger
    add_event(store, task, 1)
    with store.connect() as db:
        # Corruption fixture only: expression indexes otherwise reject malformed
        # JSON at write time, before it could reach either read implementation.
        if column == "payload_json":
            indexes = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='events' AND sql LIKE '%payload_json%'"
            ).fetchall()
            for index in indexes:
                db.execute('DROP INDEX "' + index["name"].replace('"', '""') + '"')
        db.execute(f"UPDATE events SET {column}=? WHERE ordinal=1", (value,))
    assert queries._canonical_heads(kernel, (None,))[1].ordinal == 1
    with pytest.raises((ValueError, TypeError)):
        store.canonical_events(through_ordinal=1)


def test_selected_task_identity_still_validated(ledger):
    store, task, kernel = ledger
    add_event(store, task, 1)
    with store.connect() as db:
        db.execute("UPDATE events SET task_id='invalid' WHERE ordinal=1")
    with pytest.raises(ValueError):
        queries._canonical_heads(kernel, (None,))


def test_projection_streams_rows_without_fetchall(ledger, monkeypatch):
    store, task, kernel = ledger
    for ordinal in range(1, 7):
        add_event(store, task, ordinal)
    connect = store.connect
    statements = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            return self.cursor.fetchone()

        def fetchall(self):
            pytest.fail("head projection must stream, not fetchall")

        def __iter__(self):
            return iter(self.cursor)

    class Connection:
        def __init__(self):
            self.db = connect()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.db.__exit__(*args)

        def execute(self, sql, parameters=()):
            statements.append(sql)
            return Cursor(self.db.execute(sql, parameters))

    monkeypatch.setattr(store, "connect", Connection)
    assert queries._canonical_heads(kernel, (None, 3))[6].ordinal == 6
    head_queries = [sql for sql in statements if "FROM events e " in sql]
    assert len(head_queries) == 2
    assert all("SELECT e.*" not in sql for sql in head_queries)
    assert head_queries[0].startswith("SELECT e.ordinal FROM")


def test_cap_handoff_exception_keeps_its_exact_commit_cut(ledger):
    store, task, kernel = ledger
    add_event(store, task, 1)
    member_id, member_tx = add_event(store, task, 2, stream="member")
    firing = mark_firing(store, member_id, member_tx)
    add_event(store, task, 3, stream="member-commit", transaction=member_tx,
              event_type="transaction_committed/v1")
    terminal_id, handoff_tx = add_event(
        store, task, 4, stream="terminal", event_type="agent_loop_terminal/v1",
        payload={"terminal_reason": "task_model_call_cap_handoff"})
    checkpoint = str(new_id("marking_checkpoint_version"))
    firing_ref = dict(entity_type="transition_firing/v1",
                      logical_id=firing + "-logical", version_id=firing)
    checkpoint_id, _ = add_event(
        store, task, 5, stream="checkpoint", transaction=handoff_tx,
        event_type="marking_checkpoint_committed/v1",
        payload=dict(settled=True, checkpoint_ref={"version_id": checkpoint},
                     transition_firing_refs=[firing_ref]))
    add_event(store, task, 6, stream="handoff-commit", transaction=handoff_tx,
              event_type="transaction_committed/v1")
    with store.connect() as db:
        db.execute("UPDATE events SET aggregate_type='transition_firing', "
                   "aggregate_id=? WHERE event_id=?", (firing + "-logical", str(terminal_id)))
        db.execute(
            "INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (checkpoint + "-logical", checkpoint, "marking_checkpoint/v1", 0,
             "application/json", "fixture", None, "fixture",
             json.dumps(dict(settled=True,
                             marking_checkpoint_ref={"version_id": checkpoint},
                             transition_firing_refs=[firing_ref])),
             str(handoff_tx), str(checkpoint_id)),
        )
        for kind, identity in (("event", terminal_id), ("event", checkpoint_id),
                               ("object", checkpoint)):
            db.execute("INSERT INTO firing_temporary_members VALUES (?,?,?,?)",
                       (firing, kind, str(identity), str(handoff_tx)))
    actual = queries._canonical_heads(kernel, (None, 3, 1))
    assert actual == reference_heads(store, task, (None, 3, 1))
    assert "member" not in actual[3].stream_heads
    assert {"member", "terminal", "checkpoint"}.issubset(actual[6].stream_heads)
    with store.connect() as db:
        db.execute(
            "UPDATE firing_publications SET state='SUPERSEDED', "
            "published_transaction_id=?,marking_checkpoint_version_id=? "
            "WHERE firing_version_id=?", (str(handoff_tx), checkpoint, firing))
    assert queries._canonical_heads(kernel, (6, 3, 1)) == (
        reference_heads(store, task, (6, 3, 1)))
    # The exception does not require PUBLISHED or authorize a
    # member when the exact handoff transaction loses committed authority.
    with store.connect() as db:
        db.execute("UPDATE transactions SET status='pending' WHERE transaction_id=?",
                   (str(handoff_tx),))
    rejected = queries._canonical_heads(kernel, (None,))[6]
    assert "member" not in rejected.stream_heads
    assert rejected == reference_heads(store, task, (None,))[6]


def inject_before_query(store, monkeypatch, prefix, action):
    """Run one deterministic second-connection write at a read boundary."""
    connect = store.connect
    fired = False

    class Connection:
        def __init__(self):
            self.db = connect()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.db.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, parameters=()):
            nonlocal fired
            if not fired and sql.startswith(prefix):
                fired = True
                action()
            return self.db.execute(sql, parameters)

    monkeypatch.setattr(store, "connect", Connection)
    return lambda: fired


def test_actual_writer_rotation_between_cut_reads_is_rejected(ledger, monkeypatch):
    store, task, kernel = ledger
    add_event(store, task, 1)
    epoch = store.writer_epoch
    fired = inject_before_query(
        store, monkeypatch, "SELECT e.ordinal,e.stream_id",
        lambda: store.rotate_writer(expected_epoch=epoch))
    with pytest.raises(StaleAuthorityHead, match="snapshot changed"):
        queries._canonical_heads(kernel, (None,))
    assert fired()
    assert store.writer_epoch == epoch + 1


def test_promotion_after_captured_maximum_is_not_imported(ledger, monkeypatch):
    store, task, kernel = ledger
    add_event(store, task, 1)
    member_id, member_tx = add_event(store, task, 2, stream="late")
    firing = mark_firing(store, member_id, member_tx)
    add_event(store, task, 4)

    def promote():
        _, tx = add_event(store, task, 5, stream="publication",
                          event_type="transaction_committed/v1")
        with store.connect() as db:
            db.execute(
                "UPDATE firing_publications SET state='PUBLISHED', "
                "published_transaction_id=?,operation_result_version_id='result', "
                "marking_checkpoint_version_id='successor' WHERE firing_version_id=?",
                (str(tx), firing))

    fired = inject_before_query(
        store, monkeypatch, "SELECT e.ordinal FROM events", promote)
    heads = queries._canonical_heads(kernel, (None, 1))
    assert fired()
    assert list(heads) == [4, 1]
    assert "late" not in heads[4].stream_heads
    assert store.max_ordinal() == 5
    current = queries._canonical_heads(kernel, (None,))[5]
    assert "late" in current.stream_heads
    assert current == reference_heads(store, task, (None,))[5]
