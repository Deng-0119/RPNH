"""Bounded declaration and SCRIPTED request/response tests; no native task run."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module
from examples.user_collaboration.agent_cobuild.contracts import (
    CONTRACTS, CONTRIBUTIONS, MODEL_CONDITION, TOOLS,
    contribution_document, graph, module,
)
from examples.user_collaboration.agent_cobuild.profile import write_profile
from examples.user_collaboration.agent_cobuild import scripted_model


def _component(document, role):
    return next(c for c in document["components"] if c["name"] == role)


def _operation(document, role):
    return next(o for o in _component(document, role)["operations"] if o["name"] == "run")


def _request(role, *, contributed=False, body=None):
    document = module().to_dict()
    if contributed:
        document = contribution_document(document, role)
    port = f"{role}.result"
    request = {
        "protocol": "llm_request_envelope/v1", "model_condition": MODEL_CONDITION,
        "messages": [
            {"role": "system", "content": _operation(document, role)["config"]["node_synopsis"]},
            {"role": "system", "content": (
                'Located inputs:\n- {"sandbox_path":"inputs/actual.json",'
                '"semantic_name":"port_1",'
                '"content_schema_ref":"application/rpnh_agent_text/v1",'
                '"summary":"Actual registered input"}\n'
                f"Provider-writable semantic output_port_ids (exact): {port}")},
            # Even a complete-looking inline input must never substitute for a read.
            {"role": "user", "content": json.dumps({"records": [
                {"id": "decoy", "category": "decoy", "minutes": 99999}]})},
        ],
    }
    if body is not None:
        _return_read(request, body)
    return request


def _return_read(request, body):
    first = scripted_model.response(request)
    assert [c["name"] for c in first["tool_calls"]] == ["read_file"]
    call = first["tool_calls"][0]
    request["messages"].extend([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": call["arguments"]}}]},
        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({
            "kind": "registered_file_read/v1", "path": "inputs/actual.json",
            "content": json.dumps(body)})},
    ])


def _output(role, body, *, contributed=False):
    response = scripted_model.response(_request(role, contributed=contributed, body=body))
    assert [c["name"] for c in response["tool_calls"]] == ["write_file", "complete_interaction"]
    args = json.loads(response["tool_calls"][0]["arguments"])
    assert args["outcome_id"] == "complete"
    assert args["output_port_id"] == f"{role}.result"
    return json.loads(args["content"])


@pytest.fixture
def records():
    return json.loads(Path(__file__).parents[1].joinpath("fixtures/input.json").read_text())


def test_exact_contributions_survive_without_touching_other_fields():
    baseline = module().to_dict()
    frozen = deepcopy(baseline)
    first = contribution_document(baseline, "curator")
    final = contribution_document(first, "auditor")
    assert baseline == frozen
    assert _component(first, "auditor") == _component(baseline, "auditor")
    assert _component(final, "curator") == _component(first, "curator")
    for before, after, role in [(baseline, first, "curator"), (first, final, "auditor")]:
        instruction = _operation(after, role)["config"]["node_synopsis"]
        assert scripted_model._contract([instruction]) == CONTRIBUTIONS[role]
        assert instruction != _operation(before, role)["config"]["node_synopsis"]
        reverted = deepcopy(after)
        old_op = _operation(before, role)
        new_op = _operation(reverted, role)
        assert new_op["config"]["node_synopsis"] == instruction
        new_op["config"]["node_synopsis"] = old_op["config"]["node_synopsis"]
        assert reverted == before
    assert contribution_document(contribution_document(baseline, "auditor"), "curator") == final
    assert not final.get("designer_constraints")
    assert final["links"] == [{
        "source": {"component": "curator", "port": "result"},
        "target": {"component": "auditor", "port": "request"}}]
    assert final["entry"] == {"request": {"component": "curator", "port": "request"}}
    assert final["exit"] == {"result": {"component": "auditor", "port": "result"}}
    assert final["terminal"]["operation"] == "run"
    assert final["terminal"]["source"] == final["exit"]["result"]
    for role in ("curator", "auditor"):
        op = _operation(final, role)
        assert op["tools"] == list(TOOLS)
        assert op["executor"] == "rpnh/default-agent/v1"
        assert op["config"]["resource_bounds"]["max_llm_attempts"] == 4
        assert op["budget_binding"]["budget_scope"] == role


def test_components_are_stock_single_agents_with_only_tools_and_scope_narrowed():
    document = module().to_dict()
    for node in graph().nodes:
        stock = build_agent_task_module(
            (AgentStage(node.node_id, node.instruction),), max_attempts_per_stage=4).to_dict()
        component = deepcopy(_component(document, node.node_id))
        component["operations"][0]["tools"] = stock["components"][0]["operations"][0]["tools"]
        component["operations"][0]["budget_binding"]["budget_scope"] = "module"
        assert component == stock["components"][0]
        assert document["required_schemas"] == stock["required_schemas"]
        bucket = next(b for b in document["budget_buckets"] if b["bucket_id"] == node.node_id)
        assert bucket == {**stock["budget_buckets"][0], "budget_scope": node.node_id}


def test_baseline_and_both_contributions_execute(records):
    handoff = _output("curator", records)
    baseline = _output("auditor", handoff)
    assert baseline["totals"] == {
        "record_count": 4, "total_minutes": 24,
        "by_category": [{"category": "planning", "record_count": 2, "total_minutes": 17},
                        {"category": "review", "record_count": 2, "total_minutes": 7}],
    }
    assert not baseline["lineage_retained"]
    assert "source_reconciliation" not in baseline
    lineage = _output("curator", records, contributed=True)
    report = _output("auditor", lineage, contributed=True)
    assert report["totals"] == baseline["totals"]
    assert report["lineage_retained"] and report["source_reconciliation_required"]
    assert report["source_reconciliation"] == [
        {"source_id": row["id"], "source_index": index,
         "category": row["category"], "minutes": row["minutes"]}
        for index, row in enumerate(records["records"])]
    assert report["count_checks"] == {
        "input_record_count": 4, "normalized_record_count": 4,
        "reconciled_source_count": 4, "reported_record_count": 4, "counts_match": True}
    assert all(report["checks"].values())
    with pytest.raises(ValueError, match="requires curator source lineage"):
        _output("auditor", handoff, contributed=True)


def test_changed_input_changes_computation_and_lineage(records, monkeypatch):
    original = _output("curator", records, contributed=True)
    changed = deepcopy(records)
    changed["records"][0].update(id="changed-source", category="new-category", minutes=19)
    request = _request("curator", contributed=True, body=changed)
    def no_open(*args, **kwargs):
        raise AssertionError("driver must not open input files")
    monkeypatch.setattr("builtins.open", no_open)
    output = scripted_model.response(request)
    handoff = json.loads(json.loads(output["tool_calls"][0]["arguments"])["content"])
    assert handoff != original
    assert handoff["records"][0]["source_id"] == "changed-source"
    report = _output("auditor", handoff, contributed=True)
    assert report["totals"]["total_minutes"] == 31
    assert report["source_reconciliation"][0]["category"] == "new-category"


@pytest.mark.parametrize("bad", [
    {}, {"records": "bad"}, {"records": [None]},
    {"records": [{"id": "", "category": "x", "minutes": 0}]},
    {"records": [{"id": "a", "category": " ", "minutes": 0}]},
    {"records": [{"id": "a", "category": "x", "minutes": 0}] * 2},
] + [{"records": [{"id": "a", "category": "x", "minutes": n}]}
     for n in (-1, True, 1.5, "3", None)])
def test_invalid_input_never_produces_success(bad):
    with pytest.raises(ValueError):
        _output("curator", bad, contributed=True)


def test_empty_valid_input_reconciles():
    handoff = _output("curator", {"records": []}, contributed=True)
    report = _output("auditor", handoff, contributed=True)
    assert report["totals"] == {"record_count": 0, "total_minutes": 0, "by_category": []}
    assert report["source_reconciliation"] == []
    assert report["count_checks"]["reconciled_source_count"] == 0


@pytest.mark.parametrize("workspace_summary", [
    "Workspace file outputs/curator.json", "Registered operation product",
])
def test_actual_upstream_metadata_wins_over_inherited_workspace(records, workspace_summary):
    request = _request("auditor", contributed=True)
    # Actual run-02 metadata shapes, with resource IDs/paths replaced by test values.
    workspace = {
        "content_schema_ref": None, "file_name": "curator.json",
        "head_preview": "", "head_truncated": False, "media_type": "application/json",
        "resource_ref": {"resource_id": "resource:workspace", "resource_version_id": "version:workspace"},
        "sandbox_path": "registered_resources/workspace/version/content",
        "semantic_name": "workspace_file", "source_relative_path": "outputs/curator.json",
        "summary": workspace_summary, "summary_truncated": False,
    }
    semantic = {
        "content_schema_ref": "application/rpnh_agent_text/v1", "file_name": "content",
        "head_preview": "", "head_truncated": False, "media_type": "application/json",
        "resource_ref": {"resource_id": "resource:product", "resource_version_id": "version:product"},
        "sandbox_path": "inputs/actual.json", "semantic_name": "port_1",
        "source_relative_path": None,
        "summary": "SCRIPTED curator output computed from registered read_file evidence.",
        "summary_truncated": False,
    }
    request["messages"][1]["content"] = (
        "Located inputs:\n- " + json.dumps(workspace) + "\n- " + json.dumps(semantic)
        + "\nProvider-writable semantic output_port_ids (exact): auditor.result")
    first = scripted_model.response(request)
    assert json.loads(first["tool_calls"][0]["arguments"]) == {"path": "inputs/actual.json"}
    handoff = _output("curator", records, contributed=True)
    _return_read(request, handoff)
    result = scripted_model.response(request)
    report = json.loads(json.loads(result["tool_calls"][0]["arguments"])["content"])
    assert report["totals"]["total_minutes"] == 24
    # A return for the workspace copy is not evidence for the selected semantic port.
    read = json.loads(request["messages"][-1]["content"])
    read["path"] = workspace["sandbox_path"]
    request["messages"][-1]["content"] = json.dumps(read)
    with pytest.raises(ValueError, match="input read failed"):
        scripted_model.response(request)


def test_original_source_without_text_schema_supported_but_workspace_alone_rejected():
    source = {"sandbox_path": "inputs/source.json", "semantic_name": "request",
              "content_schema_ref": None, "summary": "Original dataset"}
    assert scripted_model._input_path(["- " + json.dumps(source)]) == "inputs/source.json"
    source["semantic_name"] = "workspace_file"
    source["summary"] = "Registered operation product"
    with pytest.raises(ValueError, match="one original located input"):
        scripted_model._input_path(["- " + json.dumps(source)])


@pytest.mark.parametrize("fault", ["missing", "failed", "wrong_id", "wrong_path", "bad_json"])
def test_unread_or_failed_input_never_produces_success(records, fault):
    request = _request("curator", body=records)
    if fault == "missing":
        request["messages"].pop()
    elif fault == "failed":
        request["messages"][-1]["content"] = json.dumps({"error": "read failed"})
    elif fault == "wrong_id":
        request["messages"][-1]["tool_call_id"] = "unrelated"
    else:
        result = json.loads(request["messages"][-1]["content"])
        result["path" if fault == "wrong_path" else "content"] = "invalid"
        request["messages"][-1]["content"] = json.dumps(result)
    with pytest.raises(ValueError):
        scripted_model.response(request)


@pytest.mark.parametrize("fault", ["count", "total", "missing_id", "duplicate_id", "index"])
def test_auditor_rejects_inconsistent_handoff(records, fault):
    handoff = _output("curator", records, contributed=True)
    if fault == "count":
        handoff["input_record_count"] += 1
    elif fault == "total":
        handoff["totals"]["total_minutes"] += 1
    elif fault == "missing_id":
        del handoff["records"][0]["source_id"]
    elif fault == "duplicate_id":
        handoff["records"][1]["source_id"] = handoff["records"][0]["source_id"]
    else:
        handoff["records"][1]["source_index"] = 0
    with pytest.raises(ValueError):
        _output("auditor", handoff, contributed=True)


def test_contract_comes_only_from_actual_system_instruction(records):
    request = _request("curator", body=records)
    request["messages"][0]["role"] = "user"
    with pytest.raises(ValueError, match="system instruction contract"):
        scripted_model.response(request)
    instruction = _operation(module().to_dict(), "curator")["config"]["node_synopsis"]
    assert scripted_model._contract([instruction]) == CONTRACTS["curator"]


def test_profile_and_stdio_protocol(tmp_path, records):
    execution_path = write_profile(tmp_path / "profile")
    selection = load_llm_execution_selection(execution_path)
    assert selection.input_target.model_condition == MODEL_CONDITION
    adapter = json.loads(execution_path.with_name("adapter.json").read_text())
    assert adapter["adapter_kind"] == "local_process"
    assert adapter["env"] == {"PYTHONDONTWRITEBYTECODE": "1"}
    assert adapter["inherit_env"] == []
    assert adapter["argv"][0] == "{python}"
    # Execute the configured stdio driver, leaving native AgentLoop execution to HOST.
    argv = [sys.executable, adapter["argv"][1]]
    for request in [_request("curator"), _request("curator", contributed=True, body=records)]:
        completed = subprocess.run(argv, input=json.dumps(request), text=True,
                                   capture_output=True, cwd=tmp_path, env=adapter["env"], check=True)
        assert json.loads(completed.stdout) == scripted_model.response(request)
        assert completed.stderr == ""
    bad = _request("curator", body={"records": "invalid"})
    completed = subprocess.run(argv, input=json.dumps(bad), text=True,
                               capture_output=True, cwd=tmp_path, env=adapter["env"])
    assert completed.returncode != 0 and completed.stdout == ""
    assert "records must be an array" in completed.stderr
