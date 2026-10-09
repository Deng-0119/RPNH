"""Finite exact PN/production projection distinctions; no executor is invoked."""
from dataclasses import fields, replace

import pytest

from cpn.components.basic import lower_operation
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (ArcDeclaration, CountGuard, InputVerdictGuard,
    OutcomeDeclaration, PlaceDeclaration, ProductDeclaration, TransitionDeclaration,
    LeaseIdentityDeclaration, ResourceLeasePoolBinding)
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import (ExecutableNetAuthority, PetriTokenState,
    RegistryHead, ResourceVersionRef, TypedMarkingSnapshot)
from cpn.rpnh.runtime_net import RuntimeNet
from cpn.rpnh.pn_validation.contracts import (AnalysisPolicy, AnalysisState, EnvironmentContract,
    OperationCase, OperationModel, ProducedSpec, SchedulerContract, TerminalContract, canonical_json)
from cpn.rpnh.pn_validation.projection import build_analysis_input, restore_base, restore_state
from cpn.rpnh.pn_validation.semantics import (check_supported_semantics, enabled_bindings,
                                           successors, check_state_safety)
from test_static_lease_reads import _registration, _simple_module, CONFIG_SCHEMA_ID, TEXT


def ref(kind, number):
    return VersionRef(kind + "/v1", TypedId(kind, f"{number:032x}"),
                      TypedId(kind + "_version", f"{number + 10000:032x}"))


def token(number, place="step.request", **changes):
    base = PetriTokenState(ref("petri_token", number), number, place, 0, None, None,
                          None, None, None, None, None, None, None)
    return replace(base, **changes)


def make_input(*, outcomes=("complete",), retry=False, occurrences=1, ordinary_read=False,
               colours=(), guard=None, output_weight=1, count_scope=None, lease=False,
               policy=None, fragment_transform=None, output_channel="control"):
    """Compiled fixture + explicitly synthetic executable DTO; never Registry authority."""
    registration = _registration()
    # Fail immediately if a production path tries to execute the registered body.
    def forbidden_executor(**kwargs):
        raise AssertionError("finite analysis must never call an executor")
    registration._callables["executor"] = {key: forbidden_executor for key in registration._callables["executor"]}
    document = _simple_module("FiniteAnalysis").to_dict()
    component = document["components"][0]
    component["key"] = "finite_analysis"
    for port in component["ports"]:
        port["channel"] = output_channel if port["direction"] == "output" else "control"
    if ordinary_read:
        component["config"] = {"input_modes": {"request": "read"}}
    outcome_ports = {name: ("failure" if name == "failure" else "retry_output" if retry and name == "retry" else "result") for name in outcomes}
    for port in sorted(set(outcome_ports.values()) - {"result"}):
        component["ports"].append({"name": port, "direction": "output", "schema": TEXT, "channel": "control"})
        component["operations"][0]["outputs"].append(port)
    component["operations"][0]["outcomes"] = [
        {"name": name, "products": [{"port": outcome_ports[name]}]} for name in outcomes]
    document["terminal"]["outcome"] = outcomes[0]

    def lower(config, context):
        fragment = lower_operation(config, context)
        places = tuple(replace(p, colours=colours) if p.name == "request" else p for p in fragment.places)
        arcs = tuple(replace(a, weight=output_weight) if a.direction == "output" else a for a in fragment.arcs)
        transitions = fragment.transitions
        if guard is not None:
            transitions = tuple(replace(t, input_verdicts=(InputVerdictGuard("request", guard),)) for t in transitions)
        if retry:
            places = tuple(p for p in places if p.name != "retry_output")
            arcs = tuple(replace(a, place="request") if a.place == "retry_output" else a for a in arcs)
            fragment = replace(fragment, ports=tuple(replace(p, place="request") if p.place == "retry_output" else p for p in fragment.ports))
        if count_scope:
            transitions = (replace(transitions[0], count_guards=(CountGuard("request", "ge", 2, count_scope),)),
                           TransitionDeclaration("other", "run"))
            arcs = (*arcs, *(replace(a, transition="other") for a in arcs))
        if lease:
            places = (*places, PlaceDeclaration("lease", TEXT, token_kind="resource_lease", capacity=1, reusable=True))
            arcs = (*arcs, ArcDeclaration("lease", "run", "input", mode="read"))
            fragment = replace(fragment, lease_identities=(LeaseIdentityDeclaration("asset"),),
                               lease_pools=(ResourceLeasePoolBinding("lease", "lease", ("asset",)),))
        fragment = replace(fragment, places=places, transitions=transitions, arcs=arcs)
        return fragment_transform(fragment) if fragment_transform else fragment

    registration.register_component("finite_analysis", lower,
        identity={"implementation_id": "test.finite_analysis", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    compiled = compile_module(ModuleDeclaration.from_dict(document), registration)
    net_ref = ref("net_instance", 100)
    # The pure production projector reads net_ref only. This is a test DTO,
    # never claimed to be a verified or published executable.
    executable = ExecutableNetAuthority(net_ref, ref("team_design_root", 101), None, None, (),
                                        RegistryHead(0, 0, 0, {}))
    tokens = tuple(token(i + 1, verdict=colours[i % len(colours)] if colours else None)
                   for i in range(occurrences))
    if count_scope:
        tokens = (tokens[0], token(2, consumer="step.other"))
    plan = None
    if lease:
        from cpn.rpnh.registry.module_resources import ModuleResourcePlan, ResolvedExactRef, ResolvedLeasePool
        resource = ResourceVersionRef(TypedId("resource", "a" * 32), TypedId("resource_version", "b" * 32))
        tokens = (*tokens, token(len(tokens) + 1, "step.lease", resource_ref=resource,
                                 lease_identity_ref=resource.as_version_ref()))
        exact = ResolvedExactRef.from_version_ref(resource.as_version_ref())
        plan = ModuleResourcePlan({"step.asset": resource.as_version_ref()}, {}, (),
                                 (ResolvedLeasePool("step.lease", "step.lease", (exact,), ()),), (), ())
    snapshot = TypedMarkingSnapshot(0, len(tokens) + 1, (), tokens)
    cases = tuple(OperationCase(name, name, (ProducedSpec("step.request" if retry and name == "retry" else "step." + outcome_ports[name]),))
                  for name in outcomes)
    model = OperationModel("step.run", "v1", cases)
    return build_analysis_input(compiled, snapshot, plan, (model,),
        TerminalContract(success_places=("step.result",), permitted_failure_places=(("step.failure",) if "failure" in outcomes else ())), EnvironmentContract(), SchedulerContract(),
        policy or AnalysisPolicy(), executable=executable,
        source_kind="synthetic-offline-fixture")


def starts(analysis, state=None):
    return tuple(b for b in enabled_bindings(analysis, state or analysis.state).bindings if b.kind == "START")


def start(analysis, state=None, index=0):
    state = state or analysis.state
    return successors(analysis, state, starts(analysis, state)[index])[0].state


def settle(analysis, state, case="complete", index=0):
    bindings = tuple(b for b in enabled_bindings(analysis, state).bindings
                     if b.kind == "SETTLE" and b.case_id == case)
    return successors(analysis, state, bindings[index])[0]


def production_settle(analysis, active_state, binding):
    """Independent orchestration of the same exact production claim/settle API."""
    marking = restore_base(RuntimeNet(analysis.compiled, net_ref=analysis.net_ref,
                                     resource_plan=analysis.resource_plan), active_state.base)
    for occurrence in active_state.active:
        marking._next_claim_id = occurrence.claim_id
        marking._install_exact_registered_claim_locked(occurrence.transition_id,
            (*occurrence.consumed_refs, *occurrence.reference_refs), verify_enabled=False, allow_siblings=True)
        marking.bind_active_claim(occurrence.claim_id, occurrence.firing_ref)
    marking._next_claim_id = active_state.next_claim_id
    occurrence = next(a for a in active_state.active if a.firing_ref == binding.firing_ref)
    case = next(c for c in analysis.operation_models[0].cases if c.case_id == binding.case_id)
    view = marking.singleton_settlement_view(occurrence.firing_ref)
    op_tokens = view.claimed_operation_tokens(binding.transition_id, occurrence.claim_epoch,
                                             instance_key=occurrence.firing_ref)
    view._net = marking._net.for_outcome(binding.transition_id, case.outcome_id).for_claimed_inputs(
        binding.transition_id, tuple((t.place, t.verdict) for t in op_tokens))
    specs = [{**{f.name: getattr(p, f.name) for f in fields(p)}, "continuation": None} for p in case.produced]
    view.deposit_outputs(binding.transition_id, specs, claim_epoch=occurrence.claim_epoch)
    view.record_settled_attempt(binding.transition_id, occurrence.attempt_index)
    snapshot = view.pure_typed_snapshot(analysis.executable)
    successor = restore_base(marking._net, snapshot)
    marking.carry_active_claims_to(successor, settled_instance_key=occurrence.firing_ref)
    return snapshot, successor


def test_one_step_weighted_production_projection():
    analysis = make_input(output_weight=2)
    active = start(analysis)
    action = next(b for b in enabled_bindings(analysis, active).bindings if b.kind == "SETTLE")
    result = successors(analysis, active, action)[0]
    expected, _ = production_settle(analysis, active, action)
    assert not result.safety_violations and result.state.base == expected
    assert [t.place for t in expected.tokens] == ["step.result", "step.result"]


@pytest.mark.parametrize("guard,index", [(True, 0), ("true", 1), ("True", 2)])
def test_bool_string_guards_keep_exact_identity(guard, index):
    analysis = make_input(occurrences=3, colours=(True, "true", "True"), guard=guard)
    actions = starts(analysis)
    assert len(actions) == 1 and actions[0].token_refs == (analysis.state.base.tokens[index].token_ref,)
    local = restore_state(analysis, analysis.state)
    local._install_exact_registered_claim_locked("step.run", actions[0].token_refs, verify_enabled=True, allow_siblings=True)
    assert local.claimed_token_refs("step.run", 0) == actions[0].token_refs
    assert canonical_json(True) != canonical_json("true") != canonical_json("True")
    for value in ({1: "bad"}, {"v": float("nan")}):
        with pytest.raises((TypeError, ValueError)):
            canonical_json(value)


def test_same_transition_siblings_settle_out_of_order():
    analysis = make_input(occurrences=2)
    active = start(analysis, start(analysis))
    assert len(active.active) == 2 and active.active[0].attempt_index == 1 and active.active[1].attempt_index == 2
    binding = next(b for b in enabled_bindings(analysis, active).bindings
                   if b.firing_ref == active.active[1].firing_ref)
    result = successors(analysis, active, binding)[0]
    expected, expected_local = production_settle(analysis, active, binding)
    assert result.state.base == expected
    assert result.state.active == (active.active[0],)
    assert restore_state(analysis, result.state)._tokens == expected_local._tokens
    assert settle(analysis, result.state).state.base.attempts[0].highest_issued == 2


def test_ordinary_read_returns_new_occurrence_static_lease_preserves_reference():
    analysis = make_input(ordinary_read=True, lease=True)
    active = start(analysis)
    occurrence = active.active[0]
    request, lease = analysis.state.base.tokens
    assert occurrence.consumed_refs == (request.token_ref,)
    assert occurrence.reference_refs == (lease.token_ref,)
    assert occurrence.lease_accesses == ((lease.lease_identity_ref, "read"),)
    result = settle(analysis, active)
    assert not result.safety_violations
    after = {t.place: t for t in result.state.base.tokens}
    assert after["step.request"].token_ref != request.token_ref
    assert after["step.lease"] == lease
    binding = next(b for b in enabled_bindings(analysis, active).bindings if b.kind == "SETTLE")
    assert result.state.base == production_settle(analysis, active, binding)[0]


def test_reader_and_all_count_scope_differ():
    reader, all_count = make_input(count_scope="reader"), make_input(count_scope="all")
    assert not any(b.transition_id == "step.run" for b in starts(reader))
    assert any(b.transition_id == "step.run" for b in starts(all_count))
    for analysis in (reader, all_count):
        local = restore_base(RuntimeNet(analysis.compiled, net_ref=analysis.net_ref), analysis.state.base)
        assert local.is_enabled("step.run") == any(b.transition_id == "step.run" for b in starts(analysis))


def test_missing_model_and_binding_budget_are_unknown():
    analysis = make_input(occurrences=2, policy=AnalysisPolicy(max_bindings=1))
    bindings = enabled_bindings(analysis, analysis.state)
    assert not bindings.complete and bindings.reasons == ("max_bindings",) and len(bindings.bindings) == 1
    missing = replace(analysis, operation_models=())
    assert not check_supported_semantics(missing).supported
    assert not enabled_bindings(missing, missing.state).complete


def test_invalid_output_is_concrete_violation_not_missing_edge():
    analysis = make_input()
    bad = replace(analysis.operation_models[0], cases=(OperationCase("complete", "complete", (ProducedSpec("step.request"),)),))
    analysis = replace(analysis, operation_models=(bad,))
    result = settle(analysis, start(analysis))
    assert result.state is None and result.safety_violations and result.candidate_json


def test_selected_outcome_differs_from_structural_maximum():
    def transform(fragment):
        return replace(fragment, arcs=tuple(replace(a, weight=2) if a.direction == "output" and a.outcome == "alternate" else a for a in fragment.arcs))
    analysis = make_input(outcomes=("complete", "alternate"), fragment_transform=transform)
    assert RuntimeNet(analysis.compiled, net_ref=analysis.net_ref).output_arc_weight("step.run", "step.result") == 2
    active = start(analysis)
    for case, count in (("complete", 1), ("alternate", 2)):
        result = settle(analysis, active, case)
        assert result.state is not None and len(result.state.base.tokens) == count
        binding = next(b for b in enabled_bindings(analysis, active).bindings if b.case_id == case)
        assert result.state.base == production_settle(analysis, active, binding)[0]
    incomplete = replace(analysis, operation_models=(replace(analysis.operation_models[0], cases=analysis.operation_models[0].cases[:1]),))
    assert not check_supported_semantics(incomplete).supported


def test_capacity_violation_preserves_invalid_successor_evidence():
    def transform(fragment):
        return replace(fragment, places=tuple(replace(p, capacity=1) if p.name == "result" else p for p in fragment.places))
    analysis = make_input(occurrences=2, fragment_transform=transform)
    active = start(analysis, start(analysis))
    first = settle(analysis, active).state
    result = settle(analysis, first)
    assert result.state is None and any("capacity_exceeded" in s for s in result.safety_violations)
    assert result.candidate_json and len(result.action.after_refs) == 2


def test_projection_copies_mutable_context_and_resource_plan():
    from cpn.rpnh.registry.module_resources import ModuleResourcePlan
    analysis = make_input()
    slots = {}
    plan = ModuleResourcePlan({}, slots, (), (), (), ())
    context = {"mapping": ["before"]}
    rebuilt = build_analysis_input(analysis.compiled, analysis.state.base, plan, analysis.operation_models,
        analysis.terminal_contract, analysis.environment_contract, analysis.scheduler_contract,
        analysis.policy, executable=analysis.executable, owner_context=context)
    before = canonical_json(rebuilt)
    slots["changed"] = ref("logical_slot", 81)
    context["mapping"].append("after")
    assert canonical_json(rebuilt) == before
    with pytest.raises(TypeError):
        rebuilt.resource_plan.slot_refs["changed"] = ref("logical_slot", 82)


def test_unmodeled_data_product_is_unknown_not_a_contentless_output():
    analysis = make_input(output_channel="data")
    assert analysis.operation_models[0].cases[0].produced[0].resource_ref is None
    support = check_supported_semantics(analysis)
    assert not support.supported
    assert "unmodeled_data_product:step.run:step.result" in support.reasons
    enumeration = enabled_bindings(analysis, analysis.state)
    assert not enumeration.complete and enumeration.bindings == ()
    # Registered owner input resources do not turn a control result into data.
    control = make_input()
    resource = ResourceVersionRef(TypedId("resource", "c" * 32), TypedId("resource_version", "d" * 32))
    source = replace(control.state.base.tokens[0], resource_ref=resource)
    control = replace(control, state=replace(control.state, base=replace(control.state.base, tokens=(source,))))
    assert check_supported_semantics(control).supported
    assert settle(control, start(control)).state is not None


@pytest.mark.parametrize("normal_root", [False, True])
def test_proposed_net_ref_matches_exact_allocation_without_executable(normal_root):
    from cpn.rpnh.registry.normal_root_token_allocation import NORMAL_ROOT_TOKEN_SCHEME
    registered_scope = make_input()
    if normal_root:
        registered_scope = replace(registered_scope, ordinary_token_ref_scheme=NORMAL_ROOT_TOKEN_SCHEME)
    proposed = build_analysis_input(registered_scope.compiled, registered_scope.state.base,
        registered_scope.resource_plan, registered_scope.operation_models,
        registered_scope.terminal_contract, registered_scope.environment_contract,
        registered_scope.scheduler_contract, registered_scope.policy,
        net_ref=registered_scope.net_ref,
        ordinary_token_ref_scheme=registered_scope.ordinary_token_ref_scheme)
    assert proposed.executable is None and check_supported_semantics(proposed).supported
    actual = settle(registered_scope, start(registered_scope))
    analytical = settle(proposed, start(proposed))
    assert actual.state is not None and analytical == actual
    assert all(t.token_ref is not None for t in analytical.state.base.tokens)
