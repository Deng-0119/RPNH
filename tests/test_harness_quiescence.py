from __future__ import annotations

from types import SimpleNamespace

from cpn.rpnh.harness import Harness


def test_finite_harness_returns_quiescent_marking_without_spinning() -> None:
    """A dead nonterminal marking is observable, not an infinite owner wait."""
    harness = Harness.__new__(Harness)
    harness.owner = SimpleNamespace(
        admission_paused=False,
        terminal=lambda: None,
    )
    harness.event_loop = SimpleNamespace(
        dispatch_ready=lambda: (_ for _ in ()).throw(
            AssertionError("dead marking must return before dispatch wait")),
    )
    harness._pending = {}
    harness._waiting = {}
    harness._terminal_ref = None
    harness._completion_error = None
    harness._owner_stop_requested = False
    harness._owner_stop_ref = None
    harness._quiescent = False
    harness._terminal_handoff = False
    harness.schedule_ready = lambda: ()
    sentinel = object()
    harness.result = lambda: sentinel

    assert harness.exact_execute() is sentinel
    assert harness._quiescent is True


def test_waiting_harness_returns_blocked_result_without_dispatch_spin() -> None:
    harness = Harness.__new__(Harness)
    harness.owner = SimpleNamespace(
        admission_paused=False,
        terminal=lambda: None,
    )
    harness.event_loop = SimpleNamespace(
        dispatch_ready=lambda: (_ for _ in ()).throw(
            AssertionError("wait without a pending releaser must return")),
    )
    harness._pending = {}
    harness._waiting = {"retained": object()}
    harness._terminal_ref = None
    harness._completion_error = None
    harness._owner_stop_requested = False
    harness._owner_stop_ref = None
    harness._quiescent = False
    harness._terminal_handoff = False
    harness.schedule_ready = lambda: ()
    sentinel = object()
    harness.result = lambda: sentinel

    assert harness.exact_execute() is sentinel
    assert harness._quiescent is False


def test_owner_stop_wins_over_a_racing_worker_product() -> None:
    harness = Harness.__new__(Harness)
    lease = object()
    execution = SimpleNamespace(operation_execution_lease_ref=lease)
    harness._pending = {lease: execution}
    harness._waiting = {}
    harness._owner_stop_requested = True
    harness._completion_error = None
    harness._supports_interruption = lambda candidate: candidate is execution
    settled = object()
    calls = []
    harness._settle_interruption = (
        lambda candidate: calls.append(candidate) or settled)
    future = SimpleNamespace(result=lambda: object())

    assert harness._complete(execution, future) is settled
    assert calls == [execution]
    assert harness._completion_error is None


def test_untracked_registry_firing_reports_reconciliation_required() -> None:
    harness = Harness.__new__(Harness)
    harness.owner = SimpleNamespace(admission_paused=False)
    harness._runtime = lambda: (None, None, object())
    harness._has_unresolved_registry_firing = lambda: True
    harness._terminal_ref = None
    harness._owner_stop_ref = None
    harness._terminal_handoff = False
    harness._completion_error = None
    harness._pending = {}
    harness._waiting = {}
    harness._quiescent = True
    harness._trace = []

    result = harness.result()

    assert result.stop_reason == "reconciliation_required"
