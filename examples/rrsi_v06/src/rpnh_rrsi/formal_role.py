"""Registry/PetriNet-native multi-turn RRSI role sessions.

Each role session is one application occurrence represented by a small cyclic
Petri net.  Every ``model`` transition is a distinct lower-level firing and
therefore receives the existing one-use ``registered_llm/v1`` capability.
The following ``act`` transition validates and applies exactly one logical
RRSI tool action.  The role occurrence, source state, action journal, and
provider-visible messages remain typed Registry resources throughout.

This module is application code.  It does not extend AgentLoop, Registry, or
the Harness and it deliberately uses no content-derived identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft7Validator

from cpn.components.registered_host_llm import (
    EXECUTION_PROVENANCE_DOCUMENT,
    EXECUTION_PROVENANCE_SCHEMA,
    HOST_PROTOCOL,
    make_registered_llm_host_bindings,
    registered_host_execution_route,
    registered_host_llm_schema_data,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration,
    BindingContext,
    PNFragment,
    PlaceDeclaration,
    PortBinding,
    PortDeclaration,
    TransitionDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.run import OwnerInput, start_run


from .formal_execution import child_failure_refs, close_child, execute_child

SCHEMA_PREFIX = "application/rrsi_v06/formal_role"
ROLE_REQUEST = f"{SCHEMA_PREFIX}/request/v1"
ROLE_CONFIG = f"{SCHEMA_PREFIX}/config/v1"
ROLE_MODEL_CONFIG = f"{SCHEMA_PREFIX}/model_config/v1"
ROLE_STATE = f"{SCHEMA_PREFIX}/state/v1"
ROLE_RESPONSE = f"{SCHEMA_PREFIX}/response/v1"
ROLE_RESULT = f"{SCHEMA_PREFIX}/result/v1"
CONFORMANCE_PROFILE = "application_petri_conformance/v1"
ACTION_EVIDENCE_PROFILE = "application_role_state_action_journal/v1"

COMPONENT_KEY = f"{SCHEMA_PREFIX}/component/v1"
TERMINAL_KEY = f"{SCHEMA_PREFIX}/terminal/v1"
BUCKET_ID = "rrsi-formal-role-operations"


class FormalRoleInfrastructureError(RuntimeError):
    """A child dispatch/effect occurred but cannot be safely reconciled."""

ROLE_LIMITS = {
    "proposer": {"max_generations": 40, "max_edits": 80,
                 "max_digest_chars": 6000},
    "analyst": {"max_generations": 30, "max_edits": 0,
                "max_digest_chars": 6000},
    "digester": {"max_generations": 15, "max_edits": 0,
                 "max_digest_chars": 6000},
    "critic": {"max_generations": 3, "max_edits": 0,
               "max_digest_chars": 6000},
}


def _schema(schema_id: str, properties: Mapping[str, Any],
            required: Sequence[str]) -> dict[str, Any]:
    return {
        "$id": schema_id,
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


_ID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"}
_ROLE = {"enum": sorted(ROLE_LIMITS)}
_JSON_OBJECT = {"type": "object"}
_JSON_ARRAY = {"type": "array"}
_LIMITS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_generations": {"type": "integer", "minimum": 1, "maximum": 40},
        "max_edits": {"type": "integer", "minimum": 0, "maximum": 80},
        "max_digest_chars": {"const": 6000},
    },
    "required": ["max_generations", "max_edits", "max_digest_chars"],
}

SCHEMAS = {
    ROLE_REQUEST: _schema(ROLE_REQUEST, {
        "schema_version": {"const": "rrsi_v06/formal_role_request/v1"},
        "protocol_id": _ID,
        "round_id": _ID,
        "occurrence_id": _ID,
        "parent_action_ref": _ID,
        "role": _ROLE,
        "limits": _LIMITS,
        "input": _JSON_OBJECT,
    }, ("schema_version", "protocol_id", "round_id", "occurrence_id",
        "parent_action_ref", "role", "limits", "input")),
    ROLE_CONFIG: _schema(ROLE_CONFIG, {
        "role": _ROLE,
        "limits": _LIMITS,
    }, ("role", "limits")),
    ROLE_MODEL_CONFIG: _schema(ROLE_MODEL_CONFIG, {
        "role": _ROLE,
        "limits": _LIMITS,
        "provider_attempt_limit": {"const": 1},
        "resource_bounds": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "max_llm_attempts": {"const": 1},
                "max_tool_turns": {"const": 0},
            },
            "required": ["max_llm_attempts", "max_tool_turns"],
        },
    }, ("role", "limits", "provider_attempt_limit", "resource_bounds")),
    ROLE_STATE: _schema(ROLE_STATE, {
        "schema_version": {"const": "rrsi_v06/formal_role_state/v1"},
        "conformance_profile": {"const": CONFORMANCE_PROFILE},
        "action_evidence_profile": {"const": ACTION_EVIDENCE_PROFILE},
        "strict_agent_loop_conformance": {"const": False},
        "protocol_id": _ID,
        "round_id": _ID,
        "occurrence_id": _ID,
        "parent_action_ref": _ID,
        "role": _ROLE,
        "limits": _LIMITS,
        "generation": {"type": "integer", "minimum": 0, "maximum": 40},
        "successful_edits": {"type": "integer", "minimum": 0, "maximum": 80},
        "messages": _JSON_ARRAY,
        "files": _JSON_ARRAY,
        "traces": _JSON_ARRAY,
        "digests": _JSON_ARRAY,
        "actions": _JSON_ARRAY,
        "child_runs": _JSON_ARRAY,
        "input": _JSON_OBJECT,
    }, ("schema_version", "conformance_profile", "action_evidence_profile",
        "strict_agent_loop_conformance", "protocol_id", "round_id", "occurrence_id",
        "parent_action_ref", "role", "limits", "generation",
        "successful_edits", "messages", "files", "traces", "digests",
        "actions", "child_runs", "input")),
    ROLE_RESPONSE: _schema(ROLE_RESPONSE, {
        "schema_version": {"const": "rrsi_v06/formal_role_response/v1"},
        "state": _JSON_OBJECT,
        "response": _JSON_OBJECT,
    }, ("schema_version", "state", "response")),
    ROLE_RESULT: _schema(ROLE_RESULT, {
        "schema_version": {"const": "rrsi_v06/formal_role_result/v1"},
        "conformance_profile": {"const": CONFORMANCE_PROFILE},
        "action_evidence_profile": {"const": ACTION_EVIDENCE_PROFILE},
        "strict_agent_loop_conformance": {"const": False},
        "protocol_id": _ID,
        "round_id": _ID,
        "occurrence_id": _ID,
        "parent_action_ref": _ID,
        "role": _ROLE,
        "termination": {
            "enum": ["submitted", "method_exhausted", "application_invalid"],
        },
        "generation_count": {"type": "integer", "minimum": 1, "maximum": 40},
        "successful_edits": {"type": "integer", "minimum": 0, "maximum": 80},
        "payload": _JSON_OBJECT,
        "final_state": _JSON_OBJECT,
    }, ("schema_version", "conformance_profile", "action_evidence_profile",
        "strict_agent_loop_conformance", "protocol_id", "round_id", "occurrence_id",
        "parent_action_ref", "role", "termination", "generation_count",
        "successful_edits", "payload", "final_state")),
}


def _tool(name: str, description: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": dict(parameters),
        },
    }


def _closed(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


_PATH = {"type": "string", "minLength": 1, "maxLength": 200}
_TEXT = {"type": "string", "maxLength": 20000}
_NONEMPTY = {"type": "string", "minLength": 1, "maxLength": 20000}
_SEARCH_TEXT = {"type": "string", "minLength": 1, "maxLength": 256}

ROLE_TOOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "proposer": (
        _tool("rrsi_list_sources", "List the candidate source files.", _closed({})),
        _tool("rrsi_read_source", "Read one candidate source file.",
              _closed({"path": _PATH}, ("path",))),
        _tool("rrsi_edit_source", "Replace one exact occurrence in a source file.",
              _closed({"path": _PATH, "old": _TEXT, "new": _TEXT},
                      ("path", "old", "new"))),
        _tool("rrsi_create_source", "Create one new allowed source file.",
              _closed({"path": _PATH, "content": _TEXT},
                      ("path", "content"))),
        _tool("rrsi_submit_proposal", "Submit the completed proposal without another model call.",
              _closed({"summary": _NONEMPTY, "component": _NONEMPTY},
                      ("summary", "component"))),
    ),
    "analyst": (
        _tool("rrsi_digest_many", "Delegate up to eight selected traces to independent Digesters.",
              _closed({"requests": {
                  "type": "array", "minItems": 1, "maxItems": 8,
                  "items": _closed({
                      "task_id": _NONEMPTY,
                      "lens": {"enum": ["failure", "success", "contrast"]},
                      "questions": {"type": "array", "items": _NONEMPTY,
                                    "maxItems": 5},
                  }, ("task_id", "lens", "questions")),
              }}, ("requests",))),
        _tool("rrsi_submit_analysis", "Submit the three analysis groups.",
              _closed({
                  "failure_modes": {"type": "array", "items": _NONEMPTY, "maxItems": 16},
                  "success_patterns": {"type": "array", "items": _NONEMPTY, "maxItems": 16},
                  "contrasts": {"type": "array", "items": _NONEMPTY, "maxItems": 16},
              }, ("failure_modes", "success_patterns", "contrasts"))),
    ),
    "digester": (
        _tool("rrsi_read_trace", "Read selected lines from the one authorized trace.",
              _closed({
                  "from_line": {"type": "integer", "minimum": 1},
                  "to_line": {"type": "integer", "minimum": 1},
              }, ("from_line", "to_line"))),
        _tool("rrsi_search_trace", "Literal-search the one authorized trace.",
              _closed({"pattern": _SEARCH_TEXT}, ("pattern",))),
        _tool("rrsi_return_digest", "Return the final digest without another model call.",
              _closed({"digest": {"type": "string", "minLength": 1,
                                  "maxLength": 6000}}, ("digest",))),
    ),
    "critic": (
        _tool("rrsi_submit_critique", "Submit the review decision without another model call.",
              _closed({
                  "decision": {"enum": ["accepted", "rejected"]},
                  "reason": _NONEMPTY,
                  "component": _NONEMPTY,
              }, ("decision", "reason", "component"))),
    ),
}

TERMINAL_TOOLS = {
    "proposer": "rrsi_submit_proposal",
    "analyst": "rrsi_submit_analysis",
    "digester": "rrsi_return_digest",
    "critic": "rrsi_submit_critique",
}


def tool_catalog(role: str) -> list[dict[str, Any]]:
    try:
        return json.loads(canonical_json(list(ROLE_TOOLS[role])))
    except KeyError as exc:
        raise ValueError("unknown RRSI role") from exc


def _executor_key(role: str, stage: str) -> str:
    if role not in ROLE_LIMITS or stage not in {"init", "model", "action"}:
        raise ValueError("formal role executor identity is invalid")
    return f"{SCHEMA_PREFIX}/{role}/{stage}_executor/v1"


def _tool_schemas(role: str) -> dict[str, Mapping[str, Any]]:
    return {
        item["function"]["name"]: item["function"]["parameters"]
        for item in ROLE_TOOLS[role]
    }


def _normalized_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("source path must be nonempty text")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source path must be normalized and relative")
    return path.as_posix()


def _visible_text(value: object, *, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else canonical_json(value).decode("utf-8")
    if len(text) <= limit:
        return text
    half = (limit - len("\n...[middle truncated]...\n")) // 2
    return text[:half] + "\n...[middle truncated]...\n" + text[-half:]


def _analyst_visible_input(input_value: Mapping[str, Any]) -> dict[str, Any]:
    """Project Analyst metadata without exposing trace or response bodies."""
    visible = {
        key: input_value[key]
        for key in ("prior_mode_names", "history")
        if key in input_value
    }
    protocol = input_value.get("protocol")
    if isinstance(protocol, Mapping) and "protocol_id" in protocol:
        visible["protocol"] = {"protocol_id": protocol["protocol_id"]}
    manifest = input_value.get("evolve_manifest")
    if isinstance(manifest, Mapping):
        projected_manifest = {
            key: manifest[key]
            for key in ("manifest_id", "split")
            if key in manifest
        }
        tasks = manifest.get("tasks")
        if isinstance(tasks, list):
            projected_manifest["tasks"] = [
                {
                    key: row[key]
                    for key in ("task_id", "weight", "scorer")
                    if key in row
                }
                for row in tasks
                if isinstance(row, Mapping)
            ]
        visible["evolve_manifest"] = projected_manifest
    incumbent = input_value.get("incumbent")
    if isinstance(incumbent, Mapping):
        projected_incumbent = {
            key: incumbent[key]
            for key in ("split", "evaluation_id", "execution_id", "candidate_id")
            if key in incumbent
        }
        aggregate = incumbent.get("aggregate")
        if isinstance(aggregate, Mapping):
            projected_incumbent["aggregate"] = {
                key: aggregate[key]
                for key in (
                    "score", "cost", "expected_slots", "missing_slots",
                    "known_token_slots", "positive_token_slots",
                    "token_coverage", "known_token_total", "cost_comparable",
                )
                if key in aggregate
            }
        trials = incumbent.get("trials")
        if isinstance(trials, list):
            projected_incumbent["trials"] = [
                {
                    key: row[key]
                    for key in (
                        "task_id", "repetition", "reward", "weight",
                        "tokens", "trial_result_ref",
                    )
                    if key in row
                }
                for row in trials
                if isinstance(row, Mapping)
            ]
        visible["incumbent"] = projected_incumbent
    traces = input_value.get("traces")
    if isinstance(traces, list):
        visible["traces"] = [
            {
                key: row[key]
                for key in ("task_id", "group", "reward", "trace_ref")
                if key in row
            }
            for row in traces
            if isinstance(row, Mapping)
        ]
    return visible


def _initial_messages(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    role = request["role"]
    common = (
        "Use exactly one provided tool in every response. Multiple tool calls "
        "are rejected before any effect. Do not answer with ordinary text. "
        "A successful terminal tool ends this role immediately."
    )
    instructions = {
        "proposer": (
            "You are the RRSI Proposer. Inspect and edit only the declared source "
            "scope. You cannot run tests or shell commands. Make concrete source "
            "changes, then submit a concise proposal and component label."
        ),
        "analyst": (
            "You are the RRSI Analyst. You cannot read raw traces directly. Use "
            "digest_many for selected tasks, inspect returned digests, then submit "
            "failure modes, success patterns, and contrasts."
        ),
        "digester": (
            "You are a restricted RRSI Digester. Read or literal-search only the "
            "single selected trace, then return a digest no longer than 6000 characters."
        ),
        "critic": (
            "You are the RRSI Critic. Review only the supplied frozen candidate "
            "and whole diff. Submit accepted or rejected with a concrete reason."
        ),
    }[role]
    input_value = request["input"]
    model_input = (
        _analyst_visible_input(input_value)
        if role == "analyst"
        else input_value
    )
    return [
        {"role": "system", "content": instructions + " " + common},
        {"role": "user", "content": canonical_json(model_input).decode("utf-8")},
    ]


def _initial_state(request: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    if request["role"] != config["role"] or request["limits"] != config["limits"]:
        raise ValueError("role request differs from compiled role configuration")
    expected = ROLE_LIMITS[request["role"]]
    if request["limits"] != expected:
        raise ValueError("role request changed the fixed method limits")
    input_value = request["input"]
    files = input_value.get("files", [])
    traces = input_value.get("traces", [])
    if not isinstance(files, list) or not isinstance(traces, list):
        raise ValueError("role input files/traces must be arrays")
    normalized_files = []
    seen: set[str] = set()
    for row in files:
        if not isinstance(row, Mapping):
            raise ValueError("source member must be an object")
        path = _normalized_path(row.get("path"))
        if path in seen:
            raise ValueError("source input repeats a path")
        content, mode = row.get("content"), row.get("mode")
        if (not isinstance(content, str) or isinstance(mode, bool)
                or not isinstance(mode, int) or not 0 <= mode <= 0o777):
            raise ValueError("source member bytes/mode are invalid")
        seen.add(path)
        normalized_files.append({
            "path": path,
            "content": content,
            "base_content": content,
            "mode": mode,
            "purpose": row.get("purpose", "policy_source"),
            "operation": "unchanged",
        })
    normalized_traces = []
    trace_ids: set[str] = set()
    for row in traces:
        if (not isinstance(row, Mapping)
                or not isinstance(row.get("task_id"), str)
                or not isinstance(row.get("text"), str)):
            raise ValueError("trace input is malformed")
        if row["task_id"] in trace_ids:
            raise ValueError("trace table repeats a task_id")
        trace_ids.add(row["task_id"])
        normalized_traces.append(dict(row))
    if request["role"] == "digester":
        if len(normalized_traces) != 1:
            raise ValueError("Digester requires exactly one selected trace")
        selected_ref = input_value.get("selected_ref")
        authorized_ref = normalized_traces[0].get(
            "trace_ref", normalized_traces[0]["task_id"])
        if not isinstance(selected_ref, str) or selected_ref != authorized_ref:
            raise ValueError(
                "Digester selected_ref does not identify its authorized trace")
    return {
        "schema_version": "rrsi_v06/formal_role_state/v1",
        "conformance_profile": CONFORMANCE_PROFILE,
        "action_evidence_profile": ACTION_EVIDENCE_PROFILE,
        "strict_agent_loop_conformance": False,
        "protocol_id": request["protocol_id"],
        "round_id": request["round_id"],
        "occurrence_id": request["occurrence_id"],
        "parent_action_ref": request["parent_action_ref"],
        "role": request["role"],
        "limits": dict(request["limits"]),
        "generation": 0,
        "successful_edits": 0,
        "messages": _initial_messages(request),
        "files": normalized_files,
        "traces": normalized_traces,
        "digests": [],
        "actions": [],
        "child_runs": [],
        "input": dict(input_value),
    }


def _decode_tool_arguments(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > 65536:
        raise ValueError("tool arguments exceed the application limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("tool arguments repeat a JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("tool arguments are not bounded JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("tool arguments are not an object")
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError("tool arguments exceed the nesting limit")
        if isinstance(item, Mapping):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _response_document(response: bytes) -> dict[str, Any]:
    try:
        value = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("registered role response is not JSON") from exc
    if (not isinstance(value, dict)
            or value.get("protocol") != "llm_response_envelope/v1"
            or not isinstance(value.get("tool_calls", []), list)):
        raise ValueError("registered role response is not a canonical envelope")
    return value


def _append_invalid(state: dict[str, Any], response: Mapping[str, Any],
                    reason: str) -> None:
    state["actions"].append({
        "generation": state["generation"],
        "tool_call_id": None,
        "tool": None,
        "arguments": None,
        "status": "rejected_before_effect",
        "effect_applied": False,
        "result": {"error": reason},
    })
    state["messages"].append({
        "role": "assistant",
        "content": response.get("text") or "",
    })
    state["messages"].append({
        "role": "user",
        "content": "Action rejected before effect: " + reason
                   + ". Return exactly one valid tool call.",
    })


def _append_tool_result(state: dict[str, Any], response: Mapping[str, Any],
                        call: Mapping[str, Any], result: Mapping[str, Any],
                        *, status: str = "ok", effect_applied: bool = True) -> None:
    visible = _visible_text(result)
    try:
        journal_arguments: Any = _decode_tool_arguments(call["arguments"])
    except (TypeError, ValueError):
        journal_arguments = {
            "valid_json": False,
            "raw_text": call.get("arguments"),
        }
    state["actions"].append({
        "generation": state["generation"],
        "tool_call_id": call["id"],
        "tool": call["name"],
        "arguments": journal_arguments,
        "status": status,
        "effect_applied": effect_applied,
        "result": dict(result),
        "visible_text": visible,
    })
    state["messages"].append({
        "role": "assistant",
        "content": response.get("text") or "",
        "tool_calls": [{
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": call["arguments"],
            },
        }],
    })
    state["messages"].append({
        "role": "tool",
        "tool_call_id": call["id"],
        "content": visible,
    })


def _file_map(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["path"]: row for row in state["files"]}


def _proposer_action(state: dict[str, Any], name: str,
                     arguments: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    files = _file_map(state)
    if name == "rrsi_list_sources":
        return False, {"sources": [{
            "path": row["path"], "mode": row["mode"],
            "purpose": row["purpose"], "operation": row["operation"],
        } for row in state["files"]]}
    if name == "rrsi_read_source":
        path = _normalized_path(arguments["path"])
        if path not in files:
            raise ValueError("source path is not in the candidate")
        return False, {"path": path, "content": files[path]["content"],
                       "mode": files[path]["mode"]}
    if name in {"rrsi_edit_source", "rrsi_create_source"}:
        if state["successful_edits"] >= state["limits"]["max_edits"]:
            raise ValueError("successful source edit limit is exhausted")
        path = _normalized_path(arguments["path"])
        editable = set(state["input"].get("editable_paths", []))
        exts = tuple(state["input"].get("source_exts", [".py"]))
        if name == "rrsi_edit_source":
            if path not in editable or path not in files:
                raise ValueError("source edit is outside the editable scope")
            old, new = arguments["old"], arguments["new"]
            before = files[path]["content"]
            if not old or before.count(old) != 1:
                raise ValueError("source edit requires one exact nonempty match")
            after = before.replace(old, new, 1)
            files[path]["content"] = after
            files[path]["operation"] = "replace"
            state["successful_edits"] += 1
            return False, {
                "operation": "replace_unique", "path": path,
                "status": "ok", "bytes_changed": after != before,
                "mode": files[path]["mode"],
            }
        if path in files:
            raise ValueError("create_source does not overwrite an existing path")
        creatable = state["input"].get("creatable_paths")
        if (creatable is not None
                and (not isinstance(creatable, list) or path not in creatable)):
            raise ValueError("source create is outside the creatable scope")
        if not any(path.endswith(ext) for ext in exts):
            raise ValueError("create_source path has an unapproved extension")
        content = arguments["content"]
        mode = state["input"].get("create_mode", 0o644)
        row = {"path": path, "content": content, "base_content": None,
               "mode": mode, "purpose": "policy_source", "operation": "create"}
        state["files"].append(row)
        state["input"].setdefault("editable_paths", []).append(path)
        state["successful_edits"] += 1
        return False, {"operation": "create", "path": path, "status": "ok",
                       "bytes_changed": bool(content), "mode": mode}
    if name == "rrsi_submit_proposal":
        repair = bool(state["input"].get("repair"))
        if not repair and state["successful_edits"] == 0:
            raise ValueError("an initial proposal requires at least one successful edit")
        changed = [dict(row) for row in state["files"]
                   if row["operation"] != "unchanged"]
        if not changed and not repair:
            raise ValueError("proposal has no changed source member")
        return True, {
            "summary": arguments["summary"],
            "component": arguments["component"],
            "files": [dict(row) for row in state["files"]],
            "edit_report": {
                "successful_edit_count": state["successful_edits"],
                "changed_paths": [row["path"] for row in changed],
                "actions": list(state["actions"]),
            },
        }
    raise ValueError("unknown proposer tool")


DigestRunner = Callable[[Mapping[str, Any], int], Mapping[str, Any]]


@dataclass(slots=True)
class _RoleRuntime:
    result_sink: list[dict[str, Any]]
    digest_runner: DigestRunner | None
    config: dict[str, Any]


_RUNTIME_LOCK = RLock()
_RuntimeKey = tuple[str, str, str]
_RUNTIME_CONTEXTS: dict[_RuntimeKey, _RoleRuntime] = {}


def _runtime_key(value: Mapping[str, Any]) -> _RuntimeKey:
    fields = tuple(value.get(name) for name in (
        "protocol_id", "round_id", "occurrence_id"))
    if not all(isinstance(item, str) and item for item in fields):
        raise RuntimeError("formal role value lacks its scoped occurrence identity")
    return fields  # type: ignore[return-value]


def _analyst_action(state: dict[str, Any], name: str,
                    arguments: Mapping[str, Any],
                    digest_runner: DigestRunner | None) -> tuple[bool, dict[str, Any]]:
    if name == "rrsi_submit_analysis":
        return True, {
            **dict(arguments),
            "n_digests": len(state["digests"]),
            "digests": list(state["digests"]),
        }
    if name != "rrsi_digest_many":
        raise ValueError("unknown analyst tool")
    trace_table = {row["task_id"]: row for row in state["traces"]}
    rows = []
    for request_ordinal, item in enumerate(arguments["requests"][:8]):
        action_ref = (
            f"{state['occurrence_id']}:action:{state['generation']}")
        child_occurrence = (
            f"{state['occurrence_id']}:digest:"
            f"{state['generation']}:{request_ordinal}")
        task_id = str(item["task_id"])
        trace = trace_table.get(task_id)
        if trace is None:
            rows.append({
                "request_ordinal": request_ordinal,
                "occurrence_id": child_occurrence,
                "task_id": task_id,
                "lens": item["lens"],
                "status": "unknown_task",
                "error": "task_id is not in the selected trace table",
            })
            continue
        if digest_runner is None:
            raise FormalRoleInfrastructureError(
                "digest_many has no independent Digester runner")
        child_request = {
            "schema_version": "rrsi_v06/formal_role_request/v1",
            "protocol_id": state["protocol_id"],
            "round_id": state["round_id"],
            "occurrence_id": child_occurrence,
            "parent_action_ref": action_ref,
            "role": "digester",
            "limits": dict(ROLE_LIMITS["digester"]),
            "input": {
                "selected_ref": trace.get("trace_ref", task_id),
                "task_id": task_id,
                "lens": item["lens"],
                "questions": list(item["questions"]),
                "traces": [dict(trace)],
            },
        }
        try:
            envelope = dict(digest_runner(child_request, request_ordinal))
        except Exception as exc:
            # Dispatching a child is already an externally visible effect.
            # Any failure after that point requires B0 reconciliation and must
            # never be journaled as a model-correctable pre-effect rejection.
            raise FormalRoleInfrastructureError(
                "Digester child dispatch did not produce a usable result") from exc
        if envelope.get("schema_version") != "rrsi_v06/formal_role_envelope/v1":
            raise FormalRoleInfrastructureError(
                "Digester child returned an untyped envelope")
        child_run = envelope.get("run")
        if not isinstance(child_run, Mapping):
            raise FormalRoleInfrastructureError(
                "Digester child returned no run evidence")
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise FormalRoleInfrastructureError(
                "Digester child returned no typed result")
        expected_identity = {
            "protocol_id": child_request["protocol_id"],
            "round_id": child_request["round_id"],
            "occurrence_id": child_request["occurrence_id"],
            "parent_action_ref": child_request["parent_action_ref"],
            "role": "digester",
        }
        if (result.get("schema_version")
                != "rrsi_v06/formal_role_result/v1"
                or any(result.get(key) != value
                       for key, value in expected_identity.items())
                or result.get("termination")
                not in {"submitted", "method_exhausted"}):
            raise FormalRoleInfrastructureError(
                "Digester child result identity does not match its request")
        payload = result.get("payload")
        if not isinstance(payload, Mapping):
            raise FormalRoleInfrastructureError(
                "Digester child result has no payload")
        row = {
            "request_ordinal": request_ordinal,
            "occurrence_id": child_request["occurrence_id"],
            "task_id": task_id,
            "lens": item["lens"],
            "status": result["termination"],
            "digest": payload.get("digest"),
            "error": payload.get("error"),
            "child_run": child_run,
        }
        rows.append(row)
        state["child_runs"].append(child_run)
    state["digests"].extend(rows)
    return False, {"items": rows}


def _digester_action(state: dict[str, Any], name: str,
                     arguments: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if len(state["traces"]) != 1:
        raise ValueError("Digester requires one selected trace")
    trace = state["traces"][0]
    authorized_ref = trace.get("trace_ref", trace["task_id"])
    if state["input"].get("selected_ref") != authorized_ref:
        raise ValueError("Digester selected_ref no longer identifies its trace")
    text = trace["text"]
    if name == "rrsi_read_trace":
        lines = text.splitlines()
        start, end = arguments["from_line"], arguments["to_line"]
        if end < start:
            raise ValueError("trace line range is reversed")
        return False, {"task_id": trace["task_id"], "from_line": start,
                       "to_line": end, "text": "\n".join(lines[start - 1:end])}
    if name == "rrsi_search_trace":
        pattern = arguments["pattern"]
        matches = [{"line": index, "text": line} for index, line in
                   enumerate(text.splitlines(), 1) if pattern in line]
        return False, {"task_id": trace["task_id"], "matches": matches[:100]}
    if name == "rrsi_return_digest":
        return True, {
            "task_id": state["input"]["task_id"],
            "lens": state["input"]["lens"],
            "digest": arguments["digest"],
            "selected_ref": state["input"]["selected_ref"],
        }
    raise ValueError("unknown Digester tool")


def _critic_action(_state: dict[str, Any], name: str,
                   arguments: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if name != "rrsi_submit_critique":
        raise ValueError("unknown Critic tool")
    return True, dict(arguments)


def _fallback_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    role = state["role"]
    if role == "analyst":
        return {"failure_modes": [], "success_patterns": [], "contrasts": [],
                "n_digests": len(state["digests"]), "digests": list(state["digests"]),
                "error": "max_turns"}
    if role == "digester":
        return {"task_id": state["input"].get("task_id"),
                "lens": state["input"].get("lens"), "digest": None,
                "error": "max_turns"}
    if role == "critic":
        return {"decision": "rejected", "reason": "max_parse_attempts",
                "component": state["input"].get("component", "unknown")}
    return {"status": "no_proposal" if not state["input"].get("repair")
            else "critic_reject", "error": "max_turns"}


def _result(state: Mapping[str, Any], *, termination: str,
            payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "rrsi_v06/formal_role_result/v1",
        "conformance_profile": CONFORMANCE_PROFILE,
        "action_evidence_profile": ACTION_EVIDENCE_PROFILE,
        "strict_agent_loop_conformance": False,
        "protocol_id": state["protocol_id"],
        "round_id": state["round_id"],
        "occurrence_id": state["occurrence_id"],
        "parent_action_ref": state["parent_action_ref"],
        "role": state["role"],
        "termination": termination,
        "generation_count": state["generation"],
        "successful_edits": state["successful_edits"],
        "payload": dict(payload),
        "final_state": dict(state),
    }


def apply_response(response_value: Mapping[str, Any], *,
                   digest_runner: DigestRunner | None = None,
                   ) -> tuple[str, str, dict[str, Any]]:
    """Apply one model response and select ``continue`` or ``complete``."""
    if (not isinstance(response_value, Mapping)
            or response_value.get("schema_version")
            != "rrsi_v06/formal_role_response/v1"
            or not isinstance(response_value.get("state"), Mapping)
            or not isinstance(response_value.get("response"), Mapping)):
        raise ValueError("role action input is not a typed response")
    state = json.loads(canonical_json(dict(response_value["state"])))
    response = dict(response_value["response"])
    role = state.get("role")
    if role not in ROLE_LIMITS or state.get("limits") != ROLE_LIMITS[role]:
        raise ValueError("role state changed the fixed method limits")
    generation = state.get("generation")
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or not 0 <= generation < ROLE_LIMITS[role]["max_generations"]):
        raise ValueError("role state generation is outside its fixed limit")
    successful_edits = state.get("successful_edits")
    if (isinstance(successful_edits, bool)
            or not isinstance(successful_edits, int)
            or not 0 <= successful_edits <= ROLE_LIMITS[role]["max_edits"]):
        raise ValueError("role state edit count is outside its fixed limit")
    state["generation"] += 1
    calls = response.get("tool_calls", [])
    limit = state["limits"]["max_generations"]
    if response.get("finish_reason") == "length":
        _append_invalid(state, response, "response_was_truncated")
        if state["generation"] >= limit:
            return "complete", "result", _result(
                state, termination="method_exhausted",
                payload=_fallback_payload(state))
        return "continue", "state_out", state
    if not isinstance(calls, list) or len(calls) != 1:
        _append_invalid(state, response,
                        "exactly_one_logical_tool_action_required")
        if state["generation"] >= limit:
            return "complete", "result", _result(
                state, termination="method_exhausted",
                payload=_fallback_payload(state))
        return "continue", "state_out", state
    call = calls[0]
    if (not isinstance(call, Mapping)
            or set(call) != {"id", "name", "arguments"}
            or not all(isinstance(call.get(key), str)
                       for key in ("id", "name", "arguments"))):
        _append_invalid(state, response, "tool_call_shape_invalid")
        if state["generation"] >= limit:
            return "complete", "result", _result(
                state, termination="method_exhausted",
                payload=_fallback_payload(state))
        return "continue", "state_out", state
    schemas = _tool_schemas(state["role"])
    if call["name"] not in schemas:
        _append_invalid(state, response, "tool_not_in_role_catalog")
        if state["generation"] >= limit:
            return "complete", "result", _result(
                state, termination="method_exhausted",
                payload=_fallback_payload(state))
        return "continue", "state_out", state
    try:
        arguments = _decode_tool_arguments(call["arguments"])
        Draft7Validator(schemas[call["name"]]).validate(arguments)
        if state["role"] == "proposer":
            terminal, tool_result = _proposer_action(
                state, call["name"], arguments)
        elif state["role"] == "analyst":
            terminal, tool_result = _analyst_action(
                state, call["name"], arguments, digest_runner)
        elif state["role"] == "digester":
            terminal, tool_result = _digester_action(
                state, call["name"], arguments)
        else:
            terminal, tool_result = _critic_action(
                state, call["name"], arguments)
    except Exception as exc:
        # Known application validation failures are model-correctable.  A
        # missing delegate runner or malformed child envelope is infrastructure
        # failure and must not be converted to an ordinary method rejection.
        if isinstance(exc, FormalRoleInfrastructureError):
            raise
        _append_tool_result(
            state, response, call, {"error": str(exc)},
            status="rejected_before_effect", effect_applied=False)
        if state["generation"] >= limit:
            return "complete", "result", _result(
                state, termination="method_exhausted",
                payload=_fallback_payload(state))
        return "continue", "state_out", state
    _append_tool_result(state, response, call, tool_result)
    if terminal:
        return "complete", "result", _result(
            state, termination="submitted", payload=tool_result)
    if state["generation"] >= limit:
        return "complete", "result", _result(
            state, termination="method_exhausted",
            payload=_fallback_payload(state))
    return "continue", "state_out", state


@dataclass(frozen=True, slots=True)
class FormalRoleConfig:
    role: str

    def __post_init__(self) -> None:
        if self.role not in ROLE_LIMITS:
            raise ValueError("unknown formal role")

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "limits": dict(ROLE_LIMITS[self.role])}


def _lower_role(_config: Mapping[str, Any], context: BindingContext) -> PNFragment:
    places = (
        PlaceDeclaration("request", ROLE_REQUEST, capacity=1),
        PlaceDeclaration("state", ROLE_STATE, capacity=1),
        PlaceDeclaration("response", ROLE_RESPONSE, capacity=1),
        PlaceDeclaration("result", ROLE_RESULT, capacity=1),
    )
    transitions = (
        TransitionDeclaration("init", "init"),
        TransitionDeclaration("model", "model"),
        TransitionDeclaration("act", "act"),
    )
    arcs = (
        ArcDeclaration("request", "init", "input"),
        ArcDeclaration("state", "init", "output", mode="produce", outcome="complete"),
        ArcDeclaration("state", "model", "input"),
        ArcDeclaration("response", "model", "output", mode="produce", outcome="complete"),
        ArcDeclaration("response", "act", "input"),
        ArcDeclaration("state", "act", "output", mode="produce", outcome="continue"),
        ArcDeclaration("result", "act", "output", mode="produce", outcome="complete"),
    )
    internal_ports = (
        PortDeclaration("state_out", "output", ROLE_STATE),
        PortDeclaration("state_in", "input", ROLE_STATE),
        PortDeclaration("response_out", "output", ROLE_RESPONSE),
        PortDeclaration("response_in", "input", ROLE_RESPONSE),
    )
    internal_bindings = (
        PortBinding("state_out", "state"),
        PortBinding("state_in", "state"),
        PortBinding("response_out", "response"),
        PortBinding("response_in", "response"),
    )
    return PNFragment(
        places=places,
        transitions=transitions,
        arcs=arcs,
        ports=tuple(PortBinding(port.name, port.name) for port in context.ports),
        operations=context.operations,
        internal_ports=internal_ports,
        internal_bindings=internal_bindings,
    )


def _input_value(execution) -> dict[str, Any]:
    if len(execution.operation.inputs) != 1:
        raise ValueError("formal role operation requires one input")
    value = json.loads(execution.operation.inputs[0].artifact.payload)
    if not isinstance(value, dict):
        raise ValueError("formal role input is not an object")
    return value


def _publish(output_name: str, value: Mapping[str, Any], outcome: str,
             execution, gateway, result_sink) -> OperationExecutionResult:
    schemas = {"state_out": ROLE_STATE, "response_out": ROLE_RESPONSE,
               "result": ROLE_RESULT}
    schema = schemas[output_name]
    ports = tuple(port for port in execution.operation.spec.output_ports
                  if port.content_schema_id == schema)
    if len(ports) != 1:
        raise ValueError("formal role output schema is ambiguous")
    bindings = tuple(item for item in
                     execution.operation.operation_binding.output_port_bindings
                     if item.port_id == ports[0].port_id)
    if len(bindings) != 1:
        raise ValueError("formal role output binding is ambiguous")
    binding = bindings[0]
    context = execution.operation.canonical.context
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(dict(value)),
        media_type="application/json",
        content_schema_ref=schema,
        summary="RRSI formal role state",
        lifetime_ref=context.invocation_ref,
        descriptors={"output_outcome_id": outcome,
                     "output_port_id": binding.port_id,
                     "place": binding.place},
        idempotency_key=("rrsi-formal-role:"
                         + str(execution.operation_execution_lease_ref.version_id)
                         + ":" + output_name),
    ))
    artifact = gateway.verify_resource(execution.operation.canonical, ref)
    if output_name == "result":
        result_sink.append({
            "ref": {"resource_id": str(ref.resource_id),
                    "resource_version_id": str(ref.resource_version_id)},
            "value": json.loads(canonical_json(dict(value))),
        })
    return OperationExecutionResult(outputs=(artifact,), selected_outcome_id=outcome)


def _runtime_for(value: Mapping[str, Any]) -> _RoleRuntime:
    state = value.get("state") if isinstance(value.get("state"), Mapping) else value
    if not isinstance(state, Mapping):
        raise RuntimeError("formal role value lacks its scoped occurrence identity")
    key = _runtime_key(state)
    with _RUNTIME_LOCK:
        runtime = _RUNTIME_CONTEXTS.get(key)
    if runtime is None:
        raise RuntimeError("formal role runtime context is not active")
    return runtime


def _formal_init_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    value = _input_value(execution)
    runtime = _runtime_for(value)
    declared = runtime.config
    return _publish("state_out", _initial_state(value, declared),
                    "complete", execution, gateway, runtime.result_sink)


def _formal_model_executor(*, execution, gateway, resources, host_context):
    del resources
    state = _input_value(execution)
    runtime = _runtime_for(state)
    registered = getattr(host_context, "registered_llm", None)
    if registered is None:
        raise RuntimeError("formal role model firing lacks registered_llm/v1")
    response = registered.request({
        "protocol": HOST_PROTOCOL,
        "messages": state["messages"],
        "tools": tool_catalog(state["role"]),
        "tool_choice": "auto",
        "placeholders": [],
    })
    value = {"schema_version": "rrsi_v06/formal_role_response/v1",
             "state": state, "response": _response_document(response)}
    return _publish("response_out", value, "complete", execution, gateway,
                    runtime.result_sink)


def _formal_action_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    response = _input_value(execution)
    runtime = _runtime_for(response)
    outcome, output, value = apply_response(
        response, digest_runner=runtime.digest_runner)
    return _publish(output, value, outcome, execution, gateway,
                    runtime.result_sink)


def role_registration(config: FormalRoleConfig, *, result_sink: list,
                      digest_runner: DigestRunner | None = None) -> Registration:
    registration = Registration()
    for schema_id, document in SCHEMAS.items():
        registration.register_schema(schema_id, document)
    registration.register_schema(EXECUTION_PROVENANCE_SCHEMA,
                                 EXECUTION_PROVENANCE_DOCUMENT)
    registration.register_component(
        COMPONENT_KEY, _lower_role,
        identity={"implementation_id": "rrsi_v06.formal_role", "revision": "v1"},
        contracts={"config_schema": ROLE_CONFIG})
    del result_sink, digest_runner
    for role in sorted(ROLE_LIMITS):
        registration.register_executor(
            _executor_key(role, "init"), _formal_init_executor,
            identity={"implementation_id": f"rrsi_v06.formal_role.{role}.init",
                      "revision": "v1"},
            contracts={"transport": "deterministic", "input_ports": None,
                       "output_ports": None, "config_schema": ROLE_CONFIG})
        registration.register_executor(
            _executor_key(role, "model"), _formal_model_executor,
            identity={"implementation_id": f"rrsi_v06.formal_role.{role}.model",
                      "revision": "v1"},
            contracts={
                "transport": "llm", "input_ports": None,
                "output_ports": None, "config_schema": ROLE_MODEL_CONFIG,
                "host_protocols": [HOST_PROTOCOL],
                "resource_read_contracts": [ResourceReadContract(
                    metadata_only=False,
                    context_origins=("petri_operation",),
                    origin_kinds=("provider_request",),
                    require_producer_invocation=True,
                    require_provenance_binding=True).to_dict()],
                "provider_request_schema": "runtime/llm_request_envelope/v1",
            })
        registration.register_executor(
            _executor_key(role, "action"), _formal_action_executor,
            identity={"implementation_id": f"rrsi_v06.formal_role.{role}.action",
                      "revision": "v1"},
            contracts={"transport": "deterministic", "input_ports": None,
                       "output_ports": None, "config_schema": ROLE_CONFIG})
    # RPNH's trusted operation inventory is process-wide.  Every independent
    # role run therefore binds the same complete application inventory, while
    # each compiled model operation and provider catalog still expose only its
    # role-specific subset.
    declarations_by_name = {
        declaration["function"]["name"]: (role, declaration)
        for role, declarations in ROLE_TOOLS.items()
        for declaration in declarations
    }
    for name, (tool_role, declaration) in sorted(declarations_by_name.items()):
        registration.register_tool(
            f"{SCHEMA_PREFIX}/tool/{name}/v1", dict,
            identity={"implementation_id": f"rrsi_v06.formal_role.{name}",
                      "revision": "v1"},
            contracts={"provider_declaration": declaration,
                       "role": tool_role})
    registration.register_tool(
        TERMINAL_KEY, dict,
        identity={"implementation_id": "rrsi_v06.formal_role.terminal",
                  "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def build_role_module(config: FormalRoleConfig) -> ModuleDeclaration:
    declared = config.to_dict()
    tools = [f"{SCHEMA_PREFIX}/tool/{item['function']['name']}/v1"
             for item in ROLE_TOOLS[config.role]]
    budget = {"bucket_id": BUCKET_ID, "budget_scope": "module",
              "finalization_scope": None}

    def operation(name: str, executor: str, source: str, outputs: Sequence[str],
                  outcomes: Sequence[tuple[str, str]], *, model: bool = False):
        return {
            "name": name,
            "executor": executor,
            "inputs": [source],
            "outputs": list(outputs),
            "tools": tools if model else [],
            "config": ({**declared, "provider_attempt_limit": 1,
                        "resource_bounds": {"max_llm_attempts": 1,
                                            "max_tool_turns": 0}}
                       if model else declared),
            "request_port": source if model else None,
            "budget_binding": budget,
            "outcomes": [{"name": outcome, "products": [{"port": port}]}
                         for outcome, port in outcomes],
        }

    terminal = {
        "key": TERMINAL_KEY,
        "source": {"component": "role", "port": "result"},
        "operation": "act",
        "outcome": "complete",
        "config": {"run_outcome": "complete"},
    }
    maximum_operations = 2 * ROLE_LIMITS[config.role]["max_generations"] + 2
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": "RRSIV06FormalRoleV1" + config.role.title(),
        "components": [{
            "name": "role", "key": COMPONENT_KEY,
            "config_schema": ROLE_CONFIG, "config": declared,
            "ports": [
                {"name": "request", "direction": "input", "schema": ROLE_REQUEST},
                {"name": "result", "direction": "output", "schema": ROLE_RESULT},
            ],
            "operations": [
                operation("init", _executor_key(config.role, "init"),
                          "request", ("state_out",),
                          (("complete", "state_out"),)),
                operation("model", _executor_key(config.role, "model"),
                          "state_in", ("response_out",),
                          (("complete", "response_out"),), model=True),
                operation("act", _executor_key(config.role, "action"),
                          "response_in",
                          ("state_out", "result"),
                          (("continue", "state_out"),
                           ("complete", "result"))),
            ],
        }],
        "links": [],
        "entry": {"request": {"component": "role", "port": "request"}},
        "exit": {"result": {"component": "role", "port": "result"}},
        "terminal": terminal,
        "required_schemas": list(SCHEMAS),
        "budgets": {},
        "budget_buckets": [{**budget, "max_attempts": maximum_operations}],
    })


def role_catalog() -> SchemaCatalog:
    schemas, types = registered_host_llm_schema_data()
    return SchemaCatalog(schemas=schemas, types=types)


def run_role_session(*, run_dir: Path, request: Mapping[str, Any], selection,
                     llm_input_port=None, interruption_requested=None,
                     digest_runner: DigestRunner | None = None) -> dict[str, Any]:
    """Execute one independent role Registry to terminal completion."""
    from cpn.llm_adapters.config import LLMExecutionSelection

    if not isinstance(selection, LLMExecutionSelection):
        raise TypeError("formal role requires LLMExecutionSelection")
    try:
        Draft7Validator(SCHEMAS[ROLE_REQUEST]).validate(dict(request))
        role = request.get("role")
        config = FormalRoleConfig(role)
        _initial_state(dict(request), config.to_dict())
    except Exception as exc:
        raise ValueError(
            "formal role request failed pre-dispatch validation") from exc
    destination = run_dir.resolve()
    if destination.exists():
        raise ValueError("formal role run requires an absent run directory")
    module = build_role_module(config)
    sink: list[dict[str, Any]] = []
    occurrence_id = request.get("occurrence_id")
    if not isinstance(occurrence_id, str):
        raise ValueError("formal role request lacks an occurrence_id")
    runtime_key = _runtime_key(request)
    with _RUNTIME_LOCK:
        if runtime_key in _RUNTIME_CONTEXTS:
            raise ValueError("formal role occurrence is already active")
        _RUNTIME_CONTEXTS[runtime_key] = _RoleRuntime(
            sink, digest_runner, config.to_dict())
    created_port = False
    event_loop = None
    owner = None
    primary_error = None
    try:
        registration = role_registration(
            config, result_sink=sink, digest_runner=digest_runner)
        owner_input = OwnerInput(
            ROLE_REQUEST, canonical_json(dict(request)),
            "RRSI formal role request")
        bindings = make_registered_llm_host_bindings(
            selection.input_target,
            provider_backend_config=registered_host_execution_route(selection),
            provider_backend_schema_ref=EXECUTION_PROVENANCE_SCHEMA,
            transport_contract={
                "interaction_protocol_ref": "llm_request_envelope/v1",
                "response_adapter_ref": "llm_response_envelope/v1",
            },
            prompt={"messages": [{"role": "system",
                                  "content": "RRSI formal role loop"}]},
            tool_catalog={"tools": tool_catalog(role)},
        )
        if llm_input_port is None:
            from cpn.llm_adapters import build_llm_input_port
            llm_input_port = build_llm_input_port(
                selection, destination_run_root=destination)
            created_port = True
        maximum = 2 * ROLE_LIMITS[role]["max_generations"] + 2
        owner = start_run(
            module, registration, run_dir=destination,
            task_input=owner_input, entry_inputs={"request": owner_input},
            budgets=ModuleBudgetDeclaration(
                tuple(module.to_dict()["budget_buckets"]),
                ("rpnh/module_declaration/v1",), maximum, 0, maximum, 0),
            model_condition=selection.input_target.model_condition,
            owner_statement=f"Execute one independent RRSI {role} role",
            command_id=f"rrsi-v06:formal-role:{request['occurrence_id']}:fresh",
            catalog=role_catalog(), host_execution_bindings=bindings,
        )
        event_loop = OwnerEventLoop(owner, destination / "owner.sock")
        harness_result = execute_child(
            owner=owner, event_loop=event_loop, llm_input_port=llm_input_port,
            interruption_requested=interruption_requested)
        if harness_result.stop_reason != "terminal":
            raise RuntimeError(
                f"formal role did not reach terminal: {harness_result.stop_reason}")
        if len(harness_result.goal_resource_refs) != 1:
            raise RuntimeError("formal role terminal lacks one result resource")
        expected = {
            "resource_id": str(harness_result.goal_resource_refs[0].resource_id),
            "resource_version_id": str(
                harness_result.goal_resource_refs[0].resource_version_id),
        }
        matches = [row for row in sink if row["ref"] == expected]
        if len(matches) != 1:
            raise RuntimeError("formal role sink differs from terminal Registry output")
        return {
            "schema_version": "rrsi_v06/formal_role_envelope/v1",
            "run": {
                "run_dir": str(destination),
                "task_ref": {
                    "entity_type": owner.identity.task_ref.entity_type,
                    "logical_id": str(owner.identity.task_ref.entity_id),
                    "version_id": str(owner.identity.task_ref.version_id),
                },
                "run_ref": {
                    "entity_type": owner.identity.run_ref.entity_type,
                    "logical_id": str(owner.identity.run_ref.entity_id),
                    "version_id": str(owner.identity.run_ref.version_id),
                },
                "result_resource_ref": expected,
                "terminal_evidence_version_id": str(
                    harness_result.terminal_evidence_ref.version_id),
                "transition_trace": [item.transition_id for item in
                                     harness_result.operation_execution_trace],
            },
            "result": matches[0]["value"],
        }
    except BaseException as exc:
        primary_error = exc
        child_failure_refs(exc, owner)
        raise
    finally:
        try:
            close_child(owner=owner, event_loop=event_loop,
                        input_port=llm_input_port, created_port=created_port,
                        primary_error=primary_error)
        finally:
            with _RUNTIME_LOCK:
                _RUNTIME_CONTEXTS.pop(runtime_key, None)



__all__ = (
    "ACTION_EVIDENCE_PROFILE", "CONFORMANCE_PROFILE", "FormalRoleConfig",
    "ROLE_LIMITS", "ROLE_REQUEST", "ROLE_RESULT",
    "ROLE_STATE", "ROLE_TOOLS", "SCHEMAS", "apply_response",
    "build_role_module", "role_catalog", "role_registration",
    "run_role_session", "tool_catalog",
)
