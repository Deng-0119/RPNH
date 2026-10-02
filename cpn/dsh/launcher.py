"""Console entry point for the bundled DSH integration runner."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def resolve_execution_path(*args, **kwargs):
    from cpn.rpnh.user_config import resolve_execution_path as resolve
    return resolve(*args, **kwargs)


def profile_for_path(*args, **kwargs):
    from cpn.rpnh.user_config import profile_for_path as resolve
    return resolve(*args, **kwargs)


def missing_credentials(*args, **kwargs):
    from cpn.rpnh.user_config import missing_credentials as missing
    return missing(*args, **kwargs)


def load_llm_execution_selection(*args, **kwargs):
    from cpn.llm_adapters import load_llm_execution_selection as load
    return load(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh-dsh",
        description="Run the bundled RPNH integration against a pinned DSH checkout.",
    )
    parser.add_argument("dsh_checkout", help="path to the pinned DSH source checkout")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to DSH")
    return parser


def _application_mode(
        parser: argparse.ArgumentParser, arguments: list[str],
) -> tuple[Path | None, bool, bool, bool, list[str]]:
    """Extract launcher-owned routing flags without parsing DSH arguments."""

    execution: Path | None = None
    offline = history = resume = False
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--execution":
            if execution is not None:
                parser.error("--execution may be specified only once")
            index += 1
            if index == len(arguments):
                parser.error("--execution requires a path")
            execution = Path(arguments[index])
        elif argument.startswith("--execution="):
            if execution is not None:
                parser.error("--execution may be specified only once")
            value = argument.partition("=")[2]
            if not value:
                parser.error("--execution requires a path")
            execution = Path(value)
        else:
            forwarded.append(argument)
            offline = offline or argument == "--offline"
            history = history or argument == "--history"
            resume = resume or argument == "--resume"
        index += 1
    return execution, offline, history, resume, forwarded


def _public_profile(execution_path: Path) -> dict[str, object]:
    profile = profile_for_path(execution_path)
    missing = missing_credentials(execution_path)
    if missing:
        raise ValueError(
            "selected provider requires environment variable(s): "
            + ", ".join(missing)
            + ". Set the key in your shell, then run `rpnh doctor`.")
    selection = load_llm_execution_selection(execution_path)
    policy = selection.as_registry_policy()
    routes = policy.get("route_provenance")
    if (not isinstance(routes, list) or len(routes) != 1
            or not isinstance(routes[0], dict)
            or not isinstance(routes[0].get("transport"), str)):
        raise ValueError("selected provider lacks one exact transport route")
    return {
        "schema_version": "rpnh/dsh_execution_profile/v3",
        "profile": profile.name,
        "selection_id": profile.selection_id,
        "provider": profile.provider,
        "provider_display_name": profile.provider_display_name,
        "model_condition": selection.input_target.model_condition,
        "adapter_kind": selection.adapter_kind,
        "transport_kind": routes[0]["transport"],
        "timeout_seconds": selection.timeout_seconds,
        "max_output_tokens": selection.input_target.max_output_tokens,
        "max_response_bytes": selection.input_target.max_response_bytes,
        "reasoning_effort": selection.reasoning_effort,
        "supported_reasoning_efforts": list(
            selection.supported_reasoning_efforts),
        "default_reasoning_effort": selection.default_reasoning_effort,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    execution, offline, history, resume, forwarded = _application_mode(
        parser, list(args.args))
    if offline and execution is not None:
        parser.error("--offline cannot be combined with --execution")
    if history and execution is not None:
        parser.error("--history does not accept --execution")
    if resume and not offline and execution is None:
        parser.error(
            "--resume requires explicit --offline or --execution until the "
            "original profile can be reconstructed")

    managed_arguments = list(forwarded)
    if not history and not offline:
        try:
            execution_path = resolve_execution_path(
                execution, save_default=False, allow_interactive_setup=False)
            public_profile = _public_profile(execution_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        managed_arguments.extend((
            "--execution-path", str(execution_path),
            "--execution-profile", json.dumps(
                public_profile, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")),
        ))

    runner = importlib.resources.files("integrations.dsh").joinpath("run.sh")
    with importlib.resources.as_file(runner) as runner_path:
        return subprocess.run(
            ["bash", str(runner_path), sys.executable, args.dsh_checkout,
             *managed_arguments],
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
