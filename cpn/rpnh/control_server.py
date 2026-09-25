"""Single execution-owner dispatch for local commands and HOST completions.

Only the supplied owner has Registry write authority. Socket clients never
construct a writer. Production workers may wait for gateway replies while the
owner keeps dispatching commands; offline checks dispatch finite events only.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import queue
import selectors
import socket
from concurrent.futures import Future
from contextvars import copy_context
from typing import Callable, Mapping

from .control_client import ControlProtocolError, json_data


# Bound one client frame; this is transport admission, not Registry authority.
MAX_COMMAND_BYTES = 16 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlProtocolError("command repeats a JSON field")
        result[key] = value
    return result


def parse_command(frame: bytes):
    if (not isinstance(frame, bytes) or len(frame) > MAX_COMMAND_BYTES
            or not frame.endswith(b"\n") or frame.count(b"\n") != 1):
        raise ControlProtocolError("command must be exactly one newline JSON frame")
    value = json_data(json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_object))
    if not isinstance(value, dict) or set(value) != {"command_id", "command", "arguments"}:
        raise ControlProtocolError("command requires command_id, command, arguments")
    if any(not isinstance(value[key], str) or not value[key] for key in ("command_id", "command")):
        raise ControlProtocolError("command identities must be nonempty strings")
    if not isinstance(value["arguments"], dict):
        raise ControlProtocolError("command arguments must be an object")
    return value


@dataclass(frozen=True)
class HostEvent:
    execute: Callable
    reply: Future


class OwnerEventLoop:
    """One writer thread; completions and commands share the same dispatcher."""

    def __init__(self, owner, socket_path: str | Path):
        self.owner = owner
        self.socket_path = Path(socket_path)
        self.selector = selectors.DefaultSelector()
        self.pending = queue.SimpleQueue()
        self.buffers = {}
        self.outputs = {}
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Bind fails rather than replacing another owner's channel.
        self.listener.bind(str(self.socket_path))
        self._socket_identity = self.socket_path.stat().st_ino
        self.listener.setblocking(False)
        self.listener.listen()
        self.wake_reader, self.wake_writer = socket.socketpair()
        self.wake_reader.setblocking(False)
        self.wake_writer.setblocking(False)
        self.selector.register(self.listener, selectors.EVENT_READ, "accept")
        self.selector.register(self.wake_reader, selectors.EVENT_READ, "host")

    def submit_host(self, execute: Callable) -> Future:
        """Enqueue trusted HOST work; it executes only in the owning dispatcher."""
        if not callable(execute):
            raise TypeError("HOST dispatch requires an explicitly supplied callable")
        reply = Future()
        # This is a receipt for accepted HOST work, not an owner-stop handle.
        # Dropping/cancelling a wait must not revoke an admitted publication or
        # completion, nor raise InvalidStateError in the sole writer dispatcher.
        # Claim it before exposing it to other threads; the owner still executes
        # the queued callable exactly once, as before.
        reply.set_running_or_notify_cancel()
        self.pending.put(HostEvent(execute, reply))
        try:
            self.wake_writer.send(b"\x01")
        except BlockingIOError:
            pass  # An unread wake already makes the queue dispatchable.
        return reply

    def watch_completion(self, future: Future, on_complete: Callable) -> None:
        """A finished model/operation queues owner work, never writes from a worker."""
        future.add_done_callback(lambda finished:
            self.submit_host(lambda: on_complete(finished)))

    def dispatch_command(self, command):
        identity = command["command_id"]
        name, arguments = command["command"], command["arguments"]
        if name == "snapshot":
            if arguments:
                raise ControlProtocolError("snapshot takes no arguments")
            return {"command_id": identity, "status": "OK", "result": self.owner.snapshot()}
        if name == "diagnose":
            from .diagnostics import diagnose
            if set(arguments) - {"analyzer_keys"}:
                raise ControlProtocolError("unknown diagnose argument")
            result = diagnose(self.owner.snapshot(), self.owner.registration,
                analyzer_keys=arguments.get("analyzer_keys"))
            return {"command_id": identity, "status": "OK", "result": result}
        # Owner commands are implemented by the mechanical command service,
        # not by arbitrary JSON callbacks/import locators or this transport.
        result = self.owner.command(name, arguments, command_id=identity)
        return {"command_id": identity, "status": result["status"], "result": result}

    def _dispatch_host(self):
        try:
            self.wake_reader.recv(65536)
        except BlockingIOError:
            pass
        while not self.pending.empty():
            event = self.pending.get_nowait()
            try:
                event.reply.set_result(event.execute())
            except Exception as exc:
                event.reply.set_exception(exc)

    def _close_client(self, channel):
        self.selector.unregister(channel)
        self.buffers.pop(channel, None)
        self.outputs.pop(channel, None)
        channel.close()

    def dispatch_ready(self, *, timeout=None):
        """One readiness batch, also usable as a finite synchronous boundary."""
        for key, mask in self.selector.select(timeout):
            channel, action = key.fileobj, key.data
            if action == "accept":
                client, _ = channel.accept()
                client.setblocking(False)
                self.buffers[client] = bytearray()
                self.selector.register(client, selectors.EVENT_READ, "client")
            elif action == "host":
                self._dispatch_host()
            elif mask & selectors.EVENT_WRITE:
                data = self.outputs[channel]
                try:
                    count = channel.send(data)
                except BlockingIOError:
                    # Readiness is advisory. Keep the unsent bytes and let
                    # selector readiness schedule the next write attempt.
                    continue
                except (BrokenPipeError, ConnectionResetError):
                    # A one-shot observer can time out/close while the owner
                    # prepares its reply. Its lost reply must not exit the
                    # sole dispatcher or strand workers awaiting HOST work.
                    self._close_client(channel)
                    continue
                if count == len(data):
                    self._close_client(channel)
                else:
                    self.outputs[channel] = data[count:]
            else:
                try:
                    data = channel.recv(65536)
                except BlockingIOError:
                    continue
                except ConnectionResetError:
                    self._close_client(channel)
                    continue
                if not data:
                    self._close_client(channel)
                    continue
                self.buffers[channel].extend(data)
                if len(self.buffers[channel]) > MAX_COMMAND_BYTES:
                    self._close_client(channel)
                    continue
                if b"\n" not in self.buffers[channel]:
                    continue
                try:
                    command = parse_command(bytes(self.buffers[channel]))
                except (ValueError, UnicodeError, RecursionError):
                    # An invalid frame has no trusted command identity to reply
                    # to. Close only this client; never stop the sole owner or
                    # echo untrusted/private payloads into diagnostic output.
                    self._close_client(channel)
                    continue
                try:
                    reply = self.dispatch_command(command)
                except Exception as exc:
                    reply = {"command_id": command["command_id"], "status": "ERROR",
                        "result": {"reason": f"{type(exc).__name__}: {exc}"}}
                try:
                    self.outputs[channel] = (json.dumps(json_data(reply), ensure_ascii=False,
                        allow_nan=False) + "\n").encode("utf-8")
                except (ValueError, TypeError, RecursionError) as exc:
                    # The command may already have committed. Isolate its lost
                    # reply, never replay it or let encoding failure kill HOST
                    # completion dispatch. Do not log the invalid payload.
                    _LOGGER.warning("owner_reply_serialization_failed error_type=%s",
                                    type(exc).__name__)
                    self._close_client(channel)
                    continue
                self.selector.modify(channel, selectors.EVENT_WRITE, "client")

    def run(self):
        """Production dispatch remains interactive even with a dead marking."""
        while True:
            self.dispatch_ready()

    def close(self):
        """Close this local transport, not a firing, provider request or Registry."""
        for channel in tuple(self.buffers):
            self._close_client(channel)
        self.selector.close()
        self.listener.close()
        self.wake_reader.close()
        self.wake_writer.close()
        if self.socket_path.exists() and self.socket_path.stat().st_ino == self._socket_identity:
            self.socket_path.unlink()


class RegistryGateway:
    """Explicit HOST-bound publication/read methods, never settlement authority.

    Implementations are bound by the owner, not JSON. The external executor
    receives these normal operation/resource/provider APIs only; Start/Success,
    graph adoption and run terminal publication remain owner-only.
    """

    _OWNER_ONLY = frozenset({"succeed_module_operation", "commit_firing_success",
        "adopt_net", "publish_run_terminal", "publish_run_terminal_evidence",
        "command", "start_run"})

    def __init__(self, loop: OwnerEventLoop, methods: Mapping[str, Callable]):
        if not isinstance(methods, Mapping) or any(not isinstance(k, str) or not callable(v)
                for k, v in methods.items()) or self._OWNER_ONLY.intersection(methods):
            raise ValueError("gateway bindings require explicit non-owner HOST methods")
        self._loop = loop
        self._methods = dict(methods)

    def submit(self, name, *args, **kwargs):
        """Enqueue one explicitly bound HOST operation without blocking.

        The same owner dispatcher executes both this Python call and the
        synchronous worker ABI below. No client or future acquires write power.
        """
        if name not in self._methods:
            raise AttributeError(name)
        method = self._methods[name]
        context = copy_context()
        return self._loop.submit_host(lambda: context.run(method, *args, **kwargs))

    def __getattr__(self, name):
        if name not in self._methods:
            raise AttributeError(name)
        def invoke(*args, **kwargs):
            # Capture at each worker request, not at gateway construction.
            # Default executors scope an immutable prepared request in their
            # own thread; owner dispatch must observe that same HOST context
            # while still restoring its ambient context after this event.
            return self.submit(name, *args, **kwargs).result()
        return invoke


__all__ = ("OwnerEventLoop", "RegistryGateway", "parse_command")
