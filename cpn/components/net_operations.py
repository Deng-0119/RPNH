"""Explicit native Petri-net definition operations.

Installing this library only registers deterministic HOST capabilities.  It
does not expose them to an Agent tool list and never adopts a produced Module.
Applications opt in by declaring the component, executor, ports and outcome in
their own Module.
"""

from __future__ import annotations

import json

from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import (
    ComposeConnection,
    ComposePlan,
    ExtractPlan,
    branch_module,
    compose_modules,
    extract_module,
    instantiate_module,
)
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json

CONFIG_SCHEMA_ID = "rpnh/net_operation_config/v1"
COMPONENT_KEY = "rpnh/net-definition-operation/v1"
EXECUTOR_KEY = "rpnh/net-definition-executor/v1"
MODULE_SCHEMA_ID = "rpnh/module_declaration/v1"


_SELECTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"enum": ["whole_module", "components"]},
        "components": {
            "type": "array", "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "boundary_policy": {"const": "preserve_all_dependencies"},
        "output_name": {"type": ["string", "null"]},
    },
    "required": ["kind", "components", "boundary_policy", "output_name"],
    "allOf": [
        {"if": {"properties": {"kind": {"const": "whole_module"}}},
         "then": {"properties": {"components": {"maxItems": 0}}}},
        {"if": {"properties": {"kind": {"const": "components"}}},
         "then": {"properties": {"components": {"minItems": 1}}}},
    ],
}


CONFIG_SCHEMA = {
    "$id": CONFIG_SCHEMA_ID,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"enum": ["extract", "compose", "instantiate", "branch"]},
        "selection": _SELECTION,
        "source_order": {
            "type": "array", "items": {"type": "string", "minLength": 1},
            "minItems": 1, "uniqueItems": True,
        },
        "instances": {
            "type": "object", "minProperties": 1,
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "compose": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "terminal_instance": {"type": "string", "minLength": 1},
                "mode": {"enum": ["explicit", "serial", "parallel"]},
                "connections": {
                    "type": "array",
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "source_instance": {"type": "string", "minLength": 1},
                            "source_exit": {"type": "string", "minLength": 1},
                            "target_instance": {"type": "string", "minLength": 1},
                            "target_entry": {"type": "string", "minLength": 1},
                        },
                        "required": ["source_instance", "source_exit", "target_instance", "target_entry"],
                    },
                },
            },
            "required": ["name", "terminal_instance", "mode", "connections"],
        },
        "instance": {"type": ["string", "null"], "minLength": 1},
        "output_name": {"type": ["string", "null"]},
        "output_mode": {"enum": ["definition_only", "new_instance"]},
    },
    "required": ["kind", "source_order"],
    "allOf": [
        {"if": {"properties": {"kind": {"const": "extract"}}},
         "then": {"required": ["selection"]}},
        {"if": {"properties": {"kind": {"const": "compose"}}},
         "then": {"required": ["instances", "compose"]}},
        {"if": {"properties": {"kind": {"const": "instantiate"}}},
         "then": {"required": ["instance", "output_name"]}},
        {"if": {"properties": {"kind": {"const": "branch"}}},
         "then": {
             "required": ["selection", "output_mode", "instance"],
             "allOf": [
                 {"if": {"properties": {"output_mode": {"const": "definition_only"}}},
                  "then": {"properties": {"instance": {"type": "null"}}}},
                 {"if": {"properties": {"output_mode": {"const": "new_instance"}}},
                  "then": {"properties": {"instance": {
                      "type": "string", "minLength": 1}}}},
             ],
         }},
    ],
}


_CONFIG_KEYS = {
    "extract": frozenset(("kind", "source_order", "selection")),
    "compose": frozenset(("kind", "source_order", "instances", "compose")),
    "instantiate": frozenset(("kind", "source_order", "instance", "output_name")),
    "branch": frozenset(
        ("kind", "source_order", "selection", "output_mode", "instance")),
}


def _validate_config_shape(config):
    kind = config.get("kind") if isinstance(config, dict) else None
    expected = _CONFIG_KEYS.get(kind)
    if expected is None:
        raise ValueError("unknown net definition operation")
    if frozenset(config) != expected:
        raise ValueError(
            f"{kind} config requires exact fields: " + ", ".join(sorted(expected)))
    if kind == "instantiate" and not isinstance(config["instance"], str):
        raise ValueError("instantiate requires a nonempty instance")
    if kind == "branch":
        mode, instance = config["output_mode"], config["instance"]
        if ((mode == "definition_only" and instance is not None)
                or (mode == "new_instance" and not isinstance(instance, str))):
            raise ValueError("branch instance must match output_mode")


def _validate_config_semantics(config):
    """Reject every value that would otherwise fail only after admission."""
    _validate_config_shape(config)
    source_order = tuple(config["source_order"])
    if not source_order or len(set(source_order)) != len(source_order):
        raise ValueError("source_order requires unique source ports")
    kind = config["kind"]
    if kind in {"extract", "branch"}:
        if len(source_order) != 1:
            raise ValueError(f"{kind} requires one source definition")
        selection = _extract_plan(config["selection"])
        if kind == "branch":
            if config["output_mode"] == "new_instance":
                from cpn.rpnh.petri_contracts import symbol
                symbol(config["instance"])
            # Exercise the public branch option relationship without needing
            # an input ModuleDeclaration.
            if (config["output_mode"] == "definition_only"
                    and config["instance"] is not None):
                raise ValueError(
                    "definition_only branch does not accept an instance name")
        return selection
    if kind == "instantiate":
        if len(source_order) != 1:
            raise ValueError("instantiate requires one source definition")
        from cpn.rpnh.petri_contracts import symbol
        symbol(config["instance"])
        if config["output_name"] is not None:
            symbol(config["output_name"])
        return None
    instance_ports = config["instances"]
    if set(instance_ports) != set(source_order):
        raise ValueError("compose instances must cover exact source ports")
    instance_names = tuple(instance_ports[port] for port in source_order)
    if len(set(instance_names)) != len(instance_names):
        raise ValueError("compose instance names must be unique")
    from cpn.rpnh.petri_contracts import symbol
    for instance in instance_names:
        symbol(instance)
    spec = config["compose"]
    plan = ComposePlan(
        name=spec["name"], terminal_instance=spec["terminal_instance"],
        mode=spec["mode"],
        connections=tuple(ComposeConnection(**item)
                          for item in spec["connections"]),
    )
    if plan.terminal_instance not in instance_names:
        raise ValueError("terminal_instance is not present")
    return plan


def _extract_plan(value):
    return ExtractPlan(
        kind=value["kind"],
        components=tuple(value["components"]),
        boundary_policy=value["boundary_policy"],
        output_name=value["output_name"],
    )


def _lower(config, context):
    from cpn.rpnh.petri_contracts import (
        ArcDeclaration, DeclarationError, InitialTokenDeclaration, PNFragment,
        PlaceDeclaration, PortBinding, PortDeclaration, TransitionDeclaration,
    )

    try:
        _validate_config_semantics(config)
    except (TypeError, ValueError) as exc:
        raise DeclarationError(str(exc)) from exc
    if len(context.operations) != 1:
        raise DeclarationError("net definition component requires one operation")
    operation = context.operations[0]
    capability = "net_operation_config"
    if (operation.executor != EXECUTOR_KEY or operation.config != config
            or operation.tools or operation.request_port is not None
            or len(operation.outputs) != 1
            or operation.inputs != (*config["source_order"], capability)
            or len(operation.outcomes) != 1
            or operation.outcomes[0].name != "complete"
            or len(operation.outcomes[0].products) != 1
            or operation.outcomes[0].products[0].port != operation.outputs[0]
            or operation.outcomes[0].products[0].minimum != 1
            or operation.outcomes[0].products[0].maximum != 1
            or operation.outcomes[0].effects):
        raise DeclarationError("net definition operation differs from its exact ABI")
    ports = {port.name: port for port in context.ports}
    public_names = (*config["source_order"], *operation.outputs)
    if set(ports) != set(public_names):
        raise DeclarationError(
            "net definition operation requires exact declared public ports")
    for name in public_names:
        port = ports[name]
        expected_direction = (
            "input" if name in config["source_order"] else "output")
        if (port.schema != MODULE_SCHEMA_ID or port.direction != expected_direction
                or port.channel != "data" or port.minimum != 1
                or port.maximum != 1):
            raise DeclarationError(
                "net definition operation requires data ModuleDeclaration ports with exact cardinality 1")
    places = tuple(PlaceDeclaration(port.name, port.schema, port.channel)
                   for port in context.ports) + (
        PlaceDeclaration(capability, CONFIG_SCHEMA_ID, capacity=1,
            initial_tokens=(InitialTokenDeclaration(
                schema=CONFIG_SCHEMA_ID, value=config),)),
    )
    arcs = tuple(ArcDeclaration(name, operation.name, "input")
                 for name in config["source_order"]) + (
        ArcDeclaration(capability, operation.name, "input", mode="read"),
        ArcDeclaration(operation.outputs[0], operation.name, "output",
                       mode="produce", outcome="complete"),
    )
    return PNFragment(
        places=places,
        transitions=(TransitionDeclaration(operation.name, operation.name),),
        arcs=arcs,
        ports=tuple(PortBinding(port.name, port.name) for port in context.ports),
        operations=context.operations,
        internal_ports=(PortDeclaration(capability, "input", CONFIG_SCHEMA_ID),),
        internal_bindings=(PortBinding(capability, capability),),
    )


def _transform(config, values):
    _validate_config_semantics(config)
    kind = config["kind"]
    if kind == "extract":
        if len(values) != 1:
            raise ValueError("extract requires one source definition")
        return extract_module(values[0], _extract_plan(config["selection"])).module
    if kind == "compose":
        instance_ports = config["instances"]
        if set(instance_ports) != set(config["source_order"]):
            raise ValueError("compose instances must cover exact source ports")
        instances = {
            instance_ports[port]: value
            for port, value in zip(config["source_order"], values, strict=True)
        }
        if len(instances) != len(values):
            raise ValueError("compose instance names must be unique")
        spec = config["compose"]
        return compose_modules(instances, ComposePlan(
            name=spec["name"], terminal_instance=spec["terminal_instance"],
            mode=spec["mode"],
            connections=tuple(ComposeConnection(**item)
                              for item in spec["connections"]),
        ))
    if kind == "instantiate":
        if len(values) != 1:
            raise ValueError("instantiate requires one source definition")
        return instantiate_module(values[0], instance=config["instance"],
                                  output_name=config["output_name"])
    if kind == "branch":
        if len(values) != 1:
            raise ValueError("branch requires one source definition")
        result = branch_module(
            values[0], _extract_plan(config["selection"]),
            output_mode=config["output_mode"], instance=config["instance"])
        return result.definition if result.instance is None else result.instance
    raise ValueError("unknown net definition operation")


def _executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    from cpn.rpnh.registry.operations import OperationExecutionResult

    spec_ports = {port.port_id: port for port in execution.operation.spec.input_ports}
    delivered = {item.port_id: item for item in execution.operation.inputs}
    if set(delivered) != set(spec_ports):
        raise ValueError("net operation input delivery differs from exact spec")
    capability_ids = [port_id for port_id, port in spec_ports.items()
                      if port.content_schema_id == CONFIG_SCHEMA_ID]
    if len(capability_ids) != 1:
        raise ValueError("net operation lacks one exact config capability")
    config = json.loads(delivered[capability_ids[0]].artifact.payload)
    source_order = config["source_order"]
    source_ids = [port.port_id for port in execution.operation.spec.input_ports
                  if port.content_schema_id == MODULE_SCHEMA_ID]
    if len(source_order) != len(source_ids):
        raise ValueError("net operation input inventory differs from its config")
    values = [ModuleDeclaration.from_json(
        delivered[port_id].artifact.payload.decode("utf-8"))
        for port_id in source_ids]
    result = _transform(config, values)
    bindings = tuple(execution.operation.operation_binding.output_port_bindings)
    if len(bindings) != 1 or len(execution.operation.spec.output_ports) != 1:
        raise ValueError("net operation requires one exact output binding")
    binding = bindings[0]
    port = execution.operation.spec.output_ports[0]
    if binding.port_id != port.port_id or port.content_schema_id != MODULE_SCHEMA_ID:
        raise ValueError("net operation output differs from ModuleDeclaration ABI")
    context = execution.operation.canonical.context
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(result.to_dict()),
        media_type="application/json",
        content_schema_ref=MODULE_SCHEMA_ID,
        summary=f"Native Petri-net {config['kind']} result",
        lifetime_ref=context.invocation_ref,
        descriptors={
            "output_outcome_id": "complete",
            "output_port_id": binding.port_id,
            "place": binding.place,
            "net_operation_kind": config["kind"],
        },
        idempotency_key=(
            "net-operation:"
            + str(execution.operation_execution_lease_ref.version_id)
            + ":product"),
    ))
    artifact = gateway.verify_resource(execution.operation.canonical, ref)
    return OperationExecutionResult(outputs=(artifact,), selected_outcome_id="complete")


def register_net_components(registration) -> None:
    """Install the opt-in pure definition component and deterministic executor."""
    registration.register_schema(CONFIG_SCHEMA_ID, CONFIG_SCHEMA)
    registration.register_component(
        COMPONENT_KEY, _lower,
        identity={"implementation_id": "rpnh.net_definition_operation", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID},
    )
    registration.register_executor(
        EXECUTOR_KEY, _executor,
        identity={"implementation_id": "rpnh.net_definition_executor", "revision": "v1"},
        contracts={
            "transport": "deterministic",
            "input_ports": None,
            "output_ports": None,
            "config_schema": CONFIG_SCHEMA_ID,
        },
    )


__all__ = (
    "COMPONENT_KEY", "CONFIG_SCHEMA", "CONFIG_SCHEMA_ID", "EXECUTOR_KEY",
    "MODULE_SCHEMA_ID", "register_net_components",
)
