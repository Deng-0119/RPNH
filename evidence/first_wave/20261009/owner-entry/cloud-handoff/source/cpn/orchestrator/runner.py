"""Run the current Module through its existing sole execution owner."""
from __future__ import annotations

from ..rpnh.control_server import OwnerEventLoop
from ..rpnh.run import RunOwner
from ..rpnh.harness import Harness, HarnessResult


class NativeRunnerReferenceError(RuntimeError):
    """Public import retained; no NativeRun construction path is supported."""


class NativePetriExecutionUnavailableError(RuntimeError):
    """The supplied HOST scheduler services are unavailable."""


class Orchestrator:
    """HOST entry; never opens a Registry or adopts a second writer.

    prepare_dispatcher(*, execution, executable, claimed_inputs, event_loop)
    supplies the optional component dispatcher and its normal HOST gateways.
    submit_operation(callable) schedules its permit on the caller's production
    worker and returns a Future. All Registry I/O from that worker must use the
    supplied event_loop's RegistryGateway bindings.

    The caller owns the owner, event loop, workers and dispatcher services.
    This entry neither creates nor closes them and installs no signal handler.
    Admission, completion draining and terminal/stop policy remain in Harness.
    """

    def __init__(self, *, owner: RunOwner, event_loop: OwnerEventLoop,
                 prepare_dispatcher, submit_operation, select_ready=None,
                 prepare_admission=None, max_in_flight: int = 1) -> None:
        self.executor = Harness(owner=owner, event_loop=event_loop,
            prepare_dispatcher=prepare_dispatcher, submit_operation=submit_operation,
            select_ready=select_ready, prepare_admission=prepare_admission,
            max_in_flight=max_in_flight)

    @property
    def owner(self) -> RunOwner:
        return self.executor.owner

    def run(self) -> HarnessResult:
        return self.executor.exact_execute()

    def request_owner_stop(self) -> None:
        """Wake the existing owner loop; Harness drains its admitted work."""
        self.executor.request_owner_stop()
