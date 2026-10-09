"""Synthetic pure HOSTs for the existing bounded iteration Module.

No run, scheduler, admission, provider, mutable winner or external state lives
here. Only the existing owner may publish/settle the returned product bytes.
The integer-distance condition is an example, unrelated to frozen RRSI scores.
"""
from __future__ import annotations

import json

from cpn.components.basic import register_basic_components
from cpn.rpnh.iteration_profile import IterationProfile
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.schema_catalog import canonical_json


STATE = "application/synthetic_iteration_state/v1"
CANDIDATE = "application/synthetic_iteration_candidate/v1"
REQUEST = "application/synthetic_iteration_request/v1"
EVALUATION = "application/synthetic_iteration_evaluation/v1"
CONFIG = "application/synthetic_iteration_config/v1"
TERMINAL_CONFIG = "application/synthetic_iteration_terminal_config/v1"
TERMINAL = "example/synthetic_iteration_terminal/v1"
SCORER = "integer_absolute_distance/v1"


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


_STRING = {"type": "string", "minLength": 1}
_INTEGER = {"type": "integer"}
_ROUND = {"type": "integer", "minimum": 1, "maximum": 32}
_RESOURCE = _object({"resource_id": _STRING, "resource_version_id": _STRING})
_BINDING = _object({"entity_type": {"const": "operation_binding/v1"},
                    "logical_id": _STRING, "version_id": _STRING})
_DECISION = _object({
    "outcome": {"enum": ["select", "retain", "stop"]},
    "candidate_selected": {"type": "boolean"},
    "incumbent_ref": _RESOURCE, "candidate_ref": _RESOURCE,
    "evaluation_ref": _RESOURCE, "request_ref": _RESOURCE,
    "scorer_binding_ref": _BINDING,
})
SCHEMAS = {
    STATE: _object({"round": {"type": "integer", "minimum": 0, "maximum": 32},
        "value": _INTEGER, "selected_candidate_ref": _nullable(_RESOURCE),
        "parent_state_ref": _nullable(_RESOURCE), "decision": _nullable(_DECISION)}),
    CANDIDATE: _object({"round": _ROUND, "value": _INTEGER,
        "incumbent_value": _INTEGER, "parent_state_ref": _RESOURCE}),
    REQUEST: _object({"round": _ROUND, "target": _INTEGER,
        "scorer": {"const": SCORER}, "status": {"enum": ["known", "unknown"]},
        "stop": {"type": "boolean"}}),
    EVALUATION: _object({"round": _ROUND, "candidate_ref": _RESOURCE,
        "parent_state_ref": _RESOURCE, "request_ref": _RESOURCE,
        "scorer_binding_ref": _BINDING, "scorer": {"const": SCORER},
        "status": {"enum": ["known", "unknown"]},
        "candidate_score": _nullable(_INTEGER), "incumbent_score": _nullable(_INTEGER),
        "stop": {"type": "boolean"}}),
    CONFIG: _object({}),
    TERMINAL_CONFIG: {"type": "object", "properties": {
        "run_outcome": {"enum": ["complete", "failed"]}}, "additionalProperties": False},
}


def resource_ref(ref):
    return {"resource_id": str(ref.resource_id),
            "resource_version_id": str(ref.resource_version_id)}


def _version_ref(ref):
    return {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id),
            "version_id": str(ref.version_id)}


def _input(execution, schema):
    """Resolve the sole declared port of this example's chosen content schema."""
    ports = {port.port_id for port in execution.operation.spec.input_ports
             if port.content_schema_id == schema}
    matches = [item for item in execution.operation.inputs if item.port_id in ports]
    if len(matches) != 1:
        raise ValueError("synthetic HOST requires one exact claimed input per schema")
    item = matches[0]
    return json.loads(item.consumer_payload), resource_ref(item.resource_ref)


def _component(execution):
    component = execution.operation.firing.transition_id.rsplit(".", 1)[0]
    return component, int(component.split("_")[1])


def propose(*, execution):
    state, state_ref = _input(execution, STATE)
    component, round_number = _component(execution)
    if state["round"] + 1 != round_number:
        raise ValueError("state does not belong to this declared round")
    return "proposed", {
        component + ".incumbent": (canonical_json({**state, "parent_state_ref": state_ref}),),
        component + ".candidate": (canonical_json({"round": round_number,
            "value": state["value"] + 1, "incumbent_value": state["value"],
            "parent_state_ref": state_ref}),),
    }


def evaluate(*, execution):
    candidate, candidate_ref = _input(execution, CANDIDATE)
    request, request_ref = _input(execution, REQUEST)
    component, round_number = _component(execution)
    if not candidate["round"] == request["round"] == round_number:
        raise ValueError("evaluation inputs do not belong to the same declared round")
    known = request["status"] == "known"
    value = {"round": round_number, "candidate_ref": candidate_ref,
        "parent_state_ref": candidate["parent_state_ref"], "request_ref": request_ref,
        "scorer_binding_ref": _version_ref(execution.operation.operation_binding.operation_binding_ref),
        "scorer": SCORER, "status": request["status"], "stop": request["stop"],
        "candidate_score": abs(candidate["value"] - request["target"]) if known else None,
        "incumbent_score": abs(candidate["incumbent_value"] - request["target"]) if known else None}
    return "evaluated", {component + ".evaluation": (canonical_json(value),)}


def select(*, execution):
    incumbent, incumbent_ref = _input(execution, STATE)
    candidate, candidate_ref = _input(execution, CANDIDATE)
    evaluation, evaluation_ref = _input(execution, EVALUATION)
    component, round_number = _component(execution)
    # Business consistency checks after native admission, never admission rules.
    if (not candidate["round"] == evaluation["round"] == round_number
            or incumbent["round"] + 1 != round_number
            or evaluation["candidate_ref"] != candidate_ref
            or not candidate["parent_state_ref"] == incumbent["parent_state_ref"]
                    == evaluation["parent_state_ref"]
            or candidate["incumbent_value"] != incumbent["value"]):
        raise ValueError("selection inputs lack exact same-round candidate/evaluation lineage")
    scores = (evaluation["candidate_score"], evaluation["incumbent_score"])
    if ((evaluation["status"] == "known" and any(type(s) is not int for s in scores))
            or (evaluation["status"] == "unknown" and scores != (None, None))):
        raise ValueError("evaluation status does not match its score evidence")
    selected = evaluation["status"] == "known" and scores[0] < scores[1]
    outcome = "stop" if evaluation["stop"] else "select" if selected else "retain"
    state = {"round": round_number,
        "value": candidate["value"] if selected else incumbent["value"],
        "selected_candidate_ref": candidate_ref if selected else incumbent["selected_candidate_ref"],
        "parent_state_ref": incumbent_ref,
        "decision": {"outcome": outcome, "candidate_selected": selected,
            "incumbent_ref": incumbent_ref, "candidate_ref": candidate_ref,
            "evaluation_ref": evaluation_ref, "request_ref": evaluation["request_ref"],
            "scorer_binding_ref": evaluation["scorer_binding_ref"]}}
    return outcome, {component + (".stop" if outcome == "stop" else ".next"):
                     (canonical_json(state),)}


def _registered_products(execution, gateway, resources, outcome, products):
    """Publish this example's declared bundle through the existing typed ABI."""
    from cpn.rpnh.registry.operations import OperationExecutionResult
    from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
    operation = execution.operation
    context = operation.canonical.context
    bindings = {binding.port_id: binding
                for binding in operation.operation_binding.output_port_bindings}
    component, _ = _component(execution)
    role = component.rsplit('_', 1)[1]
    names = {'proposer': ('incumbent', 'candidate'),
             'evaluator': ('evaluation',), 'selector': ('next', 'stop')}[role]
    # OperationSpecAuthority preserves the template's declared output order.
    # Fused Petri place names are not the original symbolic output names.
    ports = dict(zip((component + '.' + name for name in names),
                     operation.spec.output_ports, strict=True))
    artifacts = []
    for name, payloads in products.items():
        port = ports[name]
        binding = bindings[port.port_id]
        for ordinal, payload in enumerate(payloads):
            ref = resources.publish_bytes(context, PublishResource(
                origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
                payload=payload, media_type='application/json',
                content_schema_ref=port.content_schema_id,
                summary='Synthetic iteration product', lifetime_ref=context.invocation_ref,
                descriptors={'output_outcome_id': outcome,
                    'output_port_id': port.port_id, 'place': binding.place},
                idempotency_key=(f'synthetic-iteration:{execution.operation_execution_lease_ref.version_id}'
                                 f':product:{port.port_id}:{ordinal}')))
            artifacts.append(gateway.verify_resource(operation.canonical, ref))
    return OperationExecutionResult(tuple(artifacts), selected_outcome_id=outcome)


def proposer_host(*, execution, gateway, resources, host_context):
    return _registered_products(execution, gateway, resources, *propose(execution=execution))


def evaluator_host(*, execution, gateway, resources, host_context):
    return _registered_products(execution, gateway, resources, *evaluate(execution=execution))


def selector_host(*, execution, gateway, resources, host_context):
    return _registered_products(execution, gateway, resources, *select(execution=execution))


def _terminal_not_callable(*args, **kwargs):
    raise AssertionError("the existing terminal binding is publication authority, not a callback")


def registration():
    """Explicit trusted HOST installation; no Registry or run is created."""
    result = Registration()
    register_basic_components(result)
    for name, body in SCHEMAS.items():
        result.register_schema(name, {"$id": name,
            "$schema": "http://json-schema.org/draft-07/schema#", **body})
    for role, function in (("proposer", proposer_host), ("evaluator", evaluator_host), ("selector", selector_host)):
        result.register_executor("example/synthetic_iteration_" + role + "/v1", function,
            identity={"implementation_id": "synthetic_iteration_" + role, "revision": "v2"},
            contracts={"transport": "deterministic", "input_ports": None,
                       "output_ports": None, "config_schema": CONFIG})
    result.register_tool(TERMINAL, _terminal_not_callable,
        identity={"implementation_id": "synthetic_iteration_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1", "config_schema": TERMINAL_CONFIG})
    return result


def profile(rounds=2):
    """Inert R1 author data, with native model-call cap independent of rounds."""
    return IterationProfile.from_dict({"schema_version": "rpnh/iteration_profile/v1",
        "template": "propose_evaluate_select/v1", "name": "SyntheticIteration", "rounds": rounds,
        "schemas": {"state": STATE, "candidate": CANDIDATE,
                    "evaluation_request": REQUEST, "evaluation": EVALUATION},
        "roles": {role: {"executor_key": "example/synthetic_iteration_" + role + "/v1",
                         "model_profile_ref": None} for role in ("proposer", "evaluator", "selector")},
        "terminal_key": TERMINAL,
        "terminal_outcomes": {"stop": "complete", "final_select": "complete", "final_retain": "complete"},
        "native_model_call_budget": {"unit": "registered_model_call", "maximum": 1}})
