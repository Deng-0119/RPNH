from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from cpn.components.agent_loop import optional_execution
from cpn.rpnh.agent_tasks import (
    AgentStage,
    AgentTaskSpec,
    resume_agent_task,
    run_agent_task,
)
from cpn.rpnh.llm_contracts import (
    LLMInputPortInterrupted,
    LLMInputResponseBytes,
)
from cpn.rpnh.main_session import (
    MainSession,
    MainSessionExecutionFailed,
    MainSessionPaused,
    MainTurnSnapshot,
    _main_prompt,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import (
    NativeBootstrapManifest,
    _bootstrap_identity,
)
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.main_thread import (
    MainThreadAuthorityError,
    MainThreadRegistry,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.session_access import MainSessionOwnerLease


def _ref(entity_type: str, logical_kind: str, version_kind: str) -> VersionRef:
    return VersionRef(entity_type, new_id(logical_kind), new_id(version_kind))


def _payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _install_child_observation(
        monkeypatch: pytest.MonkeyPatch, session: MainSession, outcome: str,
) -> None:
    observations: dict[str, dict[str, Any]] = {}

    def inspect(*, turn_ref, turn, allow_running=False):
        assert allow_running in {False, True}
        key = str(turn_ref)
        if key not in observations:
            task = _ref("task/v1", "task", "task_version")
            run = _ref("native_run_identity/v1", "run", "run_version")
            authority = _ref(
                "run_execution_authority/v1",
                "run_execution_authority",
                "run_execution_authority_version",
            )
            checkpoint = _ref(
                "marking_checkpoint/v1", "marking_checkpoint",
                "marking_checkpoint_version",
            )
            observed = {
                "main_turn_ref": _payload(turn_ref),
                "attempt_relative_path": turn["attempt_relative_path"],
                "validated_through_ordinal": 17,
                "child_task_ref": _payload(task),
                "child_run_ref": _payload(run),
                "run_execution_authority_ref": _payload(authority),
                "final_checkpoint_ref": _payload(checkpoint),
                "outcome": outcome,
                "run_outcome": None,
                "terminal_evidence_ref": None,
                "terminal_result_ref": None,
                "final_result_index_ref": None,
            }
            if outcome == "terminal":
                observed.update({
                    "run_outcome": "complete",
                    "terminal_evidence_ref": _payload(_ref(
                        "run_terminal_evidence/v1", "terminal_evidence",
                        "terminal_evidence_version")),
                    "terminal_result_ref": _payload(_ref(
                        "resource_version/v1", "resource",
                        "resource_version")),
                    "final_result_index_ref": _payload(_ref(
                        "final_result_index/v1", "resource",
                        "resource_version")),
                })
            observations[key] = observed
        return observations[key]

    monkeypatch.setattr(session._main_thread, "_inspect_child_registry", inspect)


class _TaskControl:
    def __init__(self) -> None:
        self.specs = []
        self.handle = SimpleNamespace(
            task_id="task-child", kind="single_agent")
        self.fail_once = False
        self.fail_after_register_once = False
        self.initialize_child_registry = False
        self.child_registry_core = None
        self.registered_spec = None

    def start(self, spec):
        self.specs.append(spec)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("launch failed")
        self.registered_spec = spec
        if self.fail_after_register_once:
            self.fail_after_register_once = False
            raise RuntimeError("launch failed after register")
        if self.initialize_child_registry:
            self.child_registry_core = _RegistryCore(
                spec.run_dir, create=True)
        return self.handle

    def list(self):
        if self.registered_spec is None:
            return ()
        return ({
            "task_id": self.handle.task_id,
            "run_dir": str(self.registered_spec.run_dir),
        },)

    def get(self, task_id):
        assert task_id == self.handle.task_id
        return self.handle


class _TerminalPort:
    def __init__(
            self, output: object, *, output_port_id: str = "main.result",
    ) -> None:
        self.output = output
        self.output_port_id = output_port_id

    def request_once(self, _attempt):
        return _response([{
            "id": "main-output",
            "name": "write_file",
            "arguments": json.dumps({
                "path": "outputs/result.txt",
                "description": "Main-session decision.",
                "content": json.dumps(json.dumps(
                    self.output, sort_keys=True, separators=(",", ":"))),
                "output_port_id": self.output_port_id,
                "outcome_id": "complete",
            }),
        }, {
            "id": "main-complete",
            "name": "complete_interaction",
            "arguments": "{}",
        }])

    def close(self):
        pass


class _DirectTextTerminalPort:
    def __init__(self, output: object) -> None:
        self.output = output

    def request_once(self, _attempt):
        return _response([{
            "id": "main-direct-output",
            "name": "write_file",
            "arguments": json.dumps({
                "path": "outputs/result.json",
                "description": "Main-session decision as direct text.",
                "content": json.dumps(
                    self.output, sort_keys=True, separators=(",", ":")),
                "output_port_id": "main.result",
                "outcome_id": "complete",
            }),
        }, {
            "id": "main-direct-complete",
            "name": "complete_interaction",
            "arguments": "{}",
        }])

    def close(self):
        pass


class _NonterminalPort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        return _response([{
            "id": f"query-{self.calls}",
            "name": "query_registry_resources",
            "arguments": json.dumps({"query": "", "view": "current"}),
        }])

    def close(self):
        pass


class _DoubleQueryThenTerminalPort:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        if self.calls == 1:
            return _response([{
                "id": "query-outputs",
                "name": "query_registry_resources",
                "arguments": json.dumps({
                    "query": "outputs", "view": "current"}),
            }, {
                "id": "query-result",
                "name": "query_registry_resources",
                "arguments": json.dumps({
                    "query": "result", "view": "current"}),
            }])
        if self.calls == 2:
            return _TerminalPort(self.output).request_once(_attempt)
        raise AssertionError("double-query port requested an extra turn")

    def close(self):
        pass


class _StoppedPort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        if self.calls == 1:
            calls = [{
                "id": "interrupt-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "interrupt-main-turn",
                    "timeout_seconds": 10,
                }),
            }]
        else:
            calls = [{
                "id": "discarded-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Output superseded by interruption.",
                    "content": json.dumps("discarded"),
                    "output_port_id": "main.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "discarded-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }]
        return _response(calls)

    def close(self):
        pass


class _OwnerInterruptedInputPort:
    def __init__(self) -> None:
        self.calls = 0
        self.interruption_latency: float | None = None

    def request_once(self, _attempt):
        raise AssertionError("owner-stop test requires interruptible dispatch")

    def request_once_interruptible(
            self, _attempt, *, interruption_requested):
        self.calls += 1
        started = time.monotonic()
        os.kill(os.getpid(), signal.SIGINT)
        deadline = time.monotonic() + 2
        while not interruption_requested():
            if time.monotonic() >= deadline:
                raise AssertionError("owner stop did not reach input port")
            time.sleep(0.01)
        self.interruption_latency = time.monotonic() - started
        raise LLMInputPortInterrupted(submission_state="not_submitted")

    def close(self):
        pass


def _response(calls: list[dict[str, str]]) -> LLMInputResponseBytes:
    return LLMInputResponseBytes(json.dumps({
        "protocol": "llm_response_envelope/v1",
        "tool_calls": calls,
        "finish_reason": "tool_calls",
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        status_code=None, external_request_id=None)


def _write_execution_profile(
        tmp_path: Path, *, runtime: dict[str, object] | None = None,
) -> Path:
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-main-session-test",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution = tmp_path / "execution.json"
    document = {
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-main-session-test",
        "adapter_config_path": str(adapter),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "max_response_bytes": 65536,
    }
    if runtime is not None:
        document["runtime"] = runtime
    execution.write_text(json.dumps(document), encoding="utf-8")
    return execution


def _new_session(
        tmp_path: Path, *, task_control=None,
) -> tuple[MainSession, Path, Path]:
    root = tmp_path / "session"
    execution = _write_execution_profile(tmp_path)
    return (
        MainSession(root, execution, task_control=task_control),
        root,
        execution,
    )


def test_fresh_session_holds_owner_lease_before_registry_becomes_visible(
        tmp_path: Path,
) -> None:
    root = tmp_path / "reserved-session"
    execution = _write_execution_profile(tmp_path)

    lease = MainSessionOwnerLease.reserve_for_creation(root)
    try:
        assert {path.name for path in root.iterdir()} == {
            MainSession.OWNER_LOCK_FILE}
        assert root.stat().st_mode & 0o777 == 0o700
        with pytest.raises(ValueError, match="absent root"):
            MainSessionOwnerLease.reserve_for_creation(root)
        session = MainSession(
            root, execution, owner_root_reserved=True)
        assert session.root == root.resolve()
        with pytest.raises(RuntimeError, match="owner lease"):
            MainSessionOwnerLease(root)
    finally:
        lease.close()

    with MainSessionOwnerLease(root):
        pass


def test_new_session_registry_and_prepare_retry_use_exact_attempt(
        tmp_path: Path,
) -> None:
    session, root, _execution = _new_session(tmp_path)
    reader = MainThreadRegistry(
        _RegistryCore(root / "main", create=False, read_only=True),
        session_root=root,
    )
    initial = reader.recover_thread()
    assert initial["next_turn_ordinal"] == 1
    assert initial["committed_history"] == []

    first = session.prepare_turn("question")
    object_count = len(session._registry_core.event_store.object_rows())
    second = session.prepare_turn("question")

    assert second == first
    assert first.run_dir == root / "main" / "turn-0001"
    assert len(session._registry_core.event_store.object_rows()) == object_count
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "turn_active"
    assert projection["next_turn_ordinal"] == 2
    active = session._active_turn()
    assert active is not None
    assert active[1]["state"] == "running"
    assert active[1]["attempt_relative_path"] == "turn-0001"


def test_markerless_session_keeps_legacy_session_relative_children(
        tmp_path: Path,
) -> None:
    session, root, execution = _new_session(tmp_path)
    with sqlite3.connect(
            root / "main" / ".registry_v1" / "registry.sqlite3") as db:
        db.execute(
            "DELETE FROM registry_meta WHERE key=?",
            (MainThreadRegistry.CHILD_PATH_BASE_META,))

    resumed = MainSession.resume(root, execution)
    spec = resumed.prepare_turn("legacy question")
    active = resumed._active_turn()

    assert resumed.child_path_root == root
    assert spec.run_dir == root / "main" / "turn-0001"
    assert active is not None
    assert active[1]["attempt_relative_path"] == "main/turn-0001"


def test_main_turn_uses_profile_runtime_policy_without_source_edits(
        tmp_path: Path,
) -> None:
    execution = _write_execution_profile(tmp_path, runtime={
        "max_turns_per_node": 5,
        "max_parallel_nodes": 3,
        "main_history_message_limit": 2,
        "context_pressure_trigger_ratio": 0.8,
        "context_tool_output_byte_limit": 512,
        "workspace": {
            "timeout_seconds": 9,
            "memory_bytes": 268435456,
            "process_limit": 4,
            "source_size_bytes": 4096,
            "input_size_bytes": 8192,
        },
    })
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("next question")
    prompt = _main_prompt((
        ("user", "old user"),
        ("assistant", "old answer"),
        ("user", "recent user"),
        ("assistant", "recent answer"),
    ), "next question", history_message_limit=2)

    assert spec.max_attempts_per_stage == 5
    assert spec.max_parallel_nodes == 1  # Main turn itself is single-agent.
    assert "recent user" in prompt
    assert "recent answer" in prompt
    assert "old user" not in prompt
    assert "old answer" not in prompt


def test_long_main_session_keeps_owner_socket_relative_to_turn_registry(
        tmp_path: Path,
) -> None:
    root = tmp_path / ("session-" + "x" * 100)
    session = MainSession(root, _write_execution_profile(tmp_path))

    first = session.prepare_turn("question")
    second = session.prepare_turn("question")

    assert len(os.fsencode(str(first.run_dir / "owner.sock"))) >= 108
    assert first.owner_socket_path is not None
    assert first.owner_socket_path == first.run_dir / "owner.sock"
    assert second.owner_socket_path == first.owner_socket_path


def test_active_turn_snapshot_reports_registered_pending_start(
        tmp_path: Path,
) -> None:
    session, root, _execution = _new_session(tmp_path)
    session.prepare_turn("question")

    snapshot = session.active_turn_snapshot()

    assert snapshot is not None
    assert snapshot.ordinal == 1
    assert snapshot.user_text == "question"
    assert snapshot.state == "pending_start"
    assert snapshot.attempt_path == root / "main" / "turn-0001"
    assert snapshot.registry_observation is None
    assert session.reconcile_active_turn().state == "pending_start"


def test_active_turn_resume_rejects_execution_profile_change_without_rewrite(
        tmp_path: Path,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("keep this exact route")
    replacement = tmp_path / "replacement-execution.json"
    profile_path = root / MainSession.PROFILE_FILE
    state_path = root / MainSession.STATE_FILE
    profile_before = profile_path.read_bytes()
    state_before = state_path.read_bytes()

    with pytest.raises(
            ValueError, match="cannot change the execution profile"):
        MainSession.resume(root, replacement)
    with pytest.raises(
            ValueError, match="cannot change the execution profile"):
        session.set_execution_config(replacement)

    assert session.execution_config_path == execution.resolve()
    assert profile_path.read_bytes() == profile_before
    assert state_path.read_bytes() == state_before


def test_idle_resume_requires_persisted_profile_and_rejects_explicit_change(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("finish this turn")
    _install_child_observation(monkeypatch, session, "terminal")
    session.complete_turn(
        "finish this turn", {"reply": "finished", "task": None})
    replacement = tmp_path / "replacement-execution.json"

    with pytest.raises(
            ValueError, match="cannot change the execution profile"):
        MainSession.resume(root, replacement)

    (root / MainSession.PROFILE_FILE).unlink()
    session.state_path.unlink()
    with pytest.raises(ValueError, match="persisted execution profile"):
        MainSession.resume(root, execution)


def test_idle_resume_rejects_in_place_profile_identity_change(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("finish before drift")
    _install_child_observation(monkeypatch, session, "terminal")
    session.complete_turn(
        "finish before drift", {"reply": "finished", "task": None})
    selection = json.loads(execution.read_text(encoding="utf-8"))
    adapter = Path(selection["adapter_config_path"])
    selection["model_condition"] = "changed-idle-session-model"
    adapter_document = json.loads(adapter.read_text(encoding="utf-8"))
    adapter_document["model_condition"] = "changed-idle-session-model"
    execution.write_text(json.dumps(selection), encoding="utf-8")
    adapter.write_text(json.dumps(adapter_document), encoding="utf-8")

    with pytest.raises(ValueError, match="identity changed"):
        MainSession.resume(root)


def test_active_turn_rejects_in_place_profile_identity_change_before_worker(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("keep this exact route")
    selection = json.loads(execution.read_text(encoding="utf-8"))
    adapter = Path(selection["adapter_config_path"])
    selection["model_condition"] = "changed-main-session-model"
    adapter_document = json.loads(adapter.read_text(encoding="utf-8"))
    adapter_document["model_condition"] = "changed-main-session-model"
    execution.write_text(json.dumps(selection), encoding="utf-8")
    adapter.write_text(json.dumps(adapter_document), encoding="utf-8")
    calls = 0

    def unexpected_worker(_spec):
        nonlocal calls
        calls += 1
        raise AssertionError("task worker must not start")

    monkeypatch.setattr("cpn.rpnh.main_session.run_agent_task", unexpected_worker)

    with pytest.raises(ValueError, match="identity changed"):
        MainSession.resume(root)
    with pytest.raises(ValueError, match="identity changed"):
        session.set_execution_config(execution)
    with pytest.raises(ValueError, match="identity changed"):
        session.turn("keep this exact route")

    assert calls == 0


def test_active_turn_resume_rejects_corrupt_v2_profile_without_legacy_fallback(
        tmp_path: Path,
) -> None:
    session, root, _execution = _new_session(tmp_path)
    session.prepare_turn("keep this exact route")
    (root / MainSession.PROFILE_FILE).write_text(
        '{"schema_version":"rpnh/main_session_profile/v2"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid execution profile"):
        MainSession.resume(root)


def test_active_turn_resume_rejects_local_executable_change(
        tmp_path: Path,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("keep this exact local executable")
    selection = json.loads(execution.read_text(encoding="utf-8"))
    adapter = Path(selection["adapter_config_path"])
    adapter_document = json.loads(adapter.read_text(encoding="utf-8"))
    adapter_document["argv"][0] = "/opt/changed/local-model-runner"
    adapter.write_text(json.dumps(adapter_document), encoding="utf-8")

    with pytest.raises(ValueError, match="identity changed"):
        MainSession.resume(root)


def test_active_turn_allows_unchanged_profile_identity(
        tmp_path: Path,
) -> None:
    session, root, _execution = _new_session(tmp_path)
    session.prepare_turn("keep this exact route")

    resumed = MainSession.resume(root)

    assert resumed.execution_config_path == session.execution_config_path


def test_paused_turn_rejects_in_place_profile_identity_before_resume_worker(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, execution = _new_session(tmp_path)
    session.prepare_turn("keep this paused route")
    active = session._active_turn()
    assert active is not None
    selection = json.loads(execution.read_text(encoding="utf-8"))
    adapter = Path(selection["adapter_config_path"])
    selection["model_condition"] = "changed-paused-session-model"
    adapter_document = json.loads(adapter.read_text(encoding="utf-8"))
    adapter_document["model_condition"] = "changed-paused-session-model"
    execution.write_text(json.dumps(selection), encoding="utf-8")
    adapter.write_text(json.dumps(adapter_document), encoding="utf-8")
    monkeypatch.setattr(session, "active_turn_snapshot", lambda: MainTurnSnapshot(
        ordinal=1,
        user_text="keep this paused route",
        required_task_kind=None,
        state="stopped_by_owner",
        attempt_path=session.root / "main" / "turn-0001",
        registered_output=None,
        registry_observation=None,
    ))
    calls = 0

    def unexpected_worker(_spec):
        nonlocal calls
        calls += 1
        raise AssertionError("resume worker must not start")

    monkeypatch.setattr(
        "cpn.rpnh.main_session.resume_agent_task", unexpected_worker)

    with pytest.raises(ValueError, match="identity changed"):
        session.resume_paused_turn()

    assert calls == 0


def test_legacy_v1_profile_is_readable_and_upgrades_on_resume(
        tmp_path: Path,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("legacy authority")
    profile_path = root / MainSession.PROFILE_FILE
    profile_path.write_text(json.dumps({
        "schema_version": "rpnh/main_session_profile/v1",
        "execution_config_path": str(execution.resolve()),
    }), encoding="utf-8")

    MainSession.resume(root)

    assert json.loads(profile_path.read_text(encoding="utf-8"))["schema_version"] == (
        "rpnh/main_session_profile/v2")


def test_complete_turn_registers_receipt_commits_and_is_idempotent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _TaskControl()
    control.fail_once = True
    session, _root, _execution = _new_session(
        tmp_path, task_control=control)
    session.prepare_turn("delegate this")
    _install_child_observation(monkeypatch, session, "terminal")
    output = {
        "reply": "launched",
        "task": {
            "kind": "single_agent",
            "prompt": "do the work",
            "instruction": "Return the result.",
        },
    }

    with pytest.raises(RuntimeError, match="launch failed"):
        session.complete_turn("delegate this", output)
    assert session.history == [
        ("user", "delegate this"), ("assistant", "launched")]
    decision, handle = session.complete_turn("delegate this", output)
    object_count = len(session._registry_core.event_store.object_rows())
    retried, retried_handle = session.complete_turn("delegate this", output)

    assert decision == retried
    assert handle is retried_handle is control.handle
    assert len(control.specs) == 2
    assert len(session._registry_core.event_store.object_rows()) == object_count
    assert session.history == [
        ("user", "delegate this"), ("assistant", "launched")]
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "idle"
    assert len(projection["committed_history"]) == 1
    committed = projection["committed_history"][0]
    assert committed["execution_receipt_ref"] is not None
    assert committed["answer"]["reply"] == "launched"
    links = projection["child_registry_links"]
    assert len(links) == 1
    assert links[0]["task_control_id"] == "task-child"
    assert links[0]["task_kind"] == "single_agent"
    assert links[0]["registry_relative_path"] == (
        "tasks/runs/main-turn-0001-child")
    assert links[0]["origin_main_turn_ref"] == committed["turn_ref"]
    assert links[0]["state"] == "launch_registered"


def test_direct_agent_launch_gets_an_independent_registry_index(
        tmp_path: Path,
) -> None:
    control = _TaskControl()
    control.initialize_child_registry = True
    session, root, execution = _new_session(
        tmp_path, task_control=control)

    handle = session.launch(
        "do independent work",
        stages=(AgentStage("worker", "Return the result."),),
    )

    assert handle is control.handle
    projection = session._main_thread.recover_thread()
    assert projection["committed_history"] == []
    links = projection["child_registry_links"]
    assert len(links) == 1
    assert links[0]["task_control_id"] == "task-child"
    assert links[0]["task_kind"] == "single_agent"
    assert links[0]["origin_main_turn_ref"] is None
    assert links[0]["registry_relative_path"].startswith(
        "tasks/runs/child-")
    assert (root / "main" / links[0]["registry_relative_path"]).resolve() == (
        control.registered_spec.run_dir.resolve())

    assert control.child_registry_core is not None
    identity = _bootstrap_identity(
        control.child_registry_core,
        NativeBootstrapManifest(("test-protocol/v1",)),
    )
    resumed = MainSession.resume(
        root, execution, task_control=control)
    reconciled = resumed._main_thread.recover_thread()[
        "child_registry_links"]
    assert len(reconciled) == 1
    assert len(control.specs) == 1
    assert reconciled[0]["state"] == "launch_registered"

    resumed.activate_for_execution()
    reconciled = resumed._main_thread.recover_thread()[
        "child_registry_links"]

    assert len(control.specs) == 1
    assert reconciled[0]["state"] == "registry_attached"
    assert reconciled[0]["child_task_ref"] == _payload(identity.task_ref)
    assert reconciled[0]["child_run_ref"] == _payload(identity.run_ref)
    object_count = len(resumed._registry_core.event_store.object_rows())
    assert resumed.reconcile_child_registry_links() == tuple(reconciled)
    assert len(resumed._registry_core.event_store.object_rows()) == object_count


@pytest.mark.parametrize("registered_before_failure", [False, True])
def test_resume_projection_is_effect_free_until_activation_compensates_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        registered_before_failure: bool,
) -> None:
    control = _TaskControl()
    if registered_before_failure:
        control.fail_after_register_once = True
    else:
        control.fail_once = True
    session, root, execution = _new_session(
        tmp_path, task_control=control)
    session.prepare_turn("delegate durably")
    _install_child_observation(monkeypatch, session, "terminal")
    output = {
        "reply": "launch it",
        "task": {
            "kind": "single_agent",
            "prompt": "perform exact work",
            "instruction": "Return the exact result.",
        },
    }

    with pytest.raises(RuntimeError, match="launch failed"):
        session.complete_turn("delegate durably", output)
    committed_spec = control.specs[0]

    resumed = MainSession.resume(
        root, execution, task_control=control)
    resumed_again = MainSession.resume(
        root, execution, task_control=control)

    assert len(control.specs) == 1
    history = resumed.display_history
    latest = resumed.latest_committed_reconciliation()
    assert resumed_again.reconcile_active_turn().state == "idle"
    assert len(control.specs) == 1
    if registered_before_failure:
        assert history[-1] == (
            "assistant", "launch it\n\n[launched task-child: single_agent]")
        assert latest.child is control.handle
    else:
        assert history[-1] == ("assistant", "launch it")
        assert latest.child is None

    resumed_again.activate_for_execution()
    resumed_again.activate_for_execution()

    expected_starts = 1 if registered_before_failure else 2
    assert len(control.specs) == expected_starts
    recovered_spec = control.registered_spec
    assert recovered_spec is not None
    assert recovered_spec.run_dir == committed_spec.run_dir
    assert recovered_spec.prompt == committed_spec.prompt
    assert recovered_spec.stages == committed_spec.stages
    assert recovered_spec.workflow_graph == committed_spec.workflow_graph
    assert resumed_again.display_history[-1] == (
        "assistant", "launch it\n\n[launched task-child: single_agent]")
    assert resumed_again.reconcile_active_turn().state == "idle"
    assert len(control.specs) == expected_starts


def test_conflicting_active_request_is_rejected_before_launch_compensation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _TaskControl()
    control.fail_once = True
    session, _root, _execution = _new_session(
        tmp_path, task_control=control)
    session.prepare_turn("commit a child")
    _install_child_observation(monkeypatch, session, "terminal")
    with pytest.raises(RuntimeError, match="launch failed"):
        session.complete_turn("commit a child", {
            "reply": "child committed",
            "task": {
                "kind": "single_agent",
                "prompt": "perform exact work",
                "instruction": "Return the exact result.",
            },
        })
    assert len(control.specs) == 1

    projection = session._main_thread.recover_thread()
    session._main_thread.accept_turn(
        thread_ref=session._version_ref(projection["thread_ref"]),
        user_input={"text": "expected input", "required_task_kind": None},
        expected_ordinal=int(projection["next_turn_ordinal"]),
        idempotency_key="test:accepted-before-prepare",
    )

    with pytest.raises(ValueError, match="differs from the active"):
        session.prepare_turn("conflicting input")
    assert len(control.specs) == 1


def test_new_turn_prepare_activates_committed_launch_compensation_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _TaskControl()
    control.fail_once = True
    session, root, execution = _new_session(
        tmp_path, task_control=control)
    session.prepare_turn("commit child first")
    _install_child_observation(monkeypatch, session, "terminal")
    with pytest.raises(RuntimeError, match="launch failed"):
        session.complete_turn("commit child first", {
            "reply": "child committed",
            "task": {
                "kind": "single_agent",
                "prompt": "perform committed work",
                "instruction": "Return the exact result.",
            },
        })
    resumed = MainSession.resume(root, execution, task_control=control)

    assert len(control.specs) == 1
    first = resumed.prepare_turn("start the next turn")
    second = resumed.prepare_turn("start the next turn")

    assert first == second
    assert first.run_dir.name == "turn-0002"
    assert len(control.specs) == 2


@pytest.mark.parametrize("projection_state", ["missing", "corrupt", "lying"])
def test_resume_rebuilds_projection_from_registry_authority(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        projection_state: str,
) -> None:
    session, root, execution = _new_session(tmp_path)
    session.prepare_turn("remember me")
    _install_child_observation(monkeypatch, session, "terminal")
    session.complete_turn(
        "remember me", {"reply": "remembered", "task": None})
    if projection_state == "missing":
        session.state_path.unlink()
    elif projection_state == "corrupt":
        session.state_path.write_text("{not json", encoding="utf-8")
    else:
        session.state_path.write_text(json.dumps({
            "schema_version": "rpnh/main_session_state/v1",
            "execution_config_path": "/wrong/profile.json",
            "turn_ordinal": 99,
            "history": [
                {"role": "user", "body": "forged"},
                {"role": "assistant", "body": "forged"},
            ],
        }), encoding="utf-8")

    resumed = MainSession.resume(root)

    assert resumed.execution_config_path == execution.resolve()
    assert resumed.turn_ordinal == 1
    assert resumed.history == [
        ("user", "remember me"), ("assistant", "remembered")]
    rebuilt = json.loads(resumed.state_path.read_text(encoding="utf-8"))
    assert rebuilt["turn_ordinal"] == 1
    assert rebuilt["history"][0]["body"] == "remember me"


def test_interrupted_receipt_does_not_enter_history_and_retry_is_idempotent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, _execution = _new_session(tmp_path)
    session.prepare_turn("stop this")
    _install_child_observation(monkeypatch, session, "stopped_by_owner")

    session.complete_interrupted_turn("stop this")
    object_count = len(session._registry_core.event_store.object_rows())
    session.complete_interrupted_turn("stop this")

    assert session.history == []
    assert session.turn_ordinal == 1
    assert len(session._registry_core.event_store.object_rows()) == object_count
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "stopped"
    assert projection["committed_history"] == []
    next_spec = session.prepare_turn("try again")
    assert next_spec.run_dir.name == "turn-0002"


def test_complete_turn_rejects_absent_child_registry_without_history(
        tmp_path: Path,
) -> None:
    session, _root, _execution = _new_session(tmp_path)
    session.prepare_turn("question")

    with pytest.raises(
            MainThreadAuthorityError, match="readable child Registry"):
        session.complete_turn(
            "question", {"reply": "unregistered", "task": None})

    assert session.history == []
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "turn_active"
    assert projection["committed_history"] == []


def test_real_terminal_child_registry_is_receipt_authority(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("answer exactly")
    output = {"reply": "registered answer", "task": None}
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: _TerminalPort(output))

    result = run_agent_task(spec)
    decision, handle = session.complete_turn("answer exactly", result["output"])

    assert result["stop_reason"] == "terminal"
    assert decision.reply == "registered answer"
    assert handle is None
    committed = session._main_thread.recover_thread()["committed_history"][0]
    receipt_ref = session._version_ref(committed["execution_receipt_ref"])
    receipt = session._main_thread._read_exact(
        session._registry_core, receipt_ref,
        expected_type="main_turn_execution_receipt/v1")
    assert receipt["outcome"] == "terminal"
    assert receipt["child_run_ref"] == result["run_ref"]
    assert receipt["terminal_evidence_ref"] == result["terminal_evidence_ref"]


def test_main_turn_accepts_direct_text_write_without_double_json_quoting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("answer using direct text")
    output = {"reply": "direct text accepted", "task": None}
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: (
            _DirectTextTerminalPort(output)))

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert json.loads(result["output"]) == output


def test_task_call_cap_handoff_does_not_refire_interrupted_input(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    prepared = session.prepare_turn("do not finish")
    spec = AgentTaskSpec(
        run_dir=prepared.run_dir,
        prompt=prepared.prompt,
        stages=prepared.stages,
        execution_config_path=prepared.execution_config_path,
        max_attempts_per_stage=1,
        max_parallel_nodes=prepared.max_parallel_nodes,
        execution_profiles=prepared.execution_profiles,
        owner_statement=prepared.owner_statement,
        owner_socket_path=prepared.owner_socket_path,
    )
    port = _NonterminalPort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    result = run_agent_task(spec)
    reader = _RegistryCore(spec.run_dir, create=False, read_only=True)

    assert result["stop_reason"] == "task_model_call_cap"
    assert result["terminal_evidence_ref"] is None
    assert result["output"] is None
    assert port.calls == 1
    assert len(reader.event_store.list_events_by_type((
        "firing_admitted/v1",))) == 1
    assert len(reader.event_store.list_events_by_type((
        "transition_firing_settled/v1",))) == 1


def test_two_registry_queries_share_the_committed_loop_authority(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("query twice before answering")
    output = {"reply": "queries settled", "task": None}
    port = _DoubleQueryThenTerminalPort(output)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert json.loads(result["output"]) == output
    assert port.calls == 2
    reader = _RegistryCore(
        spec.run_dir, create=False, read_only=True)
    settled_tools = [
        event.payload["tool_name"]
        for event in reader.event_store.list_events_by_type((
            "agent_action_settled/v1",))]
    assert settled_tools == [
        "query_registry_resources",
        "query_registry_resources",
        "write_file",
        "complete_interaction",
    ]


def test_reconcile_real_terminal_child_commits_registered_output(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("recover exactly")
    output = {"reply": "recovered answer", "task": None}
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: _TerminalPort(output))
    run_agent_task(spec)

    snapshot = session.active_turn_snapshot()
    reconciled = session.reconcile_active_turn()

    assert snapshot is not None
    assert snapshot.state == "terminal"
    assert json.loads(snapshot.registered_output) == output
    assert reconciled.state == "committed"
    assert reconciled.decision is not None
    assert reconciled.decision.reply == "recovered answer"
    assert session.history == [
        ("user", "recover exactly"),
        ("assistant", "recovered answer"),
    ]


def test_real_stopped_child_registry_pauses_until_explicit_rollback(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    session = MainSession(tmp_path / "session", execution)
    spec = session.prepare_turn("interrupt exactly")
    port = _StoppedPort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    def interrupt_workspace(**_kwargs):
        os.kill(os.getpid(), signal.SIGINT)
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        interrupt_workspace)
    result = run_agent_task(spec)
    child_registry = spec.run_dir / ".registry_v1" / "registry.sqlite3"

    reconciled = session.reconcile_active_turn()

    assert result["stop_reason"] == "stopped_by_owner"
    assert reconciled.state == "paused"
    assert session._main_thread.recover_thread()["state"] == "turn_active"
    assert session.history == []
    assert child_registry.is_file()

    rolled_back = session.rollback_paused_turn()

    assert rolled_back.state == "interrupted"
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "stopped"
    assert projection["committed_history"] == []
    assert child_registry.is_file()


def test_owner_stop_cancels_llm_input_and_explicit_resume_uses_same_registry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _write_execution_profile(tmp_path)
    spec = AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="pause during provider input",
        stages=(AgentStage("worker", "Return the result."),),
        execution_config_path=execution,
    )
    interrupted = _OwnerInterruptedInputPort()
    terminal = _TerminalPort(
        "resumed result", output_port_id="worker.result")
    ports = iter((interrupted, terminal))
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: next(ports))

    stopped = run_agent_task(spec)

    assert stopped["stop_reason"] == "stopped_by_owner"
    assert interrupted.calls == 1
    assert interrupted.interruption_latency is not None
    assert interrupted.interruption_latency < 1
    assert (spec.run_dir / ".registry_v1" / "registry.sqlite3").is_file()
    stopped_reader = _RegistryCore(
        spec.run_dir, create=False, read_only=True)
    interruption_events = tuple(
        stopped_reader.event_store.list_events_by_type((event_type,))[0]
        for event_type in (
            "provider_attempt_owner_interrupted/v1",
            "llm_call_owner_interrupted/v1",
            "llm_invocation_owner_interrupted/v1",
        ))
    assert len({event.transaction_id for event in interruption_events}) == 1
    assert len({json.dumps(event.payload, sort_keys=True)
                for event in interruption_events}) == 1
    interruption_event_ids = tuple(
        str(event.event_id) for event in interruption_events)
    interrupted_payload = interruption_events[0].payload
    assert interrupted_payload["submission_state"] == "not_submitted"
    assert stopped_reader.event_store.actual_model_call_counts() == (0, 0)

    resumed = resume_agent_task(spec)

    assert resumed["stop_reason"] == "terminal"
    assert json.loads(resumed["output"]) == "resumed result"
    assert resumed["actual_model_call_counts"] == [1, 0]
    resumed_reader = _RegistryCore(
        spec.run_dir, create=False, read_only=True)
    for object_type, payload_field in (
            ("provider_attempt_spec/v1", "provider_attempt_ref"),
            ("llm_call_spec/v2", "llm_call_ref"),
            ("llm_invocation_spec/v1", "llm_invocation_ref"),
            ("llm_invocation_attempt/v1",
             "llm_invocation_attempt_ref")):
        rows = resumed_reader.event_store.object_rows_by_type(object_type)
        assert len(rows) == 2
        logical_ids = {row["logical_id"] for row in rows}
        assert len(logical_ids) == 2
        assert interrupted_payload[payload_field]["logical_id"] in logical_ids
    retained_events = tuple(
        resumed_reader.event_store.list_events_by_type((event_type,))[0]
        for event_type in (
            "provider_attempt_owner_interrupted/v1",
            "llm_call_owner_interrupted/v1",
            "llm_invocation_owner_interrupted/v1",
        ))
    assert tuple(str(event.event_id) for event in retained_events) == (
        interruption_event_ids)
    assert all(event.payload == interrupted_payload
               for event in retained_events)


def test_paused_main_turn_resumes_the_same_child_registry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, root, execution = _new_session(tmp_path)
    spec = session.prepare_turn("continue this")
    _RegistryCore(spec.run_dir, create=True)
    marker = spec.run_dir / "child-registry-marker"
    marker.write_text("retained", encoding="utf-8")
    _install_child_observation(
        monkeypatch, session, "stopped_by_owner")
    resumed_specs = []

    def resume_same_registry(resumed_spec):
        resumed_specs.append(resumed_spec)
        _install_child_observation(monkeypatch, session, "terminal")
        return {
            "stop_reason": "terminal",
            "output": {"reply": "continued", "task": None},
        }

    monkeypatch.setattr(
        "cpn.rpnh.main_session.resume_agent_task",
        resume_same_registry)

    assert session.reconcile_active_turn().state == "paused"
    decision, child = session.resume_paused_turn()

    assert decision.reply == "continued"
    assert child is None
    assert len(resumed_specs) == 1
    assert resumed_specs[0].run_dir == root / "main" / "turn-0001"
    assert resumed_specs[0].execution_config_path == execution.resolve()
    assert marker.read_text(encoding="utf-8") == "retained"
    assert session.history == [
        ("user", "continue this"),
        ("assistant", "continued"),
    ]


def test_turn_stop_does_not_implicitly_rollback_main_history(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, _execution = _new_session(tmp_path)

    def stop_at_checkpoint(_spec):
        _RegistryCore(_spec.run_dir, create=True)
        _install_child_observation(
            monkeypatch, session, "stopped_by_owner")
        return {"stop_reason": "stopped_by_owner", "output": None}

    monkeypatch.setattr(
        "cpn.rpnh.main_session.run_agent_task", stop_at_checkpoint)

    with pytest.raises(MainSessionPaused, match="paused"):
        session.turn("pause this")

    assert session.reconcile_active_turn().state == "paused"
    assert session._main_thread.recover_thread()["state"] == "turn_active"
    assert session.history == []


def test_nonterminal_child_fails_main_turn_without_forging_terminal_result(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, _execution = _new_session(tmp_path)

    def return_nonterminal(spec):
        _RegistryCore(spec.run_dir, create=True)
        _install_child_observation(monkeypatch, session, "running")
        return {"stop_reason": "blocked_or_waiting", "output": None}

    monkeypatch.setattr(
        "cpn.rpnh.main_session.run_agent_task", return_nonterminal)

    with pytest.raises(
            MainSessionExecutionFailed,
            match="blocked_or_waiting"):
        session.turn("fail cleanly")

    projection = session._main_thread.recover_thread()
    assert projection["state"] == "idle"
    assert projection["active_turn_ref"] is None
    assert projection["committed_history"] == []
    assert session.history == []
    failed_turn = session._main_thread._read_exact(
        session._registry_core,
        session._version_ref(projection["latest_turn_ref"]),
        expected_type="main_turn/v1",
    )
    assert failed_turn["state"] == "failed"
    receipt = session._main_thread._read_exact(
        session._registry_core,
        session._version_ref(failed_turn["execution_receipt_ref"]),
        expected_type="main_turn_execution_receipt/v1",
    )
    assert receipt["outcome"] == "running"
    assert receipt["terminal_evidence_ref"] is None
    assert session.prepare_turn("next turn").run_dir.name == "turn-0002"


def test_explicit_main_rollback_returns_to_prior_completed_turn_only(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, _execution = _new_session(tmp_path)
    session.prepare_turn("first")
    _install_child_observation(monkeypatch, session, "terminal")
    session.complete_turn("first", {"reply": "first done", "task": None})

    second = session.prepare_turn("second")
    _RegistryCore(second.run_dir, create=True)
    marker = second.run_dir / "retained-child-state"
    marker.write_text("keep", encoding="utf-8")
    _install_child_observation(
        monkeypatch, session, "stopped_by_owner")

    session.rollback_paused_turn()

    assert session.history == [
        ("user", "first"),
        ("assistant", "first done"),
    ]
    assert marker.read_text(encoding="utf-8") == "keep"
    projection = session._main_thread.recover_thread()
    assert projection["state"] == "stopped"
    assert len(projection["committed_history"]) == 1
    latest = projection["latest_turn_ref"]
    interrupted_turn = session._main_thread._read_exact(
        session._registry_core,
        VersionRef(
            latest["entity_type"],
            TypedId.parse(latest["logical_id"], expected="resource"),
            TypedId.parse(latest["version_id"], expected="resource_version"),
        ),
        expected_type="main_turn/v1",
    )
    assert interrupted_turn["state"] == "interrupted"
    assert interrupted_turn["attempt_relative_path"] == "turn-0002"
    assert session.prepare_turn("third").run_dir.name == "turn-0003"


def test_main_rollback_preserves_indexed_independent_child_registry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _TaskControl()
    control.initialize_child_registry = True
    session, _root, _execution = _new_session(
        tmp_path, task_control=control)
    session.prepare_turn("launch child")
    _install_child_observation(monkeypatch, session, "terminal")
    session.complete_turn("launch child", {
        "reply": "child launched",
        "task": {
            "kind": "single_agent",
            "prompt": "independent child work",
            "instruction": "Return the result.",
        },
    })
    assert control.registered_spec is not None
    child_marker = control.registered_spec.run_dir / "child-owned-state"
    child_marker.write_text("retained", encoding="utf-8")
    link_before = session._main_thread.recover_thread()[
        "child_registry_links"][0]

    paused = session.prepare_turn("pause a later main turn")
    _RegistryCore(paused.run_dir, create=True)
    _install_child_observation(
        monkeypatch, session, "stopped_by_owner")
    session.rollback_paused_turn()

    links_after = session._main_thread.recover_thread()[
        "child_registry_links"]
    assert links_after == [link_before]
    assert child_marker.read_text(encoding="utf-8") == "retained"
    assert (control.registered_spec.run_dir
            / ".registry_v1" / "registry.sqlite3").is_file()


def test_turn_does_not_accept_process_result_as_terminal_evidence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _root, _execution = _new_session(tmp_path)
    monkeypatch.setattr(
        "cpn.rpnh.main_session.run_agent_task",
        lambda _spec: {
            "stop_reason": "terminal",
            "terminal_evidence_ref": {"forged": True},
            "output": {"reply": "not registered", "task": None},
        })

    with pytest.raises(
            MainThreadAuthorityError, match="readable child Registry"):
        session.turn("question")

    assert session.history == []
    assert session._main_thread.recover_thread()["state"] == "turn_active"
