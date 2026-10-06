"""Reclose declared route/reset instructions inside the ordinary Success TX.

No HOST callable is resolved or invoked here. The witness selects only symbolic
handles and bound ordinals; all contracts, resources and claims are reread from
the committing SQLite snapshot. Core's own finite marking projection verifies
the complete successor, including ordinary returns and new products.
"""
from __future__ import annotations

from collections import Counter
import json

from jsonschema import Draft7Validator

from ..executable_net import load_compiled_net
from ..marking import TeamNetMarking
from ..runtime_net import RuntimeNet
from .event_store import RegistryConflict
from .identities import TypedId
from .models import VersionRef
from .module_binding_authority import validate_module_bindings
from .module_resources import prepare_module_resources, module_slot_bindings
from .operation_output_contract import validate_compiled_output_bundle
from .publication import (_ref_payload, _resource_from_payload, _version_from_payload,
                          _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE)
from .resources import (AttemptCounterAuthority, ExecutableNetAuthority,
    NativeLaunchRegisteredArtifact, PetriContinuation, PetriLeaseClaim,
    PetriOverrideWarning, PetriTokenAuthority, PetriTokenState, ResourceHeader,
    TypedMarkingAuthority, VerifiedResourceArtifact)
from .schema_catalog import canonical_json
from .settlement_material import petri_token_metadata


def _started_effect_resource(
        started_inputs, *, token_ref, token_resource_ref):
    """Resolve one effect input from the exact durable Start input closure."""
    candidates = {
        canonical_json(token_ref),
        canonical_json(token_resource_ref),
    }
    matches = tuple(
        resource
        for binding, resource in started_inputs
        if canonical_json(binding) in candidates
    )
    return matches[0] if len(matches) == 1 else None


def _durable_reset_retirement_reason(
        state, *, claimed_elsewhere: bool, terminal: bool):
    """Mirror formal reset blockers over durable token metadata."""
    if claimed_elsewhere:
        return "token is claimed by another active firing"
    if terminal:
        return "token occupies a terminal place"
    if state["lease_identity_ref"] is not None or state["lease_claims"]:
        return "token carries unresolved lease authority"
    if any(state[field] is not None for field in (
            "kind", "continuation", "override_warning")):
        return "token carries unresolved coloured work authority"
    if state["consumed_by"] is not None:
        return "token is not a fresh predecessor occurrence"
    return None


def validate_declared_effect_success(store, db, *, firing, invocation, result,
        delta, predecessor, checkpoint, exact, metadata, require_ordinary=False,
        published_transaction_id=None, historical_cut=None,
        ordinary_token_ref_scheme=None):
    """Validate an original settlement cut or its exact canonical read snapshot."""
    def fail(message):
        raise RegistryConflict("declared effect Success: " + message)

    if (published_transaction_id is None) != (historical_cut is None):
        fail("canonical read requires both publication transaction and historical cut")
    if historical_cut is not None and (type(historical_cut) is not int or historical_cut < 0):
        fail("canonical read cut is malformed")
    if published_transaction_id is not None and not require_ordinary:
        fail("canonical read is restricted to ordinary collaboration closure")
    if 'ordinary_token_ref_scheme' in delta:
        from .normal_root_token_allocation import (
            NORMAL_ROOT_TOKEN_SCHEME, load_normal_root_token_schema,
            validate_normal_root_token_delta,
        )
        if (ordinary_token_ref_scheme != NORMAL_ROOT_TOKEN_SCHEME
                or delta['ordinary_token_ref_scheme'] != ordinary_token_ref_scheme):
            fail('normal token marker lacks its independently bound Success')
        capability_cut = (historical_cut if historical_cut is not None else int(
            db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0]))
        persisted_schema = load_normal_root_token_schema(store, db,
            task_id=TypedId.parse(invocation['task_ref']['logical_id'], expected='task'),
            cut=capability_cut)
        validate_normal_root_token_delta(persisted_schema, delta)
        require_ordinary = True
    elif ordinary_token_ref_scheme is not None:
        fail('normal token allocation selector lacks its exact delta marker')

    def obj(ref, kind=None):
        if not exact(ref, kind):
            fail("missing exact registered object")
        value = metadata(ref["version_id"], ref["entity_type"])
        if value is None:
            fail("object is outside the firing's durable visibility")
        return value

    def resource_ref(value):
        return {"entity_type": "resource_version/v1", "logical_id": value["resource_id"],
            "version_id": value["resource_version_id"]}

    def schema_ref(value):
        return resource_ref(value) if "resource_id" in value else value

    def payload(ref):
        obj(ref, "resource_version/v1")
        row = db.execute("SELECT logical_id,storage_locator,size FROM objects WHERE version_id=?",
            (ref["version_id"],)).fetchone()
        if (row is None or row["logical_id"] != ref["logical_id"]
                or row["storage_locator"] != "registry-object:" + ref["version_id"]):
            fail("resource lacks exact durable byte location")
        version = TypedId.parse(ref["version_id"], expected="resource_version")
        data = (store.path.parent / "objects" / version.kind / version.value).read_bytes()
        if len(data) != row["size"]:
            fail("resource byte quantity differs from durable envelope")
        return data

    net = obj(firing["net_instance_ref"], "net_instance/v1")
    declaration_ref = resource_ref(net["team_net_declaration_resource_ref"])
    declaration_meta = obj(declaration_ref, "resource_version/v1")
    closure = delta.get("declared_effects")
    if declaration_meta.get("content_schema_ref") != "rpnh/executable_net/v1":
        if closure is not None or require_ordinary:
            fail("effect closure requires the current registered Module declaration")
        return
    compiled = load_compiled_net(json.loads(payload(declaration_ref)))
    transition = next(t for t in compiled.symbolic.transitions if t.name == firing["transition_id"])
    operation = next(o for o in compiled.operations if o.declaration.name == transition.operation)
    if closure is None:
        # Products carry a durable selected outcome. Empty bundles without an
        # effect are unchanged; omission cannot authorize an effect outcome.
        colours = {obj(r, "resource_version/v1").get("descriptors", {}).get("output_outcome_id")
                   for r in result["output_resource_refs"]} - {None}
        possible = [o for o in operation.declaration.outcomes
                    if not colours or o.name in colours]
        if any(o.effects for o in possible):
            fail("selected registered effects lack their instruction closure")
        if not require_ordinary:
            return
        if len(colours) != 1 or len(possible) != 1:
            fail("ordinary collaboration Success requires one durable selected outcome")
        # Local validation input only; no synthetic event or effect is published.
        closure = {"selected_outcome_id": possible[0].name, "effects": []}
    selected = next(o for o in operation.declaration.outcomes if o.name == closure["selected_outcome_id"])
    if len(closure["effects"]) != len(selected.effects):
        fail("witness differs from the complete selected effect contract")
    root = obj(net["team_design_root_ref"], "team_design_root/v1")
    binding = obj(firing["operation_binding_ref"], "operation_binding/v1")
    spec = obj(binding["operation_spec_ref"], "operation_spec/v1")
    host_executor = compiled.registrations["executor"][operation.executor_key]
    if (spec["operation_id"] != operation.operation_id or spec["executor_key"] != operation.executor_key
            or spec["implementation_identity"] != host_executor["identity"]
            or spec["implementation_contracts"] != host_executor["contracts"]
            or binding["node_ref"] != firing["node_ref"]
            or root["task_ref"] != invocation["task_ref"]):
        fail("compiled operation differs from durable spec/binding/task")
    # Reclose actual HOST data (not callables or a redundant identity catalog).
    for kind, declarations in compiled.registrations.items():
        for key, host in declarations.items():
            rows = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='resource_version/v1' "
                "AND json_extract(metadata_json,'$.descriptors.host_registration_kind')=? "
                "AND json_extract(metadata_json,'$.descriptors.registered_key')=? "
                "AND json_extract(metadata_json,'$.task_ref.logical_id')=?",
                (kind, key, root["task_ref"]["logical_id"])).fetchall()
            wanted = host["schema"] if kind == "schema" else host
            if not any(payload({"entity_type": "resource_version/v1", "logical_id": row["logical_id"],
                    "version_id": row["version_id"]}) == canonical_json(wanted) for row in rows):
                fail("compiled HOST implementation/schema contract lacks durable authority")
    ports = {p.name: p for p in compiled.ports}
    output_bindings = {obj(r, "output_binding/v1")["output_port_id"]: (r, obj(r, "output_binding/v1"))
                       for r in binding["output_binding_refs"]}
    spec_outputs = {p["port_id"]: p for p in spec["output_ports"]}
    grouped = {name: [] for name in operation.declaration.outputs}
    produced = []
    colours = []

    def checked_resource(ref, port):
        data = obj(ref, "resource_version/v1")
        if data["task_ref"] != root["task_ref"] or data["content_schema_ref"] != port.schema:
            fail("bound resource differs from exact task/schema")
        source = schema_ref(data["content_schema_authority_ref"])
        if source["entity_type"] == "resource_version/v1":
            document = json.loads(payload(source))
        else:
            obj(source, "registry_type_catalog/v1")
            version = TypedId.parse(source["version_id"], expected="resource_version")
            row = db.execute("SELECT storage_locator,size FROM objects WHERE version_id=?",
                             (str(version),)).fetchone()
            raw_catalog = (store.path.parent / "objects" / version.kind / version.value).read_bytes()
            if (row is None or row["storage_locator"] != "registry-object:" + str(version)
                    or len(raw_catalog) != row["size"]):
                fail("schema catalog lacks exact registered bytes")
            document = json.loads(json.loads(raw_catalog)["schemas"][port.schema]["source"])
        if document != compiled.registrations["schema"][port.schema]["schema"]:
            fail("bound resource schema source differs from registered contract")
        raw = payload(ref)
        instance = _content_schema_instance(raw, media_type=data["media_type"])
        if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
            Draft7Validator(document).validate(instance)
        return data

    for ref in result["output_resource_refs"]:
        data = obj(ref, "resource_version/v1")
        descriptor = data.get("descriptors", {})
        port_id = descriptor.get("output_port_id")
        name = next((name for name in grouped if ports[name].port_id == port_id), None)
        if name is None or port_id not in output_bindings or port_id not in spec_outputs:
            fail("whole bundle invents an output port")
        port = ports[name]
        bound_ref, bound = output_bindings[port_id]
        expected_schema = schema_ref(bound["content_schema_ref"])
        if (data["origin"]["kind"] != "petri_output" or data["origin"]["primary_ref"] != bound_ref
                or data["producer_ref"] != invocation["invocation_ref"]
                or data["net_ref"] != firing["net_instance_ref"]
                or data["round_ref"] != invocation["task_round_ref"]
                or descriptor.get("place") != port.place or bound["place"] != port.place
                or spec_outputs[port_id]["content_schema_ref"] != bound["content_schema_ref"]
                or schema_ref(data["content_schema_authority_ref"]) != expected_schema):
            fail("whole output resource differs from exact binding/spec/origin")
        checked_resource(ref, port)
        colours.append(descriptor.get("output_outcome_id"))
        grouped[name].append(ref)
        produced.append({"place": port.place, "resource_ref": _resource_from_payload({
            "resource_id": ref["logical_id"], "resource_version_id": ref["version_id"]}),
            "output_port_id": port_id, "output_place_ref": _version_from_payload(bound["place_ref"]),
            "output_binding_ref": _version_from_payload(bound_ref), "work_resource_ref": None,
            "kind": None, "verdict": selected.name, "continuation": None, "lease_claims": ()})
    validate_compiled_output_bundle(operation.declaration, {name: len(values) for name, values in grouped.items()},
        declared_outcomes=tuple(colours), selected_outcome_id=selected.name)
    if require_ordinary and selected.effects:
        fail("ordinary collaboration Success requires an effect-free selected outcome")
    if not selected.effects and not require_ordinary:
        return
    lease_ref = invocation.get("operation_execution_lease_ref")
    invocation_ref = invocation.get("invocation_ref")
    start_rows = db.execute(
        "SELECT producer_invocation_id,payload_json FROM events "
        "WHERE event_type='operation_execution_started/v1' "
        "AND aggregate_id=?",
        (str((lease_ref or {}).get("logical_id", "")),),
    ).fetchall()
    if len(start_rows) != 1:
        fail("effect input lacks one exact durable operation Start")
    start = json.loads(start_rows[0]["payload_json"])
    start_bindings = start.get("input_binding_refs")
    start_resources = start.get("input_resource_refs")
    if (start_rows[0]["producer_invocation_id"]
            != str((invocation_ref or {}).get("logical_id", ""))
            or start.get("invocation_ref") != invocation_ref
            or start.get("operation_execution_lease_ref") != lease_ref
            or start.get("operation_binding_ref")
            != firing["operation_binding_ref"]
            or start.get("transition_firing_ref")
            != firing["transition_firing_ref"]
            or (require_ordinary and start.get("claimed_input_refs") != firing["claimed_input_refs"])
            or not isinstance(start_bindings, list)
            or not isinstance(start_resources, list)
            or len(start_bindings) != len(start_resources)):
        fail("effect input differs from exact durable operation Start")
    if require_ordinary:
        if predecessor.get("workspace_revision_refs") or db.execute(
                "SELECT 1 FROM events WHERE event_type='petri_firing_resource_accessed/v1' AND aggregate_id=?",
                (firing["transition_firing_ref"]["logical_id"],)).fetchone():
            fail("this ordinary collaboration projection requires no workspace or dynamic resource access")
        for binding_ref, resource in zip(start_bindings, start_resources, strict=True):
            binding_value = obj(binding_ref)
            if binding_ref["entity_type"] == "petri_token/v1" and binding_value.get("resource_ref") != resource:
                fail("durable Start input differs from its exact claimed occurrence")
    started_inputs = tuple(zip(start_bindings, start_resources, strict=True))
    for values in grouped.values():
        values.sort(key=lambda r: r["version_id"])
    tokens = {canonical_json(ref): obj(ref, "petri_token/v1") for ref in predecessor["token_refs"]}
    claimed = {canonical_json(ref) for ref in firing["claimed_input_refs"]}
    if not claimed <= tokens.keys():
        fail("firing lacks exact latest predecessor claim membership")
    view = RuntimeNet(compiled, net_ref=_version_from_payload(firing["net_instance_ref"]),
        resource_plan=_resource_plan(net, root, compiled, obj)).for_outcome(firing["transition_id"], selected.name)
    claimed_colours = tuple((tokens[ref]["place"], tokens[ref]["verdict"]) for ref in claimed)
    view = view.for_claimed_inputs(firing["transition_id"], claimed_colours)
    places = {p.name: p for p in compiled.symbolic.places}
    consumed_places = set(view.inputs_of(firing["transition_id"]))
    # Core read is exact consume-return: it also retires its predecessor ref.
    consumed = {ref for ref in claimed if tokens[ref]["place"] in consumed_places}
    other_claims = set()
    if published_transaction_id is None:
        rows = db.execute("SELECT p.firing_version_id,o.metadata_json FROM firing_publications p JOIN objects o "
            "ON o.version_id=p.firing_version_id WHERE p.state='PROVISIONAL' AND p.net_version_id=?",
            (firing["net_instance_ref"]["version_id"],)).fetchall()
    else:
        current = db.execute("SELECT state,published_transaction_id FROM firing_publications WHERE firing_version_id=?",
            (firing["transition_firing_ref"]["version_id"],)).fetchone()
        if current is None or current["state"] != "PUBLISHED" or current["published_transaction_id"] != published_transaction_id:
            fail("canonical read differs from the original published firing")
        rows = db.execute("SELECT p.firing_version_id,o.metadata_json FROM firing_publications p JOIN objects o "
            "ON o.version_id=p.firing_version_id JOIN events opened ON opened.event_id=o.published_event_id "
            "LEFT JOIN events closed ON closed.transaction_id=p.published_transaction_id "
            "AND closed.event_type='transaction_committed/v1' WHERE p.net_version_id=? AND opened.ordinal<=? "
            "AND (p.state='PROVISIONAL' OR closed.ordinal>?)",
            (firing["net_instance_ref"]["version_id"], historical_cut, historical_cut)).fetchall()
    if firing["transition_firing_ref"]["version_id"] not in {row["firing_version_id"] for row in rows}:
        fail("current firing lacks its live PROVISIONAL root")
    for row in rows:
        active = json.loads(row["metadata_json"])
        if active["net_instance_ref"] != firing["net_instance_ref"]:
            fail("active claim differs from exact net")
        claim_ref = active["claim_marking_delta_ref"]
        member = db.execute("SELECT 1 FROM firing_temporary_members WHERE firing_version_id=? "
            "AND member_kind='object' AND member_identity=?", (row["firing_version_id"], claim_ref["version_id"])).fetchone()
        claim_row = db.execute("SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='marking_delta/v1'", (claim_ref["version_id"],)).fetchone()
        claim = json.loads(claim_row["metadata_json"]) if claim_row is not None else {}
        if (member is None or claim_row["logical_id"] != claim_ref["logical_id"]
                or claim.get("marking_delta_ref") != claim_ref or claim.get("phase") != "claim"
                or claim.get("transition_firing_refs") != [active["transition_firing_ref"]]
                or claim.get("net_instance_ref") != firing["net_instance_ref"]):
            fail("active claim lacks exact durable temporary authority")
        if row["firing_version_id"] != firing["transition_firing_ref"]["version_id"]:
            other_claims.update(canonical_json(ref) for ref in active["claimed_input_refs"])
    terminal_places = {ports[f"{t.source.component}.{t.source.port}"].place for t in
        (compiled.source.terminal, *compiled.source.terminal_alternatives)}
    retired, route_refs, route_counts = set(), set(), Counter()
    selected_output_refs = set()
    for index, (effect, witness) in enumerate(zip(selected.effects, closure["effects"], strict=True)):
        host = compiled.registrations["tool"][effect.key]
        if (witness["effect_index"] != index
                or witness["key"] != effect.key
                or witness["effect_kind"] != "petri_action"):
            fail("instruction differs from selected registered tool contract")
        schema_id = host["contracts"].get("config_schema")
        if schema_id:
            Draft7Validator(compiled.registrations["schema"][schema_id]["schema"]).validate(dict(effect.config))
        bound = []
        for handle, name in effect.bindings.items():
            port = ports[name]
            if name in grouped:
                sources = [(ref, None) for ref in grouped[name]]
            else:
                if name not in operation.declaration.inputs:
                    fail("effect input differs from exact declared input binding")
                sources = []
                for key in sorted(claimed):
                    token = tokens[key]
                    if (token["place"] != port.place
                            or token["resource_ref"] is None):
                        continue
                    token_resource_ref = resource_ref(token["resource_ref"])
                    started_resource = _started_effect_resource(
                        started_inputs,
                        token_ref=token["petri_token_ref"],
                        token_resource_ref=token_resource_ref,
                    )
                    if started_resource is None:
                        fail("effect input was not delivered by exact durable Start")
                    sources.append((resource_ref(started_resource),
                                    token["petri_token_ref"]))
                if not port.minimum <= len(sources) <= port.maximum:
                    fail("effect input lacks exact claimed resource quantity")
            for ordinal, (ref, token_ref) in enumerate(sources):
                checked_resource(ref, port)
                bound.append({"binding": handle, "ordinal": ordinal, "resource_ref": ref, "claimed_token_ref": token_ref})
        if witness["bound_resources"] != bound:
            fail("instruction fabricates or omits an exact bound resource")

        def affected(handle):
            reference = effect.references.get(handle)
            if reference is None or reference.kind != "place" or reference.name not in places:
                fail("instruction addresses an undeclared symbolic place")
            return reference.name

        resets = set()
        for handle in witness["reset_references"]:
            place = affected(handle)
            if view.reset_place_for_selector(
                    firing["transition_id"], handle) != place:
                fail("reset differs from its exact selected Petri reset arc")
            resets.add(place)
        if any(places[p].reusable or places[p].token_kind in {"agent_resource", "resource_lease"} for p in resets):
            fail("reset targets reusable capacity/lease authority")
        effect_retired = set()
        for ref, state in tokens.items():
            if state["place"] not in resets or ref in consumed:
                continue
            reason = _durable_reset_retirement_reason(
                state,
                claimed_elsewhere=ref in other_claims,
                terminal=state["place"] in terminal_places,
            )
            if reason is not None:
                fail("reset discards unresolved authority: " + reason)
            effect_retired.add(ref)
        if {canonical_json(ref) for ref in witness["retired_token_refs"]} != effect_retired:
            fail("retirement differs from exact old ordinary predecessor refs")
        retired.update(effect_retired)
        for route in witness["routes"]:
            place = affected(route["reference"])
            source = next((b for b in bound if (b["binding"], b["ordinal"]) == (route["binding"], route["ordinal"])), None)
            if source is None or view.output_emit_for(firing["transition_id"], place) != "route_selected":
                fail("route differs from exact bound selection/selected PN arc")
            port = ports[effect.bindings[route["binding"]]]
            target = places[place]
            token = None if source["claimed_token_ref"] is None else tokens[canonical_json(source["claimed_token_ref"])]
            if (target.reusable or target.token_kind in {"agent_resource", "resource_lease"}
                    or port.schema not in target.admitted_schemas or port.channel != target.channel
                    or token is not None and (places[token["place"]].reusable or token["lease_identity_ref"] is not None or token["lease_claims"])):
                fail("route duplicates lease/capacity or changes resource type")
            key = canonical_json(route["token_ref"])
            if key in route_refs:
                fail("route repeats a successor occurrence")
            route_refs.add(key)
            route_counts[place] += 1
            colour = view.project_output_color(firing["transition_id"], place, claimed_colours)
            ref = source["resource_ref"]
            produced.append({"place": place, "resource_ref": _resource_from_payload({
                "resource_id": ref["logical_id"], "resource_version_id": ref["version_id"]}),
                "output_port_id": None, "output_place_ref": None, "output_binding_ref": None,
                "work_resource_ref": None, "kind": None, "verdict": colour,
                "continuation": None, "lease_claims": (), "selected_route_occurrence": True})
            deposited = obj(route["token_ref"], "petri_token/v1")
            if (deposited["place"] != place or resource_ref(deposited["resource_ref"]) != ref
                    or deposited["verdict"] != colour):
                fail("route witness differs from its exact deposited token")
        selected_places = set()
        for output in witness["outputs"]:
            handle = output["reference"]
            place = affected(handle)
            if (place in selected_places
                    or view.selected_output_place_for_selector(
                        firing["transition_id"], handle) != place):
                fail("selected output differs from its exact Petri output arc")
            selected_places.add(place)
            colour = view.project_output_color(
                firing["transition_id"], place, claimed_colours)
            refs = output["token_refs"]
            if len(refs) != view.output_arc_weight(
                    firing["transition_id"], place):
                fail("selected output token quantity differs from its Petri arc")
            for token_ref in refs:
                key = canonical_json(token_ref)
                if key in selected_output_refs:
                    fail("selected output repeats a successor occurrence")
                selected_output_refs.add(key)
                deposited = obj(token_ref, "petri_token/v1")
                if (deposited["place"] != place
                        or deposited["producer"] != firing["transition_id"]
                        or deposited["resource_ref"] is not None
                        or deposited["verdict"] != colour):
                    fail(
                        "selected output witness differs from its exact deposited token")
            produced.append({
                "place": place,
                "resource_ref": None,
                "output_port_id": None,
                "output_place_ref": None,
                "output_binding_ref": None,
                "work_resource_ref": None,
                "kind": None,
                "verdict": colour,
                "continuation": None,
                "lease_claims": (),
                "selected_control_occurrence": True,
            })
    if any(count > view.output_arc_weight(firing["transition_id"], place) for place, count in route_counts.items()):
        fail("selected route count exceeds PN output weight")
    if {canonical_json(ref) for ref in delta["consumed_refs"]} != consumed | retired:
        fail("delta consumption differs from claims plus verified retirement")
    actual_routes = {canonical_json(ref) for ref in delta["deposited_refs"]
        if view.output_emit_for(firing["transition_id"], obj(ref, "petri_token/v1")["place"]) == "route_selected"}
    if actual_routes != route_refs:
        fail("unselected route deposit or omitted selected occurrence")
    actual_selected_outputs = {
        canonical_json(ref) for ref in delta["deposited_refs"]
        if view.output_effect_selector_for(
            firing["transition_id"],
            obj(ref, "petri_token/v1")["place"]) is not None}
    if actual_selected_outputs != selected_output_refs:
        fail("unselected control deposit or omitted selected occurrence")
    represented = {p["place"] for p in produced}
    for place in sorted(set(view.outputs_of(firing["transition_id"])) - represented):
        emit = view.output_emit_for(firing["transition_id"], place)
        if (emit == "route_selected"
                or view.output_effect_selector_for(
                    firing["transition_id"], place) is not None):
            continue
        if emit not in {"content_less", "control_only", "forward"}:
            fail("selected ordinary product lacks exact resource")
        produced.append({"place": place, "resource_ref": None, "output_port_id": None,
            "output_place_ref": None, "output_binding_ref": None, "work_resource_ref": None,
            "kind": None, "verdict": None, "continuation": None, "lease_claims": ()})
    prior = _marking(predecessor, tokens, retired)
    local = TeamNetMarking.from_authority(view, prior)
    epoch = local.install_registered_firing_claim(firing["transition_id"], tuple(_version_from_payload(r) for r in firing["claimed_input_refs"]))
    _deposited, off_arc = local.deposit_outputs(firing["transition_id"], produced, claim_epoch=epoch)
    if off_arc:
        fail("ordinary successor has an undeclared deposit")
    local.record_settled_attempt(firing["transition_id"], firing["attempt_index"])
    declaration = payload(declaration_ref)
    header = ResourceHeader(_resource_from_payload(net["team_net_declaration_resource_ref"]),
        _version_from_payload(root["task_ref"]), None, None, _version_from_payload(declaration_meta["producer_ref"]),
        declaration_meta["origin_kind"], declaration_meta["media_type"], len(declaration), compiled.schema_version,
        None, None, (), (), None)
    executable = ExecutableNetAuthority(_version_from_payload(firing["net_instance_ref"]),
        _version_from_payload(net["team_design_root_ref"]), NativeLaunchRegisteredArtifact(declaration,
        VerifiedResourceArtifact(header, None)), _resource_from_payload(net["team_net_declaration_resource_ref"]), (), None)
    expected = local.pure_typed_snapshot(executable,
        ordinary_token_ref_scheme=ordinary_token_ref_scheme,
        allocation_firing_ref=(_version_from_payload(firing['transition_firing_ref'])
            if ordinary_token_ref_scheme is not None else None))
    expected_tokens = {canonical_json(_ref_payload(state.token_ref)): petri_token_metadata(executable, state, state.token_ref)
                       for state in expected.tokens}
    actual_tokens = {canonical_json(ref): obj(ref, "petri_token/v1") for ref in checkpoint["token_refs"]}
    if (expected_tokens != actual_tokens or checkpoint["epoch"] != expected.epoch
            or checkpoint["next_token_id"] != expected.next_token_id
            or checkpoint["attempts"] != [{"transition_id": a.transition_id, "highest_issued": a.highest_issued} for a in expected.attempts]):
        fail("complete local successor differs from Core PN projection")
    occupied = Counter(t["place"] for t in actual_tokens.values())
    if any(places[p].capacity is not None and count > places[p].capacity for p, count in occupied.items()):
        fail("complete successor exceeds declared capacity")


def _resource_plan(net, root, compiled, obj):
    nodes = {_version_from_payload(ref): obj(ref, "node_declaration/v1") for ref in net["node_refs"]}
    outputs = {_version_from_payload(ref): obj(ref, "output_binding/v1") for ref in net["output_binding_refs"]}
    validate_module_bindings(net, root, outputs, nodes, obj, RegistryConflict)
    binding = net["module_resource_bindings"]
    plan = prepare_module_resources(compiled, root_ref=_version_from_payload(net["team_design_root_ref"]),
        net_ref=_version_from_payload(net["net_instance_ref"]), declaration_ref=_resource_from_payload(net["team_net_declaration_resource_ref"]),
        node_refs={value["transition_id"]: ref for ref, value in nodes.items()},
        output_binding_refs={(nodes[_version_from_payload(value["node_ref"])]["transition_id"], value["output_port_id"]): ref
            for ref, value in outputs.items()}, owner_resource_inputs={key: _resource_from_payload(ref)
            for key, ref in binding["owner_resource_inputs"].items()}, idempotency_key=binding["command_id"],
        preserved_slot_refs={key: _version_from_payload(ref) for key, ref in binding["slot_refs"].items()})
    if (binding["lease_refs"] != {key: _ref_payload(ref) for key, ref in plan.lease_refs.items()}
            or binding["slot_refs"] != {key: _ref_payload(ref) for key, ref in plan.slot_refs.items()}
            or binding.get("slot_bindings", {}) != module_slot_bindings(plan)):
        raise RegistryConflict("declared effect resource plan differs from exact binding authority")
    return plan


def _marking(checkpoint, tokens, retired):
    def ref(value):
        return None if value is None else _version_from_payload(value)

    def resource(value):
        return None if value is None else _resource_from_payload(value)

    authorities = []
    for key, data in tokens.items():
        if key in retired:
            continue
        token_ref = ref(data["petri_token_ref"])
        continuation = data["continuation"]
        state = PetriTokenState(token_ref, data["token_id"], data["place"], data["epoch"], data["producer"],
            data["consumer"], resource(data["resource_ref"]), resource(data["work_resource_ref"]), data["kind"],
            data["consumed_by"], None if data["override_warning"] is None else PetriOverrideWarning(**data["override_warning"]),
            data["verdict"], None if continuation is None else PetriContinuation(continuation["round"], resource(continuation["source_ref"])),
            ref(data["lease_identity_ref"]), tuple(PetriLeaseClaim(ref(c["lease_identity_ref"]), resource(c["expected_resource_ref"]),
                c["access_mode"], c.get("staging_place")) for c in data["lease_claims"]))
        authorities.append(PetriTokenAuthority(token_ref, state, None))
    return TypedMarkingAuthority(ref(checkpoint["marking_checkpoint_ref"]), ref(checkpoint["net_instance_ref"]),
        ref(checkpoint["team_design_root_ref"]), checkpoint["epoch"], checkpoint["next_token_id"],
        tuple(AttemptCounterAuthority(a["transition_id"], a["highest_issued"]) for a in checkpoint["attempts"]),
        tuple(ref(r) for r in checkpoint["token_refs"] if canonical_json(r) not in retired),
        tuple(sorted(authorities, key=lambda a: a.state.token_id)), ref(checkpoint["previous_checkpoint_ref"]),
        ref(checkpoint["settlement_delta_ref"]), tuple(ref(r) for r in checkpoint["transition_firing_refs"]), None)
