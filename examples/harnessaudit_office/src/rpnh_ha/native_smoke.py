"""Concrete local RPNH/OfficeBank integration, entirely WITHOUT a model.

This launches independent native-plugin test runs, not a multi-agent task.
The scripted order item is deliberately a wiring marker, not a task answer.
"""
from __future__ import annotations
from pathlib import Path
import uuid
from .backend import BackendService,FaultSpec
from .constants import OFFICE_EFFECTS
from .evidence import read_events
from .jsonio import write_new
from .native_plugin import configuration,manager_factory,admin_factory,policy_factory
from .state_diff import diagnose
from .upstream import load_case,office_bank_factory,official_dispatch


def _zero_model_calls(result: dict) -> bool:
    """Accept current RPNH accounting lanes only when every lane is zero."""
    counts = result.get('actual_model_call_counts')
    return (isinstance(counts,list) and bool(counts)
            and all(type(value) is int and value == 0 for value in counts))


def run_native_smoke(*,audit_root:Path,output:Path,fault:bool=False) -> dict:
    from cpn.plugins.catalog import load_catalog
    from cpn.plugins.runtime import run_plugin
    task,catalog,public=load_case(audit_root)
    if output.exists():raise FileExistsError('native smoke needs a fresh output directory')
    output.mkdir(parents=True)
    run_id='native-smoke-'+uuid.uuid4().hex[:12]
    spec=FaultSpec('response_lost_after_effect','order_service_catalog_item') if fault else FaultSpec()
    records=[]
    checks=[]
    try:
        with BackendService(run_id=run_id,root=output/'backend',tools=OFFICE_EFFECTS,
             bank_factory=office_bank_factory(audit_root),dispatch=official_dispatch,fault=spec) as service:
            before=service.snapshot(output/'bank.before.sqlite')
            cfg=configuration(service.endpoints,run_id)
            bound=load_catalog(cfg,factories={'ha_manager':manager_factory,'ha_admin':admin_factory,'ha_policy':policy_factory})
            steps=[('ha_admin/read_user_directory',{'user_lookup':'USR-ETHAN-BROOKS'}),
                   ('ha_admin/order_service_catalog_item',{'item_name':'RPNH_WIRING_ONLY_NOT_TASK_SOLUTION',
                    'requested_for':'USR-ETHAN-BROOKS','quantity':'1','configuration':'synthetic integration smoke only'})]
            for i,(selector,args) in enumerate(steps):
                try:
                    result=run_plugin(bound,selector,args,run_dir=output/f'native-run-{i}')
                    records.append({'selector':selector,'result':result})
                    if fault and i==1:
                        checks.append({'name':'lost-response-not-success','passed':result['terminal_evidence_ref'] is None})
                    else:
                        checks.append({'name':f'{selector}:registered-output','passed':
                            result['terminal_evidence_ref'] is not None and isinstance(result.get('output'),dict)
                            and isinstance(result['output'].get('raw_result'),str)})
                    checks.append({'name':f'{selector}:zero-model-calls',
                                   'passed':_zero_model_calls(result)})
                except Exception as exc:
                    records.append({'selector':selector,'error_type':type(exc).__name__,'error':str(exc)})
                    checks.append({'name':f'{selector}:native-completion','passed':False})
                    break
            after=service.snapshot(output/'bank.after.sqlite')
            diagnostic=diagnose(before,after,target_user='USR-ETHAN-BROOKS')
            checks.append({'name':'one-real-sandbox-order','passed':diagnostic['target_order_delta']==1})
            write_new(output/'state_diagnostic.json',diagnostic)
    except Exception as exc:
        checks.append({'name':'integration-setup','passed':False,'error':type(exc).__name__+': '+str(exc)})
    path=output/'backend'/'witness.jsonl'
    events=read_events(path) if path.exists() else []
    if fault:
        checks.append({'name':'fault-actually-triggered','passed':any(e['kind']=='fault_response_lost' for e in events)})
    report={'schema_version':'rpnh-ha/native-smoke/v2','mode':'scripted_native_integration_not_benchmark',
            'passed':bool(checks) and all(c['passed'] for c in checks),'checks':checks,'native_results':records,
            'official_scores':None,'actual_model_calls':(0 if records and all(
                _zero_model_calls(r.get('result',{})) for r in records) else None),
            'three_role_driver_tested':False,
            'fault':spec.mode,'note':'This is not an off-t2 agent score or an automatic-recovery claim.'}
    write_new(output/'native_smoke_report.json',report)
    return report
