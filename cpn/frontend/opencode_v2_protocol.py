"""Explicit 2.2.5 REST candidate; never a production/native certification claim.

Types are mapped from 9bbb8ce18c80f099e0e448775f59d965ead40a21 generated
client.ts/types.ts. No upstream durable event history is manufactured from
Registry turn ordinals. Callers refresh REST history; native live UI is unqualified.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from cpn.rpnh.frontend_application import FrontendError, request_identity
from .opencode_protocol import OpenCodeProtocol, Reply, get_opencode_profile

CANDIDATE_VERSION = "2.2.5"


class OpenCodeV2Protocol(OpenCodeProtocol):
    basic_username = "opencode"
    event_path = "/api/event"

    def __init__(self, gateway, directory: str, *, profile):
        if profile != get_opencode_profile(certification_version=CANDIDATE_VERSION):
            raise ValueError("Exact OpenCode 2.2.5 candidate profile is required")
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
        if order not in {"asc", "desc"}:
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
        match = re.fullmatch(r"/api/session/(ses_[0-9a-f]{32})(?:/(prompt|interrupt|message))?", path)
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
            raise FrontendError("unsupported_read", "Not implemented by the explicit RPNH 2.2.5 candidate.", 404)
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
        if not match or match[2] not in {"prompt", "interrupt"}:
            raise FrontendError("unsupported_operation", "Native execution, storage and tools are unavailable.", 501)
        sid, operation = match.groups()
        view = self._view(sid)
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
                or body.get("delivery") is not None
                or (body.get("resume") is not None and body.get("resume") is not False)):
            raise FrontendError("unsupported_prompt", "Attachments, metadata, delivery, resume and runtime overrides are unavailable.", 400)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 131072 or text.lstrip().startswith("/"):
            raise FrontendError("invalid_text", "Nonempty plain text is required; native slash commands are unavailable.", 400)
        selection, effort = view["model"]["selection"], view["model"].get("reasoning_effort")
        identity = self._supplied_id({"messageID": body.get("id")}, headers)
        key = request_identity(text, None, {"provider": "rpnh", "selection": selection,
                                            "reasoning_effort": effort}, identity)
        if identity is not None and not identity.startswith("msg_"):
            raise FrontendError("invalid_request_id", "Message identity must start with msg_.", 400)
        ordinal = self.gateway.call("submit", sid, text, key, selection_id=selection, reasoning_effort=effort)
        self.notify()
        turn = next(t for t in self._view(sid)["turns"] if t["ordinal"] == ordinal)
        return Reply(200, {"data": {"id": self._ids(sid, turn)[0], "sessionID": sid,
                                    "time": {"created": turn["created"]}, "type": "user",
                                    "payload": {"text": turn["text"]}, "delivery": "queue"}},
                     {"X-RPNH-Submission-Key": key, "X-RPNH-Turn": str(ordinal)})
