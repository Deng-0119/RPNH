"""RPNH owns all agent/model execution, managed admission and Registry writes."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from .io import append, file_sha, now, write_new
from .plugin import bindings, configuration
from .upstream import split_prompt

NATIVE_INSTRUCTION = """Complete the supplied business task using the visible AutomationBench tools.
The tool output envelope's raw_result field is the exact upstream response text.
Use only the public task, returned tool results, and your registered workspace.
The environment has no interactive user; do not wait for additional user input.
When done, write a factual final report with the native write_file tool, using
the exact outcome_id and output_port_id declared by FRAMEWORK SYSTEM INITIALIZATION;
for this workflow the declared bundle is complete=team.result. Then call
complete_interaction as the final ordered tool call.
Do not claim an action succeeded unless its tool response supports that claim.
This final report closes the native task; it is not a substitute for required business actions.
""".strip()


def graph_for(messages, *, configuration_condition=None):
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowGraph, AgentWorkflowNode, AgentWorkflowPort,
        AgentWorkflowEndpoint, AgentWorkflowExecution)
    system, prompt = split_prompt(messages)
    native_instruction = NATIVE_INSTRUCTION
    if configuration_condition is not None:
        native_instruction = native_instruction.replace(
            "The tool output envelope's raw_result field is the exact upstream response text.",
            "The tool output envelope's raw_result field is the actor-visible response text. "
            "Under this explicit API comparison condition, api_search metadata may be corrected; "
            "a pre_dispatch error means that request was rejected before any upstream action.")
    readback = (configuration_condition is not None
                and configuration_condition.get("id") == "api-contract-visibility-readback-v2")
    tools = ("complete_interaction", "read_file", "read_managed_output", "write_file") if readback else ("complete_interaction", "read_file", "write_file")
    if readback:
        native_instruction += "\nManaged results carry exact action/receipt locators. Use read_managed_output to retrieve omitted JSON pages; never repeat a business call to display its output. A subsequent registered request can prove content inclusion, not semantic use."
    instruction = system + "\n\n" + native_instruction if system else native_instruction
    node = AgentWorkflowNode("executor", instruction,
            (AgentWorkflowPort("request", "task"),),
            (AgentWorkflowPort("result", "final_answer"),),
            AgentWorkflowExecution(role="actor", tools=tools,
                                   profile_id=None))
    graph = AgentWorkflowGraph((node,), (), AgentWorkflowEndpoint("executor", "request"),
                               AgentWorkflowEndpoint("executor", "result"))
    return graph, prompt


def build_spec(run_dir: Path, profile: Path, endpoint: str, run_id: str, messages, schemas, *, configuration_condition=None):
    from cpn.plugins.catalog import load_catalog
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    config = configuration(endpoint, run_id)
    catalog = load_catalog(config)
    graph, prompt = graph_for(messages, configuration_condition=configuration_condition)
    return AgentTaskSpec(run_dir=run_dir, prompt=prompt, stages=(),
            execution_config_path=profile, workflow_graph=graph,
            max_attempts_per_stage=None, max_parallel_nodes=1,
            owner_statement="User-authorized RPNH AutomationBench public experiment",
            plugin_configuration=config, plugin_catalog_digest=catalog.digest,
            managed_bindings=bindings(schemas))


def profile_identity(profile: Path) -> dict:
    from cpn.llm_adapters import load_llm_execution_selection
    selection = load_llm_execution_selection(profile)
    policy = selection.as_registry_policy()  # validates selected/private config agreement
    routes = []
    for route in policy.get("route_provenance", []):
        safe = {k: route[k] for k in ("route_id", "provider", "backend", "protocol",
                         "transport", "outbound_model") if k in route}
        if route.get("endpoint"):
            safe["endpoint_host"] = urlsplit(route["endpoint"]).hostname
        routes.append(safe)
    result = {"selection_sha256": file_sha(profile),
              "private_adapter_sha256": file_sha(selection.adapter_config_path),
              "adapter_kind": selection.adapter_kind,
              "model_condition": selection.input_target.model_condition,
              "reasoning_effort": selection.reasoning_effort,
              "physical_profile": selection.physical_profile,
              "logical_selection_id": selection.logical_selection_id,
              "routes": routes, "native_runtime_policy": policy.get("runtime"),
              "request_timeout_seconds": selection.timeout_seconds,
              "max_output_tokens_per_request": policy.get("max_output_tokens"),
              "max_response_bytes_per_request": policy.get("max_response_bytes"),
              "context_window_tokens": policy.get("context_window_tokens"),
              "native_recovery": policy.get("adapter_profile", {}).get("recovery"),
              "model_identity_scope": "configured/requested; verify registered provider attempts separately"}
    # Do not save endpoint query strings, headers, credentials, argv or env values.
    return result


def run(spec, control_root: Path, *, stop_path: Path | None = None) -> dict:
    from cpn.rpnh.task_control import TaskControl
    if len(str(spec.run_dir / "owner.sock").encode()) > 100:
        raise ValueError("work root is too long for the native Unix owner socket; use e.g. ~/ab1")
    control = TaskControl(control_root)
    handle = control.start(spec)
    manual = False
    shutdown_error = None
    forced = False
    loop_error = None
    try:
        while handle.process.poll() is None:
            if stop_path is not None and stop_path.exists():
                raise KeyboardInterrupt
            time.sleep(0.2)  # no task deadline, idle heuristic or call-count cutoff
    except BaseException as exc:
        manual = isinstance(exc, KeyboardInterrupt)
        if not manual:
            loop_error = type(exc).__name__ + ": " + str(exc)
        try:
            control.stop(handle.task_id, startup_safe=True)
        except Exception as exc:
            shutdown_error = type(exc).__name__ + ": " + str(exc)
        try:
            handle.process.wait(timeout=10)  # shutdown grace, NOT a task budget
        except subprocess.TimeoutExpired:
            forced = True
            handle.process.terminate()
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait()
    exited = handle.process.poll() is not None
    if not exited:
        raise RuntimeError("worker exit is not confirmed; do not freeze a final state")
    status = control.status(handle.task_id)
    terminal, result_error = None, None
    try:
        terminal = control.result(handle.task_id)
    except Exception as exc:
        result_error = type(exc).__name__ + ": " + str(exc)
    return {"at": now(), "executor_host": "native", "task_id": handle.task_id, "process_exit_confirmed": exited,
            "registry_path": str(spec.run_dir),
            "host_quiescent": exited and not forced and not shutdown_error and
                status.get("registry", {}).get("execution_status") in {"terminal", "stopped_by_owner"},
            "forced_termination": forced,
            "admitted": bool(terminal) or status.get("registry", {}).get("execution_status") == "stopped_by_owner",
            "manual_stop": manual, "status": status, "terminal": terminal,
            "terminal_read_error": result_error, "shutdown_error": shutdown_error,
            "worker_wait_error": loop_error,
            "control_root": str(control_root), "log_path": str(handle.log_path)}


def inspect_registry(run_dir: Path) -> tuple[dict, list[dict]]:
    """Read exact frozen authority without writing a projection."""
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.registry._registry import _RegistryCore
    core = _RegistryCore(run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    view = core.event_store.canonical_view()
    views = [view]
    with core.event_store.connect() as db:
        roots = db.execute("SELECT firing_version_id,invocation_version_id FROM firing_publications "
                           "WHERE state='PROVISIONAL' ORDER BY rowid").fetchall()
    for root in roots:
        active = core.event_store.firing_view(firing_version_id=str(root["firing_version_id"]),
                       invocation_version_id=str(root["invocation_version_id"]))
        if active.canonical.through_ordinal != view.through_ordinal:
            raise RuntimeError("native authority changed during post-exit projection")
        views.append(active)
    temporary = {identity for active in views[1:] for kind, identity in active.temporary_members if kind == "object"}
    routes, action_count, llm_specs, provider_specs, records = [], 0, 0, 0, []
    kinds = ("agent_action/v3", "run_terminal_evidence/v1", "provider_attempt_spec/v1",
             "llm_call_spec/v2", "llm_call_spec/v3")
    for kind in kinds:
        canonical = list(core.event_store.canonical_object_rows(through_ordinal=view.through_ordinal, object_type=kind))
        rows = [(row, "canonical") for row in canonical]
        seen = {str(row["version_id"]) for row in canonical}
        if temporary:
            with core.event_store.connect() as db:
                candidates = db.execute("SELECT * FROM objects WHERE object_type=? ORDER BY rowid", (kind,)).fetchall()
            rows += [(row, "active_firing_temporary") for row in candidates
                     if str(row["version_id"]) in temporary and str(row["version_id"]) not in seen]
        for row, authority in rows:
            document = json.loads(str(row["metadata_json"]))
            records.append({"object_type": kind,
                   "version_id": str(row["version_id"]), "authority": authority,
                   "document": document})
            if kind == "agent_action/v3":
                action_count += 1
            elif kind == "provider_attempt_spec/v1":
                provider_specs += 1
                route = {k: document.get(k) for k in ("backend", "model", "transport_kind", "response_protocol")}
                if route not in routes:
                    routes.append(route)
            elif kind.startswith("llm_call_spec/"):
                llm_specs += 1
    counts = list(core.event_store.actual_model_call_counts())
    facts = {"source": "native_registry_exact_authority", "canonical_through_ordinal": view.through_ordinal,
            "active_firing_authorities": len(views)-1, "managed_action_records": action_count,
            "actual_model_call_counts": counts, "actual_model_calls": sum(counts),
            "model_accounting_scope": "native executor/provider calls; excludes model calls inside upstream business tools",
            "provider_attempt_specs": provider_specs, "llm_call_specs": llm_specs,
            "registered_routes": routes,
            "fallback": {"status": "not_inferred_from_request_count",
                         "distinct_registered_routes": len(routes),
                         "opaque_gateway_fallback": "not_observable_without_provider_evidence"},
            "proves_model_consumption_of_each_tool_result": False}
    return facts, records


def project_registry(run_dir: Path, output: Path) -> dict:
    """Project exact frozen authority while leaving the raw run intact."""
    facts, records = inspect_registry(run_dir)
    for record in records:
        append(output / "registry_objects.jsonl", record)
    return facts
