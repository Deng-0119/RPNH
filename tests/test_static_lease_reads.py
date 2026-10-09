"""Static lease references through real Registry; no process/model transport."""
from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def no_external_transport(monkeypatch):
    import socket
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError("static lease verification is offline and cannot open sockets or retrieve URLs")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, InitialTokenDeclaration, LeaseIdentityDeclaration, PlaceDeclaration,
    ResourceLeasePoolBinding,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
TEXT = "application/net_operation_test_text/v1"
ALT_TEXT = "application/net_operation_test_alt_text/v1"
EXECUTOR = "test/net-operation-business/v1"
TERMINAL = "test/net-operation-terminal/v1"


def _schema(schema_id):
    return {"$id": schema_id, "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "string"}


def _registration():
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, _schema(TEXT))
    registration.register_schema(ALT_TEXT, _schema(ALT_TEXT))
    registration.register_executor(EXECUTOR, lambda **_kwargs: None,
        identity={"implementation_id": "test.net_operation_business", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.net_operation_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def _simple_module(name="Simple", *, input_schema=TEXT, output_schema=TEXT):
    binding = {"bucket_id": "work", "budget_scope": "module",
               "finalization_scope": None}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": name,
        "components": [{
            "name": "step", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": input_schema},
                {"name": "result", "direction": "output", "schema": output_schema},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "request_port": None, "tools": [], "config": {},
                "budget_binding": binding,
                "outcomes": [{"name": "complete",
                              "products": [{"port": "result"}]}],
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL,
                     "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": sorted({CONFIG_SCHEMA_ID, input_schema, output_schema}),
        "budgets": {},
        "budget_buckets": [{**binding, "max_attempts": 3}],
    })




def lease_owner(path, *, occurrences=1, pure_reference=False, ordinary_read=False,
                distinct_resource=False, control_read=False, lease_count=1, read_weight=1,
                command_id="test:static-lease:fresh"):
    registration = _registration()
    def lower(config, context):
        fragment = lower_operation(config, context)
        if occurrences != 1:
            fragment = replace(fragment, places=tuple(replace(place,
                initial_tokens=(InitialTokenDeclaration(count=occurrences - 1, value="work", schema=TEXT),))
                if place.name == "request" else place for place in fragment.places))
        return replace(fragment,
            places=(*fragment.places, PlaceDeclaration("lease", TEXT,
                token_kind="resource_lease", capacity=lease_count, reusable=True)),
            arcs=(*fragment.arcs, ArcDeclaration("lease", "run", "input", weight=read_weight, mode="read")),
            lease_identities=tuple(LeaseIdentityDeclaration("asset" + (str(i) if i else "")) for i in range(lease_count)),
            lease_pools=(ResourceLeasePoolBinding("lease", "lease", tuple("asset" + (str(i) if i else "") for i in range(lease_count))),))
    registration.register_component("lease_operation", lower,
        identity={"implementation_id": "test.static_lease", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("StaticLease").to_dict()
    document["components"][0]["key"] = "lease_operation"
    if ordinary_read:
        document["components"][0]["config"] = {"input_modes": {"request": "read"}}
    if control_read:
        document["components"][0]["ports"][0]["channel"] = "control"
    if pure_reference:
        document["entry"] = {}
    if pure_reference:
        document["components"][0]["ports"] = [port for port in document["components"][0]["ports"]
                                                if port["name"] != "request"]
        document["components"][0]["operations"][0]["inputs"] = []
    module = ModuleDeclaration.from_dict(document)
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    return start_run(module, registration, run_dir=path, task_input=task,
        entry_inputs={"request": task} if document["entry"] else {},
        resource_inputs={"step.asset" + (str(i) if i else ""):
            OwnerInput(TEXT, canonical_json("lease" + str(i)), "Lease") if distinct_resource or i else task
            for i in range(lease_count)},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-static-lease", owner_statement="Offline static lease test",
        command_id=command_id)


def test_static_lease_read_admits(tmp_path):
    owner = lease_owner(tmp_path / "run")
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:admit")
    assert admitted is not None


def current(owner):
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    return hydrate_module_runtime(owner._core)


def lease_state(marking):
    return next(token.state for token in marking.tokens if token.state.place == "step.lease")


def finish(owner, admitted, key):
    execution = owner.start(admitted, command_id=key + ":start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("result"),)}, command_id=key + ":products")
    return owner.succeed(outputs, command_id=key + ":success")


@pytest.mark.parametrize("distinct_resource", [False, True])
def test_static_read_full_lifecycle_preserves_exact_token(tmp_path, distinct_resource):
    owner = lease_owner(tmp_path / "run", distinct_resource=distinct_resource)
    initial = lease_state(current(owner)[2])
    admitted = owner.admit("step.run", logical_tau=0, command_id="test:admit")
    finish(owner, admitted, "first")
    assert lease_state(current(owner)[2]) == initial
    tokens = owner._core.event_store.object_rows_by_type("petri_token/v1")
    import json
    assert sum(json.loads(row["metadata_json"])["place"] == "step.lease" for row in tokens) == 1


def test_parallel_same_transition_and_successor_reconstruction(tmp_path):
    from cpn.rpnh.marking import TeamNetMarking
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    owner = lease_owner(tmp_path / "run", occurrences=2)
    initial = lease_state(current(owner)[2])
    first = owner.admit("step.run", logical_tau=0, command_id="first:admit")
    second = owner.admit("step.run", logical_tau=0, command_id="second:admit")
    assert first is not None and second is not None
    assert initial.token_ref in first.preflight.claimed_token_refs
    assert initial.token_ref in second.preflight.claimed_token_refs
    finish(owner, first, "first")
    executable, structure, marking = current(owner)
    local = TeamNetMarking.from_authority(structure, marking)
    assert len(install_active_module_claims(owner._core, local, executable.net_ref)) == 1
    assert lease_state(marking) == initial
    finish(owner, second, "second")
    assert lease_state(current(owner)[2]) == initial


def test_reference_only_transition_is_finite_and_retained(tmp_path):
    from cpn.rpnh.marking import TeamNetMarking
    owner = lease_owner(tmp_path / "run", pure_reference=True)
    _executable, structure, marking = current(owner)
    local = TeamNetMarking.from_authority(structure, marking)
    selected, epoch = local.claim_firing_set()
    assert selected == ["step.run"]
    assert local.claimed_tokens("step.run", epoch) == []
    assert local.claimed_token_refs("step.run", epoch) == (lease_state(marking).token_ref,)
    for index in range(2):
        admitted = owner.admit("step.run", logical_tau=index, command_id=f"admit:{index}")
        finish(owner, admitted, f"done:{index}")
    assert lease_state(current(owner)[2]) == lease_state(marking)


@pytest.mark.parametrize("control_read", [False, True])
def test_ordinary_read_still_consumes_and_returns(tmp_path, control_read):
    owner = lease_owner(tmp_path / "run", ordinary_read=True, control_read=control_read)
    before = current(owner)[2]
    original = next(token.state for token in before.tokens if token.state.place == "step.request")
    first = owner.admit("step.run", logical_tau=0, command_id="first:admit")
    assert owner.admit("step.run", logical_tau=0, command_id="blocked:admit") is None
    finish(owner, first, "first")
    after = current(owner)[2]
    returned = next(token.state for token in after.tokens if token.state.place == "step.request")
    assert returned.token_ref != original.token_ref and returned.resource_ref == original.resource_ref
    assert lease_state(before) == lease_state(after)


def direct_claim(owner):
    """Use the existing low-level claim API, bypassing the owner's selector."""
    from cpn.rpnh.registry.invocations import FiringClaim
    from cpn.rpnh.registry.publication import _version_from_payload
    executable, structure, marking = current(owner)
    transition = executable.transitions[0]
    root = owner._core.get_version(executable.team_design_root_ref.version_id).metadata
    round_ref = _version_from_payload(root["task_round_ref"])
    round_data = owner._core.get_version(round_ref.version_id).metadata
    net = owner._core.get_version(executable.net_ref.version_id).metadata
    refs = tuple(sorted(marking.token_refs, key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    consumed_places = {place for place, target, _ in structure.token_input_arcs if target == "step.run"}
    consumed = tuple(token.token_ref for token in marking.tokens if token.state.place in consumed_places)
    return FiringClaim(task_ref=_version_from_payload(root["task_ref"]),
        task_branch_ref=_version_from_payload(round_data["task_branch_ref"]), task_round_ref=round_ref,
        net_instance_ref=executable.net_ref, plan_ref=_version_from_payload(net["plan_ref"]),
        node_ref=transition.node_ref, operation_binding_ref=transition.operation_binding_ref,
        marking_checkpoint_ref=marking.checkpoint_ref, principal_ref=transition.principal_ref,
        logical_tau=0, attempt_index=1, claimed_input_refs=refs, consumed_input_refs=consumed,
        activation_ref=transition.activation_ref, agent_ref=transition.agent_ref)


@pytest.mark.parametrize("damage", ["missing", "consume_reference", "missing_consume", "wrong_logical", "foreign", "duplicate"])
def test_direct_admission_rejects_invalid_reference_claim(tmp_path, damage):
    from cpn.rpnh.registry.invocations import InvocationLifecycle, InvocationAdmissionError
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    owner = lease_owner(tmp_path / "run")
    claim = direct_claim(owner)
    lease = lease_state(current(owner)[2]).token_ref
    if damage == "missing":
        claim = replace(claim, claimed_input_refs=tuple(ref for ref in claim.claimed_input_refs if ref != lease))
    elif damage == "consume_reference":
        claim = replace(claim, consumed_input_refs=claim.claimed_input_refs)
    elif damage == "missing_consume":
        claim = replace(claim, consumed_input_refs=())
    elif damage == "wrong_logical":
        claim = replace(claim, claimed_input_refs=tuple(replace(ref, entity_id=new_id("petri_token")) if ref == lease else ref for ref in claim.claimed_input_refs))
    elif damage == "foreign":
        other = lease_owner(tmp_path / "foreign", command_id="foreign:fresh")
        foreign = lease_state(current(other)[2]).token_ref
        assert foreign != lease
        claim = replace(claim, claimed_input_refs=tuple(foreign if ref == lease else ref for ref in claim.claimed_input_refs))
    else:
        claim = replace(claim, claimed_input_refs=(*claim.claimed_input_refs, lease))
    before = len(owner._core.event_store.list_events())
    with pytest.raises((RegistryConflict, InvocationAdmissionError, SchemaGovernanceError, ValueError)):
        InvocationLifecycle(owner._core).admit_firing(claim, idempotency_key="invalid:admit")
    assert len(owner._core.event_store.list_events()) == before
    assert not owner._core.event_store.object_rows_by_type("transition_firing/v1")


@pytest.mark.parametrize("damage", ["missing_reference_index", "wrong_logical", "empty_consumed"])
def test_transaction_revalidates_claim_beyond_public_api(tmp_path, monkeypatch, damage):
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import new_id
    owner = lease_owner(tmp_path / "run")
    claim = direct_claim(owner)
    original = owner._core.begin
    def begin(**kwargs):
        tx = original(**kwargs)
        write = tx.prewrite
        def prewrite(**values):
            metadata = dict(values["metadata"])
            if values["object_type"] == "transition_firing/v1" and damage != "empty_consumed":
                if damage == "missing_reference_index":
                    metadata["claimed_input_version_ids"] = sorted(str(ref.version_id) for ref in claim.consumed_input_refs)
                else:
                    metadata["claimed_input_refs"] = [dict(ref) for ref in metadata["claimed_input_refs"]]
                    metadata["claimed_input_refs"][0]["logical_id"] = str(new_id("petri_token"))
            elif values["object_type"] == "marking_delta/v1" and damage == "empty_consumed":
                metadata["consumed_refs"] = []
            return write(**dict(values, metadata=metadata, payload=canonical_json(metadata)))
        tx.prewrite = prewrite
        return tx
    monkeypatch.setattr(owner._core, "begin", begin)
    with pytest.raises(RegistryConflict):
        InvocationLifecycle(owner._core).admit_firing(claim, idempotency_key="invalid:raw")
    assert not owner._core.event_store.object_rows_by_type("transition_firing/v1")


def test_weighted_references_require_exact_declared_quantity(tmp_path):
    from cpn.rpnh.marking import TeamNetMarking, MarkingStateError
    owner = lease_owner(tmp_path / "run", lease_count=2, read_weight=2)
    _, structure, marking = current(owner)
    lease_refs = {token.token_ref for token in marking.tokens if token.state.place == "step.lease"}
    admitted = owner.admit("step.run", logical_tau=0, command_id="weighted:admit")
    assert lease_refs <= set(admitted.preflight.claimed_token_refs)
    local = TeamNetMarking.from_authority(structure, marking)
    with pytest.raises(MarkingStateError, match="static lease read"):
        local.install_registered_firing_claim("step.run", tuple(ref for ref in admitted.preflight.claimed_token_refs if ref != next(iter(lease_refs))))
    finish(owner, admitted, "weighted")
    assert lease_refs == {token.token_ref for token in current(owner)[2].tokens if token.state.place == "step.lease"}


def test_extra_static_reference_is_rejected_instead_of_widening_read(tmp_path):
    from cpn.rpnh.registry.invocations import InvocationLifecycle
    from cpn.rpnh.registry.event_store import RegistryConflict
    owner = lease_owner(tmp_path / "run", lease_count=2, read_weight=1)
    with pytest.raises(RegistryConflict, match="static lease claim"):
        InvocationLifecycle(owner._core).admit_firing(direct_claim(owner), idempotency_key="extra:admit")
    admitted = owner.admit("step.run", logical_tau=0, command_id="correct:admit")
    assert len(admitted.preflight.claimed_token_refs) == 2


def test_two_transitions_share_reference_with_independent_control_inputs(tmp_path):
    from copy import deepcopy
    from cpn.rpnh.petri_contracts import PNFragment, PortBinding, TransitionDeclaration
    registration = _registration()
    def lower(config, context):
        places = tuple(PlaceDeclaration(port.name, port.schema, channel=port.channel) for port in context.ports)
        arcs = []
        for operation in context.operations:
            arcs.extend((ArcDeclaration(operation.inputs[0], operation.name, "input"),
                ArcDeclaration("lease", operation.name, "input", mode="read"),
                ArcDeclaration(operation.outputs[0], operation.name, "output", mode="produce", outcome="complete")))
        return PNFragment((*places, PlaceDeclaration("lease", TEXT, token_kind="resource_lease", capacity=1, reusable=True)),
            tuple(TransitionDeclaration(operation.name, operation.name) for operation in context.operations), tuple(arcs),
            tuple(PortBinding(port.name, port.name) for port in context.ports), context.operations,
            lease_identities=(LeaseIdentityDeclaration("asset"),),
            lease_pools=(ResourceLeasePoolBinding("lease", "lease", ("asset",)),))
    registration.register_component("readers", lower,
        identity={"implementation_id": "test.static_readers", "revision": "v1"}, contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("TwoReaders").to_dict()
    base = document["components"][0]
    base["key"] = "readers"
    prototype = base["operations"][0]
    base["ports"] = []
    base["operations"] = []
    for name in ("left", "right"):
        base["ports"].extend((dict(name=name + "_request", direction="input", schema=TEXT, channel="control"),
                              dict(name=name + "_result", direction="output", schema=TEXT)))
        operation = deepcopy(prototype)
        operation.update(name=name, inputs=[name + "_request"], outputs=[name + "_result"])
        operation["outcomes"][0]["products"][0]["port"] = name + "_result"
        base["operations"].append(operation)
    document["entry"] = {name: {"component": "step", "port": name + "_request"} for name in ("left", "right")}
    document["exit"] = {name: {"component": "step", "port": name + "_result"} for name in ("left", "right")}
    document["terminal"].update(source=document["exit"]["right"], operation="right")
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(ModuleDeclaration.from_dict(document), registration, run_dir=tmp_path / "run",
        task_input=task, entry_inputs={"left": task, "right": task}, resource_inputs={"step.asset": task},
        budgets=ModuleBudgetDeclaration(tuple(document["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-static-lease", owner_statement="Two readers", command_id="readers:fresh")
    initial = lease_state(current(owner)[2])
    firings = [owner.admit("step." + name, logical_tau=0, command_id=name + ":admit") for name in ("left", "right")]
    assert all(firing is not None for firing in firings)
    assert set(firings[0].preflight.claimed_token_refs) & set(firings[1].preflight.claimed_token_refs) == {initial.token_ref}
    for name, firing in zip(("left", "right"), firings):
        execution = owner.start(firing, command_id=name + ":start")
        outputs = owner.products(execution, outcome_id="complete",
            products={"step." + name + "_result": (canonical_json(name),)}, command_id=name + ":products")
        owner.succeed(outputs, command_id=name + ":success")
        assert lease_state(current(owner)[2]) == initial


def test_reference_alone_does_not_authorize_dynamic_resource_access(tmp_path):
    from cpn.rpnh.marking import TeamNetMarking, MarkingStateError
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    owner = lease_owner(tmp_path / "run")
    admitted = owner.admit("step.run", logical_tau=0, command_id="read:admit")
    executable, structure, marking = current(owner)
    local = TeamNetMarking.from_authority(structure, marking)
    install_active_module_claims(owner._core, local, executable.net_ref)
    with pytest.raises(MarkingStateError, match="no variable resource-access arc"):
        local.derive_firing_resource_access("step.run", marking.epoch,
            admitted.admission.context.own_transition_firing_ref, lease_state(marking).resource_ref, "edit")


def test_new_process_cold_registry_reconstructs_active_and_settled_refs(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    owner = lease_owner(tmp_path / "run", occurrences=2)
    initial = lease_state(current(owner)[2])
    first = owner.admit("step.run", logical_tau=0, command_id="first:admit")
    second = owner.admit("step.run", logical_tau=0, command_id="second:admit")
    script = """
import json,sys,socket
def forbidden(*args,**kwargs): raise AssertionError('offline cold reconstruction cannot use sockets')
socket.socket=forbidden
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.module_execution import install_active_module_claims
from cpn.rpnh.marking import TeamNetMarking
core=_RegistryCore(sys.argv[1],create=False,read_only=True)
net,structure,marking=hydrate_module_runtime(core)
local=TeamNetMarking.from_authority(structure,marking)
active=install_active_module_claims(core,local,net.net_ref)
lease=next(t.state for t in marking.tokens if t.state.place=='step.lease')
for row in core.event_store.object_rows_by_type('transition_firing/v1'):
    publication=core.event_store.firing_publication_row(row['version_id'])
    if publication['state']=='PUBLISHED': core.event_store.reconstruct_firing_state(row['version_id'])
print(json.dumps({'lease':str(lease.token_ref.version_id),'active':len(active),
 'references':[str(t.token_ref.version_id) for a in local.active_claim_occurrences(local.epoch)
 for t in local.claimed_operation_tokens(a.transition_id,a.claim_epoch,instance_key=a.local_key)
 if t.place=='step.lease'],'events':len(core.event_store.list_events())}))
"""
    for expected_active in (2, 1, 0):
        events = len(owner._core.event_store.list_events())
        result = subprocess.run([sys.executable, "-c", script, str(owner._core.run_dir)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(result.stdout)
        assert observed["lease"] == str(initial.token_ref.version_id)
        assert observed["active"] == expected_active
        assert observed["references"] == [str(initial.token_ref.version_id)] * expected_active
        assert observed["events"] == events == len(owner._core.event_store.list_events())
        if expected_active:
            finish(owner, first if expected_active == 2 else second, "first" if expected_active == 2 else "second")


def test_completion_recovery_preserves_static_reference(tmp_path):
    from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
    from cpn.rpnh.run import resume_run
    owner = lease_owner(tmp_path / "run")
    initial = lease_state(current(owner)[2])
    admitted = owner.admit("step.run", logical_tau=0, command_id="recover:admit")
    execution = owner.start(admitted, command_id="recover:start")
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("result"),)}, command_id="recover:products")
    kernel, repository = owner.operation_repository()
    record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key="recover:completion")
    recovered = resume_run(owner.registration, run_dir=owner._core.run_dir,
        model_condition="offline-static-lease")
    assert recovered.snapshot()["active_firings"] == []
    assert lease_state(current(recovered)[2]) == initial
