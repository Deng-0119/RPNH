"""Exact selection may bypass a dead first carrier without changing scheduling.

All carriers are published by real Registry firings. The default selector is
intentionally left unchanged: this is exact-ref validity, not binding liveness.
"""
from dataclasses import replace

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, InitialTokenDeclaration, LeaseClaimExpression,
    LeaseIdentityDeclaration, OutcomeDeclaration, PlaceDeclaration,
    PortBinding, PortDeclaration, ProductDeclaration, ResourceLeasePoolBinding,
    TransitionDeclaration, VariableResourceArc,
)
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.invocations import FiringClaim, InvocationAdmissionError, InvocationLifecycle
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_execution import install_active_module_claims
from cpn.rpnh.registry.module_gateway import start_module_firing
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh.firing_preflight import preflight_module_firing
from test_static_lease_reads import ALT_TEXT, TEXT, _registration, _simple_module


def _owner_with_dead_then_live_carrier(path):
    registration = _registration()

    def lower(config, context):
        fragment = lower_operation(config, context)
        template = fragment.operations[0]
        places = (*fragment.places,
            PlaceDeclaration("carrier", TEXT), PlaceDeclaration("used", TEXT),
            PlaceDeclaration("bad_kick", TEXT, channel="control",
                initial_tokens=(InitialTokenDeclaration(),)),
            PlaceDeclaration("good_kick", TEXT, channel="control",
                initial_tokens=(InitialTokenDeclaration(),)),
            PlaceDeclaration("pool_a", ALT_TEXT, token_kind="resource_lease", capacity=1, reusable=True),
            PlaceDeclaration("pool_b", ALT_TEXT, token_kind="resource_lease", capacity=1, reusable=True))
        operations = tuple(replace(template, name=name, inputs=inputs,
            outputs=(output,), request_port=None,
            outcomes=(OutcomeDeclaration("complete", (ProductDeclaration(output),)),))
            for name, inputs, output in (("bad", (), "bad_out"),
                ("good", (), "good_out"), ("use", ("use_in",), "use_out")))
        arcs = list(fragment.arcs)
        for name, source in (("bad", "pool_b"), ("good", "pool_a")):
            arcs.extend((ArcDeclaration(name + "_kick", name, "input"),
                ArcDeclaration(source, name, "input", mode="read"),
                ArcDeclaration("carrier", name, "output", mode="produce", outcome="complete",
                    lease_claims=(LeaseClaimExpression("asset_a", expected_resource_source=source, access_mode="read"),))))
        arcs.extend((ArcDeclaration("carrier", "use", "input"),
            ArcDeclaration("pool_a", "use", "input", mode="read"),
            ArcDeclaration("used", "use", "output", mode="produce", outcome="complete")))
        return replace(fragment, places=places, arcs=tuple(arcs),
            transitions=(*fragment.transitions,
                *(TransitionDeclaration(name, name) for name in ("bad", "good", "use"))),
            operations=(*fragment.operations, *operations),
            internal_ports=(PortDeclaration("bad_out", "output", TEXT),
                PortDeclaration("good_out", "output", TEXT),
                PortDeclaration("use_in", "input", TEXT),
                PortDeclaration("use_out", "output", TEXT)),
            internal_bindings=(PortBinding("bad_out", "carrier"),
                PortBinding("good_out", "carrier"), PortBinding("use_in", "carrier"),
                PortBinding("use_out", "used")),
            lease_identities=(LeaseIdentityDeclaration("asset_a"), LeaseIdentityDeclaration("asset_b")),
            lease_pools=(ResourceLeasePoolBinding("pool_a", "pool_a", ("asset_a",)),
                ResourceLeasePoolBinding("pool_b", "pool_b", ("asset_b",))),
            variable_resource_arcs=(VariableResourceArc("use", "carrier", "pool_a"),))

    registration.register_component("exact_selection", lower,
        identity={"implementation_id": "test.static_lease_exact_selection", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("ExactSelection").to_dict()
    document["components"][0]["key"] = "exact_selection"
    document["required_schemas"].append(ALT_TEXT)
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(ModuleDeclaration.from_dict(document), registration,
        run_dir=path, task_input=task, entry_inputs={"request": task},
        resource_inputs={"step.asset_a": OwnerInput(ALT_TEXT, canonical_json("A"), "A"),
                         "step.asset_b": OwnerInput(ALT_TEXT, canonical_json("B"), "B")},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-exact-selection",
        owner_statement="Offline exact selection regression", command_id="test:exact:fresh")
    for tau, name in enumerate(("bad", "good")):
        admitted = owner.admit("step." + name, logical_tau=tau, command_id="test:" + name + ":admit")
        assert admitted is not None
        execution = owner.start(admitted, command_id="test:" + name + ":start")
        outputs = owner.products(execution, outcome_id="complete",
            products={"step." + name + "_out": (canonical_json(name),)},
            command_id="test:" + name + ":products")
        owner.succeed(outputs, command_id="test:" + name + ":success")
    return owner


def _claim(owner, executable, marking, selected_refs, consumed_refs):
    transition = next(item for item in executable.transitions if item.transition_id == "step.use")
    root = owner._core.get_version(executable.team_design_root_ref.version_id).metadata
    round_ref = _version_from_payload(root["task_round_ref"])
    round_data = owner._core.get_version(round_ref.version_id).metadata
    net = owner._core.get_version(executable.net_ref.version_id).metadata
    refs = tuple(sorted(selected_refs,
        key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    return FiringClaim(task_ref=_version_from_payload(root["task_ref"]),
        task_branch_ref=_version_from_payload(round_data["task_branch_ref"]), task_round_ref=round_ref,
        net_instance_ref=executable.net_ref, plan_ref=_version_from_payload(net["plan_ref"]),
        node_ref=transition.node_ref, operation_binding_ref=transition.operation_binding_ref,
        marking_checkpoint_ref=marking.checkpoint_ref, principal_ref=transition.principal_ref,
        logical_tau=2, attempt_index=1, claimed_input_refs=refs,
        consumed_input_refs=consumed_refs, activation_ref=transition.activation_ref, agent_ref=transition.agent_ref)


def test_exact_valid_later_carrier_is_not_rejected_by_dead_default_binding(tmp_path):
    owner = _owner_with_dead_then_live_carrier(tmp_path / "exact")
    executable, structure, before = hydrate_module_runtime(owner._core)
    carriers = sorted((token for token in before.tokens if token.state.place == "step.carrier"),
                      key=lambda token: token.state.token_id)
    assert len(carriers) == 2
    bad, good = carriers
    pool_a = next(token for token in before.tokens if token.state.place == "step.pool_a")
    pool_b = next(token for token in before.tokens if token.state.place == "step.pool_b")
    assert bad.state.producer == "step.bad" and good.state.producer == "step.good"
    assert len(bad.state.lease_claims) == len(good.state.lease_claims) == 1
    assert bad.state.lease_claims[0].lease_identity_ref == pool_a.state.lease_identity_ref
    assert good.state.lease_claims[0].lease_identity_ref == pool_a.state.lease_identity_ref
    assert bad.state.lease_claims[0].expected_resource_ref == pool_b.state.resource_ref
    assert good.state.lease_claims[0].expected_resource_ref == pool_a.state.resource_ref
    assert pool_a.state.resource_ref != pool_b.state.resource_ref
    assert bad.state.lease_claims[0].access_mode == good.state.lease_claims[0].access_mode == "read"

    local = TeamNetMarking.from_authority(structure, before)
    # Preserve the existing default first-binding behavior; it need not search
    # past the dead carrier. The explicit exact selection must stand on its own.
    assert local.is_enabled("step.use") is False
    assert owner.admit("step.use", logical_tau=2, command_id="test:default:admit") is None
    assert local._try_reserve("step.use", set(), allowed_token_ids={
        bad.state.token_id, pool_a.state.token_id}) is None
    selected_ids = {good.state.token_id, pool_a.state.token_id}
    reserved = local._try_reserve("step.use", set(), allowed_token_ids=selected_ids)
    assert reserved is not None
    assert reserved.token_ids == (good.state.token_id,)
    assert reserved.reference_token_ids == (pool_a.state.token_id,)

    # Exact selection must still reject a genuinely mismatched carrier.
    bad_claim = _claim(owner, executable, before,
        (bad.token_ref, pool_a.token_ref), (bad.token_ref,))
    event_count = len(owner._core.event_store.list_events())
    firing_count = len(owner._core.event_store.object_rows_by_type("transition_firing/v1"))
    with pytest.raises((RegistryConflict, InvocationAdmissionError)):
        InvocationLifecycle(owner._core).admit_firing(bad_claim,
            idempotency_key="test:exact-bad:admit")
    assert len(owner._core.event_store.list_events()) == event_count
    assert len(owner._core.event_store.object_rows_by_type("transition_firing/v1")) == firing_count
    assert hydrate_module_runtime(owner._core)[2].checkpoint_ref == before.checkpoint_ref

    claim = _claim(owner, executable, before, (good.token_ref, pool_a.token_ref), (good.token_ref,))
    admitted = InvocationLifecycle(owner._core).admit_firing(claim, idempotency_key="test:exact:admit")
    preflight = preflight_module_firing(structure, before, transition_id="step.use",
        claimed_token_refs=claim.claimed_input_refs)
    projected = TeamNetMarking.from_authority(structure, before)
    install_active_module_claims(owner._core, projected, executable.net_ref)
    assert [token.token_ref for token in projected.claimed_tokens("step.use", projected.epoch)] == [good.token_ref]
    assert {token.token_ref for token in projected.claimed_operation_tokens("step.use", projected.epoch)} == set(claim.claimed_input_refs)
    kernel, repository = owner.operation_repository()
    execution = start_module_firing(owner._core, kernel, repository,
        admitted.context.invocation_ref, preflight=preflight, idempotency_key="test:exact:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.use_out": (canonical_json("used good carrier"),)}, command_id="test:exact:products")
    owner.succeed(outputs, command_id="test:exact:success")
    _, _, after = hydrate_module_runtime(owner._core)
    remaining = {token.token_ref: token.state for token in after.tokens}
    assert good.token_ref not in remaining
    assert remaining[bad.token_ref] == bad.state
    assert remaining[pool_a.token_ref] == pool_a.state
    assert remaining[pool_b.token_ref] == pool_b.state
    assert sum(token.state.place == "step.used" for token in after.tokens) == 1
