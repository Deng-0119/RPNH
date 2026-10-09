"""Whole-graph owner transfer from a drained Registry marking, never fresh M0."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Mapping

from ..executable_net import CompiledPetriNet, load_compiled_net
from ..marking import MarkingStateError, validate_typed_marking_state
from ..runtime_net import RuntimeNet
from .event_store import RegistryConflict, verified_adoption_head, verified_checkpoint_head, validate_registered_net_closure
from .identities import new_id
from .models import TypedRelation, VersionRef
from .module_runtime import hydrate_module_resource_plan
from .owner_adoption import OwnerTokenMapping, OrdinaryRetirement, OwnerInputMapping, _terminal_source_places
from .publication import _ref_payload, _resource_from_payload, _version_from_payload
from .resources import PetriTokenState, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning, ResourceVersionRef, TypedMarkingAuthority
from .schema_catalog import canonical_json
from .strict_contracts import _registered


def _need(token_ref, place, reason):
    return {"token_ref": None if token_ref is None else _ref_payload(token_ref), "place": place, "reason": reason}


@dataclass(frozen=True, slots=True)
class OwnerMappingDecision:
    status: str
    needs: tuple[Mapping, ...]
    checkpoint_ref: VersionRef
    candidate_compiled: CompiledPetriNet
    destinations: tuple[tuple[VersionRef, str], ...]
    retire_token_refs: tuple[VersionRef, ...]
    owner_inputs: tuple[tuple[str, ResourceVersionRef, VersionRef], ...]


@dataclass(frozen=True, slots=True)
class OwnerMappingAllocation:
    token_refs: tuple[VersionRef, ...]
    token_mappings: tuple[OwnerTokenMapping, ...]
    ordinary_retirements: tuple[OrdinaryRetirement, ...]
    owner_input_mappings: tuple[OwnerInputMapping, ...]
    attempts: tuple[Mapping, ...]
    next_token_id: int


@dataclass(frozen=True, slots=True)
class OwnerMappingPreview:
    source_marking: TypedMarkingAuthority
    decision: OwnerMappingDecision
    structure: RuntimeNet
    tokens: tuple[PetriTokenState, ...]
    allocation: OwnerMappingAllocation
    prepared: tuple


def token_state(ref, token):
    """The current exact token object -> shared marking DTO conversion."""
    resource = lambda v: None if v is None else _resource_from_payload(v)
    return PetriTokenState(ref, token["token_id"], token["place"], token["epoch"],
        token["producer"], token["consumer"], resource(token["resource_ref"]),
        resource(token["work_resource_ref"]), token["kind"], token["consumed_by"],
        None if token["override_warning"] is None else PetriOverrideWarning(**token["override_warning"]),
        token["verdict"], None if token["continuation"] is None else PetriContinuation(
            token["continuation"]["round"], resource(token["continuation"]["source_ref"])),
        None if token["lease_identity_ref"] is None else _version_from_payload(token["lease_identity_ref"]),
        tuple(PetriLeaseClaim(_version_from_payload(c["lease_identity_ref"]),
            resource(c["expected_resource_ref"]), c["access_mode"], c.get("staging_place"))
            for c in token["lease_claims"]))


def _latest(core, marking):
    if not isinstance(marking, TypedMarkingAuthority):
        raise TypeError("latest_marking must be current TypedMarkingAuthority")
    if (verified_adoption_head(core.event_store, core.catalog, core.task_id) != marking.net_ref
            or verified_checkpoint_head(core.event_store, core.catalog, core.task_id, marking.net_ref) != marking.checkpoint_ref):
        raise RegistryConflict("owner mapping requires latest active drained marking")
    with core.event_store.connect() as db:
        if db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' LIMIT 1").fetchone():
            raise RegistryConflict("owner mapping requires drained firings")
    _, checkpoint = _registered(core, marking.checkpoint_ref, "marking_checkpoint/v1")
    if (checkpoint["epoch"] != marking.epoch or checkpoint["next_token_id"] != marking.next_token_id
            or set(marking.token_refs) != {a.token_ref for a in marking.tokens}
            or checkpoint["token_refs"] != sorted((_ref_payload(r) for r in marking.token_refs), key=canonical_json)
            or checkpoint["attempts"] != [{"transition_id": a.transition_id, "highest_issued": a.highest_issued} for a in marking.attempts]):
        raise RegistryConflict("owner mapping DTO differs from registered checkpoint")
    for authority in marking.tokens:
        _, token = _registered(core, authority.token_ref, "petri_token/v1")
        if (token["net_instance_ref"] != _ref_payload(marking.net_ref)
                or token["petri_token_ref"] != _ref_payload(authority.token_ref)
                or token_state(authority.token_ref, token) != authority.state):
            raise RegistryConflict("owner mapping DTO differs from registered token")


def ordinary_retirement_reason(state, place, terminal_places):
    if place.reusable or place.token_kind in {"agent_resource", "resource_lease"}:
        return "reusable/capacity/lease authority cannot retire"
    if state.place in terminal_places:
        return "primary/alternative terminal token cannot retire"
    if (state.lease_identity_ref is not None or state.lease_claims or state.work_resource_ref is not None
            or state.kind is not None or state.continuation is not None or state.override_warning is not None):
        return "unresolved lease/work/continuation authority cannot retire"
    return None


def registered_owner_input(core, ref, *, task_ref, root=None, source_ref=None):
    """Only normal, non-firing owner input publication grants new occupancy."""
    if not isinstance(ref, ResourceVersionRef):
        raise TypeError("owner inputs require registered ResourceVersionRef")
    _, metadata = _registered(core, ref.as_version_ref(), "resource_version/v1")
    source = _version_from_payload(metadata["origin"]["primary_ref"])
    _registered(core, source, "bootstrap_command/v1")
    if (metadata["resource_id"] != str(ref.resource_id)
            or metadata["resource_version_id"] != str(ref.resource_version_id)
            or metadata["task_ref"] != task_ref or metadata["origin_kind"] != "private_system"
            or metadata["origin"]["kind"] != "private_system"
            or metadata["producer_ref"] != _ref_payload(source)
            or metadata["origin"]["secondary_ref"] != _ref_payload(source)
            or metadata["reference_provenance"]["producer_invocation_ref"] is not None
            or metadata["reference_provenance"]["operation_binding_ref"] is not None
            or (source_ref is not None and source != source_ref)
            or (root is not None and _ref_payload(ref.as_version_ref()) not in root["resource_refs"])):
        raise RegistryConflict("owner input lacks normal source/task/candidate-root authority")
    return source, metadata


def _place_type(place):
    # Fresh initial tokens and local capacity are not old occupancy authority.
    return place.channel, place.token_kind, place.reusable, frozenset(place.admitted_schemas), frozenset((type(c), c) for c in place.colours)


def transfer_consumer(structure, place):
    ignition = {p: t for p, t, _ in structure.synthetic_ignition_arcs}
    return ignition.get(place)


def plan_owner_mapping(core, old_compiled, candidate_compiled, latest_marking,
                       marking_mapping: dict[str, str], retire_token_refs=(), owner_inputs=None):
    """No writes: unchanged logical typed places preserve occupied tokens.

    Explicit decisions name token *version* IDs and candidate symbolic places.
    Missing/unrepresentable decisions are data, not partially published tokens.
    """
    _latest(core, latest_marking)
    net = validate_registered_net_closure(core.event_store, core.catalog, latest_marking.net_ref)
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    actual = load_compiled_net(json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id))))
    if old_compiled != actual:
        raise RegistryConflict("old compiled declaration differs from latest Registry net")
    _, root = _registered(core, latest_marking.team_design_root_ref, "team_design_root/v1")
    old_places = {p.name: p for p in old_compiled.symbolic.places}
    places = {p.name: p for p in candidate_compiled.symbolic.places}
    terminals = _terminal_source_places(old_compiled)
    candidate_terminals = _terminal_source_places(candidate_compiled)
    claim_places = {a.claim_token_place for a in candidate_compiled.symbolic.variable_resource_arcs}
    pool_places = {p.place for p in candidate_compiled.symbolic.lease_pools}
    transition_names = {t.name for t in candidate_compiled.symbolic.transitions}
    needs, destinations, inputs, retired = [], [], [], []
    states = {str(a.token_ref.version_id): a.state for a in latest_marking.tokens}
    retire = set(retire_token_refs)
    if len(retire) != len(retire_token_refs) or any(r not in latest_marking.token_refs for r in retire):
        raise RegistryConflict("retirement decision repeats or is outside latest marking")
    if set(marking_mapping) - set(states):
        raise RegistryConflict("token destination decision is outside latest marking")
    for a in latest_marking.tokens:
        state, ref = a.state, a.token_ref
        old_place = old_places[state.place]
        destination = marking_mapping.get(str(ref.version_id))
        if state.epoch != latest_marking.epoch or state.consumed_by is not None:
            needs.append(_need(ref, state.place, "old occurrence is not current drained authority")); continue
        if ref in retire:
            reason = ordinary_retirement_reason(state, old_place, terminals)
            if destination is not None:
                reason = "token has both destination and retirement decisions"
            if reason:
                needs.append(_need(ref, state.place, reason))
            else:
                retired.append(ref)
            continue
        if destination is None:
            if state.place in places and _place_type(old_place) == _place_type(places[state.place]):
                destination = state.place
            else:
                needs.append(_need(ref, state.place, "occupied removed/type-changed place needs destination or ordinary retirement")); continue
        if destination not in places:
            needs.append(_need(ref, state.place, "destination place is absent")); continue
        target = places[destination]
        if (target.token_kind != old_place.token_kind or target.reusable != old_place.reusable
                or (target.colours and (type(state.verdict), state.verdict) not in {(type(c), c) for c in target.colours})):
            needs.append(_need(ref, destination, "destination cannot preserve exact token type/colour/reusability")); continue
        if state.resource_ref is not None:
            _, metadata = _registered(core, state.resource_ref.as_version_ref(), "resource_version/v1")
            if metadata["task_ref"] != root["task_ref"] or metadata["content_schema_ref"] not in target.admitted_schemas:
                needs.append(_need(ref, destination, "destination rejects exact source Resource schema/task")); continue
        if (state.lease_identity_ref is not None) != (destination in pool_places):
            needs.append(_need(ref, destination, "destination cannot represent exact lease-pool identity")); continue
        if state.lease_claims and destination not in claim_places and not all(c.staging_place == destination for c in state.lease_claims):
            needs.append(_need(ref, destination, "owner transfer cannot preserve claim carrier without a declared claim-token or exact staging place")); continue
        warning = state.override_warning
        if warning is not None and (warning.gate_output_place != destination
                or warning.gate_transition_id not in transition_names
                or warning.gate_peer_transition_id not in transition_names):
            needs.append(_need(ref, destination, "destination cannot preserve exact override gate/place authority")); continue
        identities = (state.lease_identity_ref, *(c.lease_identity_ref for c in state.lease_claims))
        if any(r is not None and r.entity_type == "logical_artifact_slot/v1" for r in identities):
            from .module_nets import select_preserved_slot_refs
            compatible = select_preserved_slot_refs(core, old_compiled, candidate_compiled)
            if any(r is not None and r.entity_type == "logical_artifact_slot/v1"
                   and r not in compatible.values() for r in identities):
                needs.append(_need(ref, destination, "removed/contract-changed logical slot cannot preserve old exact lease identity")); continue
        destinations.append((ref, destination))
    for place, refs in (owner_inputs or {}).items():
        if place not in places:
            needs.append(_need(None, place, "owner input destination is absent")); continue
        if place in candidate_terminals or places[place].token_kind != "data" or places[place].colours:
            needs.append(_need(None, place, "owner input cannot fabricate terminal/lease/work/coloured authority")); continue
        for ref in refs:
            source, metadata = registered_owner_input(core, ref, task_ref=root["task_ref"])
            if metadata["content_schema_ref"] not in places[place].admitted_schemas:
                needs.append(_need(None, place, "owner input schema is outside destination variants"))
            else:
                inputs.append((place, ref, source))
    for place in places.values():
        count = sum(p == place.name for _, p in destinations) + sum(p == place.name for p, _, _ in inputs)
        if place.capacity is not None and count > place.capacity:
            needs.append(_need(None, place.name, "mapped occupancy exceeds candidate place capacity"))
    return OwnerMappingDecision("NEEDS_MARKING_DECISION" if needs else "READY", tuple(needs),
        latest_marking.checkpoint_ref, candidate_compiled, tuple(destinations), tuple(retired), tuple(inputs))


def preview_owner_mapping(core, candidate_publication, latest_marking, decision, *, _token_refs=None):
    """Validate and propose exact candidate token IDs without registering them.

    Proposed addresses have no Registry authority. Allocation uses these same
    immutable states, so analysis never substitutes a count-only mapping.
    """
    _latest(core, latest_marking)
    if decision.status != "READY" or decision.checkpoint_ref != latest_marking.checkpoint_ref:
        raise RegistryConflict("NEEDS_MARKING_DECISION: no owner token writes permitted")
    candidate = candidate_publication.net_ref
    net = validate_registered_net_closure(core.event_store, core.catalog, candidate)
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id))))
    normalized_candidate = load_compiled_net(
        decision.candidate_compiled.to_dict())
    if compiled != normalized_candidate:
        raise RegistryConflict("candidate publication differs from mapping decision")
    root_ref = _version_from_payload(net["team_design_root_ref"])
    _, root = _registered(core, root_ref, "team_design_root/v1")
    plan = hydrate_module_resource_plan(core, compiled, candidate, net, root_ref, root, declaration)
    structure = RuntimeNet(compiled, net_ref=candidate, resource_plan=plan)
    states = {a.token_ref: a.state for a in latest_marking.tokens}
    tokens, mappings, input_mappings, publications = [], [], [], []
    next_id = latest_marking.next_token_id
    if _token_refs is not None and (len(_token_refs) != len(decision.destinations) + len(decision.owner_inputs)
            or len(set(_token_refs)) != len(_token_refs)
            or any(not isinstance(ref, VersionRef) or ref.entity_type != "petri_token/v1" for ref in _token_refs)):
        raise RegistryConflict("owner preview requires unique exact proposed token addresses")
    proposed_refs = iter(_token_refs) if _token_refs is not None else None
    def add(state, parents):
        nonlocal next_id
        ref = (next(proposed_refs) if proposed_refs is not None else
            VersionRef("petri_token/v1", new_id("petri_token"), new_id("petri_token_version")))
        target = replace(state, token_ref=ref, token_id=next_id, epoch=latest_marking.epoch,
            producer=None, consumer=transfer_consumer(structure, state.place), consumed_by=None)
        next_id += 1
        tokens.append(target)
        publications.append((ref, target, parents))
        return ref
    for source, destination in decision.destinations:
        ref = add(replace(states[source], place=destination), (source,))
        mappings.append(OwnerTokenMapping(source, ref))
    for place, resource, source in decision.owner_inputs:
        registered_owner_input(core, resource, task_ref=root["task_ref"], root=root, source_ref=source)
        state = PetriTokenState(None, 0, place, latest_marking.epoch, None, None, resource,
            None, None, None, None, None, None)
        ref = add(state, (resource.as_version_ref(), source))
        input_mappings.append(OwnerInputMapping(resource, source, place, ref))
    attempts = tuple(a for a in latest_marking.attempts if a.transition_id in structure.transitions)
    try:
        validate_typed_marking_state(structure, epoch=latest_marking.epoch, next_token_id=next_id,
            attempts=attempts, tokens=tuple(tokens), require_token_refs=True)
    except MarkingStateError as exc:
        raise RegistryConflict(f"NEEDS_MARKING_DECISION: candidate cannot preserve exact marking: {exc}") from exc
    # Schema and capacity gate also run before prewrite, not after partial writes.
    prepared = []
    for ref, state, parents in publications:
        _, old = _registered(core, parents[0], parents[0].entity_type)
        if parents[0].entity_type == "petri_token/v1":
            token = dict(old)
        else:
            token = {"resource_ref": {"resource_id": str(state.resource_ref.resource_id),
                "resource_version_id": str(state.resource_ref.resource_version_id)}, "work_resource_ref": None,
                "kind": None, "verdict": None, "continuation": None, "lease_identity_ref": None,
                "lease_claims": [], "override_warning": None}
        token.update(petri_token_ref=_ref_payload(ref), net_instance_ref=_ref_payload(candidate),
            token_id=state.token_id, place=state.place, epoch=state.epoch, producer=None,
            consumer=state.consumer, consumed_by=None)
        core.catalog.validate_instance("petri_token/v1", category="object", instance=token)
        prepared.append((ref, token, parents))
    allocation = OwnerMappingAllocation(tuple(t.token_ref for t in tokens), tuple(mappings),
        tuple(OrdinaryRetirement(r) for r in decision.retire_token_refs), tuple(input_mappings),
        tuple({"transition_id": a.transition_id, "highest_issued": a.highest_issued} for a in attempts), next_id)
    return OwnerMappingPreview(latest_marking, decision, structure, tuple(tokens),
        allocation, tuple((ref, canonical_json(token), parents) for ref, token, parents in prepared))


def allocate_owner_mapping(core, candidate_publication, latest_marking, decision, command_id,
                           *, preview=None, transaction=None):
    """Register a reviewed preview, optionally in the adoption transaction.

    A failed adoption leaves no registered candidate tokens when its transaction
    is supplied. Existing standalone callers retain their ordinary commit path.
    """
    _latest(core, latest_marking)
    if preview is None:
        preview = preview_owner_mapping(core, candidate_publication, latest_marking, decision)
    if (not isinstance(preview, OwnerMappingPreview) or preview.source_marking != latest_marking
            or preview.decision != decision or decision.status != "READY"
            or preview.structure.registry_net_ref != candidate_publication.net_ref):
        raise RegistryConflict("owner mapping preview is stale or belongs to different inputs")
    verified = preview_owner_mapping(core, candidate_publication, latest_marking, decision,
        _token_refs=preview.allocation.token_refs)
    if (verified.tokens != preview.tokens or verified.allocation != preview.allocation
            or verified.prepared != preview.prepared
            or verified.structure.compiled != preview.structure.compiled
            or verified.structure._resource_plan != preview.structure._resource_plan):
        raise RegistryConflict("owner preview differs from the exact current transformation")
    tx = transaction or core.begin(idempotency_key=command_id + ":owner-mapping")
    if (tx.event_store is not core.event_store or tx.task_id != core.task_id
            or tx.net_instance_id is not None or tx.task_round_id is not None
            or tx.idempotency_key != (command_id if transaction is not None else command_id + ":owner-mapping")):
        raise TypeError("owner mapping transaction belongs to another Registry or command")
    for ref, payload, parents in preview.prepared:
        token = json.loads(payload)
        tx.prewrite(object_type="petri_token/v1", logical_id=ref.entity_id, version_id=ref.version_id,
            payload=payload, metadata=token, media_type="application/json",
            schema_ref="registry_v1/petri_token/v1")
        for parent in parents:
            tx.relate(TypedRelation(new_id("relation"), "derived_from", ref, parent,
                metadata={"owner_mapping": True}, system_owned=True))
    if transaction is None:
        tx.commit()
    return preview.allocation


__all__ = ("OwnerMappingDecision", "OwnerMappingAllocation", "OwnerMappingPreview",
           "plan_owner_mapping", "preview_owner_mapping", "allocate_owner_mapping")
