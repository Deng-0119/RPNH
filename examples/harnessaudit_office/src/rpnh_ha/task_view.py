"""Explicit public-input projection. Grader fields are never driver inputs.

Tool necessity, access rules and communication policy remain in the evaluator.
All domain tools are retained; no task-gold useful-tools filtering is performed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from .jsonio import dumps, loads
from .office_cases import CASE_PATHS

HIDDEN_FIELDS = frozenset({
    "access_rules", "communication_policy", "tool_necessity", "useful_tools",
    "completion_checkpoints", "ground_truth_tool_paths", "fixture_data",
    "mock_response", "fixture", "fixture_key", "perturbation_specs",
})


def _map(value: Any) -> Mapping:
    if isinstance(value, Mapping): return value
    if callable(getattr(value, "model_dump", None)): return value.model_dump()
    raise TypeError("expected a mapping or upstream Pydantic schema")


@dataclass(frozen=True)
class PublicTask:
    """Data-only copy; does not hold a hidden TaskConfig or a backend reference."""
    payload: str

    def as_dict(self) -> dict:
        return loads(self.payload)


def public_task(task: Any, catalog: Any) -> PublicTask:
    t, c = _map(task), _map(catalog)
    if t.get("domain") != "office" or c.get("domain") != "office":
        raise ValueError("this kit only supports the office domain")
    if t.get("task_id") not in CASE_PATHS:
        raise ValueError("task is outside the explicit Office campaign")
    if t.get("modality", "text_only") != "text_only" or t.get("input_assets"):
        raise ValueError("multimodal input is not silently dropped")
    roles = []
    for raw in t["agents"]:
        a = _map(raw)
        role = a["role"]
        if not isinstance(role, str) or not role or role == "user":
            raise ValueError("invalid business role")
        roles.append({"role": role, "description": a["description"],
                      "system_prompt": a.get("system_prompt", "")})
    if len({a["role"] for a in roles}) != len(roles):
        raise ValueError("duplicate business role")
    hub = t.get("metadata", {}).get("hub_role")
    if hub not in {a["role"] for a in roles}:
        raise ValueError("explicit upstream hub_role is required")
    tools = []
    for raw in c["tools"]:
        d = _map(raw)
        # params are upstream public parameter type strings, not answer hints.
        tools.append({"name": d["name"], "description": d.get("description", ""),
                      "params": dict(d.get("params", {}))})
    if len({d["name"] for d in tools}) != len(tools) or not tools:
        raise ValueError("tool catalog must be nonempty and unique")
    result = {"schema_version": "rpnh-ha/public-task/v2", "task_id": t["task_id"],
              "domain": t["domain"], "goal": t["goal"], "hub_role": hub,
              "agents": roles, "tools": tools}
    # Key-based guard is structural, not heuristic text redaction.
    assert_no_hidden_keys(result)
    return PublicTask(dumps(result))


def assert_no_hidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        bad = HIDDEN_FIELDS.intersection(value)
        if bad: raise ValueError("grader fields leaked: " + ",".join(sorted(bad)))
        for item in value.values(): assert_no_hidden_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value: assert_no_hidden_keys(item)
