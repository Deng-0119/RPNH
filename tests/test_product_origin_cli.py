"""Offline adapter-risk tests only; the public reader owns origin semantics."""
from copy import deepcopy
from dataclasses import replace
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cpn import rpnh_cli
from cpn.rpnh.collaboration import origin_cli as cli
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits, RegistryReadSessionError
from cpn.rpnh.registry.schema_catalog import canonical_json


def root_document(kind='resource'):
    if kind == 'resource':
        schema = 'rpnh/collaboration/source_resource_ref/v1'
        ref = {'resource_id': 'resource:' + '1' * 32,
               'resource_version_id': 'resource_version:' + '2' * 32}
    else:
        schema = 'rpnh/collaboration/source_version_ref/v1'
        ref = {'entity_type': 'operation_result/v1',
               'logical_id': 'operation_result:' + '1' * 32,
               'version_id': 'operation_result_version:' + '2' * 32}
    return {'schema_version': schema, 'source_id': 'source-a', 'ref': ref}


@pytest.fixture
def root_file(tmp_path):
    path = tmp_path / 'root.json'
    path.write_bytes(canonical_json(root_document()))
    return path


def pages():
    # Exact transport preserves every field and original continuation. Reader
    # schema/closure semantics and the native example are tested separately.
    base = {'schema_version': 'rpnh/product_origin_page/v1',
            'profile': 'product_origin_v1', 'contract_revision': 2,
            'root': root_document(), 'source_cut': {'cut_id': 'cut-a'},
            'access_revision': 'access-a', 'root_proof': {'root_role': 'registered_output'}}
    return [{**deepcopy(base), 'rows': [{'role': 'start_input', 'position': 0}],
             'coverage': {'state': 'partial'}, 'continuation': 'internal-cursor'},
            {**deepcopy(base), 'rows': [{'role': 'claim', 'position': 1}],
             'coverage': {'state': 'complete'}, 'continuation': None}]


class Session:
    """Only the public read surface exists on this adapter test double."""
    def __init__(self, output=None):
        self.limits = ReadLimits()
        self.output = pages() if output is None else output
        self.events = []
        self.cut = object()
        self.closed = False
        self.revoked = False

    def capture_cut(self, source_id):
        self.events.append(('capture', source_id))
        return self.cut

    def query_product_origin_v1(self, root, cut, **kwargs):
        assert cut is self.cut
        # Delivered page/buffer must be gone before asking for the next page.
        frame = inspect.currentframe().f_back
        assert 'page' not in frame.f_locals and 'encoded' not in frame.f_locals
        self.events.append(('query', root.to_dict(), cut, kwargs))
        index = 0 if kwargs['cursor'] is None else 1
        result = self.output[index]
        if isinstance(result, BaseException):
            raise result
        return deepcopy(result)

    def final_recheck(self, source_ids):
        self.events.append(('recheck', source_ids))
        if self.revoked:
            raise RegistryReadSessionError('ACCESS_CHANGED', '/private/grant details')

    def close(self):
        self.events.append(('close',))
        self.closed = True


class Sink(io.BytesIO):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def write(self, data):
        self.events.append(('write', bytes(data)))
        return super().write(data)

    def flush(self):
        self.events.append(('flush',))
        super().flush()


def run(monkeypatch, root_file, session, extra=(), sink=None, entry=cli.main):
    if sink is None:
        sink = Sink(session.events)
    stderr = io.StringIO()
    opened = []
    def open_session(path):
        opened.append(path)
        return session
    monkeypatch.setattr(cli, 'open_read_host_session', open_session)
    monkeypatch.setattr(sys, 'stdout', SimpleNamespace(buffer=sink))
    monkeypatch.setattr(sys, 'stderr', stderr)
    argv = ['--read-host-config', '/private/HOST.json', '--root', str(root_file), *extra]
    if entry is rpnh_cli.main:
        argv.insert(0, 'origin')
    status = entry(argv)
    return status, sink, stderr.getvalue(), opened


@pytest.mark.parametrize('kind', ['resource', 'result'])
def test_strict_root_preserves_source_and_exact_reference(tmp_path, kind):
    path = tmp_path / 'root.json'
    value = root_document(kind)
    path.write_bytes(canonical_json(value))
    assert cli._load_root(path).to_dict() == value


@pytest.mark.parametrize('mutation', [
    lambda value: value.update(extra='secret'),
    lambda value: value['ref'].update(extra='secret'),
    lambda value: value.pop('source_id'),
    lambda value: value.update(source_id=' inferred '),
    lambda value: value.update(source_id=''),
    lambda value: value.update(source_id=42),
    lambda value: value.update(schema_version='latest'),
    lambda value: value.update(ref='resource:' + '1' * 32),
    lambda value: value['ref'].update(resource_version_id='latest'),
])
def test_unknown_or_inferred_root_rejected_before_open(root_file, monkeypatch, mutation):
    value = root_document()
    mutation(value)
    root_file.write_bytes(canonical_json(value))
    session = Session()
    status, sink, stderr, opened = run(monkeypatch, root_file, session)
    assert status == 2 and sink.getvalue() == b'' and opened == []
    assert stderr == 'rpnh origin: INVALID_ROOT\n'


@pytest.mark.parametrize('payload', [
    b'{"schema_version":"a","schema_version":"b"}',
    b'{"ref":{"resource_id":"a","resource_id":"b"}}',
    b'"latest"', b'[]', b'null', b'{"source_id":NaN}', b'\xff',
    b'\xef\xbb\xbf{}', b'{"source_id":"\\ud800"}', b'[' * 1000 + b']' * 1000,
])
def test_duplicate_malformed_and_noncanonical_json(root_file, monkeypatch, payload):
    root_file.write_bytes(payload)
    status, sink, stderr, opened = run(monkeypatch, root_file, Session())
    assert status == 2 and sink.getvalue() == b'' and opened == []
    assert stderr == 'rpnh origin: INVALID_ROOT\n'


@pytest.mark.parametrize('ref_update', [
    {'entity_type': 'resource_version/v1', 'logical_id': 'resource:' + '1' * 32,
     'version_id': 'resource_version:' + '2' * 32},
    {'logical_id': 'invocation:' + '1' * 32},
    {'version_id': 'operation_result:' + '2' * 32},
])
def test_generic_resource_or_wrong_result_id_kinds_rejected(root_file, ref_update):
    value = root_document('result')
    value['ref'].update(ref_update)
    root_file.write_bytes(canonical_json(value))
    with pytest.raises(RegistryReadSessionError) as error:
        cli._load_root(root_file)
    assert error.value.code == 'INVALID_ROOT'


def test_root_file_bound_and_nonregular_input(root_file, tmp_path):
    root_file.write_bytes(b' ' * (cli.MAX_ROOT_BYTES + 1))
    with pytest.raises(RegistryReadSessionError) as error:
        cli._load_root(root_file)
    assert error.value.code == 'LIMIT_EXCEEDED'
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    for path in (fifo, tmp_path, tmp_path / 'missing'):
        with pytest.raises(RegistryReadSessionError) as error:
            cli._load_root(path)
        assert error.value.code == 'INVALID_ROOT'


def test_root_read_is_capped_when_file_grows(root_file, monkeypatch):
    root_file.write_bytes(b' ' * (cli.MAX_ROOT_BYTES + 1))
    original = cli.os.fstat
    monkeypatch.setattr(cli.os, 'fstat', lambda fd: SimpleNamespace(
        st_mode=original(fd).st_mode, st_size=1))
    with pytest.raises(RegistryReadSessionError) as error:
        cli._load_root(root_file)
    assert error.value.code == 'LIMIT_EXCEEDED'


@pytest.mark.parametrize('extra,code', [
    (['--cursor', 'secret'], 'INVALID_ARGUMENTS'),
    (['--resume', 'secret'], 'INVALID_ARGUMENTS'),
    (['--catalog', 'secret'], 'INVALID_ARGUMENTS'),
    (['--include', 'arbitrary-secret'], 'INVALID_ARGUMENTS'),
    (['--include', 'claims'], 'INVALID_QUERY'),
    (['--include', 'producer_execution', '--include', 'producer_execution'], 'INVALID_QUERY'),
    (['--page-size', 'true'], 'INVALID_ARGUMENTS'),
    (['--page-size', '0'], 'INVALID_QUERY'),
    (['--page-size', '-1'], 'INVALID_QUERY'),
    (['--page-size', '101'], 'LIMIT_EXCEEDED'),
])
def test_invalid_arguments_never_open_or_echo_values(root_file, monkeypatch, extra, code):
    status, sink, stderr, opened = run(monkeypatch, root_file, Session(), extra)
    assert status == 2 and not sink.getvalue() and not opened
    assert stderr == 'rpnh origin: ' + code + '\n'


def test_effective_host_root_budget_precedes_capture(root_file, monkeypatch):
    session = Session()
    session.limits = replace(session.limits, max_query_bytes=16)
    status, sink, stderr, opened = run(monkeypatch, root_file, session)
    assert status == 2 and opened and session.closed and not sink.getvalue()
    assert session.events == [('close',)]
    assert stderr == 'rpnh origin: LIMIT_EXCEEDED\n'


def test_exact_ndjson_pages_one_session_cut_and_full_request(root_file, monkeypatch):
    session = Session()
    original = cli.canonical_json
    def encode(page):
        session.events.append(('serialize',))
        return original(page)
    monkeypatch.setattr(cli, 'canonical_json', encode)
    status, sink, stderr, opened = run(monkeypatch, root_file, session, ['--page-size', '1'])
    assert status == 0 and stderr == '' and len(opened) == 1 and session.closed
    assert sink.getvalue() == b''.join(canonical_json(page) + b'\n' for page in pages())
    queries = [event for event in session.events if event[0] == 'query']
    assert len(queries) == 2
    assert queries[0][3] == {'include': None, 'page_size': 1, 'cursor': None}
    assert queries[1][3] == {'include': None, 'page_size': 1, 'cursor': 'internal-cursor'}
    names = [event[0] for event in session.events]
    assert names == ['capture', 'query', 'serialize', 'recheck', 'write', 'write', 'flush',
                     'query', 'serialize', 'recheck', 'write', 'write', 'flush', 'close']
    assert [event[1] for event in session.events if event[0] == 'recheck'] == [('source-a',)] * 2


def test_explicit_include_and_effective_default_forwarded(root_file, monkeypatch):
    page = pages()[-1]
    session = Session([page])
    status, sink, stderr, _ = run(monkeypatch, root_file, session,
                                 ['--include', 'producer_execution'])
    assert status == 0 and stderr == ''
    assert next(e for e in session.events if e[0] == 'query')[3] == {
        'include': ['producer_execution'], 'page_size': None, 'cursor': None}


@pytest.mark.parametrize('after_page', [1, 2])
def test_access_loss_after_serialization_blocks_current_line(root_file, monkeypatch, after_page):
    session = Session()
    original = cli.canonical_json
    calls = []
    def encode(page):
        result = original(page)
        calls.append(True)
        if len(calls) == after_page:
            session.revoked = True
        return result
    monkeypatch.setattr(cli, 'canonical_json', encode)
    status, sink, stderr, _ = run(monkeypatch, root_file, session)
    assert status == 2 and session.closed
    assert sink.getvalue() == (b'' if after_page == 1 else canonical_json(pages()[0]) + b'\n')
    assert stderr == 'rpnh origin: ACCESS_CHANGED\n'


def test_transport_budget_precedes_serialization(root_file, monkeypatch):
    session = Session()
    session.limits = replace(session.limits, max_response_bytes=1)
    monkeypatch.setattr(cli, 'canonical_json', lambda _: pytest.fail('unbounded serialization'))
    status, sink, stderr, _ = run(monkeypatch, root_file, session)
    assert status == 2 and session.closed and sink.getvalue() == b''
    assert stderr == 'rpnh origin: LIMIT_EXCEEDED\n'
    assert ('recheck', ('source-a',)) in session.events


def test_current_authority_overrides_transport_error(root_file, monkeypatch):
    session = Session()
    def fail(*_):
        session.revoked = True
        raise ValueError('protected serialization detail')
    monkeypatch.setattr(cli, '_serialize_page', fail)
    status, sink, stderr, _ = run(monkeypatch, root_file, session)
    assert status == 2 and session.closed and not sink.getvalue()
    assert stderr == 'rpnh origin: ACCESS_CHANGED\n'


def test_size_measurement_matches_canonical_ascii_and_rejects_objects():
    value = {'unicode': '\x00\x1f\x7f\u0080中文\U0001f680"\\\n\r\t\b\f',
             'other': [None, True, False, -20, {'a': []}]}
    payload = canonical_json(value)
    assert cli._serialize_page(value, len(payload)) == payload
    with pytest.raises(RegistryReadSessionError) as error:
        cli._serialize_page(value, len(payload) - 1)
    assert error.value.code == 'LIMIT_EXCEEDED'
    class Hidden:
        def __str__(self):
            pytest.fail('protected repr')
    with pytest.raises(RegistryReadSessionError):
        cli._serialize_page({'hidden': Hidden()}, 1000)


@pytest.mark.parametrize('failure,expected', [
    (RegistryReadSessionError('NOT_DISCLOSED', 'secret path'), 'NOT_DISCLOSED'),
    (RegistryReadSessionError('secret-code', 'secret path'), 'READ_FAILED'),
    (ValueError('private exception'), 'READ_FAILED'),
    (OSError('private local path'), 'READ_FAILED'),
])
def test_later_error_preserves_authorized_prefix_and_safe_stderr(root_file, monkeypatch, failure, expected):
    session = Session([pages()[0], failure])
    status, sink, stderr, _ = run(monkeypatch, root_file, session, entry=rpnh_cli.main)
    assert status == 2 and session.closed
    assert sink.getvalue() == canonical_json(pages()[0]) + b'\n'
    assert stderr == 'rpnh origin: ' + expected + '\n'


@pytest.mark.parametrize('operation', ['write', 'flush', 'zero', 'none'])
def test_sink_failure_always_closes_and_does_not_continue(root_file, monkeypatch, operation):
    session = Session()
    class FailingSink(Sink):
        def write(self, data):
            if operation == 'write':
                raise BrokenPipeError('private sink detail')
            if operation == 'zero':
                return 0
            if operation == 'none':
                return None
            return super().write(data)
        def flush(self):
            if operation == 'flush':
                raise OSError('private sink detail')
            super().flush()
    sink = FailingSink(session.events)
    status, _, stderr, _ = run(monkeypatch, root_file, session, sink=sink)
    assert status == 2 and session.closed
    assert sum(event[0] == 'query' for event in session.events) == 1
    assert stderr == 'rpnh origin: OUTPUT_FAILED\n'


def test_partial_sink_writes_complete_exactly(root_file, monkeypatch):
    session = Session()
    class ShortSink(Sink):
        def write(self, data):
            return super().write(data[:7])
    sink = ShortSink(session.events)
    status, _, stderr, _ = run(monkeypatch, root_file, session, sink=sink)
    assert status == 0 and stderr == '' and session.closed
    assert sink.getvalue() == b''.join(canonical_json(page) + b'\n' for page in pages())


def test_close_and_open_errors_are_fixed_codes(root_file, monkeypatch):
    session = Session([pages()[-1]])
    def close():
        session.closed = True
        raise OSError('/private/close')
    session.close = close
    status, _, stderr, _ = run(monkeypatch, root_file, session)
    assert status == 2 and session.closed and stderr == 'rpnh origin: READ_FAILED\n'
    def fail_open(_):
        raise RegistryReadSessionError('INVALID_HOST_CONFIGURATION', '/private/HOST.json')
    monkeypatch.setattr(cli, 'open_read_host_session', fail_open)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', stderr)
    assert rpnh_cli.main(['origin', '--read-host-config', 'private', '--root', str(root_file)]) == 2
    assert stderr.getvalue() == 'rpnh origin: INVALID_HOST_CONFIGURATION\n'


def test_help_exposes_stream_contract(capsys):
    with pytest.raises(SystemExit) as error:
        rpnh_cli.main(['--help'])
    assert error.value.code == 0
    top = capsys.readouterr().out
    assert 'rpnh origin' in top and 'NDJSON' in top and 'incomplete' in top
    with pytest.raises(SystemExit) as error:
        rpnh_cli.main(['origin', '--help'])
    assert error.value.code == 0
    detail = ' '.join(capsys.readouterr().out.split())
    for phrase in ('NDJSON', 'terminal page', 'incomplete stream', 'producer_execution',
                   'start_inputs', 'claims', '64 KiB', 'duplicate/unknown', 'internal only'):
        assert phrase in detail


def test_closed_pipe_process_has_safe_stderr_and_closes(root_file, tmp_path):
    """Only an adapter fake in a subprocess; no second native task or model run."""
    source = Path(cli.__file__).resolve().parents[3]
    marker = tmp_path / 'closed'
    imports = tmp_path / 'imports.json'
    # -S ignores every editable .pth. Carry existing dependency directories but
    # force the selected checkout first and verify every product import twice.
    search = [str(source), *(value for value in sys.path if value and Path(value).exists())]
    script = f'''
import json, pathlib, sys
sys.path[:0] = {search!r}
from cpn import rpnh_cli
from cpn.rpnh.collaboration import origin_cli as cli
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits
root = pathlib.Path({str(source)!r})
def verify():
    rows = {{name: str(pathlib.Path(module.__file__).resolve()) for name, module in tuple(sys.modules.items())
            if name == 'cpn' or name.startswith('cpn.') if getattr(module, '__file__', None)}}
    assert all(pathlib.Path(value).is_relative_to(root) for value in rows.values()), rows
    return rows
before = verify()
class Reader:
    limits = ReadLimits()
    def capture_cut(self, source): return object()
    def query_product_origin_v1(self, *args, **kwargs): return {{'continuation': None, 'rows': []}}
    def final_recheck(self, sources): pass
    def close(self): pathlib.Path({str(marker)!r}).write_text('closed')
cli.open_read_host_session = lambda _: Reader()
status = rpnh_cli.main(['origin', '--read-host-config', 'HOST.json', '--root', {str(root_file)!r}])
pathlib.Path({str(imports)!r}).write_text(json.dumps({{'before': before, 'after': verify()}}))
raise SystemExit(status)
'''
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run([sys.executable, '-S', '-c', script], stdout=write_fd,
                                stderr=subprocess.PIPE, timeout=30, check=False)
    finally:
        os.close(write_fd)
    assert result.returncode == 2
    assert result.stderr == b'rpnh origin: OUTPUT_FAILED\n'
    assert marker.read_text() == 'closed'
    assert set(json.loads(imports.read_text())) == {'before', 'after'}
