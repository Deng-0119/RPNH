"""Finite declaration subset; no execution profile/provider file is read."""
from dataclasses import replace
import json
from pathlib import Path
import pytest
from cpn.rpnh.bound_child_declarations import freeze_bound_module_declarations, freeze_bound_agent_declarations
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.bound_child_lowering import ORIGIN_SYMBOL
from test_static_lease_reads import _registration, _simple_module


def test_module_materials_are_actual_compilation_and_detached():
    module = _simple_module(); reg = _registration()
    frozen = freeze_bound_module_declarations(module, reg)
    baseline = frozen.declaration_digest
    data = json.loads(dict(frozen.payloads)['lowered_net'])
    assert data['source'] == json.loads(dict(frozen.payloads)['module'])
    assert data['registrations'] == json.loads(dict(frozen.payloads)['registration'])
    assert ORIGIN_SYMBOL in data['place_aliases']
    module.budgets['business'] = 1
    assert frozen.declaration_digest == baseline
    changed = freeze_bound_module_declarations(module, reg)
    with pytest.raises(ValueError): frozen.assert_unchanged(changed)
    with pytest.raises(h7.ParentChildUnsupported): frozen.require_execution_materials()
    with pytest.raises(TypeError): h7.register_child_intent(None, None, frozen)


def test_business_ref_shaped_json_and_schema_are_preserved_not_resolved():
    document = _simple_module().to_dict()
    value = {'entity_type': 'resource_version/v1', 'logical_id': 'not-a-ref', 'version_id': 'also-not-a-ref',
             'payload': {'schema': {'$ref': 'https://example.invalid/do-not-fetch'}, 'ready': True}}
    document['designer_constraints']['business'] = value
    frozen = freeze_bound_module_declarations(ModuleDeclaration.from_dict(document), _registration())
    assert json.loads(dict(frozen.payloads)['module'])['designer_constraints']['business'] == value


def agent_inputs(tmp_path):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
    def ref(kind):
        return {'entity_type': kind + '/v1', 'logical_id': kind + ':00000000000000000000000000000001',
                'version_id': kind + '_version:00000000000000000000000000000002'}
    parent = {'source_id': 'synthetic-parent', 'run_ref': ref('run'), 'task_ref': ref('task')}
    request_ref = VersionRef('resource_version/v1', TypedId('resource', '00000000000000000000000000000003'),
        TypedId('resource_version', '00000000000000000000000000000004'))
    kwargs = dict(parent=parent, request_ref=request_ref, root_binding='synthetic-root',
        control_root=tmp_path / 'control', target_root=tmp_path / 'targets', slot_id='child')
    target = h7._target(parent, 'child', 'synthetic-root', str(kwargs['control_root']))
    run = kwargs['target_root'] / target['relative_path']
    spec = AgentTaskSpec(run_dir=run, prompt='Synthetic declaration only', stages=(AgentStage('main', 'Return a result'),),
        execution_config_path=tmp_path / 'private-unread.json', owner_socket_path=run / 'owner.sock')
    return spec, kwargs, target


def test_agent_declaration_normalization_uses_final_root_and_never_reads_profile(tmp_path, monkeypatch):
    spec, kwargs, target = agent_inputs(tmp_path)
    original_read = Path.read_bytes
    def read(path):
        if path == spec.execution_config_path: raise AssertionError('private profile read')
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    frozen = freeze_bound_agent_declarations(spec, **kwargs)
    document = json.loads(dict(frozen.payloads)['normalized_worker_document'])
    assert document == spec.as_worker_document(document_root=Path(target['document_root']))
    assert document['run_relative_path'].startswith('../')
    assert not spec.run_dir.exists() and not kwargs['control_root'].exists()
    other = tmp_path / 'other'; other.mkdir(); monkeypatch.chdir(other)
    frozen.assert_unchanged(freeze_bound_agent_declarations(spec, **kwargs))
    with pytest.raises(h7.ParentChildUnsupported): frozen.require_execution_materials()
    with pytest.raises(ValueError): frozen.assert_unchanged(freeze_bound_agent_declarations(replace(spec, prompt='Changed'), **kwargs))


@pytest.mark.parametrize('field', ['run_dir', 'owner_socket_path'])
def test_agent_postfreeze_transport_paths_are_rejected(tmp_path, field):
    spec, kwargs, target = agent_inputs(tmp_path)
    frozen = freeze_bound_agent_declarations(spec, **kwargs)
    changed = replace(spec, **{field: tmp_path / 'wrong'})
    with pytest.raises(ValueError): freeze_bound_agent_declarations(changed, **kwargs)
    assert frozen.declaration_digest


def test_agent_extra_profile_and_plugin_are_explicitly_unsupported(tmp_path):
    spec, kwargs, _ = agent_inputs(tmp_path)
    cases = [replace(spec, execution_profiles=(('other', tmp_path / 'not-read'),)),
             replace(spec, plugin_configuration={}, plugin_catalog_digest='0' * 64)]
    for value in cases:
        with pytest.raises(h7.ParentChildUnsupported): freeze_bound_agent_declarations(value, **kwargs)


def test_reference_profile_mutation_cannot_be_mistaken_for_execution_inventory(tmp_path):
    spec, kwargs, _ = agent_inputs(tmp_path)
    before = freeze_bound_agent_declarations(spec, **kwargs)
    spec.execution_config_path.write_text('synthetic unread configuration')
    after = freeze_bound_agent_declarations(spec, **kwargs)
    assert before == after  # Deliberate declaration-only boundary, not a completeness claim.
    with pytest.raises(h7.ParentChildUnsupported): after.require_execution_materials()


def test_registered_schema_changes_frozen_digest():
    from test_static_lease_reads import TEXT
    module = _simple_module(); reg = _registration()
    frozen = freeze_bound_module_declarations(module, reg)
    # Fault injection into the existing Registration; production keys are immutable.
    reg._declarations['schema'][TEXT]['schema']['description'] = 'Changed actual source body'
    with pytest.raises(ValueError): frozen.assert_unchanged(freeze_bound_module_declarations(module, reg))


def test_unknown_host_profile_object_is_not_a_material_provider():
    from cpn.rpnh.collaboration.environment_host import HostProfile
    called = []
    profile = HostProfile('unproven', lambda: called.append('factory'))
    with pytest.raises(TypeError): freeze_bound_module_declarations(_simple_module(), profile)
    assert not called
