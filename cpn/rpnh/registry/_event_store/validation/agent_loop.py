"""Agent-loop transaction validation over an already-open Registry snapshot."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ... import event_store as facade
from ...identities import TypedId
from ...models import PendingEvent, PreparedObject
from ..accounting import _registered_model_call_limits


def validate_agent_loop_atomicity(
        db: sqlite3.Connection,
        objects: Sequence[PreparedObject],
        events: Sequence[PendingEvent],
        *,
        writer_epoch: int,
        validate_waiting_resource_grant_atomicity: Callable[..., bool],
        is_parent_internal_leaf_kb_transaction: Callable[..., bool],
        registered_turn_budget_extension: Callable[..., int | None],
        registered_operation_config: Callable[..., Mapping[str, Any]],
) -> None:
    """Linearize one closed agent-loop revision and its child records."""
    from ...event_store import (
        RegistryConflict,
        _TASK_MODEL_CALL_TERMINAL_REASON,
        _action_result_contains_authoritative_fields,
    )

    module_extensions = tuple(
        event for event in events
        if event.event_type == "live_firing_resource_extension/v1")
    formal_extensions = tuple(
        event for event in events
        if event.event_type == "petri_firing_resource_accessed/v1")
    if module_extensions:
        formal_fields = (
            "transition_firing_ref", "transition_id", "resource_ref",
            "access_mode", "access_checkpoint_ref", "access_net_ref",
            "access_claim_epoch", "resource_token_ref",
            "lease_pool_place", "lease_identity_ref",
            "petri_input_arc_mode", "petri_output_arc_mode",
            "writer_fencing_epoch",
        )
        unmatched = list(formal_extensions)
        for module_event in module_extensions:
            matches = tuple(
                formal_event for formal_event in unmatched
                if (formal_event.stream_id == module_event.stream_id
                    and formal_event.aggregate_id
                    == module_event.aggregate_id
                    and formal_event.aggregate_type
                    == module_event.aggregate_type
                    and formal_event.producer_invocation_id
                    == module_event.producer_invocation_id
                    and all(
                        formal_event.payload.get(field)
                        == module_event.payload.get(field)
                        for field in formal_fields)))
            if len(matches) != 1:
                raise RegistryConflict(
                    "agent resource extension lacks one Harness Petri "
                    "access fact")
            unmatched.remove(matches[0])
    if validate_waiting_resource_grant_atomicity(
            db, objects, events, writer_epoch=writer_epoch):
        return
    family = {
        "agent_loop/v1", "agent_turn/v1", "agent_action/v2",
        "agent_tool_error/v1", "agent_context_compaction/v1",
        "agent_context_compaction/v2", "agent_context_compaction/v3",
        "firing_external_kb_read/v2",
    }
    agent_objects = tuple(item for item in objects
                          if item.object_type in family)
    agent_events = tuple(item for item in events if item.event_type in {
        "agent_loop_started/v1", "agent_turn_recorded/v1",
        "agent_action_settled/v1", "agent_context_compacted/v1",
        "live_firing_resource_extension/v1",
        "firing_external_kb_read_recorded/v2",
        "agent_loop_terminal/v1",
    })
    if not agent_objects and not agent_events:
        return
    if is_parent_internal_leaf_kb_transaction(
            db, agent_objects, agent_events):
        return
    loops = tuple(item for item in agent_objects
                  if item.object_type == "agent_loop/v1")
    if len(loops) != 1:
        raise RegistryConflict(
            "agent-loop transaction requires one immutable loop revision")
    loop = loops[0]
    metadata = dict(loop.metadata)
    loop_ref = metadata.get("agent_loop_ref")
    if (not isinstance(loop_ref, Mapping)
            or loop_ref.get("logical_id") != str(loop.logical_id)
            or loop_ref.get("version_id") != str(loop.version_id)
            or metadata.get("agent_loop_id") != str(loop.logical_id)
            or metadata.get("agent_loop_version_id")
            != str(loop.version_id)):
        raise RegistryConflict("agent-loop revision ref is not exact")
    prior_row = db.execute(
        "SELECT metadata_json FROM objects WHERE object_type='agent_loop/v1' "
        "AND logical_id=? ORDER BY rowid DESC LIMIT 1",
        (str(loop.logical_id),)).fetchone()
    prior = (json.loads(prior_row["metadata_json"])
             if prior_row is not None else None)
    if prior is None:
        starts = tuple(event for event in agent_events
                       if event.event_type == "agent_loop_started/v1")
        if (metadata.get("revision") != 0
                or metadata.get("state") != "NEW"
                or metadata.get("next_turn_sequence") != 0
                or metadata.get("llm_turns_used") != 0
                or len(starts) != 1
                or starts[0].payload.get("agent_loop_version_id")
                != str(loop.version_id)
                or starts[0].payload.get("tool_catalog_ref")
                != metadata.get("tool_catalog_ref")):
            raise RegistryConflict(
                "agent-loop genesis lacks its exact start closure")
    else:
        stable = (
            "agent_loop_id", "invocation_ref", "operation_binding_ref",
            "llm_turn_budget", "model_condition",
            "tool_catalog_ref", "owner_ref",
        )
        same_role_stable_fields = (
            "agent_loop_id", "invocation_ref", "operation_binding_ref",
            "owner_ref", "model_condition", "tool_catalog_ref",
            "current_candidate_ref",
            "adopted_candidate_ref", "disposition_ref",
            "written_resource_refs", "cap_review_resource_ref",
            "workspace_binding_ref", "workspace_lineage_ref",
            "workspace_base_revision_ref", "workspace_revision_ref",
            "workspace_changed_paths", "workspace_deleted_paths",
            "next_turn_sequence", "llm_turns_used",
        )
        same_role_segment_candidate = (
            len(objects) == 1
            and len(agent_objects) == 1
            and not agent_events
            and prior.get("state") == "WAITING_FOR_LLM"
            and metadata.get("state") == "WAITING_FOR_LLM"
            and metadata.get("revision") == int(prior["revision"]) + 1
            and all(metadata.get(key) == prior.get(key)
                    for key in same_role_stable_fields)
            and metadata.get("llm_turn_budget")
            != prior.get("llm_turn_budget")
        )
        same_role_segment_continue = False
        if same_role_segment_candidate:
            binding_ref = metadata.get("operation_binding_ref")
            binding_row = (
                db.execute(
                    "SELECT metadata_json FROM objects "
                    "WHERE object_type='operation_binding/v1' "
                    "AND logical_id=? AND version_id=?",
                    (str(binding_ref.get("logical_id", "")),
                     str(binding_ref.get("version_id", ""))),
                ).fetchone()
                if isinstance(binding_ref, Mapping) else None)
            binding = (
                json.loads(binding_row["metadata_json"])
                if binding_row is not None else None)
            increment = registered_turn_budget_extension(
                db, metadata.get("invocation_ref"), binding_ref)
            same_role_segment_continue = (
                increment is not None
                and metadata.get("llm_turn_budget")
                == int(prior["llm_turn_budget"]) + increment
                and isinstance(binding_ref, Mapping)
                and binding_ref == prior.get("operation_binding_ref")
                and binding_ref.get("entity_type")
                == "operation_binding/v1"
                and isinstance(binding, Mapping)
                and binding.get("operation_binding_ref") == binding_ref
            )
        if (not same_role_segment_continue
                and (metadata.get("revision")
                     != int(prior["revision"]) + 1
                or any(metadata.get(key) != prior.get(key)
                       for key in stable)
                or metadata.get("next_turn_sequence")
                != metadata.get("llm_turns_used"))):
            raise RegistryConflict(
                "agent-loop revision is not one exact monotonic successor")
    turns = tuple(item for item in agent_objects
                  if item.object_type == "agent_turn/v1")
    turn_events = tuple(event for event in agent_events
                        if event.event_type == "agent_turn_recorded/v1")
    if turns or turn_events:
        if (prior is None or turns or len(turn_events) != 1
                or metadata.get("state") != "TURN_STORED"
                or metadata.get("next_turn_sequence")
                != int(prior["next_turn_sequence"]) + 1
                or metadata.get("llm_turns_used")
                != int(prior["llm_turns_used"]) + 1):
            raise RegistryConflict(
                "agent turn must atomically advance one loop turn")
        event = turn_events[0]
        call_seed = db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? AND object_type='llm_call_spec/v2'",
            (str(event.payload.get("llm_call_version_id", "")),),
        ).fetchone()
        attempt_seed = db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? "
            "AND object_type='provider_attempt_spec/v1'",
            (str(event.payload.get(
                "provider_attempt_version_id", "")),),
        ).fetchone()
        response_seed = db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? AND object_type='resource_version/v1'",
            (str(event.payload.get("response_version_id", "")),),
        ).fetchone()
        if (call_seed is None or attempt_seed is None
                or response_seed is None):
            raise RegistryConflict(
                "event-backed agent turn lacks exact provider lineage")
        call_ref = {
            "entity_type": "llm_call_spec/v2",
            "logical_id": str(call_seed["logical_id"]),
            "version_id": str(event.payload["llm_call_version_id"]),
        }
        attempt_ref = {
            "entity_type": "provider_attempt_spec/v1",
            "logical_id": str(attempt_seed["logical_id"]),
            "version_id": str(
                event.payload["provider_attempt_version_id"]),
        }
        response_ref = {
            "resource_id": str(response_seed["logical_id"]),
            "resource_version_id": str(event.payload["response_version_id"]),
        }
        turn_meta = {
            "agent_loop_ref": loop_ref,
            "sequence": event.payload["sequence"],
            "agent_turn_ref": {
                "entity_type": "agent_turn/v1",
                "logical_id": event.payload["agent_turn_id"],
                "version_id": event.payload["agent_turn_version_id"],
            },
            "agent_turn_id": event.payload["agent_turn_id"],
            "agent_turn_version_id": event.payload[
                "agent_turn_version_id"],
            "recorded_revision": event.payload["revision"],
            "llm_call_ref": call_ref,
            "provider_attempt_ref": attempt_ref,
            "response_resource_ref": response_ref,
            "response_size": event.payload["response_size"],
            "tool_calls": (None,) * int(event.payload["tool_call_count"]),
        }
        from types import SimpleNamespace
        turn = SimpleNamespace(
            logical_id=TypedId.parse(event.payload["agent_turn_id"]),
            version_id=TypedId.parse(
                event.payload["agent_turn_version_id"]),
            producer_invocation_id=loop.producer_invocation_id,
        )
        invocation_ref = metadata.get("invocation_ref")
        operation_binding_ref = metadata.get("operation_binding_ref")
        call_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? AND object_type='llm_call_spec/v2'",
            (str(call_ref.get("version_id", "")),),
        ).fetchone() if isinstance(call_ref, Mapping) else None)
        attempt_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? "
            "AND object_type='provider_attempt_spec/v1'",
            (str(attempt_ref.get("version_id", "")),),
        ).fetchone() if isinstance(attempt_ref, Mapping) else None)
        response_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE version_id=? AND object_type='resource_version/v1'",
            (str(response_ref.get("resource_version_id", "")),),
        ).fetchone() if isinstance(response_ref, Mapping) else None)
        call = (json.loads(call_row["metadata_json"])
                if call_row is not None else None)
        attempt = (json.loads(attempt_row["metadata_json"])
                   if attempt_row is not None else None)
        response = (json.loads(response_row["metadata_json"])
                    if response_row is not None else None)
        observations = db.execute(
            "SELECT event_id,aggregate_id,producer_invocation_id,"
            "payload_json FROM events WHERE event_type="
            "'provider_attempt_submission_observed/v1' "
            "AND aggregate_id=?",
            (str(attempt_ref.get("logical_id", ""))
             if isinstance(attempt_ref, Mapping) else "",),
        ).fetchall()
        completions = tuple(
            item for item in events
            if item.event_type == "provider_attempt_completed/v1")
        prior_completions = db.execute(
            "SELECT event_id FROM events WHERE event_type="
            "'provider_attempt_completed/v1' AND aggregate_id=?",
            (str(attempt_ref.get("logical_id", ""))
             if isinstance(attempt_ref, Mapping) else "",),
        ).fetchall()
        duplicate_turns = []
        for row in db.execute(
                "SELECT payload_json FROM events WHERE event_type="
                "'agent_turn_recorded/v1' AND ("
                "json_extract(payload_json,'$.llm_call_version_id')=? OR "
                "json_extract(payload_json,"
                "'$.provider_attempt_version_id')=? OR "
                "json_extract(payload_json,'$.response_version_id')=? OR "
                "(json_extract(payload_json,'$.agent_loop_id')=? AND "
                "json_extract(payload_json,'$.sequence')=?))",
                (
                    (call_ref.get("version_id")
                     if isinstance(call_ref, Mapping) else None),
                    (attempt_ref.get("version_id")
                     if isinstance(attempt_ref, Mapping) else None),
                    (response_ref.get("resource_version_id")
                     if isinstance(response_ref, Mapping) else None),
                    str(loop.logical_id), turn_meta.get("sequence"),
                )).fetchall():
            value = json.loads(row["payload_json"])
            if (value.get("llm_call_version_id")
                    == (call_ref.get("version_id")
                        if isinstance(call_ref, Mapping) else None)
                    or value.get("provider_attempt_version_id")
                    == (attempt_ref.get("version_id")
                        if isinstance(attempt_ref, Mapping) else None)
                    or value.get("response_version_id")
                    == (response_ref.get("resource_version_id")
                        if isinstance(response_ref, Mapping) else None)
                    or (value.get("agent_loop_id") == str(loop.logical_id)
                        and value.get("sequence")
                        == turn_meta.get("sequence"))):
                duplicate_turns.append(value)
        observation = (json.loads(observations[0]["payload_json"])
                       if len(observations) == 1 else None)
        completion = completions[0] if len(completions) == 1 else None
        expected_turn_ref = {
            "entity_type": "agent_turn/v1",
            "logical_id": str(turn.logical_id),
            "version_id": str(turn.version_id),
        }
        expected_response_origin = {
            "kind": "provider_raw_response",
            "primary_ref": attempt_ref,
            "secondary_ref": call_ref,
        }
        if (turn_meta.get("agent_loop_ref") != loop_ref
                or turn_meta.get("sequence")
                != prior.get("next_turn_sequence")
                or turn_meta.get("agent_turn_ref") != expected_turn_ref
                or turn_meta.get("agent_turn_id")
                != str(turn.logical_id)
                or turn_meta.get("agent_turn_version_id")
                != str(turn.version_id)
                or turn_meta.get("recorded_revision")
                != metadata.get("revision")
                or not isinstance(invocation_ref, Mapping)
                or not isinstance(operation_binding_ref, Mapping)
                or not isinstance(call_ref, Mapping)
                or not isinstance(attempt_ref, Mapping)
                or not isinstance(response_ref, Mapping)
                or call_row is None or attempt_row is None
                or response_row is None
                or call_row["logical_id"] != call_ref.get("logical_id")
                or attempt_row["logical_id"]
                != attempt_ref.get("logical_id")
                or response_row["logical_id"]
                != response_ref.get("resource_id")
                or call_ref.get("entity_type") != "llm_call_spec/v2"
                or attempt_ref.get("entity_type")
                != "provider_attempt_spec/v1"
                or call.get("llm_call_ref") != call_ref
                or call.get("invocation_ref") != invocation_ref
                or call.get("operation_binding_ref")
                != operation_binding_ref
                or call.get("agent_loop_ref")
                != prior.get("agent_loop_ref")
                or call.get("turn_sequence") != turn_meta.get("sequence")
                or attempt.get("provider_attempt_ref") != attempt_ref
                or attempt.get("llm_call_ref") != call_ref
                or attempt.get("invocation_ref") != invocation_ref
                or attempt.get("operation_binding_ref")
                != operation_binding_ref
                or response.get("origin_kind")
                != "provider_raw_response"
                or response.get("origin") != expected_response_origin
                or response.get("producer_ref") != invocation_ref
                or response.get("size") != turn_meta.get("response_size")
                or len(observations) != 1
                or observation.get("provider_attempt_ref") != attempt_ref
                or observation.get("llm_call_ref") != call_ref
                or observation.get("invocation_ref") != invocation_ref
                or observation.get("response_resource_ref")
                != response_ref
                or observations[0]["producer_invocation_id"]
                != invocation_ref.get("logical_id")
                or len(completions) != 1 or prior_completions
                or completion.aggregate_id
                != attempt_ref.get("logical_id")
                or completion.producer_invocation_id is None
                or str(completion.producer_invocation_id)
                != invocation_ref.get("logical_id")
                or completion.payload.get("provider_attempt_id")
                != attempt_ref.get("logical_id")
                or completion.payload.get("response_version_id")
                != response_ref.get("resource_version_id")
                or completion.payload.get("response_observed_event_id")
                != observations[0]["event_id"]
                or completion.payload.get("finish_reason")
                != observation.get("finish_reason")
                or completion.payload.get("external_request_id")
                != observation.get("external_request_id")
                or event.payload.get("agent_turn_id")
                != str(turn.logical_id)
                or event.payload.get("agent_turn_version_id")
                != str(turn.version_id)
                or event.payload.get("sequence")
                != turn_meta.get("sequence")
                or event.payload.get("revision")
                != metadata.get("revision")
                or event.payload.get("llm_call_version_id")
                != call_ref.get("version_id")
                or event.payload.get("provider_attempt_version_id")
                != attempt_ref.get("version_id")
                or event.payload.get("response_version_id")
                != response_ref.get("resource_version_id")
                or event.payload.get("response_size")
                != turn_meta.get("response_size")
                or event.payload.get("tool_call_count")
                != len(turn_meta.get("tool_calls", ()))
                or event.aggregate_id != str(loop.logical_id)
                or event.producer_invocation_id is None
                or str(event.producer_invocation_id)
                != invocation_ref.get("logical_id")
                or turn.producer_invocation_id is None
                or str(turn.producer_invocation_id)
                != invocation_ref.get("logical_id")
                or loop.producer_invocation_id is None
                or str(loop.producer_invocation_id)
                != invocation_ref.get("logical_id")
                or duplicate_turns):
            raise RegistryConflict(
                "agent turn record/event/loop revision are not one closure")
    actions = tuple(item for item in agent_objects
                    if item.object_type == "agent_action/v2")
    action_events = tuple(event for event in agent_events
                          if event.event_type == "agent_action_settled/v1")
    errors = {str(item.version_id): item for item in agent_objects
              if item.object_type == "agent_tool_error/v1"}
    if (metadata.get("state") == "ACTION_PENDING"
            and (len(agent_objects) != 2 or len(actions) != 1
                 or agent_events)):
        raise RegistryConflict(
            "ACTION_PENDING requires one unsettled action publication")
    if (prior is not None and prior.get("state") == "ACTION_PENDING"
            and (len(actions) != 1 or len(action_events) != 1
                 or len(agent_events) != 1
                 or any(item.object_type not in {
                     "agent_loop/v1", "agent_action/v2",
                     "agent_tool_error/v1"}
                     for item in agent_objects))):
        raise RegistryConflict(
            "ACTION_PENDING must close with one final action settlement")
    if actions or action_events:
        ordered_events = tuple(sorted(
            action_events,
            key=lambda item: int(item.payload["tool_call_ordinal"])))
        ordered_actions = tuple(sorted(
            actions,
            key=lambda item: int(item.metadata["tool_call_ordinal"])))
        pending_monitored = False
        if (prior is not None and len(ordered_actions) == 1
                and not ordered_events):
            pending_action = ordered_actions[0]
            pending_value = dict(pending_action.metadata)
            pending_ref = pending_value.get("agent_action_ref")
            pending_turn_ref = pending_value.get("agent_turn_ref")
            pending_arguments = pending_value.get("arguments")
            turn_row = (db.execute(
                "SELECT producer_invocation_id,payload_json FROM events "
                "WHERE event_type IN "
                "('agent_turn_recorded/v1','agent_turn_recorded/v2') "
                "AND json_extract(payload_json,'$.agent_turn_id')=? "
                "AND json_extract(payload_json,'$.agent_turn_version_id')=?",
                (str(pending_turn_ref.get("logical_id", "")),
                 str(pending_turn_ref.get("version_id", ""))),
            ).fetchall()
                if isinstance(pending_turn_ref, Mapping) else ())
            turn_payload = (
                json.loads(turn_row[0]["payload_json"])
                if len(turn_row) == 1 else {})
            pending_monitored = (
                prior.get("state") == "TURN_STORED"
                and metadata.get("state") == "ACTION_PENDING"
                and metadata.get("next_turn_sequence")
                == prior.get("next_turn_sequence")
                and metadata.get("llm_turns_used")
                == prior.get("llm_turns_used")
                and pending_value.get("state") == "ACTION_PENDING"
                and pending_value.get("tool_name") == "workspace"
                and isinstance(pending_arguments, Mapping)
                and pending_arguments.get("execution_mode") == "monitored"
                and pending_value.get("result_metadata") is None
                and pending_value.get("result_refs") == []
                and pending_value.get("tool_error_ref") is None
                and pending_value.get("tool_call_ordinal") == 0
                and pending_value.get("expected_revision")
                == prior.get("revision")
                and pending_value.get("turn_sequence")
                == int(prior["next_turn_sequence"]) - 1
                and pending_value.get("agent_loop_ref") == loop_ref
                and isinstance(pending_ref, Mapping)
                and pending_ref.get("entity_type") == "agent_action/v2"
                and pending_ref.get("logical_id")
                == str(pending_action.logical_id)
                and pending_ref.get("version_id")
                == str(pending_action.version_id)
                and isinstance(pending_turn_ref, Mapping)
                and pending_turn_ref.get("entity_type") == "agent_turn/v1"
                and len(turn_row) == 1
                and turn_payload.get("agent_loop_id")
                == str(loop.logical_id)
                and turn_payload.get("sequence")
                == pending_value.get("turn_sequence")
                and pending_action.producer_invocation_id
                == loop.producer_invocation_id
                and turn_row[0]["producer_invocation_id"]
                == str(loop.producer_invocation_id))
            if not pending_monitored:
                raise RegistryConflict(
                    "unsettled agent action is not one exact monitored "
                    "workspace pending publication")
        if (not pending_monitored
                and (prior is None
                or len(ordered_actions) != len(ordered_events)
                or not ordered_actions
                or tuple(item.payload["tool_call_ordinal"]
                         for item in ordered_events)
                != tuple(range(len(ordered_events)))
                or tuple(item.metadata["tool_call_ordinal"]
                         for item in ordered_actions)
                != tuple(range(len(ordered_actions)))
                or metadata.get("next_turn_sequence")
                != prior.get("next_turn_sequence")
                or metadata.get("llm_turns_used")
                != prior.get("llm_turns_used"))):
            raise RegistryConflict(
                "agent actions must settle once in provider order")
        for action, event in zip(ordered_actions, ordered_events):
            value = dict(action.metadata)
            error_ref = value.get("tool_error_ref")
            error_version = (error_ref.get("version_id")
                             if isinstance(error_ref, Mapping) else None)
            rejected = value.get("state") == "ACTION_REJECTED"
            if prior is not None and prior.get("state") == "ACTION_PENDING":
                pending_rows = db.execute(
                    "SELECT logical_id,producer_invocation_id,metadata_json "
                    "FROM objects WHERE object_type='agent_action/v2' "
                    "AND json_extract(metadata_json,'$.state')="
                    "'ACTION_PENDING' "
                    "AND json_extract(metadata_json,"
                    "'$.agent_loop_ref.version_id')=?",
                    (str(prior.get("agent_loop_ref", {}).get(
                        "version_id", "")),),
                ).fetchall()
                pending_predecessor = (
                    json.loads(pending_rows[0]["metadata_json"])
                    if len(pending_rows) == 1 else {})
                stable_action_fields = (
                    "agent_action_id", "agent_turn_ref", "turn_sequence",
                    "tool_call_ordinal", "tool_call_id",
                    "action_identity_kind", "action_identity_key",
                    "tool_name", "raw_arguments", "arguments",
                    "expected_revision",
                )
                if (len(pending_rows) != 1
                        or pending_rows[0]["logical_id"]
                        != str(action.logical_id)
                        or pending_rows[0]["producer_invocation_id"]
                        != str(action.producer_invocation_id)
                        or value.get("state") not in {
                            "ACTION_APPLIED", "ACTION_REJECTED"}
                        or len(errors) != (
                            1 if value.get("state") == "ACTION_REJECTED"
                            else 0)
                        or any(value.get(field)
                               != pending_predecessor.get(field)
                               for field in stable_action_fields)):
                    raise RegistryConflict(
                        "monitored workspace final action is not the exact "
                        "successor of its pending logical action")
            if (value.get("agent_loop_ref") != loop_ref
                    or event.payload.get("agent_action_id")
                    != str(action.logical_id)
                    or event.payload.get("settlement")
                    != value.get("state")
                    or event.payload.get("revision")
                    != metadata.get("revision")
                    or event.payload.get("tool_error_version_id")
                    != error_version
                    or rejected != (error_version is not None)
                    or (rejected and error_version not in errors)
                    or (rejected and value.get("result_refs"))):
                raise RegistryConflict(
                    "agent action lacks its own exact settlement/error closure")
    resource_extensions = tuple(
        event for event in agent_events
        if event.event_type == "live_firing_resource_extension/v1")
    extension_actions = tuple(
        action for action in actions
        if action.metadata.get("tool_name") == "request_resource"
        and action.metadata.get("state") == "ACTION_APPLIED")
    if len(resource_extensions) != len(extension_actions):
        raise RegistryConflict(
            "live resource extension action/event cardinality differs")
    extension_by_action_ref = {
        json.dumps(event.payload.get("agent_action_ref"), sort_keys=True):
            event
        for event in resource_extensions
    }
    if len(extension_by_action_ref) != len(resource_extensions):
        raise RegistryConflict(
            "live resource extension action authority is not unique")
    for action in extension_actions:
        value = dict(action.metadata)
        action_ref = value.get("agent_action_ref")
        event = extension_by_action_ref.get(
            json.dumps(action_ref, sort_keys=True))
        payload = dict(event.payload) if event is not None else {}
        expected_result = {
            "kind": "live_firing_resource_extension/v1",
            **payload,
        }
        action_result = value.get("result_metadata")
        firing_ref = payload.get("transition_firing_ref")
        invocation_ref = payload.get("invocation_ref")
        resource_ref = payload.get("resource_ref")
        firing_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE object_type='transition_firing/v1' AND version_id=?",
            (str(firing_ref.get("version_id", "")),),
        ).fetchone() if isinstance(firing_ref, Mapping) else None)
        invocation_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE object_type='invocation/v1' AND version_id=?",
            (str(invocation_ref.get("version_id", "")),),
        ).fetchone() if isinstance(invocation_ref, Mapping) else None)
        resource_row = (db.execute(
            "SELECT logical_id,metadata_json FROM objects "
            "WHERE object_type='resource_version/v1' AND version_id=?",
            (str(resource_ref.get("resource_version_id", "")),),
        ).fetchone() if isinstance(resource_ref, Mapping) else None)
        firing = (json.loads(firing_row["metadata_json"])
                  if firing_row is not None else {})
        invocation = (json.loads(invocation_row["metadata_json"])
                      if invocation_row is not None else {})
        resource = (json.loads(resource_row["metadata_json"])
                    if resource_row is not None else {})
        exact_lock_ref = (
            {"entity_type": "resource_version/v1",
             "logical_id": resource_ref.get("resource_id"),
             "version_id": resource_ref.get("resource_version_id")}
            if isinstance(resource_ref, Mapping) else None)
        if (event is None
                or value.get("agent_loop_ref") != loop_ref
                or payload.get("agent_loop_ref") != loop_ref
                or payload.get("agent_turn_ref")
                != value.get("agent_turn_ref")
                or payload.get("agent_action_ref") != action_ref
                or payload.get("invocation_ref")
                != metadata.get("invocation_ref")
                or payload.get("operation_binding_ref")
                != metadata.get("operation_binding_ref")
                or payload.get("llm_turns_used")
                != metadata.get("llm_turns_used")
                or payload.get("same_firing_continuation") is not True
                or payload.get("writer_fencing_epoch") != writer_epoch
                or event.stream_id != (
                    "transition-firing:"
                    f"{firing_ref.get('logical_id', '')}")
                or event.aggregate_id
                != firing_ref.get("logical_id")
                or event.aggregate_type != "transition_firing"
                or event.producer_invocation_id is None
                or str(event.producer_invocation_id)
                != invocation_ref.get("logical_id")
                or firing_row is None
                or firing_row["logical_id"]
                != firing_ref.get("logical_id")
                or firing.get("transition_firing_ref") != firing_ref
                or firing.get("transition_id")
                != payload.get("transition_id")
                or firing.get("operation_binding_ref")
                != payload.get("operation_binding_ref")
                or invocation_row is None
                or invocation_row["logical_id"]
                != invocation_ref.get("logical_id")
                or invocation.get("invocation_ref") != invocation_ref
                or invocation.get("own_transition_firing_ref")
                != firing_ref
                or invocation.get("operation_execution_lease_ref")
                != payload.get("operation_execution_lease_ref")
                or invocation.get("operation_binding_ref")
                != payload.get("operation_binding_ref")
                or resource_row is None
                or resource_row["logical_id"]
                != resource_ref.get("resource_id")
                or resource.get("task_ref") != firing.get("task_ref")
                or payload.get("resource_use_occurrence_ref")
                != expected_result.get("resource_use_occurrence_ref")
                or payload.get("resource_access_lifecycle_ref")
                != expected_result.get("resource_access_lifecycle_ref")
                or payload.get("resource_access_grant_ref") is None
                or payload.get("resource_access_lease_ref") is None
                or payload.get("logical_resource_id")
                != resource_ref.get("resource_id")
                or payload.get("lock_resource_ref") != exact_lock_ref
                # Model-facing location guidance is an advisory projection,
                # not part of the authoritative action/event closure.  The
                # event payload still has to match every Registry-owned
                # resource, firing, invocation, and access fact above.
                or not _action_result_contains_authoritative_fields(
                    action_result, expected_result)
                or value.get("result_refs")):
            raise RegistryConflict(
                "live resource extension lacks its exact same-firing "
                "action closure")
    kb_reads = tuple(item for item in agent_objects
                     if item.object_type == "firing_external_kb_read/v2")
    kb_events = tuple(event for event in agent_events
                      if event.event_type
                      == "firing_external_kb_read_recorded/v2")
    kb_result_resources = tuple(
        item for item in objects
        if (item.object_type == "resource_version/v1"
            and isinstance(item.metadata.get("extensions"), Mapping)
            and isinstance(item.metadata["extensions"].get(
                "registry.external_kb_query_result/v2"), Mapping)))
    if (len(kb_reads) != len(kb_events)
            or len(kb_reads) != len(kb_result_resources)):
        raise RegistryConflict(
            "external KB result resource/read/event cardinality differs")
    actions_by_ref = {
        json.dumps(item.metadata.get("agent_action_ref"), sort_keys=True): item
        for item in actions
    }
    resources_by_action_ref = {
        json.dumps(item.metadata["extensions"][
            "registry.external_kb_query_result/v2"].get(
                "agent_action_ref"), sort_keys=True): item
        for item in kb_result_resources
    }
    if len(resources_by_action_ref) != len(kb_result_resources):
        raise RegistryConflict(
            "external KB result resources do not have unique actions")
    for read, event in zip(
            sorted(kb_reads, key=lambda item: str(item.version_id)),
            sorted(kb_events,
                   key=lambda item: str(item.payload.get(
                       "kb_access_ref", {}).get(
                           "version_id", "")))):
        value = dict(read.metadata)
        read_ref = value.get("kb_access_ref")
        action_ref = value.get("agent_action_ref")
        action = actions_by_ref.get(json.dumps(action_ref, sort_keys=True))
        resource = resources_by_action_ref.get(
            json.dumps(action_ref, sort_keys=True))
        resource_ref = ({
            "resource_id": str(resource.logical_id),
            "resource_version_id": str(resource.version_id),
        } if resource is not None else None)
        resource_version_ref = ({
            "entity_type": "resource_version/v1",
            "logical_id": str(resource.logical_id),
            "version_id": str(resource.version_id),
        } if resource is not None else None)
        operation = value.get("operation")
        result_count = (
            len(value.get("metadata_results", ()))
            if operation == "search" else 1)
        body_access_ref = read_ref if operation == "read" else None
        expected_metadata = {
            "kind": "external_kb_query/v2",
            "operation": operation,
            "result_resource_ref": resource_ref,
            "body_access_ref": body_access_ref,
            "result_count": result_count,
        }
        expected_result_refs = [resource_version_ref] + (
            [read_ref] if operation == "read" else [])
        descriptors = (resource.metadata.get("descriptors")
                       if resource is not None else None)
        extension = (resource.metadata.get("extensions", {}).get(
            "registry.external_kb_query_result/v2")
            if resource is not None else None)
        if (not isinstance(read_ref, Mapping)
                or read_ref.get("logical_id") != str(read.logical_id)
                or read_ref.get("version_id") != str(read.version_id)
                or event.payload.get("kb_access_ref")
                != read_ref
                or event.payload.get("transition_firing_ref")
                != value.get("transition_firing_ref")
                or action is None
                or action.metadata.get("tool_name") != "query_kb"
                or operation not in {"search", "read"}
                or action.metadata.get("result_metadata")
                != expected_metadata
                or action.metadata.get("result_refs")
                != expected_result_refs
                or resource is None
                or not isinstance(descriptors, Mapping)
                or descriptors.get("content_role")
                != "external_kb_query_result"
                or descriptors.get("operation_identity")
                != f"query_kb.{operation}/v1"
                or descriptors.get("result_count") != result_count
                or extension != {
                    "agent_action_ref": action_ref,
                    "transition_firing_ref": value.get(
                        "transition_firing_ref"),
                    "external_resource_index_ref": value.get(
                        "external_resource_index_ref"),
                    "kb_package_ref": value.get("kb_package_ref"),
                    "operation_identity": f"query_kb.{operation}/v1",
                    "audit_receipt_ref": read_ref,
                    "body_access_ref": body_access_ref,
                    "result_count": result_count,
                }
                or set(event.payload) != {
                    "event_ref", "kb_access_ref",
                    "transition_firing_ref", "recorded_at_utc"}):
            raise RegistryConflict(
                "external KB result lacks its exact resource/action closure")
    compactions = tuple(item for item in agent_objects
                        if item.object_type
                        == "agent_context_compaction/v1")
    compacted = tuple(event for event in agent_events
                      if event.event_type == "agent_context_compacted/v1")
    if len(compactions) != len(compacted):
        raise RegistryConflict(
            "agent compaction object/event cardinality differs")
    terminal = tuple(event for event in agent_events
                     if event.event_type == "agent_loop_terminal/v1")
    task_cap_terminal = (
        terminal[0]
        if (len(terminal) == 1
            and terminal[0].payload.get("terminal_reason")
            == _TASK_MODEL_CALL_TERMINAL_REASON)
        else None)
    if task_cap_terminal is not None:
        invocation_ref = metadata.get("invocation_ref")
        binding_ref = metadata.get("operation_binding_ref")
        invocation_row = (
            db.execute(
                "SELECT logical_id,metadata_json FROM objects "
                "WHERE object_type='invocation/v1' AND version_id=?",
                (str(invocation_ref.get("version_id", "")),),
            ).fetchone()
            if isinstance(invocation_ref, Mapping) else None)
        binding_row = (
            db.execute(
                "SELECT logical_id,metadata_json FROM objects "
                "WHERE object_type='operation_binding/v1' AND version_id=?",
                (str(binding_ref.get("version_id", "")),),
            ).fetchone()
            if isinstance(binding_ref, Mapping) else None)
        invocation = (
            json.loads(invocation_row["metadata_json"])
            if invocation_row is not None else None)
        binding = (
            json.loads(binding_row["metadata_json"])
            if binding_row is not None else None)
        firing_ref = (
            invocation.get("own_transition_firing_ref")
            if isinstance(invocation, Mapping) else None)
        firing_row = (
            db.execute(
                "SELECT logical_id FROM objects "
                "WHERE object_type='transition_firing/v1' "
                "AND version_id=?",
                (str(firing_ref.get("version_id", "")),),
            ).fetchone()
            if isinstance(firing_ref, Mapping) else None)
        if (metadata.get("state") != "COMPLETED"
                or metadata.get("written_resource_refs") != []
                or metadata.get("cap_review_resource_ref") is not None
                or invocation_row is None
                or str(invocation_row["logical_id"])
                != str(invocation_ref.get("logical_id", ""))
                or binding_row is None
                or str(binding_row["logical_id"])
                != str(binding_ref.get("logical_id", ""))
                or not isinstance(binding, Mapping)
                or registered_operation_config(
                    db, invocation_ref, binding_ref).get("task_cap_handoff") is not True
                or not isinstance(firing_ref, Mapping)
                or firing_ref.get("entity_type")
                != "transition_firing/v1"
                or firing_row is None
                or str(firing_row["logical_id"])
                != str(firing_ref.get("logical_id", ""))
                or task_cap_terminal.aggregate_id
                != str(firing_ref.get("logical_id", ""))
                or task_cap_terminal.aggregate_type
                != "transition_firing"
                or task_cap_terminal.stream_id
                != f"cap250-handoff:{firing_ref.get('logical_id', '')}"
                or task_cap_terminal.payload.get("agent_loop_id")
                != str(loop.logical_id)):
            raise RegistryConflict(
                "task-cap loop event lacks its exact registered handoff permission")
    terminal_states = {
        "COMPLETED", "SUSPENDED_FOR_REBIND", "TIMED_OUT", "EXHAUSTED",
        "INFRASTRUCTURE_FAILED", "RECONCILIATION_REQUIRED",
    }
    if ((metadata.get("state") in terminal_states) != bool(terminal)
            or len(terminal) > 1
            or (terminal and (
                terminal[0].payload.get("state") != metadata.get("state")
                or terminal[0].payload.get("revision")
                != metadata.get("revision")))):
        raise RegistryConflict(
            "agent terminal event differs from committed loop state")

def validate_current_invocation_authority(
        context,
        invocation: Mapping[str, Any], *, boundary: str,
        operation_result_ref: Mapping[str, Any] | None = None,
        allow_recorded_completion_recovery: bool = False) -> None:
    branch_id = context.branch_id
    db = context.db
    event_store = context.event_store
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    new_by_version = context.new_by_version
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    transaction_writer_epoch = context.transaction_writer_epoch
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    exact_resource_ref_exists = context.exact_resource_ref_exists
    latest_event_payload = context.latest_event_payload
    unique_version_ref_matches = context.unique_version_ref_matches
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _AGENT_CAP_REVIEW_BUNDLE_KIND = facade._AGENT_CAP_REVIEW_BUNDLE_KIND
    _AGENT_CAP_SLOT_RETURN_KIND = facade._AGENT_CAP_SLOT_RETURN_KIND
    _AGENT_CAP_TRACE_SUMMARY_KIND = facade._AGENT_CAP_TRACE_SUMMARY_KIND
    _HistoricalMechanicalTerminalReadyEvent = facade._HistoricalMechanicalTerminalReadyEvent
    _current_task_recovery_manifest_row = facade._current_task_recovery_manifest_row
    canonical_json = facade.canonical_json
    """Close AU-A heads and open lifecycle facts in this commit snapshot."""
    invocation_ref = invocation.get("invocation_ref")
    task_ref = invocation.get("task_ref")
    round_ref = invocation.get("task_round_ref")
    net_ref = invocation.get("net_instance_ref")
    binding_ref = invocation.get("operation_binding_ref")
    authority_decision_ref = invocation.get("authority_decision_ref")
    budget_ref = invocation.get("budget_witness_ref")
    lease_ref = invocation.get("operation_execution_lease_ref")
    if (not exact_ref_exists(invocation_ref, "invocation/v1")
            or not exact_ref_exists(task_ref, "task/v1")
            or not exact_ref_exists(round_ref, "task_round/v1")
            or not exact_ref_exists(binding_ref, "operation_binding/v1")
            or not exact_ref_exists(
                authority_decision_ref, "user_authority_decision/v1")
            ):
        raise RegistryConflict(
            f"{boundary} lacks immutable task/round/operation authority")

    active = latest_event_payload(
        "net_adopted/v1", event_task_id=str(task_id),
        include_pending=False)
    active_ref = (active.get("net_instance_ref")
                  if active is not None else None)
    if (not isinstance(net_ref, Mapping)
            or active_ref != net_ref
            or not unique_version_ref_matches(net_ref, "net_instance/v1")):
        raise RegistryConflict(
            f"{boundary} does not belong to the current active-net authority")

    manifest_pointer = db.execute(
        "SELECT value FROM registry_meta "
        "WHERE key='task_recovery_manifest_version_id'").fetchone()
    manifest_version = (str(manifest_pointer["value"])
                        if manifest_pointer is not None else None)
    if (not isinstance(budget_ref, Mapping)
            or budget_ref.get("entity_type")
            != "task_recovery_manifest/v1"
            or budget_ref.get("version_id") != manifest_version
            or not exact_ref_exists(
                budget_ref, "task_recovery_manifest/v1")):
        raise RegistryConflict(f"{boundary} carries a stale budget authority")

    binding = version_metadata(
        str(binding_ref.get("version_id", "")),
        "operation_binding/v1") if isinstance(binding_ref, Mapping) else None
    if binding is None:
        raise RegistryConflict(f"{boundary} has no exact operation binding")
    decision = version_metadata(
        str(authority_decision_ref.get("version_id", "")),
        "user_authority_decision/v1")
    if (decision is None
            or decision.get("status") != "effective"
            or binding.get("authority_decision_ref")
            != authority_decision_ref):
        raise RegistryConflict(
            f"{boundary} static authority differs from user authority")
    # Node/output refs are immutable pins owned by this exact current
    # operation binding.  A later standalone version is not a new
    # invocation authority and must not invalidate the pinned binding.
    node_ref = binding.get("node_ref")
    if (node_ref is not None
            and not exact_ref_exists(node_ref, "node_declaration/v1")):
        raise RegistryConflict(f"{boundary} has a missing node pin")
    for output_binding_ref in binding.get("output_binding_refs", []):
        if not exact_ref_exists(output_binding_ref, "output_binding/v1"):
            raise RegistryConflict(
                f"{boundary} has a missing output-binding pin")

    invocation_id = str(invocation_ref.get("logical_id", ""))
    pending_terminal_ready = tuple(
        pending for pending in events
        if (pending.event_type == "operation_terminal_ready/v1"
            and (pending.aggregate_id == invocation_id
                 or pending.payload.get("invocation_ref")
                 == invocation_ref)))
    lease = version_metadata(
        str(lease_ref.get("version_id", "")),
        "operation_execution_lease/v1") if isinstance(lease_ref, Mapping) else None
    writer_epoch = db.execute(
        "SELECT value FROM registry_meta WHERE key='writer_epoch'",
    ).fetchone()
    current_writer_epoch = (
        int(writer_epoch["value"]) if writer_epoch is not None else None)
    lease_writer_epoch = int(
        lease.get("writer_fencing_epoch", -1)) if lease is not None else -1

    def has_exact_historical_terminal_ready_authorization() -> bool:
        if (boundary not in {"terminal-ready", "settlement"}
                or invocation.get("origin") != "petri_operation"):
            return False
        candidates = tuple(
            pending for pending in events
            if (isinstance(
                    pending, _HistoricalMechanicalTerminalReadyEvent)
                and pending.event_type
                == "operation_terminal_ready/v1"
                and pending.aggregate_id == invocation_id))
        if len(candidates) != 1:
            return False
        pending = candidates[0]
        proof = pending.authorization
        if proof is None:
            return False
        issued = event_store._EventStore__historical_terminal_ready_authorizations.get(
            proof.nonce)
        if issued is not proof:
            return False
        firing_ref = invocation.get("own_transition_firing_ref")
        task_ref = invocation.get("task_ref")
        round_ref = invocation.get("task_round_ref")
        net_ref = invocation.get("net_instance_ref")
        if not all(isinstance(value, Mapping) for value in (
                invocation_ref, firing_ref, lease_ref,
                operation_result_ref, task_ref, round_ref, net_ref)):
            return False
        valid = bool(
            current_writer_epoch is not None
            and transaction_writer_epoch == current_writer_epoch
            and proof.task_id == task_id
            and proof.branch_id == branch_id
            and proof.task_round_id == task_round_id
            and proof.net_instance_id == net_instance_id
            and proof.transaction_id == transaction_id
            and proof.idempotency_key == idempotency_key
            and proof.writer_epoch == transaction_writer_epoch
            and proof.lease_writer_epoch == lease_writer_epoch
            and lease_writer_epoch < transaction_writer_epoch
            and proof.task_ref
            == event_store._authorization_ref_identity(task_ref)
            and proof.task_round_ref
            == event_store._authorization_ref_identity(round_ref)
            and proof.net_instance_ref
            == event_store._authorization_ref_identity(net_ref)
            and proof.invocation_ref
            == event_store._authorization_ref_identity(invocation_ref)
            and proof.firing_ref
            == event_store._authorization_ref_identity(firing_ref)
            and proof.lease_ref
            == event_store._authorization_ref_identity(lease_ref)
            and proof.operation_result_ref
            == event_store._authorization_ref_identity(operation_result_ref)
            and proof.event_material
            == event_store._pending_event_authorization_material(pending))
        if not valid:
            return False
        matching_settlement = any(
            value.event_type == "transition_firing_settled/v1"
            and value.payload.get("invocation_ref") == invocation_ref
            for value in events)
        if (boundary == "settlement"
                or (boundary == "terminal-ready"
                    and not matching_settlement)):
            consumed = (
                event_store
                ._EventStore__historical_terminal_ready_authorizations.pop(
                    proof.nonce, None))
            if consumed is not proof:
                return False
        return True

    def has_exact_pending_terminal_ready_settlement() -> bool:
        """Bind settlement to its one same-transaction audit fact."""

        if (boundary != "settlement"
                or not isinstance(operation_result_ref, Mapping)
                or not exact_ref_exists(
                    operation_result_ref, "operation_result/v1")
                or len(pending_terminal_ready) != 1):
            return False
        pending = pending_terminal_ready[0]
        return bool(
            pending.aggregate_id == invocation_id
            and str(pending.producer_invocation_id or "")
            == invocation_id
            and pending.payload.get("invocation_ref")
            == invocation_ref
            and pending.payload.get(
                "operation_execution_lease_ref") == lease_ref
            and pending.payload.get("operation_result_ref")
            == operation_result_ref
            and pending.idempotency_key == idempotency_key
            and pending.command_id == idempotency_key)

    def is_exact_committed_agent_terminal_ready() -> bool:
        """Fence one first terminal fact over a durable completed loop."""

        if (current_writer_epoch is None
                or transaction_writer_epoch != current_writer_epoch
                or lease is None
                or invocation.get("origin") != "petri_operation"
                or not exact_ref_exists(
                    lease_ref, "operation_execution_lease/v1")
                or lease.get("invocation_ref") != invocation_ref
                or not isinstance(operation_result_ref, Mapping)
                or not exact_ref_exists(
                    operation_result_ref, "operation_result/v1")):
            return False

        result_version = str(operation_result_ref.get("version_id", ""))
        proposed_result = new_by_version.get(result_version)
        result = version_metadata(result_version, "operation_result/v1")
        if (proposed_result is None
                or proposed_result.object_type != "operation_result/v1"
                or str(proposed_result.logical_id)
                != str(operation_result_ref.get("logical_id", ""))
                or str(proposed_result.producer_invocation_id or "")
                != invocation_id
                or result is None
                or result.get("operation_result_ref")
                != operation_result_ref
                or result.get("invocation_ref") != invocation_ref
                or result.get("operation_execution_lease_ref") != lease_ref
                or result.get("terminal_outcome") != "completed"):
            return False

        output_refs = result.get("output_resource_refs")
        if (not isinstance(output_refs, list) or not output_refs
                or any(not exact_ref_exists(
                    value, "resource_version/v1") for value in output_refs)
                or output_refs != sorted(
                    output_refs,
                    key=lambda value: str(value.get("version_id", "")))
                or len({canonical_json(value) for value in output_refs})
                != len(output_refs)):
            return False

        matching_ready = [
            pending for pending in events
            if (pending.event_type == "operation_terminal_ready/v1"
                and pending.aggregate_id == invocation_id
                and pending.stream_id == f"invocation:{invocation_id}"
                and pending.aggregate_type == "invocation"
                and pending.idempotency_key == idempotency_key
                and pending.command_id == idempotency_key
                and pending.task_control
                and str(pending.producer_invocation_id or "")
                == invocation_id
                and pending.payload.get("invocation_ref")
                == invocation_ref
                and pending.payload.get(
                    "operation_execution_lease_ref") == lease_ref
                and pending.payload.get("operation_result_ref")
                == operation_result_ref)
        ]
        if len(matching_ready) != 1:
            return False
        ready = matching_ready[0].payload
        if (ready.get("terminal_outcome") != "completed"
                or ready.get("output_resource_refs") != output_refs):
            return False

        try:
            loop_rows = []
            for row in db.execute(
                    "SELECT logical_id,version_id,metadata_json,"
                    "transaction_id FROM objects "
                    "WHERE object_type='agent_loop/v1'").fetchall():
                metadata = json.loads(row["metadata_json"])
                if metadata.get("invocation_ref") == invocation_ref:
                    loop_rows.append((row, metadata))
            if not loop_rows:
                return False
            highest = max(int(value[1]["revision"])
                          for value in loop_rows)
            current = tuple(
                value for value in loop_rows
                if int(value[1]["revision"]) == highest)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if len(current) != 1:
            return False
        loop_row, loop = current[0]
        loop_id = str(loop.get("agent_loop_id", ""))
        loop_ref = loop.get("agent_loop_ref")
        if (loop.get("state") != "COMPLETED"
                or loop.get("operation_binding_ref") != binding_ref
                or not isinstance(loop_ref, Mapping)
                or loop_ref.get("logical_id") != loop_id
                or loop_ref.get("version_id")
                != str(loop_row["version_id"])):
            return False

        written_values = loop.get("written_resource_refs")
        if not isinstance(written_values, list):
            return False
        try:
            written_refs = tuple({
                "entity_type": "resource_version/v1",
                "logical_id": str(value["resource_id"]),
                "version_id": str(value["resource_version_id"]),
            } for value in written_values)
            cap_review_value = loop.get("cap_review_resource_ref")
            cap_review_refs = (() if cap_review_value is None else ({
                "entity_type": "resource_version/v1",
                "logical_id": str(cap_review_value["resource_id"]),
                "version_id": str(
                    cap_review_value["resource_version_id"]),
            },))
        except (KeyError, TypeError):
            return False
        terminal_resource_refs = written_refs or cap_review_refs
        if (bool(written_refs) == bool(cap_review_refs)
                or set(map(canonical_json, terminal_resource_refs))
                != set(map(canonical_json, output_refs))
                or len(set(map(canonical_json, terminal_resource_refs)))
                != len(terminal_resource_refs)):
            return False

        event_rows = db.execute(
            "SELECT event_type,payload_json,transaction_id,"
            "stream_sequence FROM events WHERE aggregate_id=? "
            "AND event_type IN "
            "('agent_action_settled/v1','agent_loop_terminal/v1') "
            "ORDER BY stream_sequence",
            (loop_id,),
        ).fetchall()
        try:
            loop_events = tuple(
                (row, json.loads(row["payload_json"]))
                for row in event_rows)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        terminal = tuple(
            value for value in loop_events
            if (value[0]["event_type"] == "agent_loop_terminal/v1"
                and int(value[1].get("revision", -1)) == highest))
        actions = tuple(
            value for value in loop_events
            if value[0]["event_type"] == "agent_action_settled/v1")
        terminal_actions = tuple(
            value for value in actions
            if int(value[1].get("revision", -1)) == highest)
        cap_handoff = (
            len(terminal) == 1
            and terminal[0][1].get("terminal_reason")
            == "llm_turn_cap_handoff")

        def is_exact_cap_slot_return(
                output_ref: Mapping[str, Any]) -> bool:
            """Prove one no-write output is a claimed document-slot return."""
            output_version = str(output_ref.get("version_id", ""))
            resource_row = db.execute(
                "SELECT metadata_json,transaction_id,"
                "producer_invocation_id FROM objects "
                "WHERE object_type='resource_version/v1' "
                "AND version_id=?",
                (output_version,),
            ).fetchone()
            if resource_row is None:
                return False
            try:
                output = json.loads(resource_row["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            descriptors = output.get("descriptors")
            if (not isinstance(descriptors, Mapping)
                    or descriptors.get("cap_handoff_kind")
                    != _AGENT_CAP_SLOT_RETURN_KIND):
                return False
            origin = output.get("origin")
            direct = output.get("reference_provenance")
            if (not isinstance(origin, Mapping)
                    or not isinstance(direct, Mapping)):
                return False
            derived = direct.get("derived_from_refs")
            if (not isinstance(derived, list) or len(derived) != 1
                    or not isinstance(derived[0], Mapping)):
                return False
            source_ref = derived[0]
            source_version = str(source_ref.get("version_id", ""))
            source = version_metadata(
                source_version, "resource_version/v1")
            output_binding_ref = origin.get("primary_ref")
            output_binding = (version_metadata(
                str(output_binding_ref.get("version_id", "")),
                "output_binding/v1")
                if isinstance(output_binding_ref, Mapping) else None)
            declared = (binding.get("output_binding_refs", [])
                        if isinstance(binding, Mapping) else [])
            relation_rows = exact_relation_records(
                "derived_from", source_version=output_version)
            if (source is None or output_binding is None
                    or source_ref.get("entity_type")
                    != "resource_version/v1"
                    or not exact_ref_exists(
                        source_ref, "resource_version/v1")
                    or output.get("origin_kind") != "petri_output"
                    or origin.get("kind") != "petri_output"
                    or output_binding_ref not in declared
                    or origin.get("secondary_ref")
                    != invocation.get("activation_ref")
                    or output.get("producer_ref") != invocation_ref
                    or output.get("lifetime_ref")
                    != invocation.get("activation_ref")
                    or direct.get("producer_invocation_ref")
                    != invocation_ref
                    or direct.get("operation_binding_ref")
                    != binding_ref
                    or resource_row["transaction_id"]
                    != terminal[0][0]["transaction_id"]
                    or str(resource_row["producer_invocation_id"] or "")
                    != invocation_id
                    or output_binding.get("normal_output_cardinality")
                    != {"minimum": 1, "maximum": 1}
                    or descriptors.get("output_port_id")
                    != output_binding.get("output_port_id")
                    or descriptors.get("place")
                    != output_binding.get("place")
                    or output.get("content_schema_ref")
                    != output_binding.get("content_schema_id")
                    or output.get("content_schema_authority_ref")
                    != output_binding.get("content_schema_ref")
                    or source.get("content_schema_ref")
                    != output.get("content_schema_ref")
                    or source.get("content_schema_authority_ref")
                    != output.get("content_schema_authority_ref")
                    or source.get("media_type")
                    != output.get("media_type")
                    or source.get("size") != output.get("size")
                    or descriptors.get("source_resource_id")
                    != source_ref.get("logical_id")
                    or descriptors.get("source_resource_version_id")
                    != source_version
                    or len(relation_rows) != 1
                    or relation_rows[0][1].get("entity_type")
                    != source_ref.get("entity_type")
                    or relation_rows[0][1].get("entity_id")
                    != source_ref.get("logical_id")
                    or relation_rows[0][1].get("version_id")
                    != source_ref.get("version_id")
                    or relation_rows[0][2]
                    != str(resource_row["transaction_id"])
                    or relation_rows[0][3] != "strong"):
                return False
            firing_ref = invocation.get("own_transition_firing_ref")
            firing = (version_metadata(
                str(firing_ref.get("version_id", "")),
                "transition_firing/v1")
                if isinstance(firing_ref, Mapping) else None)
            if firing is None:
                return False
            matches = []
            for token_ref in firing.get("claimed_input_refs", []):
                token = (version_metadata(
                    str(token_ref.get("version_id", "")),
                    "petri_token/v1")
                    if isinstance(token_ref, Mapping) else None)
                lease_identity = (
                    token.get("lease_identity_ref")
                    if isinstance(token, Mapping) else None)
                if (isinstance(lease_identity, Mapping)
                        and lease_identity.get("entity_type")
                        == "logical_artifact_slot/v1"
                        and token.get("resource_ref") == {
                            "resource_id": source_ref.get("logical_id"),
                            "resource_version_id": source_version,
                        }
                        and lease_identity.get("logical_id")
                        == descriptors.get("logical_slot_id")
                        and lease_identity.get("version_id")
                        == descriptors.get("logical_slot_version_id")):
                    matches.append(token)
            return len(matches) == 1

        def is_exact_cap_trace_summary(
                output_ref: Mapping[str, Any]) -> bool:
            """Prove one no-write output is the exact turn/action trace."""

            output_version = str(output_ref.get("version_id", ""))
            resource_row = db.execute(
                "SELECT metadata_json,transaction_id,"
                "producer_invocation_id FROM objects "
                "WHERE object_type='resource_version/v1' "
                "AND version_id=?",
                (output_version,),
            ).fetchone()
            if resource_row is None:
                return False
            try:
                output = json.loads(resource_row["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            descriptors = output.get("descriptors")
            if (not isinstance(descriptors, Mapping)
                    or descriptors.get("cap_handoff_kind")
                    != _AGENT_CAP_TRACE_SUMMARY_KIND
                    or descriptors.get("agent_loop_id") != loop_id):
                return False
            origin = output.get("origin")
            direct = output.get("reference_provenance")
            if (not isinstance(origin, Mapping)
                    or not isinstance(direct, Mapping)):
                return False
            output_binding_ref = origin.get("primary_ref")
            output_binding = (version_metadata(
                str(output_binding_ref.get("version_id", "")),
                "output_binding/v1")
                if isinstance(output_binding_ref, Mapping) else None)
            declared = (binding.get("output_binding_refs", [])
                        if isinstance(binding, Mapping) else [])
            derived = direct.get("derived_from_refs")
            if (output_binding is None
                    or not isinstance(derived, list)):
                return False
            turn_rows = db.execute(
                "SELECT payload_json FROM events WHERE aggregate_id=? "
                "AND event_type='agent_turn_recorded/v1' "
                "ORDER BY stream_sequence",
                (loop_id,),
            ).fetchall()
            try:
                turns = tuple(json.loads(row["payload_json"])
                              for row in turn_rows)
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            expected_derived = []
            for turn in turns:
                response = db.execute(
                    "SELECT logical_id,version_id FROM objects "
                    "WHERE object_type='resource_version/v1' "
                    "AND version_id=?",
                    (str(turn.get("response_version_id", "")),),
                ).fetchone()
                if response is None:
                    return False
                expected_derived.append({
                    "entity_type": "resource_version/v1",
                    "logical_id": str(response["logical_id"]),
                    "version_id": str(response["version_id"]),
                })
            action_count = int(db.execute(
                "SELECT COUNT(*) FROM events WHERE aggregate_id=? "
                "AND event_type='agent_action_settled/v1'",
                (loop_id,),
            ).fetchone()[0])
            source_loop_version = descriptors.get(
                "source_agent_loop_version_id")
            source_loop = db.execute(
                "SELECT logical_id FROM objects "
                "WHERE object_type='agent_loop/v1' AND version_id=?",
                (str(source_loop_version),),
            ).fetchone()
            llm_turns = terminal[0][1].get(
                "llm_turns_used")
            return (
                resource_row["transaction_id"]
                == terminal[0][0]["transaction_id"]
                and str(resource_row["producer_invocation_id"] or "")
                == invocation_id
                and output.get("origin_kind") == "petri_output"
                and origin.get("kind") == "petri_output"
                and output_binding_ref in declared
                and origin.get("secondary_ref")
                == invocation.get("activation_ref")
                and output.get("producer_ref") == invocation_ref
                and output.get("lifetime_ref")
                == invocation.get("activation_ref")
                and direct.get("producer_invocation_ref")
                == invocation_ref
                and direct.get("operation_binding_ref") == binding_ref
                and derived == expected_derived
                and isinstance(llm_turns, int)
                and len(turns) == llm_turns
                and [turn.get("sequence") for turn in turns]
                == list(range(llm_turns))
                and descriptors.get("llm_turn_count")
                == llm_turns
                and descriptors.get("settled_action_count")
                == action_count
                and source_loop is not None
                and str(source_loop["logical_id"]) == loop_id
                and output_binding.get("normal_output_cardinality")
                == {"minimum": 1, "maximum": 1}
                and descriptors.get("output_port_id")
                == output_binding.get("output_port_id")
                and descriptors.get("place")
                == output_binding.get("place")
                and output.get("content_schema_ref")
                == "registry_v1/agent_document/v2"
                and output.get("content_schema_ref")
                == output_binding.get("content_schema_id")
                and output.get("content_schema_authority_ref")
                == output_binding.get("content_schema_ref")
                and output.get("media_type") == "text/plain"
                and isinstance(output.get("size"), int)
                and output.get("size") > 0
                and all(exact_ref_exists(
                    value, "resource_version/v1")
                    for value in derived))

        def is_exact_cap_review_bundle(
                output_ref: Mapping[str, Any]) -> bool:
            """Prove the dedicated cap subject against actor history."""

            output_version = str(output_ref.get("version_id", ""))
            resource_row = db.execute(
                "SELECT metadata_json,transaction_id,"
                "producer_invocation_id,storage_locator FROM objects "
                "WHERE object_type='resource_version/v1' "
                "AND version_id=?",
                (output_version,),
            ).fetchone()
            if resource_row is None:
                return False
            try:
                output = json.loads(resource_row["metadata_json"])
                payload = json.loads(Path(
                    str(resource_row["storage_locator"])).read_bytes())
            except (OSError, TypeError, ValueError,
                    json.JSONDecodeError):
                return False
            descriptors = output.get("descriptors")
            origin = output.get("origin")
            direct = output.get("reference_provenance")
            if (not isinstance(descriptors, Mapping)
                    or descriptors.get("cap_handoff_kind")
                    != _AGENT_CAP_REVIEW_BUNDLE_KIND
                    or descriptors.get("content_role")
                    != "llm_turn_cap_review_subject"
                    or not isinstance(origin, Mapping)
                    or not isinstance(direct, Mapping)):
                return False
            output_binding_ref = origin.get("primary_ref")
            output_binding = (version_metadata(
                str(output_binding_ref.get("version_id", "")),
                "output_binding/v1")
                if isinstance(output_binding_ref, Mapping) else None)
            declared = (binding.get("output_binding_refs", [])
                        if isinstance(binding, Mapping) else [])
            derived = direct.get("derived_from_refs")
            response_refs = payload.get("all_actor_response_refs")
            action_refs = payload.get("actor_action_refs")
            partial_refs = payload.get("actor_partial_output_refs")
            input_refs = payload.get("actor_input_resource_refs")
            if (output_binding is None
                    or not all(isinstance(value, list) for value in (
                        derived, response_refs, action_refs,
                        partial_refs, input_refs))):
                return False
            turn_rows = db.execute(
                "SELECT payload_json FROM events WHERE aggregate_id=? "
                "AND event_type='agent_turn_recorded/v1' "
                "ORDER BY stream_sequence",
                (loop_id,),
            ).fetchall()
            action_rows = db.execute(
                "SELECT payload_json,transaction_id,"
                "producer_invocation_id FROM events WHERE aggregate_id=? "
                "AND event_type='agent_action_settled/v1' "
                "ORDER BY stream_sequence",
                (loop_id,),
            ).fetchall()
            try:
                turns = tuple(json.loads(row["payload_json"])
                              for row in turn_rows)
                actions_payload = tuple(
                    json.loads(row["payload_json"])
                    for row in action_rows)
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            response_versions = [
                str(value.get("resource_version_id", ""))
                for value in response_refs
                if isinstance(value, Mapping)]
            action_versions = [
                str(value.get("version_id", ""))
                for value in action_refs
                if isinstance(value, Mapping)]
            expected_response_versions = [
                str(value.get("response_version_id", ""))
                for value in turns]
            expected_action_versions = []
            for event_row, value in zip(
                    action_rows, actions_payload, strict=True):
                action_objects = db.execute(
                    "SELECT version_id FROM objects "
                    "WHERE object_type='agent_action/v2' "
                    "AND logical_id=? AND transaction_id=? "
                    "AND COALESCE(producer_invocation_id,'')="
                    "COALESCE(?,'') ORDER BY rowid",
                    (str(value.get("agent_action_id", "")),
                     str(event_row["transaction_id"]),
                     event_row["producer_invocation_id"]),
                ).fetchall()
                if len(action_objects) != 1:
                    return False
                expected_action_versions.append(
                    str(action_objects[0]["version_id"]))
            actor_loop_ref = payload.get("actor_agent_loop_ref")
            source_loop = (version_metadata(
                str(actor_loop_ref.get("version_id", "")),
                "agent_loop/v1")
                if isinstance(actor_loop_ref, Mapping) else None)
            recent = payload.get("recent_actor_messages")
            llm_turns = terminal[0][1].get(
                "llm_turns_used")
            source_turn_budget = (
                source_loop.get("llm_turn_budget")
                if isinstance(source_loop, Mapping) else None)
            try:
                task_row = db.execute(
                    "SELECT value FROM registry_meta WHERE key='task_id'",
                ).fetchone()
                if task_row is None:
                    return False
                registered_task_limit, _hard_limit = (
                    _registered_model_call_limits(
                        db, task_id=str(task_row["value"])))
            except ValueError:
                return False
            boundary = descriptors.get(
                "handoff_boundary", "llm_turn_cap")
            cap_boundary_valid = (
                (boundary == "llm_turn_cap"
                 and llm_turns == source_turn_budget)
                or (boundary == "task_model_call_cap"
                    and isinstance(llm_turns, int)
                    and isinstance(source_turn_budget, int)
                    and llm_turns < source_turn_budget
                    and descriptors.get("task_model_calls")
                    == registered_task_limit
                    and descriptors.get("task_model_call_limit")
                    == registered_task_limit))
            return (
                resource_row["transaction_id"]
                == terminal[0][0]["transaction_id"]
                and str(resource_row["producer_invocation_id"] or "")
                == invocation_id
                and output.get("media_type") == "application/json"
                and output.get("content_schema_ref")
                == "registry_v1/llm_turn_cap_review_bundle/v2"
                and payload.get("kind")
                == _AGENT_CAP_REVIEW_BUNDLE_KIND
                and payload.get("variant")
                == "settled_actor_firing"
                and output.get("origin_kind") == "petri_output"
                and origin.get("kind") == "petri_output"
                and output_binding_ref in declared
                and origin.get("secondary_ref")
                == invocation.get("activation_ref")
                and output.get("producer_ref") == invocation_ref
                and output.get("lifetime_ref")
                == invocation.get("activation_ref")
                and direct.get("producer_invocation_ref")
                == invocation_ref
                and direct.get("operation_binding_ref") == binding_ref
                and output_binding.get("output_port_id")
                == "framework_llm_turn_cap_review"
                and output_binding.get("normal_output_cardinality")
                == {"minimum": 0, "maximum": 1}
                and output_binding.get("content_schema_id")
                == output.get("content_schema_ref")
                and output_binding.get("content_schema_ref")
                == output.get("content_schema_authority_ref")
                and descriptors.get("output_port_id")
                == output_binding.get("output_port_id")
                and descriptors.get("place")
                == output_binding.get("place")
                and descriptors.get("agent_loop_id") == loop_id
                and descriptors.get("source_agent_loop_version_id")
                == str(actor_loop_ref.get("version_id", ""))
                and isinstance(llm_turns, int)
                and payload.get("llm_turn_count") == llm_turns
                and payload.get("llm_turn_budget")
                == source_turn_budget
                and cap_boundary_valid
                and response_versions == expected_response_versions
                and action_versions == expected_action_versions
                and len(response_versions) == llm_turns
                and payload.get("settled_action_count")
                == len(action_versions)
                and descriptors.get("settled_action_count")
                == len(action_versions)
                and isinstance(recent, list)
                and len(recent) == min(5, llm_turns)
                and payload.get("decision_scope")
                == ["continue", "give_up"]
                and source_loop is not None
                and source_loop.get("state") == "WAITING_FOR_LLM"
                and source_loop.get("llm_turns_used")
                == llm_turns
                and all(exact_resource_ref_exists(value)
                    for value in (*response_refs, *partial_refs,
                                  *input_refs))
                and all(exact_ref_exists(value, "agent_action/v2")
                        for value in action_refs)
                and {canonical_json({
                    "entity_type": "resource_version/v1",
                    "logical_id": value.get("resource_id"),
                    "version_id": value.get("resource_version_id"),
                }) for value in (
                    *response_refs, *partial_refs, *input_refs)}
                .issubset({canonical_json(value) for value in derived}))

        cap_slot_return_keys = {
            canonical_json(output_ref) for output_ref in written_refs
            if cap_handoff and is_exact_cap_slot_return(output_ref)}
        cap_trace_summary_keys = {
            canonical_json(output_ref) for output_ref in written_refs
            if cap_handoff and is_exact_cap_trace_summary(output_ref)}
        cap_review_bundle_keys = {
            canonical_json(output_ref)
            for output_ref in cap_review_refs
            if cap_handoff and is_exact_cap_review_bundle(output_ref)}
        cap_framework_output_keys = (
            cap_slot_return_keys | cap_trace_summary_keys
            | cap_review_bundle_keys)
        written_keys = {
            canonical_json(ref) for ref in written_refs}
        cap_semantic_candidate_keys = (
            written_keys - cap_framework_output_keys)
        if (cap_slot_return_keys
                and (len(cap_slot_return_keys) != 1
                     or len(written_refs) != 1
                     or cap_trace_summary_keys)):
            return False
        if cap_trace_summary_keys:
            mandatory_port_ids = set()
            mandatory_bindings_valid = True
            for raw_ref in (binding.get(
                    "output_binding_refs", [])
                    if isinstance(binding, Mapping) else []):
                output_binding = (version_metadata(
                    str(raw_ref.get("version_id", "")),
                    "output_binding/v1")
                    if isinstance(raw_ref, Mapping) else None)
                if output_binding is None:
                    mandatory_bindings_valid = False
                    break
                cardinality = output_binding.get(
                    "normal_output_cardinality")
                if (isinstance(cardinality, Mapping)
                        and int(cardinality.get("minimum", 0)) > 0):
                    if (output_binding.get("content_schema_id")
                            != "registry_v1/agent_document/v2"):
                        mandatory_bindings_valid = False
                        break
                    mandatory_port_ids.add(str(
                        output_binding.get("output_port_id")))
            trace_port_ids = set()
            for output_ref in written_refs:
                resource_row = db.execute(
                    "SELECT metadata_json FROM objects "
                    "WHERE object_type='resource_version/v1' "
                    "AND version_id=?",
                    (str(output_ref.get("version_id", "")),),
                ).fetchone()
                if resource_row is None:
                    return False
                try:
                    resource = json.loads(
                        resource_row["metadata_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False
                descriptors = resource.get("descriptors")
                if not isinstance(descriptors, Mapping):
                    return False
                trace_port_ids.add(str(
                    descriptors.get("output_port_id")))
            if (cap_slot_return_keys
                    or cap_trace_summary_keys != {
                        canonical_json(ref) for ref in written_refs}
                    or not mandatory_bindings_valid
                    or trace_port_ids != mandatory_port_ids
                    or len(trace_port_ids)
                    != len(cap_trace_summary_keys)):
                return False
        if cap_handoff and cap_semantic_candidate_keys:
            if cap_framework_output_keys:
                return False
            counts: dict[str, int] = {}
            selected_branches: set[str] = set()
            declared_refs = (
                binding.get("output_binding_refs", [])
                if isinstance(binding, Mapping) else [])
            declared_keys = {
                canonical_json(ref) for ref in declared_refs
                if isinstance(ref, Mapping)}
            if len(declared_keys) != len(declared_refs):
                return False
            for output_ref in written_refs:
                resource = version_metadata(
                    str(output_ref.get("version_id", "")),
                    "resource_version/v1")
                origin = (resource.get("origin")
                          if isinstance(resource, Mapping) else None)
                direct = (resource.get("reference_provenance")
                          if isinstance(resource, Mapping) else None)
                bound_ref = (origin.get("primary_ref")
                             if isinstance(origin, Mapping) else None)
                bound_key = (canonical_json(bound_ref)
                             if isinstance(bound_ref, Mapping) else "")
                output_binding = (version_metadata(
                    str(bound_ref.get("version_id", "")),
                    "output_binding/v1")
                    if isinstance(bound_ref, Mapping) else None)
                if (output_binding is None
                        or bound_key not in declared_keys
                        or not isinstance(direct, Mapping)
                        or resource.get("origin_kind") != "petri_output"
                        or origin.get("kind") != "petri_output"
                        or origin.get("secondary_ref")
                        != invocation.get("activation_ref")
                        or resource.get("producer_ref")
                        != invocation_ref
                        or direct.get("producer_invocation_ref")
                        != invocation_ref
                        or direct.get("operation_binding_ref")
                        != binding_ref):
                    return False
                counts[bound_key] = counts.get(bound_key, 0) + 1
                if output_binding.get("declared_outcome_id") is not None:
                    selected_branches.add(bound_key)
            for raw_ref in declared_refs:
                output_binding = version_metadata(
                    str(raw_ref.get("version_id", "")),
                    "output_binding/v1")
                cardinality = (output_binding.get(
                    "normal_output_cardinality")
                    if isinstance(output_binding, Mapping) else None)
                observed = counts.get(canonical_json(raw_ref), 0)
                if (not isinstance(cardinality, Mapping)
                        or isinstance(cardinality.get("minimum"), bool)
                        or not isinstance(cardinality.get("minimum"), int)
                        or isinstance(cardinality.get("maximum"), bool)
                        or not isinstance(cardinality.get("maximum"), int)
                        or observed < cardinality["minimum"]
                        or observed > cardinality["maximum"]):
                    return False
            if len(selected_branches) > 1:
                return False
        if (len(terminal) != 1
                or (not actions and not cap_framework_output_keys)
                or (cap_handoff and terminal_actions)
                or (not cap_handoff and not terminal_actions)
                or (terminal_actions
                    and tuple(int(value[1].get(
                        "tool_call_ordinal", -1))
                        for value in terminal_actions)
                    != tuple(range(len(terminal_actions))))
                or any(value[0]["transaction_id"]
                       != terminal[0][0]["transaction_id"]
                       for value in terminal_actions)
                or loop_row["transaction_id"]
                != terminal[0][0]["transaction_id"]):
            return False

        action_records = []
        for event_row, event_payload in actions:
            rows = db.execute(
                "SELECT metadata_json,transaction_id FROM objects "
                "WHERE object_type='agent_action/v2' AND logical_id=?",
                (str(event_payload.get("agent_action_id", "")),),
            ).fetchall()
            if (len(rows) != 1
                    or rows[0]["transaction_id"]
                    != event_row["transaction_id"]):
                return False
            try:
                action = json.loads(rows[0]["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            result_refs = action.get("result_refs")
            action_loop_ref = action.get("agent_loop_ref")
            result_version_ids = event_payload.get(
                "result_version_ids")
            if (not isinstance(action_loop_ref, Mapping)
                    or action_loop_ref.get("logical_id") != loop_id
                    or action.get("tool_name")
                    != event_payload.get("tool_name")
                    or action.get("state")
                    != event_payload.get("settlement")
                    or not isinstance(result_refs, list)
                    or not isinstance(result_version_ids, list)
                    or [str(value.get("version_id", ""))
                        for value in result_refs
                        if isinstance(value, Mapping)]
                    != result_version_ids
                    or len(result_refs) != len(result_version_ids)):
                return False
            action_records.append((event_row, event_payload, action))

        completions = tuple(
            value for value in action_records
            if value[2].get("tool_name") == "complete_interaction")
        normal_completion = (
            not cap_handoff
            and len(completions) == 1
            and completions[0] == action_records[-1]
            and (completions[0][0], completions[0][1])
            in terminal_actions
            and completions[0][2].get("state") == "COMPLETED"
            and tuple(completions[0][2].get("result_refs", []))
            == written_refs)
        cap_completion = (
            cap_handoff and not completions
            and bool(cap_review_bundle_keys) != bool(written_refs))
        if not (normal_completion or cap_completion):
            return False

        for output_ref in terminal_resource_refs:
            writes = tuple(
                value for value in action_records
                if (value[2].get("tool_name") == "write_file"
                    and value[2].get("result_refs") == [output_ref]))
            if len(writes) != 1:
                if (not writes
                        and canonical_json(output_ref)
                        in cap_framework_output_keys):
                    continue
                return False
            write_row, _write_payload, write = writes[0]
            result_metadata = write.get("result_metadata")
            relative = (result_metadata.get("path")
                        if isinstance(result_metadata, Mapping)
                        else None)
            resource = db.execute(
                "SELECT object_type,transaction_id,"
                "producer_invocation_id FROM objects WHERE version_id=?",
                (str(output_ref["version_id"]),),
            ).fetchone()
            addresses = []
            for address_row in db.execute(
                    "SELECT metadata_json FROM objects "
                    "WHERE object_type='resource_address_binding/v1' "
                    "AND transaction_id=?",
                    (str(write_row["transaction_id"]),)).fetchall():
                try:
                    address = json.loads(address_row["metadata_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False
                if (address.get("scope_ref") == invocation_ref
                        and address.get("opaque_name") == relative
                        and address.get("resource_ref") == {
                            "resource_id": output_ref["logical_id"],
                            "resource_version_id": output_ref["version_id"],
                        }
                        and address.get("authorization_ref")
                        == binding_ref
                        and address.get("lifecycle_state") == "bound"
                        and address.get("commit_transaction_id")
                        == str(write_row["transaction_id"])):
                    addresses.append(address)
            if (not isinstance(relative, str)
                    or resource is None
                    or resource["object_type"] != "resource_version/v1"
                    or resource["transaction_id"]
                    != write_row["transaction_id"]
                    or resource["producer_invocation_id"]
                    != invocation_id
                    or len(addresses) != 1):
                return False
        return True

    def is_exact_recorded_completion_recovery() -> bool:
        if (not allow_recorded_completion_recovery
                or boundary != "settlement"
                or current_writer_epoch is None
                or lease_writer_epoch is None
                or lease_writer_epoch >= current_writer_epoch
                or not isinstance(operation_result_ref, Mapping)):
            return False
        # A failed resume may reserve a writer epoch without publishing any
        # facts.  Such an empty fence gap is safe to cross; any committed fact
        # from a later writer means the stale completion is no longer the
        # current exact settlement authority.
        if db.execute(
                "SELECT 1 FROM events WHERE writer_fencing_epoch>? LIMIT 1",
                (lease_writer_epoch,),
        ).fetchone() is not None:
            return False
        rows = db.execute(
            "SELECT writer_fencing_epoch,producer_invocation_id,payload_json "
            "FROM events WHERE event_type="
            "'registered_operation_completion_recorded/v1' "
            "AND aggregate_id=?",
            (str(lease_ref.get("logical_id", "")),),
        ).fetchall()
        if len(rows) != 1:
            return False
        try:
            completion = json.loads(rows[0]["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        result_item = new_by_version.get(str(
            operation_result_ref.get("version_id", "")))
        result = (dict(result_item.metadata)
                  if result_item is not None else None)
        completion_output_refs = sorted(({
            "entity_type": "resource_version/v1",
            "logical_id": item.get("resource_ref", {}).get("resource_id"),
            "version_id": item.get("resource_ref", {}).get(
                "resource_version_id"),
        } for item in completion.get("ordered_outputs", [])
            if isinstance(item, Mapping)), key=canonical_json)
        return bool(
            str(rows[0]["producer_invocation_id"]) == invocation_id
            and int(rows[0]["writer_fencing_epoch"]) == lease_writer_epoch
            and completion.get("invocation_ref") == invocation_ref
            and completion.get("transition_firing_ref")
            == invocation.get("own_transition_firing_ref")
            and completion.get("operation_execution_lease_ref") == lease_ref
            and completion.get("operation_binding_ref") == binding_ref
            and completion.get("admission_writer_fencing_epoch")
            == lease_writer_epoch
            and isinstance(result, Mapping)
            and result.get("invocation_ref") == invocation_ref
            and result.get("output_resource_refs") == completion_output_refs)

    if (lease is None
            or lease.get("invocation_ref") != invocation_ref
            or current_writer_epoch is None
            or (lease_writer_epoch != current_writer_epoch
                and not is_exact_committed_agent_terminal_ready()
                and not has_exact_historical_terminal_ready_authorization()
                and not is_exact_recorded_completion_recovery())):
        raise RegistryConflict(f"{boundary} has a stale operation lease")

    invocation_events = db.execute(
        "SELECT event_type FROM events WHERE aggregate_id=?",
        (invocation_id,),
    ).fetchall()
    invocation_types = [str(row["event_type"]) for row in invocation_events]
    if invocation_types.count("invocation_started/v1") != 1:
        raise RegistryConflict(f"{boundary} has no unique open invocation")
    persisted_terminal_ready = invocation_types.count(
        "operation_terminal_ready/v1")
    if boundary == "settlement":
        if (persisted_terminal_ready != 0
                or not has_exact_pending_terminal_ready_settlement()):
            raise RegistryConflict(
                "settlement requires one new exact terminal-ready audit fact")
    elif persisted_terminal_ready or pending_terminal_ready:
        raise RegistryConflict(
            f"{boundary} uses a terminal-ready invocation")

    lease_id = str(lease_ref.get("logical_id", ""))
    lease_events = db.execute(
        "SELECT event_type FROM events WHERE aggregate_id=?",
        (lease_id,),
    ).fetchall()
    lease_types = [str(row["event_type"]) for row in lease_events]
    if invocation.get("origin") != "petri_operation":
        raise RegistryConflict(
            f"{boundary} has an unknown invocation origin")
    if lease_types.count("operation_execution_started/v1") != 1:
        raise RegistryConflict(f"{boundary} uses a closed operation lease")

    current_marking = latest_event_payload(
        "marking_checkpoint_committed/v1",
        event_task_id=str(task_id),
        event_net_id=str(net_ref.get("logical_id", "")),
        include_pending=False)
    if current_marking is None:
        raise RegistryConflict(f"{boundary} has no current marking authority")

    if invocation.get("origin") == "petri_operation":
        firing_ref = invocation.get("own_transition_firing_ref")
        firing = version_metadata(
            str(firing_ref.get("version_id", "")),
            "transition_firing/v1") if isinstance(firing_ref, Mapping) else None
        firing_id = str(firing_ref.get("logical_id", "")) if isinstance(
            firing_ref, Mapping) else ""
        firing_rows = db.execute(
            "SELECT event_type FROM events WHERE aggregate_id=?",
            (firing_id,),
        ).fetchall()
        firing_types = [str(row["event_type"]) for row in firing_rows]
        checkpoint = version_metadata(
            str((current_marking.get("checkpoint_ref") or {}).get(
                "version_id", "")),
            "marking_checkpoint/v1")
        token_refs = (checkpoint.get("token_refs", [])
                      if isinstance(checkpoint, Mapping) else [])
        present = {
            str(value.get("version_id", "")) for value in token_refs
            if isinstance(value, Mapping)
        }
        claimed = set(firing.get("claimed_input_version_ids", [])) if isinstance(
            firing, Mapping) else set()
        if (firing is None
                or firing_types.count("firing_admitted/v1") != 1
                or "transition_firing_settled/v1" in firing_types
                or "transition_firing_superseded_by_growth_recovery/v1"
                in firing_types
                or "transition_firing_superseded_by_native_resume/v1"
                in firing_types
                or not claimed.issubset(present)):
            raise RegistryConflict(
                f"{boundary} has no active firing/current marking authority")
    for activation_ref in (
            invocation.get("activation_ref"),
            invocation.get("authorization_lifetime_activation_ref")):
        if activation_ref is None:
            continue
        if not exact_ref_exists(activation_ref):
            raise RegistryConflict(f"{boundary} has a missing activation authority")

__all__ = (
    "validate_agent_loop_atomicity",
    "validate_current_invocation_authority",
)
