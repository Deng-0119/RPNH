"""Validate an explicit local experiment configuration without calling a model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .driver_contract import live_limits_from_config
from .jsonio import read
from .office_cases import CASE_PATHS
from .comparison_condition import selected_condition, COMPARISON_TASKS


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _resolve(value: Any, *, base: Path, label: str) -> Path:
    path = Path(_string(value, label)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _argument(argv: Any, flag: str) -> str | None:
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        return None
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    return argv[index + 1] if index + 1 < len(argv) else None


def load_experiment(config_path: Path) -> dict:
    config_path = Path(config_path).resolve()
    config = _object(read(config_path), "experiment configuration")
    if config.get("schema_version") != "rpnh-ha/local-experiment/v3":
        raise ValueError("unsupported experiment configuration schema")
    if selected_condition(config) and config.get("task_id") not in COMPARISON_TASKS:
        raise ValueError("comparison supports only the five public policy-dependent campaign bundles")
    return config


def inspect_live_readiness(config_path: Path) -> dict:
    """Report execution and scoring readiness as separate gates."""
    config_path = Path(config_path).resolve()
    config = load_experiment(config_path)
    base = config_path.parent
    checks: list[dict] = []

    def check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})

    check("experiment-authorized", config.get("authorized") is True)
    check("task-in-office-campaign", config.get("task_id") in CASE_PATHS)
    cost = _object(config.get("cost_policy") or {"mode": "no_cap", "owner_approved_cost_cap": None}, "cost_policy")
    check(
        "cost-policy-explicitly-no-cap",
        cost.get("mode") == "no_cap"
        and cost.get("owner_approved_cost_cap") is None,
    )
    limits = live_limits_from_config(config)
    check(
        "run-limits-parse",
        True,
        max_model_calls=limits.max_model_calls,
        max_tool_calls=limits.max_tool_calls,
        max_seconds=limits.max_seconds,
    )

    # Driver integration is an already completed phase. Missing historical
    # report paths do not trigger another acceptance campaign for every task.
    if selected_condition(config):
        check("comparison-runtime-acceptance", False, per_task_gate=False,
              status="not_performed", readiness_scope="profile declarations only; not runtime acceptance")
    else:
        check("driver-acceptance-reused", True, per_task_gate=False)

    execution = _object(config.get("execution"), "execution")
    expected_model = _string(execution.get("exact_model"), "execution.exact_model")
    expected_effort = _string(
        execution.get("reasoning_effort"), "execution.reasoning_effort")
    profile_path = _resolve(
        execution.get("profile_path"), base=base,
        label="execution.profile_path")
    profile: dict = {}
    adapter: dict = {}
    adapter_path: Path | None = None
    try:
        profile = _object(read(profile_path), "execution profile")
        raw_adapter = Path(_string(
            profile.get("adapter_config_path"),
            "execution profile adapter_config_path"))
        adapter_path = (
            raw_adapter if raw_adapter.is_absolute()
            else profile_path.parent / raw_adapter).resolve()
        adapter = _object(read(adapter_path), "execution adapter")
    except (OSError, TypeError, ValueError) as exc:
        check("executor-profile-loads", False, path=str(profile_path), error=str(exc))
    else:
        check("executor-profile-loads", True, path=str(profile_path))
        check(
            "executor-route-is-local-process",
            profile.get("adapter_kind") == "local_process"
            and adapter.get("adapter_kind") == "local_process"
            and isinstance(adapter.get("env"), dict)
            and isinstance(adapter.get("inherit_env"), list),
            adapter_path=str(adapter_path),
        )
        check(
            "executor-model-matches",
            profile.get("model_condition") == expected_model
            and adapter.get("model_condition") == expected_model
            and _argument(adapter.get("argv"), "--model") == expected_model,
            expected=expected_model,
        )
        check(
            "executor-effort-matches",
            _argument(adapter.get("argv"), "--reasoning-effort")
            == expected_effort,
            expected=expected_effort,
        )

    execution_check_names = {
        "experiment-authorized", "task-in-office-campaign",
        "executor-profile-loads", "executor-route-is-local-process",
        "executor-model-matches", "executor-effort-matches",
        "run-limits-parse",
    }
    check("executor-authorized", execution.get("authorized") is True)
    execution_ready = all(
        item["passed"] for item in checks
        if item["name"] in execution_check_names | {"executor-authorized"}
    )

    scoring = _object(config.get("scoring"), "scoring")
    judge_model = _string(scoring.get("exact_model"), "scoring.exact_model")
    judge_effort = _string(
        scoring.get("reasoning_effort"), "scoring.reasoning_effort")
    judge_max_output_tokens = scoring.get("max_output_tokens")
    if (isinstance(judge_max_output_tokens, bool)
            or not isinstance(judge_max_output_tokens, int)
            or judge_max_output_tokens < 1):
        raise ValueError("scoring.max_output_tokens must be a positive integer")
    check("judge-authorized", scoring.get("authorized") is True)
    check("judge-model-configured", bool(judge_model), model=judge_model)
    check("judge-effort-configured", bool(judge_effort), effort=judge_effort)
    judge_adapter_path = _resolve(
        scoring.get("adapter_path"), base=base,
        label="scoring.adapter_path")
    try:
        judge_adapter = _object(read(judge_adapter_path), "judge adapter")
    except (OSError, TypeError, ValueError) as exc:
        check("judge-adapter-loads", False, path=str(judge_adapter_path), error=str(exc))
    else:
        check("judge-adapter-loads", True, path=str(judge_adapter_path))
        check(
            "judge-route-is-local-process",
            judge_adapter.get("adapter_kind") == "local_process"
            and isinstance(judge_adapter.get("env"), dict)
            and isinstance(judge_adapter.get("inherit_env"), list),
        )
        check(
            "judge-model-matches",
            judge_adapter.get("model_condition") == judge_model
            and _argument(judge_adapter.get("argv"), "--model") == judge_model,
            expected=judge_model,
        )
        check(
            "judge-effort-matches",
            _argument(judge_adapter.get("argv"), "--reasoning-effort")
            == judge_effort,
            expected=judge_effort,
        )
        check(
            "judge-output-limit-matches",
            _argument(judge_adapter.get("argv"), "--model-max-output-tokens")
            == str(judge_max_output_tokens),
            expected=judge_max_output_tokens,
        )
    check(
        "judge-transport-compatible",
        scoring.get("transport_status") == "ready",
        transport_status=scoring.get("transport_status"),
    )
    scoring_ready = all(
        item["passed"] for item in checks
        if item["name"] in {
            "judge-authorized", "judge-model-configured",
            "judge-effort-configured", "judge-adapter-loads",
            "judge-route-is-local-process", "judge-model-matches",
            "judge-effort-matches", "judge-output-limit-matches",
            "judge-transport-compatible",
        }
    )
    execution_blocking = [
        item["name"] for item in checks
        if item["name"] in execution_check_names | {"executor-authorized"}
        and not item["passed"]
    ]
    scoring_blocking = [
        item["name"] for item in checks
        if item["name"].startswith("judge-") and not item["passed"]
    ]
    return {
        "schema_version": "rpnh-ha/live-readiness/v2",
        "config_path": str(config_path),
        "execution_ready": execution_ready,
        "scoring_ready": scoring_ready,
        "full_score_ready": execution_ready and scoring_ready,
        "execution_blocking": execution_blocking,
        "scoring_blocking": scoring_blocking,
        "cost_gate_required": False,
        "checks": checks,
        "actual_model_calls": 0,
        **({"configuration_condition": selected_condition(config),
            "condition_id": selected_condition(config),
            "execution_readiness_scope": "profile declarations only; comparison runtime acceptance not performed"}
           if selected_condition(config) else {}),
    }


__all__ = ("inspect_live_readiness", "load_experiment")
