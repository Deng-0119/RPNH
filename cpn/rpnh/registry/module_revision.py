"""Registered full-Module operation effects, inside ordinary Petri Success.

The HOST returns symbolic data. Core selects a normally registered candidate,
maps the ordinary PN successor, and rechecks the complete bridge in the writer
transaction. No owner command, virtual registered marking, or callable replay.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import json
from typing import Mapping

from jsonschema import Draft7Validator

from ..compiler import compile_module
from ..executable_net import load_compiled_net
from ..module import ModuleDeclaration
from ..marking import TeamNetMarking, validate_typed_marking_state
from ..petri_primitives import derive_petri_structure_delta, verify_petri_structure_delta
from ..runtime_net import RuntimeNet
from .errors import ResourceIntegrityFault
from .identities import new_id, TypedId
from .models import PendingEvent, TypedRelation, VersionRef
from .owner_adoption import _terminal_source_places
from .owner_mapping import _place_type, ordinary_retirement_reason, transfer_consumer, token_state
from .publication import (_ref_payload, _resource_from_payload, _version_from_payload,
                          _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE)
from .resources import ExecutableNetAuthority, TypedMarkingAuthority, PetriTokenState, PetriTokenAuthority, ResourceVersionRef
from .schema_catalog import canonical_json
from .module_activation import (DeclaredModuleActivation, resolve_module_activations,
                                validate_activation_contract)


@dataclass(frozen=True, slots=True)
class DeclaredModuleRevision:
    """Source is a declared resource binding; destinations are symbolic places.

    Mapping keys are EffectDeclaration place-reference handles. Retirement
    names exact predecessor refs exposed by Core in DeclaredEffectContext.
    Neither a NET identity nor an import locator is authored by the HOST.
    """
    source_binding: str
    module: ModuleDeclaration
    destinations: Mapping[str, str] = field(default_factory=dict)
    retire_token_refs: tuple[VersionRef, ...] = ()
    source_ordinal: int = 0
    activations: tuple[DeclaredModuleActivation, ...] = ()

    def to_dict(self):
        if not isinstance(self.module, ModuleDeclaration):
            raise TypeError("revision must supply the full shared ModuleDeclaration")
        if not isinstance(self.activations, tuple) or any(not isinstance(a, DeclaredModuleActivation) for a in self.activations):
            raise TypeError("revision activations require a tuple of typed symbolic instructions")
        return {"source_binding": self.source_binding, "source_ordinal": self.source_ordinal,
            "module": self.module.to_dict(), "destinations": dict(self.destinations),
            "retire_token_refs": [_ref_payload(r) for r in self.retire_token_refs],
            "activations": [a.to_dict() for a in self.activations]}


@dataclass(frozen=True, slots=True)
class PreparedModuleRevision:
    executable: ExecutableNetAuthority
    projected: TypedMarkingAuthority
    new_tokens: tuple[tuple[VersionRef, PetriTokenState], ...]
    ordinary_projected: TypedMarkingAuthority
    ordinary_new_tokens: tuple[tuple[VersionRef, PetriTokenState], ...]
    witness: dict
    relations: tuple[TypedRelation, ...]
    activation_witnesses: tuple[dict, ...] = ()


REVISION_FIELDS = ("operation_revision",)


def _module_source_matches(module, raw):
    """Compare declaration data, not an LLM body's JSON serialization."""
    return module.to_dict() == json.loads(raw)


def _schema_valid_json(raw, schema):
    value = json.loads(raw)
    Draft7Validator(schema).validate(value)
    return value


def _schema_valid_activation_resource(raw, schema, media_type):
    instance = _content_schema_instance(raw, media_type=media_type)
    if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
        Draft7Validator(schema).validate(instance)


def _map_states(old, candidate, structure, states, references, instruction,
                *, prior_refs, resource_metadata, slot_refs):
    """Pure mapping guards shared by prepare and transaction verification."""
    places = {p.name: p for p in candidate.symbolic.places}
    old_places = {p.name: p for p in old.symbolic.places}
    terminals = _terminal_source_places(old)
    pool_places = {p.place for p in candidate.symbolic.lease_pools}
    claim_places = {a.claim_token_place for a in candidate.symbolic.variable_resource_arcs}
    transitions = {t.name for t in candidate.symbolic.transitions}
    retired = set(instruction["retire_token_refs"])
    if len(retired) != len(instruction["retire_token_refs"]) or not retired <= prior_refs:
        raise ResourceIntegrityFault("revision retirement is not exact predecessor occupancy")
    destinations = {}
    for handle, destination in instruction["destinations"].items():
        reference = references.get(handle)
        if reference is None or reference.kind != "place" or destination not in places:
            raise ResourceIntegrityFault("revision mapping is outside declared place references/candidate")
        if reference.name in destinations:
            raise ResourceIntegrityFault("revision repeats a source place decision")
        destinations[reference.name] = destination
    mapped, seen_retired = [], set()
    for state in states:
        source = old_places[state.place]
        if state.token_ref in retired:
            reason = ordinary_retirement_reason(state, source, terminals)
            declared_places = {r.name for r in references.values() if r.kind == "place"}
            if reason or state.consumed_by is not None or state.place in destinations or state.place not in declared_places:
                raise ResourceIntegrityFault("revision cannot retire unresolved/terminal/capacity or mapped authority")
            seen_retired.add(state.token_ref)
            continue
        destination = destinations.get(state.place)
        if destination is None:
            if state.place not in places or _place_type(source) != _place_type(places[state.place]):
                raise ResourceIntegrityFault("occupied removed/type-changed place needs explicit mapping/retirement")
            destination = state.place
        target = places[destination]
        if (source.channel != target.channel or source.token_kind != target.token_kind
                or source.reusable != target.reusable
                or target.colours and (type(state.verdict), state.verdict)
                   not in {(type(c), c) for c in target.colours}):
            raise ResourceIntegrityFault("revision destination cannot preserve exact typed token")
        if state.resource_ref is not None:
            metadata = resource_metadata(state.resource_ref)
            if metadata["content_schema_ref"] not in target.admitted_schemas:
                raise ResourceIntegrityFault("revision destination rejects exact Resource schema")
        if (state.lease_identity_ref is not None) != (destination in pool_places):
            raise ResourceIntegrityFault("revision loses lease-pool identity")
        if state.lease_claims and destination not in claim_places and not all(c.staging_place == destination for c in state.lease_claims):
            raise ResourceIntegrityFault("revision cannot represent exact claim carrier")
        warning = state.override_warning
        if warning is not None and (warning.gate_output_place != destination
                or warning.gate_transition_id not in transitions
                or warning.gate_peer_transition_id not in transitions):
            raise ResourceIntegrityFault("revision loses exact override gate authority")
        for identity in (state.lease_identity_ref, *(c.lease_identity_ref for c in state.lease_claims)):
            if identity is not None and identity.entity_type == "logical_artifact_slot/v1" and identity not in slot_refs:
                raise ResourceIntegrityFault("revision cannot substitute an old exact logical slot lease")
        mapped.append((state, destination))
    if seen_retired != retired:
        raise ResourceIntegrityFault("revision retirement names an already consumed/non-successor occurrence")
    count = Counter(destination for _, destination in mapped)
    if any(p.capacity is not None and count[p.name] > p.capacity for p in places.values()):
        raise ResourceIntegrityFault("revision mapped occupancy exceeds candidate capacity")
    return tuple(mapped)


def prepare_module_revision(core, kernel, registration, executable, structure,
                            prior, projected, new_tokens, effects, *, candidate_publisher=None):
    from .event_store import validate_registered_net_closure
    from .module_runtime import hydrate_module_resource_plan
    from .strict_contracts import _registered
    instruction, context = effects.revisions[0]
    raw = instruction.to_dict()
    bundle = context.resources.get(instruction.source_binding)
    if (bundle is None or type(instruction.source_ordinal) is not int
            or not 0 <= instruction.source_ordinal < len(bundle)):
        raise ResourceIntegrityFault("revision fabricates its full Module source binding")
    source = bundle[instruction.source_ordinal]
    if (source.schema_id != "rpnh/module_declaration/v1"
            or not _module_source_matches(instruction.module, source.payload)):
        raise ResourceIntegrityFault("revision Module differs from its exact registered source product")
    references = next(e for e in effects.witnesses if e["effect_kind"] == "structural_revision")
    transition = next(t for t in structure.compiled.symbolic.transitions if t.name ==
        kernel._exact_object(context.firing_ref, expected_type="transition_firing/v1").metadata["transition_id"])
    selected = next(o for o in structure.compiled.operations if o.declaration.name == transition.operation)
    declaration_effect = next(o for o in selected.declaration.outcomes if o.name == context.outcome_id).effects[references["effect_index"]]
    host = registration.declaration("tool", declaration_effect.key)
    if host != structure.compiled.registrations["tool"][declaration_effect.key]:
        raise ResourceIntegrityFault("activation tool differs from exact compiled registered contract")
    validate_activation_contract(host["contracts"], raw["activations"])
    compiled = compile_module(instruction.module, registration)
    structure_delta = derive_petri_structure_delta(
        structure.compiled.symbolic, compiled.symbolic)
    verify_petri_structure_delta(
        structure.compiled.symbolic, compiled.symbolic, structure_delta)
    candidates = []
    publication = None
    if candidate_publisher is not None:
        from .module_nets import ModuleNetPublication
        if not callable(candidate_publisher):
            raise TypeError("revision candidate publisher must be explicit HOST Python authority")
        publication = candidate_publisher(compiled)
        if not isinstance(publication, ModuleNetPublication):
            raise TypeError("revision publisher must return normal typed ModuleNetPublication")
    for row in core.event_store.canonical_object_rows(object_type="net_instance/v1"):
        net = json.loads(row["metadata_json"])
        ref = _version_from_payload(net["net_instance_ref"])
        if ref == executable.net_ref:
            continue
        if publication is not None and ref != publication.net_ref:
            continue
        declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
        if json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id))) == compiled.to_dict():
            candidates.append((ref, net, declaration))
    if len(candidates) != 1:
        raise ResourceIntegrityFault("revision requires one exact normally published full Module candidate")
    candidate_ref, net, declaration = candidates[0]
    validate_registered_net_closure(core.event_store, core.catalog, candidate_ref)
    root_ref = _version_from_payload(net["team_design_root_ref"])
    _, root = _registered(core, root_ref, "team_design_root/v1")
    _, old_root = _registered(core, executable.team_design_root_ref, "team_design_root/v1")
    if any(root[field] != old_root[field] for field in ("task_ref", "run_ref", "owner_principal_ref", "task_round_ref")):
        raise ResourceIntegrityFault("revision candidate crosses exact task/run/owner/round authority")
    plan = hydrate_module_resource_plan(core, compiled, candidate_ref, net, root_ref, root, declaration)
    candidate_structure = RuntimeNet(compiled, net_ref=candidate_ref, resource_plan=plan)
    from .resources import ExecutableTransitionAuthority, NativeLaunchRegisteredArtifact, VerifiedResourceArtifact
    transitions = []
    for value in net["executable_transition_binding_refs"]:
        ref = _version_from_payload(value)
        _, binding = _registered(core, ref, "executable_transition_binding/v1")
        _, operation_binding = _registered(core, _version_from_payload(binding["operation_binding_ref"]), "operation_binding/v1")
        _, spec = _registered(core, _version_from_payload(operation_binding["operation_spec_ref"]), "operation_spec/v1")
        transitions.append(ExecutableTransitionAuthority(binding_ref=ref, transition_id=binding["transition_id"],
            execution_kind=("agent" if binding["agent_ref"] is not None
                            else spec["implementation_contracts"]["transport"]),
            node_ref=_version_from_payload(binding["node_ref"]),
            activation_ref=None if binding["activation_ref"] is None else _version_from_payload(binding["activation_ref"]),
            operation_binding_ref=_version_from_payload(binding["operation_binding_ref"]),
            principal_ref=_version_from_payload(binding["principal_ref"]),
            agent_ref=None if binding["agent_ref"] is None else _version_from_payload(binding["agent_ref"])))
    candidate_executable = replace(executable, net_ref=candidate_ref, team_design_root_ref=root_ref,
        declaration_resource_ref=declaration, transitions=tuple(sorted(transitions, key=lambda t: t.transition_id)),
        declaration=NativeLaunchRegisteredArtifact(canonical_json(compiled.to_dict()),
            VerifiedResourceArtifact(kernel._header(declaration, through_head=kernel._head()), kernel._head())))
    decision = dict(raw, retire_token_refs=instruction.retire_token_refs)
    mapped = _map_states(structure.compiled, compiled, candidate_structure,
        tuple(t.state for t in projected.tokens), declaration_effect.references, decision,
        prior_refs=set(prior.token_refs), resource_metadata=lambda ref:
            kernel._exact_object(ref.as_version_ref(), expected_type="resource_version/v1").metadata,
        slot_refs=set(plan.slot_refs.values()))
    target_tokens, relations, mappings = [], [], []
    next_id = projected.next_token_id
    for state, destination in mapped:
        ref = VersionRef("petri_token/v1", new_id("petri_token"), new_id("petri_token_version"))
        target = replace(state, token_ref=ref, token_id=next_id, place=destination,
            producer=None, consumer=transfer_consumer(candidate_structure, destination), consumed_by=None)
        next_id += 1
        target_tokens.append((ref, target))
        mappings.append({"source_token_ref": _ref_payload(state.token_ref), "new_token_ref": _ref_payload(ref)})
        relations.append(TypedRelation(new_id("relation"), "derived_from", ref, state.token_ref,
            metadata={"operation_revision": True}))
    def checked_resource(ref, schemas):
        data = kernel._exact_object(ref.as_version_ref(), expected_type="resource_version/v1").metadata
        schemas = (schemas,) if isinstance(schemas, str) else schemas
        schema_id = data["content_schema_ref"]
        if data["task_ref"] != root["task_ref"] or schema_id not in schemas:
            raise ResourceIntegrityFault("activation resource differs from exact task/schema")
        source_payload = data["content_schema_authority_ref"]
        source = (_resource_from_payload(source_payload).as_version_ref() if "resource_id" in source_payload
                  else _version_from_payload(source_payload))
        kernel._exact_object(source, expected_type=source.entity_type)
        source_document = json.loads(core.object_store.read_registered(core.get_version(source.version_id)))
        if source.entity_type == "registry_type_catalog/v1":
            source_document = json.loads(source_document["schemas"][schema_id]["source"])
        schema = compiled.registrations["schema"][schema_id]["schema"]
        if source_document != schema:
            raise ResourceIntegrityFault("activation resource lacks exact registered schema source")
        _schema_valid_activation_resource(
            core.object_store.read_registered(core.get_version(ref.resource_version_id)), schema, data["media_type"])
    def checked_identity(ref):
        data = kernel._exact_object(ref, expected_type=ref.entity_type).metadata
        if ref.entity_type == "logical_artifact_slot/v1" and data["team_design_root_ref"] != _ref_payload(root_ref):
            raise ResourceIntegrityFault("activation cannot remint an old logical slot")
        with core.event_store.connect() as db:
            row = db.execute("SELECT 1 FROM objects WHERE object_type='petri_token/v1' AND "
                "(json_extract(metadata_json,'$.lease_identity_ref.version_id')=? OR EXISTS "
                "(SELECT 1 FROM json_each(objects.metadata_json,'$.lease_claims') "
                "WHERE json_extract(value,'$.lease_identity_ref.version_id')=?)) LIMIT 1",
                (str(ref.version_id), str(ref.version_id))).fetchone()
        if row is not None:
            raise ResourceIntegrityFault("activation lease already has published occurrence/claim authority")
    activated = resolve_module_activations(structure.compiled, compiled, candidate_structure,
        structure._resource_plan, plan, raw["activations"], host["contracts"], mapped,
        epoch=projected.epoch, next_token_id=next_id, declaration_ref=declaration.as_version_ref(),
        bindings={t.transition_id: t.binding_ref for t in candidate_executable.transitions},
        bound_resources={name: tuple(r.resource_ref for r in bundle) for name, bundle in context.resources.items()},
        check_resource=checked_resource, check_identity=checked_identity)
    activation_witnesses = []
    for state, sources in activated:
        ref = VersionRef("petri_token/v1", new_id("petri_token"), new_id("petri_token_version"))
        state = replace(state, token_ref=ref)
        target_tokens.append((ref, state))
        activation_witnesses.append({"token_ref": _ref_payload(ref), "authority_refs": [_ref_payload(r) for r in sources]})
        relations.extend(TypedRelation(new_id("relation"), "derived_from", ref, source,
            metadata={"operation_activation": True}) for source in sources)
    next_id += len(activated)
    attempts = tuple(a for a in projected.attempts if a.transition_id in candidate_structure.transitions)
    validate_typed_marking_state(candidate_structure, epoch=projected.epoch, next_token_id=next_id,
        attempts=attempts, tokens=tuple(s for _, s in target_tokens), require_token_refs=True)
    successor = replace(projected, net_ref=candidate_ref, team_design_root_ref=root_ref,
        next_token_id=next_id, attempts=attempts,
        token_refs=tuple(sorted((ref for ref, _ in target_tokens), key=lambda r: canonical_json(_ref_payload(r)))),
        tokens=tuple(PetriTokenAuthority(ref, state, projected.verified_at_head) for ref, state in target_tokens))
    witness = {"effect_index": references["effect_index"], "key": references["key"],
        "source_ref": _ref_payload(source.resource_ref.as_version_ref()),
        "candidate_declaration_ref": _ref_payload(declaration.as_version_ref()),
        "source_firing_ref": _ref_payload(context.firing_ref),
        "predecessor_checkpoint_ref": _ref_payload(prior.checkpoint_ref),
        "instruction": raw, "token_mappings": mappings,
        "petri_structure_delta": structure_delta.to_dict()}
    return PreparedModuleRevision(candidate_executable, successor, tuple(target_tokens),
        projected, new_tokens, witness, tuple(relations), tuple(activation_witnesses))


def stage_operation_revision(core, tx, revision, *, old_executable, checkpoint_ref,
                             settlement):
    from .event_store import validate_registered_net_closure
    net = validate_registered_net_closure(core.event_store, core.catalog, revision.executable.net_ref)
    witness = dict(revision.witness, successor_checkpoint_ref=_ref_payload(checkpoint_ref),
        operation_result_ref=_ref_payload(settlement.operation_result_ref))
    payload = {field: net[field] for field in (
        "team_design_root_ref", "llm_macro_net_ref", "node_refs",
        "operation_binding_refs", "output_binding_refs")}
    payload.update(net_instance_ref=_ref_payload(revision.executable.net_ref),
        supersedes_net_ref=_ref_payload(old_executable.net_ref), operation_revision=witness)
    context = settlement.canonical.context
    tx.append(PendingEvent(event_type="net_adopted/v1", criticality="authoritative",
        stream_id=f"task:{core.task_id}:control", aggregate_id=str(revision.executable.net_ref.entity_id),
        aggregate_type="task_control", idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key,
        payload=payload, payload_schema_ref="registry_v1/net_adopted/v1", task_control=True,
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))


def validate_operation_revision_success(store, catalog, db, *, pending, firing,
        invocation, result, delta, predecessor, checkpoint, exact, metadata,
        events, objects, relations, transaction_id):
    """Reclose registered source, full graph, PN and mapping at BEGIN IMMEDIATE.

    No callback or lowerer runs here. Candidate lowering is the normally
    published self-contained Module wire, verified again by Core.
    """
    from .event_store import RegistryConflict, validate_registered_net_closure, _CANONICAL_EVENT_SQL
    from .declared_effect_validation import _resource_plan, _marking
    from .operation_output_contract import validate_compiled_output_bundle
    from .settlement_material import petri_token_metadata
    from .resources import (ExecutableNetAuthority, NativeLaunchRegisteredArtifact,
        ResourceHeader, VerifiedResourceArtifact)

    def fail(message):
        raise RegistryConflict("operation revision Success: " + message)

    def obj(ref, kind=None):
        if not exact(ref, kind):
            fail("missing exact durable authority")
        value = metadata(ref["version_id"], ref["entity_type"])
        if value is None:
            fail("authority outside ordinary firing visibility")
        return value

    def resource_ref(value):
        return _ref_payload(_resource_from_payload(value).as_version_ref())

    def payload(ref):
        obj(ref, "resource_version/v1")
        row = db.execute("SELECT logical_id,storage_locator,size FROM objects WHERE version_id=?", (ref["version_id"],)).fetchone()
        if row is None or row["logical_id"] != ref["logical_id"] or row["storage_locator"] != "registry-object:" + ref["version_id"]:
            fail("resource lacks exact registered bytes")
        version = TypedId.parse(ref["version_id"], expected="resource_version")
        raw = (store.path.parent / "objects" / version.kind / version.value).read_bytes()
        if len(raw) != row["size"]:
            fail("resource size differs from its envelope")
        return raw

    p = pending.payload
    w = p["operation_revision"]
    closure = delta.get("declared_effects")
    if (p["supersedes_net_ref"] != firing["net_instance_ref"]
            or p["net_instance_ref"] != checkpoint["net_instance_ref"]
            or w["source_firing_ref"] != firing["transition_firing_ref"]
            or w["operation_result_ref"] != result["operation_result_ref"]
            or w["predecessor_checkpoint_ref"] != predecessor["marking_checkpoint_ref"]
            or w["successor_checkpoint_ref"] != checkpoint["marking_checkpoint_ref"]
            or pending.producer_invocation_id != TypedId.parse(invocation["invocation_ref"]["logical_id"])
            or closure is None):
        fail("witness differs from exact ordinary Success identity")
    if sum(e.event_type == "net_adopted/v1" for e in events) != 1 or sum(e.event_type == "transition_firing_settled/v1" for e in events) != 1:
        fail("revision requires one adoption and one ordinary firing Success")
    rows = db.execute("SELECT firing_version_id,net_version_id FROM firing_publications WHERE state='PROVISIONAL'").fetchall()
    if len(rows) != 1 or rows[0]["firing_version_id"] != firing["transition_firing_ref"]["version_id"] or rows[0]["net_version_id"] != firing["net_instance_ref"]["version_id"]:
        fail("full NET switch has another unresolved active firing")
    old_net = obj(firing["net_instance_ref"], "net_instance/v1")
    candidate_net = validate_registered_net_closure(store, catalog, _version_from_payload(p["net_instance_ref"]), _db=db)
    declaration_ref = resource_ref(old_net["team_net_declaration_resource_ref"])
    old_meta = obj(declaration_ref, "resource_version/v1")
    compiled = load_compiled_net(json.loads(payload(declaration_ref)))
    candidate_ref = resource_ref(candidate_net["team_net_declaration_resource_ref"])
    candidate = load_compiled_net(json.loads(payload(candidate_ref)))
    structure_delta = derive_petri_structure_delta(
        compiled.symbolic, candidate.symbolic)
    verify_petri_structure_delta(
        compiled.symbolic, candidate.symbolic, structure_delta)
    structure_delta = structure_delta.to_dict()
    if (candidate_ref != w["candidate_declaration_ref"] or candidate.source.to_dict() != w["instruction"]["module"]
            or not _module_source_matches(candidate.source, payload(w["source_ref"]))
            or w["petri_structure_delta"] != structure_delta):
        fail("candidate full Module differs from exact source declaration/registered lowering")
    ModuleDeclaration.from_dict(w["instruction"]["module"])
    old_root = obj(old_net["team_design_root_ref"], "team_design_root/v1")
    root = obj(candidate_net["team_design_root_ref"], "team_design_root/v1")
    if any(root[f] != old_root[f] for f in ("task_ref", "run_ref", "owner_principal_ref", "task_round_ref")):
        fail("candidate crosses task/run/owner/round")
    for kind in set(compiled.registrations) | set(candidate.registrations):
        declarations = list(compiled.registrations.get(kind, {}).items()) + list(candidate.registrations.get(kind, {}).items())
        for key, host in declarations:
            rows = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='resource_version/v1' "
                "AND json_extract(metadata_json,'$.descriptors.host_registration_kind')=? "
                "AND json_extract(metadata_json,'$.descriptors.registered_key')=? "
                "AND json_extract(metadata_json,'$.task_ref.logical_id')=?", (kind, key, root["task_ref"]["logical_id"])).fetchall()
            wanted = host["schema"] if kind == "schema" else host
            if not any(payload(dict(entity_type="resource_version/v1", logical_id=r["logical_id"], version_id=r["version_id"])) == canonical_json(wanted) for r in rows):
                fail("candidate registration/schema lacks exact durable contract")
    transition = next(t for t in compiled.symbolic.transitions if t.name == firing["transition_id"])
    operation = next(o for o in compiled.operations if o.declaration.name == transition.operation)
    selected = next(o for o in operation.declaration.outcomes if o.name == closure["selected_outcome_id"])
    if len(closure["effects"]) != len(selected.effects):
        fail("selected effect bundle is incomplete")
    # Current boundary is a full structural adoption, not a second route/reset
    # publication. Additional mechanical effects require their exact preparer.
    structural = [e for e in closure["effects"] if e["effect_kind"] == "structural_revision"]
    if len(structural) != 1 or len(selected.effects) != 1:
        fail("full Module boundary requires its single registered structural instruction")
    effect = selected.effects[w["effect_index"]]
    ew = structural[0]
    host = compiled.registrations["tool"][effect.key]
    if (ew["effect_index"] != w["effect_index"] or ew["key"] != w["key"] or effect.key != w["key"]
            or ew["module_revision"] != w["instruction"] or host["contracts"].get("effect_kind") != "structural_revision"
            or ew["petri_structure_delta"] != structure_delta
            or any(ew[f] for f in ("routes", "reset_references", "retired_token_refs"))):
        fail("instruction differs from declared tool effect contract")
    config_schema = host["contracts"].get("config_schema")
    if config_schema is not None:
        Draft7Validator(compiled.registrations["schema"][config_schema]["schema"]).validate(dict(effect.config))
    binding = obj(firing["operation_binding_ref"], "operation_binding/v1")
    spec = obj(binding["operation_spec_ref"], "operation_spec/v1")
    executor = compiled.registrations["executor"][operation.executor_key]
    if (spec["operation_id"] != operation.operation_id or spec["executor_key"] != operation.executor_key
            or spec["implementation_identity"] != executor["identity"]
            or spec["implementation_contracts"] != executor["contracts"]):
        fail("original registered spec differs from compiled operation")
    ports = {port.name: port for port in compiled.ports}
    grouped = {name: [] for name in operation.declaration.outputs}
    produced, outcomes = [], []
    outputs = {obj(ref, "output_binding/v1")["output_port_id"]: (ref, obj(ref, "output_binding/v1")) for ref in binding["output_binding_refs"]}

    def checked_resource(ref, port):
        data = obj(ref, "resource_version/v1")
        if data["task_ref"] != old_root["task_ref"] or data["content_schema_ref"] != port.schema:
            fail("bound resource crosses exact task/schema")
        schema_source = data["content_schema_authority_ref"]
        if "resource_id" in schema_source:
            schema = json.loads(payload(resource_ref(schema_source)))
        else:
            obj(schema_source, "registry_type_catalog/v1")
            version = TypedId.parse(schema_source["version_id"])
            row = db.execute("SELECT storage_locator,size FROM objects WHERE version_id=?", (str(version),)).fetchone()
            raw_catalog = (store.path.parent / "objects" / version.kind / version.value).read_bytes()
            if row is None or row["storage_locator"] != "registry-object:" + str(version) or len(raw_catalog) != row["size"]:
                fail("catalog source lacks exact durable bytes")
            schema = json.loads(json.loads(raw_catalog)["schemas"][port.schema]["source"])
        if schema != compiled.registrations["schema"][port.schema]["schema"]:
            fail("bound schema differs from registered declaration")
        _schema_valid_activation_resource(payload(ref), schema, data["media_type"])
        return data

    for ref in result["output_resource_refs"]:
        data = obj(ref, "resource_version/v1")
        descriptor = data["descriptors"]
        port_id = descriptor["output_port_id"]
        name = next(n for n in grouped if ports[n].port_id == port_id)
        bound_ref, bound = outputs[port_id]
        checked_resource(ref, ports[name])
        if (data["origin"]["primary_ref"] != bound_ref or data["producer_ref"] != invocation["invocation_ref"]
                or data["origin"]["kind"] != "petri_output" or data["net_ref"] != firing["net_instance_ref"]
                or data["round_ref"] != invocation["task_round_ref"] or descriptor["place"] != bound["place"]
                or bound["place"] != ports[name].place):
            fail("whole output differs from original registered binding")
        grouped[name].append(ref)
        outcomes.append(descriptor["output_outcome_id"])
        produced.append(dict(place=ports[name].place, resource_ref=_resource_from_payload({"resource_id": ref["logical_id"],
            "resource_version_id": ref["version_id"]}), output_port_id=port_id,
            output_place_ref=_version_from_payload(bound["place_ref"]), output_binding_ref=_version_from_payload(bound_ref),
            work_resource_ref=None, kind=None, verdict=selected.name, continuation=None, lease_claims=()))
    validate_compiled_output_bundle(operation.declaration, {n: len(v) for n, v in grouped.items()},
        declared_outcomes=tuple(outcomes), selected_outcome_id=selected.name)
    tokens = {canonical_json(ref): obj(ref, "petri_token/v1") for ref in predecessor["token_refs"]}
    claimed = {canonical_json(ref) for ref in firing["claimed_input_refs"]}
    if not claimed <= tokens.keys():
        fail("firing claims are outside latest predecessor")
    bound_resources = []
    for handle, name in effect.bindings.items():
        port = ports[name]
        sources = [(ref, None) for ref in grouped[name]] if name in grouped else [
            (resource_ref(tokens[key]["resource_ref"]), tokens[key]["petri_token_ref"])
            for key in sorted(claimed) if tokens[key]["place"] == port.place and tokens[key]["resource_ref"] is not None]
        for ordinal, (ref, token) in enumerate(sources):
            checked_resource(ref, port)
            bound_resources.append(dict(binding=handle, ordinal=ordinal, resource_ref=ref, claimed_token_ref=token))
    if bound_resources != ew["bound_resources"] or not any(b["binding"] == w["instruction"]["source_binding"]
            and b["ordinal"] == w["instruction"]["source_ordinal"] and b["resource_ref"] == w["source_ref"] for b in bound_resources):
        fail("instruction invents a source outside declared resource bindings")
    if obj(w["source_ref"], "resource_version/v1")["content_schema_ref"] != "rpnh/module_declaration/v1":
        fail("source is not the full current shared Module schema")
    view = RuntimeNet(compiled, net_ref=_version_from_payload(firing["net_instance_ref"]),
        resource_plan=_resource_plan(old_net, old_root, compiled, obj)).for_outcome(firing["transition_id"], selected.name)
    view = view.for_claimed_inputs(firing["transition_id"], tuple((tokens[k]["place"], tokens[k]["verdict"]) for k in claimed))
    represented = {p["place"] for p in produced}
    for place in sorted(set(view.outputs_of(firing["transition_id"])) - represented):
        if view.output_emit_for(firing["transition_id"], place) not in {"content_less", "control_only", "forward"}:
            fail("ordinary selected output lacks its registered product")
        produced.append(dict(place=place, resource_ref=None, output_port_id=None, output_place_ref=None,
            output_binding_ref=None, work_resource_ref=None, kind=None, verdict=None, continuation=None, lease_claims=()))
    local = TeamNetMarking.from_authority(view, _marking(predecessor, tokens, set()))
    epoch = local.install_registered_firing_claim(firing["transition_id"], tuple(_version_from_payload(r) for r in firing["claimed_input_refs"]))
    _, off_arc = local.deposit_outputs(firing["transition_id"], produced, claim_epoch=epoch)
    if off_arc:
        fail("ordinary Success deposits off-arc material")
    local.record_settled_attempt(firing["transition_id"], firing["attempt_index"])
    header = ResourceHeader(_resource_from_payload(old_net["team_net_declaration_resource_ref"]),
        _version_from_payload(old_root["task_ref"]), None, None, _version_from_payload(old_meta["producer_ref"]),
        old_meta["origin_kind"], old_meta["media_type"], len(payload(declaration_ref)), compiled.schema_version,
        None, None, (), (), None)
    executable = ExecutableNetAuthority(_version_from_payload(firing["net_instance_ref"]),
        _version_from_payload(old_net["team_design_root_ref"]), NativeLaunchRegisteredArtifact(payload(declaration_ref),
            VerifiedResourceArtifact(header, None)), _resource_from_payload(old_net["team_net_declaration_resource_ref"]), (), None)
    ordinary = local.pure_typed_snapshot(executable)
    before = {canonical_json(ref) for ref in predecessor["token_refs"]}
    consumed = {canonical_json(ref) for ref in delta["consumed_refs"]}
    deposited = {canonical_json(ref) for ref in delta["deposited_refs"]}
    ordinary_refs = {canonical_json(_ref_payload(s.token_ref)) for s in ordinary.tokens}
    if before - consumed | deposited != ordinary_refs or not consumed <= claimed:
        fail("ordinary PN delta does not describe exact predecessor settlement")
    for state in ordinary.tokens:
        if obj(_ref_payload(state.token_ref), "petri_token/v1") != petri_token_metadata(executable, state, state.token_ref):
            fail("ordinary Success source token differs from Core projection")
    candidate_plan = _resource_plan(candidate_net, root, candidate, obj)
    old_plan = _resource_plan(old_net, old_root, compiled, obj)
    candidate_structure = RuntimeNet(candidate, net_ref=_version_from_payload(p["net_instance_ref"]), resource_plan=candidate_plan)
    instruction = dict(w["instruction"], retire_token_refs=tuple(_version_from_payload(r) for r in w["instruction"]["retire_token_refs"]))
    mapped = _map_states(compiled, candidate, candidate_structure, ordinary.tokens, effect.references, instruction,
        prior_refs={_version_from_payload(r) for r in predecessor["token_refs"]},
        resource_metadata=lambda ref: obj(_ref_payload(ref.as_version_ref()), "resource_version/v1"),
        slot_refs=set(candidate_plan.slot_refs.values()))
    mappings = w["token_mappings"]
    by_source = {canonical_json(m["source_token_ref"]): m["new_token_ref"] for m in mappings}
    activation_witnesses = ew["activation_witnesses"]
    activation_refs = {canonical_json(a["token_ref"]) for a in activation_witnesses}
    mapped_refs = {canonical_json(r) for r in by_source.values()}
    if (len(by_source) != len(mappings) or set(by_source) != {canonical_json(_ref_payload(s.token_ref)) for s, _ in mapped}
            or len(activation_refs) != len(activation_witnesses) or mapped_refs & activation_refs
            or mapped_refs | activation_refs != {canonical_json(r) for r in checkpoint["token_refs"]}
            or len(by_source) + len(activation_refs) != len(checkpoint["token_refs"])):
        fail("mapping and activation must account for every successor occurrence exactly once")
    states = []
    for offset, (source, destination) in enumerate(mapped):
        target_ref = by_source[canonical_json(_ref_payload(source.token_ref))]
        target = obj(target_ref, "petri_token/v1")
        state = replace(source, token_ref=_version_from_payload(target_ref), token_id=ordinary.next_token_id + offset,
            place=destination, producer=None, consumer=transfer_consumer(candidate_structure, destination), consumed_by=None)
        expected = petri_token_metadata(replace(executable, net_ref=_version_from_payload(p["net_instance_ref"]),
            team_design_root_ref=_version_from_payload(candidate_net["team_design_root_ref"])), state, state.token_ref)
        if target != expected or not any(r.relation_type == "derived_from" and r.source == state.token_ref
                and r.target == source.token_ref and r.strength == "strong" for r in relations):
            fail("mapping rewrites exact source authority or lacks SAMEtransaction lineage")
        states.append(state)
    def activation_resource(ref, schemas):
        data = obj(_ref_payload(ref.as_version_ref()), "resource_version/v1")
        schemas = (schemas,) if isinstance(schemas, str) else schemas
        schema_id = data["content_schema_ref"]
        if data["task_ref"] != root["task_ref"] or schema_id not in schemas:
            fail("activation resource differs from exact task/schema")
        schema = candidate.registrations["schema"][schema_id]["schema"]
        source_payload = data["content_schema_authority_ref"]
        if "resource_id" in source_payload:
            source_document = json.loads(payload(resource_ref(source_payload)))
        else:
            obj(source_payload, "registry_type_catalog/v1")
            version = TypedId.parse(source_payload["version_id"])
            row = db.execute("SELECT storage_locator,size FROM objects WHERE version_id=?", (str(version),)).fetchone()
            raw_catalog = (store.path.parent / "objects" / version.kind / version.value).read_bytes()
            if row is None or row["storage_locator"] != "registry-object:" + str(version) or len(raw_catalog) != row["size"]:
                fail("activation catalog lacks exact durable bytes")
            source_document = json.loads(json.loads(raw_catalog)["schemas"][schema_id]["source"])
        if source_document != schema:
            fail("activation resource lacks exact registered schema source")
        _schema_valid_activation_resource(payload(_ref_payload(ref.as_version_ref())), schema, data["media_type"])
    def activation_identity(ref):
        value = _ref_payload(ref)
        data = obj(value, ref.entity_type)
        if ref.entity_type == "logical_artifact_slot/v1" and data["team_design_root_ref"] != candidate_net["team_design_root_ref"]:
            fail("activation substitutes/remints old exact logical slot")
        row = db.execute("SELECT 1 FROM objects WHERE object_type='petri_token/v1' AND transaction_id!=? AND "
            "(json_extract(metadata_json,'$.lease_identity_ref.version_id')=? OR EXISTS "
            "(SELECT 1 FROM json_each(objects.metadata_json,'$.lease_claims') "
            "WHERE json_extract(value,'$.lease_identity_ref.version_id')=?)) LIMIT 1",
            (str(transaction_id), str(ref.version_id), str(ref.version_id))).fetchone()
        if row is not None:
            fail("activation lease already has exact published occurrence/claim authority")
    activation_bindings = {}
    for ref in candidate_net["executable_transition_binding_refs"]:
        data = obj(ref, "executable_transition_binding/v1")
        if data["declaration_resource_ref"] != candidate_net["team_net_declaration_resource_ref"]:
            fail("activation execution binding differs from exact candidate declaration")
        activation_bindings[data["transition_id"]] = _version_from_payload(ref)
    resources = {}
    for bound in bound_resources:
        ref = _version_from_payload(bound["resource_ref"])
        resources.setdefault(bound["binding"], []).append(ResourceVersionRef(ref.entity_id, ref.version_id))
    activated = resolve_module_activations(compiled, candidate, candidate_structure,
        old_plan, candidate_plan, instruction["activations"], host["contracts"], mapped,
        epoch=ordinary.epoch, next_token_id=ordinary.next_token_id + len(mapped),
        declaration_ref=_version_from_payload(candidate_ref), bindings=activation_bindings,
        bound_resources=resources, check_resource=activation_resource, check_identity=activation_identity)
    if len(activated) != len(activation_witnesses):
        fail("activation witness differs from independently resolved inventory")
    staged_tokens = {str(o.version_id) for o in objects if o.object_type == "petri_token/v1"}
    adopted_executable = replace(executable, net_ref=_version_from_payload(p["net_instance_ref"]),
        team_design_root_ref=_version_from_payload(candidate_net["team_design_root_ref"]))
    for (state, sources), witness in zip(activated, activation_witnesses):
        state = replace(state, token_ref=_version_from_payload(witness["token_ref"]))
        if (str(state.token_ref.version_id) not in staged_tokens
                or witness["authority_refs"] != [_ref_payload(r) for r in sources]
                or obj(witness["token_ref"], "petri_token/v1") != petri_token_metadata(adopted_executable, state, state.token_ref)
                or any(not any(r.relation_type == "derived_from" and r.source == state.token_ref
                    and r.target == source and r.strength == "strong" for r in relations) for source in sources)):
            fail("activation token lacks SAMEtransaction exact typed/source authority")
        states.append(state)
    attempts = tuple(a for a in ordinary.attempts if a.transition_id in candidate_structure.transitions)
    if (checkpoint["epoch"] != ordinary.epoch or checkpoint["next_token_id"] != ordinary.next_token_id + len(states)
            or checkpoint["attempts"] != [dict(transition_id=a.transition_id, highest_issued=a.highest_issued) for a in attempts]
            or checkpoint["workspace_revision_refs"] != predecessor.get("workspace_revision_refs", [])):
        fail("candidate checkpoint loses cumulative marking/workspace authority")
    validate_typed_marking_state(candidate_structure, epoch=checkpoint["epoch"], next_token_id=checkpoint["next_token_id"],
        attempts=attempts, tokens=tuple(states), require_token_refs=True)
    authorities = [o for o in objects if o.object_type == "run_execution_authority/v1"]
    row = db.execute("SELECT o.metadata_json FROM objects o JOIN events e ON e.event_id=o.published_event_id "
        "WHERE o.object_type='run_execution_authority/v1' AND " + _CANONICAL_EVENT_SQL + " ORDER BY e.ordinal DESC LIMIT 1").fetchone()
    if len(authorities) != 1 or row is None:
        fail("requires one SAMEtransaction run authority successor")
    current = json.loads(row["metadata_json"])
    successor = authorities[0].metadata
    mutable = {"run_execution_authority_ref", "latest_checkpoint_ref", "declaration_ref", "declaration_schema_ref"}
    if (current["status"] != "running" or current["terminal_evidence_ref"] is not None
            or current["latest_checkpoint_ref"] != w["predecessor_checkpoint_ref"]
            or successor["latest_checkpoint_ref"] != w["successor_checkpoint_ref"]
            or successor["declaration_ref"] != candidate_ref or successor["declaration_schema_ref"] != candidate.schema_version
            or current["run_execution_authority_ref"]["logical_id"] != successor["run_execution_authority_ref"]["logical_id"]
            or {k:v for k,v in current.items() if k not in mutable} != {k:v for k,v in successor.items() if k not in mutable}
            or not any(r.relation_type == "derived_from" and _ref_payload(r.source) == successor["run_execution_authority_ref"]
                and _ref_payload(r.target) == candidate_ref and r.metadata.get("authority_role") == "mutable_stage" for r in relations)):
        fail("run authority reinterprets cumulative facts/prospective source")


def verified_operation_revision_fields(store, catalog, db, event):
    """Current hydration accepts only the exact generic ordinary-Success bridge."""
    from .event_store import RegistryCorruptError, _exact_object_metadata, _CANONICAL_EVENT_SQL
    w = event.payload["operation_revision"]
    refs = (("source_ref", "resource_version/v1"), ("candidate_declaration_ref", "resource_version/v1"),
        ("source_firing_ref", "transition_firing/v1"), ("operation_result_ref", "operation_result/v1"),
        ("predecessor_checkpoint_ref", "marking_checkpoint/v1"), ("successor_checkpoint_ref", "marking_checkpoint/v1"))
    values = {field: _exact_object_metadata(store, _version_from_payload(w[field]), expected_type=kind, db=db) for field, kind in refs}
    checkpoint = values["successor_checkpoint_ref"]
    result = values["operation_result_ref"]
    firing = values["source_firing_ref"]
    rows = db.execute("SELECT e.* FROM events e WHERE e.transaction_id=? AND e.event_type='transition_firing_settled/v1' AND " + _CANONICAL_EVENT_SQL,
        (str(event.transaction_id),)).fetchall()
    commits = db.execute("SELECT e.* FROM events e WHERE e.transaction_id=? AND e.event_type='marking_checkpoint_committed/v1' AND " + _CANONICAL_EVENT_SQL,
        (str(event.transaction_id),)).fetchall()
    if len(rows) != 1 or len(commits) != 1:
        raise RegistryCorruptError("operation adoption lacks ONE SAMEtransaction Success/checkpoint")
    settled = json.loads(rows[0]["payload_json"])
    commit = json.loads(commits[0]["payload_json"])
    if (settled["transition_firing_ref"] != w["source_firing_ref"] or settled["operation_result_ref"] != w["operation_result_ref"]
            or settled["successor_checkpoint_ref"] != w["successor_checkpoint_ref"]
            or commit["checkpoint_ref"] != w["successor_checkpoint_ref"] or commit["net_instance_ref"] != event.payload["net_instance_ref"]
            or checkpoint["net_instance_ref"] != event.payload["net_instance_ref"] or checkpoint["previous_checkpoint_ref"] != w["predecessor_checkpoint_ref"]
            or firing["net_instance_ref"] != event.payload["supersedes_net_ref"] or result["transition_firing_ref"] != w["source_firing_ref"]):
        raise RegistryCorruptError("operation adoption differs from exact bridge identity")
    for field in ("successor_checkpoint_ref", "operation_result_ref"):
        row = db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (w[field]["version_id"],)).fetchone()
        if row is None or row[0] != str(event.transaction_id):
            raise RegistryCorruptError("operation adoption result/checkpoint is not SAMEtransaction")
    return {"operation_revision": w}


__all__ = ("DeclaredModuleRevision", "PreparedModuleRevision")
