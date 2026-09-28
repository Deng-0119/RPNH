"""Process entry for one independent RPNH agent task object."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .agent_tasks import (
    AgentTaskSpec, reopen_agent_task, resume_agent_task, run_agent_task,
)
from .task_control import claim_task_worker_launch


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    resume = bool(arguments and arguments[0] == "--resume")
    if resume:
        arguments = arguments[1:]
    checkpoint_version_id = None
    reopen_command_id = None
    reopen_reason = None
    if arguments and arguments[0] == "--reopen":
        if resume or len(arguments) < 4:
            raise SystemExit(
                "--reopen requires CHECKPOINT COMMAND_ID REASON and excludes --resume")
        checkpoint_version_id = arguments[1]
        reopen_command_id = arguments[2]
        reopen_reason = arguments[3]
        arguments = arguments[4:]
    launch_lock_fd = None
    if arguments and arguments[0] == "--launch-lock-fd":
        if len(arguments) < 2:
            raise SystemExit(
                "--launch-lock-fd requires one descriptor number")
        try:
            launch_lock_fd = int(arguments[1])
        except ValueError as exc:
            raise SystemExit(
                "--launch-lock-fd requires one descriptor number") from exc
        if launch_lock_fd < 0:
            raise SystemExit(
                "--launch-lock-fd requires one descriptor number")
        arguments = arguments[2:]
    if len(arguments) != 1:
        raise SystemExit(
            "usage: python -m cpn.rpnh.task_worker [--resume] "
            "[--reopen CHECKPOINT COMMAND_ID REASON] "
            "[--launch-lock-fd FD] TASK_SPEC.json")
    path = Path(arguments[0])
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        spec = AgentTaskSpec.from_worker_document(
            document, document_root=path.resolve().parent)
        launch_claim = claim_task_worker_launch(
            path, spec, resume=resume,
            checkpoint_version_id=checkpoint_version_id,
            reopen_command_id=reopen_command_id,
            reopen_reason=reopen_reason,
            inherited_lock_fd=launch_lock_fd)
        if launch_claim is None:
            return 3
        result = (
            reopen_agent_task(
                spec, checkpoint_version_id=checkpoint_version_id,
                command_id=reopen_command_id, reason=reopen_reason)
            if reopen_command_id is not None else
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
