"""Independent local B proof; only input port injection, real native services."""
import hashlib
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft7Validator
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.components.agent_loop.request_envelope_materialization import materialize_agent_request_envelope
from cpn.components.agent_loop.tool_catalog import parse_agent_tool_catalog
from test_main_session_registry import _response, _write_execution_profile

SOURCE = Path('<WORKSPACE>/.h26/s')
BASE = Path('<WORKSPACE>/.h26/bn')
PUBLIC = Path(__file__).resolve().parent
TEXT = '原生文本验收：αβ 🌱\n第二行：保留换行与引号 "hello"\n'

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

class ScriptedTextPort:
    def __init__(self, directory):
        self.directory = directory
        self.requests = []

    def request_once(self, attempt):
        self.requests.append(attempt.canonical_request_bytes)
        (self.directory / f'request-{len(self.requests)}.json').write_bytes(attempt.canonical_request_bytes)
        assert len(self.requests) == 1, 'unexpected additional logical call'
        return _response([
            {'id': 'b-text-write', 'name': 'write_file', 'arguments': json.dumps({
                'path': 'outputs/native.txt', 'description': 'Synthetic Unicode multiline acceptance.',
                'content': TEXT, 'output_port_id': 'main.result', 'outcome_id': 'complete'})},
            {'id': 'b-text-complete', 'name': 'complete_interaction', 'arguments': '{}'},
        ])

    def close(self):
        pass


def test_fresh_native_text_recipe_write(monkeypatch):
    iteration = os.environ['B_ITERATION']
    raw = BASE / iteration
    raw.mkdir()
    out = PUBLIC / iteration
    out.mkdir()
    port = ScriptedTextPort(raw)
    monkeypatch.setattr('cpn.rpnh.agent_tasks.build_llm_input_port', lambda *_a, **_k: port)
    spec = AgentTaskSpec(raw / 'r', 'Write the synthetic Unicode multiline text.',
                         (AgentStage('main', 'Return the exact synthetic text.'),),
                         _write_execution_profile(raw), max_attempts_per_stage=1)
    result = run_agent_task(spec)
    dump(raw / 'agent-result.json', result)
    assert result['stop_reason'] == 'terminal'
    assert result['output'] == TEXT
    assert result['actual_model_call_counts'] == [1, 0]
    core = _RegistryCore(spec.run_dir, create=False, read_only=True)
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
    assert product_meta['content_schema_ref'] == 'application/rpnh_agent_text/v1'
    assert product_bytes == canonical_json(TEXT)
    assert product_meta['size'] == len(product_bytes)
    assert product_meta['descriptors']['output_outcome_id'] == 'complete'
    assert product_meta['descriptors']['output_port_id'] == wr['result_metadata']['output_port_id']
    schema_ref = product_meta['content_schema_authority_ref']
    schema_bytes, _ = read(schema_ref)
    (out / 'output-schema-authority.json').write_bytes(schema_bytes)
    files = list(spec.run_dir.rglob('outputs/native.txt'))
    assert len(files) == 1
    workspace = files[0].read_bytes()
    assert workspace == TEXT.encode('utf-8')
    snapshots = [r for r in rows if json.loads(r['metadata_json']).get('descriptors', {}).get('workspace_path') == 'outputs/native.txt']
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
        'status': 'PASS', 'case': 'fresh_native_text', 'real_provider_calls': 0,
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
                  'representation': 'Product is canonical JSON string; workspace/snapshot are UTF-8 direct text.'},
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
