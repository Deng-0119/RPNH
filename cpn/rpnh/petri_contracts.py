"""Symbolic typed PN contracts. No execution, Registry or component imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import re
from typing import Any, Mapping, Protocol


class DeclarationError(ValueError):
    pass


def symbol(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
        raise DeclarationError(f"Not a symbolic identity: {value!r}")
    return value


def unique(items, label):
    result = {}
    for item in items:
        symbol(item.name)
        if item.name in result:
            raise DeclarationError(f"Duplicate {label}: {item.name}")
        result[item.name] = item
    return result


class RegistrationView(Protocol):
    """Trusted host lookup; unknown keys raise; declarations are pure data."""
    def resolve(self, category: str, key: str) -> Any: ...
    def declaration(self, category: str, key: str) -> Mapping[str, Any]: ...


def registered(registration: RegistrationView, category: str, key: str):
    if not isinstance(key, str) or not key:
        raise DeclarationError("Registered identity must be a nonempty exact key")
    declaration = registration.declaration(category, key)
    if declaration.get("key") != key or declaration.get("kind") != category:
        raise DeclarationError("Registered identity mismatch")
    return declaration


def colour_key(value):
    # bool and str are disjoint colours (and bool must not admit integer 0/1).
    if type(value) not in (bool, str) or (isinstance(value, str) and not value):
        raise DeclarationError("Colour must be a boolean or nonempty string")
    return type(value), value


def validate_registered_config(registration: RegistrationView, category: str, key: str,
                               config: Mapping[str, Any], required_schemas: tuple[str, ...]):
    """Apply a HOST registered config contract, when that handler declares one."""
    from jsonschema import Draft7Validator, ValidationError

    contracts = registered(registration, category, key)["contracts"]
    config_schema = contracts.get("config_schema")
    if config_schema is not None:
        if config_schema not in required_schemas:
            raise DeclarationError(f"Handler config schema must be required: {config_schema}")
        schema = registered(registration, "schema", config_schema)["schema"]
        try:
            Draft7Validator(schema).validate(config)
        except ValidationError as exc:
            raise DeclarationError(f"Invalid registered {category} config: {exc.message}") from exc


@dataclass(frozen=True)
class PortDeclaration:
    """Typed interface quantity; omitted range endpoints equal cardinality.

    cardinality remains the positive fixed-quantity default. Explicit endpoints
    describe an inclusive envelope, including Native optional inputs (0..1).
    Output outcomes may narrow that envelope; their products remain authority.
    """
    name: str
    direction: str
    schema: str
    channel: str = "data"
    cardinality: int = 1
    cardinality_minimum: int | None = None
    cardinality_maximum: int | None = None

    @property
    def minimum(self):
        return self.cardinality if self.cardinality_minimum is None else self.cardinality_minimum

    @property
    def maximum(self):
        return self.cardinality if self.cardinality_maximum is None else self.cardinality_maximum

    def validate(self):
        symbol(self.name)
        if self.direction not in ("input", "output") or self.channel not in ("data", "control"):
            raise DeclarationError(f"Invalid port: {self.name}")
        if type(self.cardinality) is not int or self.cardinality < 1:
            raise DeclarationError("Port cardinality must be positive")
        if (type(self.minimum) is not int or type(self.maximum) is not int
                or not 0 <= self.minimum <= self.maximum):
            raise DeclarationError("Port cardinality range must be nonnegative and ordered")

    @property
    def signature(self):
        return self.schema, self.channel, self.minimum, self.maximum


@dataclass(frozen=True)
class BindingContext:
    component: str
    ports: tuple[PortDeclaration, ...]
    required_schemas: tuple[str, ...]
    budgets: Mapping[str, int | float]
    operations: tuple[OperationDeclaration, ...] = ()


@dataclass(frozen=True)
class InitialTokenDeclaration:
    count: int = 1
    schema: str | None = None
    colour: bool | str | None = None
    value: Any = None


@dataclass(frozen=True)
class PlaceDeclaration:
    name: str
    schema: str
    channel: str = "data"
    capacity: int | None = None
    token_kind: str = "data"
    schema_variants: tuple[str, ...] = ()
    colours: tuple[bool | str, ...] = ()
    initial_tokens: tuple[InitialTokenDeclaration, ...] = ()
    reusable: bool = False

    @property
    def admitted_schemas(self) -> tuple[str, ...]:
        return (self.schema, *self.schema_variants)


@dataclass(frozen=True)
class PortBinding:
    name: str
    place: str


@dataclass(frozen=True)
class ProductDeclaration:
    port: str
    minimum: int = 1
    maximum: int = 1


@dataclass(frozen=True)
class SymbolReference:
    """Mechanical references are not operation-port or Registry bindings."""
    kind: str
    name: str


@dataclass(frozen=True)
class EffectDeclaration:
    """Registered host effect; port bindings never carry Registry identities.

    Port bindings and mechanical references have distinct namespaces. Registry
    transactional checks still own claim transfer, reset, route and adoption.
    """
    key: str
    bindings: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)
    references: Mapping[str, SymbolReference] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeDeclaration:
    name: str
    products: tuple[ProductDeclaration, ...]
    effects: tuple[EffectDeclaration, ...] = ()


@dataclass(frozen=True)
class BudgetBindingDeclaration:
    bucket_id: str
    budget_scope: str
    finalization_scope: str | None = None

    def validate(self):
        if any(not isinstance(v, str) or not v for v in (self.bucket_id, self.budget_scope)):
            raise DeclarationError("Budget bucket and scope must be explicit nonempty identities")
        if self.finalization_scope is not None and (not isinstance(self.finalization_scope, str) or not self.finalization_scope):
            raise DeclarationError("Budget finalization scope must be null or nonempty opaque tag")


@dataclass(frozen=True)
class OperationDeclaration:
    name: str
    executor: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    outcomes: tuple[OutcomeDeclaration, ...]
    tools: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    request_port: str | None = None
    budget_binding: BudgetBindingDeclaration | None = None

    def validate_products(self, outcome: str, products: Mapping[str, list[Any]],
                          ports: Mapping[str, PortDeclaration], registration: RegistrationView):
        """Local output data/cardinality check, not Registry publication.

        Exact runtime result/target bindings and claim/effect execution remain
        Registry responsibilities. Only the declared outcome selects a bundle.
        """
        from jsonschema import Draft7Validator, ValidationError

        selected = next((o for o in self.outcomes if o.name == outcome), None)
        if selected is None:
            raise DeclarationError("Output outcome is not declared")
        declared = {p.port: p for p in selected.products}
        if set(products) - declared.keys():
            raise DeclarationError("Output contains undeclared product ports")
        for name, product in declared.items():
            values = products.get(name, [])
            if not isinstance(values, (tuple, list)) or not product.minimum <= len(values) <= product.maximum:
                raise DeclarationError(f"Output product cardinality mismatch: {name}")
            schema = registered(registration, "schema", ports[name].schema)["schema"]
            try:
                for value in values:
                    Draft7Validator(schema).validate(value)
            except ValidationError as exc:
                raise DeclarationError(f"Invalid output type for {name}: {exc.message}") from exc


def same_operation_contract(left: OperationDeclaration, right: OperationDeclaration) -> bool:
    """Compare declared bundles, not incidental outcome/product array order.

    Input/output tuples are the ordered executor ABI and remain exact. Config
    arrays remain application data. Effects are a simultaneous declared bundle;
    their identity/config/bindings and multiplicity are preserved.
    """
    def normalized(operation):
        outcomes = tuple(replace(outcome,
            products=tuple(sorted(outcome.products, key=lambda product: product.port)),
            effects=tuple(sorted(outcome.effects,
                key=lambda effect: json.dumps(asdict(effect), sort_keys=True, allow_nan=False))),
        ) for outcome in sorted(operation.outcomes, key=lambda outcome: outcome.name))
        return replace(operation, outcomes=outcomes, tools=tuple(sorted(operation.tools)))

    return normalized(left) == normalized(right)


@dataclass(frozen=True)
class CountGuard:
    place: str
    comparison: str
    threshold: int
    scope: str = "reader"


@dataclass(frozen=True)
class InputVerdictGuard:
    place: str
    expected: bool | str


@dataclass(frozen=True)
class InputColourPredicate:
    """Deposit predicate over an exact input, not a colour stamp."""
    place: str
    expected: bool | str


@dataclass(frozen=True)
class ColourExpression:
    """Closed mechanical projection; membership carries its declared catalog.

    ``selected`` is the lowered structural selection witness, not provider text.
    Names in membership catalogs are symbolic edge/action keys, not runtime refs.
    """
    kind: str = "literal"
    value: bool | str | None = None
    source_place: str | None = None
    member: str | None = None
    candidates: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()
    present: bool | str | None = None
    absent: bool | str | None = None


@dataclass(frozen=True)
class TransitionDeclaration:
    name: str
    operation: str
    count_guards: tuple[CountGuard, ...] = ()
    input_verdicts: tuple[InputVerdictGuard, ...] = ()


@dataclass(frozen=True)
class LeaseClaimExpression:
    """One output-arc projection of an exact lease colour from an input token."""

    lease_identity: str
    access_mode: str = "read"
    expected_resource_source: str | None = None


@dataclass(frozen=True)
class LeaseClaimSetExpression:
    """Formal sources from which one output token's lease colours are built.

    Components may select a subset only from ``registered_pool_subset``.  All
    other enabled sources are deterministic projections of the firing closure.
    """

    inherit_input_claims: bool = False
    operation_resource_updates: bool = False
    claim_output_products: bool = False
    registered_pool_subset: str | None = None
    access_mode: str = "read"


@dataclass(frozen=True)
class ArcDeclaration:
    place: str
    transition: str
    direction: str
    weight: int = 1
    mode: str = "consume"
    outcome: str | None = None
    emit: str = "produced"
    forward_source: str | None = None
    colour_expression: ColourExpression | None = None
    output_predicates: tuple[InputColourPredicate, ...] = ()
    lease_claims: tuple[LeaseClaimExpression, ...] = ()
    lease_claim_exclusions: tuple[str, ...] = ()
    lease_claim_set: LeaseClaimSetExpression | None = None
    effect_selector: str | None = None


@dataclass(frozen=True)
class ResetArcDeclaration:
    """One selectable reset arc owned by the formal Petri structure.

    ``selector`` is a closed symbolic handle returned by an external effect
    interpreter. Selecting it retires predecessor occurrences at ``place``;
    the interpreter cannot introduce a place absent from these arcs.
    """

    place: str
    transition: str
    selector: str
    outcome: str | None = None


@dataclass(frozen=True)
class LeaseIdentityDeclaration:
    name: str
    kind: str = "resource"
    slot: str | None = None


@dataclass(frozen=True)
class LeaseClaimTemplate:
    lease_identity: str
    expected_resource: str | None = None
    access_mode: str = "read"


@dataclass(frozen=True)
class ResourceLeasePoolBinding:
    name: str
    place: str
    initial_resources: tuple[str, ...] = ()
    initial_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class VariableResourceArc:
    transition: str
    claim_token_place: str
    lease_pool: str
    initial_claims: tuple[LeaseClaimTemplate, ...] = ()
    input_inscription: str = "consume_exact_claim_multiset"
    output_inscription: str = "return_exact_claim_multiset"


@dataclass(frozen=True)
class LogicalSlotBinding:
    name: str
    producer_transition: str
    output_port: str
    candidate_place: str
    published_place: str
    schema: str
    reviewer_transition: str | None = None
    route_transitions: tuple[str, ...] = ()
    consumer_transitions: tuple[str, ...] = ()
    artifact_id: str = ""
    artifact_synopsis: str = ""
    content_kind: str = "document"


@dataclass(frozen=True)
class PNFragment:
    places: tuple[PlaceDeclaration, ...]
    transitions: tuple[TransitionDeclaration, ...]
    arcs: tuple[ArcDeclaration, ...]
    ports: tuple[PortBinding, ...]
    operations: tuple[OperationDeclaration, ...]
    internal_ports: tuple[PortDeclaration, ...] = ()
    internal_bindings: tuple[PortBinding, ...] = ()
    lease_identities: tuple[LeaseIdentityDeclaration, ...] = ()
    lease_pools: tuple[ResourceLeasePoolBinding, ...] = ()
    variable_resource_arcs: tuple[VariableResourceArc, ...] = ()
    logical_slots: tuple[LogicalSlotBinding, ...] = ()
    reset_arcs: tuple[ResetArcDeclaration, ...] = ()

    def validate(self, context: BindingContext, registration: RegistrationView):
        for items, kind in ((self.places, PlaceDeclaration), (self.transitions, TransitionDeclaration),
                (self.arcs, ArcDeclaration), (self.ports, PortBinding), (self.operations, OperationDeclaration),
                (self.internal_ports, PortDeclaration), (self.internal_bindings, PortBinding),
                (self.lease_identities, LeaseIdentityDeclaration), (self.lease_pools, ResourceLeasePoolBinding),
                (self.variable_resource_arcs, VariableResourceArc), (self.logical_slots, LogicalSlotBinding),
                (self.reset_arcs, ResetArcDeclaration)):
            if any(not isinstance(item, kind) for item in items):
                raise DeclarationError(f"Fragment requires typed {kind.__name__} entries")
        places = unique(self.places, "place")
        transitions = unique(self.transitions, "transition")
        operations = unique(self.operations, "operation")
        public_bindings = unique(self.ports, "public port binding")
        public = unique(context.ports, "declared public port")
        unique(context.operations, "declared operation")
        if public_bindings.keys() != public.keys():
            raise DeclarationError("Fragment ports must exactly match declared ports")
        internal = unique(self.internal_ports, "internal port")
        internal_bindings = unique(self.internal_bindings, "internal port binding")
        if internal.keys() != internal_bindings.keys() or public.keys() & internal.keys():
            raise DeclarationError("Internal ports need exact, disjoint typed bindings")
        expected = {**public, **internal}
        ports = {**public_bindings, **internal_bindings}
        slots = unique(self.logical_slots, "logical slot")
        leases = unique(self.lease_identities, "lease identity")
        pools = unique(self.lease_pools, "lease pool")
        for schema in context.required_schemas:
            registered(registration, "schema", schema)
        for place in places.values():
            if len(set(place.admitted_schemas)) != len(place.admitted_schemas):
                raise DeclarationError("Duplicate place schema variant")
            for schema in place.admitted_schemas:
                if schema not in context.required_schemas:
                    raise DeclarationError(f"Undeclared application schema: {schema}")
                registered(registration, "schema", schema)
            if place.channel not in ("data", "control") or (
                place.capacity is not None and (type(place.capacity) is not int or place.capacity < 1)
            ) or place.token_kind not in ("data", "agent_resource", "counter", "decision", "resource_lease"):
                raise DeclarationError(f"Invalid place: {place.name}")
            domain = tuple(colour_key(c) for c in place.colours)
            if len(set(domain)) != len(domain) or type(place.reusable) is not bool:
                raise DeclarationError("Invalid colour domain/reusable declaration")
            total = 0
            for token in place.initial_tokens:
                if not isinstance(token, InitialTokenDeclaration) or type(token.count) is not int or token.count < 1:
                    raise DeclarationError("Invalid initial token count")
                total += token.count
                if token.colour is not None and colour_key(token.colour) not in domain:
                    raise DeclarationError("Initial token colour outside place domain")
                if token.schema is not None:
                    if token.schema not in place.admitted_schemas:
                        raise DeclarationError("Initial token schema outside place variants")
                    from jsonschema import Draft7Validator, ValidationError
                    try:
                        Draft7Validator(registered(registration, "schema", token.schema)["schema"]).validate(token.value)
                    except ValidationError as exc:
                        raise DeclarationError(f"Invalid initial token data: {exc.message}") from exc
                elif token.value is not None:
                    raise DeclarationError("Initial payload requires its exact schema")
            if place.capacity is not None and total > place.capacity:
                raise DeclarationError("Initial marking exceeds place capacity")
            if place.reusable and (
                    place.token_kind == "counter"
                    or (place.capacity is None
                        and place.token_kind != "resource_lease")):
                raise DeclarationError(
                    "Reusable place requires declared capacity unless it is "
                    "an identity-bounded dynamic resource lease pool")
            if place.token_kind == "agent_resource" and (not place.reusable or total != place.capacity):
                raise DeclarationError("Idle execution slots must initially equal reusable capacity")
        for name, binding in ports.items():
            if binding.place not in places:
                raise DeclarationError(f"Unknown port place: {binding.place}")
            port, place = expected[name], places[binding.place]
            port.validate()
            if port.schema not in context.required_schemas or port.schema not in place.admitted_schemas or port.channel != place.channel:
                raise DeclarationError(f"Port type mismatch: {name}")
        if {name for op in self.operations for name in op.inputs + op.outputs} != expected.keys():
            raise DeclarationError("Every declared port must be used by an operation")
        for operation in operations.values():
            if operation.budget_binding is not None:
                if not isinstance(operation.budget_binding, BudgetBindingDeclaration):
                    raise DeclarationError("Operation budget binding must be typed")
                operation.budget_binding.validate()
            registration.resolve("executor", operation.executor)
            if operation.request_port is not None and operation.request_port not in operation.inputs:
                raise DeclarationError("Request binding must name an exact operation input")
            validate_registered_config(registration, "executor", operation.executor,
                                       operation.config, context.required_schemas)
            for key in operation.tools:
                registration.resolve("tool", key)
                registered(registration, "tool", key)
            for direction, names in (("input", operation.inputs), ("output", operation.outputs)):
                if len(set(names)) != len(names):
                    raise DeclarationError("Duplicate operation port")
                for name in names:
                    if name not in expected or expected[name].direction != direction:
                        raise DeclarationError(f"Invalid operation {direction}: {name}")
            outcomes = unique(operation.outcomes, "outcome")
            if not outcomes:
                raise DeclarationError("Operation requires an output protocol")
            for outcome in outcomes.values():
                products = {}
                for product in outcome.products:
                    if product.port in products or product.port not in operation.outputs:
                        raise DeclarationError("Invalid or duplicate product port")
                    if (type(product.minimum) is not int or type(product.maximum) is not int
                            or not 0 <= product.minimum <= product.maximum
                            or product.maximum > expected[product.port].maximum):
                        raise DeclarationError("Invalid product cardinality")
                    products[product.port] = product
                for name in operation.outputs:
                    port = expected[name]
                    # Existing quantity-only outputs retain conditional bundles.
                    # An explicit lower bound also constrains absent products.
                    if port.cardinality_minimum is not None and (
                            products[name].minimum if name in products else 0) < port.minimum:
                        raise DeclarationError("Outcome falls below declared output cardinality minimum")
                for effect in outcome.effects:
                    registration.resolve("tool", effect.key)
                    validate_registered_config(registration, "tool", effect.key,
                                               effect.config, context.required_schemas)
                    if any(port not in operation.inputs + operation.outputs for port in effect.bindings.values()):
                        raise DeclarationError("Effect binding outside operation ports")
                    if any(port in operation.outputs and port not in products for port in effect.bindings.values()):
                        raise DeclarationError("Effect binding references absent outcome product")
                    for name in (*effect.bindings, *effect.references):
                        symbol(name)
                    if effect.bindings.keys() & effect.references.keys():
                        raise DeclarationError("Effect port and mechanical reference handles must be disjoint")
                    for reference in effect.references.values():
                        self._validate_reference(reference, places, transitions, slots)
        for transition in transitions.values():
            if transition.operation not in operations:
                raise DeclarationError(f"Unknown operation: {transition.operation}")
        if {t.operation for t in transitions.values()} != operations.keys():
            raise DeclarationError("Every operation must be used by a transition")
        seen_resets = set()
        seen_selectors = set()
        for reset in self.reset_arcs:
            identity = (
                reset.place, reset.transition, reset.selector, reset.outcome)
            selector_identity = (
                reset.transition, reset.selector, reset.outcome)
            if identity in seen_resets or selector_identity in seen_selectors:
                raise DeclarationError("Duplicate or ambiguous reset arc")
            seen_resets.add(identity)
            seen_selectors.add(selector_identity)
            symbol(reset.selector)
            if (reset.place not in places
                    or reset.transition not in transitions):
                raise DeclarationError(
                    "Reset arc endpoint is not an exact fragment member")
            place = places[reset.place]
            if (place.reusable
                    or place.token_kind in {"agent_resource", "resource_lease"}):
                raise DeclarationError(
                    "Reset arc cannot retire reusable capacity or lease authority")
            operation = operations[transitions[reset.transition].operation]
            outcomes = tuple(
                outcome for outcome in operation.outcomes
                if reset.outcome is None or outcome.name == reset.outcome)
            if not outcomes:
                raise DeclarationError("Reset arc names an undeclared outcome")
            for outcome in outcomes:
                references = tuple(
                    effect.references[reset.selector]
                    for effect in outcome.effects
                    if reset.selector in effect.references)
                if (len(references) != 1
                        or references[0]
                        != SymbolReference("place", reset.place)):
                    raise DeclarationError(
                        "Reset arc selector lacks one exact outcome effect reference")
        seen_selected_outputs = set()
        for arc in self.arcs:
            if arc.effect_selector is None:
                continue
            selector_identity = (
                arc.transition, arc.effect_selector, arc.outcome)
            if selector_identity in seen_selectors or selector_identity in seen_selected_outputs:
                raise DeclarationError(
                    "Duplicate or ambiguous Petri action selector")
            seen_selected_outputs.add(selector_identity)
            symbol(arc.effect_selector)
            if (arc.direction != "output"
                    or arc.emit != "control_only"
                    or arc.mode != "produce"
                    or arc.forward_source is not None
                    or places[arc.place].reusable
                    or places[arc.place].token_kind in {
                        "agent_resource", "resource_lease"}):
                raise DeclarationError(
                    "Selected output must be one formal control-token arc")
            operation = operations[transitions[arc.transition].operation]
            outcomes = tuple(
                outcome for outcome in operation.outcomes
                if arc.outcome is None or outcome.name == arc.outcome)
            if not outcomes:
                raise DeclarationError(
                    "Selected output arc names an undeclared outcome")
            for outcome in outcomes:
                references = tuple(
                    effect.references[arc.effect_selector]
                    for effect in outcome.effects
                    if arc.effect_selector in effect.references)
                if (len(references) != 1
                        or references[0]
                        != SymbolReference("place", arc.place)):
                    raise DeclarationError(
                        "Selected output selector lacks one exact outcome effect reference")
        seen = set()
        for arc in self.arcs:
            identity = (arc.place, arc.transition, arc.direction, arc.outcome)
            if identity in seen:
                raise DeclarationError("Duplicate arc")
            seen.add(identity)
            if arc.place not in places or arc.transition not in transitions:
                raise DeclarationError("Arc endpoint is not an exact fragment member")
            if arc.direction not in ("input", "output") or type(arc.weight) is not int or arc.weight < 1:
                raise DeclarationError("Invalid arc direction/weight")
            if arc.mode not in ("consume", "read", "borrow", "guard", "produce", "return") or (arc.direction == "output") != (arc.mode in ("produce", "return")):
                raise DeclarationError("Invalid arc mode")
            operation = operations[transitions[arc.transition].operation]
            public_names = {name for name, binding in ports.items() if binding.place == arc.place}
            allowed_names = operation.inputs if arc.direction == "input" or arc.mode == "return" else operation.outputs
            if arc.mode != "guard" and public_names and not public_names.intersection(allowed_names):
                raise DeclarationError("Arc uses a public port outside its operation interface")
            if arc.outcome is not None and (arc.direction != "output" or arc.outcome not in {o.name for o in operation.outcomes}):
                raise DeclarationError("Invalid arc outcome")
            if places[arc.place].capacity is not None and arc.mode != "guard" and arc.weight > places[arc.place].capacity:
                raise DeclarationError("PN arc weight exceeds place capacity")
            if arc.direction == "input" and (arc.emit != "produced" or arc.forward_source is not None
                    or arc.colour_expression is not None or arc.output_predicates
                    or arc.lease_claims or arc.lease_claim_exclusions
                    or arc.lease_claim_set is not None
                    or arc.effect_selector is not None):
                raise DeclarationError("Output inscription on input arc")
            if arc.direction == "output":
                self._validate_output(arc, places)
        # Public ports are executable contracts, not just type annotations.
        for transition in transitions.values():
            operation = operations[transition.operation]
            self._validate_guards(transition, places)
            for name in operation.inputs:
                matches = [a for a in self.arcs if a.transition == transition.name
                           and a.place == ports[name].place and a.direction == "input" and a.mode != "guard"]
                # A shared operation may expose an optional input while one
                # explicit PN branch proves its absence. Quantity zero alone
                # never changes enabling: the branch must carry an exact
                # guard arc and a predicate admitting only the empty marking.
                absence_arcs = [a for a in self.arcs if a.transition == transition.name
                                and a.place == ports[name].place
                                and a.direction == "input" and a.mode == "guard"]
                absent = any(g.place == ports[name].place and
                             ((g.comparison in ("inhibitor", "lt") and g.threshold == 1)
                              or (g.comparison in ("eq", "le") and g.threshold == 0))
                             for g in transition.count_guards)
                if not matches and expected[name].minimum == 0 and len(absence_arcs) == 1 and absent:
                    continue
                if len(matches) != 1:
                    raise DeclarationError(f"Missing exact input PN arc: {name}")
            for outcome in operation.outcomes:
                products = {p.port: p for p in outcome.products}
                for name in operation.outputs:
                    matches = [a for a in self.arcs if a.transition == transition.name
                               and a.place == ports[name].place and a.direction == "output"
                               and a.outcome in (None, outcome.name)]
                    product = products.get(name)
                    if product is None:
                        shared_product = any(ports[p].place == ports[name].place for p in products)
                        if matches and not shared_product and any(a.emit == "produced" for a in matches):
                            raise DeclarationError("Arc produces undeclared outcome product")
                    elif product.maximum > 0 and not matches:
                        raise DeclarationError("Outcome product lacks an exact output PN arc")
        self._validate_reusable(places, transitions, operations)
        self._validate_leases_and_slots(places, transitions, operations, ports, expected, slots, leases, pools)

    @staticmethod
    def _validate_reference(reference, places, transitions, slots):
        if not isinstance(reference, SymbolReference):
            raise DeclarationError("Mechanical effect reference must be typed")
        symbol(reference.name)
        members = {"place": places, "transition": transitions, "slot": slots}
        if reference.kind not in members or reference.name not in members[reference.kind]:
            raise DeclarationError("Mechanical reference is not an exact typed member")

    def _inputs(self, transition, place=None):
        return [a for a in self.arcs if a.transition == transition and a.direction == "input"
                and a.mode != "guard" and (place is None or a.place == place)]

    def _validate_guards(self, transition, places):
        seen = set()
        for guard in transition.count_guards:
            if not isinstance(guard, CountGuard) or guard.place not in places:
                raise DeclarationError("Count guard requires an exact declared place")
            if (guard.comparison not in ("threshold", "inhibitor", "lt", "le", "eq", "ne", "ge", "gt")
                    or type(guard.threshold) is not int or guard.threshold < 0
                    or guard.scope not in ("reader", "all")):
                raise DeclarationError("Count comparison requires an exact nonnegative threshold")
            identity = guard.place, guard.comparison, guard.threshold, guard.scope
            if identity in seen:
                raise DeclarationError("Duplicate count guard")
            seen.add(identity)
        for arc in self.arcs:
            if arc.transition == transition.name and arc.mode == "guard" and not any(g.place == arc.place for g in transition.count_guards):
                raise DeclarationError("Guard arc lacks its typed count predicate")
        seen = set()
        for guard in transition.input_verdicts:
            if not isinstance(guard, InputVerdictGuard) or guard.place not in places:
                raise DeclarationError("Input verdict requires an exact place")
            if guard.place in seen:
                raise DeclarationError("Duplicate input verdict guard")
            seen.add(guard.place)
            inputs = self._inputs(transition.name, guard.place)
            if len(inputs) != 1 or inputs[0].mode not in ("consume", "borrow"):
                raise DeclarationError("Verdict must inspect a consumed or borrowed input")
            if colour_key(guard.expected) not in tuple(colour_key(c) for c in places[guard.place].colours):
                raise DeclarationError("Input verdict outside place colour domain")

    def _validate_output(self, arc, places):
        if arc.emit not in (
                "produced", "content_less", "forward", "control_only",
                "route_selected", "lease_mint"):
            raise DeclarationError("Unknown output emit inscription")
        if (arc.emit == "forward") != (arc.forward_source is not None):
            raise DeclarationError("Forward emit requires its exact source and only forward may carry one")
        if arc.forward_source is not None:
            inputs = self._inputs(arc.transition, arc.forward_source)
            if arc.forward_source not in places or len(inputs) != 1 or inputs[0].weight != arc.weight:
                raise DeclarationError("Forward source must be an exact input with matching PN weight")
            source, target = places[arc.forward_source], places[arc.place]
            if source.channel != target.channel or not set(source.admitted_schemas) <= set(target.admitted_schemas):
                raise DeclarationError("Forwarded token schemas/channel not admitted by destination")
            if arc.colour_expression is None and not {colour_key(c) for c in source.colours} <= {colour_key(c) for c in target.colours}:
                raise DeclarationError("Forwarded colours not admitted by destination")
        seen = set()
        for predicate in arc.output_predicates:
            if not isinstance(predicate, InputColourPredicate) or predicate.place not in places:
                raise DeclarationError("Output predicate requires typed exact input place")
            if predicate.place in seen:
                raise DeclarationError("Duplicate output input-colour predicate")
            seen.add(predicate.place)
            inputs = self._inputs(arc.transition, predicate.place)
            if len(inputs) != 1 or inputs[0].mode not in ("consume", "borrow"):
                raise DeclarationError("Output predicate must inspect consumed/borrowed input colour")
            expected = colour_key(predicate.expected)
            if expected not in {colour_key(c) for c in places[predicate.place].colours}:
                raise DeclarationError("Output predicate outside input colour domain")
            transition = next(t for t in self.transitions if t.name == arc.transition)
            if any(g.place == predicate.place and colour_key(g.expected) != expected for g in transition.input_verdicts):
                raise DeclarationError("Output predicate contradicts exact input verdict")
        expression = arc.colour_expression
        if expression is None:
            return
        if not isinstance(expression, ColourExpression):
            raise DeclarationError("Output colour expression must be typed")
        if expression.kind == "literal":
            if (expression.source_place is not None or expression.member is not None or expression.candidates
                    or expression.selected or expression.present is not None or expression.absent is not None):
                raise DeclarationError("Literal colour expression has extraneous operands")
            results = (expression.value,)
        elif expression.kind == "input_colour":
            if (expression.source_place not in places or len(self._inputs(arc.transition, expression.source_place)) != 1
                    or expression.value is not None or expression.member is not None or expression.candidates
                    or expression.selected or expression.present is not None or expression.absent is not None):
                raise DeclarationError("Input colour expression requires only an exact input source")
            results = places[expression.source_place].colours
            if not results:
                raise DeclarationError("Input colour expression source needs a declared domain")
        elif expression.kind == "membership":
            if expression.value is not None or expression.source_place is not None:
                raise DeclarationError("Membership colour expression has extraneous operands")
            for key in (*expression.candidates, *expression.selected):
                symbol(key)
            symbol(expression.member)
            if (len(set(expression.candidates)) != len(expression.candidates)
                    or len(set(expression.selected)) != len(expression.selected)
                    or expression.member not in expression.candidates
                    or not set(expression.selected) <= set(expression.candidates)):
                raise DeclarationError("Membership projection differs from its declared symbolic catalog")
            results = (expression.present, expression.absent)
        else:
            raise DeclarationError("Unknown mechanical colour expression")
        if not {colour_key(c) for c in results} <= {colour_key(c) for c in places[arc.place].colours}:
            raise DeclarationError("Projected colour outside destination domain")

    def _validate_reusable(self, places, transitions, operations):
        for transition in transitions.values():
            for arc in self._inputs(transition.name):
                place = places[arc.place]
                if arc.mode != "borrow" and not place.reusable:
                    continue
                if arc.mode == "read":
                    continue  # Non-consuming read arcs do not withdraw capacity.
                for outcome in operations[transition.operation].outcomes:
                    outputs = [a for a in self.arcs if a.transition == transition.name and a.direction == "output"
                               and a.place == arc.place and a.outcome in (None, outcome.name)]
                    if (len(outputs) != 1 or outputs[0].weight != arc.weight or outputs[0].output_predicates
                            or (arc.mode == "borrow" and outputs[0].mode != "return")):
                        raise DeclarationError("Borrow/reusable capacity requires exact unconditional return per outcome")
                    output = outputs[0]
                    if place.token_kind in ("agent_resource", "resource_lease"):
                        if output.emit != "content_less" or output.colour_expression is not None:
                            raise DeclarationError("Reusable control capacity must return content-less unchanged")
                    elif output.emit != "forward" or output.forward_source != arc.place or output.colour_expression is not None:
                        raise DeclarationError("Borrowed data/control token requires exact unchanged forwarding return")
        for arc in self.arcs:
            if arc.mode == "return" and not any(a.mode == "borrow" for a in self._inputs(arc.transition, arc.place)):
                raise DeclarationError("Return arc lacks its exact borrowed input")

    def _validate_leases_and_slots(self, places, transitions, operations, ports, expected, slots, leases, pools):
        for slot in slots.values():
            if slot.producer_transition not in transitions or slot.candidate_place not in places or slot.published_place not in places:
                raise DeclarationError("Logical slot endpoints must be exact fragment members")
            operation = operations[transitions[slot.producer_transition].operation]
            if (slot.output_port not in operation.outputs or ports[slot.output_port].place != slot.candidate_place
                    or expected[slot.output_port].schema != slot.schema
                    or slot.schema not in places[slot.published_place].admitted_schemas
                    or places[slot.candidate_place].channel != places[slot.published_place].channel):
                raise DeclarationError("Logical slot requires exact producer port/schema and admitted publication place")
            if slot.reviewer_transition is not None and slot.reviewer_transition not in transitions:
                raise DeclarationError("Logical slot reviewer must be an exact transition")
            for names in (slot.route_transitions, slot.consumer_transitions):
                if len(set(names)) != len(names) or any(n not in transitions for n in names):
                    raise DeclarationError("Logical slot transition catalog is not exact")
            for name in slot.route_transitions:
                if not any(a.transition == name and a.place == slot.published_place and a.direction == "output"
                           and a.emit == "forward" and a.forward_source == slot.candidate_place for a in self.arcs):
                    raise DeclarationError("Logical publication route must forward its exact candidate")
            for name in slot.consumer_transitions:
                if not self._inputs(name, slot.published_place):
                    raise DeclarationError("Logical consumer must have its exact published input")
        for lease in leases.values():
            if (lease.kind not in ("resource", "slot") or (lease.kind == "slot" and lease.slot not in slots)
                    or (lease.kind == "resource" and lease.slot is not None)):
                raise DeclarationError("Lease identity must bind an exact symbolic resource or logical slot")
        if len({l.slot for l in leases.values() if l.kind == "slot"}) != sum(l.kind == "slot" for l in leases.values()):
            raise DeclarationError("Logical slot has duplicate lease identities")
        for arc in self.arcs:
            if (not isinstance(arc.lease_claims, tuple)
                    or any(not isinstance(item, LeaseClaimExpression)
                           for item in arc.lease_claims)
                    or not isinstance(arc.lease_claim_exclusions, tuple)
                    or any(not isinstance(item, str) or not item
                           for item in arc.lease_claim_exclusions)):
                raise DeclarationError(
                    "Output lease-colour projection must be typed")
            claim_set = arc.lease_claim_set
            if claim_set is not None and (
                    not isinstance(claim_set, LeaseClaimSetExpression)
                    or type(claim_set.inherit_input_claims) is not bool
                    or type(claim_set.operation_resource_updates) is not bool
                    or type(claim_set.claim_output_products) is not bool
                    or claim_set.access_mode != "read"
                    or claim_set.registered_pool_subset is not None
                    and claim_set.registered_pool_subset not in pools
                    or not any((
                        claim_set.inherit_input_claims,
                        claim_set.operation_resource_updates,
                        claim_set.claim_output_products,
                        claim_set.registered_pool_subset is not None))):
                raise DeclarationError(
                    "Output lease-claim set expression is incomplete")
            identities = tuple(
                item.lease_identity for item in arc.lease_claims)
            if (len(set(identities)) != len(identities)
                    or len(set(arc.lease_claim_exclusions))
                    != len(arc.lease_claim_exclusions)
                    or set(identities) & set(arc.lease_claim_exclusions)
                    or any(name not in leases for name in (
                        *identities, *arc.lease_claim_exclusions))):
                raise DeclarationError(
                    "Output lease-colour identities must be exact and disjoint")
            if ((arc.lease_claims or arc.lease_claim_exclusions
                    or arc.lease_claim_set is not None)
                    and arc.direction != "output"):
                raise DeclarationError(
                    "Lease-colour expressions belong only to output arcs")
            for projection in arc.lease_claims:
                lease = leases[projection.lease_identity]
                source = projection.expected_resource_source
                inputs = self._inputs(arc.transition, source)
                if (projection.access_mode not in {"read", "edit", "produce"}
                        or lease.kind == "resource"
                        and projection.access_mode != "read"
                        or projection.access_mode == "produce"
                        and source is not None
                        or projection.access_mode != "produce"
                        and (source not in places or len(inputs) != 1
                             or inputs[0].weight != 1)):
                    raise DeclarationError(
                        "Output lease-colour expression has no exact input source")
        seen_places = set()
        for pool in pools.values():
            if (pool.place not in places or places[pool.place].token_kind != "resource_lease"
                    or not places[pool.place].reusable or pool.place in seen_places):
                raise DeclarationError("Lease pool requires exact unique reusable lease capacity place")
            seen_places.add(pool.place)
            for kind, names in (("resource", pool.initial_resources), ("slot", pool.initial_slots)):
                if len(set(names)) != len(names) or any(n not in leases or leases[n].kind != kind for n in names):
                    raise DeclarationError("Lease pool initial colours require exact unique typed identities")
            initial_count = len(pool.initial_resources) + len(pool.initial_slots)
            capacity = places[pool.place].capacity
            if (places[pool.place].initial_tokens
                    or (capacity is not None
                        and capacity != initial_count)):
                raise DeclarationError(
                    "Initial lease colours require either their exact fixed capacity "
                    "or an unbounded dynamic pool, without duplicate initial tokens")
        pool_places = {pool.place for pool in pools.values()}
        for arc in self.arcs:
            if arc.emit != "lease_mint":
                continue
            operation = operations[transitions[arc.transition].operation]
            output_ports = tuple(
                name for name in operation.outputs
                if ports[name].place == arc.place)
            if (arc.direction != "output" or arc.mode != "produce"
                    or arc.weight != 1 or arc.place not in pool_places
                    or not output_ports or arc.forward_source is not None
                    or arc.colour_expression is not None or arc.output_predicates):
                raise DeclarationError(
                    "Lease mint requires one or more exact product ports and a unit "
                    "output arc to a declared resource lease pool")
        seen = set()
        for arc in self.variable_resource_arcs:
            if arc.transition in seen or arc.transition not in transitions or arc.lease_pool not in pools:
                raise DeclarationError("Variable resource arc requires unique exact transition and pool")
            seen.add(arc.transition)
            inputs = self._inputs(arc.transition, arc.claim_token_place)
            if len(inputs) != 1 or inputs[0].mode not in ("consume", "borrow"):
                raise DeclarationError("Variable claims require an exact consumed/borrowed claim token place")
            if arc.input_inscription != "consume_exact_claim_multiset" or arc.output_inscription != "return_exact_claim_multiset":
                raise DeclarationError("Variable resource arc must borrow and return the exact claim multiset")
            pool = pools[arc.lease_pool]
            identities = set(pool.initial_resources + pool.initial_slots)
            claims = set()
            for claim in arc.initial_claims:
                if not isinstance(claim, LeaseClaimTemplate) or claim.lease_identity not in identities or claim.lease_identity in claims:
                    raise DeclarationError("Variable lease claim must use a unique exact pool identity")
                claims.add(claim.lease_identity)
                lease = leases[claim.lease_identity]
                if claim.access_mode not in ("read", "edit", "produce") or (lease.kind == "resource" and claim.access_mode != "read"):
                    raise DeclarationError("Variable lease claim mode/type mismatch")
                if claim.expected_resource is not None and (claim.expected_resource not in leases or leases[claim.expected_resource].kind != "resource"):
                    raise DeclarationError("Expected resource must be an exact symbolic resource identity")
                if lease.kind == "resource" and claim.expected_resource != lease.name:
                    raise DeclarationError("Static resource read must bind its exact expected resource")
                if claim.access_mode == "produce" and claim.expected_resource is not None:
                    raise DeclarationError("New logical production cannot claim an existing resource")


class ComponentLowerer(Protocol):
    def lower(self, config: Mapping[str, Any], context: BindingContext) -> PNFragment: ...
