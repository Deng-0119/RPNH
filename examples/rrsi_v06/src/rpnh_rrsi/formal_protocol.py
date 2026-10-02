"""Closed, local-only protocol for the RRSI v0.6 application example.

The application fixture and method constants are frozen, while model and
provider routing remain in the user's RPNH execution profile.  This is not an
official benchmark reproduction and contains no content-derived identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping


SCOPE = "formal_rrsi_v06_b0_local/v1"
CONFORMANCE_PROFILE = "application_petri_conformance/v1"
RECOVERY = "B0"
OFFICIAL_RRSI_REVISION = "be50316e1db05914068a973f322770ef08ed7ba1"
PROTOCOL_ID = "rrsi-v06-b0-example"
SCORER = "formal_rrsi_v06_exact_timeout_json/v1"
MANIFEST_IDS = {
    "calibration": "formal-calibration-v1",
    "evolve": "formal-evolve-v1",
    "heldout": "formal-heldout-v1",
    "export": "formal-export-v1",
}
BASELINE_POLICY_SOURCE = (
    "def build_messages(raw):\n"
    "    return [{\n"
    "        'role': 'system',\n"
    "        'content': ('You are the Policy model. Emit only JSON "
    "{\\\"timeout\\\": integer}. '\n"
    "                    'Interpret raw input as an integer timeout, including "
    "zero-padded integers; '\n"
    "                    'missing or null input must default 30. Blank input is "
    "unsupported and must '\n"
    "                    'emit {\\\"timeout\\\": 0}.'),\n"
    "    }, {\n"
    "        'role': 'user',\n"
    "        'content': str(raw),\n"
    "    }]\n"
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_FORBIDDEN_IDENTITY_FIELDS = {"hash", "checksum", "fingerprint", "digest"}


class FormalProtocolError(ValueError):
    """The formal local protocol is not the exact frozen B0 protocol."""


def score_timeout_response(response_text: str, expected: int) -> bool:
    """Apply the fixed formal scorer without provider or harness access."""
    if not isinstance(response_text, str) or isinstance(expected, bool) or not isinstance(expected, int):
        return False

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(response_text, object_pairs_hook=no_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return (isinstance(value, dict) and set(value) == {"timeout"}
            and isinstance(value["timeout"], int) and not isinstance(value["timeout"], bool)
            and value["timeout"] == expected)


def _closed(value: Any, name: str, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FormalProtocolError(f"{name} must be an object")
    actual = set(value)
    if actual != fields:
        raise FormalProtocolError(
            f"{name} has non-exact fields (missing={sorted(fields - actual)}, "
            f"extra={sorted(actual - fields)})")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise FormalProtocolError(f"{name} must be a normalized identifier")
    return value


def _path(value: Any, name: str) -> str:
    if not isinstance(value, str) or _PATH.fullmatch(value) is None:
        raise FormalProtocolError(f"{name} must be a normalized relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise FormalProtocolError(f"{name} must not escape its root")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FormalProtocolError(f"{name} must be an integer")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FormalTask:
    task_id: str
    raw_input: Any
    expected: int
    weight: int
    scorer: str


@dataclass(frozen=True, slots=True)
class TaskManifest:
    manifest_id: str
    split: str
    tasks: tuple[FormalTask, ...]


@dataclass(frozen=True, slots=True)
class FormalProtocol:
    protocol_id: str
    scope: str
    conformance_profile: str
    recovery: str
    source_basis: Mapping[str, str]
    execution: Mapping[str, Any]
    method: Mapping[str, Any]
    source_fixture: Mapping[str, Any]
    scorer: Mapping[str, Any]
    calibration: TaskManifest
    evolve: TaskManifest
    heldout: TaskManifest
    export: TaskManifest


def _validate_source_basis(value: Any) -> Mapping[str, str]:
    fields = {
        "official_rrsi_revision", "official_source", "official_domain_manifests",
        "external_benchmark_assets", "official_method_parameters",
        "local_fixture", "reproduction_claim",
    }
    data = _closed(value, "source_basis", fields)
    required = {
        "official_rrsi_revision": OFFICIAL_RRSI_REVISION,
        "official_source": "public_and_reviewed",
        "official_domain_manifests": "public_and_reviewed",
        "external_benchmark_assets": "not_required_by_this_local_fixture",
        "official_method_parameters": "public_but_not_the_local_fixture_configuration",
        "local_fixture": "frozen_timeout_application_conformance",
        "reproduction_claim": "not_an_official_paper_benchmark_reproduction",
    }
    if dict(data) != required:
        raise FormalProtocolError("source_basis must state the exact local-only assumptions")
    return MappingProxyType(required)


def _validate_execution(value: Any) -> Mapping[str, Any]:
    fields = {"round_count", "variants_per_round", "repair_rounds",
              "safe_missing_retry_max"}
    data = _closed(value, "execution", fields)
    required = {
        "round_count": 2,
        "variants_per_round": 1,
        "repair_rounds": 0,
        "safe_missing_retry_max": 1,
    }
    if dict(data) != required:
        raise FormalProtocolError("execution differs from frozen B0 local constants")
    return _freeze(required)


def _validate_method(value: Any) -> Mapping[str, Any]:
    fields = {"repetitions", "calibration_repetitions", "n_fail_traces", "n_success_traces",
              "history", "attribution", "role_limits", "selection"}
    data = _closed(value, "method", fields)
    history = _closed(data["history"], "method.history", {"recent", "unmeasured"})
    attribution = _closed(data["attribution"], "method.attribution", {"tail"})
    roles = _closed(data["role_limits"], "method.role_limits",
                    {"Proposer", "Analyst", "Digester", "Critic"})
    selection = _closed(data["selection"], "method.selection",
                        {"beta0", "beta1", "w_s", "w_c", "w_n"})
    required = {
        "repetitions": 1,
        "calibration_repetitions": 2,
        "n_fail_traces": 1,
        "n_success_traces": 1,
        "history": {"recent": 40, "unmeasured": 4},
        "attribution": {"tail": 20},
        "selection": {
            "beta0": 0.1, "beta1": 1.0,
            "w_s": 1.0, "w_c": 1.0, "w_n": 0.0,
        },
        "role_limits": {
            "Proposer": {"max_generations": 40, "max_edits": 80},
            "Analyst": {"max_actions": 30},
            "Digester": {"max_actions": 15, "max_output_tokens": 6000},
            "Critic": {"max_actions": 3},
        },
    }
    if (dict(data) != required or dict(history) != required["history"]
            or dict(attribution) != required["attribution"]
            or dict(selection) != required["selection"]
            or dict(roles) != required["role_limits"]):
        raise FormalProtocolError("method differs from frozen B0 local constants")
    return _freeze(required)


def _validate_fixture(value: Any) -> Mapping[str, Any]:
    data = _closed(value, "source_fixture", {"fixture_id", "files", "editable_paths"})
    if data["fixture_id"] != "timeout-policy-formal-v1":
        raise FormalProtocolError("source_fixture.fixture_id is not fixed")
    if data["editable_paths"] != ["policy.py"]:
        raise FormalProtocolError("only policy.py may be editable")
    files = data["files"]
    if not isinstance(files, list) or len(files) != 2:
        raise FormalProtocolError("source_fixture must have exactly two files")
    expected = {"policy.py": (0o755, "policy"), "agent/__init__.py": (0o644, "agent_init")}
    normalized: list[dict[str, Any]] = []
    for index, file_data in enumerate(files):
        file_data = _closed(file_data, f"source_fixture.files[{index}]", {"path", "mode", "content", "purpose"})
        path = _path(file_data["path"], "source_fixture file path")
        if path not in expected or (file_data["mode"], file_data["purpose"]) != expected[path]:
            raise FormalProtocolError("source_fixture file path, mode, or purpose is not fixed")
        if not isinstance(file_data["content"], str):
            raise FormalProtocolError("source_fixture file content must be text")
        normalized.append(dict(file_data))
    by_path = {item["path"]: item for item in normalized}
    if set(by_path) != set(expected) or by_path["agent/__init__.py"]["content"] != "":
        raise FormalProtocolError("the fixed empty agent package is required")
    if by_path["policy.py"]["content"] != BASELINE_POLICY_SOURCE:
        raise FormalProtocolError("policy.py differs from the exact frozen baseline source")
    return _freeze({"fixture_id": data["fixture_id"], "files": normalized,
                    "editable_paths": ["policy.py"]})


def _validate_scorer(value: Any) -> Mapping[str, Any]:
    data = _closed(value, "scorer", {
        "scorer_id", "response_contract", "decoding", "outer_bounds", "candidate_may_change",
    })
    required = {
        "scorer_id": SCORER,
        "response_contract": "exact_json_object_only_timeout_integer_equal_expected",
        "decoding": {
            "execution_profile": "user_owned_exact_profile",
            "sampling": "adapter_defined",
        },
        "outer_bounds": {"max_llm_attempts": 1, "max_tool_turns": 0},
        "candidate_may_change": ["policy.py"],
    }
    if dict(data) != required:
        raise FormalProtocolError("scorer or candidate boundary is not frozen")
    return _freeze(required)


def _validate_manifest(value: Any, split: str, expected_count: int) -> TaskManifest:
    data = _closed(value, f"task_manifests.{split}", {"manifest_id", "split", "tasks"})
    if data["split"] != split:
        raise FormalProtocolError(f"task_manifests.{split} has wrong split")
    manifest_id = _identifier(data["manifest_id"], f"task_manifests.{split}.manifest_id")
    if manifest_id != MANIFEST_IDS[split]:
        raise FormalProtocolError(f"task_manifests.{split}.manifest_id is not fixed")
    rows = data["tasks"]
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise FormalProtocolError(f"task_manifests.{split} has wrong task count")
    tasks: list[FormalTask] = []
    for index, row in enumerate(rows):
        row = _closed(row, f"task_manifests.{split}.tasks[{index}]",
                      {"task_id", "raw_input", "expected", "weight", "scorer"})
        task_id = _identifier(row["task_id"], f"task_manifests.{split} task_id")
        expected = _integer(row["expected"], f"task_manifests.{split} expected")
        if row["weight"] != 1 or row["scorer"] != SCORER:
            raise FormalProtocolError("all task weights and scorers are frozen")
        try:
            raw_input = json.loads(json.dumps(row["raw_input"], allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise FormalProtocolError("task raw_input must be finite JSON") from exc
        tasks.append(FormalTask(task_id, _freeze(raw_input), expected, 1, SCORER))
    if len({task.task_id for task in tasks}) != len(tasks):
        raise FormalProtocolError(f"task_manifests.{split} repeats a task_id")
    return TaskManifest(manifest_id, split, tuple(tasks))


def _validate_task_values(manifests: Mapping[str, TaskManifest]) -> None:
    expected = {
        "calibration": (("calibration-positive", {"raw": "15"}, 15),),
        "evolve": (("evolve-missing", {}, 30), ("evolve-null", {"raw": None}, 30),
                   ("evolve-blank", {"raw": ""}, 30)),
        "heldout": (("heldout-whitespace", {"raw": " \t "}, 30),
                    ("heldout-positive", {"raw": "15"}, 15),
                    ("heldout-zero-padded", {"raw": "0007"}, 7)),
        "export": (("export-positive", {"raw": "42"}, 42),),
    }
    all_ids: set[str] = set()
    for split, values in expected.items():
        actual = tuple((task.task_id, _thaw(task.raw_input), task.expected)
                       for task in manifests[split].tasks)
        if actual != values:
            raise FormalProtocolError(f"task_manifests.{split} differs from frozen local cases")
        if all_ids.intersection(task.task_id for task in manifests[split].tasks):
            raise FormalProtocolError("task IDs must be disjoint across splits")
        all_ids.update(task.task_id for task in manifests[split].tasks)


def _reject_identity_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_IDENTITY_FIELDS:
                raise FormalProtocolError("formal protocol must not use identity hash/checksum fields")
            _reject_identity_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_identity_fields(item)


def _from_mapping(value: Any) -> FormalProtocol:
    _reject_identity_fields(value)
    root = _closed(value, "formal protocol", {
        "scope", "protocol_id", "conformance_profile", "recovery", "source_basis",
        "execution", "method", "source_fixture", "scorer", "task_manifests",
    })
    if (root["scope"], root["conformance_profile"], root["recovery"]) != (
            SCOPE, CONFORMANCE_PROFILE, RECOVERY):
        raise FormalProtocolError("scope, conformance profile, or recovery is not fixed")
    protocol_id = _identifier(root["protocol_id"], "protocol_id")
    if protocol_id != PROTOCOL_ID:
        raise FormalProtocolError("protocol_id is not fixed")
    manifests_data = _closed(root["task_manifests"], "task_manifests",
                             {"calibration", "evolve", "heldout", "export"})
    manifests = {
        "calibration": _validate_manifest(manifests_data["calibration"], "calibration", 1),
        "evolve": _validate_manifest(manifests_data["evolve"], "evolve", 3),
        "heldout": _validate_manifest(manifests_data["heldout"], "heldout", 3),
        "export": _validate_manifest(manifests_data["export"], "export", 1),
    }
    _validate_task_values(manifests)
    return FormalProtocol(protocol_id, SCOPE, CONFORMANCE_PROFILE, RECOVERY,
                          _validate_source_basis(root["source_basis"]),
                          _validate_execution(root["execution"]), _validate_method(root["method"]),
                          _validate_fixture(root["source_fixture"]), _validate_scorer(root["scorer"]),
                          manifests["calibration"], manifests["evolve"], manifests["heldout"],
                          manifests["export"])


def load_formal_protocol(path: str | Path) -> FormalProtocol:
    """Load and strictly validate the immutable local formal protocol JSON."""
    location = Path(path)
    try:
        value = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormalProtocolError(f"cannot load formal protocol {location}: {exc}") from exc
    return _from_mapping(value)


def formal_protocol_mapping(protocol: FormalProtocol) -> dict[str, Any]:
    """Return a detached JSON-compatible representation of a validated protocol."""
    if not isinstance(protocol, FormalProtocol):
        raise TypeError("protocol must be a FormalProtocol")
    return {
        "scope": protocol.scope, "protocol_id": protocol.protocol_id,
        "conformance_profile": protocol.conformance_profile, "recovery": protocol.recovery,
        "source_basis": _thaw(protocol.source_basis),
        "execution": _thaw(protocol.execution), "method": _thaw(protocol.method),
        "source_fixture": _thaw(protocol.source_fixture), "scorer": _thaw(protocol.scorer),
        "task_manifests": {
            split: {"manifest_id": manifest.manifest_id, "split": manifest.split,
                    "tasks": [{"task_id": task.task_id, "raw_input": _thaw(task.raw_input),
                               "expected": task.expected, "weight": task.weight, "scorer": task.scorer}
                              for task in manifest.tasks]}
            for split, manifest in (("calibration", protocol.calibration), ("evolve", protocol.evolve),
                                    ("heldout", protocol.heldout), ("export", protocol.export))
        },
    }


def role_input_manifest(protocol: FormalProtocol, split: str) -> dict[str, Any]:
    """Project only calibration/evolve task metadata for evolution-role input."""
    if not isinstance(protocol, FormalProtocol):
        raise TypeError("protocol must be a FormalProtocol")
    if split not in {"calibration", "evolve"}:
        raise FormalProtocolError("heldout and export task bodies are evaluation-only")
    manifest = getattr(protocol, split)
    return {"manifest_id": manifest.manifest_id, "split": split,
            "tasks": [{"task_id": task.task_id, "raw_input": _thaw(task.raw_input),
                       "expected": task.expected, "weight": task.weight, "scorer": task.scorer}
                      for task in manifest.tasks]}


def validate_execution_selection(protocol: FormalProtocol, selection) -> None:
    """Require one complete user-owned RPNH execution selection."""
    if not isinstance(protocol, FormalProtocol):
        raise TypeError("protocol must be a FormalProtocol")
    target = getattr(selection, "input_target", None)
    if (target is None
            or not isinstance(target.model_condition, str)
            or not target.model_condition.strip()
            or not isinstance(target.max_output_tokens, int)
            or target.max_output_tokens < 1
            or not isinstance(target.max_response_bytes, int)
            or target.max_response_bytes < 1
            or not isinstance(getattr(selection, "adapter_kind", None), str)
            or not selection.adapter_kind
            or not isinstance(getattr(selection, "timeout_seconds", None), int)
            or selection.timeout_seconds < 1):
        raise FormalProtocolError(
            "execution selection is not a complete RPNH target")
    try:
        policy = selection.as_registry_policy()
    except Exception as exc:
        raise FormalProtocolError(
            "execution selection cannot expose its Registry policy") from exc
    if (not isinstance(policy, Mapping)
            or not isinstance(policy.get("runtime"), Mapping)
            or not isinstance(policy.get("route_provenance"), list)
            or not policy["route_provenance"]):
        raise FormalProtocolError("execution selection has incomplete Registry policy")
