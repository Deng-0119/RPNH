"""Run one documented serial, parallel, document or long workflow pattern."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _support.profile import write_scripted_profile
from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.agent_workflows import AgentWorkflowGraph
from cpn.rpnh.inspection import project_registry_net


ROOT = Path(__file__).resolve().parent
SCENARIO_ROOT = ROOT / "scenarios"
SCENARIOS = ("serial", "parallel", "document", "long_process")
_FIELDS = {
    "schema_version", "name", "description", "task", "expected_output",
    "max_attempts_per_node", "max_parallel_nodes", "graph",
}


def load_scenario(name: str) -> Mapping[str, Any]:
    if name not in SCENARIOS:
        raise ValueError("unknown workflow-gallery scenario")
    document = json.loads(
        (SCENARIO_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    if (not isinstance(document, dict) or set(document) != _FIELDS
            or document.get("schema_version") != "rpnh/example_workflow_pattern/v1"
            or not all(isinstance(document.get(field), str)
                       and document[field].strip()
                       for field in ("name", "description", "task",
                                     "expected_output"))
            or isinstance(document.get("max_attempts_per_node"), bool)
            or not isinstance(document.get("max_attempts_per_node"), int)
            or document["max_attempts_per_node"] < 1
            or isinstance(document.get("max_parallel_nodes"), bool)
            or not isinstance(document.get("max_parallel_nodes"), int)
            or document["max_parallel_nodes"] < 1):
        raise ValueError("workflow-gallery scenario is malformed")
    AgentWorkflowGraph.from_mapping(document["graph"])
    return document


def run_example(
        *, scenario_name: str, run_dir: Path,
        execution_config_path: Path,
) -> dict[str, Any]:
    scenario = load_scenario(scenario_name)
    graph = AgentWorkflowGraph.from_mapping(scenario["graph"])
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt=scenario["task"],
        stages=(),
        execution_config_path=execution_config_path,
        workflow_graph=graph,
        max_attempts_per_stage=scenario["max_attempts_per_node"],
        max_parallel_nodes=scenario["max_parallel_nodes"],
        owner_statement=f"RPNH public workflow pattern: {scenario_name}",
    ))
    projection = project_registry_net(run_dir, catalog=agent_task_catalog())
    expected = scenario["expected_output"]
    return {
        "schema_version": "rpnh/example_workflow_pattern_result/v1",
        "scenario": scenario_name,
        "status": (
            "PASS" if result["stop_reason"] == "terminal"
            and result["terminal_evidence_ref"] is not None
            and result["output"] == expected else "FAIL"),
        "run_dir": result["run_dir"],
        "stop_reason": result["stop_reason"],
        "terminal_evidence_ref": result["terminal_evidence_ref"],
        "output": result["output"],
        "expected_output": expected,
        "actual_model_call_counts": result["actual_model_call_counts"],
        "petri_net": projection["summary"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list scenario names")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--execution", type=Path,
        help="user-owned execution selection; omit for the scripted fixture")
    args = parser.parse_args(argv)
    if args.list:
        for name in SCENARIOS:
            scenario = load_scenario(name)
            print(f"{name}\t{scenario['description']}")
        return 0
    if args.scenario is None or args.run_dir is None:
        parser.error("--scenario and --run-dir are required unless --list is used")
    if args.execution is not None:
        result = run_example(
            scenario_name=args.scenario,
            run_dir=args.run_dir.resolve(),
            execution_config_path=args.execution.resolve(),
        )
    else:
        with tempfile.TemporaryDirectory(
                prefix="rpnh-workflow-pattern-profile-") as raw:
            execution = write_scripted_profile(Path(raw) / "profile")
            result = run_example(
                scenario_name=args.scenario,
                run_dir=args.run_dir.resolve(),
                execution_config_path=execution,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
