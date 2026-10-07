"""Installed receiver CLI over the same check/plan/prepare/launch functions."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

from .environment_contracts import EnvironmentContractError
from .environment_local_contracts import (
    EnvironmentSelection, LocalEnvironmentBinding, EnvironmentCheckReport,
    EnvironmentResolutionLock, EnvironmentPreparationPlan, PreparationReceipt,
    public_check_summary,
)
from .environment_check import check_environment, observed_resolution
from .environment_plan import ConcreteSelections, plan_environment, resolve_local_wheels
from .environment_prepare import PreparationExecutionContext, LaunchExecutionContext, prepare_environment
from .environment_setup import render_environment_setup
from .environment_host import launch_package
from .environment_requirements import read_package_environment
from .share_packages import PackageResolutionLock, PackageError, canonical_bytes, strict_json, sha256

COMMANDS = frozenset({"check-environment", "resolve-environment", "plan-environment", "prepare-environment", "setup-instructions", "run"})


def _read(path, cls):
    with Path(path).open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "local environment document exceeds bound")
    return cls.from_bytes(raw)


def _private_write(path, payload):
    # An explicit output destination authorizes this local report write only.
    import os
    with Path(path).open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(payload)


def _material(parser):
    parser.add_argument("--lock", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--local-package", action="append", default=[])
    parser.add_argument("--entry", default="main")


def _selection(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selection")
    group.add_argument("--binding")


def _confirm(description, exact):
    if not sys.stdin.isatty():
        return False
    print(description, file=sys.stderr)
    print("Type the exact identity below to authorize these displayed actions only:\n" + exact, file=sys.stderr)
    return input().strip() == exact


def _public_report(check):
    value = public_check_summary(check)
    value["checks"] = [row for row in value["checks"] if not row["check_id"].startswith("installed_distribution:")]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rpnh package", description="Explicit receiver environment preparation")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check-environment", "resolve-environment", "plan-environment", "prepare-environment", "run"):
        command = commands.add_parser(name)
        _material(command)
        if name != "run":
            _selection(command)
        if name in {"resolve-environment", "plan-environment", "prepare-environment"}:
            command.add_argument("--check", required=True)
        if name in {"plan-environment", "prepare-environment", "run"}:
            command.add_argument("--resolved-selections", required=True)
        if name == "check-environment":
            command.add_argument("--resolved-selections")
        if name in {"resolve-environment", "plan-environment"}:
            command.add_argument("--wheel", action="append", default=[])
        if name == "resolve-environment":
            command.add_argument("--allow-existing-changes", action="store_true",
                help="include visible exact wheel replacements in a later approved plan")
        if name == "prepare-environment":
            command.add_argument("--plan", required=True)
            command.add_argument("--state-dir", help="private report directory; default is the local RPNH environment-preparation directory")
        if name == "run":
            command.add_argument("--binding", required=True)
            command.add_argument("--receipt", required=True)
            command.add_argument("--owner-request", required=True)
            command.add_argument("--run-dir", required=True)
        command.add_argument("--output", help="write the private complete document; refuses overwrite")
    setup = commands.add_parser("setup-instructions")
    setup.add_argument("--plan", required=True)
    setup.add_argument("--format", choices=("text", "json"), default="text")
    setup.add_argument("--output", required=True, help="private local setup document destination; refuses overwrite")
    args = parser.parse_args(argv)
    try:
        if args.output and Path(args.output).exists():
            raise EnvironmentContractError("ENVIRONMENT_OUTPUT_EXISTS", "output already exists; choose another filename")
        if args.command == "setup-instructions":
            plan = _read(args.plan, EnvironmentPreparationPlan)
            document = render_environment_setup(plan, format=args.format)
            _private_write(args.output, document.content.encode("utf-8"))
            print(canonical_bytes({"plan_digest": plan.digest, "setup_document_written": True, "grants_authority": False}).decode("ascii"))
            return 0
        lock = PackageResolutionLock(Path(args.lock).read_bytes())
        requirements = read_package_environment(args.archive, args.local_package, args.entry, package_lock=lock)
        local = _read(args.selection, EnvironmentSelection) if getattr(args, "selection", None) else _read(args.binding, LocalEnvironmentBinding)
        resolution = _read(args.resolved_selections, EnvironmentResolutionLock) if getattr(args, "resolved_selections", None) else None
        check = _read(args.check, EnvironmentCheckReport) if getattr(args, "check", None) else None
        exit_code = 0
        if args.command == "check-environment":
            result = check_environment(requirements, local, resolution=resolution)
            public = _public_report(result)
            exit_code = 5 if public["aggregate"] == "stale" else 0 if public["aggregate"] == "passed_for_checked_scope" else 3
        elif args.command == "resolve-environment":
            if args.wheel:
                result = resolve_local_wheels(requirements, local, check, args.wheel,
                    allow_existing_changes=args.allow_existing_changes).resolution
            else:
                result = observed_resolution(requirements, check)
            public = {"target": requirements.target.to_dict(), "resolution_digest": result.digest,
                      "unresolved": result.to_dict()["unresolved"], "coverage": result.to_dict()["coverage"]}
            exit_code = 3 if public["unresolved"] else 0
        elif args.command == "plan-environment":
            artifacts = tuple((sha256(Path(path).read_bytes()), str(Path(path).absolute())) for path in args.wheel)
            result = plan_environment(requirements, local, check, concrete_selections=ConcreteSelections(resolution, artifacts))
            public = {"target": requirements.target.to_dict(), "plan_digest": result.digest,
                "actions": [{"action_id": a["action_id"], "kind": a["kind"], "trusted_adapter_contract": a["trusted_adapter_contract"]} for a in result.to_dict()["actions"]],
                "unresolved": result.to_dict()["unresolved"], "authorization_requirements": result.to_dict()["authorization_requirements"]}
            exit_code = 3 if public["unresolved"] else 0
        elif args.command == "prepare-environment":
            plan = _read(args.plan, EnvironmentPreparationPlan)
            def authorize(digest, actions, target):
                return _confirm("Preparation writes private reports to " + str(Path(context.report_directory).absolute()) + "\n" +
                    render_environment_setup(plan, format="text").content, digest)
            context = PreparationExecutionContext(requirements, local, resolution, check, args.archive,
                tuple(args.local_package), authorize, report_directory=args.state_dir)
            prepared = prepare_environment(plan, execution_context=context)
            result = prepared.receipt
            public = {"target": requirements.target.to_dict(), "receipt_digest": result.digest,
                      "action_results": result.to_dict()["action_results"], "failure": result.to_dict()["failure"],
                      "preparation_only": True}
            exit_code = 5 if public["failure"] in {"ENVIRONMENT_PREPARATION_CANCELLED", "ENVIRONMENT_BINDING_STALE"} else 4 if public["failure"] else 0
        else:
            request = strict_json(Path(args.owner_request).read_bytes(), path="owner-request")
            receipt = _read(args.receipt, PreparationReceipt)
            exact = sha256(canonical_bytes({"binding_digest": local.digest, "owner_request": request, "run_dir": str(Path(args.run_dir).absolute())}))
            def authorize_run(binding_digest, owner_request, run_dir):
                return _confirm("Separate business run authorization. Run directory: " + str(Path(run_dir).absolute()) +
                    "\nExact owner request: " + canonical_bytes(owner_request).decode("ascii"), exact)
            result = launch_package(lock, requirements.previews, local, receipt, owner_request=request,
                execution_context=LaunchExecutionContext(args.archive, tuple(args.local_package), resolution, args.run_dir, authorize_run)).wait()
            # Local interpreter paths stay in an explicitly requested private result.
            public = {key: value for key, value in result.items() if key != "host_python"}
        if args.output:
            _private_write(args.output, result.to_bytes() if hasattr(result, "to_bytes") else canonical_bytes(result))
        print(canonical_bytes(public).decode("ascii"))
        return exit_code
    except (PackageError, OSError, ValueError, TypeError) as exc:
        code = getattr(exc, "code", "INVALID_ENVIRONMENT_CONTRACT")
        print(canonical_bytes({"error": {"reason_code": code}}).decode("ascii"), file=sys.stderr)
        if code in {"PREPARATION_AUTHORIZATION_REQUIRED", "RUN_AUTHORIZATION_REQUIRED", "ENVIRONMENT_PREPARATION_INCOMPLETE"}:
            return 4
        if code in {"ENVIRONMENT_BINDING_STALE", "ENVIRONMENT_PREPARATION_CANCELLED"}:
            return 5
        if code in {"ENVIRONMENT_SELECTION_UNRESOLVED", "ENVIRONMENT_REQUIREMENTS_UNDECLARED", "HOST_PROFILE_UNAVAILABLE"}:
            return 3
        return 2
