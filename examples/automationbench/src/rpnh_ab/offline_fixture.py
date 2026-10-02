"""Synthetic, non-benchmark inputs for installed-host acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .constants import FRAME_BYTES
from .io import write_new


def synthetic_row() -> dict:
    return {
        "prompt": [{
            "role": "user",
            "content": (
                "Mark the synthetic Gmail message offline-message as STARRED. "
                "This is a local deterministic host-acceptance task."
            ),
        }],
        "info": {
            "task_name": "offline-synthetic-label",
            "initial_state": {
                "meta": {"current_time": "2026-10-02T00:00:00Z"},
                "gmail": {"messages": [{
                    "id": "offline-message", "thread_id": "offline-thread",
                    "from_": "sender@example.test", "to": ["receiver@example.test"],
                    "subject": "Offline acceptance", "body_plain": "Verified business tool path",
                    "label_ids": ["INBOX"], "date": 1790899200000,
                    "internal_date": 1790899200000,
                }]},
            },
            "assertions": [{
                "type": "gmail_message_has_label",
                "message_id": "offline-message", "label_id": "STARRED",
            }],
            "zapier_tools": [],
        },
    }


def tool_steps(*, padding: int = 0, large_payload_bytes: int = 0) -> list[dict]:
    if not isinstance(padding, int) or isinstance(padding, bool) or padding < 0:
        raise ValueError("padding must be a nonnegative integer")
    if (not isinstance(large_payload_bytes, int) or isinstance(large_payload_bytes, bool)
            or large_payload_bytes < 0 or large_payload_bytes > FRAME_BYTES // 2):
        raise ValueError("large synthetic payload is outside the acceptance bound")
    steps = [{"tool": "api_search", "arguments": {
        "query": "gmail modify message labels", "top_k": 2,
    }}, {"tool": "base64_encode", "arguments": {
        "text": "Verified business tool path",
    }}]
    if large_payload_bytes:
        steps.append({"tool": "base64_encode", "arguments": {
            "text": "x" * large_payload_bytes,
        }})
    steps.extend({"tool": "base64_encode", "arguments": {
        "text": f"padding-{index}",
    }} for index in range(padding))
    steps.append({"tool": "api_fetch", "arguments": {
        "method": "POST",
        "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages/offline-message/modify",
        "params": None,
        "body": json.dumps({"addLabelIds": ["STARRED"], "removeLabelIds": []}),
    }})
    return steps


def create_profile(root: Path, steps: list[dict], *, delay_seconds: float = 0,
                   batch_tools: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    scenario = root / "scenario.json"
    write_new(scenario, {
        "steps": steps,
        "delay_seconds": delay_seconds,
        "batch_tools": batch_tools,
        "requests_log": str(root / "requests.jsonl"),
    })
    adapter = root / "adapter.json"
    source_root = Path(__file__).resolve().parents[1]
    write_new(adapter, {
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-scripted-automationbench-acceptance",
        "argv": [sys.executable, "-m", "rpnh_ab.offline_adapter", str(scenario)],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {"PYTHONPATH": str(source_root)},
        "inherit_env": [],
    })
    selection = root / "selection.json"
    write_new(selection, {
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-scripted-automationbench-acceptance",
        "adapter_config_path": str(adapter),
        "timeout_seconds": 30,
        "max_output_tokens": 8192,
        "max_response_bytes": 1024 * 1024,
    })
    return selection
