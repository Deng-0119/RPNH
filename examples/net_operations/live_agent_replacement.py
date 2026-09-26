"""Run the executable native net-operation scope with one exact live profile.

The definition operations are deterministic: they do not call a model merely
to rearrange a graph.  Their output is nevertheless the graph executed here.
The first Agent writes a registered workspace file, whole-net Replace adopts a
one-node successor while preserving that settled workspace lineage, and the
successor reads the file before publishing the terminal result.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import tarfile
from typing import Any, Mapping

from cpn.components.agent_loop.optional_host_bindings import (
    make_optional_agent_host_bindings,
)
from cpn.components.execution_services import ExecutionServices
from cpn.llm_adapters import (
    build_llm_input_port,
    load_llm_execution_selection,
)
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh import (
    ComposePlan,
    ExtractPlan,
    apply_replacement,
    branch_module,
    compose_modules,
    extract_module,
    instantiate_module,
    prepare_replacement,
)
from cpn.rpnh.agent_tasks import (
    AgentStage,
    agent_task_catalog,
    agent_task_registration,
    build_agent_task_module,
)
from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run


SEED_TEXT = "RPNH native net live seed"
FINAL_TEXT = "RPNH native net live replacement complete"
TASK_TEXT = (
    "Exercise the repository's native Petri-net definition and replacement "
    "operations. Follow each node instruction exactly."
)
TRANSPORT_CONTRACT = {
    "interaction_protocol_ref": "llm_request_envelope/v1",
    "response_adapter_ref": "llm_response_envelope/v1",
}


def build_live_modules(*, max_attempts: int = 8):
    """Return the composed first graph, replacement graph and operation facts."""
    prepare_source = build_agent_task_module((AgentStage(
        "worker",
        "Call write_file once to create outputs/seed.txt with JSON-string "
        f"content exactly {json.dumps(SEED_TEXT)}. Use output port "
        "prepare_worker.result and outcome complete, then call "
        "complete_interaction as the final ordered tool call.",
    ),), max_attempts_per_stage=max_attempts)
    finish_source = build_agent_task_module((AgentStage(
        "worker",
        "First call workspace with script `sed -n '1p' outputs/seed.txt` and "
        "timeout_seconds 30 to inspect the preserved workspace file. After "
        "that result, call read_file with the exact sandbox_path whose Located "
        "inputs entry has source_relative_path outputs/seed.txt. Only after "
        "both results equal "
        f"{json.dumps(SEED_TEXT)}, call write_file once to create "
        "outputs/result.txt with JSON-string content exactly "
        f"{json.dumps(FINAL_TEXT)}. Use output port replacement_worker.result "
        "and outcome complete, then call complete_interaction as the final "
        "ordered tool call.",
    ),), max_attempts_per_stage=max_attempts)

    extracted = extract_module(
        prepare_source, ExtractPlan(output_name="ExtractedPrepare"))
    branched = branch_module(
        finish_source,
        ExtractPlan(output_name="BranchedFinish"),
        output_mode="definition_only",
    )
    replacement = instantiate_module(
        branched.definition,
        instance="replacement",
        output_name="ReplacementFinish",
    )
    composed = compose_modules(
        {"prepare": extracted.module, "finish": replacement},
        ComposePlan("LiveNetOperationsTask", "finish", mode="serial"),
    )
    facts = {
        "extract": extracted.module.name,
        "branch": branched.definition.name,
        "instantiate": replacement.name,
        "compose": composed.name,
        "replace": "whole_net_quiescent",
    }
    return composed, replacement, facts


def _execution_route(selection) -> dict[str, Any]:
    provenance = selection.as_registry_policy()
    routes = provenance.get("route_provenance")
    if not isinstance(routes, list) or len(routes) != 1:
        raise ValueError("live example requires one exact configured route")
    return {
        "schema_version": "optional_agent_execution_provenance/v1",
        "model": selection.input_target.model_condition,
        "backend": selection.adapter_kind,
        "timeout_seconds": selection.timeout_seconds,
        "selection": provenance,
        "transport_kind": routes[0]["transport"],
        "response_protocol": "llm_response_envelope/v1",
    }


def _checkpoint(owner) -> tuple[object, Mapping[str, Any]]:
    ref = _version_from_payload(owner.snapshot()["checkpoint_ref"])
    return ref, owner._core.get_version(ref.version_id).metadata


def _workspace(owner, checkpoint: Mapping[str, Any]):
    values = checkpoint.get("workspace_revision_refs")
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("live example requires one settled workspace lineage")
    ref = _version_from_payload(values[0])
    prepared = owner._core.get_version(ref.version_id)
    return ref, prepared.metadata, owner._core.object_store.read_registered(prepared)


def _archive_text(payload: bytes, path: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        member = archive.extractfile(path)
        if member is None:
            raise RuntimeError(f"workspace archive lacks {path}")
        text = member.read().decode("utf-8")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return text
    return decoded if isinstance(decoded, str) else text


def _terminal_output(owner, terminal_ref):
    if terminal_ref is None:
        raise RuntimeError("live example has no terminal evidence")
    kernel, _repository = owner.operation_repository()
    evidence = kernel._exact_object(
        terminal_ref, expected_type="run_terminal_evidence/v1").metadata
    result_ref = _version_from_payload(evidence["terminal_result_ref"])
    raw = kernel._read_registered(
        ResourceVersionRef(result_ref.entity_id, result_ref.version_id))
    return evidence, result_ref, json.loads(raw)


def _settled_tools(owner) -> list[str]:
    events = owner._core.event_store.list_events_by_type(
        ("agent_action_settled/v1",))
    rejected = [event for event in events
                if event.payload.get("settlement") == "ACTION_REJECTED"]
    if rejected:
        raise RuntimeError("live Agent task contains a rejected tool action")
    return [str(event.payload["tool_name"]) for event in events]


def run_live_example(*, run_dir: Path, execution_config_path: Path) -> dict[str, Any]:
    """Execute the example once; an existing destination is never reused."""
    destination = run_dir.resolve()
    if os.path.lexists(destination):
        raise ValueError("live example requires an absent run directory")
    selection = load_llm_execution_selection(execution_config_path.resolve())
    policy = selection.as_registry_policy()
    routes = policy["route_provenance"]
    route = routes[0]
    module, replacement, operations = build_live_modules()
    registration = agent_task_registration()
    buckets = tuple(module.to_dict()["budget_buckets"])
    call_cap = sum(item["max_attempts"] for item in buckets)
    task = OwnerInput(
        TEXT_SCHEMA, canonical_json(TASK_TEXT), "Native net-operation live task")
    host_bindings = make_optional_agent_host_bindings(
        selection.input_target,
        workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract=TRANSPORT_CONTRACT,
    )
    owner = start_run(
        module,
        registration,
        run_dir=destination,
        task_input=task,
        entry_inputs={"prepare_request": task},
        budgets=ModuleBudgetDeclaration(
            buckets, ("rpnh/module_declaration/v1",),
            call_cap, 0, call_cap, 0),
        model_condition=selection.input_target.model_condition,
        owner_statement=(
            "User-authorized native Petri-net live example on one exact route"),
        command_id="example:net-operations-live:fresh",
        catalog=agent_task_catalog(),
        host_execution_bindings=host_bindings,
    )
    port = build_llm_input_port(selection, destination_run_root=destination)
    first_loop = None
    second_loop = None
    try:
        first_loop = OwnerEventLoop(owner, destination / "owner-first.sock")
        first_services = ExecutionServices(
            owner=owner, event_loop=first_loop, llm_input_port=port)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_result = Orchestrator(
                owner=owner,
                event_loop=first_loop,
                prepare_dispatcher=first_services.prepare_dispatcher,
                submit_operation=pool.submit,
                select_ready=lambda **values: tuple(
                    item for item in values["enabled"]
                    if item == "prepare_worker.run"),
                max_in_flight=1,
            ).run()
        if first_result.stop_reason != "quiescent_marking":
            raise RuntimeError(
                "first graph did not stop at its expected quiescent boundary")
        first_loop.close()
        first_loop = None

        _first_checkpoint_ref, first_checkpoint = _checkpoint(owner)
        first_workspace_ref, first_workspace, first_payload = _workspace(
            owner, first_checkpoint)
        if first_workspace.get("inventory_paths") != ["outputs/seed.txt"]:
            raise RuntimeError("first Agent did not settle the exact seed file")
        if _archive_text(first_payload, "outputs/seed.txt") != SEED_TEXT:
            raise RuntimeError("first Agent seed content differs")

        bridge_token = next(
            item for item in owner.snapshot()["marking"]
            if item["place"] == "finish_replacement_worker.request")
        plan = prepare_replacement(
            owner,
            replacement,
            marking_mapping={
                bridge_token["token_ref"]["version_id"]:
                    "replacement_worker.request",
            },
        )
        adoption = apply_replacement(
            owner, plan, command_id="example:net-operations-live:replace")
        if adoption.get("status") != "ADOPTED":
            raise RuntimeError("quiescent replacement was not adopted")
        _adopted_checkpoint_ref, adopted_checkpoint = _checkpoint(owner)
        if adopted_checkpoint.get("workspace_revision_refs") != [
                ref_payload(first_workspace_ref)]:
            raise RuntimeError("replacement did not preserve the settled workspace")

        second_loop = OwnerEventLoop(owner, destination / "owner-second.sock")
        second_services = ExecutionServices(
            owner=owner, event_loop=second_loop, llm_input_port=port)
        with ThreadPoolExecutor(max_workers=1) as pool:
            terminal = Orchestrator(
                owner=owner,
                event_loop=second_loop,
                prepare_dispatcher=second_services.prepare_dispatcher,
                submit_operation=pool.submit,
                max_in_flight=1,
            ).run()
        if terminal.stop_reason != "terminal":
            raise RuntimeError("replacement graph did not reach terminal state")

        evidence, result_ref, output = _terminal_output(
            owner, terminal.terminal_evidence_ref)
        if output != FINAL_TEXT:
            raise RuntimeError("terminal result differs from the expected text")
        _final_checkpoint_ref, final_checkpoint = _checkpoint(owner)
        final_workspace_ref, final_workspace, final_payload = _workspace(
            owner, final_checkpoint)
        if final_workspace.get("parent_revision_ref") != ref_payload(
                first_workspace_ref):
            raise RuntimeError("final workspace does not descend from pre-replace state")
        if final_workspace.get("inventory_paths") != [
                "outputs/result.txt", "outputs/seed.txt"]:
            raise RuntimeError("final workspace inventory differs")
        if (_archive_text(final_payload, "outputs/seed.txt") != SEED_TEXT
                or _archive_text(final_payload, "outputs/result.txt") != FINAL_TEXT):
            raise RuntimeError("final workspace content differs")
        tools = _settled_tools(owner)
        required_tools = {
            "workspace", "read_file", "write_file", "complete_interaction"}
        if not required_tools <= set(tools):
            raise RuntimeError("live Agent task did not exercise the required tools")
        adoption_events = owner._core.event_store.list_events_by_type(
            ("net_adopted/v1",))
        replacement_adoptions = [
            event for event in adoption_events
            if event.payload.get("supersedes_net_ref") is not None]
        if len(replacement_adoptions) != 1:
            raise RuntimeError("live example requires exactly one net adoption")
        environment_count = len(owner._core.event_store.canonical_object_rows(
            object_type="execution_environment_identity/v1"))
        workspace_profile_count = len(
            owner._core.event_store.canonical_object_rows(
                object_type="numerical_tool_profile/v1"))
        if environment_count != 1 or workspace_profile_count != 1:
            raise RuntimeError(
                "replacement duplicated the run-scoped workspace runtime")

        return {
            "schema_version": "rpnh/net_operations_live_result/v1",
            "status": "PASS",
            "operations": operations,
            "route": {
                "adapter_kind": selection.adapter_kind,
                "provider": route["provider"],
                "backend": route["backend"],
                "exact_model": selection.input_target.model_condition,
                "outbound_model": route.get(
                    "outbound_model", selection.input_target.model_condition),
                "route_switches": 0,
            },
            "registry": {
                "run_ref": ref_payload(owner.identity.run_ref),
                "task_ref": ref_payload(owner.identity.task_ref),
                "terminal_evidence_ref": ref_payload(
                    terminal.terminal_evidence_ref),
                "terminal_result_ref": ref_payload(result_ref),
                "run_outcome": evidence["run_outcome"],
                "replacement_adoption_count": len(replacement_adoptions),
                "actual_model_call_counts": list(
                    owner._core.event_store.actual_model_call_counts()),
            },
            "workspace": {
                "preserved_at_adoption": True,
                "final_revision_changed": (
                    final_workspace_ref != first_workspace_ref),
                "inventory_paths": final_workspace["inventory_paths"],
                "execution_environment_authority_count": environment_count,
                "workspace_profile_authority_count": workspace_profile_count,
            },
            "settled_tools": tools,
            "terminal_output": output,
        }
    finally:
        if second_loop is not None:
            second_loop.close()
        if first_loop is not None:
            first_loop.close()
        port.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the live native Petri-net operation example")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_live_example(
        run_dir=args.run_dir,
        execution_config_path=args.execution,
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
