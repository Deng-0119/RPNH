"""Explicit, owner-published SourceSet versions, independent of task execution.

Membership and access-path names describe an expected scope. They grant no
permission and never locate a Registry. Only a host-supplied reader resolves a
source, then the existing ResourceService validates its authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

from ..registry.models import VersionRef
from ..registry.identities import TypedId
from ..registry.event_store import RegistryConflict
from ..registry.object_store import ObjectIntegrityError
from ..registry.schema_catalog import canonical_json, canonical_text
from .references import SourceQualifiedVersionRef, _require_source_id
from .authoring import _exact_object_ref
from .sources import get_local_source_identity

SOURCE_SET_TYPE = 'collaboration_source_set/v1'
SOURCE_SET_SCHEMA = 'registry_v1/' + SOURCE_SET_TYPE
LINK_KINDS = ('immutable_snapshot', 'editable_fork', 'exclusive_capability', 'control_message')


def command_key(command_id):
    _require_source_id(command_id)
    return 'collaboration-source-set:' + canonical_text({'command_id': command_id})


def version_id(task_id, command_id):
    return TypedId('resource_version', uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        'kind': 'source-set-version', 'task': str(task_id), 'command': command_id})).hex)


def set_id(task_id, command_id):
    return TypedId('resource', uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        'kind': 'source-set', 'task': str(task_id), 'command': command_id})).hex)


@dataclass(frozen=True, slots=True)
class SourceMember:
    source_ref: SourceQualifiedVersionRef
    access_paths: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    link_kind: str = 'immutable_snapshot'

    def __post_init__(self):
        _exact_object_ref(self.source_ref, entity_type='task/v1', logical_kind='task', version_kind='task_version')
        for field in ('access_paths', 'aliases'):
            values = getattr(self, field)
            if type(values) is not tuple or len(set(values)) != len(values):
                raise ValueError('source paths and aliases must be unique tuples')
            for value in values:
                _require_source_id(value)
        if not self.access_paths or self.link_kind not in LINK_KINDS:
            raise ValueError('a source requires explicit access paths and a known link kind')

    def to_dict(self):
        return {'source_ref': self.source_ref.to_dict(), 'access_paths': list(self.access_paths),
                'aliases': list(self.aliases), 'link_kind': self.link_kind}

    @classmethod
    def from_dict(cls, value, catalog):
        return cls(SourceQualifiedVersionRef.from_dict(value['source_ref'], catalog=catalog),
                   tuple(value['access_paths']), tuple(value['aliases']), value['link_kind'])


def canonical_members(members):
    """Deduplicate aliases only after exact identity/path agreement, never grants."""
    found = {}
    for member in members:
        if not isinstance(member, SourceMember):
            raise TypeError('expected SourceMember')
        key = member.source_ref.source_id
        previous = found.get(key)
        if previous is not None:
            if (previous.source_ref != member.source_ref or previous.access_paths != member.access_paths
                    or previous.link_kind != member.link_kind):
                raise ValueError('same source has conflicting identity, paths or link kind')
            member = SourceMember(member.source_ref, member.access_paths,
                                  tuple(sorted(set(previous.aliases + member.aliases))), member.link_kind)
        found[key] = member
    return tuple(found[key] for key in sorted(found))


@dataclass(frozen=True, slots=True)
class SourceSetVersion:
    source_set_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    publisher_bootstrap_ref: SourceQualifiedVersionRef
    predecessor_ref: SourceQualifiedVersionRef | None
    sequence: int
    command_id: str
    members: tuple[SourceMember, ...]

    def __post_init__(self):
        _exact_object_ref(self.source_set_ref, entity_type=SOURCE_SET_TYPE, logical_kind='resource', version_kind='resource_version')
        _exact_object_ref(self.owner_task_ref, entity_type='task/v1', logical_kind='task', version_kind='task_version')
        _exact_object_ref(self.publisher_bootstrap_ref, entity_type='bootstrap_command/v1',
                          logical_kind='bootstrap_command', version_kind='bootstrap_command_version')
        _require_source_id(self.command_id)
        if type(self.sequence) is not int or self.sequence < 1 or (self.sequence == 1) != (self.predecessor_ref is None):
            raise ValueError('SourceSet sequence and predecessor disagree')
        refs = [self.owner_task_ref, self.publisher_bootstrap_ref]
        if self.predecessor_ref is not None:
            _exact_object_ref(self.predecessor_ref, entity_type=SOURCE_SET_TYPE, logical_kind='resource', version_kind='resource_version')
            if (self.predecessor_ref.ref.entity_id != self.source_set_ref.ref.entity_id
                    or self.predecessor_ref == self.source_set_ref):
                raise ValueError('SourceSet successor must retain identity and use a fresh version')
            refs.append(self.predecessor_ref)
        if any(ref.source_id != self.source_set_ref.source_id for ref in refs):
            raise ValueError('SourceSet owner refs must share the explicit local source')
        if type(self.members) is not tuple or self.members != canonical_members(self.members):
            raise ValueError('SourceSet members must be canonical, exact and unique')

    def to_dict(self):
        return {'schema_version': SOURCE_SET_SCHEMA,
                **{k: getattr(self, k).to_dict() if getattr(self, k) is not None else None
                   for k in ('source_set_ref', 'owner_task_ref', 'publisher_bootstrap_ref', 'predecessor_ref')},
                'sequence': self.sequence, 'command_id': self.command_id,
                'members': [item.to_dict() for item in self.members]}

    @classmethod
    def from_dict(cls, value, catalog):
        catalog.validate_instance(SOURCE_SET_TYPE, category='object', instance=value)
        return cls(**{k: SourceQualifiedVersionRef.from_dict(value[k], catalog=catalog) if value[k] is not None else None
                      for k in ('source_set_ref', 'owner_task_ref', 'publisher_bootstrap_ref', 'predecessor_ref')},
                   sequence=value['sequence'], command_id=value['command_id'],
                   members=tuple(SourceMember.from_dict(m, catalog) for m in value['members']))


def read_source_set(core, reference):
    from ..registry._event_store.source_sets import exact_source_set
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        document = exact_source_set(db, core.catalog, core.task_id, reference.to_dict(), core.object_store)
    return SourceSetVersion.from_dict(document, core.catalog)


def current_source_set(core, identity):
    from ..registry._event_store.source_sets import current_source_set_document
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        value = current_source_set_document(db, core.catalog, core.task_id, str(identity), core.object_store)
    return None if value is None else SourceSetVersion.from_dict(value, core.catalog)


def _publish_source_set(gateway, *, members, command_id, expected=None, expected_sequence=0):
    core = gateway._core
    binding = get_local_source_identity(core)
    if binding is None:
        raise RegistryConflict('SourceSet requires an explicit source identity')
    previous = read_source_set(core, expected) if expected is not None else None
    if (type(expected_sequence) is not int or expected_sequence < 0
            or expected_sequence != (previous.sequence if previous else 0)):
        raise RegistryConflict('SourceSet requires the exact caller expected sequence')
    source = binding.source_id
    record = SourceSetVersion(
        SourceQualifiedVersionRef(source, VersionRef(SOURCE_SET_TYPE,
            previous.source_set_ref.ref.entity_id if previous else set_id(core.task_id, command_id),
            version_id(core.task_id, command_id))),
        SourceQualifiedVersionRef(source, gateway._task_ref), SourceQualifiedVersionRef(source, gateway._bootstrap_ref),
        expected, expected_sequence + 1, command_id, canonical_members(members))
    document = record.to_dict()
    tx = core.begin(idempotency_key=command_key(command_id))
    try:
        tx.prewrite(object_type=SOURCE_SET_TYPE, logical_id=record.source_set_ref.ref.entity_id,
                    version_id=record.source_set_ref.ref.version_id, payload=canonical_json(document),
                    metadata=document, media_type='application/json', schema_ref=SOURCE_SET_SCHEMA)
    except ObjectIntegrityError as exc:
        raise RegistryConflict('SourceSet command conflicts with immutable prior content') from exc
    tx.commit()
    return read_source_set(core, record.source_set_ref)
