from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .io import load, now, replace_checkpoint, write_new
from .scoring import score_attempt, summarize


def durable_batch_state(work: Path) -> dict:
    state = load(work / "batch-status.json") if (work / "batch-status.json").is_file() else {
        "status": "prepared", "terminal": False, "quiescent": True, "progress": {"finished": 0}, "child_tasks": []}
    state["stop_requested"] = (work / "stop.request").exists()
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RPNH AutomationBench frozen host execution and offline scoring")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "prepare", "run", "score"):
        sub = subs.add_parser(name)
        sub.add_argument("--upstream", type=Path, required=True)
        sub.add_argument("--work", type=Path, required=True)
        if name != "score":
            sub.add_argument("--profile", type=Path, required=True,
                             help="existing authorized native RPNH execution-selection JSON; never copied")
        if name in {"doctor", "prepare"}:
            selection = sub.add_mutually_exclusive_group()
            selection.add_argument("--split", choices=("public", "simple"))
            selection.add_argument("--cohort", type=Path,
                                   help="checked-in frozen cohort plan; resolves only its ordered task ids")
        if name == "prepare":
            sub.add_argument("--host",choices=("native","dsh"),default="native")
            sub.add_argument("--dsh-checkout",type=Path)
            sub.add_argument("--acceptance",type=Path)
            sub.add_argument("--launch-output",type=Path)
            sub.add_argument("--native-run-root",type=Path)
            sub.add_argument("--parent-session-root",type=Path)
        if name == "run":
            sub.add_argument("--config",type=Path,required=True,help="prepared launch request with matching acceptance")
        if name == "score":
            sub.add_argument("--revision", action="store_true", help="append an explicit scoring revision; never execute")
    sub = subs.add_parser("summarize")
    sub.add_argument("--work", type=Path, required=True)
    for name in ("status", "stop"):
        sub = subs.add_parser(name)
        sub.add_argument("--work", type=Path, required=True)
    for name in ("acceptance", "bridge-smoke"):
        sub = subs.add_parser(name)
        sub.add_argument("--upstream", type=Path, required=True)
        sub.add_argument("--work", type=Path, required=True)
    sub = subs.add_parser("accept-host")
    sub.add_argument("--upstream", type=Path, required=True)
    sub.add_argument("--work", type=Path, required=True)
    sub.add_argument("--host", choices=("native", "dsh"), default="native")
    sub.add_argument("--dsh-checkout", type=Path)
    sub = subs.add_parser("reproject")
    sub.add_argument("--work", type=Path, required=True)
    sub = subs.add_parser("export")
    sub.add_argument("--work", type=Path, required=True)
    sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.work = args.work.resolve()
    try:
        if args.command in {"status", "stop"}:
            if not args.work.is_dir():
                raise ValueError("batch work directory does not exist")
        else:
            args.work.mkdir(parents=True, exist_ok=True)
        if args.command == "status":
            report = durable_batch_state(args.work)
        elif args.command == "stop":
            if not (args.work / "plan.json").is_file():
                raise ValueError("stop requires an existing prepared batch")
            request = args.work / "stop.request"
            if not request.exists():
                plan = load(args.work / "plan.json")
                write_new(request, {"schema": "rpnh-ab/stop-request/v1", "at": now(),
                                    "manifest_sha256": plan.get("manifest_sha256")})
            report = durable_batch_state(args.work)
        elif args.command == "reproject":
            from .reprojection import reproject_batch
            from .experiment import batch_owner_lock
            with batch_owner_lock(args.work):
                report = reproject_batch(args.work)
        elif args.command == "export":
            from .exporting import export_return
            from .experiment import batch_owner_lock
            with batch_owner_lock(args.work):
                report = export_return(args.work, args.output.resolve())
        elif args.command == "summarize":
            from .experiment import batch_owner_lock
            with batch_owner_lock(args.work):
                report = summarize(load(args.work / "plan.json"), args.work)
                replace_checkpoint(args.work / "summary.live.json", report)
        else:
            from .upstream import Upstream
            upstream = Upstream(args.upstream)
            if args.command in {"acceptance", "bridge-smoke"}:
                from .acceptance import acceptance
                report = acceptance(upstream, args.work)
            elif args.command == "accept-host":
                if args.host == "dsh" and args.dsh_checkout is None:
                    raise ValueError("--host dsh requires --dsh-checkout")
                from .acceptance_manifest import produce
                report = produce(upstream, args.work, host=args.host,
                                 dsh_checkout=args.dsh_checkout)
            elif args.command == "score":
                from .experiment import batch_owner_lock
                with batch_owner_lock(args.work):
                    results = []
                    for task in load(args.work / "plan.json")["tasks"]:
                        attempt = args.work / "attempts" / task["id"] / "a0001"
                        if (attempt / "final_world.json").is_file():
                            results.append(score_attempt(attempt, upstream.score, revision=args.revision, task=task))
                    report = {"at": now(), "model_calls": 0,
                              "score_records_considered": len(results),
                              "summary": summarize(load(args.work / "plan.json"), args.work)}
            else:
                from .experiment import doctor, prepare, run_batch
                profile = args.profile.resolve()
                if args.command == "prepare":
                    from .run_spec import write_launch, dsh_identity
                    from .upstream import git_identity
                    host_identity={"runtime":"RPNH-native"}
                    if args.host=='dsh':
                        if args.dsh_checkout is None:
                            raise ValueError('--host dsh requires --dsh-checkout')
                        host_identity=dsh_identity(args.dsh_checkout)
                    report = prepare(upstream, args.work, profile, args.split, cohort=args.cohort,
                                     executor_host=args.host, host_identity=host_identity)
                    if args.launch_output:
                        if not args.acceptance:raise ValueError('--launch-output requires --acceptance')
                        report={"plan":report,"launch":write_launch(args.launch_output,work=args.work,upstream=args.upstream,
                           profile=profile,host=args.host,acceptance=args.acceptance,native_run_root=args.native_run_root,
                           dsh_checkout=args.dsh_checkout,parent_session_root=args.parent_session_root)}
                elif args.command == "doctor":
                    report = doctor(upstream, profile, args.work,
                                    split=args.split, cohort=args.cohort)
                    replace_checkpoint(args.work / "doctor.json", report)
                else:
                    from .run_spec import load_launch
                    config=load_launch(args.config)
                    if Path(config['work'])!=args.work or Path(config['upstream'])!=args.upstream.resolve() or Path(config['profile'])!=profile:
                        raise ValueError('CLI paths differ from frozen launch request')
                    report = run_batch(upstream, args.work, profile, launch=config)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print("Stopped by operator; retained attempts are not automatically repeated.", file=sys.stderr)
        return 130
    except Exception as exc:
        error = {"at": now(), "command": args.command, "error_type": type(exc).__name__, "error": str(exc)}
        from .experiment import BatchOwnerActive
        if not isinstance(exc, BatchOwnerActive):
            replace_checkpoint(args.work / f"{args.command}.error.json", error)
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
