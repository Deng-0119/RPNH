"""Real thread/start RPC with fake admission/session boundaries; no native client."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

from jsonschema import Draft7Validator
import pytest

import cpn.frontend.codex_app_server as frontend
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh.user_config import profile_for_path
from test_codex_compat import _FakeWebSocket, _reasoning_effort_profiles, _wire_effort_profiles


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("RPNH_CONFIG", str(home / "rpnh.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(home / "profiles"))
    def forbidden(*_args, **_kwargs):
        pytest.fail("selection test attempted provider, worker or subprocess execution")
    monkeypatch.setattr(frontend.subprocess, "Popen", forbidden)
    monkeypatch.setattr(frontend.TaskControl, "start", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)

    # Exercise the real handler/selection/readiness/response code, but do not
    # reserve an owner root or create a MainSession, Registry or worker.
    class FakeLease:
        def close(self):
            pass

    class FakeSession:
        def __init__(self, root, execution, *, owner_root_reserved):
            assert owner_root_reserved
            self.root = root
            self.child_path_root = root / "main"
            self.execution_config_path = execution

    monkeypatch.setattr(frontend.MainSessionOwnerLease, "reserve_for_creation",
                        lambda _root: FakeLease())
    monkeypatch.setattr(frontend, "MainSession", FakeSession)
    monkeypatch.setattr(frontend, "inspect_main_session_root", lambda _root: object())
    monkeypatch.setattr(frontend, "stable_frontend_session_id", lambda _record: "ses_" + "1" * 32)
    monkeypatch.setattr(frontend, "TaskControl", lambda _root: object())
    monkeypatch.setattr(frontend.MainSessionSourceBinding, "capture",
                        lambda _session: SimpleNamespace())
    monkeypatch.setattr(frontend.CodexAppServer, "_persist_thread", lambda _self, _state: None)


def _profiles(root):
    _reasoning_effort_profiles(root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    other = json.loads(json.dumps(catalog["providers"][0]["models"][0]))
    other.update(profile="other-model", model_condition="other-exact-model")
    other["reasoning_efforts"]["default"] = "low"
    catalog["providers"][0]["models"].append(other)
    path.write_text(json.dumps(catalog))
    build_provider_catalog(path, root / "generated")
    return root / "generated/execution"


def _start(server, params):
    socket = _FakeWebSocket([])
    server._initialized_connections.add(id(socket))
    asyncio.run(server._handle_request(socket, 1, "thread/start", params))
    response = next(message["result"] for message in socket.sent if message.get("id") == 1)
    assert not server.root.exists()
    return response, server._threads[response["thread"]["id"]]


def _official_params(params):
    # The stock request has config, not a top-level effort property.
    fixture = Path(__file__).parent / "fixtures/codex/0.155.0/ThreadStartParams.json"
    schema = json.loads(fixture.read_text())
    assert "effort" not in schema["properties"]
    assert schema["properties"]["config"]["type"] == ["object", "null"]
    Draft7Validator(schema).validate(params)


@pytest.mark.parametrize("selector", ["pinned-0.155.0", "candidate-0.161.0"])
def test_same_model_no_override_keeps_active_high(tmp_path, selector):
    profiles = _profiles(tmp_path / "profiles")
    active = profiles / "reasoning-model--effort-high.json"
    server = frontend.CodexAppServer(tmp_path / "session", active, compatibility_profile=selector)
    try:
        params = {"model": "reasoning-model"}
        _official_params(params)
        response, state = _start(server, params)
        assert response["reasoningEffort"] == "high"
        assert state.session.execution_config_path == active
        assert state.reasoning_effort == "high"
    finally:
        server.close()


@pytest.mark.parametrize("params,expected", [
    ({}, "reasoning-model--effort-high.json"),
    ({"model": None}, "reasoning-model--effort-high.json"),
    ({"model": "reasoning-model", "config": None}, "reasoning-model--effort-high.json"),
    ({"model": "reasoning-model", "config": {}}, "reasoning-model--effort-high.json"),
    ({"model": "reasoning-model", "config": {"model_reasoning_effort": None}}, "reasoning-model--effort-high.json"),
    ({"config": {"model_reasoning_effort": "high"}}, "reasoning-model--effort-high.json"),
    ({"model": "reasoning-model", "config": {"model_reasoning_effort": "high"}}, "reasoning-model--effort-high.json"),
    ({"model": "reasoning-model", "config": {"model_reasoning_effort": "medium"}}, "reasoning-model.json"),
    ({"model": "other-model"}, "other-model.json"),
    ({"model": "other-model", "config": {"model_reasoning_effort": None}}, "other-model.json"),
    ({"model": "other-model", "config": {"model_reasoning_effort": "medium"}}, "other-model--effort-medium.json"),
    ({"config": {"model_reasoning_effort": "low", "check_for_update_on_startup": False,
                 "model": "ignored-model", "approval_policy": "ignored-policy"}}, "reasoning-model--effort-low.json"),
])
def test_official_nested_selection_and_null_policy(tmp_path, params, expected):
    profiles = _profiles(tmp_path / "profiles")
    server = frontend.CodexAppServer(tmp_path / "session", profiles / "reasoning-model--effort-high.json")
    before = {path: path.read_bytes() for path in profiles.parent.rglob("*.json")}
    try:
        _official_params(params)
        response, state = _start(server, params)
        profile = profile_for_path(profiles / expected)
        assert state.session.execution_config_path == profile.path
        assert response["reasoningEffort"] == profile.reasoning_effort
        assert response["approvalPolicy"] == "never"
        assert response["modelProvider"] == "rpnh"
        assert server.default_reasoning_effort == "high"
        assert not (tmp_path / "home/rpnh.json").exists()
        assert {path: path.read_bytes() for path in before} == before
    finally:
        server.close()


@pytest.mark.parametrize("params,expected", [
    ({"effort": "low"}, "low"),
    ({"effort": None}, "high"),
    ({"effort": "low", "config": {"model_reasoning_effort": "low"}}, "low"),
    ({"effort": None, "config": {"model_reasoning_effort": "low"}}, "low"),
    ({"effort": "low", "config": {"model_reasoning_effort": None}}, "low"),
    ({"effort": None, "config": {"model_reasoning_effort": None}}, "high"),
])
def test_legacy_top_level_compatibility_is_narrow(tmp_path, params, expected):
    _, _, active = _reasoning_effort_profiles(tmp_path / "profiles")
    server = frontend.CodexAppServer(tmp_path / "session", active)
    try:
        response, state = _start(server, {"model": "reasoning-model", **params})
        assert response["reasoningEffort"] == state.reasoning_effort == expected
    finally:
        server.close()


BAD_PARAMS = [
    *({"config": value} for value in (False, True, [], "high", 0)),
    *({"config": {"model_reasoning_effort": value}} for value in (False, True, [], {}, 0, "", "unknown", "none")),
    *({"effort": value} for value in (False, True, [], {}, 0, "", "unknown", "none")),
    {"effort": "low", "config": {"model_reasoning_effort": "high"}},
    {"effort": "high", "config": {"model_reasoning_effort": False}},
    {"effort": False, "config": {"model_reasoning_effort": "high"}},
    *({"model": value} for value in (False, True, [], {}, "unknown-model")),
]


@pytest.mark.parametrize("params", BAD_PARAMS)
def test_invalid_selection_rejected_before_admission(tmp_path, monkeypatch, params):
    _, _, active = _reasoning_effort_profiles(tmp_path / "profiles")
    server = frontend.CodexAppServer(tmp_path / "session", active)
    config = tmp_path / "home/rpnh.json"
    config.write_text('{"synthetic":"unchanged"}')
    before = config.read_bytes()
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid start reached readiness, lease or session admission")
    monkeypatch.setattr(server, "_require_ready_profile", forbidden)
    monkeypatch.setattr(frontend.MainSessionOwnerLease, "reserve_for_creation", forbidden)
    monkeypatch.setattr(frontend, "MainSession", forbidden)
    socket = _FakeWebSocket([])
    try:
        with pytest.raises(ValueError):
            asyncio.run(server._start_thread(socket, 1, params))
        assert server._threads == {} and server._lease is None
        assert socket.sent == []
        assert not server.root.exists()
        assert config.read_bytes() == before
    finally:
        server.close()


@pytest.mark.parametrize("index,canonical", [(0, None), (1, "none")])
def test_official_none_token_preserves_model_scoped_identity(tmp_path, index, canonical):
    paths = _wire_effort_profiles(tmp_path / "profiles")
    execution = paths[index]
    server = frontend.CodexAppServer(tmp_path / "session", execution)
    profile = profile_for_path(execution)
    before = server._execution_identity(execution)
    try:
        params = {"model": profile.selection_id, "config": {"model_reasoning_effort": "none"}}
        _official_params(params)
        response, state = _start(server, params)
        assert response["reasoningEffort"] == "none"
        assert state.reasoning_effort == canonical
        assert state.session.execution_config_path == execution
        assert server._execution_identity(execution) == before
    finally:
        server.close()
