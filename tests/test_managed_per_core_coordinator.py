"""The original managed duplicate coordinator is scoped to one live core."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import gc
import weakref

from cpn.plugins.managed_tools import ManagedPluginInvocationService, _ActiveManagedInvocation
from test_managed_plugin_tools import _prepared_invocation


def reconstructed(p):
    return ManagedPluginInvocationService(p['owner'], p['kernel'], p['repository'], p['managed'])


def test_same_core_reconstruction_races_share_one_future_and_identity_leave(tmp_path):
    p = _prepared_invocation(tmp_path)
    services = [reconstructed(p) for _ in range(8)]
    barrier = Barrier(len(services))
    def enter(service):
        barrier.wait(timeout=10)
        return service._enter_active_call('coordinator-test')
    with ThreadPoolExecutor(max_workers=len(services)) as pool:
        entered = list(pool.map(enter, services))
    active = entered[0][0]
    assert sum(owns for _, owns in entered) == 1
    assert all(item is active and item.future is active.future for item, _ in entered)
    services[0]._leave_active_call('coordinator-test', object())
    assert services[1]._enter_active_call('coordinator-test') == (active, False)
    services[2]._leave_active_call('coordinator-test', active)
    assert not services[0]._active_calls


def test_distinct_cores_do_not_serialize_real_registry_read_under_lock(tmp_path):
    first = _prepared_invocation(tmp_path / 'first')['service']
    second = _prepared_invocation(tmp_path / 'second')['service']
    assert first._active_lock is not second._active_lock
    assert first._active_calls is not second._active_calls
    holding, release = Event(), Event()
    def hold_first():
        with first._active_lock:
            # An actual canonical read in the critical section, followed by a
            # controlled slow-read barrier. No worker/model execution.
            first.core.event_store.object_rows_by_type('resource_version/v1')
            holding.set()
            assert release.wait(10)
    def read_second():
        assert holding.wait(10)
        with second._active_lock:
            return second.core.event_store.object_rows_by_type('resource_version/v1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(hold_first)
        independent = pool.submit(read_second)
        try:
            assert independent.result(timeout=5) is not None
        finally:
            release.set()
        blocked.result(timeout=10)
    a, own_a = first._enter_active_call('same-receipt')
    b, own_b = second._enter_active_call('same-receipt')
    assert own_a and own_b and a is not b
    first._leave_active_call('same-receipt', a)
    second._leave_active_call('same-receipt', b)


def test_coordinator_lifetime_has_no_global_retention():
    class Core:
        pass
    core = Core()
    coordinator = ManagedPluginInvocationService._coordinator_for(core)
    # Deliberate active execution/core cycle must still be collectible.
    coordinator.calls[(id(core), 'active')] = _ActiveManagedInvocation(execution=core)
    reference = weakref.ref(core)
    del core, coordinator
    gc.collect()
    assert reference() is None


def test_first_coordinator_creation_is_atomic_across_threads():
    class Core:
        pass
    core = Core()
    barrier = Barrier(8)
    def create(_):
        barrier.wait(timeout=10)
        return ManagedPluginInvocationService._coordinator_for(core)
    with ThreadPoolExecutor(max_workers=8) as pool:
        coordinators = list(pool.map(create, range(8)))
    assert all(item is coordinators[0] for item in coordinators)
    assert core._managed_invocation_coordinator is coordinators[0]
