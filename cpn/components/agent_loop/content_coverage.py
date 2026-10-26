"""Exact read_file body coverage in submitted logical requests, not wire/use.

Only a trusted HOST may supply the original Registry snapshot and reader. This
module is a pure evidence checker, not an authority constructor or actor API.
All offsets count Python Unicode codepoints, matching Workspace._decoded_read.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Callable

from cpn.rpnh.agent_result_inspection import _submission
from cpn.rpnh.registry.schema_catalog import canonical_json


_TYPES = (
    "invocation/v1", "agent_loop/v1", "agent_action/v2",
    "llm_call_spec/v2", "llm_invocation_spec/v1",
    "provider_attempt_spec/v1", "resource_delivery/v1",
    "delivery_boundary_receipt/v1", "resource_version/v1",
)


def _plain(value):
    """Copy Core's immutable MappingProxy/tuple snapshots to JSON data."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class ReadCoverageSnapshot:
    """Already verified original facts, never actor-authored coverage claims.

    records contain ref, record, publication ordinal and producer_invocation_id;
    events retain original event IDs/ordinals/task IDs and producer identity.
    read_material(ref, limit) must return complete registered bytes, or None,
    enforcing the bound *before* loading. Integrity/access errors propagate.
    The HOST must keep this snapshot/reader at one original Registry view.
    """
    records: tuple
    events: tuple
    read_material: Callable
    invocation_ref: Mapping
    operation_binding_ref: Mapping
    transition_firing_ref: Mapping
    task_id: str
    head_ordinal: int
    max_material_bytes: int = 4 * 1024 * 1024


def collect_read_coverage_snapshot(
        view, *, read_material, invocation_ref, operation_binding_ref,
        transition_firing_ref, cutoff, max_records=20000, max_events=100000,
        max_material_bytes=4 * 1024 * 1024):
    """Read bounded committed originals from a Core transaction view.db.

    The caller supplies the original consistent read/validation view, not an
    exported report. Proposed new_by_version/events are deliberately excluded:
    a pending seal, constructed request or uncommitted submission is no proof.
    ``cutoff`` is an inclusive original event ordinal captured before sealing.
    Resource bytes remain behind the HOST's registered-material reader.
    """
    if type(cutoff) is not int or cutoff < 0:
        raise ValueError("coverage cutoff must be a nonnegative event ordinal")
    if any(type(n) is not int or n < 1 for n in
           (max_records, max_events, max_material_bytes)):
        raise ValueError("coverage collection bounds must be positive integers")
    db = view.db
    head = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
    if cutoff > head:
        raise ValueError("coverage cutoff exceeds original Registry head")
    placeholders = ",".join("?" for _ in _TYPES)
    rows = db.execute(
        "SELECT o.*,e.ordinal FROM objects o JOIN events e "
        "ON e.event_id=o.published_event_id WHERE e.ordinal<=? "
        f"AND o.object_type IN ({placeholders}) ORDER BY e.ordinal,o.version_id LIMIT ?",
        (cutoff, *_TYPES, max_records + 1)).fetchall()
    events = db.execute(
        "SELECT event_id,event_type,ordinal,payload_json,task_id,"
        "producer_invocation_id,occurred_at,recorded_at,transaction_id FROM events "
        "WHERE ordinal<=? ORDER BY ordinal LIMIT ?",
        (cutoff, max_events + 1)).fetchall()
    if len(rows) > max_records or len(events) > max_events:
        raise ValueError("original coverage snapshot exceeds collection bound")
    task_ids = {str(e["task_id"]) for e in events}
    if len(task_ids) != 1:
        raise ValueError("coverage snapshot requires one original task Registry")
    return ReadCoverageSnapshot(
        records=tuple({
            "ref": {"entity_type": r["object_type"], "logical_id": r["logical_id"],
                    "version_id": r["version_id"]},
            "record": json.loads(r["metadata_json"]), "ordinal": r["ordinal"],
            "producer_invocation_id": r["producer_invocation_id"],
            "transaction_id": r["transaction_id"],
        } for r in rows),
        events=tuple({**{k: e[k] for k in e.keys() if k != "payload_json"},
                      "payload": json.loads(e["payload_json"])} for e in events),
        read_material=read_material, invocation_ref=_plain(invocation_ref),
        operation_binding_ref=_plain(operation_binding_ref),
        transition_firing_ref=_plain(transition_firing_ref),
        task_id=task_ids.pop(), head_ordinal=cutoff,
        max_material_bytes=max_material_bytes)


def _key(ref):
    return canonical_json(_plain(ref))


def _resource_version(ref):
    return {"entity_type": "resource_version/v1", "logical_id": ref["resource_id"],
            "version_id": ref["resource_version_id"]}


def _json(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError, UnicodeError):
        return None


def _union(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _tool_messages(messages):
    """Pair final tool bodies with their actual preceding assistant calls."""
    pending = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            pending = list(message.get("tool_calls") or [])
        elif message.get("role") == "tool":
            calls = [call for call in pending if isinstance(call, Mapping)
                     and call.get("id") == message.get("tool_call_id")]
            if len(calls) == 1:
                yield index, message, calls[0]
                pending.remove(calls[0])
        else:
            pending = []


def verify_required_read_coverage(required_sources, verified_snapshot, *, cutoff):
    """Return errors + exact evidence for an inclusive pre-seal event cut.

    required_sources: [{doc_id,resource_ref,decoded_sha256,required_ranges:
      [{start,end_exclusive,unit:'unicode_codepoint'}]}]. Teacher bodies use the
    identical generic resource/range contract. Only genuine complete bounded
    read slices count; reducer fragments, references and summaries do not.
    No supplied coverage_ok/PASS field is consulted. Pure tests of this helper
    are not evidence that the native Success closure invokes it.
    """
    s = verified_snapshot
    required_sources = _plain(required_sources)
    if not isinstance(s, ReadCoverageSnapshot):
        raise TypeError("coverage requires a trusted ReadCoverageSnapshot")
    if type(cutoff) is not int or not 0 <= cutoff <= s.head_ordinal:
        raise ValueError("coverage cutoff is outside the original snapshot")
    if type(s.max_material_bytes) is not int or s.max_material_bytes < 1:
        raise ValueError("coverage material bound must be positive")
    records = [r for r in s.records if r["ordinal"] <= cutoff]
    by_ref = {_key(r["ref"]): r for r in records}
    if len(by_ref) != len(records):
        raise ValueError("duplicate exact objects in coverage snapshot")
    events = [e for e in s.events if e["ordinal"] <= cutoff]
    if any(e["task_id"] != s.task_id for e in events):
        raise ValueError("coverage events cross original task Registries")
    owner = s.invocation_ref["logical_id"]

    def row(ref):
        return by_ref.get(_key(ref))

    def doc(ref):
        found = row(ref)
        return found["record"] if found else {}

    def owned(entry):
        return entry["producer_invocation_id"] == owner

    def scoped(data):
        return (data.get("invocation_ref") == s.invocation_ref
                and data.get("operation_binding_ref") == s.operation_binding_ref)

    invocation = doc(s.invocation_ref)
    if (not scoped(invocation)
            or invocation.get("own_transition_firing_ref") != s.transition_firing_ref
            or invocation.get("task_ref", {}).get("logical_id") != s.task_id):
        raise ValueError("coverage scope differs from original invocation/firing/task")

    material_cache = {}

    def material(ref):
        if not isinstance(ref, Mapping):
            return None
        try:
            resource = row(_resource_version(ref))
        except KeyError:
            return None
        if resource is None:
            return None
        key = _key(ref)
        if key not in material_cache:
            raw = s.read_material(ref, s.max_material_bytes)
            material_cache[key] = raw if isinstance(raw, bytes) and len(raw) <= s.max_material_bytes else None
        return material_cache[key]

    def acknowledged(ref, resource_ref, boundary):
        delivery_row = row(ref)
        d = doc(ref)
        receipt = doc(d.get("boundary_receipt_ref"))
        acknowledgements = [e for e in events
            if e["event_type"] == "resource_delivery_acknowledged/v1"
            and e["payload"].get("terminal_delivery_ref") == ref
            and e["producer_invocation_id"] == owner]
        if (not delivery_row or not owned(delivery_row)
                or d.get("state") != "acknowledged" or d.get("boundary") != boundary
                or d.get("resource_ref") != resource_ref
                or d.get("context_ref") != s.invocation_ref
                or d.get("authorization_ref") != s.operation_binding_ref
                or receipt.get("outcome") != "acknowledged"
                or receipt.get("boundary") != boundary
                or receipt.get("delivery_ref") != d.get("previous_delivery_ref")
                or receipt.get("witness_ref") != d.get("witness_ref")
                or type(receipt.get("positive_byte_count")) is not int
                or receipt.get("positive_byte_count") != d.get("resource_byte_count")
                or receipt.get("resource_byte_count") != d.get("resource_byte_count")
                or len(acknowledgements) != 1
                or acknowledgements[0]["payload"].get("boundary_receipt_ref") != d.get("boundary_receipt_ref")):
            return None
        return acknowledgements[0]

    # Calls are linked through their exact recipe, original neutral invocation,
    # and provider attempt, never a materialization receipt's byte count.
    requests = []
    for call in records:
        c = call["record"]
        if call["ref"]["entity_type"] != "llm_call_spec/v2" or not owned(call) or not scoped(c):
            continue
        linked = [r for r in records if r["ref"]["entity_type"] == "llm_invocation_spec/v1"
                  and owned(r) and scoped(r["record"])
                  and r["record"].get("request_resource_ref") == c.get("request_resource_ref")
                  and r["record"].get("agent_loop_ref") == c.get("agent_loop_ref")
                  and r["record"].get("turn_sequence") == c.get("turn_sequence")
                  and r["record"].get("invocation_kind") == "normal_turn"]
        if len(linked) != 1:
            continue
        ack = acknowledged(c.get("terminal_delivery_ref"), c.get("request_resource_ref"), "llm_prompt")
        if ack is None:
            continue
        submitted = []
        for attempt in records:
            a = attempt["record"]
            if (attempt["ref"]["entity_type"] != "provider_attempt_spec/v1"
                    or not owned(attempt) or not scoped(a)
                    or a.get("llm_call_ref") != call["ref"]
                    or a.get("request_resource_ref") != c.get("request_resource_ref")
                    or a.get("terminal_delivery_ref") != c.get("terminal_delivery_ref")):
                continue
            ledger = [e for e in events if e["event_type"].startswith("provider_attempt_")
                      and e["producer_invocation_id"] == owner
                      and e["payload"].get("provider_attempt_id") == attempt["ref"]["logical_id"]
                      and e["payload"].get("provider_attempt_ref", attempt["ref"]) == attempt["ref"]]
            if _submission(ledger) == "submitted":
                observed = [e for e in ledger if e["event_type"] in {
                    "provider_attempt_submission_observed/v1", "provider_attempt_proven_submitted/v1"}]
                if observed and min(e["ordinal"] for e in observed) >= max(
                        ack["ordinal"], attempt["ordinal"], call["ordinal"], linked[0]["ordinal"]):
                    submitted.append((attempt, observed))
        if not submitted:
            continue
        recipe = _json(material(c.get("request_resource_ref")))
        if (not isinstance(recipe, Mapping)
                or recipe.get("schema_version") != "logical_provider_request_recipe/v1"
                or not isinstance(recipe.get("messages"), list)):
            continue
        requests.append((call, linked[0], submitted, recipe))

    errors, evidence = [], []
    for source in required_sources:
        resource_ref = source["resource_ref"]
        raw = material(resource_ref)
        text = None
        if raw is not None:
            metadata = doc(_resource_version(resource_ref))
            try:
                text = raw.decode("utf-8")
                workspace_text = (metadata.get("origin_kind") == "workspace_write"
                                  and metadata.get("content_schema_ref") is None)
                if not workspace_text:
                    if metadata.get("media_type") == "application/json":
                        decoded = json.loads(text)
                        if isinstance(decoded, str):
                            text = decoded
                    elif not str(metadata.get("media_type", "")).startswith("text/"):
                        text = None
            except (UnicodeError, ValueError):
                text = None
        if text is None or hashlib.sha256(text.encode("utf-8")).hexdigest() != source["decoded_sha256"]:
            errors.append({"doc_id": source["doc_id"], "code": "source_body_identity_unavailable"})
            continue
        required = source["required_ranges"]
        if not required or any(
                r.get("unit") != "unicode_codepoint" or type(r.get("start")) is not int
                or type(r.get("end_exclusive")) is not int
                or not 0 <= r["start"] <= r["end_exclusive"] <= len(text) for r in required):
            errors.append({"doc_id": source["doc_id"], "code": "invalid_required_ranges"})
            continue
        candidates = []
        for action in records:
            a = action["record"]
            if (action["ref"]["entity_type"] != "agent_action/v2" or not owned(action)
                    or a.get("tool_name") != "read_file" or a.get("state") != "ACTION_APPLIED"
                    or a.get("tool_error_ref") is not None):
                continue
            result, args = a.get("result_metadata"), a.get("arguments")
            if (not isinstance(result, Mapping) or not isinstance(args, Mapping)
                    or result.get("kind") != "registered_file_read/v1"
                    or result.get("resource_ref") != resource_ref
                    or result.get("path") != args.get("path")
                    or _json(a.get("raw_arguments")) != args
                    or _resource_version(resource_ref) not in a.get("result_refs", [])
                    or result.get("use_receipt_ref") not in a.get("result_refs", [])):
                continue
            start, size = args.get("offset_chars", 0), args.get("max_chars", 32768)
            if type(start) is not int or type(size) is not int or start < 0 or size < 1:
                continue
            ack = acknowledged(result.get("use_receipt_ref"), resource_ref, "tool_result")
            loop = doc(a.get("agent_loop_ref"))
            if ack is None or not scoped(loop):
                continue
            settlements = [e for e in events if e["event_type"] == "agent_action_settled/v1"
                and e["producer_invocation_id"] == owner
                and all(e["payload"].get(k) == a.get(k) for k in (
                    "agent_action_id", "expected_revision", "tool_call_id", "tool_call_ordinal",
                    "tool_name", "arguments", "raw_arguments"))
                and e["payload"].get("agent_turn_id") == a["agent_turn_ref"]["logical_id"]
                and e["payload"].get("agent_loop_id") == a["agent_loop_ref"]["logical_id"]
                and e["payload"].get("settlement") == "ACTION_APPLIED"]
            turns = [e for e in events if e["event_type"] == "agent_turn_recorded/v2"
                and e["producer_invocation_id"] == owner
                and e["payload"].get("agent_loop_id") == a["agent_loop_ref"]["logical_id"]
                and e["payload"].get("agent_turn_id") == a["agent_turn_ref"]["logical_id"]
                and e["payload"].get("agent_turn_version_id") == a["agent_turn_ref"]["version_id"]
                and e["payload"].get("sequence") == a.get("turn_sequence")]
            if len(settlements) != 1 or len(turns) != 1:
                continue
            settled, turn = settlements[0], turns[0]
            # The original atomic action command appends its settlement event
            # before the object's publication event. Their shared transaction,
            # rather than their ordering within that commit, closes the fact.
            if (not turn["ordinal"] < ack["ordinal"] <= settled["ordinal"]
                    or not action.get("transaction_id")
                    or action["transaction_id"] != settled.get("transaction_id")):
                continue
            origin_invocation = doc(turn["payload"].get("llm_invocation_ref"))
            if (not scoped(origin_invocation)
                    or origin_invocation.get("turn_sequence") != a["turn_sequence"]
                    or origin_invocation.get("agent_loop_ref", {}).get("logical_id") != a["agent_loop_ref"]["logical_id"]):
                continue
            origin_calls = [r for r in records if r["ref"]["entity_type"] == "llm_call_spec/v2"
                and owned(r) and scoped(r["record"])
                and r["record"].get("request_resource_ref") == origin_invocation.get("request_resource_ref")
                and r["record"].get("agent_loop_ref") == origin_invocation.get("agent_loop_ref")
                and r["record"].get("turn_sequence") == a["turn_sequence"]
                and r["ordinal"] < turn["ordinal"]]
            if len(origin_calls) != 1:
                continue
            response_raw = material(turn["payload"].get("response_resource_ref"))
            if response_raw is None or len(response_raw) != turn["payload"].get("response_size"):
                continue
            response = _json(response_raw)
            calls = response.get("tool_calls", []) if isinstance(response, Mapping) else []
            ordinal = a.get("tool_call_ordinal")
            if (type(ordinal) is not int or not 0 <= ordinal < len(calls)
                    or calls[ordinal] != {"id": a["tool_call_id"], "name": "read_file", "arguments": a["raw_arguments"]}):
                continue
            candidates.append((action, dict(result, content=text[start:start + size]),
                               (min(start, len(text)), min(start + size, len(text))), settled, turn,
                               origin_calls[0]))
        proofs = []
        submitted_followup_requests = 0
        for call, invocation, attempts, recipe in requests:
            c = call["record"]
            eligible = [p for p in candidates
                if p[0]["record"]["agent_loop_ref"]["logical_id"] == c["agent_loop_ref"]["logical_id"]
                and p[0]["record"]["turn_sequence"] < c["turn_sequence"]
                and p[0]["record"]["agent_turn_ref"] in invocation["record"].get("prior_turn_refs", [])
                and p[5]["ref"] in c.get("prior_turn_refs", [])
                and p[3]["ordinal"] < call["ordinal"]]
            if eligible:
                submitted_followup_requests += 1
            for index, message, tool_call in _tool_messages(recipe["messages"]):
                body = _json(message.get("content"))
                matches = [p for p in eligible if message.get("tool_call_id") == p[0]["record"]["tool_call_id"]
                           and _key(body) == _key(p[1])
                           and tool_call.get("type") == "function"
                           and tool_call.get("function") == {"name": "read_file", "arguments": p[0]["record"]["raw_arguments"]}]
                if len(matches) != 1:
                    continue
                action, _, interval, settled, turn, origin_call = matches[0]
                for attempt, observed in attempts:
                    proofs.append({"agent_action_ref": action["ref"],
                        "agent_turn_ref": action["record"]["agent_turn_ref"],
                        "origin_llm_call_ref": origin_call["ref"],
                        "origin_llm_invocation_ref": turn["payload"]["llm_invocation_ref"],
                        "tool_call_id": action["record"]["tool_call_id"],
                        "settlement_event_id": settled["event_id"],
                        "read_delivery_ref": action["record"]["result_metadata"]["use_receipt_ref"],
                        "llm_call_ref": call["ref"], "llm_invocation_ref": invocation["ref"],
                        "request_resource_ref": c["request_resource_ref"],
                        "provider_attempt_ref": attempt["ref"],
                        "submission_events": [{k: e[k] for k in ("event_id", "ordinal", "event_type")} for e in observed],
                        "message_index": index, "start": interval[0], "end_exclusive": interval[1],
                        "unit": "unicode_codepoint"})
        union = _union((p["start"], p["end_exclusive"]) for p in proofs)
        missing = [r for r in required if not any(lo <= r["start"] and hi >= r["end_exclusive"] for lo, hi in union)]
        if missing:
            reason = ("MISSING_REQUIRED_READ" if not candidates else
                      "SUBMITTED_COVERAGE_NOT_ESTABLISHED" if not submitted_followup_requests else
                      "REQUIRED_RANGE_NOT_VISIBLE")
            errors.append({"doc_id": source["doc_id"], "code": "required_read_coverage_missing",
                           "reason_code": reason, "required_ranges": missing})
        evidence.append({"doc_id": source["doc_id"], "resource_ref": resource_ref,
            "decoded_sha256": source["decoded_sha256"], "covered_ranges": [
                {"start": lo, "end_exclusive": hi, "unit": "unicode_codepoint"} for lo, hi in union],
            "acknowledged_read_actions": len(candidates),
            "submitted_followup_requests": submitted_followup_requests, "proofs": proofs})
    return _plain({"errors": errors, "evidence": evidence, "cutoff": cutoff,
        "task_id": s.task_id, "invocation_ref": s.invocation_ref,
        "operation_binding_ref": s.operation_binding_ref, "transition_firing_ref": s.transition_firing_ref,
        "wire": "NOT_CHECKED", "semantic_use": "UNKNOWN"})


__all__ = ["ReadCoverageSnapshot", "collect_read_coverage_snapshot", "verify_required_read_coverage"]
