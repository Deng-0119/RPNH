"""Reviewed materialized read paths close owned native SQLite connections."""
import pytest
from cpn.rpnh.registry.event_store import EventStore, RegistryCorruptError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry._event_store import queries
from test_event_store_owned_write_scope import handles


def ref(kind):
    return VersionRef(kind + '/v1', new_id(kind), new_id(kind + '_version'))


def cases():
    task, round_ref, net = ref('task'), ref('task_round'), ref('net_instance')
    invocation, binding, turn = ref('invocation'), ref('operation_binding'), ref('agent_turn')
    call = VersionRef('llm_call_spec/v2', new_id('llm_call'), new_id('llm_call_version'))
    return [
        ('firing_publication_row', ('missing',), {}, None),
        ('provisional_firing_rows_for_checkpoint', (), dict(net_version_id='missing', checkpoint_version_id='missing'), ()),
        ('firing_publication_for_invocation', ('missing',), {}, None),
        ('firing_publication_for_member', ('object', 'missing'), {}, None),
        ('workspace_lineage_head', ('missing',), {}, None),
        ('object_publication_event', (new_id('resource_version'),), dict(object_type='resource_version/v1'), RegistryCorruptError),
        ('latest_object_row', (new_id('resource'),), dict(object_type='resource_version/v1'), None),
        ('object_rows_by_logical', ('missing',), {}, ()),
        ('object_rows_by_type', ('missing/v1',), {}, ()),
        ('execution_environment_inventory_rows', (), dict(task_ref=task, task_round_ref=round_ref, net_instance_ref=net), ()),
        ('transition_firing_rows_for_task_net_transition', (), dict(task_ref=task, net_instance_ref=net, transition_id='missing'), ()),
        ('object_rows_by_producer', ('missing',), {}, ()),
        ('object_rows_by_invocation_id', ('missing',), {}, ()),
        ('resource_address_binding_rows_for_invocation_scope', (), dict(invocation_scope_ref=invocation, operation_binding_authorization_ref=binding), ()),
        ('provider_attempt_rows_for_call', ('missing', 'missing'), {}, ()),
        ('agent_action_rows_for_turn', (turn,), {}, ()),
        ('settled_agent_action_rows_for_turn', (turn,), {}, ()),
        ('completed_agent_context_compaction_rows_for_loop', ('missing',), {}, ()),
        ('agent_loop_rows_for_invocation', (), dict(invocation_ref=invocation, operation_binding_ref=binding), ()),
        ('provider_materialization_events_for_call', (call,), {}, ()),
        ('relation_rows_from_version', (new_id('resource_version'),), {}, ()),
        ('relation_rows_for_version', ('missing',), {}, ()),
        ('object_rows', (), {}, ()),
        ('relation_rows', (), {}, ()),
        ('outbox_rows', (), {}, ()),
    ]


@pytest.mark.parametrize('name,args,kwargs,expected', cases())
def test_empty_or_corrupt_materialized_reads_close(tmp_path, handles, name, args, kwargs, expected):
    store = EventStore(tmp_path / 'db', None)
    count = len(handles)
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            getattr(queries, name)(store, *args, **kwargs)
    else:
        assert getattr(queries, name)(store, *args, **kwargs) == expected
    assert len(handles) == count + 1
    assert all(db.closed for db in handles)
