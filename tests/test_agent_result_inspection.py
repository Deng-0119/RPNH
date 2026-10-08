"""Synthetic evidence layers: no provider, credentials, or business data."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from cpn.rpnh.agent_result_inspection import project_agent_result_evidence, project_registry_agent_results
from cpn.rpnh.registry.schema_catalog import canonical_json


def ref(kind, digit):
    stem = {"llm_call_spec": "llm_call", "llm_invocation_spec": "llm_invocation",
            "provider_attempt_spec": "provider_attempt"}.get(kind.split('/')[0], kind.split('/')[0])
    return {"entity_type": kind, "logical_id": f"{stem}:{digit * 32}",
            "version_id": f"{stem}_version:{digit * 32}"}


def resource(digit):
    return {"resource_id": f"resource:{digit * 32}", "resource_version_id": f"resource_version:{digit * 32}"}


def fixture():
    loop, turn, action = ref("agent_loop/v1", "a"), ref("agent_turn/v1", "b"), ref("agent_action/v3", "c")
    call, invocation, attempt = ref("llm_call_spec/v2", "d"), ref("llm_invocation_spec/v1", "e"), ref("provider_attempt_spec/v1", "f")
    receipt, request = resource("1"), resource("2")
    data = {"agent_loop_ref": loop, "agent_turn_ref": turn, "turn_sequence": 0,
            "tool_call_id": "synthetic-tool", "outcome": "returned", "terminal_receipt_ref": receipt,
            "output": {"new_id": "synthetic-唯一", "quotes": '"\\\n'}}
    call_data = {"agent_loop_ref": loop, "turn_sequence": 1, "request_resource_ref": request,
                 "prior_turn_refs": [ref("llm_call_spec/v2", "0")]}
    records = [{"ref": action, "record": data}, {"ref": call, "record": call_data},
               {"ref": invocation, "record": {**call_data, "prior_turn_refs": [turn]}},
               {"ref": attempt, "record": {"llm_call_ref": call}},
               {"ref": loop, "record": {"revision": 2, "state": "TERMINAL", "llm_turn_budget": 3}}]
    metadata = {"kind": "managed_native_plugin_result/v1", "output": data["output"], "terminal_receipt_ref": receipt}
    envelope = {"protocol": "llm_request_envelope/v1", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "synthetic-tool"}]},
        {"role": "tool", "tool_call_id": "synthetic-tool", "content": canonical_json(metadata).decode()}]}
    event = {"event_id": "event:synthetic", "event_type": "provider_attempt_submission_observed/v1",
             "ordinal": 1, "payload": {"provider_attempt_id": attempt["logical_id"], "provider_attempt_ref": attempt}}
    return records, [event], envelope


def project(records, events, envelope, **kwargs):
    return project_agent_result_evidence(records=records, events=events,
        read_material=lambda ref, limit: canonical_json(envelope), **kwargs)


def request_view(result):
    return result["actions"][0]["requests"][0]


def test_full_evidence_is_not_semantic_use_or_business_success():
    records, events, envelope = fixture()
    original = deepcopy((records, events, envelope))
    result = project(records, events, envelope)
    request = request_view(result)
    assert request["request_projection"] == request["submitted_request_inclusion"] == "full"
    assert request["prior_turn_refs"] == [records[0]["record"]["agent_turn_ref"]]
    assert request["prior_llm_call_refs"] == records[1]["record"]["prior_turn_refs"]
    assert result["actions"][0]["semantic_use"] == result["actions"][0]["decision_influence"] == "unknown"
    assert result["business_success"] == result["benchmark_score"] == "not_checked"
    assert result["actions"][0]["business_readback"] == "not_checked"
    assert (records, events, envelope) == original
    result["actions"][0]["requests"][0]["prior_turn_refs"].clear()
    assert (records, events, envelope) == original


@pytest.mark.parametrize("event_type,state", [
    ("provider_attempt_submission_not_permitted/v1", "not_submitted"),
    ("provider_attempt_submission_unknown/v1", "submission_unknown"),
    ("provider_attempt_submission_permitted/v2", "unknown"),
    ("provider_attempt_dispatch_started/v2", "unknown"),
])
def test_constructed_request_is_not_submitted(event_type, state):
    records, events, envelope = fixture()
    events[0]["event_type"] = event_type
    view = request_view(project(records, events, envelope))
    assert view["request_projection"] == "full"
    assert view["submitted_request_inclusion"] == "not_established"
    assert view["provider_attempts"][0]["submission_state"] == state


def test_constructed_invocation_without_call_is_preserved():
    records, _, envelope = fixture()
    records = [r for r in records if r["ref"]["entity_type"] not in {"llm_call_spec/v2", "provider_attempt_spec/v1"}]
    view = request_view(project(records, [], envelope))
    assert view["llm_call_ref"] is None and view["llm_invocation_refs"]
    assert view["request_projection"] == "full"
    assert view["submitted_request_inclusion"] == "not_established"


@pytest.mark.parametrize("projection", ["bounded", "reference", "absent"])
def test_layers_stay_distinct(projection):
    records, events, envelope = fixture()
    action = records[0]
    if projection == "reference":
        envelope["messages"][1]["content"] = json.dumps({"agent_action_ref": action["ref"]})
    elif projection == "bounded":
        from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
        metadata = json.loads(envelope["messages"][1]["content"])
        page = bounded_managed_output_projection(metadata, {"agent_action_ref": action["ref"],
            "terminal_receipt_ref": action["record"]["terminal_receipt_ref"], "offset_chars": 2, "max_bytes": 2000})
        envelope["messages"][1]["tool_call_id"] = "reader-other-call-id"
        envelope["messages"][1]["content"] = json.dumps(page)
    else:
        envelope["messages"] = [{"role": "assistant", "content": "I used every result; readback succeeded."}]
    view = request_view(project(records, events, envelope))
    assert view["request_projection"] == view["submitted_request_inclusion"] == projection


def test_old_bounded_renderer_is_verified_against_registered_output():
    from cpn.components.agent_loop.compact import reduce_tool_output
    records, events, envelope = fixture()
    records[0]["record"]["output"] = {"body": "middle" * 200}
    metadata = json.loads(envelope["messages"][1]["content"])
    metadata["output"] = records[0]["record"]["output"]
    envelope["messages"][1]["content"] = reduce_tool_output(canonical_json(metadata).decode(), byte_limit=400)
    assert request_view(project(records, events, envelope))["request_projection"] == "bounded"
    envelope["messages"][1]["content"] += "invented"
    assert request_view(project(records, events, envelope))["request_projection"] == "unavailable"


@pytest.mark.parametrize("raw", [None, b'\xff', b'not JSON', b'{}', b'x' * 300])
def test_missing_invalid_and_over_limit_material_is_unavailable(raw):
    records, events, _ = fixture()
    result = project_agent_result_evidence(records=records, events=events,
        read_material=lambda *_: raw, max_request_bytes=200)
    assert request_view(result)["request_projection"] == "unavailable"


def test_not_checked_does_not_read_and_no_next_request_requires_complete_scope():
    records, events, _ = fixture()
    def forbidden(*_):
        raise AssertionError("unexpected material read")
    result = project_agent_result_evidence(records=records, events=events,
        read_material=forbidden, inspect_requests=False)
    assert request_view(result)["request_projection"] == "not_checked"
    for complete, status in [(True, "no_next_request"), (False, "unavailable")]:
        result = project_agent_result_evidence(records=records[:1], events=[], scope_complete=complete)
        assert result["actions"][0]["request_status"] == status


def test_materialization_byte_count_is_lineage_not_wire():
    records, _, envelope = fixture()
    records.append({"ref": ref("provider_payload_materialization_receipt/v1", "3"),
                    "record": {"provider_attempt_ref": records[3]["ref"], "llm_call_ref": records[1]["ref"],
                               "request_recipe_ref": resource("2"), "byte_count": 9000}})
    view = request_view(project(records, [], envelope))
    assert view["provider_attempts"][0]["materialization_lineage"][0]["byte_count"] == 9000
    assert view["provider_attempts"][0]["wire_payload"] == "not_checked"
    assert view["submitted_request_inclusion"] == "not_established"


@pytest.mark.parametrize("reason", ["llm_turn_cap", "task_model_call_cap"])
def test_original_terminal_caps_counts_and_outer_aggregate_are_preserved(reason):
    records, events, envelope = fixture()
    events.append({"event_id": "event:stop", "ordinal": 2, "event_type": "agent_loop_terminal/v1",
                   "payload": {"agent_loop_id": records[4]["ref"]["logical_id"], "revision": 2,
                               "terminal_reason": reason, "llm_turns_used": 3, "state": "TERMINAL"}})
    result = project(records, events, envelope, actual_model_call_counts=[8, 1],
                     task_model_call_limit=7, outer_stop={"stop_reason": "terminal"})
    stop = result["stops"][0]
    assert (stop["terminal_reason"], stop["llm_turn_budget"], stop["llm_turns_used"],
            stop["task_model_call_limit"], stop["actual_model_call_counts"]) == (reason, 3, 3, 7, [8, 1])
    assert stop["agent_loop_ref"] == records[4]["ref"]
    assert stop["terminal_event"]["event_id"] == "event:stop"
    assert result["outer_stop"] == {"stop_reason": "terminal"}
    assert result["runtime_terminal"] == "not_checked"  # a child-loop stop is not whole-run terminal
    assert stop["stop_stage"] == "not_recorded"


def test_exact_loop_prior_turn_receipt_and_duplicate_id_boundaries():
    records, events, envelope = fixture()
    records[2]["record"]["prior_turn_refs"] = []
    assert request_view(project(records, events, envelope))["reason"] == "prior_turn_link_unavailable"
    records, events, envelope = fixture()
    records[1]["record"]["agent_loop_ref"] = ref("agent_loop/v1", "9")
    result = project(records, events, envelope)
    assert request_view(result)["llm_call_ref"] is None  # invocation still visible, unmatched call isn't borrowed
    records, events, envelope = fixture()
    envelope["messages"].append(deepcopy(envelope["messages"][1]))
    assert request_view(project(records, events, envelope))["reason"] == "ambiguous_reused_tool_call_id"
    records, events, envelope = fixture()
    body = json.loads(envelope["messages"][1]["content"])
    body["terminal_receipt_ref"] = resource("9")
    envelope["messages"][1]["content"] = json.dumps(body)
    assert request_view(project(records, events, envelope))["request_projection"] == "unavailable"


def test_permission_and_integrity_errors_propagate():
    records, events, _ = fixture()
    def denied(*_):
        raise PermissionError("synthetic denied")
    with pytest.raises(PermissionError, match="synthetic denied"):
        project_agent_result_evidence(records=records, events=events, read_material=denied)


def test_office_v1_graph_unchanged_v2_has_narrow_evidence_words(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "examples/harnessaudit_office/src"))
    import rpnh_ha.comparison_workflow as workflow
    original = subprocess.check_output(["git", "show", "HEAD:examples/harnessaudit_office/src/rpnh_ha/comparison_workflow.py"], cwd=root, text=True)
    historic = {"__name__": "rpnh_ha._historical_workflow", "__package__": "rpnh_ha"}
    exec(compile(original, "historical-comparison-workflow", "exec"), historic)
    view = {"hub_role": "hub", "agents": [{"role": role, "description": "Synthetic role", "system_prompt": "Synthetic prompt"}
                                          for role in ("hub", "specialist")]}
    task = SimpleNamespace(as_dict=lambda: view)
    monkeypatch.setattr(workflow, "condition_of", lambda _: workflow.CONDITION_ID)
    historic["condition_of"] = lambda _: workflow.CONDITION_ID
    old = historic["build_comparison_workflow"](task)
    assert canonical_json(workflow.build_comparison_workflow(task)[0].to_dict()) == canonical_json(old[0].to_dict())
    assert workflow.build_comparison_workflow(task) == old
    assert workflow.COMMON == historic["COMMON"]
    monkeypatch.setattr(workflow, "condition_of", lambda _: workflow.READBACK_CONDITION_ID)
    new = workflow.build_comparison_workflow(task)
    assert new != old
    assert all("read_managed_output" in n.execution.tools for n in new[0].nodes)
    assert all("read_managed_output" not in n.execution.tools for n in old[0].nodes)
    assert "confirmed subsequent model-input consumption" not in workflow.V2_COMMON
    assert "semantic use or decision influence" in workflow.V2_COMMON
    assert "request evidence is unavailable" in workflow.V2_HUB_FINALIZE
    assert "public readback" in workflow.V2_HUB_FINALIZE


def test_public_inspector_on_actual_registered_synthetic_run(tmp_path, monkeypatch):
    from test_optional_managed_plugin_actions import (
        test_node_scoped_managed_calls_persist_v3_alongside_builtin_v2,
    )
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.registry._registry import _RegistryCore
    test_node_scoped_managed_calls_persist_v3_alongside_builtin_v2(tmp_path, monkeypatch)
    run = tmp_path / "run"
    catalog = agent_task_catalog()
    core = _RegistryCore(run, create=False, read_only=True, catalog=catalog)
    before = (core.event_store.max_ordinal(), core.event_store.writer_epoch,
              len(core.event_store.object_rows()), core.event_store.actual_model_call_counts())
    result = project_registry_agent_results(run, catalog=catalog)
    managed = [a for a in result["actions"] if a["agent_action_ref"]["entity_type"] == "agent_action/v3"]
    assert len(managed) == 2
    assert all(a["registered_return"]["outcome"] == "returned" for a in managed)
    assert all(any(r["request_projection"] == "full" for r in a["requests"]) for a in managed)
    # This legacy synthetic port does not necessarily publish a transport
    # submission observation. Assert from its real ledger, never assume it.
    for action in managed:
        for request in action["requests"]:
            assert request["llm_call_ref"] and request["llm_invocation_refs"]
            assert request["prior_turn_refs"] and request["provider_attempts"]
            if not any(a["submission_state"] == "submitted" for a in request["provider_attempts"]):
                assert request["submitted_request_inclusion"] == "not_established"
    assert result["stops"] and result["actual_model_call_counts"] == list(before[3])
    assert result["runtime_terminal"] and result["run_terminal_evidence"]
    assert all(a["settlement_events"] for a in managed)
    assert all(any(r["submitted_request_inclusion"] == "full" for r in a["requests"]) for a in managed)
    bounded = project_registry_agent_results(run, catalog=catalog, max_request_bytes=1)
    assert all(r["request_projection"] == "unavailable" for a in bounded["actions"] for r in a["requests"])
    from cpn.rpnh.registry.object_store import ObjectStore, ObjectIntegrityError
    original_read = ObjectStore.read_registered
    def missing_request(self, prepared):
        if prepared.metadata.get("content_schema_ref") == "registry_v1/logical_provider_request_recipe/v1":
            try:
                raise FileNotFoundError("synthetic historical request material missing")
            except FileNotFoundError as exc:
                raise ObjectIntegrityError("missing registered payload") from exc
        return original_read(self, prepared)
    monkeypatch.setattr(ObjectStore, "read_registered", missing_request)
    missing = project_registry_agent_results(run, catalog=catalog)
    assert all(r["request_projection"] == "unavailable" for a in missing["actions"] for r in a["requests"])
    monkeypatch.setattr(ObjectStore, "read_registered", original_read)
    assert result["runtime_execution_status"] == "terminal" and result["run_execution_authority_ref"]
    (tmp_path / "registered-evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    assert before == (core.event_store.max_ordinal(), core.event_store.writer_epoch,
                      len(core.event_store.object_rows()), core.event_store.actual_model_call_counts())


def test_actual_registered_recipe_shape_is_supported_without_claiming_wire():
    records, events, envelope = fixture()
    envelope.pop("protocol")
    envelope.update(schema_version="logical_provider_request_recipe/v1", model="synthetic", max_tokens=123)
    view = request_view(project(records, events, envelope))
    assert view["request_projection"] == "full"
    assert view["provider_attempts"][0]["wire_payload"] == "not_checked"


def test_office_v2_real_condition_helper_and_public_builder(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "examples/harnessaudit_office/src"))
    from rpnh_ha.comparison_condition import READBACK_CONDITION_ID
    from rpnh_ha.task_view import PublicTask
    from rpnh_ha.workflow import build_business_workflow
    task = PublicTask(json.dumps({"task_id": "off-t1", "configuration_condition": READBACK_CONDITION_ID,
        "hub_role": "hub", "agents": [{"role": role, "description": "Synthetic", "system_prompt": "Synthetic"}
                                       for role in ("hub", "specialist")]}))
    graph, _ = build_business_workflow(task)
    assert all("read_managed_output" in node.execution.tools for node in graph.nodes)
    document = canonical_json(graph.to_dict()).decode()
    assert "confirmed subsequent model-input consumption" not in document
    assert "registered model consumption" not in document
    assert "Neither a model-authored claim nor readback proves semantic use" in document


def reader_fixture(*, large=True):
    from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
    records, events, envelope = fixture()
    source = records[0]
    source["record"]["output"] = {"body": "readback-data" * (2000 if large else 1)}
    reader_ref, reader_turn = ref("agent_action/v2", "7"), ref("agent_turn/v1", "8")
    page = bounded_managed_output_projection(
        {"kind": "managed_native_plugin_result/v1", "output": source["record"]["output"],
         "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]},
        {"agent_action_ref": source["ref"],
         "terminal_receipt_ref": source["record"]["terminal_receipt_ref"], "max_bytes": 2000})
    reader = {"ref": reader_ref, "record": {
        "agent_loop_ref": source["record"]["agent_loop_ref"], "agent_turn_ref": reader_turn,
        "turn_sequence": 1, "tool_call_id": "reader-call", "tool_name": "read_managed_output",
        "state": "ACTION_APPLIED", "result_metadata": page}}
    records.append(reader)
    records[1]["record"]["turn_sequence"] = records[2]["record"]["turn_sequence"] = 3
    records[2]["record"]["prior_turn_refs"].append(reader_turn)
    envelope["messages"] = [
        {"role": "assistant", "tool_calls": [{"id": "reader-call", "type": "function",
         "function": {"name": "read_managed_output", "arguments": json.dumps({
             "agent_action_ref": source["ref"],
             "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]})}}]},
        {"role": "tool", "tool_call_id": "reader-call", "content": canonical_json(page).decode()}]
    return records, events, envelope


@pytest.mark.parametrize("large,source_projection", [(True, "bounded"), (False, "full")])
@pytest.mark.parametrize("submitted", [True, False])
def test_registered_reader_page_and_source_have_independent_coverage(large, source_projection, submitted):
    records, events, envelope = reader_fixture(large=large)
    result = project(records, events if submitted else [], envelope)
    reader, source = result["actions"]
    request = reader["requests"][0]
    assert request["request_projection"] == "full"
    assert source["requests"][0]["request_projection"] == source_projection
    assert request["submitted_request_inclusion"] == ("full" if submitted else "not_established")
    assert request["provider_attempts"][0]["submission_state"] == ("submitted" if submitted else "unknown")
    assert reader["agent_loop_ref"] == records[-1]["record"]["agent_loop_ref"]
    assert reader["agent_turn_ref"] == records[-1]["record"]["agent_turn_ref"]
    assert reader["tool_call_id"] == "reader-call"
    assert reader["semantic_use"] == reader["decision_influence"] == "unknown"


@pytest.mark.parametrize("mutation", ["content", "receipt", "source", "call_id", "producing_action", "turn", "loop"])
def test_reader_identity_and_exact_registered_page_are_required(mutation):
    records, events, envelope = reader_fixture()
    reader = records[-1]
    body = json.loads(envelope["messages"][1]["content"])
    if mutation == "content":
        body["content"] = "invented"
    elif mutation == "receipt":
        body["terminal_receipt_ref"] = resource("9")
    elif mutation == "source":
        body["agent_action_ref"] = ref("agent_action/v3", "9")
    elif mutation == "call_id":
        envelope["messages"][1]["tool_call_id"] = "wrong-producer"
    elif mutation == "producing_action":
        body["agent_action_ref"] = ref("agent_action/v2", "9")
    elif mutation == "turn":
        records[2]["record"]["prior_turn_refs"].remove(reader["record"]["agent_turn_ref"])
    else:
        reader["record"]["agent_loop_ref"] = ref("agent_loop/v1", "9")
    envelope["messages"][1]["content"] = canonical_json(body).decode()
    result = project(records, events, envelope)
    requests = result["actions"][0]["requests"]
    if mutation == "loop":
        assert requests == []
    else:
        assert requests[0]["request_projection"] not in {"full", "bounded"}
    if mutation in {"content", "receipt"}:
        assert result["actions"][1]["requests"][0]["request_projection"] == "reference"


@pytest.mark.parametrize("duplicate_message", [True, False])
def test_reader_reused_call_id_does_not_borrow_another_turn(duplicate_message):
    records, events, envelope = reader_fixture()
    if duplicate_message:
        envelope["messages"].append(deepcopy(envelope["messages"][1]))
    else:
        other_reader = deepcopy(records[-1])
        other_reader["ref"] = ref("agent_action/v2", "9")
        other_reader["record"].update(agent_turn_ref=ref("agent_turn/v1", "9"), turn_sequence=2)
        records[2]["record"]["prior_turn_refs"].append(other_reader["record"]["agent_turn_ref"])
        records.append(other_reader)
    result = project(records, events, envelope)
    readers = [a for a in result["actions"] if a["agent_action_ref"]["entity_type"] == "agent_action/v2"]
    assert all(a["requests"][0]["request_projection"] == "unavailable" for a in readers)
    assert all(a["requests"][0]["reason"] == "ambiguous_reused_tool_call_id" for a in readers)


def test_reader_reused_call_id_with_distinct_registered_results_is_exact():
    records, events, envelope = reader_fixture()
    other_reader = deepcopy(records[-1])
    other_reader["ref"] = ref("agent_action/v2", "9")
    other_reader["record"].update(agent_turn_ref=ref("agent_turn/v1", "9"), turn_sequence=2)
    other_reader["record"]["result_metadata"]["content"] = "different page"
    records[2]["record"]["prior_turn_refs"].append(other_reader["record"]["agent_turn_ref"])
    records.append(other_reader)
    result = project(records, events, envelope)
    assert result["actions"][0]["requests"][0]["request_projection"] == "full"
    assert result["actions"][1]["requests"][0]["request_projection"] == "unavailable"


@pytest.mark.parametrize("event_type,state", [
    ("provider_attempt_submission_not_permitted/v1", "not_submitted"),
    ("provider_attempt_submission_unknown/v1", "submission_unknown"),
])
def test_full_reader_page_does_not_establish_submission(event_type, state):
    records, events, envelope = reader_fixture()
    events[0]["event_type"] = event_type
    result = project(records, events, envelope)
    request = result["actions"][0]["requests"][0]
    assert request["request_projection"] == "full"
    assert request["provider_attempts"][0]["submission_state"] == state
    assert request["submitted_request_inclusion"] == "not_established"
    assert result["actions"][0]["semantic_use"] == "unknown"


@pytest.mark.parametrize("recipe", [False, True])
def test_distinct_reader_pages_with_reused_ids_in_valid_history(recipe):
    from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
    from cpn.components.request_protocol import validate_llm_request_message_history
    records, events, envelope = reader_fixture()
    source = records[0]
    other = deepcopy(records[-1])
    other["ref"] = ref("agent_action/v2", "9")
    other["record"].update(agent_turn_ref=ref("agent_turn/v1", "9"), turn_sequence=2)
    arguments = {"agent_action_ref": source["ref"],
                 "terminal_receipt_ref": source["record"]["terminal_receipt_ref"],
                 "offset_chars": 2000, "max_bytes": 2000}
    other["record"]["result_metadata"] = bounded_managed_output_projection(
        {"kind": "managed_native_plugin_result/v1", "output": source["record"]["output"],
         "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]}, arguments)
    records[2]["record"]["prior_turn_refs"].append(other["record"]["agent_turn_ref"])
    records.append(other)
    assistant = deepcopy(envelope["messages"][0])
    assistant["tool_calls"][0]["function"]["arguments"] = json.dumps(arguments)
    envelope["messages"].extend([assistant, {"role": "tool", "tool_call_id": "reader-call",
        "content": canonical_json(other["record"]["result_metadata"]).decode()}])
    if recipe:
        del envelope["protocol"]
        envelope["schema_version"] = "logical_provider_request_recipe/v1"
    validate_llm_request_message_history(envelope["messages"])
    result = project(records, events, envelope)
    assert [a["requests"][0]["request_projection"] for a in result["actions"]] == ["full", "full", "bounded"]
    assert all(a["semantic_use"] == a["decision_influence"] == "unknown" for a in result["actions"])


@pytest.mark.parametrize("submitted", [False, True])
def test_default_batch_bound_preserves_reader_and_source_evidence(submitted):
    from cpn.components.agent_loop.compact import bound_tool_result_groups
    from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
    from cpn.components.request_protocol import validate_llm_request_message_history
    records, events, envelope = reader_fixture()
    source, template = records[0], records.pop()
    assistant = {"role": "assistant", "tool_calls": []}
    messages = []
    for index, digit in enumerate("789ab"):
        reader = deepcopy(template)
        reader["ref"] = ref("agent_action/v2", digit)
        call_id = f"reader-{index}"
        arguments = {"agent_action_ref": source["ref"],
                     "terminal_receipt_ref": source["record"]["terminal_receipt_ref"],
                     "offset_chars": index * 1000, "max_bytes": 10000}
        reader["record"].update(tool_call_id=call_id, tool_call_ordinal=index, arguments=arguments)
        reader["record"]["result_metadata"] = bounded_managed_output_projection(
            {"kind": "managed_native_plugin_result/v1", "output": source["record"]["output"],
             "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]}, arguments)
        records.append(reader)
        assistant["tool_calls"].append({"id": call_id, "type": "function", "function": {
            "name": "read_managed_output", "arguments": json.dumps(arguments)}})
        messages.append({"role": "tool", "tool_call_id": call_id,
                         "content": canonical_json(reader["record"]["result_metadata"]).decode()})
    envelope["messages"] = [assistant, *messages]
    assert [a["requests"][0]["request_projection"] for a in project(records, events, envelope)["actions"]] == ["full"] * 5 + ["bounded"]
    envelope["messages"] = list(bound_tool_result_groups(envelope["messages"]))
    validate_llm_request_message_history(envelope["messages"])
    assert len(canonical_json(envelope["messages"])) <= 40000
    original = deepcopy((records, events, envelope))
    result = project(records, events if submitted else [], envelope)
    projections = ["full", "full", "full", "bounded", "reference", "bounded"]
    assert [a["requests"][0]["request_projection"] for a in result["actions"]] == projections
    assert [a["requests"][0]["submitted_request_inclusion"] for a in result["actions"]] == (
        projections if submitted else ["not_established"] * 6)
    assert all(a["semantic_use"] == a["decision_influence"] == "unknown" for a in result["actions"])
    assert (records, events, envelope) == original


def reduced_reader_fixture(projection):
    from cpn.components.agent_loop.compact import bound_tool_result_groups
    from cpn.components.agent_loop.managed_output import bound_managed_output_page
    records, events, envelope = reader_fixture()
    page = records[-1]["record"]["result_metadata"]
    if projection == "bounded":
        body = bound_managed_output_page(page, 1000)
    else:
        body = {"kind": "tool_result_reference/v1", "result_kind": "managed_output_page/v1",
                **{key: value for key, value in page.items()
                   if key not in {"kind", "content", "truncated", "next_offset_chars"}}}
        minimum = deepcopy(envelope["messages"])
        minimum[1]["content"] = canonical_json(body).decode()
        reduced = bound_tool_result_groups(envelope["messages"], byte_limit=len(canonical_json(minimum)))
        assert json.loads(reduced[1]["content"]) == body
    envelope["messages"][1]["content"] = canonical_json(body).decode()
    return records, events, envelope


@pytest.mark.parametrize("projection", ["bounded", "reference"])
@pytest.mark.parametrize("mutation", ["receipt", "source", "reader", "offset", "total", "field", "call_id", "turn", "loop"])
def test_reduced_reader_requires_exact_metadata_and_producer_link(projection, mutation):
    records, events, envelope = reduced_reader_fixture(projection)
    assert project(records, events, envelope)["actions"][0]["requests"][0]["request_projection"] == projection
    body = json.loads(envelope["messages"][1]["content"])
    if mutation == "receipt":
        body["terminal_receipt_ref"] = resource("9")
    elif mutation == "source":
        body["agent_action_ref"] = ref("agent_action/v3", "9")
    elif mutation == "reader":
        body["reader"] = "read_action_output"
    elif mutation == "offset":
        body["offset_chars"] = False
    elif mutation == "total":
        body["total_chars"] += 1
    elif mutation == "field":
        body["unregistered"] = True
    elif mutation == "call_id":
        envelope["messages"][1]["tool_call_id"] = "wrong-producer"
    elif mutation == "turn":
        records[2]["record"]["prior_turn_refs"].remove(records[-1]["record"]["agent_turn_ref"])
    else:
        records[-1]["record"]["agent_loop_ref"] = ref("agent_loop/v1", "9")
    envelope["messages"][1]["content"] = canonical_json(body).decode()
    requests = project(records, events, envelope)["actions"][0]["requests"]
    if mutation == "loop":
        assert requests == []
    elif mutation == "call_id":
        assert requests[0]["request_projection"] == "absent"
    else:
        assert requests[0]["request_projection"] == "unavailable"
        if mutation == "turn":
            assert requests[0]["reason"] == "prior_turn_link_unavailable"


@pytest.mark.parametrize("mutation", ["content", "next_offset", "truncated", "shifted", "extended"])
def test_reduced_reader_page_must_be_a_valid_prefix_of_registered_page(mutation):
    records, events, envelope = reduced_reader_fixture("bounded")
    body = json.loads(envelope["messages"][1]["content"])
    if mutation == "content":
        body["content"] = "?" + body["content"][1:]
    elif mutation == "next_offset":
        body["next_offset_chars"] += 1
    elif mutation == "truncated":
        body["truncated"] = False
    elif mutation == "shifted":
        body["content"] = body["content"][1:]
        body["offset_chars"] += 1
    else:
        from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
        source = records[0]
        body = bounded_managed_output_projection(
            {"kind": "managed_native_plugin_result/v1", "output": source["record"]["output"],
             "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]},
            {"agent_action_ref": source["ref"], "terminal_receipt_ref": source["record"]["terminal_receipt_ref"],
             "max_bytes": 3000})
    envelope["messages"][1]["content"] = canonical_json(body).decode()
    assert project(records, events, envelope)["actions"][0]["requests"][0]["request_projection"] == "unavailable"


@pytest.mark.parametrize("projection", ["bounded", "reference"])
@pytest.mark.parametrize("collision", ["message", "reader", "source"])
def test_reduced_reader_producer_collisions_stay_ambiguous(projection, collision):
    records, events, envelope = reduced_reader_fixture(projection)
    if collision == "message":
        envelope["messages"].extend(deepcopy(envelope["messages"]))
    elif collision == "reader":
        other = deepcopy(records[-1])
        other["ref"] = ref("agent_action/v2", "9")
        other["record"].update(agent_turn_ref=ref("agent_turn/v1", "9"), turn_sequence=2)
        # Distinct original lengths still collide if the retained prefix or
        # reference cannot distinguish the two registered pages.
        page = other["record"]["result_metadata"]
        page["content"] = page["content"][:-1]
        page["next_offset_chars"] -= 1
        records[2]["record"]["prior_turn_refs"].append(other["record"]["agent_turn_ref"])
        records.append(other)
    else:
        records[0]["record"]["tool_call_id"] = "reader-call"
    view = project(records, events, envelope)["actions"][0]["requests"][0]
    assert view["request_projection"] == "unavailable"
    assert view["reason"] == "ambiguous_reused_tool_call_id"


@pytest.mark.parametrize("projection", ["bounded", "reference"])
@pytest.mark.parametrize("other_producer", ["unlinked_turn", "other_loop", "later_turn", "nonzero_offset", "different_source"])
def test_unrelated_producers_do_not_make_reader_projection_ambiguous(projection, other_producer):
    from cpn.components.agent_loop.managed_output import bounded_managed_output_projection
    records, events, envelope = reduced_reader_fixture(projection)
    source, reader = records[0], records[-1]
    if other_producer in {"unlinked_turn", "other_loop", "later_turn"}:
        other = deepcopy(reader)
        other["ref"] = ref("agent_action/v2", "9")
        other["record"]["agent_turn_ref"] = ref("agent_turn/v1", "9")
        if other_producer == "other_loop":
            other["record"]["agent_loop_ref"] = ref("agent_loop/v1", "9")
        elif other_producer == "later_turn":
            other["record"]["turn_sequence"] = records[1]["record"]["turn_sequence"]
        if other_producer != "unlinked_turn":
            records[2]["record"]["prior_turn_refs"].append(other["record"]["agent_turn_ref"])
        records.append(other)
    elif other_producer == "different_source":
        other = deepcopy(source)
        other["ref"] = ref("agent_action/v3", "9")
        other["record"]["tool_call_id"] = reader["record"]["tool_call_id"]
        records.append(other)
    else:
        source["record"]["tool_call_id"] = reader["record"]["tool_call_id"]
        page = bounded_managed_output_projection(
            {"kind": "managed_native_plugin_result/v1", "output": source["record"]["output"],
             "terminal_receipt_ref": source["record"]["terminal_receipt_ref"]},
            {"agent_action_ref": source["ref"], "terminal_receipt_ref": source["record"]["terminal_receipt_ref"],
             "offset_chars": 2000, "max_bytes": 2000})
        reader["record"]["result_metadata"] = page
        body = json.loads(envelope["messages"][1]["content"])
        body["offset_chars"] = 2000
        if projection == "bounded":
            body["content"] = page["content"][:len(body["content"])]
            body["next_offset_chars"] = 2000 + len(body["content"])
        envelope["messages"][1]["content"] = canonical_json(body).decode()
    view = project(records, events, envelope)["actions"][0]["requests"][0]
    assert view["request_projection"] == projection
