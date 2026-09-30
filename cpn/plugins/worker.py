"""Bounded trusted-plugin worker; never inherits a Registry writer via fork."""
from __future__ import annotations
import contextlib
import multiprocessing
import os
import pickle
import signal
import time

from .api import PluginContext, canonical, json_copy, implementation_identity


class WorkerFailure(RuntimeError):
    def __init__(self, code: str, *, may_have_executed: bool = True):
        if not isinstance(may_have_executed, bool):
            raise TypeError("worker execution uncertainty must be boolean")
        self.code = code
        self.may_have_executed = may_have_executed
        super().__init__(code)


def _worker(handler, packet, environment, cancelled, connection, deadline):
    try:
        os.setsid()
        os.environ.clear()
        os.environ.update(environment)
        if implementation_identity(handler) != packet["implementation"]:
            raise WorkerFailure("implementation_changed")
        context = PluginContext(**packet["context"], cancel=cancelled, deadline=deadline)
        # stdout is not a protocol/authority channel. Do not inadvertently publish secrets.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            context.check_cancelled()
            value = handler(context, packet["arguments"])
        value = json_copy(value)
        if len(canonical(value)) > packet["max_result_bytes"]:
            raise WorkerFailure("result_limit_exceeded")
        connection.send_bytes(canonical({"ok": True, "value": value}))
    except BaseException as exc:
        code = exc.code if isinstance(exc, WorkerFailure) else "handler_failed"
        # Error details may contain secrets; transport only a closed diagnostic code.
        try:
            connection.send_bytes(canonical({"ok": False, "error": code}))
        except BaseException:
            pass
    finally:
        connection.close()


def execute_worker(handler, packet, *, environment_names, timeout_seconds, cancelled):
    """One admitted invocation, one process. No retries or detached jobs."""
    import json
    try:
        pickle.dumps(handler)
    except Exception as exc:
        raise WorkerFailure(
            "handler_failed", may_have_executed=False) from exc
    allowed = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    for name in environment_names:
        if name not in os.environ:
            raise WorkerFailure(
                "credential_environment_missing",
                may_have_executed=False)
        allowed[name] = os.environ[name]
    mp = multiprocessing.get_context("spawn")
    reader, writer = mp.Pipe(duplex=False)
    event = mp.Event()
    deadline = time.monotonic() + timeout_seconds
    process = mp.Process(target=_worker, args=(handler, packet, allowed, event, writer, deadline))
    started = False
    try:
        try:
            process.start()
        except Exception as exc:
            raise WorkerFailure(
                "worker_protocol_failed", may_have_executed=False) from exc
        started = True
        writer.close()
        while True:
            if cancelled():
                event.set()
                raise WorkerFailure("cancelled_after_start")
            if time.monotonic() >= deadline:
                event.set()
                raise WorkerFailure("deadline_exceeded")
            if reader.poll(0.05):
                try:
                    payload = reader.recv_bytes(maxlength=packet["max_result_bytes"] + 1024)
                    result = json.loads(payload)
                except (OSError, EOFError, ValueError) as exc:
                    raise WorkerFailure("worker_protocol_failed") from exc
                if not isinstance(result, dict) or result.get("ok") is not True:
                    raise WorkerFailure(result.get("error", "handler_failed") if isinstance(result, dict)
                                        else "worker_protocol_failed")
                if set(result) != {"ok", "value"}:
                    raise WorkerFailure("worker_protocol_failed")
                return result["value"]
            if cancelled():
                event.set()
                raise WorkerFailure("cancelled_after_start")
            if time.monotonic() >= deadline:
                event.set()
                raise WorkerFailure("deadline_exceeded")
            if not process.is_alive():
                # The child can send and exit while the status callback above
                # is running. A previous empty poll is not evidence that the
                # now-finished worker produced no result. Drain its buffered
                # response through the normal bounded decoder before failing.
                if reader.poll(0):
                    continue
                raise WorkerFailure("worker_exited_without_result")
    finally:
        reader.close(); writer.close()
        if started:
            # Terminate the invocation process group, including plugin-created subprocesses.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                if process.is_alive():
                    process.terminate()
            process.join(0.3)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if process.is_alive():
                    process.kill()
            process.join(1)
            if not process.is_alive():
                process.close()
