"""Pure original-fact-format tests; native closure is tested by the HOST suite."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.content_coverage import (
    ReadCoverageSnapshot, collect_read_coverage_snapshot,
    verify_required_read_coverage,
)
from cpn.rpnh.firing_content_contract import _ReadSQL, _freeze


def ref(kind, name):
    return {"entity_type": kind, "logical_id": name, "version_id": name + "-version"}


def resource(name):
    return {"resource_id": name, "resource_version_id": name + "-version"}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def fixture(slices=None):
    text = '首😀e\u0301行\n"尾"'
    slices = [(0, len(text))] if slices is None else slices
    inv, op, firing = ref("invocation/v1", "inv"), ref("operation_binding/v1", "op"), ref("transition_firing/v1", "firing")
    loop = ref("agent_loop/v1", "loop")
    source = resource("source")
    records, events, materials = [], [], {}
    scope = {"invocation_ref": inv, "operation_binding_ref": op}

    def add(r, data, ordinal=1, transaction=None):
        records.append({"ref": r, "record": data, "ordinal": ordinal,
                        "transaction_id": transaction or f"tx-{ordinal}", "producer_invocation_id": "inv"})

    def event(kind, data, ordinal, transaction=None):
        events.append({"event_id": f"event-{len(events)}", "event_type": kind,
                       "ordinal": ordinal, "payload": data, "task_id": "task",
                       "transaction_id": transaction or f"tx-{ordinal}", "producer_invocation_id": "inv"})

    def blob(r, payload, ordinal=1):
        materials[r["resource_id"]] = payload
        add(ref("resource_version/v1", r["resource_id"]), {"media_type": "application/json", "content_schema_ref": "text/v1"}, ordinal)

    def delivery(name, r, boundary, ordinal):
        terminal = ref("resource_delivery/v1", name)
        previous = ref("resource_delivery/v1", name + "-released")
        receipt = ref("delivery_boundary_receipt/v1", name + "-receipt")
        witness = ref("resource_release_witness/v1", name + "-witness")
        size = len(materials[r["resource_id"]])
        add(receipt, {"delivery_ref": previous, "witness_ref": witness, "boundary": boundary,
                      "outcome": "acknowledged", "resource_byte_count": size, "positive_byte_count": size}, ordinal)
        add(terminal, {"state": "acknowledged", "resource_ref": r, "boundary": boundary,
                      "context_ref": inv, "authorization_ref": op, "resource_byte_count": size,
                      "previous_delivery_ref": previous, "witness_ref": witness, "boundary_receipt_ref": receipt}, ordinal)
        event("resource_delivery_acknowledged/v1", {"terminal_delivery_ref": terminal,
              "boundary_receipt_ref": receipt, "outcome": "acknowledged"}, ordinal)
        return terminal

    add(inv, {**scope, "own_transition_firing_ref": firing, "task_ref": ref("task/v1", "task")})
    add(loop, scope)
    blob(source, encoded(text))
    for i, (start, size) in enumerate(slices):
        base = i * 20
        turn, action = ref("agent_turn/v1", f"turn{i}"), ref("agent_action/v2", f"action{i}")
        response_ref, request_ref = resource(f"response{i}"), resource(f"request{i}")
        args = {"path": "inputs/source", "offset_chars": start, "max_chars": size}
        raw_args = encoded(args).decode()
        tool = {"id": "read", "name": "read_file", "arguments": raw_args}
        origin_call = ref("llm_call_spec/v2", f"origin-call{i}")
        origin_inv = ref("llm_invocation_spec/v1", f"origin-inv{i}")
        origin_data = {**scope, "agent_loop_ref": loop, "turn_sequence": i,
                       "request_resource_ref": resource(f"origin-request{i}")}
        add(origin_call, origin_data, base + 2)
        add(origin_inv, origin_data, base + 3)
        blob(response_ref, encoded({"protocol": "llm_response_envelope/v1", "tool_calls": [tool]}), base + 4)
        event("agent_turn_recorded/v2", {"agent_loop_id": "loop", "agent_turn_id": turn["logical_id"],
              "agent_turn_version_id": turn["version_id"], "sequence": i,
              "llm_invocation_ref": origin_inv, "response_size": len(materials[response_ref["resource_id"]]),
              "response_resource_ref": response_ref}, base + 5)
        receipt = delivery(f"read{i}", source, "tool_result", base + 6)
        result = {"kind": "registered_file_read/v1", "path": args["path"],
                  "resource_ref": source, "use_receipt_ref": receipt}
        data = {"agent_action_id": action["logical_id"], "agent_action_ref": action,
                "agent_loop_ref": loop, "agent_turn_ref": turn, "turn_sequence": i,
                "tool_call_id": "read", "tool_call_ordinal": 0, "tool_name": "read_file",
                "raw_arguments": raw_args, "arguments": args, "expected_revision": i + 1,
                "state": "ACTION_APPLIED", "result_metadata": result, "tool_error_ref": None,
                "result_refs": [ref("resource_version/v1", "source"), receipt]}
        add(action, data, base + 9, f"action-tx-{i}")
        event("agent_action_settled/v1", {**data, "agent_loop_id": "loop",
              "agent_turn_id": turn["logical_id"], "settlement": "ACTION_APPLIED"}, base + 8, f"action-tx-{i}")
        recipe = {"schema_version": "logical_provider_request_recipe/v1", "model": "offline",
                  "max_tokens": 128, "messages": [
                      {"role": "assistant", "tool_calls": [{"id": "read", "type": "function",
                       "function": {"name": "read_file", "arguments": raw_args}}]},
                      {"role": "tool", "tool_call_id": "read", "content": encoded(dict(result, content=text[start:start + size])).decode()}]}
        blob(request_ref, encoded(recipe), base + 9)
        prompt_receipt = delivery(f"prompt{i}", request_ref, "llm_prompt", base + 10)
        call, llm_inv, attempt = ref("llm_call_spec/v2", f"call{i}"), ref("llm_invocation_spec/v1", f"llminv{i}"), ref("provider_attempt_spec/v1", f"attempt{i}")
        call_data = {**scope, "agent_loop_ref": loop, "turn_sequence": i + 1,
                     "request_resource_ref": request_ref, "terminal_delivery_ref": prompt_receipt,
                     "prior_turn_refs": [origin_call]}
        add(call, call_data, base + 11)
        add(llm_inv, {**call_data, "invocation_kind": "normal_turn", "prior_turn_refs": [turn]}, base + 12)
        add(attempt, {**scope, "llm_call_ref": call, "request_resource_ref": request_ref,
                      "terminal_delivery_ref": prompt_receipt}, base + 13)
        event("provider_attempt_submission_observed/v1", {"provider_attempt_id": attempt["logical_id"],
              "provider_attempt_ref": attempt}, base + 14)
    def reader(r, limit):
        payload = materials.get(r["resource_id"])
        return payload if payload is not None and len(payload) <= limit else None
    snapshot = ReadCoverageSnapshot(tuple(records), tuple(events), reader, inv, op, firing,
                                    "task", len(slices) * 20)
    required = [{"doc_id": "source", "resource_ref": source,
                 "decoded_sha256": hashlib.sha256(text.encode()).hexdigest(),
                 "required_ranges": [{"start": 0, "end_exclusive": len(text), "unit": "unicode_codepoint"}]}]
    return required, snapshot, materials


def verify(required, snapshot):
    return verify_required_read_coverage(required, snapshot, cutoff=snapshot.head_ordinal)


def one(snapshot, kind):
    return next(r["record"] for r in snapshot.records if r["ref"]["entity_type"] == kind
                and not r["ref"]["logical_id"].startswith("origin-"))


def edit_recipe(materials, edit):
    recipe = json.loads(materials["request0"])
    edit(recipe)
    materials["request0"] = encoded(recipe)


def test_original_fact_format_positive_and_readonly():
    required, snapshot, _ = fixture()
    before = deepcopy((required, snapshot.records, snapshot.events))
    result = verify(required, snapshot)
    assert result["errors"] == []
    proof = result["evidence"][0]["proofs"][0]
    assert proof["provider_attempt_ref"]["logical_id"] == "attempt0"
    assert proof["request_resource_ref"] == resource("request0")
    assert result["wire"] == "NOT_CHECKED" and result["semantic_use"] == "UNKNOWN"
    result["evidence"][0]["resource_ref"].clear()
    assert before == (required, snapshot.records, snapshot.events)


def test_action_and_settlement_must_share_original_atomic_transaction():
    required, snapshot, _ = fixture()
    action = next(r for r in snapshot.records if r['ref']['entity_type'] == 'agent_action/v2')
    action['transaction_id'] = 'different-original-command'
    result = verify(required, snapshot)
    assert result['errors'] and result['evidence'][0]['proofs'] == []


@pytest.mark.parametrize("damage", [
    "N01_unread", "N02_wrongref", "N05_not_submitted", "N06_truncated",
    "materialized_only", "reserved_only", "unknown_submission", "assistant_quote",
    "history_provenance", "receipt_only", "missing_ack", "wrong_operation", "wrong_attempt_recipe",
    "missing_prior_turn", "unsettled", "bad_original_toolcall", "bad_source_digest",
    "missing_origin_call", "bad_final_toolcall", "no_final_toolcall",
])
def test_missing_or_nonbody_facts_never_prove_coverage(damage):
    required, snapshot, materials = fixture()
    if damage == "N01_unread":
        snapshot = replace(snapshot, records=tuple(r for r in snapshot.records if r["ref"]["entity_type"] != "agent_action/v2"))
    elif damage == "N02_wrongref":
        one(snapshot, "agent_action/v2")["result_metadata"]["resource_ref"] = resource("wrong")
    elif damage in {"N05_not_submitted", "unknown_submission", "reserved_only", "materialized_only"}:
        snapshot.events[-1]["event_type"] = {"N05_not_submitted": "provider_attempt_proven_not_submitted/v1",
            "unknown_submission": "provider_attempt_submission_unknown/v1", "reserved_only": "provider_attempt_reserved/v2",
            "materialized_only": "provider_payload_materialized/v1"}[damage]
    elif damage == "N06_truncated":
        def truncate(recipe):
            body = json.loads(recipe["messages"][1]["content"])
            body["content"] = body["content"][:2]
            recipe["messages"][1]["content"] = encoded(body).decode()
        edit_recipe(materials, truncate)
    elif damage == "assistant_quote":
        edit_recipe(materials, lambda r: r["messages"][1].update(role="assistant"))
    elif damage in {"history_provenance", "receipt_only"}:
        body = {"provenance": one(snapshot, "agent_action/v2")} if damage == "history_provenance" else one(snapshot, "agent_action/v2")["result_metadata"]
        edit_recipe(materials, lambda r: r["messages"][1].update(content=encoded(body).decode()))
    elif damage == "missing_ack":
        snapshot = replace(snapshot, events=tuple(e for e in snapshot.events if e["event_type"] != "resource_delivery_acknowledged/v1"))
    elif damage == "wrong_operation":
        one(snapshot, "llm_call_spec/v2")["operation_binding_ref"] = ref("operation_binding/v1", "other")
    elif damage == "wrong_attempt_recipe":
        one(snapshot, "provider_attempt_spec/v1")["request_resource_ref"] = resource("other")
    elif damage == "missing_prior_turn":
        one(snapshot, "llm_invocation_spec/v1")["prior_turn_refs"] = []
    elif damage == "missing_origin_call":
        one(snapshot, "llm_call_spec/v2")["prior_turn_refs"] = []
    elif damage == "bad_final_toolcall":
        edit_recipe(materials, lambda r: r["messages"][0]["tool_calls"][0]["function"].update(arguments="{}"))
    elif damage == "no_final_toolcall":
        edit_recipe(materials, lambda r: r["messages"].pop(0))
    elif damage == "unsettled":
        snapshot = replace(snapshot, events=tuple(e for e in snapshot.events if e["event_type"] != "agent_action_settled/v1"))
    elif damage == "bad_original_toolcall":
        response = json.loads(materials["response0"])
        response["tool_calls"][0]["name"] = "search_text"
        materials["response0"] = encoded(response)
    elif damage == "bad_source_digest":
        required[0]["decoded_sha256"] = "0" * 64
    assert verify(required, snapshot)["errors"]


def test_unicode_union_of_distinct_submitted_requests_and_partial_teacher_ranges():
    required, snapshot, _ = fixture([(0, 3), (3, 6)])
    assert verify(required, snapshot)["errors"] == []
    assert len(verify(required, snapshot)["evidence"][0]["proofs"]) == 2
    required[0]["doc_id"] = "teacher_anchor"
    required[0]["required_ranges"] = [{"start": 1, "end_exclusive": 5, "unit": "unicode_codepoint"}]
    assert verify(required, snapshot)["errors"] == []


def test_unicode_byte_offsets_and_gaps_are_not_codepoint_coverage():
    required, snapshot, _ = fixture([(0, 2), (4, 10)])
    assert verify(required, snapshot)["errors"]
    required[0]["required_ranges"][0]["unit"] = "byte"
    assert verify(required, snapshot)["errors"][0]["code"] == "invalid_required_ranges"


def test_cutoff_excludes_future_submission_and_later_unknown_can_revoke():
    required, snapshot, _ = fixture()
    assert verify_required_read_coverage(required, snapshot, cutoff=13)["errors"]
    assert not verify_required_read_coverage(required, snapshot, cutoff=14)["errors"]
    unknown = deepcopy(snapshot.events[-1])
    unknown.update(event_id="unknown", ordinal=15, event_type="provider_attempt_submission_unknown/v1")
    snapshot = replace(snapshot, events=(*snapshot.events, unknown))
    assert verify(required, snapshot)["errors"]
    assert not verify_required_read_coverage(required, snapshot, cutoff=14)["errors"]


def test_reused_id_cannot_replace_exact_prior_turn_and_delivery_evidence():
    required, snapshot, materials = fixture([(0, 9), (0, 9)])
    actions = [r["record"] for r in snapshot.records if r["ref"]["entity_type"] == "agent_action/v2"]
    actions[1]["result_metadata"] = deepcopy(actions[0]["result_metadata"])
    actions[1]["result_refs"] = deepcopy(actions[0]["result_refs"])
    # The reused receipt is earlier than the second turn, so even that copied
    # action cannot become genuine second-turn evidence.
    invocations = [r["record"] for r in snapshot.records if r["ref"]["entity_type"] == "llm_invocation_spec/v1"
                   and not r["ref"]["logical_id"].startswith("origin-")]
    invocations[1]["prior_turn_refs"] = [a["agent_turn_ref"] for a in actions]
    recipe = json.loads(materials["request0"])
    materials["request1"] = encoded(recipe)
    snapshot = replace(snapshot, events=tuple(e for e in snapshot.events if e["ordinal"] != 14))
    # A valid earlier action may still uniquely match: removing all exact
    # originating-turn links must never be compensated by the reused ID.
    invocations[1]["prior_turn_refs"] = [actions[1]["agent_turn_ref"]]
    assert verify(required, snapshot)["errors"]


def test_same_call_id_distinct_ranges_have_unique_body_evidence():
    required, snapshot, _ = fixture([(0, 3), (3, 6)])
    assert not verify(required, snapshot)["errors"]


def test_same_call_id_two_turns_in_one_final_request_matches_exact_bodies():
    required, snapshot, materials = fixture([(0, 3), (3, 6)])
    calls = [r["record"] for r in snapshot.records if r["ref"]["entity_type"] == "llm_call_spec/v2"
             and not r["ref"]["logical_id"].startswith("origin-")]
    invocations = [r["record"] for r in snapshot.records if r["ref"]["entity_type"] == "llm_invocation_spec/v1"
                   and not r["ref"]["logical_id"].startswith("origin-")]
    calls[1]["prior_turn_refs"] = calls[0]["prior_turn_refs"] + calls[1]["prior_turn_refs"]
    invocations[1]["prior_turn_refs"] = invocations[0]["prior_turn_refs"] + invocations[1]["prior_turn_refs"]
    recipe = json.loads(materials["request1"])
    recipe["messages"] = json.loads(materials["request0"])["messages"] + recipe["messages"]
    materials["request1"] = encoded(recipe)
    snapshot = replace(snapshot, events=tuple(e for e in snapshot.events if e["ordinal"] != 14))
    result = verify(required, snapshot)
    assert not result["errors"]
    assert len(result["evidence"][0]["proofs"]) == 2
    assert {p["request_resource_ref"]["resource_id"] for p in result["evidence"][0]["proofs"]} == {"request1"}


def test_ambiguous_exact_producers_are_not_resolved_by_tool_call_id():
    required, snapshot, _ = fixture()
    original = next(r for r in snapshot.records if r["ref"]["entity_type"] == "agent_action/v2")
    duplicate = deepcopy(original)
    duplicate["ref"] = ref("agent_action/v2", "ambiguous")
    duplicate["record"].update(agent_action_id="ambiguous", agent_action_ref=duplicate["ref"])
    settlement = deepcopy(next(e for e in snapshot.events if e["event_type"] == "agent_action_settled/v1"))
    settlement["event_id"] = "ambiguous-settlement"
    settlement["payload"]["agent_action_id"] = "ambiguous"
    snapshot = replace(snapshot, records=(*snapshot.records, duplicate), events=(*snapshot.events, settlement))
    result = verify(required, snapshot)
    assert result["errors"] and result["evidence"][0]["proofs"] == []


def test_wrong_run_or_firing_is_rejected_and_caller_boolean_is_not_input():
    required, snapshot, _ = fixture()
    with pytest.raises(ValueError, match="task"):
        verify(required, replace(snapshot, task_id="other-run"))
    with pytest.raises(ValueError, match="firing"):
        verify(required, replace(snapshot, transition_firing_ref=ref("transition_firing/v1", "other")))
    with pytest.raises(TypeError, match="trusted"):
        verify_required_read_coverage(required, {"coverage_ok": True}, cutoff=20)


def test_bounded_original_sql_collector_excludes_pending_proposals():
    required, snapshot, materials = fixture()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE events(event_id,event_type,ordinal,payload_json,task_id,producer_invocation_id,occurred_at,recorded_at,transaction_id);"
                     "CREATE TABLE objects(object_type,logical_id,version_id,metadata_json,published_event_id,producer_invocation_id,transaction_id);")
    for event in snapshot.events:
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (event["event_id"], event["event_type"], event["ordinal"], json.dumps(event["payload"]), "task", "inv", "time", "time", event['transaction_id']))
    for i, item in enumerate(snapshot.records):
        event_id = f"publication-{i}"
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (event_id, "object_registered/v1", item["ordinal"], "{}", "task", "inv", "time", "time", item['transaction_id']))
        r = item["ref"]
        db.execute("INSERT INTO objects VALUES(?,?,?,?,?,?,?)", (r["entity_type"], r["logical_id"], r["version_id"], json.dumps(item["record"]), event_id, "inv", item['transaction_id']))
    view = SimpleNamespace(db=_ReadSQL(db), new_by_version={"pending-fake-PASS": {}}, events=[])
    args = dict(read_material=snapshot.read_material, invocation_ref=_freeze(snapshot.invocation_ref),
                operation_binding_ref=_freeze(snapshot.operation_binding_ref),
                transition_firing_ref=_freeze(snapshot.transition_firing_ref), cutoff=14)
    collected = collect_read_coverage_snapshot(view, **args)
    assert not verify(_freeze(required), collected)["errors"]
    with pytest.raises(ValueError, match="bound"):
        collect_read_coverage_snapshot(view, **args, max_records=1)
    with pytest.raises(ValueError, match="head"):
        collect_read_coverage_snapshot(view, **dict(args, cutoff=15))
    db.close()
