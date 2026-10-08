"""Read-only integration preflight. Never starts Docker, an owner, or a model."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys

from .contracts import CheckpointScope


ROOT = Path(__file__).resolve().parent


def inspect_runner_source(root: Path) -> dict:
    """Check the installed checkout's inspected API shape without importing it."""
    path = root / "src/slop_code/agent_runner/agent.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    agent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Agent")
    methods = {node.name: node for node in agent.body if isinstance(node, ast.FunctionDef)}
    expected = {"setup": ("self", "session"), "run": ("self", "task"),
                "save_artifacts": ("self", "path"), "reset": ("self",),
                "cleanup": ("self",)}
    observed = {name: tuple(arg.arg for arg in methods[name].args.args)
                for name in expected if name in methods}
    return {"status": "passed" if observed == expected else "failed",
            "observed": observed,
            "note": "API shape only; verify source revision separately. No upstream import or execution."}


def report(*, prefix: int = 3, runner_source: Path | None = None) -> dict:
    if type(prefix) is not int or not 1 <= prefix <= 5:
        raise ValueError("prefix must be between 1 and 5")
    scope = CheckpointScope(tuple(f"checkpoint_{n}" for n in range(1, prefix + 1)))
    from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
    parameters = tuple(inspect.signature(AgentTaskSpec).parameters)
    return {
        "schema_version": "rpnh/slopcodebench-preflight/v1",
        "sources": json.loads((ROOT / "sources.json").read_text(encoding="utf-8")),
        "scope": {"checkpoints": scope.names, "coverage": scope.coverage,
                  "full_task_checkpoint_count": 5},
        "environment": {"python": sys.version.split()[0],
                        "upstream_python_ready": sys.version_info >= (3, 12),
                        "docker_binary_present": shutil.which("docker") is not None,
                        "docker_daemon": "not_checked",
                        "upstream_package_present": importlib.util.find_spec("slop_code") is not None,
                        "owner_socket_support": "not_checked"},
        "rpnh_public_entry": {"callable": callable(run_agent_task),
                              "task_spec_parameters": parameters},
        "runner_contract": ({"status": "not_run", "reason": "No runner checkout supplied."}
                            if runner_source is None else inspect_runner_source(runner_source)),
        "development_pilot": {"status": "implemented_not_run",
            "entry": "rpnh_scb.run", "workspace_owner": "upstream_scb_session",
            "mechanism": "native_managed_session_command",
            "adaptations": ["explicit_disabled_upstream_caps", "network_none_solver",
                            "fresh_solver_container_per_checkpoint", "separate_model_call_and_wall_bounds"]},
        "faithful_adapter": {"status": "blocked", "reason":
            "The official AgentRunner shim cannot represent unknown token/cost usage without misleading zeros. Use only the explicitly selected development pilot; no official-runner acceptance is claimed."},
        "execution": {"owner": "not_run", "model": "not_run", "official_runner": "not_run",
                      "official_evaluation": "not_run"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=int, default=3)
    parser.add_argument("--runner-source", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(report(prefix=args.prefix, runner_source=args.runner_source),
                     ensure_ascii=False, indent=2))
    return 2  # An integration preflight blocker is not a successful run.


if __name__ == "__main__":
    raise SystemExit(main())
