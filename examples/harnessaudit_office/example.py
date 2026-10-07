#!/usr/bin/env python3
"""Office example: inspect historical results or launch explicit native operations.

No model call occurs on import, --help, results, or configure.
Live execution/scoring delegates to the retained adapter; there is no agent loop here.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
TASKS = ("off-t1", "off-t3", "off-t4", "off-t5", "off-t6")


def history_rows(path: Path | None = None) -> list[dict[str, str]]:
    with (path or ROOT / "results/source_slot_scores.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {"source_slot", "task_id", "score_run_id", "condition_id", "projection_revision", "tcr", "avs", "sar_avg"}
    if not rows or not required <= set(rows[0]):
        raise ValueError("historical score table is absent or has an invalid header")
    if len({r["source_slot"] for r in rows}) != len(rows):
        raise ValueError("duplicate source slot in historical score table")
    for row in rows:
        for field in ("tcr", "avs", "sar_avg"):
            value = Decimal(row[field])
            if not value.is_finite() or not 0 <= value <= 1:
                raise ValueError(f"invalid historical {field}")
    return rows


def result_groups(rows: list[dict[str, str]]) -> list[dict]:
    # Do not pool original limited runs and unmetered supplemental executions.
    buckets: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        buckets[row["task_id"], row["condition_id"]].append(row)
    result = []
    for (task, condition), group in sorted(buckets.items()):
        result.append({"task_id": task, "condition_id": condition, "n": len(group),
                       "projection_revisions": sorted({r["projection_revision"] for r in group}),
                       **{field: str(sum((Decimal(r[field]) for r in group), Decimal(0)) / len(group))
                          for field in ("tcr", "avs", "sar_avg")}})
    return result


def show_results(as_json: bool = False) -> int:
    groups = result_groups(history_rows())
    if as_json:
        print(json.dumps({"scope": "Five-task Office subset; mixed conditions kept separate", "groups": groups}, indent=2))
    else:
        print("Historical Office results: 15 scored slots, 20 retained execution attempts.")
        print("No model calls. Not the full benchmark, a success-rate estimate, or a kernel-only score.\n")
        print("task    condition     n    TCR      AVS      SAR")
        for group in groups:
            label = "unmetered" if "unmetered" in group["condition_id"] else "original"
            print(f"{group['task_id']:7} {label:11} {group['n']:2} " + " ".join(f"{Decimal(group[k]):.4f}" for k in ("tcr", "avs", "sar_avg")))
        print("\nPer-row source/projection identity: results/source_slot_scores.csv")
        print("All attempts, including quota-truncated records: results/attempts.csv")
        print("Read RESULTS.md / RESULTS_ZH.md before interpreting these numbers.")
    return 0


def configure(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    document = {
        "schema_version": "rpnh-ha/local-experiment/v3",
        "task_id": args.task_id,
        "condition_id": "office-example-unmetered-v1",
        "authorized": args.authorize,
        "limits_per_run": {"max_model_calls": None, "max_tool_calls": None, "max_seconds": None},
        "cost_policy": {"mode": "no_cap", "owner_approved_cost_cap": None},
        "execution": {
            "authorized": args.authorize, "route": "local_process",
            "profile_path": str(args.executor_profile.expanduser().resolve()),
            "exact_model": args.executor_model, "reasoning_effort": args.executor_effort,
        },
        "scoring": {
            "authorized": args.authorize, "route": "local_process",
            "adapter_path": str(args.judge_adapter.expanduser().resolve()),
            "exact_model": args.judge_model, "reasoning_effort": args.judge_effort,
            "max_output_tokens": 512, "transport_status": "ready",
        },
    }
    condition = getattr(args, "configuration_condition", None)
    if condition:
        document["configuration_condition"] = condition
        document["condition_id"] = condition
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Configuration written: {output}; model calls: 0; authorized: {args.authorize}")
    print("Readiness checks inspect configured routes; they do not prove a live provider is reachable.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("results", help="read the bundled historical results without installing dependencies")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("configure", help="write a local configuration; no model call")
    p.add_argument("--task-id", choices=TASKS, default="off-t1")
    p.add_argument("--executor-profile", type=Path, required=True)
    p.add_argument("--executor-model", required=True)
    p.add_argument("--executor-effort", required=True)
    p.add_argument("--judge-adapter", type=Path, required=True)
    p.add_argument("--judge-model", required=True)
    p.add_argument("--judge-effort", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--authorize", action="store_true", help="mark these user-selected routes authorized; still makes no call")
    p.add_argument("--configuration-condition", choices=("office-public-discovery-workflow-v1", "office-public-discovery-readback-v2"),
                   help="explicit opt-in public discovery/evidence/action/readback condition; baseline remains the default")
    p = sub.add_parser("prepare", help="save only the selected task's model-visible inputs; no model call")
    p.add_argument("--task-id", choices=TASKS, default="off-t1")
    p.add_argument("--audit-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("check-config", help="inspect execution/scoring readiness separately; no model call")
    p.add_argument("--config", type=Path, required=True)
    p = sub.add_parser("plan-condition", help="freeze a comparison plan from saved public input; no model/backend/Registry")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--public-input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    for name, help_text in (("run", "run one fresh model-backed task"), ("score", "score an existing execution; may call judge models")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", type=Path, required=True)
        p.add_argument("--audit-root", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        if name == "score":
            p.add_argument("--run-root", type=Path, required=True)
    p = sub.add_parser("reproject", help="re-export existing Registry evidence; no executor or judge calls")
    p.add_argument("--run-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "results":
            return show_results(args.json)
        if args.command == "configure":
            return configure(args)
        sys.path.insert(0, str(ROOT / "src"))
        if hasattr(args, "audit_root"):
            audit_root = args.audit_root.expanduser().resolve()
            if not (audit_root / "multi_agent/loader.py").is_file():
                raise ValueError("audit-root must point to the installed HarnessAudit source checkout")
            sys.path.insert(0, str(audit_root))
        from rpnh_ha.cli import main as bridge_main
        names = {"prepare": "prepare", "check-config": "live-readiness", "run": "live-execute", "score": "score-full", "reproject": "reproject-live", "plan-condition": "plan-condition"}
        forwarded = [names[args.command]]
        for field in ("task_id", "config", "audit_root", "run_root", "output", "public_input"):
            value = getattr(args, field, None)
            if value is not None:
                forwarded.extend(("--" + field.replace("_", "-"), str(value)))
        return bridge_main(forwarded)
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
