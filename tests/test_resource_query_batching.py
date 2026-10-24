"""Differential read projection tests, with deterministic offline authority fakes.

Raw ledgers isolate projection/cursor behavior, not admission validation. The
existing source-query integration covers real Start/products/Success authority.
"""
from __future__ import annotations

from dataclasses import fields, replace
import json
import random
from types import MethodType, SimpleNamespace

import pytest

from cpn.rpnh.registry._resource_service import queries
from cpn.rpnh.registry.errors import (
    MissingResourceAddress, ResourceSchemaViolation, StaleAuthorityHead,
    StaleQueryContext, UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.invocations import InvocationContext
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import (
    _content_schema_source_from_payload, _ref_payload, _version_from_payload,
)
from cpn.rpnh.registry.resources import (
    RelationFilter, ResourceAddress, ResourceHeader, ResourceQuery,
    ResourceQueryResult, ResourceVersionRef, SafeDescriptor,
)
from test_canonical_head_projection import (
    add_event, inject_before_query, ledger, mark_firing, reference_heads,
)


def test_seeded_exact_cut_differential_across_batch_boundary(ledger):
    store, task, kernel = ledger
    rng = random.Random(4337)
    records = {}
    other = new_id('task')
    for ordinal in range(1, 306):
        records[ordinal] = add_event(
            store, other if ordinal % 9 == 0 else task, ordinal,
            stream=f'stream-{rng.randrange(7)}', sequence=1000 - ordinal,
            control=rng.randrange(100) if ordinal % 3 == 0 else None,
            event_type='transaction_committed/v1' if ordinal in (101, 202, 303)
            else 'fixture/v1')
    for ordinal in range(2, 68, 3):
        event, transaction = records[ordinal]
        publication = records[(101, 202, 303)[(ordinal // 3) % 3]][1]
        mark_firing(store, event, transaction, publication_tx=publication)
    for ordinal in range(70, 90, 3):
        mark_firing(store, *records[ordinal])
    # Two memberships must both permit the event, not just the earliest one.
    mark_firing(store, *records[5], publication_tx=records[303][1])
    with store.connect() as db:
        hidden = mark_firing(store, *records[91])
        db.execute("UPDATE firing_publications SET state='SUPERSEDED', "
                   "published_transaction_id=?,marking_checkpoint_version_id='successor' "
                   "WHERE firing_version_id=?", (str(records[303][1]), hidden))
    valid = [cut for cut in range(306)
             if cut == 0 or store.canonical_events(through_ordinal=cut)[-1].ordinal == cut]
    rng.shuffle(valid)
    cuts = (None, *valid, 0, None, valid[0])
    actual = queries._canonical_heads(kernel, cuts)
    assert len(actual) > 256
    assert actual == reference_heads(store, task, cuts)
    assert list(actual) == list(reference_heads(store, task, cuts))
    for cut in (2, 5, 70, 91):
        with pytest.raises(StaleAuthorityHead, match='snapshot changed'):
            queries._canonical_heads(kernel, (1, cut, 303))


def test_promoted_older_stream_event_cannot_replace_newer_event(ledger):
    store, task, kernel = ledger
    event, transaction = add_event(store, task, 1, stream='same', sequence=99, control=8)
    add_event(store, task, 2, stream='same', sequence=3, control=2)
    _, publication = add_event(store, task, 3, event_type='transaction_committed/v1')
    mark_firing(store, event, transaction, publication_tx=publication)
    actual = queries._canonical_heads(kernel, (3, 2, 0))
    assert actual == reference_heads(store, task, (3, 2, 0))
    assert actual[3].stream_heads['same'] == 3
    assert actual[2].task_control_sequence == 2
    assert actual[3].task_control_sequence == 8


def test_batch_read_snapshot_excludes_mid_scan_append(ledger, monkeypatch):
    store, task, kernel = ledger
    for ordinal in range(1, 260):
        add_event(store, task, ordinal)
    expected = reference_heads(store, task, range(260))
    fired = inject_before_query(
        store, monkeypatch, 'WITH requested(ordinal)',
        lambda: add_event(store, task, 260, stream='new'))
    assert queries._canonical_heads(kernel, range(260)) == expected
    assert fired()
    assert queries._canonical_heads(kernel, (None,))[260].stream_heads['new'] == 260



def test_orphan_membership_keeps_original_join_semantics(ledger):
    store, task, kernel = ledger
    event, transaction = add_event(store, task, 1)
    add_event(store, task, 2)
    with store.connect() as db:
        # Deliberately malformed fixture: both old and new predicates join p.
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('INSERT INTO firing_temporary_members VALUES (?,?,?,?)',
                   ('missing-publication', 'event', str(event), str(transaction)))
    assert queries._canonical_heads(kernel, (2, 1, 0)) == reference_heads(store, task, (2, 1, 0))

def query_fixture(ledger, *, count=140):
    """Actual canonical SQL and paging with explicitly fake request authority."""
    store, task, _kernel = ledger
    task_ref = VersionRef('task/v1', task, new_id('task_version'))
    producer = VersionRef('invocation/v1', new_id('invocation'), new_id('invocation_version'))
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    context = InvocationContext(**{
        field.name: task_ref if field.name == 'task_ref' else principal
        for field in fields(InvocationContext)})
    context = replace(context, principal_ref=principal, operation_binding_ref=producer,
                      invocation_ref=producer, origin='petri_operation')
    prepared, ordinals = {}, {}
    repeated_resource = new_id('resource')
    for ordinal in range(1, count + 1):
        event, transaction = add_event(store, task, ordinal)
        ref = ResourceVersionRef(repeated_resource if ordinal <= 2 else new_id('resource'),
                                 new_id('resource_version'))
        metadata = dict(task_ref=_ref_payload(task_ref), round_ref=None, net_ref=None,
                        producer_ref=_ref_payload(producer), origin_kind='private_system',
                        descriptors={'group': [ordinal % 4]}, summary=f'item-{ordinal}')
        media_type = 'text/plain' if ordinal % 3 else 'application/json'
        prepared[ref] = SimpleNamespace(metadata=metadata, media_type=media_type, size=ordinal)
        ordinals[ref] = ordinal
        with store.connect() as db:
            db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (str(ref.resource_id), str(ref.resource_version_id), 'resource_version/v1',
                        ordinal, media_type, 'fixture', None, 'fixture', json.dumps(metadata),
                        str(transaction), str(event)))
    refs = sorted(prepared, key=lambda ref: str(ref.resource_version_id))
    with store.connect() as db:
        first = db.execute('SELECT transaction_id,event_id FROM events WHERE ordinal=1').fetchone()
        db.execute('INSERT INTO relations VALUES (?,?,?,?,?,?,?,?)',
                   (str(new_id('relation')), 'derived_from',
                    json.dumps(_ref_payload(refs[1].as_version_ref())),
                    json.dumps(_ref_payload(refs[4].as_version_ref())), 'weak', '{}',
                    first['transaction_id'], first['event_id']))
    state = dict(revision=1, expired=False, allowed=set(refs), authorized=[], hydrated=[])
    kernel = SimpleNamespace(_ResourceServiceKernel__core=SimpleNamespace(event_store=store, task_id=task))
    for name in ('_canonical_head', '_canonical_heads', '_validate_query_head', '_validate_cursor',
                 '_query_material', '_cursor_for', '_visible_refs', '_header'):
        value = getattr(queries, name)
        setattr(kernel, name, value if name == '_query_material' else MethodType(value, kernel))
    def authority(_context, *args, **kwargs):
        if state['expired']:
            raise StaleQueryContext('synthetic authority expired')
        return principal, producer
    def authorize(_context, _binding, ref, **kwargs):
        state['authorized'].append(ref)
        if ref not in state['allowed']:
            raise UnauthorizedResourceDelivery('synthetic denied candidate')
    def prepare(ref, **kwargs):
        state['hydrated'].append(ref)
        return prepared[ref]
    kernel._query_authority = authority
    kernel._authority_facts = lambda _context: {'revision': state['revision'], 'writer': store.writer_epoch}
    kernel._exact_object = lambda *args, **kwargs: SimpleNamespace(metadata={})
    kernel._authorize_resource = authorize
    kernel._canonical_prepared = prepare
    kernel._canonical_published_ordinal = lambda version, **kwargs: next(
        ordinal for ref, ordinal in ordinals.items() if ref.resource_version_id == version)
    kernel._resolve_address_unchecked = lambda address, head: SimpleNamespace(resource_ref=refs[-1])
    return kernel, context, ResourceQuery(task_ref=task_ref, scope_refs=(), limit=37), state, refs


def legacy_result(kernel, context, request):
    """Run frozen prior query/header with the pre-batch head reference."""
    original_header, original_heads = kernel._header, kernel._canonical_heads
    core = kernel._ResourceServiceKernel__core
    try:
        kernel._header = MethodType(legacy_header, kernel)
        kernel._canonical_heads = lambda cuts: reference_heads(core.event_store, core.task_id, cuts)
        return legacy_query(kernel, context, request)
    finally:
        kernel._header, kernel._canonical_heads = original_header, original_heads


def test_query_headers_filters_and_pages_match_prior_implementation(ledger):
    kernel, context, request, state, refs = query_fixture(ledger)
    for changes in ({}, {'media_types': ('text/plain',)},
                    {'producer_refs': (context.invocation_ref,)},
                    {'scope_refs': (context.task_ref,)},
                    {'relation_filters': (RelationFilter('absent'),)},
                    {'relation_filters': (RelationFilter('derived_from'),)},
                    {'scope_refs': (context.task_ref,), 'address_name': 'result'}):
        query = replace(request, **changes)
        actual = queries.query(kernel, context, query)
        assert actual == legacy_result(kernel, context, query)
        if actual.cursor:
            next_query = replace(query, cursor=actual.cursor)
            assert queries.query(kernel, context, next_query) == legacy_result(kernel, context, next_query)
    page = queries.query(kernel, context, request)
    collected = list(page.headers)
    while page.cursor:
        page = queries.query(kernel, context, replace(request, cursor=page.cursor))
        collected.extend(page.headers)
    assert [header.ref for header in collected] == refs
    assert len(page.headers) == 29 and page.cursor is None
    assert len({header.published_at_head.ordinal for header in collected}) == len(refs)


def test_denied_candidates_do_not_shorten_pages_or_enter_headers(ledger):
    kernel, context, request, state, refs = query_fixture(ledger, count=34)
    state['allowed'] = set(refs[3::4])
    request = replace(request, limit=3)
    first = queries.query(kernel, context, request)
    assert first == legacy_result(kernel, context, request)
    assert len(first.headers) == 3
    assert {header.ref for header in first.headers} <= state['allowed']
    assert set(state['authorized']) == set(refs)
    assert set(state['hydrated']) <= state['allowed']
    page, returned = first, list(first.headers)
    while page.cursor:
        request = replace(request, cursor=page.cursor)
        page = queries.query(kernel, context, request)
        assert page == legacy_result(kernel, context, request)
        returned.extend(page.headers)
    assert [header.ref for header in returned] == refs[3::4]
    assert len(page.headers) == 2


def test_current_authority_rechecked_and_fixed_cut_stays_stable(ledger):
    kernel, context, request, state, refs = query_fixture(ledger, count=8)
    request = replace(request, limit=2)
    first = queries.query(kernel, context, request)
    core = kernel._ResourceServiceKernel__core
    add_event(core.event_store, core.task_id, 9, stream='after-cut')
    next_query = replace(request, cursor=first.cursor)
    second = queries.query(kernel, context, next_query)
    assert second == legacy_result(kernel, context, next_query)
    assert second.through_head == first.through_head and second.lag == 1
    for changed in (replace(next_query, media_types=('text/plain',)),
                    replace(next_query, limit=3)):
        with pytest.raises(StaleQueryContext, match='authority/query'):
            queries.query(kernel, context, changed)
    state['revision'] += 1
    with pytest.raises(StaleQueryContext, match='authority/query'):
        queries.query(kernel, context, next_query)
    state['expired'] = True
    hydrated = len(state['hydrated'])
    with pytest.raises(StaleQueryContext, match='expired'):
        queries.query(kernel, context, next_query)
    assert len(state['hydrated']) == hydrated
    state['expired'] = False
    core.event_store.rotate_writer(expected_epoch=core.event_store.writer_epoch)
    with pytest.raises(StaleQueryContext):
        queries.query(kernel, context, replace(request, through_head=first.through_head))


def test_headers_keep_bounded_batches_exact_cuts_and_no_payload_read(ledger):
    kernel, context, request, state, refs = query_fixture(ledger, count=260)
    calls = []
    original = kernel._canonical_heads
    def capture(cuts):
        cuts = tuple(cuts)
        calls.append(cuts)
        return original(cuts)
    kernel._canonical_heads = capture
    head = kernel._canonical_head()
    calls.clear()
    before = kernel._ResourceServiceKernel__core.event_store.max_ordinal()
    actual = tuple(queries._headers(kernel, refs, through_head=head))
    assert list(map(len, calls)) == [128, 128, 4]
    expected = tuple(legacy_header(kernel, ref, through_head=head) for ref in refs)
    assert actual == expected
    assert kernel._ResourceServiceKernel__core.event_store.max_ordinal() == before


# Frozen references from public 9d0331aa; change only if its contract changes.

def legacy_header(self, ref: ResourceVersionRef, *, through_head: RegistryHead) -> ResourceHeader:
    prepared = self._canonical_prepared(
        ref, through_ordinal=through_head.ordinal)
    ordinal = self._canonical_published_ordinal(
        ref.resource_version_id, through_ordinal=through_head.ordinal)
    metadata = dict(prepared.metadata)
    relation_kinds = sorted({
        row["relation_type"]
        for row in self._ResourceServiceKernel__core.event_store.canonical_relation_rows(
            through_ordinal=through_head.ordinal,
            version_id=ref.resource_version_id)
    })
    descriptor_labels = tuple(
        SafeDescriptor(
            str(name), tuple(value) if isinstance(value, list)
            else value if isinstance(value, tuple) else (value,))
        for name, value in sorted(metadata.get("descriptors", {}).items())
    )
    return ResourceHeader(
        ref=ref,
        task_ref=_version_from_payload(metadata["task_ref"]),
        round_ref=(_version_from_payload(metadata["round_ref"])
                   if metadata["round_ref"] else None),
        net_ref=(_version_from_payload(metadata["net_ref"])
                 if metadata["net_ref"] else None),
        producer_ref=_version_from_payload(metadata["producer_ref"]),
        origin_kind=metadata["origin_kind"],
        media_type=prepared.media_type, byte_size=prepared.size,
        content_schema_ref=metadata.get("content_schema_ref"),
        content_schema_authority_ref=(
            _content_schema_source_from_payload(
                metadata.get("content_schema_authority_ref"))),
        display_summary=metadata.get("summary"),
        descriptor_labels=descriptor_labels,
        relation_kinds=tuple(relation_kinds),
        published_at_head=self._canonical_head(ordinal=ordinal))

def legacy_query(
        self, context: InvocationContext | RegistryObserverContext,
        query: ResourceQuery) -> ResourceQueryResult:
    observer_fields = {"headers", "projection_head"}
    if query.relation_filters:
        observer_fields.add("relations")
    if query.address_name is not None:
        observer_fields.add("addresses")
    principal_ref, grant_ref = self._query_authority(
        context, observer_fields=observer_fields)
    if query.limit < 1 or query.limit > 1000:
        raise ResourceSchemaViolation("resource query limit must be between 1 and 1000")
    expected_task = context.task_ref
    if query.task_ref != expected_task or query.task_ref.entity_id != self._ResourceServiceKernel__core.task_id:
        raise UnauthorizedResourceDelivery("resource query cannot cross tasks")
    authority_facts = self._authority_facts(context)
    query_facts = self._query_material(query)
    if query.cursor is not None:
        self._validate_cursor(
            query.cursor, query_facts=query_facts,
            principal_ref=principal_ref, grant_ref=grant_ref,
            authority_facts=authority_facts)
        head = query.cursor.through_head
        offset = query.cursor.offset
    else:
        head = query.through_head or self._canonical_head()
        self._validate_query_head(head)
        offset = 0
    visible = self._visible_refs(context, through_ordinal=head.ordinal)
    headers: list[ResourceHeader] = []
    allowed_scopes = set(query.scope_refs)
    allowed_producers = set(query.producer_refs)
    for ref in sorted(visible, key=lambda item: str(item.resource_version_id)):
        header = self._header(ref, through_head=head)
        if allowed_scopes and not ({header.task_ref, header.round_ref, header.net_ref}
                                   & allowed_scopes):
            continue
        if allowed_producers and header.producer_ref not in allowed_producers:
            continue
        if query.media_types and header.media_type not in query.media_types:
            continue
        if query.address_name is not None:
            matched = False
            for scope in query.scope_refs:
                address = ResourceAddress(scope, query.address_name)
                try:
                    resolved = self._resolve_address_unchecked(address, head)
                except MissingResourceAddress:
                    continue
                if resolved.resource_ref == ref:
                    matched = True
                    break
            if not matched:
                continue
        if query.relation_filters:
            relation_types = set(header.relation_kinds)
            if any(item.relation_type not in relation_types
                   for item in query.relation_filters):
                continue
        headers.append(header)
    page = headers[offset:offset + query.limit]
    next_offset = offset + len(page)
    cursor = (self._cursor_for(
        query_facts=query_facts, principal_ref=principal_ref,
        grant_ref=grant_ref, through_head=head, offset=next_offset,
        authority_facts=authority_facts)
              if next_offset < len(headers) else None)
    current = self._canonical_head()
    return ResourceQueryResult(
        tuple(page), cursor, head, current.ordinal,
        max(current.ordinal - head.ordinal, 0))
