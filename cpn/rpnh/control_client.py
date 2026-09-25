"""One-shot local owner-channel client. Never opens a Registry or writer."""
from __future__ import annotations

import json
import math
import socket
from typing import Any, Mapping
from uuid import uuid4

_OMITTED = object()


class ControlProtocolError(ValueError):
    """The newline JSON channel contract was not satisfied."""


class ControlCommandError(RuntimeError):
    def __init__(self, reply: dict[str, Any]):
        self.reply = reply
        super().__init__(f"owner command status: {reply['status']}")


def _utf8_text(value: str) -> str:
    """Reject lone surrogates without echoing untrusted data into errors."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ControlProtocolError("channel strings must be valid UTF-8 text") from exc
    return value


def json_data(value: Any) -> Any:
    """Detach JSON data without stringifying HOST objects."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ControlProtocolError("JSON object keys must be strings")
        return {_utf8_text(key): json_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_data(item) for item in value]
    if type(value) is str:
        return _utf8_text(value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ControlProtocolError("channel values must be finite JSON data")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ControlProtocolError(f"{field} must be a nonempty string")
    return _utf8_text(value)


def serialize_command(command_id: str, command: str,
                      arguments: Mapping[str, Any]) -> bytes:
    if not isinstance(arguments, Mapping):
        raise ControlProtocolError("arguments must be an object")
    envelope = {"command_id": _text(command_id, "command_id"),
                "command": _text(command, "command"), "arguments": json_data(arguments)}
    return (json.dumps(envelope, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def parse_reply(wire: bytes, command_id: str) -> dict[str, Any]:
    """Parse exactly one complete newline frame and match its request identity."""
    try:
        if not isinstance(wire, bytes) or not wire.endswith(b"\n") or wire.count(b"\n") != 1:
            raise ControlProtocolError("reply must be one complete newline JSON frame")
        reply = json_data(json.loads(wire.decode("utf-8")))
    except (UnicodeError, ValueError) as exc:
        raise ControlProtocolError(str(exc)) from exc
    if not isinstance(reply, dict) or set(reply) != {"command_id", "status", "result"}:
        raise ControlProtocolError("reply requires command_id, status, result")
    if reply["command_id"] != command_id:
        raise ControlProtocolError("reply command_id does not match request")
    _text(reply["status"], "status")
    return reply


def message_arguments(target: str, body: str, *, input_revision: Any = _OMITTED) -> dict[str, Any]:
    _text(target, "target")
    if not isinstance(body, str):
        raise ControlProtocolError("body must be text")
    arguments = {"target": target, "body": body}
    if input_revision is not _OMITTED:
        arguments["input_revision"] = json_data(input_revision)
    return arguments


def edit_arguments(*, candidate: Mapping[str, Any], base_net_ref: Mapping[str, str],
                   marking_mapping: Mapping[str, Any], retire_token_refs: list[Any],
                   owner_inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Wire shapes only; full declaration/mapping/adoption authority is owner-owned."""
    for field, value in (("candidate", candidate), ("base_net_ref", base_net_ref),
                         ("marking_mapping", marking_mapping), ("owner_inputs", owner_inputs)):
        if not isinstance(value, Mapping):
            raise ControlProtocolError(f"{field} must be an explicit object")
    if (set(base_net_ref) != {"entity_type", "logical_id", "version_id"}
            or any(not isinstance(v, str) or not v for v in base_net_ref.values())):
        raise ControlProtocolError("base_net_ref requires exact entity_type/logical_id/version_id")
    if not isinstance(retire_token_refs, list):
        raise ControlProtocolError("retire_token_refs must be an explicit array")
    return json_data({"candidate": candidate, "base_net_ref": base_net_ref,
                     "marking_mapping": marking_mapping, "retire_token_refs": retire_token_refs,
                     "owner_inputs": owner_inputs})


class ControlClient:
    def __init__(self, socket_path: str, *, timeout: float = 5.0):
        self.socket_path = _text(socket_path, "socket_path")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number of seconds")
        self.timeout = timeout

    def request(self, command: str, arguments: Mapping[str, Any], *,
                command_id: str | None = None) -> dict[str, Any]:
        command_id = command_id if command_id is not None else str(uuid4())
        wire = serialize_command(command_id, command, arguments)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(self.timeout)
            channel.connect(self.socket_path)
            channel.sendall(wire)
            with channel.makefile("rb") as reader:
                return parse_reply(reader.readline(), command_id)

    def snapshot(self, *, command_id: str | None = None) -> dict[str, Any]:
        reply = self.request("snapshot", {}, command_id=command_id)
        if reply["status"] != "OK":
            raise ControlCommandError(reply)
        if not isinstance(reply["result"], dict):
            raise ControlProtocolError("snapshot result must be an object")
        return reply["result"]

    def message(self, target: str, body: str, *, input_revision: Any = _OMITTED,
                command_id: str | None = None) -> dict[str, Any]:
        return self.request("message", message_arguments(target, body, input_revision=input_revision),
                            command_id=command_id)

    def edit(self, *, candidate: Mapping[str, Any], base_net_ref: Mapping[str, str],
             marking_mapping: Mapping[str, Any], retire_token_refs: list[Any],
             owner_inputs: Mapping[str, Any], command_id: str | None = None) -> dict[str, Any]:
        return self.request("edit", edit_arguments(candidate=candidate, base_net_ref=base_net_ref,
            marking_mapping=marking_mapping, retire_token_refs=retire_token_refs,
            owner_inputs=owner_inputs), command_id=command_id)

    def retract_edit(self, edit_command_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        return self.request("retract_edit", {"edit_command_id": _text(edit_command_id, "edit_command_id")},
                            command_id=command_id)

    def correct_edit(self, edit_command_id: str, *, candidate: Mapping[str, Any],
                     base_net_ref: Mapping[str, str], marking_mapping: Mapping[str, Any],
                     retire_token_refs: list[Any], owner_inputs: Mapping[str, Any],
                     command_id: str | None = None) -> dict[str, Any]:
        arguments = edit_arguments(candidate=candidate, base_net_ref=base_net_ref,
            marking_mapping=marking_mapping, retire_token_refs=retire_token_refs, owner_inputs=owner_inputs)
        arguments["edit_command_id"] = _text(edit_command_id, "edit_command_id")
        return self.request("correct_edit", arguments, command_id=command_id)
