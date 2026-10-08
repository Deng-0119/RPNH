"""Synthetic executable/profile fixtures; real CLI capture is separate evidence."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from examples.slopcodebench.endpoint import BRIDGE_MODULE, CONTROLS, prepare_execution_snapshot


def profile(root, *, legacy=False):
    program = ["{python}", "-m", BRIDGE_MODULE]
    if legacy:
        program = ["{python}", str(root / "codex_subscription_bridge_outer_sandbox.py")]
    adapter = root / "adapter.json"
    adapter.write_text(json.dumps({"schema_version": "local_process_adapter_config/v1", "adapter_kind": "local_process",
        "model_condition": "synthetic-scb", "argv": [*program, "--codex", "{codex}", "--model", "synthetic-scb"],
        "probe_argv": ["{codex}", "--version"], "env": {}, "inherit_env": []}))
    selection = root / "selection.json"
    selection.write_text(json.dumps({"schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": "synthetic-scb", "adapter_config_path": str(adapter), "timeout_seconds": 30,
        "max_output_tokens": 2048, "max_response_bytes": 262144}))
    return selection, adapter


@pytest.mark.parametrize("legacy", [False, True])
def test_closed_endpoint_snapshot_preserves_route_model_and_budget(tmp_path, legacy):
    from cpn.llm_adapters import load_llm_execution_selection
    selected, adapter = profile(tmp_path, legacy=legacy)
    original = adapter.read_bytes()
    before = load_llm_execution_selection(selected)
    binary = tmp_path / "official-fixture"
    capture = tmp_path / "arguments.json"
    binary.write_text(f"#!{sys.executable} -I\nimport json,sys\n"
        "if sys.argv[1:] == ['--version']: print('codex-cli synthetic'); raise SystemExit(0)\n"
        f"open({str(capture)!r}, 'x').write(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    frozen_path, condition = prepare_execution_snapshot(selected, tmp_path / "private", codex_binary=binary)
    frozen = load_llm_execution_selection(frozen_path)
    assert replace(frozen, adapter_config_path=before.adapter_config_path) == before
    assert adapter.read_bytes() == original == (frozen_path.parent / "original-adapter.json").read_bytes()
    adapted = json.loads(frozen.adapter_config_path.read_bytes())
    assert adapted["argv"][:3] == [str(Path(sys.executable).absolute()), "-m", BRIDGE_MODULE]
    endpoint = frozen_path.parent / "endpoint/codex"
    assert adapted["argv"][4] == str(endpoint)
    assert adapted["probe_argv"] == [str(endpoint), "--version"]
    assert adapted["env"] == {} and adapted["inherit_env"] == []
    request = ["exec", "--model", "synthetic-scb", "--disable", "shell_tool", "--json", "-"]
    subprocess.run([endpoint, *request], check=True, timeout=3)
    assert json.loads(capture.read_text()) == ["exec", "-c", CONTROLS[0], "-c", CONTROLS[1], *request[1:]]
    assert condition["exec_controls"] == list(CONTROLS)
    assert condition["model_service_budgets"] == "existing_selection_unchanged"
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in frozen_path.parent.glob("*.json"))
    assert endpoint.stat().st_mode & 0o777 == 0o700
    assert subprocess.run([endpoint, "app-server"], capture_output=True, timeout=3).returncode != 0


def test_unclosed_codex_selection_rejected_before_writing_or_owner_start(tmp_path):
    selected, _ = profile(tmp_path)
    with pytest.raises(ValueError, match="official codex_binary"):
        prepare_execution_snapshot(selected, tmp_path / "private")
    assert not (tmp_path / "private").exists()


def test_existing_external_provider_selection_is_not_rebound(tmp_path):
    selected, _ = profile(tmp_path)
    document = json.loads(selected.read_text())
    document["adapter_kind"] = "external_provider"
    # This check does not invoke or inspect the referenced provider config.
    selected.write_text(json.dumps(document))
    original = selected.read_bytes()
    path, condition = prepare_execution_snapshot(selected, tmp_path / "private")
    assert path == selected and selected.read_bytes() == original
    assert condition["kind"] == "existing_external_provider"
    assert not (tmp_path / "private").exists()
