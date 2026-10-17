"""Deterministic native reader/workspace preflight; no model or business answers."""
import json
import os
import sys

request=json.load(sys.stdin)
results={m['tool_call_id']:json.loads(m['content']) for m in request['messages'] if m['role']=='tool'}
def call(key,name,args):
    return {'id':key,'name':name,'arguments':json.dumps(args)}
if 'read-request' not in results:
    located=[json.loads(line[2:]) for m in request['messages'] if m['role']=='system'
        for line in m.get('content','').splitlines() if line.startswith('- {')]
    inputs=[v for v in located if v.get('sandbox_path') and v.get('content_schema_ref')=='application/rpnh_agent_text/v1']
    assert len(inputs)==1
    calls=[call('read-request','read_file',{'path':inputs[0]['sandbox_path']}),
           call('read-forbidden','read_file',{'path':os.environ['MUP_FORBIDDEN_READ_PATH']})]
else:
    actual=results['read-request']
    assert actual['kind']=='registered_file_read/v1' and actual['resource_ref'] and actual['use_receipt_ref']
    assert 'NATIVE_READER_FIXTURE_INPUT' in actual['content']
    assert 'read_file path must be an exact registered input' in json.dumps(results['read-forbidden'])
    ports=[line.split(': ',1)[1] for m in request['messages'] if m['role']=='system'
        for line in m.get('content','').splitlines() if line.startswith('Provider-writable semantic output_port_ids (exact): ')]
    assert len(ports)==1 and ',' not in ports[0]
    calls=[call('fixture-output','write_file',{'path':'outputs/reader-proof.json',
        'description':'Synthetic native reader proof, not model cognition',
        'content':json.dumps({'fixture_only':True,'actual_registered_read':actual,'denial':results['read-forbidden']}),
        'output_port_id':ports[0],'outcome_id':'complete'}),call('fixture-complete','complete_interaction',{})]
sys.stdout.write(json.dumps({'protocol':'llm_response_envelope/v1','tool_calls':calls,'finish_reason':'tool_calls'},ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')))
