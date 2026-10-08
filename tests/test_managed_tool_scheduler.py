from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
import time

import pytest

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.api import PluginError
from cpn.plugins.managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationConflict,
    ManagedPluginInvocationFailed, ManagedPluginInvocationReconciliationRequired,
    ManagedPluginInvocationService, ManagedPluginToolCatalog,
    ManagedWorkerCompletion, execute_prepared_invocation,
)
from cpn.plugins.managed_scheduler import (
    ManagedConflictDomain, ManagedRunCapacity, ManagedSchedulerPolicy, ManagedToolCall, ManagedToolScheduler,
)
from cpn.plugins.worker import WorkerFailure
from cpn.rpnh.control_server import OwnerEventLoop, RegistryGateway
from test_managed_plugin_tools import (
    INTEGER_OUTPUT, OBJECT_INPUT, _managed_receipts, _plugin_catalog,
    _prepared_invocation,
)


def _calls(n=3):
    return tuple(ManagedToolCall(i, 'double_value', f'call-{i}', {'value': i}) for i in range(n))


def _pump(loop, predicate, timeout=15):
    deadline=time.monotonic()+timeout
    while not predicate():
        assert time.monotonic() < deadline, 'owner/worker barrier did not progress'
        loop.dispatch_ready(timeout=0.01)


@contextmanager
def _host(tmp_path, monkeypatch, *, effect='pure', selected=None, managed=None, policy=None):
    selected=selected or _plugin_catalog(effect=effect)
    managed=managed or ManagedPluginToolCatalog(selected, {'double_value':'synthetic/double'}, admitted_effects=(effect,))
    p=_prepared_invocation(tmp_path, selected=selected, managed=managed)
    service=p['service']; threads=[]; writes=[]
    begin=service.core.begin
    def counted_begin(*args, **kwargs):
        writes.append(get_ident())
        return begin(*args, **kwargs)
    monkeypatch.setattr(service.core, 'begin', counted_begin)
    def prepare(call):
        threads.append(('prepare',get_ident(),call.ordinal))
        return service.prepare(call.name, execution=p['execution'],call_id=call.call_id,
                               arguments=call.arguments, registration_key=call.registration_key, scheduling_policy=policy)
    def finish(prepared, completion):
        threads.append(('finish',get_ident(),prepared.receipt_key))
        return service.finish(prepared, completion)
    loop=OwnerEventLoop(p['owner'],tmp_path/'owner.sock')
    gateway=RegistryGateway(loop, {'prepare':prepare,'finish':finish,'ping':lambda:get_ident()})
    try:
        yield p,loop,gateway,threads,writes
    finally:
        loop.close()


def _run(loop,gateway,calls,*,limit=2,capacity=None,cancelled=lambda:False,policy=None):
    own=capacity is None
    capacity=capacity or ManagedRunCapacity(limit)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ManagedToolScheduler(policy or ManagedSchedulerPolicy(max_in_flight=limit),capacity).run,
                calls,operation_key='operation-1',prepare=gateway.prepare,finish=gateway.finish,cancelled=cancelled)
            _pump(loop,future.done)
            return future.result()
    finally:
        if own: capacity.close()


def test_barrier_overlap_max_inflight_and_single_owner(tmp_path,monkeypatch):
    barrier=Barrier(2); lock=Lock(); active=0; peak=0; worker_threads=[]
    def worker(_handler,packet,**kwargs):
        nonlocal active,peak
        with lock:
            active+=1; peak=max(peak,active); worker_threads.append(get_ident())
        barrier.wait(timeout=10)
        with lock: active-=1
        return packet['arguments']['value']*2
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,threads,writes):
        batch=_run(loop,gateway,_calls(4))
        assert [x.result['output'] for x in batch.outcomes]==[0,2,4,6]
        assert peak==2 and not batch.reconciliation_required
        assert {x[1] for x in threads}=={get_ident()}==set(writes)
        assert get_ident() not in worker_threads
        assert len(_managed_receipts(tmp_path/'run'))==8


def test_observer_nonblocking_exact_future_and_no_early_claim(tmp_path,monkeypatch):
    entered=Event(); release=Event()
    def worker(*args,**kwargs):
        entered.set(); assert release.wait(10); return 8
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,threads,writes), ManagedRunCapacity(1) as cap:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ManagedToolScheduler(ManagedSchedulerPolicy(max_in_flight=1),cap).run,
                _calls(2),operation_key='operation-1',prepare=gateway.prepare,finish=gateway.finish)
            try:
                _pump(loop,entered.is_set)
                observer=p['service'].prepare('double_value',execution=p['execution'],call_id='call-0',arguments={'value':0})
                again=p['service'].prepare('double_value',execution=p['execution'],call_id='call-0',arguments={'value':0})
                assert isinstance(observer,ManagedInvocationObservation)
                assert observer.future is again.future and not observer.future.done()
                assert not observer.future.cancel()
                ping=gateway.submit('ping'); _pump(loop,ping.done); assert ping.result()==get_ident()
                receipts=_managed_receipts(tmp_path/'run')
                assert [(x['call_id'],x['state']) for x in receipts]==[('call-0','started')]
                with pytest.raises(ManagedPluginInvocationConflict):
                    p['service'].prepare('double_value',execution=p['execution'],call_id='call-0',arguments={'value':9})
            finally: release.set()
            _pump(loop,future.done)
            batch=future.result()
            assert observer.future.result()==batch.outcomes[0].result
            assert len(batch.outcomes)==2


def test_partial_invalid_known_failure_and_success_all_collected(tmp_path,monkeypatch):
    def worker(_handler,packet,**kwargs):
        if packet['arguments']['value']==1:
            raise WorkerFailure('handler_failed',may_have_executed=False)
        return 4
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        calls=(replace(_calls()[0],arguments={'value':'invalid'}),*_calls()[1:])
        batch=_run(loop,gateway,calls)
        assert isinstance(batch.outcomes[0].error,PluginError)
        assert isinstance(batch.outcomes[1].error,ManagedPluginInvocationFailed)
        assert batch.outcomes[1].error.evidence['outcome']=='failed'
        assert batch.outcomes[2].result['output']==4
        assert not batch.reconciliation_required
        assert {x['call_id'] for x in _managed_receipts(tmp_path/'run')}=={'call-1','call-2'}


def test_unknown_stops_pending_collects_inflight_and_blocks_operation(tmp_path,monkeypatch):
    barrier=Barrier(2); second=Event()
    def worker(_handler,packet,**kwargs):
        value=packet['arguments']['value']
        assert value < 2
        barrier.wait(10)
        if value==0: raise RuntimeError('lost worker transport')
        assert second.wait(10)
        return 2
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_), ManagedRunCapacity(2) as cap:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ManagedToolScheduler(ManagedSchedulerPolicy(),cap).run,_calls(4),
                operation_key='operation-1',prepare=gateway.prepare,finish=gateway.finish)
            try:
                _pump(loop,lambda:cap._is_blocked('operation-1'))
                assert not future.done()
            finally: second.set()
            _pump(loop,future.done); batch=future.result()
        assert batch.reconciliation_required
        assert isinstance(batch.outcomes[0].error,ManagedPluginInvocationReconciliationRequired)
        assert batch.outcomes[1].result['output']==2
        assert all(x.not_started for x in batch.outcomes[2:])
        assert {x['call_id'] for x in _managed_receipts(tmp_path/'run')}=={'call-0','call-1'}
        blocked=_run(loop,gateway,(replace(_calls()[0],call_id='new-call'),),capacity=cap)
        assert blocked.outcomes[0].not_started
        rebuilt=ManagedPluginInvocationService(p['owner'],p['kernel'],p['repository'],p['managed'])
        with pytest.raises(ManagedPluginInvocationReconciliationRequired):
            rebuilt.prepare('double_value',execution=p['execution'],call_id='new-call',arguments={'value':0})


def test_cancel_preserves_returned_and_stops_unclaimed(tmp_path,monkeypatch):
    stop=Event()
    def worker(*args,**kwargs):
        stop.set(); return 8
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_calls(3),limit=1,cancelled=stop.is_set)
        assert batch.cancelled
        assert batch.outcomes[0].result['outcome']=='returned'
        assert all(x.not_started for x in batch.outcomes[1:])
        assert len(_managed_receipts(tmp_path/'run'))==2


def test_claim_without_terminal_recovery_never_dispatches(tmp_path,monkeypatch):
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        s=p['service']; call=_calls(1)[0]
        prepared=gateway._methods['prepare'](call)
        # Loss of the process-local coordinator models restart; Registry is real.
        with s._active_lock: s._active_calls.pop((id(s.core),prepared.receipt_key))
        monkeypatch.setattr('cpn.plugins.worker.execute_worker',lambda *a,**k:pytest.fail('recovery dispatched'))
        batch=_run(loop,gateway,(call,))
        assert batch.reconciliation_required
        assert batch.outcomes[0].error.evidence['error']['code']=='managed_terminal_observation_missing'
        assert [x['state'] for x in _managed_receipts(tmp_path/'run')]==['started','outcome_unknown']


def test_cross_turn_provider_id_keeps_receipt_replay_and_conflict(tmp_path,monkeypatch):
    dispatched=[]
    def worker(*args,**kwargs): dispatched.append(1); return 8
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        first=_run(loop,gateway,_calls(1))
        second=_run(loop,gateway,_calls(1))  # a new turn scheduler, same operation/provider ID
        assert first.outcomes[0].result==second.outcomes[0].result and dispatched==[1]
        changed=_run(loop,gateway,(replace(_calls(1)[0],arguments={'value':9}),))
        assert isinstance(changed.outcomes[0].error,ManagedPluginInvocationConflict)
        assert dispatched==[1]


def test_finish_revalidates_authority_and_exact_admission(tmp_path,monkeypatch):
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        prepared=gateway._methods['prepare'](_calls(1)[0])
        with pytest.raises(ManagedPluginInvocationConflict):
            p['service'].finish(replace(prepared,receipt_material=b'{}'),ManagedWorkerCompletion(output=8))
        original=p['service']._authorize; checked=[]
        def authorize(*args): checked.append(1); return original(*args)
        monkeypatch.setattr(p['service'],'_authorize',authorize)
        assert p['service'].finish(prepared,ManagedWorkerCompletion(output=8))['output']==8
        assert checked==[1]


def test_duplicate_batch_identity_rejected_before_prepare():
    with ManagedRunCapacity(2) as cap:
        with pytest.raises(PluginError,match='unique'):
            ManagedToolScheduler(ManagedSchedulerPolicy(),cap).run((_calls(1)[0],)*2,
                operation_key='op',prepare=lambda _:pytest.fail('admitted'),finish=lambda *_:None)


def test_run_capacity_shared_across_firings(tmp_path,monkeypatch):
    entered=Event(); release=Event(); active=0; peak=0; lock=Lock()
    def worker(*args,**kwargs):
        nonlocal active,peak
        with lock: active+=1; peak=max(active,peak)
        entered.set(); assert release.wait(10)
        with lock: active-=1
        return 8
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_),ManagedRunCapacity(1) as cap:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sched=ManagedToolScheduler(ManagedSchedulerPolicy(max_in_flight=2),cap)
            futures=[pool.submit(sched.run,(replace(_calls(1)[0],call_id=f'firing-{i}'),),operation_key=f'op-{i}',prepare=gateway.prepare,finish=gateway.finish) for i in range(2)]
            try:
                _pump(loop,entered.is_set)
                assert len(_managed_receipts(tmp_path/'run'))==1
            finally: release.set()
            _pump(loop,lambda:all(f.done() for f in futures))
            assert all(f.result().outcomes[0].result['output']==8 for f in futures)
            assert peak==1


def _process_barrier_handler(context,arguments):
    gate=Path(context.config['gate'])
    (gate/context.call_id).write_text(str(os.getpid()))
    end=time.monotonic()+10
    while len(tuple(gate.iterdir()))<2:
        context.check_cancelled()
        if time.monotonic()>end: raise RuntimeError('process barrier timed out')
        time.sleep(0.01)
    return arguments['value']*2


def test_real_spawned_processes_overlap_at_barrier(tmp_path,monkeypatch):
    assert os.getuid()==1000, 'run under normal WSL deng123'
    gate=tmp_path/'barrier'; gate.mkdir()
    selected=PluginCatalog((BoundPlugin(PluginDefinition('synthetic','1',(
        PluginOperation('double','Double',OBJECT_INPUT,INTEGER_OUTPUT,_process_barrier_handler),), config_schema={'type':'object','properties':{'gate':{'type':'string'}},'required':['gate'],'additionalProperties':False}),{'gate':str(gate)}),))
    with _host(tmp_path,monkeypatch,selected=selected) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_calls(2))
        assert [x.result['output'] for x in batch.outcomes]==[0,2]
        assert len({x.read_text() for x in gate.iterdir()})==2
        assert len(_managed_receipts(tmp_path/'run'))==4


def _domain_catalog(monkeypatch):
    from cpn.rpnh.module import ModuleDeclaration
    import test_managed_plugin_tools as helpers
    operations=tuple(PluginOperation(name,name,OBJECT_INPUT,INTEGER_OUTPUT,
        helpers.managed_double,effect=effect) for name,effect in (
            ('double','external_write'),('read','external_read'),
            ('other','external_write'),('unknown','external_write')))
    selected=PluginCatalog((BoundPlugin(PluginDefinition('synthetic','1',operations),{}),))
    managed=ManagedPluginToolCatalog(selected,{
        'double_value':'synthetic/double','read_value':'synthetic/read',
        'other_value':'synthetic/other','unknown_value':'synthetic/unknown'},
        admitted_effects=('external_read','external_write'))
    original=helpers._caller_module
    def all_tools(key):
        doc=original(key).to_dict()
        doc['components'][0]['operations'][0]['tools']=[t.registration_key for t in managed.tools]
        return ModuleDeclaration.from_dict(doc)
    monkeypatch.setattr(helpers,'_caller_module',all_tools)
    entries=(
        ManagedConflictDomain(managed.declaration('double_value').registration_key,'external_write',writes=('object-A',)),
        ManagedConflictDomain(managed.declaration('read_value').registration_key,'external_read',reads=('object-A',)),
        ManagedConflictDomain(managed.declaration('other_value').registration_key,'external_write',writes=('object-B',)),
    )
    policy=ManagedSchedulerPolicy('managed_conflict_domains/v1',2,entries)
    return selected,managed,policy


def _domain_calls(managed,names):
    return tuple(ManagedToolCall(i,name,f'call-{i}',{'value':i},managed.declaration(name).registration_key)
                 for i,name in enumerate(names))


@pytest.mark.parametrize('same_domain',['read_value','double_value'])
def test_conflict_write_read_and_write_write_serialize_independent_proceeds(tmp_path,monkeypatch,same_domain):
    selected,managed,policy=_domain_catalog(monkeypatch)
    overlap=Barrier(2); first_done=Event(); starts=[]
    def worker(_handler,packet,**kwargs):
        i=packet['arguments']['value']; starts.append(i)
        if i in (0,2):
            overlap.wait(10)
            if i==0: first_done.set()
        else: assert first_done.is_set()
        return i
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch,selected=selected,managed=managed,policy=policy) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_domain_calls(managed,['double_value',same_domain,'other_value']),policy=policy)
        assert all(x.result is not None for x in batch.outcomes), batch
        assert [x.result['output'] for x in batch.outcomes]==[0,1,2]
        assert starts.index(2)<starts.index(1)
        receipts=_managed_receipts(tmp_path/'run')
        states=[(x['call_id'],x['state']) for x in receipts]
        assert states.index(('call-0','returned'))<states.index(('call-1','started'))


def test_same_domain_reads_overlap(tmp_path,monkeypatch):
    selected,managed,policy=_domain_catalog(monkeypatch)
    barrier=Barrier(2)
    def worker(_handler,packet,**kwargs):
        barrier.wait(10); return packet['arguments']['value']
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch,selected=selected,managed=managed,policy=policy) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_domain_calls(managed,['read_value','read_value']),policy=policy)
        assert [x.result['output'] for x in batch.outcomes]==[0,1]


def test_undeclared_effect_domain_is_exclusive_pending_barrier(tmp_path,monkeypatch):
    selected,managed,policy=_domain_catalog(monkeypatch)
    entered=[Event(),Event()]; release=[Event(),Event()]; started=[]
    def worker(_handler,packet,**kwargs):
        i=packet['arguments']['value']; started.append(i)
        if i<2: entered[i].set(); assert release[i].wait(10)
        return i
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch,selected=selected,managed=managed,policy=policy) as (p,loop,gateway,*_),ManagedRunCapacity(2) as cap:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ManagedToolScheduler(policy,cap).run,
                _domain_calls(managed,['double_value','unknown_value','other_value']),
                operation_key='operation-1',prepare=gateway.prepare,finish=gateway.finish)
            try:
                _pump(loop,entered[0].is_set)
                assert started==[0]
                release[0].set(); _pump(loop,entered[1].is_set)
                assert started==[0,1]
                assert len([x for x in _managed_receipts(tmp_path/'run') if x['state']=='started'])==2
            finally:
                for event in release: event.set()
            _pump(loop,future.done)
            assert [x.result['output'] for x in future.result().outcomes]==[0,1,2]


def test_external_write_unknown_retains_receipt_and_blocks_pending(tmp_path,monkeypatch):
    selected,managed,policy=_domain_catalog(monkeypatch)
    def worker(*args,**kwargs): raise WorkerFailure('deadline_exceeded')
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch,selected=selected,managed=managed,policy=policy) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_domain_calls(managed,['double_value','double_value']),policy=policy)
        assert batch.reconciliation_required and batch.outcomes[1].not_started
        assert batch.outcomes[0].error.evidence['outcome']=='outcome_unknown'
        assert [x['state'] for x in _managed_receipts(tmp_path/'run')]==['started','outcome_unknown']


def test_conflict_identity_and_effect_mismatch_rejected_before_claim(tmp_path,monkeypatch):
    selected,managed,policy=_domain_catalog(monkeypatch)
    changed=replace(policy,conflict_domains=(ManagedConflictDomain(
        managed.declaration('double_value').registration_key,'external_read',reads=('object-A',)),))
    assert changed.identity()!=policy.identity()
    with _host(tmp_path,monkeypatch,selected=selected,managed=managed,policy=changed) as (p,loop,gateway,*_):
        batch=_run(loop,gateway,_domain_calls(managed,['double_value']),policy=changed)
        assert isinstance(batch.outcomes[0].error,PluginError)
        assert _managed_receipts(tmp_path/'run')==[]
    with pytest.raises(PluginError):
        ManagedConflictDomain('exact-key','external_write',reads=('A',))


def test_new_call_cannot_bypass_orphaned_admission(tmp_path,monkeypatch):
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        s=p['service']; prepared=gateway._methods['prepare'](_calls(1)[0])
        with s._active_lock: s._active_calls.pop((id(s.core),prepared.receipt_key))
        with pytest.raises(ManagedPluginInvocationReconciliationRequired,match='unobserved admission'):
            s.prepare('double_value',execution=p['execution'],call_id='new-provider-id',arguments={'value':1})
        assert len(_managed_receipts(tmp_path/'run'))==1


def test_buffered_worker_response_wins_cancel_race(monkeypatch):
    import cpn.plugins.worker as worker
    from cpn.plugins.api import implementation_identity
    from test_native_plugins import add
    real=worker.multiprocessing.get_context('spawn'); children=[]
    class Capture:
        def Pipe(self,**kwargs): return real.Pipe(**kwargs)
        def Event(self): return real.Event()
        def Process(self,**kwargs):
            child=real.Process(**kwargs); children.append(child); return child
    monkeypatch.setattr(worker.multiprocessing,'get_context',lambda _:Capture())
    def cancelled():
        children[0].join(15)
        assert children[0].exitcode==0
        return True
    packet={'arguments':{'x':3},'max_result_bytes':1024,'implementation':implementation_identity(add),
            'context':{'config':{'offset':2},'resources':(),'operation_id':'test_plugin/add',
                       'invocation_id':'test','firing_id':'test','call_id':'test'}}
    assert worker.execute_worker(add,packet,environment_names=(),timeout_seconds=20,cancelled=cancelled)==5


def test_finish_authority_revocation_leaves_claim_for_reconciliation(tmp_path,monkeypatch):
    from cpn.rpnh.registry.operations import OperationAuthorityError
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        service=p['service']; prepared=gateway._methods['prepare'](_calls(1)[0])
        original=service._authorize
        def revoked(*_): raise OperationAuthorityError('revoked exact execution')
        monkeypatch.setattr(service,'_authorize',revoked)
        with pytest.raises(OperationAuthorityError):
            service.finish(prepared,ManagedWorkerCompletion(output=8))
        assert [x['state'] for x in _managed_receipts(tmp_path/'run')]==['started']
        monkeypatch.setattr(service,'_authorize',original)
        with pytest.raises(ManagedPluginInvocationReconciliationRequired):
            gateway._methods['prepare'](_calls(1)[0])
        assert [x['state'] for x in _managed_receipts(tmp_path/'run')]==['started','outcome_unknown']


def test_cancel_after_claim_before_start_is_known_external_failure(tmp_path,monkeypatch):
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',lambda *a,**k:pytest.fail('cancelled work executed'))
    with _host(tmp_path,monkeypatch,effect='external_write') as (p,loop,gateway,*_):
        prepared=gateway._methods['prepare'](_calls(1)[0])
        assert not hasattr(prepared,'execution') and not hasattr(prepared,'core')
        assert prepared.started_receipt_ref['resource_version_id']
        completion=execute_prepared_invocation(prepared,cancelled=lambda:True)
        with pytest.raises(ManagedPluginInvocationFailed) as failed:
            p['service'].finish(prepared,completion)
        assert failed.value.evidence['outcome']=='failed'
        assert failed.value.code=='cancelled_before_start'


def test_out_of_order_terminals_keep_original_ordinals(tmp_path,monkeypatch):
    second_terminal=Event(); overlap=Barrier(2)
    def worker(_handler,packet,**kwargs):
        i=packet['arguments']['value']; overlap.wait(10)
        if i==0: assert second_terminal.wait(10)
        return i
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    with _host(tmp_path,monkeypatch) as (p,loop,gateway,*_):
        finish=gateway._methods['finish']
        def ordered_finish(prepared,completion):
            result=finish(prepared,completion)
            if result['call_id']=='call-1': second_terminal.set()
            return result
        gateway._methods['finish']=ordered_finish
        batch=_run(loop,gateway,_calls(2))
        assert [x.result['output'] for x in batch.outcomes]==[0,1]
        returned=[x['call_id'] for x in _managed_receipts(tmp_path/'run') if x['state']=='returned']
        assert returned==['call-1','call-0']


class _JointManagedPort:
    def __init__(self, calls, *, builtin_same_turn=False):
        self.calls=calls
        self.requests=[]
        self.builtin_same_turn=builtin_same_turn

    @staticmethod
    def final_calls():
        import json
        return [
            {'id':'final-write','name':'write_file','arguments':json.dumps({
                'path':'out/result.txt','description':'Joint scheduler result',
                'content':json.dumps('joint complete'),'output_port_id':'main.result',
                'outcome_id':'complete'})},
            {'id':'final-complete','name':'complete_interaction','arguments':'{}'},
        ]

    def request_once(self, attempt):
        import json
        from test_main_session_registry import _response
        request=json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        assert len(self.requests)<=2, 'unexpected replay/model continuation'
        if len(self.requests)==1:
            return _response(self.calls+(self.final_calls() if self.builtin_same_turn else []))
        return _response(self.final_calls())

    def close(self): pass


def _joint_identity_handler(context, arguments):
    return arguments['value']


def _joint_fixture(tmp_path, monkeypatch, selected, tools, policy, calls, *, effects=('pure',), builtin_same_turn=False):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
    from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
    from cpn.rpnh.registry._registry import _RegistryCore
    from test_main_session_registry import _write_execution_profile
    port=_JointManagedPort(calls,builtin_same_turn=builtin_same_turn)
    monkeypatch.setattr('cpn.plugins.catalog.load_catalog',lambda _:selected)
    monkeypatch.setattr('cpn.rpnh.agent_tasks.build_llm_input_port',lambda *a,**k:port)
    prepare=ManagedPluginInvocationService.prepare
    finish=ManagedPluginInvocationService.finish
    begin=_RegistryCore.begin
    settle=AgentLoopMechanicalLifecycle.settle_action_batch
    threads=[]; settlements=[]
    def owner_prepare(self,*args,**kwargs):
        threads.append(('prepare',get_ident()))
        return prepare(self,*args,**kwargs)
    def owner_finish(self,*args,**kwargs):
        threads.append(('finish',get_ident()))
        return finish(self,*args,**kwargs)
    def owner_begin(self,*args,**kwargs):
        threads.append(('write',get_ident()))
        return begin(self,*args,**kwargs)
    def owner_settle(self,*args,**kwargs):
        result=settle(self,*args,**kwargs)
        settlements.append((kwargs['turn_id'],len(kwargs['records']),result[0]))
        return result
    monkeypatch.setattr(ManagedPluginInvocationService,'prepare',owner_prepare)
    monkeypatch.setattr(ManagedPluginInvocationService,'finish',owner_finish)
    monkeypatch.setattr(_RegistryCore,'begin',owner_begin)
    monkeypatch.setattr(AgentLoopMechanicalLifecycle,'settle_action_batch',owner_settle)
    spec=AgentTaskSpec(tmp_path/'run','Exercise the selected managed policy.',
        (AgentStage('main','Run the managed calls and close their exact results.'),),
        _write_execution_profile(tmp_path),plugin_configuration={},plugin_catalog_digest=selected.digest,
        managed_bindings={'main':{'tools':{name:{'selector':selector} for name,selector in tools.items()},'admitted_effects':list(effects)}},
        managed_tool_policy=policy.identity())
    return spec,port,threads,settlements


def _joint_calls(names, values=None):
    import json
    values=values if values is not None else list(range(len(names)))
    return [{'id':f'joint-{i}','name':name,'arguments':json.dumps({'value':value})}
            for i,(name,value) in enumerate(zip(names,values))]


def _joint_managed_actions(run_dir):
    from test_optional_managed_plugin_actions import _action_documents
    return [d for kind,d in _action_documents(run_dir) if kind=='agent_action/v3']


def test_joint_registered_agentloop_real_process_overlap_single_turn_settlement(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    assert os.getuid()==1000
    gate=tmp_path/'gate'; gate.mkdir()
    selected=PluginCatalog((BoundPlugin(PluginDefinition('synthetic','1',(
        PluginOperation('double','Double',OBJECT_INPUT,INTEGER_OUTPUT,_process_barrier_handler),),
        config_schema={'type':'object','properties':{'gate':{'type':'string'}},'required':['gate'],'additionalProperties':False}),{'gate':str(gate)}),))
    spec,port,threads,settlements=_joint_fixture(tmp_path,monkeypatch,selected,
        {'double_value':'synthetic/double'},ManagedSchedulerPolicy(),_joint_calls(['double_value']*2))
    result=run_agent_task(spec)
    assert result['stop_reason']=='terminal'
    assert len({p.read_text() for p in gate.iterdir()})==2
    actions=_joint_managed_actions(tmp_path/'run')
    assert [d['output'] for d in actions]==[0,2]
    assert all(d['outcome']=='returned' and d['terminal_receipt_ref'] for d in actions)
    assert [n for _,n,_ in settlements]==[2,2]
    assert len({turn for turn,_,_ in settlements})==2
    assert {thread for _,thread in threads}=={get_ident()}
    assert {'prepare','finish','write'}<={kind for kind,_ in threads}
    delivered=[m for m in port.requests[1]['messages'] if m.get('role')=='tool']
    assert [m['tool_call_id'] for m in delivered]==['joint-0','joint-1']
    assert len(_managed_receipts(tmp_path/'run'))==4


def test_joint_registered_agentloop_invalid_known_failure_and_success(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    selected=_plugin_catalog()
    dispatched=[]
    def worker(_handler,packet,**kwargs):
        value=packet['arguments']['value']; dispatched.append(value)
        if value==1: raise WorkerFailure('handler_failed',may_have_executed=False)
        return value*2
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    spec,port,threads,settlements=_joint_fixture(tmp_path,monkeypatch,selected,
        {'double_value':'synthetic/double'},ManagedSchedulerPolicy(),
        _joint_calls(['double_value']*3,['invalid',1,2]))
    result=run_agent_task(spec)
    assert result['stop_reason']=='terminal'
    actions=_joint_managed_actions(tmp_path/'run')
    assert [d['outcome'] for d in actions]==['rejected','failed','returned']
    assert actions[0]['started_receipt_ref'] is None
    assert actions[2]['output']==4
    assert sorted(dispatched)==[1,2]
    assert [n for _,n,_ in settlements]==[3,2]
    assert len({turn for turn,_,_ in settlements})==2
    assert {thread for _,thread in threads}=={get_ident()}
    assert [m['tool_call_id'] for m in port.requests[1]['messages'] if m.get('role')=='tool']==['joint-0','joint-1','joint-2']


def test_joint_registered_agentloop_unknown_collects_success_and_stops_pending(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    selected=PluginCatalog((BoundPlugin(PluginDefinition('synthetic','1',(
        PluginOperation('write','Write',OBJECT_INPUT,INTEGER_OUTPUT,_joint_identity_handler,effect='external_write'),
        PluginOperation('pure','Pure',OBJECT_INPUT,INTEGER_OUTPUT,_joint_identity_handler),)),{}),))
    tools={'write_value':'synthetic/write','pure_value':'synthetic/pure'}
    catalog=ManagedPluginToolCatalog(selected,tools,admitted_effects=('pure','external_write'))
    policy=ManagedSchedulerPolicy('managed_conflict_domains/v1',2,(
        ManagedConflictDomain(catalog.declaration('write_value').registration_key,'external_write',writes=('object-A',)),
        ManagedConflictDomain(catalog.declaration('pure_value').registration_key,'pure'),))
    barrier=Barrier(2); unknown=Event(); dispatched=[]
    def worker(_handler,packet,**kwargs):
        i=packet['arguments']['value']; dispatched.append(i); assert i<2
        barrier.wait(10)
        if i==0: raise WorkerFailure('deadline_exceeded')
        assert unknown.wait(10)
        return i
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    spec,port,threads,settlements=_joint_fixture(tmp_path,monkeypatch,selected,tools,policy,
        _joint_calls(['write_value','pure_value','write_value']),effects=('pure','external_write'))
    finish=ManagedPluginInvocationService.finish
    def signal_unknown(self,*args,**kwargs):
        try: return finish(self,*args,**kwargs)
        except ManagedPluginInvocationReconciliationRequired:
            unknown.set(); raise
    monkeypatch.setattr(ManagedPluginInvocationService,'finish',signal_unknown)
    result=run_agent_task(spec)
    assert result['stop_reason']=='blocked_or_waiting'
    assert len(port.requests)==1
    assert sorted(dispatched)==[0,1]
    actions=_joint_managed_actions(tmp_path/'run')
    assert [d['outcome'] for d in actions]==['outcome_unknown','returned','rejected']
    assert actions[1]['output']==1 and actions[1]['terminal_receipt_ref']
    assert actions[2]['started_receipt_ref'] is None and actions[2]['terminal_receipt_ref'] is None
    assert actions[2]['non_delivery_reason']=='rejected_before_dispatch'
    assert [n for _,n,_ in settlements]==[3]
    assert {thread for _,thread in threads}=={get_ident()}
    assert len(_managed_receipts(tmp_path/'run'))==4


def test_joint_registered_agentloop_builtin_mix_keeps_legacy_path(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    selected=_plugin_catalog(); invoked=[]
    invoke=ManagedPluginInvocationService.invoke
    def legacy(self,*args,**kwargs): invoked.append(1); return invoke(self,*args,**kwargs)
    monkeypatch.setattr(ManagedPluginInvocationService,'invoke',legacy)
    monkeypatch.setattr(ManagedToolScheduler,'run',lambda *a,**k:pytest.fail('mixed builtins entered scheduler'))
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',lambda *a,**k:8)
    spec,port,threads,settlements=_joint_fixture(tmp_path,monkeypatch,selected,
        {'double_value':'synthetic/double'},ManagedSchedulerPolicy(),_joint_calls(['double_value']),builtin_same_turn=True)
    spec = replace(spec, managed_tool_policy=None)
    result=run_agent_task(spec)
    assert result['stop_reason']=='terminal' and invoked==[1]
    assert len(port.requests)==1
    assert [n for _,n,_ in settlements]==[3]


def test_joint_registered_agentloop_read_write_pure_domains(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    selected=PluginCatalog((BoundPlugin(PluginDefinition('synthetic','1',tuple(
        PluginOperation(name,name,OBJECT_INPUT,INTEGER_OUTPUT,_joint_identity_handler,effect=effect)
        for name,effect in [('write','external_write'),('read','external_read'),('pure','pure')])),{}),))
    tools={name+'_value':'synthetic/'+name for name in ('write','read','pure')}
    effects=('pure','external_read','external_write')
    catalog=ManagedPluginToolCatalog(selected,tools,admitted_effects=effects)
    policy=ManagedSchedulerPolicy('managed_conflict_domains/v1',2,(
        ManagedConflictDomain(catalog.declaration('write_value').registration_key,'external_write',writes=('object-A',)),
        ManagedConflictDomain(catalog.declaration('read_value').registration_key,'external_read',reads=('object-A',)),
        ManagedConflictDomain(catalog.declaration('pure_value').registration_key,'pure'),))
    overlap=Barrier(2); first_done=Event(); starts=[]
    def worker(_handler,packet,**kwargs):
        i=packet['arguments']['value'];starts.append(i)
        if i in (0,2):
            overlap.wait(10)
            if i==0:first_done.set()
        else:assert first_done.is_set()
        return i
    monkeypatch.setattr('cpn.plugins.worker.execute_worker',worker)
    spec,port,threads,settlements=_joint_fixture(tmp_path,monkeypatch,selected,tools,policy,
        _joint_calls(['write_value','read_value','pure_value','read_value']),effects=effects)
    result=run_agent_task(spec)
    assert result['stop_reason']=='terminal'
    actions=_joint_managed_actions(tmp_path/'run')
    assert [d['effect'] for d in actions]==['external_write','external_read','pure','external_read']
    assert [d['output'] for d in actions]==[0,1,2,3]
    assert starts.index(2)<starts.index(1)
    receipts=[(d['call_id'],d['state']) for d in _managed_receipts(tmp_path/'run')]
    assert receipts.index(('joint-0','returned'))<receipts.index(('joint-1','started'))
    assert [n for _,n,_ in settlements]==[4,2]
    assert len({turn for turn,_,_ in settlements})==2
    assert {thread for _,thread in threads}=={get_ident()}
    assert [m['tool_call_id'] for m in port.requests[1]['messages'] if m.get('role')=='tool']==['joint-0','joint-1','joint-2','joint-3']


def _isolated(tmp_path, source, *, broker=None, arguments=None, budget=None, cancelled=lambda:False, read_result=None, parent_identity=None):
    from cpn.plugins.controlled_script import IsolatedProgramBudget, ProgramBrokerReply, run_isolated_program
    observed=run_isolated_program(source=source,arguments=arguments or {},parent_identity=parent_identity or {'program':'synthetic-parent-v1'},
        allowlist=('echo','known_fail'),budget=budget or IsolatedProgramBudget(),
        broker=broker or (lambda call:ProgramBrokerReply(value=dict(call.arguments))),
        work_root=tmp_path,cancelled=cancelled,read_result=read_result)
    # Persist actual process observations, never hand-built Registry receipts.
    import json
    from cpn.plugins.api import json_copy
    evidence=tmp_path.parent/(tmp_path.name+'-program-observations')
    assert evidence.resolve().parent==tmp_path.parent.resolve()
    evidence.mkdir(exist_ok=True)
    path=evidence/(str(len(tuple(evidence.glob('*.json'))))+'.json')
    assert path.resolve().parent==evidence.resolve()
    path.write_text(json.dumps({'status':observed.status,'exit_code':observed.exit_code,
        'stdout':observed.stdout,'stderr':observed.stderr,'setup_error':json_copy(observed.setup_error),
        'value':json_copy(observed.value),'runtime_identity':json_copy(observed.runtime_identity),
        'broker_integration':'synthetic_host_callback_only',
        'reads':[{'key':item.request.key,'locator':json_copy(item.request.locator),
                  'offset_chars':item.request.offset_chars,'max_bytes':item.request.max_bytes,
                  'value':json_copy(item.reply.value),'error_code':item.reply.error_code}
                 for item in observed.reads],
        'calls':[{'key':item.call.key,'tool':item.call.tool,'arguments':json_copy(item.call.arguments),
                  'parent_identity':json_copy(item.call.parent_identity),'value':json_copy(item.reply.value),
                  'error_code':item.reply.error_code,'outcome_unknown':item.reply.outcome_unknown}
                 for item in observed.calls]},indent=2)+'\n')
    return observed


def test_isolated_program_smoke_real_namespace(tmp_path):
    assert os.getuid()==1000
    result=_isolated(tmp_path,'import sys,os\nresult({"uid":os.getuid(),"python":sys.executable,"pid":os.getpid()})')
    assert result.status=='returned',result
    assert result.value['python']=='/runtime/bin/python'
    assert result.value['pid']==1
    assert not list(tmp_path.iterdir())


def test_isolated_program_denies_host_paths_sockets_and_runtime_writes(tmp_path,monkeypatch):
    import socket
    sentinel=tmp_path/'outside-sentinel.txt'; sentinel.write_text('synthetic host-only value')
    owner_path=tmp_path/'owner.sock'
    from cpn.rpnh.unix_transport import unix_socket_address
    owner=socket.socket(socket.AF_UNIX)
    with unix_socket_address(owner_path) as address: owner.bind(address)
    monkeypatch.setenv('W6_SYNTHETIC_CREDENTIAL','must-not-enter-program')
    try:
        observed=_isolated(tmp_path,r'''
import os, socket, errno
report = {}
for label, path in arguments.items():
    try:
        with open(path) as stream: report[label] = stream.read()
    except OSError as exc: report[label] = exc.errno
for family in (socket.AF_INET, socket.AF_UNIX):
    try: socket.socket(family); report[str(family)] = 'allowed'
    except OSError as exc: report[str(family)] = exc.errno
try:
    os.open('/runtime/lib/python3.12/os.py',os.O_WRONLY)
    report['runtime_write'] = 'allowed'
except OSError as exc: report['runtime_write'] = exc.errno
report['runtime_readonly'] = bool(os.statvfs('/runtime/lib/python3.12/os.py').f_flag & os.ST_RDONLY)
report['credential'] = os.environ.get('W6_SYNTHETIC_CREDENTIAL')
report['home'] = os.environ.get('HOME')
report['proc'] = os.path.exists('/proc')
result(report)
''',arguments={'sentinel':str(sentinel),'owner':str(owner_path),'registry':str(tmp_path/'Registry.sqlite')})
    finally: owner.close()
    assert observed.status=='returned',observed
    assert observed.value['sentinel']==2 and observed.value['owner']==2 and observed.value['registry']==2
    assert observed.value['2']==1 and observed.value['1']==1
    assert observed.value['runtime_write'] in {13,30}
    assert observed.value['runtime_readonly'] is True
    assert observed.value['credential'] is None and observed.value['home'] is None
    assert observed.value['proc'] is False
    assert sentinel.read_text()=='synthetic host-only value'


def test_isolated_program_parallel_pipe_broker_and_caught_known_error(tmp_path):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    from contextvars import ContextVar
    operation_scope=ContextVar('synthetic_program_operation_scope')
    marker=object(); operation_scope.set(marker)
    overlap=Barrier(2); threads=[]
    def broker(call):
        assert operation_scope.get() is marker
        threads.append(get_ident())
        assert dict(call.parent_identity)=={'program':'synthetic-parent-v1'}
        if call.tool=='known_fail':return ProgramBrokerReply(error_code='synthetic_known_failure')
        overlap.wait(3)
        return ProgramBrokerReply(value={'doubled':call.arguments['n']*2})
    observed=_isolated(tmp_path,r'''
values = tools.parallel([
    {'key':'row-a','name':'echo','args':{'n':2}},
    {'key':'row-b','name':'echo','args':{'n':3}},
])
try:
    tools.call('known-error','known_fail',{})
except ToolCallError as exc:
    error = exc.code
print('private diagnostic, not the result')
result({'values':values,'caught':error})
''',broker=broker)
    assert observed.status=='returned',observed
    assert observed.value['caught']=='synthetic_known_failure'
    assert [v['value']['doubled'] for v in observed.value['values']]==[4,6]
    assert [o.call.key for o in observed.calls]==['row-a','row-b','known-error']
    assert len(set(threads[:2]))==2 and get_ident() not in threads
    assert observed.stdout=='private diagnostic, not the result\n'


def test_isolated_program_timeout_reaps_namespace_processes(tmp_path,monkeypatch):
    import cpn.plugins.controlled_script as runtime
    process_ids=[]; launched=[]
    popen=runtime.subprocess.Popen
    def capture(*args,**kwargs):
        process=popen(*args,**kwargs)
        if args[0][0]=='/usr/bin/unshare': launched.append(process)
        return process
    monkeypatch.setattr(runtime.subprocess,'Popen',capture)
    def broker(call):
        parent=launched[0].pid
        children=Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
        assert children
        process_ids.extend([parent,*map(int,children)])
        return runtime.ProgramBrokerReply(value=True)
    start=time.monotonic()
    observed=_isolated(tmp_path,"tools.call('started','echo',{})\nwhile True: pass",broker=broker,
        budget=runtime.IsolatedProgramBudget(wall_seconds=0.8,cpu_seconds=2))
    assert observed.status=='timeout',observed
    assert time.monotonic()-start<5
    assert len(observed.calls)==1 and observed.calls[0].reply.value is True
    end=time.monotonic()+2
    while any(Path(f'/proc/{pid}').exists() for pid in process_ids) and time.monotonic()<end:
        time.sleep(0.01)
    assert not [pid for pid in process_ids if Path(f'/proc/{pid}').exists()]
    assert not list(tmp_path.iterdir())


def test_isolated_program_fork_exec_and_memory_are_bounded(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    observed=_isolated(tmp_path,r'''
import os, resource
report = {}
try: os.fork(); report['fork']='allowed'
except OSError as exc: report['fork']=exc.errno
try: os.execve('/runtime/bin/python',['python','-c','pass'],{})
except OSError as exc: report['exec']=exc.errno
try: value=bytes(512*1024*1024); report['allocation']='allowed'
except MemoryError: report['allocation']='denied'
report['cpu']=resource.getrlimit(resource.RLIMIT_CPU)
report['memory']=resource.getrlimit(resource.RLIMIT_AS)
result(report)
''',budget=IsolatedProgramBudget(memory_bytes=128*1024*1024))
    assert observed.status=='returned',observed
    assert observed.value['fork']==1 and observed.value['exec']==1
    assert observed.value['allocation']=='denied'
    assert observed.value['cpu']==(2,2)
    assert observed.value['memory']==(128*1024*1024,)*2


@pytest.mark.parametrize('source',[
    "print('x'*1000000)",
    "result('x'*1000000)",
])
def test_isolated_program_output_is_bounded(tmp_path,source):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    observed=_isolated(tmp_path,source,budget=IsolatedProgramBudget(max_output_bytes=4096,max_frame_bytes=4096))
    assert observed.status in {'output_limit','failed'},observed
    assert len(observed.stdout.encode())+len(observed.stderr.encode())<=4096
    assert observed.value is None


def test_isolated_program_exception_drains_accepted_callbacks(tmp_path):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    overlap=Barrier(2)
    def broker(call):
        overlap.wait(3)
        if call.key=='slow':
            end=time.monotonic()+3
            while not call.cancelled() and time.monotonic()<end:time.sleep(0.01)
            assert call.cancelled()
        return ProgramBrokerReply(value=call.key)
    observed=_isolated(tmp_path,r'''
tools.send({'type':'call','key':'fast','name':'echo','args':{}})
tools.send({'type':'call','key':'slow','name':'echo','args':{}})
tools.receive()
raise RuntimeError('synthetic script exception')
''',broker=broker)
    assert observed.status=='failed',observed
    assert [o.reply.value for o in observed.calls]==['fast','slow']


def test_isolated_program_unknown_stops_new_requests_collects_siblings(tmp_path):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    overlap=Barrier(2)
    def broker(call):
        assert call.key in {'unknown','success'}
        overlap.wait(3)
        if call.key=='unknown':return ProgramBrokerReply(error_code='synthetic_unknown',outcome_unknown=True)
        end=time.monotonic()+3
        while not call.cancelled() and time.monotonic()<end:time.sleep(0.01)
        return ProgramBrokerReply(value='retained success')
    observed=_isolated(tmp_path,r'''
result(tools.parallel([
 {'key':'unknown','name':'echo','args':{}},
 {'key':'success','name':'echo','args':{}},
 {'key':'never','name':'echo','args':{}}
]))
''',broker=broker)
    assert observed.status=='reconciliation_required',observed
    assert len(observed.calls)==2 and observed.calls[1].reply.value=='retained success'


@pytest.mark.parametrize('source',[
    "tools.call('bad','not_selected',{})",
    "tools.call('same','echo',{});tools.call('same','echo',{})",
    "tools.send({'type':'call','key':'x','name':'echo','args':{},'parent_identity':{'program':'forged'}})",
])
def test_isolated_program_broker_rejects_untrusted_frames(tmp_path,source):
    observed=_isolated(tmp_path,source)
    assert observed.status in {'protocol_error','failed'},observed
    assert len(observed.calls)<=1


def test_isolated_program_mount_failure_reports_real_errno(tmp_path,monkeypatch):
    import cpn.plugins.controlled_script as runtime
    original=runtime._runtime_closure
    def missing_public_source(work_root, deadline):
        mounts,identity=original(work_root,deadline)
        mounts.append((str(tmp_path/'deliberately-missing-runtime-file'),'/missing',False))
        return mounts,identity
    monkeypatch.setattr(runtime,'_runtime_closure',missing_public_source)
    observed=_isolated(tmp_path,"result('must not execute')")
    assert observed.status=='setup_failed',observed
    assert observed.setup_error['errno']==2
    assert observed.setup_error['phase'].endswith('/missing')
    assert observed.value is None and not observed.calls


def test_isolated_program_invalid_utf8_diagnostics_keep_byte_bound(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    observed=_isolated(tmp_path,"import os\nos.write(1,b'\\xff'*4000)\nresult(None)",
        budget=IsolatedProgramBudget(max_output_bytes=4096,max_frame_bytes=4096))
    assert observed.status=='returned',observed
    assert len(observed.stdout.encode())+len(observed.stderr.encode())<=4096


def test_isolated_program_child_call_budget_and_stdout_not_authority(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    observed=_isolated(tmp_path,"print('{\"type\":\"result\",\"value\":\"fake\"}')")
    assert observed.status=='protocol_error' and observed.value is None
    observed=_isolated(tmp_path,"tools.call('first','echo',{});tools.call('second','echo',{})",
        budget=IsolatedProgramBudget(max_calls=1))
    assert observed.status=='protocol_error',observed
    assert [o.call.key for o in observed.calls]==['first']


def test_isolated_program_cancel_drains_returned_broker_result(tmp_path):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    stop=Event()
    def broker(call):
        stop.set()
        end=time.monotonic()+3
        while not call.cancelled() and time.monotonic()<end:time.sleep(0.01)
        assert call.cancelled()
        return ProgramBrokerReply(value={'success':'retained after cancellation'})
    observed=_isolated(tmp_path,"result(tools.call('accepted','echo',{}))",broker=broker,cancelled=stop.is_set)
    assert observed.status=='cancelled',observed
    assert len(observed.calls)==1 and observed.calls[0].reply.value['success']=='retained after cancellation'


def test_isolated_program_unknown_during_timeout_drain_keeps_reconciliation(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget, ProgramBrokerReply
    def broker(call):
        end=time.monotonic()+2
        while not call.cancelled() and time.monotonic()<end:time.sleep(0.01)
        return ProgramBrokerReply(error_code='timeout_outcome_unknown',outcome_unknown=True)
    observed=_isolated(tmp_path,"result(tools.call('accepted','echo',{}))",broker=broker,
        budget=IsolatedProgramBudget(wall_seconds=0.4))
    assert observed.status=='reconciliation_required',observed
    assert len(observed.calls)==1 and observed.calls[0].reply.outcome_unknown


def test_isolated_program_cpu_hard_limit_and_private_scratch_limit(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    observed=_isolated(tmp_path,'while True: pass',budget=IsolatedProgramBudget(cpu_seconds=1,wall_seconds=4))
    assert observed.status=='failed' and observed.setup_error is None,observed
    assert (observed.exit_code in {-9,137} or (observed.exit_code==1
        and 'unshare: sigprocmask unblock failed: Invalid argument' in observed.stderr)),observed
    observed=_isolated(tmp_path,r'''
import errno
try:
    with open('/work/a','wb') as stream: stream.write(b'x'*(768*1024))
    with open('/work/b','wb') as stream: stream.write(b'x'*(768*1024))
    result('unbounded')
except OSError as exc: result({'errno':exc.errno})
''',budget=IsolatedProgramBudget(scratch_bytes=1024*1024))
    assert observed.status=='returned' and observed.value['errno']==28,observed
    assert not list(tmp_path.iterdir())


def _program_read_fixture():
    # Protocol-only synthetic references; these are NOT registered receipts.
    program={'entity_type':'agent_tool_program_invocation/v1','logical_id':'invocation:synthetic-program',
             'version_id':'invocation_version:synthetic-program'}
    child={'entity_type':'agent_tool_program_call/v1','logical_id':'invocation:synthetic-child',
           'version_id':'invocation_version:synthetic-child'}
    terminal={'resource_id':'resource:synthetic-terminal','resource_version_id':'resource_version:synthetic-terminal'}
    locator={'program_invocation_ref':program,'program_call_ref':child,'terminal_receipt_ref':terminal}
    parent={'program_invocation_ref':program,'agent_action_ref':{
        'entity_type':'agent_action/v2','logical_id':'agent_action:synthetic-parent',
        'version_id':'agent_action_version:synthetic-parent'}}
    return parent,locator


def _synthetic_child_page(locator,text,offset,maximum):
    from cpn.plugins.api import canonical,json_copy
    if not 0<=offset<=len(text):raise ValueError('invalid offset')
    def page(end):
        return {'kind':'agent_tool_program_child_output_page/v1',**json_copy(locator),
                'reader':'read_program_child_output','content':text[offset:end],'offset_chars':offset,
                'next_offset_chars':end if end<len(text) else None,'total_chars':len(text),
                'truncated':end<len(text)}
    if len(canonical(page(offset)))>maximum:raise ValueError('minimum page does not fit')
    low=offset;high=min(len(text),offset+maximum)
    while low<high:
        mid=(low+high+1)//2
        if len(canonical(page(mid)))<=maximum:low=mid
        else:high=mid-1
    if low==offset and offset<len(text):raise ValueError('page cannot advance')
    return page(low)


def test_isolated_read_result_large_body_pages_no_business_replay(tmp_path):
    from contextvars import ContextVar
    from cpn.plugins.controlled_script import IsolatedProgramBudget,ProgramBrokerReply
    from cpn.plugins.api import canonical,json_copy
    parent,locator=_program_read_fixture()
    body={'payload':'A'*210000+'汉字"\\\nread-tail'}
    text=canonical(body).decode();business=[];queries=[]
    marker=ContextVar('reader_program_scope');marker.set(parent)
    def broker(call):
        business.append(call.key)
        assert call.key=='large-body'
        return ProgramBrokerReply(value=_synthetic_child_page(locator,text,0,1800))
    def reader(request):
        assert marker.get()==parent and request.parent_identity==parent
        assert request.locator==locator
        queries.append(request)
        return _synthetic_child_page(locator,text,request.offset_chars,request.max_bytes)
    observed=_isolated(tmp_path,r'''
import json
page=tools.call('large-body','echo',{})
parts=[page['content']]
while page['next_offset_chars'] is not None:
    page=tools.read_result(page,page['next_offset_chars'])
    parts.append(page['content'])
value=json.loads(''.join(parts))
result({'length':len(value['payload']),'tail':value['payload'][-14:]})
''',broker=broker,read_result=reader,parent_identity=parent,
        budget=IsolatedProgramBudget(max_calls=128,max_frame_bytes=4096,max_output_bytes=1024*1024,wall_seconds=10))
    assert observed.status=='returned',observed
    assert observed.value['length']==len(body['payload']) and observed.value['tail']==body['payload'][-14:]
    assert business==['large-body'] and len(observed.calls)==1
    assert len(observed.reads)==len(queries)>50
    assert [o.request.key for o in observed.reads]==[f'read-result:{i}' for i in range(len(queries))]
    for item in observed.reads:
        wrapper={'type':'read_result_reply','key':item.request.key,'ok':True,'value':json_copy(item.reply.value)}
        assert len(canonical(wrapper))+1<=4096
        assert len(canonical(item.reply.value))<=item.request.max_bytes<4096
    assert not any(o.call.tool=='read_program_child_output' for o in observed.calls)


@pytest.mark.parametrize('tamper',['program','child','terminal','extra'])
def test_isolated_read_result_tampered_locator_never_exposes_output(tmp_path,tamper):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    parent,locator=_program_read_fixture();queries=[]
    def broker(call):return ProgramBrokerReply(value=locator)
    def reader(request):
        queries.append(request)
        if request.locator['program_call_ref']!=locator['program_call_ref']:
            raise ValueError('not this program child')
        return _synthetic_child_page(locator,'"private child body"',0,request.max_bytes)
    source=r'''
loc=tools.call('child','echo',{})
kind=arguments['tamper']
if kind=='program': loc['program_invocation_ref']['version_id']='another-program'
elif kind=='child': loc['program_call_ref']['version_id']='another-child'
elif kind=='terminal': loc['terminal_receipt_ref']['resource_version_id']='another-terminal'
else: loc['receipt_fields']=['arguments','environment']
try:
    page=tools.read_result(loc)
    result({'leaked':page})
except ToolCallError as exc: result({'error':exc.code})
'''
    observed=_isolated(tmp_path,source,broker=broker,read_result=reader,parent_identity=parent,arguments={'tamper':tamper})
    assert observed.status=='returned',observed
    assert observed.value['error'] in {'read_result_locator_invalid','read_result_rejected','read_result_reply_invalid'}
    assert 'leaked' not in observed.value
    assert len(observed.calls)==1
    assert len(queries)==(0 if tamper in {'program','extra'} else 1)
    assert all(o.reply.value is None and o.reply.error_code for o in observed.reads)


def test_isolated_read_result_shares_call_budget_and_independent_key_space(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget,ProgramBrokerReply
    parent,locator=_program_read_fixture();business=[];queries=[]
    def broker(call):business.append(call.key);return ProgramBrokerReply(value=locator)
    def reader(request):queries.append(request.key);return _synthetic_child_page(locator,'0',0,request.max_bytes)
    observed=_isolated(tmp_path,r'''
locator=tools.call('read-result:0','echo',{})
tools.read_result(locator)
tools.read_result(locator)
''',broker=broker,read_result=reader,parent_identity=parent,budget=IsolatedProgramBudget(max_calls=2))
    assert observed.status=='protocol_error',observed
    assert business==['read-result:0'] and queries==['read-result:0']
    assert len(observed.calls)==1 and len(observed.reads)==1


def test_isolated_read_result_complete_wrapper_budget_and_oversized_reply(tmp_path):
    from cpn.plugins.controlled_script import IsolatedProgramBudget
    from cpn.plugins.api import canonical,json_copy
    parent,locator=_program_read_fixture();budgets=[]
    def reader(request):
        budgets.append(request.max_bytes)
        return _synthetic_child_page(locator,'x'*5000,0,request.max_bytes)
    observed=_isolated(tmp_path,'result(tools.read_result(arguments["locator"]))',arguments={'locator':locator},
        read_result=reader,parent_identity=parent,budget=IsolatedProgramBudget(max_frame_bytes=1200))
    assert observed.status=='returned',observed
    assert len(observed.reads)==1 and not observed.calls
    item=observed.reads[0]
    assert budgets==[1200-(len(canonical({'type':'read_result_reply','key':'read-result:0','ok':True,'value':None}))+1-4)]
    assert len(canonical({'type':'read_result_reply','key':item.request.key,'ok':True,'value':json_copy(item.reply.value)}))+1<=1200
    def oversized(request):return _synthetic_child_page(locator,'x'*5000,0,2000)
    observed=_isolated(tmp_path,r'''
try: tools.read_result(arguments['locator'])
except ToolCallError as exc: result(exc.code)
''',arguments={'locator':locator},read_result=oversized,parent_identity=parent,
        budget=IsolatedProgramBudget(max_frame_bytes=1200))
    assert observed.status=='returned' and observed.value=='read_result_reply_too_large',observed
    assert not observed.calls and observed.reads[0].reply.value is None


@pytest.mark.parametrize('frame',[
    {'type':'read_result','key':'read-result:99','locator':{},'offset_chars':0,'max_bytes':10000},
    {'type':'read_result','key':'read-result:0','locator':{},'offset_chars':-1,'max_bytes':10000},
    {'type':'read_result','key':'read-result:0','locator':{},'offset_chars':0,'max_bytes':True},
    {'type':'read_result','key':'read-result:0','locator':{},'offset_chars':0,'max_bytes':10000,'receipt_fields':['secret']},
])
def test_isolated_read_result_invalid_frame_is_not_business_call(tmp_path,frame):
    parent,_=_program_read_fixture();queries=[]
    observed=_isolated(tmp_path,'tools.send(arguments);tools.receive("read_result_reply")',arguments=frame,
        read_result=lambda request:queries.append(request),parent_identity=parent,
        broker=lambda call:pytest.fail('read frame used business broker'))
    assert observed.status=='protocol_error',observed
    assert not queries and not observed.calls and not observed.reads


def test_isolated_program_synchronous_main_call_parallel_and_read_result(tmp_path):
    from cpn.plugins.controlled_script import ProgramBrokerReply
    parent,locator=_program_read_fixture();overlap=Barrier(2);queries=[]
    def broker(call):
        if call.key=='single':return ProgramBrokerReply(value=locator)
        overlap.wait(3)
        return ProgramBrokerReply(value={'number':call.arguments['n']})
    def reader(request):
        queries.append(request.key)
        return _synthetic_child_page(locator,'{"from_page":42}',request.offset_chars,request.max_bytes)
    observed=_isolated(tmp_path,r'''
import json

def main(arguments):
    locator=tools.call('single','echo',{})
    values=tools.parallel([
        {'key':'left','name':'echo','args':{'n':arguments['n']}},
        {'key':'right','name':'echo','args':{'n':arguments['n']+1}},
    ])
    page=tools.read_result(locator,0,10000)
    return {'numbers':[item['value']['number'] for item in values],
            'page':json.loads(page['content'])}

result(main(arguments))
''',arguments={'n':7},broker=broker,read_result=reader,parent_identity=parent)
    assert observed.status=='returned',observed
    assert observed.value=={'numbers':(7,8),'page':{'from_page':42}}
    assert [o.call.key for o in observed.calls]==['single','left','right']
    assert queries==['read-result:0'] and len(observed.reads)==1


@pytest.mark.parametrize('source,status',[
    ("def main(arguments):\n    raise RuntimeError('must not auto-run')\n",'protocol_error'),
    ("async def main(arguments):\n    return 'not awaited'\nresult(main(arguments))\n",'failed'),
])
def test_isolated_program_does_not_invoke_or_await_main(tmp_path,source,status):
    observed=_isolated(tmp_path,source,broker=lambda _:pytest.fail('unexpected business call'))
    assert observed.status==status,observed
    assert observed.value is None and not observed.calls and not observed.reads


def test_isolated_program_wall_deadline_while_cancel_probe_blocked(tmp_path,monkeypatch):
    import cpn.plugins.controlled_script as runtime
    from threading import current_thread
    probe_entered=Event();probe_returned=Event();release_probe=Event()
    broker_entered=Event();release_broker=Event();broker_drained=Event()
    launched=[];probe_threads=[];observations={}
    popen=runtime.subprocess.Popen
    def capture(*args,**kwargs):
        process=popen(*args,**kwargs)
        if args[0][0]=='/usr/bin/unshare':launched.append(process)
        return process
    monkeypatch.setattr(runtime.subprocess,'Popen',capture)
    def cancelled():
        probe_threads.append(current_thread().name)
        if len(probe_threads)==1:
            return False  # Preserve the existing synchronous preflight fact.
        probe_entered.set()
        assert release_probe.wait(12), 'test must release the finite HOST probe'
        probe_returned.set()
        return False
    def broker(call):
        process=launched[0]
        children=Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
        assert children
        observations['pids']=[process.pid,*map(int,children)]
        observations['deadline']=call.deadline_monotonic
        broker_entered.set()
        assert release_broker.wait(12), 'test must release the accepted callback'
        assert call.cancelled(), 'wall termination must signal accepted callback cancellation'
        broker_drained.set()
        return runtime.ProgramBrokerReply(value={'retained':'accepted completion after wall stop'})
    with ThreadPoolExecutor(max_workers=1) as host:
        future=host.submit(_isolated,tmp_path,
            "tools.call('accepted','echo',{})\nwhile True: pass",
            broker=broker,cancelled=cancelled,
            budget=runtime.IsolatedProgramBudget(wall_seconds=2,cpu_seconds=10))
        try:
            assert probe_entered.wait(4) and broker_entered.wait(4)
            process=launched[0]
            def process_state(pid):
                try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
                except (FileNotFoundError,ProcessLookupError):return 'absent'
            end=observations['deadline']+1
            while time.monotonic()<end:
                if process.poll() is not None and all(
                        process_state(pid) in {'absent','Z','X'} for pid in observations['pids']):break
                time.sleep(0.01)
            observations['termination_observed_at']=time.monotonic()
            observations['states_while_probe_blocked']={str(pid):process_state(pid) for pid in observations['pids']}
            assert process.poll() is not None, 'blocked owner probe held the wall deadline hostage'
            assert all(value in {'absent','Z','X'} for value in observations['states_while_probe_blocked'].values())
            assert not probe_returned.is_set() and not release_probe.is_set()
            assert not future.done() and not broker_drained.is_set()
            release_probe.set()
            assert probe_returned.wait(2), 'same trusted HOST probe must finish normally'
            assert not future.done(), 'accepted callback must drain before returning'
        finally:
            release_probe.set();release_broker.set()
        result=future.result(timeout=5)
    assert result.status=='timeout' and result.setup_error is None,result
    assert broker_drained.is_set() and len(result.calls)==1
    assert result.calls[0].reply.value['retained']=='accepted completion after wall stop'
    assert 'isolated-program-deadline' not in probe_threads
    end=time.monotonic()+2
    while any(Path(f'/proc/{pid}').exists() for pid in observations['pids']) and time.monotonic()<end:time.sleep(0.01)
    assert not [pid for pid in observations['pids'] if Path(f'/proc/{pid}').exists()]
    import json
    evidence=tmp_path.parent/(tmp_path.name+'-program-observations')/'wall-gate.json'
    assert evidence.resolve().parent.parent==tmp_path.parent.resolve()
    evidence.write_text(json.dumps({**observations,'probe_threads':probe_threads,
        'probe_was_blocked_at_termination':True,'accepted_callback_retained':True,
        'status':result.status},indent=2)+'\n')
