"""Deterministic local-process adapter used only for host acceptance.

It has no provider SDK, credentials, or network transport.  The scenario is
created in the caller-owned acceptance directory and contains only synthetic
tool calls.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time


def _declared_bundle(request: dict) -> tuple[str, str]:
    marker = "Broad output contract: Use exactly one declared semantic outcome bundle: "
    for message in request["messages"]:
        if message.get("role") != "system" or not isinstance(message.get("content"), str):
            continue
        for line in message["content"].splitlines():
            if line.startswith(marker):
                bundle, _ = json.JSONDecoder().raw_decode(line[len(marker):])
                if not isinstance(bundle, dict) or len(bundle) != 1:
                    raise ValueError("expected one declared semantic outcome")
                outcome, ports = next(iter(bundle.items()))
                if not isinstance(ports, list) or len(ports) != 1:
                    raise ValueError("expected one declared semantic output port")
                return outcome, ports[0]
    raise ValueError("framework did not declare an exact semantic outcome bundle")


def response(request: dict, scenario: dict) -> dict:
    delay = scenario.get("delay_seconds", 0)
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0 or delay > 30:
        raise ValueError("invalid offline acceptance delay")
    if delay:
        time.sleep(delay)
    steps = scenario["steps"]
    batch_tools = scenario.get("batch_tools", False)
    if type(batch_tools) is not bool:
        raise ValueError("batch_tools must be boolean")
    seen = [message for message in request["messages"] if message.get("role") == "assistant"]
    index = len(seen)
    if batch_tools and not seen and steps:
        return {
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": f"offline-{step_index}",
                "name": step["tool"],
                "arguments": json.dumps(step["arguments"], sort_keys=True),
            } for step_index, step in enumerate(steps)],
            "finish_reason": "tool_calls",
        }
    if not batch_tools and index < len(steps):
        step = steps[index]
        return {
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": f"offline-{index}",
                "name": step["tool"],
                "arguments": json.dumps(step["arguments"], sort_keys=True),
            }],
            "finish_reason": "tool_calls",
        }
    tools = {row.get("function", row).get("name") for row in request["tools"]}
    if "write_file" in tools:
        outcome, output_port = _declared_bundle(request)
        calls = [{
            "id": "offline-report",
            "name": "write_file",
            "arguments": json.dumps({
                "path": "outputs/report.txt",
                "description": "offline host acceptance report",
                "content": "Deterministic synthetic tools finished.",
                "outcome_id": outcome,
                "output_port_id": output_port,
            }),
        }, {
            "id": "offline-complete",
            "name": "complete_interaction",
            "arguments": "{}",
        }]
        return {"protocol": "llm_response_envelope/v1", "tool_calls": calls,
                "finish_reason": "tool_calls"}
    return {"protocol": "llm_response_envelope/v1",
            "text": "Deterministic synthetic tools finished.",
            "tool_calls": [], "finish_reason": "stop"}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        raise SystemExit("usage: python -m rpnh_ab.offline_adapter SCENARIO.json")
    request = json.loads(sys.stdin.buffer.read())
    scenario_path = Path(arguments[0]).resolve()
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    result = response(request, scenario)
    requests_log = scenario.get("requests_log")
    if requests_log:
        with Path(requests_log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(request, sort_keys=True) + "\n")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True,
                                separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
