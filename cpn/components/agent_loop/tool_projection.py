"""Bounded AgentLoop tool-result projections."""

from __future__ import annotations

import base64
import bisect
import re
from typing import Any, Mapping


def bounded_agent_read_projection(
        payload: bytes, arguments: Mapping[str, Any],
) -> dict[str, object]:
    """Project one caller-sized page without constraining total resource size."""

    if not isinstance(payload, bytes) or not isinstance(arguments, Mapping):
        raise TypeError("registered file read requires bytes and arguments")
    try:
        text = payload.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = base64.b64encode(payload).decode("ascii")
        encoding = "base64"
    offset = arguments.get("offset_chars", 0)
    maximum = arguments.get("max_chars", 32768)
    if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or isinstance(maximum, bool) or not isinstance(maximum, int)
            or maximum < 1 or maximum > 32768):
        raise ValueError("registered file read page is invalid")
    end = min(len(text), offset + maximum)
    return {
        "content": text[offset:end] if offset < len(text) else "",
        "encoding": encoding,
        "offset_chars": offset,
        "next_offset_chars": end if end < len(text) else None,
        "total_chars": len(text),
    }


def bounded_agent_text_search_projection(
        payload: bytes, arguments: Mapping[str, Any],
) -> dict[str, object]:
    """Return bounded literal matches without copying a complete file to prompt."""

    if not isinstance(payload, bytes) or not isinstance(arguments, Mapping):
        raise TypeError("registered text search requires bytes and arguments")
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("registered text search requires UTF-8 text") from exc
    query = arguments.get("query")
    case_sensitive = arguments.get("case_sensitive", True)
    offset_match = arguments.get("offset_match", 0)
    max_matches = arguments.get("max_matches", 10)
    context_chars = arguments.get("context_chars", 120)
    if (not isinstance(query, str) or not query or len(query) > 256
            or not isinstance(case_sensitive, bool)
            or isinstance(offset_match, bool)
            or not isinstance(offset_match, int) or offset_match < 0
            or isinstance(max_matches, bool)
            or not isinstance(max_matches, int)
            or max_matches < 1 or max_matches > 20
            or isinstance(context_chars, bool)
            or not isinstance(context_chars, int)
            or context_chars < 0 or context_chars > 256):
        raise ValueError("registered text search arguments are invalid")

    flags = 0 if case_sensitive else re.IGNORECASE
    line_starts = [0]
    line_starts.extend(
        match.end() for match in re.finditer("\\n", source))
    selected: list[dict[str, object]] = []
    total_matches = 0
    for match in re.finditer(re.escape(query), source, flags):
        ordinal = total_matches
        total_matches += 1
        if ordinal < offset_match or len(selected) >= max_matches:
            continue
        start = match.start()
        line_index = bisect.bisect_right(line_starts, start) - 1
        excerpt_start = max(0, start - context_chars)
        excerpt_end = min(len(source), match.end() + context_chars)
        selected.append({
            "match_ordinal": ordinal,
            "line_number": line_index + 1,
            "column_number": start - line_starts[line_index] + 1,
            "match_start_chars": start,
            "excerpt_start_chars": excerpt_start,
            "excerpt": source[excerpt_start:excerpt_end],
        })
    next_offset = offset_match + len(selected)
    return {
        "query": query,
        "case_sensitive": case_sensitive,
        "offset_match": offset_match,
        "next_offset_match": (
            next_offset if next_offset < total_matches else None),
        "total_matches": total_matches,
        "matches": selected,
    }


def bounded_agent_action_output_projection(
        result_metadata: Mapping[str, Any], arguments: Mapping[str, Any],
) -> dict[str, object]:
    """Project one exact page from a prior registered workspace action stream."""

    if (not isinstance(result_metadata, Mapping)
            or set(result_metadata) != {
                "kind", "status", "exit_code", "stdout", "stderr",
                "output_truncated", "command_started"}
            or result_metadata.get("kind") != "workspace_execution/v1"
            or result_metadata.get("status") not in {"completed", "timed_out"}
            or isinstance(result_metadata.get("exit_code"), bool)
            or not isinstance(result_metadata.get("exit_code"), int)
            or not isinstance(result_metadata.get("stdout"), str)
            or not isinstance(result_metadata.get("stderr"), str)
            or not isinstance(result_metadata.get("output_truncated"), bool)
            or result_metadata.get("command_started") is not True
            or not isinstance(arguments, Mapping)):
        raise ValueError("action output page requires one workspace action result")
    action_ref = arguments.get("agent_action_ref")
    if (not isinstance(action_ref, Mapping)
            or set(action_ref) != {
                "entity_type", "logical_id", "version_id"}
            or action_ref.get("entity_type") != "agent_action/v2"
            or re.fullmatch(
                r"agent_action:[a-f0-9]{32}",
                str(action_ref.get("logical_id"))) is None
            or re.fullmatch(
                r"agent_action_version:[a-f0-9]{32}",
                str(action_ref.get("version_id"))) is None):
        raise ValueError("action output page requires one exact agent_action/v2 ref")
    stream = arguments.get("stream")
    offset = arguments.get("offset_chars", 0)
    maximum = arguments.get("max_chars", 32768)
    if (stream not in {"stdout", "stderr"}
            or isinstance(offset, bool) or not isinstance(offset, int)
            or offset < 0
            or isinstance(maximum, bool) or not isinstance(maximum, int)
            or maximum < 1 or maximum > 32768):
        raise ValueError("action output page arguments are invalid")
    source = str(result_metadata[stream])
    end = min(len(source), offset + maximum)
    return {
        "kind": "workspace_action_output_page/v1",
        "agent_action_ref": dict(action_ref),
        "stream": stream,
        "content": source[offset:end] if offset < len(source) else "",
        "offset_chars": offset,
        "next_offset_chars": end if end < len(source) else None,
        "total_chars": len(source),
        "source_output_truncated": result_metadata["output_truncated"],
    }


__all__ = [
    "bounded_agent_action_output_projection",
    "bounded_agent_read_projection",
    "bounded_agent_text_search_projection",
]
