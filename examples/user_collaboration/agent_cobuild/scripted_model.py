"""Dynamic SCRIPTED protocol driver, not an LLM or a replay.

No input files are opened here. Role/contract come from request system messages;
data come exclusively from correlated stock read_file tool returns. The first
response requests a read; the next computes and requests write + complete.
Invalid or unread evidence raises ValueError and main exits without a success
envelope. The pure response() function is also used by focused protocol tests.
"""
from __future__ import annotations

import json
import re
import sys


def _contract(texts: list[str]) -> dict:
    markers = [line.removeprefix("COBUILD_CONTRACT_JSON: ")
               for text in texts for line in text.splitlines()
               if line.startswith("COBUILD_CONTRACT_JSON: ")]
    if len(markers) != 1:
        raise ValueError("expected one actual system instruction contract")
    contract = json.loads(markers[0])
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    role = contract.get("role")
    flag = {"curator": "retain_source_lineage", "auditor": "reconcile_sources"}.get(role)
    if (flag is None or set(contract) != {"schema", "role", flag}
            or contract["schema"] != "agent_cobuild_contract/v1"
            or type(contract[flag]) is not bool):
        raise ValueError("unsupported co-build contract")
    return contract


def _input_path(texts: list[str]) -> str:
    # Use declared semantic inputs, never friendly summaries or workspace copies.
    rows = []
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- {"):
                try:
                    row = json.loads(line[2:])
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("sandbox_path"), str):
                    rows.append(row)
    semantic = [r for r in rows if r.get("semantic_name") != "workspace_file"]
    typed = [r for r in semantic
             if r.get("content_schema_ref") == "application/rpnh_agent_text/v1"]
    # A sole original source input can lack the upstream text-product schema.
    paths = list(dict.fromkeys(r["sandbox_path"] for r in (typed or semantic)))
    if len(paths) != 1 or not paths[0]:
        raise ValueError("expected one original located input path")
    return paths[0]


def _output_port(texts: list[str]) -> str:
    pattern = r"\b[a-z][a-z0-9_]*\.(?:output__[a-z0-9_]+__[a-z0-9_]+|result)\b"
    # Prefer the canonical current-firing output declaration to incidental refs.
    declared = [line for text in texts for line in text.splitlines()
                if line.startswith("Provider-writable semantic output_port_ids (exact): ")]
    ports = list(dict.fromkeys(re.findall(pattern, "\n".join(declared or texts))))
    if len(ports) != 1:
        raise ValueError("expected one declared semantic output port")
    return ports[0]


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name,
            "arguments": json.dumps(arguments, sort_keys=True, separators=(",", ":"))}


def _envelope(calls: list[dict]) -> dict:
    return {"protocol": "llm_response_envelope/v1", "tool_calls": calls,
            "finish_reason": "tool_calls"}


def _read_body(messages: list[dict], path: str) -> dict:
    reads = {}
    bodies = []
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") == "read_file":
                    args = json.loads(function["arguments"])
                    reads[call["id"]] = args.get("path")
        elif message.get("role") == "tool":
            if reads.get(message.get("tool_call_id")) != path:
                continue
            result = json.loads(message["content"])
            if (not isinstance(result, dict)
                    or result.get("kind") != "registered_file_read/v1"
                    or result.get("path") != path
                    or not isinstance(result.get("content"), str)):
                raise ValueError("input read failed or has no registered content")
            body = json.loads(result["content"])
            if not isinstance(body, dict):
                raise ValueError("input JSON must be an object")
            bodies.append(body)
    if len(bodies) != 1:
        raise ValueError("input has not been read exactly once through read_file")
    return bodies[0]


def _nonnegative_int(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _records(body: dict) -> list[dict]:
    rows = body.get("records")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("records must be an array of objects")
    for row in rows:
        _text(row.get("category"), "category")
        _nonnegative_int(row.get("minutes"), "minutes")
    return rows


def _totals(rows: list[dict]) -> dict:
    categories = {}
    for row in rows:
        group = categories.setdefault(row["category"], {
            "category": row["category"], "record_count": 0, "total_minutes": 0})
        group["record_count"] += 1
        group["total_minutes"] += row["minutes"]
    return {"record_count": len(rows), "total_minutes": sum(r["minutes"] for r in rows),
            "by_category": [categories[key] for key in sorted(categories)]}


def _curate(body: dict, contract: dict) -> dict:
    rows = _records(body)
    ids = [_text(row.get("id"), "id") for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("source IDs must be unique")
    lineage = contract["retain_source_lineage"]
    normalized = [
        {"category": row["category"], "minutes": row["minutes"],
         **({"source_id": row["id"], "source_index": index} if lineage else {})}
        for index, row in enumerate(rows)
    ]
    return {"schema": "agent_cobuild_handoff/v1", "lineage_retained": lineage,
            "input_record_count": len(rows), "records": normalized,
            "totals": _totals(normalized)}


def _audit(body: dict, contract: dict) -> dict:
    if (body.get("schema") != "agent_cobuild_handoff/v1"
            or type(body.get("lineage_retained")) is not bool):
        raise ValueError("invalid normalized handoff schema")
    rows = _records(body)
    count = _nonnegative_int(body.get("input_record_count"), "input_record_count")
    totals = _totals(rows)
    # Canonical JSON equality also rejects booleans/floats standing in for counts.
    if (count != len(rows) or json.dumps(body.get("totals"), sort_keys=True)
            != json.dumps(totals, sort_keys=True)):
        raise ValueError("handoff counts or totals do not reconcile")
    reconcile = contract["reconcile_sources"]
    report = {"schema": "agent_cobuild_report/v1", "totals": totals,
              "lineage_retained": body["lineage_retained"],
              "source_reconciliation_required": reconcile,
              "checks": {"input_count_matches": True, "record_count_matches": True,
                         "category_totals_match": True, "total_minutes_match": True}}
    if reconcile:
        if not body["lineage_retained"]:
            raise ValueError("auditor contribution requires curator source lineage")
        ids = [_text(row.get("source_id"), "source_id") for row in rows]
        indices = [_nonnegative_int(row.get("source_index"), "source_index") for row in rows]
        if len(set(ids)) != len(ids) or indices != list(range(count)):
            raise ValueError("source lineage is duplicated, missing or out of order")
        report["source_reconciliation"] = [
            {key: row[key] for key in ("source_id", "source_index", "category", "minutes")}
            for row in rows]
        report["count_checks"] = {
            "input_record_count": count, "normalized_record_count": len(rows),
            "reconciled_source_count": len(ids), "reported_record_count": totals["record_count"],
            "counts_match": True,
        }
        report["checks"].update(unique_source_ids=True, source_indices_complete=True)
    return report


def response(request: dict) -> dict:
    """Consume one stock envelope; no filesystem, network or retained state."""
    if request.get("protocol") != "llm_request_envelope/v1":
        raise ValueError("unsupported request protocol")
    messages = request.get("messages", [])
    texts = [m["content"] for m in messages if m.get("role") == "system"
             and isinstance(m.get("content"), str)]
    contract = _contract(texts)
    path = _input_path(texts)
    role = contract["role"]
    if not any(m.get("role") in {"tool", "assistant"} for m in messages):
        return _envelope([_call(f"read-{role}-input", "read_file", {"path": path})])
    body = _read_body(messages, path)
    output = _curate(body, contract) if role == "curator" else _audit(body, contract)
    return _envelope([
        _call(f"write-{role}-output", "write_file", {
            "path": f"outputs/{role}.json",
            "description": f"SCRIPTED {role} output computed from registered read_file evidence.",
            "content": json.dumps(output, sort_keys=True, separators=(",", ":")),
            "output_port_id": _output_port(texts), "outcome_id": "complete",
        }),
        _call(f"complete-{role}", "complete_interaction", {}),
    ])


def main() -> int:
    result = response(json.load(sys.stdin))
    json.dump(result, sys.stdout, ensure_ascii=True, allow_nan=False,
              sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
