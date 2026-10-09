"""Independent low-level H7 tests on disposable genuine Registries."""
import json
from pathlib import Path
import pytest
from parent_child_fixtures import bound_owner, child_definition, evidence
from cpn.rpnh.registry import parent_child as h7, parent_bound as bound
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import _bootstrap_identity, NativeBootstrapManifest
from cpn.rpnh.registry.event_store import EventStore, RegistryConflict
from cpn.rpnh.registry.errors import StaleInvocationContext, ResourceIdempotencyConflict
from cpn.rpnh.registry.run_authority import current_run_execution_authority
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef, TypedRelation
from cpn.rpnh.registry.publication import _ref_payload, _registry_type_catalog_ref
from cpn.rpnh.registry.schema_catalog import canonical_json


def test_bound_writable_core_reentry_fails_before_eventstore_construction(tmp_path, monkeypatch):
    owner, _ = bound_owner(tmp_path/'child',monkeypatch)
    epoch = owner._core.event_store.writer_epoch
    calls=[]
    original=EventStore.__init__
    def instrument(self,*args,**kwargs):
        calls.append((args,kwargs))
        return original(self,*args,**kwargs)
    monkeypatch.setattr(EventStore,'__init__',instrument)
    with pytest.raises(h7.ParentChildUnsupported):
        _RegistryCore(tmp_path/'child',create=False,catalog=owner._core.catalog)
    assert not calls
    assert owner._core.event_store.writer_epoch==epoch


def test_bound_direct_acquire_writer_cannot_fence_original_owner(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    epoch=owner._core.event_store.writer_epoch
    with pytest.raises(h7.ParentChildUnsupported):
        owner._core.event_store.acquire_writer()
    assert owner._core.event_store.writer_epoch==epoch


def test_bound_readonly_core_preserves_writer_and_exact_origin(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    epoch=owner._core.event_store.writer_epoch
    observer=_RegistryCore(tmp_path/'child',create=False,read_only=True,catalog=owner._core.catalog)
    assert observer.writer_epoch==epoch
    assert owner._core.event_store.writer_epoch==epoch
    assert bound.assert_bound_integrity(observer)['child_run_ref']==_ref_payload(owner.identity.run_ref)


def test_protected_catalog_bridge_is_exact_original_source(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    core=owner._core
    origin=bound.assert_bound_integrity(core)
    cap=core.get_version(origin['capability_ref']['version_id'])
    catalog_ref=_ref_payload(_registry_type_catalog_ref(core))
    assert cap.metadata['content_schema_authority_ref']==catalog_ref
    assert cap.metadata['producer_ref']==_ref_payload(owner.bootstrap_ref)
    assert cap.metadata['lifetime_ref']==_ref_payload(owner.bootstrap_ref)
    root=core.get_version(owner.publication.root_ref.version_id).metadata
    assert catalog_ref in root['resource_refs']
    assert origin['capability_ref'] in root['resource_refs']


def test_direct_same_writer_stopped_to_running_authority_is_rejected(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    core=owner._core
    owner.record_owner_stop(idempotency_key='review:stop-child')
    kernel,_=owner.operation_repository()
    authority_ref,current=current_run_execution_authority(core,kernel)
    assert current['status']=='stopped_by_owner'
    successor=VersionRef('run_execution_authority/v1',authority_ref.entity_id,new_id('run_execution_authority_version'))
    body={**current,'run_execution_authority_ref':_ref_payload(successor),'status':'running'}
    tx=core.begin(idempotency_key='review:raw-child-restart')
    tx.prewrite(object_type=successor.entity_type,logical_id=successor.entity_id,version_id=successor.version_id,
        payload=canonical_json(body),metadata=body,media_type='application/json',schema_ref='registry_v1/'+successor.entity_type)
    tx.relate(TypedRelation(new_id('relation'),'derived_from',successor,authority_ref),system_owned=True)
    before=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        tx.commit()
    assert core.event_store.max_ordinal()==before
    assert current_run_execution_authority(core,kernel)[1]['status']=='stopped_by_owner'


def test_required_bound_protocol_cannot_commit_without_atomic_marker(tmp_path):
    core=_RegistryCore(tmp_path/'unborn',create=True)
    before=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        _bootstrap_identity(core,NativeBootstrapManifest((h7.BOUND_PROTOCOL,)))
    assert core.event_store.max_ordinal()==before
    assert not core.event_store.object_rows_by_type('native_run_identity/v1')


def test_bound_first_business_start_after_durable_stop_is_rejected(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=owner.admit('step.run',logical_tau=0,command_id='review:admit-before-stop')
    assert admitted is not None
    owner.record_owner_stop(idempotency_key='review:stop-before-start')
    before=owner._core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict, StaleInvocationContext), match='stopped|terminal|foreign'):
        owner.start(admitted,command_id='review:start-after-stop')
    assert owner._core.event_store.max_ordinal()==before


def _replace_registered_metadata_for_fault_injection(core, version_id, mutate):
    """Corrupt only this disposable Registry, coherently changing row/publication metadata.

    This is an integrity fault injection, not a supported product writer or a
    claim to protect against hostile same-uid database editors.
    """
    with core.event_store.connect() as db:
        row=db.execute('SELECT * FROM objects WHERE version_id=?',(str(version_id),)).fetchone()
        body=json.loads(row['metadata_json'])
        mutate(body)
        publication=db.execute('SELECT payload_json FROM events WHERE event_id=?',(row['published_event_id'],)).fetchone()
        event=json.loads(publication[0]);event['metadata']=body
        db.execute('UPDATE objects SET metadata_json=? WHERE version_id=?',(canonical_json(body).decode(),str(version_id)))
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(canonical_json(event).decode(),row['published_event_id']))


@pytest.mark.parametrize('field', ['content_schema_authority_ref','producer_ref','lifetime_ref'])
def test_origin_integrity_rejects_catalog_or_producer_source_substitution(tmp_path,monkeypatch,field):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    core=owner._core
    origin=bound.assert_bound_integrity(core)
    replacement=_ref_payload(owner.identity.task_ref)
    _replace_registered_metadata_for_fault_injection(core,origin['capability_ref']['version_id'],
        lambda value:value.update({field:replacement}))
    before=core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict, ValueError)):
        bound.assert_bound_integrity(core)
    assert core.event_store.max_ordinal()==before


def test_direct_second_adoption_transaction_is_rejected(tmp_path,monkeypatch):
    from cpn.rpnh.registry.models import PendingEvent
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    core=owner._core
    event=next(e for e in core.event_store.list_events() if e.event_type=='net_adopted/v1')
    key='review:retained-adoption-reissue'
    tx=core.begin(idempotency_key=key,task_round_id=event.task_round_id,net_instance_id=event.net_instance_id)
    tx.append(PendingEvent(event.event_type,event.criticality,event.stream_id,event.aggregate_id,event.aggregate_type,
        key,key,dict(event.payload),event.payload_schema_ref,task_control=True,producer_principal=event.producer_principal))
    before=core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):tx.commit()
    assert core.event_store.max_ordinal()==before


def test_memory_stop_request_allows_already_started_completion_drain(tmp_path,monkeypatch):
    """D0 tests real Harness flag/settlement with a socket-free wake shim only."""
    from concurrent.futures import Future
    from cpn.rpnh.control_server import OwnerEventLoop
    from cpn.rpnh.harness import Harness, OperationProducts
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=owner.admit('step.run',logical_tau=0,command_id='review:drain-admit')
    execution=owner.start(admitted,command_id='review:drain-start')
    loop=object.__new__(OwnerEventLoop)
    loop.owner=owner
    wakes=[]
    loop.submit_host=lambda callback: wakes.append(callback)
    harness=Harness(owner=owner,event_loop=loop,prepare_dispatcher=lambda *_:None,
                    submit_operation=lambda *_:None)
    harness._pending[execution.operation_execution_lease_ref]=execution
    harness.request_owner_stop()
    assert harness._owner_stop_requested and len(wakes)==1
    kernel,_=owner.operation_repository()
    assert current_run_execution_authority(owner._core,kernel)[1]['status']=='running'
    outputs=owner.products(execution,outcome_id='complete',
        products={'step.result':(canonical_json('drained'),)},command_id='review:drain-products')
    future=Future();future.set_result(OperationProducts(outputs))
    harness._complete(execution,future)
    assert harness._completion_error is None
    assert not harness._pending
    assert len(harness._trace)==1
    assert bound.assert_bound_integrity(owner._core) is not None


@pytest.mark.parametrize('boundary', ['helper','raw-commit'])
def test_bound_admission_replay_after_stop_is_closed_at_original_boundary(tmp_path,monkeypatch,boundary):
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    claims=[];transactions=[]
    admit_original=InvocationLifecycle.admit_firing
    commit_original=EventStore.publish_batch
    def capture_admit(self,claim,*,idempotency_key):
        claims.append((claim,idempotency_key))
        return admit_original(self,claim,idempotency_key=idempotency_key)
    def capture_commit(self,**kwargs):
        if any(e.event_type=='firing_admitted/v1' for e in kwargs['events']):transactions.append(kwargs.copy())
        return commit_original(self,**kwargs)
    monkeypatch.setattr(InvocationLifecycle,'admit_firing',capture_admit)
    monkeypatch.setattr(EventStore,'publish_batch',capture_commit)
    owner.admit('step.run',logical_tau=0,command_id='review:captured-admit')
    monkeypatch.setattr(InvocationLifecycle,'admit_firing',admit_original)
    monkeypatch.setattr(EventStore,'publish_batch',commit_original)
    assert len(claims)==len(transactions)==1
    owner.record_owner_stop(idempotency_key='review:stop-after-admit')
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict,match='stopped|terminal|foreign'):
        if boundary=='helper':
            claim,key=claims[0]
            InvocationLifecycle(owner._core).admit_firing(claim,idempotency_key=key)
        else:owner._core.event_store.publish_batch(**transactions[0])
    assert owner._core.event_store.max_ordinal()==before


@pytest.mark.parametrize('mutation', ['omit-origin','consume-origin'])
def test_bound_admission_replay_cannot_forge_reference_claim(tmp_path,monkeypatch,mutation):
    from dataclasses import replace
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    saved=[]
    original=InvocationLifecycle.admit_firing
    def capture(self,claim,*,idempotency_key):
        saved.append((claim,idempotency_key))
        return original(self,claim,idempotency_key=idempotency_key)
    monkeypatch.setattr(InvocationLifecycle,'admit_firing',capture)
    owner.admit('step.run',logical_tau=0,command_id='review:claim-admit')
    monkeypatch.setattr(InvocationLifecycle,'admit_firing',original)
    claim,key=saved[0]
    references=set(claim.claimed_input_refs)-set(claim.consumed_input_refs)
    assert len(references)==1
    altered=(replace(claim,claimed_input_refs=tuple(ref for ref in claim.claimed_input_refs if ref not in references))
             if mutation=='omit-origin' else replace(claim,consumed_input_refs=claim.claimed_input_refs))
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict,match='origin|reference'):
        InvocationLifecycle(owner._core).admit_firing(altered,idempotency_key=key)
    assert owner._core.event_store.max_ordinal()==before


def test_bound_start_raw_transaction_replay_after_stop_is_closed(tmp_path,monkeypatch):
    owner,_=bound_owner(tmp_path/'child',monkeypatch)
    admitted=owner.admit('step.run',logical_tau=0,command_id='review:start-replay-admit')
    saved=[];original=EventStore.publish_batch
    def capture(self,**kwargs):
        if any(e.event_type=='operation_execution_started/v1' for e in kwargs['events']):saved.append(kwargs.copy())
        return original(self,**kwargs)
    monkeypatch.setattr(EventStore,'publish_batch',capture)
    owner.start(admitted,command_id='review:start-replay')
    monkeypatch.setattr(EventStore,'publish_batch',original)
    assert len(saved)==1
    owner.record_owner_stop(idempotency_key='review:stop-after-start')
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict,match='stopped|terminal|foreign'):
        owner._core.event_store.publish_batch(**saved[0])
    assert owner._core.event_store.max_ordinal()==before


@pytest.mark.parametrize('protected_kind', ['parent_bound_bootstrap/v1','parent_bound_origin/v1'])
def test_prospective_protected_weak_edges_fail_before_commit(tmp_path,monkeypatch,protected_kind):
    """Fault-inject original staging edges while keeping real transaction plumbing."""
    from dataclasses import replace
    from cpn.rpnh.registry.transaction import RegistryTransaction
    original_relate=RegistryTransaction.relate
    original_publish=EventStore.publish_batch
    committed=[]
    def weaken(self,relation,**kwargs):
        if relation.source.entity_type==protected_kind:
            relation=replace(relation,strength='weak')
        return original_relate(self,relation,**kwargs)
    def observe(self,**kwargs):
        result=original_publish(self,**kwargs)
        committed.extend(o.object_type for o in kwargs['objects'] if o.object_type==protected_kind)
        return result
    monkeypatch.setattr(RegistryTransaction,'relate',weaken)
    monkeypatch.setattr(EventStore,'publish_batch',observe)
    with pytest.raises((RegistryConflict,ResourceIdempotencyConflict)):
        bound_owner(tmp_path/'child',monkeypatch)
    assert committed==[], 'malformed protected edges were committed before a later integrity check'
