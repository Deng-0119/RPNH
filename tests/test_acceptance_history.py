"""Original temporary Registry D0, with explicitly injected offline native facts."""
from dataclasses import replace, asdict
import hashlib
import json
import pytest

from parent_child_fixtures import accepted_parent, parent_owner, phase, h7
from cpn.rpnh.registry.acceptance_history import ChildAcceptanceHistoryAssertion, VALID, INVALID, UNAVAILABLE
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload


def assertion_for(owner, acceptance):
    core = owner._core
    row = core.event_store.object_row(acceptance.version_id)
    body = json.loads(row['metadata_json'])
    intent = core.event_store.object_row(TypedId.parse(body['intent_ref']['version_id']))
    with core.event_store.connect() as db:
        fact = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'", (row['transaction_id'],)).fetchone()
        cut = db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (row['transaction_id'],)).fetchone()
    return ChildAcceptanceHistoryAssertion(body['parent']['source_id'],_version_from_payload(body['parent']['binding_ref']),
        _version_from_payload(body['parent']['run_ref']),_version_from_payload(body['parent']['task_ref']),body['slot_id'],acceptance,
        TypedId.parse(row['transaction_id']),TypedId.parse(fact[0]),cut[0],
        h7.digest(core.object_store.read_registered(h7._prepared(row))),
        h7.digest(core.object_store.read_registered(h7._prepared(intent))),json.loads(intent['metadata_json'])['materials']['initial_declaration_digest'])


def test_history_of_live_provisional_acceptance(tmp_path):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent')
    query=assertion_for(owner,acceptance)
    result=owner._core.event_store.classify_child_acceptance_history(query)
    assert result.status==VALID
    assert result.proof.acceptance_ref==acceptance


def database_contents(store):
    with store.connect() as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return [(name, [tuple(r) for r in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid')]) for name in names]


@pytest.mark.parametrize('later',['durable_stop','new_writer'])
def test_real_later_lifecycle_preserves_history_without_live_permission(tmp_path,later):
    owner,admitted,execution,prepared,*rest=accepted_parent(tmp_path/'parent')
    acceptance=rest[-1];core=owner._core;query=assertion_for(owner,acceptance)
    if later=='durable_stop':
        owner.record_owner_stop(idempotency_key='history:stop')
    elif later=='new_writer':
        core.event_store.acquire_writer()
    before=database_contents(core.event_store)
    result=core.event_store.classify_child_acceptance_history(query)
    assert result.status==VALID
    assert result.proof.commit_ordinal==query.commit_ordinal
    assert result.proof.writer_epoch_at_acceptance==execution.admission_head.writer_fencing_epoch
    assert database_contents(core.event_store)==before
    with pytest.raises((RuntimeError,ValueError)):
        h7.register_child_intent(core,execution,prepared)
    assert core.event_store.object_row_for_view(core.event_store.canonical_view(),version_id=acceptance.version_id) is None
    assert not core.event_store.list_events_by_type(('transition_firing_settled/v1',))


@pytest.mark.parametrize('field',['source_id','binding_ref','run_ref','task_ref','slot_id','acceptance_ref',
    'transaction_id','event_id','commit_ordinal','acceptance_sha256','intent_sha256','initial_declaration_digest'])
def test_origin_assertions_do_not_replace_original_evidence(tmp_path,field):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');store=owner._core.event_store
    query=assertion_for(owner,acceptance);value=getattr(query,field)
    if field.endswith('_ref'):
        value=replace(value,version_id=new_id(value.version_id.kind))
    elif field in ('transaction_id','event_id'):
        value=new_id(value.kind)
    elif field=='commit_ordinal':
        value-=1
    elif field.endswith('sha256') or field.endswith('digest'):
        value='f'*64
    else:value='different'
    before=database_contents(store)
    result=store.classify_child_acceptance_history(replace(query,**{field:value}))
    assert result.status in (INVALID,UNAVAILABLE)
    assert result.proof is None
    assert database_contents(store)==before


@pytest.mark.parametrize('value',[{},True,{'accepted':True},{'history_valid':True},{'allowed_action':'fresh_bound_bootstrap_once'}])
def test_copied_json_and_asserted_booleans_are_not_history_authority(tmp_path,value):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent')
    assert owner._core.event_store.classify_child_acceptance_history(value).status==INVALID


def test_valid_history_never_becomes_receipt_native_evidence_or_body_export(tmp_path):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    result=core.event_store.classify_child_acceptance_history(assertion_for(owner,acceptance))
    assert result.status==VALID
    output=json.dumps(asdict(result),default=str)
    for forbidden in ('allowed_action','fresh_bound_bootstrap_once','normalized_request','definition','public_configuration','acceptance_json','intent_json'):
        assert forbidden not in output
    worker=_version_from_payload(core.get_version(acceptance.version_id).metadata['worker_ref'])
    with pytest.raises(h7.ParentChildUnsupported):
        h7._advance_record(core,h7.PARENT_KINDS[3],worker,details={'request_digest':result.proof.request_digest},evidence=result)
    assert core.event_store.object_row_for_view(core.event_store.canonical_view(),version_id=acceptance.version_id) is None


@pytest.mark.parametrize('damage',[
    'transaction_status','transaction_command','transaction_epoch','outbox_order','commit_count','commit_schema',
    'record_schema','record_payload','record_producer','record_command','object_schema','object_storage','object_metadata',
    'member_object_missing','member_event_missing','member_relation_missing','member_transaction_missing','member_wrong_tx',
    'root_net','root_opening','relation_weak','relation_source','relation_target','relation_producer',
    'start_payload','start_producer','source_metadata','current_stream_head','at_cut_authority','later_slot_conflict',
])
def test_original_registry_damage_fails_closed(tmp_path,damage):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core;store=core.event_store
    query=assertion_for(owner,acceptance);body=core.get_version(acceptance.version_id).metadata
    tx=str(query.transaction_id);firing=body['execution']['firing_ref']['version_id']
    with store.connect() as db:
        relation=db.execute('SELECT relation_id,published_event_id FROM relations WHERE transaction_id=? ORDER BY rowid',(tx,)).fetchone()
        event=str(query.event_id)
        if damage=='transaction_status':db.execute("UPDATE transactions SET status='aborted' WHERE transaction_id=?",(tx,))
        elif damage=='transaction_command':db.execute("UPDATE transactions SET command_json='{}' WHERE transaction_id=?",(tx,))
        elif damage=='transaction_epoch':db.execute('UPDATE transactions SET writer_epoch=writer_epoch+1 WHERE transaction_id=?',(tx,))
        elif damage=='outbox_order':db.execute("UPDATE outbox SET event_ids_json='[]' WHERE transaction_id=?",(tx,))
        elif damage=='commit_count':db.execute("UPDATE events SET payload_json=json_set(payload_json,'$.object_count',2) WHERE transaction_id=? AND event_type='transaction_committed/v1'",(tx,))
        elif damage=='commit_schema':db.execute("UPDATE events SET payload_schema_ref='registry_v1/parent_child_recorded/v1' WHERE transaction_id=? AND event_type='transaction_committed/v1'",(tx,))
        elif damage=='record_schema':db.execute("UPDATE events SET payload_schema_ref='wrong/v1' WHERE event_id=?",(event,))
        elif damage=='record_payload':db.execute("UPDATE events SET payload_json=json_set(payload_json,'$.record_ref.logical_id',?) WHERE event_id=?",(str(new_id('resource')),event))
        elif damage=='record_producer':db.execute('UPDATE events SET producer_invocation_id=NULL WHERE event_id=?',(event,))
        elif damage=='record_command':db.execute("UPDATE events SET command_id='copied' WHERE event_id=?",(event,))
        elif damage=='object_schema':db.execute("UPDATE objects SET schema_ref='wrong/v1' WHERE version_id=?",(str(acceptance.version_id),))
        elif damage=='object_storage':db.execute("UPDATE objects SET storage_locator='foreign/body.json' WHERE version_id=?",(str(acceptance.version_id),))
        elif damage=='object_metadata':db.execute("UPDATE objects SET metadata_json=json_set(metadata_json,'$.execution.execution_generation',1) WHERE version_id=?",(str(acceptance.version_id),))
        elif damage.startswith('member_'):
            kind=damage.split('_')[1]
            if damage=='member_wrong_tx':db.execute('UPDATE firing_temporary_members SET transaction_id=? WHERE member_identity=?',(db.execute('SELECT opened_transaction_id FROM firing_publications WHERE firing_version_id=?',(firing,)).fetchone()[0],str(acceptance.version_id)))
            else:db.execute('DELETE FROM firing_temporary_members WHERE member_kind=? AND transaction_id=?',(kind,tx))
        elif damage=='root_net':db.execute("UPDATE firing_publications SET net_version_id='foreign' WHERE firing_version_id=?",(firing,))
        elif damage=='root_opening':db.execute('UPDATE firing_publications SET opened_transaction_id=? WHERE firing_version_id=?',(tx,firing))
        elif damage=='relation_weak':db.execute("UPDATE relations SET strength='weak' WHERE relation_id=?",(relation[0],))
        elif damage=='relation_source':db.execute("UPDATE relations SET source_json=json_set(source_json,'$.entity_id',?) WHERE relation_id=?",(str(new_id('resource')),relation[0]))
        elif damage=='relation_target':db.execute("UPDATE relations SET target_json=json_set(target_json,'$.entity_id',?) WHERE relation_id=?",(str(new_id('invocation')),relation[0]))
        elif damage=='relation_producer':db.execute('UPDATE events SET producer_invocation_id=NULL WHERE event_id=?',(relation[1],))
        elif damage=='start_payload':db.execute("UPDATE events SET payload_json=json_set(payload_json,'$.admission_registry_ordinal',0) WHERE event_id=?",(body['execution']['start_event_id'],))
        elif damage=='start_producer':db.execute('UPDATE events SET producer_invocation_id=NULL WHERE event_id=?',(body['execution']['start_event_id'],))
        elif damage=='source_metadata':db.execute("UPDATE objects SET metadata_json=json_set(metadata_json,'$.source_id','copied-source') WHERE object_type='collaboration_source_binding/v1'")
        elif damage=='current_stream_head':db.execute('UPDATE stream_heads SET sequence=sequence+1 WHERE stream_id=?',('h7:'+str(acceptance.entity_id),))
        elif damage=='at_cut_authority':db.execute("UPDATE objects SET metadata_json=json_set(metadata_json,'$.status','stopped_by_owner') WHERE version_id=?",(body['execution']['run_authority_ref']['version_id'],))
        else:
            db.execute('INSERT INTO objects SELECT logical_id,?,object_type,size,media_type,schema_ref,producer_invocation_id,storage_locator,metadata_json,transaction_id,published_event_id FROM objects WHERE version_id=?',(str(new_id('resource_version')),str(acceptance.version_id)))
    result=store.classify_child_acceptance_history(query)
    assert result.status!=VALID
    assert result.proof is None


@pytest.mark.parametrize('which',['acceptance','intent','request'])
def test_original_immutable_bytes_are_required(tmp_path,which):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance);body=core.get_version(acceptance.version_id).metadata
    ref=acceptance if which=='acceptance' else _version_from_payload(body['intent_ref'])
    if which=='request':ref=_version_from_payload(core.get_version(ref.version_id).metadata['request_ref'])
    path=core.object_store.path_for_version(ref.version_id)
    raw=path.read_bytes();path.write_bytes(b'?' + raw[1:])
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


def test_fresh_read_does_not_trust_prior_reader_memo(tmp_path):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance)
    assert core.event_store.classify_child_acceptance_history(query).status==VALID
    with core.event_store.connect() as db:
        db.execute("DELETE FROM firing_temporary_members WHERE member_kind='object' AND member_identity=?",(str(acceptance.version_id),))
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


def test_generic_terminal_ready_is_not_a_fake_reachable_closed_h7_path(tmp_path):
    from cpn.rpnh.registry.invocations import InvocationLifecycle, TerminalResultPackage
    owner,admitted,execution,prepared,*rest=accepted_parent(tmp_path/'parent')
    acceptance=rest[-1];core=owner._core;query=assertion_for(owner,acceptance)
    outputs=owner.products(execution,outcome_id='complete',products={'step.result':(b'"no native completion"',)},command_id='history:products')
    before=database_contents(core.event_store)
    with pytest.raises(TypeError,match='exact version reference'):
        InvocationLifecycle(core).mark_operation_terminal_ready(execution.operation.canonical.context,
            TerminalResultPackage('completed',tuple(o.resource_ref.as_version_ref() for o in outputs.outputs)),
            idempotency_key='history:ready')
    assert database_contents(core.event_store)==before
    assert not core.event_store.list_events_by_type(('operation_terminal_ready/v1',))
    assert core.event_store.classify_child_acceptance_history(query).status==VALID


@pytest.mark.parametrize('event_type',['object_version_published/v1','relation_published/v1','transaction_committed/v1'])
@pytest.mark.parametrize('field',['command_id','idempotency_key','correlation_id','causation_event_id','parent_event_ids_json','producer_principal'])
def test_every_h7_persisted_event_has_its_original_fixed_contract(tmp_path,event_type,field):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance)
    value='corrupted'
    if field=='causation_event_id':value=str(query.event_id)
    if field=='parent_event_ids_json':value=json.dumps([str(query.event_id)])
    with core.event_store.connect() as db:
        db.execute('UPDATE events SET '+field+'=? WHERE transaction_id=? AND event_type=?',(value,str(query.transaction_id),event_type))
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


@pytest.mark.parametrize('damage',['outbox','commit_count','schema','member','current_head'])
def test_original_start_transaction_cannot_be_replaced_by_payload_assertions(tmp_path,damage):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core;query=assertion_for(owner,acceptance)
    body=core.get_version(acceptance.version_id).metadata
    with core.event_store.connect() as db:
        start=db.execute('SELECT * FROM events WHERE event_id=?',(body['execution']['start_event_id'],)).fetchone()
        tx=start['transaction_id']
        if damage=='outbox':db.execute("UPDATE outbox SET event_ids_json='[]' WHERE transaction_id=?",(tx,))
        elif damage=='commit_count':db.execute("UPDATE events SET payload_json=json_set(payload_json,'$.fact_count',99) WHERE transaction_id=? AND event_type='transaction_committed/v1'",(tx,))
        elif damage=='schema':db.execute("UPDATE events SET payload_schema_ref='wrong/v1' WHERE event_id=?",(start['event_id'],))
        elif damage=='member':db.execute("DELETE FROM firing_temporary_members WHERE member_kind='event' AND member_identity=?",(start['event_id'],))
        else:db.execute('UPDATE stream_heads SET sequence=sequence+1 WHERE stream_id=?',(start['stream_id'],))
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


@pytest.mark.parametrize('kind',['object','event','relation','transaction'])
def test_historical_member_inventory_rejects_orphan_original_rows(tmp_path,kind):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance);body=core.get_version(acceptance.version_id).metadata
    with core.event_store.connect() as db:
        db.execute('INSERT INTO firing_temporary_members VALUES (?,?,?,?)',
            (body['execution']['firing_ref']['version_id'],kind,'orphan-original-member',str(query.transaction_id)))
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


@pytest.mark.parametrize('field',['command_id','idempotency_key','correlation_id','causation_event_id',
    'parent_event_ids_json','stream_id','aggregate_type','producer_principal','task_control_sequence'])
def test_original_start_fixed_envelope_is_not_inferred_from_payload(tmp_path,field):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance);body=core.get_version(acceptance.version_id).metadata
    value='corrupted-start'
    if field=='causation_event_id':value=str(query.event_id)
    elif field=='parent_event_ids_json':value=json.dumps([str(query.event_id)])
    elif field=='task_control_sequence':value=None
    with core.event_store.connect() as db:
        db.execute('UPDATE events SET '+field+'=? WHERE event_id=?',(value,body['execution']['start_event_id']))
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


def test_broken_sqlite_source_is_fail_closed_without_uncaught_exception(tmp_path):
    from cpn.rpnh.registry.event_store import EventStore
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');store=owner._core.event_store
    query=assertion_for(owner,acceptance)
    broken=tmp_path/'broken.sqlite3';broken.write_bytes(b'not SQLite'+b'\x00'*512)
    result=EventStore(broken,store.catalog,read_only=True).classify_child_acceptance_history(query)
    assert result.status==INVALID and result.proof is None


@pytest.mark.parametrize('field',['public_configuration','definition'])
@pytest.mark.parametrize('container',['direct','list','map'])
@pytest.mark.parametrize('shape',['version','resource'])
def test_opaque_business_reference_shapes_do_not_create_authority(tmp_path,monkeypatch,field,container,shape):
    import parent_child_fixtures as fixture
    value=({'entity_type':'task/v1','logical_id':str(new_id('task')),'version_id':str(new_id('task_version'))}
        if shape=='version' else {'resource_id':str(new_id('resource')),'resource_version_id':str(new_id('resource_version'))})
    opaque=value if container=='direct' else ([{'nested':value}] if container=='list' else {'nested':{'example':value}})
    def with_business_data(body):
        # Preserve valid Module/public-selection shapes. These designated
        # business data containers remain opaque to authority graph traversal.
        if field=='definition':
            body[field]['designer_constraints']['opaque_example']=opaque
        else:
            body[field]['configuration']['opaque_example']=opaque
        return body
    owner,*_,acceptance=fixture.accepted_parent(tmp_path/'parent',request_transform=with_business_data);core=owner._core
    query=assertion_for(owner,acceptance)
    body=core.get_version(acceptance.version_id).metadata
    intent=core.get_version(_version_from_payload(body['intent_ref']).version_id).metadata
    nested='designer_constraints' if field=='definition' else 'configuration'
    assert intent['request'][field][nested]['opaque_example']==opaque
    before=database_contents(core.event_store)
    assert core.event_store.classify_child_acceptance_history(query).status==VALID
    assert database_contents(core.event_store)==before


@pytest.mark.parametrize('field',['claimed_input_refs','content_schema_authority_ref'])
def test_schema_declared_real_authority_refs_still_require_original_bytes(tmp_path,field):
    owner,*_,acceptance=accepted_parent(tmp_path/'parent');core=owner._core
    query=assertion_for(owner,acceptance);body=core.get_version(acceptance.version_id).metadata
    if field=='claimed_input_refs':
        firing=core.get_version(_version_from_payload(body['execution']['firing_ref']).version_id).metadata
        ref=firing[field][0]
    else:
        intent=core.get_version(_version_from_payload(body['intent_ref']).version_id).metadata
        request=core.get_version(_version_from_payload(intent['request_ref']).version_id).metadata
        ref=request[field]
    core.object_store.path_for_version(TypedId.parse(ref['version_id'])).unlink()
    assert core.event_store.classify_child_acceptance_history(query).status==INVALID


def test_schema_declared_array_and_map_refs_are_followed_but_free_data_is_not():
    from cpn.rpnh.registry.acceptance_history import _HistoryReads
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    reads=object.__new__(_HistoryReads);reads.pending_objects=[]
    schema=SchemaCatalog()._validator('registry_v1/net_instance/v1').schema
    exact={'entity_type':'task/v1','logical_id':str(new_id('task')),'version_id':str(new_id('task_version'))}
    resource={'resource_id':str(new_id('resource')),'resource_version_id':str(new_id('resource_version'))}
    reads._refs({'node_refs':[exact],'module_resource_bindings':{'owner_resource_inputs':{'request':resource}}},schema,schema)
    assert exact in reads.pending_objects
    assert {'entity_type':'resource_version/v1','logical_id':resource['resource_id'],'version_id':resource['resource_version_id']} in reads.pending_objects
    reads.pending_objects=[]
    reads._refs({'any':[exact,{'nested':resource}]},{'type':'object'}, {'type':'object'})
    assert reads.pending_objects==[]
