"""Independent, finite-lifetime read sessions over existing Registry authority.

HOST resolver/configuration is trusted input, never submitted in a read request.
No operation here creates a task, grant, writer fence, schema, or Observation.
SQLite live read-only WAL sidecars may change; canonical Registry facts do not.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
from types import MappingProxyType
from typing import Callable, Mapping, ClassVar

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


def _publication_key_capture_query(store):
    """Select original publication keys and terminal witnesses at one cut.

    EXISTS avoids copying all member identities into a Python set (or a SQL
    UNION). The same statement is aggregated before any key rows are fetched.
    Witness envelopes already belong to the event capture; their additional
    verification work and identity bytes are nevertheless charged here.
    """
    objects = store._canonical_member_sql(member_kind='object',
        member_identity_sql='o.version_id', published_event_sql='p.ordinal')
    relations = store._canonical_member_sql(member_kind='relation',
        member_identity_sql='r.relation_id', published_event_sql='p.ordinal')
    columns = ('t.transaction_id', 't.idempotency_key', 't.task_id', 't.writer_epoch',
        'e.event_id', 'e.event_type', 'e.criticality', 'e.task_id', 'e.transaction_id',
        'e.stream_id', 'e.aggregate_id', 'e.aggregate_type', 'e.payload_schema_ref',
        'e.producer_invocation_id', 'e.writer_fencing_epoch', 'e.idempotency_key',
        'e.ordinal')
    size = '+'.join(f'coalesce(length(CAST({column} AS BLOB)),0)' for column in columns)
    # The retained JSON map may escape each raw key/identity byte (for example
    # a control character) as six bytes. Reserve it separately before copying.
    size += ('+6*length(CAST(t.transaction_id AS BLOB))'
             '+6*length(CAST(t.idempotency_key AS BLOB))+8')
    return ("SELECT t.transaction_id,t.idempotency_key,t.task_id,t.writer_epoch,"
        f"e.event_id AS terminal_event_id,{size} AS capture_bytes "
        "FROM transactions t LEFT JOIN events e ON e.transaction_id=t.transaction_id "
        "AND e.event_type IN ('transaction_committed/v1','transaction_aborted/v1') "
        "AND e.ordinal<=? WHERE t.status='committed' AND ("
        "EXISTS (SELECT 1 FROM objects o JOIN events p ON p.event_id=o.published_event_id "
        f"WHERE o.transaction_id=t.transaction_id AND {objects}) OR "
        "EXISTS (SELECT 1 FROM relations r JOIN events p ON p.event_id=r.published_event_id "
        f"WHERE r.transaction_id=t.transaction_id AND {relations}))")


@dataclass(frozen=True, slots=True)
class _VerifiedAuthority:
    context: object
    scope: ObserverReadScope
    revision: str
    expires_at: datetime | None
    kind: str


# Legacy observer/invocation adapters retain only their original metadata grant.
# Catalog growth requires an explicit ObserverReadScope; it grants no material.
_LEGACY_RESOURCE_FIELDS = ('resource_id', 'resource_version_id', 'media_type',
    'content_schema_ref', 'byte_count', 'summary', 'commit_ordinal')


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
                fields = _LEGACY_RESOURCE_FIELDS
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
    publication_transaction_keys: Mapping = field(repr=False)
    budget_bytes: int
    _origin_context: object | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class _Query:
    kind: ClassVar[str] = 'index'
    fingerprint: str
    entries: tuple
    cuts: dict
    source_ids: tuple
    failures: dict
    entry_bytes_by_source: dict = field(default_factory=dict)


def _requests_start(entry_type, fields):
    from .registry_typed_readers import _START_FIELDS
    return entry_type == 'transition_firing/v1' and any(field in _START_FIELDS for field in fields)


class _StartReadOwners:
    """Source-local validation owners retained through ordinary index delivery.

    Prior owners remain alive: their descriptors, failed traceback buffers and
    projected entries cannot disappear from the next source's byte ledger.
    These owners never request Core completion validation or new permissions.
    """
    def __init__(self, session):
        self.session = session
        self.contexts = []
        self.source_ids = []
        self.created_cursors = []
        self.error_reserved = False
        self.pending_capture_bytes = 0
        self._delivery_bytes = 0
        self._capture_errors = []

    @staticmethod
    def bytes_bound(value):
        from ._origin_query_context import _bytes_bound
        return _bytes_bound(value)

    def retained_baseline(self):
        current = self.session._retained_bytes()
        return max(current + self.missing_snapshot_bytes(),
            max((context._initial_retained_bytes for context in self.contexts), default=current))

    def check_retained(self, additional=0):
        total = (self.retained_baseline() + self.pending_capture_bytes + self._delivery_bytes
            + sum(context.reservation.scratch_bytes for context in self.contexts) + additional)
        if total > self.session.limits.max_scan_bytes:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')

    def reserve(self, *, size=0):
        # Only the ordinary collection/page handoff needs this fallback when
        # every capture failed before a source-local context could be created.
        self.check_retained(size)
        self._delivery_bytes += size

    def prepare_capture(self, source_id):
        if source_id not in self.source_ids:
            self.source_ids.append(source_id)
        if not self.error_reserved:
            from ._product_origin_core import _ERROR_BUFFER_BYTES
            self.reserve(size=_ERROR_BUFFER_BYTES + 4096 + 512 * len(self.session._sources))
            self.error_reserved = True

    def reserve_capture(self, size):
        self.check_retained(size)
        self.pending_capture_bytes += size

    def retain_capture_error(self, error):
        if self.error_reserved:
            # The preowned group control allowance covers one failure slot per
            # attempted source. Its hydrated bytes remain in pending_capture.
            self._capture_errors.append(error)

    def publish_capture(self, size):
        # The session now owns the exact snapshot budget; transfer once.
        self.pending_capture_bytes -= size

    def begin(self, snapshot, spec):
        from ._origin_query_context import _OriginQueryContext
        from ._product_origin_core import _ERROR_BUFFER_BYTES
        previous_work = self.contexts[-1].reservation.work if self.contexts else 0
        context = None
        def extra():
            current = self.session._retained_bytes()
            initial = current if context is None else context._initial_retained_bytes
            return (sum(item.reservation.scratch_bytes for item in self.contexts if item is not context)
                + self.pending_capture_bytes + self._delivery_bytes
                + max(0, self.retained_baseline() - max(initial, current)))
        if snapshot.source_id not in self.source_ids:
            self.source_ids.append(snapshot.source_id)
        context = _OriginQueryContext(self.session, snapshot, retained_extra=extra)
        self.contexts.append(context)
        # Subsequent sources continue W rather than resetting the logical cap.
        context.reservation.work = previous_work
        context.reserve(size=(0 if self.error_reserved else _ERROR_BUFFER_BYTES) + 4096
            + 512 * (len(spec.source_ids) + len(spec.clauses))
            + sum(context.bytes_bound(source) for source in spec.source_ids)
            + sum(context.bytes_bound(clause.projection) + context.bytes_bound(clause.entry_type)
                + sum(context.bytes_bound(predicate.value) + 256 for predicate in clause.predicates)
                for clause in spec.clauses))
        self.error_reserved = True
        return context

    def missing_snapshot_bytes(self):
        # Invalidation removes cuts from session accounting before an earlier
        # owner necessarily stops holding those original captured arrays.
        missing = 0
        for position, item in enumerate(self.contexts):
            snapshot = item.snapshot
            same = lambda other: (snapshot.objects is other.objects
                and snapshot.events is other.events and snapshot.source_id == other.source_id)
            if any(same(captured) for _cut, captured in self.session._cuts.values()):
                continue
            if any(same(self.contexts[index].snapshot) for index in range(position)):
                continue
            missing += snapshot.budget_bytes
        return missing

    def capture_retained_extra(self):
        # Capture has no query-context floor: include all group-held bytes
        # absent from the session before hydrating another source's history.
        return (self.retained_baseline() - self.session._retained_bytes()
            + self.pending_capture_bytes + self._delivery_bytes
            + sum(item.reservation.scratch_bytes for item in self.contexts))

    @property
    def current(self):
        return self.contexts[-1] if self.contexts else None

    def check(self, source_ids, failures=None):
        for source_id in source_ids:
            try:
                self.session.final_recheck((source_id,))
            except RegistryReadSessionError as exc:
                if (failures is None or source_id not in failures
                        or exc.code in {'SESSION_CLOSED', 'SESSION_EXPIRED'}):
                    raise
                # An ordinary failed source remains a source-result failure;
                # current authority takes precedence over its old data error.
                failures[source_id] = exc.code
        self.session._alive()
        if self.error_reserved or self.contexts:
            self.check_retained()
        self.session._alive()

    def fail(self, exc, source_ids):
        from ._product_origin_core import _SAFE_ERROR_CODES
        code = getattr(exc, 'code', 'INTEGRITY_FAILED')
        error = RegistryReadSessionError(code if code in _SAFE_ERROR_CODES else 'INTEGRITY_FAILED')
        if self.error_reserved:
            if len(canonical_json(error.to_dict())) > self.session.limits.max_response_bytes:
                error = RegistryReadSessionError('LIMIT_EXCEEDED')
                canonical_json(error.to_dict())
        # Keep the original exception and its charged traceback alive through
        # both safe serialization and the authoritative final callback.
        self.session.final_recheck(source_ids)
        self.session._alive()
        if self.error_reserved:
            try:
                self.check_retained()
            except RegistryReadSessionError:
                error = RegistryReadSessionError('LIMIT_EXCEEDED')
                canonical_json(error.to_dict())
                self.session.final_recheck(source_ids)
                self.session._alive()
        raise error from None

    def close(self):
        from ._product_origin_core import _clear_exception_frames
        for error in self._capture_errors:
            _clear_exception_frames(error)
        self._capture_errors.clear()
        for context in self.contexts:
            context.close()
        self.contexts.clear()
        self.pending_capture_bytes = 0
        self._delivery_bytes = 0


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
        queries, origins = {}, {}
        for token, (query, offset) in self._cursors.items():
            if query.kind == 'index':
                queries[id(query.entries)] = query
            else:
                from ._product_origin_query import _cursor_record_bytes
                origins[id(query)] = query
                total += _cursor_record_bytes(token, offset)
        return (total + sum(sum(query.entry_bytes_by_source.values()) for query in queries.values())
            + sum(query.budget_bytes for query in origins.values()))

    def capture_cut(self, source_id, *, ordinal=None):
        return self._capture_cut(source_id, ordinal=ordinal)

    def _capture_cut(self, source_id, *, ordinal=None, _retained_extra=None, _capture_owner=None):
        if _capture_owner is None:
            return self._capture_cut_impl(source_id, ordinal=ordinal, _retained_extra=_retained_extra)
        try:
            return self._capture_cut_impl(source_id, ordinal=ordinal,
                _retained_extra=_retained_extra, _capture_owner=_capture_owner)
        except Exception as exc:
            _capture_owner.retain_capture_error(exc)
            raise

    def _capture_cut_impl(self, source_id, *, ordinal=None, _retained_extra=None, _capture_owner=None):
        if _capture_owner is not None:
            _capture_owner.prepare_capture(source_id)
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
            key_sql = _publication_key_capture_query(store)
            key_args = (upper,) * 9
            key_budget = db.execute('SELECT count(*)+count(terminal_event_id),'
                f'coalesce(sum(capture_bytes),0) FROM ({key_sql})', key_args).fetchone()
            # One key row and each terminal inspected consume the existing
            # capture row budget, including duplicate or rejected witnesses.
            # Identities, keys, witness bytes and the original per-row allowance
            # also remain charged for every retained cut.
            scan_rows += int(key_budget[0]); scan_bytes += int(key_budget[1])
            if scan_rows > self.limits.max_scan_rows or scan_bytes > self.limits.max_scan_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            budget_bytes = scan_bytes + scan_rows * 256
            if _capture_owner is not None:
                # Establish ownership before the first history row is hydrated.
                # On failure this charge outlives the pending capture traceback.
                _capture_owner.reserve_capture(budget_bytes)
            else:
                extra = 0 if _retained_extra is None else _retained_extra()
                if type(extra) is not int or extra < 0:
                    raise RegistryReadSessionError('LIMIT_EXCEEDED')
                if self._retained_bytes() + extra + budget_bytes > self.limits.max_scan_bytes:
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
            publication_keys = {}
            for key_row in db.execute(key_sql, key_args):
                transaction_id, key = key_row['transaction_id'], key_row['idempotency_key']
                terminal = event_map.get(key_row['terminal_event_id'])
                if (type(transaction_id) is not str or type(key) is not str or not key
                        or transaction_id in publication_keys or terminal is None
                        or terminal.event_type != 'transaction_committed/v1'
                        or terminal.transaction_id.kind != 'transaction'
                        or str(terminal.transaction_id) != transaction_id
                        or terminal.event_id.kind != 'event'
                        or key_row['task_id'] != str(core.task_id) or terminal.task_id != core.task_id
                        or terminal.criticality != 'authoritative'
                        or terminal.stream_id != f'transaction:{transaction_id}'
                        or terminal.aggregate_id != transaction_id or terminal.aggregate_type != 'transaction'
                        or terminal.payload_schema_ref != 'registry_v1/transaction_committed/v1'
                        or terminal.producer_invocation_id is not None
                        or terminal.writer_fencing_epoch != key_row['writer_epoch']
                        or terminal.idempotency_key != key):
                    raise RegistryReadSessionError('INTEGRITY_FAILED')
                publication_keys[transaction_id] = key
            # These keys prove original publication, not subsequent canonical
            # promotion. Never replace them with the promoting transaction key.
            publication_keys = MappingProxyType(publication_keys)
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
            self.limits.max_material_bytes, MappingProxyType(publication_ordinals), publication_keys, budget_bytes)
        self.final_recheck((source_id,))
        if _capture_owner is not None:
            # Pending bytes already include this candidate; do not add them twice.
            _capture_owner.check_retained()
        elif _retained_extra is not None:
            extra = _retained_extra()
            if (type(extra) is not int or extra < 0
                    or self._retained_bytes() + extra + budget_bytes > self.limits.max_scan_bytes):
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
        self._cuts[cut.cut_id] = (cut, snapshot)
        if _capture_owner is not None:
            _capture_owner.publish_capture(budget_bytes)
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
        if (type(entry_ref) is SourceQualifiedVersionRef and type(projection) in (tuple, list)
                and _requests_start(entry_ref.ref.entity_type, projection)):
            from ._product_origin_core import _clear_exception_frames
            try:
                return self._read_exact(entry_ref=entry_ref, at_cut=at_cut, projection=projection)
            except Exception as exc:
                code = getattr(exc, 'code', 'INTEGRITY_FAILED')
                _clear_exception_frames(exc)
            raise RegistryReadSessionError(code) from None
        return self._read_exact(entry_ref=entry_ref, at_cut=at_cut, projection=projection)

    def _read_exact(self, *, entry_ref, at_cut, projection=None):
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
            if _requests_start(local.entity_type, fields):
                return self._read_start_exact(entry_ref, cut, source, snapshot, tuple(fields))
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

    def _read_start_exact(self, entry_ref, cut, source, snapshot, fields):
        from ._product_origin_core import _clear_exception_frames
        try:
            return self._read_start_exact_impl(entry_ref, cut, source, snapshot, fields)
        except Exception as exc:
            code = getattr(exc, 'code', 'INTEGRITY_FAILED')
            _clear_exception_frames(exc)
        del source, snapshot
        raise RegistryReadSessionError(code) from None

    def _read_start_exact_impl(self, entry_ref, cut, source, snapshot, fields):
        from ._origin_query_context import _OriginQueryContext
        from ._product_origin_core import _ERROR_BUFFER_BYTES, _SAFE_ERROR_CODES, _final_delivery_check
        context = getattr(snapshot, '_origin_context', None)
        owned = context is None
        error_reserved = False
        try:
            try:
                if owned:
                    context = _OriginQueryContext(self, snapshot)
                context.reserve(size=_ERROR_BUFFER_BYTES)
                error_reserved = True
                context.reserve(size=2048 + 2 * context.bytes_bound(fields)
                    + 2 * context.bytes_bound(entry_ref.to_dict()) + 2 * context.bytes_bound(cut.to_dict()))
                result = self._catalog.read_exact(source.resolved.core, entry_ref,
                    snapshot=context.snapshot, projection=fields)
                from ._product_origin_includes import _verify_start_projection
                _verify_start_projection(context, entry_ref.to_dict()['ref'], result, fields)
                context.reserve(size=context.bytes_bound(result) + 4096
                    + context.bytes_bound(fields) + context.bytes_bound(cut.to_dict()))
                disclosure = {'projected_fields': list(fields), 'unprovided_fields': sorted(set(fields) - set(result))}
                envelope = self._envelope(entry_ref, cut, source, result, disclosure)
                with context.scratch(envelope):
                    encoded = canonical_json(envelope)
                    if len(encoded) > self.limits.max_response_bytes:
                        raise RegistryReadSessionError('LIMIT_EXCEEDED')
                    del encoded
            except Exception as exc:
                code = getattr(exc, 'code', 'INTEGRITY_FAILED')
                if code == 'NOT_PRESENT_AT_CUT' and str(entry_ref.ref.version_id) in snapshot.objects:
                    code = 'INTEGRITY_FAILED'
                error = RegistryReadSessionError(code if code in _SAFE_ERROR_CODES else 'INTEGRITY_FAILED')
                if error_reserved:
                    if len(canonical_json(error.to_dict())) > self.limits.max_response_bytes:
                        error = RegistryReadSessionError('LIMIT_EXCEEDED')
                        canonical_json(error.to_dict())
                _final_delivery_check(self, cut, context if error_reserved else None)
                raise error from None
            _final_delivery_check(self, cut, context)
            return envelope
        finally:
            if owned and context is not None:
                context.close()

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

    def _collect_index(self, spec, *, _origin_context=None, _start_owners=None):
        # A direct private ordinary collection also gets a safe handoff. Public
        # IndexQuery supplies its owner group through final page serialization.
        if _origin_context is None and _start_owners is None and self._query_requests_start(spec):
            from ._product_origin_core import _clear_exception_frames
            try:
                return self._collect_start_index(spec)
            except Exception as exc:
                code = getattr(exc, 'code', 'INTEGRITY_FAILED')
                _clear_exception_frames(exc)
            raise RegistryReadSessionError(code) from None
        return self._collect_index_owned(spec, _origin_context=_origin_context, _start_owners=_start_owners)

    def _collect_start_index(self, spec):
        owners = _StartReadOwners(self)
        try:
            try:
                result = self._collect_index_owned(spec, _start_owners=owners)
                while True:
                    previous_failures = dict(result.failures)
                    if owners.current is not None:
                        with owners.current.scratch(size=owners.current.bytes_bound(result.entries)
                                + owners.current.bytes_bound(result.failures) + 256):
                            canonical_json({'entries': result.entries, 'failures': result.failures})
                    else:
                        owners.reserve(size=owners.bytes_bound(result.entries) + owners.bytes_bound(result.failures) + 256)
                        canonical_json({'entries': result.entries, 'failures': result.failures})
                    owners.check(tuple(owners.source_ids), result.failures)
                    if result.failures == previous_failures:
                        return result
            except Exception as exc:
                if not owners.error_reserved and not owners.contexts:
                    raise
                owners.fail(exc, tuple(owners.source_ids))
        finally:
            owners.close()

    def _collect_index_owned(self, spec, *, _origin_context=None, _start_owners=None):
        """Collect the complete legacy authorized index result without paging.

        The private result retains exact cuts and per-source failures: empty
        entries are readable-empty only for sources absent from failures. This
        does not allocate a cursor or provide a delivery-time final recheck.
        Consumers must retain the original final authorization/TTL checks.

        An optional private query owner reserves built-in reader work and
        allocations before construction. Trusted HOST callback signatures and
        ordinary non-Start collection behavior remain unchanged.
        """
        self._alive()
        context = _origin_context
        if context is not None:
            if type(spec) is not IndexQuery:
                raise TypeError('query requires a typed IndexQuery')
            context.reserve(size=4096 + 512 * (len(spec.source_ids) + len(spec.clauses))
                + sum(context.bytes_bound(predicate.value) + 256
                    for clause in spec.clauses for predicate in clause.predicates))
        clauses = self._validate_query(spec)
        fingerprint = document_digest(spec.to_dict())
        entries, cuts, failures = {}, {}, {}
        entry_bytes_by_source = {}
        entry_bytes = 0
        for source_id in sorted(spec.source_ids):
            context = _origin_context
            source = self._sources.get(source_id)
            if source is None or source.failure:
                failures[source_id] = source.failure if source else self._failures[source_id]
                continue
            try:
                if _start_owners is not None and source_id not in _start_owners.source_ids:
                    _start_owners.source_ids.append(source_id)
                value = None if spec.cuts is None else spec.cuts[source_id]
                if type(value) is HistoricalCutRequest:
                    if value.source_id != source_id:
                        raise RegistryReadSessionError('CURSOR_MISMATCH')
                    cut = (self.capture_cut(source_id, ordinal=value.ordinal) if _start_owners is None else
                        self._capture_cut(source_id, ordinal=value.ordinal, _retained_extra=_start_owners.capture_retained_extra, _capture_owner=_start_owners))
                elif value is None:
                    cut = (self.capture_cut(source_id) if _start_owners is None else
                        self._capture_cut(source_id, _retained_extra=_start_owners.capture_retained_extra, _capture_owner=_start_owners))
                else:
                    cut = self.validate_cut(value)
                    if cut.source_id != source_id:
                        raise RegistryReadSessionError('CURSOR_MISMATCH')
                cuts[source_id] = cut
                snapshot = self._cuts[cut.cut_id][1]
                if context is None and _start_owners is not None:
                    context = _start_owners.begin(snapshot, spec)
                if context is not None:
                    if (context.snapshot.source_id != source_id
                            or context.snapshot.objects is not snapshot.objects
                            or context.snapshot.events is not snapshot.events):
                        raise RegistryReadSessionError('CURSOR_MISMATCH')
                    snapshot = context.snapshot
                retained_bytes = self._retained_bytes()
                for row in snapshot.objects.values():
                    selected = [(clause, projection) for clause, projection in clauses if clause.entry_type == row['object_type']]
                    if not selected:
                        continue
                    if context is not None:
                        context.reserve(size=1024 + sum(context.bytes_bound(row[key])
                            for key in ('object_type', 'logical_id', 'version_id')))
                    local = VersionRef(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id']))
                    reference = (SourceQualifiedResourceRef(source_id, ResourceVersionRef(local.entity_id, local.version_id))
                                 if row['object_type'] == 'resource_version/v1' else SourceQualifiedVersionRef(source_id, local))
                    needed_fields = {field for clause, projection in selected
                        for field in (*projection, *(predicate.field for predicate in clause.predicates))}
                    # Per-object managed resource scope remains enforced.
                    try:
                        self._authorize(source_id, reference, 'index', needed_fields)
                    except RegistryReadSessionError as exc:
                        if exc.code == 'NOT_DISCLOSED':
                            continue
                        raise
                    if _origin_context is not None and row['object_type'] == 'firing_completion/v2':
                        # Every collected completion, including later predicate
                        # nonmatches, gets the finite Core exact/publication
                        # contract before configured projection callbacks run.
                        from ._product_origin_core import _CoreProof
                        context.reserve(size=1024 + sum(context.bytes_bound(row[key])
                            for key in ('object_type', 'logical_id', 'version_id')))
                        _CoreProof(self, cut, source, context).exact(
                            reference.to_dict()['ref'], 'firing_completion/v2')
                    values = self._catalog.read_index(source.resolved.core, reference, snapshot=snapshot,
                        projection=tuple(sorted(needed_fields)))
                    if _start_owners is not None and source.failure:
                        raise RegistryReadSessionError(source.failure)
                    if context is not None and _requests_start(row['object_type'], needed_fields):
                        from ._product_origin_includes import _verify_start_projection
                        _verify_start_projection(context, reference.to_dict()['ref'], values, needed_fields)
                    if context is not None:
                        context.reserve(size=context.bytes_bound(values) + 512
                            + sum(context.bytes_bound(projection) for _clause, projection in selected))
                    projected, requested = {}, set()
                    for clause, projection in selected:
                        guard = (context.scratch(size=context.bytes_bound(values)
                            + sum(context.bytes_bound(predicate.value) for predicate in clause.predicates))
                            if context is not None else nullcontext())
                        with guard:
                            matched = self._matches(values, clause.predicates)
                        if matched:
                            requested.update(projection)
                            projected.update({k: values[k] for k in projection if k in values})
                    if not requested:
                        continue
                    key = (source_id, row['object_type'], row['logical_id'], row['version_id'])
                    if context is not None:
                        context.reserve(size=2048 + context.bytes_bound(projected)
                            + sum(context.bytes_bound(field) for field in requested)
                            + sum(context.bytes_bound(row[key]) for key in
                                ('object_type', 'logical_id', 'version_id', 'schema_ref')))
                    entry = {'entry_ref': reference.to_dict(), 'entry_type': row['object_type'],
                        'schema_ref': row['schema_ref'], 'fields': projected,
                        'disclosure': {'projected_fields': sorted(requested), 'unprovided_fields': sorted(requested - set(projected))},
                        'source_cut': cut.to_dict(), 'record_access': 'not_checked',
                        'material_access': 'not_checked' if row['object_type'] == 'resource_version/v1' else 'not_applicable'}
                    guard = context.scratch(entry) if context is not None else nullcontext()
                    with guard:
                        size = len(canonical_json(entry))
                    entry_bytes += size
                    entry_bytes_by_source[source_id] = entry_bytes_by_source.get(source_id, 0) + size
                    if retained_bytes + entry_bytes > self.limits.max_scan_bytes:
                        raise RegistryReadSessionError('LIMIT_EXCEEDED')
                    entries[key] = entry
            except RegistryReadSessionError as exc:
                if exc.code in {'CURSOR_MISMATCH', 'INVALID_CUT', 'LIMIT_EXCEEDED', 'INVALID_PROJECTION'}:
                    raise
                code = exc.code
                budget = context if context is not None else _start_owners
                if budget is not None:
                    from ._product_origin_core import _SAFE_ERROR_CODES
                    code = code if code in _SAFE_ERROR_CODES else 'INTEGRITY_FAILED'
                    budget.reserve(size=512 + 128 * len(entries))
                    canonical_json(RegistryReadSessionError(code).to_dict())
                failures[source_id] = code
                entry_bytes_by_source.pop(source_id, None)
                entries = {key: value for key, value in entries.items() if key[0] != source_id}
            except Exception as exc:
                if context is not None and getattr(exc, 'code', None) == 'LIMIT_EXCEEDED':
                    raise RegistryReadSessionError('LIMIT_EXCEEDED') from None
                code = getattr(exc, 'code', 'READ_FAILED')
                budget = context if context is not None else _start_owners
                if budget is not None:
                    from ._product_origin_core import _SAFE_ERROR_CODES
                    code = code if code in _SAFE_ERROR_CODES else 'INTEGRITY_FAILED'
                    budget.reserve(size=512 + 128 * len(entries))
                    canonical_json(RegistryReadSessionError(code).to_dict())
                failures[source_id] = code
                entry_bytes_by_source.pop(source_id, None)
                entries = {key: value for key, value in entries.items() if key[0] != source_id}
        if _start_owners is not None:
            context = _start_owners.current or _start_owners
        if context is not None:
            context.reserve(size=512 + 128 * len(entries)
                + sum(context.bytes_bound(key) for key in entries))
        return _Query(fingerprint, tuple(entries[key] for key in sorted(entries)), cuts, spec.source_ids,
            failures, entry_bytes_by_source)

    def _query_requests_start(self, spec):
        if type(spec) is not IndexQuery:
            return False
        return any(_requests_start(clause.entry_type, clause.projection)
            or _requests_start(clause.entry_type, (predicate.field for predicate in clause.predicates))
            for clause in spec.clauses)

    def query_product_origin_v1(self, root, at_cut, include=None, page_size=None, cursor=None):
        """Read one fixed, fully verified product-origin closure at an exact cut."""
        from ._product_origin_query import query_product_origin_v1
        return query_product_origin_v1(self, root, at_cut, include, page_size, cursor)

    def query_index(self, spec, *, cursor=None):
        # Reject the other pool domain before the ordinary Start dispatcher
        # creates owners or interprets index-specific fields.
        if cursor is not None:
            self._alive()
            if (type(cursor) is str and cursor in self._cursors
                    and self._cursors[cursor][0].kind != 'index'):
                raise RegistryReadSessionError('CURSOR_MISMATCH')
        if not self._query_requests_start(spec):
            return self._query_index(spec, cursor=cursor)
        from ._product_origin_core import _clear_exception_frames
        try:
            return self._query_start_index(spec, cursor=cursor)
        except Exception as exc:
            code = getattr(exc, 'code', 'INTEGRITY_FAILED')
            _clear_exception_frames(exc)
        raise RegistryReadSessionError(code) from None

    def _query_start_index(self, spec, *, cursor=None):
        owners = _StartReadOwners(self)
        try:
            try:
                result = self._query_index(spec, cursor=cursor, _start_owners=owners)
                # Include failed-source responses in protected final checks,
                # preserving ordinary IndexQuery unavailable-source semantics.
                while True:
                    failures = {state['source_id']: state['access_state'] for state in result['source_results']
                        if state['access_state'] != 'readable'}
                    checked = dict(failures)
                    owners.check(tuple(owners.source_ids), checked)
                    if checked == failures:
                        return result
                    budget = owners.current or owners
                    budget.reserve(size=3 * budget.bytes_bound(result))
                    for state in result['source_results']:
                        if state['source_id'] in checked:
                            state['access_state'] = checked[state['source_id']]
                    result = json.loads(canonical_json(result))
            except Exception as exc:
                for key in owners.created_cursors:
                    self._cursors.pop(key, None)
                if not owners.error_reserved and not owners.contexts:
                    raise
                owners.fail(exc, tuple(owners.source_ids))
        finally:
            owners.close()

    def _query_index(self, spec, *, cursor=None, _start_owners=None):
        if cursor is not None:
            self._alive()
            self._validate_query(spec)
            fingerprint = document_digest(spec.to_dict())
            if type(cursor) is not str or cursor not in self._cursors:
                raise RegistryReadSessionError('CURSOR_MISMATCH')
            query, offset = self._cursors[cursor]
            if query.kind != 'index':
                raise RegistryReadSessionError('CURSOR_MISMATCH')
            if query.fingerprint != fingerprint:
                raise RegistryReadSessionError('CURSOR_MISMATCH')
            if _start_owners is not None:
                # A cached page has no relation work to repeat, but its copies,
                # serialization and final handoff still need bounded ownership.
                for source_id, cut in query.cuts.items():
                    if source_id not in query.failures and cut.cut_id in self._cuts:
                        _start_owners.begin(self._cuts[cut.cut_id][1], spec)
        else:
            query = self._collect_index(spec, _start_owners=_start_owners)
            fingerprint = query.fingerprint
            offset = 0
        context = (_start_owners.current or _start_owners) if _start_owners is not None else None
        if context is not None:
            context.reserve(size=4096 + 3 * context.bytes_bound(query.entries)
                + 2 * context.bytes_bound(query.failures) + 2 * context.bytes_bound(query.source_ids)
                + sum(2 * context.bytes_bound(cut.to_dict()) for cut in query.cuts.values()))
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
                           {k: v for k, v in query.cuts.items() if k not in failures}, query.source_ids, failures,
                           {k: v for k, v in query.entry_bytes_by_source.items() if k not in failures})
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
            if _start_owners is not None and continuation not in self._cursors:
                if context is not None:
                    context.reserve(size=256 + context.bytes_bound(continuation))
                _start_owners.created_cursors.append(continuation)
            self._cursors[continuation] = (_Query(query.fingerprint, query.entries, query.cuts, query.source_ids,
                failures, query.entry_bytes_by_source), position)
        if context is not None:
            context.reserve(size=8192 + 2 * context.bytes_bound(page)
                + 2048 * len(query.source_ids) + 2 * context.bytes_bound(continuation))
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
        if context is not None:
            context.reserve(size=3 * context.bytes_bound(result))
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


def query_product_origin_v1(session, root, at_cut, include=None, page_size=None, cursor=None):
    return session.query_product_origin_v1(root, at_cut, include, page_size, cursor)


def read_exact(session, **kwargs):
    return session.read_exact(**kwargs)


def read_material(session, **kwargs):
    return session.read_material(**kwargs)
