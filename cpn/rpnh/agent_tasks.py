"""Independent single-agent and Designer-authored graph task objects.

Every invocation creates one ordinary RPNH Module/Registry lineage.  A one
stage declaration is a single-agent task.  A workflow is an explicit graph
with typed ports, arcs, ingress and egress.  This module
composes the existing Harness, optional AgentLoop component and owner event
loop.  It owns no second scheduler or Registry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import threading
from typing import Any, Mapping, Sequence

from cpn.components.agent_loop.optional_execution import (
    OPTIONAL_TOOL_BINDINGS,
    optional_agent_loop_schema_data,
)
from cpn.components.agent_loop.optional_host_bindings import (
    make_optional_agent_host_bindings,
)
from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.components.executors import execute_default_operation
from cpn.components.execution_services import ExecutionServices
from cpn.llm_adapters import (
    build_llm_input_port,
    load_llm_execution_selection,
)
from cpn.rpnh.agent_workflows import (
    AgentWorkflowGraph,
    WORKFLOW_GRAPH_COMPONENT_V4_KEY, WORKFLOW_GRAPH_CONFIG_SCHEMA_V4, WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID,
    TEXT_SCHEMA,
    WORKFLOW_GRAPH_COMPONENT_KEY,
    WORKFLOW_GRAPH_COMPONENT_V1_KEY,
    WORKFLOW_GRAPH_COMPONENT_V3_KEY,
    WORKFLOW_GRAPH_CONFIG_SCHEMA,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_ID,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V1,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V3,
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID,
    build_agent_workflow_module,
    lower_agent_workflow_graph,
)
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import (
    _resource_from_payload,
    _version_from_payload,
)
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, resume_run, start_run


EXECUTOR_KEY = "rpnh/default-agent/v1"
TERMINAL_KEY = "rpnh/agent-task-terminal/v1"
_STAGE_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
TEXT_SCHEMA_DOCUMENT = {
    "$id": TEXT_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "string",
}


@dataclass(frozen=True, slots=True)
class AgentStage:
    """One explicitly named agent operation in a task object."""

    stage_id: str
    instruction: str

    def __post_init__(self) -> None:
        if (not isinstance(self.stage_id, str)
                or _STAGE_ID.fullmatch(self.stage_id) is None):
            raise ValueError(
                "stage_id must match [a-z][a-z0-9_]{0,47}")
        if (not isinstance(self.instruction, str)
                or not self.instruction.strip()
                or self.instruction != self.instruction.strip()):
            raise ValueError("stage instruction must be nonempty trimmed text")


@dataclass(frozen=True, slots=True)
class AgentTaskSpec:
    """Complete input needed to launch one independent task object."""

    run_dir: Path
    prompt: str
    stages: tuple[AgentStage, ...]
    execution_config_path: Path
    workflow_graph: AgentWorkflowGraph | None = None
    max_attempts_per_stage: int = 12
    max_parallel_nodes: int = 4
    execution_profiles: tuple[tuple[str, Path], ...] = ()
    owner_statement: str = "RPNH user-authorized agent task"
    plugin_configuration: Mapping[str, Any] | None = None
    plugin_catalog_digest: str | None = None
    owner_socket_path: Path | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.run_dir, Path)
                or not isinstance(self.execution_config_path, Path)):
            raise TypeError("agent task paths require pathlib.Path")
        object.__setattr__(self, "run_dir", self.run_dir.resolve())
        object.__setattr__(
            self, "execution_config_path", self.execution_config_path.resolve())
        if self.owner_socket_path is not None:
            if not isinstance(self.owner_socket_path, Path):
                raise TypeError("owner_socket_path requires pathlib.Path or None")
            object.__setattr__(
                self, "owner_socket_path", self.owner_socket_path.resolve())
        if (not isinstance(self.prompt, str) or not self.prompt.strip()):
            raise ValueError("agent task prompt must be nonempty text")
        if (not isinstance(self.stages, tuple)
                or any(not isinstance(stage, AgentStage)
                       for stage in self.stages)):
            raise ValueError("agent task stages must be typed")
        if len({stage.stage_id for stage in self.stages}) != len(self.stages):
            raise ValueError("agent task stage IDs must be unique")
        if self.workflow_graph is None:
            if len(self.stages) != 1:
                raise ValueError(
                    "single-agent task requires exactly one AgentStage")
        elif (not isinstance(self.workflow_graph, AgentWorkflowGraph)
              or self.stages):
            raise ValueError(
                "workflow task requires one graph and no serial stages")
        if (isinstance(self.max_attempts_per_stage, bool)
                or not isinstance(self.max_attempts_per_stage, int)
                or self.max_attempts_per_stage < 1):
            raise ValueError("max_attempts_per_stage must be positive")
        if (isinstance(self.max_parallel_nodes, bool)
                or not isinstance(self.max_parallel_nodes, int)
                or self.max_parallel_nodes < 1):
            raise ValueError("max_parallel_nodes must be positive")
        if self.workflow_graph is None and self.max_parallel_nodes != 1:
            object.__setattr__(self, "max_parallel_nodes", 1)
        if (not isinstance(self.execution_profiles, tuple)
                or any(not isinstance(item, tuple) or len(item) != 2
                       or not isinstance(item[0], str)
                       or _STAGE_ID.fullmatch(item[0]) is None
                       or item[0] == "default"
                       or not isinstance(item[1], Path)
                       for item in self.execution_profiles)
                or len({item[0] for item in self.execution_profiles})
                != len(self.execution_profiles)):
            raise ValueError(
                "execution_profiles require unique non-default IDs and Paths")
        object.__setattr__(self, "execution_profiles", tuple(sorted(
            ((profile_id, path.resolve())
             for profile_id, path in self.execution_profiles),
            key=lambda item: item[0])))
        if self.workflow_graph is not None:
            declared_profiles = {
                "default",
                *(profile_id for profile_id, _path
                  in self.execution_profiles),
            }
            requested_profiles = {
                node.execution.profile_id
                for node in self.workflow_graph.nodes
                if node.execution.profile_id is not None}
            missing = requested_profiles - declared_profiles
            if missing:
                raise ValueError(
                    "workflow references undeclared execution profiles: "
                    + ", ".join(sorted(missing)))
        if (not isinstance(self.owner_statement, str)
                or not self.owner_statement.strip()):
            raise ValueError("owner_statement must be nonempty text")
        if self.plugin_configuration is None:
            if self.plugin_catalog_digest is not None:
                raise ValueError("plugin digest requires its exact owner configuration")
        else:
            from cpn.plugins.api import frozen
            if (not isinstance(self.plugin_configuration, Mapping)
                    or not isinstance(self.plugin_catalog_digest, str)
                    or re.fullmatch(r"[a-f0-9]{64}", self.plugin_catalog_digest) is None):
                raise ValueError("plugin configuration requires its exact compiled catalog digest")
            object.__setattr__(self, "plugin_configuration", frozen(self.plugin_configuration))

    @property
    def kind(self) -> str:
        return "workflow" if self.workflow_graph is not None else "single_agent"

    def as_worker_document(self) -> dict[str, Any]:
        from cpn.plugins.api import json_copy
        return {
            "schema_version": "rpnh/agent_task_spec/v5",
            **({"plugin_configuration": json_copy(self.plugin_configuration),
                "plugin_catalog_digest": self.plugin_catalog_digest} if self.plugin_configuration is not None else {}),
            "run_dir": str(self.run_dir),
            "prompt": self.prompt,
            "stages": [
                {"stage_id": stage.stage_id,
                 "instruction": stage.instruction}
                for stage in self.stages
            ],
            "workflow_graph": (
                None if self.workflow_graph is None
                else self.workflow_graph.to_dict()),
            "execution_config_path": str(self.execution_config_path),
            "max_attempts_per_stage": self.max_attempts_per_stage,
            "max_parallel_nodes": self.max_parallel_nodes,
            "execution_profiles": {
                profile_id: str(path)
                for profile_id, path in self.execution_profiles
            },
            "owner_statement": self.owner_statement,
            "owner_socket_path": (
                None if self.owner_socket_path is None
                else str(self.owner_socket_path)),
        }

    @classmethod
    def from_worker_document(cls, value: object) -> "AgentTaskSpec":
        fields_v1 = {
            "schema_version", "run_dir", "prompt", "stages",
            "execution_config_path", "max_attempts_per_stage",
            "owner_statement",
        }
        fields_v2 = {*fields_v1, "workflow_graph"}
        fields_v3 = {*fields_v2, "max_parallel_nodes", "execution_profiles"}
        # Independent pre-merge branches both emitted v4 with different shapes.
        # Accept exactly one historical shape, never an ambiguous hybrid.
        fields_v4_plugins = {*fields_v3, "plugin_configuration", "plugin_catalog_digest"}
        fields_v4_main = {*fields_v3, "owner_socket_path"}
        fields_v4 = (
            fields_v4_main if isinstance(value, Mapping)
            and set(value) == fields_v4_main else fields_v4_plugins)
        fields_v5 = {
            "schema_version", "run_dir", "prompt", "stages", "workflow_graph",
            "execution_config_path", "max_attempts_per_stage",
            "max_parallel_nodes", "execution_profiles", "owner_statement",
            "owner_socket_path",
        }
        plugin_fields = {"plugin_configuration", "plugin_catalog_digest"}
        version = value.get("schema_version") if isinstance(value, Mapping) else None
        expected_fields = (
            fields_v1 if version == "rpnh/agent_task_spec/v1" else
            fields_v2 if version == "rpnh/agent_task_spec/v2" else
            fields_v3 if version == "rpnh/agent_task_spec/v3" else
            fields_v4 if version == "rpnh/agent_task_spec/v4" else
            fields_v5 | (plugin_fields if set(value) & plugin_fields else set())
            if version == "rpnh/agent_task_spec/v5" else set())
        invalid_owner_socket_path = (
            isinstance(value, Mapping)
            and version in {"rpnh/agent_task_spec/v4", "rpnh/agent_task_spec/v5"}
            and value.get("owner_socket_path") is not None
            and not isinstance(value.get("owner_socket_path"), str))
        if (not isinstance(value, Mapping)
                or set(value) != expected_fields
                or not isinstance(value.get("stages"), list)
                or (version in {"rpnh/agent_task_spec/v3", "rpnh/agent_task_spec/v4", "rpnh/agent_task_spec/v5"}
                    and not isinstance(value.get("execution_profiles"), Mapping))
                or invalid_owner_socket_path):
            raise ValueError("agent task worker document is not current")
        stages = tuple(AgentStage(**stage) for stage in value["stages"])
        graph_value = value.get("workflow_graph")
        return cls(
            run_dir=Path(value["run_dir"]),
            prompt=value["prompt"],
            stages=stages,
            execution_config_path=Path(value["execution_config_path"]),
            workflow_graph=(
                None if graph_value is None
                else AgentWorkflowGraph.from_mapping(graph_value)),
            max_attempts_per_stage=value["max_attempts_per_stage"],
            max_parallel_nodes=value.get("max_parallel_nodes", 1),
            execution_profiles=tuple(
                (profile_id, Path(path))
                for profile_id, path in value.get(
                    "execution_profiles", {}).items()),
            owner_statement=value["owner_statement"],
            plugin_configuration=value.get("plugin_configuration"),
            plugin_catalog_digest=value.get("plugin_catalog_digest"),
            owner_socket_path=(
                None if value.get("owner_socket_path") is None
                else Path(value["owner_socket_path"])),
        )


def agent_task_registration(plugin_catalog=None) -> Registration:
    """Register the optional generic agent component without Current imports."""
    registration = Registration()
    register_basic_components(registration)
    schemas, _types = optional_agent_loop_schema_data()
    for key, schema in schemas.items():
        registration.register_schema(key, schema)
    for key, (implementation, data) in OPTIONAL_TOOL_BINDINGS.items():
        registration.register_tool(key, implementation, **data)
    registration.register_schema(TEXT_SCHEMA, TEXT_SCHEMA_DOCUMENT)
    registration.register_schema(
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID,
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V1)
    registration.register_schema(
        WORKFLOW_GRAPH_CONFIG_SCHEMA_ID, WORKFLOW_GRAPH_CONFIG_SCHEMA)
    registration.register_schema(
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3)
    registration.register_component(
        WORKFLOW_GRAPH_COMPONENT_V1_KEY,
        lower_agent_workflow_graph,
        identity={
            "implementation_id": "rpnh.agent_workflow_graph",
            "revision": "v1",
        },
        contracts={"config_schema": WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID},
    )
    registration.register_component(
        WORKFLOW_GRAPH_COMPONENT_KEY,
        lower_agent_workflow_graph,
        identity={
            "implementation_id": "rpnh.agent_workflow_graph",
            "revision": "v2",
        },
        contracts={"config_schema": WORKFLOW_GRAPH_CONFIG_SCHEMA_ID},
    )
    registration.register_component(
        WORKFLOW_GRAPH_COMPONENT_V3_KEY,
        lower_agent_workflow_graph,
        identity={
            "implementation_id": "rpnh.agent_workflow_graph",
            "revision": "v3",
        },
        contracts={"config_schema": WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID},
    )
    registration.register_schema(WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID, WORKFLOW_GRAPH_CONFIG_SCHEMA_V4)
    registration.register_component(WORKFLOW_GRAPH_COMPONENT_V4_KEY, lower_agent_workflow_graph,
        identity={"implementation_id": "rpnh.agent_workflow_graph", "revision": "v4"},
        contracts={"config_schema": WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID})
    if plugin_catalog is not None:
        from cpn.plugins.host import register_plugins
        register_plugins(registration, plugin_catalog)
    registration.register_executor(
        EXECUTOR_KEY,
        execute_default_operation,
        identity={
            "implementation_id": "components.execute_default_operation",
            "revision": "v1",
        },
        contracts={
            "transport": "llm",
            "input_ports": None,
            "output_ports": None,
            "resource_read_contracts": [
                ResourceReadContract(
                    metadata_only=False,
                    context_origins=("petri_operation",),
                    origin_kinds=("provider_request",),
                    require_producer_invocation=True,
                    require_provenance_binding=True,
                ).to_dict()
            ],
            "provider_request_schema": (
                "registry_v1/logical_provider_request_recipe/v1"),
        },
    )
    registration.register_tool(
        TERMINAL_KEY,
        dict,
        identity={
            "implementation_id": "rpnh.agent_task_terminal",
            "revision": "v1",
        },
        contracts={"binding_protocol": "rpnh/module_terminal/v1"},
    )
    return registration


def agent_task_catalog(plugin_catalog=None) -> SchemaCatalog:
    schemas, types = optional_agent_loop_schema_data()
    extra = {}
    if plugin_catalog is not None:
        from cpn.plugins.runtime import plugin_registration
        extra = {item["key"]: item["schema"] for item in plugin_registration(plugin_catalog).declarations("schema")}
    return SchemaCatalog(schemas={
        **extra,
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID: WORKFLOW_GRAPH_CONFIG_SCHEMA_V4,
        **schemas,
        TEXT_SCHEMA: TEXT_SCHEMA_DOCUMENT,
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID: (
            WORKFLOW_GRAPH_CONFIG_SCHEMA_V1),
        WORKFLOW_GRAPH_CONFIG_SCHEMA_ID: WORKFLOW_GRAPH_CONFIG_SCHEMA,
        WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID: WORKFLOW_GRAPH_CONFIG_SCHEMA_V3,
    }, types=types)


def build_agent_task_module(
        stages: Sequence[AgentStage], *, max_attempts_per_stage: int = 12,
) -> ModuleDeclaration:
    """Build one single-agent typed Petri task.

    Workflows use :func:`build_agent_workflow_module`; accepting multiple
    positional stages here would recreate the deprecated serial workflow path.
    """
    stages = tuple(stages)
    if (len(stages) != 1
            or any(not isinstance(stage, AgentStage) for stage in stages)
            or len({stage.stage_id for stage in stages}) != len(stages)):
        raise ValueError(
            "single-agent Module requires exactly one AgentStage")
    if (isinstance(max_attempts_per_stage, bool)
            or not isinstance(max_attempts_per_stage, int)
            or max_attempts_per_stage < 1):
        raise ValueError("max_attempts_per_stage must be positive")

    buckets = [{
        "bucket_id": stage.stage_id,
        "budget_scope": "module",
        "finalization_scope": None,
        "max_attempts": max_attempts_per_stage,
    } for stage in stages]
    components = []
    for stage, bucket in zip(stages, buckets):
        components.append({
            "name": stage.stage_id,
            "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID,
            "config": {
                "interrupt_returns": {
                    "interrupt_return_request": "request",
                },
            },
            "ports": [
                {"name": "request", "direction": "input",
                 "schema": TEXT_SCHEMA},
                {"name": "result", "direction": "output",
                 "schema": TEXT_SCHEMA},
                {"name": "interrupt_return_request", "direction": "output",
                 "schema": TEXT_SCHEMA},
            ],
            "operations": [{
                "name": "run",
                "executor": EXECUTOR_KEY,
                "inputs": ["request"],
                "outputs": ["result", "interrupt_return_request"],
                "request_port": "request",
                "tools": sorted(OPTIONAL_TOOL_BINDINGS),
                "config": {
                    "provider_attempt_limit": 3,
                    "agent_loop_role": "actor",
                    "node_synopsis": stage.instruction,
                    "resource_bounds": {
                        "max_llm_attempts": max_attempts_per_stage,
                        "max_tool_turns": max_attempts_per_stage,
                    },
                },
                "outcomes": [{
                    "name": "complete",
                    "products": [{"port": "result"}],
                }, {
                    "name": "interrupted",
                    "products": [],
                }],
                "budget_binding": {
                    key: bucket[key] for key in (
                        "bucket_id", "budget_scope", "finalization_scope")
                },
            }],
        })
    links = [{
        "source": {"component": left.stage_id, "port": "result"},
        "target": {"component": right.stage_id, "port": "request"},
    } for left, right in zip(stages, stages[1:])]
    last = stages[-1]
    schemas, _types = optional_agent_loop_schema_data()
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": "SingleAgentTask",
        "components": components,
        "links": links,
        "entry": {
            "request": {"component": stages[0].stage_id, "port": "request"},
        },
        "exit": {
            "result": {"component": last.stage_id, "port": "result"},
        },
        "terminal": {
            "key": TERMINAL_KEY,
            "source": {"component": last.stage_id, "port": "result"},
            "operation": "run",
            "outcome": "complete",
            "config": {"run_outcome": "complete"},
        },
        "required_schemas": [TEXT_SCHEMA, CONFIG_SCHEMA_ID, *schemas],
        "budgets": {},
        "budget_buckets": buckets,
    })


def _execution_route(selection) -> dict[str, Any]:
    provenance = selection.as_registry_policy()
    routes = provenance.get("route_provenance")
    if not isinstance(routes, list) or not routes:
        raise ValueError("execution selection lacks registered route provenance")
    return {
        "schema_version": "optional_agent_execution_provenance/v1",
        "model": selection.input_target.model_condition,
        "backend": selection.adapter_kind,
        "timeout_seconds": selection.timeout_seconds,
        "selection": provenance,
        "transport_kind": routes[0]["transport"],
        "response_protocol": "llm_response_envelope/v1",
    }


def _terminal_output(owner, terminal_ref) -> tuple[object | None, object | None]:
    if terminal_ref is None:
        return None, None
    kernel, _repository = owner.operation_repository()
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    _executable, structure, _marking = hydrate_module_runtime(owner._core)
    document = kernel._exact_object(
        terminal_ref, expected_type="run_terminal_evidence/v1").metadata
    result_ref = _version_from_payload(document["terminal_result_ref"])
    raw = kernel._read_registered(
        ResourceVersionRef(result_ref.entity_id, result_ref.version_id))
    return ref_payload(result_ref), json.loads(raw)


def _validate_resumed_execution_profiles(
        run_dir: Path, selections, *, catalog, registration=None,
) -> dict[str, str]:
    """Fail before dispatch if resume would change an exact provider route.

    HOST bindings are immutable run authority.  Reopening a run therefore
    validates the newly supplied local profile documents against every
    transition's registered target/route instead of silently driving an old
    Registry invocation through a different local adapter.
    """
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel

    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=catalog)
    kernel = _ResourceServiceKernel(core)
    _executable, structure, _marking = hydrate_module_runtime(core)
    bindings = core.event_store.canonical_object_rows(
        object_type="operation_binding/v1")
    nodes = {
        str(row["version_id"]): json.loads(row["metadata_json"])
        for row in core.event_store.canonical_object_rows(
            object_type="node_declaration/v1")
    }
    from cpn.rpnh.registry.resources import ResourceVersionRef

    transition_profiles: dict[str, str] = {}
    for row in bindings:
        binding = json.loads(row["metadata_json"])
        node_ref = binding["node_ref"]
        node = nodes.get(node_ref["version_id"])
        if node is None:
            raise ValueError(
                "resume execution profile lacks its exact node declaration")
        transition_id = node["transition_id"]
        operation_name = next(
            transition.operation
            for transition in structure.compiled.symbolic.transitions
            if transition.name == transition_id)
        operation = next(
            item for item in structure.compiled.operations
            if item.declaration.name == operation_name)
        executor_contract = structure.compiled.registrations["executor"][operation.executor_key]
        if executor_contract["contracts"].get("native_plugin") is not None:
            if (registration is None or registration.declaration("executor", operation.executor_key) != executor_contract):
                raise ValueError("resume native plugin differs from the registered exact implementation")
            continue
        profile_id = (
            operation.declaration.config.get("execution_profile_id")
            or "default")
        previous_profile = transition_profiles.setdefault(
            transition_id, profile_id)
        if previous_profile != profile_id:
            raise ValueError(
                "resume Registry has conflicting transition profile authority")
        if profile_id not in selections:
            raise ValueError(
                "resume execution profile is no longer configured: "
                + profile_id)
        selection = selections[profile_id]

        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        target_payload = json.loads(kernel._read_registered(target_ref))
        if target_payload != selection.input_target.as_registry_document():
            raise ValueError(
                "resume execution profile changed the exact model target for "
                + transition_id)

        static_payloads = {}
        for raw in binding["readable_resource_refs"]:
            ref = _version_from_payload(raw)
            if ref.entity_type != "resource_version/v1":
                continue
            resource_ref = ResourceVersionRef(ref.entity_id, ref.version_id)
            resource = core.get_version(ref.version_id)
            role = resource.metadata.get("descriptors", {}).get(
                "content_role")
            if role in {"optional_agent_backend", "optional_agent_transport"}:
                if role in static_payloads:
                    raise ValueError(
                        "resume execution profile has duplicate route authority")
                static_payloads[role] = json.loads(
                    kernel._read_registered(resource_ref))
        expected = {
            "optional_agent_backend": _execution_route(selection),
            "optional_agent_transport": {
                "interaction_protocol_ref": "llm_request_envelope/v1",
                "response_adapter_ref": "llm_response_envelope/v1",
            },
        }
        if static_payloads != expected:
            raise ValueError(
                "resume execution profile changed the exact provider route for "
                + transition_id)
    expected_transitions = {
        transition.name
        for transition in structure.compiled.symbolic.transitions
        if next(op for op in structure.compiled.operations if op.declaration.name == transition.operation)
            .executor_declaration["contracts"].get("transport") == "llm"}
    if set(transition_profiles) != expected_transitions:
        raise ValueError(
            "resume Registry profile authority differs from its transition inventory")
    return transition_profiles


def _execute_agent_task(
        spec: AgentTaskSpec, *, resume: bool,
) -> dict[str, Any]:
    """Run or resume one task through the owner/Harness/AgentLoop path."""
    if not isinstance(spec, AgentTaskSpec):
        raise TypeError("run_agent_task requires AgentTaskSpec")
    destination = spec.run_dir.resolve()
    if not resume and os.path.lexists(destination):
        raise ValueError("agent task requires an absent run directory")
    if resume and not (
            destination / ".registry_v1" / "registry.sqlite3").is_file():
        raise ValueError("agent task resume requires an existing Registry")
    selection = load_llm_execution_selection(spec.execution_config_path)
    selections = {"default": selection}
    selections.update({
        profile_id: load_llm_execution_selection(path)
        for profile_id, path in spec.execution_profiles
    })
    from cpn.plugins.catalog import load_catalog
    plugin_catalog = load_catalog(spec.plugin_configuration)
    if spec.plugin_catalog_digest is not None and plugin_catalog.digest != spec.plugin_catalog_digest:
        raise ValueError("installed plugin/configuration differs from the pinned task catalog")
    registration = agent_task_registration(plugin_catalog)
    if spec.workflow_graph is None:
        module = build_agent_task_module(
            spec.stages, max_attempts_per_stage=spec.max_attempts_per_stage)
    else:
        schemas, _types = optional_agent_loop_schema_data()
        module = build_agent_workflow_module(
            spec.workflow_graph,
            executor_key=EXECUTOR_KEY,
            terminal_key=TERMINAL_KEY,
            tools=OPTIONAL_TOOL_BINDINGS,
            required_schemas=(CONFIG_SCHEMA_ID, *schemas),
            max_attempts_per_node=spec.max_attempts_per_stage,
            plugin_catalog=plugin_catalog,
        )
    bucket_documents = module.to_dict()["budget_buckets"]
    call_cap = sum(bucket["max_attempts"] for bucket in bucket_documents)
    request = OwnerInput(
        TEXT_SCHEMA, canonical_json(spec.prompt), "RPNH agent task request")
    runner = None
    stop_requested = threading.Event()

    def request_owner_stop(_signum, _frame) -> None:
        stop_requested.set()
        if runner is not None:
            runner.executor.request_owner_stop()

    previous = signal.signal(signal.SIGINT, request_owner_stop)
    event_loop = None
    port = None
    transition_ports = {}
    resumed_transition_profiles = None
    transition_profiles = None
    result = None
    try:
        host_bindings = make_optional_agent_host_bindings(
            selection.input_target,
            workspace_policy=selection.runtime_policy.workspace,
            provider_backend_config=_execution_route(selection),
            transport_contract={
                "interaction_protocol_ref": "llm_request_envelope/v1",
                "response_adapter_ref": "llm_response_envelope/v1",
            },
            execution_profiles={
                profile_id: (
                    selected.input_target,
                    _execution_route(selected),
                    {
                        "interaction_protocol_ref":
                            "llm_request_envelope/v1",
                        "response_adapter_ref":
                            "llm_response_envelope/v1",
                    },
                )
                for profile_id, selected in selections.items()
                if profile_id != "default"
            },
        )
        if resume:
            # Validate every exact model/provider route through a read-only
            # Registry handle before resume_run acquires writer authority or
            # appends the successor run_execution_authority.
            resumed_transition_profiles = _validate_resumed_execution_profiles(
                destination, selections, catalog=agent_task_catalog(plugin_catalog), registration=registration)
        owner = (
            resume_run(
                registration,
                run_dir=destination,
                model_condition=selection.input_target.model_condition,
                catalog=agent_task_catalog(plugin_catalog),
                host_execution_bindings=host_bindings,
            )
            if resume else
            start_run(
                module,
                registration,
                run_dir=destination,
                task_input=request,
                entry_inputs={"request": request},
                budgets=ModuleBudgetDeclaration(
                    tuple(bucket_documents),
                    ("rpnh/module_declaration/v1",),
                    call_cap, 0, call_cap, 0,
                ),
                model_condition=selection.input_target.model_condition,
                owner_statement=spec.owner_statement,
                command_id="rpnh:agent-task:fresh",
                catalog=agent_task_catalog(plugin_catalog),
                host_execution_bindings=host_bindings,
            ))
        socket_path = spec.owner_socket_path or destination / "owner.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        event_loop = OwnerEventLoop(owner, socket_path)
        if resumed_transition_profiles is not None:
            # Every resume route, including a one-node task, comes from the
            # Registry transition/profile authority established above.
            transition_profiles = resumed_transition_profiles
        elif spec.workflow_graph is None:
            port = build_llm_input_port(
                selection, destination_run_root=destination)
        else:
            feedback_targets = {
                arc.target.node_id for arc in spec.workflow_graph.arcs
                if arc.kind == "feedback"}
            transition_profiles = {
                transition_id: node.execution.profile_id or "default"
                for node in spec.workflow_graph.nodes
                if node.execution.plugin is None
                for transition_id in (
                    f"team.{node.node_id}",
                    *((f"team.{node.node_id}__rework",)
                      if node.node_id in feedback_targets else ()),
                )
            }
        if transition_profiles is not None:
            for transition_id, profile_id in transition_profiles.items():
                transition_ports[transition_id] = build_llm_input_port(
                    selections[profile_id], destination_run_root=destination)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port,
            llm_input_ports_by_transition=transition_ports,
            interruption_requested=stop_requested.is_set)
        with ThreadPoolExecutor(
                max_workers=spec.max_parallel_nodes) as workers:
            runner = Orchestrator(
                owner=owner,
                event_loop=event_loop,
                prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=workers.submit,
                max_in_flight=spec.max_parallel_nodes,
            )
            if stop_requested.is_set():
                runner.executor.request_owner_stop()
            result = runner.run()
    finally:
        for owned_port in dict.fromkeys((
                *((port,) if port is not None else ()),
                *transition_ports.values())):
            owned_port.close()
        if event_loop is not None:
            event_loop.close()
        signal.signal(signal.SIGINT, previous)
    if result is None:
        raise RuntimeError("agent task execution returned no owner result")
    result_ref, output = _terminal_output(owner, result.terminal_evidence_ref)
    return {
        "schema_version": "rpnh/agent_task_result/v1",
        "kind": spec.kind,
        "run_dir": str(destination),
        "run_ref": ref_payload(owner.identity.run_ref),
        "task_ref": ref_payload(owner.identity.task_ref),
        "stop_reason": result.stop_reason,
        "terminal_evidence_ref": (
            None if result.terminal_evidence_ref is None
            else ref_payload(result.terminal_evidence_ref)),
        "terminal_result_ref": result_ref,
        "output": output,
        "actual_model_call_counts": list(
            owner._core.event_store.actual_model_call_counts()),
    }


def run_agent_task(spec: AgentTaskSpec) -> dict[str, Any]:
    """Start one fresh independent RPNH agent task."""
    return _execute_agent_task(spec, resume=False)


def resume_agent_task(spec: AgentTaskSpec) -> dict[str, Any]:
    """Resume one clean owner-stopped task from its Registry checkpoint."""
    return _execute_agent_task(spec, resume=True)


__all__ = (
    "AgentStage", "AgentTaskSpec", "AgentWorkflowGraph", "EXECUTOR_KEY", "TERMINAL_KEY",
    "TEXT_SCHEMA", "agent_task_catalog", "agent_task_registration",
    "build_agent_task_module", "build_agent_workflow_module",
    "resume_agent_task", "run_agent_task",
)
