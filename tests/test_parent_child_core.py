"""H7 original Registry/PN D0; no transport proof or production create path."""
import json
import pytest
from dataclasses import replace
from parent_child_fixtures import *
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.errors import StaleInvocationContext
from cpn.rpnh.registry._registry import _RegistryCore
from test_static_lease_reads import finish,current


def test_real_parent_causal_chain_is_provisional(tmp_path):
    owner,admitted,execution,prepared,intent,dispatch,worker,acceptance=accepted_parent(tmp_path/'parent')
    core=owner._core
    assert intent.entity_type==h7.INTENT_V2
    assert isinstance(prepared,h7.PreparedChildMaterials)
    assert not core.event_store.object_rows_by_type(h7.PARENT_KINDS[0])
    assert h7.register_child_intent(core,execution,prepared)==intent
    view=core.event_store.canonical_view()
    assert core.event_store.object_row_for_view(view,version_id=acceptance.version_id) is None
    firing_view=core.event_store.firing_view(firing_version_id=execution.operation.firing.transition_firing_ref.version_id,
        invocation_version_id=execution.operation.canonical.context.invocation_ref.version_id)
    assert core.event_store.object_row_for_view(firing_view,version_id=acceptance.version_id) is not None


def test_bound_origin_true_admission_start_success(tmp_path,monkeypatch):
    owner,parent=bound_owner(tmp_path/'child',monkeypatch)
    initial=next(t.state for t in current(owner)[2].tokens if t.state.place=='rpnhOrigin')
    first=owner.admit('step.run',logical_tau=0,command_id='first:admit')
    second=owner.admit('step.run',logical_tau=0,command_id='second:admit')
    assert first is not None and second is not None
    finish(owner,first,'first');finish(owner,second,'second')
    assert next(t.state for t in current(owner)[2].tokens if t.state.place=='rpnhOrigin')==initial
    assert len([r for r in owner._core.event_store.object_rows_by_type('petri_token/v1')
        if json.loads(r['metadata_json'])['place']=='rpnhOrigin'])==1

@pytest.mark.parametrize('values', [
    {'capacity':2}, {'with_launch':False}, {'executor':EXECUTOR},
    {'outcomes':('complete','interrupted')},
])
def test_parent_pn_declared_prerequisites_not_host_flags(tmp_path,values):
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent',**values)
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        h7.register_child_intent(owner._core,execution,prepared)
    assert owner._core.event_store.max_ordinal()==before


def test_no_native_evidence_constructor_or_json_permission(tmp_path):
    with pytest.raises(h7.ParentChildUnsupported):
        h7._NativeBoundaryEvidence(store='fake',action='accepted',body={})
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent')
    intent=h7.register_child_intent(owner._core,execution,prepared)
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported):
        h7._advance_record(owner._core,h7.PARENT_KINDS[1],intent,details={'bundle_digest':'a'*64},evidence={'accepted':True})
    assert owner._core.event_store.max_ordinal()==before


@pytest.mark.parametrize('damage',['start_id','generation','lease','parent_run','slot','target','material'])
def test_parent_commit_rejects_mutated_native_closure(tmp_path,damage):
    from copy import deepcopy
    from cpn.rpnh.registry.identities import new_id
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent')
    intent=h7.register_child_intent(owner._core,execution,prepared)
    body=deepcopy(dict(owner._core.get_version(intent.version_id).metadata))
    if damage=='start_id': body['execution']['start_event_id']=str(new_id('event'))
    elif damage=='generation':body['execution']['execution_generation']=1
    elif damage=='lease':body['execution']['lease_ref']['version_id']=str(new_id('operation_execution_lease_version'))
    elif damage=='parent_run':body['parent']['run_ref']['version_id']=str(new_id('run_version'))
    elif damage=='slot':body['slot_id']='another'
    elif damage=='target':body['target']['relative_path']='h7-runs/'+'f'*64+'/run'
    else:body['materials']['public_material_digest']='c'*64
    before=owner._core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict,ValueError,RuntimeError)):
        h7._record(owner._core,intent.entity_type,body,predecessors=(body['request_ref'],body['execution']['firing_ref'],body['execution']['lease_ref']))
    assert owner._core.event_store.max_ordinal()==before


@pytest.mark.parametrize('closed',['stop','new_writer'])
def test_intent_replay_rechecks_live_entry_before_exact_replay(tmp_path,closed):
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent')
    intent=h7.register_child_intent(owner._core,execution,prepared)
    if closed=='stop':owner.record_owner_stop(idempotency_key='parent:stop')
    else:owner._core.event_store.acquire_writer()
    before=owner._core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict,RuntimeError)):
        h7.register_child_intent(owner._core,execution,prepared)
    assert owner._core.event_store.max_ordinal()==before


def test_acceptance_direct_exact_replay_requires_native_boundary(tmp_path,monkeypatch):
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent')
    intent=h7.register_child_intent(owner._core,execution,prepared)
    dispatch=phase(owner,h7.PARENT_KINDS[1],intent,{'bundle_digest':'a'*64})
    worker=phase(owner,h7.PARENT_KINDS[2],dispatch,{'worker':{'uid':1000,'pid':12345,'start_ticks':100,'boot_id':'offline'}})
    saved=[];original=owner._core.event_store.publish_batch
    def capture(**kwargs):
        saved.append(kwargs);return original(**kwargs)
    monkeypatch.setattr(owner._core.event_store,'publish_batch',capture)
    intent_body=owner._core.get_version(intent.version_id).metadata
    receipt=phase(owner,h7.PARENT_KINDS[3],worker,{'request_digest':h7.digest(h7._json(h7.bootstrap_request_material(intent_body,_ref_payload(dispatch))))})
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported):original(**saved[-1])
    assert owner._core.event_store.max_ordinal()==before


def test_parent_unknown_completion_cannot_release_slot(tmp_path):
    owner,admitted,execution,prepared,*_=accepted_parent(tmp_path/'parent')
    outputs=owner.products(execution,outcome_id='complete',products={'step.result':(canonical_json('fake child terminal'),)},command_id='fake:products')
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported):owner.succeed(outputs,command_id='fake:success')
    assert owner._core.event_store.max_ordinal()==before
    assert len(owner._core.event_store.list_events_by_type(('transition_firing_settled/v1',)))==0


def test_parent_start_payload_is_fully_rechecked(tmp_path):
    owner,admitted,execution,prepared=parent_owner(tmp_path/'parent')
    with owner._core.event_store.connect() as db:
        row=db.execute("SELECT event_id,payload_json FROM events WHERE event_type='operation_execution_started/v1'").fetchone()
        body=json.loads(row['payload_json']);body['input_resource_refs']=[]
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(body),row['event_id']))
    with pytest.raises(RegistryConflict):h7.register_child_intent(owner._core,execution,prepared)


def test_child_no_writable_reentry_zero_epoch_change(tmp_path,monkeypatch):
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    core=child._core;epoch=core.event_store.writer_epoch;head=core.event_store.max_ordinal()
    for action in (
        lambda:_RegistryCore(core.run_dir,create=False,catalog=core.catalog),
        lambda:core.event_store.acquire_writer(),
        lambda:core.event_store.rotate_writer(expected_epoch=epoch),
        lambda:bound.reject_bound_reentry(core)):
        with pytest.raises(h7.ParentChildUnsupported):action()
    assert core.event_store.writer_epoch==epoch and core.event_store.max_ordinal()==head
    cold=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    assert bound.assert_bound_integrity(cold)['child_run_ref']==_ref_payload(child.identity.run_ref)


def test_child_direct_closed_authority_cannot_revive(tmp_path,monkeypatch):
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import current_run_execution_authority
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef,TypedRelation
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    child.record_owner_stop(idempotency_key='stop')
    core=child._core;prior,body=current_run_execution_authority(core,_ResourceServiceKernel(core))
    ref=VersionRef(prior.entity_type,prior.entity_id,new_id('run_execution_authority_version'))
    value={**body,'run_execution_authority_ref':_ref_payload(ref),'status':'running'}
    tx=core.begin(idempotency_key='direct:revive')
    tx.prewrite(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,
        payload=canonical_json(value),metadata=value,media_type='application/json',schema_ref='registry_v1/'+ref.entity_type)
    tx.relate(TypedRelation(new_id('relation'),'derived_from',ref,prior),system_owned=True)
    head=core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported):tx.commit()
    assert core.event_store.max_ordinal()==head


def test_child_origin_cannot_be_cloned_by_raw_transaction(tmp_path,monkeypatch):
    from cpn.rpnh.registry.identities import new_id
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    core=child._core
    row=next(r for r in core.event_store.object_rows_by_type('petri_token/v1') if json.loads(r['metadata_json'])['place']=='rpnhOrigin')
    value=json.loads(row['metadata_json']);ref=replace(_version_from_payload(value['petri_token_ref']),entity_id=new_id('petri_token'),version_id=new_id('petri_token_version'))
    value['petri_token_ref']=_ref_payload(ref);value['token_id']=999
    before=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        core.publish_bytes(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,
            payload=canonical_json(value),metadata=value,media_type='application/json',schema_ref='registry_v1/'+ref.entity_type,idempotency_key='clone:origin')
    assert core.event_store.max_ordinal()==before


def test_child_malformed_origin_wire_rejected_before_adoption(tmp_path,monkeypatch):
    from cpn.rpnh.petri_contracts import DeclarationError
    with pytest.raises((RegistryConflict,DeclarationError,ValueError)):
        bound_owner(tmp_path/'child',monkeypatch,missing_arc=True)


def test_child_initial_adoption_replay_and_successor_rejection(tmp_path,monkeypatch):
    from cpn.rpnh.registry.identities import new_id
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    core=child._core;net=child.publication.net_ref;head=core.event_store.max_ordinal()
    core.task_control.adopt_net(net_instance_ref=net,idempotency_key='child:fresh:initial-adopt')
    assert core.event_store.max_ordinal()==head
    with pytest.raises(RegistryConflict):
        core.task_control.adopt_net(net_instance_ref=net,supersedes_net_ref=net,idempotency_key='new:adopt')
    assert core.event_store.max_ordinal()==head


def test_protocol_marker_pair_required_and_bound_schema_not_public(tmp_path):
    from cpn.rpnh.registry.bootstrap import _bootstrap_identity,NativeBootstrapManifest
    core=_RegistryCore(tmp_path/'unwired',create=True,catalog=catalog())
    before=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        _bootstrap_identity(core,NativeBootstrapManifest((h7.BOUND_PROTOCOL,)))
    assert core.event_store.max_ordinal()==before
    assert not core.event_store.object_rows_by_type('native_run_identity/v1')


def test_child_first_start_and_admission_replay_after_durable_stop_rejected(tmp_path,monkeypatch):
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=child.admit('step.run',logical_tau=0,command_id='admit')
    child.record_owner_stop(idempotency_key='stop')
    head=child._core.event_store.max_ordinal()
    with pytest.raises(StaleInvocationContext,match='stopped'):
        child.start(admitted,command_id='late:start')
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    from test_static_lease_reads import direct_claim
    with pytest.raises((RegistryConflict,ValueError)):
        InvocationLifecycle(child._core).admit_firing(direct_claim(child),idempotency_key='late:direct-admit')
    assert child._core.event_store.max_ordinal()==head


def test_child_started_completion_keeps_draining_without_durable_stop(tmp_path,monkeypatch):
    from threading import Event
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=child.admit('step.run',logical_tau=0,command_id='admit')
    execution=child.start(admitted,command_id='start')
    # A local negative request is not a durable authority rewrite. No native
    # SIGINT/owner-loop timing is certified by this deterministic control.
    requested=Event();requested.set()
    outputs=child.products(execution,outcome_id='complete',products={'step.result':(canonical_json('done'),)},command_id='products')
    child.succeed(outputs,command_id='success')
    assert requested.is_set()
    assert len(child._core.event_store.list_events_by_type(('transition_firing_settled/v1',)))==1


def test_generic_raw_transaction_cannot_mint_protected_capability(tmp_path,monkeypatch):
    from cpn.rpnh.registry.identities import new_id
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    core=child._core
    origin=json.loads(core.event_store.object_rows_by_type(h7.BOUND_KINDS[1])[0]['metadata_json'])
    old=core.get_version(_version_from_payload(origin['capability_ref']).version_id)
    ref=replace(_version_from_payload(origin['capability_ref']),entity_id=new_id('resource'),version_id=new_id('resource_version'))
    metadata={**old.metadata,'resource_id':str(ref.entity_id),'resource_version_id':str(ref.version_id)}
    head=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        core.publish_bytes(object_type='resource_version/v1',logical_id=ref.entity_id,version_id=ref.version_id,
            payload=core.object_store.read_verified(old),metadata=metadata,media_type=old.media_type,
            schema_ref=old.schema_ref,idempotency_key='generic:mint')
    assert core.event_store.max_ordinal()==head


def test_bound_registered_completion_then_one_success_without_remint(tmp_path,monkeypatch):
    from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
    child,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=child.admit('step.run',logical_tau=0,command_id='admit')
    execution=child.start(admitted,command_id='start')
    outputs=child.products(execution,outcome_id='complete',products={'step.result':(canonical_json('done'),)},command_id='products')
    kernel,repository=child.operation_repository()
    record_registered_operation_completion(child._core,kernel,repository,outputs,idempotency_key='completion')
    # Same original writer: replay the retained registered completion, not HOST.
    first=child.succeed(outputs,command_id='success')
    with pytest.raises(StaleInvocationContext):
        child.succeed(outputs,command_id='success')
    assert len(child._core.event_store.list_events_by_type(('transition_firing_settled/v1',)))==1
    assert len([r for r in child._core.event_store.object_rows_by_type('petri_token/v1')
        if json.loads(r['metadata_json'])['place']=='rpnhOrigin'])==1


@pytest.mark.parametrize('kind',h7.BOUND_KINDS)
def test_protected_weak_edges_rejected_before_commit(tmp_path,monkeypatch,kind):
    from cpn.rpnh.registry.transaction import RegistryTransaction
    original=RegistryTransaction.relate
    def weak(self,relation,**kwargs):
        if relation.source.entity_type==kind:
            relation=replace(relation,strength='weak')
        return original(self,relation,**kwargs)
    monkeypatch.setattr(RegistryTransaction,'relate',weak)
    with pytest.raises(RegistryConflict,match='strong'):
        bound_owner(tmp_path/'child',monkeypatch)
    import sqlite3
    with sqlite3.connect(tmp_path/'child'/'.registry_v1'/'registry.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM objects WHERE object_type=?',(kind,)).fetchone()[0]==0
