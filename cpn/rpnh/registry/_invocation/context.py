"""Context implementation for the InvocationLifecycle facade."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, Mapping, Sequence

from ...budgets import BudgetContractError, validate_budget_binding, validate_budget_scopes
from ..event_store import RegistryConflict, RegistryCorruptError, StaleWriterError, validate_registered_net_closure, verified_adoption_head, verified_checkpoint_head
from ..identities import TypedId
from ..models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
from ..admission_publication import PreparedFiringAdmissionPublications
from ..invocations import (
    FiringAdmission,
    FiringClaim,
    InvocationAdmissionError,
    InvocationClosedError,
    InvocationContext,
    TerminalResultPackage,
    ToolExecutionContext,
    _AgentTurnAcceptanceClosure,
    _HISTORICAL_MECHANICAL_TERMINAL_READY,
    _HistoricalMechanicalTerminalReadyRequest,
    _ref_from_payload,
    _ref_payload,
    _scope_firing_idempotency_key,
    _stable_id,
    terminal_descendant_event_ids,
)

def _scoped_firing_idempotency_key(lifecycle, caller_key: str) -> str:
    raw = lifecycle.service.event_store.get_meta("native_run_ref")
    try:
        native_run_ref = _ref_from_payload(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
        raise InvocationAdmissionError(
            "firing identity lacks its exact native run authority") from exc
    return _scope_firing_idempotency_key(native_run_ref, caller_key)

def _require_ref(lifecycle, ref: VersionRef, entity_type: str,
                 version_kind: str) -> Mapping[str, Any]:
    if not isinstance(ref, VersionRef) or ref.entity_type != entity_type:
        raise InvocationAdmissionError(
            f"required exact {entity_type} reference is missing")
    if ref.version_id.kind != version_kind:
        raise InvocationAdmissionError(
            f"{entity_type} requires {version_kind}, got {ref.version_id.kind}")
    row = lifecycle.service.event_store.object_row(ref.version_id)
    if (row is None or row["logical_id"] != str(ref.entity_id)
            or row["object_type"] != entity_type):
        raise InvocationAdmissionError(
            f"exact reference is not registered: {ref.version_id}")
    metadata = json.loads(row["metadata_json"])
    # Resource-version metadata describes the resource payload and is not the
    # resource_version object envelope. Its authority is established by the
    # exact object row and registered-byte checks in RegistryService.
    if entity_type != "resource_version/v1":
        lifecycle.service.catalog.validate_instance(
            entity_type, category="object", instance=metadata)
    return metadata

def _require_team_design_root(
        lifecycle, ref: VersionRef,
) -> Mapping[str, Any]:
    """Validate the exact root identity for either workflow design phase.

    The fixed skeleton is rooted in the launch plan, while later
    structural adoption mints an independent workflow-design root. These are
    two closed identity pairs, not aliases and not a tolerant kind check.
    """

    identity_pairs = {
        "plan_version": "plan",
        "team_design_root_version": "team_design_root",
    }
    if (not isinstance(ref, VersionRef)
            or ref.entity_type != "team_design_root/v1"
            or ref.version_id.kind not in identity_pairs
            or ref.entity_id.kind != identity_pairs[ref.version_id.kind]):
        raise InvocationAdmissionError(
            "team_design_root/v1 lacks a closed skeleton/connected identity pair")
    return lifecycle._require_ref(
        ref, "team_design_root/v1", ref.version_id.kind)

def _metadata_ref(metadata: Mapping[str, Any], name: str) -> VersionRef:
    value = metadata.get(name)
    if not isinstance(value, Mapping):
        raise InvocationAdmissionError(f"registered object is missing exact {name}")
    return _ref_from_payload(value)

def _same_ref(left: VersionRef, right: VersionRef) -> bool:
    return left == right

def _require_registered_operation_closure(
        lifecycle, *, net_ref: VersionRef, plan_ref: VersionRef,
        team_design_root_ref: VersionRef, node_ref: VersionRef,
        operation_binding_ref: VersionRef,
        ) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    try:
        closure = validate_registered_net_closure(
            lifecycle.service.event_store, lifecycle.service.catalog, net_ref)
    except RegistryCorruptError as exc:
        raise InvocationAdmissionError(
            "operation authority is outside the registered net closure") from exc
    expected = {
        "plan_ref": _ref_payload(plan_ref),
        "team_design_root_ref": _ref_payload(team_design_root_ref),
    }
    if any(closure.get(field) != value for field, value in expected.items()):
        raise InvocationAdmissionError(
            "operation authority disagrees with the registered net identity")
    node_payload = _ref_payload(node_ref)
    binding_payload = _ref_payload(operation_binding_ref)
    if (node_payload not in closure.get("node_refs", [])
            or binding_payload not in closure.get("operation_binding_refs", [])):
        raise InvocationAdmissionError(
            "node or operation binding is outside the registered net closure")
    node = lifecycle._require_ref(
        node_ref, "node_declaration/v1", "node_declaration_version")
    binding = lifecycle._require_ref(
        operation_binding_ref, "operation_binding/v1",
        "operation_binding_version")
    if (node.get("producer_operation_binding_ref") != binding_payload
            or binding.get("node_ref") != node_payload):
        raise InvocationAdmissionError(
            "node and operation binding do not form one registered closure")
    return closure, node, binding

def _current_budget_ref(lifecycle) -> VersionRef:
    try:
        return lifecycle.service.recovery_manifest_ref()
    except Exception as exc:
        raise InvocationAdmissionError(
            "current budget authority exact ref is unavailable") from exc

def _current_active_net_ref(lifecycle) -> VersionRef:
    try:
        return verified_adoption_head(
            lifecycle.service.event_store, lifecycle.service.catalog,
            lifecycle.service.task_id)
    except RegistryCorruptError as exc:
        raise InvocationAdmissionError(
            "task has no verified exact active-net head") from exc

def _current_marking_ref(lifecycle, net_ref: VersionRef) -> VersionRef:
    try:
        return verified_checkpoint_head(
            lifecycle.service.event_store, lifecycle.service.catalog,
            lifecycle.service.task_id, net_ref)
    except RegistryCorruptError as exc:
        raise InvocationAdmissionError(
            "active net has no verified checkpoint lineage head") from exc

def _marking_token_refs(metadata: Mapping[str, Any]) -> tuple[VersionRef, ...]:
    values = metadata.get("token_refs")
    if not isinstance(values, list):
        raise InvocationAdmissionError(
            "marking checkpoint lacks canonical token_refs")
    try:
        refs = tuple(_ref_from_payload(value) for value in values)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvocationAdmissionError(
            "marking checkpoint contains an invalid exact token ref") from exc
    if len({str(ref.version_id) for ref in refs}) != len(refs):
        raise InvocationAdmissionError("marking checkpoint repeats a token version")
    return refs

def _existing_command_events(lifecycle, idempotency_key: str) -> tuple[EventEnvelope, ...]:
    return lifecycle.service.event_store.list_events_by_idempotency_key(
        idempotency_key)

def hydrate_context(
        lifecycle, invocation_ref: VersionRef, *,
        require_current_writer: bool = True,
) -> InvocationContext:
    metadata = lifecycle._require_ref(invocation_ref, "invocation/v1", "invocation_version")
    required = {
        "task_ref", "task_branch_ref", "task_round_ref", "net_instance_ref",
        "plan_ref", "team_design_root_ref",
        "invocation_ref", "agent_ref",
        "operation_binding_ref",
        "authority_decision_ref",
        "admission_marking_checkpoint_ref", "budget_witness_ref", "principal_ref",
        "operation_execution_lease_ref", "origin",
        "accounting_parent_invocation_ref", "budget_scope",
    }
    missing = sorted(required - set(metadata))
    if missing:
        raise InvocationAdmissionError(f"invocation context is incomplete: {missing}")
    context = InvocationContext(
        task_ref=_ref_from_payload(metadata["task_ref"]),
        task_branch_ref=_ref_from_payload(metadata["task_branch_ref"]),
        task_round_ref=_ref_from_payload(metadata["task_round_ref"]),
        net_instance_ref=_ref_from_payload(metadata["net_instance_ref"]),
        plan_ref=_ref_from_payload(metadata["plan_ref"]),
        team_design_root_ref=_ref_from_payload(
            metadata["team_design_root_ref"]),
        invocation_ref=_ref_from_payload(metadata["invocation_ref"]),
        agent_ref=(
            _ref_from_payload(metadata["agent_ref"])
            if metadata["agent_ref"] is not None else None),
        operation_binding_ref=_ref_from_payload(metadata["operation_binding_ref"]),
        authority_decision_ref=_ref_from_payload(
            metadata["authority_decision_ref"]),
        admission_marking_checkpoint_ref=_ref_from_payload(
            metadata["admission_marking_checkpoint_ref"]),
        budget_witness_ref=_ref_from_payload(metadata["budget_witness_ref"]),
        principal_ref=_ref_from_payload(metadata["principal_ref"]),
        operation_execution_lease_ref=_ref_from_payload(
            metadata["operation_execution_lease_ref"]),
        origin=str(metadata["origin"]),  # type: ignore[arg-type]
        own_node_ref=(_ref_from_payload(metadata["own_node_ref"])
                      if metadata.get("own_node_ref") else None),
        own_transition_firing_ref=(
            _ref_from_payload(metadata["own_transition_firing_ref"])
            if metadata.get("own_transition_firing_ref") else None),
        sponsoring_transition_firing_ref=(
            _ref_from_payload(metadata["sponsoring_transition_firing_ref"])
            if metadata.get("sponsoring_transition_firing_ref") else None),
        activation_ref=(
            _ref_from_payload(metadata["activation_ref"])
            if metadata.get("activation_ref") else None),
        authorization_lifetime_activation_ref=(
            _ref_from_payload(metadata["authorization_lifetime_activation_ref"])
            if metadata.get("authorization_lifetime_activation_ref") else None),
        parent_invocation_ref=(
            _ref_from_payload(metadata["parent_invocation_ref"])
            if metadata.get("parent_invocation_ref") else None),
        accounting_parent_invocation_ref=_ref_from_payload(
            metadata["accounting_parent_invocation_ref"]),
        budget_scope=str(metadata["budget_scope"]),
        finalization_scope=(str(metadata["finalization_scope"])
                            if metadata.get("finalization_scope") is not None else None),
    )
    if context.invocation_ref != invocation_ref:
        raise InvocationAdmissionError("invocation object does not name its exact version")
    lifecycle._validate_context_refs(
        context, require_current_writer=require_current_writer)
    return context

def hydrate_serialized_context(
        lifecycle, value: Mapping[str, Any], *, boundary: str) -> InvocationContext:
    """Rehydrate a process DTO from D1-C and reject any stale/tampered copy."""
    carried = InvocationContext.from_serialized(value)
    canonical = lifecycle.hydrate_context(carried.invocation_ref)
    if canonical != carried:
        raise InvocationAdmissionError(
            "serialized InvocationContext differs from the canonical registry object")
    lifecycle.revalidate_io(canonical, boundary=boundary)
    return canonical

def _validate_context_refs(
        lifecycle, context: InvocationContext, *,
        require_current_writer: bool = True,
) -> None:
    context_metadata_by_ref: dict[
        VersionRef, Mapping[str, Any]] = {}
    for ref, entity_type, version_kind in (
            (context.task_ref, "task/v1", "task_version"),
            (context.task_branch_ref, "task_branch/v1", "task_branch_version"),
            (context.task_round_ref, "task_round/v1", "task_round_version"),
            (context.net_instance_ref, "net_instance/v1", "net_instance_version"),
            (context.plan_ref, "plan_version/v1", "plan_version"),
            (context.operation_binding_ref, "operation_binding/v1",
             "operation_binding_version"),
            (context.authority_decision_ref, "user_authority_decision/v1",
             "user_authority_decision_version"),
            (context.admission_marking_checkpoint_ref,
             "marking_checkpoint/v1", "marking_checkpoint_version"),
            (context.budget_witness_ref,
             "task_recovery_manifest/v1", "resource_version"),
            (context.principal_ref, "principal/v1", "principal_version"),
            (context.operation_execution_lease_ref,
             "operation_execution_lease/v1", "operation_execution_lease_version")):
        context_metadata_by_ref[ref] = lifecycle._require_ref(
            ref, entity_type, version_kind)
    lifecycle._require_team_design_root(context.team_design_root_ref)
    if context.agent_ref is not None:
        lifecycle._require_ref(context.agent_ref, "agent/v1", "agent_version")
    lease = context_metadata_by_ref[
        context.operation_execution_lease_ref]
    if lifecycle._metadata_ref(lease, "invocation_ref") != context.invocation_ref:
        raise InvocationAdmissionError("operation lease belongs to another invocation")
    if (require_current_writer
            and int(lease["writer_fencing_epoch"])
            != lifecycle.service.event_store.writer_epoch):
        raise StaleWriterError(
            "operation lease belongs to another writer epoch")
    binding = context_metadata_by_ref[context.operation_binding_ref]
    lifecycle._validate_registered_budget_binding(
        context_metadata_by_ref[context.budget_witness_ref], binding)
    if (binding.get("budget_scope") != context.budget_scope
            or binding.get("finalization_scope") != context.finalization_scope):
        raise InvocationAdmissionError(
            "invocation budget or Finalization scope differs from its binding")
    exact_binding_authority = {
        "authority_decision_ref": context.authority_decision_ref,
    }
    for field, wanted in exact_binding_authority.items():
        if lifecycle._metadata_ref(binding, field) != wanted:
            raise InvocationAdmissionError(
                f"invocation authority differs from binding {field}")
    decision = context_metadata_by_ref[context.authority_decision_ref]
    statement = decision.get("canonical_statement")
    if (not isinstance(statement, str)
            or not statement
            or decision.get("status") != "effective"):
        raise InvocationAdmissionError(
            "user authority decision statement or status is invalid")
    net = context_metadata_by_ref[context.net_instance_ref]
    if (lifecycle._metadata_ref(net, "team_design_root_ref")
            != context.team_design_root_ref
            or lifecycle._metadata_ref(binding, "team_design_root_ref")
            != context.team_design_root_ref):
        raise InvocationAdmissionError(
            "invocation root differs from its exact net or binding")
    if (context.origin != "petri_operation"
            or context.own_node_ref is None
            or context.own_transition_firing_ref is None
            or context.sponsoring_transition_firing_ref is not None
            or context.parent_invocation_ref is not None
            or context.accounting_parent_invocation_ref
            != context.invocation_ref):
        raise InvocationAdmissionError("invalid Petri invocation origin refs")
    lifecycle._require_ref(
        context.own_node_ref, "node_declaration/v1",
        "node_declaration_version")
    lifecycle._require_ref(
        context.own_transition_firing_ref, "transition_firing/v1",
        "transition_firing_version")
    for activation_ref in (context.activation_ref,
                           context.authorization_lifetime_activation_ref):
        if activation_ref is not None:
            lifecycle._require_ref(
                activation_ref, activation_ref.entity_type,
                activation_ref.version_id.kind)


def decode_invocation_context(cls, value):
    """Decode an exact-ref DTO without treating it as execution authority."""
    required = {
        "task_ref", "task_branch_ref", "task_round_ref", "net_instance_ref",
        "plan_ref", "team_design_root_ref",
        "invocation_ref", "agent_ref",
        "operation_binding_ref",
        "authority_decision_ref",
        "admission_marking_checkpoint_ref", "budget_witness_ref", "principal_ref",
        "operation_execution_lease_ref", "origin", "own_node_ref",
        "own_transition_firing_ref", "sponsoring_transition_firing_ref",
        "activation_ref", "authorization_lifetime_activation_ref",
        "parent_invocation_ref",
        "accounting_parent_invocation_ref", "budget_scope",
        "finalization_scope",
    }
    if set(value) != required:
        raise InvocationAdmissionError(
            f"serialized InvocationContext fields differ: "
            f"missing={sorted(required - set(value))}, "
            f"extra={sorted(set(value) - required)}")

    def optional_ref(name: str) -> VersionRef | None:
        item = value[name]
        return _ref_from_payload(item) if item is not None else None

    origin = str(value["origin"])
    if origin != "petri_operation":
        raise InvocationAdmissionError("serialized InvocationContext origin is invalid")
    finalization = value["finalization_scope"]
    try:
        validate_budget_scopes(value["budget_scope"], finalization)
    except BudgetContractError as exc:
        raise InvocationAdmissionError(
            "serialized InvocationContext budget contract syntax is invalid") from exc
    context = cls(
        task_ref=_ref_from_payload(value["task_ref"]),
        task_branch_ref=_ref_from_payload(value["task_branch_ref"]),
        task_round_ref=_ref_from_payload(value["task_round_ref"]),
        net_instance_ref=_ref_from_payload(value["net_instance_ref"]),
        plan_ref=_ref_from_payload(value["plan_ref"]),
        team_design_root_ref=_ref_from_payload(value["team_design_root_ref"]),
        invocation_ref=_ref_from_payload(value["invocation_ref"]),
        agent_ref=optional_ref("agent_ref"),
        operation_binding_ref=_ref_from_payload(value["operation_binding_ref"]),
        authority_decision_ref=_ref_from_payload(
            value["authority_decision_ref"]),
        admission_marking_checkpoint_ref=_ref_from_payload(
            value["admission_marking_checkpoint_ref"]),
        budget_witness_ref=_ref_from_payload(value["budget_witness_ref"]),
        principal_ref=_ref_from_payload(value["principal_ref"]),
        operation_execution_lease_ref=_ref_from_payload(
            value["operation_execution_lease_ref"]),
        origin=origin,  # type: ignore[arg-type]
        own_node_ref=optional_ref("own_node_ref"),
        own_transition_firing_ref=optional_ref(
            "own_transition_firing_ref"),
        sponsoring_transition_firing_ref=optional_ref(
            "sponsoring_transition_firing_ref"),
        activation_ref=optional_ref("activation_ref"),
        authorization_lifetime_activation_ref=optional_ref(
            "authorization_lifetime_activation_ref"),
        parent_invocation_ref=optional_ref("parent_invocation_ref"),
        accounting_parent_invocation_ref=_ref_from_payload(
            value["accounting_parent_invocation_ref"]),
        budget_scope=str(value["budget_scope"]),
        finalization_scope=finalization,
    )
    return context


def decode_tool_execution_context(cls, value):
    required = {
        "invocation_context", "llm_call_ref",
        "provider_response_resource_ref", "response_disposition_event_id",
        "tool_request_ref", "tool_execution_ref", "tool_catalog_ref",
        "tool_profile_ref", "authorization_snapshot_ref",
    }
    if set(value) != required:
        raise InvocationAdmissionError(
            "serialized ToolExecutionContext fields differ from contract")
    return cls(
        invocation_context=InvocationContext.from_serialized(
            value["invocation_context"]),
        llm_call_ref=_ref_from_payload(value["llm_call_ref"]),
        provider_response_resource_ref=_ref_from_payload(
            value["provider_response_resource_ref"]),
        response_disposition_event_id=TypedId.parse(
            str(value["response_disposition_event_id"]), expected="event"),
        tool_request_ref=_ref_from_payload(value["tool_request_ref"]),
        tool_execution_ref=_ref_from_payload(value["tool_execution_ref"]),
        tool_catalog_ref=_ref_from_payload(value["tool_catalog_ref"]),
        tool_profile_ref=_ref_from_payload(value["tool_profile_ref"]),
        authorization_snapshot_ref=_ref_from_payload(
            value["authorization_snapshot_ref"]),
    )
