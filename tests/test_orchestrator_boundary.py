"""The existing HOST entry borrows one Harness and no resource lifecycle."""
from types import SimpleNamespace

import pytest

from cpn.orchestrator import runner as runner_module


@pytest.mark.parametrize("fail", [False, True])
def test_entry_forwards_boundaries_without_owning_resources(monkeypatch, fail):
    owner, event_loop, dispatcher, submit, select, admission = (object() for _ in range(6))
    calls = []
    result = object()
    error = RuntimeError("same worker failure")

    def execute():
        calls.append("run")
        if fail:
            raise error
        return result

    executor = SimpleNamespace(owner=owner, exact_execute=execute,
        request_owner_stop=lambda: calls.append("stop"))

    def construct(**kwargs):
        calls.append(kwargs)
        return executor

    def unexpected_signal(*args):
        raise AssertionError("the caller owns signal handlers")

    monkeypatch.setattr(runner_module, "Harness", construct)
    monkeypatch.setattr("signal.signal", unexpected_signal)
    entry = runner_module.Orchestrator(owner=owner, event_loop=event_loop,
        prepare_dispatcher=dispatcher, submit_operation=submit,
        select_ready=select, prepare_admission=admission, max_in_flight=2)
    assert entry.executor is executor and entry.owner is owner
    assert calls == [{"owner": owner, "event_loop": event_loop,
        "prepare_dispatcher": dispatcher, "submit_operation": submit,
        "select_ready": select, "prepare_admission": admission, "max_in_flight": 2}]
    entry.request_owner_stop()
    if fail:
        with pytest.raises(RuntimeError) as caught:
            entry.run()
        assert caught.value is error
    else:
        assert entry.run() is result
    assert calls[1:] == ["stop", "run"]
    # The entry retains no independent queue, completion map or ownership flag.
    assert vars(entry) == {"executor": executor}
