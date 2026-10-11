"""Original owner adoption and stock TEXT delivery; all execution offline."""
from copy import deepcopy
import json
import uuid

import pytest

import test_terminal_owner_adoption as native
from test_user_collaboration_authority import personal, join, proposal, decision, advance, catalog
from cpn.rpnh.collaboration import UserCollaborationHost, ClosedModuleAuthor, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.user_collaboration import RECEIPT
from cpn.rpnh.registry._event_store.branch_publication import branch_command_key


def world_for_owner(owner, tmp_path):
    core=owner._core;gateway=owner.schema_gateway
    gateway.bind_source_identity(source_id='plain-source',command_id='source')
    host=UserCollaborationHost(core,gateway)
    a=host.create_session(origin_core=core,principal_ref=owner.principal_ref,command_id='A')
    bcore,bp=personal(tmp_path/'personal-B','B-origin')
    b=host.create_session(origin_core=bcore,principal_ref=bp,command_id='B')
    author=ClosedModuleAuthor(gateway,owner.registration,SourceQualifiedVersionRef('plain-source',owner.principal_ref))
    module=native.current(owner)[1].compiled.source
    ids={path:'element:'+uuid.uuid5(uuid.NAMESPACE_URL,path).hex for path in _elements(module)}
    base=author.publish(module=module,element_ids=ids,command_id='base')
    branch=gateway.create_author_branch(head_revision_ref=base.revision.revision_ref,command_id='official')
    project=host.protect(a,branch_ref=branch.branch_ref,runtime_net_ref=native.current(owner)[0].net_ref,command_id='protect')
    return dict(core=core,gateway=gateway,host=host,a=a,b=b,bcore=bcore,bp=bp,base=base,
        branch=branch,project=project,fixture=(core,gateway,owner.registration,author,None,base,ids))


@pytest.fixture
def runtime_world(tmp_path,monkeypatch):
    original=native.start_run
    monkeypatch.setattr(native,'start_run',lambda *args,**kwargs: original(*args,**kwargs,catalog=catalog()))
    owner=native.make_owner(tmp_path)
    return owner,world_for_owner(owner,tmp_path)


def test_accepted_author_leave_allows_original_owner_adoption_and_early_replay(runtime_world):
    from cpn.rpnh.net_operations import prepare_replacement
    owner,w=runtime_world
    doc=w['base'].module.to_dict();doc['name']='AcceptedOwnerFixture'
    join(w);p,c,_=proposal(w,document=doc);d,r=decision(w,p,c)
    accepted=advance(w,c,w['host'].action(w['a'],d))
    receipt=w['host'].receipt_ref(branch_command_key('advance'))
    w['host'].review(w['a'],p,disposition='request_changes',reason='Future follow-up does not undo accepted history',command_id='follow-up-review')
    w['host'].revoke(w['a'],w['project'],subject_session_ref=w['b'].ref,command_id='author-leave')
    plan=prepare_replacement(owner,c.module)
    adopted=w['host'].decide(w['a'],w['project'],operation='adopt',proposal_refs=(p,),review_refs=(r,),
        result_revision_ref=c.revision.revision_ref,expected_branch_ref=accepted.branch_ref,
        expected_head_ref=accepted.head_revision_ref,expected_runtime_ref=plan.base_net_ref,
        accepted_receipt_ref=receipt,command_id='adopt-decision')
    action=w['host'].action(w['a'],adopted)
    result=owner.control.edits.submit(plan.owner_command(),'owner-adopt',collaboration_action=action)
    assert result['status']=='ADOPTED'
    assert len(w['core'].event_store.object_rows_by_type(RECEIPT))==2
    # This executes the original OwnerEdits.submit early-history return.
    count=w['core'].event_store.max_ordinal()
    replay=owner.control.edits.submit(plan.owner_command(),'owner-adopt',collaboration_action=action)
    assert replay['status']=='ADOPTED' and w['core'].event_store.max_ordinal()==count
    with pytest.raises(RegistryConflict):
        owner.control.edits.submit(plan.owner_command(),'owner-adopt')
    # Historical native witness readers must not consult current membership.
    assert native.current(owner)[0].net_ref != plan.base_net_ref


@pytest.mark.parametrize('route',['submit','correct','retract','ledger'])
def test_original_owner_routes_reject_missing_envelope(runtime_world,route):
    from cpn.rpnh.net_operations import prepare_replacement
    owner,w=runtime_world
    plan=prepare_replacement(owner,w['base'].module)
    with pytest.raises(RegistryConflict):
        if route=='submit':owner.control.edits.submit(plan.owner_command(),'raw-submit')
        elif route=='correct':owner.control.edits.correct({'edit_command_id':'missing'},'raw-correct')
        elif route=='retract':owner.control.edits.retract({'edit_command_id':'missing'},'raw-retract')
        else:
            # A new exact adoption attempt cannot use net_instance_id=None to
            # escape protection of the original Task/runtime lineage.
            owner._core.task_control.adopt_net(net_instance_ref=plan.base_net_ref,idempotency_key='raw-ledger')


def test_original_successful_stock_text_is_delivered_with_distinct_copy_publisher(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import TEXT_SCHEMA, TEXT_SCHEMA_DOCUMENT
    from test_static_lease_reads import _registration,_simple_module,TEXT
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.user_collaboration_source import completed_text,strict_proposal_json
    original=native.start_run
    monkeypatch.setattr(native,'start_run',lambda *args,**kwargs: original(*args,**kwargs,catalog=catalog()))
    target=native.make_owner(tmp_path/'target')
    w=world_for_owner(target,tmp_path/'target')
    reg=_registration();reg.register_schema(TEXT_SCHEMA,TEXT_SCHEMA_DOCUMENT)
    source_doc=_simple_module('Source').to_dict()
    source_doc=json.loads(json.dumps(source_doc).replace(TEXT,TEXT_SCHEMA))
    # Keep original fixture OwnerInput schema aligned with the source module.
    monkeypatch.setattr(native,'TEXT',TEXT_SCHEMA)
    source=native.make_owner(tmp_path/'source',ModuleDeclaration.from_dict(source_doc),reg)
    source.schema_gateway.bind_source_identity(source_id='source-personal',command_id='source')
    session=w['host'].create_session(origin_core=source._core,principal_ref=source.principal_ref,command_id='native-author')
    payload=canonical_json(w['base'].module.to_dict())
    execution=native.begin(source)
    product=source.products(execution,outcome_id='complete',products={'step.result':(canonical_json(payload.decode('utf-8')),)},command_id='source:products')
    source.succeed(product,command_id='source:succeed')
    assert source.terminal() is not None
    raw,decoded,witness=completed_text(source._core)
    assert decoded==payload and strict_proposal_json(decoded)==w['base'].module.to_dict()
    delivery=w['host'].ingest_completed_text(session,origin_core=source._core,command_id='native-delivery')
    body=w['host'].read_fact(w['a'],delivery)
    assert body['data']['witness']['invocation_ref']==witness['invocation_ref']
    copy=w['core'].get_version(body['data']['received_text_ref']['version_id'])
    assert copy.metadata['producer_ref']!=witness['invocation_ref']
    assert w['core'].object_store.read_registered(copy)==raw
    with pytest.raises(RegistryConflict):
        w['host'].ingest_completed_text(w['b'],origin_core=source._core,command_id='wrong-user')


def test_two_original_operation_texts_resume_same_actor_before_project_terminal(tmp_path,monkeypatch):
    from cpn.rpnh.agent_tasks import TEXT_SCHEMA,TEXT_SCHEMA_DOCUMENT
    from test_static_lease_reads import _registration,_simple_module,TEXT
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.user_collaboration_source import completed_text
    original=native.start_run
    monkeypatch.setattr(native,'start_run',lambda *args,**kwargs: original(*args,**kwargs,catalog=catalog()))
    target=native.make_owner(tmp_path/'target');w=world_for_owner(target,tmp_path/'target')
    reg=_registration();reg.register_schema(TEXT_SCHEMA,TEXT_SCHEMA_DOCUMENT)
    doc=json.loads(json.dumps(_simple_module('ContinuousSource').to_dict()).replace(TEXT,TEXT_SCHEMA))
    finish=deepcopy(doc['components'][0]);finish['name']='finish';doc['components'].append(finish)
    doc['links']=[{'source':{'component':'step','port':'result'},'target':{'component':'finish','port':'request'}}]
    doc['exit']['result']['component']='finish';doc['terminal']['source']['component']='finish'
    monkeypatch.setattr(native,'TEXT',TEXT_SCHEMA)
    source=native.make_owner(tmp_path/'source',ModuleDeclaration.from_dict(doc),reg)
    source.schema_gateway.bind_source_identity(source_id='continuous-personal',command_id='source')
    # A distinct new-run owner can be bound before invocation without changing
    # the stable original personal actor. No model or new external account.
    launch=w['host'].bind_execution(w['b'],origin_core=w['bcore'],execution_core=source._core,command_id='launch')
    payload=canonical_json(w['base'].module.to_dict())
    first=native.begin(source,key='first')
    produced=source.products(first,outcome_id='complete',products={'step.result':(canonical_json(payload.decode()),)},command_id='first:products')
    source.succeed(produced,command_id='first:succeed')
    assert source.terminal() is None
    firing=first.operation.firing.transition_firing_ref
    with pytest.raises(RegistryConflict,match='terminal'):completed_text(source._core)
    received=w['host'].ingest_completed_text(w['b'],origin_core=source._core,
        execution_ref=launch,firing_ref=firing,command_id='operation-one')
    witness=w['host'].read_fact(w['a'],received)['data']['witness']
    assert witness['terminal_ref'] is None and witness['completion_kind']=='operation_success'
    join(w)
    candidate=w['fixture'][3].publish(module=w['base'].module,element_ids=w['fixture'][-1],
        parent_ref=w['base'].revision.revision_ref,command_id='received-candidate')
    proposal_args=dict(payload=w['base'].module.to_dict(),base_revision_ref=w['branch'].head_revision_ref,
        expected_branch_ref=w['branch'].branch_ref,expected_head_ref=w['branch'].head_revision_ref,
        candidate_revision_ref=candidate.revision.revision_ref)
    p1=w['host'].submit_proposal(w['b'],w['project'],command_id='received-P1',
        **proposal_args,**w['host'].delivered_proposal_inputs(w['b'],received))
    assert w['host'].read_fact(w['a'],p1)['data']['synthetic_fixture'] is False
    resumed=w['host'].resume_session(w['b'].ref,origin_core=w['bcore'],principal_ref=w['bp'])
    second=native.begin(source,key='second',transition='finish.run')
    produced=source.products(second,outcome_id='complete',products={'finish.result':(canonical_json(payload.decode()),)},command_id='second:products')
    source.succeed(produced,command_id='second:succeed')
    received2=w['host'].ingest_completed_text(resumed,origin_core=source._core,execution_ref=launch,
        firing_ref=second.operation.firing.transition_firing_ref,command_id='operation-two')
    next_witness=w['host'].read_fact(w['a'],received2)['data']['witness']
    assert resumed.actor==w['b'].actor and next_witness['invocation_ref']!=witness['invocation_ref']
    p2=w['host'].submit_proposal(resumed,w['project'],command_id='received-P2',previous_proposal_ref=p1,
        **proposal_args,**w['host'].delivered_proposal_inputs(resumed,received2))
    assert w['host'].read_fact(w['a'],p2)['data']['previous_proposal_ref']==w['host']._get(p1)['self_ref']
    with pytest.raises(RegistryConflict,match='retrospectively'):
        w['host'].bind_execution(w['b'],origin_core=w['bcore'],execution_core=source._core,command_id='late-launch')


def test_actual_ordinary_module_success_cannot_bypass_protection(tmp_path,monkeypatch):
    import cpn.rpnh.run as run_module
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data,user_collaboration_schema_data
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    from test_local_takeover_ordinary_revision import test_ordinary_revision_success_has_legacy_unmarked_delta
    original=run_module.start_run
    captured=[]
    def protected_start(*args,**kwargs):
        schemas,types,paths=normal_child_root_schema_data()
        added,extra,locations=user_collaboration_schema_data()
        schemas.update(added);paths.update(locations)
        kwargs['catalog']=SchemaCatalog(schemas=schemas,types=tuple({d.name:d for d in (*types,*extra)}.values()),schema_paths=paths)
        owner=original(*args,**kwargs)
        world_for_owner(owner,tmp_path/'control')
        captured.append(owner)
        return owner
    monkeypatch.setattr(run_module,'start_run',protected_start)
    # Invoke the original in-process native Start/products/structural Success
    # fixture. The refusal must be the central protected-target boundary.
    with pytest.raises(RegistryConflict,match='trusted HOST'):
        test_ordinary_revision_success_has_legacy_unmarked_delta(tmp_path)
    assert len(captured)==1
    assert len(captured[0]._core.event_store.list_events_by_type(('net_adopted/v1',)))==1


def test_pending_owner_edit_retains_original_action_when_later_context_changes(runtime_world):
    from cpn.rpnh.net_operations import prepare_replacement
    from cpn.rpnh.registry.strict_contracts import ref_payload
    owner,w=runtime_world;h=w['host']
    doc=w['base'].module.to_dict();doc['name']='PendingOwnerFixture'
    join(w);p,c,_=proposal(w,document=doc);d,r=decision(w,p,c)
    branch=advance(w,c,h.action(w['a'],d))
    first=native.begin(owner)
    plan=prepare_replacement(owner,c.module)
    args=dict(operation='adopt',proposal_refs=(p,),review_refs=(r,),result_revision_ref=c.revision.revision_ref,
        expected_branch_ref=branch.branch_ref,expected_head_ref=branch.head_revision_ref,
        expected_runtime_ref=plan.base_net_ref,accepted_receipt_ref=h.receipt_ref(branch_command_key('advance')))
    original=h.decide(w['a'],w['project'],command_id='original-adopt',**args)
    later=h.decide(w['a'],w['project'],command_id='later-adopt',**args)
    result=owner.control.edits.submit(plan.owner_command(),'pending-adopt',collaboration_action=h.action(w['a'],original))
    assert result['status']=='DRAINING'
    persisted=[v for _,v in owner.control.records('candidate') if v['command_id']=='pending-adopt']
    assert persisted[0]['data']['user_collaboration']['action_ref']==ref_payload(original)
    with h.command(h.action(w['a'],later),'pending-adopt'):
        with pytest.raises(RegistryConflict,match='different later HOST action'):
            native.settle(owner,first)
    assert native.current(owner)[0].net_ref==plan.base_net_ref
    assert len(w['core'].event_store.object_rows_by_type(RECEIPT))==1
    # The ordinary Success already settled; continue the retained original edit
    # without repeating a firing or relabelling its submitted authority.
    result=owner.control.edits.advance()
    assert result['status']=='ADOPTED'
    receipt=h.read_fact(w['a'],h.receipt_ref('pending-adopt'))
    assert receipt['data']['decision_ref']==ref_payload(original)
