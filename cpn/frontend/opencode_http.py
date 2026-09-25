"""Loopback HTTP/SSE transport; no execution policy or provider access."""
from __future__ import annotations

import base64
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

from cpn.rpnh.frontend_application import FrontendError, canonical
from .opencode_protocol import OpenCodeProtocol, Reply, encode_sse

MAX_BODY = 262144
MAX_RESPONSE = 4194304


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, protocol: OpenCodeProtocol, password: str) -> None:
        self.protocol = protocol
        self.authorization = "Basic " + base64.b64encode(("rpnh:" + password).encode()).decode()
        self.slots = threading.BoundedSemaphore(32)
        self.stream_slots = threading.BoundedSemaphore(8)
        # Only method, bounded route shape and status. No body/header/URL query
        # is retained, including in failure logs. This is not execution evidence.
        self.diagnostics: deque[dict[str, Any]] = deque(maxlen=64)
        self.diagnostic_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Base implementation prints traceback, potentially including secret
        # provider errors. Transport failures are deliberately generic instead.
        self.record("TRANSPORT", "unavailable", 500)

    def record(self, method: str, target: str, status: int) -> None:
        path = urlsplit(target).path
        path = re.sub(r"/ses_[^/]+", "/:session", path)
        # Avoid retaining arbitrary user content embedded in an unsupported URL.
        known = {"/session", "/session/status", "/config", "/config/providers", "/provider", "/provider/auth",
                 "/path", "/project/current", "/project/rpnh/directories", "/global/event", "/global/health",
                 "/global/config", "/agent", "/command", "/lsp", "/mcp", "/formatter", "/vcs", "/permission", "/question",
                 "/experimental/workspace", "/experimental/workspace/status", "/experimental/resource",
                 "/experimental/capabilities", "/experimental/console"}
        session_route = re.fullmatch(r"/session/:session(?:/(?:message|prompt_async|abort|command|todo|diff|children))?", path)
        route = path if path in known or session_route else "unsupported"
        with self.diagnostic_lock:
            self.diagnostics.append({"method": method, "route": route, "status": status})


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RPNH-OpenCode/1"
    sys_version = ""

    def setup(self) -> None:
        self.request.settimeout(10)
        super().setup()

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _validate(self) -> None:
        port = self.server.server_port
        if self.headers.get_all("Host") != [f"127.0.0.1:{port}"]:
            raise FrontendError("invalid_host", "Only this loopback origin is supported.", 403)
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}"):
            raise FrontendError("invalid_origin", "Cross-origin clients are not supported.", 403)
        supplied = self.headers.get_all("Authorization") or []
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode("utf-8"), self.server.authorization.encode("ascii")):
            raise FrontendError("unauthorized", "Session-local authentication is required.", 401)
        if self.headers.get("Transfer-Encoding") is not None:
            raise FrontendError("unsupported_framing", "Chunked requests are not supported.", 400)
        for key in ("Content-Length", "Content-Type", "Idempotency-Key", "x-opencode-directory", "x-opencode-workspace"):
            if len(self.headers.get_all(key) or []) > 1:
                raise FrontendError("ambiguous_header", "Duplicate request header.", 400)

    def _body(self) -> Any:
        length = self.headers.get("Content-Length", "0")
        if not re.fullmatch(r"[0-9]{1,8}", length) or int(length) > MAX_BODY:
            raise FrontendError("body_too_large", "Request body exceeds the supported bound.", 413)
        size = int(length)
        if not size:
            return None
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise FrontendError("unsupported_media", "A JSON request body is required.", 415)
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise FrontendError("incomplete_body", "Incomplete request body.", 400)
        try:
            return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (ValueError, UnicodeError, RecursionError):
            raise FrontendError("invalid_json", "Malformed, duplicate-field or nonfinite JSON.", 400) from None

    def _reply(self, reply: Reply) -> None:
        raw = b"" if reply.status == 204 else canonical(reply.body).encode("utf-8")
        if len(raw) > MAX_RESPONSE:
            raise FrontendError("projection_too_large", "Projection is too large; use bounded RPNH observations.", 413)
        self.server.record(self.command, self.path, reply.status)
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        for key, value in reply.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        self.close_connection = True

    def _stream(self) -> None:
        if not self.server.stream_slots.acquire(blocking=False):
            raise FrontendError("too_many_streams", "The bounded event stream limit was reached.", 503)
        try:
            protocol = self.server.protocol
            revision = protocol.revision()
            # Prepare the first snapshot BEFORE sending 200 so owner errors get
            # a normal error reply rather than a second status line inside SSE.
            frames = [encode_sse(protocol.envelope({"type": "server.connected", "properties": {}}))]
            frames.extend(encode_sse(event) for event in protocol.events())
            if sum(map(len, frames)) > MAX_RESPONSE:
                raise FrontendError("projection_too_large", "Event snapshot exceeds the display bound.", 413)
            self.server.record(self.command, self.path, 200)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    for frame in frames:
                        self.wfile.write(frame)
                    self.wfile.flush()
                    # Last-Event-ID is intentionally not a work-replay cursor.
                    # Reconnect sends the current registered snapshot, replacing
                    # stable message/part IDs. Slow peers coalesce updates.
                    if not protocol.wait(revision):
                        break
                    current = protocol.revision()
                    frames = ([encode_sse(event) for event in protocol.events()]
                              if current != revision else [b": keepalive\n\n"])
                    revision = current
                    if sum(map(len, frames)) > MAX_RESPONSE:
                        break
            except Exception:
                # Headers are already sent: close the stream, never append a
                # second HTTP response or provider exception inside SSE.
                self.server.record("SSE", "/global/event", 500)
            finally:
                self.close_connection = True
        finally:
            self.server.stream_slots.release()

    def _handle(self) -> None:
        try:
            self._validate()
            body = self._body()
            reply = self.server.protocol.route(self.command, self.path, body, dict(self.headers.items()))
            if self.command == "GET" and urlsplit(self.path).path == "/global/event":
                self._stream()
            else:
                self._reply(reply)
        except FrontendError as exc:
            self._reply(Reply(exc.status, {"name": "RPNHFrontendError", "data": {"code": exc.code, "message": str(exc)}}))
        except (BrokenPipeError, ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            self._reply(Reply(500, {"name": "RPNHFrontendError", "data": {
                "code": "transport_error", "message": "RPNH could not serve this request."}}))

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _handle


class OpenCodeHTTPServer:
    """Lifecycle facade for one ephemeral, authenticated loopback listener."""

    def __init__(self, protocol: OpenCodeProtocol) -> None:
        self.protocol = protocol
        self.password = secrets.token_urlsafe(32)
        self._server = _Server(protocol, self.password)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread: threading.Thread | None = None

    @property
    def authorization(self) -> str:
        return self._server.authorization

    def diagnostics(self) -> list[dict[str, Any]]:
        with self._server.diagnostic_lock:
            return list(self._server.diagnostics)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HTTP frontend is already running")
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True, name="rpnh-opencode-http")
        self._thread.start()

    def close(self) -> None:
        self.protocol.close()
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()
