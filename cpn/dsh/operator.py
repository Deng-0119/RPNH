"""One-shot DSH transport for the original TaskControl, not a second runtime.

The owner chooses the control root on the command line. Model input can select
only an existing task and an explicit operation; no paths, imports or executable
commands are accepted over stdin. Importing or inspecting never recovers pending
launches. There is deliberately no automatic retry or fresh-task creation here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from cpn.rpnh.task_control import TaskControl

PROTOCOL = "rpnh/dsh-operator/v1"
READ_OPERATIONS = frozenset({"list", "status", "result", "net", "checkpoints"})
CONTROL_OPERATIONS = frozenset({"stop", "resume", "reopen"})


def validate_request(request: Any) -> tuple[str, dict[str, str]]:
    if type(request) is not dict or set(request) != {"operation", "arguments"}:
        raise ValueError("request requires exactly operation and arguments")
    operation, arguments = request["operation"], request["arguments"]
    if type(operation) is not str or operation not in READ_OPERATIONS | CONTROL_OPERATIONS:
        raise ValueError("unknown operation")
    required = set() if operation == "list" else {"task_id"}
    if operation == "reopen":
        required |= {"checkpoint_version_id", "reason"}
    if type(arguments) is not dict or set(arguments) != required:
        raise ValueError("arguments do not match the operation's exact fields")
    for key, value in arguments.items():
        if type(value) is not str or not value.strip() or value != value.strip():
            raise ValueError(f"{key} requires nonempty trimmed text")
    return operation, arguments


def dispatch(control: TaskControl, operation: str, arguments: dict[str, str]) -> Any:
    """Use a closed mapping rather than arbitrary method dispatch."""
    if operation == "list":
        return list(control.list())
    task_id = arguments["task_id"]
    if operation == "status":
        return control.status(task_id)
    if operation == "result":
        return control.result(task_id)
    if operation == "net":
        return control.net(task_id)
    if operation == "checkpoints":
        return control.checkpoints(task_id)
    if operation == "stop":
        return control.stop(task_id)
    if operation == "resume":
        return control.resume(task_id)
    if operation == "reopen":
        return control.reopen(task_id, arguments["checkpoint_version_id"], reason=arguments["reason"])
    raise ValueError("unknown operation")


def invoke(root: Path, request: Any, *, allow_control: bool = False) -> dict[str, Any]:
    operation, arguments = validate_request(request)
    if operation in CONTROL_OPERATIONS and not allow_control:
        raise ValueError("control operations are not exposed by this operator configuration")
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("control root must be an existing absolute directory")
    # This flag is essential: construction otherwise replays pending launches.
    control = TaskControl(root, recover_pending_launches=False)
    return {"protocol": PROTOCOL, "operation": operation,
            "result": dispatch(control, operation, arguments)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--allow-control", action="store_true")
    options = parser.parse_args(argv)
    try:
        request = json.load(sys.stdin)
        value = invoke(options.control_root, request, allow_control=options.allow_control)
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        # No traceback or recovery dispatch. A failed control call can have
        # crossed the native durable-intent boundary: clients must inspect it.
        print(json.dumps({"protocol": PROTOCOL, "error": type(exc).__name__,
                          "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

