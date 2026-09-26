"""Deterministic protocol fixture for examples; this is not a language model."""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable


class _NeedLocatedInput(RuntimeError):
    pass


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _numbers(texts: Iterable[str], label: str) -> list[int] | None:
    pattern = re.compile(re.escape(label) + r"\s*([0-9]+(?:\s*,\s*[0-9]+)*)", re.I)
    for text in texts:
        match = pattern.search(text)
        if match:
            return [int(value.strip()) for value in match.group(1).split(",")]
    return None


def _summary(value: Any) -> dict[str, int | float] | None:
    if isinstance(value, dict):
        keys = {"count", "total", "mean", "minimum", "maximum"}
        if keys.issubset(value) and all(
                isinstance(value[key], (int, float)) and not isinstance(value[key], bool)
                for key in keys):
            return {key: value[key] for key in keys}
        for item in value.values():
            found = _summary(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _summary(item)
            if found is not None:
                return found
    elif isinstance(value, str):
        candidates = [value]
        candidates.extend(re.findall(r"\{[^{}]{1,1000}\}", value))
        for candidate in candidates:
            try:
                decoded = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            if decoded != value:
                found = _summary(decoded)
                if found is not None:
                    return found
    return None


def _output_port(texts: Iterable[str]) -> str:
    for text in texts:
        matches = re.findall(
            r"\b[a-z][a-z0-9_]*\.(?:output__[a-z0-9_]+__[a-z0-9_]+|result)\b",
            text,
        )
        if matches:
            return matches[0]
    raise ValueError("scripted request did not expose a semantic output port")


def _located_input_path(texts: Iterable[str]) -> str:
    rows = []
    for text in texts:
        for line in text.splitlines():
            value = line.strip()
            if not value.startswith("- {"):
                continue
            try:
                row = json.loads(value[2:])
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict) and isinstance(
                    row.get("sandbox_path"), str):
                rows.append(row)
    if rows:
        selected = next(
            (row for row in rows
             if row.get("summary") == "Registered operation product"),
            rows[0],
        )
        return selected["sandbox_path"]
    raise ValueError("scripted request did not expose a located input")


def _content(request: dict[str, Any]) -> tuple[str, str, str]:
    texts = list(_strings(request))
    combined = "\n".join(texts)
    if "Normalize the task's batch values" in combined:
        values = _numbers(texts, "Batch values:")
        if not values:
            raise _NeedLocatedInput
        return (
            "outputs/normalized.json",
            "Normalized batch values for the native summary operation.",
            json.dumps({"values": values}, separators=(",", ":")),
        )
    summary = _summary(request)
    if "Explain the registered summary" in combined:
        if summary is None:
            raise _NeedLocatedInput
        return (
            "outputs/summary.txt",
            "Explanation derived from the registered native summary.",
            (f"{summary['count']} batches total {summary['total']}; mean "
             f"{summary['mean']}; minimum {summary['minimum']}; maximum "
             f"{summary['maximum']}."),
        )
    if "data quality" in combined.lower():
        return (
            "outputs/checks.txt",
            "Three scripted data-quality checks.",
            "1. Check missing values.\n2. Check duplicate rows.\n3. Check allowed ranges.",
        )
    values = _numbers(texts, "Task values:")
    if values:
        total = sum(values)
        mean = total / len(values)
        shown_mean = int(mean) if mean.is_integer() else mean
        return (
            "outputs/task-summary.txt",
            "Scripted task summary.",
            f"{len(values)} values total {total}; mean {shown_mean}.",
        )
    if not any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in request.get("messages", [])):
        raise _NeedLocatedInput
    return (
        "outputs/result.txt",
        "Scripted task result.",
        "The deterministic example task completed.",
    )


def main() -> int:
    request = json.load(sys.stdin)
    if request.get("protocol") != "llm_request_envelope/v1":
        raise ValueError("unsupported request protocol")
    texts = list(_strings(request))
    try:
        path, description, content = _content(request)
    except _NeedLocatedInput:
        response = {
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": "read-example-input",
                "name": "read_file",
                "arguments": json.dumps({
                    "path": _located_input_path(texts),
                }, separators=(",", ":")),
            }],
            "finish_reason": "tool_calls",
        }
        json.dump(
            response, sys.stdout, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"))
        return 0
    port = _output_port(texts)
    response = {
        "protocol": "llm_response_envelope/v1",
        "tool_calls": [{
            "id": "write-example-output",
            "name": "write_file",
            "arguments": json.dumps({
                "path": path,
                "description": description,
                "content": content,
                "output_port_id": port,
                "outcome_id": "complete",
            }, separators=(",", ":")),
        }, {
            "id": "complete-example-interaction",
            "name": "complete_interaction",
            "arguments": "{}",
        }],
        "finish_reason": "tool_calls",
    }
    json.dump(
        response, sys.stdout, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
