"""Real loopback TLS/process/installed-CLI tests, not external-model evidence.

These deterministic loopback cases never contact a supplier. Certificate
verification stays enabled. All payloads and credentials below are synthetic.
Stop latency is a campaign acceptance target, not an existing documented SLA.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
from uuid import uuid4
import pytest
from cpn.llm_adapters.external_provider import ExternalProviderInputPort
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputPortFailure, LLMInputPortInterrupted
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef

MODEL = 'wire/exact-model@fixture-v1'

def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()

def completion(text='wire-answer', **message):
    return canonical({'id':'wire-response','choices':[{'message':{'role':'assistant','content':text,**message},'finish_reason':'stop'}], 'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})

@pytest.fixture(scope='session')
def tls(tmp_path_factory):
    root=tmp_path_factory.mktemp('tls')
    cert,key=root/'cert.pem',root/'key.pem'
    p=subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],capture_output=True)
    assert p.returncode==0,p.stderr.decode()
    return cert,key

@dataclass
class Server:
    http: ThreadingHTTPServer
    records: list
    root: Path
    entered: threading.Event
    scheme: str = 'https'
    @property
    def endpoint(self): return f'{self.scheme}://127.0.0.1:{self.http.server_address[1]}/v1/chat/completions'

@contextmanager
def serve(root, tls, actions, *, scheme='https'):
    lock=threading.Lock(); records=[]; entered=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            with lock:
                ordinal=len(records)
                action=(actions(ordinal,json.loads(body)) if callable(actions) else actions[min(ordinal,len(actions)-1)])
                records.append({'ordinal':ordinal,'method':'POST','target':self.path,'body':json.loads(body),'headers':dict(self.headers),'at_monotonic_ns':time.monotonic_ns(),'fault':{k:v for k,v in action.items() if k!='body'},'planned_response_base64':base64.b64encode(action.get('body',completion())).decode()})
                (root/'wire-server.json').write_text(json.dumps(records,indent=2))
            entered.set()
            try:
                if action.get('delay'): time.sleep(action['delay'])
                if action.get('disconnect'):
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                payload=action.get('body',completion())
                self.send_response(action.get('status',200))
                self.send_header('Content-Type',action.get('type','application/json'))
                self.send_header('Content-Length',str(len(payload)+action.get('length_extra',0)))
                self.send_header('x-request-id',f'wire-{ordinal}')
                for k,v in action.get('headers',{}).items():self.send_header(k,v)
                self.end_headers()
                if action.get('body_delay'):
                    self.wfile.flush();time.sleep(action['body_delay'])
                self.wfile.write(payload);self.wfile.flush()
                if action.get('length_extra'):
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close()
            except (OSError,ssl.SSLError):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.daemon_threads=True
    if scheme == 'https':
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(*map(str,tls));http.socket=ctx.wrap_socket(http.socket,server_side=True)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield Server(http,records,root,entered,scheme)
    finally:
        http.shutdown();http.server_close();thread.join(timeout=2)
        (root/'wire-server.json').write_text(json.dumps(records,indent=2))

def make_port(root, endpoint, *, timeout=2, cap=1024*1024, probes=1, cycles=1, credential=None):
    config=root/'adapter.json';run=root/'run'
    config.write_bytes(canonical({'schema_version':'external_provider_adapter_config/v2','adapter_kind':'external_provider','model_condition':MODEL,'recovery':{'strategy':'bounded_same_route_health_probe/v1','max_probe_attempts':probes,'probe_timeout_budget_seconds':3,'max_probe_success_formal_failure_cycles':cycles},'routes':[{'route_id':'fixture','provider':'wire-fixture','backend':'loopback','protocol':'openai_chat_completions/v1','endpoint':endpoint,'outbound_model':MODEL,'credential':credential,'headers':{}}]}))
    request=canonical({'protocol':'llm_request_envelope/v1','model_condition':MODEL,'max_output_tokens':32,'messages':[{'role':'user','content':'Formal nonce '+uuid4().hex}],'tools':[],'tool_choice':'none'})
    attempt=LLMCallAttempt(invocation_ref=VersionRef('llm_invocation_spec/v1',new_id('llm_invocation'),new_id('llm_invocation_version')),attempt_ref=VersionRef('llm_invocation_attempt/v1',new_id('llm_invocation_attempt'),new_id('llm_invocation_attempt_version')),attempt_ordinal=0,model_condition=MODEL,canonical_request_bytes=request,max_response_bytes=cap)
    return ExternalProviderInputPort(model_condition=MODEL,max_output_tokens=32,timeout_seconds=timeout,max_response_bytes=cap,config_path=config,destination_run_root=run),attempt

def audit(root):
    return [json.loads(s) for s in (root/'run'/'adapter-private'/'llm-attempts.jsonl').read_text().splitlines()]

@pytest.fixture(autouse=True)
def ca(monkeypatch,tls):monkeypatch.setenv('SSL_CERT_FILE',str(tls[0]))

def test_tls_success_exact_model_and_audit(tmp_path,tls):
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint);r=p.request_once(a)
        assert r.status_code==200 and r.external_request_id=='wire-0'
        assert len(s.records)==1
        wire=s.records[0]['body'];assert wire['model']==MODEL and wire['stream'] is False and wire['max_tokens']==32
        assert s.records[0]['target']=='/v1/chat/completions'
        rows=audit(tmp_path);assert sum(x['lifecycle']=='provider_attempt_started' for x in rows)==1
        assert sum(x['lifecycle']=='provider_attempt_finished' for x in rows)==1

def test_loopback_http_uses_same_provider_port_and_audit(tmp_path,tls):
    with serve(tmp_path,tls,[{}],scheme='http') as s:
        p,a=make_port(tmp_path,s.endpoint);r=p.request_once(a)
        assert r.status_code==200 and r.external_request_id=='wire-0'
        assert len(s.records)==1 and s.records[0]['body']['model']==MODEL
        started=[x for x in audit(tmp_path) if x['lifecycle']=='provider_attempt_started']
        assert len(started)==1 and started[0]['transport']=='http'

@pytest.mark.parametrize('status',[400,401,403,404,413,422,302])
def test_nonretryable_http_stops_after_one_physical_call(tmp_path,tls,status):
    with serve(tmp_path,tls,[{'status':status,'headers':{'Location':'https://example.invalid/never'}}]) as s:
        p,a=make_port(tmp_path,s.endpoint)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert len(s.records)==1

@pytest.mark.parametrize('status',[408,429,500,502,503,504])
def test_transient_http_same_route_probe_then_formal_retry(tmp_path,tls,status):
    with serve(tmp_path,tls,[{'status':status},{'body':completion('READY.')},{'body':completion('recovered')}]) as s:
        p,a=make_port(tmp_path,s.endpoint);p.request_once(a)
        assert len(s.records)==3
        first,probe,last=[x['body'] for x in s.records]
        assert first==last
        assert probe['messages'][0]['content']=='Reply with READY.' and probe['max_tokens']==8
        assert {x['model'] for x in [first,probe,last]}=={MODEL}
        rows=[x for x in audit(tmp_path) if x['lifecycle']=='provider_attempt_started']
        assert [x['call_kind'] for x in rows]==['real_model_call','health_probe','real_model_call']
        assert len({x['provider_attempt_id'] for x in rows})==3

def test_recovery_physical_calls_are_bounded(tmp_path,tls):
    def action(i,request):
        probe=request['messages'][0]['content']=='Reply with READY.'
        return {'status':200 if probe and i%4==3 else 503}
    with serve(tmp_path,tls,action) as s:
        p,a=make_port(tmp_path,s.endpoint,probes=3,cycles=3)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert len(s.records)==13
        rows=[x for x in audit(tmp_path) if x['lifecycle']=='provider_attempt_started']
        assert sum(x['call_kind']=='real_model_call' for x in rows)==4
        assert sum(x['call_kind']=='health_probe' for x in rows)==9

@pytest.mark.parametrize('payload',[b'',b'not json',b'{"choices":[],"choices":[]}',b'\xff',b'[]',canonical({'choices':[]}),completion(['not text']),completion(None,tool_calls=[{'id':'dup','type':'function','function':{'name':'f','arguments':'{}'}},{'id':'dup','type':'function','function':{'name':'f','arguments':'{}'}}])])
def test_invalid_complete_response_is_not_retried(tmp_path,tls,payload):
    with serve(tmp_path,tls,[{'body':payload}]) as s:
        p,a=make_port(tmp_path,s.endpoint)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert len(s.records)==1

def test_partial_body_is_not_silently_replayed(tmp_path,tls):
    with serve(tmp_path,tls,[{'body':b'{"choices":[','length_extra':400}]) as s:
        p,a=make_port(tmp_path,s.endpoint)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert len(s.records)==1

def test_submission_unknown_keeps_physical_retry_evidence(tmp_path,tls):
    with serve(tmp_path,tls,[{'disconnect':True},{'body':completion('READY')},{}]) as s:
        p,a=make_port(tmp_path,s.endpoint);p.request_once(a)
        assert len(s.records)==3 and s.records[0]['body']==s.records[2]['body']
        rows=[x for x in audit(tmp_path) if x['lifecycle']=='provider_attempt_finished']
        assert rows[0]['outcome']=='submission_unknown'

def test_response_byte_limit_no_retry(tmp_path,tls):
    with serve(tmp_path,tls,[{'body':completion('X'*8192)}]) as s:
        p,a=make_port(tmp_path,s.endpoint,cap=1024)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert len(s.records)==1

def test_duplicate_submission_rejected_across_adapter_reopen(tmp_path,tls):
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint);p.request_once(a);p.close()
        second=ExternalProviderInputPort(model_condition=MODEL,max_output_tokens=32,timeout_seconds=2,max_response_bytes=1024*1024,config_path=tmp_path/'adapter.json',destination_run_root=tmp_path/'run')
        with pytest.raises(LLMInputPortFailure):second.request_once(a)
        assert len(s.records)==1

def test_missing_credential_produces_zero_requests(tmp_path,tls,monkeypatch):
    monkeypatch.delenv('RPNH_WIRE_TEST_KEY',raising=False)
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint,credential={'environment':'RPNH_WIRE_TEST_KEY','header':'Authorization','prefix':'Bearer '})
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert not s.records

def test_synthetic_credential_is_absent_from_adapter_audit(tmp_path,tls,monkeypatch):
    secret='SYNTHETIC_ONLY_'+uuid4().hex;monkeypatch.setenv('RPNH_WIRE_TEST_KEY',secret)
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint,credential={'environment':'RPNH_WIRE_TEST_KEY','header':'Authorization','prefix':'Bearer '});p.request_once(a)
        assert s.records[0]['headers']['Authorization']=='Bearer '+secret
        assert secret not in (tmp_path/'run'/'adapter-private'/'llm-attempts.jsonl').read_text()

def test_real_tls_owner_stop_interrupts_blocked_headers_promptly(tmp_path,tls):
    with serve(tmp_path,tls,[{'delay':2.5}]) as s:
        p,a=make_port(tmp_path,s.endpoint,timeout=5);stopped=threading.Event()
        def stop():
            assert s.entered.wait(2)
            time.sleep(.15);stopped.set()
        stopper=threading.Thread(target=stop,daemon=True);stopper.start();start=time.monotonic()
        with pytest.raises(LLMInputPortInterrupted):p.request_once_interruptible(a,interruption_requested=stopped.is_set)
        elapsed=time.monotonic()-start;stopper.join(timeout=1)
        (tmp_path/'stop-timing.json').write_text(json.dumps({'elapsed_seconds':elapsed,'synthetic_provider_delay':2.5,'acceptance_seconds':1.5}))
        assert len(s.records)==1
        assert elapsed<1.5,f'Owner stop waited {elapsed:.3f}s for server instead of promptly interrupting I/O'

def test_installed_basic_cli_over_real_tls_registry_terminal(tmp_path,tls):
    answer='WIRE_E2E_OK_'+uuid4().hex
    calls=[{'id':'wire-write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'outputs/result.txt','description':'Synthetic main reply.','content':json.dumps({'reply':answer,'task':None}),'output_port_id':'main.result','outcome_id':'complete'})}},{'id':'wire-complete','type':'function','function':{'name':'complete_interaction','arguments':'{}'}}]
    response=canonical({'id':'wire-e2e','choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})
    with serve(tmp_path,tls,[{'body':response}]) as s:
        root=tmp_path/'session';catalog=tmp_path/'catalog.json';profiles=tmp_path/'profiles'
        catalog.write_bytes(canonical({'schema_version':'rpnh/provider_model_catalog/v2','providers':[{'provider':'wire-fixture','display_name':'Wire fixture','models':[{'profile':'wire','model_condition':MODEL,'adapter':{'adapter_kind':'external_provider','route_id':'fixture','backend':'loopback','protocol':'openai_chat_completions/v1','endpoint':s.endpoint,'credential':None,'headers':{},'recovery':{'strategy':'bounded_same_route_health_probe/v1','max_probe_attempts':1,'probe_timeout_budget_seconds':3,'max_probe_success_formal_failure_cycles':1}},'timeout_seconds':10,'max_output_tokens':256,'max_response_bytes':1024*1024}]}]}))
        rpnh=shutil.which('rpnh');assert rpnh,'installed rpnh command missing'
        env=dict(os.environ,HOME=str(tmp_path/'home'),RPNH_CONFIG=str(tmp_path/'selected.json'),RPNH_PROFILE_DIR=str(profiles/'execution'),RPNH_PROVIDER_CATALOG=str(catalog))
        def run(label,args,seconds=60):
            try:
                r=subprocess.run([rpnh,*args],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=seconds)
            except subprocess.TimeoutExpired as exc:
                (tmp_path/(label+'.stdout')).write_bytes(exc.stdout or b'');(tmp_path/(label+'.stderr')).write_bytes(exc.stderr or b'');raise
            (tmp_path/(label+'.stdout')).write_text(r.stdout);(tmp_path/(label+'.stderr')).write_text(r.stderr)
            assert r.returncode==0,r.stderr+'\n'+r.stdout
            return r
        run('build',['config','build','--catalog',str(catalog),'--output-root',str(profiles)])
        execution=profiles/'execution'/'wire.json';assert execution.exists()
        result=run('cli',['--frontend','basic','--execution',str(execution),'--session-dir',str(root),'--prompt','Return '+answer+'. Do not launch other tasks.'])
        assert answer in result.stdout and len(s.records)==1
        from cpn.rpnh.main_session import MainSession
        session=MainSession.resume(root)
        assert any(answer==text for role,text in session.history if role=='assistant')
        assert len(s.records)==1,'Reading formal history must not redispatch'


@pytest.mark.parametrize('close_connection',[False,True])
@pytest.mark.parametrize('repeat',range(2))
def test_owner_stop_interrupts_body_even_after_socket_detach(tmp_path,tls,close_connection,repeat):
    headers={'Connection':'close'} if close_connection else {}
    with serve(tmp_path,tls,[{'body_delay':2.5,'headers':headers}]) as s:
        p,a=make_port(tmp_path,s.endpoint,timeout=5)
        stopped=threading.Event()
        def stop():
            assert s.entered.wait(2)
            time.sleep(.15);stopped.set()
        stopper=threading.Thread(target=stop,daemon=True);stopper.start()
        start=time.monotonic()
        with pytest.raises(LLMInputPortInterrupted):
            p.request_once_interruptible(a,interruption_requested=stopped.is_set)
        elapsed=time.monotonic()-start;stopper.join(1)
        (tmp_path/'stop-timing.json').write_text(json.dumps({'elapsed_seconds':elapsed,
            'phase':'body','connection_close':close_connection,'repeat':repeat}))
        assert elapsed<1.5
        assert len(s.records)==1
        rows=audit(tmp_path)
        assert [x['outcome'] for x in rows if x['lifecycle']=='provider_attempt_finished']==['owner_interrupted']
        assert not any(x.get('call_kind')=='health_probe' for x in rows)


def test_stop_before_connect_has_zero_physical_requests(tmp_path,tls):
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint)
        with pytest.raises(LLMInputPortInterrupted) as error:
            p.request_once_interruptible(a,interruption_requested=lambda:True)
        assert error.value.submission_state=='not_submitted'
        assert not s.records


def test_untrusted_tls_certificate_never_sends_http(tmp_path,tls,monkeypatch):
    monkeypatch.delenv('SSL_CERT_FILE',raising=False)
    with serve(tmp_path,tls,[{}]) as s:
        p,a=make_port(tmp_path,s.endpoint)
        with pytest.raises(LLMInputPortFailure):p.request_once(a)
        assert not s.records
