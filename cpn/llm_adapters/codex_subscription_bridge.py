"""One-shot bridge from the neutral LLM envelope to a Codex subscription."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from cpn.rpnh.response_protocol import (
    LLMResponseProtocolError,
    canonicalize_llm_response_payload,
)

from ._common import (
    ResponseEnvelopeError,
    canonical_request_envelope_document,
)


_CODEX_LIFECYCLE_EVENTS = {
    "thread.started", "turn.started", "turn.completed",
}
_CODEX_FAILURE_EVENT_TYPES = {"error", "turn.failed"}
_ALLOWED_ITEM_TYPES = {"agent_message", "reasoning"}
_BRIDGE_FAILURE_PREFIX = "bridge: local_bridge_failure: "


class CodexSubscriptionBridgeError(RuntimeError):
    """The Codex process did not produce one safe canonical model response."""

    def __init__(self, message: str, *, failure_code: str = "codex_bridge_failure") -> None:
        super().__init__(message)
        self.failure_code = failure_code


# These instructions belong to RPNH, not to the terminal client or provider.
# They replace the provider CLI's built-in coding-agent instructions. Never
# rewrite user messages or the registered request to remove brand names.
RPNH_ENDPOINT_INSTRUCTIONS = (
    "You are the language-model response endpoint for RPNH, not an independent "
    "agent runtime. Follow the roles, messages and declared tools in REQUEST_JSON. "
    "RPNH alone owns tool execution, resources, permissions and task completion. "
    "Do not inspect host files, run commands, browse, call external services, "
    "or invoke tools supplied by the transport process. Request an RPNH tool "
    "only by returning it in tool_calls; a request is not evidence of execution. "
    "Return exactly one JSON object matching the supplied output schema and "
    "protocol llm_response_envelope/v1. Every tool call needs a unique id, its "
    "declared name, and arguments encoded as a JSON string. Use "
    "finish_reason=tool_calls for tool requests and finish_reason=stop for a "
    "final response. Return unused nullable fields as null and usage as null; "
    "the transport records observed usage. Do not add Markdown or commentary "
    "outside the JSON. Never invent execution results or change the selected model."
)


def _build_endpoint_prompt(request: Mapping[str, Any]) -> str:
    canonical_request = json.dumps(
        request, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "Respond to the following request under the RPNH endpoint contract.\n"
        "REQUEST_JSON_BEGIN\n"
        f"{canonical_request}\n"
        "REQUEST_JSON_END"
    )


def _codex_argv(
        *, codex_binary: Path, model: str, response_schema: Path,
        working_directory: Path, reasoning_effort: str,
        model_context_window: int,
        verbosity: str, instructions_file: Path,
) -> tuple[str, ...]:
    return (
        str(codex_binary), "exec", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "-c",
        f"model_context_window={model_context_window}", "-c",
        f'model_reasoning_effort="{reasoning_effort}"', "-c",
        f'model_verbosity="{verbosity}"', "-c",
        "model_instructions_file=" + json.dumps(str(instructions_file)),
        # This subprocess is the transport adapter's raw endpoint.  The
        # framework's declared tools are represented in REQUEST_JSON and must
        # be returned as response tool_calls; they are not Codex-runtime tools
        # for the bridge process to execute.  Disable the CLI capabilities that
        # could otherwise emit built-in tool items, which the bridge cannot
        # translate into the framework response contract.
        "--disable", "shell_tool",
        "--disable", "computer_use",
        "--disable", "browser_use",
        "--disable", "browser_use_external",
        "--disable", "view_image",
        "--disable", "apps",
        "--disable", "enable_mcp_apps",
        "--disable", "tool_call_mcp_elicitation",
        "--skip-git-repo-check", "--sandbox", "read-only",
        "--cd", str(working_directory), "--model", model,
        "--output-schema", str(response_schema), "--json", "-",
    )


def _codex_process_environment(*, runtime_root: Path) -> dict[str, str]:
    """Give the endpoint writable firing-local state without copying credentials.

    ``os.access`` and filesystem flags cannot observe policy-based write
    restrictions inherited by a child process.  ``codex exec --ephemeral``
    still initializes writable app-server state, so every configured
    subscription home is projected into a firing-local home while the existing
    ``auth.json`` remains a reference to its configured source.  This avoids a
    credential copy that could survive a forced process-group termination.  The
    model, route, and credential bytes are unchanged.
    """
    environment = dict(os.environ)
    configured = environment.get("CODEX_HOME")
    if configured:
        configured_path = Path(configured)
    else:
        home = environment.get("HOME")
        if not home:
            raise CodexSubscriptionBridgeError(
                "Codex subscription home is unavailable")
        configured_path = Path(home) / ".codex"
    auth_source = configured_path / "auth.json"
    if not auth_source.is_file():
        raise CodexSubscriptionBridgeError(
            "Codex subscription home has no readable auth.json")
    ephemeral_home = runtime_root / "codex-home"
    ephemeral_home.mkdir(mode=0o700)
    (ephemeral_home / "tmp").mkdir(mode=0o700)
    auth_destination = ephemeral_home / "auth.json"
    auth_destination.symlink_to(auth_source)
    environment["CODEX_HOME"] = str(ephemeral_home)
    environment["HOME"] = str(runtime_root / "home")
    Path(environment["HOME"]).mkdir(mode=0o700)
    environment["TMPDIR"] = str(runtime_root / "tmp")
    Path(environment["TMPDIR"]).mkdir(mode=0o700)
    environment["XDG_RUNTIME_DIR"] = str(runtime_root / "xdg-runtime")
    Path(environment["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    return environment


def _event_document(raw_line: bytes, *, ordinal: int) -> dict[str, Any]:
    try:
        value = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexSubscriptionBridgeError(
            f"Codex event {ordinal} is not JSON") from exc
    if not isinstance(value, dict):
        raise CodexSubscriptionBridgeError(
            f"Codex event {ordinal} is not an object")
    return value


def _codex_event_failure(event: Mapping[str, Any]) -> CodexSubscriptionBridgeError:
    """Retain a provider event's mechanical failure cause across the bridge."""
    message: str | None = None
    direct = event.get("message")
    if isinstance(direct, str) and direct.strip():
        message = direct.strip()
    error = event.get("error")
    if isinstance(error, str) and error.strip():
        message = error.strip()
    elif isinstance(error, Mapping):
        nested = error.get("message")
        if isinstance(nested, str) and nested.strip():
            message = nested.strip()
    detail = event.get("detail")
    if message is None and isinstance(detail, str) and detail.strip():
        message = detail.strip()
    if message is None:
        message = "Codex reported a failed turn"
    message = message[:1000]
    failure_code = (
        "provider_model_capacity"
        if "at capacity" in message.casefold()
        else "codex_provider_error"
    )
    return CodexSubscriptionBridgeError(
        message, failure_code=failure_code)


def _codex_failure_from_events(stdout: bytes) -> CodexSubscriptionBridgeError | None:
    """Find a typed provider failure in a non-success Codex JSONL stream."""
    if not isinstance(stdout, bytes) or not stdout.strip():
        return None
    for ordinal, raw_line in enumerate(stdout.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (isinstance(event, Mapping)
                and event.get("type") in _CODEX_FAILURE_EVENT_TYPES):
            return _codex_event_failure(event)
    return None


def _canonical_response_from_codex_events(stdout: bytes) -> bytes:
    if not isinstance(stdout, bytes) or not stdout.strip():
        raise CodexSubscriptionBridgeError("Codex returned no JSONL events")
    final_messages: list[str] = []
    completion_usage: Mapping[str, Any] | None = None
    saw_completed_turn = False
    for ordinal, raw_line in enumerate(stdout.splitlines(), start=1):
        if not raw_line.strip():
            continue
        event = _event_document(raw_line, ordinal=ordinal)
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise CodexSubscriptionBridgeError(
                f"Codex event {ordinal} lacks a type")
        if event_type in _CODEX_FAILURE_EVENT_TYPES:
            raise _codex_event_failure(event)
        if event_type in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, Mapping) else None
            if item_type not in _ALLOWED_ITEM_TYPES:
                raise CodexSubscriptionBridgeError(
                    "Codex attempted a built-in tool or unsupported item")
            if event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or not text:
                    raise CodexSubscriptionBridgeError(
                        "Codex completed an empty agent message")
                final_messages.append(text)
            continue
        if event_type == "turn.completed":
            if saw_completed_turn:
                raise CodexSubscriptionBridgeError(
                    "Codex reported more than one completed turn")
            usage = event.get("usage")
            if usage is not None and not isinstance(usage, Mapping):
                raise CodexSubscriptionBridgeError(
                    "Codex completion usage is malformed")
            completion_usage = usage
            saw_completed_turn = True
            continue
        if event_type not in _CODEX_LIFECYCLE_EVENTS:
            raise CodexSubscriptionBridgeError(
                f"Codex emitted unsupported event type {event_type!r}")
    if not saw_completed_turn or len(final_messages) != 1:
        raise CodexSubscriptionBridgeError(
            "Codex did not produce exactly one completed final message")
    try:
        response = json.loads(final_messages[0])
    except json.JSONDecodeError as exc:
        raise CodexSubscriptionBridgeError(
            "Codex final message is not a JSON response envelope") from exc
    if not isinstance(response, dict):
        raise CodexSubscriptionBridgeError(
            "Codex final message is not a response object")
    if completion_usage is not None:
        response["usage"] = dict(completion_usage)
    try:
        return canonicalize_llm_response_payload(response)
    except LLMResponseProtocolError as exc:
        raise CodexSubscriptionBridgeError(str(exc)) from exc


def _codex_error_summary(stderr: bytes) -> str | None:
    if not isinstance(stderr, bytes) or not stderr.strip():
        return None
    text = stderr.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1][:1000]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate one framework LLM request through Codex")
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-context-window", required=True, type=int)
    parser.add_argument("--model-max-output-tokens", required=True, type=int)
    parser.add_argument("--output-token-policy", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--verbosity", required=True)
    parser.add_argument("--response-schema", required=True, type=Path)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not args.codex.is_absolute() or not args.codex.is_file():
        raise CodexSubscriptionBridgeError("Codex executable is unavailable")
    text_values = (
        args.model, args.output_token_policy, args.reasoning_effort,
        args.verbosity,
    )
    if any(not isinstance(value, str) or not value
           or value != value.strip()
           or any(ord(character) < 32 or ord(character) == 127
                  for character in value)
           for value in text_values):
        raise CodexSubscriptionBridgeError(
            "Codex bridge text configuration is invalid")
    if (isinstance(args.model_context_window, bool)
            or not isinstance(args.model_context_window, int)
            or args.model_context_window < 1
            or isinstance(args.model_max_output_tokens, bool)
            or not isinstance(args.model_max_output_tokens, int)
            or args.model_max_output_tokens < 1):
        raise CodexSubscriptionBridgeError(
            "Codex bridge token limits must be positive")
    if not args.response_schema.is_absolute() or not args.response_schema.is_file():
        raise CodexSubscriptionBridgeError("response schema is unavailable")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_args(args)
        request_bytes = sys.stdin.buffer.read()
        request = canonical_request_envelope_document(
            request_bytes,
            expected_model_condition=args.model,
            expected_max_output_tokens=args.model_max_output_tokens,
        )
        prompt = _build_endpoint_prompt(request)
        with tempfile.TemporaryDirectory(
                prefix="codex-subscription-endpoint-", dir=Path.cwd()) as root:
            runtime_root = Path(root)
            instructions_file = runtime_root / "rpnh-endpoint-instructions.txt"
            instructions_file.write_text(RPNH_ENDPOINT_INSTRUCTIONS, encoding="utf-8")
            instructions_file.chmod(0o600)
            command = _codex_argv(
                codex_binary=args.codex,
                model=args.model,
                response_schema=args.response_schema,
                working_directory=Path(root),
                reasoning_effort=args.reasoning_effort,
                model_context_window=args.model_context_window,
                verbosity=args.verbosity,
                instructions_file=instructions_file,
            )
            previous_umask = os.umask(0o077)
            try:
                completed = subprocess.run(
                    command, input=prompt.encode("utf-8"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, shell=False,
                    env=_codex_process_environment(runtime_root=runtime_root),
                )
            finally:
                os.umask(previous_umask)
        if completed.returncode != 0:
            event_failure = _codex_failure_from_events(completed.stdout)
            if event_failure is not None:
                raise event_failure
            detail = _codex_error_summary(completed.stderr)
            raise CodexSubscriptionBridgeError(
                f"Codex endpoint exited with status {completed.returncode}"
                + (f": {detail}" if detail else ""),
                failure_code="codex_endpoint_exit")
        response_bytes = _canonical_response_from_codex_events(completed.stdout)
    except (
        CodexSubscriptionBridgeError, ResponseEnvelopeError, OSError, ValueError,
    ) as exc:
        failure_code = getattr(exc, "failure_code", "codex_bridge_failure")
        print(
            f"{_BRIDGE_FAILURE_PREFIX}{failure_code} | {exc}",
            file=sys.stderr,
        )
        return 1
    sys.stdout.buffer.write(response_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CodexSubscriptionBridgeError", "main",
]
