"""Pure model-visible context reduction for the current AgentLoop lane."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from typing import AbstractSet, Any, Mapping, Sequence

from cpn.components.request_protocol import (
    validate_llm_request_message_history,
)

# Includes the assistant call frame, all result frames, JSON escaping and IDs.
TOOL_BATCH_BYTE_LIMIT = 40_000


CONTEXT_CHECKPOINT_PROMPT = (
    "You are performing a CONTEXT CHECKPOINT COMPACTION for the same current "
    "task. Produce a forward continuation summary for the next model context; "
    "do not retrospectively reopen or retell the whole transcript.\n"
    "Prioritize:\n"
    "- The current objective and controlling task constraints\n"
    "- Current state, progress, and decisions that still govern the work\n"
    "- Important registered artifacts and exact Registry references/locators\n"
    "- Unresolved blockers or uncertainties\n"
    "- The immediate next actions needed to continue\n"
    "The next context session may retain a bounded tail of recent complete "
    "messages after your summary. Summarize the full governing state anyway, "
    "while compressing early history most aggressively.\n"
    "Treat early, completed, or non-priority history as supplemental: refer to "
    "its exact Registry locator when useful instead of expanding it into the "
    "forward summary. Be concise and preserve only material needed to continue "
    "the current task. Return one nonempty plain-text summary in the assistant "
    "message. Do not call tools, emit a JSON wrapper, or leave the assistant "
    "content empty."
)


@dataclass(frozen=True, slots=True)
class ContextReductionSettings:
    """Always-available deterministic model-visible reduction settings."""

    checkpoint_prompt: str = CONTEXT_CHECKPOINT_PROMPT
    tool_output_byte_limit: int = 10_000
    retained_history_token_limit: int = 20_000

    def __post_init__(self) -> None:
        if (not isinstance(self.checkpoint_prompt, str)
                or not self.checkpoint_prompt.strip()
                or self.checkpoint_prompt != self.checkpoint_prompt.strip()):
            raise ValueError("context checkpoint prompt must be nonempty text")
        if (isinstance(self.tool_output_byte_limit, bool)
                or not isinstance(self.tool_output_byte_limit, int)
                or self.tool_output_byte_limit < 128):
            raise ValueError("tool output byte limit is too small")
        if (isinstance(self.retained_history_token_limit, bool)
                or not isinstance(self.retained_history_token_limit, int)
                or self.retained_history_token_limit < 1):
            raise ValueError("retained history token limit must be positive")


@dataclass(frozen=True, slots=True)
class ContextPressurePolicy:
    """Optional pressure trigger available only for known model capacity."""

    context_window_tokens: int
    reserved_output_tokens: int = 0
    trigger_ratio: float = 0.90

    def __post_init__(self) -> None:
        if (isinstance(self.context_window_tokens, bool)
                or not isinstance(self.context_window_tokens, int)
                or self.context_window_tokens < 1):
            raise ValueError("context window must be a positive token count")
        if (isinstance(self.reserved_output_tokens, bool)
                or not isinstance(self.reserved_output_tokens, int)
                or self.reserved_output_tokens < 0):
            raise ValueError("reserved output tokens must be nonnegative")
        if (isinstance(self.trigger_ratio, bool)
                or not isinstance(self.trigger_ratio, (int, float))
                or not 0.0 < float(self.trigger_ratio) < 1.0):
            raise ValueError(
                "context pressure trigger ratio must be between zero and one")

    @property
    def trigger_tokens(self) -> int:
        exact_ratio = Fraction(str(self.trigger_ratio))
        ratio_boundary = (
            math.floor(self.context_window_tokens * exact_ratio) + 1)
        output_boundary = max(
            1, self.context_window_tokens - self.reserved_output_tokens)
        return min(ratio_boundary, output_boundary)


def approximate_tokens(payload: bytes | str) -> int:
    """Tokenizer-independent fallback estimate: UTF-8 bytes divided by four."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise TypeError("token approximation requires bytes or text")
    return math.ceil(len(raw) / 4)


def projected_tokens(
        payload: bytes, *, previous_request_bytes: int | None = None,
        previous_input_tokens: int | None = None,
) -> int:
    """Project from existing provider usage when both prior facts exist."""
    if not isinstance(payload, bytes):
        raise TypeError("projected request usage requires bytes")
    if (isinstance(previous_request_bytes, int)
            and not isinstance(previous_request_bytes, bool)
            and previous_request_bytes >= 0
            and isinstance(previous_input_tokens, int)
            and not isinstance(previous_input_tokens, bool)
            and previous_input_tokens >= 0):
        return max(0, math.ceil(
            previous_input_tokens
            + (len(payload) - previous_request_bytes) / 4))
    return approximate_tokens(payload)


def should_compact(
        policy: ContextPressurePolicy, payload: bytes, *,
        previous_request_bytes: int | None = None,
        previous_input_tokens: int | None = None,
) -> bool:
    if not isinstance(policy, ContextPressurePolicy):
        raise TypeError("context pressure decision requires a typed policy")
    return projected_tokens(
        payload,
        previous_request_bytes=previous_request_bytes,
        previous_input_tokens=previous_input_tokens,
    ) >= policy.trigger_tokens


def _utf8_prefix(raw: bytes, size: int) -> bytes:
    candidate = raw[:max(0, size)]
    while candidate:
        try:
            candidate.decode("utf-8")
            return candidate
        except UnicodeDecodeError:
            candidate = candidate[:-1]
    return b""


def _utf8_suffix(raw: bytes, size: int) -> bytes:
    candidate = raw[max(0, len(raw) - max(0, size)):]
    while candidate:
        try:
            candidate.decode("utf-8")
            return candidate
        except UnicodeDecodeError:
            candidate = candidate[1:]
    return b""


def reduce_tool_output(content: str, *, byte_limit: int) -> str:
    """Keep a UTF-8 prefix and suffix with an explicit omitted-byte marker."""
    if not isinstance(content, str):
        raise TypeError("tool output reduction requires text")
    if (isinstance(byte_limit, bool) or not isinstance(byte_limit, int)
            or byte_limit < 128):
        raise ValueError("tool output byte limit is too small")
    raw = content.encode("utf-8")
    if len(raw) <= byte_limit:
        return content
    omitted = len(raw)
    marker = b""
    prefix = suffix = b""
    for _ in range(4):
        marker = (
            f"\n[... {omitted} UTF-8 bytes omitted from model-visible "
            "tool output; full Registry evidence is unchanged ...]\n"
        ).encode("utf-8")
        room = byte_limit - len(marker)
        prefix = _utf8_prefix(raw, max(0, room // 2))
        suffix = _utf8_suffix(raw, max(0, room - len(prefix)))
        omitted = len(raw) - len(prefix) - len(suffix)
    reduced = prefix + marker + suffix
    if len(reduced) > byte_limit:
        suffix = _utf8_suffix(suffix, len(suffix) - (len(reduced) - byte_limit))
        reduced = prefix + marker + suffix
    return reduced.decode("utf-8")


def reduce_tool_messages(
        messages: Sequence[Mapping[str, Any]], *, byte_limit: int,
        exempt_tool_call_ids: AbstractSet[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    """Copy messages while reducing non-exempt model-visible tool content."""
    reduced: list[dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, Mapping):
            raise TypeError("history messages must be mappings")
        message = dict(raw)
        if message.get("role") == "tool":
            content = message.get("content")
            if not isinstance(content, str):
                raise TypeError("tool history content must be text")
            if message.get("tool_call_id") not in exempt_tool_call_ids:
                try:
                    value = json.loads(content)
                except ValueError:
                    value = None
                kind = value.get("kind") if isinstance(value, Mapping) else None
                if kind == "managed_output_page/v1":
                    from .managed_output import (
                        bound_managed_output_page, serialized_managed_json,
                    )
                    message["content"] = serialized_managed_json(
                        bound_managed_output_page(value, byte_limit))
                elif kind == "tool_program_output_page/v1":
                    from .managed_output import _fit_page, serialized_managed_json
                    message["content"] = serialized_managed_json(_fit_page(value, byte_limit))
                elif kind == "tool_result_archive_page/v1":
                    from .result_archive import bound_result_archive_page
                    from .managed_output import serialized_managed_json
                    message["content"] = serialized_managed_json(
                        bound_result_archive_page(value, byte_limit))
                elif kind in {"managed_native_plugin_result/v1", "tool_program_result/v1"}:
                    if len(content.encode("utf-8")) > byte_limit:
                        raise ValueError(
                            "managed output cannot be truncated without a callable reader")
                else:
                    message["content"] = reduce_tool_output(
                        content, byte_limit=byte_limit)
        reduced.append(message)
    return tuple(reduced)


def bound_tool_result_groups(messages, *, byte_limit=TOOL_BATCH_BYTE_LIMIT):
    """Bound each complete turn, preserving every call/result identity.

    Exact pages may become explicit locators when their previews cannot fit.
    Already executed calls are never dropped, combined, or dispatched again.
    """
    if type(byte_limit) is not int or byte_limit < 1:
        raise ValueError("tool batch byte limit must be positive")

    def size(value):
        return len(json.dumps(value, ensure_ascii=True, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode())

    bounded = []
    for group in _message_groups(messages):
        if not any(m.get("role") == "tool" for m in group) or size(group) <= byte_limit:
            bounded.extend(group)
            continue
        minimum = deepcopy(list(group))
        expandable = []
        for index, message in enumerate(minimum):
            if message.get("role") != "tool":
                continue
            content = message["content"]
            try:
                body = json.loads(content)
            except ValueError:
                body = None
            kind = body.get("kind") if isinstance(body, Mapping) else None
            if kind in {"managed_output_page/v1", "tool_program_output_page/v1", "tool_result_archive_page/v1"}:
                reference = {"kind": "tool_result_reference/v1", "result_kind": kind,
                             **{key: value for key, value in body.items()
                                if key not in {"kind", "content", "truncated", "next_offset_chars", "entries", "next_offset"}}}
                message["content"] = json.dumps(reference, ensure_ascii=True,
                    sort_keys=True, separators=(",", ":"), allow_nan=False)
                expandable.append((index, content))
            elif kind not in {"managed_native_plugin_result/v1", "tool_program_result/v1"}:
                # Preserve known error/status metadata intact; only plain text
                # may use the existing marked head/tail representation.
                if body is None and len(content.encode()) > 128:
                    message["content"] = reduce_tool_output(content, byte_limit=128)
                    expandable.append((index, content))
        if size(minimum) > byte_limit:
            raise ValueError("tool batch minimum call/result envelopes exceed the visible budget")
        # Deterministic original ordinal order. Account for double JSON
        # escaping in the actual message list, not just the inner page bytes.
        for index, original in expandable:
            low, high = 0, len(original.encode())
            best = minimum[index]["content"]
            while low <= high:
                middle = (low + high) // 2
                try:
                    candidate = reduce_tool_messages(
                        [dict(minimum[index], content=original)],
                        byte_limit=max(128, middle))[0]["content"]
                except ValueError:
                    low = middle + 1
                    continue
                minimum[index]["content"] = candidate
                if size(minimum) <= byte_limit:
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            minimum[index]["content"] = best
        assert size(minimum) <= byte_limit
        bounded.extend(minimum)
    return tuple(bounded)


def prior_tool_result_reference(
        result_metadata: Mapping[str, Any],
        agent_action_ref: Mapping[str, Any],
        *, model_visible_byte_limit: int | None = None,
) -> dict[str, Any]:
    """Replace an already-delivered large tool body with its exact action locator."""

    if (not isinstance(agent_action_ref, Mapping)
            or set(agent_action_ref) != {
                "entity_type", "logical_id", "version_id"}
            or agent_action_ref.get("entity_type") not in {
                "agent_action/v2", "agent_action/v3"}
            or not isinstance(agent_action_ref.get("logical_id"), str)
            or not str(agent_action_ref["logical_id"]).startswith(
                "agent_action:")
            or len(str(agent_action_ref["logical_id"])) != 45
            or any(character not in "0123456789abcdef"
                   for character in str(agent_action_ref["logical_id"])[13:])
            or not isinstance(agent_action_ref.get("version_id"), str)
            or not str(agent_action_ref["version_id"]).startswith(
                "agent_action_version:")
            or len(str(agent_action_ref["version_id"])) != 53
            or any(character not in "0123456789abcdef"
                   for character in str(agent_action_ref["version_id"])[21:])):
        raise TypeError("prior tool result requires one exact agent-action ref")
    if not isinstance(result_metadata, Mapping):
        raise TypeError("prior tool result metadata must be a mapping")
    kind = result_metadata.get("kind")
    common = {
        "agent_action_ref": dict(agent_action_ref),
        "result_kind": kind,
        "model_visible_history": "reference_only_after_immediate_delivery",
    }
    if kind == "managed_native_plugin_result/v1":
        terminal_ref = result_metadata.get("terminal_receipt_ref")
        if (agent_action_ref.get("entity_type") != "agent_action/v3"
                or set(result_metadata) != {
                    "kind", "output", "terminal_receipt_ref"}
                or not isinstance(terminal_ref, Mapping)
                or set(terminal_ref) != {
                    "resource_id", "resource_version_id"}
                or not isinstance(terminal_ref.get("resource_id"), str)
                or not terminal_ref["resource_id"].startswith("resource:")
                or not isinstance(
                    terminal_ref.get("resource_version_id"), str)
                or not terminal_ref["resource_version_id"].startswith(
                    "resource_version:")):
            raise ValueError("prior managed result metadata is invalid")
        if (model_visible_byte_limit is not None
                and (isinstance(model_visible_byte_limit, bool)
                     or not isinstance(model_visible_byte_limit, int)
                     or model_visible_byte_limit < 128)):
            raise ValueError("managed result delivery limit is invalid")
        serialized_size = len(json.dumps(
            dict(result_metadata), ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8"))
        if model_visible_byte_limit is None:
            delivery = "not_recorded"
            history = "delivery_not_recorded"
            notice = (
                "This action records the complete Registry result and terminal "
                "receipt, but does not record whether provider content was "
                "delivered. No provider-delivery claim is made.")
        elif serialized_size <= model_visible_byte_limit:
            delivery = "full"
            history = "full_within_limit_before_reference"
            notice = (
                "The complete managed result fit the immediate model-visible "
                "history limit. The terminal receipt identifies full Registry "
                "evidence; it is not itself proof of provider receipt.")
        else:
            delivery = "bounded"
            history = "bounded_before_reference"
            notice = (
                "Only a bounded representation of the managed result fit the "
                "immediate model-visible history. The complete output was not "
                "delivered in full as provider content; it remains available "
                "from the exact Registry terminal receipt.")
        return {
            **common,
            "model_visible_history": history,
            "status": "returned",
            "terminal_receipt_ref": dict(terminal_ref),
            "provider_content_delivery": delivery,
            "output_replayed": False,
            "history_notice": notice,
        }
    if kind == "bounded_numerical_result/v1":
        if (set(result_metadata) != {
                "kind", "status", "result_size_bytes", "path"}
                or result_metadata.get("status") != "ok"
                or isinstance(result_metadata.get("result_size_bytes"), bool)
                or not isinstance(result_metadata.get("result_size_bytes"), int)
                or int(result_metadata["result_size_bytes"]) < 1
                or not isinstance(result_metadata.get("path"), str)
                or not result_metadata["path"]):
            raise ValueError("prior numerical result metadata is invalid")
        return {
            **common,
            "status": "ok",
            "path": result_metadata["path"],
            "result_size_bytes": result_metadata["result_size_bytes"],
            "result_replayed": False,
            "history_notice": (
                "The complete numerical result was delivered on the immediately "
                "following provider turn and remains at the exact workspace path. "
                "It is not duplicated in later turn history."),
        }
    if kind == "workspace_execution/v1":
        if (set(result_metadata) != {
                "kind", "status", "exit_code", "stdout", "stderr",
                "output_truncated", "command_started"}
                or result_metadata.get("status") not in {
                    "completed", "timed_out"}
                or isinstance(result_metadata.get("exit_code"), bool)
                or not isinstance(result_metadata.get("exit_code"), int)
                or not isinstance(result_metadata.get("stdout"), str)
                or not isinstance(result_metadata.get("stderr"), str)
                or not isinstance(result_metadata.get("output_truncated"), bool)
                or result_metadata.get("command_started") is not True):
            raise ValueError("prior workspace result metadata is invalid")
        return {
            **common,
            "status": result_metadata["status"],
            "exit_code": result_metadata["exit_code"],
            "output_truncated": result_metadata["output_truncated"],
            "stdout_replayed": False,
            "stderr_replayed": False,
            "history_notice": (
                "Complete stdout/stderr was delivered on the immediately following "
                "provider turn. It is not duplicated in later history. Use "
                "read_action_output with this exact agent_action_ref and the "
                "required stdout or stderr stream to retrieve bounded pages."),
        }
    if kind == "registered_file_read/v1":
        if (set(result_metadata) != {
                "kind", "path", "resource_ref", "use_receipt_ref"}
                or not isinstance(result_metadata.get("path"), str)
                or not result_metadata["path"]
                or not isinstance(result_metadata.get("resource_ref"), Mapping)
                or not isinstance(
                    result_metadata.get("use_receipt_ref"), Mapping)):
            raise ValueError("prior registered file read metadata is invalid")
        return {
            **common,
            "path": result_metadata["path"],
            "resource_ref": dict(result_metadata["resource_ref"]),
            "use_receipt_ref": dict(result_metadata["use_receipt_ref"]),
            "body_replayed": False,
            "history_notice": (
                "The complete registered file page was delivered on the "
                "immediately following provider turn. It is not duplicated in "
                "later history; call read_file with this exact path when the "
                "body is needed again."),
        }
    raise ValueError("tool result kind has no reference-only history projection")


def _message_groups(
        messages: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], ...]:
    """Return complete assistant/tool groups without splitting tool closure."""

    copied = tuple(dict(message) for message in messages)
    validate_llm_request_message_history(copied)
    groups: list[tuple[dict[str, Any], ...]] = []
    index = 0
    while index < len(copied):
        message = copied[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            cursor = index + 1
            while (cursor < len(copied)
                   and copied[cursor].get("role") == "tool"):
                cursor += 1
            groups.append(copied[index:cursor])
            index = cursor
        else:
            groups.append((message,))
            index += 1
    return tuple(groups)


def retained_recent_history(
        messages: Sequence[Mapping[str, Any]], *, token_limit: int,
) -> tuple[dict[str, Any], ...]:
    """Retain the newest complete model-visible groups within one token budget."""

    if (isinstance(token_limit, bool) or not isinstance(token_limit, int)
            or token_limit < 1):
        raise ValueError("retained history token limit must be positive")
    groups = _message_groups(messages)
    kept: list[tuple[dict[str, Any], ...]] = []
    used = 0
    for group in reversed(groups):
        size = approximate_tokens(json.dumps(
            group, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")))
        if used + size > token_limit:
            break
        kept.append(group)
        used += size
    kept.reverse()
    return tuple(message for group in kept for message in group)


def build_replacement_history(
        messages: Sequence[Mapping[str, Any]], summary: str, *,
        retained_history_token_limit: int,
        fact_capsule: Mapping[str, Any],
        pending_tool_call_ids: AbstractSet[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    """Build the bounded forward-only replacement-history contract."""
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("context summary must be nonempty text")
    if (not isinstance(messages, Sequence)
            or isinstance(messages, (str, bytes))
            or any(not isinstance(message, Mapping) for message in messages)
            or isinstance(retained_history_token_limit, bool)
            or not isinstance(retained_history_token_limit, int)
            or retained_history_token_limit < 1):
        raise TypeError("context compaction source history is invalid")
    if (not isinstance(fact_capsule, Mapping)
            or fact_capsule.get("kind") != "agent_context_fact_capsule"
            or fact_capsule.get("schema_version")
            != "agent_context_fact_capsule/v1"):
        raise ValueError("context compaction requires one typed fact capsule")
    archive = fact_capsule.get("result_archive")
    archive_bytes = (len(json.dumps(archive, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()) if archive else 0)
    if archive_bytes >= TOOL_BATCH_BYTE_LIMIT:
        raise ValueError("result archive locator exceeds the active notification budget")
    groups = list(_message_groups(messages))
    # Only the latest recorded ordinary turn can still owe a result notice.
    # Compaction responses do not discharge that obligation. Reused IDs in
    # older groups do not make old pages permanently protected.
    pending_index = next((i for i in range(len(groups) - 1, -1, -1)
        if any(m.get("role") == "tool" and m.get("tool_call_id") in pending_tool_call_ids
               for m in groups[i])), None)
    if pending_index is not None:
        groups[pending_index] = bound_tool_result_groups(
            groups[pending_index], byte_limit=TOOL_BATCH_BYTE_LIMIT - archive_bytes)
    messages = tuple(message for group in groups for message in group)
    recent_budget = retained_history_token_limit - math.ceil(archive_bytes / 4)
    recent = (retained_recent_history(messages, token_limit=recent_budget)
              if recent_budget > 0 else ())
    recent_start = len(messages) - len(recent)
    retained = []
    consumed = 0
    for index, group in enumerate(groups):
        if consumed >= recent_start or index == pending_index:
            retained.extend(bound_tool_result_groups(group))
        else:
            for message in group:
                if message.get("role") != "tool":
                    continue
                try:
                    value = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    continue
                if (isinstance(value, Mapping)
                        and value.get("kind") == "managed_native_plugin_result/v1"
                        and value.get("reader") is None):
                    raise ValueError("cannot retire a managed result without a callable reader")
        consumed += len(group)
    return copy_replacement_history((
        deepcopy(dict(fact_capsule)),
        {"kind": "compaction_summary", "content": summary},
        *({"kind": "retained_model_visible_message", "message": message}
          for message in retained),
    ))


def copy_replacement_history(
        replacement_history: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Copy one exact typed history without reconstructing capsule facts."""

    if (not isinstance(replacement_history, Sequence)
            or isinstance(replacement_history, (str, bytes))):
        raise TypeError("replacement history must be a typed sequence")
    copied = tuple(
        deepcopy(dict(entry)) if isinstance(entry, Mapping) else None
        for entry in replacement_history)
    if any(entry is None for entry in copied):
        raise TypeError("replacement history entries must be mappings")
    typed = tuple(entry for entry in copied if entry is not None)
    capsule_entries = tuple(
        entry for entry in typed
        if entry.get("kind") == "agent_context_fact_capsule")
    summary_entries = tuple(
        entry for entry in typed
        if entry.get("kind") == "compaction_summary")
    if (len(typed) < 2
            or len(capsule_entries) != 1
            or len(summary_entries) != 1
            or typed[0] is not capsule_entries[0]
            or typed[1] is not summary_entries[0]
            or capsule_entries[0].get("schema_version")
            != "agent_context_fact_capsule/v1"
            or set(summary_entries[0]) != {"kind", "content"}
            or not isinstance(summary_entries[0].get("content"), str)
            or not summary_entries[0]["content"].strip()):
        raise ValueError(
            "replacement history requires one capsule and one summary prefix")

    retained_entries = typed[2:]
    if any(
            set(entry) != {"kind", "message"}
            or entry.get("kind") != "retained_model_visible_message"
            or not isinstance(entry.get("message"), Mapping)
            for entry in retained_entries):
        raise ValueError("replacement history retained messages are invalid")
    retained_messages = tuple(
        dict(entry["message"]) for entry in retained_entries)
    for message in retained_messages:
        role = message.get("role")
        allowed = {"role", "content"}
        if role == "assistant":
            allowed.update({"tool_calls", "reasoning_content"})
        elif role == "tool":
            allowed.add("tool_call_id")
        if (role not in {"user", "assistant", "tool"}
                or not isinstance(message.get("content"), str)
                or (role != "assistant" and not message["content"].strip())
                or not set(message).issubset(allowed)
                or (role == "tool"
                    and (not isinstance(message.get("tool_call_id"), str)
                         or not message["tool_call_id"]))
                or ("reasoning_content" in message
                    and not isinstance(message["reasoning_content"], str))):
            raise ValueError(
                "replacement history retained message is invalid")
    try:
        validate_llm_request_message_history(retained_messages)
    except ValueError as exc:
        raise ValueError(
            "replacement history retained messages break tool closure") from exc

    forbidden_capsule_fields = {
        "arguments", "body", "body_content", "content", "message",
        "normalized_assistant_text", "provider_prose", "raw_arguments",
        "raw_response_utf8", "semantic_conclusion", "stderr", "stdout",
        "summary",
    }

    def contains_forbidden_field(value: Any) -> bool:
        if isinstance(value, Mapping):
            return (bool(forbidden_capsule_fields.intersection(value))
                    or any(contains_forbidden_field(item)
                           for item in value.values()))
        if isinstance(value, (list, tuple)):
            return any(contains_forbidden_field(item) for item in value)
        return False

    if contains_forbidden_field(capsule_entries[0]):
        raise ValueError("fact capsule contains forbidden body or prose fields")
    return typed


def drop_oldest_visible_item(
        messages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Retry reduction for a compaction request rejected as over-context."""
    values = tuple(dict(message) for message in messages)
    return values[1:] if values else values


__all__ = [
    "CONTEXT_CHECKPOINT_PROMPT",
    "ContextPressurePolicy",
    "ContextReductionSettings",
    "approximate_tokens",
    "drop_oldest_visible_item",
    "projected_tokens",
    "prior_tool_result_reference",
    "reduce_tool_messages",
    "reduce_tool_output",
    "build_replacement_history",
    "copy_replacement_history",
    "retained_recent_history",
    "should_compact",
]
