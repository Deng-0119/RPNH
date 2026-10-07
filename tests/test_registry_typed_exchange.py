"""New exact readers and real ordinary exchange; no runtime or HOST read work."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog, TypedReadError, descriptor_at, payload_at, wire_ref
from cpn.rpnh.collaboration.public_projections import read_public_projection, read_public_mapping
from cpn.rpnh.collaboration.subnet_exchange import (plan_subnet_export,export_subnet,plan_subnet_import,import_subnet,
    TargetRegistrySelection,SubnetExchangeError,_zip,_exchange,_Paths)
from cpn.rpnh.collaboration import ClosedModuleAuthor,SourceQualifiedVersionRef,SourceQualifiedResourceRef,author_material_schema_data
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest,_bootstrap_identity
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import new_id,TypedId
from cpn.rpnh.registry.schema_catalog import SchemaCatalog,canonical_json
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.collaboration.share_packages import canonical_bytes,sha256,PackageError
from cpn.rpnh.collaboration.package_preview import preview_package
from test_native_net_operations import _registration,_simple_module


def world(path,source,observer=False):
    schemas,types,paths=author_material_schema_data()
    if observer:
        from cpn.rpnh.registry.observer_access import observer_access_schema_data
        other,definitions,locations=observer_access_schema_data()
        schemas.update(other);paths.update(locations)
        types=tuple({item.name:item for item in (*types,*definitions)}.values())
    catalog=SchemaCatalog(schemas=schemas,types=types,schema_paths=paths)
    core=_RegistryCore(path,create=True,catalog=catalog)
    owner=_bootstrap_identity(core,NativeBootstrapManifest(('closed-author-test/v1',)))
    bootstrap=_version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway=RegistryRegistrationGateway(core,owner.task_ref,bootstrap)
    gateway.bind_source_identity(source_id=source,command_id='source:first')
    ref=VersionRef('principal/v1',new_id('principal'),new_id('principal_version'))
    body={'principal_id':str(ref.entity_id),'principal_version_id':str(ref.version_id),'display_name':'Author'}
    core.publish_bytes(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,payload=canonical_json(body),
        metadata=body,media_type='application/json',schema_ref='registry_v1/principal/v1',idempotency_key='principal')
    author=ClosedModuleAuthor(gateway,_registration(),SourceQualifiedVersionRef(source,ref))
    return core,author


def publish(author,command='first',parent=None,copied=False):
    module=_simple_module()
    ids={p:'element:'+uuid.uuid4().hex for p in _elements(module)}
    copies={}
    if parent:
        prior={x['locator']:x['element_id'] for x in parent.element_map['elements']}
        if copied:copies={ids[p]:prior[p] for p in ids}
        else:ids=prior
    return author.publish(module=module,element_ids=ids,command_id=command,
        parent_ref=parent.revision.revision_ref if parent else None,copy_sources=copies)


def snapshot(core,source,denied=()):
    def check(ref,operation):
        if canonical_bytes(ref.to_dict() if hasattr(ref,'to_dict') else ref) in denied:
            raise TypedReadError('MATERIAL_ACCESS_NOT_GRANTED' if operation=='material' else 'NOT_DISCLOSED')
    rows={row['version_id']:dict(row) for row in core.event_store.object_rows()}
    return SimpleNamespace(objects=rows,events=core.event_store.list_events(),relations=(),source_id=source,
        check_authorized=check,max_material_bytes=4*1024*1024,
        publication_ordinals={r['version_id']:1 for r in rows.values()})


class SourceSession:
    """Trusted adapter fixture; real canonical sessions have separate tests."""
    def __init__(self,core,source):
        self.core=core;self.source=source;self.snapshot=snapshot(core,source);self.catalog=TypedReaderCatalog();self.revoked=False
        self.cut={'source_id':source,'cut_id':'fixture-cut','head':{'ordinal':core.event_store.max_ordinal(),
            'writer_fencing_epoch':core.writer_epoch},'reader_contract_version':'rpnh/registry_read/v1'}
    def read_exact(self,*,entry_ref,at_cut):
        assert at_cut==self.cut
        return {'status':'ok','record':self.catalog.read_exact(self.core,entry_ref,snapshot=self.snapshot)}
    def read_material(self,*,resource_ref,at_cut,max_bytes):
        assert at_cut==self.cut
        raw,_=payload_at(self.core,resource_ref,self.snapshot,material=True,max_bytes=max_bytes)
        return {'status':'ok','bytes':raw}
    def read_public_projection(self,*,entry_ref,at_cut):
        assert at_cut==self.cut
        return {'status':'ok','record':read_public_projection(self.core,entry_ref,snapshot=self.snapshot)}
    def final_recheck(self,source_ids=None):
        if self.revoked:raise TypedReadError('ACCESS_CHANGED')
    def authorize_export(self,refs,*,destination,at_cuts):
        assert destination=='local-review'
        self.final_recheck()


def exported(core,author,value):
    session=SourceSession(core,author.binding['source_id'])
    plan=plan_subnet_export(session,root_revision_ref=value.revision.revision_ref,cuts={session.source:session.cut})
    return export_subnet(session,plan=plan,destination='local-review')


def target(author):return TargetRegistrySelection(SourceQualifiedVersionRef(author.binding['source_id'],author.gateway._task_ref))
def head(core):return {'ordinal':core.event_store.max_ordinal(),'writer_fencing_epoch':core.writer_epoch}


def test_stored_projection_reads_without_compilation_and_keeps_copy_direction(tmp_path,monkeypatch):
    core,author=world(tmp_path/'source','source-a');base=publish(author);copied=publish(author,'copy',base,True)
    before=len(core.event_store.list_events())
    def forbidden(*a,**k):raise AssertionError('read invoked HOST')
    monkeypatch.setattr(author.registration,'resolve',forbidden)
    monkeypatch.setattr('cpn.rpnh.collaboration.materials.compile_module',forbidden)
    snap=snapshot(core,'source-a')
    graph=read_public_projection(core,copied.revision.revision_ref,snapshot=snap)
    assert graph['nodes'] and graph['edges'] and graph['configuration']=={'host_requirements_ref':copied.revision.host_requirements_ref.to_dict(),
        'declared_configuration_ref':copied.revision.definition_ref.to_dict()}
    assert graph['mapping_groups'] and {g['relation_kind'] for g in graph['mapping_groups']}=={'copied_from'}
    assert all(g['source_revision_ref']==base.revision.revision_ref.to_dict() for g in graph['mapping_groups'])
    assert read_public_mapping(core,copied.revision.revision_ref,base.revision.revision_ref,snapshot=snap)['groups']==graph['mapping_groups']
    assert len(core.event_store.list_events())==before


def test_projection_dependency_denial_and_corruption_never_returns_graph(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author)
    snap=snapshot(core,'source-a',{canonical_bytes(value.revision.definition_ref.to_dict())})
    with pytest.raises(TypedReadError,match='MATERIAL_ACCESS_NOT_GRANTED'):
        read_public_projection(core,value.revision.revision_ref,snapshot=snap)
    path=core.object_store.path_for_version(value.revision.definition_ref.ref.resource_version_id)
    raw=path.read_bytes();path.write_bytes(raw.replace(b'Simple',b'SimpLe'))
    # A change with identical size cannot be certified by legacy size checking.
    if path.read_bytes()==raw:path.write_bytes(raw[:-1]+b' ')
    with pytest.raises(TypedReadError,match='INTEGRITY_FAILED'):
        read_public_projection(core,value.revision.revision_ref,snapshot=snapshot(core,'source-a'))


def test_reader_exact_type_logical_and_field_allowlist(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author);catalog=TypedReaderCatalog();snap=snapshot(core,'source-a')
    assert catalog.read_exact(core,value.revision.revision_ref,snapshot=snap)['definition_kind']=='closed_module'
    wrong=SourceQualifiedVersionRef('source-a',replace(value.revision.revision_ref.ref,entity_id=new_id('resource')))
    with pytest.raises(TypedReadError,match='INTEGRITY_FAILED'):catalog.read_exact(core,wrong,snapshot=snap)
    with pytest.raises(TypedReadError,match='INVALID_PROJECTION'):catalog.read_exact(core,value.revision.revision_ref,snapshot=snap,projection=('storage_locator',))
    with pytest.raises(TypedReadError,match='UNSUPPORTED_ENTRY_TYPE'):catalog.fields('collaboration_net_revision/v99')


def test_real_export_import_new_identities_replay_and_stale_head(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author);package=exported(core,author,value)
    assert package.to_dict()['definition_closure_status']=='complete'
    assert _exchange(package.package)['root_revision_ref']==value.revision.revision_ref.to_dict()
    dest,owner=world(tmp_path/'target','source-b')
    plan=plan_subnet_import(package,target=target(owner));before=head(dest)
    result=import_subnet(owner,plan=plan,command_id='copy',expected_target_head=before)
    assert result.revision_ref.source_id=='source-b' and result.revision_ref.ref!=value.revision.revision_ref.ref
    assert all(row['origin']['kind']=='registry_export' and row['local_element_id']!=row['origin']['element_id'] for row in result.origin_to_local_map)
    count=len(dest.event_store.list_events())
    assert import_subnet(owner,plan=plan,command_id='copy',expected_target_head=before)==result
    assert len(dest.event_store.list_events())==count
    with pytest.raises(RegistryConflict,match='head'):import_subnet(owner,plan=plan,command_id='another',expected_target_head=before)
    assert len(dest.event_store.list_events_by_type(('net_adopted/v1','marking_checkpoint_committed/v1')))==0
    reexport=exported(dest,owner,owner._validate(result.revision_ref))
    assert _exchange(reexport.package)['root_revision_ref']==result.revision_ref.to_dict()
    assert preview_package(reexport.archive_bytes)


def test_atomic_target_head_cas_rejects_write_between_precheck_and_commit(tmp_path,monkeypatch):
    core,author=world(tmp_path/'source','source-a');value=publish(author);package=exported(core,author,value)
    dest,owner=world(tmp_path/'target','source-b');plan=plan_subnet_import(package,target=target(owner))
    from cpn.rpnh.registry.transaction import RegistryTransaction
    original=RegistryTransaction.commit;injected=[]
    def raced(tx):
        if tx.idempotency_key=='subnet-import:race:request' and not injected:
            injected.append(True)
            nested=dest.begin(idempotency_key='concurrent-append');original(nested)
        return original(tx)
    monkeypatch.setattr(RegistryTransaction,'commit',raced)
    with pytest.raises(RegistryConflict,match='head'):
        import_subnet(owner,plan=plan,command_id='race',expected_target_head=head(dest))
    assert not dest.event_store.object_rows_by_type('collaboration_net_revision/v1')


def test_inner_exchange_checksum_and_missing_dependency_rejected(tmp_path):
    core,author=world(tmp_path/'source','source-a');package=exported(core,author,publish(author)).package
    files={'manifest.json':package.manifest_bytes,**dict(package.artifacts)}
    exchange_path=next(path for path,raw in package.artifacts if path.startswith('subnet-exchange'))
    doc=json.loads(files[exchange_path]);doc['materials'][0]['sha256']='0'*64
    files[exchange_path]=canonical_bytes(doc);manifest=json.loads(files['manifest.json'])
    row=next(r for r in manifest['artifacts'] if r['path']==exchange_path);row.update(bytes=len(files[exchange_path]),sha256=sha256(files[exchange_path]))
    files['manifest.json']=canonical_bytes(manifest)
    damaged=preview_package(_zip(files));dest,owner=world(tmp_path/'target','source-b')
    with pytest.raises(SubnetExchangeError,match='INTEGRITY_FAILED'):plan_subnet_import(damaged,target=target(owner))
    manifest['dependencies']=[{'dependency_id':'missing','package_id':'test/missing','manifest_digest':'1'*64,
        'version_range':None,'required':True,'acquisition_hint':None}]
    files['manifest.json']=canonical_bytes(manifest)
    with pytest.raises(PackageError,match='DEPENDENCY_UNRESOLVED'):plan_subnet_import(_zip(files),target=target(owner))


def test_allocator_reserves_generated_and_preserved_casefold_paths():
    paths=_Paths(['closure/0000.json','SUBNET-EXCHANGE.json','declarations/main.json'])
    assert paths.allocate('closure/0000.json')=='closure/0000-0001.json'
    assert paths.allocate('closure/0000.json')=='closure/0000-0002.json'
    assert paths.allocate('subnet-exchange.json')=='subnet-exchange-0001.json'


def test_real_net_projection_reuses_stored_inventory_without_database_or_host_reads(tmp_path,monkeypatch):
    from test_module_graph_projection import graph_arguments
    from cpn.rpnh.registry.module_nets import publish_module_net
    core,compiled,registration,args=graph_arguments(tmp_path,'basic')
    publication=publish_module_net(core,compiled,registration,**args)
    snap=snapshot(core,'source-a')
    def forbidden(*a,**k):raise AssertionError('projection performed live query or HOST call')
    monkeypatch.setattr(core.event_store,'connect',forbidden)
    monkeypatch.setattr(registration,'resolve',forbidden)
    result=read_public_projection(core,SourceQualifiedVersionRef('source-a',publication.net_ref),snapshot=snap)
    assert result['runtime']=={'coverage':'not_provided'}
    assert {n['id'] for n in result['nodes']}=={p.name for p in compiled.symbolic.places}|{t.name for t in compiled.symbolic.transitions}
    assert result['net_ref']==SourceQualifiedVersionRef('source-a',publication.net_ref).to_dict()


def test_real_checkpoint_projection_keeps_occurrence_refs_and_exact_commit(tmp_path,monkeypatch):
    from test_adoption_prefix import owner_replacement_fixture
    core,first,last=owner_replacement_fixture.__wrapped__(tmp_path,monkeypatch)
    rows=core.event_store.object_rows_by_type('marking_checkpoint/v1')
    assert rows
    row=rows[-1];ref=SourceQualifiedVersionRef('source-a',VersionRef('marking_checkpoint/v1',TypedId.parse(row['logical_id']),TypedId.parse(row['version_id'])))
    snap=snapshot(core,'source-a')
    result=read_public_projection(core,ref,snapshot=snap)
    assert result['runtime']['coverage']=='selected_checkpoint_marking'
    assert result['checkpoint_commit_ordinal']>0
    assert all(t['token_ref']['source_id']=='source-a' for t in result['runtime']['tokens'])
    assert not core.event_store.object_rows_by_type('transition_firing/v1')


def test_v2_all_environment_bytes_and_exact_dependency_archives_survive_collisions(tmp_path):
    from test_package_environment_requirements import requirements
    from test_share_packages_resolution import dependency
    from cpn.rpnh.collaboration.environment_requirements import read_package_environment
    core,author=world(tmp_path/'source','source-a');base=exported(core,author,publish(author)).package
    def package(identity,raw,dependencies=()):
        files=dict(base.artifacts);manifest=base.manifest
        old=next(path for path in files if path.startswith('subnet-exchange'))
        files.pop(old);manifest['artifacts']=[row for row in manifest['artifacts'] if row['path']!=old]
        manifest['provenance']=[row for row in manifest['provenance'] if row['artifact_path']!=old]
        path='closure/0000.json';files[path]=raw
        manifest['schema_version']='rpnh/share_package/v2';manifest['package_id']=identity;manifest['dependencies']=list(dependencies)
        manifest['entries'][0]['environment_requirements_path']=path
        row=next(row for row in manifest['artifacts'] if row['path']==path);row.update(bytes=len(raw),sha256=sha256(raw))
        # Non-entry inert environment artifact must survive too.
        extra='subnet-exchange.json';files[extra]=raw+b'\n'
        manifest['artifacts'].append(dict(row,path=extra,bytes=len(files[extra]),sha256=sha256(files[extra])))
        manifest['provenance'].append({'artifact_path':extra,'relation':'authored','origin_ref':None,'origin_digest':None})
        files['manifest.json']=canonical_bytes(manifest)
        return preview_package(_zip(files))
    left=requirements();left['python']['version_specifier']='>=3.11,<4'
    right=requirements();right['python']['version_specifier']='>=3.12,<4'
    leaf=package('example/leaf',json.dumps(right,indent=3).encode()+b'\n')
    root=package('example/root',json.dumps(left,indent=2).encode()+b'\n',[dependency(leaf)])
    dest,owner=world(tmp_path/'target','source-b')
    plan=plan_subnet_import(root,target=target(owner),local_packages=[leaf])
    result=import_subnet(owner,plan=plan,command_id='package-origin',expected_target_head=head(dest))
    assert all(row['origin']['kind']=='package_artifact' and 'revision_ref' not in row['origin'] for row in result.origin_to_local_map)
    exported1=exported(dest,owner,owner._validate(result.revision_ref))
    assert exported1.dependency_archives==(leaf.archive_bytes,)
    exported2=exported(dest,owner,owner._validate(result.revision_ref))
    assert exported1.archive_bytes==exported2.archive_bytes
    env=read_package_environment(exported1.package,exported1.dependency_archives)
    assert len(env.requirements)==2
    files=dict(exported1.package.artifacts)
    assert files['closure/0000.json']==dict(root.artifacts)['closure/0000.json']
    assert files['subnet-exchange.json']==dict(root.artifacts)['subnet-exchange.json']
    index=_exchange(exported1.package)
    preserved={(row['manifest_digest'],row['original_path']):files[row['artifact_path']] for row in index['preserved_environment_artifacts']}
    for p in (root,leaf):
        for name in ('closure/0000.json','subnet-exchange.json'):
            assert preserved[(p.manifest_digest,name)]==dict(p.artifacts)[name]
    final,other=world(tmp_path/'last','source-c')
    last=import_subnet(other,plan=plan_subnet_import(exported1,target=target(other)),command_id='reimport',expected_target_head=head(final))
    assert last.revision_ref.source_id=='source-c'


def test_real_independent_observer_session_exports_author_without_runtime(tmp_path):
    from cpn.rpnh.registry.observer_access import ObserverReadScope,issue_observer_access,revoke_observer_access
    from cpn.rpnh.collaboration.registry_read_contracts import ReadSessionRequest,ExplicitSources,SourceSelection,RegistryReadSessionError
    from cpn.rpnh.collaboration.registry_read_session import RegistryReadHostBinding,ExistingReadAuthorityProvider,open_registry_session,open_readonly_source
    core,author=world(tmp_path/'source','source-a',observer=True);value=publish(author)
    catalog=TypedReaderCatalog();fields={kind:tuple(catalog.fields(kind)) for kind in ('resource_version/v1','collaboration_net_revision/v1')}
    resources=tuple(SourceQualifiedResourceRef('source-a',ResourceVersionRef(TypedId.parse(row['logical_id']),TypedId.parse(row['version_id'])))
        for row in core.event_store.object_rows_by_type('resource_version/v1'))
    grant=issue_observer_access(author.gateway,principal_ref=author.producer.ref,
        scope=ObserverReadScope(fields,fields,resources,(*resources,value.revision.revision_ref),('local-review',)),
        purpose='exchange',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),command_id='observer:exchange')
    def resolver(source,access):return open_readonly_source(core.run_dir,catalog=core.catalog)
    session=open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(target(author).source_ref,'local'),)),'exchange'),
        host=RegistryReadHostBinding('test-user',resolver,ExistingReadAuthorityProvider({('test-user','source-a','local'):grant}),catalog))
    before=len(core.event_store.list_events());cut=session.capture_cut('source-a')
    plan=plan_subnet_export(session,root_revision_ref=value.revision.revision_ref,cuts={'source-a':cut})
    package=export_subnet(session,plan=plan,destination='local-review')
    assert package.package.manifest['entries'][0]['kind']=='closed_module'
    destination,owner=world(tmp_path/'target','source-b')
    imported=import_subnet(owner,plan=plan_subnet_import(package,target=target(owner)),
        command_id='legal-exchange',expected_target_head=head(destination))
    assert imported.revision_ref.source_id=='source-b'
    assert all(row['origin']['revision_ref']==value.revision.revision_ref.to_dict() for row in imported.origin_to_local_map)
    assert len(core.event_store.list_events())==before
    revoke_observer_access(author.gateway,grant_ref=grant.grant_ref,command_id='observer:revoke')
    with pytest.raises(RegistryReadSessionError,match='access'):
        export_subnet(session,plan=plan,destination='local-review')


def test_actual_split_fusion_projection_preserves_groups_without_host(tmp_path,monkeypatch):
    import test_collaboration_plain_transform as support
    inputs,author,_=support.fixture.__wrapped__(tmp_path,monkeypatch)
    core,gateway,registration,ordinary,analyzer,base,ids=inputs
    split=author.publish(**support.split_request(base))
    fused=author.publish(**support.fusion_request(split))
    snap=snapshot(core,author.binding['source_id'])
    def forbidden(*a,**k):raise AssertionError('read compiled')
    monkeypatch.setattr(registration,'resolve',forbidden)
    result=read_public_projection(core,split.revision.revision_ref,snapshot=snap)
    groups=[g for g in result['mapping_groups'] if g['relation_kind']=='split']
    assert groups and all(len(g['source_element_ids'])==1 and len(g['target_element_ids'])>=2 for g in groups)
    assert all(g['semantic_claim']=='author_correspondence' for g in groups)
    assert result['edge_roles']
    reverse=read_public_projection(core,fused.revision.revision_ref,snapshot=snap)
    assert any(g['relation_kind']=='fusion' and len(g['source_element_ids'])>=2 and len(g['target_element_ids'])==1 for g in reverse['mapping_groups'])


def test_actual_assembly_v9_public_mapping_reconstructed_without_host(tmp_path,monkeypatch):
    import test_typed_assembly_v9 as support
    core,gateway,registration,producer,author,composer=support.world(tmp_path/'source',context=True)
    member=support.publish_member(author,registration,'member',plain=True,budget=('left',5))
    request=support.request(member,claims=(support.PLAIN,support.PLAIN))
    assert request is not None
    value=composer.publish(**request)
    snap=snapshot(core,'source-typed-v9')
    def forbidden(*a,**k):raise AssertionError('assembly read invoked HOST')
    monkeypatch.setattr(registration,'resolve',forbidden)
    monkeypatch.setattr('cpn.rpnh.compiler.compile_module',forbidden)
    result=read_public_projection(core,value.revision.revision_ref,snapshot=snap)
    assert result['assembly_origins']['origins']==value.lowering_map['origins']
    assert 'member_proofs' not in result['assembly_origins'] and 'fragment_origins' not in result['assembly_origins']
    assert result['mapping_groups'] and all(g['semantic_claim']=='author_correspondence' for g in result['mapping_groups'])
    assert len(result['hierarchy'])>1


@pytest.mark.parametrize('field',['mapping_groups','mapping_closure','hierarchy','edge_roles'])
def test_owned_projection_does_not_certify_mutated_derived_claims(tmp_path,field):
    from cpn.rpnh.collaboration.public_projections import projection_resource_ref
    from test_candidate_plan_offline import _replace_resource_payload
    from test_candidate_plan_resources import mutate_resource_metadata
    core,author=world(tmp_path/'source','source-a');first=publish(author);second=publish(author,'second',first)
    ref=projection_resource_ref(core,second.revision.revision_ref,binding=author.binding)
    document=json.loads(core.object_store.path_for_version(ref.ref.resource_version_id).read_bytes())
    if field=='mapping_closure':document['projection'][field]['created_element_ids']=['element:'+'f'*32]
    else:document['projection'][field]=[]
    raw=canonical_json(document);_replace_resource_payload(core,ref.ref,raw)
    mutate_resource_metadata(core,ref.ref,lambda m:m['descriptors'].update(content_sha256=sha256(raw)))
    with pytest.raises(TypedReadError,match='INTEGRITY_FAILED'):
        read_public_projection(core,second.revision.revision_ref,snapshot=snapshot(core,'source-a'))


def test_forged_export_plan_cannot_omit_export_authority_refs(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author);session=SourceSession(core,'source-a')
    plan=plan_subnet_export(session,root_revision_ref=value.revision.revision_ref,cuts={'source-a':session.cut})
    document=plan.to_dict();document['included_refs']=[]
    with pytest.raises(SubnetExchangeError,match='PLAN_MISMATCH'):
        export_subnet(session,plan=replace(plan,payload=canonical_bytes(document)),destination='local-review')


def test_import_interrupted_after_author_replays_same_identity(tmp_path,monkeypatch):
    import cpn.rpnh.registry.resource_service as publisher
    core,author=world(tmp_path/'source','source-a');package=exported(core,author,publish(author))
    dest,owner=world(tmp_path/'target','source-b');plan=plan_subnet_import(package,target=target(owner));before=head(dest)
    original=publisher._publish_private_system
    def fail(core,task,command,**kwargs):
        if command.idempotency_key.endswith(':provenance'):raise RuntimeError('interruption')
        return original(core,task,command,**kwargs)
    monkeypatch.setattr(publisher,'_publish_private_system',fail)
    with pytest.raises(RuntimeError,match='interruption'):import_subnet(owner,plan=plan,command_id='recover',expected_target_head=before)
    rows=dest.event_store.object_rows_by_type('collaboration_net_revision/v1');assert len(rows)==1
    monkeypatch.setattr(publisher,'_publish_private_system',original)
    result=import_subnet(owner,plan=plan,command_id='recover',expected_target_head=before)
    assert str(result.revision_ref.ref.version_id)==rows[0]['version_id']
    assert len(dest.event_store.object_rows_by_type('collaboration_net_revision/v1'))==1


def test_optional_parent_mapping_denial_retains_exact_topology_without_mapping(tmp_path):
    core,author=world(tmp_path/'source','source-a');first=publish(author);second=publish(author,'second',first)
    snap=snapshot(core,'source-a',{canonical_bytes(first.revision.element_mapping_ref.to_dict())})
    graph=read_public_projection(core,second.revision.revision_ref,snapshot=snap)
    assert graph['nodes'] and graph['mapping_groups']==[] and graph['mapping_closure'] is None
    assert graph['mapping_coverage']=='not_provided'
    assert graph['target_ref']==second.revision.revision_ref.to_dict()


@pytest.mark.parametrize('location',['kind','root','file'])
def test_material_reader_rejects_symlink_redirects_at_every_store_component(tmp_path,location):
    core,author=world(tmp_path/'source','source-a');value=publish(author)
    ref=value.revision.definition_ref;snap=snapshot(core,'source-a')
    path=core.object_store.path_for_version(ref.ref.resource_version_id)
    selected={'kind':path.parent,'root':core.object_store.root,'file':path}[location]
    moved=selected.with_name(selected.name+'-moved');selected.rename(moved);selected.symlink_to(moved,target_is_directory=location!='file')
    with pytest.raises(TypedReadError,match='INTEGRITY_FAILED'):payload_at(core,ref,snap,material=True)


def test_import_same_command_changed_package_conflicts_without_new_objects(tmp_path):
    core,author=world(tmp_path/'source','source-a');first=exported(core,author,publish(author))
    dest,owner=world(tmp_path/'target','source-b')
    imported=import_subnet(owner,plan=plan_subnet_import(first,target=target(owner)),command_id='one',expected_target_head=head(dest))
    second=exported(core,author,publish(author,'another'))
    before=len(dest.event_store.list_events())
    with pytest.raises(RegistryConflict,match='conflicts'):
        import_subnet(owner,plan=plan_subnet_import(second,target=target(owner)),command_id='one',expected_target_head=head(dest))
    assert len(dest.event_store.list_events())==before


@pytest.mark.parametrize('selection',['history','checkpoints','results','open_region'])
def test_export_unsupported_selections_are_explicit_before_source_reads(tmp_path,selection):
    core,author=world(tmp_path/'source','source-a');value=publish(author);session=SourceSession(core,'source-a')
    before=len(core.event_store.list_events())
    with pytest.raises(SubnetExchangeError,match='UNSUPPORTED_EXPORT_SELECTION'):
        plan_subnet_export(session,root_revision_ref=value.revision.revision_ref,cuts={'source-a':session.cut},include=selection)
    assert len(core.event_store.list_events())==before


def test_export_missing_source_never_discovers_an_unselected_registry(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author);session=SourceSession(core,'source-a')
    with pytest.raises(SubnetExchangeError,match='MISSING_SOURCE'):
        plan_subnet_export(session,root_revision_ref=value.revision.revision_ref,cuts={})


def test_import_explicit_registry_head_and_bad_head_types(tmp_path):
    from cpn.rpnh.registry.resources import RegistryHead
    core,author=world(tmp_path/'source','source-a');package=exported(core,author,publish(author))
    dest,owner=world(tmp_path/'target','source-b');plan=plan_subnet_import(package,target=target(owner))
    for value in (True,0,{'ordinal':False,'writer_fencing_epoch':dest.writer_epoch},{'ordinal':dest.event_store.max_ordinal()}):
        with pytest.raises(SubnetExchangeError,match='INVALID_TARGET_HEAD'):
            import_subnet(owner,plan=plan,command_id='invalid',expected_target_head=value)
    expected=RegistryHead(dest.event_store.max_ordinal(),dest.writer_epoch,0,{})
    assert import_subnet(owner,plan=plan,command_id='typed-head',expected_target_head=expected).revision_ref.source_id=='source-b'


def test_resource_public_identity_and_catalog_field_types_are_not_aliased(tmp_path):
    core,author=world(tmp_path/'source','source-a');value=publish(author);catalog=TypedReaderCatalog()
    resource=value.revision.definition_ref
    wrong=SourceQualifiedVersionRef(resource.source_id,resource.ref.as_version_ref())
    with pytest.raises(TypedReadError,match='INVALID_REFERENCE'):catalog.read_exact(core,wrong,snapshot=snapshot(core,'source-a'))
    assert catalog.fields('resource_version/v1')['content_schema_ref']=='nullable_string'
    assert catalog.fields('collaboration_candidate_plan/v1')['schema_refs']=='object'
    assert catalog.fields('collaboration_candidate_plan/v1')['operation_refs']=='object'


@pytest.mark.parametrize('original,changed',[(True,1),(1,1.0),({'nested':[False,{'value':1}]},{'nested':[0,{'value':1.0}]}),(None,0)])
def test_exchange_inner_declaration_equality_preserves_nested_json_types(tmp_path,original,changed):
    core,author=world(tmp_path/'source','source-a')
    document=_simple_module().to_dict();document['terminal']['config']['probe']=original
    module=ModuleDeclaration.from_dict(document)
    value=author.publish(module=module,element_ids={key:'element:'+uuid.uuid4().hex for key in _elements(module)},command_id='typed-source')
    package=exported(core,author,value).package;manifest=package.manifest;files=dict(package.artifacts)
    path=manifest['entries'][0]['declaration_path'];declaration=json.loads(files[path])
    declaration['terminal']['config']['probe']=changed;files[path]=canonical_bytes(declaration)
    for row in manifest['artifacts']:
        if row['path']==path:row.update(bytes=len(files[path]),sha256=sha256(files[path]))
    files['manifest.json']=canonical_bytes(manifest)
    tampered=preview_package(_zip(files)) # Outer inventory remains valid.
    dest,owner=world(tmp_path/'target','source-b')
    before=len(dest.event_store.list_events())
    with pytest.raises(SubnetExchangeError,match='INTEGRITY_FAILED'):
        plan_subnet_import(tampered,target=target(owner))
    assert len(dest.event_store.list_events())==before


def test_imported_author_index_reads_only_requested_and_predicate_fields(tmp_path):
    """An index-only observer need not read the imported provenance resource."""
    from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
    from cpn.rpnh.collaboration.registry_read_contracts import (
        ReadSessionRequest, ExplicitSources, SourceSelection, RegistryReadSessionError,
        IndexQuery, TypedIndexClause, TypedPredicate,
    )
    from cpn.rpnh.collaboration.registry_read_session import (
        RegistryReadHostBinding, ExistingReadAuthorityProvider, open_registry_session, open_readonly_source,
    )

    source, original = world(tmp_path/'source', 'index-source')
    package = exported(source, original, publish(original))
    destination, owner = world(tmp_path/'target', 'index-target', observer=True)
    imported = import_subnet(owner, plan=plan_subnet_import(package, target=target(owner)),
        command_id='metadata-import', expected_target_head=head(destination))
    kind = 'collaboration_net_revision/v1'
    grant = issue_observer_access(owner.gateway, principal_ref=owner.producer.ref,
        scope=ObserverReadScope({kind: ('definition_kind', 'command_id')}, {}),
        purpose='index-only', expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        command_id='observer:index-only')
    host = RegistryReadHostBinding('index-user',
        lambda source_id, access: open_readonly_source(destination.run_dir, catalog=destination.catalog),
        ExistingReadAuthorityProvider({('index-user', 'index-target', 'local'): grant}), TypedReaderCatalog())
    request = ReadSessionRequest(ExplicitSources((SourceSelection(target(owner).source_ref, 'local'),)), 'index-only')
    before = len(destination.event_store.list_events())  # Independent read-only QA measurement.
    with open_registry_session(request, host=host) as session:
        query = IndexQuery(('index-target',), (TypedIndexClause(kind, projection=('definition_kind',)),))
        page = session.query_index(query)
        assert page['coverage']['state'] == 'complete'
        assert len(page['entries']) == 1
        assert page['entries'][0]['entry_ref'] == imported.revision_ref.to_dict()
        assert page['entries'][0]['fields'] == {'definition_kind': 'closed_module'}

        # Predicate fields must be loaded even when absent from the response;
        # a nonmatching clause must not add its projection to that response.
        selected = TypedIndexClause(kind, (TypedPredicate('definition_kind', 'eq', 'closed_module'),), ('command_id',))
        nonmatching = TypedIndexClause(kind, (TypedPredicate('definition_kind', 'eq', 'open_region'),), ('definition_kind',))
        filtered = session.query_index(IndexQuery(('index-target',), (selected, nonmatching)))
        assert len(filtered['entries']) == 1
        assert set(filtered['entries'][0]['fields']) == {'command_id'}
        assert filtered['entries'][0]['fields']['command_id']
        assert filtered['entries'][0]['disclosure']['projected_fields'] == ['command_id']

        matching = TypedIndexClause(kind, projection=('definition_kind',))
        combined = session.query_index(IndexQuery(('index-target',), (selected, matching)))
        assert set(combined['entries'][0]['fields']) == {'command_id', 'definition_kind'}
        hidden = TypedIndexClause(kind, (TypedPredicate('subnet_provenance_ref', 'eq', imported.provenance_ref.to_dict()),),
            ('definition_kind',))
        with pytest.raises(RegistryReadSessionError) as denied:
            session.query_index(IndexQuery(('index-target',), (hidden,)))
        assert denied.value.code == 'NOT_DISCLOSED'
        cut = session.capture_cut('index-target')
        with pytest.raises(RegistryReadSessionError) as denied:
            session.read_exact(entry_ref=imported.revision_ref, at_cut=cut, projection=('definition_kind',))
        assert denied.value.code == 'NOT_DISCLOSED'
        with pytest.raises(RegistryReadSessionError) as denied:
            session.read_material(resource_ref=imported.provenance_ref, at_cut=cut)
        assert denied.value.code == 'MATERIAL_ACCESS_NOT_GRANTED'
    assert len(destination.event_store.list_events()) == before
