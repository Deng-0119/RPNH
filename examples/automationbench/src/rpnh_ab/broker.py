"""Serial world owner. One admitted plugin call -> one upstream invocation.

Deduplication is process-lifetime only. A crashed/in-flight write is never replayed
on resume. Logs prove environment execution, not subsequent model consumption.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import socketserver
import tempfile
import threading
from .constants import OPERATION_TIMEOUT_SECONDS, TOOLS
from .io import append, now, receive, replace_checkpoint, send, sha
from .configuration_condition import overlay_search, pre_dispatch_error, selected


class Broker:
    def __init__(self, upstream, state: dict, attempt: Path, run_id: str):
        self.upstream, self.state, self.attempt, self.run_id = upstream, state, attempt, run_id
        self.sequence = 0
        self.cache: dict[tuple, tuple[str, dict]] = {}
        self.poisoned = False
        self.closed = False
        self.lock = threading.RLock()
        self._temp = None
        self._server = None
        self._thread = None

    def call(self, request: dict) -> dict:
        with self.lock:
            required = {"run_id", "tool", "operation_id", "invocation_id", "firing_id", "call_id", "arguments"}
            if not isinstance(request, dict) or set(request) != required:
                return {"ok": False, "error": "invalid_request_shape"}
            if request["run_id"] != self.run_id or request["tool"] not in TOOLS:
                return {"ok": False, "error": "unbound_run_or_tool"}
            if any(not isinstance(request[k], str) or not request[k]
                   for k in required - {"arguments"}) or not isinstance(request["arguments"], dict):
                return {"ok": False, "error": "invalid_identity_or_arguments"}
            key = tuple(request[k] for k in ("run_id", "invocation_id", "firing_id", "call_id"))
            digest = sha(request)
            if key in self.cache:
                old_digest, response = self.cache[key]
                if digest != old_digest:
                    return {"ok": False, "error": "identity_reused_with_different_payload"}
                append(self.attempt / "tool_events.jsonl", {"kind": "delivery_replay", "at": now(),
                       "request_sha256": digest, "sequence": response.get("request_sequence")})
                return copy.deepcopy(response)
            if self.closed or self.poisoned:
                return {"ok": False, "error": "world_owner_closed_or_checkpoint_failed"}
            self.sequence += 1
            seq = self.sequence
            condition = selected(self.upstream)
            if condition is not None:
                error = pre_dispatch_error(request["tool"], request["arguments"])
                if error is not None:
                    response = {"ok": True, "result": json.dumps(error), "request_sequence": seq}
                    # A rejection is a returned tool result, never an upstream
                    # dispatch. Preserve the original arguments privately.
                    append(self.attempt / "tool_events.jsonl", {
                        "kind": "pre_dispatch_rejected", "at": now(), "sequence": seq,
                        "request": request, "request_sha256": digest,
                        "response": response, "configuration_condition": condition,
                        "upstream_dispatched": False, "effect_status": "not_started"})
                    self.cache[key] = (digest, response)
                    return copy.deepcopy(response)
            append(self.attempt / "tool_events.jsonl", {"kind": "dispatch_started", "at": now(),
                   "sequence": seq, "request": request, "request_sha256": digest})
            try:
                raw = self.upstream.dispatch(self.state, request["tool"], request["arguments"])
                visible = raw
                if condition is not None and request["tool"] == "api_search":
                    visible, endpoints = overlay_search(raw)
                    append(self.attempt / "api_search_metadata_events.jsonl", {
                        "schema": "rpnh-ab/api-search-metadata/v1", "at": now(),
                        "sequence": seq, "request_sha256": digest,
                        "configuration_condition": condition,
                        "raw_upstream_result": raw, "actor_visible_result": visible,
                        "raw_upstream_sha256": sha(raw), "actor_visible_sha256": sha(visible),
                        "changed_endpoint_ids": endpoints,
                        "proves_subsequent_model_consumption": False})
                response = {"ok": True, "result": visible, "request_sequence": seq}
            except Exception as exc:
                response = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "request_sequence": seq}
            self.cache[key] = (digest, response)
            # A failed upstream function may already have changed state. Save it,
            # too; never restore state to make an exception look like no action.
            try:
                replace_checkpoint(self.attempt / "world.latest.json", self.upstream.dump_world(self.state))
                append(self.attempt / "tool_events.jsonl", {"kind": "dispatch_finished", "at": now(),
                       "sequence": seq, "response": response})
            except Exception:
                self.poisoned = True
                raise
            return copy.deepcopy(response)

    def __enter__(self):
        self._temp = tempfile.TemporaryDirectory(prefix="rab-")
        endpoint = Path(self._temp.name) / "tools.sock"
        self.endpoint = str(endpoint)
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(OPERATION_TIMEOUT_SECONDS)
                try:
                    response = owner.call(receive(self.request))
                    send(self.request, response)
                except (EOFError, OSError, ValueError) as exc:
                    append(owner.attempt / "transport_events.jsonl",
                           {"at": now(), "error": type(exc).__name__ + ": " + str(exc)})
        self._server = socketserver.UnixStreamServer(self.endpoint, Handler)
        os.chmod(self.endpoint, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *unused):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join()
        with self.lock:
            self.closed = True
        if self._temp:
            self._temp.cleanup()
