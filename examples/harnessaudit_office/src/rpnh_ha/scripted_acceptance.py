"""Deterministic end-to-end acceptance of the native three-role driver."""
from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

from .adapter import RPNHHarnessAuditAdapter
from .backend import FaultSpec
from .driver_contract import LiveLimits
from .evidence import read_events
from .jsonio import read, write_new
from .local_driver import ScriptedLocalNativeDriver
from .scripted_profile import write_scripted_profile
from .upstream import load_case, office_bank_factory, official_dispatch


async def run_scripted_acceptance(
        *, audit_root: Path, output: Path, lost_response: bool = False) -> dict:
    """Run actual RPNH/Registry/OfficeBank wiring without an external model."""
    audit_root = Path(audit_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("scripted acceptance requires a fresh output root")
    output.mkdir(parents=True)
    task, catalog, public = load_case(audit_root)
    profile = write_scripted_profile(output / "profile")
    factory = office_bank_factory(audit_root)
    bank = factory()
    run_id = "scripted-acceptance-" + uuid.uuid4().hex[:12]
    fault = (
        FaultSpec("response_lost_after_effect", "order_service_catalog_item")
        if lost_response else FaultSpec()
    )
    try:
        from multi_agent.frameworks.core.action_sink import ActionSink
        from multi_agent.frameworks.core.base import RunContext

        sink = ActionSink(run_id=run_id)
        context = RunContext(
            run_id=run_id,
            task=task,
            catalog=catalog,
            model="rpnh-ha-scripted-no-external-model",
            max_turns=60,
            bank=bank,
            workspace=None,
            action_sink=sink,
        )
        adapter = RPNHHarnessAuditAdapter(
            driver=ScriptedLocalNativeDriver(),
            root=output / "adapter-run",
            execution_profile=profile,
            limits=LiveLimits(60, 60, 300),
            bank_factory=factory,
            dispatch=official_dispatch,
            fault=fault,
        )
        outcome = await adapter.run(context, public.as_dict()["goal"])
        actions = sink.actions
    finally:
        bank.close()

    run_root = output / "adapter-run"
    status = read(run_root / "run_status.json")
    result_path = run_root / "driver_result.json"
    result = read(result_path) if result_path.is_file() else None
    crosswalk = read(run_root / "crosswalk.json")
    diagnostic_path = run_root / "state_diagnostic.json"
    diagnostic = read(diagnostic_path) if diagnostic_path.is_file() else None
    monitor_path = run_root / "rpnh-monitor.json"
    monitor = read(monitor_path) if monitor_path.is_file() else None
    observed_roles = set(result["roles_observed"]) if result else set()
    expected_roles = {item["role"] for item in public.as_dict()["agents"]}
    tool_actions = [item for item in actions if str(item.surface) == "tool_call"]
    communication_actions = [
        item for item in actions if str(item.surface) == "communication"]
    witness_path = run_root / "backend" / "witness.jsonl"
    witness = read_events(witness_path) if witness_path.is_file() else []
    unknown_write_observed = any(
        str(item.surface) == "tool_call"
        and getattr(item, "tool_name", None) == "order_service_catalog_item"
        and isinstance(getattr(item, "raw_event", None), dict)
        and item.raw_event.get("rpnh_phase") == "outcome_unknown"
        for item in actions
    )
    checks = [
        {"name": "adapter-returned-without-error",
         "passed": outcome.error is None},
        {"name": "native-scripted-mode-is-explicit",
         "passed": status["execution_mode"] == "native_scripted"},
        {"name": "bottom-task-monitor-report-present",
         "passed": monitor is not None},
        {"name": (
             "lost-response-has-no-success-terminal" if lost_response
             else "canonical-terminal-evidence-present"),
         "passed": status["native_terminal_present"] is (not lost_response)},
        {"name": "trace-capture-complete",
         "passed": status["trace_complete"] is True},
        {"name": "registry-backend-crosswalk-complete",
         "passed": crosswalk["complete"] is True},
        {"name": "all-public-roles-observed",
         "passed": (
             ({"operations_manager", "workplace_services_admin"}
              <= observed_roles <= expected_roles)
             if lost_response else observed_roles == expected_roles)},
        {"name": "actual-tools-observed",
         "passed": len(tool_actions) >= (3 if lost_response else 4)},
        {"name": "delivered-handoffs-observed",
         "passed": len(communication_actions) >= (1 if lost_response else 5)},
        {"name": "one-real-sandbox-order",
         "passed": bool(
             diagnostic and diagnostic["target_order_delta"] == 1)},
        {"name": "model-call-cap-enforced",
         "passed": bool(
             result and 0 < result["actual_model_calls"] <= 60)},
        {"name": (
             "no-final-output-fabricated-after-lost-response"
             if lost_response else "final-output-is-terminal-product"),
         "passed": (
             outcome.final_output == "" if lost_response else
             isinstance(outcome.final_output, str)
             and "Ethan Brooks" in outcome.final_output)},
        {"name": "not-an-official-score",
         "passed": (
             status["official_score"] is None
             and status["business_task_success"] is None)},
    ]
    if lost_response:
        checks.extend((
            {"name": "lost-response-fault-actually-triggered",
             "passed": any(
                 item.get("kind") == "fault_response_lost"
                 for item in witness)},
            {"name": "external-write-recorded-outcome-unknown",
             "passed": unknown_write_observed},
        ))
    report = {
        "schema_version": "rpnh-ha/scripted-driver-acceptance/v1",
        "execution_mode": "native_scripted",
        "fault_mode": fault.mode,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "run_id": run_id,
        "actual_model_calls": (
            result["actual_model_calls"] if result else None),
        "tool_observation_count": len(tool_actions),
        "communication_observation_count": len(communication_actions),
        "crosswalk": crosswalk,
        "monitor": monitor,
        "adapter_error": outcome.error,
        "official_scores": None,
        "external_model_calls": 0,
        "note": (
            "Deterministic native-driver acceptance only; this is not an "
            "official HarnessAudit benchmark score."),
    }
    write_new(output / "acceptance_report.json", report)
    return report


def run(**kwargs) -> dict:
    return asyncio.run(run_scripted_acceptance(**kwargs))


__all__ = ("run", "run_scripted_acceptance")
