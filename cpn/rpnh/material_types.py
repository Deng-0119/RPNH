"""Exact material codecs over the existing Agent text product contract.

These helpers prepare bytes and an ordinary ``PublishResource`` command. They
 do not grant access, publish resources, move addresses, or settle a firing.
The live Registry must revalidate the supplied execution and every publication
against its original operation, schema, and address authority.
"""
from __future__ import annotations

import json

from .agent_workflows import TEXT_SCHEMA as AGENT_TEXT_SCHEMA
from .registry.operations import OperationExecutionAuthority
from .registry.resources import (
    AddressBindingIntent,
    PetriOutputOrigin,
    PublishResource,
    ResourceAddress,
    ResourceAddressBindingRef,
    ResourceVersionRef,
)
from .registry.schema_catalog import canonical_json


def encode_agent_text(value: str) -> bytes:
    """Encode one complete literal text value with the original JSON schema.

    A value that itself looks like JSON remains literal text. In particular,
    quotation marks are never stripped from a GUI or trusted HOST input.
    """
    if not isinstance(value, str):
        raise TypeError("Agent text material requires one complete string")
    return canonical_json(value)


def decode_agent_text(
        payload: bytes, *, content_schema_ref: str, media_type: str,
) -> str:
    """Read exact existing Agent text; unrelated JSON is not editable text."""
    if content_schema_ref != AGENT_TEXT_SCHEMA or media_type != "application/json":
        raise ValueError("material is outside the existing Agent text contract")
    if not isinstance(payload, bytes):
        raise TypeError("Agent text payload must be immutable bytes")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, str):
        raise ValueError("Agent text payload must contain a JSON string")
    return value


def agent_text_workspace_bytes(value: str) -> bytes:
    """Encode the separate UTF-8 materialization used by the text producer."""
    if not isinstance(value, str):
        raise TypeError("Agent text material requires one complete string")
    return value.encode("utf-8")


def agent_text_publication(
        execution: OperationExecutionAuthority, *, output_port_id: str,
        outcome_id: str, value: str, address: ResourceAddress,
        expected_binding_ref: ResourceAddressBindingRef | None,
        base_resource_ref: ResourceVersionRef | None, summary: str,
        idempotency_key: str,
) -> PublishResource:
    """Prepare one original typed output at an explicitly selected task address.

    The address must already be selected/validated by the owner command path.
    This first adapter supports only an exact current task scope; historical
    invocation and activation addresses do not become writable by changing
    their displayed owner. The operation binding supplies the real producer,
    output binding, and schema source. An edit records ``derived_from`` without
    claiming the original producer's logical resource identity.

    The original output registrar still validates the declared outcome bundle;
    the Registry still validates current authority and expected address state.
    Returning this command does not establish successful materialization or
    publication and must never be treated as a write receipt.
    """
    if not isinstance(execution, OperationExecutionAuthority):
        raise TypeError("material publication requires original operation execution")
    if (not isinstance(output_port_id, str) or not output_port_id
            or not isinstance(outcome_id, str) or not outcome_id
            or not isinstance(idempotency_key, str) or not idempotency_key):
        raise ValueError("material publication requires exact port, outcome, and key")
    if not isinstance(summary, str):
        raise TypeError("material summary must be text")
    context = execution.operation.canonical.context
    if (not isinstance(address, ResourceAddress)
            or address.scope_ref != context.task_ref
            or not isinstance(address.opaque_name, str)
            or not address.opaque_name or "\x00" in address.opaque_name):
        raise ValueError("material address requires an explicit current task scope")
    if (expected_binding_ref is not None
            and not isinstance(expected_binding_ref, ResourceAddressBindingRef)):
        raise TypeError("expected address binding must be an exact binding or null")
    if (base_resource_ref is not None
            and not isinstance(base_resource_ref, ResourceVersionRef)):
        raise TypeError("base material must be an exact resource version or null")
    bindings = tuple(item for item in
        execution.operation.operation_binding.output_port_bindings
        if item.port_id == output_port_id)
    if len(bindings) != 1:
        raise ValueError("material requires one exact declared output port")
    binding, = bindings
    if binding.content_schema.schema_id != AGENT_TEXT_SCHEMA:
        raise ValueError("output port is outside the existing Agent text contract")
    return PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=encode_agent_text(value), media_type="application/json",
        content_schema_ref=AGENT_TEXT_SCHEMA,
        content_schema_authority_ref=binding.content_schema.content_schema_ref,
        summary=summary, lifetime_ref=context.invocation_ref,
        address_bindings=(AddressBindingIntent(
            address, context.operation_binding_ref, expected_binding_ref),),
        derived_from=(() if base_resource_ref is None else (base_resource_ref,)),
        descriptors={"output_outcome_id": outcome_id,
            "output_port_id": binding.port_id, "place": binding.place},
        idempotency_key=idempotency_key,
    )


__all__ = (
    "AGENT_TEXT_SCHEMA", "encode_agent_text", "decode_agent_text",
    "agent_text_workspace_bytes", "agent_text_publication",
)
