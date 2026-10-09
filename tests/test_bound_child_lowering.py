"""Pure compiler and real Registry tests. Native evidence remains explicit D0 injection."""
from dataclasses import replace
import json
import pytest
from cpn.rpnh.bound_child_lowering import with_bound_origin, ORIGIN_SYMBOL, ORIGIN_SCHEMA
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import DeclarationError
from test_static_lease_reads import _registration, _simple_module


def test_generic_origin_compiler_roundtrip():
    reg = _registration(); original = _simple_module()
    plain = compile_module(original, reg)
    module = with_bound_origin(original)
    bound = compile_module(module, reg)
    assert with_bound_origin(module) == module
    assert compile_module(original, reg) == plain
    assert all(p.schema != ORIGIN_SCHEMA for p in plain.symbolic.places)
    assert bound.symbolic.variable_resource_arcs == plain.symbolic.variable_resource_arcs
    assert len(bound.symbolic.places) == len(plain.symbolic.places) + 1
    assert load_compiled_net(bound.to_dict()) == bound
    assert bound.place_aliases[ORIGIN_SYMBOL] == ORIGIN_SYMBOL
    assert {a.transition for a in bound.symbolic.arcs if a.place == ORIGIN_SYMBOL} == {t.name for t in bound.symbolic.transitions}


@pytest.mark.parametrize('damage', ['missing_arc', 'consume', 'duplicate', 'schema', 'constraint', 'alias'])
def test_bound_wire_cannot_rewrite_mechanical_structure(damage):
    doc = compile_module(with_bound_origin(_simple_module()), _registration()).to_dict()
    origin = next(a for a in doc['symbolic']['arcs'] if a['place'] == ORIGIN_SYMBOL)
    if damage == 'missing_arc': doc['symbolic']['arcs'].remove(origin)
    elif damage == 'consume': origin['mode'] = 'consume'
    elif damage == 'duplicate': doc['symbolic']['arcs'].append(origin)
    elif damage == 'schema': doc['source']['required_schemas'].remove(ORIGIN_SCHEMA)
    elif damage == 'constraint': doc['source']['designer_constraints']['rpnh_parent_bound_origin_v1']['resource_input'] = 'elsewhere'
    else: del doc['place_aliases'][ORIGIN_SYMBOL]
    with pytest.raises((DeclarationError, ValueError)): load_compiled_net(doc)


def test_mechanical_origin_real_registry_start_success(tmp_path, monkeypatch):
    from bound_lowering_fixtures import compiled_bound_owner
    from test_static_lease_reads import current, finish
    from cpn.rpnh.registry.parent_bound import assert_bound_integrity
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    initial = next(t.state for t in current(owner)[2].tokens if t.state.place == ORIGIN_SYMBOL)
    admission = owner.admit('step.run', logical_tau=0, command_id='actual:admit')
    assert initial.token_ref in admission.preflight.claimed_token_refs
    finish(owner, admission, 'actual')
    assert next(t.state for t in current(owner)[2].tokens if t.state.place == ORIGIN_SYMBOL) == initial
    assert_bound_integrity(owner._core)


def test_fanout_branches_share_one_origin_without_serialization(tmp_path, monkeypatch):
    from copy import deepcopy
    from bound_lowering_fixtures import compiled_bound_owner
    from test_static_lease_reads import current
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    from cpn.rpnh.marking import TeamNetMarking
    document = _simple_module('Fanout').to_dict()
    seed = document['components'][0]; seed['name'] = 'seed'
    seed['ports'][1]['name'] = 'left'
    seed['ports'].append({**seed['ports'][1], 'name': 'right'})
    op = seed['operations'][0]; op['outputs'] = ['left', 'right']
    op['outcomes'][0]['products'] = [{'port': 'left'}, {'port': 'right'}]
    for name in ('left', 'right'):
        component = deepcopy(_simple_module().to_dict()['components'][0]); component['name'] = name
        document['components'].append(component)
    document['entry'] = {'request': {'component': 'seed', 'port': 'request'}}
    document['links'] = [{'source': {'component': 'seed', 'port': name},
                          'target': {'component': name, 'port': 'request'}} for name in ('left', 'right')]
    document['exit'] = {name: {'component': name, 'port': 'result'} for name in ('left', 'right')}
    document['terminal']['source']['component'] = 'right'
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), ModuleDeclaration.from_dict(document))
    initial = next(t.state for t in current(owner)[2].tokens if t.state.place == ORIGIN_SYMBOL)
    seed = owner.admit('seed.run', logical_tau=0, command_id='seed:admit')
    execution = owner.start(seed, command_id='seed:start')
    products = owner.products(execution, outcome_id='complete',
        products={'seed.left': (canonical_json('left'),), 'seed.right': (canonical_json('right'),)}, command_id='seed:products')
    owner.succeed(products, command_id='seed:success')
    claims = [owner.admit(name + '.run', logical_tau=1, command_id=name + ':admit') for name in ('left', 'right')]
    assert all(initial.token_ref in claim.preflight.claimed_token_refs for claim in claims)
    assert len(set(claims[0].preflight.claimed_token_refs) & set(claims[1].preflight.claimed_token_refs)) == 1
    for name, claim in zip(('left', 'right'), claims):
        execution = owner.start(claim, command_id=name + ':start')
        products = owner.products(execution, outcome_id='complete', products={name + '.result': (canonical_json('done'),)}, command_id=name + ':products')
        owner.succeed(products, command_id=name + ':success')
        executable, structure, marking = current(owner)
        assert next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL) == initial
        remaining = install_active_module_claims(owner._core, TeamNetMarking.from_authority(structure, marking), executable.net_ref)
        assert len(remaining) == (1 if name == 'left' else 0)


@pytest.mark.parametrize('mode', ['read', 'produce'])
def test_existing_variable_arcs_survive_compiler_origin_and_registry_success(tmp_path, monkeypatch, mode):
    import test_static_lease_interactions as interactions
    from bound_lowering_fixtures import compiled_bound_owner
    from test_static_lease_reads import current
    from cpn.rpnh.registry.parent_bound import assert_bound_integrity
    def start(module, registration, **kwargs):
        return compiled_bound_owner(kwargs['run_dir'], monkeypatch, registration, module,
            entries=kwargs['entry_inputs'], resources=kwargs['resource_inputs'], task=kwargs['task_input'])[0]
    monkeypatch.setattr(interactions, 'start_run', start)
    owner = interactions._world(tmp_path / 'child', variable_mode=mode, static_read=True)
    executable, structure, marking = current(owner)
    assert len(structure.compiled.symbolic.variable_resource_arcs) == 1
    initial = next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL)
    admission = owner.admit('step.run', logical_tau=1, command_id='step:admit')
    assert initial.token_ref in admission.preflight.claimed_token_refs
    interactions._settle(owner, admission)
    assert next(t.state for t in current(owner)[2].tokens if t.state.place == ORIGIN_SYMBOL) == initial
    assert_bound_integrity(owner._core)


def test_origin_does_not_override_business_pn_guard(tmp_path, monkeypatch):
    from cpn.components.basic import lower_operation, CONFIG_SCHEMA_ID
    from cpn.rpnh.petri_contracts import CountGuard
    from bound_lowering_fixtures import compiled_bound_owner
    reg = _registration()
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment, transitions=tuple(replace(t,
            count_guards=(CountGuard('request', 'ge', 2),)) for t in fragment.transitions))
    reg.register_component('guarded', lower, identity={'implementation_id': 'test.guarded', 'revision': 'v1'},
        contracts={'config_schema': CONFIG_SCHEMA_ID})
    document = _simple_module().to_dict(); document['components'][0]['key'] = 'guarded'
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, reg, ModuleDeclaration.from_dict(document))
    before = owner._core.event_store.max_ordinal()
    assert owner.admit('step.run', logical_tau=0, command_id='guarded:admit') is None
    assert owner._core.event_store.max_ordinal() == before


def test_ref_shaped_business_payload_roundtrips_real_registry(tmp_path, monkeypatch):
    from bound_lowering_fixtures import compiled_bound_owner
    from cpn.rpnh.run import OwnerInput
    from cpn.rpnh.registry.schema_catalog import canonical_json
    schema_id = 'application/h7_business_object/v1'
    reg = _registration()
    reg.register_schema(schema_id, {'$id': schema_id, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    payload = {'entity_type': 'resource_version/v1', 'logical_id': 'business-id', 'version_id': 'business-version',
        'schema': {'$ref': 'https://example.invalid/not-a-schema-lookup'}, 'payload': ['two', 'one'], 'ready': True}
    task = OwnerInput(schema_id, canonical_json(payload), 'Synthetic structured payload')
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, reg,
        _simple_module(input_schema=schema_id, output_schema=schema_id), task=task)
    admission = owner.admit('step.run', logical_tau=0, command_id='object:admit')
    execution = owner.start(admission, command_id='object:start')
    result = owner.products(execution, outcome_id='complete', products={'step.result': (canonical_json(payload),)}, command_id='object:products')
    owner.succeed(result, command_id='object:success')
    ref = result.outputs[0].resource_ref
    assert json.loads(owner._core.object_store.read_verified(owner._core.get_version(ref.resource_version_id))) == payload


def test_compiler_origin_cold_readonly_hydration(tmp_path, monkeypatch):
    from bound_lowering_fixtures import compiled_bound_owner
    from test_static_lease_reads import current, finish
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    from cpn.rpnh.registry.parent_bound import assert_bound_integrity
    from cpn.rpnh.marking import TeamNetMarking
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    admission = owner.admit('step.run', logical_tau=0, command_id='cold:admit')
    cold = _RegistryCore(owner._core.run_dir, create=False, read_only=True, catalog=owner._core.catalog)
    executable, structure, marking = hydrate_module_runtime(cold)
    assert len(install_active_module_claims(cold, TeamNetMarking.from_authority(structure, marking), executable.net_ref)) == 1
    assert_bound_integrity(cold)
    finish(owner, admission, 'cold')
    _, _, after = hydrate_module_runtime(cold)
    assert next(t.state for t in after.tokens if t.state.place == ORIGIN_SYMBOL) == next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL)


@pytest.mark.parametrize('constraint', [None, {}, {'resource_input': 'other'}, {'resource_input': ORIGIN_SYMBOL, 'allow': True}])
def test_reserved_origin_constraint_rejects_wrong_shape(constraint):
    document = with_bound_origin(_simple_module()).to_dict()
    document['designer_constraints']['rpnh_parent_bound_origin_v1'] = constraint
    with pytest.raises(DeclarationError): compile_module(ModuleDeclaration.from_dict(document), _registration())


def test_reserved_origin_schema_cannot_be_supplied_by_business_component():
    from parent_child_fixtures import child_definition
    reg, module, _ = child_definition(legacy_origin=True)
    with pytest.raises(DeclarationError): compile_module(with_bound_origin(module), reg)
