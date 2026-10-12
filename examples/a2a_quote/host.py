"""Ordinary registered mock quote; only the original PN admits execution."""
import json
from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import (PNFragment, PlaceDeclaration, TransitionDeclaration,
    ArcDeclaration, PortBinding, PortDeclaration)
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from .contracts import GATE, EXECUTOR, TERMINAL, SCHEMAS, STAGE, STAGE2, schema_id, evidence


def lower_gate(config, context):
    operation = context.operations[0]
    private = PortDeclaration("evidence", "input", schema_id("evidence"))
    return PNFragment(
        places=tuple(PlaceDeclaration(p.name, p.schema, capacity=1) for p in (*context.ports, private)),
        transitions=(TransitionDeclaration(operation.name, operation.name),),
        arcs=tuple([ArcDeclaration(p, operation.name, "input") for p in operation.inputs]
                   + [ArcDeclaration("result", operation.name, "output", mode="produce", outcome="complete")]),
        ports=tuple(PortBinding(p.name, p.name) for p in context.ports), operations=context.operations,
        internal_ports=(private,), internal_bindings=(PortBinding("evidence", "evidence"),))


def execute(*, execution, gateway, resources, host_context):
    del resources, host_context
    context = execution.operation.canonical.context
    # Compiled IDs are opaque: resolve their semantic roles through the spec.
    roles = {p.port_id: p.content_schema_id for p in execution.operation.spec.input_ports}
    inputs = {roles[item.port_id]: item for item in execution.operation.inputs}
    request_input, proof_input = inputs[schema_id("request")], inputs[schema_id("evidence")]
    request, proof = json.loads(request_input.artifact.payload), json.loads(proof_input.artifact.payload)
    association = proof["association"]
    if (association["task_ref"] != ref_payload(context.task_ref)
            or association["net_ref"] != ref_payload(context.net_instance_ref)
            or association["request_resource_ref"] != ref_payload(request_input.resource_ref.as_version_ref())
            or any(association[k] != request[k] for k in (
                "assistant_task_id", "assistant_context_id", "merchant_task_id", "merchant_context_id"))
            or canonical_json(proof) != canonical_json(evidence(association, proof["fixture"]))):
        raise ValueError("evidence differs from the exact native request or fixture contract")
    discount = proof["discount_percent"]
    value = {"status": "completed", "evidence_level": STAGE, "stage2": STAGE2,
        "association": association, "evidence_ref": ref_payload(proof_input.resource_ref.as_version_ref()),
        "verdict": proof["verdict"], "discount_percent": discount, "base_amount_minor": 20000,
        "amount_minor": 20000 * (100 - discount) // 100, "currency": "USD",
        "hotel": request["hotel"], "booking": False, "payment": False}
    port = execution.operation.spec.output_ports[0]
    binding = execution.operation.operation_binding.output_port_bindings[0]
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(value), media_type="application/json", content_schema_ref=port.content_schema_id,
        summary="Synthetic hotel quote; no cryptographic verification", lifetime_ref=context.invocation_ref,
        derived_from=tuple(i.resource_ref for i in execution.operation.inputs),
        descriptors={"output_outcome_id": "complete", "output_port_id": port.port_id,
                     "place": binding.place, "a2a_stage": "quote"},
        idempotency_key="a2a-quote:" + str(execution.operation_execution_lease_ref.version_id)))
    return OperationExecutionResult((gateway.verify_resource(execution.operation.canonical, ref),),
                                    selected_outcome_id="complete")


def registration():
    reg = Registration()
    register_basic_components(reg)
    reg.register_component(GATE, lower_gate,
        identity={"implementation_id": "a2a_quote.external_input_gate", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    for key, body in SCHEMAS.items():
        reg.register_schema(key, body)
    reg.register_executor(EXECUTOR, execute,
        identity={"implementation_id": "a2a_quote.quote", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    reg.register_tool(TERMINAL, dict,
        identity={"implementation_id": "a2a_quote.terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return reg
