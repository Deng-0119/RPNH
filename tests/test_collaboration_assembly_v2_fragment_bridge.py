"""Complex real graph sources and two distinct final-fragment rejection gates."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.rpnh.agent_workflows import (
    TEXT_SCHEMA, WORKFLOW_GRAPH_COMPONENT_V3_KEY, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID, lower_agent_workflow_graph,
)
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, GraphModuleAuthor, SourceQualifiedVersionRef, ValidatedGraphRevision,
    graph_assembly_schema_data, make_graph_source, validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.collaboration._assembly_lowering import resolve_pointer
from cpn.rpnh.petri_contracts import LeaseIdentityDeclaration, PlaceDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from test_collaboration_assembly_v2_publication import A, B, assert_no_run, request
from test_collaboration_assembly_v2_recovery import _facts
from test_collaboration_graph_materials import forbidden_execution, registration
from test_collaboration_graph_source import graph_wire, recipe, source_ids


def _registration(mode='normal', calls=None):
    if mode == 'normal':
        return registration()
    selected = Registration()
    selected.register_schema(TEXT_SCHEMA, {'$id': TEXT_SCHEMA,
        '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
    selected.register_schema(WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3)
    selected.register_schema('application/graph_test_config/v1', {
        '$id': 'application/graph_test_config/v1', '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    def lower(config, context):
        if calls is not None:
            calls.append(context.component)
        original = lower_agent_workflow_graph(config, context)
        if mode == 'orphan_place':
            return replace(original, places=(*original.places, PlaceDeclaration('unexplained', TEXT_SCHEMA)))
        assert mode == 'resource_lease_identity'
        return replace(original, lease_identities=(*original.lease_identities, LeaseIdentityDeclaration('unexplained_lease')))
    # This distinct implementation is explicitly declared before any gateway
    # binding/publication; no existing HOST key or callable inventory is changed.
    selected.register_component(WORKFLOW_GRAPH_COMPONENT_V3_KEY, lower,
        identity={'implementation_id': 'test.graph_fragment.' + mode, 'revision': 'v1'},
        contracts={'config_schema': WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID})
    selected.register_executor('test/ordinary-agent/v1', forbidden_execution,
        identity={'implementation_id': 'test.graph_agent', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'config_schema': 'application/graph_test_config/v1'})
    for key in ('test/graph-terminal/v1', 'write_file', 'complete_interaction'):
        selected.register_tool(key, forbidden_execution,
            identity={'implementation_id': 'test.graph_tool', 'revision': 'v1'},
            contracts={'binding_protocol': 'rpnh/module_terminal/v1'} if key.startswith('test/') else {})
    return selected


def _setup(tmp_path, wire, *, mode='normal', attempts=12):
    schemas, types, paths = graph_assembly_schema_data()
    core = _RegistryCore(tmp_path / 'fragment-bridge', create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('fragment-bridge/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id='source-fragment-bridge', command_id='bind')
    ref = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    principal = {'principal_id': str(ref.entity_id), 'principal_version_id': str(ref.version_id), 'display_name': 'Fragment fixture'}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(principal), metadata=principal, media_type='application/json',
        schema_ref='registry_v1/principal/v1', idempotency_key='fixture:principal')
    producer = SourceQualifiedVersionRef('source-fragment-bridge', ref)
    calls = []
    selected = _registration(mode, calls)
    graph_author = GraphModuleAuthor(gateway, selected, producer)
    source = make_graph_source(wire)
    member = graph_author.publish(source=source, recipe=recipe(max_attempts_per_node=attempts),
        source_ids=source_ids(source), command_id='graph:fragment-bridge')
    # Establish that the actual member is fully valid before claiming a later
    # Assembly gate rejected it. Compiler-invalid synthetic PN is not coverage.
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_closed_revision(reader, member.revision.revision_ref, _registration(mode))
    assert isinstance(checked, ValidatedGraphRevision)
    assert checked.source == member.source and checked.source_map == member.source_map
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(member.compiled.to_dict())
    assert _facts(core) == before
    author = AssemblyAuthorV2(gateway, selected, producer)
    return core, author, member, calls


def _checked_assembly(core, value):
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, _registration())
    assert checked.plan == value.plan and checked.lowering_map == value.lowering_map
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert _facts(core) == before
    assert_no_run(reader)
    return checked


def _fanout_wire():
    # The four-node fanout/join source from the reviewed A pure projection test.
    wire = graph_wire()
    left = deepcopy(wire['nodes'][1]); left['node_id'] = 'left'
    left['output_ports'] = [{'port_id': 'left', 'artifact_id': 'left'}]
    right = deepcopy(left); right['node_id'] = 'right'
    right['output_ports'] = [{'port_id': 'right', 'artifact_id': 'right'}]
    join = deepcopy(wire['nodes'][1]); join['node_id'] = 'join'
    join['input_ports'] = [{'port_id': name, 'artifact_id': name} for name in ('left', 'right')]
    wire['nodes'] = [wire['nodes'][0], left, right, join]
    def edge(name, source, source_port, target, target_port):
        return {'arc_id': name, 'source': {'node_id': source, 'port_id': source_port},
                'target': {'node_id': target, 'port_id': target_port}, 'kind': 'dependency'}
    wire['arcs'] = [edge('to_left', 'draft', 'candidate', 'left', 'candidate'),
                   edge('to_right', 'draft', 'candidate', 'right', 'candidate'),
                   edge('left_join', 'left', 'left', 'join', 'left'),
                   edge('right_join', 'right', 'right', 'join', 'right')]
    wire['egress']['node_id'] = 'join'
    return wire


def test_real_fanout_join_source_preserves_shared_data_roles_after_reopen(tmp_path):
    core, author, member, _ = _setup(tmp_path, _fanout_wire())
    value = author.publish(**request(member, command_id='assembly:fanout'))
    checked = _checked_assembly(core, value)
    document = checked.compiled.to_dict()
    shared = []
    for identity in (A, B):
        rows = [row for row in checked.lowering_map['graph_source_origins']
            if row['member_id'] == identity and row['source_locator'] in ('/arcs/to_left', '/arcs/to_right')]
        pointers = [next(role['fragment_pointer'] for role in row['compiled_roles']
                         if role['role'] == 'edge_source_data') for row in rows]
        assert len(rows) == 2 and pointers[0] == pointers[1]
        assert resolve_pointer(document, pointers[0])['weight'] == 2
        assert all(row['declaration_locators'] == [] for row in rows)
        shared.append({'member_id': identity, 'source_arcs': sorted(row['source_locator'] for row in rows),
                       'shared_fragment_pointer': pointers[0], 'weight': 2})
    assert len(checked.lowering_map['graph_source_origins']) == 2 * len(member.source_map['elements'])
    assert len(checked.lowering_map['graph_fragment_coverage']) == 2
    print('C_FRAGMENT_BRIDGE=' + json.dumps({'case': 'fanout_join', 'member_full_proof': True,
        'assembly_full_readonly_proof': True, 'shared_data_roles': shared}, sort_keys=True))


def test_real_multiple_feedback_inputs_use_one_local_permit_and_shared_budget(tmp_path):
    wire = graph_wire(True)
    wire['nodes'][0]['input_ports'].append({'port_id': 'other', 'artifact_id': 'other'})
    wire['nodes'][1]['output_ports'].append({'port_id': 'other', 'artifact_id': 'other'})
    wire['arcs'].append({'arc_id': 'other_feedback', 'source': {'node_id': 'review', 'port_id': 'other'},
        'target': {'node_id': 'draft', 'port_id': 'other'}, 'kind': 'feedback'})
    core, author, member, _ = _setup(tmp_path, wire, attempts=None)
    checked = _checked_assembly(core, author.publish(**request(member, command_id='assembly:multi-feedback')))
    assert len(checked.generated.module.budget_buckets) == 3
    summary = []
    for name, fragment in checked.compiled.to_dict()['fragments'].items():
        permits = [arc for arc in fragment['arcs'] if arc['place'] == 'control__workflow_rework_permit' and arc['direction'] == 'input']
        assert len(permits) == 1 and permits[0]['weight'] == 1 and permits[0]['transition'] == 'draft__rework'
        operation = next(row for row in fragment['operations'] if row['name'] == 'draft__rework')
        assert operation['inputs'] == ['input__draft__changes', 'input__draft__other']
        assert not any(arc['place'] == 'control__workflow_rework_permit' and arc['outcome'] == 'interrupted' for arc in fragment['arcs'])
        summary.append({'component': name, 'rework_inputs': operation['inputs'], 'permit_inputs': len(permits), 'permit_weight': 1})
    assert len(summary) == 2
    assert all(bucket.max_attempts is None for bucket in checked.generated.module.budget_buckets
               if bucket.bucket_id != 'rpnh:workflow:rework')
    print('C_FRAGMENT_BRIDGE=' + json.dumps({'case': 'multiple_feedback', 'member_full_proof': True,
        'assembly_full_readonly_proof': True, 'shared_bucket_count': 3, 'local_permits': summary}, sort_keys=True))


@pytest.mark.parametrize('mode,expected', (('orphan_place', 'no specific source role'),
    ('resource_lease_identity', 'unsupported fragment topology')), ids=('unmapped_place', 'unsupported_lease'))
def test_real_valid_member_fragment_cannot_hide_unexplained_or_unsupported_topology(tmp_path, monkeypatch, mode, expected):
    core, author, member, calls = _setup(tmp_path, graph_wire(), mode=mode)
    fragment = member.compiled.to_dict()['fragments']['team']
    if mode == 'orphan_place':
        assert any(row['name'] == 'unexplained' for row in fragment['places'])
    else:
        assert fragment['lease_identities'] == [{'name': 'unexplained_lease', 'kind': 'resource', 'slot': None}]
    before = _facts(core)
    old_calls = len(calls)
    plan_calls = []
    original = author._publish_document
    def observe(*args, **kwargs):
        plan_calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(author, '_publish_document', observe)
    with pytest.raises(ValueError, match=expected) as rejected:
        author.publish(**request(member, command_id='assembly:reject-' + mode))
    final_context_calls = [name for name in calls[old_calls:] if name.startswith('m_')]
    assert len(final_context_calls) == 2 and set(final_context_calls) == {
        'm_' + A.split(':')[1] + '_team', 'm_' + B.split(':')[1] + '_team'}
    assert plan_calls == [] and _facts(core) == before
    assert core.event_store.object_rows_by_type('collaboration_assembly_revision/v2') == ()
    assert_no_run(core)
    print('C_FRAGMENT_BRIDGE=' + json.dumps({'case': mode, 'member_full_proof': True,
        'member_compiler_valid': True, 'actual_final_lower_calls': final_context_calls,
        'plan_writer_calls': 0, 'rejection': str(rejected.value)}, sort_keys=True))
