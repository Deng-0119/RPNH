"""Optional manually authored one-operation component; no agent/A2C policy."""
from __future__ import annotations

from cpn.rpnh.petri_contracts import (
    ArcDeclaration, DeclarationError, PNFragment, PlaceDeclaration,
    PortBinding, TransitionDeclaration,
)


CONFIG_SCHEMA_ID = "application/operation_component_config/v1"
CONFIG_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": CONFIG_SCHEMA_ID,
    "type": "object", "additionalProperties": False,
    "properties": {
        "input_modes": {"type": "object", "additionalProperties": {
            "enum": ["consume", "read"]}},
        "capacities": {"type": "object", "additionalProperties": {
            "type": "integer", "minimum": 1}},
        "interrupt_returns": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
    },
}


def lower_operation(config, context):
    """Preserve declared interfaces/outcomes, producing a typed PN fragment.

    A component key is a reusable HOST lowerer, not a core node kind. Both human
    and Designer declare the executor, tools, product bundles and symbolic effects.
    """
    if len(context.operations) != 1:
        raise DeclarationError("operation component requires one declared operation")
    operation = context.operations[0]
    ports = {port.name: port for port in context.ports}
    modes = config.get("input_modes", {})
    capacities = config.get("capacities", {})
    interrupt_returns = config.get("interrupt_returns", {})
    if (set(modes) - set(operation.inputs)
            or set(capacities) - ports.keys()):
        raise DeclarationError("component config names undeclared ports")
    if (set(interrupt_returns) - set(operation.outputs)
            or set(interrupt_returns.values()) - set(operation.inputs)
            or len(set(interrupt_returns.values())) != len(interrupt_returns)):
        raise DeclarationError(
            "interrupt returns must map distinct operation outputs to inputs")
    return_targets = set(interrupt_returns)
    places = tuple(PlaceDeclaration(
        name=port.name, schema=port.schema, channel=port.channel,
        capacity=capacities.get(port.name)) for port in context.ports
        if port.name not in return_targets)
    arcs = [ArcDeclaration(
        name, operation.name, "input", ports[name].cardinality,
        modes.get(name, "consume")) for name in operation.inputs]
    for outcome in operation.outcomes:
        arcs.extend(ArcDeclaration(
            product.port, operation.name, "output", product.maximum,
            "produce", outcome.name)
            for product in outcome.products if product.maximum)
    arcs.extend(ArcDeclaration(
        input_name, operation.name, "output", 1, "produce",
        "interrupted", emit="forward", forward_source=input_name)
        for _output_name, input_name in sorted(interrupt_returns.items()))
    bindings = tuple(PortBinding(
        port.name,
        interrupt_returns.get(port.name, port.name),
    ) for port in context.ports)
    return PNFragment(
        places, (TransitionDeclaration(operation.name, operation.name),),
        tuple(arcs), bindings, context.operations)


def register_basic_components(registration):
    """Explicit optional library setup at the HOST composition root."""
    registration.register_schema(CONFIG_SCHEMA_ID, CONFIG_SCHEMA)
    registration.register_component(
        "operation", lower_operation,
        identity={"implementation_id": "typed_operation_component", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})


__all__ = ("register_basic_components", "lower_operation", "CONFIG_SCHEMA_ID")
