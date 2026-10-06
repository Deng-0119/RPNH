"""Real finite typed members -> v9 A/G, actual contexts and closed recovery."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import uuid

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, CONFIG_SCHEMA, lower_operation
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyMemberV2, AssemblyAuthorV9, AssemblyMemberV9,
    AssemblyConnection, AssemblyCompletion, ClosedModuleAuthor, SourceQualifiedVersionRef,
    typed_assembly_schema_data, validate_assembly_revision, validate_closed_revision,
    validate_generated_assembly_v9,
)
from cpn.rpnh.collaboration import assembly_v9 as assembly
from cpn.rpnh.collaboration._assembly_lowering import prefix
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_ir import ControlIR, ControlIRError, PROOF_KEY
from cpn.rpnh.executable_net import load_compiled_control_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import ResetArcDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_control_ir_compiler import (author_document, contract, must_not_execute, DATA, CONFIG, EXECUTOR,
                                     INT, RATIONAL, lit, op, read)
from test_typed_author_interoperation import counts, no_runtime

A, B, C = ('member:' + letter * 32 for letter in 'abc')
FIN = 'finite_control_ir_v1'
PLAIN = 'plain_closed_v1'
UNIT = {'kind': 'Int', 'unit': {'bytes': 1}}


def registration(*, context=False, mode=None, unit=False):
    reg = Registration()
    reg.register_schema(CONFIG_SCHEMA_ID, CONFIG_SCHEMA)
    reg.register_schema(DATA, {'$id': DATA, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
    reg.register_schema(CONFIG, {'$id': CONFIG, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    for side in ('left', 'right'):
        schema = 'application/' + side + '_context/v1'
        reg.register_schema(schema, {'$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'integer'})
    identity = {'implementation_id': 'offline_typed_assembly_v9', 'revision': str((context, mode, unit))}
    def lower(config, binding):
        fragment = lower_operation(config, binding)
        final = binding.component.startswith('m_')
        if context:
            names = {p.name: p.name + ('_final' if final else '_source') for p in fragment.places}
            fragment = replace(fragment,
                places=tuple(replace(p, name=names[p.name], capacity=sum(binding.budgets.values())) for p in fragment.places),
                ports=tuple(replace(p, place=names[p.place]) for p in fragment.ports),
                arcs=tuple(replace(a, place=names[a.place]) for a in fragment.arcs))
        if final and mode == 'int_float':
            fragment = replace(fragment, operations=(replace(fragment.operations[0], config={'threshold': 7.0}),))
        if final and mode == 'read_arc':
            fragment = replace(fragment, arcs=tuple(replace(a, mode='read') if a.direction == 'input' else a for a in fragment.arcs))
        if final and mode == 'weight':
            fragment = replace(fragment, arcs=tuple(replace(a, weight=2) if a.direction == 'input' else a for a in fragment.arcs))
        if final and mode == 'reset':
            fragment = replace(fragment, reset_arcs=(ResetArcDeclaration(fragment.places[0].name, fragment.transitions[0].name, 'erase', 'complete'),))
        if final and mode == 'hidden':
            extra = replace(fragment.operations[0], name='hidden')
            transition = replace(fragment.transitions[0], name='hidden', operation='hidden')
            arcs = tuple(replace(a, transition='hidden') for a in fragment.arcs)
            fragment = replace(fragment, operations=(*fragment.operations, extra),
                transitions=(*fragment.transitions, transition), arcs=(*fragment.arcs, *arcs))
        return fragment
    atomic = contract()
    if unit:
        atomic['config_type']['fields']['threshold'] = UNIT
    reg.register_component('operation', lower, identity=identity, contracts={'config_schema': CONFIG_SCHEMA_ID})
    reg.register_executor(EXECUTOR, must_not_execute, identity=identity,
        contracts={'config_schema': CONFIG, 'control_ir': atomic})
    reg.register_tool('test/terminal/v1', must_not_execute, identity=identity, contracts={'config_schema': CONFIG})
    return reg


def world(path, **options):
    schemas, types, paths = typed_assembly_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(path, create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('typed-assembly/v9',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id='source-typed-v9', command_id='typed:source')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Offline typed assembly'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
        version_id=principal.version_id, payload=canonical_json(body), metadata=body,
        media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='fixture:principal')
    reg = registration(**options)
    producer = SourceQualifiedVersionRef('source-typed-v9', principal)
    author = ClosedModuleAuthor(gateway, reg, producer)
    composer = AssemblyAuthorV9(gateway, reg, producer)
    return core, gateway, reg, producer, author, composer


def publish_member(author, reg, command, *, plain=False, budget=None, capacity=10, parent=None, unit=False, bucket=None, binding_scope=None):
    doc = author_document()
    doc['bindings']['capacity']['value'] = capacity
    doc['bindings']['capacity']['origin']['ref']['version'] = capacity
    if budget:
        side, value = budget
        doc['module']['budgets'] = {side: value}
        doc['module']['required_schemas'].append('application/' + side + '_context/v1')
    if bucket is not None:
        doc['module']['budget_buckets'] = [bucket]
        doc['module']['components'][0]['operations'][0]['budget_binding'] = {'bucket_id': bucket['bucket_id'], 'budget_scope': binding_scope or bucket['budget_scope'], 'finalization_scope': bucket['finalization_scope']}
    if unit:
        doc['bindings']['capacity']['type'] = UNIT
        doc['atoms'][0]['config']['threshold'] = op('add', op('floor', op('mul', lit('0.6', RATIONAL), read('capacity'))), lit(1, UNIT))
        doc['atoms'][0]['guard'] = op('gt', read('capacity'), lit(0, UNIT))
    compiled = compile_module(ControlIR.from_dict(doc), reg)
    module = compiled.source
    if plain:
        value = module.to_dict()
        value['designer_constraints'] = {}
        module = ModuleDeclaration.from_dict(value)
    ids = {path: 'element:' + uuid.uuid5(uuid.NAMESPACE_URL, 'typed-v9-member:' + path).hex for path in _elements(module)}
    member = author.publish(module=module, element_ids=ids, command_id=command, parent_ref=parent)
    return member, ids, doc


def request(left, right=None, *, command='assembly:first', claims=(FIN, FIN), labels=('Same', 'Same'), parent=None):
    right = left if right is None else right
    l, lids, _ = left
    r, rids, _ = right
    return dict(name='FinitePair', members=(AssemblyMemberV9(A, labels[0], l.revision.revision_ref, claims[0]),
        AssemblyMemberV9(B, labels[1], r.revision.revision_ref, claims[1])),
        connections=(AssemblyConnection(A, lids['/exit/result'], B, rids['/entry/request']),),
        completion=AssemblyCompletion(B, rids['/terminal']), budget_policy='shared_exact',
        deployment_intent='same_run_candidate', command_id=command, parent_ref=parent)


def check_pair(core, reg, value):
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, reg)
    paired = validate_generated_assembly_v9(reader, value.revision.revision_ref, value.revision.generated_revision_ref, reg)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert paired.revision == checked.revision
    assert PROOF_KEY not in checked.generated.module.designer_constraints
    with pytest.raises(ControlIRError, match='control_proof_required'):
        load_compiled_control_net(checked.compiled.to_json())
    assert counts(core) == before
    no_runtime(core)
    return checked


def test_real_typed_pair_repeated_instance_replay_and_reopen(tmp_path):
    core, _, reg, _, author, composer = world(tmp_path / 'pair')
    item = publish_member(author, reg, 'typed:member')
    args = request(item)
    value = composer.publish(**args)
    check_pair(core, reg, value)
    before = counts(core)
    assert composer.publish(**args).revision == value.revision
    assert counts(core) == before
    proofs = value.lowering_map['member_proofs']
    assert len(proofs) == 2 and {row['revision_ref']['ref']['version_id'] for row in proofs} == {str(item[0].revision.revision_ref.ref.version_id)}
    assert all(row['resolution']['constraints'][PROOF_KEY]['author'] == item[2] for row in proofs)
    assert len(value.compiled.operations) == 2
    names = {o.name for o in value.compiled.symbolic.operations}
    assert names == {prefix(A) + '_step.run', prefix(B) + '_step.run'}
    ports = value.compiled.symbolic.port_places
    assert ports[prefix(A) + '_step.result'] == ports[prefix(B) + '_step.request']
    assert value.compiled.source.terminal.source.component == prefix(B) + '_step'
    assert item[0].module.designer_constraints[PROOF_KEY]['author'] == item[2]
    print('TYPED_V9_PAIR', json.dumps({'assembly': value.revision.to_dict(), 'proof_count': len(proofs), 'operations': sorted(names)}, sort_keys=True))


def test_actual_final_context_fragments_units_and_successor(tmp_path):
    core, _, reg, _, author, composer = world(tmp_path / 'context', context=True, unit=True)
    left = publish_member(author, reg, 'typed:left', budget=('left', 10), unit=True)
    right = publish_member(author, reg, 'typed:right', budget=('right', 20), capacity=20, unit=True)
    first = composer.publish(**request(left, right))
    checked = check_pair(core, reg, first)
    assert [o.config['threshold'] for o in checked.compiled.symbolic.operations] == [7, 13]
    for row in checked.lowering_map['fragment_origins']:
        assert row['context']['budgets'] == {'left': 10, 'right': 20}
        assert {'application/left_context/v1', 'application/right_context/v1'} <= set(row['context']['required_schemas'])
        actual = checked.compiled.fragments[row['component']]
        assert all(p.name.endswith('_final') and p.capacity == 30 for p in actual.places)
        member = left if row['member_id'] == A else right
        original = member[0].compiled.fragments['step']
        assert all(p.name.endswith('_source') for p in original.places) and actual != original
        proof = next(p for p in checked.lowering_map['member_proofs'] if p['member_id'] == row['member_id'])['resolution']['constraints'][PROOF_KEY]
        assert proof['evaluations'][0]['type'] == UNIT and proof['evaluations'][0]['read_set']['heads'] == []
    changed = publish_member(author, reg, 'typed:right:successor', budget=('right', 30), capacity=30,
        parent=right[0].revision.revision_ref, unit=True)
    successor = composer.publish(**request(left, changed, command='assembly:successor', labels=('Renamed', 'Same'), parent=first.revision.revision_ref))
    assert successor.revision.revision_ref.ref.entity_id == first.revision.revision_ref.ref.entity_id
    assert successor.revision.revision_ref != first.revision.revision_ref
    assert successor.compiled.source.budgets == {'left': 10, 'right': 30}
    assert {row['member_id'] for row in successor.lowering_map['member_proofs']} == {A, B}
    identity_map = lambda item: {row['locator']: row['element_id'] for row in item.generated.element_map['elements']}
    assert identity_map(successor) == identity_map(first)
    check_pair(core, reg, successor)
    check_pair(core, reg, first)
    print('TYPED_V9_CONTEXT', json.dumps({'first': first.revision.revision_ref.to_dict(), 'next': successor.revision.revision_ref.to_dict(), 'original_capacity': [10, 20], 'final_capacity': 30, 'next_capacity': 40, 'unit': UNIT}, sort_keys=True))


def test_mixed_plain_and_legacy_v2_control(tmp_path, monkeypatch):
    core, gateway, reg, producer, author, composer = world(tmp_path / 'mixed')
    typed = publish_member(author, reg, 'typed:member')
    plain = publish_member(author, reg, 'plain:member', plain=True)
    import cpn.rpnh.petri_contracts as contracts
    observed = []
    validate_schema = contracts._validate_schema_instance
    def observe(schema, instance, *, offline_schema_validation=False):
        observed.append(offline_schema_validation)
        return validate_schema(schema, instance, offline_schema_validation=offline_schema_validation)
    plain_args = request(plain, claims=(PLAIN, PLAIN), command='assembly:plain')
    with monkeypatch.context() as patch:
        patch.setattr(contracts, '_validate_schema_instance', observe)
        check_pair(core, reg, composer.publish(**request(typed, plain, claims=(FIN, PLAIN))))
        check_pair(core, reg, composer.publish(**plain_args))
    assert observed and all(observed)
    print('TYPED_V9_OFFLINE_CALLS', len(observed))
    old = AssemblyAuthorV2(gateway, reg, producer)
    legacy = {**plain_args, 'command_id': 'assembly:legacy', 'members': tuple(AssemblyMemberV2(m.member_id, m.display_name, m.revision_ref) for m in plain_args['members'])}
    assert old.publish(**legacy).compiled.source.name == 'FinitePair'
    bad = {**request(typed), 'members': tuple(AssemblyMemberV2(m.member_id, m.display_name, m.revision_ref) for m in request(typed)['members'])}
    before = counts(core)
    with pytest.raises(ValueError, match='plain v1 member cannot supply'):
        old.publish(**bad)
    assert counts(core) == before
    no_runtime(core)


class DurableCut(RuntimeError):
    pass


def test_complete_command_cut_reopens_new_owner_and_recovers(tmp_path, monkeypatch):
    core, gateway, reg, producer, author, composer = world(tmp_path / 'recovery')
    member = publish_member(author, reg, 'typed:member')
    args = request(member)
    key = assembly._command(args['command_id'])
    expected_a = assembly._result_ref(core, composer.binding['source_id'], args['command_id'], None)
    _, _, expected_g = assembly._generated_ref(core, composer.binding['source_id'], args['command_id'], None)
    original = RegistryTransaction.commit
    def commit_then_cut(tx):
        result = original(tx)
        if tx.event_store is core.event_store and tx.idempotency_key == key + ':plan':
            assert tx._closed
            raise DurableCut('complete plan committed')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', commit_then_cut)
        with pytest.raises(DurableCut, match='complete plan committed'):
            composer.publish(**args)
    assert core.event_store.object_row(expected_g.ref.version_id) is None
    assert core.event_store.object_row(expected_a.ref.version_id) is None
    plan_ref = assembly._material_ref(core, composer.binding, key + ':plan')
    plan_path = core.object_store.path_for_version(plan_ref.ref.resource_version_id)
    locked_bytes = plan_path.read_bytes()
    metadata = json.loads(core.event_store.object_row(plan_ref.ref.resource_version_id)['metadata_json'])
    command = json.loads(metadata['descriptors']['assembly_author_command_v9'])
    assert len(command['prepared_materials']) == 6 and command['plan'] == json.loads(locked_bytes)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV9(next_gateway, registration(), producer)
    before = counts(reopened)
    changed = {**args, 'members': (replace(args['members'][0], display_name='Changed'), args['members'][1])}
    with pytest.raises(RegistryConflict, match='conflict'):
        replay.publish(**changed)
    assert counts(reopened) == before and plan_path.read_bytes() == locked_bytes
    value = replay.publish(**args)
    assert value.revision.revision_ref == expected_a and value.revision.generated_revision_ref == expected_g
    assert value.plan == command['plan']
    for spec in command['prepared_materials']:
        resource_version = spec['resource_ref']['ref']['resource_version_id']
        raw = reopened.object_store.path_for_version(TypedId.parse(resource_version, expected="resource_version")).read_bytes()
        assert raw == canonical_json(spec['document'])
    check_pair(reopened, replay.registration, value)
    final = counts(reopened)
    assert replay.publish(**args).revision == value.revision and counts(reopened) == final
    print('TYPED_V9_RECOVERY', json.dumps({'assembly': expected_a.to_dict(), 'generated': expected_g.to_dict(), 'complete_materials': 6, 'locked_plan_sha256': hashlib.sha256(locked_bytes).hexdigest()}, sort_keys=True))


@pytest.mark.parametrize('mode,expected', [('int_float', 'typed_config_lowering_mismatch'), ('read_arc', 'atomic_arc_lowering_mismatch'), ('weight', 'atomic_arc_lowering_mismatch'), ('hidden', 'undeclared_atomic_operation'), ('reset', 'Reset arc selector lacks')], ids=['int_float', 'read_arc', 'weight', 'hidden', 'reset'])
def test_actual_final_lower_cannot_erase_typed_semantics(tmp_path, mode, expected):
    core, _, reg, _, author, composer = world(tmp_path / mode, mode=mode)
    member = publish_member(author, reg, 'typed:source')
    assert load_compiled_control_net(member[0].compiled.to_json()).source.components[0].operations[0].config == {'threshold': 7}
    before = counts(core)
    with pytest.raises(ValueError, match=expected):
        composer.publish(**request(member))
    assert counts(core) == before
    no_runtime(core)


def test_exact_history_roles_refuse_laundering_before_lower(tmp_path, monkeypatch):
    core, _, reg, _, author, composer = world(tmp_path / 'history')
    typed = publish_member(author, reg, 'typed:root')
    washed = publish_member(author, reg, 'plain:washed', plain=True, parent=typed[0].revision.revision_ref)
    fresh = publish_member(author, reg, 'plain:fresh', plain=True)
    before = counts(core)
    import cpn.rpnh.compiler as compiler
    original = compiler._compile_module_offline
    observed = []
    def observe(module, selected):
        observed.append(module.name)
        return original(module, selected)
    with monkeypatch.context() as patch:
        patch.setattr(compiler, '_compile_module_offline', observe)
        with pytest.raises(RegistryConflict, match='plain member cannot erase'):
            composer.publish(**request(washed, claims=(PLAIN, PLAIN)))
    assert observed == [] and counts(core) == before
    with pytest.raises(RegistryConflict, match='plain member cannot erase'):
        composer.publish(**request(typed, claims=(PLAIN, PLAIN)))
    with pytest.raises(RegistryConflict, match='complete original ControlIR proof'):
        composer.publish(**request(fresh))
    assert counts(core) == before
    # A fresh ordinary root with identical declaration content is legitimate.
    check_pair(core, reg, composer.publish(**request(fresh, claims=(PLAIN, PLAIN), command='assembly:fresh')))
    no_runtime(core)


def test_bad_boundaries_budget_and_pair_reject(tmp_path):
    core, _, reg, _, author, composer = world(tmp_path / 'boundaries')
    one = publish_member(author, reg, 'typed:one', budget=('left', 10))
    conflict = publish_member(author, reg, 'typed:conflict', budget=('left', 11))
    bucket = {'bucket_id': 'shared', 'budget_scope': 'scope', 'finalization_scope': None, 'max_attempts': 2}
    bound = publish_member(author, reg, 'typed:bucket', bucket=bucket)
    mismatch = publish_member(author, reg, 'typed:bucket:conflict', bucket={**bucket, 'max_attempts': 3})
    bad_scope = publish_member(author, reg, 'typed:bucket:scope', bucket=bucket, binding_scope='wrong')
    before = counts(core)
    with pytest.raises(ValueError, match='shared_exact conflicting budget bucket'):
        composer.publish(**request(bound, mismatch))
    with pytest.raises(ValueError, match='member budget binding differs'):
        composer.publish(**request(bad_scope))
    with pytest.raises(ValueError, match='shared_exact conflicting budget'):
        composer.publish(**request(one, conflict))
    args = request(one)
    with pytest.raises(ValueError, match='terminal instance output'):
        composer.publish(**{**args, 'completion': AssemblyCompletion(A, one[1]['/terminal'])})
    with pytest.raises(ValueError, match='exact member boundary'):
        composer.publish(**{**args, 'connections': (AssemblyConnection(A, one[1]['/terminal'], B, one[1]['/entry/request']),)})
    triple = (*args['members'], AssemblyMemberV9(C, 'Third', one[0].revision.revision_ref, FIN))
    with pytest.raises(ValueError, match='multiple consumers'):
        composer.publish(**{**args, 'members': triple, 'connections': (*args['connections'], AssemblyConnection(A, one[1]['/exit/result'], C, one[1]['/entry/request']))})
    with pytest.raises(ValueError, match='multiple producers'):
        composer.publish(**{**args, 'members': triple, 'connections': (AssemblyConnection(A, one[1]['/exit/result'], C, one[1]['/entry/request']), AssemblyConnection(B, one[1]['/exit/result'], C, one[1]['/entry/request']))})
    assert counts(core) == before
    value = composer.publish(**args)
    with pytest.raises(RegistryConflict, match='exact complete Assembly v9 and generated pair'):
        validate_generated_assembly_v9(core, value.revision.revision_ref, one[0].revision.revision_ref, reg)
    no_runtime(core)


def test_persisted_proof_projection_bytes_and_authority_refuse(tmp_path):
    core, _, reg, _, author, composer = world(tmp_path / 'corruption')
    member = publish_member(author, reg, 'typed:member')
    value = composer.publish(**request(member))
    check_pair(core, reg, value)
    before = counts(core)
    cases = []
    proof = deepcopy(member[0].module.to_dict())
    proof['designer_constraints'][PROOF_KEY]['evaluations'][0]['value'] = 8
    cases.append(('original_proof', member[0].revision.definition_ref, proof))
    for field, damage in [('source_component_locator', '/components/missing'), ('component', 'wrong_namespace'), ('fragment_pointer', '/fragments/missing')]:
        changed = deepcopy(value.lowering_map)
        changed['fragment_origins'][0][field] = damage
        cases.append((field, value.revision.lowering_mapping_ref, changed))
    inventory = deepcopy(value.compiled.to_dict())
    inventory['fragments'][prefix(A) + '_step']['operations'][0]['config']['threshold'] = 7.0
    cases.append(('actual_fragment_bytes', value.revision.compiled_inventory_ref, inventory))
    observed = []
    for label, reference, document in cases:
        path = core.object_store.path_for_version(reference.ref.resource_version_id)
        original = path.read_bytes()
        try:
            path.write_bytes(canonical_json(document))
            with pytest.raises((RegistryConflict, ObjectIntegrityError, ValueError)) as rejected:
                validate_generated_assembly_v9(core, value.revision.revision_ref, value.revision.generated_revision_ref, reg)
            observed.append({'case': label, 'error': type(rejected.value).__name__})
            assert counts(core) == before
        finally:
            path.write_bytes(original)
    check_pair(core, reg, value)
    schema = composer.schemas[assembly.PLAN_V9_SCHEMA]
    with core.event_store.connect() as db:
        row = db.execute("SELECT e.event_id,e.aggregate_type FROM events e JOIN objects o ON o.transaction_id=e.transaction_id WHERE o.version_id=? AND e.event_type='transaction_committed/v1'", (str(schema.resource_version_id),)).fetchone()
        assert row is not None
        db.execute("UPDATE events SET aggregate_type='incorrect_authority' WHERE event_id=?", (row['event_id'],))
    with pytest.raises(RegistryConflict, match='canonical publication/commit closure'):
        validate_generated_assembly_v9(core, value.revision.revision_ref, value.revision.generated_revision_ref, reg)
    assert counts(core) == before
    no_runtime(core)
    print('TYPED_V9_CORRUPTION', json.dumps(observed, sort_keys=True))
