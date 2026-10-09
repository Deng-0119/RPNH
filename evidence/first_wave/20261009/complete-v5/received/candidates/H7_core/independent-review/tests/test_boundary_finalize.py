"""Narrow continuation checks on unreviewed prospective protected provenance."""
from dataclasses import replace
import pytest
from parent_child_fixtures import bound_owner
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.event_store import EventStore, RegistryConflict
from cpn.rpnh.registry.errors import ResourceIdempotencyConflict


def test_prospective_protected_lifetime_fails_before_commit(tmp_path, monkeypatch):
    """Inject a locally valid private-system lifetime different from native bootstrap."""
    from cpn.rpnh.registry import resource_service
    original_publish = resource_service._publish_private_system
    original_commit = EventStore.publish_batch
    committed = []
    def substitute(core, task_ref, command, **kwargs):
        if command.content_schema_ref == h7.CAPABILITY_SCHEMA:
            command = replace(command, lifetime_ref=task_ref)
        return original_publish(core, task_ref, command, **kwargs)
    def observe(self, **kwargs):
        result = original_commit(self, **kwargs)
        committed.extend(o.object_type for o in kwargs['objects']
                         if o.object_type == h7.BOUND_KINDS[1])
        return result
    monkeypatch.setattr(resource_service, '_publish_private_system', substitute)
    monkeypatch.setattr(EventStore, 'publish_batch', observe)
    with pytest.raises((RegistryConflict, ResourceIdempotencyConflict)):
        bound_owner(tmp_path / 'child', monkeypatch)
    assert committed == [], 'wrong protected lifetime committed before later integrity rejection'


def test_prospective_protected_extra_capability_edge_fails_before_commit(tmp_path, monkeypatch):
    """An origin transaction cannot smuggle an extra capability-to-task edge."""
    from cpn.rpnh.registry import resource_service
    from cpn.rpnh.registry.transaction import RegistryTransaction
    from cpn.rpnh.registry.models import TypedRelation
    from cpn.rpnh.registry.identities import new_id
    original_publish = resource_service._publish_private_system
    original_relate = RegistryTransaction.relate
    original_commit = EventStore.publish_batch
    task = []
    committed = []
    def capture_task(core, task_ref, command, **kwargs):
        if command.content_schema_ref == h7.CAPABILITY_SCHEMA:
            task.append(task_ref)
        return original_publish(core, task_ref, command, **kwargs)
    def extra_edge(self, relation, **kwargs):
        result = original_relate(self, relation, **kwargs)
        if relation.source.entity_type == h7.BOUND_KINDS[1] and relation.target.entity_type == 'resource_version/v1':
            assert len(task) == 1
            original_relate(self, TypedRelation(new_id('relation'), 'derived_from', relation.target, task[0]),
                            system_owned=True)
        return result
    def observe(self, **kwargs):
        result = original_commit(self, **kwargs)
        committed.extend(o.object_type for o in kwargs['objects'] if o.object_type == h7.BOUND_KINDS[1])
        return result
    monkeypatch.setattr(resource_service, '_publish_private_system', capture_task)
    monkeypatch.setattr(RegistryTransaction, 'relate', extra_edge)
    monkeypatch.setattr(EventStore, 'publish_batch', observe)
    try:
        bound_owner(tmp_path / 'child', monkeypatch)
    except (RegistryConflict, ResourceIdempotencyConflict):
        pass
    else:
        pytest.fail('malformed protected origin transaction accepted; committed=' + repr(committed))
    assert committed == [], 'extra protected edge committed before later integrity rejection'
