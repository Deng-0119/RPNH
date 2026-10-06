"""Pure v2 contracts/projection. Synthetic refs here are NOT Registry proof.

Real publication/reopen is a separate vertical acceptance stage. Only trusted
ordinary lower runs; no Registry, executor, tool, plugin, or provider is started.
"""
from copy import deepcopy
from dataclasses import replace
import uuid

import pytest

from cpn.rpnh.collaboration import (
    AssemblyMember, AssemblyMemberV2, AssemblyRevisionV2, AssemblyConnection,
    AssemblyCompletion, SourceQualifiedVersionRef, SourceQualifiedResourceRef,
    graph_assembly_schema_data, assembly_schema_data, graph_author_schema_data,
)
from cpn.rpnh.collaboration.assembly_v2 import (
    ASSEMBLY_V2_TYPE, ASSEMBLY_V2_SCHEMA, PLAN_V2_SCHEMA, LOWERING_V2_SCHEMA,
    resolver_recipe, material_signature, _command,
)
from cpn.rpnh.collaboration._assembly_v2_lowering import (
    compose_declarations, compose_plan_v2, lowering_map_v2, check_lowering_map_v2,
)
from cpn.rpnh.collaboration._assembly_lowering import prefix, resolve_pointer
from cpn.rpnh.collaboration.graph_authoring import GraphNetRevision, ValidatedGraphRevision
from cpn.rpnh.collaboration.graph_source import (
    make_graph_source, rebuild_graph_module, graph_source_elements, make_graph_source_map, graph_element_map,
)
from cpn.rpnh.collaboration.materials import _boundaries, _elements, _element_map, ValidatedClosedRevision
from cpn.rpnh.collaboration.authoring import NetRevision
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from test_collaboration_graph_source import graph_wire, recipe
from test_collaboration_graph_materials import registration

A, B = ('member:'+v*32 for v in 'ab')
SOURCE='source-pure'

def tid(kind, seed): return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, seed).hex)
def ref(entity='collaboration_net_revision/v2', seed='graph'):
    kinds={'task/v1':('task','task_version'),'principal/v1':('principal','principal_version')}.get(entity,('resource','resource_version'))
    return SourceQualifiedVersionRef(SOURCE,VersionRef(entity,tid(kinds[0],seed),tid(kinds[1],seed+':version')))
def resource(seed):
    return SourceQualifiedResourceRef(SOURCE,ResourceVersionRef(tid('resource',seed),tid('resource_version',seed+':version')))

def member(*, feedback=False, limit=12, seed='graph', wire=None):
    source=make_graph_source(wire or graph_wire(feedback));options=recipe(max_attempts_per_node=limit)
    module=rebuild_graph_module(source,options)
    ids={k:'element:'+uuid.uuid5(uuid.NAMESPACE_URL,'source:'+k).hex for k in graph_source_elements(source)}
    source_map=make_graph_source_map(source,ids)
    elements=graph_element_map(module,source,source_map)
    rev=GraphNetRevision(ref(seed=seed),ref('task/v1','owner'),ref('principal/v1','producer'),seed,(),
        *(resource(seed+':'+str(i)) for i in range(7)))
    # Deliberately no standalone compiled net: assembly must use final fragments.
    return ValidatedGraphRevision(rev,module,None,elements,_boundaries(module,elements),{},source,options,source_map)

def simple_plain_module():
    from cpn.components.basic import CONFIG_SCHEMA_ID
    text='application/rpnh_agent_text/v1'
    binding={'bucket_id':'work','budget_scope':'module','finalization_scope':None}
    return ModuleDeclaration.from_dict({
        'schema_version':'rpnh/module_declaration/v1','name':'Plain',
        'components':[{'name':'step','key':'operation','config_schema':CONFIG_SCHEMA_ID,'config':{},
            'ports':[{'name':'request','direction':'input','schema':text},{'name':'result','direction':'output','schema':text}],
            'operations':[{'name':'run','executor':'test/ordinary-agent/v1','inputs':['request'],'outputs':['result'],
                'request_port':None,'tools':[],'config':{},'budget_binding':binding,
                'outcomes':[{'name':'complete','products':[{'port':'result'}]}]}]}],
        'links':[],'entry':{'request':{'component':'step','port':'request'}},
        'exit':{'result':{'component':'step','port':'result'}},
        'terminal':{'key':'test/graph-terminal/v1','source':{'component':'step','port':'result'},'operation':'run','outcome':'complete','config':{}},
        'required_schemas':[CONFIG_SCHEMA_ID,text,'application/graph_test_config/v1'],
        'budgets':{},'budget_buckets':[{**binding,'max_attempts':3}]})


def plain_member(module):
    revision=NetRevision(ref('collaboration_net_revision/v1','plain'),ref('task/v1','owner'),ref('principal/v1','producer'),
        'plain','closed_module',resource('plain:def'),(),(),resource('plain:elements'),resource('plain:boundary'),resource('plain:host'),None)
    ids={k:'element:'+uuid.uuid5(uuid.NAMESPACE_URL,'plain:'+k).hex for k in _elements(module)}
    elements=_element_map(module,ids,None,{})
    return ValidatedClosedRevision(revision,module,None,elements,_boundaries(module,elements),{})


def plan(values, *, connect=True):
    ids=lambda m:{r['locator']:r['element_id'] for r in m.element_map['elements']}
    return {'name':'Pair','members':[AssemblyMemberV2(k,'Same',v.revision.revision_ref).to_dict() for k,v in sorted(values.items())],
        'connections':([AssemblyConnection(A,ids(values[A])['/exit/result'],B,ids(values[B])['/entry/request']).to_dict()] if connect else []),
        'completion':AssemblyCompletion(B,ids(values[B])['/terminal']).to_dict(),
        'budget_policy':'shared_exact','deployment_intent':'same_run_candidate','resolver_recipe':resolver_recipe()}

def catalog():
    schemas,types,paths=graph_assembly_schema_data()
    return SchemaCatalog(schemas=schemas,types=types,schema_paths=paths)

def projected(*,feedback=False,limit=12,connect=True):
    m=member(feedback=feedback,limit=limit);members={A:m,B:m};p=plan(members,connect=connect)
    module,compiled=compose_plan_v2(p,members,registration())
    mapping,ids=lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)
    return p,members,module,compiled,mapping,ids

@pytest.mark.parametrize('feedback',[False,True])
@pytest.mark.parametrize('limit',[None,1,12])
@pytest.mark.parametrize('connect',[False,True])
def test_actual_graph_projection_covers_source_and_every_primitive(feedback,limit,connect):
    p,members,module,compiled,mapping,ids=projected(feedback=feedback,limit=limit,connect=connect)
    catalog().validate_schema_ref(LOWERING_V2_SCHEMA,mapping)
    assert len(mapping['graph_source_origins'])==2*len(members[A].source_map['elements'])
    assert len(ids)==len(mapping['declarations'])
    for row in mapping['graph_source_origins']:
        if row['source_kind'] in {'input_port','output_port','arc'}:
            assert row['declaration_locators']==[]
            assert row['representation']=='field_and_lowering_role'
        for target in row['declaration_field_targets']: resolve_pointer(module.to_dict(),target)
        for role in row['compiled_roles']:
            resolve_pointer(compiled.to_dict(),role['fragment_pointer'])
            for target in role['compiled_targets']: resolve_pointer(compiled.to_dict(),target)
    for coverage in mapping['graph_fragment_coverage']:
        fragment=compiled.to_dict()['fragments'][coverage['component']]
        expected=sum(len(fragment[k]) for k in ('places','transitions','arcs','ports','operations','internal_ports','internal_bindings'))
        assert len(coverage['elements'])==expected
        assert all(row['origins'] for row in coverage['elements'])
    if connect:
        assert compiled.symbolic.port_places[prefix(A)+'_team.result']==compiled.symbolic.port_places[prefix(B)+'_team.request']
    assert len(module.budget_buckets)==len(members[A].module.budget_buckets)
    assert module.designer_constraints['assembly_member_constraints']['members']==[
        {'member_id':key,'constraints':members[key].module.to_dict()['designer_constraints']} for key in (A,B)]
    assert 'max_rework_cycles' not in module.designer_constraints


def test_label_and_input_wire_order_have_no_generated_identity_semantics():
    p,members,module,compiled,mapping,_=projected(feedback=True)
    p['members'][0]['display_name']='Changed'
    other,net=compose_plan_v2(p,members,registration())
    actual,_=lowering_map_v2(p,members,other,net,'resource:'+'c'*32)
    assert canonical_json(module.to_dict())==canonical_json(other.to_dict())
    assert canonical_json(compiled.to_dict())==canonical_json(net.to_dict())
    assert canonical_json(mapping)==canonical_json(actual)
    wire=graph_wire(True);wire['nodes'].reverse();wire['arcs'].reverse()
    for n in wire['nodes']: n['input_ports'].reverse();n['output_ports'].reverse()
    alternate=member(wire=wire)
    assert alternate.source!=members[A].source
    assert alternate.source_map==members[A].source_map

@pytest.mark.parametrize('change',['policy','deployment','recipe','constraints','binding','limit','legacy'])
def test_member_and_resolver_semantics_fail_closed(change):
    m=member(feedback=True);members={A:m,B:m};p=plan(members)
    if change=='policy': p['budget_policy']='isolated'
    elif change=='deployment': p['deployment_intent']='independent_run'
    elif change=='recipe': p['resolver_recipe']['local_graph_symbols']='rename/v1'
    elif change=='limit': members[B]=member(feedback=True,limit=1,seed='other');p=plan(members)
    elif change=='legacy':
        members[B]=plain_member(m.module)
        p=plan(members)
    else:
        d=m.module.to_dict()
        if change=='constraints': d['designer_constraints']={}
        else: d['components'][0]['operations'][0]['budget_binding']['budget_scope']='wrong'
        members[B]=replace(m,module=ModuleDeclaration.from_dict(d))
    with pytest.raises((ValueError,TypeError)): compose_plan_v2(p,members,registration())

@pytest.mark.parametrize('damage',['drop_source','duplicate_source','wrong_member','unrelated_pointer','false_element','role','coverage','fusion'])
def test_recomputed_projection_rejects_schema_shaped_false_maps(damage):
    p,members,module,compiled,mapping,_=projected(feedback=True)
    bad=deepcopy(mapping);rows=bad['graph_source_origins']
    if damage=='drop_source': rows.pop()
    elif damage=='duplicate_source': rows.append(deepcopy(rows[0]))
    elif damage=='wrong_member': rows[0]['member_id']=B if rows[0]['member_id']==A else A
    elif damage=='unrelated_pointer': rows[0]['compiled_roles'][0]['compiled_targets']=['/symbolic']
    elif damage=='false_element': next(r for r in rows if r['source_kind']=='input_port')['declaration_locators']=['/terminal']
    elif damage=='role': rows[0]['compiled_roles'][0]['role']='egress'
    elif damage=='coverage': bad['graph_fragment_coverage'][0]['elements'].pop()
    else: bad['place_aliases'][0]['compiled_place']='another_actual_place'
    with pytest.raises(ValueError,match='origin map differs'):
        check_lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32,bad)


def test_unrelated_actual_graph_primitive_is_not_hidden_by_root_aggregate():
    from cpn.rpnh.petri_contracts import PlaceDeclaration
    m=member();members={A:m,B:m};p=plan(members);r=registration()
    base=r.resolve('component','rpnh/agent-workflow-graph/v3')
    def lower(config,context):
        original=base(config,context)
        return replace(original,places=(*original.places,PlaceDeclaration('unexplained','application/rpnh_agent_text/v1')))
    r._callables['component']['rpnh/agent-workflow-graph/v3']=lower
    module,compiled=compose_plan_v2(p,members,r)
    with pytest.raises(ValueError,match='no specific source role'):
        lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)


def test_feedback_shared_permit_and_interrupt_have_real_roles_only():
    _,_,_,compiled,mapping,_=projected(feedback=True)
    roots=[r for r in mapping['graph_source_origins'] if r['source_kind']=='graph']
    assert all({x['role'] for x in r['compiled_roles']}=={'component','permit','permit_seed','permit_consume'} for r in roots)
    for c,fragment in compiled.to_dict()['fragments'].items():
        permit=[a for a in fragment['arcs'] if a['place']=='control__workflow_rework_permit']
        assert not any(a['outcome']=='interrupted' for a in permit)
    rows=[r for r in mapping['graph_source_origins'] if r['source_kind']=='input_port']
    assert all(any(x['role']=='interrupt' for x in row['compiled_roles']) for row in rows)


def test_catalog_and_v1_acceptance_are_explicitly_separate():
    for loader in (assembly_schema_data,graph_author_schema_data):
        schemas,types,_=loader();assert ASSEMBLY_V2_SCHEMA not in schemas
        assert ASSEMBLY_V2_TYPE not in {t.name for t in types}
    c=catalog();c.require(ASSEMBLY_V2_TYPE,category='object')
    with pytest.raises((TypeError,ValueError)): AssemblyMember(A,'Graph',ref())
    assert AssemblyMemberV2(A,'Graph',ref()).revision_ref==ref()
    with pytest.raises(SchemaGovernanceError): SchemaCatalog().require(ASSEMBLY_V2_TYPE,category='object')
    assert _command('same')!='collaboration-assembly:{"command_id":"same"}'


def test_v2_record_does_not_accept_v1_parent_or_graph_generated_revision():
    args=[ref(ASSEMBLY_V2_TYPE,'assembly'),ref('task/v1','owner'),ref('principal/v1','producer'),'command',None,
          resource('plan'),ref('collaboration_net_revision/v1','generated'),resource('compiled'),resource('mapping')]
    valid=AssemblyRevisionV2(*args);c=catalog()
    assert AssemblyRevisionV2.from_dict(valid.to_dict(),catalog=c)==valid
    for field,value in ((4,ref('collaboration_assembly_revision/v1')),(6,ref())):
        bad=list(args);bad[field]=value
        with pytest.raises((TypeError,ValueError)): AssemblyRevisionV2(*bad)
    bad=valid.to_dict();bad['extra']='unsupported'
    with pytest.raises(SchemaGovernanceError): AssemblyRevisionV2.from_dict(bad,catalog=c)


def test_plan_schema_locks_resolution_recipe_and_six_exact_product_refs():
    m=member();p=plan({A:m,B:m});p.update({'schema_version':PLAN_V2_SCHEMA,'source_id':SOURCE,
        'owner_task_ref':ref('task/v1','owner').to_dict(),'producer_principal_ref':ref('principal/v1','producer').to_dict(),
        'command_id':'plan','parent_revision_ref':None,'host_requirements':{'schema_version':'rpnh/collaboration/author_host_requirements/v1','registrations':{},'declaration_refs':[]},
        'generated_revision_ref':ref('collaboration_net_revision/v1','generated').to_dict()})
    for row in p['members']:
        row['resolution']={'kind':'ordinary_graph_v2',**{f:getattr(m.revision,f).to_dict() for f in (
            'graph_source_ref','graph_recipe_ref','graph_source_mapping_ref','definition_ref','element_mapping_ref','boundary_mapping_ref','host_requirements_ref')}}
    schemas=('rpnh/module_declaration/v1','rpnh/collaboration/author_element_map/v1','rpnh/collaboration/author_boundary_map/v1','rpnh/collaboration/author_host_requirements/v1','rpnh/executable_net/v1','rpnh/collaboration/assembly_lowering_map/v2')
    p['prepared_materials']=[material_signature(role,schema,resource(role),{'fixture':role})
        for role,schema in zip(('definition','element_mapping','boundary_mapping','host_requirements','compiled_inventory','lowering_mapping'),schemas)]
    c=catalog();c.validate_schema_ref(PLAN_V2_SCHEMA,p)
    for key in ('resolver_recipe','prepared_materials','generated_revision_ref'):
        bad=deepcopy(p);del bad[key]
        with pytest.raises(SchemaGovernanceError): c.validate_schema_ref(PLAN_V2_SCHEMA,bad)
    wrong=deepcopy(p);wrong['prepared_materials'].reverse()
    with pytest.raises(SchemaGovernanceError): c.validate_schema_ref(PLAN_V2_SCHEMA,wrong)
    bad=deepcopy(p);bad['resolver_recipe']['budget_policy']='isolated'
    with pytest.raises(SchemaGovernanceError): c.validate_schema_ref(PLAN_V2_SCHEMA,bad)


def test_mixed_plain_actual_final_context_is_used_without_member_precompile():
    from cpn.components.basic import CONFIG_SCHEMA, CONFIG_SCHEMA_ID, lower_operation
    from cpn.rpnh.petri_contracts import PlaceDeclaration
    r=registration();r.register_schema(CONFIG_SCHEMA_ID,CONFIG_SCHEMA)
    def lower(config,context):
        fragment=lower_operation(config,context)
        return replace(fragment,places=(*fragment.places,PlaceDeclaration('derived_'+context.component,'application/rpnh_agent_text/v1')))
    r.register_component('context-derived',lower,identity={'implementation_id':'test.context-derived','revision':'v1'},contracts={'config_schema':CONFIG_SCHEMA_ID})
    document=simple_plain_module().to_dict()
    document['components'][0]['key']='context-derived'
    document['components'][0]['operations'][0]['executor']='test/ordinary-agent/v1'
    document['terminal']['key']='test/graph-terminal/v1'
    members={A:member(),B:plain_member(ModuleDeclaration.from_dict(document))};p=plan(members)
    module,compiled=compose_plan_v2(p,members,r)
    mapping,_=lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)
    assert all(row['member_id']==A for row in mapping['graph_source_origins'])
    plain_component=prefix(B)+'_step'
    assert any(place.name=='derived_'+plain_component for place in compiled.fragments[plain_component].places)
    assert not any(place.name=='derived_step' for place in compiled.fragments[plain_component].places)
    assert all(value.compiled is None for value in members.values())


def test_fanout_source_arcs_share_exact_data_route_without_fake_declarations():
    wire=graph_wire()
    left=deepcopy(wire['nodes'][1]);left['node_id']='left';left['output_ports']=[{'port_id':'left','artifact_id':'left'}]
    right=deepcopy(left);right['node_id']='right';right['output_ports']=[{'port_id':'right','artifact_id':'right'}]
    join=deepcopy(wire['nodes'][1]);join['node_id']='join';join['input_ports']=[{'port_id':s,'artifact_id':s} for s in ('left','right')]
    wire['nodes']=[wire['nodes'][0],left,right,join]
    def edge(name,s,sp,t,tp):
        return {'arc_id':name,'source':{'node_id':s,'port_id':sp},'target':{'node_id':t,'port_id':tp},'kind':'dependency'}
    wire['arcs']=[edge('to_left','draft','candidate','left','candidate'),edge('to_right','draft','candidate','right','candidate'),
        edge('left_join','left','left','join','left'),edge('right_join','right','right','join','right')]
    wire['egress']['node_id']='join'
    m=member(wire=wire);members={A:m,B:m};p=plan(members)
    module,compiled=compose_plan_v2(p,members,registration());mapping,_=lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)
    rows=[r for r in mapping['graph_source_origins'] if r['member_id']==A and r['source_locator'] in ('/arcs/to_left','/arcs/to_right')]
    pointers=[next(r['fragment_pointer'] for r in row['compiled_roles'] if r['role']=='edge_source_data') for row in rows]
    assert len(pointers)==2 and pointers[0]==pointers[1]
    assert resolve_pointer(compiled.to_dict(),pointers[0])['weight']==2
    assert all(row['declaration_locators']==[] for row in rows)


def test_multiple_feedback_inputs_consume_one_graph_permit():
    wire=graph_wire(True)
    wire['nodes'][0]['input_ports'].append({'port_id':'other','artifact_id':'other'})
    wire['nodes'][1]['output_ports'].append({'port_id':'other','artifact_id':'other'})
    wire['arcs'].append({'arc_id':'other_feedback','source':{'node_id':'review','port_id':'other'},
        'target':{'node_id':'draft','port_id':'other'},'kind':'feedback'})
    m=member(wire=wire);members={A:m,B:m};p=plan(members)
    module,compiled=compose_plan_v2(p,members,registration());mapping,_=lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)
    for fragment in compiled.to_dict()['fragments'].values():
        assert sum(a['place']=='control__workflow_rework_permit' and a['direction']=='input' for a in fragment['arcs'])==1
        assert next(o for o in fragment['operations'] if o['name']=='draft__rework')['inputs']==['input__draft__changes','input__draft__other']
    assert len(mapping['graph_fragment_coverage'])==2

@pytest.mark.parametrize('damage',['missing','duplicated'])
def test_actual_source_inventory_must_be_exact_before_projection(damage):
    m=member();source_map=deepcopy(m.source_map)
    if damage=='missing': source_map['elements'].pop()
    else: source_map['elements'].append(deepcopy(source_map['elements'][0]))
    members={A:replace(m,source_map=source_map),B:m};p=plan(members)
    module,compiled=compose_plan_v2(p,members,registration())
    with pytest.raises(ValueError,match='exact unique source coverage'):
        lowering_map_v2(p,members,module,compiled,'resource:'+'c'*32)


def test_plain_binding_requires_own_bucket_and_exact_scope():
    doc=simple_plain_module().to_dict();doc['components'][0]['operations'][0]['budget_binding']['budget_scope']='elsewhere'
    bad=plain_member(ModuleDeclaration.from_dict(doc));members={A:bad,B:bad};p=plan(members)
    with pytest.raises(ValueError,match='binding differs'):
        compose_declarations(p,members)


def test_projection_requires_exact_module_compiled_pair():
    p,members,module,compiled,_,_=projected()
    document=module.to_dict();document['name']='Different'
    with pytest.raises(ValueError,match='exact compiled Module pair'):
        lowering_map_v2(p,members,ModuleDeclaration.from_dict(document),compiled,'resource:'+'c'*32)
