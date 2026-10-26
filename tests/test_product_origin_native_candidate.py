"""Native typed sealing and workspace settlement, without provider dispatch."""
import json

import pytest

import test_registry_read_session as readers
from test_product_origin_includes import grant
from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService
from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
from cpn.rpnh.agent_tasks import AgentStage, agent_task_catalog, agent_task_registration, build_agent_task_module
from cpn.rpnh.collaboration._product_origin_core import _ref
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.schema_catalog import registry_read_schema_data
from cpn.rpnh.firing_content_contract import FiringContentContractError, seal_candidate
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run


@pytest.fixture(scope='module')
def native_candidate(tmp_path_factory):
    schema = 'application/origin_candidate_test/v1'
    payload = canonical_json({'value': 7})
    registration, catalog = agent_task_registration(), agent_task_catalog()
    registration.register_schema(schema, {'$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object',
        'properties': {'value': {'type': 'integer'}}, 'required': ['value'], 'additionalProperties': False})
    schemas, types, paths = registry_read_schema_data()
    for name, document in schemas.items():
        catalog.register_schema(name, document, source_path=paths.get(name))
    for definition in types:
        catalog.register_type(definition)

    def checker(*, snapshot, evidence):
        assert evidence.read_resource(snapshot['sources']['input']['resource_ref']) == payload
        if snapshot['products']['work.result']['payload'] != payload:
            raise FiringContentContractError('VALUE_MISMATCH')

    registration.register_tool('origin-candidate-checker', checker,
        identity={'implementation_id': 'tests.origin_candidate_checker', 'revision': 'v1'},
        contracts={'protocol': 'firing_content_checker/v1'})
    document = build_agent_task_module((AgentStage('work', 'Return the typed input.'),),
                                      max_attempts_per_stage=2).to_dict()
    component = document['components'][0]
    for port in component['ports']:
        port['schema'] = schema
    operation = component['operations'][0]
    operation['tools'] = ['complete_interaction', 'read_file', 'search_text', 'submit_candidate']
    operation['config']['firing_content_contract'] = {'protocol': 'firing_content_contract/v1',
        'binding_key': 'work', 'checker_key': 'origin-candidate-checker', 'success_outcomes': ['complete']}
    document['required_schemas'].append(schema)
    module = ModuleDeclaration.from_dict(document)

    def configure(owner):
        def publish(value, key):
            return _publish_private_system(owner._core, owner.identity.task_ref,
                PublishResource(origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=canonical_json(value),
                    media_type='application/json', content_schema_ref=None, summary=key,
                    lifetime_ref=owner.identity.task_ref, idempotency_key=key))
        contract = {'protocol': 'firing_content_contract/v1',
            'run_ref': _ref_payload(owner.identity.run_ref), 'net_ref': _ref_payload(owner.publication.net_ref),
            'operation_binding_refs': [_ref_payload(ref) for ref in owner.publication.binding_refs.values()],
            'checker': registration.declaration('tool', 'origin-candidate-checker'),
            'sources': {'input': {'resource_ref': _ref_payload(owner.original_input_ref.as_version_ref())}},
            'products': {'work.result': schema}, 'required_reads': [], 'refinement': None}
        contract_ref = publish(contract, 'origin:contract')
        return (publish({'firing_content_contracts': {'work': {
            'resource_id': str(contract_ref.resource_id),
            'resource_version_id': str(contract_ref.resource_version_id)}}}, 'origin:config'),
                owner.publication.declaration_resource_ref)

    target = LLMInputTarget('origin-offline-no-dispatch', 64, 65536)
    value = OwnerInput(schema, payload, 'Typed owner input')
    owner = start_run(module, registration, run_dir=tmp_path_factory.mktemp('native-origin') / 'run',
        task_input=value, entry_inputs={'request': value},
        budgets=ModuleBudgetDeclaration(tuple(document['budget_buckets']),
            ('rpnh/module_declaration/v1',), 2, 0, 2, 0),
        model_condition=target.model_condition, owner_statement='Offline typed origin regression',
        command_id='origin:fresh', catalog=catalog, configuration_sources=configure,
        host_execution_bindings=make_optional_agent_host_bindings(target))
    execution = owner.start(owner.admit('work.run', logical_tau=0, command_id='origin:admit'),
                            command_id='origin:start')
    core = owner._core
    kernel, repository = owner.operation_repository()

    def forbid_tools(*args, **kwargs):
        raise AssertionError('This fixture must not dispatch tools or a provider')

    service = OptionalAgentLoopRegistryService(owner=owner, kernel=kernel, repository=repository,
        provider_attempts=core.provider_attempts, invoke_tool=forbid_tools)
    tools = service.current_agent_tool_catalog_v1(execution)
    loop = service.start_agent_loop_v1(service.prepare_agent_loop_start_v1(
        execution, tools, idempotency_key='origin:loop'))
    service.prepare_agent_system_initialization_v1(execution, loop, tools)
    with pytest.raises(FiringContentContractError, match='VALUE_MISMATCH'):
        seal_candidate(core, execution, products={'work.result': canonical_json({'value': 8})},
                       selected_outcome_id='complete', idempotency_key='origin:rejected')
    outputs = seal_candidate(core, execution, products={'work.result': payload},
                             selected_outcome_id='complete', idempotency_key='origin:accepted')
    owner.succeed(outputs, command_id='origin:success')
    source = 'native-candidate'
    owner.schema_gateway.bind_source_identity(source_id=source, command_id='origin:bind')
    principal = execution.operation.canonical.context.principal_ref
    world = (core, owner.schema_gateway, owner.identity, owner.bootstrap_ref, principal, source)
    permission = grant(world, command='origin:grant')
    result_row, = core.event_store.canonical_object_rows(object_type='operation_result/v1')
    result = json.loads(result_row['metadata_json'])
    output_ref, = result['output_resource_refs']
    product_row = core.event_store.canonical_object_row(_version_from_payload(output_ref).version_id)
    completion_row, = core.event_store.canonical_object_rows(object_type='firing_completion/v2')
    completion = json.loads(completion_row['metadata_json'])
    revision_ref = _version_from_payload(completion['workspace_revision_ref'])
    assert revision_ref.entity_id.kind == 'workspace_lineage'
    assert revision_ref.version_id.kind == 'workspace_revision'
    assert core.get_version(revision_ref.version_id).metadata['disposition'] == 'committed'
    # Sealed typed products precede Success; neither transaction is synthesized.
    assert product_row['transaction_id'] != completion_row['transaction_id']
    return world, permission, result


@pytest.mark.parametrize('kind', ['resource', 'result'])
def test_native_typed_candidate_workspace_origin(native_candidate, kind):
    world, permission, result = native_candidate
    source = world[-1]
    output = _version_from_payload(result['output_resource_refs'][0])
    root = (SourceQualifiedResourceRef(source,
        ResourceVersionRef(output.entity_id, output.version_id))
        if kind == 'resource' else SourceQualifiedVersionRef(source,
            _version_from_payload(result['operation_result_ref'])))
    before = readers.counts(world)
    with readers.session(world, permission) as session:
        cut = session.capture_cut(source)
        pages, cursor = [], None
        while True:
            page = session.query_product_origin_v1(root, cut, page_size=1, cursor=cursor)
            pages.append(page)
            cursor = page['continuation']
            if cursor is None:
                break
        assert [row['role'] for page in pages for row in page['rows']] == ['start_input', 'claim']
        assert pages[-1]['coverage']['state'] == 'complete'
        assert all(page['root_proof']['root_role'] == (
            'registered_output' if kind == 'resource' else 'operation_result') for page in pages)
        assert pages[0]['root_proof']['producer_invocation_ref']['ref'] == result['invocation_ref']
        session.final_recheck((source,))
    assert readers.counts(world) == before


@pytest.mark.parametrize('logical,version', [
    ('workspace_revision', 'workspace_revision'),
    ('workspace_lineage', 'workspace_lineage'),
    ('workspace_revision', 'workspace_lineage'),
])
def test_workspace_revision_ref_rejects_wrong_id_kinds(logical, version):
    with pytest.raises(TypeError):
        _ref({'entity_type': 'workspace_revision/v1', 'logical_id': str(new_id(logical)),
              'version_id': str(new_id(version))}, 'workspace_revision/v1')
