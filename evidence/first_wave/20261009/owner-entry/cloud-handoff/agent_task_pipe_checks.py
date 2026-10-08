"""Explicit validation-only pipe wake transport; never installed in production.

Uses the unchanged example pipe adapter and hides Orchestrator.executor from
AgentTask so its stop handling must use the shared entry's narrow public method.
"""
import signal
import pytest
from examples.tool_pipeline.tests.pipe_transport import PipeOwnerEventLoop


@pytest.fixture(autouse=True)
def explicit_agent_task_pipe(monkeypatch):
    from cpn.rpnh import agent_tasks
    from cpn.orchestrator.runner import Orchestrator
    loops = []
    entries = []
    previous = signal.getsignal(signal.SIGINT)

    class TrackingLoop(PipeOwnerEventLoop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_count = 0
            loops.append(self)

        def close(self):
            self.close_count += 1
            assert self.close_count == 1
            super().close()

    class EntrySurface:
        def __init__(self, **kwargs):
            self._entry = Orchestrator(**kwargs)
            self.stops = 0
            entries.append(self)

        def run(self):
            return self._entry.run()

        def request_owner_stop(self):
            self.stops += 1
            self._entry.request_owner_stop()

    monkeypatch.setattr(agent_tasks, "OwnerEventLoop", TrackingLoop)
    monkeypatch.setattr(agent_tasks, "Orchestrator", EntrySurface)
    yield
    assert loops and all(loop.close_count == 1 for loop in loops)
    assert entries and any(entry.stops for entry in entries)
    assert signal.getsignal(signal.SIGINT) is previous
