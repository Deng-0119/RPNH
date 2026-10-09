"""Query-owned, bounded scratch and validation caches for private origin reads.

This context never grants access. Only an already-authorized reader/query
attaches it through a shallow snapshot copy; captured session snapshots remain
unchanged, and close detaches the query-owned copy.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace

from ._snapshot_producer_proof import _ProofReservation
from .registry_read_contracts import RegistryReadSessionError


def _bytes_bound(value):
    """Conservative canonical-byte bound, without building a serialized copy."""
    if value is None or type(value) is bool:
        return 5
    if isinstance(value, str):
        return 12 * len(value) + 2
    if isinstance(value, bytes):
        return len(value)
    if type(value) in (int, float):
        return 64 + (value.bit_length() if type(value) is int else 0)
    if isinstance(value, Mapping):
        return 2 + sum(_bytes_bound(key) + _bytes_bound(item) + 2 for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return 2 + sum(_bytes_bound(item) + 1 for item in value)
    raise RegistryReadSessionError('INTEGRITY_FAILED')


class _OriginQueryContext:
    def __init__(self, session, snapshot, *, retained_extra=None):
        self._session = session
        self._retained_extra = retained_extra
        self._initial_retained_bytes = session._retained_bytes()
        self.reservation = _ProofReservation(session.limits, self._initial_retained_bytes)
        self.reserve(size=256)
        self.snapshot = replace(snapshot, _origin_context=self)
        self._cache = {}
        self._event_index = None

    def close(self):
        self._cache.clear()
        self._event_index = None
        self.reservation.release(self.reservation.scratch_bytes)
        self.snapshot = None
        self._session = None
        self._retained_extra = None

    @staticmethod
    def bytes_bound(value):
        return _bytes_bound(value)

    def reserve(self, category=None, *, rows=0, size=0):
        # A trusted callback can legitimately retain another cut/cursor. Keep
        # those live bytes in the same ledger before the next allocation.
        extra = 0 if self._retained_extra is None else self._retained_extra()
        if type(extra) is not int or extra < 0:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        self.reservation.retained_bytes = max(self._initial_retained_bytes, self._session._retained_bytes()) + extra
        self.reservation.reserve(category, rows=rows, size=size)

    def retain(self, value):
        size = self.bytes_bound(value)
        self.reserve(size=size)
        return size

    @contextmanager
    def scratch(self, value=None, *, size=None):
        amount = self.bytes_bound(value) if size is None else size
        self.reserve(size=amount)
        try:
            yield
        except BaseException:
            # A failed validator's traceback can still own temporary buffers
            # during protected-error delivery. Keep those bytes charged until
            # close(); only successful scope exit establishes scratch release.
            raise
        else:
            self.reservation.release(amount)

    def cached(self, namespace, key, contract, loader, *, category='D'):
        identity = (self.snapshot.source_id, namespace, key, contract)
        if identity in self._cache:
            return self._cache[identity]
        # The result's allocation is reserved by the loader before it occurs.
        # Failed validations are never marked as cached successful results.
        self.reserve(category, rows=0 if category is None else 1, size=self.bytes_bound(identity) + 128)
        value = loader()
        self._cache[identity] = value
        return value

    def validate_event(self, event, contract, validator):
        return self.cached('event', (str(event.event_id), id(event)), contract,
            lambda: validator(event), category='E')

    def validate_events(self, events, contract, validator):
        """Charge every uncached candidate before validating the first one."""
        self.reserve(size=256 + 128 * len(events))
        pending = []
        for event in events:
            identity = (self.snapshot.source_id, 'event', (str(event.event_id), id(event)), contract)
            if identity not in self._cache:
                self.reserve('E', rows=1, size=self.bytes_bound(identity) + 128)
            pending.append((identity, event))
        results = []
        for identity, event in pending:
            if identity not in self._cache:
                self._cache[identity] = validator(event)
            results.append(self._cache[identity])
        return tuple(results)

    def semantic_array(self, values, category, contract, validator):
        """Cache an actual whole-array validation, including every position."""
        key = (id(values), category, contract)
        identity = (self.snapshot.source_id, 'array', key)
        if identity in self._cache:
            return self._cache[identity][1]
        self.reserve(category, rows=len(values), size=self.bytes_bound(identity) + 128)
        result = validator(values)
        # Hold the actual array too: an ephemeral caller array must not be
        # collected and have its identity recycled into an unrelated cache hit.
        self._cache[identity] = (values, result)
        return result

    def _index_events(self):
        if self._event_index is not None:
            return self._event_index
        by_id, by_transaction, by_type, by_checkpoint = {}, {}, {}, {}
        # Index only references into the captured snapshot, never copy payloads.
        # Reserve each insertion before allocating its key or candidate list.
        for event in self.snapshot.events:
            self.reserve(size=768 + sum(self.bytes_bound(str(value)) for value in
                (event.event_id, event.transaction_id, event.event_type)))
            by_id.setdefault(str(event.event_id), []).append(event)
            by_type.setdefault(event.event_type, []).append(event)
            schema_type = (event.payload_schema_ref[len('registry_v1/'):]
                if isinstance(event.payload_schema_ref, str) and event.payload_schema_ref.startswith('registry_v1/') else None)
            if schema_type is not None and schema_type != event.event_type:
                self.reserve(size=self.bytes_bound(schema_type) + 128)
                by_type.setdefault(schema_type, []).append(event)
            if 'transaction_committed/v1' in (event.event_type, schema_type):
                by_transaction.setdefault(str(event.transaction_id), []).append(event)
            if 'marking_checkpoint_committed/v1' in (event.event_type, schema_type):
                ref = event.payload.get('checkpoint_ref')
                if isinstance(ref, Mapping):
                    self.retain(ref)
                    key = tuple(ref.get(name) for name in ('entity_type', 'logical_id', 'version_id'))
                    if all(isinstance(value, str) for value in key):
                        by_checkpoint.setdefault(key, []).append(event)
        self.reserve(size=64 * len(self.snapshot.events) + 256)
        self._event_index = tuple({key: tuple(values) for key, values in mapping.items()}
            for mapping in (by_id, by_transaction, by_type, by_checkpoint))
        return self._event_index

    @property
    def events_by_id(self):
        return self._index_events()[0]

    @property
    def commits_by_transaction(self):
        return self._index_events()[1]

    def events_labelled(self, event_type):
        return self._index_events()[2].get(event_type, ())

    def checkpoint_events(self, ref):
        key = tuple(ref.get(name) for name in ('entity_type', 'logical_id', 'version_id'))
        return self._index_events()[3].get(key, ())
