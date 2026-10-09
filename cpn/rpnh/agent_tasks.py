"""Independent single-agent and Designer-authored graph task objects.

Every invocation creates one ordinary RPNH Module/Registry lineage.  A one
stage declaration is a single-agent task.  A workflow is an explicit graph
with typed ports, arcs, ingress and egress.  This module
composes the existing Harness, optional AgentLoop component and owner event
loop.  It owns no second scheduler or Registry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
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
    execution_config_path: Path | None
    workflow_graph: AgentWorkflowGraph | None = None
    max_attempts_per_stage: int | None = 12
    max_parallel_nodes: int = 4
    execution_profiles: tuple[tuple[str, Path], ...] = ()
    owner_statement: str = "RPNH user-authorized agent task"
    plugin_configuration: Mapping[str, Any] | None = None
    plugin_catalog_digest: str | None = None
    owner_socket_path: Path | None = None
    managed_bindings: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict)
    managed_tool_policy: Mapping[str, Any] | None = None
    tool_program_policy: Mapping[str, Any] | None = None
    registered_execution_sources: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.registered_execution_sources) is not tuple:
            raise TypeError('registered execution sources require immutable tuples')
        public = bool(self.registered_execution_sources)
        if public:
            if (type(self.registered_execution_sources) is not tuple
                    or len(self.registered_execution_sources) != 1
                    or type(self.registered_execution_sources[0]) is not tuple
                    or len(self.registered_execution_sources[0]) != 2
                    or self.registered_execution_sources[0][0] != 'default'
                    or type(self.registered_execution_sources[0][1]) is not str
                    or re.fullmatch(r'[a-z][a-z0-9_.:-]{0,127}', self.registered_execution_sources[0][1]) is None
                    or self.execution_config_path is not None or self.execution_profiles
                    or self.workflow_graph is not None or len(self.stages) != 1
                    or self.plugin_configuration is not None or self.plugin_catalog_digest is not None
                    or self.managed_bindings or self.managed_tool_policy is not None
                    or self.tool_program_policy is not None or self.max_parallel_nodes != 1
                    or self.owner_socket_path is None):
                raise ValueError('registered AgentTask requires exclusive single-stage public sources')
        if (not isinstance(self.run_dir, Path)
                or not public and not isinstance(self.execution_config_path, Path)):
            raise TypeError("agent task paths require pathlib.Path")
        object.__setattr__(self, "run_dir", self.run_dir.resolve())
        if self.execution_config_path is not None:
            object.__setattr__(self, "execution_config_path", self.execution_config_path.resolve())
        if self.owner_socket_path is not None:
            if not isinstance(self.owner_socket_path, Path):
                raise TypeError("owner_socket_path requires pathlib.Path or None")
            object.__setattr__(
                self, "owner_socket_path", self.owner_socket_path.resolve())
        if public and self.owner_socket_path != self.run_dir / 'owner.sock':
            raise ValueError('registered AgentTask socket must be owner.sock')
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
        if (self.max_attempts_per_stage is not None
                and (isinstance(self.max_attempts_per_stage, bool)
                     or not isinstance(self.max_attempts_per_stage, int)
                     or self.max_attempts_per_stage < 1)):
            raise ValueError(
                "max_attempts_per_stage must be null or positive")
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
            if self.plugin_catalog_digest is not None or self.managed_bindings:
                raise ValueError("plugin digest requires its exact owner configuration")
        else:
            from cpn.plugins.api import frozen
            if (not isinstance(self.plugin_configuration, Mapping)
                    or not isinstance(self.plugin_catalog_digest, str)
                    or re.fullmatch(r"[a-f0-9]{64}", self.plugin_catalog_digest) is None):
                raise ValueError("plugin configuration requires its exact compiled catalog digest")
            object.__setattr__(self, "plugin_configuration", frozen(self.plugin_configuration))
        if not isinstance(self.managed_bindings, Mapping):
            raise ValueError("managed bindings must be keyed by node or stage")
        normalized = {}
        for node_id, raw in self.managed_bindings.items():
            if (not isinstance(node_id, str)
                    or _STAGE_ID.fullmatch(node_id) is None
                    or not isinstance(raw, Mapping)
                    or set(raw) not in ({"tools"}, {"tools", "admitted_effects"})
                    or not isinstance(raw.get("tools"), Mapping)
                    or not raw["tools"]):
                raise ValueError(
                    "managed bindings require one exact nonempty node tool declaration")
            effects = raw.get("admitted_effects", ["pure"])
            if (not isinstance(effects, (list, tuple)) or not effects
                    or len(set(effects)) != len(effects)
                    or any(effect not in {
                        "pure", "external_read", "external_write"}
                           for effect in effects)):
                raise ValueError("managed binding effects are invalid")
            tools = {}
            for visible_name, declaration in raw["tools"].items():
                if (not isinstance(visible_name, str)
                        or _STAGE_ID.fullmatch(visible_name) is None
                        or not isinstance(declaration, Mapping)
                        or set(declaration) - {
                            "selector", "description", "input_schema"}
                        or not isinstance(declaration.get("selector"), str)):
                    raise ValueError("managed tool declaration is invalid")
                tools[visible_name] = dict(declaration)
            normalized[node_id] = {
                "tools": tools,
                "admitted_effects": list(effects),
            }
        known_nodes = (
            {stage.stage_id for stage in self.stages}
            if self.workflow_graph is None else
            {node.node_id for node in self.workflow_graph.nodes})
        if set(normalized) - known_nodes:
            raise ValueError("managed bindings reference an unknown node or stage")
        if self.workflow_graph is not None:
            plugin_nodes = {
                node.node_id for node in self.workflow_graph.nodes
                if node.execution.plugin is not None}
            if set(normalized) & plugin_nodes:
                raise ValueError(
                    "managed bindings cannot target plugin-executed workflow nodes")
        if any(set(binding["tools"]) & set(OPTIONAL_TOOL_BINDINGS)
               for binding in normalized.values()):
            raise ValueError(
                "managed tool names cannot collide with reserved built-in tools")
        from cpn.plugins.api import frozen
        object.__setattr__(self, "managed_bindings", frozen(normalized))
        if self.managed_tool_policy is not None:
            if not normalized or not isinstance(self.managed_tool_policy, Mapping):
                raise ValueError("managed scheduling requires explicit managed bindings and policy")
            from cpn.components.agent_loop.managed_execution import managed_scheduler_policy_from_document
            policy = managed_scheduler_policy_from_document(self.managed_tool_policy)
            object.__setattr__(self, "managed_tool_policy", frozen(policy.identity()))
        if self.tool_program_policy is not None:
            if self.managed_tool_policy is None:
                raise ValueError("tool programs require the shared managed scheduler policy")
            from cpn.components.agent_loop.program_execution import validate_program_policy
            program, budget = validate_program_policy(self.tool_program_policy)
            if budget.max_parallel > policy.max_in_flight:
                raise ValueError("program parallelism exceeds shared run capacity")
            program_nodes = [node for node in (() if self.workflow_graph is None else self.workflow_graph.nodes)
                             if "run_tool_program" in (node.execution.tools or ())]
            if not program_nodes or any(
                    "read_tool_program_output" not in (node.execution.tools or ())
                    or not set(program["tools"]).issubset(normalized.get(node.node_id, {}).get("tools", {}))
                    for node in program_nodes):
                raise ValueError("program tools require a paired reader and exact node managed bindings")
            object.__setattr__(self, "tool_program_policy", frozen(program))

    @property
    def kind(self) -> str:
        return "workflow" if self.workflow_graph is not None else "single_agent"

    @staticmethod
    def _relative_worker_path(path: Path, root: Path) -> str:
        value = Path(os.path.relpath(path, root)).as_posix()
        if PurePosixPath(value).is_absolute():
            raise ValueError("worker path must be relative to its parent")
        return value

    @staticmethod
    def _resolve_worker_path(value: object, root: Path, *, label: str) -> Path:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ValueError(f"{label} must be a relative POSIX path")
        pure = PurePosixPath(value)
        if pure.is_absolute() or pure.as_posix() != value:
            raise ValueError(f"{label} must be a relative POSIX path")
        return (root / Path(*pure.parts)).resolve()

    def as_worker_document(
            self, *, document_root: Path | None = None,
    ) -> dict[str, Any]:
        from cpn.plugins.api import json_copy
        if self.registered_execution_sources:
            if document_root is None:
                raise ValueError('registered worker document requires final document root')
            return {
                'schema_version': 'rpnh/agent_task_spec/v13',
                'run_relative_path': self._relative_worker_path(self.run_dir, Path(document_root).resolve()),
                'prompt': self.prompt,
                'stages': [{'stage_id': stage.stage_id, 'instruction': stage.instruction} for stage in self.stages],
                'workflow_graph': None,
                'registered_execution_sources': dict(self.registered_execution_sources),
                'max_attempts_per_stage': self.max_attempts_per_stage,
                'max_parallel_nodes': self.max_parallel_nodes,
                'owner_statement': self.owner_statement,
                'owner_socket_relative_path': 'owner.sock',
            }
        if document_root is not None:
            root = Path(document_root).resolve()
            socket_relative = (
                None if self.owner_socket_path is None else
                self._relative_worker_path(
                    self.owner_socket_path, self.run_dir))
            if (socket_relative is not None
                    and ".." in PurePosixPath(socket_relative).parts):
                raise ValueError(
                    "owner socket must remain inside its child Registry")
            return {
                "schema_version": (
                    "rpnh/agent_task_spec/v12" if self.tool_program_policy is not None else
                    "rpnh/agent_task_spec/v10" if self.managed_tool_policy is not None else
                    "rpnh/agent_task_spec/v8" if self.managed_bindings
                    else "rpnh/agent_task_spec/v6"),
                **({"plugin_configuration": json_copy(self.plugin_configuration),
                    "plugin_catalog_digest": self.plugin_catalog_digest}
                   if self.plugin_configuration is not None else {}),
                "run_relative_path": self._relative_worker_path(
                    self.run_dir, root),
                "prompt": self.prompt,
                "stages": [
                    {"stage_id": stage.stage_id,
                     "instruction": stage.instruction}
                    for stage in self.stages
                ],
                "workflow_graph": (
                    None if self.workflow_graph is None
                    else self.workflow_graph.to_dict()),
                "execution_config_relative_path": self._relative_worker_path(
                    self.execution_config_path, root),
                "max_attempts_per_stage": self.max_attempts_per_stage,
                "max_parallel_nodes": self.max_parallel_nodes,
                "execution_profiles": {
                    profile_id: self._relative_worker_path(path, root)
                    for profile_id, path in self.execution_profiles
                },
                "owner_statement": self.owner_statement,
                "owner_socket_relative_path": socket_relative,
                **({"managed_bindings": json_copy(self.managed_bindings)}
                   if self.managed_bindings else {}),
                **({"managed_tool_policy": json_copy(self.managed_tool_policy)}
                   if self.managed_tool_policy is not None else {}),
                **({"tool_program_policy": json_copy(self.tool_program_policy)}
                   if self.tool_program_policy is not None else {}),
            }
        return {
            "schema_version": (
                "rpnh/agent_task_spec/v11" if self.tool_program_policy is not None else
                "rpnh/agent_task_spec/v9" if self.managed_tool_policy is not None else
                "rpnh/agent_task_spec/v7" if self.managed_bindings
                else "rpnh/agent_task_spec/v5"),
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
            **({"managed_bindings": json_copy(self.managed_bindings)}
               if self.managed_bindings else {}),
                **({"managed_tool_policy": json_copy(self.managed_tool_policy)}
                   if self.managed_tool_policy is not None else {}),
                **({"tool_program_policy": json_copy(self.tool_program_policy)}
                   if self.tool_program_policy is not None else {}),
        }

    @classmethod
    def from_worker_document(
            cls, value: object, *, document_root: Path | None = None,
    ) -> "AgentTaskSpec":
        if isinstance(value, Mapping) and value.get('schema_version') == 'rpnh/agent_task_spec/v13':
            from .public_material_contracts import validate
            if type(value) is not dict: raise TypeError('public Spec requires an exact JSON object')
            value = validate('rpnh/agent_task_spec/v13', value)
            if document_root is None:
                raise ValueError('registered worker document requires final document root')
            run_dir = cls._resolve_worker_path(value['run_relative_path'], Path(document_root).resolve(), label='run_relative_path')
            return cls(run_dir=run_dir, prompt=value['prompt'],
                stages=tuple(AgentStage(**stage) for stage in value['stages']),
                execution_config_path=None, max_attempts_per_stage=value['max_attempts_per_stage'],
                max_parallel_nodes=value['max_parallel_nodes'], owner_statement=value['owner_statement'],
                owner_socket_path=run_dir / 'owner.sock',
                registered_execution_sources=tuple(value['registered_execution_sources'].items()))
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
        fields_v6 = {
            "schema_version", "run_relative_path", "prompt", "stages",
            "workflow_graph", "execution_config_relative_path",
            "max_attempts_per_stage", "max_parallel_nodes",
            "execution_profiles", "owner_statement",
            "owner_socket_relative_path",
        }
        fields_v7 = fields_v5 | {"managed_bindings"}
        fields_v8 = fields_v6 | {"managed_bindings"}
        expected_fields = (
            fields_v1 if version == "rpnh/agent_task_spec/v1" else
            fields_v2 if version == "rpnh/agent_task_spec/v2" else
            fields_v3 if version == "rpnh/agent_task_spec/v3" else
            fields_v4 if version == "rpnh/agent_task_spec/v4" else
            fields_v5 | (plugin_fields if set(value) & plugin_fields else set())
            if version == "rpnh/agent_task_spec/v5" else
            fields_v6 | (plugin_fields if set(value) & plugin_fields else set())
            if version == "rpnh/agent_task_spec/v6" else
            fields_v7 | plugin_fields
            if version == "rpnh/agent_task_spec/v7" else
            fields_v8 | plugin_fields
            if version == "rpnh/agent_task_spec/v8" else
            fields_v7 | plugin_fields | {"managed_tool_policy"}
            if version == "rpnh/agent_task_spec/v9" else
            fields_v8 | plugin_fields | {"managed_tool_policy"}
            if version == "rpnh/agent_task_spec/v10" else
            fields_v7 | plugin_fields | {"managed_tool_policy", "tool_program_policy"}
            if version == "rpnh/agent_task_spec/v11" else
            fields_v8 | plugin_fields | {"managed_tool_policy", "tool_program_policy"}
            if version == "rpnh/agent_task_spec/v12" else set())
        if version in {"rpnh/agent_task_spec/v6", "rpnh/agent_task_spec/v8", "rpnh/agent_task_spec/v10", "rpnh/agent_task_spec/v12"}:
            if document_root is None:
                raise ValueError(
                    "v6 worker document requires its parent directory")
            if (not isinstance(value, Mapping)
                    or set(value) != expected_fields
                    or not isinstance(value.get("stages"), list)
                    or not isinstance(value.get("execution_profiles"), Mapping)
                    or (value.get("owner_socket_relative_path") is not None
                        and not isinstance(
                            value.get("owner_socket_relative_path"), str))):
                raise ValueError("agent task worker document is not current")
            root = Path(document_root).resolve()
            run_dir = cls._resolve_worker_path(
                value["run_relative_path"], root, label="run_relative_path")
            socket_value = value.get("owner_socket_relative_path")
            socket_path = (
                None if socket_value is None else
                cls._resolve_worker_path(
                    socket_value, run_dir,
                    label="owner_socket_relative_path"))
            if (socket_path is not None
                    and not socket_path.is_relative_to(run_dir)):
                raise ValueError(
                    "owner socket escapes its child Registry")
            return cls(
                run_dir=run_dir,
                prompt=value["prompt"],
                stages=tuple(AgentStage(**stage) for stage in value["stages"]),
                execution_config_path=cls._resolve_worker_path(
                    value["execution_config_relative_path"], root,
                    label="execution_config_relative_path"),
                workflow_graph=(
                    None if value.get("workflow_graph") is None else
                    AgentWorkflowGraph.from_mapping(value["workflow_graph"])),
                max_attempts_per_stage=value["max_attempts_per_stage"],
                max_parallel_nodes=value["max_parallel_nodes"],
                execution_profiles=tuple(
                    (profile_id, cls._resolve_worker_path(
                        path, root,
                        label=f"execution_profiles.{profile_id}"))
                    for profile_id, path in value["execution_profiles"].items()),
                owner_statement=value["owner_statement"],
                plugin_configuration=value.get("plugin_configuration"),
                plugin_catalog_digest=value.get("plugin_catalog_digest"),
                owner_socket_path=socket_path,
                managed_bindings=value.get("managed_bindings", {}),
                managed_tool_policy=value.get("managed_tool_policy"),
                tool_program_policy=value.get("tool_program_policy"),
            )
        invalid_owner_socket_path = (
            isinstance(value, Mapping)
            and version in {"rpnh/agent_task_spec/v4", "rpnh/agent_task_spec/v5"}
            and value.get("owner_socket_path") is not None
            and not isinstance(value.get("owner_socket_path"), str))
        if (not isinstance(value, Mapping)
                or set(value) != expected_fields
                or not isinstance(value.get("stages"), list)
                or (version in {"rpnh/agent_task_spec/v3", "rpnh/agent_task_spec/v4", "rpnh/agent_task_spec/v5", "rpnh/agent_task_spec/v7", "rpnh/agent_task_spec/v9", "rpnh/agent_task_spec/v11"}
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
            managed_bindings=value.get("managed_bindings", {}),
            managed_tool_policy=value.get("managed_tool_policy"),
            tool_program_policy=value.get("tool_program_policy"),
        )


def agent_task_registration(plugin_catalog=None, managed_catalogs=()) -> Registration:
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
    if managed_catalogs:
        from cpn.plugins.managed_tools import ManagedPluginToolAdapter
        registered = set()
        for managed in managed_catalogs:
            fresh = tuple(
                tool for tool in managed.tools
                if tool.registration_key not in registered)
            if fresh:
                # Registration rejects duplicate keys. Per-node catalogs may
                # share an exact declaration, so register each exact key once.
                ManagedPluginToolAdapter(managed).register(
                    registration,
                    registration_keys={
                        declaration.registration_key
                        for declaration in fresh})
                registered.update(
                    declaration.registration_key for declaration in fresh)
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


def agent_task_catalog(plugin_catalog=None, *, registered_public=False) -> SchemaCatalog:
    schemas, types = optional_agent_loop_schema_data()
    paths = {}
    if registered_public:
        from .collaboration import source_identity_schema_data
        public_schemas, public_types, paths = source_identity_schema_data()
        schemas = {**schemas, **public_schemas}
        types = (*types, *public_types)
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
    }, types=types, schema_paths=paths)


def build_agent_task_module(
        stages: Sequence[AgentStage], *,
        max_attempts_per_stage: int | None = 12,
        managed_tools: Mapping[str, Sequence[str]] | None = None,
        managed_tool_names: Mapping[str, Sequence[str]] | None = None,
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
    if (max_attempts_per_stage is not None
            and (isinstance(max_attempts_per_stage, bool)
                 or not isinstance(max_attempts_per_stage, int)
                 or max_attempts_per_stage < 1)):
        raise ValueError(
            "max_attempts_per_stage must be null or positive")

    buckets = [{
        "bucket_id": stage.stage_id,
        "budget_scope": "module",
        "finalization_scope": None,
        "max_attempts": max_attempts_per_stage,
    } for stage in stages]
    components = []
    managed_tools = dict(managed_tools or {})
    managed_tool_names = dict(managed_tool_names or {})
    if (set(managed_tool_names) - {stage.stage_id for stage in stages}
            or any(set(names) & set(OPTIONAL_TOOL_BINDINGS)
                   for names in managed_tool_names.values())):
        raise ValueError(
            "managed stage tool names collide with the built-in surface")
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
                "tools": sorted({
                    *(name for name in OPTIONAL_TOOL_BINDINGS
                      if name not in {"read_managed_output", "read_tool_program_output", "run_tool_program"}),
                    *managed_tools.get(stage.stage_id, ()),
                }),
                "config": {
                    "provider_attempt_limit": 3,
                    "agent_loop_role": "actor",
                    "semantic_node_id": stage.stage_id,
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
    from .public_material_contracts import POLICY, validate_policy
    if provenance.get('schema_version') == POLICY:
        provenance = validate_policy(provenance)
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
        checkpoint_version_id: str | None = None,
        reopen_command_id: str | None = None,
        reopen_reason: str | None = None,
        registered_context=None,
) -> dict[str, Any]:
    """Run or resume one task through the owner/Harness/AgentLoop path."""
    if not isinstance(spec, AgentTaskSpec):
        raise TypeError("run_agent_task requires AgentTaskSpec")
    if spec.registered_execution_sources:
        from .public_agent_materials import RegisteredAgentExecutionContext
        if type(registered_context) is not RegisteredAgentExecutionContext or resume:
            raise ValueError('registered AgentTask requires fresh bound verified execution context')
        registered_context.verify_spec(spec)
        # The native receipt/origin issuer remains deliberately unavailable.
        # No public DTO or valid material inventory can authorize native launch.
        registered_context.require_native_execution()
    elif registered_context is not None:
        raise ValueError('registered context cannot replace legacy execution sources')
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
    from cpn.plugins.managed_tools import ManagedPluginToolCatalog
    managed_catalogs = {
        node_id: ManagedPluginToolCatalog(
            plugin_catalog, binding["tools"],
            admitted_effects=binding["admitted_effects"])
        for node_id, binding in spec.managed_bindings.items()
    }
    managed_registration_keys = {
        node_id: tuple(
            declaration.registration_key for declaration in catalog.tools)
        for node_id, catalog in managed_catalogs.items()
    }
    managed_visible_names = {
        node_id: tuple(declaration.name for declaration in catalog.tools)
        for node_id, catalog in managed_catalogs.items()
    }
    registration = agent_task_registration(
        plugin_catalog, managed_catalogs.values())
    if spec.workflow_graph is None:
        module = build_agent_task_module(
            spec.stages, max_attempts_per_stage=spec.max_attempts_per_stage,
            managed_tools=managed_registration_keys,
            managed_tool_names=managed_visible_names)
    else:
        schemas, _types = optional_agent_loop_schema_data()
        module = build_agent_workflow_module(
            spec.workflow_graph,
            executor_key=EXECUTOR_KEY,
            terminal_key=TERMINAL_KEY,
            tools=tuple(name for name in OPTIONAL_TOOL_BINDINGS
                        if name not in {"read_managed_output", "read_tool_program_output", "run_tool_program"}),
            required_schemas=(CONFIG_SCHEMA_ID, *schemas),
            max_attempts_per_node=spec.max_attempts_per_stage,
            plugin_catalog=plugin_catalog,
            managed_tools=managed_registration_keys,
            managed_tool_names=managed_visible_names,
        )
    bucket_documents = module.to_dict()["budget_buckets"]
    call_cap = (None if any(bucket["max_attempts"] is None
                            for bucket in bucket_documents)
                else sum(bucket["max_attempts"]
                         for bucket in bucket_documents))
    request = OwnerInput(
        TEXT_SCHEMA, canonical_json(spec.prompt), "RPNH agent task request")
    runner = None
    stop_requested = threading.Event()

    def request_owner_stop(_signum, _frame) -> None:
        stop_requested.set()
        if runner is not None:
            runner.request_owner_stop()

    previous = signal.signal(signal.SIGINT, request_owner_stop)
    event_loop = None
    port = None
    transition_ports = {}
    resumed_transition_profiles = None
    transition_profiles = None
    result = None
    managed_capacity = None
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
            managed_catalogs=managed_catalogs,
            managed_tool_policy=spec.managed_tool_policy,
            tool_program_policy=spec.tool_program_policy,
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
                checkpoint_version_id=checkpoint_version_id,
                reopen_command_id=reopen_command_id,
                reopen_reason=reopen_reason,
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
        if managed_catalogs:
            from types import MappingProxyType
            from cpn.plugins.managed_tools import ManagedPluginInvocationService
            assert services._optional_agent_service is not None
            services._optional_agent_service.managed_plugin_services = (
                MappingProxyType({
                    node_id: ManagedPluginInvocationService(
                        owner, services._kernel, services._repository, catalog)
                    for node_id, catalog in managed_catalogs.items()
                }))
            services._optional_agent_service.managed_plugin_interruption_requested = (
                stop_requested.is_set)
        if spec.managed_tool_policy is not None:
            from cpn.components.agent_loop.managed_execution import managed_scheduler_policy_from_document
            from cpn.plugins.managed_scheduler import ManagedRunCapacity
            policy = managed_scheduler_policy_from_document(spec.managed_tool_policy)
            managed_capacity = ManagedRunCapacity(policy.max_in_flight)
            services._optional_agent_service.managed_scheduler_policy = policy
            services._optional_agent_service.managed_run_capacity = managed_capacity
        if spec.tool_program_policy is not None:
            services._optional_agent_service.tool_program_policy = spec.tool_program_policy
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
                runner.request_owner_stop()
            result = runner.run()
    finally:
        for owned_port in dict.fromkeys((
                *((port,) if port is not None else ()),
                *transition_ports.values())):
            owned_port.close()
        if managed_capacity is not None:
            managed_capacity.close()
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


def resume_agent_task(
        spec: AgentTaskSpec,
) -> dict[str, Any]:
    """Resume one owner-stopped task from its current checkpoint."""
    return _execute_agent_task(spec, resume=True)


def reopen_agent_task(
        spec: AgentTaskSpec, *, checkpoint_version_id: str,
        command_id: str, reason: str,
) -> dict[str, Any]:
    """Start a new generation at one exact owner-selected checkpoint."""
    return _execute_agent_task(
        spec, resume=True, checkpoint_version_id=checkpoint_version_id,
        reopen_command_id=command_id, reopen_reason=reason)


__all__ = (
    "AgentStage", "AgentTaskSpec", "AgentWorkflowGraph", "EXECUTOR_KEY", "TERMINAL_KEY",
    "TEXT_SCHEMA", "agent_task_catalog", "agent_task_registration",
    "build_agent_task_module", "build_agent_workflow_module",
    "reopen_agent_task", "resume_agent_task", "run_agent_task",
)
