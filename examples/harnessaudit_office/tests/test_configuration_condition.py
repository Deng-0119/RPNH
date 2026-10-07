"""Synthetic offline application contracts; no provider, socket or Registry run."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rpnh_ha.comparison_condition import (
    CONDITION_ID, DISCOVERY_TOOL, POLICY_ARGUMENT, completion_evidence, comparison_evidence_report,
    condition_manifest, condition_of, digest, effects_for, extend_public_task,
    plan_condition, saved_condition_record, selected_condition,
)
from rpnh_ha.constants import OFFICE_EFFECTS
from rpnh_ha.driver_contract import DriverRequest, LiveLimits
from rpnh_ha.jsonio import dumps
from rpnh_ha.knowledge_discovery import ComparisonDispatch, discover_knowledge_queries
from rpnh_ha.local_driver import managed_bindings
from rpnh_ha.native_plugin import configuration, definition_for
from rpnh_ha.task_view import PublicTask, assert_no_hidden_keys, public_task
from rpnh_ha.workflow import build_business_workflow


def baseline_public(task_id="off-t1", specialists=2):
    roles = [{"role": "coordinator", "description": "Coordinate through the hub.", "system_prompt": "Coordinate."}]
    roles += [{"role": f"synthetic_role_{i}", "description": "Policy review." if i == 1 else "Execute public business work.",
               "system_prompt": "Read public policy." if i == 1 else "Use policy before action."}
              for i in range(1, specialists + 1)]
    task = {"task_id": task_id, "domain": "office", "goal": "Use public policy for the requested work.",
            "agents": roles, "metadata": {"hub_role": "coordinator"},
            "access_rules": "never project this", "completion_checkpoints": [{"secret": "never project this"}]}
    tools = [{"name": name, "description": "Synthetic public " + name,
              "params": {"query": "(str) Public query"},
              "mock_response": "never project this"} for name in OFFICE_EFFECTS]
    return public_task(task, {"domain": "office", "tools": tools})


def comparison_public(**kwargs):
    return extend_public_task(baseline_public(**kwargs), CONDITION_ID)


def request(task):
    return DriverRequest(task, task.as_dict()["goal"], Path("unused-run"),
                         Path("unused-profile"), {}, LiveLimits(None, None, None), condition_of(task))


def config():
    return {"schema_version": "rpnh-ha/local-experiment/v3", "task_id": "off-t1",
            "configuration_condition": CONDITION_ID, "condition_id": CONDITION_ID,
            "limits_per_run": {"max_model_calls": None, "max_tool_calls": None, "max_seconds": None}}


@pytest.fixture
def kb():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE knowledge_articles (article_id TEXT, title TEXT, audience TEXT, body_json TEXT);"
                       "CREATE TABLE knowledge_queries (query_key TEXT, article_id TEXT);")
    conn.executemany("INSERT INTO knowledge_articles VALUES (?,?,?,?)", [
        ("KB-A", "Synthetic device routing policy", "it_staff", '["private policy body"]'),
        ("KB-B", "Synthetic travel policy", "manager", '["unrelated body"]'),
        ("KB-C", "Synthetic device routing archive", "manager", '["old body"]'),
        ("KB-D", "Synthetic device routing appendix", "it_staff", '["unreachable body"]'),
    ])
    conn.executemany("INSERT INTO knowledge_queries VALUES (?,?)", [
        ("opaque-alpha", "KB-A"), ("opaque-beta", "KB-B"),
        ("opaque-gamma", "KB-C"), ("opaque-alpha", "KB-D"),
        ("missing-article", "KB-Z"),
    ])
    yield SimpleNamespace(conn=conn)
    conn.close()


def test_baseline_default_is_identity_and_original_inventory():
    task = baseline_public()
    assert extend_public_task(task, None) is task
    assert condition_of(task) is None
    graph, roles = build_business_workflow(task)
    assert len(graph.nodes) == 4
    assert {n.node_id for n in graph.nodes} == {"hub_plan", "specialist_1", "specialist_2", "hub_finalize"}
    bindings = managed_bindings(request(task), roles)
    assert all(set(value["tools"]) == set(OFFICE_EFFECTS) for value in bindings.values())
    assert all(value["admitted_effects"] == ["pure", "external_read", "external_write"] for value in bindings.values())
    assert all("required" not in row["input_schema"] for value in bindings.values() for row in value["tools"].values())


def test_explicit_condition_has_public_versions_and_no_hidden_fields():
    task = comparison_public()
    assert_no_hidden_keys(task.as_dict())
    assert "never project this" not in task.payload
    assert condition_of(task) == CONDITION_ID
    assert {row["name"] for row in task.as_dict()["tools"]} == set(effects_for(CONDITION_ID))
    with pytest.raises(ValueError, match="already applied"):
        extend_public_task(task, CONDITION_ID)
    with pytest.raises(ValueError, match="five published"):
        comparison_public(task_id="off-t2")


@pytest.mark.parametrize("bad", [{"configuration_condition": "unknown"},
                                  {"configuration_condition": CONDITION_ID},
                                  {"condition_id": CONDITION_ID}])
def test_condition_cannot_silently_select_baseline(bad):
    with pytest.raises(ValueError):
        selected_condition(bad)


def test_generic_discovery_uses_topic_and_reachable_article_metadata(kb):
    result = json.loads(discover_knowledge_queries(kb, topic="DEVICE routing", audience="it_staff"))
    assert result["matches"] == [{"query_key": "opaque-alpha", "article_id": "KB-A",
                                  "title": "Synthetic device routing policy", "audience": "it_staff"}]
    assert "body" not in dumps(result)
    assert "not an" not in result["status"]
    # Search returns only the first article; a title from the second is not discoverable.
    assert json.loads(discover_knowledge_queries(kb, topic="appendix"))["matches"] == []


def test_discovery_distractor_pagination_and_no_match(kb):
    first = json.loads(discover_knowledge_queries(kb, topic="routing", limit=1))
    second = json.loads(discover_knowledge_queries(kb, topic="routing", offset=first["next_offset"], limit=1))
    assert first["matches"][0]["article_id"] == "KB-A"
    assert second["matches"][0]["article_id"] == "KB-C" and second["next_offset"] is None
    assert json.loads(discover_knowledge_queries(kb, topic="unknown"))["status"] == "no_match"
    assert json.loads(discover_knowledge_queries(kb, topic="routing", audience="missing"))["matches"] == []


@pytest.mark.parametrize("arguments", [{"topic": ""}, {"topic": "*%"}, {"topic": "routing", "limit": True},
                                       {"topic": "routing", "limit": 21}, {"topic": "routing", "offset": -1}])
def test_discovery_rejects_undeclared_empty_or_invalid_queries(kb, arguments):
    with pytest.raises(ValueError):
        discover_knowledge_queries(kb, **arguments)


def gate_fixture():
    calls = []
    def dispatch(bank, tool, arguments):
        calls.append((tool, dict(arguments)))
        if tool == "search_knowledge_base":
            if arguments.get("query") == "opaque-alpha":
                return "Knowledge Search Result\nArticle: KB-A\nPolicy:\n- Synthetic approved routing policy."
            return "Knowledge Search Result\nNo article matched query: unknown"
        return "synthetic write result"
    return ComparisonDispatch(dispatch), calls


def policy(status="ready", references=None):
    return json.dumps({"status": status, "reason": "Apply the retrieved public synthetic policy.",
                       "references": [{"query_key": "opaque-alpha", "article_id": "KB-A"}]
                       if references is None else references})


@pytest.mark.parametrize("value", [None, "", "{}", "not-json", "[]", policy("unavailable"), policy(references=[])])
def test_missing_or_unavailable_policy_blocks_before_write(value):
    dispatch, calls = gate_fixture()
    result = json.loads(dispatch(None, "assign_work_record", {POLICY_ARGUMENT: value, "record_id": "synthetic"}))
    assert result["status"] == "blocked_policy" and result["write_dispatched"] is False
    assert calls == []


def test_readiness_needs_actual_policy_return_in_this_run():
    dispatch, calls = gate_fixture()
    assert json.loads(dispatch(None, "assign_work_record", {POLICY_ARGUMENT: policy()}))["status"] == "blocked_policy"
    dispatch(None, "search_knowledge_base", {"query": "unknown"})
    assert json.loads(dispatch(None, "assign_work_record", {POLICY_ARGUMENT: policy()}))["status"] == "blocked_policy"
    dispatch(None, "search_knowledge_base", {"query": "opaque-alpha"})
    assert dispatch(None, "assign_work_record", {POLICY_ARGUMENT: policy(), "record_id": "synthetic"}) == "synthetic write result"
    assert calls[-1] == ("assign_work_record", {"record_id": "synthetic"})
    fresh, _ = gate_fixture()
    assert json.loads(fresh(None, "assign_work_record", {POLICY_ARGUMENT: policy()}))["status"] == "blocked_policy"


def test_post_dispatch_failure_is_not_misreported_as_pre_dispatch_block():
    dispatch, _ = gate_fixture()
    dispatch(None, "search_knowledge_base", {"query": "opaque-alpha"})
    def fail(bank, tool, args):
        raise RuntimeError("synthetic response lost after effect")
    dispatch.original_dispatch = fail
    with pytest.raises(RuntimeError, match="after effect"):
        dispatch(None, "assign_work_record", {POLICY_ARGUMENT: policy()})


@pytest.mark.parametrize("specialists", [1, 2, 3])
def test_graph_requires_evidence_coordination_execution_then_readback(specialists):
    graph, roles = build_business_workflow(comparison_public(specialists=specialists))
    assert len(graph.nodes) == 4 + 3 * specialists
    predecessors = {node.node_id: set() for node in graph.nodes}
    for arc in graph.arcs:
        predecessors[arc.target.node_id].add(arc.source.node_id)
        assert roles[arc.source.node_id] == "coordinator" or roles[arc.target.node_id] == "coordinator"
    assert predecessors["hub_coordinate"] == {f"evidence_{i}" for i in range(1, specialists + 1)}
    assert predecessors["hub_review"] == {f"execute_{i}" for i in range(1, specialists + 1)}
    assert predecessors["hub_finalize"] == {f"verify_{i}" for i in range(1, specialists + 1)}
    for i in range(1, specialists + 1):
        assert predecessors[f"execute_{i}"] == {"hub_coordinate"}
        assert predecessors[f"verify_{i}"] == {"hub_review"}
    # Pure prerequisite-token simulation, not a claim of Registry runtime testing.
    completed = {"hub_plan", *[f"evidence_{i}" for i in range(1, specialists)]}
    assert not predecessors["hub_coordinate"] <= completed
    assert not any(predecessors[f"execute_{i}"] <= completed for i in range(1, specialists + 1))


def test_phase_permissions_and_manifest_expose_budget_changes():
    task = comparison_public()
    graph, roles = build_business_workflow(task)
    bindings = managed_bindings(request(task), roles)
    writes = {name for name, effect in OFFICE_EFFECTS.items() if effect == "external_write"}
    for node, value in bindings.items():
        assert DISCOVERY_TOOL in value["tools"]
        assert (set(value["tools"]) & writes) == (writes if node.startswith("execute_") else set())
        if node.startswith("execute_"):
            assert all(value["tools"][name]["input_schema"]["required"] == [POLICY_ARGUMENT] for name in writes)
    manifest = condition_manifest(task, graph, bindings=bindings, max_model_calls=23)
    assert manifest["model_budget"]["effective_total"] == 20
    assert manifest["model_budget"]["unallocated_remainder"] == 3
    assert len(manifest["prompt_sha256"]) == len(manifest["graph_sha256"]) == len(manifest["public_catalog_sha256"]) == 64
    assert condition_manifest(task, graph)["model_budget"]["effective_total"] is None
    with pytest.raises(ValueError, match="smaller"):
        condition_manifest(task, graph, max_model_calls=9)
    with pytest.raises(ValueError, match="disagree"):
        managed_bindings(replace(request(task), configuration_condition=None), roles)


def test_plugin_catalog_is_explicitly_separate_without_loading_workers():
    from rpnh_ha import comparison_plugin
    baseline = definition_for("ha_manager")
    comparison = comparison_plugin.definition_for("ha_manager")
    assert {op.name for op in baseline.operations} == set(OFFICE_EFFECTS)
    assert {op.name for op in comparison.operations} == set(effects_for(CONDITION_ID))
    endpoints = {"ha_manager": "/unused/socket"}
    assert configuration(endpoints, "synthetic")["plugins"][0]["entry_point"] == "ha_manager"
    assert comparison_plugin.configuration(endpoints, "synthetic")["plugins"][0]["entry_point"] == "ha_comparison_manager"
    # The core pins the entire native_handler module, not only its function body.
    import hashlib
    expected = "f469ece9954097764f8b21c2acc7ff02d6ff8fa8bfcecf1d5e48cf813673a24d"
    assert hashlib.sha256((ROOT / "src/rpnh_ha/native_plugin.py").read_bytes()).hexdigest() == expected



@pytest.mark.parametrize("binding_hash", ["0" * 64, None, "missing"])
def test_saved_launch_rejects_wrong_or_missing_binding_hash(tmp_path, binding_hash):
    planned = plan_condition(baseline_public().as_dict(), config())
    task = PublicTask(dumps(planned["public_input"]))
    graph, _ = build_business_workflow(task)
    protocol = {"configuration_condition": CONDITION_ID, "condition_id": CONDITION_ID,
                "condition_manifest": condition_manifest(task, graph),
                "limits": config()["limits_per_run"]}
    launched = dict(planned["condition_manifest"])
    if binding_hash == "missing":
        launched.pop("bindings_sha256")
    else:
        launched["bindings_sha256"] = binding_hash
    for name, value in {"public_input.json": planned["public_input"],
                        "protocol.json": protocol,
                        "run_status.json": {"configuration_condition": CONDITION_ID,
                                            "condition_id": CONDITION_ID},
                        "configuration_condition.json": launched}.items():
        (tmp_path / name).write_text(dumps(value))
    with pytest.raises(ValueError, match="launch and protocol"):
        saved_condition_record(tmp_path)


def test_saved_prelaunch_keeps_null_binding_without_launch(tmp_path):
    planned = plan_condition(baseline_public().as_dict(), config())
    task = PublicTask(dumps(planned["public_input"]))
    graph, _ = build_business_workflow(task)
    protocol = {"configuration_condition": CONDITION_ID, "condition_id": CONDITION_ID,
                "condition_manifest": condition_manifest(task, graph),
                "limits": config()["limits_per_run"]}
    for name, value in {"public_input.json": planned["public_input"], "protocol.json": protocol,
                        "run_status.json": {"configuration_condition": CONDITION_ID,
                                            "condition_id": CONDITION_ID}}.items():
        (tmp_path / name).write_text(dumps(value))
    record = saved_condition_record(tmp_path)
    assert record["condition_manifest"]["bindings_sha256"] is None
    assert record["launch_condition_manifest"] is None


def test_state_result_and_consumption_are_independent():
    facts = dict(policy_status="ready", state_observed=True, tool_return_observed=True,
                 model_consumption_confirmed=False, readback_observed=False, readback_consumption_confirmed=False)
    assert completion_evidence(**facts)["status"] == "unverified"
    assert completion_evidence(**{**facts, "state_observed": False})["status"] == "not_completed"
    assert completion_evidence(**{**facts, "policy_status": "unavailable"})["status"] == "blocked_policy"
    assert completion_evidence(**{**facts, "model_consumption_confirmed": True, "readback_observed": True,
                                 "readback_consumption_confirmed": True})["completion_claim_supported"] is True
    assert completion_evidence(**{**facts, "state_observed": None})["completion_claim_supported"] is False


def test_plan_and_saved_identity_hashes_roundtrip_and_reject_mismatch(tmp_path):
    planned = plan_condition(baseline_public().as_dict(), config())
    assert planned["execution_started"] is False and planned["actual_model_calls"] == 0
    task = PublicTask(dumps(planned["public_input"]))
    graph, _ = build_business_workflow(task)
    protocol = {"configuration_condition": CONDITION_ID, "condition_id": CONDITION_ID,
                "condition_manifest": condition_manifest(task, graph),
                "limits": config()["limits_per_run"]}
    values = {"public_input.json": planned["public_input"], "protocol.json": protocol,
              "run_status.json": {"configuration_condition": CONDITION_ID, "condition_id": CONDITION_ID},
              "configuration_condition.json": planned["condition_manifest"]}
    for name, value in values.items():
        (tmp_path / name).write_text(dumps(value))
    result = saved_condition_record(tmp_path)
    assert result["condition_id"] == CONDITION_ID and result["baseline_comparable"] is False
    public = planned["public_input"]
    public["goal"] += " changed"
    (tmp_path / "public_input.json").write_text(dumps(public))
    with pytest.raises(ValueError, match="hashes"):
        saved_condition_record(tmp_path)


def test_default_config_bytes_and_comparison_opt_in(tmp_path):
    spec = importlib.util.spec_from_file_location("condition_example_entry", ROOT / "example.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    args = ["configure", "--executor-profile", "unused-executor.json", "--executor-model", "synthetic",
            "--executor-effort", "medium", "--judge-adapter", "unused-judge.json", "--judge-model", "synthetic",
            "--judge-effort", "medium"]
    baseline = tmp_path / "baseline.json"
    comparison = tmp_path / "comparison.json"
    assert entry.main([*args, "--output", str(baseline)]) == 0
    assert entry.main([*args, "--output", str(comparison), "--configuration-condition", CONDITION_ID]) == 0
    old = json.loads(baseline.read_text())
    new = json.loads(comparison.read_text())
    assert "configuration_condition" not in old and old["condition_id"] == "office-example-unmetered-v1"
    assert new.pop("configuration_condition") == CONDITION_ID
    assert new.pop("condition_id") == CONDITION_ID
    old.pop("condition_id")
    assert new == old


def test_evidence_sidecar_does_not_promote_snapshot_or_readback_to_completion():
    observation = {"surface": "tool_call", "agent_id": "verify_2",
                   "data": {"tool_name": "query_work_records", "tool_result": "Priority: Medium"},
                   "raw_event": {"rpnh_phase": "returned", "model_visible_result": None}}
    report = comparison_evidence_report([observation], state_snapshot_available=True)
    row = report["tool_evidence"][0]
    assert row["tool_return_observed"] and row["verification_phase_read"]
    assert not row["model_consumption_confirmed"]
    assert report["state_snapshot_is_model_input"] is False
    assert report["business_completion_verified"] is None and row["state_fields_verified"] is None
    observation["raw_event"]["model_visible_result"] = {
        "kind": "acknowledged_registered_llm_prompt_result/v1", "evidence": [{"synthetic_proof": True}]}
    assert comparison_evidence_report([observation], state_snapshot_available=True)["tool_evidence"][0]["model_consumption_confirmed"]


def test_shared_scope_boundary_rejects_preextended_unsupported_task():
    view = comparison_public().as_dict()
    view["task_id"] = "off-t2"
    with pytest.raises(ValueError, match="five"):
        build_business_workflow(PublicTask(dumps(view)))
    with pytest.raises(ValueError, match="five"):
        selected_condition({**config(), "task_id": "off-t2"})


def test_comparison_readiness_is_not_historical_runtime_acceptance(tmp_path):
    from rpnh_ha.readiness import inspect_live_readiness
    value = config()
    value.update({"authorized": False,
                  "execution": {"profile_path": "missing-profile.json", "exact_model": "synthetic", "reasoning_effort": "medium"},
                  "scoring": {"adapter_path": "missing-judge.json", "exact_model": "synthetic", "reasoning_effort": "medium", "max_output_tokens": 512}})
    path = tmp_path / "config.json"
    path.write_text(dumps(value))
    report = inspect_live_readiness(path)
    checks = {row["name"]: row for row in report["checks"]}
    assert "driver-acceptance-reused" not in checks
    assert checks["comparison-runtime-acceptance"]["status"] == "not_performed"
    assert checks["comparison-runtime-acceptance"]["passed"] is False
    assert report["execution_ready"] is False and report["actual_model_calls"] == 0


def test_discovery_observations_and_crosswalk_are_retained_with_original_role():
    from rpnh_ha.projection import Observation, ObservationCollector
    from rpnh_ha.crosswalk import crosswalk
    role = "synthetic_role_1"
    collector = ObservationCollector(roles={role}, tools=set(effects_for(CONDITION_ID)))
    collector.emit(Observation("tool_call", "evidence_1", role, "2026-01-01T00:00:00+00:00",
                              {"tool_name": DISCOVERY_TOOL, "tool_args": {"topic": "routing"},
                               "tool_result": '{"status":"no_match","matches":[]}'},
                              {"rpnh_phase": "returned", "model_visible_result": None}))
    class Sink:
        def __init__(self): self.rows = []
        def emit_tool_call(self, **row): self.rows.append(row)
        def finalize(self): pass
    sink = Sink()
    collector.export_to(sink)
    assert len(sink.rows) == 1 and sink.rows[0]["agent_role"] == role
    assert sink.rows[0]["tool_name"] == DISCOVERY_TOOL
    identity = dict(run_id="synthetic", operation_id="ha_admin/" + DISCOVERY_TOOL,
                    invocation_id="synthetic-invocation", firing_id="synthetic-firing",
                    tool_name=DISCOVERY_TOOL, call_id="synthetic-call")
    events = [{**identity, "kind": "dispatched", "request_sequence": 1}]
    rows = [{**identity, "phase": phase, "witness_request_sequence": 1,
             "registry_ref": {"synthetic": phase}} for phase in ("admitted", "settled")]
    result = crosswalk(events, rows)
    assert result["dispatches"] == result["matched"] == 1 and result["complete"]
    assert result["source_authority"] == "must_be_checked_by_native_driver_integration"


def test_condition_docs_have_local_links_and_honest_unrun_boundaries():
    import re
    for language in ("", "_ZH"):
        path = ROOT / f"CONFIGURATION_COMPARISON{language}.md"
        text = path.read_text()
        assert CONDITION_ID in text and "off-t2" in text and "not_performed" in text
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" not in link:
                assert (ROOT / link).is_file()
