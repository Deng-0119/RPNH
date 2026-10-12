"""Declarations and exact, independent contributions for the co-build example.

Contract parsing: the node instruction contains one line beginning with
``COBUILD_CONTRACT_JSON:`` followed by a JSON object. The SCRIPTED driver reads
that line only from actual request system messages. Port contracts below are
human-readable JSON shape requirements; stock ports carry registered text.
``contribution_document`` takes a ModuleDeclaration.to_dict() document and
changes only the target role's operation node_synopsis.

The collaboration plain-proposal API rejects nonempty designer_constraints.
graph() is an authoring description only. The executable artifact is explicitly
assembled from two stock single-agent Modules, using ordinary operation
components and a result-to-request link. It never strips graph constraints or
uses a graph lowerer through the plain API. Each role keeps its real AgentLoop.
"""
from __future__ import annotations

from copy import deepcopy
import json

from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.agent_workflows import (
    AgentWorkflowArc, AgentWorkflowEndpoint, AgentWorkflowExecution,
    AgentWorkflowGraph, AgentWorkflowNode, AgentWorkflowPort,
)

MODEL_CONDITION = "rpnh-agent-cobuild-scripted-v1"
TOOLS = tuple(sorted({"read_file", "write_file", "complete_interaction"}))
CONTRACTS = {
    "curator": {
        "schema": "agent_cobuild_contract/v1", "role": "curator",
        "retain_source_lineage": False,
    },
    "auditor": {
        "schema": "agent_cobuild_contract/v1", "role": "auditor",
        "reconcile_sources": False,
    },
}
CONTRIBUTIONS = {
    "curator": {**CONTRACTS["curator"], "retain_source_lineage": True},
    "auditor": {**CONTRACTS["auditor"], "reconcile_sources": True},
}


def _instruction(contract: dict) -> str:
    if contract["role"] == "curator":
        text = (
            "Curator: read the original located JSON input with read_file. "
            "Input contract: records is an array of {id, category, minutes}; "
            "IDs are unique nonempty strings, categories are nonempty strings, "
            "minutes are nonnegative integers (not booleans). Reject invalid input. "
            "Output contract: register agent_cobuild_handoff/v1 JSON text with "
            "input_record_count, lineage_retained, records in original order, and "
            "totals {record_count, total_minutes, by_category}, where by_category "
            "contains sorted {category, record_count, total_minutes} rows. "
        )
        text += (
            "Retain each original id as source_id and its zero-based source_index "
            "alongside category and minutes in every normalized record. Set "
            "lineage_retained=true; every input record must remain traceable."
            if contract["retain_source_lineage"] else
            "Normalized records contain category and minutes only; set "
            "lineage_retained=false."
        )
    else:
        text = (
            "Auditor: read the registered curator handoff with read_file. "
            "Input contract: agent_cobuild_handoff/v1 JSON text with records, "
            "lineage_retained, input_record_count and totals. Recompute counts "
            "and minutes from those records; reject inconsistent handoffs. "
            "Output contract: register agent_cobuild_report/v1 JSON text with "
            "totals, lineage_retained, source_reconciliation_required and checks. "
            "Always report input_count_matches, record_count_matches, "
            "category_totals_match and total_minutes_match checks. "
        )
        text += (
            "Require lineage_retained=true and each record's source_id and "
            "source_index. Reconcile every original source exactly once, with "
            "unique IDs and complete ordered indices. Add source_reconciliation "
            "rows {source_id, source_index, category, minutes}; add count_checks "
            "{input_record_count, normalized_record_count, reconciled_source_count, "
            "reported_record_count, counts_match} and explicit unique_source_ids "
            "and source_indices_complete checks. Set source_reconciliation_required=true."
            if contract["reconcile_sources"] else
            "A handoff without source lineage is valid. Set "
            "source_reconciliation_required=false; do not claim per-source checks."
        )
    return (
        text + " Use only read_file, write_file and complete_interaction. "
        "Read first; compute from the tool return, write the exact semantic "
        "output port, then complete. This is a SCRIPTED protocol demonstration, "
        "not evidence of model cognition.\nCOBUILD_CONTRACT_JSON: "
        + json.dumps(contract, sort_keys=True, separators=(",", ":"))
    )


def graph() -> AgentWorkflowGraph:
    execution = AgentWorkflowExecution(tools=TOOLS)
    return AgentWorkflowGraph(
        nodes=(
            AgentWorkflowNode(
                "curator", _instruction(CONTRACTS["curator"]),
                (AgentWorkflowPort("records", "source_records"),),
                (AgentWorkflowPort("handoff", "normalized_records"),), execution),
            AgentWorkflowNode(
                "auditor", _instruction(CONTRACTS["auditor"]),
                (AgentWorkflowPort("handoff", "normalized_records"),),
                (AgentWorkflowPort("report", "reconciliation_report"),), execution),
        ),
        arcs=(AgentWorkflowArc(
            "curator_to_auditor", AgentWorkflowEndpoint("curator", "handoff"),
            AgentWorkflowEndpoint("auditor", "handoff")),),
        ingress=AgentWorkflowEndpoint("curator", "records"),
        egress=AgentWorkflowEndpoint("auditor", "report"),
    )


def module():
    """Compose two genuine stock single-agent declarations for the plain API."""
    instructions = {node.node_id: node.instruction for node in graph().nodes}
    pieces = [build_agent_task_module(
        (AgentStage(role, instructions[role]),), max_attempts_per_stage=4,
    ).to_dict() for role in ("curator", "auditor")]
    for piece in pieces:
        component = piece["components"][0]
        role = component["name"]
        operation = component["operations"][0]
        operation["tools"] = list(TOOLS)
        operation["budget_binding"]["budget_scope"] = role
        piece["budget_buckets"][0]["budget_scope"] = role
    document = deepcopy(pieces[0])
    document["name"] = "AgentCobuild"
    document["components"] = [piece["components"][0] for piece in pieces]
    document["budget_buckets"] = [piece["budget_buckets"][0] for piece in pieces]
    document["links"] = [{
        "source": {"component": "curator", "port": "result"},
        "target": {"component": "auditor", "port": "request"},
    }]
    document["exit"] = pieces[1]["exit"]
    document["terminal"] = pieces[1]["terminal"]
    return ModuleDeclaration.from_dict(document)


def contribution_document(base: dict, kind: str) -> dict:
    """Return one exact role contribution without rebuilding the other node.

    Apply curator first, then auditor to that accepted document. Auditor's new
    contract intentionally requires the curator contribution's lineage output.
    No permissions, topology, tools, budgets or display labels are changed.
    """
    if kind not in CONTRIBUTIONS:
        raise ValueError("contribution kind must be curator or auditor")
    result = deepcopy(base)
    components = [c for c in result["components"] if c["name"] == kind]
    if len(components) != 1:
        raise ValueError("expected one exact role component")
    operations = [o for o in components[0]["operations"] if o["name"] == "run"]
    if len(operations) != 1:
        raise ValueError("expected exact role run operation")
    operations[0]["config"]["node_synopsis"] = _instruction(CONTRIBUTIONS[kind])
    return result
