"""Run the public 3-DOF task with a user-selected exact profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from examples._support.live_workflow import run_designer_workflow


HERE = Path(__file__).resolve().parent


def task_text() -> str:
    return (
        (HERE / "task.md").read_text(encoding="utf-8")
        + "\n\n--- BEGIN REGISTERED PROBLEM ---\n"
        + json.dumps(json.loads((HERE / "problem.json").read_text(
            encoding="utf-8")), ensure_ascii=False, indent=2)
        + "\n--- END REGISTERED PROBLEM ---"
        + "\n\n--- BEGIN INDEPENDENT VERIFIER SOURCE ---\n"
        + (HERE / "verify_solution.py").read_text(encoding="utf-8")
        + "\n--- END INDEPENDENT VERIFIER SOURCE ---\n"
        + "Use this verifier source unchanged on the generated "
          "descent_solution.json and include its JSON result in the final "
          "engineering report."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    args = parser.parse_args()
    run_designer_workflow(
        execution=args.execution,
        session_dir=args.session_dir,
        task_text=task_text(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
