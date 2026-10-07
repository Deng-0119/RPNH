"""Independent, finite-lifetime read sessions over existing Registry authority.

HOST resolver/configuration is trusted input, never submitted in a read request.
No operation here creates a task, grant, writer fence, schema, or Observation.
SQLite live read-only WAL sidecars may change; canonical Registry facts do not.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
from types import MappingProxyType
from typing import Callable, Mapping

from ..registry._registry import _RegistryCore
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.invocations import InvocationContext
from ..registry.resource_service import _ResourceServiceKernel
from ..registry.resources import RegistryHead, RegistryObserverContext, ResourceVersionRef
from ..registry.observer_access import RegistryReadObserverContext, ObserverReadScope, verify_observer_access, timestamp
from ..registry.schema_catalog import canonical_json
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .sources import get_local_source_identity
from .source_query import wire
from .registry_read_contracts import (RegistryReadSessionError, ReadLimits, SourceSelection,
    ReadSessionRequest, SelectedSourceSet, SourceCut, HistoricalCutRequest, PublicRegistryHead,
    IndexQuery, qualified_ref, document_digest, READER_CONTRACT_VERSION)


@dataclass(slots=True)
class ResolvedReadSource:
    core: _RegistryCore
    binding_generation: str
    closed: bool = False

    def __post_init__(self):
        if type(self.core) is not _RegistryCore or not self.core.read_only:
            raise TypeError('read source requires an existing read-only Registry')
        if not isinstance(self.binding_generation, str) or not self.binding_generation:
            raise TypeError('source resolver must supply a binding generation')

    def close(self):
        # Core connections are operation-scoped; release the source handle only.
        self.closed = True


def open_readonly_source(path: Path | str, *, catalog, binding_generation='1') -> ResolvedReadSource:
    """Public trusted-HOST opener; never create a missing source or obtain a writer."""
    try:
        candidate = Path(path)
        if candidate.is_symlink() or (candidate / '.registry_v1').is_symlink() or (candidate / '.registry_v1/registry.sqlite3').is_symlink():
            raise RegistryReadSessionError('SOURCE_UNAVAILABLE')
        core = _RegistryCore(candidate, create=False, read_only=True, catalog=catalog)
        return ResolvedReadSource(core, binding_generation)
    except RegistryReadSessionError:
        raise
    except Exception as exc:
        raise RegistryReadSessionError('SOURCE_UNAVAILABLE') from exc


def _schema_fingerprint(core):
    # Include exact schema content, not just a catalog name or version label.
    return document_digest({'types': [asdict(x) for x in core.catalog.definitions()],
                            'schemas': {schema: core.catalog._validator(schema).schema for schema in sorted({x.schema_ref for x in core.catalog.definitions()} | set(core.catalog.registered_schemas()))}})


def _budget_preflight(core, limits, *, db=None, ordinal=None):
    """Reject over-budget history before any unbounded legacy hydration call."""
    if db is None:
        with core.event_store.connect() as db:
            db.execute('BEGIN')
            return _budget_preflight(core, limits, db=db, ordinal=ordinal)
    upper = ordinal if ordinal is not None else core.event_store.max_ordinal()
    rows = size = 0
    queries = (
        ('SELECT count(*),coalesce(sum(length(CAST(payload_json AS BLOB))),0) FROM events WHERE ordinal<=?', (upper,)),
        ('SELECT count(*),coalesce(sum(length(CAST(o.metadata_json AS BLOB))),0) FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE e.ordinal<=?', (upper,)),
        ('SELECT count(*),coalesce(sum(length(CAST(r.metadata_json AS BLOB))+length(CAST(r.source_json AS BLOB))+length(CAST(r.target_json AS BLOB))),0) FROM relations r JOIN events e ON e.event_id=r.published_event_id WHERE e.ordinal<=?', (upper,)),
    )
    for sql, args in queries:
        result = db.execute(sql, args).fetchone()
        rows += int(result[0]); size += int(result[1])
        if rows > limits.max_scan_rows or size > limits.max_scan_bytes:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
    return rows, size


@dataclass(frozen=True, slots=True)
class _VerifiedAuthority:
    context: object
    scope: ObserverReadScope
    revision: str
    expires_at: datetime | None
    kind: str


class ExistingReadAuthorityProvider:
    """Trusted mapping from HOST-verified callers to pre-existing source grants.

    Entries are configuration, not self-signed credentials. Every resolution
    checks the exact canonical source grant/profile and current lifecycle.
    """
    def __init__(self, bindings: Mapping[tuple[str, str, str], object]):
        self._bindings = dict(bindings)
        if any(type(key) is not tuple or len(key) != 3 or any(type(x) is not str or not x for x in key)
               or type(value) not in (RegistryReadObserverContext, RegistryObserverContext, InvocationContext)
               for key, value in self._bindings.items()):
            raise TypeError('authority provider requires explicit existing HOST bindings')

    def assert_selection_configured(self, verified_caller, source_ref, access_path):
        context = self._bindings.get((verified_caller, source_ref.source_id, access_path))
        if context is None:
            # This says only that the HOST lacks a binding. It is returned
            # before resolving or checking whether a source actually exists.
            raise RegistryReadSessionError('AUTHORITY_NOT_CONFIGURED')
        if context.task_ref != source_ref.ref:
            raise RegistryReadSessionError('NOT_DISCLOSED')

    def resolve_existing(self, verified_caller, source_ref, access_path, purpose, *, core, catalog, limits):
        context = self._bindings.get((verified_caller, source_ref.source_id, access_path))
        if context is None:
            raise RegistryReadSessionError('AUTHORITY_NOT_CONFIGURED')
        if context.task_ref != source_ref.ref:
            raise RegistryReadSessionError('NOT_DISCLOSED')
        _budget_preflight(core, limits)
        try:
            if type(context) is RegistryReadObserverContext:
                scope = verify_observer_access(core, context, purpose=purpose)
                facts = {'context': context.to_dict(), 'scope': scope.to_dict()}
                expiry, kind = timestamp(context.expires_at), 'observer'
            else:
                service = _ResourceServiceKernel(core)
                service._query_authority(context, observer_fields=('headers', 'projection_head'))
                fields = tuple(catalog.fields('resource_version/v1'))
                scope = ObserverReadScope({'resource_version/v1': fields}, {'resource_version/v1': fields})
                facts = service._authority_facts(context)
                expiry = timestamp(context.expires_at) if type(context) is RegistryObserverContext else None
                kind = 'managed_invocation' if type(context) is InvocationContext else 'observer_metadata_v1'
        except Exception as exc:
            raise RegistryReadSessionError('ACCESS_CHANGED') from exc
        return _VerifiedAuthority(context, scope, document_digest(wire(facts)), expiry, kind)


@dataclass(frozen=True, slots=True)
class RegistryReadHostBinding:
    verified_caller: str
    source_resolver: Callable
    authority_provider: ExistingReadAuthorityProvider
    typed_reader_catalog: object
    limits: ReadLimits = field(default_factory=ReadLimits)
    source_set_resolver: Callable | None = None

    def __post_init__(self):
        if (not isinstance(self.verified_caller, str) or not self.verified_caller
                or not callable(self.source_resolver) or type(self.authority_provider) is not ExistingReadAuthorityProvider
                or type(self.limits) is not ReadLimits or self.source_set_resolver is not None and not callable(self.source_set_resolver)):
            raise TypeError('read HOST binding requires verified caller and trusted adapters')
        for name in ('fingerprint', 'fields', 'read_index', 'read_exact', 'default_index_projection', 'default_record_projection'):
            if not callable(getattr(self.typed_reader_catalog, name, None)):
                raise TypeError('read HOST requires a typed reader catalog')


@dataclass(slots=True)
class _Source:
    selection: SourceSelection
    resolved: ResolvedReadSource
    authority: _VerifiedAuthority
    binding: tuple
    schema_fingerprint: str
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    source_id: str
    head: RegistryHead
    objects: Mapping
    events: tuple
    relations: tuple
    check_authorized: Callable
    max_material_bytes: int
    publication_ordinals: Mapping
    budget_bytes: int


@dataclass(slots=True)
class _Query:
    fingerprint: str
    entries: tuple
    cuts: dict
    source_ids: tuple
    failures: dict


class RegistryReadSession:
    def __init__(self, request, host):
        if type(request) is not ReadSessionRequest or type(host) is not RegistryReadHostBinding:
            raise TypeError('read session requires a typed request and trusted HOST binding')
        self._host, self._request = host, request
        self._catalog = host.typed_reader_catalog
        self._catalog_fingerprint = self._catalog.fingerprint()
        self._caller = host.verified_caller
        self.limits = ReadLimits().tightened(request.limits).tightened(host.limits)
        if len(request.selection.sources) > self.limits.max_sources or len(canonical_json(request.to_dict())) > self.limits.max_query_bytes:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        self.session_id = 'rs_' + secrets.token_urlsafe(24)
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.limits.session_seconds)
        self._sources, self._failures, self._cuts, self._cursors = {}, {}, {}, {}
        self._closed = False
        try:
            if type(request.selection) is SelectedSourceSet:
                self._verify_source_set(request.selection)
            for selection in request.selection.sources:
                source_id = selection.source_ref.source_id
                try:
                    resolved, authority, binding = self._resolve(selection)
                    self._sources[source_id] = _Source(selection, resolved, authority, binding, _schema_fingerprint(resolved.core))
                    if authority.expires_at is not None:
                        self.expires_at = min(self.expires_at, authority.expires_at)
                except RegistryReadSessionError as exc:
                    if exc.code == 'ACCESS_CHANGED':
                        raise
                    self._failures[source_id] = exc.code
            if not self._sources:
                raise RegistryReadSessionError(next(iter(self._failures.values()), 'NOT_DISCLOSED'))
            self.final_recheck()
        except Exception:
            self.close()
            raise

    def _verify_source_set(self, selection):
        from .source_sets import read_source_set
        if self._host.source_set_resolver is None:
            raise RegistryReadSessionError('AUTHORITY_NOT_CONFIGURED')
        # Manifest presence is explicit trusted HOST configuration; it only
        # restricts selected members and never grants access to their sources.
        source = None
        try:
            source = self._host.source_set_resolver(selection.source_set_ref)
            if type(source) is not ResolvedReadSource or source.closed:
                raise RegistryReadSessionError('NOT_DISCLOSED')
            _budget_preflight(source.core, self.limits)
            manifest = read_source_set(source.core, selection.source_set_ref)
            members = {m.source_ref.source_id: m for m in manifest.members}
            if any(s.source_ref.source_id not in members or members[s.source_ref.source_id].source_ref != s.source_ref
                    or s.access_path not in members[s.source_ref.source_id].access_paths for s in selection.sources):
                raise RegistryReadSessionError('NOT_DISCLOSED')
            self._manifest = (source, selection.source_set_ref, document_digest(manifest.to_dict()),
                source.binding_generation, str(source.core.event_store.path.resolve()), _schema_fingerprint(source.core))
        except RegistryReadSessionError:
            if type(source) is ResolvedReadSource:
                source.close()
            raise
        except Exception as exc:
            if type(source) is ResolvedReadSource:
                source.close()
            raise RegistryReadSessionError('NOT_DISCLOSED') from exc

    def _resolve(self, selection):
        resolved = None
        try:
            self._host.authority_provider.assert_selection_configured(self._caller, selection.source_ref, selection.access_path)
            resolved = self._host.source_resolver(selection.source_ref.source_id, selection.access_path)
            if type(resolved) is not ResolvedReadSource or resolved.closed:
                raise RegistryReadSessionError('SOURCE_UNAVAILABLE')
            _budget_preflight(resolved.core, self.limits)
            identity = get_local_source_identity(resolved.core)
            if identity is None or identity.source_id != selection.source_ref.source_id or identity.task_ref != selection.source_ref.ref:
                raise RegistryReadSessionError('NOT_DISCLOSED')
            authority = self._host.authority_provider.resolve_existing(self._caller, selection.source_ref,
                selection.access_path, self._request.purpose, core=resolved.core, catalog=self._catalog, limits=self.limits)
            path = resolved.core.event_store.path.resolve()
            stat = path.stat()
            binding = (resolved.binding_generation, str(path), stat.st_dev, stat.st_ino, identity.event_id)
            return resolved, authority, binding
        except RegistryReadSessionError:
            if type(resolved) is ResolvedReadSource:
                resolved.close()
            raise
        except Exception as exc:
            if type(resolved) is ResolvedReadSource:
                resolved.close()
            raise RegistryReadSessionError('SOURCE_UNAVAILABLE') from exc

    def _alive(self):
        if self._closed:
            raise RegistryReadSessionError('SESSION_CLOSED')
        if datetime.now(timezone.utc) >= self.expires_at:
            self._cuts.clear(); self._cursors.clear()
            raise RegistryReadSessionError('SESSION_EXPIRED')
        if self._host.verified_caller != self._caller or self._catalog.fingerprint() != self._catalog_fingerprint:
            self._cuts.clear(); self._cursors.clear()
            raise RegistryReadSessionError('ACCESS_CHANGED')

    def _invalidate(self, source_id, code='ACCESS_CHANGED'):
        source = self._sources.get(source_id)
        if source is not None:
            source.failure = code
        self._cuts = {key: pair for key, pair in self._cuts.items() if pair[0].source_id != source_id}
        # Page state can contain this source's sensitive fields; discard whole
        # cursor state. A subsequent explicit query may retain other sources.
        self._cursors.clear()

    def final_recheck(self, source_ids=None):
        self._alive()
        if hasattr(self, '_manifest'):
            from .source_sets import read_source_set
            previous, ref, digest, generation, path, schema = self._manifest
            current = None
            try:
                current = self._host.source_set_resolver(ref)
                if (type(current) is not ResolvedReadSource or current.closed or previous.closed
                        or current.binding_generation != generation or str(current.core.event_store.path.resolve()) != path
                        or _schema_fingerprint(current.core) != schema
                        or document_digest(read_source_set(current.core, ref).to_dict()) != digest):
                    raise RegistryReadSessionError('ACCESS_CHANGED')
            except Exception as exc:
                self._cuts.clear(); self._cursors.clear()
                raise RegistryReadSessionError('ACCESS_CHANGED') from exc
            finally:
                if type(current) is ResolvedReadSource and current is not previous:
                    current.close()
        ids = tuple(self._sources) if source_ids is None else tuple(source_ids)
        for source_id in ids:
            source = self._sources.get(source_id)
            if source is None:
                raise RegistryReadSessionError(self._failures.get(source_id, 'NOT_DISCLOSED'))
            if source.failure:
                raise RegistryReadSessionError(source.failure)
            resolved = None
            try:
                resolved, authority, binding = self._resolve(source.selection)
                if (binding != source.binding or authority.revision != source.authority.revision
                        or _schema_fingerprint(resolved.core) != source.schema_fingerprint
                        or source.resolved.closed):
                    raise RegistryReadSessionError('ACCESS_CHANGED')
            except RegistryReadSessionError as exc:
                self._invalidate(source_id, 'ACCESS_CHANGED')
                raise RegistryReadSessionError('ACCESS_CHANGED') from exc
            finally:
                if type(resolved) is ResolvedReadSource and resolved is not source.resolved:
                    resolved.close()
        # A callback/revalidation can consume the last fraction of a TTL or
        # change the reader catalog. Check these again at actual delivery.
        self._alive()

    def close(self):
        if not self._closed:
            self._closed = True
            self._cuts.clear(); self._cursors.clear()
            for source in self._sources.values():
                source.resolved.close()
            if hasattr(self, '_manifest'):
                self._manifest[0].close()
        return {'status': 'closed'}

    def __enter__(self):
        self._alive(); return self

    def __exit__(self, *_args):
        self.close()

    def describe(self):
        self._alive()
        states = [self.source_state(source_id) for source_id in self._sources]
        states += [{'source_id': key, 'source_ref': None, 'cut': None, 'access_revision': None,
                    'access_state': code, 'coverage': {'state': 'unavailable', 'loaded_count': None, 'total_count': None}}
                   for key, code in self._failures.items()]
        self.final_recheck()
        return {'session_id': self.session_id, 'expires_at': self.expires_at.isoformat(),
                'effective_limits': self.limits.to_dict(), 'sources': states,
                'entry_types': list(self._catalog.entry_types),
                'fields': {kind: dict(self._catalog.fields(kind)) for kind in self._catalog.entry_types},
                'reader_contract_version': READER_CONTRACT_VERSION, 'global_atomic_snapshot': False}

    def source_state(self, source_id, cut=None):
        if type(source_id) is SourceCut:
            cut, source_id = source_id, source_id.source_id
        self.final_recheck((source_id,))
        source = self._sources[source_id]
        if cut is not None:
            cut = self.validate_cut(cut)
            if cut.source_id != source_id:
                raise RegistryReadSessionError('CURSOR_MISMATCH')
        supported = set(self._catalog.entry_types) & {item.name for item in source.resolved.core.catalog.definitions() if item.category == 'object'}
        index_fields = {kind: list(fields) for kind, fields in source.authority.scope.index_fields.items() if kind in supported}
        record_fields = {kind: list(fields) for kind, fields in source.authority.scope.record_fields.items() if kind in supported}
        return {'source_id': source_id, 'source_ref': source.selection.source_ref.to_dict(),
                'access_path': source.selection.access_path,
                'cut': None if cut is None else cut.to_dict(), 'access_revision': source.authority.revision,
                'access_state': 'readable', 'coverage': {'state': 'not_queried', 'loaded_count': None, 'total_count': None},
                'capabilities': {'index': list(index_fields), 'index_fields': index_fields,
                    'record': list(record_fields), 'record_fields': record_fields, 'material': bool(source.authority.scope.material_refs),
                    'export': bool(source.authority.scope.export_refs),
                    'projection': [kind for kind in getattr(self._catalog, 'public_projection_types', ())
                        if kind in record_fields and set(self._catalog.default_record_projection(kind)) <= set(record_fields[kind])],
                    'execution': 'not_checked'}}

    def _authorize(self, source_id, reference, operation, fields=()):
        from .registry_typed_readers import wire_ref
        actual_source, ref = wire_ref(reference)
        if actual_source != source_id:
            raise RegistryReadSessionError('NOT_DISCLOSED')
        source = self._sources[source_id]
        scope = source.authority.scope
        if operation in ('index', 'record'):
            allowed = getattr(scope, operation + '_fields').get(ref['entity_type'])
            if allowed is None or set(fields) - set(allowed):
                raise RegistryReadSessionError('NOT_DISCLOSED')
            if source.authority.kind == 'managed_invocation':
                if ref['entity_type'] != 'resource_version/v1':
                    raise RegistryReadSessionError('NOT_DISCLOSED')
                from ..registry.errors import UnauthorizedResourceDelivery
                service = _ResourceServiceKernel(source.resolved.core)
                try:
                    service._query_authority(source.authority.context,
                        ResourceVersionRef(TypedId.parse(ref['logical_id']), TypedId.parse(ref['version_id'])))
                except UnauthorizedResourceDelivery as exc:
                    raise RegistryReadSessionError('NOT_DISCLOSED') from exc
                except Exception as exc:
                    raise RegistryReadSessionError('ACCESS_CHANGED') from exc
        elif operation == 'material':
            value = reference.to_dict() if hasattr(reference, 'to_dict') else reference
            if source.authority.kind == 'managed_invocation':
                raise RegistryReadSessionError('GOVERNED_DELIVERY_REQUIRED')
            if value not in [x.to_dict() for x in scope.material_refs]:
                raise RegistryReadSessionError('MATERIAL_ACCESS_NOT_GRANTED')
        elif operation == 'export':
            value = reference.to_dict() if hasattr(reference, 'to_dict') else reference
            if value not in [x.to_dict() for x in scope.export_refs]:
                raise RegistryReadSessionError('EXPORT_ACCESS_NOT_GRANTED')
        else:
            raise RegistryReadSessionError('NOT_DISCLOSED')

    def _retained_bytes(self):
        total = sum(snapshot.budget_bytes for _cut, snapshot in self._cuts.values())
        queries = {id(query.entries): query.entries for query, _offset in self._cursors.values()}
        return total + sum(sum(len(canonical_json(entry)) for entry in entries) for entries in queries.values())

    def capture_cut(self, source_id, *, ordinal=None):
        self.final_recheck((source_id,))
        source = self._sources[source_id]
        if not source.authority.scope.disclose_head:
            raise RegistryReadSessionError('NOT_DISCLOSED')
        if ordinal is not None and (type(ordinal) is not int or ordinal < 0):
            raise RegistryReadSessionError('INVALID_CUT')
        if len(self._cuts) >= self.limits.max_cuts:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        core, store = source.resolved.core, source.resolved.core.event_store
        with store.connect() as db:
            db.execute('BEGIN')
            maximum = int(db.execute('SELECT coalesce(max(ordinal),0) FROM events').fetchone()[0])
            upper = maximum if ordinal is None else ordinal
            if upper > maximum:
                raise RegistryReadSessionError('INVALID_CUT')
            scan_rows, scan_bytes = _budget_preflight(core, self.limits, db=db, ordinal=upper)
            budget_bytes = scan_bytes + scan_rows * 256
            if self._retained_bytes() + budget_bytes > self.limits.max_scan_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            writer = int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
            def canonical(alias, table, member, identity, published):
                predicate = store._canonical_member_sql(member_kind=member, member_identity_sql=identity,
                    published_event_sql=published)
                return tuple(db.execute(f'SELECT {alias}.* FROM {table} WHERE {predicate} ORDER BY {alias}.rowid',
                                        (upper, upper, upper, upper)).fetchall())
            event_rows = canonical('e', "events e JOIN transactions t ON t.transaction_id=e.transaction_id AND t.status='committed'",
                                   'event', 'e.event_id', 'e.ordinal')
            events = tuple(store._row_to_envelope(row) for row in event_rows)
            chosen = max((x.ordinal for x in events), default=0)
            if ordinal is not None and ordinal != chosen:
                raise RegistryReadSessionError('INVALID_CUT')
            objects = canonical('o', "objects o JOIN events p ON p.event_id=o.published_event_id JOIN transactions t ON t.transaction_id=o.transaction_id AND t.status='committed'",
                                'object', 'o.version_id', 'p.ordinal')
            relations = canonical('r', "relations r JOIN events p ON p.event_id=r.published_event_id JOIN transactions t ON t.transaction_id=r.transaction_id AND t.status='committed'",
                                  'relation', 'r.relation_id', 'p.ordinal')
            event_map = {str(x.event_id): x for x in events}
            transaction_commits = {str(event.transaction_id): event.ordinal for event in events if event.event_type == 'transaction_committed/v1'}
            publication_ordinals = {row['version_id']: transaction_commits[row['transaction_id']] for row in objects if row['transaction_id'] in transaction_commits}
            promotions = db.execute("SELECT m.member_identity,MAX(e.ordinal) AS ordinal FROM firing_temporary_members m JOIN firing_publications p ON p.firing_version_id=m.firing_version_id JOIN events e ON e.transaction_id=p.published_transaction_id AND e.event_type='transaction_committed/v1' WHERE m.member_kind='object' AND p.state='PUBLISHED' AND e.ordinal<=? GROUP BY m.member_identity", (upper,))
            for promotion in promotions:
                if promotion['member_identity'] in publication_ordinals:
                    publication_ordinals[promotion['member_identity']] = max(publication_ordinals[promotion['member_identity']], promotion['ordinal'])
            terminals = {str(x.transaction_id) for x in events if x.event_type == 'transaction_committed/v1'}
            for row in objects:
                publication = event_map.get(str(row['published_event_id']))
                if (publication is None or publication.event_type != 'object_version_published/v1'
                        or str(publication.transaction_id) != row['transaction_id'] or row['transaction_id'] not in terminals
                        or publication.task_id != core.task_id
                        or canonical_json(publication.payload) != canonical_json({
                            'logical_id': row['logical_id'], 'version_id': row['version_id'], 'object_type': row['object_type'],
                            'size': row['size'], 'media_type': row['media_type'], 'schema_ref': row['schema_ref'],
                            'storage_locator': row['storage_locator'], 'metadata': json.loads(row['metadata_json'])})):
                    raise RegistryReadSessionError('INTEGRITY_FAILED')
            stream_heads = {}
            control = 0
            for event in events:
                stream_heads[event.stream_id] = max(stream_heads.get(event.stream_id, 0), event.stream_sequence)
                if event.task_id == core.task_id and event.task_control_sequence is not None:
                    control = max(control, event.task_control_sequence)
            head = RegistryHead(chosen, writer, control, MappingProxyType(stream_heads))
        cut = SourceCut(source_id, 'cut_' + secrets.token_urlsafe(24), PublicRegistryHead(chosen, writer))
        def check_authorized(ref, operation):
            from .registry_typed_readers import wire_ref
            fields = self._catalog.default_record_projection(wire_ref(ref)[1]['entity_type']) if operation == 'record' else ()
            self._authorize(source_id, ref, operation, fields)
        snapshot = _Snapshot(source_id, head,
            MappingProxyType({r['version_id']: MappingProxyType(dict(r)) for r in objects}), events,
            tuple(MappingProxyType(dict(r)) for r in relations),
            check_authorized,
            self.limits.max_material_bytes, MappingProxyType(publication_ordinals), budget_bytes)
        self.final_recheck((source_id,))
        self._cuts[cut.cut_id] = (cut, snapshot)
        return cut

    def validate_cut(self, value):
        self._alive()
        cut = SourceCut.from_dict(value) if type(value) is dict else value
        if type(cut) is not SourceCut:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        stored = self._cuts.get(cut.cut_id)
        if stored is None or stored[0] != cut:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        self.final_recheck((cut.source_id,))
        return cut

    resolve_cut = validate_cut

    def _read_setup(self, reference, at_cut):
        local = qualified_ref(reference)
        cut = self.validate_cut(at_cut)
        if reference.source_id != cut.source_id:
            raise RegistryReadSessionError('NOT_DISCLOSED')
        source = self._sources[cut.source_id]
        snapshot = self._cuts[cut.cut_id][1]
        return local, cut, source, snapshot

    def _envelope(self, reference, cut, source, record, disclosure):
        local = qualified_ref(reference)
        return {'status': 'ok', 'entry_ref': reference.to_dict(), 'entry_type': local.entity_type,
                'schema_ref': source.resolved.core.catalog.require(local.entity_type, category='object').schema_ref,
                'source_cut': cut.to_dict(), 'record': record, 'disclosure': disclosure,
                'access_revision': source.authority.revision}

    def read_exact(self, *, entry_ref, at_cut, projection=None):
        if projection == 'public_pn':
            return self.read_public_projection(entry_ref=entry_ref, at_cut=at_cut)
        local, cut, source, snapshot = self._read_setup(entry_ref, at_cut)
        try:
            fields = self._catalog.default_record_projection(local.entity_type) if projection is None else projection
            if type(fields) not in (tuple, list) or len(fields) > self.limits.max_projection_fields:
                raise RegistryReadSessionError('INVALID_PROJECTION')
            if len(set(fields)) != len(fields) or set(fields) - set(self._catalog.fields(local.entity_type)):
                raise RegistryReadSessionError('INVALID_PROJECTION')
            self._authorize(cut.source_id, entry_ref, 'record', fields)
            result = self._catalog.read_exact(source.resolved.core, entry_ref, snapshot=snapshot, projection=tuple(fields))
            disclosure = {'projected_fields': list(fields), 'unprovided_fields': sorted(set(fields) - set(result))}
            envelope = self._envelope(entry_ref, cut, source, result, disclosure)
            if len(canonical_json(envelope)) > self.limits.max_response_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            self.final_recheck((cut.source_id,))
            return envelope
        except RegistryReadSessionError:
            raise
        except Exception as exc:
            raise RegistryReadSessionError(getattr(exc, 'code', 'INTEGRITY_FAILED')) from exc

    def read_public_projection(self, *, entry_ref, at_cut):
        local, cut, source, snapshot = self._read_setup(entry_ref, at_cut)
        self._authorize(cut.source_id, entry_ref, 'record', self._catalog.default_record_projection(local.entity_type))
        try:
            result = self._catalog.read_public_projection(source.resolved.core, entry_ref, snapshot=snapshot)
            envelope = self._envelope(entry_ref, cut, source, result, {'projected_fields': ['public_pn']})
            if len(canonical_json(envelope)) > self.limits.max_response_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            self.final_recheck((cut.source_id,))
            return envelope
        except RegistryReadSessionError:
            raise
        except Exception as exc:
            raise RegistryReadSessionError(getattr(exc, 'code', 'INTEGRITY_FAILED')) from exc

    def read_public_mapping(self, *, left_ref, right_ref, at_cut):
        _, cut, source, snapshot = self._read_setup(left_ref, at_cut)
        if right_ref.source_id != cut.source_id:
            raise RegistryReadSessionError('MISSING_SOURCE')
        for ref in (left_ref, right_ref):
            self._authorize(cut.source_id, ref, 'record', self._catalog.default_record_projection(qualified_ref(ref).entity_type))
        try:
            result = self._catalog.read_public_mapping(source.resolved.core, left_ref, right_ref, snapshot=snapshot)
            if len(canonical_json(result)) > self.limits.max_response_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            self.final_recheck((cut.source_id,))
            return result
        except RegistryReadSessionError:
            raise
        except Exception as exc:
            raise RegistryReadSessionError(getattr(exc, 'code', 'INTEGRITY_FAILED')) from exc

    def read_material(self, *, resource_ref, at_cut, max_bytes=None, representation='bytes'):
        """Read a verified bytes/UTF-8 body, retaining legacy bytes/digest fields."""
        from .registry_typed_readers import payload_at
        if type(resource_ref) is not SourceQualifiedResourceRef:
            raise TypeError('material reads require a source-qualified resource reference')
        if type(representation) is not str or representation not in ('bytes', 'utf8'):
            raise RegistryReadSessionError('INVALID_REPRESENTATION')
        _, cut, source, snapshot = self._read_setup(resource_ref, at_cut)
        self._authorize(cut.source_id, resource_ref, 'material')
        limit = self.limits.max_material_bytes if max_bytes is None else max_bytes
        if type(limit) is not int or limit < 1:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        limit = min(limit, self.limits.max_material_bytes)
        try:
            payload, prepared = payload_at(source.resolved.core, resource_ref, snapshot, material=True, max_bytes=limit)
            digest = hashlib.sha256(payload).hexdigest()
            result = {'status': 'ok', 'resource_ref': resource_ref.to_dict(), 'source_cut': cut.to_dict(),
                      'bytes': payload, 'sha256': digest, 'byte_count': len(payload),
                      'body': payload, 'representation': representation, 'material_digest': digest,
                      'content_schema_ref': prepared.metadata['content_schema_ref'],
                      'media_type': prepared.media_type, 'access_revision': source.authority.revision,
                      'integrity': 'registered_size_and_recorded_digest' if prepared.metadata.get('descriptors', {}).get('content_sha256') else 'registered_size'}
            if representation == 'utf8':
                try:
                    body = payload.decode('utf-8', errors='strict')
                except UnicodeDecodeError as exc:
                    raise RegistryReadSessionError('INVALID_UTF8') from exc
                result['body'] = body
            self.final_recheck((cut.source_id,))
            return result
        except RegistryReadSessionError:
            raise
        except Exception as exc:
            raise RegistryReadSessionError(getattr(exc, 'code', 'INTEGRITY_FAILED')) from exc

    def authorize_export(self, refs, *, destination, at_cuts):
        for ref in refs:
            if ref.source_id not in at_cuts:
                raise RegistryReadSessionError('MISSING_SOURCE')
            _, cut, source, _ = self._read_setup(ref, at_cuts[ref.source_id])
            self._authorize(cut.source_id, ref, 'export')
            if destination not in source.authority.scope.export_destinations:
                raise RegistryReadSessionError('EXPORT_ACCESS_NOT_GRANTED')
        self.final_recheck(tuple(at_cuts))

    def capture_observation(self, *_args, **_kwargs):
        raise RegistryReadSessionError('UNSUPPORTED_OBSERVATION_CAPTURE')

    def _validate_query(self, spec):
        if type(spec) is not IndexQuery:
            raise TypeError('query requires a typed IndexQuery')
        try:
            json.dumps(spec.to_dict(), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RegistryReadSessionError('INVALID_QUERY') from exc
        if (len(spec.source_ids) > self.limits.max_sources or len(spec.clauses) > self.limits.max_clauses
                or sum(len(x.predicates) for x in spec.clauses) > self.limits.max_predicates
                or spec.page_size > self.limits.max_page_size
                or len(canonical_json(spec.to_dict())) > self.limits.max_query_bytes):
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        if set(spec.source_ids) - (set(self._sources) | set(self._failures)):
            raise RegistryReadSessionError('NOT_DISCLOSED')
        if spec.cuts is not None and set(spec.cuts) != set(spec.source_ids):
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        clauses = []
        try:
            for clause in spec.clauses:
                fields = self._catalog.fields(clause.entry_type)
                projection = clause.projection or self._catalog.default_index_projection(clause.entry_type)
                if len(projection) > self.limits.max_projection_fields:
                    raise RegistryReadSessionError('LIMIT_EXCEEDED')
                requested = set(projection) | {p.field for p in clause.predicates}
                if requested - set(fields):
                    raise RegistryReadSessionError('INVALID_PROJECTION')
                for pred in clause.predicates:
                    if pred.operator == 'in' and (type(pred.value) not in (list, tuple)
                            or not pred.value or len(pred.value) > self.limits.max_in_values):
                        raise RegistryReadSessionError('LIMIT_EXCEEDED')
                    if pred.operator in ('lt', 'le', 'gt', 'ge') and fields[pred.field] not in ('number', 'time'):
                        raise RegistryReadSessionError('UNSUPPORTED_PREDICATE')
                    values = pred.value if pred.operator == 'in' else (pred.value,)
                    kind = fields[pred.field]
                    for value in values:
                        if (kind in ('string', 'time') and type(value) is not str
                                or kind == 'number' and (type(value) not in (int, float))
                                or kind == 'boolean' and type(value) is not bool
                                or kind == 'ref' and value is not None and type(value) is not dict
                                or kind == 'array' and type(value) not in (list, tuple)):
                            raise RegistryReadSessionError('INVALID_PREDICATE')
                for source_id in spec.source_ids:
                    source = self._sources.get(source_id)
                    if source is not None and not source.failure:
                        try:
                            source.resolved.core.catalog.require(clause.entry_type, category='object')
                        except Exception as exc:
                            raise RegistryReadSessionError('UNSUPPORTED_READER_VERSION') from exc
                        allowed = source.authority.scope.index_fields.get(clause.entry_type)
                        if allowed is None or requested - set(allowed):
                            raise RegistryReadSessionError('NOT_DISCLOSED')
                clauses.append((clause, tuple(projection)))
        except RegistryReadSessionError:
            raise
        except Exception as exc:
            raise RegistryReadSessionError(getattr(exc, 'code', 'UNSUPPORTED_ENTRY_TYPE')) from exc
        return clauses

    @staticmethod
    def _matches(values, predicates):
        for pred in predicates:
            if pred.field not in values:
                return False
            actual, expected = values[pred.field], pred.value
            # JSON equality distinguishes booleans from numeric values.
            if pred.operator == 'eq' and canonical_json(actual) != canonical_json(expected):
                return False
            if pred.operator == 'in' and not any(canonical_json(actual) == canonical_json(x) for x in expected):
                return False
            if pred.operator in ('lt', 'le', 'gt', 'ge'):
                try:
                    matched = {'lt': lambda: actual < expected, 'le': lambda: actual <= expected,
                               'gt': lambda: actual > expected, 'ge': lambda: actual >= expected}[pred.operator]()
                except TypeError:
                    return False
                if not matched:
                    return False
        return True

    def query_index(self, spec, *, cursor=None):
        self._alive()
        clauses = self._validate_query(spec)
        fingerprint = document_digest(spec.to_dict())
        if cursor is not None:
            if type(cursor) is not str or cursor not in self._cursors:
                raise RegistryReadSessionError('CURSOR_MISMATCH')
            query, offset = self._cursors[cursor]
            if query.fingerprint != fingerprint:
                raise RegistryReadSessionError('CURSOR_MISMATCH')
        else:
            entries, cuts, failures = {}, {}, {}
            entry_bytes = 0
            for source_id in sorted(spec.source_ids):
                source = self._sources.get(source_id)
                if source is None or source.failure:
                    failures[source_id] = source.failure if source else self._failures[source_id]
                    continue
                try:
                    value = None if spec.cuts is None else spec.cuts[source_id]
                    if type(value) is HistoricalCutRequest:
                        if value.source_id != source_id:
                            raise RegistryReadSessionError('CURSOR_MISMATCH')
                        cut = self.capture_cut(source_id, ordinal=value.ordinal)
                    elif value is None:
                        cut = self.capture_cut(source_id)
                    else:
                        cut = self.validate_cut(value)
                        if cut.source_id != source_id:
                            raise RegistryReadSessionError('CURSOR_MISMATCH')
                    cuts[source_id] = cut
                    snapshot = self._cuts[cut.cut_id][1]
                    retained_bytes = self._retained_bytes()
                    for row in snapshot.objects.values():
                        selected = [(clause, projection) for clause, projection in clauses if clause.entry_type == row['object_type']]
                        if not selected:
                            continue
                        local = VersionRef(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id']))
                        reference = (SourceQualifiedResourceRef(source_id, ResourceVersionRef(local.entity_id, local.version_id))
                                     if row['object_type'] == 'resource_version/v1' else SourceQualifiedVersionRef(source_id, local))
                        # Per-object managed resource scope remains enforced.
                        try:
                            self._authorize(source_id, reference, 'index')
                        except RegistryReadSessionError as exc:
                            if exc.code == 'NOT_DISCLOSED':
                                continue
                            raise
                        needed_fields = {field for clause, projection in selected
                            for field in (*projection, *(predicate.field for predicate in clause.predicates))}
                        values = self._catalog.read_index(source.resolved.core, reference, snapshot=snapshot,
                            projection=tuple(sorted(needed_fields)))
                        projected, requested = {}, set()
                        for clause, projection in selected:
                            if self._matches(values, clause.predicates):
                                requested.update(projection)
                                projected.update({k: values[k] for k in projection if k in values})
                        if not requested:
                            continue
                        key = (source_id, row['object_type'], row['logical_id'], row['version_id'])
                        entry = {'entry_ref': reference.to_dict(), 'entry_type': row['object_type'],
                            'schema_ref': row['schema_ref'], 'fields': projected,
                            'disclosure': {'projected_fields': sorted(requested), 'unprovided_fields': sorted(requested - set(projected))},
                            'source_cut': cut.to_dict(), 'record_access': 'not_checked',
                            'material_access': 'not_checked' if row['object_type'] == 'resource_version/v1' else 'not_applicable'}
                        entry_bytes += len(canonical_json(entry))
                        if retained_bytes + entry_bytes > self.limits.max_scan_bytes:
                            raise RegistryReadSessionError('LIMIT_EXCEEDED')
                        entries[key] = entry
                except RegistryReadSessionError as exc:
                    if exc.code in {'CURSOR_MISMATCH', 'INVALID_CUT', 'LIMIT_EXCEEDED', 'INVALID_PROJECTION'}:
                        raise
                    failures[source_id] = exc.code
                    entries = {key: value for key, value in entries.items() if key[0] != source_id}
                except Exception as exc:
                    failures[source_id] = getattr(exc, 'code', 'READ_FAILED')
                    entries = {key: value for key, value in entries.items() if key[0] != source_id}
            query = _Query(fingerprint, tuple(entries[key] for key in sorted(entries)), cuts, spec.source_ids, failures)
            offset = 0
        failures = dict(query.failures)
        for source_id in query.source_ids:
            if source_id in failures:
                continue
            try:
                self.final_recheck((source_id,))
            except RegistryReadSessionError as exc:
                if exc.code in {'SESSION_CLOSED', 'SESSION_EXPIRED'}:
                    raise
                failures[source_id] = exc.code
        # Remove invalidated source material from retained page state, adjusting the
        # stable offset so independent entries are neither skipped nor repeated.
        if failures != query.failures:
            offset = sum(x['entry_ref']['source_id'] not in failures for x in query.entries[:offset])
            query = _Query(query.fingerprint, tuple(x for x in query.entries if x['entry_ref']['source_id'] not in failures),
                           {k: v for k, v in query.cuts.items() if k not in failures}, query.source_ids, failures)
        page, position = [], offset
        response_bytes = 0
        while position < len(query.entries) and len(page) < spec.page_size:
            value = query.entries[position]
            if value['entry_ref']['source_id'] in failures:
                position += 1
                continue
            size = len(canonical_json(value))
            if response_bytes + size > self.limits.max_response_bytes - 4096:
                if not page:
                    raise RegistryReadSessionError('LIMIT_EXCEEDED')
                break
            page.append(value); response_bytes += size; position += 1
        more = any(entry['entry_ref']['source_id'] not in failures for entry in query.entries[position:])
        continuation = None
        if more:
            if not hasattr(self, '_cursor_secret'):
                self._cursor_secret = secrets.token_hex(32)
            continuation = 'cursor_' + document_digest({'secret': self._cursor_secret, 'query': fingerprint,
                'cuts': {k: v.to_dict() for k, v in query.cuts.items()}, 'offset': position})
            if continuation not in self._cursors and len(self._cursors) >= self.limits.max_cursors:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            self._cursors[continuation] = (_Query(query.fingerprint, query.entries, query.cuts, query.source_ids, failures), position)
        states = []
        for source_id in query.source_ids:
            if source_id in failures:
                states.append({'source_id': source_id, 'source_ref': None, 'cut': None, 'access_revision': None,
                    'access_state': failures[source_id], 'coverage': {'state': 'unavailable', 'loaded_count': None, 'total_count': None}})
                continue
            source = self._sources[source_id]
            remaining = any(x['entry_ref']['source_id'] == source_id for x in query.entries[position:])
            count = sum(x['entry_ref']['source_id'] == source_id for x in page)
            total = sum(x['entry_ref']['source_id'] == source_id for x in query.entries)
            states.append({'source_id': source_id, 'source_ref': source.selection.source_ref.to_dict(),
                'cut': query.cuts[source_id].to_dict(), 'access_revision': source.authority.revision,
                'access_state': 'readable', 'coverage': {'state': 'partial' if remaining else 'complete',
                    'loaded_count': count, 'total_count': None if remaining else total}})
        result = {'schema_version': 'rpnh/registry_index_page/v1', 'entries': page, 'source_results': states,
            'continuation': continuation, 'coverage': {'state': 'partial' if more or failures else 'complete',
                'loaded_count': len(page), 'total_count': None if more or failures else len(query.entries)},
            'global_atomic_snapshot': False}
        if len(canonical_json(result)) > self.limits.max_response_bytes:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        # The serialization and pagination work above is also inside the
        # authorization interval. Never return a cached page after its expiry.
        for source_id in query.source_ids:
            if source_id not in failures:
                self.final_recheck((source_id,))
        self._alive()
        return json.loads(canonical_json(result))


def open_registry_session(request: ReadSessionRequest, *, host: RegistryReadHostBinding) -> RegistryReadSession:
    return RegistryReadSession(request, host)


def query_index(session, spec, *, cursor=None):
    return session.query_index(spec, cursor=cursor)


def read_exact(session, **kwargs):
    return session.read_exact(**kwargs)


def read_material(session, **kwargs):
    return session.read_material(**kwargs)
