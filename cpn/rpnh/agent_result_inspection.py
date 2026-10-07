"""Read-only AgentAction/request evidence, separate from execution authority.

The filesystem entry follows inspection.py's trusted local inspection boundary.
It is not an observer grant or a RegistryReadSession permission bypass. The pure
projector accepts already verified records and events from one consistent head.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from collections.abc import Mapping

from .registry.schema_catalog import canonical_json

SCHEMA_VERSION = "rpnh/agent_result_evidence/v1"
_RECORD_TYPES = (
    "agent_action/v2", "agent_action/v3", "agent_loop/v1", "run_terminal_evidence/v1",
    "llm_call_spec/v2", "llm_invocation_spec/v1",
    "provider_attempt_spec/v1", "provider_payload_materialization_receipt/v1",
)


def _same_loop(left, right):
    return (isinstance(left, Mapping) and isinstance(right, Mapping)
            and left.get("entity_type") == right.get("entity_type")
            and left.get("logical_id") is not None
            and left.get("logical_id") == right.get("logical_id"))


def _contains(value, ref):
    if not ref:
        return False
    if value == ref:
        return True
    if isinstance(value, Mapping):
        return any(_contains(item, ref) for item in value.values())
    if isinstance(value, list):
        return any(_contains(item, ref) for item in value)
    return False


def _decode(content):
    try:
        return json.loads(content) if isinstance(content, (str, bytes)) else content
    except (ValueError, UnicodeError):
        return None


def _request_projection(action_ref, action, envelope):
    """No string-search of actor text, and no matching by tool name alone."""
    if (not isinstance(envelope, Mapping)
            or (envelope.get("protocol") != "llm_request_envelope/v1"
                and envelope.get("schema_version") != "logical_provider_request_recipe/v1")
            or not isinstance(envelope.get("messages"), list)):
        return "unavailable", "unsupported_request_material"
    messages = envelope["messages"]
    matched = [m for m in messages if isinstance(m, Mapping)
               and m.get("role") == "tool"
               and (m.get("tool_call_id") == action.get("tool_call_id")
                    or (isinstance(_decode(m.get("content")), Mapping)
                        and _decode(m.get("content")).get("agent_action_ref") == action_ref))]
    states = []
    for message in matched:
        content = message.get("content")
        body = _decode(content)
        exact = isinstance(body, Mapping) and body.get("agent_action_ref") == action_ref
        # Reused provider call IDs across turns are legal: do not guess which.
        if len(matched) > 1 and not exact:
            continue
        if isinstance(body, Mapping):
            if body.get("agent_action_ref") not in (None, action_ref):
                continue
            if action_ref["entity_type"] == "agent_action/v3":
                if (action.get("outcome") == "returned"
                        and body.get("kind") == "managed_native_plugin_result/v1"
                        and "output" in body
                        and body["output"] == action.get("output")
                        and body.get("terminal_receipt_ref") == action.get("terminal_receipt_ref")):
                    states.append("full")
                    continue
                if (action.get("outcome") == "returned" and exact
                        and body.get("kind") == "managed_output_page/v1"
                        and body.get("terminal_receipt_ref") == action.get("terminal_receipt_ref")
                        and isinstance(body.get("content"), str)
                        and type(body.get("offset_chars")) is int):
                    from cpn.components.agent_loop.managed_output import validate_managed_output_page
                    try:
                        validate_managed_output_page(body)
                    except ValueError:
                        states.append("reference")
                        continue
                    source = canonical_json(action.get("output")).decode("utf-8")
                    offset = body["offset_chars"]
                    text = body["content"]
                    if (offset >= 0 and body.get("total_chars") == len(source)
                            and text == source[offset:offset + len(text)]):
                        states.append("full" if offset == 0 and text == source else "bounded")
                        continue
            elif body == action.get("result_metadata") or (
                    exact and {k: v for k, v in body.items() if k != "agent_action_ref"}
                    == action.get("result_metadata")):
                states.append("full")
                continue
            if _contains(body, action_ref):
                states.append("reference")
        # Old UTF-8 head/tail renderer: verify the retained bytes against the
        # exact registered metadata rather than trusting an omission claim.
        if isinstance(content, str) and len(matched) == 1:
            metadata = ({"kind": "managed_native_plugin_result/v1",
                         "output": action.get("output"),
                         "terminal_receipt_ref": action.get("terminal_receipt_ref")}
                        if action_ref["entity_type"] == "agent_action/v3"
                        else action.get("result_metadata"))
            expected = canonical_json(metadata).decode("utf-8")
            marker = re.search(r"\n\[\.\.\. (\d+) UTF-8 bytes omitted from model-visible "
                               r"tool output; full Registry evidence is unchanged \.\.\.\]\n", content)
            if marker:
                head, tail = content[:marker.start()], content[marker.end():]
                if (expected.startswith(head) and expected.endswith(tail)
                        and len(expected.encode()) - len(head.encode()) - len(tail.encode())
                        == int(marker.group(1))):
                    states.append("bounded")
    for state in ("full", "bounded", "reference"):
        if state in states:
            return state, "exact_tool_result_projection"
    # An exact JSON locator in replacement history is only reference evidence.
    if any(isinstance(m, Mapping) and _contains(_decode(m.get("content")), action_ref)
           for m in messages):
        return "reference", "exact_locator_only"
    if len(matched) > 1:
        return "unavailable", "ambiguous_reused_tool_call_id"
    if matched:
        return "unavailable", "unrecognized_tool_projection"
    return "absent", "no_matching_result_or_locator"


def _submission(events):
    state = "unknown"
    for event in sorted(events, key=lambda e: e["ordinal"]):
        kind, payload = event["event_type"], event["payload"]
        if kind in {"provider_attempt_submission_observed/v1", "provider_attempt_proven_submitted/v1"}:
            state = "submitted"
        elif kind in {"provider_attempt_submission_not_permitted/v1",
                      "provider_attempt_proven_not_submitted/v1",
                      "provider_attempt_cancelled_before_submission/v1"}:
            state = "not_submitted"
        elif kind == "provider_attempt_submission_unknown/v1":
            state = "submission_unknown"
        elif kind == "provider_attempt_owner_interrupted/v1":
            observed = payload.get("submission_state")
            if observed in {"not_submitted", "submission_unknown"}:
                state = observed
    return state


def project_agent_result_evidence(*, records, events, read_material=None,
                                  actual_model_call_counts=None, task_model_call_limit=None,
                                  inspect_requests=True, max_request_bytes=4 * 1024 * 1024,
                                  scope_complete=True, outer_stop=None):
    """Project verified exact records/events without mutating them or counting calls.

    records: iterable of {ref, record}; events: {event_id,event_type,ordinal,payload}.
    read_material(ref, max_bytes) returns the complete registered bytes, or None
    for missing material. It must enforce the read bound before loading bytes.
    Integrity/access errors propagate; a missing body is not an absent result.
    scope_complete=False prevents a partial query from proving no_next_request.
    """
    if type(max_request_bytes) is not int or max_request_bytes < 1:
        raise ValueError("request material limit must be positive")
    records, events = list(records), list(events)
    def rows(kind):
        return [r for r in records if r["ref"]["entity_type"] == kind]
    calls, invocations = rows("llm_call_spec/v2"), rows("llm_invocation_spec/v1")
    # Some retained histories stop between envelope/invocation registration and
    # provider-call linkage. Keep these visible as constructed-only requests.
    requests_without_calls = [i for i in invocations if not any(
        c["record"].get("request_resource_ref") == i["record"].get("request_resource_ref")
        and c["record"].get("agent_loop_ref") == i["record"].get("agent_loop_ref")
        for c in calls)]
    calls = calls + requests_without_calls
    actions = rows("agent_action/v2") + rows("agent_action/v3")
    attempts = rows("provider_attempt_spec/v1")
    receipts = rows("provider_payload_materialization_receipt/v1")
    material_cache = {}
    result_actions = []
    for entry in actions:
        ref, action = entry["ref"], entry["record"]
        requests = []
        for call in calls:
            data = call["record"]
            if (not _same_loop(action.get("agent_loop_ref"), data.get("agent_loop_ref"))
                    or data.get("turn_sequence", -1) <= action.get("turn_sequence", -1)):
                continue
            request_ref = data.get("request_resource_ref")
            linked_invocations = [i for i in invocations
                                  if i["record"].get("request_resource_ref") == request_ref
                                  and i["record"].get("agent_loop_ref") == data.get("agent_loop_ref")]
            prior_turns = [turn for i in linked_invocations
                           for turn in i["record"].get("prior_turn_refs", [])]
            linked_attempts = [a for a in attempts if a["record"].get("llm_call_ref") == call["ref"]]
            attempt_views = []
            for attempt in linked_attempts:
                ledger = [e for e in events if e["event_type"].startswith("provider_attempt_")
                          and e["payload"].get("provider_attempt_id") == attempt["ref"]["logical_id"]
                          and e["payload"].get("provider_attempt_ref", attempt["ref"]) == attempt["ref"]]
                lineage = [dict(ref=r["ref"], **r["record"]) for r in receipts
                           if r["record"].get("provider_attempt_ref") == attempt["ref"]
                           and r["record"].get("llm_call_ref") == call["ref"]]
                attempt_views.append({"provider_attempt_ref": attempt["ref"],
                                      "submission_state": _submission(ledger),
                                      "ledger_events": ledger,
                                      "materialization_lineage": lineage,
                                      "wire_payload": "not_checked"})
            if not inspect_requests:
                projection, reason = "not_checked", "inspection_not_requested"
            elif read_material is None:
                projection, reason = "not_checked", "material_reader_not_provided"
            elif request_ref is None:
                projection, reason = "unavailable", "missing_request_reference"
            else:
                key = canonical_json(request_ref)
                if key not in material_cache:
                    try:
                        raw = read_material(request_ref, max_request_bytes)
                    except FileNotFoundError:
                        raw = None
                    material_cache[key] = (None if raw is None or len(raw) > max_request_bytes
                                           else _decode(raw))
                envelope = material_cache[key]
                projection, reason = ("unavailable", "request_material_unavailable") if envelope is None else _request_projection(ref, action, envelope)
                if projection in {"full", "bounded"} and action.get("agent_turn_ref") not in prior_turns:
                    projection, reason = "unavailable", "prior_turn_link_unavailable"
            submitted = any(a["submission_state"] == "submitted" for a in attempt_views)
            requests.append({"llm_call_ref": call["ref"] if call["ref"]["entity_type"] == "llm_call_spec/v2" else None, "request_resource_ref": request_ref,
                             "llm_invocation_refs": [i["ref"] for i in linked_invocations],
                             "prior_turn_refs": prior_turns,
                             "prior_llm_call_refs": data.get("prior_turn_refs", []) if call["ref"]["entity_type"] == "llm_call_spec/v2" else [],
                             "request_projection": projection, "reason": reason,
                             "provider_attempts": attempt_views,
                             "submitted_request_inclusion": projection if submitted else "not_established"})
        result_actions.append({"agent_action_ref": ref,
                               "agent_loop_ref": action.get("agent_loop_ref"),
                               "agent_turn_ref": action.get("agent_turn_ref"),
                               "tool_call_id": action.get("tool_call_id"),
                               "action_state": action.get("state"),
                               "settlement_events": [e for e in events
                                   if e["event_type"] == "agent_action_settled/v1"
                                   and e["payload"].get("agent_action_id") == ref["logical_id"]
                                   and e["payload"].get("expected_revision") == action.get("expected_revision")],
                               "registered_return": {"outcome": action.get("outcome", action.get("state")),
                                                     "terminal_receipt_ref": action.get("terminal_receipt_ref"),
                                                     "result_refs": action.get("result_refs", [])},
                               "request_status": "checked" if requests else ("no_next_request" if scope_complete else "unavailable"),
                               "requests": requests,
                               "observed_followup_action_refs": [a["ref"] for a in actions
                                   if _same_loop(action.get("agent_loop_ref"), a["record"].get("agent_loop_ref"))
                                   and a["record"].get("turn_sequence", -1) > action.get("turn_sequence", -1)],
                               "business_readback": "not_checked",
                               "semantic_use": "unknown", "decision_influence": "unknown"})
    stops = []
    for event in events:
        if event["event_type"] != "agent_loop_terminal/v1":
            continue
        payload = event["payload"]
        loops = [r for r in rows("agent_loop/v1")
                 if r["ref"]["logical_id"] == payload.get("agent_loop_id")
                 and r["record"].get("revision") == payload.get("revision")]
        loop = loops[0] if len(loops) == 1 else None
        stops.append({"terminal_event": event, "terminal_reason": payload.get("terminal_reason"),
                      "agent_loop_ref": loop["ref"] if loop else None,
                      "stop_stage": payload.get("stop_stage", "not_recorded"),
                      "terminal_loop_state": loop["record"].get("state") if loop else payload.get("state"),
                      "llm_turn_budget": loop["record"].get("llm_turn_budget") if loop else None,
                      "llm_turns_used": payload.get("llm_turns_used"),
                      "task_model_call_limit": task_model_call_limit,
                      "actual_model_call_counts": actual_model_call_counts})
    return deepcopy({"schema_version": SCHEMA_VERSION, "read_only": True,
                     "coverage": "complete_at_head" if scope_complete else "partial",
                     "actions": result_actions, "stops": stops, "outer_stop": outer_stop,
                     "agent_loop_terminal_observed": bool(stops),
                     "runtime_terminal": "not_checked",
                     "run_terminal_evidence": rows("run_terminal_evidence/v1"),
                     "business_success": "not_checked",
                     "benchmark_score": "not_checked",
                     "actual_model_call_counts": actual_model_call_counts,
                     "task_model_call_limit": task_model_call_limit})


def project_registry_agent_results(run_dir: Path, *, catalog, inspect_requests=True,
                                   max_request_bytes=4 * 1024 * 1024, outer_stop=None):
    """Trusted local read-only inspection at one stable Registry head.

    Shares the existing inspection boundary, not the execution/owner path.
    Missing material stays unavailable; integrity failures and a moving head fail.
    No wire capture, transport, replay, scoring or actor-authored consumption claim.
    """
    from .registry._registry import _RegistryCore
    from .registry.errors import UnknownResourceVersion
    from .registry.object_store import ObjectIntegrityError
    from .registry.run_authority import current_run_execution_authority
    from .registry.publication import _version_from_payload, _resource_from_payload
    from .registry.resource_service import _ResourceServiceKernel
    from .registry.schema_catalog import SchemaCatalog
    if not isinstance(run_dir, Path) or not isinstance(catalog, SchemaCatalog):
        raise TypeError("agent result inspection requires pathlib.Path and SchemaCatalog")
    core = _RegistryCore(run_dir.resolve(), create=False, read_only=True, catalog=catalog)
    kernel = _ResourceServiceKernel(core)
    head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
    records = []
    for kind in _RECORD_TYPES:
        for row in core.event_store.object_rows_by_type(kind):
            ref = {"entity_type": kind, "logical_id": str(row["logical_id"]),
                   "version_id": str(row["version_id"])}
            record = dict(kernel._exact_object(_version_from_payload(ref), expected_type=kind).metadata)
            records.append({"ref": ref, "record": record})
    events = [{"event_id": str(e.event_id), "event_type": e.event_type,
               "ordinal": e.ordinal, "payload": dict(e.payload)}
              for e in core.event_store.list_events()]
    def read_material(ref, limit):
        resource = _resource_from_payload(ref)
        try:
            prepared = kernel._prepared_reference(resource)
            if prepared.size > limit:
                return None
            return core.object_store.read_registered(prepared)
        except UnknownResourceVersion:
            return None
        except ObjectIntegrityError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return None
            raise
    result = project_agent_result_evidence(
        records=records, events=events, read_material=read_material,
        actual_model_call_counts=list(core.event_store.actual_model_call_counts()),
        task_model_call_limit=core.event_store.actual_model_call_limit(),
        inspect_requests=inspect_requests, max_request_bytes=max_request_bytes, outer_stop=outer_stop)
    authority_ref, authority = current_run_execution_authority(core, kernel)
    result["runtime_terminal"] = authority["status"] == "terminal"
    result["runtime_execution_status"] = authority["status"]
    result["run_execution_authority_ref"] = {
        "entity_type": authority_ref.entity_type, "logical_id": str(authority_ref.entity_id),
        "version_id": str(authority_ref.version_id)}
    if head != core.event_store.max_ordinal() or epoch != core.event_store.writer_epoch:
        raise RuntimeError("Registry advanced during result inspection; retry the read")
    result["source"] = {"run_dir": str(run_dir.resolve()), "head_ordinal": head,
                        "writer_fencing_epoch": epoch, "task_id": str(core.task_id)}
    return result


__all__ = ["project_agent_result_evidence", "project_registry_agent_results"]
