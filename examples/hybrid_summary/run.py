"""Run the public model -> native plugin -> model workflow example."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _support.profile import write_scripted_profile
from cpn.plugins.catalog import load_catalog, read_config
from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
from cpn.rpnh.agent_workflows import AgentWorkflowGraph


ROOT = Path(__file__).resolve().parent


def _load_graph(path: Path) -> AgentWorkflowGraph:
    with path.open(encoding="utf-8") as source:
        document = json.load(source)
    return AgentWorkflowGraph.from_mapping(document)


def run_example(
        *, run_dir: Path, input_path: Path, graph_path: Path,
        plugin_config_path: Path, execution_config_path: Path,
) -> dict:
    prompt = input_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("hybrid example input must be nonempty")
    configuration = read_config(plugin_config_path)
    catalog = load_catalog(configuration)
    spec = AgentTaskSpec(
        run_dir=run_dir,
        prompt=prompt,
        stages=(),
        execution_config_path=execution_config_path,
        workflow_graph=_load_graph(graph_path),
        # A Located-input read plus bounded correction of a rejected
        # publication can require more than two real-model turns.
        max_attempts_per_stage=4,
        max_parallel_nodes=1,
        owner_statement="RPNH public hybrid summary example",
        plugin_configuration=configuration,
        plugin_catalog_digest=catalog.digest,
    )
    return run_agent_task(spec)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=ROOT / "input.txt")
    parser.add_argument("--graph", type=Path, default=ROOT / "graph.json")
    parser.add_argument(
        "--plugins", type=Path,
        default=ROOT.parent / "native_plugin" / "plugins.json")
    parser.add_argument(
        "--execution", type=Path,
        help="user-owned execution selection; omit for the scripted fixture")
    args = parser.parse_args(argv)
    if args.execution is not None:
        result = run_example(
            run_dir=args.run_dir.resolve(),
            input_path=args.input.resolve(),
            graph_path=args.graph.resolve(),
            plugin_config_path=args.plugins.resolve(),
            execution_config_path=args.execution.resolve(),
        )
    else:
        with tempfile.TemporaryDirectory(prefix="rpnh-example-profile-") as raw:
            execution = write_scripted_profile(Path(raw) / "profile")
            result = run_example(
                run_dir=args.run_dir.resolve(),
                input_path=args.input.resolve(),
                graph_path=args.graph.resolve(),
                plugin_config_path=args.plugins.resolve(),
                execution_config_path=execution,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["terminal_evidence_ref"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
