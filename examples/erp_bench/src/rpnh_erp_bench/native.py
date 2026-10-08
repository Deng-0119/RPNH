"""RPNH owns model execution, tool admission, lifecycle and terminal facts."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


ACTOR_INSTRUCTION = """Complete the supplied original ERP business task in the task-bound Odoo world.
First use read_file with the original request's exact Located-input sandbox_path
to read its full instruction in the current firing workspace. Follow its access
and connection instructions; continue with offset_chars if another page is needed.
That input locator belongs to the native firing workspace, not the ERP container.
erp_python runs your Python source inside that world's isolated agent workspace.
The managed admission/receipt covers one script, not each Odoo transaction.
Use the original instruction's odoo-client-lib interface and legal observations.
Never repeat a possibly committed write just to recover missing output. Read back
known object identities; if the result remains ambiguous, report the uncertainty.
Use validate_plan for deterministic arithmetic over visible orders/routes and
your proposed allocations. Its result is not an official grader or proof that
observations are current. Recheck assumptions after mutations. No hidden grader,
reference solution or developer artifact is available or permitted.
Keep observed facts, proposed plan, validation calculations, applied/read-back
operations and SO/MO/PO lineage distinguishable in your final factual report.
Use write_file for your report, setting outcome_id=complete and
output_port_id=team.result, then complete_interaction as the final ordered call.
Additional working documents may omit output_port_id. Do not claim process
definition revision or reusable results solely because the business data changed.
Use read_managed_output for omitted result pages or the historical directory;
never rerun a business script solely to display its prior output.
""".strip()


def graph_for():
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowGraph, AgentWorkflowNode, AgentWorkflowPort,
        AgentWorkflowEndpoint, AgentWorkflowExecution,
    )
    node = AgentWorkflowNode("executor", ACTOR_INSTRUCTION,
        (AgentWorkflowPort("request", "task"),),
        (AgentWorkflowPort("result", "final_report"),),
        AgentWorkflowExecution(role="actor", tools=(
            "complete_interaction", "read_file", "read_managed_output", "write_file")))
    return AgentWorkflowGraph((node,), (), AgentWorkflowEndpoint("executor", "request"),
                               AgentWorkflowEndpoint("executor", "result"))


def build_spec(run_dir, execution_selection, endpoint, trial_id, instruction):
    from cpn.plugins.catalog import load_catalog
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    from .plugin import configuration, bindings
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("the unchanged official instruction is required")
    configuration_document = configuration(str(endpoint), trial_id)
    catalog = load_catalog(configuration_document)
    from cpn.plugins.managed_tools import ManagedPluginToolCatalog
    managed = ManagedPluginToolCatalog(catalog, {
        "erp_python": "erp_bench/erp_python", "validate_plan": "erp_bench/validate_plan"},
        admitted_effects=("pure", "external_write"))
    domains = [{"registration_key": managed.declaration(name).registration_key,
                "effect": managed.declaration(name).effect, "reads": [],
                "writes": ["erp-world:" + trial_id] if name == "erp_python" else [],
                "unknown": False} for name in ("erp_python", "validate_plan")]
    return AgentTaskSpec(run_dir=Path(run_dir), prompt=instruction, stages=(),
        execution_config_path=Path(execution_selection), workflow_graph=graph_for(),
        max_attempts_per_stage=None, max_parallel_nodes=1,
        owner_statement="User-authorized ERP-Bench trial using the existing exact-model configuration",
        plugin_configuration=configuration_document, plugin_catalog_digest=catalog.digest,
        managed_bindings=bindings(),
        managed_tool_policy={"policy_id": "managed_conflict_domains/v1",
                             "max_in_flight": 1, "conflict_domains": domains})


def profile_identity(path):
    """Public configuration identity, excluding argv, env and endpoint values."""
    from cpn.llm_adapters import load_llm_execution_selection
    selection = load_llm_execution_selection(Path(path))
    policy = selection.as_registry_policy()
    return {"adapter_kind": selection.adapter_kind,
            "model_condition": selection.input_target.model_condition,
            "logical_selection_id": selection.logical_selection_id,
            "physical_profile": selection.physical_profile,
            "reasoning_effort": selection.reasoning_effort,
            "request_timeout_seconds": selection.timeout_seconds,
            "max_output_tokens_per_request": policy.get("max_output_tokens"),
            "max_response_bytes_per_request": policy.get("max_response_bytes"),
            "context_window_tokens": selection.input_target.context_window_tokens,
            "context_compaction_retained_tokens": selection.input_target.context_compaction_retained_tokens,
            "runtime": policy.get("runtime"),
            "identity_scope": "Configured exact model; actual submissions come from Registry evidence."}


def freeze_profile(path, destination, *, codex_executable=None) -> Path:
    """Snapshot a local-process selection and adapter without changing policy.

    Destination is a new private directory under an existing parent. Return its
    selection path for both profile_identity() and build_spec(). The adapter's
    bytes (argv, fixed env, inherited env names, service settings and credential
    file references) are copied unchanged. Referenced scripts/auth files are not
    copied: official local-process argv paths remain relative to the worker's
    run directory, not to the relocated adapter JSON. Inherited environment
    bindings keep their existing worker-time semantics.

    With codex_executable, Codex program tokens in argv and probe_argv are bound
    to that executable; the known historical script becomes the installed bridge
    module without changing its model arguments. Original bytes are retained in
    original-adapter.json (0600); adapter.json is explicitly adapted in this mode.
    The shim is supplied by the caller and is never executed here.
    """
    from dataclasses import replace
    from cpn.llm_adapters import load_llm_execution_selection
    from .export import new_path
    from .source import json_bytes, load_json

    path = Path(path)
    selection_bytes = path.read_bytes()
    selection = load_llm_execution_selection(path)
    if selection.adapter_kind != "local_process":
        raise ValueError("profile freezing supports local_process selections only")
    if path.read_bytes() != selection_bytes:
        raise ValueError("selection changed while preparing its private snapshot")
    adapter_bytes = selection.adapter_config_path.read_bytes()
    document = load_json(selection_bytes)
    # Reject malformed JSON, including duplicate keys, before retaining it.
    adapter_document = load_json(adapter_bytes)
    original_adapter_bytes = None
    if codex_executable is not None:
        argv = adapter_document.get("argv") if isinstance(adapter_document, dict) else None
        bridge_module = "cpn.llm_adapters.codex_subscription_bridge"
        if not isinstance(argv, list) or argv.count("{codex}") != 1:
            raise ValueError("Codex override requires the subscription bridge and exactly one {codex} argv token")
        module_entry = (argv.count("-m") == 1 and argv.index("-m") + 1 < len(argv)
                        and argv[argv.index("-m") + 1] == bridge_module)
        legacy_entry = (len(argv) >= 2 and argv[0] == "{python}"
            and Path(argv[1]).name == "codex_subscription_bridge_outer_sandbox.py")
        if not module_entry and not legacy_entry:
            raise ValueError("Codex override requires a supported subscription bridge entry")
        if legacy_entry:
            # The existing local profile's historical script monkeypatches the
            # bridge to disable its sandbox. Bind its unchanged model arguments
            # to the installed supported module instead; never execute that
            # script. Retain the original adapter bytes privately below.
            argv = [argv[0], "-m", bridge_module, *argv[2:]]
        if argv[0] == "{python}":
            # Resolving the venv interpreter symlink loses pyvenv.cfg and can
            # import a different installed core. Keep the active venv pathname.
            import sys
            argv = [str(Path(sys.executable).absolute()), *argv[1:]]
        executable = Path(codex_executable).expanduser().resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("Codex override must be an existing executable file")
        original_adapter_bytes = adapter_bytes
        adapter_document["argv"] = [str(executable) if token == "{codex}" else token for token in argv]
        # The existing profile also uses {codex} for its --version readiness
        # probe. Bind that executable too; workers need not have Codex on PATH.
        probe = adapter_document.get("probe_argv")
        if isinstance(probe, list):
            adapter_document["probe_argv"] = [str(executable) if token == "{codex}" else token for token in probe]
        adapter_bytes = json_bytes(adapter_document)
    destination = Path(destination).absolute()
    destination = new_path(destination.parent, destination.name)
    destination.mkdir(mode=0o700)  # Existing parent required; never reuse a snapshot.
    adapter_path = destination / "adapter.json"
    frozen_path = destination / "selection.json"
    document["adapter_config_path"] = str(adapter_path)
    files = [(adapter_path, adapter_bytes), (frozen_path, json_bytes(document))]
    if original_adapter_bytes is not None:
        files.insert(0, (destination / "original-adapter.json", original_adapter_bytes))
    for target, data in files:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    frozen = load_llm_execution_selection(frozen_path)
    if replace(frozen, adapter_config_path=selection.adapter_config_path) != selection:
        raise ValueError("frozen selection policy differs from the original")
    # Official public policy projection also checks adapter/model/effort binding.
    # Do not publish its argv/env provenance or private configuration values.
    frozen.as_registry_policy()
    return frozen_path


def run_owner(spec, control_root, *, agent_timeout_seconds=3600, stop_requested=lambda: False):
    """Enforce the official wall window, then require supported writer shutdown.

    A forced stop is explicitly non-quiescent and cannot authorize grading.
    """
    from cpn.rpnh.task_control import TaskControl
    if type(agent_timeout_seconds) is not int or agent_timeout_seconds < 1:
        raise ValueError("agent timeout must be a positive integer")
    control = TaskControl(Path(control_root))
    start = time.monotonic()
    handle = control.start(spec)
    reason, shutdown_error, forced = None, None, False
    try:
        while handle.process.poll() is None:
            requested = stop_requested()
            if requested or time.monotonic() - start >= agent_timeout_seconds:
                reason = "owner_requested" if requested else "official_agent_timeout"
                control.stop(handle.task_id, startup_safe=True)
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        reason = "owner_requested"
        try:
            control.stop(handle.task_id, startup_safe=True)
        except Exception as exc:
            shutdown_error = type(exc).__name__
    except Exception as exc:
        reason, shutdown_error = "owner_control_failure", type(exc).__name__
        try:
            control.stop(handle.task_id, startup_safe=True)
        except Exception:
            pass
    if handle.process.poll() is None:
        try:
            handle.process.wait(timeout=30)  # shutdown grace, not a solver budget
        except subprocess.TimeoutExpired:
            forced = True
            handle.process.terminate()
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait(timeout=5)
    status = control.status(handle.task_id)
    result, result_error = None, None
    try:
        result = control.result(handle.task_id)
    except (RuntimeError, ValueError) as exc:
        result_error = type(exc).__name__
    try:
        evidence = control.result_evidence(handle.task_id)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        evidence = {"status": "unavailable", "reason": type(exc).__name__,
                    "actual_model_call_counts": None}
    registry_status = status.get("registry", {}).get("execution_status")
    process_exit_code = handle.process.poll()
    quiescent = (process_exit_code is not None and not forced and not shutdown_error
                 and registry_status in {"terminal", "stopped_by_owner"})
    return {"task_id": handle.task_id, "process_exit_confirmed": process_exit_code is not None,
            "process_exit_code": process_exit_code,
            "owner_quiescent": quiescent, "forced_termination": forced,
            "stop_reason": reason, "shutdown_error": shutdown_error,
            "elapsed_seconds": time.monotonic() - start,
            "max_attempts_per_stage": spec.max_attempts_per_stage,
            "official_agent_timeout_seconds": agent_timeout_seconds,
            "status": status, "terminal": result, "terminal_read_error": result_error,
            "result_evidence": evidence}


def safe_owner_projection(result):
    """Export actual identities/counts, never raw model/tool body transcripts."""
    evidence = result["result_evidence"]
    actions = []
    for action in evidence.get("actions", []):
        registered = action.get("registered_return", {})
        actions.append({key: action[key] for key in
            ("agent_action_ref", "agent_loop_ref", "agent_turn_ref", "tool_call_id", "tool_name")
            if key in action} | {"registered_return": {
                key: registered[key] for key in ("outcome", "started_receipt_ref", "terminal_receipt_ref")
                if key in registered}})
    terminal = result.get("terminal")
    return {"task_id": result["task_id"], "owner_quiescent": result["owner_quiescent"],
            "process_exit_confirmed": result["process_exit_confirmed"],
            "process_exit_code": result.get("process_exit_code"),
            "forced_termination": result["forced_termination"],
            "terminal_evidence_ref": terminal.get("terminal_evidence_ref") if terminal else None,
            "terminal_result_ref": terminal.get("terminal_result_ref") if terminal else None,
            "actual_model_call_counts": terminal.get("actual_model_call_counts") if terminal else
                evidence.get("actual_model_call_counts"),
            "actions": actions,
            "semantic_use": "unknown", "process_definition_revision": "not_performed",
            "definition_reuse": "supplied_single_actor_definition", "numeric_result_reuse": "not_claimed"}
