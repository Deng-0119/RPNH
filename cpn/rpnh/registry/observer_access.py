"""Canonical observer issuance by the existing trusted source-owner gateway.

This same-OS-user HOST boundary is not multi-tenant authentication. Session
requests cannot issue grants; an arbitrary context is never authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from types import MappingProxyType
import uuid
from typing import Mapping
from .identities import TypedId
from .models import VersionRef
from .schema_catalog import canonical_json, canonical_text, TypeDefinition
from .publication import _version_from_payload
from .strict_contracts import ref_payload
from .event_store import RegistryConflict
from ._event_store.collaboration_descriptors import exact_descriptor, descriptor_store, readable_descriptor
from ._event_store.branch_publication import _local_binding
from ._event_store.source_identity import _canonical_commit_event
from ..collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef, _require_source_id

PROFILE_TYPE = 'registry_observer_profile/v2'
GRANT_TYPE = 'registry_observer_grant/v2'
REVOCATION_TYPE = 'registry_observer_revocation/v1'
OBSERVER_TYPES = (PROFILE_TYPE, GRANT_TYPE, REVOCATION_TYPE)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('authority expiration requires a timezone-aware timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('authority expiration requires a timezone')
    return parsed.astimezone(timezone.utc)


def _exact(value):
    if type(value) not in (SourceQualifiedResourceRef, SourceQualifiedVersionRef):
        raise TypeError('observer scope requires exact source-qualified references')
    return value.to_dict()


@dataclass(frozen=True, slots=True)
class ObserverReadScope:
    index_fields: Mapping[str, tuple[str, ...]]
    record_fields: Mapping[str, tuple[str, ...]]
    material_refs: tuple[SourceQualifiedResourceRef, ...] = ()
    export_refs: tuple[SourceQualifiedResourceRef | SourceQualifiedVersionRef, ...] = ()
    export_destinations: tuple[str, ...] = ()
    disclose_head: bool = True

    def __post_init__(self):
        for name in ('index_fields', 'record_fields'):
            data = getattr(self, name)
            if not isinstance(data, Mapping) or any(type(k) is not str or type(v) is not tuple
                    or not v or any(type(x) is not str or not x for x in v) or len(set(v)) != len(v)
                    for k, v in data.items()):
                raise ValueError('observer field scope must name explicit type and fields')
            object.__setattr__(self, name, MappingProxyType(dict(sorted(data.items()))))
        if (type(self.material_refs) is not tuple or any(type(x) is not SourceQualifiedResourceRef for x in self.material_refs)
                or type(self.export_refs) is not tuple or type(self.export_destinations) is not tuple
                or type(self.disclose_head) is not bool):
            raise TypeError('invalid observer body/export scope')
        for name in ('material_refs', 'export_refs'):
            keys = [canonical_json(_exact(x)) for x in getattr(self, name)]
            if len(set(keys)) != len(keys):
                raise ValueError('observer references must be unique')
        for value in self.export_destinations:
            _require_source_id(value)
        if len(set(self.export_destinations)) != len(self.export_destinations):
            raise ValueError('observer destinations must be unique')
        if bool(self.export_refs) != bool(self.export_destinations):
            raise ValueError('export refs and exact destinations must be granted together')

    def to_dict(self):
        return {'index_fields': {k: list(v) for k, v in self.index_fields.items()},
                'record_fields': {k: list(v) for k, v in self.record_fields.items()},
                'material_refs': [_exact(x) for x in self.material_refs],
                'export_refs': [_exact(x) for x in self.export_refs],
                'export_destinations': list(self.export_destinations), 'disclose_head': self.disclose_head}

    @classmethod
    def from_dict(cls, value, *, catalog):
        if not isinstance(value, dict) or set(value) != {'index_fields', 'record_fields', 'material_refs',
                'export_refs', 'export_destinations', 'disclose_head'}:
            raise ValueError('invalid observer scope document')
        def ref(value):
            cls = SourceQualifiedResourceRef if value.get('schema_version') == 'rpnh/collaboration/source_resource_ref/v1' else SourceQualifiedVersionRef
            return cls.from_dict(value, catalog=catalog)
        return cls({k: tuple(v) for k, v in value['index_fields'].items()},
                   {k: tuple(v) for k, v in value['record_fields'].items()},
                   tuple(ref(x) for x in value['material_refs']), tuple(ref(x) for x in value['export_refs']),
                   tuple(value['export_destinations']), value['disclose_head'])


@dataclass(frozen=True, slots=True)
class RegistryReadObserverContext:
    observer_principal_ref: VersionRef
    observer_profile_ref: VersionRef
    task_ref: VersionRef
    grant_ref: VersionRef
    issued_writer_fencing_epoch: int
    issued_task_control_sequence: int
    reader_fence: str
    expires_at: str
    purpose: str

    def __post_init__(self):
        for name, kind in (('observer_principal_ref', 'principal/v1'), ('observer_profile_ref', PROFILE_TYPE),
                           ('task_ref', 'task/v1'), ('grant_ref', GRANT_TYPE)):
            if type(getattr(self, name)) is not VersionRef or getattr(self, name).entity_type != kind:
                raise TypeError('observer context has an invalid exact authority type')
        if any(type(x) is not int or x < 0 for x in (self.issued_writer_fencing_epoch, self.issued_task_control_sequence)):
            raise ValueError('observer context has an invalid authority head')
        timestamp(self.expires_at)
        _require_source_id(self.reader_fence)
        _require_source_id(self.purpose)

    def to_dict(self):
        return {**{name: ref_payload(getattr(self, name)) for name in
                   ('observer_principal_ref', 'observer_profile_ref', 'task_ref', 'grant_ref')},
                **{name: getattr(self, name) for name in ('issued_writer_fencing_epoch',
                   'issued_task_control_sequence', 'reader_fence', 'expires_at', 'purpose')}}

    @classmethod
    def from_dict(cls, value):
        fields = {'observer_principal_ref', 'observer_profile_ref', 'task_ref', 'grant_ref',
                  'issued_writer_fencing_epoch', 'issued_task_control_sequence', 'reader_fence', 'expires_at', 'purpose'}
        if type(value) is not dict or set(value) != fields:
            raise ValueError('invalid observer context document')
        refs = {'observer_principal_ref', 'observer_profile_ref', 'task_ref', 'grant_ref'}
        return cls(**{key: _version_from_payload(item) if key in refs else item for key, item in value.items()})


def _key(command_id):
    _require_source_id(command_id)
    return 'registry-observer-access:' + canonical_text({'command_id': command_id})


def _ref(task_id, command_id, object_type):
    def make(kind):
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            'kind': kind, 'task': str(task_id), 'command': command_id, 'type': object_type})).hex)
    return VersionRef(object_type, make('resource'), make('resource_version'))


def _control(db, task_id):
    row = db.execute('SELECT sequence FROM task_control_heads WHERE task_id=?', (str(task_id),)).fetchone()
    return int(row['sequence']) if row is not None else 0


def _publish(gateway, documents, *, command_id):
    from .registration_gateway import RegistryRegistrationGateway
    if type(gateway) is not RegistryRegistrationGateway or gateway._core.read_only:
        raise TypeError('observer issuance requires the existing owner registration gateway')
    if any(len(canonical_json(value)) > 1024 * 1024 for value in documents):
        raise ValueError('observer authority exceeds its bounded record size')
    core = gateway._core
    if core.writer_epoch != core.event_store.writer_epoch:
        raise RegistryConflict('observer issuer has a stale writer fence')
    tx = core.begin(idempotency_key=_key(command_id))
    for value in documents:
        reference = _version_from_payload(value['self_ref'])
        tx.prewrite(object_type=reference.entity_type, logical_id=reference.entity_id, version_id=reference.version_id,
                    payload=canonical_json(value), metadata=value, media_type='application/json',
                    schema_ref='registry_v1/' + reference.entity_type)
    tx.commit()


def _read_observer_principal(db, store, task_id, reference):
    """Require canonical principal bytes to name the same exact identity."""
    if reference.get('entity_type') != 'principal/v1':
        raise RegistryConflict('observer principal has an unsupported type')
    value = exact_descriptor(db, store, task_id, reference)
    if (value.get('principal_id') != reference.get('logical_id')
            or value.get('principal_version_id') != reference.get('version_id')):
        raise RegistryConflict('observer principal self-identity differs from its exact reference')
    return value


def issue_observer_access(gateway, *, principal_ref: VersionRef, scope: ObserverReadScope,
                          purpose: str, expires_at: str, command_id: str) -> RegistryReadObserverContext:
    """Explicit owner action. Renewal requires a fresh command and context."""
    from .registration_gateway import RegistryRegistrationGateway
    if type(gateway) is not RegistryRegistrationGateway or type(scope) is not ObserverReadScope:
        raise TypeError('observer issuance requires an owner gateway and explicit scope')
    if type(principal_ref) is not VersionRef or principal_ref.entity_type != 'principal/v1':
        raise TypeError('observer principal must already be canonically registered')
    _require_source_id(purpose)
    if timestamp(expires_at) <= datetime.now(timezone.utc):
        raise ValueError('observer grant must expire in the future')
    core = gateway._core
    profile_ref, grant_ref = (_ref(core.task_id, command_id, kind) for kind in (PROFILE_TYPE, GRANT_TYPE))
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        binding = _local_binding(db, core.catalog, core.task_id, core.object_store)
        if binding['task_ref'] != ref_payload(gateway._task_ref) or binding['bootstrap_command_ref'] != ref_payload(gateway._bootstrap_ref):
            raise RegistryConflict('observer issuer differs from source owner')
        _read_observer_principal(db, core.object_store, core.task_id, ref_payload(principal_ref))
        control = _control(db, core.task_id)
    if any(ref.source_id != binding['source_id'] for ref in (*scope.material_refs, *scope.export_refs)):
        raise ValueError('observer scope cannot grant other sources')
    context = RegistryReadObserverContext(principal_ref, profile_ref, gateway._task_ref, grant_ref,
        core.writer_epoch, control, 'orf_' + _ref(core.task_id, command_id, 'observer_fence/v1').version_id.value,
        expires_at, purpose)
    common = {'owner_task_ref': ref_payload(gateway._task_ref),
              'publisher_bootstrap_ref': ref_payload(gateway._bootstrap_ref),
              'source_id': binding['source_id'], 'command_id': command_id}
    profile = {'schema_version': 'registry_v1/' + PROFILE_TYPE, 'self_ref': ref_payload(profile_ref),
               **common, 'observer_principal_ref': ref_payload(principal_ref), 'scope': scope.to_dict()}
    grant = {'schema_version': 'registry_v1/' + GRANT_TYPE, 'self_ref': ref_payload(grant_ref),
             **common, **context.to_dict()}
    _publish(gateway, (profile, grant), command_id=command_id)
    verify_observer_access(core, context, purpose=purpose)
    return context


def revoke_observer_access(gateway, *, grant_ref: VersionRef, command_id: str) -> VersionRef:
    from .registration_gateway import RegistryRegistrationGateway
    if type(gateway) is not RegistryRegistrationGateway or type(grant_ref) is not VersionRef or grant_ref.entity_type != GRANT_TYPE:
        raise TypeError('observer revocation requires the owner gateway and exact grant')
    core = gateway._core
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        binding = _local_binding(db, core.catalog, core.task_id, core.object_store)
        _read_owner_document(db, core, grant_ref)
    reference = _ref(core.task_id, command_id, REVOCATION_TYPE)
    document = {'schema_version': 'registry_v1/' + REVOCATION_TYPE, 'self_ref': ref_payload(reference),
                'owner_task_ref': ref_payload(gateway._task_ref), 'publisher_bootstrap_ref': ref_payload(gateway._bootstrap_ref),
                'source_id': binding['source_id'], 'command_id': command_id, 'grant_ref': ref_payload(grant_ref)}
    _publish(gateway, (document,), command_id=command_id)
    return reference


def _read_owner_document(db, core, ref):
    if ref.entity_type not in OBSERVER_TYPES:
        raise RegistryConflict('unsupported observer authority type')
    row = db.execute('SELECT e.*,o.producer_invocation_id AS object_invocation,o.size AS object_size '
        'FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.object_type=? '
        'AND o.logical_id=? AND o.version_id=?', (ref.entity_type, str(ref.entity_id), str(ref.version_id))).fetchone()
    if row is None or row['object_size'] > 1024 * 1024:
        raise RegistryConflict('observer authority is absent or exceeds its budget')
    value = exact_descriptor(db, core.object_store, core.task_id, ref_payload(ref))
    binding = _local_binding(db, core.catalog, core.task_id, core.object_store)
    if (value['self_ref'] != ref_payload(ref) or ref != _ref(core.task_id, value['command_id'], ref.entity_type)
            or value['source_id'] != binding['source_id'] or value['owner_task_ref'] != binding['task_ref']
            or value['publisher_bootstrap_ref'] != binding['bootstrap_command_ref']
            or row['producer_principal'] != 'framework' or row['producer_invocation_id'] is not None
            or row['object_invocation'] is not None or row['net_instance_id'] is not None or row['task_round_id'] is not None
            or row['command_id'] != _key(value['command_id']) or row['idempotency_key'] != row['command_id']
            or row['stream_sequence'] != 1 or _canonical_commit_event(db, row['transaction_id'], core.task_id) is None
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE (member_kind='object' AND member_identity=?) "
                          "OR (member_kind='event' AND member_identity=?)", (str(ref.version_id), row['event_id'])).fetchone()):
        raise RegistryConflict('observer authority lacks canonical owner issuance')
    return value


def verify_observer_access(core, context, *, purpose):
    """Verify current authority from the source; return immutable effective scope."""
    if type(context) is not RegistryReadObserverContext:
        raise TypeError('unsupported independent observer context')
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        grant = _read_owner_document(db, core, context.grant_ref)
        profile = _read_owner_document(db, core, context.observer_profile_ref)
        if (any(grant.get(key) != value for key, value in context.to_dict().items())
                or profile['observer_principal_ref'] != ref_payload(context.observer_principal_ref)
                or profile['command_id'] != grant['command_id'] or context.task_ref.entity_id != core.task_id
                or context.purpose != purpose or context.issued_writer_fencing_epoch != core.event_store.writer_epoch
                or context.issued_task_control_sequence != _control(db, core.task_id)
                or timestamp(context.expires_at) <= datetime.now(timezone.utc)):
            raise RegistryConflict('observer authority is no longer current')
        _read_observer_principal(db, core.object_store, core.task_id, ref_payload(context.observer_principal_ref))
        rows = db.execute('SELECT logical_id,version_id FROM objects WHERE object_type=? '
                          "AND json_extract(metadata_json,'$.grant_ref.version_id')=? LIMIT 2",
                          (REVOCATION_TYPE, str(context.grant_ref.version_id))).fetchall()
        if rows:
            for row in rows:
                _read_owner_document(db, core, VersionRef(REVOCATION_TYPE, TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
            raise RegistryConflict('observer authority has been revoked')
    return ObserverReadScope.from_dict(profile['scope'], catalog=core.catalog)


def validate_observer_publications(context):
    """Owner-only transaction shape validation, installed by the Registry writer."""
    chosen = [obj for obj in context.objects if obj.object_type in OBSERVER_TYPES]
    uses = bool(chosen) or any(event.payload.get('object_type') in OBSERVER_TYPES for event in context.events)
    identities = {str(obj.logical_id) for obj in context.objects}
    identities.update(event.stream_id.removeprefix('object:') for event in context.events if event.stream_id.startswith('object:'))
    if not uses and identities:
        marks = ','.join('?' for _ in identities)
        kinds = ','.join('?' for _ in OBSERVER_TYPES)
        uses = context.db.execute(f'SELECT 1 FROM objects WHERE object_type IN ({kinds}) AND logical_id IN ({marks}) LIMIT 1', (*OBSERVER_TYPES, *sorted(identities))).fetchone() is not None
    if not uses:
        return
    types = {obj.object_type for obj in chosen}
    if (types not in ({PROFILE_TYPE, GRANT_TYPE}, {REVOCATION_TYPE}) or len(chosen) != len(types)
            or len(context.objects) != len(chosen) or context.relations
            or len(context.events) != len(chosen) + 1 or context.task_round_id is not None or context.net_instance_id is not None):
        raise RegistryConflict('observer access requires a separate owner transaction')
    store = descriptor_store(context.event_store)
    binding = _local_binding(context.db, context.event_store.catalog, context.task_id, store)
    documents = {obj.object_type: readable_descriptor(store, obj) for obj in chosen}
    for obj in chosen:
        value = documents[obj.object_type]
        expected = _ref(context.task_id, value['command_id'], obj.object_type)
        if (obj.logical_id != expected.entity_id or obj.version_id != expected.version_id
                or value['self_ref'] != ref_payload(expected) or obj.producer_invocation_id is not None
                or value['owner_task_ref'] != binding['task_ref'] or value['publisher_bootstrap_ref'] != binding['bootstrap_command_ref']
                or value['source_id'] != binding['source_id'] or context.idempotency_key != _key(value['command_id'])):
            raise RegistryConflict('observer issuer owner/source/command differs')
        event = next((e for e in context.events if e.event_type == 'object_version_published/v1'
                      and e.payload.get('version_id') == str(obj.version_id)), None)
        if (event is None or event.producer_principal != 'framework' or event.producer_invocation_id is not None
                or event.task_control or event.criticality != 'authoritative' or event.stream_id != f'object:{obj.logical_id}'
                or event.command_id != context.idempotency_key or event.idempotency_key != context.idempotency_key
                or event.aggregate_id != str(obj.logical_id) or event.aggregate_type != obj.object_type
                or event.payload_schema_ref != 'registry_v1/object_version_published/v1'
                or canonical_json(dict(event.payload)) != canonical_json({'object_type': obj.object_type,
                    'logical_id': str(obj.logical_id), 'version_id': str(obj.version_id), 'size': obj.size,
                    'media_type': obj.media_type, 'schema_ref': obj.schema_ref,
                    'storage_locator': obj.storage_locator, 'metadata': dict(obj.metadata)})):
            raise RegistryConflict('observer publication differs from owner transaction')
        if context.db.execute('SELECT 1 FROM objects WHERE logical_id=? LIMIT 1', (str(obj.logical_id),)).fetchone():
            raise RegistryConflict('observer immutable authority identity already exists')
    terminals = [event for event in context.events if event.event_type == 'transaction_committed/v1']
    if len(terminals) != 1:
        raise RegistryConflict('observer issuance requires one exact terminal')
    terminal = terminals[0]
    if (terminal.producer_principal != 'framework' or terminal.producer_invocation_id is not None
            or terminal.task_control or terminal.criticality != 'authoritative'
            or terminal.stream_id != f'transaction:{context.transaction_id}'
            or terminal.aggregate_id != str(context.transaction_id) or terminal.aggregate_type != 'transaction'
            or terminal.command_id != context.idempotency_key or terminal.idempotency_key != context.idempotency_key
            or terminal.payload_schema_ref != 'registry_v1/transaction_committed/v1'
            or dict(terminal.payload) != {'object_count': len(chosen), 'relation_count': 0, 'fact_count': len(chosen)}):
        raise RegistryConflict('observer terminal differs from exact owner issuance')
    if GRANT_TYPE in types:
        grant, profile = documents[GRANT_TYPE], documents[PROFILE_TYPE]
        scope = ObserverReadScope.from_dict(profile['scope'], catalog=context.event_store.catalog)
        if (grant['observer_profile_ref'] != profile['self_ref'] or grant['observer_principal_ref'] != profile['observer_principal_ref']
                or grant['task_ref'] != binding['task_ref'] or grant['issued_writer_fencing_epoch'] != context.transaction_writer_epoch
                or grant['issued_task_control_sequence'] != _control(context.db, context.task_id)
                or timestamp(grant['expires_at']) <= datetime.now(timezone.utc)
                or any(ref.source_id != binding['source_id'] for ref in (*scope.material_refs, *scope.export_refs))):
            raise RegistryConflict('observer grant/profile scope or lifecycle differs')
        _read_observer_principal(context.db, store, context.task_id, grant['observer_principal_ref'])
    else:
        grant = documents[REVOCATION_TYPE]['grant_ref']
        class CoreView:
            pass
        core = CoreView()
        core.task_id, core.catalog, core.object_store = context.task_id, context.event_store.catalog, store
        _read_owner_document(context.db, core, _version_from_payload(grant))


def observer_access_schema_data():
    """Opt-in inventory; v1 observers and mechanical catalogs remain unchanged."""
    from ..collaboration.schema_catalog import source_identity_schema_data
    documents, definitions, paths = source_identity_schema_data()
    root = Path(__file__).resolve().parents[2] / 'schemas' / 'registry_v1'
    for object_type in OBSERVER_TYPES:
        schema = 'registry_v1/' + object_type
        path = root / (object_type.replace('/', '.') + '.schema.json')
        documents[schema] = json.loads(path.read_text(encoding='utf-8'))
        paths[schema] = path
        definitions = (*definitions, TypeDefinition(name=object_type, category='object', owner='registry',
            schema_ref=schema, criticality=None, permission='writer-only', retention='permanent',
            recovery_rule='exact-observer-owner-command-replay', integrity_rule='canonical-owner-profile-grant-and-revocation'))
    return documents, definitions, paths
