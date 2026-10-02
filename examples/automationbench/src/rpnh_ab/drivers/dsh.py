from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from ..io import load, write_new
from ..plugin import bindings, configuration
from ..upstream import split_prompt

class DshDriver:
    host='dsh'
    def run(self, *, run_dir, profile, broker, messages, schemas, control_root,
            dsh_checkout=None, stop_path=None, **unused):
        if not dsh_checkout:
            raise ValueError('dsh executor requires the pinned DSH checkout')
        from cpn.dsh.launcher import _public_profile
        run_dir.mkdir(parents=True,exist_ok=False)
        plugin=run_dir/'plugins.json'; write_new(plugin,configuration(broker.endpoint,broker.run_id))
        system,prompt=split_prompt(messages)
        task=(system+'\n\n' if system else '')+prompt
        task+='\n\nComplete the business task using the visible tools. Finish with a factual assistant report. Tool raw_result is the exact upstream response. Do not request interactive user input.'
        session='ab-'+broker.run_id
        packet={'schema_version':'rpnh/dsh_host_task/v1','root':str(run_dir),
                'session_id':session,'task':task,'execution_path':str(profile),
                'execution_profile':_public_profile(profile),'plugin_config_path':str(plugin),
                'managed_binding':bindings(schemas)['executor'],'max_attempts':None,
                'result_path':str(run_dir/'host-result.json'),'stop_path':str(stop_path or run_dir/'stop.request')}
        request=run_dir/'host-task.json'; write_new(request,packet)
        with (run_dir/'host.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','cpn.dsh.launcher',str(dsh_checkout),
                                      '--host-task',str(request)],stdout=log,stderr=subprocess.STDOUT)
            try:
                process.wait()
            except BaseException:
                Path(packet['stop_path']).touch()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate(); process.wait(timeout=5)
                raise
        result=load(run_dir/'host-result.json') if (run_dir/'host-result.json').is_file() else {}
        return lifecycle_from_result(result,run_dir=run_dir,session=session,return_code=process.returncode)

    def project(self,run_dir,output):
        result=load(run_dir/'host-result.json')
        return {'source':'dsh_registered_host','registry_path':result.get('registry_path'),
                'raw_host_result':str(run_dir/'host-result.json'),
                'status':'raw_retained','observation_error':result.get('observation_error'),
                'proves_model_consumption_of_each_tool_result':False}


def lifecycle_from_result(result, *, run_dir, session, return_code):
    """Project real outcome mappings; Registry path alone is not admission."""
    outcome=result.get('outcome')
    terminal=isinstance(outcome,dict) and outcome.get('status')=='terminal'
    stopped=bool(result.get('stop_requested')) and isinstance(outcome,dict) and outcome.get('status') in {'stopped_by_owner','interrupted','cancelled'}
    quiet=bool(result.get('host_quiescent')) and bool(result.get('process_quiescent'))
    admitted=(terminal or stopped) and result.get('admitted') is True and not result.get('start_error')
    return {'executor_host':'dsh','task_id':session,'process_exit_confirmed':True,
        'host_quiescent':quiet and admitted,
        'manual_stop':stopped, 'admitted':admitted,
        'terminal':result if terminal else None,
        'execution_status':'host_terminal' if terminal else 'manual_stop' if stopped else 'configuration_blocked',
        'status':result.get('status'), 'registry_path':result.get('registry_path'),
        'raw_host_result':str(run_dir/'host-result.json'),'return_code':return_code}
