"""SQLite connection, schema, metadata, and writer-fence operations."""

from __future__ import annotations

import json
import sqlite3
from typing import Mapping

from ..event_store import RegistryConflict, RegistryCorruptError, StaleWriterError
from ..models import VersionRef
from .accounting import _current_task_recovery_manifest_row

def connect(store) -> sqlite3.Connection:
    if store.read_only:
        # A live monitor must see committed WAL growth without obtaining a
        # writer fence. SQLite read-only mode follows the WAL; immutable
        # mode would incorrectly freeze an active Registry at open time.
        uri = f"{store.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(
            uri, timeout=30, isolation_level=None, uri=True)
    else:
        connection = sqlite3.connect(
            store.path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if store.read_only:
        connection.execute("PRAGMA query_only=ON")
    else:
        with store._lock:
            if not store._journal_mode_ready:
                connection.execute("PRAGMA journal_mode=WAL")
                store._journal_mode_ready = True
        connection.execute("PRAGMA synchronous=FULL")
    return connection

def _initialize(store) -> None:
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS registry_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS published_model_call_baselines (
                baseline_id TEXT PRIMARY KEY,
                baseline_version_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL UNIQUE,
                destination_run_ref_json TEXT NOT NULL,
                source_run_ref_json TEXT NOT NULL,
                source_task_ref_json TEXT NOT NULL,
                source_checkpoint_ref_json TEXT NOT NULL,
                exact_model TEXT NOT NULL,
                published_call_count INTEGER NOT NULL
                    CHECK(published_call_count >= 0),
                idempotency_key TEXT NOT NULL UNIQUE,
                writer_epoch INTEGER NOT NULL,
                installed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                status TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                command_json TEXT NOT NULL,
                writer_epoch INTEGER NOT NULL, created_at TEXT NOT NULL,
                committed_at TEXT, UNIQUE(task_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS objects (
                logical_id TEXT NOT NULL, version_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL, size INTEGER NOT NULL,
                media_type TEXT NOT NULL, schema_ref TEXT NOT NULL,
                producer_invocation_id TEXT, storage_locator TEXT NOT NULL,
                metadata_json TEXT NOT NULL, transaction_id TEXT NOT NULL,
                published_event_id TEXT NOT NULL,
                UNIQUE(logical_id, version_id),
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE TABLE IF NOT EXISTS firing_publications (
                firing_version_id TEXT PRIMARY KEY,
                firing_logical_id TEXT NOT NULL UNIQUE,
                invocation_version_id TEXT NOT NULL UNIQUE,
                invocation_logical_id TEXT NOT NULL UNIQUE,
                net_version_id TEXT NOT NULL,
                operation_binding_version_id TEXT NOT NULL,
                admission_checkpoint_version_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PROVISIONAL','PUBLISHED')),
                opened_transaction_id TEXT NOT NULL,
                published_transaction_id TEXT,
                operation_result_version_id TEXT,
                workspace_lineage_id TEXT,
                workspace_revision_version_id TEXT,
                marking_checkpoint_version_id TEXT,
                CHECK(
                    (state='PROVISIONAL'
                     AND published_transaction_id IS NULL
                     AND operation_result_version_id IS NULL
                     AND workspace_lineage_id IS NULL
                     AND workspace_revision_version_id IS NULL
                     AND marking_checkpoint_version_id IS NULL)
                    OR
                    (state='PUBLISHED'
                     AND published_transaction_id IS NOT NULL
                     AND operation_result_version_id IS NOT NULL
                     AND ((workspace_lineage_id IS NULL
                           AND workspace_revision_version_id IS NULL)
                          OR
                          (workspace_lineage_id IS NOT NULL
                           AND workspace_revision_version_id IS NOT NULL))
                     AND marking_checkpoint_version_id IS NOT NULL)
                ),
                FOREIGN KEY(opened_transaction_id)
                    REFERENCES transactions(transaction_id),
                FOREIGN KEY(published_transaction_id)
                    REFERENCES transactions(transaction_id)
            );
            CREATE TABLE IF NOT EXISTS firing_temporary_members (
                firing_version_id TEXT NOT NULL,
                member_kind TEXT NOT NULL CHECK(member_kind IN (
                    'object','event','relation','transaction')),
                member_identity TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                PRIMARY KEY(firing_version_id,member_kind,member_identity),
                FOREIGN KEY(firing_version_id)
                    REFERENCES firing_publications(firing_version_id),
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE INDEX IF NOT EXISTS firing_temporary_member_identity
                ON firing_temporary_members(member_kind,member_identity);
            CREATE TABLE IF NOT EXISTS workspace_lineage_heads (
                workspace_lineage_id TEXT PRIMARY KEY,
                workspace_revision_version_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE TABLE IF NOT EXISTS stream_heads (
                stream_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                event_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS task_control_heads (
                task_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL,
                event_schema_version TEXT NOT NULL, criticality TEXT NOT NULL,
                task_id TEXT NOT NULL, branch_id TEXT NOT NULL,
                task_round_id TEXT, net_instance_id TEXT,
                stream_id TEXT NOT NULL, aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL, stream_sequence INTEGER NOT NULL,
                aggregate_version INTEGER NOT NULL, task_control_sequence INTEGER,
                idempotency_key TEXT NOT NULL, command_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL, causation_event_id TEXT,
                parent_event_ids_json TEXT NOT NULL,
                producer_principal TEXT NOT NULL, producer_invocation_id TEXT,
                transaction_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL, payload_schema_ref TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                writer_fencing_epoch INTEGER NOT NULL,
                previous_event_hash TEXT, event_hash TEXT,
                UNIQUE(stream_id, stream_sequence),
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY, relation_type TEXT NOT NULL,
                source_json TEXT NOT NULL, target_json TEXT NOT NULL,
                strength TEXT NOT NULL, metadata_json TEXT NOT NULL,
                transaction_id TEXT NOT NULL, published_event_id TEXT NOT NULL,
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE INDEX IF NOT EXISTS relations_source ON relations(source_json, relation_type);
            CREATE INDEX IF NOT EXISTS relations_target ON relations(target_json, relation_type);
            CREATE INDEX IF NOT EXISTS relations_transaction ON relations(transaction_id);
            CREATE INDEX IF NOT EXISTS relations_type_source_version ON relations(
                relation_type, json_extract(source_json,'$.version_id'));
            CREATE INDEX IF NOT EXISTS relations_source_version ON relations(
                json_extract(source_json,'$.version_id'));
            CREATE INDEX IF NOT EXISTS relations_type_source_identity ON relations(
                relation_type, json_extract(source_json,'$.entity_type'),
                json_extract(source_json,'$.entity_id'));
            CREATE INDEX IF NOT EXISTS relations_type_target_version ON relations(
                relation_type, json_extract(target_json,'$.version_id'));
            CREATE INDEX IF NOT EXISTS relations_target_version ON relations(
                json_extract(target_json,'$.version_id'));
            CREATE INDEX IF NOT EXISTS relations_type_target_identity ON relations(
                relation_type, json_extract(target_json,'$.entity_type'),
                json_extract(target_json,'$.entity_id'));
            CREATE INDEX IF NOT EXISTS objects_type_logical ON objects(object_type, logical_id);
            CREATE INDEX IF NOT EXISTS objects_producer_type ON objects(
                producer_invocation_id, object_type);
            CREATE INDEX IF NOT EXISTS objects_invocation_id_type ON objects(
                json_extract(metadata_json,'$.invocation_id'), object_type);
            CREATE INDEX IF NOT EXISTS objects_call_ref_type ON objects(
                json_extract(metadata_json,'$.llm_call_ref.logical_id'),
                json_extract(metadata_json,'$.llm_call_ref.version_id'),
                object_type);
            CREATE INDEX IF NOT EXISTS objects_call_id_type ON objects(
                json_extract(metadata_json,'$.llm_call_id'), object_type);
            CREATE INDEX IF NOT EXISTS objects_leaf_callsite_action ON objects(
                object_type,
                json_extract(metadata_json,
                '$.delegation_callsite.parent_action_id'),
                json_extract(metadata_json,
                '$.delegation_callsite.leaf_turn_sequence'));
            CREATE INDEX IF NOT EXISTS objects_agent_action_turn ON objects(
                object_type,
                json_extract(metadata_json,
                '$.agent_turn_ref.logical_id'),
                json_extract(metadata_json,
                '$.agent_turn_ref.version_id'));
            CREATE INDEX IF NOT EXISTS objects_agent_loop_invocation_binding ON objects(
                object_type,
                json_extract(metadata_json,
                '$.invocation_ref.version_id'),
                json_extract(metadata_json,
                '$.operation_binding_ref.version_id'));
            CREATE INDEX IF NOT EXISTS objects_execution_inventory_scope ON objects(
                object_type,
                json_extract(metadata_json,'$.descriptors.content_role'),
                json_extract(metadata_json,'$.task_ref.logical_id'),
                json_extract(metadata_json,'$.task_ref.version_id'),
                json_extract(metadata_json,
                '$.extensions."registry.execution_environment_inventory/v1".scope.task_round_ref.logical_id'),
                json_extract(metadata_json,
                '$.extensions."registry.execution_environment_inventory/v1".scope.task_round_ref.version_id'),
                json_extract(metadata_json,
                '$.extensions."registry.execution_environment_inventory/v1".scope.net_instance_ref.logical_id'),
                json_extract(metadata_json,
                '$.extensions."registry.execution_environment_inventory/v1".scope.net_instance_ref.version_id'));
            CREATE INDEX IF NOT EXISTS objects_transition_firing_task_net_transition ON objects(
                object_type,
                json_extract(metadata_json,'$.task_ref.logical_id'),
                json_extract(metadata_json,'$.task_ref.version_id'),
                json_extract(metadata_json,'$.net_instance_ref.logical_id'),
                json_extract(metadata_json,'$.net_instance_ref.version_id'),
                json_extract(metadata_json,'$.transition_id'));
            CREATE INDEX IF NOT EXISTS objects_transaction ON objects(transaction_id);
            CREATE INDEX IF NOT EXISTS events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS events_aggregate ON events(aggregate_id, event_type);
            CREATE INDEX IF NOT EXISTS events_stream_ordinal ON events(stream_id, ordinal);
            CREATE INDEX IF NOT EXISTS events_transaction ON events(transaction_id);
            CREATE INDEX IF NOT EXISTS events_idem ON events(idempotency_key);
            CREATE INDEX IF NOT EXISTS events_task_type_control ON events(
                task_id, event_type, task_control_sequence);
            CREATE INDEX IF NOT EXISTS events_task_provider_reservation_budget ON events(
                task_id, event_type,
                json_extract(payload_json,'$.budget_witness_ref.entity_type'),
                json_extract(payload_json,'$.budget_witness_ref.logical_id'),
                json_extract(payload_json,'$.budget_witness_ref.version_id'),
                transaction_id);
            CREATE INDEX IF NOT EXISTS events_net_type_control ON events(
                net_instance_id, event_type, task_control_sequence);
            CREATE INDEX IF NOT EXISTS events_producer_type ON events(
                producer_invocation_id, event_type);
            CREATE INDEX IF NOT EXISTS events_turn_call_version ON events(
                event_type,
                json_extract(payload_json,'$.llm_call_version_id'));
            CREATE INDEX IF NOT EXISTS events_turn_attempt_version ON events(
                event_type,
                json_extract(payload_json,'$.provider_attempt_version_id'));
            CREATE INDEX IF NOT EXISTS events_turn_response_version ON events(
                event_type,
                json_extract(payload_json,'$.response_version_id'));
            CREATE INDEX IF NOT EXISTS events_turn_loop_sequence ON events(
                event_type, json_extract(payload_json,'$.agent_loop_id'),
                json_extract(payload_json,'$.sequence'));
            CREATE INDEX IF NOT EXISTS events_turn_identity ON events(
                event_type,
                json_extract(payload_json,'$.agent_turn_version_id'));
            CREATE TABLE IF NOT EXISTS projection_manifests (
                projection_name TEXT PRIMARY KEY, manifest_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segment_seals (
                stream_id TEXT NOT NULL, through_sequence INTEGER NOT NULL,
                head_hash TEXT NOT NULL, seal_digest TEXT NOT NULL,
                PRIMARY KEY(stream_id, through_sequence)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                transaction_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                writer_epoch INTEGER NOT NULL, event_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            );
            CREATE TABLE IF NOT EXISTS evidence_resources (
                evidence_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                media_type TEXT NOT NULL,
                access_semantics TEXT NOT NULL,
                content BLOB NOT NULL,
                lineage_json TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                UNIQUE(task_id, source_kind, source_identity, content_sha256)
            );
            CREATE TABLE IF NOT EXISTS evidence_receipts (
                receipt_id TEXT PRIMARY KEY,
                evidence_id TEXT NOT NULL,
                receipt_json BLOB NOT NULL,
                projection_json BLOB,
                issued_at TEXT NOT NULL,
                FOREIGN KEY(evidence_id) REFERENCES evidence_resources(evidence_id)
            );
        """)
        db.execute("INSERT OR IGNORE INTO registry_meta(key,value) VALUES('writer_epoch','1')")

def writer_epoch(store) -> int:
    with store.connect() as db:
        row = db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()
        if row is None:
            raise RegistryCorruptError("writer epoch is missing")
        return int(row[0])

def get_or_create_meta(store, key: str, candidate: str) -> str:
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO registry_meta(key,value) VALUES(?,?)",
                   (key, candidate))
        value = str(db.execute(
            "SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()[0])
        db.commit()
    return value

def get_meta(store, key: str) -> str | None:
    with store.connect() as db:
        row = db.execute(
            "SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row is not None else None

def rotate_writer(store, *, expected_epoch: int) -> int:
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        from ..parent_bound import reject_bound_writer_at
        reject_bound_writer_at(db)
        current = int(db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
        if current != expected_epoch:
            db.rollback()
            raise StaleWriterError(f"writer epoch {expected_epoch} is stale; current={current}")
        replacement = current + 1
        db.execute("UPDATE registry_meta SET value=? WHERE key='writer_epoch'",
                   (str(replacement),))
        db.commit()
        return replacement

def acquire_writer(store) -> int:
    """Atomically acquire the sole production-writer epoch."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        from ..parent_bound import reject_bound_writer_at
        reject_bound_writer_at(db)
        current = int(db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
        replacement = current + 1
        db.execute("UPDATE registry_meta SET value=? WHERE key='writer_epoch'",
                   (str(replacement),))
        db.commit()
        return replacement

def current_task_recovery_manifest_row(store) -> sqlite3.Row | None:
    """Read the committed task manifest named by the exact current pointer."""
    with store.connect() as db:
        return _current_task_recovery_manifest_row(db)

def compare_and_set_task_recovery_manifest_pointer(
        store, *, expected_ref: VersionRef, replacement_ref: VersionRef,
        writer_epoch: int,
) -> None:
    """Select one published cap revision under the sole current writer."""

    if (not isinstance(expected_ref, VersionRef)
            or not isinstance(replacement_ref, VersionRef)
            or expected_ref.entity_type != "task_recovery_manifest/v1"
            or replacement_ref.entity_type != "task_recovery_manifest/v1"
            or expected_ref.entity_id != replacement_ref.entity_id
            or isinstance(writer_epoch, bool)
            or not isinstance(writer_epoch, int)
            or writer_epoch < 1):
        raise TypeError(
            "recovery-manifest pointer replacement requires exact refs")
    with store._lock, store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='task_id'",
        ).fetchone()
        epoch_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'",
        ).fetchone()
        pointer_row = db.execute(
            "SELECT value FROM registry_meta "
            "WHERE key='task_recovery_manifest_version_id'",
        ).fetchone()
        replacement = db.execute(
            "SELECT o.logical_id,o.object_type,o.metadata_json,"
            "t.status,t.writer_epoch FROM objects o "
            "JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE o.version_id=?",
            (str(replacement_ref.version_id),),
        ).fetchone()
        try:
            replacement_metadata = (
                json.loads(str(replacement["metadata_json"]))
                if replacement is not None else None)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            db.rollback()
            raise RegistryConflict(
                "replacement recovery manifest is malformed") from exc
        if (task_row is None or epoch_row is None or pointer_row is None
                or str(task_row["value"])
                != str(expected_ref.entity_id)
                or int(epoch_row["value"]) != writer_epoch
                or str(pointer_row["value"])
                != str(expected_ref.version_id)
                or replacement is None
                or str(replacement["logical_id"])
                != str(replacement_ref.entity_id)
                or replacement["object_type"]
                != "task_recovery_manifest/v1"
                or replacement["status"] != "committed"
                or int(replacement["writer_epoch"]) != writer_epoch
                or not isinstance(replacement_metadata, Mapping)
                or replacement_metadata.get("task_id")
                != str(replacement_ref.entity_id)
                or replacement_metadata.get("writer_fencing_epoch")
                != writer_epoch):
            db.rollback()
            raise RegistryConflict(
                "recovery-manifest pointer replacement lost authority")
        changed = db.execute(
            "UPDATE registry_meta SET value=? "
            "WHERE key='task_recovery_manifest_version_id' AND value=?",
            (str(replacement_ref.version_id),
             str(expected_ref.version_id)),
        ).rowcount
        if changed != 1:
            db.rollback()
            raise RegistryConflict(
                "recovery-manifest pointer changed concurrently")
        db.commit()
