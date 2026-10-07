"""Versioned hub-mediated evidence, execution and verification application graph."""
from __future__ import annotations

from .comparison_condition import CONDITION_ID, condition_of
from .office_cases import plugin_roles_for_task
from .workflow import WORKFLOW_BUILTIN_TOOLS


COMMON = (
    "Use only the public goal, original role text, delivered workflow inputs and visible tools. "
    "Do not infer hidden rules, answers or scoring paths. Respect your original public role. "
    "All specialist communication goes through the hub; forward the minimum necessary facts, not unrelated personal data. "
    "Read each delivered input with read_file before using it. All external writes in this condition "
    "require the public policy_evidence argument, referencing an actual earlier KB return. "
    "If policy is unavailable, ambiguous or outside your responsibility, report blocked_policy or not_applicable; "
    "do not invent a policy, routing destination, threshold or completed action. "
    "For each requested outcome distinguish actual tool-return evidence, observed state/readback, "
    "and confirmed subsequent model-input consumption. Unknown evidence stays unknown. "
    "Read tool results on a subsequent turn before relying on them in a report; do not issue a tool "
    "call and claim its result in the same response. A tool return alone does not prove final state. "
    "Finish even when blocked by publishing a factual JSON report with status, evidence references, "
    "policy references and limitations. Use write_file with outcome_id complete and the exact "
    "declared output_port_id, then complete_interaction. Workflow terminal is not business success."
)


def build_comparison_workflow(task, *, tool_ids_by_node=None):
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowArc as Arc, AgentWorkflowEndpoint as Endpoint,
        AgentWorkflowExecution as Execution, AgentWorkflowGraph as Graph,
        AgentWorkflowNode as Node, AgentWorkflowPort as Port,
    )
    if condition_of(task) != CONDITION_ID:
        raise ValueError("comparison graph requires explicit public condition")
    view = task.as_dict()
    plugin_roles_for_task(view)
    hub = next(role for role in view["agents"] if role["role"] == view["hub_role"])
    specialists = [role for role in view["agents"] if role != hub]
    node_ids = {"hub_plan", "hub_coordinate", "hub_review", "hub_finalize"} | {
        f"{phase}_{i}" for phase in ("evidence", "execute", "verify")
        for i in range(1, len(specialists) + 1)}
    configured = dict(tool_ids_by_node or {})
    if set(configured) - node_ids:
        raise ValueError("tool mapping names an unknown comparison node")
    nodes, arcs, roles = [], [], {}

    def node(name, role, instruction, inputs, output, artifact):
        nodes.append(Node(name, "\n".join((
            "[rpnh-ha:" + name + "] " + instruction,
            "BUSINESS_ROLE: " + role["role"],
            "ORIGINAL_DESCRIPTION:", role["description"],
            "ORIGINAL_SYSTEM_PROMPT:", role["system_prompt"], COMMON)),
            tuple(Port(*item) for item in inputs), (Port(output, artifact),),
            Execution(role="actor", tools=configured.get(name, WORKFLOW_BUILTIN_TOOLS))))
        roles[name] = role["role"]

    def arc(source, source_port, target, target_port):
        arcs.append(Arc(source + "_to_" + target, Endpoint(source, source_port), Endpoint(target, target_port)))

    node("hub_plan", hub, "Plan public evidence collection for each declared specialist. No business writes.",
         [("request", "task")], "plan", "evidence_plan")
    for i, role in enumerate(specialists, 1):
        node(f"evidence_{i}", role,
             "Collect read-only facts relevant to your public responsibility. The policy-responsible role "
             "uses discover_knowledge_queries by public title/topic, then search_knowledge_base with a discovered "
             "exact key. Report the actual query_key/article_id and relevant policy text. Metadata alone is "
             "not policy. Do not perform writes in this phase. Other roles collect only their own required evidence.",
             [("plan", "evidence_plan")], "report", f"evidence_{i}")
        arc("hub_plan", "plan", f"evidence_{i}", "plan")
        arc(f"evidence_{i}", "report", "hub_coordinate", f"evidence_{i}")
    node("hub_coordinate", hub,
         "Read every specialist evidence report. Coordinate only policy-supported actions, naming the "
         "responsible public role and required readback fields. Relay relevant policy query_key/article_id "
         "references and public rationale. Missing, conflicting or ambiguous policy means status blocked_policy. "
         "Never treat an empty completed report as readiness. No business writes.",
         [(f"evidence_{i}", f"evidence_{i}") for i in range(1, len(specialists) + 1)],
         "coordination", "coordinated_actions")
    for i, role in enumerate(specialists, 1):
        node(f"execute_{i}", role,
             "Read the hub coordination. Execute only actions within your original public role when policy "
             "evidence is ready. For each write include policy_evidence JSON with status ready, a nonempty "
             "reason, and references containing query_key and article_id from real retrieved policies. "
             "If coordination is blocked or no relevant policy is available, perform no write and report "
             "blocked_policy. Record actual returned write results and newly created IDs without declaring "
             "unread fields verified. Do not repeat writes to recover unknown outcomes.",
             [("coordination", "coordinated_actions")], "report", f"execution_{i}")
        arc("hub_coordinate", "coordination", f"execute_{i}", "coordination")
        arc(f"execute_{i}", "report", "hub_review", f"execution_{i}")
    node("hub_review", hub,
         "Read every execution report. Preserve blocked/unknown outcomes, actual returned IDs and policy "
         "references. Coordinate independent read-only state checks by each responsible specialist using "
         "the actual returned identifiers and requested fields. Do not substitute a success receipt for readback. "
         "Do not repair missing work yourself or request replay of an outcome-unknown write.",
         [(f"execution_{i}", f"execution_{i}") for i in range(1, len(specialists) + 1)],
         "verification", "verification_plan")
    for i, role in enumerate(specialists, 1):
        node(f"verify_{i}", role,
             "Read the hub verification plan. Use available public read tools to verify actual resulting "
             "fields within your public responsibility. Consume returned readbacks on a subsequent turn. "
             "Report per-field observed state, write-result availability and readback availability separately. "
             "If no suitable public readback exists, report unverified; never read backend SQL or invent values. "
             "No business writes or retries are permitted in this phase.",
             [("verification", "verification_plan")], "report", f"verification_{i}")
        arc("hub_review", "verification", f"verify_{i}", "verification")
        arc(f"verify_{i}", "report", "hub_finalize", f"verification_{i}")
    node("hub_finalize", hub,
         "Read all verification reports. Produce the official final answer with verified completions, "
         "blocked_policy, not_completed and unverified outcomes clearly separated. Cite actual tool and "
         "readback evidence. A model-authored report is not independent proof of registered model consumption; "
         "that is checked by the evidence exporter. No business writes or invented completion claims.",
         [(f"verification_{i}", f"verification_{i}") for i in range(1, len(specialists) + 1)],
         "result", "final_answer")
    return Graph(tuple(nodes), tuple(arcs), Endpoint("hub_plan", "request"), Endpoint("hub_finalize", "result")), roles
