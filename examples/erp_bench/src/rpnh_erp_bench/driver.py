"""One authorized, fresh ERP condition; runtime handles stay outside SUT tools."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import traceback

from .bridge import Bridge
from .environment import OfficialWorld
from .endpoint import is_codex_profile, prepare_codex_endpoint
from .evidence import (STAGES, artifact_record, assert_public_safe,
                       build_result_manifest, record_original_score,
                       stage_observation, stage_record, write_result_manifest)
from .export import new_path, source_identity, write_new
from .native import build_spec, freeze_profile, profile_identity, run_owner, safe_owner_projection
from .sandbox import HarborBackend
from .scoring import parse_original_score
from .source import json_bytes, validate_task


ADAPTATIONS = (
    "Solver container egress disabled; upstream allow_internet remains unchanged in task.toml.",
    "Nonroot solver and local PostgreSQL access isolation; trusted setup has NET_ADMIN.",
    "RPNH managed admission covers each full Python script, not each Odoo transaction.",
    "Harbor 0.24 Compose build definition uses explicit host-network build entitlement.",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _world_projection(world):
    """Allowlist identities; never publish full inspect, logs, mounts or errors."""
    metadata = world.metadata
    preflight = metadata.get("preflight", {})
    images = metadata.get("image", [])
    compatibility = metadata.get("build_compatibility")
    return {
        "condition_id": world.condition_id, "phase": world.phase,
        "container_id": world.container_id, "harbor_version": "0.24.0",
        "official_limits": world.source.official_limits,
        "resources": {key: preflight[key] for key in
                      ("disk_free_bytes", "cpu", "memory_bytes", "storage_driver", "architecture")
                      if key in preflight},
        "images": [{key: row[key] for key in ("Id", "RepoTags", "RepoDigests", "Size") if key in row}
                   for row in images],
        "resolved_base_images": metadata.get("resolved_base_images", {}),
        "dependency_versions": metadata.get("dependency_versions", {}),
        "firewall_versions": metadata.get("firewall_versions", []),
        "container_limits": metadata.get("container_limits", {}),
        "build_compatibility": ({key: compatibility[key] for key in
            ("condition", "original_dockerfile_sha256", "adapted_dockerfile_sha256", "odoo_client_lib_requested")
            if key in compatibility} if compatibility else None),
        "verifier_exit_code": metadata.get("verifier_exit_code"),
        "initial_identity": world.initial_identity,
        "terminal_snapshot": world.snapshot,
        "isolation": ({"probe": world.sandbox.probe, "condition": world.sandbox.condition}
                      if world.sandbox else None),
        "stages": {name: {key: row[key] for key in ("status", "at", "timeout_seconds") if key in row}
                   for name, row in metadata.get("stages", {}).items()},
    }


def _registered_reference(projection):
    for key in ("terminal_evidence_ref", "terminal_result_ref"):
        if projection.get(key):
            return projection[key]
    for action in projection.get("actions", []):
        if action.get("agent_action_ref"):
            return action["agent_action_ref"]
    return None


async def run_trial(*, upstream, task_id, execution_selection, run_root,
                    condition_id, source_root, shared_validator,
                    authorize_existing_model=False, previous_record=None,
                    firewall_packages=(), build_compatibility=False, codex_binary=None):
    """Run exactly one condition, preserving every failed attempt in its root.

    Stage ``evaluation=passed`` means the original verifier produced complete
    reports. The independent original ``reward.passed`` is business success.
    Offline/mock acceptance belongs to separate measured records, never inferred
    from this live trial. No automatic retry, restore or solver resume occurs.
    """
    if authorize_existing_model is not True:
        raise ValueError("explicit authorization for the existing model is required")
    task = validate_task(upstream, task_id)
    frozen_source = source_identity(source_root)
    root = Path(run_root).absolute()
    # Check every ancestor before the first write; evidence is append-only.
    new_path(root, "owner.json")
    if root.exists():
        raise FileExistsError("condition run root already exists; choose a new condition")
    if len(os.fsencode(root / "r" / "owner.sock")) > 107:
        raise ValueError("run root must be shorter for real AF_UNIX owner channels")
    root.mkdir(parents=True, mode=0o700)
    endpoint_metadata = None
    endpoint_options = {}
    if codex_binary is not None:
        executable, endpoint_metadata = prepare_codex_endpoint(codex_binary, root / "transport")
        endpoint_options["codex_executable"] = executable
    frozen_selection = freeze_profile(execution_selection, root / "profile", **endpoint_options)
    if endpoint_metadata is None and is_codex_profile(frozen_selection):
        raise ValueError("Codex ERP trials require --codex-binary for the closed response endpoint")
    model_config = profile_identity(frozen_selection)
    public = root / "public"
    public.mkdir(mode=0o700)
    artifacts, scores, registry = [], [], None
    stages = {name: stage_record("not_run", reason="Separate acceptance record; not measured by this live condition.")
              for name in STAGES}
    windows = {}
    owner, world, backend, bridge = None, None, None, None
    owner_job = None
    request_stop = threading.Event()
    failure, failed_phase = None, None
    phase = "environment"

    def publish(name, value, identifier, role):
        assert_public_safe(value)
        write_new(public, name, json_bytes(value))
        row = artifact_record(public, name, artifact_id=identifier, role=role)
        artifacts.append(row)
        return row

    config_row = publish("model-configuration.json", model_config, "model-configuration", "model_configuration")
    if endpoint_metadata is not None:
        publish("endpoint-condition.json", endpoint_metadata, "endpoint-condition", "environment")
    try:
        world_options = {}
        if firewall_packages:
            world_options["firewall_packages"] = tuple(firewall_packages)
        if build_compatibility:
            world_options["build_compatibility"] = True
        world = OfficialWorld(task.task_path, root / "w", condition_id, **world_options)
        await world.start()
        await world.prepare_solver()
        backend = HarborBackend(world.environment, asyncio.get_running_loop(), world.sandbox)
        candidate_bridge = Bridge(root / "bridge.sock", condition_id, backend)
        candidate_bridge.__enter__()
        bridge = candidate_bridge  # Only successfully entered resources need exit.
        spec = build_spec(root / "r", frozen_selection, root / "bridge.sock",
                          condition_id, task.instruction_bytes.decode("utf-8"))
        if source_identity(source_root) != frozen_source:
            raise RuntimeError("source changed before model launch; use a new condition")
        phase = "owner"
        windows["owner"] = [_now(), None]
        owner_job = asyncio.create_task(asyncio.to_thread(run_owner, spec, root / "control",
            agent_timeout_seconds=int(task.official_limits["agent_timeout_sec"]),
            stop_requested=request_stop.is_set))
        try:
            owner = await asyncio.shield(owner_job)
        finally:
            if not owner_job.done():
                request_stop.set()
                owner = await asyncio.shield(owner_job)
            windows["owner"][1] = _now()
            bridge.close_admission()
        write_new(root, "owner.json", json_bytes(owner))
        phase = "quiescence"
        quiescence = await backend.quiesce()
        publish("quiescence.json", quiescence, "quiescence", "environment")
        closing_bridge = bridge
        bridge = None
        closing_bridge.__exit__(None, None, None)
        if owner["owner_quiescent"] is not True:
            raise RuntimeError("owner is not quiescent; grading is forbidden")
        phase = "freeze"
        await world.freeze(owner_quiescent=True, bridge_quiescent=True)
        phase = "evaluation"
        windows["evaluation"] = [_now(), None]
        try:
            await world.grade(owner_quiescent=True, bridge_quiescent=True)
            original = parse_original_score(world.paths.verifier_dir, task_id,
                verifier_exit_code=world.metadata.get("verifier_exit_code"))
            if original is None:
                raise RuntimeError("original verifier reports unavailable")
            artifacts.extend(record_original_score(original, public))
            scores.append(original.score_record(claim="grader_compatibility", evidence=["original-score"]))
        finally:
            windows["evaluation"][1] = _now()
    except (Exception, asyncio.CancelledError) as exc:
        failure, failed_phase = type(exc).__name__, phase
        write_new(root, "failure.txt", traceback.format_exc().encode())
    finally:
        # Closing host admission is necessary but never sufficient to stop a
        # writer that has already entered the container.
        try:
            if bridge is not None:
                bridge.close_admission()
                try:
                    if backend is not None:
                        await backend.quiesce()
                except Exception:
                    failure, failed_phase = failure or "QuiescenceError", failed_phase or "quiescence"
                    write_new(root, "cleanup-quiescence.txt", traceback.format_exc().encode())
                finally:
                    try:
                        bridge.__exit__(None, None, None)
                    except Exception:
                        failure, failed_phase = failure or "BridgeTeardownError", failed_phase or "quiescence"
                        write_new(root, "cleanup-bridge.txt", traceback.format_exc().encode())
        finally:
            if world is not None:
                try:
                    await world.stop()
                except Exception:
                    failure, failed_phase = failure or "TeardownError", failed_phase or "teardown"
                    write_new(root, "cleanup-world.txt", traceback.format_exc().encode())

    projection = None
    if owner is not None:
        projection = safe_owner_projection(owner)
        publish("owner-projection.json", projection, "owner-projection", "registry_projection")
        ref = _registered_reference(projection)
        if ref is not None:
            registry = [{"purpose": "actual_owner_execution", "status": "available",
                         "task_id": projection["task_id"], "projection_artifact_id": "owner-projection",
                         "ref": ref, "reason": None}]
    if world is not None:
        publish("world-projection.json", _world_projection(world), "world-projection", "environment")
    publish("lifecycle.json", {"failure_class": failure, "failed_phase": failed_phase,
        "owner_started": "owner" in windows, "original_verifier_completed": bool(scores),
        "business_passed": original.passed if scores else None,
        "scope": "Fresh isolated condition; no restore, retry or resumed solver."}, "lifecycle", "test_log")

    counts = projection.get("actual_model_call_counts") if projection else None
    verified_counts = (owner is not None and owner.get("terminal") is not None
                       and owner.get("owner_quiescent") is True and isinstance(counts, list)
                       and len(counts) == 2 and all(type(n) is int and n >= 0 for n in counts))
    calls = ({"status": "verified", "real_provider_calls": counts[0], "fake_provider_calls": 0,
              "evidence": ["owner-projection"], "reason": None} if verified_counts else
             {"status": "unknown", "real_provider_calls": None, "fake_provider_calls": None,
              "evidence": ["owner-projection"] if projection else [],
              "reason": "No complete terminal call accounting; partial Registry counters are retained separately."})

    def measured(name, status, window, command, evidence_ids, exit_code, reason=None):
        publish(name + "-observation.json", stage_observation(status=status,
            commands=[command], started_at=window[0], ended_at=window[1],
            exit_code=exit_code,
            tested_tree_sha256=frozen_source["owned_tree_sha256"], reason=reason), name + "-observation", "test_log")
        stages[name] = stage_record(status, commands=[command],
            evidence=[*evidence_ids, name + "-observation"], reason=reason)

    if "owner" in windows:
        owner_exit = owner.get("process_exit_code") if owner else None
        native_ok = (owner is not None and owner.get("owner_quiescent") is True
                     and registry is not None and owner_exit == 0)
        native_status = "passed" if native_ok else ("failed" if owner_exit is not None else "unknown")
        measured("native", native_status, windows["owner"],
                 "TaskControl.start/status/result/result_evidence/stop", ["lifecycle"] + (["owner-projection"] if projection else []),
                 owner_exit,
                 None if native_ok else "Owner execution did not establish quiescent registered native evidence.")
        provider_ok = (verified_counts and counts[0] > 0 and owner_exit == 0
                       and owner["terminal"].get("run_outcome") == "complete")
        measured("provider", "passed" if provider_ok else ("failed" if owner_exit is not None else "unknown"), windows["owner"],
                 "RPNH existing exact-model local_process execution", ["lifecycle"] + (["owner-projection"] if projection else []),
                 owner_exit,
                 None if provider_ok else "Provider condition did not reach a complete native terminal.")
    else:
        for name in ("native", "provider"):
            stages[name] = stage_record("blocked", evidence=["lifecycle"],
                reason="Official world or isolation prerequisite failed before owner launch.")
    if "evaluation" in windows:
        verifier_exit = world.metadata.get("verifier_exit_code")
        measured("evaluation", "passed" if scores else ("failed" if verifier_exit is not None else "unknown"), windows["evaluation"],
                 "Harbor Verifier.verify with unchanged original tests/test.sh", ["lifecycle"] + (["original-score"] if scores else []),
                 verifier_exit,
                 "Completed verifier is independent of the emitted business passed threshold." if scores else "Original verifier did not complete.")
    else:
        stages["evaluation"] = stage_record("blocked", evidence=["lifecycle"],
            reason="No proven frozen quiescent world was available for original evaluation.")

    predecessors = []
    if previous_record is not None:
        previous = json.loads(Path(previous_record).read_bytes())
        publish("previous-record.json", previous, "previous-record", "history")
        predecessors.append("previous-record")
    if source_identity(source_root) != frozen_source:
        raise RuntimeError("tested source changed; retained raw condition cannot be relabeled as current source")
    manifest = build_result_manifest(record_id=condition_id, task=task, source_root=source_root,
        artifacts_root=public, shared_validator_path=shared_validator,
        condition={"runtime": "RPNH_owned_official_Harbor_world", "world_id": condition_id,
                   "adaptation": "network_none_nonroot_full_python_script", "official_limits": task.official_limits,
                   "build_compatibility": bool(build_compatibility),
                   "firewall_packages": [Path(p).name for p in firewall_packages],
                   "codex_endpoint": endpoint_metadata,
                   "retry": "none", "world_reuse": "none"},
        stages=stages, artifacts=artifacts, registry_refs=registry, scores=scores,
        model={"kind": "real", "provider": model_config["adapter_kind"],
               "exact_model": model_config["model_condition"], "configuration_sha256": config_row["sha256"],
               "configuration_artifact_id": config_row["id"], "unavailable_reason": None},
        model_calls=calls, adaptations=(*ADAPTATIONS,
            *(("Explicit build compatibility: uv system install permits the disposable container's managed Python.",)
              if build_compatibility else ()),
            *(("Explicit offline firewall dependency installation before solver admission.",) if firewall_packages else ()),
            *(("Official Codex binary bypasses global wrapper; native web search and project documents disabled.",)
              if endpoint_metadata is not None else ())),
        previous_record_artifacts=predecessors,
        public_notes=["Adapted runtime; no claim of an unadapted upstream network condition.",
                      "Task mode identifies original task inputs; score claim is grader_compatibility for this adapted runtime.",
                      "Evaluation pass means complete original reports; reward.passed is business success.",
                      "Registry counter is total settled calls plus post-limit excess; the second value is not additional calls.",
                      "Static actor definition is supplied; semantic use and numeric reuse are not inferred."],
        privacy_reviewed=False)
    write_result_manifest(manifest, public / "result-manifest.json", shared_validator_path=shared_validator,
                          artifacts_root=public, source_root=source_root)
    return {"record_id": condition_id, "stages": stages, "model_calls": calls,
            "overall_score": original.overall_score if scores else None,
            "business_passed": original.passed if scores else None,
            "failure_class": failure, "failed_phase": failed_phase}
