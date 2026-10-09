#!/usr/bin/env python3
"""Run the bounded, offline-only OpenCode candidate gate with installed pytest."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

# Third-party plugins must not execute before the fixture-level effect guards.
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
import pytest


class NoEffects:
    @pytest.fixture(autouse=True)
    def guard_external_effects(self, monkeypatch):
        def denied(*_args, **_kwargs):
            raise AssertionError("G1 forbids sockets, threads, native processes and PTYs")
        # Mocked subprocess.run return values remain usable by version probes.
        # Any real subprocess.run reaches Popen and is rejected before launch.
        monkeypatch.setattr(subprocess, "Popen", denied)
        monkeypatch.setattr(socket, "socket", denied)
        monkeypatch.setattr(threading.Thread, "start", denied)
        for name in ("fork", "forkpty", "openpty", "posix_spawn", "posix_spawnp", "system"):
            if hasattr(os, name):
                monkeypatch.setattr(os, name, denied)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New/local evidence directory, outside the source tree")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    source = root / "source"
    output = args.output.resolve()
    if output.is_relative_to(source):
        parser.error("Evidence must stay outside source")
    output.mkdir(parents=True, exist_ok=True)
    nodes = json.loads((root / "evidence/g1-regression-nodes.json").read_text())
    targets = ["tests/test_opencode_candidate_profiles.py",
               "tests/test_opencode_candidate_gate_support.py", *nodes,
               "tests/test_opencode_cli.py::test_installed_parser_advertises_opencode",
               "tests/test_opencode_cli.py::test_cli_dispatches_opencode_without_replacing_other_frontends",
               "tests/test_opencode_cli.py::test_opencode_rejects_one_shot_prompt_before_owner_creation"]
    # Explicit node list excludes legacy native/socket/real Registry execution.
    os.chdir(source)
    sys.path.insert(0, str(source))
    code = int(pytest.main(["-q", "-p", "no:cacheprovider", *targets,
                            f"--junitxml={output / 'g1.xml'}"], plugins=[NoEffects()]))
    (output / "g1-status.json").write_text(json.dumps({
        "lane": "G1", "status": "PASS" if code == 0 else "FAIL",
        "exit_code": code, "targets": targets,
        "boundary": "offline source/schema/projection/fake-process tests only",
        "native_g2": "NOT_RUN", "native_g3": "NOT_RUN",
        "official_sdk_compilation": "NOT_RUN",
        "socket_pty_process_thread_guards": True,
    }, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
