"""Pure transforms over the shared :class:`ModuleDeclaration` wire.

These functions do not inspect or mutate a Registry.  Their results must still
be lowered with the caller's exact HOST ``Registration`` before publication.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from ..module import ModuleDeclaration


_SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _symbol(value: str, label: str) -> str:
    if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None:
        raise ValueError(f"{label} must be a Module symbol")
    return value


@dataclass(frozen=True, slots=True)
class ExtractPlan:
    """A closed definition selection; never an active-marking selection."""

    kind: str = "whole_module"
    components: tuple[str, ...] = ()
    boundary_policy: str = "preserve_all_dependencies"
    output_name: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"whole_module", "components"}:
            raise ValueError("extract kind must be whole_module or components")
        if self.boundary_policy != "preserve_all_dependencies":
            raise ValueError("only preserve_all_dependencies is implemented")
        if (self.kind == "whole_module" and self.components
                or self.kind == "components" and not self.components):
            raise ValueError("component selection must match extract kind")
        if len(set(self.components)) != len(self.components):
            raise ValueError("extract components must be unique")
        for value in self.components:
            _symbol(value, "component")
        if self.output_name is not None:
            _symbol(self.output_name, "output_name")


@dataclass(frozen=True, slots=True)
class ExtractResult:
    module: ModuleDeclaration
    component_sources: Mapping[str, str]
    entry_sources: Mapping[str, str]
    exit_sources: Mapping[str, str]


def _boundary_name(component: str, port: str, occupied: set[str]) -> str:
    base = f"{component}_{port}"
    name = base
    ordinal = 2
    while name in occupied:
        name = f"{base}_{ordinal}"
        ordinal += 1
    occupied.add(name)
    return name


def extract_module(source: ModuleDeclaration, plan: ExtractPlan) -> ExtractResult:
    """Extract a whole Module or a dependency-closed component region.

    Crossing data boundaries become explicit public entry/exit ports.  A
    selected region must retain one of the source's declared terminal bindings;
    inventing a new business terminal is intentionally outside this pure API.
    """
    if not isinstance(source, ModuleDeclaration) or not isinstance(plan, ExtractPlan):
        raise TypeError("extract_module requires ModuleDeclaration and ExtractPlan")
    document = source.to_dict()
    selected = ({item["name"] for item in document["components"]}
                if plan.kind == "whole_module" else set(plan.components))
    available = {item["name"] for item in document["components"]}
    missing = selected - available
    if missing:
        raise ValueError("extract names unknown components: " + ", ".join(sorted(missing)))

    terminals = [document["terminal"], *document.get("terminal_alternatives", [])]
    retained_terminals = [item for item in terminals
                          if item["source"]["component"] in selected]
    if not retained_terminals:
        raise ValueError(
            "selected region has no declared terminal; select a complete region")

    entries = {name: value for name, value in document["entry"].items()
               if value["component"] in selected}
    exits = {name: value for name, value in document["exit"].items()
             if value["component"] in selected}
    entry_sources = {name: f'{value["component"]}.{value["port"]}'
                     for name, value in entries.items()}
    exit_sources = {name: f'{value["component"]}.{value["port"]}'
                    for name, value in exits.items()}
    entry_names, exit_names = set(entries), set(exits)
    links = []
    for link in document["links"]:
        source_selected = link["source"]["component"] in selected
        target_selected = link["target"]["component"] in selected
        if source_selected and target_selected:
            links.append(link)
        elif not source_selected and target_selected:
            endpoint = deepcopy(link["target"])
            name = _boundary_name(endpoint["component"], endpoint["port"], entry_names)
            entries[name] = endpoint
            entry_sources[name] = (
                f'{link["source"]["component"]}.{link["source"]["port"]}')
        elif source_selected and not target_selected:
            endpoint = deepcopy(link["source"])
            name = _boundary_name(endpoint["component"], endpoint["port"], exit_names)
            exits[name] = endpoint
            exit_sources[name] = (
                f'{link["target"]["component"]}.{link["target"]["port"]}')

    retained = retained_terminals[0]
    terminal_alternatives = retained_terminals[1:]
    endpoint = retained["source"]
    if endpoint not in exits.values():
        name = _boundary_name(endpoint["component"], endpoint["port"], exit_names)
        exits[name] = deepcopy(endpoint)
        exit_sources[name] = f'{endpoint["component"]}.{endpoint["port"]}'

    result = deepcopy(document)
    result["name"] = plan.output_name or document["name"]
    result["components"] = [item for item in document["components"]
                            if item["name"] in selected]
    result["links"] = links
    result["entry"] = entries
    result["exit"] = exits
    result["terminal"] = retained
    result["terminal_alternatives"] = terminal_alternatives
    module = ModuleDeclaration.from_dict(result)
    return ExtractResult(
        module=module,
        component_sources={name: name for name in sorted(selected)},
        entry_sources=dict(sorted(entry_sources.items())),
        exit_sources=dict(sorted(exit_sources.items())),
    )


@dataclass(frozen=True, slots=True)
class ComposeConnection:
    source_instance: str
    source_exit: str
    target_instance: str
    target_entry: str

    def __post_init__(self) -> None:
        for label, value in (
            ("source_instance", self.source_instance),
            ("source_exit", self.source_exit),
            ("target_instance", self.target_instance),
            ("target_entry", self.target_entry),
        ):
            _symbol(value, label)
        if self.source_instance == self.target_instance:
            raise ValueError("composition connections must cross instances")


@dataclass(frozen=True, slots=True)
class ComposePlan:
    name: str
    terminal_instance: str
    connections: tuple[ComposeConnection, ...] = ()
    mode: str = "explicit"

    def __post_init__(self) -> None:
        _symbol(self.name, "composition name")
        _symbol(self.terminal_instance, "terminal_instance")
        if self.mode not in {"explicit", "serial", "parallel"}:
            raise ValueError("compose mode must be explicit, serial or parallel")
        if any(not isinstance(item, ComposeConnection) for item in self.connections):
            raise TypeError("connections must contain ComposeConnection values")
        if self.mode == "parallel" and self.connections:
            raise ValueError("parallel composition has independent lanes; use explicit for joins")


def _merge_unique_documents(values: Sequence[dict], label: str, key: str) -> list[dict]:
    merged: dict[str, dict] = {}
    for value in values:
        identity = value[key]
        if identity in merged and merged[identity] != value:
            raise ValueError(f"conflicting {label}: {identity}")
        merged[identity] = value
    return [merged[name] for name in sorted(merged)]


def _prefixed_endpoint(endpoint: Mapping[str, str], prefix: str) -> dict[str, str]:
    return {"component": f'{prefix}_{endpoint["component"]}', "port": endpoint["port"]}


def compose_modules(
    instances: Mapping[str, ModuleDeclaration], plan: ComposePlan,
) -> ModuleDeclaration:
    """Flatten named Module instances and fuse only explicit connections.

    ``parallel`` means independent entry lanes.  It never copies one token or
    resource into several consumers.  Fan-out and all-settled joins therefore
    remain explicit registered components supplied by the application.
    """
    if not isinstance(instances, Mapping) or not instances:
        raise ValueError("compose requires at least one named Module instance")
    if not isinstance(plan, ComposePlan):
        raise TypeError("compose requires ComposePlan")
    for name, module in instances.items():
        _symbol(name, "instance")
        if not isinstance(module, ModuleDeclaration):
            raise TypeError("compose instances must be ModuleDeclaration values")
    if plan.terminal_instance not in instances:
        raise ValueError("terminal_instance is not present")

    documents = {name: module.to_dict() for name, module in instances.items()}
    connections = list(plan.connections)
    if plan.mode == "serial" and not connections:
        ordered = list(instances)
        for left, right in zip(ordered, ordered[1:]):
            left_exits = list(documents[left]["exit"])
            right_entries = list(documents[right]["entry"])
            if len(left_exits) != 1 or len(right_entries) != 1:
                raise ValueError(
                    "implicit serial composition requires one exit and one entry per boundary")
            connections.append(ComposeConnection(
                left, left_exits[0], right, right_entries[0]))

    seen_targets: set[tuple[str, str]] = set()
    links: list[dict] = []
    connected_sources: set[tuple[str, str]] = set()
    connected_targets: set[tuple[str, str]] = set()
    for connection in connections:
        if (connection.source_instance not in documents
                or connection.target_instance not in documents):
            raise ValueError("connection names an unknown instance")
        left = documents[connection.source_instance]["exit"].get(connection.source_exit)
        right = documents[connection.target_instance]["entry"].get(connection.target_entry)
        if left is None or right is None:
            raise ValueError("connection names an unknown public endpoint")
        target = (connection.target_instance, connection.target_entry)
        if target in seen_targets:
            raise ValueError("one composed entry cannot have multiple producers")
        seen_targets.add(target)
        connected_sources.add((connection.source_instance, connection.source_exit))
        connected_targets.add(target)
        links.append({
            "source": _prefixed_endpoint(left, connection.source_instance),
            "target": _prefixed_endpoint(right, connection.target_instance),
        })

    components: list[dict] = []
    component_names: set[str] = set()
    internal_links: list[dict] = []
    entries: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    schemas: set[str] = set()
    analyzers: set[str] = set()
    budgets: dict[str, int | float] = {}
    buckets: list[dict] = []
    for instance, document in documents.items():
        for component in deepcopy(document["components"]):
            component["name"] = f'{instance}_{component["name"]}'
            if component["name"] in component_names:
                raise ValueError(
                    "qualified component names collide: " + component["name"])
            component_names.add(component["name"])
            components.append(component)
        internal_links.extend({
            "source": _prefixed_endpoint(link["source"], instance),
            "target": _prefixed_endpoint(link["target"], instance),
        } for link in document["links"])
        for name, endpoint in document["entry"].items():
            if (instance, name) in connected_targets:
                continue
            qualified = f"{instance}_{name}"
            if qualified in entries:
                raise ValueError(
                    "qualified public entry names collide: " + qualified)
            entries[qualified] = _prefixed_endpoint(endpoint, instance)
        for name, endpoint in document["exit"].items():
            if (instance, name) in connected_sources:
                continue
            qualified = f"{instance}_{name}"
            if qualified in exits:
                raise ValueError(
                    "qualified public exit names collide: " + qualified)
            exits[qualified] = _prefixed_endpoint(endpoint, instance)
        schemas.update(document["required_schemas"])
        analyzers.update(document.get("analyzers", []))
        for key, value in document["budgets"].items():
            if key in budgets and budgets[key] != value:
                raise ValueError(f"conflicting budget value: {key}")
            budgets[key] = value
        buckets.extend(deepcopy(document.get("budget_buckets", [])))

    terminal_document = documents[plan.terminal_instance]
    terminal = deepcopy(terminal_document["terminal"])
    terminal["source"] = _prefixed_endpoint(
        terminal["source"], plan.terminal_instance)
    terminal_alternatives = []
    for item in terminal_document.get("terminal_alternatives", []):
        item = deepcopy(item)
        item["source"] = _prefixed_endpoint(
            item["source"], plan.terminal_instance)
        terminal_alternatives.append(item)
    terminal_endpoint = terminal["source"]
    if terminal_endpoint not in exits.values():
        # A terminal carrier may be connected into a declared join. In that
        # case the terminal instance must be the downstream join, not a lane.
        raise ValueError("terminal instance output is not an exposed composed exit")

    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": plan.name,
        "components": components,
        "links": [*internal_links, *links],
        "entry": dict(sorted(entries.items())),
        "exit": dict(sorted(exits.items())),
        "terminal": terminal,
        "terminal_alternatives": terminal_alternatives,
        "required_schemas": sorted(schemas),
        "budgets": dict(sorted(budgets.items())),
        "designer_constraints": {
            "native_composition": {
                "mode": plan.mode,
                "instances": list(instances),
            },
        },
        "analyzers": sorted(analyzers),
        "budget_buckets": _merge_unique_documents(
            buckets, "budget bucket", "bucket_id"),
    })


def instantiate_module(
    definition: ModuleDeclaration, *, instance: str, output_name: str | None = None,
) -> ModuleDeclaration:
    """Create one symbolically independent instance of a definition."""
    if not isinstance(definition, ModuleDeclaration):
        raise TypeError("instantiate requires a ModuleDeclaration")
    _symbol(instance, "instance")
    return compose_modules(
        {instance: definition},
        ComposePlan(
            name=output_name or f"{instance}_{definition.name}",
            terminal_instance=instance,
            mode="parallel",
        ),
    )


@dataclass(frozen=True, slots=True)
class BranchResult:
    definition: ModuleDeclaration
    instance: ModuleDeclaration | None
    sources: ExtractResult


def branch_module(
    source: ModuleDeclaration, plan: ExtractPlan, *,
    output_mode: str = "definition_only", instance: str | None = None,
) -> BranchResult:
    """Extract a region and optionally create one independent symbol instance."""
    if output_mode not in {"definition_only", "new_instance"}:
        raise ValueError("branch output_mode must be definition_only or new_instance")
    extracted = extract_module(source, plan)
    instantiated = None
    if output_mode == "new_instance":
        if instance is None:
            raise ValueError("new_instance branch requires an instance name")
        instantiated = instantiate_module(extracted.module, instance=instance)
    elif instance is not None:
        raise ValueError("definition_only branch does not accept an instance name")
    return BranchResult(extracted.module, instantiated, extracted)
