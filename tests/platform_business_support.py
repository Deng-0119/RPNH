"""Offline platform business acceptance over the production Registry/PN path.

The response port supplies deterministic fixture bytes. ``native`` retains the
production OwnerEventLoop transport. ``session-pipe`` is an explicit test-only
transport substitution for restricted sessions; it never grants native scope.
No provider process, network/model request, package install, or GUI is launched.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import tempfile
import time
from unittest.mock import patch


SCENARIOS = ("main_session_pause_handoff_resume", "parallel_checkpoint_continuation")


def native_ipc_observation() -> dict:
    """Observe AF_UNIX construction and bind without a fallback transport."""
    try:
        with tempfile.TemporaryDirectory(prefix="rpnh-platform-ipc-") as directory:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(Path(directory) / "owner.sock"))
                listener.listen(1)
    except OSError as error:
        return {"status": "BLOCKED", "errno": error.errno,
                "error_type": type(error).__name__, "message": str(error)}
    return {"status": "PASS", "scope": "AF_UNIX construction/bind/listen"}


def _require(condition: bool, description: str) -> None:
    if not condition:
        raise AssertionError(description)


def _continuation_helper(explicit=None):
    if explicit is not None:
        return explicit
    from examples.project_collaboration.scenario import continuation_experiment
    return continuation_experiment


@contextmanager
def transport_scope(transport: str, *, continuation_module=None, pipe_owner_loop=None):
    if transport not in {"native", "session-pipe"}:
        raise ValueError("transport must be native or session-pipe")
    with ExitStack() as stack:
        if transport == "session-pipe":
            if pipe_owner_loop is None:
                from multi_tool_offline_support import SessionPipeOwnerEventLoop
                pipe_owner_loop = SessionPipeOwnerEventLoop
            from cpn.rpnh import agent_tasks
            continuation_experiment = _continuation_helper(continuation_module)
            stack.enter_context(patch.object(
                agent_tasks, "OwnerEventLoop", pipe_owner_loop))
            stack.enter_context(patch.object(
                continuation_experiment, "OwnerEventLoop", pipe_owner_loop))
        yield


def _profile(directory: Path) -> Path:
    directory.mkdir(parents=True)
    adapter = directory / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-platform-business-fixture",
        "argv": [sys.executable, "-c",
                 "raise RuntimeError('offline fixture port was not supplied')"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    execution = directory / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-platform-business-fixture",
        "adapter_config_path": str(adapter), "timeout_seconds": 30,
        "max_output_tokens": 1024, "max_response_bytes": 65536,
    }), encoding="utf-8")
    return execution


def _response(calls):
    from cpn.rpnh.llm_contracts import LLMInputResponseBytes
    return LLMInputResponseBytes(json.dumps({
        "protocol": "llm_response_envelope/v1", "tool_calls": calls,
        "finish_reason": "tool_calls",
    }, sort_keys=True, separators=(",", ":")).encode(),
        status_code=None, external_request_id=None)


class _ReplyPort:
    """Two real registry queries, followed by a real write and completion."""
    def __init__(self, reply: str):
        self.reply, self.calls = reply, 0

    def request_once(self, attempt):
        self.calls += 1
        request = json.loads(attempt.canonical_request_bytes)
        if self.calls == 1:
            return _response([
                {"id": "query-outputs", "name": "query_registry_resources",
                 "arguments": json.dumps({"query": "outputs", "view": "current"})},
                {"id": "query-result", "name": "query_registry_resources",
                 "arguments": json.dumps({"query": "result", "view": "current"})},
            ])
        _require(self.calls == 2, "unexpected extra provider request")
        _require(sum(message.get("role") == "tool" for message in request["messages"]) >= 2,
                 "registered query results were not delivered to the next turn")
        return _response([
            {"id": "write-result", "name": "write_file", "arguments": json.dumps({
                "path": "outputs/platform-result.json", "description": "Offline platform result",
                "content": json.dumps({"reply": self.reply, "task": None}),
                "output_port_id": "main.result", "outcome_id": "complete"})},
            {"id": "complete", "name": "complete_interaction", "arguments": "{}"},
        ])

    def close(self):
        pass


class _OwnerStopPort:
    """Ask the production signal handler to stop before model submission."""
    def __init__(self):
        self.calls = 0

    def request_once(self, attempt):
        raise AssertionError("owner-stop coverage requires interruptible dispatch")

    def request_once_interruptible(self, attempt, *, interruption_requested):
        from cpn.rpnh.llm_contracts import LLMInputPortInterrupted
        self.calls += 1
        os.kill(os.getpid(), signal.SIGINT)
        deadline = time.monotonic() + 5
        while not interruption_requested():
            if time.monotonic() >= deadline:
                raise AssertionError("owner interruption did not reach the active input port")
            time.sleep(0.01)
        raise LLMInputPortInterrupted(submission_state="not_submitted")

    def close(self):
        pass


def _business_facts(session):
    store = session._registry_core.event_store
    authority = session._main_thread.recover_thread()
    return {
        "thread_ref": authority["thread_ref"],
        "active_turn_ref": authority["active_turn_ref"],
        "committed_history": authority["committed_history"],
        "events": len(store.list_events()),
        "objects": len(store.object_rows()),
    }


def main_session_chain(root: Path) -> dict:
    """Complete, pause, hand the same Registry across adapters, then resume."""
    from cpn.rpnh import agent_tasks
    from cpn.rpnh.main_session import MainSession, MainSessionPaused
    from cpn.rpnh.frontend_application import RegistryFrontendApplication, FrontendError
    from cpn.frontend.codex_app_server import CodexAppServer
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.session_access import MainSessionOwnerLease
    from cpn.rpnh.session_frontend import remember_session_frontend, read_session_frontend

    root.mkdir(parents=True)
    execution = _profile(root / "profiles")
    session_root = root / "session with spaces 会话"
    first, stop, last = _ReplyPort("first committed answer"), _OwnerStopPort(), _ReplyPort("continued answer")
    ports = iter((first, stop, last))
    adoptions = []
    with patch.dict(os.environ, {"RPNH_CONFIG": str(root / "user-config.json")}), patch.object(
            agent_tasks, "build_llm_input_port", lambda *args, **kwargs: next(ports)):
        with MainSessionOwnerLease.reserve_for_creation(session_root) as lease:
            session = MainSession(session_root, execution, owner_root_reserved=True,
                                  owner_lease=lease)
            remember_session_frontend(session_root, "basic", lease=lease)
            decision, handle = session.turn("Complete the first offline turn.")
            _require(decision.reply == first.reply and handle is None, "first turn did not complete")
            try:
                session.turn("Pause and continue the second offline turn.")
            except MainSessionPaused:
                pass
            else:
                raise AssertionError("owner stop did not pause the main turn")
            _require(session.reconcile_active_turn().state == "paused", "main turn is not paused")
            stopped_snapshot = session.active_turn_snapshot()
            child_root = stopped_snapshot.attempt_path
            _require(child_root is not None, "paused turn has no registered child path")
            before_handoff = _business_facts(session)
            expected_history = list(session.history)
            _require(len(expected_history) == 2, "paused turn entered committed history")
            child = _RegistryCore(child_root, create=False, read_only=True)
            identity_rows = [dict(row) for row in child.event_store.object_rows_by_type("native_run_identity/v1")]
            interruption_types = ("provider_attempt_owner_interrupted/v1",
                                  "llm_call_owner_interrupted/v1",
                                  "llm_invocation_owner_interrupted/v1")
            interrupted = child.event_store.list_events_by_type(interruption_types)
            interruption_ids = [str(event.event_id) for event in interrupted]
            _require(len(interrupted) == 3, "owner interruption receipts are incomplete")
            _require(len({str(event.transaction_id) for event in interrupted}) == 1,
                     "owner interruption did not settle in one transaction")
            _require(child.event_store.actual_model_call_counts() == (0, 0),
                     "not-submitted interruption was counted as a model call")
            try:
                RegistryFrontendApplication(session_root, execution, resume=True, frontend="dsh")
            except FrontendError as error:
                _require(error.code == "session_owned", "second owner failed for an unrelated reason")
            else:
                raise AssertionError("second presentation owner was admitted")

        for frontend in ("opencode", "dsh"):
            app = RegistryFrontendApplication(session_root, execution, resume=True, frontend=frontend)
            try:
                _require(len(app._sessions) == 1, "adapter did not adopt the exact session")
                adopted = next(iter(app._sessions.values())).session
                _require(adopted.root == session_root.resolve(), "adapter changed the Registry root")
                _require(adopted.history == expected_history, "adapter changed committed history")
                _require(_business_facts(adopted) == before_handoff,
                         "adapter adoption changed business authority")
                _require(read_session_frontend(session_root) == frontend, "frontend choice was not recorded")
                _require(bool(app.snapshot()), "adapter snapshot is empty")
                adoptions.append({"frontend": frontend, "status": "PASS",
                                  "scope": "production application adoption; no native client/UI"})
            finally:
                app.close()
        codex = CodexAppServer(session_root, execution)
        try:
            _require(len(codex._threads) == 1, "Codex adapter did not adopt the exact session")
            adopted = next(iter(codex._threads.values())).session
            _require(adopted.root == session_root.resolve(), "Codex changed the Registry root")
            _require(_business_facts(adopted) == before_handoff, "Codex adoption changed business authority")
            _require(read_session_frontend(session_root) == "codex", "Codex choice was not recorded")
            adoptions.append({"frontend": "codex", "status": "PASS",
                              "scope": "production application adoption; no native client/UI"})
        finally:
            codex.close()

        with MainSessionOwnerLease.acquire(session_root) as lease:
            resumed = MainSession.resume(session_root, execution, owner_lease=lease)
            remember_session_frontend(session_root, "basic", lease=lease)
            _require(resumed.active_turn_snapshot().attempt_path == child_root,
                     "resume selected another child Registry")
            decision, handle = resumed.resume_paused_turn()
            _require(decision.reply == last.reply and handle is None, "paused turn did not complete")
            _require(resumed.history == expected_history + [
                ("user", "Pause and continue the second offline turn."),
                ("assistant", last.reply)], "resume changed history or duplicated the first turn")
            after_child = _RegistryCore(child_root, create=False, read_only=True)
            _require([dict(row) for row in after_child.event_store.object_rows_by_type("native_run_identity/v1")]
                     == identity_rows, "resume replaced the child run identity")
            _require([str(event.event_id) for event in after_child.event_store.list_events_by_type(interruption_types)]
                     == interruption_ids, "resume rewrote interruption evidence")
            tools = [event.payload["tool_name"] for event in
                     after_child.event_store.list_events_by_type(("agent_action_settled/v1",))]
            _require(tools == ["query_registry_resources", "query_registry_resources", "write_file", "complete_interaction"],
                     "unexpected actual tool settlement order")
            _require((first.calls, stop.calls, last.calls) == (2, 1, 2),
                     "unexpected fixture provider call counts")
            return {"status": "PASS", "completed_turns": 2, "interrupted_attempts": 1,
                    "same_child_registry": True, "same_run_identity": True,
                    "interruption_receipts_retained": len(interruption_ids),
                    "actual_tools_after_resume": tools,
                    "fixture_input_calls": {"first": first.calls, "stopped": stop.calls, "resumed": last.calls},
                    "frontend_adoptions": adoptions, "double_owner_rejected": True,
                    "native_frontend_processes": 0, "real_model_api_calls": 0}


def continuation_chain(root: Path, *, continuation_module=None) -> dict:
    report = _continuation_helper(continuation_module).run_experiment(root, offline=True)
    _require(report["status"] == "PASS", "checkpoint continuation did not pass: " + json.dumps(report))
    _require(all(report["checks"].values()), "checkpoint continuation coverage is incomplete")
    return report


def run_acceptance(output: Path, *, transport: str, source_reference: str,
                   continuation_module=None, pipe_owner_loop=None,
                   scenario: str = "all") -> dict:
    if transport not in {"native", "session-pipe"}:
        raise ValueError("transport must be native or session-pipe")
    if not source_reference.strip():
        raise ValueError("an explicit source reference is required")
    if not isinstance(scenario, str) or scenario not in ("all", *SCENARIOS):
        raise ValueError("select all or one exact nonempty business scenario")
    requested = list(SCENARIOS) if scenario == "all" else [scenario]
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    observation = native_ipc_observation()
    report = {
        "schema_version": "rpnh/platform_business_acceptance/v1",
        "source_reference": source_reference, "transport": transport,
        "environment": {"system": platform.system(), "release": platform.release(),
                        "machine": platform.machine(), "python": platform.python_version(),
                        "python_implementation": platform.python_implementation()},
        "native_ipc": observation, "real_model_api_calls": 0,
        "provider": "deterministic fixture input bytes", "scenarios": {},
        "requested_scenarios": requested, "actual_scenarios": [],
        "native_frontends": "NOT_RUN", "managed_install_executor": "NOT_EXERCISED",
    }
    if transport == "native" and observation["status"] != "PASS":
        report["status"] = "BLOCKED_NATIVE_IPC"
    else:
        with transport_scope(transport, continuation_module=continuation_module,
                             pipe_owner_loop=pipe_owner_loop):
            for name, function in (("main_session_pause_handoff_resume", main_session_chain),
                                   ("parallel_checkpoint_continuation", lambda path: continuation_chain(
                                       path, continuation_module=continuation_module))):
                if name not in requested:
                    continue
                scenario_started = time.monotonic()
                try:
                    result = function(output / name)
                except Exception as error:
                    result = {"status": "FAILED", "error_type": type(error).__name__,
                              "message": str(error)}
                result["elapsed_seconds"] = round(time.monotonic() - scenario_started, 6)
                report["scenarios"][name] = result
                report["actual_scenarios"].append(name)
        passed = (report["actual_scenarios"] == requested
                  and all(result["status"] == "PASS" for result in report["scenarios"].values()))
        report["status"] = ("PASS_NATIVE_OFFLINE_BUSINESS" if transport == "native"
                            else "PASS_WITH_TRANSPORT_SUBSTITUTION") if passed else "FAILED"
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    (output / "RESULT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
