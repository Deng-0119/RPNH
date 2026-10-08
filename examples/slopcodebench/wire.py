"""Bounded local IPC framing, never exposed inside the solver container."""
import json
import struct

from .contracts import canonical

FRAME_LIMIT = 16 * 1024 * 1024


def receive_exact(connection, length):
    data = bytearray()
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise EOFError("incomplete local command frame")
        data.extend(part)
    return bytes(data)


def send(connection, value):
    payload = canonical(value)
    if len(payload) > FRAME_LIMIT:
        raise ValueError("command frame exceeds declared limit")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def receive(connection):
    length, = struct.unpack("!I", receive_exact(connection, 4))
    if length > FRAME_LIMIT:
        raise ValueError("command frame exceeds declared limit")
    return json.loads(receive_exact(connection, length))
