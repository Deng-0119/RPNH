"""Explicit MCP contributions to the original native/managed plugin catalog.

HOST code selects a deployment and exact tool documents. Discovery remains an
ordinary, separately admitted read operation. Neither a server annotation nor
the discovery output installs code or selects tools, accounts, or effects.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .api import (API_VERSION_V2, PluginDefinition, PluginError, PluginOperation,
                  checked_account_requirement, json_copy, plugin_implementation, symbol, version)
from .mcp_contracts import SelectedTool
from .mcp_stdio import StdioDeployment, invoke_selected


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    selection: SelectedTool
    declaration_revision: str
    effect: str
    timeout_seconds: int = 60
    account_requirement: Mapping[str, str] | None = None

    def __post_init__(self):
        if not isinstance(self.selection, SelectedTool):
            raise PluginError("mcp_binding_requires_selected_document")
        version(self.declaration_revision)
        if self.effect not in {"external_read", "external_write"}:
            raise PluginError("mcp_effect_requires_explicit_external_classification")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 7200:
            raise PluginError("mcp_timeout_invalid")
        object.__setattr__(self, "account_requirement", checked_account_requirement(self.account_requirement))


def _deployment(value):
    value = json_copy(value)
    value["args"] = tuple(value["args"])
    return StdioDeployment(**value)


@plugin_implementation("rpnh.mcp.selected_stdio_tool", "1")
def invoke_mcp_tool(context, arguments):
    config = context.config
    if context.operation_id.count("/") != 1:
        raise PluginError("mcp_operation_identity_invalid")
    plugin, alias = context.operation_id.split("/")
    if plugin != config["plugin_name"] or alias not in config["tools"]:
        raise PluginError("mcp_operation_not_selected")
    selection = SelectedTool(alias, config["tools"][alias])
    return invoke_selected(
        _deployment(config["deployment"]), selection, arguments,
        environment=context.child_environment,
        timeout_seconds=context.remaining_seconds,
        cancelled=lambda: context.cancelled,
    )


@plugin_implementation("rpnh.mcp.stdio_discovery", "1")
def discover_mcp_tools(context, arguments):
    if arguments != {} or context.operation_id != context.config["plugin_name"] + "/discover":
        raise PluginError("mcp_discovery_selection_invalid")
    return invoke_selected(
        _deployment(context.config["deployment"]), None,
        environment=context.child_environment,
        timeout_seconds=context.remaining_seconds,
        cancelled=lambda: context.cancelled,
    )


_OBSERVATION_SCHEMA = {
    "type": "object", "required": ["schema_version", "remote_name", "result",
        "business_status", "protocol_validation", "output_validation"],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": "rpnh/mcp_call_observation/v1"},
        "remote_name": {"type": "string", "minLength": 1}, "result": {},
        "business_status": {"enum": ["returned", "error", "invalid_result"]},
        "protocol_validation": {"enum": ["valid", "invalid"]},
        "output_validation": {"enum": ["not_declared", "not_checked_protocol_invalid",
            "not_applicable_tool_error", "valid", "output_schema_mismatch"]},
    },
}


def mcp_plugin(*, name: str, plugin_version: str, deployment: StdioDeployment,
               tools: tuple[MCPToolBinding, ...]) -> tuple[PluginDefinition, dict]:
    """Build a declaration and exact nonsecret config; no I/O or registration.

    Pass the returned pair to ``BoundPlugin`` with an explicit binding revision,
    then to a versioned ``PluginCatalog``. HOST account selection supplies the
    worker-private child environment through the normal invocation service.
    """
    symbol(name)
    version(plugin_version)
    if not isinstance(deployment, StdioDeployment):
        raise PluginError("mcp_deployment_required")
    if (not isinstance(tools, tuple) or not 1 <= len(tools) <= 128
            or any(not isinstance(tool, MCPToolBinding) for tool in tools)
            or len({tool.selection.alias for tool in tools}) != len(tools)
            or len({tool.selection.remote_name for tool in tools}) != len(tools)):
        raise PluginError("mcp_explicit_distinct_tools_required")
    config = {"schema_version": "rpnh/mcp_deployment_binding/v1",
              "plugin_name": name, "deployment": json_copy(asdict(deployment)),
              "tools": {tool.selection.alias: json_copy(tool.selection.descriptor)
                        for tool in tools}}
    operations = tuple(PluginOperation(
        name=tool.selection.alias,
        description=(tool.selection.descriptor.get("description") or
                     "Invoke the selected MCP tool " + tool.selection.remote_name),
        input_schema=tool.selection.input_schema, output_schema=_OBSERVATION_SCHEMA,
        handler=invoke_mcp_tool, effect=tool.effect,
        timeout_seconds=tool.timeout_seconds, max_result_bytes=deployment.max_result_bytes,
        identity_version="v2", declaration_revision=tool.declaration_revision,
        account_requirement=tool.account_requirement,
    ) for tool in tools)
    return PluginDefinition(name, plugin_version, operations,
        config_schema={"type": "object", "const": config}, api_version=API_VERSION_V2), config


def mcp_discovery_plugin(*, name: str, plugin_version: str, declaration_revision: str,
                         deployment: StdioDeployment, timeout_seconds: int = 60,
                         account_requirement: Mapping[str, str] | None = None):
    """Declare an explicit discovery read; the result confers no tool authority."""
    symbol(name)
    version(plugin_version)
    version(declaration_revision)
    if not isinstance(deployment, StdioDeployment):
        raise PluginError("mcp_deployment_required")
    config = {"schema_version": "rpnh/mcp_discovery_binding/v1", "plugin_name": name,
              "deployment": json_copy(asdict(deployment))}
    operation = PluginOperation("discover", "Read the selected MCP server's complete tool catalog",
        {"type": "object", "additionalProperties": False},
        {"type": "object", "additionalProperties": False,
         "required": ["complete", "page_count", "tools"],
         "properties": {"complete": {"const": True}, "page_count": {"type": "integer", "minimum": 1},
                        "tools": {"type": "array", "items": {"type": "object"}}}},
        discover_mcp_tools, effect="external_read", timeout_seconds=timeout_seconds,
        max_result_bytes=deployment.max_result_bytes, identity_version="v2",
        declaration_revision=declaration_revision, account_requirement=account_requirement)
    return PluginDefinition(name, plugin_version, (operation,),
        config_schema={"type": "object", "const": config}, api_version=API_VERSION_V2), config
