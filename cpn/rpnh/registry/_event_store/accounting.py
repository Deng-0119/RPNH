"""Model-call accounting and published baseline commands for EventStore."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Mapping

from ..identities import TypedId, new_id
from ..models import VersionRef
from ..schema_catalog import canonical_text

_ACTUAL_MODEL_CALL_EVENT_TYPES = frozenset({
    "llm_invocation_interrupted/v1",
    "llm_invocation_succeeded/v1",
})

def _returned_attempt_counts_toward_cap(
        attempt_metadata: Mapping[str, Any],
        invocation_metadata: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
) -> bool:
    """Classify already-bound registered attempts, not scope/role names.

    Publication/admission owns exact invocation, binding and manifest checks;
    this predicate does not establish authority from supplied metadata.
    Compaction retains its existing unconditional task-call accounting.
    """

    if (not isinstance(attempt_metadata, Mapping)
            or not isinstance(invocation_metadata, Mapping)
            or not isinstance(request_metadata, Mapping)):
        raise TypeError("returned-call classification requires metadata")
    invocation_kind = invocation_metadata.get("invocation_kind")
    return bool(
        invocation_kind == "context_compaction"
        or (invocation_kind in {"normal_turn", "delegated_subtask"}
            and "finalization_scope" in attempt_metadata
            and attempt_metadata["finalization_scope"] is None))

def _project_actual_model_call_counts(
        total_returned_calls: int, limit: int | None,
) -> tuple[int, int]:
    """Project the raw settled total and its post-limit excess."""

    if (isinstance(total_returned_calls, bool)
            or not isinstance(total_returned_calls, int)
            or total_returned_calls < 0
            or (limit is not None
                and (isinstance(limit, bool) or not isinstance(limit, int)
                     or limit < 1))):
        raise ValueError("actual model-call projection values are invalid")
    if limit is None:
        return total_returned_calls, 0
    return total_returned_calls, max(0, total_returned_calls - limit)

def _accountable_model_call_event_sql() -> str:
    """Return exact permanent/current/mechanically-closed call ownership."""

    return (
        "EXISTS (SELECT 1 FROM firing_temporary_members accountable_member "
        "JOIN firing_publications accountable_publication ON "
        "accountable_publication.firing_version_id="
        "accountable_member.firing_version_id "
        "JOIN transactions accountable_opening ON "
        "accountable_opening.transaction_id="
        "accountable_publication.opened_transaction_id "
        "LEFT JOIN transactions accountable_settlement ON "
        "accountable_settlement.transaction_id="
        "accountable_publication.published_transaction_id "
        "WHERE accountable_member.member_kind='event' "
        "AND accountable_member.member_identity=e.event_id AND ("
        "(accountable_publication.state='PUBLISHED' "
        "AND accountable_publication.published_transaction_id IS NOT NULL "
        "AND accountable_settlement.status='committed') OR "
        "(accountable_publication.state='PROVISIONAL' "
        "AND accountable_opening.status='committed' "
        "AND accountable_opening.writer_epoch=CAST((SELECT value FROM "
        "registry_meta WHERE key='writer_epoch') AS INTEGER)) OR "
        + _task_model_call_cap_handoff_member_authority_sql(
            member_alias="accountable_member",
            publication_alias="accountable_publication") + "))"
    )

def _accountable_returned_model_call_attempt_ids(
        db: sqlite3.Connection, *, task_id: str | None = None,
) -> set[str]:
    """Select returned calls owned by permanent or current-writer firings."""

    placeholders = ",".join(
        "?" for _value in _ACTUAL_MODEL_CALL_EVENT_TYPES)
    task_clause = "" if task_id is None else "AND e.task_id=? "
    parameters: tuple[object, ...] = tuple(sorted(
        _ACTUAL_MODEL_CALL_EVENT_TYPES))
    if task_id is not None:
        parameters = (*parameters, task_id)
    rows = db.execute(
        "SELECT DISTINCT e.aggregate_id FROM events e "
        "JOIN transactions event_transaction ON "
        "event_transaction.transaction_id=e.transaction_id "
        "WHERE event_transaction.status='committed' "
        f"AND e.event_type IN ({placeholders}) {task_clause}"
        f"AND {_accountable_model_call_event_sql()}",
        parameters,
    ).fetchall()
    return {str(row["aggregate_id"]) for row in rows}

def _accountable_returned_task_model_call_attempt_version_ids(
        db: sqlite3.Connection, *, task_id: str | None = None,
) -> set[str]:
    """Classify exact accountable returned task-call attempt versions."""
    from ..event_store import RegistryCorruptError

    result: set[str] = set()
    for attempt_id in _accountable_returned_model_call_attempt_ids(
            db, task_id=task_id):
        attempts = db.execute(
            "SELECT o.logical_id,o.version_id,o.metadata_json FROM objects o "
            "JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.status='committed' AND o.logical_id=? "
            "AND o.object_type='llm_invocation_attempt/v1' "
            + ("" if task_id is None else "AND t.task_id=?"),
            ((attempt_id,) if task_id is None else (attempt_id, task_id)),
        ).fetchall()
        if len(attempts) != 1:
            raise RegistryCorruptError(
                "accountable model call has no unique attempt authority")
        attempt = json.loads(str(attempts[0]["metadata_json"]))
        invocation_ref = attempt.get("llm_invocation_ref")
        invocation_version = (
            str(invocation_ref.get("version_id", ""))
            if isinstance(invocation_ref, Mapping) else "")
        invocation_logical = (
            str(invocation_ref.get("logical_id", ""))
            if isinstance(invocation_ref, Mapping) else "")
        invocation_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='llm_invocation_spec/v1'",
            (invocation_version,),
        ).fetchone()
        if (invocation_row is None
                or str(invocation_row["logical_id"]) != invocation_logical):
            raise RegistryCorruptError(
                "accountable model call has no exact logical invocation")
        invocation = json.loads(str(invocation_row["metadata_json"]))
        request_ref = invocation.get("request_resource_ref")
        request_version = (
            str(request_ref.get("resource_version_id", ""))
            if isinstance(request_ref, Mapping) else "")
        request_row = db.execute(
            "SELECT metadata_json FROM objects WHERE version_id=? "
            "AND object_type='resource_version/v1'",
            (request_version,),
        ).fetchone()
        if request_row is None:
            raise RegistryCorruptError(
                "accountable model call request resource is missing")
        request = json.loads(str(request_row["metadata_json"]))
        if _returned_attempt_counts_toward_cap(
                attempt, invocation, request):
            result.add(str(attempts[0]["version_id"]))
    return result

def _registered_raw_return_attempt_version_ids(
        db: sqlite3.Connection, *, task_id: str,
) -> set[str]:
    """Count current raw returns by committed exact attempt/budget lineage.

    Tagged terminal buckets retain their separate quota; the ordinary task
    cap is not inferred from an executor/role or an application turn record.
    """
    rows = db.execute(
        "SELECT attempt.version_id FROM relations counted "
        "JOIN transactions return_tx ON return_tx.transaction_id=counted.transaction_id "
        "JOIN objects attempt ON attempt.version_id=json_extract(counted.source_json,'$.version_id') "
        "AND attempt.logical_id=json_extract(counted.source_json,'$.entity_id') "
        "JOIN transactions attempt_tx ON attempt_tx.transaction_id=attempt.transaction_id "
        "JOIN objects budget ON budget.version_id=json_extract(counted.target_json,'$.version_id') "
        "AND budget.logical_id=json_extract(counted.target_json,'$.entity_id') "
        "JOIN objects call ON call.version_id=json_extract(attempt.metadata_json,'$.llm_call_ref.version_id') "
        "AND call.logical_id=json_extract(attempt.metadata_json,'$.llm_call_ref.logical_id') "
        "JOIN events observed ON observed.transaction_id=counted.transaction_id "
        "AND observed.event_type='provider_attempt_submission_observed/v1' "
        "AND json_extract(observed.payload_json,'$.provider_attempt_ref.version_id')=attempt.version_id "
        "AND json_extract(observed.payload_json,'$.provider_attempt_ref.logical_id')=attempt.logical_id "
        "JOIN objects response ON response.transaction_id=counted.transaction_id "
        "AND response.version_id=json_extract(observed.payload_json,'$.response_resource_ref.resource_version_id') "
        "AND response.logical_id=json_extract(observed.payload_json,'$.response_resource_ref.resource_id') "
        "WHERE return_tx.status='committed' AND attempt_tx.status='committed' "
        "AND return_tx.task_id=? AND attempt_tx.task_id=? AND observed.task_id=? "
        "AND counted.relation_type='actual_model_call_recorded_against_limit' "
        "AND counted.strength='strong' "
        "AND json_extract(counted.source_json,'$.entity_type')='provider_attempt_spec/v1' "
        "AND json_extract(counted.target_json,'$.entity_type')='task_recovery_manifest/v1' "
        "AND attempt.object_type='provider_attempt_spec/v1' "
        "AND budget.object_type='task_recovery_manifest/v1' "
        "AND call.object_type IN ('llm_call_spec/v2','llm_call_spec/v3') "
        "AND json_extract(attempt.metadata_json,'$.budget_witness_ref.version_id')=budget.version_id "
        "AND json_extract(attempt.metadata_json,'$.budget_witness_ref.logical_id')=budget.logical_id "
        "AND json_type(attempt.metadata_json,'$.finalization_scope')='null' "
        "AND response.object_type='resource_version/v1' "
        "AND json_extract(response.metadata_json,'$.origin_kind')='provider_raw_response'",
        (task_id, task_id, task_id),
    ).fetchall()
    return {str(row["version_id"]) for row in rows}

def _physical_model_call_attempt_version_id(
        db: sqlite3.Connection, *, task_id: str,
        attempt_version_id: str,
) -> str:
    """Resolve an optional neutral attempt to its exact Core provider attempt."""
    from ..event_store import RegistryCorruptError

    rows = db.execute(
        "SELECT provider.version_id FROM relations linkage "
        "JOIN transactions linkage_tx ON "
        "linkage_tx.transaction_id=linkage.transaction_id "
        "JOIN objects neutral ON "
        "neutral.version_id=json_extract(linkage.source_json,'$.version_id') "
        "AND neutral.logical_id=json_extract(linkage.source_json,'$.entity_id') "
        "JOIN transactions neutral_tx ON "
        "neutral_tx.transaction_id=neutral.transaction_id "
        "JOIN objects provider ON "
        "provider.version_id=json_extract(linkage.target_json,'$.version_id') "
        "AND provider.logical_id=json_extract(linkage.target_json,'$.entity_id') "
        "JOIN transactions provider_tx ON "
        "provider_tx.transaction_id=provider.transaction_id "
        "WHERE linkage_tx.status='committed' "
        "AND neutral_tx.status='committed' "
        "AND provider_tx.status='committed' "
        "AND linkage_tx.task_id=? AND neutral_tx.task_id=? "
        "AND provider_tx.task_id=? "
        "AND linkage.relation_type='derived_from' "
        "AND linkage.strength='strong' "
        "AND neutral.object_type='llm_invocation_attempt/v1' "
        "AND neutral.version_id=? "
        "AND provider.object_type='provider_attempt_spec/v1' "
        "AND json_extract(linkage.source_json,'$.entity_type')="
        "'llm_invocation_attempt/v1' "
        "AND json_extract(linkage.target_json,'$.entity_type')="
        "'provider_attempt_spec/v1'",
        (task_id, task_id, task_id, attempt_version_id),
    ).fetchall()
    if len(rows) > 1:
        raise RegistryCorruptError(
            "one neutral model attempt links to multiple provider attempts")
    return (str(rows[0]["version_id"]) if rows else attempt_version_id)

def _cumulative_task_model_call_attempt_version_ids(
        db: sqlite3.Connection, *, task_id: str,
        pending_attempt_version_ids: Iterable[str] = (),
) -> set[str]:
    """Select committed task-call consumption by category and exact attempt."""

    result = {
        _physical_model_call_attempt_version_id(
            db, task_id=task_id, attempt_version_id=str(value))
        for value in pending_attempt_version_ids
    }
    result.update(_registered_raw_return_attempt_version_ids(db, task_id=task_id))
    result.update(
        _physical_model_call_attempt_version_id(
            db, task_id=task_id, attempt_version_id=value)
        for value in _accountable_returned_task_model_call_attempt_version_ids(
            db, task_id=task_id))
    current_rows = db.execute(
        "SELECT json_extract(e.payload_json,"
        "'$.llm_invocation_attempt_ref.version_id') AS attempt_version_id "
        "FROM events e JOIN transactions t ON "
        "t.transaction_id=e.transaction_id JOIN objects attempt ON "
        "attempt.version_id=json_extract(e.payload_json,"
        "'$.llm_invocation_attempt_ref.version_id') "
        "AND attempt.logical_id=json_extract(e.payload_json,"
        "'$.llm_invocation_attempt_ref.logical_id') "
        "WHERE t.status='committed' AND e.task_id=? "
        "AND attempt.object_type='llm_invocation_attempt/v1' AND (("
        "e.event_type='llm_invocation_succeeded/v1' AND "
        "json_type(e.payload_json,"
        "'$.agent_context_compaction_ref')='object') OR (("
        "e.event_type='llm_invocation_succeeded/v1' AND "
        "json_type(e.payload_json,'$.agent_turn_ref')='object') OR ("
        "e.event_type='llm_invocation_interrupted/v1' AND "
        "json_type(e.payload_json,'$.agent_loop_id')='text')) AND "
        "json_type(attempt.metadata_json,'$.finalization_scope')='null') "
        "AND "
        "json_extract(e.payload_json,"
        "'$.llm_invocation_attempt_ref.entity_type')="
        "'llm_invocation_attempt/v1'",
        (task_id,),
    ).fetchall()
    result.update(
        _physical_model_call_attempt_version_id(
            db, task_id=task_id,
            attempt_version_id=str(row["attempt_version_id"]))
        for row in current_rows)

    turn_rows = db.execute(
        "SELECT json_extract(e.payload_json,"
        "'$.provider_attempt_version_id') AS attempt_version_id "
        "FROM events e JOIN transactions t ON "
        "t.transaction_id=e.transaction_id JOIN objects attempt ON "
        "attempt.version_id=json_extract(e.payload_json,"
        "'$.provider_attempt_version_id') "
        "WHERE t.status='committed' "
        "AND e.task_id=? AND e.event_type='agent_turn_recorded/v1' "
        "AND attempt.object_type='provider_attempt_spec/v1' "
        "AND json_type(attempt.metadata_json,'$.finalization_scope')='null' "
        "AND json_type(e.payload_json,"
        "'$.provider_attempt_version_id')='text'",
        (task_id,),
    ).fetchall()
    result.update(str(row["attempt_version_id"]) for row in turn_rows)

    compaction_rows = db.execute(
        "SELECT json_extract(o.metadata_json,"
        "'$.provider_attempt_ref.version_id') AS attempt_version_id "
        "FROM objects o JOIN transactions t ON "
        "t.transaction_id=o.transaction_id JOIN objects attempt ON "
        "attempt.version_id=json_extract(o.metadata_json,"
        "'$.provider_attempt_ref.version_id') AND "
        "attempt.logical_id=json_extract(o.metadata_json,"
        "'$.provider_attempt_ref.logical_id') "
        "WHERE t.status='committed' "
        "AND t.task_id=? AND o.object_type='agent_context_compaction/v2' "
        "AND attempt.object_type='provider_attempt_spec/v1' "
        "AND json_extract(o.metadata_json,'$.state')='completed' AND "
        "json_extract(o.metadata_json,"
        "'$.provider_attempt_ref.entity_type')="
        "'provider_attempt_spec/v1'",
        (task_id,),
    ).fetchall()
    result.update(str(row["attempt_version_id"])
                  for row in compaction_rows)

    disposition_rows = db.execute(
        "SELECT json_extract(e.payload_json,"
        "'$.provider_attempt_version_id') AS attempt_version_id "
        "FROM events e JOIN transactions event_tx ON "
        "event_tx.transaction_id=e.transaction_id JOIN objects attempt ON "
        "attempt.version_id=json_extract(e.payload_json,"
        "'$.provider_attempt_version_id') JOIN transactions attempt_tx ON "
        "attempt_tx.transaction_id=attempt.transaction_id "
        "WHERE event_tx.status='committed' "
        "AND attempt_tx.status='committed' AND e.task_id=? "
        "AND e.event_type='llm_call_result_adopted/v1' "
        "AND attempt.object_type='provider_attempt_spec/v1' "
        "AND json_type(attempt.metadata_json,'$.finalization_scope')='null' "
        "AND json_type(attempt.metadata_json,"
        "'$.delegation_execution_id')='null'",
        (task_id,),
    ).fetchall()
    result.update(str(row["attempt_version_id"])
                  for row in disposition_rows)
    return result

def _accountable_agent_loop_model_call_monitoring_counts(
        db: sqlite3.Connection, *, agent_loop_id: str,
) -> tuple[int, int]:
    """Return direct and delegated returned-call counts for one parent loop.

    This node-analysis projection is not a cap authority and intentionally
    has no relationship to task_total_hard_cap.
    """
    from ..event_store import RegistryCorruptError

    if not isinstance(agent_loop_id, str) or not agent_loop_id:
        raise ValueError("agent-loop monitoring identity is invalid")
    direct = 0
    delegated = 0
    for attempt_id in _accountable_returned_model_call_attempt_ids(db):
        attempt_row = db.execute(
            "SELECT metadata_json FROM objects WHERE logical_id=? "
            "AND object_type='llm_invocation_attempt/v1'",
            (attempt_id,),
        ).fetchone()
        if attempt_row is None:
            raise RegistryCorruptError(
                "accountable monitored call has no attempt authority")
        attempt = json.loads(str(attempt_row["metadata_json"]))
        invocation_ref = attempt.get("llm_invocation_ref")
        if not isinstance(invocation_ref, Mapping):
            raise RegistryCorruptError(
                "accountable monitored call lacks its invocation authority")
        invocation_row = db.execute(
            "SELECT metadata_json FROM objects WHERE version_id=? "
            "AND logical_id=? AND object_type='llm_invocation_spec/v1'",
            (str(invocation_ref.get("version_id", "")),
             str(invocation_ref.get("logical_id", ""))),
        ).fetchone()
        if invocation_row is None:
            raise RegistryCorruptError(
                "accountable monitored call has no exact invocation authority")
        invocation = json.loads(str(invocation_row["metadata_json"]))
        loop_ref = invocation.get("agent_loop_ref")
        if (not isinstance(loop_ref, Mapping)
                or loop_ref.get("logical_id") != agent_loop_id):
            continue
        if invocation.get("invocation_kind") == "delegated_subtask":
            delegated += 1
        elif invocation.get("invocation_kind") in {
                "normal_turn", "context_compaction"}:
            direct += 1
    return direct, delegated

def _published_model_call_baseline_count(
        db: sqlite3.Connection, *, task_id: str,
) -> int:
    """Return the sole imported permanent-call baseline for this task."""
    from ..event_store import RegistryCorruptError
    from .net_lineage import _version_ref_from_payload

    rows = db.execute(
        "SELECT * FROM published_model_call_baselines WHERE task_id=?",
        (task_id,),
    ).fetchall()
    if not rows:
        return 0
    if len(rows) != 1:
        raise RegistryCorruptError(
            "task has multiple imported published-model-call baselines")
    row = rows[0]
    try:
        source_run_ref = json.loads(str(row["source_run_ref_json"]))
        source_task_ref = json.loads(str(row["source_task_ref_json"]))
        source_checkpoint_ref = json.loads(
            str(row["source_checkpoint_ref_json"]))
        destination_run_ref = json.loads(
            str(row["destination_run_ref_json"]))
        refs = tuple(
            _version_ref_from_payload(value) for value in (
                source_run_ref, source_task_ref, source_checkpoint_ref,
                destination_run_ref))
        count = int(row["published_call_count"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            "imported published-model-call baseline is malformed") from exc
    if (refs[0].entity_type != "native_run_identity/v1"
            or refs[1].entity_type != "task/v1"
            or str(refs[1].entity_id) != task_id
            or refs[2].entity_type != "marking_checkpoint/v1"
            or refs[3].entity_type != "native_run_identity/v1"
            or not isinstance(row["exact_model"], str)
            or not str(row["exact_model"])
            or count < 0):
        raise RegistryCorruptError(
            "imported published-model-call baseline provenance is invalid")
    return count

def _current_task_recovery_manifest_row(
        db: sqlite3.Connection,
) -> sqlite3.Row | None:
    """Read the committed manifest selected by the exact current pointer."""

    return db.execute(
        "SELECT o.* FROM registry_meta pointer "
        "JOIN registry_meta task ON task.key='task_id' "
        "JOIN objects o ON o.version_id=pointer.value "
        "AND o.logical_id=task.value "
        "AND o.object_type='task_recovery_manifest/v1' "
        "JOIN transactions t ON t.transaction_id=o.transaction_id "
        "AND t.status='committed' "
        "WHERE pointer.key='task_recovery_manifest_version_id'",
    ).fetchone()


def _registered_model_call_limits(
        db: sqlite3.Connection, *, task_id: str,
) -> tuple[int | None, int | None]:
    """Return ordinary/hard limits including owner-authorized generations.

    The startup manifest remains the immutable per-generation budget
    contract.  Every atomically closed ``run_reopened/v1`` authorization
    grants one additional copy of that original budget.  This keeps physical
    attempt identities and cumulative accounting monotonic while preventing a
    later owner-selected checkpoint generation from inheriting an already
    consumed run-wide cap.
    """
    current_row = _current_task_recovery_manifest_row(db)
    if current_row is None:
        raise ValueError("task has no current registered call-limit authority")
    initial_rows = db.execute(
        "SELECT o.metadata_json FROM objects o JOIN transactions t ON "
        "t.transaction_id=o.transaction_id AND t.status='committed' "
        "WHERE o.object_type='task_recovery_manifest/v1' "
        "AND o.logical_id=? ORDER BY o.rowid LIMIT 1",
        (task_id,),
    ).fetchall()
    if len(initial_rows) != 1:
        raise ValueError("task has no immutable initial call-limit authority")
    try:
        current = json.loads(str(current_row["metadata_json"]))
        initial = json.loads(str(initial_rows[0]["metadata_json"]))
        current_ordinary = current["ordinary_global_cap"]
        current_hard = current["task_total_hard_cap"]
        generation_ordinary = initial["ordinary_global_cap"]
        generation_hard = initial["task_total_hard_cap"]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("registered model-call limits are malformed") from exc
    values = (
        current_ordinary, current_hard,
        generation_ordinary, generation_hard,
    )
    if any(value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1)
           for value in values):
        raise ValueError("registered model-call limits are invalid")
    if any(value is None for value in values):
        if not all(value is None for value in values):
            raise ValueError(
                "registered model-call limits mix bounded and unbounded authority")
        return None, None

    rows = db.execute(
        "SELECT o.metadata_json FROM objects o JOIN transactions t ON "
        "t.transaction_id=o.transaction_id AND t.status='committed' "
        "WHERE o.object_type='run_reopen_authorization/v1' "
        "AND json_extract(o.metadata_json,'$.task_ref.logical_id')=? "
        "AND EXISTS (SELECT 1 FROM events e WHERE "
        "e.transaction_id=o.transaction_id "
        "AND e.event_type='run_reopened/v1' "
        "AND json_extract(e.payload_json,"
        "'$.run_reopen_authorization_ref.version_id')=o.version_id)",
        (task_id,),
    ).fetchall()
    try:
        generations = sorted(
            int(json.loads(str(row["metadata_json"]))["execution_generation"])
            for row in rows)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(
            "checkpoint generation budget authority is malformed") from exc
    if generations and generations != list(range(1, generations[-1] + 1)):
        raise ValueError(
            "checkpoint generation budget authority is not contiguous")
    generation_count = len(generations)
    return (
        current_ordinary + generation_count * generation_ordinary,
        current_hard + generation_count * generation_hard,
    )

_AGENT_CAP_SLOT_RETURN_KIND = "registered_logical_slot_return/v1"

_AGENT_CAP_TRACE_SUMMARY_KIND = "registered_agent_trace_summary/v1"

_AGENT_CAP_REVIEW_BUNDLE_KIND = "registered_agent_cap_review_bundle/v2"

_TASK_MODEL_CALL_TERMINAL_REASON = "task_model_call_cap_handoff"

def _task_model_call_cap_handoff_member_authority_sql(
        *, member_alias: str, publication_alias: str,
        through_ordinal_sql: str | None = None,
) -> str:
    """Return exact atomic authority for one mechanically closed Cap250 firing."""

    member_commit_ordinal_clause = (
        "" if through_ordinal_sql is None else
        f"AND cap_member_commit.ordinal<={through_ordinal_sql} ")
    handoff_commit_ordinal_clause = (
        "" if through_ordinal_sql is None else
        f"AND cap_handoff_commit.ordinal<={through_ordinal_sql} ")
    return (
        "EXISTS (SELECT 1 FROM transactions cap_member_transaction "
        "JOIN transactions cap_transaction ON cap_transaction.status='committed' "
        "JOIN events cap_terminal ON cap_terminal.transaction_id="
        "cap_transaction.transaction_id "
        "JOIN events cap_checkpoint_event ON "
        "cap_checkpoint_event.transaction_id="
        "cap_transaction.transaction_id "
        "JOIN objects cap_checkpoint ON cap_checkpoint.transaction_id="
        "cap_transaction.transaction_id "
        "WHERE cap_member_transaction.transaction_id="
        f"{member_alias}.transaction_id "
        "AND cap_member_transaction.status='committed' "
        "AND cap_transaction.status='committed' "
        "AND cap_terminal.event_type='agent_loop_terminal/v1' "
        "AND cap_terminal.aggregate_type='transition_firing' "
        f"AND cap_terminal.aggregate_id={publication_alias}.firing_logical_id "
        "AND json_extract(cap_terminal.payload_json,'$.terminal_reason')="
        f"'{_TASK_MODEL_CALL_TERMINAL_REASON}' "
        "AND cap_checkpoint_event.event_type="
        "'marking_checkpoint_committed/v1' "
        "AND cap_checkpoint_event.task_id=cap_terminal.task_id "
        "AND json_extract(cap_checkpoint_event.payload_json,'$.settled')=1 "
        "AND cap_checkpoint.object_type='marking_checkpoint/v1' "
        "AND cap_checkpoint.version_id=json_extract("
        "cap_checkpoint_event.payload_json,'$.checkpoint_ref.version_id') "
        "AND json_extract(cap_checkpoint.metadata_json,'$.settled')=1 "
        "AND json_extract(cap_checkpoint.metadata_json,"
        "'$.marking_checkpoint_ref.version_id')=cap_checkpoint.version_id "
        "AND EXISTS (SELECT 1 FROM json_each("
        "cap_checkpoint_event.payload_json,'$.transition_firing_refs') "
        "cap_event_firing WHERE json_extract("
        "cap_event_firing.value,'$.entity_type')='transition_firing/v1' "
        "AND json_extract(cap_event_firing.value,'$.logical_id')="
        f"{publication_alias}.firing_logical_id "
        "AND json_extract(cap_event_firing.value,'$.version_id')="
        f"{publication_alias}.firing_version_id) "
        "AND EXISTS (SELECT 1 FROM json_each("
        "cap_checkpoint.metadata_json,'$.transition_firing_refs') "
        "cap_object_firing WHERE json_extract("
        "cap_object_firing.value,'$.entity_type')='transition_firing/v1' "
        "AND json_extract(cap_object_firing.value,'$.logical_id')="
        f"{publication_alias}.firing_logical_id "
        "AND json_extract(cap_object_firing.value,'$.version_id')="
        f"{publication_alias}.firing_version_id) "
        "AND EXISTS (SELECT 1 FROM firing_temporary_members cap_terminal_member "
        "WHERE cap_terminal_member.firing_version_id="
        f"{publication_alias}.firing_version_id "
        "AND cap_terminal_member.member_kind='event' "
        "AND cap_terminal_member.member_identity=cap_terminal.event_id "
        "AND cap_terminal_member.transaction_id="
        "cap_transaction.transaction_id) "
        "AND EXISTS (SELECT 1 FROM firing_temporary_members cap_event_member "
        "WHERE cap_event_member.firing_version_id="
        f"{publication_alias}.firing_version_id "
        "AND cap_event_member.member_kind='event' "
        "AND cap_event_member.member_identity=cap_checkpoint_event.event_id "
        "AND cap_event_member.transaction_id=cap_transaction.transaction_id) "
        "AND EXISTS (SELECT 1 FROM firing_temporary_members cap_object_member "
        "WHERE cap_object_member.firing_version_id="
        f"{publication_alias}.firing_version_id "
        "AND cap_object_member.member_kind='object' "
        "AND cap_object_member.member_identity=cap_checkpoint.version_id "
        "AND cap_object_member.transaction_id=cap_transaction.transaction_id) "
        "AND EXISTS (SELECT 1 FROM events cap_member_commit WHERE "
        "cap_member_commit.transaction_id=cap_member_transaction.transaction_id "
        "AND cap_member_commit.event_type='transaction_committed/v1' "
        f"{member_commit_ordinal_clause}) "
        "AND EXISTS (SELECT 1 FROM events cap_handoff_commit WHERE "
        "cap_handoff_commit.transaction_id=cap_transaction.transaction_id "
        "AND cap_handoff_commit.event_type='transaction_committed/v1' "
        f"{handoff_commit_ordinal_clause}))"
    )

def _task_model_call_cap_handoff_member_publication_ordinal(
        db: sqlite3.Connection, *, member_kind: str, member_identity: str,
        through_ordinal: int,
) -> int | None:
    """Return when one exact mechanically closed member became canonical."""
    from ..event_store import RegistryCorruptError

    rows = db.execute(
        "SELECT cap_handoff_commit.ordinal FROM firing_temporary_members m "
        "JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
        "JOIN events cap_terminal ON cap_terminal.aggregate_id="
        "p.firing_logical_id AND cap_terminal.aggregate_type='transition_firing' "
        "AND cap_terminal.event_type='agent_loop_terminal/v1' "
        "AND json_extract(cap_terminal.payload_json,'$.terminal_reason')=? "
        "JOIN events cap_handoff_commit ON cap_handoff_commit.transaction_id="
        "cap_terminal.transaction_id AND cap_handoff_commit.event_type="
        "'transaction_committed/v1' WHERE m.member_kind=? "
        "AND m.member_identity=? AND p.state='PROVISIONAL' "
        "AND cap_handoff_commit.ordinal<=? AND "
        + _task_model_call_cap_handoff_member_authority_sql(
            member_alias="m", publication_alias="p",
            through_ordinal_sql="?")
        + " ORDER BY cap_handoff_commit.ordinal",
        (_TASK_MODEL_CALL_TERMINAL_REASON, member_kind, member_identity,
         through_ordinal, through_ordinal, through_ordinal),
    ).fetchall()
    ordinals = {int(row[0]) for row in rows}
    if len(ordinals) > 1:
        raise RegistryCorruptError(
            "mechanically closed member has multiple publication ordinals")
    return next(iter(ordinals)) if ordinals else None

_CANONICAL_EVENT_SQL = (
    "NOT EXISTS (SELECT 1 FROM firing_temporary_members member "
    "JOIN firing_publications publication ON "
    "publication.firing_version_id=member.firing_version_id "
    "WHERE member.member_kind='event' "
    "AND member.member_identity=e.event_id AND ("
    "publication.state!='PUBLISHED' OR "
    "publication.published_transaction_id IS NULL OR "
    "NOT EXISTS (SELECT 1 FROM events authority_event WHERE "
    "authority_event.transaction_id=publication.published_transaction_id "
    "AND authority_event.event_type='transaction_committed/v1')) AND NOT "
    + _task_model_call_cap_handoff_member_authority_sql(
        member_alias="member", publication_alias="publication") + ")"
)

def _task_model_call_terminal_phase_entered(
        db: sqlite3.Connection, *, task_id: str,
) -> bool:
    """Return only the committed Registry Cap250 handoff marker."""

    row = db.execute(
        "SELECT 1 FROM events e JOIN transactions event_transaction ON "
        "event_transaction.transaction_id=e.transaction_id "
        "WHERE event_transaction.status='committed' AND e.task_id=? "
        "AND e.event_type='agent_loop_terminal/v1' "
        "AND e.aggregate_type='transition_firing' "
        "AND json_extract(e.payload_json,'$.terminal_reason')=? AND "
        f"{_CANONICAL_EVENT_SQL} LIMIT 1",
        (task_id, _TASK_MODEL_CALL_TERMINAL_REASON),
    ).fetchone()
    return row is not None

def install_published_model_call_baseline_v1(
        store, *, source_run_ref: Mapping[str, Any],
        source_task_ref: Mapping[str, Any],
        source_checkpoint_ref: Mapping[str, Any], exact_model: str,
        published_call_count: int, idempotency_key: str,
) -> VersionRef:
    """Atomically install one imported permanent-call accounting fact."""
    from ..event_store import (RegistryConflict, TaskModelCallLimitExceeded, _exact_ref_payload, _now, _version_ref_from_payload)

    raw_refs = (
        source_run_ref, source_task_ref, source_checkpoint_ref)
    if (any(not isinstance(value, Mapping)
            or set(value) != {
                "entity_type", "logical_id", "version_id"}
            for value in raw_refs)
            or not isinstance(exact_model, str) or not exact_model
            or isinstance(published_call_count, bool)
            or not isinstance(published_call_count, int)
            or published_call_count < 0
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "published call baseline requires exact provenance/count/key")
    try:
        source_refs = tuple(
            _version_ref_from_payload(value) for value in raw_refs)
    except (TypeError, ValueError, KeyError) as exc:
        raise RegistryConflict(
            "published call baseline provenance is malformed") from exc
    if (source_refs[0].entity_type != "native_run_identity/v1"
            or source_refs[1].entity_type != "task/v1"
            or source_refs[2].entity_type != "marking_checkpoint/v1"):
        raise RegistryConflict(
            "published call baseline provenance names invalid authorities")

    with store._lock, store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='task_id'",
        ).fetchone()
        destination_run_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='native_run_ref'",
        ).fetchone()
        if task_row is None or destination_run_row is None:
            db.rollback()
            raise RegistryConflict(
                "published call baseline lacks destination task/run authority")
        task_id = str(task_row["value"])
        try:
            destination_run = json.loads(
                str(destination_run_row["value"]))
            destination_run_ref = _version_ref_from_payload(
                destination_run)
        except (TypeError, ValueError, KeyError,
                json.JSONDecodeError) as exc:
            db.rollback()
            raise RegistryConflict(
                "destination run authority is malformed") from exc
        if (str(source_refs[1].entity_id) != task_id
                or destination_run_ref.entity_type
                != "native_run_identity/v1"
                or destination_run_ref == source_refs[0]):
            db.rollback()
            raise RegistryConflict(
                "published call baseline differs from continuation identity")
        try:
            _ordinary_limit, limit = _registered_model_call_limits(
                db, task_id=task_id)
        except ValueError as exc:
            db.rollback()
            raise RegistryConflict(
                "destination model-call limit is malformed") from exc
        if limit is not None and published_call_count > limit:
            db.rollback()
            raise TaskModelCallLimitExceeded(
                "imported published calls exceed the destination cap")
        writer_epoch_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'",
        ).fetchone()
        if writer_epoch_row is None:
            db.rollback()
            raise RegistryConflict(
                "published call baseline lacks a writer epoch")
        expected = {
            "task_id": task_id,
            "destination_run_ref_json": canonical_text(
                _exact_ref_payload(destination_run_ref)),
            "source_run_ref_json": canonical_text(
                _exact_ref_payload(source_refs[0])),
            "source_task_ref_json": canonical_text(
                _exact_ref_payload(source_refs[1])),
            "source_checkpoint_ref_json": canonical_text(
                _exact_ref_payload(source_refs[2])),
            "exact_model": exact_model,
            "published_call_count": published_call_count,
            "idempotency_key": idempotency_key,
            "writer_epoch": int(writer_epoch_row["value"]),
        }
        existing = db.execute(
            "SELECT * FROM published_model_call_baselines "
            "WHERE task_id=? OR idempotency_key=?",
            (task_id, idempotency_key),
        ).fetchall()
        if existing:
            if (len(existing) != 1
                    or any(existing[0][key] != value
                           for key, value in expected.items())):
                db.rollback()
                raise RegistryConflict(
                    "published call baseline idempotency conflicts")
            db.commit()
            return VersionRef(
                "published_model_call_baseline/v1",
                TypedId.parse(str(existing[0]["baseline_id"]),
                              expected="published_model_call_baseline"),
                TypedId.parse(
                    str(existing[0]["baseline_version_id"]),
                    expected="published_model_call_baseline_version"))
        baseline_id = new_id("published_model_call_baseline")
        baseline_version_id = new_id(
            "published_model_call_baseline_version")
        db.execute(
            "INSERT INTO published_model_call_baselines("
            "baseline_id,baseline_version_id,task_id,"
            "destination_run_ref_json,source_run_ref_json,"
            "source_task_ref_json,source_checkpoint_ref_json,"
            "exact_model,published_call_count,idempotency_key,"
            "writer_epoch,installed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(baseline_id), str(baseline_version_id),
             expected["task_id"], expected["destination_run_ref_json"],
             expected["source_run_ref_json"],
             expected["source_task_ref_json"],
             expected["source_checkpoint_ref_json"],
             expected["exact_model"], expected["published_call_count"],
             expected["idempotency_key"], expected["writer_epoch"],
             _now()),
        )
        db.commit()
        return VersionRef(
            "published_model_call_baseline/v1",
            baseline_id, baseline_version_id)

def actual_model_call_counts(store) -> tuple[int, int]:
    """Return raw settled task calls and their post-limit excess."""
    from ..event_store import RegistryCorruptError

    with store.connect() as db:
        task_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='task_id'",
        ).fetchone()
        if task_row is None:
            raise RegistryCorruptError("task identity is missing")
        task_id = str(task_row["value"])
        total = (
            _published_model_call_baseline_count(db, task_id=task_id)
            + len(_cumulative_task_model_call_attempt_version_ids(
                db, task_id=task_id)))
    limit = store.actual_model_call_limit()
    return _project_actual_model_call_counts(total, limit)

def actual_model_call_count(store) -> int:
    """Count all settled task calls without clamping to configuration."""

    return store.actual_model_call_counts()[0]

def extra_model_call_count(store) -> int:
    """Count post-cap calls completed by already-admitted firings."""

    return store.actual_model_call_counts()[1]

def agent_loop_model_call_monitoring_counts(
        store, agent_loop_id: str,
) -> tuple[int, int, int]:
    """Return direct, delegated, and aggregate calls for node analysis.

    The aggregate is monitoring-only and cannot be used as parent-cap
    authority.
    """

    with store.connect() as db:
        direct, delegated = (
            _accountable_agent_loop_model_call_monitoring_counts(
                db, agent_loop_id=agent_loop_id))
    return direct, delegated, direct + delegated

def agent_loop_parent_direct_model_call_count(
        store, agent_loop_id: str,
) -> int:
    """Return only this parent loop's direct canonical calls."""

    direct, _delegated, _aggregate = (
        store.agent_loop_model_call_monitoring_counts(agent_loop_id))
    return direct

def task_model_call_terminal_phase_entered(store) -> bool:
    """Return the checkpoint-stable post-cap phase authority."""
    from ..event_store import RegistryCorruptError

    task_id = store.get_meta("task_id")
    if task_id is None:
        raise RegistryCorruptError("task identity is missing")
    with store.connect() as db:
        return _task_model_call_terminal_phase_entered(
            db, task_id=task_id)

def actual_model_call_limit(store) -> int | None:
    """Return the effective hard limit for the current execution generation."""
    from ..event_store import RegistryCorruptError

    with store.connect() as db:
        task_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='task_id'",
        ).fetchone()
        if task_row is None:
            raise RegistryCorruptError("task identity is missing")
        try:
            _ordinary, hard = _registered_model_call_limits(
                db, task_id=str(task_row["value"]))
        except ValueError as exc:
            raise RegistryCorruptError(
                "registered actual-model-call limit is invalid") from exc
    return hard


def ordinary_model_call_limit(store) -> int | None:
    """Return the effective ordinary-call limit for the current generation."""
    from ..event_store import RegistryCorruptError

    with store.connect() as db:
        task_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='task_id'",
        ).fetchone()
        if task_row is None:
            raise RegistryCorruptError("task identity is missing")
        try:
            ordinary, _hard = _registered_model_call_limits(
                db, task_id=str(task_row["value"]))
        except ValueError as exc:
            raise RegistryCorruptError(
                "registered ordinary model-call limit is invalid") from exc
    return ordinary
