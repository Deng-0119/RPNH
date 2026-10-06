"""Bounded real Assembly-v2 authority, exact pairing and ancestry checks."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import validate_assembly_revision
from cpn.rpnh.collaboration import assembly_v2 as assembly
from cpn.rpnh.collaboration import materials
from cpn.rpnh.registry._event_store import collaboration_descriptors as descriptors
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v2_publication import assert_no_run, fixture, request
from test_collaboration_assembly_v2_recovery import _facts, _payload, _version
from test_collaboration_graph_materials import registration


def _reference(qualified):
    ref = _version(qualified)
    return {'entity_type': ref.entity_type, 'logical_id': str(ref.entity_id), 'version_id': str(ref.version_id)}


@contextmanager
def _observe(monkeypatch):
    """Observe originals; never substitute successful authority or payload reads."""
    trace = {'reads': [], 'prepared_attempts': [], 'completed_materials': [],
             'completed_preparations': [], 'completed_closed_refs': []}
    read_original = ObjectStore.read_registered
    exact_original = descriptors.exact_prepared
    material_original = materials._material
    prepare_original = assembly._prepare_at
    closed_original = materials._validate_at
    def read(store, prepared):
        payload = read_original(store, prepared)
        trace['reads'].append({'ref': {'entity_type': prepared.object_type,
            'logical_id': str(prepared.logical_id), 'version_id': str(prepared.version_id)},
            'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
        return payload
    def exact(db, store, task_id, ref):
        assert db.in_transaction
        trace['prepared_attempts'].append(dict(ref))
        return exact_original(db, store, task_id, ref)
    def material(db, core, qualified, binding, schema_id):
        result = material_original(db, core, qualified, binding, schema_id)
        trace['completed_materials'].append({'ref': _reference(qualified), 'schema': schema_id})
        return result
    def prepare(*args, **kwargs):
        result = prepare_original(*args, **kwargs)
        trace['completed_preparations'].append({'command_id': result[0]['command_id'],
            'plan_sha256': hashlib.sha256(canonical_json(result[0])).hexdigest()})
        return result
    def closed(db, core, reference, *args):
        result = closed_original(db, core, reference, *args)
        trace['completed_closed_refs'].append(_reference(reference))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ObjectStore, 'read_registered', read)
        patch.setattr(descriptors, 'exact_prepared', exact)
        patch.setattr(materials, 'exact_prepared', exact)
        patch.setattr(materials, '_material', material)
        patch.setattr(assembly, '_prepare_at', prepare)
        patch.setattr(materials, '_validate_at', closed)
        yield trace


def _read_summary(trace):
    values, counts = {}, Counter()
    for item in trace['reads']:
        key = item['ref']['version_id']
        if key in values:
            assert values[key] == item
        values[key] = item
        counts[key] += 1
    return [{**values[key], 'count': counts[key]} for key in sorted(values)]


def _normal_control(core, value, member, selected, monkeypatch):
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with _observe(monkeypatch) as trace:
        checked = validate_assembly_revision(reader, value.revision.revision_ref, selected)
    assert checked.plan == value.plan and checked.lowering_map == value.lowering_map
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    refs = {'assembly_descriptor': value.revision.revision_ref,
            'generated_descriptor': value.revision.generated_revision_ref,
            'plan': value.revision.plan_ref, 'compiled': value.revision.compiled_inventory_ref,
            'lowering': value.revision.lowering_mapping_ref,
            'member_descriptor': member.revision.revision_ref}
    for field in ('definition_ref', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref'):
        refs['generated_' + field] = getattr(value.generated.revision, field)
        refs['member_' + field] = getattr(member.revision, field)
    for field in ('graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref'):
        if hasattr(member.revision, field):
            refs['member_' + field] = getattr(member.revision, field)
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    for row in value.plan['host_requirements']['declaration_refs']:
        refs['final_host:' + row['kind'] + ':' + row['key']] = SourceQualifiedResourceRef.from_dict(
            row['resource_ref'], catalog=core.catalog)
    observed = {row['ref']['version_id']: row for row in _read_summary(trace)}
    roles = {}
    for role, qualified in refs.items():
        expected = _reference(qualified)
        actual = observed[expected['version_id']]
        assert actual['ref'] == expected
        assert actual['sha256'] == hashlib.sha256(_payload(core, qualified)).hexdigest()
        roles[role] = expected
    assert _facts(core) == before
    assert_no_run(core)
    return {'roles': roles, 'actual_read_set': list(observed.values()),
            'read_calls': len(trace['reads']), 'unique_reads': len(observed)}


def _unchanged_payloads(core, refs):
    return {str(_version(ref).version_id): hashlib.sha256(_payload(core, ref)).hexdigest() for ref in refs}


@pytest.mark.parametrize('damage', ('plan_schema_commit', 'lowering_producer_relation'))
def test_actual_v2_material_roles_require_schema_commit_and_strong_producer(fixture, monkeypatch, damage):
    core, _, author, _, member = fixture
    value = author.publish(**request(member, command_id='assembly:authority:' + damage))
    control = _normal_control(core, value, member, registration(), monkeypatch)
    plan_schema = author.schemas[assembly.PLAN_V2_SCHEMA]
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    authority = SourceQualifiedResourceRef(author.binding['source_id'], plan_schema)
    exact_payloads = (value.revision.plan_ref, authority, value.revision.lowering_mapping_ref)
    retained = _unchanged_payloads(core, exact_payloads)
    facts = _facts(core)
    if damage == 'plan_schema_commit':
        with core.event_store.connect() as db:
            row = db.execute("SELECT e.event_id,e.aggregate_type FROM events e JOIN objects o "
                "ON o.transaction_id=e.transaction_id WHERE o.version_id=? AND e.event_type='transaction_committed/v1'",
                (str(plan_schema.resource_version_id),)).fetchone()
            assert row is not None and row['aggregate_type'] == 'transaction'
            db.execute("UPDATE events SET aggregate_type='incorrect_authority' WHERE event_id=?", (row['event_id'],))
        expected = 'canonical publication/commit closure'
    else:
        version = str(value.revision.lowering_mapping_ref.ref.resource_version_id)
        with core.event_store.connect() as db:
            rows = db.execute("SELECT r.relation_id,r.published_event_id,r.strength,e.payload_json "
                "FROM relations r JOIN events e ON e.event_id=r.published_event_id "
                "WHERE json_extract(r.source_json,'$.version_id')=?", (version,)).fetchall()
            assert len(rows) == 1 and rows[0]['strength'] == 'strong'
            relation = rows[0]
            event = json.loads(relation['payload_json']); assert event['strength'] == 'strong'
            event['strength'] = 'weak'
            core.catalog.validate_schema_ref('registry_v1/relation_published/v1', event)
            db.execute("UPDATE relations SET strength='weak' WHERE relation_id=?", (relation['relation_id'],))
            db.execute('UPDATE events SET payload_json=? WHERE event_id=?',
                       (canonical_json(event).decode(), relation['published_event_id']))
        expected = 'producer relation lacks exact canonical authority'
    assert _unchanged_payloads(core, exact_payloads) == retained and _facts(core) == facts
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with _observe(monkeypatch) as rejected_trace:
        with pytest.raises(RegistryConflict, match=expected) as rejected:
            validate_assembly_revision(reader, value.revision.revision_ref, registration())
    read_versions = {row['ref']['version_id'] for row in rejected_trace['reads']}
    if damage == 'plan_schema_commit':
        assert _reference(value.revision.plan_ref)['version_id'] in read_versions
        assert _reference(authority) in rejected_trace['prepared_attempts']
        assert _reference(authority)['version_id'] not in read_versions
        assert rejected_trace['completed_preparations'] == []
    else:
        assert [row['command_id'] for row in rejected_trace['completed_preparations']] == [value.revision.command_id]
        assert _reference(value.revision.generated_revision_ref) in rejected_trace['completed_closed_refs']
        assert _reference(value.revision.compiled_inventory_ref)['version_id'] in read_versions
        assert _reference(value.revision.lowering_mapping_ref) in rejected_trace['prepared_attempts']
        assert _reference(value.revision.lowering_mapping_ref)['version_id'] not in read_versions
    assert _facts(core) == facts and _unchanged_payloads(core, exact_payloads) == retained
    assert_no_run(core)
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': damage, 'control': control,
        'rejection': str(rejected.value), 'rejected_actual_read_set': _read_summary(rejected_trace),
        'completed_preparations': rejected_trace['completed_preparations'],
        'completed_closed_refs': rejected_trace['completed_closed_refs'], 'facts_unchanged': True}, sort_keys=True))


def _publish_record(core, record, key):
    from test_collaboration_assembly_v2_origins import _publish_descriptor
    from cpn.rpnh.collaboration import read_assembly_revision
    _publish_descriptor(core, record, assembly.ASSEMBLY_V2_TYPE, assembly.ASSEMBLY_V2_SCHEMA, key)
    assert read_assembly_revision(core, record.revision_ref) == record


def _rejected_reader(core, record, selected, monkeypatch, expected):
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with _observe(monkeypatch) as trace:
        with pytest.raises(RegistryConflict, match=expected) as rejected:
            validate_assembly_revision(reader, record.revision_ref, selected)
    assert _facts(core) == before
    assert_no_run(core)
    return trace, str(rejected.value)


def test_same_command_second_canonical_result_is_not_an_assembly_retry(fixture, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.collaboration import SourceQualifiedVersionRef
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    core, _, author, _, member = fixture
    value = author.publish(**request(member, command_id='assembly:one-result'))
    control = _normal_control(core, value, member, registration(), monkeypatch)
    reference = SourceQualifiedVersionRef(value.revision.revision_ref.source_id,
        VersionRef(assembly.ASSEMBLY_V2_TYPE, value.revision.revision_ref.ref.entity_id, new_id('resource_version')))
    claimed = replace(value.revision, revision_ref=reference)
    assert claimed.command_id == value.revision.command_id and claimed.plan_ref == value.revision.plan_ref
    _publish_record(core, claimed, 'claim:second-assembly-result')
    trace, rejection = _rejected_reader(core, claimed, registration(), monkeypatch, 'exact pair differs')
    assert trace['completed_preparations'] == [{'command_id': value.revision.command_id,
        'plan_sha256': hashlib.sha256(canonical_json(value.plan)).hexdigest()}]
    assert _reference(claimed.generated_revision_ref) not in trace['completed_closed_refs']
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': 'second_result_identity', 'control': control,
        'canonical_record_read': True, 'full_assembly_success': False, 'rejection': rejection,
        'completed_preparations': trace['completed_preparations'],
        'rejected_actual_read_set': _read_summary(trace)}, sort_keys=True))


def test_same_body_closed_revision_cannot_replace_the_exact_generated_pair(fixture, monkeypatch):
    from cpn.rpnh.collaboration import validate_closed_revision
    from test_collaboration_assembly_v2_origins import _prepare_claim
    core, _, author, _, member = fixture
    control_value = author.publish(**request(member, command_id='assembly:pair-control'))
    control = _normal_control(core, control_value, member, registration(), monkeypatch)
    prepared = _prepare_claim(fixture, 'assembly:pair-claim')
    plan, module, compiled, mapping, ids, documents, refs, reference, command, generated_ref = prepared
    key = assembly._command(plan['command_id'])
    plan_ref = author._publish_document(key + ':plan', assembly.PLAN_V2_SCHEMA, plan,
        descriptors={'assembly_author_command_v2': assembly._envelope(plan, documents)})
    original_generated = author.author.publish(module=module, element_ids=ids, command_id=command)
    assert original_generated.revision.revision_ref == generated_ref
    compiled_ref = author._publish_document(key + ':compiled', 'rpnh/executable_net/v1', documents[4])
    lowering_ref = author._publish_document(key + ':lowering', assembly.LOWERING_V2_SCHEMA, documents[5])
    assert (compiled_ref, lowering_ref) == refs[4:]
    alternate = author.author.publish(module=module, element_ids=ids, command_id='closed:alternate-pair')
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_closed_revision(reader, alternate.revision.revision_ref, registration())
    assert checked.revision.revision_ref != generated_ref
    assert canonical_json(checked.module.to_dict()) == canonical_json(original_generated.module.to_dict())
    assert canonical_json(checked.element_map) == canonical_json(original_generated.element_map)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(compiled.to_dict())
    claimed = assembly.AssemblyRevisionV2(reference, member.revision.owner_task_ref, author.producer,
        plan['command_id'], None, plan_ref, alternate.revision.revision_ref, compiled_ref, lowering_ref)
    assert claimed.revision_ref == assembly._result_ref(core, author.binding['source_id'], plan['command_id'], None)
    _publish_record(core, claimed, key)
    trace, rejection = _rejected_reader(core, claimed, registration(), monkeypatch, 'exact pair differs')
    assert trace['completed_preparations'] == [{'command_id': plan['command_id'],
        'plan_sha256': hashlib.sha256(canonical_json(plan)).hexdigest()}]
    assert _reference(alternate.revision.revision_ref) not in trace['completed_closed_refs']
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': 'alternate_generated_exact_pair', 'control': control,
        'alternate_independent_closed_proof': True, 'same_module_element_map_and_compiled': True,
        'expected_result_identity_preserved': True, 'canonical_record_read': True,
        'full_assembly_success': False, 'rejection': rejection,
        'completed_preparations': trace['completed_preparations'],
        'rejected_actual_read_set': _read_summary(trace)}, sort_keys=True))


def _rejected_member_publish(core, author, args, monkeypatch):
    original, selected = assembly._members_at, []
    writes = []
    original_write = author._publish_document
    def members(*values, **kwargs):
        result = original(*values, **kwargs)
        selected.extend(_reference(item.revision.revision_ref) for item in result.values())
        return result
    def publish(*values, **kwargs):
        writes.append(values[0])
        return original_write(*values, **kwargs)
    before = _facts(core)
    with monkeypatch.context() as patch:
        patch.setattr(assembly, '_members_at', members)
        patch.setattr(author, '_publish_document', publish)
        with _observe(monkeypatch) as trace:
            with pytest.raises(ValueError, match='plain v1 member cannot supply graph or opaque constraints proof') as rejected:
                author.publish(**args)
    assert selected and writes == [] and _facts(core) == before
    assert_no_run(core)
    return {'rejection': str(rejected.value), 'selected_full_member_refs': selected,
            'plan_writer_calls': 0, 'facts_unchanged': True, 'rejected_actual_read_set': _read_summary(trace)}


def test_cleared_constraints_do_not_turn_known_graph_into_plain_v1_proof(fixture, monkeypatch):
    from cpn.rpnh.collaboration import validate_closed_revision
    from cpn.rpnh.module import ModuleDeclaration
    core, _, author, _, member = fixture
    value = author.publish(**request(member, command_id='assembly:masquerade-control'))
    control = _normal_control(core, value, member, registration(), monkeypatch)
    document = member.module.to_dict(); document['designer_constraints'] = {}
    ids = {row['locator']: row['element_id'] for row in member.element_map['elements']}
    masquerade = author.author.publish(module=ModuleDeclaration.from_dict(document), element_ids=ids,
        command_id='closed:cleared-graph')
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_closed_revision(reader, masquerade.revision.revision_ref, registration())
    assert checked.revision.revision_ref.ref.entity_type == 'collaboration_net_revision/v1'
    assert not checked.module.designer_constraints
    assert any(component.key == 'rpnh/agent-workflow-graph/v3' for component in checked.module.components)
    rejected = _rejected_member_publish(core, author, request(masquerade, command_id='assembly:reject-masquerade'), monkeypatch)
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': 'plain_graph_masquerade', 'control': control,
        'independent_closed_proof': True, 'constraints_empty': True, 'known_graph_key_present': True, **rejected}, sort_keys=True))


def test_plain_only_generated_closed_revision_is_not_recursive_assembly_proof(fixture, monkeypatch):
    import uuid
    from cpn.components.basic import register_basic_components
    from cpn.rpnh.collaboration import AssemblyMemberV2, AssemblyCompletion, validate_closed_revision
    from cpn.rpnh.collaboration.materials import _elements
    from test_collaboration_assembly_v2_projection import simple_plain_module
    from test_collaboration_assembly_v2_publication import A
    core, _, author, selected, _ = fixture
    register_basic_components(selected)
    module = simple_plain_module()
    ids = {locator: 'element:' + uuid.uuid4().hex for locator in _elements(module)}
    member = author.author.publish(module=module, element_ids=ids, command_id='closed:plain-nesting-control')
    value = author.publish(**request(member, command_id='assembly:plain-only-control'))
    fresh = registration(); register_basic_components(fresh)
    control = _normal_control(core, value, member, fresh, monkeypatch)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    nested = validate_closed_revision(reader, value.revision.generated_revision_ref, fresh)
    assert not any(component.key in {f'rpnh/agent-workflow-graph/v{n}' for n in range(1, 5)} for component in nested.module.components)
    assert 'native_composition' in nested.module.designer_constraints
    assert 'assembly_member_constraints' in nested.module.designer_constraints
    terminal = next(row['element_id'] for row in nested.element_map['elements'] if row['locator'] == '/terminal')
    args = {'name': 'Nested', 'members': (AssemblyMemberV2(A, 'Generated plain', nested.revision.revision_ref),),
        'connections': (), 'completion': AssemblyCompletion(A, terminal), 'budget_policy': 'shared_exact',
        'deployment_intent': 'same_run_candidate', 'command_id': 'assembly:reject-recursive-plain'}
    rejected = _rejected_member_publish(core, author, args, monkeypatch)
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': 'plain_generated_recursive', 'control': control,
        'independent_closed_proof': True, 'known_graph_key_absent': True, 'native_composition_present': True,
        **rejected}, sort_keys=True))


def test_two_canonical_descriptor_cycle_rejects_before_plan_semantics(fixture, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.collaboration import SourceQualifiedVersionRef
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    core, _, author, _, member = fixture
    value = author.publish(**request(member, command_id='assembly:ancestry-control'))
    control = _normal_control(core, value, member, registration(), monkeypatch)
    base = value.revision.revision_ref
    refs = tuple(SourceQualifiedVersionRef(base.source_id,
        VersionRef(assembly.ASSEMBLY_V2_TYPE, base.ref.entity_id, new_id('resource_version'))) for _ in range(2))
    records = tuple(replace(value.revision, revision_ref=refs[index], parent_revision_ref=refs[1-index],
        command_id='assembly:cycle:' + str(index)) for index in range(2))
    for index, record in enumerate(records):
        _publish_record(core, record, 'claim:cycle:' + str(index))
    read_original, actual = assembly._read_at, []
    def read(*args):
        record = read_original(*args)
        actual.append(record.revision_ref)
        return record
    with monkeypatch.context() as patch:
        patch.setattr(assembly, '_read_at', read)
        trace, rejection = _rejected_reader(core, records[0], registration(), monkeypatch, 'parent ancestry contains a cycle')
    assert actual == list(refs) and trace['completed_preparations'] == []
    assert _reference(value.revision.plan_ref)['version_id'] not in {row['ref']['version_id'] for row in trace['reads']}
    print('C_AUTHORITY_PAIRING=' + json.dumps({'case': 'canonical_descriptor_cycle', 'control': control,
        'canonical_record_reads': [_reference(ref) for ref in actual], 'full_plan_semantics_reached': False,
        'full_assembly_success': False, 'rejection': rejection,
        'rejected_actual_read_set': _read_summary(trace)}, sort_keys=True))
