"""Official 0.166 schema candidate; no native binary, socket or model execution."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jsonschema import Draft7Validator
import pytest

from cpn.frontend.codex_app_server import CodexAppServer
from cpn.frontend.codex_history import public_id
from cpn.rpnh.main_session import MainSession
from test_codex_compat import _FakeWebSocket, _local_profile
from test_codex_history import _append, _request


SCHEMAS = Path(__file__).parent / "fixtures/codex/0.166.0"


def _validate(name, response):
    Draft7Validator(json.loads((SCHEMAS / f"{name}.json").read_text())).validate(response)


@pytest.fixture
def current(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    session = MainSession(tmp_path / "session", execution, task_control=object())
    for ordinal in (1, 2):
        _append(session._main_thread, monkeypatch, ordinal, private=True)
    server = CodexAppServer(session.root, execution, compatibility_profile="candidate-0.166.0")
    socket = _FakeWebSocket([])
    asyncio.run(server._handle_request(socket, 0, "initialize", {
        "clientInfo": {"name": "codex-tui", "version": "0.166.0"},
    }))
    _validate("InitializeResponse", socket.sent[-1]["result"])
    server._initialized_connections.add(id(socket))
    yield server, next(iter(server._threads.values())), socket
    server.close()


@pytest.mark.parametrize("method,schema", [
    ("model/list", "ModelListResponse"),
    ("hooks/list", "HooksListResponse"),
    ("skills/list", "SkillsListResponse"),
    ("apps/list", "AppsListResponse"),
    ("thread/loaded/list", "ThreadLoadedListResponse"),
    ("thread/list", "ThreadListResponse"),
    ("config/read", "ConfigReadResponse"),
    ("configRequirements/read", "ConfigRequirementsReadResponse"),
    ("permissionProfile/list", "PermissionProfileListResponse"),
    ("plugin/list", "PluginListResponse"),
    ("thread/resume", "ThreadResumeResponse"),
    ("thread/goal/get", "ThreadGoalGetResponse"),
    ("thread/read", "ThreadReadResponse"),
    ("thread/turns/list", "ThreadTurnsListResponse"),
    ("thread/items/list", "ThreadItemsListResponse"),
])
def test_current_discovery_and_committed_history_match_native_schema(current, method, schema):
    server, state, socket = current
    response = _request(server, state, socket, method)
    _validate(schema, response)
    assert "PRIVATE_" not in json.dumps(response)


def test_current_item_anchor_and_cold_resume_keep_committed_identity(current):
    server, state, socket = current
    params = {"threadId": state.thread_id,
              "turnId": public_id(state.thread_id, 1, "turn"),
              "cursor": {"type": "item", "itemId": public_id(state.thread_id, 1, "user")},
              "sortDirection": "asc"}
    _validate("ThreadItemsListParams", params)
    response = _request(server, state, socket, "thread/items/list", **params)
    _validate("ThreadItemsListResponse", response)
    assert [entry["item"]["id"] for entry in response["data"]] == [public_id(state.thread_id, 1, "agent")]
    thread_id = state.thread_id
    root, execution = state.session.root, server.execution_config_path
    server.close()
    reopened = CodexAppServer(root, execution, compatibility_profile="candidate-0.166.0")
    try:
        again = next(iter(reopened._threads.values()))
        reopened._initialized_connections.add(id(socket))
        resumed = _request(reopened, again, socket, "thread/resume")
        _validate("ThreadResumeResponse", resumed)
        assert resumed["thread"]["id"] == thread_id
        assert resumed["thread"]["cliVersion"] == "0.166.0"
        assert resumed["collaborationMode"] is None
        assert resumed["disabledPluginIds"] == []
        assert _request(reopened, again, socket, "thread/items/list", **params) == response
    finally:
        reopened.close()


def test_unsubscribe_detaches_only_client_notifications_and_resume_reattaches(current):
    server, state, socket = current
    other = _FakeWebSocket([])
    before = state.session.history_registry.capture_read_cut()
    response = _request(server, state, socket, "thread/unsubscribe")
    _validate("ThreadUnsubscribeResponse", response)
    assert response == {"status": "unsubscribed"}
    assert _request(server, state, socket, "thread/unsubscribe") == {"status": "notSubscribed"}
    event = {"threadId": state.thread_id, "status": {"type": "idle"}}
    socket.sent.clear()
    asyncio.run(server._notify(socket, "thread/status/changed", event))
    asyncio.run(server._notify(other, "thread/status/changed", event))
    assert not socket.sent
    assert other.sent == [{"method": "thread/status/changed", "params": event}]
    assert state.thread_id in server._threads
    assert state.session.history_registry.capture_read_cut() == before
    _request(server, state, socket, "thread/resume")
    socket.sent.clear()
    asyncio.run(server._notify(socket, "thread/status/changed", event))
    assert socket.sent == other.sent


def test_candidate_is_explicit_and_binary_gate_does_not_change_default(monkeypatch):
    from types import SimpleNamespace
    import cpn.frontend.codex_app_server as frontend
    import cpn.llm_adapters.codex_subscription_bridge as backend
    selected = []
    monkeypatch.setenv('RPNH_CODEX_BIN', '/selected/frontend-codex')
    monkeypatch.setenv('RPNH_CODEX_COMPATIBILITY_PROFILE', 'candidate-0.166.0')
    monkeypatch.setattr(frontend.shutil, 'which', lambda path: selected.append(path) or path)
    monkeypatch.setattr(frontend.subprocess, 'run', lambda argv, **kw:
                        SimpleNamespace(stdout='codex-cli 0.166.0\n', stderr='', returncode=0))
    assert frontend.CODEX_FRONTEND_VERSION == '0.163.0'
    with pytest.raises(frontend.CodexCompatibilityError, match='requires codex-cli 0.163.0'):
        frontend.resolve_codex_binary()
    assert frontend.resolve_codex_binary(compatibility_profile='candidate-0.166.0') == '/selected/frontend-codex'
    assert selected == ['/selected/frontend-codex', '/selected/frontend-codex']
    # The frontend selector is not a backend tool allowance.
    assert backend._ALLOWED_ITEM_TYPES == {'agent_message', 'reasoning'}
    argv = backend._codex_argv(codex_binary=Path('/configured/backend-codex'),
        model='owner-model', response_schema=Path('/workspace/schema.json'),
        working_directory=Path('/workspace'), reasoning_effort='medium',
        model_context_window=10000, verbosity='low', instructions_file=Path('/workspace/instructions'))
    assert argv[0] == '/configured/backend-codex'
    assert '--remote' not in argv
    assert argv[argv.index('--sandbox') + 1] == 'read-only'


@pytest.mark.parametrize('version', ['0.163.0', '0.166.0-alpha', 'latest'])
def test_candidate_rejects_wrong_handshake_without_history(current, version):
    server, state, _ = current
    before = state.session.history_registry.capture_read_cut()
    socket = _FakeWebSocket([
        {'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'codex-tui', 'version': version}}},
        {'method': 'initialized'},
        {'id': 2, 'method': 'thread/read', 'params': {'threadId': state.thread_id}},
    ])
    asyncio.run(server.handle(socket))
    assert all('result' not in value for value in socket.sent)
    assert state.session.history_registry.capture_read_cut() == before


def test_candidate_uninitialized_and_unsupported_requests_do_not_dispatch(current):
    server, state, _ = current
    before = state.session.history_registry.capture_read_cut()
    socket = _FakeWebSocket([
        {'id': 0, 'method': 'turn/start', 'params': {'threadId': state.thread_id,
                                                'input': [{'type': 'text', 'text': 'not dispatched'}]}},
        {'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'codex-tui', 'version': '0.166.0'}}},
        {'method': 'initialized'},
        *[{'id': index + 2, 'method': method, 'params': {}}
          for index, method in enumerate(('memory/read', 'process/spawn', 'mcpServer/call', 'thread/attachment/create'))],
    ])
    asyncio.run(server.handle(socket))
    replies = {row['id']: row for row in socket.sent if 'id' in row}
    assert 'successfully initialize' in replies[0]['error']['message']
    _validate('InitializeResponse', replies[1]['result'])
    for index in range(2, 6):
        assert replies[index]['error']['code'] == -32601
    assert state.session.history_registry.capture_read_cut() == before


def test_candidate_interrupt_terminal_race_matches_native_notification_schemas(tmp_path, monkeypatch):
    import test_codex_compat as prior
    original = CodexAppServer
    servers = []
    schemas = {
        'item/started': 'ItemStartedNotification',
        'item/agentMessage/delta': 'AgentMessageDeltaNotification',
        'item/completed': 'ItemCompletedNotification',
        'turn/completed': 'TurnCompletedNotification',
        'thread/status/changed': 'ThreadStatusChangedNotification',
    }
    observed = []

    def construct(*args, **kwargs):
        server = original(*args, **kwargs, compatibility_profile='candidate-0.166.0')
        servers.append(server)
        notify = server._notify

        async def checked(socket, method, params):
            _validate(schemas[method], params)
            observed.append(method)
            await notify(socket, method, params)
        server._notify = checked
        return server

    monkeypatch.setattr(prior, 'CodexAppServer', construct)
    try:
        # The existing fixture supplies a terminal reconciliation from fake
        # session/control objects after one stop, and rejects a stale turn.
        # Validate those emitted wire shapes, not an actual Registry race.
        prior.test_codex_interrupt_race_prefers_registry_terminal_evidence(tmp_path)
        assert set(observed) == set(schemas)
    finally:
        for server in servers:
            server.close()


def test_candidate_start_response_and_notification_match_native_schema(tmp_path):
    execution = _local_profile(tmp_path / 'profiles')
    server = CodexAppServer(tmp_path / 'session', execution,
                            compatibility_profile='candidate-0.166.0')
    socket = _FakeWebSocket([])
    params = {'model': 'local-process/test-model', 'cwd': str(tmp_path)}
    _validate('ThreadStartParams', params)
    try:
        asyncio.run(server._start_thread(socket, 'start', params))
        _validate('ThreadStartResponse', socket.sent[0]['result'])
        _validate('ThreadStartedNotification', socket.sent[1]['params'])
        assert socket.sent[0]['result']['thread']['cliVersion'] == '0.166.0'
        state = next(iter(server._threads.values()))
        assert state.session.history == []
    finally:
        server.close()


def test_candidate_turn_admission_schema_uses_existing_registry_once(tmp_path, monkeypatch):
    from types import SimpleNamespace
    execution = _local_profile(tmp_path / 'profiles')
    original = MainSession(tmp_path / 'session', execution, task_control=object())
    server = CodexAppServer(original.root, execution, compatibility_profile='candidate-0.166.0')
    state = next(iter(server._threads.values()))
    socket = _FakeWebSocket([])
    calls = []
    state.main_turn_control = SimpleNamespace(start=lambda spec: calls.append(spec) or SimpleNamespace(
        task_id='offline-unstarted-task', process=SimpleNamespace(poll=lambda: None)))
    monkeypatch.setattr(server, '_ensure_active_tracking', lambda *_args: None)
    params = {'threadId': state.thread_id,
              'input': [{'type': 'text', 'text': 'candidate once'}]}
    _validate('TurnStartParams', params)
    try:
        asyncio.run(server._start_turn(socket, 'turn', params))
        _validate('TurnStartResponse', socket.sent[0]['result'])
        _validate('ThreadStatusChangedNotification', socket.sent[1]['params'])
        _validate('TurnStartedNotification', socket.sent[2]['params'])
        assert state.session.active_turn_snapshot().user_text == 'candidate once'
        assert len(calls) == 1
        with pytest.raises(ValueError, match='already has an active turn'):
            asyncio.run(server._start_turn(socket, 'repeat', params))
        assert len(calls) == 1
    finally:
        server.close()
