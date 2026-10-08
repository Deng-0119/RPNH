"""Small trusted HOST assembly; execution order comes only from the adopted PN."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from .contracts import RULES, SCHEMAS, schema_id
from . import tools

TERMINAL = "example/tool_pipeline/terminal/v1"
ASYNC_ABI = "example/tool_pipeline/async_atomic_tool/v1"
# Role -> content schema is the explicit executor ABI. No order-based port guesses.
STEPS = {
    "read_usage": ({"source": "source_usage"}, {"raw": "usage_raw", "validation_source": "usage_validation_source"}),
    "read_tariff": ({"source": "source_tariff"}, {"raw": "tariff_raw", "validation_source": "tariff_validation_source"}),
    "check_usage_input": ({"raw": "usage_raw"}, {"checked": "usage_checked", "rejection": "rejection"}),
    "check_tariff_input": ({"raw": "tariff_raw"}, {"checked": "tariff_checked", "rejection": "rejection"}),
    "normalize_usage": ({"checked": "usage_checked"}, {"normalized": "usage_normalized"}),
    "normalize_tariff": ({"checked": "tariff_checked"}, {"normalized": "tariff_normalized"}),
    "join_intervals": ({"usage": "usage_normalized", "tariff": "tariff_normalized"}, {"joined": "joined", "rejection": "rejection"}),
    "compute_cost": ({"joined": "joined"}, {"candidate": "candidate"}),
    "validate_report": ({"candidate": "candidate", "usage_source": "usage_validation_source", "tariff_source": "tariff_validation_source"},
                        {"validated": "validated", "rejection": "rejection"}),
    "publish_report": ({"validated": "validated"}, {"final": "final"}),
}


def tool_key(step):
    return f"example/tool_pipeline/{step}/v1"


def executor_key(step):
    return f"example/tool_pipeline/execute_{step}/v1"


def resource_ref(ref):
    return {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}


def _ports_by_role(ports, roles):
    expected = {schema_id(value) for value in roles.values()}
    by_schema = {port.content_schema_id: port for port in ports}
    if len(by_schema) != len(ports) or by_schema.keys() != expected:
        raise ValueError("admitted ports differ from atomic tool ABI")
    return {role: by_schema[schema_id(value)] for role, value in roles.items()}


@dataclass(frozen=True)
class AtomicToolExecutor:
    step: str
    identity: dict
    contracts: dict

    def __call__(self, *, execution, gateway, resources, host_context):
        del resources, host_context  # No ambient reads and no dispatcher internals.
        expected_inputs, expected_outputs = STEPS[self.step]
        in_ports = _ports_by_role(execution.operation.spec.input_ports, expected_inputs)
        out_ports = _ports_by_role(execution.operation.spec.output_ports, expected_outputs)
        delivered = {item.port_id: item for item in execution.operation.inputs}
        if set(delivered) != {port.port_id for port in in_ports.values()}:
            raise ValueError("input delivery differs from exact declared ports")
        inputs = {role: {"ref": resource_ref(delivered[port.port_id].resource_ref),
                         "value": json.loads(delivered[port.port_id].artifact.payload)}
                  for role, port in in_ports.items()}
        parent_refs = copy.deepcopy([item["ref"] for item in inputs.values()])
        # This call performs actual existing-tool admission checks on the owner.
        # An async function's body starts ONLY when awaited in this worker.
        pending = gateway.invoke_registered_tool(execution, tool_key(self.step),
            identity=self.identity, contracts=self.contracts,
            kwargs={"inputs": inputs, "rules": copy.deepcopy(self.contracts["rules"])})
        if not inspect.iscoroutine(pending):
            raise TypeError("async atomic HOST tool must return one coroutine, never a callable")
        result = asyncio.run(pending)
        if type(result) is not tools.ToolResult or result.outcome not in {"complete", "rejected"}:
            raise TypeError("atomic HOST tool returned an invalid typed product bundle")
        expected = ({"rejection"} if result.outcome == "rejected" else set(expected_outputs) - {"rejection"})
        if set(result.products) != expected or not expected.issubset(out_ports):
            raise ValueError("atomic tool output ports differ from selected outcome")
        bindings = {b.port_id: b for b in execution.operation.operation_binding.output_port_bindings}
        context = execution.operation.canonical.context
        artifacts = []
        for role, value in result.products.items():
            if (not isinstance(value, dict) or value.get("parents") != parent_refs
                    or value.get("rules_id") != self.contracts["rules"]["id"]):
                raise ValueError("tool output provenance differs from exact delivered inputs or policy")
            port = out_ports[role]
            binding = bindings[port.port_id]
            ref = gateway.publish_bytes(context, PublishResource(
                origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
                payload=canonical_json(value), media_type="application/json",
                content_schema_ref=port.content_schema_id,
                summary=f"Synthetic billing: {self.step}/{role}", lifetime_ref=context.invocation_ref,
                derived_from=tuple(item.resource_ref for item in execution.operation.inputs),
                descriptors={"output_outcome_id": result.outcome, "output_port_id": port.port_id,
                             "place": binding.place, "tool_key": tool_key(self.step)},
                idempotency_key=f"tool-pipeline:{execution.operation_execution_lease_ref.version_id}:{role}"))
            artifacts.append(gateway.verify_resource(execution.operation.canonical, ref))
        return OperationExecutionResult(tuple(artifacts), selected_outcome_id=result.outcome)


def registration(*, test_identity=None):
    """Owner-explicit trust. Optional label identifies an injected test assembly."""
    result = Registration()
    register_basic_components(result)
    for key, body in SCHEMAS.items():
        result.register_schema(key, body)
    root = Path(__file__).parent
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in ("contracts.py", "tools.py", "host.py")}
    for step, function in tools.TOOLS.items():
        if not inspect.iscoroutinefunction(function):
            raise TypeError("only explicitly registered async atomic tools are supported")
        identity = {"implementation_id": "tool_pipeline." + step, "revision": "v1",
                    "source_sha256": hashes,
                    "handler_sha256": hashlib.sha256(inspect.getsource(function).encode()).hexdigest()}
        if test_identity is not None:
            identity["test_identity"] = test_identity
        contracts = {"binding_protocol": ASYNC_ABI, "returns": "coroutine[ToolResult]",
                     "effect": "pure", "rules": copy.deepcopy(RULES),
                     "inputs": {k: schema_id(v) for k, v in STEPS[step][0].items()},
                     "outputs": {k: schema_id(v) for k, v in STEPS[step][1].items()}}
        result.register_tool(tool_key(step), function, identity=identity, contracts=contracts)
        result.register_executor(executor_key(step), AtomicToolExecutor(step, identity, contracts),
            identity={"implementation_id": "tool_pipeline.atomic_executor", "revision": "v1",
                      "step": step, "source_sha256": hashes["host.py"]},
            contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                       "config_schema": CONFIG_SCHEMA_ID})
    result.register_tool(TERMINAL, dict,
        identity={"implementation_id": "tool_pipeline.terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return result
