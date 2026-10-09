"""Independent read-boundary probes over synthetic Registries only."""
from __future__ import annotations
import asyncio
import base64
from dataclasses import replace
import json
import os
import sqlite3
from pathlib import Path
import pytest
from cpn.frontend.codex_history import HistoryCursor, PREFIX, public_id
from cpn.rpnh.registry.main_thread import MainThreadAuthorityError
from test_codex_history import owner, _request, _append, _walk


def _clone_db(core, target):
    with core.event_store.connect() as source, sqlite3.connect(target) as destination:
        source.backup(destination)


def test_source_binding_rejects_actual_event_store_path_rebinding(owner, tmp_path):
    server, state, socket, _ = owner
    clone = tmp_path / 'same-identity-other-database.sqlite3'
    _clone_db(state.session._registry_core, clone)
    state.session._registry_core.event_store.path = clone
    with pytest.raises(ValueError, match='binding is unavailable'):
        _request(server, state, socket, 'thread/items/list')
    assert not socket.sent


@pytest.mark.parametrize('change', ['source', 'database', 'lease', 'thread_state'])
def test_source_swap_while_waiting_for_send_lock_is_rejected(owner, tmp_path, change):
    server, state, socket, _ = owner
    async def scenario():
        lock = server._send_locks.setdefault(id(socket), asyncio.Lock())
        await lock.acquire()
        pending = asyncio.create_task(server._handle_request(socket, 101, 'thread/items/list', {'threadId':state.thread_id}))
        await asyncio.sleep(0)
        if change == 'source':
            state.session._registry_core.branch_id = 'replacement-source'
        elif change == 'database':
            old = state.session._registry_core.event_store.path
            clone = tmp_path / 'replacement.sqlite3'
            _clone_db(state.session._registry_core, clone)
            os.replace(clone, old)
        elif change == 'lease':
            server._lease.path.unlink()
            server._lease.path.write_text('replacement lease')
        else:
            server._threads[state.thread_id] = replace(state)
        lock.release()
        with pytest.raises(ValueError, match='binding is unavailable'):
            await pending
    asyncio.run(scenario())
    assert not socket.sent


def _token(raw):
    return PREFIX + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


@pytest.mark.parametrize('mutation', ['unknown_top', 'unknown_cut', 'unknown_anchor', 'bool_version', 'bool_ordinal', 'bool_index', 'deep', 'nan', 'future', 'foreign_event'])
def test_strict_cursor_extra_keys_types_depth_and_native_identity(owner, mutation):
    server, state, socket, _ = owner
    valid = _request(server, state, socket, 'thread/items/list', limit=1)['nextCursor']
    body = valid[len(PREFIX):]
    raw = base64.urlsafe_b64decode(body + '=' * (-len(body)%4))
    data = json.loads(raw)
    if mutation == 'unknown_top': data['extra'] = 'PRIVATE_SENTINEL'
    elif mutation == 'unknown_cut': data['cut']['extra'] = 'PRIVATE_SENTINEL'
    elif mutation == 'unknown_anchor': data['anchor']['extra'] = 'PRIVATE_SENTINEL'
    elif mutation == 'bool_version': data['version'] = True
    elif mutation == 'bool_ordinal': data['cut']['ordinal'] = True
    elif mutation == 'bool_index': data['anchor']['itemIndex'] = True
    elif mutation == 'deep':
        invalid = _token(b'['*1200+b'0'+b']'*1200)
    elif mutation == 'nan': data['anchor']['ordinal'] = float('nan')
    elif mutation == 'future': data['cut']['ordinal'] += 100000
    elif mutation == 'foreign_event': data['cut']['eventId'] = 'event:' + 'a'*32
    if mutation != 'deep': invalid = _token(json.dumps(data).encode('ascii'))
    with pytest.raises((ValueError, MainThreadAuthorityError)):
        _request(server, state, socket, 'thread/items/list', cursor=invalid)
    assert not socket.sent


def test_committed_hydration_denies_future_and_foreign_turn_and_duplicate_refs(owner, monkeypatch):
    server, state, socket, native = owner
    cut = native.capture_read_cut()
    existing = native.project_thread_at(cut).turns[0].turn_ref
    future = _append(native, monkeypatch, 5).turn_ref
    for refs in ((future,), (existing, existing), (cut.thread_ref,)):
        with pytest.raises((ValueError, MainThreadAuthorityError)):
            state.session.display_history_at(cut, refs)
    assert state.session.display_history_at(cut, (existing,))[0].user_text == 'question 1'


def test_source_binding_allows_profile_selection_metadata_change(owner):
    server, state, socket, native = owner
    path = state.session.root / 'execution_profile.json'
    original = path.read_bytes()
    try:
        path.write_text('{"profile_changed_by_owner":true}')
        assert len(_request(server, state, socket, 'thread/items/list')['data']) == 6
    finally:
        path.write_bytes(original)


def test_uninitialized_and_unknown_thread_cannot_deliver_body(owner):
    server, state, socket, native = owner
    server._initialized_connections.discard(id(socket))
    with pytest.raises(ValueError):
        _request(server,state,socket,'thread/items/list')
    assert not socket.sent
    server._initialized_connections.add(id(socket))
    with pytest.raises(ValueError):
        asyncio.run(server._handle_request(socket,1,'thread/items/list',{'threadId':'not-the-bound-thread'}))
    assert not socket.sent


def test_history_opens_only_query_only_connections(owner, monkeypatch):
    server, state, socket, _ = owner
    backend = state.session._registry_core.event_store
    original = sqlite3.connect
    opened = []
    def observe_connection(*args, **kwargs):
        connection = original(*args, **kwargs)
        # The URI must open the engine read-only; query_only is set next by the
        # native read-only EventStore path and is separately covered by code.
        opened.append((str(args[0]), kwargs.get('uri', False)))
        return connection
    monkeypatch.setattr(sqlite3, 'connect', observe_connection)
    _request(server, state, socket, 'thread/items/list', limit=1)
    assert opened, 'fixture did not exercise Registry connections'
    writable = [(path, uri) for path, uri in opened if not uri or 'mode=ro' not in path]
    assert not writable, f'history opened {len(writable)} writable connections: {writable[:3]}'


def test_workflow_instructions_and_plugin_configuration_never_reach_safe_wire(owner, monkeypatch):
    from cpn.rpnh.main_session import MainSession, parse_main_decision
    from test_main_thread_registry import _install_observation
    server, state, socket, native = owner
    cut = native.capture_read_cut()
    decision = parse_main_decision({
        'reply':'PUBLIC_GRAPH_REPLY',
        'task':{'kind':'workflow','prompt':'SECRET_GRAPH_PROMPT', 'graph':{
            'nodes':[{'node_id':'worker','instruction':'SECRET_GRAPH_INSTRUCTION',
                      'input_ports':[{'port_id':'request','artifact_id':'task'}],
                      'output_ports':[{'port_id':'result','artifact_id':'result'}]}],
            'arcs':[], 'ingress':{'node_id':'worker','port_id':'request'},
            'egress':{'node_id':'worker','port_id':'result'}, 'max_rework_cycles':0}}})
    accepted = native.accept_turn(thread_ref=cut.thread_ref, expected_ordinal=5, idempotency_key='workflow-accept',
        user_input={'text':'PUBLIC_GRAPH_QUESTION\n完整原文', 'required_task_kind':'workflow',
                    'native_plugins':{'configuration':{'secret':'SECRET_PLUGIN_CONFIG'},'catalog_digest':'SECRET_CATALOG'}})
    running = native.attach_attempt(thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref, idempotency_key='workflow-running')
    _install_observation(monkeypatch,native,'terminal')
    receipt = native.record_execution_receipt(turn_ref=running.turn_ref,idempotency_key='workflow-receipt')
    native.commit_terminal_answer(thread_ref=running.thread_ref,turn_ref=running.turn_ref,receipt_ref=receipt,
        idempotency_key='workflow-commit',answer=MainSession._decision_document(decision))
    for query, params in [('items',{'turnId':public_id(state.thread_id,5,'turn')}),
                          ('turns',{'itemsView':'full','limit':1}),
                          ('turns',{'itemsView':'summary','limit':1})]:
        result = _request(server,state,socket,f'thread/{query}/list',**params)
        wire = json.dumps(result,ensure_ascii=False)
        assert 'PUBLIC_GRAPH_REPLY' in wire and 'PUBLIC_GRAPH_QUESTION' in wire and '完整原文' in wire
        assert 'SECRET_' not in wire
        assert 'workflow_graph' not in wire and 'instruction' not in wire and 'native_plugins' not in wire


def test_active_committed_wal_remains_visible_and_authority_files_unchanged(owner, monkeypatch):
    import gc
    import hashlib
    server, state, socket, native = owner
    writer = native.core.event_store
    reader = state.session.history_registry.core.event_store
    # Keep the fixture writer lifetime explicit. This is setup, not a read-side
    # checkpoint, and history must see the subsequent committed WAL append.
    keeper = writer.connect()
    try:
        _append(native, monkeypatch, 5, text='VISIBLE_COMMITTED_WAL')
        gc.collect()
        db = writer.path
        wal = Path(str(db)+'-wal')
        assert wal.is_file() and wal.stat().st_size > 0
        def fingerprint():
            return {str(path.relative_to(state.session.root)):hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in state.session.root.rglob('*') if path.is_file() and not path.name.endswith('-shm')}
        head_before = (reader.max_ordinal(),reader.writer_epoch)
        files_before = fingerprint()
        def forbidden(*a,**kw):
            pytest.fail('history opened the fixture writer or began a write')
        monkeypatch.setattr(writer,'connect',forbidden)
        monkeypatch.setattr(native.core,'begin',forbidden)
        result = _request(server,state,socket,'thread/items/list',turnId=public_id(state.thread_id,5,'turn'))
        assert 'VISIBLE_COMMITTED_WAL' in json.dumps(result)
        assert fingerprint() == files_before
        assert (reader.max_ordinal(),reader.writer_epoch) == head_before
    finally:
        keeper.close()
