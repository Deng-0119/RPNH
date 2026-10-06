"""Actual author-only Assembly v2 publication and read-only source-proof entry."""
from copy import deepcopy
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyMemberV2, AssemblyConnection, AssemblyCompletion,
    GraphModuleAuthor, SourceQualifiedVersionRef, ValidatedAssemblyRevisionV2,
    ValidatedClosedRevision, ValidatedGraphRevision, graph_assembly_schema_data,
    make_graph_source, read_assembly_revision, validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.collaboration.assembly_v2 import ASSEMBLY_V2_TYPE
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from test_collaboration_graph_materials import registration
from test_collaboration_graph_source import graph_wire, recipe, source_ids

A,B=('member:'+c*32 for c in 'ab')

@pytest.fixture
def fixture(tmp_path):
    schemas,types,paths=graph_assembly_schema_data()
    core=_RegistryCore(tmp_path/'assembly-v2',create=True,catalog=SchemaCatalog(schemas=schemas,types=types,schema_paths=paths))
    owner=_bootstrap_identity(core,NativeBootstrapManifest(('assembly-v2-test/v1',)))
    bootstrap=_version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway=RegistryRegistrationGateway(core,owner.task_ref,bootstrap)
    gateway.bind_source_identity(source_id='source-assembly-v2',command_id='bind')
    principal=VersionRef('principal/v1',new_id('principal'),new_id('principal_version'))
    body={'principal_id':str(principal.entity_id),'principal_version_id':str(principal.version_id),'display_name':'Assembly fixture'}
    core.publish_bytes(object_type=principal.entity_type,logical_id=principal.entity_id,version_id=principal.version_id,
        payload=canonical_json(body),metadata=body,media_type='application/json',schema_ref='registry_v1/principal/v1',idempotency_key='principal')
    producer=SourceQualifiedVersionRef('source-assembly-v2',principal)
    selected=registration();graph_author=GraphModuleAuthor(gateway,selected,producer)
    source=make_graph_source(graph_wire())
    member=graph_author.publish(source=source,recipe=recipe(),source_ids=source_ids(source),command_id='member')
    author=AssemblyAuthorV2(gateway,selected,producer)
    return core,gateway,author,selected,member

def request(member,**changes):
    ids={r['locator']:r['element_id'] for r in member.element_map['elements']}
    return {'name':'GraphPair','members':(AssemblyMemberV2(A,'Same',member.revision.revision_ref),AssemblyMemberV2(B,'Same',member.revision.revision_ref)),
        'connections':(AssemblyConnection(A,ids['/exit/result'],B,ids['/entry/request']),),
        'completion':AssemblyCompletion(B,ids['/terminal']),'budget_policy':'shared_exact',
        'deployment_intent':'same_run_candidate','command_id':'assembly:first',**changes}

def counts(core): return len(core.event_store.object_rows()),len(core.event_store.list_events())

def assert_no_run(core):
    assert core.event_store.object_rows_by_type('net_instance/v1')==()
    assert core.event_store.list_events_by_type(('net_adopted/v1','marking_checkpoint_committed/v1','firing_started/v1','execution_instance_created/v1'))==()


def test_real_same_graph_chain_publishes_seven_materials_reopens_and_proves_exact_pair(fixture):
    core,gateway,author,selected,member=fixture
    before={row['version_id'] for row in core.event_store.object_rows()}
    events_before=len(core.event_store.list_events())
    with core.event_store.connect() as db:
        transactions_before=db.execute("SELECT COUNT(*) FROM transactions WHERE status='committed'").fetchone()[0]
    value=author.publish(**request(member))
    created=[row for row in core.event_store.object_rows() if row['version_id'] not in before]
    assert len(created)==9
    assert sum(row['object_type']=='resource_version/v1' for row in created)==7
    assert sum(row['object_type']=='collaboration_net_revision/v1' for row in created)==1
    assert sum(row['object_type']==ASSEMBLY_V2_TYPE for row in created)==1
    with core.event_store.connect() as db:
        transactions_after=db.execute("SELECT COUNT(*) FROM transactions WHERE status='committed'").fetchone()[0]
    trace={'assembly_ref':value.revision.revision_ref.to_dict(),'member_ref':member.revision.revision_ref.to_dict(),
        'generated_ref':value.revision.generated_revision_ref.to_dict(),
        'new_resource_count':7,'new_descriptor_count':2,'committed_transactions_before':transactions_before,
        'committed_transactions_after':transactions_after,'events_before':events_before,'events_after':len(core.event_store.list_events()),
        'objects':[{'object_type':row['object_type'],'logical_id':row['logical_id'],'version_id':row['version_id'],
                    'transaction_id':row['transaction_id']} for row in created]}
    print('ASSEMBLY_V2_PUBLISH_TRACE='+json.dumps(trace,sort_keys=True))
    assert isinstance(value,ValidatedAssemblyRevisionV2)
    assert value.revision.generated_revision_ref==value.generated.revision.revision_ref
    assert len(value.generated.module.components)==2 and len(value.generated.module.links)==1
    assert len(value.generated.module.entry)==len(value.generated.module.exit)==1
    assert len(value.generated.module.budget_buckets)==len(member.module.budget_buckets)==2
    assert len(value.lowering_map['graph_source_origins'])==2*len(member.source_map['elements'])
    assert len(value.plan['prepared_materials'])==6
    refs=[value.generated.revision.definition_ref,value.generated.revision.element_mapping_ref,
        value.generated.revision.boundary_mapping_ref,value.generated.revision.host_requirements_ref,
        value.revision.compiled_inventory_ref,value.revision.lowering_mapping_ref]
    for signature,ref in zip(value.plan['prepared_materials'],refs,strict=True):
        assert signature['resource_ref']==ref.to_dict()
        payload=core.object_store.path_for_version(ref.ref.resource_version_id).read_bytes()
        assert signature['bytes']==len(payload)
        assert signature['sha256']==hashlib.sha256(payload).hexdigest()
    assert_no_run(core)
    after=counts(core)
    reopened=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    fresh=registration()
    assert read_assembly_revision(reopened,value.revision.revision_ref)==value.revision
    checked=validate_assembly_revision(reopened,value.revision.revision_ref,fresh)
    assert canonical_json(checked.generated.module.to_dict())==canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict())==canonical_json(value.compiled.to_dict())
    assert canonical_json(checked.lowering_map)==canonical_json(value.lowering_map)
    independent=validate_closed_revision(reopened,value.revision.generated_revision_ref,fresh)
    assert type(independent) is ValidatedClosedRevision
    assert not isinstance(independent,(ValidatedGraphRevision,ValidatedAssemblyRevisionV2))
    assert counts(core)==after
    assert author.publish(**request(member)).revision.revision_ref==value.revision.revision_ref
    assert counts(core)==after
    assert_no_run(core)


def test_real_source_payload_tamper_cannot_be_replaced_by_generated_v1(fixture):
    core,_,author,_,member=fixture
    value=author.publish(**request(member))
    source_path=core.object_store.path_for_version(member.revision.graph_source_ref.ref.resource_version_id)
    original=source_path.read_bytes()
    changed=original.replace(b'Draft the result.',b'Wrong the result.')
    assert changed!=original and len(changed)==len(original)
    source_path.write_bytes(changed)
    reopened=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    before=counts(core)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        validate_assembly_revision(reopened,value.revision.revision_ref,registration())
    # Independent generated-v1 still has only its original closed Module proof.
    independent=validate_closed_revision(reopened,value.revision.generated_revision_ref,registration())
    assert type(independent) is ValidatedClosedRevision
    assert counts(core)==before


@pytest.mark.parametrize('field',('binding_ref','task_ref','bootstrap_command_ref','native_run_ref'))
@pytest.mark.parametrize('damage',('missing','different_bytes'))
def test_local_authority_exact_payload_is_required_with_metadata_unchanged(fixture,field,damage):
    core,_,author,_,member=fixture
    value=author.publish(**request(member))
    binding=author.binding
    reference=binding[field]
    from cpn.rpnh.registry.identities import TypedId
    path=core.object_store.path_for_version(TypedId.parse(reference['version_id']))
    metadata=core.event_store.object_row(TypedId.parse(reference['version_id']))['metadata_json']
    if damage=='missing':
        path.unlink()
    else:
        body=json.loads(path.read_bytes())
        key={'binding_ref':'source_id','task_ref':'task_id','bootstrap_command_ref':'bootstrap_command_id','native_run_ref':'run_id'}[field]
        original=body[key];body[key]=original[:-1]+('a' if original[-1]!='a' else 'b')
        changed=canonical_json(body)
        assert len(changed)==path.stat().st_size
        path.write_bytes(changed)
    before=counts(core)
    reopened=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        validate_assembly_revision(reopened,value.revision.revision_ref,registration())
    assert core.event_store.object_row(TypedId.parse(reference['version_id']))['metadata_json']==metadata
    assert counts(core)==before


def test_four_local_authority_reads_are_exact_and_legacy_source_reader_is_unchanged(fixture,monkeypatch):
    core,_,author,_,_=fixture
    from cpn.rpnh.collaboration.assembly_v2 import _binding_at
    from cpn.rpnh.registry._event_store import collaboration_descriptors
    from cpn.rpnh.registry._event_store.source_identity import read_source_binding
    original=collaboration_descriptors.exact_descriptor;seen=[]
    def observe(db,store,task,reference):
        seen.append(dict(reference))
        return original(db,store,task,reference)
    monkeypatch.setattr(collaboration_descriptors,'exact_descriptor',observe)
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        binding=_binding_at(db,core)
        legacy=json.loads(read_source_binding(db,core.catalog,core.task_id)['binding_metadata_json'])
    assert binding==legacy==author.binding
    assert seen==[binding[field] for field in ('binding_ref','task_ref','native_run_ref','bootstrap_command_ref')]
    print('ASSEMBLY_V2_LOCAL_EXACT_AUTHORITY_READS='+json.dumps(seen,sort_keys=True))


@pytest.mark.parametrize('limit',(None,12))
def test_real_feedback_and_nullable_budget_are_source_verified(fixture,limit):
    core,gateway,author,selected,_=fixture
    source=make_graph_source(graph_wire(True))
    graph_author=GraphModuleAuthor(gateway,selected,author.producer)
    member=graph_author.publish(source=source,recipe=recipe(max_attempts_per_node=limit),source_ids=source_ids(source),command_id='feedback-member')
    value=author.publish(**request(member))
    reopened=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    checked=validate_assembly_revision(reopened,value.revision.revision_ref,registration())
    assert canonical_json(checked.lowering_map)==canonical_json(value.lowering_map)
    assert len(value.generated.module.budget_buckets)==3  # Shared_exact, not 6 isolated buckets.
    for fragment in value.compiled.fragments.values():
        assert sum(p.name=='control__workflow_rework_permit' for p in fragment.places)==1
    assert_no_run(core)


def setup_context_plain(fixture):
    from dataclasses import replace
    from cpn.components.basic import CONFIG_SCHEMA,CONFIG_SCHEMA_ID,lower_operation
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.petri_contracts import OperationDeclaration,OutcomeDeclaration,TransitionDeclaration
    from cpn.rpnh.collaboration.materials import _elements
    from test_collaboration_graph_materials import forbidden_execution
    from test_collaboration_assembly_v2_projection import simple_plain_module
    import uuid
    core,gateway,author,selected,graph=fixture
    selected.register_schema(CONFIG_SCHEMA_ID,CONFIG_SCHEMA)
    selected.register_executor('test/final-only/v1',forbidden_execution,
        identity={'implementation_id':'test.final-only','revision':'v1'},contracts={'config_schema':'application/graph_test_config/v1'})
    selected.register_tool('test/plain-terminal/v1',forbidden_execution,
        identity={'implementation_id':'test.plain-terminal','revision':'v1'},contracts={})
    def lower(config,context):
        fragment=lower_operation(config,context)
        if context.component.startswith('m_'):
            operation=OperationDeclaration('final_context_only','test/final-only/v1',(),(),(OutcomeDeclaration('complete',()),))
            return replace(fragment,operations=(*fragment.operations,operation),
                transitions=(*fragment.transitions,TransitionDeclaration('final_context_only','final_context_only')))
        return fragment
    selected.register_component('test/context-plain/v1',lower,
        identity={'implementation_id':'test.context-plain','revision':'v1'},contracts={'config_schema':CONFIG_SCHEMA_ID})
    d=simple_plain_module().to_dict();d['components'][0]['key']='test/context-plain/v1';d['terminal']['key']='test/plain-terminal/v1'
    module=ModuleDeclaration.from_dict(d)
    ids={key:'element:'+uuid.uuid4().hex for key in _elements(module)}
    plain=author.author.publish(module=module,element_ids=ids,command_id='plain')
    graph_ids={r['locator']:r['element_id'] for r in graph.element_map['elements']}
    args=request(graph,members=(AssemblyMemberV2(A,'Graph',graph.revision.revision_ref),AssemblyMemberV2(B,'Plain',plain.revision.revision_ref)),
        connections=(AssemblyConnection(A,graph_ids['/exit/result'],B,ids['/entry/request']),),completion=AssemblyCompletion(B,ids['/terminal']))
    return args,plain


@pytest.mark.parametrize('missing',(False,True))
def test_final_used_host_is_exact_preexisting_and_not_the_union_of_members(fixture,missing):
    core,gateway,author,selected,graph=fixture
    args,plain=setup_context_plain(fixture)
    assert 'test/final-only/v1' not in plain.host_requirements['registrations']['executor']
    reference=gateway.declaration_refs['executor','test/final-only/v1']
    if missing:
        core.object_store.path_for_version(reference.resource_version_id).unlink()
        before=counts(core)
        with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
            author.publish(**args)
        assert counts(core)==before  # No HOST repair or plan write is allowed.
        assert core.event_store.object_rows_by_type(ASSEMBLY_V2_TYPE)==()
        return
    value=author.publish(**args)
    assert 'test/final-only/v1' in value.plan['host_requirements']['registrations']['executor']
    assert 'test/graph-terminal/v1' not in value.plan['host_requirements']['registrations']['tool']
    assert 'test/plain-terminal/v1' in value.plan['host_requirements']['registrations']['tool']
    assert any(o.declaration.name.endswith('.final_context_only') for o in value.compiled.operations)
    before=counts(core)
    reopened=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    checked=validate_assembly_revision(reopened,value.revision.revision_ref,selected)
    assert canonical_json(checked.compiled.to_dict())==canonical_json(value.compiled.to_dict())
    assert counts(core)==before
    assert_no_run(core)


def test_legacy_assembly_entry_and_invalid_reference_behavior_remain_unchanged(fixture):
    import uuid
    from cpn.components.basic import register_basic_components
    from cpn.rpnh.collaboration import AssemblyAuthor,AssemblyMember,ValidatedAssemblyRevision
    from cpn.rpnh.collaboration.materials import _elements
    from test_collaboration_assembly_v2_projection import simple_plain_module
    core,gateway,new_author,selected,_=fixture
    register_basic_components(selected)
    old=AssemblyAuthor(gateway,selected,new_author.producer)
    module=simple_plain_module();ids={k:'element:'+uuid.uuid4().hex for k in _elements(module)}
    plain=old.author.publish(module=module,element_ids=ids,command_id='legacy-plain')
    value=old.publish(name='LegacyPair',members=(AssemblyMember(A,'A',plain.revision.revision_ref),AssemblyMember(B,'B',plain.revision.revision_ref)),
        connections=(AssemblyConnection(A,ids['/exit/result'],B,ids['/entry/request']),),completion=AssemblyCompletion(B,ids['/terminal']),
        budget_policy='shared_exact',deployment_intent='same_run_candidate',command_id='legacy-assembly')
    assert type(validate_assembly_revision(core,value.revision.revision_ref,selected)) is ValidatedAssemblyRevision
    assert read_assembly_revision(core,value.revision.revision_ref)==value.revision
    before=counts(core)
    for bad in (None,'invalid',object()):
        with pytest.raises(TypeError): read_assembly_revision(core,bad)
        with pytest.raises(TypeError): validate_assembly_revision(core,bad,selected)
    assert counts(core)==before
