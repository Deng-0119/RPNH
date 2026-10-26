"""Offline subprocess failures must reach AgentLoop without an unsafe retry."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from cpn.llm_adapters.local_process import _local_failure_disposition
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.registry._registry import _RegistryCore
from test_optional_context_compaction import _objects


@pytest.mark.parametrize("failure", [
    None, "local_process_error", "timeout", "local_process_unavailable",
])
@pytest.mark.parametrize("request_submitted", [False, True])
def test_local_failure_preserves_submission_boundary(failure, request_submitted):
    expected = (("submission_unknown", "submission_unknown")
                if request_submitted else ("provider_retryable", "not_submitted"))
    assert _local_failure_disposition(
        failure, request_submitted=request_submitted) == expected


@pytest.mark.parametrize("request_submitted", [False, True])
def test_oversized_response_remains_observed(request_submitted):
    assert _local_failure_disposition(
        "response_too_large", request_submitted=request_submitted
    ) == ("provider_failure", "response_observed")


@pytest.mark.parametrize("mode,outcome", [
    ("empty", "no_response"),
    ("nonzero", "local_process_error"),
    ("timeout", "timeout"),
])
def test_real_local_process_unknown_compaction_does_not_resubmit(
        tmp_path: Path, mode: str, outcome: str,
) -> None:
    # The first real subprocess returns a length interruption. The second reads
    # the compaction request, then loses its result. No port/classifier wrapper
    # intervenes between subprocess, production adapter and mechanical policy.
    script = tmp_path / "local_fixture.py"
    script.write_text('''import json
from pathlib import Path
import sys
import time

request = json.load(sys.stdin)
calls = Path("calls.jsonl")
first = not calls.exists()
with calls.open("a") as output:
    output.write(json.dumps(request) + "\\n")
if first:
    sys.stdout.write(json.dumps({"protocol": "llm_response_envelope/v1",
                                 "tool_calls": [], "finish_reason": "length"},
                                sort_keys=True, separators=(",", ":")))
else:
    print("bridge: local_bridge_failure: fixture_result_lost | offline detail",
          file=sys.stderr, flush=True)
    if sys.argv[1] == "nonzero":
        print("partial stdout without a result", flush=True)
        sys.exit(7)
    if sys.argv[1] == "timeout":
        print("partial stdout without a result", flush=True)
        time.sleep(10)
''', encoding="utf-8")
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process", "model_condition": "offline-local",
        "argv": [sys.executable, "-B", str(script), mode],
        "probe_argv": [sys.executable, "-B", "-c", "pass"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process", "model_condition": "offline-local",
        "adapter_config_path": str(adapter), "timeout_seconds": 2,
        "max_output_tokens": 128, "max_response_bytes": 65536,
    }), encoding="utf-8")
    run_dir = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt="Exercise offline local result loss.",
        stages=(AgentStage("main", "Return one result."),),
        execution_config_path=selection, max_attempts_per_stage=3,
    ))
    calls = [json.loads(line) for line in
             (run_dir / "calls.jsonl").read_text().splitlines()]
    from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
    assert calls[1]["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
    assert len(calls) == 2, "unknown compaction submission was sent again"
    assert result["stop_reason"] == "blocked_or_waiting"
    assert result["output"] is None
    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    failures = core.event_store.list_events_by_type(("llm_invocation_failed/v1",))
    assert len(failures) == 1
    failure = failures[0].payload
    assert failure["disposition"] == failure["submission_state"] == "submission_unknown"
    assert failure["failure_code"] == "local_bridge_failure:fixture_result_lost"
    assert not _objects(core, "agent_context_compaction/v3")
    audit = [json.loads(line) for line in
             (run_dir / "adapter-private/llm-attempts.jsonl").read_text().splitlines()]
    finished = [row for row in audit if row.get("outcome") == outcome]
    assert len(finished) == 1
    detail = finished[0]["detail"]
    assert detail["submission_state"] == "submission_unknown"
    assert detail["retry_eligible"] is False
    assert detail["recovery_disposition"] == "block_no_retry"
    assert detail["failure_category"] == "local_bridge_failure"
    assert "offline detail" in detail["stderr_excerpt"]
