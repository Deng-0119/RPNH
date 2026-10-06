"""Real closed child publication, root publication, replay and full consumption."""
from copy import deepcopy
import json
import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyAuthorV3, AssemblyMemberV2, AssemblyMemberV3,
    AssemblyConnection, AssemblyCompletion, GraphModuleAuthor, SourceQualifiedVersionRef,
    nested_assembly_schema_data, make_graph_source, read_assembly_revision, validate_assembly_revision, validate_closed_revision,
)
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
from test_collaboration_assembly_v2_publication import assert_no_run, counts
from cpn.rpnh.collaboration._assembly_lowering import resolve_pointer, prefix

A, B, C, D = ('member:' + c * 32 for c in 'abcd')


def setup_fixture(tmp_path):
    schemas, types, paths = nested_assembly_schema_data()
    core = _RegistryCore(tmp_path / 'assembly-v3', create=True, catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('assembly-v3-test/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id='source-assembly-v3', command_id='bind')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Nested fixture'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='principal')
    producer = SourceQualifiedVersionRef('source-assembly-v3', principal)
    selected = registration()
    source = make_graph_source(graph_wire())
    graph = GraphModuleAuthor(gateway, selected, producer).publish(source=source, recipe=recipe(), source_ids=source_ids(source), command_id='graph')
    return core, gateway, selected, producer, graph


@pytest.fixture(scope='module')
def fixture(tmp_path_factory):
    return setup_fixture(tmp_path_factory.mktemp('nested-v3-shared'))


def request(member, *, version=3, pair=True, command='assembly', ids=(A, B), **changes):
    proof = member.generated if hasattr(member, 'generated') else member
    elements = {row['locator']: row['element_id'] for row in proof.element_map['elements']}
    Member = AssemblyMemberV3 if version == 3 else AssemblyMemberV2
    names = (next(iter(proof.module.exit)), next(iter(proof.module.entry)))
    return dict(name='Assembly', members=tuple(Member(identity, 'Same', member.revision.revision_ref) for identity in (ids if pair else ids[:1])),
        connections=(AssemblyConnection(ids[0], elements['/exit/' + names[0]], ids[1], elements['/entry/' + names[1]]),) if pair else (),
        completion=AssemblyCompletion(ids[1] if pair else ids[0], elements['/terminal']),
        budget_policy='shared_exact', deployment_intent='same_run_candidate', command_id=command, **changes)


def publish_pair(fixture, child_version=2):
    core, gateway, selected, producer, graph = fixture
    Child = AssemblyAuthorV2 if child_version == 2 else AssemblyAuthorV3
    child = Child(gateway, selected, producer).publish(**request(graph, version=child_version, command='child'))
    author = AssemblyAuthorV3(gateway, selected, producer)
    args = request(child, command='root:child-v' + str(child_version), ids=(C, D))
    root = author.publish(**args)
    return child, author, args, root


def reopen(core, root):
    before = counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_assembly_revision(reopened, root.revision.revision_ref) == root.revision
    checked = validate_assembly_revision(reopened, root.revision.revision_ref, registration())
    assert canonical_json(checked.lowering_map) == canonical_json(root.lowering_map)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(root.compiled.to_dict())
    assert counts(core) == before
    assert_no_run(core)
    return checked


@pytest.mark.parametrize('child_version', (2, 3))
def test_nested_real_publish_reopen_replay_and_complete_instance_source_coverage(fixture, child_version):
    core, _, _, _, graph = fixture
    child, author, args, root = publish_pair(fixture, child_version)
    assert len(root.generated.module.components) == 4
    assert len(root.generated.module.links) == 3
    assert len(root.generated.module.entry) == len(root.generated.module.exit) == 1
    assert root.plan['members'][0]['resolution']['generated_revision_ref'] == child.revision.generated_revision_ref.to_dict()
    mapping = root.lowering_map
    paths = {(C, A), (C, B), (D, A), (D, B)}
    assert {tuple(row['member_path']) for row in mapping['graph_members']} == paths
    assert len(mapping['graph_source_origins']) == 4 * len(graph.source_map['elements'])
    for row in mapping['graph_source_origins']:
        assert row['revision_path'][0] == child.revision.revision_ref.to_dict()
        assert row['revision_path'][1] == graph.revision.revision_ref.to_dict()
        for pointer in row['declaration_field_targets']:
            resolve_pointer(root.generated.module.to_dict(), pointer)
        for role in row['compiled_roles']:
            resolve_pointer(root.compiled.to_dict(), role['fragment_pointer'])
            for pointer in role['compiled_targets']:
                resolve_pointer(root.compiled.to_dict(), pointer)
    for coverage in mapping['graph_fragment_coverage']:
        fragment = root.compiled.to_dict()['fragments'][coverage['component']]
        count = sum(len(fragment[field]) for field in ('places', 'transitions', 'arcs', 'ports', 'operations', 'internal_ports', 'internal_bindings'))
        assert len(coverage['elements']) == count
        assert all(row['origins'] for row in coverage['elements'])
    assert len(mapping['assembly_members']) == 2
    for identity in (C, D):
        origins = [row for row in mapping['assembly_introduced_origins'] if row['member_path'] == [identity]]
        assert any(row['source_locator'] == '/connections/0' for row in origins)
        assert any(row['source_locator'] == '/completion' for row in origins)
        assert all(row['compiled_targets'] and row['via_elements'] for row in origins)
    reopen(core, root)
    before = counts(core)
    assert author.publish(**args).revision.revision_ref == root.revision.revision_ref
    assert counts(core) == before
    assert_no_run(core)


@pytest.mark.parametrize('field', ('plan_ref', 'compiled_inventory_ref', 'lowering_mapping_ref', 'generated_definition', 'graph_source'))
def test_child_material_damage_rejects_parent_but_generated_remains_independent(fixture, field):
    core, _, _, _, graph = fixture
    child, _, _, root = publish_pair(fixture)
    ref = graph.revision.graph_source_ref if field == 'graph_source' else child.generated.revision.definition_ref if field == 'generated_definition' else getattr(child.revision, field)
    path = core.object_store.path_for_version(ref.ref.resource_version_id)
    original = path.read_bytes(); path.write_bytes(original[:-1] + (b' ' if original[-1:] != b' ' else b'\n'))
    before = counts(core)
    with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
        validate_assembly_revision(core, root.revision.revision_ref, registration())
    validate_closed_revision(core, root.revision.generated_revision_ref, registration())
    assert counts(core) == before
    path.write_bytes(original)
    reopen(core, root)


def test_generated_child_cannot_be_laundered_as_plain_member(fixture):
    core, gateway, selected, producer, _ = fixture
    child, author, _, _ = publish_pair(fixture)
    before = counts(core)
    with pytest.raises(ValueError, match='plain v1'):
        author.publish(**request(child.generated, pair=False, command='laundered'))
    assert counts(core) == before


def test_containment_depth_limit_rejects_nested_child_before_root_plan(fixture):
    core, _, _, _, _ = fixture
    _, author, _, root = publish_pair(fixture)
    before = counts(core)
    with pytest.raises(RegistryConflict, match='flat child'):
        author.publish(**request(root, pair=False, command='too-deep'))
    assert counts(core) == before


def test_root_history_and_repeated_child_dag_are_distinct(fixture):
    core, _, _, _, _ = fixture
    _, author, args, first = publish_pair(fixture)
    second = author.publish(**{**args, 'command_id': 'root:next', 'parent_ref': first.revision.revision_ref})
    assert second.revision.revision_ref.ref.entity_id == first.revision.revision_ref.ref.entity_id
    assert second.generated.revision.parent_revision_refs == (first.revision.generated_revision_ref,)
    reopen(core, second)


def test_wrong_source_child_fails_before_parent_plan(fixture):
    from dataclasses import replace
    core, _, _, _, _ = fixture
    child, author, args, _ = publish_pair(fixture)
    wrong = SourceQualifiedVersionRef('different-source', child.revision.revision_ref.ref)
    before = counts(core)
    with pytest.raises(RegistryConflict, match='source'):
        author.publish(**{**args, 'command_id': 'wrong-source', 'members': (replace(args['members'][0], revision_ref=wrong), args['members'][1])})
    assert counts(core) == before


def test_final_cut_child_damage_rejects_then_same_exact_request_recovers(fixture, monkeypatch):
    from cpn.rpnh.collaboration import assembly_v3 as assembly
    core, _, _, _, _ = fixture
    child, author, args, _ = publish_pair(fixture)
    args = {**args, 'command_id': 'root:child-final-cut'}
    reference = assembly._result_ref(core, author.binding['source_id'], args['command_id'], None)
    path = core.object_store.path_for_version(child.revision.plan_ref.ref.resource_version_id)
    original = path.read_bytes(); publish = author._publish_document
    def publish_then_damage(key, schema, document, **kwargs):
        result = publish(key, schema, document, **kwargs)
        if key == assembly._command(args['command_id']) + ':lowering':
            path.write_bytes(original[:-1] + b' ')
        return result
    monkeypatch.setattr(author, '_publish_document', publish_then_damage)
    try:
        with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
            author.publish(**args)
        assert core.event_store.object_row(reference.ref.version_id) is None
    finally:
        path.write_bytes(original)
        monkeypatch.setattr(author, '_publish_document', publish)
    repaired = author.publish(**args)
    assert repaired.revision.revision_ref == reference
    reopen(core, repaired)


@pytest.mark.parametrize('damage', ('drop_role', 'wrong_path', 'wrong_coverage_source', 'drop_child_connection'))
def test_canonical_transitive_map_claim_fails_independent_full_consumer(fixture, monkeypatch, damage):
    from cpn.rpnh.collaboration import _assembly_v3_lowering as lowering
    core, _, _, _, _ = fixture
    child, author, args, _ = publish_pair(fixture)
    original = lowering.lowering_map_v3
    def altered(*values, **kwargs):
        mapping, ids = original(*values, **kwargs)
        mapping = deepcopy(mapping)
        if damage == 'drop_role':
            row = next(row for row in mapping['graph_source_origins'] if len(row['compiled_roles']) > 1)
            row['compiled_roles'].pop()
        elif damage == 'wrong_path':
            mapping['graph_source_origins'][0]['member_path'][0] = D
        elif damage == 'wrong_coverage_source':
            origins = mapping['graph_fragment_coverage'][0]['elements'][0]['origins']
            origins[0]['source_element_id'] = 'element:' + 'f' * 32
        else:
            mapping['assembly_introduced_origins'] = [row for row in mapping['assembly_introduced_origins'] if row['source_locator'] != '/connections/0']
        core.catalog.validate_schema_ref('rpnh/collaboration/assembly_lowering_map/v3', mapping)
        return mapping, ids
    monkeypatch.setattr(lowering, 'lowering_map_v3', altered)
    # Publish real canonical resources/envelope as an adversarial producer claim.
    # Only the independent unmodified consumer decides whether this is valid.
    claim = author.publish(**{**args, 'command_id': 'claim:map:' + damage})
    monkeypatch.setattr(lowering, 'lowering_map_v3', original)
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with pytest.raises(RegistryConflict):
        validate_assembly_revision(reader, claim.revision.revision_ref, registration())
    validate_closed_revision(reader, claim.revision.generated_revision_ref, registration())
    assert counts(core) == before
    assert_no_run(core)
