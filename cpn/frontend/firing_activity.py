"""Opt-in three-event activity display, separate from checkpoint authority."""
from __future__ import annotations

import base64
import json
import re

from . import checkpoint_view as checkpoints
from cpn.rpnh.registry._event_store.views import (
    ACTIVITY_TYPES, ActivityStaleError, activity_reference, _activity_head)

SCHEMA = 'rpnh/firing_activity/v1'
CURSOR_SCHEMA = 'rpnh/firing_activity_cursor/v1'
PROFILE = 'single_registry_standard_activity_metadata/v1'
QUERY_BYTES = 16384
CURSOR_BYTES = 8192
MAX_ORDINAL = 2 ** 53 - 1


class ActivityQueryError(ValueError):
    pass


class ActivityAccessChanged(RuntimeError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActivityQueryError('duplicate JSON field')
        result[key] = value
    return result


def _nonfinite(value):
    raise ActivityQueryError('non-finite JSON value')


def _json(text):
    return json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def transitions(value):
    if (type(value) is not list or not 1 <= len(value) <= 64
            or any(type(x) is not str or not x or len(x.encode()) > 256 for x in value)
            or len(value) != len(set(value))):
        raise ActivityQueryError('transition_ids requires 1..64 unique nonempty IDs')
    return sorted(value)


def parse_query(query):
    from urllib.parse import parse_qs
    if len(query.encode()) > QUERY_BYTES:
        raise ActivityQueryError('activity query is too large')
    try:
        q = parse_qs(query, keep_blank_values=True, strict_parsing=True, encoding='utf8', errors='strict')
        if not {'net_ref', 'checkpoint_ref', 'cut', 'transition_ids'} <= set(q) or set(q) - {'net_ref', 'checkpoint_ref', 'cut', 'transition_ids', 'limit', 'cursor'} or any(len(v) != 1 for v in q.values()):
            raise ActivityQueryError('invalid activity query keys')
        def positive(key, default=None):
            text = q.get(key, [str(default)])[0]
            if re.fullmatch(r'[1-9][0-9]{0,15}', text) is None or int(text) > MAX_ORDINAL:
                raise ActivityQueryError('invalid positive decimal')
            return int(text)
        selected = checkpoints.selector(_json(q['net_ref'][0]), _json(q['checkpoint_ref'][0]), positive('cut'))
        ids = transitions(_json(q['transition_ids'][0]))
        limit = positive('limit', 50)
        if not 1 <= limit <= 100:
            raise ActivityQueryError('limit must be within 1..100')
        cursor = q.get('cursor', [None])[0]
        if cursor is not None:
            decode_cursor(cursor)
        return {**selected, 'transition_ids': ids, 'limit': limit, 'cursor': cursor}
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ActivityQueryError('invalid firing-activity query') from exc


def encode_cursor(scope, capture, last):
    value = {'schema_version': CURSOR_SCHEMA, 'scope': scope, 'capture': capture, 'after': last}
    token = base64.urlsafe_b64encode(_canonical(value).encode()).decode().rstrip('=')
    if len(token) > CURSOR_BYTES:
        raise RuntimeError('activity cursor exceeds the reader budget')
    return token


def decode_cursor(token):
    try:
        if type(token) is not str or not 1 <= len(token) <= CURSOR_BYTES or re.fullmatch(r'[A-Za-z0-9_-]+', token) is None:
            raise ValueError('cursor encoding')
        value = _json(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)).decode('utf8'))
        if (type(value) is not dict or set(value) != {'schema_version', 'scope', 'capture', 'after'}
                or value['schema_version'] != CURSOR_SCHEMA or type(value['scope']) is not dict
                or type(value['capture']) is not dict or set(value['capture']) != {'head_ordinal', 'writer_fencing_epoch'}
                or type(value['capture']['head_ordinal']) is not int or not 1 <= value['capture']['head_ordinal'] <= MAX_ORDINAL
                or type(value['capture']['writer_fencing_epoch']) is not int or not 0 <= value['capture']['writer_fencing_epoch'] <= MAX_ORDINAL
                or type(value['after']) is not list or len(value['after']) != 2
                or type(value['after'][0]) is not int or not 1 <= value['after'][0] <= value['capture']['head_ordinal']
                or type(value['after'][1]) is not str):
            raise ValueError('cursor shape')
        from cpn.rpnh.registry.identities import TypedId
        if TypedId.parse(value['after'][1]).kind != 'event':
            raise ValueError('cursor event identity')
        if encode_cursor(value['scope'], value['capture'], value['after']) != token:
            raise ValueError('noncanonical cursor encoding')
        return value
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ActivityQueryError('invalid activity cursor') from exc


def firing_activity(provider, *, net_ref, checkpoint_ref, cut, transition_ids, limit=50, cursor=None):
    selected = checkpoints.selector(net_ref, checkpoint_ref, cut)
    ids = transitions(transition_ids)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ActivityQueryError('invalid activity limit')
    previous = decode_cursor(cursor) if cursor is not None else None
    try:
        provider._bound()
    except Exception as exc:
        raise ActivityAccessChanged('activity source binding changed') from exc
    try:
        core, anchor = checkpoints._capture_anchor(provider)
    except RuntimeError as exc:
        if str(exc) == 'Registry advanced during dashboard read; retry':
            raise ActivityStaleError('activity observation changed') from exc
        raise
    capture = {'head_ordinal': anchor['source']['verified_head_ordinal'],
               'writer_fencing_epoch': anchor['source']['writer_fencing_epoch']}
    _, _, identity = checkpoints._chain(provider, core, anchor, selected)
    reads = checkpoints._BoundedReads(core, cut)
    checkpoint, commit = reads.checkpoint(checkpoint_ref)
    if commit.ordinal != cut:
        raise ValueError('selected checkpoint cut differs')
    _, all_bindings, root = checkpoints._inventory(reads, selected, checkpoint, identity)
    if not set(ids) <= {b['transition_id'] for b in all_bindings}:
        raise ActivityQueryError('activity member is outside selected scope')
    activity_reference(identity['task_ref'], 'task/v1')
    activity_reference(identity['run_ref'], 'native_run_identity/v1')
    source = {'mode': 'registry_current', 'run_dir': str(provider.run_dir), 'task_id': provider.task_id}
    scope = {**identity, 'net_ref': net_ref, 'checkpoint_ref': checkpoint_ref, 'cut': cut,
             'transition_ids': ids, 'supported_event_types': list(ACTIVITY_TYPES),
             'source': source, 'disclosure_profile': PROFILE}
    if previous is not None and previous['scope'] != scope:
        # Same selector in another bound source is an invalid cursor, never an
        # invitation to resolve a path supplied by the client.
        raise ActivityQueryError('activity cursor scope differs')
    if previous is not None and previous['capture'] != capture:
        raise ActivityStaleError('activity observation changed')
    run = reads.metadata(identity['run_ref'], 'native_run_identity/v1')
    branch = reads.metadata(run['task_branch_ref'], 'task_branch/v1')
    if branch['branch_name'] != run['branch_id'] or branch['task_ref'] != identity['task_ref']:
        raise ValueError('activity run/branch identity differs')
    bindings = []
    for b in all_bindings:
        if b['transition_id'] in ids:
            operation = reads.metadata(b['operation_binding_ref'], 'operation_binding/v1')
            bindings.append({**b, **{k: operation[k] for k in ('operation_spec_ref', 'principal_ref', 'authority_decision_ref')}})
    read_scope = {**scope, 'task_branch_ref': run['task_branch_ref'],
                  'branch_id': run['branch_id'], 'team_design_root_ref': root['team_design_root_ref'],
                  'task_round_ref': root['task_round_ref']}
    page = core.event_store.firing_activity_page(catalog=core.catalog, object_store=core.object_store,
        scope=read_scope, bindings=bindings, expected_capture=tuple(capture.values()), limit=limit,
        after=previous['after'] if previous is not None else None)
    try:
        provider._bound()
    except Exception as exc:
        raise ActivityAccessChanged('activity source binding changed') from exc
    with core.event_store.connect() as fresh:
        fresh.execute('BEGIN')
        try:
            if _activity_head(fresh) != tuple(capture.values()):
                raise ActivityStaleError('activity observation changed')
        finally:
            fresh.execute('ROLLBACK')
    context = {'mode': 'retrospective-activity', 'source_set_ref': None, 'manifest_ref': None,
               'path_ref': None, 'disclosure_ref': None, 'query_scope': {**scope, 'evidence_cut': capture},
               'source_cuts': [{'source': source, 'cut': capture}]}
    by_firing = {f['firing_ref']['version_id']: f for f in page['firings']}
    for record in page['records']:
        f = by_firing[record['firing_ref']['version_id']]
        record['evidence'] = {'pointer': {'source': source, 'firing_ref': record['firing_ref']},
            'recorded_position': {'event_id': record['event_id'], 'ordinal': record['recorded_ordinal']},
            'visible_position': max(f['publication_visible_position'], record['transaction_commit_ordinal']) if f['publication_visible_position'] is not None else None,
            'publication_class': f['publication_class_at_evidence'], 'observed_at': capture}
    result = {'schema_version': SCHEMA, 'selector': selected,
        'view': {'context': context, 'activity': {'source_identity': {**identity, 'source': source},
            'supported_event_types': list(ACTIVITY_TYPES), 'record_range': {'after_ordinal': 0, 'through_ordinal': capture['head_ordinal']},
            'firings': page['firings'], 'records': page['records'],
            'coverage': {'scope': context['query_scope'], 'status': 'page', 'loaded_count': len(page['records']), 'total_count': None,
                         'missing': ['completion', 'result', 'settlement', 'successor_checkpoint', 'delta', 'other_activity']},
            'lifecycle_coverage': 'not_provided'}},
        'page': {'request_cursor': cursor, 'returned_count': len(page['records']), 'limit': limit,
                 'has_more': page['has_more'], 'end_of_supported_scope': not page['has_more'],
                 'next_cursor': encode_cursor(scope, capture, page['last_key']) if page['has_more'] else None}}
    return result
