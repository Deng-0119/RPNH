from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.frontend.codex_app_server import (
    ActiveTurn,
    CodexAppServer,
    CodexCompatibilityError,
    ThreadState,
    _run_codex_frontend_async,
)
from cpn.rpnh.agent_tasks import AgentStage
from cpn.rpnh.main_session import (
    MainDecision,
    MainSession,
    MainTaskDecision,
    MainTurnReconciliation,
    MainTurnSnapshot,
)
from cpn.rpnh.user_config import profile_for_path


def _local_profile(root: Path, *, model: str = "test-model") -> Path:
    adapters = root / "adapters"
    executions = root / "execution"
    adapters.mkdir(parents=True)
    executions.mkdir(parents=True)
    adapter = adapters / "local.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": model,
        "argv": ["/usr/bin/true"],
        "probe_argv": ["/usr/bin/true"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution = executions / "local-test.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": model,
        "adapter_config_path": "../adapters/local.json",
        "timeout_seconds": 60,
        "max_output_tokens": 1024,
        "max_response_bytes": 8192,
    }), encoding="utf-8")
    return execution


class _FakeWebSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = iter(messages)
        self.sent: list[dict[str, object]] = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            value = next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        return json.dumps(value)

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))


def _write_thread_projection(
        root: Path, *, thread_id: str = "thread-1", turns: object = None,
        execution: Path | None = None,
) -> Path:
    thread_root = root / "threads" / thread_id
    thread_root.mkdir(parents=True, exist_ok=True)
    (thread_root / "thread_state.json").write_text(json.dumps({
        "schema_version": "rpnh/codex_thread_state/v1",
        "thread_id": thread_id,
        "model_id": "local-process/test-model",
        "cwd": str(root),
        "created_at": 100,
        "preview": "",
        "name": None,
        "turns": [] if turns is None else turns,
        "attachments": [],
    }), encoding="utf-8")
    if execution is not None:
        (thread_root / "execution_profile.json").write_text(json.dumps({
            "schema_version": "rpnh/main_session_profile/v1",
            "execution_config_path": str(execution.resolve()),
        }), encoding="utf-8")
    return thread_root


class _RecoverySession:
    def __init__(
            self, root: Path, execution: Path,
            reconciliation: MainTurnReconciliation,
            history: list[tuple[str, str]] | None = None,
            committed_history: list[tuple[str, str]] | None = None,
    ) -> None:
        self.root = root
        self.child_path_root = root / "main"
        self.execution_config_path = execution.resolve()
        self.history = list(history or [])
        self.reconciliation = reconciliation
        self.committed_history = committed_history
        self.reconcile_calls = 0

    def reconcile_active_turn(self) -> MainTurnReconciliation:
        self.reconcile_calls += 1
        if self.committed_history is not None:
            self.history = list(self.committed_history)
        return self.reconciliation


def test_codex_0155_handshake_projects_only_rpnh_backend(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    socket = _FakeWebSocket([
        {
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "codex-tui", "version": "0.155.0"},
            },
        },
        {"method": "initialized"},
        {"id": 1, "method": "account/read", "params": {}},
        {"id": 2, "method": "model/list", "params": {}},
        {
            "id": 3,
            "method": "thread/start",
            "params": {
                "model": "local-process/test-model", "cwd": str(tmp_path)},
        },
        {"id": 4, "method": "thread/list", "params": {}},
    ])
    asyncio.run(server.handle(socket))

    responses = {
        item["id"]: item["result"]
        for item in socket.sent if "id" in item
    }
    assert responses["initialize"]["userAgent"].startswith("rpnh/")
    assert responses[1] == {"account": None, "requiresOpenaiAuth": False}
    assert responses[2]["data"][0]["model"] == "local-process/test-model"
    thread = responses[3]["thread"]
    assert thread["modelProvider"] == "rpnh"
    assert thread["source"] == "appServer"
    assert responses[4]["data"][0]["id"] == thread["id"]


def test_codex_handshake_rejects_unaccepted_initialized_and_correlates_errors(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    socket = _FakeWebSocket([
        {"method": "initialized"},
        {"id": 1, "method": "model/list", "params": {}},
        {"id": 2, "method": "model/list", "params": 1},
    ])

    asyncio.run(server.handle(socket))

    response = next(item for item in socket.sent if item.get("id") == 1)
    assert "successfully initialize" in response["error"]["message"]
    malformed = next(item for item in socket.sent if item.get("id") == 2)
    assert malformed["error"]["message"] == (
        "JSON-RPC params must be an object")

    rejected = _FakeWebSocket([
        {"id": "bad", "method": "initialize", "params": {
            "clientInfo": {"name": "codex-tui", "version": "wrong"}}},
        {"method": "initialized"},
        {"id": 3, "method": "model/list", "params": {}},
    ])
    asyncio.run(server.handle(rejected))
    assert "requires codex-tui" in next(
        item for item in rejected.sent if item.get("id") == "bad"
    )["error"]["message"]
    assert "successfully initialize" in next(
        item for item in rejected.sent if item.get("id") == 3
    )["error"]["message"]


def test_rejected_turn_and_failed_settings_keep_the_current_profile(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_a = _local_profile(tmp_path / "profile-a", model="model-a")
    execution_b = _local_profile(tmp_path / "profile-b", model="model-b")
    server = CodexAppServer(tmp_path / "session", execution_a)
    profile_b = profile_for_path(execution_b)
    server._profiles_by_model_id[profile_b.selection_id] = profile_b
    socket = _FakeWebSocket([])
    asyncio.run(server._start_thread(socket, "start", {
        "model": server.frontend_model_id,
        "cwd": str(tmp_path),
    }))
    thread_id = next(
        item["result"]["thread"]["id"]
        for item in socket.sent if item.get("id") == "start")
    state = server._threads[thread_id]
    original = (state.model_id, state.session.execution_config_path)

    with pytest.raises(ValueError, match="nonempty input"):
        asyncio.run(server._start_turn(socket, "turn", {
            "threadId": thread_id,
            "model": profile_b.selection_id,
            "input": [],
        }))
    assert (state.model_id, state.session.execution_config_path) == original

    def fail_config(_self, _path):
        raise OSError("injected profile persistence failure")

    monkeypatch.setattr(
        type(state.session), "set_execution_config", fail_config)
    with pytest.raises(OSError, match="profile persistence failure"):
        asyncio.run(server._update_thread_settings(socket, "settings", {
            "threadId": thread_id,
            "model": profile_b.selection_id,
        }))
    assert state.model_id == original[0]


def test_codex_rejects_unready_profile_before_thread_or_turn_preparation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    profile = replace(
        profile_for_path(execution),
        required_environment=("RPNH_TEST_MISSING_CREDENTIAL",),
    )
    monkeypatch.delenv("RPNH_TEST_MISSING_CREDENTIAL", raising=False)
    server._profiles_by_model_id[profile.selection_id] = profile
    socket = _FakeWebSocket([])

    with pytest.raises(ValueError, match="profile is not ready"):
        asyncio.run(server._start_thread(socket, "start", {
            "model": profile.selection_id,
        }))
    assert server._threads == {}

    class _Session:
        execution_config_path = execution.resolve()
        prepare_calls = 0

        @staticmethod
        def reconcile_active_turn():
            return MainTurnReconciliation("idle", None)

        def prepare_turn(self, _text: str):
            self.prepare_calls += 1
            return object()

    session = _Session()
    state = ThreadState(
        thread_id="thread-unready",
        session=session,
        main_turn_control=object(),
        model_id=profile.selection_id,
        cwd=tmp_path,
        created_at=100,
    )
    server._threads[state.thread_id] = state

    with pytest.raises(ValueError, match="profile is not ready"):
        asyncio.run(server._start_turn(socket, "turn", {
            "threadId": state.thread_id,
            "input": [{"type": "text", "text": "do not prepare"}],
        }))
    assert session.prepare_calls == 0


def test_codex_model_write_persists_the_rpnh_profile(
        tmp_path: Path, monkeypatch,
) -> None:
    execution = _local_profile(tmp_path / "runtime" / "profiles")
    selected_config = tmp_path / "user" / "config.json"
    monkeypatch.setenv("RPNH_CONFIG", str(selected_config))
    server = CodexAppServer(tmp_path / "session", execution)
    socket = _FakeWebSocket([
        {
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "codex-tui", "version": "0.155.0"},
            },
        },
        {"method": "initialized"},
        {
            "id": 1,
            "method": "config/batchWrite",
            "params": {
                "expectedVersion": "rpnh-uninitialized",
                "reloadUserConfig": True,
                "edits": [{
                    "keyPath": "model",
                    "value": "local-process/test-model",
                    "mergeStrategy": "replace",
                }],
            },
        },
    ])
    asyncio.run(server.handle(socket))

    response = next(item["result"] for item in socket.sent if item.get("id") == 1)
    assert response["status"] == "ok"
    assert response["version"].startswith("rpnh-")
    saved = json.loads(selected_config.read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": "rpnh/cli_config/v3",
        "execution_config_path": str(execution.resolve()),
    }


def test_codex_frontend_attachment_crud_cannot_fake_registered_inputs(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    socket = _FakeWebSocket([
        {"id": "initialize", "method": "initialize", "params": {
            "clientInfo": {"name": "codex-tui", "version": "0.155.0"}}},
        {"method": "initialized"},
        *({"id": index, "method": "thread/attachment/" + action,
           "params": {"threadId": "unused", "attachmentType": "local-file",
                      "identityKey": "notes", "payload": {"path": "/tmp/notes"}}}
          for index, action in enumerate(("add", "list", "remove"))),
    ])
    asyncio.run(server.handle(socket))
    for index in range(3):
        response = next(item for item in socket.sent if item.get("id") == index)
        assert response["error"]["code"] == -32601
        assert "registered task inputs" in response["error"]["message"]
        assert "result" not in response
    assert not any(item.get("method") == "thread/attachment/updated" for item in socket.sent)
    assert not server._threads
    assert not (tmp_path / "session").exists()


def test_codex_resume_rebuilds_transcript_from_main_session_registry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    thread_root = _write_thread_projection(
        root, turns="forged UI transcript", execution=execution)
    session = _RecoverySession(
        thread_root, execution,
        MainTurnReconciliation("idle", None),
        history=[("user", "authoritative question"),
                 ("assistant", "authoritative answer")],
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root: session,
    )

    server = CodexAppServer(root, execution)

    turn = server._threads["thread-1"].turns[0]
    assert turn["status"] == "completed"
    assert turn["items"][0]["content"][0]["text"] == (
        "authoritative question")
    assert turn["items"][1]["text"] == "authoritative answer"
    persisted = json.loads(
        (thread_root / "thread_state.json").read_text(encoding="utf-8"))
    assert persisted["turns"] == server._threads["thread-1"].turns


def test_codex_load_reports_unavailable_profile_without_hiding_valid_thread(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    missing_execution = tmp_path / "missing" / "execution.json"
    valid_root = _write_thread_projection(
        root, thread_id="thread-valid", execution=execution)
    missing_root = _write_thread_projection(
        root, thread_id="thread-missing", execution=missing_execution)
    missing_projection = missing_root / "thread_state.json"
    before = missing_projection.read_bytes()
    sessions = {
        "thread-valid": _RecoverySession(
            valid_root, execution, MainTurnReconciliation("idle", None)),
        "thread-missing": _RecoverySession(
            missing_root, missing_execution,
            MainTurnReconciliation("idle", None)),
    }
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda thread_root: sessions[thread_root.name],
    )

    server = CodexAppServer(root, execution)

    assert tuple(server._threads) == ("thread-valid",)
    assert server.thread_load_failures == (
        "persisted_profile_unavailable",)
    diagnostic = server.thread_load_diagnostic()
    assert diagnostic == (
        "1 persisted thread(s) were not loaded "
        "(persisted_profile_unavailable=1)")
    assert "thread-missing" not in diagnostic
    assert str(tmp_path) not in diagnostic
    assert missing_projection.read_bytes() == before


def test_codex_resume_fails_loud_when_all_persisted_profiles_unavailable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    missing_execution = tmp_path / "missing" / "execution.json"
    thread_root = _write_thread_projection(
        root, execution=missing_execution)
    projection = thread_root / "thread_state.json"
    before = projection.read_bytes()
    session = _RecoverySession(
        thread_root, missing_execution,
        MainTurnReconciliation("idle", None),
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root: session,
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.resolve_codex_binary",
        lambda _value=None: "/usr/bin/true",
    )

    with pytest.raises(CodexCompatibilityError) as raised:
        asyncio.run(_run_codex_frontend_async(
            root, execution, codex_binary="ignored", resume=True))

    message = str(raised.value)
    assert "no valid persisted threads" in message
    assert "persisted_profile_unavailable=1" in message
    assert "thread-1" not in message
    assert str(tmp_path) not in message
    assert projection.read_bytes() == before


def test_codex_unavailable_exact_profile_does_not_touch_real_session(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles", model="same-model")
    orphan_execution = _local_profile(
        tmp_path / "orphan-profiles", model="same-model")
    root = tmp_path / "session"
    thread_root = root / "threads" / "thread-orphan"
    MainSession(thread_root, orphan_execution)
    _write_thread_projection(
        root, thread_id="thread-orphan", execution=orphan_execution)
    before = {
        path.relative_to(thread_root): path.read_bytes()
        for path in thread_root.rglob("*") if path.is_file()
    }

    server = CodexAppServer(root, execution)

    after = {
        path.relative_to(thread_root): path.read_bytes()
        for path in thread_root.rglob("*") if path.is_file()
    }
    assert server._threads == {}
    assert server.thread_load_failures == (
        "persisted_profile_unavailable",)
    assert after == before


def test_codex_resumed_transcript_preserves_live_launch_annotation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    _write_thread_projection(root, execution=execution)
    child = SimpleNamespace(task_id="task-durable", kind="single_agent")
    decision = MainDecision(
        "delegated", MainTaskDecision(
            "single_agent", "do it", (AgentStage("worker", "Do it."),)))
    live_text = CodexAppServer._decision_output(
        MainTurnReconciliation("committed", None, decision, child))
    session = _RecoverySession(
        root / "threads" / "thread-1", execution,
        MainTurnReconciliation("idle", None),
        history=[("user", "do it"), ("assistant", "delegated")],
    )
    session.display_history = [
        ("user", "do it"), ("assistant", live_text)]
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root: session,
    )
    server = CodexAppServer(root, execution)
    socket = _FakeWebSocket([{
        "id": "initialize",
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "codex-tui", "version": "0.155.0"},
        },
    }, {
        "method": "initialized",
    }, {
        "id": 1,
        "method": "thread/read",
        "params": {"threadId": "thread-1"},
    }])

    asyncio.run(server.handle(socket))

    response = next(item for item in socket.sent if item.get("id") == 1)
    transcript_text = response["result"]["thread"]["turns"][0][
        "items"][1]["text"]
    assert transcript_text == live_text
    assert transcript_text == (
        "delegated\n\n[launched task-durable: single_agent]")


def test_codex_resume_reconciles_registry_terminal_before_projection(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    thread_root = _write_thread_projection(root, execution=execution)
    snapshot = MainTurnSnapshot(
        ordinal=1,
        user_text="finish naturally",
        required_task_kind=None,
        state="terminal",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output={"reply": "registered result", "task": None},
        registry_observation={"outcome": "terminal"},
    )
    session = _RecoverySession(
        thread_root, execution,
        MainTurnReconciliation(
            "committed", snapshot, MainDecision("registered result", None)),
        committed_history=[
            ("user", "finish naturally"),
            ("assistant", "registered result"),
        ],
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root: session,
    )

    server = CodexAppServer(root, execution)

    state = server._threads["thread-1"]
    assert session.reconcile_calls == 1
    assert state.active is None
    assert state.turns[0]["items"][1]["text"] == "registered result"


def test_codex_resume_restores_only_live_matching_foreground_handle(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    thread_root = _write_thread_projection(root, execution=execution)
    attempt_path = thread_root / "main" / "turn-0001"
    snapshot = MainTurnSnapshot(
        ordinal=1,
        user_text="still running",
        required_task_kind=None,
        state="running",
        attempt_path=attempt_path,
        registered_output=None,
        registry_observation={"outcome": "running"},
    )
    session = _RecoverySession(
        thread_root, execution,
        MainTurnReconciliation("running", snapshot),
    )
    handle = SimpleNamespace(
        task_id="task-main", process=SimpleNamespace(poll=lambda: None))

    class _Control:
        def __init__(self, _root: Path) -> None:
            pass

        def list(self):
            return ({"task_id": "task-main", "run_dir": str(attempt_path)},)

        def get(self, task_id: str):
            assert task_id == "task-main"
            return handle

    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root: session,
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.TaskControl", _Control)

    server = CodexAppServer(root, execution)

    active = server._threads["thread-1"].active
    assert active is not None
    assert active.user_text == "still running"
    assert active.task_handle is handle


def test_codex_start_failure_keeps_pending_registry_turn_visible(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    thread_root = tmp_path / "thread"
    thread_root.mkdir()
    snapshot = MainTurnSnapshot(
        ordinal=1,
        user_text="start once",
        required_task_kind=None,
        state="pending_start",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output=None,
        registry_observation=None,
    )

    class _PendingSession:
        root = thread_root
        history: list[tuple[str, str]] = []
        prepare_calls = 0

        def prepare_turn(self, text: str):
            assert text == "start once"
            self.prepare_calls += 1
            return object()

        @staticmethod
        def active_turn_snapshot():
            return snapshot

        @staticmethod
        def reconcile_active_turn():
            return MainTurnReconciliation("pending_start", snapshot)

    class _FailingControl:
        start_calls = 0

        def start(self, _spec):
            self.start_calls += 1
            raise RuntimeError("foreground launch failed")

        @staticmethod
        def list():
            return ()

    session = _PendingSession()
    control = _FailingControl()
    state = ThreadState(
        thread_id="thread-pending",
        session=session,
        main_turn_control=control,
        model_id="local-process/test-model",
        cwd=tmp_path,
        created_at=100,
    )
    server._threads[state.thread_id] = state
    params = {
        "threadId": state.thread_id,
        "input": [{"type": "text", "text": "start once"}],
    }
    socket = _FakeWebSocket([])

    with pytest.raises(RuntimeError, match="foreground launch failed"):
        asyncio.run(server._start_turn(socket, 1, params))

    assert state.active is not None
    assert state.active.task_handle is None
    assert server._thread_document(state)["status"]["type"] == "active"
    asyncio.run(server._refresh_active_turn(None, state))
    with pytest.raises(
            RuntimeError, match="no live foreground task to interrupt"):
        asyncio.run(server._interrupt_turn(socket, 2, {
            "threadId": state.thread_id,
            "turnId": state.active.turn_id,
        }))
    with pytest.raises(ValueError, match="already has an active turn"):
        asyncio.run(server._start_turn(socket, 3, params))
    assert session.prepare_calls == 1
    assert control.start_calls == 1
    assert state.turns == []


def test_codex_interrupt_race_prefers_registry_terminal_evidence(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    thread_root = tmp_path / "thread"
    thread_root.mkdir()
    snapshot = MainTurnSnapshot(
        ordinal=2,
        user_text="race",
        required_task_kind=None,
        state="running",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output=None,
        registry_observation={"outcome": "running"},
    )

    class _RaceSession:
        root = thread_root
        history: list[tuple[str, str]] = []
        terminal = False

        def reconcile_active_turn(self):
            if not self.terminal:
                return MainTurnReconciliation("running", snapshot)
            self.history = [("user", "race"), ("assistant", "won naturally")]
            return MainTurnReconciliation(
                "committed", snapshot, MainDecision("won naturally", None))

    session = _RaceSession()

    class _RaceControl:
        stop_calls = 0

        def stop(self, task_id: str, *, startup_safe: bool):
            assert task_id == "task-main"
            assert startup_safe is True
            self.stop_calls += 1
            session.terminal = True
            return {"task_id": task_id, "status": "ALREADY_EXITED"}

    control = _RaceControl()
    handle = SimpleNamespace(
        task_id="task-main", process=SimpleNamespace(poll=lambda: 0))
    state = ThreadState(
        thread_id="thread-race",
        session=session,
        main_turn_control=control,
        model_id="local-process/test-model",
        cwd=tmp_path,
        created_at=100,
        active=ActiveTurn(
            turn_id="turn-race",
            item_id="item-race",
            ordinal=2,
            user_text="race",
            required_task_kind=None,
            started_at=100,
            task_handle=handle,
        ),
    )
    server._threads[state.thread_id] = state
    socket = _FakeWebSocket([])

    asyncio.run(server._interrupt_turn(socket, 1, {
        "threadId": state.thread_id,
        "turnId": "turn-race",
    }))

    assert control.stop_calls == 1
    assert state.active is None
    assert state.turns[0]["status"] == "completed"
    assert state.turns[0]["id"] == "turn-race"
    assert state.turns[0]["items"][1]["id"] == "item-race"
    assert state.turns[0]["items"][1]["text"] == "won naturally"
    completed = next(
        item for item in socket.sent if item.get("method") == "turn/completed")
    assert completed["params"]["turn"]["status"] == "completed"
    assert completed["params"]["turn"]["id"] == state.turns[0]["id"]
    rebuilt = server._committed_turns(
        state, previous_turns=list(state.turns))
    assert rebuilt[0]["id"] == "turn-race"


def test_codex_stopped_turn_is_reconciled_without_entering_history(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    thread_root = tmp_path / "thread"
    thread_root.mkdir()
    snapshot = MainTurnSnapshot(
        ordinal=1,
        user_text="stop",
        required_task_kind=None,
        state="stopped_by_owner",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output=None,
        registry_observation={"outcome": "stopped_by_owner"},
    )

    class _StoppedSession:
        root = thread_root
        history: list[tuple[str, str]] = []

        @staticmethod
        def reconcile_active_turn():
            return MainTurnReconciliation("paused", snapshot)

    handle = SimpleNamespace(
        task_id="task-main", process=SimpleNamespace(poll=lambda: 0))
    state = ThreadState(
        thread_id="thread-stopped",
        session=_StoppedSession(),
        main_turn_control=SimpleNamespace(),
        model_id="local-process/test-model",
        cwd=tmp_path,
        created_at=100,
        active=ActiveTurn(
            turn_id="turn-stopped",
            item_id="item-stopped",
            ordinal=1,
            user_text="stop",
            required_task_kind=None,
            started_at=100,
            task_handle=handle,
        ),
    )
    server._threads[state.thread_id] = state
    socket = _FakeWebSocket([])

    asyncio.run(server._finish_turn(socket, state, state.active))

    assert state.active is None
    assert state.turns == []
    persisted = json.loads(
        (thread_root / "thread_state.json").read_text(encoding="utf-8"))
    assert persisted["turns"] == []
    completed = next(
        item for item in socket.sent if item.get("method") == "turn/completed")
    assert completed["params"]["turn"]["status"] == "interrupted"
    assert "checkpoint" in completed["params"]["turn"]["items"][1]["text"]


def test_codex_process_exit_without_registry_terminal_fails_only_main_turn(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    thread_root = tmp_path / "thread"
    thread_root.mkdir()
    snapshot = MainTurnSnapshot(
        ordinal=1,
        user_text="not terminal",
        required_task_kind=None,
        state="running",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output=None,
        registry_observation={"outcome": "running"},
    )

    class _RunningSession:
        root = thread_root
        history: list[tuple[str, str]] = []
        failed = False

        def reconcile_active_turn(self):
            return MainTurnReconciliation("running", snapshot)

        def fail_active_turn(self):
            self.failed = True
            return MainTurnReconciliation("failed", snapshot)

    session = _RunningSession()

    active = ActiveTurn(
        turn_id="turn-running",
        item_id="item-running",
        ordinal=1,
        user_text="not terminal",
        required_task_kind=None,
        started_at=100,
        task_handle=SimpleNamespace(
            task_id="task-main", process=SimpleNamespace(poll=lambda: 7)),
        tracking=True,
    )
    state = ThreadState(
        thread_id="thread-running",
        session=session,
        main_turn_control=SimpleNamespace(),
        model_id="local-process/test-model",
        cwd=tmp_path,
        created_at=100,
        active=active,
    )
    socket = _FakeWebSocket([])

    asyncio.run(server._finish_turn(socket, state, active))

    assert session.failed is True
    assert state.active is None
    assert state.turns == []
    completed = next(
        item for item in socket.sent if item.get("method") == "turn/completed")
    assert completed["params"]["turn"]["status"] == "failed"
    assert "without terminal Registry evidence" in (
        completed["params"]["turn"]["items"][1]["text"])


def test_codex_shutdown_stops_only_foreground_main_turn(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    server = CodexAppServer(tmp_path / "session", execution)
    thread_root = tmp_path / "thread"
    thread_root.mkdir()
    running = MainTurnSnapshot(
        ordinal=1,
        user_text="shutdown",
        required_task_kind=None,
        state="running",
        attempt_path=thread_root / "main" / "turn-0001",
        registered_output=None,
        registry_observation={"outcome": "running"},
    )
    stopped = MainTurnSnapshot(
        ordinal=1,
        user_text="shutdown",
        required_task_kind=None,
        state="stopped_by_owner",
        attempt_path=running.attempt_path,
        registered_output=None,
        registry_observation={"outcome": "stopped_by_owner"},
    )

    class _BackgroundControl:
        def stop(self, *_args, **_kwargs):
            raise AssertionError("background task control must not be stopped")

    class _ShutdownSession:
        root = thread_root
        history: list[tuple[str, str]] = []
        task_control = _BackgroundControl()
        was_stopped = False

        def reconcile_active_turn(self):
            return MainTurnReconciliation(
                "paused" if self.was_stopped else "running",
                stopped if self.was_stopped else running,
            )

    session = _ShutdownSession()
    process = SimpleNamespace(poll=lambda: 0 if session.was_stopped else None)
    handle = SimpleNamespace(task_id="task-main", process=process)

    class _MainTurnControl:
        stop_calls = 0

        def stop(self, task_id: str, *, startup_safe: bool):
            assert task_id == "task-main"
            assert startup_safe is True
            self.stop_calls += 1
            session.was_stopped = True
            return {"task_id": task_id, "status": "STOP_REQUESTED"}

    control = _MainTurnControl()
    state = ThreadState(
        thread_id="thread-shutdown",
        session=session,
        main_turn_control=control,
        model_id="local-process/test-model",
        cwd=tmp_path,
        created_at=100,
        active=ActiveTurn(
            turn_id="turn-shutdown",
            item_id="item-shutdown",
            ordinal=1,
            user_text="shutdown",
            required_task_kind=None,
            started_at=100,
            task_handle=handle,
        ),
    )
    server._threads[state.thread_id] = state

    asyncio.run(server.stop_active_turns())

    assert control.stop_calls == 1
    assert state.active is None
    assert state.turns == []
