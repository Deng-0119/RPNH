"""Compiler-only regression for the original boolean/number contract gap."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.components.basic import CONFIG_SCHEMA, CONFIG_SCHEMA_ID, lower_operation
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    DeclarationError, EffectDeclaration, InitialTokenDeclaration, OperationDeclaration, OutcomeDeclaration,
    ProductDeclaration, same_operation_contract,
)
from cpn.rpnh.registration import Registration


DATA_SCHEMA = "application/compiler_json_data/v1"
JSON_CONFIG_SCHEMA = "application/compiler_json_config/v1"
EXECUTOR = "test/compiler-json-executor/v1"
EFFECT = "test/compiler-json-effect/v1"
TERMINAL = "test/compiler-json-terminal/v1"


def _must_not_execute(*_args, **_kwargs):
    raise AssertionError("Contract checking must not execute host business code")


def _registration(rewrite=None, *, initial_values=None):
    registration = Registration()
    registration.register_schema(CONFIG_SCHEMA_ID, CONFIG_SCHEMA)
    for key, kind in ((DATA_SCHEMA, "string"), (JSON_CONFIG_SCHEMA, "object")):
        schema = {
            "$id": key, "$schema": "http://json-schema.org/draft-07/schema#",
            "type": kind,
        }
        if key == DATA_SCHEMA and initial_values is not None:
            schema.pop("type")  # Fusion tests admit each legal JSON payload.
        registration.register_schema(key, schema)

    def lower(config, context):
        fragment = lower_operation(config, context)
        if rewrite is not None:
            fragment = replace(fragment, operations=tuple(
                rewrite(operation) for operation in fragment.operations))
        if initial_values is not None:
            fragment = replace(fragment, places=tuple(replace(place, initial_tokens=tuple(
                InitialTokenDeclaration(schema=place.schema, value=value)
                for value in initial_values.get((context.component, place.name), ())))
                for place in fragment.places))
        return fragment

    identity = {"implementation_id": "test.compiler_json", "revision": "v1"}
    registration.register_component("operation", lower, identity=identity,
                                    contracts={"config_schema": CONFIG_SCHEMA_ID})
    registration.register_executor(EXECUTOR, _must_not_execute, identity=identity,
                                   contracts={"config_schema": JSON_CONFIG_SCHEMA})
    for key in (EFFECT, TERMINAL):
        registration.register_tool(key, _must_not_execute, identity=identity,
                                   contracts={"config_schema": JSON_CONFIG_SCHEMA})
    return registration


def _module(value, target="operation"):
    config = {"nested": [None, {"flag": value}]}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "JsonContract",
        "components": [{
            "name": "step", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": DATA_SCHEMA},
                {"name": "result", "direction": "output", "schema": DATA_SCHEMA},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "config": config if target == "operation" else {},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}],
                              "effects": [{"key": EFFECT, "bindings": {},
                                           "config": config if target == "effect" else {}}]}],
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {}},
        "required_schemas": [CONFIG_SCHEMA_ID, DATA_SCHEMA, JSON_CONFIG_SCHEMA],
        "budgets": {},
    })


def _with_value(operation, value, target):
    config = {"nested": [None, {"flag": value}]}
    if target == "operation":
        return replace(operation, config=config)
    outcome = operation.outcomes[0]
    effect = replace(outcome.effects[0], config=config)
    return replace(operation, outcomes=(replace(outcome, effects=(effect,)),))


def _wire_config(operation, target):
    return (operation if target == "operation"
            else operation["outcomes"][0]["effects"][0])["config"]


@pytest.mark.parametrize("source,changed", [
    (1, True), (True, 1), (0, False), (False, 0), (1.0, True), (0.0, False),
])
@pytest.mark.parametrize("target", ["operation", "effect"])
def test_host_lower_cannot_change_json_boolean_number_type(source, changed, target):
    module = _module(source, target)
    registration = _registration(lambda op: _with_value(op, changed, target))
    with pytest.raises(DeclarationError, match="exactly preserve declared protocols"):
        compile_module(module, registration)


@pytest.mark.parametrize("source,changed", [(1, True), (0, False), (True, 1), (False, 0)])
@pytest.mark.parametrize("target", ["operation", "effect"])
@pytest.mark.parametrize("surface", ["fragment", "symbolic"])
def test_wire_rejects_json_boolean_number_substitution(source, changed, target, surface):
    document = compile_module(_module(source, target), _registration()).to_dict()
    if surface == "fragment":
        operation = document["fragments"]["step"]["operations"][0]
        message = "Explicit operation protocol differs"
    else:
        operation = document["symbolic"]["operations"][0]
        message = "Symbolic net differs"
    _wire_config(operation, target)["nested"][1]["flag"] = changed
    with pytest.raises(DeclarationError, match=message):
        load_compiled_net(document)


@pytest.mark.parametrize("value", [None, True, False, 1, 0, 1.25, "1", [1, True, 0, False]])
@pytest.mark.parametrize("target", ["operation", "effect"])
def test_normal_json_config_compiles_and_round_trips(value, target):
    compiled = compile_module(_module(value, target), _registration())
    document = compiled.to_dict()
    reloaded = load_compiled_net(json.dumps(document))
    # Serialized comparison deliberately distinguishes every JSON primitive.
    assert json.dumps(reloaded.to_dict(), sort_keys=True) == json.dumps(document, sort_keys=True)


@pytest.mark.parametrize("source,changed", [(1, 1.0), (0, -0.0), (1.0, 1)])
@pytest.mark.parametrize("target", ["operation", "effect"])
def test_existing_numeric_equality_survives_lowering_and_wire(source, changed, target):
    module = _module(source, target)
    compiled = compile_module(module, _registration(lambda op: _with_value(op, changed, target)))
    document = compiled.to_dict()
    assert type(_wire_config(document["fragments"]["step"]["operations"][0], target)
                ["nested"][1]["flag"]) is type(changed)
    _wire_config(document["symbolic"]["operations"][0], target)["nested"][1]["flag"] = source
    load_compiled_net(document)


def _bundle():
    return OperationDeclaration(
        "run", EXECUTOR, ("a", "b"), ("x", "y"),
        (OutcomeDeclaration("complete", (ProductDeclaration("x"), ProductDeclaration("y")),
                            (EffectDeclaration("first", config={"v": [1, True]}),
                             EffectDeclaration("second", config={"v": [0, False]}))),
         OutcomeDeclaration("retry", (ProductDeclaration("y"),))),
        tools=("tool-b", "tool-a"), config={"number": 1, "boolean": True, "array": [1, True]},
    )


def test_declared_bundle_order_remains_incidental():
    operation = _bundle()
    reordered = replace(operation, tools=tuple(reversed(operation.tools)), outcomes=tuple(
        replace(outcome, products=tuple(reversed(outcome.products)),
                effects=tuple(reversed(outcome.effects)))
        for outcome in reversed(operation.outcomes)),
        config=dict(reversed(tuple(operation.config.items()))))
    assert same_operation_contract(operation, reordered)


@pytest.mark.parametrize("field", ["inputs", "outputs", "config", "effect_config", "multiplicity"])
def test_ordered_abi_config_arrays_and_effect_multiplicity_stay_exact(field):
    operation = _bundle()
    if field in ("inputs", "outputs"):
        changed = replace(operation, **{field: tuple(reversed(getattr(operation, field)))})
    elif field == "config":
        changed = replace(operation, config=dict(operation.config, array=[True, 1]))
    else:
        outcome = operation.outcomes[0]
        if field == "multiplicity":
            effects = (*outcome.effects, outcome.effects[0])
        else:
            effects = (replace(outcome.effects[0], config={"v": [True, 1]}), outcome.effects[1])
        changed = replace(operation, outcomes=(replace(outcome, effects=effects), operation.outcomes[1]))
    assert not same_operation_contract(operation, changed)


@pytest.mark.parametrize("left,right", [
    (1, 1.5), (2**53 + 1, float(2**53)), ("1", 1), (None, False),
    ([1], (1,)), ({"a": 1}, [["a", 1]]),
])
def test_existing_unequal_numbers_and_container_shapes_stay_unequal(left, right):
    operation = _module(left).components[0].operations[0]
    assert not same_operation_contract(operation, _with_value(operation, right, "operation"))


def test_wire_inventory_reordering_preserves_config_array_order():
    document = compile_module(_module([1, True, 0, False]), _registration()).to_dict()
    reordered = deepcopy(document)
    for key in ("places", "arcs", "required_schemas"):
        reordered["symbolic"][key].reverse()
    reordered["ports"].reverse()
    load_compiled_net(reordered)
    _wire_config(reordered["symbolic"]["operations"][0], "operation")["nested"][1]["flag"].reverse()
    with pytest.raises(DeclarationError, match="Symbolic net differs"):
        load_compiled_net(reordered)


def _fusion_module():
    document = _module(None).to_dict()
    a, b = deepcopy(document["components"][0]), deepcopy(document["components"][0])
    a["name"], b["name"] = "a", "b"
    document["components"] = [a, b]
    document["links"] = [{"source": {"component": "a", "port": "result"},
                          "target": {"component": "b", "port": "request"}}]
    document["entry"]["request"]["component"] = "a"
    document["exit"]["result"]["component"] = "b"
    document["terminal"]["source"]["component"] = "b"
    return ModuleDeclaration.from_dict(document)


def _fusion_registration(left, right):
    return _registration(initial_values={("a", "result"): left, ("b", "request"): right})


@pytest.mark.parametrize("source,changed", [
    (1, True), (0, False), (True, 1), (False, 0),
    ({"nested": [1]}, {"nested": [True]}), ([1, True], [True, 1]),
])
@pytest.mark.parametrize("surface", ["lower", "wire"])
def test_fusion_rejects_boolean_number_change_in_initial_payload(source, changed, surface):
    module = _fusion_module()
    if surface == "lower":
        with pytest.raises(DeclarationError, match="Fusion token/schema/colour/initial"):
            compile_module(module, _fusion_registration([source], [changed]))
    else:
        compiled = compile_module(module, _fusion_registration([source], [source]))
        document = compiled.to_dict()
        assert document["place_aliases"]["b.request"] == "a.result"
        assert "b.request" not in {place["name"] for place in document["symbolic"]["places"]}
        vanished = next(place for place in document["fragments"]["b"]["places"]
                        if place["name"] == "request")
        vanished["initial_tokens"][0]["value"] = changed
        with pytest.raises(DeclarationError, match="Fusion token/schema/colour/initial"):
            load_compiled_net(document)


@pytest.mark.parametrize("left,right", [
    ([1, 2], [2, 1]), ([1, 1], [1.0, 1.0]), ([0], [-0.0]),
    ([{"nested": [1, True]}, None], [None, {"nested": [1.0, True]}]),
])
def test_fusion_keeps_initial_token_multiset_and_numeric_equivalence(left, right):
    compiled = compile_module(_fusion_module(), _fusion_registration(left, right))
    document = compiled.to_dict()
    vanished = next(place for place in document["fragments"]["b"]["places"]
                    if place["name"] == "request")
    vanished["initial_tokens"].reverse()
    load_compiled_net(document)


@pytest.mark.parametrize("left,right", [
    ([1], [1, 1]), ([[1, 2]], [[2, 1]]),
    ([2**53 + 1], [float(2**53)]), ([[1]], [(1,)]),
])
def test_fusion_keeps_multiplicity_payload_order_and_container_mismatches(left, right):
    with pytest.raises(DeclarationError, match="Fusion token/schema/colour/initial"):
        compile_module(_fusion_module(), _fusion_registration(left, right))
