"""Small public CLI; runtime lifecycle remains owned by the trial driver."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from .evidence import assert_public_safe, validate_result_manifest
from .export import export_source_overlay, source_identity
from .source import TASK_IDS, json_bytes, load_json, validate_task


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rpnh-erp", description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-task", help="Verify one unchanged pinned upstream task", allow_abbrev=False)
    inspect.add_argument("--upstream", required=True, type=Path)
    inspect.add_argument("--task", required=True, choices=TASK_IDS)

    plan = commands.add_parser("check-plan", help="Check arithmetic over supplied visible observations", allow_abbrev=False)
    plan.add_argument("--input", required=True, type=Path)

    report = commands.add_parser("validate-report", help="Validate a portable record with the explicit frozen contract", allow_abbrev=False)
    report.add_argument("--manifest", required=True, type=Path)
    report.add_argument("--shared-validator", required=True, type=Path)
    report.add_argument("--artifacts-root", required=True, type=Path)
    report.add_argument("--source-root", type=Path)

    export = commands.add_parser("export-source", help="Export the complete current ERP source allowlist", allow_abbrev=False)
    export.add_argument("--source-root", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)

    run = commands.add_parser("run", help="Run one explicitly authorized existing-model trial", allow_abbrev=False)
    run.add_argument("--upstream", required=True, type=Path)
    run.add_argument("--task", required=True, choices=TASK_IDS)
    run.add_argument("--execution-selection", required=True, type=Path,
                     help="Existing private RPNH exact-model selection; never exported")
    run.add_argument("--run-root", required=True, type=Path)
    run.add_argument("--condition-id", required=True)
    run.add_argument("--source-root", required=True, type=Path)
    run.add_argument("--shared-validator", required=True, type=Path)
    run.add_argument("--authorize-existing-model", action="store_true", required=True,
                     help="Confirm prior user authorization for this task and existing exact model")
    run.add_argument("--previous-record", type=Path,
                     help="Retained predecessor manifest for a new append-only condition")
    run.add_argument("--firewall-package", action="append", type=Path, default=[],
                     help="Explicit task-local firewall package; repeat for each package")
    run.add_argument("--build-compatibility", action="store_true",
                     help="Declare the runtime build compatibility repair as a separate condition")
    run.add_argument("--codex-binary", type=Path,
                     help="Official Codex executable for the declared closed response endpoint")
    return parser


def _dispatch(args):
    if args.command == "inspect-task":
        return validate_task(args.upstream, args.task).public_identity(), 0
    if args.command == "check-plan":
        from .planning import validate_plan
        result = validate_plan(load_json(args.input.read_bytes()))
        return result, 1 if result["status"] == "domain_infeasible" else 0
    if args.command == "validate-report":
        result = validate_result_manifest(load_json(args.manifest.read_bytes()),
            shared_validator_path=args.shared_validator, artifacts_root=args.artifacts_root,
            source_root=args.source_root)
        return result, 0
    if args.command == "export-source":
        identity = source_identity(args.source_root)
        result = export_source_overlay(args.source_root, args.output,
            paths=[row["path"] for row in identity["owned_files"]])
        return result, 0
    if args.command == "run":
        # Do not import Harbor/driver for offline commands or rejected arguments.
        from .driver import run_trial
        result = asyncio.run(run_trial(upstream=args.upstream, task_id=args.task,
            execution_selection=args.execution_selection, run_root=args.run_root,
            condition_id=args.condition_id, source_root=args.source_root,
            shared_validator=args.shared_validator,
            authorize_existing_model=args.authorize_existing_model,
            previous_record=args.previous_record,
            firewall_packages=tuple(args.firewall_package),
            build_compatibility=args.build_compatibility,
            codex_binary=args.codex_binary))
        return result, 0
    raise ValueError("unsupported command")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, code = _dispatch(args)
        assert_public_safe(result)
        payload = json_bytes(result).decode("utf-8")
    except KeyboardInterrupt:
        print('{"error": "interrupted"}', file=sys.stderr)
        return 130
    except Exception as exc:
        # Exception text can contain private profile paths, endpoints or secrets.
        print(json_bytes({"error": "command_failed", "error_type": type(exc).__name__}).decode(),
              file=sys.stderr, end="")
        return 1
    print(payload, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
