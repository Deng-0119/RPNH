"""Fixed product-origin page delivery through the existing read session.

The complete private evaluator runs once before paging. Only detached fixed
proofs/rows enter the session's existing, domain-separated cursor pool.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from typing import ClassVar

from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.resources import ResourceVersionRef
from ..registry.schema_catalog import canonical_json
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .registry_read_contracts import (RegistryReadSessionError, READER_CONTRACT_VERSION,
    PRODUCT_ORIGIN_PROFILE, PRODUCT_ORIGIN_PAGE_SCHEMA, PRODUCT_ORIGIN_CONTRACT_REVISION,
    document_digest, product_origin_delivery_contract)
from ._origin_core_contract import core_contract
from ._origin_query_context import _OriginQueryContext, _bytes_bound
from ._product_origin_core import (_CoreProof, _preauthorize, _SAFE_ERROR_CODES,
    _ERROR_BUFFER_BYTES, _clear_exception_frames)
from ._product_origin_includes import _normalize_include, _evaluate_origin_includes


_UNSUPPORTED = ('direct_derivations', 'calls_in_execution', 'observed_reads',
    'formal_access', 'declarations', 'parent_child', 'recursive_ancestors', 'content_influence')


def _canonical_size(value):
    """Exact canonical JSON length without allocating an encoded string."""
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int:
        return len(str(value))
    if type(value) is str:
        return 2 + sum(2 if char in '\\"\b\f\n\r\t' else 6 if ord(char) < 32 or 127 <= ord(char) <= 65535
            else 12 if ord(char) > 65535 else 1 for char in value)
    if type(value) in (tuple, list):
        return 2 + max(0, len(value) - 1) + sum(_canonical_size(item) for item in value)
    if type(value) is dict:
        return 2 + max(0, len(value) - 1) + sum(_canonical_size(key) + 1 + _canonical_size(item)
            for key, item in value.items())
    raise RegistryReadSessionError('INVALID_QUERY')


@dataclass(frozen=True, slots=True)
class _OriginQuery:
    kind: ClassVar[str] = 'product_origin'
    fingerprint: str
    data: dict
    budget_bytes: int


def _cursor_record_bytes(token, offset):
    # A fixed canonical token/kind/state-reference/offset record plus its tuple
    # bookkeeping. No query rows are reserialized by session accounting.
    return 256 + len(token) + len(str(offset)) + 64 + len(_OriginQuery.kind)


class _OriginDelivery:
    """Single-call owner; pins transferred identities through final callbacks."""
    def __init__(self, session, snapshot):
        self.session, self.snapshot = session, snapshot
        self.states = []
        self.records = []
        self.created = []
        self.context = None
        self.error_reserved = False
        try:
            self.context = _OriginQueryContext(session, snapshot, retained_extra=self.retained_extra)
            self.context.reserve(size=_ERROR_BUFFER_BYTES + 4096)
            self.error_reserved = True
        except BaseException:
            # Assignment to the caller's owner has not completed yet. Break
            # the context's retained_extra bound-method cycle here as well.
            self.close()
            raise

    def retained_extra(self):
        current = self.session._retained_bytes()
        missing = 0
        if not any(captured is self.snapshot for _cut, captured in self.session._cuts.values()):
            missing += self.snapshot.budget_bytes
        for state in self.states:
            if not any(query is state for query, _offset in self.session._cursors.values()):
                missing += state.budget_bytes
        for token, record in self.records:
            if self.session._cursors.get(token) is not record:
                missing += _cursor_record_bytes(token, record[1])
        initial = current if self.context is None else self.context._initial_retained_bytes
        return max(0, current + missing - max(initial, current))

    def pin(self, state, token=None, record=None):
        # Fixed per-call bookkeeping is covered before any protected access.
        if not any(item is state for item in self.states):
            self.states.append(state)
        if record is not None and not any(item is record for _key, item in self.records):
            self.records.append((token, record))

    def check_pins(self):
        # Neither serialization nor a trusted callback may resurrect a cut or
        # cache record already removed by session invalidation. No restoration.
        if (not any(captured is self.snapshot for _cut, captured in self.session._cuts.values())
                or any(self.session._cursors.get(token) is not record for token, record in self.records)):
            raise RegistryReadSessionError('CURSOR_MISMATCH')

    def rollback(self):
        for token, record in self.created:
            if self.session._cursors.get(token) is record:
                del self.session._cursors[token]

    def check(self):
        failure = None
        try:
            self.session.final_recheck((self.snapshot.source_id,))
            self.session._alive()
        except Exception as exc:
            failure = exc
        # Refresh even if invalidation removed the locally pinned state/cut.
        # Current unrelated retention and missing pinned bytes form a union.
        try:
            self.context.reserve()
        except RegistryReadSessionError as exc:
            if failure is None:
                failure = exc
        try:
            self.session._alive()
        except Exception as exc:
            failure = exc
        if failure is not None:
            raise failure

    def fail(self, exc):
        self.rollback()
        code = _safe_code(exc)
        # The fixed allowance remains owned even when a refresh reports that
        # a trusted callback retained too much. Authority errors take priority.
        while True:
            try:
                self.context.reserve()
            except RegistryReadSessionError:
                if code not in {'ACCESS_CHANGED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'SOURCE_UNAVAILABLE'}:
                    code = 'LIMIT_EXCEEDED'
            error = RegistryReadSessionError(code)
            encoded = canonical_json(error.to_dict())
            if (len(encoded) > self.session.limits.max_response_bytes
                    and code not in {'LIMIT_EXCEEDED', 'ACCESS_CHANGED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'SOURCE_UNAVAILABLE'}):
                code = 'LIMIT_EXCEEDED'
                continue
            del encoded
            try:
                self.check()
            except Exception as current:
                checked = _safe_code(current)
                if checked != code:
                    code = checked
                    continue
            raise error from None

    def close(self):
        self.states.clear(); self.records.clear(); self.created.clear()
        self.snapshot = None
        if self.context is not None:
            self.context.close()
        self.session = None


def _safe_code(exc):
    code = getattr(exc, 'code', 'INTEGRITY_FAILED')
    return code if type(code) is str and code in _SAFE_ERROR_CODES | {
        'INVALID_ROOT', 'INVALID_QUERY', 'UNSUPPORTED_RELATION'} else 'INTEGRITY_FAILED'


def _root_shape(root):
    resource = type(root) is SourceQualifiedResourceRef
    try:
        if resource:
            if type(root.ref) is not ResourceVersionRef:
                raise ValueError
            TypedId.parse(str(root.ref.resource_id), expected='resource')
            TypedId.parse(str(root.ref.resource_version_id), expected='resource_version')
        else:
            if (type(root) is not SourceQualifiedVersionRef or type(root.ref) is not VersionRef
                    or root.ref.entity_type != 'operation_result/v1'):
                raise ValueError
            TypedId.parse(str(root.ref.entity_id), expected='operation_result')
            TypedId.parse(str(root.ref.version_id), expected='operation_result_version')
    except Exception:
        raise RegistryReadSessionError('INVALID_ROOT') from None
    return resource


def _fingerprint(session, source, cut, root, include, page_size, context):
    # Tables are small static contract values. Caller strings are measured
    # before canonical request serialization; no unbounded request copy.
    contract = {'validation': core_contract(), 'delivery': product_origin_delivery_contract()}
    request = {'kind': _OriginQuery.kind, 'profile': PRODUCT_ORIGIN_PROFILE,
        'contract_revision': PRODUCT_ORIGIN_CONTRACT_REVISION, 'root': root.to_dict(),
        'include': include, 'page_size': page_size, 'source_cut': cut.to_dict(), 'contract': contract}
    if _canonical_size(request) > session.limits.max_query_bytes:
        raise RegistryReadSessionError('LIMIT_EXCEEDED')
    context.reserve(size=2 * _bytes_bound(request) + 4096)
    identity = {'request': request, 'source_selection': source.selection.to_dict(),
        'source_binding': tuple(str(value) if isinstance(value, TypedId) else value for value in source.binding),
        'binding_generation': source.resolved.binding_generation,
        'authority_revision': source.authority.revision, 'authority_kind': source.authority.kind,
        'verified_caller': session._caller, 'schema_fingerprint': source.schema_fingerprint,
        'catalog_fingerprint': session._catalog_fingerprint, 'reader_contract_version': READER_CONTRACT_VERSION}
    # Source-selected strings are trusted HOST data but remain budgeted.
    with context.scratch(size=2 * _bytes_bound(identity)):
        return document_digest(identity)


def _coverage(include, boundaries, end, total):
    relations = {'producer_execution': {'state': 'complete', 'witness': 'verified_at_cut'}}
    for name, (start, stop) in zip(('start_inputs', 'claims'), boundaries):
        relations[name] = ({'state': 'complete' if start == stop or end >= stop else 'partial',
            'witness': 'verified_at_cut'} if name in include else {'state': 'not_in_profile'})
    relations.update((name, {'state': 'not_in_profile'}) for name in _UNSUPPORTED)
    return {'scope': 'authorized_root_at_cut', 'state': 'partial' if end < total else 'complete',
        'relations': relations}


def _token(session, fingerprint, cut, offset):
    if not hasattr(session, '_cursor_secret'):
        session._cursor_secret = secrets.token_hex(32)
    return 'cursor_' + document_digest({'kind': _OriginQuery.kind, 'secret': session._cursor_secret,
        'session_id': session.session_id, 'fingerprint': fingerprint, 'source_cut': cut, 'offset': offset})


def _envelope(data, rows, end, continuation):
    return {'schema_version': PRODUCT_ORIGIN_PAGE_SCHEMA, 'profile': PRODUCT_ORIGIN_PROFILE,
        'contract_revision': PRODUCT_ORIGIN_CONTRACT_REVISION, 'root': data['root'],
        'source_cut': data['source_cut'], 'access_revision': data['access_revision'],
        'root_proof': data['root_proof'], 'rows': rows,
        'coverage': _coverage(data['include'], data['boundaries'], end, len(data['rows'])),
        'continuation': continuation}


def _select_page(session, fingerprint, data, offset, context):
    total = len(data['rows'])
    selected, token = offset, None
    row_bytes = 0
    # Envelope/coverage/token planning is fixed-size scratch, independent of
    # the retained row body. One exact row length is already prepaid per row.
    with context.scratch(size=4096 + 2 * (_bytes_bound(data['root']) + _bytes_bound(data['source_cut'])
            + _bytes_bound(data['root_proof']) + _bytes_bound(data['access_revision'])
            + _bytes_bound(_coverage(data['include'], data['boundaries'], offset, total)))):
        if offset == total:
            candidate = _envelope(data, [], total, None)
            if _canonical_size(candidate) > session.limits.max_response_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            return total, None
        for end in range(offset + 1, min(total, offset + data['page_size']) + 1):
            row_bytes += data['row_bytes'][end - 1]
            proposed = _token(session, fingerprint, data['source_cut'], end) if end < total else None
            candidate = _envelope(data, [], end, proposed)
            size = _canonical_size(candidate) + row_bytes + end - offset - 1
            if size > session.limits.max_response_bytes:
                break
            selected, token = end, proposed
        if selected == offset:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
    return selected, token


def _completed_data(root, cut, source, include, page_size, result, context):
    rows = result['rows']
    context.reserve(size=4096 + 64 * len(rows) + _bytes_bound(root.to_dict()) + _bytes_bound(cut.to_dict()))
    starts = sum(row['role'] == 'start_input' for row in rows)
    # The accepted evaluator has already fixed and validated all row shapes.
    return {'root': root.to_dict(), 'source_cut': cut.to_dict(), 'access_revision': source.authority.revision,
        'include': include, 'page_size': page_size, 'root_proof': result['root_proof'], 'rows': rows,
        'boundaries': ((0, starts), (starts, len(rows))), 'row_bytes': tuple(_canonical_size(row) for row in rows)}


def _detach_state(fingerprint, data, context):
    bound = _bytes_bound(data) + _bytes_bound(fingerprint) + 256
    # Separate state, encoding and parse/copy allocations coexist with the
    # original evaluator result. Only the exact tagged state charge transfers.
    context.reserve(size=3 * bound)
    encoded = canonical_json(data)
    detached = json.loads(encoded)
    state_size = len(encoded) + len(fingerprint) + 256
    state = _OriginQuery(fingerprint, detached, state_size)
    del encoded, detached
    context.reservation.release(3 * bound - state_size)
    return state


def _stage(owner, fingerprint, data, end, token, state):
    session, context = owner.session, owner.context
    owner.check_pins()
    existing = session._cursors.get(token)
    if existing is not None:
        resident, offset = existing
        if (getattr(resident, 'kind', None) != _OriginQuery.kind or resident.fingerprint != fingerprint
                or offset != end):
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        owner.pin(resident, token, existing)
        return resident
    if len(session._cursors) >= session.limits.max_cursors:
        raise RegistryReadSessionError('LIMIT_EXCEEDED')
    new_state = state is None
    if new_state:
        state = _detach_state(fingerprint, data, context)
    # Compact-state detachment also serializes protected data. Recheck pins
    # at the last pre-insertion point, after every such allocation/copy.
    owner.check_pins()
    amount = _cursor_record_bytes(token, end)
    context.reserve(size=amount)
    record = (state, end)
    # No callbacks or source reads between insertion and the tagged transfer.
    owner.created.append((token, record))
    session._cursors[token] = record
    owner.pin(state, token, record)
    context.reservation.release(amount + (state.budget_bytes if new_state else 0))
    context.reserve()
    return state


def query_product_origin_v1(session, root, at_cut, include=None, page_size=None, cursor=None):
    """Private implementation of the public session method, with safe disposal."""
    try:
        return _query_impl(session, root, at_cut, include, page_size, cursor)
    except Exception as exc:
        code = _safe_code(exc)
        _clear_exception_frames(exc)
    raise RegistryReadSessionError(code) from None


def _query_impl(session, root, at_cut, include, page_size, cursor):
    include = _normalize_include(include)
    resource = _root_shape(root)
    maximum = min(100, session.limits.max_page_size)
    page_size = min(20, maximum) if page_size is None else page_size
    if type(page_size) is not int or page_size < 1:
        raise RegistryReadSessionError('INVALID_QUERY')
    if page_size > maximum:
        raise RegistryReadSessionError('LIMIT_EXCEEDED')
    session._alive()
    record = None
    if cursor is not None:
        if type(cursor) is not str or cursor not in session._cursors:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        if getattr(session._cursors[cursor][0], 'kind', None) != _OriginQuery.kind:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
    cut = session.validate_cut(at_cut)
    if root.source_id != cut.source_id:
        raise RegistryReadSessionError('INVALID_ROOT')
    source = session._sources[cut.source_id]
    _preauthorize(session, source, resource, include)
    if cursor is not None:
        record = session._cursors.get(cursor)
        if record is None or getattr(record[0], 'kind', None) != _OriginQuery.kind:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
    owner = None
    try:
        owner = _OriginDelivery(session, session._cuts[cut.cut_id][1])
        context = owner.context
        try:
            fingerprint = _fingerprint(session, source, cut, root, include, page_size, context)
            state = None
            offset = 0
            if record is not None:
                state, offset = record
                owner.pin(state, cursor, record)
                if state.fingerprint != fingerprint:
                    raise RegistryReadSessionError('CURSOR_MISMATCH')
                data = state.data
            else:
                verifier = _CoreProof(session, cut, source, context)
                result = _evaluate_origin_includes(verifier, root, resource, include)
                data = _completed_data(root, cut, source, include, page_size, result, context)
            end, continuation = _select_page(session, fingerprint, data, offset, context)
            if continuation is not None and continuation in session._cursors:
                # Resolve an existing next-page identity before copying the
                # response, without overwriting it or allocating a duplicate.
                resident_record = session._cursors[continuation]
                resident, resident_offset = resident_record
                if (getattr(resident, 'kind', None) != _OriginQuery.kind
                        or resident.fingerprint != fingerprint or resident_offset != end):
                    raise RegistryReadSessionError('CURSOR_MISMATCH')
                owner.pin(resident, continuation, resident_record)
                state, data = resident, resident.data
            context.reserve(size=4096 + 3 * (_bytes_bound(data['root']) + _bytes_bound(data['source_cut'])
                + _bytes_bound(data['root_proof']) + _bytes_bound(data['access_revision'])
                + _bytes_bound(_coverage(data['include'], data['boundaries'], end, len(data['rows'])))
                + _bytes_bound(continuation)
                + sum(_bytes_bound(data['rows'][index]) for index in range(offset, end))))
            page = _envelope(data, data['rows'][offset:end], end, continuation)
            encoded = canonical_json(page)
            if len(encoded) > session.limits.max_response_bytes:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            response = json.loads(encoded)
            del encoded, page
            if continuation is not None:
                state = _stage(owner, fingerprint, data, end, continuation, state)
            owner.check()
            owner.check_pins()
            return response
        except Exception as exc:
            if getattr(exc, 'code', None) == 'NOT_PRESENT_AT_CUT':
                version = str(root.ref.resource_version_id if resource else root.ref.version_id)
                if version in context.snapshot.objects:
                    exc = RegistryReadSessionError('INTEGRITY_FAILED')
            owner.fail(exc)
    finally:
        if owner is not None:
            owner.close()
