"""N01 finite Registries with explicit synthetic Start/products/Success.

The existing start_run and owner.admit establish canonical input grants. They
never invoke an executor, provider, worker, channel or transport. Settlement
uses the original APIs with declared static bytes and no external effects. Query and
Viewer reads are measured independently from explicit owner recording writes.
"""
from dataclasses import replace, asdict
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.resources import ResourceQuery, ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.errors import ResourceServiceError, StaleQueryContext, StaleAuthorityHead
from cpn.rpnh.registry.transaction import RegistryTransaction
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh.collaboration import (SourceMember, SourceQualifiedVersionRef, SourceQualifiedResourceRef,
    SourceSetQuery, RegistrySourceReader, SourceUnavailable, source_observation_schema_data,
    read_source_set, current_source_set, SourceObservationDraft)
from cpn.rpnh.collaboration.source_observations import _read_source_observation
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.source_observation import SourceObservationView
from cpn.frontend.server import handle_request

TEXT='application/source_query_text/v1'
EXECUTOR='test/source-query-never-executed/v1'
TERMINAL='test/source-query-terminal/v1'


def forbidden_executor(**kwargs):
    raise AssertionError('N01 fixture must not execute a business operation')


def _module():
    binding={'bucket_id':'work','budget_scope':'module','finalization_scope':None}
    def component(name):
        return {'name':name,'key':'operation','config_schema':CONFIG_SCHEMA_ID,'config':{'input_modes':{'request':'read'}},
            'ports':[{'name':'request','direction':'input','schema':TEXT},{'name':'result','direction':'output','schema':TEXT}],
            'operations':[{'name':'run','executor':EXECUTOR,'inputs':['request'],'outputs':['result'],
                'request_port':None,'tools':[],'config':{},'budget_binding':binding,
                'outcomes':[{'name':'complete','products':[{'port':'result'}]}]}]}
    return ModuleDeclaration.from_dict({'schema_version':'rpnh/module_declaration/v1','name':'SourceQuery',
        'components':[component('left'),component('right')],'links':[],
        'entry':{n:{'component':n,'port':'request'} for n in ('left','right')},
        'exit':{'result':{'component':'left','port':'result'}},
        'terminal':{'key':TERMINAL,'source':{'component':'left','port':'result'},'operation':'run','outcome':'complete','config':{}},
        'required_schemas':[CONFIG_SCHEMA_ID,TEXT],'budgets':{},'budget_buckets':[{**binding,'max_attempts':4}]})


def _owner(path, source):
    schemas,types,paths=source_observation_schema_data()
    catalog=SchemaCatalog(schemas=schemas,types=types,schema_paths=paths)
    registration=Registration();register_basic_components(registration)
    registration.register_schema(TEXT,{'$id':TEXT,'$schema':'http://json-schema.org/draft-07/schema#','type':'string'})
    registration.register_executor(EXECUTOR,forbidden_executor,identity={'implementation_id':'tests.source_query','revision':'v1'},
        contracts={'transport':'deterministic','input_ports':None,'output_ports':None,'config_schema':CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL,forbidden_executor,identity={'implementation_id':'tests.source_terminal','revision':'v1'},
        contracts={'binding_protocol':'rpnh/module_terminal/v1'})
    module=_module();values={n:OwnerInput(TEXT,canonical_json(source+':'+n),source+' '+n+' private input') for n in ('left','right')}
    owner=start_run(module,registration,run_dir=path,task_input=values['left'],entry_inputs=values,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']),('rpnh/module_declaration/v1',),4,0,4,0),
        model_condition='offline-source-query-no-model',owner_statement='Synthetic offline query fixture; no business execution',
        command_id='fixture:'+source,catalog=catalog)
    owner.schema_gateway.bind_source_identity(source_id=source,command_id='bind:'+source)
    owner.query_admissions={path:owner.admit(name+'.run',logical_tau=0,command_id='admit:'+source+':'+name)
                            for path,name in (('main','left'),('other','right'))}
    contexts={path:value.admission.context for path,value in owner.query_admissions.items()}

    return owner,contexts


def _readonly(owner):
    return _RegistryCore(owner._core.run_dir,create=False,read_only=True,catalog=owner._core.catalog)


def _settle_and_readmit(owners,sources):
    """Real original Start/products/Success on explicitly synthetic bytes only."""
    for source in sources:
        owner,contexts=owners[source]
        assert owner.control.edits.queue==[] and owner.host_execution_bindings is None
        execution=owner.start(owner.query_admissions['main'],command_id='fixture:start:'+source)
        outputs=owner.products(execution,outcome_id='complete',products={'left.result':(canonical_json('explicit synthetic query fixture output'),)},command_id='fixture:products:'+source)
        owner.succeed(outputs,command_id='fixture:success:'+source)
        admitted=owner.admit('left.run',logical_tau=0,command_id='fixture:new-reader:'+source)
        assert admitted is not None
        contexts['main']=admitted.admission.context



def counts(owners):
    return {name:(len(owner._core.event_store.list_events()),len(owner._core.event_store.object_rows()),
                  owner._core.event_store.writer_epoch) for name,(owner,_) in owners.items()}


@pytest.fixture
def world(tmp_path):
    owners={source:_owner(tmp_path/source,source) for source in ('R1','R2','R3','R4')}
    members=tuple(SourceMember(SourceQualifiedVersionRef(source,owner.identity.task_ref),('main','other'),(source+'-alias',))
                  for source,(owner,_) in owners.items())
    gateway=owners['R1'][0].schema_gateway
    s1=gateway.publish_source_set(members=members[:3],command_id='sources:S1')
    attached={'R1','R2','R4'};choices={};calls=[]
    def resolver(source,path):
        calls.append((source,path))
        if source not in attached:raise SourceUnavailable('offline')
        owner,contexts=owners[source]
        return RegistrySourceReader(_readonly(owner),contexts[choices.get((source,path),path)])
    query=SourceSetQuery(_readonly(owners['R1'][0]),resolver,capture_context=owners['R1'][1]['main'])
    return owners,members,gateway,s1,attached,choices,calls,query


def test_real_n01_manifest_pages_saved_get_and_supplement(world,tmp_path,monkeypatch):
    owners,members,gateway,s1,attached,choices,calls,query=world
    paths={s:'main' for s in ('R1','R2','R3')}
    before=counts(owners)
    draft=query.query(s1.source_set_ref,paths=paths,limit=1)
    assert counts(owners)==before
    capture=draft.to_dict();assert capture['coverage']['state']=='partial' and capture['coverage']['total_count'] is None
    assert [r['source_id'] for r in capture['sources']]==['R1','R2','R3']
    assert capture['sources'][2]['coverage']['loaded_count'] is None
    assert all(r['cursor'] is not None for r in capture['sources'][:2])
    fixed={row['source_id']:row['cut']['ordinal'] for row in capture['sources'] if row['cut'] is not None}
    # Exercise the original ResourceService as well as the SourceSet consumer.
    # Retain native pre-promotion heads/pages/cursors for a new-reader regression.
    native={}
    for source in ('R1','R2'):
        owner,contexts=owners[source];service=_ResourceServiceKernel(_readonly(owner))
        head=service.head(contexts['main'],ordinal=fixed[source])
        request=ResourceQuery(task_ref=owner.identity.task_ref,scope_refs=(),limit=1,through_head=head)
        native[source]=(head,request,service.query(contexts['main'],request))
    replay=query.query(s1.source_set_ref,paths=paths,limit=1,through=fixed)
    assert replay.canonical_bytes==draft.canonical_bytes and counts(owners)==before
    obs1=gateway.record_source_observation(query=query,draft=draft,command_id='Obs:1')
    persisted=_read_source_observation(query.core,obs1,capture_context=query.capture_context)
    s2=gateway.publish_source_set(members=members,command_id='sources:S2',expected=s1.source_set_ref,expected_sequence=1)
    before=counts(owners);second=query.query(s1.source_set_ref,paths=paths,limit=1,previous=obs1)
    assert counts(owners)==before
    assert all(r['source_id']!='R4' for r in second.to_dict()['sources'])
    latest=query.query(s2.source_set_ref,paths={s:'main' for s in ('R1','R2','R3','R4')},limit=1).to_dict()
    assert [r['source_id'] for r in latest['sources']]==['R1','R2','R3','R4']
    assert latest['sources'][3]['availability']=='readable'
    assert [r['cut'] for r in second.to_dict()['sources']]==[r['cut'] for r in capture['sources']]
    assert all(r['coverage']['state']=='complete' for r in second.to_dict()['sources'][:2])
    obs2=gateway.record_source_observation(query=query,draft=second,command_id='Obs:2')
    assert _read_source_observation(query.core,obs1,capture_context=query.capture_context)==persisted
    # A distinct source vector sees a later captured cut; no old observation is rewritten.
    header=second.to_dict()['sources'][0]['headers'][0]
    dependency=SourceQualifiedResourceRef('R1',ResourceVersionRef(
        TypedId.parse(header['ref']['ref']['resource_id']),
        TypedId.parse(header['ref']['ref']['resource_version_id'])))
    before=counts(owners);sup=query.supplement(obs1,dependency=dependency);assert counts(owners)==before
    assert sup.to_dict()['sources'][0]['cut']['ordinal']>capture['sources'][0]['cut']['ordinal']
    corrupted=sup.to_dict();corrupted['sources'][0]['cut']['writer_fencing_epoch']+=1
    with pytest.raises(ResourceServiceError):
        gateway.record_source_observation(query=query,draft=SourceObservationDraft(canonical_json(corrupted)),command_id='Sup:bad-head')
    supref=gateway.record_source_observation(query=query,draft=sup,command_id='Sup:1')
    assert _read_source_observation(query.core,obs1,capture_context=query.capture_context)==persisted
    selected=SourceObservationView(query,(obs1,obs2,supref))
    dashboard=RegistryDashboard(owners['R1'][0]._core.run_dir,catalog=query.core.catalog,source_observations=selected)
    with pytest.raises(RegistryConflict,match='not canonically'):
        query.view(obs1)
    # No different firing can read the temporary capture.
    with pytest.raises(RegistryConflict,match='same-firing'):
        _read_source_observation(query.core,obs1,capture_context=owners['R1'][1]['other'])
    with owners['R1'][0]._core.event_store.connect() as db:
        created_ordinal=db.execute('SELECT e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.version_id=?',(str(obs1.ref.version_id),)).fetchone()[0]
    _settle_and_readmit(owners,('R1','R2'))
    for source,(old_head,request,old_page) in native.items():
        owner,contexts=owners[source];service=_ResourceServiceKernel(_readonly(owner))
        current=service.head(contexts['main'])
        assert current.ordinal>old_head.ordinal
        canonical=owner._core.event_store.canonical_events()
        assert current.ordinal==canonical[-1].ordinal
        assert current.stream_heads=={e.stream_id:e.stream_sequence for e in canonical}
        assert current.task_control_sequence==max(e.task_control_sequence or 0 for e in canonical if e.task_id==owner._core.task_id)
        assert service._canonical_heads((0,old_head.ordinal,current.ordinal,None))=={
            0:replace(old_head,ordinal=0,task_control_sequence=0,stream_heads={}),
            old_head.ordinal:old_head,current.ordinal:current}
        # The Success-promoted invocation stream is absent from the old cut.
        promoted={e.stream_id for e in owner._core.event_store.canonical_events()
                  if e.ordinal<=old_head.ordinal}
        historical={e.stream_id for e in owner._core.event_store.canonical_events(through_ordinal=old_head.ordinal)}
        assert promoted-historical and promoted-historical<=set(current.stream_heads)
        assert not (promoted-historical)&set(old_head.stream_heads)
        fresh=service.query(contexts['main'],request)
        assert fresh.headers==old_page.headers and fresh.through_head==old_head
        assert old_page.cursor is not None
        with pytest.raises(StaleQueryContext,match='authority/query'):
            service.query(contexts['main'],replace(request,cursor=old_page.cursor))
        with pytest.raises(StaleQueryContext):
            service.query(contexts['main'],replace(request,through_head=replace(old_head,ordinal=current.ordinal+1)))
        with pytest.raises(StaleQueryContext,match='committed history'):
            service.query(contexts['main'],replace(request,through_head=replace(old_head,task_control_sequence=old_head.task_control_sequence+1)))
        with pytest.raises(StaleAuthorityHead):
            service._canonical_heads((-1,))
        with pytest.raises(TypeError):
            service._canonical_heads((True,))
    assert gateway.record_source_observation(query=query,draft=draft,command_id='Obs:1')==obs1
    validations=[];original_query=_ResourceServiceKernel.query
    def observe(service,context,request):
        validations.append(request)
        return original_query(service,context,request)
    monkeypatch.setattr(_ResourceServiceKernel,'query',observe)
    before=counts(owners)
    response=handle_request(dashboard,'GET','/api/v2/source-observation')
    assert response.status==200
    body=json.loads(response.body);assert body['observation_ref']==obs1.to_dict()
    assert body['available_manifest']==s2.source_set_ref.to_dict()
    assert body['sources'][2]['coverage']['total_count'] is None
    assert body['sources'][2]['current_access']=='not_checked'
    assert body['publication']['state']=='PUBLISHED'
    assert all(row['current_access']=='readable' for row in body['sources'][:2])
    assert [row['headers'] for row in body['sources'][:2]]==[row['headers'] for row in capture['sources'][:2]]
    assert body['coverage']['loaded_count']==2
    commits=[e for e in owners['R1'][0]._core.event_store.list_events_by_idempotency_key('fixture:success:R1') if e.event_type=='transaction_committed/v1']
    assert len(commits)==1 and body['publication']['visible_ordinal']==commits[0].ordinal>created_ordinal
    assert body['publication']['published_transaction_id']==str(commits[0].transaction_id)
    assert body['query_scope']=={'limit':1,'media_types':[]}
    assert validations and all(q.limit==1 and q.through_head is not None for q in validations)
    assert all('cursor' not in r and 'authority' not in r for r in body['sources'])
    assert handle_request(dashboard,'HEAD','/api/v2/source-observation').body==b''
    assert handle_request(dashboard,'POST','/api/v2/source-observation').status==405
    assert counts(owners)==before
    output=Path(__import__('os').environ.get('RPNH_SOURCE_QUERY_FIXTURE',str(tmp_path/'viewer-response.json')))
    output.write_text(json.dumps(body,sort_keys=True))


def test_real_paths_never_union_and_current_access_clears_saved_content(world):
    owners,members,gateway,s1,attached,choices,calls,query=world
    paths={s:'main' for s in ('R1','R2','R3')}
    draft=query.query(s1.source_set_ref,paths=paths,limit=10);obs=gateway.record_source_observation(query=query,draft=draft,command_id='Obs:paths')
    original=_read_source_observation(query.core,obs,capture_context=query.capture_context)
    headers=draft.to_dict()['sources'][0]['headers']
    assert any(h['display_summary']=='R1 left private input' for h in headers)
    assert not any(h['display_summary']=='R1 right private input' for h in headers)
    with pytest.raises(ValueError,match='continuation'):
        query.query(s1.source_set_ref,paths={**paths,'R1':'other'},limit=10,previous=obs)
    _settle_and_readmit(owners,('R1','R2'))
    choices['R1','main']='other'
    before=counts(owners);view=query.view(obs);assert counts(owners)==before
    assert view['sources'][0]['current_access'] in ('denied','access_changed')
    assert view['sources'][0]['headers']==[] and view['sources'][0]['coverage']['loaded_count'] is None
    assert view['sources'][0]['capture_coverage']['state']=='complete'
    assert 'R1 left private input' not in json.dumps(view)
    assert _read_source_observation(query.core,obs,capture_context=query.capture_context)==original
    from cpn.rpnh.registry.errors import StaleInvocationContext
    with pytest.raises(StaleInvocationContext):
        query.query(s1.source_set_ref,paths=paths,limit=10,previous=obs)


def test_real_sourceset_alias_dedup_replay_stale_cas_and_reopen(world,monkeypatch):
    owners,members,gateway,s1,attached,choices,calls,query=world
    alias=replace(members[0],aliases=('same-source',))
    separate=gateway.publish_source_set(members=(members[0],alias),command_id='alias:first')
    assert len(separate.members)==1 and set(separate.members[0].aliases)=={'same-source','R1-alias'}
    assert gateway.publish_source_set(members=(members[0],alias),command_id='alias:first')==separate
    alias_page=query.query(separate.source_set_ref,paths={'R1':'main'},limit=1).to_dict()
    assert alias_page['coverage']['expected_source_count']==1
    original_commit=RegistryTransaction.commit;won=[]
    def compete(tx):
        if 'alias:loser' in tx.idempotency_key:
            won.append(gateway.publish_source_set(members=(members[0],),command_id='alias:winner',expected=separate.source_set_ref,expected_sequence=1))
        return original_commit(tx)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction,'commit',compete)
        with pytest.raises(RegistryConflict):
            gateway.publish_source_set(members=(alias,),command_id='alias:loser',expected=separate.source_set_ref,expected_sequence=1)
    assert len(won)==1 and current_source_set(query.core,separate.source_set_ref.ref.entity_id)==won[0]
    with pytest.raises(ValueError,match='conflicting'):
        gateway.publish_source_set(members=(members[0],replace(alias,access_paths=('other',))),command_id='bad:path-union')
    s2=gateway.publish_source_set(members=members,command_id='sources:S2',expected=s1.source_set_ref,expected_sequence=1)
    with pytest.raises(RegistryConflict,match='stale SourceSet'):
        gateway.publish_source_set(members=members[:2],command_id='sources:stale',expected=s1.source_set_ref,expected_sequence=1)
    with pytest.raises(RegistryConflict,match='expected sequence'):
        gateway.publish_source_set(members=members,command_id='sources:bad-seq',expected=s2.source_set_ref,expected_sequence=1)
    assert read_source_set(_readonly(owners['R1'][0]),s1.source_set_ref)==s1
    assert current_source_set(query.core,s1.source_set_ref.ref.entity_id)==s2
    assert gateway.publish_source_set(members=members,command_id='sources:S2',expected=s1.source_set_ref,expected_sequence=1)==s2


def test_real_manifest_identity_unavailable_and_unauthorized_supplement(world):
    owners,members,gateway,s1,attached,choices,calls,query=world
    paths={s:'main' for s in ('R1','R2','R3')}
    obs=gateway.record_source_observation(query=query,draft=query.query(s1.source_set_ref,paths=paths,limit=10),command_id='Obs:denied')
    other=SourceSetQuery(query.core,lambda source,path:RegistrySourceReader(_readonly(owners['R2'][0]),owners['R2'][1]['main']))
    bad=other.query(s1.source_set_ref,paths=paths,limit=10).to_dict()
    assert bad['sources'][0]['availability']=='access_changed' and bad['sources'][0]['headers']==[]
    owner,contexts=owners['R1'];service=_ResourceServiceKernel(_readonly(owner))
    result=service.query(contexts['other'],ResourceQuery(contexts['other'].task_ref,(),limit=10))
    forbidden=next(h.ref for h in result.headers if h.display_summary=='R1 right private input')
    before=counts(owners);sup=query.supplement(obs,dependency=SourceQualifiedResourceRef('R1',forbidden));assert counts(owners)==before
    assert sup.to_dict()['sources'][0]['availability']=='denied' and sup.to_dict()['coverage']['total_count'] is None
    with pytest.raises(ValueError,match='outside'):
        query.supplement(obs,dependency=SourceQualifiedResourceRef('R4',forbidden))
    assert handle_request(type('Empty',(),{})(),'GET','/api/v2/source-observation').status==501
    view=SourceObservationView(query,(obs,));dashboard=type('Provider',(),{'source_observation':staticmethod(view)})()
    foreign=replace(obs,ref=replace(obs.ref,version_id=new_id('resource_version')))
    assert handle_request(dashboard,'GET','/api/v2/source-observation?'+urlencode({'observation_ref':json.dumps(foreign.to_dict())})).status==403
    assert handle_request(dashboard,'GET','/api/v2/source-observation?observation_ref={}&observation_ref={}').status==400


def test_real_record_rejects_cross_manifest_cut_path_and_false_zero(world):
    owners,members,gateway,s1,attached,choices,calls,query=world
    paths={s:'main' for s in ('R1','R2','R3')}
    first=query.query(s1.source_set_ref,paths=paths,limit=1);obs=gateway.record_source_observation(query=query,draft=first,command_id='Obs:base')
    gateway.publish_source_set(members=members,command_id='sources:later-authority-head',expected=s1.source_set_ref,expected_sequence=1)
    later_authority_head=asdict(_ResourceServiceKernel(_readonly(owners['R1'][0])).head(owners['R1'][1]['main']))
    second=query.query(s1.source_set_ref,paths=paths,limit=1,previous=obs)
    assert later_authority_head!=second.to_dict()['sources'][0]['authority_head']
    for kind in ('path','cut','unknown_zero','header_size','header_summary','cursor_offset','authority_head','cursor_principal'):
        data=second.to_dict()
        if kind=='path':data['sources'][0]['access_path']='other'
        if kind=='cut':data['sources'][0]['cut']['ordinal']+=1
        if kind=='unknown_zero':data['sources'][2]['coverage']['total_count']=0
        if kind=='header_size':data['sources'][0]['headers'][0]['byte_size']+=1
        if kind=='header_summary':data['sources'][0]['headers'][0]['display_summary']='fabricated'
        if kind=='authority_head':data['sources'][0]['authority_head']=later_authority_head
        if kind=='cursor_principal':
            data=first.to_dict();data['sources'][0]['cursor']['principal_ref']['ref']['version_id']=str(new_id('principal_version'))
        if kind=='cursor_offset':
            data=first.to_dict();data['sources'][0]['cursor']['offset']+=1
        with pytest.raises((RegistryConflict,ValueError,RuntimeError)):
            gateway.record_source_observation(query=query,draft=SourceObservationDraft(canonical_json(data)),command_id='bad:'+kind)
    with pytest.raises(RegistryConflict,match='conflicts'):
        gateway.record_source_observation(query=query,draft=second,command_id='Obs:base')
    assert _read_source_observation(query.core,obs,capture_context=query.capture_context)['observation']==first.to_dict()


def test_source_draft_requires_exact_immutable_canonical_json():
    for value in (bytearray(b'{}'), b'{"a":1,"a":2}', b'{"value":NaN}', b'{ "x": 1 }', b'[]'):
        with pytest.raises((ValueError,TypeError)):
            SourceObservationDraft(value)


def test_real_historical_data_cut_keeps_separate_current_qualification_head(world):
    owners,members,gateway,s1,attached,choices,calls,query=world
    owner=owners['R2'][0]
    source_service=_ResourceServiceKernel(_readonly(owner))
    available=source_service.query(owners['R2'][1]['main'],ResourceQuery(owner.identity.task_ref,(),limit=10))
    # The task input and left entry deliberately have the same display summary.
    # Select the exact entry ref actually authorized by ResourceService, not an
    # arbitrary same-summary Registry object that this reader cannot query.
    inputs=[h for h in available.headers if h.display_summary=='R2 left private input']
    assert len(inputs)==1
    resource=next(row for row in owner._core.event_store.canonical_object_rows(object_type='resource_version/v1')
                  if row['version_id']==str(inputs[0].ref.resource_version_id))
    commits=[e for e in owner._core.event_store.list_events_by_transaction(resource['transaction_id']) if e.event_type=='transaction_committed/v1']
    assert len(commits)==1
    draft=query.query(s1.source_set_ref,paths={s:'main' for s in ('R1','R2','R3')},through={'R2':commits[0].ordinal},limit=10)
    row=draft.to_dict()['sources'][1]
    assert row['authority_head']['ordinal']>row['cut']['ordinal']
    assert len(row['headers'])==1
    assert row['headers'][0]['ref']['ref']['resource_version_id']==str(inputs[0].ref.resource_version_id)
    reference=gateway.record_source_observation(query=query,draft=draft,command_id='Obs:historical-data')
    before_record=_read_source_observation(query.core,reference,capture_context=query.capture_context)
    later=next(h for h in available.headers if str(h.ref.resource_version_id)!=row['headers'][0]['ref']['ref']['resource_version_id'])
    assert later.published_at_head.ordinal>row['cut']['ordinal']
    supplement=query.supplement(reference,dependency=SourceQualifiedResourceRef('R2',later.ref))
    supref=gateway.record_source_observation(query=query,draft=supplement,command_id='Sup:later-dependency')
    assert _read_source_observation(query.core,reference,capture_context=query.capture_context)==before_record
    _settle_and_readmit(owners,('R1','R2'))
    body=query.view(reference)
    assert body['sources'][1]['current_access']=='readable'
    assert body['sources'][1]['headers']==row['headers']
    supplemental=query.view(supref)
    assert supplemental['sources'][0]['current_access']=='readable'
    assert supplemental['sources'][0]['headers'][0]['ref']==SourceQualifiedResourceRef('R2',later.ref).to_dict()
    assert supplemental['sources'][0]['cut']['ordinal']>row['cut']['ordinal']
