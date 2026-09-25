"""Deterministic protocol/transport tests with explicit application doubles.

These tests do NOT claim to run a provider or real Registry execution. Real-core
coverage lives separately in test_opencode_registry_integration.py.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace as NS
from urllib.parse import urlsplit

import pytest

from cpn.rpnh.frontend_application import (
    FrontendError, FrontendGateway, RegistryFrontendApplication, _Session, canonical, request_identity,
)
from cpn.frontend.opencode_protocol import COMMANDS, OPENCODE_VERSION, OpenCodeProtocol, encode_sse
from cpn.frontend.opencode_http import MAX_BODY, OpenCodeHTTPServer
from cpn.frontend.opencode_launcher import check_version, isolated_environment

SID = "ses_" + "a" * 32
MODEL = {"provider": "test-provider", "model": "exact-model", "selection": "test-selection"}
MODEL_TWO = {"provider": "test-provider-two", "model": "exact-model-two", "selection": "test-selection-two"}


def public_model(model, *, ready=True):
    return {**model, "provider_name": model["provider"], "ready": ready}


class ApplicationDouble:
    def __init__(self):
        self.views = []
        self.calls = []
        self.owner_threads = [threading.get_ident()]
        self.closed = False
        self.physical_calls = 0

    def _record(self, name):
        self.calls.append(name)
        self.owner_threads.append(threading.get_ident())

    def configuration(self):
        self._record("configuration")
        return {"default_selection": MODEL["selection"],
                "profiles": [public_model(MODEL), public_model(MODEL_TWO)]}

    def snapshot(self):
        self._record("snapshot")
        return copy.deepcopy(self.views)

    def tick(self):
        self._record("tick")

    def close(self):
        self._record("close")
        self.closed = True

    def create_session(self, selection_id=None):
        self._record("create_session")
        selection_id = selection_id or MODEL["selection"]
        model = MODEL if selection_id == MODEL["selection"] else MODEL_TWO
        sid = SID if not self.views else "ses_" + f"{len(self.views):032x}"
        self.views.append({"id": sid, "created": 1000, "updated": 1000,
                           "model": public_model(model), "turns": [], "error": None})
        return sid

    def submit(self, sid, text, key, *, kind=None, selection_id=None):
        self._record("submit")
        view = next(view for view in self.views if view["id"] == sid)
        model = MODEL if (selection_id or view["model"]["selection"]) == MODEL["selection"] else MODEL_TWO
        view["model"] = public_model(model)
        turns = view["turns"]
        for turn in turns:
            if turn["key"] == key:
                if (turn["text"], turn["kind"]) != (text, kind):
                    raise FrontendError("request_conflict", "Conflict.")
                return turn["ordinal"]
        if any(t["state"] == "running" for t in turns):
            raise FrontendError("session_busy", "Busy.")
        number = len(turns) + 1
        turns.append({"ordinal": number, "key": key, "text": text, "kind": kind, "state": "running",
                      "created": 1000 + number, "updated": 1000 + number,
                      "model": public_model(model),
                      "turn_ref": {"entity_type": "main_turn/v1", "logical_id": f"turn-{number}", "version_id": "v1"},
                      "attempt": f"main/turn-{number:04d}", "answer": None, "children": []})
        return number

    def commit(self, text="registered answer"):
        self.views[0]["turns"][-1].update(state="committed", updated=2000,
                                        answer={"reply": text, "protocol_valid": True, "task": None})

    def abort(self, sid):
        self._record("abort")
        if self.views[0]["turns"]:
            self.views[0]["turns"][-1]["state"] = "stopped_by_owner"
        return True

    def command(self, sid, name, arguments, key, *, selection_id=None):
        self._record("command")
        return {"status": "observation", "name": name}


class LocalGateway:
    def __init__(self, app):
        self.app = app

    def call(self, name, *args, **kwargs):
        return getattr(self.app, name)(*args, **kwargs)


@pytest.fixture
def context(tmp_path):
    app = ApplicationDouble()
    protocol = OpenCodeProtocol(LocalGateway(app), str(tmp_path))
    protocol.route("POST", "/session", {})
    return app, protocol


def prompt(text="question", **extra):
    return {"agent": "rpnh", "model": {"providerID": "rpnh", "modelID": MODEL["selection"]},
            "parts": [{"type": "text", "text": text}], **extra}


@pytest.mark.parametrize("path,kind", [
    ("/config/providers", dict), ("/provider", dict), ("/agent", list), ("/config", dict),
    ("/path", dict), ("/project/current", dict), ("/project/rpnh/directories", list),
    ("/session", list), ("/session/status", dict), ("/command", list), ("/lsp", list),
    ("/mcp", dict), ("/experimental/resource", dict), ("/formatter", list),
    ("/provider/auth", dict), ("/vcs", dict), ("/experimental/workspace", list),
    ("/experimental/workspace/status", list), ("/experimental/capabilities", dict),
    ("/permission", list), ("/question", list), ("/global/health", dict),
])
def test_pinned_bootstrap_response_shapes(context, path, kind):
    app, protocol = context
    reply = protocol.route("GET", path)
    assert reply.status == 200 and isinstance(reply.body, kind)
    assert app.physical_calls == 0


def test_exact_provider_allowlist_and_no_routes(context):
    app, protocol = context
    value = protocol.route("GET", "/provider").body
    assert value["default"] == {"rpnh": MODEL["selection"]}
    provider = value["all"][0]
    assert provider["env"] == [] and provider["options"] == {}
    model = provider["models"][MODEL["selection"]]
    assert model["headers"] == {} and model["api"]["url"] == ""
    assert model["options"]["rpnh_metrics"] == "unavailable"
    assert set(provider["models"]) == {MODEL["selection"], MODEL_TWO["selection"]}
    assert model["options"]["rpnh_provider"] == MODEL["provider"]
    assert model["options"]["rpnh_exact_model"] == MODEL["model"]
    assert protocol.route("GET", "/permission").body == []


def test_session_creation_and_turn_can_select_an_rpnh_profile(context):
    app, protocol = context
    created = protocol.route("POST", "/session", {
        "agent": "rpnh",
        "model": {"providerID": "rpnh", "id": MODEL_TWO["selection"]},
    }).body
    assert created["model"] == {
        "providerID": "rpnh", "id": MODEL_TWO["selection"]}
    sid = created["id"]
    receipt = protocol.route("POST", f"/session/{sid}/message", {
        **prompt("second profile"),
        "model": {"providerID": "rpnh", "modelID": MODEL_TWO["selection"]},
    })
    assert receipt.body["info"]["modelID"] == MODEL_TWO["selection"]
    view = next(view for view in app.views if view["id"] == sid)
    assert view["model"]["selection"] == MODEL_TWO["selection"]
    assert view["turns"][0]["model"]["model"] == MODEL_TWO["model"]


def test_registered_answer_only_and_stable_reconnect(context):
    app, protocol = context
    path = f"/session/{SID}/message"
    receipt = protocol.route("POST", path, prompt(messageID="msg_client_one"))
    assert receipt.body["info"]["role"] == "assistant"
    assert receipt.body["parts"] == []
    before = protocol.events()
    assert not any(e["payload"]["type"] == "message.updated" and
                   e["payload"]["properties"]["info"]["role"] == "assistant" for e in before)
    app.commit()
    protocol.notify()
    messages = protocol.route("GET", path).body
    assert [m["info"]["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["info"]["id"] == "msg_client_one"
    recreated = OpenCodeProtocol(LocalGateway(app), protocol.directory)
    assert recreated.route("GET", path).body == messages
    for event in recreated.events():
        assert event["directory"] == protocol.directory and event["payload"]["id"].startswith("evt_")
        assert event["payload"]["properties"]["sessionID"] == SID
    assert app.physical_calls == 0


def test_reject_uncommitted_answer(context):
    app, protocol = context
    protocol.route("POST", f"/session/{SID}/message", prompt())
    app.views[0]["turns"][0]["answer"] = {"reply": "not committed"}
    with pytest.raises(FrontendError, match="committed"):
        protocol.events()


def test_response_loss_does_not_change_identity(context):
    app, protocol = context
    path = f"/session/{SID}/message"
    first = protocol.route("POST", path, prompt())
    for _ in range(4):
        assert protocol.route("POST", path, prompt()).headers == first.headers
        protocol.events()
    app.commit()
    assert protocol.route("POST", path, prompt()).headers == first.headers
    assert len(app.views[0]["turns"]) == 1
    protocol.route("POST", path, prompt(messageID="intentional-repeat"))
    assert len(app.views[0]["turns"]) == 2


@pytest.mark.parametrize("request_id", ["", False, 1, {}, "bad value", "x" * 161])
def test_invalid_explicit_identity_does_not_fallback(context, request_id):
    app, protocol = context
    with pytest.raises(FrontendError):
        protocol.route("POST", f"/session/{SID}/message", prompt(messageID=request_id))
    assert app.views[0]["turns"] == []


def test_conflicting_id_headers(context):
    _, protocol = context
    with pytest.raises(FrontendError, match="agree"):
        protocol.route("POST", f"/session/{SID}/message", prompt(messageID="one"), {"Idempotency-Key": "two"})


@pytest.mark.parametrize("operation", ["shell", "fork", "revert", "share", "summarize", "init", "permissions/id"])
def test_unsupported_effects_do_not_reach_application(context, operation):
    app, protocol = context
    before = len(app.calls)
    with pytest.raises(FrontendError) as error:
        protocol.route("POST", f"/session/{SID}/{operation}", {"command": "forbidden"})
    assert error.value.status == 501
    assert app.calls[before:] == []


@pytest.mark.parametrize("changes", [
    {"system": "override"}, {"tools": {}}, {"model": {"providerID": "other", "modelID": "exact-model"}},
    {"agent": "build"}, {"variant": "high"}, {"noReply": True}, {"format": {"type": "json_schema"}},
    {"parts": [{"type": "file", "url": "file:///unavailable"}]},
    {"parts": [{"type": "text", "text": "question", "synthetic": True}]},
])
def test_prompt_overrides_rejected_before_submission(context, changes):
    app, protocol = context
    with pytest.raises(FrontendError):
        protocol.route("POST", f"/session/{SID}/message", prompt(**changes))
    assert app.views[0]["turns"] == []


def test_abort_projects_error_not_success_and_command_allowlist(context):
    app, protocol = context
    protocol.route("POST", f"/session/{SID}/message", prompt())
    assert protocol.route("POST", f"/session/{SID}/abort", {}).body is True
    message = protocol.route("GET", f"/session/{SID}/message").body[-1]
    assert message["info"]["error"]["name"] == "MessageAbortedError"
    assert "finish" not in message["info"]
    assert {c["name"] for c in protocol.route("GET", "/command").body} == set(COMMANDS)
    reply = protocol.route("POST", f"/session/{SID}/command", {"command": "rpnh-tasks", "arguments": ""})
    assert reply.body["parts"][0]["metadata"]["rpnh_persistent"] is False
    assert "finish" not in reply.body["info"]


def test_sse_framing_and_revision(context):
    app, protocol = context
    protocol.route("POST", f"/session/{SID}/message", prompt())
    event = protocol.events()[0]
    encoded = encode_sse(event)
    assert encoded.endswith(b"\n\n") and encoded.count(b"\ndata: ") == 1
    assert json.loads(encoded.split(b"\ndata: ")[1].strip()) == event
    protocol.notify()
    assert protocol.events()[0]["payload"]["id"] != event["payload"]["id"]
    part_events = [item for item in protocol.events()
                   if item["payload"]["type"] == "message.part.updated"]
    assert part_events and all(
        isinstance(item["payload"]["properties"]["time"], int)
        for item in part_events)


@pytest.fixture
def http_server(context):
    app, protocol = context
    server = OpenCodeHTTPServer(protocol)
    server.start()
    try:
        yield app, protocol, server
    finally:
        server.close()


def request(server, method, path, body=None, headers=None):
    address = urlsplit(server.url)
    client = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
    actual = {"Authorization": server.authorization, "Content-Type": "application/json"}
    actual.update(headers or {})
    client.request(method, path, None if body is None else json.dumps(body), actual)
    response = client.getresponse()
    value = response.read()
    status = response.status
    client.close()
    return status, json.loads(value) if value else None


def test_real_loopback_roundtrip_and_unsupported(http_server):
    app, protocol, server = http_server
    assert request(server, "GET", "/global/health")[1]["version"] == OPENCODE_VERSION
    status, receipt = request(server, "POST", f"/session/{SID}/message", prompt())
    assert status == 200 and receipt["info"]["role"] == "assistant" and receipt["parts"] == []
    assert request(server, "POST", f"/session/{SID}/shell", {"command": "forbidden"})[0] == 501
    assert app.physical_calls == 0


@pytest.mark.parametrize("headers,status", [
    ({"Authorization": "wrong"}, 401), ({"Host": "attacker.invalid"}, 403),
    ({"Origin": "https://attacker.invalid"}, 403), ({"Transfer-Encoding": "chunked"}, 400),
])
def test_real_http_request_boundary(http_server, headers, status):
    app, _, server = http_server
    assert request(server, "GET", "/global/health", headers=headers)[0] == status
    assert app.physical_calls == 0


def test_real_http_duplicate_json_and_size(http_server):
    _, _, server = http_server
    address = urlsplit(server.url)
    client = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
    client.request("POST", "/session", '{"agent":"rpnh","agent":"build"}',
                   {"Authorization": server.authorization, "Content-Type": "application/json"})
    response = client.getresponse()
    assert response.status == 400
    assert json.loads(response.read())["data"]["code"] == "invalid_json"
    client.close()
    assert request(server, "POST", "/session", headers={"Content-Length": str(MAX_BODY + 1)})[0] == 413


def test_real_sse_reconnect_does_not_submit(http_server):
    app, _, server = http_server
    request(server, "POST", f"/session/{SID}/message", prompt())
    address = urlsplit(server.url)
    for _ in range(2):
        client = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
        client.request("GET", "/global/event", headers={"Authorization": server.authorization, "Last-Event-ID": "lost"})
        response = client.getresponse()
        assert response.status == 200
        seen = []
        for _line in range(24):  # Fixed bound; first full snapshot contains status.
            line = response.readline()
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                seen.append(event)
                if event["payload"]["type"] == "session.status":
                    break
        assert any(e["payload"]["type"] == "message.updated" for e in seen)
        response.close()
        client.close()
    assert app.calls.count("submit") == 1


def test_gateway_serializes_application_and_closes_on_owner():
    holder = []
    def factory():
        holder.append(ApplicationDouble())
        return holder[0]
    gateway = FrontendGateway(factory, tick_interval=60)
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _: gateway.call("create_session"), range(24)))
        assert len(values) == len(set(values)) == 24 and SID in values
    gateway.close()
    assert holder[0].closed
    assert len(set(holder[0].owner_threads)) == 1
    assert holder[0].owner_threads[0] != threading.get_ident()
    with pytest.raises(FrontendError, match="unavailable"):
        gateway.call("snapshot")


def test_gets_do_not_trigger_reconciliation():
    holder = []
    gateway = FrontendGateway(lambda: (holder.append(ApplicationDouble()) or holder[0]), tick_interval=60)
    try:
        for _ in range(5):
            gateway.call("snapshot")
        assert "tick" not in holder[0].calls
    finally:
        gateway.close()


def test_environment_strips_provider_and_global_settings(tmp_path):
    source = {"PATH": "/usr/bin", "TERM": "xterm-256color", "OPENAI_API_KEY": "secret",
              "OPENCODE_CONFIG": "/private/config", "NODE_OPTIONS": "--require private-plugin",
              "HTTP_PROXY": "private-proxy", "RPNH_EXECUTION_CONFIG": "/private/profile"}
    env = isolated_environment(tmp_path, source)
    assert not {"OPENAI_API_KEY", "NODE_OPTIONS", "HTTP_PROXY", "RPNH_EXECUTION_CONFIG"} & set(env)
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    assert Path(env["OPENCODE_CONFIG"]).is_relative_to(tmp_path)
    assert json.loads(Path(env["OPENCODE_TUI_CONFIG"]).read_text()) == {"plugin": []}
    assert source["OPENCODE_CONFIG"] == "/private/config"


@pytest.mark.parametrize("stdout,code,success", [("1.18.32\n", 0, True), ("opencode 1.18.32\n", 0, True),
    ("1.18.33\n", 0, False), ("1.18.32+modified", 0, False), ("1.18.32", 1, False)])
def test_exact_version_probe(tmp_path, monkeypatch, stdout, code, success):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: NS(stdout=stdout, returncode=code))
    if success:
        check_version("opencode", {}, tmp_path)
    else:
        with pytest.raises(RuntimeError, match="exactly"):
            check_version("opencode", {}, tmp_path)


def test_resume_removes_exact_diagnostic_id(context):
    app, protocol = context
    protocol.route("POST", f"/session/{SID}/message", prompt(messageID="msg_client_time"))
    protocol.route("POST", f"/session/{SID}/abort", {})
    old = protocol.route("GET", f"/session/{SID}/message").body[-1]["info"]["id"]
    app.views[0]["turns"][-1]["state"] = "running"
    protocol.notify()
    removed = [e["payload"]["properties"]["messageID"] for e in protocol.events()
               if e["payload"]["type"] == "message.removed"]
    assert old in removed


def test_close_listener_releases_port(context):
    _, protocol = context
    server = OpenCodeHTTPServer(protocol)
    port = urlsplit(server.url).port
    server.start()
    server.close()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))


def test_source_extracted_sdk_subset_schemas(context):
    import importlib.resources
    import jsonschema
    app, protocol = context
    source = importlib.resources.files("cpn.frontend").joinpath("opencode_compatibility.v1.json").read_text()
    fixture = json.loads(source)
    assert fixture["upstream"]["package_version"] == OPENCODE_VERSION
    for path, schema in fixture["responses"].items():
        jsonschema.Draft202012Validator({**schema, "$defs": fixture["$defs"]}).validate(protocol.route("GET", path).body)
    receipt = protocol.route(
        "POST", f"/session/{SID}/message",
        prompt(messageID="msg_schema_case"))
    jsonschema.Draft202012Validator({
        **fixture["post_responses"]["/session/:session/message"],
        "$defs": fixture["$defs"],
    }).validate(receipt.body)
    app.commit()
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/MessageWithParts", "$defs": fixture["$defs"]})
    for message in protocol.route("GET", f"/session/{SID}/message").body:
        validator.validate(message)
    event_validator = jsonschema.Draft202012Validator({
        **fixture["events"]["message.part.updated"],
        "$defs": fixture["$defs"],
    })
    for event in protocol.events():
        if event["payload"]["type"] == "message.part.updated":
            event_validator.validate(event["payload"])
