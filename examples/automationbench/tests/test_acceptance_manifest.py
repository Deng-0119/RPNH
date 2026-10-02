"""Fast contract checks for the installed-host acceptance producer."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rpnh_ab.acceptance_manifest import produce
from rpnh_ab.io import file_sha, load, write_new
from rpnh_ab.offline_adapter import response
from rpnh_ab.run_spec import REQUIRED_ACCEPTANCE, _validate_dsh_host_result


def test_offline_adapter_uses_tools_then_host_specific_completion():
    request = {"messages": [], "tools": [{"function": {"name": "api_fetch"}}]}
    scenario = {"steps": [{"tool": "api_fetch", "arguments": {"x": 1}}]}
    first = response(request, scenario)
    assert first["tool_calls"][0]["name"] == "api_fetch"
    request["messages"].append({"role": "assistant", "content": "called"})
    assert response(request, scenario)["text"].startswith("Deterministic")


def test_offline_adapter_can_batch_many_tool_calls_in_one_model_turn():
    request = {"messages": [], "tools": [{"function": {"name": "base64_encode"}}]}
    scenario = {"steps": [
        {"tool": "base64_encode", "arguments": {"text": str(index)}}
        for index in range(50)
    ], "batch_tools": True}
    first = response(request, scenario)
    assert len(first["tool_calls"]) == 50
    assert len({call["id"] for call in first["tool_calls"]}) == 50
    request["messages"].append({"role": "assistant", "content": "called"})
    assert response(request, scenario)["finish_reason"] == "stop"


def test_lifecycle_doubles_cannot_certify_installed_host_acceptance(
        tmp_path, monkeypatch,
):
    work = tmp_path / "work"
    work.mkdir()
    execution = {
        "executor_host": "native", "rpnh": {"commit": "test"},
        "example_sources": {"x.py": "a" * 64}, "host_identity": {},
        "limits": {"cumulative_model_calls": None},
    }
    benchmark = {"tool_schemas_sha256": "b" * 64}
    write_new(work / "conditions.json", {
        "benchmark_spec": benchmark, "execution_spec": execution,
    })
    schemas = [{"function": {
        "name": name, "description": name,
        "parameters": {"type": "object"},
    }} for name in ("api_search", "api_fetch", "base64_encode")]
    upstream = SimpleNamespace(schemas=schemas)

    def fake_attempt(_upstream, case, attempt, _profile, **_kwargs):
        attempt.mkdir(parents=True)
        write_new(attempt / "task_contract.json", case["row"])
        write_new(attempt / "attempt.json", {
            "id": case["id"],
            "task_contract_sha256": case["task_contract_sha256"],
            "task_contract_file_sha256": file_sha(attempt / "task_contract.json"),
        })
        write_new(attempt / "scoring_input.json", {
            "initial_state": {}, "info": {},
        })
        if case["id"].endswith("stop"):
            write_new(attempt / "lifecycle.json", {
                "execution_status": "manual_stop", "admitted": True,
                "host_quiescent": True, "world_owner_quiescent": False,
            })
            write_new(attempt / "native_evidence.json", {
                "source": "native_registry_exact_authority",
                "managed_action_records": 0,
            })
            return True
        write_new(attempt / "lifecycle.json", {
            "execution_status": "host_terminal", "admitted": True,
            "host_quiescent": True, "world_owner_quiescent": True,
        })
        write_new(attempt / "final_world.json", {})
        write_new(attempt / "score-0001.json", {
            "task_id": case["id"],
            "task_contract_sha256": case["task_contract_sha256"],
            "task_contract_file_sha256": file_sha(attempt / "task_contract.json"),
            "final_world_sha256": file_sha(attempt / "final_world.json"),
            "scoring_input_sha256": file_sha(attempt / "scoring_input.json"),
            "status": "scored", "partial_credit": 1.0,
            "task_completed_correctly": 1.0,
        })
        with (attempt / "tool_events.jsonl").open("w", encoding="utf-8") as stream:
            for index in range(50):
                stream.write(json.dumps({"kind": "dispatch_started", "sequence": index}) + "\n")
                result = "x" * (70 * 1024) if index == 0 else "ok"
                stream.write(json.dumps({"kind": "dispatch_finished", "sequence": index,
                                         "response": {"ok": True, "result": result}}) + "\n")
        write_new(attempt / "native_evidence.json", {
            "source": "native_registry_exact_authority",
            "managed_action_records": 50,
        })
        write_new(attempt / "bridge_registry_check.json", {
            "registry_projection_available": True,
            "all_environment_returns_registered": True,
            "matched_unique_sequences": 50,
        })
        return False

    import rpnh_ab.experiment as experiment
    monkeypatch.setattr(experiment, "one_attempt", fake_attempt)
    import pytest
    with pytest.raises(ValueError, match="Registry path differs"):
        produce(upstream, work, host="native")
    manifest = load(work / "host-acceptance" / "acceptance.json")
    assert set(manifest["cases"]) == set(REQUIRED_ACCEPTANCE)
    assert all(row["status"] == "passed" for row in manifest["cases"].values())
    assert manifest["historical_benchmark_tasks_executed"] == 0
    assert manifest["real_provider_calls"] == 0


def test_dsh_raw_host_validation_reparses_terminal_history(tmp_path):
    import pytest
    ref = {"entity_type": "turn/v1", "entity_id": "turn-1",
           "version_id": "turn-version-1"}
    raw = {
        "schema_version": "rpnh/automationbench_dsh_host_result/v1",
        "session_id": "session-1", "admitted": True,
        "process_exit_confirmed": True, "process_quiescent": True,
        "process_group_alive": False, "host_quiescent": True,
        "stop_requested": False, "return_code": 0, "status": "terminal",
        "outcome": {"status": "terminal", "answer": {"text": "done"}},
        "history": {"active": None, "committed_history": [
            {"answer": {"text": "done"}}], "latest_turn_ref": ref,
            "latest_committed_turn_ref": ref},
    }
    path = tmp_path / "host-result.json"
    write_new(path, raw)
    assert _validate_dsh_host_result(path, terminal=True)["session_id"] == "session-1"
    raw["history"]["latest_committed_turn_ref"] = None
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="terminal/history"):
        _validate_dsh_host_result(path, terminal=True)
