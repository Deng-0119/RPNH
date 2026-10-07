"""Local bridge commands; live-execute and score-full may call models."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys
from .jsonio import dumps,write_new,read
from .preflight import inspect_sources


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight',help='source/API/data checks only; no provider calls')
    p.add_argument('--rpnh-root',type=Path,required=True);p.add_argument('--audit-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('plan-condition',help='freeze an opted-in public comparison plan; no Registry/backend/provider')
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--public-input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('prepare',help='export only model-visible task fields')
    p.add_argument('--task-id',default='off-t2')
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-smoke',help='run real native SDK/Registry+OfficeBank, no model, no task score')
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lost-response',action='store_true')
    p=sub.add_parser('scripted-acceptance',help='run the legacy off-t2 driver with a local scripted model; not a score')
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lost-response',action='store_true')
    p=sub.add_parser('score-rules',help='partial regrade of an actual fully-observed local driver run')
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('score-full',help='run pinned HarnessAudit SAR/AVS/TCR judges on a saved native run')
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True);p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('live-readiness',help='report executor and scoring gates separately; no model call')
    p.add_argument('--config',type=Path,required=True)
    p=sub.add_parser('live-execute',help='run one explicitly authorized native task; calls a model')
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--audit-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('recover-live',help='recover post-run evidence from an existing native run; no model call')
    p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('reproject-live',help='reproject a completed native run into fresh evidence; no model call')
    p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='plan-condition':
            from .comparison_condition import plan_condition
            from .readiness import load_experiment
            result=plan_condition(read(args.public_input),load_experiment(args.config))
            write_new(args.output,result);print(dumps(result));return 0
        if args.command=='preflight':
            result=inspect_sources(
                args.rpnh_root.resolve(),args.audit_root.resolve())
            write_new(args.output,result); print(dumps(result))
            return 0 if result['source_preflight_passed'] else 2
        if args.command=='prepare':
            from .upstream import load_case
            _,_,public=load_case(args.audit_root.resolve(), args.task_id)
            write_new(args.output,public.as_dict()); print(str(args.output)); return 0
        if args.command=='native-smoke':
            from .native_smoke import run_native_smoke
            result=run_native_smoke(audit_root=args.audit_root.resolve(),output=args.output.resolve(),fault=args.lost_response)
            print(dumps(result)); return 0 if result['passed'] else 2
        if args.command=='scripted-acceptance':
            from .scripted_acceptance import run
            result=run(audit_root=args.audit_root.resolve(),output=args.output.resolve(),lost_response=args.lost_response)
            print(dumps(result)); return 0 if result['passed'] else 2
        if args.command=='score-rules':
            from .scoring import score_rule_only
            result=asyncio.run(score_rule_only(audit_root=args.audit_root.resolve(),run_root=args.run_root.resolve(),output=args.output))
            print(dumps(result));return 0
        if args.command=='score-full':
            from .scoring import score_full
            result=asyncio.run(score_full(audit_root=args.audit_root,run_root=args.run_root,
                config_path=args.config,output=args.output))
            print(dumps(result));return 0
        if args.command=='live-readiness':
            from .readiness import inspect_live_readiness
            result=inspect_live_readiness(args.config)
            print(dumps(result));return 0 if result['execution_ready'] else 2
        if args.command=='live-execute':
            from .live_execution import run
            result=run(config_path=args.config,audit_root=args.audit_root,output=args.output)
            print(dumps(result));return 0 if result['execution_completed'] else 2
        if args.command=='recover-live':
            from .recovery import recover_live_evidence
            result=recover_live_evidence(run_root=args.run_root,output=args.output)
            print(dumps(result));return 0 if result['recovery_completed'] else 2
        if args.command=='reproject-live':
            from .recovery import reproject_live_evidence
            result=reproject_live_evidence(run_root=args.run_root,output=args.output)
            print(dumps(result));return 0 if result['reprojection_completed'] else 2
    except (OSError,ValueError,RuntimeError,ImportError,KeyError,TypeError) as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr); return 2
    return 2

if __name__=='__main__':raise SystemExit(main())
