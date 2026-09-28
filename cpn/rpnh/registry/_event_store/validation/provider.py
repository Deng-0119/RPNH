"""Provider validation over an already-open EventStore transaction."""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from typing import Any, Mapping, Sequence

from ... import event_store as facade
from ...models import PendingEvent, PreparedObject
from ..accounting import (
    _ACTUAL_MODEL_CALL_EVENT_TYPES,
    _cumulative_task_model_call_attempt_version_ids,
    _published_model_call_baseline_count,
    _registered_model_call_limits,
)


def validate_provider_materialization_atomicity(
        objects: Sequence[PreparedObject],
        events: Sequence[PendingEvent]) -> None:
    """Bind one metadata-only materialization receipt to its audit event."""
    from ...event_store import RegistryConflict
    receipts = tuple(item for item in objects if item.object_type
                     == "provider_payload_materialization_receipt/v1")
    recorded = tuple(item for item in events if item.event_type
                     == "provider_payload_materialization_recorded/v1")
    if not receipts and not recorded:
        return
    if len(receipts) != 1 or len(recorded) != 1:
        raise RegistryConflict(
            "provider materialization requires one receipt/event pair")
    receipt = dict(receipts[0].metadata)
    event = dict(recorded[0].payload)
    receipt_ref = receipt.get(
        "provider_payload_materialization_receipt_ref")
    attempt_ref = receipt.get("provider_attempt_ref")
    if (receipt != event
            or not isinstance(receipt_ref, Mapping)
            or receipt_ref.get("logical_id") != str(receipts[0].logical_id)
            or receipt_ref.get("version_id") != str(receipts[0].version_id)
            or not isinstance(attempt_ref, Mapping)
            or recorded[0].aggregate_id != attempt_ref.get("logical_id")
            or not isinstance(receipt.get("llm_call_ref"), Mapping)
            or not isinstance(receipt.get("request_recipe_ref"), Mapping)
            or not isinstance(receipt.get("terminal_delivery_ref"), Mapping)
            or isinstance(receipt.get("byte_count"), bool)
            or not isinstance(receipt.get("byte_count"), int)
            or receipt["byte_count"] < 1):
        raise RegistryConflict(
            "provider materialization receipt/event contract differs")


def validate_llm_model_call_budget(
        db: sqlite3.Connection,
        objects: Sequence[PreparedObject],
        events: Sequence[PendingEvent]) -> None:
    """Require one atomic canonical response per counted returned call."""
    from ...event_store import (
        RegistryConflict,
        TaskModelCallLimitExceeded,
    )

    returned_terminals = tuple(
        event for event in events
        if event.event_type in _ACTUAL_MODEL_CALL_EVENT_TYPES)
    successes = tuple(
        event for event in returned_terminals
        if event.event_type == "llm_invocation_succeeded/v1")
    interruptions = tuple(
        event for event in returned_terminals
        if event.event_type == "llm_invocation_interrupted/v1")
    if not returned_terminals:
        return
    pending_objects = {
        (item.object_type, str(item.version_id)): dict(item.metadata)
        for item in objects
    }

    def exact_metadata(
            ref: object, *, expected_type: str,
    ) -> Mapping[str, Any]:
        if (not isinstance(ref, Mapping)
                or ref.get("entity_type") != expected_type
                or not isinstance(ref.get("logical_id"), str)
                or not isinstance(ref.get("version_id"), str)):
            raise RegistryConflict(
                "LLM success contains an invalid exact object ref")
        key = (expected_type, str(ref["version_id"]))
        pending = pending_objects.get(key)
        if pending is not None:
            return pending
        row = db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? AND object_type=?",
            (str(ref["version_id"]), expected_type),
        ).fetchone()
        if row is None or str(row["logical_id"]) != str(ref["logical_id"]):
            raise RegistryConflict(
                "LLM success exact object is unavailable")
        return json.loads(str(row["metadata_json"]))

    pending_terminal_attempt_ids: set[str] = set()
    pending_returned_attempt_ids: set[str] = set()
    pending_returned_attempt_version_ids: set[str] = set()
    for success in successes:
        payload = success.payload
        attempt_ref = payload.get("llm_invocation_attempt_ref")
        invocation_ref = payload.get("llm_invocation_ref")
        response_ref = payload.get("response_resource_ref")
        response_events = tuple(
            event for event in events
            if event.event_type == "llm_response_registered/v1"
            and event.aggregate_id == success.aggregate_id)
        turn_events = tuple(
            event for event in events
            if event.event_type == "agent_turn_recorded/v2"
            and event.payload.get("llm_invocation_attempt_ref")
            == attempt_ref)
        attempt = exact_metadata(
            attempt_ref, expected_type="llm_invocation_attempt/v1")
        invocation = exact_metadata(
            invocation_ref, expected_type="llm_invocation_spec/v1")
        invocation_kind = invocation.get("invocation_kind")
        compaction_ref = payload.get("agent_context_compaction_ref")
        compaction_objects = tuple(
            item for item in objects
            if isinstance(compaction_ref, Mapping)
            and item.object_type == "agent_context_compaction/v3"
            and item.metadata.get("agent_context_compaction_ref")
            == compaction_ref)
        loop_objects = tuple(
            item for item in objects
            if item.object_type == "agent_loop/v1")
        if (len(response_events) != 1
                or success.aggregate_type != "llm_invocation_attempt"
                or success.aggregate_id != (
                    attempt_ref.get("logical_id")
                    if isinstance(attempt_ref, Mapping) else None)
                or response_events[0].payload.get(
                    "llm_invocation_ref") != invocation_ref
                or response_events[0].payload.get(
                    "llm_invocation_attempt_ref") != attempt_ref
                or response_events[0].payload.get(
                    "response_resource_ref") != response_ref
                or success.idempotency_key
                != response_events[0].idempotency_key
                or success.command_id != response_events[0].command_id
                or invocation_kind not in {
                    "normal_turn", "context_compaction",
                    "delegated_subtask"}):
            raise RegistryConflict(
                "LLM success lacks one atomic registered response")
        if (attempt.get("llm_invocation_attempt_ref") != attempt_ref
                or attempt.get("llm_invocation_ref") != invocation_ref
                or invocation.get("llm_invocation_ref") != invocation_ref
                or invocation.get("request_resource_ref") is None
                or invocation.get("llm_input_target_ref") is None):
            raise RegistryConflict(
                "counted LLM success differs from its invocation authority")
        if invocation_kind == "normal_turn":
            if (len(turn_events) != 1 or compaction_objects
                    or payload.get("agent_turn_ref")
                    != {
                        "entity_type": "agent_turn/v1",
                        "logical_id": turn_events[0].payload.get(
                            "agent_turn_id"),
                        "version_id": turn_events[0].payload.get(
                            "agent_turn_version_id"),
                    }
                    or "agent_context_compaction_ref" in payload
                    or turn_events[0].payload.get(
                        "llm_invocation_ref") != invocation_ref
                    or turn_events[0].payload.get(
                        "response_resource_ref") != response_ref
                    or success.idempotency_key
                    != turn_events[0].idempotency_key
                    or success.command_id
                    != turn_events[0].command_id):
                raise RegistryConflict(
                    "normal LLM success lacks one atomic response/turn closure")
        elif invocation_kind == "context_compaction":
            compaction = (dict(compaction_objects[0].metadata)
                          if len(compaction_objects) == 1 else {})
            loop = (dict(loop_objects[0].metadata)
                    if len(loop_objects) == 1 else {})
            if (turn_events or len(compaction_objects) != 1
                    or len(loop_objects) != 1
                    or "agent_turn_ref" in payload
                    or not isinstance(compaction_ref, Mapping)
                    or compaction.get("llm_invocation_ref")
                    != invocation_ref
                    or compaction.get("llm_invocation_attempt_ref")
                    != attempt_ref
                    or compaction.get("agent_loop_ref")
                    != loop.get("agent_loop_ref")
                    or loop.get("state") != "WAITING_FOR_LLM"
                    or loop.get("next_turn_sequence")
                    != invocation.get("turn_sequence")):
                raise RegistryConflict(
                    "compaction LLM success lacks one atomic v3/loop closure")
        else:
            local_sequence = invocation.get("local_sequence")
            prior_invocation_refs = invocation.get("prior_turn_refs")
            delegated_compaction_objects = tuple(
                item for item in objects
                if item.object_type == "agent_context_compaction/v3"
                and (item.metadata.get("llm_invocation_ref")
                     == invocation_ref
                     or item.metadata.get("llm_invocation_attempt_ref")
                     == attempt_ref))
            if (turn_events or delegated_compaction_objects
                    or "agent_turn_ref" in payload
                    or "agent_context_compaction_ref" in payload
                    or isinstance(local_sequence, bool)
                    or not isinstance(local_sequence, int)
                    or local_sequence < 0
                    or invocation.get("turn_sequence") != local_sequence
                    or payload.get("turn_sequence") != local_sequence
                    or not isinstance(
                        invocation.get("child_session_id"), str)
                    or not invocation.get("child_session_id")
                    or invocation.get("execution_agent_ref")
                    != invocation.get("subject_agent_ref")
                    or not isinstance(prior_invocation_refs, list)
                    or len(prior_invocation_refs) != local_sequence):
                raise RegistryConflict(
                    "delegated LLM success lacks exact parent-owned authority")
            for prior_sequence, prior_ref in enumerate(
                    prior_invocation_refs):
                prior = exact_metadata(
                    prior_ref, expected_type="llm_invocation_spec/v1")
                if (prior.get("llm_invocation_ref") != prior_ref
                        or prior.get("invocation_kind")
                        != "delegated_subtask"
                        or prior.get("child_session_id")
                        != invocation.get("child_session_id")
                        or prior.get("local_sequence") != prior_sequence
                        or prior.get("invocation_ref")
                        != invocation.get("invocation_ref")
                        or prior.get("operation_binding_ref")
                        != invocation.get("operation_binding_ref")
                        or prior.get("agent_loop_ref")
                        != invocation.get("agent_loop_ref")):
                    raise RegistryConflict(
                        "delegated LLM success has invalid child-call closure")
        pending_terminal_attempt_ids.add(success.aggregate_id)
        if (invocation_kind == "context_compaction"
                or (invocation_kind in {
                        "normal_turn", "delegated_subtask"}
                    and "finalization_scope" in attempt
                    and attempt["finalization_scope"] is None)):
            pending_returned_attempt_ids.add(success.aggregate_id)
            pending_returned_attempt_version_ids.add(
                str(attempt_ref["version_id"]))

    for interruption in interruptions:
        payload = interruption.payload
        attempt_ref = payload.get("llm_invocation_attempt_ref")
        invocation_ref = payload.get("llm_invocation_ref")
        response_ref = payload.get("response_resource_ref")
        response_events = tuple(
            event for event in events
            if event.event_type == "llm_response_registered/v1"
            and event.aggregate_id == interruption.aggregate_id)
        attempt = exact_metadata(
            attempt_ref, expected_type="llm_invocation_attempt/v1")
        invocation = exact_metadata(
            invocation_ref, expected_type="llm_invocation_spec/v1")
        if (len(response_events) != 1
                or interruption.aggregate_type
                != "llm_invocation_attempt"
                or interruption.aggregate_id != (
                    attempt_ref.get("logical_id")
                    if isinstance(attempt_ref, Mapping) else None)
                or response_events[0].payload.get(
                    "llm_invocation_ref") != invocation_ref
                or response_events[0].payload.get(
                    "llm_invocation_attempt_ref") != attempt_ref
                or response_events[0].payload.get(
                    "response_resource_ref") != response_ref
                or interruption.idempotency_key
                != response_events[0].idempotency_key
                or interruption.command_id != response_events[0].command_id
                or payload.get("finish_reason") != "length"
                or invocation.get("invocation_kind") not in {
                    "normal_turn", "delegated_subtask"}):
            raise RegistryConflict(
                "length-interrupted LLM call lacks one atomic response")
        if (attempt.get("llm_invocation_attempt_ref") != attempt_ref
                or attempt.get("llm_invocation_ref") != invocation_ref
                or invocation.get("llm_invocation_ref") != invocation_ref
                or invocation.get("request_resource_ref") is None
                or invocation.get("llm_input_target_ref") is None):
            raise RegistryConflict(
                "counted LLM interruption differs from its authority")
        if invocation.get("invocation_kind") == "normal_turn":
            if (not isinstance(payload.get("agent_loop_id"), str)
                    or payload.get("turn_sequence")
                    != invocation.get("turn_sequence")
                    or not isinstance(payload.get("revision"), int)):
                raise RegistryConflict(
                    "parent length interruption lacks AgentLoop authority")
        elif (payload.get("child_session_id")
                != invocation.get("child_session_id")
                or payload.get("local_sequence")
                != invocation.get("local_sequence")
                or "agent_loop_id" in payload
                or "turn_sequence" in payload
                or "revision" in payload):
            raise RegistryConflict(
                "child length interruption lacks local session annotation")
        if (invocation.get("invocation_kind") in {
                "normal_turn", "delegated_subtask"}
                and "finalization_scope" in attempt
                and attempt["finalization_scope"] is None):
            pending_returned_attempt_ids.add(interruption.aggregate_id)
            pending_returned_attempt_version_ids.add(
                str(attempt_ref["version_id"]))
        pending_terminal_attempt_ids.add(interruption.aggregate_id)

    placeholders = ",".join(
        "?" for _value in pending_terminal_attempt_ids)
    terminal_placeholders = ",".join(
        "?" for _value in _ACTUAL_MODEL_CALL_EVENT_TYPES)
    if pending_terminal_attempt_ids and db.execute(
            "SELECT 1 FROM events e JOIN transactions t "
            "ON t.transaction_id=e.transaction_id "
            "WHERE t.status='committed' "
            f"AND e.event_type IN ({terminal_placeholders}) "
            f"AND e.aggregate_id IN ({placeholders}) LIMIT 1",
            (*tuple(sorted(_ACTUAL_MODEL_CALL_EVENT_TYPES)),
             *tuple(sorted(pending_terminal_attempt_ids))),
    ).fetchone() is not None:
        raise RegistryConflict(
            "one LLM attempt cannot register a returned response twice")

    if not pending_returned_attempt_ids:
        return

    task_row = db.execute(
        "SELECT value FROM registry_meta WHERE key='task_id'",
    ).fetchone()
    if task_row is None:
        raise RegistryConflict("returned LLM response lacks task identity")
    task_id = str(task_row["value"])
    try:
        _ordinary_limit, limit = _registered_model_call_limits(
            db, task_id=task_id)
    except ValueError as exc:
        raise RegistryConflict(
            "returned LLM response lacks valid generation call-limit authority"
        ) from exc
    returned_total = (
        _published_model_call_baseline_count(db, task_id=task_id)
        + len(_cumulative_task_model_call_attempt_version_ids(
            db, task_id=task_id,
            pending_attempt_version_ids=(
                pending_returned_attempt_version_ids))))
    if returned_total > limit:
        raise TaskModelCallLimitExceeded(
            "actual returned task model-call cap is exhausted")

def require_canonical_provider_lineage(
        context,
        relation_type: str, source_ref: Mapping[str, Any],
        target_ref: Mapping[str, Any]) -> Mapping[str, Any]:
    db = context.db
    idempotency_key = context.idempotency_key
    new_by_version = context.new_by_version
    objects = context.objects
    persisted_member_visible = context.persisted_member_visible
    relations = context.relations
    transaction_id = context.transaction_id
    registered_refs_for_versions = context.registered_refs_for_versions
    CanonicalProviderLineageViolation = facade.CanonicalProviderLineageViolation
    RegistryConflict = facade.RegistryConflict
    RegistryCorruptError = facade.RegistryCorruptError
    _canonical_provider_lineage_endpoint_types = facade._canonical_provider_lineage_endpoint_types
    _ref_json = facade._ref_json
    canonical_provider_lineage_record = facade.canonical_provider_lineage_record
    """Use one predicate for persisted authority and this proposal."""

    try:
        source_type, target_type, _suffix = (
            _canonical_provider_lineage_endpoint_types(
                relation_type, source_ref, target_ref))
    except CanonicalProviderLineageViolation as exc:
        raise RegistryConflict(
            f"{relation_type} proposal names unsupported endpoint types"
        ) from exc
    source_id = str(source_ref.get(
        "logical_id", source_ref.get("entity_id", "")))
    source_version = str(source_ref.get("version_id", ""))
    target_id = str(target_ref.get(
        "logical_id", target_ref.get("entity_id", "")))
    target_version = str(target_ref.get("version_id", ""))

    persisted_source = db.execute(
        "SELECT o.logical_id,o.metadata_json,o.transaction_id,"
        "t.idempotency_key FROM objects o JOIN transactions t "
        "ON t.transaction_id=o.transaction_id WHERE o.version_id=? "
        "AND o.object_type=?", (source_version, source_type),
    ).fetchone()
    persisted_target = db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
        "AND object_type=?", (target_version, target_type),
    ).fetchone()
    if (persisted_source is not None
            and not persisted_member_visible(
                "object", source_version)):
        persisted_source = None
    if (persisted_target is not None
            and not persisted_member_visible(
                "object", target_version)):
        persisted_target = None
    new_source = new_by_version.get(source_version)
    new_target = new_by_version.get(target_version)
    if persisted_source is not None:
        try:
            source_metadata = json.loads(persisted_source["metadata_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryCorruptError(
                f"persisted {relation_type} source metadata is corrupt") from exc
        source_transaction = str(persisted_source["transaction_id"])
        source_key = str(persisted_source["idempotency_key"])
        if str(persisted_source["logical_id"]) != source_id:
            raise RegistryConflict(
                f"{relation_type} proposal names the wrong source identity")
    elif (new_source is not None and new_source.object_type == source_type
          and str(new_source.logical_id) == source_id):
        source_metadata = dict(new_source.metadata)
        source_transaction = str(transaction_id)
        source_key = idempotency_key
    else:
        raise RegistryConflict(
            f"{relation_type} source is not an exact registered object")
    authoritative_target = ({
        "entity_type": str(source_metadata.get(
            "llm_call_ref", {}).get(
                "entity_type", "llm_call_spec/v1")),
        "logical_id": source_metadata.get("llm_call_id"),
        "version_id": source_metadata.get("llm_call_version_id"),
    } if relation_type == "attempt_of_call" else {
        "entity_type": "invocation/v1",
        "logical_id": (
            source_metadata.get("invocation_ref", {}).get("logical_id")
            if isinstance(source_metadata.get("invocation_ref"), Mapping)
            else source_metadata.get("invocation_id")),
        "version_id": (
            source_metadata.get("invocation_ref", {}).get("version_id")
            if isinstance(source_metadata.get("invocation_ref"), Mapping)
            else source_metadata.get("invocation_version_id")),
    })
    authoritative_target_row = db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
        "AND object_type=?",
        (str(authoritative_target["version_id"]), target_type),
    ).fetchone()
    if (authoritative_target_row is not None
            and not persisted_member_visible(
                "object", str(authoritative_target["version_id"]))):
        authoritative_target_row = None
    if persisted_source is not None:
        if (authoritative_target_row is None
                or str(authoritative_target_row["logical_id"])
                != str(authoritative_target["logical_id"])):
            raise RegistryCorruptError(
                f"persisted {relation_type} authoritative target is missing")
        try:
            authoritative_target_metadata = json.loads(
                authoritative_target_row["metadata_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryCorruptError(
                f"persisted {relation_type} target metadata is corrupt") from exc
    if persisted_target is not None:
        try:
            target_metadata = json.loads(persisted_target["metadata_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryCorruptError(
                f"persisted {relation_type} target metadata is corrupt") from exc
        if str(persisted_target["logical_id"]) != target_id:
            raise RegistryConflict(
                f"{relation_type} proposal names the wrong target identity")
    elif (new_target is not None and new_target.object_type == target_type
          and str(new_target.logical_id) == target_id):
        target_metadata = dict(new_target.metadata)
    else:
        raise RegistryConflict(
            f"{relation_type} target is not an exact registered object")

    persisted_records = [dict(row) for row in db.execute(
        "SELECT r.relation_id,r.relation_type,r.source_json,r.target_json,"
        "r.strength,r.metadata_json,r.transaction_id,"
        "COALESCE(e.transaction_id,'') AS publication_transaction_id,"
        "COALESCE(e.event_type,'') AS publication_event_type,"
        "e.payload_json AS publication_payload_json "
        "FROM relations r LEFT JOIN events e "
        "ON e.event_id=r.published_event_id WHERE r.relation_type=? "
        "AND (json_extract(r.source_json,'$.entity_id')=? OR "
        "json_extract(r.source_json,'$.version_id')=?)",
        (relation_type, source_id, source_version),
    ).fetchall() if persisted_member_visible(
        "relation", str(row["relation_id"]))]
    persisted_ref_versions = {source_version, target_version}
    for record in persisted_records:
        for field in ("source_json", "target_json"):
            value = record.get(field)
            try:
                ref = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(ref, Mapping):
                persisted_ref_versions.add(str(ref.get("version_id", "")))
    persisted_refs = registered_refs_for_versions(
        persisted_ref_versions)
    if persisted_source is not None:
        try:
            canonical_provider_lineage_record(
                relation_type, source_ref, authoritative_target,
                publication_transaction=source_transaction,
                publication_key=source_key,
                source_metadata=source_metadata,
                target_metadata=authoritative_target_metadata,
                relation_records=persisted_records,
                registered_exact_refs=persisted_refs,
            )
        except (CanonicalProviderLineageViolation,
                json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RegistryCorruptError(
                f"persisted {relation_type} authority is corrupt") from exc

    proposal_records = [{
        "relation_id": str(relation.relation_id),
        "relation_type": relation.relation_type,
        "source": _ref_json(relation.source),
        "target": _ref_json(relation.target),
        "strength": relation.strength,
        "metadata": dict(relation.metadata),
        "transaction_id": str(transaction_id),
        "publication_transaction_id": str(transaction_id),
        "publication_event_type": "relation_published/v1",
        "publication_payload": {
            "relation_id": str(relation.relation_id),
            "relation_type": relation.relation_type,
            "source": _ref_json(relation.source),
            "target": _ref_json(relation.target),
            "strength": relation.strength,
            "metadata": dict(relation.metadata),
        },
    } for relation in relations if relation.relation_type == relation_type]
    proposal_refs = {
        (item.object_type, str(item.logical_id), str(item.version_id))
        for item in objects
    }
    try:
        return canonical_provider_lineage_record(
            relation_type, source_ref, target_ref,
            publication_transaction=source_transaction,
            publication_key=source_key,
            source_metadata=source_metadata,
            target_metadata=target_metadata,
            relation_records=(*persisted_records, *proposal_records),
            registered_exact_refs=(*persisted_refs, *proposal_refs),
        )
    except CanonicalProviderLineageViolation as exc:
        raise RegistryConflict(
            f"{relation_type} proposal is not canonical") from exc

def require_canonical_attempt_response(
        context,
        attempt_ref: Mapping[str, Any],
        response_ref: Mapping[str, Any]) -> Mapping[str, Any]:
    db = context.db
    event_store = context.event_store
    objects = context.objects
    relations = context.relations
    transaction_id = context.transaction_id
    registered_refs_for_versions = context.registered_refs_for_versions
    CanonicalAttemptResponseViolation = facade.CanonicalAttemptResponseViolation
    RegistryConflict = facade.RegistryConflict
    RegistryCorruptError = facade.RegistryCorruptError
    _ref_json = facade._ref_json
    canonical_attempt_response_record = facade.canonical_attempt_response_record
    """Validate one persisted attempt-response authority in this snapshot."""

    attempt_id = str(attempt_ref.get("logical_id", ""))
    attempt_version = str(attempt_ref.get("version_id", ""))
    response_id = str(response_ref.get("logical_id", ""))
    response_version = str(response_ref.get("version_id", ""))
    attempt_row = db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
        "AND object_type='provider_attempt_spec/v1'", (attempt_version,),
    ).fetchone()
    response_row = db.execute(
        "SELECT o.logical_id,o.metadata_json,o.transaction_id,"
        "t.idempotency_key FROM objects o JOIN transactions t "
        "ON t.transaction_id=o.transaction_id WHERE o.version_id=? "
        "AND o.object_type='resource_version/v1'", (response_version,),
    ).fetchone()
    if (attempt_row is None or response_row is None
            or str(attempt_row["logical_id"])
            != str(attempt_ref.get("logical_id", ""))
            or str(response_row["logical_id"])
            != str(response_ref.get("logical_id", ""))):
        raise RegistryCorruptError(
            "persisted attempt response has no exact endpoint objects")
    relation_records = [dict(row) for row in db.execute(
        "SELECT r.relation_id,r.relation_type,r.source_json,r.target_json,"
        "r.strength,r.metadata_json,r.transaction_id,"
        "COALESCE(e.transaction_id,'') AS publication_transaction_id,"
        "COALESCE(e.event_type,'') AS publication_event_type,"
        "e.payload_json AS publication_payload_json "
        "FROM relations r LEFT JOIN events e "
        "ON e.event_id=r.published_event_id "
        "WHERE r.relation_type='attempt_produced_response' AND ("
        "json_extract(r.source_json,'$.entity_id')=? OR "
        "json_extract(r.source_json,'$.version_id')=? OR "
        "json_extract(r.target_json,'$.entity_id')=? OR "
        "json_extract(r.target_json,'$.version_id')=?)",
        (attempt_id, attempt_version, response_id, response_version),
    ).fetchall()]
    completion_rows = db.execute(
        "SELECT * "
        "FROM events WHERE event_type IN "
        "('provider_attempt_submission_observed/v1',"
        "'provider_attempt_completed/v1',"
        "'provider_attempt_reconciled_completed/v1') AND ("
        "aggregate_id=? OR "
        "json_extract(payload_json,'$.response_version_id')=? OR "
        "json_extract(payload_json,"
        "'$.response_resource_ref.resource_version_id')=?)",
        (attempt_id, response_version, response_version),
    ).fetchall()
    try:
        completion_records = [
            event_store._verified_persisted_event_record(db, row)
            for row in completion_rows]
    except RegistryCorruptError as exc:
        raise RegistryCorruptError(
            "persisted attempt response authority is corrupt") from exc
    registered_versions = {attempt_version, response_version}
    attempt_metadata = json.loads(attempt_row["metadata_json"])
    response_metadata = json.loads(response_row["metadata_json"])
    registered_versions.add(str(
        attempt_metadata.get("llm_call_version_id", "")))
    for record in relation_records:
        for field in ("source_json", "target_json"):
            value = record.get(field)
            try:
                ref = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(ref, Mapping):
                registered_versions.add(str(ref.get("version_id", "")))
    registered_refs = registered_refs_for_versions(
        registered_versions)
    proposal_records = [{
        "relation_id": str(relation.relation_id),
        "relation_type": relation.relation_type,
        "source": _ref_json(relation.source),
        "target": _ref_json(relation.target),
        "strength": relation.strength,
        "metadata": dict(relation.metadata),
        "transaction_id": str(transaction_id),
        "publication_transaction_id": str(transaction_id),
        "publication_event_type": "relation_published/v1",
        "publication_payload": {
            "relation_id": str(relation.relation_id),
            "relation_type": relation.relation_type,
            "source": _ref_json(relation.source),
            "target": _ref_json(relation.target),
            "strength": relation.strength,
            "metadata": dict(relation.metadata),
        },
    } for relation in relations
        if relation.relation_type == "attempt_produced_response"]
    proposal_refs = {
        (item.object_type, str(item.logical_id), str(item.version_id))
        for item in objects
    }
    try:
        canonical = canonical_attempt_response_record(
            attempt_ref, response_ref,
            publication_transaction=str(response_row["transaction_id"]),
            publication_key=str(response_row["idempotency_key"]),
            attempt_metadata=attempt_metadata,
            response_metadata=response_metadata,
            relation_records=relation_records,
            completion_records=completion_records,
            registered_exact_refs=registered_refs,
        )
    except (CanonicalAttemptResponseViolation, json.JSONDecodeError,
            TypeError, ValueError) as exc:
        raise RegistryCorruptError(
            "persisted attempt response authority is corrupt") from exc
    if not proposal_records:
        return canonical
    try:
        return canonical_attempt_response_record(
            attempt_ref, response_ref,
            publication_transaction=str(response_row["transaction_id"]),
            publication_key=str(response_row["idempotency_key"]),
            attempt_metadata=attempt_metadata,
            response_metadata=response_metadata,
            relation_records=(*relation_records, *proposal_records),
            completion_records=completion_records,
            registered_exact_refs=(*registered_refs, *proposal_refs),
        )
    except CanonicalAttemptResponseViolation as exc:
        raise RegistryConflict(
            "attempt response relation proposal is not canonical") from exc

def require_canonical_attempt_response_proposal(
        context,
        attempt_ref: Mapping[str, Any], response_ref: Mapping[str, Any],
        response_metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    branch_id = context.branch_id
    db = context.db
    event_store = context.event_store
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    objects = context.objects
    relations = context.relations
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    registered_refs_for_versions = context.registered_refs_for_versions
    CanonicalAttemptResponseViolation = facade.CanonicalAttemptResponseViolation
    RegistryConflict = facade.RegistryConflict
    RegistryCorruptError = facade.RegistryCorruptError
    _ref_json = facade._ref_json
    canonical_attempt_response_record = facade.canonical_attempt_response_record
    """Validate a newly proposed response with conflict classification."""

    attempt_id = str(attempt_ref.get("logical_id", ""))
    attempt_version = str(attempt_ref.get("version_id", ""))
    response_id = str(response_ref.get("logical_id", ""))
    response_version = str(response_ref.get("version_id", ""))
    attempt_row = db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
        "AND object_type='provider_attempt_spec/v1'", (attempt_version,),
    ).fetchone()
    if (attempt_row is None or str(attempt_row["logical_id"]) != str(
            attempt_ref.get("logical_id", ""))):
        raise RegistryConflict(
            "attempt response proposal has no exact source object")
    try:
        attempt_metadata = json.loads(attempt_row["metadata_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            "persisted attempt response source metadata is corrupt") from exc
    proposal_relations = [{
        "relation_id": str(relation.relation_id),
        "relation_type": relation.relation_type,
        "source": _ref_json(relation.source),
        "target": _ref_json(relation.target),
        "strength": relation.strength,
        "metadata": dict(relation.metadata),
        "transaction_id": str(transaction_id),
        "publication_transaction_id": str(transaction_id),
        "publication_event_type": "relation_published/v1",
        "publication_payload": {
            "relation_id": str(relation.relation_id),
            "relation_type": relation.relation_type,
            "source": _ref_json(relation.source),
            "target": _ref_json(relation.target),
            "strength": relation.strength,
            "metadata": dict(relation.metadata),
        },
    } for relation in relations
        if relation.relation_type == "attempt_produced_response"]
    proposal_completions = [{
        "proposal_record": True,
        "event_type": pending.event_type,
        "event_schema_version": pending.event_type.rsplit("/", 1)[-1],
        "criticality": pending.criticality,
        "task_id": str(task_id),
        "branch_id": branch_id,
        "task_round_id": (
            str(task_round_id) if task_round_id is not None else None),
        "net_instance_id": (
            str(net_instance_id) if net_instance_id is not None else None),
        "stream_id": pending.stream_id,
        "aggregate_id": pending.aggregate_id,
        "aggregate_type": pending.aggregate_type,
        "task_control": pending.task_control,
        "idempotency_key": pending.idempotency_key,
        "command_id": pending.command_id,
        "correlation_id": str(transaction_id),
        "causation_event_id": (
            str(pending.causation_event_id)
            if pending.causation_event_id is not None else None),
        "parent_event_ids": [
            str(value) for value in pending.parent_event_ids],
        "producer_principal": pending.producer_principal,
        "producer_invocation_id": (
            str(pending.producer_invocation_id)
            if pending.producer_invocation_id is not None else None),
        "transaction_id": str(transaction_id),
        "occurred_at": pending.occurred_at,
        "payload_schema_ref": pending.payload_schema_ref,
        "payload": dict(pending.payload),
    } for pending in events if pending.event_type in {
        "provider_attempt_submission_observed/v1",
        "provider_attempt_completed/v1",
        "provider_attempt_reconciled_completed/v1",
    }]
    persisted_relations = [dict(row) for row in db.execute(
        "SELECT r.relation_id,r.relation_type,r.source_json,r.target_json,"
        "r.strength,r.metadata_json,r.transaction_id,"
        "COALESCE(e.transaction_id,'') AS publication_transaction_id,"
        "COALESCE(e.event_type,'') AS publication_event_type,"
        "e.payload_json AS publication_payload_json "
        "FROM relations r LEFT JOIN events e "
        "ON e.event_id=r.published_event_id "
        "WHERE r.relation_type='attempt_produced_response' AND ("
        "json_extract(r.source_json,'$.entity_id')=? OR "
        "json_extract(r.source_json,'$.version_id')=? OR "
        "json_extract(r.target_json,'$.entity_id')=? OR "
        "json_extract(r.target_json,'$.version_id')=?)",
        (attempt_id, attempt_version, response_id, response_version),
    ).fetchall()]
    persisted_completion_rows = db.execute(
        "SELECT * "
        "FROM events WHERE event_type IN "
        "('provider_attempt_submission_observed/v1',"
        "'provider_attempt_completed/v1',"
        "'provider_attempt_reconciled_completed/v1') AND ("
        "aggregate_id=? OR "
        "json_extract(payload_json,'$.response_version_id')=? OR "
        "json_extract(payload_json,"
        "'$.response_resource_ref.resource_version_id')=?)",
        (attempt_id, response_version, response_version),
    ).fetchall()
    persisted_completions = [
        event_store._verified_persisted_event_record(db, row)
        for row in persisted_completion_rows]
    registered_versions = {
        attempt_version,
        str(attempt_metadata.get("llm_call_version_id", "")),
    }
    for record in persisted_relations:
        for field in ("source_json", "target_json"):
            value = record.get(field)
            try:
                ref = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(ref, Mapping):
                registered_versions.add(str(ref.get("version_id", "")))
    persisted_refs = registered_refs_for_versions(
        registered_versions)
    proposal_refs = {
        (item.object_type, str(item.logical_id), str(item.version_id))
        for item in objects
    }
    try:
        return canonical_attempt_response_record(
            attempt_ref, response_ref,
            publication_transaction=str(transaction_id),
            publication_key=idempotency_key,
            attempt_metadata=attempt_metadata,
            response_metadata=response_metadata,
            relation_records=(*persisted_relations, *proposal_relations),
            completion_records=(
                *persisted_completions, *proposal_completions),
            registered_exact_refs=(*persisted_refs, *proposal_refs),
        )
    except CanonicalAttemptResponseViolation as exc:
        raise RegistryConflict(
            "attempt response proposal is not canonical") from exc

def require_canonical_producer(
        context,
        resource_ref: Mapping[str, Any],
        producer_ref: Mapping[str, Any],
        *, publication_transaction: str,
        ) -> Mapping[str, Any]:
    db = context.db
    idempotency_key = context.idempotency_key
    objects = context.objects
    relations = context.relations
    transaction_id = context.transaction_id
    CanonicalProducerViolation = facade.CanonicalProducerViolation
    RegistryConflict = facade.RegistryConflict
    RegistryCorruptError = facade.RegistryCorruptError
    _ref_json = facade._ref_json
    canonical_producer_logical_set = facade.canonical_producer_logical_set
    """Validate persisted state first, then this transaction's proposal."""
    resource_id = str(resource_ref.get("logical_id", ""))
    resource_version = str(resource_ref.get("version_id", ""))
    current_records: list[dict[str, Any]] = []
    for relation in relations:
        if relation.relation_type != "produced_by":
            continue
        current_records.append({
            "relation_id": str(relation.relation_id),
            "relation_type": relation.relation_type,
            "source": _ref_json(relation.source),
            "target": _ref_json(relation.target),
            "strength": relation.strength,
            "metadata": dict(relation.metadata),
            "transaction_id": str(transaction_id),
        })
    persisted_records = [dict(row) for row in db.execute(
        "SELECT relation_id,source_json,target_json,strength,metadata_json,"
        "transaction_id,relation_type FROM relations "
        "WHERE relation_type='produced_by' AND "
        "json_extract(source_json,'$.entity_type')="
        "'resource_version/v1' AND "
        "json_extract(source_json,'$.entity_id')=?",
        (resource_id,),
    ).fetchall()]
    persisted_resources: list[dict[str, Any]] = []
    for row in db.execute(
            "SELECT logical_id,version_id,metadata_json,transaction_id "
            "FROM objects WHERE logical_id=? "
            "AND object_type='resource_version/v1'", (resource_id,)):
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryCorruptError(
                f"persisted resource metadata is corrupt: {row['version_id']}") from exc
        persisted_resources.append({
            "logical_id": str(row["logical_id"]),
            "version_id": str(row["version_id"]),
            "metadata": metadata,
            "transaction_id": str(row["transaction_id"]),
        })
    resource_transactions = {
        str(record["transaction_id"]) for record in persisted_resources}
    persisted_transaction_keys = {
        transaction: str(row["idempotency_key"])
        for transaction in resource_transactions
        if (row := db.execute(
            "SELECT idempotency_key FROM transactions "
            "WHERE transaction_id=?", (transaction,)).fetchone())
        is not None
    }
    persisted_exact_refs: set[tuple[str, str, str]] = set()
    producer_values = [
        record["metadata"].get("producer_ref")
        for record in persisted_resources
        if isinstance(record.get("metadata"), Mapping)]
    producer_values.extend(
        item.metadata.get("producer_ref") for item in objects
        if item.object_type == "resource_version/v1"
        and str(item.logical_id) == resource_id)
    for value in producer_values:
        if not isinstance(value, Mapping):
            continue
        version = str(value.get("version_id", ""))
        row = db.execute(
            "SELECT object_type,logical_id,version_id FROM objects "
            "WHERE version_id=?", (version,)).fetchone()
        if row is not None:
            persisted_exact_refs.add((
                str(row["object_type"]), str(row["logical_id"]),
                str(row["version_id"])))
    persisted_exact_refs.update(
        ("resource_version/v1", resource_id,
         str(record["version_id"]))
        for record in persisted_resources)
    # A malformed already-committed sibling is corruption, never a
    # proposal conflict and never a later recovery-only surprise.
    if persisted_resources:
        try:
            canonical_producer_logical_set(
                persisted_resources,
                relation_records=persisted_records,
                transaction_keys=persisted_transaction_keys,
                registered_exact_refs=persisted_exact_refs,
            )
        except CanonicalProducerViolation as exc:
            raise RegistryCorruptError(
                f"persisted produced_by authority is corrupt: {resource_id}") from exc
    combined_resources = [*persisted_resources, *(
        {
            "logical_id": str(item.logical_id),
            "version_id": str(item.version_id),
            "metadata": dict(item.metadata),
            "transaction_id": str(transaction_id),
        }
        for item in objects
        if item.object_type == "resource_version/v1"
        and str(item.logical_id) == resource_id
    )]
    try:
        canonical = canonical_producer_logical_set(
            combined_resources,
            relation_records=(*persisted_records, *current_records),
            transaction_keys={
                **persisted_transaction_keys,
                str(transaction_id): idempotency_key,
            },
            registered_exact_refs=persisted_exact_refs | {
                (item.object_type, str(item.logical_id), str(item.version_id))
                for item in objects
            },
        )
    except CanonicalProducerViolation as exc:
        raise RegistryConflict(str(exc)) from exc
    expected_target = {
        "entity_type": str(producer_ref.get("entity_type", "")),
        "entity_id": str(producer_ref.get(
            "logical_id", producer_ref.get("entity_id", ""))),
        "version_id": str(producer_ref.get("version_id", "")),
    }
    selected = canonical.get(resource_version)
    selected_resource = next(
        (record for record in combined_resources
         if record["version_id"] == resource_version), None)
    if (selected is None or selected.get("target") != expected_target
            or selected_resource is None
            or selected_resource["transaction_id"] != publication_transaction):
        raise RegistryConflict(
            "resource produced_by authority differs from requested publication")
    return selected

def validate_direct_provider_raw_response(
        context,
        item: PreparedObject, metadata: Mapping[str, Any],
        origin: Mapping[str, Any], direct: Mapping[str, Any]) -> None:
    branch_id = context.branch_id
    events = context.events
    net_instance_id = context.net_instance_id
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS = facade._DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
    canonical_json = facade.canonical_json
    require_canonical_producer = partial(globals()["require_canonical_producer"], context)
    require_canonical_provider_lineage = partial(globals()["require_canonical_provider_lineage"], context)
    """Close a fresh raw provider response over exact authority."""
    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    binding_ref = direct.get("operation_binding_ref")
    loop_ref = direct.get("agent_loop_ref")
    input_refs = direct.get("input_resource_refs")
    producer_ref = metadata.get("producer_ref")
    invocation = (version_metadata(
        str(producer_ref.get("version_id", "")), "invocation/v1")
        if isinstance(producer_ref, Mapping) else None)
    binding = (version_metadata(
        str(binding_ref.get("version_id", "")), "operation_binding/v1")
        if isinstance(binding_ref, Mapping) else None)
    loop = (version_metadata(
        str(loop_ref.get("version_id", "")), str(loop_ref.get("entity_type", "")))
        if isinstance(loop_ref, Mapping) and exact_ref_exists(
            loop_ref, str(loop_ref.get("entity_type", ""))) else None)
    session_invocation = (loop.get("producer_ref")
        if loop is not None and loop_ref.get("entity_type") == "resource_version/v1"
        else loop.get("invocation_ref") if loop is not None else None)
    session_binding = (loop.get("reference_provenance", {}).get("operation_binding_ref")
        if loop is not None and loop_ref.get("entity_type") == "resource_version/v1"
        else loop.get("operation_binding_ref") if loop is not None else None)
    if (set(metadata) != _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
            or direct.get("schema_version")
            != "resource_reference_provenance/v1"
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(input_refs, list)
            or input_refs != sorted(input_refs, key=canonical_json)
            or len(input_refs) != len({
                canonical_json(value) for value in input_refs})
            or any(not exact_ref_exists(
                value, "resource_version/v1") for value in input_refs)
            or invocation is None or binding is None or loop is None
            or metadata.get("task_ref") != invocation.get("task_ref")
            or metadata.get("round_ref")
            != invocation.get("task_round_ref")
            or metadata.get("net_ref")
            != invocation.get("net_instance_ref")
            or binding_ref != invocation.get("operation_binding_ref")
            or session_invocation != producer_ref
            or session_binding != binding_ref
            or metadata.get("branch_id") != branch_id
            or metadata.get("task_ref", {}).get("logical_id")
            != str(task_id)
            or metadata.get("round_ref", {}).get("logical_id")
            != str(task_round_id)
            or metadata.get("net_ref", {}).get("logical_id")
            != str(net_instance_id)
            or item.producer_invocation_id is None
            or str(item.producer_invocation_id)
            != str(producer_ref.get("logical_id"))
            or publication != {
                "origin_kind": metadata.get("origin_kind"),
                "primary_ref": origin.get("primary_ref"),
                "secondary_ref": origin.get("secondary_ref"),
                "lifetime_ref": metadata.get("lifetime_ref"),
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": metadata.get("content_schema_ref"),
                "content_schema_authority_ref": metadata.get(
                    "content_schema_authority_ref"),
            }
            or consumer.get("consumer_ref") != loop_ref):
        raise RegistryConflict(
            "fresh provider resource direct authority is not exact")
    resource_ref = {
        "entity_type": "resource_version/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    }
    require_canonical_producer(
        resource_ref, producer_ref,
        publication_transaction=str(transaction_id))
    publications = [
        pending for pending in events
        if pending.event_type == "object_version_published/v1"
        and pending.payload.get("version_id") == str(item.version_id)]
    if (len(publications) != 1
            or publications[0].payload.get("metadata") != metadata
            or publications[0].payload.get("size") != item.size):
        raise RegistryConflict(
            "fresh provider resource lacks one exact publication fact")
    if metadata.get("origin_kind") != "provider_raw_response":
        raise RegistryConflict(
            "reference-only provenance is outside the fresh provider path")
    primary = origin.get("primary_ref")
    secondary = origin.get("secondary_ref")
    call_type = (str(secondary.get("entity_type", ""))
                 if isinstance(secondary, Mapping) else "")
    attempt = (version_metadata(
        str(primary.get("version_id", "")),
        "provider_attempt_spec/v1")
        if isinstance(primary, Mapping) else None)
    call = (version_metadata(
        str(secondary.get("version_id", "")), call_type)
        if isinstance(secondary, Mapping)
        and call_type in {"llm_call_spec/v2", "llm_call_spec/v3"}
        else None)
    observations = [
        pending for pending in events
        if pending.event_type
        == "provider_attempt_submission_observed/v1"
        and pending.payload.get("response_resource_ref") == {
            "resource_id": str(item.logical_id),
            "resource_version_id": str(item.version_id)}]
    response_relations = exact_relation_records(
        "attempt_produced_response",
        source_version=(str(primary.get("version_id", ""))
                        if isinstance(primary, Mapping) else ""))
    registered_host = call_type == "llm_call_spec/v3"
    consumer_exact = (
        registered_host
        and call is not None
        and call.get("invocation_kind") == "registered_host"
        and loop_ref == secondary
        and consumer.get("boundary") == "registered_host_response"
    ) or (
        call_type == "llm_call_spec/v2"
        and call is not None
        and call.get("agent_loop_ref") == loop_ref
        and consumer.get("boundary") == "agent_turn_decode"
    )
    if (attempt is None or call is None
            or primary.get("entity_type")
            != "provider_attempt_spec/v1"
            or call_type not in {"llm_call_spec/v2", "llm_call_spec/v3"}
            or attempt.get("llm_call_ref") != secondary
            or attempt.get("invocation_ref") != producer_ref
            or attempt.get("operation_binding_ref") != binding_ref
            or call.get("invocation_ref") != producer_ref
            or call.get("operation_binding_ref") != binding_ref
            or not consumer_exact
            or input_refs
            or metadata.get("content_schema_ref") is not None
            or metadata.get("content_schema_authority_ref") is not None
            or len(observations) != 1
            or observations[0].payload.get("provider_attempt_ref")
            != primary
            or observations[0].payload.get("llm_call_ref") != secondary
            or observations[0].payload.get("invocation_ref")
            != producer_ref
            or observations[0].payload.get("response_size")
            != item.size
            or len(response_relations) != 1
            or response_relations[0][1].get("version_id")
            != str(item.version_id)
            or response_relations[0][2] != str(transaction_id)
            or response_relations[0][3] != "strong"):
        raise RegistryConflict(
            "fresh raw provider response direct relations are not exact")
    require_canonical_provider_lineage(
        "attempt_of_call", primary, secondary)
    require_canonical_provider_lineage(
        "call_of_invocation", secondary, producer_ref)

def validate_provider_candidate_output(
        context,
        invocation: Mapping[str, Any], output_ref: Mapping[str, Any]) -> None:
    task_id = context.task_id
    disposition_records = context.disposition_records
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    version_metadata = context.version_metadata
    version_transaction = context.version_transaction
    RegistryConflict = facade.RegistryConflict
    canonical_json = facade.canonical_json
    require_canonical_attempt_response = partial(globals()["require_canonical_attempt_response"], context)
    require_canonical_producer = partial(globals()["require_canonical_producer"], context)
    require_canonical_provider_lineage = partial(globals()["require_canonical_provider_lineage"], context)
    """Close a main or leaf output over one exact adopted provider candidate.

        Provider candidates remain Registry content/provenance facts.  This
        validator only proves that a Petri output which uses such content
        has the approved exact lineage before terminal-ready/settlement.
        """
    invocation_ref = invocation.get("invocation_ref")
    if not isinstance(invocation_ref, Mapping):
        raise RegistryConflict(
            "provider candidate settlement lacks an exact invocation ref")
    invocation_id = str(invocation_ref.get("logical_id", ""))
    invocation_version = str(invocation_ref.get("version_id", ""))
    output_version = str(output_ref.get("version_id", ""))
    output = version_metadata(output_version, "resource_version/v1")
    output_origin = output.get("origin") if output is not None else None
    output_tx = version_transaction(output_version)
    if (output is None
            or not isinstance(output_origin, Mapping)
            or output.get("producer_ref") != invocation_ref
            or output.get("task_ref") != invocation.get("task_ref")
            or output.get("round_ref") != invocation.get("task_round_ref")
            or output.get("net_ref") != invocation.get("net_instance_ref")
            or output_tx is None):
        raise RegistryConflict(
            "provider-backed output lacks exact scope/provenance authority")
    require_canonical_producer(
        output_ref, invocation_ref,
        publication_transaction=output_tx)

    if invocation.get("origin") == "petri_operation":
        binding_ref = invocation.get("operation_binding_ref")
        binding = (version_metadata(
            str(binding_ref.get("version_id", "")), "operation_binding/v1")
            if isinstance(binding_ref, Mapping) else None)
        primary_ref = output_origin.get("primary_ref")
        output_binding = (version_metadata(
            str(primary_ref.get("version_id", "")), "output_binding/v1")
            if isinstance(primary_ref, Mapping) else None)
        declared = (binding.get("output_binding_refs", [])
                    if binding is not None else [])
        output_counts = {
            canonical_json(value): 0 for value in declared
            if isinstance(value, Mapping)
        }
        if (output.get("origin_kind") != "petri_output"
                or output_origin.get("kind") != "petri_output"
                or primary_ref not in declared
                or output_origin.get("secondary_ref")
                != invocation.get("activation_ref")
                or output_binding is None
                or output_binding.get("task_round_ref")
                != invocation.get("task_round_ref")
                or output_binding.get("net_ref")
                != invocation.get("net_instance_ref")
                or output_binding.get("node_ref")
                != invocation.get("own_node_ref")):
            raise RegistryConflict(
                "provider-backed Petri output lacks exact binding authority")
    else:
        raise RegistryConflict("provider-backed output has an unknown invocation origin")

    disposition_types = {
        "llm_call_result_adopted/v1",
        "llm_call_candidate_not_adopted/v1",
    }
    invocation_dispositions = []
    for record in disposition_records(disposition_types):
        event_type, disposition, producer_id, event_task_id, aggregate_id = record
        call_version = str(disposition.get("llm_call_version_id", ""))
        call = version_metadata(call_version, "llm_call_spec/v1")
        if (call is None
                or call.get("invocation_id") != invocation_id
                or call.get("invocation_version_id") != invocation_version):
            continue
        if (producer_id != invocation_id
                or event_task_id != str(task_id)
                or aggregate_id != str(disposition.get("llm_call_id", ""))):
            raise RegistryConflict(
                "provider candidate disposition differs from invocation authority")
        invocation_dispositions.append((event_type, disposition, call))

    adopted = [
        record for record in invocation_dispositions
        if record[0] == "llm_call_result_adopted/v1"
    ]
    derived_records = []
    for record in exact_relation_records(
            "derived_from", source_version=output_version):
        _source, target, _relation_tx, _strength = record
        target_metadata = version_metadata(
            str(target.get("version_id", "")), "resource_version/v1")
        if (target_metadata is not None
                and target_metadata.get("origin_kind") == "provider_response"):
            derived_records.append(record)

    if not adopted and not derived_records:
        return
    if len(derived_records) != 1:
        raise RegistryConflict(
            "terminal Petri output requires exactly one adopted provider candidate relation")
    source, candidate_ref, relation_tx, strength = derived_records[0]
    candidate_version = str(candidate_ref.get("version_id", ""))
    candidate = version_metadata(
        candidate_version, "resource_version/v1")
    if (source.get("entity_type") != "resource_version/v1"
            or source.get("version_id") != output_version
            or candidate is None
            or candidate_ref.get("entity_type") != "resource_version/v1"
            or not exact_ref_exists({
                "entity_type": candidate_ref.get("entity_type"),
                "logical_id": candidate_ref.get("entity_id"),
                "version_id": candidate_ref.get("version_id"),
            }, "resource_version/v1")
            or relation_tx != output_tx
            or strength != "strong"):
        raise RegistryConflict(
            "provider candidate derivation is not the output's atomic exact relation")

    origin = candidate.get("origin")
    if (candidate.get("origin_kind") != "provider_response"
            or not isinstance(origin, Mapping)
            or origin.get("kind") != "provider_response"):
        raise RegistryConflict(
            "terminal output lineage target is not a provider response")
    attempt_ref = origin.get("primary_ref")
    call_ref = origin.get("secondary_ref")
    if (not exact_ref_exists(attempt_ref, "provider_attempt_spec/v1")
            or not exact_ref_exists(call_ref, "llm_call_spec/v1")):
        raise RegistryConflict(
            "provider candidate origin lacks exact attempt/call refs")
    attempt_version = str(attempt_ref.get("version_id", ""))
    call_version = str(call_ref.get("version_id", ""))
    attempt = version_metadata(
        attempt_version, "provider_attempt_spec/v1")
    call = version_metadata(call_version, "llm_call_spec/v1")
    if (attempt is None or call is None
            or attempt.get("provider_attempt_id")
            != attempt_ref.get("logical_id")
            or attempt.get("provider_attempt_version_id")
            != attempt_version
            or attempt.get("llm_call_id") != call_ref.get("logical_id")
            or attempt.get("llm_call_version_id") != call_version
            or call.get("llm_call_id") != call_ref.get("logical_id")
            or call.get("llm_call_version_id") != call_version
            or attempt.get("invocation_id") != invocation_id
            or attempt.get("invocation_version_id") != invocation_version
            or call.get("invocation_id") != invocation_id
            or call.get("invocation_version_id") != invocation_version
            or candidate.get("producer_ref") != invocation_ref):
        raise RegistryConflict(
            "provider candidate is not the invocation's exact attempt/call chain")
    require_canonical_provider_lineage(
        "attempt_of_call", attempt_ref, call_ref)
    require_canonical_provider_lineage(
        "call_of_invocation", call_ref, invocation_ref)

    require_canonical_attempt_response(
        attempt_ref,
        {
            "entity_type": "resource_version/v1",
            "logical_id": candidate_ref.get("entity_id"),
            "version_id": candidate_version,
        },
    )
    require_canonical_producer(
        {
            "entity_type": "resource_version/v1",
            "logical_id": candidate_ref.get("entity_id"),
            "version_id": candidate_version,
        },
        invocation_ref,
        publication_transaction=str(version_transaction(candidate_version)),
    )

    matching_adoptions = [
        disposition for event_type, disposition, _call in adopted
        if disposition.get("response_version_id") == candidate_version
        and disposition.get("provider_attempt_version_id")
        == attempt_version
        and disposition.get("llm_call_id") == call_ref.get("logical_id")
        and disposition.get("llm_call_version_id") == call_version
        and disposition.get("selection_authority_ref")
        == output_origin.get("primary_ref")
    ]
    conflicting_dispositions = [
        disposition for event_type, disposition, _call
        in invocation_dispositions
        if event_type == "llm_call_candidate_not_adopted/v1"
        and disposition.get("response_version_id") == candidate_version
    ]
    if len(matching_adoptions) != 1 or conflicting_dispositions:
        raise RegistryConflict(
            "terminal Petri output does not derive from one exact adopted candidate")

def validate_provider_provenance(context):
    db = context.db
    objects = context.objects
    relations = context.relations
    transaction_id = context.transaction_id
    RegistryCorruptError = facade.RegistryCorruptError
    _ref_json = facade._ref_json
    require_canonical_producer = partial(globals()["require_canonical_producer"], context)
    require_canonical_provider_lineage = partial(globals()["require_canonical_provider_lineage"], context)
    def validate_produced_by_commit_boundary() -> None:
        """Close every resource logical identity touched by this command."""
        impacted = {
            str(item.logical_id) for item in objects
            if item.object_type == "resource_version/v1"
        }
        for relation in relations:
            if relation.relation_type != "produced_by":
                continue
            source = _ref_json(relation.source)
            logical_id = str(source.get("entity_id", ""))
            if (source.get("entity_type") == "resource_version/v1"
                    and logical_id):
                impacted.add(logical_id)

        for logical_id in sorted(impacted):
            impacted_versions = {
                str(item.version_id) for item in objects
                if item.object_type == "resource_version/v1"
                and str(item.logical_id) == logical_id
            }
            impacted_versions.update(
                str(getattr(relation.source, "version_id", ""))
                for relation in relations
                if relation.relation_type == "produced_by"
                and str(getattr(relation.source, "entity_id", ""))
                == logical_id)
            registered_rows = [dict(row) for row in db.execute(
                "SELECT logical_id,version_id,metadata_json,transaction_id "
                "FROM objects WHERE object_type='resource_version/v1' "
                "AND logical_id=?", (logical_id,)).fetchall()]
            resource_records: list[dict[str, Any]] = []
            for row in registered_rows:
                if str(row["logical_id"]) != logical_id:
                    continue
                try:
                    metadata = json.loads(str(row["metadata_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RegistryCorruptError(
                        f"persisted resource metadata is corrupt: {row['version_id']}") from exc
                resource_records.append({
                    "version_id": str(row["version_id"]),
                    "metadata": metadata,
                    "transaction_id": str(row["transaction_id"]),
                })
            resource_records.extend({
                "version_id": str(item.version_id),
                "metadata": dict(item.metadata),
                "transaction_id": str(transaction_id),
            } for item in objects
                if item.object_type == "resource_version/v1"
                and str(item.logical_id) == logical_id)
            for record in resource_records:
                if record["version_id"] not in impacted_versions:
                    continue
                metadata = record["metadata"]
                producer_ref = (metadata.get("producer_ref")
                                if isinstance(metadata, Mapping) else None)
                require_canonical_producer(
                    {
                        "entity_type": "resource_version/v1",
                        "logical_id": logical_id,
                        "version_id": record["version_id"],
                    },
                    producer_ref if isinstance(producer_ref, Mapping) else {},
                    publication_transaction=record["transaction_id"],
                )

    for item in objects:
        if item.object_type in {
                "llm_call_spec/v1", "llm_call_spec/v2",
                "llm_call_spec/v3"}:
            stored_invocation = item.metadata.get("invocation_ref")
            invocation_ref = (dict(stored_invocation)
                if isinstance(stored_invocation, Mapping) else {
                    "entity_type": "invocation/v1",
                    "logical_id": item.metadata.get("invocation_id"),
                    "version_id": item.metadata.get(
                        "invocation_version_id"),
                })
            require_canonical_provider_lineage(
                "call_of_invocation",
                {
                    "entity_type": item.object_type,
                    "logical_id": str(item.logical_id),
                    "version_id": str(item.version_id),
                },
                invocation_ref,
            )

    validate_produced_by_commit_boundary()

def validate_attempt_response_boundary(context):
    events = context.events
    idempotency_key = context.idempotency_key
    new_by_version = context.new_by_version
    relations = context.relations
    RegistryConflict = facade.RegistryConflict
    _ref_json = facade._ref_json
    def validate_attempt_response_commit_boundary() -> None:
        """Require every response relation to be born with its authority."""

        proposals = [
            relation for relation in relations
            if relation.relation_type == "attempt_produced_response"
        ]
        if not proposals:
            return
        proposal_publications = [
            pending for pending in events
            if pending.event_type == "relation_published/v1"
            and pending.payload.get("relation_type")
            == "attempt_produced_response"
        ]
        if len(proposal_publications) != len(proposals):
            raise RegistryConflict(
                "attempt response relations require one publication fact each")
        for relation in proposals:
            source = _ref_json(relation.source)
            target = _ref_json(relation.target)
            target_version = str(target.get("version_id", ""))
            proposed_target = new_by_version.get(target_version)
            if (set(source) != {"entity_type", "entity_id", "version_id"}
                    or set(target)
                    != {"entity_type", "entity_id", "version_id"}
                    or proposed_target is None
                    or proposed_target.object_type != "resource_version/v1"
                    or target.get("entity_type") != "resource_version/v1"
                    or str(proposed_target.logical_id)
                    != str(target.get("entity_id", ""))
                    or proposed_target.metadata.get("origin_kind") not in {
                        "provider_response", "provider_raw_response"}):
                raise RegistryConflict(
                    "attempt response relation must target a provider response "
                    "created in the same transaction")

            expected_publication = {
                "relation_id": str(relation.relation_id),
                "relation_type": relation.relation_type,
                "source": source,
                "target": target,
                "strength": relation.strength,
                "metadata": dict(relation.metadata),
            }
            publication_matches = [
                pending for pending in events
                if pending.event_type == "relation_published/v1"
                and str(pending.payload.get("relation_id", ""))
                == str(relation.relation_id)
            ]
            if (len(publication_matches) != 1
                    or dict(publication_matches[0].payload)
                    != expected_publication
                    or publication_matches[0].criticality != "authoritative"
                    or publication_matches[0].stream_id
                    != f"relation:{relation.relation_id}"
                    or publication_matches[0].aggregate_id
                    != str(relation.relation_id)
                    or publication_matches[0].aggregate_type
                    != "typed_relation/v1"
                    or publication_matches[0].idempotency_key
                    != idempotency_key
                    or publication_matches[0].command_id != idempotency_key
                    or publication_matches[0].payload_schema_ref
                    != "registry_v1/relation_published/v1"
                    or publication_matches[0].task_control
                    or publication_matches[0].causation_event_id is not None
                    or publication_matches[0].parent_event_ids
                    or publication_matches[0].producer_principal != "framework"
                    or publication_matches[0].producer_invocation_id
                    != relation.producer_invocation_id
                    or publication_matches[0].occurred_at is not None):
                raise RegistryConflict(
                    "attempt response relation lacks one exact publication fact")

    validate_attempt_response_commit_boundary()

def validate_provider_pre_resource_event(context, pending):
    db = context.db
    events = context.events
    payload = pending.payload
    event_type = pending.event_type
    has_relation = context.has_relation
    version_exists = context.version_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    require_canonical_attempt_response = partial(globals()["require_canonical_attempt_response"], context)
    require_canonical_provider_lineage = partial(globals()["require_canonical_provider_lineage"], context)
    if event_type in {
            "provider_attempt_owner_interrupted/v1",
            "llm_call_owner_interrupted/v1",
            "llm_invocation_owner_interrupted/v1"}:
        owner_event_types = {
            "provider_attempt_owner_interrupted/v1",
            "llm_call_owner_interrupted/v1",
            "llm_invocation_owner_interrupted/v1",
        }
        interruption_events = {
            kind: tuple(
                item for item in events if item.event_type == kind)
            for kind in owner_event_types
        }
        if any(len(values) != 1
               for values in interruption_events.values()):
            raise RegistryConflict(
                "LLM owner interruption lacks one atomic three-layer closure")
        neutral_event = interruption_events[
            "llm_invocation_owner_interrupted/v1"][0]
        call_event = interruption_events[
            "llm_call_owner_interrupted/v1"][0]
        provider_event = interruption_events[
            "provider_attempt_owner_interrupted/v1"][0]
        common = dict(neutral_event.payload)
        neutral_ref = common.get("llm_invocation_attempt_ref")
        llm_invocation_ref = common.get("llm_invocation_ref")
        provider_ref = common.get("provider_attempt_ref")
        call_ref = common.get("llm_call_ref")
        invocation_ref = common.get("invocation_ref")
        neutral = (version_metadata(
            str(neutral_ref.get("version_id", "")),
            "llm_invocation_attempt/v1")
            if isinstance(neutral_ref, Mapping) else None)
        llm_invocation = (version_metadata(
            str(llm_invocation_ref.get("version_id", "")),
            "llm_invocation_spec/v1")
            if isinstance(llm_invocation_ref, Mapping) else None)
        provider = (version_metadata(
            str(provider_ref.get("version_id", "")),
            "provider_attempt_spec/v1")
            if isinstance(provider_ref, Mapping) else None)
        call = (version_metadata(
            str(call_ref.get("version_id", "")),
            "llm_call_spec/v2")
            if isinstance(call_ref, Mapping) else None)
        invocation = (version_metadata(
            str(invocation_ref.get("version_id", "")),
            "invocation/v1")
            if isinstance(invocation_ref, Mapping) else None)
        conflicting_neutral_terminals = tuple(
            item for item in events
            if (item.aggregate_id == str(
                    neutral_ref.get("logical_id", ""))
                and item.event_type in {
                    "llm_invocation_failed/v1",
                    "llm_invocation_interrupted/v1",
                    "llm_invocation_succeeded/v1"}))
        persisted_neutral_terminals = db.execute(
            "SELECT event_type,payload_json,producer_invocation_id "
            "FROM events WHERE aggregate_id=? "
            "AND event_type IN ("
            "'llm_invocation_failed/v1',"
            "'llm_invocation_interrupted/v1',"
            "'llm_invocation_owner_interrupted/v1',"
            "'llm_invocation_succeeded/v1') ORDER BY ordinal",
            (str(neutral_ref.get("logical_id", "")),),
        ).fetchall()
        prior_failed_closure = False
        if len(persisted_neutral_terminals) == 1:
            prior = persisted_neutral_terminals[0]
            try:
                prior_payload = json.loads(str(prior["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                prior_payload = None
            prior_failed_closure = bool(
                prior["event_type"] == "llm_invocation_failed/v1"
                and isinstance(prior_payload, Mapping)
                and prior_payload.get("llm_invocation_ref")
                == llm_invocation_ref
                and prior_payload.get("llm_invocation_attempt_ref")
                == neutral_ref
                and prior_payload.get("next_attempt_allowed") is False
                and prior_payload.get("submission_state")
                == common.get("submission_state")
                and str(prior["producer_invocation_id"])
                == str(invocation_ref.get("logical_id", "")))
        if (dict(call_event.payload) != common
                or dict(provider_event.payload) != common
                or neutral_event.aggregate_type
                != "llm_invocation_attempt"
                or neutral_event.aggregate_id
                != str(neutral_ref.get("logical_id", ""))
                or call_event.aggregate_type != "llm_call"
                or call_event.aggregate_id
                != str(call_ref.get("logical_id", ""))
                or provider_event.aggregate_type != "provider_attempt"
                or provider_event.aggregate_id
                != str(provider_ref.get("logical_id", ""))
                or any(item.producer_invocation_id
                       != neutral_event.producer_invocation_id
                       for item in (call_event, provider_event))
                or neutral_event.producer_invocation_id is None
                or str(neutral_event.producer_invocation_id)
                != str(invocation_ref.get("logical_id", ""))
                or neutral is None
                or llm_invocation is None
                or provider is None
                or call is None
                or invocation is None
                or neutral.get("llm_invocation_attempt_ref")
                != neutral_ref
                or neutral.get("llm_invocation_ref")
                != llm_invocation_ref
                or llm_invocation.get("llm_invocation_ref")
                != llm_invocation_ref
                or llm_invocation.get("invocation_ref")
                != invocation_ref
                or provider.get("provider_attempt_ref") != provider_ref
                or provider.get("llm_call_ref") != call_ref
                or provider.get("invocation_ref") != invocation_ref
                or call.get("llm_call_ref") != call_ref
                or call.get("invocation_ref") != invocation_ref
                or provider.get("operation_binding_ref")
                != call.get("operation_binding_ref")
                or llm_invocation.get("operation_binding_ref")
                != call.get("operation_binding_ref")
                or not has_relation(
                    "derived_from",
                    str(neutral_ref.get("version_id", "")),
                    str(provider_ref.get("version_id", "")))
                or conflicting_neutral_terminals
                or (persisted_neutral_terminals
                    and not prior_failed_closure)):
            raise RegistryConflict(
                "LLM owner interruption differs from its exact current authority")

    if event_type == "llm_call_submission_unknown/v1":
        call_ref = payload.get("llm_call_ref", {})
        call_id = str(call_ref.get("logical_id", ""))
        call_version = str(call_ref.get("version_id", ""))
        call_type = str(call_ref.get("entity_type", ""))
        if call_type != "llm_call_spec/v2":
            raise RegistryConflict(
                "LLM-call submission unknown has an invalid call type")
        call = version_metadata(call_version, call_type)
        attempt_unknowns = [
            item for item in events
            if (item.event_type
                == "provider_attempt_submission_unknown/v1")
        ]
        if (pending.aggregate_id != call_id
                or call is None
                or call.get("llm_call_id") != call_id
                or call.get("llm_call_version_id") != call_version
                or call.get("invocation_ref")
                != payload.get("invocation_ref")
                or call.get("operation_binding_ref")
                != payload.get("operation_binding_ref")
                or call.get("request_resource_ref")
                != payload.get("request_resource_ref")
                or call.get("terminal_delivery_ref")
                != payload.get("terminal_delivery_ref")
                or len(attempt_unknowns) != 1
                or dict(attempt_unknowns[0].payload) != dict(payload)):
            raise RegistryConflict(
                "LLM-call submission unknown lacks its exact atomic "
                "attempt closure")

    if event_type in {
            "llm_call_result_adopted/v1",
            "llm_call_candidate_not_adopted/v1",
            "llm_call_failed/v1"}:
        call_id = str(payload.get("llm_call_id", ""))
        call_version = str(payload.get("llm_call_version_id", ""))
        attempt_meta = None
        if event_type != "llm_call_failed/v1":
            attempt_version = str(payload.get(
                "provider_attempt_version_id", ""))
            attempt_row = db.execute(
                "SELECT metadata_json FROM objects WHERE version_id=? "
                "AND object_type='provider_attempt_spec/v1'",
                (attempt_version,)).fetchone()
            attempt_meta = (json.loads(attempt_row["metadata_json"])
                            if attempt_row else None)
        call_type = "llm_call_spec/v1"
        attempt_call_ref = (
            attempt_meta.get("llm_call_ref")
            if isinstance(attempt_meta, Mapping) else None)
        if (isinstance(attempt_call_ref, Mapping)
                and attempt_call_ref.get("entity_type")
                == "llm_call_spec/v2"
                and attempt_call_ref.get("version_id") == call_version):
            call_type = "llm_call_spec/v2"
        elif (event_type == "llm_call_failed/v1"
              and any(item.event_type.startswith("provider_attempt_")
                    and item.payload.get("llm_call_ref", {}).get(
                        "entity_type") == "llm_call_spec/v2"
                    and item.payload.get("llm_call_ref", {}).get(
                        "version_id") == call_version
                    for item in events)):
            call_type = "llm_call_spec/v2"
        elif (event_type == "llm_call_failed/v1"
              and version_metadata(
                  call_version, "llm_call_spec/v2") is not None):
            call_type = "llm_call_spec/v2"
        call_row = db.execute(
            "SELECT logical_id FROM objects WHERE version_id=? "
            "AND object_type=?", (call_version, call_type)).fetchone()
        if call_row is None or call_row["logical_id"] != call_id:
            raise RegistryConflict(
                "LLM-call disposition has no exact logical call version")
        if event_type != "llm_call_failed/v1":
            response_version = str(payload.get("response_version_id", ""))
            call_meta = version_metadata(call_version, call_type)
            response_meta = version_metadata(
                response_version, "resource_version/v1")
            response_origin = (
                response_meta.get("origin")
                if isinstance(response_meta, Mapping) else None)
            raw_v2 = bool(
                call_type == "llm_call_spec/v2"
                and isinstance(response_meta, Mapping)
                and response_meta.get("origin_kind")
                == "provider_raw_response"
                and isinstance(response_origin, Mapping)
                and response_origin.get("primary_ref") == {
                    "entity_type": "provider_attempt_spec/v1",
                    "logical_id": attempt_meta.get(
                        "provider_attempt_id"),
                    "version_id": attempt_version,
                }
                and response_origin.get("secondary_ref") == {
                    "entity_type": call_type,
                    "logical_id": call_id,
                    "version_id": call_version,
                })
            if (attempt_meta is None
                    or call_meta is None
                    or attempt_meta.get("llm_call_id") != call_id
                    or attempt_meta.get("llm_call_version_id") != call_version
                    or not version_exists(response_version, "resource_version/v1")
                    or (not raw_v2 and not has_relation(
                        "attempt_produced_response", attempt_version,
                        response_version))):
                raise RegistryConflict(
                    "LLM-call candidate disposition is not closed over exact refs")
            attempt_ref = {
                "entity_type": "provider_attempt_spec/v1",
                "logical_id": attempt_meta.get("provider_attempt_id"),
                "version_id": attempt_version,
            }
            call_ref = {
                "entity_type": call_type,
                "logical_id": call_id,
                "version_id": call_version,
            }
            invocation_ref = call_meta.get("invocation_ref")
            if not isinstance(invocation_ref, Mapping):
                invocation_ref = {
                    "entity_type": "invocation/v1",
                    "logical_id": call_meta.get("invocation_id"),
                    "version_id": call_meta.get(
                        "invocation_version_id"),
                }
            require_canonical_provider_lineage(
                "attempt_of_call", attempt_ref, call_ref)
            require_canonical_provider_lineage(
                "call_of_invocation", call_ref, invocation_ref)
            response_row = db.execute(
                "SELECT logical_id FROM objects WHERE version_id=? "
                "AND object_type='resource_version/v1'",
                (response_version,),
            ).fetchone()
            if response_row is None:
                raise RegistryConflict(
                    "LLM-call candidate has no exact response object")
            if raw_v2:
                observed_rows = db.execute(
                    "SELECT event_id,payload_json FROM events "
                    "WHERE aggregate_id=? AND event_type="
                    "'provider_attempt_submission_observed/v1'",
                    (str(attempt_meta["provider_attempt_id"]),),
                ).fetchall()
                observations = [
                    (str(row["event_id"]),
                     json.loads(row["payload_json"]))
                    for row in observed_rows
                    if json.loads(row["payload_json"]).get(
                        "response_resource_ref", {}).get(
                            "resource_version_id")
                        == response_version]
                persisted_completions = [
                    json.loads(row["payload_json"])
                    for row in db.execute(
                        "SELECT payload_json FROM events "
                        "WHERE aggregate_id=? AND event_type="
                        "'provider_attempt_completed/v1'",
                        (str(attempt_meta[
                            "provider_attempt_id"]),)).fetchall()
                    if json.loads(row["payload_json"]).get(
                        "response_version_id") == response_version]
                proposed_completions = [
                    dict(item.payload) for item in events
                    if (item.event_type
                        == "provider_attempt_completed/v1"
                        and item.aggregate_id == str(attempt_meta[
                            "provider_attempt_id"])
                        and item.payload.get("response_version_id")
                        == response_version)]
                completions = [
                    *persisted_completions, *proposed_completions]
                if (len(observations) != 1
                        or len(completions) != 1
                        or completions[0].get(
                            "response_observed_event_id")
                        != observations[0][0]):
                    raise RegistryConflict(
                        "v2 raw candidate lacks exact terminal closure")
            else:
                require_canonical_attempt_response(
                    attempt_ref,
                    {
                        "entity_type": "resource_version/v1",
                        "logical_id": str(response_row["logical_id"]),
                        "version_id": response_version,
                    },
                )

def validate_provider_attempt_event(context, pending):
    db = context.db
    payload = pending.payload
    event_type = pending.event_type
    events = context.events
    new_by_version = context.new_by_version
    exact_ref_exists = context.exact_ref_exists
    has_relation = context.has_relation
    object_metadata = context.object_metadata
    version_exists = context.version_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    require_canonical_provider_lineage = partial(globals()["require_canonical_provider_lineage"], context)
    if event_type == "provider_attempt_host_closed/v1":
        return
    if event_type.startswith("provider_attempt_"):
        attempt_id = str(payload["provider_attempt_id"])
        attempt = object_metadata(attempt_id, "provider_attempt_spec/v1")
        if pending.aggregate_id != attempt_id or attempt is None:
            raise RegistryConflict("provider lifecycle has no exact attempt spec")
        if event_type == "provider_attempt_dispatch_started/v2":
            receipt_ref = payload.get(
                "provider_payload_materialization_receipt_ref", {})
            receipt = version_metadata(
                str(receipt_ref.get("version_id", "")),
                "provider_payload_materialization_receipt/v1")
            if (receipt is None
                    or receipt.get(
                        "provider_payload_materialization_receipt_ref")
                    != receipt_ref
                    or receipt.get("provider_attempt_ref")
                    != attempt.get("provider_attempt_ref")
                    or receipt.get("llm_call_ref")
                    != attempt.get("llm_call_ref")
                    or receipt.get("request_recipe_ref")
                    != attempt.get("request_resource_ref")
                    or receipt.get("terminal_delivery_ref")
                    != attempt.get("terminal_delivery_ref")
                    or payload.get("request_payload_byte_count")
                    != receipt.get("byte_count")):
                raise RegistryConflict(
                    "provider dispatch lacks its exact materialization receipt")
        if event_type == "provider_attempt_reserved/v1":
            if (str(attempt.get("provider_attempt_version_id"))
                    != str(payload["provider_attempt_version_id"])
                    or attempt.get("reservation_class") != payload["reservation_class"]
                    or bool(attempt.get("finalization_scope"))
                    != bool(payload["finalization_scope"])):
                raise RegistryConflict("provider reservation does not match attempt spec")
            invocation_version = str(attempt.get("invocation_version_id", ""))
            invocation_row = db.execute(
                "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
                "AND object_type='invocation/v1'", (invocation_version,)).fetchone()
            call_version = str(attempt.get("llm_call_version_id", ""))
            call_ref = attempt.get("llm_call_ref")
            call_type = (str(call_ref.get("entity_type"))
                         if isinstance(call_ref, Mapping) else "")
            if call_type not in {
                    "llm_call_spec/v1", "llm_call_spec/v2",
                    "llm_call_spec/v3"}:
                raise RegistryConflict(
                    "provider reservation has an invalid call type")
            call_row = db.execute(
                "SELECT metadata_json FROM objects WHERE version_id=? "
                "AND object_type=?", (call_version, call_type)).fetchone()
            if invocation_row is None or call_row is None:
                raise RegistryConflict(
                    "provider reservation requires exact invocation and call versions")
            invocation = json.loads(invocation_row["metadata_json"])
            call = json.loads(call_row["metadata_json"])
            binding_ref = invocation.get("operation_binding_ref")
            if not isinstance(binding_ref, Mapping):
                raise RegistryConflict(
                    "provider invocation lacks an exact operation binding")
            binding_row = db.execute(
                "SELECT metadata_json FROM objects WHERE version_id=? "
                "AND object_type='operation_binding/v1'",
                (str(binding_ref.get("version_id", "")),)).fetchone()
            binding = json.loads(binding_row["metadata_json"]) if binding_row else None
            finalization_scope = invocation.get("finalization_scope")
            exact = {
                "invocation_id": invocation_row["logical_id"],
                "invocation_version_id": invocation_version,
                "context_digest": invocation.get("context_digest"),
            }
            accounting_ref = invocation.get(
                "accounting_parent_invocation_ref")
            if call_type == "llm_call_spec/v1":
                exact_call = {
                    **exact,
                    "origin": invocation.get("origin"),
                    "accounting_parent_invocation_id": (
                        accounting_ref.get("logical_id")
                        if isinstance(accounting_ref, Mapping)
                        else None),
                    "accounting_parent_invocation_version_id": (
                        accounting_ref.get("version_id")
                        if isinstance(accounting_ref, Mapping)
                        else None),
                    "budget_scope": invocation.get("budget_scope"),
                    "finalization_scope": finalization_scope,
                }
            elif call_type == "llm_call_spec/v2":
                exact_call = {
                    "invocation_ref": invocation.get("invocation_ref"),
                    "operation_binding_ref": invocation.get(
                        "operation_binding_ref"),
                    "context_digest": invocation.get("context_digest"),
                }
            else:
                exact_call = {
                    "invocation_kind": "registered_host",
                    "invocation_ref": invocation.get("invocation_ref"),
                    "operation_binding_ref": invocation.get(
                        "operation_binding_ref"),
                }
            if (binding is None
                    or any(attempt.get(key) != value
                           for key, value in exact.items())
                    or any(call.get(key) != value
                           for key, value in exact_call.items())
                    or binding.get("budget_scope") != invocation.get("budget_scope")
                    or binding.get("finalization_scope") != finalization_scope
                    or attempt.get("reservation_class")
                    != invocation.get("budget_scope")
                    or attempt.get("finalization_scope") != finalization_scope
                    or bool(payload["finalization_scope"])
                    != (finalization_scope is not None)
                    or attempt.get("origin") != invocation.get("origin")):
                raise RegistryConflict(
                    "provider budget classification is not canonical invocation scope")
            if not isinstance(accounting_ref, Mapping):
                raise RegistryConflict(
                    "provider invocation lacks exact accounting parent")
            if (attempt.get("accounting_parent_invocation_id")
                    != accounting_ref.get("logical_id")
                    or attempt.get("accounting_parent_invocation_version_id")
                    != accounting_ref.get("version_id")):
                raise RegistryConflict(
                    "provider attempt accounting parent is not canonical")
            if invocation.get("origin") == "petri_operation":
                if accounting_ref != invocation.get("invocation_ref"):
                    raise RegistryConflict(
                        "Petri provider attempt changed accounting parent")
            else:
                raise RegistryConflict("provider attempt has an invalid origin")
            attempt_version = str(attempt["provider_attempt_version_id"])
            require_canonical_provider_lineage(
                "attempt_of_call",
                {
                    "entity_type": "provider_attempt_spec/v1",
                    "logical_id": attempt.get("provider_attempt_id"),
                    "version_id": attempt_version,
                },
                {
                    "entity_type": call_type,
                    "logical_id": attempt.get("llm_call_id"),
                    "version_id": attempt.get("llm_call_version_id"),
                },
            )
            required_relations = {
                "bound_to_backend": str(attempt["backend_config_version_id"]),
                "governed_by_call_policy": str(
                    attempt["budget_witness_version_id"]),
            }
            if any(not has_relation(kind, attempt_version, target)
                   for kind, target in required_relations.items()):
                raise RegistryConflict(
                    "provider reservation is missing an exact governed relation")
        if event_type in {
                "provider_attempt_submission_permitted/v1",
                "provider_attempt_submission_permitted/v2",
                "provider_attempt_submission_not_permitted/v1"}:
            dispatch_row = db.execute(
                "SELECT aggregate_id,event_type,payload_json FROM events "
                "WHERE event_id=? AND event_type IN "
                "('provider_attempt_dispatch_started/v1',"
                "'provider_attempt_dispatch_started/v2')",
                (str(payload.get("dispatch_event_id", "")),),
            ).fetchone()
            dispatch_payload = (json.loads(dispatch_row["payload_json"])
                                if dispatch_row is not None else None)
            if (dispatch_row is None
                    or dispatch_row["aggregate_id"] != attempt_id
                    or not isinstance(dispatch_payload, Mapping)
                    or dispatch_payload.get("provider_attempt_ref")
                    != attempt.get("provider_attempt_ref")
                    or dispatch_payload.get("llm_call_ref")
                    != attempt.get("llm_call_ref")
                    or dispatch_payload.get("invocation_ref")
                    != attempt.get("invocation_ref")
                    or (event_type
                        == "provider_attempt_submission_permitted/v1"
                        and dispatch_row["event_type"]
                        != "provider_attempt_dispatch_started/v1")
                    or (event_type
                        == "provider_attempt_submission_permitted/v2"
                        and dispatch_row["event_type"]
                        != "provider_attempt_dispatch_started/v2")):
                raise RegistryConflict(
                    "provider permit closure lacks its exact dispatch fact")
            if event_type in {
                    "provider_attempt_submission_permitted/v1",
                    "provider_attempt_submission_permitted/v2"}:
                current_materialization = (
                    event_type
                    == "provider_attempt_submission_permitted/v2")
                invocation_ref = attempt.get("invocation_ref", {})
                invocation = version_metadata(
                    str(invocation_ref.get("version_id", "")),
                    "invocation/v1") if isinstance(
                        invocation_ref, Mapping) else None
                lease_ref = payload.get(
                    "operation_execution_lease_ref", {})
                start_row = db.execute(
                    "SELECT aggregate_id,payload_json FROM events "
                    "WHERE event_id=? AND event_type="
                    "'operation_execution_started/v1'",
                    (str(payload.get("operation_start_event_id", "")),),
                ).fetchone()
                start_payload = (
                    json.loads(start_row["payload_json"])
                    if start_row is not None else None)
                if (invocation is None
                        or not isinstance(lease_ref, Mapping)
                        or invocation.get(
                            "operation_execution_lease_ref") != lease_ref
                        or start_row is None
                        or start_row["aggregate_id"]
                        != str(lease_ref.get("logical_id", ""))
                        or not isinstance(start_payload, Mapping)
                        or start_payload.get("invocation_ref")
                        != attempt.get("invocation_ref")
                        or start_payload.get(
                            "operation_execution_lease_ref") != lease_ref
                        or payload.get("provider_attempt_ref")
                        != attempt.get("provider_attempt_ref")
                        or payload.get("llm_call_ref")
                        != attempt.get("llm_call_ref")
                        or payload.get("invocation_ref")
                        != attempt.get("invocation_ref")
                        or (current_materialization and (
                            payload.get("request_payload_byte_count")
                            != dispatch_payload.get(
                                "request_payload_byte_count")))
                        or payload.get("response_protocol")
                        != attempt.get("response_protocol")
                        or payload.get(
                            "lifecycle_observation_event_id")
                        != payload.get("dispatch_event_id")):
                    raise RegistryConflict(
                        "provider submission permit receipt is not exact")
        proof = payload.get("proof_resource_version_id")
        if proof is not None and not version_exists(str(proof), "resource_version/v1"):
            raise RegistryConflict("provider reconciliation proof is not registered")
        response = payload.get("response_version_id")
        if event_type in {
                "provider_attempt_completed/v1",
                "provider_attempt_reconciled_completed/v1"}:
            response_object = new_by_version.get(str(response))
            response_resource_metadata = (
                dict(response_object.metadata)
                if response_object is not None
                and response_object.object_type == "resource_version/v1"
                else version_metadata(
                    str(response), "resource_version/v1"))
            response_origin = (
                response_resource_metadata.get("origin")
                if response_resource_metadata is not None else None)
            raw_response = bool(
                isinstance(response_resource_metadata, Mapping)
                and response_resource_metadata.get("origin_kind")
                == "provider_raw_response")
            if (response_resource_metadata is None
                    or response_resource_metadata.get("origin_kind")
                    not in {"provider_response", "provider_raw_response"}
                    or not isinstance(response_origin, Mapping)
                    or response_origin.get("primary_ref", {}).get("logical_id")
                    != attempt_id
                    or response_origin.get("primary_ref", {}).get("version_id")
                    != str(attempt["provider_attempt_version_id"])
                    or response_origin.get("secondary_ref", {}).get("logical_id")
                    != str(attempt["llm_call_id"])
                    or response_origin.get("secondary_ref", {}).get("version_id")
                    != str(attempt["llm_call_version_id"])
                    or (not raw_response and not has_relation(
                        "attempt_produced_response",
                        str(attempt["provider_attempt_version_id"]),
                        str(response)))):
                raise RegistryConflict(
                    "provider completion lacks its previously observed response origin")
            if event_type == "provider_attempt_completed/v1":
                observed_rows = db.execute(
                    "SELECT event_id,payload_json FROM events "
                    "WHERE aggregate_id=? AND event_type="
                    "'provider_attempt_submission_observed/v1'",
                    (attempt_id,),
                ).fetchall()
                observed_payloads = [(
                    row["event_id"], json.loads(row["payload_json"]))
                    for row in observed_rows
                ]
                matches = [
                    item for event_id, item in observed_payloads
                    if (event_id == payload.get(
                            "response_observed_event_id")
                        and item.get("response_resource_ref", {}).get(
                        "resource_version_id") == str(response)
                        and item.get("finish_reason")
                        == payload.get("finish_reason")
                        and item.get("external_request_id")
                        == payload.get(
                            "external_request_id"))
                ]
                if len(matches) != 1:
                    raise RegistryConflict(
                        "provider completion does not consume one exact "
                        "durable response observation")
        elif response is not None and not version_exists(
                str(response), "resource_version/v1"):
            raise RegistryConflict("provider response version is not registered")
        if event_type == "provider_attempt_submission_observed/v1":
            response_ref = payload.get("response_resource_ref", {})
            response_version = str(response_ref.get(
                "resource_version_id", ""))
            response_metadata = version_metadata(
                response_version, "resource_version/v1")
            dispatch_row = db.execute(
                "SELECT aggregate_id,payload_json FROM events "
                "WHERE event_id=? AND event_type IN "
                "('provider_attempt_dispatch_started/v1',"
                "'provider_attempt_dispatch_started/v2')",
                (str(payload.get("dispatch_event_id", "")),),
            ).fetchone()
            material = {
                "provider_attempt_ref": payload.get(
                    "provider_attempt_ref"),
                "llm_call_ref": payload.get("llm_call_ref"),
                "invocation_ref": payload.get("invocation_ref"),
                "dispatch_event_id": payload.get("dispatch_event_id"),
                "response_resource_ref": response_ref,
                "response_size": payload.get("response_size"),
                "finish_reason": payload.get("finish_reason"),
                "external_request_id": payload.get(
                    "external_request_id"),
            }
            origin = (response_metadata.get("origin")
                      if isinstance(response_metadata, Mapping)
                      else None)
            raw_response = bool(
                isinstance(response_metadata, Mapping)
                and response_metadata.get("origin_kind")
                == "provider_raw_response")
            response_extensions = (
                response_metadata.get("extensions")
                if isinstance(response_metadata, Mapping) else None)
            response_facts = (
                response_extensions.get(
                    "registry.provider_raw_response/v2")
                if isinstance(response_extensions, Mapping) else None)
            status_code = (
                response_facts.get("status_code")
                if isinstance(response_facts, Mapping) else None)
            successful_raw_response = bool(
                raw_response
                and isinstance(status_code, int)
                and not isinstance(status_code, bool)
                and 200 <= status_code < 300)
            if (payload.get("provider_attempt_version_id")
                    != attempt.get("provider_attempt_version_id")
                    or payload.get("provider_attempt_ref")
                    != attempt.get("provider_attempt_ref")
                    or payload.get("llm_call_ref")
                    != attempt.get("llm_call_ref")
                    or payload.get("invocation_ref")
                    != attempt.get("invocation_ref")
                    or response_metadata is None
                    or response_metadata.get("size")
                    != payload.get("response_size")
                    or response_metadata.get("origin_kind") not in {
                        "provider_response", "provider_raw_response"}
                    or not isinstance(origin, Mapping)
                    or origin.get("primary_ref")
                    != payload.get("provider_attempt_ref")
                    or origin.get("secondary_ref")
                    != payload.get("llm_call_ref")
                    or dispatch_row is None
                    or dispatch_row["aggregate_id"] != attempt_id
                    or (not raw_response and not has_relation(
                        "attempt_produced_response",
                        str(attempt["provider_attempt_version_id"]),
                        response_version))):
                raise RegistryConflict(
                    "provider response observation lacks exact durable "
                    "dispatch/response lineage")
            if ((not raw_response or successful_raw_response)
                    and not has_relation(
                        "actual_model_call_recorded_against_limit",
                        str(attempt[
                            "provider_attempt_version_id"]),
                        str(attempt[
                            "budget_witness_version_id"]))):
                raise RegistryConflict(
                    "successful provider result lacks actual-call accounting")
        if (event_type == "provider_attempt_submission_unknown/v1"
                and not has_relation(
                    "submission_unknown_escrowed_by_budget",
                    str(attempt["provider_attempt_version_id"]),
                    str(attempt["budget_witness_version_id"]))):
            raise RegistryConflict(
                "unknown provider submission is missing its budget escrow")
        if event_type == "provider_attempt_submission_unknown/v1":
            exact_unknown_fields = (
                "provider_attempt_ref", "llm_call_ref",
                "invocation_ref", "operation_binding_ref",
                "llm_execution_target_ref", "request_resource_ref",
                "terminal_delivery_ref", "dispatch_event_id",
                "diagnostic_resource_ref", "uncertainty_kind",
            )
            call_unknowns = [
                item for item in events
                if (item.event_type
                    == "llm_call_submission_unknown/v1")
            ]
            dispatch_row = db.execute(
                "SELECT aggregate_id,payload_json FROM events "
                "WHERE event_id=? AND event_type="
                "'provider_attempt_dispatch_started/v2'",
                (str(payload.get("dispatch_event_id", "")),),
            ).fetchone()
            dispatch_payload = (json.loads(
                dispatch_row["payload_json"])
                if dispatch_row is not None else None)
            unknown_ref = payload.get(
                "provider_submission_unknown_ref", {})
            unknown = version_metadata(
                str(unknown_ref.get("version_id", "")),
                "provider_submission_unknown/v1")
            diagnostic = payload.get("diagnostic_resource_ref")
            exact_from_attempt = {
                "provider_attempt_ref": attempt.get(
                    "provider_attempt_ref"),
                "llm_call_ref": attempt.get("llm_call_ref"),
                "invocation_ref": attempt.get("invocation_ref"),
                "operation_binding_ref": attempt.get(
                    "operation_binding_ref"),
                "llm_execution_target_ref": attempt.get(
                    "llm_execution_target_ref"),
                "request_resource_ref": attempt.get(
                    "request_resource_ref"),
                "terminal_delivery_ref": attempt.get(
                    "terminal_delivery_ref"),
            }
            if (payload.get("provider_attempt_version_id")
                    != attempt.get("provider_attempt_version_id")
                    or any(payload.get(name) != value
                           for name, value
                           in exact_from_attempt.items())
                    or payload.get("uncertainty_kind")
                    != "possibly_submitted_response_unknown"
                    or unknown is None
                    or not exact_ref_exists(
                        unknown_ref,
                        "provider_submission_unknown/v1")
                    or unknown.get("provider_submission_unknown_id")
                    != unknown_ref.get("logical_id")
                    or unknown.get(
                        "provider_submission_unknown_version_id")
                    != unknown_ref.get("version_id")
                    or unknown.get("provider_submission_unknown_ref")
                    != unknown_ref
                    or unknown.get("provider_attempt_ref")
                    != payload.get("provider_attempt_ref")
                    or unknown.get("llm_call_ref")
                    != payload.get("llm_call_ref")
                    or any(unknown.get(name) != payload.get(name)
                           for name in exact_unknown_fields
                           if name not in {
                               "provider_attempt_ref", "llm_call_ref"})
                    or (diagnostic is not None and not version_exists(
                        str(diagnostic.get(
                            "resource_version_id", "")),
                        "resource_version/v1"))
                    or dispatch_row is None
                    or dispatch_row["aggregate_id"] != attempt_id
                    or dispatch_payload is None
                    or any(dispatch_payload.get(name)
                           != payload.get(name)
                           for name in exact_from_attempt)
                    or len(call_unknowns) != 1
                    or dict(call_unknowns[0].payload) != dict(payload)
                    or call_unknowns[0].aggregate_id
                    != str(attempt.get("llm_call_id", ""))
                    or call_unknowns[0].producer_invocation_id
                    != pending.producer_invocation_id):
                raise RegistryConflict(
                    "provider submission unknown lacks one exact atomic "
                    "dispatch/attempt/call closure")

def validate_provider_outcome_event(context, pending):
    db = context.db
    events = context.events
    payload = pending.payload
    event_type = pending.event_type
    exact_ref_exists = context.exact_ref_exists
    object_metadata = context.object_metadata
    version_exists = context.version_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    require_canonical_attempt_response = partial(globals()["require_canonical_attempt_response"], context)
    if event_type == "llm_call_result_adopted/v1":
        selection_ref = payload.get("selection_authority_ref")
        selection = (version_metadata(
            str(selection_ref.get("version_id", "")),
            "output_binding/v1")
            if isinstance(selection_ref, Mapping) else None)
        attempt_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='provider_attempt_spec/v1'",
            (str(payload["provider_attempt_version_id"]),)).fetchone()
        attempt = json.loads(attempt_row["metadata_json"]) if attempt_row else None
        call_type = (
            attempt.get("llm_call_ref", {}).get("entity_type")
            if isinstance(attempt, Mapping) else None)
        if call_type not in {"llm_call_spec/v1", "llm_call_spec/v2"}:
            call_type = "llm_call_spec/v1"
        call = object_metadata(
            str(payload["llm_call_id"]), call_type)
        response_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='resource_version/v1'",
            (str(payload["response_version_id"]),)).fetchone()
        response_meta = (json.loads(response_row["metadata_json"])
                         if response_row else None)
        response_origin = (
            response_meta.get("origin")
            if isinstance(response_meta, Mapping) else None)
        raw_v2 = bool(
            call_type == "llm_call_spec/v2"
            and isinstance(response_meta, Mapping)
            and response_meta.get("origin_kind")
            == "provider_raw_response"
            and isinstance(response_origin, Mapping)
            and response_origin.get("primary_ref", {}).get(
                "version_id")
            == str(payload["provider_attempt_version_id"])
            and response_origin.get("secondary_ref") == {
                "entity_type": call_type,
                "logical_id": str(payload["llm_call_id"]),
                "version_id": str(payload["llm_call_version_id"]),
            })
        if (pending.aggregate_id != str(payload["llm_call_id"])
                or call is None
                or call.get("llm_call_version_id") != payload["llm_call_version_id"]
                or attempt is None
                or attempt.get("llm_call_id") != payload["llm_call_id"]
                or not version_exists(str(payload["response_version_id"]),
                                      "resource_version/v1")
                or response_row is None
                or selection is None
                or not exact_ref_exists(
                    selection_ref, "output_binding/v1")):
            raise RegistryConflict("LLM call adoption contains an invalid exact ref")
        operation_binding_ref = call.get("operation_binding_ref")
        operation_binding = (version_metadata(
            str(operation_binding_ref.get("version_id", "")),
            "operation_binding/v1")
            if isinstance(operation_binding_ref, Mapping) else None)
        invocation_ref = call.get("invocation_ref")
        invocation = (version_metadata(
            str(invocation_ref.get("version_id", "")),
            "invocation/v1")
            if isinstance(invocation_ref, Mapping) else None)
        if (operation_binding is None or invocation is None
                or selection_ref not in operation_binding.get(
                    "output_binding_refs", [])
                or selection.get("node_ref")
                != invocation.get("own_node_ref")
                or selection.get("net_ref")
                != invocation.get("net_instance_ref")
                or operation_binding_ref
                != invocation.get("operation_binding_ref")):
            raise RegistryConflict(
                "LLM call selection authority differs from exact operation output")
        if not raw_v2:
            require_canonical_attempt_response(
                {
                    "entity_type": "provider_attempt_spec/v1",
                    "logical_id": str(attempt_row["logical_id"]),
                    "version_id": str(
                        payload["provider_attempt_version_id"]),
                },
                {
                    "entity_type": "resource_version/v1",
                    "logical_id": str(response_row["logical_id"]),
                    "version_id": str(payload["response_version_id"]),
                },
            )

    if event_type == "llm_call_candidate_not_adopted/v1":
        attempt_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='provider_attempt_spec/v1'",
            (str(payload["provider_attempt_version_id"]),)).fetchone()
        attempt = (json.loads(attempt_row["metadata_json"])
                   if attempt_row else None)
        call_type = (
            attempt.get("llm_call_ref", {}).get("entity_type")
            if isinstance(attempt, Mapping) else None)
        if call_type not in {"llm_call_spec/v1", "llm_call_spec/v2"}:
            call_type = "llm_call_spec/v1"
        call = object_metadata(
            str(payload["llm_call_id"]), call_type)
        response_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='resource_version/v1'",
            (str(payload["response_version_id"]),)).fetchone()
        response_meta = (json.loads(response_row["metadata_json"])
                         if response_row else None)
        response_origin = (
            response_meta.get("origin")
            if isinstance(response_meta, Mapping) else None)
        raw_v2 = bool(
            call_type == "llm_call_spec/v2"
            and isinstance(response_meta, Mapping)
            and response_meta.get("origin_kind")
            == "provider_raw_response"
            and isinstance(response_origin, Mapping)
            and response_origin.get("primary_ref", {}).get(
                "version_id")
            == str(payload["provider_attempt_version_id"])
            and response_origin.get("secondary_ref") == {
                "entity_type": call_type,
                "logical_id": str(payload["llm_call_id"]),
                "version_id": str(payload["llm_call_version_id"]),
            })
        if (pending.aggregate_id != str(payload["llm_call_id"])
                or call is None
                or not version_exists(str(payload["response_version_id"]),
                                      "resource_version/v1")
                or attempt_row is None or response_row is None):
            raise RegistryConflict(
                "LLM candidate disposition contains an invalid exact ref")
        if not raw_v2:
            require_canonical_attempt_response(
                {
                    "entity_type": "provider_attempt_spec/v1",
                    "logical_id": str(attempt_row["logical_id"]),
                    "version_id": str(
                        payload["provider_attempt_version_id"]),
                },
                {
                    "entity_type": "resource_version/v1",
                    "logical_id": str(response_row["logical_id"]),
                    "version_id": str(payload["response_version_id"]),
                },
            )

    if event_type == "llm_call_failed/v1":
        paired_call_refs = [
            item.payload.get("llm_call_ref") for item in events
            if item.event_type.startswith("provider_attempt_")
            and item.payload.get("llm_call_ref", {}).get("logical_id")
            == payload.get("llm_call_id")]
        call_type = "llm_call_spec/v1"
        if any(isinstance(ref, Mapping)
               and ref.get("entity_type") == "llm_call_spec/v2"
               for ref in paired_call_refs):
            call_type = "llm_call_spec/v2"
        elif object_metadata(
                str(payload["llm_call_id"]),
                "llm_call_spec/v2") is not None:
            call_type = "llm_call_spec/v2"
        call = object_metadata(str(payload["llm_call_id"]), call_type)
        if (pending.aggregate_id != str(payload["llm_call_id"])
                or call is None
                or call.get("llm_call_version_id") != payload["llm_call_version_id"]):
            raise RegistryConflict("LLM call failure contains an invalid exact ref")


def validate_registered_host_llm_event(context, pending):
    """Close source-neutral HOST LLM lifecycle events over exact Registry refs."""

    event_type = pending.event_type
    current = {
        "registered_host_llm_attempt_reserved/v1",
        "llm_response_registered/v2",
        "llm_invocation_succeeded/v2",
        "provider_attempt_host_closed/v1",
        "llm_call_registered_host_closed/v1",
        "registered_host_llm_attempt_closed/v1",
    }
    if event_type not in current:
        return
    RegistryConflict = facade.RegistryConflict
    payload = pending.payload
    attempt_ref = payload.get("registered_host_llm_attempt_ref")
    call_ref = payload.get("llm_call_ref")
    provider_ref = payload.get("provider_attempt_ref")
    invocation_ref = payload.get("invocation_ref")
    binding_ref = payload.get("operation_binding_ref")
    refs = (
        (attempt_ref, "registered_host_llm_attempt/v1"),
        (call_ref, "llm_call_spec/v3"),
        (provider_ref, "provider_attempt_spec/v1"),
        (invocation_ref, "invocation/v1"),
        (binding_ref, "operation_binding/v1"),
    )

    def exact(ref, expected_type):
        if (not isinstance(ref, Mapping)
                or ref.get("entity_type") != expected_type):
            return None
        return context.version_metadata(
            str(ref.get("version_id", "")), expected_type)

    attempt, call, provider, invocation, binding = (
        exact(ref, expected_type) for ref, expected_type in refs)
    if (payload.get("invocation_kind") != "registered_host"
            or any(value is None for value in (
                attempt, call, provider, invocation, binding))
            or attempt.get("registered_host_llm_attempt_ref") != attempt_ref
            or attempt.get("llm_call_ref") != call_ref
            or attempt.get("provider_attempt_ref") != provider_ref
            or attempt.get("invocation_ref") != invocation_ref
            or attempt.get("operation_binding_ref") != binding_ref
            or call.get("invocation_kind") != "registered_host"
            or call.get("invocation_ref") != invocation_ref
            or call.get("operation_binding_ref") != binding_ref
            or provider.get("llm_call_ref") != call_ref
            or provider.get("invocation_ref") != invocation_ref
            or provider.get("operation_binding_ref") != binding_ref
            or invocation.get("operation_binding_ref") != binding_ref):
        raise RegistryConflict(
            "registered HOST LLM event lacks its exact authority closure")
    if event_type == "registered_host_llm_attempt_reserved/v1":
        if (dict(attempt) != dict(payload)
                or pending.aggregate_type != "registered_host_llm_attempt"
                or pending.aggregate_id != attempt_ref.get("logical_id")
                or not context.has_relation(
                    "derived_from", str(attempt_ref.get("version_id", "")),
                    str(provider_ref.get("version_id", "")))):
            raise RegistryConflict(
                "registered HOST LLM reservation is not exact")
        return
    siblings = tuple(
        item for item in context.events
        if item.payload.get("registered_host_llm_attempt_ref") == attempt_ref)
    if event_type in {
            "llm_response_registered/v2", "llm_invocation_succeeded/v2"}:
        expected = {
            "llm_response_registered/v2",
            "llm_invocation_succeeded/v2",
        }
        if ({item.event_type for item in siblings if item.event_type in expected}
                != expected
                or any(dict(item.payload) != dict(payload)
                       for item in siblings if item.event_type in expected)
                or pending.aggregate_type != "registered_host_llm_attempt"
                or pending.aggregate_id != attempt_ref.get("logical_id")):
            raise RegistryConflict(
                "registered HOST LLM success events are not one exact pair")
        response_ref = payload.get("response_resource_ref")
        response = (context.version_metadata(
            str(response_ref.get("resource_version_id", "")),
            "resource_version/v1")
            if isinstance(response_ref, Mapping) else None)
        extension = (response.get("extensions", {}).get(
            "registry.registered_host_llm_response/v1")
            if isinstance(response, Mapping) else None)
        origin = response.get("origin") if isinstance(response, Mapping) else None
        if (response is None
                or response.get("origin_kind") != "llm_response"
                or not isinstance(origin, Mapping)
                or origin.get("primary_ref") != attempt_ref
                or origin.get("secondary_ref") != call_ref
                or response.get("producer_ref") != invocation_ref
                or not isinstance(extension, Mapping)
                or extension.get("registered_host_llm_attempt_ref")
                != attempt_ref
                or extension.get("llm_call_ref") != call_ref
                or extension.get("model_condition") != call.get("model")):
            raise RegistryConflict(
                "registered HOST LLM semantic response is not exact")
        completions = tuple(
            item for item in context.events
            if item.event_type == "provider_attempt_completed/v1"
            and item.aggregate_id == provider_ref.get("logical_id"))
        if len(completions) != 1:
            raise RegistryConflict(
                "registered HOST LLM success lacks provider completion")
        return
    closed_types = {
        "provider_attempt_host_closed/v1",
        "llm_call_registered_host_closed/v1",
        "registered_host_llm_attempt_closed/v1",
    }
    closed = tuple(item for item in siblings if item.event_type in closed_types)
    if ({item.event_type for item in closed} != closed_types
            or any(dict(item.payload) != dict(payload) for item in closed)
            or payload.get("next_attempt_allowed") is not False):
        raise RegistryConflict(
            "registered HOST LLM closure is not one exact terminal bundle")

__all__ = (
    "require_canonical_attempt_response",
    "require_canonical_attempt_response_proposal",
    "require_canonical_producer",
    "require_canonical_provider_lineage",
    "validate_attempt_response_boundary",
    "validate_llm_model_call_budget",
    "validate_provider_attempt_event",
    "validate_provider_outcome_event",
    "validate_provider_pre_resource_event",
    "validate_provider_provenance",
    "validate_provider_materialization_atomicity",
    "validate_registered_host_llm_event",
)
