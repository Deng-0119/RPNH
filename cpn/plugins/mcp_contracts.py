"""Selected MCP metadata at the existing HOST boundary.

This module does not register plugins, grant execution, resolve credentials, or
install dependencies. Its values must be published and selected through the
existing Registry/PN path before the transport is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from jsonschema import Draft7Validator

from .api import PluginError, canonical, frozen, json_copy, symbol

DRAFT7 = "http://json-schema.org/draft-07/schema#"
_SCHEMA_VERSIONS = {
    DRAFT7, "https://json-schema.org/draft-07/schema#",
    "https://json-schema.org/draft/2020-12/schema",
}
_SIMPLE = {
    "type", "enum", "const", "title", "description", "default", "examples",
    "deprecated", "readOnly", "writeOnly", "required",
    "minProperties", "maxProperties", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
}
_MAPS = {"properties", "patternProperties"}
_ONE = {"additionalProperties", "propertyNames", "items", "contains", "not",
        "if", "then", "else"}
_MANY = {"allOf", "anyOf", "oneOf"}


def native_schema(document: Mapping[str, Any]) -> dict[str, Any]:
    """Convert only the explicit common subset; never drop unknown semantics.

    The original MCP schema remains in SelectedTool.descriptor. References,
    vocabulary declarations, formats and tuple dialects require another adapter.
    Property names and annotation values are data, not schema keywords.
    """
    source = json_copy(document)
    if not isinstance(source, dict) or source.get("type") != "object":
        raise PluginError("mcp_schema_root_requires_object")

    def visit(node, *, root=False):
        if type(node) is bool:
            return
        if not isinstance(node, dict):
            raise PluginError("mcp_schema_node_invalid")
        for key, value in node.items():
            if key == "$schema":
                if not root or not isinstance(value, str) or value not in _SCHEMA_VERSIONS:
                    raise PluginError("mcp_schema_dialect_unsupported")
            elif key in _SIMPLE:
                continue
            elif key in _MAPS:
                if not isinstance(value, dict):
                    raise PluginError("mcp_schema_map_invalid")
                for child in value.values():
                    visit(child)
            elif key in _ONE:
                visit(value)
            elif key in _MANY:
                if not isinstance(value, list):
                    raise PluginError("mcp_schema_array_invalid")
                for child in value:
                    visit(child)
            else:
                raise PluginError("mcp_schema_keyword_unsupported")
    visit(source, root=True)
    converted = {**source, "$schema": DRAFT7}
    try:
        Draft7Validator.check_schema(converted)
    except Exception as exc:
        raise PluginError("mcp_schema_invalid") from exc
    return converted


@dataclass(frozen=True, slots=True)
class SelectedTool:
    """An exact HOST-selected tool document with an explicit local alias."""
    alias: str
    descriptor: Mapping[str, Any]
    input_schema: Mapping[str, Any] = field(init=False)
    output_schema: Mapping[str, Any] | None = field(init=False)

    def __post_init__(self):
        symbol(self.alias)
        doc = json_copy(self.descriptor)
        if (not isinstance(doc, dict) or not isinstance(doc.get("name"), str)
                or not doc["name"] or "\x00" in doc["name"]):
            raise PluginError("mcp_remote_name_invalid")
        execution = doc.get("execution")
        if execution is not None:
            if not isinstance(execution, dict):
                raise PluginError("mcp_execution_metadata_invalid")
            if execution.get("taskSupport", "forbidden") not in ("forbidden", "optional"):
                raise PluginError("mcp_tasks_not_supported")
        input_schema = native_schema(doc.get("inputSchema"))
        output = doc.get("outputSchema")
        output_schema = None if output is None else native_schema(output)
        object.__setattr__(self, "descriptor", frozen(doc))
        object.__setattr__(self, "input_schema", frozen(input_schema))
        object.__setattr__(self, "output_schema",
                           None if output_schema is None else frozen(output_schema))

    @property
    def remote_name(self):
        return self.descriptor["name"]

    def require_same_document(self, observed):
        if canonical(observed) != canonical(self.descriptor):
            raise PluginError("mcp_selected_tool_changed")

    def arguments(self, value):
        document = json_copy(value)
        if not isinstance(document, dict):
            raise PluginError("mcp_arguments_require_object")
        try:
            Draft7Validator(json_copy(self.input_schema)).validate(document)
        except Exception as exc:
            raise PluginError("mcp_arguments_invalid") from exc
        return document


def catalog_pages(pages, *, max_bytes):
    """Join complete discovery pages, rejecting incomplete/cyclic pagination."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise PluginError("mcp_catalog_limit_invalid")
    documents, names, cursors, used, count = [], set(), set(), 0, 0
    complete = False
    for page in pages:
        if complete:
            raise PluginError("mcp_catalog_after_terminal_page")
        page = json_copy(page)
        if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
            raise PluginError("mcp_catalog_page_invalid")
        used += len(canonical(page))
        if used > max_bytes:
            raise PluginError("mcp_catalog_limit_exceeded")
        count += 1
        for tool in page["tools"]:
            if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                    or not tool["name"] or "\x00" in tool["name"]
                    or not isinstance(tool.get("inputSchema"), dict)):
                raise PluginError("mcp_catalog_tool_invalid")
            if tool["name"] in names:
                raise PluginError("mcp_catalog_duplicate_name")
            names.add(tool["name"])
            documents.append(tool)
        cursor = page.get("nextCursor")
        if cursor is None:
            complete = True
        else:
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise PluginError("mcp_catalog_cursor_invalid")
            cursors.add(cursor)
    if not complete:
        raise PluginError("mcp_catalog_incomplete")
    return {"complete": True, "page_count": count, "tools": documents}


def result_envelope(selection: SelectedTool, raw, *, max_bytes):
    """Preserve a complete response; validate without replacing its raw fields."""
    if type(max_bytes) is not int or max_bytes < 1:
        raise PluginError("mcp_result_limit_invalid")
    raw = json_copy(raw)
    from importlib import metadata
    try:
        if metadata.version("mcp") != "1.30.0":
            raise PluginError("mcp_sdk_version_mismatch")
        from mcp.types import CallToolResult
    except (metadata.PackageNotFoundError, ImportError) as exc:
        raise PluginError("mcp_sdk_missing") from exc
    try:
        CallToolResult.model_validate(raw, strict=True)
        protocol_valid = True
    except Exception:
        protocol_valid = False
    tool_error = isinstance(raw, dict) and raw.get("isError") is True
    validation = "not_declared"
    if not protocol_valid:
        validation = "not_checked_protocol_invalid"
    elif selection.output_schema is not None:
        validation = "not_applicable_tool_error" if tool_error else "valid"
        if not tool_error:
            try:
                Draft7Validator(json_copy(selection.output_schema)).validate(raw.get("structuredContent"))
            except Exception:
                validation = "output_schema_mismatch"
    envelope = {
        "schema_version": "rpnh/mcp_call_observation/v1",
        "remote_name": selection.remote_name,
        "result": raw,
        "business_status": ("invalid_result" if not protocol_valid else
                            "error" if tool_error else
                            "invalid_result" if validation == "output_schema_mismatch" else
                            "returned"),
        "protocol_validation": "valid" if protocol_valid else "invalid",
        "output_validation": validation,
    }
    if len(canonical(envelope)) > max_bytes:
        raise PluginError("mcp_result_limit_exceeded")
    return envelope

