"""Synthetic offline component coverage; no providers, sockets or Registry runtime."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from rpnh_ab import configuration_condition as cc, experiment, io, run_spec
from rpnh_ab.broker import Broker
from rpnh_ab.constants import UPSTREAM_COMMIT
from rpnh_ab.evidence import crosscheck, records
from rpnh_ab.exporting import export_return
from rpnh_ab.native import graph_for, NATIVE_INSTRUCTION


SECRET = "synthetic-private-input-never-echo"


def endpoint(identifier, method="GET", **kw):
    return {"id": identifier, "method": method, "url": "https://synthetic.example/unchanged",
            "description": "Original description", "request": "Original request", **kw}


def search_result():
    return json.dumps({"results": [
        endpoint("unknown.endpoint"),
        endpoint("salesforce.sobjects.account.update", "PATCH"),
        endpoint("sheets.spreadsheets.get", parameters={
            "includeGridData": {"type": "boolean", "required": False, "location": "query", "description": "grid"},
            "ranges": {"type": "array", "items": {"type": "string"}, "description": "range"},
        }),
        endpoint("salesforce.sobjects.account.create", "POST"),
        endpoint("sheets.spreadsheets.values.get"),
    ], "count": 5, "extra": "untouched"}, indent=2)


def upstream(tmp_path, *, enabled=True):
    catalog = tmp_path / "catalog" / "automationbench/tools/api/schemas"
    catalog.mkdir(parents=True, exist_ok=True)
    for name in ("salesforce", "google_sheets", "unrelated"):
        (catalog / (name + ".jsonc")).write_text('{"synthetic":true}\n')
    up = SimpleNamespace(root=catalog.parents[3], identity={"commit": UPSTREAM_COMMIT, "tracked_changes": ""},
                         schemas=[], calls=[])
    def dispatch(state, tool, arguments):
        up.calls.append((tool, copy.deepcopy(arguments)))
        if tool == "api_search":
            return search_result()
        state["mutations"] = state.get("mutations", 0) + 1
        if arguments.get("body") == '{"raise_after_write":true}':
            raise ValueError(SECRET)
        return '{"ok":true}'
    up.dispatch = dispatch
    up.dump_world = copy.deepcopy
    cc.configure(up, cc.CONDITION if enabled else cc.BASELINE)
    return up


def request(tool="api_fetch", call="one", **arguments):
    return {"run_id": "synthetic", "tool": tool, "operation_id": "synthetic",
            "invocation_id": "synthetic", "firing_id": "synthetic", "call_id": call,
            "arguments": arguments}


def test_overlay_only_changes_returned_metadata_without_rank_or_search_changes():
    raw = search_result()
    visible, changed = cc.overlay_search(raw)
    original, value = json.loads(raw), json.loads(visible)
    assert changed == ["salesforce.sobjects.account.update", "sheets.spreadsheets.get", "sheets.spreadsheets.values.get"]
    assert value["count"] == original["count"] == 5
    assert value["extra"] == original["extra"]
    for before, after in zip(original["results"], value["results"]):
        for key in ("id", "method", "url"):
            assert before[key] == after[key]
    assert original["results"][0] == value["results"][0]
    assert original["results"][3] == value["results"][3]  # Account create untouched
    account = value["results"][1]
    assert "Optional[str]" in account["request"] and "no enforced enum" in account["request"]
    for field in ("HealthStatus", "Tier", "Priority"):
        assert field in account["request"]
    sheets = value["results"][2]
    assert "no grid cell data" in sheets["description"]
    assert "values/{range}" in sheets["description"]
    assert "first sheet" in sheets["description"]
    assert "data}" not in sheets["response"]
    assert sheets["parameters"]["includeGridData"]["type"] == "boolean"
    assert sheets["parameters"]["ranges"]["type"] == "array"
    assert search_result() == raw


@pytest.mark.parametrize("raw", ["not json", "[]", '{"results":[]}', '{"results":[{"id":"unknown"}]}'])
def test_unknown_or_unchanged_search_is_byte_preserved(raw):
    assert cc.overlay_search(raw) == (raw, [])


def test_baseline_is_exact_and_never_runs_preflight(tmp_path):
    up = upstream(tmp_path, enabled=False)
    broker = Broker(up, {}, tmp_path, "synthetic")
    assert broker.call(request("api_search", query="anything"))["result"] == search_result()
    assert broker.call(request(call="two", unknown=SECRET))["ok"] is True
    assert len(up.calls) == 2
    assert not (tmp_path / "api_search_metadata_events.jsonl").exists()
    assert all(e["kind"] != "pre_dispatch_rejected" for e in records(tmp_path / "tool_events.jsonl"))


def test_opt_in_search_preserves_raw_and_visible_evidence_and_dedup(tmp_path):
    up = upstream(tmp_path)
    state = {}
    broker = Broker(up, state, tmp_path, "synthetic")
    req = request("api_search", query="generic public fields")
    response = broker.call(req)
    assert broker.call(req) == response and len(up.calls) == 1 and state == {}
    evidence = records(tmp_path / "api_search_metadata_events.jsonl")
    assert len(evidence) == 1
    evidence = evidence[0]
    assert evidence["raw_upstream_result"] == search_result()
    assert evidence["actor_visible_result"] == response["result"] != search_result()
    assert evidence["raw_upstream_sha256"] == io.sha(search_result())
    assert evidence["actor_visible_sha256"] == io.sha(response["result"])
    assert evidence["configuration_condition"] == cc.selected(up)
    assert evidence["proves_subsequent_model_consumption"] is False


@pytest.mark.parametrize("args,field", [
    ({"url": "https://synthetic.example/"}, "method"),
    ({"method": "PATCH", "url": []}, "url"),
    ({"method": "PATCH", "url": "https://[malformed"}, "url"),
    ({"method": "PATCH", "url": "https://synthetic.example/", "world": SECRET}, "arguments"),
    ({"method": "GET", "url": "https://synthetic.example/", "params": SECRET}, "params"),
    ({"method": "GET", "url": "https://synthetic.example/", "params": '["' + SECRET + '"]'}, "params"),
    ({"method": "PATCH", "url": "https://fixture.salesforce.com/services/data/v61.0/sobjects/Account/random", "body": '"' + SECRET + '"'}, "body"),
])
def test_actionable_sanitized_preflight_never_dispatches(tmp_path, args, field):
    up = upstream(tmp_path)
    state = {}
    broker = Broker(up, state, tmp_path, "synthetic")
    req = request(**args)
    response = broker.call(req)
    assert response["ok"] is True
    error = json.loads(response["result"])["error"]
    assert error["field"] == field and error["message"]
    assert error["stage"] == "pre_dispatch" and error["upstream_dispatched"] is False
    assert error["effect_status"] == "not_started"
    assert SECRET not in response["result"]
    assert broker.call(req) == response
    assert not up.calls and state == {} and not (tmp_path / "world.latest.json").exists()
    events = records(tmp_path / "tool_events.jsonl")
    assert events[0]["kind"] == "pre_dispatch_rejected"
    assert not any(e["kind"].startswith("dispatch_") for e in events)
    assert events[0]["request"] == req
    # Correcting arguments under a new identity is a new operation, not replay.
    corrected = broker.call(request(call="fixed", method="GET", url="https://synthetic.example/"))
    assert corrected["ok"] is True and len(up.calls) == 1


@pytest.mark.parametrize("body", ['"label-id"', '[{"synthetic":"array endpoint"}]', 'SELECT * FROM Invoice', '{bad json'])
def test_does_not_invent_global_body_schema_or_override_upstream_text_routes(body):
    assert cc.pre_dispatch_error("api_fetch", {"method": "POST", "url": "https://synthetic.example/", "body": body}) is None
    assert cc.pre_dispatch_error("api_fetch", {"method": "POST", "url": "https://api.trello.com/1/cards/example/idLabels", "body": '"label-id"'}) is None


def test_null_and_optional_sentinels_retain_baseline_normalization():
    for value in (None, "null", {}, ""):
        assert cc.pre_dispatch_error("api_fetch", {"method": "GET", "url": "https://synthetic.example/", "params": value, "body": value}) is None


def test_post_dispatch_failure_is_unknown_never_reclassified_or_replayed(tmp_path):
    up = upstream(tmp_path)
    state = {}
    broker = Broker(up, state, tmp_path, "synthetic")
    req = request(method="PATCH", url="https://synthetic.example/", body='{"raise_after_write":true}')
    failure = broker.call(req)
    assert failure["ok"] is False and "error" in failure
    assert broker.call(req) == failure and len(up.calls) == 1
    assert state == {"mutations": 1} == io.load(tmp_path / "world.latest.json")
    assert not any(e["kind"] == "pre_dispatch_rejected" for e in records(tmp_path / "tool_events.jsonl"))
    assert "not_started" not in io.dumps(failure)


def test_registry_diagnostic_checks_actor_visible_return_without_claiming_consumption(tmp_path):
    up = upstream(tmp_path)
    broker = Broker(up, {}, tmp_path, "synthetic")
    req = request(method=None, url=SECRET)
    response = broker.call(req)
    row = {"object_type": "agent_action/v3", "version_id": "synthetic-only",
           "document": {"selector": "ab_api/api_fetch", "outcome": "returned", "arguments": req["arguments"],
                        "output": {"raw_result": response["result"], "witness_request_sequence": 1}}}
    report = crosscheck(tmp_path, registry_rows=[row])
    assert report["environment_returned"] == 0
    assert report["pre_dispatch_rejections_returned"] == 1
    assert report["matched_unique_sequences"] == 1 and report["mismatches"] == []
    assert report["proves_subsequent_model_consumption"] is False


def test_condition_identity_hashes_catalog_overlay_and_frozen_plan(tmp_path):
    up = upstream(tmp_path)
    selected = cc.selected(up)
    assert selected["id"] == cc.CONDITION
    assert selected["sha256"] == io.sha({k: v for k, v in selected.items() if k != "sha256"})
    assert selected["overlay_sha256"] == io.sha(cc.OVERLAY)
    plan = {"condition_id": "original", "split": "simple", "upstream_commit": UPSTREAM_COMMIT,
            "tasks": [{"task_contract_sha256": "a" * 64}], "manifest_sha256": "unchanged"}
    frozen = cc.freeze_plan(plan, selected)
    assert frozen["tasks"] == plan["tasks"] and frozen["manifest_sha256"] == plan["manifest_sha256"]
    assert cc.freeze_plan(plan, None) is plan and "configuration_condition" not in plan
    benchmark = run_spec.benchmark_spec(frozen, [], business_mode="offline_synthetic")
    baseline = run_spec.benchmark_spec(plan, [], business_mode="offline_synthetic")
    assert benchmark["configuration_condition"] == selected and "configuration_condition" not in baseline
    conditions = {"benchmark_spec": benchmark, "configuration_condition": selected,
                  "execution_spec": {"executor_host": "native"}}
    assert cc.restore_frozen(up, conditions) == selected
    changed = up.root / "automationbench/tools/api/schemas/unrelated.jsonc"
    changed.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="identity changed"):
        cc.restore_frozen(up, conditions)


def test_opt_in_rejects_unsupported_hosts_before_preparation_or_dispatch(tmp_path):
    up = upstream(tmp_path)
    with pytest.raises(ValueError, match="only the native host"):
        experiment.prepare(up, tmp_path / "work", tmp_path / "profile", executor_host="dsh", configuration_condition=cc.CONDITION)
    assert not (tmp_path / "work").exists() and not up.calls
    condition = cc.selected(up)
    with pytest.raises(ValueError, match="only the native host"):
        cc.restore_frozen(up, {"configuration_condition": condition,
                             "benchmark_spec": {"configuration_condition": condition},
                             "execution_spec": {"executor_host": "dsh"}})


def test_native_instruction_is_truthful_only_when_explicitly_opted_in():
    messages = [{"role": "system", "content": "PUBLIC RULES UNCHANGED"}, {"role": "user", "content": "PUBLIC REQUEST UNCHANGED"}]
    baseline, prompt = graph_for(messages)
    opted, new_prompt = graph_for(messages, configuration_condition={"id": cc.CONDITION})
    assert prompt == new_prompt == messages[1]["content"]
    # Workflow graph construction is declaration-only; no worker/Registry starts.
    assert baseline.nodes[0].instruction == messages[0]["content"] + "\n\n" + NATIVE_INSTRUCTION
    assert "actor-visible response text" in opted.nodes[0].instruction
    assert "exact upstream response text" not in opted.nodes[0].instruction


def test_launch_and_acceptance_bound_to_explicit_condition(tmp_path):
    up = upstream(tmp_path)
    condition = cc.selected(up)
    benchmark = {"configuration_condition": condition, "tool_schemas_sha256": "synthetic"}
    execution = {"executor_host": "native", "rpnh": {}, "example_sources": {}, "host_identity": {},
                 "limits": {}, "configured_model": {}}
    io.write_new(tmp_path / "conditions.json", {"benchmark_spec": benchmark, "execution_spec": execution})
    launch = run_spec.write_launch(tmp_path / "launch.json", work=tmp_path, upstream=up.root,
                                  profile=tmp_path / "unused", acceptance=tmp_path / "never-run")
    assert launch["configuration_condition"] == condition
    assert run_spec.load_launch(tmp_path / "launch.json", require_acceptance=False, validate_current=False) == launch
    identity = run_spec.acceptance_identity(benchmark, execution)
    assert identity["configuration_condition"] == condition
    del launch["configuration_condition"]
    launch["request_sha256"] = io.sha({k: v for k, v in launch.items() if k != "request_sha256"})
    (tmp_path / "launch.json").write_text(io.dumps(launch))
    with pytest.raises(ValueError, match="launch configuration condition"):
        run_spec.load_launch(tmp_path / "launch.json", require_acceptance=False, validate_current=False)


def test_metadata_export_retains_both_views_with_byte_inventory(tmp_path):
    work = tmp_path / "work"
    attempt = work / "attempts/synthetic/a0001"
    attempt.mkdir(parents=True)
    io.write_new(work / "plan.json", {"split": "simple", "tasks": [{"id": "synthetic", "domain": "synthetic", "task_name": "synthetic"}]})
    io.append(attempt / "api_search_metadata_events.jsonl", {"raw_upstream_result": "raw", "actor_visible_result": "visible"})
    export_return(work, tmp_path / "output.zip")
    with zipfile.ZipFile(tmp_path / "output.zip") as archive:
        name = "attempts/synthetic/a0001/api_search_metadata_events.jsonl"
        assert json.loads(archive.read(name))["raw_upstream_result"] == "raw"
        inventory = json.loads(archive.read("RETURN_MANIFEST.json"))["exported_file_inventory"]
        assert any(item["path"] == name for item in inventory)


def test_attempt_freezes_condition_and_preserves_public_inputs_and_initial_evidence(tmp_path, monkeypatch):
    import rpnh_ab.drivers
    up = upstream(tmp_path)
    condition = cc.selected(up)
    initial = {"meta": {"current_time": "2026-01-02T03:04:05Z"}, "synthetic": "original"}
    row = {"prompt": [{"role": "user", "content": "Public synthetic request"}],
           "info": {"initial_state": initial, "assertions": [{"type": "synthetic-only"}]}}
    case = {"id": "synthetic-only", "domain": "synthetic", "task_name": "synthetic.only",
            "task_contract_sha256": io.sha(row), "row": row}
    original = copy.deepcopy(row)
    up.start = lambda value: {"world": copy.deepcopy(initial), "initial_state": value["info"]["initial_state"], "info": value["info"]}
    up.dump_world = lambda state: copy.deepcopy(state["world"])
    # Stop before IPC/host startup, after immutable initial-world capture.
    class NoRuntime:
        def __init__(self, *args):
            raise RuntimeError("synthetic stop before IPC")
    monkeypatch.setattr(experiment, "Broker", NoRuntime)
    monkeypatch.setattr(rpnh_ab.drivers, "get_driver", lambda _: object())
    attempt = tmp_path / "attempt"
    assert experiment.one_attempt(up, case, attempt, tmp_path / "unused") is True
    assert io.load(attempt / "attempt.json")["configuration_condition"] == condition
    assert io.load(attempt / "public_task.json") == {"prompt": original["prompt"]}
    assert io.load(attempt / "task_contract.json") == original == row
    assert io.load(attempt / "world.initial.materialized.json") == initial
    assert io.load(attempt / "scoring_input.json") == {"initial_state": initial, "info": original["info"]}
    assert not up.calls and not (attempt / "final_world.json").exists()


def test_acceptance_rejects_baseline_attempt_relabelled_as_comparison(tmp_path):
    up = upstream(tmp_path)
    condition = cc.selected(up)
    benchmark = {"configuration_condition": condition, "tool_schemas_sha256": "synthetic"}
    execution = {"executor_host": "native", "rpnh": {}, "example_sources": {}, "host_identity": {}, "limits": {}}
    manifest = {"schema": "rpnh-ab/acceptance-manifest/v1",
                "identity": run_spec.acceptance_identity(benchmark, execution),
                "cases": {key: {"status": "passed"} for key in run_spec.REQUIRED_ACCEPTANCE},
                "real_provider_calls": 0, "real_business_api_calls": 0,
                "historical_benchmark_tasks_executed": 0, "synthetic_acceptance_only": True}
    io.write_new(tmp_path / "manifest.json", manifest)
    for name in ("success-attempt", "stop-attempt"):
        io.write_new(tmp_path / name / "attempt.json", {})  # baseline evidence
    with pytest.raises(ValueError, match="attempt configuration condition"):
        run_spec.validate_acceptance(tmp_path / "manifest.json", benchmark, execution)


def test_summary_and_score_provenance_are_opt_in_without_changing_rubric(tmp_path):
    from rpnh_ab.scoring import score_attempt, summarize
    up = upstream(tmp_path)
    condition = cc.selected(up)
    task = {"id": "synthetic", "domain": "synthetic", "task_name": "synthetic.only", "task_contract_sha256": "a" * 64}
    plan = {"split": "simple", "tasks": [task], "condition_id": "synthetic-baseline", "manifest_sha256": io.sha([task])}
    baseline_summary = summarize(plan, tmp_path)
    assert "configuration_condition" not in baseline_summary and "condition_id" not in baseline_summary
    opted_plan = cc.freeze_plan(plan, condition)
    attempt = tmp_path / "attempts/synthetic/a0001"
    io.write_new(attempt / "task_contract.json", {"synthetic": True})
    io.write_new(attempt / "attempt.json", {"id": task["id"], "task_contract_sha256": task["task_contract_sha256"],
                 "task_contract_file_sha256": io.file_sha(attempt / "task_contract.json"), "configuration_condition": condition})
    io.write_new(attempt / "scoring_input.json", {"initial_state": {"before": True}, "info": {"assertions": []}})
    io.write_new(attempt / "final_world.json", {"after": True})
    io.write_new(attempt / "lifecycle.json", {"admitted": True, "execution_status": "host_terminal", "host_quiescent": True, "world_owner_quiescent": True})
    seen = []
    def scorer(*args):
        seen.append(args)
        return {"partial_credit": 0.5, "task_completed_correctly": 0.0}
    score = score_attempt(attempt, scorer, task=task)
    assert seen == [({"after": True}, {"before": True}, {"assertions": []})]
    assert score["configuration_condition"] == condition and score["partial_credit"] == 0.5
    summary = summarize(opted_plan, tmp_path)
    assert summary["configuration_condition"] == condition and summary["condition_id"] == opted_plan["condition_id"]
    assert summary["manifest_sha256"] == plan["manifest_sha256"]
    assert summary["scored_tasks"] == 1 and summary["full_batch_mean_partial_credit"] == 0.5
    assert summarize(plan, tmp_path)["scored_tasks"] == 0  # no cross-condition mixing
    output = tmp_path / "condition-return.zip"
    io.write_new(tmp_path / "plan.json", opted_plan)
    export_return(tmp_path, output)
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read("summary.json"))["configuration_condition"] == condition
