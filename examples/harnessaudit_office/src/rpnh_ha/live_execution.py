"""One explicitly authorized native executor run; scoring remains separate."""
from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

from .adapter import RPNHHarnessAuditAdapter
from .driver_contract import LiveLimits, live_limits_from_config
from .jsonio import read, write_new
from .local_driver import LocalNativeDriver
from .readiness import inspect_live_readiness, load_experiment
from .upstream import load_case, office_bank_factory, official_dispatch


def _limits(config: dict) -> LiveLimits:
    return live_limits_from_config(config)


async def execute_live_canary(
        *, config_path: Path, audit_root: Path, output: Path) -> dict:
    config_path = Path(config_path).resolve()
    audit_root = Path(audit_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("live execution requires a fresh output root")
    readiness = inspect_live_readiness(config_path)
    if not readiness["execution_ready"]:
        raise RuntimeError(
            "executor gate is not ready: "
            + ", ".join(readiness["execution_blocking"]))
    config = load_experiment(config_path)
    execution = config["execution"]
    profile = Path(execution["profile_path"]).expanduser()
    if not profile.is_absolute():
        profile = config_path.parent / profile
    profile = profile.resolve()
    limits = _limits(config)

    output.mkdir(parents=True)
    write_new(output / "experiment_config.snapshot.json", config)
    write_new(output / "readiness.snapshot.json", readiness)
    task, catalog, public = load_case(audit_root, config["task_id"])
    factory = office_bank_factory(audit_root)
    bank = factory()
    run_id = "rpnh-office-" + config["task_id"] + "-" + uuid.uuid4().hex[:12]
    try:
        if callable(getattr(bank, "apply_task_metadata", None)):
            bank.apply_task_metadata(task.metadata)
        from multi_agent.frameworks.core.action_sink import ActionSink
        from multi_agent.frameworks.core.base import RunContext

        sink = ActionSink(run_id=run_id)
        context = RunContext(
            run_id=run_id,
            task=task,
            catalog=catalog,
            model=execution["exact_model"],
            max_turns=limits.max_model_calls,
            bank=bank,
            workspace=None,
            action_sink=sink,
        )
        adapter = RPNHHarnessAuditAdapter(
            driver=LocalNativeDriver(),
            root=output / "adapter-run",
            execution_profile=profile,
            limits=limits,
            bank_factory=factory,
            dispatch=official_dispatch,
        )
        outcome = await adapter.run(context, public.as_dict()["goal"])
    finally:
        bank.close()

    run_root = output / "adapter-run"
    status = read(run_root / "run_status.json")
    result_path = run_root / "driver_result.json"
    result = read(result_path) if result_path.is_file() else None
    monitor_path = run_root / "rpnh-monitor.json"
    monitor = read(monitor_path) if monitor_path.is_file() else None
    completed = bool(
        outcome.error is None
        and result is not None
        and status.get("trace_complete") is True
    )
    report = {
        "schema_version": "rpnh-ha/live-execution/v1",
        "run_id": run_id,
        "task_id": config["task_id"],
        "execution_completed": completed,
        "adapter_error": outcome.error,
        "execution_mode": status.get("execution_mode"),
        "native_terminal_present": status.get("native_terminal_present"),
        "trace_complete": status.get("trace_complete"),
        "stop_reason": result.get("stop_reason") if result else None,
        "actual_model_calls": (
            result.get("actual_model_calls") if result else None),
        "model_identity": result.get("model_identity") if result else None,
        "selected_model": execution["exact_model"],
        "selected_reasoning_effort": execution["reasoning_effort"],
        "selected_route": execution["route"],
        "limits": limits.__dict__,
        "monitor": monitor,
        "official_scores": None,
        "scoring_pending": True,
        "note": (
            "Executor canary only. Completion and benchmark scores require "
            "the separately configured upstream judge stage."),
    }
    write_new(output / "live_execution_report.json", report)
    return report


def run(**kwargs) -> dict:
    return asyncio.run(execute_live_canary(**kwargs))


__all__ = ("execute_live_canary", "run")
