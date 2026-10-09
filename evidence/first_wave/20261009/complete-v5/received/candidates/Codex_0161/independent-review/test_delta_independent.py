"""Independent delta probes: real synthetic Registry, fake JSON transport only."""
from __future__ import annotations

import asyncio
import gc
import hashlib
import json
from uuid import UUID

import pytest

from cpn.frontend.codex_app_server import CodexAppServer
from cpn.frontend.codex_history import HistoryCursor, public_id
from cpn.rpnh.main_session import MainSession
from test_codex_compat import _FakeWebSocket, _local_profile
from test_codex_history import _append, _request, _walk

CANDIDATE = "candidate-0.161.0"


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    execution = _local_profile(tmp_path / "profiles")
    original = MainSession(tmp_path / "session", execution, task_control=object())
    for ordinal in range(1, 5):
        _append(original._main_thread, monkeypatch, ordinal,
                outcome="running" if ordinal == 2 else "terminal", private=ordinal == 1)
    server = CodexAppServer(original.root, execution, compatibility_profile=CANDIDATE)
    state = next(iter(server._threads.values()))
    socket = _FakeWebSocket([])
    server._initialized_connections.add(id(socket))
    yield server, state, socket, state.session._main_thread
    server.close()


def anchored(state, ordinal=3, kind="user", order="asc", **extra):
    return {"turnId": public_id(state.thread_id, ordinal, "turn"),
            "cursor": {"type": "item", "itemId": public_id(state.thread_id, ordinal, kind)},
            "sortDirection": order, **extra}


@pytest.mark.parametrize("kind,order,expected", [
    ("user", "asc", "agent"), ("user", "desc", None),
    ("agent", "asc", None), ("agent", "desc", "user"),
])
@pytest.mark.parametrize("limit", [0, 1, None, 0xFFFFFFFF])
def test_exact_exclusive_two_slot_semantics(candidate, kind, order, expected, limit):
    server, state, socket, _ = candidate
    page = _request(server, state, socket, "thread/items/list",
                    **anchored(state, kind=kind, order=order, limit=limit))
    assert page["nextCursor"] is None  # Never invent a third safe slot for coverage.
    if expected is None:
        assert page == {"data": [], "nextCursor": None, "backwardsCursor": None}
        return
    assert len(page["data"]) == 1
    entry = page["data"][0]
    assert entry["turnId"] == public_id(state.thread_id, 3, "turn")
    assert entry["item"]["id"] == public_id(state.thread_id, 3, expected)
    assert entry["startedAtMs"] is None and entry["completedAtMs"] is None
    token = HistoryCursor.decode(page["backwardsCursor"])
    assert token.anchor.inclusive and token.turn_filter == token.anchor.turn_ref
    assert token.anchor.turn_ordinal == 3  # Failed ordinal 2 must not compress IDs.
    assert token.order == ("desc" if order == "asc" else "asc")
    backward = _request(server, state, socket, "thread/items/list",
                        turnId=entry["turnId"], cursor=page["backwardsCursor"],
                        sortDirection=token.order)
    assert backward["data"][0] == entry


def test_object_captures_current_cut_once_and_excludes_later_append(candidate, monkeypatch):
    server, state, socket, service = candidate
    old = _request(server, state, socket, "thread/resume")
    old_cut = HistoryCursor.decode(old["itemsBackwardsCursor"]).cut
    _append(service, monkeypatch, 5)
    capture = server._history_cut
    cuts = []

    def once(current_state):
        cut = capture(current_state)
        cuts.append(cut)
        _append(service, monkeypatch, 6)
        return cut

    monkeypatch.setattr(server, "_history_cut", once)
    page = _request(server, state, socket, "thread/items/list", **anchored(state, ordinal=5))
    token = HistoryCursor.decode(page["backwardsCursor"])
    assert len(cuts) == 1 and token.cut == cuts[0] and token.cut != old_cut
    assert [row["item"]["id"] for row in page["data"]] == [public_id(state.thread_id, 5, "agent")]

    def no_recapture(*args, **kwargs):
        pytest.fail("string continuation recaptured current head")

    monkeypatch.setattr(server, "_history_cut", no_recapture)
    rows = _walk(server, state, socket, "items", cursor=old["itemsBackwardsCursor"],
                 sortDirection="desc", limit=1)
    assert len(rows) == 6 and all(row["turnId"] != public_id(state.thread_id, 5, "turn") for row in rows)


@pytest.mark.parametrize("change", ["source", "lease", "close"])
def test_object_body_delivery_rechecks_binding_inside_send_lock(candidate, change):
    server, state, socket, _ = candidate

    async def scenario():
        lock = server._send_locks.setdefault(id(socket), asyncio.Lock())
        await lock.acquire()
        task = asyncio.create_task(server._handle_request(
            socket, 101, "thread/items/list", {"threadId": state.thread_id, **anchored(state)}))
        await asyncio.sleep(0)
        if change == "source":
            state.session._registry_core.branch_id = "replacement-source"
        elif change == "lease":
            server._lease.path.unlink()
            server._lease.path.write_text("replacement lease")
        else:
            server.close()
        lock.release()
        with pytest.raises(ValueError, match="binding is unavailable"):
            await task

    asyncio.run(scenario())
    assert not socket.sent


def test_object_uses_ro_reader_and_safe_renderer_without_fact_writes(candidate, monkeypatch):
    server, state, socket, _ = candidate
    gc.collect()  # Finish fixture SQLite cleanup before the observation window.
    write_core = state.session._registry_core
    read_core = state.session.history_registry.core

    def fingerprint():
        return {str(p.relative_to(state.session.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in state.session.root.rglob("*") if p.is_file() and not p.name.endswith("-shm")}

    epoch = (read_core.event_store.max_ordinal(), read_core.event_store.writer_epoch)
    # SQLite may create an empty WAL/SHM on the first read-only connection.
    # Establish that coordination boundary before hashing authority files.
    before = fingerprint()

    def forbid(*args, **kwargs):
        pytest.fail("object request touched writer, live state, child, or raw refresh")

    for name in ("_refresh_from_authority", "_persist_state", "_persist_profile", "activate_for_execution",
                 "active_turn_snapshot", "reconcile_active_turn", "prepare_turn", "_registered_decision_child"):
        monkeypatch.setattr(state.session, name, forbid)
    monkeypatch.setattr(write_core, "begin", forbid)
    monkeypatch.setattr(write_core.event_store, "connect", forbid)
    monkeypatch.setattr(state.session._main_thread, "_inspect_child_registry", forbid)
    monkeypatch.setattr(server, "_observe_active_turn", forbid)
    original = read_core.event_store.connect
    opened = []

    def query_only():
        connection = original()
        assert read_core.read_only and connection.execute("PRAGMA query_only").fetchone()[0] == 1
        opened.append(True)
        return connection

    monkeypatch.setattr(read_core.event_store, "connect", query_only)
    page = _request(server, state, socket, "thread/items/list", **anchored(state, ordinal=1))
    assert page["data"] and "PRIVATE_" not in json.dumps(page)
    assert opened and fingerprint() == before
    assert epoch == (read_core.event_store.max_ordinal(), read_core.event_store.writer_epoch)


def test_cross_profile_reopen_keeps_uuid_native_ordinals_and_v1_cut(candidate):
    server, state, socket, _ = candidate
    candidate_resume = _request(server, state, socket, "thread/resume")
    token = candidate_resume["itemsBackwardsCursor"]
    original_thread = state.thread_id
    assert str(UUID(original_thread)) == original_thread
    expected = [public_id(original_thread, ordinal, kind) for ordinal in (4, 3, 1) for kind in ("agent", "user")]
    root, execution = server.root, server.execution_config_path
    server.close()
    pinned = CodexAppServer(root, execution)
    try:
        pstate = next(iter(pinned._threads.values()))
        psocket = _FakeWebSocket([])
        pinned._initialized_connections.add(id(psocket))
        assert pstate.thread_id == original_thread
        rows = _walk(pinned, pstate, psocket, "items", cursor=token, sortDirection="desc", limit=1)
        assert [row["item"]["id"] for row in rows] == expected
        assert all("startedAtMs" not in row and "completedAtMs" not in row for row in rows)
        resume = _request(pinned, pstate, psocket, "thread/resume")
        assert resume["thread"]["cliVersion"] == "0.155.0"
        with pytest.raises(ValueError):
            _request(pinned, pstate, psocket, "thread/items/list", **anchored(pstate))
    finally:
        pinned.close()


def test_bad_handshake_cannot_enable_candidate_history(candidate):
    server, state, _, _ = candidate
    socket = _FakeWebSocket([
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex-tui", "version": "0.155.0"}}},
        {"method": "initialized"},
        {"id": 2, "method": "thread/items/list", "params": {"threadId": state.thread_id, **anchored(state)}},
    ])
    asyncio.run(server.handle(socket))
    assert len(socket.sent) == 2 and all("error" in row for row in socket.sent)
    assert "question" not in json.dumps(socket.sent) and "answer" not in json.dumps(socket.sent)


@pytest.mark.parametrize("bad", [
    {}, {"type": "item"}, {"type": "turn", "itemId": "x"},
    {"type": "item", "itemId": ""}, {"type": "item", "itemId": True},
    {"type": "item", "itemId": "x", "root": "/PRIVATE_ANCHOR_PATH"},
    {"type": "item", "itemId": "x" * 10000},
])
def test_malformed_object_rejects_before_safe_body_hydration(candidate, monkeypatch, bad):
    server, state, socket, _ = candidate

    def forbid(*args, **kwargs):
        pytest.fail("invalid anchor reached body hydration")

    monkeypatch.setattr(state.session, "display_history_at", forbid)
    with pytest.raises(ValueError):
        _request(server, state, socket, "thread/items/list",
                 turnId=public_id(state.thread_id, 3, "turn"), cursor=bad)
    assert not socket.sent


@pytest.mark.parametrize("turn_ordinal,item_ordinal,item_kind", [
    (2, 2, "user"), (3, 1, "user"), (3, 3, "tool"), (5, 5, "user"),
])
def test_failed_future_cross_turn_and_non_slot_ids_never_resolve(candidate, turn_ordinal, item_ordinal, item_kind):
    server, state, socket, _ = candidate
    with pytest.raises(ValueError):
        _request(server, state, socket, "thread/items/list",
                 turnId=public_id(state.thread_id, turn_ordinal, "turn"),
                 cursor={"type": "item", "itemId": public_id(state.thread_id, item_ordinal, item_kind)})
    assert not socket.sent
