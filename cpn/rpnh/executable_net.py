"""Typed, neutral compiled inventory. No execution or Registry authority.

The sole wire format is compiler output for rehydration, not a second LLM input.
Persisted HOST declarations describe contracts; loading grants no callable trust.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from typing import Any, Mapping

from jsonschema import Draft7Validator, ValidationError
from cpn.rpnh._schema_validation import check_draft7_schema
from referencing import Registry, Resource

from .module import ModuleDeclaration, SymbolicNet, _operation, SCHEMA_PATH, MechanicalValidator
from .composition import compose_fragments
from .petri_contracts import (
    ArcDeclaration, ResetArcDeclaration, BindingContext, DeclarationError, OperationDeclaration,
    PNFragment, PlaceDeclaration, PortBinding, PortDeclaration,
    TransitionDeclaration, same_operation_contract, validate_registered_config,
    _same_json_value,
    InitialTokenDeclaration, CountGuard, InputVerdictGuard, InputColourPredicate,
    ColourExpression, LeaseClaimExpression, LeaseClaimSetExpression,
    LeaseIdentityDeclaration,
    ResourceLeasePoolBinding,
    VariableResourceArc, LeaseClaimTemplate, LogicalSlotBinding,
)


WIRE_SCHEMA_PATH = SCHEMA_PATH.parent / "executable_net.v1.schema.json"


def _data(value):
    """Reject coercible non-JSON primitives, including integer-valued floats."""
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise DeclarationError("Inventory object keys must be exact strings")
        return {k: _data(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_data(v) for v in value]
    if value is not None and type(value) not in (str, int, float, bool):
        raise DeclarationError("Inventory must contain only JSON primitives")
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DeclarationError("Inventory must contain finite JSON data") from exc


def _canonical(value):
    """Inventory arrays are sets/multisets, never execution sequence policy.

    Arbitrary config, schema, identity and constraints arrays remain opaque data.
    """
    value = _data(value)
    opaque = {"config", "schema", "identity", "contracts", "designer_constraints", "inputs", "outputs",
              "value", "colour", "expected", "present", "absent"}

    def walk(item):
        if isinstance(item, dict):
            return {k: v if k in opaque else walk(v) for k, v in sorted(item.items())}
        if isinstance(item, list):
            return sorted((walk(v) for v in item), key=lambda v: json.dumps(v, sort_keys=True))
        return item

    return walk(value)


def _handles(names, prefix):
    return {name: f"{prefix}_{i}" for i, name in enumerate(sorted(names))}


def _place(value):
    return PlaceDeclaration(**dict(value,
        schema_variants=tuple(value.get("schema_variants", [])),
        colours=tuple(value.get("colours", [])),
        initial_tokens=tuple(InitialTokenDeclaration(**v) for v in value.get("initial_tokens", []))))


def _transition(value):
    return TransitionDeclaration(**dict(value,
        count_guards=tuple(CountGuard(**v) for v in value.get("count_guards", [])),
        input_verdicts=tuple(InputVerdictGuard(**v) for v in value.get("input_verdicts", []))))


def _arc(value):
    expression = value.get("colour_expression")
    if expression is not None:
        expression = ColourExpression(**dict(expression,
            candidates=tuple(expression.get("candidates", [])),
            selected=tuple(expression.get("selected", []))))
    claim_set = value.get("lease_claim_set")
    if claim_set is not None:
        claim_set = LeaseClaimSetExpression(**claim_set)
    return ArcDeclaration(**dict(
        value,
        colour_expression=expression,
        output_predicates=tuple(
            InputColourPredicate(**v)
            for v in value.get("output_predicates", [])),
        lease_claims=tuple(
            LeaseClaimExpression(**v)
            for v in value.get("lease_claims", [])),
        lease_claim_exclusions=tuple(
            value.get("lease_claim_exclusions", [])),
        lease_claim_set=claim_set,
    ))


def _pool(value):
    return ResourceLeasePoolBinding(**dict(value,
        initial_resources=tuple(value.get("initial_resources", [])),
        initial_slots=tuple(value.get("initial_slots", []))))


def _variable_arc(value):
    return VariableResourceArc(**dict(value,
        initial_claims=tuple(LeaseClaimTemplate(**v) for v in value.get("initial_claims", []))))


def _slot(value):
    return LogicalSlotBinding(**dict(value,
        route_transitions=tuple(value.get("route_transitions", [])),
        consumer_transitions=tuple(value.get("consumer_transitions", []))))


def _fragment(value):
    return PNFragment(
        places=tuple(_place(p) for p in value["places"]),
        transitions=tuple(_transition(t) for t in value["transitions"]),
        arcs=tuple(_arc(a) for a in value["arcs"]),
        ports=tuple(PortBinding(**p) for p in value["ports"]),
        operations=tuple(_operation(o) for o in value["operations"]),
        internal_ports=tuple(PortDeclaration(**p) for p in value["internal_ports"]),
        internal_bindings=tuple(PortBinding(**p) for p in value["internal_bindings"]),
        lease_identities=tuple(LeaseIdentityDeclaration(**v) for v in value.get("lease_identities", [])),
        lease_pools=tuple(_pool(v) for v in value.get("lease_pools", [])),
        variable_resource_arcs=tuple(_variable_arc(v) for v in value.get("variable_resource_arcs", [])),
        logical_slots=tuple(_slot(v) for v in value.get("logical_slots", [])),
        reset_arcs=tuple(
            ResetArcDeclaration(**v) for v in value.get("reset_arcs", [])),
    )


def _ports(source, fragments, symbolic, handles):
    result = []
    for component in source.components:
        fragment = fragments[component.name]
        for public, ports in ((True, component.ports), (False, fragment.internal_ports)):
            for port in ports:
                name = f"{component.name}.{port.name}"
                result.append(dict(asdict(port), name=name,
                                   place=symbolic["port_places"][name],
                                   port_id=handles[name], public=public))
    return result


def _make_document(source, fragments, symbolic, registrations):
    operations = _handles((o["name"] for o in symbolic["operations"]), "op")
    ports = _handles(symbolic["port_places"], "port")
    aliases = {}
    for name, fragment in fragments.items():
        aliases.update({f"{name}.{p.name}": f"{name}.{p.name}" for p in fragment.places})
        for binding in fragment.ports + fragment.internal_bindings:
            aliases[f"{name}.{binding.place}"] = symbolic["port_places"][f"{name}.{binding.name}"]
    return {
        "schema_version": "rpnh/executable_net/v1", "source": source,
        "fragments": {k: asdict(v) for k, v in fragments.items()},
        "symbolic": symbolic, "registrations": registrations,
        "operation_handles": operations, "port_handles": ports,
        "place_aliases": aliases,
        "ports": _ports(ModuleDeclaration.from_dict(source), fragments, symbolic, ports),
    }


class _ContractInventory:
    """Pure-data membership for mechanical validation, NOT HOST registration."""

    def __init__(self, declarations):
        self.declarations = declarations
        self.used = {}

    def declaration(self, category, key):
        try:
            declaration = self.declarations[category][key]
        except KeyError as exc:
            raise DeclarationError(f"Missing exact {category} inventory: {key}") from exc
        if declaration.get("kind") != category or declaration.get("key") != key:
            raise DeclarationError("Registration inventory identity mismatch")
        self.used.setdefault(category, set()).add(key)
        return declaration

    def resolve(self, category, key):
        # PNFragment validation only checks membership; no code is reconstructed.
        return self.declaration(category, key)


def _verify(document, source, fragments, *, offline_schema_validation=False):
    """Check wire against the recorded exact lowering inventory without lowering."""
    inventory = _ContractInventory(document["registrations"])
    for category, declarations in document["registrations"].items():
        if category not in {"schema", "component", "executor", "tool", "analyzer"}:
            raise DeclarationError("Unknown registration category")
        for key, declaration in declarations.items():
            fields = {"kind", "key", "schema"} if category == "schema" else {"kind", "key", "identity", "contracts"}
            if set(declaration) != fields or type(key) is not str or not key:
                raise DeclarationError("Exact registered declaration shape required")
            if category != "schema" and (not isinstance(declaration["identity"], dict)
                                         or not declaration["identity"]
                                         or not isinstance(declaration["contracts"], dict)):
                raise DeclarationError("Registered identity/contracts must remain pure data")
    components = {c.name: c for c in source.components}
    if len(components) != len(source.components) or components.keys() != fragments.keys():
        raise DeclarationError("Exact unique component/fragment inventory required")
    for key in source.required_schemas:
        declaration = inventory.declaration("schema", key)
        if declaration["schema"].get("$id") != key:
            raise DeclarationError("Exact schema identity required")
        check_draft7_schema(declaration["schema"])
    for key in source.analyzers:
        inventory.resolve("analyzer", key)
    for terminal in (source.terminal, *source.terminal_alternatives):
        inventory.resolve("tool", terminal.key)
        validate_registered_config(inventory, "tool", terminal.key,
                                   terminal.config, source.required_schemas,
                                   _offline_schema_validation=offline_schema_validation)
    places, port_places, external, operations, transitions, arcs = {}, {}, {}, [], [], []
    for name, component in components.items():
        fragment = fragments[name]
        contracts = inventory.declaration("component", component.key)["contracts"]
        if component.config_schema not in source.required_schemas:
            raise DeclarationError("Component config schema must be required")
        if contracts.get("config_schema", component.config_schema) != component.config_schema:
            raise DeclarationError("Component config schema differs from HOST contract")
        from .petri_contracts import _validate_schema_instance
        _validate_schema_instance(inventory.declaration("schema", component.config_schema)["schema"], component.config,
            offline_schema_validation=offline_schema_validation)
        context = BindingContext(name, component.ports, source.required_schemas, source.budgets, component.operations)
        if offline_schema_validation:
            fragment.validate(context, inventory, _offline_schema_validation=True)
        else:
            fragment.validate(context, inventory)
        actual = {o.name: o for o in fragment.operations}
        if any(o.name not in actual for o in component.operations):
            raise DeclarationError("Missing declared operation")
        if any(not same_operation_contract(actual[o.name], o) for o in component.operations):
            raise DeclarationError("Explicit operation protocol differs from lowered inventory")
    from .control_ir import verify_control_proof
    verify_control_proof(source, fragments, inventory)
    composed, aliases = compose_fragments(source, fragments)
    expected = asdict(composed)
    operations = composed.operations
    port_places = composed.port_places
    if not _same_json_value(_canonical(expected), _canonical(document["symbolic"])):
        raise DeclarationError("Symbolic net differs from exact declaration/fragment composition")
    if "rpnh_control_ir_v1" in (source.designer_constraints or {}):
        # The explicit typed domain distinguishes Int from Rational. This must
        # not tighten the legacy v1 numeric comparison outside that opt-in.
        if json.dumps(_canonical(expected), sort_keys=True) != json.dumps(_canonical(document["symbolic"]), sort_keys=True):
            raise DeclarationError("Typed symbolic net differs from exact lowering")
    if document["place_aliases"] != aliases:
        raise DeclarationError("Exact original logical place aliases required")
    expected_ops = _handles((op.name for op in operations), "op")
    expected_ports = _handles(port_places, "port")
    if document["operation_handles"] != expected_ops or document["port_handles"] != expected_ports:
        raise DeclarationError("Lexical handles must match sorted qualified symbolic inventory")
    if not _same_json_value(_canonical(document["ports"]),
                            _canonical(_ports(source, fragments, expected, expected_ports))):
        raise DeclarationError("Typed public/internal port inventory mismatch")
    if {k: set(v) for k, v in document["registrations"].items()} != inventory.used:
        raise DeclarationError("Registration inventory must contain exactly referenced declarations")
    return composed


@dataclass(frozen=True)
class CompiledPort:
    name: str
    direction: str
    schema: str
    channel: str
    cardinality: int
    place: str
    port_id: str
    public: bool
    cardinality_minimum: int | None = None
    cardinality_maximum: int | None = None

    @property
    def minimum(self):
        return self.cardinality if self.cardinality_minimum is None else self.cardinality_minimum

    @property
    def maximum(self):
        return self.cardinality if self.cardinality_maximum is None else self.cardinality_maximum

    @property
    def signature(self):
        return self.schema, self.channel, self.minimum, self.maximum


@dataclass(frozen=True)
class CompiledOperation:
    operation_id: str
    executor_key: str
    declaration: OperationDeclaration
    executor_declaration: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledPetriNet:
    source: ModuleDeclaration
    fragments: Mapping[str, PNFragment]
    symbolic: SymbolicNet
    ports: tuple[CompiledPort, ...]
    operations: tuple[CompiledOperation, ...]
    registrations: Mapping[str, Mapping[str, Mapping[str, Any]]]
    operation_handles: Mapping[str, str]
    port_handles: Mapping[str, str]
    place_aliases: Mapping[str, str]
    schema_version: str = "rpnh/executable_net/v1"

    def to_dict(self):
        return _data(_make_document(self.source.to_dict(), self.fragments,
                                    asdict(self.symbolic), self.registrations))

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False)

    def validate_products(self, operation: str, outcome: str, products: Mapping[str, list[Any]]):
        """Exact selected product quantities/types only, no semantic interpreter."""
        selected = next((o.declaration for o in self.operations if o.declaration.name == operation), None)
        if selected is None:
            raise DeclarationError("Unknown exact logical operation")
        selected_outcome = next((o for o in selected.outcomes if o.name == outcome), None)
        if selected_outcome is None:
            raise DeclarationError("Unknown exact outcome")
        contracts = {p.port: p for p in selected_outcome.products}
        if set(products) - contracts.keys():
            raise DeclarationError("Undeclared selected outcome product")
        schemas = self.registrations["schema"]
        registry = Registry().with_resources((key, Resource.from_contents(v["schema"])) for key, v in schemas.items())
        ports = {p.name: p for p in self.ports}
        for name, contract in contracts.items():
            values = products.get(name, [])
            if not isinstance(values, (list, tuple)) or not contract.minimum <= len(values) <= contract.maximum:
                raise DeclarationError("Selected product quantity mismatch")
            validator = Draft7Validator(schemas[ports[name].schema]["schema"], registry=registry)
            try:
                for value in values:
                    validator.validate(_data(value))
            except ValidationError as exc:
                raise DeclarationError(f"Selected product type mismatch: {name}") from exc


def load_compiled_net(document: Mapping[str, Any] | str) -> CompiledPetriNet:
    """Rehydrate only the sole compiler wire format; never resolve HOST code."""
    return _load_compiled_net(document, offline_schema_validation=False)


def load_compiled_control_net(document: dict | str) -> CompiledPetriNet:
    """Require an exact typed author proof; no coercible Python containers.

    The ordinary loader intentionally accepts legacy v1 with no typed claim.
    Use this entry when a consumer requires the ControlIR proof to be present.
    """
    from .control_types import data as control_data
    from .control_ir import control_proof_key
    try:
        incoming = json.loads(document) if type(document) is str else document
    except json.JSONDecodeError as exc:
        from .control_types import ControlIRError
        raise ControlIRError("invalid_json", "$") from exc
    incoming = control_data(incoming)
    if type(incoming) is not dict or type(incoming.get("source")) is not dict:
        from .control_types import fail
        fail("control_proof_required", "$.source")
    constraints = incoming["source"].get("designer_constraints", {})
    if type(constraints) is not dict:
        from .control_types import fail
        fail("expected_record", "$.source.designer_constraints")
    control_proof_key(constraints, required=True)
    return _load_compiled_net(incoming, offline_schema_validation=True)


def _load_compiled_net_offline(document: Mapping[str, Any] | str) -> CompiledPetriNet:
    """Fixed opt-in reader: no implicit remote schema retrieval or HOST lower."""
    return _load_compiled_net(document, offline_schema_validation=True)


def _load_compiled_net(document, *, offline_schema_validation):
    try:
        incoming = json.loads(document) if isinstance(document, str) else document
        # Inspect builtin dict storage without invoking Mapping/subclass hooks.
        # The legacy Mapping API remains available for untyped v1 inventory.
        def plain_dict_slot(value, key):
            if not isinstance(value, dict):
                return None
            # Check before a lookup: a hostile non-string key may collide with
            # the requested string's hash and run __eq__ inside dict.get.
            if any(type(name) is not str for name in dict.keys(value)):
                raise DeclarationError("Inventory object keys must be exact strings")
            return dict.get(value, key)
        raw_source = plain_dict_slot(incoming, "source")
        raw_constraints = plain_dict_slot(raw_source, "designer_constraints")
        plain_dict_slot(raw_constraints, "rpnh_control_ir_v1")
        typed_proof = isinstance(raw_constraints, dict) and any(
            type(key) is str and key.startswith("rpnh_control_ir_") for key in dict.keys(raw_constraints))
        if typed_proof:
            from .control_types import data as control_data
            incoming = control_data(incoming)
        value = _data(incoming)
        wire_schema = json.loads(WIRE_SCHEMA_PATH.read_text(encoding="utf-8"))
        source_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in (wire_schema, source_schema))
        MechanicalValidator(wire_schema, registry=registry).validate(value)
        source = ModuleDeclaration.from_dict(value["source"])
        # A typed author proof opts into offline validation. Legacy v1 sources
        # without this reserved proof keep their existing validator behavior.
        from .control_ir import control_proof_key
        if control_proof_key(source.designer_constraints or {}) is not None:
            offline_schema_validation = True
        fragments = {k: _fragment(v) for k, v in value["fragments"].items()}
        symbolic = _verify(value, source, fragments, offline_schema_validation=offline_schema_validation)
        operations = tuple(CompiledOperation(value["operation_handles"][op.name],
                                            op.executor, op, value["registrations"]["executor"][op.executor])
                           for op in symbolic.operations)
        return CompiledPetriNet(source, fragments, symbolic,
                               tuple(CompiledPort(**p) for p in value["ports"]), operations,
                               value["registrations"], value["operation_handles"], value["port_handles"],
                               value["place_aliases"])
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, DeclarationError):
            raise
        raise DeclarationError(f"Invalid compiled inventory: {exc}") from exc


__all__ = ("CompiledPetriNet", "CompiledOperation", "CompiledPort", "load_compiled_net", "load_compiled_control_net")
