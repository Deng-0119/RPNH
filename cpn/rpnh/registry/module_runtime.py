"""Exact registered Module net/marking hydration, with no example imports."""
from __future__ import annotations

import json

from ..executable_net import load_compiled_net
from ..marking import validate_typed_marking_state
from ..runtime_net import RuntimeNet
from ._registry import _RegistryCore
from .event_store import (validate_registered_net_closure, verified_adoption_head,
                          verified_checkpoint_head)
from .publication import _version_from_payload, _resource_from_payload
from .resource_service import _ResourceServiceKernel
from .resources import (AttemptCounterAuthority, ExecutableNetAuthority, ExecutableTransitionAuthority,
    NativeLaunchRegisteredArtifact, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning,
    PetriTokenAuthority, PetriTokenState, TypedMarkingAuthority, VerifiedResourceArtifact)
from .strict_contracts import _registered, ref_payload


def hydrate_module_resource_plan(core, compiled, net_ref, net, root_ref, root, declaration_ref):
    """Check persisted mechanical bindings using the original ordered reads."""
    from ._module_resource_projection import _LegacyResourceReads, _resource_projection
    return _resource_projection(_LegacyResourceReads(core), compiled, net_ref, net, root_ref, root, declaration_ref)


def hydrate_module_runtime(core: _RegistryCore):
    """Read only actual current heads; addresses/JSON alone confer no authority."""
    kernel = _ResourceServiceKernel(core)
    head = kernel._head()
    net_ref = verified_adoption_head(core.event_store, core.catalog, core.task_id)
    net = validate_registered_net_closure(core.event_store, core.catalog, net_ref)
    declaration_ref = _resource_from_payload(net["team_net_declaration_resource_ref"])
    prepared = kernel._prepared(declaration_ref)
    payload = core.object_store.read_registered(prepared)
    compiled = load_compiled_net(json.loads(payload))
    root_ref = _version_from_payload(net["team_design_root_ref"])
    _, root = _registered(core, root_ref, "team_design_root/v1")
    if (root["task_ref"]["logical_id"] != str(core.task_id)
            or prepared.metadata["content_schema_ref"] != compiled.schema_version):
        raise ValueError("Module declaration differs from actual task/schema authority")
    transitions = []
    declared = {transition.name for transition in compiled.symbolic.transitions}
    for value in net["executable_transition_binding_refs"]:
        ref = _version_from_payload(value)
        _, data = _registered(core, ref, "executable_transition_binding/v1")
        if (data["transition_id"] not in declared
                or data["declaration_resource_ref"] != net["team_net_declaration_resource_ref"]
                or data["declaration_schema_ref"] != compiled.schema_version):
            raise ValueError("executable transition differs from exact compiled declaration")
        _, operation_binding = _registered(core, _version_from_payload(data["operation_binding_ref"]), "operation_binding/v1")
        _, operation_spec = _registered(core, _version_from_payload(operation_binding["operation_spec_ref"]), "operation_spec/v1")
        transitions.append(ExecutableTransitionAuthority(binding_ref=ref, transition_id=data["transition_id"],
            execution_kind=("agent" if data["agent_ref"] is not None
                            else operation_spec["implementation_contracts"]["transport"]),
            node_ref=_version_from_payload(data["node_ref"]),
            activation_ref=None if data["activation_ref"] is None else _version_from_payload(data["activation_ref"]),
            operation_binding_ref=_version_from_payload(data["operation_binding_ref"]),
            principal_ref=_version_from_payload(data["principal_ref"]),
            agent_ref=None if data["agent_ref"] is None else _version_from_payload(data["agent_ref"])))
    if len(transitions) != len(declared) or {item.transition_id for item in transitions} != declared:
        raise ValueError("Module runtime transition inventory differs from exact closure")
    executable = ExecutableNetAuthority(net_ref=net_ref, team_design_root_ref=root_ref,
        declaration=NativeLaunchRegisteredArtifact(payload, VerifiedResourceArtifact(
            kernel._header(declaration_ref, through_head=head), head)),
        declaration_resource_ref=declaration_ref, transitions=tuple(sorted(transitions, key=lambda t: t.transition_id)),
        verified_at_head=head)
    resource_plan = hydrate_module_resource_plan(core, compiled, net_ref, net,
        root_ref, root, declaration_ref)
    structure = RuntimeNet(compiled, net_ref=net_ref, resource_plan=resource_plan)
    checkpoint_ref = verified_checkpoint_head(core.event_store, core.catalog, core.task_id, net_ref)
    _, checkpoint = _registered(core, checkpoint_ref, "marking_checkpoint/v1")
    token_refs = tuple(_version_from_payload(value) for value in checkpoint["token_refs"])
    tokens = []
    for ref in token_refs:
        _, data = _registered(core, ref, "petri_token/v1")
        core.catalog.validate_instance("petri_token/v1", category="object", instance=data)
        if data["petri_token_ref"] != ref_payload(ref) or data["net_instance_ref"] != ref_payload(net_ref):
            raise ValueError("Module marking token differs from exact net/self authority")
        def resource(name):
            return None if data[name] is None else _resource_from_payload(data[name])
        warning = data["override_warning"]
        continuation = data["continuation"]
        state = PetriTokenState(token_ref=ref, token_id=data["token_id"], place=data["place"], epoch=data["epoch"],
            producer=data["producer"], consumer=data["consumer"], resource_ref=resource("resource_ref"),
            work_resource_ref=resource("work_resource_ref"), kind=data["kind"], consumed_by=data["consumed_by"],
            override_warning=None if warning is None else PetriOverrideWarning(**warning),
            verdict=data["verdict"], continuation=None if continuation is None else PetriContinuation(
                continuation["round"], _resource_from_payload(continuation["source_ref"])),
            lease_identity_ref=None if data["lease_identity_ref"] is None else _version_from_payload(data["lease_identity_ref"]),
            lease_claims=tuple(PetriLeaseClaim(_version_from_payload(claim["lease_identity_ref"]),
                None if claim["expected_resource_ref"] is None else _resource_from_payload(claim["expected_resource_ref"]),
                claim["access_mode"], claim.get("staging_place")) for claim in data["lease_claims"]))
        for resource_ref in (state.resource_ref, state.work_resource_ref):
            if resource_ref is not None:
                kernel._prepared(resource_ref)
        tokens.append(PetriTokenAuthority(ref, state, head))
    attempts = tuple(AttemptCounterAuthority(item["transition_id"], item["highest_issued"])
                     for item in checkpoint["attempts"])
    states = tuple(sorted((token.state for token in tokens), key=lambda state: state.token_id))
    validate_typed_marking_state(structure, epoch=checkpoint["epoch"], next_token_id=checkpoint["next_token_id"],
        attempts=attempts, tokens=states, require_token_refs=True)
    occupied = {}
    capacities = {place.name: place.capacity for place in compiled.symbolic.places}
    for state in states:
        if state.epoch == checkpoint["epoch"] and state.consumed_by is None:
            occupied[state.place] = occupied.get(state.place, 0) + 1
    if any(capacities[place] is not None and count > capacities[place] for place, count in occupied.items()):
        raise ValueError("actual Module marking exceeds declared place capacity")
    marking = TypedMarkingAuthority(checkpoint_ref, net_ref, root_ref, checkpoint["epoch"],
        checkpoint["next_token_id"], attempts, token_refs, tuple(sorted(tokens, key=lambda token: token.state.token_id)),
        None if checkpoint["previous_checkpoint_ref"] is None else _version_from_payload(checkpoint["previous_checkpoint_ref"]),
        None if checkpoint["settlement_delta_ref"] is None else _version_from_payload(checkpoint["settlement_delta_ref"]),
        tuple(_version_from_payload(value) for value in checkpoint["transition_firing_refs"]), head)
    return executable, structure, marking


__all__ = ("hydrate_module_runtime", "hydrate_module_resource_plan")


# Deliberately not ExecutableNetAuthority: historical material confers no
# admission, scheduling, or current-head authority.
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _TerminalNetMaterial:
    net_ref: object
    team_design_root_ref: object
    declaration_resource_ref: object
    transitions: tuple


def _read_terminal_net_material(core, reads, net_ref):
    """Read immutable per-net material through the fixed same-Registry reader."""
    from ._event_store.adoption_reads import AdoptionPrefixReads
    from ._event_store.net_lineage import validate_registered_net_closure
    from .errors import ResourceIntegrityFault
    if (type(reads) is not AdoptionPrefixReads or not reads.terminal
            or reads.store is not core.event_store or reads.catalog is not core.catalog
            or reads.task_id != core.task_id):
        raise TypeError("historical terminal material requires its fixed Registry read")
    net = validate_registered_net_closure(core.event_store, core.catalog, net_ref,
        _db=reads.db, _memo={}, _prefix_reads=reads)
    declaration_ref = _resource_from_payload(net["team_net_declaration_resource_ref"])
    compiled = reads.compiled(declaration_ref.as_version_ref())
    prepared = reads.prepared(declaration_ref.as_version_ref(), "resource_version/v1")
    root_ref = _version_from_payload(net["team_design_root_ref"])
    root = reads.metadata(root_ref, "team_design_root/v1")
    if (root["task_ref"]["logical_id"] != str(core.task_id)
            or prepared.metadata["task_ref"] != root["task_ref"]
            or prepared.metadata["content_schema_ref"] != compiled.schema_version):
        raise ResourceIntegrityFault("historical Module declaration differs from exact task/schema")
    transitions = []
    declared = {item.name for item in compiled.symbolic.transitions}
    for value in net["executable_transition_binding_refs"]:
        ref = _version_from_payload(value)
        data = reads.metadata(ref, "executable_transition_binding/v1")
        if (data["transition_id"] not in declared
                or data["declaration_resource_ref"] != net["team_net_declaration_resource_ref"]
                or data["declaration_schema_ref"] != compiled.schema_version):
            raise ResourceIntegrityFault("historical transition differs from compiled declaration")
        binding = reads.metadata(_version_from_payload(data["operation_binding_ref"]), "operation_binding/v1")
        spec = reads.metadata(_version_from_payload(binding["operation_spec_ref"]), "operation_spec/v1")
        transitions.append(ExecutableTransitionAuthority(binding_ref=ref, transition_id=data["transition_id"],
            execution_kind=("agent" if data["agent_ref"] is not None else spec["implementation_contracts"]["transport"]),
            node_ref=_version_from_payload(data["node_ref"]),
            activation_ref=None if data["activation_ref"] is None else _version_from_payload(data["activation_ref"]),
            operation_binding_ref=_version_from_payload(data["operation_binding_ref"]),
            principal_ref=_version_from_payload(data["principal_ref"]),
            agent_ref=None if data["agent_ref"] is None else _version_from_payload(data["agent_ref"])))
    if len(transitions) != len(declared) or {item.transition_id for item in transitions} != declared:
        raise ResourceIntegrityFault("historical Module transition inventory differs from exact closure")
    plan = reads.resource_plan(compiled, net_ref, net, root_ref, root, declaration_ref)
    structure = RuntimeNet(compiled, net_ref=net_ref, resource_plan=plan)
    return _TerminalNetMaterial(net_ref, root_ref, declaration_ref,
        tuple(sorted(transitions, key=lambda item: item.transition_id))), structure
