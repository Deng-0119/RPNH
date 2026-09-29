"""Run the prepared JB task with a user-selected exact profile."""
from __future__ import annotations

import argparse
from pathlib import Path

from examples._support.live_workflow import run_designer_workflow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-participant-data-transfer",
        action="store_true",
        required=True,
        help=(
            "confirm that the prepared participant-level task may be stored "
            "in its Registry and sent through the selected execution route"),
    )
    args = parser.parse_args()
    prompt = args.input_dir.resolve() / "prompt.txt"
    if not prompt.is_file():
        raise ValueError(
            "--input-dir must be produced by prepare_inputs.py")
    run_designer_workflow(
        execution=args.execution,
        session_dir=args.session_dir,
        task_text=prompt.read_text(encoding="utf-8"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
