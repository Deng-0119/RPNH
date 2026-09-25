"""AgentLoop tool-call and schema validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from .models import stable_action_id, stable_malformed_action_id
from .tool_catalog import TOOL_ARGUMENT_SCHEMAS, _unique_object_pairs


@dataclass(frozen=True, slots=True)
class ValidatedAgentToolAction:
    action_id: str
    loop_id: str
    turn_sequence: int
    tool_call_id: str
    tool_name: str
    raw_arguments: str
    arguments: Mapping[str, Any]
    expected_revision: int


@dataclass(frozen=True, slots=True)
class AgentToolSyntaxError:
    code: str
    detail: str
    action_id: str
    tool_call_id: str | None
    tool_call_ordinal: int
    tool_name: str | None
    raw_arguments: str | None


def validate_agent_tool_call(
    *, loop_id: str, turn_sequence: int, expected_revision: int,
    tool_call_id: str, tool_name: str, raw_arguments: str,
    tool_argument_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> ValidatedAgentToolAction | AgentToolSyntaxError:
    action_id = stable_action_id(loop_id, turn_sequence, tool_call_id)
    if not isinstance(raw_arguments, str):
        return AgentToolSyntaxError(
            "arguments_not_text", "tool-call arguments are not JSON text",
            action_id, tool_call_id, 0, str(tool_name),
            None)
    schemas = (
        TOOL_ARGUMENT_SCHEMAS
        if tool_argument_schemas is None else tool_argument_schemas)
    if tool_name not in schemas:
        return AgentToolSyntaxError(
            "tool_not_permitted", "tool name is outside the agent catalog",
            action_id, tool_call_id, 0, str(tool_name), raw_arguments)
    try:
        arguments = _decode_unique_object(raw_arguments)
        _validate_schema(
            arguments, schemas[tool_name], path="$arguments")
    except (ValueError, TypeError) as exc:
        return AgentToolSyntaxError(
            "arguments_invalid", str(exc), action_id,
            tool_call_id, 0, tool_name, raw_arguments)
    return ValidatedAgentToolAction(
        action_id=action_id, loop_id=loop_id, turn_sequence=turn_sequence,
        tool_call_id=tool_call_id, tool_name=tool_name,
        raw_arguments=raw_arguments, arguments=arguments,
        expected_revision=expected_revision,
    )


def workspace_execution_mode(arguments: Mapping[str, Any]) -> str:
    """Return the closed workspace execution mode with its sync default."""

    if not isinstance(arguments, Mapping):
        raise TypeError("workspace execution mode requires argument metadata")
    mode = arguments.get("execution_mode", "sync")
    if mode not in {"sync", "monitored"}:
        raise ValueError("workspace execution mode is not closed")
    return str(mode)


def validate_agent_tool_observation(
    *, loop_id: str, turn_sequence: int, expected_revision: int,
    observation: Any,
    tool_argument_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> ValidatedAgentToolAction | AgentToolSyntaxError:
    """Validate one preserved tool-call position without affecting siblings."""
    from cpn.rpnh.response_protocol import LLMToolCallObservation

    if not isinstance(observation, LLMToolCallObservation):
        raise TypeError("expected LLMToolCallObservation")
    action_id = (
        stable_action_id(loop_id, turn_sequence, observation.action_identity_key)
        if observation.action_identity_kind == "tool_call_id"
        else stable_malformed_action_id(
            loop_id, turn_sequence, observation.tool_call_ordinal))
    if observation.syntax_errors:
        return AgentToolSyntaxError(
            code="provider_tool_call_invalid",
            detail=",".join(observation.syntax_errors), action_id=action_id,
            tool_call_id=observation.tool_call_id,
            tool_call_ordinal=observation.tool_call_ordinal,
            tool_name=observation.tool_name,
            raw_arguments=observation.raw_arguments)
    if (observation.tool_call_id is None
            or observation.tool_name is None
            or observation.raw_arguments is None):
        raise RuntimeError("syntax-clean tool-call observation lacks fields")
    result = validate_agent_tool_call(
        loop_id=loop_id, turn_sequence=turn_sequence,
        expected_revision=expected_revision,
        tool_call_id=observation.tool_call_id,
        tool_name=observation.tool_name,
        raw_arguments=observation.raw_arguments,
        tool_argument_schemas=tool_argument_schemas)
    if isinstance(result, AgentToolSyntaxError):
        return AgentToolSyntaxError(
            result.code, result.detail, result.action_id,
            observation.tool_call_id, observation.tool_call_ordinal,
            observation.tool_name, observation.raw_arguments)
    return result


def _decode_unique_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError("arguments are not one JSON document") from exc
    if not isinstance(value, dict):
        raise TypeError("arguments must be one JSON object")
    return value


def _validate_schema(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    if not isinstance(schema, Mapping):
        raise TypeError(f"{path} uses a malformed catalog schema")
    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        raise TypeError(f"{path} uses a malformed catalog schema") from exc
    errors = sorted(
        Draft7Validator(schema).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message),
    )
    if errors:
        error = errors[0]
        suffix = "".join(f"[{part!r}]" for part in error.absolute_path)
        raise ValueError(f"{path}{suffix}: {error.message}")


__all__ = [
    "AgentToolSyntaxError", "ValidatedAgentToolAction",
    "validate_agent_tool_call", "validate_agent_tool_observation",
    "workspace_execution_mode",
]
