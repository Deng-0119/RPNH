from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from cpn.components import default_agent_executor as executor_module
from cpn.components.default_agent_executor import DefaultAgentExecutor


def test_executor_forwards_selected_outcome_to_dispatcher_completion(
        monkeypatch,
) -> None:
    captured = None

    def completion(execution, result, **kwargs):
        nonlocal captured
        captured = (execution, result, kwargs)
        return captured

    monkeypatch.setattr(
        executor_module, "RegisteredOperationCompletion", completion)
    executor = object.__new__(DefaultAgentExecutor)
    authority = object()
    result = object()
    executor._execution = authority

    returned = executor._close_execution_result(
        authority, result,
        timing_observation="timing",
        selected_outcome_id="completed")

    assert returned is captured
    assert captured == (
        authority,
        result,
        {
            "generic_critic_selection": None,
            "timing_observation": "timing",
            "selected_outcome_id": "completed",
        },
    )


def _workspace_execution():
    return SimpleNamespace(operation=SimpleNamespace(
        firing=SimpleNamespace(
            transition_id="team.final_delivery",
            transition_firing_ref=SimpleNamespace(
                version_id="transition-firing-version"),
        )))


def test_workspace_finalization_retries_one_transient_sqlite_io_error() -> None:
    calls = []

    def finalize(execution, loop, *, idempotency_key):
        calls.append((execution, loop, idempotency_key))
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")

    executor = object.__new__(DefaultAgentExecutor)
    executor._registry = SimpleNamespace(
        finalize_agent_workspace_v1=finalize)
    execution = _workspace_execution()
    loop = object()

    executor._finalize_workspace(execution, loop)

    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_workspace_finalization_io_retry_is_strictly_bounded() -> None:
    calls = []

    def finalize(execution, loop, *, idempotency_key):
        calls.append((execution, loop, idempotency_key))
        raise sqlite3.OperationalError("disk I/O error")

    executor = object.__new__(DefaultAgentExecutor)
    executor._registry = SimpleNamespace(
        finalize_agent_workspace_v1=finalize)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        executor._finalize_workspace(_workspace_execution(), object())

    assert len(calls) == 2
