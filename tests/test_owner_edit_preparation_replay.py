"""Resume one real owner candidate after pre-operation preparation failure."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cpn.rpnh import owner_edits
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._operation import registration as operation_registration
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.operations import OperationAuthorityError, bind_operation_registration
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_terminal_owner_adoption import make_owner, current


@pytest.mark.parametrize(("registered_value", "replayed_value"), [
    (True, 1),
    (1, 1.0),
], ids=["boolean-vs-number", "integer-vs-float"])
def test_same_candidate_replays_after_registration_failure(
        tmp_path, monkeypatch, registered_value, replayed_value):
    # Restore process-global registration after the test as well as explicitly
    # restoring this original owner's registration for the actual replay.
    monkeypatch.setattr(operation_registration, '_OPERATION_REGISTRATION',
                        operation_registration._OPERATION_REGISTRATION)
    owner = make_owner(tmp_path)
    core = owner._core
    identity = owner.identity
    original_net = current(owner)[0].net_ref
    writer_epoch = core.event_store.writer_epoch
    document = current(owner)[1].compiled.source.to_dict()
    document['name'] = 'RecoveredExactCandidate'
    document['terminal']['config']['typed_replay_option'] = registered_value
    candidate = ModuleDeclaration.from_dict(document)
    arguments = prepare_replacement(owner, candidate).owner_command()
    assert arguments['owner_inputs'] == {}
    command_id = 'same-original-owner-edit'
    operations_before = len(core.event_store.object_rows_by_type('operation_spec/v1'))
    # The owner's compiler still has the correct Registration, while the
    # process-global operation inventory models the real unrelated HOST bind.
    bind_operation_registration(Registration())
    with pytest.raises(OperationAuthorityError, match='HOST-registered'):
        owner.control.edits.submit(arguments, command_id)

    candidates = [(ref, body) for ref, body in owner.control.records('candidate')
                  if body['command_id'] == command_id]
    (candidate_ref, candidate_body), = candidates
    original_bytes = core.object_store.read_registered(core.get_version(candidate_ref.resource_version_id))
    assert candidate_body['data'] == arguments
    assert not [body for _ref, body in owner.control.records('command') if body['command_id'] == command_id]
    assert not [body for _ref, body in owner.control.records('result') if body['command_id'] == command_id]
    assert owner.control.edits.queue == []
    assert current(owner)[0].net_ref == original_net
    assert len(core.event_store.object_rows_by_type('operation_spec/v1')) == operations_before
    partial_head = core.event_store.max_ordinal()

    changed = deepcopy(arguments)
    changed['candidate']['name'] = 'DifferentImmutableData'
    with pytest.raises(ValueError, match='different immutable candidate data'):
        owner.control.edits.submit(changed, command_id)
    assert core.event_store.max_ordinal() == partial_head

    # These have equal Python values but different immutable JSON documents.
    typed_change = deepcopy(arguments)
    typed_change['candidate']['terminal']['config']['typed_replay_option'] = replayed_value
    assert typed_change == arguments
    assert canonical_json(typed_change) != canonical_json(arguments)
    with monkeypatch.context() as reject_before_compile:
        def unexpected_compile(*args, **kwargs):
            pytest.fail('different typed candidate must be rejected before compilation')
        reject_before_compile.setattr(owner_edits, 'compile_module', unexpected_compile)
        with pytest.raises(ValueError, match='different immutable candidate data'):
            owner.control.edits.submit(typed_change, command_id)
    assert core.event_store.max_ordinal() == partial_head

    # A later queued partial state is not reconstructed by this narrow repair.
    owner.control.edits.queue.append(SimpleNamespace(command_id=command_id))
    try:
        with pytest.raises(RegistryConflict, match='command or queued edit'):
            owner.control.edits.submit(arguments, command_id)
    finally:
        owner.control.edits.queue.clear()
    assert core.event_store.max_ordinal() == partial_head

    publish = owner.control.publish
    def no_candidate_republication(kind, *args, **kwargs):
        assert kind != 'candidate', 'same candidate must be reused, never republished'
        return publish(kind, *args, **kwargs)
    monkeypatch.setattr(owner.control, 'publish', no_candidate_republication)
    bind_operation_registration(owner.registration)
    adopted = owner.control.edits.submit(arguments, command_id)
    assert adopted['status'] == 'ADOPTED'
    assert owner._core is core and owner.identity == identity
    assert core.event_store.writer_epoch == writer_epoch
    assert current(owner)[1].compiled.source.to_dict() == candidate.to_dict()
    assert current(owner)[0].net_ref != original_net
    assert owner.control.edits.queue == []
    assert [(ref, body) for ref, body in owner.control.records('candidate')
            if body['command_id'] == command_id] == candidates
    assert core.object_store.read_registered(core.get_version(candidate_ref.resource_version_id)) == original_bytes
    assert original_bytes == canonical_json(candidate_body)
    assert len([body for _ref, body in owner.control.records('command') if body['command_id'] == command_id]) == 1

    final_head = core.event_store.max_ordinal()
    replay = owner.control.edits.submit(arguments, command_id)
    assert replay['status'] == 'ADOPTED'
    assert core.event_store.max_ordinal() == final_head
    with pytest.raises(ValueError, match='different immutable candidate data'):
        owner.control.edits.submit(changed, command_id)
    assert core.event_store.max_ordinal() == final_head
