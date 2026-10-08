"""TEST ONLY: replace denied socket transport with an OS-pipe wake channel.

Registry, HOST gateway, admission, completion dispatch and Harness are unchanged.
This is not native AF_UNIX/client acceptance. No ready list or business state
lives here. All inherited submit_host/watch_completion/_dispatch_host semantics
remain in cpn.rpnh.control_server.OwnerEventLoop.
"""
import os
from pathlib import Path
import queue
import selectors

from cpn.rpnh.control_server import OwnerEventLoop


class _PipeEnd:
    def __init__(self, fd):
        self.fd = fd
        os.set_blocking(fd, False)

    def fileno(self):
        return self.fd

    def send(self, data):
        return os.write(self.fd, data)

    def recv(self, size):
        return os.read(self.fd, size)

    def close(self):
        os.close(self.fd)


class PipeOwnerEventLoop(OwnerEventLoop):
    def __init__(self, owner, socket_path):
        self.owner = owner
        self.socket_path = Path(socket_path)
        self.selector = selectors.DefaultSelector()
        self.pending = queue.SimpleQueue()
        self.buffers, self.outputs = {}, {}
        read_fd, write_fd = os.pipe()
        self.wake_reader, self.wake_writer = _PipeEnd(read_fd), _PipeEnd(write_fd)
        self.selector.register(self.wake_reader, selectors.EVENT_READ, "host")

    def close(self):
        self.selector.close()
        self.wake_reader.close()
        self.wake_writer.close()
