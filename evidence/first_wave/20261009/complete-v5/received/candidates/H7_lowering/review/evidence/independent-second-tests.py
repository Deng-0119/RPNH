"""Independent finite D0 probes; native evidence injection never establishes OS facts."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import pytest

from cpn.rpnh.bound_child_lowering import (
    with_bound_origin, compose_bound_origin, ORIGIN_CONSTRAINT, ORIGIN_SYMBOL, ORIGIN_SCHEMA,
)
from cpn.rpnh.bound_child_declarations import FrozenBoundChildDeclarations, freeze_bound_module_declarations, freeze_bound_agent_declarations
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import DeclarationError, PlaceDeclaration, LeaseIdentityDeclaration, ResourceLeasePoolBinding, ArcDeclaration, InitialTokenDeclaration
from cpn.rpnh.registration import RegistrationError
from cpn.rpnh.registry import parent_child as h7, parent_bound
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.identities import new_id
from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from test_static_lease_reads import _registration, _simple_module, current, finish, direct_claim, TEXT
from bound_lowering_fixtures import compiled_bound_owner


def _origin(owner):
    return next(t.state for t in current(owner)[2].tokens if t.state.place == ORIGIN_SYMBOL)


def _custom(lower, name='review-lower'):
    reg = _registration()
    reg.register_component(name, lower,
        identity={'implementation_id': name, 'revision': 'v1'},
        contracts={'config_schema': CONFIG_SCHEMA_ID})
    doc = _simple_module().to_dict()
    doc['components'][0]['key'] = name
    return ModuleDeclaration.from_dict(doc), reg


@pytest.mark.parametrize('sample', ['simple', 'json-effect', 'single-agent'])
def test_exact_business_projection_is_unchanged(sample):
    if sample == 'json-effect':
        from test_compiler_json_contract import _module, _registration as jsonreg
        module, reg = _module([True, 1, False, 0, {'$ref': 'inert'}], 'effect'), jsonreg()
    elif sample == 'single-agent':
        from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module, agent_task_registration
        module, reg = build_agent_task_module((AgentStage('main', 'Review only'),), max_attempts_per_stage=3), agent_task_registration()
    else:
        module, reg = _simple_module(), _registration()
    plain = compile_module(module, reg)
    bound = compile_module(with_bound_origin(module), reg)
    projection = replace(bound.symbolic,
        places=tuple(p for p in bound.symbolic.places if p.name != ORIGIN_SYMBOL),
        arcs=tuple(a for a in bound.symbolic.arcs if a.place != ORIGIN_SYMBOL),
        lease_identities=tuple(l for l in bound.symbolic.lease_identities if l.name != ORIGIN_SYMBOL),
        lease_pools=tuple(p for p in bound.symbolic.lease_pools if p.name != ORIGIN_SYMBOL),
        required_schemas=plain.symbolic.required_schemas,
        designer_constraints=plain.symbolic.designer_constraints)
    assert projection == plain.symbolic
    for field in ('fragments', 'ports', 'operations', 'operation_handles', 'port_handles'):
        assert getattr(bound, field) == getattr(plain, field)
    assert load_compiled_net(bound.to_json()) == bound
    assert len([a for a in bound.symbolic.arcs if a.place == ORIGIN_SYMBOL]) == len(bound.symbolic.transitions)


@pytest.mark.parametrize('key,value', [
    ('rpnh_parent_bound_origin_v2', {'resource_input': ORIGIN_SYMBOL}),
    (ORIGIN_CONSTRAINT, {'resource_input': 'foreign'}),
    (ORIGIN_CONSTRAINT, {'resource_input': ORIGIN_SYMBOL, 'ready': True}),
    (ORIGIN_CONSTRAINT, True),
])
def test_reserved_contract_variants_reject(key, value):
    doc = _simple_module().to_dict()
    doc['designer_constraints'][key] = value
    with pytest.raises(DeclarationError):
        compile_module(ModuleDeclaration.from_dict(doc), _registration())


def test_duplicate_application_and_protected_schema_collision_reject():
    module, reg = _simple_module(), _registration()
    bound = compile_module(with_bound_origin(module), reg)
    assert with_bound_origin(bound.source) == bound.source
    with pytest.raises(DeclarationError, match='occupied'):
        compose_bound_origin(bound.source, bound.symbolic, bound.place_aliases)
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment, places=(*fragment.places, PlaceDeclaration('pretend', ORIGIN_SCHEMA)))
    module, reg = _custom(lower)
    with pytest.raises(DeclarationError):
        compile_module(with_bound_origin(module), reg)


def test_public_business_name_same_spelling_is_not_origin_identity(tmp_path, monkeypatch):
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment,
            places=(*fragment.places, PlaceDeclaration(ORIGIN_SYMBOL, TEXT, token_kind='resource_lease', capacity=1, reusable=True)),
            arcs=(*fragment.arcs, ArcDeclaration(ORIGIN_SYMBOL, 'run', 'input', mode='read')),
            lease_identities=(LeaseIdentityDeclaration(ORIGIN_SYMBOL),),
            lease_pools=(ResourceLeasePoolBinding(ORIGIN_SYMBOL, ORIGIN_SYMBOL, (ORIGIN_SYMBOL,)),))
    module, reg = _custom(lower)
    from cpn.rpnh.run import OwnerInput
    business = OwnerInput(TEXT, canonical_json('ordinary business lease'), 'same-spelling business')
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, reg, module,
        resources={'step.' + ORIGIN_SYMBOL: business})
    tokens = {t.state.place: t.state for t in current(owner)[2].tokens}
    assert tokens[ORIGIN_SYMBOL].resource_ref != tokens['step.' + ORIGIN_SYMBOL].resource_ref
    admitted = owner.admit('step.run', logical_tau=0, command_id='identity:admit')
    assert tokens[ORIGIN_SYMBOL].token_ref in admitted.preflight.claimed_token_refs
    assert tokens['step.' + ORIGIN_SYMBOL].token_ref in admitted.preflight.claimed_token_refs
    finish(owner, admitted, 'identity')
    assert _origin(owner) == tokens[ORIGIN_SYMBOL]


@pytest.mark.parametrize('damage', ['origin_output', 'origin_reset', 'origin_pool', 'initial_token', 'source_unbound', 'forged_fragment'])
def test_wire_rejects_protected_shape_mutations(damage):
    doc = compile_module(with_bound_origin(_simple_module()), _registration()).to_dict()
    sym = doc['symbolic']
    if damage == 'origin_output':
        arc = deepcopy(next(a for a in sym['arcs'] if a['direction'] == 'output'))
        arc['place'] = ORIGIN_SYMBOL
        sym['arcs'].append(arc)
    elif damage == 'origin_reset':
        sym['reset_arcs'].append({'place': ORIGIN_SYMBOL, 'transition': 'step.run', 'effect': 'reset', 'outcome': 'complete'})
    elif damage == 'origin_pool':
        next(p for p in sym['lease_pools'] if p['name'] == ORIGIN_SYMBOL)['initial_resources'] = ['step.fake']
    elif damage == 'initial_token':
        next(p for p in sym['places'] if p['name'] == ORIGIN_SYMBOL)['initial_tokens'] = [{'count': 1, 'value': 'forged', 'schema': ORIGIN_SCHEMA}]
    elif damage == 'source_unbound':
        del doc['source']['designer_constraints'][ORIGIN_CONSTRAINT]
    else:
        doc['fragments']['step']['places'].append({'name': 'localFake', 'schema': ORIGIN_SCHEMA, 'token_kind': 'resource_lease', 'capacity': 1, 'reusable': True, 'initial_tokens': []})
    with pytest.raises(DeclarationError):
        load_compiled_net(doc)


@pytest.mark.parametrize('damage', ['missing', 'consume', 'duplicate', 'same_version_other_logical'])
def test_exact_origin_claim_rechecked_in_registry(tmp_path, monkeypatch, damage):
    from cpn.rpnh.registry.invocations import InvocationLifecycle, InvocationAdmissionError
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    claim, origin = direct_claim(owner), _origin(owner).token_ref
    if damage == 'missing':
        claim = replace(claim, claimed_input_refs=tuple(r for r in claim.claimed_input_refs if r != origin))
    elif damage == 'consume':
        claim = replace(claim, consumed_input_refs=(*claim.consumed_input_refs, origin))
    elif damage == 'duplicate':
        claim = replace(claim, claimed_input_refs=(*claim.claimed_input_refs, origin))
    else:
        claim = replace(claim, claimed_input_refs=tuple(replace(r, entity_id=new_id('petri_token')) if r == origin else r for r in claim.claimed_input_refs))
    before = owner._core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict, InvocationAdmissionError, ValueError)):
        InvocationLifecycle(owner._core).admit_firing(claim, idempotency_key='review:bad-admit')
    assert owner._core.event_store.max_ordinal() == before
    assert not owner._core.event_store.object_rows_by_type('transition_firing/v1')


def test_same_transition_parallel_claims_and_cold_read_exact_origin(tmp_path, monkeypatch):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    from cpn.rpnh.marking import TeamNetMarking
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment, places=tuple(replace(p, initial_tokens=(InitialTokenDeclaration(count=1, value='second', schema=TEXT),)) if p.name == 'request' else p for p in fragment.places))
    module, reg = _custom(lower)
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, reg, module)
    initial = _origin(owner)
    first = owner.admit('step.run', logical_tau=0, command_id='parallel:first')
    second = owner.admit('step.run', logical_tau=0, command_id='parallel:second')
    assert first is not None and second is not None
    assert set(first.preflight.claimed_token_refs) & set(second.preflight.claimed_token_refs) == {initial.token_ref}
    finish(owner, first, 'parallel:first')
    core = owner._core
    before = (core.event_store.writer_epoch, core.event_store.max_ordinal())
    cold = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    parent_bound.assert_bound_integrity(cold)
    executable, structure, marking = hydrate_module_runtime(cold)
    assert next(t.state for t in marking.tokens if t.state.place == ORIGIN_SYMBOL) == initial
    assert len(install_active_module_claims(cold, TeamNetMarking.from_authority(structure, marking), executable.net_ref)) == 1
    assert (core.event_store.writer_epoch, core.event_store.max_ordinal()) == before
    finish(owner, second, 'parallel:second')
    assert _origin(owner) == initial


@pytest.mark.parametrize('damage', ['consume', 'cloned_ref', 'wrong_resource', 'wrong_lease'])
def test_origin_raw_token_write_is_fail_closed(tmp_path, monkeypatch, damage):
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    core = owner._core
    row = next(r for r in core.event_store.object_rows_by_type('petri_token/v1') if json.loads(r['metadata_json'])['place'] == ORIGIN_SYMBOL)
    value = json.loads(row['metadata_json'])
    old = _version_from_payload(value['petri_token_ref'])
    ref = replace(old, entity_id=new_id('petri_token'), version_id=new_id('petri_token_version'))
    value['petri_token_ref'] = _ref_payload(ref)
    value['token_id'] = 999
    if damage == 'consume':
        value['epoch'] = 1
    elif damage == 'wrong_resource':
        value['resource_ref']['resource_version_id'] = str(new_id('resource_version'))
    elif damage == 'wrong_lease':
        value['lease_identity_ref']['version_id'] = str(new_id('resource_version'))
    before = core.event_store.max_ordinal()
    with pytest.raises((RegistryConflict, ValueError)):
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(value), metadata=value, media_type='application/json',
            schema_ref='registry_v1/' + ref.entity_type, idempotency_key='review:raw-token')
    assert core.event_store.max_ordinal() == before


def test_owner_replacement_and_direct_adoption_leave_origin_unchanged(tmp_path, monkeypatch):
    from cpn.rpnh.net_operations import apply_replacement, prepare_replacement
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    core = owner._core
    initial = _origin(owner)
    net = owner.publication.net_ref
    initial_net, _, initial_marking = current(owner)
    with pytest.raises((RegistryConflict, ValueError)):
        plan = prepare_replacement(owner, with_bound_origin(_simple_module('Successor')))
        apply_replacement(owner, plan, command_id='review:successor')
    # The ordinary owner path can persist candidate/command audit records before
    # its adoption transaction rejects. Only the adopted net/checkpoint/token
    # authority must remain unchanged; zero generic events is not its contract.
    before_direct = core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        core.task_control.adopt_net(net_instance_ref=net, supersedes_net_ref=net, idempotency_key='review:direct-adopt')
    assert core.event_store.max_ordinal() == before_direct
    after_net, _, after_marking = current(owner)
    assert after_net.net_ref == initial_net.net_ref
    assert after_marking.checkpoint_ref == initial_marking.checkpoint_ref
    assert len(core.event_store.list_events_by_type(('net_adopted/v1',))) == 1
    assert _origin(owner) == initial


def test_freezing_preserves_business_arrays_and_detaches_all_inventories():
    from test_compiler_json_contract import _module, _registration as jsonreg
    payload = {'entity_type': 'resource_version/v1', 'logical_id': 'business-string', 'version_id': 'business-string', 'values': [True, 1, False, 0], '$ref': 'https://invalid.example/inert-business'}
    module = _module(payload, 'effect')
    frozen = freeze_bound_module_declarations(module, jsonreg())
    documents = {name: json.loads(value) for name, value in frozen.payloads}
    assert documents['application_declaration']['components'][0]['operations'][0]['outcomes'][0]['effects'][0]['config']['nested'][1]['flag'] == payload
    assert documents['registration']['schema'][ORIGIN_SCHEMA]['schema']['$id'] == ORIGIN_SCHEMA
    baseline = frozen.declaration_digest
    inventory = frozen.inventory
    inventory[0]['sha256'] = '0' * 64
    assert frozen.declaration_digest == baseline
    changed = deepcopy(payload)
    changed['values'] = [1, True, 0, False]
    assert freeze_bound_module_declarations(_module(changed, 'effect'), jsonreg()).declaration_digest != baseline


@pytest.mark.parametrize('kind', ['module', 'agent_task'])
def test_handcrafted_declarations_cannot_be_intent_authority(kind):
    fake = FrozenBoundChildDeclarations(kind, (('host', b'{}'), ('implementation', b'{}')))
    with pytest.raises(h7.ParentChildUnsupported):
        fake.require_execution_materials()
    with pytest.raises(TypeError):
        h7.register_child_intent(None, None, fake)


def test_registration_change_during_lowering_is_reported():
    reg = _registration()
    def lower(config, context):
        reg.register_schema('application/review_added/v1', {'$id': 'application/review_added/v1', '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
        return lower_operation(config, context)
    reg.register_component('review_mutating_lowerer', lower,
        identity={'implementation_id': 'review-mutation', 'revision': 'v1'}, contracts={'config_schema': CONFIG_SCHEMA_ID})
    doc = _simple_module().to_dict(); doc['components'][0]['key'] = 'review_mutating_lowerer'
    with pytest.raises(ValueError, match='Registration mutated'):
        freeze_bound_module_declarations(ModuleDeclaration.from_dict(doc), reg)


@pytest.mark.parametrize('slot', ['', 'child/a', '../child', 'a' * 65, 1])
def test_agent_static_slot_is_explicit_canonical_data(tmp_path, slot):
    from test_bound_child_declarations import agent_inputs
    spec, kwargs, _ = agent_inputs(tmp_path)
    kwargs['slot_id'] = slot
    with pytest.raises(ValueError):
        freeze_bound_agent_declarations(spec, **kwargs)


def test_agent_final_target_context_and_digest_have_no_postfreeze_path_rewrite(tmp_path):
    from test_bound_child_declarations import agent_inputs
    spec, kwargs, target = agent_inputs(tmp_path)
    frozen = freeze_bound_agent_declarations(spec, **kwargs)
    docs = {name: json.loads(value) for name, value in frozen.payloads}
    assert docs['target_context']['target'] == target
    assert docs['normalized_worker_document'] == spec.as_worker_document(document_root=Path(target['document_root']))
    before = frozen.declaration_digest
    kwargs['parent']['source_id'] = 'another-parent'
    with pytest.raises(ValueError, match='transport paths'):
        freeze_bound_agent_declarations(spec, **kwargs)
    assert frozen.declaration_digest == before
    assert not spec.run_dir.exists()


def test_protected_schema_freezes_installed_body_and_public_override_rejects():
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    reg = _registration()
    actual = json.loads(SchemaCatalog._mechanical_schema_path(ORIGIN_SCHEMA).read_bytes())
    with pytest.raises(RegistrationError, match='protected'):
        reg.register_schema(ORIGIN_SCHEMA, {'$id': ORIGIN_SCHEMA, '$schema': 'http://json-schema.org/draft-07/schema#'})
    frozen = freeze_bound_module_declarations(_simple_module(), reg)
    registration = json.loads(dict(frozen.payloads)['registration'])
    assert registration['schema'][ORIGIN_SCHEMA]['schema'] == actual


def test_recursive_launch_and_control_proof_are_explicitly_rejected():
    reg = _registration()
    reg.register_executor(h7.NATIVE_LAUNCH_EXECUTOR, lambda **kwargs: None,
        identity={'implementation_id': 'review-recursive-never-call', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'config_schema': CONFIG_SCHEMA_ID})
    doc = _simple_module().to_dict()
    doc['components'][0]['operations'][0]['executor'] = h7.NATIVE_LAUNCH_EXECUTOR
    with pytest.raises(DeclarationError, match='recursive native launch'):
        compile_module(with_bound_origin(ModuleDeclaration.from_dict(doc)), reg)
    doc = _simple_module().to_dict()
    doc['designer_constraints']['rpnh_control_ir_v1'] = {}
    with pytest.raises(DeclarationError, match='separate proof contract'):
        with_bound_origin(ModuleDeclaration.from_dict(doc))


def test_bound_external_config_schema_ref_fails_without_retrieval():
    from test_compiler_json_contract import _module, _registration as jsonreg
    from cpn.rpnh.control_types import ControlIRError
    reg = jsonreg()
    schema = 'application/review_remote_config/v1'
    reg.register_schema(schema, {'$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#', '$ref': 'https://invalid.example/never-fetch'})
    key = 'test/review-remote-config/v1'
    reg.register_executor(key, lambda **kwargs: None,
        identity={'implementation_id': 'review-remote-config', 'revision': 'v1'}, contracts={'config_schema': schema})
    doc = _module({'business': [1, True]}).to_dict()
    doc['required_schemas'].append(schema)
    doc['components'][0]['operations'][0]['executor'] = key
    with pytest.raises((DeclarationError, ControlIRError), match='schema_reference_unresolvable|Unresolvable'):
        compile_module(with_bound_origin(ModuleDeclaration.from_dict(doc)), reg)


@pytest.mark.parametrize('payloads', [[('a', b'{}')], (('a', '{}'),), (('b', b'{}'), ('a', b'{}')), (('a', b'{}'), ('a', b'{}'))])
def test_declaration_dto_requires_immutable_unique_sorted_rows(payloads):
    with pytest.raises(TypeError):
        FrozenBoundChildDeclarations('module', payloads)


def test_multistage_agent_declaration_slice_remains_unsupported(tmp_path):
    from test_bound_child_declarations import agent_inputs
    from cpn.rpnh.agent_tasks import AgentStage
    spec, kwargs, _ = agent_inputs(tmp_path)
    with pytest.raises(h7.ParentChildUnsupported):
        freeze_bound_agent_declarations(replace(spec, stages=(*spec.stages, AgentStage('later', 'Unsupported second stage'))), **kwargs)
