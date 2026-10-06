"""Real nested feedback, fan-out and selected terminal-alternative proof."""
from copy import deepcopy
import uuid
import pytest
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyAuthorV3, AssemblyCompletion, GraphModuleAuthor,
    make_graph_source, validate_assembly_revision,
)
from cpn.rpnh.collaboration._assembly_lowering import prefix, resolve_pointer
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.module import ModuleDeclaration
from test_collaboration_assembly_v3 import setup_fixture, request, A, B, C, D
from test_collaboration_assembly_v3_adversarial import configure_context
from test_collaboration_assembly_v2_carriers import _dual_input
from test_collaboration_assembly_v2_fragment_bridge import _fanout_wire
from test_collaboration_assembly_v2_publication import assert_no_run, counts
from test_collaboration_graph_materials import registration
from test_collaboration_graph_source import graph_wire, recipe, source_ids


@pytest.fixture
def fixture(tmp_path):
    return setup_fixture(tmp_path)


def assert_readonly(core, value, selected):
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, selected)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert checked.lowering_map == value.lowering_map
    assert counts(core) == before
    assert_no_run(core)


@pytest.mark.parametrize('feature', ('feedback', 'fanout'))
def test_real_nested_graph_feature_roles_cover_actual_root_fragments(fixture, feature):
    core, gateway, selected, producer, _ = fixture
    wire = graph_wire(True) if feature == 'feedback' else _fanout_wire()
    if feature == 'feedback':
        # Multiple feedback inputs still consume one local graph permit.
        wire['nodes'][0]['input_ports'].append({'port_id':'other','artifact_id':'other'})
        wire['nodes'][1]['output_ports'].append({'port_id':'other','artifact_id':'other'})
        wire['arcs'].append({'arc_id':'other_feedback','source':{'node_id':'review','port_id':'other'},
            'target':{'node_id':'draft','port_id':'other'},'kind':'feedback'})
    source = make_graph_source(wire)
    graph = GraphModuleAuthor(gateway, selected, producer).publish(source=source,
        recipe=recipe(max_attempts_per_node=None), source_ids=source_ids(source), command_id='feature:graph:'+feature)
    child_version = 2 if feature == 'feedback' else 3
    Child = AssemblyAuthorV2 if child_version == 2 else AssemblyAuthorV3
    child = Child(gateway, selected, producer).publish(**request(graph, version=child_version, command='feature:child:'+feature))
    author = AssemblyAuthorV3(gateway, selected, producer)
    args = request(child, command='feature:root:'+feature, ids=(C, D))
    root = author.publish(**args)
    mapping, compiled = root.lowering_map, root.compiled.to_dict()
    paths = {(C, A), (C, B), (D, A), (D, B)}
    assert {tuple(row['member_path']) for row in mapping['graph_members']} == paths
    assert len(mapping['graph_source_origins']) == 4 * len(graph.source_map['elements'])
    assert len(mapping['graph_fragment_coverage']) == 4
    assert {tuple(row['member_path']) for row in mapping['graph_fragment_coverage']} == paths
    for coverage in mapping['graph_fragment_coverage']:
        fragment = compiled['fragments'][coverage['component']]
        expected = sum(len(fragment[field]) for field in ('places','transitions','arcs','ports','operations','internal_ports','internal_bindings'))
        assert len(coverage['elements']) == expected and all(row['origins'] for row in coverage['elements'])
    for path in paths:
        rows = [row for row in mapping['graph_source_origins'] if tuple(row['member_path']) == path]
        if feature == 'feedback':
            roles = {role['role'] for row in rows for role in row['compiled_roles']}
            assert {'permit','permit_seed','permit_consume','edge_interrupt_data','edge_interrupt_control','interrupt'} <= roles
            component = prefix(path[0]) + '_' + prefix(path[1]) + '_team'
            fragment = compiled['fragments'][component]
            assert sum(place['name'] == 'control__workflow_rework_permit' for place in fragment['places']) == 1
            permits = [arc for arc in fragment['arcs'] if arc['place'] == 'control__workflow_rework_permit']
            inputs = [arc for arc in permits if arc['direction'] == 'input']
            assert len(inputs) == 1 and inputs[0]['weight'] == 1 and inputs[0]['transition'] == 'draft__rework'
            assert all(arc['outcome'] != 'interrupted' for arc in permits)
            assert next(op for op in fragment['operations'] if op['name'] == 'draft__rework')['inputs'] == ['input__draft__changes','input__draft__other']
        else:
            arcs = [row for row in rows if row['source_locator'] in ('/arcs/to_left','/arcs/to_right')]
            pointers = [next(role['fragment_pointer'] for role in row['compiled_roles'] if role['role'] == 'edge_source_data') for row in arcs]
            assert len(pointers) == 2 and pointers[0] == pointers[1]
            assert resolve_pointer(compiled, pointers[0])['weight'] == 2
            assert all(row['declaration_locators'] == [] for row in arcs)
    assert len(root.generated.module.budget_buckets) == len(graph.module.budget_buckets)
    assert all(bucket['max_attempts'] is None for bucket in root.generated.module.to_dict()['budget_buckets'] if bucket['bucket_id'] != 'rpnh:workflow:rework')
    assert_readonly(core, root, registration())
    before = counts(core)
    assert author.publish(**args).revision.revision_ref == root.revision.revision_ref
    assert counts(core) == before


def test_real_selected_child_primary_and_alternative_terminal_provenance(fixture):
    core, gateway, selected, producer, _ = fixture
    key = configure_context(selected, [], False)
    author = AssemblyAuthorV3(gateway, selected, producer)
    document = _dual_input(); document['components'][0]['key'] = key
    alternative = deepcopy(document['terminal'])
    alternative['source']['port'] = 'other_result'; alternative['operation'] = 'other_run'
    document['terminal_alternatives'] = [alternative]
    module = ModuleDeclaration.from_dict(document)
    ids = {locator:'element:'+uuid.uuid4().hex for locator in _elements(module)}
    leaf = author.author.publish(module=module, element_ids=ids, command_id='terminal:plain')
    child = author.publish(**request(leaf, pair=False, command='terminal:child'))
    child_ids = {row['locator']:row['element_id'] for row in child.generated.element_map['elements']}
    args = request(child, command='terminal:root', ids=(C,D)); args['connections'] = ()
    before = counts(core)
    with pytest.raises(ValueError, match='primary terminal'):
        author.publish(**{**args,'command_id':'terminal:invalid-alternative','completion':AssemblyCompletion(C,child_ids['/terminal_alternatives/0'])})
    assert counts(core) == before
    root = author.publish(**args)
    expected_component = prefix(D) + '_' + prefix(A) + '_step'
    assert root.generated.module.terminal.source.component == expected_component
    assert len(root.generated.module.terminal_alternatives) == 1
    assert root.generated.module.terminal_alternatives[0].source.component == expected_component
    assert root.generated.module.terminal_alternatives[0].source.port == 'other_result'
    for identity in (C,D):
        rows = [row for row in root.lowering_map['instance_origins'] if row['member_path'] == [identity,A] and row['kind'] == 'terminal']
        assert {row['source_locator'] for row in rows} == {'/terminal','/terminal_alternatives/0'}
        if identity == D:
            assert {tuple(row['declaration_locators']) for row in rows} == {('/terminal',),('/terminal_alternatives/0',)}
        else:
            assert all('not_root_completion' in row['dispositions'] for row in rows)
        introduced = [row for row in root.lowering_map['assembly_introduced_origins'] if row['member_path'] == [identity] and row['source_locator'] == '/completion']
        assert len(introduced) == 2 and all(row['compiled_targets'] and row['via_elements'] for row in introduced)
    fresh = registration(); configure_context(fresh, [], False)
    assert_readonly(core, root, fresh)
