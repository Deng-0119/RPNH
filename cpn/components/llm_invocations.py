"""Optional LLM component DTOs over exact Registry invocation authorities."""

from __future__ import annotations

from dataclasses import dataclass

from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef


@dataclass(frozen=True, slots=True)
class RegisteredLLMInvocation:
    ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    agent_loop_ref: VersionRef
    subject_agent_ref: VersionRef
    execution_agent_ref: VersionRef
    invocation_kind: str
    turn_sequence: int
    request_resource_ref: ResourceVersionRef
    semantic_prompt_resource_ref: ResourceVersionRef
    llm_input_target_ref: ResourceVersionRef
    model_condition: str
    tool_catalog_ref: ResourceVersionRef
    prior_turn_refs: tuple[VersionRef, ...]
    max_response_bytes: int
    replay_parent_invocation_ref: VersionRef | None = None
    replay_after_compaction_ref: VersionRef | None = None
    replay_interrupted_response_ref: ResourceVersionRef | None = None
    child_session_id: str | None = None
    local_sequence: int | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.ref, VersionRef)
                or self.ref.entity_type != "llm_invocation_spec/v1"
                or not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "invocation/v1"
                or not isinstance(self.operation_binding_ref, VersionRef)
                or not isinstance(self.agent_loop_ref, VersionRef)
                or self.agent_loop_ref.entity_type != "agent_loop/v1"
                or not isinstance(self.subject_agent_ref, VersionRef)
                or self.subject_agent_ref.entity_type != "agent/v1"
                or not isinstance(self.execution_agent_ref, VersionRef)
                or self.execution_agent_ref.entity_type != "agent/v1"
                or self.invocation_kind not in {
                    "normal_turn", "context_compaction",
                    "delegated_subtask"}
                or isinstance(self.turn_sequence, bool)
                or not isinstance(self.turn_sequence, int)
                or self.turn_sequence < 0
                or not isinstance(self.request_resource_ref, ResourceVersionRef)
                or not isinstance(
                    self.semantic_prompt_resource_ref, ResourceVersionRef)
                or not isinstance(
                    self.llm_input_target_ref, ResourceVersionRef)
                or not isinstance(self.tool_catalog_ref, ResourceVersionRef)
                or not isinstance(self.model_condition, str)
                or not self.model_condition
                or self.model_condition != self.model_condition.strip()
                or isinstance(self.max_response_bytes, bool)
                or not isinstance(self.max_response_bytes, int)
                or self.max_response_bytes < 1):
            raise TypeError("registered LLM invocation authority is invalid")
        replay = (
            self.replay_parent_invocation_ref,
            self.replay_after_compaction_ref,
            self.replay_interrupted_response_ref,
        )
        if any(value is not None for value in replay):
            if (not isinstance(replay[0], VersionRef)
                    or replay[0].entity_type != "llm_invocation_spec/v1"
                    or not isinstance(replay[1], VersionRef)
                    or replay[1].entity_type
                    != "agent_context_compaction/v3"
                    or not isinstance(replay[2], ResourceVersionRef)
                    or self.invocation_kind != "normal_turn"):
                raise TypeError("LLM replay lineage is incomplete")
        prior = tuple(self.prior_turn_refs)
        delegated = self.invocation_kind == "delegated_subtask"
        annotations = (self.child_session_id, self.local_sequence)
        if delegated:
            if (not isinstance(self.child_session_id, str)
                    or not self.child_session_id
                    or isinstance(self.local_sequence, bool)
                    or not isinstance(self.local_sequence, int)
                    or self.local_sequence < 0
                    or self.turn_sequence != self.local_sequence
                    or len(prior) != self.local_sequence
                    or any(not isinstance(ref, VersionRef)
                           or ref.entity_type != "llm_invocation_spec/v1"
                           for ref in prior)):
                raise TypeError(
                    "delegated LLM invocation authority is invalid")
        elif (any(value is not None for value in annotations)
              or len(prior) != self.turn_sequence
              or any(not isinstance(ref, VersionRef)
                     or ref.entity_type != "agent_turn/v1"
                     for ref in prior)):
            raise TypeError("LLM invocation prior-turn closure is invalid")
        object.__setattr__(self, "prior_turn_refs", prior)

    @property
    def loop_ref(self) -> dict[str, str]:
        return {
            "entity_type": self.agent_loop_ref.entity_type,
            "logical_id": str(self.agent_loop_ref.entity_id),
            "version_id": str(self.agent_loop_ref.version_id),
        }

@dataclass(frozen=True, slots=True)
class RegisteredLLMInvocationAttempt:
    ref: VersionRef
    invocation: RegisteredLLMInvocation
    attempt_ordinal: int
    prior_attempt_ref: VersionRef | None
    reservation_class: str
    finalization_scope: str | None

    def __post_init__(self) -> None:
        if (not isinstance(self.ref, VersionRef)
                or self.ref.entity_type != "llm_invocation_attempt/v1"
                or not isinstance(self.invocation, RegisteredLLMInvocation)
                or isinstance(self.attempt_ordinal, bool)
                or not isinstance(self.attempt_ordinal, int)
                or self.attempt_ordinal < 0
                or not isinstance(self.reservation_class, str)
                or not self.reservation_class
                or (self.finalization_scope is not None
                    and not isinstance(self.finalization_scope, str))):
            raise TypeError("registered LLM invocation attempt is invalid")
        if self.attempt_ordinal == 0:
            if self.prior_attempt_ref is not None:
                raise TypeError("first LLM attempt cannot name a predecessor")
        elif (not isinstance(self.prior_attempt_ref, VersionRef)
              or self.prior_attempt_ref.entity_type
              != "llm_invocation_attempt/v1"):
            raise TypeError("later LLM attempt requires its predecessor")


__all__ = [
    "RegisteredLLMInvocation",
    "RegisteredLLMInvocationAttempt",
]
