"""Durable whole-batch process; frontends only observe its persisted status."""
from pathlib import Path
import fcntl
import sys
from .io import load, now, replace_checkpoint
from .run_spec import load_launch


def main(argv=None):
    config=load_launch(Path((argv or sys.argv[1:])[0]))
    work=Path(config['work']); children=[]; finished=0
    def status(**fields):
        replace_checkpoint(work/'batch-status.json',{'at':now(),'progress':{'finished':finished},
                 'child_tasks':children,**fields})
    def progress(case,attempt):
        nonlocal finished
        finished+=1
        life=load(attempt/'lifecycle.json')
        if config['executor_host']=='native' and life.get('task_id'):
            children.append({'task_id':life['task_id'],'kind':life.get('status',{}).get('kind','workflow'),'run_dir':life['registry_path']})
        status(status='running',terminal=False,quiescent=False)
    with (work/'.owner.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            from .upstream import Upstream
            from .experiment import run_batch
            summary=run_batch(Upstream(Path(config['upstream'])),work,Path(config['profile']),launch=config,progress=progress)
            lives=[load(p) for p in work.glob('attempts/*/a0001/lifecycle.json')]
            blocked=any(x.get('execution_status')=='configuration_blocked' for x in lives)
            quiet=all(x.get('world_owner_quiescent') for x in lives)
            status(status='configuration_blocked' if blocked else 'stopped' if (work/'stop.request').exists() else 'completed',
                   terminal=True,quiescent=quiet,summary=summary)
            return 2 if blocked else 0
        except Exception as exc:
            # Do not claim a frozen world or successful cancellation after an unknown owner failure.
            status(status='configuration_blocked',terminal=True,quiescent=False,
                   error=type(exc).__name__+': '+str(exc))
            return 2

if __name__=='__main__':
    raise SystemExit(main())
