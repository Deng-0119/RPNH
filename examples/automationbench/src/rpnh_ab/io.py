"""Strict JSON, immutable evidence, atomic checkpoints, and local framing."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
from datetime import datetime, timezone
from .constants import FRAME_BYTES


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(value) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(dumps(value) + "\n")
        f.flush()
        os.fsync(f.fileno())


def replace_checkpoint(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(value) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def append(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps(value) + "\n")
        f.flush()
        os.fsync(f.fileno())


def receive_exact(conn: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        part = conn.recv(length - len(result))
        if not part:
            raise EOFError("incomplete local bridge frame")
        result.extend(part)
    return bytes(result)


def send(conn: socket.socket, value) -> None:
    data = dumps(value).encode("utf-8")
    if len(data) > FRAME_BYTES:
        raise ValueError("frame exceeds native transport capacity; never truncated")
    conn.sendall(struct.pack("!I", len(data)) + data)


def receive(conn: socket.socket):
    length = struct.unpack("!I", receive_exact(conn, 4))[0]
    if length > FRAME_BYTES:
        raise ValueError("frame exceeds native transport capacity; never truncated")
    return json.loads(receive_exact(conn, length))
