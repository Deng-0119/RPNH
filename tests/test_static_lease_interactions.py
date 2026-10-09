"""Offline coexistence of static lease references and existing Petri mechanisms."""
from dataclasses import replace
from copy import deepcopy

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import apply_replacement, prepare_replacement
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, DeclarationError, InitialTokenDeclaration,
    LeaseClaimTemplate, LeaseIdentityDeclaration, LogicalSlotBinding,
    OutcomeDeclaration, PlaceDeclaration, PortBinding, PortDeclaration,
    ResetArcDeclaration, ResourceLeasePoolBinding, TransitionDeclaration,
    VariableResourceArc,
)
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_effects import DeclaredEffectInstruction
from cpn.rpnh.registry.module_execution import install_active_module_claims
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from test_static_lease_reads import ALT_TEXT, TEXT, _registration, _simple_module, lease_owner


EFFECT = "test/static-lease-reset/v1"


def _world(path, *, same_pool=True, variable_mode="read", effect=False,
           reset_lease=False, with_edit=False, static_read=True, extra_static_resources=0):
    registration = _registration()
    if effect:
        registration.register_tool(EFFECT,
            lambda _context: DeclaredEffectInstruction(reset_references=("clear",)),
            identity={"implementation_id": "test.static_lease_reset", "revision": "v1"},
            contracts={"config_schema": CONFIG_SCHEMA_ID})

    def lower(config, context):
        fragment = lower_operation(config, context)
        # Lease-only authority uses a different schema from operation bytes.
        # This keeps the fixture independent of explicit byte-input lease ports.
        static_names = ("asset", *("spare" + str(index) for index in range(extra_static_resources)))
        places = [*fragment.places, PlaceDeclaration("static", ALT_TEXT,
            token_kind="resource_lease", capacity=len(static_names), reusable=True)]
        identities = [LeaseIdentityDeclaration(name) for name in static_names]
        pools = [ResourceLeasePoolBinding("static", "static", static_names)]
        slots = []
        if variable_mode == "read":
            pool = "static" if same_pool else "variable"
            claim = LeaseClaimTemplate("asset" if same_pool else "other",
                "asset" if same_pool else "other", "read")
            if not same_pool:
                places.append(PlaceDeclaration("variable", ALT_TEXT,
                    token_kind="resource_lease", capacity=1, reusable=True))
                identities.append(LeaseIdentityDeclaration("other"))
                pools.append(ResourceLeasePoolBinding("variable", "variable", ("other",)))
        else:
            pool = "variable"
            places.append(PlaceDeclaration("variable", TEXT,
                token_kind="resource_lease", capacity=1, reusable=True))
            identities.append(LeaseIdentityDeclaration("document", kind="slot", slot="document"))
            pools.append(ResourceLeasePoolBinding("variable", "variable", initial_slots=("document",)))
            slots.append(LogicalSlotBinding("document", "run", "result", "result", "result", TEXT))
            claim = LeaseClaimTemplate("document", access_mode=variable_mode)
        resets = []
        if effect or reset_lease:
            if not reset_lease:
                places.append(PlaceDeclaration("scratch", TEXT,
                    initial_tokens=(InitialTokenDeclaration(),)))
            resets.append(ResetArcDeclaration("static" if reset_lease else "scratch",
                "run", "clear", "complete"))
        transitions = fragment.transitions
        operations = fragment.operations
        arcs = fragment.arcs
        if static_read:
            arcs = (*arcs, ArcDeclaration("static", "run", "input", mode="read"))
        variables = (VariableResourceArc("run", "request", pool, (claim,)),)
        internal_ports = ()
        internal_bindings = ()
        if with_edit:
            transitions = (*transitions, TransitionDeclaration("edit", "edit"))
            operations = (*operations, replace(operations[0], name="edit",
                inputs=("edit_request",), outputs=(), request_port=None,
                outcomes=(OutcomeDeclaration("complete", ()),)))
            internal_ports = (PortDeclaration("edit_request", "input", TEXT),)
            internal_bindings = (PortBinding("edit_request", "result"),)
            arcs = (*arcs, ArcDeclaration("result", "edit", "input"))
            if static_read:
                arcs = (*arcs, ArcDeclaration("static", "edit", "input", mode="read"))
            variables = (*variables, VariableResourceArc("edit", "result", "variable",
                (LeaseClaimTemplate("document", access_mode="edit"),)))
        return replace(fragment,
            places=tuple(places), transitions=transitions, operations=operations,
            internal_ports=internal_ports, internal_bindings=internal_bindings,
            arcs=arcs,
            lease_identities=tuple(identities), lease_pools=tuple(pools),
            logical_slots=tuple(slots), reset_arcs=tuple(resets),
            variable_resource_arcs=variables)

    registration.register_component("interactions", lower,
        identity={"implementation_id": "test.static_lease_interactions", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("LeaseInteractions").to_dict()
    document["components"][0]["key"] = "interactions"
    document["required_schemas"].append(ALT_TEXT)
    if effect:
        document["components"][0]["operations"][0]["outcomes"][0]["effects"] = [{
            "key": EFFECT, "bindings": {}, "config": {},
            "references": {"clear": {"kind": "place", "name": "scratch"}},
        }]
    seed = deepcopy(_simple_module("Seed").to_dict()["components"][0])
    seed["name"] = "seed"
    document["components"].insert(0, seed)
    document["entry"]["request"]["component"] = "seed"
    document["links"] = [{"source": {"component": "seed", "port": "result"},
                           "target": {"component": "step", "port": "request"}}]
    module = ModuleDeclaration.from_dict(document)
    if reset_lease:
        return module, registration
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    resources = {"step.asset": OwnerInput(ALT_TEXT, canonical_json("asset"), "Asset")}
    resources.update({"step.spare" + str(index): OwnerInput(ALT_TEXT,
        canonical_json("spare" + str(index)), "Spare") for index in range(extra_static_resources)})
    if not same_pool and variable_mode == "read":
        resources["step.other"] = OwnerInput(ALT_TEXT, canonical_json("other"), "Other")
    owner = start_run(module, registration, run_dir=path, task_input=task,
        entry_inputs={"request": task}, resource_inputs=resources,
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-static-lease-interactions",
        owner_statement="Offline static lease interaction test",
        command_id="test:interactions:fresh")
    # Initial entry tokens intentionally carry no variable claim colours.
    # A real predecessor firing creates the destination's declared claim set.
    seed_admission = owner.admit("seed.run", logical_tau=0, command_id="test:seed:admit")
    assert seed_admission is not None
    seed_execution = owner.start(seed_admission, command_id="test:seed:start")
    seed_outputs = owner.products(seed_execution, outcome_id="complete",
        products={"seed.result": (canonical_json("seeded"),)}, command_id="test:seed:products")
    owner.succeed(seed_outputs, command_id="test:seed:success")
    return owner


def _fresh_leases(marking):
    tokens = tuple(token for token in marking.tokens
        if token.state.lease_identity_ref is not None
        and token.state.consumed_by is None and token.state.epoch == marking.epoch)
    assert len({token.state.place for token in tokens}) == len(tokens)
    return {token.state.place: token for token in tokens}


def _settle(owner, admitted):
    execution = owner.start(admitted, command_id="test:interactions:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("done"),)},
        command_id="test:interactions:products")
    owner.succeed(outputs, command_id="test:interactions:success")
    return outputs


@pytest.mark.parametrize("static_read", [True, False], ids=["static-and-variable", "legacy-variable-only"])
@pytest.mark.parametrize("same_pool", [True, False], ids=["same-token-deduplicated", "different-pools"])
def test_static_and_variable_reads_share_exact_reference_union(tmp_path, same_pool, static_read):
    owner = _world(tmp_path / "reads", same_pool=same_pool, static_read=static_read)
    executable, structure, before = hydrate_module_runtime(owner._core)
    leases = _fresh_leases(before)
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:interactions:admit")
    assert admitted is not None
    exact = admitted.preflight.claimed_token_refs
    variable_place = structure.variable_resource_arc_for("step.run").lease_pool_place
    expected_references = {leases[variable_place].token_ref}
    if static_read:
        expected_references.add(leases["step.static"].token_ref)
    else:
        assert structure.lease_reference_arcs == ()
    assert len(exact) == len(set(exact)) == 1 + len(expected_references)
    assert expected_references <= set(exact)

    # Reconstructing live claims exercises the original registered-active path,
    # separately from the scheduler's initial read/consume split.
    local = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, local, executable.net_ref)
    consumed = local.claimed_tokens("step.run", local.epoch)
    operation = local.claimed_operation_tokens("step.run", local.epoch)
    assert [token.place for token in consumed] == [structure.variable_resource_arc_for("step.run").claim_token_place]
    assert len(operation) == len(exact)
    assert len(consumed[0].lease_claims) == 1
    assert consumed[0].lease_claims[0]["access_mode"] == "read"
    variable_place = structure.variable_resource_arc_for("step.run").lease_pool_place
    assert leases[variable_place].token_ref in exact
    assert all(token.consumed_by is None for token in operation
               if token.lease_identity_ref is not None)
    _settle(owner, admitted)
    _, _, after = hydrate_module_runtime(owner._core)
    assert {name: (token.token_ref, token.state) for name, token in _fresh_leases(after).items()} == {
        name: (token.token_ref, token.state) for name, token in leases.items()}


@pytest.mark.parametrize("static_read", [True, False], ids=["static-and-variable", "legacy-variable-only"])
def test_static_read_does_not_change_variable_produce_consume_return(tmp_path, static_read):
    owner = _world(tmp_path / "produce", variable_mode="produce", static_read=static_read)
    executable, structure, before = hydrate_module_runtime(owner._core)
    leases = _fresh_leases(before)
    assert leases["step.variable"].state.resource_ref is None
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:interactions:admit")
    assert admitted is not None
    local = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, local, executable.net_ref)
    assert {token.place for token in local.claimed_tokens("step.run", local.epoch)} == {
        structure.variable_resource_arc_for("step.run").claim_token_place, "step.variable"}
    claim_carrier = next(token for token in local.claimed_tokens("step.run", local.epoch)
                         if token.place != "step.variable")
    assert len(claim_carrier.lease_claims) == 1
    assert claim_carrier.lease_claims[0]["access_mode"] == "produce"
    expected_claims = {claim_carrier.token_ref, leases["step.variable"].token_ref}
    if static_read:
        expected_claims.add(leases["step.static"].token_ref)
    else:
        assert structure.lease_reference_arcs == ()
    assert set(admitted.preflight.claimed_token_refs) == expected_claims
    assert {token.token_ref for token in local.claimed_operation_tokens("step.run", local.epoch)} == expected_claims
    outputs = _settle(owner, admitted)
    _, _, after = hydrate_module_runtime(owner._core)
    current = _fresh_leases(after)
    assert current["step.static"].token_ref == leases["step.static"].token_ref
    assert current["step.variable"].token_ref != leases["step.variable"].token_ref
    assert current["step.variable"].state.lease_identity_ref == leases["step.variable"].state.lease_identity_ref
    assert current["step.variable"].state.resource_ref == outputs.outputs[0].resource_ref
    assert leases["step.variable"].token_ref not in after.token_refs



def test_static_reference_survives_declared_reset_effect_settlement(tmp_path):
    owner = _world(tmp_path / "effect", effect=True)
    _, _, before = hydrate_module_runtime(owner._core)
    leases = _fresh_leases(before)
    scratch = next(token for token in before.tokens if token.state.place == "step.scratch")
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:interactions:admit")
    assert admitted is not None
    _settle(owner, admitted)
    _, _, after = hydrate_module_runtime(owner._core)
    assert {name: (token.token_ref, token.state) for name, token in _fresh_leases(after).items()} == {
        name: (token.token_ref, token.state) for name, token in leases.items()}
    assert scratch.token_ref not in after.token_refs
    assert after.previous_checkpoint_ref == before.checkpoint_ref


def test_static_read_does_not_authorize_reset_of_lease_pool(tmp_path):
    module, registration = _world(tmp_path / "unused", reset_lease=True)
    with pytest.raises(DeclarationError, match="Reset arc cannot retire reusable capacity or lease authority"):
        compile_module(module, registration)


def test_static_reference_still_blocks_immediate_graph_replacement(tmp_path):
    owner = lease_owner(tmp_path / "replacement")
    original_net = owner.snapshot()["net_ref"]
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:interactions:admit")
    assert admitted is not None
    plan = prepare_replacement(owner, _simple_module("Replacement"))
    result = apply_replacement(owner, plan, command_id="test:interactions:replace")
    assert result["status"] == "DRAINING"
    assert owner.snapshot()["net_ref"] == original_net
    with pytest.raises(RuntimeError, match="replacement is pending"):
        owner.record_owner_stop(idempotency_key="test:interactions:stop")


@pytest.mark.parametrize("static_read", [True, False], ids=["static-and-variable", "legacy-variable-only"])
def test_static_read_does_not_change_legal_variable_edit_consume_return(tmp_path, static_read):
    owner = _world(tmp_path / "edit", variable_mode="produce", with_edit=True, static_read=static_read)
    produce = owner.admit("step.run", logical_tau=1, command_id="test:interactions:admit")
    assert produce is not None
    products = _settle(owner, produce)
    executable, structure, before = hydrate_module_runtime(owner._core)
    leases = _fresh_leases(before)
    document = next(token for token in before.tokens if token.state.place == "step.result")
    assert len(document.state.lease_claims) == 1
    assert document.state.lease_claims[0].access_mode == "edit"
    assert document.state.lease_claims[0].lease_identity_ref == leases["step.variable"].state.lease_identity_ref
    assert leases["step.variable"].state.resource_ref == products.outputs[0].resource_ref
    admitted = owner.admit("step.edit", logical_tau=2, command_id="test:edit:admit")
    assert admitted is not None
    local = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, local, executable.net_ref)
    assert {token.place for token in local.claimed_tokens("step.edit", local.epoch)} == {
        "step.result", "step.variable"}
    expected_claims = {document.token_ref, leases["step.variable"].token_ref}
    if static_read:
        expected_claims.add(leases["step.static"].token_ref)
    else:
        assert structure.lease_reference_arcs == ()
    assert set(admitted.preflight.claimed_token_refs) == expected_claims
    assert {token.token_ref for token in local.claimed_operation_tokens("step.edit", local.epoch)} == expected_claims
    execution = owner.start(admitted, command_id="test:edit:start")
    outputs = owner.products(execution, outcome_id="complete", products={},
        command_id="test:edit:products")
    owner.succeed(outputs, command_id="test:edit:success")
    _, _, after = hydrate_module_runtime(owner._core)
    current = _fresh_leases(after)
    assert current["step.static"].token_ref == leases["step.static"].token_ref
    assert current["step.variable"].token_ref != leases["step.variable"].token_ref
    assert current["step.variable"].state.lease_identity_ref == leases["step.variable"].state.lease_identity_ref
    assert current["step.variable"].state.resource_ref == leases["step.variable"].state.resource_ref
    assert leases["step.variable"].token_ref not in after.token_refs


@pytest.mark.parametrize("channel", ["data", "control"])
def test_ordinary_read_without_any_lease_arc_keeps_consume_return(tmp_path, channel):
    registration = _registration()
    document = _simple_module("OrdinaryRead").to_dict()
    document["components"][0]["ports"][0]["channel"] = channel
    document["components"][0]["config"] = {
        "input_modes": {"request": "read"}, "capacities": {"request": 1}}
    module = ModuleDeclaration.from_dict(document)
    task = OwnerInput(TEXT, canonical_json("ordinary input"), "Task")
    owner = start_run(module, registration, run_dir=tmp_path / channel,
        task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-ordinary-read-regression",
        owner_statement="Offline ordinary data/control read regression",
        command_id="test:ordinary:fresh")
    executable, structure, before = hydrate_module_runtime(owner._core)
    assert structure.lease_reference_arcs == ()
    assert structure.variable_resource_arcs == ()
    assert structure.resource_lease_pool_bindings == ()
    original = next(token for token in before.tokens if token.state.place == "step.request")
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:ordinary:admit")
    assert admitted is not None
    assert admitted.preflight.claimed_token_refs == (original.token_ref,)
    local = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, local, executable.net_ref)
    consumed = local.claimed_tokens("step.run", local.epoch)
    assert len(consumed) == 1
    assert consumed[0].token_ref == original.token_ref
    assert consumed[0].consumed_by == "step.run"
    assert local.claimed_operation_tokens("step.run", local.epoch) == consumed
    projection = next(place for place in admitted.preflight.outcomes[0].places
                      if place.place == "step.request")
    assert (projection.consumed_count, projection.returned_count) == (1, 1)
    _settle(owner, admitted)
    _, _, after = hydrate_module_runtime(owner._core)
    returned = tuple(token for token in after.tokens if token.state.place == "step.request")
    assert len(returned) == 1
    assert returned[0].token_ref != original.token_ref
    assert returned[0].state.resource_ref == original.state.resource_ref
    assert returned[0].state.consumed_by is None
    assert returned[0].state.lease_identity_ref is None
    assert returned[0].state.lease_claims == ()
    assert original.token_ref not in after.token_refs


def test_removing_static_arc_adopts_after_drain_and_maps_lease_authority(tmp_path):
    owner = _world(tmp_path / "remove-static", same_pool=False)
    executable, structure, before = hydrate_module_runtime(owner._core)
    original_leases = _fresh_leases(before)
    original_lower = owner.registration.resolve("component", "interactions")

    def without_static(config, context):
        fragment = original_lower(config, context)
        return replace(fragment, arcs=tuple(arc for arc in fragment.arcs
            if not (arc.place == "static" and arc.direction == "input" and arc.mode == "read")))

    owner.registration.register_component("interactions_without_static", without_static,
        identity={"implementation_id": "test.static_lease_interactions_without_static", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = structure.compiled.source.to_dict()
    next(component for component in document["components"]
         if component["name"] == "step")["key"] = "interactions_without_static"
    candidate = ModuleDeclaration.from_dict(document)
    candidate_compiled = compile_module(candidate, owner.registration)
    assert candidate_compiled.symbolic.places == structure.compiled.symbolic.places
    assert candidate_compiled.symbolic.lease_pools == structure.compiled.symbolic.lease_pools
    assert candidate_compiled.symbolic.lease_identities == structure.compiled.symbolic.lease_identities
    assert tuple(arc for arc in structure.compiled.symbolic.arcs
        if not (arc.place == "step.static" and arc.direction == "input" and arc.mode == "read")) == candidate_compiled.symbolic.arcs

    admitted = owner.admit("step.run", logical_tau=1, command_id="test:interactions:admit")
    assert admitted is not None
    assert original_leases["step.static"].token_ref in admitted.preflight.claimed_token_refs
    plan = prepare_replacement(owner, candidate)
    result = apply_replacement(owner, plan, command_id="test:remove-static:replace")
    assert result["status"] == "DRAINING"
    assert hydrate_module_runtime(owner._core)[0].net_ref == executable.net_ref
    _settle(owner, admitted)

    current_executable, current_structure, after = hydrate_module_runtime(owner._core)
    assert current_executable.net_ref != executable.net_ref
    assert current_structure.lease_reference_arcs == ()
    assert current_structure.variable_resource_arcs == structure.variable_resource_arcs
    current_leases = _fresh_leases(after)
    assert set(current_leases) == set(original_leases)
    result = apply_replacement(owner, plan, command_id="test:remove-static:replace")
    assert result["status"] == "ADOPTED"
    assert result["ordinary_retirements"] == []
    assert result["owner_input_mappings"] == []
    from cpn.rpnh.registry.publication import _version_from_payload
    mappings = {_version_from_payload(mapping["old_token_ref"]):
                _version_from_payload(mapping["new_token_ref"])
                for mapping in result["token_mappings"]}
    for place, original in original_leases.items():
        current = current_leases[place]
        assert mappings[original.token_ref] == current.token_ref
        assert current.token_ref != original.token_ref
        assert current.state.resource_ref == original.state.resource_ref
        assert current.state.lease_identity_ref == original.state.lease_identity_ref
        assert current.state.consumed_by is None



def _direct_step_claim(owner):
    """Select a legal exact union without invoking the scheduler's preference."""
    from cpn.rpnh.registry.invocations import FiringClaim
    from cpn.rpnh.registry.publication import _version_from_payload
    executable, structure, marking = hydrate_module_runtime(owner._core)
    transition = next(item for item in executable.transitions if item.transition_id == "step.run")
    root = owner._core.get_version(executable.team_design_root_ref.version_id).metadata
    round_ref = _version_from_payload(root["task_round_ref"])
    round_data = owner._core.get_version(round_ref.version_id).metadata
    net = owner._core.get_version(executable.net_ref.version_id).metadata
    refs = tuple(sorted(marking.token_refs,
        key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    consumed_places = {place for place, target, _ in structure.token_input_arcs if target == "step.run"}
    consumed = tuple(ref for ref in refs if any(token.token_ref == ref
        and token.state.place in consumed_places for token in marking.tokens))
    return FiringClaim(task_ref=_version_from_payload(root["task_ref"]),
        task_branch_ref=_version_from_payload(round_data["task_branch_ref"]), task_round_ref=round_ref,
        net_instance_ref=executable.net_ref, plan_ref=_version_from_payload(net["plan_ref"]),
        node_ref=transition.node_ref, operation_binding_ref=transition.operation_binding_ref,
        marking_checkpoint_ref=marking.checkpoint_ref, principal_ref=transition.principal_ref,
        logical_tau=1, attempt_index=1, claimed_input_refs=refs, consumed_input_refs=consumed,
        activation_ref=transition.activation_ref, agent_ref=transition.agent_ref)


def test_static_read_can_select_another_token_beside_same_pool_variable_read(tmp_path):
    from cpn.rpnh.firing_preflight import preflight_module_firing
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    from cpn.rpnh.registry.module_gateway import start_module_firing
    owner = _world(tmp_path / "explicit-union", extra_static_resources=1)
    executable, structure, before = hydrate_module_runtime(owner._core)
    leases = tuple(token for token in before.tokens if token.state.place == "step.static")
    carrier = next(token for token in before.tokens if token.state.lease_claims)
    assert len(leases) == 2 and len(carrier.state.lease_claims) == 1
    assert carrier.state.lease_claims[0].access_mode == "read"
    variable_identity = carrier.state.lease_claims[0].lease_identity_ref
    assert sum(token.state.lease_identity_ref == variable_identity for token in leases) == 1
    claim = _direct_step_claim(owner)
    assert set(claim.claimed_input_refs) == {carrier.token_ref, *(token.token_ref for token in leases)}
    assert claim.consumed_input_refs == (carrier.token_ref,)
    preflight = preflight_module_firing(structure, before, transition_id="step.run",
        claimed_token_refs=claim.claimed_input_refs)
    admitted = InvocationLifecycle(owner._core).admit_firing(claim,
        idempotency_key="test:explicit-union:admit")
    local = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, local, executable.net_ref)
    assert [token.token_ref for token in local.claimed_tokens("step.run", local.epoch)] == [carrier.token_ref]
    assert {token.token_ref for token in local.claimed_operation_tokens("step.run", local.epoch)} == set(claim.claimed_input_refs)
    kernel, repository = owner.operation_repository()
    execution = start_module_firing(owner._core, kernel, repository,
        admitted.context.invocation_ref, preflight=preflight,
        idempotency_key="test:explicit-union:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("done"),)}, command_id="test:explicit-union:products")
    owner.succeed(outputs, command_id="test:explicit-union:success")
    _, _, after = hydrate_module_runtime(owner._core)
    current = tuple(token for token in after.tokens if token.state.place == "step.static")
    assert {(token.token_ref, token.state) for token in current} == {
        (token.token_ref, token.state) for token in leases}


def test_static_weight_one_rejects_two_unrelated_same_pool_references(tmp_path):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.invocations import InvocationAdmissionError, InvocationLifecycle
    owner = _world(tmp_path / "oversized-union", extra_static_resources=2)
    _, _, before = hydrate_module_runtime(owner._core)
    leases = tuple(token for token in before.tokens if token.state.place == "step.static")
    carrier = next(token for token in before.tokens if token.state.lease_claims)
    assert len(leases) == 3 and len(carrier.state.lease_claims) == 1
    assert carrier.state.lease_claims[0].access_mode == "read"
    claim = _direct_step_claim(owner)
    assert len(claim.claimed_input_refs) == 4
    assert claim.consumed_input_refs == (carrier.token_ref,)
    event_count = len(owner._core.event_store.list_events())
    firing_count = len(owner._core.event_store.object_rows_by_type("transition_firing/v1"))
    with pytest.raises((InvocationAdmissionError, RegistryConflict)):
        InvocationLifecycle(owner._core).admit_firing(claim,
            idempotency_key="test:oversized-union:admit")
    assert len(owner._core.event_store.list_events()) == event_count
    assert len(owner._core.event_store.object_rows_by_type("transition_firing/v1")) == firing_count
    assert hydrate_module_runtime(owner._core)[2].checkpoint_ref == before.checkpoint_ref
