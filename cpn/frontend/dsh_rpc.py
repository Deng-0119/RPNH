"""Private pipe RPC for DSH over the shared frontend application owner.

The launcher binds one existing session and owns the gateway's lifetime. RPC
never creates sessions, resumes attempts, or retries failed calls. A transport
``id`` correlates replies; the required ``requestId`` identifies an application
submission across reconnects and must be retained by the client on retry.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO

from cpn.rpnh.frontend_application import (
    FrontendError, FrontendGateway, request_identity,
)

PROTOCOL = "rpnh/dsh_frontend/v1"
MAX_FRAME_BYTES = 68 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number.")


def _valid_id(value: Any) -> bool:
    if isinstance(value, str) and 1 <= len(value) <= 160:
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
        return True
    return type(value) is int and abs(value) <= 2**53 - 1


def _encode(value: Any) -> bytes:
    """Encode strict JSON with a byte limit, excluding the line delimiter."""
    encoded = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                               separators=(",", ":"))
    for part in encoder.iterencode(value):
        chunk = part.encode("utf-8")
        if len(encoded) + len(chunk) > MAX_FRAME_BYTES:
            raise FrontendError("frame_too_large", "RPC frame exceeds 68 MiB.", 413)
        encoded.extend(chunk)
    return bytes(encoded)


def _error(identity: Any, code: str, message: str) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "id": identity,
            "error": {"code": code, "message": message}}


class DshFrontendRPC:
    """One bound session, accessed exclusively through FrontendGateway.call."""

    def __init__(self, gateway: FrontendGateway, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("A bound frontend session identity is required.")
        self.gateway = gateway
        self.session_id = session_id

    def _session(self) -> dict[str, Any]:
        rows = [row for row in self.gateway.call("snapshot")
                if row["id"] == self.session_id]
        if len(rows) != 1:
            raise FrontendError("unknown_session", "Bound RPNH session is unavailable.", 404)
        return rows[0]

    def _selection(self, params: dict[str, Any]) -> tuple[str, str | None]:
        current = self._session()["model"]
        selection = params.get("selectionId", current["selection"])
        # Match the application's defaults: explicit selection uses that
        # logical model's default effort; omission retains the current effort.
        if "selectionId" in params:
            profiles = self.gateway.call("configuration")["profiles"]
            profile = next((p for p in profiles if p["selection"] == selection), None)
            if profile is None:
                raise FrontendError("unknown_model", "Unknown RPNH provider/model selection.", 400)
            effort = profile.get("default_reasoning_effort")
        else:
            effort = current.get("reasoning_effort")
        return selection, params.get("reasoningEffort", effort)

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method in {"initialize", "snapshot", "abort"}:
            if params:
                raise FrontendError("invalid_params", "This RPC method takes no parameters.", 400)
            if method == "initialize":
                return {"configuration": self.gateway.call("configuration"),
                        "session": self._session()}
            if method == "snapshot":
                return self._session()
            return {"stopped": self.gateway.call("abort", self.session_id)}
        if method not in {"submit", "command"}:
            raise FrontendError("unknown_method", "Unsupported frontend RPC method.", 400)
        required = {"requestId", "text"} if method == "submit" else {"requestId", "name", "arguments"}
        if (not required <= params.keys()
                or params.keys() - required - {"selectionId", "reasoningEffort"}
                or any(not isinstance(params[key], str) for key in required)
                or ("selectionId" in params and (
                    not isinstance(params["selectionId"], str) or not params["selectionId"]))
                or ("reasoningEffort" in params and (
                    not isinstance(params["reasoningEffort"], str) or not params["reasoningEffort"]))):
            raise FrontendError("invalid_params", "Invalid frontend RPC parameters.", 400)
        if method == "submit" and not params["text"].strip():
            raise FrontendError("invalid_params", "A nonempty text prompt is required.", 400)
        if method == "command" and not params["name"]:
            raise FrontendError("invalid_params", "A command name is required.", 400)
        selection, effort = self._selection(params)
        text = params["text"] if method == "submit" else params["name"] + "\n" + params["arguments"]
        key = request_identity(text, None, {
            "provider": "rpnh", "selection": selection, "reasoning_effort": effort,
        }, params["requestId"])
        options = {"selection_id": selection, "reasoning_effort": effort}
        if method == "submit":
            return {"ordinal": self.gateway.call(
                "submit", self.session_id, text, key, **options)}
        return self.gateway.call(
            "command", self.session_id, params["name"], params["arguments"], key, **options)

    def handle(self, frame: Any) -> dict[str, Any]:
        """Return one correlated result/error; never expose internal exceptions."""
        identity = frame.get("id") if isinstance(frame, dict) else None
        if not _valid_id(identity):
            identity = None
        try:
            try:
                _encode(frame)
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise FrontendError("invalid_frame", "A finite UTF-8 JSON frame is required.", 400) from None
            if (not isinstance(frame, dict)
                    or set(frame) != {"protocol", "id", "method", "params"}
                    or frame["protocol"] != PROTOCOL or identity is None
                    or not isinstance(frame["method"], str)
                    or not isinstance(frame["params"], dict)):
                raise FrontendError("invalid_frame", "Invalid frontend RPC envelope.", 400)
            result = self._dispatch(frame["method"], frame["params"])
            reply = {"protocol": PROTOCOL, "id": identity, "result": result}
            try:
                _encode(reply)
            except FrontendError:
                return _error(identity, "response_too_large", "RPC response exceeds 68 MiB; operation may have completed.")
            return reply
        except FrontendError as exc:
            # FrontendError is the application's deliberately public boundary.
            return _error(identity, exc.code, str(exc))
        except Exception:
            return _error(identity, "application_error", "RPNH could not complete the RPC operation; inspect its private owner state.")

    def serve(self, binary_reader: BinaryIO, binary_writer: BinaryIO) -> None:
        """Serve inherited private pipes; leave both pipes and gateway open.

        Invalid JSON has id=null because no unambiguous envelope exists. An
        oversized line ends serving after its error, without draining/replaying
        an unbounded input. Other malformed lines receive an error and continue.
        EOF with an unterminated frame is rejected without dispatching it.
        """
        while True:
            line = binary_reader.readline(MAX_FRAME_BYTES + 2)
            if not line:
                return
            oversized = len(line.rstrip(b"\n")) > MAX_FRAME_BYTES
            if oversized:
                reply = _error(None, "frame_too_large", "RPC frame exceeds 68 MiB.")
            elif not line.endswith(b"\n"):
                reply = _error(None, "invalid_json", "RPC requires a complete newline-delimited JSON frame.")
            else:
                try:
                    frame = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                       parse_constant=_reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    reply = _error(None, "invalid_json", "Invalid, duplicate, or nonfinite JSON fields.")
                else:
                    reply = self.handle(frame)
            binary_writer.write(_encode(reply) + b"\n")
            binary_writer.flush()
            if oversized:
                return
