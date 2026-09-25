"""Process entry for one independent RPNH agent task object."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .agent_tasks import AgentTaskSpec, resume_agent_task, run_agent_task
from .task_control import claim_task_worker_launch


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    resume = bool(arguments and arguments[0] == "--resume")
    if resume:
        arguments = arguments[1:]
    if len(arguments) != 1:
        raise SystemExit(
            "usage: python -m cpn.rpnh.task_worker [--resume] TASK_SPEC.json")
    path = Path(arguments[0])
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        spec = AgentTaskSpec.from_worker_document(document)
        launch_claim = claim_task_worker_launch(
            path, spec, resume=resume)
        if launch_claim is None:
            return 3
        result = (
            resume_agent_task(spec) if resume else run_agent_task(spec))
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({
            "schema_version": "rpnh/agent_task_worker_error/v1",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["terminal_evidence_ref"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
