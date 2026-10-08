"""Run an explicitly selected development prefix with the original grader.

Never use this CLI to report an untouched official AgentRunner experiment.
No install, image build/pull, credential setup or provider selection is automatic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .contracts import CHECKPOINTS, CheckpointScope
from .pilot import CheckpointPilot, DevelopmentCondition, write_new

ROOT = Path(__file__).resolve().parent


def verify_checkout(path, revision):
    observed = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if observed != revision:
        raise ValueError("upstream checkout differs from the frozen revision")
    if subprocess.run(["git", "-C", str(path), "diff", "--quiet", "HEAD"], check=False).returncode:
        raise ValueError("upstream tracked source has local changes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-source", type=Path, required=True)
    parser.add_argument("--problems-source", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--codex-binary", type=Path,
                        help="explicit official Codex executable; required for a Codex execution selection")
    parser.add_argument("--condition", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prefix", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--pass-policy", required=True,
                        choices=["any", "any-case", "all-cases", "all-non-error-cases",
                                 "core-cases", "any-core-cases", "all-core-cases"])
    parser.add_argument("--acknowledge-development-model-run", action="store_true", required=True)
    args = parser.parse_args(argv)
    pins = json.loads((ROOT / "sources.json").read_text())
    verify_checkout(args.runner_source, pins["runner"]["revision"])
    verify_checkout(args.problems_source, pins["problems"]["revision"])
    condition = DevelopmentCondition(**json.loads(args.condition.read_text()))
    if args.output.exists():
        raise ValueError("output directory must be absent")
    sys.path.insert(0, str(args.runner_source.resolve() / "src"))
    import slop_code
    if not Path(slop_code.__file__).resolve().is_relative_to(args.runner_source.resolve()):
        raise ValueError("imported slop_code is outside the pinned source checkout")
    import yaml
    from pydantic import TypeAdapter
    from slop_code.agent_runner.runner import get_task_for_checkpoint
    from slop_code.evaluation import PassPolicy, ProblemConfig, run_checkpoint
    from slop_code.execution import EnvironmentSpecType, Session

    evaluation_environment = TypeAdapter(EnvironmentSpecType).validate_python(
        yaml.safe_load(args.environment.read_text()))
    if evaluation_environment.type != "docker":
        raise ValueError("the pilot requires a Docker environment")
    problem = ProblemConfig.from_yaml(args.problems_source.resolve() / "code_search")
    checkpoints = list(problem.iterate_checkpoint_items())
    if tuple(name for name, _ in checkpoints) != CHECKPOINTS or problem.static_assets:
        raise ValueError("pinned code_search checkpoint/assets contract differs")
    solver_environment = evaluation_environment.model_copy(update={
        "docker": evaluation_environment.docker.model_copy(update={"network": "none"})})
    image_name = solver_environment.get_base_image()
    image_id = subprocess.check_output(
        [solver_environment.docker.binary, "image", "inspect", image_name, "--format", "{{.Id}}"],
        text=True).strip()
    scope = CheckpointScope(CHECKPOINTS[:args.prefix])
    policy = PassPolicy(args.pass_policy)
    template = args.template.read_text()
    with Session.from_environment_spec(solver_environment, base_dir=None,
                                      is_agent_infer=True) as session:
        pilot = CheckpointPilot(session, execution=args.execution.resolve(),
                                evidence_root=args.output.resolve(), condition=condition,
                                solver_image_id=image_id, scope=scope, codex_binary=args.codex_binary)
        write_new(pilot.root / "input-identities.json", {
            "environment_sha256": hashlib.sha256(args.environment.read_bytes()).hexdigest(),
            "template_sha256": hashlib.sha256(args.template.read_bytes()).hexdigest(),
            "pass_policy": policy.value, "scope": list(scope.names),
            "original_agent_runner": "not_run", "mode": "adapted_development_prefix"})
        summaries = []
        for index, (name, checkpoint) in enumerate(checkpoints[:args.prefix]):
            # Only this current prompt is loaded and rendered. No grader result
            # or future prompt is passed into the RPNH task.
            prompt_dir = pilot.root / f"rendered-{name}"
            prompt_dir.mkdir()
            prompt = get_task_for_checkpoint(name, problem.get_checkpoint_spec(name), template,
                problem.entry_file, solver_environment, is_first_checkpoint=index == 0,
                output_path=prompt_dir, agent_type="rpnh_scb_pilot")
            pilot.run(name, prompt)
            grade = run_checkpoint(submission_path=pilot.root / name / "snapshot",
                                   problem=problem, checkpoint=checkpoint,
                                   env_spec=evaluation_environment)
            grade.save(pilot.root / name)
            passed = policy.check(grade.pass_counts, grade.total_counts)
            summaries.append({"checkpoint": name, "original_evaluator": "executed",
                              "infrastructure_failure": grade.infrastructure_failure,
                              "passed_selected_policy": passed})
            # Retain upstream ANY_CASE's continue-on-test-failure policy. Never
            # continue through evaluator infrastructure failure in this pilot.
            if grade.infrastructure_failure or (not passed and policy != PassPolicy.ANY_CASE):
                break
        write_new(pilot.root / "development-summary.json", {
            "mode": "adapted_development_prefix", "scope": scope.coverage,
            "official_agent_runner": "not_run", "checkpoints": summaries,
            "usage": {"cost_usd": None, "tokens": None, "status": "unavailable"}})
    return 0 if len(summaries) == args.prefix and all(
        not row["infrastructure_failure"] for row in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
