"""Explicit comparison entry points; preserve baseline handler-module identity."""
from __future__ import annotations

from .comparison_condition import DISCOVERY_TOOL
from .native_plugin import definition_for as baseline_definition, native_handler
from .office_cases import PLUGIN_SLOTS

VERSION = "0.1.0"


def definition_for(plugin_name: str):
    from cpn.plugins.api import PluginDefinition, PluginOperation
    base = baseline_definition(plugin_name)
    discovery = PluginOperation(
        name=DISCOVERY_TOOL, description="Discover public Office KB metadata once.",
        input_schema={"type": "object"},
        output_schema=base.operations[0].output_schema,
        handler=native_handler, effect="external_read", timeout_seconds=60,
        max_result_bytes=4 * 1024 * 1024)
    return PluginDefinition(name=plugin_name, version=VERSION,
                            operations=(*base.operations, discovery), config_schema=base.config_schema)


def manager_factory(): return definition_for("ha_manager")
def admin_factory(): return definition_for("ha_admin")
def policy_factory(): return definition_for("ha_policy")
def extra_factory(): return definition_for("ha_extra")


def configuration(endpoints: dict[str, str], run_id: str) -> dict:
    if not endpoints or set(endpoints) - set(PLUGIN_SLOTS):
        raise ValueError("registered host slot endpoints are required")
    return {"schema_version": "rpnh/plugins/v1", "plugins": [
        {"name": name, "entry_point": name.replace("ha_", "ha_comparison_", 1),
         "version": VERSION, "config": {"endpoint": endpoints[name], "run_id": run_id},
         "environment": []} for name in sorted(endpoints)]}
