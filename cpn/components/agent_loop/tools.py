"""Compatibility façade for AgentLoop tool contracts and context rendering."""

from __future__ import annotations

from .context import (
    UserAttachmentNavigation, build_agent_system_initialization,
    render_registered_hardware_inventory,
)
from .models import AgentSystemInitialization, LocatedAgentInput
from .tool_catalog import (
    AGENT_TOOL_NAMES,
    DEFAULT_AGENT_TOOL_NAMES,
    DELEGATE_LEAF_TOOL_NAME,
    IMMEDIATE_COMPLETION_GUIDANCE,
    READ_FILE_MODEL_DESCRIPTION,
    READ_FILE_UNREGISTERED_PATH_DETAIL,
    READ_FILE_WORKSPACE_ROUTING_GUIDANCE,
    REQUEST_RESOURCE_TOOL_NAME,
    TOOL_ARGUMENT_SCHEMAS,
    AgentToolCatalog,
    _canonical_agent_tool_catalog_payload,
    _canonical_catalog_document,
    _parse_agent_tool_catalog_document,
    _unique_object_pairs,
    build_agent_tool_catalog,
    derive_atomic_subtask_tools,
    model_visible_agent_tool_description,
    parse_agent_tool_catalog,
)
from .tool_projection import (
    bounded_agent_action_output_projection,
    bounded_agent_read_projection,
    bounded_agent_text_search_projection,
)
from .tool_validation import (
    AgentToolSyntaxError,
    ValidatedAgentToolAction,
    _decode_unique_object,
    _validate_schema,
    validate_agent_tool_call,
    validate_agent_tool_observation,
    workspace_execution_mode,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef


__all__ = [
    "AGENT_TOOL_NAMES", "DELEGATE_LEAF_TOOL_NAME",
    "REQUEST_RESOURCE_TOOL_NAME",
    "TOOL_ARGUMENT_SCHEMAS",
    "IMMEDIATE_COMPLETION_GUIDANCE",
    "READ_FILE_MODEL_DESCRIPTION", "READ_FILE_UNREGISTERED_PATH_DETAIL",
    "READ_FILE_WORKSPACE_ROUTING_GUIDANCE",
    "AgentToolCatalog",
    "UserAttachmentNavigation",
    "AgentToolSyntaxError", "ValidatedAgentToolAction",
    "bounded_agent_action_output_projection",
    "bounded_agent_read_projection",
    "bounded_agent_text_search_projection",
    "build_agent_system_initialization", "build_agent_tool_catalog",
    "parse_agent_tool_catalog",
    "derive_atomic_subtask_tools",
    "render_registered_hardware_inventory",
    "validate_agent_tool_call", "validate_agent_tool_observation",
    "workspace_execution_mode",
]
