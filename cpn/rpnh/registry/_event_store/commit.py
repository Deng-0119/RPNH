"""Atomic EventStore commit coordination over one writer snapshot."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..identities import TypedId, new_id
from ..models import (
    EventEnvelope,
    ObjectRef,
    PendingEvent,
    PreparedObject,
    TypedRelation,
    VersionRef,
)
from .accounting import _CANONICAL_EVENT_SQL

if TYPE_CHECKING:
    from ..event_store import EventStore


def publish_batch(event_store: "EventStore", *, task_id: TypedId, branch_id: str,
                  task_round_id: TypedId | None,
                  net_instance_id: TypedId | None,
                  transaction_id: TypedId, idempotency_key: str,
                  writer_epoch: int, objects: Sequence[PreparedObject],
                  events: Sequence[PendingEvent],
                  relations: Sequence[TypedRelation],
                  expected_heads: Mapping[str, int],
                  expected_snapshot_predecessors: Mapping[
                      str, Mapping[str, Any] | None] | None = None,
                  expected_dependency_root_predecessor: Mapping[
                      str, str | None] | None = None,
                  firing_publications: Sequence[Mapping[str, object]] = (),
                  workspace_head_advances: Sequence[Mapping[str, object]] = (),
                  expected_registry_ordinal: int | None = None,
                  ) -> tuple[EventEnvelope, ...]:
    """Validate and atomically append one complete Registry transaction."""
    from ..event_store import (
        RegistryConflict,
        RegistryCorruptError,
        StaleWriterError,
        _closing_firing_owner_for_system_relation,
        _now,
        _ref_json,
        _transaction_command_material,
        fact_event_envelope,
        prepared_object_envelope,
    )
    if expected_registry_ordinal is not None and (
            type(expected_registry_ordinal) is not int or expected_registry_ordinal < 0):
        raise TypeError("expected Registry ordinal must be nonnegative")
    if transaction_id.kind != "transaction" or task_id.kind != "task":
        raise TypeError("publish_batch requires typed task and transaction ids")
    if not events:
        raise ValueError("a committed transaction must publish at least one fact event")
    snapshot_predecessors = dict(expected_snapshot_predecessors or {})
    dependency_root_predecessor = (
        dict(expected_dependency_root_predecessor)
        if expected_dependency_root_predecessor is not None else None)
    publication_commands = tuple(dict(item) for item in firing_publications)
    workspace_head_commands = tuple(
        dict(item) for item in workspace_head_advances)
    # HISTORICAL_INERT: no current caller can supply fault-mechanical
    # settlement publication commands.  The retained validator below is
    # unreachable until it is removed with the historical schemas.
    historical_mechanical_publication_commands: tuple[
        Mapping[str, object], ...] = ()
    business_settlements = tuple(
        event for event in events
        if event.event_type == "transition_firing_settled/v1")
    removed_business_terminal_events = tuple(
        event for event in events
        if event.event_type in {
            "invocation_settled/v1",
            "operation_execution_finished/v1",
        })
    if (len(publication_commands) > 1
            or len(business_settlements) > 1
            or bool(publication_commands) != bool(business_settlements)
            or (publication_commands
                and len(business_settlements) != 1)
            or (publication_commands
                and removed_business_terminal_events)):
        raise RegistryConflict(
            "one Registry transaction may settle and publish only one firing")
    if publication_commands and workspace_head_commands:
        raise RegistryConflict(
            "workspace head advances cannot share a firing publication transaction")
    required_publication_fields = {
        "firing_version_id", "invocation_version_id",
        "admission_checkpoint_version_id",
        "expected_parent_checkpoint_version_id",
        "operation_result_version_id", "workspace_lineage_id",
        "expected_workspace_head_version_id",
        "workspace_revision_version_id",
        "marking_checkpoint_version_id",
        "marking_net_version_id",
    }
    workspace_publication_fields = {
        "workspace_lineage_id", "expected_workspace_head_version_id",
        "workspace_revision_version_id",
    }
    if (any(
            set(item) != required_publication_fields
            or any(not isinstance(value, str) or not value
                   for name, value in item.items()
                   if name not in workspace_publication_fields)
            or not (
                all(item[name] is None
                    for name in workspace_publication_fields)
                or all(isinstance(item[name], str) and bool(item[name])
                       for name in workspace_publication_fields))
            for item in publication_commands)
            or len({item["firing_version_id"]
                    for item in publication_commands})
            != len(publication_commands)):
        raise TypeError("firing publication command is malformed")
    required_workspace_head_fields = {
        "workspace_lineage_id", "expected_workspace_head_version_id",
        "workspace_revision_version_id", "authority_entity_type",
        "authority_logical_id", "authority_version_id",
    }
    if (any(
            set(item) != required_workspace_head_fields
            or any(not isinstance(value, str) or not value
                   for value in item.values())
            for item in workspace_head_commands)
            or len({item["workspace_lineage_id"]
                    for item in workspace_head_commands})
            != len(workspace_head_commands)):
        raise TypeError("workspace head advance command is malformed")
    required_mechanical_publication_fields = {
        "settlement_version_id", "firing_version_id",
        "sidecar_action_version_id", "operation_fault_version_id",
        "root_fault_chain_version_id", "fault_route_binding_version_id",
        "emitted_token_version_ids", "marking_delta_version_id",
        "successor_checkpoint_version_id",
    }
    if any(
            set(item) != required_mechanical_publication_fields
            or any(not isinstance(item[name], str) or not item[name]
                   for name in required_mechanical_publication_fields
                   if name != "emitted_token_version_ids")
            or not isinstance(item["emitted_token_version_ids"], tuple)
            or not item["emitted_token_version_ids"]
            or any(not isinstance(value, str) or not value
                   for value in item["emitted_token_version_ids"])
            or len(set(item["emitted_token_version_ids"]))
            != len(item["emitted_token_version_ids"])
            for item in historical_mechanical_publication_commands):
        raise TypeError("mechanical settlement publication command is malformed")
    prepared_by_version = {str(item.version_id): item for item in objects}
    for command in workspace_head_commands:
        successors = tuple(
            item for item in objects
            if (item.object_type == "workspace_revision/v1"
                and str(item.logical_id) == command["workspace_lineage_id"]
                and str(item.version_id)
                == command["workspace_revision_version_id"]))
        successor = successors[0] if len(successors) == 1 else None
        parent = (successor.metadata.get("parent_revision_ref")
                  if successor is not None else None)
        successor_ref = (successor.metadata.get("workspace_revision_ref")
                         if successor is not None else None)
        if (successor is None
                or not isinstance(parent, Mapping)
                or parent != {
                    "entity_type": "workspace_revision/v1",
                    "logical_id": command["workspace_lineage_id"],
                    "version_id": command[
                        "expected_workspace_head_version_id"],
                }
                or successor.metadata.get("workspace_lineage_id")
                != command["workspace_lineage_id"]
                or successor_ref != {
                    "entity_type": "workspace_revision/v1",
                    "logical_id": command["workspace_lineage_id"],
                    "version_id": command[
                        "workspace_revision_version_id"],
                }
                or not any(
                    relation.system_owned
                    and relation.producer_invocation_id is None
                    and relation.strength == "strong"
                    and isinstance(relation.source, VersionRef)
                    and isinstance(relation.target, VersionRef)
                    and relation.source.entity_type == "workspace_revision/v1"
                    and str(relation.source.entity_id)
                    == command["workspace_lineage_id"]
                    and str(relation.source.version_id)
                    == command["workspace_revision_version_id"]
                    and relation.target.entity_type
                    == command["authority_entity_type"]
                    and str(relation.target.entity_id)
                    == command["authority_logical_id"]
                    and str(relation.target.version_id)
                    == command["authority_version_id"]
                    for relation in relations)):
            raise RegistryConflict(
                "workspace head advance lacks its exact successor authority")
    for command in historical_mechanical_publication_commands:
        settlement = prepared_by_version.get(command["settlement_version_id"])
        delta = prepared_by_version.get(command["marking_delta_version_id"])
        checkpoint = prepared_by_version.get(
            command["successor_checkpoint_version_id"])
        emitted_tokens = tuple(
            prepared_by_version.get(version_id)
            for version_id in command["emitted_token_version_ids"])
        metadata = settlement.metadata if settlement is not None else {}
        delta_metadata = delta.metadata if delta is not None else {}
        checkpoint_metadata = (
            checkpoint.metadata if checkpoint is not None else {})
        predecessor = checkpoint_metadata.get("previous_checkpoint_ref")
        net_ref = checkpoint_metadata.get("net_instance_ref")
        marking_stream = (
            f"marking:{net_ref.get('logical_id')}"
            if isinstance(net_ref, Mapping) else "")
        prepared_token_versions = {
            str(item.version_id) for item in objects
            if item.object_type == "petri_token/v1"}
        deposited_versions = {
            str(ref.get("version_id", ""))
            for ref in delta_metadata.get("deposited_refs", [])
            if isinstance(ref, Mapping)}
        settlement_relation = tuple(
            relation for relation in relations
            if relation.system_owned
            and relation.producer_invocation_id is None
            and relation.relation_type == "derived_from"
            and str(getattr(relation.source, "version_id", ""))
            == command["settlement_version_id"]
            and str(getattr(relation.target, "version_id", ""))
            == command["firing_version_id"]
        )
        delta_relation = tuple(
            relation for relation in relations
            if relation.system_owned
            and relation.producer_invocation_id is None
            and relation.relation_type == "derived_from"
            and str(getattr(relation.source, "version_id", ""))
            == command["marking_delta_version_id"]
            and str(getattr(relation.target, "version_id", ""))
            == str((predecessor or {}).get("version_id", "")))
        checkpoint_relation = tuple(
            relation for relation in relations
            if relation.system_owned
            and relation.producer_invocation_id is None
            and relation.relation_type == "derived_from"
            and str(getattr(relation.source, "version_id", ""))
            == command["successor_checkpoint_version_id"]
            and str(getattr(relation.target, "version_id", ""))
            == command["marking_delta_version_id"])
        checkpoint_events = tuple(
            event for event in events
            if event.event_type == "marking_checkpoint_committed/v1"
            and event.payload.get("checkpoint_ref")
            == checkpoint_metadata.get("marking_checkpoint_ref"))
        old_mechanical_events = tuple(
            event for event in events
            if event.event_type in {
                "fault_mechanical_firing_admitted/v1",
                "fault_mechanical_firing_completed/v1",
                "fault_mechanical_firing_settled/v1",
            })
        if (settlement is None
                or settlement.object_type != "fault_mechanical_settlement/v1"
                or settlement.producer_invocation_id is not None
                or delta is None or delta.object_type != "marking_delta/v1"
                or delta.producer_invocation_id is not None
                or checkpoint is None
                or checkpoint.object_type != "marking_checkpoint/v1"
                or checkpoint.producer_invocation_id is not None
                or any(token is None
                       or token.object_type != "petri_token/v1"
                       or token.producer_invocation_id is not None
                       for token in emitted_tokens)
                or metadata.get(
                    "fault_mechanical_settlement_ref", {}).get(
                        "version_id") != command["settlement_version_id"]
                or metadata.get("transition_firing_ref", {}).get("version_id")
                != command["firing_version_id"]
                or metadata.get("fault_sidecar_action_ref", {}).get("version_id")
                != command["sidecar_action_version_id"]
                or metadata.get("operation_fault_ref", {}).get("version_id")
                != command["operation_fault_version_id"]
                or metadata.get("root_fault_chain_ref", {}).get("version_id")
                != command["root_fault_chain_version_id"]
                or metadata.get("fault_route_binding_ref", {}).get("version_id")
                != command["fault_route_binding_version_id"]
                or metadata.get("marking_delta_ref", {}).get("version_id")
                != command["marking_delta_version_id"]
                or metadata.get("successor_checkpoint_ref", {}).get("version_id")
                != command["successor_checkpoint_version_id"]
                or tuple(ref.get("version_id") for ref in metadata.get(
                    "emitted_token_refs", []) if isinstance(ref, Mapping))
                != command["emitted_token_version_ids"]
                or metadata.get("transaction_ref", {}).get("logical_id")
                != str(transaction_id)
                or metadata.get("writer_fencing_epoch") != writer_epoch
                or delta_metadata.get("marking_delta_ref", {}).get(
                    "version_id") != command["marking_delta_version_id"]
                or delta_metadata.get("net_instance_ref") != net_ref
                or delta_metadata.get("phase") != "settlement"
                or delta_metadata.get("transition_firing_refs") != []
                or delta_metadata.get("mechanical_firing_refs") != [
                    metadata.get("transition_firing_ref")]
                or delta_metadata.get("operation_binding_refs") != []
                or delta_metadata.get("operation_fault_refs") != [
                    metadata.get("operation_fault_ref")]
                or not delta_metadata.get("consumed_refs")
                or not deposited_versions
                or deposited_versions != prepared_token_versions
                or checkpoint_metadata.get("settlement_delta_ref", {}).get(
                    "version_id") != command["marking_delta_version_id"]
                or checkpoint_metadata.get("marking_checkpoint_ref", {}).get(
                    "version_id")
                != command["successor_checkpoint_version_id"]
                or checkpoint_metadata.get("settled") is not True
                or checkpoint_metadata.get("transition_firing_refs") != []
                or checkpoint_metadata.get("mechanical_firing_refs") != [
                    metadata.get("transition_firing_ref")]
                or not checkpoint_metadata.get("token_refs")
                or not marking_stream
                or snapshot_predecessors.get(marking_stream) != predecessor
                or len(settlement_relation) != 1
                or len(delta_relation) != 1
                or len(checkpoint_relation) != 1
                or len(checkpoint_events) != 1
                or checkpoint_events[0].payload.get(
                    "previous_checkpoint_ref") != predecessor
                or checkpoint_events[0].payload.get(
                    "settlement_delta_ref")
                != checkpoint_metadata.get("settlement_delta_ref")
                or checkpoint_events[0].payload.get(
                    "mechanical_firing_refs")
                != checkpoint_metadata.get("mechanical_firing_refs")
                or checkpoint_events[0].producer_invocation_id is not None
                or old_mechanical_events):
            raise RegistryConflict(
                "mechanical settlement lacks exact generic publication evidence")
    expected_checkpoint_workspaces: dict[
        str, set[tuple[str, str, str]]] = {}
    for command in publication_commands:
        expected = expected_checkpoint_workspaces.setdefault(
            command["marking_checkpoint_version_id"], set())
        if command["workspace_lineage_id"] is not None:
            expected.add((
                "workspace_revision/v1",
                command["workspace_lineage_id"],
                command["workspace_revision_version_id"],
            ))
    if (dependency_root_predecessor is not None
            and (set(dependency_root_predecessor) != {
                "logical_id", "expected_version_id"}
                 or not isinstance(
                     dependency_root_predecessor["logical_id"], str)
                 or not dependency_root_predecessor["logical_id"]
                 or (dependency_root_predecessor["expected_version_id"]
                     is not None
                     and not isinstance(
                         dependency_root_predecessor[
                             "expected_version_id"], str)))):
        raise TypeError("dependency root predecessor condition is malformed")
    for item in objects:
        definition = event_store.catalog.require(item.object_type, category="object")
        if item.schema_ref != definition.schema_ref:
            raise RegistryConflict(
                f"schema ref does not match registered object type {item.object_type!r}")
        event_store.catalog.validate_schema_ref(
            "registry_v1/object_envelope/v1", prepared_object_envelope(item))
    for pending in events:
        definition = event_store.catalog.require(
            pending.event_type, category="event", criticality=pending.criticality)
        if pending.payload_schema_ref != definition.schema_ref:
            raise RegistryConflict(
                f"payload schema ref does not match {pending.event_type!r}")
        event_store.catalog.validate_event_payload(
            pending.event_type, criticality=pending.criticality,
            payload=pending.payload)
    if any(
            not isinstance(relation.system_owned, bool)
            or ((relation.producer_invocation_id is None)
                == (not relation.system_owned))
            or (relation.producer_invocation_id is not None
                and relation.producer_invocation_id.kind != "invocation")
            for relation in relations):
        raise TypeError(
            "relation requires one exact invocation or system owner")
    command_material = _transaction_command_material(
        branch_id=branch_id,
        task_round_id=(str(task_round_id) if task_round_id else None),
        net_instance_id=(str(net_instance_id) if net_instance_id else None),
        objects=tuple({
            "object_type": item.object_type,
            "logical_id": str(item.logical_id),
            "version_id": str(item.version_id),
            "metadata": item.metadata,
            "producer_invocation_id": (
                str(item.producer_invocation_id)
                if item.producer_invocation_id is not None else None),
        } for item in objects),
        events=tuple({
            "event_type": item.event_type,
            "stream_id": item.stream_id,
            "aggregate_id": item.aggregate_id,
            "aggregate_type": item.aggregate_type,
            "criticality": item.criticality,
            "payload": item.payload,
            "payload_schema_ref": item.payload_schema_ref,
            "task_control": item.task_control,
            "producer_principal": item.producer_principal,
            "producer_invocation_id": (
                str(item.producer_invocation_id)
                if item.producer_invocation_id is not None else None),
        } for item in events),
        relations=tuple({
            "relation_id": str(item.relation_id),
            "relation_type": item.relation_type,
            "source": _ref_json(item.source),
            "target": _ref_json(item.target),
            "strength": item.strength,
            "metadata": item.metadata,
            "producer_invocation_id": (
                str(item.producer_invocation_id)
                if item.producer_invocation_id is not None else None),
            "system_owned": item.system_owned,
        } for item in relations),
        expected_snapshot_predecessors=snapshot_predecessors,
        expected_dependency_root_predecessor=(
            dependency_root_predecessor),
        workspace_head_advances=workspace_head_commands,
    )
    with event_store._lock, event_store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current_epoch = int(db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
        if writer_epoch != current_epoch:
            db.rollback()
            raise StaleWriterError(
                f"writer epoch {writer_epoch} is stale; current={current_epoch}")
        # No workspace publication means this firing leaves that separate
        # checkpoint authority unchanged. Reclose the carry-forward from
        # the exact pair-CAS predecessor in this writer snapshot; never
        # accept the successor checkpoint itself as the source.
        for command in publication_commands:
            if command["workspace_lineage_id"] is not None:
                continue
            predecessor = db.execute(
                "SELECT metadata_json FROM objects WHERE version_id=? "
                "AND object_type='marking_checkpoint/v1'",
                (command["expected_parent_checkpoint_version_id"],),
            ).fetchone()
            if predecessor is None:
                db.rollback()
                raise RegistryConflict(
                    "firing workspace carry-forward lacks its exact predecessor")
            predecessor_metadata = json.loads(
                str(predecessor["metadata_json"]))
            predecessor_workspaces = predecessor_metadata.get(
                "workspace_revision_refs")
            if (not isinstance(predecessor_workspaces, list)
                    or any(not isinstance(value, Mapping)
                           for value in predecessor_workspaces)):
                db.rollback()
                raise RegistryConflict(
                    "firing predecessor workspace authority is malformed")
            expected = expected_checkpoint_workspaces[
                command["marking_checkpoint_version_id"]]
            expected.update((
                str(value.get("entity_type", "")),
                str(value.get("logical_id", "")),
                str(value.get("version_id", "")),
            ) for value in predecessor_workspaces)
            if len(expected) != len(predecessor_workspaces):
                db.rollback()
                raise RegistryConflict(
                    "firing predecessor repeats workspace authority")
        existing = db.execute(
            "SELECT transaction_id,status,command_json FROM transactions WHERE task_id=? AND idempotency_key=?",
            (str(task_id), idempotency_key)).fetchone()
        from .preserved_plan_commit import validate_preserved_plan_commit
        validate_preserved_plan_commit(
            event_store, db, task_id=task_id, branch_id=branch_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            transaction_id=transaction_id, idempotency_key=idempotency_key,
            objects=objects, events=events, relations=relations,
            command_material=command_material, existing=existing,
            extra_commands=bool(snapshot_predecessors or dependency_root_predecessor
                or publication_commands or workspace_head_commands))
        from ..parent_child import validate_parent_child_commit
        validate_parent_child_commit(event_store, db,
            task_id=task_id, branch_id=branch_id, task_round_id=task_round_id,
            net_instance_id=net_instance_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, writer_epoch=writer_epoch,
            objects=objects, events=events, relations=relations, existing=existing,
            extra_commands=bool(snapshot_predecessors or dependency_root_predecessor
                or publication_commands or workspace_head_commands))
        from ..public_materials import validate_public_material_commit
        validate_public_material_commit(event_store, db, task_id=task_id, branch_id=branch_id,
            objects=objects, events=events, relations=relations, existing=existing,
            idempotency_key=idempotency_key, transaction_id=transaction_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            extra_commands=bool(snapshot_predecessors or dependency_root_predecessor
                or publication_commands or workspace_head_commands))
        if existing is not None:
            if existing["status"] not in {"committed", "aborted"}:
                db.rollback()
                raise RegistryConflict("idempotency key belongs to an incomplete transaction")
            if json.loads(existing["command_json"]) != command_material:
                db.rollback()
                raise RegistryConflict("idempotency key was reused for a different command")
            replay_roots = {
                str(row["firing_version_id"])
                for row in db.execute(
                    "SELECT firing_version_id FROM firing_publications "
                    "WHERE published_transaction_id=?",
                    (str(existing["transaction_id"]),),
                ).fetchall()
            }
            commanded_roots = {
                command["firing_version_id"]
                for command in publication_commands
            }
            if replay_roots != commanded_roots:
                db.rollback()
                raise RegistryConflict(
                    "firing publication replay names different roots")
            for command in publication_commands:
                root = db.execute(
                    "SELECT * FROM firing_publications "
                    "WHERE firing_version_id=?",
                    (command["firing_version_id"],),
                ).fetchone()
                checkpoint = db.execute(
                    "SELECT metadata_json FROM objects WHERE version_id=? "
                    "AND object_type='marking_checkpoint/v1'",
                    (command["marking_checkpoint_version_id"],),
                ).fetchone()
                has_workspace = command["workspace_lineage_id"] is not None
                revision = (db.execute(
                    "SELECT metadata_json FROM objects WHERE version_id=? "
                    "AND object_type='workspace_revision/v1'",
                    (command["workspace_revision_version_id"],),
                ).fetchone() if has_workspace else None)
                checkpoint_metadata = (
                    json.loads(str(checkpoint["metadata_json"]))
                    if checkpoint is not None else {})
                revision_metadata = (json.loads(str(
                    revision["metadata_json"])) if revision is not None else {})
                replay_workspace_refs = checkpoint_metadata.get(
                    "workspace_revision_refs")
                replay_workspaces = (
                    {
                        (str(value.get("entity_type", "")),
                         str(value.get("logical_id", "")),
                         str(value.get("version_id", "")))
                        for value in replay_workspace_refs
                        if isinstance(value, Mapping)
                    }
                    if isinstance(replay_workspace_refs, list) else set())
                if (root is None or root["state"] != "PUBLISHED"
                        or str(root["published_transaction_id"])
                        != str(existing["transaction_id"])
                        or str(root["invocation_version_id"])
                        != command["invocation_version_id"]
                        or str(root["admission_checkpoint_version_id"])
                        != command["admission_checkpoint_version_id"]
                        or str(root["operation_result_version_id"])
                        != command["operation_result_version_id"]
                        or (None if root["workspace_lineage_id"] is None
                            else str(root["workspace_lineage_id"]))
                        != command["workspace_lineage_id"]
                        or (None if root["workspace_revision_version_id"] is None
                            else str(root["workspace_revision_version_id"]))
                        != command["workspace_revision_version_id"]
                        or str(root["marking_checkpoint_version_id"])
                        != command["marking_checkpoint_version_id"]
                        or checkpoint_metadata.get(
                            "previous_checkpoint_ref", {}).get(
                                "version_id")
                        != command[
                            "expected_parent_checkpoint_version_id"]
                        or (has_workspace and (
                            revision_metadata.get(
                                "parent_revision_ref", {}).get("version_id")
                            != command[
                                "expected_workspace_head_version_id"]))
                        or checkpoint_metadata.get(
                            "net_instance_ref", {}).get("version_id")
                        != command["marking_net_version_id"]
                        or len(replay_workspace_refs or ())
                        != len(expected_checkpoint_workspaces[
                            command["marking_checkpoint_version_id"]])
                        or replay_workspaces
                        != expected_checkpoint_workspaces[
                            command["marking_checkpoint_version_id"]]):
                    db.rollback()
                    raise RegistryConflict(
                        "firing publication replay differs from exact settlement")
            for command in workspace_head_commands:
                head = db.execute(
                    "SELECT workspace_revision_version_id,transaction_id "
                    "FROM workspace_lineage_heads WHERE workspace_lineage_id=?",
                    (command["workspace_lineage_id"],),
                ).fetchone()
                if (head is None
                        or str(head["workspace_revision_version_id"])
                        != command["workspace_revision_version_id"]
                        or str(head["transaction_id"])
                        != str(existing["transaction_id"])):
                    db.rollback()
                    raise RegistryConflict(
                        "workspace head advance replay differs from committed CAS")
            requested_relation_owners = {
                str(relation.relation_id): {
                    "producer_invocation_id": (
                        str(relation.producer_invocation_id)
                        if relation.producer_invocation_id is not None
                        else None),
                    "system_owned": relation.system_owned,
                }
                for relation in relations
            }
            replay_relation_owners = {
                str(event.payload.get("relation_id")): {
                    "producer_invocation_id": (
                        str(event.producer_invocation_id)
                        if event.producer_invocation_id is not None else None),
                    "system_owned": event.producer_invocation_id is None,
                }
                for event in (
                    event_store._row_to_envelope(row) for row in db.execute(
                        "SELECT * FROM events WHERE transaction_id=? "
                        "AND event_type='relation_published/v1'",
                        (existing["transaction_id"],),
                    ).fetchall())
            }
            if replay_relation_owners != requested_relation_owners:
                db.rollback()
                raise RegistryConflict(
                    "relation publication replay changed firing ownership")
            rows = db.execute(
                "SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
                (existing["transaction_id"],)).fetchall()
            db.rollback()
            return tuple(event_store._row_to_envelope(row) for row in rows)

        # Compare under the same BEGIN IMMEDIATE snapshot as the ensuing append.
        # The normal exact command replay above must remain usable after head
        # advancement; a new command cannot pass by racing an earlier precheck.
        if expected_registry_ordinal is not None:
            current_ordinal = int(db.execute("SELECT coalesce(max(ordinal),0) FROM events").fetchone()[0])
            if current_ordinal != expected_registry_ordinal:
                db.rollback()
                raise RegistryConflict("expected Registry head changed")

        for command in workspace_head_commands:
            authority = db.execute(
                "SELECT 1 FROM objects WHERE object_type=? AND logical_id=? "
                "AND version_id=?",
                (command["authority_entity_type"],
                 command["authority_logical_id"],
                 command["authority_version_id"]),
            ).fetchone()
            if authority is None:
                db.rollback()
                raise RegistryConflict(
                    "workspace head advance authority is not an immutable Registry object")

        for command in historical_mechanical_publication_commands:
            settlement = prepared_by_version[
                command["settlement_version_id"]]
            delta = prepared_by_version[command["marking_delta_version_id"]]
            checkpoint = prepared_by_version[
                command["successor_checkpoint_version_id"]]
            settlement_metadata = dict(settlement.metadata)
            delta_metadata = dict(delta.metadata)
            checkpoint_metadata = dict(checkpoint.metadata)
            predecessor_ref = checkpoint_metadata[
                "previous_checkpoint_ref"]
            net_ref = checkpoint_metadata["net_instance_ref"]

            def canonical_object_metadata(
                    version_id: str, object_type: str,
            ) -> Mapping[str, Any] | None:
                row = db.execute(
                    "SELECT metadata_json FROM objects WHERE version_id=? "
                    "AND object_type=?",
                    (version_id, object_type),
                ).fetchone()
                if row is None:
                    return None
                open_owner = db.execute(
                    "SELECT 1 FROM firing_temporary_members member "
                    "JOIN firing_publications publication ON "
                    "publication.firing_version_id="
                    "member.firing_version_id "
                    "WHERE member.member_kind='object' "
                    "AND member.member_identity=? "
                    "AND publication.state!='PUBLISHED' LIMIT 1",
                    (version_id,),
                ).fetchone()
                return (None if open_owner is not None else
                        json.loads(str(row["metadata_json"])))

            firing = canonical_object_metadata(
                command["firing_version_id"], "transition_firing/v2")
            action = canonical_object_metadata(
                command["sidecar_action_version_id"],
                "fault_sidecar_action/v1")
            fault = canonical_object_metadata(
                command["operation_fault_version_id"],
                "operation_fault/v1")
            route = canonical_object_metadata(
                command["fault_route_binding_version_id"],
                "fault_route_binding/v1")
            predecessor = canonical_object_metadata(
                str(predecessor_ref.get("version_id", "")),
                "marking_checkpoint/v1")
            current_row = db.execute(
                "SELECT e.payload_json FROM events e WHERE e.task_id=? "
                "AND e.net_instance_id=? AND e.event_type="
                "'marking_checkpoint_committed/v1' AND "
                f"{_CANONICAL_EVENT_SQL} "
                "ORDER BY e.task_control_sequence DESC LIMIT 1",
                (str(task_id), str(net_ref.get("logical_id", ""))),
            ).fetchone()
            current_payload = (
                json.loads(str(current_row["payload_json"]))
                if current_row is not None else None)
            duplicate = db.execute(
                "SELECT 1 FROM objects WHERE object_type="
                "'fault_mechanical_settlement/v1' AND "
                "json_extract(metadata_json,'$.transition_firing_ref."
                "version_id')=? LIMIT 1",
                (command["firing_version_id"],),
            ).fetchone()
            prepared_tokens = {
                str(item.version_id): item
                for item in objects
                if item.object_type == "petri_token/v1"}
            deposited_refs = tuple(
                value for value in delta_metadata["deposited_refs"]
                if isinstance(value, Mapping))
            deposited_versions = {
                str(value.get("version_id", ""))
                for value in deposited_refs}
            consumed_refs = tuple(delta_metadata["consumed_refs"])
            predecessor_token_refs = (
                predecessor.get("token_refs", [])
                if isinstance(predecessor, Mapping) else [])
            predecessor_versions = {
                str(value.get("version_id", ""))
                for value in predecessor_token_refs
                if isinstance(value, Mapping)}
            successor_refs = tuple(checkpoint_metadata["token_refs"])
            successor_versions = {
                str(value.get("version_id", ""))
                for value in successor_refs
                if isinstance(value, Mapping)}
            consumed_versions = {
                str(value.get("version_id", ""))
                for value in consumed_refs
                if isinstance(value, Mapping)}
            emitted_versions = set(
                command["emitted_token_version_ids"])
            claimed_refs = (
                firing.get("claimed_token_occurrence_refs", [])
                if isinstance(firing, Mapping) else [])
            if (firing is None or action is None or fault is None
                    or route is None or predecessor is None
                    or current_payload is None
                    or current_payload.get("checkpoint_ref")
                    != predecessor_ref
                    or duplicate is not None
                    or firing.get("transition_firing_ref")
                    != settlement_metadata["transition_firing_ref"]
                    or firing.get("occurrence_ref")
                    != settlement_metadata["fault_sidecar_action_ref"]
                    or firing.get("net_instance_ref") != net_ref
                    or firing.get("invocation_ref") is not None
                    or firing.get("status") != "open"
                    or firing.get("settlement_event_ref") is not None
                    or action.get("fault_sidecar_action_ref")
                    != settlement_metadata["fault_sidecar_action_ref"]
                    or action.get("operation_fault_ref")
                    != settlement_metadata["operation_fault_ref"]
                    or action.get("root_fault_chain_ref")
                    != settlement_metadata["root_fault_chain_ref"]
                    or fault.get("operation_fault_ref")
                    != settlement_metadata["operation_fault_ref"]
                    or fault.get("root_fault_chain_ref")
                    != settlement_metadata["root_fault_chain_ref"]
                    or route.get("fault_route_binding_ref")
                    != settlement_metadata["fault_route_binding_ref"]
                    or delta_metadata["consumed_refs"] != claimed_refs
                    or consumed_versions != predecessor_versions.intersection(
                        consumed_versions)
                    or deposited_versions != set(prepared_tokens)
                    or emitted_versions - deposited_versions
                    or any(dict(prepared_tokens[version].metadata).get(
                        "net_instance_ref") != net_ref
                           for version in deposited_versions)
                    or successor_versions != (
                        predecessor_versions - consumed_versions
                        | deposited_versions)
                    or len(successor_versions) != len(successor_refs)
                    or checkpoint_metadata["settlement_delta_ref"]
                    != settlement_metadata["marking_delta_ref"]):
                db.rollback()
                raise RegistryConflict(
                    "current fault sidecar settlement is stale, duplicate, "
                    "or lacks its exact token/checkpoint closure")

        # Lifecycle validation shares this BEGIN IMMEDIATE snapshot with the
        # ensuing append. Schema/object validation already ran above before
        # the database transaction was opened.
        event_store._validate_petri_firing_resource_access(
            db, events, task_id=task_id,
            net_instance_id=net_instance_id,
            writer_epoch=writer_epoch)
        event_store._validate_agent_loop_atomicity(
            db, objects, events, writer_epoch=writer_epoch)
        event_store._validate_firing_resource_settlement_atomicity(
            db, objects, events)
        event_store._validate_provider_materialization_atomicity(objects, events)
        event_store._validate_authoritative_references(
            db, task_id=task_id, branch_id=branch_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            transaction_id=transaction_id, idempotency_key=idempotency_key,
            transaction_writer_epoch=writer_epoch,
            objects=objects, events=events,
            relations=relations)
        event_store._validate_llm_model_call_budget(db, objects, events)
        event_store._validate_lifecycle(db, events)

        if dependency_root_predecessor is not None:
            root_objects = tuple(
                item for item in objects
                if item.object_type == "dependency_root_index/v1")
            logical_id = dependency_root_predecessor["logical_id"]
            if (len(root_objects) != 1
                    or str(root_objects[0].logical_id) != logical_id):
                db.rollback()
                raise RegistryConflict(
                    "dependency root CAS requires one exact root insertion")
            latest = db.execute(
                "SELECT version_id FROM objects WHERE logical_id=? "
                "AND object_type='dependency_root_index/v1' "
                "ORDER BY rowid DESC LIMIT 1",
                (logical_id,),
            ).fetchone()
            actual_version = (
                str(latest["version_id"]) if latest is not None else None)
            if (actual_version
                    != dependency_root_predecessor[
                        "expected_version_id"]):
                db.rollback()
                raise RegistryConflict(
                    "dependency root predecessor is stale")

        for stream_id, expected_snapshot in (
                snapshot_predecessors.items()):
            marking_stream = stream_id.startswith("marking:")
            latest = db.execute(
                ("SELECT e.payload_json FROM events e WHERE e.task_id=? "
                 "AND e.stream_id=? AND " + _CANONICAL_EVENT_SQL + " "
                 "ORDER BY e.ordinal DESC LIMIT 1")
                if marking_stream else
                "SELECT payload_json FROM events WHERE stream_id=? "
                "ORDER BY ordinal DESC LIMIT 1",
                ((str(task_id), stream_id) if marking_stream
                 else (stream_id,)),
            ).fetchone()
            latest_payload = (
                json.loads(str(latest["payload_json"]))
                if latest is not None else None)
            actual_snapshot = (
                latest_payload.get("checkpoint_ref")
                if marking_stream and latest_payload is not None else
                latest_payload.get("snapshot_after_ref")
                if latest_payload is not None else None)
            if ((expected_snapshot is None and latest is not None)
                    or (expected_snapshot is not None
                        and actual_snapshot != expected_snapshot)):
                db.rollback()
                raise RegistryConflict(
                    f"snapshot predecessor conflict for {stream_id!r}")

        for pending in events:
            if pending.event_type != "net_adopted/v1":
                continue
            current = db.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND event_type='net_adopted/v1' "
                "ORDER BY task_control_sequence DESC LIMIT 1", (str(task_id),)).fetchone()
            active = (json.loads(current["payload_json"]).get("net_instance_ref")
                      if current is not None else None)
            supersedes = pending.payload.get("supersedes_net_ref")
            candidate = pending.payload.get("net_instance_ref")
            if active is None and supersedes is not None:
                db.rollback()
                raise RegistryConflict(
                    "initial net adoption cannot supersede a missing active net")
            if active is not None and (supersedes != active or candidate == active):
                db.rollback()
                raise RegistryConflict(
                    "net adoption must exactly supersede the active net with a fresh instance")

        for stream_id in {event.stream_id for event in events}:
            row = db.execute(
                "SELECT sequence FROM stream_heads WHERE stream_id=?", (stream_id,)).fetchone()
            actual = int(row[0]) if row is not None else 0
            expected = int(expected_heads.get(stream_id, 0))
            if actual != expected:
                db.rollback()
                raise RegistryConflict(
                    f"compare-and-append conflict for {stream_id!r}: expected={expected}, actual={actual}")

        created = _now()
        db.execute("""INSERT INTO transactions(
                transaction_id,task_id,status,idempotency_key,command_json,
                writer_epoch,created_at,committed_at) VALUES(?,?,?,?,?,?,?,NULL)""",
                   (str(transaction_id), str(task_id), "prepared", idempotency_key,
                    json.dumps(command_material, sort_keys=True),
                    writer_epoch, created))
        new_invocations = {
            str(item.metadata.get("own_transition_firing_ref", {}).get(
                "version_id")): item
            for item in objects
            if (item.object_type == "invocation/v1"
                and isinstance(
                    item.metadata.get("own_transition_firing_ref"), Mapping))
        }
        for item in objects:
            if item.object_type != "transition_firing/v1":
                continue
            firing_version_id = str(item.version_id)
            invocation_item = new_invocations.get(firing_version_id)
            if invocation_item is None:
                continue
            metadata = dict(item.metadata)
            invocation_metadata = dict(invocation_item.metadata)
            try:
                net_version_id = str(
                    metadata["net_instance_ref"]["version_id"])
                operation_binding_version_id = str(
                    metadata["operation_binding_ref"]["version_id"])
                admission_checkpoint_version_id = str(
                    metadata["admission_marking_checkpoint_ref"][
                        "version_id"])
            except (KeyError, TypeError) as exc:
                db.rollback()
                raise RegistryConflict(
                    "firing publication root lacks exact admission refs") from exc
            if (invocation_metadata.get("invocation_ref", {}).get(
                    "version_id") != str(invocation_item.version_id)
                    or invocation_metadata.get(
                        "own_transition_firing_ref", {}).get(
                            "version_id") != firing_version_id):
                db.rollback()
                raise RegistryConflict(
                    "firing publication root differs from its invocation")
            db.execute(
                "INSERT INTO firing_publications("
                "firing_version_id,firing_logical_id,"
                "invocation_version_id,invocation_logical_id,"
                "net_version_id,operation_binding_version_id,"
                "admission_checkpoint_version_id,state,"
                "opened_transaction_id,published_transaction_id,"
                "operation_result_version_id,workspace_lineage_id,"
                "workspace_revision_version_id,"
                "marking_checkpoint_version_id) "
                "VALUES(?,?,?,?,?,?,?,'PROVISIONAL',?,NULL,NULL,NULL,NULL,NULL)",
                (firing_version_id, str(item.logical_id),
                 str(invocation_item.version_id),
                 str(invocation_item.logical_id), net_version_id,
                 operation_binding_version_id,
                 admission_checkpoint_version_id,
                 str(transaction_id)))
        new_objects: dict[str, tuple[str, str]] = {
            key: (item.object_type, str(item.logical_id))
            for item in objects
            for key in (str(item.version_id), str(item.logical_id))
        }
        for item in objects:
            if item.producer_invocation_id is not None:
                producer = str(item.producer_invocation_id)
                found = db.execute(
                    "SELECT 1 FROM objects WHERE logical_id=? AND object_type='invocation/v1'",
                    (producer,)).fetchone()
                new = new_objects.get(producer)
                if found is None and (new is None or new[0] != "invocation/v1"):
                    db.rollback()
                    raise RegistryConflict(
                        f"object producer invocation is not registered: {producer}")

        publication_by_invocation: dict[str, str | None] = {}

        def open_firing_for_invocation(
                invocation_id: str | None) -> str | None:
            if invocation_id is None:
                return None
            if invocation_id in publication_by_invocation:
                return publication_by_invocation[invocation_id]
            roots = db.execute(
                "SELECT firing_version_id,state FROM firing_publications "
                "WHERE invocation_logical_id=? OR invocation_version_id=?",
                (invocation_id, invocation_id),
            ).fetchall()
            if len(roots) > 1:
                db.rollback()
                raise RegistryCorruptError(
                    "producer invocation resolves to multiple firing roots")
            if roots and roots[0]["state"] != "PROVISIONAL":
                db.rollback()
                raise RegistryConflict(
                    "producer-attributed Registry batch targets a closed firing")
            firing_version_id = (
                str(roots[0]["firing_version_id"]) if roots else None)
            if firing_version_id is None:
                invocation_item = next(
                    (candidate for candidate in objects
                     if candidate.object_type == "invocation/v1"
                     and invocation_id in {
                         str(candidate.logical_id),
                         str(candidate.version_id)}),
                    None)
                invocation_row = (
                    None if invocation_item is not None else db.execute(
                        "SELECT metadata_json FROM objects "
                        "WHERE object_type='invocation/v1' AND "
                        "(logical_id=? OR version_id=?) "
                        "ORDER BY rowid DESC LIMIT 1",
                        (invocation_id, invocation_id),
                    ).fetchone())
                invocation_metadata = (
                    dict(invocation_item.metadata)
                    if invocation_item is not None else
                    json.loads(str(invocation_row["metadata_json"]))
                    if invocation_row is not None else {})
                if isinstance(
                        invocation_metadata.get(
                            "own_transition_firing_ref"), Mapping):
                    db.rollback()
                    raise RegistryConflict(
                        "firing-owned producer has no open publication root")
            publication_by_invocation[invocation_id] = firing_version_id
            return firing_version_id

        def provisional_endpoint_roots(
                endpoint: VersionRef | ObjectRef,
        ) -> tuple[set[str], set[str]]:
            """Resolve open ownership and all firing endpoint provenance."""

            if isinstance(endpoint, VersionRef):
                stored = db.execute(
                    "SELECT DISTINCT p.firing_version_id,p.state FROM "
                    "firing_temporary_members m JOIN firing_publications p "
                    "ON p.firing_version_id=m.firing_version_id "
                    "WHERE m.member_kind='object' "
                    "AND m.member_identity=?",
                    (str(endpoint.version_id),),
                ).fetchall()
                candidates = tuple(
                    item for item in objects
                    if item.version_id == endpoint.version_id)
            else:
                stored = db.execute(
                    "SELECT DISTINCT p.firing_version_id,p.state FROM objects o "
                    "JOIN firing_temporary_members m "
                    "ON m.member_kind='object' "
                    "AND m.member_identity=o.version_id "
                    "JOIN firing_publications p "
                    "ON p.firing_version_id=m.firing_version_id "
                    "WHERE o.logical_id=?",
                    (str(endpoint.entity_id),),
                ).fetchall()
                candidates = tuple(
                    item for item in objects
                    if item.logical_id == endpoint.entity_id)
            all_roots = {
                str(row["firing_version_id"]) for row in stored}
            roots = {
                str(row["firing_version_id"]) for row in stored
                if row["state"] == "PROVISIONAL"}
            for candidate in candidates:
                producer = candidate.producer_invocation_id
                if (producer is None
                        and candidate.object_type == "invocation/v1"):
                    producer = candidate.logical_id
                root = open_firing_for_invocation(
                    str(producer) if producer is not None else None)
                if root is not None:
                    roots.add(root)
                    all_roots.add(root)
            return roots, all_roots

        transaction_firings: set[str] = set()
        producer_ids = {
            str(item.producer_invocation_id)
            for item in objects
            if item.producer_invocation_id is not None
        } | {
            str(pending.producer_invocation_id)
            for pending in events
            if pending.producer_invocation_id is not None
        } | {
            str(item.logical_id)
            for item in objects if item.object_type == "invocation/v1"
        }
        for producer_id in producer_ids:
            firing_version_id = open_firing_for_invocation(producer_id)
            if firing_version_id is not None:
                transaction_firings.add(firing_version_id)
        for item in objects:
            if item.object_type != "transition_firing/v1":
                continue
            root = db.execute(
                "SELECT state FROM firing_publications "
                "WHERE firing_version_id=?",
                (str(item.version_id),),
            ).fetchone()
            if root is None:
                continue
            if root["state"] != "PROVISIONAL":
                db.rollback()
                raise RegistryConflict(
                    "transition firing object targets a closed publication root")
            transaction_firings.add(str(item.version_id))
        relation_firings: dict[str, str | None] = {}
        closing_firing_versions = {
            str(command["firing_version_id"])
            for command in publication_commands
        }
        for relation in relations:
            roots = set()
            explicit = None
            if relation.producer_invocation_id is not None:
                explicit = open_firing_for_invocation(
                    str(relation.producer_invocation_id))
                if explicit is not None:
                    roots.add(explicit)
            source_open, source_all = provisional_endpoint_roots(
                relation.source)
            target_open, target_all = provisional_endpoint_roots(
                relation.target)
            if relation.system_owned:
                try:
                    owner = _closing_firing_owner_for_system_relation(
                        source_open | target_open,
                        closing_firing_versions)
                except RegistryConflict:
                    db.rollback()
                    raise
                relation_firings[str(relation.relation_id)] = owner
                continue
            roots.update(source_open)
            roots.update(target_open)
            if len(roots) > 1:
                db.rollback()
                raise RegistryConflict(
                    "relation crosses distinct provisional firing roots")
            owner = next(iter(roots), None)
            if (owner is not None
                    and relation.producer_invocation_id is not None
                    and explicit is None):
                db.rollback()
                raise RegistryConflict(
                    "relation producer does not own its provisional firing endpoint")
            if owner is None and (source_all or target_all):
                db.rollback()
                raise RegistryConflict(
                    "relation without an open firing owner targets a closed "
                    "firing endpoint")
            relation_firings[str(relation.relation_id)] = owner
            if owner is not None:
                transaction_firings.add(owner)
        if len(transaction_firings) > 1:
            db.rollback()
            raise RegistryConflict(
                "one Registry transaction cannot belong to multiple firings")
        envelopes: list[EventEnvelope] = []
        task_sequence_row = db.execute(
            "SELECT sequence FROM task_control_heads WHERE task_id=?", (str(task_id),)).fetchone()
        task_sequence = int(task_sequence_row[0]) if task_sequence_row else 0
        stream_state: dict[str, int] = {}
        for pending in events:
            event_refs = ((pending.causation_event_id,)
                          if pending.causation_event_id is not None else ()) + pending.parent_event_ids
            for event_ref in event_refs:
                if db.execute("SELECT 1 FROM events WHERE event_id=?",
                              (str(event_ref),)).fetchone() is None:
                    db.rollback()
                    raise RegistryConflict(f"event has missing causal ref: {event_ref}")
            if pending.producer_invocation_id is not None:
                producer = str(pending.producer_invocation_id)
                found = db.execute(
                    "SELECT 1 FROM objects WHERE logical_id=? AND object_type='invocation/v1'",
                    (producer,)).fetchone()
                new = new_objects.get(producer)
                if found is None and (new is None or new[0] != "invocation/v1"):
                    db.rollback()
                    raise RegistryConflict(
                        f"event producer invocation is not registered: {producer}")
            if pending.stream_id not in stream_state:
                row = db.execute("SELECT sequence FROM stream_heads WHERE stream_id=?",
                                 (pending.stream_id,)).fetchone()
                stream_state[pending.stream_id] = int(row[0]) if row else 0
            sequence = stream_state[pending.stream_id]
            sequence += 1
            if pending.task_control:
                task_sequence += 1
                event_task_sequence: int | None = task_sequence
            else:
                event_task_sequence = None
            event_id = new_id("event")
            recorded = _now()
            occurred = pending.occurred_at or recorded
            event_net_instance_id = net_instance_id
            if pending.event_type == "marking_checkpoint_committed/v1":
                checkpoint_net_ref = pending.payload.get(
                    "net_instance_ref")
                if not isinstance(checkpoint_net_ref, Mapping):
                    db.rollback()
                    raise RegistryConflict(
                        "checkpoint event lacks its exact net identity")
                event_net_instance_id = TypedId.parse(
                    str(checkpoint_net_ref.get("logical_id", "")),
                    expected="net_instance")
            envelope = EventEnvelope(
                envelope_version="v1", event_id=event_id,
                event_type=pending.event_type,
                event_schema_version=pending.event_type.rsplit("/", 1)[-1],
                criticality=pending.criticality, task_id=task_id, branch_id=branch_id,
                task_round_id=task_round_id,
                net_instance_id=event_net_instance_id,
                stream_id=pending.stream_id, aggregate_id=pending.aggregate_id,
                aggregate_type=pending.aggregate_type, stream_sequence=sequence,
                aggregate_version=sequence, task_control_sequence=event_task_sequence,
                idempotency_key=pending.idempotency_key, command_id=pending.command_id,
                correlation_id=str(transaction_id),
                causation_event_id=pending.causation_event_id,
                parent_event_ids=pending.parent_event_ids,
                producer_principal=pending.producer_principal,
                producer_invocation_id=pending.producer_invocation_id,
                transaction_id=transaction_id, occurred_at=occurred, recorded_at=recorded,
                payload_schema_ref=pending.payload_schema_ref,
                payload=dict(pending.payload),
                writer_fencing_epoch=writer_epoch)
            event_store.catalog.validate_fact_envelope(fact_event_envelope(envelope))
            event_store._insert_event(db, envelope)
            envelopes.append(envelope)
            stream_state[pending.stream_id] = sequence

        publication_events = {
            str(event.payload.get("version_id")): event
            for event in envelopes if event.event_type == "object_version_published/v1"
        }
        for item in objects:
            event_store.catalog.require(item.object_type, category="object")
            publication_event = publication_events.get(str(item.version_id))
            if publication_event is None:
                db.rollback()
                raise RegistryConflict(
                    f"object has no matching publication fact: {item.version_id}")
            prior = db.execute("SELECT * FROM objects WHERE version_id=?",
                               (str(item.version_id),)).fetchone()
            if prior is not None:
                immutable = (prior["logical_id"], prior["object_type"],
                             prior["size"], prior["media_type"],
                             prior["schema_ref"], prior["storage_locator"])
                proposed = (str(item.logical_id), item.object_type,
                            item.size, item.media_type,
                            item.schema_ref, item.storage_locator)
                if immutable != proposed:
                    db.rollback()
                    raise RegistryConflict(f"immutable version conflict: {item.version_id}")
                continue
            db.execute(
                "INSERT INTO objects("
                "logical_id,version_id,object_type,size,media_type,"
                "schema_ref,producer_invocation_id,storage_locator,"
                "metadata_json,transaction_id,published_event_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                str(item.logical_id), str(item.version_id), item.object_type,
                item.size, item.media_type, item.schema_ref,
                str(item.producer_invocation_id) if item.producer_invocation_id else None,
                item.storage_locator, json.dumps(item.metadata, sort_keys=True, default=str),
                str(transaction_id), str(publication_event.event_id)))

        for firing_version_id in transaction_firings:
            # Once any producer resolves to an open firing, the complete
            # transaction is part of that firing's temporary Registry area.
            for item in objects:
                db.execute(
                    "INSERT OR IGNORE INTO firing_temporary_members "
                    "VALUES(?,?,?,?)",
                    (firing_version_id, "object", str(item.version_id),
                     str(transaction_id)))
            for event in envelopes:
                db.execute(
                    "INSERT OR IGNORE INTO firing_temporary_members "
                    "VALUES(?,?,?,?)",
                    (firing_version_id, "event", str(event.event_id),
                     str(transaction_id)))
            db.execute(
                "INSERT OR IGNORE INTO firing_temporary_members "
                "VALUES(?,?,?,?)",
                (firing_version_id, "transaction", str(transaction_id),
                 str(transaction_id)))

        relation_events = {
            str(event.payload.get("relation_id")): event
            for event in envelopes if event.event_type == "relation_published/v1"
        }
        for relation in relations:
            event_store.catalog.require(relation.relation_type, category="relation")
            relation_event = relation_events.get(str(relation.relation_id))
            if relation_event is None:
                db.rollback()
                raise RegistryConflict(
                    f"relation has no matching publication fact: {relation.relation_id}")
            source = _ref_json(relation.source)
            target = _ref_json(relation.target)
            if relation.strength == "strong":
                event_store._verify_ref(db, source, new_objects)
                event_store._verify_ref(db, target, new_objects)
            db.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)", (
                str(relation.relation_id), relation.relation_type,
                json.dumps(source, sort_keys=True), json.dumps(target, sort_keys=True),
                relation.strength, json.dumps(relation.metadata, sort_keys=True, default=str),
                str(transaction_id), str(relation_event.event_id)))
            owner = relation_firings[str(relation.relation_id)]
            for firing_version_id in (
                    (owner,) if owner is not None
                    else tuple(transaction_firings)):
                db.execute(
                    "INSERT OR IGNORE INTO firing_temporary_members "
                    "VALUES(?,?,?,?)",
                    (firing_version_id, "relation",
                     str(relation.relation_id), str(transaction_id)))

        for command in workspace_head_commands:
            head = db.execute(
                "SELECT workspace_revision_version_id FROM workspace_lineage_heads "
                "WHERE workspace_lineage_id=?",
                (command["workspace_lineage_id"],),
            ).fetchone()
            if (head is None
                    or str(head["workspace_revision_version_id"])
                    != command["expected_workspace_head_version_id"]):
                db.rollback()
                raise RegistryConflict(
                    "workspace head compare-and-swap conflict")
            db.execute(
                "UPDATE workspace_lineage_heads SET "
                "workspace_revision_version_id=?,transaction_id=? "
                "WHERE workspace_lineage_id=? AND "
                "workspace_revision_version_id=?",
                (command["workspace_revision_version_id"],
                 str(transaction_id), command["workspace_lineage_id"],
                 command["expected_workspace_head_version_id"]),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.rollback()
                raise RegistryConflict(
                    "workspace head SQL CAS did not advance one head")

        for item in objects:
            if (item.object_type != "workspace_revision/v1"
                    or item.metadata.get("disposition") != "genesis"
                    or item.metadata.get("settled") is not True):
                continue
            if transaction_firings:
                db.rollback()
                raise RegistryConflict(
                    "a provisional firing cannot advance the committed "
                    "workspace genesis head")
            db.execute(
                "INSERT INTO workspace_lineage_heads VALUES(?,?,?)",
                (str(item.logical_id), str(item.version_id),
                 str(transaction_id)))

        def augment_settled_firing_event_record(
                firing_version_id: str, checkpoint_version_id: str) -> None:
            """Persist the exact ordinal closure after event IDs exist."""
            settlement_events = tuple(
                event for event in envelopes
                if event.event_type == "transition_firing_settled/v1"
                and event.aggregate_id == str(
                    db.execute(
                        "SELECT firing_logical_id FROM firing_publications "
                        "WHERE firing_version_id=?",
                        (firing_version_id,),
                    ).fetchone()["firing_logical_id"])
            )
            checkpoint_events = tuple(
                event for event in envelopes
                if event.event_type == "marking_checkpoint_committed/v1"
                and event.payload.get("checkpoint_ref", {}).get("version_id")
                == checkpoint_version_id
            )
            if len(settlement_events) != 1 or len(checkpoint_events) != 1:
                raise RegistryConflict(
                    "settled firing requires one settlement and checkpoint event")
            rows = db.execute(
                "SELECT e.event_id,e.ordinal,e.event_type "
                "FROM firing_temporary_members m JOIN events e "
                "ON e.event_id=m.member_identity WHERE "
                "m.firing_version_id=? AND m.member_kind='event' "
                "ORDER BY e.ordinal", (firing_version_id,)).fetchall()
            records = [{
                "event_id": str(row["event_id"]),
                "ordinal": int(row["ordinal"]),
                "event_type": str(row["event_type"]),
            } for row in rows]
            current_ids = {str(event.event_id) for event in envelopes}
            if not current_ids.issubset({item["event_id"] for item in records}):
                raise RegistryConflict(
                    "settled firing temporary event area omits settling events")
            settlement = settlement_events[0]
            settlement_payload = dict(settlement.payload)
            settlement_payload["settlement_event_id"] = str(settlement.event_id)
            settlement_payload["ordered_event_records"] = records
            checkpoint = checkpoint_events[0]
            checkpoint_payload = dict(checkpoint.payload)
            checkpoint_payload["settlement_event_id"] = str(settlement.event_id)
            event_store.catalog.validate_event_payload(
                "transition_firing_settled/v1", payload=settlement_payload)
            event_store.catalog.validate_event_payload(
                "marking_checkpoint_committed/v1", payload=checkpoint_payload)
            db.execute(
                "UPDATE events SET payload_json=? WHERE event_id=?",
                (json.dumps(settlement_payload, sort_keys=True, default=str),
                 str(settlement.event_id)))
            db.execute(
                "UPDATE events SET payload_json=? WHERE event_id=?",
                (json.dumps(checkpoint_payload, sort_keys=True, default=str),
                 str(checkpoint.event_id)))
            for index, event in enumerate(envelopes):
                if event.event_id == settlement.event_id:
                    envelopes[index] = replace(event, payload=settlement_payload)
                elif event.event_id == checkpoint.event_id:
                    envelopes[index] = replace(event, payload=checkpoint_payload)

        for command in publication_commands:
            augment_settled_firing_event_record(
                str(command["firing_version_id"]),
                str(command["marking_checkpoint_version_id"]))
            root = db.execute(
                "SELECT * FROM firing_publications "
                "WHERE firing_version_id=?",
                (command["firing_version_id"],),
            ).fetchone()
            if (root is None or root["state"] != "PROVISIONAL"
                    or str(root["invocation_version_id"])
                    != command["invocation_version_id"]
                    or str(root["admission_checkpoint_version_id"])
                    != command["admission_checkpoint_version_id"]):
                db.rollback()
                raise RegistryConflict(
                    "firing publication root is absent, stale, or already settled")
            result = db.execute(
                "SELECT metadata_json,producer_invocation_id,transaction_id "
                "FROM objects WHERE version_id=? "
                "AND object_type='operation_result/v1'",
                (command["operation_result_version_id"],),
            ).fetchone()
            firing = db.execute(
                "SELECT metadata_json,producer_invocation_id,transaction_id "
                "FROM objects WHERE version_id=? "
                "AND object_type='transition_firing/v1'",
                (command["firing_version_id"],),
            ).fetchone()
            checkpoint = db.execute(
                "SELECT metadata_json,producer_invocation_id,transaction_id "
                "FROM objects WHERE version_id=? "
                "AND object_type='marking_checkpoint/v1'",
                (command["marking_checkpoint_version_id"],),
            ).fetchone()
            has_workspace = command["workspace_lineage_id"] is not None
            revision = (db.execute(
                "SELECT logical_id,metadata_json,producer_invocation_id,"
                "transaction_id FROM objects "
                "WHERE version_id=? AND object_type='workspace_revision/v1'",
                (command["workspace_revision_version_id"],),
            ).fetchone() if has_workspace else None)
            invocation = db.execute(
                "SELECT transaction_id FROM objects WHERE version_id=? "
                "AND object_type='invocation/v1'",
                (command["invocation_version_id"],),
            ).fetchone()

            def exact_object_member(
                    version_id: str, object_row: sqlite3.Row | None,
            ) -> bool:
                if object_row is None:
                    return False
                member = db.execute(
                    "SELECT transaction_id FROM firing_temporary_members "
                    "WHERE firing_version_id=? AND member_kind='object' "
                    "AND member_identity=?",
                    (command["firing_version_id"], version_id),
                ).fetchone()
                return (member is not None
                        and str(member["transaction_id"])
                        == str(object_row["transaction_id"]))
            if (result is None or firing is None or checkpoint is None
                    or (has_workspace and revision is None)
                    or invocation is None
                    or not exact_object_member(
                        command["operation_result_version_id"], result)
                    or not exact_object_member(
                        command["firing_version_id"], firing)
                    or not exact_object_member(
                        command["invocation_version_id"], invocation)
                    or not exact_object_member(
                        command["marking_checkpoint_version_id"], checkpoint)
                    or (has_workspace and not exact_object_member(
                        command["workspace_revision_version_id"], revision))
                    or (has_workspace and str(revision["logical_id"])
                        != command["workspace_lineage_id"])):
                db.rollback()
                raise RegistryConflict(
                    "firing publication lacks its provisional result, "
                    "workspace revision, or successor checkpoint")
            result_metadata = json.loads(str(result["metadata_json"]))
            firing_metadata = json.loads(str(firing["metadata_json"]))
            checkpoint_metadata = json.loads(
                str(checkpoint["metadata_json"]))
            revision_metadata = (json.loads(str(revision["metadata_json"]))
                                 if revision is not None else {})
            delta_raw = checkpoint_metadata.get("settlement_delta_ref")
            delta_version = (
                str(delta_raw.get("version_id"))
                if isinstance(delta_raw, Mapping) else "")
            delta = db.execute(
                "SELECT metadata_json,producer_invocation_id,transaction_id "
                "FROM objects WHERE version_id=? "
                "AND object_type='marking_delta/v1'",
                (delta_version,),
            ).fetchone()
            delta_metadata = (
                json.loads(str(delta["metadata_json"]))
                if delta is not None else {})
            checkpoint_token_versions = {
                str(value.get("version_id"))
                for value in checkpoint_metadata.get("token_refs", [])
                if isinstance(value, Mapping)}
            registered_checkpoint_tokens = {
                str(row["version_id"])
                for token_version in checkpoint_token_versions
                for row in db.execute(
                    "SELECT version_id FROM objects WHERE version_id=? "
                    "AND object_type='petri_token/v1'",
                    (token_version,),
                ).fetchall()}
            checkpoint_workspace_refs = (
                checkpoint_metadata.get("workspace_revision_refs"))
            actual_checkpoint_workspaces = (
                {
                    (str(value.get("entity_type", "")),
                     str(value.get("logical_id", "")),
                     str(value.get("version_id", "")))
                    for value in checkpoint_workspace_refs
                    if isinstance(value, Mapping)
                }
                if isinstance(checkpoint_workspace_refs, list) else set())
            expected_workspaces = expected_checkpoint_workspaces[
                command["marking_checkpoint_version_id"]]
            derived_rows = (db.execute(
                "SELECT relation_id,transaction_id FROM relations "
                "WHERE relation_type='derived_from' AND "
                "json_extract(source_json,'$.version_id')=? AND "
                "json_extract(target_json,'$.version_id')=?",
                (command["marking_checkpoint_version_id"],
                 command["workspace_revision_version_id"]),
            ).fetchall() if has_workspace else ())
            derived_member = (
                db.execute(
                    "SELECT transaction_id FROM firing_temporary_members "
                    "WHERE firing_version_id=? AND member_kind='relation' "
                    "AND member_identity=?",
                    (command["firing_version_id"],
                     str(derived_rows[0]["relation_id"])),
                ).fetchone()
                if len(derived_rows) == 1 else None)
            structural_mode = (
                command["marking_net_version_id"]
                != str(root["net_version_id"]))
            net_adoptions = tuple(
                event for event in envelopes
                if (event.event_type == "net_adopted/v1"
                    and event.producer_invocation_id is not None
                    and str(event.producer_invocation_id)
                    == str(root["invocation_logical_id"])))
            structural_adoptions = tuple(
                event for event in envelopes
                if (event.event_type == "structural_growth_adopted/v1"
                    and event.producer_invocation_id is not None
                    and str(event.producer_invocation_id)
                    == str(root["invocation_logical_id"])))
            marking_commits = tuple(
                event for event in envelopes
                if (event.event_type
                    == "marking_checkpoint_committed/v1"
                    and event.producer_invocation_id is not None
                    and str(event.producer_invocation_id)
                    == str(root["invocation_logical_id"])
                    and event.payload.get(
                        "checkpoint_ref", {}).get("version_id")
                    == command["marking_checkpoint_version_id"]
                    and event.payload.get(
                        "net_instance_ref", {}).get("version_id")
                    == command["marking_net_version_id"]))
            candidate_net = db.execute(
                "SELECT producer_invocation_id,transaction_id FROM objects "
                "WHERE version_id=? "
                "AND object_type='net_instance/v1'",
                (command["marking_net_version_id"],),
            ).fetchone()
            structural_closure_valid = not structural_mode
            if structural_mode:
                structural_closure_valid = (
                    len(net_adoptions) == 1
                    and len(structural_adoptions) == 1
                    and candidate_net is not None
                    and exact_object_member(
                        command["marking_net_version_id"], candidate_net)
                    and str(candidate_net["producer_invocation_id"])
                    == str(root["invocation_logical_id"])
                    and len(marking_commits) == 1
                    and net_adoptions[0].payload.get(
                        "net_instance_ref", {}).get("version_id")
                    == command["marking_net_version_id"]
                    and net_adoptions[0].payload.get(
                        "supersedes_net_ref", {}).get("version_id")
                    == str(root["net_version_id"])
                    and structural_adoptions[0].payload.get(
                        "predecessor_net_ref", {}).get("version_id")
                    == str(root["net_version_id"])
                    and structural_adoptions[0].payload.get(
                        "candidate_net_ref", {}).get("version_id")
                    == command["marking_net_version_id"]
                    and structural_adoptions[0].payload.get(
                        "candidate_marking_checkpoint_ref", {}).get(
                            "version_id")
                    == command["marking_checkpoint_version_id"]
                )
                operation_adoptions = tuple(e for e in net_adoptions if "operation_revision" in e.payload)
                if operation_adoptions:
                    structural_closure_valid = (len(net_adoptions) == 1 and len(operation_adoptions) == 1
                        and not structural_adoptions and candidate_net is not None
                        and len(marking_commits) == 1
                        and operation_adoptions[0].payload["net_instance_ref"]["version_id"] == command["marking_net_version_id"]
                        and operation_adoptions[0].payload["supersedes_net_ref"]["version_id"] == str(root["net_version_id"])
                        and operation_adoptions[0].payload["operation_revision"]["source_firing_ref"]["version_id"] == command["firing_version_id"]
                        and operation_adoptions[0].payload["operation_revision"]["successor_checkpoint_ref"]["version_id"] == command["marking_checkpoint_version_id"])
            if (result_metadata.get("operation_result_ref", {}).get(
                    "version_id")
                    != command["operation_result_version_id"]
                    or result_metadata.get(
                        "invocation_ref", {}).get("version_id")
                    != command["invocation_version_id"]
                    or str(result["producer_invocation_id"])
                    != str(root["invocation_logical_id"])
                    or str(firing["producer_invocation_id"])
                    != str(root["invocation_logical_id"])
                    or firing_metadata.get(
                        "transition_firing_ref", {}).get("version_id")
                    != command["firing_version_id"]
                    or firing_metadata.get(
                        "net_instance_ref", {}).get("version_id")
                    != str(root["net_version_id"])
                    or firing_metadata.get(
                        "operation_binding_ref", {}).get("version_id")
                    != str(root["operation_binding_version_id"])
                    or firing_metadata.get(
                        "admission_marking_checkpoint_ref", {}).get(
                            "version_id")
                    != str(root["admission_checkpoint_version_id"])
                    or checkpoint_metadata.get(
                        "net_instance_ref", {}).get("version_id")
                    != command["marking_net_version_id"]
                    or str(checkpoint["producer_invocation_id"])
                    != str(root["invocation_logical_id"])
                    or checkpoint_metadata.get(
                        "previous_checkpoint_ref", {}).get("version_id")
                    != command["expected_parent_checkpoint_version_id"]
                    or command["firing_version_id"] not in {
                        str(value.get("version_id"))
                        for value in checkpoint_metadata.get(
                            "transition_firing_refs", [])
                        if isinstance(value, Mapping)}
                    or (has_workspace and (
                        revision_metadata.get(
                            "transition_firing_ref", {}).get("version_id")
                        != command["firing_version_id"]
                        or revision_metadata.get(
                            "net_instance_ref", {}).get("version_id")
                        != str(root["net_version_id"])
                        or revision_metadata.get(
                            "producer_invocation_ref", {}).get("version_id")
                        != command["invocation_version_id"]
                        or str(revision["producer_invocation_id"])
                        != str(root["invocation_logical_id"])
                        or revision_metadata.get(
                            "parent_revision_ref", {}).get("version_id")
                        != command["expected_workspace_head_version_id"]))
                    or delta is None
                    or not exact_object_member(delta_version, delta)
                    or str(delta["producer_invocation_id"])
                    != str(root["invocation_logical_id"])
                    or delta_metadata.get("phase") != "settlement"
                    or delta_metadata.get(
                        "net_instance_ref", {}).get("version_id")
                    != str(root["net_version_id"])
                    or command["firing_version_id"] not in {
                        str(value.get("version_id"))
                        for value in delta_metadata.get(
                            "transition_firing_refs", [])
                        if isinstance(value, Mapping)}
                    or str(root["operation_binding_version_id"]) not in {
                        str(value.get("version_id"))
                        for value in delta_metadata.get(
                            "operation_binding_refs", [])
                        if isinstance(value, Mapping)}
                    or registered_checkpoint_tokens
                    != checkpoint_token_versions
                    or len(checkpoint_workspace_refs or ())
                    != len(expected_workspaces)
                    or actual_checkpoint_workspaces
                    != expected_workspaces
                    or (has_workspace and (
                        len(derived_rows) != 1
                        or str(derived_rows[0]["transaction_id"])
                        != str(transaction_id)
                        or derived_member is None
                        or str(derived_member["transaction_id"])
                        != str(transaction_id)
                        or revision_metadata.get("settled") is not True))
                    or not structural_closure_valid):
                db.rollback()
                raise RegistryConflict(
                    "firing publication authority relations differ")
            event_store._before_firing_authority_mutation(
                db, firing_version_id=command["firing_version_id"],
                transaction_id=str(transaction_id))
            if has_workspace:
                head = db.execute(
                    "SELECT workspace_revision_version_id "
                    "FROM workspace_lineage_heads "
                    "WHERE workspace_lineage_id=?",
                    (command["workspace_lineage_id"],),
                ).fetchone()
                if (head is None
                        or str(head["workspace_revision_version_id"])
                        != command["expected_workspace_head_version_id"]):
                    db.rollback()
                    raise RegistryConflict(
                        "workspace publication head compare-and-swap conflict")
                db.execute(
                    "UPDATE workspace_lineage_heads SET "
                    "workspace_revision_version_id=?,transaction_id=? "
                    "WHERE workspace_lineage_id=? AND "
                    "workspace_revision_version_id=?",
                    (command["workspace_revision_version_id"],
                     str(transaction_id), command["workspace_lineage_id"],
                     command["expected_workspace_head_version_id"]))
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    db.rollback()
                    raise RegistryConflict(
                        "workspace publication SQL CAS did not advance one head")
            db.execute(
                "UPDATE firing_publications SET state='PUBLISHED',"
                "published_transaction_id=?,operation_result_version_id=?,"
                "workspace_lineage_id=?,workspace_revision_version_id=?,"
                "marking_checkpoint_version_id=? "
                "WHERE firing_version_id=? AND state='PROVISIONAL'",
                (str(transaction_id),
                 command["operation_result_version_id"],
                 command["workspace_lineage_id"],
                 command["workspace_revision_version_id"],
                 command["marking_checkpoint_version_id"],
                 command["firing_version_id"]))
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.rollback()
                raise RegistryConflict(
                    "firing publication SQL CAS did not promote one root")
            event_store._after_firing_authority_mutation(
                db, firing_version_id=command["firing_version_id"],
                transaction_id=str(transaction_id))

        for stream_id, sequence in stream_state.items():
            db.execute("""INSERT INTO stream_heads(stream_id,sequence,event_hash) VALUES(?,?,?)
                    ON CONFLICT(stream_id) DO UPDATE SET sequence=excluded.sequence,event_hash=excluded.event_hash""",
                       (stream_id, sequence, None))
        if task_sequence_row is None:
            db.execute("INSERT INTO task_control_heads(task_id,sequence) VALUES(?,?)",
                       (str(task_id), task_sequence))
        else:
            db.execute("UPDATE task_control_heads SET sequence=? WHERE task_id=?",
                       (task_sequence, str(task_id)))
        terminal_status = ("aborted" if any(
            event.event_type == "transaction_aborted/v1" for event in envelopes)
            else "committed")
        db.execute("UPDATE transactions SET status=?,committed_at=? WHERE transaction_id=?",
                   (terminal_status, _now(), str(transaction_id)))
        db.execute("INSERT INTO outbox VALUES(?,?,?,?,?)", (
            str(transaction_id), str(task_id), writer_epoch,
            json.dumps([str(event.event_id) for event in envelopes]), _now()))
        db.commit()
        return tuple(envelopes)
