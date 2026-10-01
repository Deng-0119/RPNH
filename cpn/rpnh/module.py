"""One public Python/JSON declaration and independent symbolic PN composition.

This module does not adopt a net, execute operations or mint runtime identities.
The execution owner must compile the returned exact symbolic inventory into
Registry declarations and enforce transactional claim/effect/terminal checks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator, ValidationError, validators

from .budgets import validate_budget_buckets
from .petri_contracts import (
    ArcDeclaration, BindingContext, DeclarationError, EffectDeclaration,
    OperationDeclaration, OutcomeDeclaration, PNFragment, PlaceDeclaration,
    PortBinding, PortDeclaration, ProductDeclaration, RegistrationView,
    BudgetBindingDeclaration, SymbolReference, LeaseIdentityDeclaration,
    ResetArcDeclaration, ResourceLeasePoolBinding, VariableResourceArc,
    LogicalSlotBinding,
    TransitionDeclaration, same_operation_contract, unique, validate_registered_config,
)


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/rpnh/module_declaration.v1.schema.json"
MechanicalValidator = validators.extend(
    Draft7Validator,
    type_checker=Draft7Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int),
)


def validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        # Python declarations obey the same data-only contract as JSON clients.
        value = json.loads(json.dumps(document, allow_nan=False))
        MechanicalValidator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).validate(value)
        for component in value["components"]:
            for port in component["ports"]:
                PortDeclaration(**port).validate()
        if value.get("budget_buckets"):
            validate_budget_buckets(value["budget_buckets"])
    except (TypeError, ValueError, ValidationError) as exc:
        raise DeclarationError(f"Invalid ModuleDeclaration: {exc}") from exc
    return value


def _effect(document):
    return EffectDeclaration(
        key=document["key"], bindings=document.get("bindings", {}),
        config=document.get("config", {}),
        references={k: SymbolReference(**v) for k, v in document.get("references", {}).items()},
    )


def _operation(document):
    return OperationDeclaration(
        name=document["name"], executor=document["executor"],
        inputs=tuple(document["inputs"]), outputs=tuple(document["outputs"]),
        outcomes=tuple(OutcomeDeclaration(
            name=outcome["name"],
            products=tuple(ProductDeclaration(**p) for p in outcome["products"]),
            effects=tuple(_effect(e) for e in outcome.get("effects", [])),
        ) for outcome in document["outcomes"]),
        tools=tuple(document.get("tools", [])), config=document.get("config", {}),
        request_port=document.get("request_port"),
        budget_binding=BudgetBindingDeclaration(**document["budget_binding"])
        if document.get("budget_binding") is not None else None,
    )


@dataclass(frozen=True)
class ComponentDeclaration:
    name: str
    key: str
    config_schema: str
    config: Mapping[str, Any]
    ports: tuple[PortDeclaration, ...]
    operations: tuple[OperationDeclaration, ...] = ()


@dataclass(frozen=True)
class Endpoint:
    component: str
    port: str


@dataclass(frozen=True)
class LinkDeclaration:
    source: Endpoint
    target: Endpoint


@dataclass(frozen=True)
class TerminalBinding:
    key: str
    source: Endpoint
    operation: str
    outcome: str
    config: Mapping[str, Any]


@dataclass(frozen=True)
class BudgetBucketDeclaration:
    bucket_id: str
    budget_scope: str
    finalization_scope: str | None
    max_attempts: int | None


def _terminal(value):
    return TerminalBinding(value["key"], Endpoint(**value["source"]),
                           value["operation"], value["outcome"], value["config"])


@dataclass(frozen=True)
class SymbolicNet:
    """Qualified symbolic identities, never concrete Registry references.

    port_places preserves every exact external endpoint after place fusion.
    Operation port names are qualified identically; entry/exit map boundary
    names to these endpoints. terminal is a registered host rule, not evidence.
    Links fuse places; they do not duplicate products for broadcast consumers.
    """
    name: str
    places: tuple[PlaceDeclaration, ...]
    transitions: tuple[TransitionDeclaration, ...]
    arcs: tuple[ArcDeclaration, ...]
    operations: tuple[OperationDeclaration, ...]
    port_places: Mapping[str, str]
    entry: Mapping[str, str]
    exit: Mapping[str, str]
    terminal: TerminalBinding
    required_schemas: tuple[str, ...]
    budgets: Mapping[str, int | float]
    designer_constraints: Mapping[str, Any]
    analyzers: tuple[str, ...]

    lease_identities: tuple[LeaseIdentityDeclaration, ...] = ()
    lease_pools: tuple[ResourceLeasePoolBinding, ...] = ()
    variable_resource_arcs: tuple[VariableResourceArc, ...] = ()
    logical_slots: tuple[LogicalSlotBinding, ...] = ()
    terminal_alternatives: tuple[TerminalBinding, ...] = ()
    budget_buckets: tuple[BudgetBucketDeclaration, ...] = ()
    reset_arcs: tuple[ResetArcDeclaration, ...] = ()

    @property
    def terminal_operation(self) -> str:
        return f"{self.terminal.source.component}.{self.terminal.operation}"

    def validate_products(self, operation: str, outcome: str,
                          products: Mapping[str, list[Any]], registration: RegistrationView):
        """Check qualified local product data, without publishing anything."""
        selected = next((op for op in self.operations if op.name == operation), None)
        if selected is None:
            raise DeclarationError("Unknown exact composed operation")
        places = {p.name: p for p in self.places}
        ports = {name: PortDeclaration(name, "output", places[self.port_places[name]].schema)
                 for name in selected.outputs}
        selected.validate_products(outcome, products, ports, registration)


@dataclass(frozen=True)
class ModuleDeclaration:
    name: str
    components: tuple[ComponentDeclaration, ...]
    links: tuple[LinkDeclaration, ...]
    entry: Mapping[str, Endpoint]
    exit: Mapping[str, Endpoint]
    terminal: TerminalBinding
    required_schemas: tuple[str, ...]
    budgets: Mapping[str, int | float]
    designer_constraints: Mapping[str, Any] | None = None
    analyzers: tuple[str, ...] = ()
    schema_version: str = "rpnh/module_declaration/v1"
    terminal_alternatives: tuple[TerminalBinding, ...] = ()
    budget_buckets: tuple[BudgetBucketDeclaration, ...] = ()

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> ModuleDeclaration:
        value = validate_document(document)
        return cls(
            name=value["name"], schema_version=value["schema_version"],
            components=tuple(ComponentDeclaration(
                name=c["name"], key=c["key"], config_schema=c["config_schema"],
                config=c["config"], ports=tuple(PortDeclaration(**p) for p in c["ports"]),
                operations=tuple(_operation(o) for o in c.get("operations", [])),
            ) for c in value["components"]),
            links=tuple(LinkDeclaration(Endpoint(**l["source"]), Endpoint(**l["target"])) for l in value["links"]),
            entry={k: Endpoint(**v) for k, v in value["entry"].items()},
            exit={k: Endpoint(**v) for k, v in value["exit"].items()},
            terminal=TerminalBinding(
                key=value["terminal"]["key"], source=Endpoint(**value["terminal"]["source"]),
                operation=value["terminal"]["operation"],
                outcome=value["terminal"]["outcome"], config=value["terminal"]["config"],
            ), required_schemas=tuple(value["required_schemas"]), budgets=value["budgets"],
            designer_constraints=value.get("designer_constraints", {}),
            analyzers=tuple(value.get("analyzers", [])),
            terminal_alternatives=tuple(_terminal(v) for v in value.get("terminal_alternatives", [])),
            budget_buckets=tuple(BudgetBucketDeclaration(**v) for v in value.get("budget_buckets", [])),
        )

    @classmethod
    def from_json(cls, document: str) -> ModuleDeclaration:
        try:
            return cls.from_dict(json.loads(document))
        except json.JSONDecodeError as exc:
            raise DeclarationError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["designer_constraints"] = self.designer_constraints or {}
        return validate_document(value)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def lower(self, registration: RegistrationView) -> SymbolicNet:
        # Direct Python construction has exactly the same acceptance boundary.
        declaration = self.from_dict(self.to_dict())
        components = unique(declaration.components, "component")
        for key in declaration.required_schemas:
            registration.declaration("schema", key)
        for key in declaration.analyzers:
            registration.resolve("analyzer", key)
        for terminal in (declaration.terminal, *declaration.terminal_alternatives):
            registration.resolve("tool", terminal.key)
            validate_registered_config(registration, "tool", terminal.key,
                                       terminal.config, declaration.required_schemas)
        fragments = {}
        endpoint_ports = {}
        for component in components.values():
            if component.config_schema not in declaration.required_schemas:
                raise DeclarationError("Component config schema must be required")
            schema = registration.declaration("schema", component.config_schema)["schema"]
            contracts = registration.declaration("component", component.key)["contracts"]
            if contracts.get("config_schema", component.config_schema) != component.config_schema:
                raise DeclarationError("Component config schema differs from HOST contract")
            try:
                Draft7Validator(schema).validate(component.config)
            except ValidationError as exc:
                raise DeclarationError(f"Invalid config for {component.name}: {exc.message}") from exc
            context = BindingContext(component.name, component.ports,
                                     declaration.required_schemas, declaration.budgets,
                                     component.operations)
            fragment = registration.resolve("component", component.key)(component.config, context)
            if not isinstance(fragment, PNFragment):
                raise DeclarationError("Registered lower must return a typed PNFragment")
            fragment.validate(context, registration)
            lowered_operations = {o.name: o for o in fragment.operations}
            if any(o.name not in lowered_operations or not same_operation_contract(
                    lowered_operations[o.name], o) for o in component.operations):
                raise DeclarationError("Lowered operations must exactly preserve declared protocols")
            fragments[component.name] = fragment
            endpoint_ports.update({f"{component.name}.{p.name}": p for p in component.ports})

        from .composition import compose_fragments
        return compose_fragments(declaration, fragments)[0]
