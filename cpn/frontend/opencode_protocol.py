"""Pinned OpenCode DTO projection. No Registry writer or execution effects here."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from cpn.rpnh.frontend_application import FrontendError, canonical, request_identity, stable_id

@dataclass(frozen=True)
class OpenCodeCompatibilityProfile:
    """An exact presentation target, not a declaration of native certification."""

    version: str
    commit: str
    certification_only: bool


def _load_opencode_profiles() -> tuple[OpenCodeCompatibilityProfile, ...]:
    # This packaged manifest is the only version/commit source. No environment,
    # user config, binary output, or caller-provided manifest changes the set.
    manifest = json.loads(Path(__file__).with_name(
        "opencode_compatibility.v1.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "rpnh/opencode_compatibility/v1":
        raise ValueError("Unsupported OpenCode compatibility manifest")
    upstream = manifest["upstream"]
    profiles = [OpenCodeCompatibilityProfile(
        upstream["package_version"], upstream["commit"], False)]
    for candidate in manifest.get("certification_candidates", []):
        if (candidate["status"] != "certification-only"
                or candidate["production_enabled"] is not False
                or candidate["contract_source_commit"] != upstream["commit"]
                or candidate["native_g2"] != "not-run"
                or candidate["native_g3"] != "not-run"):
            raise ValueError("Invalid OpenCode certification-only metadata")
        profiles.append(OpenCodeCompatibilityProfile(
            candidate["package_version"], candidate["commit"], True))
    for profile in profiles:
        if (not isinstance(profile.version, str)
                or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", profile.version) is None
                or not isinstance(profile.commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", profile.commit) is None):
            raise ValueError("OpenCode profiles require exact versions and commits")
    if len({profile.version for profile in profiles}) != len(profiles):
        raise ValueError("Duplicate OpenCode compatibility version")
    return tuple(profiles)


_OPENCODE_PROFILES = _load_opencode_profiles()
DEFAULT_PROFILE = _OPENCODE_PROFILES[0]
# Backwards-compatible aliases retain the production pin; never monkeypatch
# them to exercise a different client. Tests explicitly resolve a profile.
OPENCODE_VERSION = DEFAULT_PROFILE.version
OPENCODE_COMMIT = DEFAULT_PROFILE.commit


def get_opencode_profile(
        *, certification_version: str | None = None,
) -> OpenCodeCompatibilityProfile:
    if certification_version is None:
        return DEFAULT_PROFILE
    for profile in _OPENCODE_PROFILES[1:]:
        if type(certification_version) is str and certification_version == profile.version:
            return profile
    raise ValueError("Unknown exact OpenCode certification candidate")


def require_opencode_profile(
        profile: OpenCodeCompatibilityProfile,
) -> OpenCodeCompatibilityProfile:
    """Reject forged/mixed targets before a process or application is touched."""
    if (type(profile) is not OpenCodeCompatibilityProfile
            or type(profile.certification_only) is not bool
            or type(profile.version) is not str
            or type(profile.commit) is not str
            or profile not in _OPENCODE_PROFILES):
        raise ValueError("Unregistered OpenCode compatibility profile")
    return profile


COMMANDS = {
    "rpnh-help": "RPNH controls and limitations",
    "rpnh-tasks": "List independent RPNH tasks/workflows",
    "rpnh-task": "ID status|result|stop|checkpoints|resume|reopen CHECKPOINT [:: REASON]|net|message",
    "rpnh-net": "Main-run Petri net [--show-resources|--resources-only]",
    "rpnh-agent": "TEXT: launch an independent RPNH agent",
    "rpnh-workflow": "TEXT: ask the RPNH Designer for a workflow",
    "rpnh-resume": "Resume the paused main Registry checkpoint",
    "rpnh-rollback": "Roll back paused main conversation; retain child Registry",
    "rpnh-send": "UNIQUE_ID TEXT: explicitly identify a new/retried request",
}


@dataclass(frozen=True)
class Reply:
    status: int
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)


def _identity(prefix: str, sid: str, ordinal: int, kind: str, created: int) -> str:
    suffix = stable_id(f"{sid}:{ordinal}:{kind}")[:12]
    order = "0" if kind == "user" else "1"
    return f"{prefix}_{created:013x}{ordinal:08x}{order}_{suffix}"


class OpenCodeProtocol:
    """Exact active-model projection and a fail-closed HTTP route allowlist."""

    def __init__(self, gateway: Any, directory: str, *,
                 profile: OpenCodeCompatibilityProfile = DEFAULT_PROFILE) -> None:
        self._profile = require_opencode_profile(profile)
        self.gateway, self.directory = gateway, directory
        configuration = gateway.call("configuration")
        if (not isinstance(configuration, Mapping)
                or set(configuration) != {
                    "default_selection", "default_reasoning_effort",
                    "profiles"}
                or not isinstance(configuration["default_selection"], str)
                or (configuration["default_reasoning_effort"] is not None
                    and not isinstance(
                        configuration["default_reasoning_effort"], str))
                or not isinstance(configuration["profiles"], list)
                or not configuration["profiles"]):
            raise FrontendError("invalid_catalog", "Invalid RPNH public model projection.", 500)
        profiles = configuration["profiles"]
        if any(
                not isinstance(profile, Mapping)
                or set(profile) != {
                    "selection", "provider", "provider_name", "model",
                    "reasoning_effort", "supported_reasoning_efforts",
                    "default_reasoning_effort", "ready"}
                or any(not isinstance(profile[key], str) or not profile[key]
                       for key in ("selection", "provider", "provider_name", "model"))
                or (profile["reasoning_effort"] is not None
                    and not isinstance(profile["reasoning_effort"], str))
                or not isinstance(profile["supported_reasoning_efforts"], list)
                or any(not isinstance(effort, str) or not effort
                       for effort in profile["supported_reasoning_efforts"])
                or len(set(profile["supported_reasoning_efforts"]))
                != len(profile["supported_reasoning_efforts"])
                or (profile["default_reasoning_effort"] is not None
                    and profile["default_reasoning_effort"]
                    not in profile["supported_reasoning_efforts"])
                or not isinstance(profile["ready"], bool)
                for profile in profiles):
            raise FrontendError("invalid_catalog", "Invalid RPNH public model projection.", 500)
        self.profiles = {profile["selection"]: dict(profile) for profile in profiles}
        if (len(self.profiles) != len(profiles)
                or configuration["default_selection"] not in self.profiles):
            raise FrontendError("invalid_catalog", "Invalid RPNH public model projection.", 500)
        self.default_selection = configuration["default_selection"]
        self.default_reasoning_effort = configuration[
            "default_reasoning_effort"]
        self._condition = threading.Condition()
        self._revision, self._sequence = 0, 0
        self._epoch = uuid4().hex
        self._closed = False
        self._observations: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=20))

    @property
    def profile(self) -> OpenCodeCompatibilityProfile:
        return self._profile

    def notify(self) -> None:
        with self._condition:
            self._revision += 1
            self._condition.notify_all()

    def revision(self) -> int:
        with self._condition:
            return self._revision

    def wait(self, previous: int, timeout: float = 15.0) -> bool:
        with self._condition:
            self._condition.wait_for(lambda: self._closed or self._revision != previous, timeout)
            return not self._closed

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _views(self) -> list[dict[str, Any]]:
        return self.gateway.call("snapshot")

    def _view(self, sid: str) -> dict[str, Any]:
        for view in self._views():
            if view["id"] == sid:
                return view
        raise FrontendError("unknown_session", "Unknown RPNH session.", 404)

    def _selection(
            self, body: Mapping[str, Any], *, fallback: str,
            fallback_effort: str | None = None,
            command: bool = False, create: bool = False,
    ) -> tuple[str, str | None]:
        if body.get("agent", "rpnh") != "rpnh":
            raise FrontendError("unsupported_agent", "Only the RPNH main agent is available.", 400)
        variant = body.get("variant")
        if variant == "":
            variant = None
        requested = body.get("model")
        if requested is None:
            selection = fallback
            valid = selection in self.profiles
        elif command:
            prefix = "rpnh/"
            selection = requested.removeprefix(prefix) if isinstance(requested, str) else ""
            valid = isinstance(requested, str) and requested.startswith(prefix)
        else:
            model_key = "id" if create else "modelID"
            if isinstance(requested, dict):
                requested = dict(requested)
                nested_variant = requested.pop("variant", None) if create else None
                if nested_variant == "":
                    nested_variant = None
                if (nested_variant is not None and variant is not None
                        and nested_variant != variant):
                    raise FrontendError(
                        "conflicting_effort",
                        "Model and request reasoning efforts differ.", 400)
                if nested_variant is not None:
                    variant = nested_variant
                selection = requested.get(model_key, "")
                valid = requested == {"providerID": "rpnh", model_key: selection}
            else:
                selection = ""
                valid = False
        if not valid or selection not in self.profiles:
            raise FrontendError("selection_drift", "Unknown RPNH provider/model selection.")
        profile = self.profiles[selection]
        effort = fallback_effort if variant is None else variant
        if requested is not None and variant is None:
            effort = profile["default_reasoning_effort"]
        supported = profile["supported_reasoning_efforts"]
        if (effort is not None and not isinstance(effort, str)):
            raise FrontendError(
                "invalid_effort", "Reasoning effort must be text.", 400)
        if ((effort is None and supported)
                or (effort is not None and effort not in supported)):
            raise FrontendError(
                "unsupported_effort",
                "Unknown reasoning effort for this RPNH model.", 400)
        return selection, effort

    def _provider(self) -> dict[str, Any]:
        modalities = {"text": True, "audio": False, "image": False, "video": False, "pdf": False}
        models = {}
        for selection, profile in self.profiles.items():
            # Required numeric fields have no nullable alternative in the pinned
            # DTO. Zero is an UNKNOWN display sentinel, never proof of free use.
            models[selection] = {
                "id": selection, "providerID": "rpnh",
                "api": {"id": selection, "url": "", "npm": ""},
                "name": f"{profile['model']} ({profile['provider_name']})",
                "capabilities": {"temperature": False,
                    "reasoning": bool(profile["supported_reasoning_efforts"]),
                    "attachment": False, "toolcall": False, "input": modalities,
                    "output": dict(modalities), "interleaved": False},
                "cost": {"input": 0, "output": 0,
                         "cache": {"read": 0, "write": 0}},
                "limit": {"context": 0, "output": 0}, "status": "active",
                "options": {"rpnh_display_only": True,
                            "rpnh_metrics": "unavailable",
                            "rpnh_provider": profile["provider"],
                            "rpnh_exact_model": profile["model"],
                            "rpnh_default_reasoning_effort": profile[
                                "default_reasoning_effort"],
                            "rpnh_ready": profile["ready"]},
                "headers": {}, "release_date": "", "variants": {
                    effort: {} for effort in profile[
                        "supported_reasoning_efforts"]}}
        return {"id": "rpnh", "name": "RPNH profiles", "source": "config",
                "env": [], "options": {}, "models": models}

    def session_document(self, view: Mapping[str, Any]) -> dict[str, Any]:
        first = view["turns"][0]["text"] if view["turns"] else "RPNH session"
        selection = view["model"]["selection"]
        return {"id": view["id"], "slug": view["id"], "projectID": "rpnh", "directory": self.directory,
                "title": first[:120], "agent": "rpnh", "version": self.profile.version,
                "model": {"providerID": "rpnh", "id": selection},
                "time": {"created": view["created"], "updated": view["updated"]},
                "metadata": {"rpnh_authority": "Registry", "rpnh_metrics": "unavailable",
                             "rpnh_permission": "unavailable", "rpnh_state_error": view.get("error"),
                             "rpnh_provider": view["model"]["provider"],
                             "rpnh_exact_model": view["model"]["model"],
                             "rpnh_reasoning_effort": view["model"].get(
                                 "reasoning_effort")}}

    @staticmethod
    def session_status(view: Mapping[str, Any]) -> dict[str, str]:
        active = {"accepted", "pending_start", "running", "terminal"}
        return {"type": "busy" if any(t["state"] in active for t in view["turns"]) else "idle"}

    def _assistant(
            self, sid: str, aid: str, uid: str, created: int,
            selection: str, *, model_evidence: str | None = None,
    ) -> dict[str, Any]:
        result = {"id": aid, "sessionID": sid, "role": "assistant", "parentID": uid,
                "time": {"created": created}, "modelID": selection, "providerID": "rpnh",
                "agent": "rpnh", "mode": "rpnh", "path": {"cwd": self.directory, "root": self.directory},
                "cost": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}}
        if model_evidence is not None:
            result["metadata"] = {
                "rpnh_turn_model_evidence": model_evidence,
                "rpnh_turn_model_display_basis": (
                    "registered_frontend_request"
                    if model_evidence in {
                        "frontend-request/v2", "frontend-request/v3"} else
                    "session_current_selection"),
            }
        return result

    @staticmethod
    def _message_ids(sid: str, turn: Mapping[str, Any]) -> tuple[str, str]:
        ordinal, created = turn["ordinal"], turn["created"]
        uid = _identity("msg", sid, ordinal, "user", created)
        aid = _identity("msg", sid, ordinal, "assistant", created)
        key = turn.get("key") or ""
        if key.startswith("explicit:msg_"):
            uid = key.removeprefix("explicit:")
            aid = uid + ".rpnh-answer"
        return uid, aid

    def _message_pair(self, sid: str, turn: Mapping[str, Any]) -> list[dict[str, Any]]:
        ordinal, created = turn["ordinal"], turn["created"]
        uid, aid = self._message_ids(sid, turn)
        selection = turn["model"]["selection"]
        model_evidence = turn.get("model_evidence", "unavailable")
        model_metadata = {
            "rpnh_turn_model_evidence": model_evidence,
            "rpnh_turn_model_display_basis": (
                "registered_frontend_request"
                if model_evidence in {
                    "frontend-request/v2", "frontend-request/v3"} else
                "session_current_selection"),
        }
        user = {"id": uid, "sessionID": sid, "role": "user", "time": {"created": created}, "agent": "rpnh",
                "model": {"providerID": "rpnh", "modelID": selection},
                "metadata": dict(model_metadata)}

        def part(mid: str, kind: str, text: str, synthetic: bool = False) -> dict[str, Any]:
            return {"id": _identity("prt", sid, ordinal, kind, created), "sessionID": sid,
                    "messageID": mid, "type": "text", "text": text, "synthetic": synthetic,
                    "metadata": {"rpnh_turn_ref": turn["turn_ref"], "rpnh_metrics": "unavailable",
                                 **model_metadata}}

        messages = [{"info": user, "parts": [part(uid, "user", turn["text"])]}]
        answer = turn.get("answer")
        failed = turn["state"] in {"stopped_by_owner", "failed", "interrupted"}
        if answer is None and not failed:
            return messages
        info = self._assistant(
            sid, aid, uid, created, selection,
            model_evidence=model_evidence)
        if answer is not None:
            if turn["state"] != "committed" or not isinstance(answer.get("reply"), str):
                raise FrontendError("invalid_terminal", "An answer lacks committed main-turn evidence.", 500)
            info["finish"] = "stop"
            text = answer["reply"]
            if answer.get("protocol_valid") is False:
                text += "\n[RPNH main returned prose; no valid child-task decision was accepted.]"
            for child in turn.get("children", ()):
                text += f"\n[RPNH child registered: {child['task_id']} ({child['kind']}); this is not terminal success.]"
            synthetic = False
        else:
            text = ("RPNH paused at its Registry checkpoint. Use /rpnh-resume or /rpnh-rollback."
                    if turn["state"] == "stopped_by_owner" else
                    "RPNH has no committed answer for this turn. Its child Registry was retained.")
            info["error"] = {"name": "UnknownError" if turn["state"] == "failed" else "MessageAbortedError",
                             "data": {"message": text}}
            synthetic = True
        info["time"]["completed"] = turn["updated"]
        messages.append({"info": info, "parts": [part(aid, "assistant", text, synthetic)]})
        return messages

    def messages(self, view: Mapping[str, Any]) -> list[dict[str, Any]]:
        result = [m for t in view["turns"] if t["state"] != "interrupted" for m in self._message_pair(view["id"], t)]
        with self._condition:
            result.extend(list(self._observations.get(view["id"], ())))
        # Stable sorting preserves Registry turn order, and user-before-answer,
        # when publication timestamps coincide or client IDs are non-temporal.
        return sorted(result, key=lambda m: m["info"]["time"]["created"])

    def events(self) -> list[dict[str, Any]]:
        """Replace current message/part snapshots, never replay output deltas/effects."""
        events = []
        revision = self.revision()
        for view in self._views():
            sid = view["id"]
            events.append({"type": "session.updated", "properties": {"sessionID": sid, "info": self.session_document(view)}})
            for message in self.messages(view):
                events.append({"type": "message.updated", "properties": {"sessionID": sid, "info": message["info"]}})
                for part in message["parts"]:
                    part_time = message["info"]["time"].get(
                        "completed", message["info"]["time"]["created"])
                    events.append({"type": "message.part.updated", "properties": {
                        "sessionID": sid, "part": part, "time": part_time}})
            for turn in view["turns"]:
                if turn["state"] == "interrupted":
                    for message in self._message_pair(sid, turn):
                        events.append({"type": "message.removed", "properties": {
                            "sessionID": sid, "messageID": message["info"]["id"]}})
                elif turn["state"] in {"accepted", "pending_start", "running", "terminal"} and turn.get("answer") is None:
                    # Remove a previously paused diagnostic on explicit resume.
                    events.append({"type": "message.removed", "properties": {"sessionID": sid,
                        "messageID": self._message_ids(sid, turn)[1]}})
            if view.get("error"):
                events.append({"type": "session.error", "properties": {"sessionID": sid,
                    "error": {"name": "UnknownError", "data": {
                        "message": "RPNH reconciliation is required; no successful completion is asserted."}}}})
            events.append({"type": "session.status", "properties": {"sessionID": sid, "status": self.session_status(view)}})
        return [self.envelope(e, revision) for e in events]

    def envelope(self, event: Mapping[str, Any], revision: int | None = None) -> dict[str, Any]:
        revision = self.revision() if revision is None else revision
        identity = stable_id(canonical({"epoch": self._epoch, "revision": revision, "event": dict(event)}))
        return {"directory": self.directory, "payload": {**event, "id": "evt_" + identity}}

    def _observation(self, sid: str, name: str, result: Any) -> dict[str, Any]:
        now = time.time_ns() // 1_000_000
        with self._condition:
            self._sequence += 1
            sequence = self._sequence
        aid = _identity("msg", sid, sequence, "observation", now)
        view = self._view(sid)
        existing = self.messages(view)
        parent = next((m["info"]["id"] for m in reversed(existing) if m["info"]["role"] == "user"), "msg_rpnh_observation")
        info = self._assistant(
            sid, aid, parent, now, view["model"]["selection"])
        info["mode"], info["time"]["completed"] = "rpnh-observation", now
        text = "RPNH command observation — not an agent answer or terminal proof.\n" + json.dumps(
            result, ensure_ascii=False, indent=2, allow_nan=False)
        if len(text) > 131072:
            text = text[:131072] + "\n[Display truncated; use the RPNH basic frontend for the complete observation.]"
        message = {"info": info, "parts": [{"id": "prt_" + aid[4:], "sessionID": sid, "messageID": aid,
            "type": "text", "text": text, "synthetic": True,
            "metadata": {"rpnh_observation": name, "rpnh_persistent": False, "rpnh_metrics": "unavailable"}}]}
        with self._condition:
            self._observations[sid].append(message)
        self.notify()
        return message

    def route(self, method: str, target: str, body: Any = None,
              headers: Mapping[str, str] | None = None) -> Reply:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise FrontendError("invalid_target", "Only local origin-form requests are accepted.", 400)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(v) != 1 for v in query.values()):
            raise FrontendError("ambiguous_query", "Duplicate query parameters are not supported.", 400)
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        workspace = query.get("workspace", [headers.get("x-opencode-workspace", "")])[0]
        directory = query.get("directory", [unquote(headers.get("x-opencode-directory", self.directory))])[0]
        if workspace or directory not in {"", self.directory}:
            raise FrontendError("unsupported_location", "This attach is scoped to its isolated display directory.", 400)
        if method == "GET":
            return self._get_route(path, query)
        if method != "POST":
            raise FrontendError("unsupported_by_rpnh_frontend", "This write operation is not supported by RPNH.", 405)
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise FrontendError("invalid_body", "A JSON object is required.", 400)
        if path == "/session":
            if set(body) - {"agent", "model", "variant"}:
                raise FrontendError("unsupported_session_options", "Session options are controlled by RPNH.", 400)
            selection, effort = self._selection(
                body, fallback=self.default_selection,
                fallback_effort=self.default_reasoning_effort, create=True)
            sid = self.gateway.call(
                "create_session", selection,
                reasoning_effort=effort)
            self.notify()
            return Reply(200, self.session_document(self._view(sid)))
        match = re.fullmatch(r"/session/(ses_[0-9a-f]{32})/(message|prompt_async|abort|command)", path)
        if not match:
            raise FrontendError("unsupported_by_rpnh_frontend", "OpenCode-native effects are disabled; use RPNH commands.", 501)
        sid, operation = match.groups()
        self._view(sid)
        if operation == "abort":
            if body:
                raise FrontendError("invalid_abort", "Abort accepts no overrides.", 400)
            result = self.gateway.call("abort", sid)
            self.notify()
            return Reply(200, result)
        if operation == "command":
            allowed = {"command", "arguments", "agent", "model", "variant", "parts", "messageID"}
            if set(body) - allowed or body.get("parts") not in (None, []):
                raise FrontendError("unsupported_command_input", "Command attachments and overrides are unavailable.", 400)
            name, arguments = body.get("command"), body.get("arguments", "")
            if not isinstance(name, str) or name not in COMMANDS or not isinstance(arguments, str):
                raise FrontendError("unsupported_command", "Only advertised RPNH commands are accepted.", 400)
            view = self._view(sid)
            selection, effort = self._selection(
                body, fallback=view["model"]["selection"],
                fallback_effort=view["model"].get("reasoning_effort"),
                command=True)
            identity_model = {
                "provider": "rpnh", "selection": selection,
                "reasoning_effort": effort}
            key = request_identity(
                name + "\n" + arguments, None, identity_model,
                self._supplied_id(body, headers))
            result = self.gateway.call(
                "command", sid, name, arguments, key,
                selection_id=selection, reasoning_effort=effort)
            return Reply(200, self._observation(sid, name, result), {"X-RPNH-Submission-Key": key})
        allowed = {"parts", "messageID", "model", "agent", "variant", "noReply", "format"}
        if set(body) - allowed or (body.get("noReply") is not None and body.get("noReply") is not False):
            raise FrontendError("unsupported_prompt_options", "Tools/system/runtime overrides are not accepted.", 400)
        if body.get("format") not in (None, {"type": "text"}):
            raise FrontendError("unsupported_format", "Only plain text is supported.", 400)
        view = self._view(sid)
        selection, effort = self._selection(
            body, fallback=view["model"]["selection"],
            fallback_effort=view["model"].get("reasoning_effort"))
        parts = body.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
            raise FrontendError("invalid_parts", "Nonempty text parts are required.", 400)
        texts = []
        for part in parts:
            if (not isinstance(part, dict) or part.get("type") != "text" or set(part) - {"type", "text", "id"}
                    or not isinstance(part.get("text"), str)):
                raise FrontendError("unsupported_part", "Only plain text is supported; attachments need RPNH intake.", 400)
            texts.append(part["text"])
        text = "\n".join(texts)
        if not text.strip() or len(text) > 131072:
            raise FrontendError("invalid_text", "Text must be nonempty and at most 131072 characters.", 400)
        if text.lstrip().startswith("/"):
            raise FrontendError("unsupported_command", "Use an advertised RPNH command, not a native slash command.", 400)
        identity_model = {
            "provider": "rpnh", "selection": selection,
            "reasoning_effort": effort}
        key = request_identity(
            text, None, identity_model, self._supplied_id(body, headers))
        ordinal = self.gateway.call(
            "submit", sid, text, key, selection_id=selection,
            reasoning_effort=effort)
        self.notify()
        turn = next(t for t in self._view(sid)["turns"] if t["ordinal"] == ordinal)
        # The pinned endpoint requires an AssistantMessage response. Until a
        # Registry answer commits, return only its in-progress identity; the
        # TUI receives the durable answer and parts through snapshot events.
        messages = self._message_pair(sid, turn)
        if len(messages) == 2:
            message = messages[-1]
        else:
            uid, aid = self._message_ids(sid, turn)
            message = {
                "info": self._assistant(
                    sid, aid, uid, turn["created"],
                    turn["model"]["selection"],
                    model_evidence=turn.get("model_evidence", "unavailable")),
                "parts": [],
            }
        return Reply(204 if operation == "prompt_async" else 200,
                     None if operation == "prompt_async" else message,
                     {"X-RPNH-Submission-Key": key, "X-RPNH-Turn": str(ordinal)})

    @staticmethod
    def _supplied_id(body: Mapping[str, Any], headers: Mapping[str, str]) -> str | None:
        message_id, header_id = body.get("messageID"), headers.get("idempotency-key")
        if message_id is not None and header_id is not None and message_id != header_id:
            raise FrontendError("ambiguous_identity", "messageID and Idempotency-Key must agree.", 400)
        return message_id if message_id is not None else header_id

    def _get_route(self, path: str, query: Mapping[str, list[str]]) -> Reply:
        provider = self._provider()
        defaults = {"rpnh": self.default_selection}
        if path in {"/lsp", "/formatter", "/permission", "/question", "/experimental/workspace", "/experimental/workspace/status"}:
            return Reply(200, [])
        if path in {"/mcp", "/experimental/resource", "/provider/auth"}:
            return Reply(200, {})
        if path == "/global/event":
            return Reply(200)  # The HTTP transport handles this as SSE.
        if path in {"/global/health", "/health"}:
            return Reply(200, {"healthy": True, "version": self.profile.version})
        if path == "/experimental/capabilities":
            return Reply(200, {"backgroundSubagents": False})
        if path == "/path":
            return Reply(200, {k: self.directory for k in ("home", "state", "config", "worktree", "directory")})
        if path == "/project/current":
            return Reply(200, {"id": "rpnh", "worktree": self.directory, "name": "RPNH",
                               "time": {"created": 0, "updated": 0}, "sandboxes": []})
        if path == "/project/rpnh/directories":
            return Reply(200, [{"directory": self.directory}])
        if path == "/config/providers":
            return Reply(200, {"providers": [provider], "default": defaults})
        if path == "/provider":
            return Reply(200, {"all": [provider], "default": defaults, "connected": ["rpnh"]})
        if path in {"/config", "/global/config"}:
            return Reply(200, {"model": f"rpnh/{self.default_selection}", "default_agent": "rpnh",
                               "share": "disabled", "autoupdate": False, "plugin": [], "mcp": {}})
        if path == "/agent":
            return Reply(200, [{"name": "rpnh", "mode": "primary", "native": False, "hidden": False,
                "description": "RPNH-owned execution; OpenCode is presentation only", "permission": [],
                "model": {"providerID": "rpnh", "modelID": self.default_selection},
                "options": {"rpnh_permission": "unavailable"}}])
        if path == "/command":
            return Reply(200, [{"name": n, "description": d, "template": "$ARGUMENTS", "hints": [], "agent": "rpnh"}
                               for n, d in COMMANDS.items()])
        if path == "/vcs":
            return Reply(200, {"branch": ""})
        if path == "/session/status":
            return Reply(200, {v["id"]: self.session_status(v) for v in self._views()})
        if path == "/session":
            docs = [self.session_document(v) for v in sorted(self._views(), key=lambda v: (v["updated"], v["id"]), reverse=True)]
            if "search" in query:
                docs = [d for d in docs if query["search"][0].casefold() in d["title"].casefold()]
            if "start" in query:
                docs = [d for d in docs if d["time"]["updated"] >= self._integer(query["start"][0])]
            if "limit" in query:
                docs = docs[:self._integer(query["limit"][0], maximum=1000)]
            return Reply(200, docs)
        match = re.fullmatch(r"/session/(ses_[0-9a-f]{32})(?:/(message|todo|diff|children)(?:/(msg_[A-Za-z0-9._:-]+))?)?", path)
        if match:
            sid, operation, message_id = match.groups()
            view = self._view(sid)
            if operation is None:
                return Reply(200, self.session_document(view))
            if operation in {"todo", "diff", "children"} and message_id is None:
                return Reply(200, [])
            if operation != "message":
                raise FrontendError("unsupported_read", "Unsupported projection.", 404)
            messages = self.messages(view)
            if message_id is not None:
                for message in messages:
                    if message["info"]["id"] == message_id:
                        return Reply(200, message)
                raise FrontendError("unknown_message", "Unknown registered message.", 404)
            if "before" in query:
                indices = [i for i, m in enumerate(messages) if m["info"]["id"] == query["before"][0]]
                if not indices:
                    raise FrontendError("invalid_cursor", "Unknown message cursor.", 400)
                messages = messages[:indices[0]]
            if "limit" in query:
                limit = self._integer(query["limit"][0], maximum=1000)
                messages = messages[-limit:] if limit else []
            return Reply(200, messages)
        raise FrontendError("unsupported_read", "This OpenCode projection is unavailable.", 404)

    @staticmethod
    def _integer(value: str, *, maximum: int = 2**63 - 1) -> int:
        if not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > maximum:
            raise FrontendError("invalid_query", "Query value is outside the supported integer range.", 400)
        return int(value)


def encode_sse(value: Mapping[str, Any]) -> bytes:
    encoded = canonical(value)
    return f"id: {stable_id(encoded)}\ndata: {encoded}\n\n".encode("utf-8")
