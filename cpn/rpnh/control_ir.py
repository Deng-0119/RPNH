"""Opt-in author ControlIR/v1 -> exact existing Module/Registration lowering.

The supported adapter specializes finite author expressions over immutable
inputs into executor config. It does not implement runtime guards or calls.
Unsupported semantics are validated, reported and refused, never erased.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from .control_calls import validate_call_contract
from .control_eval import evaluate_expression, infer_expression, _environment
from .control_types import (
    BOOL, ControlIRError, canonical_key, data, fail, fields, same_type,
    text, type_spec, typed_value,
)
from .module import ModuleDeclaration


VERSION = "rpnh/control_ir/v1"
ATOMIC_VERSION = "rpnh/finite_atomic/v1"
PROOF_KEY = "rpnh_control_ir_v1"
PROOF_FAMILY = "rpnh_control_ir_"
_ARCS = {"consume_occurrence", "observe_fact", "acquire_access", "release_access",
         "emit_occurrence", "observe_count", "observe_absence", "reserve_quantity",
         "convert_reservation"}


def control_proof_key(constraints, *, required=False):
    keys = [key for key in constraints if key.startswith(PROOF_FAMILY)]
    if keys and keys != [PROOF_KEY]:
        fail("unsupported_control_proof_version", "$.designer_constraints", repr(keys))
    if not keys and required:
        fail("control_proof_required", "$.designer_constraints")
    return PROOF_KEY if keys else None


@dataclass(frozen=True)
class LoweringCapability:
    path: str
    construct: str
    status: str
    reason: str


class ControlIRCapabilityError(ControlIRError):
    def __init__(self, capabilities):
        self.capabilities = tuple(capabilities)
        blocked = next(c for c in capabilities if c.status != "NATIVE")
        super().__init__("lowering_capability_required", blocked.path,
                         f"{blocked.construct}: {blocked.status}: {blocked.reason}")


@dataclass(frozen=True)
class ControlIR:
    """Immutable pure-data author input, accepted by compile_module explicitly."""
    _json: str

    @classmethod
    def from_dict(cls, document):
        value = data(document)
        fields(value, ("schema_version", "backend", "module", "bindings", "atoms",
                       "calls", "continuations", "closures"))
        if value["schema_version"] != VERSION:
            fail("unsupported_control_ir_version", "$.schema_version")
        if value["backend"] not in ("business_pn/v1", "execution/v1"):
            fail("unknown_backend", "$.backend")
        ModuleDeclaration.from_dict(value["module"])
        if any(key.startswith(PROOF_FAMILY) for key in value["module"].get("designer_constraints", {})):
            fail("reserved_control_proof", "$.module.designer_constraints")
        _environment(value["bindings"])
        for name in ("atoms", "calls", "continuations", "closures"):
            if type(value[name]) is not list:
                fail("expected_sequence", "$." + name)
        return cls(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False))

    @classmethod
    def from_json(cls, document):
        if type(document) is not str:
            fail("expected_json_text", "$")
        try:
            return cls.from_dict(json.loads(document))
        except json.JSONDecodeError as exc:
            raise ControlIRError("invalid_json", "$") from exc

    def to_dict(self):
        if type(self._json) is not str:
            fail("expected_json_text", "$")
        return json.loads(self._json)


def validate_atomic_contract(contract, path="$.contracts.control_ir"):
    contract = data(contract, path)
    fields(contract, ("schema_version", "effect", "config_type", "input_schemas",
                      "output_schemas", "outcomes"), path=path)
    if contract["schema_version"] != ATOMIC_VERSION:
        fail("unsupported_atomic_contract_version", path + ".schema_version")
    if contract["effect"] not in ("pure", "physical", "registry_atomic", "engine_protocol"):
        fail("unknown_effect_class", path + ".effect")
    spec = type_spec(contract["config_type"], path + ".config_type")
    if spec["kind"] != "Record":
        fail("config_record_required", path + ".config_type")
    for field in ("input_schemas", "output_schemas"):
        if type(contract[field]) is not dict:
            fail("expected_record", path + "." + field)
        for port, schema in contract[field].items():
            text(port, path + "." + field)
            text(schema, path + "." + field + "." + port)
    outcomes = contract["outcomes"]
    if type(outcomes) is not dict or not outcomes:
        fail("closed_outcome_union_required", path + ".outcomes")
    for tag, products in outcomes.items():
        text(tag, path + ".outcomes")
        if type(products) is not list:
            fail("expected_sequence", path + ".outcomes." + tag)
        seen = set()
        for i, product in enumerate(products):
            p = f"{path}.outcomes.{tag}[{i}]"
            fields(product, ("port", "minimum", "maximum"), path=p)
            port = text(product["port"], p + ".port")
            if port in seen or port not in contract["output_schemas"]:
                fail("invalid_outcome_port", p)
            seen.add(port)
            low, high = product["minimum"], product["maximum"]
            if type(low) is not int or type(high) is not int or not 0 <= low <= high:
                fail("invalid_product_quantity", p)
    return contract


def _routes(routes, contract, path):
    if type(routes) is not dict or routes.keys() != contract["outcomes"].keys():
        fail("outcome_routes_incomplete", path)
    for tag, products in contract["outcomes"].items():
        if type(routes[tag]) is not list:
            fail("invalid_outcome_route", path + "." + tag)
        wanted = sorted(p["port"] for p in products if p["maximum"] > 0)
        if any(type(port) is not str for port in routes[tag]) or sorted(routes[tag]) != wanted:
            fail("outcome_route_mismatch", path + "." + tag)


def _arc(arc, path):
    fields(arc, ("kind", "port", "weight"), ("outcome", "scope", "unit"), path)
    if type(arc["kind"]) is not str or arc["kind"] not in _ARCS:
        fail("unknown_arc_semantics", path + ".kind")
    text(arc["port"], path + ".port")
    if type(arc["weight"]) is not int or arc["weight"] < 1:
        fail("invalid_arc_weight", path + ".weight")
    if arc["kind"] == "emit_occurrence":
        text(arc.get("outcome"), path + ".outcome")
    elif "outcome" in arc:
        fail("unexpected_arc_outcome", path + ".outcome")
    if arc["kind"] in ("observe_count", "observe_absence"):
        text(arc.get("scope"), path + ".scope")
    if arc["kind"] in ("reserve_quantity", "convert_reservation"):
        text(arc.get("unit"), path + ".unit")


def prepare_control_ir(author: ControlIR, registration):
    """Validate everything, then specialize supported author data only.

    Called by the public compiler and by wire verification using data inventory.
    No implementation is resolved or invoked here, including finite executors.
    """
    # Revalidate direct dataclass construction and untrusted wire material.
    value = ControlIR.from_dict(author.to_dict()).to_dict()
    module = value["module"]
    bindings = value["bindings"]
    components = {c["name"]: c for c in module["components"]}
    operations = {(c["name"], o["name"]): o for c in module["components"] for o in c.get("operations", [])}
    seen, capabilities, evaluations = set(), [], []

    def capability(path, construct, status, reason):
        capabilities.append(LoweringCapability(path, construct, status, reason))

    if value["backend"] == "execution/v1":
        capability("$.backend", "author_module", "UNSUPPORTED",
                   "execution/v1 is not the Module compiler backend and has no shared typed adapter")
    for index, atom in enumerate(value["atoms"]):
        path = f"$.atoms[{index}]"
        fields(atom, ("component", "operation", "config", "arcs", "routes"), ("guard",), path)
        identity = (text(atom["component"], path + ".component"), text(atom["operation"], path + ".operation"))
        if identity in seen or identity not in operations:
            fail("unknown_or_duplicate_atomic_operation", path)
        seen.add(identity)
        operation, component = operations[identity], components[identity[0]]
        declaration = registration.declaration("executor", operation["executor"])
        if "control_ir" not in declaration["contracts"]:
            fail("typed_atomic_contract_required", path, operation["executor"])
        contract = validate_atomic_contract(declaration["contracts"]["control_ir"], path + ".contract")
        ports = {port["name"]: port for port in component["ports"]}
        for side in ("input", "output"):
            actual = {name: ports[name]["schema"] for name in operation[side + "s"] if name in ports}
            if actual != contract[side + "_schemas"] or set(actual) != set(operation[side + "s"]):
                fail("atomic_port_schema_mismatch", path + "." + side + "s")
        declared_outcomes = {o["name"]: o["products"] for o in operation["outcomes"]}
        normalized = lambda outcomes: {tag: sorted(
            ({"port": p["port"], "minimum": p.get("minimum", 1), "maximum": p.get("maximum", 1)} for p in ps),
            key=lambda p: p["port"]) for tag, ps in outcomes.items()}
        if canonical_key(normalized(declared_outcomes)) != canonical_key(normalized(contract["outcomes"])):
            fail("atomic_outcome_union_mismatch", path + ".routes")
        _routes(atom["routes"], contract, path + ".routes")
        if contract["effect"] != "pure":
            capability(path, contract["effect"], "EXACT_ADAPTER_REQUIRED",
                       "only author-specialized pure atomic contracts have an exact adapter")
        if operation.get("tools") or any(o.get("effects") for o in operation["outcomes"]):
            fail("pure_atomic_effect_mismatch", path)
        if type(atom["config"]) is not dict or atom["config"].keys() != contract["config_type"]["fields"].keys():
            fail("config_fields_mismatch", path + ".config")
        # Requiring an empty placeholder prevents silently replacing old config.
        if operation["config"]:
            fail("typed_config_placeholder_required", path + ".config")
        config = {}
        for key, expression in atom["config"].items():
            target_type = contract["config_type"]["fields"][key]
            inferred = infer_expression(expression, bindings)
            if not same_type(inferred, target_type):
                fail("config_type_mismatch", path + ".config." + key)
            result = evaluate_expression(expression, bindings)
            if result.error:
                raise ControlIRError(result.error.code, path + ".config." + key + result.error.path[1:], result.error.detail)
            if result.read_set.heads:
                capability(path + ".config." + key, "mutable_read_set", "NEW_VERSION_REQUIRED",
                           "author specialization cannot freeze a runtime mutable head; no admission CAS adapter")
            config[key] = result.value
            evaluations.append({"path": path + ".config." + key, "type": result.type,
                                "value": result.value, "read_set": asdict(result.read_set)})
        operation["config"] = typed_value(config, contract["config_type"], path + ".config")
        if "guard" in atom:
            inferred = infer_expression(atom["guard"], bindings)
            if not same_type(inferred, BOOL):
                fail("guard_bool_required", path + ".guard")
            result = evaluate_expression(atom["guard"], bindings)
            if result.error:
                raise ControlIRError(result.error.code, path + ".guard" + result.error.path[1:], result.error.detail)
            if result.read_set.heads or result.value is not True:
                capability(path + ".guard", "runtime_guard", "EXACT_ADAPTER_REQUIRED",
                           "only an immutable author guard proven true can be discharged; false is not erased")
            evaluations.append({"path": path + ".guard", "type": result.type,
                                "value": result.value, "read_set": asdict(result.read_set)})
        if type(atom["arcs"]) is not list:
            fail("expected_sequence", path + ".arcs")
        consumed = False
        for i, arc in enumerate(atom["arcs"]):
            arc_path = f"{path}.arcs[{i}]"
            _arc(arc, arc_path)
            if arc["port"] not in ports:
                fail("unknown_arc_port", arc_path)
            kind = arc["kind"]
            if kind == "consume_occurrence": consumed = True
            if kind not in ("consume_occurrence", "emit_occurrence"):
                capability(arc_path, kind, "EXACT_ADAPTER_REQUIRED",
                           "occurrence consumption is distinct from observation, access and quantity rights")
            else:
                if "scope" in arc or "unit" in arc:
                    fail("unexpected_arc_dimension", arc_path)
                capability(arc_path, kind, "NATIVE", "verified against the actual lowered occurrence arc")
        if not consumed:
            fail("case_occurrence_required", path + ".arcs")
        capability(path, "finite_author_specialization", "NATIVE",
                   "typed immutable author values become actual executor config; no executor is called")
    if seen != operations.keys():
        fail("atomic_inventory_incomplete", "$.atoms")
    seen_calls = set()
    for index, call in enumerate(value["calls"]):
        path = f"$.calls[{index}]"
        normalized_call = validate_call_contract(call, path)
        if normalized_call["site"] in seen_calls:
            fail("duplicate_call_site", path)
        seen_calls.add(normalized_call["site"])
        capability(path, "typed_call", "NEW_VERSION_REQUIRED",
                   "the complete static contract is valid; attach/return/per-consumer wake are unavailable")
    for name in ("continuations", "closures"):
        for index, item in enumerate(value[name]):
            path = f"$.{name}[{index}]"
            fields(item, ("contract", "binding"), path=path)
            text(item["contract"], path + ".contract")
            text(item["binding"], path + ".binding")
            capability(path, name, "NEW_VERSION_REQUIRED",
                       "persistent continuation/closure contracts require their actual runtime producer and consumer")
    if any(item.status != "NATIVE" for item in capabilities):
        raise ControlIRCapabilityError(capabilities)
    proof = {"author": author.to_dict(), "evaluations": evaluations,
             "capabilities": [asdict(item) for item in capabilities]}
    module.setdefault("designer_constraints", {})[PROOF_KEY] = proof
    return ModuleDeclaration.from_dict(module), tuple(capabilities)


def verify_control_fragments(author: ControlIR, fragments, module):
    """Ensure trusted lowering really preserved every accepted author arc."""
    operations = {(c.name, o.name): o for c in module.components for o in c.operations}
    for component in module.components:
        fragment = fragments[component.name]
        if {o.name for o in fragment.operations} != {o.name for o in component.operations}:
            fail("undeclared_atomic_operation", "$.fragments." + component.name)
        if len(fragment.transitions) != len(component.operations):
            fail("undeclared_atomic_transition", "$.fragments." + component.name)
    for index, atom in enumerate(author.to_dict()["atoms"]):
        path = f"$.atoms[{index}]"
        fragment = fragments[atom["component"]]
        actual_operations = [o for o in fragment.operations if o.name == atom["operation"]]
        expected_operation = operations[(atom["component"], atom["operation"])]
        if len(actual_operations) != 1 or canonical_key(actual_operations[0].config) != canonical_key(expected_operation.config):
            fail("typed_config_lowering_mismatch", path + ".config")
        # The supported adapter has exactly one transition for this operation.
        transitions = [t for t in fragment.transitions if t.operation == atom["operation"]]
        if len(transitions) != 1 or transitions[0].count_guards or transitions[0].input_verdicts:
            fail("atomic_transition_adapter_mismatch", path)
        transition = transitions[0].name
        port_places = {p.name: p.place for p in fragment.ports}
        expected = []
        for arc in atom["arcs"]:
            if arc["port"] not in port_places:
                fail("missing_lowered_port", path)
            expected.append({"place": port_places[arc["port"]], "transition": transition,
                             "direction": "input" if arc["kind"] == "consume_occurrence" else "output",
                             "weight": arc["weight"],
                             "mode": "consume" if arc["kind"] == "consume_occurrence" else "produce",
                             "outcome": arc.get("outcome"), "emit": "produced", "forward_source": None,
                             "colour_expression": None, "output_predicates": [], "lease_claims": [],
                             "lease_claim_exclusions": [], "lease_claim_set": None, "effect_selector": None})
        actual = [asdict(arc) for arc in fragment.arcs if arc.transition == transition]
        canonical = lambda items: sorted(canonical_key(data(json.loads(json.dumps(item)))) for item in items)
        if canonical(expected) != canonical(actual):
            fail("atomic_arc_lowering_mismatch", path + ".arcs")
        if any(arc.transition == transition for arc in fragment.reset_arcs) or any(
                arc.transition == transition for arc in fragment.variable_resource_arcs):
            fail("undeclared_atomic_effect_arc", path)


def verify_control_proof(module, fragments, registration):
    constraints = module.designer_constraints or {}
    if control_proof_key(constraints) is None:
        return
    proof = constraints[PROOF_KEY]
    fields(proof, ("author", "evaluations", "capabilities"), path="$.control_proof")
    author = ControlIR.from_dict(proof["author"])
    rebuilt, _ = prepare_control_ir(author, registration)
    if canonical_key(rebuilt.to_dict()) != canonical_key(module.to_dict()):
        fail("control_proof_mismatch", "$.control_proof")
    verify_control_fragments(author, fragments, module)


__all__ = ("ControlIR", "ControlIRError", "ControlIRCapabilityError", "LoweringCapability")
