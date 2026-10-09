"""Real Registry, owner lifecycle, pure RPC/schema checks; no native socket/provider."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import gc
import sqlite3
import time
from pathlib import Path

from jsonschema import Draft7Validator
import pytest

from cpn.frontend.codex_app_server import ActiveTurn, CodexAppServer, _ui_id
from cpn.frontend.codex_history import HistoryCursor, INITIAL_VIEW, PREFIX, codex_thread_id, page_limit, public_id
from cpn.rpnh.main_session import MainDecision, MainSession, MainTurnReconciliation
from cpn.rpnh.registry.main_thread import MainThreadAuthorityError
from test_codex_compat import _FakeWebSocket, _local_profile
from test_main_thread_registry import _install_observation


def _append(service, monkeypatch, ordinal, *, outcome="terminal", private=False, text=None):
    head = service.capture_read_cut().thread_ref
    accepted = service.accept_turn(
        thread_ref=head, expected_ordinal=ordinal, idempotency_key=f"accept-{ordinal}",
        user_input={"text": text or f"question {ordinal}", "required_task_kind": None,
                    "native_plugins": {"private": "PRIVATE_PLUGIN", "profile": "PRIVATE_PROFILE"}})
    running = service.attach_attempt(thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref,
                                     idempotency_key=f"attempt-{ordinal}")
    _install_observation(monkeypatch, service, outcome)
    receipt = service.record_execution_receipt(
        turn_ref=running.turn_ref, idempotency_key=f"receipt-{ordinal}", allow_running=outcome == "running")
    arguments = {"thread_ref": running.thread_ref, "turn_ref": running.turn_ref,
                 "receipt_ref": receipt, "idempotency_key": f"commit-{ordinal}"}
    if outcome == "running":
        return service.commit_execution_failure(**arguments)
    if outcome == "stopped_by_owner":
        return service.commit_interruption(**arguments)
    return service.commit_terminal_answer(**arguments, answer={
        "reply": f"answer {ordinal}", "protocol_valid": ordinal != 3,
        "task": None if not private else {"kind": "single_agent", "prompt": "PRIVATE_PROMPT",
              "stages": [{"stage_id": "worker", "instruction": "PRIVATE_INSTRUCTION"}],
              "workflow_graph": None}})


@pytest.fixture
def owner(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    session = MainSession(tmp_path / "session", execution, task_control=object())
    service = session._main_thread
    for ordinal in range(1, 5):
        _append(service, monkeypatch, ordinal,
                outcome="running" if ordinal == 2 else "terminal", private=ordinal == 1)
    server = CodexAppServer(session.root, execution)
    state = next(iter(server._threads.values()))
    socket = _FakeWebSocket([])
    server._initialized_connections.add(id(socket))
    yield server, state, socket, state.session._main_thread
    server.close()


def _request(server, state, socket, method, **params):
    socket.sent.clear()
    asyncio.run(server._handle_request(socket, 1, method, {"threadId": state.thread_id, **params}))
    return socket.sent[-1]["result"]


def _walk(server, state, socket, query, **params):
    entries, seen = [], set()
    while True:
        page = _request(server, state, socket, f"thread/{query}/list", **params)
        entries.extend(page["data"])
        token = page["nextCursor"]
        if token is None:
            return entries
        assert token not in seen and page["data"]
        seen.add(token)
        params["cursor"] = token


@pytest.mark.parametrize("version", ["0.155.0", "0.161.0"])
def test_real_rpc_pages_validate_official_schema_and_safe_fields(owner, version):
    server, state, socket, _ = owner
    fixture = Path(__file__).parent / "fixtures" / "codex" / version
    for query, response in (("turns", "ThreadTurnsListResponse"), ("items", "ThreadItemsListResponse")):
        page = _request(server, state, socket, f"thread/{query}/list", limit=2)
        Draft7Validator(json.loads((fixture / f"{response}.json").read_text())).validate(page)
        text = json.dumps(page)
        assert "PRIVATE_" not in text and "question" in text and "answer" in text
    resume = _request(server, state, socket, "thread/resume")
    Draft7Validator(json.loads((fixture / "ThreadResumeResponse.json").read_text())).validate(resume)
    assert resume["thread"]["turns"] == []


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("limit", [1, 2, 3, 100])
def test_pages_complete_in_both_directions_with_native_ordinal_gaps(owner, order, limit):
    server, state, socket, _ = owner
    ordinals = [1, 3, 4] if order == "asc" else [4, 3, 1]
    turns = _walk(server, state, socket, "turns", limit=limit, sortDirection=order, itemsView="notLoaded")
    assert [turn["id"] for turn in turns] == [public_id(state.thread_id, n, "turn") for n in ordinals]
    assert all(turn["items"] == [] and turn["itemsView"] == "notLoaded" for turn in turns)
    items = _walk(server, state, socket, "items", limit=limit, sortDirection=order)
    expected = [(n, kind) for n in [1, 3, 4] for kind in ["user", "agent"]]
    if order == "desc":
        expected.reverse()
    assert [entry["item"]["id"] for entry in items] == [public_id(state.thread_id, n, k) for n, k in expected]
    assert len({entry["item"]["id"] for entry in items}) == 6
    assert "no child task was launched" in json.dumps(items)
    assert all(turn["completedAt"] is None for turn in turns)


def test_defaults_limits_views_filter_and_backwards_inclusive(owner, monkeypatch):
    server, state, socket, _ = owner
    assert [page_limit(n) for n in (None, 0, 1, 100, 101, 0xFFFFFFFF)] == [25, 1, 1, 100, 100, 100]
    for n in (True, -1, 1.0, "1", 0x100000000):
        with pytest.raises(ValueError):
            page_limit(n)
    for view in ("summary", "full"):
        page = _request(server, state, socket, "thread/turns/list", itemsView=view, limit=2)
        assert all(t["itemsView"] == view and len(t["items"]) == 2 for t in page["data"])
        backwards = _request(server, state, socket, "thread/turns/list", itemsView=view,
                             cursor=page["backwardsCursor"], sortDirection="asc")
        assert backwards["data"][0]["id"] == page["data"][0]["id"]
    for order in ("asc", "desc"):
        page = _request(server, state, socket, "thread/items/list", limit=2, sortDirection=order)
        backwards = _request(server, state, socket, "thread/items/list", cursor=page["backwardsCursor"],
                             sortDirection="asc" if order == "desc" else "desc")
        assert backwards["data"][0] == page["data"][0]
    selected = _walk(server, state, socket, "items", limit=1, turnId=public_id(state.thread_id, 3, "turn"))
    assert len(selected) == 2 and all(e["turnId"] == selected[0]["turnId"] for e in selected)
    def forbidden(*a, **kw):
        pytest.fail("notLoaded attempted safe body hydration")
    monkeypatch.setattr(state.session, "display_history_at", forbidden)
    assert _request(server, state, socket, "thread/turns/list", itemsView="notLoaded")["data"]


def test_resume_shared_cut_inclusive_views_and_append_stability(owner, monkeypatch):
    server, state, socket, service = owner
    resumed = _request(server, state, socket, "thread/resume")
    t = HistoryCursor.decode(resumed["turnsBackwardsCursor"])
    i = HistoryCursor.decode(resumed["itemsBackwardsCursor"])
    assert t.cut == i.cut and t.anchor.inclusive and i.anchor.inclusive
    assert t.items_view == INITIAL_VIEW
    _append(service, monkeypatch, 5)
    for view in ("notLoaded", "summary", "full"):
        turns = _walk(server, state, socket, "turns", cursor=resumed["turnsBackwardsCursor"],
                      itemsView=view, limit=1)
        assert len(turns) == 3 and turns[0]["id"] == public_id(state.thread_id, 4, "turn")
    assert len(_walk(server, state, socket, "items", cursor=resumed["itemsBackwardsCursor"],
                     sortDirection="desc", limit=1)) == 6
    assert len(_walk(server, state, socket, "items")) == 8


def test_empty_resume_stays_empty_after_commit(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    original = MainSession(tmp_path / "empty", execution, task_control=object())
    server = CodexAppServer(original.root, execution)
    try:
        state = next(iter(server._threads.values()))
        socket = _FakeWebSocket([])
        server._initialized_connections.add(id(socket))
        resumed = _request(server, state, socket, "thread/resume")
        _append(state.session._main_thread, monkeypatch, 1)
        for query, token in (("turns", resumed["turnsBackwardsCursor"]), ("items", resumed["itemsBackwardsCursor"])):
            assert _request(server, state, socket, f"thread/{query}/list", cursor=token,
                            sortDirection="desc") == {"data": [], "nextCursor": None, "backwardsCursor": None}
        assert len(_request(server, state, socket, "thread/items/list")["data"]) == 2
    finally:
        server.close()


def test_cursor_wrong_query_direction_view_filter_source_and_version_rejected(owner, tmp_path, monkeypatch):
    server, state, socket, _ = owner
    first = _request(server, state, socket, "thread/turns/list", limit=1, itemsView="notLoaded")
    token = first["nextCursor"]
    for query, extra in (("items", {}), ("turns", {"sortDirection": "asc", "itemsView": "notLoaded"}),
                         ("turns", {"itemsView": "full"})):
        with pytest.raises(ValueError):
            _request(server, state, socket, f"thread/{query}/list", cursor=token, **extra)
    item = _request(server, state, socket, "thread/items/list", limit=1)
    with pytest.raises(ValueError):
        _request(server, state, socket, "thread/items/list", cursor=item["nextCursor"],
                 turnId=public_id(state.thread_id, 1, "turn"))
    parsed = HistoryCursor.decode(token)
    for bad in (replace(parsed, thread_id="another-thread"),
                replace(parsed, cut=replace(parsed.cut, branch_id="other"),
                        anchor=replace(parsed.anchor, cut=replace(parsed.cut, branch_id="other")))):
        with pytest.raises((ValueError, MainThreadAuthorityError)):
            _request(server, state, socket, "thread/turns/list", cursor=bad.encode(), itemsView="notLoaded")
    with pytest.raises(ValueError):
        _request(server, state, socket, "thread/items/list", cursor={"type": "item", "itemId": "x"},
                 turnId=public_id(state.thread_id, 1, "turn"))


@pytest.mark.parametrize("bad", ["", "!", PREFIX + "A", PREFIX + "a" * 4096,
                                 PREFIX + "e30=", None, True, [], {}])
def test_malformed_cursor_is_bounded_and_rejected(bad):
    with pytest.raises(ValueError, match="invalid history cursor"):
        HistoryCursor.decode(bad)


def test_duplicate_json_keys_and_initial_nonboundary_anchor_rejected(owner):
    server, state, socket, _ = owner
    token = _request(server, state, socket, "thread/resume")["turnsBackwardsCursor"]
    encoded = token[len(PREFIX):]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    malformed = b'{"version": 1,' + raw[1:]
    duplicate = PREFIX + base64.urlsafe_b64encode(malformed).decode().rstrip("=")
    with pytest.raises(ValueError):
        HistoryCursor.decode(duplicate)
    parsed = HistoryCursor.decode(token)
    earlier = state.session._main_thread.project_thread_at(parsed.cut).turns[0]
    wrong = replace(parsed, anchor=replace(parsed.anchor, turn_ref=earlier.turn_ref, turn_ordinal=earlier.ordinal))
    with pytest.raises(ValueError, match="cut boundary"):
        _request(server, state, socket, "thread/turns/list", cursor=wrong.encode())


def test_same_cut_annotation_and_safe_renderer_never_read_live_child(owner, monkeypatch):
    server, state, socket, service = owner
    before = _request(server, state, socket, "thread/resume")
    cut = HistoryCursor.decode(before["turnsBackwardsCursor"]).cut
    turn = service.project_thread_at(cut).turns[0]
    service.register_child_registry_link(task_control_id="task-visible", task_kind="single_agent",
        registry_relative_path="tasks/runs/child", origin_main_turn_ref=turn.turn_ref, idempotency_key="link")
    after = service.capture_read_cut()
    assert cut.thread_ref == after.thread_ref and cut != after
    def forbidden(*a, **kw):
        pytest.fail("history touched live task control, raw current projection or child")
    monkeypatch.setattr(state.session, "_registered_decision_child", forbidden)
    monkeypatch.setattr(state.session, "_refresh_from_authority", forbidden)
    monkeypatch.setattr(state.session._main_thread, "_inspect_child_registry", forbidden)
    old = state.session.display_history_at(cut, (turn.turn_ref,))[0]
    new = state.session.display_history_at(after, (turn.turn_ref,))[0]
    assert old.assistant_text == "answer 1"
    assert new.assistant_text == "answer 1\n\n[launched task-visible: single_agent]"
    assert "PRIVATE_" not in repr(new)


def _fingerprint(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.name.endswith("-shm")}


def test_history_read_is_pure_with_source_rechecks_and_optional_budgets(owner, monkeypatch):
    server, state, socket, _ = owner
    core = state.session._registry_core
    read_core = state.session.history_registry.core
    # Fixture writers are finished. Collect their already-unreachable SQLite
    # connections BEFORE the observation window, never in the product reader.
    gc.collect()
    counters = (read_core.event_store.max_ordinal(), read_core.event_store.writer_epoch)
    before = _fingerprint(state.session.root)
    def forbidden(*a, **kw):
        pytest.fail("pure history attempted execution, mutation, live observation or refresh")
    for name in ("_refresh_from_authority", "_persist_state", "_persist_profile", "activate_for_execution",
                 "active_turn_snapshot", "reconcile_active_turn", "prepare_turn"):
        monkeypatch.setattr(state.session, name, forbidden)
    monkeypatch.setattr(core, "begin", forbidden)
    monkeypatch.setattr(core.event_store, "connect", forbidden)
    original_connect = read_core.event_store.connect
    readonly_connections = []
    def checked_connect():
        assert read_core.read_only and read_core.event_store.read_only
        connection = original_connect()
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        readonly_connections.append(True)
        return connection
    monkeypatch.setattr(read_core.event_store, "connect", checked_connect)
    monkeypatch.setattr(server, "_observe_active_turn", forbidden)
    for query in ("turns", "items"):
        assert _walk(server, state, socket, query, limit=1)
    full = _request(server, state, socket, "thread/read", includeTurns=True)
    assert len(full["thread"]["turns"]) == 3
    assert _fingerprint(state.session.root) == before
    assert counters == (read_core.event_store.max_ordinal(), read_core.event_store.writer_epoch)
    assert readonly_connections
    server.history_response_max_bytes = 10
    with pytest.raises(ValueError, match="response exceeds"):
        _request(server, state, socket, "thread/items/list")
    assert not socket.sent
    server.history_response_max_bytes = None
    server.history_object_max_bytes = 1
    with pytest.raises(MainThreadAuthorityError):
        _request(server, state, socket, "thread/items/list")


@pytest.mark.parametrize("change", ["close", "lease", "root", "database", "objects", "source"])
def test_owner_and_source_lifecycle_rechecked(owner, change, tmp_path):
    server, state, socket, _ = owner
    if change == "close":
        server.close()
    elif change == "lease":
        server._lease.path.unlink()
        server._lease.path.write_text("new lock")
    elif change == "root":
        state.session.root = tmp_path
    elif change in ("database", "objects"):
        path = state.session._registry_core.event_store.path if change == "database" else state.session._registry_core.object_store.root
        path.rename(path.with_name(path.name + ".retained"))
        if change == "database":
            path.write_bytes(b"replacement")
        else:
            path.mkdir()
    else:
        state.session._registry_core.branch_id = "another-source"
    with pytest.raises(ValueError, match="binding is unavailable"):
        _request(server, state, socket, "thread/items/list")
    assert not socket.sent


def test_close_while_waiting_for_send_lock_rejects_body(owner):
    server, state, socket, _ = owner
    async def run():
        lock = server._send_locks.setdefault(id(socket), asyncio.Lock())
        await lock.acquire()
        task = asyncio.create_task(server._handle_request(socket, 1, "thread/items/list", {"threadId": state.thread_id}))
        await asyncio.sleep(0)
        server.close()
        lock.release()
        with pytest.raises(ValueError, match="binding is unavailable"):
            await task
    asyncio.run(run())
    assert not socket.sent


def test_old_cursor_survives_owner_restart_with_same_native_source(owner):
    server, state, socket, _ = owner
    first = _request(server, state, socket, "thread/items/list", limit=1)
    path, execution = state.session.root, state.session.execution_config_path
    server.close()
    reopened = CodexAppServer(path, execution)
    try:
        again = next(iter(reopened._threads.values()))
        opened = _FakeWebSocket([])
        reopened._initialized_connections.add(id(opened))
        rest = _walk(reopened, again, opened, "items", cursor=first["nextCursor"], limit=1)
        assert len(rest) == 5 and all(e["item"]["id"] != first["data"][0]["item"]["id"] for e in rest)
    finally:
        reopened.close()


def test_ids_match_live_namespace_without_compacting_failed_ordinals(owner):
    server, state, socket, _ = owner
    data = _walk(server, state, socket, "turns", sortDirection="asc", itemsView="full")
    for turn, ordinal in zip(data, (1, 3, 4), strict=True):
        assert turn["id"] == _ui_id(state.thread_id, ordinal, "active-turn")
        assert turn["items"][0]["id"] == _ui_id(state.thread_id, ordinal, "active-user")
        assert turn["items"][1]["id"] == _ui_id(state.thread_id, ordinal, "active-agent")


def test_schema_0161_anchor_is_not_0155_and_pin_stays_fixed():
    root = Path(__file__).parent / "fixtures" / "codex"
    document = {"threadId": "t", "turnId": "turn", "cursor": {"type": "item", "itemId": "i"}}
    for version, valid in (("0.155.0", False), ("0.161.0", True)):
        validator = Draft7Validator(json.loads((root / version / "ThreadItemsListParams.json").read_text()))
        assert validator.is_valid(document) is valid
    from cpn.frontend.codex_app_server import CODEX_FRONTEND_VERSION
    assert CODEX_FRONTEND_VERSION == "0.155.0"


def test_large_legal_body_has_no_implicit_four_mib_cap(owner, monkeypatch):
    server, state, socket, service = owner
    text = "large public " + "x" * (4 * 1024 * 1024 + 1)
    _append(service, monkeypatch, 5, text=text)
    page = _request(server, state, socket, "thread/items/list", limit=1,
                    turnId=public_id(state.thread_id, 5, "turn"))
    assert page["data"][0]["item"]["content"][0]["text"] == text
    server.history_object_max_bytes = 4 * 1024 * 1024
    with pytest.raises(MainThreadAuthorityError):
        _request(server, state, socket, "thread/items/list")


def test_native_hydration_requires_unique_committed_exact_members(owner, monkeypatch):
    server, state, socket, service = owner
    cut = service.capture_read_cut()
    member = service.project_thread_at(cut).turns[0].turn_ref
    with pytest.raises(ValueError):
        state.session.display_history_at(cut, (member, member))
    with pytest.raises(ValueError):
        state.session.display_history_at(cut, (member,) * 101)
    appended = _append(service, monkeypatch, 5)
    with pytest.raises(MainThreadAuthorityError, match="not committed"):
        state.session.display_history_at(cut, (appended.turn_ref,))
    accepted = service.accept_turn(thread_ref=appended.thread_ref, expected_ordinal=6,
                                  user_input={"text": "not public yet", "required_task_kind": None},
                                  idempotency_key="accepted-only")
    new_cut = service.capture_read_cut()
    with pytest.raises(MainThreadAuthorityError, match="not committed"):
        state.session.display_history_at(new_cut, (accepted.turn_ref,))
    assert "not public yet" not in json.dumps(_request(server, state, socket, "thread/items/list"))


def test_resume_live_hook_separate_from_pure_page_requests(owner, monkeypatch):
    server, state, socket, _ = owner
    calls = []
    async def live(websocket, bound):
        calls.append((websocket, bound))
    monkeypatch.setattr(server, "_observe_active_turn", live)
    _request(server, state, socket, "thread/resume")
    assert calls == [(socket, state)]
    _request(server, state, socket, "thread/turns/list")
    _request(server, state, socket, "thread/items/list")
    _request(server, state, socket, "thread/read", includeTurns=True)
    assert calls == [(socket, state)]


def test_terminal_notification_and_cold_history_share_exact_native_ordinal_ids(owner, monkeypatch):
    server, state, socket, service = owner
    _append(service, monkeypatch, 5)
    active = ActiveTurn(public_id(state.thread_id, 5, "turn"), public_id(state.thread_id, 5, "agent"),
                        5, "question 5", None, 1234, None)
    state.active = active
    asyncio.run(server._finalize_active_turn(
        socket, state, active, MainTurnReconciliation("committed", None, MainDecision("answer 5", None))))
    notification = next(item for item in socket.sent if item.get("method") == "turn/completed")
    live = notification["params"]["turn"]
    cold = _request(server, state, socket, "thread/turns/list", limit=1, itemsView="full")["data"][0]
    assert live["id"] == cold["id"]
    assert [item["id"] for item in live["items"]] == [item["id"] for item in cold["items"]]
    assert state.active is None


@pytest.mark.parametrize("params", [
    {"sortDirection": False}, {"sortDirection": ""}, {"itemsView": ""},
    {"itemsView": []}, {"limit": True}, {"limit": -1}, {"cursor": ""}, {"path": "/tmp/private"},
])
def test_invalid_params_never_fall_back_to_current_history(owner, params):
    server, state, socket, _ = owner
    with pytest.raises((ValueError, TypeError)):
        _request(server, state, socket, "thread/turns/list", **params)
    assert not socket.sent


def test_rpc_error_does_not_echo_cursor_private_material(owner):
    server, state, socket, _ = owner
    wire = _FakeWebSocket([{"id": 4, "method": "thread/items/list",
                           "params": {"threadId": state.thread_id, "cursor": "PRIVATE_CURSOR_PATH"}}])
    server._initialized_connections.add(id(wire))
    asyncio.run(server.handle(wire))
    assert wire.sent == [{"id": 4, "error": {"code": -32602, "message": "history request rejected"}}]


def test_uninitialized_unknown_thread_and_profile_change(owner):
    server, state, socket, _ = owner
    unopened = _FakeWebSocket([])
    with pytest.raises(ValueError):
        _request(server, state, unopened, "thread/items/list")
    with pytest.raises(ValueError):
        server._history_state(socket, "unknown")
    state.session.execution_config_path = state.session.root / "different-owner-profile.json"
    # A valid in-process model selection changes profile/config, not the source.
    assert _request(server, state, socket, "thread/items/list")["data"]


def test_codex_wire_uuid_preserves_native_and_generic_identity_and_rejects_old_alias(owner, tmp_path):
    from uuid import UUID
    from cpn.rpnh.session_access import stable_frontend_session_id
    server, state, socket, _ = owner
    native_before = state.session._main_thread.capture_read_cut()
    generic = stable_frontend_session_id(state.session.root)
    assert generic.startswith("ses_")
    assert state.thread_id == codex_thread_id(generic) == str(UUID(hex=generic[4:]))
    assert UUID(state.thread_id).hex == generic[4:]
    assert codex_thread_id("ses_" + "1" * 32) != state.thread_id
    with pytest.raises(ValueError):
        server._history_state(socket, generic)
    current = _request(server, state, socket, "thread/items/list", limit=1)
    old_token = replace(HistoryCursor.decode(current["nextCursor"]), thread_id=generic).encode()
    with pytest.raises(ValueError, match="query differs"):
        _request(server, state, socket, "thread/items/list", cursor=old_token)
    assert state.session._main_thread.capture_read_cut() == native_before
    assert server._thread_document(state)["id"] == state.thread_id


def test_history_connection_metrics_are_readonly(owner, monkeypatch, capsys):
    server, state, socket, _ = owner
    opened = []
    original = sqlite3.connect
    def observe(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append((str(args[0]), kwargs.get("uri", False)))
        return connection
    monkeypatch.setattr(sqlite3, "connect", observe)
    start = time.perf_counter()
    _request(server, state, socket, "thread/items/list", limit=1)
    elapsed = time.perf_counter() - start
    assert opened and all(uri and "mode=ro" in path for path, uri in opened)
    with capsys.disabled():
        print(json.dumps({"scenario": "three_committed_one_failed_items_limit_1",
                          "connections": len(opened), "writable_connections": 0,
                          "elapsed_seconds": round(elapsed, 6),
                          "claim": "one-fixture measurement, not a scalability bound"}))


def test_rebound_actual_event_store_path_and_readonly_flags_fail_closed(owner, tmp_path):
    server, state, socket, _ = owner
    store = state.session._registry_core.event_store
    actual = store.path
    clone = tmp_path / "clone.sqlite3"
    clone.write_bytes(actual.read_bytes())
    store.path = clone
    with pytest.raises(ValueError, match="binding is unavailable"):
        _request(server, state, socket, "thread/items/list")
    store.path = actual
    state.session.history_registry.core.event_store.read_only = False
    with pytest.raises(ValueError, match="binding is unavailable"):
        _request(server, state, socket, "thread/items/list")
