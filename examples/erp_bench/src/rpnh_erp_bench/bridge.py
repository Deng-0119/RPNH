"""Trial-local transport. A receipt admits a script, never its ERP transactions."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import select
import socket
import socketserver
import struct
import threading
import time
import uuid

MAX_FRAME = 2 * 1024 * 1024
MAX_SOURCE = 256 * 1024
MAX_OUTPUT = 32 * 1024
IDENTITY_KEYS = ("trial_id", "operation_id", "invocation_id", "firing_id", "call_id")
STATUSES = ("completed", "failed", "domain_infeasible", "unknown", "interrupted")


def send_frame(connection, value):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("bridge frame too large")
    connection.sendall(struct.pack("!I", len(data)) + data)


def receive_frame(connection, check=None):
    def read(size):
        data = bytearray()
        while len(data) < size:
            if check:
                check()
            try:
                part = connection.recv(size - len(data))
            except socket.timeout:
                if check:
                    continue
                raise
            if not part:
                raise EOFError("bridge response lost")
            data.extend(part)
        return bytes(data)
    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("invalid bridge frame length")
    return json.loads(read(size))


def check_arguments(arguments):
    if not isinstance(arguments, dict) or set(arguments) - {"source", "timeout_seconds"}:
        raise ValueError("only source and timeout_seconds are accepted")
    source = arguments.get("source")
    timeout = arguments.get("timeout_seconds", 3600)
    if not isinstance(source, str) or len(source.encode()) > MAX_SOURCE:
        raise ValueError("source must be a bounded Python string")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("timeout_seconds must be an integer from 1 to 3600")
    return source, timeout


def result(status, identity, execution_id=None, *, stdout="", stderr="", exit_code=None):
    return {"status": status, "stdout": stdout[:MAX_OUTPUT], "stderr": stderr[:MAX_OUTPUT],
            "exit_code": exit_code, "execution_id": execution_id,
            "identity": dict(identity), "admission_granularity": "script"}


class Bridge:
    """One non-resumable trial lifetime; never reopen a trial to replay effects.

    The parent must stop/quiesce its container after an unknown result. Closing
    this server requests cooperative cancellation; it does not prove ERP rollback.
    """

    def __init__(self, endpoint, trial_id, backend):
        self.endpoint = str(endpoint)
        if not Path(self.endpoint).is_absolute() or len(os.fsencode(self.endpoint)) > 107:
            raise ValueError("endpoint must be a short absolute Unix socket path")
        if not isinstance(trial_id, str) or not trial_id:
            raise ValueError("trial_id is required")
        self.trial_id, self.backend = trial_id, backend
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._records = {}
        self._uncertain = False
        self._server = None

    def __enter__(self):
        if self._server is not None or self._stop.is_set():
            raise RuntimeError("bridge instances cannot be restarted")
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(5)
                try:
                    frame = receive_frame(self.request)
                    owner._dispatch(frame, self.request)
                    return
                except (ValueError, TypeError, KeyError, EOFError, OSError):
                    reply = {"error": "invalid trial request"}
                try:
                    send_frame(self.request, reply)
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            request_queue_size = 8

            def __init__(self, *args):
                self.slots = threading.BoundedSemaphore(8)
                super().__init__(*args)

            def process_request(self, request, address):
                if not self.slots.acquire(False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, address)
                except BaseException:
                    self.slots.release()
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()

        # Never remove/replace an existing endpoint, including a stale one.
        self._server = Server(self.endpoint, Handler)
        os.chmod(self.endpoint, 0o600)
        self._socket_inode = os.stat(self.endpoint).st_ino
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self.close_admission()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        try:
            if os.lstat(self.endpoint).st_ino == self._socket_inode:
                os.unlink(self.endpoint)
        except FileNotFoundError:
            pass

    def close_admission(self):
        """Reject new execution and request cancellation; parent then quiesces backend."""
        self._stop.set()

    def _dispatch(self, frame, connection):
        if not isinstance(frame, dict) or set(frame) != {"identity", "arguments", "remaining_seconds"}:
            raise ValueError("invalid invocation frame")
        identity = frame["identity"]
        if (not isinstance(identity, dict) or set(identity) != set(IDENTITY_KEYS)
                or any(not isinstance(v, str) or not v or len(v) > 512 for v in identity.values())
                or identity["trial_id"] != self.trial_id):
            raise ValueError("invalid invocation identity")
        source, timeout = check_arguments(frame["arguments"])
        remaining = frame["remaining_seconds"]
        if type(remaining) not in (float, int) or not math.isfinite(remaining) or not 0 < remaining <= 3600:
            raise ValueError("invalid deadline")
        deadline = time.monotonic() + min(timeout, remaining)

        def cancelled():
            if self._stop.is_set() or time.monotonic() >= deadline:
                return True
            readable, _, _ = select.select([connection], [], [], 0)
            # No second request is allowed on this connection. EOF is cancellation.
            return bool(readable)

        while not self._lock.acquire(timeout=0.05):
            if cancelled():
                send_frame(connection, result("interrupted", identity, stderr="cancelled before execution"))
                return
        try:
            key = identity["call_id"]
            if key in self._records:
                prior_identity, prior_arguments, prior_result = self._records[key]
                if prior_identity != identity or prior_arguments != frame["arguments"]:
                    raise ValueError("call identity reused with different inputs")
                self._deliver(connection, prior_result)
                return
            execution_id = uuid.uuid4().hex
            answer = result("unknown", identity, execution_id, stderr="execution response unavailable")
            self._records[key] = (dict(identity), dict(frame["arguments"]), answer)
            if cancelled():
                answer = result("interrupted", identity, execution_id, stderr="cancelled before execution")
            elif self._uncertain:
                answer = result("interrupted", identity, execution_id, stderr="trial requires reconciliation")
            else:
                try:
                    raw = self.backend.execute_python(source, min(timeout, deadline - time.monotonic()),
                                                      cancelled, dict(identity))
                    if (not isinstance(raw, dict) or raw.get("status") not in STATUSES
                            or not isinstance(raw.get("stdout"), str) or not isinstance(raw.get("stderr"), str)
                            or (raw.get("exit_code") is not None and type(raw["exit_code"]) is not int)
                            or not isinstance(raw.get("execution_id"), str) or not raw["execution_id"]):
                        raise ValueError("invalid backend response")
                    answer = result(raw["status"], identity, raw["execution_id"], stdout=raw["stdout"],
                                    stderr=raw["stderr"], exit_code=raw.get("exit_code"))
                except Exception:
                    # Never expose host exceptions/paths/credentials as actor observations.
                    pass
                if answer["status"] in {"unknown", "interrupted"}:
                    self._uncertain = True
            self._records[key] = (dict(identity), dict(frame["arguments"]), answer)
            self._deliver(connection, answer)
        finally:
            self._lock.release()

    def _deliver(self, connection, answer):
        """Called under the admission lock: sending is part of settlement.

        A successful send is only local transport evidence, not proof of model
        consumption. A failed send must close the world before another call ID
        can execute; the original result and identity remain available in cache.
        """
        try:
            send_frame(connection, answer)
        except (OSError, ValueError):
            self._uncertain = True
            raise
