"""Bounded memo of successful pure catalog parses, never runtime authority."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import sys
from threading import RLock
from typing import Any, Callable


def _estimated_size(value: Any, seen: set[int] | None = None) -> int:
    """Estimated retained Python bytes, not an RSS measurement."""
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    total = sys.getsizeof(value)
    if isinstance(value, dict):
        total += sum(_estimated_size(k, seen) + _estimated_size(v, seen)
                     for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        total += sum(_estimated_size(item, seen) for item in value)
    return total


class ParsedCatalogCache:
    """Exact built-in bytes keys; callers always receive independent documents."""

    def __init__(self, *, max_entries: int = 32,
                 max_estimated_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_entries = max_entries
        self.max_estimated_bytes = max_estimated_bytes
        self._entries: OrderedDict[bytes, tuple[Any, int]] = OrderedDict()
        self._estimated_bytes = 0
        self._lock = RLock()

    def parse(self, payload: bytes, parser: Callable[[bytes], Any]) -> Any:
        # Preserve old behavior for bytes subclasses, including overridden decode.
        if (type(payload) is not bytes or not payload
                or len(payload) > self.max_estimated_bytes
                or self.max_entries <= 0):
            return parser(payload)
        with self._lock:
            cached = self._entries.get(payload)
            if cached is not None:
                self._entries.move_to_end(payload)
                return deepcopy(cached[0])
        # Failures are never stored. Parsing outside the lock avoids serializing
        # unrelated catalogs; concurrent duplicate work is harmless pure work.
        parsed = parser(payload)
        # Include conservative bookkeeping allowance for the LRU node,
        # entry tuple, and size integer; still an estimate, not RSS.
        estimated = _estimated_size((payload, parsed)) + 256
        if estimated > self.max_estimated_bytes:
            return parsed
        with self._lock:
            old = self._entries.pop(payload, None)
            if old is not None:
                self._estimated_bytes -= old[1]
            self._entries[payload] = (parsed, estimated)
            self._estimated_bytes += estimated
            while (len(self._entries) > self.max_entries
                   or self._estimated_bytes > self.max_estimated_bytes):
                _key, (_value, size) = self._entries.popitem(last=False)
                self._estimated_bytes -= size
            # The stored parse is never returned directly, even on first use.
            return deepcopy(parsed)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._estimated_bytes = 0


PARSED_CATALOG_CACHE = ParsedCatalogCache()
