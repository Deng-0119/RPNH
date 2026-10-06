"""Observed blob/transaction/response boundaries for real author-only recovery."""
from dataclasses import replace
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import AssemblyAuthorV2, validate_assembly_revision
from cpn.rpnh.collaboration import assemblies
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v2_publication import assert_no_run, fixture, request
from test_collaboration_assembly_v2_recovery import _facts, _payload, _stages, _version
from test_collaboration_graph_materials import registration


LOWLEVEL_CUTS = ('plan_prewrite_before', 'plan_blob_before_commit', 'plan_transaction_rollback',
                'assembly_transaction_rollback', 'validated_reply_lost')


class LowLevelCut(RuntimeError):
    """A test-only interruption at an explicitly observed real boundary."""


def _blobs(core):
    return {str(path.relative_to(core.object_store.root)): {
        'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in core.object_store.root.rglob('*') if path.is_file()}


def _transaction_for(core, key):
    with core.event_store.connect() as db:
        row = db.execute('SELECT transaction_id,status FROM transactions WHERE idempotency_key=?', (key,)).fetchone()
    return None if row is None else dict(row)


@pytest.mark.parametrize('cut', LOWLEVEL_CUTS)
def test_observed_lowlevel_boundary_restores_original_exact_request(fixture, monkeypatch, cut):
    core, gateway, author, _, member = fixture
    args = request(member, command_id='assembly:lowlevel')
    stages = _stages(author, args['command_id'])
    plan_ref, assembly_ref = stages[0][2], stages[-1][2]
    plan_version, assembly_version = _version(plan_ref), _version(assembly_ref)
    plan_path = core.object_store.path_for_version(plan_version.version_id)
    assembly_path = core.object_store.path_for_version(assembly_version.version_id)
    before, before_blobs = _facts(core), _blobs(core)
    assert not plan_path.exists() and not assembly_path.exists()
    assert _transaction_for(core, stages[0][1]) is None
    observed, prepared_bytes, full_result = {}, {}, []

    with monkeypatch.context() as patch:
        if cut in ('plan_prewrite_before', 'plan_blob_before_commit'):
            original = core.object_store.prewrite_with_metadata_factory
            def prewrite(**kwargs):
                selected = kwargs['version_id'] == plan_version.version_id
                if selected:
                    prepared_bytes['plan'] = kwargs['payload']
                    assert kwargs['logical_id'] == plan_version.entity_id
                    assert kwargs['object_type'] == 'resource_version/v1'
                    if cut == 'plan_prewrite_before':
                        assert not plan_path.exists()
                        observed.update(boundary='before_original_object_store_prewrite', original_prewrite_returned=False)
                        raise LowLevelCut(cut)
                result = original(**kwargs)
                if selected:
                    assert plan_path.read_bytes() == kwargs['payload']
                    assert core.event_store.object_row(plan_version.version_id) is None
                    assert _transaction_for(core, stages[0][1]) is None
                    observed.update(boundary='after_original_object_store_prewrite_before_sql', original_prewrite_returned=True)
                    raise LowLevelCut(cut)
                return result
            patch.setattr(core.object_store, 'prewrite_with_metadata_factory', prewrite)
        elif cut in ('plan_transaction_rollback', 'assembly_transaction_rollback'):
            original_insert = core.event_store._insert_event
            selected_version = plan_version if cut == 'plan_transaction_rollback' else assembly_version
            selected_key = stages[0][1] if cut == 'plan_transaction_rollback' else stages[-1][1]
            def insert_then_interrupt(db, event):
                result = original_insert(db, event)
                if (event.event_type == 'object_version_published/v1'
                        and event.payload.get('version_id') == str(selected_version.version_id)):
                    assert event.idempotency_key == selected_key
                    assert db.in_transaction
                    stored_event = db.execute('SELECT event_id,transaction_id FROM events WHERE event_id=?',
                                              (str(event.event_id),)).fetchone()
                    transaction = db.execute('SELECT status FROM transactions WHERE transaction_id=?',
                                             (str(event.transaction_id),)).fetchone()
                    assert stored_event is not None and stored_event['transaction_id'] == str(event.transaction_id)
                    assert transaction is not None and transaction['status'] == 'prepared'
                    # The real implementation inserts event rows before object
                    # rows. This is a genuine in-SQL mutation/rollback boundary,
                    # without claiming that object insertion has happened yet.
                    object_row = db.execute('SELECT version_id FROM objects WHERE version_id=?',
                                            (str(selected_version.version_id),)).fetchone()
                    assert object_row is None
                    assert core.object_store.path_for_version(selected_version.version_id).is_file()
                    observed.update(boundary='after_real_event_insert_inside_sql', in_transaction=True,
                        prepared_transaction_visible=True, event_visible=True, object_inserted=False,
                        event_id=str(event.event_id), transaction_id=str(event.transaction_id))
                    raise LowLevelCut(cut)
                return result
            patch.setattr(core.event_store, '_insert_event', insert_then_interrupt)
        else:
            original_validate = assemblies.validate_assembly_revision
            def validated_then_lost(selected_core, reference, selected_registration):
                result = original_validate(selected_core, reference, selected_registration)
                if selected_core is core and reference == assembly_ref:
                    assert core.event_store.object_row(assembly_version.version_id) is not None
                    full_result.append(result)
                    observed.update(boundary='after_real_public_full_validation', public_validation_completed=True)
                    raise LowLevelCut(cut)
                return result
            patch.setattr(assemblies, 'validate_assembly_revision', validated_then_lost)
        with pytest.raises(LowLevelCut, match=cut):
            author.publish(**args)

    assert observed
    after_fault, after_blobs = _facts(core), _blobs(core)
    expected_prefix = 8 if cut == 'assembly_transaction_rollback' else 9 if cut == 'validated_reply_lost' else 0
    expected_events = 23 if expected_prefix == 8 else 25 if expected_prefix == 9 else 0
    assert after_fault == {'objects': before['objects'] + expected_prefix,
                          'events': before['events'] + expected_events,
                          'committed_transactions': before['committed_transactions'] + expected_prefix}
    assert all(after_blobs[path] == value for path, value in before_blobs.items())
    added_blobs = {path: value for path, value in after_blobs.items() if path not in before_blobs}
    expected_blobs = 0 if cut == 'plan_prewrite_before' else 1 if expected_prefix == 0 else 9
    assert len(added_blobs) == expected_blobs
    assert plan_path.exists() == (cut != 'plan_prewrite_before')
    assert assembly_path.exists() == (expected_prefix in (8, 9))
    assert (core.event_store.object_row(plan_version.version_id) is not None) == (expected_prefix in (8, 9))
    assert (core.event_store.object_row(assembly_version.version_id) is not None) == (expected_prefix == 9)
    if cut in ('plan_prewrite_before', 'plan_blob_before_commit', 'plan_transaction_rollback'):
        assert _transaction_for(core, stages[0][1]) is None
    if cut == 'assembly_transaction_rollback':
        assert _transaction_for(core, stages[-1][1]) is None
    if cut in ('plan_transaction_rollback', 'assembly_transaction_rollback'):
        with core.event_store.connect() as db:
            assert db.execute('SELECT event_id FROM events WHERE event_id=?', (observed['event_id'],)).fetchone() is None
            assert db.execute('SELECT transaction_id FROM transactions WHERE transaction_id=?', (observed['transaction_id'],)).fetchone() is None
        observed['inserted_event_and_transaction_rolled_back'] = True
    if cut == 'validated_reply_lost':
        assert len(full_result) == 1
    locked_plan_bytes = prepared_bytes['plan'] if cut == 'plan_prewrite_before' else plan_path.read_bytes()
    locked_plan = json.loads(locked_plan_bytes)

    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    new_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV2(new_gateway, registration(), author.producer)
    assert reopened is not core and new_gateway is not gateway and replay is not author
    assert reopened.writer_epoch > core.writer_epoch
    assert _facts(reopened) == after_fault and _blobs(reopened) == after_blobs
    if cut != 'plan_prewrite_before':
        changed = {**args, 'members': (replace(args['members'][0], display_name='Different legal label'), args['members'][1])}
        with pytest.raises(RegistryConflict, match='conflict'):
            replay.publish(**changed)
        assert _facts(reopened) == after_fault and _blobs(reopened) == after_blobs
        if expected_prefix == 0:
            # Exact-version-addressed bytes block replacement, but the plan has
            # no canonical publication, command authority or successful result.
            assert reopened.event_store.object_row(plan_version.version_id) is None
            assert _transaction_for(reopened, stages[0][1]) is None
            observed['changed_request_rejected_by_orphan_immutable_bytes'] = True
        else:
            observed['changed_request_rejected_by_committed_plan'] = True
    else:
        observed['no_persistent_request_bytes_or_canonical_command'] = True

    value = replay.publish(**args)
    final_refs = (value.revision.plan_ref, value.generated.revision.definition_ref,
        value.generated.revision.element_mapping_ref, value.generated.revision.boundary_mapping_ref,
        value.generated.revision.host_requirements_ref, value.revision.generated_revision_ref,
        value.revision.compiled_inventory_ref, value.revision.lowering_mapping_ref, value.revision.revision_ref)
    assert final_refs == tuple(reference for _, _, reference in stages)
    assert _payload(reopened, plan_ref) == locked_plan_bytes
    assert value.plan == locked_plan
    final_blobs = _blobs(reopened)
    assert all(final_blobs[path] == digest for path, digest in after_blobs.items())
    documents = [value.generated.module.to_dict(), value.generated.element_map, value.generated.boundary_map,
                 value.generated.host_requirements, value.compiled.to_dict(), value.lowering_map]
    refs = (final_refs[1], final_refs[2], final_refs[3], final_refs[4], final_refs[6], final_refs[7])
    for reference, document, signature in zip(refs, documents, locked_plan['prepared_materials'], strict=True):
        raw = _payload(reopened, reference)
        assert raw == canonical_json(document) and signature['resource_ref'] == reference.to_dict()
        assert len(raw) == signature['bytes'] and hashlib.sha256(raw).hexdigest() == signature['sha256']
    final_facts = _facts(reopened)
    assert final_facts == {'objects': before['objects'] + 9, 'events': before['events'] + 25,
                          'committed_transactions': before['committed_transactions'] + 9}
    if cut == 'validated_reply_lost':
        assert final_facts == after_fault and final_blobs == after_blobs
        assert value.revision == full_result[0].revision and value.lowering_map == full_result[0].lowering_map
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, registration())
    assert checked.revision == value.revision and checked.plan == value.plan
    assert checked.lowering_map == value.lowering_map
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert _facts(reopened) == final_facts and _blobs(reopened) == final_blobs
    assert_no_run(reader)
    print('C_DR02_TRACE=' + json.dumps({'cut': cut, 'observed': observed, 'before': before,
        'after_fault': after_fault, 'after_recovery': final_facts, 'new_blob_count_at_fault': len(added_blobs),
        'new_blob_digests_at_fault': added_blobs, 'plan_blob_at_fault': cut != 'plan_prewrite_before',
        'plan_canonical_at_fault': expected_prefix in (8, 9), 'assembly_canonical_at_fault': expected_prefix == 9,
        'old_epoch': core.writer_epoch, 'new_epoch': reopened.writer_epoch,
        'exact_original_request_recovered': True, 'fresh_readonly_full_consumer': True}, sort_keys=True))
