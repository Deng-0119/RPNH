"""Concrete local native RPNH driver for the pinned HarnessAudit bridge."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import signal
import time
from typing import Mapping

from .office_cases import plugin_roles_for_task
from .lifecycle import NativeTerminationUnconfirmed, OwnedProcessTree
from .constants import OFFICE_EFFECTS
from .comparison_condition import condition_of, effects_for, condition_manifest, POLICY_ARGUMENT
from .driver_contract import DriverRequest, DriverResult
from .registry_export import (
    _authority_views, _rows_for_authority, export_registry,
)
from .workflow import NODE_ROLE_ORDER, build_business_workflow, public_argument_schema


_MIN_BOTTOM_ACTIVITY_TIMEOUT_SECONDS = 120.0
_STOP_GRACE_SECONDS = 5.0
_TERMINATE_GRACE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class _Progress:
    max_ordinal: int
    model_calls: int
    managed_receipts: int
    written_products: int
    completed_interactions: int
    terminal_evidence: int
    maximum_exact_replay: int

    @property
    def semantic_signature(self) -> tuple[int, int, int, int]:
        return (
            self.managed_receipts,
            self.written_products,
            self.completed_interactions,
            self.terminal_evidence,
        )


def _registry_progress(run_dir: Path) -> _Progress | None:
    database = run_dir / ".registry_v1" / "registry.sqlite3"
    if not database.is_file():
        return None
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.registry._registry import _RegistryCore

    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    views = _authority_views(core, include_provisional=True)
    view = views[0]
    rows = [
        row
        for object_type in (
            "agent_action/v2", "agent_action/v3",
            "run_terminal_evidence/v1",
        )
        for row in _rows_for_authority(
            core, views, object_type)
    ]
    receipts: set[tuple[str, str]] = set()
    products: set[tuple[str, str]] = set()
    completions: set[str] = set()
    replay_keys = []
    terminal_count = 0
    for row in rows:
        document = json.loads(str(row["metadata_json"]))
        if row["object_type"] == "run_terminal_evidence/v1":
            terminal_count += 1
            continue
        if row["object_type"] == "agent_action/v3":
            terminal = document.get("terminal_receipt_ref")
            if isinstance(terminal, dict):
                receipt = (
                    str(terminal.get("resource_id")),
                    str(terminal.get("resource_version_id")),
                )
                receipts.add(receipt)
                replay_keys.append((
                    document.get("selector"),
                    document.get("tool_call_id"),
                    receipt,
                ))
            continue
        metadata = document.get("result_metadata")
        if document.get("tool_name") == "write_file" and isinstance(
                metadata, dict):
            resource = metadata.get("resource_ref")
            if isinstance(resource, dict):
                products.add((
                    str(resource.get("resource_id")),
                    str(resource.get("resource_version_id")),
                ))
        elif (document.get("tool_name") == "complete_interaction"
              and document.get("state") == "COMPLETED"):
            completions.add(str(document.get("agent_action_id")))
    counts = core.event_store.actual_model_call_counts()
    return _Progress(
        max_ordinal=view.through_ordinal,
        model_calls=sum(counts),
        managed_receipts=len(receipts),
        written_products=len(products),
        completed_interactions=len(completions),
        terminal_evidence=terminal_count,
        maximum_exact_replay=max(Counter(replay_keys).values(), default=0),
    )


def _profile_timeout(path: Path) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        timeout = value.get("timeout_seconds")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 30
    return timeout if type(timeout) is int and timeout > 0 else 30


def _nonterminal_worker_result(
        status: Mapping[str, object], expected_run_dir: Path) -> dict | None:
    """Read one strict result while permitting preceding worker diagnostics."""
    if (status.get("process_status") != "FAILED"
            or status.get("return_code") != 2):
        return None
    log_path = status.get("log_path")
    if not isinstance(log_path, str):
        raise ValueError("nonterminal worker status lacks its result log")
    from .jsonio import loads
    results = []
    for raw_line in Path(log_path).read_text(
            encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            candidate = loads(line)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "nonterminal worker emitted a malformed JSON result candidate"
            ) from exc
        if (isinstance(candidate, dict)
                and candidate.get("schema_version")
                == "rpnh/agent_task_result/v1"):
            results.append(candidate)
    if len(results) != 1:
        raise ValueError(
            "nonterminal worker must emit exactly one structured result")
    result = results[0]
    if (not isinstance(result, dict)
            or result.get("schema_version") != "rpnh/agent_task_result/v1"
            or Path(str(result.get("run_dir"))).resolve()
            != expected_run_dir.resolve()
            or result.get("terminal_evidence_ref") is not None
            or result.get("terminal_result_ref") is not None
            or result.get("output") is not None
            or not isinstance(result.get("stop_reason"), str)
            or not result["stop_reason"]):
        raise ValueError("nonterminal worker result is malformed")
    counts = result.get("actual_model_call_counts")
    if (not isinstance(counts, list)
            or any(type(value) is not int or value < 0 for value in counts)):
        raise ValueError("nonterminal worker model accounting is malformed")
    return result


def managed_bindings(request: DriverRequest, roles_by_node: Mapping[str, str]):
    """Project every public office tool through each node's role-bound plugin."""
    tools = {item["name"]: item for item in request.task.as_dict()["tools"]}
    condition = condition_of(request.task)
    if condition != request.configuration_condition:
        raise ValueError("driver request and public input condition disagree")
    effects = effects_for(condition)
    if set(tools) != set(effects):
        raise ValueError("public office tool inventory differs from the bridge catalog")
    plugin_by_role = {role: plugin for plugin, role in plugin_roles_for_task(request.task.as_dict()).items()}
    if set(roles_by_node.values()) - set(plugin_by_role):
        raise ValueError("workflow names an unbound business role")
    def schema(tool):
        result = public_argument_schema(tool)
        if condition and effects[tool["name"]] == "external_write":
            result["required"] = [POLICY_ARGUMENT]
        return result

    return {
        node: {
            "tools": {
                name: {
                    "selector": f"{plugin_by_role[role]}/{name}",
                    "description": tools[name]["description"],
                    "input_schema": schema(tools[name]),
                }
                for name in sorted(tools)
                if not condition or node.startswith("execute_") or effects[name] != "external_write"
            },
            "admitted_effects": (["pure", "external_read", "external_write"]
                                 if not condition or node.startswith("execute_")
                                 else ["pure", "external_read"]),
        }
        for node, role in roles_by_node.items()
    }


def _attempts_per_node(
        max_model_calls: int | None, node_count: int = 4,
) -> int | None:
    if type(node_count) is not int or node_count < 2:
        raise ValueError("public workflow must contain at least two nodes")
    if max_model_calls is None:
        return None
    if type(max_model_calls) is not int or max_model_calls < node_count:
        raise ValueError("model-call limit is smaller than the public workflow node count")
    return max_model_calls // node_count


async def _wait_for_exit(
        control, task_id: str, *, run_dir: Path, deadline: float | None,
        provider_timeout: int, startup_timeout: float = 30,
        activity_timeout: float | None = None,
        stop_grace: float = _STOP_GRACE_SECONDS,
        terminate_grace: float = _TERMINATE_GRACE_SECONDS,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        process_observer=None, max_calls_without_progress: int | None = None):
    from .jsonio import write_replace

    started = time.monotonic()
    startup_deadline = started + startup_timeout
    if deadline is not None:
        startup_deadline = min(deadline, startup_deadline)
    if activity_timeout is None:
        activity_timeout = max(
            _MIN_BOTTOM_ACTIVITY_TIMEOUT_SECONDS,
            max(30.0, provider_timeout + 15.0))
    if (activity_timeout <= 0 or stop_grace < 0
            or terminate_grace <= 0 or poll_interval <= 0):
        raise ValueError("monitor timing thresholds are invalid")
    if max_calls_without_progress is not None and (
            type(max_calls_without_progress) is not int or max_calls_without_progress < 1):
        raise ValueError("optional progress call threshold must be positive")
    if process_observer is None:
        process_observer = OwnedProcessTree(control.get(task_id).process)
    last_activity = started
    registry_seen = False
    last_bottom_activity: tuple[int, int, int, int, int] | None = None
    last_semantic: tuple[int, int, int, int] | None = None
    calls_at_semantic_progress = 0
    stop_requested_at = None
    stop_reason = None
    signal_sent = False
    stop_error = None
    signal_error = None
    termination_deadline = None
    process_facts = {}
    samples = []
    live_path = run_dir.parent / "rpnh-monitor.live.json"
    thresholds = {
        "startup_seconds": startup_timeout,
        "bottom_activity_seconds": activity_timeout,
        "stop_grace_seconds": stop_grace,
        "terminate_grace_seconds": terminate_grace,
        "poll_interval_seconds": poll_interval,
        "max_calls_without_progress": max_calls_without_progress,
        "whole_task_deadline": deadline,
    }

    def publish_live(status, *, phase: str, forced: bool = False) -> None:
        write_replace(live_path, {
            "schema_version": "rpnh-ha/task-monitor/v2",
            "thresholds": thresholds,
            "phase": phase,
            "stop_trigger": stop_reason,
            "forced_termination": forced,
            "samples": samples,
            "latest_status": status,
            "process_observation": process_facts,
            "stop_error": stop_error, "signal_error": signal_error,
        })

    live_initialized = False
    while True:
        status = control.status(task_id)
        if not live_initialized:
            publish_live(status, phase="starting")
            live_initialized = True
        process_facts = process_observer.sample()
        if (process_facts.get("process_exit_confirmed") is True
                and process_facts.get("observed_tree_quiescent") is True):
            report = {
                "schema_version": "rpnh-ha/task-monitor/v2",
                "thresholds": thresholds,
                "stop_trigger": stop_reason,
                "forced_termination": signal_sent,
                "process_exit_confirmed": True,
                "observed_tree_quiescent": True,
                "final_snapshot_permitted": True,
                "process_observation": process_facts,
                "stop_error": stop_error, "signal_error": signal_error,
                "samples": samples, "final_status": status,
            }
            publish_live(status, phase="exit_confirmed", forced=signal_sent)
            return status, report
        now = time.monotonic()
        try:
            progress = _registry_progress(run_dir)
        except (OSError, RuntimeError, TypeError, ValueError,
                json.JSONDecodeError):
            # Registry creation and a poll can overlap; the next sample reads
            # one complete SQLite snapshot.
            progress = None
        if progress is not None:
            registry_seen = True
            bottom_activity = (
                progress.model_calls,
                *progress.semantic_signature,
            )
            if bottom_activity != last_bottom_activity:
                last_bottom_activity = bottom_activity
                last_activity = now
            if progress.semantic_signature != last_semantic:
                last_semantic = progress.semantic_signature
                calls_at_semantic_progress = progress.model_calls
            sample = {
                "elapsed_seconds": round(now - started, 3),
                "process_status": status["process_status"],
                "max_ordinal": progress.max_ordinal,
                "model_calls": progress.model_calls,
                "managed_receipts": progress.managed_receipts,
                "written_products": progress.written_products,
                "completed_interactions": progress.completed_interactions,
                "terminal_evidence": progress.terminal_evidence,
                "maximum_exact_replay": progress.maximum_exact_replay,
            }
            if not samples or any(
                    sample[key] != samples[-1].get(key)
                    for key in sample if key != "elapsed_seconds"):
                samples.append(sample)
                publish_live(status, phase="running")
            if stop_reason is None and progress.maximum_exact_replay >= 3:
                stop_reason = "repeated_exact_managed_receipt"
            elif (stop_reason is None and max_calls_without_progress is not None
                  and progress.model_calls - calls_at_semantic_progress >= max_calls_without_progress):
                stop_reason = "model_calls_without_semantic_progress"
        elif (stop_reason is None and not registry_seen
              and now >= startup_deadline):
            stop_reason = "registry_startup_timeout"
        if (stop_reason is None and registry_seen
                and now - last_activity >= activity_timeout):
            stop_reason = "bottom_task_activity_timeout"
        if stop_reason is None and deadline is not None and now >= deadline:
            stop_reason = "wall_clock_timeout"
        if stop_reason is not None and stop_requested_at is None:
            try:
                control.stop(task_id, startup_safe=True)
            except (OSError, RuntimeError, ValueError) as exc:
                stop_error = type(exc).__name__ + ": " + str(exc)
            stop_requested_at = now
            publish_live(status, phase="stopping")
        if (stop_requested_at is not None and termination_deadline is None
                and now - stop_requested_at >= stop_grace):
            try:
                sent = process_observer.send_signal(signal.SIGTERM)
                signal_sent = bool(sent)
            except (OSError, RuntimeError, ValueError) as exc:
                signal_error = type(exc).__name__ + ": " + str(exc)
            termination_deadline = time.monotonic() + terminate_grace
            publish_live(status, phase="signal_sent", forced=signal_sent)
        if termination_deadline is not None and time.monotonic() >= termination_deadline:
            # A signal or Registry terminal label is not evidence of process exit.
            report = {
                "schema_version": "rpnh-ha/task-monitor/v2",
                "thresholds": thresholds, "stop_trigger": stop_reason,
                "forced_termination": signal_sent,
                "process_exit_confirmed": process_facts.get("process_exit_confirmed", False),
                "observed_tree_quiescent": False, "final_snapshot_permitted": False,
                "process_observation": process_facts,
                "stop_error": stop_error, "signal_error": signal_error,
                "samples": samples, "final_status": status,
            }
            publish_live(status, phase="shutdown_unconfirmed", forced=signal_sent)
            write_replace(run_dir.parent / "rpnh-monitor.json", report)
            raise NativeTerminationUnconfirmed(report)
        await asyncio.sleep(poll_interval)


class LocalNativeDriver:
    """Launch one isolated RPNH worker and export only Registry-backed facts."""

    async def execute(self, request: DriverRequest, observations) -> DriverResult:
        from cpn.plugins.catalog import load_catalog
        from cpn.rpnh.agent_tasks import AgentTaskSpec
        from cpn.rpnh.task_control import TaskControl

        if request.run_dir.exists():
            raise FileExistsError("native driver requires a fresh RPNH run directory")
        graph, roles_by_node = build_business_workflow(request.task)
        bindings = managed_bindings(request, roles_by_node)
        catalog = load_catalog(request.plugin_configuration)
        if request.configuration_condition:
            from .jsonio import write_new
            write_new(request.run_dir.parent / "configuration_condition.json",
                      {**condition_manifest(request.task, graph, bindings=bindings,
                                            max_model_calls=request.limits.max_model_calls),
                       "native_plugin_catalog_digest": catalog.digest})
        spec = AgentTaskSpec(
            run_dir=Path(request.run_dir),
            prompt=request.initial_input,
            stages=(),
            execution_config_path=Path(request.execution_profile),
            workflow_graph=graph,
            max_attempts_per_stage=_attempts_per_node(
                request.limits.max_model_calls, len(graph.nodes)),
            max_parallel_nodes=2,
            owner_statement=(
                "User-authorized RPNH HarnessAudit bridge execution"),
            plugin_configuration=request.plugin_configuration,
            plugin_catalog_digest=catalog.digest,
            managed_bindings=bindings,
        )
        control = TaskControl(request.run_dir.parent / "rpnh-control")
        handle = control.start(spec)
        try:
            status, monitor = await _wait_for_exit(
                control, handle.task_id,
                run_dir=request.run_dir,
                deadline=(
                    None if request.limits.max_seconds is None else
                    time.monotonic() + request.limits.max_seconds),
                provider_timeout=_profile_timeout(request.execution_profile))
        except NativeTerminationUnconfirmed:
            raise
        except (Exception, asyncio.CancelledError) as exc:
            from .jsonio import write_replace
            try:
                control.stop(handle.task_id, startup_safe=True)
            except (OSError, RuntimeError, ValueError):
                pass
            report = {"schema_version": "rpnh-ha/task-monitor/v2",
                      "phase": "shutdown_unconfirmed",
                      "final_snapshot_permitted": False,
                      "process_exit_confirmed": False,
                      "observed_tree_quiescent": False,
                      "stop_trigger": "monitor_error_or_cancellation",
                      "error": type(exc).__name__ + ": " + str(exc)}
            write_replace(request.run_dir.parent / "rpnh-monitor.json", report)
            raise NativeTerminationUnconfirmed(report) from exc
        from .jsonio import write_new
        write_new(request.run_dir.parent / "rpnh-monitor.json", monitor)
        terminal = None
        nonterminal = _nonterminal_worker_result(status, request.run_dir)
        stop_reason = (
            monitor["stop_trigger"]
            or "worker_" + status["process_status"].lower())
        final_output = ""
        try:
            terminal = control.result(handle.task_id)
        except RuntimeError:
            pass
        else:
            stop_reason = terminal["run_outcome"]
            final_output = terminal["output"]
            if not isinstance(final_output, str):
                raise ValueError("native workflow terminal output must be text")
        if terminal is None and nonterminal is not None:
            stop_reason = nonterminal["stop_reason"]

        exported = export_registry(
            request.run_dir, roles_by_node, observations,
            terminal_evidence_ref=(
                None if terminal is None else terminal["terminal_evidence_ref"]),
            stop_reason=stop_reason,
            include_provisional=(
                nonterminal is not None
                and stop_reason == "blocked_or_waiting"),
        )
        write_new(request.run_dir.parent / "capture_diagnostics.json",
                  exported.capture_diagnostics)
        if (request.limits.max_model_calls is not None
                and exported.actual_model_calls
                > request.limits.max_model_calls):
            raise RuntimeError("RPNH exceeded the admitted physical model-call cap")
        if (nonterminal is not None
                and sum(nonterminal["actual_model_call_counts"])
                != exported.actual_model_calls):
            raise RuntimeError(
                "worker and Registry model-call accounting differ")
        return DriverResult(
            execution_mode="native_live",
            final_output=final_output,
            terminal_evidence_ref=exported.terminal_evidence_ref,
            stop_reason=exported.stop_reason,
            actual_model_calls=exported.actual_model_calls,
            registry_records=exported.registry_records,
            roles_observed=exported.roles_observed,
            context_capture_complete=exported.context_capture_complete,
            model_identity=exported.model_identity,
        )


class ScriptedLocalNativeDriver(LocalNativeDriver):
    """Same native driver, explicitly labelled for deterministic acceptance."""

    async def execute(self, request: DriverRequest, observations) -> DriverResult:
        result = await super().execute(request, observations)
        return DriverResult(
            execution_mode="native_scripted",
            final_output=result.final_output,
            terminal_evidence_ref=result.terminal_evidence_ref,
            stop_reason=result.stop_reason,
            actual_model_calls=result.actual_model_calls,
            registry_records=result.registry_records,
            roles_observed=result.roles_observed,
            context_capture_complete=result.context_capture_complete,
            model_identity=result.model_identity,
        )


__all__ = (
    "LocalNativeDriver", "ScriptedLocalNativeDriver", "managed_bindings")
