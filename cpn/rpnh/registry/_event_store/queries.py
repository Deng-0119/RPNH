"""Raw EventStore record queries with no authority promotion."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from ..event_store import RegistryCorruptError, _exact_ref_payload
from ..identities import TypedId
from ..models import EventEnvelope, VersionRef

def stream_heads(store) -> dict[str, int]:
    with store.connect() as db:
        return {row["stream_id"]: int(row["sequence"])
                for row in db.execute("SELECT stream_id,sequence FROM stream_heads")}

def stream_head_at(store, stream_id: str, *, through_ordinal: int) -> int:
    """Return one stream head at an already captured Registry ordinal."""
    if not stream_id or through_ordinal < 0:
        raise ValueError("stream head lookup requires a stream and ordinal")
    with store.connect() as db:
        row = db.execute(
            "SELECT stream_sequence FROM events WHERE stream_id=? "
            "AND ordinal<=? ORDER BY ordinal DESC LIMIT 1",
            (stream_id, through_ordinal),
        ).fetchone()
    return int(row["stream_sequence"]) if row is not None else 0

def max_ordinal(store) -> int:
    with store.connect() as db:
        return int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])

def list_events(store, *, after_ordinal: int = 0) -> tuple[EventEnvelope, ...]:
    with store.connect() as db:
        rows = db.execute("SELECT * FROM events WHERE ordinal>? ORDER BY ordinal",
                          (after_ordinal,)).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def event_by_id(store, event_id: TypedId) -> EventEnvelope | None:
    if not isinstance(event_id, TypedId) or event_id.kind != "event":
        raise TypeError("event lookup requires one exact typed event id")
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM events WHERE event_id=?", (str(event_id),)
        ).fetchone()
        return store._row_to_envelope(row) if row is not None else None

def first_and_last_events(
        store,
) -> tuple[EventEnvelope | None, EventEnvelope | None]:
    """Return persisted Registry time bounds without hydrating its history."""
    with store.connect() as db:
        first = db.execute(
            "SELECT * FROM events ORDER BY ordinal LIMIT 1").fetchone()
        last = db.execute(
            "SELECT * FROM events ORDER BY ordinal DESC LIMIT 1").fetchone()
    return (
        store._row_to_envelope(first) if first is not None else None,
        store._row_to_envelope(last) if last is not None else None,
    )

def has_event_idempotency_prefix(store, prefix: str) -> bool:
    """Test one exact key prefix without materializing unrelated events."""
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("event idempotency prefix must be nonempty")
    upper_bound = prefix + "\U0010ffff"
    with store.connect() as db:
        row = db.execute(
            "SELECT 1 FROM events "
            "WHERE idempotency_key>=? AND idempotency_key<? LIMIT 1",
            (prefix, upper_bound),
        ).fetchone()
    return row is not None

def list_events_by_type(
        store, event_types: tuple[str, ...], *,
        after_ordinal: int = 0) -> tuple[EventEnvelope, ...]:
    if not event_types:
        return ()
    placeholders = ",".join("?" * len(event_types))
    with store.connect() as db:
        rows = db.execute(
            f"SELECT * FROM events WHERE ordinal>? AND event_type IN "
            f"({placeholders}) ORDER BY ordinal",
            (after_ordinal, *event_types)).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def list_events_by_aggregate(
        store, aggregate_id: str, *,
        event_types: tuple[str, ...] = (),
        after_ordinal: int = 0) -> tuple[EventEnvelope, ...]:
    with store.connect() as db:
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            rows = db.execute(
                f"SELECT * FROM events WHERE ordinal>? AND aggregate_id=? "
                f"AND event_type IN ({placeholders}) ORDER BY ordinal",
                (after_ordinal, aggregate_id, *event_types)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM events WHERE ordinal>? AND aggregate_id=? "
                "ORDER BY ordinal",
                (after_ordinal, aggregate_id)).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def list_events_by_producer(
        store, producer_invocation_id: TypedId | str, *,
        event_types: tuple[str, ...] = (),
        after_ordinal: int = 0) -> tuple[EventEnvelope, ...]:
    """Read only events emitted by one exact invocation principal."""
    with store.connect() as db:
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            rows = db.execute(
                f"SELECT * FROM events WHERE ordinal>? "
                f"AND producer_invocation_id=? AND event_type IN "
                f"({placeholders}) ORDER BY ordinal",
                (after_ordinal, str(producer_invocation_id),
                 *event_types)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM events WHERE ordinal>? "
                "AND producer_invocation_id=? ORDER BY ordinal",
                (after_ordinal, str(producer_invocation_id))).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def firing_lifecycle_events_for_net(
        store, net_ref: VersionRef) -> tuple[EventEnvelope, ...]:
    """Read only firing admission/settlement facts for one exact net."""
    ref = _exact_ref_payload(net_ref)
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE (net_instance_id=? AND "
            "event_type IN ('firing_admitted/v1',"
            "'transition_firing_settled/v1',"
            "'transition_firing_superseded_by_native_resume/v1')) OR ("
            "event_type='transition_firing_superseded_by_growth_recovery/v1' "
            "AND json_extract(payload_json,"
            "'$.superseded_net_ref.entity_type')=? "
            "AND json_extract(payload_json,"
            "'$.superseded_net_ref.logical_id')=? "
            "AND json_extract(payload_json,"
            "'$.superseded_net_ref.version_id')=?) ORDER BY ordinal",
            (str(net_ref.entity_id), ref["entity_type"],
             ref["logical_id"], ref["version_id"]),
        ).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def agent_turn_events_for_closure(
        store, *, call_version_id: TypedId | str,
        attempt_version_id: TypedId | str,
        response_version_id: TypedId | str,
        loop_id: TypedId | str, sequence: int,
        ) -> tuple[EventEnvelope, ...]:
    """Read turn facts that can collide with one exact turn closure."""
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events "
            "WHERE event_type='agent_turn_recorded/v1' AND ("
            "json_extract(payload_json,'$.llm_call_version_id')=? OR "
            "json_extract(payload_json,'$.provider_attempt_version_id')=? OR "
            "json_extract(payload_json,'$.response_version_id')=? OR ("
            "json_extract(payload_json,'$.agent_loop_id')=? AND "
            "json_extract(payload_json,'$.sequence')=?)) ORDER BY ordinal",
            (str(call_version_id), str(attempt_version_id),
             str(response_version_id), str(loop_id), sequence),
        ).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def agent_turn_events_for_ref(
        store, turn_ref: VersionRef) -> tuple[EventEnvelope, ...]:
    """Read turn-recorded facts for one exact agent-turn version."""
    if (not isinstance(turn_ref, VersionRef)
            or turn_ref.entity_type != "agent_turn/v1"):
        raise TypeError(
            "agent turn event lookup requires an exact agent_turn/v1 ref")
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events "
            "WHERE event_type='agent_turn_recorded/v2' "
            "AND json_extract(payload_json,'$.agent_turn_id')=? "
            "AND json_extract(payload_json,'$.agent_turn_version_id')=? "
            "ORDER BY ordinal",
            (str(turn_ref.entity_id), str(turn_ref.version_id)),
        ).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def adopted_call_events_for_invocation(
        store, invocation_id: TypedId | str,
        ) -> tuple[EventEnvelope, ...]:
    """Read legacy call adoptions whose exact call belongs to an invocation."""
    with store.connect() as db:
        rows = db.execute(
            "SELECT e.* FROM events e JOIN objects o "
            "ON o.version_id=json_extract("
            "e.payload_json,'$.llm_call_version_id') "
            "WHERE e.event_type='llm_call_result_adopted/v1' AND (("
            "o.object_type='llm_call_spec/v1' AND "
            "json_extract(o.metadata_json,'$.invocation_id')=?) OR ("
            "o.object_type='llm_call_spec/v2' AND "
            "json_extract(o.metadata_json,"
            "'$.invocation_ref.logical_id')=?)) ORDER BY e.ordinal",
            (str(invocation_id), str(invocation_id)),
        ).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def list_events_by_transaction(
        store, transaction_id: str) -> tuple[EventEnvelope, ...]:
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
            (transaction_id,)).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def list_events_by_idempotency_key(
        store, idempotency_key: str) -> tuple[EventEnvelope, ...]:
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE idempotency_key=? ORDER BY ordinal",
            (idempotency_key,)).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def _row_to_envelope(row: sqlite3.Row) -> EventEnvelope:
    return EventEnvelope(
        envelope_version="v1", event_id=TypedId.parse(row["event_id"], expected="event"),
        event_type=row["event_type"], event_schema_version=row["event_schema_version"],
        criticality=row["criticality"],
        task_id=TypedId.parse(row["task_id"], expected="task"), branch_id=row["branch_id"],
        task_round_id=TypedId.parse(row["task_round_id"], expected="task_round")
        if row["task_round_id"] else None,
        net_instance_id=TypedId.parse(row["net_instance_id"], expected="net_instance")
        if row["net_instance_id"] else None,
        stream_id=row["stream_id"], aggregate_id=row["aggregate_id"],
        aggregate_type=row["aggregate_type"], stream_sequence=row["stream_sequence"],
        aggregate_version=row["aggregate_version"],
        task_control_sequence=row["task_control_sequence"],
        idempotency_key=row["idempotency_key"], command_id=row["command_id"],
        correlation_id=row["correlation_id"],
        causation_event_id=TypedId.parse(row["causation_event_id"], expected="event")
        if row["causation_event_id"] else None,
        parent_event_ids=tuple(TypedId.parse(item, expected="event")
                               for item in json.loads(row["parent_event_ids_json"])),
        producer_principal=row["producer_principal"],
        producer_invocation_id=TypedId.parse(row["producer_invocation_id"], expected="invocation")
        if row["producer_invocation_id"] else None,
        transaction_id=TypedId.parse(row["transaction_id"], expected="transaction"),
        occurred_at=row["occurred_at"], recorded_at=row["recorded_at"],
        payload_schema_ref=row["payload_schema_ref"],
        payload=json.loads(row["payload_json"]),
        writer_fencing_epoch=row["writer_fencing_epoch"],
        ordinal=int(row["ordinal"]))

def _verified_persisted_event_record(
        store, db: sqlite3.Connection, row: sqlite3.Row) -> Mapping[str, Any]:
    """Verify only persisted event structure and publication pairing."""

    def verified_envelope(event_row: sqlite3.Row) -> EventEnvelope:
        try:
            candidate = store._row_to_envelope(event_row)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(
                "persisted event envelope is malformed") from exc
        schema_version = candidate.event_type.rsplit("/", 1)[-1]
        if (candidate.envelope_version != "v1"
                or candidate.event_schema_version != schema_version
                or not schema_version.startswith("v")
                or not schema_version[1:].isdigit()
                or not candidate.stream_id
                or not candidate.aggregate_id
                or not candidate.aggregate_type
                or candidate.stream_sequence < 1
                or candidate.aggregate_version < 1
                or candidate.writer_fencing_epoch < 0
                or not isinstance(candidate.payload, Mapping)):
            raise RegistryCorruptError(
                "persisted event envelope schema is malformed")
        return candidate

    event = verified_envelope(row)

    transaction = db.execute(
        "SELECT status,task_id,writer_epoch FROM transactions "
        "WHERE transaction_id=?",
        (str(event.transaction_id),),
    ).fetchone()
    if transaction is None:
        raise RegistryCorruptError(
            "persisted event transaction authority is missing")
    outbox = db.execute(
        "SELECT task_id,writer_epoch,event_ids_json FROM outbox "
        "WHERE transaction_id=?", (str(event.transaction_id),),
    ).fetchone()
    try:
        outbox_ids = (json.loads(outbox["event_ids_json"])
                      if outbox is not None else None)
        transaction_event_rows = db.execute(
            "SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
            (str(event.transaction_id),),
        ).fetchall()
        transaction_events = [
            verified_envelope(item) for item in transaction_event_rows]
        authoritative_event_ids = [
            str(item.event_id) for item in transaction_events]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            "persisted event transaction authority is malformed") from exc
    if (transaction["status"] not in {"committed", "aborted"}
            or transaction["task_id"] != str(event.task_id)
            or int(transaction["writer_epoch"])
            != event.writer_fencing_epoch
            or outbox is None or outbox["task_id"] != str(event.task_id)
            or int(outbox["writer_epoch"]) != event.writer_fencing_epoch
            or not isinstance(outbox_ids, list)
            or outbox_ids != authoritative_event_ids
            or str(event.event_id) not in authoritative_event_ids
            or any(str(item.task_id) != transaction["task_id"]
                   or item.writer_fencing_epoch != int(transaction["writer_epoch"])
                   or item.transaction_id != event.transaction_id
                   for item in transaction_events)):
        raise RegistryCorruptError(
            "persisted event transaction authority is corrupt")

    object_publications = [
        item for item in transaction_events
        if item.event_type == "object_version_published/v1"
    ]
    relation_publications = [
        item for item in transaction_events
        if item.event_type == "relation_published/v1"
    ]
    committed_facts = [
        item for item in transaction_events
        if item.event_type == "transaction_committed/v1"
    ]
    aborted_facts = [
        item for item in transaction_events
        if item.event_type == "transaction_aborted/v1"
    ]
    object_rows = db.execute(
        "SELECT * FROM objects WHERE transaction_id=?",
        (str(event.transaction_id),),
    ).fetchall()
    relation_rows = db.execute(
        "SELECT * FROM relations WHERE transaction_id=?",
        (str(event.transaction_id),),
    ).fetchall()
    committed_terminal_is_exact = (
        len(committed_facts) == 1
        and not aborted_facts
    )
    aborted_terminal_is_exact = (
        len(aborted_facts) == 1 and not committed_facts
    )
    if not (
        (transaction["status"] == "committed"
         and committed_terminal_is_exact)
        or (transaction["status"] == "aborted"
            and aborted_terminal_is_exact)
    ):
        raise RegistryCorruptError(
            "persisted transaction terminal fact authority is corrupt")
    if len(object_publications) != len(object_rows):
        raise RegistryCorruptError(
            "persisted object publication batch is corrupt")
    if len(relation_publications) != len(relation_rows):
        raise RegistryCorruptError(
            "persisted relation publication batch is corrupt")
    try:
        for object_row in object_rows:
            matching_publications = [
                item for item in object_publications
                if str(item.payload.get("version_id"))
                == object_row["version_id"]
            ]
            if len(matching_publications) != 1:
                raise RegistryCorruptError(
                    "persisted object publication fact is corrupt")
            publication = matching_publications[0]
            if (str(publication.event_id)
                    != object_row["published_event_id"]
                    or str(publication.transaction_id)
                    != object_row["transaction_id"]
                    or (str(publication.producer_invocation_id)
                        if publication.producer_invocation_id else None)
                    != object_row["producer_invocation_id"]):
                raise RegistryCorruptError(
                    "persisted object publication fact is corrupt")
        for relation_row in relation_rows:
            matching_publications = [
                item for item in relation_publications
                if str(item.payload.get("relation_id"))
                == relation_row["relation_id"]
            ]
            if len(matching_publications) != 1:
                raise RegistryCorruptError(
                    "persisted relation publication fact is corrupt")
            publication = matching_publications[0]
            if (str(publication.event_id)
                    != relation_row["published_event_id"]
                    or str(publication.transaction_id)
                    != relation_row["transaction_id"]):
                raise RegistryCorruptError(
                    "persisted relation publication fact is corrupt")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            "persisted event publication batch is malformed") from exc

    for stream_id in {item.stream_id for item in transaction_events}:
        stream_rows = db.execute(
            "SELECT * FROM events WHERE stream_id=? ORDER BY stream_sequence",
            (stream_id,),
        ).fetchall()
        for expected_sequence, stream_row in enumerate(stream_rows, start=1):
            try:
                stream_event = store._row_to_envelope(stream_row)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RegistryCorruptError(
                    "persisted event stream is malformed") from exc
            if (stream_event.stream_sequence != expected_sequence
                    or stream_event.aggregate_version != expected_sequence):
                raise RegistryCorruptError(
                    "persisted event stream authority is corrupt")
        stream_head = db.execute(
            "SELECT sequence FROM stream_heads WHERE stream_id=?",
            (stream_id,),
        ).fetchone()
        if (stream_head is None
                or int(stream_head["sequence"]) != len(stream_rows)):
            raise RegistryCorruptError(
                "persisted event stream head is corrupt")

    task_sequences = [int(value["task_control_sequence"])
                      for value in db.execute(
                          "SELECT task_control_sequence FROM events "
                          "WHERE task_id=? AND task_control_sequence IS NOT NULL "
                          "ORDER BY task_control_sequence",
                          (str(event.task_id),)).fetchall()]
    task_head = db.execute(
        "SELECT sequence FROM task_control_heads WHERE task_id=?",
        (str(event.task_id),),
    ).fetchone()
    if (task_sequences != list(range(1, len(task_sequences) + 1))
            or task_head is None
            or int(task_head["sequence"]) != len(task_sequences)):
        raise RegistryCorruptError(
            "persisted event task-control head is corrupt")
    return dict(row)

def object_row(store, version_id: TypedId) -> sqlite3.Row | None:
    key = str(version_id)
    with store._lock:
        cached = store._object_row_memo.get(key)
    if cached is not None:
        return cached
    with store.connect() as db:
        row = db.execute("SELECT * FROM objects WHERE version_id=?",
                         (key,)).fetchone()
    if row is not None:
        # Object versions are immutable after their publishing transaction
        # commits.  Cache only positive exact-version reads; a miss remains
        # observable after a later transaction publishes that version.
        with store._lock:
            return store._object_row_memo.setdefault(key, row)
    return None

def firing_publication_row(
        store, firing_version_id: TypedId | str) -> sqlite3.Row | None:
    """Return the mutable publication gate for one exact firing."""

    with store.connect() as db:
        return db.execute(
            "SELECT * FROM firing_publications WHERE firing_version_id=?",
            (str(firing_version_id),),
        ).fetchone()

def provisional_firing_rows_for_checkpoint(
        store, *, net_version_id: TypedId | str,
        checkpoint_version_id: TypedId | str,
) -> tuple[sqlite3.Row, ...]:
    """Return open firing roots admitted from one exact current cut."""

    net_key = str(net_version_id)
    checkpoint_key = str(checkpoint_version_id)
    if not net_key or not checkpoint_key:
        raise TypeError(
            "provisional firing selection requires exact net/checkpoint ids")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM firing_publications "
            "WHERE state='PROVISIONAL' AND net_version_id=? "
            "AND admission_checkpoint_version_id=? ORDER BY rowid",
            (net_key, checkpoint_key),
        ).fetchall())

def firing_publication_for_invocation(
        store, invocation_id: TypedId | str) -> sqlite3.Row | None:
    """Resolve the publication gate that owns one invocation lineage."""

    with store.connect() as db:
        return db.execute(
            "SELECT * FROM firing_publications "
            "WHERE invocation_logical_id=? OR invocation_version_id=?",
            (str(invocation_id), str(invocation_id)),
        ).fetchone()

def firing_publication_for_member(
        store, member_kind: str,
        member_identity: TypedId | str) -> sqlite3.Row | None:
    """Resolve one temporary member through its firing publication root."""

    if member_kind not in {"object", "event", "relation", "transaction"}:
        raise TypeError("unknown firing temporary member kind")
    with store.connect() as db:
        rows = db.execute(
            "SELECT p.* FROM firing_temporary_members m "
            "JOIN firing_publications p "
            "ON p.firing_version_id=m.firing_version_id "
            "WHERE m.member_kind=? AND m.member_identity=?",
            (member_kind, str(member_identity)),
        ).fetchall()
    if len(rows) > 1:
        raise RegistryCorruptError(
            "temporary Registry member belongs to multiple firings")
    return rows[0] if rows else None

def workspace_lineage_head(
        store, workspace_lineage_id: TypedId | str) -> sqlite3.Row | None:
    """Return the sole SQL-CAS workspace head for one run lineage."""

    with store.connect() as db:
        return db.execute(
            "SELECT * FROM workspace_lineage_heads "
            "WHERE workspace_lineage_id=?",
            (str(workspace_lineage_id),),
        ).fetchone()

def object_publication_event(
        store, version_id: TypedId, *, object_type: str,
) -> EventEnvelope:
    """Return the integrity-checked event that atomically published an object.

    Timed Petri guards use this durable Registry timestamp as the queue-entry
    clock.  The helper never samples a new time and never creates scheduler
    state: it only projects the immutable object's existing publication fact.
    """

    if (not isinstance(version_id, TypedId)
            or not isinstance(object_type, str) or not object_type):
        raise TypeError(
            "object publication lookup requires an exact typed version")
    with store.connect() as db:
        row = db.execute(
            "SELECT e.* FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id "
            "WHERE o.version_id=? AND o.object_type=?",
            (str(version_id), object_type),
        ).fetchone()
        if row is None:
            raise RegistryCorruptError(
                "registered object has no exact publication event")
        object_row = db.execute(
            "SELECT o.logical_id,o.transaction_id,o.published_event_id,"
            "t.status AS transaction_status "
            "FROM objects o JOIN transactions t "
            "ON t.transaction_id=o.transaction_id "
            "WHERE o.version_id=? AND o.object_type=?",
            (str(version_id), object_type),
        ).fetchone()
        transaction_id = (
            str(object_row["transaction_id"])
            if object_row is not None else "")
        # One successful call verifies every event/object/relation in the
        # exact transaction.  The immutable read-only snapshot may then
        # reuse that transaction-wide proof while each object still has
        # to resolve its own exact publication row below.
        if (not store.read_only
                or transaction_id
                not in store._verified_read_only_transactions):
            store._verified_persisted_event_record(db, row)
            if store.read_only:
                store._verified_read_only_transactions.add(transaction_id)
        event = store._row_to_envelope(row)
    if (object_row is None
            or object_row["transaction_status"] != "committed"
            or event.event_id != TypedId.parse(
                str(object_row["published_event_id"]), expected="event")
            or event.transaction_id != TypedId.parse(
                str(object_row["transaction_id"]), expected="transaction")):
        raise RegistryCorruptError(
            "object publication event differs from its committed object row")
    return event

def latest_object_row(
        store, logical_id: TypedId, *, object_type: str) -> sqlite3.Row | None:
    """Return the latest committed version for one exact logical object."""

    with store.connect() as db:
        return db.execute(
            "SELECT * FROM objects WHERE logical_id=? AND object_type=? "
            "ORDER BY rowid DESC LIMIT 1",
            (str(logical_id), object_type)).fetchone()

def object_rows_by_logical(
        store, logical_id: TypedId | str, *,
        object_type: str | None = None) -> tuple[sqlite3.Row, ...]:
    """Read only immutable versions under one logical identity."""
    with store.connect() as db:
        if object_type is None:
            rows = db.execute(
                "SELECT * FROM objects WHERE logical_id=? ORDER BY rowid",
                (str(logical_id),)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM objects WHERE logical_id=? AND object_type=? "
                "ORDER BY rowid",
                (str(logical_id), object_type)).fetchall()
    return tuple(rows)

def object_rows_by_type(
        store, object_type: str) -> tuple[sqlite3.Row, ...]:
    """Read only objects of one registered type."""
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects WHERE object_type=? ORDER BY rowid",
            (object_type,)).fetchall())

def execution_environment_inventory_rows(
        store, *, task_ref: VersionRef, task_round_ref: VersionRef,
        net_instance_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
    """Read environment inventories for one exact task/round/net scope."""
    expected = (
        (task_ref, "task/v1"),
        (task_round_ref, "task_round/v1"),
        (net_instance_ref, "net_instance/v1"),
    )
    if any(not isinstance(ref, VersionRef)
           or ref.entity_type != entity_type
           for ref, entity_type in expected):
        raise TypeError(
            "execution inventory lookup requires exact task/round/net refs")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects "
            "WHERE object_type='resource_version/v1' "
            "AND json_extract(metadata_json,"
            "'$.descriptors.content_role')="
            "'execution_environment_inventory' "
            "AND json_extract(metadata_json,'$.task_ref.entity_type')=? "
            "AND json_extract(metadata_json,'$.task_ref.logical_id')=? "
            "AND json_extract(metadata_json,'$.task_ref.version_id')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.task_round_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.task_round_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.task_round_ref.version_id')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.net_instance_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.net_instance_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.extensions.\"registry.execution_environment_inventory/v1\".scope.net_instance_ref.version_id')=? "
            "ORDER BY rowid",
            (task_ref.entity_type, str(task_ref.entity_id),
             str(task_ref.version_id), task_round_ref.entity_type,
             str(task_round_ref.entity_id), str(task_round_ref.version_id),
             net_instance_ref.entity_type, str(net_instance_ref.entity_id),
             str(net_instance_ref.version_id)),
        ).fetchall())

def transition_firing_rows_for_task_net_transition(
        store, *, task_ref: VersionRef, net_instance_ref: VersionRef,
        transition_id: str) -> tuple[sqlite3.Row, ...]:
    """Read committed firings for one exact task, net, and transition."""
    expected = (
        (task_ref, "task/v1"),
        (net_instance_ref, "net_instance/v1"),
    )
    if (any(not isinstance(ref, VersionRef) or ref.entity_type != entity_type
            for ref, entity_type in expected)
            or not isinstance(transition_id, str) or not transition_id):
        raise TypeError(
            "transition firing lookup requires exact task/net refs and "
            "one transition id")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT o.* FROM objects o JOIN transactions t "
            "ON t.transaction_id=o.transaction_id AND t.status='committed' "
            "WHERE o.object_type='transition_firing/v1' "
            "AND json_extract(o.metadata_json,'$.task_ref.entity_type')=? "
            "AND json_extract(o.metadata_json,'$.task_ref.logical_id')=? "
            "AND json_extract(o.metadata_json,'$.task_ref.version_id')=? "
            "AND json_extract(o.metadata_json,"
            "'$.net_instance_ref.entity_type')=? "
            "AND json_extract(o.metadata_json,"
            "'$.net_instance_ref.logical_id')=? "
            "AND json_extract(o.metadata_json,"
            "'$.net_instance_ref.version_id')=? "
            "AND json_extract(o.metadata_json,'$.transition_id')=? "
            "ORDER BY o.rowid",
            (task_ref.entity_type, str(task_ref.entity_id),
             str(task_ref.version_id), net_instance_ref.entity_type,
             str(net_instance_ref.entity_id), str(net_instance_ref.version_id),
             transition_id),
        ).fetchall())

def object_rows_by_producer(
        store, producer_invocation_id: TypedId | str, *,
        object_types: tuple[str, ...] = ()) -> tuple[sqlite3.Row, ...]:
    """Read only objects published by one exact invocation principal."""
    with store.connect() as db:
        if object_types:
            placeholders = ",".join("?" * len(object_types))
            rows = db.execute(
                f"SELECT * FROM objects WHERE producer_invocation_id=? "
                f"AND object_type IN ({placeholders}) ORDER BY rowid",
                (str(producer_invocation_id), *object_types)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM objects WHERE producer_invocation_id=? "
                "ORDER BY rowid",
                (str(producer_invocation_id),)).fetchall()
    return tuple(rows)

def object_rows_by_invocation_id(
        store, invocation_id: TypedId | str, *,
        object_types: tuple[str, ...] = ()) -> tuple[sqlite3.Row, ...]:
    """Read objects whose immutable metadata names one invocation."""
    with store.connect() as db:
        if object_types:
            placeholders = ",".join("?" * len(object_types))
            rows = db.execute(
                f"SELECT * FROM objects WHERE "
                f"json_extract(metadata_json,'$.invocation_id')=? "
                f"AND object_type IN ({placeholders}) ORDER BY rowid",
                (str(invocation_id), *object_types)).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM objects WHERE "
                "json_extract(metadata_json,'$.invocation_id')=? "
                "ORDER BY rowid",
                (str(invocation_id),)).fetchall()
    return tuple(rows)

def resource_address_binding_rows_for_invocation_scope(
        store, *, invocation_scope_ref: VersionRef,
        operation_binding_authorization_ref: VersionRef,
) -> tuple[sqlite3.Row, ...]:
    """Read address bindings for one exact invocation and authorization."""

    if (not isinstance(invocation_scope_ref, VersionRef)
            or invocation_scope_ref.entity_type != "invocation/v1"
            or not isinstance(operation_binding_authorization_ref, VersionRef)
            or operation_binding_authorization_ref.entity_type
            != "operation_binding/v1"):
        raise TypeError(
            "address-binding lookup requires exact invocation and "
            "operation-binding refs")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects "
            "WHERE object_type='resource_address_binding/v1' "
            "AND json_extract(metadata_json,'$.scope_ref.entity_type')=? "
            "AND json_extract(metadata_json,'$.scope_ref.logical_id')=? "
            "AND json_extract(metadata_json,'$.scope_ref.version_id')=? "
            "AND json_extract(metadata_json,"
            "'$.authorization_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.authorization_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.authorization_ref.version_id')=? ORDER BY rowid",
            (invocation_scope_ref.entity_type,
             str(invocation_scope_ref.entity_id),
             str(invocation_scope_ref.version_id),
             operation_binding_authorization_ref.entity_type,
             str(operation_binding_authorization_ref.entity_id),
             str(operation_binding_authorization_ref.version_id)),
        ).fetchall())

def provider_attempt_rows_for_call(
        store, call_id: TypedId | str,
        call_version_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
    """Read provider attempts belonging to one exact logical call."""
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects "
            "WHERE object_type='provider_attempt_spec/v1' "
            "AND json_extract(metadata_json,'$.llm_call_ref.logical_id')=? "
            "AND json_extract(metadata_json,'$.llm_call_ref.version_id')=? "
            "ORDER BY rowid",
            (str(call_id), str(call_version_id))).fetchall())

def agent_action_rows_for_turn(
        store, turn_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
    """Read actions belonging to one exact agent-turn version."""
    if (not isinstance(turn_ref, VersionRef)
            or turn_ref.entity_type != "agent_turn/v1"):
        raise TypeError(
            "agent action lookup requires an exact agent_turn/v1 ref")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects WHERE object_type IN "
            "('agent_action/v2','agent_action/v3') "
            "AND json_extract(metadata_json,"
            "'$.agent_turn_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.agent_turn_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.agent_turn_ref.version_id')=? ORDER BY rowid",
            (turn_ref.entity_type, str(turn_ref.entity_id),
             str(turn_ref.version_id))).fetchall())

def settled_agent_action_rows_for_turn(
        store, turn_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
    """Read final action versions closed by their settlement event."""

    if (not isinstance(turn_ref, VersionRef)
            or turn_ref.entity_type != "agent_turn/v1"):
        raise TypeError(
            "settled action lookup requires an exact agent_turn/v1 ref")
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT action.* FROM objects action WHERE "
            "action.object_type IN ('agent_action/v2','agent_action/v3') AND "
            "json_extract(action.metadata_json,"
            "'$.agent_turn_ref.entity_type')=? AND "
            "json_extract(action.metadata_json,"
            "'$.agent_turn_ref.logical_id')=? AND "
            "json_extract(action.metadata_json,"
            "'$.agent_turn_ref.version_id')=? AND EXISTS ("
            "SELECT 1 FROM events settlement JOIN transactions tx ON "
            "tx.transaction_id=settlement.transaction_id WHERE "
            "settlement.transaction_id=action.transaction_id AND "
            "tx.status='committed' AND "
            "settlement.event_type='agent_action_settled/v1' AND "
            "json_extract(settlement.payload_json,"
            "'$.agent_action_id')=action.logical_id AND "
            "json_extract(settlement.payload_json,'$.settlement')="
            "json_extract(action.metadata_json,'$.state')) "
            "ORDER BY action.rowid",
            (turn_ref.entity_type, str(turn_ref.entity_id),
             str(turn_ref.version_id))).fetchall())

def completed_agent_context_compaction_rows_for_loop(
        store, loop_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
    """Read current-v3 completed context compactions for one agent loop."""
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects "
            "WHERE object_type='agent_context_compaction/v3' "
            "AND json_extract(metadata_json,"
            "'$.agent_loop_ref.logical_id')=? "
            "ORDER BY rowid",
            (str(loop_id),)).fetchall())

def agent_loop_rows_for_invocation(
        store, *, invocation_ref: VersionRef,
        operation_binding_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
    """Read loops for one exact invocation and operation binding."""
    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM objects WHERE object_type='agent_loop/v1' "
            "AND json_extract(metadata_json,"
            "'$.invocation_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.invocation_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.invocation_ref.version_id')=? "
            "AND json_extract(metadata_json,"
            "'$.operation_binding_ref.entity_type')=? "
            "AND json_extract(metadata_json,"
            "'$.operation_binding_ref.logical_id')=? "
            "AND json_extract(metadata_json,"
            "'$.operation_binding_ref.version_id')=? "
            "ORDER BY rowid",
            (invocation_ref.entity_type, str(invocation_ref.entity_id),
             str(invocation_ref.version_id),
             operation_binding_ref.entity_type,
             str(operation_binding_ref.entity_id),
             str(operation_binding_ref.version_id)),
        ).fetchall())

def provider_materialization_events_for_call(
        store, call_ref: VersionRef) -> tuple[EventEnvelope, ...]:
    """Read provider materialization facts for one exact call version."""
    if (not isinstance(call_ref, VersionRef)
            or call_ref.entity_type != "llm_call_spec/v2"):
        raise TypeError(
            "provider materialization lookup requires an exact "
            "llm_call_spec/v2 ref")
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE "
            "event_type='provider_payload_materialization_recorded/v1' "
            "AND json_extract(payload_json,'$.llm_call_ref.entity_type')=? "
            "AND json_extract(payload_json,'$.llm_call_ref.logical_id')=? "
            "AND json_extract(payload_json,'$.llm_call_ref.version_id')=? "
            "ORDER BY ordinal",
            (call_ref.entity_type, str(call_ref.entity_id),
             str(call_ref.version_id)),
        ).fetchall()
        return tuple(store._row_to_envelope(row) for row in rows)

def relation_rows_from_version(
        store, version_id: TypedId) -> tuple[sqlite3.Row, ...]:
    """Read only relations whose exact source version matches."""

    with store.connect() as db:
        return tuple(db.execute(
            "SELECT * FROM relations WHERE "
            "json_extract(source_json,'$.version_id')=? ORDER BY rowid",
            (str(version_id),)).fetchall())

def relation_rows_for_version(
        store, version_id: TypedId | str, *,
        relation_type: str | None = None,
        endpoint: str = "either") -> tuple[sqlite3.Row, ...]:
    """Read relations touching one exact source or target version."""
    if endpoint not in {"source", "target", "either"}:
        raise ValueError("relation endpoint must be source, target, or either")
    clauses = {
        "source": "json_extract(source_json,'$.version_id')=?",
        "target": "json_extract(target_json,'$.version_id')=?",
        "either": (
            "(json_extract(source_json,'$.version_id')=? OR "
            "json_extract(target_json,'$.version_id')=?)"),
    }
    parameters: list[str] = [str(version_id)]
    if endpoint == "either":
        parameters.append(str(version_id))
    where = clauses[endpoint]
    if relation_type is not None:
        where = "relation_type=? AND " + where
        parameters.insert(0, relation_type)
    with store.connect() as db:
        return tuple(db.execute(
            f"SELECT * FROM relations WHERE {where} ORDER BY rowid",
            tuple(parameters)).fetchall())

def object_rows(store) -> tuple[sqlite3.Row, ...]:
    with store.connect() as db:
        return tuple(db.execute("SELECT * FROM objects ORDER BY rowid"))

def relation_rows(store) -> tuple[sqlite3.Row, ...]:
    with store.connect() as db:
        return tuple(db.execute("SELECT * FROM relations ORDER BY rowid"))

def outbox_rows(store) -> tuple[sqlite3.Row, ...]:
    """Committed fact-batch notifications, written atomically with their facts."""
    with store.connect() as db:
        return tuple(db.execute("SELECT * FROM outbox ORDER BY rowid"))
