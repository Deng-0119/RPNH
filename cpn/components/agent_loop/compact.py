"""Pure model-visible context reduction for the current AgentLoop lane."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import AbstractSet, Any, Mapping, Sequence


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
    retained_user_token_limit: int = 20_000

    def __post_init__(self) -> None:
        if (not isinstance(self.checkpoint_prompt, str)
                or not self.checkpoint_prompt.strip()
                or self.checkpoint_prompt != self.checkpoint_prompt.strip()):
            raise ValueError("context checkpoint prompt must be nonempty text")
        if (isinstance(self.tool_output_byte_limit, bool)
                or not isinstance(self.tool_output_byte_limit, int)
                or self.tool_output_byte_limit < 128):
            raise ValueError("tool output byte limit is too small")
        if (isinstance(self.retained_user_token_limit, bool)
                or not isinstance(self.retained_user_token_limit, int)
                or self.retained_user_token_limit < 1):
            raise ValueError("retained user token limit must be positive")


@dataclass(frozen=True, slots=True)
class ContextPressurePolicy:
    """Optional pressure trigger available only for known model capacity."""

    context_window_tokens: int
    trigger_ratio: float = 0.90

    def __post_init__(self) -> None:
        if (isinstance(self.context_window_tokens, bool)
                or not isinstance(self.context_window_tokens, int)
                or self.context_window_tokens < 1):
            raise ValueError("context window must be a positive token count")
        if (isinstance(self.trigger_ratio, bool)
                or not isinstance(self.trigger_ratio, (int, float))
                or not 0.0 < float(self.trigger_ratio) < 1.0):
            raise ValueError(
                "context pressure trigger ratio must be between zero and one")

    @property
    def trigger_tokens(self) -> int:
        exact_ratio = Fraction(str(self.trigger_ratio))
        return math.floor(self.context_window_tokens * exact_ratio) + 1


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
                message["content"] = reduce_tool_output(
                    content, byte_limit=byte_limit)
        reduced.append(message)
    return tuple(reduced)


def prior_tool_result_reference(
        result_metadata: Mapping[str, Any],
        agent_action_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace an already-delivered large tool body with its exact action locator."""

    if (not isinstance(agent_action_ref, Mapping)
            or set(agent_action_ref) != {
                "entity_type", "logical_id", "version_id"}
            or agent_action_ref.get("entity_type") != "agent_action/v2"
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


def retained_recent_user_messages(
        messages: Sequence[Mapping[str, Any]], *, token_limit: int,
) -> tuple[dict[str, str], ...]:
    """Retain the newest real user messages within the configured retained-user budget."""
    if (isinstance(token_limit, bool) or not isinstance(token_limit, int)
            or token_limit < 1):
        raise ValueError("retained user token limit must be positive")
    kept: list[dict[str, str]] = []
    used = 0
    for raw in reversed(messages):
        if not isinstance(raw, Mapping) or raw.get("role") != "user":
            continue
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        size = approximate_tokens(content)
        if kept and used + size > token_limit:
            break
        if not kept and size > token_limit:
            continue
        kept.append({"role": "user", "content": content})
        used += size
    kept.reverse()
    return tuple(kept)


def build_replacement_history(
        messages: Sequence[Mapping[str, Any]], summary: str, *,
        retained_user_token_limit: int,
        fact_capsule: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Build the bounded forward-only replacement-history contract."""
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("context summary must be nonempty text")
    if (not isinstance(messages, Sequence)
            or isinstance(messages, (str, bytes))
            or any(not isinstance(message, Mapping) for message in messages)
            or isinstance(retained_user_token_limit, bool)
            or not isinstance(retained_user_token_limit, int)
            or retained_user_token_limit < 1):
        raise TypeError("context compaction source history is invalid")
    if (not isinstance(fact_capsule, Mapping)
            or fact_capsule.get("kind") != "agent_context_fact_capsule"
            or fact_capsule.get("schema_version")
            != "agent_context_fact_capsule/v1"):
        raise ValueError("context compaction requires one typed fact capsule")
    return copy_replacement_history((
        deepcopy(dict(fact_capsule)),
        {"kind": "compaction_summary", "content": summary},
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
    if (len(typed) != 2
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
            "replacement history requires exactly one capsule followed by "
            "one final summary")

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
    "retained_recent_user_messages",
    "should_compact",
]
