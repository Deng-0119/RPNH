"""Bounded read-only browsing over a HOST-established Registry read session.

Folders are source/type selections, never filesystem paths. Opaque handles only
retain exact session-issued references and cuts; they never convey permission.
"""
from __future__ import annotations

from copy import deepcopy
import json
import secrets
from threading import RLock
from urllib.parse import parse_qs

SCHEMA = 'rpnh/registry_browser/v1'
MAX_PAGES = 128
MAX_ITEMS = 3200
MAX_RETAINED_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_BYTES = 256 * 1024


class RegistryBrowserError(ValueError):
    def __init__(self, code='invalid_query'):
        self.code = code if code in {
            'invalid_query', 'access_changed', 'not_disclosed',
            'stale_observation', 'scope_limit', 'invalid_utf8',
            'unsupported', 'read_failed', 'governed_delivery_required',
            'material_disabled', 'material_availability_unknown',
        } else 'read_failed'
        super().__init__(self.code)


def parse_query(query):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise RegistryBrowserError() from exc
    if any(len(v) != 1 or not v[0] or len(v[0]) > 256
           or any(ord(c) < 32 or ord(c) == 127 for c in v[0]) for v in values.values()):
        raise RegistryBrowserError()
    params = {key: value[0] for key, value in values.items()}
    action = params.get('action')
    required = {'catalog': {'action'}, 'list': {'action', 'source_id', 'entry_type'},
                'record': {'action', 'handle'}, 'material': {'action', 'handle'}, 'versions': {'action', 'handle'},
                'positions': {'action', 'source_id'}, 'position_history': {'action', 'handle'}}.get(action)
    optional = {'cursor'} if action in {'list', 'versions', 'positions', 'position_history'} else set()
    if required is None or not required <= set(params) or set(params) - required - optional:
        raise RegistryBrowserError()
    return params


def session_error(error):
    code = getattr(error, 'code', '')
    if code in {'ACCESS_CHANGED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'NOT_SELECTED', 'NOT_AUTHORIZED'}:
        return RegistryBrowserError('access_changed')
    if code in {'GOVERNED_DELIVERY_REQUIRED', 'MATERIAL_DISABLED', 'MATERIAL_AVAILABILITY_UNKNOWN'}:
        return RegistryBrowserError(code.lower())
    if code in {'NOT_DISCLOSED', 'PAYLOAD_NOT_DISCLOSED', 'MATERIAL_ACCESS_NOT_GRANTED'}:
        return RegistryBrowserError('not_disclosed')
    if code in {'CURSOR_MISMATCH', 'STALE_OBSERVATION', 'CUT_UNAVAILABLE', 'NOT_PRESENT_AT_CUT'}:
        return RegistryBrowserError('stale_observation')
    if code == 'INVALID_UTF8':
        return RegistryBrowserError('invalid_utf8')
    if 'LIMIT' in code or code == 'MATERIAL_TOO_LARGE':
        return RegistryBrowserError('scope_limit')
    if code in {'UNSUPPORTED_READER_VERSION', 'UNSUPPORTED_ENTRY_TYPE', 'UNSUPPORTED_PROJECTION'}:
        return RegistryBrowserError('unsupported')
    return RegistryBrowserError('read_failed')


def _reference(value):
    # Only server-retained index rows reach this decoder. All exact identities
    # and permissions are still checked by the original session read boundary.
    from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    ref = value['ref']
    if value['schema_version'] == 'rpnh/collaboration/source_resource_ref/v1':
        return SourceQualifiedResourceRef(value['source_id'], ResourceVersionRef(
            TypedId.parse(ref['resource_id'], expected='resource'),
            TypedId.parse(ref['resource_version_id'], expected='resource_version')))
    return SourceQualifiedVersionRef(value['source_id'], VersionRef(ref['entity_type'],
        TypedId.parse(ref['logical_id']), TypedId.parse(ref['version_id'])))


class RegistryBrowserProvider:
    def __init__(self, session, *, lock=None):
        self.session = session
        self._lock = lock if lock is not None else RLock()
        self._pages = {}
        self._items = {}
        self._retained_bytes = 0

    def _clear(self):
        self._pages.clear()
        self._items.clear()
        self._retained_bytes = 0

    def registry_browser(self, params):
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        with self._lock:
            try:
                description = self.session.describe()
                action = params['action']
                if action == 'catalog':
                    payload = self._catalog(description)
                elif action == 'list':
                    payload = self._list(description, params)
                elif action in {'record', 'material'}:
                    payload = self._read(description, params)
                elif action in {'positions', 'position_history'}:
                    # A shared index/record grant does not disclose address
                    # chains. This provider has no original position contract.
                    raise RegistryBrowserError('unsupported')
                else:
                    raise RegistryBrowserError()
                # Session-owned exact refs retain the old structure cut.  A
                # cached Viewer page cannot retain old availability or text.
                payload = self._project_current(payload)
                # Serialization and cached-page replay stay within this final
                # authorization interval, including closed/expired sessions.
                size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                if size > self.session.limits.max_response_bytes:
                    raise RegistryBrowserError('scope_limit')
                self.session.final_recheck()
                return deepcopy(payload)
            except RegistryReadSessionError as exc:
                self._clear()
                raise session_error(exc) from exc
            except Exception:
                self._clear()
                raise

    def _catalog(self, description):
        return {'schema_version': SCHEMA, 'action': 'catalog', 'sources': [
            {'source_id': source['source_id'], 'access_state': source['access_state'],
             **({'body_read_mode': source['body_read_mode']} if source.get('body_read_mode') else {}),
             'entry_types': sorted(source.get('capabilities', {}).get('index_fields', {}))}
            for source in description['sources']]}

    def _project_current(self, payload):
        payload = deepcopy(payload)

        def project(value):
            handle = value.get('handle')
            if handle is None:
                # A record response has its exact identity but no handle.
                item = next((item for item in self._items.values()
                    if item['ref'].to_dict() == value.get('entry_ref')), None)
            else:
                item = self._items.get(handle)
            if item is None:
                return
            fields, current = self.session.project_current_material(
                entry_ref=item['ref'], at_cut=item['cut'], fields=value.get('fields', {}))
            if current is not None:
                if 'fields' in value:
                    value['fields'] = fields
                value.update(current, material_may_be_available=False)

        project(payload)
        for entry in payload.get('entries', ()):
            project(entry)
        return payload

    @staticmethod
    def _source(description, source_id):
        source = next((s for s in description['sources'] if s['source_id'] == source_id), None)
        if source is None or source.get('access_state') != 'readable':
            raise RegistryBrowserError('not_disclosed')
        return source

    def _new_page(self, source_id, entry_type, spec, cut, cursor):
        if len(self._pages) >= min(MAX_PAGES, self.session.limits.max_cursors):
            raise RegistryBrowserError('scope_limit')
        token = secrets.token_urlsafe(24)
        self._pages[token] = {'source_id': source_id, 'entry_type': entry_type,
            'spec': spec, 'cut': cut, 'cursor': cursor, 'response': None}
        return token

    def _list(self, description, params):
        from cpn.rpnh.collaboration.registry_read_contracts import IndexQuery, TypedIndexClause
        source_id, entry_type = params['source_id'], params['entry_type']
        source = self._source(description, source_id)
        fields = source.get('capabilities', {}).get('index_fields', {}).get(entry_type)
        if not fields:
            raise RegistryBrowserError('not_disclosed')
        token = params.get('cursor')
        if token is None:
            cut = self.session.capture_cut(source_id)
            projection = tuple(fields[:self.session.limits.max_projection_fields])
            spec = IndexQuery((source_id,), (TypedIndexClause(entry_type, projection=projection),),
                page_size=min(25, self.session.limits.max_page_size), cuts={source_id: cut})
            token = self._new_page(source_id, entry_type, spec, cut, None)
        state = self._pages.get(token)
        if state is None or (source_id, entry_type) != (state['source_id'], state['entry_type']):
            raise RegistryBrowserError('stale_observation')
        self.session.validate_cut(state['cut'])
        if state['response'] is not None:
            return state['response']
        page = self.session.query_index(state['spec'], cursor=state['cursor'])
        # An unavailable source must never be presented as an empty directory.
        if any(s['access_state'] != 'readable' for s in page['source_results']):
            raise RegistryBrowserError('read_failed')
        entries = []
        for row in page['entries']:
            if len(self._items) >= MAX_ITEMS:
                raise RegistryBrowserError('scope_limit')
            handle = secrets.token_urlsafe(24)
            self._items[handle] = {'ref': _reference(row['entry_ref']),
                'entry_type': entry_type, 'cut': state['cut']}
            entries.append({'entry_ref': row['entry_ref'], 'fields': row['fields'], 'handle': handle,
                'material_may_be_available': entry_type == 'resource_version/v1' and bool(source['capabilities'].get('material'))})
        next_cursor = None
        if page['continuation'] is not None:
            next_cursor = self._new_page(source_id, entry_type, state['spec'], state['cut'], page['continuation'])
        result = {'schema_version': SCHEMA, 'action': 'list', 'source_id': source_id,
            'entry_type': entry_type, 'entries': entries, 'source_cut': state['cut'].to_dict(),
            'coverage': page['coverage'], 'page_cursor': token, 'next_cursor': next_cursor}
        size = len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        if self._retained_bytes + size > MAX_RETAINED_BYTES:
            raise RegistryBrowserError('scope_limit')
        self._retained_bytes += size
        state['response'] = deepcopy(result)
        return result

    def _read(self, description, params):
        item = self._items.get(params['handle'])
        if item is None:
            raise RegistryBrowserError('stale_observation')
        ref, cut, entry_type = item['ref'], item['cut'], item['entry_type']
        source = self._source(description, ref.source_id)
        self.session.validate_cut(cut)
        result = {'schema_version': SCHEMA, 'action': params['action'],
            'entry_ref': ref.to_dict(), 'source_cut': cut.to_dict()}
        if params['action'] == 'record':
            fields = source.get('capabilities', {}).get('record_fields', {}).get(entry_type)
            if not fields:
                raise RegistryBrowserError('not_disclosed')
            record = self.session.read_exact(entry_ref=ref, at_cut=cut,
                projection=tuple(fields[:self.session.limits.max_projection_fields]))
            result.update(fields=record['record'], disclosure=record['disclosure'],
                material_may_be_available=entry_type == 'resource_version/v1' and bool(source['capabilities'].get('material')))
        else:
            from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef
            if type(ref) is not SourceQualifiedResourceRef:
                raise RegistryBrowserError('unsupported')
            material = self.session.read_material(resource_ref=ref, at_cut=cut,
                representation='utf8', max_bytes=MAX_PREVIEW_BYTES, include_digest=False)
            result.update(body=material['body'], media_type=material['media_type'],
                byte_count=material['byte_count'])
        return result
