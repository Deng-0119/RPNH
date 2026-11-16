"""Explicit OpenCode v2 frontend candidates; Registry remains the authority.

2.2.5 retains the earlier REST subset. 2.2.6 adds a local native-client
candidate with read-only, reconstructible display events and REST recovery.
Neither candidate is native-client or real-provider certification.
"""
from __future__ import annotations

import json
from math import isfinite
import os
import time
from uuid import uuid4
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from cpn.rpnh.frontend_application import FrontendError, request_identity
from .opencode_protocol import OpenCodeProtocol, Reply, get_opencode_profile, require_opencode_profile

CANDIDATE_VERSION = "2.2.5"
CANDIDATE_VERSIONS = ("2.2.5", "2.2.6")


class _OpenCodeV2RestProtocol(OpenCodeProtocol):
    basic_username = "opencode"
    event_path = "/api/event"

    def __init__(self, gateway, directory: str, *, profile):
        require_opencode_profile(profile)
        version = getattr(profile, "version", None)
        if (version not in CANDIDATE_VERSIONS
                or profile != get_opencode_profile(certification_version=version)):
            raise ValueError(
                "An exact registered OpenCode v2 candidate profile is required")
        super().__init__(gateway, directory, profile=profile)

    @staticmethod
    def _ids(sid, turn):
        # Existing request_identity forbids '/', while upstream SessionMessageID
        # requires only a msg_ prefix. Keep explicit user IDs verbatim and give projected IDs a
        # disjoint, reversible namespace from existing session/turn references.
        # No hash, identity store, or new execution fact is introduced.
        base = f"msg_rpnh/{sid}/{turn['ordinal']}"
        key = turn.get("key") or ""
        uid = key.removeprefix("explicit:") if key.startswith("explicit:") else base + "/user"
        if not uid.startswith("msg_") or (key.startswith("explicit:") and "/" in uid):
            raise FrontendError("unsupported_history_identity",
                                "Existing user identity cannot be preserved in the v2 message namespace.", 409)
        return uid, base + "/assistant"

    def session_document(self, view):
        return {
            "id": view["id"], "projectID": "rpnh", "agent": "rpnh",
            "model": {"providerID": "rpnh", "id": view["model"]["selection"],
                      **({"variant": view["model"]["reasoning_effort"]}
                         if view["model"].get("reasoning_effort") else {})},
            "title": (view["turns"][0]["text"] if view["turns"] else "RPNH session")[:120],
            "location": {"directory": self.directory},
            "time": {"created": view["created"], "updated": view["updated"]},
            "cost": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0,
                                   "cache": {"read": 0, "write": 0}},
            "metadata": {"rpnh_metrics": "unavailable_zero_sentinel",
                         "rpnh_authority": "Registry", "rpnh_candidate": True,
                         "rpnh_live_message_events": "unavailable",
                         "rpnh_state_error": view.get("error")},
        }

    def messages_v2(self, view):
        result = []
        for turn in view["turns"]:
            if turn["state"] == "interrupted":
                continue
            uid, aid = self._ids(view["id"], turn)
            # Reuse the established safe answer/paused projection; never expose
            # raw model/provider output or invent an answer for an active turn.
            pair = self._message_pair(view["id"], turn)
            model_metadata = dict(pair[0]["info"]["metadata"])
            result.append({"id": uid, "type": "user", "text": turn["text"],
                           "time": {"created": turn["created"]}, "metadata": model_metadata})
            if len(pair) == 1:
                continue
            legacy = pair[-1]
            info = legacy["info"]
            message = {"id": aid, "type": "assistant", "agent": "rpnh",
                       "model": {"providerID": "rpnh", "id": turn["model"]["selection"],
                                 **({"variant": turn["model"]["reasoning_effort"]}
                                    if turn["model"].get("reasoning_effort") else {})},
                       "time": dict(info["time"]),
                       "content": [{"type": "text", "text": p["text"]} for p in legacy["parts"]],
                       "metadata": {"rpnh_turn_ref": turn["turn_ref"],
                                    "rpnh_metrics": "unavailable", **model_metadata}}
            result.append(message)
        return result

    def connected_event(self):
        return {"id": f"evt_{self._epoch}_connected", "type": "server.connected",
                "data": {}, "location": {"directory": self.directory}}

    def events(self):
        # SessionStatusUpdated is explicitly non-durable in the fixed schema.
        # A Registry ordinal is NOT an upstream aggregate stream sequence.
        return [{"id": f"evt_{self._epoch}_{self.revision()}_{v['id']}",
                 "type": "session.status", "created": v["updated"],
                 "location": {"directory": self.directory},
                 "data": {"sessionID": v["id"], "status": self.session_status(v)}}
                for v in self._views()]

    def _page(self, rows, query):
        if set(query) - {"limit", "order"}:
            raise FrontendError("unsupported_query", "Only bounded limit/order projection is available; no native cursor.", 400)
        order = query.get("order", ["asc"])[0]
        if order not in ("asc", "desc"):
            raise FrontendError("invalid_order", "Expected asc or desc.", 400)
        limit = self._integer(query.get("limit", ["1000"])[0], maximum=1000)
        ordered = sorted(rows, key=lambda row: row["time"]["created"], reverse=order == "desc")
        # Do not advertise absent pagination as complete history.
        if len(ordered) > limit:
            raise FrontendError("projection_too_large", "Candidate cannot supply a complete page with native cursors.", 413)
        return {"data": ordered, "cursor": {"previous": None, "next": None}}

    def route(self, method: str, target: str, body: Any = None,
              headers: Mapping[str, str] | None = None) -> Reply:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise FrontendError("invalid_target", "Only origin-form requests are accepted.", 400)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise FrontendError("ambiguous_query", "Duplicate query fields.", 400)
        path = parsed.path
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        if headers.get("x-opencode-workspace") or headers.get("x-opencode-directory", self.directory) != self.directory:
            raise FrontendError("unsupported_location", "Only the isolated RPNH display directory is available.", 400)
        match = re.fullmatch(r"/api/session/(ses_[0-9a-f]{32})(?:/(prompt|interrupt|message|model))?", path)
        if method == "GET":
            if path == "/api/session":
                return Reply(200, self._page([self.session_document(v) for v in self._views()], query))
            if match and match[2] == "message":
                return Reply(200, self._page(self.messages_v2(self._view(match[1])), query))
            if query:
                raise FrontendError("unsupported_query", "This read takes no query options.", 400)
            if path == "/api/info":
                return Reply(200, {"version": self.profile.version, "pid": os.getpid(), "urls": [],
                                   "paths": {"tmp": self.directory}, "capabilities": {"persistentPty": False}})
            if path == self.event_path:
                return Reply(200)
            if path == "/api/session/active":
                return Reply(200, {"data": {v["id"]: {"type": "running"} for v in self._views()
                                           if self.session_status(v)["type"] == "busy"}})
            if match and match[2] is None:
                return Reply(200, {"data": self.session_document(self._view(match[1]))})
            raise FrontendError(
                "unsupported_read",
                f"Not implemented by the explicit RPNH {self.profile.version} candidate.",
                404)
        if method != "POST" or query:
            raise FrontendError("unsupported_operation", "Native effects/query overrides are unavailable.", 405)
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise FrontendError("invalid_body", "Expected a JSON object.", 400)
        if path == "/api/session":
            if set(body) - {"agent", "model", "location"}:
                raise FrontendError("unsupported_create", "IDs, parentage, metadata and permissions remain RPNH-owned.", 400)
            if body.get("location", {"directory": self.directory}) != {"directory": self.directory}:
                raise FrontendError("unsupported_location", "Only the isolated display directory is available.", 400)
            selection, effort = self._selection(body, fallback=self.default_selection,
                                                fallback_effort=self.default_reasoning_effort, create=True)
            sid = self.gateway.call("create_session", selection, reasoning_effort=effort)
            self.notify()
            return Reply(200, {"data": self.session_document(self._view(sid))})
        if not match or match[2] not in {"prompt", "interrupt", "model"}:
            raise FrontendError("unsupported_operation", "Native execution, storage and tools are unavailable.", 501)
        sid, operation = match.groups()
        view = self._view(sid)
        if operation == "model":
            # The native TUI confirms its selection before every prompt.
            # A same-selection confirmation is presentation-only. Model changes
            # need the existing RPNH selection path, not a second frontend state.
            if set(body) != {"model"} or not isinstance(body.get("model"), dict):
                raise FrontendError("invalid_model_confirmation",
                                    "Model confirmation requires only a model.", 400)
            selection, effort = self._selection(
                body, fallback=view["model"]["selection"],
                fallback_effort=view["model"].get("reasoning_effort"), create=True)
            if (selection, effort) != (
                    view["model"]["selection"], view["model"].get("reasoning_effort")):
                raise FrontendError(
                    "unsupported_model_change",
                    "This candidate confirms the current RPNH model only; select changes through RPNH.", 409)
            return Reply(204)
        if operation == "interrupt":
            if body:
                raise FrontendError("invalid_interrupt", "Interrupt accepts no overrides.", 400)
            interrupted = self.gateway.call("abort", sid)
            self.notify()
            return Reply(200, {"interrupted": interrupted})
        allowed = {"id", "text", "files", "agents", "skills", "metadata", "delivery", "resume"}
        if (set(body) - allowed
                or any(body.get(key, []) != [] for key in ("files", "agents", "skills"))
                or body.get("metadata", {}) != {}
                or body.get("delivery") not in (None, "steer")
                or (body.get("resume") is not None and body.get("resume") is not False)):
            raise FrontendError("unsupported_prompt", "Attachments, metadata, queue delivery, resume and runtime overrides are unavailable.", 400)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 131072 or text.lstrip().startswith("/"):
            raise FrontendError("invalid_text", "Nonempty plain text is required; native slash commands are unavailable.", 400)
        selection, effort = view["model"]["selection"], view["model"].get("reasoning_effort")
        identity = self._supplied_id({"messageID": body.get("id")}, headers)
        key = request_identity(text, None, {"provider": "rpnh", "selection": selection,
                                            "reasoning_effort": effort}, identity)
        if identity is not None and not identity.startswith("msg_"):
            raise FrontendError("invalid_request_id", "Message identity must start with msg_.", 400)
        # A plain/steer input admits one ordinary RPNH turn. The application
        # resolves identical-ID retries before enforcing its existing busy rule;
        # do not pre-check busy here or implement native steering/queueing.
        ordinal = self.gateway.call("submit", sid, text, key, selection_id=selection, reasoning_effort=effort)
        self.notify()
        turn = next(t for t in self._view(sid)["turns"] if t["ordinal"] == ordinal)
        return Reply(200, {"data": {"id": self._ids(sid, turn)[0], "sessionID": sid,
                                    "time": {"created": turn["created"]}, "type": "user",
                                    "payload": {"text": turn["text"]}, "delivery": "steer"}},
                     {"X-RPNH-Submission-Key": key, "X-RPNH-Turn": str(ordinal)})


class OpenCodeV2Protocol(_OpenCodeV2RestProtocol):
    """2.2.6 wire projection over the existing RPNH application.

    A projected event sequence is a display DTO, not an OpenCode event store,
    Registry sequence, task receipt, or replay authority. It is rebuilt from
    Registry views; revision/reconnect never submits or resumes work.
    """

    @property
    def differential_events(self):
        return self.profile.version == "2.2.6"

    def _ids(self, sid, turn):
        if not self.differential_events:
            return super()._ids(sid, turn)
        base = f"msg_rpnh/{sid}/{turn['ordinal']}"
        key = turn.get("key") or ""
        explicit = key.removeprefix("explicit:") if key.startswith("explicit:") else None
        # Preserve native OpenCode IDs. Other frontend request identities stay
        # unchanged in Registry and receive a reversible display-only name.
        uid = (explicit if isinstance(explicit, str)
               and re.fullmatch(r"msg_[A-Za-z0-9._:-]{0,156}", explicit) else base + "/user")
        return uid, base + "/assistant"

    @staticmethod
    def _model_ref(model):
        return {"providerID": "rpnh", "id": model["selection"],
                **({"variant": model["reasoning_effort"]}
                   if model.get("reasoning_effort") else {})}

    @staticmethod
    def _tokens():
        return {"input": 0, "output": 0, "reasoning": 0,
                "cache": {"read": 0, "write": 0}}

    @staticmethod
    def _outcome(turn):
        if turn["state"] == "committed":
            return "succeeded"
        if turn["state"] == "failed":
            return "failed"
        if turn["state"] in {"stopped_by_owner", "interrupted"}:
            return "interrupted"
        return None

    @staticmethod
    def _event_id(sid, turn, stage):
        # A reversible presentation namespace, disjoint from explicit input IDs.
        return f"evt_rpnh/{sid}/{turn['ordinal']}/{stage}"

    @staticmethod
    def _turn_metadata(turn):
        evidence = turn.get("model_evidence", "unavailable")
        return {
            "rpnh_projection": "registry-view/display-v1",
            "rpnh_authority": "Registry",
            "rpnh_turn_ordinal": turn["ordinal"],
            "rpnh_turn_model_evidence": evidence,
            "rpnh_turn_model_display_basis": (
                "registered_frontend_request"
                if evidence in {"frontend-request/v2", "frontend-request/v3"}
                else "session_current_selection"),
        }

    @staticmethod
    def _notice(turn):
        if turn["state"] == "stopped_by_owner":
            return "RPNH paused at its Registry checkpoint. Use /rpnh-resume or /rpnh-rollback."
        return "RPNH has no committed answer for this turn. Its child Registry was retained."

    @staticmethod
    def _error(turn):
        return {"type": "rpnh.no-committed-answer",
                "message": OpenCodeV2Protocol._notice(turn)}

    def _answer_text(self, turn):
        answer = turn.get("answer")
        if answer is None:
            if turn["state"] == "committed":
                raise FrontendError("invalid_terminal", "A committed answer is unavailable.", 500)
            return None
        if turn["state"] != "committed" or not isinstance(answer.get("reply"), str):
            raise FrontendError("invalid_terminal", "An answer lacks committed main-turn evidence.", 500)
        text = answer["reply"]
        if answer.get("protocol_valid") is False:
            text += "\n[RPNH main returned prose; no valid child-task decision was accepted.]"
        for child in turn.get("children", ()):
            text += (f"\n[RPNH child registered: {child['task_id']} ({child['kind']}); "
                     "this is not terminal success.]")
        return text

    def session_document(self, view):
        if not self.differential_events:
            return super().session_document(view)
        clock = {"created": view["created"], "updated": view["updated"]}
        if view["turns"]:
            clock["busy"] = view["turns"][-1]["created"]
        terminals = [t for t in view["turns"] if self._outcome(t)]
        if terminals:
            clock["idle"] = terminals[-1]["updated"]
        return {
            "id": view["id"], "projectID": "rpnh", "agent": "rpnh",
            "model": self._model_ref(view["model"]),
            "title": (view["turns"][0]["text"] if view["turns"] else "RPNH session")[:120],
            "location": {"directory": self.directory}, "time": clock,
            "cost": 0, "tokens": self._tokens(),
            **({"outcome": self._outcome(terminals[-1])} if terminals else {}),
            "metadata": {
                "rpnh_authority": "Registry", "rpnh_candidate": True,
                "rpnh_metrics": "unavailable_zero_sentinel",
                "rpnh_live_message_events": "admission_and_checkpoints_with_rest_reply_refresh",
                "rpnh_event_sequence": "display_only_not_Registry_or_native_event_log",
                "rpnh_state_error": view.get("error"),
            },
        }

    def _checked_ids(self, view):
        seen = set()
        for turn in view["turns"]:
            uid, aid = self._ids(view["id"], turn)
            if uid in seen or aid in seen or uid == aid:
                raise FrontendError("message_identity_collision", "Message identities require reconciliation.", 409)
            seen.update((uid, aid))

    def messages_v2(self, view):
        if not self.differential_events:
            return super().messages_v2(view)
        self._checked_ids(view)
        result = []
        for turn in view["turns"]:
            uid, aid = self._ids(view["id"], turn)
            metadata = self._turn_metadata(turn)
            # Pending input lives in the inbox until RPNH has begun its attempt.
            if turn["state"] in {"accepted", "pending_start"}:
                continue
            result.append({"id": uid, "type": "user", "text": turn["text"],
                           "time": {"created": turn["created"]}, "metadata": metadata})
            answer = self._answer_text(turn)
            outcome = self._outcome(turn)
            if answer is not None:
                result.append({
                    "id": aid, "type": "assistant", "agent": "rpnh",
                    "model": self._model_ref(turn["model"]),
                    "time": {"created": turn["created"], "completed": turn["updated"]},
                    "finish": "stop", "cost": 0, "tokens": self._tokens(),
                    "content": [{"type": "text", "text": answer}],
                    "metadata": {**metadata, "rpnh_turn_ref": turn["turn_ref"],
                                 "rpnh_metrics": "unavailable_zero_sentinel",
                                 "rpnh_started_basis": "turn_acceptance_not_provider_dispatch"},
                })
            elif outcome:
                # A checkpoint/failure observation is not a model reply.
                result.append({
                    "id": self._event_id(view["id"], turn, "notice").replace("evt_", "msg_", 1),
                    "type": "synthetic", "text": self._notice(turn),
                    "description": "RPNH Registry checkpoint observation",
                    "time": {"created": turn["updated"]},
                    "metadata": {**metadata, "rpnh_state": turn["state"]},
                })
            if outcome:
                idle = {"id": self._event_id(view["id"], turn, "idle").replace("evt_", "msg_", 1),
                        "type": "idle", "outcome": outcome,
                        "time": {"created": turn["updated"]}}
                if outcome == "failed":
                    idle["error"] = self._error(turn)
                result.append(idle)
        if view.get("error"):
            # The native client renders synthetic messages, not our session
            # metadata. This current observation is not a committed reply or
            # execution outcome. Error changes already force REST rehydration.
            result.append({
                "id": f"msg_rpnh/{view['id']}/reconciliation",
                "type": "synthetic",
                "text": ("RPNH reconciliation is required. Do not resend this input; "
                         "inspect the session Registry. No terminal outcome is "
                         "confirmed by this diagnostic notice."),
                # Stock TUI displays the synthetic description while its full
                # text is collapsed, so keep the actionable warning here too.
                "description": ("RPNH reconciliation is required. Do not resend this input; "
                                "inspect the Registry. (Diagnostic)"),
                "time": {"created": view["updated"]},
                "metadata": {"rpnh_observation": "reconciliation_required",
                             "rpnh_persisted": False},
            })
        return result

    def _inbox(self, view):
        return [{"id": self._ids(view["id"], turn)[0], "sessionID": view["id"],
                 "time": {"created": turn["created"]}, "type": "user",
                 "payload": {"text": turn["text"]}, "delivery": "steer"}
                for turn in view["turns"]
                if turn["state"] in {"accepted", "pending_start"}]

    def connected_event(self):
        if not self.differential_events:
            return super().connected_event()
        return {"id": f"evt_{self._epoch}_connected", "type": "server.connected",
                "created": int(time.time() * 1000), "data": {},
                "location": {"directory": self.directory}}

    def _project_events(self, view):
        self._checked_ids(view)
        events = []
        sid = view["id"]
        for turn in view["turns"]:
            uid, aid = self._ids(sid, turn)
            metadata = self._turn_metadata(turn)

            def emit(stage, kind, data, *, completed=False, extra=None):
                events.append({
                    "id": self._event_id(sid, turn, stage), "type": kind,
                    "created": turn["updated"] if completed else turn["created"],
                    "location": {"directory": self.directory},
                    "metadata": {**metadata, **(extra or {})},
                    "durable": {"aggregateID": sid, "seq": len(events), "version": 1},
                    "data": {"sessionID": sid, **data},
                })

            emit("enqueued", "session.inbox.enqueued",
                 {"inboxID": uid, "item": {"type": "user", "payload": {"text": turn["text"]},
                                           "delivery": "steer"}})
            if turn["state"] in {"accepted", "pending_start"}:
                continue
            emit("delivered", "session.inbox.delivered", {"inboxID": uid})
            emit("started", "session.execution.started", {},
                 extra={"rpnh_started_basis": "turn_acceptance_not_provider_dispatch"})
            answer = self._answer_text(turn)
            outcome = self._outcome(turn)
            if answer is not None:
                # One display step for one committed RPNH reply. The source does
                # not expose provider dispatch/stream timestamps or token usage.
                # Acceptance is an explicit display-time proxy, never a claim
                # that OpenCode dispatched a provider request or ran a tool.
                extra = {"rpnh_metrics": "unavailable_zero_sentinel",
                         "rpnh_started_basis": "turn_acceptance_not_provider_dispatch",
                         "rpnh_step": "committed_reply_display_only"}
                emit("step", "session.step.started",
                     {"assistantMessageID": aid, "agent": "rpnh",
                      "model": self._model_ref(turn["model"]), "started": turn["created"]},
                     completed=True, extra=extra)
                emit("text-start", "session.text.started",
                     {"assistantMessageID": aid, "ordinal": 0}, completed=True, extra=extra)
                emit("text-end", "session.text.ended",
                     {"assistantMessageID": aid, "ordinal": 0, "text": answer},
                     completed=True, extra=extra)
                emit("step-end", "session.step.ended",
                     {"assistantMessageID": aid, "finish": "stop",
                      "cost": 0, "tokens": self._tokens()}, completed=True, extra=extra)
            elif outcome:
                emit("notice", "session.synthetic",
                     {"text": self._notice(turn),
                      "description": "RPNH Registry checkpoint observation",
                      "metadata": {**metadata, "rpnh_state": turn["state"]}}, completed=True)
            if outcome:
                data = ({"error": self._error(turn)} if outcome == "failed" else
                        {"reason": "user"} if outcome == "interrupted" else {})
                emit("idle", f"session.execution.{outcome}", data, completed=True)
        return events

    def events(self):
        if not self.differential_events:
            return super().events()
        return [event for view in self._views() for event in self._project_events(view)]

    def _stream_snapshot(self):
        return {view["id"]: {
            "events": self._project_events(view),
            "model": self._model_ref(view["model"]),
            "error": view.get("error"),
        } for view in self._views()}

    def open_event_stream(self):
        """Baseline only: the client hydrates current history via REST."""
        return self._stream_snapshot(), []

    def poll_event_stream(self, cursor):
        """Return safe prefix extensions, or close for REST reconciliation.

        The cursor belongs to this HTTP connection. Last-Event-ID is never a
        replay cursor. A committed reply also closes the stream: the native
        text.started reducer appends even if a racing REST hydration already
        installed that reply. Reconnect replaces the complete transcript by
        stable IDs, avoiding a fabricated token stream or duplicate text.
        Pause/resume/rollback and selection changes use the same read recovery.
        """
        current = self._stream_snapshot()
        if set(cursor) - set(current):
            return None
        events = []
        for sid, snapshot in current.items():
            previous = cursor.get(sid, {"events": [], "model": snapshot["model"], "error": snapshot["error"]})
            old = previous["events"]
            if (snapshot["model"] != previous["model"] or snapshot["error"] != previous["error"]
                    or snapshot["events"][:len(old)] != old):
                return None
            changes = snapshot["events"][len(old):]
            if any(event["type"] == "session.step.started" for event in changes):
                # The REST response and SSE have independent delivery ordering.
                # No per-connection cursor can prove the native UI has not
                # already hydrated this assistant. Do not send append events.
                return None
            events.extend(changes)
        return current, events

    def _location_query(self, query, extra=()):
        if set(query) - {"location[directory]", *extra}:
            raise FrontendError("unsupported_query", "Unsupported catalog query.", 400)
        if query.get("location[directory]", [self.directory])[0] != self.directory:
            raise FrontendError("unsupported_location", "Only the isolated display directory is available.", 400)

    def _catalog(self, path, query):
        location = {"directory": self.directory}
        paths = {"/api/location", "/api/agent", "/api/agent/rpnh",
                 "/api/provider", "/api/provider/rpnh", "/api/model", "/api/model/default",
                 "/api/command", "/api/integration", "/api/mcp", "/api/mcp/resource",
                 "/api/reference", "/api/skill", "/api/shell", "/api/vcs",
                 "/api/config", "/api/permission/request", "/api/plugin",
                 "/api/form", "/api/fs/list", "/api/experimental/usage/limits"}
        if path not in paths:
            return None
        extra = ("types",) if path == "/api/model" else (("path",) if path == "/api/fs/list" else ())
        self._location_query(query, extra=extra)
        if path == "/api/fs/list" and query.get("path", ["."])[0] not in {"", ".", self.directory}:
            raise FrontendError("unsupported_location", "Only the empty isolated display directory is available.", 400)
        if path == "/api/location":
            return Reply(200, {**location, "project": {"id": "rpnh", "directory": self.directory,
                                                       "canonical": self.directory}})
        data = []
        if path in {"/api/agent", "/api/agent/rpnh"}:
            data = [{"id": "rpnh", "name": "RPNH", "mode": "primary", "hidden": False,
                     "description": "RPNH Registry owns execution; OpenCode presents the session.",
                     "model": self._model_ref({"selection": self.default_selection,
                                              "reasoning_effort": self.default_reasoning_effort}),
                     "request": {"settings": {}, "headers": {}, "body": {}}, "permissions": []}]
        elif path in {"/api/provider", "/api/provider/rpnh"}:
            data = [{"id": "rpnh", "name": "RPNH profiles",
                     "activation": "enabled", "package": ""}]
        elif path in {"/api/model", "/api/model/default"}:
            if query.get("types", ["chat"])[0] != "chat":
                data = []
            else:
                data = [{
                    "id": selection, "modelID": selection, "providerID": "rpnh",
                    "type": "chat", "name": f"{profile['model']} ({profile['provider_name']})",
                    "capabilities": {"tools": False, "input": ["text"], "output": ["text"]},
                    "variants": [{"id": effort} for effort in profile["supported_reasoning_efforts"]],
                    "time": {"released": 0}, "cost": [], "status": "active",
                    "enabled": profile["ready"], "limit": {"context": 0, "output": 0},
                } for selection, profile in self.profiles.items()]
            if path == "/api/model/default":
                data = next(item for item in data if item["id"] == self.default_selection)
        elif path == "/api/command":
            data = [{"name": "rpnh-resume", "description": "Resume the existing RPNH checkpoint"},
                    {"name": "rpnh-rollback", "description": "Roll back the existing RPNH main turn"}]
        elif path == "/api/mcp/resource":
            data = {"resources": [], "templates": []}
        elif path == "/api/vcs":
            data = {"branch": {}}
        elif path == "/api/experimental/usage/limits":
            # No OpenCode Console account or allowance exists in this frontend.
            data = None
        elif path == "/api/config":
            return Reply(200, [])
        if path in {"/api/agent/rpnh", "/api/provider/rpnh"}:
            data = data[0]
        return Reply(200, {"location": location, "data": data})

    def _message_page(self, sid, rows, query):
        if set(query) - {"limit", "order", "cursor", "type"}:
            raise FrontendError("unsupported_query", "Unsupported history query.", 400)
        limit = self._integer(query.get("limit", ["100"])[0], maximum=200)
        kind = query.get("type", [None])[0]
        if kind is not None:
            allowed = {"agent-switched", "model-switched", "location-switched", "user",
                       "synthetic", "system", "skill", "shell", "assistant", "compaction", "idle"}
            if kind not in allowed:
                raise FrontendError("invalid_type", "Unknown message type.", 400)
            rows = [row for row in rows if row["type"] == kind]
        cursor = query.get("cursor", [None])[0]
        order = query.get("order", ["desc"])[0]
        anchor, direction = None, "next"
        if cursor is not None:
            try:
                token = json.loads(cursor)
                if (set(token) != {"scope", "session", "order", "type", "anchor", "direction"}
                        or token["scope"] != "rpnh-display-message/v1"
                        or token["session"] != sid or token["type"] != kind or "order" in query
                        or token["direction"] not in {"next", "previous"}
                        or not isinstance(token["anchor"], str)):
                    raise ValueError
                order, anchor, direction = token["order"], token["anchor"], token["direction"]
            except (ValueError, TypeError, KeyError):
                raise FrontendError("invalid_cursor", "Invalid display cursor; refresh REST history.", 400) from None
        if order not in ("asc", "desc"):
            raise FrontendError("invalid_order", "Expected asc or desc.", 400)
        ordered = list(rows) if order == "asc" else list(reversed(rows))
        start, end = 0, limit
        if anchor is not None:
            position = next((i for i, row in enumerate(ordered) if row["id"] == anchor), None)
            if position is None:
                raise FrontendError("stale_cursor", "History changed; refresh the first REST page.", 409)
            if direction == "next":
                start, end = position + 1, position + 1 + limit
            else:
                start, end = max(0, position - limit), position
        page = ordered[start:end]
        cursors = {}
        def token(row, direction):
            return json.dumps({"scope": "rpnh-display-message/v1", "session": sid, "order": order,
                               "type": kind, "anchor": row["id"], "direction": direction},
                              separators=(",", ":"))
        if page and start:
            cursors["previous"] = token(page[0], "previous")
        if page and end < len(ordered):
            cursors["next"] = token(page[-1], "next")
        return {"data": page, "cursor": cursors}

    def _sessions_page(self, query):
        if set(query) - {"limit", "order", "search", "parentID", "archived", "directory", "project", "subpath"}:
            raise FrontendError("unsupported_query", "Unsupported session query.", 400)
        self._integer(query.get("limit", ["50"])[0], maximum=1000)
        if query.get("order", ["desc"])[0] not in {"asc", "desc"}:
            raise FrontendError("invalid_order", "Expected asc or desc.", 400)
        if query.get("archived", ["false"])[0] not in {"true", "false"}:
            raise FrontendError("invalid_query", "Expected a boolean archive filter.", 400)
        if query.get("directory", [self.directory])[0] != self.directory:
            raise FrontendError("unsupported_location", "Only the isolated display directory is available.", 400)
        rows = [self.session_document(v) for v in self._views()]
        if (query.get("parentID", ["null"])[0] != "null"
                or query.get("archived", ["false"])[0] == "true"
                or query.get("project", ["rpnh"])[0] != "rpnh"
                or query.get("subpath", [""])[0]):
            rows = []
        search = query.get("search", [""])[0].casefold()
        return {"data": [row for row in rows if search in row["title"].casefold()], "cursor": {}}

    def route(self, method: str, target: str, body: Any = None,
              headers: Mapping[str, str] | None = None) -> Reply:
        if not self.differential_events:
            return super().route(method, target, body, headers)
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise FrontendError("invalid_target", "Only origin-form requests are accepted.", 400)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise FrontendError("ambiguous_query", "Duplicate query fields.", 400)
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        if headers.get("x-opencode-workspace") or headers.get("x-opencode-directory", self.directory) != self.directory:
            raise FrontendError("unsupported_location", "Only the isolated display directory is available.", 400)
        path = parsed.path
        match = re.fullmatch(
            r"/api/session/(ses_[0-9a-f]{32})(?:/(prompt|interrupt|message|inbox|model|command|permission|form|view)(?:/(.+))?)?",
            path)
        if method == "GET":
            catalog = self._catalog(path, query)
            if catalog is not None:
                return catalog
            if path == "/api/session":
                return Reply(200, self._sessions_page(query))
            if match and match[2] == "message":
                view = self._view(match[1])
                messages = self.messages_v2(view)
                if match[3] is None:
                    return Reply(200, self._message_page(match[1], messages, query))
                if query:
                    raise FrontendError("unsupported_query", "A message read takes no query.", 400)
                message = next((m for m in messages if m["id"] == unquote(match[3])), None)
                if message is None:
                    raise FrontendError("unknown_message", "Unknown registered message.", 404)
                return Reply(200, {"data": message})
            if path == "/api/session/global/form":
                self._location_query(query)
                return Reply(200, {"data": []})
            if path == "/api/project":
                if set(query) - {"archived"} or query.get("archived", ["false"])[0] not in {"true", "false"}:
                    raise FrontendError("unsupported_query", "Unsupported project query.", 400)
                return Reply(200, [{"id": "rpnh", "canonical": self.directory, "name": "RPNH",
                                    "time": {"created": 0, "updated": 0, "active": 0}, "sandboxes": []}])
            if query:
                raise FrontendError("unsupported_query", "This read takes no query.", 400)
            if path == "/api/info":
                return Reply(200, {"version": self.profile.version, "pid": os.getpid(), "urls": [],
                                   "paths": {"tmp": self.directory}, "capabilities": {"persistentPty": False}})
            if path == "/api/config/shell":
                return Reply(200, [])
            if path == "/api/experimental/migration/v1":
                # Match the upstream no-op migration provider: this frontend
                # has no native v1 database and performs no migration work.
                return Reply(200, {"status": "completed"},
                             {"X-RPNH-Migration": "not-applicable"})
            if path == self.event_path:
                return Reply(200)
            if path == "/api/session/active":
                return Reply(200, {"data": {v["id"]: {"type": "running"} for v in self._views()
                                           if self.session_status(v)["type"] == "busy"}})
            if match and match[3] is None:
                view = self._view(match[1])
                if match[2] is None:
                    return Reply(200, {"data": self.session_document(view)})
                if match[2] == "inbox":
                    return Reply(200, {"data": self._inbox(view)})
                if match[2] in {"permission", "form"}:
                    return Reply(200, {"data": []})
            raise FrontendError("unsupported_read", "This RPNH frontend capability is unavailable.", 404)
        if method == "POST" and path == "/api/rpc/opencode.goal/get":
            self._location_query(query)
            if (not isinstance(body, dict) or set(body) != {"input"}
                    or not isinstance(body["input"], dict) or set(body["input"]) != {"sessionID"}
                    or not isinstance(body["input"]["sessionID"], str)):
                raise FrontendError("invalid_goal_read", "A goal read requires only the registered session ID.", 400)
            self._view(body["input"]["sessionID"])
            # The built-in goal plugin reads null when no goal exists. RPNH
            # has no native goal scheduler, storage or continuation loop.
            return Reply(200, {"output": None})
        interrupt_query = (match is not None and match[2] == "interrupt" and match[3] is None
                           and set(query) <= {"resume"}
                           and query.get("resume", ["false"])[0] in {"true", "false"})
        if method != "POST" or (query and not interrupt_query):
            raise FrontendError("unsupported_operation", "Native effects/query overrides are unavailable.", 405)
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise FrontendError("invalid_body", "Expected a JSON object.", 400)
        if path == "/api/session":
            # A local launcher binds its one session before the native UI starts.
            # The client optimistically creates IDs; never substitute an alias.
            if set(body) - {"agent", "model", "location"}:
                raise FrontendError("unsupported_create", "Session IDs and parentage are RPNH-owned; reopen the bound session.", 400)
            if body.get("location", {"directory": self.directory}) != {"directory": self.directory}:
                raise FrontendError("unsupported_location", "Only the isolated display directory is available.", 400)
            selection, effort = self._selection(body, fallback=self.default_selection,
                                                fallback_effort=self.default_reasoning_effort, create=True)
            sid = self.gateway.call("create_session", selection, reasoning_effort=effort)
            self.notify()
            return Reply(200, {"data": self.session_document(self._view(sid))})
        if not match or match[3] is not None:
            raise FrontendError("unsupported_operation", "Native execution, storage and tools are unavailable.", 501)
        sid, operation = match[1], match[2]
        view = self._view(sid)
        if operation == "model":
            if set(body) != {"model"} or not isinstance(body["model"], dict):
                raise FrontendError("invalid_model_confirmation", "Selection requires only a model.", 400)
            selection, effort = self._selection(body, fallback=view["model"]["selection"],
                                                fallback_effort=view["model"].get("reasoning_effort"), create=True)
            if (selection, effort) != (view["model"]["selection"], view["model"].get("reasoning_effort")):
                selected = self.gateway.call("select_model", sid, selection_id=selection, reasoning_effort=effort)
                if (selected["selection"], selected.get("reasoning_effort")) != (selection, effort):
                    raise FrontendError("selection_mismatch", "RPNH did not confirm the requested selection.", 409)
                self.notify()
            return Reply(204)
        if operation == "interrupt":
            if body:
                raise FrontendError("invalid_interrupt", "Interrupt accepts no overrides.", 400)
            # The native Esc request uses ?resume=true to drain queued inputs.
            # This frontend admits no queued/steered work: only the existing
            # RPNH stop runs. A Registry checkpoint is never auto-resumed.
            interrupted = self.gateway.call("abort", sid)
            self.notify()
            return Reply(200, {"interrupted": interrupted})
        if operation == "view":
            if (set(body) != {"idle"} or type(body["idle"]) not in {int, float}
                    or not isfinite(body["idle"])):
                raise FrontendError("invalid_view", "A numeric idle display watermark is required.", 400)
            # Read acknowledgement only; RPNH has no native read-receipt store.
            return Reply(204)
        if operation == "command":
            if (set(body) - {"name", "text", "files", "agents", "skills", "delivery"}
                    or body.get("name") not in ("rpnh-resume", "rpnh-rollback")
                    or not isinstance(body.get("text", ""), str) or body.get("text", "").strip()
                    or any(body.get(key, []) != [] for key in ("files", "agents", "skills"))
                    or body.get("delivery") not in (None, "steer")):
                raise FrontendError("unsupported_command", "Only argument-free RPNH checkpoint controls are available.", 400)
            self.gateway.call("command", sid, body["name"], "", "explicit:opencode-control:" + uuid4().hex)
            self.notify()
            return Reply(204)
        if operation != "prompt":
            raise FrontendError("unsupported_operation", "Native execution, storage and tools are unavailable.", 501)
        allowed = {"id", "text", "files", "agents", "skills", "metadata", "delivery", "resume"}
        if (set(body) - allowed
                or any(body.get(key, []) != [] for key in ("files", "agents", "skills"))
                or body.get("metadata", {}) != {}
                or body.get("delivery") not in (None, "steer")
                or ("resume" in body and body["resume"] is not True)):
            # resume=false means enqueue-without-run upstream; do not execute it.
            raise FrontendError("unsupported_prompt", "Only immediate plain-text admission is supported.", 400)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 131072 or text.lstrip().startswith("/"):
            raise FrontendError("invalid_text", "Nonempty plain text is required.", 400)
        selection, effort = view["model"]["selection"], view["model"].get("reasoning_effort")
        if "id" in body and (not isinstance(body["id"], str) or not body["id"]):
            raise FrontendError("invalid_request_id", "An explicit message identity must be a nonempty string.", 400)
        identity = self._supplied_id({"messageID": body.get("id")}, headers)
        if identity is None:
            identity = "msg_rpnh_" + uuid4().hex
        if not isinstance(identity, str) or not identity.startswith("msg_"):
            raise FrontendError("invalid_request_id", "Message identity must start with msg_.", 400)
        key = request_identity(text, None, {"provider": "rpnh", "selection": selection,
                                           "reasoning_effort": effort}, identity)
        self._checked_ids(view)
        # The existing application resolves the same-ID retry before its busy
        # and paused checks. A new ID never steers or queues another owner.
        ordinal = self.gateway.call("submit", sid, text, key, selection_id=selection, reasoning_effort=effort)
        self.notify()
        turn = next(t for t in self._view(sid)["turns"] if t["ordinal"] == ordinal)
        return Reply(200, {"data": {"id": self._ids(sid, turn)[0], "sessionID": sid,
                                    "time": {"created": turn["created"]}, "type": "user",
                                    "payload": {"text": turn["text"]}, "delivery": "steer"}},
                     {"X-RPNH-Submission-Key": key, "X-RPNH-Turn": str(ordinal)})
