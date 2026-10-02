"""Append read-only native evidence revisions without invoking any model/tool."""
from __future__ import annotations
from pathlib import Path
from .evidence import crosscheck
from .io import load, now, replace_checkpoint, write_new
from .drivers import get_driver


def reproject_batch(work: Path) -> dict:
    from .experiment import installed_rpnh_identity
    condition = load(work / "conditions.json")
    if installed_rpnh_identity() != condition["rpnh"]:
        raise ValueError("use the original isolated RPNH runtime for evidence reprojection")
    items = []
    for task in load(work / "plan.json")["tasks"]:
        attempt = work / "attempts" / task["id"] / "a0001"
        if not (attempt / "lifecycle.json").is_file():
            items.append({"id": task["id"], "status": "not_reprojected", "reason": "no terminal lifecycle evidence"})
            continue
        lifecycle = load(attempt / "lifecycle.json")
        if not lifecycle.get("host_quiescent") or not lifecycle.get("world_owner_quiescent"):
            items.append({"id": task["id"], "status": "not_reprojected", "reason": "worker exit is not confirmed"})
            continue
        revisions = attempt / "projection-revisions"
        revisions.mkdir(exist_ok=True)
        number = len(list(revisions.glob("r[0-9]*"))) + 1
        output = revisions / f"r{number:04d}"
        output.mkdir(exist_ok=False)
        try:
            host=lifecycle.get('executor_host','native')
            run_dir=Path(lifecycle.get('registry_path',str(attempt/host)))
            if host=='dsh':run_dir=Path(lifecycle.get('raw_host_result',str(attempt/'dsh/host-result.json'))).parent
            facts = get_driver(host).project(run_dir, output)
            write_new(output / (host + "_evidence.json"), facts)
            check = crosscheck(attempt, output / "registry_objects.jsonl")
            write_new(output / "bridge_registry_check.json", check)
            record = {"id": task["id"], "status": "projected", "path": str(output.relative_to(attempt))}
        except Exception as exc:
            record = {"id": task["id"], "status": "projection_error", "error": type(exc).__name__ + ": " + str(exc)}
            write_new(output / "error.json", record)
        replace_checkpoint(attempt / "latest_projection.json", record)
        items.append(record)
    return {"schema": "rpnh-ab/reprojection/v1", "at": now(), "model_calls": 0,
            "business_tool_executions": 0, "results": items}
