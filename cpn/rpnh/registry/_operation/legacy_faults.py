"""Isolated historical Operation fault closure paths.

They remain non-default compatibility paths and use the repository's existing
publication closure; this module does not introduce a transaction of its own.
"""

from __future__ import annotations

from .. import operations as _facade


def _historical_register_operation_fault(
        repository: _facade._OperationAuthorityRepository,
        execution: _facade.OperationExecutionAuthority,
        fault: _facade.RegisterOperationFault, *,
        idempotency_key: str,
) -> _facade._HistoricalOperationFaultAuthority:
    """Publish and rehydrate one closed, reference-only operation fault."""
    if not isinstance(execution, _facade.OperationExecutionAuthority):
        raise TypeError("fault registration requires parent execution authority")
    authority = execution.operation
    if not isinstance(fault, _facade.RegisterOperationFault):
        raise TypeError("fault registration requires closed RegisterOperationFault")
    if not idempotency_key:
        raise _facade.OperationAuthorityError("operation fault idempotency key is empty")
    if isinstance(authority, _facade.RegisteredOperationAuthority):
        claimed = set(authority.firing.claimed_input_refs)
        if set(fault.held_work_refs) | set(fault.returned_capacity_refs) != claimed:
            raise _facade.OperationAuthorityError(
                "operation fault hold/capacity refs differ from firing claim")
    if (fault.held_work_refs != authority.fault_partition.held_work_refs
            or fault.returned_capacity_refs
            != authority.fault_partition.returned_capacity_refs):
        raise _facade.OperationAuthorityError(
            "operation fault command differs from Registry route partition")
    fault_ref, terminal_evidence_ref = repository._historical_publish_operation_fault_closure(
        authority=authority, command=fault, idempotency_key=idempotency_key)
    if (not isinstance(fault_ref, _facade.VersionRef)
            or not isinstance(terminal_evidence_ref, _facade.VersionRef)
            or terminal_evidence_ref.entity_type != "fault_terminal_witness/v1"):
        raise _facade.OperationAuthorityError(
            "Registry did not close operation fault with generic terminal evidence")
    metadata = repository.exact_metadata(fault_ref, expected_type="operation_fault/v1")
    expected = (
        ("invocation_ref", _facade._ref_payload(
            authority.canonical.context.invocation_ref)),
        ("transition_firing_ref", (
            _facade._ref_payload(authority.firing.transition_firing_ref)
            if isinstance(authority, _facade.RegisteredOperationAuthority) else None)),
        ("operation_binding_ref", _facade._ref_payload(
            authority.operation_binding.operation_binding_ref
            if isinstance(authority, _facade.RegisteredOperationAuthority)
            else authority.binding.operation_binding_ref)),
        ("fault_route_binding_ref", _facade._ref_payload(
            authority.operation_binding.fault_route_binding_ref
            if isinstance(authority, _facade.RegisteredOperationAuthority)
            else authority.binding.fault_route.fault_route_binding_ref)),
        ("policy_ref", _facade._ref_payload(authority.spec.fault_policy_ref)),
        ("authority_decision_ref", _facade._ref_payload(
            authority.operation_binding.authority_decision_ref
            if isinstance(authority, _facade.RegisteredOperationAuthority)
            else authority.binding.authority_decision_ref)),
        ("boundary", fault.boundary), ("cause_class", fault.cause_class),
        ("certainty", fault.certainty),
        ("provider_submission_unknown_ref", (
            _facade._ref_payload(fault.provider_submission_unknown_ref)
            if fault.provider_submission_unknown_ref is not None else None)),
        ("held_work_refs", tuple(_facade._ref_payload(ref)
                                  for ref in fault.held_work_refs)),
        ("returned_capacity_refs", tuple(_facade._ref_payload(ref)
                                          for ref in fault.returned_capacity_refs)),
        ("normal_output_resource_refs", ()),
    )
    if not _facade._matches_operation_fault_metadata(metadata, expected):
        raise _facade.OperationAuthorityError(
            "published operation fault differs from closed authority")
    dependency = metadata["dependency_fingerprint"] if "dependency_fingerprint" in metadata else None
    return _facade._HistoricalOperationFaultAuthority(
        execution=execution, operation_fault_ref=fault_ref,
        route=authority.fault_partition.route,
        provider_submission_unknown_ref=fault.provider_submission_unknown_ref,
        boundary=fault.boundary, cause_class=fault.cause_class,
        certainty=fault.certainty,
        fault_terminal_evidence_ref=terminal_evidence_ref,
        held_work_refs=fault.held_work_refs,
        returned_capacity_refs=fault.returned_capacity_refs,
        dependency_fingerprint=dependency, verified_at_head=repository.registry_head())


def _historical_register_operation_uncertain(
        repository: _facade._OperationAuthorityRepository,
        execution: _facade.OperationExecutionAuthority,
        submission_unknown: _facade.ProviderSubmissionUnknownAuthority,
        fault: _facade.RegisterOperationFault, *,
        idempotency_key: str,
) -> _facade._HistoricalOperationUncertainAuthority:
    """Close a submitted-unknown provider attempt without claiming failure."""
    from ..resources import ProviderSubmissionUnknownAuthority

    if not isinstance(execution, _facade.OperationExecutionAuthority):
        raise TypeError("operation uncertainty requires execution authority")
    if not isinstance(submission_unknown, ProviderSubmissionUnknownAuthority):
        raise TypeError("operation uncertainty requires ProviderSubmissionUnknownAuthority")
    if not isinstance(fault, _facade.RegisterOperationFault):
        raise TypeError("operation uncertainty requires RegisterOperationFault")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise _facade.OperationAuthorityError("operation uncertainty key is empty")
    authority = execution.operation
    attempt = submission_unknown.permit.dispatch.attempt
    if isinstance(attempt, _facade.ProviderAttemptV2Authority):
        attempt_invocation_ref = attempt.call.invocation_ref
        attempt_operation_binding_ref = attempt.call.operation_binding_ref
    elif isinstance(attempt, _facade.ProviderAttemptAuthority):
        attempt_invocation_ref = attempt.invocation_ref
        attempt_operation_binding_ref = attempt.operation_binding_ref
    else:  # pragma: no cover - submission_unknown validates this union
        raise TypeError("operation uncertainty has an invalid provider attempt")
    if (attempt_invocation_ref != authority.canonical.context.invocation_ref
            or attempt_operation_binding_ref
            != authority.operation_binding.operation_binding_ref
            or fault.boundary != "provider"
            or fault.cause_class != "submitted_outcome_unknown"
            or fault.certainty != "unknown_requires_reconciliation"
            or fault.provider_submission_unknown_ref
            != submission_unknown.provider_submission_unknown_ref
            or fault.held_work_refs != authority.fault_partition.held_work_refs
            or fault.returned_capacity_refs
            != authority.fault_partition.returned_capacity_refs):
        raise _facade.OperationAuthorityError(
            "provider submission uncertainty differs from execution/route")
    fault_ref, terminal_evidence_ref = repository._historical_publish_operation_fault_closure(
        authority=authority, command=fault, idempotency_key=idempotency_key)
    if (not isinstance(fault_ref, _facade.VersionRef)
            or fault_ref.entity_type != "operation_fault/v1"
            or not isinstance(terminal_evidence_ref, _facade.VersionRef)
            or terminal_evidence_ref.entity_type != "fault_terminal_witness/v1"):
        raise _facade.OperationAuthorityError(
            "submitted-unknown fault lacks generic terminal evidence")
    metadata = repository.exact_metadata(fault_ref, expected_type="operation_fault/v1")
    expected = (
        ("invocation_ref", _facade._ref_payload(authority.canonical.context.invocation_ref)),
        ("transition_firing_ref", _facade._ref_payload(authority.firing.transition_firing_ref)),
        ("operation_binding_ref", _facade._ref_payload(
            authority.operation_binding.operation_binding_ref)),
        ("fault_route_binding_ref", _facade._ref_payload(
            authority.operation_binding.fault_route_binding_ref)),
        ("policy_ref", _facade._ref_payload(authority.spec.fault_policy_ref)),
        ("authority_decision_ref", _facade._ref_payload(
            authority.operation_binding.authority_decision_ref)),
        ("boundary", "provider"), ("cause_class", "submitted_outcome_unknown"),
        ("certainty", "unknown_requires_reconciliation"),
        ("provider_submission_unknown_ref", _facade._ref_payload(
            submission_unknown.provider_submission_unknown_ref)),
        ("held_work_refs", tuple(_facade._ref_payload(ref)
                                  for ref in fault.held_work_refs)),
        ("returned_capacity_refs", tuple(_facade._ref_payload(ref)
                                          for ref in fault.returned_capacity_refs)),
        ("normal_output_resource_refs", ()),
    )
    if not _facade._matches_operation_fault_metadata(metadata, expected):
        raise _facade.OperationAuthorityError(
            "operation uncertainty fault metadata is not closed")
    return _facade._HistoricalOperationUncertainAuthority(
        execution=execution, submission_unknown=submission_unknown,
        operation_fault_ref=fault_ref, route=authority.fault_partition.route,
        boundary="provider", cause_class="submitted_outcome_unknown",
        certainty="unknown_requires_reconciliation",
        fault_terminal_evidence_ref=terminal_evidence_ref,
        held_work_refs=fault.held_work_refs,
        returned_capacity_refs=fault.returned_capacity_refs,
        dependency_fingerprint=(metadata["dependency_fingerprint"]
                                if "dependency_fingerprint" in metadata else None),
        verified_at_head=repository.registry_head())
