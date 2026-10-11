"""Operation-local immutable-body accounting, separate from read authority.

Counts unique input bytes, not peak heap, repeated I/O, or concurrent operations.
Nothing is retained on the session, canonical snapshot, or Registry.
"""
from contextlib import contextmanager

from .registry_typed_readers import TypedReadError, wire_ref


class _ProjectionReadBudget:
    def __init__(self, snapshot):
        self.max_body_bytes = getattr(snapshot, 'max_material_bytes', 4 * 1024 * 1024)
        closure = getattr(snapshot, 'max_material_closure_bytes', None)
        self.max_closure_bytes = self.max_body_bytes if closure is None else closure
        if any(type(value) is not int or value < 1
               for value in (self.max_body_bytes, self.max_closure_bytes)):
            raise TypedReadError('LIMIT_EXCEEDED')
        self.total_bytes = 0
        self._sizes = {}
        self.closed = False

    def admit(self, reference, prepared):
        if self.closed:
            raise TypedReadError('LIMIT_EXCEEDED')
        size = prepared.size
        if type(size) is not int or size < 0:
            raise TypedReadError('INTEGRITY_FAILED')
        if size > self.max_body_bytes:
            raise TypedReadError('MATERIAL_TOO_LARGE')
        source, ref = wire_ref(reference)
        key = (source, ref['entity_type'], ref['logical_id'], ref['version_id'])
        if key in self._sizes:
            if self._sizes[key] != size:
                raise TypedReadError('INTEGRITY_FAILED')
            return
        if self.total_bytes + size > self.max_closure_bytes:
            raise TypedReadError('LIMIT_EXCEEDED')
        self._sizes[key] = size
        self.total_bytes += size

    def close(self):
        self.closed = True
        self._sizes.clear()


class _ProjectionSnapshot:
    __slots__ = ('_snapshot', '_projection_material_budget')

    def __init__(self, snapshot, budget):
        self._snapshot = snapshot
        self._projection_material_budget = budget

    def __getattr__(self, name):
        return getattr(self._snapshot, name)


@contextmanager
def projection_read_scope(snapshot):
    existing = getattr(snapshot, '_projection_material_budget', None)
    if existing is not None:
        yield snapshot
        return
    budget = _ProjectionReadBudget(snapshot)
    try:
        yield _ProjectionSnapshot(snapshot, budget)
    finally:
        budget.close()
