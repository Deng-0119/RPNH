"""Bounded local task-address observations over original canonical facts.

Handles and pages belong to the existing local browser's finite observation
cache. They grant no write or body authority. Structure is read in one explicit
SQLite snapshot; every delivery separately projects current exact-version
effective availability in a new read snapshot. No live Owner is involved.
"""
from __future__ import annotations

from copy import deepcopy
import json
import secrets

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry._event_store.address_promotion import _binding_event
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits
from .registry_browser import (
    SCHEMA, MAX_PAGES, MAX_ITEMS, MAX_RETAINED_BYTES, RegistryBrowserError,
)

PAGE_SIZE = 25
MAX_RESPONSE_BYTES = ReadLimits().max_response_bytes
_BINDING = 'resource_address_binding/v1'
CAPABILITY = {'supported': True, 'scope': 'task_scope_only',
              'history': True, 'body_mode': 'metadata_only'}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _ref(value, kind):
    if (type(value) is not dict or set(value) != {'entity_type', 'logical_id', 'version_id'}
            or value['entity_type'] != kind):
        raise RegistryBrowserError('read_failed')
    base = kind.split('/')[0]
    TypedId.parse(value['logical_id'], expected=base)
    TypedId.parse(value['version_id'], expected=base + '_version')
    return deepcopy(value)


def _row_ref(row):
    return {'entity_type': row['object_type'], 'logical_id': row['logical_id'],
            'version_id': row['version_id']}


class LocalPositionQueries:
    def __init__(self, provider):
        self.provider = provider
        self.core = provider._core
        self.store = self.core.event_store
        self._objects, self._bindings = {}, {}
        self._scanned_bytes = 0

    def _reserve(self, value):
        self.provider._retained_bytes += _size(value)
        if self.provider._retained_bytes > MAX_RETAINED_BYTES:
            raise RegistryBrowserError('scope_limit')

    def _predicate(self):
        return self.store._canonical_member_sql(member_kind='object',
            member_identity_sql='o.version_id', published_event_sql='published_event.ordinal')

    def _exact(self, db, ref, upper):
        key = (ref['entity_type'], ref['logical_id'], ref['version_id'], upper)
        if key in self._objects:
            return self._objects[key]
        params = (upper, upper, upper, upper,
                  ref['entity_type'], ref['logical_id'], ref['version_id'])
        where = (' FROM objects o JOIN events published_event '
            'ON published_event.event_id=o.published_event_id WHERE ' + self._predicate()
            + ' AND o.object_type=? AND o.logical_id=? AND o.version_id=?')
        size = db.execute('SELECT length(CAST(o.metadata_json AS BLOB))' + where, params).fetchone()
        if size is None:
            raise RegistryBrowserError('stale_observation')
        self._scanned_bytes += int(size[0]) + 512
        if self._scanned_bytes > MAX_RESPONSE_BYTES:
            raise RegistryBrowserError('scope_limit')
        row = dict(db.execute('SELECT o.*' + where, params).fetchone())
        prepared = self.provider._prepared(row)
        self._objects[key] = row, prepared
        return row, prepared

    def _task(self, db, upper):
        row = db.execute("SELECT value FROM registry_meta WHERE key='task_ref'").fetchone()
        raw = None if row is None else row[0]
        if raw != self.provider._position_task_ref_raw:
            raise RegistryBrowserError('access_changed')
        if raw is None:
            raise RegistryBrowserError('unsupported')
        task = _ref(json.loads(raw), 'task/v1')
        if task['logical_id'] != self.provider.task_id:
            raise RegistryBrowserError('access_changed')
        if self.provider._source is not None:
            from cpn.rpnh.registry.strict_contracts import ref_payload
            if task != ref_payload(self.provider._source.task_ref):
                raise RegistryBrowserError('access_changed')
        self._exact(db, task, upper)
        return task

    def _cut(self, db):
        upper = int(db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0])
        row = db.execute('SELECT event_id FROM events WHERE ordinal=?', (upper,)).fetchone()
        return {'schema_version': 'rpnh/registry_browser/local_cut/v1',
            'source_id': self.provider.source_id, 'source_bound': self.provider._source is not None,
            'task_id': self.provider.task_id, 'through_ordinal': upper,
            'head_event_id': row[0] if row else None, 'cut_id': secrets.token_urlsafe(24)}

    def _check_cut(self, db, cut):
        if (cut['source_id'] != self.provider.source_id or cut['task_id'] != self.provider.task_id
                or cut['source_bound'] != (self.provider._source is not None)):
            raise RegistryBrowserError('stale_observation')
        row = db.execute('SELECT event_id FROM events WHERE ordinal=?',
            (cut['through_ordinal'],)).fetchone()
        if (row[0] if row else None) != cut['head_event_id']:
            raise RegistryBrowserError('stale_observation')
        return self._task(db, cut['through_ordinal'])

    def _publication(self, db, row, upper):
        roots = db.execute('SELECT p.* FROM firing_temporary_members m '
            'JOIN firing_publications p ON p.firing_version_id=m.firing_version_id '
            "WHERE m.member_kind='object' AND m.member_identity=? LIMIT 2",
            (row['version_id'],)).fetchall()
        if len(roots) > 1:
            raise RegistryBrowserError('read_failed')
        if not roots:
            event = db.execute('SELECT ordinal FROM events WHERE event_id=? AND ordinal<=?',
                (row['published_event_id'], upper)).fetchone()
            if event is None:
                raise RegistryBrowserError('read_failed')
            return int(event[0]), None
        root, = roots
        if root['state'] != 'PUBLISHED':
            raise RegistryBrowserError('read_failed')
        events = db.execute('SELECT ordinal FROM events WHERE transaction_id=? '
            "AND event_type='transaction_committed/v1' AND ordinal<=? LIMIT 2",
            (root['published_transaction_id'], upper)).fetchall()
        if len(events) != 1:
            raise RegistryBrowserError('read_failed')
        return int(events[0][0]), root

    def _binding(self, db, ref, cut, task):
        ref = _ref(ref, _BINDING)
        upper = cut['through_ordinal']
        key = ref['version_id'], upper
        if key in self._bindings:
            return self._bindings[key]
        row, prepared = self._exact(db, ref, upper)
        data = prepared.metadata
        if data['scope_ref'] != task:
            raise RegistryBrowserError('not_disclosed')
        predicate = self.store._canonical_member_sql(member_kind='event',
            member_identity_sql='e.event_id', published_event_sql='e.ordinal')
        events = db.execute('SELECT e.* FROM events e WHERE ' + predicate
            + " AND e.transaction_id=? AND e.aggregate_id=? AND e.event_type IN "
            "('resource_address_bound/v1','resource_address_unbound/v1') "
            "AND json_extract(e.payload_json,'$.binding_ref.version_id')=? "
            'AND length(CAST(e.payload_json AS BLOB))<=? LIMIT 2',
            (upper, upper, upper, upper, row['transaction_id'], row['logical_id'],
             row['version_id'], MAX_RESPONSE_BYTES)).fetchall()
        if len(events) != 1:
            raise RegistryBrowserError('read_failed')
        exact, address, previous, authorization, _paired = _binding_event(db, events[0])
        if exact != ref or address['scope_ref'] != task:
            raise RegistryBrowserError('read_failed')
        if previous is not None:
            _ref(previous, _BINDING)
            if previous['logical_id'] != ref['logical_id'] or previous == ref:
                raise RegistryBrowserError('read_failed')
        commit, root = self._publication(db, row, upper)
        invocation = firing = None
        if root is not None:
            invocation = {'entity_type': 'invocation/v1', 'logical_id': root['invocation_logical_id'],
                'version_id': root['invocation_version_id']}
            firing = {'entity_type': 'transition_firing/v1', 'logical_id': root['firing_logical_id'],
                'version_id': root['firing_version_id']}
            _ref(invocation, 'invocation/v1'); _ref(firing, 'transition_firing/v1')
            _irow, original_i = self._exact(db, invocation, upper)
            _frow, original_f = self._exact(db, firing, upper)
            if (row['producer_invocation_id'] != invocation['logical_id']
                    or original_i.metadata['invocation_ref'] != invocation
                    or original_i.metadata['task_ref'] != task
                    or original_i.metadata['operation_binding_ref'] != authorization
                    or original_i.metadata['own_transition_firing_ref'] != firing
                    or original_f.metadata['transition_firing_ref'] != firing
                    or original_f.metadata['task_ref'] != task
                    or original_f.metadata['operation_binding_ref'] != authorization):
                raise RegistryBrowserError('read_failed')
        resource = data['resource_ref']
        resource_entry = None
        if resource is not None:
            if type(resource) is not dict or set(resource) != {'resource_id', 'resource_version_id'}:
                raise RegistryBrowserError('read_failed')
            TypedId.parse(resource['resource_id'], expected='resource')
            TypedId.parse(resource['resource_version_id'], expected='resource_version')
            target = {'entity_type': 'resource_version/v1', 'logical_id': resource['resource_id'],
                'version_id': resource['resource_version_id']}
            resource_row, original = self._exact(db, target, upper)
            if original.metadata.get('task_ref') != task:
                raise RegistryBrowserError('not_disclosed')
            resource_entry = self.provider._reference(resource_row)
        tombstone = data['lifecycle_state'] == 'tombstoned'
        if tombstone != (resource is None):
            raise RegistryBrowserError('read_failed')
        result = {'address': address, 'sequence': data['resulting_stream_sequence'],
            'entry': {'binding_ref': exact, 'previous_binding_ref': previous,
                'operation': 'unbind' if tombstone else 'bind', 'tombstoned': tombstone,
                'resource_ref': deepcopy(resource), 'entry_ref': self.provider._reference(row),
                'resource_entry_ref': resource_entry, 'commit_ordinal': commit,
                'producer_invocation_ref': invocation, 'firing_ref': firing}}
        self._bindings[key] = result
        return result

    def _previous(self, db, current, cut, task):
        ref = current['entry']['previous_binding_ref']
        if ref is None:
            return None
        previous = self._binding(db, ref, cut, task)
        if (previous['address'] != current['address']
                or previous['sequence'] >= current['sequence']
                or previous['entry']['commit_ordinal'] > current['entry']['commit_ordinal']):
            raise RegistryBrowserError('read_failed')
        return previous

    def _head(self, db, logical_id, cut, task):
        upper = cut['through_ordinal']
        rows = db.execute('SELECT o.version_id FROM objects o JOIN events published_event '
            'ON published_event.event_id=o.published_event_id WHERE ' + self._predicate()
            + " AND o.object_type=? AND o.logical_id=? ORDER BY "
            "json_extract(o.metadata_json,'$.resulting_stream_sequence') DESC LIMIT 2",
            (upper, upper, upper, upper, _BINDING, logical_id)).fetchall()
        if not rows:
            raise RegistryBrowserError('stale_observation')
        head = self._binding(db, {'entity_type': _BINDING, 'logical_id': logical_id,
            'version_id': rows[0][0]}, cut, task)
        previous = self._previous(db, head, cut, task)
        if ((previous is None) != (len(rows) == 1)
                or previous is not None and previous['entry']['binding_ref']['version_id'] != rows[1][0]):
            raise RegistryBrowserError('read_failed')
        return head

    def _page(self, query, cut, *, offset='', handle=None, next_ref=None):
        if len(self.provider._pages) >= MAX_PAGES:
            raise RegistryBrowserError('scope_limit')
        token = secrets.token_urlsafe(24)
        page = {'query': query, 'cut': deepcopy(cut), 'offset': offset, 'handle': handle,
            'next_ref': deepcopy(next_ref), 'response': None}
        self._reserve(page)
        self.provider._pages[token] = page
        return token

    def _reply(self, action, cut, token, next_cursor, entries):
        return {'schema_version': SCHEMA, 'action': action, 'source_id': self.provider.source_id,
            'source_cut': deepcopy(cut), 'page_cursor': token, 'next_cursor': next_cursor,
            'body_mode': 'metadata_only', 'coverage': {'canonical_only': True,
                'scope': 'task_scope_only', 'through_ordinal': cut['through_ordinal'],
                'has_more': next_cursor is not None, 'order': 'address_identity_ascending'
                    if action == 'positions' else 'binding_chain_newest_first'},
            'entries': entries}

    def read(self, params):
        action = params['action']
        with self.store.connection_scope() as db:
            db.execute('BEGIN')
            if action == 'positions':
                result = self._positions(db, params)
            else:
                result = self._history(db, params)
            self._check_cut(db, result['source_cut'])
            return deepcopy(result)

    def _positions(self, db, params):
        if params['source_id'] != self.provider.source_id:
            raise RegistryBrowserError('not_disclosed')
        token = params.get('cursor')
        if token is None:
            token = self._page('positions', self._cut(db))
        page = self.provider._pages.get(token)
        if page is None or page.get('query') != 'positions':
            raise RegistryBrowserError('stale_observation')
        cut = page['cut']
        task = self._check_cut(db, cut)
        if page['response'] is not None:
            return page['response']
        upper = cut['through_ordinal']
        rows = db.execute('SELECT DISTINCT o.logical_id FROM objects o JOIN events published_event '
            'ON published_event.event_id=o.published_event_id WHERE ' + self._predicate()
            + " AND o.object_type=? AND json_extract(o.metadata_json,'$.scope_ref.entity_type')=? "
            "AND json_extract(o.metadata_json,'$.scope_ref.logical_id')=? "
            "AND json_extract(o.metadata_json,'$.scope_ref.version_id')=? "
            'AND o.logical_id>? ORDER BY o.logical_id LIMIT ?',
            (upper, upper, upper, upper, _BINDING, task['entity_type'], task['logical_id'],
             task['version_id'], page['offset'], PAGE_SIZE + 1)).fetchall()
        entries = []
        for row in rows[:PAGE_SIZE]:
            head = self._head(db, row[0], cut, task)
            if len(self.provider._items) >= MAX_ITEMS:
                raise RegistryBrowserError('scope_limit')
            handle = secrets.token_urlsafe(24)
            item = {'kind': 'position', 'address': deepcopy(head['address']),
                'head_binding_ref': deepcopy(head['entry']['binding_ref']), 'cut': deepcopy(cut)}
            self._reserve(item)
            self.provider._items[handle] = item
            entries.append({'handle': handle, 'address': deepcopy(head['address']), **deepcopy(head['entry'])})
        next_cursor = self._page('positions', cut, offset=rows[PAGE_SIZE - 1][0]) if len(rows) > PAGE_SIZE else None
        result = self._reply('positions', cut, token, next_cursor, entries)
        self._reserve(result)
        page['response'] = deepcopy(result)
        return result

    def _history(self, db, params):
        handle = params['handle']
        item = self.provider._items.get(handle)
        if item is None or item.get('kind') != 'position':
            raise RegistryBrowserError('stale_observation')
        cut = item['cut']
        task = self._check_cut(db, cut)
        head = self._head(db, item['head_binding_ref']['logical_id'], cut, task)
        if head['entry']['binding_ref'] != item['head_binding_ref'] or head['address'] != item['address']:
            raise RegistryBrowserError('stale_observation')
        token = params.get('cursor')
        if token is None:
            token = self._page('position_history', cut, handle=handle, next_ref=item['head_binding_ref'])
        page = self.provider._pages.get(token)
        if (page is None or page.get('query') != 'position_history' or page.get('handle') != handle
                or page['cut'] != cut):
            raise RegistryBrowserError('stale_observation')
        if page['response'] is not None:
            return page['response']
        current = self._binding(db, page['next_ref'], cut, task)
        entries = []
        while current is not None and len(entries) < PAGE_SIZE:
            if current['address'] != item['address']:
                raise RegistryBrowserError('read_failed')
            entries.append(deepcopy(current['entry']))
            current = self._previous(db, current, cut, task)
        next_cursor = (self._page('position_history', cut, handle=handle,
            next_ref=current['entry']['binding_ref']) if current is not None else None)
        result = self._reply('position_history', cut, token, next_cursor, entries)
        result.update(handle=handle, address=deepcopy(item['address']),
            head_binding_ref=deepcopy(item['head_binding_ref']))
        self._reserve(result)
        page['response'] = deepcopy(result)
        return result

    def project_current(self, result):
        from cpn.rpnh.material_body_policy import resolve_body_availability
        result = deepcopy(result)
        with self.store.connection_scope() as db:
            db.execute('BEGIN')
            upper = int(db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0])
            self._task(db, upper)
            current = {}
            for entry in result['entries']:
                resource = entry['resource_ref']
                if resource is None:
                    entry['availability'] = None
                    continue
                key = resource['resource_version_id']
                if key not in current:
                    exact = {'entity_type': 'resource_version/v1', 'logical_id': resource['resource_id'],
                        'version_id': key}
                    observation = resolve_body_availability(self.store, db, self.core.task_id, exact,
                        source_cut=result['source_cut'])
                    if (observation.get('availability') not in {'available', 'disabled', 'unknown'}
                            or observation.get('availability_as_of') != upper):
                        raise RegistryBrowserError('read_failed')
                    current[key] = observation['availability']
                entry['availability'] = current[key]
            result['availability_as_of'] = upper
        return result


__all__ = ('LocalPositionQueries', 'CAPABILITY', 'PAGE_SIZE')
