"""Two-round, local-only orchestration for the frozen formal RRSI v0.6 B0 run.

The campaign deliberately composes the public role and Policy child runners.
It owns neither a second workflow nor Registry internals; every model-facing
occurrence remains a child application run.
"""
from __future__ import annotations

from dataclasses import asdict
from difflib import unified_diff
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .formal_execution import raise_if_stopped
from .formal_observation import ObservedInputPort, usage_summary
from .formal_reporting import (
    CampaignProgress, add_secondary_failure, failure_summary,
)
from .formal_policy import (
    FormalPolicyContractError,
    _build_and_validate_messages,
    _restricted_build_messages,
    run_policy_trial,
)
from .formal_protocol import (
    FormalProtocol,
    formal_protocol_mapping,
    role_input_manifest,
    validate_execution_selection,
)
from .formal_role import ROLE_LIMITS, run_role_session
from .method import (
    AggregateResult,
    History,
    HistoryEntry,
    Incumbent,
    CandidateEvaluation,
    SelectionParameters,
    TaskPlan,
    TrialObservation,
    aggregate_trials,
    build_representative_traces,
    calibrate_noise_band,
    cost_rule_passes,
    project_trial_rows,
    relative_cost_change,
    select_candidate,
    TraceTask,
    TraceTrial,
)


_FORBIDDEN_IDENTITY = {"hash", "checksum", "fingerprint", "digest"}
_PRECHECK = re.compile(r"\b(?:todo|placeholder|generic|pass)\b", re.IGNORECASE)


class FormalCampaignError(ValueError):
    """The local formal campaign request violates its closed boundary."""


def _json(value: Any) -> Any:
    def thaw(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): thaw(value) for key, value in item.items()}
        if isinstance(item, tuple):
            return [thaw(value) for value in item]
        if isinstance(item, list):
            return [thaw(value) for value in item]
        return item
    return json.loads(json.dumps(thaw(value), ensure_ascii=False, allow_nan=False))


def _ref(value: Any) -> Any:
    """Keep public child references opaque while preserving their exact shape."""
    return _json(value) if value is not None else None


def _assert_no_identity_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_IDENTITY:
                raise FormalCampaignError("CandidateManifest must not contain identity fields")
            _assert_no_identity_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_identity_fields(item)


def _diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> str:
    before_by_path = {item["path"]: item["content"] for item in before}
    chunks: list[str] = []
    for item in after:
        path = item["path"]
        old = before_by_path.get(path, "")
        if old != item["content"]:
            chunks.extend(unified_diff(old.splitlines(keepends=True),
                                       item["content"].splitlines(keepends=True),
                                       fromfile=f"a/{path}", tofile=f"b/{path}"))
    return "".join(chunks)


def build_candidate_manifest(*, protocol: FormalProtocol, round_index: int,
                             candidate_id: str, parent_candidate_id: str,
                             source_files: list[dict[str, Any]],
                             source_request: Mapping[str, Any],
                             source_request_ref: Any, analysis_ref: Any,
                             proposal_envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze a candidate from the typed Proposer output, without projections.

    The Proposer result is the sole source for post-edit members.  This helper
    intentionally has no settled-workspace field and no content identity.
    """
    result = proposal_envelope.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("payload"), Mapping):
        raise FormalCampaignError("Proposer did not return a typed result payload")
    payload = result["payload"]
    proposed = payload.get("files")
    if not isinstance(proposed, list):
        raise FormalCampaignError("Proposer result has no source member list")
    baseline = {item["path"]: item for item in source_files}
    by_path = {item.get("path"): item for item in proposed if isinstance(item, Mapping)}
    if set(by_path) != set(baseline):
        raise FormalCampaignError("Proposer source members differ from current source")
    members: list[dict[str, Any]] = []
    for source in source_files:
        path = source["path"]
        proposed_member = by_path[path]
        after = proposed_member.get("content")
        if not isinstance(after, str) or proposed_member.get("mode") != source["mode"]:
            raise FormalCampaignError("Proposer changed a source mode or omitted source text")
        operation = "replace" if after != source["content"] else "unchanged"
        if path != "policy.py" and operation != "unchanged":
            raise FormalCampaignError("only policy.py may be changed")
        members.append({"path": path, "mode": source["mode"],
                        "purpose": source["purpose"], "operation": operation,
                        "before": source["content"], "after": after})
    run = proposal_envelope.get("run")
    manifest = {
        "schema_version": "rrsi_v06/formal_candidate_manifest/v1",
        "protocol_id": protocol.protocol_id,
        "round": round_index,
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_candidate_id,
        "source_request": _json(source_request),
        "source_request_ref": _ref(source_request_ref),
        "analysis_ref": _ref(analysis_ref),
        "proposal_role_output_ref": _ref(run.get("result_resource_ref") if isinstance(run, Mapping) else None),
        "proposal_role_run_ref": _ref(run.get("run_ref") if isinstance(run, Mapping) else None),
        "members": members,
        "whole_diff": _diff(source_files, [{**item, "content": item["after"]} for item in members]),
        "editable_paths": ["policy.py"],
        "check": "python_compile_import_build_messages/v1",
    }
    _assert_no_identity_fields(manifest)
    return manifest


def fixed_precheck(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The no-model Critic gate required before a Critic child can be launched."""
    diff = manifest.get("whole_diff")
    if not isinstance(diff, str) or not diff.strip():
        return {"passed": False, "reason": "empty_diff"}
    match = _PRECHECK.search(diff)
    if match:
        return {"passed": False, "reason": "generic_pattern", "pattern": match.group(0)}
    return {"passed": True, "reason": "clean"}


def _validate_start(*, run_dir: Path, protocol: Any, selection: Any) -> Path:
    if not isinstance(protocol, FormalProtocol):
        raise FormalCampaignError("protocol must be a validated FormalProtocol")
    # This additionally makes a detached, finite JSON copy of the frozen value.
    formal_protocol_mapping(protocol)
    try:
        validate_execution_selection(protocol, selection)
    except Exception as exc:
        raise FormalCampaignError(
            "formal campaign requires a complete RPNH execution selection") from exc
    import cpn
    if not Path(cpn.__file__).resolve().is_file():
        raise FormalCampaignError("formal campaign could not load RPNH")
    destination = Path(run_dir).resolve(strict=False)
    if destination.exists():
        raise FormalCampaignError("formal campaign run_dir must be absent")
    return destination


def _role_request(protocol: FormalProtocol, *, round_id: str, occurrence_id: str,
                  parent_action_ref: str, role: str, input_value: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema_version": "rrsi_v06/formal_role_request/v1",
            "protocol_id": protocol.protocol_id, "round_id": round_id,
            "occurrence_id": occurrence_id, "parent_action_ref": parent_action_ref,
            "role": role, "limits": dict(ROLE_LIMITS[role]), "input": _json(input_value)}


def _role_payload(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    result = envelope.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("payload"), Mapping):
        raise FormalCampaignError("role child did not return typed terminal payload")
    return result["payload"]


def _child_evidence(kind: str, label: str, envelope: Mapping[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    run = envelope.get("run", envelope.get("run_refs"))
    result = envelope.get("result")
    transition_trace = (envelope.get("transition_trace")
                        if kind == "policy"
                        else run.get("transition_trace")
                        if isinstance(run, Mapping) else None)
    return {"kind": kind, "label": label, "run_ref": _ref(run),
            "termination": (result.get("termination")
                            if isinstance(result, Mapping) else None),
            "terminal_evidence_ref": _ref(envelope.get("terminal_evidence_ref") if "terminal_evidence_ref" in envelope
                                            else run.get("terminal_evidence_version_id") if isinstance(run, Mapping) else None),
            "output_ref": _ref(envelope.get("output_ref") if "output_ref" in envelope
                                else run.get("result_resource_ref") if isinstance(run, Mapping) else None),
            "transition_trace": _ref(transition_trace),
            "attempts": _json(observations)}


def _production_evidence_complete(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    run_ids: set[str] = set()
    attempt_ids: set[str] = set()
    for row in rows:
        run = row.get("run_ref")
        if not isinstance(run, Mapping):
            return False
        run_ref = run.get("run_ref")
        if not isinstance(run_ref, Mapping):
            return False
        logical_id = run_ref.get("logical_id")
        if not isinstance(logical_id, str) or logical_id in run_ids:
            return False
        run_ids.add(logical_id)
        if row.get("terminal_evidence_ref") is None or row.get("output_ref") is None:
            return False
        trace = row.get("transition_trace")
        if row.get("kind") == "policy":
            if trace != ["policy.prepare", "policy.model", "policy.grade"]:
                return False
        elif (not isinstance(trace, list) or not trace
              or trace[0] != "role.init" or trace[-1] != "role.act"):
            return False
        attempts = row.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return False
        for attempt in attempts:
            if (not isinstance(attempt, Mapping)
                    or attempt.get("submission_state") != "response_observed"
                    or not isinstance(attempt.get("canonical_request"), Mapping)
                    or not isinstance(attempt.get("canonical_response"), Mapping)):
                return False
            attempt_ref = attempt.get("attempt_ref")
            attempt_id = (attempt_ref.get("logical_id")
                          if isinstance(attempt_ref, Mapping) else None)
            if not isinstance(attempt_id, str) or attempt_id in attempt_ids:
                return False
            attempt_ids.add(attempt_id)
    return True


def _sources(protocol: FormalProtocol) -> list[dict[str, Any]]:
    return [{"path": item["path"], "content": item["content"], "mode": item["mode"],
             "purpose": item["purpose"]} for item in protocol.source_fixture["files"]]


def _smoke(run_dir: Path, manifest: Mapping[str, Any],
           protocol: FormalProtocol) -> dict[str, Any]:
    root = run_dir / "smoke"
    root.mkdir(parents=True, exist_ok=False)
    policy_path: Path | None = None
    try:
        for member in manifest["members"]:
            target = (root / member["path"]).resolve()
            if not target.is_relative_to(root.resolve()):
                raise FormalCampaignError("smoke source escaped candidate root")
            if not isinstance(member.get("after"), str):
                raise FormalCampaignError("smoke source text is malformed")
            if (isinstance(member.get("mode"), bool)
                    or not isinstance(member.get("mode"), int)):
                raise FormalCampaignError("smoke source mode is malformed")
            target.parent.mkdir(parents=True, exist_ok=True)
            if member["path"] == "policy.py":
                policy_path = target
        if policy_path is None:
            raise FormalCampaignError("CandidateManifest has no policy.py")
    except (KeyError, TypeError, ValueError) as exc:
        return {"passed": False, "check": "python_compile_import_build_messages/v1",
                "error": f"{type(exc).__name__}: {exc}"}
    for member in manifest["members"]:
        target = (root / member["path"]).resolve()
        target.write_text(member["after"], encoding="utf-8")
        target.chmod(member["mode"])
    try:
        build_messages = _restricted_build_messages(policy_path)
    except FormalPolicyContractError as exc:
        return {"passed": False,
                "check": "python_compile_import_build_messages_evolve_inputs/v1",
                "error": f"{type(exc).__name__}: {exc}"}
    for task in protocol.evolve.tasks:
        try:
            _build_and_validate_messages(
                build_messages, _json(task.raw_input))
        except FormalPolicyContractError as exc:
            return {"passed": False,
                    "check": "python_compile_import_build_messages_evolve_inputs/v1",
                    "task_id": task.task_id,
                    "error": f"{type(exc).__name__}: {exc}"}
    return {"passed": True,
            "check": "python_compile_import_build_messages_evolve_inputs/v1",
            "evolve_inputs_checked": len(protocol.evolve.tasks)}


def _token(observations: list[dict[str, Any]]) -> int | None:
    if len(observations) != 1:
        return None
    usage = observations[0].get("usage")
    total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
    return total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None


def _invoke_child(*, kind, label, owner, run_dir, runner, base_port,
                  progress, interruption_requested=None, **kwargs):
    """Keep started/returned children and attempts even when a runner raises."""
    observed = ObservedInputPort(base_port)
    previous_stage = progress.stage
    progress.stage = f"{kind}:{label}:before_start"
    entry = {"kind": kind, "label": label,
             "run_dir": str(run_dir.relative_to(progress.destination)),
             "status": "planned", "run_ref": None, "termination": None,
             "terminal_evidence_ref": None, "output_ref": None,
             "transition_trace": None, "attempts": []}
    progress.report["child_runs"].append(entry)
    primary_error = None
    try:
        progress.checkpoint()
        raise_if_stopped(interruption_requested)
        entry["status"] = "started"
        progress.stage = f"{kind}:{label}:runner"
        progress.checkpoint()
        if interruption_requested is not None:
            kwargs["interruption_requested"] = interruption_requested
        envelope = runner(run_dir=run_dir, llm_input_port=observed, **kwargs)
        progress.stage = f"{kind}:{label}:result"
        entry.update(_child_evidence(kind, label, envelope, []))
        entry["status"] = "returned"
        # Retain completed results even if the containing evaluation aborts.
        entry["result"] = _json(envelope.get("result"))
        return envelope
    except BaseException as exc:
        primary_error = exc
        error = failure_summary(exc, stage=progress.stage)
        entry["status"] = error["status"]
        entry["error"] = error
        if getattr(exc, "rrsi_run_refs", None) is not None:
            entry["run_ref"] = _ref(exc.rrsi_run_refs)
        if progress.failure is None:
            progress.failure = error
        raise
    finally:
        observations = list(observed.observations())
        entry["attempts"] = observations
        progress.report["attempt_inventory"].extend(
            {"owner": owner, "label": label, **row} for row in observations)
        try:
            progress.checkpoint()
        except BaseException as secondary:
            if primary_error is None:
                raise
            add_secondary_failure(primary_error, secondary, stage="report_write")
        finally:
            progress.stage = previous_stage


def _evaluate(*, run_dir: Path, protocol: FormalProtocol, selection: Any,
              evaluation_id: str, candidate_id: str,
              source_files: list[dict[str, Any]], split: str, repetitions: int,
              policy_runner: Callable[..., Mapping[str, Any]], base_port: Any,
              child_evidence: list[dict[str, Any]], attempt_inventory: list[dict[str, Any]],
              phase_order: list[str], progress: CampaignProgress,
              interruption_requested=None) -> dict[str, Any]:
    progress.stage = f"evaluation:{evaluation_id}:prepare"
    manifest = getattr(protocol, split)
    rows: list[dict[str, Any]] = []
    for task in manifest.tasks:
        for repetition in range(repetitions):
            attempt = 0
            occurrence_id = (f"{protocol.protocol_id}:{evaluation_id}:"
                             f"{task.task_id}:{repetition}:{attempt}")
            policy_sources = [{
                "path": item["path"], "content": item["content"],
                "mode": item["mode"],
            } for item in source_files]
            request = {"protocol_id": protocol.protocol_id, "split": split,
                       "evaluation_id": evaluation_id, "candidate_id": candidate_id,
                       "task_id": task.task_id, "repetition": repetition, "attempt": attempt,
                       "occurrence_id": occurrence_id, "source_files": policy_sources,
                       "raw_task_input": _json(task.raw_input), "expected": task.expected, "weight": task.weight}
            label = f"{evaluation_id}:{candidate_id}:{task.task_id}:{repetition}"
            phase_order.append(f"policy:{label}")
            envelope = _invoke_child(
                kind="policy", label=label, owner="Policy",
                run_dir=run_dir / "policy" / evaluation_id / candidate_id /
                f"{task.task_id}-{repetition}", runner=policy_runner,
                base_port=base_port, progress=progress,
                interruption_requested=interruption_requested,
                protocol=protocol, request=request, selection=selection)
            observations = child_evidence[-1]["attempts"]
            result = envelope.get("result")
            if not isinstance(result, Mapping):
                raise FormalCampaignError("Policy child did not return typed trial result")
            rows.append({"task_id": task.task_id, "repetition": repetition, "request": request,
                         "result": _json(result), "trial_result_ref": _ref(envelope.get("output_ref")),
                         "run_refs": _ref(envelope.get("run_refs")), "reward": result.get("reward", 0),
                         "weight": task.weight, "tokens": _token(observations),
                         "usage": usage_summary(observations)})
    progress.stage = f"evaluation:{evaluation_id}:aggregate"
    plans = [TaskPlan(task.task_id, tuple(float(task.weight) for _ in range(repetitions)))
             for task in manifest.tasks]
    observations = [TrialObservation(row["task_id"], row["repetition"], float(row["reward"]),
                                     row["tokens"], str(row["trial_result_ref"])) for row in rows]
    aggregate: AggregateResult = aggregate_trials(project_trial_rows(plans, observations))
    result = {"split": split, "evaluation_id": evaluation_id,
              "candidate_id": candidate_id, "trials": rows,
              "aggregate": asdict(aggregate), "source_files": _json(source_files)}
    progress.report["completed_evaluations"].append(result)
    return result


def _trace_input(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    tasks: list[TraceTask] = []
    text_by_ref: dict[str, str] = {}
    task_ids = list(dict.fromkeys(
        row["task_id"] for row in evaluation["trials"]))
    for task_id in task_ids:
        rows = [row for row in evaluation["trials"] if row["task_id"] == task_id]
        traces: list[TraceTrial] = []
        for row in rows:
            ref = str(row["trial_result_ref"])
            result = row["result"]
            text_by_ref[ref] = json.dumps({"raw": row["request"]["raw_task_input"],
                                           "expected": row["request"]["expected"],
                                           "output": result.get("output"), "reward": row["reward"],
                                           "response_trace": result.get("execution_trace"),
                                           "trial_result_ref": row["trial_result_ref"]},
                                          ensure_ascii=False, sort_keys=True)
            traces.append(TraceTrial(float(row["reward"]), ref))
        tasks.append(TraceTask(task_id, sum(item.reward for item in traces) / len(traces), tuple(traces)))
    selected = build_representative_traces(tasks,
                                           planned_task_ids=task_ids,
                                           n_fail_traces=1, n_success_traces=1)
    if not selected.sufficient:
        raise FormalCampaignError("evolve evaluation lacks representative traces")
    return [{"task_id": item.task_id, "group": item.group, "reward": item.reward,
             "trace_ref": item.trace_ref, "text": text_by_ref[item.trace_ref]}
            for item in selected.traces]


def run_formal_campaign(*, run_dir: Path, protocol, selection, llm_input_port=None,
                        role_runner=run_role_session, policy_runner=run_policy_trial,
                        interruption_requested=None) -> dict:
    """Run the complete two-round formal local campaign without real-provider shortcuts."""
    if interruption_requested is not None and not callable(interruption_requested):
        raise TypeError("interruption_requested must be callable")
    production_runners = (
        role_runner is run_role_session
        and policy_runner is run_policy_trial
        and llm_input_port is None)
    destination = _validate_start(run_dir=run_dir, protocol=protocol, selection=selection)
    destination.mkdir(parents=True)
    created_port = False
    primary_error = None
    child_evidence: list[dict[str, Any]] = []
    attempt_inventory: list[dict[str, Any]] = []
    phase_order: list[str] = []
    progress = CampaignProgress(destination, {
        "schema_version": "rrsi_v06/formal_campaign_report/v1",
        "scope": protocol.scope,
        "protocol": {"ref": protocol.protocol_id, "path": "protocol.json"},
        "execution_selection": {
            "model_condition": selection.input_target.model_condition,
            "adapter_kind": selection.adapter_kind,
        },
        "report_status": "running", "termination": None,
        "campaign_complete": False, "formal_rrsi_v06_local_complete": False,
        "child_runs": child_evidence, "attempt_inventory": attempt_inventory,
        "phase_order": phase_order, "completed_evaluations": [],
    })

    def invoke_role(*, label: str, request: Mapping[str, Any], digest_runner=None) -> Mapping[str, Any]:
        phase_order.append(f"role:{label}")
        return _invoke_child(
            kind="role", label=label, owner=request["role"],
            run_dir=destination / "roles" / label.replace(":", "-"),
            runner=role_runner, base_port=llm_input_port, progress=progress,
            interruption_requested=interruption_requested,
            request=request, selection=selection, digest_runner=digest_runner)

    try:
        progress.checkpoint()
        progress.stage = "protocol_write"
        (destination / "protocol.json").write_text(
            json.dumps(formal_protocol_mapping(protocol), indent=2,
                       ensure_ascii=False) + "\n", encoding="utf-8")
        progress.stage = "input_port_build"
        raise_if_stopped(interruption_requested)
        if llm_input_port is None:
            from cpn.llm_adapters import build_llm_input_port
            llm_input_port = build_llm_input_port(selection, destination_run_root=destination)
            created_port = True
        progress.stage = "campaign_execution"
        h0_sources = _sources(protocol)
        phase_order.append("calibration_h0")
        calibration = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                                evaluation_id="calibration-h0", candidate_id="H0",
                                source_files=h0_sources, split="calibration", repetitions=2,
                                policy_runner=policy_runner, base_port=llm_input_port,
                                child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                                phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
        calibration_scores = [row["reward"] for row in calibration["trials"]]
        calibration_result = calibrate_noise_band(
            calibration_scores,
            ((row["reward"], row["weight"])
             for row in calibration["trials"]),
        )
        delta = float(calibration_result["delta"])
        phase_order.append("h0_evolve")
        h0_evolve = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                              evaluation_id="evolve-h0", candidate_id="H0",
                              source_files=h0_sources, split="evolve", repetitions=1,
                              policy_runner=policy_runner, base_port=llm_input_port,
                              child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                              phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
        incumbent_id, incumbent_sources, incumbent_eval = "H0", h0_sources, h0_evolve
        score_star = float(h0_evolve["aggregate"]["score"])
        selection_parameters = SelectionParameters(
            **dict(protocol.method["selection"]))
        history = History()
        attribution: list[dict[str, Any]] = []
        manifests: list[dict[str, Any]] = []
        round_decisions: list[dict[str, Any]] = []

        progress.report.update({"candidate_manifests": manifests,
                                "round_decisions": round_decisions})
        for round_index in range(1, 3):
            round_id = f"round-{round_index}"
            phase_order.append(f"{round_id}:search")
            traces = _trace_input(incumbent_eval)

            def digest_runner(request: Mapping[str, Any], ordinal: int, *, _round=round_index) -> Mapping[str, Any]:
                del ordinal
                return invoke_role(
                    label=f"round-{_round}:digester:{request['occurrence_id']}",
                    request=request)

            analyst_request = _role_request(protocol, round_id=round_id,
                occurrence_id=f"{protocol.protocol_id}:{round_id}:analyst", parent_action_ref=f"{round_id}:analysis",
                role="analyst", input_value={"protocol": {"protocol_id": protocol.protocol_id},
                "evolve_manifest": role_input_manifest(protocol, "evolve"), "incumbent": incumbent_eval,
                "traces": traces, "prior_mode_names": [], "history": [asdict(item) for item in history.render_window()]})
            analyst = invoke_role(label=f"{round_id}:analyst", request=analyst_request, digest_runner=digest_runner)
            analysis = _role_payload(analyst)
            analyst_run = analyst.get("run") if isinstance(analyst, Mapping) else None
            analysis_ref = analyst_run.get("result_resource_ref") if isinstance(analyst_run, Mapping) else None
            candidate_id = f"candidate-r{round_index}-v0"
            source_request = {"candidate_id": incumbent_id, "files": _json(incumbent_sources),
                              "editable_paths": ["policy.py"]}
            proposer_request = _role_request(protocol, round_id=round_id,
                occurrence_id=f"{protocol.protocol_id}:{round_id}:proposer", parent_action_ref=f"{round_id}:proposal",
                role="proposer", input_value={"protocol": {"protocol_id": protocol.protocol_id},
                "current_source": source_request, "analysis": _json(analysis), "analysis_ref": _ref(analysis_ref),
                "evolve_role_manifest": role_input_manifest(protocol, "evolve"),
                "history": [asdict(item) for item in history.render_window()],
                "attribution": attribution[-20:], "history_bounds": {"recent": 40, "unmeasured": 4},
                "attribution_bound": 20, "constitution": (
                    "Only policy.py may change. Preserve exactly one "
                    "build_messages(raw) function with one return expression; "
                    "do not add imports, assignments, attributes, decorators, "
                    "or calls other than str(raw). Change only the Policy "
                    "messages needed to improve evolve behavior."),
                "method_state": {"delta": delta, "incumbent_id": incumbent_id}, "files": _json(incumbent_sources),
                "traces": traces, "editable_paths": ["policy.py"], "source_exts": [".py"],
                "creatable_paths": [], "create_mode": 0o644, "repair": False})
            proposer = invoke_role(label=f"{round_id}:proposer", request=proposer_request)
            proposal = _role_payload(proposer)
            if proposal.get("status") == "no_proposal" or not isinstance(proposal.get("files"), list):
                history = history.append(HistoryEntry(round_index, candidate_id, None, None, "no_proposal", False,
                                                       None, None, None, None))
                round_decisions.append({"round": round_index, "candidate_id": candidate_id,
                                        "outcome": "no_proposal", "selected_id": incumbent_id, "adopted": False})
                continue
            manifest = build_candidate_manifest(protocol=protocol, round_index=round_index,
                                                candidate_id=candidate_id, parent_candidate_id=incumbent_id,
                                                source_files=incumbent_sources, source_request=source_request,
                                                source_request_ref=(
                                                    proposer.get("run", {}).get("task_ref")
                                                    if isinstance(proposer.get("run"), Mapping)
                                                    else None),
                                                analysis_ref=analysis_ref, proposal_envelope=proposer)
            manifests.append(manifest)
            precheck = fixed_precheck(manifest)
            if not precheck["passed"]:
                history = history.append(HistoryEntry(round_index, candidate_id, proposal.get("component"),
                                                       proposal.get("summary"), "precheck_reject", False,
                                                       None, None, None, None))
                round_decisions.append({"round": round_index, "candidate_id": candidate_id,
                                        "outcome": "gate_reject", "precheck": precheck,
                                        "critic_calls": 0, "candidate_trials": 0,
                                        "selected_id": incumbent_id, "adopted": False})
                continue
            critic_request = _role_request(protocol, round_id=round_id,
                occurrence_id=f"{protocol.protocol_id}:{round_id}:critic", parent_action_ref=f"{round_id}:review",
                role="critic", input_value={"candidate_manifest": _json(manifest),
                "whole_diff": manifest["whole_diff"], "component": proposal.get("component", "policy")})
            critic = invoke_role(label=f"{round_id}:critic", request=critic_request)
            review = _role_payload(critic)
            if review.get("decision") != "accepted":
                history = history.append(HistoryEntry(round_index, candidate_id, proposal.get("component"),
                                                       proposal.get("summary"), "critic_reject", False,
                                                       None, None, None, None))
                round_decisions.append({"round": round_index, "candidate_id": candidate_id,
                                        "outcome": "critic_reject", "review": _json(review),
                                        "critic_calls": 1, "candidate_trials": 0,
                                        "selected_id": incumbent_id, "adopted": False})
                continue
            phase_order.append(f"{round_id}:smoke")
            progress.stage = f"{round_id}:smoke"
            smoke = _smoke(destination / "candidates" / candidate_id,
                           manifest, protocol)
            if not smoke["passed"]:
                history = history.append(HistoryEntry(round_index, candidate_id, proposal.get("component"),
                                                       proposal.get("summary"), "smoke_fail", False,
                                                       None, None, None, None))
                round_decisions.append({"round": round_index, "candidate_id": candidate_id,
                                        "outcome": "smoke_fail", "smoke": smoke,
                                        "critic_calls": 1, "candidate_trials": 0,
                                        "selected_id": incumbent_id, "adopted": False})
                continue
            candidate_sources = [{"path": item["path"], "content": item["after"], "mode": item["mode"],
                                  "purpose": item["purpose"]} for item in manifest["members"]]
            candidate_eval = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                                       evaluation_id=f"evolve-r{round_index}",
                                       candidate_id=candidate_id,
                                       source_files=candidate_sources, split="evolve",
                                       repetitions=1, policy_runner=policy_runner, base_port=llm_input_port,
                                       child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                                       phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
            candidate_aggregate = candidate_eval["aggregate"]
            incumbent_aggregate = incumbent_eval["aggregate"]
            candidate_score, incumbent_score = candidate_aggregate["score"], incumbent_aggregate["score"]
            incumbent = Incumbent(
                incumbent_id, incumbent_score, incumbent_aggregate["cost"],
                incumbent_aggregate["cost_comparable"])
            candidate = CandidateEvaluation(
                candidate_id, candidate_score, candidate_aggregate["cost"],
                candidate_aggregate["cost_comparable"], True, 0)
            cost_rule = cost_rule_passes(
                incumbent, candidate, delta=delta,
                parameters=selection_parameters)
            decision = select_candidate(
                incumbent, [candidate], delta=delta, score_star=score_star,
                parameters=selection_parameters)
            adopted = decision.adopted
            delta_score = round(candidate_score - incumbent_score, 6)
            history = history.append(HistoryEntry(round_index, candidate_id, proposal.get("component"),
                                                   proposal.get("summary"), "evaluated", adopted, delta_score,
                                                   relative_cost_change(
                                                       candidate_aggregate["cost"],
                                                       incumbent_aggregate["cost"]),
                                                   candidate_score,
                                                   candidate_aggregate["cost"]))
            attribution.append({"round": round_index, "candidate_id": candidate_id,
                                "component": proposal.get("component"), "predicted": proposal.get("summary"),
                                "delta_score": delta_score, "adopted": adopted})
            round_decisions.append({"round": round_index, "candidate_id": candidate_id, "outcome": "evaluated",
                                    "smoke": smoke, "review": _json(review), "evaluation": candidate_eval,
                                    "selection": asdict(decision), "cost_rule_passed": cost_rule,
                                    "domain_guard_passed": True, "variant_order": [candidate_id],
                                    "critic_calls": 1,
                                    "candidate_trials": len(candidate_eval["trials"]),
                                    "selected_id": decision.selected_id, "adopted": adopted})
            if adopted:
                incumbent_id, incumbent_sources, incumbent_eval = candidate_id, candidate_sources, candidate_eval
                score_star = max(score_star, float(decision.selected_score))

        phase_order.append("h0_heldout")
        h0_heldout = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                               evaluation_id="heldout-h0", candidate_id="H0",
                               source_files=h0_sources, split="heldout", repetitions=1,
                               policy_runner=policy_runner, base_port=llm_input_port,
                               child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                               phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
        phase_order.append("hfinal_heldout")
        hfinal_heldout = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                                   evaluation_id="heldout-final",
                                   candidate_id=incumbent_id,
                                   source_files=incumbent_sources, split="heldout", repetitions=1,
                                   policy_runner=policy_runner, base_port=llm_input_port,
                                   child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                                   phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
        phase_order.append("export")
        export = _evaluate(run_dir=destination, protocol=protocol, selection=selection,
                           evaluation_id="export-final", candidate_id=incumbent_id,
                           source_files=incumbent_sources, split="export", repetitions=1,
                           policy_runner=policy_runner, base_port=llm_input_port,
                           child_evidence=child_evidence, attempt_inventory=attempt_inventory,
                           phase_order=phase_order, progress=progress,
                              interruption_requested=interruption_requested)
        round_completion = []
        for round_index in range(1, protocol.execution["round_count"] + 1):
            prefix = f"round-{round_index}:"
            role_rows = [row for row in child_evidence
                         if row["kind"] == "role"
                         and row["label"].startswith(prefix)]
            labels = {row["label"] for row in role_rows
                      if row.get("termination") == "submitted"}
            decision = next((row for row in round_decisions
                             if row["round"] == round_index), None)
            checks = {
                "analyst_submitted": f"{prefix}analyst" in labels,
                "digester_submitted": any(
                    label.startswith(f"{prefix}digester:")
                    for label in labels),
                "proposer_submitted": f"{prefix}proposer" in labels,
                "critic_submitted": f"{prefix}critic" in labels,
                "candidate_evaluated": (
                    isinstance(decision, Mapping)
                    and decision.get("outcome") == "evaluated"
                    and decision.get("candidate_trials")
                    == len(protocol.evolve.tasks)),
            }
            round_completion.append({
                "round": round_index, "checks": checks,
                "complete": all(checks.values()),
            })
        expected_policy_children = (
            len(protocol.calibration.tasks)
            * protocol.method["calibration_repetitions"]
            + len(protocol.evolve.tasks)
            * protocol.method["repetitions"]
            * (1 + protocol.execution["round_count"])
            + len(protocol.heldout.tasks) * protocol.method["repetitions"] * 2
            + len(protocol.export.tasks) * protocol.method["repetitions"])
        actual_policy_children = sum(
            row["kind"] == "policy" for row in child_evidence)
        completion = {
            "rounds": round_completion,
            "expected_policy_children": expected_policy_children,
            "actual_policy_children": actual_policy_children,
            "policy_plan_complete": (
                actual_policy_children == expected_policy_children),
        }
        structural_campaign_complete = (
            len(round_decisions) == protocol.execution["round_count"]
            and all(row["complete"] for row in round_completion)
            and completion["policy_plan_complete"])
        evidence_complete = (
            production_runners
            and _production_evidence_complete(child_evidence))
        campaign_complete = structural_campaign_complete and evidence_complete
        bootstrap_exercised = (
            calibration_result["method"] == "bootstrap over pooled trials")
        report = {"schema_version": "rrsi_v06/formal_campaign_report/v1", "scope": protocol.scope,
                  "application_conformance": {"profile": protocol.conformance_profile,
                                                "strict_agent_loop_conformance": False},
                  "source_basis_caveat": _json(protocol.source_basis),
                  "protocol": {"ref": protocol.protocol_id, "path": "protocol.json"},
                  "execution_selection": {
                      "model_condition": selection.input_target.model_condition,
                      "adapter_kind": selection.adapter_kind,
                  },
                  "local_selection_assumption": (
                      "The public RRSI Algorithm 2 formula is used with the explicit local fixture parameters in "
                      "protocol.method.selection. The timeout fixture and its fixed gate/smoke/scope guard are local, "
                      "not an official paper domain."),
                  "repair_rounds": 0,
                  "calibration": {"h0": calibration, **calibration_result,
                                  "bootstrap_fallback": {
                                      "available": True,
                                      "exercised": bootstrap_exercised,
                                      "reason": (
                                          "public_seeded_bootstrap_used_for_degenerate_repeats"
                                          if bootstrap_exercised
                                          else "repeated_base_evaluations_selected")}},
                  "h0_evolve": h0_evolve, "hfinal_evolve": incumbent_eval,
                  "h0_heldout": h0_heldout, "hfinal_heldout": hfinal_heldout, "export": export,
                  "candidate_manifests": manifests, "round_decisions": round_decisions,
                  "child_runs": child_evidence, "attempt_inventory": attempt_inventory,
                  "usage": {"roles": usage_summary([item for item in attempt_inventory if item["owner"] != "Policy"]),
                            "policy": usage_summary([item for item in attempt_inventory if item["owner"] == "Policy"])},
                  "phase_order": phase_order, "history": [asdict(item) for item in history.entries],
                  "attribution_tail": attribution[-20:],
                  "completion_evidence": completion,
                  "execution_evidence": {
                      "mode": ("registry_petri_provider"
                               if production_runners else "injected_test_runners"),
                      "structural_campaign_complete": structural_campaign_complete,
                      "production_evidence_complete": evidence_complete,
                  },
                  "campaign_complete": campaign_complete,
                  "formal_rrsi_v06_local_complete": campaign_complete}
        report.update({"report_status": "completed",
                       "termination": {"status": "completed", "stage": "campaign_end"},
                       "completed_evaluations": progress.report["completed_evaluations"]})
        progress.report = report
        if created_port:
            # Close before publishing completion; never retry a failing close.
            created_port = False
            progress.stage = "input_port_close"
            close = getattr(llm_input_port, "close", None)
            if callable(close):
                close()
        progress.stage = "report_write"
        progress.checkpoint()
        return report
    except BaseException as exc:
        primary_error = exc
        progress.failed(exc)
        raise
    finally:
        if created_port and llm_input_port is not None:
            close = getattr(llm_input_port, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as secondary:
                    if primary_error is None:
                        progress.stage = "input_port_close"
                        progress.failed(secondary)
                        raise
                    add_secondary_failure(primary_error, secondary, stage="input_port_close")


__all__ = ("FormalCampaignError", "build_candidate_manifest", "fixed_precheck", "run_formal_campaign")
