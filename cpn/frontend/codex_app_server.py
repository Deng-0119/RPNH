"""Exact-profile Codex TUI compatibility surface backed only by RPNH.

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
import tempfile
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.main_session import (
    MainDecision,
    MainSession,
    MainTurnReconciliation,
    MainTurnSnapshot,
    render_main_decision,
)
from cpn.rpnh.provider_catalog import provider_manifest_path
from cpn.rpnh.session_access import (
    MainSessionOwnerLease,
    MainSessionSourceBinding,
    inspect_main_session_root,
    stable_frontend_session_id,
)
from cpn.rpnh.task_control import TaskControl, TaskHandle
from cpn.rpnh.unix_transport import unix_socket_address
from cpn.frontend.codex_history import (
    HistoryCursor, INITIAL_VIEW, codex_thread_id, entry_anchor, page_limit, public_id,
    object_item_anchor, object_item_cursor_id,
)
from cpn.rpnh.user_config import (
    ExecutionProfile,
    config_path,
    discover_profiles,
    profile_for_path,
    save_selected_path,
)


CODEX_DISABLED_FEATURES = ("goals", "personality", "plugins")
CODEX_CONFIG_OVERRIDES = ("check_for_update_on_startup=false",)


class CodexCompatibilityError(RuntimeError):
    """The pinned frontend or protocol contract cannot be satisfied."""


@dataclass(frozen=True, slots=True)
class _CodexCompatibilityProfile:
    name: str
    version: str
    reported_cli_version: str
    object_item_cursor: bool
    explicit_history_defaults: bool
    status: str


def _load_compatibility_profiles():
    manifest = json.loads(Path(__file__).with_name(
        "codex_compatibility.v1.json").read_text(encoding="utf-8"))
    table = manifest["compatibility_profiles"]
    profiles = MappingProxyType({
        name: _CodexCompatibilityProfile(name=name, **fields)
        for name, fields in table["profiles"].items()
    })
    default = profiles[table["default"]]
    if (default.version != manifest["frontend"]["version"]
            or default.reported_cli_version != manifest["frontend"]["reported_cli_version"]):
        raise CodexCompatibilityError("Codex default profile and frontend pin differ")
    return profiles, default


_CODEX_PROFILES, _DEFAULT_CODEX_PROFILE = _load_compatibility_profiles()
# Preserve the published default and existing caller constants.
CODEX_FRONTEND_VERSION = _DEFAULT_CODEX_PROFILE.version
CODEX_CLI_VERSION_TEXT = _DEFAULT_CODEX_PROFILE.reported_cli_version


def _compatibility_profile(name: str | None = None) -> _CodexCompatibilityProfile:
    if name is None:
        return _DEFAULT_CODEX_PROFILE
    if not isinstance(name, str) or name not in _CODEX_PROFILES:
        raise CodexCompatibilityError("unknown Codex compatibility profile")
    return _CODEX_PROFILES[name]


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
    reasoning_effort: str | None = None
    preview: str = ""
    name: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    # Preserve old UI sidecar metadata on resume; never advertise it as input.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    active: ActiveTurn | None = None
    history_binding: MainSessionSourceBinding | None = None


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
            compatibility_profile: str | None = None,
            history_object_max_bytes: int | None = None,
            history_response_max_bytes: int | None = None,
    ) -> None:
        self._compatibility_profile = _compatibility_profile(compatibility_profile)
        for budget in (history_object_max_bytes, history_response_max_bytes):
            if budget is not None and (type(budget) is not int or budget < 0):
                raise ValueError("history byte budget must be a nonnegative integer")
        self.history_object_max_bytes = history_object_max_bytes
        self.history_response_max_bytes = history_response_max_bytes
        self.root = root.resolve()
        self.execution_config_path = execution_config_path.resolve()
        selection = load_llm_execution_selection(self.execution_config_path)
        provenance = selection.as_registry_policy()
        self.model_condition = selection.input_target.model_condition
        active_profile = profile_for_path(self.execution_config_path)
        self.profile_name = profile_name or active_profile.name
        self.frontend_model_id = active_profile.selection_id
        adjacent_profiles = (
            discover_profiles(self.execution_config_path.parent)
            if provider_manifest_path(
                self.execution_config_path.parent).is_file()
            else ())
        installed_profiles = (
            adjacent_profiles
            if any(profile.path == self.execution_config_path
                   for profile in adjacent_profiles)
            else discover_profiles())
        if any(
                profile.path == self.execution_config_path
                for profile in installed_profiles):
            all_profiles = tuple(
                profile for profile in installed_profiles
                if profile.selectable)
        else:
            all_profiles = (active_profile,)
        self._profiles_by_variant = {
            (profile.selection_id, profile.reasoning_effort): profile
            for profile in all_profiles
        }
        if len(self._profiles_by_variant) != len(all_profiles):
            raise ValueError(
                "RPNH model/effort execution identities are not unique")
        self.model_profiles = tuple(
            profile for profile in all_profiles
            if profile.reasoning_effort == profile.default_reasoning_effort)
        if not self.model_profiles:
            self.model_profiles = (active_profile,)
        self._profiles_by_model_id = {
            profile.selection_id: profile for profile in self.model_profiles
        }
        self._profiles_by_path = {
            profile.path: profile for profile in all_profiles
        }
        self._initial_profile_identities = {
            profile.path: self._execution_identity(profile.path)
            for profile in all_profiles
        }
        self.default_model_id = self.frontend_model_id
        self.default_reasoning_effort = active_profile.reasoning_effort
        routes = provenance.get("route_provenance")
        self.provider_route = (
            str(routes[0].get("provider"))
            if isinstance(routes, list) and routes else selection.adapter_kind)
        self._threads: dict[str, ThreadState] = {}
        self._initialize_accepted_connections: set[int] = set()
        self._initialized_connections: set[int] = set()
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._lease: MainSessionOwnerLease | None = None
        try:
            self._load_direct_session()
        except Exception:
            self.close()
            raise

    @property
    def compatibility_profile(self) -> _CodexCompatibilityProfile:
        """Fixed at construction; client input cannot upgrade this profile."""
        return self._compatibility_profile

    @staticmethod
    def _thread_state_path(root: Path) -> Path:
        return root / ".frontends" / "codex.json"

    def _persist_thread(self, state: ThreadState) -> None:
        path = self._thread_state_path(state.session.root)
        value = {
            "schema_version": "rpnh/codex_frontend_metadata/v1",
            "protocol_thread_id": state.thread_id,
            "cwd": str(state.cwd),
            "created_at": state.created_at,
            "preview": state.preview,
            "name": state.name,
            "attachments": state.attachments,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _committed_turns(
            state: ThreadState, *, latest_active: ActiveTurn | None = None,
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
            projected_turn_id, projected_user_id, projected_assistant_id = (
                _ui_id(state.thread_id, ordinal, "turn"),
                _ui_id(state.thread_id, ordinal, "user"),
                _ui_id(state.thread_id, ordinal, "assistant"),
            )
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

    def _load_direct_session(self) -> None:
        if not os.path.lexists(self.root):
            return
        record = inspect_main_session_root(self.root)
        profile = self._profiles_by_path.get(record.execution_config_path)
        if profile is None:
            raise ValueError("persisted main-session model is unavailable")
        path = self._thread_state_path(record.root)
        value: Mapping[str, Any] = {}
        if path.is_file():
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if (isinstance(candidate, Mapping)
                        and candidate.get("schema_version")
                        == "rpnh/codex_frontend_metadata/v1"):
                    value = candidate
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
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
            else int(record.root.stat().st_mtime))
        raw_preview = value.get("preview")
        preview = raw_preview if isinstance(raw_preview, str) else ""
        raw_name = value.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        raw_attachments = value.get("attachments", [])
        try:
            attachments = self._attachments(
                raw_attachments if isinstance(raw_attachments, list) else [])
        except ValueError:
            attachments = []

        lease = MainSessionOwnerLease.acquire(record)
        self._lease = lease
        try:
            session = MainSession.resume(
                record.root, record.execution_config_path,
                task_control=object())
            session.task_control = TaskControl(
                session.child_path_root / "tasks",
                recover_pending_launches=False,
            )
            state = ThreadState(
                thread_id=codex_thread_id(stable_frontend_session_id(record)),
                session=session,
                main_turn_control=TaskControl(
                    session.child_path_root / "main-turn-control",
                    recover_pending_launches=False),
                model_id=profile.selection_id,
                reasoning_effort=profile.reasoning_effort,
                cwd=cwd,
                created_at=created_at,
                preview=preview,
                name=name,
                attachments=attachments,
            )
            state.history_binding = MainSessionSourceBinding.capture(session)
            state.turns = self._committed_turns(state)
            snapshot = session.active_turn_snapshot()
            if snapshot is not None:
                state.active = self._active_from_snapshot(state, snapshot)
            if not state.preview and session.history:
                state.preview = session.history[0][1][:200]
            self._threads[state.thread_id] = state
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Release ownership of the canonical MainSession root."""
        if self._lease is not None:
            lease, self._lease = self._lease, None
            lease.close()

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

    @staticmethod
    def _has_unconfigured_effort(profile: ExecutionProfile) -> bool:
        """Identify absence of canonical configuration, not the string "none"."""
        return (
            profile.reasoning_effort is None
            and profile.default_reasoning_effort is None
            and not profile.supported_reasoning_efforts)

    @classmethod
    def _effort_to_wire(
            cls, profile: ExecutionProfile, effort: str | None,
    ) -> str | None:
        """Codex's None enum is a display token scoped to one selection.

        Never write it into an execution profile or provider request. A model
        configured with the literal effort "none" retains that exact value.
        """
        if effort is None and cls._has_unconfigured_effort(profile):
            return "none"
        return effort

    def _selected_effort_to_wire(
            self, model_id: str, effort: str | None,
    ) -> str | None:
        return self._effort_to_wire(self._profiles_by_model_id[model_id], effort)

    def _profile_for_selection(
            self, model_id: str, effort: object = None,
    ) -> ExecutionProfile:
        if not isinstance(model_id, str):
            raise ValueError("RPNH thread model must be a selection ID")
        logical = self._profiles_by_model_id.get(model_id)
        if logical is None:
            raise ValueError(
                f"unknown RPNH provider/model selection: {model_id}")
        # Decode only after resolving the selection. "none" is also a real
        # configured effort and must not be normalized globally.
        if effort == "none" and self._has_unconfigured_effort(logical):
            effort = None
        selected_effort = (
            logical.default_reasoning_effort if effort is None else effort)
        if selected_effort is not None and not isinstance(
                selected_effort, str):
            raise ValueError("RPNH reasoning effort must be text or null")
        if (selected_effort is None
                and logical.reasoning_effort is None
                and not logical.supported_reasoning_efforts):
            profile = logical
        else:
            profile = self._profiles_by_variant.get(
                (model_id, selected_effort))
            if profile is None:
                choices = ", ".join(logical.supported_reasoning_efforts)
                raise ValueError(
                    "unknown RPNH reasoning effort for selected model; "
                    f"choose one of: {choices or '(none)'}")
        self._assert_profile_identity(profile)
        return profile

    @staticmethod
    def _execution_identity(path: Path) -> dict[str, object]:
        """Capture the complete non-secret identity behind one physical path."""

        selected = path.expanduser().resolve()
        selection = load_llm_execution_selection(selected)
        profile = profile_for_path(selected)
        return {
            "schema_version": "rpnh/main_session_profile/v3",
            "execution_config_path": str(selected),
            "adapter_config_path": str(selection.adapter_config_path),
            "selection_id": profile.selection_id,
            "provider": profile.provider,
            "model_condition": selection.input_target.model_condition,
            "reasoning_effort": selection.reasoning_effort,
            "adapter_kind": selection.adapter_kind,
            "registry_policy": selection.as_registry_policy(),
        }

    def _assert_profile_identity(self, profile: ExecutionProfile) -> None:
        expected = self._initial_profile_identities.get(profile.path)
        try:
            current = self._execution_identity(profile.path)
        except Exception as exc:
            raise ValueError(
                "RPNH provider/model/effort profile changed while the "
                "Codex frontend was running; restart the frontend") from exc
        if expected is None or current != expected:
            raise ValueError(
                "RPNH provider/model/effort profile changed while the "
                "Codex frontend was running; restart the frontend")

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
            "defaultReasoningEffort": self._effort_to_wire(
                profile, profile.default_reasoning_effort),
            "supportedReasoningEfforts": [{
                "reasoningEffort": effort,
                "description": (
                    "Configured for this exact model in the RPNH catalog."),
            } for effort in profile.supported_reasoning_efforts],
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
            "cliVersion": self.compatibility_profile.version,
            "source": "appServer",
            "status": status,
            # Persisted history is hydrated through native fixed-cut pages.
            # state.turns is only the existing live reconciliation cache.
            "turns": [],
            "projectId": None,
            "historyMode": "paginated",
        }

    async def _send(
            self, websocket: Any, value: Mapping[str, Any], *,
            recheck: Callable[[], None] | None = None,
            max_bytes: int | None = None,
    ) -> None:
        key = id(websocket)
        lock = self._send_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if recheck is not None:
                recheck()
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if max_bytes is not None and len(encoded.encode("utf-8")) > max_bytes:
                raise ValueError("history response exceeds configured byte budget")
            await websocket.send(encoded)

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

    def _history_state(self, websocket: Any, thread_id: object) -> ThreadState:
        """Recheck the existing local-owner binding; a cursor grants nothing."""
        try:
            state = self._thread(thread_id)
            if (id(websocket) not in self._initialized_connections
                    or self._lease is None or self._lease.root != self.root
                    or state.session.root != self.root or state.history_binding is None):
                raise ValueError("unbound")
            self._lease.assert_held()
            state.history_binding.assert_matches(state.session)
            return state
        except Exception as exc:
            raise ValueError("history owner/source binding is unavailable") from exc

    async def _history_result(self, websocket, request_id, state, value):
        def recheck():
            if self._history_state(websocket, state.thread_id) is not state:
                raise ValueError("history owner/source binding is unavailable")
        await self._send(websocket, {"id": request_id, "result": value},
                         recheck=recheck, max_bytes=self.history_response_max_bytes)

    def _history_cut(self, state):
        return state.session.history_registry.capture_read_cut(
            max_object_bytes=self.history_object_max_bytes)

    @staticmethod
    def _history_items(thread_id, display):
        return [{"type": "userMessage", "id": public_id(thread_id, display.ordinal, "user"),
                 "content": [{"type": "text", "text": display.user_text}]},
                {"type": "agentMessage", "id": public_id(thread_id, display.ordinal, "agent"),
                 "text": display.assistant_text}]

    def _history_page(self, state, params, query):
        allowed = {"threadId", "cursor", "limit", "sortDirection",
                   "itemsView" if query == "turns" else "turnId"}
        if set(params) - allowed:
            raise ValueError("history query contains unsupported fields")
        order = params.get("sortDirection")
        if order is None:
            order = "desc" if query == "turns" else "asc"
        if order not in ("asc", "desc"):
            raise ValueError("history sortDirection must be asc or desc")
        view = params.get("itemsView") if query == "turns" else None
        if query == "turns" and view is None:
            view = "summary"
        if query == "turns" and view not in ("notLoaded", "summary", "full"):
            raise ValueError("history itemsView is invalid")
        limit = page_limit(params.get("limit"))
        token = params.get("cursor")
        requested_turn = params.get("turnId") if query == "items" else None
        item_id = None
        if isinstance(token, dict):
            if query != "items" or not self.compatibility_profile.object_item_cursor:
                raise ValueError("invalid history cursor")
            item_id = object_item_cursor_id(token, requested_turn)
        cursor = None if token is None or item_id is not None else HistoryCursor.decode(token)
        if cursor is not None:
            cursor.bind(thread_id=state.thread_id, query=query, order=order, items_view=view)
        cut = self._history_cut(state) if cursor is None else cursor.cut
        native = state.session.history_registry
        turn_filter = None
        # Ordinary pages are validated by the native page/hydration seams.
        # Only filter resolution or a special initial edge needs an extra
        # refs projection; do not eagerly replay all lineages one more time.
        projection = None
        if requested_turn is not None or cursor is not None and (
                cursor.items_view == INITIAL_VIEW or cursor.anchor is None):
            projection = native.project_thread_at(
                cut, max_object_bytes=self.history_object_max_bytes)
        if requested_turn is not None:
            if not isinstance(requested_turn, str) or not requested_turn:
                raise ValueError("history turnId must identify a committed turn")
            matches = [turn for turn in projection.turns
                       if public_id(state.thread_id, turn.ordinal, "turn") == requested_turn]
            if len(matches) != 1:
                raise ValueError("history turnId is not present at this cut")
            turn_filter = matches[0].turn_ref
            if item_id is not None:
                # Object anchors are fresh queries. Capture above exactly once,
                # then normalize into the existing native/string-cursor seam.
                cursor = HistoryCursor(state.thread_id, cut, query, order, view, turn_filter,
                    object_item_anchor(cut, matches[0], state.thread_id, item_id, order=order))
        if cursor is not None and cursor.turn_filter != turn_filter:
            raise ValueError("history cursor filter differs")
        if cursor is not None and cursor.items_view == INITIAL_VIEW:
            expected = (None if not projection.turns else entry_anchor(
                cut, projection.turns[-1], query="turns", order="desc", inclusive=True))
            if cursor.anchor != expected:
                raise ValueError("history initial cursor is not the cut boundary")
        if cursor is not None and cursor.anchor is None and projection.turns:
            raise ValueError("history empty boundary is not empty")
        kwargs = {"limit": limit, "order": order,
                  "anchor": None if cursor is None else cursor.anchor,
                  "max_object_bytes": self.history_object_max_bytes}
        page = (native.page_turns_at(cut, **kwargs) if query == "turns" else
                native.page_items_at(cut, turn_filter=turn_filter, **kwargs))
        refs = tuple(dict.fromkeys(entry.turn_ref for entry in page.entries))
        displays = {} if view == "notLoaded" else {
            display.turn_ref: display for display in state.session.display_history_at(
                cut, refs, max_object_bytes=self.history_object_max_bytes)}
        data = []
        for entry in page.entries:
            ordinal = entry.ordinal if query == "turns" else entry.turn_ordinal
            turn_id = public_id(state.thread_id, ordinal, "turn")
            if query == "turns":
                data.append({"id": turn_id, "items": [] if view == "notLoaded" else
                             self._history_items(state.thread_id, displays[entry.turn_ref]),
                             "itemsView": view, "status": "completed", "error": None,
                             "startedAt": None, "completedAt": None, "durationMs": None})
            else:
                item = self._history_items(state.thread_id, displays[entry.turn_ref])[entry.item_index]
                data.append({"turnId": turn_id, "item": item,
                             **({"startedAtMs": None, "completedAtMs": None}
                                if self.compatibility_profile.explicit_history_defaults else {})})
        next_cursor = None if page.next_anchor is None else HistoryCursor(
            state.thread_id, cut, query, order, view, turn_filter, page.next_anchor).encode()
        reverse_order = "asc" if order == "desc" else "desc"
        backwards = None if not page.entries else HistoryCursor(
            state.thread_id, cut, query, reverse_order, view, turn_filter,
            entry_anchor(cut, page.entries[0], query=query, order=reverse_order,
                         turn_filter=turn_filter, inclusive=True)).encode()
        return {"data": data, "nextCursor": next_cursor, "backwardsCursor": backwards}

    def _resume_history(self, state):
        cut = self._history_cut(state)
        projection = state.session.history_registry.project_thread_at(
            cut, max_object_bytes=self.history_object_max_bytes)
        turn_anchor = item_anchor = None
        if projection.turns:
            last = projection.turns[-1]
            turn_anchor = entry_anchor(cut, last, query="turns", order="desc", inclusive=True)
            item_anchor = entry_anchor(cut, last.items[-1], query="items", order="desc", inclusive=True)
        cursors = {
            "turnsBackwardsCursor": HistoryCursor(
                state.thread_id, cut, "turns", "desc", INITIAL_VIEW, None, turn_anchor).encode(),
            "itemsBackwardsCursor": HistoryCursor(
                state.thread_id, cut, "items", "desc", None, None, item_anchor).encode(),
        }
        return cursors, projection

    def _read_history_thread(self, state, include_turns):
        document = self._thread_document(state)
        if include_turns:
            params = {"threadId": state.thread_id, "sortDirection": "asc",
                      "itemsView": "full", "limit": 100}
            turns = []
            while True:
                page = self._history_page(state, params, "turns")
                turns.extend(page["data"])
                if page["nextCursor"] is None:
                    break
                params["cursor"] = page["nextCursor"]
            document["turns"] = turns
        return document

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

    def _start_thread_profile(
            self, params: Mapping[str, Any],
    ) -> ExecutionProfile:
        """Decode only the registered model/effort part of thread/start.

        Stock Codex sends effort in config; the top-level spelling remains a
        narrow legacy input. Absent/null values mean no explicit override.
        Other config keys retain their existing ignored behavior and cannot
        become provider, permission or execution configuration.
        """
        config = params.get("config")
        if config is not None and not isinstance(config, Mapping):
            raise ValueError("thread/start config must be an object or null")
        nested_effort = (
            config.get("model_reasoning_effort") if config is not None else None)
        legacy_effort = params.get("effort")
        for effort in (nested_effort, legacy_effort):
            if effort is not None and not isinstance(effort, str):
                raise ValueError("RPNH reasoning effort must be text or null")
        if (nested_effort is not None and legacy_effort is not None
                and nested_effort != legacy_effort):
            raise ValueError("conflicting thread/start reasoning effort values")
        requested_model = params.get("model")
        model_id = (
            self.default_model_id if requested_model is None else requested_model)
        requested_effort = (
            nested_effort if nested_effort is not None else legacy_effort)
        if requested_effort is None and model_id == self.default_model_id:
            requested_effort = self.default_reasoning_effort
        # Preserve the model-scoped wire-none codec, allowlist and immutable
        # execution identity checks in the existing selection resolver.
        return self._profile_for_selection(model_id, requested_effort)

    async def _start_thread(
            self, websocket: Any, request_id: object,
            params: Mapping[str, Any],
    ) -> None:
        if self._threads or self._lease is not None:
            raise ValueError(
                "RPNH Codex frontend is already bound to its one main session")
        profile = self._start_thread_profile(params)
        self._require_ready_profile(profile)
        raw_cwd = params.get("cwd")
        cwd = (
            Path(raw_cwd).expanduser().resolve()
            if raw_cwd else Path.cwd().resolve())
        lease = MainSessionOwnerLease.reserve_for_creation(self.root)
        self._lease = lease
        try:
            session = MainSession(
                self.root, profile.path, owner_root_reserved=True)
            record = inspect_main_session_root(self.root)
            thread_id = codex_thread_id(stable_frontend_session_id(record))
            state = ThreadState(
                thread_id=thread_id,
                session=session,
                main_turn_control=TaskControl(
                    session.child_path_root / "main-turn-control"),
                model_id=profile.selection_id,
                reasoning_effort=profile.reasoning_effort,
                cwd=cwd,
                created_at=_now_seconds(),
            )
            state.history_binding = MainSessionSourceBinding.capture(session)
            self._threads[thread_id] = state
            self._persist_thread(state)
        except Exception:
            if "thread_id" in locals():
                self._threads.pop(thread_id, None)
            self.close()
            raise
        document = self._thread_document(state)
        await self._result(websocket, request_id, {
            "thread": document,
            "model": state.model_id,
            "modelProvider": "rpnh",
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "sandbox": {"type": "dangerFullAccess"},
            "reasoningEffort": self._selected_effort_to_wire(
                state.model_id, state.reasoning_effort),
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
        requested_effort = params.get("effort")
        model_id = state.model_id if requested_model is None else requested_model
        effort = (
            state.reasoning_effort
            if requested_model is None and requested_effort is None
            else requested_effort)
        profile = self._profile_for_selection(model_id, effort)
        self._require_ready_profile(profile)
        if getattr(
                state.session, "execution_config_path", profile.path
        ) != profile.path:
            state.session.set_execution_config(profile.path)
        state.model_id = profile.selection_id
        state.reasoning_effort = profile.reasoning_effort
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

        # Rebuild the in-memory transcript from Registry authority after every
        # terminal reconciliation. Interrupted turns are live notifications
        # only and never enter conversation history.
        state.turns = self._committed_turns(
            state,
            latest_active=(
                active if reconciliation.state == "committed" else None),
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

    async def _observe_active_turn(
            self, websocket: Any | None, state: ThreadState,
    ) -> None:
        """Observe existing Registry activity without semantic reconciliation."""
        snapshot = state.session.active_turn_snapshot()
        if snapshot is None:
            return
        active = state.active
        if active is None or active.ordinal != snapshot.ordinal:
            active = self._active_from_snapshot(state, snapshot)
            state.active = active
        elif active.task_handle is None:
            active.task_handle = self._live_main_turn_handle(
                state.main_turn_control, snapshot.attempt_path)
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
                "cannot change RPNH provider/model/effort during an active turn")
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
        model_id = params.get("model")
        effort = params.get("effort")
        selected_model = state.model_id if model_id is None else model_id
        selected_effort = (
            state.reasoning_effort
            if model_id is None and effort is None else effort)
        if model_id is not None or effort is not None:
            profile = self._profile_for_selection(
                selected_model, selected_effort)
            self._require_ready_profile(profile)
            state.session.set_execution_config(profile.path)
            state.model_id = profile.selection_id
            state.reasoning_effort = profile.reasoning_effort
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
        model_id = model_edit.get("value")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(
                "RPNH config/batchWrite model value must be a selection ID")
        effort = None if effort_edit is None else effort_edit.get("value")
        profile = self._profile_for_selection(model_id, effort)
        destination = config_path().expanduser().resolve()
        current_version = self._config_version(destination)
        if (expected_version is not None
                and expected_version != current_version):
            raise ValueError("RPNH config/batchWrite expectedVersion is stale")
        save_selected_path(profile.path)
        self.default_model_id = profile.selection_id
        self.default_reasoning_effort = profile.reasoning_effort
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
                    or client.get("version") != self.compatibility_profile.version):
                raise ValueError(
                    "RPNH requires codex-tui "
                    f"{self.compatibility_profile.version} exactly")
            await self._result(websocket, request_id, {
                "userAgent": f"rpnh/codex-compat-{self.compatibility_profile.version}",
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
                    "model_reasoning_effort": self._selected_effort_to_wire(
                        self.default_model_id, self.default_reasoning_effort),
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
            state = self._history_state(websocket, params.get("threadId"))
            # Codex presentation reopen is not semantic paused-turn resume.
            # Preserve existing live reconnect tracking separately from the
            # pure fixed-cut history path below. Tracking may later reconcile.
            await self._observe_active_turn(websocket, state)
            cursors, projection = self._resume_history(state)
            document = self._thread_document(state)
            document["status"] = ({"type": "active", "activeFlags": []}
                                  if projection.active_turn_ref is not None else {"type": "idle"})
            await self._history_result(websocket, request_id, state, {
                "thread": document,
                **cursors,
                **({"collaborationMode": None, "disabledPluginIds": []}
                   if self.compatibility_profile.explicit_history_defaults else {}),
                "model": state.model_id,
                "modelProvider": "rpnh",
                "cwd": str(state.cwd),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {"type": "dangerFullAccess"},
                "reasoningEffort": self._selected_effort_to_wire(
                    state.model_id, state.reasoning_effort),
                "serviceTier": None,
                "instructionSources": [],
            })
        elif method == "thread/loaded/list":
            await self._result(websocket, request_id, {
                "data": list(self._threads), "nextCursor": None})
        elif method == "thread/list":
            states = self._listed_threads(params)
            for state in states:
                self._history_state(websocket, state.thread_id)
            def recheck_list():
                for state in states:
                    if self._history_state(websocket, state.thread_id) is not state:
                        raise ValueError("history owner/source binding is unavailable")
            await self._send(websocket, {"id": request_id, "result": {
                "data": [
                    self._thread_document(state)
                    for state in states
                ],
                "nextCursor": None,
                "backwardsCursor": None,
            }}, recheck=recheck_list, max_bytes=self.history_response_max_bytes)
        elif method == "thread/read":
            state = self._history_state(websocket, params.get("threadId"))
            include_turns = params.get("includeTurns", False)
            if type(include_turns) is not bool:
                raise ValueError("history includeTurns must be boolean")
            await self._history_result(websocket, request_id, state, {
                "thread": self._read_history_thread(state, include_turns)})
        elif method in {"thread/turns/list", "thread/items/list"}:
            state = self._history_state(websocket, params.get("threadId"))
            page = self._history_page(state, params, "turns" if method == "thread/turns/list" else "items")
            await self._history_result(websocket, request_id, state, page)
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
                                    await self._observe_active_turn(
                                        websocket, state)
                            continue
                        await self._handle_request(
                            websocket, request_id, method, params)
                    except (json.JSONDecodeError, OSError, RuntimeError,
                            TypeError, ValueError) as exc:
                        if request_id is not None:
                            message = ("history request rejected" if method in {
                                "thread/resume", "thread/read", "thread/list",
                                "thread/turns/list", "thread/items/list"} else str(exc))
                            await self._error(websocket, request_id, message)
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


def resolve_codex_binary(
        value: str | None = None, *, compatibility_profile: str | None = None,
) -> str:
    profile = _compatibility_profile(compatibility_profile)
    selected = value or os.environ.get("RPNH_CODEX_BIN") or "codex"
    resolved = shutil.which(selected)
    if resolved is None:
        raise CodexCompatibilityError(
            "Codex frontend is not installed; install "
            f"@openai/codex@{profile.version}")
    try:
        completed = subprocess.run(
            [resolved, "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise CodexCompatibilityError("Codex version check timed out") from exc
    reported = completed.stdout.strip()
    if completed.returncode != 0 or reported != profile.reported_cli_version:
        raise CodexCompatibilityError(
            f"RPNH requires {profile.reported_cli_version}; got "
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
        compatibility_profile: str | None = None,
) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise CodexCompatibilityError(
            "Codex frontend requires the Python `websockets` package") from exc
    profile = _compatibility_profile(compatibility_profile)
    binary = (resolve_codex_binary(codex_binary) if compatibility_profile is None else
              resolve_codex_binary(codex_binary, compatibility_profile=profile.name))
    root = root.resolve()
    if resume and not root.is_dir():
        raise CodexCompatibilityError(
            "RPNH Codex resume requires an existing session directory")
    try:
        server = CodexAppServer(root, execution_config_path, compatibility_profile=profile.name)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CodexCompatibilityError(str(exc)) from exc
    if resume and not server._threads:
        server.close()
        raise CodexCompatibilityError(
            "RPNH Codex resume requires one direct main-session root")
    try:
        with tempfile.TemporaryDirectory(prefix="rpnh-codex-socket-") as raw:
            socket_path = Path(raw) / "app.sock"
            with unix_socket_address(
                    socket_path, visible_to_child_process=True) as address:
                async with websockets.unix_serve(server.handle, address):
                    socket_path.chmod(0o600)
                    process = await asyncio.create_subprocess_exec(
                        *codex_frontend_argv(
                            binary, address, server.frontend_model_id))
                    try:
                        return int(await process.wait())
                    finally:
                        await server.stop_active_turns()
    finally:
        server.close()


def run_codex_frontend(
        root: Path, execution_config_path: Path, *,
        codex_binary: str | None = None, resume: bool = False,
        compatibility_profile: str | None = None,
) -> int:
    return asyncio.run(_run_codex_frontend_async(
        root, execution_config_path, codex_binary=codex_binary,
        resume=resume, compatibility_profile=compatibility_profile))


__all__ = (
    "CODEX_CLI_VERSION_TEXT", "CODEX_CONFIG_OVERRIDES",
    "CODEX_DISABLED_FEATURES",
    "CODEX_FRONTEND_VERSION", "CodexAppServer", "CodexCompatibilityError",
    "resolve_codex_binary", "run_codex_frontend",
)
