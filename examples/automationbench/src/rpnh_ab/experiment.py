"""Frozen plans, sequential whole-batch execution and non-reexecuting continuation."""
from __future__ import annotations
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit
from .broker import Broker
from .configuration_condition import BASELINE, configure, freeze_plan, restore_frozen, selected
from .cohort import plan_for_cohort, resolve_frozen_cases
from .constants import CONDITION_ID, PUBLIC_DOMAINS, RPNH_REVIEWED_COMMIT, UPSTREAM_COMMIT
from .io import append, file_sha, load, now, replace_checkpoint, sha, write_new
from .native import build_spec, profile_identity, project_registry, run
from .scoring import score_attempt, summarize
from .upstream import git_identity, public_messages


class BatchOwnerActive(RuntimeError):
    """Another process owns the nonblocking batch lock."""


@contextmanager
def batch_owner_lock(work: Path):
    """Serialize the live owner and state-mutating offline maintenance."""
    with (work / ".batch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchOwnerActive("another process owns this batch") from exc
        yield


def helper_environment() -> dict:
    """Record, never manufacture, the native ChatGPT business-tool backend."""
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    return {"implementation": "upstream _call_openai unchanged",
            "openai_api_key_configured": configured,
            "openai_base_url_host": urlsplit(os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").hostname,
            "mode": "native_real_api_configured" if configured else "native_no_key_simulated_response",
            "model": "selected by business-tool arguments or upstream defaults; not executor identity",
            "transport_verified": False}


def installed_rpnh_identity() -> dict:
    import cpn.rpnh.agent_tasks as module
    root = Path(module.__file__).resolve().parents[2]
    identity = git_identity(root)
    identity["reviewed_commit"] = RPNH_REVIEWED_COMMIT
    identity["same_as_reviewed_commit"] = identity["commit"] == RPNH_REVIEWED_COMMIT
    identity["installed_module"] = str(Path(module.__file__).resolve())
    paths = [*sorted((root/'cpn').rglob('*.py')), *sorted((root/'cpn').rglob('*.json')),
             *sorted((root/'integrations/dsh').rglob('*.ts')), root/'integrations/dsh/UPSTREAM.json']
    identity['runtime_source_sha256'] = sha({str(p.relative_to(root)):file_sha(p) for p in paths if p.is_file()})
    return identity


def environment_identity() -> dict:
    packages = {}
    for name in ("automation-bench", "rpnh-automationbench", "verifiers", "datasets", "pydantic", "jsonschema"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    sources = {p.name: file_sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    return {"python": sys.version, "platform": platform.platform(), "packages": packages,
            "adapter_sources": sources}


def initial_evidence_sources(upstream) -> dict:
    """Record source identities without copying paths, credentials or task data."""
    identity = getattr(upstream, "identity", {})
    root = Path(__file__).parent
    sources = {path.relative_to(root).as_posix(): file_sha(path)
               for path in sorted(root.rglob("*.py"))}
    return {"upstream_pinned_commit": UPSTREAM_COMMIT,
            "upstream_commit": identity.get("commit"),
            "upstream_tracked_clean": (not identity["tracked_changes"]
                                       if "tracked_changes" in identity else None),
            "adapter_sources_sha256": sha(sources)}


def preserve_initial_world(upstream, state: dict, attempt: Path) -> dict:
    """Freeze the materialized world before the host or dispatch lane starts.

    This is private evidence, not a replacement scoring input or actor input.
    In particular, record upstream's clock as materialized; never set it here.
    """
    path = attempt / "world.initial.materialized.json"
    write_new(path, upstream.dump_world(state))
    # Read the retained bytes, not a possibly aliased live world dictionary.
    frozen = load(path)
    record = load(attempt / "attempt.json")
    meta = frozen.get("meta", {})
    write_new(attempt / "initial_world_provenance.json", {
        "schema": "rpnh-ab/initial-world/v1", "at": now(),
        "capture_stage": "before_host_start_and_tool_dispatch",
        "clock_policy": "record_upstream_materialized_value_unchanged",
        "task_id": record["id"], "attempt": record["attempt"],
        "attempt_file_sha256": file_sha(attempt / "attempt.json"),
        "task_contract_sha256": record["task_contract_sha256"],
        "task_contract_file_sha256": record["task_contract_file_sha256"],
        "sources": record["initial_evidence_sources"],
        "world_file": path.name, "world_sha256": file_sha(path),
        "world_byte_size": path.stat().st_size,
        "current_time": meta.get("current_time"),
        "allowed_services": meta.get("allowed_services"),
    })
    return frozen


def plan_for(cases: list[dict], split: str) -> dict:
    expected = 600 if split == "public" else 200
    if len(cases) != expected:
        raise ValueError("the experiment must preserve the full selected split")
    tasks = [{k: v for k, v in case.items() if k != "row"} for case in cases]
    if split == "public" and any(sum(t["domain"] == d for t in tasks) != 100 for d in PUBLIC_DOMAINS):
        raise ValueError("public split must contain all six 100-task domains")
    return {"schema": "rpnh-ab/plan/v1", "condition_id": CONDITION_ID,
            "split": split, "upstream_commit": UPSTREAM_COMMIT, "repetitions": 1,
            "selection": "all-in-native-domain-order", "tasks": tasks,
            "manifest_sha256": sha(tasks)}


def prepare(upstream, work: Path, profile: Path, split: str | None = None, *, cohort: Path | None = None,
            executor_host="native", host_identity=None, configuration_condition=BASELINE) -> dict:
    if configuration_condition != BASELINE and executor_host != "native":
        raise ValueError("API comparison condition currently supports only the native host")
    condition = configure(upstream, configuration_condition)
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    # Linux privacy defaults; the model never receives this directory as a tool.
    os.chmod(work, 0o700)
    if len(str(work / "attempts/sales-0001/a0001/native/owner.sock").encode()) > 100:
        raise ValueError("choose a shorter work path, e.g. ~/ab1")
    if cohort is not None:
        if split is not None:
            raise ValueError("--cohort and --split have mutually exclusive meanings")
        plan = plan_for_cohort(upstream, cohort)
        split = plan["split"]
    else:
        split = split or "public"
        cases = upstream.cases(split)
        plan = plan_for(cases, split)
    plan = freeze_plan(plan, condition)
    configured = profile_identity(profile)
    conditions = {"schema": "rpnh-ab/condition/v1", "condition_id": plan["condition_id"],
          "split": split, "upstream": upstream.identity, "rpnh": installed_rpnh_identity(),
          "environment": environment_identity(), "configured_model": configured,
          "business_tool_helper": helper_environment(),
          "tools": {"toolset": "api", "schemas_sha256": sha(upstream.schemas)},
          "agent": "one native actor node; no fixed Office team; no independent model loop",
          "limits": {"cumulative_model_calls": None, "cumulative_tool_calls": None,
                     "max_attempts_per_stage": None, "whole_task_seconds": None, "amount_ceiling": None,
                     "parallel_tasks": 1, "parallel_nodes": 1},
          "upstream_defaults_not_applied_to_native_loop": {"env_max_turns": 25, "cli_max_steps": 50},
          "comparison": "descriptive; no claim of identical upstream executor conditions",
          "fallback_policy": "no adapter-created model fallback; archive registered routes; opaque provider routing may be unknown"}
    from .run_spec import benchmark_spec, execution_spec
    conditions["benchmark_spec"] = benchmark_spec(plan, upstream.schemas, business_mode="native_real_api")
    if condition is not None:
        conditions["configuration_condition"] = condition
    conditions["execution_spec"] = execution_spec(profile, executor_host, conditions["rpnh"], host_identity=host_identity)
    conditions["executor_host"] = executor_host
    conditions["agent"] = "one frozen actor; host-owned execution; shared world and upstream rubric"
    for name, value in (("plan.json", plan), ("conditions.json", conditions), ("tool_schemas.json", upstream.schemas)):
        path = work / name
        if path.exists():
            if load(path) != value:
                raise ValueError(f"{name} changed; retain this batch, use a new work root for a new condition")
        else:
            write_new(path, value)
    return plan


def doctor(upstream, profile: Path, work: Path, *, split: str | None = None,
           cohort: Path | None = None, configuration_condition=BASELINE) -> dict:
    """No paid calls: validate the selected condition and its upstream split."""
    condition = configure(upstream, configuration_condition)
    from jsonschema import Draft7Validator
    from automationbench.rubric.registry import AssertionRegistry, STRICT_MODE
    import automationbench.rubric.assertions  # registration only
    if not STRICT_MODE:
        raise ValueError("AUTOMATIONBENCH_STRICT_ASSERTIONS must not be disabled")
    profile = profile.resolve()
    requested = profile_identity(profile)
    if cohort is not None:
        if split is not None:
            raise ValueError("--cohort and --split have mutually exclusive meanings")
        selected_plan = plan_for_cohort(upstream, cohort)
        selected_cases = resolve_frozen_cases(upstream, selected_plan)
    else:
        split = split or "public"
        selected_cases = upstream.cases(split)
        selected_plan = plan_for(selected_cases, split)
    selected_plan = freeze_plan(selected_plan, condition)
    cases = upstream.cases(selected_plan["split"])
    selected_ids = {case["id"] for case in selected_cases}
    domain_counts = {}
    assertion_types = set()
    helper_capable_cases = []
    for schema in upstream.schemas:
        Draft7Validator.check_schema(schema["function"]["parameters"])
    for case in cases:
        state = upstream.start(case["row"])
        for assertion in state["info"].get("assertions", []):
            atype = assertion["type"]
            if atype not in AssertionRegistry._handlers:
                raise ValueError(f"{case['id']}: unknown assertion handler {atype}")
            assertion_types.add(atype)
        if not isinstance(state["world"].meta.allowed_services, list):
            raise ValueError("upstream service scoping not initialized")
        if (case["id"] in selected_ids
                and "chatgpt" in state["world"].meta.allowed_services):
            helper_capable_cases.append(case["id"])
        domain_counts[case["domain"]] = domain_counts.get(case["domain"], 0) + 1
    spec = build_spec(work / "doctor-native-never-started", profile, "/tmp/not-started.sock",
                      "declaration-check", public_messages(cases[0]["row"]), upstream.schemas,
                      configuration_condition=condition)
    if spec.max_attempts_per_stage is not None:
        raise ValueError("unexpected cumulative model call allowance")
    report = {"schema": "rpnh-ab/doctor/v1", "at": now(), "status": "passed",
            "worlds_initialized": len(cases), "domain_counts": domain_counts,
            "selected_tasks": len(selected_cases),
            "selected_split": selected_plan["split"],
            "selected_manifest_sha256": selected_plan["manifest_sha256"],
            "assertion_handler_types": len(assertion_types), "tools": 3,
            "configured_model": requested, "native_spec_constructed": True,
            "business_tool_helper": helper_environment(),
            "helper_capable_case_ids": helper_capable_cases,
            "ready_for_live_batch": not helper_capable_cases or helper_environment()["openai_api_key_configured"],
            "configuration_blocker": ("native ChatGPT business-tool backend has no API key; do not silently substitute placeholder text"
                                      if helper_capable_cases and not helper_environment()["openai_api_key_configured"] else None),
            "model_calls": 0, "native_worker_started": False,
            "limitations": ["not a live RPNH worker acceptance", "not a benchmark result", "not an all-tool semantic audit"]}

    if condition is not None:
        report["configuration_condition"] = condition
    return report


def one_attempt(upstream, case: dict, attempt: Path, profile: Path, *, executor_host="native", native_run_dir=None, control_root=None, dsh_checkout=None, stop_path=None) -> bool:
    """Return True on manual stop. Errors become retained attempts, never reruns."""
    if selected(upstream) is not None and executor_host != "native":
        raise ValueError("API comparison condition currently supports only the native host")
    attempt.mkdir(parents=True, exist_ok=False)
    # Freeze the private task payload before creating the attempt identity so a
    # later score revision cannot silently re-baseline changed rubric inputs.
    write_new(attempt / "task_contract.json", case["row"])
    write_new(attempt / "attempt.json", {"schema": "rpnh-ab/attempt/v1", "at": now(),
        "id": case["id"], "task_name": case["task_name"], "domain": case["domain"],
        "attempt": "a0001", "execution_mode": executor_host + "_live",
        "task_contract_sha256": case["task_contract_sha256"],
        "task_contract_file_sha256": file_sha(attempt / "task_contract.json"),
        "initial_evidence_sources": initial_evidence_sources(upstream),
        **({"configuration_condition": selected(upstream)} if selected(upstream) is not None else {})})
    write_new(attempt / "public_task.json", {"prompt": public_messages(case["row"])})
    state, lifecycle, broker, driver = None, {}, None, None
    manual = False
    admitted = False
    host_run_dir = native_run_dir or (attempt / executor_host)
    upstream.normalization_log = attempt / "normalization_events.jsonl"
    from .drivers import get_driver
    try:
        driver = get_driver(executor_host)
        state = upstream.start(case["row"])
        initial_world = preserve_initial_world(upstream, state, attempt)
        write_new(attempt / "scoring_input.json", {"initial_state": state["initial_state"],
                                                   "info": state["info"]})
        replace_checkpoint(attempt / "world.latest.json", initial_world)
        with Broker(upstream, state, attempt, uuid.uuid4().hex) as broker:
            lifecycle = driver.run(run_dir=host_run_dir, profile=profile, broker=broker,
                         messages=public_messages(case["row"]), schemas=upstream.schemas,
                         control_root=control_root or (attempt / "control"),
                         dsh_checkout=dsh_checkout, stop_path=stop_path)
            admitted = lifecycle.get("admitted", False)
            manual = lifecycle["manual_stop"]
            lifecycle["execution_status"] = "manual_stop" if manual else (
                "host_terminal" if lifecycle.get("terminal") else "host_nonterminal")
            if ((not admitted and not manual)
                    or not lifecycle.get("host_quiescent", False)):
                lifecycle["execution_status"] = "configuration_blocked"
                lifecycle["blocker"] = "host admission or quiescence not established"
    except KeyboardInterrupt:
        manual = True
        lifecycle.update(execution_status="manual_stop_outside_worker", error="KeyboardInterrupt")
    except Exception as exc:
        lifecycle.update(execution_status="configuration_blocked" if not admitted else "execution_error", error=type(exc).__name__ + ": " + str(exc))
    # A closed broker has drained the one world-mutating dispatch lane. This is
    # independent from claims about native terminal success or model consumption.
    if (admitted and lifecycle.get("execution_status") == "host_terminal"
            and lifecycle.get("host_quiescent", False) and state is not None
            and broker is not None and broker.closed):
        lifecycle["world_owner_quiescent"] = True
        write_new(attempt / "final_world.json", upstream.dump_world(state))
    else:
        lifecycle["world_owner_quiescent"] = False
    lifecycle["admitted"] = admitted
    lifecycle["executor_host"] = executor_host
    lifecycle["at"] = now()
    write_new(attempt / "lifecycle.json", lifecycle)
    if driver is not None and host_run_dir.exists():
        try:
            facts = driver.project(host_run_dir, attempt)
        except Exception as exc:
            facts = {"status": "projection_error", "error": type(exc).__name__ + ": " + str(exc),
                     "raw_registry_retained": True}
        write_new(attempt / (executor_host + "_evidence.json"), facts)
    from .evidence import crosscheck
    write_new(attempt / "bridge_registry_check.json", crosscheck(attempt))
    try:
        if (attempt / "final_world.json").is_file():
            score_attempt(attempt, upstream.score, task=case)
        return manual or lifecycle["execution_status"] == "configuration_blocked" or not lifecycle["world_owner_quiescent"]
    finally:
        upstream.normalization_log = None


def run_batch(upstream, work: Path, profile: Path, *, launch=None, progress=None) -> dict:
    plan = load(work / "plan.json")
    conditions = load(work / "conditions.json")
    condition = restore_frozen(upstream, conditions)
    if plan.get("configuration_condition") != condition:
        raise ValueError("plan configuration condition differs from frozen conditions")
    if launch is None:
        raise ValueError("run requires a validated prepared launch request with host-bound acceptance")
    from .run_spec import load_launch
    launch = load_launch(Path(launch)) if isinstance(launch, (str, Path)) else launch
    from .run_spec import validate_acceptance
    validate_acceptance(launch['acceptance'],conditions['benchmark_spec'],conditions['execution_spec'])
    host=conditions['execution_spec']['executor_host']
    stop_path=work/'stop.request'
    if plan.get("schema") == "rpnh-ab/plan/v1":
        cases = upstream.cases(plan["split"])
        if freeze_plan(plan_for(cases, plan["split"]), condition) != plan:
            raise ValueError("resolved task plan differs from the frozen plan")
    elif plan.get("schema") == "rpnh-ab/cohort-plan/v1":
        cases = resolve_frozen_cases(upstream, plan)
    else:
        raise ValueError("unsupported frozen plan schema")
    benchmark = conditions.get("benchmark_spec", {})
    if (benchmark.get("split") != plan.get("split")
            or benchmark.get("manifest_sha256") != plan.get("manifest_sha256")
            or benchmark.get("task_contracts") != [task["task_contract_sha256"] for task in plan["tasks"]]):
        raise ValueError("frozen plan differs from the accepted benchmark condition")
    if environment_identity() != conditions["environment"]:
        raise ValueError("installed adapter/dependencies changed since prepare; preserve this condition")
    if installed_rpnh_identity() != conditions["rpnh"]:
        raise ValueError("RPNH changed since prepare; never mix versions silently")
    if not (work / "doctor.json").is_file() or load(work / "doctor.json").get("status") != "passed":
        raise ValueError("run the no-model doctor check before the live batch")
    doctor_record = load(work / "doctor.json")
    if (doctor_record.get("selected_split") != plan["split"]
            or doctor_record.get("selected_manifest_sha256") != plan["manifest_sha256"]):
        raise ValueError("doctor check belongs to another task selection")
    if doctor_record.get("configuration_condition") != condition:
        raise ValueError("doctor check belongs to another configuration condition")
    if not doctor_record.get("ready_for_live_batch", False):
        raise ValueError(str(doctor_record.get("configuration_blocker", "real tool backend readiness not recorded")))
    if helper_environment() != conditions.get("business_tool_helper"):
        raise ValueError("business-tool helper environment differs from frozen conditions")
    finished = sum(
        (work / "attempts" / task["id"] / "a0001" / "lifecycle.json").is_file()
        for task in plan["tasks"]
    )

    def batch_status(status: str, *, terminal: bool, quiescent: bool, summary=None) -> None:
        value = {"at": now(), "status": status, "terminal": terminal,
                 "quiescent": quiescent, "progress": {
                     "finished": finished, "planned": len(plan["tasks"])},
                 "stop_requested": stop_path.exists()}
        if summary is not None:
            value["summary"] = summary
        replace_checkpoint(work / "batch-status.json", value)

    # A local interprocess batch lock prevents duplicate attempts. It is not a
    # model/tool/task resource allowance.
    with batch_owner_lock(work):
        try:
            batch_status("running", terminal=False, quiescent=False)
            for case in cases:
                if helper_environment() != conditions.get("business_tool_helper"):
                    raise ValueError("business-tool helper routing changed within the batch")
                if installed_rpnh_identity() != conditions["rpnh"]:
                    raise ValueError("RPNH changed during the batch; preserve remaining tasks without mixed runtime versions")
                if profile_identity(profile) != conditions["configured_model"]:
                    raise ValueError("configured route/effort changed within the batch")
                attempt = work / "attempts" / case["id"] / "a0001"
                if stop_path.exists():
                    break
                if attempt.exists():
                    lifecycle_path=attempt/'lifecycle.json'
                    if not lifecycle_path.is_file() or load(lifecycle_path).get('execution_status')=='configuration_blocked':
                        raise RuntimeError('retained attempt is blocked/uncertain; automatic replay is forbidden')
                    continue
                run_root=Path(launch['native_run_root']) if launch.get('native_run_root') else work/'host-runs'
                stopped = one_attempt(upstream, case, attempt, profile, executor_host=host,
                         native_run_dir=run_root/case['id'], control_root=work/'task-control',
                         dsh_checkout=launch.get('dsh_checkout'), stop_path=stop_path)
                finished += 1
                batch_status("running", terminal=False, quiescent=False)
                if progress is not None:
                    progress(case, attempt)
                replace_checkpoint(work / "summary.live.json", summarize(plan, work))
                print(json.dumps({"finished": case["id"], "manual_stop": stopped}), flush=True)
                if stopped:
                    break
            summary = summarize(plan, work)
            replace_checkpoint(work / "summary.live.json", summary)
            lifecycles = [load(path) for path in work.glob("attempts/*/a0001/lifecycle.json")]
            blocked = any(row.get("execution_status") not in {"host_terminal", "manual_stop"}
                          for row in lifecycles)
            quiescent = all(row.get("host_quiescent") is True for row in lifecycles)
            status = "configuration_blocked" if blocked else "stopped" if stop_path.exists() else "completed"
            batch_status(status, terminal=True, quiescent=quiescent, summary=summary)
            return summary
        except KeyboardInterrupt:
            batch_status("stopped", terminal=True, quiescent=False)
            raise
        except Exception:
            batch_status("configuration_blocked", terminal=True, quiescent=False)
            raise
