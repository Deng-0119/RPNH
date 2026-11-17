"""Local operator inspection of the existing canonical Registry, without grants.

Local task references deliberately do not claim a published source identity.
All handles are server-owned and bounded; a page, record and body share one cut.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import stat
from threading import RLock

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import PreparedObject, VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError
from cpn.rpnh.collaboration.sources import get_local_source_identity
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits
from .registry_browser import (SCHEMA, MAX_PAGES, MAX_ITEMS, MAX_RETAINED_BYTES,
    MAX_PREVIEW_BYTES, RegistryBrowserError)

PAGE_SIZE = 25
MAX_RESPONSE_BYTES = ReadLimits().max_response_bytes
LOCAL_REF_SCHEMA = 'rpnh/registry_browser/local_task_ref/v1'


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _owned(path):
    value = path.lstat()
    if (value.st_uid != os.getuid() or stat.S_ISLNK(value.st_mode)
            or not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode))):
        raise RegistryBrowserError('access_changed')
    return value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode


def _local_catalog(catalog):
    # Ordinary --run catalogs need not opt in to publishing source identities.
    # Load the existing read contract privately, without mutating HOST schemas.
    try:
        catalog.require('collaboration_source_binding/v1', category='object')
        return catalog
    except SchemaGovernanceError:
        from cpn.rpnh.collaboration.schema_catalog import source_identity_schema_data
        documents, definitions, paths = source_identity_schema_data()
        registered = catalog.registered_schemas()
        for name in registered:
            try:
                paths[name] = catalog.schema_path(name)
            except SchemaGovernanceError:
                paths.pop(name, None)
        types = {item.name: item for item in (*definitions, *catalog.definitions())}
        return SchemaCatalog(schemas={**documents, **registered}, types=types.values(), schema_paths=paths)


class LocalRegistryBrowserProvider:
    def __init__(self, run_dir, *, catalog, binding=None):
        if not isinstance(catalog, SchemaCatalog):
            raise TypeError('an explicit SchemaCatalog is required')
        self.run_dir = Path(run_dir).absolute()
        self.catalog, self.binding = _local_catalog(catalog), binding
        self._lock = RLock()
        self._pages, self._items = {}, {}
        self._retained_bytes = 0
        root = self.run_dir / '.registry_v1'
        # Capture locations before opening SQLite. Never resolve away symlinks.
        paths = (self.run_dir, root, root / 'registry.sqlite3', root / 'objects')
        self._paths = {p: _owned(p) for p in paths}
        self._core = _RegistryCore(self.run_dir, create=False, read_only=True, catalog=self.catalog)
        self.task_id = str(self._core.task_id)
        self._source = get_local_source_identity(self._core)
        self.source_id = self._source.source_id if self._source else 'local-task:' + self.task_id
        self._position_task_ref_raw = self._core.event_store.get_meta('task_ref')
        self._check()

    def _check(self):
        if self.binding is not None:
            self.binding()
        for path, witness in self._paths.items():
            if _owned(path) != witness:
                raise RegistryBrowserError('access_changed')
        store = self._core.event_store
        if (store.get_meta('task_id') != self.task_id
                or store.get_meta('task_ref') != self._position_task_ref_raw
                or get_local_source_identity(self._core) != self._source):
            raise RegistryBrowserError('access_changed')

    def _clear(self):
        self._pages.clear()
        self._items.clear()
        self._retained_bytes = 0

    def registry_browser(self, params):
        with self._lock:
            try:
                self._check()
                action = params['action']
                if action == 'catalog':
                    result = self._catalog()
                elif action == 'list':
                    result = self._list(params)
                elif action == 'versions':
                    result = self._versions(params)
                elif action in {'positions', 'position_history'}:
                    from .registry_positions import LocalPositionQueries
                    positions = LocalPositionQueries(self)
                    result = positions.project_current(positions.read(params))
                elif action in {'record', 'material'}:
                    result = self._read(params)
                else:
                    raise RegistryBrowserError('invalid_query')
                # Retained pages and links pin structure, never current body
                # eligibility.  Reproject even when their immutable cache hit.
                if action not in {'positions', 'position_history'}:
                    result = self._project_current(result)
                if _size(result) > MAX_RESPONSE_BYTES:
                    raise RegistryBrowserError('scope_limit')
                self._check()
                return deepcopy(result)
            except RegistryBrowserError:
                self._clear()
                raise
            except (OSError, PermissionError) as exc:
                self._clear()
                raise RegistryBrowserError('access_changed') from exc
            except Exception as exc:
                self._clear()
                raise RegistryBrowserError('read_failed') from exc

    def _predicate(self):
        return self._core.event_store._canonical_member_sql(member_kind='object',
            member_identity_sql='o.version_id', published_event_sql='published_event.ordinal')

    def _supported(self, kind):
        try:
            self.catalog.require(kind, category='object')
        except SchemaGovernanceError:
            return False
        return True

    def _catalog(self):
        from cpn.rpnh.registry._event_store.material_availability import source_governs_materials_at
        from .registry_positions import CAPABILITY
        with self._core.event_store.connection_scope() as db:
            db.execute('BEGIN')
            governed = source_governs_materials_at(self._core.event_store, db, self._core.task_id)
        upper = self._core.event_store.canonical_view().through_ordinal
        with self._core.event_store.connection_scope() as db:
            rows = db.execute('SELECT DISTINCT o.object_type FROM objects o JOIN events published_event '
                'ON published_event.event_id=o.published_event_id WHERE ' + self._predicate()
                + ' ORDER BY o.object_type LIMIT ?', (upper, upper, upper, upper, MAX_ITEMS + 1)).fetchall()
        if len(rows) > MAX_ITEMS:
            raise RegistryBrowserError('scope_limit')
        return {'schema_version': SCHEMA, 'action': 'catalog', 'access_mode': 'local_owner',
            'sources': [{'source_id': self.source_id, 'access_state': 'readable',
                'access_mode': 'local_owner', 'source_bound': self._source is not None,
                'task_id': self.task_id, 'version_query': True,
                **({'position_query': deepcopy(CAPABILITY)} if self._position_task_ref_raw is not None
                    and self._supported('resource_address_binding/v1') else {}),
                **({'body_read_mode': 'explicit_delivery_required'} if governed else {}),
                'entry_types': [r[0] for r in rows if self._supported(r[0])],
                'unsupported_entry_types': [r[0] for r in rows if not self._supported(r[0])]}]}

    def _new_page(self, entry_type, cut, offset=0, *, anchor=None, logical_id=None):
        if len(self._pages) >= MAX_PAGES:
            raise RegistryBrowserError('scope_limit')
        token = secrets.token_urlsafe(24)
        self._pages[token] = {'entry_type': entry_type, 'cut': cut, 'offset': offset, 'response': None,
                              'anchor': anchor, 'logical_id': logical_id}
        return token

    def _cut(self):
        upper = self._core.event_store.canonical_view().through_ordinal
        with self._core.event_store.connection_scope() as db:
            row = db.execute('SELECT event_id FROM events WHERE ordinal=?', (upper,)).fetchone()
        return {'schema_version': 'rpnh/registry_browser/local_cut/v1', 'source_id': self.source_id,
            'source_bound': self._source is not None, 'task_id': self.task_id,
            'through_ordinal': upper, 'head_event_id': row[0] if row else None,
            'cut_id': secrets.token_urlsafe(24)}

    def _view(self, cut):
        if (cut['task_id'] != self.task_id or cut['source_id'] != self.source_id
                or cut['source_bound'] != (self._source is not None)):
            raise RegistryBrowserError('stale_observation')
        upper = cut['through_ordinal']
        with self._core.event_store.connection_scope() as db:
            row = db.execute('SELECT event_id FROM events WHERE ordinal=?', (upper,)).fetchone()
        if (row[0] if row else None) != cut['head_event_id']:
            raise RegistryBrowserError('stale_observation')
        return self._core.event_store.canonical_view(through_ordinal=upper)

    def _reference(self, row):
        ref = VersionRef(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id']))
        if self._source:
            if ref.entity_type == 'resource_version/v1':
                return SourceQualifiedResourceRef(self.source_id,
                    ResourceVersionRef(ref.entity_id, ref.version_id)).to_dict()
            return SourceQualifiedVersionRef(self.source_id, ref).to_dict()
        return {'schema_version': LOCAL_REF_SCHEMA, 'task_id': self.task_id,
            'ref': {'entity_type': ref.entity_type, 'logical_id': str(ref.entity_id),
                'version_id': str(ref.version_id)}}

    def _prepared(self, row):
        body = json.loads(row['metadata_json'])
        self.catalog.validate_instance(row['object_type'], category='object', instance=body)
        owner = body.get('task_ref') or body.get('owner_task_ref')
        if owner:
            local = owner.get('ref', owner)
            if (local.get('logical_id') != self.task_id
                    or 'source_id' in owner and (self._source is None or owner['source_id'] != self.source_id)
                    or self._source and local != SourceQualifiedVersionRef(self.source_id, self._source.task_ref).to_dict()['ref']):
                raise RegistryBrowserError('not_disclosed')
        if row['object_type'] == 'resource_version/v1' and (
                body['resource_id'] != row['logical_id'] or body['resource_version_id'] != row['version_id']
                or body['size'] != row['size'] or body['media_type'] != row['media_type']):
            raise RegistryBrowserError('read_failed')
        prepared = PreparedObject(row['object_type'], TypedId.parse(row['logical_id']),
            TypedId.parse(row['version_id']), row['size'], row['media_type'], row['schema_ref'],
            TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None,
            row['storage_locator'], body)
        self._core.object_store.validate_envelope(prepared)
        return prepared

    def _files(self, row):
        path = self._core.object_store.path_for_version(TypedId.parse(row['version_id']))
        try:
            return {p: _owned(p) for p in (path.parent, path)}
        except FileNotFoundError:
            # Metadata/identity survives a missing or formally purged carrier.
            # Material still requires the original captured file witnesses.
            return None

    def _project_current(self, result):
        from cpn.rpnh.collaboration.material_read_policy import project_material_fields
        result = deepcopy(result)

        def project(value, cut):
            reference = value.get('entry_ref')
            if reference is None:
                return
            fields, current = project_material_fields(self._core, reference,
                value.get('fields', {}), source_cut=cut)
            if current is not None:
                if 'fields' in value:
                    value['fields'] = fields
                value.update(current, material_may_be_available=False)

        cut = result.get('source_cut')
        project(result, cut)
        for entry in result.get('entries', ()):
            project(entry, cut)
        for link in result.get('linked_entries', ()):
            project(link, cut)
        return result

    def _versions(self, params):
        # Identity and history scope come only from a retained exact item.
        anchor = params['handle']
        item = self._items.get(anchor)
        if item is None or item.get('kind') == 'position':
            raise RegistryBrowserError('stale_observation')
        self._item(item)
        return self._list({'source_id': self.source_id, 'entry_type': item['row']['object_type'],
                           'cursor': params.get('cursor')}, anchor=anchor)

    def _list(self, params, *, anchor=None):
        if params['source_id'] != self.source_id:
            raise RegistryBrowserError('not_disclosed')
        kind = params['entry_type']
        if not self._supported(kind):
            raise RegistryBrowserError('unsupported')
        token = params.get('cursor')
        if token is None:
            item = self._items[anchor] if anchor is not None else None
            token = self._new_page(kind, item['cut'] if item else self._cut(),
                anchor=anchor, logical_id=item['row']['logical_id'] if item else None)
        page = self._pages.get(token)
        if page is None or page.get('entry_type') != kind or page.get('anchor') != anchor:
            raise RegistryBrowserError('stale_observation')
        cut = page['cut']
        view = self._view(cut)
        if page['response'] is not None:
            return page['response']
        upper = cut['through_ordinal']
        # Select bounded IDs/lengths first, then use Core's exact canonical API.
        identity_clause = ' AND o.logical_id=?' if page['logical_id'] is not None else ''
        identity_params = (page['logical_id'],) if page['logical_id'] is not None else ()
        with self._core.event_store.connection_scope() as db:
            rows = db.execute('SELECT o.rowid, o.version_id, length(CAST(o.metadata_json AS BLOB)) AS bytes '
                'FROM objects o JOIN events published_event ON published_event.event_id=o.published_event_id '
                'WHERE ' + self._predicate() + ' AND o.object_type=?' + identity_clause + ' AND o.rowid>? ORDER BY o.rowid LIMIT ?',
                (upper, upper, upper, upper, kind) + identity_params + (page['offset'], PAGE_SIZE + 1)).fetchall()
        if sum(r['bytes'] for r in rows[:PAGE_SIZE]) > MAX_RESPONSE_BYTES:
            raise RegistryBrowserError('scope_limit')
        entries = []
        for selected in rows[:PAGE_SIZE]:
            if len(self._items) >= MAX_ITEMS:
                raise RegistryBrowserError('scope_limit')
            row = dict(self._core.event_store.object_row_for_view(view, selected['version_id']))
            prepared = self._prepared(row)
            ref = self._reference(row)
            handle = secrets.token_urlsafe(24)
            files = self._files(row)
            fields = {'media_type': row['media_type'], 'byte_count': row['size'],
                'commit_ordinal': self._core.event_store.canonical_object_publication_ordinal(
                    prepared.version_id, through_ordinal=upper)}
            for name in ('summary', 'title', 'name'):
                if name in prepared.metadata:
                    fields[name] = prepared.metadata[name]
            self._items[handle] = {'row': row, 'ref': ref, 'cut': cut, 'files': files}
            self._retained_bytes += _size(row) + _size(ref) + _size(cut) + 512
            entries.append({'entry_ref': ref, 'fields': fields, 'handle': handle,
                'material_may_be_available': kind == 'resource_version/v1' and files is not None})
        next_cursor = self._new_page(kind, cut, rows[PAGE_SIZE - 1]['rowid'], anchor=anchor, logical_id=page['logical_id']) if len(rows) > PAGE_SIZE else None
        result = {'schema_version': SCHEMA, 'action': 'versions' if anchor is not None else 'list', 'source_id': self.source_id,
            'entry_type': kind, 'entries': entries, 'source_cut': cut, 'page_cursor': token,
            'next_cursor': next_cursor, 'coverage': {'canonical_only': True, 'through_ordinal': upper,
                'has_more': next_cursor is not None}}
        if anchor is not None:
            result['version_scope'] = {'anchor_ref': self._items[anchor]['ref'],
                'logical_id': page['logical_id'], 'entry_type': kind,
                'scope': 'same_logical_object_at_observed_cut', 'order': 'storage_insertion_order',
                'selects_current': False}
        self._retained_bytes += _size(result)
        if self._retained_bytes > MAX_RETAINED_BYTES:
            raise RegistryBrowserError('scope_limit')
        # Reuse one authority value only within this call, then verify the
        # same persisted head still exists before retaining the response.
        self._view(cut)
        page['response'] = deepcopy(result)
        return result

    def _item(self, item, *, material=False):
        self._check()
        row = self._core.event_store.object_row_for_view(self._view(item['cut']), item['row']['version_id'])
        if row is None or dict(row) != item['row'] or self._reference(row) != item['ref']:
            raise RegistryBrowserError('stale_observation')
        if material and (item['files'] is None or self._files(row) != item['files']):
            raise RegistryBrowserError('access_changed')
        return self._prepared(row)

    def _links(self, item, prepared):
        # Bounded same-cut links from the selected record, never arbitrary IDs
        # supplied by the browser. The existing read boundary remains in force.
        if 'links' in item:
            return item['links']
        from .registry_navigation import declared_links
        refs = declared_links(prepared.object_type, prepared.metadata)
        links = []
        view = self._view(item['cut'])
        for label, ref in refs[:PAGE_SIZE]:
            link = {'label': label, 'status': 'unavailable'}
            if not self._supported(ref.entity_type):
                link['status'] = 'unsupported'
                links.append(link)
                continue
            with self._core.event_store.connection_scope() as db:
                size = db.execute('SELECT length(CAST(metadata_json AS BLOB)) FROM objects WHERE version_id=?',
                                  (str(ref.version_id),)).fetchone()
            if size is not None and size[0] > MAX_RESPONSE_BYTES:
                raise RegistryBrowserError('scope_limit')
            row = self._core.event_store.object_row_for_view(view, ref.version_id)
            if row is None or row['object_type'] != ref.entity_type or row['logical_id'] != str(ref.entity_id):
                links.append(link)
                continue
            row = dict(row)
            try:
                target = self._prepared(row)
                files = self._files(row)
            except (OSError, RegistryBrowserError, SchemaGovernanceError, ObjectIntegrityError, ValueError):
                # An optional target cannot invalidate an otherwise readable
                # parent. Global binding is still checked after the response.
                links.append(link)
                continue
            if len(self._items) >= MAX_ITEMS:
                raise RegistryBrowserError('scope_limit')
            handle = secrets.token_urlsafe(24)
            exact = self._reference(row)
            retained = _size(row) + _size(exact) + _size(item['cut']) + 512
            if self._retained_bytes + retained > MAX_RETAINED_BYTES:
                raise RegistryBrowserError('scope_limit')
            self._retained_bytes += retained
            self._items[handle] = {'row': row, 'ref': exact, 'cut': item['cut'], 'files': files}
            link.update(status='readable', handle=handle, entry_ref=exact,
                        material_may_be_available=target.object_type == 'resource_version/v1' and files is not None)
            links.append(link)
        response = {'linked_entries': links, 'link_coverage': {
            'scope': 'declared_refs_of_selected_record', 'total': len(refs),
            'shown': len(links), 'has_more': len(refs) > PAGE_SIZE}}
        self._retained_bytes += _size(response)
        if self._retained_bytes > MAX_RETAINED_BYTES:
            raise RegistryBrowserError('scope_limit')
        item['links'] = response
        return response

    def _read(self, params):
        item = self._items.get(params['handle'])
        if item is None or item.get('kind') == 'position':
            raise RegistryBrowserError('stale_observation')
        prepared = self._item(item)
        result = {'schema_version': SCHEMA, 'action': params['action'],
            'entry_ref': item['ref'], 'source_cut': item['cut']}
        if params['action'] == 'record':
            result.update(fields=dict(prepared.metadata), disclosure={'metadata': 'canonical'},
                material_may_be_available=prepared.object_type == 'resource_version/v1' and item['files'] is not None)
            result.update(self._links(item, prepared))
        else:
            if prepared.object_type != 'resource_version/v1':
                raise RegistryBrowserError('unsupported')
            from cpn.rpnh.collaboration.material_read_policy import (
                require_untracked_material_read, MaterialReadPolicyError)
            try:
                require_untracked_material_read(self._core,
                    ResourceVersionRef(prepared.logical_id, prepared.version_id),
                    purpose='local_registry_browser')
            except MaterialReadPolicyError as exc:
                raise RegistryBrowserError(exc.code.lower()) from exc
            if prepared.size > MAX_PREVIEW_BYTES:
                raise RegistryBrowserError('scope_limit')
            # Lowest existing bounded registered reader accepts a local exact
            # PreparedObject; no invented SourceQualified ref or grant needed.
            self._item(item, material=True)
            raw = self._core.object_store.read_registered(prepared, max_bytes=MAX_PREVIEW_BYTES)
            self._item(item, material=True)
            try:
                body = raw.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise RegistryBrowserError('invalid_utf8') from exc
            result.update(body=body, media_type=prepared.media_type, byte_count=len(raw))
        self._item(item)
        return result

