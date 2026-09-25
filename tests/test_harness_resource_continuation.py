from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

import cpn.components.harness_adapter as adapter
import cpn.rpnh.registry.agent_resource_broker as broker
from cpn.rpnh.harness import Harness, OperationDisposition
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import OperationExecutionAuthority
from cpn.rpnh.registry.resources import TransitionFiringAuthority


def test_adapter_retains_exact_dispatcher_for_resource_resume(
        monkeypatch: pytest.MonkeyPatch) -> None:
    execution = object.__new__(OperationExecutionAuthority)

    class ResourceWait:
        def __init__(self):
            self.execution = execution

    class ExecutionBlock:
        pass

    class TerminalHandoff:
        pass

    monkeypatch.setattr(adapter, "RegisteredOperationResourceWait", ResourceWait)
    monkeypatch.setattr(adapter, "RegisteredOperationExecutionBlock", ExecutionBlock)
    monkeypatch.setattr(adapter, "RegisteredOperationTerminalHandoff", TerminalHandoff)
    wait = ResourceWait()
    block = ExecutionBlock()
    grants = []
    dispatcher = SimpleNamespace(
        permit=SimpleNamespace(dispatch=lambda: wait),
        resume_resource_grant=lambda grant: grants.append(grant) or block,
    )

    disposition = adapter.adapt_registered_dispatcher(
        execution, dispatcher).invoke()
    assert isinstance(disposition, OperationDisposition)
    assert disposition.payload is wait

    grant = object()
    resumed = disposition.resume(grant)
    assert grants == [grant]
    assert isinstance(resumed, OperationDisposition)
    assert resumed.execution is execution
    assert resumed.kind == "execution_block"
    assert resumed.payload is block


def _execution(value: int) -> OperationExecutionAuthority:
    execution = object.__new__(OperationExecutionAuthority)
    lease_ref = VersionRef(
        "operation_execution_lease/v1",
        TypedId("operation_execution_lease", f"{value:032x}"),
        TypedId("operation_execution_lease_version", f"{value + 10:032x}"),
    )
    firing_ref = VersionRef(
        "transition_firing/v1",
        TypedId("transition_firing", f"{value + 20:032x}"),
        TypedId("transition_firing_version", f"{value + 30:032x}"),
    )
    firing = object.__new__(TransitionFiringAuthority)
    object.__setattr__(firing, "transition_firing_ref", firing_ref)
    object.__setattr__(execution, "operation_execution_lease_ref", lease_ref)
    object.__setattr__(execution, "operation", SimpleNamespace(
        firing=firing))
    return execution


def test_harness_resumes_all_currently_grantable_resource_waiters(
        monkeypatch: pytest.MonkeyPatch) -> None:
    executions = (_execution(1), _execution(2))
    grants = tuple(SimpleNamespace(
        operation_execution_lease_ref=(
            execution.operation_execution_lease_ref),
        transition_firing_ref=(
            execution.operation.firing.transition_firing_ref),
    ) for execution in executions)
    resumed = []
    dispositions = tuple(OperationDisposition(
        execution, "resource_wait", payload=object(),
        resume=lambda grant, ordinal=ordinal: resumed.append(
            (ordinal, grant)))
        for ordinal, execution in enumerate(executions))
    promotion_calls = []

    def promote(_core, candidates, *, idempotency_key):
        promotion_calls.append((candidates, idempotency_key))
        grant = grants[len(promotion_calls) - 1]
        assert grant.operation_execution_lease_ref in {
            candidate.operation_execution_lease_ref
            for candidate in candidates}
        return grant

    monkeypatch.setattr(
        broker, "promote_one_waiting_agent_resource", promote)
    submitted = []
    watched = []
    harness = Harness.__new__(Harness)
    harness.owner = SimpleNamespace(_core=object())
    harness.submit_operation = lambda callback: (
        submitted.append(callback) or Future())
    harness.event_loop = SimpleNamespace(
        watch_completion=lambda future, callback: watched.append(
            (future, callback)))
    harness._waiting = {
        execution.operation_execution_lease_ref: disposition
        for execution, disposition in zip(executions, dispositions)}
    harness._pending = {}
    harness._trace = []
    harness._owner_stop_requested = False

    assert harness._resume_resource_waits(
        idempotency_key="test:resume-all") == 2
    assert len(promotion_calls) == 2
    assert len(promotion_calls[0][0]) == 2
    assert len(promotion_calls[1][0]) == 1
    assert not harness._waiting
    assert set(harness._pending) == {
        execution.operation_execution_lease_ref for execution in executions}
    assert len(watched) == 2

    for callback in submitted:
        callback()
    assert resumed == [(0, grants[0]), (1, grants[1])]
