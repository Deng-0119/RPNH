"""Test-only owner wake transport for sessions without AF_UNIX sockets.

SessionPipeOwnerEventLoop inherits every owner dispatch/command method. Only
construction of the external command socket and wake-up socket pair is replaced
by an OS pipe. Tests using it do not prove native owner-socket integration.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import selectors
from types import SimpleNamespace

from cpn.rpnh.control_server import OwnerEventLoop


class _PipeChannel:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        os.set_blocking(descriptor, False)

    def fileno(self):
        return self.descriptor

    def send(self, data):
        return os.write(self.descriptor, data)

    def recv(self, count):
        return os.read(self.descriptor, count)

    def close(self):
        os.close(self.descriptor)


class SessionPipeOwnerEventLoop(OwnerEventLoop):
    def __init__(self, owner, socket_path):
        self.owner = owner
        self.socket_path = Path(socket_path)
        self.selector = selectors.DefaultSelector()
        self.pending = queue.SimpleQueue()
        self.buffers, self.outputs = {}, {}
        self.listener = SimpleNamespace(close=lambda: None)
        self._socket_identity = None
        reader, writer = os.pipe()
        self.wake_reader, self.wake_writer = _PipeChannel(reader), _PipeChannel(writer)
        self.selector.register(self.wake_reader, selectors.EVENT_READ, "host")

