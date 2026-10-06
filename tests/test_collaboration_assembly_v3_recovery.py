"""Durable v3 cuts resume the exact nested request under a fresh writer."""
from dataclasses import replace
import json
import pytest
from cpn.rpnh.collaboration import AssemblyAuthorV2, AssemblyAuthorV3, validate_assembly_revision
from cpn.rpnh.collaboration import assembly_v3 as assembly
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.transaction import RegistryTransaction
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_v3 import setup_fixture, request, C, D
from test_collaboration_assembly_v2_publication import assert_no_run, counts
from test_collaboration_assembly_v2_recovery import CUTS, DurableCut, _version, _payload, _facts
from test_collaboration_graph_materials import registration


@pytest.fixture
def fixture(tmp_path):
    return setup_fixture(tmp_path)


def stages(author, command):
    core, binding = author.core, author.binding
    key = assembly._command(command)
    _, generated_key, generated_ref = assembly._generated_ref(core, binding['source_id'], command, None)
    values = [(key + ':plan', assembly._material_ref(core, binding, key + ':plan'))]
    values.extend((generated_key + ':material:' + str(i), assembly._material_ref(core, binding, generated_key + ':material:' + str(i))) for i in range(4))
    values.append((generated_key, generated_ref))
    values.extend((key + ':' + role, assembly._material_ref(core, binding, key + ':' + role)) for role in ('compiled', 'lowering'))
    values.append((key, assembly._result_ref(core, binding['source_id'], command, None)))
    return [(name, command, reference) for name, (command, reference) in zip(CUTS, values, strict=True)]


@pytest.mark.parametrize('cut', CUTS)
def test_nested_every_durable_cut_fresh_writer_conflict_and_exact_recovery(fixture, monkeypatch, cut):
    core, gateway, selected, producer, graph = fixture
    child = AssemblyAuthorV2(gateway, selected, producer).publish(**request(graph, version=2, command='child'))
    author = AssemblyAuthorV3(gateway, selected, producer)
    args = request(child, command='root:recovery', ids=(C, D))
    expected = stages(author, args['command_id']); keys = {key: (name, reference) for name, key, reference in expected}
    before = _facts(core); prefix = {}; commit = RegistryTransaction.commit
    def interrupt(tx):
        result = commit(tx)
        if tx.event_store is core.event_store and tx.idempotency_key in keys:
            name, ref = keys[tx.idempotency_key]
            prefix[name] = _payload(core, ref)
            if name == cut:
                raise DurableCut(name)
        return result
    monkeypatch.setattr(RegistryTransaction, 'commit', interrupt)
    with pytest.raises(DurableCut, match=cut):
        author.publish(**args)
    monkeypatch.setattr(RegistryTransaction, 'commit', commit)
    assert list(prefix) == list(CUTS[:CUTS.index(cut) + 1])
    after_cut = _facts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    gateway2 = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV3(gateway2, registration(), producer)
    assert reopened.writer_epoch != core.writer_epoch
    assert _facts(reopened) == after_cut
    with pytest.raises(StaleWriterError):
        author.publish(**args)
    changed = {**args, 'members': (replace(args['members'][0], display_name='Changed'), args['members'][1])}
    with pytest.raises(RegistryConflict):
        replay.publish(**changed)
    assert _facts(reopened) == after_cut
    result = replay.publish(**args)
    final = _facts(reopened)
    assert final == {key: before[key] + delta for key, delta in (('objects', 9), ('events', 25), ('committed_transactions', 9))}
    for name, _, ref in expected:
        if name in prefix:
            assert _payload(reopened, ref) == prefix[name]
    checked = validate_assembly_revision(_RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog), result.revision.revision_ref, registration())
    assert canonical_json(checked.lowering_map) == canonical_json(result.lowering_map)
    assert replay.publish(**args).revision == result.revision
    assert _facts(reopened) == final
    assert_no_run(reopened)
    print('NESTED_V3_RECOVERY=' + json.dumps({'cut': cut, 'before': before, 'after_cut': after_cut, 'after_recovery': final, 'writer_epoch_changed': True}, sort_keys=True))
