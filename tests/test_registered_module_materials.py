import json
import pytest
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.public_module_materials import prepare_module_material_draft
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.public_materials import read_inventory
from cpn.rpnh.registry.schema_catalog import canonical_json
from registered_material_fixtures import make_layout,parent_with_request

@pytest.mark.parametrize('story',['numbers','files'])
def test_real_module_registered_inventory_intent(tmp_path,monkeypatch,story):
    setup=make_layout(tmp_path/'layout',monkeypatch,story)
    owner,admitted,execution,old=parent_with_request(tmp_path/'parent',setup.request)
    parent=h7.parent_identity(owner._core)
    draft=prepare_module_material_draft(profile_id=setup.entrypoint.name,parent=parent,request_bytes=canonical_json(setup.request),
        root_binding='test-root-binding',control_root=str(tmp_path/'control'))
    inventory=owner.schema_gateway.publish_public_material_inventory(draft,command_id='materials')
    assert read_inventory(owner._core,inventory.resource_ref)==inventory
    prepared=h7.prepare_registered_child_materials(owner._core,request_ref=old.request_ref,request_bytes=old.request_bytes,
        registered_inventory_ref=inventory.resource_ref,root_binding='test-root-binding',control_root=str(tmp_path/'control'))
    intent=h7.register_child_intent(owner._core,execution,prepared)
    assert intent.entity_type==h7.INTENT_V2
    assert h7.register_child_intent(owner._core,execution,prepared)==intent
    assert not owner._core.event_store.object_rows_by_type(h7.PARENT_KINDS[0])
    with pytest.raises(h7.ParentChildUnsupported):
        h7._advance_record(owner._core,h7.PARENT_KINDS[1],intent,details={'bundle_digest':'a'*64},evidence=None)
    with pytest.raises(TypeError):h7.register_child_intent(owner._core,execution,old)

@pytest.fixture(scope='module')
def material_case(tmp_path_factory):
    path=tmp_path_factory.mktemp('registered-material-case')
    patch=pytest.MonkeyPatch()
    setup=make_layout(path/'layout',patch,'files')
    owner,admitted,execution,old=parent_with_request(path/'parent',setup.request)
    draft=prepare_module_material_draft(profile_id=setup.entrypoint.name,parent=h7.parent_identity(owner._core),request_bytes=old.request_bytes,
        root_binding='test-root-binding',control_root=str(path/'control'))
    original_publish=owner._core.event_store.publish_batch
    def capture(**kwargs):
        result=original_publish(**kwargs)
        if any(item.metadata.get('content_schema_ref')==w.INVENTORY for item in kwargs['objects']):setup.root_commit=kwargs
        return result
    patch.setattr(owner._core.event_store,'publish_batch',capture)
    inventory=owner.schema_gateway.publish_public_material_inventory(draft,command_id='case')
    patch.setattr(owner._core.event_store,'publish_batch',original_publish)
    prepared=h7.prepare_registered_child_materials(owner._core,request_ref=old.request_ref,request_bytes=old.request_bytes,
        registered_inventory_ref=inventory.resource_ref,root_binding='test-root-binding',control_root=str(path/'control'))
    intent=h7.register_child_intent(owner._core,execution,prepared)
    yield owner,execution,old,prepared,draft,inventory,setup,path,intent
    patch.undo()


def test_complete_registration_and_selected_execution_differ(material_case):
    owner,execution,old,prepared,draft,inventory,setup,path,intent=material_case
    root=inventory.root;data=dict(draft.payloads)
    snapshot=w.decode(data[root['roots']['registration']])
    executors=[v for v in snapshot['declarations'] if v['kind']=='executor']
    assert len(executors)==2
    selected=w.decode(data[root['roots']['selection']])
    assert len([v for v in selected['selected_registrations'] if v['kind']=='executor'])==1
    observations=[w.decode(data[n]) for n in root['roots']['implementation']]
    assert 'numpy' not in {v['distribution']['name'] for v in observations}
    assert 'packaging' in {v['distribution']['name'] for v in observations}
    assert len(root['declaration_refs'])==len(snapshot['declarations'])


@pytest.mark.parametrize('damage',['extra_node','missing_edge','cycle','missing_registration','unknown_unit','profile_slash'])
def test_draft_semantic_negative_matrix(material_case,damage):
    from dataclasses import replace
    from cpn.rpnh.registry.public_materials import validate_draft
    _,_,_,_,draft,_,_,_,_=material_case
    inventory=draft.inventory;roots=draft.roots;payloads=dict(draft.payloads);contract=draft.contract
    if damage=='extra_node':
        row=dict(inventory[0]);row['node_id']='unused';inventory.append(row);payloads['unused']=payloads[inventory[0]['node_id']];inventory.sort(key=lambda v:v['node_id'])
    elif damage=='missing_edge':next(v for v in inventory if v['node_id']==roots['payload'])['depends_on']=[]
    elif damage=='cycle':next(v for v in inventory if v['node_id']==roots['payload'])['depends_on']=[roots['lowered_net']]
    elif damage=='missing_registration':contract['registrations'].pop()
    elif damage=='unknown_unit':contract['implementation_units'][0]['dependency_unit_ids']=['unknown']
    else:contract['contract_id']='invalid/identity'
    changed=replace(draft,inventory_bytes=w.canonical(inventory),contract_bytes=w.canonical(contract),payloads=tuple(sorted(payloads.items())))
    with pytest.raises(Exception):validate_draft(changed)


@pytest.mark.parametrize('kind',['publication','relation','missing_relation_event','extra_relation','source','schema_authority'])
def test_fresh_reader_rejects_real_registry_damage(material_case,kind):
    from cpn.rpnh.registry.public_materials import pair
    owner,_,_,_,_,inventory,_,_,_=material_case;core=owner._core;ref=inventory.resource_ref
    with core.event_store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute('SELECT * FROM objects WHERE version_id=?',(str(ref.resource_version_id),)).fetchone()
            if kind=='publication':
                event=db.execute('SELECT payload_json FROM events WHERE event_id=?',(row['published_event_id'],)).fetchone();body=json.loads(event[0]);body['metadata']['summary']='tampered'
                db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(body),row['published_event_id']))
            elif kind in ('relation','missing_relation_event','extra_relation'):
                relation=db.execute("SELECT * FROM relations WHERE json_extract(source_json,'$.version_id')=? LIMIT 1",(str(ref.resource_version_id),)).fetchone()
                if kind=='relation':db.execute("UPDATE relations SET strength='weak' WHERE relation_id=?",(relation['relation_id'],))
                elif kind=='missing_relation_event':db.execute('UPDATE relations SET published_event_id=? WHERE relation_id=?',('event:'+'e'*32,relation['relation_id']))
                else:
                    columns=list(relation.keys());values=[relation[k] for k in columns];values[columns.index('relation_id')]='relation:'+'e'*32
                    db.execute('INSERT INTO relations ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',values)
            elif kind=='source':db.execute("UPDATE objects SET metadata_json=json_set(metadata_json,'$.task_ref.logical_id',?) WHERE version_id=?",('task:'+'e'*32,str(ref.resource_version_id)))
            else:db.execute("UPDATE objects SET metadata_json=json_set(metadata_json,'$.content_schema_authority_ref.logical_id',?) WHERE version_id=?",('schema:'+'e'*32,str(ref.resource_version_id)))
            from cpn.rpnh.registry._candidate_read_context import _CandidateReadContext
            from cpn.rpnh.registry.public_materials import read_inventory_at
            with pytest.raises(Exception):read_inventory_at(_CandidateReadContext.from_core(core,db),ref)
        finally:db.rollback()
    assert read_inventory(core,ref)==inventory


def test_registered_materials_do_not_supply_pn_authority(material_case):
    owner,_,old,prepared,_,_,_,_,_=material_case
    with pytest.raises(TypeError):h7.register_child_intent(owner._core,None,prepared)
    with pytest.raises(TypeError):h7.register_child_intent(owner._core,None,old)


def test_raw_prepared_wrong_root_is_not_authority(material_case):
    from dataclasses import replace
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.identities import new_id
    owner,execution,_,prepared,_,_,_,_,_=material_case
    fake=replace(prepared,registered_inventory_ref=ResourceVersionRef(new_id('resource'),new_id('resource_version')))
    with pytest.raises(Exception):h7.register_child_intent(owner._core,execution,fake)


def test_intent_type_sensitive_request_material_binding(material_case):
    from dataclasses import replace
    owner,execution,_,prepared,_,_,_,_,_=material_case
    request=json.loads(prepared.request_bytes)
    # files rule factor 1 must not compare equal to True or 1.0.
    for value in (True,1.0):
        request['public_configuration']['configuration']['rule']['factor']=value
        with pytest.raises(Exception):h7.register_child_intent(owner._core,execution,replace(prepared,request_bytes=canonical_json(request)))


@pytest.mark.parametrize('mutation',['terminal_stream','principal','ghost_relation','command_id'])
def test_low_level_replay_rejects_changed_proposal_before_early_return(material_case,mutation):
    from dataclasses import replace
    owner,_,_,_,_,_,setup,_,_=material_case
    command=dict(setup.root_commit);events=list(command['events'])
    if mutation=='terminal_stream':events[-1]=replace(events[-1],stream_id='transaction:wrong')
    elif mutation=='principal':events[-1]=replace(events[-1],producer_principal='attacker')
    elif mutation=='ghost_relation':events.insert(-1,next(e for e in events if e.event_type=='relation_published/v1'))
    else:events[0]=replace(events[0],command_id='other')
    command['events']=tuple(events)
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(Exception,match='material prospective'):
        owner._core.event_store.publish_batch(**command)
    assert owner._core.event_store.max_ordinal()==before


@pytest.mark.parametrize('entry',['commit','resource_helper'])
def test_replay_rechecks_corrupted_descendant_publication(material_case,entry):
    from cpn.rpnh.registry.publication import _resource_from_payload
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PublishResource,PrivateSystemOrigin
    from cpn.rpnh.registry.public_materials import unique_refs
    owner,_,_,_,_,inventory,setup,_,_=material_case;core=owner._core;root=inventory.root
    ref=_resource_from_payload(root['installed_contract_ref'])
    with core.event_store.connect() as db:
        row=db.execute('SELECT e.event_id,e.payload_json FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.version_id=?',(str(ref.resource_version_id),)).fetchone()
        changed=json.loads(row['payload_json']);changed['metadata']['summary']='damaged publication'
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(changed),row['event_id']))
    try:
        with pytest.raises(Exception,match='publication'):
            if entry=='commit':core.event_store.publish_batch(**setup.root_commit)
            else:
                meta=core.get_version(inventory.resource_ref.resource_version_id).metadata
                command=PublishResource(origin=PrivateSystemOrigin(owner.schema_gateway._bootstrap_ref),payload=inventory.root_bytes,
                    media_type='application/json',content_schema_ref=w.INVENTORY,summary=meta['summary'],lifetime_ref=owner.schema_gateway._bootstrap_ref,
                    descriptors=meta['descriptors'],derived_from=unique_refs([root['installed_contract_ref'],*[v['resource_ref'] for k in ('node_refs','declaration_refs','schema_refs') for v in root[k]]]),
                    idempotency_key='public-material:v1:root:'+root['public_material_digest'])
                _publish_private_system(core,owner.schema_gateway._task_ref,command)
    finally:
        with core.event_store.connect() as db:db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(row['payload_json'],row['event_id']))
    assert read_inventory(core,inventory.resource_ref)==inventory


def test_new_root_terminal_must_be_valid_before_commit(tmp_path,monkeypatch):
    from dataclasses import replace
    setup=make_layout(tmp_path/'layout',monkeypatch,'files')
    owner,_,_,old=parent_with_request(tmp_path/'parent',setup.request)
    draft=prepare_module_material_draft(profile_id=setup.entrypoint.name,parent=h7.parent_identity(owner._core),request_bytes=old.request_bytes,
        root_binding='test-root-binding',control_root=str(tmp_path/'control'))
    original=owner._core.event_store.publish_batch
    def corrupt(**kwargs):
        if any(item.metadata.get('content_schema_ref')==w.INVENTORY for item in kwargs['objects']):
            kwargs['events']=(*kwargs['events'][:-1],replace(kwargs['events'][-1],stream_id='transaction:wrong'))
        return original(**kwargs)
    monkeypatch.setattr(owner._core.event_store,'publish_batch',corrupt)
    with pytest.raises(Exception,match='material prospective terminal'):
        owner.schema_gateway.publish_public_material_inventory(draft,command_id='bad-first-root')
    with owner._core.event_store.connect() as db:
        assert not db.execute("SELECT 1 FROM objects WHERE object_type='resource_version/v1' AND json_extract(metadata_json,'$.content_schema_ref')=?",(w.INVENTORY,)).fetchone()


def test_generic_operation_identity_sources_are_not_interchangeable():
    from types import SimpleNamespace as S
    def module(*ops):return S(components=[S(operations=list(ops))])
    def declaration(key,plugin=False):return {'kind':'executor','key':key,'contracts':({'native_plugin':{'selector':'p/run','operation':{'name':'run'}}} if plugin else {})}
    ordinary=S(name='run',executor='a')
    with pytest.raises(ValueError,match='ambiguous'):
        w.module_operation_shapes(module(ordinary,S(name='run',executor='b')),[declaration('a'),declaration('b')])
    with pytest.raises(ValueError,match='ambiguous'):
        w.module_operation_shapes(module(ordinary,S(name='anything',executor='b')),[declaration('a'),declaration('b',True)])
    bad=declaration('b',True);bad['contracts']['native_plugin']['operation']=None
    with pytest.raises(ValueError,match='malformed plugin'):
        w.module_operation_shapes(module(S(name='run',executor='b')),[bad])
