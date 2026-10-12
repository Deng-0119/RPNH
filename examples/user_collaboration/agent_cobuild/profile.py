"""Credential-free profile for the existing local_process adapter."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import MODEL_CONDITION


def write_profile(directory: Path) -> Path:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("scripted profile directory must be empty")
    adapter = directory / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": MODEL_CONDITION,
        "argv": ["{python}", str(Path(__file__).with_name("scripted_model.py").resolve())],
        "probe_argv": ["{python}", "-c", "raise SystemExit(0)"],
        "env": {"PYTHONDONTWRITEBYTECODE": "1"},
        "inherit_env": [],
    }, indent=2) + "\n", encoding="utf-8")
    execution = directory / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": MODEL_CONDITION,
        "adapter_config_path": str(adapter),
        "timeout_seconds": 30,
        "max_output_tokens": 4096,
        "max_response_bytes": 65536,
    }, indent=2) + "\n", encoding="utf-8")
    return execution
