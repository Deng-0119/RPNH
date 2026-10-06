"""Actual final-context carriers over graph/plain canonical member revisions."""
from copy import deepcopy
from dataclasses import replace
import json
import uuid

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.collaboration import (
    AssemblyCompletion, AssemblyConnection, AssemblyMemberV2,
    validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.collaboration._assembly_lowering import prefix, resolve_pointer
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, InitialTokenDeclaration, PlaceDeclaration, PNFragment,
    PortBinding, TransitionDeclaration,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v2_publication import A, B, assert_no_run, counts, fixture, request
from test_collaboration_assembly_v2_projection import simple_plain_module
from test_collaboration_graph_materials import registration


C = 'member:' + 'c' * 32
ALT_TEXT = 'application/carrier_other_text/v1'


def _key(mode):
    return 'test/assembly-carrier-' + mode + '/v1'


def _configure_carriers(selected, calls):
    """Explicit trusted offline lowerers; all business registrations stay stubs."""
    register_basic_components(selected)
    selected.register_schema(ALT_TEXT, {'$id': ALT_TEXT,
        '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
    for mode in ('passthrough', 'dual-input', 'initial', 'capacity'):
        def lower(config, context, *, mode=mode):
            calls.append({'mode': mode, 'component': context.component})
            if mode == 'dual-input':
                # This explicitly registered fixture lowerer supports two
                # operations; the basic operation lowerer accepts exactly one.
                ports = {port.name: port for port in context.ports}
                arcs = []
                for operation in context.operations:
                    arcs.extend(ArcDeclaration(name, operation.name, 'input', ports[name].cardinality)
                                for name in operation.inputs)
                    arcs.extend(ArcDeclaration(product.port, operation.name, 'output', product.maximum,
                        mode='produce', outcome=outcome.name)
                        for outcome in operation.outcomes for product in outcome.products)
                fragment = PNFragment(
                    tuple(PlaceDeclaration(port.name, port.schema, channel=port.channel) for port in context.ports),
                    tuple(TransitionDeclaration(operation.name, operation.name) for operation in context.operations),
                    tuple(arcs), tuple(PortBinding(port.name, port.name) for port in context.ports), context.operations)
            else:
                fragment = lower_operation(config, context)
            if not context.component.startswith('m_'):
                return fragment
            aliases = {'result': 'request'} if mode == 'passthrough' else (
                {'other_request': 'request'} if mode == 'dual-input' else {})
            physical = lambda name: aliases.get(name, name)
            places = {}
            for place in fragment.places:
                value = replace(place, name=physical(place.name))
                if mode == 'initial' and place.name == 'request':
                    value = replace(value, initial_tokens=(InitialTokenDeclaration(schema=TEXT_SCHEMA, value='prepared'),))
                if mode == 'capacity' and place.name == 'request':
                    value = replace(value, capacity=2)
                if value.name in places:
                    assert places[value.name] == value
                places[value.name] = value
            return replace(fragment, places=tuple(places[name] for name in sorted(places)),
                ports=tuple(replace(port, place=physical(port.place)) for port in fragment.ports),
                arcs=tuple(replace(arc, place=physical(arc.place),
                    forward_source=None if arc.forward_source is None else physical(arc.forward_source))
                    for arc in fragment.arcs))
        selected.register_component(_key(mode), lower,
            identity={'implementation_id': 'test.assembly.carrier.' + mode, 'revision': 'v1'},
            contracts={'config_schema': CONFIG_SCHEMA_ID})
    return selected


def _fresh_registration(calls):
    return _configure_carriers(registration(), calls)


def _ids(member):
    return {row['locator']: row['element_id'] for row in member.element_map['elements']}


def _publish_plain(author, module, command):
    ids = {locator: 'element:' + uuid.uuid4().hex for locator in _elements(module)}
    value = author.author.publish(module=module, element_ids=ids, command_id=command)
    assert validate_closed_revision(author.core, value.revision.revision_ref, author.registration).revision == value.revision
    return value


def _dual_input():
    document = simple_plain_module().to_dict()
    component = document['components'][0]
    component['key'] = _key('dual-input')
    additional = deepcopy(component['ports'])
    for port in additional:
        port['name'] = 'other_' + port['name']
    component['ports'].extend(additional)
    operation = deepcopy(component['operations'][0])
    operation.update(name='other_run', inputs=['other_request'], outputs=['other_result'])
    operation['outcomes'][0]['products'][0]['port'] = 'other_result'
    component['operations'].append(operation)
    document['entry']['other_request'] = {'component': 'step', 'port': 'other_request'}
    document['exit']['other_result'] = {'component': 'step', 'port': 'other_result'}
    return document


def _internally_fused(side, *, alternative=False):
    if side == 'output':
        document = simple_plain_module().to_dict()
        right = deepcopy(document['components'][0])
        right.update(name='right', key=_key('passthrough'))
        document['components'].append(right)
        document['links'] = [{'source': {'component': 'step', 'port': 'result'},
                              'target': {'component': 'right', 'port': 'request'}}]
        document['exit']['other_result'] = {'component': 'right', 'port': 'result'}
        document['terminal']['source']['component'] = 'right'
    else:
        document = _dual_input()
        left = deepcopy(simple_plain_module().to_dict()['components'][0])
        left.update(name='left', key=_key('passthrough'))
        document['components'].append(left)
        document['links'] = [{'source': {'component': 'left', 'port': 'result'},
                              'target': {'component': 'step', 'port': 'other_request'}}]
        document['entry']['left_request'] = {'component': 'left', 'port': 'request'}
        del document['entry']['other_request']
    if alternative:
        independent = deepcopy(simple_plain_module().to_dict()['components'][0])
        independent['name'] = 'independent'
        document['components'].append(independent)
        document['entry']['independent_request'] = {'component': 'independent', 'port': 'request'}
        document['exit']['independent_result'] = {'component': 'independent', 'port': 'result'}
        document['terminal_alternatives'] = [deepcopy(document['terminal'])]
        document['terminal']['source']['component'] = 'independent'
    return ModuleDeclaration.from_dict(document)


def _assert_prewrite_reject(core, author, args, expected, calls):
    before = counts(core)
    start = len(calls)
    with pytest.raises(ValueError, match=expected) as rejected:
        author.publish(**args)
    assert counts(core) == before
    assert core.event_store.object_rows_by_type('collaboration_assembly_revision/v2') == ()
    actual_final = [row for row in calls[start:] if row['component'].startswith('m_')]
    if calls:
        assert actual_final
    assert_no_run(core)
    print('C_CF_REJECTION=' + json.dumps({'command': args['command_id'],
        'actual_final_lower_calls': actual_final, 'rejection': str(rejected.value),
        'command_writes': 0}, sort_keys=True))


def test_real_graph_plain_graph_chain_contracts_all_internal_fusion_exit_origins(fixture, monkeypatch):
    from cpn.rpnh.collaboration import _assembly_v2_lowering as lowering
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    plain = _publish_plain(author, _internally_fused('output'), 'plain:internal-output')
    gids, pids = _ids(graph), _ids(plain)
    assert plain.compiled.symbolic.port_places['step.result'] != plain.compiled.symbolic.port_places['right.result']
    args = request(graph, command_id='assembly:mixed-three-chain',
        members=(AssemblyMemberV2(A, 'Graph A', graph.revision.revision_ref),
                 AssemblyMemberV2(B, 'Fused B', plain.revision.revision_ref),
                 AssemblyMemberV2(C, 'Graph C', graph.revision.revision_ref)),
        connections=(AssemblyConnection(A, gids['/exit/result'], B, pids['/entry/request']),
                     AssemblyConnection(B, pids['/exit/result'], C, gids['/entry/request'])),
        completion=AssemblyCompletion(C, gids['/terminal']))
    observed = []
    original_contract, original_mapping = lowering._contract_carriers, lowering.lowering_map_v2
    def no_relower(stage, original):
        def observe(*args, **kwargs):
            before = len(calls)
            result = original(*args, **kwargs)
            assert len(calls) == before, stage + ' must reuse actual final fragments'
            observed.append(stage)
            return result
        return observe
    monkeypatch.setattr(lowering, '_contract_carriers', no_relower('carrier_cut', original_contract))
    monkeypatch.setattr(lowering, 'lowering_map_v2', no_relower('origin_map', original_mapping))
    value = author.publish(**args)
    assert len(value.generated.module.links) == 3  # One member link, then A→B→C.
    assert set(value.generated.module.exit) == {prefix(C) + '_result'}
    b_step, b_right = prefix(B) + '_step', prefix(B) + '_right'
    places = value.compiled.symbolic.port_places
    assert places[b_step + '.result'] == places[b_right + '.request'] == places[b_right + '.result']
    assert places[b_step + '.result'] == places[prefix(C) + '_team.request']
    matching_links = [index for index, link in enumerate(value.generated.module.links)
        if (link.source.component, link.source.port, link.target.component, link.target.port)
        == (b_step, 'result', prefix(C) + '_team', 'request')]
    assert len(matching_links) == 1
    selected_connection = next(index for index, row in enumerate(value.plan['connections'])
        if row['source_member_id'] == B and row['source_exit_element_id'] == pids['/exit/result']
        and row['target_member_id'] == C and row['target_entry_element_id'] == gids['/entry/request'])
    assert matching_links[0] == len(plain.module.links) + selected_connection
    connection_locator = '/links/' + str(matching_links[0])
    consumed = [row for row in value.lowering_map['origins'] if row['member_id'] == B and row['kind'] == 'exit']
    assert {row['source_locator'] for row in consumed} == {'/exit/result', '/exit/other_result'}
    assert all(row['disposition'] == 'consumed' and row['declaration_locators'] == [connection_locator] for row in consumed)
    assert {row['member_id'] for row in value.lowering_map['graph_members']} == {A, C}
    for row in value.lowering_map['declarations']:
        for pointer in row['compiled_targets']:
            resolve_pointer(value.compiled.to_dict(), pointer)
    fresh = _fresh_registration(calls)
    before = counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reopened, value.revision.revision_ref, fresh)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert checked.lowering_map == value.lowering_map
    assert counts(core) == before
    assert observed.count('carrier_cut') == observed.count('origin_map') >= 2
    assert any(row['component'] == b_right for row in calls)
    assert_no_run(core)
    print('C_CF_REUSE=' + json.dumps({'observed_product_stages': observed,
        'actual_final_components': sorted({row['component'] for row in calls if row['component'].startswith('m_')}),
        'consumed_exit_aliases': sorted(row['source_locator'] for row in consumed),
        'introduced_link_for_aliases': connection_locator}, sort_keys=True))


@pytest.mark.parametrize('side', ('source', 'target'))
def test_real_member_internal_fusion_keeps_competing_carriers_visible(fixture, side):
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    plain = _publish_plain(author, _internally_fused('output' if side == 'source' else 'input'), 'plain:fused-' + side)
    gids, pids = _ids(graph), _ids(plain)
    if side == 'source':
        args = request(graph, command_id='assembly:duplicate-source-carrier',
            members=(AssemblyMemberV2(A, 'Plain', plain.revision.revision_ref),
                     AssemblyMemberV2(B, 'Graph', graph.revision.revision_ref),
                     AssemblyMemberV2(C, 'Graph', graph.revision.revision_ref)),
            connections=(AssemblyConnection(A, pids['/exit/result'], B, gids['/entry/request']),
                         AssemblyConnection(A, pids['/exit/other_result'], C, gids['/entry/request'])),
            completion=AssemblyCompletion(C, gids['/terminal']))
    else:
        args = request(graph, command_id='assembly:duplicate-target-carrier',
            members=(AssemblyMemberV2(A, 'Graph', graph.revision.revision_ref),
                     AssemblyMemberV2(B, 'Graph', graph.revision.revision_ref),
                     AssemblyMemberV2(C, 'Plain', plain.revision.revision_ref)),
            connections=(AssemblyConnection(A, gids['/exit/result'], C, pids['/entry/request']),
                         AssemblyConnection(B, gids['/exit/result'], C, pids['/entry/left_request'])),
            completion=AssemblyCompletion(C, pids['/terminal']))
    _assert_prewrite_reject(core, author, args, 'actual ' + side + ' carrier', calls)


def test_real_single_target_rejects_final_context_multiple_entry_aliases(fixture):
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    plain = _publish_plain(author, ModuleDeclaration.from_dict(_dual_input()), 'plain:dual-input')
    gids, pids = _ids(graph), _ids(plain)
    assert plain.compiled.symbolic.port_places['step.request'] != plain.compiled.symbolic.port_places['step.other_request']
    args = request(graph, command_id='assembly:single-target-alias',
        members=(AssemblyMemberV2(A, 'Graph', graph.revision.revision_ref), AssemblyMemberV2(B, 'Plain', plain.revision.revision_ref)),
        connections=(AssemblyConnection(A, gids['/exit/result'], B, pids['/entry/other_request']),),
        completion=AssemblyCompletion(B, pids['/terminal']))
    _assert_prewrite_reject(core, author, args, 'actual target carrier aliases multiple public entries', calls)


@pytest.mark.parametrize('alternative', (False, True), ids=('primary', 'alternative'))
def test_real_selected_terminal_carrier_cannot_hide_behind_an_internal_alias(fixture, alternative):
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    plain = _publish_plain(author, _internally_fused('output', alternative=alternative), 'plain:terminal-' + str(alternative))
    gids, pids = _ids(graph), _ids(plain)
    args = request(graph, command_id='assembly:terminal-alias-' + str(alternative),
        members=(AssemblyMemberV2(A, 'Selected', plain.revision.revision_ref), AssemblyMemberV2(B, 'Graph', graph.revision.revision_ref)),
        connections=(AssemblyConnection(A, pids['/exit/result'], B, gids['/entry/request']),),
        completion=AssemblyCompletion(A, pids['/terminal']))
    _assert_prewrite_reject(core, author, args, 'selected terminal carrier', calls)


@pytest.mark.parametrize('side', ('source', 'target'))
def test_real_cross_direction_final_context_alias_is_rejected(fixture, side):
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    document = simple_plain_module().to_dict()
    document['components'][0]['key'] = _key('passthrough')
    plain = _publish_plain(author, ModuleDeclaration.from_dict(document), 'plain:cross-' + side)
    left, right = (plain, graph) if side == 'source' else (graph, plain)
    lids, rids = _ids(left), _ids(right)
    args = request(graph, command_id='assembly:cross-' + side,
        members=(AssemblyMemberV2(A, 'Left', left.revision.revision_ref), AssemblyMemberV2(B, 'Right', right.revision.revision_ref)),
        connections=(AssemblyConnection(A, lids['/exit/result'], B, rids['/entry/request']),),
        completion=AssemblyCompletion(B, rids['/terminal']))
    _assert_prewrite_reject(core, author, args, 'cross-direction public boundary alias', calls)


@pytest.mark.parametrize('mismatch', ('schema', 'initial', 'capacity'))
def test_real_fusion_requires_exact_schema_initial_and_capacity(fixture, mismatch):
    core, _, author, selected, graph = fixture
    calls = []
    _configure_carriers(selected, calls)
    document = simple_plain_module().to_dict()
    if mismatch == 'schema':
        document['components'][0]['ports'][0]['schema'] = ALT_TEXT
        document['required_schemas'].append(ALT_TEXT)
        assert selected.declaration('schema', ALT_TEXT)['schema']['type'] == selected.declaration('schema', TEXT_SCHEMA)['schema']['type'] == 'string'
    else:
        document['components'][0]['key'] = _key(mismatch)
    plain = _publish_plain(author, ModuleDeclaration.from_dict(document), 'plain:mismatch-' + mismatch)
    gids, pids = _ids(graph), _ids(plain)
    args = request(graph, command_id='assembly:fusion-' + mismatch,
        members=(AssemblyMemberV2(A, 'Graph', graph.revision.revision_ref), AssemblyMemberV2(B, 'Plain', plain.revision.revision_ref)),
        connections=(AssemblyConnection(A, gids['/exit/result'], B, pids['/entry/request']),),
        completion=AssemblyCompletion(B, pids['/terminal']))
    _assert_prewrite_reject(core, author, args, 'Fusion token/schema/colour/initial/reusable/capacity mismatch', calls)
