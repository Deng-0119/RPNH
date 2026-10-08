"""Independent final-source HOST metadata, read-scope and no-replay checks."""
from pathlib import Path
import copy
import hashlib
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'source'))
from examples.tool_pipeline import tools
from examples.tool_pipeline.host import registration
from examples.tool_pipeline.run import create_owner, make_harness, inspect_registry
from examples.tool_pipeline.tests.pipe_transport import PipeOwnerEventLoop
from cpn.components.execution_services import ExecutionServices
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.errors import UnauthorizedResourceDelivery

calls={'usage':0}
entered={'usage':threading.Event(),'tariff':threading.Event()}
release=threading.Event()
originals={x:tools.TOOLS['read_'+x] for x in entered}
async def false_parent_usage(*,inputs,rules):
    calls['usage']+=1
    entered['usage'].set()
    if not release.wait(180):raise TimeoutError('final review gate')
    result=await originals['usage'](inputs=inputs,rules=rules)
    result.products['raw']['parents'][0]['resource_version_id']='resource_version:'+'f'*32
    return result
async def held_tariff(*,inputs,rules):
    entered['tariff'].set()
    if not release.wait(180):raise TimeoutError('final review gate')
    return await originals['tariff'](inputs=inputs,rules=rules)
path=ROOT/'final-boundary-run'
if path.exists():shutil.rmtree(path)
tools.TOOLS.update(read_usage=false_parent_usage,read_tariff=held_tariff)
try:owner=create_owner(path,selected_registration=registration(test_identity='review-final-boundaries/v1'))
finally:tools.TOOLS.update({'read_'+x:fn for x,fn in originals.items()})
loop=PipeOwnerEventLoop(owner,path/'owner.sock')
result={'transport':'explicit_test_transport','source_sha256':{name:hashlib.sha256((ROOT.parent/'source/examples/tool_pipeline'/name).read_bytes()).hexdigest() for name in ('host.py','tools.py','contracts.py','module.json')}}
try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        runner=make_harness(owner,loop,pool)
        observation_error=None
        try:
            deadline=time.monotonic()+180
            while not all(x.is_set() for x in entered.values()):
                assert time.monotonic()<deadline
                runner.schedule_ready();loop.dispatch_ready(timeout=.01)
            executions={x.operation.firing.transition_id:x for x in runner._pending.values()}
            usage=executions['read_usage.run'];tariff=executions['read_tariff.run']
            kernel,_=owner.operation_repository()
            foreign=usage.operation.inputs[0].resource_ref
            try:verify_resource(owner._core,kernel,tariff.operation.canonical,foreign)
            except UnauthorizedResourceDelivery as exc:
                result['same_registry_unauthorized_read']={'error_type':type(exc).__name__,'error':str(exc)}
            else:raise AssertionError('read_tariff could read the unclaimed usage entry')
        except BaseException as exc:
            observation_error=exc
        finally:release.set()
        try:runner.exact_execute()
        except ValueError as exc:
            assert 'provenance' in str(exc)
            result['forged_parent_ref_rejected']={'error_type':type(exc).__name__,'error':str(exc)}
        else:raise AssertionError('HOST accepted forged parent metadata')
        if observation_error is not None:raise observation_error
        before=calls['usage']
        resumed=make_harness(owner,loop,pool).exact_execute()
        assert resumed.stop_reason=='reconciliation_required',resumed.stop_reason
        assert calls['usage']==before==1
        result['fresh_harness_no_unresolved_replay']={'stop_reason':resumed.stop_reason,'tool_calls':calls['usage']}
    evidence=inspect_registry(owner._core)
    assert not evidence['terminal_evidence'] and evidence['final'] is None
    assert not any(r['value']['stage']=='usage_raw' for r in evidence['resources'])
    assert evidence['actual_model_call_counts']==[0,0]
    result.update(actual_model_call_counts=[0,0],terminal_count=0,
        firings=[{'transition':f['firing']['transition_id'],'state':f['state']} for f in evidence['firings']])
finally:loop.close()
(ROOT/'final-boundary-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
