"""Run one provider-backed example through a Designer-authored workflow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cpn.rpnh.main_session import MainSession


def run_designer_workflow(
        *, execution: Path, session_dir: Path, task_text: str,
) -> dict[str, Any]:
    """Create one main session, let its Designer build the graph, and wait.

    The caller supplies the exact execution selection.  This helper never
    chooses, changes, or retries a provider/model route.
    """
    execution = execution.resolve()
    session_dir = session_dir.resolve()
    if not execution.is_file():
        raise ValueError("--execution must name an existing execution selection")
    if session_dir.exists():
        raise ValueError("--session-dir must be absent for a fresh example")
    if not isinstance(task_text, str) or not task_text.strip():
        raise ValueError("example task text must be nonempty")

    session = MainSession(session_dir, execution)
    decision, handle = session.turn(
        task_text, required_task_kind="workflow")
    if handle is None or handle.kind != "workflow":
        raise RuntimeError("main Designer did not launch the required workflow")

    print(json.dumps({
        "event": "workflow_launched",
        "task_id": handle.task_id,
        "run_dir": str(handle.run_dir),
        "designer_reply": decision.reply,
    }, ensure_ascii=False, indent=2))

    try:
        return_code = handle.process.wait()
    except KeyboardInterrupt:
        stop = session.task_control.stop(handle.task_id, startup_safe=True)
        print(json.dumps({
            "event": "owner_stop_requested",
            "task_id": handle.task_id,
            "session_dir": str(session_dir),
            "control_result": stop,
            "next": (
                "Resume the owning session and inspect task status before "
                "using /task ID resume."),
        }, ensure_ascii=False, indent=2))
        raise

    status = session.task_control.status(handle.task_id)
    registry = status.get("registry") or {}
    if return_code != 0 or registry.get("execution_status") != "terminal":
        raise RuntimeError(json.dumps({
            "task_id": handle.task_id,
            "return_code": return_code,
            "registry": registry,
            "log_path": status.get("log_path"),
        }, ensure_ascii=False, sort_keys=True))

    result = session.task_control.result(handle.task_id)
    if result["run_outcome"] != "complete":
        raise RuntimeError(json.dumps({
            "task_id": handle.task_id,
            "run_outcome": result["run_outcome"],
            "terminal_evidence_ref": result.get("terminal_evidence_ref"),
        }, ensure_ascii=False, sort_keys=True))
    projection = session.task_control.net(handle.task_id)
    summary = {
        "event": "workflow_terminal",
        "task_id": handle.task_id,
        "run_dir": str(handle.run_dir),
        "registry_status": registry.get("execution_status"),
        "run_outcome": result["run_outcome"],
        "actual_model_call_counts": result["actual_model_call_counts"],
        "petri_net": projection["summary"],
        "result": result["output"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary
