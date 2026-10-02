"""Explicit no-model execution profile for native driver acceptance only."""
from __future__ import annotations

import json
from pathlib import Path


MODEL_CONDITION = "rpnh-ha-off-t2-scripted-acceptance-v1"


def write_scripted_profile(directory: Path) -> Path:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("scripted profile directory must be empty")
    model = Path(__file__).with_name("scripted_model.py").resolve()
    adapter = directory / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": MODEL_CONDITION,
        "argv": ["{python}", str(model)],
        "probe_argv": ["{python}", "-c", "raise SystemExit(0)"],
        "env": {},
        "inherit_env": [],
    }, indent=2) + "\n", encoding="utf-8")
    execution = directory / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": MODEL_CONDITION,
        "adapter_config_path": str(adapter),
        "timeout_seconds": 30,
        "max_output_tokens": 2048,
        "max_response_bytes": 262144,
    }, indent=2) + "\n", encoding="utf-8")
    return execution


__all__ = ("MODEL_CONDITION", "write_scripted_profile")
