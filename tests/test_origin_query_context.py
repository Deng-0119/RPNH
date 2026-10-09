"""Focused query-owned reservation seams; deterministic local Registry fixtures."""
from dataclasses import replace

import pytest

from cpn.rpnh.collaboration import registry_read_session as sessions
from cpn.rpnh.collaboration import registry_typed_readers as readers
from cpn.rpnh.collaboration._origin_query_context import _OriginQueryContext
from cpn.rpnh.collaboration.registry_read_contracts import (
    IndexQuery, TypedIndexClause, RegistryReadSessionError)
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_registry_read_session import world, issue, session, counts, publish, query
from test_snapshot_producer_proof import foundation


def context_for(reader, cut):
    return _OriginQueryContext(reader, reader._cuts[cut.cut_id][1])


def test_query_copy_shares_capture_and_owns_actual_cached_results(world):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        snapshot = reader._cuts[cut.cut_id][1]
        context = context_for(reader, cut)
        assert context.snapshot is not snapshot
        assert snapshot._origin_context is None
        assert context.snapshot.objects is snapshot.objects
        assert context.snapshot.events is snapshot.events
        calls = []
        def load():
            calls.append(True)
            return None
        assert context.cached('test', ('exact',), 'weak/v1', load) is None
        work = context.reservation.work
        assert context.cached('test', ('exact',), 'weak/v1', load) is None
        assert len(calls) == 1 and context.reservation.work == work
        context.cached('test', ('exact',), 'strong/v1', load)
        assert len(calls) == 2 and context.reservation.work == work + 1
        context.close()
        assert not context._cache and context._event_index is None
        assert context.reservation.scratch_bytes == 0


def test_retained_cursor_totals_are_stored_deduplicated_without_reserializing(world, monkeypatch):
    with session(world, issue(world)) as reader:
        page = reader.query_index(query(world, 1))
        reader.query_index(query(world, 1), cursor=page['continuation'])
        assert len(reader._cursors) == 2
        first = next(iter(reader._cursors.values()))[0]
        assert all(item.entries is first.entries for item, _ in reader._cursors.values())
        expected_entries = sum(len(canonical_json(entry)) for entry in first.entries)
        assert first.entry_bytes_by_source == {world[-1]: expected_entries}
        expected = sum(snapshot.budget_bytes for _, snapshot in reader._cuts.values()) + expected_entries
        monkeypatch.setattr(sessions, 'canonical_json', lambda *_: pytest.fail('retained bytes serialized entries'))
        assert reader._retained_bytes() == expected
        cut = next(iter(reader._cuts.values()))[0]
        assert context_for(reader, cut).reservation.retained_bytes == expected


def test_metadata_budget_precedes_json_decode(world, monkeypatch):
    ref = publish(world, 'bounded body', 'bounded')
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        context = context_for(reader, cut)
        context.reservation.limits = replace(reader.limits,
            max_scan_bytes=context.reservation.retained_bytes + 1)
        monkeypatch.setattr(readers.json, 'loads', lambda *_: pytest.fail('decoded before reservation'))
        with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
            readers.prepared_at(reader._sources[world[-1]].resolved.core, ref, context.snapshot)


def test_raw_budget_precedes_store_open(world, monkeypatch):
    ref = publish(world, 'bounded body', 'bounded')
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        context = context_for(reader, cut)
        core = reader._sources[world[-1]].resolved.core
        readers.prepared_at(core, ref, context.snapshot)
        context.reservation.limits = replace(reader.limits,
            max_scan_bytes=context.reservation.retained_bytes + context.reservation.scratch_bytes + 1)
        monkeypatch.setattr(core.object_store, 'path_for_version', lambda *_: pytest.fail('opened before reservation'))
        with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
            readers._payload_at(core, ref, context.snapshot)


def test_projection_budget_precedes_deepcopy(world, monkeypatch):
    ref = publish(world, 'bounded body', 'bounded')
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        context = context_for(reader, cut)
        core = reader._sources[world[-1]].resolved.core
        readers.prepared_at(core, ref, context.snapshot)
        context.reservation.limits = replace(reader.limits,
            max_scan_bytes=context.reservation.retained_bytes + context.reservation.scratch_bytes + 1)
        monkeypatch.setattr(readers, 'deepcopy', lambda *_: pytest.fail('copied before reservation'))
        with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
            reader._catalog.read_exact(core, ref, snapshot=context.snapshot, projection=('summary',))


def test_descriptor_cache_reuses_actual_implicit_dependency_validation(foundation):
    world, _, _, reader, cut, _, _, _, producer = foundation
    context = context_for(reader, cut)
    core = reader._sources[world[-1]].resolved.core
    ref = SourceQualifiedVersionRef(world[-1], producer)
    body = readers.descriptor_at(core, ref, context.snapshot)
    charged = context.reservation.work
    assert charged > 1  # prepared, raw, equality, and limited owner/net checks
    assert readers.descriptor_at(core, ref, context.snapshot) is body
    assert context.reservation.work == charged
    # Projection enrichment must not mutate the retained descriptor result.
    reader._catalog.read_exact(core, ref, snapshot=context.snapshot, projection=('invocation_ref',))
    assert 'commit_ordinal' not in body


def test_event_index_walks_history_once_and_is_retained(foundation):
    world, _, _, reader, cut, snapshot, _, _, _ = foundation
    class Counted(tuple):
        passes = 0
        def __iter__(self):
            self.passes += 1
            return super().__iter__()
    events = Counted(snapshot.events)
    context = _OriginQueryContext(reader, replace(snapshot, events=events))
    assert context.events_by_id
    retained = context.reservation.scratch_bytes
    assert context.commits_by_transaction
    assert context.events_labelled('object_version_published/v1')
    for event in context.events_labelled('marking_checkpoint_committed/v1'):
        assert event in context.checkpoint_events(event.payload['checkpoint_ref'])
    assert events.passes == 1
    assert context.reservation.scratch_bytes == retained


def test_checkpoint_arrays_and_events_are_real_cached_work(foundation):
    world, _, _, reader, cut, snapshot, _, _, _ = foundation
    context = context_for(reader, cut)
    core = reader._sources[world[-1]].resolved.core
    event = context.events_labelled('marking_checkpoint_committed/v1')[-1]
    ref = readers.qualify(world[-1], event.payload['checkpoint_ref'])
    body = readers.descriptor_at(core, ref, context.snapshot)
    before = context.reservation.work
    ordinal = readers._checkpoint_commit_ordinal(body, context.snapshot)
    assert context.reservation.work > before
    after = context.reservation.work
    assert readers._checkpoint_commit_ordinal(body, context.snapshot) == ordinal
    assert context.reservation.work == after


def test_complete_collector_uses_existing_callbacks_and_no_cursor(world):
    observed = []
    class Catalog(readers.TypedReaderCatalog):
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            observed.append(snapshot._origin_context)
            return super().read_index(core, entry_ref, snapshot=snapshot, projection=projection)
    with session(world, issue(world), reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        spec = replace(query(world, 1), cuts={world[-1]: cut})
        plain = reader._collect_index(spec)
        context = context_for(reader, cut)
        before = counts(world)
        result = reader._collect_index(spec, _origin_context=context)
        assert result.entries == plain.entries
        assert result.entry_bytes_by_source == plain.entry_bytes_by_source
        assert not result.failures and not reader._cursors
        assert observed.count(context) == len(result.entries)
        assert context.reservation.work >= len(result.entries)
        assert counts(world) == before


def test_collector_failure_drops_live_totals(world):
    class Catalog(readers.TypedReaderCatalog):
        seen = 0
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            self.seen += 1
            if self.seen == 2:
                raise readers.TypedReadError('INTEGRITY_FAILED')
            return super().read_index(core, entry_ref, snapshot=snapshot, projection=projection)
    with session(world, issue(world), reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        result = reader._collect_index(replace(query(world), cuts={world[-1]: cut}),
            _origin_context=context_for(reader, cut))
        assert not result.entries and not result.entry_bytes_by_source
        assert result.failures == {world[-1]: 'INTEGRITY_FAILED'}


def test_collector_limit_is_error_not_readable_empty(world):
    class Catalog(readers.TypedReaderCatalog):
        def read_index(self, core, entry_ref, *, snapshot, projection=None):
            raise readers.TypedReadError('LIMIT_EXCEEDED')
    with session(world, issue(world), reader_catalog=Catalog()) as reader:
        cut = reader.capture_cut(world[-1])
        with pytest.raises(RegistryReadSessionError, match='limit exceeded'):
            reader._collect_index(replace(query(world), cuts={world[-1]: cut}),
                _origin_context=context_for(reader, cut))


def test_origin_fields_are_explicit_finite_and_six_new_defaults_self_only():
    catalog = readers.TypedReaderCatalog()
    for entry_type, fields in readers._ORIGIN_FIELDS.items():
        expected = (entry_type.split('/')[0] + '_ref',)
        assert catalog.fields(entry_type) == fields
        assert catalog.default_record_projection(entry_type) == expected
        assert catalog.default_index_projection(entry_type) == expected
        assert 'commit_ordinal' not in fields
    assert len(readers._ORIGIN_FIELDS) == 6 and 'marking_delta/v1' in catalog.entry_types
    from cpn.rpnh.collaboration._origin_core_contract import START_FIELDS
    assert {field for field in catalog.fields('transition_firing/v1') if field.startswith('start_')} == set(START_FIELDS)
    assert 'claimed_input_refs' in catalog.fields('transition_firing/v1')
    assert catalog.default_record_projection('resource_version/v1') == (
        'resource_id', 'resource_version_id', 'media_type', 'content_schema_ref',
        'byte_count', 'summary', 'commit_ordinal')


def test_event_schema_labels_preserve_mismatched_candidates(foundation):
    _, _, _, reader, _, snapshot, _, _, _ = foundation
    original = next(event for event in snapshot.events
        if event.event_type == 'marking_checkpoint_committed/v1')
    damaged = replace(original, event_type='transition_firing_settled/v1')
    context = _OriginQueryContext(reader, replace(snapshot, events=(damaged,)))
    assert context.events_labelled('marking_checkpoint_committed/v1') == (damaged,)
    assert context.events_labelled('transition_firing_settled/v1') == (damaged,)
    assert context.checkpoint_events(damaged.payload['checkpoint_ref']) == (damaged,)


def test_candidate_batch_charges_rejected_late_candidate_before_first_validation(foundation):
    _, _, _, reader, cut, snapshot, _, _, _ = foundation
    context = context_for(reader, cut)
    candidates = snapshot.events[:2]
    before = context.reservation.work
    def reject(_event):
        assert context.reservation.work == before + 2
        raise RegistryReadSessionError('INTEGRITY_FAILED')
    with pytest.raises(RegistryReadSessionError, match='integrity failed'):
        context.validate_events(candidates, 'test-all-candidates/v1', reject)
    assert not context._cache


def test_callback_retained_growth_refreshes_next_allocation(world):
    with session(world, issue(world)) as reader:
        first = reader.capture_cut(world[-1])
        context = context_for(reader, first)
        baseline = context.reservation.retained_bytes
        second = reader.capture_cut(world[-1])
        assert second != first
        context.reserve(size=1)
        assert context.reservation.retained_bytes == reader._retained_bytes() > baseline


def test_semantic_array_whole_result_reused_and_every_position_charged(world):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        context = context_for(reader, cut)
        values = ['same', 'same', 'same']
        before = context.reservation.work
        calls = []
        def check(actual):
            calls.append(tuple(actual))
            return True
        assert context.semantic_array(values, 'A', 'positions/v1', check) is True
        assert context.reservation.work == before + 3
        assert context.semantic_array(values, 'A', 'positions/v1', check) is True
        assert len(calls) == 1 and context.reservation.work == before + 3


def test_core_contract_contents_change_catalog_fingerprint(monkeypatch):
    from cpn.rpnh.collaboration import _origin_core_contract as contract
    catalog = readers.TypedReaderCatalog()
    before = catalog.fingerprint()
    monkeypatch.setattr(contract, 'CORE_DEPENDENCIES', (*contract.CORE_DEPENDENCIES,
        ('test-only', 'new actual fixed dependency predicate')))
    assert catalog.fingerprint() != before


def test_selected_completion_ref_kind_rejects_schema_legal_wrong_kind(foundation, monkeypatch):
    from cpn.rpnh.registry.identities import new_id
    world, _, _, reader, cut, snapshot, _, _, _ = foundation
    context = context_for(reader, cut)
    core = reader._sources[world[-1]].resolved.core
    row = next(row for row in snapshot.objects.values() if row['object_type'] == 'firing_completion/v2')
    ref = readers.qualify(world[-1], {key: row[source] for key, source in
        (('entity_type', 'object_type'), ('logical_id', 'logical_id'), ('version_id', 'version_id'))})
    body = dict(readers.descriptor_at(core, ref, context.snapshot))
    body['invocation_ref'] = {**body['invocation_ref'], 'version_id': str(new_id('task_version'))}
    # This is legal under the existing generic-ref JSON schema. The newly
    # exposed finite field must enforce its actual invocation_version kind.
    core.catalog.validate_instance('firing_completion/v2', category='object', instance=body)
    monkeypatch.setattr(readers, 'descriptor_at', lambda *_: body)
    with pytest.raises(readers.TypedReadError, match='INTEGRITY_FAILED'):
        reader._catalog.read_index(core, ref, snapshot=context.snapshot,
            projection=('transition_firing_ref', 'invocation_ref'))


def test_result_projection_without_output_field_does_not_expand_outputs(foundation, monkeypatch):
    world, _, _, reader, cut, snapshot, _, _, _ = foundation
    context = context_for(reader, cut)
    core = reader._sources[world[-1]].resolved.core
    row = next(row for row in snapshot.objects.values() if row['object_type'] == 'operation_result/v1')
    ref = readers.qualify(world[-1], {key: row[source] for key, source in
        (('entity_type', 'object_type'), ('logical_id', 'logical_id'), ('version_id', 'version_id'))})
    original = readers.descriptor_at(core, ref, context.snapshot)
    body = dict(original)
    # A plain JSON value isolates semantic type validation from the existing
    # generic-ref schema validation. Unselected outputs must remain unexpanded.
    body['output_resource_refs'] = [{'entity_type': 'task/v1',
        'logical_id': body['invocation_ref']['logical_id'],
        'version_id': body['invocation_ref']['version_id']}]
    monkeypatch.setattr(readers, 'descriptor_at', lambda *_: body)
    result = reader._catalog.read_exact(core, ref, snapshot=context.snapshot,
        projection=('operation_result_ref', 'invocation_ref', 'transition_firing_ref', 'business_outcome'))
    assert 'output_resource_refs' not in result
    with pytest.raises(readers.TypedReadError, match='INTEGRITY_FAILED'):
        reader._catalog.read_exact(core, ref, snapshot=context.snapshot, projection=('output_resource_refs',))


def test_selected_output_array_validation_reuses_actual_result(foundation):
    world, _, _, reader, cut, snapshot, _, _, _ = foundation
    context = context_for(reader, cut)
    core = reader._sources[world[-1]].resolved.core
    row = next(row for row in snapshot.objects.values() if row['object_type'] == 'operation_result/v1')
    ref = readers.qualify(world[-1], {key: row[source] for key, source in
        (('entity_type', 'object_type'), ('logical_id', 'logical_id'), ('version_id', 'version_id'))})
    first = reader._catalog.read_exact(core, ref, snapshot=context.snapshot, projection=('output_resource_refs',))
    work = context.reservation.work
    second = reader._catalog.read_exact(core, ref, snapshot=context.snapshot, projection=('output_resource_refs',))
    assert first == second and context.reservation.work == work


def test_failed_scratch_is_held_through_error_delivery(world):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        context = context_for(reader, cut)
        before = context.reservation.scratch_bytes
        with pytest.raises(ValueError):
            with context.scratch(size=1234):
                retained_by_traceback = bytearray(8)
                raise ValueError('synthetic validation failure')
        assert context.reservation.scratch_bytes == before + 1234
        context.close()
        assert context.reservation.scratch_bytes == 0


def test_failed_contextual_foundation_keeps_local_scratch(foundation):
    import json
    from cpn.rpnh.collaboration._snapshot_producer_proof import _canonical_snapshot_producer
    _, _, _, reader, _, snapshot, row, resource, producer = foundation
    relations = tuple({**relation, 'strength': 'weak'} if relation['relation_type'] == 'produced_by'
        and json.loads(relation['source_json']).get('version_id') == row['version_id'] else relation
        for relation in snapshot.relations)
    context = _OriginQueryContext(reader, replace(snapshot, relations=relations))
    context.events_by_id
    before = context.reservation.scratch_bytes
    with pytest.raises(RegistryReadSessionError, match='integrity failed'):
        _canonical_snapshot_producer(context.snapshot, resource, producer, context.reservation)
    assert context.reservation.scratch_bytes > before
    context.close()
    assert context.reservation.scratch_bytes == 0
