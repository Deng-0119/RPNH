"""Frozen legacy disclosure and mechanical full-collection regressions.

No origin query or origin logical/preallocation budget contract is implemented
by this preparatory boundary. All histories are deterministic offline fixtures.
"""
from dataclasses import replace
import pytest

from cpn.rpnh.collaboration import registry_typed_readers as readers
from cpn.rpnh.collaboration.registry_read_session import ExistingReadAuthorityProvider
from cpn.rpnh.collaboration.registry_read_contracts import (
    IndexQuery, TypedIndexClause, TypedPredicate, ReadLimits, RegistryReadSessionError)
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.registry.resources import RegistryObserverContext
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from test_registry_read_session import world, issue, session, counts, publish


# These expected tuples come from the original catalog, not the runtime tables.
_EXPECTED_RECORD_PROJECTIONS = {
    'collaboration_assembly_revision/v1': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v2': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v3': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v4': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v5': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v6': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'operation', 'parent_revision_refs',
        'lineage_root_ref', 'analysis_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v7': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v8': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_assembly_revision/v9': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'parent_revision_ref', 'plan_ref', 'generated_revision_ref',
        'compiled_inventory_ref', 'lowering_mapping_ref', 'commit_ordinal'),
    'collaboration_branch/v1': ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref',
        'fork_base_revision_ref', 'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id',
        'commit_ordinal'),
    'collaboration_branch/v2': ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref',
        'fork_base_revision_ref', 'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id',
        'commit_ordinal'),
    'collaboration_branch/v3': ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref',
        'fork_base_revision_ref', 'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id',
        'commit_ordinal'),
    'collaboration_candidate_plan/v1': ('schema_version', 'plan_ref', 'manifest_ref', 'source_id',
        'command_id', 'owner_task_ref', 'producer_principal_ref', 'author_ref', 'principal_ref',
        'bootstrap_ref', 'task_round_ref', 'schema_refs', 'operation_refs', 'commit_ordinal'),
    'collaboration_candidate_plan/v2': ('schema_version', 'plan_ref', 'manifest_ref', 'source_id',
        'command_id', 'owner_task_ref', 'producer_principal_ref', 'author_ref', 'principal_ref',
        'bootstrap_ref', 'task_round_ref', 'schema_refs', 'operation_refs', 'commit_ordinal'),
    'collaboration_net_revision/v1': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'definition_kind', 'definition_ref', 'parent_revision_refs',
        'selected_change_refs', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref',
        'open_region_contract_ref', 'subnet_provenance_ref', 'commit_ordinal'),
    'collaboration_net_revision/v2': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'definition_kind', 'definition_ref', 'parent_revision_refs',
        'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref',
        'source_contract', 'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref',
        'commit_ordinal'),
    'collaboration_net_revision/v3': ('schema_version', 'revision_ref', 'owner_task_ref',
        'producer_principal_ref', 'command_id', 'definition_kind', 'definition_ref', 'parent_revision_refs',
        'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref',
        'source_contract', 'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref',
        'author_kind', 'author_command_ref', 'resolution_ref', 'commit_ordinal'),
    'collaboration_source_set/v1': ('schema_version', 'source_set_ref', 'owner_task_ref', 'predecessor_ref',
        'sequence', 'command_id', 'members', 'commit_ordinal'),
    'execution_checkpoint/v1': ('execution_checkpoint_ref', 'execution_instance_ref',
        'execution_net_definition_ref', 'sequence', 'previous_checkpoint_ref', 'transition_firing_ref',
        'token_refs', 'active_firing_refs', 'evidence_refs', 'status', 'commit_ordinal'),
    'marking_checkpoint/v1': ('checkpoint_commit_ordinal', 'net_ref', 'marking_checkpoint_ref',
        'net_instance_ref', 'epoch', 'token_refs', 'settled', 'previous_checkpoint_ref',
        'transition_firing_refs', 'commit_ordinal'),
    'native_run_identity/v1': ('run_id', 'run_version_id', 'task_ref', 'task_branch_ref', 'branch_id',
        'protocol_versions', 'commit_ordinal'),
    'net_instance/v1': ('net_instance_ref', 'task_round_ref', 'plan_ref', 'team_design_root_ref',
        'llm_macro_net_ref', 'team_net_declaration_resource_ref', 'executable_transition_binding_refs',
        'node_refs', 'operation_binding_refs', 'output_binding_refs', 'commit_ordinal'),
    'petri_token/v1': ('petri_token_ref', 'net_instance_ref', 'place', 'kind', 'resource_ref', 'epoch',
        'consumed_by', 'token_id', 'commit_ordinal'),
    'resource_version/v1': ('resource_id', 'resource_version_id', 'media_type', 'content_schema_ref',
        'byte_count', 'summary', 'commit_ordinal'),
    'run_terminal_evidence/v1': ('terminal_evidence_ref', 'run_ref', 'terminal_occurrence_ref',
        'run_outcome', 'terminal_result_ref', 'final_result_index_ref', 'final_checkpoint_ref',
        'commit_ordinal'),
}
_EXPECTED_INDEX_EXCLUSIONS = {
    'members', 'token_refs', 'active_firing_refs', 'evidence_refs', 'node_refs',
    'operation_binding_refs', 'output_binding_refs', 'executable_transition_binding_refs'}


@pytest.mark.parametrize('entry_type', tuple(_EXPECTED_RECORD_PROJECTIONS))
def test_all_legacy_defaults_stay_exact_after_catalog_field_expansion(monkeypatch, entry_type):
    catalog = readers.TypedReaderCatalog()
    expected_record = _EXPECTED_RECORD_PROJECTIONS[entry_type]
    expected_index = tuple(field for field in expected_record if field not in _EXPECTED_INDEX_EXCLUSIONS)
    assert set(_EXPECTED_RECORD_PROJECTIONS) <= set(catalog.entry_types)
    assert catalog.default_record_projection(entry_type) == expected_record
    assert catalog.default_index_projection(entry_type) == expected_index
    before = catalog.fingerprint()
    monkeypatch.setitem(readers._FIELDS, entry_type, (*readers._FIELDS[entry_type], 'test_only_extra_field'))
    assert 'test_only_extra_field' in catalog.fields(entry_type)
    assert catalog.fingerprint() != before
    assert catalog.default_record_projection(entry_type) == expected_record
    assert catalog.default_index_projection(entry_type) == expected_index


@pytest.mark.parametrize('table', ['fields', 'default_record_projection', 'default_index_projection'])
def test_fingerprint_covers_actual_field_types_and_ordered_defaults(monkeypatch, table):
    catalog = readers.TypedReaderCatalog()
    before = catalog.fingerprint()
    original = getattr(catalog, table)
    def changed(entry_type):
        value = original(entry_type)
        if entry_type != 'resource_version/v1':
            return value
        return {**value, 'summary': 'nullable_string'} if table == 'fields' else tuple(reversed(value))
    monkeypatch.setattr(catalog, table, changed)
    assert catalog.fingerprint() != before


def test_unknown_type_does_not_get_catalog_wide_defaults(monkeypatch):
    # A catalog-only addition is insufficient: future readers must explicitly
    # declare their reviewed self-ref-only defaults before they can be used.
    catalog = readers.TypedReaderCatalog()
    monkeypatch.setitem(readers._FIELDS, 'test_only_type/v1', ('self_ref', 'nested_refs'))
    for method in (catalog.default_record_projection, catalog.default_index_projection):
        with pytest.raises(readers.TypedReadError, match='UNSUPPORTED_ENTRY_TYPE'):
            method('test_only_type/v1')
    assert 'test_only_type/v1' not in catalog.entry_types


def test_legacy_observer_adapter_freezes_original_seven_fields(world, monkeypatch):
    # Isolate the compatibility adapter after the existing authority verifier;
    # this unit test does not replace canonical grant verification coverage.
    context_v2 = issue(world)
    context = RegistryObserverContext(context_v2.observer_principal_ref, context_v2.observer_profile_ref,
        context_v2.task_ref, context_v2.grant_ref, 1, 0, 'test-only-fence', context_v2.expires_at, 'inspect')
    calls = []
    def verified(_service, actual, *, observer_fields):
        assert actual is context
        calls.append(observer_fields)
    monkeypatch.setattr(_ResourceServiceKernel, '_query_authority', verified)
    monkeypatch.setattr(_ResourceServiceKernel, '_authority_facts', lambda *_: {'test_only': 'verified'})
    catalog = readers.TypedReaderCatalog()
    monkeypatch.setitem(readers._FIELDS, 'resource_version/v1',
        (*readers._FIELDS['resource_version/v1'], 'test_only_extra_field'))
    source_ref = SourceQualifiedVersionRef(world[-1], world[2].task_ref)
    provider = ExistingReadAuthorityProvider({('caller', world[-1], 'local'): context})
    authority = provider.resolve_existing('caller', source_ref, 'local', 'inspect',
        core=world[0], catalog=catalog, limits=ReadLimits())
    expected = {'resource_version/v1': _EXPECTED_RECORD_PROJECTIONS['resource_version/v1']}
    assert dict(authority.scope.index_fields) == dict(authority.scope.record_fields) == expected
    assert calls == [('headers', 'projection_head')]
    assert authority.kind == 'observer_metadata_v1'
    assert authority.scope.material_refs == authority.scope.export_refs == ()


def test_explicit_existing_scope_and_default_reads_survive_field_expansion(world, monkeypatch):
    ref = publish(world, 'header', 'header')
    context = issue(world)
    monkeypatch.setitem(readers._FIELDS, 'resource_version/v1',
        (*readers._FIELDS['resource_version/v1'], 'test_only_extra_field'))
    with session(world, context) as reader:
        cut = reader.capture_cut(world[-1])
        result = reader.read_exact(entry_ref=ref, at_cut=cut)
        assert result['disclosure']['projected_fields'] == list(_EXPECTED_RECORD_PROJECTIONS['resource_version/v1'])
        spec = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1'),), 1, cuts={world[-1]: cut})
        collected = reader._collect_index(spec)
        assert collected.entries and not collected.failures and not reader._cursors
        assert all('test_only_extra_field' not in item['fields'] for item in collected.entries)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.read_exact(entry_ref=ref, at_cut=cut, projection=('test_only_extra_field',))
        assert error.value.code == 'NOT_DISCLOSED'
        denied = replace(spec, clauses=(TypedIndexClause('resource_version/v1', projection=('test_only_extra_field',)),))
        with pytest.raises(RegistryReadSessionError) as error:
            reader._collect_index(denied)
        assert error.value.code == 'NOT_DISCLOSED'


def test_managed_scope_stays_frozen_and_per_object_restricted(tmp_path, monkeypatch):
    from test_source_set_query import _owner
    from cpn.rpnh.registry.publication import _version_from_payload
    import json
    owner, contexts = _owner(tmp_path / 'run', 'managed-source')
    core = owner._core
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    synthetic = (core, owner.schema_gateway, owner.identity, bootstrap, owner.principal_ref, 'managed-source')
    monkeypatch.setitem(readers._FIELDS, 'resource_version/v1',
        (*readers._FIELDS['resource_version/v1'], 'test_only_extra_field'))
    with session(synthetic, contexts['main']) as reader:
        cut = reader.capture_cut(synthetic[-1])
        expected = {'resource_version/v1': _EXPECTED_RECORD_PROJECTIONS['resource_version/v1']}
        authority = reader._sources[synthetic[-1]].authority
        assert authority.kind == 'managed_invocation'
        assert dict(authority.scope.index_fields) == dict(authority.scope.record_fields) == expected
        spec = IndexQuery((synthetic[-1],), (TypedIndexClause('resource_version/v1'),), 1,
            cuts={synthetic[-1]: cut})
        before = counts(synthetic)
        collected = reader._collect_index(spec)
        assert collected.entries and not collected.failures and not reader._cursors
        seen = set()
        for row in reader._cuts[cut.cut_id][1].objects.values():
            if row['object_type'] != 'resource_version/v1':
                continue
            from cpn.rpnh.registry.resources import ResourceVersionRef
            from cpn.rpnh.registry.identities import TypedId
            ref = SourceQualifiedResourceRef(synthetic[-1], ResourceVersionRef(
                TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
            try:
                reader._authorize(synthetic[-1], ref, 'index')
            except RegistryReadSessionError as error:
                assert error.code == 'NOT_DISCLOSED'
            else:
                seen.add(row['version_id'])
        assert seen == {row['entry_ref']['ref']['resource_version_id'] for row in collected.entries}
        all_resources = sum(row['object_type'] == 'resource_version/v1'
            for row in reader._cuts[cut.cut_id][1].objects.values())
        assert len(seen) < all_resources
        ref = SourceQualifiedResourceRef.from_dict(collected.entries[0]['entry_ref'], catalog=core.catalog)
        for action, code in (
            (lambda: reader.read_exact(entry_ref=ref, at_cut=cut, projection=('test_only_extra_field',)), 'NOT_DISCLOSED'),
            (lambda: reader.read_material(resource_ref=ref, at_cut=cut), 'GOVERNED_DELIVERY_REQUIRED'),
            (lambda: reader.read_exact(entry_ref=SourceQualifiedVersionRef(synthetic[-1], contexts['main'].net_instance_ref),
                at_cut=cut), 'NOT_DISCLOSED')):
            with pytest.raises(RegistryReadSessionError) as error:
                action()
            assert error.value.code == code
        assert counts(synthetic) == before


def test_collection_is_complete_ordered_same_cut_and_does_not_allocate_cursor(world, monkeypatch):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        spec = IndexQuery((world[-1],), (
            TypedIndexClause('resource_version/v1', (TypedPredicate('byte_count', 'ge', 1),), ('summary',)),
            TypedIndexClause('resource_version/v1', (TypedPredicate('summary', 'eq', 'item-0'),), ('media_type',))),
            1, cuts={world[-1]: cut})
        read_calls = []
        original = reader._catalog.read_index
        def read(*args, **kwargs):
            read_calls.append(kwargs['projection'])
            return original(*args, **kwargs)
        monkeypatch.setattr(reader._catalog, 'read_index', read)
        before = counts(world)
        collected = reader._collect_index(spec)
        assert len(collected.entries) == len(read_calls) == 4 > spec.page_size
        assert not reader._cursors and not hasattr(reader, '_cursor_secret')
        assert collected.cuts == {world[-1]: cut} and collected.failures == {}
        assert all('byte_count' in fields for fields in read_calls)
        assert all('byte_count' not in entry['fields'] for entry in collected.entries)
        assert all(entry['source_cut'] == cut.to_dict() for entry in collected.entries)
        keys = [(entry['entry_ref']['source_id'], entry['entry_type'], entry['entry_ref']['ref']['resource_id'],
            entry['entry_ref']['ref']['resource_version_id']) for entry in collected.entries]
        assert keys == sorted(keys)
        assert next(entry for entry in collected.entries if entry['fields']['summary'] == 'item-0')['fields']['media_type'] == 'text/plain'
        page = reader.query_index(spec)
        delivered = list(page['entries'])
        while page['continuation']:
            cursor = page['continuation']
            page = reader.query_index(spec, cursor=cursor)
            assert page == reader.query_index(spec, cursor=cursor)
            delivered.extend(page['entries'])
        assert tuple(delivered) == collected.entries
        assert counts(world) == before


def test_collection_readable_empty_differs_from_failure_after_full_traversal(world, monkeypatch):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        spec = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1',
            (TypedPredicate('summary', 'eq', 'absent'),), ('summary',)),), 1, cuts={world[-1]: cut})
        empty = reader._collect_index(spec)
        assert not empty.entries and empty.failures == {} and empty.cuts == {world[-1]: cut}
        original = reader._catalog.read_index
        calls = []
        def later_failure(*args, **kwargs):
            calls.append(True)
            if len(calls) % 4 == 0:
                raise readers.TypedReadError('INTEGRITY_FAILED')
            return original(*args, **kwargs)
        monkeypatch.setattr(reader._catalog, 'read_index', later_failure)
        all_spec = replace(spec, clauses=(TypedIndexClause('resource_version/v1', projection=('summary',)),))
        failed = reader._collect_index(all_spec)
        assert len(calls) == 4  # Failure beyond page_size discards earlier entries.
        assert not failed.entries and failed.failures == {world[-1]: 'INTEGRITY_FAILED'}
        assert not reader._cursors
        page = reader.query_index(all_spec)
        assert not page['entries'] and page['continuation'] is None
        assert page['source_results'][0]['access_state'] == 'INTEGRITY_FAILED'
        assert page['source_results'][0]['coverage']['loaded_count'] is None


@pytest.mark.parametrize('code', ['NOT_DISCLOSED', 'ACCESS_CHANGED'])
def test_collection_preserves_per_object_authorization_failure_semantics(world, monkeypatch, code):
    with session(world, issue(world)) as reader:
        cut = reader.capture_cut(world[-1])
        original = reader._authorize
        calls = []
        def authorize(*args, **kwargs):
            original(*args, **kwargs)
            calls.append(True)
            if len(calls) == 2:
                raise RegistryReadSessionError(code)
        monkeypatch.setattr(reader, '_authorize', authorize)
        result = reader._collect_index(IndexQuery((world[-1],),
            (TypedIndexClause('resource_version/v1', projection=('summary',)),), 1, cuts={world[-1]: cut}))
        if code == 'NOT_DISCLOSED':
            assert len(calls) == 4 and len(result.entries) == 3 and result.failures == {}
        else:
            assert len(calls) == 2 and not result.entries and result.failures == {world[-1]: code}
        assert not reader._cursors


def test_collection_validates_predicate_permissions_before_read(world, monkeypatch):
    from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access
    from datetime import datetime, timedelta, timezone
    context = issue_observer_access(world[1], principal_ref=world[4],
        scope=ObserverReadScope({'resource_version/v1': ('summary',)}, {}), purpose='inspect',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='restricted')
    with session(world, context) as reader:
        monkeypatch.setattr(reader._catalog, 'read_index', lambda *_a, **_k: pytest.fail('unauthorized predicate read'))
        spec = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1',
            (TypedPredicate('byte_count', 'eq', 6),), ('summary',)),), 1)
        with pytest.raises(RegistryReadSessionError) as error:
            reader._collect_index(spec)
        assert error.value.code == 'NOT_DISCLOSED'
        assert not reader._cuts and not reader._cursors


def test_resource_only_custom_catalog_keeps_its_supported_surface(world):
    class ResourceOnlyCatalog(readers.TypedReaderCatalog):
        entry_types = ('resource_version/v1',)
        def fields(self, entry_type):
            if entry_type not in self.entry_types:
                raise readers.TypedReadError('UNSUPPORTED_ENTRY_TYPE')
            return super().fields(entry_type)
    catalog = ResourceOnlyCatalog()
    with session(world, issue(world), reader_catalog=catalog) as reader:
        page = reader.query_index(IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1'),), 1))
        assert page['entries'] and page['continuation']
        ref = SourceQualifiedResourceRef.from_dict(page['entries'][0]['entry_ref'], catalog=world[0].catalog)
        record = reader.read_exact(entry_ref=ref, at_cut=page['entries'][0]['source_cut'])
        assert record['disclosure']['projected_fields'] == list(_EXPECTED_RECORD_PROJECTIONS['resource_version/v1'])
        assert set(record['record']) == set(page['entries'][0]['fields'])
