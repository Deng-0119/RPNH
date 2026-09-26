"""Frontend-neutral application boundary; Registry and existing task owners execute.

Transport code must not access Registry files, provider transports or workspaces.
This service deliberately reuses the pinned main-thread contract, including its
registered command identities; it does not create another event store or loop.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5


class FrontendError(RuntimeError):
    """A deliberately public error without provider/configuration details."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def stable_id(value: str) -> str:
    """Identity only, like MainThreadRegistry; not a content integrity check."""
    return uuid5(NAMESPACE_URL, "rpnh:frontend:" + value).hex


def request_identity(text: str, kind: str | None, model: Mapping[str, str],
                     request_id: str | None) -> str:
    if request_id is not None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", request_id):
            raise FrontendError("invalid_request_id", "Invalid request identity.", 400)
        return "explicit:" + request_id
    # The pinned stock TUI does not always supply messageID. Without an ID,
    # identical input means the SAME operation in a session, even after it ends.
    # Intentional repetition needs /rpnh-send FRESH_ID TEXT; never guess intent
    # from HTTP lifetime or timeout. Exact input is also checked on every retry.
    return "unkeyed:" + stable_id(canonical({"text": text, "kind": kind, "model": dict(model)}))


def _milliseconds(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


@dataclass
class _Session:
    session: Any
    main_control: Any
    failure: str | None = None
    stop_requested_for: str | None = None


class RegistryFrontendApplication:
    """One application owner over independent MainSession/TaskControl instances."""

    def __init__(self, root: Path, execution: Path, *, resume: bool = False) -> None:
        import fcntl
        from cpn.rpnh.main_session import MainSession
        from cpn.rpnh.task_control import TaskControl
        from cpn.rpnh.user_config import discover_profiles, profile_for_path
        self.root, self.execution = root.expanduser().resolve(), execution.expanduser().resolve()
        active_profile = profile_for_path(self.execution)
        installed = discover_profiles()
        self.profiles = (
            tuple(profile for profile in installed if profile.selectable)
            if any(profile.path == self.execution for profile in installed)
            else (active_profile,)
        )
        self._profiles_by_selection = {
            profile.selection_id: profile for profile in self.profiles}
        self._profiles_by_path = {
            profile.path: profile for profile in self.profiles}
        if len(self._profiles_by_selection) != len(self.profiles):
            raise FrontendError(
                "ambiguous_catalog", "RPNH profile selection identities are not unique.", 500)
        self.default_selection = active_profile.selection_id
        self._sessions: dict[str, _Session] = {}
        self._main_session_type, self._task_control_type = MainSession, TaskControl
        self._lock = None
        self._initial_identities = {
            profile.selection_id: self._execution_identity(profile.path)
            for profile in self.profiles
        }
        if resume:
            if not (self.root / "threads").is_dir():
                raise FrontendError("not_frontend_root", "Resume needs an existing OpenCode frontend root.")
        elif self.root.exists():
            raise FrontendError("root_exists", "A new frontend needs an absent root.")
        else:
            self.root.mkdir(parents=True, mode=0o700)
            (self.root / "threads").mkdir(mode=0o700)
        lock_path = self.root / ".frontend-owner.lock"
        if lock_path.is_symlink():
            raise FrontendError("owner_lock", "The owner lock must not be a symlink.")
        self._lock = lock_path.open("a+b")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if resume:
                paths = [p for p in sorted((self.root / "threads").iterdir())
                         if re.fullmatch(r"ses_[0-9a-f]{32}", p.name)]
                # Both constructors can compensate durable launches. Check ALL
                # saved execution identities before opening any runnable owner.
                for path in paths:
                    if path.is_symlink() or not path.is_dir():
                        raise FrontendError("invalid_session", "Invalid session directory.")
                    self._validate_saved_profile(path)
                for path in paths:
                    session = MainSession.resume(path)
                    self._sessions[path.name] = _Session(session, TaskControl(path / "main-turn-control"))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        # Exiting the UI does not signal independently owned workers.
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def configuration(self) -> dict[str, Any]:
        return {
            "default_selection": self.default_selection,
            "profiles": [self._public_profile(profile) for profile in self.profiles],
        }

    @staticmethod
    def _profile_ready(profile: Any) -> bool:
        return all(os.environ.get(name) for name in profile.required_environment)

    def _public_profile(self, profile: Any) -> dict[str, Any]:
        return {
            "selection": profile.selection_id,
            "provider": profile.provider,
            "provider_name": profile.provider_display_name,
            "model": profile.model_condition,
            "ready": self._profile_ready(profile),
        }

    def _profile(self, selection_id: str) -> Any:
        profile = self._profiles_by_selection.get(selection_id)
        if profile is None:
            raise FrontendError(
                "unknown_model", "Unknown RPNH provider/model selection.", 400)
        return profile

    def _execution_identity(self, path: Path) -> dict[str, Any]:
        from cpn.llm_adapters import load_llm_execution_selection
        from cpn.rpnh.user_config import profile_for_path
        selected = path.expanduser().resolve()
        selection, profile = load_llm_execution_selection(selected), profile_for_path(selected)
        return {"schema_version": "rpnh/main_session_profile/v2",
                "execution_config_path": str(selected),
                "adapter_config_path": str(selection.adapter_config_path),
                "selection_id": profile.selection_id, "provider": profile.provider,
                "model_condition": selection.input_target.model_condition,
                "adapter_kind": selection.adapter_kind, "registry_policy": selection.as_registry_policy()}

    def _validate_saved_profile(self, root: Path) -> Any:
        persisted = self._main_session_type._persisted_execution_profile(root)
        if persisted is None or persisted.get("schema_version") != "rpnh/main_session_profile/v2":
            raise FrontendError("missing_profile", "A current persisted RPNH profile is required.")
        raw_path = persisted.get("execution_config_path")
        if not isinstance(raw_path, (str, Path)):
            raise FrontendError("missing_profile", "A current persisted RPNH profile is required.")
        profile = self._profiles_by_path.get(Path(raw_path).expanduser().resolve())
        if profile is None:
            raise FrontendError("selection_unavailable", "The persisted RPNH profile is unavailable.")
        expected = dict(persisted)
        expected["execution_config_path"] = str(expected["execution_config_path"])
        initial = self._initial_identities[profile.selection_id]
        if expected != initial or self._execution_identity(profile.path) != initial:
            raise FrontendError("selection_drift", "RPNH profile drift was rejected before opening work.")
        return profile

    def _check_model(self, session: Any) -> None:
        profile = self._validate_saved_profile(session.root)
        if session.execution_config_path != profile.path:
            raise FrontendError("selection_drift", "RPNH execution selection changed.")

    def _select_profile(self, session: Any, selection_id: str) -> Any:
        profile = self._profile(selection_id)
        initial = self._initial_identities[selection_id]
        if self._execution_identity(profile.path) != initial:
            raise FrontendError("selection_drift", "RPNH profile drift was rejected before execution.")
        if not self._profile_ready(profile):
            raise FrontendError("profile_not_ready", "The selected RPNH profile is not ready.", 400)
        if session.execution_config_path != profile.path:
            if session.active_turn_snapshot() is not None:
                raise FrontendError(
                    "session_busy", "Cannot change provider/model during an active main turn.")
            session.set_execution_config(profile.path)
        return profile

    def _get(self, sid: str) -> _Session:
        if sid not in self._sessions:
            raise FrontendError("unknown_session", "Unknown RPNH session.", 404)
        return self._sessions[sid]

    def create_session(self, selection_id: str | None = None) -> str:
        profile = self._profile(selection_id or self.default_selection)
        initial = self._initial_identities[profile.selection_id]
        if self._execution_identity(profile.path) != initial:
            raise FrontendError("selection_drift", "RPNH profile drift was rejected before session creation.")
        if not self._profile_ready(profile):
            raise FrontendError("profile_not_ready", "The selected RPNH profile is not ready.", 400)
        sid = "ses_" + uuid4().hex
        path = self.root / "threads" / sid
        session = self._main_session_type(path, profile.path)
        self._sessions[sid] = _Session(session, self._task_control_type(path / "main-turn-control"))
        return sid

    def _turns(self, state: _Session) -> list[dict[str, Any]]:
        session = state.session
        authority = session._main_thread.recover_thread()
        thread = session._version_ref(authority["thread_ref"])
        events = {str(e.payload.get("version_id")): e
                  for e in session._registry_core.event_store.list_events()
                  if e.event_type == "object_version_published/v1"}
        committed = {int(item["ordinal"]): item for item in authority["committed_history"]}
        result = []
        for lineage in session._main_thread._turn_lineages(thread.entity_id):
            first_ref, first = lineage[0]
            ref, current = lineage[-1]
            event = events.get(str(first_ref.version_id))
            latest = events.get(str(ref.version_id))
            if event is None or latest is None:
                raise FrontendError("missing_evidence", "A turn publication is unavailable.")
            try:
                command = json.loads(event.command_id)
            except (TypeError, ValueError):
                command = {}
            if not isinstance(command, dict):
                command = {}
            key, identity, selection_id = command.get("caller_idempotency_key", ""), None, None
            if isinstance(key, str) and key.startswith("frontend-request/v2:"):
                try:
                    request_evidence = json.loads(
                        key.removeprefix("frontend-request/v2:"))
                except (TypeError, ValueError):
                    request_evidence = None
                if (not isinstance(request_evidence, dict)
                        or set(request_evidence) != {"key", "selection"}
                        or not isinstance(request_evidence["key"], str)
                        or not isinstance(request_evidence["selection"], str)):
                    raise FrontendError(
                        "invalid_evidence", "Submission identity evidence is invalid.")
                identity = request_evidence["key"]
                selection_id = request_evidence["selection"]
                self._profile(selection_id)
                material = command.get("material")
                if (command.get("authority") != "main_thread/v1" or command.get("command") != "accept_turn"
                        or not isinstance(material, dict) or material.get("user_input") != first["user_input"]):
                    raise FrontendError("invalid_evidence", "Submission evidence differs from its Registry turn.")
            else:
                raise FrontendError(
                    "missing_evidence", "A turn lacks current frontend submission evidence.")
            text, kind = session._turn_input(first["user_input"])
            ordinal = int(first["ordinal"])
            item = {"ordinal": ordinal, "key": identity, "text": text, "kind": kind,
                    "model": self._public_profile(self._profile(selection_id)),
                    "state": current["state"], "created": _milliseconds(event.recorded_at),
                    "updated": _milliseconds(latest.recorded_at),
                    "turn_ref": {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id),
                                 "version_id": str(ref.version_id)},
                    "attempt": current.get("attempt_relative_path"), "answer": None, "children": []}
            if ordinal in committed:
                item["answer"] = committed[ordinal]["answer"]
                item["children"] = [{"task_id": link["task_control_id"], "kind": link["task_kind"]}
                                    for link in authority.get("child_registry_links", ())
                                    if link.get("origin_main_turn_ref") == committed[ordinal]["turn_ref"]]
            result.append(item)
        # A child creates its SQLite file before its first complete authority
        # snapshot is necessarily readable.  Keep the main-thread projection
        # available during that bounded handoff instead of letting a transient
        # child read terminate the single frontend-owner thread.  The main turn
        # remains running and cannot admit another request; tick() will either
        # reconcile exact Registry evidence later or retain the visible error.
        from cpn.rpnh.registry.main_thread import MainThreadAuthorityError
        try:
            active = session.active_turn_snapshot()
        except MainThreadAuthorityError:
            state.failure = "reconciliation_required"
            active = None
        if active is not None:
            for item in result:
                if item["ordinal"] == active.ordinal:
                    item["state"] = active.state
        return result

    def snapshot(self) -> list[dict[str, Any]]:
        views = []
        for sid, state in self._sessions.items():
            turns = self._turns(state)
            events = tuple(state.session._registry_core.event_store.list_events())
            profile = self._profiles_by_path.get(state.session.execution_config_path)
            if profile is None:
                raise FrontendError(
                    "selection_unavailable", "The session's RPNH profile is unavailable.")
            views.append({"id": sid, "created": _milliseconds(events[0].recorded_at) if events else 0,
                          "updated": _milliseconds(events[-1].recorded_at) if events else 0,
                          "model": self._public_profile(profile), "turns": turns,
                          "error": state.failure})
        return views

    @staticmethod
    def _matching_handle(control: Any, path: Path | None) -> Any | None:
        if path is None:
            return None
        matches = [control.get(row["task_id"]) for row in control.list()
                   if Path(row["run_dir"]).resolve() == path.resolve()]
        if len(matches) > 1:
            raise FrontendError("ambiguous_owner", "Multiple task handles match one Registry attempt.")
        return matches[0] if matches else None

    def submit(
            self, sid: str, text: str, key: str, *, kind: str | None = None,
            selection_id: str | None = None,
    ) -> int:
        state = self._get(sid)
        session = state.session
        current = self._profiles_by_path.get(session.execution_config_path)
        if current is None:
            raise FrontendError(
                "selection_unavailable", "The session's RPNH profile is unavailable.")
        profile = self._profile(selection_id or current.selection_id)
        initial = self._initial_identities[profile.selection_id]
        if self._execution_identity(profile.path) != initial:
            raise FrontendError("selection_drift", "RPNH profile drift was rejected before execution.")
        if not isinstance(text, str) or not text.strip() or kind not in {None, "workflow"}:
            raise FrontendError("invalid_prompt", "A nonempty text prompt is required.", 400)
        if not isinstance(key, str) or not key or len(key) > 200:
            raise FrontendError("invalid_request_id", "Invalid request identity.", 400)
        existing = [item for item in self._turns(state) if item["key"] == key]
        if len(existing) > 1:
            raise FrontendError("ambiguous_submission", "Duplicate Registry submission identities.")
        if existing:
            item = existing[0]
            if ((item["text"], item["kind"], item["model"]["selection"])
                    != (text, kind, profile.selection_id)):
                raise FrontendError("request_conflict", "This request identity already has different input.")
            if item["state"] not in {"accepted", "pending_start"}:
                return item["ordinal"]
            ordinal = item["ordinal"]
        else:
            profile = self._select_profile(session, profile.selection_id)
            if session.active_turn_snapshot() is not None:
                raise FrontendError("session_busy", "Resolve the active or paused main turn first.")
            projection = session._main_thread.recover_thread()
            ordinal = int(projection["next_turn_ordinal"])
            session._main_thread.accept_turn(
                thread_ref=session._version_ref(projection["thread_ref"]),
                user_input={"text": text, "required_task_kind": kind}, expected_ordinal=ordinal,
                idempotency_key="frontend-request/v2:" + canonical({
                    "key": key, "selection": profile.selection_id}))
        spec = session.prepare_turn(text, required_task_kind=kind)
        snap = session.active_turn_snapshot()
        if snap is None or snap.ordinal != ordinal:
            raise FrontendError("missing_attempt", "The prepared turn is missing from Registry authority.")
        handle = self._matching_handle(state.main_control, snap.attempt_path)
        if handle is None:
            if spec.run_dir.exists() and any(spec.run_dir.iterdir()):
                raise FrontendError("unknown_outcome", "An unowned attempt exists; no automatic replay.")
            state.main_control.start(spec)
        state.failure = None
        return ordinal

    def tick(self) -> None:
        for state in self._sessions.values():
            try:
                snap = state.session.active_turn_snapshot()
                if snap is None:
                    continue
                handle = self._matching_handle(state.main_control, snap.attempt_path)
                if snap.state == "terminal":
                    self._check_model(state.session)
                    state.session.reconcile_active_turn()
                    state.failure = None
                elif snap.state == "running" and handle is not None and handle.process.poll() is not None:
                    if state.session.reconcile_active_turn().state == "running":
                        state.session.fail_active_turn()
                elif snap.state in {"accepted", "pending_start", "running"} and (
                        handle is None or handle.process.poll() is not None):
                    state.failure = "reconciliation_required"
            except Exception:
                state.failure = "reconciliation_required"  # Never provider exception text.

    def abort(self, sid: str) -> bool:
        state = self._get(sid)
        snap = state.session.active_turn_snapshot()
        if snap is None or snap.state in {"terminal", "stopped_by_owner"}:
            return True
        handle = self._matching_handle(state.main_control, snap.attempt_path)
        if handle is None:
            raise FrontendError("owner_unavailable", "No exact main-turn owner is available; no process was signalled.")
        # Coalesce retransmissions during this owner lifetime, but do not claim
        # checkpoint completion until Registry reports stopped_by_owner.
        if state.stop_requested_for == handle.task_id:
            return True
        reply = state.main_control.stop(handle.task_id, startup_safe=True)
        accepted = reply.get("status") in {
            "STOP_REQUESTED", "STARTUP_STOP_REQUESTED", "ALREADY_EXITED"}
        if not accepted:
            raise FrontendError(
                "owner_not_ready",
                "The exact main-turn owner is not ready to checkpoint the interruption.",
                503,
            )
        state.stop_requested_for = handle.task_id
        return True

    def _net(self, state: _Session, task_id: str | None, flags: tuple[str, ...]) -> dict[str, Any]:
        from cpn.cli import _filter_projection
        from cpn.rpnh.agent_tasks import agent_task_catalog
        from cpn.rpnh.inspection import project_registry_net
        if len(set(flags)) != len(flags) or set(flags) - {"--show-resources", "--resources-only"}:
            raise FrontendError("invalid_net_options", "Only --show-resources and --resources-only are supported.", 400)
        if task_id is None:
            attempted = [t for t in self._turns(state) if t["attempt"]]
            if not attempted:
                raise FrontendError("net_unavailable", "No registered main-turn run exists yet.")
            run_dir = (state.session.root / attempted[-1]["attempt"]).resolve()
            if not run_dir.is_relative_to(state.session.root):
                raise FrontendError("invalid_attempt", "The attempt escapes its session.")
        else:
            run_dir = state.session.task_control.get(task_id).run_dir
        return _filter_projection(project_registry_net(run_dir, catalog=agent_task_catalog()),
                                  show_resources="--show-resources" in flags,
                                  resources_only="--resources-only" in flags, node_id=None)

    def launch_agent(self, sid: str, text: str, key: str) -> dict[str, str]:
        from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
        state = self._get(sid)
        session = state.session
        self._check_model(session)
        if not text.strip():
            raise FrontendError("invalid_prompt", "An agent task needs text.", 400)
        run_dir = session.root / "tasks" / "runs" / ("frontend-agent-" + stable_id(key))
        handle = self._matching_handle(session.task_control, run_dir)
        if handle is not None:
            if handle.spec.prompt != text or handle.kind != "single_agent":
                raise FrontendError("request_conflict", "This agent request identity has different input.")
        else:
            links = session._main_thread._ordered_documents(session._registry_core, "main_child_registry_link/v1")
            relative = run_dir.relative_to(session.root).as_posix()
            if run_dir.exists() or any(doc.get("registry_relative_path") == relative for _, doc in links):
                raise FrontendError("unknown_outcome", "An agent Registry lacks its launch handle; no replay.")
            from cpn.llm_adapters import load_llm_execution_selection
            runtime = load_llm_execution_selection(
                session.execution_config_path).runtime_policy
            handle = session.task_control.start(AgentTaskSpec(
                run_dir=run_dir, prompt=text,
                stages=(AgentStage("worker", "Complete the requested task and return its result."),),
                execution_config_path=session.execution_config_path,
                max_attempts_per_stage=runtime.max_turns_per_node,
                max_parallel_nodes=runtime.max_parallel_nodes,
                owner_statement="RPNH application authorized independent agent"))
        session._index_child_registry(handle, run_dir=run_dir, origin_main_turn_ref=None)
        return {"task_id": handle.task_id, "kind": handle.kind, "status": "launch_registered_not_terminal"}

    def command(
            self, sid: str, name: str, arguments: str, key: str, *,
            selection_id: str | None = None,
    ) -> Any:
        import shlex
        state = self._get(sid)
        session = state.session
        current = self._profiles_by_path.get(session.execution_config_path)
        if current is None:
            raise FrontendError(
                "selection_unavailable", "The session's RPNH profile is unavailable.")
        selected = self._profile(selection_id or current.selection_id)
        if name == "rpnh-help":
            if arguments.strip():
                raise FrontendError("invalid_arguments", "rpnh-help takes no arguments.", 400)
            return {"commands": ["rpnh-tasks", "rpnh-task ID status|result|stop|resume|net|message",
                                 "rpnh-net [--show-resources|--resources-only]", "rpnh-agent TEXT",
                                 "rpnh-workflow TEXT", "rpnh-resume", "rpnh-rollback", "rpnh-send UNIQUE_ID TEXT"],
                    "notice": "Identical unkeyed prompts are one request per session. Command observations are not agent answers."}
        if name in {"rpnh-workflow", "rpnh-send"}:
            selected = self._select_profile(session, selected.selection_id)
            text = arguments
            kind = "workflow" if name == "rpnh-workflow" else None
            if name == "rpnh-send":
                parts = arguments.split(maxsplit=1)
                if len(parts) != 2:
                    raise FrontendError("invalid_arguments", "Usage: /rpnh-send UNIQUE_ID TEXT", 400)
                key = request_identity(
                    parts[1], None,
                    {"provider": "rpnh", "selection": selected.selection_id},
                    parts[0])
                text = parts[1]
            return {"turn_ordinal": self.submit(
                sid, text, key, kind=kind,
                selection_id=selected.selection_id),
                "status": "registered_not_terminal"}
        if name == "rpnh-agent":
            self._select_profile(session, selected.selection_id)
            return self.launch_agent(sid, arguments, key)
        try:
            words = shlex.split(arguments)
        except ValueError:
            raise FrontendError("invalid_arguments", "Invalid quoted arguments.", 400) from None
        if name == "rpnh-tasks" and not words:
            session.reconcile_child_registry_links()
            return [self._status(row) for row in session.task_control.list()]
        if name == "rpnh-net":
            return self._net(state, None, tuple(words))
        if name == "rpnh-task" and len(words) >= 2:
            task_id, action, *flags = words
            session.task_control.get(task_id)
            if action == "net":
                return self._net(state, task_id, tuple(flags))
            if action == "message":
                fields = arguments.split(maxsplit=2)
                if len(fields) != 3 or not fields[2].strip():
                    raise FrontendError(
                        "invalid_arguments", "Usage: /rpnh-task ID message TEXT", 400)
                payload = fields[2]
                if " :: " in payload:
                    target, body = payload.split(" :: ", 1)
                    return session.task_control.message(
                        task_id, body, target=target.strip())
                return session.task_control.message(task_id, payload)
            if flags or action not in {"status", "result", "stop", "resume"}:
                raise FrontendError("unsupported_command", "Unsupported task action.", 400)
            if action == "status":
                return self._status(session.task_control.status(task_id))
            if action == "resume":
                self._check_model(session)
            return getattr(session.task_control, action)(task_id)
        if name in {"rpnh-resume", "rpnh-rollback"} and not words:
            snap = session.active_turn_snapshot()
            if snap is None or snap.state != "stopped_by_owner":
                raise FrontendError("not_paused", "The main turn is not paused at a Registry checkpoint.")
            if name == "rpnh-rollback":
                session.rollback_paused_turn()
                return {"status": "main_interrupted_child_registry_retained"}
            self._select_profile(session, selected.selection_id)
            self._check_model(session)
            handle = self._matching_handle(state.main_control, snap.attempt_path)
            if handle is None:
                raise FrontendError("owner_unavailable", "The paused main turn has no exact launch handle.")
            result = state.main_control.resume(handle.task_id)
            state.stop_requested_for = None
            return result
        raise FrontendError("unsupported_command", "Unsupported RPNH command or arguments.", 400)

    def shutdown(self, *, timeout: float = 30.0) -> None:
        """Checkpoint-stop active main turns while leaving child tasks alone."""
        deadline = time.monotonic() + timeout
        pending: list[_Session] = []
        for state in self._sessions.values():
            snap = state.session.active_turn_snapshot()
            if snap is None:
                continue
            if snap.state == "terminal":
                state.session.reconcile_active_turn()
                continue
            if snap.state == "stopped_by_owner":
                continue
            handle = self._matching_handle(state.main_control, snap.attempt_path)
            if handle is None:
                state.failure = "reconciliation_required"
                pending.append(state)
                continue
            reply = state.main_control.stop(handle.task_id, startup_safe=True)
            if reply.get("status") not in {
                    "STOP_REQUESTED", "STARTUP_STOP_REQUESTED", "ALREADY_EXITED"}:
                state.failure = "reconciliation_required"
            pending.append(state)

        while pending and time.monotonic() < deadline:
            self.tick()
            remaining = []
            for state in pending:
                snap = state.session.active_turn_snapshot()
                if snap is None or snap.state == "stopped_by_owner":
                    continue
                remaining.append(state)
            pending = remaining
            if pending:
                time.sleep(0.05)
        if pending:
            raise FrontendError(
                "checkpoint_timeout",
                "Active main turns did not reach a Registry checkpoint before frontend shutdown.",
                500,
            )

    @staticmethod
    def _status(row: Mapping[str, Any]) -> dict[str, Any]:
        result = {k: row[k] for k in ("task_id", "kind", "process_status", "return_code") if k in row}
        if isinstance(row.get("registry"), Mapping):
            allowed = {"run_ref", "task_ref", "net_ref", "checkpoint_ref", "execution_status",
                       "enabled_transitions", "active_firings", "terminal_evidence_count",
                       "final_result_index_count", "actual_model_call_counts"}
            result["registry"] = {k: v for k, v in row["registry"].items() if k in allowed}
        return result


class FrontendGateway:
    """Bounded single-thread application owner; protocols never own a writer."""

    METHODS = frozenset({"configuration", "snapshot", "create_session", "submit", "abort", "command"})

    def __init__(self, factory: Callable[[], Any], *, on_change: Callable[[], None] | None = None,
                 tick_interval: float = 0.2) -> None:
        self._factory, self._on_change = factory, on_change or (lambda: None)
        self._interval = tick_interval
        self._requests: queue.Queue[Any] = queue.Queue(maxsize=128)
        self._ready: Future[None] = Future()
        self._closing = threading.Event()
        self._shutdown_error: BaseException | None = None
        self._last = ""
        self._thread = threading.Thread(target=self._run, name="rpnh-frontend-owner", daemon=True)
        self._thread.start()
        try:
            self._ready.result(timeout=30)
        except BaseException:
            self._closing.set()
            raise

    def _changed(self, app: Any) -> None:
        value = canonical(app.snapshot())
        if value != self._last:
            self._last = value
            self._on_change()

    def _run(self) -> None:
        app = None
        try:
            app = self._factory()
            self._changed(app)
            self._ready.set_result(None)
            while not self._closing.is_set():
                try:
                    item = self._requests.get(timeout=self._interval)
                except queue.Empty:
                    app.tick()
                    self._changed(app)
                    continue
                if item is None:
                    break
                name, args, kwargs, result = item
                if not result.set_running_or_notify_cancel():
                    continue
                try:
                    value = getattr(app, name)(*args, **kwargs)
                    # Reads only project state. Reconciliation is an application
                    # owner activity, not a side effect of a UI GET.
                    if name not in {"configuration", "snapshot"}:
                        app.tick()
                    self._changed(app)
                except FrontendError as exc:
                    result.set_exception(exc)
                except Exception:
                    result.set_exception(FrontendError("application_error",
                        "RPNH rejected the operation; inspect its private Registry/owner state.", 500))
                else:
                    result.set_result(value)
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            self._closing.set()
            if app is not None:
                try:
                    shutdown = getattr(app, "shutdown", None)
                    if shutdown is not None:
                        shutdown()
                except BaseException as exc:
                    self._shutdown_error = exc
                finally:
                    app.close()
            while True:
                try:
                    pending = self._requests.get_nowait()
                except queue.Empty:
                    break
                if pending is not None and not pending[3].done():
                    pending[3].set_exception(FrontendError("owner_stopped", "The frontend owner stopped.", 503))

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self.METHODS:
            raise FrontendError("unsupported_operation", "Unsupported application operation.", 400)
        if self._closing.is_set() or not self._thread.is_alive():
            raise FrontendError("owner_stopped", "The frontend owner is unavailable.", 503)
        result: Future[Any] = Future()
        try:
            self._requests.put_nowait((name, args, kwargs, result))
        except queue.Full:
            raise FrontendError("owner_busy", "The application queue is full.", 503) from None
        try:
            return result.result(timeout=30)
        except FutureTimeout:
            result.cancel()
            raise FrontendError("outcome_pending", "Outcome pending. Retry only with the same identity.", 504) from None

    def close(self) -> None:
        self._closing.set()
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=35)
        if self._thread.is_alive():
            raise RuntimeError("RPNH frontend owner did not stop within the shutdown bound")
        if self._shutdown_error is not None:
            raise RuntimeError(str(self._shutdown_error)) from self._shutdown_error
