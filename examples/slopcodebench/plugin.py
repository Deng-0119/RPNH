"""One admitted RPNH managed call -> one upstream Session command."""
from __future__ import annotations

import socket

from .wire import receive, send

VERSION = "0.1.0"
COMMAND_TIMEOUT = 60
OPERATION_TIMEOUT = 180

COMMAND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"command": {"type": "string", "minLength": 1, "maxLength": 65536}},
    "required": ["command"],
}


def command_handler(context, arguments):
    context.check_cancelled()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(min(OPERATION_TIMEOUT, context.remaining_seconds))
        connection.connect(context.config["endpoint"])
        send(connection, {"checkpoint_key": context.config["checkpoint_key"],
                          "operation_id": context.operation_id,
                          "invocation_id": context.invocation_id,
                          "firing_id": context.firing_id, "call_id": context.call_id,
                          "command": arguments["command"]})
        response = receive(connection)
    context.check_cancelled()
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("SCB command result unavailable; do not retry an uncertain write")
    return response["result"]


def factory():
    from cpn.plugins.api import PluginDefinition, PluginOperation
    result_schema = {"type": "object", "additionalProperties": False,
        "properties": {"stdout": {"type": "string"}, "stderr": {"type": "string"},
                       "exit_code": {"type": "integer"}, "timed_out": {"type": "boolean"},
                       "elapsed": {"type": "number", "minimum": 0},
                       "sequence": {"type": "integer", "minimum": 1},
                       "command_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
        "required": ["stdout", "stderr", "exit_code", "timed_out", "elapsed",
                     "sequence", "command_sha256"]}
    return PluginDefinition(name="scb_session", version=VERSION, operations=(
        PluginOperation(name="command", description=(
            "Run a shell command in the benchmark's current isolated solution workspace. "
            "Use it to inspect, write, edit and test code. stdout/stderr are exact, not a grade."),
            input_schema=COMMAND_SCHEMA, output_schema=result_schema,
            handler=command_handler, effect="external_write",
            timeout_seconds=OPERATION_TIMEOUT, max_result_bytes=16 * 1024 * 1024),),
        config_schema={"type": "object", "additionalProperties": False,
                       "properties": {"endpoint": {"type": "string", "pattern": "^/"},
                                      "checkpoint_key": {"type": "string", "minLength": 1}},
                       "required": ["endpoint", "checkpoint_key"]})


def configuration(endpoint, checkpoint_key):
    return {"schema_version": "rpnh/plugins/v1", "plugins": [
        {"name": "scb_session", "entry_point": "scb_session", "version": VERSION,
         "config": {"endpoint": str(endpoint), "checkpoint_key": checkpoint_key},
         "environment": []}]}


def bindings():
    return {"solve": {"tools": {"session_command": {"selector": "scb_session/command"}},
                      "admitted_effects": ["external_write"]}}
