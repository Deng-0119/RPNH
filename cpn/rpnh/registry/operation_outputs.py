"""Symbolic product submission through ordinary Registry output authority.

Products are schema-valid fake or real executor bytes, never settlement facts.
Registry IDs are resolved from the admitted executable declaration; an external
executor submits symbolic port keys and an explicitly declared outcome only.
"""
from __future__ import annotations

import json
from typing import Mapping

from ..executable_net import load_compiled_net
from ._registry import _RegistryCore
from .operation_execution import verify_operation_execution
from .operations import OperationExecutionAuthority, RegisteredOperationOutputsAuthority, register_operation_outputs
from .publication import _resource_from_payload
from .resource_service import _ResourceServiceKernel
from .resource_verification import verify_resource
from .resources import PetriOutputOrigin, PublishResource


def publish_operation_products(core: _RegistryCore, kernel: _ResourceServiceKernel,
        repository, execution: OperationExecutionAuthority, *, outcome_id: str,
        products: Mapping[str, tuple[bytes, ...]], idempotency_key: str) -> RegisteredOperationOutputsAuthority:
    """Publish exact declared products, without result/Success/terminal authority.

    The only accepted keys are this registered operation's symbolic output
    ports. Every schema, quantity and selected outcome comes from the actual
    immutable shared declaration, not an executor's role or document prose.
    """
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(idempotency_key, str) or not idempotency_key
            or not isinstance(products, Mapping)):
        raise TypeError("product submission requires owner Registry, bytes mapping and key")
    execution = verify_operation_execution(core, kernel, repository, execution)
    operation = execution.operation
    context = operation.canonical.context
    executable = kernel._exact_object(operation.transition.binding_ref,
        expected_type="executable_transition_binding/v1")
    declaration_ref = _resource_from_payload(executable.metadata["declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(kernel._read_firing_registered(context, declaration_ref)))
    transition = next(item for item in compiled.symbolic.transitions
        if item.name == operation.firing.transition_id)
    declared = next(item for item in compiled.operations if item.declaration.name == transition.operation).declaration
    selected = next((item for item in declared.outcomes if item.name == outcome_id), None)
    if selected is None:
        raise ValueError("product submission selects an undeclared operation outcome")
    if set(products) - set(declared.outputs):
        raise ValueError("product submission invents a symbolic output port")
    quantities = {item.port: item for item in selected.products}
    for name in declared.outputs:
        payloads = products.get(name, ())
        if not isinstance(payloads, tuple) or any(not isinstance(payload, bytes) for payload in payloads):
            raise TypeError("product quantities are explicit tuples of immutable bytes")
        quantity = quantities.get(name)
        if ((quantity is None and payloads)
                or (quantity is not None and not quantity.minimum <= len(payloads) <= quantity.maximum)):
            raise ValueError("product quantity differs from exact selected bundle")
    ports = {item.name: item for item in compiled.ports}
    bindings = {item.port_id: item for item in operation.operation_binding.output_port_bindings}
    artifacts = []
    for name in declared.outputs:
        port = ports[name]
        binding = bindings[port.port_id]
        for ordinal, payload in enumerate(products.get(name, ())):
            ref = kernel.publish_bytes(context, PublishResource(
                origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
                payload=payload, media_type="application/json", content_schema_ref=port.schema,
                summary="Registered operation product", lifetime_ref=context.invocation_ref,
                descriptors={"output_outcome_id": outcome_id,
                    "output_port_id": port.port_id, "place": binding.place},
                idempotency_key=f"{idempotency_key}:product:{port.port_id}:{ordinal}"))
            artifacts.append(verify_resource(core, kernel, operation.canonical, ref))
    return register_operation_outputs(repository, execution, tuple(artifacts),
        selected_outcome_id=outcome_id, idempotency_key=idempotency_key)


__all__ = ("publish_operation_products",)
