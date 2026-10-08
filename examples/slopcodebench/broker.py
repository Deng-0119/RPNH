"""Host-side serial bridge to an existing official inference StreamingRuntime.

No model, scheduler, workspace copier or evaluator. Transport ambiguity poisons
the bridge; it never blindly replays a potentially executed shell write.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import socketserver
import tempfile
import threading
import time

from .contracts import canonical, digest
from .plugin import COMMAND_TIMEOUT, OPERATION_TIMEOUT
from .wire import FRAME_LIMIT, receive, send


class SessionCommandBroker:
    def __init__(self, runtime, *, checkpoint_key: str, evidence_dir: Path):
        self.runtime, self.checkpoint_key = runtime, checkpoint_key
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.poisoned = False
        self.closed = False
        self.cache = {}
        self.lock = threading.Lock()
        self._server = self._thread = self._temporary = None
        self.quiescent = False

    @staticmethod
    def _bounded(action, seconds):
        failures = []
        def run():
            try:
                action()
            except BaseException as exc:
                failures.append(exc)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(seconds)
        if worker.is_alive():
            raise RuntimeError("runtime shutdown deadline exceeded; snapshot prohibited")
        if failures:
            raise RuntimeError("runtime shutdown failed; snapshot prohibited") from failures[0]

    def _stop_uncertain_runtime(self):
        self.poisoned = True
        self._bounded(self.runtime.kill, 15)

    def _record(self, body):
        with (self.evidence_dir / "commands.jsonl").open("ab") as stream:
            stream.write(canonical(body) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def call(self, request):
        fields = {"checkpoint_key", "operation_id", "invocation_id", "firing_id", "call_id", "command"}
        if (not isinstance(request, dict) or set(request) != fields
                or any(not isinstance(v, str) or not v for v in request.values())
                or request["checkpoint_key"] != self.checkpoint_key
                or len(request["command"]) > 65536):
            return {"ok": False, "error": "invalid_bound_request"}
        key = tuple(request[field] for field in ("operation_id", "invocation_id", "firing_id", "call_id"))
        identity = digest(request)
        with self.lock:
            if key in self.cache:
                old_identity, response = self.cache[key]
                if old_identity != identity:
                    return {"ok": False, "error": "identity_reused_with_different_request"}
                return deepcopy(response)
            if self.closed or self.poisoned:
                return {"ok": False, "error": "closed_or_uncertain_runtime"}
            self.sequence += 1
            sequence = self.sequence
            self._record({"event": "command_started", "sequence": sequence,
                          "request_sha256": identity,
                          "identity": {k: request[k] for k in fields - {"command"}},
                          "command_sha256": hashlib.sha256(request["command"].encode()).hexdigest()})
            result = None
            observed_bytes = 0
            try:
                for event in self.runtime.stream(command=request["command"], env={}, timeout=COMMAND_TIMEOUT):
                    if event.kind == "finished":
                        if result is not None or event.result is None:
                            raise ValueError("runtime returned an invalid terminal event")
                        result = event.result
                    elif event.kind in {"stdout", "stderr"}:
                        observed_bytes += len((event.text or "").encode("utf-8"))
                        if observed_bytes > FRAME_LIMIT - 4096:
                            raise ValueError("runtime output exceeds declared transport bound")
                    else:
                        raise ValueError("unknown runtime event")
                if result is None:
                    raise ValueError("runtime ended without a terminal event")
                payload = {"stdout": result.stdout, "stderr": result.stderr,
                           "exit_code": result.exit_code, "timed_out": result.timed_out,
                           "elapsed": result.elapsed, "sequence": sequence,
                           "command_sha256": hashlib.sha256(request["command"].encode()).hexdigest()}
                if len(canonical(payload)) > FRAME_LIMIT - 4096:
                    raise ValueError("runtime result exceeds declared transport bound")
                response = {"ok": True, "result": payload}
                if result.timed_out:
                    self._stop_uncertain_runtime()
                    response = {"ok": False, "error": "runtime_effect_unknown", "sequence": sequence}
            except BaseException:
                self._stop_uncertain_runtime()
                response = {"ok": False, "error": "runtime_effect_unknown", "sequence": sequence}
            self.cache[key] = (identity, response)
            try:
                self._record({"event": "command_finished", "sequence": sequence,
                              "response": response, "poisoned": self.poisoned})
            except BaseException:
                self._stop_uncertain_runtime()
                raise
            return deepcopy(response)

    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="rpnh-scb-")
        self.endpoint = str(Path(self._temporary.name) / "command.sock")
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(OPERATION_TIMEOUT)
                try:
                    send(self.request, owner.call(receive(self.request)))
                except (EOFError, OSError, ValueError):
                    # Never retry: the command may already have executed.
                    owner._stop_uncertain_runtime()

        try:
            self._server = socketserver.UnixStreamServer(self.endpoint, Handler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        except BaseException:
            self._temporary.cleanup()
            raise
        return self

    def __exit__(self, _exc_type=None, primary=None, _traceback=None):
        self.closed = True
        self.quiescent = False
        # Stop the container, including detached children, before draining IPC.
        # Do not block forever in server.shutdown() behind an active handler.
        deadline = time.monotonic() + 30
        failures = []

        def attempt(label, action, limit=None):
            remaining = max(0, deadline - time.monotonic())
            try:
                self._bounded(action, remaining if limit is None else min(limit, remaining))
            except BaseException as error:
                self.poisoned = True
                failures.append((label, error))

        # A cleanup error must not skip closing our listener and temporary path.
        attempt("initial runtime cleanup", self.runtime.cleanup, 15)
        if self._server is not None:
            attempt("command server shutdown", self._server.shutdown)
            attempt("command server close", self._server.server_close)
        if self._thread is not None:
            try:
                self._thread.join(max(0, deadline - time.monotonic()))
                if self._thread.is_alive():
                    raise RuntimeError("command server did not quiesce")
            except BaseException as error:
                self.poisoned = True
                failures.append(("command server join", error))
        # Startup may race the first cleanup. Drain proves no handler can create
        # a new container after this final cleanup completes.
        attempt("final runtime cleanup", self.runtime.cleanup)
        if self._temporary is not None:
            attempt("temporary socket directory cleanup", self._temporary.cleanup)
        if failures:
            error = primary if primary is not None else failures[0][1]
            for label, secondary in failures:
                cause = secondary.__cause__ or secondary
                error.add_note(f"SCB {label} failed: {type(cause).__name__}: {cause}")
            if primary is None:
                raise error
            return False  # Let the with statement propagate the original owner exception.
        self.quiescent = True
        return False
