"""Typed identities for D1-C objects, versions, events, and transactions."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

IdKind = Literal[
    "run", "run_version", "native_genesis", "native_genesis_version",
    "native_resume", "native_resume_version",
    "bootstrap_command", "bootstrap_command_version",
    "run_root_anchor", "run_root_anchor_version",
    "dependency_entry", "dependency_entry_version",
    "dependency_partition_index", "dependency_partition_index_version",
    "dependency_root_index", "dependency_root_index_version",
    "dependency_snapshot", "dependency_snapshot_version",
    "run_dependency_binding", "run_dependency_binding_version",
    "task", "task_version", "task_branch", "task_branch_version",
    "task_round", "task_round_version", "net_instance", "net_instance_version",
    "plan", "plan_version", "node", "node_declaration_version",
    "team_design_root", "team_design_root_version",
    "logical_slot", "logical_slot_version",
    "transition_firing", "transition_firing_version",
    "firing_admission", "firing_admission_version",
    "a2c_activation", "a2c_activation_version",
    "critic_evidence", "critic_evidence_version",
    "operation_binding", "operation_binding_version",
    "operation_spec", "operation_spec_version",
    "principal", "principal_version", "agent", "agent_version",
    "invocation", "invocation_version",
    "generic_critic_invocation", "generic_critic_invocation_version",
    "operation_execution_lease", "operation_execution_lease_version",
    "operation_result", "operation_result_version",
    "marking_delta", "marking_delta_version",
    "marking_checkpoint", "marking_checkpoint_version",
    "checkpoint_repair", "checkpoint_repair_version",
    "executable_transition_binding", "executable_transition_binding_version",
    "petri_token", "petri_token_version",
    "output_binding", "output_binding_version",
    "write_intent", "write_intent_version",
    "workspace_binding", "workspace_binding_version",
    "workspace_lineage", "workspace_revision",
    "resource", "resource_version", "resource_address_binding",
    "resource_address_binding_version", "resource_delivery",
    "resource_delivery_version", "release_witness", "release_witness_version",
    "release_nonce", "boundary_receipt", "boundary_receipt_version",
    "observer_profile", "observer_profile_version", "observer_grant",
    "observer_grant_version", "grant", "opinion", "transaction",
    "transaction_version", "event",
    "relation", "schema", "snapshot", "fact_event", "fact_event_version",
    "llm_call", "llm_call_version", "provider_attempt",
    "provider_attempt_version", "llm_invocation",
    "llm_invocation_version", "llm_invocation_attempt",
    "llm_invocation_attempt_version",
    "registered_host_llm_attempt", "registered_host_llm_attempt_version",
    "agent_loop", "agent_loop_version", "agent_turn", "agent_turn_version",
    "agent_action", "agent_action_version", "agent_tool_error",
    "agent_tool_error_version", "agent_tool_readiness",
    "agent_tool_readiness_version", "delegated_leaf_session",
    "agent_context_compaction",
    "agent_continuation", "agent_continuation_version",
    "node_generation", "node_generation_version",
    "firing_external_kb_read", "firing_external_kb_read_version",
    "provider_request_placeholder",
    "provider_payload_materialization_receipt",
    "provider_payload_materialization_receipt_version",
    "agent_context_compaction_version", "agent_candidate",
    "agent_candidate_version",
    "provider_submission_unknown", "provider_submission_unknown_version",
    "user_authority_decision", "user_authority_decision_version",
    "fault_disposition_policy", "fault_disposition_policy_version",
    "operation_fault", "operation_fault_version", "fault_chain",
    "fault_retry_admission", "fault_retry_admission_version",
    "fault_subnet_template", "fault_subnet_template_version",
    "fault_petri_place", "fault_petri_place_version",
    "fault_petri_transition", "fault_petri_transition_version",
    "fault_route_binding", "fault_route_binding_version",
    "fault_mechanical_settlement", "fault_mechanical_settlement_version",
    "fault_terminal_witness", "fault_terminal_witness_version",
    "resource_place", "resource_place_version",
    "semantic_prompt_authority", "semantic_prompt_authority_version",
    "semantic_prompt_role", "semantic_prompt_role_version",
    "execution_environment", "execution_environment_version",
    "numerical_tool_profile", "numerical_tool_profile_version",
    "external_kb_package", "external_kb_package_version",
    "external_resource_index", "external_resource_index_version",
    "registered_run_inputs", "registered_run_inputs_version",
    "firing_resource_use", "firing_resource_use_version",
    "firing_resource_production", "firing_resource_production_version",
    "route_deposit", "route_deposit_version",
    "run_execution_authority", "run_execution_authority_version",
    "run_reopen_authorization", "run_reopen_authorization_version",
    "terminal_evidence", "terminal_evidence_version",
    "firing_completion", "firing_completion_version",
    "runtime_timed_scheduler_snapshot",
    "runtime_timed_scheduler_snapshot_version",
    "published_model_call_baseline",
    "published_model_call_baseline_version",
    "execution_net_definition", "execution_net_definition_version",
    "execution_instance", "execution_instance_version",
    "execution_token", "execution_token_version",
    "execution_transition_firing", "execution_transition_firing_version",
    "execution_checkpoint", "execution_checkpoint_version",
    "workspace_revision_candidate", "workspace_revision_candidate_version",
    "execution_terminal_mapping", "execution_terminal_mapping_version",
]
_KINDS = frozenset(IdKind.__args__)
_VALUE = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True, order=True)
class TypedId:
    kind: IdKind
    value: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown typed-id kind: {self.kind!r}")
        if not isinstance(self.value, str) or not _VALUE.fullmatch(self.value):
            raise ValueError("typed-id value must be an opaque 128-bit lowercase hex value")

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"

    @classmethod
    def parse(cls, value: str, *, expected: IdKind | None = None) -> "TypedId":
        try:
            kind, opaque = value.split(":", 1)
        except ValueError as exc:
            raise ValueError(f"invalid typed id: {value!r}") from exc
        result = cls(kind=kind, value=opaque)  # type: ignore[arg-type]
        if expected is not None and result.kind != expected:
            raise TypeError(f"expected {expected!r} id, received {result.kind!r}")
        return result


def new_id(kind: IdKind) -> TypedId:
    return TypedId(kind=kind, value=uuid.uuid4().hex)


_EXECUTION_ID_KINDS = frozenset({
    "execution_net_definition", "execution_net_definition_version",
    "execution_instance", "execution_instance_version",
    "execution_token", "execution_token_version",
    "execution_transition_firing", "execution_transition_firing_version",
    "execution_checkpoint", "execution_checkpoint_version",
})


def stable_execution_id(kind: IdKind, *scope: str | TypedId) -> TypedId:
    """Return one deterministic identity inside an execution-instance scope."""

    if kind not in _EXECUTION_ID_KINDS:
        raise ValueError("stable execution identity requires an execution kind")
    if (not scope or any(
            not isinstance(value, (str, TypedId)) or not str(value)
            for value in scope)):
        raise ValueError("stable execution identity requires a nonempty scope")
    material = ":".join(str(value) for value in scope)
    return TypedId(
        kind=kind,
        value=uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"d1-c:execution:{kind}:{material}",
        ).hex,
    )


def fresh_bootstrap_resource_id(
        task_id: TypedId, bootstrap_version_id: TypedId,
        information_item_key: str,
) -> TypedId:
    """Canonical logical ID for one fresh bootstrap information item."""

    if (not isinstance(task_id, TypedId) or task_id.kind != "task"
            or not isinstance(bootstrap_version_id, TypedId)
            or bootstrap_version_id.kind != "bootstrap_command_version"
            or not isinstance(information_item_key, str)
            or not information_item_key):
        raise TypeError("bootstrap information identity is not canonical")
    material = ":".join(str(part) for part in (
        task_id, "private_system", bootstrap_version_id,
        information_item_key))
    return TypedId(
        "resource",
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"d1-c:resource:{material}").hex,
    )
