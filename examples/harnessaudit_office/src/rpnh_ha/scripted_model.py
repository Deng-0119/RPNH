"""Deterministic off-t2 wiring fixture; never a native_live benchmark model."""
from __future__ import annotations

import json
import re
import sys
from typing import Any


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _output_port(text: str) -> str:
    matches = re.findall(
        r"\b[a-z][a-z0-9_]*\.(?:output__[a-z0-9_]+__[a-z0-9_]+|result)\b",
        text,
    )
    if not matches:
        raise ValueError("scripted request lacks a semantic output port")
    return matches[0]


def _located_paths(text: str) -> list[str]:
    paths = []
    for line in text.splitlines():
        value = line.strip()
        if not value.startswith("- {"):
            continue
        try:
            row = json.loads(value[2:])
        except (TypeError, ValueError):
            continue
        path = row.get("sandbox_path") if isinstance(row, dict) else None
        if isinstance(path, str):
            paths.append(path)
    return list(dict.fromkeys(paths))


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":"))}


def _write_calls(text: str, *, path: str, description: str, content: str) -> list[dict]:
    return [
        _call("write-scripted-output", "write_file", {
            "path": path,
            "description": description,
            "content": content,
            "output_port_id": _output_port(text),
            "outcome_id": "complete",
        }),
        _call("complete-scripted-interaction", "complete_interaction", {}),
    ]


def _tool_names(request: dict) -> set[str]:
    names = set()
    for message in request.get("messages", []):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                name = (
                    function.get("name")
                    if isinstance(function, dict) else call.get("name"))
                if isinstance(name, str):
                    names.add(name)
        if message.get("role") != "tool":
            continue
        name = message.get("name")
        if isinstance(name, str):
            names.add(name)
        names.update(re.findall(
            r"(?:read_user_directory|query_asset_inventory|order_service_catalog_item|search_knowledge_base)",
            str(message.get("content", "")),
        ))
    return names


def response(request: dict) -> dict:
    if request.get("protocol") != "llm_request_envelope/v1":
        raise ValueError("unsupported request protocol")
    text = "\n".join(_strings(request))
    paths = _located_paths(text)
    seen = _tool_names(request)
    calls: list[dict]
    if "[rpnh-ha:hub-plan]" in text:
        calls = _write_calls(
            text,
            path="outputs/coordination-plan.txt",
            description="Public-role coordination plan for both specialists.",
            content=(
                "Inspect the employee and assigned device using actual tool results, "
                "check the applicable warranty policy, place any justified standard "
                "replacement request, and return factual findings for final synthesis."
            ),
        )
    elif "[rpnh-ha:specialist-1]" in text:
        if "read_user_directory" not in seen:
            calls = [*[
                _call(f"read-plan-{index}", "read_file", {"path": path})
                for index, path in enumerate(paths, start=1)
            ], _call("directory", "read_user_directory", {
                "user_lookup": "USR-ETHAN-BROOKS",
            })]
        elif "query_asset_inventory" not in seen:
            calls = [_call("asset", "query_asset_inventory", {
                "assigned_to": "USR-ETHAN-BROOKS",
                "asset_type": "laptop",
                "query": "USR-ETHAN-BROOKS",
            })]
        elif "order_service_catalog_item" not in seen:
            calls = [_call("order", "order_service_catalog_item", {
                "item_name": "Developer Laptop 16",
                "requested_for": "USR-ETHAN-BROOKS",
                "quantity": "1",
                "configuration": "standard developer replacement",
            })]
        else:
            calls = _write_calls(
                text,
                path="outputs/workplace-report.txt",
                description="Scripted workplace-services acceptance report.",
                content=(
                    "Directory, assigned laptop, warranty date, hardware defect, and "
                    "replacement-order evidence were obtained from the actual business tools."
                ),
            )
    elif "[rpnh-ha:specialist-2]" in text:
        if "search_knowledge_base" not in seen:
            calls = [*[
                _call(f"read-plan-{index}", "read_file", {"path": path})
                for index, path in enumerate(paths, start=1)
            ], _call("policy", "search_knowledge_base", {
                "query": "warranty_replacement_policy",
                "audience": "it_staff",
            })]
        else:
            calls = _write_calls(
                text,
                path="outputs/policy-report.txt",
                description="Scripted policy acceptance report.",
                content=(
                    "The actual knowledge-base result says a defective laptop whose "
                    "warranty expires within 30 days should be replaced immediately."
                ),
            )
    elif "[rpnh-ha:hub-finalize]" in text:
        tool_messages = [m for m in request.get("messages", []) if m.get("role") == "tool"]
        if paths and not tool_messages:
            calls = [
                _call(f"read-report-{index}", "read_file", {"path": path})
                for index, path in enumerate(paths, start=1)
            ]
        else:
            calls = _write_calls(
                text,
                path="outputs/final-answer.txt",
                description="Final scripted native acceptance answer.",
                content=(
                    "Ethan Brooks's assigned Developer Laptop 16 has a reported hardware "
                    "defect and its warranty expires on 2026-05-20. The warranty policy "
                    "calls for immediate replacement when a defective laptop is within "
                    "30 days of expiry. A standard Developer Laptop 16 replacement was "
                    "ordered for USR-ETHAN-BROOKS; fulfillment is pending."
                ),
            )
    else:
        raise ValueError("unknown scripted workflow node")
    return {
        "protocol": "llm_response_envelope/v1",
        "tool_calls": calls,
        "finish_reason": "tool_calls",
    }


def main() -> int:
    json.dump(response(json.load(sys.stdin)), sys.stdout, ensure_ascii=True,
              allow_nan=False, sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
