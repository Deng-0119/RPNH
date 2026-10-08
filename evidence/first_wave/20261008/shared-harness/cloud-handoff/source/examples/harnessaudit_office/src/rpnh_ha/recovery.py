"""Offline recovery of a native result after adapter-side post-run failure.

This path never starts a worker, backend, or model provider.  It projects the
already-frozen Registry and backend witness into a fresh evidence directory so
the original failed report remains unchanged.
"""
from __future__ import annotations

from pathlib import Path
import shutil

from .constants import OFFICE_EFFECTS
from .crosswalk import crosswalk
from .driver_contract import DriverResult
from .evidence import read_events
from .jsonio import dumps, read, write_new
from .local_driver import _nonterminal_worker_result
from .projection import (
    ObservationCollector,
    save_normalized_actions,
)
from .registry_export import export_registry
from .task_view import PublicTask, assert_no_hidden_keys
from .workflow import build_business_workflow
from .comparison_condition import condition_of, effects_for, saved_condition_record, comparison_evidence_report


def _action_sink(run_id: str):
    from multi_agent.frameworks.core.action_sink import ActionSink

    return ActionSink(run_id=run_id)


def _one_run_id(events: list[dict]) -> str:
    values = {
        event["run_id"] for event in events
        if isinstance(event.get("run_id"), str) and event["run_id"]
    }
    if len(values) != 1:
        raise ValueError("backend witness must contain exactly one run identity")
    return next(iter(values))


def recover_live_evidence(*, run_root: Path, output: Path) -> dict:
    """Recover one nonterminal native result without altering source evidence."""
    run_root = Path(run_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("offline recovery requires a fresh output directory")
    if (run_root / "driver_result.json").exists():
        raise ValueError("source run already has a native driver result")
    native_run = run_root / "rpnh-run"
    if not native_run.is_dir():
        raise FileNotFoundError("source native Registry run is missing")

    public = read(run_root / "public_input.json")
    if (not isinstance(public, dict)
            or public.get("schema_version") != "rpnh-ha/public-task/v2"):
        raise ValueError("source public input is not the pinned task projection")
    assert_no_hidden_keys(public)
    condition_record = saved_condition_record(run_root)
    task = PublicTask(dumps(public))
    _graph, roles_by_node = build_business_workflow(task)
    roles = {row["role"] for row in public["agents"]}
    tools = {row["name"] for row in public["tools"]}
    if tools != set(effects_for(condition_of(task))):
        raise ValueError("source public tool inventory differs from the bridge catalog")

    monitor = read(run_root / "rpnh-monitor.json")
    if (not isinstance(monitor, dict)
            or monitor.get("schema_version") not in {"rpnh-ha/task-monitor/v1", "rpnh-ha/task-monitor/v2"}
            or not isinstance(monitor.get("final_status"), dict)):
        raise ValueError("source task monitor lacks one final worker status")
    if monitor.get("final_snapshot_permitted") is False:
        raise ValueError("shutdown was not confirmed; do not project a final run")
    worker = _nonterminal_worker_result(
        monitor["final_status"], native_run)
    if worker is None:
        raise ValueError("offline recovery supports only an exit-2 nonterminal worker")

    collector = ObservationCollector(roles=roles, tools=tools)
    exported = export_registry(
        native_run,
        roles_by_node,
        collector,
        terminal_evidence_ref=None,
        stop_reason=worker["stop_reason"],
        include_provisional=worker["stop_reason"] == "blocked_or_waiting",
    )
    if sum(worker["actual_model_call_counts"]) != exported.actual_model_calls:
        raise RuntimeError("worker and recovered Registry model accounting differ")

    witness_path = run_root / "backend" / "witness.jsonl"
    events = read_events(witness_path)
    run_id = _one_run_id(events)
    if ({row["run_id"] for row in exported.registry_records} or {run_id}) != {run_id}:
        raise ValueError("Registry export and backend witness run identities differ")
    links = crosswalk(events, exported.registry_records)
    result = DriverResult(
        execution_mode="native_live",
        final_output="",
        terminal_evidence_ref=None,
        stop_reason=exported.stop_reason,
        actual_model_calls=exported.actual_model_calls,
        registry_records=exported.registry_records,
        roles_observed=exported.roles_observed,
        context_capture_complete=exported.context_capture_complete,
        model_identity=exported.model_identity,
    )

    sink = _action_sink(run_id)
    collector.export_to(sink)
    trace_complete = bool(
        result.context_capture_complete
        and result.roles_observed
        and set(result.roles_observed) <= roles
        and len(collector) > 0
        and links["complete"]
    )
    source_status = read(run_root / "run_status.json")
    protocol = read(run_root / "protocol.json")
    limits = protocol.get("limits") if isinstance(protocol, dict) else None
    max_model_calls = (
        limits.get("max_model_calls") if isinstance(limits, dict) else None)
    if (max_model_calls is not None
            and (type(max_model_calls) is not int or max_model_calls < 1)):
        raise ValueError("source protocol has a malformed model-call limit")

    recovered_status = {
        "error": None,
        "source_error": source_status.get("error"),
        "recovery_mode": "offline_registry_projection",
        "native_terminal_present": False,
        "business_task_success": None,
        "execution_mode": result.execution_mode,
        "trace_complete": trace_complete,
        "official_score": None,
        "actual_model_calls": result.actual_model_calls,
        "fault_extension": source_status.get("fault_extension", False),
        "baseline_comparability": source_status.get(
            "baseline_comparability",
            "requires_native_driver_protocol_review"),
        "model_budget_exceeded": (
            None if max_model_calls is None else
            result.actual_model_calls > max_model_calls),
    }
    report = {
        "schema_version": "rpnh-ha/offline-recovery/v1",
        "recovery_completed": True,
        "provider_calls_made": 0,
        "source_run_root": str(run_root),
        "source_adapter_error": source_status.get("error"),
        "run_id": run_id,
        "execution_mode": result.execution_mode,
        "native_terminal_present": False,
        "trace_complete": trace_complete,
        "stop_reason": result.stop_reason,
        "actual_model_calls": result.actual_model_calls,
        "observations": len(collector),
        "registry_records": len(result.registry_records),
        "crosswalk": links,
        "model_identity": result.model_identity,
        "official_scores": None,
        "scoring_pending": True,
        "note": (
            "Recovered only the adapter-side evidence projection from the "
            "existing native run; no worker or model provider was invoked."),
    }

    output.mkdir(parents=True)
    recovered_status.update(condition_record)
    report.update(condition_record)
    if condition_record:
        for relative in ("public_input.json", "protocol.json", "configuration_condition.json"):
            if (run_root / relative).is_file():
                shutil.copy2(run_root / relative, output / relative)
    write_new(output / "capture_diagnostics.json", exported.capture_diagnostics)
    collector.save(output / "observations.json")
    if condition_record:
        write_new(output / "comparison_evidence.json", comparison_evidence_report(
            read(output / "observations.json"), state_snapshot_available=(run_root / "bank.after.sqlite").is_file()))
    save_normalized_actions(output / "actions.normalized.json", sink)
    write_new(output / "driver_result.json", result.__dict__)
    write_new(output / "crosswalk.json", links)
    write_new(output / "run_status.json", recovered_status)
    write_new(output / "offline_recovery_report.json", report)
    return report


def reproject_live_evidence(*, run_root: Path, output: Path) -> dict:
    """Reproject one completed native run without rerunning its executor."""
    run_root = Path(run_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(
            "offline reprojection requires a fresh output directory")
    native_run = run_root / "rpnh-run"
    if not native_run.is_dir():
        raise FileNotFoundError("source native Registry run is missing")
    source_status = read(run_root / "run_status.json")
    source_driver = read(run_root / "driver_result.json")
    monitor_path = run_root / "rpnh-monitor.json"
    if monitor_path.is_file():
        monitor = read(monitor_path)
        if (monitor.get("schema_version") == "rpnh-ha/task-monitor/v2"
                and monitor.get("final_snapshot_permitted") is not True):
            raise ValueError("source worker quiescence was not confirmed")
    terminal_ref = source_driver.get("terminal_evidence_ref")
    if (source_status.get("execution_mode") != "native_live"
            or source_status.get("native_terminal_present") is not True
            or source_driver.get("execution_mode") != "native_live"
            or not isinstance(terminal_ref, dict)
            or not isinstance(source_driver.get("final_output"), str)):
        raise ValueError(
            "offline reprojection requires a completed native live run")

    public = read(run_root / "public_input.json")
    if (not isinstance(public, dict)
            or public.get("schema_version") != "rpnh-ha/public-task/v2"):
        raise ValueError("source public input is not the pinned task projection")
    assert_no_hidden_keys(public)
    condition_record = saved_condition_record(run_root)
    task = PublicTask(dumps(public))
    _graph, roles_by_node = build_business_workflow(task)
    roles = {row["role"] for row in public["agents"]}
    tools = {row["name"] for row in public["tools"]}
    if tools != set(effects_for(condition_of(task))):
        raise ValueError(
            "source public tool inventory differs from the bridge catalog")

    collector = ObservationCollector(roles=roles, tools=tools)
    exported = export_registry(
        native_run,
        roles_by_node,
        collector,
        terminal_evidence_ref=terminal_ref,
        stop_reason=source_driver["stop_reason"],
    )
    if exported.actual_model_calls != source_driver.get("actual_model_calls"):
        raise RuntimeError(
            "source result and reprojected Registry model accounting differ")
    if {dumps(row) for row in exported.registry_records} != {
            dumps(row) for row in source_driver.get("registry_records", [])}:
        raise RuntimeError(
            "source result and reprojected managed Registry evidence differ")

    witness_path = run_root / "backend" / "witness.jsonl"
    events = read_events(witness_path)
    run_id = _one_run_id(events)
    if ({row["run_id"] for row in exported.registry_records} or {run_id}) != {
            run_id}:
        raise ValueError(
            "Registry export and backend witness run identities differ")
    links = crosswalk(events, exported.registry_records)
    result = DriverResult(
        execution_mode="native_live",
        final_output=source_driver["final_output"],
        terminal_evidence_ref=exported.terminal_evidence_ref,
        stop_reason=exported.stop_reason,
        actual_model_calls=exported.actual_model_calls,
        registry_records=exported.registry_records,
        roles_observed=exported.roles_observed,
        context_capture_complete=exported.context_capture_complete,
        model_identity=exported.model_identity,
    )

    sink = _action_sink(run_id)
    collector.export_to(sink)
    trace_complete = bool(
        result.context_capture_complete
        and result.roles_observed
        and set(result.roles_observed) <= roles
        and len(collector) > 0
        and links["complete"]
    )
    protocol = read(run_root / "protocol.json")
    limits = protocol.get("limits") if isinstance(protocol, dict) else None
    max_model_calls = (
        limits.get("max_model_calls") if isinstance(limits, dict) else None)
    if (max_model_calls is not None
            and (type(max_model_calls) is not int or max_model_calls < 1)):
        raise ValueError("source protocol has a malformed model-call limit")
    bank_path = run_root / "bank.after.sqlite"
    if not bank_path.is_file():
        raise FileNotFoundError("source final bank snapshot is missing")

    reprojected_status = {
        "error": None,
        "source_error": source_status.get("error"),
        "recovery_mode": "offline_registry_reprojection",
        "native_terminal_present": True,
        "business_task_success": source_status.get("business_task_success"),
        "execution_mode": result.execution_mode,
        "trace_complete": trace_complete,
        "official_score": None,
        "actual_model_calls": result.actual_model_calls,
        "fault_extension": source_status.get("fault_extension", False),
        "baseline_comparability": source_status.get(
            "baseline_comparability",
            "requires_native_driver_protocol_review"),
        "model_budget_exceeded": (
            None if max_model_calls is None else
            result.actual_model_calls > max_model_calls),
    }
    report = {
        "schema_version": "rpnh-ha/offline-reprojection/v1",
        "reprojection_completed": True,
        "provider_calls_made": 0,
        "source_run_root": str(run_root),
        "run_id": run_id,
        "execution_mode": result.execution_mode,
        "native_terminal_present": True,
        "trace_complete": trace_complete,
        "stop_reason": result.stop_reason,
        "actual_model_calls": result.actual_model_calls,
        "observations": len(collector),
        "registry_records": len(result.registry_records),
        "crosswalk": links,
        "model_identity": result.model_identity,
        "official_scores": None,
        "scoring_pending": True,
        "note": (
            "Reprojected normalized evidence from the existing native Registry "
            "and copied its final bank snapshot; no executor, backend, or model "
            "provider was invoked."),
    }

    output.mkdir(parents=True)
    reprojected_status.update(condition_record)
    report.update(condition_record)
    write_new(output / "capture_diagnostics.json", exported.capture_diagnostics)
    write_new(output / "projection_provenance.json", {
        "projection_revision": exported.capture_diagnostics["projection_revision"],
        "original_execution_protocol_preserved": True,
        "original_execution_rerun": False,
        "scope": "registered_tool_execution_proven_read_handoffs_terminal_output",
        "all_possible_context_surfaces_audited": False,
        "provider_calls_made": 0,
    })
    collector.save(output / "observations.json")
    if condition_record:
        write_new(output / "comparison_evidence.json", comparison_evidence_report(
            read(output / "observations.json"), state_snapshot_available=bank_path.is_file()))
    save_normalized_actions(output / "actions.normalized.json", sink)
    write_new(output / "driver_result.json", result.__dict__)
    write_new(output / "crosswalk.json", links)
    write_new(output / "run_status.json", reprojected_status)
    write_new(output / "offline_reprojection_report.json", report)
    for relative in (
            Path("bank.after.sqlite"), Path("public_input.json"),
            Path("protocol.json"), Path("rpnh-monitor.json"),
            Path("configuration_condition.json"),
            Path("backend/witness.jsonl")):
        source = run_root / relative
        if source.is_file():
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return report


__all__ = ("recover_live_evidence", "reproject_live_evidence")
