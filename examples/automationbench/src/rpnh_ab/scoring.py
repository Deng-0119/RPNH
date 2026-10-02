"""Offline official scoring; execution and grading errors stay distinguishable."""
from __future__ import annotations
from pathlib import Path
from .io import file_sha, load, now, write_new


def score_attempt(attempt: Path, score_function, *, revision: bool = False) -> dict:
    scores = sorted(attempt.glob("score-*.json"))
    if scores and not revision:
        return load(scores[-1])
    final = attempt / "final_world.json"
    contract = attempt / "scoring_input.json"
    if not final.is_file() or not contract.is_file():
        raise ValueError("no frozen final-world evidence; do not score a live checkpoint")
    frozen = load(contract)
    envelope = {"schema": "rpnh-ab/score/v1", "at": now(),
                "final_world_sha256": file_sha(final), "scoring_input_sha256": file_sha(contract),
                "revision": len(scores)+1}
    try:
        result = score_function(load(final), frozen["initial_state"], frozen["info"])
        envelope.update(status="scored", **result)
    except Exception as exc:
        envelope.update(status="score_error", error=type(exc).__name__ + ": " + str(exc),
                        partial_credit=None, task_completed_correctly=None)
    write_new(attempt / f"score-{len(scores)+1:04d}.json", envelope)
    return envelope


def summarize(plan: dict, work: Path) -> dict:
    tasks, missing, unscored, passes, partials, rows = plan["tasks"], [], [], 0, [], []
    for task in tasks:
        attempt = work / "attempts" / task["id"] / "a0001"
        if not (attempt / "attempt.json").is_file():
            missing.append(task["id"])
            rows.append({"id": task["id"], "domain": task["domain"], "status": "not_attempted"})
            continue
        scores = sorted(attempt.glob("score-*.json"))
        score = load(scores[-1]) if scores else None
        lifecycle = load(attempt / "lifecycle.json") if (attempt / "lifecycle.json").is_file() else {}
        row = {"id": task["id"], "domain": task["domain"], "task_name": task["task_name"],
               "attempt": "a0001", "execution_status": lifecycle.get("execution_status", "incomplete_evidence"),
               "status": score.get("status") if score else "not_scored",
               "partial_credit": score.get("partial_credit") if score else None,
               "task_completed_correctly": score.get("task_completed_correctly") if score else None}
        rows.append(row)
        if not score or score.get("status") != "scored":
            unscored.append(task["id"])
        else:
            passes += int(score["task_completed_correctly"] == 1.0)
            partials.append(score["partial_credit"])
    all_scored = bool(tasks) and not missing and not unscored and len(partials) == len(tasks)
    domain_results = {}
    for domain in dict.fromkeys(task["domain"] for task in tasks):
        selected = [r for r in rows if r["domain"] == domain]
        scored = [r for r in selected if r["status"] == "scored"]
        domain_results[domain] = {"planned": len(selected), "scored": len(scored),
           "passes": sum(r["task_completed_correctly"] == 1.0 for r in scored),
           "pass_rate": (sum(r["task_completed_correctly"] == 1.0 for r in scored)/len(selected)
                         if len(scored) == len(selected) and selected else None)}
    return {"schema": "rpnh-ab/summary/v1", "at": now(), "split": plan["split"],
            "planned_tasks": len(tasks), "scored_tasks": len(partials), "passes": passes,
            "not_attempted": missing, "unscored": unscored, "all_scores_present": all_scored,
            "benchmark_pass_rate": passes/len(tasks) if all_scored and plan["split"] == "public" else None,
            "full_batch_mean_partial_credit": sum(partials)/len(tasks) if all_scored else None,
            "observed_scored_subset_pass_rate": passes/len(partials) if partials else None,
            "headline_is_complete": all_scored and plan["split"] == "public" and len(tasks) == 600,
            "domain_results": domain_results, "tasks": rows,
            "comparison": "descriptive_only; upstream public rows are not matched local controls",
            "selection": "all first attempts; no best-of selection; latest retained scoring revision"}
