"""Immutable DTOs and mechanical state rules for agent-loop/v1."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
from cpn.rpnh.registry.resources import ResourceVersionRef

from .timing import (
    AgentActionTimingEvidence,
    AgentOperationTimingEvidence,
    AgentTurnTimingEvidence,
    record_timing_offset,
)


A2C_CRITIC_VERDICTS = ("continue", "pass", "escalate", "give_up")
MANAGED_FRAMEWORK_ERROR_MAX_BYTES = 64 * 1024


class AgentLoopProtocolError(RuntimeError):
    """A command or recovered record violates framework-owned mechanics."""


class AgentLoopLLMBudgetExhausted(BaseException):
    """Terminal run signal for the committed LLM-turn ceiling."""

    def __init__(self, limit_error: BaseException) -> None:
        if not isinstance(limit_error, BaseException):
            raise TypeError(
                "agent-loop LLM exhaustion requires the typed limit error")
        self.limit_error = limit_error
        super().__init__(str(limit_error))


class AgentLoopLocalTurnLimitExhausted(BaseException):
    """Typed operation-local stop raised after the exact loop is exhausted."""

    def __init__(self, exhausted_loop: "AgentLoopSnapshot") -> None:
        if (not isinstance(exhausted_loop, AgentLoopSnapshot)
                or exhausted_loop.state != AgentLoopState.EXHAUSTED
                or exhausted_loop.llm_turn_budget is None
                or exhausted_loop.llm_turns_used
                != exhausted_loop.llm_turn_budget):
            raise TypeError(
                "local turn exhaustion requires its exact exhausted loop head")
        self.exhausted_loop = exhausted_loop
        super().__init__("llm_turn_limit_exhausted")


class WorkspaceRevisionConflict(RuntimeError):
    """A firing cannot merge its private view into the committed lineage."""

    def __init__(self, conflict_ref: VersionRef, paths: tuple[str, ...]) -> None:
        if (not isinstance(conflict_ref, VersionRef)
                or conflict_ref.entity_type != "workspace_revision/v1"
                or not paths or tuple(paths) != tuple(sorted(set(paths)))):
            raise TypeError(
                "workspace conflict requires exact Registry evidence and paths")
        self.conflict_ref = conflict_ref
        self.paths = paths
        super().__init__("workspace_revision_conflict")


def require_exact_critic_verdict(value: object) -> str:
    """Accept one A2C verdict field without trimming or synonym mapping."""
    if not isinstance(value, str) or value not in A2C_CRITIC_VERDICTS:
        raise AgentLoopProtocolError(
            "critic verdict must be exactly one literal: "
            "continue|pass|escalate|give_up")
    return value


def critic_verdict_only_correction() -> str:
    """Framework-owned bounded correction text for a malformed verdict."""
    return (
        "VERDICT_FORMAT_ERROR: Return exactly one JSON object with exactly one "
        "field named \"verdict\" and no other content. Its value must be exactly "
        "one of continue|pass|escalate|give_up; for example, "
        "{\"verdict\":\"pass\"}. Do not normalize case or whitespace. Correct "
        "only the verdict; do not rewrite or republish the actor output, and do "
        "not repeat or revise the prior critique/evidence."
    )


class AgentLoopState(str, Enum):
    NEW = "NEW"
    WAITING_FOR_LLM = "WAITING_FOR_LLM"
    TURN_STORED = "TURN_STORED"
    ACTION_PENDING = "ACTION_PENDING"
    ACTION_APPLIED = "ACTION_APPLIED"
    ACTION_REJECTED = "ACTION_REJECTED"
    CANDIDATE_COMMITTED = "CANDIDATE_COMMITTED"
    CANDIDATE_ADOPTED = "CANDIDATE_ADOPTED"
    DISPOSITION_RECORDED = "DISPOSITION_RECORDED"
    COMPACTING = "COMPACTING"
    COMPLETED = "COMPLETED"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    TIMED_OUT = "TIMED_OUT"
    EXHAUSTED = "EXHAUSTED"
    INFRASTRUCTURE_FAILED = "INFRASTRUCTURE_FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TERMINAL_STATES = frozenset({
    AgentLoopState.COMPLETED,
    AgentLoopState.TIMED_OUT,
    AgentLoopState.EXHAUSTED,
    AgentLoopState.INFRASTRUCTURE_FAILED,
    AgentLoopState.RECONCILIATION_REQUIRED,
})

_TRANSITIONS = {
    AgentLoopState.NEW: frozenset({
        AgentLoopState.WAITING_FOR_LLM,
        AgentLoopState.TIMED_OUT,
        AgentLoopState.EXHAUSTED,
        AgentLoopState.INFRASTRUCTURE_FAILED,
    }),
    AgentLoopState.WAITING_FOR_LLM: frozenset({
        AgentLoopState.COMPACTING,
        AgentLoopState.TURN_STORED,
        AgentLoopState.COMPLETED,
        AgentLoopState.TIMED_OUT,
        AgentLoopState.EXHAUSTED,
        AgentLoopState.INFRASTRUCTURE_FAILED,
        AgentLoopState.RECONCILIATION_REQUIRED,
    }),
    AgentLoopState.TURN_STORED: frozenset({
        AgentLoopState.ACTION_PENDING,
        AgentLoopState.WAITING_FOR_LLM,
        AgentLoopState.TIMED_OUT,
        AgentLoopState.EXHAUSTED,
        AgentLoopState.INFRASTRUCTURE_FAILED,
    }),
    AgentLoopState.ACTION_PENDING: frozenset({
        AgentLoopState.ACTION_APPLIED,
        AgentLoopState.ACTION_REJECTED,
        AgentLoopState.CANDIDATE_COMMITTED,
        AgentLoopState.CANDIDATE_ADOPTED,
        AgentLoopState.DISPOSITION_RECORDED,
        AgentLoopState.COMPLETED,
        AgentLoopState.WAITING_RESOURCE,
        AgentLoopState.EXHAUSTED,
        AgentLoopState.INFRASTRUCTURE_FAILED,
    }),
    AgentLoopState.ACTION_APPLIED: frozenset({
        AgentLoopState.WAITING_FOR_LLM,
    }),
    AgentLoopState.ACTION_REJECTED: frozenset({
        AgentLoopState.WAITING_FOR_LLM,
    }),
    AgentLoopState.CANDIDATE_COMMITTED: frozenset({
        AgentLoopState.CANDIDATE_ADOPTED,
        AgentLoopState.WAITING_FOR_LLM,
    }),
    AgentLoopState.CANDIDATE_ADOPTED: frozenset({AgentLoopState.COMPLETED}),
    AgentLoopState.DISPOSITION_RECORDED: frozenset({AgentLoopState.COMPLETED}),
    AgentLoopState.COMPACTING: frozenset({
        AgentLoopState.WAITING_FOR_LLM,
        AgentLoopState.EXHAUSTED,
        AgentLoopState.INFRASTRUCTURE_FAILED,
    }),
    AgentLoopState.COMPLETED: frozenset(),
    AgentLoopState.WAITING_RESOURCE: frozenset({
        AgentLoopState.WAITING_FOR_LLM,
        AgentLoopState.INFRASTRUCTURE_FAILED,
    }),
    AgentLoopState.TIMED_OUT: frozenset(),
    AgentLoopState.EXHAUSTED: frozenset(),
    AgentLoopState.INFRASTRUCTURE_FAILED: frozenset(),
    AgentLoopState.RECONCILIATION_REQUIRED: frozenset(),
}


def require_state_transition(
    current: AgentLoopState, target: AgentLoopState,
) -> None:
    if not isinstance(current, AgentLoopState) or not isinstance(target, AgentLoopState):
        raise TypeError("agent-loop transition requires typed states")
    if target not in _TRANSITIONS[current]:
        raise AgentLoopProtocolError(
            f"agent-loop transition {current.value}->{target.value} is not allowed")


def stable_loop_id(invocation_ref: VersionRef, semantic_key: str) -> str:
    if not isinstance(invocation_ref, VersionRef):
        raise TypeError("stable loop id requires an invocation ref")
    return "agent_loop:" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"registry-agent-loop:v1:{invocation_ref.version_id}:{semantic_key}",
    ).hex


def stable_action_id(
    loop_id: str, turn_sequence: int, tool_call_id: str,
) -> str:
    _require_id("agent_loop", loop_id)
    if (isinstance(turn_sequence, bool) or not isinstance(turn_sequence, int)
            or turn_sequence < 0):
        raise ValueError("turn sequence must be nonnegative")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ValueError("LLM tool-call id is required")
    return "agent_action:" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"registry-agent-action:v2:{loop_id}:{turn_sequence}:"
        f"{tool_call_id}",
    ).hex


def stable_malformed_action_id(
    loop_id: str, turn_sequence: int, tool_call_ordinal: int,
) -> str:
    """Derive a disjoint per-position id when no unique LLM call id exists."""
    _require_id("agent_loop", loop_id)
    if (isinstance(turn_sequence, bool) or not isinstance(turn_sequence, int)
            or turn_sequence < 0 or isinstance(tool_call_ordinal, bool)
            or not isinstance(tool_call_ordinal, int) or tool_call_ordinal < 0):
        raise ValueError("malformed action turn/ordinal is invalid")
    return "agent_action:" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"registry-malformed-agent-action:v2:{loop_id}:{turn_sequence}:"
        f"{tool_call_ordinal}",
    ).hex


@dataclass(frozen=True, slots=True)
class LocatedAgentInput:
    """One exact admitted resource location with navigation metadata."""

    resource_ref: ResourceVersionRef
    sandbox_path: str
    semantic_name: str = "resource"
    file_name: str = "resource"
    source_relative_path: str | None = None
    summary: str = "Registered resource"
    summary_truncated: bool = False
    media_type: str = "application/octet-stream"
    content_schema_ref: str | None = None
    head_preview: str = ""
    head_truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.resource_ref, ResourceVersionRef):
            raise TypeError("located input requires one exact resource ref")
        if (not isinstance(self.sandbox_path, str) or not self.sandbox_path
                or self.sandbox_path != self.sandbox_path.strip()
                or self.sandbox_path.startswith("/")
                or ".." in self.sandbox_path.split("/")):
            raise AgentLoopProtocolError(
                "located input requires one canonical sandbox-relative path")
        if (not isinstance(self.semantic_name, str)
                or not self.semantic_name
                or self.semantic_name != self.semantic_name.strip()
                or not isinstance(self.file_name, str)
                or not self.file_name
                or self.file_name != self.file_name.strip()
                or "/" in self.file_name or "\\" in self.file_name
                or (self.source_relative_path is not None
                    and (not isinstance(self.source_relative_path, str)
                         or not self.source_relative_path
                         or self.source_relative_path
                         != self.source_relative_path.strip()
                         or self.source_relative_path.startswith("/")
                         or ".." in self.source_relative_path.split("/")))
                or not isinstance(self.summary, str)
                or not self.summary
                or self.summary != self.summary.strip()
                or len(self.summary) > 600
                or not isinstance(self.summary_truncated, bool)
                or not isinstance(self.media_type, str)
                or not self.media_type
                or self.media_type != self.media_type.strip()
                or (self.content_schema_ref is not None
                    and (not isinstance(self.content_schema_ref, str)
                         or not self.content_schema_ref.strip()))
                or not isinstance(self.head_preview, str)
                or len(self.head_preview) > 240
                or self.head_truncated != bool(self.head_preview)
                or not isinstance(self.head_truncated, bool)):
            raise AgentLoopProtocolError(
                "located input presentation metadata is invalid")


@dataclass(frozen=True, slots=True)
class AgentSystemInitialization:
    """Deterministic framework-owned initialization for one LLM firing."""

    invocation_ref: VersionRef
    sponsoring_transition_firing_ref: VersionRef | None
    role: str
    tool_names: tuple[str, ...]
    skill_ids: tuple[str, ...]
    located_inputs: tuple[LocatedAgentInput, ...]
    output_kind: str
    output_contract: str
    payload: bytes

    def __post_init__(self) -> None:
        if (not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "invocation/v1"):
            raise TypeError("agent initialization requires an invocation/v1 ref")
        firing = self.sponsoring_transition_firing_ref
        if (firing is not None
                and (not isinstance(firing, VersionRef)
                     or firing.entity_type != "transition_firing/v1")):
            raise TypeError(
                "agent initialization firing must be transition_firing/v1")
        if (not isinstance(self.role, str) or not self.role
                or self.role != self.role.strip()):
            raise ValueError("agent initialization role is required")
        for values, label in ((self.tool_names, "tools"),
                              (self.skill_ids, "skills")):
            if (tuple(values) != tuple(sorted(set(values)))
                    or any(not isinstance(value, str) or not value
                           for value in values)):
                raise AgentLoopProtocolError(
                    f"agent initialization {label} must be unique and sorted")
        located = tuple(self.located_inputs)
        if (any(not isinstance(item, LocatedAgentInput) for item in located)
                or len({item.sandbox_path for item in located}) != len(located)):
            raise AgentLoopProtocolError(
                "agent initialization located inputs must be exact and unique")
        if self.output_kind not in {
                "ordinary_document", "executable_design_index",
                "module_declaration", "a2c_critic_document",
                "finalization_review"}:
            raise ValueError("agent initialization output kind is not closed")
        if (not isinstance(self.output_contract, str)
                or not self.output_contract.strip()):
            raise ValueError("agent initialization output contract is required")
        if not isinstance(self.payload, bytes) or not self.payload:
            raise TypeError("agent initialization payload must be nonempty bytes")
        object.__setattr__(self, "located_inputs", located)


@dataclass(frozen=True, slots=True)
class AgentToolCallFact:
    """Content-free protocol facts reconstructed from one raw response ref."""

    tool_call_ordinal: int
    tool_call_id: str | None
    tool_name: str | None
    raw_arguments: str | None
    syntax_errors: tuple[str, ...]
    action_identity_kind: str
    action_identity_key: str

    def __post_init__(self) -> None:
        if (isinstance(self.tool_call_ordinal, bool)
                or not isinstance(self.tool_call_ordinal, int)
                or self.tool_call_ordinal < 0):
            raise ValueError("tool-call fact ordinal is invalid")
        if (self.tool_call_id is not None
                and (not isinstance(self.tool_call_id, str)
                     or not self.tool_call_id)):
            raise TypeError("tool-call fact id is invalid")
        if (self.tool_name is not None
                and (not isinstance(self.tool_name, str) or not self.tool_name)):
            raise TypeError("tool-call fact tool name is invalid")
        if (self.raw_arguments is not None
                and not isinstance(self.raw_arguments, str)):
            raise TypeError("tool-call raw arguments must be text or None")
        errors = tuple(self.syntax_errors)
        if (len(set(errors)) != len(errors)
                or any(not isinstance(value, str) or not value
                       for value in errors)):
            raise AgentLoopProtocolError(
                "tool-call syntax errors must be unique closed facts")
        if self.action_identity_kind not in {
                "tool_call_id", "malformed_position"}:
            raise ValueError("tool-call action identity kind is invalid")
        if not isinstance(self.action_identity_key, str) or not self.action_identity_key:
            raise ValueError("tool-call action identity key is required")
        if (self.action_identity_kind == "tool_call_id"
                and self.action_identity_key != self.tool_call_id):
            raise AgentLoopProtocolError(
                "tool-call identity differs from its exact key")
        if (self.action_identity_kind == "malformed_position"
                and self.action_identity_key
                != str(self.tool_call_ordinal)):
            raise AgentLoopProtocolError(
                "malformed tool-call identity differs from its response position")
        object.__setattr__(self, "syntax_errors", errors)


@dataclass(frozen=True, slots=True)
class AgentLoopSnapshot:
    loop_id: str
    loop_version_id: str
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    state: AgentLoopState
    revision: int
    next_turn_sequence: int
    llm_turn_budget: int | None
    llm_turns_used: int
    model_condition: str
    tool_catalog_ref: ResourceVersionRef
    current_candidate_ref: VersionRef | None
    adopted_candidate_ref: VersionRef | None
    disposition_ref: VersionRef | None
    owner_ref: VersionRef
    written_resource_refs: tuple[ResourceVersionRef, ...] = ()
    cap_review_resource_ref: ResourceVersionRef | None = None
    workspace_binding_ref: VersionRef | None = None
    workspace_lineage_ref: VersionRef | None = None
    workspace_base_revision_ref: VersionRef | None = None
    workspace_revision_ref: VersionRef | None = None
    workspace_changed_paths: tuple[str, ...] = ()
    workspace_deleted_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_id("agent_loop", self.loop_id)
        _require_id("agent_loop_version", self.loop_version_id)
        if (not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "invocation/v1"):
            raise TypeError("agent loop requires an exact invocation ref")
        if not isinstance(self.operation_binding_ref, VersionRef):
            raise TypeError("agent loop requires an operation binding ref")
        if not isinstance(self.state, AgentLoopState):
            raise TypeError("agent loop state must be typed")
        for value, label in ((self.revision, "revision"),
                             (self.next_turn_sequence, "turn sequence"),
                             (self.llm_turns_used, "turn usage")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"agent loop {label} is invalid")
        if (self.llm_turn_budget is not None
                and (isinstance(self.llm_turn_budget, bool)
                     or not isinstance(self.llm_turn_budget, int)
                     or self.llm_turn_budget < 0)):
            raise ValueError("agent loop turn budget is invalid")
        # A configured per-agent cap is positive.  A paired critic can still
        # start with no returned-call allowance after actor-cap compaction;
        # null is the explicit unmetered contract.
        if ((self.llm_turn_budget is not None
             and self.llm_turns_used > self.llm_turn_budget)
                or self.next_turn_sequence != self.llm_turns_used):
            raise AgentLoopProtocolError("agent-loop turn accounting is inconsistent")
        if (not isinstance(self.model_condition, str)
                or not self.model_condition
                or self.model_condition != self.model_condition.strip()):
            raise ValueError("agent-loop model condition must be exact input")
        if not isinstance(self.tool_catalog_ref, ResourceVersionRef):
            raise TypeError("agent loop requires exact tool catalog ref")
        if not isinstance(self.owner_ref, VersionRef):
            raise TypeError("agent loop requires an exact rotating owner ref")
        written = tuple(self.written_resource_refs)
        if (any(not isinstance(ref, ResourceVersionRef) for ref in written)
                or len(set(written)) != len(written)):
            raise AgentLoopProtocolError(
                "agent-loop written resources must be unique exact refs")
        object.__setattr__(self, "written_resource_refs", written)
        if (self.cap_review_resource_ref is not None
                and not isinstance(
                    self.cap_review_resource_ref, ResourceVersionRef)):
            raise TypeError(
                "agent-loop cap review requires one exact resource ref")
        workspace_refs = (
            self.workspace_binding_ref, self.workspace_lineage_ref,
            self.workspace_base_revision_ref)
        if any(ref is not None for ref in workspace_refs):
            if (any(not isinstance(ref, VersionRef)
                    for ref in workspace_refs)
                    or self.workspace_binding_ref.entity_type
                    != "workspace_binding/v1"
                    or self.workspace_lineage_ref.entity_type
                    != "workspace_revision/v1"
                    or self.workspace_base_revision_ref.entity_type
                    != "workspace_revision/v1"
                    or (self.workspace_revision_ref is not None
                        and (not isinstance(
                            self.workspace_revision_ref, VersionRef)
                             or self.workspace_revision_ref.entity_type
                             != "workspace_revision/v1"))):
                raise TypeError(
                    "agent loop workspace authority is incomplete")
        elif self.workspace_revision_ref is not None:
            raise TypeError(
                "agent loop revision requires workspace binding authority")
        for paths, label in (
                (self.workspace_changed_paths, "changed"),
                (self.workspace_deleted_paths, "deleted")):
            if (tuple(paths) != tuple(sorted(set(paths)))
                    or any(not isinstance(path, str) or not path
                           for path in paths)):
                raise AgentLoopProtocolError(
                    f"agent-loop workspace {label} paths are not canonical")
        if set(self.workspace_changed_paths) & set(
                self.workspace_deleted_paths):
            raise AgentLoopProtocolError(
                "agent-loop workspace change/delete paths overlap")
        if self.state == AgentLoopState.COMPLETED:
            ordinary_completion = bool(written)
            cap_completion = self.cap_review_resource_ref is not None
            if ((ordinary_completion and cap_completion)
                    or self.adopted_candidate_ref is not None
                    or self.disposition_ref is not None):
                raise AgentLoopProtocolError(
                    "completed file loop cannot mix ordinary-write and cap-review "
                    "closures or carry adoption/disposition state")


@dataclass(frozen=True, slots=True)
class AgentTurnRecord:
    turn_id: str
    loop_id: str
    sequence: int
    llm_invocation_ref: VersionRef
    llm_invocation_attempt_ref: VersionRef
    response_resource_ref: ResourceVersionRef
    response_size: int
    response_protocol_ref: str
    text_present: bool
    tool_calls: tuple[AgentToolCallFact, ...]
    finish_reason: str | None
    recorded_revision: int

    def __post_init__(self) -> None:
        _require_id("agent_turn", self.turn_id)
        _require_id("agent_loop", self.loop_id)
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("agent turn sequence is invalid")
        if (isinstance(self.recorded_revision, bool)
                or not isinstance(self.recorded_revision, int)
                or self.recorded_revision < 1):
            raise ValueError("agent turn revision is invalid")
        if (not isinstance(self.llm_invocation_ref, VersionRef)
                or self.llm_invocation_ref.entity_type
                != "llm_invocation_spec/v1"):
            raise TypeError("agent turn requires an exact LLM invocation ref")
        if (not isinstance(self.llm_invocation_attempt_ref, VersionRef)
                or self.llm_invocation_attempt_ref.entity_type
                != "llm_invocation_attempt/v1"):
            raise TypeError(
                "agent turn requires an exact LLM invocation attempt ref")
        if not isinstance(self.response_resource_ref, ResourceVersionRef):
            raise TypeError("agent turn requires raw response resource ref")
        if (isinstance(self.response_size, bool)
                or not isinstance(self.response_size, int)
                or self.response_size < 1):
            raise ValueError("agent turn response size is invalid")
        if (not isinstance(self.response_protocol_ref, str)
                or not self.response_protocol_ref
                or self.response_protocol_ref
                != self.response_protocol_ref.strip()):
            raise ValueError("agent turn response protocol ref is required")
        if not isinstance(self.text_present, bool):
            raise TypeError("agent turn text presence must be boolean")
        calls = tuple(self.tool_calls)
        if (any(not isinstance(call, AgentToolCallFact) for call in calls)
                or tuple(call.tool_call_ordinal for call in calls)
                != tuple(range(len(calls)))):
            raise AgentLoopProtocolError(
                "turn tool-call facts lost exact LLM response order")
        if (self.finish_reason is not None
                and not isinstance(self.finish_reason, str)):
            raise TypeError("agent turn finish reason must be text or None")
        object.__setattr__(self, "tool_calls", calls)


@dataclass(frozen=True, slots=True)
class AgentActionRecord:
    action_id: str
    loop_id: str
    turn_sequence: int
    tool_call_ordinal: int
    tool_call_id: str | None
    action_identity_kind: str
    action_identity_key: str
    tool_name: str | None
    raw_arguments: str | None
    arguments: Mapping[str, Any] | None
    expected_revision: int
    state: AgentLoopState
    result_refs: tuple[VersionRef, ...] = ()
    tool_error_ref: VersionRef | None = None
    result_metadata: Mapping[str, Any] | None = None
    managed_action: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        expected_action_id = (
            stable_action_id(
                self.loop_id, self.turn_sequence, self.action_identity_key)
            if self.action_identity_kind == "tool_call_id"
            else stable_malformed_action_id(
                self.loop_id, self.turn_sequence, self.tool_call_ordinal))
        if self.action_id != expected_action_id:
            raise AgentLoopProtocolError("agent action id is not replay-stable")
        if (isinstance(self.tool_call_ordinal, bool)
                or not isinstance(self.tool_call_ordinal, int)
                or self.tool_call_ordinal < 0):
            raise ValueError("agent action LLM ordinal is invalid")
        if (self.tool_call_id is not None
                and not isinstance(self.tool_call_id, str)):
            raise TypeError("agent action LLM id must be text or None")
        if not isinstance(self.action_identity_key, str) or not self.action_identity_key:
            raise ValueError("agent action identity key is required")
        if self.action_identity_kind not in {
                "tool_call_id", "malformed_position"}:
            raise ValueError("agent action identity kind is invalid")
        malformed_identity = self.action_identity_kind == "malformed_position"
        if (not malformed_identity
                and self.tool_call_id != self.action_identity_key):
            raise AgentLoopProtocolError(
                "valid LLM id must remain exact action identity")
        if (malformed_identity
                and self.action_identity_key
                != str(self.tool_call_ordinal)):
            raise AgentLoopProtocolError(
                "malformed action identity differs from LLM response position")
        if (malformed_identity
                and self.state not in {AgentLoopState.ACTION_PENDING,
                                       AgentLoopState.ACTION_REJECTED}):
            raise AgentLoopProtocolError(
                "malformed LLM identity cannot produce an applied action")
        if self.tool_name is not None and not isinstance(self.tool_name, str):
            raise TypeError("agent action tool name must be text or None")
        if (self.raw_arguments is not None
                and not isinstance(self.raw_arguments, str)):
            raise TypeError("agent action raw arguments must be text or None")
        if malformed_identity:
            if self.arguments is not None:
                raise AgentLoopProtocolError(
                    "malformed action cannot carry parsed arguments")
        elif self.arguments is None:
            if self.state not in {
                    AgentLoopState.ACTION_PENDING,
                    AgentLoopState.ACTION_REJECTED}:
                raise AgentLoopProtocolError(
                    "applied action requires explicit parsed arguments")
        elif not isinstance(self.arguments, Mapping):
            raise AgentLoopProtocolError(
                "agent action arguments must be an object or None")
        else:
            try:
                json.dumps(
                    dict(self.arguments), ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    "agent action arguments must be closed JSON values") from exc
        if (isinstance(self.expected_revision, bool)
                or not isinstance(self.expected_revision, int)
                or self.expected_revision < 0):
            raise ValueError("agent action expected revision is invalid")
        if self.state not in {
                AgentLoopState.ACTION_PENDING,
                AgentLoopState.ACTION_APPLIED,
                AgentLoopState.ACTION_REJECTED,
                AgentLoopState.CANDIDATE_COMMITTED,
                AgentLoopState.CANDIDATE_ADOPTED,
                AgentLoopState.DISPOSITION_RECORDED,
                AgentLoopState.COMPACTING,
                AgentLoopState.COMPLETED,
                AgentLoopState.WAITING_RESOURCE}:
            raise AgentLoopProtocolError("agent action has an impossible state")
        if self.state == AgentLoopState.ACTION_REJECTED:
            if self.tool_error_ref is None or self.result_refs:
                raise AgentLoopProtocolError(
                    "rejected action requires one tool error and no results")
        elif self.tool_error_ref is not None:
            raise AgentLoopProtocolError(
                "non-rejected action cannot carry a tool error")
        if self.result_metadata is not None:
            try:
                json.dumps(
                    self.result_metadata, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    "agent action metadata must be JSON-serializable") from exc
        if self.managed_action is None:
            self._validate_result_metadata()
        else:
            self._validate_managed_action()
        if (self.tool_name == "query_kb"
                and self.state != AgentLoopState.ACTION_REJECTED):
            if (self.state != AgentLoopState.ACTION_APPLIED
                    or not isinstance(self.result_metadata, Mapping)):
                raise AgentLoopProtocolError(
                    "KB query must settle as one applied action")
            raw_resource = self.result_metadata["result_resource_ref"]
            assert isinstance(raw_resource, Mapping)
            resource_version_ref = ResourceVersionRef(
                TypedId.parse(
                    str(raw_resource["resource_id"]), expected="resource"),
                TypedId.parse(
                    str(raw_resource["resource_version_id"]),
                    expected="resource_version"),
            ).as_version_ref()
            raw_access = self.result_metadata["body_access_ref"]
            expected_refs = (resource_version_ref,)
            if raw_access is not None:
                assert isinstance(raw_access, Mapping)
                expected_refs += (VersionRef(
                    str(raw_access["entity_type"]),
                    TypedId.parse(str(raw_access["logical_id"])),
                    TypedId.parse(str(raw_access["version_id"])),
                ),)
            if self.result_refs != expected_refs:
                raise AgentLoopProtocolError(
                    "KB query result refs differ from its Registry closure")
        if (self.tool_name == "query_environment_resources"
                and self.state != AgentLoopState.ACTION_REJECTED):
            if (self.state != AgentLoopState.ACTION_APPLIED
                    or not isinstance(self.result_metadata, Mapping)):
                raise AgentLoopProtocolError(
                    "environment resource query must settle as one applied action")
            raw_ref = self.result_metadata["resource_ref"]
            assert isinstance(raw_ref, Mapping)
            exact_resource_ref = ResourceVersionRef(
                TypedId.parse(
                    str(raw_ref["resource_id"]), expected="resource"),
                TypedId.parse(
                    str(raw_ref["resource_version_id"]),
                    expected="resource_version"),
            )
            if self.result_refs != (exact_resource_ref.as_version_ref(),):
                raise AgentLoopProtocolError(
                    "environment query result refs differ from its Registry resource")
        if (self.tool_name == "query_registry_resources"
                and self.state != AgentLoopState.ACTION_REJECTED
                and self.result_refs):
            raise AgentLoopProtocolError(
                "resource catalog metadata must not deliver resource bodies")
        if (self.tool_name in {"read_action_output", "read_managed_output", "read_tool_program_output"}
                and self.state != AgentLoopState.ACTION_REJECTED
                and not (isinstance(self.result_metadata, Mapping)
                         and self.result_metadata.get("kind") == "tool_result_archive_page/v1")):
            if (self.state != AgentLoopState.ACTION_APPLIED
                    or not isinstance(self.result_metadata, Mapping)):
                raise AgentLoopProtocolError(
                    "action output read must settle as one applied action")
            raw_action_ref = self.result_metadata["agent_action_ref"]
            assert isinstance(raw_action_ref, Mapping)
            source_action_ref = VersionRef(
                str(raw_action_ref["entity_type"]),
                TypedId.parse(
                    str(raw_action_ref["logical_id"]),
                    expected="agent_action"),
                TypedId.parse(
                    str(raw_action_ref["version_id"]),
                    expected="agent_action_version"),
            )
            if self.result_refs != (source_action_ref,):
                raise AgentLoopProtocolError(
                    "action output result ref differs from its exact source action")
            if (self.tool_name == "read_managed_output"
                    and (not isinstance(self.arguments, Mapping)
                         or self.arguments.get("agent_action_ref") != raw_action_ref
                         or self.arguments.get("terminal_receipt_ref")
                         != self.result_metadata["terminal_receipt_ref"]
                         or self.arguments.get("offset_chars", 0)
                         != self.result_metadata["offset_chars"])):
                raise AgentLoopProtocolError(
                    "managed output page differs from its requested locator")
            if (self.tool_name == "read_tool_program_output"
                    and (self.arguments.get("agent_action_ref") != raw_action_ref
                         or self.arguments.get("output_resource_ref") != self.result_metadata["output_resource_ref"]
                         or self.arguments.get("offset_chars", 0) != self.result_metadata["offset_chars"])):
                raise AgentLoopProtocolError("program output page differs from its requested locator")
        if (self.tool_name == "delegate_leaf"
                and self.state != AgentLoopState.ACTION_REJECTED):
            if (self.state != AgentLoopState.ACTION_APPLIED
                    or not isinstance(self.result_metadata, Mapping)):
                raise AgentLoopProtocolError(
                    "delegated subtask must settle once with its final result")
            raw_resource = self.result_metadata["result_resource_ref"]
            assert isinstance(raw_resource, Mapping)
            result_resource_ref = ResourceVersionRef(
                TypedId.parse(
                    str(raw_resource["resource_id"]), expected="resource"),
                TypedId.parse(
                    str(raw_resource["resource_version_id"]),
                    expected="resource_version"),
            )
            if self.result_refs != (result_resource_ref.as_version_ref(),):
                raise AgentLoopProtocolError(
                    "delegated subtask result ref differs from its final result")

    def _validate_managed_action(self) -> None:
        value = self.managed_action
        if not isinstance(value, Mapping):
            raise AgentLoopProtocolError("managed action identity is missing")
        fields = {
            "provider_name", "registration_key", "selector",
            "plugin_catalog_digest", "binding_digest", "effect", "outcome",
            "request_admission_receipt_ref", "admitted_at_utc",
            "started_receipt_ref", "terminal_receipt_ref",
            "model_visible_result_ref", "non_delivery_reason", "output",
            "error", "max_result_bytes",
        }
        if (set(value) != fields
                or self.action_identity_kind != "tool_call_id"
                or self.tool_name != value.get("provider_name")
                or self.result_refs
                or value.get("effect") not in {
                    "pure", "external_read", "external_write"}
                or value.get("outcome") not in {
                    "returned", "failed", "outcome_unknown", "rejected"}
                or not isinstance(value.get("max_result_bytes"), int)
                or isinstance(value.get("max_result_bytes"), bool)
                or not 0 < value["max_result_bytes"] <= 16 * 1024 * 1024):
            raise AgentLoopProtocolError(
                "managed action differs from its closed identity")
        try:
            output_size = len(json.dumps(
                value.get("output"), ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8"))
            error_size = len(json.dumps(
                value.get("error"), ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise AgentLoopProtocolError(
                "managed action output/error must be closed JSON") from exc

        def resource_ref(raw: object) -> bool:
            return (isinstance(raw, Mapping)
                    and set(raw) == {"resource_id", "resource_version_id"}
                    and isinstance(raw.get("resource_id"), str)
                    and raw["resource_id"].startswith("resource:")
                    and isinstance(raw.get("resource_version_id"), str)
                    and raw["resource_version_id"].startswith(
                        "resource_version:"))

        outcome = value["outcome"]
        if outcome == "returned":
            valid = (
                self.state == AgentLoopState.ACTION_APPLIED
                and self.tool_error_ref is None
                and isinstance(self.result_metadata, Mapping)
                and set(self.result_metadata) == {
                    "kind", "output", "terminal_receipt_ref"}
                and self.result_metadata.get("kind")
                == "managed_native_plugin_result/v1"
                and self.result_metadata.get("output") == value.get("output")
                and self.result_metadata.get("terminal_receipt_ref")
                == value.get("terminal_receipt_ref")
                and value.get("error") is None
                and output_size <= value["max_result_bytes"]
                and resource_ref(value.get("request_admission_receipt_ref"))
                and value.get("request_admission_receipt_ref")
                == value.get("started_receipt_ref")
                and resource_ref(value.get("terminal_receipt_ref"))
                and value.get("model_visible_result_ref") is None
                and value.get("non_delivery_reason")
                == "provider_delivery_not_recorded"
                and isinstance(value.get("admitted_at_utc"), str))
        elif outcome == "rejected":
            valid = (
                self.state == AgentLoopState.ACTION_REJECTED
                and self.tool_error_ref is not None
                and self.result_metadata is None
                and all(value.get(name) is None for name in (
                    "request_admission_receipt_ref", "admitted_at_utc",
                    "started_receipt_ref", "terminal_receipt_ref",
                    "model_visible_result_ref", "output"))
                and value.get("non_delivery_reason")
                == "rejected_before_dispatch"
                and value.get("error") is not None
                and error_size <= MANAGED_FRAMEWORK_ERROR_MAX_BYTES)
        else:
            valid = (
                self.state == AgentLoopState.ACTION_REJECTED
                and self.tool_error_ref is not None
                and self.result_metadata is None
                and resource_ref(value.get("request_admission_receipt_ref"))
                and value.get("request_admission_receipt_ref")
                == value.get("started_receipt_ref")
                and resource_ref(value.get("terminal_receipt_ref"))
                and value.get("model_visible_result_ref") is None
                and value.get("output") is None
                and value.get("non_delivery_reason") == (
                    "outcome_unknown" if outcome == "outcome_unknown"
                    else "managed_call_failed")
                and value.get("error") is not None
                and isinstance(value.get("admitted_at_utc"), str)
                and error_size <= MANAGED_FRAMEWORK_ERROR_MAX_BYTES)
        if not valid:
            raise AgentLoopProtocolError(
                "managed action lacks its exact closed outcome evidence")

    def _validate_result_metadata(self) -> None:
        value = self.result_metadata
        if self.state == AgentLoopState.ACTION_REJECTED:
            if value is not None:
                raise AgentLoopProtocolError(
                    "rejected action cannot carry result metadata")
            return
        if self.state == AgentLoopState.ACTION_PENDING:
            if (self.tool_name != "workspace"
                    or not isinstance(self.arguments, Mapping)
                    or self.arguments.get("execution_mode") != "monitored"
                    or self.result_refs
                    or self.tool_error_ref is not None
                    or value is not None):
                raise AgentLoopProtocolError(
                    "pending action must be one unsettled monitored workspace call")
            return
        if not isinstance(value, Mapping):
            raise AgentLoopProtocolError(
                "successful agent action requires closed result metadata")
        if (self.tool_name in {"read_managed_output", "read_tool_program_output"}
                and (value.get("kind") == "tool_result_archive_page/v1"
                     or "archive_loop_ref" in self.arguments)):
            from .result_archive import validate_archive_arguments, validate_result_archive_page
            try:
                validate_archive_arguments(self.arguments)
                validate_result_archive_page(value)
                raw_ref = value["archive_loop_ref"]
                source_ref = VersionRef(
                    "agent_loop/v1", TypedId.parse(raw_ref["logical_id"], expected="agent_loop"),
                    TypedId.parse(raw_ref["version_id"], expected="agent_loop_version"))
                valid = (
                    self.state == AgentLoopState.ACTION_APPLIED
                    and value["reader"] == self.tool_name
                    and raw_ref["logical_id"] == self.loop_id
                    and raw_ref == self.arguments["archive_loop_ref"]
                    and value["before_turn_sequence"] == self.arguments["before_turn_sequence"]
                    and value["before_turn_sequence"] <= self.turn_sequence
                    and value["offset"] == self.arguments.get("offset", 0)
                    and self.result_refs == (source_ref,)
                    and len(json.dumps(dict(value), ensure_ascii=True, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode("utf-8"))
                    <= self.arguments.get("max_bytes", 10000))
            except (ValueError, TypeError, KeyError) as exc:
                raise AgentLoopProtocolError("result archive read has invalid closed metadata") from exc
            if not valid:
                raise AgentLoopProtocolError("result archive page differs from its requested loop/cut/reader")
            return
        expected: dict[str, tuple[str, frozenset[str]]] = {
            "query_kb": (
                "external_kb_query/v2", frozenset({
                    "kind", "operation", "result_resource_ref",
                    "body_access_ref", "result_count"})),
            "query_environment_resources": (
                "execution_environment_resources/v1", frozenset({
                    "kind", "environment_name", "python_version",
                    "inventory_complete", "requested_packages",
                    "installed_resources", "not_installed",
                    "allowed_by_tool", "resource_ref"})),
            "query_registry_resources": (
                "registry_resource_catalog/v1", frozenset({
                    "kind", "query", "view", "offset", "page_size",
                    "result_count", "total_count", "next_offset", "rows"})),
            "read_action_output": (
                "workspace_action_output_page/v1", frozenset({
                    "kind", "agent_action_ref", "stream", "content",
                    "offset_chars", "next_offset_chars", "total_chars",
                    "source_output_truncated"})),
            "read_managed_output": (
                "managed_output_page/v1", frozenset({
                    "kind", "agent_action_ref", "terminal_receipt_ref",
                    "reader", "content", "offset_chars", "next_offset_chars",
                    "total_chars", "truncated"})),
            "read_tool_program_output": (
                "tool_program_output_page/v1", frozenset({
                    "kind", "agent_action_ref", "output_resource_ref", "reader",
                    "content", "offset_chars", "next_offset_chars", "total_chars",
                    "truncated", "source_status"})),
            "run_tool_program": (
                "tool_program_result/v1", frozenset({
                    "kind", "program_invocation_ref", "output_resource_ref", "status",
                    "call_count", "reader", "agent_action_ref"})),
            "read_file": (
                "registered_file_read/v1", frozenset({
                    "kind", "path", "resource_ref", "use_receipt_ref"})),
            "search_text": (
                "registered_text_search/v1", frozenset({
                    "kind", "path", "query", "case_sensitive",
                    "offset_match", "next_offset_match", "total_matches",
                    "matches", "resource_ref", "use_receipt_ref"})),
            "write_file": (
                "registered_file_write/v1", frozenset({
                    "kind", "path", "description", "output_port_id",
                    "resource_ref", "registered"})),
            "request_resource": (
                "request_resource", frozenset()),
            "workspace": (
                "workspace_execution/v1", frozenset({
                    "kind", "status", "exit_code", "stdout", "stderr",
                    "output_truncated", "command_started"})),
            "complete_interaction": (
                "interaction_completion/v1", frozenset({
                    "kind", "completed", "written_resource_refs",
                    "selected_outcome_id"})),
            "delegate_leaf": (
                "parent_owned_delegated_subtask_result/v1", frozenset({
                    "kind", "result_resource_ref", "child_session_id",
                    "local_sequence", "llm_invocation_ref",
                    "llm_invocation_attempt_ref"})),
        }
        contract = expected.get(self.tool_name or "")
        if contract is None:
            raise AgentLoopProtocolError(
                "agent action result metadata differs from its tool contract")
        if self.tool_name == "request_resource":
            extension_fields = frozenset({
                "kind", "transition_firing_ref", "transition_id",
                "invocation_ref", "operation_execution_lease_ref",
                "operation_binding_ref", "agent_loop_ref", "agent_turn_ref",
                "agent_action_ref", "resource_use_occurrence_ref",
                "resource_access_lifecycle_ref", "resource_access_grant_ref",
                "resource_access_lease_ref", "logical_resource_id",
                "lock_resource_ref", "resource_ref", "access_mode",
                "access_checkpoint_ref", "access_net_ref",
                "access_claim_epoch", "resource_token_ref",
                "lease_pool_place", "lease_identity_ref",
                "petri_input_arc_mode", "petri_output_arc_mode",
                "petri_arc_kind", "return_arc_required", "llm_turns_used",
                "writer_fencing_epoch", "same_firing_continuation",
                "available_on_next_turn"})
            if (value.get("kind") != "live_firing_resource_extension/v1"
                    or set(value) != extension_fields):
                raise AgentLoopProtocolError(
                    "agent action result metadata differs from its tool contract")
        elif value.get("kind") != contract[0] or set(value) != contract[1]:
            raise AgentLoopProtocolError(
                "agent action result metadata differs from its tool contract")

        def exact_ref(raw: object, entity_type: str | None = None) -> bool:
            return (isinstance(raw, Mapping)
                    and set(raw) == {"entity_type", "logical_id", "version_id"}
                    and (entity_type is None
                         or raw.get("entity_type") == entity_type)
                    and all(isinstance(raw.get(key), str)
                            and ":" in raw[key]
                            for key in ("logical_id", "version_id")))

        def resource_ref(raw: object) -> bool:
            return (isinstance(raw, Mapping)
                    and set(raw) == {"resource_id", "resource_version_id"}
                    and isinstance(raw.get("resource_id"), str)
                    and raw["resource_id"].startswith("resource:")
                    and isinstance(raw.get("resource_version_id"), str)
                    and raw["resource_version_id"].startswith(
                        "resource_version:"))

        def typed_ref(
                raw: object, entity_type: str,
                logical_kind: str, version_kind: str) -> bool:
            if not exact_ref(raw, entity_type):
                return False
            assert isinstance(raw, Mapping)
            try:
                TypedId.parse(str(raw["logical_id"]), expected=logical_kind)
                TypedId.parse(str(raw["version_id"]), expected=version_kind)
            except (TypeError, ValueError):
                return False
            return True

        valid = False
        if self.tool_name == "query_kb":
            count = value["result_count"]
            operation = value["operation"]
            access_ref = value["body_access_ref"]
            valid = (resource_ref(value["result_resource_ref"])
                     and operation in {"search", "read"}
                     and isinstance(count, int) and not isinstance(count, bool)
                     and count >= 0
                     and ((operation == "search" and access_ref is None)
                          or (operation == "read" and count == 1
                              and exact_ref(
                                  access_ref,
                                  "firing_external_kb_read/v2"))))
        elif self.tool_name == "query_environment_resources":
            requested = value["requested_packages"]
            installed = value["installed_resources"]
            not_installed = value["not_installed"]
            allowed_by_tool = value["allowed_by_tool"]
            valid = (
                resource_ref(value["resource_ref"])
                and value["environment_name"] == "research-exp"
                and isinstance(value["python_version"], str)
                and bool(value["python_version"])
                and value["inventory_complete"] is False
                and isinstance(requested, list) and bool(requested)
                and len(requested) <= 16
                and all(isinstance(name, str) and bool(name)
                        for name in requested)
                and requested == sorted(set(requested))
                and isinstance(installed, list)
                and all(isinstance(item, Mapping)
                        and set(item) == {"kind", "name", "version"}
                        and item["kind"] == "python_distribution"
                        and isinstance(item["name"], str) and bool(item["name"])
                        and isinstance(item["version"], str)
                        and bool(item["version"])
                        for item in installed)
                and [item["name"] for item in installed]
                == sorted({item["name"] for item in installed})
                and isinstance(not_installed, list)
                and all(isinstance(name, str) and bool(name)
                        for name in not_installed)
                and not_installed == sorted(set(not_installed))
                and isinstance(allowed_by_tool, list)
                and allowed_by_tool == ["math", "numpy", "scipy"]
                and sorted(
                    [item["name"] for item in installed] + not_installed)
                == requested)
        elif self.tool_name == "query_registry_resources":
            rows = value["rows"]
            offset = value["offset"]
            page_size = value["page_size"]
            result_count = value["result_count"]
            total_count = value["total_count"]
            next_offset = value["next_offset"]
            view = value["view"]
            catalog_row_fields = {
                "resource_id", "resource_version_id",
                "relative_path", "file_name", "description_summary",
                "description_summary_truncated", "content_role",
                "output_port_id", "producer_ref",
                "producer_transition_id", "producer_node_ref",
                "transition_firing_ref", "producer_turn",
                "settlement_status", "document_provenance",
                "source_execution", "destination_publication",
                "lineage", "currentness"}
            attachment_row_fields = catalog_row_fields | {
                "attachment_id", "attachment_ordinal",
                "attachment_package_ref", "media_type", "size_bytes",
                "availability"}
            valid = (
                isinstance(value["query"], str)
                and len(value["query"]) <= 256
                and view in {"current", "history"}
                and isinstance(offset, int) and not isinstance(offset, bool)
                and offset >= 0
                and isinstance(page_size, int)
                and not isinstance(page_size, bool)
                and 1 <= page_size <= 20
                and isinstance(result_count, int)
                and not isinstance(result_count, bool)
                and 0 <= result_count <= page_size
                and isinstance(total_count, int)
                and not isinstance(total_count, bool)
                and total_count >= result_count
                and isinstance(rows, list)
                and len(rows) == result_count
                and all(
                    isinstance(item, Mapping)
                    and frozenset(item) in {
                        frozenset(catalog_row_fields),
                        frozenset(attachment_row_fields)}
                    and isinstance(item["resource_id"], str)
                    and item["resource_id"].startswith("resource:")
                    and isinstance(item["resource_version_id"], str)
                    and item["resource_version_id"].startswith(
                        "resource_version:")
                    and isinstance(item["relative_path"], str)
                    and bool(item["relative_path"])
                    and isinstance(item["file_name"], str)
                    and bool(item["file_name"])
                    and (item["description_summary"] is None
                         or isinstance(item["description_summary"], str)
                         and bool(item["description_summary"])
                         and len(item["description_summary"]) <= 600)
                    and isinstance(
                        item["description_summary_truncated"], bool)
                    and (item["content_role"] is None
                         or isinstance(item["content_role"], str)
                         and bool(item["content_role"]))
                    and (item["output_port_id"] is None
                         or isinstance(item["output_port_id"], str)
                         and bool(item["output_port_id"]))
                    and (item["producer_ref"] is None
                         or exact_ref(item["producer_ref"]))
                    and (item["producer_transition_id"] is None
                         or isinstance(item["producer_transition_id"], str)
                         and bool(item["producer_transition_id"]))
                    and (item["producer_node_ref"] is None
                         or exact_ref(
                             item["producer_node_ref"],
                             "node_declaration/v1"))
                    and (item["transition_firing_ref"] is None
                         or exact_ref(item["transition_firing_ref"]))
                    and (item["producer_turn"] is None
                         or isinstance(item["producer_turn"], int)
                         and not isinstance(item["producer_turn"], bool)
                         and item["producer_turn"] >= 0)
                    and item["settlement_status"] in {
                        None, "provisional", "settled", "registered"}
                    and (item["document_provenance"] is None
                         or isinstance(item["document_provenance"], Mapping))
                    and (item["source_execution"] is None
                         or isinstance(item["source_execution"], Mapping))
                    and isinstance(item["destination_publication"], Mapping)
                    and set(item["destination_publication"]) == {
                        "producer_ref", "transition_firing_ref",
                        "settlement_status"}
                    and isinstance(item["lineage"], Mapping)
                    and set(item["lineage"]) == {
                        "input_resource_refs", "derived_from_refs",
                        "contributor_refs",
                        "tool_evidence_refs", "supersedes_ref",
                        "address_binding_ref",
                        "previous_address_binding_ref"}
                    and all(
                        (item["lineage"][field] is None
                         or isinstance(item["lineage"][field], list)
                         and all(exact_ref(ref)
                                 for ref in item["lineage"][field])
                         and len({json.dumps(ref, sort_keys=True)
                                  for ref in item["lineage"][field]})
                         == len(item["lineage"][field]))
                        for field in {
                            "input_resource_refs", "derived_from_refs",
                            "contributor_refs",
                            "tool_evidence_refs"})
                    and (item["lineage"]["supersedes_ref"] is None
                         or exact_ref(
                             item["lineage"]["supersedes_ref"],
                             "resource_version/v1"))
                    and (item["lineage"]["address_binding_ref"] is None
                         or exact_ref(
                             item["lineage"]["address_binding_ref"],
                             "resource_address_binding/v1"))
                    and (item["lineage"][
                            "previous_address_binding_ref"] is None
                         or exact_ref(
                             item["lineage"][
                                 "previous_address_binding_ref"],
                             "resource_address_binding/v1"))
                    and ((item["transition_firing_ref"] is None
                          and item["settlement_status"] in {
                              None, "registered"})
                         or (item["transition_firing_ref"] is not None
                             and item["settlement_status"] in {
                                 "provisional", "settled"}))
                    and item["currentness"] in {
                        "current", "superseded"}
                    and (
                        item["content_role"] == "user_attachment_member"
                        and set(item) == attachment_row_fields
                        and item["settlement_status"] == "registered"
                        and item["currentness"] == "current"
                        and item["producer_ref"] is None
                        and item["producer_transition_id"] is None
                        and item["producer_node_ref"] is None
                        and item["transition_firing_ref"] is None
                        and item["producer_turn"] is None
                        and item["document_provenance"] is None
                        and item["source_execution"] is None
                        and item["destination_publication"] == {
                            "producer_ref": None,
                            "transition_firing_ref": None,
                            "settlement_status": "registered"}
                        and item["lineage"] == {
                            "input_resource_refs": None,
                            "derived_from_refs": None,
                            "contributor_refs": None,
                            "tool_evidence_refs": None,
                            "supersedes_ref": None,
                            "address_binding_ref": None,
                            "previous_address_binding_ref": None}
                        and isinstance(item["attachment_id"], str)
                        and bool(item["attachment_id"])
                        and isinstance(item["attachment_ordinal"], int)
                        and not isinstance(item["attachment_ordinal"], bool)
                        and item["attachment_ordinal"] >= 0
                        and resource_ref(item["attachment_package_ref"])
                        and isinstance(item["media_type"], str)
                        and bool(item["media_type"])
                        and isinstance(item["size_bytes"], int)
                        and not isinstance(item["size_bytes"], bool)
                        and item["size_bytes"] >= 1
                        and item["availability"] == "request_required"
                        or item["content_role"] != "user_attachment_member"
                        and set(item) == catalog_row_fields
                        and item["settlement_status"] != "registered"
                        and item["lineage"]["address_binding_ref"]
                        is not None)
                    for item in rows)
                and (view == "history"
                     or all(item["currentness"] == "current"
                            for item in rows))
                and (next_offset is None
                     and offset + result_count >= total_count
                     or isinstance(next_offset, int)
                     and not isinstance(next_offset, bool)
                     and next_offset == offset + result_count
                     and next_offset < total_count))
        elif self.tool_name == "write_file":
            valid = (isinstance(value["path"], str) and bool(value["path"])
                     and isinstance(value["description"], str)
                     and bool(value["description"].strip())
                     and len(value["description"]) <= 600
                     and isinstance(value["output_port_id"], str)
                     and bool(value["output_port_id"])
                     and resource_ref(value["resource_ref"])
                     and value["registered"] is True)
        elif self.tool_name == "read_action_output":
            ref = value["agent_action_ref"]
            offset = value["offset_chars"]
            next_offset = value["next_offset_chars"]
            total = value["total_chars"]
            content = value["content"]
            valid = (
                typed_ref(
                    ref, "agent_action/v2", "agent_action",
                    "agent_action_version")
                and value["stream"] in {"stdout", "stderr"}
                and isinstance(content, str)
                and len(content) <= 32768
                and isinstance(offset, int) and not isinstance(offset, bool)
                and offset >= 0
                and isinstance(total, int) and not isinstance(total, bool)
                and total >= 0
                and (next_offset is None
                     or isinstance(next_offset, int)
                     and not isinstance(next_offset, bool)
                     and next_offset == offset + len(content)
                     and next_offset < total)
                and ((next_offset is None and offset + len(content) >= total)
                     or next_offset is not None)
                and isinstance(value["source_output_truncated"], bool))
        elif self.tool_name in {"read_managed_output", "read_tool_program_output"}:
            offset, next_offset = value["offset_chars"], value["next_offset_chars"]
            total, content = value["total_chars"], value["content"]
            valid = (
                typed_ref(value["agent_action_ref"],
                          "agent_action/v3" if self.tool_name == "read_managed_output" else "agent_action/v2",
                          "agent_action", "agent_action_version")
                and resource_ref(value["terminal_receipt_ref"] if self.tool_name == "read_managed_output"
                                 else value["output_resource_ref"])
                and value["reader"] == self.tool_name
                and (self.tool_name == "read_managed_output"
                     or value["source_status"] in {"returned", "failed", "cancelled", "outcome_unknown"})
                and isinstance(content, str)
                and type(offset) is int and type(total) is int
                and 0 <= offset <= total and offset + len(content) <= total
                and type(value["truncated"]) is bool
                and ((next_offset is None and offset + len(content) == total
                      and value["truncated"] is False)
                     or (type(next_offset) is int and next_offset == offset + len(content)
                         and offset < next_offset < total
                         and value["truncated"] is True)))
            if valid:
                budget = self.arguments.get("max_bytes", 10000)
                valid = (type(budget) is int and budget > 0
                         and len(json.dumps(dict(value), ensure_ascii=True,
                                            sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode("utf-8")) <= budget)
        elif self.tool_name == "run_tool_program":
            valid = (
                typed_ref(value["program_invocation_ref"], "agent_tool_program_invocation/v1",
                          "invocation", "invocation_version")
                and resource_ref(value["output_resource_ref"])
                and typed_ref(value["agent_action_ref"], "agent_action/v2",
                              "agent_action", "agent_action_version")
                and value["agent_action_ref"]["logical_id"] == self.action_id
                and value["status"] in {"returned", "failed", "cancelled", "outcome_unknown"}
                and type(value["call_count"]) is int and value["call_count"] >= 0
                and value["reader"] == "read_tool_program_output")
            if valid:
                raw=value["output_resource_ref"]
                expected_ref=VersionRef("resource_version/v1",
                    TypedId.parse(raw["resource_id"],expected="resource"),
                    TypedId.parse(raw["resource_version_id"],expected="resource_version"))
                valid=self.result_refs==(expected_ref,)
        elif self.tool_name == "read_file":
            valid = (
                isinstance(value["path"], str) and bool(value["path"])
                and resource_ref(value["resource_ref"])
                and exact_ref(
                    value["use_receipt_ref"], "resource_delivery/v1"))
        elif self.tool_name == "search_text":
            matches = value["matches"]
            offset = value["offset_match"]
            next_offset = value["next_offset_match"]
            total = value["total_matches"]
            valid = (
                isinstance(value["path"], str) and bool(value["path"])
                and isinstance(value["query"], str) and bool(value["query"])
                and len(value["query"]) <= 256
                and isinstance(value["case_sensitive"], bool)
                and isinstance(offset, int) and not isinstance(offset, bool)
                and offset >= 0
                and isinstance(total, int) and not isinstance(total, bool)
                and total >= 0
                and isinstance(matches, list) and len(matches) <= 20
                and all(
                    isinstance(item, Mapping)
                    and set(item) == {
                        "match_ordinal", "line_number", "column_number",
                        "match_start_chars", "excerpt_start_chars", "excerpt"}
                    and all(isinstance(item[field], int)
                            and not isinstance(item[field], bool)
                            and item[field] >= (1 if field in {
                                "line_number", "column_number"} else 0)
                            for field in {
                                "match_ordinal", "line_number", "column_number",
                                "match_start_chars", "excerpt_start_chars"})
                    and isinstance(item["excerpt"], str)
                    for item in matches)
                and [item["match_ordinal"] for item in matches]
                == list(range(offset, offset + len(matches)))
                and (next_offset is None
                     or isinstance(next_offset, int)
                     and not isinstance(next_offset, bool)
                     and next_offset == offset + len(matches)
                     and next_offset < total)
                and ((next_offset is None
                      and offset + len(matches) >= total)
                     or next_offset is not None)
                and resource_ref(value["resource_ref"])
                and exact_ref(
                    value["use_receipt_ref"], "resource_delivery/v1"))
        elif self.tool_name == "request_resource":
            valid = (
                    exact_ref(value["transition_firing_ref"], "transition_firing/v1")
                    and isinstance(value["transition_id"], str)
                    and bool(value["transition_id"])
                    and exact_ref(value["invocation_ref"], "invocation/v1")
                    and exact_ref(value["operation_execution_lease_ref"], "operation_execution_lease/v1")
                    and exact_ref(value["operation_binding_ref"], "operation_binding/v1")
                    and exact_ref(value["agent_loop_ref"], "agent_loop/v1")
                    and exact_ref(value["agent_turn_ref"], "agent_turn/v1")
                    and exact_ref(value["agent_action_ref"], "agent_action/v2")
                    and exact_ref(
                        value["resource_use_occurrence_ref"],
                        "resource_access_request/v1")
                    and exact_ref(
                        value["resource_access_lifecycle_ref"],
                        "resource_access_lifecycle/v1")
                    and (value["resource_access_grant_ref"] is None
                         or exact_ref(
                             value["resource_access_grant_ref"],
                             "resource_access_grant/v1"))
                    and (value["resource_access_lease_ref"] is None
                         or exact_ref(
                             value["resource_access_lease_ref"],
                             "resource_access_lease/v1"))
                    and isinstance(value["logical_resource_id"], str)
                    and value["logical_resource_id"].startswith("resource:")
                    and exact_ref(
                        value["lock_resource_ref"], "resource_version/v1")
                    and resource_ref(value["resource_ref"])
                    and value["logical_resource_id"]
                    == value["resource_ref"]["resource_id"]
                    and value["lock_resource_ref"] == {
                        "entity_type": "resource_version/v1",
                        "logical_id": value["resource_ref"]["resource_id"],
                        "version_id": value["resource_ref"][
                            "resource_version_id"],
                    }
                    and value["access_mode"] in {"read", "edit"}
                    and exact_ref(
                        value["access_checkpoint_ref"],
                        "marking_checkpoint/v1")
                    and exact_ref(
                        value["access_net_ref"], "net_instance/v1")
                    and isinstance(value["access_claim_epoch"], int)
                    and not isinstance(value["access_claim_epoch"], bool)
                    and value["access_claim_epoch"] >= 0
                    and exact_ref(
                        value["resource_token_ref"], "petri_token/v1")
                    and isinstance(value["lease_pool_place"], str)
                    and bool(value["lease_pool_place"])
                    and exact_ref(value["lease_identity_ref"])
                    and value["petri_input_arc_mode"] in {"read", "borrow"}
                    and value["petri_output_arc_mode"] in {None, "return"}
                    and value["petri_arc_kind"]
                    == value["petri_input_arc_mode"]
                    and isinstance(value["return_arc_required"], bool)
                    and value["return_arc_required"]
                    == (value["petri_output_arc_mode"] == "return")
                    and isinstance(value["llm_turns_used"], int)
                    and not isinstance(value["llm_turns_used"], bool)
                    and value["llm_turns_used"] >= 1
                    and isinstance(value["writer_fencing_epoch"], int)
                    and not isinstance(value["writer_fencing_epoch"], bool)
                    and value["writer_fencing_epoch"] >= 1
                    and value["same_firing_continuation"] is True)
            valid = (
                valid
                and isinstance(value["available_on_next_turn"], str)
                and bool(value["available_on_next_turn"]))
        elif self.tool_name == "delegate_leaf":
            local_sequence = value["local_sequence"]
            valid = (
                resource_ref(value["result_resource_ref"])
                and isinstance(value["child_session_id"], str)
                and bool(value["child_session_id"])
                and isinstance(local_sequence, int)
                and not isinstance(local_sequence, bool)
                and local_sequence >= 0
                and typed_ref(
                    value["llm_invocation_ref"],
                    "llm_invocation_spec/v1", "llm_invocation",
                    "llm_invocation_version")
                and typed_ref(
                    value["llm_invocation_attempt_ref"],
                    "llm_invocation_attempt/v1", "llm_invocation_attempt",
                    "llm_invocation_attempt_version"))
        elif self.tool_name == "workspace":
            valid = (
                value["status"] in {"completed", "timed_out"}
                and isinstance(value["exit_code"], int)
                and not isinstance(value["exit_code"], bool)
                and isinstance(value["stdout"], str)
                and isinstance(value["stderr"], str)
                and isinstance(value["output_truncated"], bool)
                and value["command_started"] is True)
        elif self.tool_name == "complete_interaction":
            refs = value["written_resource_refs"]
            valid = (value["completed"] is True
                     and isinstance(refs, list) and bool(refs)
                     and all(resource_ref(item) for item in refs)
                     and len({json.dumps(item, sort_keys=True)
                              for item in refs}) == len(refs))
        if not valid:
            raise AgentLoopProtocolError(
                "agent action result metadata values are invalid")


@dataclass(frozen=True, slots=True)
class AgentLoopCompletion:
    """Current-lane completion carrying registered file output references."""

    loop: AgentLoopSnapshot
    written_resource_refs: tuple[ResourceVersionRef, ...]
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.COMPLETED):
            raise AgentLoopProtocolError(
                "file-agent completion requires a completed v1 loop")
        refs = tuple(self.written_resource_refs)
        if (not refs
                or any(not isinstance(ref, ResourceVersionRef) for ref in refs)
                or len(set(refs)) != len(refs)):
            raise AgentLoopProtocolError(
                "file-agent completion requires unique registered writes")
        if refs != self.loop.written_resource_refs:
            raise AgentLoopProtocolError(
                "file-agent completion differs from its Registry write closure")
        object.__setattr__(self, "written_resource_refs", refs)


@dataclass(frozen=True, slots=True)
class AgentCapReviewCompletion:
    """Actor cap completion carrying only its ordinary-critic handoff bundle."""

    loop: AgentLoopSnapshot
    cap_review_resource_ref: ResourceVersionRef
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.COMPLETED
                or self.loop.written_resource_refs
                or not isinstance(
                    self.cap_review_resource_ref, ResourceVersionRef)
                or self.loop.cap_review_resource_ref
                != self.cap_review_resource_ref):
            raise AgentLoopProtocolError(
                "cap-review completion differs from its Registry closure")


@dataclass(frozen=True, slots=True)
class AgentTaskModelCallCapHandoffCompletion:
    """Mechanical agent closure at the ordinary task model-call cap."""

    loop: AgentLoopSnapshot
    agent_loop_role: str
    transition_firing_ref: VersionRef
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.COMPLETED
                or self.loop.written_resource_refs
                or self.loop.cap_review_resource_ref is not None
                or self.agent_loop_role not in {
                    "actor", "critic", "finalization_reviewer"}
                or not isinstance(self.transition_firing_ref, VersionRef)
                or self.transition_firing_ref.entity_type
                != "transition_firing/v1"):
            raise AgentLoopProtocolError(
                "task-cap handoff completion requires one exact agent "
                "loop/firing closure without semantic output")


@dataclass(frozen=True, slots=True)
class AgentLoopResourceWait:
    """Typed same-firing wait boundary returned to Petri scheduling."""

    loop: AgentLoopSnapshot
    turn: AgentTurnRecord
    action: AgentActionRecord
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.WAITING_RESOURCE
                or not isinstance(self.turn, AgentTurnRecord)
                or not isinstance(self.action, AgentActionRecord)
                or self.turn.loop_id != self.loop.loop_id
                or self.turn.sequence + 1 != self.loop.next_turn_sequence
                or self.action.loop_id != self.loop.loop_id
                or self.action.turn_sequence != self.turn.sequence
                or self.action.tool_name != "request_resource"
                or self.action.state != AgentLoopState.WAITING_RESOURCE):
            raise AgentLoopProtocolError(
                "resource wait requires one exact committed loop/turn/action")


@dataclass(frozen=True, slots=True)
class AgentLoopInterruptionCheckpoint:
    """A settled action boundary ready for owner-stop checkpointing."""

    loop: AgentLoopSnapshot
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state != AgentLoopState.WAITING_FOR_LLM):
            raise AgentLoopProtocolError(
                "owner interruption requires one settled waiting loop head")


@dataclass(frozen=True, slots=True)
class AgentLoopExecutionBlock:
    """Process-local stop for one incomplete provisional firing."""

    loop: AgentLoopSnapshot
    block_authority: OperationExecutionBlockAuthority = field(repr=False)
    bottom_error: BaseException | None = field(
        default=None, compare=False, repr=False)
    timing_observation: AgentOperationTimingEvidence | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        block_kind = getattr(self.block_authority, "block_kind", None)
        execution = getattr(self.block_authority, "execution", None)
        if (not isinstance(self.loop, AgentLoopSnapshot)
                or self.loop.state in TERMINAL_STATES
                or not isinstance(
                    self.block_authority, OperationExecutionBlockAuthority)
                or block_kind not in {
                    "llm_repair",
                    "framework_repair", "submission_reconciliation",
                }
                or execution is None
                or not isinstance(
                    getattr(self.block_authority, "exact_error_ref", None),
                    VersionRef)
                or getattr(
                    self.block_authority, "retry_not_before_utc", None)
                is not None
                or (self.bottom_error is not None
                    and not isinstance(self.bottom_error, BaseException))):
            raise AgentLoopProtocolError(
                "execution block lacks exact provisional-firing authority")


@dataclass(frozen=True, slots=True)
class VerifiedAgentLoopResourceGrant:
    """P3 adapter around one P0-verified grant and its stored wait context.

    ``registry_authority`` is accepted only after AgentLoopService verifies the
    nominal P0 ``AgentLoopResourceGrantAuthority`` type.  These duplicated exact
    identities are comparison inputs, never an alternate grant authority.
    """

    registry_authority: object = field(repr=False, compare=False)
    transition_firing_ref: VersionRef
    invocation_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    operation_binding_ref: VersionRef
    queue_entry_id: str
    resource_ref: ResourceVersionRef
    writer_fencing_epoch: int
    agent_loop_ref: VersionRef
    agent_turn_ref: VersionRef
    agent_action_ref: VersionRef
    waiting_loop: AgentLoopSnapshot
    waiting_turn: AgentTurnRecord
    waiting_action: AgentActionRecord

    def __post_init__(self) -> None:
        typed_refs = (
            (self.transition_firing_ref, "transition_firing/v1"),
            (self.invocation_ref, "invocation/v1"),
            (self.operation_execution_lease_ref,
             "operation_execution_lease/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.agent_loop_ref, "agent_loop/v1"),
            (self.agent_turn_ref, "agent_turn/v1"),
            (self.agent_action_ref, "agent_action/v2"),
        )
        if (self.registry_authority is None
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != expected
                       for ref, expected in typed_refs)
                or not isinstance(self.queue_entry_id, str)
                or not self.queue_entry_id.strip()
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or isinstance(self.writer_fencing_epoch, bool)
                or not isinstance(self.writer_fencing_epoch, int)
                or self.writer_fencing_epoch < 1):
            raise AgentLoopProtocolError(
                "resource grant requires complete exact typed identities")
        wait = AgentLoopResourceWait(
            self.waiting_loop, self.waiting_turn, self.waiting_action)
        metadata = wait.action.result_metadata
        if not isinstance(metadata, Mapping):
            raise AgentLoopProtocolError(
                "resource grant wait action lacks registered metadata")

        def exact_ref(ref: VersionRef) -> dict[str, str]:
            return {
                "entity_type": ref.entity_type,
                "logical_id": str(ref.entity_id),
                "version_id": str(ref.version_id),
            }

        exact_resource = {
            "resource_id": str(self.resource_ref.resource_id),
            "resource_version_id": str(
                self.resource_ref.resource_version_id),
        }
        expected_metadata = {
            "transition_firing_ref": exact_ref(self.transition_firing_ref),
            "invocation_ref": exact_ref(self.invocation_ref),
            "operation_execution_lease_ref": exact_ref(
                self.operation_execution_lease_ref),
            "operation_binding_ref": exact_ref(self.operation_binding_ref),
            "agent_loop_ref": exact_ref(self.agent_loop_ref),
            "agent_turn_ref": exact_ref(self.agent_turn_ref),
            "agent_action_ref": exact_ref(self.agent_action_ref),
            "resource_ref": exact_resource,
            "writer_fencing_epoch": self.writer_fencing_epoch,
        }
        if (any(metadata.get(name) != value
                for name, value in expected_metadata.items())
                or self.waiting_loop.invocation_ref != self.invocation_ref
                or self.waiting_loop.operation_binding_ref
                != self.operation_binding_ref
                or str(self.agent_loop_ref.entity_id)
                != self.waiting_loop.loop_id
                or str(self.agent_loop_ref.version_id)
                != self.waiting_loop.loop_version_id
                or str(self.agent_turn_ref.entity_id)
                != self.waiting_turn.turn_id
                or str(self.agent_action_ref.entity_id)
                != self.waiting_action.action_id
                or metadata.get("llm_turns_used")
                != self.waiting_loop.llm_turns_used
                or metadata.get("same_firing_continuation") is not True):
            raise AgentLoopProtocolError(
                "resource grant differs from its exact stored wait context")


@dataclass(frozen=True, slots=True)
class AgentContextCompaction:
    compaction_id: str
    loop_id: str
    first_turn_sequence: int
    last_turn_sequence: int
    covered_turn_refs: tuple[VersionRef, ...]
    summary_resource_ref: ResourceVersionRef
    summary_size: int
    authority_ref: VersionRef

    def __post_init__(self) -> None:
        _require_id("agent_context_compaction", self.compaction_id)
        _require_id("agent_loop", self.loop_id)
        if (self.first_turn_sequence < 0
                or self.last_turn_sequence < self.first_turn_sequence
                or len(self.covered_turn_refs)
                != self.last_turn_sequence - self.first_turn_sequence + 1):
            raise AgentLoopProtocolError(
                "context compaction does not cover one contiguous lineage")
        if any(not isinstance(ref, VersionRef) for ref in self.covered_turn_refs):
            raise TypeError("context compaction requires exact turn refs")
        if not isinstance(self.summary_resource_ref, ResourceVersionRef):
            raise TypeError("context compaction summary must be a resource")
        if (isinstance(self.summary_size, bool)
                or not isinstance(self.summary_size, int)
                or self.summary_size < 0):
            raise ValueError("context compaction summary size is invalid")
        if not isinstance(self.authority_ref, VersionRef):
            raise TypeError("context compaction requires exact authority")


@dataclass(frozen=True, slots=True)
class AgentContextOverlay:
    """Completed current-v3 effective-history overlay for fresh execution."""

    compaction_ref: VersionRef
    loop_id: str
    source_context_session_ordinal: int
    context_session_ordinal: int
    trigger_reason: str
    first_turn_sequence: int
    last_turn_sequence: int
    covered_turn_refs: tuple[VersionRef, ...]
    replacement_history: tuple[Mapping[str, Any], ...]
    llm_invocation_ref: VersionRef
    llm_invocation_attempt_ref: VersionRef

    def __post_init__(self) -> None:
        if (not isinstance(self.compaction_ref, VersionRef)
                or self.compaction_ref.entity_type
                != "agent_context_compaction/v3"):
            raise AgentLoopProtocolError(
                "context overlay requires one exact current-v3 ref")
        _require_id(
            "agent_context_compaction", str(self.compaction_ref.entity_id))
        _require_id(
            "agent_context_compaction_version",
            str(self.compaction_ref.version_id))
        _require_id("agent_loop", self.loop_id)
        if (isinstance(self.source_context_session_ordinal, bool)
                or not isinstance(self.source_context_session_ordinal, int)
                or self.source_context_session_ordinal < 0
                or isinstance(self.context_session_ordinal, bool)
                or not isinstance(self.context_session_ordinal, int)
                or self.context_session_ordinal
                != self.source_context_session_ordinal + 1):
            raise AgentLoopProtocolError(
                "context overlay session boundary is invalid")
        if self.trigger_reason not in {
                "context_pressure", "response_length", "turn_cap"}:
            raise AgentLoopProtocolError(
                "context overlay trigger reason is invalid")
        if (isinstance(self.first_turn_sequence, bool)
                or not isinstance(self.first_turn_sequence, int)
                or isinstance(self.last_turn_sequence, bool)
                or not isinstance(self.last_turn_sequence, int)
                or self.first_turn_sequence != 0
                or self.last_turn_sequence < -1):
            raise AgentLoopProtocolError(
                "context overlay turn coverage is invalid")
        covered = tuple(self.covered_turn_refs)
        if (len(covered) != self.last_turn_sequence + 1
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "agent_turn/v1"
                       for ref in covered)):
            raise AgentLoopProtocolError(
                "context overlay lacks one contiguous exact turn prefix")
        from .compact import copy_replacement_history

        replacement = copy_replacement_history(self.replacement_history)
        if (replacement[0].get("source_context_session_ordinal")
                != self.source_context_session_ordinal
                or replacement[0].get("context_session_ordinal")
                != self.context_session_ordinal):
            raise AgentLoopProtocolError(
                "context overlay capsule crosses its session boundary")
        if (not isinstance(self.llm_invocation_ref, VersionRef)
                or self.llm_invocation_ref.entity_type
                != "llm_invocation_spec/v1"
                or not isinstance(self.llm_invocation_attempt_ref, VersionRef)
                or self.llm_invocation_attempt_ref.entity_type
                != "llm_invocation_attempt/v1"):
            raise AgentLoopProtocolError(
                "context overlay requires exact LLM invocation refs")
        object.__setattr__(self, "covered_turn_refs", covered)
        object.__setattr__(self, "replacement_history", replacement)

    @property
    def compaction_id(self) -> str:
        return str(self.compaction_ref.entity_id)

    @property
    def summary_content(self) -> str:
        """Return the final typed compaction-summary entry."""

        return str(self.replacement_history[1]["content"])

    @property
    def fact_capsule(self) -> Mapping[str, Any]:
        """Return the registered framework-owned fact capsule."""

        from .compact import copy_replacement_history

        return copy_replacement_history(self.replacement_history)[0]

    @property
    def model_visible_messages(self) -> tuple[Mapping[str, Any], ...]:
        """Project the one stored replacement history into chat messages."""

        return tuple(
            dict(entry["message"])
            if entry["kind"] == "retained_model_visible_message"
            else {"role": "user", "content": json.dumps(
                entry, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"))}
            if entry["kind"] == "agent_context_fact_capsule"
            else {"role": "user", "content": entry["content"]}
            for entry in self.replacement_history)


def _require_id(kind: str, value: object) -> None:
    prefix = f"{kind}:"
    if (not isinstance(value, str) or not value.startswith(prefix)
            or len(value) != len(prefix) + 32
            or any(character not in "0123456789abcdef"
                   for character in value[len(prefix):])):
        raise ValueError(f"expected an exact {kind} id")


__all__ = [
    "A2C_CRITIC_VERDICTS",
    "AgentActionRecord",
    "AgentActionTimingEvidence",
    "AgentCapReviewCompletion",
    "AgentContextCompaction",
    "AgentContextOverlay",
    "AgentOperationTimingEvidence",
    "AgentLoopLLMBudgetExhausted",
    "AgentLoopLocalTurnLimitExhausted",
    "AgentLoopProtocolError",
    "AgentLoopCompletion",
    "AgentLoopExecutionBlock",
    "AgentLoopInterruptionCheckpoint",
    "AgentLoopResourceWait",
    "VerifiedAgentLoopResourceGrant",
    "AgentLoopSnapshot",
    "AgentLoopState",
    "AgentSystemInitialization",
    "AgentTaskModelCallCapHandoffCompletion",
    "AgentToolCallFact",
    "AgentTurnRecord",
    "AgentTurnTimingEvidence",
    "LocatedAgentInput",
    "TERMINAL_STATES",
    "critic_verdict_only_correction",
    "require_exact_critic_verdict",
    "require_state_transition",
    "record_timing_offset",
    "stable_action_id",
    "stable_malformed_action_id",
    "stable_loop_id",
]
