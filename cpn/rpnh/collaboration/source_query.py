"""Read-only composition of existing, independently authorized resource queries.

A source resolver is a trusted HOST input: manifest paths and aliases cannot
open a path, create a grant, start a task or select a broader principal. Drafts
are immutable values. Recording a draft is a separate, explicit owner command.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import json
import hashlib
import sqlite3
from typing import Callable

from ..registry._registry import _RegistryCore
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.invocations import InvocationContext
from ..registry.resource_service import _ResourceServiceKernel
from ..registry.resources import RegistryObserverContext, ResourceVersionRef, ResourceQuery, QueryCursor, RegistryHead
from ..registry.errors import (ResourceServiceError, UnauthorizedResourceDelivery, StaleQueryContext,
                               StaleInvocationContext, StaleWriterFence, UnknownResourceVersion)
from ..registry.event_store import RegistryConflict
from ..registry.publication import _version_from_payload
from ..registry.schema_catalog import canonical_json
from .sources import get_local_source_identity
from .references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from .source_sets import read_source_set, current_source_set

OBSERVATION_SCHEMA = 'rpnh/source_observation/v1'


class SourceUnavailable(RuntimeError):
    """A registered source path has no currently attached reader."""


class SourceNotEstablished(SourceUnavailable):
    """No reader was ever supplied for this expected source/path."""


class SourceAccessChanged(RuntimeError):
    """The exact source/path/current authority no longer matches the capture."""


def wire(value):
    if isinstance(value, TypedId):
        return str(value)
    if isinstance(value, VersionRef):
        return {'entity_type': value.entity_type, 'logical_id': str(value.entity_id), 'version_id': str(value.version_id)}
    if is_dataclass(value):
        return {field.name: wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def _head(value):
    if value is None:
        return None
    if set(value) != {'ordinal', 'writer_fencing_epoch', 'task_control_sequence', 'stream_heads'}:
        raise ValueError('invalid source cut')
    if any(type(value[k]) is not int or value[k] < 0 for k in ('ordinal', 'writer_fencing_epoch', 'task_control_sequence')):
        raise ValueError('invalid source cut position')
    return RegistryHead(**value)


def _qualified(value, source):
    """Use existing ref contracts at every cross-source reference boundary."""
    if isinstance(value, dict):
        if set(value) == {'entity_type', 'logical_id', 'version_id'}:
            return SourceQualifiedVersionRef(source, _version_from_payload(value)).to_dict()
        if set(value) == {'resource_id', 'resource_version_id'}:
            return SourceQualifiedResourceRef(source, ResourceVersionRef(
                TypedId.parse(value['resource_id'], expected='resource'),
                TypedId.parse(value['resource_version_id'], expected='resource_version'))).to_dict()
        return {key: _qualified(item, source) for key, item in value.items()}
    if isinstance(value, list):
        return [_qualified(item, source) for item in value]
    return value


def _unqualified(value, source):
    if isinstance(value, dict):
        if value.get('schema_version') in ('rpnh/collaboration/source_version_ref/v1',
                                           'rpnh/collaboration/source_resource_ref/v1'):
            if set(value) != {'schema_version', 'source_id', 'ref'} or value['source_id'] != source:
                raise ValueError('reference crosses its enclosing exact source')
            return dict(value['ref'])
        return {key: _unqualified(item, source) for key, item in value.items()}
    if isinstance(value, list):
        return [_unqualified(item, source) for item in value]
    return value


def _cursor(value, source):
    if value is None:
        return None
    value = _unqualified(value, source)
    if set(value) != {'query_facts', 'principal_ref', 'grant_ref', 'through_head', 'offset', 'authority_facts'}:
        raise ValueError('invalid source cursor')
    authority = dict(value['authority_facts'])
    if 'grant_states' in authority:
        authority['grant_states'] = [tuple(item) for item in authority['grant_states']]
    return QueryCursor(value['query_facts'], _version_from_payload(value['principal_ref']),
                       _version_from_payload(value['grant_ref']), _head(value['through_head']),
                       value['offset'], authority)


def _disclosure_label(authority):
    # Display-only label. The full raw qualified authority remains in the record.
    return hashlib.sha256(canonical_json(authority)).hexdigest()


@dataclass(frozen=True, slots=True)
class RegistrySourceReader:
    core: _RegistryCore
    context: InvocationContext | RegistryObserverContext

    def __post_init__(self):
        if not isinstance(self.core, _RegistryCore) or not self.core.read_only:
            raise TypeError('SourceSet query requires an existing read-only Registry')
        if not isinstance(self.context, (InvocationContext, RegistryObserverContext)):
            raise TypeError('SourceSet requires an existing ResourceService authority context')

    def validate(self, member):
        identity = get_local_source_identity(self.core)
        if (identity is None or identity.source_id != member.source_ref.source_id
                or identity.task_ref != member.source_ref.ref or self.context.task_ref != identity.task_ref):
            raise SourceAccessChanged('resolved source is not the exact declared identity/task')
        service = _ResourceServiceKernel(self.core)
        service._query_authority(self.context, observer_fields=('headers', 'projection_head'))
        before = service.head(self.context)
        authority = _qualified(wire(service._authority_facts(self.context)), member.source_ref.source_id)
        after = service.head(self.context)
        if before != after:
            raise SourceAccessChanged('source changed while capturing qualification')
        return service, authority, wire(after)


@dataclass(frozen=True, slots=True)
class SourceObservationDraft:
    """Copied canonical bytes prevent a caller mutating a completed query draft."""
    canonical_bytes: bytes

    def __post_init__(self):
        if type(self.canonical_bytes) is not bytes:
            raise TypeError('draft requires immutable bytes')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate draft JSON key')
                result[key] = value
            return result
        def nonfinite(value):
            raise ValueError('nonfinite draft JSON')
        document = json.loads(self.canonical_bytes, object_pairs_hook=unique, parse_constant=nonfinite)
        if type(document) is not dict or canonical_json(document) != self.canonical_bytes:
            raise ValueError('draft must contain one canonical JSON object')

    def to_dict(self):
        return json.loads(self.canonical_bytes)


def _coverage(rows):
    complete = all(row['coverage']['state'] == 'complete' for row in rows)
    return {'state': 'complete' if complete else 'partial',
            'loaded_count': sum(len(row['headers']) for row in rows),
            'total_count': sum(len(row['headers']) for row in rows) if complete else None,
            'expected_source_count': len(rows),
            'missing_sources': [row['source_id'] for row in rows if row['coverage']['state'] != 'complete']}


def _missing(row, state):
    # Current access loss cannot retain previously visible content or counts.
    return {**row, 'availability': state, 'headers': [], 'cursor': None,
            'coverage': {'state': state, 'loaded_count': None, 'total_count': None}}


class SourceSetQuery:
    def __init__(self, manifest_core, resolver: Callable[[str, str], RegistrySourceReader], *, capture_context=None):
        if not isinstance(manifest_core, _RegistryCore) or not manifest_core.read_only or not callable(resolver):
            raise TypeError('SourceSetQuery needs a read-only manifest Registry and an explicit resolver')
        if capture_context is not None and (not isinstance(capture_context, InvocationContext)
                or capture_context.task_ref.entity_id != manifest_core.task_id):
            raise TypeError('capture context must be the exact local query-owner Invocation')
        self.core, self.resolve, self.capture_context = manifest_core, resolver, capture_context

    def _resolve(self, member, path):
        if path not in member.access_paths:
            raise SourceAccessChanged('access path is outside the fixed manifest')
        reader = self.resolve(member.source_ref.source_id, path)
        if reader is None:
            raise SourceNotEstablished('source reader was not established')
        if type(reader) is not RegistrySourceReader:
            raise TypeError('source resolver returned an unsupported reader')
        service, authority, authority_head = reader.validate(member)
        return reader, service, authority, authority_head

    def query(self, source_set_ref, *, paths, limit=100, media_types=(), through=None, previous=None):
        """Read one page per selected source; never writes any Registry.

        A continuation references a persisted Observation. Completed sources are
        reauthorized without rescanning. Missing sources retain their original
        gap until the caller deliberately starts a new observation/supplement.
        """
        from .source_observations import _read_source_observation
        manifest = read_source_set(self.core, source_set_ref)
        if (type(limit) is not int or not 1 <= limit <= 1000
                or type(paths) is not dict or set(paths) != {m.source_ref.source_id for m in manifest.members}
                or any(paths[m.source_ref.source_id] not in m.access_paths for m in manifest.members)
                or not isinstance(media_types, (tuple, list)) or any(type(x) is not str for x in media_types)):
            raise ValueError('query requires exact selected paths and ResourceService limits')
        query = {'limit': limit, 'media_types': list(media_types)}
        older = None
        if previous is not None:
            prior = _read_source_observation(self.core, previous, capture_context=self.capture_context)
            if (self.capture_context is None or prior['producer_invocation_ref']['ref'] != wire(self.capture_context.invocation_ref)):
                raise SourceAccessChanged('only the original active capture Invocation can continue its pages')
            _ResourceServiceKernel(self.core)._revalidate_invocation(self.capture_context, boundary='source-set-page')
            older = prior['observation']
        if older is not None and (older['source_set_ref'] != source_set_ref.to_dict() or older['query'] != query
                or older['kind'] != 'query' or {r['source_id']: r['access_path'] for r in older['sources']} != paths
                or through is not None):
            raise ValueError('continuation cannot change manifest, paths, query or cut')
        if through is not None and (type(through) is not dict or set(through) - set(paths)):
            raise ValueError('cuts require exact source identities')
        previous_rows = {r['source_id']: r for r in older['sources']} if older else {}
        rows, checks = [], []
        for member in manifest.members:
            source, path = member.source_ref.source_id, paths[member.source_ref.source_id]
            old = previous_rows.get(source)
            row = {'source_id': source, 'source_ref': member.source_ref.to_dict(), 'access_path': path,
                   'aliases': list(member.aliases), 'availability': 'not_established', 'cut': None,
                   'authority': None, 'authority_head': None, 'headers': [], 'cursor': None,
                   'coverage': {'state': 'not_loaded', 'loaded_count': None, 'total_count': None}}
            if old is not None:
                row = json.loads(json.dumps(old))
                if old['coverage']['state'] not in ('complete', 'partial'):
                    rows.append(row)
                    continue
            try:
                reader, service, authority, authority_head = self._resolve(member, path)
                if old is not None and authority != old['authority']:
                    raise SourceAccessChanged('source authority changed after capture')
                # Revalidate every retained resource with the original authority.
                if old:
                    self._check_headers(reader, service, old['headers'], old['cut'], source)
                if old is None or old['cursor'] is not None:
                    requested_head = (_head(old['cut']) if old else
                        service.head(reader.context, ordinal=through[source]) if through and source in through else None)
                    page = service.query(reader.context, ResourceQuery(
                        reader.context.task_ref, (), media_types=tuple(media_types), through_head=requested_head,
                        cursor=_cursor(old['cursor'], source) if old else None, limit=limit))
                    row.update(availability='readable', cut=wire(page.through_head), authority=authority,
                               authority_head=old['authority_head'] if old else authority_head,
                               headers=(old['headers'] if old else []) + [_qualified(wire(h), source) for h in page.headers], cursor=_qualified(wire(page.cursor), source))
                    refs = [h['ref']['ref']['resource_version_id'] for h in row['headers']]
                    if len(refs) != len(set(refs)):
                        raise ValueError('source continuation repeats a resource version')
                    row['coverage'] = {'state': 'partial' if page.cursor else 'complete', 'loaded_count': len(refs),
                                       'total_count': None if page.cursor else len(refs)}
                checks.append((len(rows), member, reader, service, authority))
            except UnauthorizedResourceDelivery:
                row = _missing(row, 'denied')
            except (SourceAccessChanged, StaleInvocationContext, StaleQueryContext, StaleWriterFence):
                row = _missing(row, 'access_changed')
            except SourceNotEstablished:
                row = _missing(row, 'not_established')
            except SourceUnavailable:
                row = _missing(row, 'unavailable')
            except (OSError, sqlite3.Error, ResourceServiceError, RegistryConflict):
                row = _missing(row, 'read_failed')
            rows.append(row)
        for index, member, reader, service, authority in checks:
            try:
                if reader.validate(member)[1] != authority:
                    raise SourceAccessChanged('source authority moved during observation')
                self._check_headers(reader, service, rows[index]['headers'], rows[index]['cut'], member.source_ref.source_id)
            except (SourceAccessChanged, ResourceServiceError, RegistryConflict):
                rows[index] = _missing(rows[index], 'access_changed')
        document = {'schema_version': OBSERVATION_SCHEMA, 'kind': 'query', 'source_set_ref': source_set_ref.to_dict(),
                    'previous_observation_ref': previous.to_dict() if previous else None, 'supplement_of': None,
                    'dependencies': [], 'query': query, 'sources': rows, 'coverage': _coverage(rows),
                    'causal_completeness': 'not_established', 'global_atomic_snapshot': False}
        return SourceObservationDraft(canonical_json(document))

    @staticmethod
    def _check_headers(reader, service, headers, cut, source):
        for header in headers:
            plain = _unqualified(header, source)
            ref = plain['ref']
            exact = ResourceVersionRef(TypedId.parse(ref['resource_id'], expected='resource'),
                                       TypedId.parse(ref['resource_version_id'], expected='resource_version'))
            service._query_authority(reader.context, exact if isinstance(reader.context, InvocationContext) else None,
                                     observer_fields=('headers', 'projection_head'))
            actual = _qualified(wire(service._header(exact, through_head=_head(cut))), source)
            if actual != header:
                raise ValueError('saved header differs from its exact canonical source projection')

    def supplement(self, observation_ref, *, dependency: SourceQualifiedResourceRef):
        """Read one exact missing dependency at its own later cut, independently."""
        from .source_observations import _read_source_observation
        original = _read_source_observation(self.core, observation_ref, capture_context=self.capture_context)['observation']
        manifest_ref = SourceQualifiedVersionRef.from_dict(original['source_set_ref'], catalog=self.core.catalog)
        manifest = read_source_set(self.core, manifest_ref)
        matches = [m for m in manifest.members if m.source_ref.source_id == dependency.source_id]
        if len(matches) != 1:
            raise ValueError('supplement dependency is outside the original expected sources')
        member = matches[0]
        old = next(r for r in original['sources'] if r['source_id'] == dependency.source_id)
        row = {**old, 'headers': [], 'cursor': None, 'cut': None, 'authority': None, 'authority_head': None}
        try:
            reader, service, authority, authority_head = self._resolve(member, old['access_path'])
            cut = service.head(reader.context)
            header = service.get_header(reader.context, dependency.ref)
            if service.head(reader.context) != cut or reader.validate(member)[1] != authority:
                raise SourceAccessChanged('supplement authority changed')
            row.update(headers=[_qualified(wire(header), dependency.source_id)], availability='readable', cut=wire(cut), authority=authority, authority_head=authority_head,
                       coverage={'state': 'complete', 'loaded_count': 1, 'total_count': 1})
        except UnauthorizedResourceDelivery:
            row = _missing(row, 'denied')
        except UnknownResourceVersion:
            row = _missing(row, 'missing_dependency')
        except (SourceAccessChanged, StaleInvocationContext, StaleQueryContext, StaleWriterFence):
            row = _missing(row, 'access_changed')
        except SourceNotEstablished:
            row = _missing(row, 'not_established')
        except SourceUnavailable:
            row = _missing(row, 'unavailable')
        except (OSError, sqlite3.Error, ResourceServiceError, RegistryConflict):
            row = _missing(row, 'read_failed')
        return SourceObservationDraft(canonical_json({
            'schema_version': OBSERVATION_SCHEMA, 'kind': 'supplement', 'source_set_ref': original['source_set_ref'],
            'previous_observation_ref': None, 'supplement_of': observation_ref.to_dict(),
            'dependencies': [dependency.to_dict()], 'query': None, 'sources': [row], 'coverage': _coverage([row]),
            'causal_completeness': 'not_established', 'global_atomic_snapshot': False}))

    def validate_draft(self, draft, *, historical=False):
        """Revalidate source content and finite page/completion claims, never trust DTOs."""
        from .source_observations import validate_observation
        document = draft.to_dict()
        manifest_ref = SourceQualifiedVersionRef.from_dict(document['source_set_ref'], catalog=self.core.catalog)
        manifest = read_source_set(self.core, manifest_ref)
        validate_observation(document, manifest, self.core.catalog)
        members = {m.source_ref.source_id: m for m in manifest.members}
        for row in document['sources']:
            if row['availability'] == 'readable':
                self._validate_saved_source(members[row['source_id']], row, document, historical=historical)
        return document

    def _validate_saved_source(self, member, row, document, *, historical):
        reader, service, current, current_authority_head = self._resolve(member, row['access_path'])
        service._validate_query_head(_head(row['cut']))
        service._validate_query_head(_head(row['authority_head']))
        if not historical and current != row['authority']:
            raise SourceAccessChanged('original capture qualification changed')
        original_context = self._verify_capture_context(reader, service, row) if historical else reader.context
        self._verify_qualification_evidence(reader, service, row, original_context)
        self._check_headers(reader, service, row['headers'], row['cut'], row['source_id'])
        if document['kind'] == 'query':
            query = document['query']
            expected_cursor = _cursor(row['cursor'], row['source_id'])
            principal = (original_context.principal_ref if isinstance(original_context, InvocationContext)
                         else original_context.observer_principal_ref)
            grant = (original_context.operation_binding_ref if isinstance(original_context, InvocationContext)
                     else original_context.grant_ref)
            if expected_cursor is not None and (expected_cursor.principal_ref != principal or expected_cursor.grant_ref != grant):
                raise ValueError('saved cursor principal/grant differs from original capture context')
            initial = ResourceQuery(reader.context.task_ref, (), media_types=tuple(query['media_types']),
                                    through_head=_head(row['cut']), limit=query['limit'])
            if expected_cursor is not None and (expected_cursor.query_facts != service._query_material(initial)
                    or expected_cursor.offset != len(row['headers']) or expected_cursor.through_head != initial.through_head
                    or _qualified(wire(expected_cursor.authority_facts), row['source_id']) != row['authority']):
                raise ValueError('saved cursor differs from its query/cut/offset/capture authority')
            # This is an independent validation query. A new reader never uses
            # an old cursor with rewritten authority facts.
            headers, cursor = [], None
            pages = max(1, (len(row['headers']) + query['limit'] - 1) // query['limit'])
            for index in range(pages):
                result = service.query(reader.context, ResourceQuery(reader.context.task_ref, (),
                    media_types=tuple(query['media_types']), through_head=initial.through_head, cursor=cursor, limit=query['limit']))
                headers.extend(_qualified(wire(h), row['source_id']) for h in result.headers)
                cursor = result.cursor
                if cursor is None and index + 1 < pages:
                    raise ValueError('saved page count exceeds the exact source query')
            if (headers != row['headers'] or (cursor is None) != (row['cursor'] is None)
                    or not historical and _qualified(wire(cursor), row['source_id']) != row['cursor']):
                raise SourceAccessChanged('saved finite pages/completion differ from the source query')
        if reader.validate(member)[1] != current:
            raise SourceAccessChanged('qualification changed during source validation')
        return current

    @staticmethod
    def _verify_capture_context(reader, service, row):
        capture = _unqualified(row['authority'], row['source_id'])
        if 'invocation_ref' in capture:
            from ..registry.invocations import InvocationLifecycle
            ref = _version_from_payload(capture['invocation_ref'])
            visible = reader.core.event_store.object_row_for_view(reader.core.event_store.canonical_view(), ref.version_id)
            if visible is None:
                raise SourceAccessChanged('historical capture qualification has not been canonically published')
            original = InvocationLifecycle(reader.core).hydrate_context(ref, require_current_writer=False)
            if original.task_ref != reader.context.task_ref or wire(original.operation_binding_ref) != capture['binding']:
                raise ValueError('capture qualification differs from its historical registered context')
            return original
        else:
            # No issuer is added by SourceSet. A supplied observer context must
            # retain its exact existing grant/profile authority.
            if _qualified(wire(service._authority_facts(reader.context)), row['source_id']) != row['authority']:
                raise SourceAccessChanged('historical observer context is not supported by this current reader')
            return reader.context

    @staticmethod
    def _verify_qualification_evidence(reader, service, row, original):
        """Qualification uses its own observation head in both write/read gates."""
        capture = _unqualified(row['authority'], row['source_id'])
        if isinstance(original, InvocationContext):
            if capture.get('invocation_ref') != wire(original.invocation_ref) or capture.get('binding') != wire(original.operation_binding_ref):
                raise ValueError('qualification evidence differs from its exact context')
            # The immutable execution lease fixes the original writer epoch.
            lease = service._exact_object(original.operation_execution_lease_ref, expected_type='operation_execution_lease/v1')
            if lease.metadata['writer_fencing_epoch'] != capture['writer']:
                raise ValueError('capture writer differs from its original lease')
            grant_rows = reader.core.event_store.canonical_object_rows(through_ordinal=row['authority_head']['ordinal'], object_type='capability_grant/v1')
            targets = {r['logical_id'] for r in grant_rows if json.loads(r['metadata_json']).get('target_invocation_id') == str(original.invocation_ref.entity_id)}
            states = [(e.aggregate_id, e.event_type, e.stream_sequence)
                      for e in reader.core.event_store.canonical_events(through_ordinal=row['authority_head']['ordinal'])
                      if e.event_type.startswith('capability_') and (e.payload.get('target_invocation_id') == str(original.invocation_ref.entity_id) or e.aggregate_id in targets)]
            if wire(states) != capture['grant_states']:
                raise ValueError('capture capability states differ from their independent qualification observation head')
        elif _qualified(wire(service._authority_facts(original)), row['source_id']) != row['authority']:
            raise SourceAccessChanged('observer qualification changed')

    def view(self, observation_ref):
        """Read canonical capture evidence under independent current permissions."""
        from .source_observations import _read_source_observation, observation_publication
        record = _read_source_observation(self.core, observation_ref)
        observation = record['observation']
        manifest_ref = SourceQualifiedVersionRef.from_dict(observation['source_set_ref'], catalog=self.core.catalog)
        manifest = read_source_set(self.core, manifest_ref)
        members = {m.source_ref.source_id: m for m in manifest.members}
        rows = []
        for saved in observation['sources']:
            row = json.loads(json.dumps(saved))
            captured_coverage = dict(saved['coverage'])
            current_access, disclosure = 'not_checked', None
            if saved['authority'] is not None and saved['availability'] == 'readable':
                try:
                    current = self._validate_saved_source(members[saved['source_id']], saved, observation, historical=True)
                    current_access, disclosure = 'readable', _disclosure_label(current)
                except UnauthorizedResourceDelivery:
                    current_access = 'denied'
                except SourceNotEstablished:
                    current_access = 'not_established'
                except SourceUnavailable:
                    current_access = 'unavailable'
                except (SourceAccessChanged, ResourceServiceError, RegistryConflict, ValueError, OSError, sqlite3.Error):
                    current_access = 'access_changed'
            if current_access != 'readable':
                row = _missing(row, saved['coverage']['state'] if current_access == 'not_checked' else current_access)
                captured_coverage = {'state': saved['coverage']['state'], 'loaded_count': None, 'total_count': None}
            for head_field in ('cut', 'authority_head'):
                if row[head_field] is not None:
                    row[head_field] = {key: row[head_field][key] for key in ('ordinal', 'writer_fencing_epoch')}
            row.pop('authority')
            row.pop('cursor')
            row.update(capture_coverage=captured_coverage, capture_availability=saved['availability'],
                       capture_disclosure=_disclosure_label(saved['authority']) if saved['authority'] is not None else None,
                       current_access=current_access, current_disclosure=disclosure)
            rows.append(row)
        available = current_source_set(self.core, manifest_ref.ref.entity_id)
        return {'schema_version': OBSERVATION_SCHEMA, 'observation_ref': observation_ref.to_dict(),
                'publication': observation_publication(self.core, observation_ref),
                'source_set_ref': observation['source_set_ref'], 'manifest_version': manifest.sequence,
                'available_manifest': available.source_set_ref.to_dict() if available and available.source_set_ref != manifest_ref else None,
                'kind': observation['kind'], 'previous_observation_ref': observation['previous_observation_ref'],
                'supplement_of': observation['supplement_of'], 'dependencies': observation['dependencies'],
                'query_scope': observation['query'], 'sources': rows, 'coverage': _coverage(rows),
                'global_atomic_snapshot': False, 'causal_completeness': 'not_established'}
