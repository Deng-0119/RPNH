"""Application-side composition of the existing cooperative owner-stop seam."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from cpn.components.execution_services import ExecutionServices
from cpn.rpnh.harness import Harness
from cpn.rpnh.llm_contracts import LLMInputPortInterrupted


class FormalRunStopped(RuntimeError):
    """A child returned a nonterminal Harness result, not a graded answer."""

    def __init__(self, stop_reason: str) -> None:
        super().__init__("formal child did not reach terminal completion")
        self.stop_reason = stop_reason


def execute_child(*, owner, event_loop, llm_input_port,
                  interruption_requested=None):
    """Forward a caller-owned stop probe at admission and input boundaries.

    This does not introduce signal handlers, background retries, or new
    checkpoint routes. An in-flight call is cancellable only if its input port
    supports that contract. Existing durable completions still settle first.
    """
    if interruption_requested is not None and not callable(interruption_requested):
        raise TypeError("interruption_requested must be callable")
    stop = interruption_requested or (lambda: False)
    requested = False
    lock = Lock()
    harness = None

    def probe():
        nonlocal requested
        with lock:
            if not requested and stop():
                requested = True
                harness.request_owner_stop()
            return requested

    services = ExecutionServices(
        owner=owner, event_loop=event_loop, llm_input_port=llm_input_port,
        interruption_requested=probe)
    def prepare_dispatcher(**kwargs):
        probe()
        return services.prepare_dispatcher(**kwargs)

    with ThreadPoolExecutor(max_workers=1) as workers:
        harness = Harness(
            owner=owner, event_loop=event_loop,
            prepare_dispatcher=prepare_dispatcher,
            submit_operation=workers.submit,
            max_in_flight=1)
        probe()
        result = harness.exact_execute()
    if result.stop_reason != "terminal":
        raise FormalRunStopped(result.stop_reason)
    return result


def raise_if_stopped(interruption_requested) -> None:
    if interruption_requested is not None and interruption_requested():
        raise LLMInputPortInterrupted(submission_state="not_submitted")


def child_failure_refs(exc, owner) -> None:
    """Retain known public references without changing the original exception."""
    if owner is not None:
        def ref(value):
            return {"entity_type": value.entity_type,
                    "logical_id": str(value.entity_id),
                    "version_id": str(value.version_id)}
        exc.rrsi_run_refs = {"task_ref": ref(owner.identity.task_ref),
                             "run_ref": ref(owner.identity.run_ref)}


def close_child(*, owner, event_loop, input_port, created_port, primary_error):
    """Attempt all owned cleanup without replacing an earlier child failure."""
    pending = primary_error
    for stage, resource in (("event_loop_close", event_loop),
                            ("input_port_close", input_port if created_port else None)):
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except BaseException as exc:
            if pending is None:
                pending = exc
                child_failure_refs(exc, owner)
            else:
                pending.add_note(f"RRSI secondary {stage} failure: {type(exc).__name__}")
    if primary_error is None and pending is not None:
        raise pending
