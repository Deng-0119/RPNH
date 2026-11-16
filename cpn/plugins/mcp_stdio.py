"""Per-invocation MCP stdio exchange behind an explicitly registered plugin.

Use only inside the existing POSIX RPNH worker. The subprocess stays in that
worker's process group. The HOST supplies a complete private child environment.
No credentials, executable selection, effect or tool admission come from tool
arguments. This module has no Registry writer or model client.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .api import PluginError, canonical, json_copy
from .mcp_contracts import SelectedTool, catalog_pages, result_envelope
from .worker import WorkerFailure

SDK_VERSION = "1.30.0"
PROTOCOL_VERSION = "2025-11-25"


class MCPBridgeFailure(WorkerFailure):
    def __init__(self, code, *, dispatched=False, observed=False, protocol_code=None):
        super().__init__(code, may_have_executed=dispatched)
        self.observation_lost = dispatched and not observed
        self.protocol_code = protocol_code


@dataclass(frozen=True, slots=True)
class StdioDeployment:
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    max_frame_bytes: int = 1024 * 1024
    max_catalog_bytes: int = 4 * 1024 * 1024
    max_result_bytes: int = 1024 * 1024

    def __post_init__(self):
        if (not isinstance(self.command, str) or "\x00" in self.command
                or not Path(self.command).is_absolute()):
            raise PluginError("mcp_command_requires_explicit_absolute_path")
        if (not isinstance(self.args, tuple)
                or any(not isinstance(value, str) or "\x00" in value for value in self.args)):
            raise PluginError("mcp_arguments_invalid")
        if self.cwd is not None and (not isinstance(self.cwd, str)
                or "\x00" in self.cwd or not Path(self.cwd).is_absolute()):
            raise PluginError("mcp_cwd_requires_explicit_absolute_path")
        if any(type(value) is not int or value < 1 for value in (
                self.max_frame_bytes, self.max_catalog_bytes, self.max_result_bytes)):
            raise PluginError("mcp_limits_invalid")


@dataclass(slots=True)
class _State:
    dispatched: bool = False
    observed: bool = False
    last_request_id: int | str | None = None
    tool_request_id: int | str | None = None
    protocol_error_code: int | None = None
    terminal: dict | None = None
    buffered_result: Any = None
    result_received: bool = False
    discovery: bool = False
    discovery_request_id: int | str | None = None
    discovery_response_received: bool = False


def _record_complete_response(document, state):
    """Capture a strict, complete JSON-RPC response before SDK validation.

    The caller has already bounded and completely parsed one newline frame.
    The result body may itself be invalid MCP data, including null or a scalar;
    that is still an observed response and must remain available for validation.
    Requests, notifications, malformed envelopes and other IDs cannot establish
    this fact. Request IDs compare by both JSON type and value, never coercion.
    """
    if not isinstance(document, dict) or not ({"result", "error"} & set(document)):
        return
    result = "result" in document
    fields = {"jsonrpc", "id", "result" if result else "error"}
    if (set(document) != fields or document.get("jsonrpc") != "2.0"
            or type(document.get("id")) not in (str, int)):
        raise MCPBridgeFailure("mcp_protocol_invalid")
    if not result:
        error = document["error"]
        if (not isinstance(error, dict)
                or set(error) not in ({"code", "message"}, {"code", "message", "data"})
                or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)):
            raise MCPBridgeFailure("mcp_protocol_invalid")

    def matches(expected):
        return expected is not None and type(document["id"]) is type(expected) and document["id"] == expected

    if state.discovery and matches(state.discovery_request_id):
        state.discovery_response_received = True
    if matches(state.tool_request_id) and not state.observed:
        if result:
            state.buffered_result = json_copy(document["result"])
            state.result_received = True
        else:
            state.protocol_error_code = document["error"]["code"]
        state.observed = True
    elif state.tool_request_id is None and not result and matches(state.last_request_id):
        state.protocol_error_code = document["error"]["code"]


def private_environment(environment: Mapping[str, str]):
    """Validate an already-resolved HOST environment without reading os.environ."""
    if not isinstance(environment, Mapping):
        raise PluginError("mcp_environment_invalid")
    result = dict(environment)
    if any(not isinstance(k, str) or not k or "=" in k or "\x00" in k
           or not isinstance(v, str) or "\x00" in v for k, v in result.items()):
        result.clear()
        raise PluginError("mcp_environment_invalid")
    return result


def _sdk():
    try:
        installed = metadata.version("mcp")
    except metadata.PackageNotFoundError as exc:
        raise MCPBridgeFailure("mcp_sdk_missing") from exc
    if installed != SDK_VERSION:
        raise MCPBridgeFailure("mcp_sdk_version_mismatch")
    try:
        import anyio
        from mcp import ClientSession, types
        from mcp.shared.message import SessionMessage
        from pydantic import BaseModel, ConfigDict
    except ImportError as exc:
        raise MCPBridgeFailure("mcp_sdk_missing") from exc

    class RawResult(BaseModel):
        model_config = ConfigDict(extra="allow")
    return anyio, ClientSession, types, SessionMessage, RawResult


@asynccontextmanager
async def _stdio(deployment, environment, state, sdk):
    anyio, _, types, SessionMessage, _ = sdk
    inbound, incoming = anyio.create_memory_object_stream(0)
    outgoing, outbound = anyio.create_memory_object_stream(0)
    process = await anyio.open_process(
        [deployment.command, *deployment.args], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=environment, cwd=deployment.cwd, start_new_session=False,
    )

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    async def receive():
        buffer = bytearray()
        async with inbound:
            while True:
                try:
                    chunk = await process.stdout.receive(65536)
                except anyio.EndOfStream:
                    if buffer:
                        raise MCPBridgeFailure("mcp_incomplete_frame")
                    return
                buffer.extend(chunk)
                while b"\n" in buffer:
                    boundary = buffer.index(b"\n")
                    if boundary > deployment.max_frame_bytes:
                        raise MCPBridgeFailure("mcp_frame_limit_exceeded")
                    frame = bytes(buffer[:boundary])
                    del buffer[:boundary + 1]
                    try:
                        # JSON rejects nonfinite constants; Pydantic alone can coerce.
                        parsed = json.loads(frame, object_pairs_hook=unique_keys, parse_constant=lambda _: (
                            _ for _ in ()).throw(ValueError("nonfinite")))
                        # Capture the original complete matching response before
                        # SDK result-object validation can reject its raw body.
                        _record_complete_response(parsed, state)
                        message = types.JSONRPCMessage.model_validate(parsed)
                    except Exception as exc:
                        raise MCPBridgeFailure("mcp_protocol_invalid") from exc
                    await inbound.send(SessionMessage(message))
                if len(buffer) > deployment.max_frame_bytes:
                    raise MCPBridgeFailure("mcp_frame_limit_exceeded")

    async def send():
        async with outbound:
            async for item in outbound:
                payload = item.message.model_dump_json(
                    by_alias=True, exclude_none=True).encode("utf-8")
                if len(payload) > deployment.max_frame_bytes:
                    raise MCPBridgeFailure("mcp_request_limit_exceeded")
                if isinstance(item.message.root, types.JSONRPCRequest):
                    state.last_request_id = item.message.root.id
                if getattr(item.message.root, "method", None) == "tools/call":
                    # Set before writing: a partial write is already uncertain.
                    state.dispatched = True
                    state.tool_request_id = item.message.root.id
                elif state.discovery and getattr(item.message.root, "method", None) == "tools/list":
                    # Discovery is its own admitted read operation. Its pending
                    # page observation must not be mistaken for an undispatched
                    # operation, while selected-tool preflight still leaves the
                    # actual tools/call undispatched.
                    state.dispatched = True
                    state.discovery_request_id = item.message.root.id
                    state.discovery_response_received = False
                await process.stdin.send(payload + b"\n")

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(receive)
            group.start_soon(send)
            try:
                yield incoming, outgoing
            finally:
                group.cancel_scope.cancel()
    finally:
        # Keep cleanup bounded even if the outer exchange is cancelled.
        with anyio.CancelScope(shield=True):
            if process.stdin is not None:
                try:
                    await process.stdin.aclose()
                except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                    pass
            with anyio.move_on_after(0.2):
                await process.wait()
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                with anyio.move_on_after(0.2):
                    await process.wait()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                with anyio.move_on_after(1):
                    await process.wait()
            # Descendants are additionally removed by the original worker group
            # cleanup. Do not call killpg here: this group includes the worker.
            for stream in (inbound, incoming, outgoing, outbound):
                await stream.aclose()
            with anyio.move_on_after(0.2):
                await process.aclose()


async def _list_all(session, deployment, sdk):
    _, _, types, _, RawResult = sdk
    pages, seen_cursors, cursor, total = [], set(), None, 0
    while True:
        request = types.ClientRequest(types.ListToolsRequest(
            params=None if cursor is None else types.PaginatedRequestParams(cursor=cursor)))
        page = (await session.send_request(request, RawResult)).model_dump(
            mode="json", by_alias=True, exclude_unset=True)
        total += len(canonical(page))
        if total > deployment.max_catalog_bytes:
            raise MCPBridgeFailure("mcp_catalog_limit_exceeded")
        if not isinstance(page.get("tools"), list):
            raise MCPBridgeFailure("mcp_catalog_page_invalid")
        pages.append(page)
        cursor = page.get("nextCursor")
        if cursor is None:
            return catalog_pages(pages, max_bytes=deployment.max_catalog_bytes)
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise MCPBridgeFailure("mcp_catalog_cursor_invalid")
        seen_cursors.add(cursor)


def _failure_code(error):
    if isinstance(error, MCPBridgeFailure):
        return error.code
    if isinstance(error, PluginError):
        code = str(error)
        return code if code.startswith("mcp_") else "mcp_contract_invalid"
    if isinstance(error, TimeoutError):
        return "mcp_deadline_exceeded"
    nested = getattr(error, "exceptions", ())
    for child in nested:
        code = _failure_code(child)
        if code != "mcp_transport_failed":
            return code
    return "mcp_transport_failed"


def invoke_selected(deployment: StdioDeployment, selection: SelectedTool | None,
                    arguments=None, *, environment, timeout_seconds, cancelled):
    """Execute discovery or exactly one previously admitted selected tool.

    selection=None performs discovery only. A selection requires the same tool
    document on this connection before its single tools/call. Discovery failure
    never returns an empty or complete catalog. No request retry is performed.
    """
    if os.name != "posix":
        raise MCPBridgeFailure("mcp_platform_not_implemented")
    if not isinstance(deployment, StdioDeployment):
        raise TypeError("deployment must be a StdioDeployment")
    if selection is not None and not isinstance(selection, SelectedTool):
        raise TypeError("selection must be a SelectedTool")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds < float("inf") or not callable(cancelled)):
        raise PluginError("mcp_deadline_invalid")
    if selection is None and arguments is not None:
        raise PluginError("mcp_discovery_has_no_arguments")
    args = None if selection is None else selection.arguments(arguments)
    child_env = private_environment(environment)
    try:
        if cancelled():
            raise MCPBridgeFailure("mcp_cancelled")
        sdk = _sdk()
        anyio, ClientSession, types, _, RawResult = sdk
        state = _State(discovery=selection is None)

        async def exchange():
            async with _stdio(deployment, child_env, state, sdk) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as session:
                    initialized = await session.initialize()
                    if initialized.protocolVersion != PROTOCOL_VERSION:
                        raise MCPBridgeFailure("mcp_protocol_version_unsupported")
                    if initialized.capabilities.tools is None:
                        raise MCPBridgeFailure("mcp_tools_not_supported")
                    observed = await _list_all(session, deployment, sdk)
                    if selection is None:
                        if len(canonical(observed)) > deployment.max_result_bytes:
                            raise MCPBridgeFailure("mcp_result_limit_exceeded")
                        state.observed = True
                        state.terminal = observed
                        return observed
                    matches = [tool for tool in observed["tools"]
                               if tool["name"] == selection.remote_name]
                    if len(matches) != 1:
                        raise MCPBridgeFailure("mcp_selected_tool_missing")
                    selection.require_same_document(matches[0])
                    # Public low-level SDK request: no high-level relist/retry or
                    # validation that discards a complete isError/invalid response.
                    request = types.ClientRequest(types.CallToolRequest(
                        params=types.CallToolRequestParams(name=selection.remote_name, arguments=args)))
                    raw = (await session.send_request(request, RawResult)).model_dump(
                        mode="json", by_alias=True, exclude_unset=True)
                    state.observed = True
                    state.terminal = result_envelope(selection, raw, max_bytes=deployment.max_result_bytes)
                    return state.terminal

        async def watch_cancel():
            while not state.observed:
                if cancelled():
                    raise MCPBridgeFailure("mcp_cancelled")
                await anyio.sleep(0.05)

        async def bounded():
            with anyio.fail_after(timeout_seconds):
                async with anyio.create_task_group() as group:
                    group.start_soon(watch_cancel)
                    try:
                        return await exchange()
                    finally:
                        group.cancel_scope.cancel()

        try:
            return anyio.run(bounded)
        except Exception as exc:
            if state.result_received:
                try:
                    return result_envelope(
                        selection, state.buffered_result,
                        max_bytes=deployment.max_result_bytes)
                except Exception as invalid:
                    raise MCPBridgeFailure(_failure_code(invalid),
                        dispatched=state.dispatched, observed=True) from None
            observed = state.observed or (state.discovery and state.discovery_response_received)
            code = ("mcp_response_unknown" if state.dispatched and not observed else
                    "mcp_remote_protocol_error" if state.protocol_error_code is not None else
                    _failure_code(exc))
            if state.terminal is not None and code in {"mcp_cancelled", "mcp_deadline_exceeded"}:
                return state.terminal
            raise MCPBridgeFailure(code, dispatched=state.dispatched, observed=observed,
                                   protocol_code=state.protocol_error_code) from None
    finally:
        child_env.clear()

