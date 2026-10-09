"""Pure fake runner/log tests. No native binary, socket, model, or subprocess."""
from pathlib import Path
import asyncio
import importlib.util
import json
import sys
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
import cpn.frontend.codex_app_server as frontend
from cpn.rpnh.task_control import TaskControl


def module(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("scenario,expected", [("object", 0), ("invalid_cursor", 0),
                                               ("unknown_rpc", 2), ("rpc_error", 2), ("child", 2)])
def test_gate_logger_and_fail_closed_lane_exit(tmp_path, monkeypatch, scenario, expected):
    runner = module("run_profile_gate")
    package = tmp_path / "package"
    (package / "native-gate").mkdir(parents=True)
    monkeypatch.setattr(runner, "__file__", str(package / "native-gate/run_profile_gate.py"))
    (package / "file-manifest.json").write_text(json.dumps({"source_files": [], "source_fingerprint": "fake"}))
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "gate.json").write_text(json.dumps({"source_fingerprint": "fake", "turns": 60, "pending_start": False}))
    log = tmp_path / "body-free.jsonl"
    monkeypatch.setattr(sys, "argv", ["gate", "--repo", str(tmp_path), "--fixture", str(fixture),
                                     "--codex", "/synthetic/codex", "--profile", "candidate-0.161.0", "--log", str(log)])
    accepted = []
    async def handle(self, ws, ident, method, params):
        accepted.append(params)
    async def send(self, ws, value, **kwargs):
        pass
    monkeypatch.setattr(frontend.CodexAppServer, "_handle_request", handle)
    monkeypatch.setattr(frontend.CodexAppServer, "_send", send)
    old_start = TaskControl.start
    def fake_run(*a, **kwargs):
        assert kwargs["compatibility_profile"] == "candidate-0.161.0"
        async def act():
            fake, ws = SimpleNamespace(), object()
            if scenario == "child":
                with pytest.raises(RuntimeError):
                    TaskControl.start(None)
                return
            token = {"type": "item", "itemId": "DO_NOT_LOG_RAW_ITEM"} if scenario == "object" else "DO_NOT_LOG_RAW_CURSOR"
            method = "thread/delete" if scenario == "unknown_rpc" else "thread/items/list"
            if scenario == "unknown_rpc":
                with pytest.raises(ValueError):
                    await frontend.CodexAppServer._handle_request(fake, ws, 1, method, {"cursor": token})
            else:
                await frontend.CodexAppServer._handle_request(fake, ws, 1, method, {"cursor": token})
            if scenario in ("unknown_rpc", "rpc_error"):
                await frontend.CodexAppServer._send(fake, ws, {"id": 1, "error": {"code": -32602, "message": "DO_NOT_LOG_ERROR"}})
        asyncio.run(act())
        return 0
    monkeypatch.setattr(frontend, "run_codex_frontend", fake_run)
    assert runner.main() == expected
    assert frontend.CodexAppServer._handle_request is handle and TaskControl.start is old_start
    text = log.read_text()
    assert "DO_NOT_LOG" not in text
    rows = [json.loads(x) for x in text.splitlines()]
    assert rows[-1]["exit_code"] == expected and rows[-1]["client_exit_code"] == 0
    if scenario == "object":
        assert accepted and rows[1]["cursor_edge"]["exact_item_keys"] is True


def sample_log():
    thread = "00112233-4455-6677-8899-aabbccddeeff"
    def pid(n, kind):
        return str(uuid5(NAMESPACE_URL, f"rpnh:codex:{thread}:{n}:active-{kind}"))
    def edge(token):
        return {"cut_ordinal": 100, "boundary_event": "evt-fixture", "token_sha256": token}
    rows = [
        {"phase": "start", "fixture": {"turns": 2, "pending_start": False}},
        {"phase": "request", "id": 1, "method": "thread/resume"},
        {"phase": "response", "id": 1, "method": "thread/resume", "error": None, "thread_id": thread,
         "thread_status": {"type": "idle"}, "turnsBackwardsCursor": edge("turn-initial"), "itemsBackwardsCursor": edge("item-initial")},
        {"phase": "request", "id": 2, "method": "thread/turns/list", "limit": 5, "view": "notLoaded", "order": "desc", "cursor_edge": edge("turn-initial")},
        {"phase": "response", "id": 2, "method": "thread/turns/list", "count": 2, "ids": [pid(2, "turn"), pid(1, "turn")]},
    ]
    for i, n in enumerate((2, 1), start=3):
        rows.extend([
            {"phase": "request", "id": i, "method": "thread/items/list", "limit": 2, "order": "desc", "cursor_edge": edge("item-initial" if i == 3 else str(i))},
            {"phase": "response", "id": i, "method": "thread/items/list", "count": 2,
             "ids": [pid(n, "agent"), pid(n, "user")], "item_turn_ids": [pid(n, "turn")] * 2,
             "backwardsCursor": edge("back" + str(i)), "nextCursor": edge(str(i + 1)) if n == 2 else None},
        ])
    rows.append({"phase": "exit", "exit_code": 0})
    return rows


def test_dynamic_item_limits_are_not_forced_to_100_or_native_pass():
    result = module("analyze_log").analyze(sample_log())
    assert result["status"] == "RPC_COVERAGE_COMPLETE_UI_REVIEW_REQUIRED"
    assert result["native_certification"] == "NOT_ESTABLISHED_BY_THIS_ANALYZER"


@pytest.mark.parametrize("mutation", ["truncated", "duplicate", "cut", "request_cut", "wrong_chain", "limit", "error", "no_advance", "denied"])
def test_incomplete_or_bad_trace_never_certifies(mutation):
    rows = sample_log()
    if mutation == "truncated":
        rows = rows[:-3] + [rows[-1]]
    elif mutation == "duplicate":
        rows[-2]["ids"] = rows[-4]["ids"]
    elif mutation == "cut":
        rows[-2]["backwardsCursor"]["cut_ordinal"] = 101
    elif mutation == "request_cut":
        rows[-3]["cursor_edge"]["cut_ordinal"] = 101
    elif mutation == "wrong_chain":
        rows[-3]["cursor_edge"]["token_sha256"] = "unissued"
    elif mutation == "limit":
        rows[-3]["limit"] = 101
    elif mutation == "error":
        rows[-2]["error"] = -32602
    elif mutation == "no_advance":
        rows[-4]["nextCursor"]["token_sha256"] = rows[-5]["cursor_edge"]["token_sha256"]
    else:
        rows.append({"phase": "execution_denied", "method": "thread/delete"})
    assert module("analyze_log").analyze(rows)["status"] == "INCOMPLETE"


def test_dynamic_turn_metadata_page_can_interleave_item_chain():
    rows = sample_log()
    first = rows[4]
    second_turn = first["ids"].pop()
    first["count"] = 1
    continuation = {"cut_ordinal": 100, "boundary_event": "evt-fixture", "token_sha256": "turn-next"}
    first["nextCursor"] = continuation
    rows[7:7] = [
        {"phase": "request", "id": 20, "method": "thread/turns/list", "limit": 1,
         "order": "desc", "view": "notLoaded", "cursor_edge": continuation},
        {"phase": "response", "id": 20, "method": "thread/turns/list", "count": 1,
         "ids": [second_turn], "nextCursor": None},
    ]
    assert module("analyze_log").analyze(rows)["status"] == "RPC_COVERAGE_COMPLETE_UI_REVIEW_REQUIRED"
