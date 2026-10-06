"""Canonical hostile child claims and root-only HOST carrier changes."""
from copy import deepcopy
from dataclasses import replace
import uuid
import pytest
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyAuthorV3, AssemblyMemberV2, AssemblyMemberV3,
    AssemblyConnection, AssemblyCompletion, SourceQualifiedVersionRef,
    validate_assembly_revision, validate_closed_revision, read_assembly_revision,
)
from cpn.rpnh.collaboration import assembly_v3 as assembly
from cpn.rpnh.collaboration._assembly_lowering import prefix
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v3 import setup_fixture, request, publish_pair, A, B, C, D
from test_collaboration_assembly_v2_publication import counts, assert_no_run
from test_collaboration_assembly_v2_origins import _publish_descriptor
from test_collaboration_graph_materials import registration


@pytest.fixture
def fixture(tmp_path):
    return setup_fixture(tmp_path)


def prepare(author, args):
    core = author.core
    plan = {'schema_version': assembly.PLAN_V3_SCHEMA, 'source_id': author.binding['source_id'],
        'owner_task_ref': SourceQualifiedVersionRef(author.binding['source_id'], author.gateway._task_ref).to_dict(),
        'producer_principal_ref': author.producer.to_dict(), 'command_id': args['command_id'],
        'parent_revision_ref': None, 'name': args['name'],
        'members': sorted((row.to_dict() for row in args['members']), key=lambda row: row['member_id']),
        'connections': sorted((row.to_dict() for row in args['connections']), key=lambda row: canonical_json(row)),
        'completion': args['completion'].to_dict(), 'budget_policy': args['budget_policy'],
        'deployment_intent': args['deployment_intent'], 'resolver_recipe': assembly.resolver_recipe()}
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        binding = assembly._binding_at(db, core)
        members = assembly._members_at(db, core, plan, author.registration, binding, locked=False, visiting=set())
        return assembly._prepare_at(db, core, plan, members, None, author.registration, binding)


@pytest.mark.parametrize('axis', ('contents', 'reference'))
def test_canonical_child_generated_pair_mismatch_plain_validator_still_passes(fixture, axis):
    core, gateway, selected, producer, graph = fixture
    author = AssemblyAuthorV3(gateway, selected, producer)
    prepared = prepare(author, request(graph, command='claim:child-pair', pair=False))
    plan, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = prepared
    key = assembly._command(plan['command_id'])
    plan_ref = author._publish_document(key + ':plan', assembly.PLAN_V3_SCHEMA, plan,
        descriptors={'assembly_author_command_v3': assembly._envelope(plan, docs)})
    wrong_ids = {locator: 'element:' + uuid.uuid4().hex for locator in ids}
    generated = author.author.publish(module=module, element_ids=wrong_ids if axis == 'contents' else ids,
        command_id=generated_command if axis == 'contents' else generated_command + ':same-content-substitute')
    if axis == 'contents':
        assert generated.revision.revision_ref == generated_ref
    else:
        assert generated.revision.revision_ref != generated_ref
        assert canonical_json(generated.module.to_dict()) == canonical_json(module.to_dict())
        assert canonical_json(generated.element_map) == canonical_json(docs[1])
    compiled_ref = author._publish_document(key + ':compiled', 'rpnh/executable_net/v1', compiled.to_dict())
    map_ref = author._publish_document(key + ':lowering', assembly.LOWERING_V3_SCHEMA, mapping)
    record = assembly.AssemblyRevisionV3(reference, graph.revision.owner_task_ref, producer,
        plan['command_id'], None, plan_ref, generated.revision.revision_ref, compiled_ref, map_ref)
    _publish_descriptor(core, record, assembly.ASSEMBLY_V3_TYPE, assembly.ASSEMBLY_V3_SCHEMA, 'claim:child-pair:descriptor')
    assert read_assembly_revision(core, reference) == record
    assert validate_closed_revision(core, generated.revision.revision_ref, registration()).element_map == generated.element_map
    before = counts(core)
    expected = 'exact paired derivation' if axis == 'contents' else 'exact pair differs'
    with pytest.raises(RegistryConflict, match=expected):
        validate_assembly_revision(core, reference, registration())
    with pytest.raises(RegistryConflict, match=expected):
        author.publish(**request(type('Child', (), {'revision': record, 'generated': generated})(), command='parent:bad-pair', pair=False))
    assert counts(core) == before
    assert_no_run(core)


def test_canonical_mixed_containment_history_cycle_uses_one_snapshot_path(fixture,monkeypatch):
    core, gateway, selected, producer, graph = fixture
    author = AssemblyAuthorV3(gateway, selected, producer)
    plan, _, _, _, _, docs, _, _, _, _ = prepare(author, request(graph, command='cycle:template', pair=False))
    a = assembly._result_ref(core, author.binding['source_id'], 'cycle:A', None)
    b = assembly._result_ref(core, author.binding['source_id'], 'cycle:B', a)
    flat = author.publish(**request(graph, command='cycle:valid-flat', pair=False))
    plan_a, plan_b = deepcopy(plan), deepcopy(plan)
    plan_a.update(command_id='cycle:A', generated_revision_ref=flat.revision.generated_revision_ref.to_dict())
    plan_b.update(command_id='cycle:B', parent_revision_ref=a.to_dict(), generated_revision_ref=flat.revision.generated_revision_ref.to_dict())
    plan_a['members'][0]['revision_ref'] = b.to_dict()
    plan_a['members'][0]['resolution'] = assembly._resolution(assembly.ResolvedAssemblyMember(flat, flat.members))
    pa = author._publish_document('cycle:A:plan-claim', assembly.PLAN_V3_SCHEMA, plan_a)
    pb = author._publish_document('cycle:B:plan-claim', assembly.PLAN_V3_SCHEMA, plan_b)
    common = dict(owner_task_ref=flat.revision.owner_task_ref, producer_principal_ref=producer,
        generated_revision_ref=flat.revision.generated_revision_ref,
        compiled_inventory_ref=flat.revision.compiled_inventory_ref, lowering_mapping_ref=flat.revision.lowering_mapping_ref)
    ra = assembly.AssemblyRevisionV3(revision_ref=a, command_id='cycle:A', parent_revision_ref=None, plan_ref=pa, **common)
    rb = assembly.AssemblyRevisionV3(revision_ref=b, command_id='cycle:B', parent_revision_ref=a, plan_ref=pb, **common)
    _publish_descriptor(core, ra, assembly.ASSEMBLY_V3_TYPE, assembly.ASSEMBLY_V3_SCHEMA, 'cycle:A:descriptor')
    _publish_descriptor(core, rb, assembly.ASSEMBLY_V3_TYPE, assembly.ASSEMBLY_V3_SCHEMA, 'cycle:B:descriptor')
    assert read_assembly_revision(core, a) == ra and read_assembly_revision(core, b) == rb
    before = counts(core)
    snapshots=[]; original=assembly._read_at
    def observe(db,*args,**kwargs):
        assert db.in_transaction
        snapshots.append(id(db))
        return original(db,*args,**kwargs)
    monkeypatch.setattr(assembly,'_read_at',observe)
    from cpn.rpnh.registry._registry import _RegistryCore
    reader=_RegistryCore(core.run_dir,create=False,read_only=True,catalog=core.catalog)
    with pytest.raises(RegistryConflict, match='containment/history.*cycle'):
        validate_assembly_revision(reader, a, registration())
    assert len(snapshots)==2 and len(set(snapshots))==1
    assert counts(core) == before
    assert_no_run(core)


def test_child_exact_owner_version_claim_is_rejected(fixture):
    core, gateway, selected, producer, graph = fixture
    child, author, args, root = publish_pair(fixture)
    from cpn.rpnh.collaboration import assembly_v2
    claimed = replace(child.revision,
        revision_ref=SourceQualifiedVersionRef(author.binding['source_id'], VersionRef(assembly_v2.ASSEMBLY_V2_TYPE,new_id('resource'),new_id('resource_version'))),
        owner_task_ref=SourceQualifiedVersionRef(author.binding['source_id'], VersionRef('task/v1',gateway._task_ref.entity_id,new_id('task_version'))))
    _publish_descriptor(core, claimed, assembly_v2.ASSEMBLY_V2_TYPE, assembly_v2.ASSEMBLY_V2_SCHEMA, 'claim:child-owner')
    before = counts(core)
    with pytest.raises(RegistryConflict, match='exact owner'):
        author.publish(**{**args,'command_id':'parent:wrong-owner','members':(replace(args['members'][0],revision_ref=claimed.revision_ref),args['members'][1])})
    assert counts(core) == before
    assert validate_assembly_revision(core, root.revision.revision_ref, registration()).revision == root.revision


def configure_context(selected, calls, alias):
    from cpn.components.basic import register_basic_components, CONFIG_SCHEMA_ID
    from cpn.rpnh.petri_contracts import PNFragment,PlaceDeclaration,TransitionDeclaration,PortBinding,ArcDeclaration
    register_basic_components(selected)
    key = 'test/nested-context-' + ('alias' if alias else 'safe') + '/v1'
    def lower(config, context):
        calls.append(context.component)
        nested = context.component.count('m_') == 2
        physical = lambda name: 'result' if alias and nested and name == 'other_result' else name
        ports = {port.name:port for port in context.ports}
        places = {physical(port.name):PlaceDeclaration(physical(port.name),port.schema,channel=port.channel) for port in context.ports}
        arcs=[]
        for op in context.operations:
            arcs.extend(ArcDeclaration(physical(name),op.name,'input',ports[name].cardinality) for name in op.inputs)
            arcs.extend(ArcDeclaration(physical(product.port),op.name,'output',product.maximum,mode='produce',outcome=outcome.name)
                for outcome in op.outcomes for product in outcome.products)
        return PNFragment(tuple(places.values()),tuple(TransitionDeclaration(op.name,op.name) for op in context.operations),
            tuple(arcs),tuple(PortBinding(port.name,physical(port.name)) for port in context.ports),context.operations)
    selected.register_component(key,lower,identity={'implementation_id':'test.nested-context.'+str(alias),'revision':'v1'},contracts={'config_schema':CONFIG_SCHEMA_ID})
    return key


@pytest.mark.parametrize('alias',(False,True))
def test_child_internal_connection_rechecked_against_actual_root_context(fixture,alias):
    from cpn.rpnh.collaboration.materials import _elements
    from cpn.rpnh.module import ModuleDeclaration
    from test_collaboration_assembly_v2_carriers import _dual_input
    core,gateway,selected,producer,graph=fixture
    calls=[]; key=configure_context(selected,calls,alias)
    author=AssemblyAuthorV3(gateway,selected,producer)
    document=_dual_input();document['components'][0]['key']=key
    plain_module=ModuleDeclaration.from_dict(document)
    ids={locator:'element:'+uuid.uuid4().hex for locator in _elements(plain_module)}
    plain=author.author.publish(module=plain_module,element_ids=ids,command_id='context:plain')
    gids={row['locator']:row['element_id'] for row in graph.element_map['elements']}
    child_args=request(graph,version=2,command='context:child')
    child_args.update(members=(AssemblyMemberV2(A,'Plain',plain.revision.revision_ref),AssemblyMemberV2(B,'Graph',graph.revision.revision_ref)),
        connections=(AssemblyConnection(A,ids['/exit/result'],B,gids['/entry/request']),),completion=AssemblyCompletion(B,gids['/terminal']))
    child=AssemblyAuthorV2(gateway,selected,producer).publish(**child_args)
    assert prefix(A)+'_other_result' in child.generated.module.exit
    root_args=request(child,command='context:root',pair=False,ids=(C,D))
    before=counts(core)
    if alias:
        with pytest.raises(ValueError,match='root-context contracted public exits'):
            author.publish(**root_args)
        assert counts(core)==before
    else:
        root=author.publish(**root_args)
        fresh=registration();configure_context(fresh,[],alias)
        assert validate_assembly_revision(core,root.revision.revision_ref,fresh).lowering_map==root.lowering_map
        assert len(root.lowering_map['graph_members'])==1
        assert root.lowering_map['graph_members'][0]['member_path']==[C,B]
    assert any(name==prefix(C)+'_'+prefix(A)+'_step' for name in calls)
    assert_no_run(core)
