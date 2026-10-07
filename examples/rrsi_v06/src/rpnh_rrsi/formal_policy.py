"""Independent registered-host policy trials for the formal local RRSI benchmark.

The application owns only the policy fixture, prompt construction, and score
interpretation.  The public RPNH run, Registry, Petri net, and registered-host
ledger remain the authority for the execution itself.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator, ValidationError

from cpn.components.registered_host_llm import (
    EXECUTION_PROVENANCE_DOCUMENT,
    EXECUTION_PROVENANCE_SCHEMA,
    HOST_PROTOCOL,
    make_registered_llm_host_bindings,
    registered_host_execution_route,
    registered_host_llm_schema_data,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, BindingContext, PNFragment, PlaceDeclaration,
    PortBinding, PortDeclaration, TransitionDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.run import OwnerInput, start_run


from .formal_execution import child_failure_refs, close_child, execute_child

SCHEMA_PREFIX = "application/rrsi_v06/formal_policy"
POLICY_REQUEST = f"{SCHEMA_PREFIX}/request/v1"
POLICY_CONFIG = f"{SCHEMA_PREFIX}/config/v1"
POLICY_MODEL_CONFIG = f"{SCHEMA_PREFIX}/model_config/v1"
POLICY_STATE = f"{SCHEMA_PREFIX}/state/v1"
POLICY_RESPONSE = f"{SCHEMA_PREFIX}/response/v1"
POLICY_RESULT = f"{SCHEMA_PREFIX}/result/v1"
COMPONENT_KEY = f"{SCHEMA_PREFIX}/component/v1"
PREPARE_EXECUTOR_KEY = f"{SCHEMA_PREFIX}/prepare_executor/v1"
MODEL_EXECUTOR_KEY = f"{SCHEMA_PREFIX}/model_executor/v1"
GRADE_EXECUTOR_KEY = f"{SCHEMA_PREFIX}/grade_executor/v1"
TERMINAL_KEY = f"{SCHEMA_PREFIX}/terminal/v1"
BUCKET_ID = "rrsi-formal-policy-operations"


class FormalPolicyContractError(ValueError):
    """A candidate Policy source violates the frozen application contract."""


def _closed(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "properties": dict(properties), "required": list(required)}


_ID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
_NONEMPTY = {"type": "string", "minLength": 1}
_PATH = {"type": "string", "pattern": "^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*[.]py$",
         "maxLength": 160}
_SOURCE_FILE = _closed({
    "path": _PATH,
    "content": {"type": "string", "maxLength": 20000},
    "mode": {"type": "integer", "minimum": 0, "maximum": 511},
}, ("path", "content", "mode"))
_MESSAGES = {"type": "array", "minItems": 1, "items": {
    "type": "object", "additionalProperties": True,
    "properties": {"role": _NONEMPTY, "content": {"type": "string"}},
    "required": ["role", "content"],
}}
_USAGE = {"anyOf": [{"type": "object"}, {"type": "null"}]}
_RESPONSE_ENVELOPE = _closed({
    "protocol": {"const": "llm_response_envelope/v1"},
    "text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "tool_calls": {"type": "array", "items": _closed({
        "id": _NONEMPTY, "name": _NONEMPTY, "arguments": {"type": "string"},
    }, ("id", "name", "arguments"))},
    "reasoning_content": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "finish_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "usage": _USAGE,
}, ("protocol", "tool_calls"))

SCHEMAS = {
    POLICY_CONFIG: {"$id": POLICY_CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
                    **_closed({}, ())},
    POLICY_MODEL_CONFIG: {"$id": POLICY_MODEL_CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
                          **_closed({
                              "provider_attempt_limit": {"const": 1},
                              "resource_bounds": _closed({
                                  "max_llm_attempts": {"const": 1},
                                  "max_tool_turns": {"const": 0},
                              }, ("max_llm_attempts", "max_tool_turns")),
                          }, ("provider_attempt_limit", "resource_bounds"))},
    POLICY_REQUEST: {"$id": POLICY_REQUEST, "$schema": "http://json-schema.org/draft-07/schema#",
                     **_closed({
                         "protocol_id": _ID, "split": _ID,
                         "evaluation_id": _ID, "candidate_id": _ID,
                         "task_id": _ID, "repetition": {"type": "integer", "minimum": 0},
                         "attempt": {"type": "integer", "minimum": 0},
                         "occurrence_id": _ID,
                         "source_files": {"type": "array", "minItems": 1, "maxItems": 16,
                                          "items": _SOURCE_FILE},
                         "raw_task_input": {}, "expected": {"type": "integer"},
                         "weight": {"type": "number", "minimum": 0},
                     }, ("protocol_id", "split", "evaluation_id", "candidate_id",
                         "task_id", "repetition",
                         "attempt", "occurrence_id", "source_files", "raw_task_input",
                         "expected", "weight"))},
    POLICY_STATE: {"$id": POLICY_STATE, "$schema": "http://json-schema.org/draft-07/schema#",
                   **_closed({
                       "request": {"$ref": POLICY_REQUEST}, "messages": _MESSAGES,
                       "actual_module_path": _NONEMPTY,
                   }, ("request", "messages", "actual_module_path"))},
    POLICY_RESPONSE: {"$id": POLICY_RESPONSE, "$schema": "http://json-schema.org/draft-07/schema#",
                      **_closed({
                          "state": {"$ref": POLICY_STATE}, "response": _RESPONSE_ENVELOPE,
                      }, ("state", "response"))},
    POLICY_RESULT: {"$id": POLICY_RESULT, "$schema": "http://json-schema.org/draft-07/schema#",
                    **_closed({
                        "protocol_id": _ID, "split": _ID,
                        "evaluation_id": _ID, "candidate_id": _ID,
                        "task_id": _ID, "repetition": {"type": "integer", "minimum": 0},
                        "attempt": {"type": "integer", "minimum": 0}, "occurrence_id": _ID,
                        "actual_module_path": _NONEMPTY,
                        "execution_trace": {"type": "string", "maxLength": 2000},
                        "usage": _USAGE, "reward": {"enum": [0, 1]},
                        "weight": {"type": "number", "minimum": 0},
                        "expected": {"type": "integer"},
                        "output": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    }, ("protocol_id", "split", "evaluation_id", "candidate_id",
                        "task_id", "repetition",
                        "attempt", "occurrence_id", "actual_module_path", "execution_trace",
                        "usage", "reward", "weight", "expected", "output"))},
}


def _schema_body(schema: Mapping[str, Any]) -> dict[str, Any]:
    """RPNH v1 resource schemas permit local JSON pointers only."""
    return {key: json.loads(canonical_json(schema[key]))
            for key in ("type", "additionalProperties", "properties", "required")}


# State and response resources are independently published content schemas, so
# their frozen request/state members are inlined rather than cross-schema refs.
SCHEMAS[POLICY_STATE]["properties"]["request"] = _schema_body(SCHEMAS[POLICY_REQUEST])
SCHEMAS[POLICY_RESPONSE]["properties"]["state"] = _schema_body(SCHEMAS[POLICY_STATE])


@dataclass(slots=True)
class _PolicyRuntime:
    run_root: Path
    result_sink: list[dict[str, Any]]


_RUNTIME_LOCK = RLock()
_RUNTIME_CONTEXTS: dict[str, _PolicyRuntime] = {}


def _validate_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("formal policy request must be an object")
    value = json.loads(canonical_json(dict(request)))
    try:
        Draft7Validator(SCHEMAS[POLICY_REQUEST]).validate(value)
    except ValidationError as exc:
        raise ValueError(f"formal policy request is malformed: {exc.message}") from exc
    expected_occurrence = (f"{value['protocol_id']}:{value['evaluation_id']}:"
                           f"{value['task_id']}:{value['repetition']}:{value['attempt']}")
    if value["occurrence_id"] != expected_occurrence:
        raise ValueError("formal policy occurrence identity is inconsistent")
    paths: set[str] = set()
    for source in value["source_files"]:
        path = _normal_relative_path(source["path"])
        if path in paths:
            raise ValueError("formal policy source files repeat a path")
        paths.add(path)
    if "policy.py" not in paths:
        raise ValueError("formal policy source files must include policy.py")
    return value


def _normal_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("formal policy source path is not normalized relative")
    return path.as_posix()


def _runtime_for(value: Mapping[str, Any]) -> _PolicyRuntime:
    request = value.get("request") if isinstance(value.get("request"), Mapping) else value
    if isinstance(request, Mapping) and isinstance(request.get("state"), Mapping):
        request = request["state"].get("request")
    occurrence_id = request.get("occurrence_id") if isinstance(request, Mapping) else None
    if not isinstance(occurrence_id, str):
        raise RuntimeError("formal policy value lacks occurrence identity")
    with _RUNTIME_LOCK:
        runtime = _RUNTIME_CONTEXTS.get(occurrence_id)
    if runtime is None:
        raise RuntimeError("formal policy runtime context is not active")
    return runtime


_POLICY_EXPRESSION_NODES = (
    ast.Return, ast.List, ast.Tuple, ast.Dict, ast.Constant, ast.Name,
    ast.Call, ast.Load,
)


def _restricted_build_messages(policy_path: Path):
    """Load the one allowed function without exposing host-process globals."""
    try:
        tree = ast.parse(policy_path.read_text(encoding="utf-8"),
                         filename=str(policy_path))
    except OSError:
        raise
    except (SyntaxError, UnicodeError) as exc:
        raise FormalPolicyContractError(
            "formal policy.py cannot be parsed") from exc
    body = list(tree.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.FunctionDef):
        raise FormalPolicyContractError(
            "formal policy.py may define only build_messages")
    function = body[0]
    args = function.args
    if (function.name != "build_messages" or function.decorator_list
            or function.returns is not None or function.type_comment is not None
            or args.posonlyargs or len(args.args) != 1
            or args.args[0].arg != "raw" or args.args[0].annotation is not None
            or args.vararg is not None or args.kwonlyargs or args.kw_defaults
            or args.kwarg is not None or args.defaults):
        raise FormalPolicyContractError(
            "formal policy build_messages signature is not allowed")
    function_body = list(function.body)
    if (function_body and isinstance(function_body[0], ast.Expr)
            and isinstance(function_body[0].value, ast.Constant)
            and isinstance(function_body[0].value.value, str)):
        function_body = function_body[1:]
    if len(function_body) != 1 or not isinstance(function_body[0], ast.Return):
        raise FormalPolicyContractError(
            "formal policy build_messages must contain one return")
    for node in ast.walk(function_body[0]):
        if not isinstance(node, _POLICY_EXPRESSION_NODES):
            raise FormalPolicyContractError(
                f"formal policy expression node is not allowed: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in {"raw", "str"}:
            raise FormalPolicyContractError(
                "formal policy expression reads an unauthorized name")
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or node.func.id != "str"
                    or len(node.args) != 1 or node.keywords):
                raise FormalPolicyContractError(
                    "formal policy may call only str(value)")
    sandbox: dict[str, Any] = {"__builtins__": {"str": str}, "str": str}
    code = compile(tree, str(policy_path), "exec")
    exec(code, sandbox, sandbox)
    value = sandbox.get("build_messages")
    if not callable(value):
        raise FormalPolicyContractError(
            "formal policy.py must define build_messages(raw)")
    return value


def _validate_provider_messages(messages: object) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise FormalPolicyContractError(
            "formal policy build_messages(raw) must return nonempty messages")
    try:
        normalized = json.loads(canonical_json(messages))
    except (TypeError, ValueError) as exc:
        raise FormalPolicyContractError(
            "formal policy provider messages are not canonical JSON") from exc
    try:
        Draft7Validator(_MESSAGES).validate(normalized)
    except ValidationError as exc:
        raise FormalPolicyContractError(
            f"formal policy provider messages are malformed: {exc.message}") from exc
    return normalized


def _build_and_validate_messages(build_messages, raw: object) -> list[dict[str, Any]]:
    """Evaluate the restricted candidate and classify its own runtime defect."""
    try:
        messages = build_messages(raw)
    except TypeError as exc:
        raise FormalPolicyContractError(
            "formal policy build_messages(raw) could not evaluate the frozen input: "
            f"{type(exc).__name__}: {exc}") from exc
    return _validate_provider_messages(messages)


def _materialize_and_build(request: Mapping[str, Any], run_root: Path) -> tuple[list[dict[str, Any]], str]:
    for source in request["source_files"]:
        relative = _normal_relative_path(source["path"])
        target = (run_root / relative).resolve()
        if not target.is_relative_to(run_root):
            raise ValueError("formal policy source escaped its run root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source["content"], encoding="utf-8")
        target.chmod(source["mode"])
    policy_path = (run_root / "policy.py").resolve()
    if not policy_path.is_file() or not policy_path.is_relative_to(run_root):
        raise ValueError("formal policy.py was not materialized under the run root")
    build_messages = _restricted_build_messages(policy_path)
    messages = _build_and_validate_messages(
        build_messages, request["raw_task_input"])
    return messages, str(policy_path)


def _lower_policy(_config: Mapping[str, Any], context: BindingContext) -> PNFragment:
    return PNFragment(
        places=(PlaceDeclaration("request", POLICY_REQUEST, capacity=1),
                PlaceDeclaration("state", POLICY_STATE, capacity=1),
                PlaceDeclaration("response", POLICY_RESPONSE, capacity=1),
                PlaceDeclaration("result", POLICY_RESULT, capacity=1)),
        transitions=(TransitionDeclaration("prepare", "prepare"),
                     TransitionDeclaration("model", "model"),
                     TransitionDeclaration("grade", "grade")),
        arcs=(ArcDeclaration("request", "prepare", "input"),
              ArcDeclaration("state", "prepare", "output", mode="produce", outcome="complete"),
              ArcDeclaration("state", "model", "input"),
              ArcDeclaration("response", "model", "output", mode="produce", outcome="complete"),
              ArcDeclaration("response", "grade", "input"),
              ArcDeclaration("result", "grade", "output", mode="produce", outcome="complete")),
        ports=tuple(PortBinding(port.name, port.name) for port in context.ports),
        operations=context.operations,
        internal_ports=(PortDeclaration("state_out", "output", POLICY_STATE),
                        PortDeclaration("state_in", "input", POLICY_STATE),
                        PortDeclaration("response_out", "output", POLICY_RESPONSE),
                        PortDeclaration("response_in", "input", POLICY_RESPONSE)),
        internal_bindings=(PortBinding("state_out", "state"), PortBinding("state_in", "state"),
                           PortBinding("response_out", "response"), PortBinding("response_in", "response")),
    )


def _input_value(execution) -> dict[str, Any]:
    if len(execution.operation.inputs) != 1:
        raise ValueError("formal policy operation requires one input")
    value = json.loads(execution.operation.inputs[0].artifact.payload)
    if not isinstance(value, dict):
        raise ValueError("formal policy input is not an object")
    return value


def _publish(output_name: str, value: Mapping[str, Any], execution, gateway,
             sink: list[dict[str, Any]]) -> OperationExecutionResult:
    schemas = {"state_out": POLICY_STATE, "response_out": POLICY_RESPONSE, "result": POLICY_RESULT}
    schema = schemas[output_name]
    ports = [port for port in execution.operation.spec.output_ports if port.content_schema_id == schema]
    bindings = [item for item in execution.operation.operation_binding.output_port_bindings
                if ports and item.port_id == ports[0].port_id]
    if len(ports) != 1 or len(bindings) != 1:
        raise ValueError("formal policy output binding is ambiguous")
    context = execution.operation.canonical.context
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(bindings[0].output_binding_ref, context.activation_ref),
        payload=canonical_json(dict(value)), media_type="application/json", content_schema_ref=schema,
        summary="RRSI formal policy output", lifetime_ref=context.invocation_ref,
        descriptors={"output_outcome_id": "complete", "output_port_id": bindings[0].port_id,
                     "place": bindings[0].place},
        idempotency_key="rrsi-formal-policy:" + str(execution.operation_execution_lease_ref.version_id) + ":" + output_name,
    ))
    artifact = gateway.verify_resource(execution.operation.canonical, ref)
    if output_name == "result":
        sink.append({"resource_ref": _resource_ref(ref), "value": json.loads(canonical_json(dict(value)))})
    return OperationExecutionResult(outputs=(artifact,), selected_outcome_id="complete")


def _prepare_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    request = _validate_request(_input_value(execution))
    runtime = _runtime_for(request)
    messages, module_path = _materialize_and_build(request, runtime.run_root)
    return _publish("state_out", {"request": request, "messages": messages,
                                   "actual_module_path": module_path}, execution, gateway, runtime.result_sink)


def _model_executor(*, execution, gateway, resources, host_context):
    del resources
    state = _input_value(execution)
    runtime = _runtime_for(state)
    registered = getattr(host_context, "registered_llm", None)
    if registered is None:
        raise RuntimeError("formal policy model firing lacks registered_llm/v1")
    response = registered.request({"protocol": HOST_PROTOCOL, "messages": state["messages"],
                                   "tools": [], "tool_choice": "none", "placeholders": []})
    try:
        document = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("formal policy registered-host response is not JSON") from exc
    if not isinstance(document, dict) or document.get("protocol") != "llm_response_envelope/v1":
        raise ValueError("formal policy response is not a canonical envelope")
    return _publish("response_out", {"state": state, "response": document}, execution, gateway, runtime.result_sink)


def _trace_text(response: Mapping[str, Any], reason: str) -> str:
    text = response.get("text")
    shown = text if isinstance(text, str) else ""
    return (reason + (": " + shown if shown else ""))[:2000]


def _grade_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    envelope = _input_value(execution)
    runtime = _runtime_for(envelope)
    state, response = envelope["state"], envelope["response"]
    request = state["request"]
    output: int | None = None
    reason = "invalid_response"
    text = response.get("text") if isinstance(response, Mapping) else None
    if response.get("finish_reason") == "length":
        reason = "response_was_truncated"
    elif response.get("tool_calls") != []:
        reason = "response_contains_tool_calls"
    elif isinstance(text, str):
        try:
            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate JSON field")
                    value[key] = item
                return value
            decoded = json.loads(text, object_pairs_hook=unique_object)
            if (isinstance(decoded, dict) and set(decoded) == {"timeout"}
                    and isinstance(decoded["timeout"], int) and not isinstance(decoded["timeout"], bool)):
                output = decoded["timeout"]
                reason = "parsed"
            else:
                reason = "response_text_is_not_exact_timeout_object"
        except (json.JSONDecodeError, ValueError):
            reason = "response_text_is_not_json"
    else:
        reason = "response_text_is_missing"
    result = {
        **{key: request[key] for key in ("protocol_id", "split", "evaluation_id",
                                        "candidate_id", "task_id", "repetition",
                                        "attempt", "occurrence_id")},
        "actual_module_path": state["actual_module_path"], "execution_trace": _trace_text(response, reason),
        "usage": response.get("usage") if isinstance(response, Mapping) and isinstance(response.get("usage"), Mapping) else None,
        "reward": int(output == request["expected"]), "weight": request["weight"],
        "expected": request["expected"], "output": output,
    }
    return _publish("result", result, execution, gateway, runtime.result_sink)


def policy_registration() -> Registration:
    """The fixed complete inventory required by process-global RPNH registration."""
    registration = Registration()
    for schema_id, document in SCHEMAS.items():
        registration.register_schema(schema_id, document)
    registration.register_schema(EXECUTION_PROVENANCE_SCHEMA, EXECUTION_PROVENANCE_DOCUMENT)
    registration.register_component(COMPONENT_KEY, _lower_policy,
                                    identity={"implementation_id": "rrsi_v06.formal_policy", "revision": "v1"},
                                    contracts={"config_schema": POLICY_CONFIG})
    registration.register_executor(PREPARE_EXECUTOR_KEY, _prepare_executor,
                                   identity={"implementation_id": "rrsi_v06.formal_policy.prepare", "revision": "v1"},
                                   contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                                              "config_schema": POLICY_CONFIG})
    registration.register_executor(MODEL_EXECUTOR_KEY, _model_executor,
                                   identity={"implementation_id": "rrsi_v06.formal_policy.model", "revision": "v1"},
                                   contracts={"transport": "llm", "input_ports": None, "output_ports": None,
                                              "config_schema": POLICY_MODEL_CONFIG, "host_protocols": [HOST_PROTOCOL],
                                              "resource_read_contracts": [ResourceReadContract(metadata_only=False,
                                                  context_origins=("petri_operation",), origin_kinds=("provider_request",),
                                                  require_producer_invocation=True, require_provenance_binding=True).to_dict()],
                                              "provider_request_schema": "runtime/llm_request_envelope/v1"})
    registration.register_executor(GRADE_EXECUTOR_KEY, _grade_executor,
                                   identity={"implementation_id": "rrsi_v06.formal_policy.grade", "revision": "v1"},
                                   contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                                              "config_schema": POLICY_CONFIG})
    registration.register_tool(TERMINAL_KEY, dict,
                               identity={"implementation_id": "rrsi_v06.formal_policy.terminal", "revision": "v1"},
                               contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def build_policy_module() -> ModuleDeclaration:
    budget = {"bucket_id": BUCKET_ID, "budget_scope": "module", "finalization_scope": None}
    def operation(name: str, executor: str, source: str, output: str, *, model: bool = False) -> dict[str, Any]:
        return {"name": name, "executor": executor, "inputs": [source], "outputs": [output],
                "tools": [], "config": {"provider_attempt_limit": 1,
                "resource_bounds": {"max_llm_attempts": 1, "max_tool_turns": 0}} if model else {},
                "request_port": source if model else None, "budget_binding": budget,
                "outcomes": [{"name": "complete", "products": [{"port": output}]}]}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "RRSIV06FormalPolicyV1",
        "components": [{"name": "policy", "key": COMPONENT_KEY, "config_schema": POLICY_CONFIG,
                        "config": {}, "ports": [{"name": "request", "direction": "input", "schema": POLICY_REQUEST},
                        {"name": "result", "direction": "output", "schema": POLICY_RESULT}], "operations": [
                        operation("prepare", PREPARE_EXECUTOR_KEY, "request", "state_out"),
                        operation("model", MODEL_EXECUTOR_KEY, "state_in", "response_out", model=True),
                        operation("grade", GRADE_EXECUTOR_KEY, "response_in", "result")] }], "links": [],
        "entry": {"request": {"component": "policy", "port": "request"}},
        "exit": {"result": {"component": "policy", "port": "result"}},
        "terminal": {"key": TERMINAL_KEY, "source": {"component": "policy", "port": "result"},
                     "operation": "grade", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": list(SCHEMAS), "budgets": {}, "budget_buckets": [{**budget, "max_attempts": 3}],
    })


def policy_catalog() -> SchemaCatalog:
    schemas, types = registered_host_llm_schema_data()
    return SchemaCatalog(schemas=schemas, types=types)


def _resource_ref(ref) -> dict[str, str]:
    return {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}


def _version_ref(ref) -> dict[str, str]:
    return {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id), "version_id": str(ref.version_id)}


def _run_policy_trial_unchecked(*, run_dir: Path,
                                request: Mapping[str, Any], selection,
                                llm_input_port=None, interruption_requested=None) -> dict[str, Any]:
    """Low-level trial primitive; callers must not label it a formal campaign."""
    from cpn.llm_adapters.config import LLMExecutionSelection
    if not isinstance(selection, LLMExecutionSelection):
        raise TypeError("formal policy requires LLMExecutionSelection")
    destination = Path(run_dir).resolve()
    if destination.exists():
        raise ValueError("formal policy run root must be absent")
    value = _validate_request(request)
    occurrence_id = value["occurrence_id"]
    module, sink = build_policy_module(), []
    with _RUNTIME_LOCK:
        if occurrence_id in _RUNTIME_CONTEXTS:
            raise ValueError("formal policy occurrence is already active")
        _RUNTIME_CONTEXTS[occurrence_id] = _PolicyRuntime(destination, sink)
    created_port = False
    event_loop = None
    owner = None
    primary_error = None
    try:
        if llm_input_port is None:
            from cpn.llm_adapters import build_llm_input_port
            llm_input_port = build_llm_input_port(selection, destination_run_root=destination)
            created_port = True
        bindings = make_registered_llm_host_bindings(
            selection.input_target, provider_backend_config=registered_host_execution_route(selection),
            provider_backend_schema_ref=EXECUTION_PROVENANCE_SCHEMA,
            transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                                "response_adapter_ref": "llm_response_envelope/v1"},
            prompt={"messages": [{"role": "system", "content": "RRSI formal policy trial"}]},
            tool_catalog={"tools": []})
        owner_input = OwnerInput(POLICY_REQUEST, canonical_json(value), "RRSI formal policy request")
        owner = start_run(module, policy_registration(), run_dir=destination, task_input=owner_input,
                          entry_inputs={"request": owner_input}, budgets=ModuleBudgetDeclaration(
                              tuple(module.to_dict()["budget_buckets"]), ("rpnh/module_declaration/v1",), 3, 0, 3, 0),
                          model_condition=selection.input_target.model_condition,
                          owner_statement="Execute one independent RRSI policy trial",
                          command_id=f"rrsi-v06:formal-policy:{occurrence_id}:fresh", catalog=policy_catalog(),
                          host_execution_bindings=bindings)
        event_loop = OwnerEventLoop(owner, destination / "owner.sock")
        harness_result = execute_child(
            owner=owner, event_loop=event_loop, llm_input_port=llm_input_port,
            interruption_requested=interruption_requested)
        if (harness_result.stop_reason != "terminal" or not harness_result.goal_reached
                or harness_result.terminal_evidence_ref is None or len(harness_result.goal_resource_refs) != 1):
            raise RuntimeError("formal policy did not reach one terminal result")
        output_ref = harness_result.goal_resource_refs[0]
        matches = [row for row in sink if row["resource_ref"] == _resource_ref(output_ref)]
        if len(matches) != 1:
            raise RuntimeError("formal policy sink differs from terminal Registry output")
        trace = [item.transition_id for item in harness_result.operation_execution_trace]
        if trace != ["policy.prepare", "policy.model", "policy.grade"]:
            raise RuntimeError("formal policy did not execute its exact transition sequence")
        return {"schema_version": "rrsi_v06/formal_policy_envelope/v1",
                "run_refs": {"task_ref": _version_ref(owner.identity.task_ref), "run_ref": _version_ref(owner.identity.run_ref)},
                "terminal_evidence_ref": _version_ref(harness_result.terminal_evidence_ref),
                "output_ref": _resource_ref(output_ref), "transition_trace": trace,
                "result": matches[0]["value"]}
    except BaseException as exc:
        primary_error = exc
        child_failure_refs(exc, owner)
        raise
    finally:
        try:
            close_child(owner=owner, event_loop=event_loop,
                        input_port=llm_input_port, created_port=created_port,
                        primary_error=primary_error)
        finally:
            with _RUNTIME_LOCK:
                _RUNTIME_CONTEXTS.pop(occurrence_id, None)



def _formal_request(protocol, request: Mapping[str, Any], selection) -> dict[str, Any]:
    from .formal_protocol import (
        FormalProtocol, formal_protocol_mapping, validate_execution_selection,
    )

    if not isinstance(protocol, FormalProtocol):
        raise TypeError("formal policy trial requires a validated FormalProtocol")
    validate_execution_selection(protocol, selection)
    value = _validate_request(request)
    if value["protocol_id"] != protocol.protocol_id:
        raise ValueError("formal policy request changed the frozen protocol")
    manifests = {
        item.split: item for item in (
            protocol.calibration, protocol.evolve, protocol.heldout,
            protocol.export)
    }
    manifest = manifests.get(value["split"])
    if manifest is None:
        raise ValueError("formal policy request uses an unknown split")
    task = next((item for item in manifest.tasks
                 if item.task_id == value["task_id"]), None)
    if task is None:
        raise ValueError("formal policy request changed the frozen task set")
    protocol_value = formal_protocol_mapping(protocol)
    task_value = next(item for item in
                      protocol_value["task_manifests"][value["split"]]["tasks"]
                      if item["task_id"] == value["task_id"])
    if (value["raw_task_input"] != task_value["raw_input"]
            or value["expected"] != task.expected
            or value["weight"] != task.weight):
        raise ValueError("formal policy request changed task input or grading")
    maximum_repetitions = (
        protocol.method["calibration_repetitions"]
        if value["split"] == "calibration"
        else protocol.method["repetitions"])
    if value["repetition"] >= maximum_repetitions or value["attempt"] != 0:
        raise ValueError("formal policy occurrence is outside the frozen plan")
    fixture = protocol_value["source_fixture"]["files"]
    expected_files = {item["path"]: item for item in fixture}
    actual_files = {item["path"]: item for item in value["source_files"]}
    if (len(actual_files) != len(value["source_files"])
            or set(actual_files) != set(expected_files)):
        raise ValueError("formal policy request changed the frozen source members")
    for path, expected_file in expected_files.items():
        actual = actual_files[path]
        if actual["mode"] != expected_file["mode"]:
            raise ValueError("formal policy request changed a frozen source mode")
        if path != "policy.py" and actual["content"] != expected_file["content"]:
            raise ValueError("formal policy request changed a fixed source member")
    if (value["candidate_id"] == "H0"
            and actual_files["policy.py"]["content"]
            != expected_files["policy.py"]["content"]):
        raise ValueError("H0 policy source differs from the frozen fixture")
    return value


def run_policy_trial(*, run_dir: Path, protocol, request: Mapping[str, Any],
                     selection, llm_input_port=None, interruption_requested=None) -> dict[str, Any]:
    """Execute one protocol-bound formal Policy-agent trial."""
    destination = Path(run_dir).resolve()
    value = _formal_request(protocol, request, selection)
    return _run_policy_trial_unchecked(
        run_dir=destination, request=value, selection=selection,
        llm_input_port=llm_input_port,
        interruption_requested=interruption_requested)


__all__ = ("POLICY_CONFIG", "POLICY_MODEL_CONFIG", "POLICY_REQUEST", "POLICY_RESPONSE", "POLICY_RESULT", "POLICY_STATE",
           "SCHEMAS", "build_policy_module", "policy_catalog", "policy_registration", "run_policy_trial")
