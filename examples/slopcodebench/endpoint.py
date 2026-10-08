"""SCB-local Codex endpoint closure, matching the verified ERP control seam.

No ERP installation is needed. Configuration files stay private; this module
does not open credential files or change model, route, or request budgets.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

CONTROLS = ('web_search="disabled"', 'project_doc_max_bytes=0')
BRIDGE_MODULE = "cpn.llm_adapters.codex_subscription_bridge"


def _private_write(path, payload, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)


def prepare_codex_endpoint(binary, directory):
    """Bind an explicit official CLI, bypassing ambient wrappers and tools."""
    binary = Path(binary).resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("an executable official Codex binary is required")
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    version = subprocess.run([str(binary), "--version"], cwd=directory,
        capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    endpoint = directory / "codex"
    program = (f"#!{sys.executable} -I\n"
        "import os, sys\n"
        "arguments = sys.argv[1:]\n"
        f"binary = {str(binary)!r}\n"
        "if arguments == ['--version']:\n"
        "    os.execv(binary, [binary, '--version'])\n"
        "if not arguments or arguments[0] != 'exec':\n"
        "    raise SystemExit('SCB endpoint accepts the RPNH exec transport only')\n"
        f"controls = {CONTROLS!r}\n"
        "argv = [binary, 'exec']\n"
        "for control in controls: argv.extend(['-c', control])\n"
        "os.execv(binary, [*argv, *arguments[1:]])\n")
    _private_write(endpoint, program.encode(), 0o700)
    return endpoint, {"kind": "explicit_official_codex_response_endpoint",
        "cli_version": version, "exec_controls": list(CONTROLS),
        "global_wrapper": "bypassed", "existing_rpnh_tool_disable_flags": "preserved",
        "subscription_bridge": "installed_supported_RPNH_module",
        "model_service_budgets": "existing_selection_unchanged"}


def prepare_execution_snapshot(path, directory, *, codex_binary=None):
    """Freeze a supported selection before any owner or solver runtime starts.

    Only known Codex module/legacy-script bindings are adapted. All other local
    process configurations are copied unchanged without claiming their native
    endpoint capabilities were independently verified. Referenced auth files
    and scripts are never opened, copied, or probed by profile freezing.
    """
    from cpn.llm_adapters import load_llm_execution_selection

    path = Path(path)
    selection = load_llm_execution_selection(path)
    document = json.loads(path.read_bytes())
    if selection.adapter_kind != "local_process":
        if codex_binary is not None:
            raise ValueError("codex_binary was provided for a non-Codex selection")
        return path.absolute(), {"kind": "existing_external_provider", "endpoint_closure": "not_applicable",
                                 "model_service_budgets": "existing_selection_unchanged"}
    original = selection.adapter_config_path.read_bytes()
    adapter = json.loads(original)
    argv = adapter.get("argv", [])
    legacy = (len(argv) >= 2 and argv[0] == "{python}"
              and Path(argv[1]).name == "codex_subscription_bridge_outer_sandbox.py")
    codex = BRIDGE_MODULE in argv or legacy or "{codex}" in argv
    if codex:
        module = len(argv) >= 3 and argv[1:3] == ["-m", BRIDGE_MODULE]
        if not (module or legacy) or argv.count("{codex}") != 1:
            raise ValueError("Codex selection requires the exact supported bridge and one {codex} token")
        if codex_binary is None:
            raise ValueError("Codex selection requires an explicit official codex_binary before owner startup")
    elif codex_binary is not None:
        raise ValueError("codex_binary was provided for a non-Codex selection")
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    condition = {"kind": "existing_non_codex_local_process", "endpoint_closure": "not_applicable",
                 "model_service_budgets": "existing_selection_unchanged"}
    payload = original
    if codex:
        endpoint, condition = prepare_codex_endpoint(codex_binary, directory / "endpoint")
        if legacy:
            argv = [argv[0], "-m", BRIDGE_MODULE, *argv[2:]]
        if argv[0] == "{python}":
            # Preserve the venv pathname; resolving its symlink loses pyvenv.cfg.
            argv[0] = str(Path(sys.executable).absolute())
        adapter["argv"] = [str(endpoint) if item == "{codex}" else item for item in argv]
        if isinstance(adapter.get("probe_argv"), list):
            adapter["probe_argv"] = [str(endpoint) if item == "{codex}" else item for item in adapter["probe_argv"]]
        payload = json.dumps(adapter, ensure_ascii=True, allow_nan=False).encode()
        _private_write(directory / "original-adapter.json", original)
    document["adapter_config_path"] = str(directory / "adapter.json")
    _private_write(directory / "adapter.json", payload)
    frozen_path = directory / "selection.json"
    _private_write(frozen_path, json.dumps(document, ensure_ascii=True, allow_nan=False).encode())
    frozen = load_llm_execution_selection(frozen_path)
    if replace(frozen, adapter_config_path=selection.adapter_config_path) != selection:
        raise ValueError("frozen model selection or budget differs from the original")
    frozen.as_registry_policy()
    return frozen_path, condition
