"""Public-task-only workflow construction for the native three-role driver."""
from __future__ import annotations

import re
from typing import Mapping

from .task_view import PublicTask


NODE_ROLE_ORDER = ("hub_plan", "specialist_1", "specialist_2", "hub_finalize")
WORKFLOW_BUILTIN_TOOLS = ("complete_interaction", "read_file", "write_file")


def _instruction(*, role: dict, responsibility: str) -> str:
    """Keep upstream public role text verbatim inside a fixed host envelope."""
    return "\n".join((
        responsibility,
        "BUSINESS_ROLE: " + role["role"],
        "ORIGINAL_DESCRIPTION:",
        role["description"],
        "ORIGINAL_SYSTEM_PROMPT:",
        role["system_prompt"],
        "Use only the public task, actual delivered workflow inputs, and tools visible to this role.",
        "Do not assume a hidden scoring path or hidden access policy.",
        "Execution is turn-bounded; one response may request multiple independent visible tools.",
        "A workflow product write must use outcome_id \"complete\" and the exact declared output_port_id.",
        "Treat repeated no-match results as evidence; do not spend the final available turn on a semantically similar search.",
        "Once evidence is sufficient, or the remaining evidence is unavailable, publish a factual report with explicit limitations using write_file and call complete_interaction in the same response.",
    ))


def build_business_workflow(
    task: PublicTask, *,
    tool_ids_by_node: Mapping[str, tuple[str, ...]] | None = None,
):
    """Build a generic public hub/fan-out/join workflow and its role map.

    The topology is derived only from the declared hub role and public role
    inventory. It does not inspect grader rules, useful tools, checkpoints, or
    a golden action path.
    """
    from .comparison_condition import condition_of
    if condition_of(task) is not None:
        from .comparison_workflow import build_comparison_workflow
        return build_comparison_workflow(task, tool_ids_by_node=tool_ids_by_node)
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowArc as Arc,
        AgentWorkflowEndpoint as Endpoint,
        AgentWorkflowExecution as Execution,
        AgentWorkflowGraph,
        AgentWorkflowNode as Node,
        AgentWorkflowPort as Port,
    )

    view = task.as_dict()
    roles = {row["role"]: row for row in view["agents"]}
    hub_role = view["hub_role"]
    from .office_cases import plugin_roles_for_task
    plugin_roles_for_task(view)  # public inventory only
    specialists = [role for role in view["agents"] if role["role"] != hub_role]
    node_order = ("hub_plan", *(f"specialist_{i}" for i in range(1, len(specialists) + 1)), "hub_finalize")

    configured_tools = dict(tool_ids_by_node or {})
    if set(configured_tools) - set(node_order):
        raise ValueError("tool mapping names an unknown workflow node")

    def execution(node_id: str) -> Execution:
        # delegate_leaf is intentionally absent: managed business tools are
        # bound to these workflow firings, not to a delegated child session.
        # Advertising delegation here exposes a path that cannot execute the
        # advertised managed tools and fails only after a provider call.
        tools = configured_tools.get(node_id, WORKFLOW_BUILTIN_TOOLS)
        return Execution(role="actor", tools=tools, profile_id=None)

    hub = roles[hub_role]
    nodes = [Node(
        "hub_plan",
        _instruction(role=hub, responsibility=(
            "[rpnh-ha:hub-plan] Coordinate the public task. Produce one neutral "
            + ("work plan for both declared specialists without prescribing a hidden "
               if len(specialists) == 2 else
               "work plan for all declared specialists without prescribing a hidden ")
            + "tool sequence.")),
        (Port("request", "task"),), (Port("plan", "coordination_plan"),),
        execution("hub_plan"),
    )]
    arcs = []
    roles_by_node = {"hub_plan": hub_role, "hub_finalize": hub_role}
    for i, specialist in enumerate(specialists, 1):
        node_id = f"specialist_{i}"
        report_id = f"specialist_{i}_report"
        nodes.append(Node(
            node_id,
            _instruction(role=specialist, responsibility=(
                f"[rpnh-ha:specialist-{i}] Investigate your declared responsibility, "
                "choose any needed visible tools efficiently, and reserve capacity "
                "to publish a factual report for the coordinator.")),
            (Port("plan", "coordination_plan"),), (Port("report", report_id),),
            execution(node_id),
        ))
        arcs.extend((
            Arc(f"plan_to_specialist_{i}", Endpoint("hub_plan", "plan"), Endpoint(node_id, "plan")),
            Arc(f"specialist_{i}_to_hub", Endpoint(node_id, "report"), Endpoint("hub_finalize", report_id)),
        ))
        roles_by_node[node_id] = specialist["role"]
    nodes.append(Node(
        "hub_finalize",
        _instruction(role=hub, responsibility=(
            "[rpnh-ha:hub-finalize] Read " + ("both" if len(specialists) == 2 else "all")
            + " delivered specialist reports and "
            "produce the official final user answer. Do not invent missing tool "
            "results or undisclosed policy.")),
        tuple(Port(f"specialist_{i}_report", f"specialist_{i}_report") for i in range(1, len(specialists) + 1)),
        (Port("result", "final_answer"),), execution("hub_finalize"),
    ))
    # Arc order remains legacy-compatible for the two-specialist case.
    arcs.sort(key=lambda arc: (0 if arc.source.node_id == "hub_plan" else 1, arc.source.node_id, arc.target.node_id))
    graph = AgentWorkflowGraph(tuple(nodes), tuple(arcs), Endpoint("hub_plan", "request"), Endpoint("hub_finalize", "result"))
    return graph, roles_by_node


ARGUMENT_SCHEMA_REVISION = "public-descriptions-explicit-types-v3"
_EXPLICIT_TYPE = re.compile(r"^\((str|string|int|float|bool)\)\s*")
_JSON_TYPES = {"str": "string", "string": "string", "int": "integer",
               "float": "number", "bool": "boolean"}


def public_argument_schema(tool: dict) -> dict:
    """Preserve published descriptions; convert only explicit type prefixes.

    Requiredness remains unchanged from the experiment baseline (no required
    list). Prose examples are NOT inferred as enums, defaults, permissions or
    task-specific validators. Unknown type notation retains its description.
    """
    params = tool.get("params")
    if (not isinstance(params, dict)
            or any(not isinstance(name, str) or not name
                   or not isinstance(description, str)
                   for name, description in params.items())):
        raise ValueError("public tool params must map nonempty names to text")
    properties = {}
    for name, description in params.items():
        projected = {"description": description}
        match = _EXPLICIT_TYPE.match(description)
        if match:
            projected["type"] = _JSON_TYPES[match.group(1)]
        properties[name] = projected
    return {"type": "object", "properties": properties,
            "additionalProperties": False}


__all__ = (
    "NODE_ROLE_ORDER", "WORKFLOW_BUILTIN_TOOLS", "build_business_workflow",
    "public_argument_schema", "ARGUMENT_SCHEMA_REVISION",
)
