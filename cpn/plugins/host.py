"""Native-plugin bridge on the existing Registry owner and Petri dispatcher.

All SDK authors see is api.py. This HOST implementation alone translates their
contributions to admitted operations, exact input deliveries and product closure.
"""
from __future__ import annotations
import base64
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .api import PluginError, ResourceView, canonical, json_copy, validate
from .catalog import PluginCatalog

BINDING_SCHEMA = "application/rpnh_native_plugin_binding/v1"
COMPONENT_KEY = "rpnh/native-plugin-operation/v1"
TERMINAL_KEY = "rpnh/native-plugin-terminal/v1"
CONFIG_SCHEMA = {
    "$id": BINDING_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "properties": {
        "native_plugin": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "selector": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,47}/[a-z][a-z0-9_]{0,47}$"},
                "binding_digest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "wire_mode": {"enum": ["json", "text_json"]},
                "request_port": {"type": "string", "minLength": 1},
                "result_port": {"type": "string", "minLength": 1},
                "capability_port": {"type": "string", "minLength": 1},
                "capability_schema": {"type": "string", "minLength": 1},
                "capability": {"type": "object"},
            },
            "required": ["selector", "binding_digest", "wire_mode", "request_port", "result_port",
                         "capability_port", "capability_schema", "capability"],
        },
    }, "required": ["native_plugin"],
}


def schema_key(catalog, selector, label):
    plugin, op = catalog.resolve(selector)
    return f"application/plugin/{plugin.definition.name}/{op.name}/d{plugin.digest}/{label}/v1"


def capability_value(plugin, op):
    resources = {r.name: r for r in plugin.definition.resources}
    return {"schema_version": "rpnh/plugin_capability/v1", "binding_digest": plugin.digest,
            "plugin": plugin.definition.descriptor(), "operation": op.name,
            "config": json_copy(plugin.config), "environment": list(plugin.environment),
            "assets": [{**resources[name].descriptor(),
                        "base64": base64.b64encode(resources[name].payload).decode("ascii")}
                       for name in op.resources]}


def operation_config(catalog, selector, *, request_port="request", result_port="result",
                     capability_port="capability", wire_mode="json"):
    plugin, op = catalog.resolve(selector)
    return {"native_plugin": {
        "selector": selector, "binding_digest": plugin.digest, "wire_mode": wire_mode,
        "request_port": request_port, "result_port": result_port,
        "capability_port": capability_port,
        "capability_schema": schema_key(catalog, selector, "capability"),
        "capability": capability_value(plugin, op),
    }}


def _operation_descriptor(plugin, op):
    return {"selector": plugin.definition.name + "/" + op.name,
            "binding_digest": plugin.digest, "operation": op.descriptor(),
            "capability_sha256": hashlib.sha256(canonical(capability_value(plugin, op))).hexdigest()}


@dataclass(frozen=True)
class NativePluginExecutor:
    plugin: Any
    operation: Any

    def __call__(self, *, execution, gateway, resources, host_context):
        from cpn.rpnh.registry.operations import OperationExecutionResult
        from cpn.components.registered_operation_dispatcher import RegisteredOperationExecutionBlock
        from .worker import WorkerFailure, execute_worker
        expected = _operation_descriptor(self.plugin, self.operation)
        prepared = gateway.native_plugin_prepare(execution, expected)
        try:
            value = execute_worker(
                self.operation.handler, prepared["packet"],
                environment_names=self.plugin.environment,
                timeout_seconds=self.operation.timeout_seconds,
                cancelled=lambda: gateway.operation_interruption_requested(execution))
            value = validate(self.operation.output_schema, value)
        except (WorkerFailure, PluginError) as exc:
            code = exc.code if isinstance(exc, WorkerFailure) else "output_schema_mismatch"
            authority = gateway.native_plugin_failed(execution, prepared["attempt_ref"], code)
            return RegisteredOperationExecutionBlock(execution, authority)
        artifacts = gateway.native_plugin_products(execution, prepared["attempt_ref"], value)
        return OperationExecutionResult(outputs=artifacts, selected_outcome_id="complete")


def register_plugins(registration, catalog: PluginCatalog):
    """Register selected contributions with no plugin-specific core branches."""
    if not catalog.plugins:
        return
    registration.register_schema(BINDING_SCHEMA, CONFIG_SCHEMA)
    registration.register_component(COMPONENT_KEY, lower_plugin_operation,
        identity={"implementation_id": "rpnh.native_plugin_component", "revision": "v1"},
        contracts={"config_schema": BINDING_SCHEMA})
    registration.register_tool(TERMINAL_KEY, dict,
        identity={"implementation_id": "rpnh.native_plugin_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    for plugin in catalog.plugins:
        for op in plugin.definition.operations:
            selector = plugin.definition.name + "/" + op.name
            schemas = {"input": json_copy(op.input_schema), "output": json_copy(op.output_schema),
                       "capability": {"const": capability_value(plugin, op)}}
            for label, schema in schemas.items():
                key = schema_key(catalog, selector, label)
                registration.register_schema(key, {**schema, "$id": key,
                    "$schema": "http://json-schema.org/draft-07/schema#"})
            registration.register_executor(catalog.operation_key(selector), NativePluginExecutor(plugin, op),
                identity={"implementation_id": "rpnh.native_plugin_executor", "revision": "v1",
                          "plugin_name": plugin.definition.name, "plugin_version": plugin.definition.version,
                          "binding_digest": plugin.digest},
                contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                           "config_schema": BINDING_SCHEMA,
                           "native_plugin": _operation_descriptor(plugin, op)})


def lower_plugin_operation(config, context):
    """One real transition, one capability read token, ordinary typed I/O."""
    from cpn.rpnh.petri_contracts import (
        PNFragment, PlaceDeclaration, PortBinding, PortDeclaration,
        TransitionDeclaration, ArcDeclaration, InitialTokenDeclaration, DeclarationError)
    if len(context.operations) != 1:
        raise DeclarationError("native plugin component needs one operation")
    operation = context.operations[0]
    binding = config["native_plugin"]
    capability_port = binding["capability_port"]
    if (operation.config != config or operation.inputs != ("request", capability_port)
            or operation.outputs != ("result",) or operation.request_port is not None
            or [o.name for o in operation.outcomes] != ["complete"]):
        raise DeclarationError("native plugin operation differs from its declared component ABI")
    ports = {p.name: p for p in context.ports}
    return PNFragment(
        places=(PlaceDeclaration("request", ports["request"].schema),
                PlaceDeclaration("result", ports["result"].schema),
                PlaceDeclaration(capability_port, binding["capability_schema"], capacity=1,
                    initial_tokens=(InitialTokenDeclaration(schema=binding["capability_schema"],
                                                            value=binding["capability"]),))),
        transitions=(TransitionDeclaration(operation.name, operation.name),),
        arcs=(ArcDeclaration("request", operation.name, "input"),
              ArcDeclaration(capability_port, operation.name, "input", mode="read"),
              ArcDeclaration("result", operation.name, "output", mode="produce", outcome="complete")),
        ports=(PortBinding("request", "request"), PortBinding("result", "result")),
        operations=context.operations,
        internal_ports=(PortDeclaration(capability_port, "input", binding["capability_schema"]),),
        internal_bindings=(PortBinding(capability_port, capability_port),))


class NativePluginHost:
    """Sole-owner methods exposed to the trusted executor wrapper, not plugins."""
    def __init__(self, owner, kernel, repository):
        self.owner, self.core, self.kernel, self.repository = owner, owner._core, kernel, repository

    def _binding(self, execution):
        from cpn.rpnh.registry.operation_execution import verify_operation_execution
        from cpn.rpnh.executable_net import load_compiled_net
        from cpn.rpnh.registry.publication import _resource_from_payload
        execution = verify_operation_execution(self.core, self.kernel, self.repository, execution)
        operation = execution.operation
        registered = self.owner.registration.declaration("executor", operation.spec.executor_key)
        contract = registered["contracts"].get("native_plugin")
        if not isinstance(contract, dict):
            raise PluginError("operation is not a registered native plugin")
        executable = self.kernel._exact_object(operation.transition.binding_ref,
                                              expected_type="executable_transition_binding/v1")
        source = _resource_from_payload(executable.metadata["declaration_resource_ref"])
        compiled = load_compiled_net(json.loads(self.kernel._read_firing_registered(operation.canonical.context, source)))
        if compiled.registrations["executor"].get(operation.spec.executor_key) != registered:
            raise PluginError("plugin implementation differs from adopted declaration")
        declaration = next(x.declaration for x in compiled.operations if x.operation_id == operation.spec.operation_id)
        binding = declaration.config.get("native_plugin")
        if (not isinstance(binding, dict) or binding["binding_digest"] != contract["binding_digest"]
                or binding["selector"] != contract["selector"] or binding["wire_mode"] not in {"json", "text_json"}):
            raise PluginError("plugin binding differs from admitted contract")
        names = {p.port_id: p.name for p in compiled.ports}
        inputs = {names[x.port_id]: x for x in operation.inputs}
        def qualify(name):
            prefix = declaration.name.rsplit(".", 1)[0]
            return name if name in inputs or name in declaration.outputs else prefix + "." + name
        request_name, capability_name = qualify(binding["request_port"]), qualify(binding["capability_port"])
        if set(inputs) != {request_name, capability_name}:
            raise PluginError("plugin requires its exact request and capability inputs")
        capability = inputs[capability_name]
        body = json.loads(capability.artifact.payload)
        if hashlib.sha256(canonical(body)).hexdigest() != contract["capability_sha256"]:
            raise PluginError("capability token differs from selected plugin version")
        return execution, contract, binding, inputs[request_name], capability, body, qualify(binding["result_port"])

    def _receipts(self, execution, phase):
        """Read our deterministic receipt in this exact provisional firing.

        Canonical-only rows intentionally omit active invocation resources.
        This is owner-side inspection after verify_operation_execution, not a
        grant to plugins to read another invocation's provisional objects.
        """
        from cpn.rpnh.registry.publication import _stable_id
        from cpn.rpnh.registry.strict_contracts import ref_payload
        lease = str(execution.operation_execution_lease_ref.version_id)
        key = f"native-plugin:{lease}:{phase}"
        row = self.core.event_store.object_row(_stable_id("resource_version", key))
        if row is None:
            return []
        prepared = self.core.get_version(_stable_id("resource_version", key))
        metadata = prepared.metadata
        if (prepared.object_type != "resource_version/v1"
                or str(prepared.logical_id) != str(_stable_id("resource", key))
                or metadata.get("producer_ref") != ref_payload(execution.operation.canonical.context.invocation_ref)
                or metadata.get("lifetime_ref") != ref_payload(execution.operation_execution_lease_ref)
                or metadata.get("descriptors") != {"native_plugin_execution": lease, "phase": phase}):
            raise PluginError("plugin receipt differs from its exact firing provenance")
        body = json.loads(self.core.object_store.read_registered(prepared))
        if (body.get("execution_ref") != ref_payload(execution.operation_execution_lease_ref)
                or body.get("firing_ref") != ref_payload(execution.operation.firing.transition_firing_ref)
                or body.get("phase") != phase):
            raise PluginError("plugin receipt body differs from its exact firing")
        return [row]

    def _receipt(self, execution, phase, value, sources=()):
        from cpn.rpnh.registry.publication import (
            _append_direct_resource_version_publication, _direct_resource_metadata, _stable_id)
        from cpn.rpnh.registry.resources import ResourceVersionRef
        from cpn.rpnh.registry.strict_contracts import ref_payload
        context = execution.operation.canonical.context
        lease = str(execution.operation_execution_lease_ref.version_id)
        key = f"native-plugin:{lease}:{phase}"
        ref = ResourceVersionRef(_stable_id("resource", key), _stable_id("resource_version", key))
        payload = canonical({"schema_version": "rpnh/native_plugin_receipt/v1", "phase": phase,
            "execution_ref": ref_payload(execution.operation_execution_lease_ref),
            "firing_ref": ref_payload(execution.operation.firing.transition_firing_ref), **value})
        tx = self.core.begin(idempotency_key=key, task_round_id=context.task_round_ref.entity_id,
                             net_instance_id=context.net_instance_ref.entity_id)
        def metadata(size):
            document = _direct_resource_metadata(self.core, ref=ref,
                origin_kind="native_plugin_execution", primary=context.operation_execution_lease_ref,
                secondary=context.invocation_ref, task_ref=context.task_ref,
                round_ref=context.task_round_ref, net_ref=context.net_instance_ref,
                producer_ref=context.invocation_ref, lifetime_ref=context.operation_execution_lease_ref,
                operation_binding_ref=context.operation_binding_ref, agent_loop_ref=context.invocation_ref,
                payload_size=size, media_type="application/json", content_schema_ref=None,
                content_schema_authority_ref=None, summary="Native plugin " + phase,
                descriptors={"native_plugin_execution": lease, "phase": phase}, extensions={},
                input_resource_refs=tuple(sources), intended_boundary="not_applicable")
            # This is a generic operation, not a manufactured AgentLoop identity.
            provenance = document["reference_provenance"]
            provenance["invocation_ref"] = provenance.pop("agent_loop_ref")
            return document
        _append_direct_resource_version_publication(tx, ref=ref, payload=payload,
            metadata_factory=metadata,
            media_type="application/json", producer_ref=context.invocation_ref,
            producer_invocation_id=context.invocation_ref.entity_id, relation_key=key,
            direct_owners=(context.operation_binding_ref, context.operation_execution_lease_ref),
            input_resources=tuple(sources))
        tx.commit()
        return ref

    def prepare(self, execution, expected):
        execution, contract, binding, request, capability, body, _result = self._binding(execution)
        if expected != contract:
            raise PluginError("worker implementation differs from registered plugin contract")
        if self._receipts(execution, "started"):
            raise PluginError("this plugin firing was already dispatched; reconciliation is required")
        arguments = json.loads(request.artifact.payload)
        if binding["wire_mode"] == "text_json":
            if not isinstance(arguments, str):
                raise PluginError("workflow plugin input must be serialized JSON text")
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise PluginError("workflow plugin input is not JSON text") from exc
        arguments = validate(contract["operation"]["input_schema"], arguments)
        views = []
        for asset in body["assets"]:
            payload = base64.b64decode(asset["base64"], validate=True)
            if len(payload) != asset["size_bytes"] or hashlib.sha256(payload).hexdigest() != asset["sha256"]:
                raise PluginError("plugin asset integrity mismatch")
            views.append(ResourceView(asset["name"], payload, asset["media_type"],
                                      str(capability.resource_ref.resource_id),
                                      str(capability.resource_ref.resource_version_id)))
        attempt = self._receipt(execution, "started", {"selector": binding["selector"],
                "binding_digest": binding["binding_digest"]}, (request.resource_ref, capability.resource_ref))
        context = execution.operation.canonical.context
        return {"attempt_ref": attempt, "packet": {
            "context": {"config": body["config"], "resources": tuple(views),
                        "operation_id": binding["selector"],
                        "invocation_id": str(context.invocation_ref.version_id),
                        "firing_id": str(execution.operation.firing.transition_firing_ref.version_id),
                        "call_id": str(attempt.resource_version_id)},
            "arguments": arguments, "max_result_bytes": contract["operation"]["max_result_bytes"],
            "implementation": contract["operation"]["implementation"]}}

    def _attempt(self, execution, attempt):
        from cpn.rpnh.registry.resources import ResourceVersionRef
        if not isinstance(attempt, ResourceVersionRef):
            raise PluginError("plugin result requires an exact dispatch receipt")
        rows = self._receipts(execution, "started")
        if (len(rows) != 1 or rows[0]["version_id"] != str(attempt.resource_version_id)
                or rows[0]["logical_id"] != str(attempt.resource_id)):
            raise PluginError("plugin result crossed its exact dispatch")
        if self._receipts(execution, "failed") or self._receipts(execution, "returned"):
            raise PluginError("plugin execution already has a terminal observation")

    def products(self, execution, attempt, value):
        from cpn.rpnh.registry.operation_outputs import publish_operation_products
        from cpn.rpnh.registry.resource_verification import verify_resource
        execution, contract, binding, _request, _capability, _body, result_port = self._binding(execution)
        self._attempt(execution, attempt)
        value = validate(contract["operation"]["output_schema"], value)
        raw = canonical(value)
        if len(raw) > contract["operation"]["max_result_bytes"]:
            raise PluginError("plugin result exceeds the registered limit")
        payload = canonical(raw.decode("utf-8")) if binding["wire_mode"] == "text_json" else raw
        closed = publish_operation_products(self.core, self.kernel, self.repository, execution,
            outcome_id="complete", products={result_port: (payload,)},
            idempotency_key=f"native-plugin:{execution.operation_execution_lease_ref.version_id}:products")
        artifacts = tuple(verify_resource(self.core, self.kernel, execution.operation.canonical, x.resource_ref)
                          for x in closed.outputs)
        self._receipt(execution, "returned", {"selector": binding["selector"]},
                      (attempt, *(x.header.ref for x in artifacts)))
        return artifacts

    def failed(self, execution, attempt, code):
        from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
        execution, contract, binding, *_ = self._binding(execution)
        self._attempt(execution, attempt)
        if code not in {"result_limit_exceeded", "handler_failed", "worker_protocol_failed",
                        "cancelled_after_start", "deadline_exceeded", "worker_exited_without_result",
                        "credential_environment_missing", "output_schema_mismatch", "implementation_changed"}:
            raise PluginError("unknown plugin failure code")
        uncertain = contract["operation"]["effect"] == "external_write"
        error = self._receipt(execution, "failed", {"code": code,
            "selector": binding["selector"], "outcome_unknown": uncertain}, (attempt,))
        return OperationExecutionBlockAuthority(execution,
            "submission_reconciliation" if uncertain else "framework_repair",
            binding["selector"], code, None, "native_plugin", 1, error.as_version_ref(), None)
