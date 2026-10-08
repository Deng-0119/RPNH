"""Example-local closure of the existing Codex response endpoint's tools."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .export import new_path


CONTROLS = ('web_search="disabled"', 'project_doc_max_bytes=0')


def is_codex_profile(selection_path):
    from cpn.llm_adapters import load_llm_execution_selection
    from .source import load_json
    selection = load_llm_execution_selection(Path(selection_path))
    adapter = load_json(selection.adapter_config_path.read_bytes())
    return "cpn.llm_adapters.codex_subscription_bridge" in adapter.get("argv", [])


def prepare_codex_endpoint(binary, directory):
    """Call an explicitly selected official CLI, preserving RPNH's arguments.

    The existing global wrapper adds workspace access and developer instructions.
    This private executable bypasses it and closes native web/project-document
    inputs. Model, service, credentials and request budgets remain in the existing
    RPNH bridge. Only the current bridge's ``exec`` invocation is accepted.
    """
    binary = Path(binary).resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("an executable official Codex binary is required")
    directory = Path(directory).absolute()
    directory = new_path(directory.parent, directory.name)
    directory.mkdir(mode=0o700)
    version = subprocess.run([str(binary), "--version"], cwd=directory,
        capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    endpoint = directory / "codex"
    program = (f"#!{sys.executable} -I\n"
        "import os, sys\n"
        "arguments = sys.argv[1:]\n"
        "if not arguments or arguments[0] != 'exec':\n"
        "    raise SystemExit('ERP endpoint accepts the RPNH exec transport only')\n"
        f"binary = {str(binary)!r}\n"
        f"controls = {CONTROLS!r}\n"
        "argv = [binary, 'exec']\n"
        "for control in controls: argv.extend(['-c', control])\n"
        "os.execv(binary, [*argv, *arguments[1:]])\n")
    descriptor = os.open(endpoint, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(program)
    return endpoint, {"kind": "explicit_official_codex_response_endpoint",
        "cli_version": version, "exec_controls": list(CONTROLS),
        "global_wrapper": "bypassed", "existing_rpnh_tool_disable_flags": "preserved",
        "model_service_budgets": "existing_selection_unchanged"}
