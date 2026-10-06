"""Fixed private dependencies for candidate reads in an existing Registry cut.

This context owns no writer, opens no connection and grants no authority. Its
consumers must still verify canonical objects and actual bytes. Public readers
return detached data, never this live context.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .event_store import EventStore, RegistryConflict
from .identities import TypedId
from .object_store import ObjectStore
from ._event_store.collaboration_descriptors import descriptor_store


@dataclass(frozen=True, slots=True)
class _CandidateReadContext:
    event_store: EventStore
    catalog: object
    object_store: ObjectStore
    task_id: TypedId
    branch_id: str
    db: sqlite3.Connection

    def __post_init__(self):
        if (type(self.event_store) is not EventStore or type(self.object_store) is not ObjectStore
                or self.catalog is not self.event_store.catalog or self.object_store.catalog is not self.catalog
                or type(self.task_id) is not TypedId or type(self.task_id.kind) is not str
                or self.task_id.kind != 'task' or type(self.task_id.value) is not str
                or type(self.branch_id) is not str):
            raise TypeError('candidate reading requires fixed Registry dependencies')
        object.__setattr__(self, 'task_id', TypedId('task', self.task_id.value))
        self.require_cut()

    def require_cut(self):
        if type(self.db) is not sqlite3.Connection or not self.db.in_transaction:
            raise TypeError('candidate reading requires an existing SQLite cut')
        main = next((row for row in self.db.execute('PRAGMA database_list').fetchall()
            if row['name'] == 'main'), None)
        if (main is None or not main['file']
                or Path(main['file']).resolve() != self.event_store.path.resolve()
                or self.object_store.root.resolve() != (self.event_store.path.parent / 'objects').resolve()):
            raise RegistryConflict('candidate reading belongs to another Registry')
        identity = dict(self.db.execute("SELECT key,value FROM registry_meta WHERE key IN ('task_id','branch_id')"))
        if identity != {'task_id': str(self.task_id), 'branch_id': self.branch_id}:
            raise RegistryConflict('candidate read identity differs from its Registry')

    @classmethod
    def from_core(cls, core, db):
        from ._registry import _RegistryCore
        if type(core) is not _RegistryCore:
            raise TypeError('candidate reading requires the owning Registry Core')
        return cls(core.event_store, core.catalog, core.object_store, core.task_id, core.branch_id, db)

    @classmethod
    def from_event_store(cls, event_store, db, *, task_id, branch_id):
        if type(event_store) is not EventStore:
            raise TypeError('candidate reading requires the owning EventStore')
        return cls(event_store, event_store.catalog, descriptor_store(event_store), task_id, branch_id, db)
