"""Frozen, ordered subsets of an AutomationBench split."""
from __future__ import annotations

from pathlib import Path

from .io import file_sha, load, sha


COHORT_SCHEMA = "rpnh-ab-local/stratified-pilot-plan/v1"


def plan_for_cohort(upstream, source: Path) -> dict:
    """Resolve a checked-in cohort once and retain its exact task contracts."""
    source = source.resolve()
    document = load(source)
    if document.get("schema") != COHORT_SCHEMA:
        raise ValueError(f"unsupported cohort schema: {document.get('schema')!r}")
    split = document.get("split")
    if split not in {"public", "simple"}:
        raise ValueError("cohort split must be public or simple")
    selected = document.get("selected_tasks")
    if not isinstance(selected, list) or not selected:
        raise ValueError("cohort must contain a non-empty selected_tasks list")
    cases = {case["id"]: case for case in upstream.cases(split)}
    task_ids = []
    tasks = []
    for position, selected_task in enumerate(selected, 1):
        if not isinstance(selected_task, dict):
            raise ValueError("cohort selected task is not an object")
        if selected_task.get("sample_index") != position:
            raise ValueError("cohort selected_tasks order/sample_index drift")
        task_id = selected_task.get("task_id")
        if not isinstance(task_id, str) or task_id in task_ids:
            raise ValueError("cohort has a missing or duplicate task_id")
        case = cases.get(task_id)
        if case is None:
            raise ValueError(f"cohort task is absent from pinned upstream: {task_id}")
        for field in ("domain", "task_name"):
            if selected_task.get(field) != case[field]:
                raise ValueError(f"cohort {task_id} {field} differs from pinned upstream")
        task_ids.append(task_id)
        tasks.append({key: value for key, value in case.items() if key != "row"})
    return {
        "schema": "rpnh-ab/cohort-plan/v1",
        "condition_id": "ab-frozen-cohort-rpnh-api-no-cumulative-quota-v1",
        "split": split,
        "upstream_commit": upstream.identity["commit"],
        "repetitions": 1,
        "selection": "frozen-cohort-task-id-order",
        "cohort": {
            "source_schema": document["schema"],
            "source_path": str(source),
            "source_sha256": file_sha(source),
            "selected_task_ids": task_ids,
        },
        "tasks": tasks,
        "manifest_sha256": sha(tasks),
    }


def resolve_frozen_cases(upstream, plan: dict) -> list[dict]:
    """Return only the plan's ordered cases, rejecting all resolution drift."""
    if plan.get("upstream_commit") != upstream.identity["commit"]:
        raise ValueError("frozen cohort upstream commit drift")
    cases = {case["id"]: case for case in upstream.cases(plan["split"])}
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("frozen plan has no tasks")
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)) or any(not isinstance(task_id, str) for task_id in ids):
        raise ValueError("frozen plan has missing or duplicate task ids")
    resolved = []
    for task in tasks:
        case = cases.get(task["id"])
        if case is None:
            raise ValueError(f"frozen task is absent from pinned upstream: {task['id']}")
        frozen = {key: value for key, value in case.items() if key != "row"}
        if frozen != task:
            raise ValueError(f"frozen task contract drift: {task['id']}")
        resolved.append(case)
    if plan.get("manifest_sha256") != sha(tasks):
        raise ValueError("frozen task manifest digest drift")
    if plan.get("schema") == "rpnh-ab/cohort-plan/v1":
        cohort = plan.get("cohort") or {}
        if cohort.get("selected_task_ids") != ids:
            raise ValueError("frozen cohort order drift")
    return resolved
