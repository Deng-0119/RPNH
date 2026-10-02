"""External effect witness. This journal is NOT a substitute Registry."""
from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from typing import Any
from .constants import SCHEMA
from .jsonio import dumps, loads


class Witness:
    def __init__(self, path: Path):
        if path.exists(): raise FileExistsError("witness path must be fresh")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stream = path.open("x", encoding="utf-8")
        os.chmod(path, 0o600)
        self._lock = threading.Lock()
        self._seq = 0

    def append(self, kind: str, **fields: Any) -> dict:
        if any(k in fields for k in ("schema_version", "sequence", "timestamp", "kind")):
            raise ValueError("reserved witness field")
        with self._lock:
            event = {"schema_version": SCHEMA, "sequence": self._seq + 1,
                     "timestamp": datetime.now(timezone.utc).isoformat(),
                     "kind": kind, **fields}
            raw = dumps(event)
            self._stream.write(raw + "\n")
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._seq += 1
            return event

    def close(self):
        with self._lock:
            self._stream.close()


def read_events(path: Path) -> list[dict]:
    events = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        item = loads(line)
        if item.get("sequence") != lineno or item.get("schema_version") != SCHEMA:
            raise ValueError("incomplete or inconsistent witness sequence")
        events.append(item)
    return events
