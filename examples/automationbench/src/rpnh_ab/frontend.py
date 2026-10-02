"""Allowlisted prepared-example factory. Launches one durable batch owner.

A reconnect is observation only. An uncertain or stopped owner is never
implicitly relaunched, and no frontend content is passed to MainDecision.
"""
from __future__ import annotations
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from .io import append, load, now, replace_checkpoint
from .run_spec import load_launch


def _snapshot(config):
    work=Path(config['work']); path=work/'batch-status.json'
    value=load(path) if path.is_file() else {'status':'prepared','terminal':False,'quiescent':True,'progress':{'finished':0},'child_tasks':[]}
    if config['executor_host']=='native' and (work/'task-control/manifests').is_dir():
        from cpn.rpnh.task_control import TaskControl
        control=TaskControl(work/'task-control',recover_pending_launches=False)
        value['child_tasks']=[{'task_id':row['task_id'],'kind':row['kind'],'run_dir':row['run_dir']} for row in control.list()]
    value.update(batch_id=config['batch_id'],executor_host=config['executor_host'],
                 condition_digest=config['condition_digest'],task_control_root=str(work/'task-control'))
    if (work/'owner.json').is_file() and not value.get('terminal'):
        from cpn.rpnh.task_control import _process_start_ticks
        owner=load(work/'owner.json')
        if _process_start_ticks(owner['pid'])!=owner['start_ticks']:
            value.update(status='owner_lost',terminal=True,quiescent=False,
                         error='Batch owner exited without terminal evidence; do not replay uncertain attempts')
    return value


class AutomationBenchFactory:
    def handle(self, request, *, session=None):
        config=load_launch(request.config_path,require_acceptance=False,validate_current=False)
        work=Path(config['work'])
        if request.batch_id is not None and request.batch_id!=config['batch_id']:
            raise ValueError('request targets another batch')
        if request.operation=='status':
            return _snapshot(config)
        if request.operation=='stop':
            state=_snapshot(config)
            if not state['terminal']:
                (work/'stop.request').touch(exist_ok=True)
                state.update(status='stop_requested',quiescent=False)
            return state
        with (work/'.launch.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if request.execution_config_path is not None:
                from .native import profile_identity
                if profile_identity(request.execution_config_path) != load(work/'conditions.json')['execution_spec']['configured_model']:
                    raise ValueError('frontend model selection conflicts with frozen execution selection')
            if (work/'owner.json').exists():
                return _snapshot(config)
            # Check every hard gate before creating a process or model request.
            config=load_launch(request.config_path,selected_profile=request.execution_config_path)
            if session is not None and config['executor_host']=='native':
                root=Path(config['native_run_root'] or '').resolve()
                if not root.is_absolute() or not root.is_relative_to(session.child_path_root.resolve()):
                    raise ValueError('prepare native_run_root beneath this MainSession child_path_root')
            append(work/'launch-events.jsonl',{'at':now(),'frontend':request.frontend,
                   'frontend_session_id':request.frontend_session_id,'batch_id':config['batch_id'],'operation':'start'})
            replace_checkpoint(work/'batch-status.json',{'status':'starting','terminal':False,
                       'quiescent':False,'progress':{'finished':0},'child_tasks':[]})
            with (work/'owner.log').open('ab') as log:
                process=subprocess.Popen([sys.executable,'-m','rpnh_ab.owner',str(request.config_path)],
                      cwd=str(work),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            from cpn.rpnh.task_control import _process_start_ticks
            replace_checkpoint(work/'owner.json',{'pid':process.pid,'start_ticks':_process_start_ticks(process.pid),
                              'batch_id':config['batch_id'],'config':str(request.config_path)})
        return _snapshot(config)

example_factory=AutomationBenchFactory()
