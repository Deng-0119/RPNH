"""Explicit, public-only Office comparison condition; baseline remains opt-out.

This is an application contract, not a hidden-rubric repair or kernel policy.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Mapping

from .constants import OFFICE_EFFECTS
from .jsonio import dumps
from .task_view import PublicTask, assert_no_hidden_keys

CONDITION_ID = "office-public-discovery-workflow-v1"
READBACK_CONDITION_ID = "office-public-discovery-readback-v2"
COMPARISON_CONDITIONS = (CONDITION_ID, READBACK_CONDITION_ID)
# Exact archived 7267c12 identity for read-only saved-v1 inspection.
# Newly loaded implementation files always report their actual identity.
HISTORICAL_V1_IMPLEMENTATION = {'files_sha256': {'comparison_condition.py': '440d3849502e27470201421207999935ab57dbf8c5f71b4df582ab22d894c630', 'comparison_workflow.py': 'd52fab44a965f6d1f35cee836c6647cf500368beb858a01132b1c7586a345872', 'knowledge_discovery.py': '6f5d50467f68bfa9daea1582dda8c02e569eebff649f0684b033b9abdeab8ed3', 'workflow.py': '256f54847755333d30a48b2fba13cf7b69c2a805cafe19d5966329694582bf27', 'task_view.py': '433cbb7769681719e430fc7ed15bb08ee4ebdde7cad968215ecfe9b3f398cce9', 'local_driver.py': '4da95c1087cab8761f3a228bc64758422ecba3acade4ec56c1d440b6593ed2f4', 'native_plugin.py': 'f469ece9954097764f8b21c2acc7ff02d6ff8fa8bfcecf1d5e48cf813673a24d', 'comparison_plugin.py': 'd317bc09266d3107aec4cdcfc623c28b30b590d0147d4000ad08481b5a36e658', 'adapter.py': 'a4aa82251c0a9f6ef5bf005500b0f0b2bd3c7262fd13f969d1dbd4f9a9e8448c'}, 'bundle_sha256': '81f81ae4affbde8b6c92cf09bf1a88dacc56c5ab46d584ad5f49dc0ef03680b3'}
DISCOVERY_REVISION = "kb-title-key-discovery-v1"
WORKFLOW_REVISION = "public-hub-evidence-execute-verify-v1"
PROMPT_REVISION = "public-evidence-and-readback-v1"
WRITE_GATE_REVISION = "retrieved-policy-reference-v1"
DISCOVERY_TOOL = "discover_knowledge_queries"
POLICY_ARGUMENT = "policy_evidence"
# Public campaign goals request a policy-dependent action bundle. The legacy
# off-t2 probe is intentionally excluded until its public dependency is reviewed.
COMPARISON_TASKS = frozenset({"off-t1", "off-t3", "off-t4", "off-t5", "off-t6"})
DISCOVERY_DECLARATION = {
    "name": DISCOVERY_TOOL,
    "description": (
        "Discover exact KB query keys by words in public article titles. Returns only "
        "reachable first-article metadata (article_id, title, audience, query_key), "
        "never article bodies. Use search_knowledge_base afterwards. Audience is an "
        "optional exact metadata filter, not an access authorization; the original "
        "search tool does not enforce audience. Empty topics are rejected."),
    "params": {
        "topic": "(str) One or more literal words from the requested public policy topic; all words must occur in the title.",
        "audience": "(str) Optional exact article audience metadata filter; empty means no filter. This is not an ACL.",
        "offset": "(int) Zero-based metadata page offset, default 0.",
        "limit": "(int) Page size from 1 to 20, default 10.",
    },
}
POLICY_DESCRIPTION = (
    '(str) Required comparison-only JSON object: {"status":"ready",'
    '"reason":"public policy basis", "references":[{"query_key":"a key actually retrieved",'
    '"article_id":"the returned article ID"}]}. Every reference must match an earlier '
    'successful search_knowledge_base return in this run. Missing, unavailable or empty '
    'policy evidence blocks this write. The host removes this envelope before original '
    'business dispatch. Retrieval does not prove relevance, truth or model consumption.')


def selected_condition(config: Mapping) -> str | None:
    value = config.get("configuration_condition")
    if value not in (None, *COMPARISON_CONDITIONS):
        raise ValueError("unsupported Office configuration condition")
    if value is not None and config.get("condition_id") != value:
        raise ValueError("comparison configuration requires its distinct condition_id")
    if value is None and config.get("condition_id") in COMPARISON_CONDITIONS:
        raise ValueError("comparison condition_id requires explicit configuration_condition")
    if value and config.get("task_id") not in COMPARISON_TASKS:
        raise ValueError("comparison supports only the five public policy-dependent campaign bundles")
    return value


def condition_of(task: PublicTask | Mapping) -> str | None:
    view = task.as_dict() if isinstance(task, PublicTask) else task
    value = view.get("configuration_condition")
    if value not in (None, *COMPARISON_CONDITIONS):
        raise ValueError("unsupported public Office condition")
    if value is not None:
        if view.get("task_id") not in COMPARISON_TASKS:
            raise ValueError("comparison supports only the five public policy-dependent campaign bundles")
        assert_no_hidden_keys(view)
    return value


def effects_for(condition: str | None) -> dict[str, str]:
    if condition not in (None, *COMPARISON_CONDITIONS):
        raise ValueError("unsupported Office condition")
    return {**OFFICE_EFFECTS, **({DISCOVERY_TOOL: "external_read"} if condition else {})}


def extend_public_task(task: PublicTask, condition: str | None) -> PublicTask:
    if condition not in (None, *COMPARISON_CONDITIONS):
        raise ValueError("unsupported Office condition")
    if condition_of(task) is not None:
        raise ValueError("public task condition already applied")
    if condition is None:
        return task
    view = task.as_dict()
    if view.get("task_id") not in COMPARISON_TASKS:
        raise ValueError("comparison policy dependency is defined only for the five published campaign tasks")
    if {row["name"] for row in view["tools"]} != set(OFFICE_EFFECTS):
        raise ValueError("comparison requires the original public Office tool catalog")
    origin = {"public_input_sha256": digest(view), "public_catalog_sha256": digest(view["tools"])}
    for tool in view["tools"]:
        if OFFICE_EFFECTS[tool["name"]] == "external_write":
            if POLICY_ARGUMENT in tool["params"]:
                raise ValueError("comparison parameter collides with public upstream parameter")
            tool["params"][POLICY_ARGUMENT] = POLICY_DESCRIPTION
    view["tools"].append(copy.deepcopy(DISCOVERY_DECLARATION))
    view["configuration_condition"] = condition
    view["condition_origin"] = origin
    view["public_workflow_contract"] = {
        "revision": WORKFLOW_REVISION,
        "communication": "hub-spoke-only; no direct specialist handoffs",
        "policy_requirement": "For the five supported public policy-dependent campaign bundles, each external write requires earlier successful public KB retrieval and a ready policy_evidence envelope. This conservative bundle-level dependency is not a universal Office authorization rule.",
        "role_scope": "Use only original public role descriptions; no hidden role allowlists are inferred.",
        "missing_policy": "Publish blocked_policy; do not invent policy, queue, routing, thresholds or completed state.",
        "verification": "A returned write is separate from state readback and from confirmed model consumption.",
    }
    if condition == READBACK_CONDITION_ID:
        view["public_workflow_contract"].update(
            revision="public-hub-evidence-readback-v2",
            verification="Registered return, verifiable subsequent registered-request inclusion and observed readback are separate; semantic use or influence remains unknown.",
            managed_output_reader="read_managed_output/v1")
    assert_no_hidden_keys(view)
    return PublicTask(dumps(view))


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def implementation_identity() -> dict:
    """Content identity survives source copies; no machine paths are recorded."""
    from pathlib import Path
    directory = Path(__file__).parent
    names = ("comparison_condition.py", "comparison_workflow.py", "knowledge_discovery.py",
             "workflow.py", "task_view.py", "local_driver.py", "native_plugin.py", "comparison_plugin.py", "adapter.py")
    files = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}
    return {"files_sha256": files, "bundle_sha256": digest(files)}


def condition_manifest(task: PublicTask, graph, *, bindings=None, max_model_calls=None) -> dict:
    """Hash declared inputs; never imply an actual registered provider request."""
    if condition_of(task) not in COMPARISON_CONDITIONS:
        raise ValueError("condition manifest requires explicit comparison task")
    view = task.as_dict()
    graph_doc = graph.to_dict()
    count = len(graph.nodes)
    if max_model_calls is not None and (type(max_model_calls) is not int or max_model_calls < count):
        raise ValueError("model-call limit is smaller than the comparison workflow node count")
    per_node = None if max_model_calls is None else max_model_calls // count
    return {
        "schema_version": "rpnh-ha/configuration-condition/v1",
        "condition_id": condition_of(task),
        "discovery_revision": DISCOVERY_REVISION,
        "workflow_revision": ("public-hub-evidence-readback-v2" if condition_of(task) == READBACK_CONDITION_ID else WORKFLOW_REVISION),
        "prompt_revision": ("registered-request-inclusion-v2" if condition_of(task) == READBACK_CONDITION_ID else PROMPT_REVISION),
        "write_gate_revision": WRITE_GATE_REVISION,
        "origin": view["condition_origin"],
        "implementation": implementation_identity(),
        "public_input_sha256": digest(view),
        "public_catalog_sha256": digest(view["tools"]),
        "prompt_sha256": digest({"goal": view["goal"], "nodes": {n.node_id: n.instruction for n in graph.nodes}}),
        "graph_sha256": digest(graph_doc),
        "bindings_sha256": None if bindings is None else digest(bindings),
        "node_count": count,
        "model_budget": {"requested_total": max_model_calls,
                         "per_node": {n.node_id: per_node for n in graph.nodes},
                         "effective_total": None if per_node is None else count * per_node,
                         "unallocated_remainder": None if per_node is None else max_model_calls - count * per_node},
        "gate_scope": "retrieved reference plus declared readiness; not policy truth, entitlement or model-consumption proof",
        "registered_provider_request_tested": False,
        "baseline_comparable": False,
        "published_results_changed": False,
    }


def completion_evidence(*, policy_status: str, state_observed: bool | None,
                        tool_return_observed: bool, model_consumption_confirmed: bool,
                        readback_observed: bool, readback_consumption_confirmed: bool) -> dict:
    """Evidence classification only; callers must supply independently proved facts.

    This is not a scorer and never reads private Bank state into a model prompt.
    """
    if policy_status not in {"ready", "unavailable", "unknown"}:
        raise ValueError("invalid policy evidence status")
    flags = (tool_return_observed, model_consumption_confirmed,
             readback_observed, readback_consumption_confirmed)
    if any(type(value) is not bool for value in flags) or not (state_observed is None or type(state_observed) is bool):
        raise ValueError("evidence flags must be explicit booleans; state may be unknown")
    if model_consumption_confirmed and not tool_return_observed:
        raise ValueError("tool consumption needs its tool return")
    if readback_consumption_confirmed and not readback_observed:
        raise ValueError("readback consumption needs a readback")
    verified = (policy_status == "ready" and state_observed is True
                and tool_return_observed and model_consumption_confirmed
                and readback_observed and readback_consumption_confirmed)
    status = ("verified_complete" if verified else "blocked_policy"
              if policy_status != "ready" else "not_completed"
              if state_observed is False else "unverified")
    return {"status": status, "state_observed": state_observed,
            "tool_return_observed": tool_return_observed,
            "model_consumption_confirmed": model_consumption_confirmed,
            "readback_observed": readback_observed,
            "readback_consumption_confirmed": readback_consumption_confirmed,
            "completion_claim_supported": verified}


def plan_condition(public: Mapping, config: Mapping) -> dict:
    """Offline declaration plan; neither plugin loading nor runtime execution."""
    from pathlib import Path
    from .driver_contract import DriverRequest, live_limits_from_config
    from .local_driver import managed_bindings
    from .workflow import build_business_workflow
    condition = selected_condition(config)
    if condition is None:
        raise ValueError("plan-condition requires explicit comparison opt-in")
    assert_no_hidden_keys(public)
    if public.get("task_id") != config.get("task_id"):
        raise ValueError("configuration and public task identity disagree")
    task = PublicTask(dumps(public))
    if condition_of(task) is None:
        task = extend_public_task(task, condition)
    graph, roles = build_business_workflow(task)
    limits = live_limits_from_config(dict(config))
    request = DriverRequest(task, task.as_dict()["goal"], Path("unused-run"),
                            Path("unused-profile"), {}, limits, condition)
    bindings = managed_bindings(request, roles)
    return {"schema_version": "rpnh-ha/condition-plan/v1",
            "public_input": task.as_dict(), "graph": graph.to_dict(),
            "roles_by_node": roles, "managed_bindings": bindings,
            "condition_manifest": condition_manifest(task, graph, bindings=bindings,
                                                       max_model_calls=limits.max_model_calls),
            "execution_started": False, "actual_model_calls": 0}


def saved_condition_record(run_root) -> dict:
    """Preserve condition provenance through export/scoring; fail on relabelling."""
    from pathlib import Path
    from .jsonio import read
    from .workflow import build_business_workflow
    root = Path(run_root)
    public = read(root / "public_input.json")
    protocol = read(root / "protocol.json")
    status = read(root / "run_status.json")
    condition = condition_of(public)
    if any(row.get("configuration_condition") != condition for row in (protocol, status)):
        raise ValueError("saved public/protocol/status comparison conditions disagree")
    if condition is None:
        return {}
    if any(row.get("condition_id") != condition for row in (protocol, status)):
        raise ValueError("saved comparison condition_id is missing or changed")
    task = PublicTask(dumps(public))
    graph, _ = build_business_workflow(task)
    expected = condition_manifest(task, graph,
                                  max_model_calls=protocol["limits"]["max_model_calls"])
    declared = protocol.get("condition_manifest")
    historical_v1 = (condition == CONDITION_ID and isinstance(declared, Mapping)
                     and declared.get("implementation") == HISTORICAL_V1_IMPLEMENTATION)
    if historical_v1:
        expected["implementation"] = copy.deepcopy(HISTORICAL_V1_IMPLEMENTATION)
    if declared != expected:
        raise ValueError("saved comparison declaration hashes no longer match public inputs/graph")
    launched_path = root / "configuration_condition.json"
    launched = read(launched_path) if launched_path.is_file() else None
    if launched is not None:
        # The protocol predates binding projection and keeps its null digest.
        # A launch must match bindings rebuilt from the saved public task.
        expected_launch = plan_condition(public, {
            "task_id": public["task_id"],
            "configuration_condition": condition,
            "condition_id": condition,
            "limits_per_run": protocol["limits"],
        })["condition_manifest"]
        if historical_v1:
            expected_launch["implementation"] = copy.deepcopy(HISTORICAL_V1_IMPLEMENTATION)
        if any(launched.get(key) != value for key, value in expected_launch.items()):
            raise ValueError("launch and protocol comparison declarations disagree")
    return {"configuration_condition": condition, "condition_id": condition,
            "condition_manifest": declared, "launch_condition_manifest": launched,
            "baseline_comparable": False,
            "scorer_scope_note": "Original pinned scorer unchanged; new discovery/write-envelope/graph condition is not baseline-calibrated."}


def comparison_evidence_report(observations: list[dict], *, state_snapshot_available: bool,
                               condition_id: str = CONDITION_ID) -> dict:
    """Keep trace-return, registered input and independent state surfaces apart."""
    import json
    if condition_id not in COMPARISON_CONDITIONS:
        raise ValueError("comparison evidence requires its explicit condition identity")
    rows = []
    for observation in observations:
        if observation.get("surface") != "tool_call":
            continue
        raw, data = observation["raw_event"], observation["data"]
        returned = raw.get("rpnh_phase") == "returned"
        visible = raw.get("model_visible_result")
        consumed = bool(returned and isinstance(visible, dict)
                        and visible.get("kind") == "acknowledged_registered_llm_prompt_result/v1"
                        and visible.get("evidence"))
        blocked = False
        if returned:
            try:
                result = json.loads(data["tool_result"])
            except (ValueError, TypeError):
                result = None
            blocked = (isinstance(result, dict) and result.get("status") == "blocked_policy"
                       and result.get("write_dispatched") is False)
        node = observation.get("agent_id", "")
        rows.append({"node": node, "tool_name": data["tool_name"],
                     "tool_return_observed": returned,
                     "model_consumption_confirmed": consumed,
                     "blocked_policy_before_business_dispatch": blocked,
                     "verification_phase_read": bool(node.startswith("verify_")
                         and effects_for(CONDITION_ID).get(data["tool_name"]) == "external_read"),
                     "state_fields_verified": None})
        if condition_id == READBACK_CONDITION_ID:
            rows[-1].pop("model_consumption_confirmed")
            rows[-1].update(registered_request_projection_available=consumed,
                            provider_submission="unknown", semantic_use="unknown")
    return {"schema_version": ("rpnh-ha/comparison-evidence/v2" if condition_id == READBACK_CONDITION_ID
                               else "rpnh-ha/comparison-evidence/v1"), "condition_id": condition_id,
            "state_snapshot_available": state_snapshot_available,
            "state_snapshot_is_model_input": False,
            "tool_evidence": rows, "business_completion_verified": None,
            "note": "A phase read is only a readback candidate. This sidecar does not judge field equality, policy relevance or final-answer truth; model reports and snapshot state remain separate."}
