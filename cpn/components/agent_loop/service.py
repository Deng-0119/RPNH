"""Agent-loop coordinator over a Registry-owned persistence port.

This module owns no authoritative mutable state.  Every returned snapshot,
turn, action and compaction must have been committed by the supplied Registry
port.  The port is deliberately explicit so shared Registry wiring can be
added serially without a path, sidecar, process-global or in-memory authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, Protocol, runtime_checkable

from cpn.rpnh.llm_contracts import (
    LLMCallAttempt,
    LLMInputPort,
    LLMInputPortFailure,
    LLMInputPortInterrupted,
    LLMInputTarget,
)
from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError,
    canonicalize_llm_response_payload,
)
from cpn.rpnh.registry.event_store import TaskModelCallLimitExceeded
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
from cpn.rpnh.registry.resources import ResourceVersionRef
from .models import (
    AgentActionRecord,
    AgentCapReviewCompletion,
    AgentOperationTimingEvidence,
    AgentTurnTimingEvidence,
    AgentLoopCompletion,
    AgentLoopExecutionBlock,
    AgentLoopInterruptionCheckpoint,
    AgentLoopProtocolError,
    AgentLoopResourceWait,
    AgentLoopSnapshot,
    AgentLoopState,
    AgentSystemInitialization,
    AgentTaskModelCallCapHandoffCompletion,
    AgentTurnRecord,
    TERMINAL_STATES,
    VerifiedAgentLoopResourceGrant,
    require_state_transition,
    record_timing_offset,
)
from .tools import (
    AgentToolCatalog,
    ValidatedAgentToolAction,
    build_agent_tool_catalog,
    workspace_execution_mode,
)
from .compact import ContextPressurePolicy, ContextReductionSettings
from .delegated_history import compact_delegated_subtask_history_after_length

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartAgentLoopCommand:
    loop_id: str
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    owner_ref: VersionRef
    model_condition: str
    llm_turn_budget: int
    tool_catalog: AgentToolCatalog
    registered_tool_catalog_ref: ResourceVersionRef
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CompletedAgentLLMInvocation:
    attempt: LLMCallAttempt
    response_bytes: bytes
    waiting_loop: AgentLoopSnapshot | None = field(
        default=None, compare=False, repr=False)
    timing_evidence: AgentTurnTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.attempt, LLMCallAttempt)
                or not isinstance(self.response_bytes, bytes)
                or not self.response_bytes):
            raise TypeError(
                "completed agent LLM invocation requires a successful result")
        if (self.waiting_loop is not None
                and (not isinstance(self.waiting_loop, AgentLoopSnapshot)
                     or self.waiting_loop.state
                     != AgentLoopState.WAITING_FOR_LLM)):
            raise TypeError("published turn requires its exact waiting loop head")


@dataclass(frozen=True, slots=True)
class ParentOwnedDelegatedSubtaskRequest:
    """One non-recursive local subtask annotated under a parent action."""

    action_id: str
    parent_invocation_ref: VersionRef
    sponsoring_transition_firing_ref: VersionRef
    accounting_parent_invocation_ref: VersionRef
    parent_operation_binding_ref: VersionRef
    parent_operation_execution_lease_ref: VersionRef
    parent_a2c_activation_ref: VersionRef | None
    parent_budget_witness_ref: VersionRef | None
    parent_agent_loop_ref: VersionRef
    parent_agent_turn_ref: VersionRef
    parent_action_ref: VersionRef
    tool_call_id: str
    tool_call_ordinal: int
    raw_arguments: str
    budget_scope: str | None
    finalization_scope: str | None
    instruction: str
    readable_resource_refs: tuple[ResourceVersionRef, ...]
    discoverable_resource_refs: tuple[ResourceVersionRef, ...]
    parent_readable_resource_refs: tuple[ResourceVersionRef, ...]
    parent_discoverable_resource_refs: tuple[ResourceVersionRef, ...]
    allowed_tool_names: tuple[str, ...]
    parent_tool_names: tuple[str, ...]
    leaf_turn_limit: int
    child_session_id: str
    result_resource_ref: ResourceVersionRef
    llm_invocation_ref: VersionRef | None = None
    llm_invocation_attempt_ref: VersionRef | None = None

    def __post_init__(self) -> None:
        parent_refs = (
            self.parent_invocation_ref,
            self.accounting_parent_invocation_ref,
        )
        if (not isinstance(self.action_id, str) or not self.action_id
                or any(not isinstance(ref, VersionRef) for ref in parent_refs)
                or self.accounting_parent_invocation_ref
                != self.parent_invocation_ref
                or not isinstance(self.parent_agent_loop_ref, VersionRef)
                or self.parent_agent_loop_ref.entity_type != "agent_loop/v1"
                or not isinstance(self.parent_agent_turn_ref, VersionRef)
                or self.parent_agent_turn_ref.entity_type != "agent_turn/v1"
                or not isinstance(self.parent_action_ref, VersionRef)
                or self.parent_action_ref.entity_type != "agent_action/v2"
                or not isinstance(self.instruction, str)
                or not self.instruction.strip()
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in self.readable_resource_refs)
                or self.discoverable_resource_refs
                != self.readable_resource_refs
                or "delegate_leaf" in self.allowed_tool_names
                or isinstance(self.leaf_turn_limit, bool)
                or not isinstance(self.leaf_turn_limit, int)
                or self.leaf_turn_limit < 1
                or not isinstance(self.child_session_id, str)
                or not self.child_session_id
                or not isinstance(
                    self.result_resource_ref, ResourceVersionRef)):
            raise TypeError("parent-owned delegated subtask request is invalid")
        canonical_call_refs = (
            self.llm_invocation_ref,
            self.llm_invocation_attempt_ref,
        )
        if any(ref is not None for ref in canonical_call_refs):
            if (not isinstance(self.llm_invocation_ref, VersionRef)
                    or self.llm_invocation_ref.entity_type
                    != "llm_invocation_spec/v1"
                    or not isinstance(
                        self.llm_invocation_attempt_ref, VersionRef)
                    or self.llm_invocation_attempt_ref.entity_type
                    != "llm_invocation_attempt/v1"):
                raise TypeError(
                    "delegated subtask canonical call refs are incomplete")


@dataclass(frozen=True, slots=True)
class ParentOwnedDelegatedSubtaskToolStep:
    """One provisional local tool step; it owns no AgentLoop state."""

    leaf_turn_sequence: int
    llm_invocation_ref: VersionRef
    llm_invocation_attempt_ref: VersionRef
    response_resource_ref: ResourceVersionRef
    tool_step_evidence_ref: ResourceVersionRef
    history_messages: tuple[Mapping[str, Any], ...] = field(
        compare=False, repr=False)
    result_candidate_ref: ResourceVersionRef | None = None

    def __post_init__(self) -> None:
        if (isinstance(self.leaf_turn_sequence, bool)
                or not isinstance(self.leaf_turn_sequence, int)
                or self.leaf_turn_sequence < 0
                or not isinstance(self.llm_invocation_ref, VersionRef)
                or self.llm_invocation_ref.entity_type
                != "llm_invocation_spec/v1"
                or not isinstance(
                    self.llm_invocation_attempt_ref, VersionRef)
                or self.llm_invocation_attempt_ref.entity_type
                != "llm_invocation_attempt/v1"
                or not isinstance(
                    self.response_resource_ref, ResourceVersionRef)
                or not isinstance(
                    self.tool_step_evidence_ref, ResourceVersionRef)
                or not self.history_messages
                or any(not isinstance(message, Mapping)
                       for message in self.history_messages)
                or (self.result_candidate_ref is not None
                    and not isinstance(
                        self.result_candidate_ref, ResourceVersionRef))):
            raise TypeError("delegated subtask tool step is invalid")


@dataclass(frozen=True, slots=True)
class ParentOwnedDelegatedSubtaskResult:
    """Terminal local result awaiting attachment to the parent action."""

    parent_invocation_ref: VersionRef
    sponsoring_transition_firing_ref: VersionRef
    accounting_parent_invocation_ref: VersionRef
    parent_operation_binding_ref: VersionRef
    parent_operation_execution_lease_ref: VersionRef
    parent_agent_loop_ref: VersionRef
    parent_agent_turn_ref: VersionRef
    parent_action_ref: VersionRef
    action_id: str
    tool_call_id: str
    tool_call_ordinal: int
    raw_arguments: str
    llm_invocation_ref: VersionRef
    llm_invocation_attempt_ref: VersionRef
    result_resource_ref: ResourceVersionRef
    local_sequence: int
    child_session_id: str


@dataclass(frozen=True, slots=True)
class ParentOwnedDelegatedSubtaskLengthReplay:
    """Process-local child-history replacement after one length stop."""

    local_sequence: int
    history_messages: tuple[Mapping[str, Any], ...] = field(
        compare=False, repr=False)

    def __post_init__(self) -> None:
        if (isinstance(self.local_sequence, bool)
                or not isinstance(self.local_sequence, int)
                or self.local_sequence < 0
                or not self.history_messages
                or any(not isinstance(message, Mapping)
                       for message in self.history_messages)):
            raise TypeError("delegated length replay state is invalid")


@dataclass(slots=True)
class _PreparedParentOwnedDelegatedSubtaskContext:
    """Request-local parent authority exposed only during one local call."""

    parent_context: object
    loop: AgentLoopSnapshot
    parent_loop_ref: VersionRef
    leaf_turn_sequence: int
    leaf_request: ParentOwnedDelegatedSubtaskRequest
    callsite: Mapping[str, Any]
    target: LLMInputTarget
    target_ref: ResourceVersionRef
    target_prepared: object
    request_ref: ResourceVersionRef
    request_prepared: object
    request_payload: bytes
    prompt_ref: ResourceVersionRef
    prompt_prepared: object
    prompt_payload: bytes
    tool_catalog_ref: ResourceVersionRef
    tool_catalog_prepared: object
    tool_catalog_payload: bytes


@dataclass(frozen=True, slots=True)
class PreparedParentOwnedDelegatedSubtaskCall:
    """One prepared provider call in a local parent-owned subtask session."""

    request: ParentOwnedDelegatedSubtaskRequest
    attempt: LLMCallAttempt
    prepared_context: _PreparedParentOwnedDelegatedSubtaskContext = field(
        compare=False, repr=False)
    leaf_turn_sequence: int
    history_messages: tuple[Mapping[str, Any], ...] = field(
        compare=False, repr=False)
    prior_llm_invocation_refs: tuple[VersionRef, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.request, ParentOwnedDelegatedSubtaskRequest)
                or not isinstance(self.attempt, LLMCallAttempt)
                or not isinstance(
                    self.prepared_context,
                    _PreparedParentOwnedDelegatedSubtaskContext)
                or self.request.llm_invocation_ref
                != self.attempt.invocation_ref
                or self.request.llm_invocation_attempt_ref
                != self.attempt.attempt_ref
                or any(not isinstance(message, Mapping)
                       for message in self.history_messages)
                or len(self.prior_llm_invocation_refs)
                != self.leaf_turn_sequence
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "llm_invocation_spec/v1"
                       for ref in self.prior_llm_invocation_refs)):
            raise TypeError("prepared delegated subtask call is invalid")


@dataclass(frozen=True, slots=True)
class AgentLengthInterruptionRecord:
    """Immutable evidence for one length-stopped semantic work item."""

    loop_id: str
    sequence: int
    llm_invocation_ref: VersionRef
    llm_invocation_attempt_ref: VersionRef
    response_resource_ref: ResourceVersionRef
    response_size: int
    model_condition: str
    recorded_revision: int

    def __post_init__(self) -> None:
        if (not isinstance(self.loop_id, str) or not self.loop_id
                or isinstance(self.sequence, bool)
                or not isinstance(self.sequence, int) or self.sequence < 0
                or not isinstance(self.llm_invocation_ref, VersionRef)
                or self.llm_invocation_ref.entity_type
                != "llm_invocation_spec/v1"
                or not isinstance(self.llm_invocation_attempt_ref, VersionRef)
                or self.llm_invocation_attempt_ref.entity_type
                != "llm_invocation_attempt/v1"
                or not isinstance(
                    self.response_resource_ref, ResourceVersionRef)
                or isinstance(self.response_size, bool)
                or not isinstance(self.response_size, int)
                or self.response_size < 1
                or not isinstance(self.model_condition, str)
                or not self.model_condition
                or isinstance(self.recorded_revision, bool)
                or not isinstance(self.recorded_revision, int)
                or self.recorded_revision < 1):
            raise TypeError("length interruption authority is invalid")


@dataclass(frozen=True, slots=True)
class PreparedAgentLLMTurn:
    attempt: LLMCallAttempt

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, LLMCallAttempt):
            raise TypeError("prepared agent LLM invocation is invalid")


@dataclass(slots=True)
class PreparedAgentTurnContext:
    """One request-local Registry preparation shared by one LLM turn."""

    loop: AgentLoopSnapshot
    execution: object = field(compare=False, repr=False)
    initialization: AgentSystemInitialization
    located_inputs: tuple[Mapping[str, object], ...] = field(
        compare=False, repr=False)
    located_payloads: tuple[bytes, ...] = field(compare=False, repr=False)
    prior_turn_refs: tuple[VersionRef, ...] = field(
        compare=False, repr=False)
    semantic_context_turn_refs: tuple[VersionRef, ...] = field(
        compare=False, repr=False)
    target: LLMInputTarget = field(compare=False, repr=False)
    target_ref: ResourceVersionRef
    target_prepared: object = field(compare=False, repr=False)
    prompt_ref: ResourceVersionRef
    prompt_prepared: object = field(compare=False, repr=False)
    prompt_payload: bytes = field(compare=False, repr=False)
    hydrated_registry_index_refs: tuple[ResourceVersionRef, ...] = field(
        compare=False, repr=False)
    tool_catalog_prepared: object = field(compare=False, repr=False)
    tool_catalog_payload: bytes = field(compare=False, repr=False)
    prior_turn_messages_payload: bytes = field(compare=False, repr=False)
    prior_turn_closure_payload: bytes = field(compare=False, repr=False)
    forced_compaction_reason: str | None = field(
        default=None, compare=False, repr=False)
    request_ref: ResourceVersionRef | None = field(
        default=None, compare=False, repr=False)
    request_prepared: object | None = field(
        default=None, compare=False, repr=False)
    request_payload: bytes | None = field(
        default=None, compare=False, repr=False)
    terminal_delivery_ref: VersionRef | None = field(
        default=None, compare=False, repr=False)
    delivery_prepared: object | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.WAITING_FOR_LLM
                or not isinstance(
                    self.initialization, AgentSystemInitialization)
                or any(not isinstance(item, Mapping)
                       for item in self.located_inputs)
                or any(not isinstance(item, bytes)
                       for item in self.located_payloads)
                or len(self.located_inputs) != len(self.located_payloads)
                or any(not isinstance(item, VersionRef)
                       or item.entity_type != "agent_turn/v1"
                       for item in self.prior_turn_refs)
                or any(not isinstance(item, VersionRef)
                       or item.entity_type != "agent_turn/v1"
                       for item in self.semantic_context_turn_refs)
                or len(set(self.semantic_context_turn_refs))
                != len(self.semantic_context_turn_refs)
                or not isinstance(self.target, LLMInputTarget)
                or not isinstance(self.target_ref, ResourceVersionRef)
                or not isinstance(self.prompt_ref, ResourceVersionRef)
                or not isinstance(self.prompt_payload, bytes)
                or not self.prompt_payload
                or any(not isinstance(item, ResourceVersionRef)
                       for item in self.hydrated_registry_index_refs)
                or len(set(self.hydrated_registry_index_refs))
                != len(self.hydrated_registry_index_refs)
                or not isinstance(self.tool_catalog_payload, bytes)
                or not self.tool_catalog_payload
                or not isinstance(self.prior_turn_messages_payload, bytes)
                or not isinstance(self.prior_turn_closure_payload, bytes)
                or self.forced_compaction_reason not in {
                    None, "response_length"}):
            raise TypeError("prepared agent turn context is invalid")


@dataclass(slots=True)
class PreparedAgentContextCompaction:
    """Neutral dispatch authority for one current compaction attempt."""

    attempt: LLMCallAttempt
    loop: AgentLoopSnapshot
    compaction_id: str
    trigger_reason: str
    force: bool
    reduction_settings: ContextReductionSettings
    pressure_policy: ContextPressurePolicy | None
    semantic_context_turn_refs: tuple[VersionRef, ...]
    subject_agent_ref: VersionRef
    execution_agent_ref: VersionRef
    interrupted_invocation_ref: VersionRef | None = None
    interrupted_invocation_attempt_ref: VersionRef | None = None
    interrupted_response_resource_ref: ResourceVersionRef | None = None

    def __post_init__(self) -> None:
        compaction_suffix = (
            self.compaction_id.removeprefix("agent_context_compaction:")
            if isinstance(self.compaction_id, str) else "")
        if (not isinstance(self.attempt, LLMCallAttempt)
                or not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.COMPACTING
                or not isinstance(self.compaction_id, str)
                or not self.compaction_id.startswith(
                    "agent_context_compaction:")
                or len(compaction_suffix) != 32
                or any(character not in "0123456789abcdef"
                       for character in compaction_suffix)
                or self.trigger_reason not in {
                    "context_pressure", "response_length", "turn_cap"}
                or not isinstance(self.force, bool)
                or self.force != (self.trigger_reason in {
                    "response_length", "turn_cap"})
                or not isinstance(
                    self.reduction_settings, ContextReductionSettings)
                or (self.pressure_policy is not None
                    and not isinstance(
                        self.pressure_policy, ContextPressurePolicy))
                or (self.trigger_reason == "context_pressure"
                    and self.pressure_policy is None)
                or not isinstance(self.subject_agent_ref, VersionRef)
                or self.subject_agent_ref.entity_type != "agent/v1"
                or not isinstance(self.execution_agent_ref, VersionRef)
                or self.execution_agent_ref.entity_type != "agent/v1"):
            raise TypeError("prepared context compaction authority is invalid")
        interruption = (
            self.interrupted_invocation_ref,
            self.interrupted_invocation_attempt_ref,
            self.interrupted_response_resource_ref,
        )
        if self.trigger_reason == "response_length":
            if (not isinstance(interruption[0], VersionRef)
                    or interruption[0].entity_type
                    != "llm_invocation_spec/v1"
                    or not isinstance(interruption[1], VersionRef)
                    or interruption[1].entity_type
                    != "llm_invocation_attempt/v1"
                    or not isinstance(interruption[2], ResourceVersionRef)):
                raise TypeError(
                    "response-length compaction lacks interruption lineage")
        elif any(value is not None for value in interruption):
            raise TypeError(
                "non-length compaction cannot carry interruption lineage")
        if ((not self.semantic_context_turn_refs
             and self.trigger_reason != "response_length")
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "agent_turn/v1"
                       for ref in self.semantic_context_turn_refs)
                or len(set(self.semantic_context_turn_refs))
                != len(self.semantic_context_turn_refs)):
            raise TypeError(
                "prepared context compaction semantic turn closure is invalid")


@dataclass(frozen=True, slots=True)
class CompletedAgentContextCompaction:
    """One committed current-v3 compaction and its waiting loop head."""

    waiting_loop: AgentLoopSnapshot
    compaction_ref: VersionRef

    def __post_init__(self) -> None:
        logical_id = (
            str(self.compaction_ref.entity_id)
            if isinstance(self.compaction_ref, VersionRef) else "")
        version_id = (
            str(self.compaction_ref.version_id)
            if isinstance(self.compaction_ref, VersionRef) else "")
        if (not isinstance(self.waiting_loop, AgentLoopSnapshot)
                or self.waiting_loop.state != AgentLoopState.WAITING_FOR_LLM
                or not isinstance(self.compaction_ref, VersionRef)
                or self.compaction_ref.entity_type
                != "agent_context_compaction/v3"
                or not logical_id.startswith("agent_context_compaction:")
                or len(logical_id) != len("agent_context_compaction:") + 32
                or not version_id.startswith(
                    "agent_context_compaction_version:")
                or len(version_id)
                != len("agent_context_compaction_version:") + 32):
            raise TypeError(
                "completed context compaction requires exact current authority")


class _LLMExecutionBlock(BaseException):
    """Process-local LLM wait/block; never a firing outcome."""

    def __init__(self, authority: OperationExecutionBlockAuthority,
                 loop: AgentLoopSnapshot | None = None) -> None:
        if not isinstance(authority, OperationExecutionBlockAuthority):
            raise TypeError("LLM block requires exact Registry authority")
        self.authority = authority
        self.loop = loop
        super().__init__("LLM execution blocked")


from . import ports as _ports
from .ports import AgentLoopLLMPort, AgentLoopRegistryPort

_ports._bind_protocol_annotation_types({
    name: globals()[name]
    for name in (
        "AgentActionRecord",
        "AgentLengthInterruptionRecord",
        "AgentLoopSnapshot",
        "AgentSystemInitialization",
        "AgentToolCatalog",
        "AgentTurnRecord",
        "AgentTurnTimingEvidence",
        "Any",
        "CompletedAgentContextCompaction",
        "CompletedAgentLLMInvocation",
        "ContextPressurePolicy",
        "ContextReductionSettings",
        "LLMCallAttempt",
        "Mapping",
        "OperationExecutionBlockAuthority",
        "ParentOwnedDelegatedSubtaskLengthReplay",
        "ParentOwnedDelegatedSubtaskResult",
        "ParentOwnedDelegatedSubtaskToolStep",
        "PreparedAgentContextCompaction",
        "PreparedAgentLLMTurn",
        "PreparedAgentTurnContext",
        "PreparedParentOwnedDelegatedSubtaskCall",
        "StartAgentLoopCommand",
        "VersionRef",
    )
})
del _ports


class RegistryAgentLoopLLMPort:
    """Bridge Registry-prepared attempts to the injected neutral input port."""

    __slots__ = (
        "_registry", "_input_port", "_timing_origin_ns",
        "_reduction_settings", "_context_pressure_trigger_ratio")

    def __init__(
            self, registry: AgentLoopRegistryPort,
            input_port: LLMInputPort, *,
            timing_origin_ns: int | None = None,
            reduction_settings: ContextReductionSettings | None = None,
            context_pressure_trigger_ratio: float = 0.90) -> None:
        from cpn.components.operation_gateway import has_port_methods
        if not has_port_methods(registry, AgentLoopRegistryPort):
            raise TypeError("agent LLM port requires the current Registry port")
        if not isinstance(input_port, LLMInputPort):
            raise TypeError("agent LLM port requires LLMInputPort")
        if (reduction_settings is not None
                and not isinstance(
                    reduction_settings, ContextReductionSettings)):
            raise TypeError("agent LLM reduction settings are invalid")
        if (isinstance(context_pressure_trigger_ratio, bool)
                or not isinstance(
                    context_pressure_trigger_ratio, (int, float))
                or not 0.0 < float(context_pressure_trigger_ratio) < 1.0):
            raise TypeError("agent context pressure ratio is invalid")
        self._registry = registry
        self._input_port = input_port
        self._reduction_settings = (
            reduction_settings or ContextReductionSettings())
        self._context_pressure_trigger_ratio = float(
            context_pressure_trigger_ratio)
        self._timing_origin_ns = (
            timing_origin_ns
            if (isinstance(timing_origin_ns, int)
                and not isinstance(timing_origin_ns, bool)
                and timing_origin_ns >= 0)
            else None)

    def request_agent_turn_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, idempotency_key: str,
    ) -> CompletedAgentLLMInvocation:
        from .turn_execution import request_agent_turn_v1 as _execute
        return _execute(
            self, execution, loop, catalog, idempotency_key=idempotency_key)

    def request_delegated_subtask_turn_v1(
            self, prepared: PreparedParentOwnedDelegatedSubtaskCall, *,
            idempotency_key: str) -> bytes | None:
        from .delegation import (
            request_delegated_subtask_turn_v1 as _execute,
        )
        return _execute(self, prepared, idempotency_key=idempotency_key)

    def compact_agent_context_v1(
        self, execution: object, loop: AgentLoopSnapshot,
        catalog: AgentToolCatalog, *, trigger_reason: str, force: bool,
        idempotency_key: str,
    ) -> CompletedAgentContextCompaction:
        from .compaction import compact_agent_context_v1 as _execute
        return _execute(
            self, execution, loop, catalog, trigger_reason=trigger_reason,
            force=force, idempotency_key=idempotency_key)


class AgentLoopService:
    """Coordinate one nondeterministic loop without becoming its authority."""

    __slots__ = (
        "_registry", "_llm", "_catalog", "_timing_origin_ns",
        "_timing_turns", "_timing_expected_turn_count",
        "_timing_expected_action_count", "_workspace_runner")

    def __init__(
        self, registry: AgentLoopRegistryPort, *,
        llm: AgentLoopLLMPort | None = None,
        tool_catalog: AgentToolCatalog | None = None,
        timing_origin_ns: int | None = None,
        workspace_runner=None,
    ) -> None:
        from cpn.components.operation_gateway import has_port_methods
        if not has_port_methods(registry, AgentLoopRegistryPort):
            raise TypeError("agent-loop service requires the Registry v1 port")
        catalog = tool_catalog or build_agent_tool_catalog()
        if not isinstance(catalog, AgentToolCatalog):
            raise TypeError("agent-loop service requires the exact tool catalog")
        self._registry = registry
        if llm is not None and not isinstance(llm, AgentLoopLLMPort):
            raise TypeError("agent-loop service requires a typed LLM port")
        self._llm = llm
        self._catalog = catalog
        if workspace_runner is not None and not callable(workspace_runner):
            raise TypeError("workspace_runner must be callable")
        self._workspace_runner = workspace_runner
        self._timing_origin_ns = (
            timing_origin_ns
            if (isinstance(timing_origin_ns, int)
                and not isinstance(timing_origin_ns, bool)
                and timing_origin_ns >= 0)
            else None)
        self._timing_turns: list[AgentTurnTimingEvidence] = []
        self._timing_expected_turn_count = 0
        self._timing_expected_action_count = 0

    def run(
        self, execution: object, *, idempotency_key: str,
    ) -> (AgentLoopCompletion | AgentCapReviewCompletion
          | AgentTaskModelCallCapHandoffCompletion
          | AgentLoopInterruptionCheckpoint
          | AgentLoopResourceWait
          | AgentLoopExecutionBlock):
        """Run the current invocation/v1 loop to explicit file completion."""
        if self._llm is None:
            raise AgentLoopProtocolError("agent-loop run has no LLM port")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("agent-loop run requires an idempotency key")
        command = self._registry.prepare_agent_loop_start_v1(
            execution, self._catalog,
            idempotency_key=f"{idempotency_key}:prepare")
        loop = self.start(command)
        return self._run_from_loop(
            execution, loop, idempotency_key=idempotency_key)

    def resume_resource_grant(
        self, execution: object, grant: VerifiedAgentLoopResourceGrant, *,
        idempotency_key: str,
    ) -> (AgentLoopCompletion | AgentCapReviewCompletion
          | AgentTaskModelCallCapHandoffCompletion
          | AgentLoopInterruptionCheckpoint
          | AgentLoopResourceWait
          | AgentLoopExecutionBlock):
        """Consume one P0-verified grant and continue the same waiting loop."""
        if self._llm is None:
            raise AgentLoopProtocolError(
                "resource-grant resume has no LLM port")
        if (not isinstance(grant, VerifiedAgentLoopResourceGrant)
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise AgentLoopProtocolError(
                "resource-grant resume requires its typed grant and key")
        from cpn.rpnh.registry.resources import AgentLoopResourceGrantAuthority
        if not isinstance(grant.registry_authority,
                          AgentLoopResourceGrantAuthority):
            raise AgentLoopProtocolError(
                "resource-grant resume accepts only P0 Registry authority")
        authority_fields = (
            "transition_firing_ref", "invocation_ref",
            "operation_execution_lease_ref", "operation_binding_ref",
            "queue_entry_id", "resource_ref", "writer_fencing_epoch",
            "agent_loop_ref", "agent_turn_ref", "agent_action_ref",
        )
        if any(getattr(grant.registry_authority, name, None)
               != getattr(grant, name) for name in authority_fields):
            raise AgentLoopProtocolError(
                "P0 resource grant differs from the stored wait identities")
        resumed = self._registry.consume_agent_loop_resource_grant_v1(
            execution, grant.registry_authority,
            waiting_loop=grant.waiting_loop,
            waiting_turn=grant.waiting_turn,
            waiting_action=grant.waiting_action,
            catalog=self._catalog,
            idempotency_key=f"{idempotency_key}:consume-grant")
        self._require_transition(
            grant.waiting_loop, resumed,
            AgentLoopState.WAITING_FOR_LLM)
        return self._run_from_loop(
            execution, resumed, idempotency_key=idempotency_key)

    def _run_from_loop(
        self, execution: object, loop: AgentLoopSnapshot, *,
        idempotency_key: str,
    ) -> (AgentLoopCompletion | AgentCapReviewCompletion
          | AgentTaskModelCallCapHandoffCompletion
          | AgentLoopInterruptionCheckpoint
          | AgentLoopResourceWait
          | AgentLoopExecutionBlock):
        self._timing_turns = []
        self._timing_expected_turn_count = 0
        self._timing_expected_action_count = 0
        resource_wait_context: tuple[
            AgentTurnRecord, AgentActionRecord] | None = None
        while loop.state not in {
                AgentLoopState.COMPLETED,
                AgentLoopState.WAITING_RESOURCE}:
            if loop.state in TERMINAL_STATES:
                raise AgentLoopProtocolError(
                    f"agent loop ended as {loop.state.value} without completion")
            if loop.state == AgentLoopState.TURN_STORED:
                turn = self._registry.hydrate_current_agent_turn_v1(loop)
                try:
                    loop, actions = self._settle_stored_turn(
                        execution, loop, turn,
                        idempotency_key=(
                            f"{idempotency_key}:turn:{turn.sequence}"))
                except _LLMExecutionBlock as blocked:
                    committed_loop = blocked.loop or loop
                    return AgentLoopExecutionBlock(
                        loop=committed_loop,
                        block_authority=blocked.authority,
                        timing_observation=(
                            self._operation_timing_observation(
                                execution, loop.loop_id)))
                except Exception as exc:
                    return self._block_from_bottom_error(
                        execution, loop, exc,
                        block_kind="framework_repair",
                        boundary="agent_action_settlement",
                        idempotency_key=(
                            f"{idempotency_key}:turn:{turn.sequence}:block"))
                waiting_actions = tuple(
                    action for action in actions
                    if action.state == AgentLoopState.WAITING_RESOURCE)
                if loop.state == AgentLoopState.WAITING_RESOURCE:
                    if len(waiting_actions) != 1:
                        raise AgentLoopProtocolError(
                            "WAITING_RESOURCE lacks one exact stored action")
                    resource_wait_context = (turn, waiting_actions[0])
                elif waiting_actions:
                    raise AgentLoopProtocolError(
                        "resource-wait action did not leave its loop waiting")
                if (loop.state == AgentLoopState.WAITING_FOR_LLM
                        and self._interruption_requested(execution)):
                    return AgentLoopInterruptionCheckpoint(
                        loop, self._operation_timing_observation(
                            execution, loop.loop_id))
                continue
            waiting = (self.enter_waiting_for_llm(
                loop,
                idempotency_key=(
                    f"{idempotency_key}:turn:{loop.next_turn_sequence}:waiting"),
            ) if loop.state == AgentLoopState.NEW else loop)
            self._require_snapshot(
                waiting, state=AgentLoopState.WAITING_FOR_LLM)
            if self._interruption_requested(execution):
                return AgentLoopInterruptionCheckpoint(
                    waiting, self._operation_timing_observation(
                        execution, waiting.loop_id))
            segment_role = self._registry.agent_loop_segment_role_v1(
                execution, waiting)
            task_call_cap_reached = (
                self._registry.task_model_call_handoff_required_v1(execution))
            if (task_call_cap_reached
                    and segment_role in {
                        "actor", "critic", "finalization_reviewer"}):
                loop = (
                    self._registry.handoff_agent_task_model_call_cap_v1(
                        execution, waiting,
                        idempotency_key=(
                            f"{idempotency_key}:task-call-cap-handoff")))
                self._require_snapshot(
                    loop, state=AgentLoopState.COMPLETED)
                _LOGGER.warning(
                    "agent_loop_task_cap_handoff loop=%s invocation=%s "
                    "turns=%s outputs=%s next=terminal_downstream",
                    loop.loop_id,
                    loop.invocation_ref.version_id,
                    loop.llm_turns_used,
                    len(loop.written_resource_refs),
                )
                return AgentTaskModelCallCapHandoffCompletion(
                    loop=loop,
                    agent_loop_role=segment_role,
                    transition_firing_ref=(
                        execution.operation.firing.transition_firing_ref),
                    timing_observation=self._operation_timing_observation(
                        execution, loop.loop_id),
                )
            segment_boundary = (
                waiting.llm_turns_used >= waiting.llm_turn_budget)
            if segment_boundary:
                segment_identity = (
                    f"{segment_role}-segment:{waiting.next_turn_sequence}")
                if waiting.next_turn_sequence > 0:
                    try:
                        compacted = self._llm.compact_agent_context_v1(
                            execution, waiting, self._catalog,
                            trigger_reason="turn_cap", force=True,
                            idempotency_key=(
                                f"{idempotency_key}:{segment_identity}:"
                                "compaction"))
                        waiting = compacted.waiting_loop
                        self._require_snapshot(
                            waiting, state=AgentLoopState.WAITING_FOR_LLM)
                    except _LLMExecutionBlock as blocked:
                        return AgentLoopExecutionBlock(
                            loop=waiting,
                            block_authority=blocked.authority,
                            timing_observation=(
                                self._operation_timing_observation(
                                    execution, waiting.loop_id)))
                    except LLMInputPortInterrupted:
                        if not self._interruption_requested(execution):
                            raise AgentLoopProtocolError(
                                "LLM input reported interruption without "
                                "an owner stop")
                        return AgentLoopInterruptionCheckpoint(
                            waiting, self._operation_timing_observation(
                                execution, waiting.loop_id))
                    except Exception as exc:
                        return self._block_from_bottom_error(
                            execution, waiting, exc,
                            block_kind="framework_repair",
                            boundary="agent_context_compaction",
                            idempotency_key=(
                                f"{idempotency_key}:{segment_identity}:"
                                "compaction:block"))
                if segment_role == "actor":
                    handed_off = self._registry.handoff_agent_llm_turn_cap_v1(
                        execution, waiting,
                        idempotency_key=(
                            f"{idempotency_key}:turn-cap-handoff"))
                    if handed_off is None:
                        raise AgentLoopProtocolError(
                            "actor LLM-turn cap produced no registered "
                            "ordinary-critic handoff")
                    self._require_snapshot(
                        handed_off, state=AgentLoopState.COMPLETED)
                    loop = handed_off
                    _LOGGER.warning(
                        "agent_loop_cap_handoff loop=%s invocation=%s "
                        "turns=%s outputs=%s next=declared_downstream",
                        loop.loop_id,
                        loop.invocation_ref.version_id,
                        loop.llm_turns_used,
                        len(loop.written_resource_refs),
                    )
                else:
                    loop = self._continue_agent_loop_role_segment(
                        execution, waiting, segment_role=segment_role,
                        idempotency_key=(
                            f"{idempotency_key}:{segment_identity}:continue"))
                    _LOGGER.warning(
                        "agent_loop_same_role_segment loop=%s invocation=%s "
                        "role=%s turns=%s next_segment_boundary=%s",
                        loop.loop_id,
                        loop.invocation_ref.version_id,
                        segment_role,
                        loop.llm_turns_used,
                        loop.llm_turn_budget,
                    )
                continue
            try:
                completed = self._llm.request_agent_turn_v1(
                    execution, waiting, self._catalog,
                    idempotency_key=(
                        f"{idempotency_key}:turn:"
                        f"{waiting.next_turn_sequence}:llm"),
                )
            except _LLMExecutionBlock as blocked:
                return AgentLoopExecutionBlock(
                    loop=waiting,
                    block_authority=blocked.authority,
                    timing_observation=self._operation_timing_observation(
                        execution, waiting.loop_id))
            except LLMInputPortInterrupted:
                if not self._interruption_requested(execution):
                    raise AgentLoopProtocolError(
                        "LLM input reported interruption without an owner stop")
                return AgentLoopInterruptionCheckpoint(
                    waiting, self._operation_timing_observation(
                        execution, waiting.loop_id))
            except TaskModelCallLimitExceeded:
                if segment_role not in {
                        "actor", "critic", "finalization_reviewer"}:
                    raise
                loop = (
                    self._registry.handoff_agent_task_model_call_cap_v1(
                        execution, waiting,
                        idempotency_key=(
                            f"{idempotency_key}:task-call-cap-handoff")))
                self._require_snapshot(
                    loop, state=AgentLoopState.COMPLETED)
                _LOGGER.warning(
                    "agent_loop_task_cap_handoff loop=%s invocation=%s "
                    "turns=%s outputs=%s next=terminal_downstream",
                    loop.loop_id,
                    loop.invocation_ref.version_id,
                    loop.llm_turns_used,
                    len(loop.written_resource_refs),
                )
                return AgentTaskModelCallCapHandoffCompletion(
                    loop=loop,
                    agent_loop_role=segment_role,
                    transition_firing_ref=(
                        execution.operation.firing.transition_firing_ref),
                    timing_observation=self._operation_timing_observation(
                        execution, loop.loop_id),
                )
            except Exception as exc:
                return self._block_from_bottom_error(
                    execution, waiting, exc,
                    block_kind="framework_repair",
                    boundary="llm_execution",
                    idempotency_key=(
                        f"{idempotency_key}:turn:"
                        f"{waiting.next_turn_sequence}:block"))
            if not isinstance(completed, CompletedAgentLLMInvocation):
                raise AgentLoopProtocolError(
                    "LLM port returned no completed current-lane invocation")
            waiting = completed.waiting_loop or waiting
            self._require_snapshot(
                waiting, state=AgentLoopState.WAITING_FOR_LLM)
            turn_key = (
                f"{idempotency_key}:turn:{waiting.next_turn_sequence}")
            try:
                loop, turn = self._record_and_validate_completed_invocation(
                    waiting,
                    attempt=completed.attempt,
                    response_bytes=completed.response_bytes,
                    idempotency_key=turn_key,
                    timing_evidence=completed.timing_evidence,
                )
            except Exception:
                raise
            try:
                loop, actions = self._settle_stored_turn(
                    execution, loop, turn, idempotency_key=turn_key)
            except _LLMExecutionBlock as blocked:
                return AgentLoopExecutionBlock(
                    loop=loop,
                    block_authority=blocked.authority,
                    timing_observation=self._operation_timing_observation(
                        execution, loop.loop_id))
            except Exception as exc:
                return self._block_from_bottom_error(
                    execution, loop, exc,
                    block_kind="framework_repair",
                    boundary="agent_action_settlement",
                    idempotency_key=f"{turn_key}:block")
            waiting_actions = tuple(
                action for action in actions
                if action.state == AgentLoopState.WAITING_RESOURCE)
            if loop.state == AgentLoopState.WAITING_RESOURCE:
                if len(waiting_actions) != 1:
                    raise AgentLoopProtocolError(
                        "WAITING_RESOURCE lacks one exact stored action")
                resource_wait_context = (turn, waiting_actions[0])
            elif waiting_actions:
                raise AgentLoopProtocolError(
                    "resource-wait action did not leave its loop waiting")
            if (loop.state == AgentLoopState.WAITING_FOR_LLM
                    and self._interruption_requested(execution)):
                return AgentLoopInterruptionCheckpoint(
                    loop, self._operation_timing_observation(
                        execution, loop.loop_id))
        if loop.state == AgentLoopState.COMPLETED:
            if loop.cap_review_resource_ref is not None:
                return AgentCapReviewCompletion(
                    loop, loop.cap_review_resource_ref,
                    self._operation_timing_observation(
                        execution, loop.loop_id))
            if not loop.written_resource_refs:
                role = self._registry.agent_loop_segment_role_v1(
                    execution, loop)
                firing_ref = (
                    execution.operation.firing.transition_firing_ref)
                return AgentTaskModelCallCapHandoffCompletion(
                    loop=loop,
                    agent_loop_role=role,
                    transition_firing_ref=firing_ref,
                    timing_observation=self._operation_timing_observation(
                        execution, loop.loop_id),
                )
            return AgentLoopCompletion(
                loop, loop.written_resource_refs,
                self._operation_timing_observation(execution, loop.loop_id))
        if resource_wait_context is None:
            raise AgentLoopProtocolError(
                "WAITING_RESOURCE lost its stored turn/action context")
        return AgentLoopResourceWait(
            loop=loop, turn=resource_wait_context[0],
            action=resource_wait_context[1],
            timing_observation=self._operation_timing_observation(
                execution, loop.loop_id),
        )

    def _continue_agent_loop_role_segment(
            self, execution: object, loop: AgentLoopSnapshot, *,
            segment_role: str, idempotency_key: str) -> AgentLoopSnapshot:
        """Open the next segment for the same critic/reviewer."""

        if segment_role not in {"critic", "finalization_reviewer"}:
            raise AgentLoopProtocolError(
                "mechanical segment continuation requires a critic/reviewer")
        self._require_snapshot(loop, state=AgentLoopState.WAITING_FOR_LLM)
        continued = self._registry.continue_agent_loop_role_segment_v1(
            execution, loop, idempotency_key=idempotency_key)
        self._require_snapshot(
            continued, state=AgentLoopState.WAITING_FOR_LLM)
        stable_fields = (
            "loop_id", "invocation_ref", "operation_binding_ref", "owner_ref",
            "next_turn_sequence", "llm_turns_used", "model_condition",
            "tool_catalog_ref", "current_candidate_ref",
            "adopted_candidate_ref", "disposition_ref",
            "written_resource_refs", "cap_review_resource_ref",
            "workspace_binding_ref", "workspace_lineage_ref",
            "workspace_base_revision_ref", "workspace_revision_ref",
            "workspace_changed_paths", "workspace_deleted_paths",
        )
        llm_extension = continued.llm_turn_budget - loop.llm_turn_budget
        if (any(getattr(continued, name) != getattr(loop, name)
                for name in stable_fields)
                or continued.revision != loop.revision + 1
                or continued.loop_version_id == loop.loop_version_id
                or llm_extension < 1):
            raise AgentLoopProtocolError(
                "same-role segment continuation crossed its held firing")
        return continued

    def _block_from_bottom_error(
            self, execution: object, loop: AgentLoopSnapshot,
            error: BaseException, *, block_kind: str, boundary: str,
            idempotency_key: str) -> AgentLoopExecutionBlock:
        """Expose one exact lower-layer block without a Petri outcome."""
        exact_ref = None
        candidates = (
            getattr(error, "conflict_ref", None),
            getattr(error, "attempt_ref", None),
            getattr(error, "invocation_ref", None),
            getattr(getattr(error, "authority", None), "exact_error_ref", None),
            getattr(execution, "operation_execution_lease_ref", None),
        )
        for candidate in candidates:
            if isinstance(candidate, ResourceVersionRef):
                exact_ref = candidate.as_version_ref()
                break
            if isinstance(candidate, VersionRef):
                exact_ref = candidate
                break
        if exact_ref is None:
            raise AgentLoopProtocolError(
                "bottom execution error lacks exact Registry evidence") from error
        error_code = type(error).__name__
        block = self._registry.register_operation_execution_block(
            execution,
            block_kind=block_kind,
            operation_or_tool_identity=str(
                loop.operation_binding_ref.entity_id),
            error_code=error_code,
            error_message=str(error),
            boundary=boundary,
            consecutive_count=1,
            exact_error_ref=exact_ref,
            retry_not_before_utc=None,
        )
        _LOGGER.warning(
            "agent_loop_execution_block loop=%s kind=%s boundary=%s "
            "error_code=%s error_message=%s exact_error_ref=%s",
            loop.loop_id, block_kind, boundary, error_code, str(error),
            exact_ref.version_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        del idempotency_key
        return AgentLoopExecutionBlock(
            loop=loop, block_authority=block, bottom_error=error,
            timing_observation=self._operation_timing_observation(
                execution, loop.loop_id))

    def _operation_timing_observation(
            self, execution: object, loop_id: str,
    ) -> AgentOperationTimingEvidence | None:
        try:
            if self._timing_origin_ns is None:
                return None
            return AgentOperationTimingEvidence(
                operation_execution_ref=(
                    execution.operation_execution_lease_ref),
                operation_start_event_id=execution.start_event_id,
                timing_origin_ns=self._timing_origin_ns,
                agent_loop_id=loop_id,
                expected_committed_turn_count=(
                    self._timing_expected_turn_count),
                expected_committed_action_count=(
                    self._timing_expected_action_count),
                turns=list(self._timing_turns),
            )
        except Exception:
            return None

    def start(self, command: StartAgentLoopCommand) -> AgentLoopSnapshot:
        if not isinstance(command, StartAgentLoopCommand):
            raise TypeError("agent-loop start requires a typed command")
        if (command.tool_catalog.payload != self._catalog.payload
                or command.tool_catalog.tool_names != self._catalog.tool_names
                or not isinstance(
                    command.registered_tool_catalog_ref, ResourceVersionRef)):
            raise AgentLoopProtocolError(
                "agent-loop start tool catalog differs from service registration")
        loop = self._registry.start_agent_loop_v1(command)
        if (not isinstance(loop, AgentLoopSnapshot)
                or loop.loop_id != command.loop_id
                or loop.model_condition != command.model_condition
                or loop.tool_catalog_ref
                != command.registered_tool_catalog_ref):
            raise AgentLoopProtocolError(
                "Registry returned a different committed loop start")
        return loop

    def enter_waiting_for_llm(
        self, loop: AgentLoopSnapshot, *, idempotency_key: str,
    ) -> AgentLoopSnapshot:
        """Commit the explicit LLM-wait state before dispatch."""
        self._require_snapshot(loop)
        waiting = self._registry.mark_agent_loop_waiting_v1(
            loop, expected_revision=loop.revision,
            idempotency_key=idempotency_key)
        self._require_transition(
            loop, waiting, AgentLoopState.WAITING_FOR_LLM)
        return waiting

    def record_completed_invocation(
        self, loop: AgentLoopSnapshot, *, execution: object | None = None,
        attempt: LLMCallAttempt, response_bytes: bytes,
        idempotency_key: str,
        timing_evidence: AgentTurnTimingEvidence | None = None,
    ) -> tuple[
        AgentLoopSnapshot,
        AgentTurnRecord | AgentLengthInterruptionRecord,
        tuple[AgentActionRecord, ...],
    ]:
        """Persist one usable turn or length interruption, then settle it."""
        stored, turn = self._record_and_validate_completed_invocation(
            loop, attempt=attempt, response_bytes=response_bytes,
            idempotency_key=idempotency_key,
            timing_evidence=timing_evidence)
        settled, actions = self._settle_stored_turn(
            execution, stored, turn, idempotency_key=idempotency_key)
        return settled, turn, actions

    def _record_and_validate_completed_invocation(
        self, loop: AgentLoopSnapshot, *,
        attempt: LLMCallAttempt, response_bytes: bytes,
        idempotency_key: str,
        timing_evidence: AgentTurnTimingEvidence | None = None,
    ) -> tuple[
        AgentLoopSnapshot,
        AgentTurnRecord | AgentLengthInterruptionRecord,
    ]:
        self._require_snapshot(loop, state=AgentLoopState.WAITING_FOR_LLM)
        record_timing_offset(
            timing_evidence, "response_registration_start",
            self._timing_origin_ns)
        stored, turn = self._registry.record_agent_llm_turn_v1(
            loop, attempt, response_bytes,
            idempotency_key=f"{idempotency_key}:turn")
        record_timing_offset(
            timing_evidence, "response_registration_return",
            self._timing_origin_ns)
        self._require_transition(loop, stored, AgentLoopState.TURN_STORED)
        if turn.sequence != loop.next_turn_sequence:
            raise AgentLoopProtocolError("Registry stored a noncontiguous turn")
        if isinstance(turn, AgentTurnRecord):
            self._timing_expected_turn_count += 1
        try:
            if timing_evidence is not None:
                timing_evidence.agent_turn_id = (
                    turn.turn_id if isinstance(turn, AgentTurnRecord) else None)
                timing_evidence.llm_invocation_attempt_ref = (
                    turn.llm_invocation_attempt_ref)
                timing_evidence.expected_committed_action_count = (
                    len(turn.tool_calls)
                    if isinstance(turn, AgentTurnRecord) else 0)
                record_timing_offset(
                    timing_evidence, "agent_turn_commit_return",
                    self._timing_origin_ns)
                self._timing_turns.append(timing_evidence)
        except Exception:
            pass
        return stored, turn

    def _settle_stored_turn(
        self, execution: object, stored: AgentLoopSnapshot,
        turn: AgentTurnRecord | AgentLengthInterruptionRecord, *,
        idempotency_key: str,
    ) -> tuple[AgentLoopSnapshot, tuple[AgentActionRecord, ...]]:
        self._require_snapshot(stored, state=AgentLoopState.TURN_STORED)
        if isinstance(turn, AgentLengthInterruptionRecord):
            waiting = (
                self._registry.continue_after_incomplete_length_turn_v1(
                    stored, turn, expected_revision=stored.revision,
                    idempotency_key=f"{idempotency_key}:response-length"))
            self._require_transition(
                stored, waiting, AgentLoopState.WAITING_FOR_LLM)
            timing = next((
                item for item in reversed(self._timing_turns)
                if item.agent_loop_id == turn.loop_id
                and item.turn_sequence == turn.sequence
            ), None)
            if timing is not None:
                timing.expected_committed_action_count = 0
            record_timing_offset(
                timing, "action_settlement_return", self._timing_origin_ns)
            return waiting, ()
        if not isinstance(turn, AgentTurnRecord):
            raise AgentLoopProtocolError(
                "stored AgentLoop result is neither a turn nor length interruption")
        if not turn.tool_calls:
            waiting = self._registry.continue_after_text_turn_v1(
                stored, turn, expected_revision=stored.revision,
                idempotency_key=f"{idempotency_key}:text-only")
            self._require_transition(stored, waiting,
                                     AgentLoopState.WAITING_FOR_LLM)
            timing = next((
                item for item in reversed(self._timing_turns)
                if item.agent_loop_id == turn.loop_id
                and item.turn_sequence == turn.sequence
            ), None)
            record_timing_offset(
                timing, "action_settlement_return", self._timing_origin_ns)
            return waiting, ()
        preparation_timing_kwargs = (
            {"timing_origin_ns": self._timing_origin_ns}
            if self._timing_origin_ns is not None else {})
        prepared = self._registry.prepare_agent_turn_actions_v1(
            stored, turn, **preparation_timing_kwargs)
        if not prepared:
            raise AgentLoopProtocolError(
                "action turn contains no registered actions")
        timing = next((
            item for item in reversed(self._timing_turns)
            if item.agent_loop_id == turn.loop_id
            and item.turn_sequence == turn.sequence
        ), None)
        try:
            if timing is not None:
                timing.actions = [
                    item.timing_evidence for item in prepared
                    if item.timing_evidence is not None]
        except Exception:
            pass
        settlement_timing_kwargs = (
            {"timing_evidence": timing} if timing is not None else {})
        permitted_tool_names = tuple(sorted(self._catalog.tool_names))
        if self._interruption_requested(execution):
            return self._interrupt_action_batch(
                execution, stored, turn, prepared,
                permitted_tool_names=permitted_tool_names,
                idempotency_key=idempotency_key)
        external_tool_results: dict[str, Mapping[str, Any]] = {}
        workspace_snapshot = None
        workspace_root = None
        for prepared_action in prepared:
            action_validation = prepared_action.validation
            if (isinstance(action_validation, ValidatedAgentToolAction)
                    and action_validation.tool_name == "workspace"):
                request = self._registry.prepare_workspace_action_execution_v1(
                    execution, stored, turn, prepared_action)
                if self._workspace_runner is None:
                    raise AgentLoopProtocolError(
                        "workspace action requires a worker-side runner")
                from pathlib import Path
                from cpn.rpnh.workspace_settlement import (
                    _restore_tree, _tree_files,
                )
                current_root = Path(str(request["cwd"]))
                if workspace_snapshot is None:
                    workspace_root = current_root
                    workspace_snapshot = _tree_files(current_root)
                elif current_root != workspace_root:
                    raise AgentLoopProtocolError(
                        "one action batch crossed firing workspaces")
                external_tool_results[action_validation.action_id] = (
                    self._workspace_runner(**dict(request)))
                if self._interruption_requested(execution):
                    assert workspace_root is not None
                    _restore_tree(workspace_root, workspace_snapshot)
                    return self._interrupt_action_batch(
                        execution, stored, turn, prepared,
                        permitted_tool_names=permitted_tool_names,
                        idempotency_key=idempotency_key)
        if self._interruption_requested(execution):
            if workspace_snapshot is not None:
                assert workspace_root is not None
                from cpn.rpnh.workspace_settlement import _restore_tree
                _restore_tree(workspace_root, workspace_snapshot)
            return self._interrupt_action_batch(
                execution, stored, turn, prepared,
                permitted_tool_names=permitted_tool_names,
                idempotency_key=idempotency_key)
        validation = prepared[0].validation if len(prepared) == 1 else None
        monitored_workspace = (
            isinstance(validation, ValidatedAgentToolAction)
            and validation.tool_name == "workspace"
            and workspace_execution_mode(validation.arguments)
            == "monitored")
        if monitored_workspace:
            pending_loop, pending_action = (
                self._registry.begin_monitored_workspace_action_v1(
                    stored, turn, prepared[0],
                    permitted_tool_names=permitted_tool_names,
                    idempotency_key=f"{idempotency_key}:actions:pending",
                    execution=execution))
            self._require_transition(
                stored, pending_loop, AgentLoopState.ACTION_PENDING)
            settlement = self._registry.finish_monitored_workspace_action_v1(
                pending_loop, turn, prepared[0], pending_action,
                permitted_tool_names=permitted_tool_names,
                idempotency_key=f"{idempotency_key}:actions:final",
                execution=execution,
                **settlement_timing_kwargs)
        else:
            try:
                delegated_results = self._resolve_parent_owned_delegated_subtasks(
                    execution, stored, turn, prepared,
                    parent_settlement_idempotency_key=(
                        f"{idempotency_key}:actions"))
            except LLMInputPortInterrupted:
                if not self._interruption_requested(execution):
                    raise AgentLoopProtocolError(
                        "delegated LLM input reported interruption without "
                        "an owner stop")
                if workspace_snapshot is not None:
                    assert workspace_root is not None
                    from cpn.rpnh.workspace_settlement import _restore_tree
                    _restore_tree(workspace_root, workspace_snapshot)
                return self._interrupt_action_batch(
                    execution, stored, turn, prepared,
                    permitted_tool_names=permitted_tool_names,
                    idempotency_key=idempotency_key)
            delegated_settlement_kwargs = (
                {"delegated_subtask_results": delegated_results}
                if delegated_results else {})
            settlement = self._registry.settle_agent_turn_actions_v1(
                stored, turn, prepared,
                permitted_tool_names=permitted_tool_names,
                idempotency_key=f"{idempotency_key}:actions",
                execution=execution,
                **delegated_settlement_kwargs,
                external_tool_results=external_tool_results,
                **settlement_timing_kwargs)
        if isinstance(settlement, OperationExecutionBlockAuthority):
            raise _LLMExecutionBlock(settlement)
        if (isinstance(settlement, tuple) and len(settlement) == 2
                and isinstance(settlement[0], OperationExecutionBlockAuthority)
                and isinstance(settlement[1], AgentLoopSnapshot)):
            raise _LLMExecutionBlock(settlement[0], settlement[1])
        if (not isinstance(settlement, tuple)
                or len(settlement) != 2):
            raise AgentLoopProtocolError(
                "Registry returned neither settlement nor execution block")
        settled, actions = settlement
        actions = tuple(actions)
        if (not isinstance(settled, AgentLoopSnapshot)
                or any(not isinstance(item, AgentActionRecord)
                       for item in actions)):
            raise AgentLoopProtocolError(
                "Registry returned an incomplete action settlement")
        self._timing_expected_action_count += len(actions)
        try:
            if timing is not None:
                timing.expected_committed_action_count = len(actions)
        except Exception:
            pass
        try:
            timing_by_action = {
                item.action_id: item for item in timing.actions
            } if timing is not None else {}
            for action in actions:
                evidence = timing_by_action.get(action.action_id)
                if evidence is not None:
                    evidence.settlement = action.state.value
        except Exception:
            pass
        record_timing_offset(
            timing, "action_settlement_return", self._timing_origin_ns)
        return settled, actions

    def _interrupt_action_batch(
            self, execution: object, stored: AgentLoopSnapshot,
            turn: AgentTurnRecord, prepared: tuple[object, ...], *,
            permitted_tool_names: tuple[str, ...], idempotency_key: str,
    ) -> tuple[AgentLoopSnapshot, tuple[AgentActionRecord, ...]]:
        interrupt = getattr(
            self._registry, "interrupt_agent_turn_actions_v1", None)
        if not callable(interrupt):
            raise AgentLoopProtocolError(
                "Registry lacks owner-interruption action settlement")
        settlement = interrupt(
            stored, turn, prepared,
            permitted_tool_names=permitted_tool_names,
            idempotency_key=f"{idempotency_key}:owner-interruption",
            execution=execution)
        if (not isinstance(settlement, tuple) or len(settlement) != 2
                or not isinstance(settlement[0], AgentLoopSnapshot)
                or settlement[0].state != AgentLoopState.WAITING_FOR_LLM
                or any(not isinstance(item, AgentActionRecord)
                       or item.state != AgentLoopState.ACTION_REJECTED
                       for item in settlement[1])):
            raise AgentLoopProtocolError(
                "owner interruption returned no settled action checkpoint")
        self._timing_expected_action_count += len(settlement[1])
        return settlement[0], tuple(settlement[1])

    def _interruption_requested(self, execution: object) -> bool:
        probe = getattr(
            self._registry, "operation_interruption_requested", None)
        if probe is None:
            return False
        if not callable(probe):
            raise AgentLoopProtocolError(
                "operation interruption probe is not callable")
        requested = probe(execution)
        if not isinstance(requested, bool):
            raise AgentLoopProtocolError(
                "operation interruption probe returned no boolean")
        return requested

    def _resolve_parent_owned_delegated_subtasks(
            self, execution: object, stored: AgentLoopSnapshot,
            turn: AgentTurnRecord, prepared_actions: tuple[object, ...], *,
            parent_settlement_idempotency_key: str,
    ) -> dict[str, ParentOwnedDelegatedSubtaskResult]:
        """Resolve every parent delegate action as a synchronous local session."""

        delegated = tuple(
            item for item in prepared_actions
            if isinstance(
                getattr(item, "validation", None),
                ValidatedAgentToolAction,
            )
            and item.validation.tool_name == "delegate_leaf")
        if not delegated:
            return {}
        request_turn = getattr(
            self._llm, "request_delegated_subtask_turn_v1", None)
        if not callable(request_turn):
            raise AgentLoopProtocolError(
                "delegated subtask requires a synchronous local LLM interface")
        results: dict[str, ParentOwnedDelegatedSubtaskResult] = {}
        for action in delegated:
            action_id = str(action.validation.action_id)
            prior_steps: tuple[ParentOwnedDelegatedSubtaskToolStep, ...] = ()
            leaf_turn_sequence = 0
            history_messages: tuple[Mapping[str, Any], ...] = ()
            length_replay_ordinal = 0
            while True:
                prepared = (
                    self._registry
                    .prepare_parent_owned_delegated_subtask_call_v1(
                        execution, stored, turn, action,
                        parent_tool_catalog=self._catalog,
                        idempotency_key=(
                            f"{parent_settlement_idempotency_key}:delegate:"
                            f"{action.tool_call.tool_call_ordinal}:"
                            f"{leaf_turn_sequence}:length-replay:"
                            f"{length_replay_ordinal}:prepare"),
                        parent_settlement_idempotency_key=(
                            parent_settlement_idempotency_key),
                        leaf_turn_sequence=leaf_turn_sequence,
                        prior_leaf_steps=prior_steps,
                        history_messages=history_messages))
                if not isinstance(
                        prepared, PreparedParentOwnedDelegatedSubtaskCall):
                    raise AgentLoopProtocolError(
                        "Registry returned no prepared delegated subtask call")
                response_bytes = request_turn(
                    prepared,
                    idempotency_key=(
                        f"{parent_settlement_idempotency_key}:delegate:"
                        f"{action.tool_call.tool_call_ordinal}:"
                        f"{leaf_turn_sequence}:length-replay:"
                        f"{length_replay_ordinal}:dispatch"))
                if response_bytes is None:
                    continue
                outcome = (
                    self._registry
                    .settle_parent_owned_delegated_subtask_tool_step_v1(
                        execution, stored, turn, action, prepared,
                        response_bytes,
                        prior_leaf_steps=prior_steps,
                        idempotency_key=(
                            f"{parent_settlement_idempotency_key}:delegate:"
                            f"{action.tool_call.tool_call_ordinal}:"
                            f"{leaf_turn_sequence}:length-replay:"
                            f"{length_replay_ordinal}:settle")))
                if isinstance(outcome, ParentOwnedDelegatedSubtaskResult):
                    if (outcome.parent_action_ref
                            != prepared.request.parent_action_ref
                            or outcome.parent_invocation_ref
                            != stored.invocation_ref
                            or outcome.child_session_id
                            != prepared.request.child_session_id):
                        raise AgentLoopProtocolError(
                            "delegated result differs from its parent action")
                    results[action_id] = outcome
                    break
                if isinstance(
                        outcome, ParentOwnedDelegatedSubtaskLengthReplay):
                    if outcome.local_sequence != leaf_turn_sequence:
                        raise AgentLoopProtocolError(
                            "delegated length replay changed local sequence")
                    history_messages = outcome.history_messages
                    length_replay_ordinal += 1
                    continue
                if not isinstance(
                        outcome, ParentOwnedDelegatedSubtaskToolStep):
                    raise AgentLoopProtocolError(
                        "Registry returned no delegated subtask step or result")
                prior_steps = (*prior_steps, outcome)
                history_messages = outcome.history_messages
                leaf_turn_sequence += 1
                length_replay_ordinal = 0
                if leaf_turn_sequence >= prepared.request.leaf_turn_limit:
                    raise AgentLoopProtocolError(
                        "delegated subtask exhausted its local turn limit")
        return results

    def rehydrate(self, loop_ref: VersionRef) -> AgentLoopSnapshot:
        loop = self._registry.hydrate_agent_loop_v1(loop_ref)
        self._require_snapshot(loop)
        return loop

    @staticmethod
    def _require_snapshot(
        loop: AgentLoopSnapshot, *, state: AgentLoopState | None = None,
    ) -> None:
        if not isinstance(loop, AgentLoopSnapshot):
            raise TypeError("agent-loop service requires a committed snapshot")
        if state is not None and loop.state != state:
            raise AgentLoopProtocolError(
                f"agent loop is {loop.state.value}, expected {state.value}")

    @staticmethod
    def _require_transition(
        previous: AgentLoopSnapshot, current: AgentLoopSnapshot,
        target: AgentLoopState,
    ) -> None:
        AgentLoopService._require_snapshot(previous)
        AgentLoopService._require_snapshot(current, state=target)
        require_state_transition(previous.state, current.state)
        if (previous.loop_id != current.loop_id
                or previous.invocation_ref != current.invocation_ref
                or previous.operation_binding_ref != current.operation_binding_ref
                or previous.model_condition != current.model_condition
                or previous.tool_catalog_ref != current.tool_catalog_ref
                or current.revision != previous.revision + 1):
            raise AgentLoopProtocolError(
                "Registry transition changed stable loop authority")


__all__ = [
    "AgentLoopLLMPort",
    "AgentLoopRegistryPort",
    "AgentLoopService",
    "CompletedAgentContextCompaction",
    "CompletedAgentLLMInvocation",
    "PreparedAgentContextCompaction",
    "PreparedAgentLLMTurn",
    "RegistryAgentLoopLLMPort",
    "StartAgentLoopCommand",
]
