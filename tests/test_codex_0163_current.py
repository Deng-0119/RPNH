"""Current native-generated protocol schemas against real RPNH history."""
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


SCHEMAS = Path(__file__).parent / "fixtures/codex/0.163.0"


def _validate(name, response):
    Draft7Validator(json.loads((SCHEMAS / f"{name}.json").read_text())).validate(response)


@pytest.fixture
def current(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    session = MainSession(tmp_path / "session", execution, task_control=object())
    for ordinal in (1, 2):
        _append(session._main_thread, monkeypatch, ordinal, private=True)
    server = CodexAppServer(session.root, execution, compatibility_profile="pinned-0.163.0")
    socket = _FakeWebSocket([])
    asyncio.run(server._handle_request(socket, 0, "initialize", {
        "clientInfo": {"name": "codex-tui", "version": "0.163.0"},
    }))
    _validate("InitializeResponse", socket.sent[-1]["result"])
    server._initialized_connections.add(id(socket))
    yield server, next(iter(server._threads.values())), socket
    server.close()


@pytest.mark.parametrize("method,schema", [
    ("model/list", "ModelListResponse"),
    ("account/bedrock/checkGovCloudRequirements", "BedrockCheckGovCloudRequirementsResponse"),
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
    reopened = CodexAppServer(root, execution, compatibility_profile="pinned-0.163.0")
    try:
        again = next(iter(reopened._threads.values()))
        reopened._initialized_connections.add(id(socket))
        resumed = _request(reopened, again, socket, "thread/resume")
        _validate("ThreadResumeResponse", resumed)
        assert resumed["thread"]["id"] == thread_id
        assert resumed["thread"]["cliVersion"] == "0.163.0"
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
