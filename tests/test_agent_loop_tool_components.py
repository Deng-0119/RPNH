from __future__ import annotations

from cpn.components import agent_loop
from cpn.components.agent_loop import tools
from cpn.components.agent_loop import tool_catalog, tool_projection, tool_validation


def test_legacy_and_package_tool_exports_are_leaf_identities() -> None:
    assert tools.AGENT_TOOL_NAMES is tool_catalog.AGENT_TOOL_NAMES
    assert tools.TOOL_ARGUMENT_SCHEMAS is tool_catalog.TOOL_ARGUMENT_SCHEMAS
    assert tools.AgentToolCatalog is tool_catalog.AgentToolCatalog
    assert tools.build_agent_tool_catalog is tool_catalog.build_agent_tool_catalog
    assert tools.parse_agent_tool_catalog is tool_catalog.parse_agent_tool_catalog
    assert tools.derive_atomic_subtask_tools is tool_catalog.derive_atomic_subtask_tools
    assert tools.bounded_agent_read_projection is (
        tool_projection.bounded_agent_read_projection)
    assert tools.bounded_agent_text_search_projection is (
        tool_projection.bounded_agent_text_search_projection)
    assert tools.bounded_agent_action_output_projection is (
        tool_projection.bounded_agent_action_output_projection)
    assert tools.AgentToolSyntaxError is tool_validation.AgentToolSyntaxError
    assert tools.ValidatedAgentToolAction is tool_validation.ValidatedAgentToolAction
    assert tools.validate_agent_tool_call is tool_validation.validate_agent_tool_call
    assert tools.validate_agent_tool_observation is (
        tool_validation.validate_agent_tool_observation)
    assert tools.workspace_execution_mode is tool_validation.workspace_execution_mode
    assert agent_loop.AGENT_TOOL_NAMES is tools.AGENT_TOOL_NAMES
    assert agent_loop.AgentToolCatalog is tools.AgentToolCatalog
    assert agent_loop.build_agent_system_initialization is (
        tools.build_agent_system_initialization)
    assert agent_loop.build_agent_tool_catalog is tools.build_agent_tool_catalog


def test_catalog_and_projection_surfaces_remain_canonical() -> None:
    catalog = tools.build_agent_tool_catalog()
    assert tools.parse_agent_tool_catalog(catalog.payload) == catalog
    assert tuple(item["name"] for item in catalog.tool_descriptors) == (
        tools.DEFAULT_AGENT_TOOL_NAMES)
    assert "read_managed_output" not in catalog.tool_names
    with_reader = tools.build_agent_tool_catalog(tool_names=tools.AGENT_TOOL_NAMES)
    assert "read_managed_output" in with_reader.tool_names
    assert tools.parse_agent_tool_catalog(with_reader.payload) == with_reader
    assert tools.bounded_agent_read_projection(
        b"abcd", {"offset_chars": 1, "max_chars": 2},
    ) == {
        "content": "bc", "encoding": "utf-8", "offset_chars": 1,
        "next_offset_chars": 3, "total_chars": 4,
    }
    assert tools.bounded_agent_text_search_projection(
        b"a\nba", {"query": "a", "max_matches": 2, "context_chars": 0},
    )["matches"] == [
        {
            "match_ordinal": 0, "line_number": 1, "column_number": 1,
            "match_start_chars": 0, "excerpt_start_chars": 0, "excerpt": "a",
        },
        {
            "match_ordinal": 1, "line_number": 2, "column_number": 2,
            "match_start_chars": 3, "excerpt_start_chars": 3, "excerpt": "a",
        },
    ]
    assert tools.bounded_agent_action_output_projection(
        {
            "kind": "workspace_execution/v1", "status": "completed",
            "exit_code": 0, "stdout": "abcd", "stderr": "",
            "output_truncated": False, "command_started": True,
        },
        {
            "agent_action_ref": {
                "entity_type": "agent_action/v2",
                "logical_id": "agent_action:" + "0" * 32,
                "version_id": "agent_action_version:" + "1" * 32,
            },
            "stream": "stdout", "offset_chars": 1, "max_chars": 2,
        },
    )["content"] == "bc"


def test_malformed_call_and_workspace_schema_remain_closed() -> None:
    malformed = tools.validate_agent_tool_call(
        loop_id="agent_loop:" + "0" * 32,
        turn_sequence=0,
        expected_revision=0,
        tool_call_id="bad-call",
        tool_name="workspace",
        raw_arguments='{"script":"echo ok","timeout_seconds":1,"extra":true}',
    )
    assert isinstance(malformed, tools.AgentToolSyntaxError)
    assert malformed.code == "arguments_invalid"
    workspace_schema = tools.TOOL_ARGUMENT_SCHEMAS["workspace"]
    assert workspace_schema["additionalProperties"] is False
    assert workspace_schema["properties"]["execution_mode"]["enum"] == [
        "sync", "monitored"]
