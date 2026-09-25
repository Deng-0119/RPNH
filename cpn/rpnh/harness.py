"""Standalone Registry/Petri execution harness.

The harness owns admission, firing start, completion settlement and terminal
projection. Operation semantics enter only through :class:`OperationDispatch`;
this module imports no executor, AgentLoop, provider, tool, workspace or
workflow implementation.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .marking import TeamNetMarking
from .registry.identities import TypedId
from .registry.models import VersionRef
from .registry.operations import OperationExecutionAuthority, RegisteredOperationOutputsAuthority
from .registry.resources import ResourceVersionRef, TypedMarkingAuthority


class HarnessBoundaryError(RuntimeError):
    """An injected operation boundary disagrees with Registry authority."""


@dataclass(frozen=True, slots=True)
class OperationDispatch:
    """One external operation callback bound to an exact admitted execution."""
    execution: OperationExecutionAuthority
    invoke: Callable[[], "OperationProducts | OperationDisposition"] = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.execution, OperationExecutionAuthority) or not callable(self.invoke):
            raise HarnessBoundaryError("operation dispatch requires exact execution and callable")


@dataclass(frozen=True, slots=True)
class OperationProducts:
    """Externally supplied products already closed by Registry authority."""
    authority: RegisteredOperationOutputsAuthority
    timing_observation: object | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.authority, RegisteredOperationOutputsAuthority):
            raise HarnessBoundaryError("operation products require registered output authority")


@dataclass(frozen=True, slots=True)
class OperationDisposition:
    """A non-success module disposition; never terminal or Success authority."""
    execution: OperationExecutionAuthority
    kind: Literal["resource_wait", "execution_block", "terminal_handoff"]
    payload: object | None = field(default=None, compare=False, repr=False)
    resume: Callable[[object], "OperationProducts | OperationDisposition"] | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.execution, OperationExecutionAuthority)
                or self.kind not in {
                    "resource_wait", "execution_block", "terminal_handoff"}
                or (self.kind == "resource_wait"
                    and (self.payload is None or not callable(self.resume)))
                or (self.kind != "resource_wait" and self.resume is not None)):
            raise HarnessBoundaryError("operation disposition is not exact")


@dataclass(frozen=True, slots=True)
class HarnessResult:
    goal_reached: bool
    stop_reason: str
    marking: TypedMarkingAuthority
    goal_token_refs: tuple[VersionRef, ...]
    goal_resource_refs: tuple[ResourceVersionRef, ...]
    operation_execution_trace: tuple["OperationExecutionTrace", ...]
    terminal_evidence_ref: VersionRef | None = None
    completion_error: BaseException | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class OperationExecutionTrace:
    transition_id: str
    operation_id: str
    execution: OperationExecutionAuthority
    operation_execution_ref: VersionRef
    operation_result_ref: VersionRef
    start_event_id: TypedId
    output_resource_refs: tuple[ResourceVersionRef, ...]
    budget_scope: str
    finalization_scope: str | None
    timing_observation: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        context = self.execution.operation.canonical.context
        if (not self.transition_id or not self.operation_id
                or self.execution.operation_execution_lease_ref != self.operation_execution_ref
                or self.execution.start_event_id != self.start_event_id
                or self.operation_execution_ref.entity_type != "operation_execution_lease/v1"
                or self.operation_result_ref.entity_type != "operation_result/v1"
                or self.transition_id != self.execution.operation.firing.transition_id
                or self.operation_id != self.execution.operation.spec.operation_id
                or self.budget_scope != context.budget_scope
                or self.finalization_scope != context.finalization_scope
                or any(not isinstance(ref, ResourceVersionRef) for ref in self.output_resource_refs)):
            raise HarnessBoundaryError("operation trace lacks exact committed authorities")


class Harness:
    """Execute an adopted typed net on one existing Registry owner/event loop."""

    def __init__(self, *, owner, event_loop, prepare_dispatcher, submit_operation,
                 select_ready=None, prepare_admission=None,
                 max_in_flight: int = 1) -> None:
        from .control_server import OwnerEventLoop
        from .run import RunOwner
        if not isinstance(owner, RunOwner):
            raise TypeError("harness requires the existing RunOwner")
        if not isinstance(event_loop, OwnerEventLoop) or event_loop.owner is not owner:
            raise TypeError("harness requires this owner's event loop")
        if (not callable(prepare_dispatcher) or not callable(submit_operation)
                or (select_ready is not None and not callable(select_ready))
                or (prepare_admission is not None
                    and not callable(prepare_admission))):
            raise TypeError("external operation boundary must be callable")
        if (isinstance(max_in_flight, bool)
                or not isinstance(max_in_flight, int)
                or max_in_flight < 1):
            raise ValueError("max_in_flight must be a positive integer")
        self.owner, self.event_loop = owner, event_loop
        self.prepare_dispatcher, self.submit_operation = prepare_dispatcher, submit_operation
        self.select_ready = select_ready
        self.prepare_admission = prepare_admission
        self.max_in_flight = max_in_flight
        self._pending, self._waiting, self._trace = {}, {}, []
        self._terminal_ref = self._completion_error = None
        self._quiescent = False
        self._terminal_handoff = False
        self._logical_tau = 0
        self._owner_stop_requested = False
        self._owner_stop_ref = None

    def request_owner_stop(self) -> None:
        self._owner_stop_requested = True
        self.event_loop.submit_host(lambda: None)

    def _runtime(self):
        from .registry.module_runtime import hydrate_module_runtime
        from .runtime_net import RuntimeNet
        executable, structure, marking = hydrate_module_runtime(self.owner._core)
        if not isinstance(structure, RuntimeNet):
            raise HarnessBoundaryError("harness requires current typed Petri runtime")
        return executable, structure, marking

    def _has_unresolved_registry_firing(self) -> bool:
        """Report durable active authority not owned by this process loop."""
        core = getattr(self.owner, "_core", None)
        if core is None:
            return False
        from .registry.module_execution import active_module_firings
        executable, _structure, _marking = self._runtime()
        return bool(active_module_firings(core, executable.net_ref))

    @staticmethod
    def _claimed_inputs(execution):
        from .registry.operations import ClaimedPetriInputAuthority, canonical_claimed_petri_inputs
        ports = {item.port_id: item for item in execution.operation.spec.input_ports}
        return canonical_claimed_petri_inputs(tuple(ClaimedPetriInputAuthority(
            token_ref=item.claimed_token_ref, resource_ref=item.resource_ref,
            port=ports[item.port_id], artifact=item.artifact,
            source_resource_ref=item.source_resource_ref,
            substituted_content_schema_id=(
                item.substituted_content_schema_id))
            for item in execution.operation.inputs))

    def schedule_ready(self) -> tuple[VersionRef, ...]:
        """Admit every eligible occurrence; injected code selects no firing."""
        if (self._terminal_ref is not None or self.owner.admission_paused
                or self._completion_error is not None):
            return ()
        capacity = self.max_in_flight - len(self._pending) - len(self._waiting)
        if capacity <= 0:
            return ()
        executable, structure, marking = self._runtime()
        local = TeamNetMarking.from_authority(structure, marking)
        from .registry.module_execution import install_active_module_claims
        active = install_active_module_claims(
            self.owner._core, local, executable.net_ref)
        enabled = tuple(local.enabled_transitions())
        selected = enabled
        if self.select_ready is not None:
            selected = self.select_ready(
                structure=structure,
                marking=marking,
                enabled=enabled,
                active=tuple(item.transition_id for item in active),
            )
            if (not isinstance(selected, tuple)
                    or len(selected) != len(set(selected))
                    or any(not isinstance(item, str) or item not in enabled
                           for item in selected)):
                raise HarnessBoundaryError(
                    "external scheduling policy must return a distinct enabled subset")
        selected = selected[:capacity]
        started = []
        for transition_id in selected:
            command = (f"harness:{executable.net_ref.version_id}:"
                       f"{marking.checkpoint_ref.version_id}:{transition_id}:{self._logical_tau}")
            admitted = self.owner.admit(
                transition_id, logical_tau=self._logical_tau,
                command_id=f"{command}:admit",
                prepare_admission=self.prepare_admission)
            self._logical_tau += 1
            if admitted is None:
                continue
            execution = self.owner.start(admitted, command_id=f"{command}:start")
            dispatch = self.prepare_dispatcher(execution=execution, executable=admitted.executable,
                claimed_inputs=self._claimed_inputs(execution), event_loop=self.event_loop)
            if not isinstance(dispatch, OperationDispatch) or dispatch.execution is not execution:
                raise HarnessBoundaryError("external dispatch differs from exact operation Start")
            self.watch_completion(execution, self.submit_operation(dispatch.invoke))
            started.append(execution.operation_execution_lease_ref)
        return tuple(started)

    def watch_completion(self, execution, future: Future) -> None:
        if not isinstance(execution, OperationExecutionAuthority) or not isinstance(future, Future):
            raise TypeError("completion requires exact execution and Future")
        key = execution.operation_execution_lease_ref
        if key in self._pending or key in self._waiting or any(
                entry.operation_execution_ref == key for entry in self._trace):
            raise HarnessBoundaryError("execution already has a completion handler")
        self._pending[key] = execution
        self.event_loop.watch_completion(future, lambda finished: self._complete(execution, finished))

    def _settle_interruption(self, execution):
        """Return this firing's claimed inputs through its declared stop route."""
        key = execution.operation_execution_lease_ref
        outputs = self.owner.products(
            execution,
            outcome_id="interrupted",
            products={},
            command_id=f"harness:{key.version_id}:interrupted-products",
        )
        successor = self.owner.succeed(
            outputs,
            command_id=f"harness:{key.version_id}:interrupted-success",
        )
        from .registry.publication import _version_from_payload
        record = self.owner._core.event_store.ordered_firing_record(
            execution.operation.firing.transition_firing_ref.version_id)
        result_ref = _version_from_payload(
            record["firing_completion"]["operation_result_ref"])
        context = execution.operation.canonical.context
        self._trace.append(OperationExecutionTrace(
            transition_id=execution.operation.firing.transition_id,
            operation_id=execution.operation.spec.operation_id,
            execution=execution,
            operation_execution_ref=key,
            operation_result_ref=result_ref,
            start_event_id=execution.start_event_id,
            output_resource_refs=(),
            budget_scope=context.budget_scope,
            finalization_scope=context.finalization_scope,
            timing_observation=None,
        ))
        self._pending.pop(key, None)
        self._waiting.pop(key, None)
        self._terminal_ref = self.owner.terminal()
        return successor

    def _supports_interruption(self, execution) -> bool:
        """Check the exact registered operation, without assuming a role."""
        _executable, structure, _marking = self._runtime()
        transition = next(
            item for item in structure.compiled.symbolic.transitions
            if item.name == execution.operation.firing.transition_id)
        operation = next(
            item.declaration for item in structure.compiled.operations
            if item.declaration.name == transition.operation)
        return any(
            outcome.name == "interrupted" for outcome in operation.outcomes)

    def _complete(self, execution, future):
        key = execution.operation_execution_lease_ref
        durable_products = False
        try:
            if self._pending.get(key) is not execution:
                raise HarnessBoundaryError("completion differs from pending execution")
            result = future.result()
            if isinstance(result, OperationProducts):
                # Registered dispatch records this exact completion before the
                # products leave the worker.  Once present, settlement must win
                # over a racing stop; replaying the semantic action is unsafe.
                outputs = result.authority
                if (outputs.execution.operation_execution_lease_ref != key
                        or outputs.execution.start_event_id != execution.start_event_id
                        or outputs.execution.operation.firing != execution.operation.firing):
                    raise HarnessBoundaryError("products belong to another firing")
                durable_products = True
                successor = self.owner.succeed(outputs, command_id=f"harness:{key.version_id}:success")
                from .registry.publication import _version_from_payload
                record = self.owner._core.event_store.ordered_firing_record(
                    execution.operation.firing.transition_firing_ref.version_id)
                result_ref = _version_from_payload(record["firing_completion"]["operation_result_ref"])
                context = execution.operation.canonical.context
                self._trace.append(OperationExecutionTrace(
                    transition_id=execution.operation.firing.transition_id,
                    operation_id=execution.operation.spec.operation_id,
                    execution=execution, operation_execution_ref=key,
                    operation_result_ref=result_ref, start_event_id=execution.start_event_id,
                    output_resource_refs=tuple(item.resource_ref for item in outputs.outputs),
                    budget_scope=context.budget_scope, finalization_scope=context.finalization_scope,
                    timing_observation=result.timing_observation))
                self._pending.pop(key)
                self._terminal_ref = self.owner.terminal()
                if (self._terminal_ref is None
                        and not self._owner_stop_requested):
                    self._resume_resource_waits(
                        idempotency_key=(
                            "harness:resource-wake-after-settlement:"
                            f"{key.version_id}"))
                return successor
            if (self._owner_stop_requested
                    and self._supports_interruption(execution)):
                return self._settle_interruption(execution)
            if not isinstance(result, OperationDisposition) or result.execution is not execution:
                raise HarnessBoundaryError("completion lacks registered products or disposition")
            if result.kind == "terminal_handoff":
                if not self._supports_interruption(execution):
                    raise HarnessBoundaryError(
                        "terminal handoff requires a declared checkpoint route")
                successor = self._settle_interruption(execution)
                self._terminal_handoff = True
                return successor
            self._waiting[key] = result
            self._pending.pop(key)
            self._terminal_ref = self.owner.terminal()
            return result
        except Exception as exc:
            if (not durable_products
                    and self._owner_stop_requested
                    and self._supports_interruption(execution)):
                try:
                    return self._settle_interruption(execution)
                except Exception as interruption_exc:
                    exc = interruption_exc
            self._pending.pop(key, None)
            if self._completion_error is None:
                self._completion_error = exc
            return None

    def _resume_resource_waits(self, *, idempotency_key: str) -> int:
        """Resume every retained wait Registry can currently promote."""
        from .registry.agent_resource_broker import (
            AgentResourceWaitCandidate,
            promote_one_waiting_agent_resource,
        )

        retained_wait_count = sum(
            1 for disposition in self._waiting.values()
            if disposition.kind == "resource_wait")
        resumed = 0
        for promotion_ordinal in range(retained_wait_count):
            if getattr(self, "_owner_stop_requested", False):
                break
            waits = tuple(
                disposition for disposition in self._waiting.values()
                if disposition.kind == "resource_wait")
            if not waits:
                break
            candidates = tuple(AgentResourceWaitCandidate(
                disposition.execution.operation.firing,
                disposition.execution.operation_execution_lease_ref,
            ) for disposition in waits)
            grant = promote_one_waiting_agent_resource(
                self.owner._core, candidates,
                idempotency_key=(
                    f"{idempotency_key}:promotion:{promotion_ordinal}"))
            if grant is None:
                break
            key = grant.operation_execution_lease_ref
            disposition = self._waiting.get(key)
            if (disposition is None
                    or disposition.kind != "resource_wait"
                    or disposition.resume is None
                    or disposition.execution.operation.firing.transition_firing_ref
                    != grant.transition_firing_ref):
                raise HarnessBoundaryError(
                    "promoted resource grant lacks its retained continuation")
            self._waiting.pop(key)
            self.watch_completion(
                disposition.execution,
                self.submit_operation(
                    lambda disposition=disposition, grant=grant:
                    disposition.resume(grant)))
            resumed += 1
        return resumed

    def result(self) -> HarnessResult:
        _, _, marking = self._runtime()
        resources = tokens = ()
        if self._terminal_ref is not None:
            from .registry.strict_contracts import _registered
            from .registry.publication import _resource_from_payload
            _, evidence = _registered(self.owner._core, self._terminal_ref,
                                      "run_terminal_evidence/v1")
            value = evidence["terminal_result_ref"]
            product = _resource_from_payload({"resource_id": value["logical_id"],
                                              "resource_version_id": value["version_id"]})
            resources = (product,)
            tokens = tuple(item.token_ref for item in marking.tokens
                if item.state.consumed_by is None and product in
                (item.state.resource_ref, item.state.work_resource_ref))
        reason = ("terminal" if self._terminal_ref is not None else
            "stopped_by_owner" if self._owner_stop_ref is not None else
            "task_model_call_cap" if self._terminal_handoff else
            "completion_failed" if self._completion_error is not None else
            "waiting_operation" if self._pending else
            "blocked_or_waiting" if self._waiting else
            "reconciliation_required" if self._has_unresolved_registry_firing() else
            "admission_paused" if self.owner.admission_paused else
            "quiescent_marking" if self._quiescent else
            "waiting_owner_commands")
        return HarnessResult(self._terminal_ref is not None, reason, marking, tokens, resources,
            tuple(self._trace), self._terminal_ref, self._completion_error)

    def exact_execute(self) -> HarnessResult:
        self._terminal_ref = self.owner.terminal()
        while ((self._terminal_ref is None and not self._terminal_handoff)
               or self._pending):
            # The first completion failure closes admission immediately, but
            # already-started sibling firings still own worker/gateway state.
            # Drain those completions on the sole owner loop before surfacing
            # the primary failure.
            if self._completion_error is not None and not self._pending:
                raise self._completion_error
            if self._owner_stop_requested and not self._pending:
                for disposition in tuple(self._waiting.values()):
                    if self._supports_interruption(disposition.execution):
                        self._settle_interruption(disposition.execution)
                self._owner_stop_ref = self.owner.record_owner_stop(idempotency_key="launcher-owner-stop")
                return self.result()
            if (not self._owner_stop_requested
                    and self._completion_error is None
                    and not self._terminal_handoff
                    and self._terminal_ref is None):
                started = self.schedule_ready()
                if (not started and not self._pending and self._waiting):
                    # No live completion can release contention. Surface the
                    # retained wait instead of repeatedly polling the owner.
                    return self.result()
                if (not started and not self._pending and not self._waiting
                        and not self.owner.admission_paused):
                    if self._has_unresolved_registry_firing():
                        return self.result()
                    # A finite harness run must return a dead/quiescent marking
                    # to its caller.  The interactive OwnerEventLoop remains
                    # available as a separate API; blocking here would hide a
                    # malformed graph or an exhausted bounded-feedback lane.
                    self._quiescent = True
                    return self.result()
            self.event_loop.dispatch_ready()
            if self._completion_error is not None and not self._pending:
                raise self._completion_error
        return self.result()


__all__ = ("Harness", "HarnessBoundaryError", "HarnessResult", "OperationDispatch",
    "OperationDisposition", "OperationExecutionTrace", "OperationProducts")
