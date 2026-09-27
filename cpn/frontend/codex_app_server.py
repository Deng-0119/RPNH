"""Codex CLI 0.155.0 TUI compatibility surface backed only by RPNH.

The stock Codex TUI is a remote JSON-RPC client.  This module implements the
small current protocol slice required by that client over WebSocket frames on
a Unix-domain socket.  It never starts a Codex app-server or dispatches a
model outside the selected RPNH execution path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.main_session import (
    MainDecision,
    MainSession,
    MainTurnReconciliation,
    MainTurnSnapshot,
    render_main_decision,
)
from cpn.rpnh.task_control import TaskControl, TaskHandle
from cpn.rpnh.unix_transport import unix_socket_address
from cpn.rpnh.user_config import (
    ExecutionProfile,
    config_path,
    discover_profiles,
    profile_for_path,
    save_selected_path,
    select_profile,
)


CODEX_FRONTEND_VERSION = "0.155.0"
CODEX_CLI_VERSION_TEXT = f"codex-cli {CODEX_FRONTEND_VERSION}"
CODEX_DISABLED_FEATURES = ("goals", "personality", "plugins")
CODEX_CONFIG_OVERRIDES = ("check_for_update_on_startup=false",)


class CodexCompatibilityError(RuntimeError):
    """The pinned frontend or protocol contract cannot be satisfied."""


@dataclass(slots=True)
class ActiveTurn:
    turn_id: str
    item_id: str
    ordinal: int
    user_text: str
    required_task_kind: str | None
    started_at: int
    task_handle: TaskHandle | None
    tracking: bool = False


@dataclass(slots=True)
class ThreadState:
    thread_id: str
    session: MainSession
    main_turn_control: TaskControl
    model_id: str
    cwd: Path
    created_at: int
    preview: str = ""
    name: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    # Preserve old UI sidecar metadata on resume; never advertise it as input.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    active: ActiveTurn | None = None


def _now_seconds() -> int:
    return int(time.time())


def _now_milliseconds() -> int:
    return int(time.time() * 1000)


async def _wait_process(process: Any) -> int:
    """Wait without occupying the event loop's shutdown-blocking thread pool."""
    while True:
        return_code = process.poll()
        if return_code is not None:
            return int(return_code)
        await asyncio.sleep(0.1)


def _turn_document(
        turn_id: str, status: str, *, items: list[dict[str, Any]] | None = None,
        started_at: int | None = None, error: str | None = None,
) -> dict[str, Any]:
    started = _now_seconds() if started_at is None else started_at
    completed = _now_seconds() if status != "inProgress" else None
    return {
        "id": turn_id,
        "items": [] if items is None else items,
        "status": status,
        "startedAt": started,
        "completedAt": completed,
        "durationMs": (
            None if completed is None else max(0, completed - started) * 1000),
        "error": None if error is None else {"message": error},
    }


def _ui_id(thread_id: str, ordinal: int, kind: str) -> str:
    return str(uuid5(
        NAMESPACE_URL, f"rpnh:codex:{thread_id}:{ordinal}:{kind}"))


def _text_input(params: Mapping[str, Any]) -> str:
    items = params.get("input")
    if not isinstance(items, list) or not items:
        raise ValueError("turn/start requires nonempty input")
    text: list[str] = []
    for item in items:
        if (not isinstance(item, Mapping)
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)):
            raise ValueError(
                "RPNH Codex frontend currently accepts text input only")
        text.append(str(item["text"]))
    result = "\n".join(text).strip()
    if not result:
        raise ValueError("turn/start text input must be nonempty")
    return result


class CodexAppServer:
    """One local Codex-TUI protocol server with RPNH-owned state."""

    def __init__(
            self, root: Path, execution_config_path: Path, *,
            profile_name: str | None = None,
    ) -> None:
        self.root = root.resolve()
        self.execution_config_path = execution_config_path.resolve()
        selection = load_llm_execution_selection(self.execution_config_path)
        provenance = selection.as_registry_policy()
        self.model_condition = selection.input_target.model_condition
        active_profile = profile_for_path(self.execution_config_path)
        self.profile_name = profile_name or active_profile.name
        self.frontend_model_id = active_profile.selection_id
        installed_profiles = discover_profiles()
        if any(
                profile.path == self.execution_config_path
                for profile in installed_profiles):
            self.model_profiles = tuple(
                profile for profile in installed_profiles
                if profile.selectable)
        else:
            self.model_profiles = (active_profile,)
        self._profiles_by_model_id = {
            profile.selection_id: profile for profile in self.model_profiles
        }
        self._profiles_by_path = {
            profile.path: profile for profile in self.model_profiles
        }
        self.default_model_id = self.frontend_model_id
        routes = provenance.get("route_provenance")
        self.provider_route = (
            str(routes[0].get("provider"))
            if isinstance(routes, list) and routes else selection.adapter_kind)
        self._threads: dict[str, ThreadState] = {}
        self._thread_load_failures: list[str] = []
        self._initialize_accepted_connections: set[int] = set()
        self._initialized_connections: set[int] = set()
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._load_threads()

    @staticmethod
    def _thread_state_path(root: Path) -> Path:
        return root / "thread_state.json"

    def _persist_thread(self, state: ThreadState) -> None:
        path = self._thread_state_path(state.session.root)
        value = {
            "schema_version": "rpnh/codex_thread_state/v1",
            "thread_id": state.thread_id,
            "model_id": state.model_id,
            "cwd": str(state.cwd),
            "created_at": state.created_at,
            "preview": state.preview,
            "name": state.name,
            "turns": state.turns,
            "attachments": state.attachments,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _matching_projection_ids(
            value: object, *, user_text: str, assistant_text: str,
    ) -> tuple[str, str, str] | None:
        if (not isinstance(value, Mapping)
                or value.get("status") != "completed"
                or not isinstance(value.get("id"), str)
                or not isinstance(value.get("items"), list)):
            return None
        user_items = [
            item for item in value["items"]
            if isinstance(item, Mapping) and item.get("type") == "userMessage"
        ]
        assistant_items = [
            item for item in value["items"]
            if isinstance(item, Mapping) and item.get("type") == "agentMessage"
        ]
        if len(user_items) != 1 or len(assistant_items) != 1:
            return None
        user_item = user_items[0]
        assistant_item = assistant_items[0]
        content = user_item.get("content")
        if (not isinstance(user_item.get("id"), str)
                or not isinstance(assistant_item.get("id"), str)
                or not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], Mapping)
                or content[0].get("type") != "text"
                or content[0].get("text") != user_text
                or assistant_item.get("text") != assistant_text):
            return None
        return value["id"], user_item["id"], assistant_item["id"]

    @staticmethod
    def _committed_turns(
            state: ThreadState, *, latest_active: ActiveTurn | None = None,
            previous_turns: list[object] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the Codex transcript only from MainSession Registry history."""
        history = list(getattr(
            state.session, "display_history", state.session.history))
        if len(history) % 2:
            raise RuntimeError("main-session Registry history is incomplete")
        turns: list[dict[str, Any]] = []
        for index in range(0, len(history), 2):
            ordinal = index // 2 + 1
            is_latest = (
                latest_active is not None and index == len(history) - 2)
            user_role, user_text = history[index]
            assistant_role, assistant_text = history[index + 1]
            if (user_role != "user" or assistant_role != "assistant"
                    or not isinstance(user_text, str)
                    or not isinstance(assistant_text, str)):
                raise RuntimeError(
                    "main-session Registry history has invalid roles")
            previous_ids = (
                CodexAppServer._matching_projection_ids(
                    previous_turns[ordinal - 1],
                    user_text=user_text,
                    assistant_text=assistant_text,
                )
                if previous_turns is not None
                and ordinal <= len(previous_turns) else None)
            projected_turn_id, projected_user_id, projected_assistant_id = (
                previous_ids or (
                    _ui_id(state.thread_id, ordinal, "turn"),
                    _ui_id(state.thread_id, ordinal, "user"),
                    _ui_id(state.thread_id, ordinal, "assistant"),
                ))
            user_item = {
                "type": "userMessage",
                "id": (
                    _ui_id(
                        state.thread_id, latest_active.ordinal, "active-user")
                    if is_latest else
                    projected_user_id),
                "content": [{"type": "text", "text": user_text}],
            }
            assistant_item = {
                "type": "agentMessage",
                "id": (
                    latest_active.item_id if is_latest
                    else projected_assistant_id),
                "text": assistant_text,
            }
            turns.append(_turn_document(
                (latest_active.turn_id if is_latest
                 else projected_turn_id),
                "completed",
                items=[user_item, assistant_item],
                started_at=state.created_at,
            ))
        return turns

    @staticmethod
    def _live_main_turn_handle(
            control: TaskControl, attempt_path: Path | None,
    ) -> TaskHandle | None:
        if attempt_path is None:
            return None
        matches: list[TaskHandle] = []
        for item in control.list():
            if Path(item["run_dir"]).resolve() != attempt_path:
                continue
            handle = control.get(item["task_id"])
            if handle.process.poll() is None:
                matches.append(handle)
        if len(matches) > 1:
            raise RuntimeError(
                "multiple live foreground handles match the active main turn")
        return matches[0] if matches else None

    def _active_from_snapshot(
            self, state: ThreadState, snapshot: MainTurnSnapshot,
    ) -> ActiveTurn:
        return ActiveTurn(
            turn_id=_ui_id(state.thread_id, snapshot.ordinal, "active-turn"),
            item_id=_ui_id(state.thread_id, snapshot.ordinal, "active-agent"),
            ordinal=snapshot.ordinal,
            user_text=snapshot.user_text,
            required_task_kind=snapshot.required_task_kind,
            started_at=state.created_at,
            task_handle=self._live_main_turn_handle(
                state.main_turn_control, snapshot.attempt_path),
        )

    def _load_threads(self) -> None:
        threads = self.root / "threads"
        if not threads.is_dir():
            return
        for thread_root in sorted(path for path in threads.iterdir()
                                  if path.is_dir()):
            path = self._thread_state_path(thread_root)
            failure_code = "persisted_profile_unavailable"
            try:
                persisted_profile = (
                    MainSession._persisted_execution_config_path(thread_root))
                if persisted_profile is None:
                    raise ValueError(
                        "persisted thread profile projection is unavailable")
                profile = self._profiles_by_path.get(persisted_profile)
                if profile is None:
                    raise ValueError("persisted thread model is unavailable")
                failure_code = "session_unavailable"
                session = MainSession.resume(thread_root)
                value: Mapping[str, Any] = {}
                if path.is_file():
                    try:
                        candidate = json.loads(path.read_text(encoding="utf-8"))
                        if (isinstance(candidate, Mapping)
                                and candidate.get("schema_version")
                                == "rpnh/codex_thread_state/v1"
                                and candidate.get("thread_id")
                                == thread_root.name):
                            value = candidate
                    except (OSError, TypeError, ValueError,
                            json.JSONDecodeError):
                        pass
                failure_code = "thread_projection_invalid"
                raw_cwd = value.get("cwd")
                cwd = (
                    Path(raw_cwd).resolve()
                    if isinstance(raw_cwd, str) and raw_cwd
                    else Path.cwd().resolve())
                raw_created_at = value.get("created_at")
                created_at = (
                    raw_created_at
                    if isinstance(raw_created_at, int)
                    and not isinstance(raw_created_at, bool)
                    else int(thread_root.stat().st_mtime))
                raw_preview = value.get("preview")
                preview = raw_preview if isinstance(raw_preview, str) else ""
                raw_name = value.get("name")
                name = raw_name if isinstance(raw_name, str) else None
                raw_attachments = value.get("attachments", [])
                try:
                    attachments = self._attachments(
                        raw_attachments
                        if isinstance(raw_attachments, list) else [])
                except ValueError:
                    attachments = []
                state = ThreadState(
                    thread_id=thread_root.name,
                    session=session,
                    main_turn_control=TaskControl(
                        session.child_path_root
                        / "main-turn-control"),
                    model_id=profile.selection_id,
                    cwd=cwd,
                    created_at=created_at,
                    preview=preview,
                    name=name,
                    attachments=attachments,
                )
                failure_code = "thread_reconciliation_failed"
                reconciliation = session.reconcile_active_turn()
                raw_turns = value.get("turns")
                state.turns = self._committed_turns(
                    state,
                    previous_turns=(
                        raw_turns if isinstance(raw_turns, list) else None),
                )
                if reconciliation.state in {
                        "accepted", "pending_start", "running"}:
                    if reconciliation.snapshot is None:
                        raise RuntimeError(
                            "active main-turn reconciliation lacks a snapshot")
                    state.active = self._active_from_snapshot(
                        state, reconciliation.snapshot)
                elif reconciliation.state not in {
                        "idle", "committed", "interrupted", "paused"}:
                    raise RuntimeError(
                        "unsupported main-turn reconciliation state: "
                        f"{reconciliation.state}")
                if not state.preview and state.session.history:
                    state.preview = state.session.history[0][1][:200]
                self._threads[state.thread_id] = state
                try:
                    self._persist_thread(state)
                except OSError:
                    pass
            except (KeyError, OSError, RuntimeError, TypeError, ValueError,
                    json.JSONDecodeError):
                self._thread_load_failures.append(failure_code)

    @property
    def thread_load_failures(self) -> tuple[str, ...]:
        """Redacted reason codes for persisted threads omitted at startup."""
        return tuple(self._thread_load_failures)

    def thread_load_diagnostic(self) -> str | None:
        if not self._thread_load_failures:
            return None
        counts = {
            code: self._thread_load_failures.count(code)
            for code in set(self._thread_load_failures)
        }
        details = ", ".join(
            f"{code}={counts[code]}" for code in sorted(counts))
        return (
            f"{len(self._thread_load_failures)} persisted thread(s) "
            f"were not loaded ({details})")

    @staticmethod
    def _attachments(value: list[object]) -> list[dict[str, Any]]:
        """Validate the persisted, generic app-server attachment records."""
        attachments: list[dict[str, Any]] = []
        identities: set[tuple[str, str]] = set()
        for attachment in value:
            if not isinstance(attachment, Mapping):
                raise ValueError("persisted thread attachment is invalid")
            attachment_id = attachment.get("id")
            attachment_type = attachment.get("attachmentType")
            identity_key = attachment.get("identityKey")
            created_at = attachment.get("createdAt")
            if (not isinstance(attachment_id, str) or not attachment_id
                    or not isinstance(attachment_type, str)
                    or not attachment_type
                    or not isinstance(identity_key, str) or not identity_key
                    or not isinstance(created_at, int)
                    or isinstance(created_at, bool)
                    or (attachment_type, identity_key) in identities
                    or "payload" not in attachment):
                raise ValueError("persisted thread attachment is invalid")
            try:
                json.dumps(attachment["payload"], allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "persisted thread attachment payload is invalid") from exc
            identities.add((attachment_type, identity_key))
            attachments.append({
                "id": attachment_id,
                "attachmentType": attachment_type,
                "identityKey": identity_key,
                "payload": attachment["payload"],
                "createdAt": created_at,
            })
        return attachments

    def _model(self, profile: ExecutionProfile) -> dict[str, Any]:
        public = profile.as_public_dict()
        readiness = "ready" if public["ready"] else "credentials required"
        return {
            "id": profile.selection_id,
            "model": profile.selection_id,
            "displayName": (
                f"{profile.model_condition} ({profile.provider_display_name})"),
            "description": (
                f"RPNH provider {profile.provider}; profile {profile.name}; "
                f"{readiness}"),
            "hidden": False,
            "isDefault": profile.selection_id == self.default_model_id,
            "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [{
                "reasoningEffort": "medium",
                "description": "Execution behavior is owned by the RPNH profile.",
            }],
            "inputModalities": ["text"],
            "supportsPersonality": False,
            "additionalSpeedTiers": [],
            "serviceTiers": [],
        }

    def _thread_document(self, state: ThreadState) -> dict[str, Any]:
        now = _now_seconds()
        status = (
            {"type": "active", "activeFlags": []}
            if state.active is not None else {"type": "idle"})
        return {
            "id": state.thread_id,
            "sessionId": state.thread_id,
            "preview": state.preview,
            "name": state.name,
            "ephemeral": False,
            "model": state.model_id,
            "modelProvider": "rpnh",
            "createdAt": state.created_at,
            "updatedAt": now,
            "recencyAt": now,
            "cwd": str(state.cwd),
            "cliVersion": CODEX_FRONTEND_VERSION,
            "source": "appServer",
            "status": status,
            "turns": list(state.turns),
            "projectId": None,
            "historyMode": "paginated",
        }

    async def _send(self, websocket: Any, value: Mapping[str, Any]) -> None:
        key = id(websocket)
        lock = self._send_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await websocket.send(json.dumps(
                value, ensure_ascii=False, separators=(",", ":")))

    async def _result(self, websocket: Any, request_id: object, value: object) -> None:
        await self._send(websocket, {"id": request_id, "result": value})

    async def _error(
            self, websocket: Any, request_id: object, message: str, *,
            code: int = -32602,
    ) -> None:
        await self._send(websocket, {
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    async def _notify(
            self, websocket: Any, method: str, params: Mapping[str, Any],
    ) -> None:
        await self._send(websocket, {"method": method, "params": params})

    def _thread(self, thread_id: object) -> ThreadState:
        if not isinstance(thread_id, str) or thread_id not in self._threads:
            raise ValueError("unknown RPNH thread")
        return self._threads[thread_id]

    @staticmethod
    def _require_ready_profile(profile: ExecutionProfile) -> None:
        if not all(os.environ.get(name) for name in profile.required_environment):
            raise ValueError("The selected RPNH profile is not ready.")

    def _listed_threads(
            self, params: Mapping[str, Any],
    ) -> list[ThreadState]:
        """Project only real RPNH thread relationships into Codex lists."""
        parent = params.get("parentThreadId")
        ancestor = params.get("ancestorThreadId")
        if parent is not None and ancestor is not None:
            raise ValueError(
                "thread/list parentThreadId and ancestorThreadId are exclusive")
        # RPNH currently has no Codex ThreadSpawn descendants.  In particular,
        # independent /agent task objects have their own Registry and PetriNet;
        # they must never be projected as parent-owned Codex subagents.
        if parent is not None or ancestor is not None:
            return []
        source_kinds = params.get("sourceKinds")
        if (isinstance(source_kinds, list) and source_kinds
                and "subAgentThreadSpawn" in source_kinds
                and "appServer" not in source_kinds):
            return []
        return list(self._threads.values())

    async def _start_thread(
            self, websocket: Any, request_id: object,
            params: Mapping[str, Any],
    ) -> None:
        requested_model = params.get("model")
        model_id = (
            self.default_model_id
            if requested_model is None else requested_model)
        if not isinstance(model_id, str):
            raise ValueError("RPNH thread model must be a selection ID")
        profile = self._profiles_by_model_id.get(model_id)
        if profile is None:
            raise ValueError(
                f"unknown RPNH provider/model selection: {model_id}")
        self._require_ready_profile(profile)
        raw_cwd = params.get("cwd")
        cwd = (
            Path(raw_cwd).expanduser().resolve()
            if raw_cwd else Path.cwd().resolve())
        thread_id = str(uuid4())
        thread_root = self.root / "threads" / thread_id
        session = MainSession(
            thread_root,
            profile.path,
        )
        state = ThreadState(
            thread_id=thread_id,
            session=session,
            main_turn_control=TaskControl(
                session.child_path_root / "main-turn-control"),
            model_id=profile.selection_id,
            cwd=cwd,
            created_at=_now_seconds(),
        )
        self._threads[thread_id] = state
        self._persist_thread(state)
        document = self._thread_document(state)
        await self._result(websocket, request_id, {
            "thread": document,
            "model": state.model_id,
            "modelProvider": "rpnh",
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandbox": {"type": "dangerFullAccess"},
            "reasoningEffort": "medium",
            "serviceTier": None,
            "instructionSources": [],
        })
        await self._notify(websocket, "thread/started", {"thread": document})

    async def _start_turn(
            self, websocket: Any, request_id: object,
            params: Mapping[str, Any],
    ) -> None:
        state = self._thread(params.get("threadId"))
        if state.active is not None:
            raise ValueError("RPNH thread already has an active turn")
        reconciliation = state.session.reconcile_active_turn()
        if reconciliation.state == "paused":
            raise ValueError(
                "RPNH main turn is paused at its Registry checkpoint; "
                "reopen this session with --frontend basic and use /resume "
                "or /rollback")
        user_text = _text_input(params)
        requested_model = params.get("model")
        if requested_model is not None:
            if not isinstance(requested_model, str):
                raise ValueError("RPNH turn model must be a selection ID")
            profile = self._profiles_by_model_id.get(requested_model)
            if profile is None:
                raise ValueError(
                    f"unknown RPNH provider/model selection: {requested_model}")
        else:
            profile = self._profiles_by_model_id.get(state.model_id)
            if profile is None:
                raise ValueError(
                    f"unknown RPNH provider/model selection: {state.model_id}")
        self._require_ready_profile(profile)
        if requested_model is not None:
            state.session.set_execution_config(profile.path)
            state.model_id = profile.selection_id
        spec = state.session.prepare_turn(user_text)
        snapshot = state.session.active_turn_snapshot()
        if snapshot is None:
            raise RuntimeError(
                "prepared main turn is absent from Registry authority")
        active = ActiveTurn(
            turn_id=_ui_id(
                state.thread_id, snapshot.ordinal, "active-turn"),
            item_id=_ui_id(
                state.thread_id, snapshot.ordinal, "active-agent"),
            ordinal=snapshot.ordinal,
            user_text=snapshot.user_text,
            required_task_kind=snapshot.required_task_kind,
            started_at=_now_seconds(),
            task_handle=None,
        )
        state.active = active
        if not state.preview:
            state.preview = user_text[:200]
        self._persist_thread(state)
        try:
            active.task_handle = state.main_turn_control.start(spec)
        except Exception:
            # prepare_turn has already made this Registry turn authoritative.
            # Keep it visible and discover a handle if start failed only after
            # persisting/spawning the foreground task.
            active.task_handle = self._live_main_turn_handle(
                state.main_turn_control, snapshot.attempt_path)
            self._persist_thread(state)
            raise
        self._persist_thread(state)
        turn = _turn_document(
            active.turn_id, "inProgress", started_at=active.started_at)
        await self._result(websocket, request_id, {"turn": turn})
        await self._notify(websocket, "thread/status/changed", {
            "threadId": state.thread_id,
            "status": {"type": "active", "activeFlags": []},
        })
        await self._notify(websocket, "turn/started", {
            "threadId": state.thread_id,
            "turn": turn,
        })
        self._ensure_active_tracking(websocket, state)

    def _ensure_active_tracking(
            self, websocket: Any, state: ThreadState,
    ) -> None:
        active = state.active
        if (active is None or active.task_handle is None
                or active.tracking):
            return
        active.tracking = True
        task = asyncio.create_task(self._finish_turn(websocket, state, active))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    @staticmethod
    def _decision_output(reconciliation: MainTurnReconciliation) -> str:
        decision = reconciliation.decision
        if decision is None:
            raise RuntimeError(
                "committed main-turn reconciliation lacks a decision")
        return render_main_decision(decision, reconciliation.child)

    @staticmethod
    def _resolve_idle_reconciliation(
            state: ThreadState,
            reconciliation: MainTurnReconciliation,
    ) -> MainTurnReconciliation:
        if reconciliation.state != "idle":
            return reconciliation
        committed_count = len(state.session.history) // 2
        projected_count = len(state.turns)
        if committed_count == projected_count + 1:
            recover = getattr(
                state.session, "latest_committed_reconciliation", None)
            if recover is not None:
                recovered = recover()
                if recovered.state != "committed":
                    raise RuntimeError(
                        "latest main-session turn is not committed")
                return recovered
            role, reply = state.session.history[-1]
            if role != "assistant" or not isinstance(reply, str):
                raise RuntimeError(
                    "main-session Registry history has an invalid answer")
            return MainTurnReconciliation(
                "committed", None, MainDecision(reply, None))
        if committed_count == projected_count:
            return MainTurnReconciliation("interrupted", None)
        raise RuntimeError(
            "idle main-turn reconciliation differs from the UI projection")

    async def _finalize_active_turn(
            self, websocket: Any | None, state: ThreadState,
            active: ActiveTurn,
            reconciliation: MainTurnReconciliation,
    ) -> None:
        if state.active is not active:
            return
        if reconciliation.state == "committed":
            status = "completed"
            output = self._decision_output(reconciliation)
        elif reconciliation.state in {"interrupted", "paused"}:
            status = "interrupted"
            output = (
                "当前 RPNH turn 已暂停并保留 checkpoint；"
                "请在 basic 前端使用 /resume 或 /rollback。"
                if reconciliation.state == "paused" else
                "已按用户请求回退当前 RPNH turn。")
        elif reconciliation.state == "failed":
            status = "failed"
            output = (
                "RPNH main turn ended without terminal Registry evidence. "
                "The child Registry was retained and the main thread is "
                "ready for another turn.")
        else:
            raise RuntimeError(
                "cannot finalize a nonterminal main-turn reconciliation")

        started_item = {
            "type": "agentMessage", "id": active.item_id, "text": ""}
        item = {
            "type": "agentMessage", "id": active.item_id, "text": output}
        user_item = {
            "type": "userMessage",
            "id": _ui_id(state.thread_id, active.ordinal, "active-user"),
            "content": [{"type": "text", "text": active.user_text}],
        }
        turn = _turn_document(
            active.turn_id, status, items=[user_item, item],
            started_at=active.started_at)

        # The persisted list is rebuilt after every terminal reconciliation.
        # Interrupted turns are therefore notified to the live client but are
        # never inserted into conversation history.
        state.turns = self._committed_turns(
            state,
            latest_active=(
                active if reconciliation.state == "committed" else None),
            previous_turns=list(state.turns),
        )
        state.active = None
        self._persist_thread(state)
        if websocket is None:
            return
        try:
            await self._notify(websocket, "item/started", {
                "threadId": state.thread_id,
                "turnId": active.turn_id,
                "item": started_item,
                "startedAtMs": _now_milliseconds(),
            })
            await self._notify(websocket, "item/agentMessage/delta", {
                "threadId": state.thread_id,
                "turnId": active.turn_id,
                "itemId": active.item_id,
                "delta": output,
            })
            await self._notify(websocket, "item/completed", {
                "threadId": state.thread_id,
                "turnId": active.turn_id,
                "item": item,
                "completedAtMs": _now_milliseconds(),
            })
            await self._notify(websocket, "turn/completed", {
                "threadId": state.thread_id,
                "turn": turn,
            })
            await self._notify(websocket, "thread/status/changed", {
                "threadId": state.thread_id,
                "status": {"type": "idle"},
            })
        except Exception:
            pass

    async def _finish_turn(
            self, websocket: Any | None, state: ThreadState,
            active: ActiveTurn,
    ) -> None:
        try:
            if active.task_handle is None:
                return
            await _wait_process(active.task_handle.process)
            if state.active is not active:
                return
            reconciliation = self._resolve_idle_reconciliation(
                state, state.session.reconcile_active_turn())
            if reconciliation.state in {"committed", "interrupted", "paused"}:
                await self._finalize_active_turn(
                    websocket, state, active, reconciliation)
            elif reconciliation.state == "running":
                # Process exit is not child terminal evidence.  Record the
                # exact nonterminal child observation as a failed main turn,
                # retaining the independent child Registry unchanged.
                failed = state.session.fail_active_turn()
                await self._finalize_active_turn(
                    websocket, state, active, failed)
        finally:
            if state.active is active:
                active.tracking = False

    async def _refresh_active_turn(
            self, websocket: Any | None, state: ThreadState,
    ) -> None:
        active = state.active
        if active is None:
            return
        reconciliation = self._resolve_idle_reconciliation(
            state, state.session.reconcile_active_turn())
        if reconciliation.state in {"committed", "interrupted", "paused"}:
            await self._finalize_active_turn(
                websocket, state, active, reconciliation)
            return
        if reconciliation.state not in {
                "accepted", "pending_start", "running"}:
            raise RuntimeError(
                "unsupported active main-turn reconciliation state: "
                f"{reconciliation.state}")
        if reconciliation.snapshot is None:
            raise RuntimeError(
                "active main-turn reconciliation lacks a snapshot")
        if active.task_handle is None:
            active.task_handle = self._live_main_turn_handle(
                state.main_turn_control,
                reconciliation.snapshot.attempt_path,
            )
        if websocket is not None:
            self._ensure_active_tracking(websocket, state)

    async def _interrupt_turn(
            self, websocket: Any, request_id: object,
            params: Mapping[str, Any],
    ) -> None:
        state = self._thread(params.get("threadId"))
        active = state.active
        if active is None:
            await self._result(websocket, request_id, {})
            return
        requested_turn = params.get("turnId")
        if requested_turn not in (None, active.turn_id):
            raise ValueError("turn/interrupt does not match the active RPNH turn")
        reconciliation = self._resolve_idle_reconciliation(
            state, state.session.reconcile_active_turn())
        if reconciliation.state in {"committed", "interrupted", "paused"}:
            await self._finalize_active_turn(
                websocket, state, active, reconciliation)
            await self._result(websocket, request_id, {})
            return
        if active.task_handle is None:
            raise RuntimeError(
                "active Registry turn has no live foreground task to interrupt")
        state.main_turn_control.stop(
            active.task_handle.task_id, startup_safe=True)
        # Re-read Registry after signaling so a natural terminal publication
        # that won the race cannot be projected as an interruption.
        reconciliation = self._resolve_idle_reconciliation(
            state, state.session.reconcile_active_turn())
        if reconciliation.state in {"committed", "interrupted", "paused"}:
            await self._finalize_active_turn(
                websocket, state, active, reconciliation)
        elif state.active is active:
            self._ensure_active_tracking(websocket, state)
        await self._result(websocket, request_id, {})

    async def _update_thread_settings(
            self, websocket: Any, request_id: object,
            params: Mapping[str, Any],
    ) -> None:
        state = self._thread(params.get("threadId"))
        if state.active is not None:
            raise ValueError(
                "cannot change RPNH provider/model during an active turn")
        supported_fields = {
            "threadId", "model", "effort", "collaborationMode",
            "approvalPolicy", "approvalsReviewer", "cwd", "multiAgentMode",
            "permissions", "personality", "sandboxPolicy", "serviceTier",
            "summary",
        }
        unknown = set(params) - supported_fields
        if unknown:
            raise ValueError(
                "RPNH thread/settings/update contains unknown fields")
        unchanged_fields = supported_fields - {
            "threadId", "model", "effort", "collaborationMode",
            "approvalPolicy", "approvalsReviewer", "permissions",
        }
        if any(params.get(field) is not None for field in unchanged_fields):
            raise ValueError(
                "RPNH thread/settings/update only changes model, effort, "
                "or the fixed permission profile")
        permissions = params.get("permissions")
        approval_policy = params.get("approvalPolicy")
        approvals_reviewer = params.get("approvalsReviewer")
        if permissions not in {None, ":danger-full-access"}:
            raise ValueError(
                "RPNH exposes only the fixed Full Access permission profile")
        if approval_policy not in {None, "never"}:
            raise ValueError(
                "RPNH exposes only the fixed never approval policy")
        if approvals_reviewer not in {None, "user"}:
            raise ValueError(
                "RPNH exposes only the fixed user approvals reviewer")
        collaboration_mode = params.get("collaborationMode")
        if (collaboration_mode is not None
                and (not isinstance(collaboration_mode, Mapping)
                     or collaboration_mode.get("mode") != "default")):
            raise ValueError(
                "RPNH Codex frontend supports default collaboration mode only")
        effort = params.get("effort")
        if effort not in {None, "medium"}:
            raise ValueError(
                "RPNH profiles expose only the medium frontend effort")
        model_id = params.get("model")
        if model_id is not None:
            if not isinstance(model_id, str):
                raise ValueError("RPNH thread model must be a selection ID")
            profile = self._profiles_by_model_id.get(model_id)
            if profile is None:
                raise ValueError(
                    f"unknown RPNH provider/model selection: {model_id}")
            state.session.set_execution_config(profile.path)
            state.model_id = profile.selection_id
        self._persist_thread(state)
        await self._result(websocket, request_id, {})

    def _write_default_model(
            self, params: Mapping[str, Any],
    ) -> dict[str, Any]:
        unknown = set(params) - {
            "edits", "expectedVersion", "filePath", "reloadUserConfig",
        }
        if unknown:
            raise ValueError("RPNH config/batchWrite contains unknown fields")
        file_path = params.get("filePath")
        if file_path is not None:
            raise ValueError(
                "RPNH config/batchWrite does not write Codex config files")
        expected_version = params.get("expectedVersion")
        if expected_version is not None and not isinstance(expected_version, str):
            raise ValueError(
                "RPNH config/batchWrite expectedVersion must be text or null")
        reload_user_config = params.get("reloadUserConfig")
        if reload_user_config is not None and not isinstance(
                reload_user_config, bool):
            raise ValueError(
                "RPNH config/batchWrite reloadUserConfig must be boolean")
        edits = params.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError(
                "RPNH config/batchWrite requires model selection edits")
        by_key: dict[str, Mapping[str, Any]] = {}
        for edit in edits:
            if not isinstance(edit, Mapping):
                raise ValueError(
                    "RPNH config/batchWrite edit must be an object")
            key_path = edit.get("keyPath")
            if key_path not in {"model", "model_reasoning_effort"}:
                raise ValueError(
                    "RPNH config/batchWrite only supports model selection")
            if key_path in by_key:
                raise ValueError(
                    "RPNH config/batchWrite has duplicate model edits")
            if edit.get("mergeStrategy") not in {"replace", "upsert"}:
                raise ValueError(
                    "RPNH config/batchWrite model edit has invalid "
                    "mergeStrategy")
            by_key[key_path] = edit
        model_edit = by_key.get("model")
        if model_edit is None:
            raise ValueError(
                "RPNH config/batchWrite requires a model selection ID")
        effort_edit = by_key.get("model_reasoning_effort")
        if (effort_edit is not None
                and effort_edit.get("value") not in {None, "medium"}):
            raise ValueError(
                "RPNH profiles expose only the medium frontend effort")
        model_id = model_edit.get("value")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(
                "RPNH config/batchWrite model value must be a selection ID")
        profile = self._profiles_by_model_id.get(model_id)
        if profile is None:
            choices = ", ".join(self._profiles_by_model_id) or "(none)"
            raise ValueError(
                "unknown RPNH provider/model selection; choose one of: "
                f"{choices}")
        destination = config_path().expanduser().resolve()
        current_version = self._config_version(destination)
        if (expected_version is not None
                and expected_version != current_version):
            raise ValueError("RPNH config/batchWrite expectedVersion is stale")
        if (len(self.model_profiles) == 1
                and profile.path == self.execution_config_path):
            save_selected_path(profile.path)
        else:
            select_profile(profile.selection_id)
        self.default_model_id = profile.selection_id
        return {
            "filePath": str(destination),
            "status": "ok",
            "version": self._config_version(destination),
        }

    @staticmethod
    def _config_version(path: Path) -> str:
        if not path.exists():
            return "rpnh-uninitialized"
        stat = path.stat()
        return f"rpnh-{stat.st_mtime_ns}-{stat.st_size}"

    async def _handle_request(
            self, websocket: Any, request_id: object, method: str,
            params: Mapping[str, Any],
    ) -> None:
        if method == "initialize":
            client = params.get("clientInfo")
            if (not isinstance(client, Mapping)
                    or client.get("name") != "codex-tui"
                    or client.get("version") != CODEX_FRONTEND_VERSION):
                raise ValueError(
                    "RPNH requires codex-tui "
                    f"{CODEX_FRONTEND_VERSION} exactly")
            await self._result(websocket, request_id, {
                "userAgent": f"rpnh/codex-compat-{CODEX_FRONTEND_VERSION}",
                "codexHome": str(self.root),
                "platformFamily": "unix",
                "platformOs": "linux",
            })
            self._initialize_accepted_connections.add(id(websocket))
            return
        if id(websocket) not in self._initialized_connections:
            raise ValueError(
                "client must successfully initialize before other requests")
        if method == "account/read":
            await self._result(websocket, request_id, {
                "account": None, "requiresOpenaiAuth": False})
        elif method == "model/list":
            await self._result(websocket, request_id, {
                "data": [
                    self._model(profile) for profile in self.model_profiles
                ],
                "nextCursor": None,
            })
        elif method == "configRequirements/read":
            await self._result(websocket, request_id, {
                "requirements": {
                    "allowedApprovalPolicies": ["never"],
                    "allowedApprovalsReviewers": ["user"],
                    "allowedPermissionProfiles": {
                        ":danger-full-access": True,
                    },
                    "allowedSandboxModes": ["danger-full-access"],
                    "defaultPermissions": ":danger-full-access",
                },
            })
        elif method == "config/read":
            await self._result(websocket, request_id, {
                "config": {
                    "model": self.default_model_id,
                    "model_reasoning_effort": "medium",
                    "model_provider": "rpnh",
                    "default_permissions": ":danger-full-access",
                    "approval_policy": "never",
                    "sandbox_mode": "danger-full-access",
                    "analytics": {"enabled": False},
                },
                "origins": {},
                "layers": None,
            })
        elif method == "config/batchWrite":
            await self._result(
                websocket, request_id, self._write_default_model(params))
        elif method.startswith("thread/attachment/"):
            # A frontend-only attachment is not an acknowledged Registry input.
            # Do not return successful CRUD for a capability the runtime lacks.
            await self._error(
                websocket, request_id,
                "RPNH does not implement frontend attachment delivery; "
                "use explicitly registered task inputs instead.", code=-32601)
        elif method in {"collaborationMode/list", "hooks/list", "skills/list"}:
            await self._result(websocket, request_id, {"data": []})
        elif method == "plugin/list":
            await self._result(websocket, request_id, {
                "marketplaces": [], "featuredPluginIds": [],
                "marketplaceLoadErrors": [],
            })
        elif method == "apps/list":
            await self._result(websocket, request_id, {
                "data": [], "nextCursor": None})
        elif method == "permissionProfile/list":
            await self._result(websocket, request_id, {
                "data": [{
                    "id": ":danger-full-access",
                    "description": (
                        "RPNH-managed execution. This frontend compatibility label "
                        "does not bypass Registry grants or runtime isolation."),
                    "allowed": True,
                }],
                "nextCursor": None,
            })
        elif method == "thread/start":
            await self._start_thread(websocket, request_id, params)
        elif method == "thread/resume":
            state = self._thread(params.get("threadId"))
            await self._refresh_active_turn(websocket, state)
            await self._result(websocket, request_id, {
                "thread": self._thread_document(state),
                "model": state.model_id,
                "modelProvider": "rpnh",
                "cwd": str(state.cwd),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {"type": "dangerFullAccess"},
                "reasoningEffort": "medium",
                "serviceTier": None,
                "instructionSources": [],
            })
        elif method == "thread/loaded/list":
            await self._result(websocket, request_id, {
                "data": list(self._threads), "nextCursor": None})
        elif method == "thread/list":
            await self._result(websocket, request_id, {
                "data": [
                    self._thread_document(state)
                    for state in self._listed_threads(params)
                ],
                "nextCursor": None,
                "backwardsCursor": None,
            })
        elif method == "thread/read":
            state = self._thread(params.get("threadId"))
            await self._result(websocket, request_id, {
                "thread": self._thread_document(state)})
        elif method == "thread/name/set":
            state = self._thread(params.get("threadId"))
            name = params.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("thread name must be nonempty")
            state.name = name.strip()
            self._persist_thread(state)
            await self._result(websocket, request_id, {})
            await self._notify(websocket, "thread/name/updated", {
                "threadId": state.thread_id, "threadName": state.name})
        elif method == "thread/settings/update":
            await self._update_thread_settings(
                websocket, request_id, params)
        elif method == "turn/start":
            await self._start_turn(websocket, request_id, params)
        elif method == "turn/interrupt":
            await self._interrupt_turn(websocket, request_id, params)
        else:
            await self._error(
                websocket, request_id,
                f"RPNH Codex compatibility method is not implemented: {method}",
                code=-32601,
            )

    async def handle(self, websocket: Any) -> None:
        key = id(websocket)
        try:
            try:
                async for raw in websocket:
                    request_id: object | None = None
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, Mapping):
                            raise ValueError("JSON-RPC message must be an object")
                        request_id = value.get("id")
                        method = value.get("method")
                        if not isinstance(method, str) or not method:
                            raise ValueError("JSON-RPC method must be nonempty text")
                        params = value.get("params", {})
                        if not isinstance(params, Mapping):
                            raise ValueError("JSON-RPC params must be an object")
                        if request_id is None:
                            if method == "initialized":
                                if key not in self._initialize_accepted_connections:
                                    raise ValueError(
                                        "client must successfully initialize "
                                        "before sending initialized")
                                self._initialized_connections.add(key)
                                for state in self._threads.values():
                                    await self._refresh_active_turn(
                                        websocket, state)
                            continue
                        await self._handle_request(
                            websocket, request_id, method, params)
                    except (json.JSONDecodeError, OSError, RuntimeError,
                            TypeError, ValueError) as exc:
                        if request_id is not None:
                            await self._error(websocket, request_id, str(exc))
            except Exception as exc:
                if (not exc.__class__.__module__.startswith("websockets.")
                        or not exc.__class__.__name__.startswith(
                            "ConnectionClosed")):
                    raise
        finally:
            self._initialize_accepted_connections.discard(key)
            self._initialized_connections.discard(key)
            self._send_locks.pop(key, None)

    async def stop_active_turns(self) -> None:
        for state in self._threads.values():
            await self._refresh_active_turn(None, state)
            active = state.active
            if active is None or active.task_handle is None:
                continue
            state.main_turn_control.stop(
                active.task_handle.task_id, startup_safe=True)
            if not active.tracking:
                active.tracking = True
                task = asyncio.create_task(
                    self._finish_turn(None, state, active))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
        if self._background:
            pending = tuple(self._background)
            await asyncio.gather(*pending, return_exceptions=True)
        for state in self._threads.values():
            await self._refresh_active_turn(None, state)


def resolve_codex_binary(value: str | None = None) -> str:
    selected = value or os.environ.get("RPNH_CODEX_BIN") or "codex"
    resolved = shutil.which(selected)
    if resolved is None:
        raise CodexCompatibilityError(
            "Codex frontend is not installed; install "
            f"@openai/codex@{CODEX_FRONTEND_VERSION}")
    try:
        completed = subprocess.run(
            [resolved, "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise CodexCompatibilityError("Codex version check timed out") from exc
    reported = completed.stdout.strip()
    if completed.returncode != 0 or reported != CODEX_CLI_VERSION_TEXT:
        raise CodexCompatibilityError(
            f"RPNH requires {CODEX_CLI_VERSION_TEXT}; got "
            f"{reported or completed.stderr.strip() or 'unknown'}")
    return resolved


def codex_frontend_argv(
        binary: str, socket_path: str | Path, model_id: str,
) -> tuple[str, ...]:
    """Pinned presentation client only; no provider or execution authority."""
    return (
        binary, "--remote", f"unix://{socket_path}", "--model", model_id,
        "--no-alt-screen",
        *(argument for feature in CODEX_DISABLED_FEATURES
          for argument in ("--disable", feature)),
        *(argument for override in CODEX_CONFIG_OVERRIDES
          for argument in ("--config", override)),
    )


async def _run_codex_frontend_async(
        root: Path, execution_config_path: Path, *,
        codex_binary: str | None = None, resume: bool = False,
) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise CodexCompatibilityError(
            "Codex frontend requires the Python `websockets` package") from exc
    binary = resolve_codex_binary(codex_binary)
    root = root.resolve()
    if resume and not root.is_dir():
        raise CodexCompatibilityError(
            "RPNH Codex resume requires an existing session directory")
    root.mkdir(parents=True, exist_ok=True)
    socket_path = root / "codex-app-server.sock"
    if os.path.lexists(socket_path):
        raise CodexCompatibilityError(
            f"Codex frontend socket already exists: {socket_path}")
    server = CodexAppServer(root, execution_config_path)
    if resume and not server._threads:
        diagnostic = server.thread_load_diagnostic()
        raise CodexCompatibilityError(
            "RPNH Codex resume found no valid persisted threads"
            + (f"; {diagnostic}" if diagnostic else ""))
    diagnostic = server.thread_load_diagnostic()
    if diagnostic:
        print(f"RPNH Codex warning: {diagnostic}", file=sys.stderr)
    socket_identity: int | None = None
    try:
        with unix_socket_address(
                socket_path, visible_to_child_process=True) as address:
            async with websockets.unix_serve(server.handle, address):
                socket_path.chmod(0o600)
                socket_identity = socket_path.stat().st_ino
                process = await asyncio.create_subprocess_exec(
                    *codex_frontend_argv(
                        binary, address, server.frontend_model_id))
                try:
                    return int(await process.wait())
                finally:
                    await server.stop_active_turns()
    finally:
        if (socket_identity is not None and socket_path.exists()
                and socket_path.stat().st_ino == socket_identity):
            socket_path.unlink()


def run_codex_frontend(
        root: Path, execution_config_path: Path, *,
        codex_binary: str | None = None, resume: bool = False,
) -> int:
    return asyncio.run(_run_codex_frontend_async(
        root, execution_config_path, codex_binary=codex_binary,
        resume=resume))


__all__ = (
    "CODEX_CLI_VERSION_TEXT", "CODEX_CONFIG_OVERRIDES",
    "CODEX_DISABLED_FEATURES",
    "CODEX_FRONTEND_VERSION", "CodexAppServer", "CodexCompatibilityError",
    "resolve_codex_binary", "run_codex_frontend",
)
