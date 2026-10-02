"""Strict JSON, durable evidence files, and bounded single-frame IPC."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
from typing import Any

MAX_FRAME_BYTES = 4 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(text):
    raise ValueError(f"non-finite JSON constant: {text}")


def loads(raw: str | bytes) -> Any:
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def dumps(value: Any) -> str:
    # Do not coerce non-string keys or arbitrary Python objects.
    def check(v):
        if isinstance(v, dict):
            if any(type(k) is not str for k in v):
                raise ValueError("JSON object keys must be strings")
            for x in v.values(): check(x)
        elif isinstance(v, (list, tuple)):
            for x in v: check(x)
        elif type(v) is float and not math.isfinite(v):
            raise ValueError("JSON numbers must be finite")
        elif v is not None and type(v) not in (str, int, float, bool):
            raise TypeError("value is not JSON data")
    check(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def read(path: Path) -> Any:
    return loads(path.read_bytes())


def write_new(path: Path, value: Any) -> None:
    """Never overwrite a result from an earlier attempt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(dumps(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_replace(path: Path, value: Any) -> None:
    """Atomically replace mutable operational status, never final evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(dumps(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def recv_frame(sock, limit: int = MAX_FRAME_BYTES) -> Any:
    data = bytearray()
    while True:
        chunk = sock.recv(min(65536, limit + 1 - len(data)))
        if not chunk:
            raise EOFError("peer closed without a complete response")
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("IPC frame exceeds limit")
        if b"\n" in data:
            line, tail = bytes(data).split(b"\n", 1)
            if tail:
                raise ValueError("multiple frames on one connection are forbidden")
            return loads(line)


def send_frame(sock, value: Any, limit: int = MAX_FRAME_BYTES) -> None:
    raw = dumps(value).encode("utf-8") + b"\n"
    if len(raw) > limit:
        raise ValueError("IPC frame exceeds limit")
    sock.sendall(raw)
