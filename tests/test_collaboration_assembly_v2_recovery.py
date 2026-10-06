"""Real committed Assembly cuts and recovery by a newly reopened owner."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import AssemblyAuthorV2, SourceQualifiedResourceRef, validate_assembly_revision
from cpn.rpnh.collaboration import assembly_v2 as assembly
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_collaboration_assembly_v2_publication import assert_no_run, fixture, request
from test_collaboration_graph_materials import registration


CUTS = ('plan', 'definition', 'element_mapping', 'boundary_mapping', 'host_requirements',
        'generated_revision', 'compiled_inventory', 'lowering_mapping', 'assembly_revision')


class DurableCut(RuntimeError):
    """A lost response after the original Registry commit really returned."""


def _facts(core):
    with core.event_store.connect() as db:
        committed = db.execute("SELECT COUNT(*) FROM transactions WHERE status='committed'").fetchone()[0]
    return {'objects': len(core.event_store.object_rows()),
            'events': len(core.event_store.list_events()), 'committed_transactions': committed}


def _version(reference):
    return reference.ref.as_version_ref() if isinstance(reference, SourceQualifiedResourceRef) else reference.ref


def _stages(author, command_id):
    core, binding = author.core, author.binding
    key = assembly._command(command_id)
    _, generated_key, generated_ref = assembly._generated_ref(core, binding['source_id'], command_id, None)
    values = [(key + ':plan', assembly._material_ref(core, binding, key + ':plan'))]
    values.extend((generated_key + ':material:' + str(index),
        assembly._material_ref(core, binding, generated_key + ':material:' + str(index))) for index in range(4))
    values.append((generated_key, generated_ref))
    values.extend((key + ':' + role, assembly._material_ref(core, binding, key + ':' + role))
                  for role in ('compiled', 'lowering'))
    values.append((key, assembly._result_ref(core, binding['source_id'], command_id, None)))
    return [(name, command, ref) for name, (command, ref) in zip(CUTS, values, strict=True)]


def _payload(core, reference):
    return core.object_store.path_for_version(_version(reference).version_id).read_bytes()


@pytest.mark.parametrize('cut', CUTS)
def test_every_real_committed_cut_reopens_new_owner_and_recovers_exact_command(fixture, monkeypatch, cut):
    core, gateway, author, _, member = fixture
    args = request(member, command_id='assembly:durable-cut')
    stages = _stages(author, args['command_id'])
    by_key = {key: (name, reference) for name, key, reference in stages}
    before = _facts(core)
    before_ids = {row['version_id'] for row in core.event_store.object_rows()}
    observed, committed_payloads = [], {}
    original_commit = RegistryTransaction.commit

    def commit_then_interrupt(transaction):
        result = original_commit(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key in by_key:
            name, reference = by_key[transaction.idempotency_key]
            assert name == CUTS[len(observed)]
            assert transaction._closed  # The real commit returned successfully.
            assert len(transaction._objects) == 1
            prepared = transaction._objects[0]
            exact = _version(reference)
            assert (prepared.object_type, prepared.logical_id, prepared.version_id) == (
                exact.entity_type, exact.entity_id, exact.version_id)
            row = core.event_store.object_row(exact.version_id)
            assert row is not None and row['transaction_id'] == str(transaction.transaction_id)
            with core.event_store.connect() as db:
                status = db.execute('SELECT status FROM transactions WHERE transaction_id=?',
                                    (str(transaction.transaction_id),)).fetchone()[0]
                assert status == 'committed'
            raw = _payload(core, reference)
            assert len(raw) == prepared.size
            committed_payloads[name] = raw
            observed.append({'stage': name, 'reference': reference.to_dict(),
                'transaction_id': str(transaction.transaction_id), 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest()})
            if name == cut:
                raise DurableCut('real committed cut: ' + name)
        return result

    monkeypatch.setattr(RegistryTransaction, 'commit', commit_then_interrupt)
    with pytest.raises(DurableCut, match='real committed cut: ' + cut):
        author.publish(**args)
    monkeypatch.setattr(RegistryTransaction, 'commit', original_commit)
    cut_index = CUTS.index(cut)
    assert [row['stage'] for row in observed] == list(CUTS[:cut_index + 1])
    created = [row for row in core.event_store.object_rows() if row['version_id'] not in before_ids]
    assert len(created) == cut_index + 1
    expected_resources = sum(_version(reference).entity_type == 'resource_version/v1'
                             for _, _, reference in stages[:cut_index + 1])
    expected_generated = int(cut_index >= CUTS.index('generated_revision'))
    expected_assembly = int(cut == 'assembly_revision')
    assert sum(row['object_type'] == 'resource_version/v1' for row in created) == expected_resources
    assert sum(row['object_type'] == 'collaboration_net_revision/v1' for row in created) == expected_generated
    assert sum(row['object_type'] == assembly.ASSEMBLY_V2_TYPE for row in created) == expected_assembly
    after_cut = _facts(core)
    assert after_cut['committed_transactions'] - before['committed_transactions'] == cut_index + 1
    assert after_cut['events'] - before['events'] == 3 * expected_resources + 2 * (expected_generated + expected_assembly)
    locked_plan = json.loads(committed_payloads['plan'])
    plan_metadata = json.loads(core.event_store.object_row(_version(stages[0][2]).version_id)['metadata_json'])
    fixed_envelope = plan_metadata['descriptors']['assembly_author_command_v2']
    locked_documents = json.loads(fixed_envelope)['documents']
    assert json.loads(fixed_envelope)['plan'] == locked_plan
    assert len(locked_documents) == len(locked_plan['prepared_materials']) == 6

    # This is a new writable core and a newly configured owner, not continuation
    # through the interrupted author or cached registration objects.
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = registration()
    replay = AssemblyAuthorV2(next_gateway, next_registration, author.producer)
    assert reopened is not core and next_gateway is not gateway and replay is not author
    assert reopened.writer_epoch != core.writer_epoch
    assert _facts(reopened) == after_cut  # Setup reuses its preexisting resources.
    changed = {**args, 'members': (replace(args['members'][0], display_name='Different valid label'),
                                  args['members'][1])}
    with pytest.raises(RegistryConflict, match='conflict'):
        replay.publish(**changed)
    assert _facts(reopened) == after_cut
    for name, _, reference in stages[:cut_index + 1]:
        assert _payload(reopened, reference) == committed_payloads[name]
    assert json.loads(reopened.event_store.object_row(_version(stages[0][2]).version_id)['metadata_json'])['descriptors']['assembly_author_command_v2'] == fixed_envelope

    value = replay.publish(**args)
    final_refs = (value.revision.plan_ref, value.generated.revision.definition_ref,
        value.generated.revision.element_mapping_ref, value.generated.revision.boundary_mapping_ref,
        value.generated.revision.host_requirements_ref, value.revision.generated_revision_ref,
        value.revision.compiled_inventory_ref, value.revision.lowering_mapping_ref, value.revision.revision_ref)
    assert final_refs == tuple(reference for _, _, reference in stages)
    assert value.plan == locked_plan
    actual_documents = [value.generated.module.to_dict(), value.generated.element_map,
        value.generated.boundary_map, value.generated.host_requirements, value.compiled.to_dict(), value.lowering_map]
    assert canonical_json(actual_documents) == canonical_json(locked_documents)
    material_refs = (final_refs[1], final_refs[2], final_refs[3], final_refs[4], final_refs[6], final_refs[7])
    for reference, document, signature in zip(material_refs, locked_documents, locked_plan['prepared_materials'], strict=True):
        raw = _payload(reopened, reference)
        assert raw == canonical_json(document)
        assert signature['resource_ref'] == reference.to_dict()
        assert signature['bytes'] == len(raw) and signature['sha256'] == hashlib.sha256(raw).hexdigest()
    for name, _, reference in stages[:cut_index + 1]:
        assert _payload(reopened, reference) == committed_payloads[name]
    after_replay = _facts(reopened)
    assert after_replay == {key: before[key] + count for key, count in
                           (('objects', 9), ('events', 25), ('committed_transactions', 9))}
    if cut == 'assembly_revision':
        assert after_replay == after_cut  # Commit succeeded; only its reply was lost.
    assert len(reopened.event_store.object_rows_by_type(assembly.ASSEMBLY_V2_TYPE)) == 1
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, registration())
    assert checked.revision == value.revision
    assert checked.plan == value.plan and checked.lowering_map == value.lowering_map
    assert canonical_json(checked.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert _facts(reopened) == after_replay
    assert_no_run(reader)
    print('C_DR_COMMIT_TRACE=' + json.dumps({'cut': cut, 'original_epoch': core.writer_epoch,
        'reopened_epoch': reopened.writer_epoch, 'committed_prefix': observed,
        'after_cut': after_cut, 'after_replay': after_replay, 'before': before,
        'new_owner_label_conflict': True, 'original_request_exact_recovery': True,
        'assembly_committed_before_replay': bool(expected_assembly),
        'readonly_full_consumer': True}, sort_keys=True))
