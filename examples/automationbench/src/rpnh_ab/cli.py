from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .io import load, now, replace_checkpoint, write_new
from .scoring import score_attempt, summarize


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
        if name == "prepare":
            sub.add_argument("--split", choices=("public", "simple"), default="public")
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
    sub = subs.add_parser("acceptance")
    sub.add_argument("--upstream", type=Path, required=True)
    sub.add_argument("--work", type=Path, required=True)
    sub = subs.add_parser("reproject")
    sub.add_argument("--work", type=Path, required=True)
    sub = subs.add_parser("export")
    sub.add_argument("--work", type=Path, required=True)
    sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "reproject":
            from .reprojection import reproject_batch
            report = reproject_batch(args.work)
        elif args.command == "export":
            from .exporting import export_return
            report = export_return(args.work, args.output.resolve())
        elif args.command == "summarize":
            report = summarize(load(args.work / "plan.json"), args.work)
            replace_checkpoint(args.work / "summary.live.json", report)
        else:
            from .upstream import Upstream
            upstream = Upstream(args.upstream)
            if args.command == "acceptance":
                from .acceptance import acceptance
                report = acceptance(upstream, args.work)
            elif args.command == "score":
                results = []
                for task in load(args.work / "plan.json")["tasks"]:
                    attempt = args.work / "attempts" / task["id"] / "a0001"
                    if (attempt / "final_world.json").is_file():
                        results.append(score_attempt(attempt, upstream.score, revision=args.revision))
                report = {"at": now(), "model_calls": 0, "score_records_considered": len(results),
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
                    report = prepare(upstream, args.work, profile, args.split,executor_host=args.host,host_identity=host_identity)
                    if args.launch_output:
                        if not args.acceptance:raise ValueError('--launch-output requires --acceptance')
                        report={"plan":report,"launch":write_launch(args.launch_output,work=args.work,upstream=args.upstream,
                           profile=profile,host=args.host,acceptance=args.acceptance,native_run_root=args.native_run_root,
                           dsh_checkout=args.dsh_checkout,parent_session_root=args.parent_session_root)}
                elif args.command == "doctor":
                    report = doctor(upstream, profile, args.work)
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
        replace_checkpoint(args.work / f"{args.command}.error.json", error)
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
