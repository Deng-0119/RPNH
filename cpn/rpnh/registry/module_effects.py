"""Pure preparation of an exact OutcomeDeclaration Petri action bundle.

HOST tools parse application bytes into typed symbolic selections.  Formal
``route_selected`` output arcs and typed reset arcs alone bound affected
places; registration metadata grants no Petri mutation authority.  Existing
config schemas are validated against the Module's required schemas.
EffectDeclaration bindings select verified resource bundles.
The synchronous callback ABI is ``tool(DeclaredEffectContext) ->
DeclaredEffectInstruction``. It interprets application bytes, not Core.

This module neither writes nor mints identities. Callers must hydrate the latest
exact compiled net/marking, reclose the WHOLE operation output bundle, and supply
all currently provisional firing authorities and an exact read-only resource
reader. Returned instructions are proposals: the Success owner must recheck
claims/capacity and publish retirement, routes and its ONE successor in the same
transaction. Preparation alone is not Success or terminal evidence.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Callable, Mapping, Any

from jsonschema import Draft7Validator

from ..petri_contracts import SymbolReference, validate_registered_config
from ..registration import Registration
from ..runtime_net import RuntimeNet
from .errors import ResourceIntegrityFault
from .models import VersionRef
from .operations import RegisteredOperationOutputsAuthority
from .publication import _ref_payload, _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE
from .resources import (ExecutableNetAuthority, PetriTokenState,
                        ResourceVersionRef, TransitionFiringAuthority,
                        TypedMarkingAuthority)


DECLARED_PETRI_ACTION = "petri_action"


@dataclass(frozen=True, slots=True)
class BoundEffectResource:
    """Core-resolved exact resource, never a callback-supplied Registry ID."""
    resource_ref: ResourceVersionRef
    schema_id: str
    payload: bytes
    token: PetriTokenState | None


@dataclass(frozen=True, slots=True)
class DeclaredEffectContext:
    checkpoint_ref: VersionRef
    net_ref: VersionRef
    firing_ref: VersionRef
    outcome_id: str
    config: Mapping[str, Any]
    references: Mapping[str, SymbolReference]
    resources: Mapping[str, tuple[BoundEffectResource, ...]]
    tokens: tuple[PetriTokenState, ...]


@dataclass(frozen=True, slots=True)
class DeclaredRouteSelection:
    """One declared destination handle and ordinal in a bound resource bundle."""
    reference: str
    binding: str
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class DeclaredEffectInstruction:
    routes: tuple[DeclaredRouteSelection, ...] = ()
    reset_references: tuple[str, ...] = ()
    output_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedDeclaredRoute:
    place: str
    source: BoundEffectResource
    source_port: str
    colour: bool | str | None


@dataclass(frozen=True, slots=True)
class PreparedSelectedOutput:
    """One selected formal control-token output arc."""

    transition_id: str
    place: str
    selector: str
    colour: bool | str | None
    weight: int


@dataclass(frozen=True, slots=True)
class PreparedModuleEffects:
    checkpoint_ref: VersionRef
    net_ref: VersionRef
    firing_ref: VersionRef
    outcome_id: str
    routes: tuple[PreparedDeclaredRoute, ...]
    reset_places: tuple[str, ...]
    retired_token_refs: tuple[VersionRef, ...]
    witnesses: tuple[dict, ...] = ()
    revisions: tuple[tuple[Any, DeclaredEffectContext], ...] = ()
    selected_outputs: tuple[PreparedSelectedOutput, ...] = ()


def _readonly(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_readonly(item) for item in value)
    return value


def _formal_reset_retirement_reason(
        state: PetriTokenState, place, *, claimed_elsewhere: bool,
        terminal: bool) -> str | None:
    """Return the authority that prevents one selected formal reset.

    ``work_resource_ref`` is occurrence metadata pointing to an immutable
    Registry resource.  Retiring the ordinary Petri occurrence does not delete
    that resource and therefore does not constitute unresolved authority.
    """
    if claimed_elsewhere:
        return "token is claimed by another active firing"
    if place.reusable or place.token_kind in {"agent_resource", "resource_lease"}:
        return "token carries reusable capacity/lease authority"
    if terminal:
        return "token occupies a terminal place"
    if state.lease_identity_ref is not None or state.lease_claims:
        return "token carries unresolved lease authority"
    if (state.kind is not None or state.continuation is not None
            or state.override_warning is not None):
        return "token carries unresolved coloured work authority"
    if state.consumed_by is not None:
        return "token is not a fresh predecessor occurrence"
    return None


def prepare_module_effects(
    executable: ExecutableNetAuthority,
    structure: RuntimeNet,
    predecessor: TypedMarkingAuthority,
    outputs: RegisteredOperationOutputsAuthority,
    registration: Registration,
    *,
    active_firings: tuple[TransitionFiringAuthority, ...],
    read_resource: Callable[[ResourceVersionRef], bytes],
    read_resource_media_type: Callable[[ResourceVersionRef], str],
) -> PreparedModuleEffects:
    """Resolve selected effects to bounded data; no implicit lifecycle policy.

    ``active_firings`` is mandatory, including read claims; the current firing
    may be included. Only its normally consumed inputs are excluded from reset
    retirement. Existing reusable/lease/claim/continuation/terminal authority
    cannot be retired. Old token/resource objects remain evidence; a selected
    reset may retire an ordinary occurrence carrying an immutable work-resource
    reference without deleting that resource.
    Apply retirement by retired_token_refs, never by blanket-clearing reset_places
    on the projected successor: ordinary returns and newly produced tokens are
    not predecessor retirement candidates.
    ``read_resource`` is called only for exact registered effect-bound refs.
    """
    if (not isinstance(executable, ExecutableNetAuthority)
            or not isinstance(structure, RuntimeNet)
            or not isinstance(predecessor, TypedMarkingAuthority)
            or not isinstance(outputs, RegisteredOperationOutputsAuthority)
            or not isinstance(registration, Registration)
            or not isinstance(active_firings, tuple) or not callable(read_resource)
            or not callable(read_resource_media_type)):
        raise TypeError("declared effect preparation requires exact typed Core inputs")
    operation = outputs.execution.operation
    firing = operation.firing
    if (executable.net_ref != structure.registry_net_ref
            or predecessor.net_ref != executable.net_ref
            or firing.net_ref != executable.net_ref
            or predecessor.team_design_root_ref != executable.team_design_root_ref):
        raise ResourceIntegrityFault("effect preparation differs from exact net closure")
    transition = next((t for t in executable.transitions if t.transition_id == firing.transition_id), None)
    if transition != operation.transition:
        raise ResourceIntegrityFault("effect firing differs from registered transition")
    compiled = structure.compiled
    symbolic_transition = next(t for t in compiled.symbolic.transitions if t.name == firing.transition_id)
    declared = next(o for o in compiled.operations if o.declaration.name == symbolic_transition.operation)
    if (declared.operation_id != operation.spec.operation_id
            or declared.executor_key != operation.spec.executor_key):
        raise ResourceIntegrityFault("effect operation differs from compiled registered identity")
    selected = next((o for o in declared.declaration.outcomes if o.name == outputs.selected_outcome_id), None)
    if selected is None:
        raise ResourceIntegrityFault("effect preparation requires an exact registered outcome")
    ports = {p.name: p for p in compiled.ports}
    by_id = {ports[name].port_id: name for name in declared.declaration.outputs}
    grouped = {name: [] for name in declared.declaration.outputs}
    output_bindings = {b.port_id: b for b in operation.operation_binding.output_port_bindings}
    output_specs = {p.port_id: p for p in operation.spec.output_ports}
    for output in outputs.outputs:
        name = by_id.get(output.port_id)
        binding = output_bindings.get(output.port_id)
        if (name is None or binding is None or output.place != ports[name].place
                or output.place != binding.place or output.place_ref != binding.place_ref
                or output.output_binding_ref != binding.output_binding_ref
                or output.schema_ref != output_specs[output.port_id].schema_ref
                or output.artifact.header.content_schema_ref != ports[name].schema
                or output.artifact.header.task_ref != operation.canonical.context.task_ref):
            raise ResourceIntegrityFault("effect input is outside exact WHOLE output bundle")
        grouped[name].append(output)
    quantities = {p.port: p for p in selected.products}
    for name, values in grouped.items():
        quantity = quantities.get(name)
        if ((quantity is None and values) or (quantity is not None
                and not quantity.minimum <= len(values) <= quantity.maximum)):
            raise ResourceIntegrityFault("effect input differs from selected product quantity")
    refs = tuple(o.resource_ref for o in outputs.outputs)
    if len(refs) != len(set(refs)):
        raise ResourceIntegrityFault("effect input repeats registered product resources")
    if not selected.effects:
        return PreparedModuleEffects(predecessor.checkpoint_ref, executable.net_ref,
            firing.transition_firing_ref, selected.name, (), (), ())
    tokens = {t.token_ref: t.state for t in predecessor.tokens}
    if (set(tokens) != set(predecessor.token_refs) or len(tokens) != len(predecessor.tokens)
            or any(state.token_ref != ref for ref, state in tokens.items())
            or not set(firing.claimed_input_refs) <= set(tokens)):
        raise ResourceIntegrityFault("effect lacks exact predecessor token/claim membership")
    claims = set()
    for active in active_firings:
        if not isinstance(active, TransitionFiringAuthority) or active.net_ref != executable.net_ref:
            raise ResourceIntegrityFault("effect requires exact same-net active firing authorities")
        if active.transition_firing_ref != firing.transition_firing_ref:
            claims.update(active.claimed_input_refs)
    view = structure.for_outcome(firing.transition_id, selected.name).for_claimed_inputs(
        firing.transition_id, tuple((tokens[r].place, tokens[r].verdict) for r in firing.claimed_input_refs))
    places = {p.name: p for p in compiled.symbolic.places}
    consumed_places = {a.place for a in view._arcs if a.transition == firing.transition_id
                       and a.direction == "input" and a.mode in {"consume", "borrow", "read"}}
    consumed = {r for r in firing.claimed_input_refs if tokens[r].place in consumed_places}
    routes, resets, selected_outputs, witnesses, revisions = [], set(), [], [], []
    for effect_index, effect in enumerate(selected.effects):
        host = registration.declaration("tool", effect.key)
        if host != compiled.registrations.get("tool", {}).get(effect.key):
            raise ResourceIntegrityFault("effect tool differs from exact registered HOST contract")
        structural_revision = (
            host["contracts"].get("effect_kind") == "structural_revision")
        formal_action_references = {
            handle for handle, reference in effect.references.items()
            if isinstance(reference, SymbolReference)
            and reference.kind == "place"
            and reference.name in places
            and (view.output_emit_for(
                    firing.transition_id, reference.name) == "route_selected"
                 or view.reset_place_for_selector(
                    firing.transition_id, handle) == reference.name
                 or view.selected_output_place_for_selector(
                    firing.transition_id, handle) == reference.name)
        }
        if structural_revision and formal_action_references:
            raise ResourceIntegrityFault(
                "structural revision cannot also select Petri action arcs")
        if not structural_revision and not formal_action_references:
            raise ResourceIntegrityFault(
                "selected effect lacks formal Petri action arcs")
        validate_registered_config(registration, "tool", effect.key, effect.config,
                                   compiled.source.required_schemas)
        resources = {}
        for handle, name in effect.bindings.items():
            port = ports[name]
            values = []
            if name in grouped:
                sources = [(o.resource_ref, None) for o in grouped[name]]
            else:
                sources = [(i.resource_ref, tokens.get(i.claimed_token_ref)) for i in operation.inputs
                           if i.port_id == port.port_id]
                if any(token is None or token.token_ref not in firing.claimed_input_refs
                       for _, token in sources):
                    raise ResourceIntegrityFault("effect resource lacks exact input claim membership")
            for ref, token in sources:
                payload = read_resource(ref)
                if not isinstance(payload, bytes):
                    raise TypeError("effect resource reader must return immutable bytes")
                instance = _content_schema_instance(payload, media_type=read_resource_media_type(ref))
                if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
                    Draft7Validator(registration.declaration("schema", port.schema)["schema"]).validate(instance)
                values.append(BoundEffectResource(ref, port.schema, payload, token))
            resources[handle] = tuple(values)
        context = DeclaredEffectContext(predecessor.checkpoint_ref, executable.net_ref,
            firing.transition_firing_ref, selected.name, _readonly(effect.config),
            _readonly(effect.references), _readonly(resources), tuple(tokens.values()))
        instruction = registration.resolve("tool", effect.key)(context)
        if structural_revision:
            from .module_revision import DeclaredModuleRevision
            if not isinstance(instruction, DeclaredModuleRevision):
                raise ResourceIntegrityFault("structural effect requires a typed full Module instruction")
            if revisions:
                raise ResourceIntegrityFault("one Success cannot adopt multiple full Modules")
            revisions.append((instruction, context))
            witnesses.append({"effect_index": effect_index, "key": effect.key,
                "effect_kind": "structural_revision",
                "bound_resources": [{"binding": handle, "ordinal": ordinal,
                    "resource_ref": _ref_payload(value.resource_ref.as_version_ref()),
                    "claimed_token_ref": None if value.token is None else _ref_payload(value.token.token_ref)}
                    for handle, values in resources.items() for ordinal, value in enumerate(values)],
                "reset_references": [], "routes": [], "outputs": [],
                "retired_token_refs": [],
                "module_revision": instruction.to_dict()})
            continue
        if (not isinstance(instruction, DeclaredEffectInstruction)
                or not isinstance(instruction.routes, tuple)
                or not isinstance(instruction.reset_references, tuple)
                or not isinstance(instruction.output_references, tuple)):
            raise ResourceIntegrityFault("HOST effect must return a typed pure data instruction")
        if (len(set(instruction.reset_references))
                != len(instruction.reset_references)
                or len(set(instruction.output_references))
                != len(instruction.output_references)):
            raise ResourceIntegrityFault(
                "HOST effect repeats one formal Petri action selector")
        witness = {"effect_index": effect_index, "key": effect.key,
            "effect_kind": DECLARED_PETRI_ACTION,
            "bound_resources": [{"binding": handle, "ordinal": ordinal,
                "resource_ref": _ref_payload(value.resource_ref.as_version_ref()),
                "claimed_token_ref": None if value.token is None else _ref_payload(value.token.token_ref)}
                for handle, values in resources.items() for ordinal, value in enumerate(values)],
            "reset_references": list(instruction.reset_references),
            "routes": [], "outputs": [], "retired_token_refs": []}
        witnesses.append(witness)

        def affected(handle):
            reference = effect.references.get(handle)
            if not isinstance(reference, SymbolReference) or reference.kind != "place" or reference.name not in places:
                raise ResourceIntegrityFault("effect addresses an undeclared symbolic place reference")
            return reference.name

        for handle in instruction.reset_references:
            place = affected(handle)
            declared_place = view.reset_place_for_selector(
                firing.transition_id, handle)
            if declared_place != place:
                raise ResourceIntegrityFault(
                    "effect reset is outside exact selected Petri reset arcs")
            if places[place].reusable or places[place].token_kind in {"agent_resource", "resource_lease"}:
                raise ResourceIntegrityFault("reset cannot target reusable capacity/lease authority")
            resets.add(place)
        for route in instruction.routes:
            if not isinstance(route, DeclaredRouteSelection):
                raise ResourceIntegrityFault("effect route must be a typed symbolic selection")
            place = affected(route.reference)
            if view.output_emit_for(firing.transition_id, place) != "route_selected":
                raise ResourceIntegrityFault("effect route is outside exact selected route output arcs")
            bundle = resources.get(route.binding)
            if (bundle is None or type(route.ordinal) is not int or not 0 <= route.ordinal < len(bundle)):
                raise ResourceIntegrityFault("effect route fabricates a bound resource ordinal")
            source = bundle[route.ordinal]
            port_name = effect.bindings[route.binding]
            port = ports[port_name]
            target = places[place]
            if (target.reusable or target.token_kind in {"agent_resource", "resource_lease"}
                    or source.schema_id not in target.admitted_schemas or port.channel != target.channel):
                raise ResourceIntegrityFault("effect route cannot change resource type/capacity authority")
            source_output = next((o for o in grouped.get(port_name, ()) if o.resource_ref == source.resource_ref), None)
            state = source.token
            if ((state is not None and (places[state.place].reusable or state.lease_identity_ref is not None or state.lease_claims))
                    or (source_output is not None and source_output.lease_claims)):
                raise ResourceIntegrityFault("effect route cannot duplicate unresolved lease claims")
            # route_selected carries the exact resource onto a declared lane;
            # unlike a forward arc it does not copy a source verdict stamp.
            # Colour comes solely from this output's PN inscription.
            colour = view.project_output_color(firing.transition_id, place,
                tuple((tokens[r].place, tokens[r].verdict) for r in firing.claimed_input_refs))
            routes.append(PreparedDeclaredRoute(place, source, port_name, colour))
            witness["routes"].append({"reference": route.reference,
                "binding": route.binding, "ordinal": route.ordinal})
        claimed_colours = tuple(
            (tokens[ref].place, tokens[ref].verdict)
            for ref in firing.claimed_input_refs)
        for handle in instruction.output_references:
            place = affected(handle)
            declared_place = view.selected_output_place_for_selector(
                firing.transition_id, handle)
            if declared_place != place:
                raise ResourceIntegrityFault(
                    "effect output is outside exact selected Petri output arcs")
            colour = view.project_output_color(
                firing.transition_id, place, claimed_colours)
            selected_outputs.append(
                PreparedSelectedOutput(
                    firing.transition_id,
                    place,
                    handle,
                    colour,
                    view.output_arc_weight(firing.transition_id, place),
                ))
            witness["outputs"].append({"reference": handle})
    route_counts = Counter(r.place for r in routes)
    if any(count > view.output_arc_weight(firing.transition_id, place) for place, count in route_counts.items()):
        raise ResourceIntegrityFault("effect route quantity exceeds selected PN arc weight")
    terminal_ports = {f"{t.source.component}.{t.source.port}" for t in
                      (compiled.source.terminal, *compiled.source.terminal_alternatives)}
    terminal_places = {ports[name].place for name in terminal_ports}
    retired = []
    for ref, state in tokens.items():
        if state.place not in resets or ref in consumed:
            continue
        place = places[state.place]
        reason = _formal_reset_retirement_reason(
            state,
            place,
            claimed_elsewhere=ref in claims,
            terminal=state.place in terminal_places,
        )
        if reason is not None:
            raise ResourceIntegrityFault(
                "reset cannot discard unresolved authority: " + reason)
        retired.append(ref)
    for effect, witness in zip(selected.effects, witnesses, strict=True):
        effect_resets = {effect.references[handle].name for handle in witness["reset_references"]}
        witness["retired_token_refs"] = [_ref_payload(ref) for ref in sorted(retired,
            key=lambda r: (r.entity_type, str(r.entity_id), str(r.version_id)))
            if tokens[ref].place in effect_resets]
    selected_output_places = Counter(
        output.place for output in selected_outputs)
    if any(count != 1 for count in selected_output_places.values()):
        raise ResourceIntegrityFault(
            "effect selects one Petri output place more than once")
    occupied = Counter(state.place for ref, state in tokens.items() if ref not in consumed and ref not in retired)
    for place in view.outputs_of(firing.transition_id):
        if view.output_emit_for(firing.transition_id, place) == "route_selected":
            occupied[place] += route_counts[place]
        elif view.output_effect_selector_for(
                firing.transition_id, place) is not None:
            if selected_output_places[place]:
                occupied[place] += view.output_arc_weight(
                    firing.transition_id, place)
        else:
            product_count = sum(len(values) for name, values in grouped.items() if ports[name].place == place)
            occupied[place] += (product_count if view.output_emit_for(firing.transition_id, place) is None
                                else view.output_arc_weight(firing.transition_id, place))
    if any(places[place].capacity is not None and count > places[place].capacity
           for place, count in occupied.items()):
        raise ResourceIntegrityFault("declared effect successor exceeds exact place capacity")
    return PreparedModuleEffects(predecessor.checkpoint_ref, executable.net_ref,
        firing.transition_firing_ref, selected.name, tuple(routes), tuple(sorted(resets)),
        tuple(sorted(retired, key=lambda r: (r.entity_type, str(r.entity_id), str(r.version_id)))),
        tuple(witnesses), tuple(revisions), tuple(selected_outputs))


__all__ = ("DECLARED_PETRI_ACTION", "BoundEffectResource", "DeclaredEffectContext",
           "DeclaredRouteSelection", "DeclaredEffectInstruction", "PreparedDeclaredRoute",
           "PreparedSelectedOutput", "PreparedModuleEffects", "prepare_module_effects")
