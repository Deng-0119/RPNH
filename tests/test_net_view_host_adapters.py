"""Real main Registry tests for optional selectors, never real model calls."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cpn.frontend.server import handle_request
from cpn.frontend.net_view_adapters import _registry as bindings
from cpn.frontend.net_view_adapters.codex import bind_codex, main as codex_main
from cpn.frontend.net_view_adapters.dsh import bind_dsh, main as dsh_main
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('net_viewer_fixture', ROOT / 'scripts/net_viewer_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture(scope='module')
def real_run(tmp_path_factory):
    path = tmp_path_factory.mktemp('native') / 'run'
    _, _, snapshot, catalog = fixture.make_run(path)
    return path, snapshot, catalog


@pytest.fixture
def host_run(tmp_path, real_run):
    template, snapshot, catalog = real_run
    root = tmp_path / 'host'
    session = root / 'session-a'
    session.mkdir(parents=True)
    # The same registered session shape is used under both host paths.
    codex_session = root / 'threads' / 'thread-a'
    codex_session.parent.mkdir()
    service = MainThreadRegistry(_RegistryCore(session / 'main', create=True), session_root=session)
    thread = service.create_thread(idempotency_key='fixture')
    accepted = service.accept_turn(thread_ref=thread, user_input={'session_id': 'session-a', 'request_id': 'request-a'}, idempotency_key='turn')
    attempt = service.attach_attempt(thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref, idempotency_key='attempt')
    shutil.copytree(template, attempt.attempt_path, dirs_exist_ok=True)
    shutil.copytree(session, codex_session)
    return root, service, attempt, catalog, snapshot


@pytest.mark.parametrize('selector', ['latest', 'active', 1, '1'])
def test_codex_selects_registered_turn(host_run, selector):
    root, _, _, catalog, expected = host_run
    bound = bind_codex(root, 'thread-a', turn=selector, catalog=catalog)
    view = bound()
    assert view['nodes'] == expected['nodes']
    assert view['edges'] == expected['edges']
    assert bound.describe()['selection_kind'] == 'main_turn'
    assert 'threads/thread-a/attempts/' in str(bound.run_dir)


@pytest.mark.parametrize('kwargs', [{'turn': 'latest'}, {'turn': 'active'}, {'turn': 1}, {'request_id': 'request-a'}])
def test_dsh_selects_registered_request(host_run, kwargs):
    root, _, _, catalog, expected = host_run
    bound = bind_dsh(root, 'session-a', catalog=catalog, **kwargs)
    assert bound()['marking'] == expected['marking']
    assert bound.describe()['host'] == 'dsh'


def test_reading_and_http_never_begin_transaction(host_run, monkeypatch):
    root, service, attempt, catalog, _ = host_run
    before = service.core.event_store.max_ordinal()
    def forbidden(*args, **kwargs):
        raise AssertionError('viewer attempted a Registry transaction')
    monkeypatch.setattr(_RegistryCore, 'begin', forbidden)
    bound = bind_dsh(root, 'session-a', catalog=catalog)
    child = _RegistryCore(attempt.attempt_path, create=False, read_only=True, catalog=catalog)
    child_before = child.event_store.max_ordinal()
    assert handle_request(bound, 'GET', '/api/v1/net').status == 200
    assert handle_request(bound, 'POST', '/api/v1/net').status == 405
    assert service.core.event_store.max_ordinal() == before
    assert child.event_store.max_ordinal() == child_before


def test_codex_child_task_requires_registration(host_run, real_run):
    root, _, _, catalog, _ = host_run
    session = root / 'threads/thread-a'
    service = MainThreadRegistry(_RegistryCore(session / 'main', create=False), session_root=session)
    link = service.register_child_registry_link(task_control_id='task-one', task_kind='single_agent',
        registry_relative_path='tasks/task-one', origin_main_turn_ref=None, idempotency_key='link')
    shutil.copytree(real_run[0], link.registry_path)
    service.attach_child_registry_link(task_control_id='task-one', idempotency_key='attach')
    before = service.core.event_store.max_ordinal()
    bound = bind_codex(root, 'thread-a', task_id='task-one', catalog=catalog)
    assert bound.run_dir == link.registry_path
    assert bound.describe()['selection_kind'] == 'child_task'
    assert bound()['schema_version'] == 'rpnh/net_view/v1'
    assert service.core.event_store.max_ordinal() == before
    with pytest.raises(ValueError, match='not uniquely registered'):
        bind_codex(root, 'thread-a', task_id='task-missing', catalog=catalog)


@pytest.mark.parametrize('turn', [0, -1, True, '0', 'nonsense', 2])
def test_invalid_or_missing_turn_does_not_fallback(host_run, turn):
    root, _, _, catalog, _ = host_run
    with pytest.raises(ValueError):
        bind_dsh(root, 'session-a', turn=turn, catalog=catalog)


@pytest.mark.parametrize('identity', ['..', '../else', 'a/b', 'a\\b', ''])
def test_invalid_host_identity_does_not_create_directories(tmp_path, identity):
    before = sorted(tmp_path.rglob('*'))
    with pytest.raises((ValueError, FileNotFoundError)):
        bind_dsh(tmp_path, identity)
    assert sorted(tmp_path.rglob('*')) == before


def test_symlink_registered_attempt_is_rejected(host_run, tmp_path):
    root, _, attempt, catalog, _ = host_run
    target = tmp_path / 'moved'
    shutil.move(attempt.attempt_path, target)
    attempt.attempt_path.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        bind_dsh(root, 'session-a', catalog=catalog)


def test_pending_latest_is_not_replaced_by_old_turn(host_run):
    root, service, attempt, catalog, _ = host_run
    old = bind_dsh(root, 'session-a', catalog=catalog)
    receipt = service.record_execution_receipt(turn_ref=attempt.turn_ref, idempotency_key='receipt')
    completed = service.commit_terminal_answer(thread_ref=attempt.thread_ref, turn_ref=attempt.turn_ref,
        receipt_ref=receipt, answer={'text': 'done'}, idempotency_key='answer')
    service.accept_turn(thread_ref=completed.thread_ref,
        user_input={'session_id': 'session-a', 'request_id': 'request-b'}, idempotency_key='next')
    assert old()['source']['run_dir'] == str(old.run_dir)
    with pytest.raises(ValueError, match='no attempt yet'):
        bind_dsh(root, 'session-a', catalog=catalog)
    assert bind_dsh(root, 'session-a', request_id='request-a', catalog=catalog).run_ref == old.run_ref


def test_binding_detects_replaced_native_identity(host_run, monkeypatch):
    root, _, _, catalog, _ = host_run
    bound = bind_dsh(root, 'session-a', catalog=catalog)
    monkeypatch.setattr(bindings, '_identity', lambda *a: (bound.task_ref, None))
    with pytest.raises(ValueError, match='identity changed'):
        bound()


def test_binding_rejects_continuously_changing_child_head(host_run, monkeypatch):
    root, _, _, catalog, _ = host_run
    bound = bind_dsh(root, 'session-a', catalog=catalog)
    count = iter(range(100))
    monkeypatch.setattr(bindings, '_head', lambda *a: (next(count), 0))
    with pytest.raises(ValueError, match='changed during projection'):
        bound()


def test_dsh_session_mismatch_and_selector_conflict(host_run):
    root, _, _, catalog, _ = host_run
    shutil.copytree(root / 'session-a', root / 'session-b')
    with pytest.raises(ValueError, match='different DSH session'):
        bind_dsh(root, 'session-b', catalog=catalog)
    with pytest.raises(ValueError, match='either a turn'):
        bind_dsh(root, 'session-a', turn=1, request_id='request-a', catalog=catalog)
    with pytest.raises(ValueError, match='missing or ambiguous'):
        bind_dsh(root, 'session-a', request_id='missing', catalog=catalog)


def test_companion_cli_outputs_and_never_starts_view_for_json(host_run, capsys, monkeypatch):
    root, _, _, _, _ = host_run
    from cpn.frontend.net_view_adapters import _cli
    monkeypatch.setattr(_cli, 'serve_projection', lambda *a, **k: pytest.fail('unexpected HTTP start'))
    assert dsh_main(['--root', str(root), '--session-id', 'session-a', '--describe']) == 0
    assert json.loads(capsys.readouterr().out)['host'] == 'dsh'
    assert codex_main(['--root', str(root), '--thread-id', 'thread-a', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['schema_version'] == 'rpnh/net_view/v1'
    with pytest.raises(SystemExit) as exc:
        dsh_main(['--root', str(root), '--session-id', 'missing', '--json'])
    assert exc.value.code == 2


def test_companion_view_delegates_to_shared_server(host_run, monkeypatch):
    root, _, _, _, _ = host_run
    from cpn.frontend.net_view_adapters import _cli
    seen = {}
    def capture(provider, **kwargs):
        seen.update(kwargs)
        assert provider()['source']['mode'] == 'registry_current'
        return 0
    monkeypatch.setattr(_cli, 'serve_projection', capture)
    assert dsh_main(['--root', str(root), '--session-id', 'session-a', '--view', '--no-open', '--show-resources']) == 0
    assert seen == {'port': 0, 'open_browser': False, 'show_resources': True}


def test_fresh_import_does_not_load_execution_hosts():
    code = """import sys
from cpn.frontend.net_view_adapters.codex import bind_codex
from cpn.frontend.net_view_adapters.dsh import bind_dsh
assert not any(name in sys.modules for name in ('cpn.dsh.backend', 'cpn.frontend.codex_app_server', 'cpn.rpnh.main_session', 'cpn.rpnh.task_control'))
"""
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)


@pytest.mark.parametrize('session_id', ['_session', '-session', 's1'])
def test_dsh_accepts_backend_identifier_alphabet(tmp_path, monkeypatch, session_id):
    from cpn.frontend.net_view_adapters import dsh
    (tmp_path / session_id).mkdir()
    selected = {}
    def capture(path, **kwargs):
        selected.update(kwargs)
        assert path == tmp_path / session_id
        return 'bound'
    monkeypatch.setattr(dsh, 'bind_session', capture)
    assert dsh.bind_dsh(tmp_path, session_id) == 'bound'
    assert selected['session_id'] == session_id
