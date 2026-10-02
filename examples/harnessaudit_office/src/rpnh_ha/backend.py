"""Single-thread-owned upstream Bank, exposed through role-bound Unix sockets.

This service is the benchmark environment, NOT an agent scheduler. It contains
no LLM, no task solution, no hidden access-rule enforcement and no tool retry.
A separate socket binding supplies the actor identity; model arguments cannot.
The upstream SQLite connection is created, used and snapshotted in ONE thread.
"""
from __future__ import annotations
from dataclasses import dataclass
from concurrent.futures import Future
import copy
import os
from pathlib import Path
import queue
import selectors
import socket
import sqlite3
import tempfile
import threading
from typing import Any, Callable, Mapping

from .evidence import Witness
from .jsonio import recv_frame, send_frame, write_new, dumps
from .state_diff import snapshot_tables

PLUGIN_ROLES = {
    "ha_manager": "operations_manager",
    "ha_admin": "workplace_services_admin",
    "ha_policy": "policy_analyst",
}


@dataclass(frozen=True)
class FaultSpec:
    mode: str = "none"
    tool: str = ""
    occurrence: int = 1

    def __post_init__(self):
        if self.mode not in {"none", "read_error", "response_lost_after_effect"}:
            raise ValueError("unsupported fault mode")
        if self.mode != "none" and not self.tool:
            raise ValueError("fault tool is required")
        if type(self.occurrence) is not int or self.occurrence < 1:
            raise ValueError("fault occurrence must be a positive integer")


class BackendService:
    def __init__(self, *, run_id: str, root: Path, tools: Mapping[str, str],
                 bank_factory: Callable[[], Any],
                 dispatch: Callable[[Any, str, dict], str],
                 fault: FaultSpec = FaultSpec(), socket_timeout: float = 5.0, max_calls: int | None = None,
                 plugin_roles: Mapping[str, str] | None = None):
        if not run_id or not tools or any(v not in {"external_read", "external_write", "pure"}
                                         for v in tools.values()):
            raise ValueError("run identity and declared tool effects are required")
        if socket_timeout <= 0: raise ValueError("socket timeout must be positive")
        if fault.mode != "none" and fault.tool not in tools:
            raise ValueError("fault tool is absent from this catalog")
        if fault.mode == "read_error" and tools[fault.tool] != "external_read":
            raise ValueError("read_error injection must target a read operation")
        if fault.mode == "response_lost_after_effect" and tools[fault.tool] != "external_write":
            raise ValueError("after-effect injection must target an external write")
        if max_calls is not None and (type(max_calls) is not int or max_calls < 1):
            raise ValueError("max_calls must be a positive integer or None")
        from .office_cases import PLUGIN_SLOTS
        roles = dict(PLUGIN_ROLES if plugin_roles is None else plugin_roles)
        if (not roles or set(roles) - set(PLUGIN_SLOTS)
                or any(not isinstance(role, str) or not role or role == "user" for role in roles.values())
                or len(set(roles.values())) != len(roles)):
            raise ValueError("host plugin roles are invalid")
        self.plugin_roles = roles
        self.max_calls, self._native_count = max_calls, 0
        self.run_id, self.root, self.tools = run_id, Path(root), dict(tools)
        self._bank_factory, self._dispatch = bank_factory, dispatch
        self.fault, self.socket_timeout = fault, socket_timeout
        self._commands: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._ready: Future = Future()
        self._thread = None
        self._temporary = None
        self.endpoints: dict[str, str] = {}
        self._listeners = []
        self._seen: dict[tuple, str] = {}
        self._occurrences: dict[str, int] = {}
        self._fault_triggered = False
        self._fatal: str | None = None

    def start(self):
        self.root.mkdir(parents=True, exist_ok=False)
        os.chmod(self.root, 0o700)
        self.witness = Witness(self.root / "witness.jsonl")
        self._temporary = tempfile.TemporaryDirectory(prefix="rha-")
        os.chmod(self._temporary.name, 0o700)
        for number, plugin in enumerate(self.plugin_roles):
            path = str(Path(self._temporary.name) / f"{number}.sock")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(path)
            os.chmod(path, 0o600)
            sock.listen(16)
            self._listeners.append((plugin, sock))
            self.endpoints[plugin] = path
        self._thread = threading.Thread(target=self._serve, name="ha-bank-owner", daemon=True)
        self._thread.start()
        try:
            self._ready.result(timeout=30)
        except BaseException:
            self.close()
            raise
        return self

    def __enter__(self): return self.start()
    def __exit__(self, *_): self.close()

    def _serve(self):
        selector = selectors.DefaultSelector()
        bank = None
        try:
            bank = self._bank_factory()
            if bank is None or not isinstance(getattr(bank, "conn", None), sqlite3.Connection):
                raise TypeError("a real SQLite-backed bank is required; no mock fallback")
            for plugin, sock in self._listeners:
                selector.register(sock, selectors.EVENT_READ, plugin)
            self.witness.append("backend_started", run_id=self.run_id,
                                owner_thread=threading.get_ident(),
                                fault={"mode": self.fault.mode, "tool": self.fault.tool,
                                       "occurrence": self.fault.occurrence})
            self._ready.set_result(True)
            while not self._stop.is_set():
                self._drain_commands(bank)
                for key, _ in selector.select(timeout=0.05):
                    conn, _ = key.fileobj.accept()
                    with conn:
                        conn.settimeout(self.socket_timeout)
                        try:
                            request = recv_frame(conn)
                            response = self._handle(bank, key.data, request)
                            if response is not None:  # None deliberately closes a lost response.
                                send_frame(conn, response)
                        except (OSError, ValueError, TypeError, KeyError, EOFError) as exc:
                            self.witness.append("transport_error", run_id=self.run_id,
                                                plugin=key.data, error_type=type(exc).__name__)
                            try: send_frame(conn, {"ok": False, "error": "transport_error"})
                            except (OSError, ValueError): pass
            self._drain_commands(bank)
        except BaseException as exc:
            self._fatal = type(exc).__name__ + ": " + str(exc)
            if not self._ready.done(): self._ready.set_exception(exc)
        finally:
            while True:
                try: _, _, future = self._commands.get_nowait()
                except queue.Empty: break
                if not future.done(): future.set_exception(RuntimeError(self._fatal or "backend closed"))
            if bank is not None:
                try: bank.conn.close()
                except Exception: pass
            selector.close()

    def _drain_commands(self, bank):
        while True:
            try: command, args, future = self._commands.get_nowait()
            except queue.Empty: break
            try:
                if command == "snapshot":
                    path, = args
                    if path.exists(): raise FileExistsError("snapshot must be fresh")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    # SQLite backup includes committed WAL state without copying live DB files.
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                    with sqlite3.connect(path) as target:
                        bank.conn.backup(target)
                    result = snapshot_tables(bank.conn)
                elif command == "tables": result = snapshot_tables(bank.conn)
                elif command == "counters":
                    from .bank_transfer import COUNTERS
                    result = {name: getattr(bank, name) for name in COUNTERS if hasattr(bank, name)}
                else: raise ValueError("unknown host-only command")
                future.set_result(result)
            except BaseException as exc:
                future.set_exception(exc)

    def _host_command(self, command, *args):
        if self._thread is None or not self._thread.is_alive() or self._stop.is_set():
            raise RuntimeError(self._fatal or "backend is not running")
        future = Future()
        self._commands.put((command, args, future))
        return future.result(timeout=30)

    def snapshot(self, path: Path) -> dict:
        return self._host_command("snapshot", Path(path))

    def tables(self) -> dict:
        return self._host_command("tables")

    def counters(self) -> dict:
        return self._host_command("counters")

    def _handle(self, bank, plugin: str, req: Any):
        expected = {
            "run_id", "operation_id", "invocation_id", "firing_id",
            "call_id", "arguments",
        }
        if not isinstance(req, dict) or set(req) != expected:
            raise ValueError("invalid call envelope; no caller-supplied actor field allowed")
        if any(not isinstance(req[k], str) or not 1 <= len(req[k]) <= 512
               for k in expected - {"arguments"}):
            raise ValueError("invalid native invocation identity")
        if req["run_id"] != self.run_id or not isinstance(req["arguments"], dict):
            raise ValueError("cross-run request or non-object arguments")
        prefix, sep, tool = req["operation_id"].partition("/")
        if not sep or prefix != plugin or tool not in self.tools:
            raise ValueError("operation does not match its host-bound endpoint")
        role = self.plugin_roles[plugin]
        identity = (
            plugin, req["invocation_id"], req["firing_id"],
            req["call_id"], tool,
        )
        fields = {"run_id": self.run_id, "agent_role": role,
                  "agent_id": role, "operation_id": req["operation_id"],
                  "invocation_id": req["invocation_id"], "firing_id": req["firing_id"],
                  "call_id": req["call_id"],
                  "tool_name": tool, "tool_args": copy.deepcopy(req["arguments"]),
                  "effect": self.tools[tool]}
        request_event = self.witness.append("requested", **fields)
        request_seq = request_event["sequence"]
        if identity in self._seen:
            self.witness.append("duplicate_transport_rejected", request_sequence=request_seq,
                                previous_status=self._seen[identity], **fields)
            return {"ok": False, "error": "duplicate_native_invocation"}
        # Never suppress a DIFFERENT firing because its business arguments match.
        if self.max_calls is not None and self._native_count >= self.max_calls:
            self._seen[identity] = "budget_rejected"
            self.witness.append("tool_budget_rejected", request_sequence=request_seq, **fields)
            return {"ok": False, "error": "tool_call_budget_exhausted"}
        self._native_count += 1
        self._seen[identity] = "dispatch_intent"
        self._occurrences[tool] = self._occurrences.get(tool, 0) + 1
        inject = (not self._fault_triggered and self.fault.tool == tool
                  and self._occurrences[tool] == self.fault.occurrence)
        if inject and self.fault.mode == "read_error":
            self._fault_triggered = True
            result = "ERROR: injected read failure (diagnostic track)"
            self._seen[identity] = "injected_without_dispatch"
            self.witness.append("fault_read_error", request_sequence=request_seq,
                                tool_result=result, **fields)
            return {"ok": True, "result": result, "request_sequence": request_seq}
        self.witness.append("dispatched", request_sequence=request_seq, **fields)
        previous_role = getattr(bank, "_current_agent_role", None)
        bank._current_agent_role = role
        try:
            # No private SQL mutation, rewritten tool semantics, or retry here.
            result = str(self._dispatch(bank, tool, copy.deepcopy(req["arguments"])))
        except Exception as exc:
            self._seen[identity] = "dispatch_exception"
            self.witness.append("dispatch_exception", request_sequence=request_seq,
                                error_type=type(exc).__name__, **fields)
            return {"ok": False, "error": "dispatch_exception"}
        finally:
            bank._current_agent_role = previous_role
        self._seen[identity] = "backend_returned"
        self.witness.append("backend_returned", request_sequence=request_seq,
                            tool_result=result, **fields)
        if inject and self.fault.mode == "response_lost_after_effect":
            self._fault_triggered = True
            self._seen[identity] = "response_lost"
            self.witness.append("fault_response_lost", request_sequence=request_seq, **fields)
            return None
        self._seen[identity] = "response_sent"
        self.witness.append("response_prepared", request_sequence=request_seq, **fields)
        return {"ok": True, "result": result, "request_sequence": request_seq}

    def close(self):
        if self._thread is None: return
        self._stop.set()
        self._thread.join(timeout=self.socket_timeout + 2)
        if self._thread.is_alive():
            # Do not pretend a timed-out backend stopped, or publish a final snapshot.
            raise RuntimeError("backend is still executing; run evidence is incomplete")
        for _, sock in self._listeners: sock.close()
        self.witness.append("backend_closed", run_id=self.run_id,
                            fault_triggered=self._fault_triggered, fatal_error=self._fatal)
        self.witness.close()
        self._temporary.cleanup()
        self._thread = None
