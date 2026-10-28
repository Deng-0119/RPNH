"""Bounded process-local storage for immutable exact object rows.

The shared budget is not shared source authority: each EventStore has its own
namespace, including readers of the same file. No miss, visibility decision or
publication proof is cached here. Byte counts estimate retained Python objects;
they are not a measurement or bound of process RSS.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import sqlite3
import sys
import threading


@dataclass(frozen=True)
class StoreNamespace:
    source_path: str
    instance: object = field(default_factory=object)


class ExactReadCache:
    """LRU with one entries/estimated-bytes budget across all source namespaces."""

    def __init__(self, *, max_entries: int = 4096,
                 max_estimated_bytes: int = 32 * 1024 * 1024) -> None:
        if (type(max_entries) is not int or max_entries < 0
                or type(max_estimated_bytes) is not int
                or max_estimated_bytes < 0):
            raise ValueError("cache budgets must be nonnegative integers")
        self.max_entries = max_entries
        self.max_estimated_bytes = max_estimated_bytes
        self._entries: OrderedDict[
            tuple[StoreNamespace, str], tuple[sqlite3.Row, int]] = OrderedDict()
        self._estimated_bytes = 0
        self._lock = threading.RLock()

    @staticmethod
    def estimated_entry_bytes(namespace: StoreNamespace, version: str,
                              row: sqlite3.Row) -> int:
        # Only native SQLite values/column names enter this cache. Counting
        # shared strings repeatedly intentionally overestimates retained data.
        # Fixed allowance covers the OrderedDict node and entry bookkeeping.
        values = tuple(row)
        names = row.keys()
        return (256 + sys.getsizeof((namespace, version))
                + sys.getsizeof(namespace) + sys.getsizeof(namespace.instance)
                + sys.getsizeof(namespace.source_path) + sys.getsizeof(version)
                + sys.getsizeof(row) + sys.getsizeof(values)
                + sum(sys.getsizeof(value) for value in values)
                + sys.getsizeof(names)
                + sum(sys.getsizeof(name) for name in names))

    def get(self, namespace: StoreNamespace, version: str) -> sqlite3.Row | None:
        key = (namespace, version)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(self, namespace: StoreNamespace, version: str,
            row: sqlite3.Row) -> sqlite3.Row:
        if not isinstance(row, sqlite3.Row):
            raise TypeError("exact read cache accepts native SQLite rows only")
        size = self.estimated_entry_bytes(namespace, version, row)
        if self.max_entries == 0 or size > self.max_estimated_bytes:
            return row
        key = (namespace, version)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                return existing[0]
            while self._entries and (
                    len(self._entries) >= self.max_entries
                    or self._estimated_bytes + size > self.max_estimated_bytes):
                _, (_, removed_size) = self._entries.popitem(last=False)
                self._estimated_bytes -= removed_size
            self._entries[key] = (row, size)
            self._estimated_bytes += size
        return row

    def clear_namespace(self, namespace: StoreNamespace) -> None:
        """Drop current entries; a concurrent later read may populate anew."""
        with self._lock:
            for key in tuple(self._entries):
                if key[0] == namespace:
                    _, size = self._entries.pop(key)
                    self._estimated_bytes -= size

    def statistics(self) -> dict[str, int]:
        """Return detached accounting, without source paths or object bodies."""
        with self._lock:
            return {"entries": len(self._entries),
                    "estimated_bytes": self._estimated_bytes,
                    "max_entries": self.max_entries,
                    "max_estimated_bytes": self.max_estimated_bytes}


PROCESS_EXACT_READ_CACHE = ExactReadCache()
