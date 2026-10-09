"""Inert iteration authoring sugar for the existing Module compiler.

The sole v1 template is fixed, finite and uses the optional basic component.
No evaluator, selector, provider, Registry, publication or run is created here.
Only caller-owned Registration keys are resolved; HOST business code is never
called. Model-profile references are nonsecret, unresolved binding obligations.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from cpn.components.schema_catalog import schema_data
from .compiler import compile_module
from .executable_net import CompiledPetriNet
from .module import ModuleDeclaration
from .petri_contracts import DeclarationError
from .registration import Registration
from .registry.module_budgets import ModuleBudgetDeclaration
from .registry.schema_catalog import SchemaCatalog


PROFILE_SCHEMA = "rpnh/iteration_profile/v1"
_MAX_PROFILE_BYTES = 65_536
_ROLES = ("proposer", "evaluator", "selector")


def iteration_profile_schema_data():
    """Opt-in content schema inventory; not a mechanical catalog extension."""
    return schema_data((PROFILE_SCHEMA,), (), (), owner="authoring")


def _json_data(value):
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise DeclarationError("Iteration profile keys must be strings")
        return {key: _json_data(item) for key, item in value.items()}
    if type(value) is list:
        return [_json_data(item) for item in value]
    if value is not None and type(value) not in (str, bool, int, float):
        raise DeclarationError("Iteration profile requires finite JSON data")
    return value


def _validate(document):
    value = _json_data(document)
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (ValueError, TypeError) as exc:
        raise DeclarationError("Iteration profile requires finite JSON data") from exc
    if len(serialized.encode("utf-8")) > _MAX_PROFILE_BYTES:
        raise DeclarationError("Iteration profile exceeds 65536 bytes")
    schemas, types, paths = iteration_profile_schema_data()
    SchemaCatalog(schemas=schemas, types=types, schema_paths=paths).validate_schema_ref(
        PROFILE_SCHEMA, value)
    if type(value["rounds"]) is not int or type(value["native_model_call_budget"]["maximum"]) is not int:
        raise DeclarationError("Iteration rounds and model-call budget require exact integers")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DeclarationError(f"Duplicate iteration profile key: {key}")
        value[key] = item
    return value


@dataclass(frozen=True)
class IterationProfile:
    """Immutable JSON author input; direct construction is rechecked at compile."""
    _json: str

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "IterationProfile":
        value = _validate(document)
        return cls(json.dumps(value, allow_nan=False, sort_keys=True))

    @classmethod
    def from_json(cls, document: str) -> "IterationProfile":
        if type(document) is not str or len(document.encode("utf-8")) > _MAX_PROFILE_BYTES:
            raise DeclarationError("Iteration profile requires bounded JSON text")
        try:
            return cls.from_dict(json.loads(document, object_pairs_hook=_unique_object))
        except (ValueError, RecursionError) as exc:
            raise DeclarationError(f"Invalid iteration profile JSON: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json, object_pairs_hook=_unique_object)


@dataclass(frozen=True)
class EntryBindingRequirement:
    name: str
    schema: str
    purpose: str


@dataclass(frozen=True)
class ModelBindingRequirement:
    operation: str
    profile_ref: str


@dataclass(frozen=True)
class IterationSourceMapEntry:
    component: str
    profile_path: str
    round: int


@dataclass(frozen=True)
class PreparedIteration:
    """Construction materials, not author revision, run receipt or authority.

    Exact HOST requirements remain in compiled.to_dict()['registrations'].
    The source map is diagnostic provenance, not a registered author map.
    """
    profile: IterationProfile
    module: ModuleDeclaration
    compiled: CompiledPetriNet
    budgets: ModuleBudgetDeclaration
    source_map: tuple[IterationSourceMapEntry, ...]
    required_entry_bindings: tuple[EntryBindingRequirement, ...]
    required_model_bindings: tuple[ModelBindingRequirement, ...]


def load_iteration_profile(path: str | Path) -> IterationProfile:
    """Read only this profile, never its model/data references or environment."""
    with Path(path).open("rb") as stream:
        document = stream.read(_MAX_PROFILE_BYTES + 1)
    if len(document) > _MAX_PROFILE_BYTES:
        raise DeclarationError("Iteration profile exceeds 65536 bytes")
    try:
        return IterationProfile.from_json(document.decode("utf-8"))
    except UnicodeError as exc:
        raise DeclarationError("Iteration profile must be UTF-8 JSON") from exc


def _local_schema_references(value):
    """The v1 convenience boundary cannot fetch schema documents during compile."""
    if isinstance(value, dict):
        if "$ref" in value and not (isinstance(value["$ref"], str) and value["$ref"].startswith("#")):
            raise DeclarationError("Iteration schemas require inline or local-fragment references")
        for item in value.values():
            _local_schema_references(item)
    elif isinstance(value, list):
        for item in value:
            _local_schema_references(item)


def compile_iteration_profile(profile: IterationProfile, *, registration: Registration) -> PreparedIteration:
    """Expand the trusted v1 template, then call the original compile_module.

    This performs syntax/contract checks, not runtime admission or score matching.
    The caller must bind approved inputs/HOSTs, publish and run via existing APIs.
    """
    if not isinstance(profile, IterationProfile) or not isinstance(registration, Registration):
        raise TypeError("Iteration compilation requires IterationProfile and HOST Registration")
    profile = IterationProfile.from_json(profile._json)
    value = profile.to_dict()
    # A profile cannot introduce an arbitrary lowerer. Reuse the shipped pure
    # component; schema/executor/tool matching remains Registration/compiler-owned.
    if registration.resolve("component", "operation") is not lower_operation:
        raise DeclarationError("Iteration v1 requires the standard basic operation lowerer")
    required = {CONFIG_SCHEMA_ID, *value["schemas"].values()}
    selections = [("executor", value["roles"][role]["executor_key"]) for role in _ROLES]
    selections.append(("tool", value["terminal_key"]))
    for category, key in selections:
        config_schema = registration.declaration(category, key)["contracts"].get("config_schema")
        if config_schema is not None:
            required.add(config_schema)
    for schema in sorted(required):
        _local_schema_references(registration.declaration("schema", schema)["schema"])

    components, links, source_map, models, terminals = [], [], [], [], []
    entry, exit_ports = {}, {}
    requirements = []
    schemas = value["schemas"]
    buckets = [{"bucket_id": role, "budget_scope": f"iteration_{role}",
                "finalization_scope": None, "max_attempts": None} for role in _ROLES]

    def endpoint(component, port):
        return {"component": component, "port": port}

    def link(source, output, target, input_name):
        links.append({"source": endpoint(source, output), "target": endpoint(target, input_name)})

    def component(name, role, inputs, outputs, outcomes, *, reads=()):
        ports = [{"name": port, "direction": direction, "schema": schema}
                 for direction, pairs in (("input", inputs), ("output", outputs))
                 for port, schema in pairs]
        components.append({
            "name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {"capacities": {port["name"]: 1 for port in ports},
                       "input_modes": {port: "read" for port in reads}},
            "ports": ports,
            "operations": [{"name": "run", "executor": value["roles"][role]["executor_key"],
                "inputs": [port for port, _ in inputs], "outputs": [port for port, _ in outputs],
                "config": {}, "outcomes": [{"name": outcome, "products": [{"port": port} for port in products]}
                                           for outcome, products in outcomes],
                "budget_binding": {"bucket_id": role, "budget_scope": f"iteration_{role}",
                                   "finalization_scope": None}}],
        })
        source_map.append(IterationSourceMapEntry(name, f"$.roles.{role}", round_number))
        model_ref = value["roles"][role]["model_profile_ref"]
        if model_ref is not None:
            models.append(ModelBindingRequirement(f"{name}.run", model_ref))

    for round_number in range(1, value["rounds"] + 1):
        prefix = f"round_{round_number}"
        propose, evaluate, select = (f"{prefix}_{role}" for role in _ROLES)
        component(propose, "proposer", [("state", schemas["state"])],
                  [("incumbent", schemas["state"]), ("candidate", schemas["candidate"])],
                  [("proposed", ["incumbent", "candidate"])])
        component(evaluate, "evaluator", [("candidate", schemas["candidate"]),
                  ("request", schemas["evaluation_request"])], [("evaluation", schemas["evaluation"])],
                  [("evaluated", ["evaluation"])], reads=("candidate",))
        component(select, "selector", [("incumbent", schemas["state"]),
                  ("candidate", schemas["candidate"]), ("evaluation", schemas["evaluation"])],
                  [("next", schemas["state"]), ("stop", schemas["state"])],
                  [("select", ["next"]), ("retain", ["next"]), ("stop", ["stop"])])
        link(propose, "incumbent", select, "incumbent")
        link(propose, "candidate", evaluate, "candidate")
        link(propose, "candidate", select, "candidate")
        link(evaluate, "evaluation", select, "evaluation")
        if round_number == 1:
            entry["initial_state"] = endpoint(propose, "state")
            requirements.append(EntryBindingRequirement("initial_state", schemas["state"], "incumbent_and_allowed_feedback"))
        else:
            link(f"round_{round_number - 1}_selector", "next", propose, "state")
        request = f"{prefix}_evaluation_request"
        entry[request] = endpoint(evaluate, "request")
        requirements.append(EntryBindingRequirement(request, schemas["evaluation_request"], "validation_request"))
        exit_ports[f"{prefix}_stop"] = endpoint(select, "stop")
        terminals.append({"key": value["terminal_key"], "source": endpoint(select, "stop"),
                          "operation": "run", "outcome": "stop",
                          "config": {"run_outcome": value["terminal_outcomes"]["stop"]}})
        if round_number == value["rounds"]:
            exit_ports["selected_state"] = endpoint(select, "next")
            terminals.extend({"key": value["terminal_key"], "source": endpoint(select, "next"),
                              "operation": "run", "outcome": outcome,
                              "config": {"run_outcome": value["terminal_outcomes"]["final_" + outcome]}}
                             for outcome in ("select", "retain"))

    module = ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": value["name"],
        "components": components, "links": links, "entry": entry, "exit": exit_ports,
        "terminal": terminals[-2], "terminal_alternatives": [*terminals[:-2], terminals[-1]],
        "required_schemas": sorted(required), "budgets": {}, "budget_buckets": buckets,
    })
    compiled = compile_module(module, registration)
    # Native model-call accounting only; structural rounds do not set call caps.
    budgets = ModuleBudgetDeclaration(tuple(buckets), ("rpnh/module_declaration/v1",),
                                     value["native_model_call_budget"]["maximum"], 0,
                                     value["native_model_call_budget"]["maximum"], 0)
    return PreparedIteration(profile, module, compiled, budgets, tuple(source_map),
                             tuple(requirements), tuple(models))


__all__ = ("IterationProfile", "PreparedIteration", "load_iteration_profile",
           "compile_iteration_profile", "iteration_profile_schema_data")
