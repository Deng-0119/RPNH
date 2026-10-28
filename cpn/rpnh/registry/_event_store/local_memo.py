"""Bounded lifecycle of existing source-local positive transaction proofs.

This container does not establish proof eligibility or validity. Its caller
must keep the original read-only gate and perform verification before add().
"""
from collections import OrderedDict
from collections.abc import MutableMapping
from copy import deepcopy
import sys
import threading


class BoundedProofSet:
    def __init__(self, *, max_entries=4096, max_estimated_bytes=1024 * 1024):
        if (type(max_entries) is not int or max_entries < 0
                or type(max_estimated_bytes) is not int or max_estimated_bytes < 0):
            raise ValueError('proof budgets must be nonnegative integers')
        self.max_entries = max_entries
        self.max_estimated_bytes = max_estimated_bytes
        self._entries = OrderedDict()
        self._estimated_bytes = 0
        self._lock = threading.RLock()

    def __contains__(self, key):
        with self._lock:
            if key not in self._entries:
                return False
            self._entries.move_to_end(key)
            return True

    def add(self, key):
        if type(key) is not str:
            raise TypeError('transaction proof key must be a native string')
        if not key:
            return
        size = sys.getsizeof(key) + 128
        if not self.max_entries or size > self.max_estimated_bytes:
            return
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return
            while self._entries and (len(self._entries) >= self.max_entries
                    or self._estimated_bytes + size > self.max_estimated_bytes):
                _, removed = self._entries.popitem(last=False)
                self._estimated_bytes -= removed
            self._entries[key] = size
            self._estimated_bytes += size

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._estimated_bytes = 0

    def statistics(self):
        with self._lock:
            return {'entries': len(self._entries), 'estimated_bytes': self._estimated_bytes,
                    'max_entries': self.max_entries, 'max_estimated_bytes': self.max_estimated_bytes}


class EntryBoundedMemo(MutableMapping):
    """Source-local LRU preserving value aliases; entry bound, NOT byte bound."""
    def __init__(self, max_entries=128):
        if type(max_entries) is not int or max_entries < 0:
            raise ValueError('memo entry budget must be a nonnegative integer')
        self.max_entries = max_entries
        self._entries = OrderedDict()
        self._lock = threading.RLock()

    def __getitem__(self, key):
        with self._lock:
            value = self._entries[key]
            self._entries.move_to_end(key)
            return value

    def get(self, key, default=None):
        with self._lock:
            if key not in self._entries:
                return default
            self._entries.move_to_end(key)
            return self._entries[key]

    def __setitem__(self, key, value):
        with self._lock:
            if not self.max_entries:
                return
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __delitem__(self, key):
        with self._lock:
            del self._entries[key]

    def __iter__(self):
        with self._lock:
            return iter(tuple(self._entries))

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __deepcopy__(self, memo):
        result = type(self)(self.max_entries)
        memo[id(self)] = result
        with self._lock:
            result._entries = deepcopy(self._entries, memo)
        return result
