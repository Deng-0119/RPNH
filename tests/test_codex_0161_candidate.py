"""Exact 0.161 candidate JSON/Registry delta; no binary, socket or model execution."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft7Validator
import pytest

import cpn.frontend.codex_app_server as frontend
from cpn.frontend.codex_history import HistoryCursor, public_id
from cpn.rpnh.main_session import MainSession
from test_codex_compat import _FakeWebSocket, _local_profile
from test_codex_history import _append, _request, _walk

CANDIDATE = "candidate-0.161.0"
PINNED = "pinned-0.155.0"


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    original = MainSession(tmp_path / "session", execution, task_control=object())
    for ordinal in range(1, 5):
        _append(original._main_thread, monkeypatch, ordinal,
                outcome="running" if ordinal == 2 else "terminal", private=ordinal == 1)
    server = frontend.CodexAppServer(original.root, execution, compatibility_profile=CANDIDATE)
    state = next(iter(server._threads.values()))
    socket = _FakeWebSocket([])
    server._initialized_connections.add(id(socket))
    yield server, state, socket, state.session._main_thread
    server.close()


def _object_request(owner, ordinal=3, kind="user", **params):
    server, state, socket, _ = owner
    return _request(server, state, socket, "thread/items/list",
                    turnId=public_id(state.thread_id, ordinal, "turn"),
                    cursor={"type": "item", "itemId": public_id(state.thread_id, ordinal, kind)}, **params)


def test_manifest_is_single_exact_table_and_profiles_are_immutable(candidate, monkeypatch):
    server, _, _, _ = candidate
    manifest = json.loads(Path(frontend.__file__).with_name("codex_compatibility.v1.json").read_text())
    table = manifest["compatibility_profiles"]
    assert set(table["profiles"]) == {PINNED, CANDIDATE}
    assert table["default"] == PINNED
    assert frontend.CODEX_FRONTEND_VERSION == manifest["frontend"]["version"] == "0.155.0"
    assert frontend.CODEX_CLI_VERSION_TEXT == "codex-cli 0.155.0"
    assert server.compatibility_profile is frontend._compatibility_profile(CANDIDATE)
    assert server.compatibility_profile.status == "CANDIDATE_NATIVE_CERTIFICATION_PENDING"
    with pytest.raises(FrozenInstanceError):
        server.compatibility_profile.version = "0.999.0"
    with pytest.raises(AttributeError):
        server.compatibility_profile = frontend._compatibility_profile(PINNED)
    with pytest.raises(TypeError):
        frontend._CODEX_PROFILES["candidate-0.999.0"] = server.compatibility_profile
    monkeypatch.setenv("RPNH_CODEX_COMPATIBILITY_PROFILE", CANDIDATE)
    assert frontend._compatibility_profile().version == "0.155.0"


@pytest.mark.parametrize("bad", ["0.161.0", "candidate-0.162.0", "latest", "", True, [], {}, 161])
def test_unknown_selector_rejected_before_binary_or_state_access(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(frontend.shutil, "which", lambda *_: pytest.fail("binary lookup attempted"))
    with pytest.raises(frontend.CodexCompatibilityError, match="unknown Codex"):
        frontend.resolve_codex_binary("synthetic", compatibility_profile=bad)
    with pytest.raises(frontend.CodexCompatibilityError, match="unknown Codex"):
        frontend.CodexAppServer(tmp_path / "absent", tmp_path / "missing.json", compatibility_profile=bad)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("selector,reported,ok", [
    (None, "codex-cli 0.155.0", True), (None, "codex-cli 0.161.0", False),
    (PINNED, "codex-cli 0.155.0", True), (CANDIDATE, "codex-cli 0.161.0", True),
    (CANDIDATE, "codex-cli 0.155.0", False), (CANDIDATE, "codex-cli 0.162.0", False),
    (CANDIDATE, "codex-cli 0.161.0-alpha", False), (CANDIDATE, "0.161.0", False),
])
def test_resolver_checks_only_selected_binary_exact_version(monkeypatch, selector, reported, ok):
    calls = []
    monkeypatch.setattr(frontend.shutil, "which", lambda path: calls.append(("which", path)) or "/selected/codex")
    def run(argv, **kwargs):
        calls.append(("run", argv))
        return SimpleNamespace(stdout=reported + "\n", stderr="", returncode=0)
    monkeypatch.setattr(frontend.subprocess, "run", run)
    if ok:
        assert frontend.resolve_codex_binary("/requested/codex", compatibility_profile=selector) == "/selected/codex"
    else:
        with pytest.raises(frontend.CodexCompatibilityError, match="RPNH requires"):
            frontend.resolve_codex_binary("/requested/codex", compatibility_profile=selector)
    assert calls == [("which", "/requested/codex"), ("run", ["/selected/codex", "--version"])]


@pytest.mark.parametrize("selector,version", [(None, "0.155.0"), (CANDIDATE, "0.161.0")])
def test_launcher_binds_verified_profile_before_transport(tmp_path, monkeypatch, selector, version):
    calls = []
    monkeypatch.setattr(frontend.shutil, "which", lambda _: "/selected/codex")
    monkeypatch.setattr(frontend.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=f"codex-cli {version}", stderr="", returncode=0))
    class StopBeforeTransport(Exception):
        pass
    def construct(root, execution, **kwargs):
        calls.append(frontend._compatibility_profile(kwargs["compatibility_profile"]))
        raise StopBeforeTransport
    monkeypatch.setattr(frontend, "CodexAppServer", construct)
    with pytest.raises(StopBeforeTransport):
        asyncio.run(frontend._run_codex_frontend_async(tmp_path, tmp_path / "unused.json",
                    codex_binary="/requested/codex", compatibility_profile=selector))
    assert calls == [frontend._compatibility_profile(selector)]
    assert calls[0].version == version


@pytest.mark.parametrize("selector,version,wrong", [(PINNED, "0.155.0", "0.161.0"),
                                                    (CANDIDATE, "0.161.0", "0.155.0")])
def test_initialize_exact_profile_and_failed_handshake_has_no_history(tmp_path, selector, version, wrong):
    execution = _local_profile(tmp_path / "profiles")
    original = MainSession(tmp_path / "session", execution, task_control=object())
    server = frontend.CodexAppServer(original.root, execution, compatibility_profile=selector)
    try:
        state = next(iter(server._threads.values()))
        socket = _FakeWebSocket([
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex-tui", "version": wrong}}},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "thread/items/list", "params": {"threadId": state.thread_id}},
        ])
        asyncio.run(server.handle(socket))
        assert all("result" not in reply for reply in socket.sent)
        assert server.compatibility_profile.version == version
        good = _FakeWebSocket([])
        asyncio.run(server._handle_request(good, 3, "initialize", {"clientInfo": {"name": "codex-tui", "version": version}}))
        assert good.sent[0]["result"]["userAgent"] == f"rpnh/codex-compat-{version}"
        assert server._thread_document(state)["cliVersion"] == version
    finally:
        server.close()


def test_candidate_response_defaults_match_schema_without_invented_facts(candidate):
    server, state, socket, _ = candidate
    fixture = Path(__file__).parent / "fixtures/codex/0.161.0"
    resume = _request(server, state, socket, "thread/resume")
    assert resume["collaborationMode"] is None and resume["disabledPluginIds"] == []
    assert resume["thread"]["cliVersion"] == "0.161.0"
    Draft7Validator(json.loads((fixture / "ThreadResumeResponse.json").read_text())).validate(resume)
    page = _object_request(candidate)
    assert page["data"][0]["startedAtMs"] is None and page["data"][0]["completedAtMs"] is None
    Draft7Validator(json.loads((fixture / "ThreadItemsListResponse.json").read_text())).validate(page)
    assert "PRIVATE_" not in json.dumps(page)


@pytest.mark.parametrize("order,kind,expected", [("asc", "user", "agent"), ("desc", "agent", "user"),
                                                 ("asc", "agent", None), ("desc", "user", None)])
@pytest.mark.parametrize("limit", [None, 0, 1, 100, 0xFFFFFFFF])
def test_two_safe_slots_exclusive_boundaries_no_fake_next_page(candidate, order, kind, expected, limit):
    server, state, socket, _ = candidate
    page = _object_request(candidate, kind=kind, sortDirection=order, limit=limit)
    assert page["nextCursor"] is None  # Exactly two slots: never manufacture a third item.
    if expected is None:
        assert page == {"data": [], "nextCursor": None, "backwardsCursor": None}
    else:
        assert len(page["data"]) == 1
        assert page["data"][0]["item"]["id"] == public_id(state.thread_id, 3, expected)
        token = page["backwardsCursor"]
        decoded = HistoryCursor.decode(token)
        assert decoded.anchor.inclusive and decoded.turn_filter == decoded.anchor.turn_ref
        reverse = _request(server, state, socket, "thread/items/list", turnId=public_id(state.thread_id, 3, "turn"),
                           cursor=token, sortDirection="desc" if order == "asc" else "asc")
        assert reverse["data"][0] == page["data"][0]
        assert all(e["turnId"] == public_id(state.thread_id, 3, "turn") for e in reverse["data"])


@pytest.mark.parametrize("cursor,turn", [
    ({"type": "item", "itemId": "x"}, None), ({"type": "item", "itemId": "x"}, ""),
    ({"type": "item", "itemId": "x"}, True), ({"type": "item", "itemId": "x"}, []),
    ({"type": "item", "itemId": "x"}, "x" * 257),
    ({"type": "item", "itemId": ""}, "x"), ({"type": "item", "itemId": True}, "x"),
    ({"type": "item", "itemId": 1}, "x"), ({"type": "item", "itemId": []}, "x"),
    ({"type": "item", "itemId": "x" * 257}, "x"), ({"type": "other", "itemId": "x"}, "x"),
    ({"itemId": "x"}, "x"), ({"type": "item"}, "x"),
    ({"type": "item", "itemId": "x", "root": "/private"}, "x"),
    ({"type": "item", "itemId": "x", "cut": {}}, "x"),
    ({"type": "item", "itemId": "x", "order": "asc"}, "x"),
    (True, "x"), ([], "x"), (1, "x"),
])
def test_bad_object_parser_rejects_before_cut_or_body(candidate, monkeypatch, cursor, turn):
    server, state, socket, _ = candidate
    monkeypatch.setattr(server, "_history_cut", lambda *_: pytest.fail("cut captured before validation"))
    with pytest.raises(ValueError, match="invalid history cursor"):
        _request(server, state, socket, "thread/items/list", cursor=cursor, turnId=turn)
    assert not socket.sent


@pytest.mark.parametrize("turn,kind,anchor_turn", [(2, "user", 2), (5, "user", 5), (3, "user", 4),
                                                  (3, "tool", 3), (3, "unknown", 3)])
def test_failed_pending_cross_turn_nonmember_rejected_without_hydration(candidate, monkeypatch, turn, kind, anchor_turn):
    server, state, socket, service = candidate
    accepted = service.accept_turn(thread_ref=service.capture_read_cut().thread_ref,
        expected_ordinal=5, idempotency_key="pending5", user_input={"text": "PRIVATE_PENDING", "required_task_kind": None})
    service.attach_attempt(thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref, idempotency_key="pending-attempt5")
    monkeypatch.setattr(state.session, "display_history_at", lambda *a, **kw: pytest.fail("bad anchor read body"))
    with pytest.raises(ValueError, match="not present"):
        _request(server, state, socket, "thread/items/list", turnId=public_id(state.thread_id, turn, "turn"),
                 cursor={"type": "item", "itemId": public_id(state.thread_id, anchor_turn, kind)})
    assert not socket.sent


def test_object_captures_once_after_old_resume_and_append_does_not_move_cut(candidate, monkeypatch):
    server, state, socket, service = candidate
    old = _request(server, state, socket, "thread/resume")
    old_cut = HistoryCursor.decode(old["itemsBackwardsCursor"]).cut
    _append(service, monkeypatch, 5)
    capture = server._history_cut
    calls = []
    def capture_then_append(bound):
        cut = capture(bound)
        calls.append(cut)
        _append(service, monkeypatch, 6)
        return cut
    monkeypatch.setattr(server, "_history_cut", capture_then_append)
    page = _object_request(candidate, ordinal=5)
    assert len(calls) == 1
    token = page["backwardsCursor"]
    assert HistoryCursor.decode(token).cut == calls[0] != old_cut
    assert page["data"][0]["item"]["id"] == public_id(state.thread_id, 5, "agent")
    monkeypatch.setattr(server, "_history_cut", lambda *_: pytest.fail("string recaptured head"))
    assert len(_walk(server, state, socket, "items", cursor=old["itemsBackwardsCursor"], sortDirection="desc", limit=1)) == 6
    assert len(_request(server, state, socket, "thread/items/list", turnId=public_id(state.thread_id, 5, "turn"),
                        cursor=token, sortDirection="desc")["data"]) == 2


def test_shared_string_cursor_and_ids_survive_same_root_reopen_across_profiles(candidate):
    server, state, socket, _ = candidate
    root, execution, thread_id = server.root, server.execution_config_path, state.thread_id
    token = _object_request(candidate)["backwardsCursor"]
    original = HistoryCursor.decode(token)
    server.close()
    for selector in (PINNED, CANDIDATE):
        reopened = frontend.CodexAppServer(root, execution, compatibility_profile=selector)
        try:
            rebound = next(iter(reopened._threads.values()))
            ws = _FakeWebSocket([])
            reopened._initialized_connections.add(id(ws))
            assert rebound.thread_id == thread_id
            page = _request(reopened, rebound, ws, "thread/items/list", cursor=token,
                            turnId=public_id(thread_id, 3, "turn"), sortDirection="desc")
            assert [e["item"]["id"] for e in page["data"]] == [public_id(thread_id, 3, k) for k in ("agent", "user")]
            assert HistoryCursor.decode(page["backwardsCursor"]).cut == original.cut
            if selector == PINNED:
                assert all(set(e) == {"turnId", "item"} for e in page["data"])
                resume = _request(reopened, rebound, ws, "thread/resume")
                assert "collaborationMode" not in resume and "disabledPluginIds" not in resume
                with pytest.raises(ValueError):
                    _object_request((reopened, rebound, ws, None))
        finally:
            reopened.close()


def test_turns_query_and_extra_root_fields_stay_rejected(candidate):
    server, state, socket, _ = candidate
    for method, extra in [("thread/turns/list", {}), ("thread/items/list", {"root": "/private"})]:
        with pytest.raises(ValueError):
            _request(server, state, socket, method, cursor={"type": "item", "itemId": "x"}, **extra)
    assert not socket.sent
