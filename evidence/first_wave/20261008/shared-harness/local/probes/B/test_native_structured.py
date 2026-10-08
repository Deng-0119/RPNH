"""Independent trusted HOST structured-schema proof; real owner/Harness/ABI."""
import hashlib
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft7Validator
from concurrent.futures import ThreadPoolExecutor
from cpn.rpnh.agent_tasks import (AgentStage, build_agent_task_module,
    agent_task_registration, agent_task_catalog, _execution_route, _terminal_output)
from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.orchestrator.runner import Orchestrator
from cpn.components.execution_services import ExecutionServices
from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.components.agent_loop.request_envelope_materialization import materialize_agent_request_envelope
from cpn.components.agent_loop.tool_catalog import parse_agent_tool_catalog
from test_main_session_registry import _response, _write_execution_profile

SOURCE = Path('<WORKSPACE>/.h26/s')
BASE = Path('<WORKSPACE>/.h26/bn')
PUBLIC = Path(__file__).resolve().parent
OUTPUT_SCHEMA = 'application/local_b_structured/v1'
SCHEMA = {'$id': OUTPUT_SCHEMA, '$schema': 'http://json-schema.org/draft-07/schema#',
          'type': 'object', 'additionalProperties': False,
          'properties': {'message': {'type': 'string'},
                         'values': {'type': 'array', 'items': {'type': 'integer'}},
                         'verified': {'type': 'boolean'}},
          'required': ['message', 'values', 'verified']}
VALUE = {'message': '结构化原生结果 🌱\n第二行', 'values': [2, 7], 'verified': True}
CONTENT = json.dumps(VALUE, ensure_ascii=False, indent=2)


def sha(data):
    return hashlib.sha256(data).hexdigest()

def dump(path, data):
    assert path.resolve().is_relative_to(BASE) or path.resolve().is_relative_to(PUBLIC)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

def exact(row):
    return dict(entity_type=row['object_type'], logical_id=row['logical_id'], version_id=row['version_id'])

def normalize(ref):
    if 'resource_id' in ref:
        return dict(entity_type='resource_version/v1', logical_id=ref['resource_id'], version_id=ref['resource_version_id'])
    return ref

class ScriptedStructuredPort:
    def __init__(self, directory):
        self.directory = directory
        self.requests = []

    def request_once(self, attempt):
        self.requests.append(attempt.canonical_request_bytes)
        (self.directory / f'request-{len(self.requests)}.json').write_bytes(attempt.canonical_request_bytes)
        assert len(self.requests) == 1, 'unexpected additional logical call'
        return _response([
            {'id': 'b-structured-write', 'name': 'write_file', 'arguments': json.dumps({
                'path': 'outputs/structured.json', 'description': 'Synthetic declared-object JSON acceptance.',
                'content': CONTENT, 'output_port_id': 'main.result', 'outcome_id': 'complete'})},
            {'id': 'b-structured-complete', 'name': 'complete_interaction', 'arguments': '{}'},
        ])

    def close(self):
        pass


def test_fresh_native_structured_recipe_write():
    iteration = os.environ['B_ITERATION']
    raw = BASE / iteration
    raw.mkdir()
    out = PUBLIC / iteration
    out.mkdir()
    port = ScriptedStructuredPort(raw)
    run_dir = raw / 'r'
    # Local authored declaration only. No replacement of a production factory.
    module_data = build_agent_task_module(
        (AgentStage('main', 'Return the exact object under the declared JSON schema.'),),
        max_attempts_per_stage=1).to_dict()
    module_data['name'] = 'LocalBStructuredAcceptance'
    output_port = next(p for p in module_data['components'][0]['ports'] if p['name'] == 'result')
    output_port['schema'] = OUTPUT_SCHEMA
    module_data['required_schemas'].append(OUTPUT_SCHEMA)
    module = ModuleDeclaration.from_dict(module_data)
    registration = agent_task_registration()
    registration.register_schema(OUTPUT_SCHEMA, SCHEMA)
    catalog = agent_task_catalog()
    catalog.register_schema(OUTPUT_SCHEMA, SCHEMA)
    selection = load_llm_execution_selection(_write_execution_profile(raw))
    bindings = make_optional_agent_host_bindings(
        selection.input_target, workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract={'interaction_protocol_ref': 'llm_request_envelope/v1',
                            'response_adapter_ref': 'llm_response_envelope/v1'})
    request = OwnerInput(TEXT_SCHEMA, canonical_json('Write the synthetic declared JSON object.'),
                         'Local B structured task input')
    dump(out / 'authored-module.json', module_data)
    dump(out / 'declared-output-schema.json', SCHEMA)
    owner = start_run(module, registration, run_dir=run_dir, task_input=request,
                      entry_inputs={'request': request},
                      budgets=ModuleBudgetDeclaration(tuple(module_data['budget_buckets']),
                          ('rpnh/module_declaration/v1',), 1, 0, 1, 0),
                      model_condition=selection.input_target.model_condition,
                      owner_statement='Authorized offline local B structured native acceptance',
                      command_id='local-b-structured:fresh', catalog=catalog,
                      host_execution_bindings=bindings)
    event_loop = OwnerEventLoop(owner, run_dir / 'owner.sock')
    try:
        services = ExecutionServices(owner=owner, event_loop=event_loop, llm_input_port=port)
        with ThreadPoolExecutor(max_workers=1) as workers:
            native = Orchestrator(owner=owner, event_loop=event_loop,
                prepare_dispatcher=services.prepare_dispatcher, submit_operation=workers.submit,
                max_in_flight=1).run()
        result_ref, output = _terminal_output(owner, native.terminal_evidence_ref)
        result = {'stop_reason': native.stop_reason, 'output': output,
                  'run_ref': ref_payload(owner.identity.run_ref),
                  'task_ref': ref_payload(owner.identity.task_ref),
                  'terminal_evidence_ref': ref_payload(native.terminal_evidence_ref),
                  'terminal_result_ref': result_ref,
                  'actual_model_call_counts': list(owner._core.event_store.actual_model_call_counts())}
    finally:
        port.close()
        event_loop.close()
    dump(raw / 'agent-result.json', result)
    assert result['stop_reason'] == 'terminal'
    assert result['output'] == VALUE
    assert result['actual_model_call_counts'] == [1, 0]
    core = _RegistryCore(run_dir, create=False, read_only=True)
    before = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    rows = [dict(row) for row in core.event_store.object_rows()]
    by_version = {row['version_id']: row for row in rows}
    def read(ref):
        ref = normalize(ref)
        row = by_version[ref['version_id']]
        assert exact(row) == ref
        obj = core.get_version(TypedId.parse(ref['version_id']))
        payload = core.object_store.read_registered(obj)
        assert len(payload) == row['size']
        return payload, obj.metadata
    def evidence(ref):
        data, meta = read(ref)
        return {'ref': normalize(ref), 'payload_sha256': sha(data), 'size': len(data)}
    recipes = [r for r in rows if json.loads(r['metadata_json']).get('content_schema_ref') == 'registry_v1/logical_provider_request_recipe/v1']
    assert len(recipes) == len(port.requests) == 1
    recipe_ref = exact(recipes[0])
    recipe_bytes, recipe_meta = read(recipe_ref)
    recipe = json.loads(recipe_bytes)
    assert canonical_json(recipe) == recipe_bytes
    prompt_ref, catalog_ref = recipe['source_prompt_ref'], recipe['tool_catalog_ref']
    prompt_bytes, prompt_meta = read(prompt_ref)
    catalog_bytes, catalog_meta = read(catalog_ref)
    assert prompt_meta['descriptors']['content_role'] == 'optional_agent_prompt'
    assert catalog_meta['descriptors']['content_role'] == 'optional_agent_tool_catalog'
    (out / 'static-prompt.json').write_bytes(prompt_bytes)
    (out / 'tool-catalog.json').write_bytes(catalog_bytes)
    prompt = json.loads(prompt_bytes)
    descriptors = parse_agent_tool_catalog(catalog_bytes).tool_descriptors
    assert recipe['messages'][1:] == prompt['messages']  # fresh first turn, no history/reentry
    rebuilt = materialize_agent_request_envelope(
        model_condition=recipe['model'], max_output_tokens=recipe['max_tokens'],
        system_content=recipe['messages'][0]['content'], prompt_messages=prompt['messages'],
        history_messages=(), tool_descriptors=descriptors,
        source_prompt_ref=prompt_ref, tool_catalog_ref=catalog_ref)
    envelope = json.loads(port.requests[0])
    assert rebuilt == envelope
    assert canonical_json(rebuilt) == port.requests[0]
    assert recipe['messages'] == envelope['messages']
    assert recipe['tools'] == envelope['tools']
    assert recipe_bytes != port.requests[0]
    source_refs = recipe_meta['reference_provenance']['input_resource_refs']
    assert normalize(prompt_ref) in source_refs and normalize(catalog_ref) in source_refs
    source_evidence = [evidence(ref) for ref in source_refs]
    write = next(t['function'] for t in envelope['tools'] if t['function']['name'] == 'write_file')
    instruction = '\n'.join(m['content'] for m in prompt['messages'])
    for guidance in ('content is direct text', 'content is one JSON document'):
        assert guidance in instruction and guidance in write['description']
    assert 'source_resource_ref' in instruction and 'source_resource_ref' in write['description']
    assert 'never both' in instruction and 'workspace bytes are never an implicit write source' in instruction
    assert 'complete=main.result' in instruction and 'never substitute internal port_' in instruction
    actions = [(exact(r), json.loads(r['metadata_json'])) for r in rows if r['object_type'] == 'agent_action/v2']
    wr_ref, wr = next((ref, d) for ref, d in actions if d['tool_name'] == 'write_file')
    co_ref, co = next((ref, d) for ref, d in actions if d['tool_name'] == 'complete_interaction')
    Draft7Validator(write['parameters']).validate(wr['arguments'])
    assert wr['state'] == 'ACTION_APPLIED' and co['state'] == 'COMPLETED'
    assert wr['arguments']['output_port_id'] == 'main.result'
    assert wr['arguments']['outcome_id'] == co['result_metadata']['selected_outcome_id'] == 'complete'
    product_ref = wr['result_refs'][0]
    product_bytes, product_meta = read(product_ref)
    assert product_meta['content_schema_ref'] == OUTPUT_SCHEMA != TEXT_SCHEMA
    assert product_bytes == CONTENT.encode('utf-8')
    assert json.loads(product_bytes) == VALUE
    Draft7Validator(SCHEMA).validate(json.loads(product_bytes))
    assert product_meta['size'] == len(product_bytes)
    assert product_meta['descriptors']['output_outcome_id'] == 'complete'
    assert product_meta['descriptors']['output_port_id'] == wr['result_metadata']['output_port_id']
    schema_ref = product_meta['content_schema_authority_ref']
    schema_bytes, _ = read(schema_ref)
    (out / 'output-schema-authority.json').write_bytes(schema_bytes)
    assert json.loads(schema_bytes) == SCHEMA
    assert json.loads(schema_bytes)['type'] == 'object'
    files = list(run_dir.rglob('outputs/structured.json'))
    assert len(files) == 1
    workspace = files[0].read_bytes()
    assert workspace == CONTENT.encode('utf-8') == product_bytes
    snapshots = [r for r in rows if json.loads(r['metadata_json']).get('descriptors', {}).get('workspace_path') == 'outputs/structured.json']
    assert len(snapshots) == 1
    snapshot_ref = exact(snapshots[0])
    snapshot_bytes, snapshot_meta = read(snapshot_ref)
    assert snapshot_bytes == workspace and snapshot_meta['size'] == len(workspace)
    term_bytes, terminal = read(result['terminal_evidence_ref'])
    assert terminal['run_outcome'] == 'complete'
    assert terminal['terminal_result_ref'] == result['terminal_result_ref'] == product_ref
    operations = [r for r in rows if r['object_type'] == 'operation_result/v1']
    assert len(operations) == 1
    op_ref = exact(operations[0]); _, operation = read(op_ref)
    assert operation['business_outcome'] == 'completed'
    assert product_ref in operation['output_resource_refs']
    assert terminal['terminal_occurrence_ref'] == operation['transition_firing_ref']
    after = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    assert before == after
    assert read(prompt_ref)[0] == prompt_bytes and read(catalog_ref)[0] == catalog_bytes
    report = {
        'status': 'PASS', 'case': 'fresh_native_structured', 'entry': 'trusted HOST ModuleDeclaration/Registration/start_run -> OwnerEventLoop/Orchestrator(Harness)/ExecutionServices optional AgentLoop', 'real_provider_calls': 0,
        'scripted_port_requests': len(port.requests), 'logical_call_ledger': result['actual_model_call_counts'],
        'ledger_tuple_meaning': ['settled_calls', 'post_limit_excess'],
        'static_prompt': evidence(prompt_ref), 'optional_agent_tool_catalog': evidence(catalog_ref),
        'recipe': {**evidence(recipe_ref), 'canonical_sha256': sha(canonical_json(recipe)), 'source_refs': source_evidence},
        'provider_envelope': {'bytes_sha256': sha(port.requests[0]), 'size': len(port.requests[0]),
                              'rematerialized_sha256': sha(canonical_json(rebuilt)), 'messages_and_tools_equal': True,
                              'recipe_bytes_equal_envelope': False},
        'write': {'action_ref': wr_ref, 'completion_action_ref': co_ref,
                  'output_schema': product_meta['content_schema_ref'], 'schema_authority': evidence(schema_ref),
                  'declared_output_port': 'main.result', 'internal_port': wr['result_metadata']['output_port_id'],
                  'outcome': 'complete', 'product': evidence(product_ref), 'workspace_snapshot': evidence(snapshot_ref),
                  'workspace': {'sha256': sha(workspace), 'size': len(workspace)},
                  'representation': 'Non-text object schema. Product/workspace/snapshot preserve identical supplied JSON-document bytes.'},
        'operation_result': evidence(op_ref), 'terminal': evidence(result['terminal_evidence_ref']),
        'run_ref': result['run_ref'], 'task_ref': result['task_ref'], 'terminal_result_ref': product_ref,
        'run_outcome': terminal['run_outcome'], 'read_only_head_before_after': [before, after],
        'fresh_prompt_unchanged_on_readback': True, 'historical_resume': 'NOT_RUN',
        'local_runtime': str(raw), 'local_request': str(raw / 'request-1.json'),
    }
    dump(out / 'evidence.json', report)
    loaded = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
                     if getattr(m, '__file__', None) and Path(m.__file__).resolve().is_relative_to(SOURCE)})
    loaded.append(Path(__file__).resolve())
    dump(out / 'tested-files.json', [{'path': str(p), 'sha256': sha(p.read_bytes()), 'size': p.stat().st_size} for p in loaded if p.is_file()])
