"""Bounded managed workers with all Registry callbacks on the caller's gateway.

The run owns one capacity pool. A turn owns an ordered, complete result vector;
this module never settles a turn or creates an AgentLoop successor.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from threading import Condition
from typing import Any, Callable, Mapping

from .api import PluginError, frozen
from .managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationReconciliationRequired,
    ManagedWorkerCompletion, PreparedManagedInvocation, execute_prepared_invocation,
)


@dataclass(frozen=True, slots=True)
class ManagedConflictDomain:
    """Trusted HOST declaration bound to an exact registered tool, not a name."""

    registration_key: str
    effect: str
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    unknown: bool = False

    def __post_init__(self):
        if not isinstance(self.registration_key, str) or not self.registration_key:
            raise PluginError("conflict declaration needs an exact registration key")
        if self.effect not in {"pure", "external_read", "external_write"}:
            raise PluginError("conflict declaration effect is unsupported")
        if type(self.unknown) is not bool:
            raise PluginError("unknown conflict flag must be boolean")
        for name in ("reads", "writes"):
            values = getattr(self, name)
            if (not isinstance(values, (tuple, list))
                    or any(not isinstance(v, str) or not v for v in values)
                    or len(set(values)) != len(values)):
                raise PluginError("conflict domains must be explicit unique domain IDs")
            object.__setattr__(self, name, tuple(sorted(values)))
        if self.effect == "pure" and (self.reads or self.writes or self.unknown):
            raise PluginError("pure conflict declaration cannot carry external domains")
        if not self.unknown and (
                self.effect == "external_read" and (not self.reads or self.writes)
                or self.effect == "external_write" and not self.writes):
            raise PluginError("external effects require explicit matching conflict domains")

    def document(self):
        return {"registration_key": self.registration_key, "effect": self.effect,
                "reads": list(self.reads), "writes": list(self.writes),
                "unknown": self.unknown}


@dataclass(frozen=True, slots=True)
class _Access:
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    unknown: bool = False

    def conflicts(self, other):
        return (self.unknown or other.unknown
                or bool(set(self.writes).intersection(other.reads + other.writes))
                or bool(set(self.reads).intersection(other.writes)))


@dataclass(frozen=True, slots=True)
class ManagedSchedulerPolicy:
    policy_id: str = "managed_pure_parallel/v1"
    max_in_flight: int = 2
    conflict_domains: tuple[ManagedConflictDomain, ...] = ()

    def __post_init__(self):
        if self.policy_id not in {"managed_pure_parallel/v1", "managed_conflict_domains/v1"}:
            raise PluginError("unsupported managed scheduler policy")
        if type(self.max_in_flight) is not int or self.max_in_flight < 1:
            raise PluginError("managed turn capacity must be a positive integer")
        entries = tuple(self.conflict_domains)
        if (any(not isinstance(v, ManagedConflictDomain) for v in entries)
                or len({v.registration_key for v in entries}) != len(entries)
                or self.policy_id == "managed_pure_parallel/v1" and entries):
            raise PluginError("conflict policy requires unique exact declarations")
        object.__setattr__(self, "conflict_domains", tuple(sorted(
            entries, key=lambda v: v.registration_key)))

    def identity(self):
        value = {"policy_id": self.policy_id, "max_in_flight": self.max_in_flight}
        if self.policy_id == "managed_conflict_domains/v1":
            value["conflict_domains"] = [v.document() for v in self.conflict_domains]
        return value

    @property
    def admitted_effects(self):
        return (("pure",) if self.policy_id == "managed_pure_parallel/v1"
                else ("pure", "external_read", "external_write"))

    def validate_declaration(self, declaration):
        if declaration.protocol_version != "v2" or declaration.effect not in self.admitted_effects:
            raise PluginError("managed declaration is outside the scheduler policy")
        for entry in self.conflict_domains:
            if entry.registration_key == declaration.registration_key and entry.effect != declaration.effect:
                raise PluginError("trusted conflict effect differs from exact tool registration")

    def _access(self, call):
        if self.policy_id == "managed_pure_parallel/v1":
            return _Access()
        for entry in self.conflict_domains:
            if entry.registration_key == call.registration_key:
                return _Access(entry.reads, entry.writes, entry.unknown)
        # No declared conflict information means an exclusive barrier, including
        # with independently pending work. Never infer domains from tool names.
        return _Access(unknown=True)


@dataclass(frozen=True, slots=True)
class ManagedToolCall:
    ordinal: int
    name: str
    call_id: str
    arguments: Mapping[str, Any]
    registration_key: str | None = None

    def __post_init__(self):
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise PluginError("managed ordinal must be a nonnegative integer")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise PluginError("managed call ID must be nonempty")
        object.__setattr__(self, "arguments", frozen(self.arguments))


@dataclass(frozen=True, slots=True)
class ManagedToolOutcome:
    ordinal: int
    call_id: str
    result: Mapping[str, Any] | None = None
    error: BaseException | None = None
    not_started: bool = False


@dataclass(frozen=True, slots=True)
class ManagedToolBatchResult:
    outcomes: tuple[ManagedToolOutcome, ...]
    reconciliation_required: bool
    cancelled: bool


class ManagedRunCapacity:
    """One shared run pool, including simultaneous firings and turn observers."""

    def __init__(self, max_in_flight: int):
        if type(max_in_flight) is not int or max_in_flight < 1:
            raise PluginError("managed run capacity must be a positive integer")
        self.max_in_flight = max_in_flight
        self._condition = Condition()
        self._used = 0
        self._leases: dict[object, _Access] = {}
        self._closed = False
        self._blocked: set[str] = set()
        self._pool = ThreadPoolExecutor(
            max_workers=max_in_flight, thread_name_prefix="managed-worker")

    def _reserve(self, access):
        with self._condition:
            if self._closed:
                raise RuntimeError("managed run capacity is closed")
            if (self._used == self.max_in_flight
                    or any(access.conflicts(other) for other in self._leases.values())):
                return None
            token = object()
            self._leases[token] = access
            self._used += 1
            return token

    def _release(self, token):
        with self._condition:
            self._leases.pop(token)
            self._used -= 1
            self._condition.notify_all()

    def _block(self, operation_key):
        with self._condition:
            self._blocked.add(operation_key)
            self._condition.notify_all()

    def _is_blocked(self, operation_key):
        with self._condition:
            return operation_key in self._blocked

    def _execute(self, operation_key, prepared, cancelled):
        # This check also covers admitted work not yet started when a sibling
        # becomes unknown. No automatic recovery dispatch is offered.
        if self._is_blocked(operation_key) or cancelled():
            return ManagedWorkerCompletion(
                error_code="cancelled_before_start", may_have_executed=False)
        return execute_prepared_invocation(prepared, cancelled=cancelled)

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._pool.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ManagedToolScheduler:
    def __init__(self, policy: ManagedSchedulerPolicy, capacity: ManagedRunCapacity):
        if not isinstance(policy, ManagedSchedulerPolicy) or not isinstance(capacity, ManagedRunCapacity):
            raise TypeError("scheduler requires an exact policy and shared run capacity")
        self.policy, self.capacity = policy, capacity

    def run(self, calls, *, operation_key: str, prepare: Callable,
            finish: Callable, cancelled: Callable = lambda: False):
        """Run outside owner. prepare/finish MUST cross the same owner gateway.

        The prepare adapter must enforce policy.admitted_effects BEFORE calling
        service.prepare. It also binds and validates exact action/turn identity.
        """
        calls = tuple(calls)
        if (not isinstance(operation_key, str) or not operation_key
                or any(not isinstance(c, ManagedToolCall) for c in calls)
                or len({c.ordinal for c in calls}) != len(calls)
                or len({c.call_id for c in calls}) != len(calls)):
            raise PluginError("batch requires unique exact ordinals and call IDs")
        pending = list(sorted(calls, key=lambda c: c.ordinal))
        running: dict[Future, tuple[ManagedToolCall, PreparedManagedInvocation | None, object | None]] = {}
        outcomes = {}
        was_cancelled = False
        unknown = self.capacity._is_blocked(operation_key)

        def record(call, result=None, error=None, not_started=False):
            nonlocal unknown
            outcomes[call.ordinal] = ManagedToolOutcome(
                call.ordinal, call.call_id, result, error, not_started)
            if isinstance(error, ManagedPluginInvocationReconciliationRequired):
                unknown = True
                self.capacity._block(operation_key)

        while pending or running:
            was_cancelled = was_cancelled or bool(cancelled())
            unknown = unknown or self.capacity._is_blocked(operation_key)
            if was_cancelled or unknown:
                for call in pending:
                    record(call, error=PluginError(
                        "managed call not started: reconciliation required" if unknown
                        else "managed call not started: cancelled"), not_started=True)
                pending.clear()

            # Drain ALL ready siblings before considering another admission.
            completed = [f for f in running if f.done()]
            for future in completed:
                call, prepared, reserved = running.pop(future)
                try:
                    if prepared is None:
                        result = future.result()
                    else:
                        try:
                            completion = future.result()
                        except BaseException:
                            completion = ManagedWorkerCompletion(
                                error_code="worker_observation_lost", observation_lost=True)
                        result = finish(prepared, completion)
                    record(call, result=result)
                except BaseException as exc:
                    record(call, error=exc)
                    # A claimed invocation without a durable terminal must also
                    # block further admissions, even if finish itself failed.
                    if prepared is not None and not getattr(exc, "evidence", None):
                        unknown = True
                        self.capacity._block(operation_key)
                finally:
                    if reserved:
                        self.capacity._release(reserved)
            if completed:
                continue

            selected = None
            reserved = None
            if pending and len(running) < self.policy.max_in_flight and not unknown and not was_cancelled:
                earlier = []
                for index, candidate in enumerate(pending):
                    access = self.policy._access(candidate)
                    # Preserve order among conflicting pending items while
                    # allowing unrelated domains past a temporarily busy one.
                    if not any(access.conflicts(other) for other in earlier):
                        reserved = self.capacity._reserve(access)
                        if reserved is not None:
                            selected = index
                            break
                    earlier.append(access)
            if selected is not None:
                call = pending.pop(selected)
                retained = False
                try:
                    prepared = prepare(call)
                    if isinstance(prepared, ManagedInvocationObservation):
                        running[prepared.future] = (call, None, None)
                    elif isinstance(prepared, PreparedManagedInvocation):
                        if (prepared.effect not in self.policy.admitted_effects
                                or prepared.protocol_version != "v2"):
                            completion = ManagedWorkerCompletion(
                                error_code="scheduler_policy_not_admitted", may_have_executed=False)
                            record(call, result=finish(prepared, completion))
                        else:
                            try:
                                future = self.capacity._pool.submit(
                                    self.capacity._execute, operation_key, prepared, cancelled)
                            except Exception:
                                record(call, result=finish(prepared, ManagedWorkerCompletion(
                                    error_code="worker_not_submitted", may_have_executed=False)))
                            else:
                                running[future] = (call, prepared, reserved)
                                retained = True
                    else:
                        raise TypeError("prepare must return admitted work or observation")
                except BaseException as exc:
                    record(call, error=exc)
                finally:
                    if not retained:
                        self.capacity._release(reserved)
                continue

            if running:
                wait(running, timeout=0.05, return_when=FIRST_COMPLETED)
            elif pending:
                with self.capacity._condition:
                    self.capacity._condition.wait(timeout=0.05)

        return ManagedToolBatchResult(
            tuple(outcomes[c.ordinal] for c in sorted(calls, key=lambda c: c.ordinal)),
            unknown, was_cancelled)
