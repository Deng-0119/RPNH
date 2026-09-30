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
    ThreadState,
)
from cpn.rpnh.agent_tasks import AgentStage
from cpn.rpnh.main_session import (
    MainDecision,
    MainSession,
    MainTaskDecision,
    MainTurnReconciliation,
    MainTurnSnapshot,
)
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh.session_access import (
    MainSessionOwnerLease,
    stable_frontend_session_id,
)
from cpn.rpnh.task_control import TaskControl
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


def _reasoning_effort_profiles(
        root: Path, *, default: str = "medium",
) -> tuple[Path, Path, Path]:
    catalog = root / "catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v3",
        "providers": [{
            "provider": "local",
            "display_name": "Local",
            "models": [{
                "profile": "reasoning-model",
                "model_condition": "exact-reasoning-model",
                "reasoning_efforts": {
                    "supported": ["low", "medium", "high"],
                    "default": default,
                },
                "adapter": {
                    "adapter_kind": "local_process",
                    "argv": [
                        "/usr/bin/true", "--model", "{model}",
                        "--effort", "{reasoning_effort}",
                    ],
                    "probe_argv": ["/usr/bin/true"],
                    "env": {},
                    "inherit_env": [],
                },
                "timeout_seconds": 60,
                "max_output_tokens": 1024,
                "max_response_bytes": 8192,
            }],
        }],
    }), encoding="utf-8")
    generated = root / "generated"
    build_provider_catalog(catalog, generated)
    execution = generated / "execution"
    def path(effort: str) -> Path:
        return execution / (
            "reasoning-model.json"
            if effort == default else
            f"reasoning-model--effort-{effort}.json")
    return (
        path("low"), path("medium"), path("high"),
    )


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
    if execution is None:
        raise ValueError("direct-root test fixture requires an execution profile")
    MainSession(root, execution)
    sidecar = root / ".frontends" / "codex.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({
        "schema_version": "rpnh/codex_frontend_metadata/v1",
        "protocol_thread_id": thread_id,
        "cwd": str(root),
        "created_at": 100,
        "preview": "",
        "name": None,
        "turns": [] if turns is None else turns,
        "attachments": [],
    }), encoding="utf-8")
    return root


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

    def active_turn_snapshot(self) -> MainTurnSnapshot | None:
        return self.reconciliation.snapshot


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
    server._profiles_by_variant[
        (profile_b.selection_id, profile_b.reasoning_effort)] = profile_b
    server._profiles_by_path[profile_b.path] = profile_b
    server._initial_profile_identities[profile_b.path] = (
        server._execution_identity(profile_b.path))
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
        "schema_version": "rpnh/cli_config/v4",
        "execution_config_path": str(execution.resolve()),
        "reasoning_effort": None,
    }


def test_codex_lists_selects_and_persists_model_scoped_reasoning_effort(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    low, medium, high = _reasoning_effort_profiles(
        tmp_path / "runtime")
    selected_config = tmp_path / "user" / "config.json"
    monkeypatch.setenv("RPNH_CONFIG", str(selected_config))
    server = CodexAppServer(tmp_path / "session", medium)
    socket = _FakeWebSocket([
        {
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "codex-tui", "version": "0.155.0"},
            },
        },
        {"method": "initialized"},
        {"id": 1, "method": "model/list", "params": {}},
        {
            "id": 2,
            "method": "config/batchWrite",
            "params": {
                "expectedVersion": "rpnh-uninitialized",
                "reloadUserConfig": True,
                "edits": [
                    {
                        "keyPath": "model",
                        "value": "reasoning-model",
                        "mergeStrategy": "replace",
                    },
                    {
                        "keyPath": "model_reasoning_effort",
                        "value": "high",
                        "mergeStrategy": "replace",
                    },
                ],
            },
        },
        {
            "id": 3,
            "method": "thread/start",
            "params": {
                "model": "reasoning-model",
                "effort": "high",
                "cwd": str(tmp_path),
            },
        },
    ])

    asyncio.run(server.handle(socket))

    model = next(
        item["result"]["data"][0]
        for item in socket.sent if item.get("id") == 1)
    assert model["defaultReasoningEffort"] == "medium"
    assert [
        item["reasoningEffort"]
        for item in model["supportedReasoningEfforts"]
    ] == ["low", "medium", "high"]
    started = next(
        item["result"] for item in socket.sent if item.get("id") == 3)
    assert started["reasoningEffort"] == "high"
    state = next(iter(server._threads.values()))
    assert state.reasoning_effort == "high"
    assert state.session.execution_config_path == high.resolve()
    assert low.resolve() in server._profiles_by_path
    saved = json.loads(selected_config.read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": "rpnh/cli_config/v4",
        "execution_config_path": str(high.resolve()),
        "reasoning_effort": "high",
    }


def test_codex_rejects_cached_effort_path_after_catalog_default_changes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    low, _, _ = _reasoning_effort_profiles(runtime, default="low")
    selected_config = tmp_path / "user" / "config.json"
    monkeypatch.setenv("RPNH_CONFIG", str(selected_config))
    server = CodexAppServer(tmp_path / "session", low)

    _reasoning_effort_profiles(runtime, default="high")
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
                "edits": [
                    {
                        "keyPath": "model",
                        "value": "reasoning-model",
                        "mergeStrategy": "replace",
                    },
                    {
                        "keyPath": "model_reasoning_effort",
                        "value": "low",
                        "mergeStrategy": "replace",
                    },
                ],
            },
        },
        {
            "id": 2,
            "method": "thread/start",
            "params": {
                "model": "reasoning-model",
                "effort": "low",
                "cwd": str(tmp_path),
            },
        },
    ])

    asyncio.run(server.handle(socket))

    for request_id in (1, 2):
        response = next(
            item for item in socket.sent if item.get("id") == request_id)
        assert "result" not in response
        assert "profile changed" in response["error"]["message"]
    assert not selected_config.exists()
    assert not (tmp_path / "session").exists()


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


def test_codex_projects_one_direct_basic_main_session(tmp_path: Path) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    MainSession(root, execution)

    server = CodexAppServer(root, execution)
    thread_id = stable_frontend_session_id(root)

    assert tuple(server._threads) == (thread_id,)
    assert server._threads[thread_id].session.root == root.resolve()
    assert not (root / "threads").exists()
    server.close()


def test_codex_fresh_thread_is_direct_and_rejects_a_second_thread(
        tmp_path: Path,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    server = CodexAppServer(root, execution)
    socket = _FakeWebSocket([])

    asyncio.run(server._start_thread(socket, "first", {}))
    state = next(iter(server._threads.values()))
    assert state.session.root == root.resolve()
    assert state.thread_id == stable_frontend_session_id(root)
    assert not (root / "threads").exists()
    with pytest.raises(ValueError, match="already bound"):
        asyncio.run(server._start_thread(socket, "second", {}))
    server.close()


def test_codex_rejects_frontend_container_root(tmp_path: Path) -> None:
    execution = _local_profile(tmp_path / "profiles")
    container = tmp_path / "container"
    MainSession(container / "threads" / "legacy", execution)

    with pytest.raises(ValueError, match="container roots"):
        CodexAppServer(container, execution)


def test_codex_resume_rebuilds_transcript_and_sanitizes_forged_sidecar(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    _write_thread_projection(
        root, thread_id="forged-id", turns=[{"text": "forged"}],
        execution=execution)
    session = _RecoverySession(
        root, execution, MainTurnReconciliation("idle", None),
        history=[("user", "authoritative question"),
                 ("assistant", "authoritative answer")],
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root, _execution=None, **_kwargs: session,
    )

    server = CodexAppServer(root, execution)
    state = next(iter(server._threads.values()))
    turn = state.turns[0]
    assert state.thread_id == stable_frontend_session_id(root)
    assert turn["items"][0]["content"][0]["text"] == (
        "authoritative question")
    assert turn["items"][1]["text"] == "authoritative answer"

    server._persist_thread(state)
    persisted = json.loads(
        (root / ".frontends" / "codex.json").read_text(encoding="utf-8"))
    assert set(persisted) == {
        "schema_version", "protocol_thread_id", "cwd", "created_at",
        "preview", "name", "attachments",
    }
    assert "turns" not in persisted
    assert "model_id" not in persisted
    server.close()

    (root / ".frontends" / "codex.json").unlink()
    reopened = CodexAppServer(root, execution)
    reopened_turn = next(iter(reopened._threads.values())).turns[0]
    assert reopened_turn["items"][1]["text"] == "authoritative answer"
    reopened.close()


def test_codex_releases_lease_on_close_and_constructor_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    MainSession(root, execution)
    server = CodexAppServer(root, execution)
    with pytest.raises(RuntimeError, match="owner lease"):
        MainSessionOwnerLease(root)
    server.close()
    with MainSessionOwnerLease(root):
        pass

    def fail_resume(*_args, **_kwargs):
        raise RuntimeError("resume failed")

    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume", fail_resume)
    with pytest.raises(RuntimeError, match="resume failed"):
        CodexAppServer(root, execution)
    with MainSessionOwnerLease(root):
        pass


def test_codex_resume_projection_starts_no_worker_or_reconciliation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / "session"
    MainSession(root, execution)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("resume projection must not start or reconcile")

    recovery_modes: list[bool] = []
    original_init = TaskControl.__init__

    def record_task_control_mode(self, *args, **kwargs):
        recovery_modes.append(kwargs.get("recover_pending_launches", True))
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(TaskControl, "__init__", record_task_control_mode)
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.TaskControl.start", forbidden)
    monkeypatch.setattr(MainSession, "reconcile_active_turn", forbidden)
    server = CodexAppServer(root, execution)
    thread_id = next(iter(server._threads))
    socket = _FakeWebSocket([{
        "id": "initialize",
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "codex-tui", "version": "0.155.0"},
        },
    }, {
        "method": "initialized",
    }, {
        "id": 1, "method": "thread/list", "params": {},
    }, {
        "id": 2, "method": "thread/read",
        "params": {"threadId": thread_id},
    }, {
        "id": 3, "method": "thread/resume",
        "params": {"threadId": thread_id},
    }])

    asyncio.run(server.handle(socket))

    assert all("error" not in item for item in socket.sent if "id" in item)
    assert recovery_modes == [False, False]
    server.close()


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
        root, execution,
        MainTurnReconciliation("idle", None),
        history=[("user", "do it"), ("assistant", "delegated")],
    )
    session.display_history = [
        ("user", "do it"), ("assistant", live_text)]
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root, _execution=None, **_kwargs: session,
    )
    server = CodexAppServer(root, execution)
    thread_id = next(iter(server._threads))
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
        "params": {"threadId": thread_id},
    }])

    asyncio.run(server.handle(socket))

    response = next(item for item in socket.sent if item.get("id") == 1)
    transcript_text = response["result"]["thread"]["turns"][0][
        "items"][1]["text"]
    assert transcript_text == live_text
    assert transcript_text == (
        "delegated\n\n[launched task-durable: single_agent]")


def test_codex_resume_observes_terminal_without_reconciliation(
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
        lambda _root, _execution=None, **_kwargs: session,
    )

    server = CodexAppServer(root, execution)

    state = next(iter(server._threads.values()))
    assert session.reconcile_calls == 0
    assert state.active is not None
    assert state.active.user_text == "finish naturally"
    assert state.turns == []
    server.close()


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
        def __init__(self, _root: Path, **_kwargs) -> None:
            pass

        def list(self):
            return ({"task_id": "task-main", "run_dir": str(attempt_path)},)

        def get(self, task_id: str):
            assert task_id == "task-main"
            return handle

    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.MainSession.resume",
        lambda _root, _execution=None, **_kwargs: session,
    )
    monkeypatch.setattr(
        "cpn.frontend.codex_app_server.TaskControl", _Control)

    server = CodexAppServer(root, execution)

    active = next(iter(server._threads.values())).active
    assert active is not None
    assert active.user_text == "still running"
    assert active.task_handle is handle
    server.close()


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
    rebuilt = server._committed_turns(state)
    assert rebuilt[0]["id"] != "turn-race"
    assert rebuilt[0]["items"][1]["text"] == "won naturally"


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
        (thread_root / ".frontends" / "codex.json").read_text(
            encoding="utf-8"))
    assert "turns" not in persisted
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
