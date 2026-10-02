"""Candidate RPNH SDK bridge: one admitted plugin operation -> one Bank call.

No LLM, routing loop, model-supplied business identity, SQL oracle, or retries.
Full Registry/worker validation is covered by local integration tests, not by
this kit's self-contained contract tests.
"""
from __future__ import annotations
from pathlib import Path
import socket
from .office_cases import PLUGIN_SLOTS
from .constants import OFFICE_EFFECTS
from .jsonio import recv_frame, send_frame

VERSION = "0.2.0"


class NativeBridgeError(RuntimeError):
    pass


def invoke_endpoint(*, endpoint: str, run_id: str, operation_id: str,
                    invocation_id: str, firing_id: str, call_id: str,
                    arguments: dict,
                    timeout: float = 30) -> dict:
    if not Path(endpoint).is_absolute(): raise ValueError("endpoint must be an absolute Unix socket")
    if not 0 < timeout <= 120: raise ValueError("invalid bridge deadline")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(endpoint)
        send_frame(conn, {"run_id": run_id, "operation_id": operation_id,
                          "invocation_id": invocation_id, "firing_id": firing_id,
                          "call_id": call_id,
                          "arguments": arguments})
        response = recv_frame(conn)
    if not isinstance(response, dict) or response.get("ok") is not True:
        code = response.get("error", "invalid_response") if isinstance(response, dict) else "invalid_response"
        raise NativeBridgeError(str(code))
    if set(response) != {"ok", "result", "request_sequence"} or not isinstance(response["result"], str):
        raise NativeBridgeError("invalid successful response")
    return {"raw_result": response["result"], "witness_request_sequence": response["request_sequence"]}


def native_handler(context, arguments):
    context.check_cancelled()
    # These identities are supplied by PluginContext, not by model JSON.
    result = invoke_endpoint(endpoint=context.config["endpoint"], run_id=context.config["run_id"],
                             operation_id=context.operation_id, invocation_id=context.invocation_id,
                             firing_id=context.firing_id, call_id=context.call_id,
                             arguments=arguments,
                             timeout=min(30.0, context.remaining_seconds))
    context.check_cancelled()
    return result


def definition_for(plugin_name: str):
    from cpn.plugins.api import PluginDefinition, PluginOperation
    if plugin_name not in PLUGIN_SLOTS: raise ValueError("unknown host plugin identity")
    # Public parameter descriptions are supplied from upstream ToolCatalog by
    # the live driver. This envelope does not pre-filter errors using hidden gold.
    operations = tuple(PluginOperation(
        name=name, description=f"Execute the upstream office tool {name} once.",
        input_schema={"type": "object"},
        output_schema={"type": "object", "additionalProperties": False,
                       "properties": {"raw_result": {"type": "string"},
                                      "witness_request_sequence": {"type": "integer", "minimum": 1}},
                       "required": ["raw_result", "witness_request_sequence"]},
        handler=native_handler, effect=effect, timeout_seconds=60,
        max_result_bytes=4 * 1024 * 1024,
    ) for name, effect in sorted(OFFICE_EFFECTS.items()))
    return PluginDefinition(name=plugin_name, version=VERSION, operations=operations,
        config_schema={"type": "object", "additionalProperties": False,
                       "properties": {"endpoint": {"type": "string", "pattern": "^/"},
                                      "run_id": {"type": "string", "minLength": 1}},
                       "required": ["endpoint", "run_id"]})


def manager_factory(): return definition_for("ha_manager")
def admin_factory(): return definition_for("ha_admin")
def policy_factory(): return definition_for("ha_policy")
def extra_factory(): return definition_for("ha_extra")


def configuration(endpoints: dict[str, str], run_id: str) -> dict:
    if not endpoints or set(endpoints) - set(PLUGIN_SLOTS): raise ValueError("registered host slot endpoints are required")
    return {"schema_version": "rpnh/plugins/v1", "plugins": [
        {"name": name, "entry_point": name, "version": VERSION,
         "config": {"endpoint": endpoints[name], "run_id": run_id}, "environment": []}
        for name in sorted(endpoints)]}
