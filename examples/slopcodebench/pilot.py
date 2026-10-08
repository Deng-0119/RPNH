"""Native RPNH per-checkpoint pilot using the official solver Session.

This is a development condition, not a replacement implementation of AgentRunner.
Unknown upstream usage cannot be faithfully represented in its UsageTracker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .broker import SessionCommandBroker
from .contracts import CheckpointLedger, CheckpointScope, canonical, current_request, digest
from .plugin import bindings, configuration
from .endpoint import prepare_execution_snapshot


@dataclass(frozen=True)
class DevelopmentCondition:
    condition_id: str = "scb-session-command-development-v1"
    cost_limit: float = 0
    net_cost_limit: float = 0
    step_limit: int = 0
    max_model_calls: int = 48
    checkpoint_timeout_seconds: int = 7200
    solver_network: str = "none"

    def __post_init__(self):
        if (self.condition_id != "scb-session-command-development-v1"
                or any(value != 0 for value in (self.cost_limit, self.net_cost_limit, self.step_limit))):
            raise ValueError("this explicit pilot requires disabled upstream caps; positive caps are unsupported")
        if (type(self.max_model_calls) is not int or self.max_model_calls < 1
                or type(self.checkpoint_timeout_seconds) is not int or self.checkpoint_timeout_seconds < 1
                or self.solver_network != "none"):
            raise ValueError("a positive model-call/wall bound and network-isolated solver are required")


def graph():
    from cpn.rpnh.agent_workflows import AgentWorkflowGraph
    return AgentWorkflowGraph.from_mapping({
        "nodes": [{"node_id": "solve", "instruction": (
            "Implement the current benchmark request using session_command for all source-file "
            "inspection, editing and tests. It executes in the benchmark's isolated solution "
            "workspace. No later instructions, reference code or original evaluator are available. "
            "Read the current Located request with read_file. A command result is not a benchmark "
            "grade. Use read_managed_output to retrieve retained output without repeating a command. "
            "When finished, publish a concise factual report with write_file using the declared "
            "result port, then complete_interaction. The report is not the source-code artifact."),
            "input_ports": [{"port_id": "request", "artifact_id": "current_task"}],
            "output_ports": [{"port_id": "result", "artifact_id": "completion_report"}],
            "execution": {"role": "actor", "profile_id": None,
                "tools": ["complete_interaction", "read_file", "read_managed_output", "write_file"]}}],
        "arcs": [], "ingress": {"node_id": "solve", "port_id": "request"},
        "egress": {"node_id": "solve", "port_id": "result"}, "max_rework_cycles": 0})


def build_spec(*, prompt, run_dir, execution, endpoint, checkpoint_key, condition,
               catalog_loader=None):
    from cpn.plugins.catalog import load_catalog
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    selected = configuration(endpoint, checkpoint_key)
    catalog = (catalog_loader or load_catalog)(selected)
    return AgentTaskSpec(run_dir=run_dir, prompt=prompt, stages=(),
        execution_config_path=execution, workflow_graph=graph(),
        max_attempts_per_stage=condition.max_model_calls, max_parallel_nodes=1,
        plugin_configuration=selected, plugin_catalog_digest=catalog.digest,
        managed_bindings=bindings(), owner_statement="Owner-selected SlopCodeBench development pilot")


def invoke_rpnh(spec, control_dir, timeout_seconds):
    """Use the real public task owner boundary; no alternate model loop."""
    from cpn.rpnh.task_control import TaskControl
    control = TaskControl(control_dir)
    handle = control.start(spec)
    try:
        return_code = handle.process.wait(timeout=timeout_seconds)
    except BaseException:
        try:
            control.stop(handle.task_id, startup_safe=True)
            handle.process.wait(timeout=20)
        except BaseException:
            handle.process.terminate()
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait(timeout=5)
        raise
    if return_code != 0:
        raise RuntimeError("RPNH task owner did not exit successfully")
    result = control.result(handle.task_id)
    if result.get("run_outcome") != "complete":
        raise RuntimeError("RPNH task has no successful terminal report")
    return result


def validate_session(session, evidence_root):
    """Reject unsafe Session variants instead of moving execution to the host."""
    if not session.is_agent_infer or session.spec.type != "docker":
        raise ValueError("pilot requires the official Docker inference Session")
    docker = session.spec.docker
    if (docker.extra_mounts or session.static_assets or not docker.mount_workspace
            or docker.network != "none" or session.spec.environment.include_os_env):
        raise ValueError("solver must have only its workspace mount and disabled external network")
    if session.spec.get_setup_commands(is_evaluation=False):
        raise ValueError("pilot disables solver setup; configured setup commands cannot be skipped")
    work, evidence = Path(session.working_dir).resolve(), evidence_root.resolve()
    if work == evidence or work in evidence.parents or evidence in work.parents:
        raise ValueError("private RPNH/evaluation evidence must be outside the solver workspace")


def selected_snapshot_manifest(snapshot):
    """Hash exactly the original Snapshot's extracted file selection.

    SnapshotConfig, not this adapter, decides whether .venv, caches and other
    files are omitted. Source bytes are not copied into the model prompt.
    """
    files = [{"path": path.as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
              "size_bytes": len(content)}
             for path, content in sorted(snapshot.extract_contents().items())]
    body = {"schema_version": "rpnh/slopcodebench-selected-snapshot/v1", "files": files}
    return {**body, "sha256": digest(body)}


def capture_snapshot(session):
    from slop_code.execution import Snapshot
    snapshot = Snapshot.from_environment_spec(cwd=session.working_dir, env_spec=session.spec,
                                              static_assets=session.static_assets)
    try:
        return selected_snapshot_manifest(snapshot)
    finally:
        snapshot.cleanup()


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


class CheckpointPilot:
    """Accept one currently revealed checkpoint at a time, with a fresh owner.

    Session owns source files. A fresh command runtime per checkpoint is killed
    before the original snapshot API is called. No grades are sent to the model.
    Restart/resume and automatic replay are deliberately unsupported.
    """

    def __init__(self, session, *, execution: Path, evidence_root: Path,
                 condition: DevelopmentCondition, solver_image_id: str, scope=CheckpointScope(),
                 codex_binary: Path | None = None,
                 _invoke=invoke_rpnh, _broker=SessionCommandBroker,
                 _capture=capture_snapshot, _catalog_loader=None):
        validate_session(session, evidence_root)
        if re.fullmatch(r"sha256:[a-f0-9]{64}", solver_image_id) is None:
            raise ValueError("solver_image_id must be an exact locally resolved Docker image ID")
        if evidence_root.exists():
            raise ValueError("pilot evidence directory must be fresh")
        evidence_root.mkdir(parents=True)
        self.session, self.execution, self.root, self.condition = session, execution, evidence_root, condition
        self.scope = scope
        self.solver_image_id = solver_image_id
        self.ledger = CheckpointLedger(evidence_root / "lineage", scope)
        self.invoke, self.broker, self.capture = _invoke, _broker, _capture
        self.catalog_loader = _catalog_loader
        self.execution, endpoint_condition = prepare_execution_snapshot(
            execution, self.root / "profile", codex_binary=codex_binary)
        write_new(self.root / "endpoint-condition.json", endpoint_condition)
        write_new(self.root / "condition.json", asdict(condition))
        write_new(self.root / "definition.json", graph().to_dict())
        write_new(self.root / "environment.json", {"solver_image_id": solver_image_id,
                  "solver_network": "none", "runtime_policy": "fresh_container_per_checkpoint"})

    def run(self, checkpoint: str, prompt: str):
        records = self.ledger.records()
        if (len(records) >= len(self.scope.names)
                or checkpoint != self.scope.names[len(records)]):
            raise ValueError("checkpoint must be the next original prefix member")
        if records and records[-1]["handoff_status"] != "submitted":
            raise ValueError("prior checkpoint did not complete; no replay or advance")
        before = self.capture(self.session)
        if records and before["sha256"] != records[-1]["settled_workspace_sha256"]:
            raise ValueError("Session workspace differs from the settled predecessor")
        directory = self.root / checkpoint
        directory.mkdir()
        write_new(directory / "before.json", before)
        request = current_request(checkpoint=checkpoint, prompt=prompt, workspace=before,
            definition_sha256=digest(graph().to_dict()),
            predecessor_sha256=digest(records[-1]) if records else None)
        write_new(directory / "request.json", request)
        runtime = broker = None
        try:
            runtime = self.session.spawn(disable_setup=True, image=self.solver_image_id)
            broker = self.broker(runtime, checkpoint_key=checkpoint, evidence_dir=directory)
            with broker:
                spec = build_spec(prompt=prompt, run_dir=directory / "registry", execution=self.execution,
                    endpoint=broker.endpoint, checkpoint_key=checkpoint, condition=self.condition,
                    catalog_loader=self.catalog_loader)
                result = self.invoke(spec, directory / "control", self.condition.checkpoint_timeout_seconds)
                if broker.poisoned:
                    raise RuntimeError("command state uncertain; checkpoint cannot be submitted")
            if not broker.quiescent or broker.poisoned:
                raise RuntimeError("runtime quiescence was not confirmed")
            # This happens only after the full solver container is stopped.
            self.session.finish_checkpoint(directory / "snapshot")
            after = selected_snapshot_manifest(self.session.workspace.initial_snapshot)
            write_new(directory / "after.json", after)
            write_new(directory / "rpnh-result.json", result)
            output = {"schema_version": "rpnh/slopcodebench-pilot-result/v1",
                "checkpoint": checkpoint, "condition_id": self.condition.condition_id,
                "coverage": self.scope.coverage, "status": "submitted",
                "before_sha256": before["sha256"], "after_sha256": after["sha256"],
                "solution_workspace_owner": "upstream_scb_session",
                "native_workspace_reuse": False, "topology_revision": False,
                "rpnh_result": result, "snapshot": "snapshot",
                "usage": {"tokens": None, "cost_usd": None, "status": "unavailable",
                          "actual_model_call_counts": result.get("actual_model_call_counts")},
                "official_agent_runner": "not_run", "original_evaluation": "not_collected"}
            write_new(directory / "result.json", output)
            self.ledger.append(request, handoff_status="submitted", rpnh_result=result,
                               settled_workspace_sha256=after["sha256"])
            return output
        except BaseException as exc:
            # A failed/unknown runtime must never reach finish_checkpoint.
            quiescent = runtime is None or (broker is not None and broker.quiescent)
            if runtime is not None and not quiescent:
                try:
                    SessionCommandBroker._bounded(runtime.cleanup, 15)
                except BaseException as cleanup_error:
                    detail = cleanup_error.__cause__ or cleanup_error
                    exc.add_note(f"Additional runtime cleanup failed: {type(detail).__name__}: {detail}")
            try:
                self.ledger.append(request, handoff_status="failed", rpnh_result=None,
                                   reason=type(exc).__name__ + ": checkpoint not submitted")
                failure = {"status": "failed", "error_type": type(exc).__name__, "error_message": str(exc),
                           "error_notes": list(getattr(exc, "__notes__", ())),
                           "visibility": "private_unreviewed", "public_safe": False,
                           "quiescent": bool(broker is not None and broker.quiescent),
                           "runtime_returned": runtime is not None, "official_evaluation": "not_run"}
                descriptor = os.open(directory / "failure.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(canonical(failure) + b"\n")
            except BaseException as recording_error:
                exc.add_note("Failure evidence could not be completed: " + type(recording_error).__name__)
            raise
