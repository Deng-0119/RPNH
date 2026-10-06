"""Finite author/compiler acceptance; no Registry, run, provider or executor."""
from copy import deepcopy
from dataclasses import fields as dataclass_fields, replace
import json

import pytest

from cpn.components.basic import CONFIG_SCHEMA, CONFIG_SCHEMA_ID, lower_operation
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_calls import call_contract, call_sites
from cpn.rpnh.control_eval import evaluate_expression
from cpn.rpnh.control_ir import ControlIR, ControlIRCapabilityError, ControlIRError, PROOF_KEY
from cpn.rpnh.executable_net import load_compiled_net, load_compiled_control_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration, RegistrationError
from cpn.rpnh.petri_contracts import PNFragment


INT = {"kind": "Int"}
BOOL = {"kind": "Bool"}
RATIONAL = {"kind": "Rational"}
TEXT = {"kind": "Text"}
DATA = "application/control_test_data/v1"
CONFIG = "application/control_test_config/v1"
EXECUTOR = "test/finite-author/v1"


def lit(value, spec=INT):
    return {"op": "literal", "type": spec, "value": value}


def op(name, *args):
    return {"op": name, "args": list(args)}


def read(name, *path):
    return {"op": "read", "name": name, "path": list(path)}


def binding(value, spec=INT, *, source="source-a", version=1, stream=None):
    origin = ({"kind": "head", "source": source, "stream": stream, "identity": "state", "version": version}
              if stream else {"kind": "immutable", "ref": {"schema": "application/author_input/v1",
                            "source": source, "identity": "input", "version": version}})
    return {"type": spec, "value": value, "origin": origin}


def contract():
    return {"schema_version": "rpnh/finite_atomic/v1", "effect": "pure",
            "config_type": {"kind": "Record", "fields": {"threshold": INT}},
            "input_schemas": {"request": DATA}, "output_schemas": {"result": DATA},
            "outcomes": {"complete": [{"port": "result", "minimum": 1, "maximum": 1}]}}


def must_not_execute(*args, **kwargs):
    raise AssertionError("compiler must never invoke executor or terminal/effect code")


def registration(rewrite=None, atomic=None, config_schema=None):
    reg = Registration()
    reg.register_schema(CONFIG_SCHEMA_ID, CONFIG_SCHEMA)
    reg.register_schema(DATA, {"$id": DATA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    reg.register_schema(CONFIG, {"$id": CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
                                 **({"type": "object"} if config_schema is None else config_schema)})
    identity = {"implementation_id": "offline_finite_author_test", "revision": "v1"}
    def lower(config, context):
        fragment = lower_operation(config, context)
        return rewrite(fragment) if rewrite else fragment
    reg.register_component("operation", lower, identity=identity, contracts={"config_schema": CONFIG_SCHEMA_ID})
    reg.register_executor(EXECUTOR, must_not_execute, identity=identity,
                          contracts={"config_schema": CONFIG, "control_ir": contract() if atomic is None else atomic})
    reg.register_tool("test/terminal/v1", must_not_execute, identity=identity, contracts={"config_schema": CONFIG})
    return reg


def author_document():
    module = {"schema_version": "rpnh/module_declaration/v1", "name": "FiniteAuthor",
        "components": [{"name": "step", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {}, "ports": [{"name": "request", "direction": "input", "schema": DATA},
                                        {"name": "result", "direction": "output", "schema": DATA}],
            "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request"],
                "outputs": ["result"], "config": {},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]}],
        "links": [], "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": "test/terminal/v1", "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {}},
        "required_schemas": [CONFIG_SCHEMA_ID, CONFIG, DATA], "budgets": {}}
    return {"schema_version": "rpnh/control_ir/v1", "backend": "business_pn/v1", "module": module,
            "bindings": {"capacity": binding(10)}, "atoms": [{"component": "step", "operation": "run",
                "config": {"threshold": op("add", op("floor", op("mul", lit("0.6", RATIONAL), read("capacity"))), lit(1))},
                "guard": op("gt", read("capacity"), lit(0)),
                "arcs": [{"kind": "consume_occurrence", "port": "request", "weight": 1},
                         {"kind": "emit_occurrence", "port": "result", "weight": 1, "outcome": "complete"}],
                "routes": {"complete": ["result"]}}], "calls": [], "continuations": [], "closures": []}


def compile_document(document, reg=None):
    return compile_module(ControlIR.from_dict(document), registration() if reg is None else reg)


def test_public_compiler_consumes_typed_values_in_real_lowering_and_wire():
    author = ControlIR.from_dict(author_document())
    compiled = compile_module(author, registration())
    assert compiled.source.components[0].operations[0].config == {"threshold": 7}
    assert compiled.fragments["step"].operations[0].config == {"threshold": 7}
    assert compiled.symbolic.operations[0].config == {"threshold": 7}
    assert [arc.weight for arc in compiled.symbolic.arcs] == [1, 1]
    proof = compiled.source.designer_constraints[PROOF_KEY]
    assert proof["author"] == author.to_dict()
    assert proof["evaluations"][0]["read_set"]["heads"] == []
    assert len(proof["evaluations"][0]["read_set"]["immutable_refs"]) == 1
    assert load_compiled_net(compiled.to_json()).to_dict() == compiled.to_dict()
    compiled.validate_products("step.run", "complete", {"step.result": ["ok"]})


def test_author_input_is_detached_and_direct_constructor_rechecked():
    document = author_document()
    author = ControlIR.from_dict(document)
    document["bindings"]["capacity"]["value"] = 40
    detached = author.to_dict()
    detached["bindings"]["capacity"]["value"] = 50
    assert compile_module(author, registration()).symbolic.operations[0].config["threshold"] == 7
    invalid = author.to_dict()
    invalid["schema_version"] = "review_ir/0.4"
    with pytest.raises(ControlIRError, match="unsupported_control_ir_version"):
        compile_module(ControlIR(json.dumps(invalid)), registration())


@pytest.mark.parametrize("change,code", [
    (lambda d: d["atoms"][0]["config"].update(threshold=lit(True, BOOL)), "config_type_mismatch"),
    (lambda d: d["bindings"]["capacity"].update(value=True), "type_mismatch"),
    (lambda d: d["atoms"][0]["config"].clear(), "config_fields_mismatch"),
    (lambda d: d["atoms"][0]["routes"].clear(), "outcome_routes_incomplete"),
    (lambda d: d["atoms"][0]["routes"].update(complete=[]), "outcome_route_mismatch"),
    (lambda d: d["atoms"][0].update(guard=lit(1)), "guard_bool_required"),
    (lambda d: d["atoms"][0]["arcs"][0].update(weight=True), "invalid_arc_weight"),
    (lambda d: d["atoms"][0]["arcs"][0].update(kind="read"), "unknown_arc_semantics"),
    (lambda d: d["atoms"].clear(), "atomic_inventory_incomplete"),
    (lambda d: d["module"]["components"][0]["operations"][0]["config"].update(old=1), "typed_config_placeholder_required"),
    (lambda d: d["atoms"][0]["config"].update(threshold=op("floor_div", lit(1), lit(0))), "division_by_zero"),
    (lambda d: d["atoms"][0]["config"].update(threshold=op("add", lit(1, {"kind": "Int", "unit": {"bytes": 1}}), lit(1))), "unit_mismatch"),
])
def test_public_compiler_rejects_precise_author_errors(change, code):
    document = author_document()
    change(document)
    with pytest.raises(ControlIRError) as caught:
        compile_document(document)
    assert caught.value.code == code


@pytest.mark.parametrize("change,construct,status", [
    (lambda d: d["bindings"].update(capacity=binding(10, stream="config")), "mutable_read_set", "NEW_VERSION_REQUIRED"),
    (lambda d: d["atoms"][0].update(guard=lit(False, BOOL)), "runtime_guard", "EXACT_ADAPTER_REQUIRED"),
    (lambda d: d.update(backend="execution/v1"), "author_module", "UNSUPPORTED"),
    (lambda d: d["atoms"][0]["arcs"].append({"kind": "observe_fact", "port": "request", "weight": 1}), "observe_fact", "EXACT_ADAPTER_REQUIRED"),
    (lambda d: d["continuations"].append({"contract": "durable/v1", "binding": "exact-case"}), "continuations", "NEW_VERSION_REQUIRED"),
    (lambda d: d["closures"].append({"contract": "abnormal/v1", "binding": "exact-child"}), "closures", "NEW_VERSION_REQUIRED"),
])
def test_public_compiler_reports_capability_without_invoking_lower(change, construct, status):
    document = author_document()
    change(document)
    reg = registration(rewrite=lambda fragment: must_not_execute())
    with pytest.raises(ControlIRCapabilityError) as caught:
        compile_document(document, reg)
    assert any(c.construct == construct and c.status == status for c in caught.value.capabilities)


@pytest.mark.parametrize("rewrite", [
    lambda f: replace(f, arcs=tuple(replace(a, mode="read") if a.direction == "input" else a for a in f.arcs)),
    lambda f: replace(f, arcs=tuple(replace(a, weight=2) if a.direction == "input" else a for a in f.arcs)),
])
def test_actual_host_lower_cannot_erase_occurrence_semantics(rewrite):
    with pytest.raises(ControlIRError, match="atomic_arc_lowering_mismatch"):
        compile_document(author_document(), registration(rewrite))


def test_typed_lower_rejects_hidden_operation_and_transition():
    def rewrite(fragment):
        hidden = replace(fragment.operations[0], name="hidden")
        transition = replace(fragment.transitions[0], name="hidden", operation="hidden")
        arcs = tuple(replace(arc, transition="hidden") for arc in fragment.arcs)
        return replace(fragment, operations=(*fragment.operations, hidden),
                       transitions=(*fragment.transitions, transition), arcs=(*fragment.arcs, *arcs))
    with pytest.raises(ControlIRError, match="undeclared_atomic_operation"):
        compile_document(author_document(), registration(rewrite))


@pytest.mark.parametrize("surface", ["lower", "fragment", "symbolic"])
def test_typed_int_config_never_inherits_legacy_numeric_coercion(surface):
    if surface == "lower":
        def rewrite(fragment):
            return replace(fragment, operations=(replace(fragment.operations[0], config={"threshold": 7.0}),))
        with pytest.raises(ControlIRError, match="typed_config_lowering_mismatch"):
            compile_document(author_document(), registration(rewrite))
    else:
        document = compile_document(author_document()).to_dict()
        target = document["fragments"]["step"] if surface == "fragment" else document["symbolic"]
        target["operations"][0]["config"]["threshold"] = 7.0
        with pytest.raises(ValueError):
            load_compiled_net(document)


@pytest.mark.parametrize("bad_kind", ["tuple", "subclass", "nested_subclass"])
def test_typed_wire_rejects_coercible_python_containers_before_callbacks(bad_kind):
    document = compile_document(author_document()).to_dict()
    if bad_kind == "tuple":
        document["source"]["designer_constraints"][PROOF_KEY]["author"]["atoms"] = tuple(
            document["source"]["designer_constraints"][PROOF_KEY]["author"]["atoms"])
    else:
        class Evil(dict):
            def items(self):
                raise AssertionError("typed input must not invoke Mapping callbacks")
        if bad_kind == "subclass":
            document = Evil(document)
        else:
            document["source"]["designer_constraints"][PROOF_KEY]["author"]["bindings"] = Evil(
                document["source"]["designer_constraints"][PROOF_KEY]["author"]["bindings"])
    with pytest.raises(ControlIRError, match="non_json_data"):
        load_compiled_net(document)


def test_typed_compile_and_public_loader_preserve_local_recursive_schema_scopes():
    schema = {"type": "object", "properties": {"threshold": {"$ref": "urn:control-local"}},
              "definitions": {"node": {"$id": "urn:control-local", "anyOf": [
                  {"type": "integer"}, {"type": "array", "items": {"$ref": "urn:control-local"}}]}}}
    document = author_document()
    atomic = contract()
    spec = {"kind": "Sequence", "item": {"kind": "Sequence", "item": INT}}
    atomic["config_type"]["fields"]["threshold"] = spec
    document["atoms"][0]["config"]["threshold"] = lit([[1], [2, 3]], spec)
    compiled = compile_document(document, registration(atomic=atomic, config_schema=schema))
    assert load_compiled_net(compiled.to_json()).symbolic.operations[0].config == {"threshold": [[1], [2, 3]]}


@pytest.mark.parametrize("surface", ["compile", "wire"])
def test_typed_remote_schema_references_reject_without_retrieval(surface):
    from referencing.exceptions import Unresolvable
    schema = {"$ref": "https://example.invalid/never-retrieve-control-schema.json"}
    if surface == "compile":
        with pytest.raises(ControlIRError, match="schema_reference_unresolvable"):
            compile_document(author_document(), registration(config_schema=schema))
    else:
        document = compile_document(author_document()).to_dict()
        document["registrations"]["schema"][CONFIG]["schema"].update(schema)
        with pytest.raises((ValueError, Unresolvable)):
            load_compiled_net(document)


@pytest.mark.parametrize("target", ["author", "evaluations", "config", "arc"])
def test_rehydration_checks_typed_proof_without_host_callbacks(target):
    document = compile_document(author_document()).to_dict()
    proof = document["source"]["designer_constraints"][PROOF_KEY]
    if target == "author": proof["author"]["bindings"]["capacity"]["value"] = 11
    elif target == "evaluations": proof["evaluations"][0]["value"] = 99
    elif target == "config": document["source"]["components"][0]["operations"][0]["config"]["threshold"] = True
    else: document["fragments"]["step"]["arcs"][0]["mode"] = "read"
    with pytest.raises(ValueError):
        load_compiled_net(document)


@pytest.mark.parametrize("proof", [None, {}, {"author": {"schema_version": "review_ir/0.4"},
                                           "evaluations": [], "capabilities": []}])
def test_present_but_malformed_typed_proof_cannot_downgrade_to_legacy(proof):
    document = compile_document(author_document()).to_dict()
    document["source"]["designer_constraints"][PROOF_KEY] = proof
    with pytest.raises(ControlIRError):
        load_compiled_net(document)


@pytest.mark.parametrize("loader", [load_compiled_net, load_compiled_control_net])
@pytest.mark.parametrize("key", ["rpnh_control_ir_v2", "rpnh_control_ir_v1\n"])
def test_unknown_reserved_typed_family_version_never_falls_back(loader, key):
    document = compile_document(author_document()).to_dict()
    for surface in (document["source"], document["symbolic"]):
        surface["designer_constraints"][key] = surface["designer_constraints"].pop(PROOF_KEY)
    with pytest.raises(ControlIRError, match="unsupported_control_proof_version"):
        loader(document)


def test_explicit_typed_reader_requires_proof_while_legacy_default_stays_usable():
    document = compile_document(author_document()).to_dict()
    assert load_compiled_control_net(document).source.designer_constraints[PROOF_KEY]
    for surface in (document["source"], document["symbolic"]):
        del surface["designer_constraints"][PROOF_KEY]
    assert load_compiled_net(document).symbolic.operations[0].config == {"threshold": 7}
    with pytest.raises(ControlIRError, match="control_proof_required"):
        load_compiled_control_net(document)
    legacy = compile_module(ModuleDeclaration.from_dict(author_document()["module"]), registration()).to_dict()
    assert load_compiled_net(legacy).source.name == "FiniteAuthor"
    with pytest.raises(ControlIRError, match="control_proof_required"):
        load_compiled_control_net(legacy)


@pytest.mark.parametrize("loader", [load_compiled_net, load_compiled_control_net])
@pytest.mark.parametrize("surface", ["root", "source", "constraints"])
def test_typed_detection_never_calls_nonstring_key_comparison(loader, surface):
    key_name = {"root": "source", "source": "designer_constraints", "constraints": PROOF_KEY}[surface]
    class Collision:
        def __hash__(self): return hash(key_name)
        def __eq__(self, other): raise AssertionError("no data-key callback")
    content = {Collision(): 1}
    if surface == "source": content = {"source": content}
    elif surface == "constraints": content = {"source": {"designer_constraints": content}}
    with pytest.raises(ValueError):
        loader(content)


def test_atomic_registration_rejects_before_gateway_or_executor():
    reg = Registration()
    reg.bind_gateway(must_not_execute)
    bad = contract()
    bad["config_type"] = {"kind": "Python"}
    with pytest.raises(RegistrationError, match="unknown_type"):
        reg.register_executor(EXECUTOR, must_not_execute, identity={"id": "test"}, contracts={"control_ir": bad})
    assert reg.declarations() == ()


def test_existing_module_v1_without_typed_opt_in_remains_usable():
    document = author_document()["module"]
    compiled = compile_module(ModuleDeclaration.from_dict(document), registration())
    assert compiled.source.components[0].operations[0].config == {}
    assert PROOF_KEY not in compiled.source.designer_constraints


def test_legacy_host_fragment_validator_keeps_original_call_signature():
    calls = []
    class LegacyFragment(PNFragment):
        def validate(self, context, registration):
            calls.append(context.component)
            return super().validate(context, registration)
    def rewrite(fragment):
        return LegacyFragment(**{field.name: getattr(fragment, field.name) for field in dataclass_fields(fragment)})
    compile_module(ModuleDeclaration.from_dict(author_document()["module"]), registration(rewrite))
    assert calls == ["step"]


@pytest.mark.parametrize("site", call_sites())
def test_all_seven_complete_calls_reach_explicit_backend_rejection(site):
    document = author_document()
    document["calls"] = [call_contract(site)]
    with pytest.raises(ControlIRCapabilityError) as caught:
        compile_document(document, registration(rewrite=lambda fragment: must_not_execute()))
    assert caught.value.capabilities[-1].construct == "typed_call"


CALL_INPUTS = [(site, i) for site in call_sites() for i, _ in enumerate(call_contract(site)["inputs"])]
CALL_RETURNS = [(site, i) for site in call_sites() for i, _ in enumerate(call_contract(site)["returns"])]


@pytest.mark.parametrize("site,index", CALL_INPUTS)
@pytest.mark.parametrize("field", ["type", "mode", "source_path", "default", "omission_allowed"])
def test_each_of_46_call_inputs_is_checked_by_real_compiler(site, index, field):
    document = author_document()
    call = call_contract(site)
    port = call["inputs"][index]
    port[field] = True if field == "omission_allowed" else "invalid"
    document["calls"] = [call]
    with pytest.raises(ControlIRError) as caught:
        compile_document(document)
    assert caught.value.code == "call_input_mismatch"
    assert caught.value.path.endswith("." + field)


@pytest.mark.parametrize("site,index", CALL_RETURNS)
@pytest.mark.parametrize("field", ["required_payload_fields", "parent_destination", "parent_case_updates"])
def test_each_of_35_call_returns_is_checked_by_real_compiler(site, index, field):
    document = author_document()
    call = call_contract(site)
    call["returns"][index][field] = [] if field != "parent_destination" else "wrong.destination"
    document["calls"] = [call]
    with pytest.raises(ControlIRError) as caught:
        compile_document(document)
    assert caught.value.code == "call_return_mismatch"


@pytest.mark.parametrize("site", call_sites())
@pytest.mark.parametrize("collection", ["inputs", "returns"])
def test_missing_call_port_or_return_never_becomes_backend_success(site, collection):
    document = author_document()
    call = call_contract(site)
    call[collection].pop()
    document["calls"] = [call]
    with pytest.raises(ControlIRError) as caught:
        compile_document(document)
    assert caught.value.code == "call_" + collection + "_incomplete"


def test_catalog_carries_all_seven_46_35_and_exact_additional_context():
    assert len(call_sites()) == 7 and len(CALL_INPUTS) == 46 and len(CALL_RETURNS) == 35
    assert "neutral_attempt_ref" in call_contract("N.adapter_call")["additional_call_context"]
    assert next(p for p in call_contract("Q.normal_call")["inputs"] if p["port"] == "neutral_max")["source_path"] == 1
    assert next(p for p in call_contract("C.request_summary")["inputs"] if p["port"] == "neutral_max")["source_path"] == 2
