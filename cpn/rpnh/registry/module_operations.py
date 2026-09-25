"""Publish typed operation inventory through the sole Registry authority.

The shared compiler supplies symbolic inventory, not runtime references or HOST
trust. Native/current configuration and external modules use the same immutable
spec publisher. This boundary creates no firing, adoption or terminal evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from ..executable_net import CompiledPetriNet
from ..registration import Registration
from ._registry import _RegistryCore
from .models import VersionRef
from .operations import OperationAuthorityError, OperationPortAuthority, registered_operation_contract
from .resources import ResourceVersionRef
from .strict_contracts import publish_operation_spec


@dataclass(frozen=True, slots=True)
class OperationPublication:
    name: str
    operation_id: str
    executor_key: str
    input_ports: tuple[OperationPortAuthority, ...]
    output_ports: tuple[OperationPortAuthority, ...]
    llm_prompt_port_id: str | None
    allowed_tool_ids: tuple[str, ...]
    idempotency_key: str


def publish_operation_inventory(core: _RegistryCore,
                                inventory: tuple[OperationPublication, ...]) -> dict[str, VersionRef]:
    """Publish each exact declared ABI; preserve its allocated key/identity.

    Uses the existing immutable spec transaction boundary, not a new writer or
    a replacement operation repository. Caller keys distinguish repeated uses
    of one registered executor (for example two different logical transitions).
    """
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("operation inventory requires the execution owner's Registry")
    if any(not isinstance(item, OperationPublication) for item in inventory):
        raise TypeError("operation inventory requires typed declarations")
    names = tuple(item.name for item in inventory)
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise OperationAuthorityError("operation inventory needs unique exact logical keys")
    return {item.name: publish_operation_spec(
        core, operation_id=item.operation_id, executor_key=item.executor_key,
        input_ports=item.input_ports, output_ports=item.output_ports,
        llm_prompt_port_id=item.llm_prompt_port_id, allowed_tool_ids=item.allowed_tool_ids,
        idempotency_key=item.idempotency_key,
    ) for item in inventory}


def publish_module_operations(core: _RegistryCore, compiled: CompiledPetriNet,
                              registration: Registration,
                              schema_refs: Mapping[str, ResourceVersionRef], *,
                              idempotency_key: str) -> dict[str, VersionRef]:
    """Resolve real schema authority and publish shared lowered operations.

    Rehydrated wire data cannot register executable code: all recorded HOST
    declarations must match this execution owner's explicit Registration.
    Exact input order and conditional output quantity envelopes are retained.
    This is the operation part of Module publication, not a complete net launch.
    """
    if not isinstance(compiled, CompiledPetriNet) or not isinstance(registration, Registration):
        raise TypeError("module operation publication needs compiled inventory and HOST Registration")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("module publication needs its explicit command key")
    for category, declarations in compiled.registrations.items():
        for key, declaration in declarations.items():
            if registration.declaration(category, key) != declaration:
                raise OperationAuthorityError("compiled inventory differs from exact HOST registration")
            if category != "schema":
                registration.resolve(category, key)
            if category == "executor" and registered_operation_contract(key) != declaration:
                raise OperationAuthorityError("compiler HOST executor differs from the execution owner's inventory")
    ports = {port.name: port for port in compiled.ports}
    schemas = {}
    for key in compiled.source.required_schemas:
        ref = schema_refs[key]
        if not isinstance(ref, ResourceVersionRef):
            raise TypeError("compiled schema requires an actual Registry resource reference")
        authority = core.verify_registered_content_schema_ref(ref, schema_document_ref=ref.as_version_ref())
        if authority.schema_id != key:
            raise OperationAuthorityError("compiled schema differs from exact Registry source")
        resource = core.get_version(ref.resource_version_id)
        document = json.loads(core.object_store.read_verified(resource))
        if document != registration.declaration("schema", key)["schema"]:
            raise OperationAuthorityError("registered schema document differs from the exact compiled contract")
        schemas[key] = authority

    inventory = []
    for item in compiled.operations:
        operation = item.declaration

        def typed_port(name, direction):
            port = ports[name]
            declared_minimum = port.cardinality if port.cardinality_minimum is None else port.cardinality_minimum
            declared_maximum = port.cardinality if port.cardinality_maximum is None else port.cardinality_maximum
            if direction == "input":
                minimum, maximum = declared_minimum, declared_maximum
            else:
                products = [{product.port: product for product in outcome.products}.get(name)
                            for outcome in operation.outcomes]
                minimum = min(product.minimum if product is not None else 0 for product in products)
                maximum = max(product.maximum if product is not None else 0 for product in products)
                if maximum > declared_maximum or (
                        port.cardinality_minimum is not None and minimum < declared_minimum):
                    raise OperationAuthorityError("Operation output protocol exceeds declared port cardinality range")
            return OperationPortAuthority(port.port_id, port.place,
                schema_refs[port.schema].as_version_ref(), schemas[port.schema], minimum, maximum)

        prompt_name = operation.request_port
        prompt = ports[prompt_name].port_id if prompt_name is not None else None
        inventory.append(OperationPublication(
            operation.name, item.operation_id, item.executor_key,
            tuple(typed_port(name, "input") for name in operation.inputs),
            tuple(typed_port(name, "output") for name in operation.outputs),
            prompt, tuple(operation.tools), f"{idempotency_key}:operation-spec:{operation.name}"))
    return publish_operation_inventory(core, tuple(inventory))


__all__ = ("OperationPublication", "publish_operation_inventory", "publish_module_operations")
