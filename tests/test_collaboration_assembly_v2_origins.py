"""Real Assembly-v2 source/origin consistency, with no runtime execution."""
from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from cpn.rpnh.collaboration import (
    AssemblyCompletion, AssemblyConnection, AssemblyMemberV2, GraphModuleAuthor,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
    read_assembly_revision, validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v2_publication import (
    A, B, assert_no_run, counts, fixture, request,
)
from test_collaboration_graph_materials import registration
from test_collaboration_graph_source import recipe


C = 'member:' + 'c' * 32


def _checked_reopen(core, value):
    before = counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reopened, value.revision.revision_ref, registration())
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert canonical_json(checked.lowering_map) == canonical_json(value.lowering_map)
    assert counts(core) == before
    assert_no_run(core)
    return checked


def test_real_label_successor_normalizes_member_and_connection_order(fixture):
    core, _, author, _, member = fixture
    ids = {row['locator']: row['element_id'] for row in member.element_map['elements']}
    members = tuple(AssemblyMemberV2(identity, 'Same', member.revision.revision_ref) for identity in (A, B, C))
    connections = tuple(AssemblyConnection(left, ids['/exit/result'], right, ids['/entry/request'])
                        for left, right in ((A, B), (B, C)))
    args = request(member, members=members, connections=connections,
                   completion=AssemblyCompletion(C, ids['/terminal']))
    first = author.publish(**args)
    renamed = tuple(AssemblyMemberV2(identity, 'Label ' + str(index), member.revision.revision_ref)
                    for index, identity in enumerate((C, B, A)))
    next_args = {**args, 'members': renamed, 'connections': tuple(reversed(connections)),
                 'command_id': 'assembly:labels', 'parent_ref': first.revision.revision_ref}
    second = author.publish(**next_args)
    assert first.revision.revision_ref.ref.entity_id == second.revision.revision_ref.ref.entity_id
    assert first.revision.revision_ref != second.revision.revision_ref
    assert first.revision.plan_ref != second.revision.plan_ref
    assert [row['member_id'] for row in second.plan['members']] == [A, B, C]
    assert [row['display_name'] for row in second.plan['members']] == ['Label 2', 'Label 1', 'Label 0']
    assert canonical_json(first.generated.module.to_dict()) == canonical_json(second.generated.module.to_dict())
    assert canonical_json(first.generated.element_map) == canonical_json(second.generated.element_map)
    assert canonical_json(first.compiled.to_dict()) == canonical_json(second.compiled.to_dict())
    assert canonical_json(first.lowering_map) == canonical_json(second.lowering_map)
    _checked_reopen(core, second)
    before = counts(core)
    normalized = {**next_args, 'members': tuple(reversed(renamed)), 'connections': connections}
    assert author.publish(**normalized).revision.revision_ref == second.revision.revision_ref
    assert counts(core) == before


def test_real_single_member_successor_preserves_other_graph_source_identity(fixture):
    core, gateway, author, selected, member = fixture
    first = author.publish(**request(member))
    changed = deepcopy(member.source)
    changed['graph']['nodes'][0]['instruction'] = 'Draft the updated result.'
    stable_ids = {row['locator']: row['element_id'] for row in member.source_map['elements']}
    graph_author = GraphModuleAuthor(gateway, selected, author.producer)
    updated = graph_author.publish(source=changed, recipe=recipe(), source_ids=stable_ids,
        command_id='member:instruction-successor', parent_ref=member.revision.revision_ref)
    second = author.publish(**request(member, command_id='assembly:member-successor',
        parent_ref=first.revision.revision_ref,
        members=(AssemblyMemberV2(A, 'Same', member.revision.revision_ref),
                 AssemblyMemberV2(B, 'Same', updated.revision.revision_ref))))
    assert second.plan['members'][0]['revision_ref'] == member.revision.revision_ref.to_dict()
    assert second.plan['members'][1]['revision_ref'] == updated.revision.revision_ref.to_dict()
    assert updated.revision.graph_source_ref != member.revision.graph_source_ref
    assert updated.revision.revision_ref.ref.entity_id == member.revision.revision_ref.ref.entity_id
    assert updated.source_map == member.source_map
    old_rows = {row['source_element_id']: row for row in first.lowering_map['graph_source_origins'] if row['member_id'] == A}
    new_rows = {row['source_element_id']: row for row in second.lowering_map['graph_source_origins'] if row['member_id'] == A}
    assert old_rows == new_rows
    assert {row['source_element_id'] for row in second.lowering_map['graph_source_origins'] if row['member_id'] == B} == set(stable_ids.values())
    component = {value.name: value for value in second.generated.module.components}
    assert component['m_' + A.split(':')[1] + '_team'].operations[0].config['node_synopsis'] == 'Draft the result.'
    assert component['m_' + B.split(':')[1] + '_team'].operations[0].config['node_synopsis'] == 'Draft the updated result.'
    _checked_reopen(core, second)


def _publish_descriptor(core, record, object_type, schema, key):
    """Publish a canonical adversarial claim, never a validated success."""
    reference = record.revision_ref.ref
    document = record.to_dict()
    core.catalog.validate_instance(object_type, category='object', instance=document)
    core.publish_bytes(object_type=object_type, logical_id=reference.entity_id,
        version_id=reference.version_id, payload=canonical_json(document), metadata=document,
        media_type='application/json', schema_ref=schema, idempotency_key=key)


def _canonical_graph_claim(fixture, axis):
    """Keep one exact command envelope while changing its declared materials.

    The baseline envelope uses this claim's exact command/identity, rather than
    borrowing a different command header and failing for that trivial reason.
    It is durably registered with the first source resource and is not rewritten.
    All payloads, metadata, refs, producer relations and commits use real writers.
    """
    from cpn.rpnh.collaboration import graph_authoring as graph
    from cpn.rpnh.collaboration.graph_source import rebuild_graph_module
    from cpn.rpnh.collaboration.materials import _material
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    core, gateway, author, selected, member = fixture
    graph_author = GraphModuleAuthor(gateway, selected, author.producer)
    command_id = 'graph:canonical-claim:' + axis
    owner = SourceQualifiedVersionRef(author.binding['source_id'], gateway._task_ref)
    documents = deepcopy([member.source, member.recipe, member.source_map, member.module.to_dict(),
                          member.element_map, member.boundary_map, member.host_requirements])
    original_envelope = graph._command(source_id=author.binding['source_id'], owner=owner,
        producer=author.producer, command_id=command_id, parents=(), documents=documents)
    if axis in ('instruction', 'joint_source_derived'):
        documents[0]['graph']['nodes'][0]['instruction'] = 'Canonical changed instruction.'
    if axis == 'attempts':
        documents[1]['max_attempts_per_node'] = 17
    if axis == 'derived_operation':
        documents[3]['components'][0]['operations'][0]['config']['node_synopsis'] = 'Only the derived operation changed.'
    if axis == 'joint_source_derived':
        documents[3] = rebuild_graph_module(documents[0], documents[1]).to_dict()
    refs = []
    for index, (schema, document) in enumerate(zip(graph.GRAPH_DOCUMENT_SCHEMAS, documents, strict=True)):
        core.catalog.validate_schema_ref(schema, document)
        ref = _publish_private_system(core, gateway._task_ref, PublishResource(
            origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=canonical_json(document),
            media_type='application/json', content_schema_ref=schema,
            content_schema_authority_ref=graph_author.schemas[schema], summary='Canonical graph consistency claim',
            lifetime_ref=gateway._bootstrap_ref,
            descriptors={'graph_author_command_v1': original_envelope} if index == 0 else {},
            idempotency_key=command_id + ':material:' + str(index)))
        refs.append(SourceQualifiedResourceRef(author.binding['source_id'], ref))
    record = graph.GraphNetRevision(graph._revision_reference(core, author.binding['source_id'], command_id),
        owner, author.producer, command_id, (), *refs)
    _publish_descriptor(core, record, graph.GRAPH_REVISION_TYPE, graph.GRAPH_REVISION_SCHEMA, command_id)
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        assert graph._read_graph_revision_at(db, core, record.revision_ref,
            local_source_id=author.binding['source_id']) == record
        for ref, schema, document in zip(refs, graph.GRAPH_DOCUMENT_SCHEMAS, documents, strict=True):
            assert _material(db, core, ref, author.binding, schema)[0] == document
        persisted, metadata = _material(db, core, refs[0], author.binding, graph.GRAPH_SOURCE_SCHEMA)
        assert metadata['descriptors']['graph_author_command_v1'] == original_envelope
    assert json.loads(original_envelope)['command_id'] == record.command_id
    return record, refs[0], original_envelope


@pytest.mark.parametrize('axis', ('instruction', 'attempts', 'derived_operation', 'joint_source_derived'))
def test_real_canonical_graph_material_changes_reach_source_or_command_semantics(fixture, monkeypatch, axis):
    from cpn.rpnh.collaboration import graph_authoring as graph
    from cpn.rpnh.collaboration.materials import _material
    core, _, author, _, member = fixture
    record, source_ref, fixed_envelope = _canonical_graph_claim(fixture, axis)
    seen = []
    original_proof, original_finish = graph._read_graph_proof, graph._finish_graph_validation
    def proof(*args):
        result = original_proof(*args)
        seen.append('source_recipe_rebuilt')
        return result
    def finish(*args):
        seen.append('complete_command_compare')
        return original_finish(*args)
    monkeypatch.setattr(graph, '_read_graph_proof', proof)
    monkeypatch.setattr(graph, '_finish_graph_validation', finish)
    args = request(member, command_id='assembly:reject:' + axis,
        members=(AssemblyMemberV2(A, 'Claim', record.revision_ref),
                 AssemblyMemberV2(B, 'Valid', member.revision.revision_ref)))
    before = counts(core)
    expected = 'immutable complete author command' if axis == 'joint_source_derived' else 'complete source/recipe reconstruction'
    with pytest.raises(RegistryConflict, match=expected) as rejected:
        author.publish(**args)
    assert seen[0] == 'source_recipe_rebuilt'
    assert ('complete_command_compare' in seen) == (axis == 'joint_source_derived')
    assert counts(core) == before
    assert core.event_store.object_rows_by_type('collaboration_assembly_revision/v2') == ()
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        assert _material(db, core, source_ref, author.binding, graph.GRAPH_SOURCE_SCHEMA)[1]['descriptors']['graph_author_command_v1'] == fixed_envelope
    assert_no_run(core)
    print('C_SO_SEMANTIC_TRACE=' + json.dumps({'case': axis, 'route': 'AssemblyAuthorV2.publish',
        'outer_canonical_materials': 7, 'fixed_authority': 'first_source_graph_author_command_v1',
        'command_sha256': hashlib.sha256(fixed_envelope.encode()).hexdigest(),
        'executed': seen, 'rejection': str(rejected.value)}, sort_keys=True))


def _prepare_claim(fixture, command_id):
    from cpn.rpnh.collaboration import assembly_v2 as assembly
    core, _, author, _, member = fixture
    args = request(member, command_id=command_id)
    plan = {'schema_version': assembly.PLAN_V2_SCHEMA, 'source_id': author.binding['source_id'],
        'owner_task_ref': member.revision.owner_task_ref.to_dict(),
        'producer_principal_ref': author.producer.to_dict(), 'command_id': command_id,
        'parent_revision_ref': None, 'name': args['name'],
        'members': [row.to_dict() for row in args['members']],
        'connections': [row.to_dict() for row in args['connections']],
        'completion': args['completion'].to_dict(), 'budget_policy': args['budget_policy'],
        'deployment_intent': args['deployment_intent'], 'resolver_recipe': assembly.resolver_recipe()}
    before = counts(core)
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        binding = assembly._binding_at(db, core)
        selected = assembly._members_at(db, core, plan, author.registration, binding, locked=False)
        result = assembly._prepare_at(db, core, plan, selected, None, author.registration, binding)
    assert counts(core) == before
    return result


def _canonical_assembly_claim(fixture, damage):
    """Publish internally signed claims around fixed valid member/source proof.

    The new plan describes the deliberately wrong output. A reader must rebuild
    from the fixed graph members to reject it; no existing authority is rewritten.
    """
    from cpn.rpnh.collaboration import assembly_v2 as assembly
    from cpn.rpnh.collaboration._assembly_lowering import resolve_pointer
    from cpn.rpnh.collaboration.materials import _material
    from cpn.rpnh.module import ModuleDeclaration
    core, _, author, _, member = fixture
    prepared = _prepare_claim(fixture, 'assembly:canonical-claim:' + damage)
    plan, module, compiled, mapping, ids, documents, refs, reference, generated_command, generated_ref = prepared
    expected_plan = deepcopy(plan)
    documents = deepcopy(list(documents))
    altered = documents[5]
    row = next(row for row in altered['graph_source_origins']
        if row['member_id'] == A and row['source_locator'] == '/nodes/review/input_ports/candidate')
    if damage == 'missing_internal_input':
        altered['graph_source_origins'].remove(row)
    elif damage == 'missing_arc':
        altered['graph_source_origins'].remove(next(row for row in altered['graph_source_origins']
            if row['member_id'] == A and row['source_kind'] == 'arc'))
    elif damage == 'other_member_pointer':
        other = next(value for value in altered['graph_source_origins']
            if value['member_id'] == B and value['source_locator'] == row['source_locator'])
        row['compiled_roles'] = deepcopy(other['compiled_roles'])
        for role in row['compiled_roles']:
            resolve_pointer(compiled.to_dict(), role['fragment_pointer'])
            for pointer in role['compiled_targets']:
                resolve_pointer(compiled.to_dict(), pointer)
    elif damage == 'invented_internal_element':
        # This internal input is represented by a field and lowering roles;
        # it has no independent Module declaration. Claim a duplicate existing
        # declaration locator under a new element identity to avoid dangling JSON.
        declaration = deepcopy(next(item for item in altered['declarations'] if item['kind'] == 'port'))
        declaration['element_id'] = 'element:' + 'e' * 32
        assert declaration['element_id'] not in {item['element_id'] for item in altered['declarations']}
        altered['declarations'].append(declaration)
        row['representation'] = 'derived_declaration'
        row['declaration_locators'] = [declaration['locator']]
        assert row['source_kind'] == 'input_port'
    elif damage == 'missing_constraints_projection':
        documents[0]['designer_constraints'].pop('assembly_member_constraints')
    elif damage == 'global_rework_constraint':
        projection = documents[0]['designer_constraints'].pop('assembly_member_constraints')
        documents[0]['designer_constraints']['max_rework_cycles'] = projection['members'][0]['constraints']['max_rework_cycles']
    else:
        raise AssertionError(damage)
    signatures = plan['prepared_materials']
    for index, document in enumerate(documents):
        item = signatures[index]
        core.catalog.validate_schema_ref(item['schema'], document)
        signatures[index] = assembly.material_signature(item['role'], item['schema'], refs[index], document)
    core.catalog.validate_schema_ref(assembly.PLAN_V2_SCHEMA, plan)
    key = assembly._command(plan['command_id'])
    fixed_envelope = assembly._envelope(plan, documents)
    plan_ref = author._publish_document(key + ':plan', assembly.PLAN_V2_SCHEMA, plan,
        descriptors={'assembly_author_command_v2': fixed_envelope})
    generated = author.author.publish(module=ModuleDeclaration.from_dict(documents[0]), element_ids=ids,
        command_id=generated_command)
    assert generated.revision.revision_ref == generated_ref
    actual_generated_refs = tuple(getattr(generated.revision, field) for field in
        ('definition_ref', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref'))
    assert actual_generated_refs == refs[:4]
    assert canonical_json([generated.module.to_dict(), generated.element_map, generated.boundary_map,
                           generated.host_requirements]) == canonical_json(documents[:4])
    compiled_ref = author._publish_document(key + ':compiled', 'rpnh/executable_net/v1', documents[4])
    lowering_ref = author._publish_document(key + ':lowering', assembly.LOWERING_V2_SCHEMA, documents[5])
    assert (compiled_ref, lowering_ref) == refs[4:]
    record = assembly.AssemblyRevisionV2(reference, member.revision.owner_task_ref, author.producer,
        plan['command_id'], None, plan_ref, generated_ref, compiled_ref, lowering_ref)
    _publish_descriptor(core, record, assembly.ASSEMBLY_V2_TYPE, assembly.ASSEMBLY_V2_SCHEMA, key)
    assert read_assembly_revision(core, reference) == record
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        assert _material(db, core, plan_ref, author.binding, assembly.PLAN_V2_SCHEMA)[0] == plan
        for ref, item, document in zip(refs, plan['prepared_materials'], documents, strict=True):
            assert _material(db, core, ref, author.binding, item['schema'])[0] == document
    # Canonical generated-v1 remains a valid closed declaration in its own scope.
    assert validate_closed_revision(core, generated_ref, registration()).revision.revision_ref == generated_ref
    assert expected_plan['prepared_materials'] != plan['prepared_materials']
    return record, expected_plan, fixed_envelope


@pytest.mark.parametrize('damage', ('missing_internal_input', 'missing_arc', 'other_member_pointer',
    'invented_internal_element', 'missing_constraints_projection', 'global_rework_constraint'))
def test_real_canonical_origin_and_constraints_claims_require_independent_reconstruction(fixture, monkeypatch, damage):
    from cpn.rpnh.collaboration import assembly_v2 as assembly
    from cpn.rpnh.collaboration.materials import _material
    core, _, author, _, _ = fixture
    record, expected_plan, fixed_envelope = _canonical_assembly_claim(fixture, damage)
    original = assembly._prepare_at
    reconstructed = []
    def observe(*args):
        result = original(*args)
        # Observation only: this is the unchanged product builder, actual final
        # compile, fragment-role projection and signatures running independently.
        reconstructed.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(assembly, '_prepare_at', observe)
    before = counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with pytest.raises(RegistryConflict, match='complete command, prepared signatures or exact pair') as rejected:
        validate_assembly_revision(reopened, record.revision_ref, registration())
    assert reconstructed == [expected_plan]
    assert counts(core) == before
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        assert _material(db, core, record.plan_ref, author.binding, assembly.PLAN_V2_SCHEMA)[1]['descriptors']['assembly_author_command_v2'] == fixed_envelope
    assert_no_run(core)
    print('C_SO_SEMANTIC_TRACE=' + json.dumps({'case': damage, 'route': 'readonly_public_full_consumer',
        'outer_canonical_materials': 7, 'outer_canonical_descriptors': 2,
        'fixed_authority': 'new_plan_claim_with_original_exact_graph_members',
        'plan_envelope_sha256': hashlib.sha256(fixed_envelope.encode()).hexdigest(),
        'executed': ['full_graph_source_consumer', 'actual_final_compile', 'source_fragment_double_coverage',
                     'recomputed_prepared_signatures'], 'rejection': str(rejected.value)}, sort_keys=True))


def test_real_graph_bucket_cap_conflict_is_rejected_before_first_plan(fixture):
    core, gateway, author, selected, member = fixture
    other = GraphModuleAuthor(gateway, selected, author.producer).publish(source=deepcopy(member.source),
        recipe=recipe(max_attempts_per_node=17),
        source_ids={row['locator']: row['element_id'] for row in member.source_map['elements']},
        command_id='member:other-cap')
    assert validate_closed_revision(core, other.revision.revision_ref, registration()).revision == other.revision
    args = request(member, command_id='assembly:cap-conflict',
        members=(AssemblyMemberV2(A, 'A', member.revision.revision_ref), AssemblyMemberV2(B, 'B', other.revision.revision_ref)))
    before = counts(core)
    with pytest.raises(ValueError, match='shared_exact conflicting budget bucket'):
        author.publish(**args)
    assert counts(core) == before
    assert core.event_store.object_rows_by_type('collaboration_assembly_revision/v2') == ()
    assert_no_run(core)


def test_real_distinct_bucket_ids_cannot_share_one_budget_scope(fixture):
    from cpn.components.basic import register_basic_components
    from cpn.rpnh.collaboration.materials import _elements
    from cpn.rpnh.module import ModuleDeclaration
    from test_collaboration_assembly_v2_projection import simple_plain_module
    core, _, author, selected, member = fixture
    register_basic_components(selected)
    document = simple_plain_module().to_dict()
    graph_bucket = member.module.budget_buckets[0]
    changed_scope = graph_bucket.budget_scope
    document['budget_buckets'][0]['budget_scope'] = changed_scope
    document['components'][0]['operations'][0]['budget_binding']['budget_scope'] = changed_scope
    assert document['budget_buckets'][0]['bucket_id'] != graph_bucket.bucket_id
    plain = ModuleDeclaration.from_dict(document)
    ids = {key: 'element:' + uuid.uuid4().hex for key in _elements(plain)}
    good = author.author.publish(module=plain, element_ids=ids, command_id='member:plain-scope')
    assert validate_closed_revision(core, good.revision.revision_ref, selected).revision == good.revision
    graph_ids = {row['locator']: row['element_id'] for row in member.element_map['elements']}
    args = request(member, command_id='assembly:scope-conflict',
        members=(AssemblyMemberV2(A, 'Graph', member.revision.revision_ref), AssemblyMemberV2(B, 'Plain', good.revision.revision_ref)),
        connections=(AssemblyConnection(A, graph_ids['/exit/result'], B, ids['/entry/request']),),
        completion=AssemblyCompletion(B, ids['/terminal']))
    before = counts(core)
    with pytest.raises(ValueError, match='budget scope must identify exactly one bucket'):
        author.publish(**args)
    assert counts(core) == before
    assert core.event_store.object_rows_by_type('collaboration_assembly_revision/v2') == ()
    assert_no_run(core)
