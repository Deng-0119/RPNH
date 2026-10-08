"""Synthetic stdin/stdout adapter. No real provider/config/credential access."""
from __future__ import annotations

import json
from pathlib import Path
import sys

MODEL = 'scb-synthetic-local-process-v1'
TOOLS = {'complete_interaction', 'read_file', 'read_managed_output', 'write_file', 'session_command'}


def input_locator(request):
    found = []
    for message in request['messages']:
        if message['role'] != 'system':
            continue
        for line in message.get('content', '').splitlines():
            if line.startswith('- {'):
                item = json.loads(line[2:])
                if item.get('summary') == 'RPNH agent task request' and item.get('sandbox_path'):
                    found.append(item['sandbox_path'])
    if len(found) != 1:
        raise ValueError('expected one registered Located request')
    return found[0]


def response(request, fixture):
    if request['protocol'] != 'llm_request_envelope/v1' or request['model_condition'] != MODEL:
        raise ValueError('synthetic protocol/model mismatch')
    names = {item['function']['name'] for item in request['tools']}
    if names != TOOLS:
        raise ValueError('unexpected actor tools: ' + repr(sorted(names)))
    seen = set()
    for message in request['messages']:
        if message['role'] == 'assistant':
            for call in message.get('tool_calls', []):
                seen.add(call.get('function', call)['name'])
    results = {m['tool_call_id']: json.loads(m['content'])
               for m in request['messages'] if m['role'] == 'tool'}

    def call(identity, name, arguments):
        return {'id': identity, 'name': name, 'arguments': json.dumps(arguments)}

    if 'read_file' not in seen:
        calls = [call('native-read', 'read_file', {'path': input_locator(request)})]
    elif 'session_command' not in seen:
        observed = results.get('native-read', {})
        if observed.get('content') != fixture['prompt'] or not observed.get('resource_ref') or not observed.get('use_receipt_ref'):
            raise ValueError('registered original synthetic input was not delivered')
        calls = [call('native-command', 'session_command', {'command': fixture['command']})]
    else:
        if 'write_file' in seen:
            raise ValueError('synthetic final product was rejected; do not repeat an unchanged call')
        if fixture['case'] != 'complete':
            raise ValueError('stopped fixture must not produce a completion report')
        if fixture['marker'] not in json.dumps(results.get('native-command', {})):
            raise ValueError('actual subprocess marker missing from tool result')
        ports = [line.split(':', 1)[1].strip()
                 for message in request['messages'] if message['role'] == 'system'
                 for line in message.get('content', '').splitlines()
                 if line.startswith('Provider-writable semantic output_port_ids (exact):')]
        if len(ports) != 1 or ',' in ports[0] or not ports[0]:
            raise ValueError('expected one actual declared semantic output port')
        calls = [call('native-report', 'write_file', {
            'path': 'outputs/native.txt', 'description': 'Synthetic native transport evidence only',
            'content': json.dumps('Synthetic subprocess observed: ' + fixture['marker']),
            'output_port_id': ports[0], 'outcome_id': 'complete'}),
            call('native-complete', 'complete_interaction', {})]
    return {'protocol': 'llm_response_envelope/v1', 'tool_calls': calls, 'finish_reason': 'tool_calls'}


def write_profile(directory, fixture):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    (directory / 'transcript').mkdir(mode=0o700)
    (directory / 'fixture.json').write_text(json.dumps(fixture))
    adapter = directory / 'adapter.json'
    adapter.write_text(json.dumps({
        'schema_version': 'local_process_adapter_config/v1', 'adapter_kind': 'local_process',
        'model_condition': MODEL,
        'argv': ['{python}', '-B', str(Path(__file__).resolve()), str(directory)],
        'probe_argv': ['{python}', '-B', str(Path(__file__).resolve())],
        'env': {'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(directory)}, 'inherit_env': []}))
    execution = directory / 'execution.json'
    execution.write_text(json.dumps({
        'schema_version': 'llm_execution_selection/v1', 'adapter_kind': 'local_process',
        'model_condition': MODEL, 'adapter_config_path': str(adapter),
        'timeout_seconds': 30, 'max_output_tokens': 2048, 'max_response_bytes': 262144}))
    return execution


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('explicit synthetic profile directory required')
    directory = Path(sys.argv[1])
    fixture = json.loads((directory / 'fixture.json').read_text())
    request = json.load(sys.stdin)
    ordinal = len(list((directory / 'transcript').glob('request-*.json'))) + 1
    if ordinal > 8:
        raise SystemExit('synthetic fixture exceeded declared 8 calls')
    with (directory / 'transcript' / f'request-{ordinal}.json').open('x') as stream:
        json.dump(request, stream, sort_keys=True)
    value = response(request, fixture)
    with (directory / 'transcript' / f'response-{ordinal}.json').open('x') as stream:
        json.dump(value, stream, sort_keys=True)
    sys.stdout.write(json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')))
