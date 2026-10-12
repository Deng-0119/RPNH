"""Thin trusted HOST adapters; native gateway and PN own every publication.

Admitted config lookup follows the product default_agent_executor pattern.
Resource publication follows public tool_pipeline.host.AtomicToolExecutor.
The deterministic proposal executor publishes stock TEXT through its own run.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from jsonschema import Draft7Validator
from cpn.rpnh.agent_tasks import TEXT_SCHEMA, TEXT_SCHEMA_DOCUMENT
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from examples.tool_pipeline.contracts import RULES
from examples.tool_pipeline.host import registration as billing_registration, _ports_by_role, resource_ref, STEPS
from examples.tool_pipeline import tools
from .business import DEFAULT_LIMIT, bounded_input, contribution_document

PREFIX = "example/native_billing_collaboration/"
CONFIG = "application/native_billing_bound/v1"
CONFIG_DOCUMENT = {"$id": CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False, "required": ["max_integer"],
    "properties": {"max_integer": {"type": "integer", "minimum": 1, "maximum": DEFAULT_LIMIT}}}
PROPOSAL_CONFIG = "application/native_billing_proposal/v1"
PROPOSAL_DOCUMENT = {"$id": PROPOSAL_CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False, "required": ["kind"],
    "properties": {"kind": {"enum": ["usage", "tariff"]}}}
TERMINAL = PREFIX + "proposal_terminal/v1"


def admitted_config(execution, host_context):
    executable = host_context._executable_net
    if executable.net_ref != execution.operation.firing.net_ref:
        raise ValueError("configuration belongs to another admitted net")
    compiled = load_compiled_net(json.loads(executable.declaration.payload))
    transition = next(t for t in compiled.symbolic.transitions if t.name == execution.operation.firing.transition_id)
    operation = next(o for o in compiled.operations if o.declaration.name == transition.operation)
    if (operation.executor_key != execution.operation.spec.executor_key
            or operation.operation_id != execution.operation.spec.operation_id):
        raise ValueError("configuration differs from admitted operation")
    return dict(operation.declaration.config)


def publish(execution, gateway, products, *, outcome, label):
    """Publish only declared outcome products via the original gateway."""
    spec = execution.operation.spec
    ports = {p.port_id: p for p in spec.output_ports}
    bindings = {b.port_id: b for b in execution.operation.operation_binding.output_port_bindings}
    context = execution.operation.canonical.context
    artifacts = []
    for port_id, value in products.items():
        port, binding = ports[port_id], bindings[port_id]
        ref = gateway.publish_bytes(context, PublishResource(
            origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
            payload=canonical_json(value), media_type="application/json", content_schema_ref=port.content_schema_id,
            summary=label, lifetime_ref=context.invocation_ref,
            derived_from=tuple(i.resource_ref for i in execution.operation.inputs),
            descriptors={"output_outcome_id": outcome, "output_port_id": port_id,
                         "place": binding.place, "example": "native_billing_collaboration"},
            idempotency_key=f"native-billing:{execution.operation_execution_lease_ref.version_id}:{port_id}"))
        artifacts.append(gateway.verify_resource(execution.operation.canonical, ref))
    return OperationExecutionResult(tuple(artifacts), selected_outcome_id=outcome)


@dataclass(frozen=True)
class BoundExecutor:
    kind: str
    identity: dict
    contracts: dict

    def __call__(self, *, execution, gateway, resources, host_context):
        del resources
        config = admitted_config(execution, host_context)
        Draft7Validator(CONFIG_DOCUMENT).validate(config)
        step = "check_" + self.kind + "_input"
        ins = _ports_by_role(execution.operation.spec.input_ports, STEPS[step][0])
        outs = _ports_by_role(execution.operation.spec.output_ports, STEPS[step][1])
        delivered = {i.port_id: i for i in execution.operation.inputs}
        if set(delivered) != {p.port_id for p in ins.values()}:
            raise ValueError("input differs from the admitted billing check")
        inputs = {role: {"ref": resource_ref(delivered[p.port_id].resource_ref),
                         "value": json.loads(delivered[p.port_id].artifact.payload)} for role, p in ins.items()}
        result = asyncio.run(gateway.invoke_registered_tool(execution, PREFIX + "bounded_input/v1",
            identity=self.identity, contracts=self.contracts,
            kwargs={"kind": self.kind, "inputs": inputs, "rules": deepcopy(RULES), **config}))
        if type(result) is not tools.ToolResult or result.outcome not in {"complete", "rejected"}:
            raise ValueError("invalid bounded check result")
        expected = {"checked"} if result.outcome == "complete" else {"rejection"}
        if set(result.products) != expected:
            raise ValueError("bounded check returned wrong outcome products")
        parents = [deepcopy(i["ref"]) for i in inputs.values()]
        if any(v.get("parents") != parents or v.get("rules_id") != RULES["id"] for v in result.products.values()):
            raise ValueError("bounded check altered input lineage or billing policy")
        return publish(execution, gateway, {outs[r].port_id: v for r, v in result.products.items()},
                       outcome=result.outcome, label="Synthetic billing per-interval input bound")


def propose(*, execution, gateway, resources, host_context):
    del resources
    config = admitted_config(execution, host_context)
    Draft7Validator(PROPOSAL_DOCUMENT).validate(config)
    delivered, = execution.operation.inputs
    base = json.loads(json.loads(delivered.artifact.payload))
    document = contribution_document(base, config["kind"])
    port, = execution.operation.spec.output_ports
    # Stock TEXT is a JSON string, not raw JSON bytes mislabeled as TEXT.
    return publish(execution, gateway, {port.port_id: canonical_json(document).decode("utf-8")},
                   outcome="complete", label="Deterministic independent billing proposal")


def registration():
    selected = billing_registration()
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (Path(__file__), Path(__file__).with_name("business.py"))}
    identity = {"implementation_id": "native_billing.bounded_input", "revision": "v1", "source_sha256": hashes}
    contracts = {"binding_protocol": "example/native_billing_bounded_input/v1",
        "returns": "coroutine[ToolResult]", "effect": "pure", "rules": deepcopy(RULES),
        "admitted_config_schema": CONFIG}
    selected.register_schema(CONFIG, CONFIG_DOCUMENT)
    selected.register_schema(PROPOSAL_CONFIG, PROPOSAL_DOCUMENT)
    selected.register_schema(TEXT_SCHEMA, TEXT_SCHEMA_DOCUMENT)
    selected.register_tool(PREFIX + "bounded_input/v1", bounded_input, identity=identity, contracts=contracts)
    for kind in ("usage", "tariff"):
        selected.register_executor(PREFIX + "check_" + kind + "/v1", BoundExecutor(kind, identity, contracts),
            identity={"implementation_id": "native_billing.bound_executor", "revision": "v1", "kind": kind,
                      "source_sha256": hashes},
            contracts={"transport": "deterministic", "input_ports": None, "output_ports": None, "config_schema": CONFIG})
    selected.register_executor(PREFIX + "propose/v1", propose,
        identity={"implementation_id": "native_billing.propose", "revision": "v1", "source_sha256": hashes},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None, "config_schema": PROPOSAL_CONFIG})
    selected.register_tool(TERMINAL, dict,
        identity={"implementation_id": "native_billing.proposal_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return selected
