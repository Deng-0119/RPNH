"""Importable managed operation with a closed model input surface."""
from __future__ import annotations

import socket
from pathlib import Path
from cpn.plugins.api import PluginDefinition, PluginOperation, json_copy, validate
from .bridge import (IDENTITY_KEYS, MAX_SOURCE, STATUSES, check_arguments,
                     receive_frame, send_frame)
from .planning import PLAN_INPUT_SCHEMA, validate_plan

INPUT_SCHEMA = {"type": "object", "additionalProperties": False,
                "properties": {"source": {"type": "string", "maxLength": MAX_SOURCE},
                               "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}},
                "required": ["source"]}
CONFIG_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"endpoint": {"type": "string", "pattern": "^/"},
                                "trial_id": {"type": "string", "minLength": 1}},
                 "required": ["endpoint", "trial_id"]}
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"status": {"enum": list(STATUSES)}, "stdout": {"type": "string"},
                    "stderr": {"type": "string"}, "exit_code": {"type": ["integer", "null"]},
                    "execution_id": {"type": ["string", "null"]},
                    "identity": {"type": "object", "additionalProperties": False,
                                 "properties": {key: {"type": "string"} for key in IDENTITY_KEYS},
                                 "required": list(IDENTITY_KEYS)},
                    "admission_granularity": {"const": "script"}},
                 "required": ["status", "stdout", "stderr", "exit_code", "execution_id",
                              "identity", "admission_granularity"]}


def erp_python_handler(context, arguments):
    context.check_cancelled()
    arguments = json_copy(arguments)
    check_arguments(arguments)
    identity = {key: getattr(context, key) for key in IDENTITY_KEYS if key != "trial_id"}
    identity["trial_id"] = context.config["trial_id"]
    dispatched = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(min(5, context.remaining_seconds))
            connection.connect(context.config["endpoint"])
            dispatched = True  # A partial send must also be treated conservatively.
            send_frame(connection, {"identity": identity, "arguments": arguments,
                                    "remaining_seconds": min(3600, context.remaining_seconds)})
            connection.settimeout(0.1)
            answer = receive_frame(connection, context.check_cancelled)
            answer = validate(OUTPUT_SCHEMA, answer)
            if answer["identity"] != identity:
                raise ValueError("bridge returned a different effect identity")
    except Exception:
        if not dispatched:
            raise
        # An external write may have committed. Let the existing managed worker
        # failure path publish outcome_unknown and block further Registry calls.
        raise RuntimeError("ERP execution outcome unknown; do not replay") from None
    if answer["status"] == "unknown":
        raise RuntimeError("ERP execution outcome unknown; do not replay")
    return answer


def validate_plan_handler(context, arguments):
    context.check_cancelled()
    return validate_plan(json_copy(arguments))


def factory():
    return PluginDefinition(name="erp_bench", version="0.1.0", config_schema=CONFIG_SCHEMA,
        operations=(PluginOperation(name="erp_python", description=(
            "Execute one Python script in the trial's isolated ERP container. Admission and evidence "
            "cover the script, not each ERP transaction. An unknown result must not be replayed."),
            input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA, handler=erp_python_handler,
            effect="external_write", timeout_seconds=3600, max_result_bytes=1048576),
            PluginOperation(name="validate_plan", description="Check arithmetic over supplied visible observations.",
                input_schema=PLAN_INPUT_SCHEMA, output_schema={"type": "object"},
                handler=validate_plan_handler, effect="pure", timeout_seconds=30, max_result_bytes=1048576)))


def configuration(endpoint, trial_id):
    if not Path(endpoint).is_absolute():
        raise ValueError("endpoint must be absolute")
    config = validate(CONFIG_SCHEMA, {"endpoint": str(endpoint), "trial_id": trial_id})
    return {"schema_version": "rpnh/plugins/v1", "plugins": [{"name": "erp_bench",
            "entry_point": "erp_bench", "version": "0.1.0", "config": config, "environment": []}]}


def bindings():
    return {"executor": {"tools": {"erp_python": {"selector": "erp_bench/erp_python"},
                                    "validate_plan": {"selector": "erp_bench/validate_plan"}},
                         "admitted_effects": ["pure", "external_write"]}}
