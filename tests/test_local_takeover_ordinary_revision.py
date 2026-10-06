"""Actual ordinary structural revision Success, entirely in-process Registry.

The only HOST callback returns one fixed typed Module declaration. No provider,
worker, workspace, process or external effect is used.
"""
from copy import deepcopy
import json

from test_local_takeover_normal_boundaries import receipt


def test_ordinary_revision_success_has_legacy_unmarked_delta(tmp_path):
    from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.compiler import compile_module
    from cpn.rpnh.run import start_run, OwnerInput
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.module_revision import DeclaredModuleRevision
    from cpn.rpnh.registry.module_operations import publish_module_operations
    from cpn.rpnh.registry.module_nets import publish_module_net
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.publication import _version_from_payload
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data
    from normal_child_root_matrix_fixture import never_dispatch, operation_component, start_parent

    schema = 'rpnh/module_declaration/v1'
    revision_key = 'test/b4-pure-module-revision/v1'
    reg = Registration()
    register_basic_components(reg)
    reg.register_executor('test/normal-child-matrix-never-dispatch/v1', never_dispatch,
        identity={'implementation_id': 'b4-never-dispatch', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'input_ports': None, 'output_ports': None, 'config_schema': CONFIG_SCHEMA_ID})
    reg.register_tool('test/normal-child-matrix-never-terminal/v1', never_dispatch,
        identity={'implementation_id': 'b4-terminal-declaration', 'revision': 'v1'},
        contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    calls = []
    def pure_revision(context):
        assert set(context.resources) == {'candidate'}
        actual = json.loads(context.resources['candidate'][0].payload)
        assert actual == candidate.to_dict()
        calls.append(context.firing_ref)
        return DeclaredModuleRevision('candidate', candidate)
    reg.register_tool(revision_key, pure_revision,
        identity={'implementation_id': 'b4-fixed-declaration-only', 'revision': 'v1'},
        contracts={'effect_kind': 'structural_revision', 'config_schema': CONFIG_SCHEMA_ID})
    components = [operation_component('revise'), operation_component('finish')]
    for component in components:
        for port in component['ports']:
            port['schema'] = schema
    components[0]['operations'][0]['outcomes'][0]['effects'] = [
        {'key': revision_key, 'bindings': {'candidate': 'result'}, 'config': {}, 'references': {}}]
    components[0]['operations'][0]['tools'] = [revision_key]
    document = {'schema_version': 'rpnh/module_declaration/v1', 'name': 'B4OrdinaryRevision',
        'components': components,
        'links': [{'source': {'component': 'revise', 'port': 'result'}, 'target': {'component': 'finish', 'port': 'request'}}],
        'entry': {'request': {'component': 'revise', 'port': 'request'}},
        'exit': {'result': {'component': 'finish', 'port': 'result'}},
        'terminal': {'key': 'test/normal-child-matrix-never-terminal/v1',
            'source': {'component': 'finish', 'port': 'result'}, 'operation': 'run',
            'outcome': 'complete', 'config': {'run_outcome': 'complete'}},
        'required_schemas': [CONFIG_SCHEMA_ID, schema], 'budgets': {},
        'budget_buckets': [{'bucket_id': 'work', 'budget_scope': 'module', 'finalization_scope': None, 'max_attempts': 3}]}
    module = ModuleDeclaration.from_dict(document)
    updated = deepcopy(document)
    updated['name'] = 'B4RevisedModule'
    candidate = ModuleDeclaration.from_dict(updated)
    schemas, types, paths = normal_child_root_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    value = OwnerInput(schema, canonical_json(module.to_dict()), 'finite revision input')
    owner = start_run(module, reg, run_dir=tmp_path / 'ordinary', task_input=value,
        entry_inputs={'request': value}, budgets=ModuleBudgetDeclaration(tuple(document['budget_buckets']),
            ('rpnh/module_declaration/v1',), 3, 0, 3, 0), model_condition='offline-no-model',
        owner_statement='finite ordinary revision', command_id='b4:revision:bootstrap', catalog=catalog,
        host_execution_bindings=None, configuration_sources=None)
    execution = start_parent(owner, 'revise', 'b4:revision')
    outputs = owner.products(execution, outcome_id='complete',
        products={'revise.result': (canonical_json(candidate.to_dict()),)}, command_id='b4:revision:products')
    executable, _, _ = hydrate_module_runtime(owner._core)
    binding = owner._core.get_version(executable.transitions[0].operation_binding_ref.version_id).metadata
    compiled = compile_module(candidate, reg)
    operations = publish_module_operations(owner._core, compiled, reg, owner.schema_gateway.schema_refs,
        idempotency_key='b4:revision:candidate-ops')
    publication = publish_module_net(owner._core, compiled, reg, identity=owner.identity,
        bootstrap_ref=owner.bootstrap_ref, principal_ref=owner.principal_ref, task_round_ref=owner.task_round_ref,
        authority_decision_ref=_version_from_payload(binding['authority_decision_ref']), entry_inputs={},
        schema_refs=owner.schema_gateway.schema_refs, operation_refs=operations,
        host_execution_bindings={}, idempotency_key='b4:revision:candidate-net')
    successor = owner.succeed(outputs, command_id='b4:revision:success')
    current, _, marking = hydrate_module_runtime(owner._core)
    assert current.net_ref == publication.net_ref and marking == successor
    assert len(calls) == 1
    record = owner._core.event_store.ordered_firing_record(execution.operation.firing.transition_firing_ref.version_id)
    delta = record['marking_delta']
    assert 'ordinary_token_ref_scheme' not in delta
    assert delta['declared_effects']['effects'][0]['effect_kind'] == 'structural_revision'
    assert not owner._core.event_store.object_rows_by_type('execution_child_seal/v1')
    assert not owner._core.event_store.object_rows_by_type('collaboration_root_terminal/v2')
    receipt('ordinary-revision', delta=delta, completion=record['firing_completion'],
        checkpoint=record['successor_checkpoint'], old_net=executable.net_ref, new_net=current.net_ref,
        pure_declaration_callback_count=len(calls), run_dir=str(owner._core.run_dir))
