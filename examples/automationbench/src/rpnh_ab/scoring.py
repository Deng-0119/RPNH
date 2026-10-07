"""Offline official scoring; execution and grading errors stay distinguishable."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from .io import file_sha, load, now, write_new


REQUIRED_ATTEMPT_FILES = ("attempt.json", "task_contract.json", "scoring_input.json",
                          "final_world.json", "lifecycle.json")


def attempt_eligibility(attempt: Path, task: dict | None = None, score: dict | None = None) -> tuple[bool, str | None]:
    """Validate the durable evidence required to score or report an attempt."""
    missing = [name for name in REQUIRED_ATTEMPT_FILES if not (attempt / name).is_file()]
    if missing:
        return False, "missing required evidence: " + ", ".join(missing)
    attempt_record = load(attempt / "attempt.json")
    lifecycle = load(attempt / "lifecycle.json")
    task_id, contract = attempt_record.get("id"), attempt_record.get("task_contract_sha256")
    contract_file = attempt_record.get("task_contract_file_sha256")
    if (not isinstance(task_id, str) or not isinstance(contract, str)
            or not isinstance(contract_file, str)):
        return False, "attempt identity/contract is incomplete"
    if file_sha(attempt / "task_contract.json") != contract_file:
        return False, "task contract file differs from attempt creation"
    if task is not None and (task_id != task.get("id") or contract != task.get("task_contract_sha256")):
        return False, "attempt task identity or contract differs from frozen plan"
    for field, expected in (("admitted", True), ("execution_status", "host_terminal"),
                            ("host_quiescent", True), ("world_owner_quiescent", True)):
        if lifecycle.get(field) != expected:
            return False, f"lifecycle is not eligible: {field}"
    if score is not None:
        if score.get("configuration_condition") != attempt_record.get("configuration_condition"):
            return False, "score configuration condition is stale"
        if score.get("task_id") != task_id or score.get("task_contract_sha256") != contract:
            return False, "score task identity or contract is stale"
        if score.get("final_world_sha256") != file_sha(attempt / "final_world.json"):
            return False, "score final-world input hash is stale"
        if score.get("scoring_input_sha256") != file_sha(attempt / "scoring_input.json"):
            return False, "score scoring-input hash is stale"
        if score.get("task_contract_file_sha256") != file_sha(attempt / "task_contract.json"):
            return False, "score task-contract file hash is stale"
    return True, None


def score_attempt(attempt: Path, score_function, *, revision: bool = False, task: dict | None = None) -> dict:
    scores = sorted(attempt.glob("score-*.json"))
    if scores:
        existing = load(scores[-1])
        eligible, reason = attempt_eligibility(attempt, task, existing)
        if not eligible:
            raise ValueError("existing score is ineligible: " + str(reason))
        if not revision:
            return existing
    eligible, reason = attempt_eligibility(attempt, task)
    if not eligible:
        raise ValueError("attempt is not eligible for scoring: " + str(reason))
    final, contract = attempt / "final_world.json", attempt / "scoring_input.json"
    frozen, attempt_record = load(contract), load(attempt / "attempt.json")
    envelope = {"schema": "rpnh-ab/score/v1", "at": now(), "task_id": attempt_record["id"],
                "task_contract_sha256": attempt_record["task_contract_sha256"],
                "final_world_sha256": file_sha(final), "scoring_input_sha256": file_sha(contract),
                "task_contract_file_sha256": file_sha(attempt / "task_contract.json"),
                "revision": len(scores)+1}
    if attempt_record.get("configuration_condition") is not None:
        envelope["configuration_condition"] = attempt_record["configuration_condition"]
    try:
        result = score_function(load(final), frozen["initial_state"], frozen["info"])
        envelope.update(status="scored", **result)
    except Exception as exc:
        envelope.update(status="score_error", error=type(exc).__name__ + ": " + str(exc),
                        partial_credit=None, task_completed_correctly=None)
    write_new(attempt / f"score-{len(scores)+1:04d}.json", envelope)
    return envelope


def initial_world_summary(attempt: Path) -> dict:
    """Optional provenance diagnostic; never changes score eligibility.

    Project only hashes, source identities and the materialized clock. Full
    worlds, service lists, prompts and arbitrary provenance fields stay private.
    Older attempts cannot be reconstructed and are explicitly unknown.
    """
    path = attempt / "initial_world_provenance.json"
    if not path.is_file():
        return {"status": "unknown"}

    def digest(value, length=64):
        return value if isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) else None

    try:
        provenance = load(path)
        if provenance.get("schema") != "rpnh-ab/initial-world/v1":
            return {"status": "unknown"}
        sources = provenance["sources"]
        clock = provenance.get("current_time")
        # Only a timestamp is public metadata, never arbitrary world text.
        if clock is not None:
            datetime.fromisoformat(clock)
        initial = attempt / "world.initial.materialized.json"
        record = load(attempt / "attempt.json")
        matches = (initial.is_file()
                   and provenance.get("world_file") == initial.name
                   and provenance.get("capture_stage") == "before_host_start_and_tool_dispatch"
                   and provenance.get("clock_policy") == "record_upstream_materialized_value_unchanged"
                   and initial.stat().st_size == provenance.get("world_byte_size")
                   and file_sha(initial) == provenance.get("world_sha256")
                   and file_sha(attempt / "attempt.json") == provenance.get("attempt_file_sha256")
                   and file_sha(attempt / "task_contract.json") == provenance.get("task_contract_file_sha256")
                   and record.get("id") == provenance.get("task_id")
                   and record.get("attempt") == provenance.get("attempt")
                   and record.get("task_contract_sha256") == provenance.get("task_contract_sha256")
                   and record.get("initial_evidence_sources") == sources
                   and load(initial).get("meta", {}).get("current_time") == clock)
        return {"status": "captured" if matches else "mismatch",
                "hash_scope": "retained_private_evidence",
                "world_sha256": digest(provenance.get("world_sha256")),
                "provenance_sha256": file_sha(path),
                "attempt_file_sha256": digest(provenance.get("attempt_file_sha256")),
                "task_contract_file_sha256": digest(provenance.get("task_contract_file_sha256")),
                "current_time": clock,
                "sources": {"upstream_pinned_commit": digest(sources.get("upstream_pinned_commit"), 40),
                            "upstream_commit": digest(sources.get("upstream_commit"), 40),
                            "adapter_sources_sha256": digest(sources.get("adapter_sources_sha256"))}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # An optional observation gap must not invalidate an otherwise eligible
        # historical score or leak private parse/error details into a summary.
        return {"status": "unavailable"}


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
        eligible, reason = attempt_eligibility(attempt, task, score) if score else attempt_eligibility(attempt, task)
        if load(attempt / "attempt.json").get("configuration_condition") != plan.get("configuration_condition"):
            eligible, reason = False, "attempt configuration condition differs from frozen plan"
        lifecycle = load(attempt / "lifecycle.json") if (attempt / "lifecycle.json").is_file() else {}
        row = {"id": task["id"], "domain": task["domain"], "task_name": task["task_name"], "attempt": "a0001",
               "execution_status": lifecycle.get("execution_status", "incomplete_evidence"),
               "status": score.get("status") if score and eligible else "ineligible_evidence" if not eligible else "not_scored",
               "partial_credit": score.get("partial_credit") if score and eligible else None,
               "task_completed_correctly": score.get("task_completed_correctly") if score and eligible else None}
        row["initial_world"] = initial_world_summary(attempt)
        if not eligible:
            row["eligibility_error"] = reason
        rows.append(row)
        if not score or not eligible or score.get("status") != "scored":
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
    report = {"schema": "rpnh-ab/summary/v1", "at": now(), "split": plan["split"],
            "planned_tasks": len(tasks), "scored_tasks": len(partials), "passes": passes,
            "not_attempted": missing, "unscored": unscored, "all_scores_present": all_scored,
            "benchmark_pass_rate": passes/len(tasks) if all_scored and plan["split"] == "public" else None,
            "full_batch_mean_partial_credit": sum(partials)/len(tasks) if all_scored else None,
            "observed_scored_subset_pass_rate": passes/len(partials) if partials else None,
            "headline_is_complete": all_scored and plan["split"] == "public" and len(tasks) == 600,
            "domain_results": domain_results, "tasks": rows,
            "comparison": "descriptive_only; upstream public rows are not matched local controls",
            "selection": "all first attempts; no best-of selection; latest eligible retained scoring revision"}

    if plan.get("configuration_condition") is not None:
        report["condition_id"] = plan["condition_id"]
        report["configuration_condition"] = plan["configuration_condition"]
        report["manifest_sha256"] = plan["manifest_sha256"]
    return report
