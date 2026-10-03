"""Real installed-host acceptance using only a synthetic local adapter/world."""
from __future__ import annotations

import json
from pathlib import Path
import threading

from .constants import PLUGIN_RESULT_BYTES
from .io import file_sha, load, sha, write_new
from .offline_fixture import create_profile, synthetic_row, tool_steps
from .plugin import bindings, factory
from .run_spec import (REQUIRED_ACCEPTANCE, acceptance_identity, dsh_identity,
                       validate_acceptance)


def _case(row: dict, identifier: str) -> dict:
    return {
        "id": identifier,
        "domain": "synthetic",
        "domain_index": 0,
        "example_id": -1,
        "example_id_assigned_by_adapter": True,
        "task_name": row["info"]["task_name"],
        "task_contract_sha256": sha(row),
        "row": row,
    }


def _event_counts(path: Path) -> tuple[int, int]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    started = sum(event.get("kind") == "dispatch_started" for event in events)
    finished = sum(event.get("kind") == "dispatch_finished" for event in events)
    return started, finished


def _record(root: Path, name: str, value: dict) -> dict:
    value = {"schema": "rpnh-ab/acceptance-evidence/v1", "case": name,
             "status": "passed", **value}
    path = root / "evidence" / f"{name}.json"
    write_new(path, value)
    return {"status": "passed", "evidence_path": str(path),
            "evidence_sha256": file_sha(path)}


def _stop_after_first_model_request(
        requests_log: Path, stop_path: Path, finished: threading.Event,
        stop_signalled: threading.Event, transition_lock: threading.Lock,
) -> None:
    """Request stop only after the installed host has admitted model work."""
    while not finished.wait(0.05):
        if requests_log.is_file() and requests_log.stat().st_size:
            with transition_lock:
                if finished.is_set():
                    return
                stop_path.touch()
                stop_signalled.set()
            return


def produce(upstream, work: Path, *, host: str, dsh_checkout: Path | None = None) -> dict:
    """Produce the seven-case gate; never touch checked-in historical results."""
    work = Path(work).resolve()
    conditions = load(work / "conditions.json")
    benchmark, execution = conditions["benchmark_spec"], conditions["execution_spec"]
    if execution.get("executor_host") != host:
        raise ValueError("acceptance host differs from the prepared execution condition")
    if host == "dsh":
        if dsh_checkout is None:
            raise ValueError("DSH acceptance requires the pinned checkout")
        if dsh_identity(dsh_checkout) != execution.get("host_identity"):
            raise ValueError("DSH checkout/patch identity differs from the prepared condition")
    root = work / "host-acceptance"
    root.mkdir(mode=0o700, exist_ok=False)
    success_profile = create_profile(
        root / "success-profile",
        tool_steps(padding=47, large_payload_bytes=70 * 1024),
        # Native accepts a standards-compliant multi-call response. The pinned
        # DSH managed profile intentionally admits one tool call per turn.
        batch_tools=host == "native",
    )
    row = synthetic_row()
    success_attempt = root / "success-attempt"
    from .experiment import one_attempt
    blocked = one_attempt(
        upstream, _case(row, "synthetic-host-success"), success_attempt,
        success_profile, executor_host=host,
        native_run_dir=root / "success-host-run",
        control_root=root / "success-control",
        dsh_checkout=dsh_checkout,
    )
    lifecycle = load(success_attempt / "lifecycle.json")
    if (blocked or lifecycle.get("execution_status") != "host_terminal"
            or not lifecycle.get("host_quiescent")
            or not lifecycle.get("world_owner_quiescent")
            or not (success_attempt / "final_world.json").is_file()):
        raise RuntimeError("installed host did not produce terminal/quiescent synthetic evidence")
    started, finished = _event_counts(success_attempt / "tool_events.jsonl")
    if started != finished or started <= 48:
        raise RuntimeError("installed host did not complete the unmetered >48 dispatch fixture")
    score = load(sorted(success_attempt.glob("score-*.json"))[-1])
    if score.get("task_completed_correctly") != 1.0:
        raise RuntimeError("installed host synthetic world/rubric result differs")

    operation_bounds = {operation.name: operation.max_result_bytes
                        for operation in factory().operations}
    if set(operation_bounds.values()) != {PLUGIN_RESULT_BYTES}:
        raise RuntimeError("installed managed-tool result declaration differs")
    managed = bindings(upstream.schemas)["executor"]

    stop_profile = create_profile(root / "stop-profile", tool_steps(), delay_seconds=3)
    stop_path = root / "stop.request"
    stop_finished = threading.Event()
    stop_signalled = threading.Event()
    stop_transition = threading.Lock()
    timer = threading.Thread(
        target=_stop_after_first_model_request,
        args=(stop_profile.parent / "requests.jsonl", stop_path,
              stop_finished, stop_signalled, stop_transition),
        daemon=True)
    timer.start()
    stop_attempt = root / "stop-attempt"
    try:
        stopped = one_attempt(
            upstream, _case(row, "synthetic-host-stop"), stop_attempt, stop_profile,
            executor_host=host, native_run_dir=root / "stop-host-run",
            control_root=root / "stop-control", dsh_checkout=dsh_checkout,
            stop_path=stop_path,
        )
    finally:
        with stop_transition:
            stop_finished.set()
        timer.join(timeout=1)
    if timer.is_alive():
        raise RuntimeError("stop fixture coordinator did not exit")
    if not stop_signalled.is_set():
        raise RuntimeError(
            "installed host ended before the stop fixture observed admitted model work")
    stop_lifecycle = load(stop_attempt / "lifecycle.json")
    if (not stopped or stop_lifecycle.get("admitted") is not True
            or not stop_lifecycle.get("host_quiescent")
            or (stop_attempt / "final_world.json").exists()
            or list(stop_attempt.glob("score-*.json"))):
        raise RuntimeError("host stop fixture was not quiescent or was incorrectly frozen/scored")
    if host == "dsh" and dsh_identity(dsh_checkout) != execution.get("host_identity"):
        raise RuntimeError("DSH checkout identity changed during installed-host acceptance")

    shared = {
        "executor_host": host,
        "success_attempt": str(success_attempt),
        "stop_attempt": str(stop_attempt),
        "real_provider_calls": 0,
        "real_business_api_calls": 0,
    }
    cases = {
        "installed_runtime": _record(root, "installed_runtime", {
            **shared, "lifecycle": lifecycle,
        }),
        "managed_schema_effect": _record(root, "managed_schema_effect", {
            **shared, "tool_names": sorted(managed["tools"]),
            "admitted_effects": managed["admitted_effects"],
        }),
        "host_execution": _record(root, "host_execution", {
            **shared, "execution_status": lifecycle["execution_status"],
            "admitted": lifecycle["admitted"],
        }),
        "result_boundaries": _record(root, "result_boundaries", {
            **shared, "declared_operation_bytes": operation_bounds,
            "crossed_legacy_64k_fixture": True,
        }),
        "null_budget_over_48": _record(root, "null_budget_over_48", {
            **shared, "successful_dispatches": finished,
            "configured_cumulative_model_calls": None,
            "configured_cumulative_tool_calls": None,
        }),
        "quiescent_stop": _record(root, "quiescent_stop", {
            **shared, "execution_status": stop_lifecycle.get("execution_status"),
            "host_quiescent": stop_lifecycle.get("host_quiescent"),
            "world_owner_quiescent": stop_lifecycle.get("world_owner_quiescent"),
            "final_world_written": False, "score_written": False,
        }),
        "matched_upstream_world_rubric": _record(root, "matched_upstream_world_rubric", {
            **shared, "score_status": score["status"],
            "partial_credit": score["partial_credit"],
            "task_completed_correctly": score["task_completed_correctly"],
        }),
    }
    if set(cases) != set(REQUIRED_ACCEPTANCE):
        raise AssertionError("acceptance producer and validator case sets differ")
    manifest = {
        "schema": "rpnh-ab/acceptance-manifest/v1",
        "identity": acceptance_identity(benchmark, execution),
        "cases": cases,
        "real_provider_calls": 0,
        "real_business_api_calls": 0,
        "historical_benchmark_tasks_executed": 0,
        "synthetic_acceptance_only": True,
    }
    path = root / "acceptance.json"
    write_new(path, manifest)
    validate_acceptance(path, benchmark, execution)
    return {"manifest": str(path), "manifest_sha256": file_sha(path), **manifest}
