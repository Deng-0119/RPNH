"""Importable native RPNH plugin; no model client or private actor loop."""
from __future__ import annotations
import socket
from .constants import OPERATION_TIMEOUT_SECONDS, PLUGIN_RESULT_BYTES, PLUGIN_VERSION, TOOLS
from .io import receive, send


def _json_plain(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {k: _json_plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_plain(v) for v in value]
    return value


def _invoke(tool, context, arguments):
    context.check_cancelled()
    timeout = min(float(OPERATION_TIMEOUT_SECONDS), context.remaining_seconds)
    if timeout <= 0:
        raise TimeoutError("native operation deadline reached before dispatch")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(context.config["endpoint"])
        send(conn, {"run_id": context.config["run_id"], "tool": tool,
                    "operation_id": context.operation_id, "invocation_id": context.invocation_id,
                    "firing_id": context.firing_id, "call_id": context.call_id,
                    "arguments": _json_plain(arguments)})
        response = receive(conn)
    context.check_cancelled()
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("AutomationBench bridge: " + str(response.get("error") if isinstance(response, dict) else "invalid reply"))
    if not isinstance(response.get("result"), str) or type(response.get("request_sequence")) is not int:
        raise ValueError("invalid native bridge result")
    return {"raw_result": response["result"], "witness_request_sequence": response["request_sequence"]}


def api_search_handler(context, arguments):
    return _invoke("api_search", context, arguments)


def api_fetch_handler(context, arguments):
    return _invoke("api_fetch", context, arguments)


def base64_encode_handler(context, arguments):
    return _invoke("base64_encode", context, arguments)


def factory():
    from cpn.plugins.api import PluginDefinition, PluginOperation
    handlers = dict(zip(TOOLS, (api_search_handler, api_fetch_handler, base64_encode_handler)))
    output = {"type": "object", "additionalProperties": False,
              "properties": {"raw_result": {"type": "string"},
                             "witness_request_sequence": {"type": "integer", "minimum": 1}},
              "required": ["raw_result", "witness_request_sequence"]}
    operations = tuple(PluginOperation(name=name, description=f"Execute upstream {name} exactly once.",
                        input_schema={"type": "object"}, output_schema=output, handler=handlers[name],
                        effect="external_write" if name == "api_fetch" else "pure",
                        timeout_seconds=OPERATION_TIMEOUT_SECONDS, max_result_bytes=PLUGIN_RESULT_BYTES)
                       for name in TOOLS)
    return PluginDefinition(name="ab_api", version=PLUGIN_VERSION, operations=operations,
               config_schema={"type": "object", "additionalProperties": False,
                 "properties": {"endpoint": {"type": "string", "pattern": "^/"},
                                "run_id": {"type": "string", "minLength": 1}},
                 "required": ["endpoint", "run_id"]})


def configuration(endpoint: str, run_id: str):
    return {"schema_version": "rpnh/plugins/v1", "plugins": [
        {"name": "ab_api", "entry_point": "ab_api", "version": PLUGIN_VERSION,
         "config": {"endpoint": endpoint, "run_id": run_id}, "environment": []}]}


def bindings(schemas):
    return {"executor": {"tools": {
        row["function"]["name"]: {"selector": "ab_api/" + row["function"]["name"],
                                  "description": row["function"]["description"],
                                  "input_schema": row["function"]["parameters"]}
        for row in schemas}, "admitted_effects": ["pure", "external_read", "external_write"]}}
