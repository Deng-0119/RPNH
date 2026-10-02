"""Deterministic local-process adapter. No model SDK or network imports."""
import json
import sys
from pathlib import Path


def declared_bundle(request):
    marker = 'Broad output contract: Use exactly one declared semantic outcome bundle: '
    for message in request['messages']:
        if message.get('role') != 'system' or not isinstance(message.get('content'), str):
            continue
        for line in message['content'].splitlines():
            if not line.startswith(marker):
                continue
            bundle, _ = json.JSONDecoder().raw_decode(line[len(marker):])
            if len(bundle) != 1:
                raise ValueError('expected one declared outcome')
            outcome, ports = next(iter(bundle.items()))
            if not isinstance(ports, list) or len(ports) != 1:
                raise ValueError('expected one declared semantic output port')
            return outcome, ports[0]
    raise ValueError('framework did not declare an exact semantic outcome bundle')


request = json.loads(sys.stdin.buffer.read())
scenario = json.loads(Path(sys.argv[1]).read_text())
steps = scenario['steps']
# Read actual registered tool history; no external mutable response counter.
seen = [m for m in request['messages'] if m.get('role') == 'assistant']
index = len(seen)
if index < len(steps):
    step = steps[index]
    calls = [{'id':f'offline-{index}', 'name':step['tool'], 'arguments':json.dumps(step['arguments'], sort_keys=True)}]
    result = {'protocol':'llm_response_envelope/v1','tool_calls':calls,'finish_reason':'tool_calls'}
else:
    tools = {x.get('function',x).get('name') for x in request['tools']}
    if 'write_file' in tools:
        outcome, output_port = declared_bundle(request)
        calls=[{'id':'offline-report','name':'write_file','arguments':json.dumps({'path':'outputs/report.txt','description':'offline verification report','content':'Deterministic tools finished.','outcome_id':outcome,'output_port_id':output_port})}, {'id':'offline-complete','name':'complete_interaction','arguments':'{}'}]
        result={'protocol':'llm_response_envelope/v1','tool_calls':calls,'finish_reason':'tool_calls'}
    else:
        result={'protocol':'llm_response_envelope/v1','text':'Deterministic tools finished.','tool_calls':[], 'finish_reason':'stop'}
if scenario.get('requests_log'):
    with Path(scenario['requests_log']).open('a') as out:
        out.write(json.dumps(request, sort_keys=True)+'\n')
sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(',',':')))
