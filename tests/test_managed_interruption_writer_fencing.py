"""Offline managed interruption evidence survives a real Registry writer change.

This exercises the managed/Registry authority boundary, not checkpoint reentry:
a new writer must not revive the old execution's admission authority.
"""
from copy import copy
from threading import Event

import pytest

from cpn.plugins.managed_tools import (
    ManagedPluginInvocationReconciliationRequired,
    ManagedPluginInvocationService,
    ManagedPluginToolCatalog,
    execute_prepared_invocation,
)
from cpn.plugins.worker import WorkerFailure
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.errors import StaleInvocationContext, StaleWriterFence
from test_managed_plugin_tools import (
    _managed_receipts, _plugin_catalog, _prepared_invocation,
)


@pytest.mark.parametrize("terminal_recorded", [False, True],
                         ids=["started-only", "unknown-recorded"])
def test_interrupted_external_write_cannot_regain_authority_after_writer_change(
        tmp_path, monkeypatch, terminal_recorded):
    selected = _plugin_catalog(effect="external_write")
    managed = ManagedPluginToolCatalog(
        selected, {"double_value": "synthetic/double"},
        admitted_effects=("external_write",))
    original = _prepared_invocation(tmp_path, selected=selected, managed=managed)
    service = original["service"]
    stop = Event()
    dispatched = []

    def interrupted_worker(_handler, packet, *, cancelled, **_kwargs):
        # The worker was entered before the owner stop: absence of a return
        # cannot establish that an external write did not happen.
        assert not cancelled()
        dispatched.append(packet["context"]["call_id"])
        stop.set()
        assert cancelled()
        raise WorkerFailure("cancelled_after_start", may_have_executed=True)

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", interrupted_worker)
    arguments = {"value": 4}
    prepared = service.prepare(
        "double_value", execution=original["execution"],
        call_id="interrupted-write", arguments=arguments)
    completion = execute_prepared_invocation(prepared, cancelled=stop.is_set)
    assert completion.error_code == "cancelled_after_start"
    assert completion.may_have_executed

    if terminal_recorded:
        with pytest.raises(ManagedPluginInvocationReconciliationRequired) as unknown:
            service.finish(prepared, completion)
        assert unknown.value.evidence["outcome"] == "outcome_unknown"
        assert unknown.value.evidence["error"] == {
            "code": "cancelled_after_start"}

    before = _managed_receipts(tmp_path / "run")
    assert [item["state"] for item in before] == (
        ["started", "outcome_unknown"] if terminal_recorded else ["started"])
    head = service.core.event_store.max_ordinal()

    # Use the genuine Registry writer acquisition, not a mocked authorization
    # failure. Reconstruct only owner/kernel/repository wrappers for the exact
    # same registration; do not manufacture a replacement execution lease.
    reopened = _RegistryCore(
        service.core.run_dir, create=False, catalog=service.core.catalog)
    assert reopened.writer_epoch > service.core.writer_epoch
    replacement_owner = copy(original["owner"])
    replacement_owner._core = reopened
    kernel, repository = replacement_owner.operation_repository()
    replacement = ManagedPluginInvocationService(
        replacement_owner, kernel, repository, managed)
    assert replacement._active_calls is not service._active_calls

    if not terminal_recorded:
        with pytest.raises(StaleWriterFence, match="writer fence is stale"):
            service.finish(prepared, completion)
        assert not service._active_calls

    # Both stale and newly reconstructed services must reject the old exact
    # execution before either replay or a new call can reach a worker.
    for candidate, failure, message in (
            (service, StaleWriterFence, "writer fence is stale"),
            (replacement, StaleInvocationContext, "another writer epoch")):
        for call_id in ("interrupted-write", "replacement-call"):
            with pytest.raises(failure, match=message):
                candidate.prepare(
                    "double_value", execution=original["execution"],
                    call_id=call_id, arguments=arguments)

    assert dispatched == ["interrupted-write"]
    assert reopened.event_store.max_ordinal() == head
    assert _managed_receipts(tmp_path / "run") == before
