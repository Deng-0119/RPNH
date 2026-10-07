"""Composition tests with fake Harness/Services; never create an owner socket."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rpnh_rrsi.formal_execution import FormalRunStopped, execute_child
from rpnh_rrsi.formal_observation import ObservedInputPort
from cpn.rpnh.llm_contracts import LLMInputPortInterrupted

from test_formal_observation import _SuccessPort, _attempt


@pytest.mark.parametrize("stop_before_run", [False, True])
def test_owner_stop_is_latched_once_and_transport_gets_probe(monkeypatch, stop_before_run):
    import rpnh_rrsi.formal_execution as execution
    state = {"stop": stop_before_run, "requests": 0, "calls": 0, "finished": False}

    class Port(_SuccessPort):
        def request_once(self, _attempt):
            raise AssertionError("interruptible method must be selected")

        def request_once_interruptible(self, attempt, *, interruption_requested):
            state["calls"] += 1
            state["stop"] = True
            assert interruption_requested() is True
            assert interruption_requested() is True
            state["finished"] = True
            raise LLMInputPortInterrupted(submission_state="submission_unknown")

    class Services:
        def __init__(self, *, interruption_requested, llm_input_port, **_kwargs):
            self.probe = interruption_requested
            self.port = llm_input_port

        def prepare_dispatcher(self, **_kwargs):
            def invoke():
                return self.port.request_once_interruptible(
                    _attempt(), interruption_requested=self.probe)
            return invoke

    class FakeHarness:
        def __init__(self, *, prepare_dispatcher, submit_operation, **_kwargs):
            self.prepare = prepare_dispatcher
            self.submit = submit_operation

        def request_owner_stop(self):
            state["requests"] += 1

        def exact_execute(self):
            if not state["stop"]:
                worker = self.submit(self.prepare())
                with pytest.raises(LLMInputPortInterrupted):
                    worker.result(timeout=2)
            return SimpleNamespace(stop_reason="stopped_by_owner")

    monkeypatch.setattr(execution, "ExecutionServices", Services)
    monkeypatch.setattr(execution, "Harness", FakeHarness)
    port = ObservedInputPort(Port())
    with pytest.raises(FormalRunStopped) as caught:
        execute_child(owner=object(), event_loop=object(), llm_input_port=port,
                      interruption_requested=lambda: state["stop"])
    assert caught.value.stop_reason == "stopped_by_owner"
    assert state["requests"] == 1
    assert state["calls"] == (0 if stop_before_run else 1)
    assert state["finished"] is (not stop_before_run)
    if not stop_before_run:
        assert port.observations()[0]["outcome"] == "owner_interrupted"


def test_completed_result_wins_over_racing_stop(monkeypatch):
    import rpnh_rrsi.formal_execution as execution
    terminal = SimpleNamespace(stop_reason="terminal")
    state = {"stop": False}

    class Services:
        def __init__(self, **kwargs):
            self.probe = kwargs["interruption_requested"]

        def prepare_dispatcher(self, **kwargs):
            return None

    class FakeHarness:
        def __init__(self, **kwargs):
            self.prepare = kwargs["prepare_dispatcher"]

        def request_owner_stop(self):
            pass

        def exact_execute(self):
            state["stop"] = True
            self.prepare()
            return terminal

    monkeypatch.setattr(execution, "ExecutionServices", Services)
    monkeypatch.setattr(execution, "Harness", FakeHarness)
    assert execute_child(owner=object(), event_loop=object(), llm_input_port=_SuccessPort(),
                         interruption_requested=lambda: state["stop"]) is terminal


def test_composition_keeps_unclassified_timeout_identity(monkeypatch):
    import rpnh_rrsi.formal_execution as execution
    error = TimeoutError("private detail")

    class Services:
        def __init__(self, **kwargs):
            pass

        def prepare_dispatcher(self, **kwargs):
            pass

    class FakeHarness:
        def __init__(self, **kwargs):
            pass

        def exact_execute(self):
            raise error

    monkeypatch.setattr(execution, "ExecutionServices", Services)
    monkeypatch.setattr(execution, "Harness", FakeHarness)
    with pytest.raises(TimeoutError) as caught:
        execute_child(owner=object(), event_loop=object(), llm_input_port=_SuccessPort())
    assert caught.value is error


@pytest.mark.parametrize("kind", ["policy", "role"])
def test_runners_forward_stop_to_composition_and_preserve_failure_refs(tmp_path, monkeypatch, kind):
    import rpnh_rrsi.formal_policy as policy
    import rpnh_rrsi.formal_role as role
    from test_formal_policy import _request as policy_request, _selection as policy_selection
    from test_formal_role import _request as role_request, _selection as role_selection
    from test_formal_observation import _ref

    module = policy if kind == "policy" else role
    callback = lambda: False
    error = TimeoutError("private detail")
    closed = []
    owner = SimpleNamespace(identity=SimpleNamespace(
        task_ref=_ref("task/v1", "task", "task"),
        run_ref=_ref("run/v1", "run", "run")))

    class EventLoop:
        def __init__(self, actual_owner, _path):
            assert actual_owner is owner

        def close(self):
            closed.append(True)

    def execute(**kwargs):
        assert kwargs["owner"] is owner
        assert kwargs["interruption_requested"] is callback
        raise error

    monkeypatch.setattr(module, "start_run", lambda *a, **k: owner)
    monkeypatch.setattr(module, "OwnerEventLoop", EventLoop)
    monkeypatch.setattr(module, "execute_child", execute)
    with pytest.raises(TimeoutError) as caught:
        if kind == "policy":
            policy._run_policy_trial_unchecked(
                run_dir=tmp_path / "run", request=policy_request(),
                selection=policy_selection(tmp_path), llm_input_port=_SuccessPort(),
                interruption_requested=callback)
        else:
            role.run_role_session(
                run_dir=tmp_path / "run", request=role_request("critic", "stop-0", {
                    "files": [], "traces": [], "component": "policy"}),
                selection=role_selection(tmp_path), llm_input_port=_SuccessPort(),
                interruption_requested=callback)
    assert caught.value is error
    assert error.rrsi_run_refs["run_ref"]["logical_id"].startswith("run:")
    assert closed == [True]
    assert not module._RUNTIME_CONTEXTS
