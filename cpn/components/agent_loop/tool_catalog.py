"""Closed AgentLoop tool catalog representation and canonical construction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError


# Operation specs require lexical, unique, sorted tool ids.  Keep this tuple in
# that exact order; it is also the one outward agent-tool surface.
AGENT_TOOL_NAMES = (
    "complete_interaction",
    "delegate_leaf",
    "query_environment_resources",
    "query_kb",
    "query_registry_resources",
    "read_action_output",
    "read_file",
    "request_resource",
    "search_text",
    "workspace",
    "write_file",
)

DELEGATE_LEAF_TOOL_NAME = "delegate_leaf"
REQUEST_RESOURCE_TOOL_NAME = "request_resource"


READ_FILE_MODEL_DESCRIPTION = (
    "Read only an exact registered Located input or registered resource "
    "delivery, never an arbitrary workspace file. Ordinary project files "
    "discovered or created in the workspace are outside this tool's authority; "
    "when workspace is available, use workspace to inspect those files. "
    "Use search_text first when locating a symbol, constant, requirement, or "
    "result."
)

READ_FILE_WORKSPACE_ROUTING_GUIDANCE = (
    "File-read capability routing: use read_file only for a path explicitly "
    "supplied as an exact registered Located input or resource delivery; use "
    "workspace for every ordinary project file discovered or created in the "
    "workspace tree."
)

READ_FILE_UNREGISTERED_PATH_DETAIL = (
    "read_file path is not an exact registered Located input or resource "
    "delivery; ordinary workspace files are outside read_file authority, so "
    "use workspace when that tool is available"
)


IMMEDIATE_COMPLETION_GUIDANCE = (
    "Immediate-completion rule: hard requirements are only those explicitly "
    "declared by the original assignment/task (including this exact role's "
    "task-derived responsibility to the extent it faithfully carries that "
    "original task) and the required output for this role. Once those hard "
    "requirements and the necessary registered "
    "evidence are satisfied, submit the required output and complete "
    "immediately. Do not spend another provider or tool call on optional "
    "optimization, elegance, robustness beyond a declared bound, method "
    "preferences, solver convergence beyond declared acceptance, or additional "
    "evidence the original task did not require. A role prompt or critic must "
    "not promote an undeclared method or quality preference into a hard "
    "requirement. This rule never permits bypassing required write_file and "
    "complete_interaction calls."
)


TOOL_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "complete_interaction": {
        "type": "object", "additionalProperties": False,
        "properties": {},
    },
    "delegate_leaf": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "instruction": {"type": "string", "minLength": 1},
            "resource_refs": {
                "type": "array", "uniqueItems": True,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "resource_id": {
                            "type": "string",
                            "pattern": "^resource:[a-f0-9]{32}$",
                        },
                        "resource_version_id": {
                            "type": "string",
                            "pattern": "^resource_version:[a-f0-9]{32}$",
                        },
                    },
                    "required": ["resource_id", "resource_version_id"],
                },
            },
        },
        "required": ["instruction", "resource_refs"],
    },
    "query_environment_resources": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "packages": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
                },
            },
        },
    },
    "query_kb": {
        "type": "object",
        "oneOf": [
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "operation": {"const": "search"},
                    "query": {"type": "string", "minLength": 1},
                    "kind": {"type": ["string", "null"], "minLength": 1},
                    "tags_any": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "tags_all": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "offset": {"type": "integer", "minimum": 0},
                    "page_size": {
                        "type": "integer", "minimum": 1,
                    },
                },
                "required": ["operation", "query"],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "operation": {"const": "read"},
                    "item_id": {"type": "string", "minLength": 1},
                    "offset_chars": {"type": "integer", "minimum": 0},
                    "max_chars": {
                        "type": "integer", "minimum": 1,
                    },
                },
                "required": ["operation", "item_id"],
            },
        ],
    },
    "query_registry_resources": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "maxLength": 256},
            "view": {"type": "string", "enum": ["current", "history"]},
            "offset": {"type": "integer", "minimum": 0},
            "page_size": {
                "type": "integer", "minimum": 1, "maximum": 20,
            },
        },
    },
    "read_action_output": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "agent_action_ref": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "entity_type": {"const": "agent_action/v2"},
                    "logical_id": {
                        "type": "string",
                        "pattern": "^agent_action:[a-f0-9]{32}$",
                    },
                    "version_id": {
                        "type": "string",
                        "pattern": "^agent_action_version:[a-f0-9]{32}$",
                    },
                },
                "required": ["entity_type", "logical_id", "version_id"],
            },
            "stream": {"type": "string", "enum": ["stdout", "stderr"]},
            "offset_chars": {"type": "integer", "minimum": 0},
            "max_chars": {
                "type": "integer", "minimum": 1, "maximum": 32768,
            },
        },
        "required": ["agent_action_ref", "stream"],
    },
    "read_file": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "offset_chars": {"type": "integer", "minimum": 0},
            "max_chars": {
                "type": "integer", "minimum": 1, "maximum": 32768,
            },
        },
        "required": ["path"],
    },
    "request_resource": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "resource_id": {
                "type": "string",
                "pattern": "^resource:[a-f0-9]{32}$",
            },
            "resource_version_id": {
                "type": "string",
                "pattern": "^resource_version:[a-f0-9]{32}$",
            },
            "access_mode": {"type": "string", "enum": ["read", "edit"]},
        },
        "required": ["resource_id", "resource_version_id", "access_mode"],
    },
    "search_text": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1, "maxLength": 256},
            "case_sensitive": {"type": "boolean"},
            "offset_match": {"type": "integer", "minimum": 0},
            "max_matches": {
                "type": "integer", "minimum": 1, "maximum": 20,
            },
            "context_chars": {
                "type": "integer", "minimum": 0, "maximum": 256,
            },
        },
        "required": ["path", "query"],
    },
    "workspace": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "script": {
                "type": "string", "minLength": 1, "maxLength": 65536,
            },
            "timeout_seconds": {
                "type": "integer", "minimum": 1, "maximum": 7200,
            },
            "execution_mode": {
                "type": "string", "enum": ["sync", "monitored"],
                "default": "sync",
            },
        },
        "required": ["script", "timeout_seconds"],
    },
    "write_file": {
        "type": "object",
        "oneOf": [
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "description": {
                        "type": "string", "minLength": 1, "maxLength": 600,
                    },
                    "content": {"type": "string", "minLength": 1},
                    "output_port_id": {"type": "string", "minLength": 1},
                    "outcome_id": {"type": "string", "minLength": 1},
                },
                "required": ["path", "description", "content"],
            },
            {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "description": {
                        "type": "string", "minLength": 1, "maxLength": 600,
                    },
                    "source_resource_ref": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "resource_id": {
                                "type": "string",
                                "pattern": "^resource:[a-f0-9]{32}$",
                            },
                            "resource_version_id": {
                                "type": "string",
                                "pattern": (
                                    "^resource_version:[a-f0-9]{32}$"),
                            },
                        },
                        "required": [
                            "resource_id", "resource_version_id"],
                    },
                    "output_port_id": {"type": "string", "minLength": 1},
                    "outcome_id": {"type": "string", "minLength": 1},
                },
                "required": [
                    "path", "description", "source_resource_ref"],
            },
        ],
    },
}


@dataclass(frozen=True, slots=True)
class AgentToolCatalog:
    catalog_ref: str
    payload: bytes
    tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.catalog_ref != "registry_agent_tool_catalog/v1":
            raise ValueError("agent tool catalog is not the parent current surface")
        document, names = _parse_agent_tool_catalog_document(self.payload)
        if self.tool_names != names:
            raise ValueError(
                "agent tool catalog names differ from its registered descriptors")
        if self.payload != _canonical_catalog_document(document):
            raise ValueError(
                "agent tool catalog payload differs from its canonical surface")

    @property
    def tool_descriptors(self) -> tuple[Mapping[str, Any], ...]:
        document, _names = _parse_agent_tool_catalog_document(self.payload)
        return tuple(document["tools"])

    @property
    def argument_schemas(self) -> Mapping[str, Mapping[str, Any]]:
        return {
            str(tool["name"]): tool["arguments"]
            for tool in self.tool_descriptors
        }


def _canonical_agent_tool_catalog_payload() -> bytes:
    """Return the sole canonical persisted payload for an authorized surface."""
    document = {
        "schema_version": "agent_tool_catalog/v1",
        "surface_kind": "parent_current",
        "workspace_scope": "firing_private_projection",
        "tools": [
            {
                "name": name,
                "description": {
                    "complete_interaction": (
                        "Complete only after at least one write_file call succeeded "
                        "and registered a document. It may be the final ordered "
                        "tool call in the same response as the final write_file."),
                    "delegate_leaf": (
                        "Open one depth-one, one-atomic-task subtask session "
                        "annotation under this parent invocation. It is not a "
                        "team agent or child invocation and receives no separate "
                        "execution authority. All nested calls, tools, files, "
                        "and results remain parent-produced and provisional "
                        "under this firing and workspace. Redelegation is "
                        "unavailable."),
                    "query_environment_resources": (
                        "Optionally point-query up to 16 Python distributions from "
                        "the complete fixed research-exp inventory registered before "
                        "provider use. Omit packages for the NumPy/SciPy minimum. "
                        "Registry registers the exact query result; installed_resources "
                        "and not_installed are informational and never authorize or "
                        "block normal imports."),
                    "query_kb": (
                        "Search the firing-authorized immutable external KB metadata "
                        "or read one exact item_id. Search never opens bodies; read "
                        "returns one bounded page and records that exact access."),
                    "query_registry_resources": (
                        "Search or list bounded metadata for run-generated resources "
                        "authorized to this active firing. Empty query lists current "
                        "resources; view=history also includes authorized superseded "
                        "versions. Query summaries first, then use the exact returned "
                        "resource identity or path with request_resource, read_file, "
                        "or search_text when body evidence is needed. This tool never "
                        "returns file bodies, stdout, or stderr."),
                    "read_action_output": (
                        "Read one bounded page of stdout or stderr from one exact "
                        "prior workspace agent_action/v2 in this live firing. When "
                        "older output is needed, use its exact locator rather than "
                        "rerunning solely to display the same output; rerun when "
                        "fresh execution is semantically required. Use offset_chars "
                        "and max_chars for pagination; max_chars is bounded to "
                        "32768."),
                    "read_file": (
                        READ_FILE_MODEL_DESCRIPTION),
                    "request_resource": (
                        "Request read or owner-only edit access to one exact "
                        "registered resource in this firing. Registry/Petri lock "
                        "authority decides grant or WAITING_RESOURCE; the request "
                        "does not rebind the firing."),
                    "search_text": (
                        "Search literal text in one exact registered Located input "
                        "or resource delivery. Returns bounded line/column excerpts "
                        "and pagination metadata without opening the complete body. "
                        "Use it after the file summary and before full-page reads."),
                    "workspace": (
                        "Run one opaque shell program in the firing-private view "
                        "of the exact Registry/Petri-authorized workspace projection. "
                        "The framework does not inspect commands, paths, scripts, "
                        "or internal file changes. OS isolation confines writes to "
                        "the workspace and denies network access. Captured logs are "
                        "truncated at the registered byte limit without terminating "
                        "the command. Every call must supply timeout_seconds "
                        "(1-7200) as its requested deadline. If the registered wall "
                        "deadline is none, the request is effective; otherwise the "
                        "effective deadline is the smaller of the request and the "
                        "registered cap shown for this firing. execution_mode defaults "
                        "to sync. monitored durably records ACTION_PENDING before the "
                        "command starts and must be the sole tool call in that provider "
                        "turn. Size the workload to "
                        "finish well under the effective deadline. When it expires "
                        "the framework kills the command and returns one timed_out "
                        "status to the same agent loop as a correctable result. If "
                        "the workload exceeds the effective deadline, split "
                        "it into checkpointable resumable chunks: persist "
                        "intermediate state into the workspace and resume from it in "
                        "a follow-up call."),
                    "write_file": (
                        "Register one nonempty root-relative UTF-8 workspace file "
                        "as the exact semantic output on a declared Petri binding. "
                        "Supply a concise description of the file's purpose and "
                        "important result so downstream agents can navigate it. "
                        "Either supply content to write exact bytes directly, or "
                        "first use query_registry_resources summaries and select "
                        "one exact current source_resource_ref returned for this "
                        "active firing. Workspace bytes are never an implicit write "
                        "source. The final write_file and complete_interaction "
                        "may be ordered in the same response when completion is the "
                        "final tool call."),
                }[name],
                "arguments": TOOL_ARGUMENT_SCHEMAS[name],
            }
            for name in AGENT_TOOL_NAMES
        ],
    }
    return json.dumps(
        document, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def _canonical_catalog_document(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate field {key!r}")
        value[key] = item
    return value


def _parse_agent_tool_catalog_document(
        payload: bytes) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("agent tool catalog payload must be nonempty bytes")
    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("agent tool catalog payload is not JSON") from exc
    if (not isinstance(document, dict)
            or set(document) != {
                "schema_version", "surface_kind", "workspace_scope", "tools"}
            or document.get("schema_version") != "agent_tool_catalog/v1"
            or document.get("surface_kind") != "parent_current"
            or document.get("workspace_scope")
            != "firing_private_projection"
            or not isinstance(document.get("tools"), list)):
        raise ValueError("agent tool catalog document is malformed")
    names: list[str] = []
    for tool in document["tools"]:
        if (not isinstance(tool, dict)
                or set(tool) != {"name", "description", "arguments"}
                or not isinstance(tool.get("name"), str)
                or re.fullmatch(r"[a-z][a-z0-9_]*", tool["name"]) is None
                or not isinstance(tool.get("description"), str)
                or not tool["description"].strip()
                or not isinstance(tool.get("arguments"), dict)):
            raise ValueError("agent tool catalog descriptor is malformed")
        try:
            Draft7Validator.check_schema(tool["arguments"])
        except SchemaError as exc:
            raise ValueError(
                "agent tool catalog argument schema is invalid") from exc
        names.append(tool["name"])
    if len(set(names)) != len(names):
        raise ValueError("agent tool catalog names must be unique")
    return document, tuple(names)


def parse_agent_tool_catalog(payload: bytes) -> AgentToolCatalog:
    """Parse one registered global catalog without fixed names or archive order."""

    _document, names = _parse_agent_tool_catalog_document(payload)
    return AgentToolCatalog("registry_agent_tool_catalog/v1", payload, names)


def build_agent_tool_catalog() -> AgentToolCatalog:
    payload = _canonical_agent_tool_catalog_payload()
    return AgentToolCatalog(
        "registry_agent_tool_catalog/v1", payload, AGENT_TOOL_NAMES)


def derive_atomic_subtask_tools(
        parent_catalog: AgentToolCatalog,
) -> tuple[dict[str, Any], ...]:
    """Filter tools without a child-local settlement path from one catalog."""

    if not isinstance(parent_catalog, AgentToolCatalog):
        raise TypeError("atomic subtask tools require the admitted parent catalog")
    document = json.loads(parent_catalog.payload)
    return tuple(
        tool for tool in document["tools"]
        if tool["name"] not in {
            DELEGATE_LEAF_TOOL_NAME, REQUEST_RESOURCE_TOOL_NAME})


def model_visible_agent_tool_description(name: str) -> str:
    """Return structural provider guidance without runtime control metadata."""

    if name == "workspace":
        return (
            "Run one opaque shell script in the current firing-private workspace. "
            "Use it for ordinary file inspection, editing, and execution; use "
            "write_file to register semantic outputs. The workspace is separate "
            "from the Registry's registered resource authority and network access "
            "is unavailable. A timeout or nonzero exit is recorded as an observed "
            "failed action and must be explicitly resolved by a later successful "
            "workspace action before an actor can complete.")

    descriptions = {
        "complete_interaction": (
            "Complete only after write_file has registered every intended output "
            "file required by the assignment. After any earlier timed-out or "
            "nonzero-exit workspace action, an actor must run a later successful "
            "workspace action before completion; a registered critic/reviewer may "
            "instead report the diagnostic when its operation policy allows it. "
            "The final write_file may precede this call in the same response when "
            "complete_interaction is the final ordered call."),
        "delegate_leaf": (
            "Open one depth-one, one-atomic-task subtask session annotation. "
            "Its calls, tools, files, and results execute under this parent "
            "firing. Redelegation is unavailable."),
        "query_environment_resources": (
            "Optionally inspect registered NumPy/SciPy environment information."),
        "query_kb": (
            "Use operation=search to find registered knowledge, or operation=read "
            "with one exact item_id to inspect the selected item. Only read "
            "records body access."),
        "query_registry_resources": (
            "Search or list bounded Registry metadata for resources authorized to "
            "this active firing. Start with summaries, then use request_resource, "
            "read_file, or search_text for an exact selected body. Optional "
            "view=history includes authorized superseded versions; bodies and "
            "workspace command output are never returned."),
        "read_action_output": (
            "Read stdout or stderr from one exact prior workspace agent_action/v2, "
            "using its exact locator when older output is needed. Do not rerun "
            "solely to display the same output; rerun when fresh execution is "
            "semantically required."),
        "read_file": (
            READ_FILE_MODEL_DESCRIPTION),
        "request_resource": (
            "Request read or owner-only edit access to one exact registered "
            "resource in this firing."),
        "search_text": (
            "Search literal UTF-8 text in one exact registered Located input or "
            "resource delivery. Read the listed summary first; use bounded search "
            "to locate relevant passages, then read original pages only when the "
            "task needs the complete evidence or surrounding context."),
        "write_file": (
            "Register a semantic output from exactly one source: supplied nonempty "
            "UTF-8 content, or one exact current Registry source_resource_ref. For "
            "a Registry source, query_registry_resources summaries first and copy "
            "the exact resource_id and resource_version_id from the selected current "
            "row; workspace bytes are not source authority. Supply a concise "
            "description covering the file's purpose and "
            "important result; it is a navigation summary, not a substitute for "
            "the file. A final complete_interaction call may follow in this same "
            "response."),
    }
    try:
        return descriptions[name]
    except KeyError as exc:
        raise ValueError("unknown agent tool description") from exc


__all__ = [
    "AGENT_TOOL_NAMES", "DELEGATE_LEAF_TOOL_NAME",
    "REQUEST_RESOURCE_TOOL_NAME", "TOOL_ARGUMENT_SCHEMAS",
    "IMMEDIATE_COMPLETION_GUIDANCE", "READ_FILE_MODEL_DESCRIPTION",
    "READ_FILE_UNREGISTERED_PATH_DETAIL",
    "READ_FILE_WORKSPACE_ROUTING_GUIDANCE", "AgentToolCatalog",
    "build_agent_tool_catalog", "derive_atomic_subtask_tools",
    "model_visible_agent_tool_description", "parse_agent_tool_catalog",
]
