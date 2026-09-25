"""Execution regressions with Registry-backed failure diagnostics.

These tests run actual spawned handlers; no provider or network access is used.
"""
from __future__ import annotations

import json

import pytest

from cpn.plugins.runtime import run_plugin
from cpn.rpnh.registry._registry import _RegistryCore
from test_native_plugins import catalog


def failure_receipts(run_dir):
    core = _RegistryCore(run_dir, create=False, read_only=True)
    results = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        if metadata.get("descriptors", {}).get("phase") == "failed":
            value = core.object_store.read_registered(core.get_version(row["version_id"]))
            results.append(json.loads(value))
    return results


@pytest.mark.parametrize("operation, arguments, expected", [
    ("add", {"x": 3}, 5),
    ("skill", {}, "Use exact registered inputs."),
])
def test_native_success_is_not_an_unexplained_block(tmp_path, operation, arguments, expected):
    run_dir = tmp_path / "run"
    result = run_plugin(catalog(), "test_plugin/" + operation, arguments, run_dir=run_dir)
    failures = failure_receipts(run_dir)
    assert result["stop_reason"] == "terminal", {"result": result, "failures": failures}
    assert result["output"] == expected
    assert not failures


@pytest.mark.parametrize("operation, expected_code", [
    ("wrong", "output_schema_mismatch"),
    ("slow", "deadline_exceeded"),
])
def test_native_failure_has_the_expected_cause(tmp_path, operation, expected_code):
    run_dir = tmp_path / "run"
    result = run_plugin(catalog(), "test_plugin/" + operation, {"x": 1}, run_dir=run_dir)
    failures = failure_receipts(run_dir)
    assert result["terminal_evidence_ref"] is None
    assert len(failures) == 1, failures
    assert failures[0]["code"] == expected_code, failures


class _MissFirstPoll:
    """Force the legal interleaving: first poll misses an in-flight response."""

    def __init__(self, reader):
        self.reader = reader
        self.first = True

    def poll(self, timeout=0):
        if self.first:
            self.first = False
            return False
        return self.reader.poll(timeout)

    def recv_bytes(self, maxlength=None):
        return self.reader.recv_bytes(maxlength)

    def close(self):
        self.reader.close()


@pytest.mark.parametrize("reports_error", [False, True])
def test_response_is_drained_when_worker_exits_during_status_check(monkeypatch, reports_error):
    """A real spawned child finishes between pipe polling and liveness check.

    A missed first poll plus join in the second status callback fixes the
    interleaving without timing sleeps. Both successful and error envelopes
    must be read, rather than replaced by worker_exited_without_result.
    """
    import cpn.plugins.worker as worker
    from cpn.plugins.api import implementation_identity
    from test_native_plugins import add, external_failure

    real = worker.multiprocessing.get_context("spawn")
    children = []

    class CapturedContext:
        def Pipe(self, *, duplex):
            reader, writer = real.Pipe(duplex=duplex)
            return _MissFirstPoll(reader), writer

        def Event(self):
            return real.Event()

        def Process(self, **kwargs):
            process = real.Process(**kwargs)
            children.append(process)
            return process

    monkeypatch.setattr(worker.multiprocessing, "get_context", lambda method: CapturedContext())
    probes = 0

    def cancelled():
        nonlocal probes
        probes += 1
        if probes == 2:
            children[0].join(20)
            assert children[0].exitcode == 0, "spawned worker did not finish the response"
        return False

    handler = external_failure if reports_error else add
    packet = {
        "arguments": {"x": 3}, "max_result_bytes": 1024,
        "implementation": implementation_identity(handler),
        "context": {"config": {"offset": 2}, "resources": (),
                    "operation_id": "test_plugin/add", "invocation_id": "test-invocation",
                    "firing_id": "test-firing"},
    }
    if reports_error:
        with pytest.raises(worker.WorkerFailure, match="^handler_failed$"):
            worker.execute_worker(handler, packet, environment_names=(), timeout_seconds=30,
                                  cancelled=cancelled)
    else:
        assert worker.execute_worker(handler, packet, environment_names=(), timeout_seconds=30,
                                     cancelled=cancelled) == 5
    assert probes >= 2
