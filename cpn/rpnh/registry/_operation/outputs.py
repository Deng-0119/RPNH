"""Verification of an already-published Operation output bundle."""

from __future__ import annotations

from ..operations import (
    OperationAuthorityError,
    OperationExecutionAuthority,
    RegisteredOperationOutputAuthority,
    RegisteredOperationOutputsAuthority,
    VerifiedResourceArtifact,
    _OperationAuthorityRepository,
)


def register_operation_outputs(
        repository: _OperationAuthorityRepository,
        execution: OperationExecutionAuthority,
        outputs: tuple[VerifiedResourceArtifact, ...], *,
        idempotency_key: str,
        selected_outcome_id: str | None = None,
) -> RegisteredOperationOutputsAuthority:
    """Close already-published exact resources over one operation authority.

    This does not publish ``operation_result/v1`` and does not settle a firing;
    ``complete_registered_firing`` remains the sole atomic terminal-ready path.
    """
    if not isinstance(execution, OperationExecutionAuthority):
        raise TypeError("output registration requires OperationExecutionAuthority")
    authority = execution.operation
    if not idempotency_key:
        raise OperationAuthorityError("operation output idempotency key is empty")
    if any(not isinstance(item, VerifiedResourceArtifact) for item in outputs):
        raise TypeError("operation outputs must be exact verified resources")
    if len({item.header.ref for item in outputs}) != len(outputs):
        raise OperationAuthorityError("operation output resources contain duplicates")
    registered = tuple(repository.resolve_operation_output(
        authority=authority, artifact=artifact) for artifact in outputs)
    if any(not isinstance(item, RegisteredOperationOutputAuthority)
           for item in registered):
        raise TypeError("Registry returned an untyped operation output")
    ports = {port.port_id: port for port in authority.spec.output_ports}
    binding_by_port = {
        item.port_id: item
        for item in authority.operation_binding.output_port_bindings}
    grouped: dict[str, list[RegisteredOperationOutputAuthority]] = {
        port_id: [] for port_id in ports}
    for item in registered:
        port = ports.get(item.port_id)
        port_binding = binding_by_port.get(item.port_id)
        if (port is None or item.schema_ref != port.schema_ref
                or port_binding is None
                or item.output_binding_ref != port_binding.output_binding_ref
                or item.place != port_binding.place
                or item.place_ref != port_binding.place_ref
                or item.artifact.header.content_schema_ref
                != port.content_schema_id
                or item.artifact.header.task_ref
                != authority.canonical.context.task_ref):
            raise OperationAuthorityError(
                "published output is outside operation binding/spec authority")
        grouped[item.port_id].append(item)
    for port_id, port in ports.items():
        count = len(grouped[port_id])
        if count and (count < port.minimum or count > port.maximum):
            raise OperationAuthorityError(
                f"operation output port {port_id!r} violates declared cardinality")
    selected, registered = repository.validate_operation_output_bundle(
        authority=authority, outputs=registered,
        selected_outcome_id=selected_outcome_id)
    ordered = tuple(sorted(
        registered,
        key=lambda item: (item.port_id, str(item.resource_ref.resource_version_id))))
    return RegisteredOperationOutputsAuthority(
        execution=execution, outputs=ordered,
        verified_at_head=repository.registry_head(),
        selected_outcome_id=selected)
