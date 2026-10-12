"""Ordinary registered executors; no money, provider, scheduler or side ledger."""
import json

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import (PNFragment, PlaceDeclaration, TransitionDeclaration,
    ArcDeclaration, PortBinding, PortDeclaration)
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from .contracts import POLICY, ORDER, SCHEMAS, TERMINAL, executor_key, schema_id


def lower_gate(config, context):
    """Required-one external port is private, initially empty and producer-free."""
    operation = context.operations[0]
    external = "request" if "request" in operation.inputs else "decision"
    internal = PortDeclaration(external, "input", schema_id(external))
    return PNFragment(
        places=tuple(PlaceDeclaration(p.name, p.schema, capacity=1) for p in (*context.ports, internal)),
        transitions=(TransitionDeclaration(operation.name, operation.name),),
        arcs=tuple([ArcDeclaration(p, operation.name, "input") for p in operation.inputs]
                   + [ArcDeclaration(p, operation.name, "output", mode="produce", outcome="complete")
                      for p in operation.outputs]),
        ports=tuple(PortBinding(p.name, p.name) for p in context.ports),
        operations=context.operations, internal_ports=(internal,),
        internal_bindings=(PortBinding(external, external),))


def execute(*, execution, gateway, resources, host_context):
    del resources, host_context
    context = execution.operation.canonical.context
    by_port = {p.port_id: p.content_schema_id for p in execution.operation.spec.input_ports}
    roles = {schema_id(name): name for name in ("policy", "request", "candidate", "decision")}
    inputs = {roles[by_port[item.port_id]]: item for item in execution.operation.inputs}
    values = {key: json.loads(item.artifact.payload) for key, item in inputs.items()}
    stage = execution.operation.firing.transition_id.split(".")[0]
    if stage == "candidate":
        request, policy = values["request"], values["policy"]
        if (policy["policy"] != POLICY or policy["order"] != ORDER or request["case_id"] != policy["case_id"]
                or type(request["amount_minor"]) is not int
                or not 1 <= request["amount_minor"] <= POLICY["max_amount_minor"]
                or request["amount_minor"] > policy["order"]["paid_minor"]
                or request["currency"] != POLICY["currency"] or not policy["order"]["refundable"]):
            raise ValueError("request differs from immutable refund policy")
        value = {**request, "order": policy["order"]["id"], "policy_ref": ref_payload(inputs["policy"].resource_ref.as_version_ref()),
                 "command_ref": ref_payload(inputs["request"].resource_ref.as_version_ref()),
                 "source_refs": [ref_payload(item.resource_ref.as_version_ref()) for item in execution.operation.inputs]}
    elif stage == "refund":
        candidate, decision = values["candidate"], values["decision"]
        candidate_ref = ref_payload(inputs["candidate"].resource_ref.as_version_ref())
        if canonical_json(decision["candidate"]) != canonical_json(candidate) or decision["candidate_ref"] != candidate_ref:
            raise ValueError("decision does not match exact immutable candidate")
        disposition = decision["decision"]
        if disposition not in {"approve", "deny", "expired"}:
            raise ValueError("unrecognized disposition")
        value = {"status": {"approve": "completed", "deny": "denied", "expired": "expired"}[disposition],
                 "mock_refund": disposition == "approve", "case_id": candidate["case_id"],
                 "order": candidate["order"],
                 "amount_minor": candidate["amount_minor"], "currency": candidate["currency"],
                 "target": candidate["target"], "wait_ref": candidate["wait_ref"],
                 "candidate_ref": candidate_ref,
                 "decision_ref": ref_payload(inputs["decision"].resource_ref.as_version_ref())}
    else:
        raise ValueError("unknown operation")
    if (value["target"]["task_ref"] != ref_payload(context.task_ref)
            or value["target"]["net_ref"] != ref_payload(context.net_instance_ref)):
        raise ValueError("business target differs from native execution")
    port = execution.operation.spec.output_ports[0]
    binding = execution.operation.operation_binding.output_port_bindings[0]
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(value), media_type="application/json", content_schema_ref=port.content_schema_id,
        summary="Controlled refund " + stage, lifetime_ref=context.invocation_ref,
        derived_from=tuple(item.resource_ref for item in execution.operation.inputs),
        descriptors={"output_outcome_id": "complete", "output_port_id": port.port_id,
                     "place": binding.place, "approval_stage": stage},
        idempotency_key="approval-output:" + str(execution.operation_execution_lease_ref.version_id)))
    return OperationExecutionResult((gateway.verify_resource(execution.operation.canonical, ref),),
                                    selected_outcome_id="complete")


def registration():
    reg = Registration()
    register_basic_components(reg)
    reg.register_component("example/approval_workflow/gate/v1", lower_gate,
        identity={"implementation_id": "approval_workflow.external_input_gate", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    for key, body in SCHEMAS.items():
        reg.register_schema(key, body)
    for stage in ("candidate", "refund"):
        reg.register_executor(executor_key(stage), execute,
            identity={"implementation_id": "approval_workflow." + stage, "revision": "v1"},
            contracts={"transport": "deterministic", "input_ports": None,
                       "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    reg.register_tool(TERMINAL, dict,
        identity={"implementation_id": "approval_workflow.terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return reg
