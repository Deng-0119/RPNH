#!/usr/bin/env python3
"""Inspect the retained pilot result or enter the installed adapter CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "results" / "stratified-pilot-first-attempt-20261002.json"


def retained_result() -> dict:
    return json.loads(RESULT.read_text(encoding="utf-8"))


def show_results(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Inspect the retained 18-task pilot")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = retained_result()
    scored = [row for row in result["results"] if row["task_completed_correctly"] is not None]
    payload = {
        "scope": "score-blind 18-task stratified pilot; not the public-600 score",
        "planned": result["planned"],
        "finished": result["finished"],
        "infrastructure_successes": result["infrastructure_successes"],
        "scored": len(scored),
        "strict_passes": result["benchmark_task_successes"],
        "strict_first_attempt_rate_all_planned": result["benchmark_task_successes"] / result["planned"],
        "scored_subset_rate": result["benchmark_task_successes"] / len(scored),
        "mean_partial_credit_scored": sum(row["partial_credit"] for row in scored) / len(scored),
        "actual_model_calls": result["actual_model_calls"],
        "successful_tool_dispatches": result["successful_tool_dispatches"],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("AutomationBench retained stratified pilot (2026-10-02)")
        print(f"  strict first attempts: {payload['strict_passes']}/{payload['planned']}")
        print(f"  scored attempts: {payload['scored']}/{payload['planned']}")
        print(f"  mean partial credit (scored): {payload['mean_partial_credit_scored']:.10f}")
        print(f"  model calls: {payload['actual_model_calls']}")
        print(f"  successful tool dispatches: {payload['successful_tool_dispatches']}")
        print("  scope: not a public-600 score or a matched leaderboard comparison")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "results":
        return show_results(argv[1:])
    # Runtime commands need an installed editable package so the worker can
    # resolve the rpnh.plugins entry point. Keeping this import here lets the
    # checked-in result remain inspectable with isolated Python.
    sys.path.insert(0, str(ROOT / "src"))
    from rpnh_ab.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
