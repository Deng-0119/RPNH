"""Approved Office text cases and host role slots; no grader or answer data."""
from __future__ import annotations
from pathlib import Path
from typing import Mapping, Any

CASE_PATHS = {
    "off-t1": "multi_agent/tasks/office/personnel/off-t1.yaml",
    "off-t2": "multi_agent/tasks/office/office_asset/off-t2.yaml",
    "off-t3": "multi_agent/tasks/office/office_asset/off-t3.yaml",
    "off-t4": "multi_agent/tasks/office/finance/off-t4.yaml",
    "off-t5": "multi_agent/tasks/office/finance/off-t5.yaml",
    "off-t6": "multi_agent/tasks/office/office_asset/off-t6.yaml",
}
# Legacy names are private host slots, NOT business-role declarations.
PLUGIN_SLOTS = ("ha_manager", "ha_admin", "ha_policy", "ha_extra")
CAMPAIGN_TASKS = ("off-t3", "off-t1", "off-t4", "off-t5", "off-t6")


def case_path(root: Path, task_id: str) -> Path:
    if task_id not in CASE_PATHS:
        raise ValueError("task is outside the explicit Office campaign")
    return Path(root) / CASE_PATHS[task_id]


def plugin_roles_for_task(view: Mapping[str, Any]) -> dict[str, str]:
    rows = view.get("agents")
    if not isinstance(rows, list) or not 2 <= len(rows) <= len(PLUGIN_SLOTS):
        raise ValueError("Office bridge supports one hub and one to three specialists")
    names = [row.get("role") if isinstance(row, Mapping) else None for row in rows]
    if (any(not isinstance(role, str) or not role or role == "user" for role in names)
            or len(set(names)) != len(names) or view.get("hub_role") not in names):
        raise ValueError("public role inventory is invalid")
    ordered = [view["hub_role"], *(role for role in names if role != view["hub_role"])]
    return dict(zip(PLUGIN_SLOTS, ordered))
