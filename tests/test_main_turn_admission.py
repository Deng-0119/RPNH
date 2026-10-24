"""One Registry admission contract across native hosts; no worker/provider runs.

These tests exercise the real MainSession and shared application, with only
plugin discovery and worker dispatch replaced by deterministic test fixtures.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import threading

import pytest

from cpn.frontend.codex_app_server import CodexAppServer
from cpn.frontend.dsh_rpc import DshFrontendRPC, PROTOCOL
from cpn.frontend.opencode_protocol import OpenCodeProtocol
from cpn.plugins import catalog as plugins
from cpn.plugins.api import json_copy
from cpn.rpnh.frontend_application import (
    FrontendError, FrontendGateway, RegistryFrontendApplication,
)
from cpn.rpnh.main_session import MainSession, MainSessionPaused
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry
from cpn.rpnh.task_control import TaskControl
from test_codex_compat import _FakeWebSocket, _reasoning_effort_profiles
from test_main_session_registry import (
    _install_child_observation, _write_execution_profile,
)
from test_native_plugins import configuration, fixture_plugin
from test_opencode_registry_integration import CaptureDispatch


@pytest.fixture
def selected_plugins(monkeypatch):
    state = NS(configuration=configuration(), reads=0)
    original_load = plugins.load_catalog

    def read_config():
        state.reads += 1
        return deepcopy(state.configuration)

    def load_catalog(document):
        return original_load(document, factories={"test": fixture_plugin})

    def forbidden(*_args, **_kwargs):
        pytest.fail("admission/observation must not execute a worker or provider")

    monkeypatch.setattr(plugins, "read_config", read_config)
    monkeypatch.setattr(plugins, "load_catalog", load_catalog)
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", forbidden)
    monkeypatch.setattr(TaskControl, "_spawn", forbidden)
    return state


def _gateway(app):
    return NS(call=lambda method, *args, **kwargs: getattr(app, method)(*args, **kwargs))


def _rpc_frame(method, params=None):
    return {"protocol": PROTOCOL, "id": "transport-id", "method": method,
            "params": params or {}}


def _accepted(session):
    lineage = session._main_thread._turn_lineages(session._thread_ref.entity_id)[0]
    ref, document = lineage[0]
    event = session._registry_core.event_store.object_publication_event(
        ref.version_id, object_type=ref.entity_type)
    return ref, document, json.loads(event.command_id)


def _new_app(tmp_path, execution=None):
    execution = execution or _write_execution_profile(tmp_path)
    app = RegistryFrontendApplication(tmp_path / "session", execution)
    sid = app.create_session()
    control = CaptureDispatch()
    app._sessions[sid].main_control = control
    return app, sid, control, execution


@pytest.mark.parametrize("entry", ["basic", "codex", "opencode", "dsh"])
def test_first_turn_has_same_registered_plugins_and_selected_effort(
        tmp_path, monkeypatch, selected_plugins, entry):
    _low, _medium, high = _reasoning_effort_profiles(tmp_path / "profiles")
    root = tmp_path / "session"
    owner = None
    if entry == "basic":
        session = MainSession(root, high)
        spec = session.prepare_turn("same user intent")
    elif entry == "codex":
        owner = CodexAppServer(root, high)
        monkeypatch.setattr(owner, "_ensure_active_tracking", lambda *_args: None)
        socket = _FakeWebSocket([])
        asyncio.run(owner._start_thread(socket, 1, {}))
        state = next(iter(owner._threads.values()))
        control = CaptureDispatch()
        state.main_turn_control = control
        asyncio.run(owner._start_turn(socket, 2, {
            "threadId": state.thread_id,
            "input": [{"type": "text", "text": "same user intent"}],
        }))
        session, spec = state.session, control.spec
        assert control.starts == 1
    else:
        owner, sid, control, _ = _new_app(tmp_path, high)
        gateway = _gateway(owner)
        if entry == "opencode":
            reply = OpenCodeProtocol(gateway, str(tmp_path)).route(
                "POST", f"/session/{sid}/prompt_async", {
                    "parts": [{"type": "text", "text": "same user intent"}],
                    "messageID": "same-request",
                })
            assert reply.status == 204
        else:
            reply = DshFrontendRPC(gateway, sid).handle(_rpc_frame("submit", {
                "text": "same user intent", "requestId": "same-request",
            }))
            assert reply["result"] == {"ordinal": 1}
        session, spec = owner._sessions[sid].session, control.spec
        assert control.starts == 1
    try:
        _ref, accepted, command = _accepted(session)
        native = accepted["user_input"]["native_plugins"]
        assert accepted["user_input"]["text"] == "same user intent"
        assert native["configuration"] == selected_plugins.configuration
        assert json_copy(spec.plugin_configuration) == native["configuration"]
        assert spec.plugin_catalog_digest == native["catalog_digest"]
        assert "test_plugin/add" in spec.prompt
        assert spec.execution_config_path == high
        profile = session._persisted_execution_profile(root)
        assert profile["selection_id"] == "reasoning-model"
        assert profile["reasoning_effort"] == "high"
        assert selected_plugins.reads == 1
        if entry in {"opencode", "dsh"}:
            assert json.loads(command["caller_idempotency_key"].split(":", 1)[1]) == {
                "key": "explicit:same-request", "selection": "reasoning-model",
                "reasoning_effort": "high",
            }
    finally:
        if owner is not None:
            owner.close()


@pytest.mark.parametrize("failure_stage", ["attach", "start"])
def test_retry_after_acceptance_uses_original_snapshot_and_receipt(
        tmp_path, monkeypatch, selected_plugins, failure_stage):
    app, sid, control, _ = _new_app(tmp_path)
    session = app._sessions[sid].session
    target = session._main_thread if failure_stage == "attach" else control
    method = "attach_attempt" if failure_stage == "attach" else "start"
    original = getattr(target, method)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected failure after acceptance")

    try:
        monkeypatch.setattr(target, method, fail)
        with pytest.raises(RuntimeError, match="injected failure"):
            app.submit(sid, "keep registered capability", "explicit:one")
        before = _accepted(session)
        assert selected_plugins.reads == 1
        selected_plugins.configuration["plugins"][0]["config"]["offset"] = 99
        monkeypatch.setattr(target, method, original)
        assert app.submit(sid, "keep registered capability", "explicit:one") == 1
        assert app.submit(sid, "keep registered capability", "explicit:one") == 1
        assert _accepted(session) == before
        assert selected_plugins.reads == 1
        assert control.spec.plugin_configuration["plugins"][0]["config"]["offset"] == 2
        assert control.starts == 1
        assert "_acceptance_key" not in app.snapshot()[0]["turns"][0]
        with pytest.raises(FrontendError, match="different input"):
            app.submit(sid, "different", "explicit:one")
        with pytest.raises(FrontendError, match="different input"):
            app.submit(sid, "keep registered capability", "explicit:one", kind="workflow")
        with pytest.raises(FrontendError, match="active or paused"):
            app.submit(sid, "keep registered capability", "explicit:new")
    finally:
        app.close()


@pytest.mark.parametrize("legacy", ["v2", "v3"])
def test_legacy_accepted_turn_reuses_exact_key_and_empty_capability(
        tmp_path, selected_plugins, legacy):
    app, sid, control, _ = _new_app(tmp_path)
    session = app._sessions[sid].session
    profile = app.profiles[0]
    identity = {"key": "explicit:legacy", "selection": profile.selection_id}
    if legacy == "v3":
        identity["reasoning_effort"] = profile.reasoning_effort
    # Whitespace is intentional: recovery must reuse original identity bytes.
    key = f"frontend-request/{legacy}:" + json.dumps(identity)
    session._main_thread.accept_turn(
        thread_ref=session._thread_ref, expected_ordinal=1,
        user_input={"text": "legacy request", "required_task_kind": None},
        idempotency_key=key)
    before = _accepted(session)
    try:
        assert app.submit(sid, "legacy request", "explicit:legacy") == 1
        assert _accepted(session) == before
        assert control.spec.plugin_configuration is None
        assert selected_plugins.reads == 0
        assert control.starts == 1
    finally:
        app.close()


@pytest.mark.parametrize("attach", [False, True])
def test_common_prepare_rejects_different_active_request_identity(
        tmp_path, monkeypatch, selected_plugins, attach):
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    original = session._main_thread.attach_attempt
    if not attach:
        monkeypatch.setattr(session._main_thread, "attach_attempt",
                            lambda **_kw: (_ for _ in ()).throw(RuntimeError("stop")))
        with pytest.raises(RuntimeError, match="stop"):
            session.prepare_turn("same text", idempotency_key="request:original")
        monkeypatch.setattr(session._main_thread, "attach_attempt", original)
    else:
        session.prepare_turn("same text", idempotency_key="request:original")
    before = session._main_thread.recover_thread()
    with pytest.raises(ValueError, match="request identity differs"):
        session.prepare_turn("same text", idempotency_key="request:new")
    assert session._main_thread.recover_thread() == before
    assert selected_plugins.reads == 1
    session.prepare_turn("same text", idempotency_key="request:original")
    assert selected_plugins.reads == 1


def test_registered_catalog_drift_fails_closed_without_worker(
        tmp_path, monkeypatch, selected_plugins):
    app, sid, control, _ = _new_app(tmp_path)
    session = app._sessions[sid].session
    original = control.start
    monkeypatch.setattr(control, "start", lambda _spec: (_ for _ in ()).throw(RuntimeError("stop")))
    try:
        with pytest.raises(RuntimeError, match="stop"):
            app.submit(sid, "registered plugin", "explicit:one")
        before = _accepted(session)
        monkeypatch.setattr(control, "start", original)
        real_load = plugins.load_catalog
        monkeypatch.setattr(plugins, "load_catalog", lambda value: NS(digest="changed-installed-code"))
        with pytest.raises(ValueError, match="installed plugins differ"):
            app.submit(sid, "registered plugin", "explicit:one")
        assert _accepted(session) == before
        assert selected_plugins.reads == 1
        assert control.starts == 0
        monkeypatch.setattr(plugins, "load_catalog", real_load)
        assert app.submit(sid, "registered plugin", "explicit:one") == 1
    finally:
        app.close()


@pytest.mark.parametrize("outcome", ["stopped_by_owner", "terminal"])
def test_foreign_or_paused_turn_and_reopen_are_observational(
        tmp_path, monkeypatch, selected_plugins, outcome):
    app, sid, control, execution = _new_app(tmp_path)
    try:
        app.submit(sid, "original request", "explicit:one")
        session = app._sessions[sid].session
        # Existing fixture supplies child observations; Registry remains real.
        _RegistryCore(control.spec.run_dir, create=True)
        _install_child_observation(monkeypatch, session, outcome)
        if outcome == "terminal":
            ref, turn = session._active_turn()
            observed = session._main_thread._inspect_child_registry(
                turn_ref=ref, turn=turn)
            result = session._version_ref(observed["terminal_result_ref"])
            original_read = MainThreadRegistry._read_registered_bytes

            def read_fixture_result(registry, core, requested, **kwargs):
                if requested == result:
                    assert core.run_dir == control.spec.run_dir
                    assert kwargs["expected_type"] == "resource_version/v1"
                    return None, b'{"reply":"offline fixture answer","task":null}'
                return original_read(registry, core, requested, **kwargs)

            monkeypatch.setattr(MainThreadRegistry, "_read_registered_bytes",
                                read_fixture_result)
        app._sessions[sid].owned_main_turn_task_ids.clear()
        before = _accepted(session)
        selected_plugins.configuration = {"invalid": "current discovery must not run"}
        assert app.submit(sid, "original request", "explicit:one") == 1
        if outcome == "stopped_by_owner":
            with pytest.raises(MainSessionPaused, match="paused"):
                session.prepare_turn("original request")
        with pytest.raises(FrontendError, match="active or paused"):
            app.submit(sid, "new request", "explicit:new")
        app.tick()
        assert _accepted(session) == before
        assert control.starts == selected_plugins.reads == 1
        with pytest.raises(FrontendError, match="writable owner"):
            RegistryFrontendApplication(app.root, execution, resume=True)
        root = app.root
    finally:
        app.close()
    # Preserve the same observational child fixture when reopening the parent.
    observation = session._main_thread._inspect_child_registry
    monkeypatch.setattr(MainThreadRegistry, "_inspect_child_registry",
                        lambda _self, **kw: observation(**kw))
    reopened = RegistryFrontendApplication(root, execution, resume=True)
    try:
        forbidden = CaptureDispatch()
        reopened._sessions[sid].main_control = forbidden
        assert reopened.snapshot()[0]["turns"][0]["state"] == outcome
        rpc = DshFrontendRPC(_gateway(reopened), sid)
        assert "result" in rpc.handle(_rpc_frame("initialize"))
        assert OpenCodeProtocol(_gateway(reopened), str(tmp_path)).route(
            "GET", f"/session/{sid}/message").status == 200
        assert reopened.submit(sid, "original request", "explicit:one") == 1
        assert forbidden.starts == 0
        assert selected_plugins.reads == 1
        assert _accepted(reopened._sessions[sid].session) == before
    finally:
        reopened.close()


def test_duplicate_profile_or_effort_conflict_does_not_recapture(
        tmp_path, selected_plugins):
    low, medium, high = _reasoning_effort_profiles(tmp_path / "profiles")
    app, sid, control, _ = _new_app(tmp_path, high)
    try:
        app.submit(sid, "same text", "explicit:one")
        with pytest.raises(FrontendError, match="different input"):
            app.submit(sid, "same text", "explicit:one",
                       selection_id="reasoning-model", reasoning_effort="low")
        before = _accepted(app._sessions[sid].session)
        selection = json.loads(high.read_text())
        selection["timeout_seconds"] += 1
        high.write_text(json.dumps(selection))
        with pytest.raises(FrontendError, match="profile drift"):
            app.submit(sid, "same text", "explicit:one")
        assert _accepted(app._sessions[sid].session) == before
        assert control.starts == selected_plugins.reads == 1
    finally:
        app.close()


@pytest.mark.parametrize("native", [None, {"invalid": "configuration"}])
def test_empty_or_invalid_current_plugins_do_not_gain_capabilities(
        tmp_path, selected_plugins, native):
    selected_plugins.configuration = native
    app, sid, control, _ = _new_app(tmp_path)
    try:
        if native is None:
            assert app.submit(sid, "no plugins selected", "explicit:one") == 1
            assert control.spec.plugin_configuration is None
            assert "native_plugins" not in _accepted(app._sessions[sid].session)[1]["user_input"]
        else:
            with pytest.raises(ValueError):
                app.submit(sid, "invalid plugin selection", "explicit:one")
            assert app.snapshot()[0]["turns"] == []
            assert control.starts == 0
        assert selected_plugins.reads == 1
    finally:
        app.close()


def test_committed_receipt_reopen_never_recaptures_current_plugins(
        tmp_path, monkeypatch, selected_plugins):
    app, sid, control, execution = _new_app(tmp_path)
    try:
        app.submit(sid, "finished request", "explicit:one")
        session = app._sessions[sid].session
        _install_child_observation(monkeypatch, session, "terminal")
        session.complete_turn("finished request", {"reply": "done", "task": None})
        before = app.snapshot()[0]["turns"][0]
        assert before["state"] == "committed"
        root = app.root
    finally:
        app.close()
    selected_plugins.configuration = {"invalid": "must not be discovered"}
    reopened = RegistryFrontendApplication(root, execution, resume=True)
    try:
        observed = CaptureDispatch()
        reopened._sessions[sid].main_control = observed
        assert reopened.snapshot()[0]["turns"][0] == before
        assert reopened.submit(sid, "finished request", "explicit:one") == 1
        assert selected_plugins.reads == 1
        assert observed.starts == 0
    finally:
        reopened.close()


def test_gateway_serializes_same_request_without_recapturing_plugins(
        tmp_path, selected_plugins):
    holder = []

    def factory():
        app, sid, control, _ = _new_app(tmp_path)
        holder.append((app, sid, control))
        return app

    gateway = FrontendGateway(factory, tick_interval=60)
    app, sid, control = holder[0]
    ready = threading.Barrier(2)

    def submit():
        ready.wait(timeout=10)
        return gateway.call("submit", sid, "same request", "explicit:one")

    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            results = [callers.submit(submit) for _ in range(2)]
            assert [result.result(timeout=20) for result in results] == [1, 1]
        view = gateway.call("snapshot")[0]
        assert len(view["turns"]) == 1
        assert selected_plugins.reads == control.starts == 1
    finally:
        # Captured dispatch has no process to checkpoint; remove its fake
        # handles before exercising the gateway's ordinary owner shutdown.
        control.handles.clear()
        gateway.close()
