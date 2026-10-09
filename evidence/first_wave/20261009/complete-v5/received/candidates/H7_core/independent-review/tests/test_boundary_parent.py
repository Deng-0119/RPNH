"""Reviewer-authored tests. Real temporary Registries; offline evidence is test-only."""
import json
import pytest
from parent_child_fixtures import accepted_parent, parent_owner, evidence, phase
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import EventStore, RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_static_lease_reads import EXECUTOR


def capture_chain(path, monkeypatch):
    recorded = {}
    original = EventStore.publish_batch
    def capture(self, **kwargs):
        for item in kwargs['objects']:
            if item.object_type in h7.PARENT_KINDS:
                recorded[item.object_type] = kwargs.copy()
        return original(self, **kwargs)
    monkeypatch.setattr(EventStore, 'publish_batch', capture)
    result = accepted_parent(path)
    monkeypatch.setattr(EventStore, 'publish_batch', original)
    return result, recorded


def raw_replay(core, kind, kwargs):
    if kind == h7.PARENT_KINDS[0]:
        return core.event_store.publish_batch(**kwargs)
    body = next(dict(item.metadata) for item in kwargs['objects'] if item.object_type == kind)
    proof = evidence(core.event_store, kind, body)
    with h7._native_boundary(proof, core.event_store, kind, body):
        return core.event_store.publish_batch(**kwargs)


@pytest.mark.parametrize('kind', h7.PARENT_KINDS)
def test_live_exact_raw_replay_has_no_new_commit(tmp_path, monkeypatch, kind):
    values, recorded = capture_chain(tmp_path/'parent', monkeypatch)
    core = values[0]._core
    before = core.event_store.max_ordinal()
    events = raw_replay(core, kind, recorded[kind])
    assert events
    assert core.event_store.max_ordinal() == before
    assert len(core.event_store.object_rows_by_type(kind)) == 1


@pytest.mark.parametrize('kind', h7.PARENT_KINDS)
def test_exact_raw_replay_after_owner_stop_revalidates_live_authority(tmp_path, monkeypatch, kind):
    values, recorded = capture_chain(tmp_path/'parent', monkeypatch)
    owner = values[0]
    owner.record_owner_stop(idempotency_key='review:stop')
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match='closed|fresh'):
        raw_replay(owner._core, kind, recorded[kind])
    assert owner._core.event_store.max_ordinal() == before
    assert len(owner._core.event_store.object_rows_by_type(h7.PARENT_KINDS[-1])) == 1


def test_parent_cannot_complete_from_self_asserted_products_without_native_observation(tmp_path):
    owner, admitted, execution, prepared, *_ = accepted_parent(tmp_path/'parent')
    outputs = owner.products(execution, outcome_id='complete',
                   products={'step.result':(canonical_json('done'),)}, command_id='review:products')
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported, match='observation|completion'):
        owner.succeed(outputs, command_id='review:fake-native-success')
    assert owner._core.event_store.max_ordinal() == before
    with owner._core.event_store.connect() as db:
        roots = db.execute("SELECT state FROM firing_publications").fetchall()
    assert [row[0] for row in roots] == ['PROVISIONAL']


def test_intent_helper_replay_after_parent_writer_fence_is_closed(tmp_path):
    path=tmp_path/'parent'
    owner, admitted, execution, prepared, *_ = accepted_parent(path)
    new_core = _RegistryCore(path, create=False, catalog=owner._core.catalog)
    assert new_core.writer_epoch > owner._core.writer_epoch
    before = new_core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        h7.register_child_intent(owner._core, execution, prepared)
    assert new_core.event_store.max_ordinal() == before


@pytest.mark.parametrize('change', [
    {'capacity':2}, {'with_launch':False}, {'outcomes':('complete','failed','interrupted')},
    {'outcomes':('complete',)}, {'executor':EXECUTOR}],
    ids=['two-slot-capacity','no-launch-capability','interrupted-outcome','missing-failed-outcome','ordinary-executor'])
def test_started_ordinary_operation_cannot_substitute_launcher_pn_contract(tmp_path, monkeypatch, change):
    if change.get('capacity') == 2:
        import parent_child_fixtures as fixture
        original = fixture.InitialTokenDeclaration
        monkeypatch.setattr(fixture, 'InitialTokenDeclaration', lambda **kw: original(**{**kw, 'count':2}))
    owner, admitted, execution, prepared = parent_owner(tmp_path/'parent', **change)
    assert execution.start_event_id is not None
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        h7.register_child_intent(owner._core, execution, prepared)
    assert owner._core.event_store.max_ordinal() == before
    assert not owner._core.event_store.object_rows_by_type(h7.PARENT_KINDS[0])


@pytest.mark.parametrize('proof', [None, {}, {'peer':True,'ready':True,'stopped':False}])
def test_public_dispatch_cannot_use_self_asserted_native_evidence(tmp_path, proof):
    owner, admitted, execution, prepared = parent_owner(tmp_path/'parent')
    intent=h7.register_child_intent(owner._core,execution,prepared)
    before=owner._core.event_store.max_ordinal()
    with pytest.raises(h7.ParentChildUnsupported):
        h7._advance_record(owner._core,h7.PARENT_KINDS[1],intent,
                           details={'bundle_digest':'a'*64},evidence=proof)
    assert owner._core.event_store.max_ordinal()==before
    assert not owner._core.event_store.object_rows_by_type(h7.PARENT_KINDS[1])


def test_native_evidence_has_no_production_constructor():
    with pytest.raises(h7.ParentChildUnsupported):
        h7._NativeBoundaryEvidence(store=None, action='acceptance', body=b'{}')
