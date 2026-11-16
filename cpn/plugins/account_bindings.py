"""Explicit account metadata through original publication, Success and inputs.

This module constructs ordinary declared PN products. Account-use authority
stays with InstalledAccountResolver.current_use_check at dispatch, and this
publisher cannot make visible resources into acknowledged operation inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from .api import canonical, json_copy
from .account_credentials import AccountCredentialError, AccountSelection, _text


BINDING_SCHEMA = "rpnh/stdio_account_binding/v1"
PLAN_SCHEMA = "rpnh/stdio_account_plan/v1"
PUBLICATION_CONFIG_SCHEMA = "rpnh/stdio_account_publication_config/v1"
PUBLISHER = "account_bindings"
PUBLISHER_TRANSITION = PUBLISHER + ".publish"
PUBLISHER_EXECUTOR = "rpnh/account-binding-publication/v1"


def account_binding_schema_data():
    """Explicit optional HOST schema/source inventory, without a new authority."""
    from cpn.rpnh.collaboration.schema_catalog import source_identity_schema_data
    schemas, types, paths = source_identity_schema_data()
    root = Path(__file__).resolve().parents[1] / "schemas" / "rpnh"
    from .account_provider_catalog import PROVIDER_SCHEMA
    for schema_id in (BINDING_SCHEMA, PLAN_SCHEMA, PROVIDER_SCHEMA, PUBLICATION_CONFIG_SCHEMA):
        path = root / (schema_id.split("/")[1] + ".v1.schema.json")
        schemas[schema_id] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema_id] = path
    # The original compiler validates executor config in isolation. Compose
    # self-contained definitions from the two canonical contracts; no network
    # reference resolution or second handwritten provider contract is needed.
    from .account_provider_catalog import AccountProviderSelection
    definitions = {"plan": json_copy(schemas[PLAN_SCHEMA]),
                   "provider": AccountProviderSelection.schema_document()}
    for document in definitions.values():
        document.pop("$id", None)
        document.pop("$schema", None)
    if schemas[PUBLICATION_CONFIG_SCHEMA].get("definitions") != definitions:
        raise ValueError("account publication schema definitions differ from the source contracts")
    return schemas, types, paths


def register_account_binding_catalog(catalog):
    """Compose the optional schemas/types into this original HOST catalog."""
    schemas, types, paths = account_binding_schema_data()
    for schema_id, document in schemas.items():
        catalog.register_schema(schema_id, document, source_path=paths.get(schema_id))
    for definition in types:
        catalog.register_type(definition)
    return catalog


@dataclass(frozen=True, slots=True)
class AccountBindingRequest:
    node_id: str
    tool_name: str
    service_id: str
    account_id: str
    binding_revision: str
    slot: str = "github_pat/v1"

    def __post_init__(self):
        if any(type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value) is None
               for value in (self.node_id, self.tool_name)):
            raise ValueError("account request requires exact node and tool symbols")
        for value in (self.service_id, self.account_id, self.binding_revision):
            _text(value)
        if self.slot != "github_pat/v1":
            raise ValueError("unsupported installed account slot")

    def to_dict(self):
        return {name: getattr(self, name) for name in (
            "node_id", "tool_name", "service_id", "account_id", "binding_revision", "slot")}

    def matches(self, selection):
        return type(selection) is AccountSelection and all(
            getattr(selection, name) == getattr(self, name)
            for name in ("service_id", "account_id", "binding_revision", "slot"))


@dataclass(frozen=True, slots=True)
class AccountBindingPlan:
    source_id: str
    bindings: tuple[AccountBindingRequest, ...]

    def __post_init__(self):
        from jsonschema import Draft7Validator
        _text(self.source_id)
        if (type(self.bindings) is not tuple or not self.bindings
                or any(type(row) is not AccountBindingRequest for row in self.bindings)):
            raise TypeError("account plan requires immutable typed fixed requests")
        keys = tuple((row.node_id, row.tool_name) for row in self.bindings)
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
            raise ValueError("account plan requires unique requests in node/tool order")
        schemas, _, _ = account_binding_schema_data()
        Draft7Validator(schemas[PLAN_SCHEMA]).validate(self.to_dict())
        for row in self.bindings:
            for name in ("service_id", "account_id", "binding_revision"):
                _text(getattr(row, name))

    def to_dict(self):
        return {"schema_version": PLAN_SCHEMA, "source_id": self.source_id,
                "bindings": [row.to_dict() for row in self.bindings]}

    @classmethod
    def from_dict(cls, value):
        from jsonschema import Draft7Validator
        value = json_copy(value)
        schemas, _, _ = account_binding_schema_data()
        Draft7Validator(schemas[PLAN_SCHEMA]).validate(value)
        return cls(value["source_id"], tuple(sorted(
            (AccountBindingRequest(**row) for row in value["bindings"]),
            key=lambda row: (row.node_id, row.tool_name))))

    def require_catalogs(self, catalogs):
        """Validate selected tools only; this grants neither account nor PN use."""
        from .managed_tools import ManagedPluginToolCatalog
        if not isinstance(catalogs, Mapping):
            raise TypeError("account plan requires explicit node catalogs")
        requests = {(row.node_id, row.tool_name): row for row in self.bindings}
        for node_id, catalog in catalogs.items():
            if not isinstance(catalog, ManagedPluginToolCatalog):
                raise ValueError("account plan requires actual selected managed catalogs")
            for declaration in catalog.tools:
                _plugin, operation = catalog.binding(declaration.name)
                requirement = operation.account_requirement
                if requirement is None:
                    continue
                request = requests.get((node_id, declaration.name))
                if request is None:
                    raise ValueError("account plan is missing a required tool binding")
                if (request.service_id != requirement["service_id"]
                        or request.slot != requirement["slot"]):
                    raise ValueError("account plan differs from the tool account requirement")
        for request in self.bindings:
            catalog = catalogs.get(request.node_id)
            if not isinstance(catalog, ManagedPluginToolCatalog):
                raise ValueError("account plan names a node without managed tools")
            declaration = catalog.declaration(request.tool_name)
            plugin, _ = catalog.binding(request.tool_name)
            if declaration.protocol_version != "v3" or plugin.environment:
                raise ValueError("account plan requires v3 tools without ambient credentials")


def register_account_binding_support(registration):
    from .account_provider_catalog import PROVIDER_SCHEMA
    schemas, _, _ = account_binding_schema_data()
    for schema_id in (BINDING_SCHEMA, PLAN_SCHEMA, PROVIDER_SCHEMA, PUBLICATION_CONFIG_SCHEMA):
        registration.register_schema(schema_id, schemas[schema_id])
    registration.register_executor(PUBLISHER_EXECUTOR, execute_account_binding_publication,
        identity={"implementation_id": "cpn.plugins.account_binding_publication", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": PUBLICATION_CONFIG_SCHEMA})


def account_publication_config(plan, provider_selection=None):
    """Pin the explicit provider choice with the original compiled publisher."""
    from .account_provider_catalog import AccountProviderSelection
    if type(plan) is not AccountBindingPlan:
        raise TypeError("account publication requires a typed plan")
    if provider_selection is not None:
        if type(provider_selection) is not AccountProviderSelection:
            provider_selection = AccountProviderSelection.from_dict(provider_selection)
        provider_selection = provider_selection.to_dict()
    return {"schema_version": PUBLICATION_CONFIG_SCHEMA, "plan": plan.to_dict(),
            "provider_selection": provider_selection}


def with_account_binding_inputs(module, plan, *, consumers=None, provider_selection=None):
    """Add one declared publisher and real reusable read inputs to this Module.

    Generic one-operation components use their original input_modes contract.
    Workflow v5 attaches a node's inputs to its initial and rework activations.
    DSH's explicit component lowerer handles the same binding ports. Unknown
    component lowering remains rejected rather than silently omitting the input.
    """
    from cpn.components.basic import CONFIG_SCHEMA_ID
    from cpn.rpnh.module import ModuleDeclaration
    if type(module) is not ModuleDeclaration or type(plan) is not AccountBindingPlan:
        raise TypeError("account input composition requires typed Module and plan")
    document = module.to_dict()
    components = {item["name"]: item for item in document["components"]}
    if PUBLISHER in components or PUBLISHER in document["entry"]:
        raise ValueError("account publisher identity is already used")
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowGraph, WORKFLOW_GRAPH_COMPONENT_V3_KEY,
        WORKFLOW_GRAPH_COMPONENT_V4_KEY, WORKFLOW_GRAPH_COMPONENT_V5_KEY,
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V5_ID,
    )
    graph_consumers = {}
    requested_nodes = {request.node_id for request in plan.bindings}
    for component in document["components"]:
        if component["key"] not in {
                WORKFLOW_GRAPH_COMPONENT_V3_KEY, WORKFLOW_GRAPH_COMPONENT_V4_KEY}:
            continue
        graph = AgentWorkflowGraph.from_mapping(component["config"])
        feedback_nodes = {arc.target.node_id for arc in graph.arcs if arc.kind == "feedback"}
        for node in graph.nodes:
            if node.node_id not in requested_nodes:
                continue
            if node.node_id in graph_consumers:
                raise ValueError("account node has ambiguous workflow components")
            graph_consumers[node.node_id] = (component, node,
                (node.node_id, *((f"{node.node_id}__rework",)
                                if node.node_id in feedback_nodes else ())))
    consumers = None if consumers is None else dict(consumers)
    outputs = []
    for ordinal, request in enumerate(plan.bindings):
        component = components.get(request.node_id)
        graph_consumer = graph_consumers.get(request.node_id)
        if (component is not None and graph_consumer is not None
                and component is not graph_consumer[0]):
            raise ValueError("account node has ambiguous consumer components")
        if graph_consumer is not None:
            if consumers is not None:
                raise ValueError("workflow account activations cannot be overridden")
            component, node, names = graph_consumer
            if node.execution.plugin is not None:
                raise ValueError("account plan requires a managed workflow node")
            if component["key"] != WORKFLOW_GRAPH_COMPONENT_V5_KEY:
                component["key"] = WORKFLOW_GRAPH_COMPONENT_V5_KEY
                component["config_schema"] = WORKFLOW_GRAPH_CONFIG_SCHEMA_V5_ID
                component["config"]["account_inputs"] = []
                document["required_schemas"].append(WORKFLOW_GRAPH_CONFIG_SCHEMA_V5_ID)
        else:
            if component is None or consumers is not None and request.node_id not in consumers:
                raise ValueError("account plan has no exact consumer component")
            names = (consumers[request.node_id] if consumers is not None else "run",)
        operations = [op for op in component["operations"] if op["name"] in names]
        if len(operations) != len(names) or {op["name"] for op in operations} != set(names):
            raise ValueError("account plan has no exact consumer operation")
        port = f"account_binding_{ordinal}"
        if any(existing["name"] == port for existing in component["ports"]):
            raise ValueError("account input port identity is already used")
        component["ports"].append({"name": port, "direction": "input", "schema": BINDING_SCHEMA})
        for operation in operations:
            operation["inputs"].append(port)
        if component["key"] == "operation":
            component["config"].setdefault("input_modes", {})[port] = "read"
        elif component["key"] == WORKFLOW_GRAPH_COMPONENT_V5_KEY:
            component["config"]["account_inputs"].append({
                "node_id": request.node_id, "port_id": port})
        elif component["key"] != "rpnh/dsh-driver/v1":
            raise ValueError("account input composition does not support this component")
        output = f"binding_{ordinal}"
        outputs.append(output)
        document["links"].append({"source": {"component": PUBLISHER, "port": output},
                                  "target": {"component": component["name"], "port": port}})
    bucket = {"bucket_id": PUBLISHER, "budget_scope": PUBLISHER, "finalization_scope": None}
    document["components"].append({"name": PUBLISHER, "key": "operation",
        "config_schema": CONFIG_SCHEMA_ID, "config": {},
        "ports": [{"name": "request", "direction": "input", "schema": PLAN_SCHEMA},
                  *({"name": name, "direction": "output", "schema": BINDING_SCHEMA} for name in outputs)],
        "operations": [{"name": "publish", "executor": PUBLISHER_EXECUTOR,
            "inputs": ["request"], "outputs": outputs, "request_port": None, "tools": [],
            "config": account_publication_config(plan, provider_selection), "outcomes": [{"name": "complete",
                "products": [{"port": name} for name in outputs]}], "budget_binding": bucket}]})
    document["entry"][PUBLISHER] = {"component": PUBLISHER, "port": "request"}
    document["required_schemas"] = list(dict.fromkeys(
        [*document["required_schemas"], CONFIG_SCHEMA_ID, PLAN_SCHEMA, BINDING_SCHEMA,
         PUBLICATION_CONFIG_SCHEMA, "rpnh/installed_account_provider/v1"]))
    document["budget_buckets"].append({**bucket, "max_attempts": 1})
    return ModuleDeclaration.from_dict(document)


def account_binding_input(plan):
    from cpn.rpnh.run import OwnerInput
    if type(plan) is not AccountBindingPlan:
        raise TypeError("account input requires a typed plan")
    return OwnerInput(PLAN_SCHEMA, canonical(plan.to_dict()), "Explicit non-secret account selections")


def _binding_documents(execution, plan):
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    operation = execution.operation
    if (operation.spec.executor_key != PUBLISHER_EXECUTOR or len(operation.inputs) != 1
            or operation.inputs[0].projected_payload is not None
            or operation.inputs[0].source_resource_ref is not None
            or canonical(json.loads(operation.inputs[0].consumer_payload)) != canonical(plan.to_dict())):
        raise AccountCredentialError("account_binding_input_missing")
    context = operation.canonical.context
    return tuple({"schema_version": BINDING_SCHEMA,
        "principal_ref": SourceQualifiedVersionRef(plan.source_id, context.principal_ref).to_dict(),
        "task_ref": SourceQualifiedVersionRef(plan.source_id, context.task_ref).to_dict(),
        **{name: getattr(request, name) for name in ("service_id", "account_id", "binding_revision", "slot")}}
        for request in plan.bindings)


def execute_account_binding_publication(*, execution, gateway, resources, host_context):
    """Ordinary registered executor; publication is followed by original Success."""
    from cpn.rpnh.executable_net import load_compiled_net
    from cpn.rpnh.registry.operations import OperationExecutionResult
    from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
    del resources
    compiled = load_compiled_net(json.loads(host_context._executable_net.declaration.payload))
    declared, = [item for item in compiled.operations if item.operation_id == execution.operation.spec.operation_id]
    plan = AccountBindingPlan.from_dict(declared.declaration.config["plan"])
    documents = _binding_documents(execution, plan)
    context = execution.operation.canonical.context
    products = []
    for ordinal, document in enumerate(documents):
        place = compiled.symbolic.port_places[f"{PUBLISHER}.binding_{ordinal}"]
        binding, = [item for item in execution.operation.operation_binding.output_port_bindings if item.place == place]
        port, = [item for item in execution.operation.spec.output_ports if item.port_id == binding.port_id]
        ref = gateway.publish_bytes(context, PublishResource(
            origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
            payload=canonical(document), media_type="application/json", content_schema_ref=port.content_schema_id,
            summary="Fixed non-secret account binding", lifetime_ref=context.invocation_ref,
            descriptors={"output_outcome_id": "complete", "output_port_id": binding.port_id, "place": place},
            idempotency_key=f"account-binding:{execution.operation_execution_lease_ref.version_id}:{ordinal}"))
        products.append(gateway.verify_resource(execution.operation.canonical, ref))
    return OperationExecutionResult(outputs=tuple(products), selected_outcome_id="complete")


def _require_publication_config(structure, plan, provider_selection):
    declared = [item for item in structure.compiled.operations
                if item.declaration.name == PUBLISHER_TRANSITION]
    if plan is None:
        if declared or provider_selection is not None:
            raise AccountCredentialError("account_binding_unavailable")
        return
    expected = account_publication_config(plan, provider_selection)
    if (len(declared) != 1 or declared[0].executor_key != PUBLISHER_EXECUTOR
            or canonical(declared[0].declaration.config) != canonical(expected)):
        raise AccountCredentialError("account_binding_unavailable")


def verify_account_binding_plan(run_dir, catalog, plan, provider_selection=None):
    """Read original compiled identity before acquiring a resumed run writer.

    Even dropping a previously selected plan is rejected here. Reading the
    Registry does not invoke the factory, read credentials or append authority.
    ``prepare_account_bindings`` repeats the same comparison on the real owner.
    """
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    if plan is not None and type(plan) is not AccountBindingPlan:
        raise TypeError("account plan verification requires the typed plan")
    core = _RegistryCore(run_dir, create=False, read_only=True,
                         catalog=register_account_binding_catalog(catalog))
    _, structure, _ = hydrate_module_runtime(core)
    _require_publication_config(structure, plan, provider_selection)


def prepare_account_bindings(owner, plan, *, provider_selection=None):
    """Drive the declared original owner prelude, then return exact live products.

    On ordinary resume the persistent read-input tokens select the same resource
    versions without republishing. An unfinished/ambiguous prelude is rejected
    for original recovery; this helper never manufactures a replacement token.
    """
    from cpn.rpnh.run import RunOwner
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
    if not isinstance(owner, RunOwner) or type(plan) is not AccountBindingPlan:
        raise TypeError("account binding prelude requires its original owner and typed plan")
    _executable, structure, marking = hydrate_module_runtime(owner._core)
    _require_publication_config(structure, plan, provider_selection)
    owner.schema_gateway.bind_source_identity(source_id=plan.source_id, command_id="managed-account:source")
    places = tuple(structure.compiled.symbolic.port_places[f"{PUBLISHER}.binding_{ordinal}"]
                   for ordinal in range(len(plan.bindings)))

    def current_refs(current_marking):
        matches = [tuple(token.state.resource_ref for token in current_marking.tokens
                         if token.state.place == place and token.state.resource_ref is not None)
                   for place in places]
        if any(len(rows) > 1 for rows in matches):
            raise AccountCredentialError("account_binding_unavailable")
        return tuple(rows[0] if rows else None for rows in matches)

    refs = current_refs(marking)
    if not any(refs):
        admitted = owner.admit(PUBLISHER_TRANSITION, logical_tau=0, command_id="managed-account:admit")
        if admitted is None:
            raise AccountCredentialError("account_binding_unavailable")
        execution = owner.start(admitted, command_id="managed-account:start")
        documents = _binding_documents(execution, plan)
        outputs = owner.products(execution, outcome_id="complete", products={
            f"{PUBLISHER}.binding_{ordinal}": (canonical(document),)
            for ordinal, document in enumerate(documents)}, command_id="managed-account:products")
        owner.succeed(outputs, command_id="managed-account:success")
        _, _, marking = hydrate_module_runtime(owner._core)
        refs = current_refs(marking)
    if not all(refs):
        raise AccountCredentialError("account_binding_unavailable")
    result = {}
    for request, ref in zip(plan.bindings, refs):
        selection = AccountSelection(
            SourceQualifiedVersionRef(plan.source_id, owner.principal_ref),
            SourceQualifiedVersionRef(plan.source_id, owner.identity.task_ref),
            SourceQualifiedResourceRef(plan.source_id, ref), request.service_id, request.account_id,
            request.binding_revision, request.slot)
        raw = owner._core.object_store.read_registered(owner._core.get_version(ref.resource_version_id))
        if canonical(json.loads(raw)) != canonical(selection.binding_document()):
            raise AccountCredentialError("account_binding_unavailable")
        result.setdefault(request.node_id, {})[request.tool_name] = selection
    return MappingProxyType({node: MappingProxyType(values) for node, values in result.items()})


__all__ = ("BINDING_SCHEMA", "PLAN_SCHEMA", "PUBLICATION_CONFIG_SCHEMA", "PUBLISHER", "PUBLISHER_TRANSITION", "PUBLISHER_EXECUTOR",
           "AccountBindingRequest", "AccountBindingPlan", "account_binding_schema_data",
           "register_account_binding_catalog", "register_account_binding_support", "with_account_binding_inputs", "account_binding_input",
           "account_publication_config", "verify_account_binding_plan", "prepare_account_bindings")
