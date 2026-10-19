"""Private DSH RPC: offline application doubles plus actual MainSession history."""
from __future__ import annotations

import copy
import io
import json
import threading

import pytest

from cpn.frontend import dsh_rpc
from cpn.frontend.dsh_rpc import DshFrontendRPC, PROTOCOL
from cpn.rpnh.frontend_application import FrontendError, FrontendGateway
from test_opencode_frontend import ApplicationDouble, MODEL, MODEL_TWO, SID


def frame(method="snapshot", params=None, identity="rpc-1"):
    return {"protocol": PROTOCOL, "id": identity, "method": method,
            "params": {} if params is None else params}


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class RecordingGateway:
    def __init__(self, app):
        self.app = app
        self.calls = []

    def call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return getattr(self.app, name)(*args, **kwargs)


@pytest.fixture
def context():
    app = ApplicationDouble()
    app.create_session(reasoning_effort="high")
    gateway = RecordingGateway(app)
    return app, gateway, DshFrontendRPC(gateway, SID)


def test_initialize_and_snapshot_return_only_bound_session(context):
    app, gateway, rpc = context
    other = copy.deepcopy(app.views[0])
    other["id"] = "unrelated-session"
    app.views.insert(0, other)
    expected = app.views[1]
    reply = rpc.handle(frame("initialize", identity=0))
    assert reply == {"protocol": PROTOCOL, "id": 0, "result": {
        "configuration": app.configuration(), "session": expected}}
    assert rpc.handle(frame())["result"] == expected
    assert all(call[0] in {"configuration", "snapshot"} for call in gateway.calls)


@pytest.mark.parametrize("params,selection,effort", [
    ({}, MODEL["selection"], "high"),
    ({"reasoningEffort": "low"}, MODEL["selection"], "low"),
    ({"selectionId": MODEL["selection"]}, MODEL["selection"], "medium"),
    ({"selectionId": MODEL_TWO["selection"]}, MODEL_TWO["selection"], "medium"),
    ({"selectionId": MODEL_TWO["selection"], "reasoningEffort": "low"}, MODEL_TWO["selection"], "low"),
])
@pytest.mark.parametrize("method", ["submit", "command"])
def test_submission_delegates_stable_identity_and_actual_defaults(
        context, monkeypatch, params, selection, effort, method):
    _app, gateway, rpc = context
    original = dsh_rpc.request_identity
    identities = []

    def capture(*args):
        identities.append(args)
        return original(*args)

    monkeypatch.setattr(dsh_rpc, "request_identity", capture)
    payload = {"text": "问题\nsecond line"} if method == "submit" else {
        "name": "rpnh-task", "arguments": "task-1 status"}
    payload.update(requestId="retry:one", **params)
    reply = rpc.handle(frame(method, payload))
    text = payload["text"] if method == "submit" else "rpnh-task\ntask-1 status"
    assert identities == [(text, None, {
        "provider": "rpnh", "selection": selection, "reasoning_effort": effort,
    }, "retry:one")]
    arguments = (SID, text, "explicit:retry:one") if method == "submit" else (
        SID, "rpnh-task", "task-1 status", "explicit:retry:one")
    assert gateway.calls[-1] == (method, arguments, {
        "selection_id": selection, "reasoning_effort": effort})
    assert reply["result"] == ({"ordinal": 1} if method == "submit" else {
        "status": "observation", "name": "rpnh-task"})


def test_nonreasoning_model_uses_none_in_identity(context, monkeypatch):
    app, gateway, rpc = context
    app.views[0]["model"]["reasoning_effort"] = None
    captured = []
    monkeypatch.setattr(dsh_rpc, "request_identity", lambda *a: captured.append(a) or "explicit:one")
    rpc.handle(frame("submit", {"text": "hello", "requestId": "one"}))
    assert captured[0][2]["reasoning_effort"] is None
    assert gateway.calls[-1][2]["reasoning_effort"] is None


def test_response_loss_reuses_request_identity_across_rpc_instances(context):
    app, gateway, rpc = context
    request = frame("submit", {"text": "hello", "requestId": "one"})
    assert rpc.handle(request)["result"] == {"ordinal": 1}
    request["id"] = "new-transport-id"
    reply = DshFrontendRPC(gateway, SID).handle(request)
    assert reply == {"protocol": PROTOCOL, "id": "new-transport-id", "result": {"ordinal": 1}}
    assert len(app.views[0]["turns"]) == 1
    request["params"]["text"] = "different"
    assert rpc.handle(request)["error"]["code"] == "request_conflict"


def test_abort_delegates_to_bound_session(context):
    _app, gateway, rpc = context
    assert rpc.handle(frame("abort"))["result"] == {"stopped": True}
    assert gateway.calls == [("abort", (SID,), {})]


@pytest.mark.parametrize("changes", [
    {"protocol": "other"}, {"id": None}, {"id": True}, {"id": 1.5},
    {"id": 2**53}, {"id": ""}, {"id": "a" * 161}, {"method": []},
    {"params": None}, {"params": []}, {"root": "/private"},
])
def test_invalid_envelopes_reject_without_dispatch(context, changes):
    _app, gateway, rpc = context
    value = {**frame(), **changes}
    assert rpc.handle(value)["error"]["code"] == "invalid_frame"
    assert gateway.calls == []


@pytest.mark.parametrize("value", [None, [], "snapshot", {}, {"protocol": PROTOCOL}])
def test_missing_envelope_rejects(context, value):
    _app, gateway, rpc = context
    assert rpc.handle(value)["error"]["code"] == "invalid_frame"
    assert gateway.calls == []


@pytest.mark.parametrize("method", ["create_session", "resume", "close", "configuration", "provider", "__dict__"])
def test_unknown_methods_reject_without_dispatch(context, method):
    _app, gateway, rpc = context
    assert rpc.handle(frame(method))["error"]["code"] == "unknown_method"
    assert gateway.calls == []


@pytest.mark.parametrize("method", ["initialize", "snapshot", "abort", "submit", "command"])
@pytest.mark.parametrize("override", ["root", "sessionId", "provider", "module", "tools"])
def test_authority_overrides_never_reach_gateway(context, method, override):
    _app, gateway, rpc = context
    params = {"text": "hello", "requestId": "one"} if method == "submit" else (
        {"name": "rpnh-help", "arguments": "", "requestId": "one"} if method == "command" else {})
    params[override] = "client-owned"
    assert rpc.handle(frame(method, params))["error"]["code"] == "invalid_params"
    assert gateway.calls == []


@pytest.mark.parametrize("params", [
    {"text": "hello"}, {"text": "hello", "requestId": None},
    {"text": 42, "requestId": "one"}, {"text": " ", "requestId": "one"},
    {"text": "hello", "requestId": "one", "selectionId": ""},
    {"text": "hello", "requestId": "one", "selectionId": None},
    {"text": "hello", "requestId": "one", "reasoningEffort": False},
])
def test_invalid_submit_parameters_reject_without_dispatch(context, params):
    _app, gateway, rpc = context
    assert rpc.handle(frame("submit", params))["error"]["code"] == "invalid_params"
    assert gateway.calls == []


@pytest.mark.parametrize("request_id", ["", "space id", "é", "a" * 161])
def test_invalid_request_identity_never_falls_back(context, request_id):
    _app, gateway, rpc = context
    reply = rpc.handle(frame("submit", {"text": "hello", "requestId": request_id}))
    assert reply["error"]["code"] == "invalid_request_id"
    assert all(call[0] not in {"submit", "command"} for call in gateway.calls)


def test_missing_bound_session_and_unknown_model_do_not_submit(context):
    app, gateway, rpc = context
    reply = rpc.handle(frame("submit", {
        "text": "hello", "requestId": "one", "selectionId": "unknown"}))
    assert reply["error"]["code"] == "unknown_model"
    app.views.clear()
    assert rpc.handle(frame())["error"]["code"] == "unknown_session"
    assert not any(call[0] == "submit" for call in gateway.calls)


@pytest.mark.parametrize("error,code", [
    (FrontendError("unknown_outcome", "An unowned attempt exists; no automatic replay."), "unknown_outcome"),
    (FrontendError("outcome_pending", "Outcome pending. Retry only with the same identity."), "outcome_pending"),
    (RuntimeError("secret-token private-endpoint traceback"), "application_error"),
])
def test_failures_are_correlated_and_never_retried(context, monkeypatch, error, code):
    app, gateway, rpc = context

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(app, "submit", fail)
    reply = rpc.handle(frame("submit", {"text": "hello", "requestId": "one"}, "lost"))
    assert reply["id"] == "lost" and reply["error"]["code"] == code
    assert "secret-token" not in json.dumps(reply)
    assert len([call for call in gateway.calls if call[0] == "submit"]) == 1


@pytest.mark.parametrize("bad", [
    b'not-json\n', b'\xff\n', b'\n',
    b'{"protocol":"rpnh/dsh_frontend/v1","id":1,"id":2,"method":"snapshot","params":{}}\n',
    b'{"protocol":"rpnh/dsh_frontend/v1","id":1,"method":"abort","params":{"x":1,"x":2}}\n',
    b'{"id":NaN}\n', b'{"id":Infinity}\n', b'{"id":-Infinity}\n',
])
def test_malformed_wire_frames_reject_and_next_frame_remains_usable(context, bad):
    _app, gateway, rpc = context
    reader = io.BytesIO(bad + wire(frame()))
    writer = io.BytesIO()
    rpc.serve(reader, writer)
    replies = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert replies[0]["id"] is None
    assert replies[0]["error"]["code"] == "invalid_json"
    assert replies[1]["result"]["id"] == SID
    assert gateway.calls == [("snapshot", (), {})]
    assert not reader.closed and not writer.closed


def test_nonfinite_and_non_utf8_values_do_not_dispatch(context):
    _app, gateway, rpc = context
    for value in [float("nan"), float("inf"), "\ud800"]:
        reply = rpc.handle(frame("abort", {"bad": value}))
        assert reply["id"] == "rpc-1"
        assert reply["error"]["code"] == "invalid_frame"
    writer = io.BytesIO()
    rpc.serve(io.BytesIO(wire(frame("abort", {"bad": 1})).replace(b'"bad":1', b'"bad":1e999')), writer)
    assert json.loads(writer.getvalue())["error"]["code"] == "invalid_frame"
    assert gateway.calls == []


def test_invalid_unicode_id_still_has_an_encodable_error(context):
    _app, gateway, rpc = context
    writer = io.BytesIO()
    value = frame(identity="\ud800")
    rpc.serve(io.BytesIO(json.dumps(value).encode() + b"\n"), writer)
    reply = json.loads(writer.getvalue())
    assert reply["id"] is None
    assert reply["error"]["code"] == "invalid_frame"
    assert gateway.calls == []


def test_eof_does_not_dispatch_partial_frame_or_close_gateway(context):
    app, gateway, rpc = context
    writer = io.BytesIO()
    rpc.serve(io.BytesIO(), writer)
    assert writer.getvalue() == b""
    rpc.serve(io.BytesIO(wire(frame("abort")).rstrip(b"\n")), writer)
    assert json.loads(writer.getvalue())["error"]["code"] == "invalid_json"
    assert gateway.calls == []
    assert not app.closed


def test_wire_and_direct_frame_limits_are_bytes(context, monkeypatch):
    _app, gateway, rpc = context
    assert dsh_rpc.MAX_FRAME_BYTES == 68 * 1024 * 1024
    monkeypatch.setattr(dsh_rpc, "MAX_FRAME_BYTES", 512)
    value = frame("submit", {"text": "", "requestId": "one"})
    overhead = len(wire(value)) - 1
    value["params"]["text"] = "a" * (512 - overhead - 3) + "中"
    assert len(wire(value)) == 513
    writer = io.BytesIO()
    rpc.serve(io.BytesIO(wire(value)), writer)
    assert json.loads(writer.getvalue())["result"] == {"ordinal": 1}
    value["params"]["text"] += "a"
    assert rpc.handle(value)["error"]["code"] == "frame_too_large"
    writer = io.BytesIO()
    rpc.serve(io.BytesIO(wire(value) + wire(frame("abort"))), writer)
    assert json.loads(writer.getvalue())["error"]["code"] == "frame_too_large"
    assert len([call for call in gateway.calls if call[0] == "submit"]) == 1
    assert not any(call[0] == "abort" for call in gateway.calls)


def test_response_limits_and_invalid_results_do_not_leak_or_retry(context, monkeypatch):
    app, gateway, rpc = context
    monkeypatch.setattr(dsh_rpc, "MAX_FRAME_BYTES", 512)
    for result, code in [("secret" * 200, "response_too_large"),
                         (float("nan"), "application_error"),
                         (object(), "application_error")]:
        monkeypatch.setattr(app, "command", lambda *_a, **_kw: result)
        writer = io.BytesIO()
        rpc.serve(io.BytesIO(wire(frame("command", {
            "name": "rpnh-help", "arguments": "", "requestId": "one"}))), writer)
        reply = json.loads(writer.getvalue())
        assert reply["id"] == "rpc-1" and reply["error"]["code"] == code
        assert "secret" not in writer.getvalue().decode()
    assert len([call for call in gateway.calls if call[0] == "command"]) == 3


def test_real_gateway_owns_calls_and_launcher_owns_close():
    holder = []

    def factory():
        app = ApplicationDouble()
        app.create_session()
        holder.append(app)
        return app

    gateway = FrontendGateway(factory, tick_interval=60)
    app = holder[0]
    try:
        rpc = DshFrontendRPC(gateway, SID)
        writer = io.BytesIO()
        rpc.serve(io.BytesIO(wire(frame("initialize")) + wire(frame("abort", identity=2))), writer)
        assert [json.loads(line)["id"] for line in writer.getvalue().splitlines()] == ["rpc-1", 2]
        assert not app.closed
        assert set(app.owner_threads) == {gateway._thread.ident}
        assert gateway._thread.ident != threading.get_ident()
    finally:
        gateway.close()
    assert app.closed


def test_real_main_session_history_owner_and_close_reopen(tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    from cpn.rpnh.frontend_application import RegistryFrontendApplication
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh.session_access import stable_frontend_session_id
    from cpn.rpnh.task_control import TaskControl
    from test_main_session_registry import _write_execution_profile
    from test_opencode_registry_integration import CaptureDispatch, CountingTerminalPort

    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "shared-session"
    port = CountingTerminalPort({"reply": "registered offline answer", "task": None})
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)

    def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("RPC history/reopen must not launch workers")

    monkeypatch.setattr(TaskControl, "_spawn", unexpected_spawn)
    basic = MainSession(root, execution)
    run_agent_task(basic.prepare_turn("from Basic"))
    basic.reconcile_active_turn()
    sid = stable_frontend_session_id(root)
    app = RegistryFrontendApplication(root, execution, resume=True)
    request = frame("submit", {"text": "from DSH", "requestId": "dsh-one"})
    try:
        rpc = DshFrontendRPC(RecordingGateway(app), sid)
        assert rpc.handle(frame("initialize"))["result"]["session"]["turns"][0]["text"] == "from Basic"
        with pytest.raises(FrontendError, match="writable owner"):
            RegistryFrontendApplication(root, execution, resume=True)
        control = CaptureDispatch()
        app._sessions[sid].main_control = control
        assert rpc.handle(request)["result"] == {"ordinal": 2}
        assert rpc.handle(request)["result"] == {"ordinal": 2}
        assert control.starts == 1
        run_agent_task(control.spec)
        app.tick()
        turns = rpc.handle(frame())["result"]["turns"]
        assert [turn["text"] for turn in turns] == ["from Basic", "from DSH"]
        assert turns[1]["state"] == "committed"
        assert turns[1]["answer"]["reply"] == "registered offline answer"
        assert turns[1]["key"] == "explicit:dsh-one"
        assert port.calls == 2
        assert not (root / "threads").exists()
    finally:
        app.close()

    reopened = RegistryFrontendApplication(root, execution, resume=True)
    try:
        rpc = DshFrontendRPC(RecordingGateway(reopened), sid)
        assert rpc.handle(frame())["result"]["turns"] == turns
        assert rpc.handle(request)["result"] == {"ordinal": 2}
        conflict = copy.deepcopy(request)
        conflict["params"]["text"] = "different"
        assert rpc.handle(conflict)["error"]["code"] == "request_conflict"
        assert port.calls == 2
    finally:
        reopened.close()
    assert MainSession.resume(root).history == [
        ("user", "from Basic"), ("assistant", "registered offline answer"),
        ("user", "from DSH"), ("assistant", "registered offline answer")]


def test_real_foreign_terminal_requires_explicit_command(tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import run_agent_task
    from cpn.rpnh.frontend_application import RegistryFrontendApplication
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh.session_access import stable_frontend_session_id
    from test_main_session_registry import _write_execution_profile
    from test_opencode_registry_integration import CountingTerminalPort

    execution = _write_execution_profile(tmp_path)
    root = tmp_path / "foreign-terminal"
    port = CountingTerminalPort({"reply": "terminal offline answer", "task": None})
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)
    basic = MainSession(root, execution)
    run_agent_task(basic.prepare_turn("foreign turn"))
    app = RegistryFrontendApplication(root, execution, resume=True)
    try:
        rpc = DshFrontendRPC(RecordingGateway(app), stable_frontend_session_id(root))
        assert rpc.handle(frame("initialize"))["result"]["session"]["turns"][0]["state"] == "terminal"
        app.tick()
        assert rpc.handle(frame())["result"]["turns"][0]["state"] == "terminal"
        assert app._sessions[rpc.session_id].session._main_thread.recover_thread()["committed_history"] == []
        reply = rpc.handle(frame("command", {
            "name": "rpnh-resume", "arguments": "", "requestId": "settle"}))
        assert reply["result"] == {"status": "main_terminal_evidence_committed"}
        assert rpc.handle(frame())["result"]["turns"][0]["state"] == "committed"
        assert port.calls == 1
    finally:
        app.close()
