"""Generic Designer-authored workflow graphs for independent agent tasks.

The graph is application-neutral: nodes are ordinary registered agent
operations, arcs are typed product dependencies, and ingress/egress are exact
port bindings. Dependency arcs form the finite business path; explicitly
declared feedback arcs are alternative rework activations. Lowering adds one
control lane per arc and duplicates a fan-out product occurrence by the
declared output-arc weight. Consequently a branch cannot steal another
branch's only activation, joins retain their ordinary Petri all-input enabling
rule, and a feedback edge never becomes a first-generation join prerequisite.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from .module import ModuleDeclaration
from .petri_contracts import (
    ArcDeclaration,
    BindingContext,
    DeclarationError,
    PNFragment,
    PlaceDeclaration,
    PortBinding,
    PortDeclaration,
    TransitionDeclaration,
)


TEXT_SCHEMA = "application/rpnh_agent_text/v1"
WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID = (
    "application/rpnh_agent_workflow_graph_config/v1")
WORKFLOW_GRAPH_CONFIG_SCHEMA_ID = (
    "application/rpnh_agent_workflow_graph_config/v2")
WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID = (
    "application/rpnh_agent_workflow_graph_config/v3")
WORKFLOW_GRAPH_COMPONENT_V1_KEY = "rpnh/agent-workflow-graph/v1"
WORKFLOW_GRAPH_COMPONENT_KEY = "rpnh/agent-workflow-graph/v2"
WORKFLOW_GRAPH_COMPONENT_V3_KEY = "rpnh/agent-workflow-graph/v3"
WORKFLOW_GRAPH_COMPONENT_V4_KEY = "rpnh/agent-workflow-graph/v4"
WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID = "application/rpnh_agent_workflow_graph_config/v4"

_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{label} must match [a-z][a-z0-9_]{{0,47}}")
    return value


def _strict_mapping(
        value: object, fields: set[str], label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


@dataclass(frozen=True, slots=True)
class AgentWorkflowPort:
    """One symbolic text-product port and its business artifact category."""

    port_id: str
    artifact_id: str

    def __post_init__(self) -> None:
        _identifier(self.port_id, "workflow port_id")
        _identifier(self.artifact_id, "workflow artifact_id")

    def to_dict(self) -> dict[str, str]:
        return {"port_id": self.port_id, "artifact_id": self.artifact_id}

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowPort":
        raw = _strict_mapping(
            value, {"port_id", "artifact_id"}, "workflow port")
        return cls(raw["port_id"], raw["artifact_id"])


@dataclass(frozen=True, slots=True)
class AgentWorkflowExecution:
    """Trusted execution selectors carried by a Designer node.

    A profile is an allowlisted identifier resolved by the task launcher; it
    is never a path or provider document supplied by the graph.
    """

    role: str = "actor"
    tools: tuple[str, ...] | None = None
    profile_id: str | None = None
    plugin: str | None = None

    def __post_init__(self) -> None:
        if self.plugin is not None:
            from cpn.plugins.api import symbol
            if not isinstance(self.plugin, str) or self.plugin.count("/") != 1:
                raise ValueError("plugin selector must be plugin_name/operation_name")
            for part in self.plugin.split("/"):
                symbol(part)
            if self.role != "actor" or self.tools is not None or self.profile_id is not None:
                raise ValueError("plugin execution cannot also select LLM tools, roles or profiles")
        if self.role not in {"actor", "critic", "finalization_reviewer"}:
            raise ValueError("workflow execution role is not supported")
        if self.tools is not None:
            if (not isinstance(self.tools, tuple)
                    or any(_ID.fullmatch(name) is None for name in self.tools)
                    or tuple(sorted(set(self.tools))) != self.tools):
                raise ValueError(
                    "workflow execution tools must be unique sorted identifiers")
            if not {"write_file", "complete_interaction"}.issubset(
                    self.tools):
                raise ValueError(
                    "workflow execution tools require write_file and complete_interaction")
        if (self.profile_id is not None
                and (not isinstance(self.profile_id, str)
                     or _ID.fullmatch(self.profile_id) is None)):
            raise ValueError("workflow profile_id must be an identifier or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            **({"plugin": self.plugin} if self.plugin is not None else {}),
            "role": self.role,
            "tools": None if self.tools is None else list(self.tools),
            "profile_id": self.profile_id,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowExecution":
        if not isinstance(value, Mapping) or set(value) not in (
                {"role", "tools", "profile_id"},
                {"role", "tools", "profile_id", "plugin"}):
            raise ValueError("workflow node execution fields are invalid")
        raw = value
        tools = raw["tools"]
        if tools is not None and not isinstance(tools, list):
            raise ValueError("workflow execution tools must be an array or null")
        return cls(
            role=raw["role"],
            tools=None if tools is None else tuple(tools),
            profile_id=raw["profile_id"], plugin=raw.get("plugin"),
        )


@dataclass(frozen=True, slots=True)
class AgentWorkflowNode:
    """One Designer-assigned business responsibility."""

    node_id: str
    instruction: str
    input_ports: tuple[AgentWorkflowPort, ...]
    output_ports: tuple[AgentWorkflowPort, ...]
    execution: AgentWorkflowExecution = AgentWorkflowExecution()

    def __post_init__(self) -> None:
        _identifier(self.node_id, "workflow node_id")
        if (not isinstance(self.instruction, str)
                or not self.instruction.strip()
                or self.instruction != self.instruction.strip()):
            raise ValueError(
                "workflow node instruction must be nonempty trimmed text")
        for label, ports in (
                ("input", self.input_ports), ("output", self.output_ports)):
            if (not isinstance(ports, tuple) or not ports
                    or any(not isinstance(port, AgentWorkflowPort)
                           for port in ports)):
                raise ValueError(
                    f"workflow node requires one or more {label} ports")
            if len({port.port_id for port in ports}) != len(ports):
                raise ValueError(f"workflow node {label} port IDs must be unique")
        if ({port.port_id for port in self.input_ports}
                & {port.port_id for port in self.output_ports}):
            raise ValueError(
                "workflow node input/output port IDs must be disjoint")
        if not isinstance(self.execution, AgentWorkflowExecution):
            raise TypeError("workflow node execution must be typed")
        object.__setattr__(
            self, "input_ports", tuple(sorted(
                self.input_ports, key=lambda port: port.port_id)))
        object.__setattr__(
            self, "output_ports", tuple(sorted(
                self.output_ports, key=lambda port: port.port_id)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "instruction": self.instruction,
            "input_ports": [port.to_dict() for port in self.input_ports],
            "output_ports": [port.to_dict() for port in self.output_ports],
            "execution": self.execution.to_dict(),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowNode":
        if (not isinstance(value, Mapping)
                or set(value) not in ({
                    "node_id", "instruction", "input_ports", "output_ports",
                }, {
                    "node_id", "instruction", "input_ports", "output_ports",
                    "execution",
                })):
            raise ValueError("workflow node fields are invalid")
        raw = value
        if (not isinstance(raw["input_ports"], list)
                or not isinstance(raw["output_ports"], list)):
            raise ValueError("workflow node ports must be arrays")
        return cls(
            raw["node_id"], raw["instruction"],
            tuple(AgentWorkflowPort.from_mapping(port)
                  for port in raw["input_ports"]),
            tuple(AgentWorkflowPort.from_mapping(port)
                  for port in raw["output_ports"]),
            (AgentWorkflowExecution()
             if "execution" not in raw else
             AgentWorkflowExecution.from_mapping(raw["execution"])),
        )


@dataclass(frozen=True, slots=True)
class AgentWorkflowEndpoint:
    node_id: str
    port_id: str

    def __post_init__(self) -> None:
        _identifier(self.node_id, "workflow endpoint node_id")
        _identifier(self.port_id, "workflow endpoint port_id")

    def to_dict(self) -> dict[str, str]:
        return {"node_id": self.node_id, "port_id": self.port_id}

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowEndpoint":
        raw = _strict_mapping(
            value, {"node_id", "port_id"}, "workflow endpoint")
        return cls(raw["node_id"], raw["port_id"])


@dataclass(frozen=True, slots=True)
class AgentWorkflowArc:
    arc_id: str
    source: AgentWorkflowEndpoint
    target: AgentWorkflowEndpoint
    kind: str = "dependency"

    def __post_init__(self) -> None:
        _identifier(self.arc_id, "workflow arc_id")
        if (not isinstance(self.source, AgentWorkflowEndpoint)
                or not isinstance(self.target, AgentWorkflowEndpoint)):
            raise TypeError("workflow arc endpoints must be typed")
        if self.kind not in {"dependency", "feedback"}:
            raise ValueError(
                "workflow arc kind must be dependency or feedback")

    def to_dict(self) -> dict[str, Any]:
        return {
            "arc_id": self.arc_id,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "kind": self.kind,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowArc":
        if (not isinstance(value, Mapping)
                or set(value) not in (
                    {"arc_id", "source", "target"},
                    {"arc_id", "source", "target", "kind"})):
            raise ValueError("workflow arc fields are invalid")
        raw = value
        return cls(
            raw["arc_id"],
            AgentWorkflowEndpoint.from_mapping(raw["source"]),
            AgentWorkflowEndpoint.from_mapping(raw["target"]),
            raw.get("kind", "dependency"),
        )


@dataclass(frozen=True, slots=True)
class AgentWorkflowGraph:
    """A finite workflow graph with explicit bounded rework semantics."""

    nodes: tuple[AgentWorkflowNode, ...]
    arcs: tuple[AgentWorkflowArc, ...]
    ingress: AgentWorkflowEndpoint
    egress: AgentWorkflowEndpoint
    max_rework_cycles: int = 0

    def __post_init__(self) -> None:
        if (not isinstance(self.nodes, tuple) or not self.nodes
                or any(not isinstance(node, AgentWorkflowNode)
                       for node in self.nodes)):
            raise ValueError("workflow graph requires one or more nodes")
        if (not isinstance(self.arcs, tuple)
                or any(not isinstance(arc, AgentWorkflowArc)
                       for arc in self.arcs)
                or not isinstance(self.ingress, AgentWorkflowEndpoint)
                or not isinstance(self.egress, AgentWorkflowEndpoint)):
            raise TypeError("workflow graph members must be typed")
        if (isinstance(self.max_rework_cycles, bool)
                or not isinstance(self.max_rework_cycles, int)
                or self.max_rework_cycles < 0):
            raise ValueError(
                "workflow max_rework_cycles must be a nonnegative integer")
        nodes = {node.node_id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("workflow node IDs must be unique")
        if len({arc.arc_id for arc in self.arcs}) != len(self.arcs):
            raise ValueError("workflow arc IDs must be unique")

        inputs = {
            (node.node_id, port.port_id): port
            for node in self.nodes for port in node.input_ports
        }
        outputs = {
            (node.node_id, port.port_id): port
            for node in self.nodes for port in node.output_ports
        }
        ingress_key = (self.ingress.node_id, self.ingress.port_id)
        egress_key = (self.egress.node_id, self.egress.port_id)
        if ingress_key not in inputs:
            raise ValueError("workflow ingress must bind a declared input port")
        if egress_key not in outputs:
            raise ValueError("workflow egress must bind a declared output port")

        incoming: dict[tuple[str, str], list[AgentWorkflowArc]] = {
            key: [] for key in inputs}
        outgoing: dict[tuple[str, str], list[AgentWorkflowArc]] = {
            key: [] for key in outputs}
        pairs: set[tuple[str, str, str, str]] = set()
        following = {node_id: set() for node_id in nodes}
        preceding = {node_id: set() for node_id in nodes}
        for arc in self.arcs:
            source = (arc.source.node_id, arc.source.port_id)
            target = (arc.target.node_id, arc.target.port_id)
            if source not in outputs or target not in inputs:
                raise ValueError(
                    "workflow arc must bind declared output and input ports")
            if outputs[source].artifact_id != inputs[target].artifact_id:
                raise ValueError(
                    "workflow arc artifact categories must match exactly")
            identity = (*source, *target)
            if identity in pairs:
                raise ValueError("workflow graph contains a duplicate arc")
            pairs.add(identity)
            incoming[target].append(arc)
            outgoing[source].append(arc)
            if arc.kind == "dependency":
                following[arc.source.node_id].add(arc.target.node_id)
                preceding[arc.target.node_id].add(arc.source.node_id)

        if incoming[ingress_key]:
            raise ValueError("workflow ingress input cannot have an arc producer")
        for key, producers in incoming.items():
            expected = 0 if key == ingress_key else 1
            if len(producers) != expected:
                raise ValueError(
                    "every non-ingress workflow input needs exactly one arc")
        if outgoing[egress_key]:
            raise ValueError("workflow egress output cannot feed another node")
        for key, consumers in outgoing.items():
            expected = 0 if key == egress_key else 1
            if len(consumers) < expected:
                raise ValueError(
                    "every non-egress workflow output needs an arc consumer")

        # Two inputs on one target transition cannot be projected from the
        # same fused source place without losing their exact port identities.
        source_target_nodes = [
            (arc.source.node_id, arc.source.port_id, arc.target.node_id)
            for arc in self.arcs]
        if len(set(source_target_nodes)) != len(source_target_nodes):
            raise ValueError(
                "one source output may feed a target node only once")

        feedback = tuple(arc for arc in self.arcs
                         if arc.kind == "feedback")
        plugin_nodes = tuple(node for node in self.nodes if node.execution.plugin is not None)
        if plugin_nodes and feedback:
            raise ValueError("native plugin v1 graphs cannot replay operations through feedback")
        if any(len(node.input_ports) != 1 or len(node.output_ports) != 1 for node in plugin_nodes):
            raise ValueError("native plugin nodes require one JSON-text input and output")
        if bool(feedback) != bool(self.max_rework_cycles):
            raise ValueError(
                "feedback arcs require a positive max_rework_cycles and "
                "acyclic graphs require zero")
        for key, producers in incoming.items():
            kinds = {arc.kind for arc in producers}
            if len(kinds) > 1:
                raise ValueError(
                    "one workflow input cannot mix dependency and feedback producers")
        for key, consumers in outgoing.items():
            kinds = {arc.kind for arc in consumers}
            if len(kinds) > 1:
                raise ValueError(
                    "one workflow output cannot mix dependency and feedback consumers")
        feedback_targets = {arc.target.node_id for arc in feedback}
        for node_id in feedback_targets:
            if not any(
                    key == ingress_key or any(
                        arc.kind == "dependency" for arc in incoming[key])
                    for key in inputs if key[0] == node_id):
                raise ValueError(
                    "feedback target requires a first-generation dependency input")

        def closure(start: str, edges: Mapping[str, set[str]]) -> set[str]:
            reached: set[str] = set()
            pending = [start]
            while pending:
                current = pending.pop()
                if current in reached:
                    continue
                reached.add(current)
                pending.extend(edges[current] - reached)
            return reached

        reachable = closure(self.ingress.node_id, following)
        reaches_exit = closure(self.egress.node_id, preceding)
        if reachable != set(nodes) or reaches_exit != set(nodes):
            raise ValueError(
                "dependency arcs must place every workflow node on an "
                "ingress-to-egress path")

        indegree = {node_id: len(preceding[node_id]) for node_id in nodes}
        ready = sorted(node_id for node_id, count in indegree.items()
                       if count == 0)
        visited: list[str] = []
        while ready:
            current = ready.pop(0)
            visited.append(current)
            for target in sorted(following[current]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort()
        if len(visited) != len(nodes):
            raise ValueError(
                "workflow dependency arcs must be acyclic; cycles use "
                "explicit feedback arcs")
        position = {node_id: index for index, node_id in enumerate(visited)}
        for arc in feedback:
            if (position[arc.target.node_id] >= position[arc.source.node_id]
                    or arc.source.node_id not in closure(
                        arc.target.node_id, following)):
                raise ValueError(
                    "feedback arc must close a dependency path toward an "
                    "earlier node")

        object.__setattr__(
            self, "nodes", tuple(sorted(self.nodes, key=lambda node: node.node_id)))
        object.__setattr__(
            self, "arcs", tuple(sorted(self.arcs, key=lambda arc: arc.arc_id)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "arcs": [arc.to_dict() for arc in self.arcs],
            "ingress": self.ingress.to_dict(),
            "egress": self.egress.to_dict(),
            "max_rework_cycles": self.max_rework_cycles,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "AgentWorkflowGraph":
        if (not isinstance(value, Mapping)
                or set(value) not in (
                    {"nodes", "arcs", "ingress", "egress"},
                    {"nodes", "arcs", "ingress", "egress",
                     "max_rework_cycles"})):
            raise ValueError("workflow graph fields are invalid")
        raw = value
        if not isinstance(raw["nodes"], list) or not isinstance(raw["arcs"], list):
            raise ValueError("workflow nodes and arcs must be arrays")
        return cls(
            tuple(AgentWorkflowNode.from_mapping(node)
                  for node in raw["nodes"]),
            tuple(AgentWorkflowArc.from_mapping(arc)
                  for arc in raw["arcs"]),
            AgentWorkflowEndpoint.from_mapping(raw["ingress"]),
            AgentWorkflowEndpoint.from_mapping(raw["egress"]),
            raw.get("max_rework_cycles", 0),
        )


def _port_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "port_id": {"type": "string", "pattern": _ID.pattern},
            "artifact_id": {"type": "string", "pattern": _ID.pattern},
        },
        "required": ["port_id", "artifact_id"],
    }


WORKFLOW_GRAPH_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": WORKFLOW_GRAPH_CONFIG_SCHEMA_ID,
    "type": "object",
    "additionalProperties": False,
    "definitions": {
        "port": _port_schema(),
        "endpoint": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "node_id": {"type": "string", "pattern": _ID.pattern},
                "port_id": {"type": "string", "pattern": _ID.pattern},
            },
            "required": ["node_id", "port_id"],
        },
    },
    "properties": {
        "nodes": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "node_id": {"type": "string", "pattern": _ID.pattern},
                    "instruction": {"type": "string", "minLength": 1},
                    "input_ports": {
                        "type": "array", "minItems": 1,
                        "items": {"$ref": "#/definitions/port"},
                    },
                    "output_ports": {
                        "type": "array", "minItems": 1,
                        "items": {"$ref": "#/definitions/port"},
                    },
                },
                "required": [
                    "node_id", "instruction", "input_ports", "output_ports"],
            },
        },
        "arcs": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "arc_id": {"type": "string", "pattern": _ID.pattern},
                    "source": {"$ref": "#/definitions/endpoint"},
                    "target": {"$ref": "#/definitions/endpoint"},
                    "kind": {
                        "type": "string",
                        "enum": ["dependency", "feedback"],
                    },
                },
                "required": ["arc_id", "source", "target", "kind"],
            },
        },
        "ingress": {"$ref": "#/definitions/endpoint"},
        "egress": {"$ref": "#/definitions/endpoint"},
        "max_rework_cycles": {"type": "integer", "minimum": 0},
    },
    "required": [
        "nodes", "arcs", "ingress", "egress", "max_rework_cycles"],
}

WORKFLOW_GRAPH_CONFIG_SCHEMA_V3 = deepcopy(WORKFLOW_GRAPH_CONFIG_SCHEMA)
WORKFLOW_GRAPH_CONFIG_SCHEMA_V3["$id"] = WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID
_v3_node = WORKFLOW_GRAPH_CONFIG_SCHEMA_V3["properties"]["nodes"]["items"]
_v3_node["properties"]["execution"] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "role": {
            "type": "string",
            "enum": ["actor", "critic", "finalization_reviewer"],
        },
        "tools": {
            "anyOf": [
                {"type": "null"},
                {"type": "array", "uniqueItems": True,
                 "items": {"type": "string", "pattern": _ID.pattern}},
            ],
        },
        "profile_id": {
            "anyOf": [
                {"type": "null"},
                {"type": "string", "pattern": _ID.pattern},
            ],
        },
    },
    "required": ["role", "tools", "profile_id"],
}
_v3_node["required"].append("execution")

# A new graph contract; old v1-v3 schema bytes remain unchanged.
WORKFLOW_GRAPH_CONFIG_SCHEMA_V4 = deepcopy(WORKFLOW_GRAPH_CONFIG_SCHEMA_V3)
WORKFLOW_GRAPH_CONFIG_SCHEMA_V4["$id"] = WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID
WORKFLOW_GRAPH_CONFIG_SCHEMA_V4["properties"]["nodes"]["items"]["properties"]["execution"]["properties"]["plugin"] = {
    "type": "string", "pattern": "^[a-z][a-z0-9_]{0,47}/[a-z][a-z0-9_]{0,47}$"}

# v1 remains the immutable dependency-DAG wire contract.  The typed Python
# loader accepts it and supplies dependency/zero-rework defaults, while all
# newly authored graphs use the explicit v2 fields above.
WORKFLOW_GRAPH_CONFIG_SCHEMA_V1 = deepcopy(WORKFLOW_GRAPH_CONFIG_SCHEMA)
WORKFLOW_GRAPH_CONFIG_SCHEMA_V1["$id"] = (
    WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID)
WORKFLOW_GRAPH_CONFIG_SCHEMA_V1["properties"].pop("max_rework_cycles")
WORKFLOW_GRAPH_CONFIG_SCHEMA_V1["required"].remove("max_rework_cycles")
_v1_arc = WORKFLOW_GRAPH_CONFIG_SCHEMA_V1["properties"]["arcs"]["items"]
_v1_arc["properties"].pop("kind")
_v1_arc["required"].remove("kind")


def _input_handle(node_id: str, port_id: str) -> str:
    return f"input__{node_id}__{port_id}"


def _output_handle(node_id: str, port_id: str) -> str:
    return f"output__{node_id}__{port_id}"


def _data_place(node_id: str, port_id: str) -> str:
    return f"data__{node_id}__{port_id}"


def _control_place(arc_id: str) -> str:
    return f"control__{arc_id}"


def _rework_permit_place() -> str:
    return "control__workflow_rework_permit"


def _interrupt_handle(node_id: str, port_id: str, variant: str) -> str:
    return f"interrupt__{node_id}__{variant}__{port_id}"


def _rework_operation(node_id: str) -> str:
    return f"{node_id}__rework"


def _node_input_handle(
        graph: AgentWorkflowGraph, node_id: str, port_id: str,
) -> str:
    return ("request" if (node_id, port_id)
            == (graph.ingress.node_id, graph.ingress.port_id)
            else _input_handle(node_id, port_id))


def _node_output_handle(
        graph: AgentWorkflowGraph, node_id: str, port_id: str,
) -> str:
    return ("result" if (node_id, port_id)
            == (graph.egress.node_id, graph.egress.port_id)
            else _output_handle(node_id, port_id))


def lower_agent_workflow_graph(
        config: Mapping[str, Any], context: BindingContext,
) -> PNFragment:
    """Lower dependency paths, rework activations and interruption returns."""
    graph = AgentWorkflowGraph.from_mapping(config)
    operations = {operation.name: operation for operation in context.operations}
    nodes = {node.node_id: node for node in graph.nodes}
    feedback_targets = {
        arc.target.node_id for arc in graph.arcs if arc.kind == "feedback"}
    expected_operations = set(nodes) | {
        _rework_operation(node_id) for node_id in feedback_targets}
    if set(operations) != expected_operations:
        raise DeclarationError(
            "workflow graph operations must exactly match node activations")

    producer_for = {
        (arc.target.node_id, arc.target.port_id): arc
        for arc in graph.arcs
    }
    outgoing: dict[tuple[str, str], list[AgentWorkflowArc]] = {}
    for arc in graph.arcs:
        outgoing.setdefault(
            (arc.source.node_id, arc.source.port_id), []).append(arc)

    ingress_key = (graph.ingress.node_id, graph.ingress.port_id)
    egress_key = (graph.egress.node_id, graph.egress.port_id)
    ingress_place = f"ingress__{graph.ingress.node_id}__{graph.ingress.port_id}"

    places = [
        PlaceDeclaration(ingress_place, TEXT_SCHEMA),
        *(PlaceDeclaration(
            _data_place(node.node_id, port.port_id), TEXT_SCHEMA)
          for node in graph.nodes for port in node.output_ports),
        *(PlaceDeclaration(
            _control_place(arc.arc_id), TEXT_SCHEMA, channel="control")
          for arc in graph.arcs),
        *( (PlaceDeclaration(
            _rework_permit_place(), TEXT_SCHEMA, channel="control"),)
           if graph.max_rework_cycles else ()),
    ]
    public_bindings = (
        PortBinding("request", ingress_place),
        PortBinding("result", _data_place(*egress_key)),
    )
    internal_ports: list[PortDeclaration] = []
    internal_bindings: list[PortBinding] = []
    transitions: list[TransitionDeclaration] = []
    arcs: list[ArcDeclaration] = []

    input_places: dict[tuple[str, str], str] = {}
    for node in graph.nodes:
        for port in node.input_ports:
            key = (node.node_id, port.port_id)
            producer = producer_for.get(key)
            place = (ingress_place if key == ingress_key else
                     _data_place(
                         producer.source.node_id, producer.source.port_id)
                     if producer is not None else None)
            if place is None:
                raise DeclarationError("workflow input has no exact producer")
            input_places[key] = place
            handle = _node_input_handle(graph, *key)
            if handle != "request":
                internal_ports.append(
                    PortDeclaration(handle, "input", TEXT_SCHEMA))
                internal_bindings.append(PortBinding(handle, place))

        for port in node.output_ports:
            key = (node.node_id, port.port_id)
            handle = _node_output_handle(graph, *key)
            if handle != "result":
                internal_ports.append(
                    PortDeclaration(handle, "output", TEXT_SCHEMA))
                internal_bindings.append(
                    PortBinding(handle, _data_place(*key)))

    for node in graph.nodes:
        initial_ports = tuple(
            port for port in node.input_ports
            if (node.node_id, port.port_id) == ingress_key
            or producer_for[(node.node_id, port.port_id)].kind
            == "dependency")
        feedback_ports = tuple(
            port for port in node.input_ports
            if (node.node_id, port.port_id) != ingress_key
            and producer_for[(node.node_id, port.port_id)].kind == "feedback")
        variants = [(node.node_id, initial_ports, "dependency")]
        if feedback_ports:
            variants.append((
                _rework_operation(node.node_id), feedback_ports, "feedback"))

        for operation_name, selected_ports, arc_kind in variants:
            operation = operations[operation_name]
            expected_inputs = tuple(
                _node_input_handle(
                    graph, node.node_id, port.port_id)
                for port in selected_ports)
            semantic_outputs = tuple(
                _node_output_handle(
                    graph, node.node_id, port.port_id)
                for port in node.output_ports)
            interrupt_outputs = tuple(
                _interrupt_handle(
                    node.node_id, port.port_id, operation_name)
                for port in selected_ports)
            has_interrupt_route = (
                operation.outputs == semantic_outputs + interrupt_outputs)
            native = operation.config.get("native_plugin") if node.execution.plugin is not None else None
            if native is not None:
                from cpn.rpnh.petri_contracts import InitialTokenDeclaration
                capability_port = native["capability_port"]
                if (native["selector"] != node.execution.plugin
                        or native["request_port"] != expected_inputs[0]
                        or native["result_port"] != semantic_outputs[0]
                        or operation.request_port is not None
                        or has_interrupt_route or len(operation.outcomes) != 1
                        or operation.outcomes[0].name != "complete"):
                    raise DeclarationError("native plugin graph binding differs from its operation")
                places.append(PlaceDeclaration(capability_port, native["capability_schema"], capacity=1,
                    initial_tokens=(InitialTokenDeclaration(schema=native["capability_schema"],
                                                          value=native["capability"]),)))
                internal_ports.append(PortDeclaration(capability_port, "input", native["capability_schema"]))
                internal_bindings.append(PortBinding(capability_port, capability_port))
                arcs.append(ArcDeclaration(capability_port, operation_name, "input", mode="read"))
            if (operation.inputs != expected_inputs + ((native["capability_port"],) if native else ())
                    or operation.outputs not in (
                        semantic_outputs,
                        semantic_outputs + interrupt_outputs)
                    or operation.request_port != (None if native else expected_inputs[0])):
                raise DeclarationError(
                    "workflow node operation ports differ from its activation")
            transitions.append(TransitionDeclaration(
                operation_name, operation_name))

            selected_controls: list[AgentWorkflowArc] = []
            for port, handle, rollback in zip(
                    selected_ports, expected_inputs, interrupt_outputs,
                    strict=True):
                key = (node.node_id, port.port_id)
                place = input_places[key]
                arcs.append(ArcDeclaration(
                    place, operation_name, "input"))
                if has_interrupt_route:
                    internal_ports.append(
                        PortDeclaration(rollback, "output", TEXT_SCHEMA))
                    internal_bindings.append(PortBinding(rollback, place))
                    arcs.append(ArcDeclaration(
                        place, operation_name, "output", mode="produce",
                        outcome="interrupted", emit="forward",
                        forward_source=place))
                producer = producer_for.get(key)
                if producer is not None:
                    if producer.kind != arc_kind:
                        raise DeclarationError(
                            "workflow activation mixed dependency and feedback")
                    selected_controls.append(producer)

            for edge in sorted(
                    selected_controls, key=lambda item: item.arc_id):
                place = _control_place(edge.arc_id)
                arcs.append(ArcDeclaration(
                    place, operation_name, "input"))
                if has_interrupt_route:
                    arcs.append(ArcDeclaration(
                        place, operation_name, "output", mode="produce",
                        outcome="interrupted", emit="forward",
                        forward_source=place))

            if arc_kind == "feedback":
                arcs.append(ArcDeclaration(
                    _rework_permit_place(), operation_name, "input"))

            for port in node.output_ports:
                key = (node.node_id, port.port_id)
                place = _data_place(*key)
                consumers = outgoing.get(key, [])
                kinds = {edge.kind for edge in consumers}
                route = "rework" if kinds == {"feedback"} else "complete"
                weight = max(1, len(consumers))
                arcs.append(ArcDeclaration(
                    place, operation_name, "output", weight=weight,
                    mode="produce", outcome=route))
                for edge in sorted(
                        consumers, key=lambda item: item.arc_id):
                    arcs.append(ArcDeclaration(
                        _control_place(edge.arc_id), operation_name, "output",
                        mode="produce", outcome=route,
                        emit="control_only"))
            if (graph.max_rework_cycles
                    and node.node_id == graph.ingress.node_id
                    and arc_kind == "dependency"):
                for outcome in {
                        "rework" if {
                            edge.kind for edge in outgoing.get(
                                (node.node_id, port.port_id), ())
                        } == {"feedback"} else "complete"
                        for port in node.output_ports}:
                    arcs.append(ArcDeclaration(
                        _rework_permit_place(), operation_name, "output",
                        weight=graph.max_rework_cycles,
                        mode="produce", outcome=outcome,
                        emit="control_only"))

    return PNFragment(
        places=tuple(places),
        transitions=tuple(transitions),
        arcs=tuple(arcs),
        ports=public_bindings,
        operations=context.operations,
        internal_ports=tuple(internal_ports),
        internal_bindings=tuple(internal_bindings),
    )


def build_agent_workflow_module(
        graph: AgentWorkflowGraph, *, executor_key: str, terminal_key: str,
        tools: Sequence[str], required_schemas: Sequence[str],
        max_attempts_per_node: int = 12, plugin_catalog=None,
) -> ModuleDeclaration:
    """Build one graph-shaped independent workflow Module."""
    if not isinstance(graph, AgentWorkflowGraph):
        raise TypeError("workflow Module requires AgentWorkflowGraph")
    if (isinstance(max_attempts_per_node, bool)
            or not isinstance(max_attempts_per_node, int)
            or max_attempts_per_node < 1):
        raise ValueError("max_attempts_per_node must be positive")
    node_buckets = [{
        "bucket_id": node.node_id,
        "budget_scope": node.node_id if len(graph.nodes) > 1 else "module",
        "finalization_scope": None,
        "max_attempts": max_attempts_per_node,
    } for node in graph.nodes]
    rework_bucket = ({
        "bucket_id": "rpnh:workflow:rework",
        "budget_scope": "rpnh:workflow:rework",
        "finalization_scope": None,
        # The Petri permit place bounds how many rework firings may occur.
        # This bucket bounds provider calls across those firings, so each
        # permitted firing needs the same normal turn allowance as a node.
        "max_attempts": graph.max_rework_cycles * max_attempts_per_node,
    } if graph.max_rework_cycles else None)
    buckets = [*node_buckets, *((rework_bucket,) if rework_bucket else ())]
    producer_for = {
        (arc.target.node_id, arc.target.port_id): arc
        for arc in graph.arcs}
    outgoing: dict[tuple[str, str], tuple[AgentWorkflowArc, ...]] = {}
    for node in graph.nodes:
        for port in node.output_ports:
            key = (node.node_id, port.port_id)
            outgoing[key] = tuple(
                arc for arc in graph.arcs
                if (arc.source.node_id, arc.source.port_id) == key)
    ingress_key = (graph.ingress.node_id, graph.ingress.port_id)
    operations = []
    for node, bucket in zip(graph.nodes, node_buckets, strict=True):
        initial_ports = tuple(
            port for port in node.input_ports
            if (node.node_id, port.port_id) == ingress_key
            or producer_for[(node.node_id, port.port_id)].kind
            == "dependency")
        feedback_ports = tuple(
            port for port in node.input_ports
            if (node.node_id, port.port_id) != ingress_key
            and producer_for[(node.node_id, port.port_id)].kind == "feedback")
        semantic_outputs = [
            _node_output_handle(graph, node.node_id, port.port_id)
            for port in node.output_ports]
        complete_products = []
        rework_products = []
        for port, handle in zip(
                node.output_ports, semantic_outputs, strict=True):
            kinds = {arc.kind for arc in outgoing[
                node.node_id, port.port_id]}
            (rework_products if kinds == {"feedback"}
             else complete_products).append({"port": handle})
        semantic_outcomes = [{
            "name": "complete", "products": complete_products,
        }]
        if rework_products:
            semantic_outcomes.append({
                "name": "rework", "products": rework_products,
            })

        def operation(
                name: str, selected_ports, selected_bucket,
                attempt_limit: int,
        ) -> dict[str, Any]:
            inputs = [
                _node_input_handle(graph, node.node_id, port.port_id)
                for port in selected_ports]
            interrupt_outputs = [
                _interrupt_handle(node.node_id, port.port_id, name)
                for port in selected_ports]
            if node.execution.plugin is not None:
                from cpn.plugins.api import PluginError
                from cpn.plugins.host import operation_config
                if plugin_catalog is None:
                    raise PluginError("plugin workflow requires an explicitly selected HOST catalog")
                selector = node.execution.plugin
                binding = operation_config(plugin_catalog, selector, request_port=inputs[0],
                    result_port=semantic_outputs[0], capability_port="capability__" + node.node_id,
                    wire_mode="text_json")
                return {"name": name, "executor": plugin_catalog.operation_key(selector),
                    "inputs": inputs + [binding["native_plugin"]["capability_port"]],
                    "outputs": semantic_outputs, "request_port": None, "tools": [],
                    "config": binding, "outcomes": semantic_outcomes,
                    "budget_binding": {key: selected_bucket[key] for key in (
                        "bucket_id", "budget_scope", "finalization_scope")}}
            return {
                "name": name,
                "executor": executor_key,
                "inputs": inputs,
                "outputs": semantic_outputs + interrupt_outputs,
                "request_port": inputs[0],
                "tools": list(
                    node.execution.tools
                    if node.execution.tools is not None else sorted(tools)),
                "config": {
                    "provider_attempt_limit": 3,
                    "agent_loop_role": node.execution.role,
                    "workspace_failure_policy": (
                        "allow_explicit_diagnostic"
                        if node.execution.role in {
                            "critic", "finalization_reviewer"}
                        else "require_resolved"),
                    "execution_profile_id": node.execution.profile_id,
                    "semantic_node_id": node.node_id,
                    "node_synopsis": node.instruction,
                    "semantic_outcome_ids": [
                        outcome["name"] for outcome in semantic_outcomes],
                    "resource_bounds": {
                        "max_llm_attempts": attempt_limit,
                        "max_tool_turns": attempt_limit,
                    },
                    # Critic/reviewer loops use the same registered segment
                    # size when context compaction opens a continuation.  The
                    # task/bucket ledger remains the independent hard cap.
                    "turn_budget_extension": attempt_limit,
                },
                "outcomes": [
                    *semantic_outcomes,
                    {"name": "interrupted", "products": []},
                ],
                "budget_binding": {
                    key: selected_bucket[key] for key in (
                        "bucket_id", "budget_scope", "finalization_scope")
                },
            }

        operations.append(operation(
            node.node_id, initial_ports, bucket, max_attempts_per_node))
        if feedback_ports:
            assert rework_bucket is not None
            operations.append(operation(
                _rework_operation(node.node_id), feedback_ports,
                rework_bucket, max_attempts_per_node))

    has_plugins = any(node.execution.plugin is not None for node in graph.nodes)
    graph_schema = WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID if has_plugins else WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID
    graph_component = WORKFLOW_GRAPH_COMPONENT_V4_KEY if has_plugins else WORKFLOW_GRAPH_COMPONENT_V3_KEY
    plugin_schemas = []
    if has_plugins:
        from cpn.plugins.host import BINDING_SCHEMA, schema_key
        plugin_schemas.append(BINDING_SCHEMA)
        plugin_schemas.extend(schema_key(plugin_catalog, node.execution.plugin, "capability")
                              for node in graph.nodes if node.execution.plugin is not None)
    schemas = tuple(dict.fromkeys((
        TEXT_SCHEMA, graph_schema, *required_schemas, *plugin_schemas)))
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": "AgentWorkflowGraph",
        "components": [{
            "name": "team",
            "key": graph_component,
            "config_schema": graph_schema,
            "config": graph.to_dict(),
            "ports": [
                {"name": "request", "direction": "input",
                 "schema": TEXT_SCHEMA},
                {"name": "result", "direction": "output",
                 "schema": TEXT_SCHEMA},
            ],
            "operations": operations,
        }],
        "links": [],
        "entry": {
            "request": {"component": "team", "port": "request"},
        },
        "exit": {
            "result": {"component": "team", "port": "result"},
        },
        "terminal": {
            "key": terminal_key,
            "source": {"component": "team", "port": "result"},
            "operation": graph.egress.node_id,
            "outcome": "complete",
            "config": {"run_outcome": "complete"},
        },
        "required_schemas": list(schemas),
        "budgets": {},
        "designer_constraints": {
            "topology_authority": "declared_arcs",
            "array_order_semantics": "inert",
            "graph_generation": "dependency_graph_with_bounded_feedback_v3",
            "max_rework_cycles": graph.max_rework_cycles,
        },
        "budget_buckets": buckets,
    })


__all__ = (
    "AgentWorkflowArc", "AgentWorkflowEndpoint", "AgentWorkflowExecution",
    "AgentWorkflowGraph",
    "AgentWorkflowNode", "AgentWorkflowPort", "TEXT_SCHEMA",
    "WORKFLOW_GRAPH_COMPONENT_KEY", "WORKFLOW_GRAPH_COMPONENT_V1_KEY",
    "WORKFLOW_GRAPH_COMPONENT_V3_KEY",
    "WORKFLOW_GRAPH_CONFIG_SCHEMA", "WORKFLOW_GRAPH_CONFIG_SCHEMA_ID",
    "WORKFLOW_GRAPH_CONFIG_SCHEMA_V1",
    "WORKFLOW_GRAPH_CONFIG_SCHEMA_V1_ID", "build_agent_workflow_module",
    "WORKFLOW_GRAPH_CONFIG_SCHEMA_V3", "WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID",
    "lower_agent_workflow_graph", "WORKFLOW_GRAPH_COMPONENT_V4_KEY",
    "WORKFLOW_GRAPH_CONFIG_SCHEMA_V4", "WORKFLOW_GRAPH_CONFIG_SCHEMA_V4_ID",
)
