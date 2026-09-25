"""Interactive user-facing RPNH terminal."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import sys
from typing import Sequence
from uuid import uuid4

from cpn.rpnh.agent_tasks import AgentStage
from cpn.rpnh.main_session import (
    MainSession,
    MainSessionExecutionFailed,
    MainSessionPaused,
)
from cpn.rpnh.user_config import (
    config_path,
    discover_profiles,
    interactive_setup,
    missing_credentials,
    profile_for_path,
    read_selected_path,
    resolve_execution_path,
    select_profile,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh",
        description="RPNH — an AI assistant for conversations and tasks",
        epilog=(
            "Start with `rpnh init`; check configuration with `rpnh doctor`. "
            "Commands: `rpnh config {init|add|build|list|show|use|setup|doctor}` and "
            "`rpnh net --run RUN_DIR`"),
    )
    parser.add_argument(
        "--execution", type=Path,
        help=("current LLM execution selection; otherwise use "
              "RPNH_EXECUTION_CONFIG or the saved user default"),
    )
    parser.add_argument(
        "--save-default", action="store_true",
        help="save --execution as the user default, then start RPNH",
    )
    session = parser.add_mutually_exclusive_group()
    session.add_argument(
        "--session-dir", type=Path,
        help="absent operational directory for this fresh CLI session",
    )
    session.add_argument(
        "--resume", type=Path, metavar="SESSION_DIR",
        help="resume an existing RPNH main session",
    )
    parser.add_argument(
        "--prompt", help="run one main-session turn and exit the input loop",
    )
    parser.add_argument(
        "--frontend", choices=("auto", "codex", "basic", "opencode"), default="auto",
        help=("auto uses compatible Codex 0.155.0 when available, "
              "otherwise the built-in terminal; OpenCode 1.18.32 is explicit"),
    )
    return parser


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _config_path() -> Path:
    return config_path()


def _execution_path(explicit: Path | None, *, save_default: bool) -> Path:
    return resolve_execution_path(
        explicit, save_default=save_default,
        allow_interactive_setup=False)


def _config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh config",
        description="RPNH-owned provider and exact-model configuration",
    )
    commands = parser.add_subparsers(dest="config_command", required=True)
    init = commands.add_parser(
        "init", help="create an empty catalog for manual/scripted configuration")
    init.add_argument("--catalog", type=Path)
    commands.add_parser("list", help="list user-generated profiles")
    commands.add_parser("show", help="show the active profile")
    use = commands.add_parser(
        "use", help="select one user-configured profile or exact pair")
    use.add_argument("provider_or_profile")
    use.add_argument("model", nargs="?")
    commands.add_parser("setup", help="create or select a model interactively")
    commands.add_parser("add", help="add a model without editing JSON")
    doctor = commands.add_parser("doctor", help="check local configuration without model calls")
    doctor.add_argument("--execution", type=Path)
    doctor.add_argument("--json", action="store_true")
    build = commands.add_parser(
        "build", help="generate profiles from one provider-model catalog")
    build.add_argument("--catalog", type=Path)
    build.add_argument("--output-root", type=Path)
    build.add_argument(
        "--check", action="store_true",
        help="verify generated profiles without changing files")
    return parser


def _config_command(argv: Sequence[str]) -> int:
    args = _config_parser().parse_args(argv)
    if args.config_command == "init":
        from cpn.rpnh.provider_setup import initialize_provider_catalog
        _print_json(initialize_provider_catalog(args.catalog))
        return 0
    if args.config_command == "list":
        selected = read_selected_path()
        _print_json([
            profile.as_public_dict(selected=selected == profile.path)
            for profile in discover_profiles()
        ])
        return 0
    if args.config_command == "show":
        selected = read_selected_path()
        if selected is None:
            raise ValueError("no RPNH profile is selected")
        _print_json(profile_for_path(selected).as_public_dict(selected=True))
        return 0
    if args.config_command == "use":
        profile = select_profile(args.provider_or_profile, args.model)
        _print_json(profile.as_public_dict(selected=True))
        return 0
    if args.config_command == "build":
        from cpn.rpnh.provider_setup import build_provider_catalog
        _print_json(build_provider_catalog(
            args.catalog, args.output_root, check=args.check))
        return 0
    if args.config_command == "doctor":
        return _doctor(args.execution, as_json=args.json)
    _require_setup_terminal()
    if args.config_command == "add":
        from cpn.rpnh.onboarding import setup_profile
        setup_profile(add_new=True)
    else:
        interactive_setup()
    return 0


def _require_setup_terminal() -> None:
    if not sys.stdin.isatty():
        raise ValueError(
            "interactive setup needs a terminal; run `rpnh init` there, or "
            "use `rpnh config init/build/use` for scripted configuration")


def _doctor(execution: Path | None = None, *, as_json: bool = False) -> int:
    from cpn.rpnh.onboarding import configuration_report
    report = configuration_report(execution)
    if as_json:
        _print_json(report)
    else:
        print("RPNH configuration check (offline)")
        for item in report["checks"]:
            print(f"  {item['status'].upper()}: {item['check']}: {item['detail']}")
        print("No provider request sent; this does not test network or model availability.")
        if report["ready"]:
            print("Local checks passed. Start with: rpnh")
    return 0 if report["ready"] else 1


def _choose_frontend(requested: str) -> str:
    if requested != "auto":
        return requested
    if not sys.stdin.isatty():
        return "basic"
    from cpn.frontend.codex_app_server import resolve_codex_binary
    try:
        resolve_codex_binary()
    except (OSError, RuntimeError, ValueError):
        print("Using the built-in terminal. Optional Codex 0.155.0 frontend is unavailable.",
              file=sys.stderr)
        return "basic"
    return "codex"


def _net_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rpnh net", description="RPNH read-only PetriNet viewer")
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--show-resources", action="store_true")
    parser.add_argument("--resources-only", action="store_true")
    parser.add_argument("--node")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--view", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    return parser


def _net_command(argv: Sequence[str]) -> int:
    args = _net_parser().parse_args(argv)
    if args.view:
        incompatible = []
        if args.resources_only:
            incompatible.append("--resources-only")
        if args.node is not None:
            incompatible.append("--node")
        if args.output is not None:
            incompatible.append("--output")
        if args.format != "text":
            incompatible.append("--format")
        if incompatible:
            raise ValueError(
                "rpnh net --view does not accept " + ", ".join(incompatible))
    forwarded = ["net", "view" if args.view else "show"]
    forwarded.extend(("--run", str(args.run)))
    if args.show_resources:
        forwarded.append("--show-resources")
    if args.view:
        if args.no_open:
            forwarded.append("--no-open")
        forwarded.extend(("--host", args.host, "--port", str(args.port)))
    else:
        forwarded.extend(("--format", args.format))
        if args.resources_only:
            forwarded.append("--resources-only")
        if args.node is not None:
            forwarded.extend(("--node", args.node))
        if args.output is not None:
            forwarded.extend(("--output", str(args.output)))
    from cpn.cli import main as cpn_main
    return cpn_main(forwarded)


@dataclass(slots=True)
class _BasicFrontendState:
    """Process-local focus; task execution remains independent/background."""

    selected_task_id: str | None = None


def _selected_task_id(state: _BasicFrontendState) -> str:
    if state.selected_task_id is None:
        raise ValueError("no child task is selected; use /switch TASK_ID")
    return state.selected_task_id


def _run_task_action(
        session: MainSession, task_id: str, action: str,
        payload: str | None,
) -> None:
    if action == "status" and payload is None:
        _print_json(session.task_control.status(task_id))
    elif action == "result" and payload is None:
        _print_json(session.task_control.result(task_id))
    elif action == "net":
        options = shlex.split(payload or "")
        if not options:
            projection = session.task_control.net(task_id)
            _print_json({"source": projection["source"],
                         "summary": projection["summary"]})
        elif options[0] == "view":
            flags = options[1:]
            allowed = {"--show-resources", "--no-open"}
            if (any(flag not in allowed for flag in flags)
                    or len(flags) != len(set(flags))):
                raise ValueError(
                    "usage: /net view [--show-resources] [--no-open]")
            handle = session.task_control.get(task_id)
            arguments = ["--run", str(handle.run_dir), "--view", *flags]
            _net_command(arguments)
        else:
            raise ValueError(
                "usage: /net [view [--show-resources] [--no-open]]")
    elif action == "stop" and payload is None:
        _print_json(session.task_control.stop(task_id))
    elif action == "resume" and payload is None:
        _print_json(session.task_control.resume(task_id))
    elif action == "message" and payload:
        if " :: " in payload:
            target, body = payload.split(" :: ", 1)
            reply = session.task_control.message(
                task_id, body, target=target.strip())
        else:
            reply = session.task_control.message(task_id, payload)
        _print_json(reply)
    else:
        raise ValueError("invalid task command")


def _task_command(
        session: MainSession, line: str,
        state: _BasicFrontendState | None = None,
) -> bool:
    if line == "/help":
        print("""Commands:
  /tasks                       list child task objects
  /agent PROMPT                launch one independent single-agent task
  /workflow PROMPT             ask the main-session Designer to launch a graph workflow
  /switch ID | /switch main    change the CLI focus without stopping any task
  /current                     show the current CLI focus
  /task ID status              inspect process and Registry projection
  /task ID result              read the registered terminal result
  /task ID net                 show PetriNet summary
  /task ID net view [--show-resources] [--no-open]
  /task ID message TEXT        queue input for a single-agent task
  /task ID message TARGET :: TEXT
  /task ID stop                checkpoint-stop this exact task process
  /task ID resume              resume an owner-stopped task from Registry
  /status | /result | /net | /message | /stop | /resume
                               operate on the selected child task
  /resume                      with main selected, continue its paused turn
  /rollback                    with main selected, return to its prior completed turn
  TEXT                         talk to main, or message the selected single-agent
  /quit                        leave the main session without stopping tasks""")
        return True
    if line == "/tasks":
        session.reconcile_child_registry_links()
        _print_json(session.task_control.list())
        return True
    if line.startswith("/agent "):
        prompt = line[len("/agent "):].strip()
        handle = session.launch(prompt, (AgentStage(
            "worker", "Complete the requested task and return its result."),))
        print(f"launched {handle.task_id} (single_agent)")
        if state is not None:
            state.selected_task_id = handle.task_id
            print(f"selected {handle.task_id}")
        return True
    if line.startswith("/workflow "):
        prompt = line[len("/workflow "):].strip()
        try:
            decision, handle = session.turn(
                prompt, required_task_kind="workflow")
        except MainSessionPaused:
            print(
                "[RPNH workflow design turn paused; use /resume to continue "
                "or /rollback to return to the prior completed main turn]")
            return True
        except MainSessionExecutionFailed as exc:
            print(f"[RPNH workflow design turn failed: {exc}]", file=sys.stderr)
            return True
        print(decision.reply)
        if handle is None:
            print("[main Designer returned no valid workflow graph]")
        else:
            print(f"launched {handle.task_id} (workflow)")
            if state is not None:
                state.selected_task_id = handle.task_id
                print(f"selected {handle.task_id}")
        return True
    if line.startswith("/switch "):
        if state is None:
            raise ValueError("task switching requires the interactive frontend")
        target = line[len("/switch "):].strip()
        if target == "main":
            state.selected_task_id = None
        else:
            session.task_control.get(target)
            state.selected_task_id = target
        print("selected " + (state.selected_task_id or "main"))
        return True
    if line == "/current":
        selected = None if state is None else state.selected_task_id
        _print_json({"selected": selected or "main"})
        return True
    if line.startswith("/task "):
        parts = line.split(maxsplit=3)
        if len(parts) < 3:
            raise ValueError(
                "usage: /task ID {status|result|net|message|stop|resume}")
        _run_task_action(
            session, parts[1], parts[2],
            None if len(parts) == 3 else parts[3])
        return True
    selected_commands = {
        "/status": "status", "/result": "result", "/stop": "stop",
    }
    if line == "/resume":
        if state is None:
            raise ValueError("resume requires interactive state")
        if state.selected_task_id is not None:
            _run_task_action(
                session, state.selected_task_id, "resume", None)
            return True
        try:
            decision, task = session.resume_paused_turn()
        except MainSessionPaused:
            print("[RPNH main turn paused again; its checkpoint is retained]")
            return True
        except MainSessionExecutionFailed as exc:
            print(f"[RPNH main turn failed: {exc}]", file=sys.stderr)
            return True
        _print_main_result(decision, task)
        return True
    if line == "/rollback":
        if state is None or state.selected_task_id is not None:
            raise ValueError(
                "main-session rollback requires /switch main")
        session.rollback_paused_turn()
        print(
            "[RPNH main session returned to the prior completed turn; "
            "the paused child Registry was retained]")
        return True
    if line in selected_commands:
        if state is None:
            raise ValueError("selected-task commands require interactive state")
        _run_task_action(
            session, _selected_task_id(state), selected_commands[line], None)
        return True
    if line == "/net" or line.startswith("/net "):
        if state is None:
            raise ValueError("selected-task commands require interactive state")
        _run_task_action(
            session, _selected_task_id(state), "net",
            None if line == "/net" else line[len("/net "):])
        return True
    if line.startswith("/message "):
        if state is None:
            raise ValueError("selected-task commands require interactive state")
        _run_task_action(
            session, _selected_task_id(state), "message",
            line[len("/message "):])
        return True
    return False


def _route_selected_input(
        session: MainSession, state: _BasicFrontendState, text: str,
) -> None:
    task_id = _selected_task_id(state)
    handle = session.task_control.get(task_id)
    if handle.kind == "workflow":
        if " :: " not in text:
            raise ValueError(
                "selected workflow input requires TARGET :: TEXT")
        target, body = text.split(" :: ", 1)
        reply = session.task_control.message(
            task_id, body, target=target.strip())
    else:
        reply = session.task_control.message(task_id, text)
    _print_json(reply)


def _print_main_result(decision, task) -> None:
    print(decision.reply)
    if not decision.protocol_valid:
        print("[main agent returned prose; no child task was launched]")
    if task is not None:
        print(f"[launched {task.task_id}: {task.kind}]")


def _run_turn(session: MainSession, text: str) -> bool:
    try:
        decision, task = session.turn(text)
    except MainSessionPaused:
        print(
            "[RPNH main turn paused at its checkpoint; use /resume to "
            "continue or /rollback to return to the prior completed turn]")
        return True
    except MainSessionExecutionFailed as exc:
        print(f"[RPNH main turn failed: {exc}]", file=sys.stderr)
        return False
    _print_main_result(decision, task)
    return True


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    from cpn.rpnh.onboarding import SetupCancelled
    if arguments and arguments[0] in {"init", "doctor"}:
        command = arguments[0]
        parser = argparse.ArgumentParser(
            prog=f"rpnh {command}", description=(
                "Create or select a model interactively" if command == "init"
                else "Check local configuration without sending a provider request"))
        if command == "doctor":
            parser.add_argument("--execution", type=Path)
            parser.add_argument("--json", action="store_true")
        options = parser.parse_args(arguments[1:])
        try:
            if command == "doctor":
                return _doctor(options.execution, as_json=options.json)
            _require_setup_terminal()
            interactive_setup()
            return 0
        except SetupCancelled as exc:
            print(str(exc), file=sys.stderr)
            return 130
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"rpnh {command}: error: {exc}", file=sys.stderr)
            return 2
    if arguments and arguments[0] == "plugins":
        from cpn.plugins.cli import main as plugins_main
        try:
            return plugins_main(arguments[1:])
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"rpnh plugins: error: {exc}", file=sys.stderr)
            return 2
    if arguments and arguments[0] in {"config", "net"}:
        try:
            return (_config_command(arguments[1:])
                    if arguments[0] == "config"
                    else _net_command(arguments[1:]))
        except SetupCancelled as exc:
            print(str(exc), file=sys.stderr)
            return 130
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"rpnh: error: {exc}", file=sys.stderr)
            return 2
    parser = _parser()
    args = parser.parse_args(arguments)
    try:
        execution = resolve_execution_path(
            args.execution,
            save_default=args.save_default,
            allow_interactive_setup=(
                args.execution is None and args.prompt is None
                and args.resume is None and sys.stdin.isatty()),
        )
        missing = missing_credentials(execution)
        if missing:
            raise ValueError(
                "selected provider requires environment variable(s): "
                + ", ".join(missing)
                + ". Set the key in your shell, then run `rpnh doctor`.")
    except SetupCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 130
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if args.frontend == "opencode" and args.prompt is not None:
        parser.error(
            "OpenCode is an interactive attach frontend; use --frontend basic "
            "for a one-shot prompt")
    frontend = "basic" if args.prompt is not None else _choose_frontend(args.frontend)
    root = (args.resume or args.session_dir or (
        Path.cwd() / ".rpnh" / "sessions" / (
            ("opencode-" if frontend == "opencode" else "session-")
            + uuid4().hex[:12])))
    if frontend == "opencode":
        try:
            from cpn.frontend.opencode_launcher import run_opencode_frontend
            return run_opencode_frontend(
                root, execution, resume=args.resume is not None)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            parser.error(str(exc))
    if frontend == "codex":
        try:
            from cpn.frontend.codex_app_server import run_codex_frontend
            return run_codex_frontend(
                root, execution, resume=args.resume is not None)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            parser.error(str(exc))
    try:
        session = (
            MainSession.resume(root, execution)
            if args.resume is not None else
            MainSession(root, execution))
        if args.resume is not None:
            reconciliation = session.reconcile_active_turn()
            if reconciliation.state in {
                    "accepted", "pending_start", "running"}:
                raise RuntimeError(
                    "resumed main-session turn remains active; resolve or "
                    "interrupt it before accepting new input")
            if reconciliation.state not in {
                    "idle", "committed", "interrupted", "paused"}:
                raise RuntimeError(
                    "unsupported resumed main-session state: "
                    f"{reconciliation.state}")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print("RPNH — AI assistant")
    print(f"session: {session.root}")
    print("Type /help for task controls.")
    if args.prompt is not None:
        return 0 if _run_turn(session, args.prompt) else 1
    frontend_state = _BasicFrontendState()
    while True:
        try:
            focus = frontend_state.selected_task_id or "main"
            line = input(f"RPNH[{focus}] › ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nUse /quit to leave; active child tasks are not stopped.")
            continue
        if not line:
            continue
        if line == "/quit":
            return 0
        try:
            if not _task_command(session, line, frontend_state):
                if frontend_state.selected_task_id is None:
                    _run_turn(session, line)
                else:
                    _route_selected_input(session, frontend_state, line)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
