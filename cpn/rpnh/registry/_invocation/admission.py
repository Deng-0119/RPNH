"""Admission implementation for the InvocationLifecycle facade."""

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

def _validate_claim_scope(lifecycle, claim: FiringClaim) -> Mapping[str, Any]:
    task = lifecycle._require_ref(claim.task_ref, "task/v1", "task_version")
    if claim.task_ref.entity_id != lifecycle.service.task_id:
        raise InvocationAdmissionError("firing task ref is not this registry task")
    branch = lifecycle._require_ref(
        claim.task_branch_ref, "task_branch/v1", "task_branch_version")
    task_round = lifecycle._require_ref(
        claim.task_round_ref, "task_round/v1", "task_round_version")
    net = lifecycle._require_ref(
        claim.net_instance_ref, "net_instance/v1", "net_instance_version")
    lifecycle._require_ref(claim.plan_ref, "plan_version/v1", "plan_version")
    root_ref = lifecycle._metadata_ref(net, "team_design_root_ref")
    root = lifecycle._require_team_design_root(root_ref)
    node = lifecycle._require_ref(
        claim.node_ref, "node_declaration/v1", "node_declaration_version")
    binding = lifecycle._require_ref(
        claim.operation_binding_ref, "operation_binding/v1",
        "operation_binding_version")
    marking = lifecycle._require_ref(
        claim.marking_checkpoint_ref, "marking_checkpoint/v1",
        "marking_checkpoint_version")
    lifecycle._require_ref(claim.principal_ref, "principal/v1", "principal_version")
    _, closed_node, closed_binding = lifecycle._require_registered_operation_closure(
        net_ref=claim.net_instance_ref, plan_ref=claim.plan_ref,
        team_design_root_ref=root_ref, node_ref=claim.node_ref,
        operation_binding_ref=claim.operation_binding_ref)
    if closed_node != node or closed_binding != binding:
        raise InvocationAdmissionError(
            "claim operation tuple changed during registered closure validation")
    transition_matches: list[Mapping[str, Any]] = []
    for value in net.get("executable_transition_binding_refs", ()):
        transition_ref = _ref_from_payload(value)
        transition = lifecycle._require_ref(
            transition_ref, "executable_transition_binding/v1",
            "executable_transition_binding_version")
        if (transition.get("node_ref") == _ref_payload(claim.node_ref)
                and transition.get("operation_binding_ref")
                == _ref_payload(claim.operation_binding_ref)):
            transition_matches.append(transition)
    if len(transition_matches) != 1:
        raise InvocationAdmissionError(
            "firing claim lacks one executable transition binding")
    transition = transition_matches[0]
    raw_agent_ref = transition.get("agent_ref")
    transition_agent_ref = (
        _ref_from_payload(raw_agent_ref)
        if raw_agent_ref is not None else None)
    if transition_agent_ref != claim.agent_ref:
        raise InvocationAdmissionError(
            "firing agent differs from the executable transition")
    if transition_agent_ref is not None:
        agent = lifecycle._require_ref(
            transition_agent_ref, "agent/v1", "agent_version")
        if (agent.get("agent_ref")
                != _ref_payload(transition_agent_ref)
                or agent.get("shadow_transition_id")
                != node.get("transition_id")):
            raise InvocationAdmissionError(
                "firing agent differs from its exact declaration shadow")
    if task.get("task_id") != str(lifecycle.service.task_id):
        raise InvocationAdmissionError("registered task does not match registry identity")
    if binding.get("origin") != "petri_operation":
        raise InvocationAdmissionError("Petri firing requires a Petri operation binding")
    expected = (
        (lifecycle._metadata_ref(branch, "task_ref"), claim.task_ref, "branch/task"),
        (lifecycle._metadata_ref(task_round, "task_branch_ref"), claim.task_branch_ref,
         "round/branch"),
        (lifecycle._metadata_ref(net, "task_round_ref"), claim.task_round_ref,
         "net/round"),
        (lifecycle._metadata_ref(net, "plan_ref"), claim.plan_ref, "net/plan"),
        (lifecycle._metadata_ref(root, "task_ref"), claim.task_ref, "root/task"),
        (lifecycle._metadata_ref(root, "task_round_ref"), claim.task_round_ref,
         "root/round"),
        (lifecycle._metadata_ref(node, "team_design_root_ref"), root_ref,
         "node/root"),
        (lifecycle._metadata_ref(binding, "team_design_root_ref"), root_ref,
         "binding/root"),
        (lifecycle._metadata_ref(node, "plan_ref"), claim.plan_ref, "node/plan"),
        (lifecycle._metadata_ref(binding, "node_ref"), claim.node_ref, "binding/node"),
        (lifecycle._metadata_ref(binding, "principal_ref"), claim.principal_ref,
         "binding/principal"),
    )
    for actual, wanted, label in expected:
        if not lifecycle._same_ref(actual, wanted):
            raise InvocationAdmissionError(f"exact scope mismatch: {label}")
    if (marking.get("net_instance_ref") != _ref_payload(claim.net_instance_ref)
            or not bool(marking.get("settled"))):
        raise InvocationAdmissionError(
            "firing requires a settled marking checkpoint for its exact net")
    if claim.activation_ref is not None:
        publications = claim.admission_publications
        if publications is None:
            activation = lifecycle._require_ref(
                claim.activation_ref, claim.activation_ref.entity_type,
                claim.activation_ref.version_id.kind)
            if (lifecycle._metadata_ref(activation, "net_instance_ref")
                    != claim.net_instance_ref):
                raise InvocationAdmissionError(
                    "firing activation belongs to another net")
        else:
            activations = tuple(
                item for item in publications.objects
                if item.ref == claim.activation_ref)
            expected_scope = {
                "net_instance_ref": _ref_payload(claim.net_instance_ref),
                "node_ref": _ref_payload(claim.node_ref),
                "producer_operation_binding_ref": _ref_payload(
                    claim.operation_binding_ref),
            }
            if (len(activations) != 1
                    or any(activations[0].metadata.get(key) != value
                           for key, value in expected_scope.items())):
                raise InvocationAdmissionError(
                    "prepared activation scope differs from the firing claim")
    for item in claim.claimed_input_refs:
        lifecycle._require_ref(item, item.entity_type, item.version_id.kind)
    return binding

def _validate_registered_budget_binding(
        lifecycle, manifest: Mapping[str, Any], binding: Mapping[str, Any],
) -> None:
    # REQUIRED producer/schema wiring: the exact published manifest must
    # contain budget_buckets and bindings must contain budget_bucket_id.
    # An unpublished contract never falls back to implicit role policy.
    if (manifest.get("task_id") != str(lifecycle.service.task_id)
            or manifest.get("branch_id") != lifecycle.service.branch_id):
        raise InvocationAdmissionError(
            "budget manifest belongs to another registry task or branch")
    try:
        validate_budget_binding(manifest, binding)
    except BudgetContractError as exc:
        raise InvocationAdmissionError(
            "registered operation budget bucket contract is invalid") from exc

def _assert_current_claim_authority(lifecycle, claim: FiringClaim) -> VersionRef:
    if lifecycle._current_active_net_ref() != claim.net_instance_ref:
        raise InvocationAdmissionError("firing claim does not name the active net head")
    if lifecycle._current_marking_ref(claim.net_instance_ref) != claim.marking_checkpoint_ref:
        raise InvocationAdmissionError(
            "firing claim does not name the current marking head")
    budget_ref = lifecycle._current_budget_ref()
    marking = lifecycle._require_ref(
        claim.marking_checkpoint_ref, "marking_checkpoint/v1",
        "marking_checkpoint_version")
    present = {str(ref.version_id) for ref in lifecycle._marking_token_refs(marking)}
    requested = {str(ref.version_id) for ref in claim.claimed_input_refs}
    missing = sorted(requested - present)
    if missing:
        raise InvocationAdmissionError(
            f"firing claim input is absent from the current marking: {missing}")
    return budget_ref

def _active_claim_inputs(
        lifecycle, net_ref: VersionRef,
) -> list[tuple[str, set[str], set[str]]]:
    events = lifecycle.service.event_store.firing_lifecycle_events_for_net(
        net_ref)
    admitted = {
        event.aggregate_id for event in events
        if event.event_type == "firing_admitted/v1"
        and event.net_instance_id == net_ref.entity_id
    }
    settled = {
        event.aggregate_id for event in events
        if ((event.event_type == "transition_firing_settled/v1"
             and event.net_instance_id == net_ref.entity_id)
            or (event.event_type
                == "transition_firing_superseded_by_growth_recovery/v1"
                and event.payload.get("superseded_net_ref")
                == _ref_payload(net_ref)))
    }
    active: list[tuple[str, set[str], set[str]]] = []
    current_epoch = lifecycle.service.event_store.writer_epoch

    def writer_epoch(metadata: Mapping[str, Any], *, label: str) -> int:
        value = metadata.get("writer_fencing_epoch")
        if isinstance(value, bool):
            raise InvocationAdmissionError(
                f"active firing {label} writer epoch is malformed")
        try:
            epoch = int(value)
        except (TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                f"active firing {label} writer epoch is malformed") from exc
        if epoch < 0 or epoch > current_epoch:
            raise InvocationAdmissionError(
                f"active firing {label} writer epoch is invalid")
        return epoch

    for firing_id in sorted(admitted - settled):
        rows = lifecycle.service.event_store.object_rows_by_logical(
            firing_id, object_type="transition_firing/v1")
        if len(rows) != 1:
            raise InvocationAdmissionError(
                f"active firing has no unique immutable object: {firing_id}")
        metadata = json.loads(rows[0]["metadata_json"])
        try:
            admission_ref = _ref_from_payload(metadata["firing_admission_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "active firing has no exact firing admission") from exc
        admission = lifecycle._require_ref(
            admission_ref, "firing_admission/v1", "firing_admission_version")
        try:
            lease_ref = _ref_from_payload(
                admission["operation_execution_lease_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "active firing admission has no exact operation lease") from exc
        lease = lifecycle._require_ref(
            lease_ref, "operation_execution_lease/v1",
            "operation_execution_lease_version")
        admission_epoch = writer_epoch(admission, label="admission")
        lease_epoch = writer_epoch(lease, label="operation lease")
        if admission_epoch < current_epoch or lease_epoch < current_epoch:
            continue
        try:
            delta_ref = _ref_from_payload(
                metadata["claim_marking_delta_ref"])
            delta = lifecycle._require_ref(
                delta_ref, "marking_delta/v1", "marking_delta_version")
            consumed = {
                str(_ref_from_payload(value).version_id)
                for value in delta["consumed_refs"]
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "active firing claim delta is malformed") from exc
        claimed = set(metadata["claimed_input_version_ids"])
        if not consumed.issubset(claimed):
            raise InvocationAdmissionError(
                "active firing consumed refs exceed its exact claim")
        active.append((firing_id, claimed, consumed))
    return active

def admit_firing(lifecycle, claim: FiringClaim, *, idempotency_key: str) -> FiringAdmission:
    """Atomically claim and start one independently-settled real firing."""
    caller_idempotency_key = idempotency_key
    idempotency_key = lifecycle._scoped_firing_idempotency_key(
        caller_idempotency_key)
    if (isinstance(claim.attempt_index, bool)
            or not isinstance(claim.attempt_index, int)
            or claim.attempt_index <= 0):
        raise InvocationAdmissionError(
            "firing claim requires one durable positive attempt index")
    consumed_input_refs = (
        claim.claimed_input_refs
        if claim.consumed_input_refs is None
        else claim.consumed_input_refs)
    if (not isinstance(consumed_input_refs, tuple)
            or any(not isinstance(ref, VersionRef)
                   for ref in consumed_input_refs)
            or len(set(consumed_input_refs)) != len(consumed_input_refs)
            or not set(consumed_input_refs).issubset(
                set(claim.claimed_input_refs))):
        raise InvocationAdmissionError(
            "firing consumed inputs must be a unique subset of its claim")
    publications = claim.admission_publications
    if (publications is not None
            and (not isinstance(
                publications, PreparedFiringAdmissionPublications)
                 or claim.activation_ref
                 != publications.activation_ref)):
        raise InvocationAdmissionError(
            "admission publications differ from the firing claim")
    claimed_activation = (
        _ref_payload(claim.activation_ref)
        if claim.activation_ref is not None else None)
    existing = lifecycle._existing_command_events(idempotency_key)
    if existing:
        admitted = next((event for event in existing
                         if event.event_type == "firing_admitted/v1"), None)
        if admitted is None:
            raise InvocationAdmissionError("idempotency key belongs to another command")
        context = lifecycle.hydrate_context(_ref_from_payload(admitted.payload["invocation_ref"]))
        firing = lifecycle._require_ref(
            context.own_transition_firing_ref, "transition_firing/v1",
            "transition_firing_version")
        node = lifecycle._require_ref(
            claim.node_ref, "node_declaration/v1",
            "node_declaration_version")
        expected = {
            "transition_id": node["transition_id"],
            "attempt_index": claim.attempt_index,
            "activation_ref": claimed_activation,
            "task_ref": _ref_payload(claim.task_ref),
            "task_branch_ref": _ref_payload(claim.task_branch_ref),
            "task_round_ref": _ref_payload(claim.task_round_ref),
            "net_instance_ref": _ref_payload(claim.net_instance_ref),
            "plan_ref": _ref_payload(claim.plan_ref),
            "node_ref": _ref_payload(claim.node_ref),
            "agent_ref": (
                _ref_payload(claim.agent_ref)
                if claim.agent_ref is not None else None),
            "operation_binding_ref": _ref_payload(claim.operation_binding_ref),
            "principal_ref": _ref_payload(claim.principal_ref),
            "admission_marking_checkpoint_ref": _ref_payload(
                claim.marking_checkpoint_ref),
            "logical_tau": claim.logical_tau,
            "claimed_input_refs": [
                _ref_payload(ref) for ref in claim.claimed_input_refs],
        }
        if any(firing.get(key) != value for key, value in expected.items()):
            raise InvocationAdmissionError(
                "firing-admission idempotency key was reused for different input")
        try:
            claim_delta = lifecycle._require_ref(
                _ref_from_payload(firing["claim_marking_delta_ref"]),
                "marking_delta/v1", "marking_delta_version")
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "firing-admission replay lacks its claim delta") from exc
        if claim_delta.get("consumed_refs") != [
                _ref_payload(ref) for ref in consumed_input_refs]:
            raise InvocationAdmissionError(
                "firing-admission idempotency key changed consume semantics")
        return FiringAdmission(
            _ref_from_payload(admitted.payload["firing_admission_ref"]), context,
            _ref_from_payload(admitted.payload["claim_marking_delta_ref"]),
            admitted.event_id)
    binding = lifecycle._validate_claim_scope(claim)
    node = lifecycle._require_ref(
        claim.node_ref, "node_declaration/v1", "node_declaration_version")
    if ((publications is None
         and node.get("activation_ref") != claimed_activation)
            or (publications is not None
                and (node.get("activation_ref") is not None
                     or claimed_activation
                     != _ref_payload(publications.activation_ref)))):
        raise InvocationAdmissionError(
            "firing activation differs from the executable node mapping")
    budget_ref = lifecycle._assert_current_claim_authority(claim)
    lifecycle._validate_registered_budget_binding(
        lifecycle._require_ref(
            budget_ref, "task_recovery_manifest/v1", "resource_version"),
        binding)
    requested = {str(ref.version_id) for ref in claim.claimed_input_refs}
    consumed_requested = {
        str(ref.version_id) for ref in consumed_input_refs}
    for firing_id, claimed, consumed in lifecycle._active_claim_inputs(
            claim.net_instance_ref):
        overlap = sorted(
            (consumed_requested & claimed) | (requested & consumed))
        if overlap:
            raise InvocationAdmissionError(
                f"firing conflicts with active claim {firing_id}: {overlap}")

    firing_id = _stable_id("transition_firing", idempotency_key)
    firing_version = _stable_id("transition_firing_version", idempotency_key)
    admission_id = _stable_id("firing_admission", idempotency_key)
    admission_version = _stable_id("firing_admission_version", idempotency_key)
    invocation_id = _stable_id("invocation", idempotency_key)
    invocation_version = _stable_id("invocation_version", idempotency_key)
    lease_id = _stable_id("operation_execution_lease", idempotency_key)
    lease_version = _stable_id(
        "operation_execution_lease_version", idempotency_key)
    delta_id = _stable_id("marking_delta", idempotency_key)
    delta_version = _stable_id("marking_delta_version", idempotency_key)
    firing_ref = VersionRef(
        "transition_firing/v1", firing_id, firing_version)
    admission_ref = VersionRef(
        "firing_admission/v1", admission_id, admission_version)
    invocation_ref = VersionRef("invocation/v1", invocation_id, invocation_version)
    lease_ref = VersionRef(
        "operation_execution_lease/v1", lease_id, lease_version)
    delta_ref = VersionRef("marking_delta/v1", delta_id, delta_version)
    if (publications is not None
            and publications.invocation_ref != invocation_ref):
        raise InvocationAdmissionError(
            "admission publications predicted another invocation")
    provisional = InvocationContext(
        task_ref=claim.task_ref, task_branch_ref=claim.task_branch_ref,
        task_round_ref=claim.task_round_ref,
        net_instance_ref=claim.net_instance_ref, plan_ref=claim.plan_ref,
        team_design_root_ref=lifecycle._metadata_ref(
            lifecycle._require_ref(
                claim.net_instance_ref, "net_instance/v1",
                "net_instance_version"),
            "team_design_root_ref"),
        invocation_ref=invocation_ref,
        agent_ref=claim.agent_ref,
        operation_binding_ref=claim.operation_binding_ref,
        authority_decision_ref=lifecycle._metadata_ref(
            binding, "authority_decision_ref"),
        admission_marking_checkpoint_ref=claim.marking_checkpoint_ref,
        budget_witness_ref=budget_ref,
        principal_ref=claim.principal_ref,
        operation_execution_lease_ref=lease_ref, origin="petri_operation",
        own_node_ref=claim.node_ref, own_transition_firing_ref=firing_ref,
        sponsoring_transition_firing_ref=None,
        activation_ref=claim.activation_ref,
        authorization_lifetime_activation_ref=None,
        parent_invocation_ref=None,
        accounting_parent_invocation_ref=invocation_ref,
        budget_scope=str(binding["budget_scope"]),
        finalization_scope=(str(binding["finalization_scope"])
                            if binding["finalization_scope"] is not None else None))
    context = provisional
    firing = {
        "transition_firing_ref": _ref_payload(firing_ref),
        "transition_id": node["transition_id"],
        "attempt_index": claim.attempt_index,
        "activation_ref": claimed_activation,
        "task_ref": _ref_payload(claim.task_ref),
        "task_branch_ref": _ref_payload(claim.task_branch_ref),
        "task_round_ref": _ref_payload(claim.task_round_ref),
        "net_instance_ref": _ref_payload(claim.net_instance_ref),
        "plan_ref": _ref_payload(claim.plan_ref),
        "node_ref": _ref_payload(claim.node_ref),
        "agent_ref": (
            _ref_payload(claim.agent_ref)
            if claim.agent_ref is not None else None),
        "operation_binding_ref": _ref_payload(claim.operation_binding_ref),
        "principal_ref": _ref_payload(claim.principal_ref),
        "admission_marking_checkpoint_ref": _ref_payload(
            claim.marking_checkpoint_ref),
        "budget_witness_ref": _ref_payload(budget_ref),
        "firing_admission_ref": _ref_payload(admission_ref),
        "claim_marking_delta_ref": _ref_payload(delta_ref),
        "logical_tau": claim.logical_tau,
        "claimed_input_refs": [_ref_payload(ref) for ref in claim.claimed_input_refs],
        "claimed_input_version_ids": sorted(requested),
    }
    admission = {
        "firing_admission_ref": _ref_payload(admission_ref),
        "transition_firing_ref": _ref_payload(firing_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_execution_lease_ref": _ref_payload(lease_ref),
        "claim_marking_delta_ref": _ref_payload(delta_ref),
        "admission_marking_checkpoint_ref": _ref_payload(
            claim.marking_checkpoint_ref),
        "logical_tau": claim.logical_tau,
        "writer_fencing_epoch": lifecycle.service.writer_epoch,
    }
    lease = {
        "operation_execution_lease_ref": _ref_payload(lease_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "writer_fencing_epoch": lifecycle.service.writer_epoch,
        "lease_generation": 1,
        "state": "dispatch_reserved",
    }
    invocation = context.serialized()
    marking_delta = {
        "marking_delta_ref": _ref_payload(delta_ref),
        "net_instance_ref": _ref_payload(claim.net_instance_ref),
        "transition_firing_refs": [_ref_payload(firing_ref)],
        "operation_binding_refs": [
            _ref_payload(claim.operation_binding_ref)],
        "consumed_refs": [
            _ref_payload(ref) for ref in consumed_input_refs],
        "deposited_refs": [],
        "phase": "claim",
        "provider_submission_unknown_refs": [],
    }
    from ..publication import _current_process_configuration_refs_v1
    immutable_config_ref, mutable_config_ref, process_budget_ref, (
        run_execution_authority_ref) = (
            _current_process_configuration_refs_v1(lifecycle.service))
    if process_budget_ref != budget_ref:
        raise InvocationAdmissionError(
            "process configuration recovery authority changed before admission")
    tx = lifecycle.service.begin(
        idempotency_key=idempotency_key,
        task_round_id=claim.task_round_ref.entity_id,
        net_instance_id=claim.net_instance_ref.entity_id)
    if publications is not None:
        from ..publication import _append_direct_resource_version_publication
        for resource in publications.resources:
            value = dict(resource.metadata)
            expected_resource = {
                "resource_id": str(resource.ref.resource_id),
                "resource_version_id": str(
                    resource.ref.resource_version_id),
                "task_ref": _ref_payload(claim.task_ref),
                "round_ref": _ref_payload(claim.task_round_ref),
                "net_ref": _ref_payload(claim.net_instance_ref),
                "producer_ref": _ref_payload(invocation_ref),
                "size": len(resource.payload),
                "media_type": resource.media_type,
            }
            if any(value.get(key) != expected
                   for key, expected in expected_resource.items()):
                raise InvocationAdmissionError(
                    "admission resource metadata differs from firing scope")
            _append_direct_resource_version_publication(
                tx, ref=resource.ref, payload=resource.payload,
                metadata_factory=(
                    lambda _prepared, exact=value: exact),
                media_type=resource.media_type,
                producer_ref=invocation_ref,
                producer_invocation_id=invocation_id,
                relation_key=idempotency_key,
                direct_owners=resource.direct_owner_refs,
                input_resources=resource.input_resource_refs)
        for item in publications.objects:
            value = dict(item.metadata)
            object_type = item.ref.entity_type
            lifecycle.service.catalog.validate_instance(
                object_type, category="object", instance=value)
            tx.prewrite(
                object_type=object_type,
                logical_id=item.ref.entity_id,
                version_id=item.ref.version_id,
                payload=canonical_json(value), metadata=value,
                media_type="application/json",
                schema_ref=f"registry_v1/{object_type}",
                producer_invocation_id=invocation_id)
    for object_type, ref, value in (
            ("invocation/v1", invocation_ref, invocation),
            ("transition_firing/v1", firing_ref, firing),
            ("firing_admission/v1", admission_ref, admission),
            ("operation_execution_lease/v1", lease_ref, lease),
            ("marking_delta/v1", delta_ref, marking_delta)):
        lifecycle.service.catalog.validate_instance(
            object_type, category="object", instance=value)
        tx.prewrite(
            object_type=object_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(value),
            metadata=value, media_type="application/json",
            schema_ref=f"registry_v1/{object_type}",
            producer_invocation_id=(invocation_id if object_type != "invocation/v1"
                                    else None))
    relations = [
        ("firing_of_node", firing_ref, claim.node_ref),
        ("firing_in_net", firing_ref, claim.net_instance_ref),
        ("invocation_of_firing", invocation_ref, firing_ref),
        ("invocation_as_principal", invocation_ref, claim.principal_ref),
        ("invocation_uses_binding", invocation_ref, claim.operation_binding_ref),
        ("derived_from", invocation_ref, claim.marking_checkpoint_ref),
        ("invocation_governed_by_limits", invocation_ref, budget_ref),
        ("invocation_uses_lease", invocation_ref, lease_ref),
        ("derived_from", invocation_ref,
         immutable_config_ref.as_version_ref()),
        ("derived_from", invocation_ref,
         mutable_config_ref.as_version_ref()),
        ("derived_from", invocation_ref, run_execution_authority_ref),
    ]
    if claim.firing_allocation_ref is not None:
        relations.append((
            "derived_from", firing_ref, claim.firing_allocation_ref))
    if claim.agent_ref is not None:
        relations.append((
            "invocation_uses_agent", invocation_ref, claim.agent_ref))
    if claim.activation_ref is not None:
        relations.append((
            "invocation_in_activation", invocation_ref, claim.activation_ref))
    for relation_type, source, target in relations:
        tx.relate(TypedRelation(
            _stable_id(
                "relation",
                f"{idempotency_key}:{relation_type}:"
                f"{source.version_id}:{target.version_id}"),
            relation_type, source, target),
            producer_invocation_id=invocation_id)
    shared_stream = f"firing-claims:{claim.net_instance_ref.version_id}"
    common = {
        "firing_admission_ref": _ref_payload(admission_ref),
        "transition_firing_ref": _ref_payload(firing_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_execution_lease_ref": _ref_payload(lease_ref),
        "claim_marking_delta_ref": _ref_payload(delta_ref),
        "logical_tau": claim.logical_tau,
    }
    for event_type, aggregate_id, stream_id in (
            ("firing_admitted/v1", str(firing_id), shared_stream),
            ("transition_firing_started/v1", str(firing_id),
             f"transition-firing:{firing_id}"),
            ("invocation_started/v1", str(invocation_id),
             f"invocation:{invocation_id}"),
            ("operation_dispatch_reserved/v1", str(lease_id),
             f"operation-lease:{lease_id}")):
        event_payload = common
        if event_type == "invocation_started/v1":
            event_payload = {"origin": "petri_operation", **common}
        elif event_type == "operation_dispatch_reserved/v1":
            event_payload = {
                "origin": "petri_operation",
                "invocation_ref": _ref_payload(invocation_ref),
                "operation_execution_lease_ref": _ref_payload(lease_ref),
            }
        tx.append(PendingEvent(
            event_type=event_type, criticality="authoritative",
            stream_id=stream_id, aggregate_id=aggregate_id,
            aggregate_type=event_type.split("/", 1)[0],
            idempotency_key=idempotency_key, command_id=idempotency_key,
            payload=event_payload, payload_schema_ref=f"registry_v1/{event_type}",
            task_control=True, producer_principal=str(claim.principal_ref.entity_id),
            producer_invocation_id=invocation_id))
    try:
        committed = tx.commit()
    except RegistryConflict as exc:
        if "firing conflicts with active claim" in str(exc):
            raise InvocationAdmissionError(str(exc)) from exc
        if "compare-and-append conflict for 'firing-claims:" in str(exc):
            # A disjoint same-tau admission may have advanced only the
            # shared claim stream. Rebuild against the new authoritative
            # head; EventStore rechecks token/resource conflicts atomically.
            return lifecycle.admit_firing(
                claim, idempotency_key=caller_idempotency_key)
        raise
    admission_event = next(
        event for event in committed
        if event.event_type == "firing_admitted/v1")
    return FiringAdmission(
        admission_ref, context, delta_ref, admission_event.event_id)
