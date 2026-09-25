"""Additional transport fault boundaries; loopback only, no supplier calls."""
from contextlib import contextmanager
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import selectors
import socket
import ssl
import threading
import time

import pytest
from cpn.rpnh.control_client import ControlProtocolError, parse_reply, serialize_command
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.llm_contracts import LLMInputPortFailure
from test_api_wire import audit, ca, completion, make_port, serve, tls


class Owner:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result
    def snapshot(self):
        self.calls += 1
        return self.result if self.result is not None else {'calls': self.calls}
    def command(self, name, arguments, command_id):
        self.calls += 1
        return {'status': 'OK'}


def dispatch(loop, rounds=4):
    for _ in range(rounds):
        loop.dispatch_ready(timeout=.025)


def client(loop, wire):
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    channel.settimeout(1)
    channel.connect(str(loop.socket_path))
    channel.sendall(wire)
    return channel


def good_after(loop, owner):
    before = owner.calls
    done = loop.submit_host(lambda: 'host-completion')
    with client(loop, serialize_command('valid', 'snapshot', {})) as channel:
        dispatch(loop)
        reply = parse_reply(channel.recv(65536), 'valid')
        assert reply['status'] == 'OK'
    assert owner.calls == before + 1
    assert done.result(timeout=1) == 'host-completion'


@pytest.mark.parametrize('field', ['command_id', 'command', 'argument_value', 'argument_key'])
def test_escaped_unpaired_surrogate_rejected_before_owner(tmp_path, field):
    owner = Owner()
    loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    command = {'command_id': 'c', 'command': 'snapshot', 'arguments': {}}
    if field in command:
        command[field] = '\ud800'
    elif field == 'argument_value':
        command['arguments'] = {'value': '\udfff'}
    else:
        command['arguments'] = {'\ud800': 1}
    wire = (json.dumps(command, ensure_ascii=True) + '\n').encode('ascii')
    try:
        with client(loop, wire) as channel:
            dispatch(loop)
            assert channel.recv(1) == b''
        assert owner.calls == 0
        good_after(loop, owner)
    finally:
        loop.close()


def test_valid_surrogate_pair_decodes_to_ordinary_unicode(tmp_path):
    owner = Owner(); loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    try:
        wire = b'{"command_id":"\\ud83d\\ude00","command":"snapshot","arguments":{}}\n'
        with client(loop, wire) as channel:
            dispatch(loop)
            reply = parse_reply(channel.recv(65536), '\U0001f600')
            assert reply['result'] == {'calls': 1}
    finally:
        loop.close()


@pytest.mark.parametrize('bad', ['\ud800', object(), float('nan')])
def test_unserializable_reply_does_not_kill_owner_or_repeat_command(tmp_path, bad):
    owner = Owner({'payload': bad}); loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    try:
        with client(loop, serialize_command('bad-result', 'snapshot', {})) as channel:
            dispatch(loop)
            assert channel.recv(1) == b''
        assert owner.calls == 1  # Reply loss must not replay the command.
        owner.result = None
        good_after(loop, owner)
    finally:
        loop.close()


class BackpressureSocket:
    """One deterministic EAGAIN on an otherwise real accepted Unix socket."""
    def __init__(self, channel): self.channel, self.calls = channel, 0
    def fileno(self): return self.channel.fileno()
    def send(self, data):
        self.calls += 1
        if self.calls == 1: raise BlockingIOError('synthetic EAGAIN')
        return self.channel.send(data)
    def close(self): self.channel.close()


def test_reply_backpressure_keeps_pending_bytes_and_owner_live(tmp_path):
    owner = Owner(); loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    try:
        with client(loop, serialize_command('pressure', 'snapshot', {})) as channel:
            dispatch(loop, 2)  # accept then read/prepare reply; not yet write
            accepted, = loop.outputs
            wrapper = BackpressureSocket(accepted)
            loop.selector.unregister(accepted)
            loop.buffers[wrapper] = loop.buffers.pop(accepted)
            loop.outputs[wrapper] = loop.outputs.pop(accepted)
            loop.selector.register(wrapper, selectors.EVENT_WRITE, 'client')
            before = loop.outputs[wrapper]
            dispatch(loop, 1)
            assert loop.outputs[wrapper] == before
            dispatch(loop, 1)
            assert parse_reply(channel.recv(65536), 'pressure')['status'] == 'OK'
            assert owner.calls == 1
        good_after(loop, owner)
    finally:
        loop.close()


def test_accepted_host_reply_cancellation_is_not_owner_cancellation(tmp_path):
    owner = Owner(); loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    effects = []
    try:
        reply = loop.submit_host(lambda: effects.append('accepted') or 42)
        assert reply.cancel() is False  # Accepted HOST work is owner-controlled.
        dispatch(loop)
        assert reply.result(timeout=1) == 42 and effects == ['accepted']
        good_after(loop, owner)
    finally:
        loop.close()


def test_accepted_host_exception_preserves_later_completion(tmp_path):
    owner = Owner(); loop = OwnerEventLoop(owner, tmp_path/'owner.sock')
    def fail(): raise RuntimeError('synthetic host failure')
    try:
        reply = loop.submit_host(fail)
        assert reply.cancel() is False
        later = loop.submit_host(lambda: 99)
        dispatch(loop)
        with pytest.raises(RuntimeError, match='synthetic host failure'):
            reply.result(timeout=1)
        assert later.result(timeout=1) == 99
    finally:
        loop.close()


@pytest.mark.parametrize('close_connection', [False, True])
def test_valid_json_with_truncated_content_length_is_not_a_complete_response(tmp_path, tls, close_connection):
    headers = {'Connection': 'close'} if close_connection else {}
    with serve(tmp_path, tls, [{'body': completion('valid-json-is-not-complete-wire'),
                              'length_extra': 200, 'headers': headers}]) as service:
        port, attempt = make_port(tmp_path, service.endpoint)
        with pytest.raises(LLMInputPortFailure):
            port.request_once(attempt)
        assert len(service.records) == 1
        finished = [e for e in audit(tmp_path) if e['lifecycle'] == 'provider_attempt_finished']
        assert len(finished) == 1
        assert finished[0]['detail']['submission_state'] == 'response_headers_received_body_incomplete'
        assert finished[0]['detail']['retry_eligible'] is False
        assert finished[0]['outcome'] != 'response_returned'


@contextmanager
def trickle_server(root, tls, *, close_connection=False, chunked=False, truncated=False):
    """Complete JSON sent slowly or chunk-framed, with independent receipt log."""
    records = []
    stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args): pass
        def do_POST(self):
            records.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            (root/'requests.json').write_text(json.dumps(records))
            body = completion('trickle')
            self.send_response(200)
            if chunked: self.send_header('Transfer-Encoding', 'chunked')
            else: self.send_header('Content-Length', str(len(body)))
            if close_connection: self.send_header('Connection', 'close')
            self.end_headers()
            try:
                if chunked:
                    self.wfile.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n')
                    if not truncated: self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                    if truncated:
                        self.connection.shutdown(socket.SHUT_RDWR); self.connection.close()
                else:
                    width = max(1, len(body)//12)
                    for index in range(0, len(body), width):
                        if stop.is_set(): break
                        self.wfile.write(body[index:index+width]); self.wfile.flush()
                        if stop.wait(.2): break
            except (OSError, ssl.SSLError): pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler); http.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(*map(str, tls))
    http.socket = context.wrap_socket(http.socket, server_side=True)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    try:
        yield f'https://127.0.0.1:{http.server_address[1]}/v1/chat/completions', records
    finally:
        stop.set(); http.shutdown(); http.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('close_connection', [False, True])
def test_slow_body_cannot_extend_the_formal_request_deadline(tmp_path, tls, close_connection):
    with trickle_server(tmp_path, tls, close_connection=close_connection) as (endpoint, records):
        port, attempt = make_port(tmp_path, endpoint, timeout=1)
        start = time.monotonic()
        with pytest.raises(LLMInputPortFailure):
            port.request_once(attempt)
        elapsed = time.monotonic()-start
        (tmp_path/'deadline.json').write_text(json.dumps({'elapsed': elapsed, 'timeout': 1}))
        assert elapsed < 1.8, f'continuous body activity extended total timeout: {elapsed}'
        assert len(records) == 1
        finished = [e for e in audit(tmp_path) if e['lifecycle'] == 'provider_attempt_finished']
        assert finished[0]['detail']['submission_state'] == 'response_headers_received_body_incomplete'


@pytest.mark.parametrize('truncated', [False, True])
def test_chunked_response_requires_final_zero_chunk(tmp_path, tls, truncated):
    with trickle_server(tmp_path, tls, chunked=True, truncated=truncated) as (endpoint, records):
        port, attempt = make_port(tmp_path, endpoint)
        if truncated:
            with pytest.raises(LLMInputPortFailure): port.request_once(attempt)
        else:
            assert port.request_once(attempt).status_code == 200
        assert len(records) == 1
