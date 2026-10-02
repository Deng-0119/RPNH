"""Compare environment witnesses with native registered return records.

A matching registered return does not prove a later model read. We deliberately
leave that stronger claim unmade; native request resources remain in the local run.
"""
from __future__ import annotations
import json
from pathlib import Path


def records(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def crosscheck(attempt: Path, registry_path: Path | None = None,
               registry_rows: list[dict] | None = None) -> dict:
    events = records(attempt / "tool_events.jsonl")
    requests = {x["sequence"]: x["request"] for x in events if x["kind"] == "dispatch_started"}
    returned = {x["sequence"]: x["response"] for x in events
                if x["kind"] == "dispatch_finished" and x["response"].get("ok") is True}
    if registry_rows is not None and registry_path is not None:
        raise ValueError("choose a Registry path or in-memory rows, not both")
    registry_path = registry_path or (attempt / "registry_objects.jsonl")
    rows = registry_rows if registry_rows is not None else records(registry_path)
    matched, mismatches, native_returned = set(), [], 0
    for row in rows:
        if row["object_type"] != "agent_action/v3":
            continue
        action = row["document"]
        if not str(action.get("selector", "")).startswith("ab_api/") or action.get("outcome") != "returned":
            continue
        native_returned += 1
        output = action.get("output")
        seq = output.get("witness_request_sequence") if isinstance(output, dict) else None
        response, request = returned.get(seq), requests.get(seq)
        if (response is None or request is None or output.get("raw_result") != response["result"]
            or action.get("arguments") != request["arguments"]
            or action.get("selector") != "ab_api/" + request["tool"]):
            mismatches.append({"version_id": row["version_id"], "sequence": seq})
        else:
            matched.add(seq)
    available = registry_rows is not None or registry_path.is_file()
    return {"schema": "rpnh-ab/bridge-registry-check/v1", "registry_projection_available": available,
            "environment_returned": len(returned), "native_returned_records": native_returned,
            "matched_unique_sequences": len(matched), "mismatches": mismatches,
            "environment_returns_without_matching_registered_return": sorted(set(returned)-matched),
            "all_environment_returns_registered": available and not mismatches and set(returned) == matched,
            "proves_subsequent_model_consumption": False,
            "classification": "evidence diagnostic, not a replacement for upstream state scoring"}
