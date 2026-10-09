"""Bounded S1 validation; execute ONLY after the parent grants SLOT_GRANTED.

Fixture setup is direct Registry. Native execution uses original Harness,
OwnerEventLoop, deterministic HOST Futures and real AF_UNIX snapshot traffic.
No worker subprocess, provider, monkeypatch, new product policy or H7 behavior.
"""
from concurrent.futures import Future
from dataclasses import asdict, is_dataclass, replace
import json
import os
from pathlib import Path
import socket
import time

import pytest

# Import only the factories: the offline module's autouse transport sentinel
# is intentionally NOT imported as a fixture into this native validation module.
from test_static_lease_reads import (
    TEXT, CONFIG_SCHEMA_ID, _registration, _simple_module, lease_owner,
    current, lease_state,
)
from test_static_lease_interactions import _world, _fresh_leases
from test_static_lease_exact_selection import _owner_with_dead_then_live_carrier, _claim
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.harness import Harness, OperationDispatch, OperationProducts, OperationDisposition
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_execution import install_active_module_claims
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import resume_run
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh.petri_contracts import ArcDeclaration, PlaceDeclaration, LeaseIdentityDeclaration, ResourceLeasePoolBinding

OUT = Path(__file__).resolve().parent


def evidence_json(value):
    return json.loads(json.dumps(value, default=lambda item:
        asdict(item) if is_dataclass(item) else str(item)))


def save(name, value):
    path = OUT / (name + '.json')
    assert path.resolve().parent == OUT and not path.is_symlink()
    assert not path.exists() or path.stat().st_nlink == 1
    path.write_text(json.dumps(evidence_json(value), indent=2) + '\n')


def read_state(owner):
    """Original read-only Registry reconstruction, explicitly a direct reader."""
    head = owner._core.event_store.max_ordinal()
    core = _RegistryCore(owner._core.run_dir, create=False, read_only=True)
    net, structure, marking = hydrate_module_runtime(core)
    local = TeamNetMarking.from_authority(structure, marking)
    active = install_active_module_claims(core, local, net.net_ref)
    rows = core.event_store.object_rows_by_type('transition_firing/v1')
    publications = []
    for row in rows:
        pub = core.event_store.firing_publication_row(row['version_id'])
        publications.append(dict(pub))
        if pub['state'] == 'PUBLISHED':
            core.event_store.reconstruct_firing_state(row['version_id'])
    tokens = core.event_store.object_rows_by_type('petri_token/v1')
    leases = [t for t in marking.tokens if t.state.lease_identity_ref is not None]
    minted = [json.loads(row['metadata_json']) for row in tokens]
    assert core.event_store.max_ordinal() == head == owner._core.event_store.max_ordinal()
    assert core.event_store.actual_model_call_counts() == (0, 0)
    return evidence_json(dict(
        reader='direct read-only Registry', head=head, active=len(active),
        active_refs=[a.transition_firing_ref for a in active],
        active_claims=[dict(transition=a.transition_id,
            firing=a.transition_firing_ref, claimed_input_refs=a.claimed_input_refs) for a in active],
        marking_checkpoint=marking.checkpoint_ref,
        leases=[t.state for t in leases], lease_count=len(leases),
        lease_minted_count=sum(t.get('lease_identity_ref') is not None for t in minted),
        token_minted_count=len(tokens), firing_count=len(rows), publications=publications,
        settlements=len(core.event_store.list_events_by_type(('transition_firing_settled/v1',))),
        completions=len(core.event_store.list_events_by_type(('registered_operation_completion_recorded/v1',))),
        model_counts=core.event_store.actual_model_call_counts(),
    ))


class Native:
    """Finite HOST implementation at the original injectable callback boundary."""
    def __init__(self, owner, name, capacity=2):
        self.owner, self.name = owner, name
        self.path = Path(owner._core.run_dir) / 'n.sock'
        self.loop = OwnerEventLoop(owner, self.path)
        self.jobs, self.observations = [], []
        self.body_count = 0
        self.harness = Harness(owner=owner, event_loop=self.loop,
            prepare_dispatcher=self.prepare, submit_operation=self.submit,
            max_in_flight=capacity)

    def prepare(self, *, execution, **_):
        def invoke():
            self.body_count += 1
            key = str(execution.operation_execution_lease_ref.version_id)
            _, structure, _ = current(self.owner)
            transition = next(t for t in structure.compiled.symbolic.transitions
                if t.name == execution.operation.firing.transition_id)
            declared = next(o.declaration for o in structure.compiled.operations
                if o.declaration.name == transition.operation)
            products = {name: (canonical_json('native:' + key),)
                        for name in declared.outputs}
            outputs = self.owner.products(execution, outcome_id='complete',
                products=products, command_id=key + ':products')
            kernel, repository = self.owner.operation_repository()
            record_registered_operation_completion(self.owner._core, kernel,
                repository, outputs, idempotency_key=key + ':durable')
            return OperationProducts(outputs)
        return OperationDispatch(execution, invoke)

    def submit(self, callback):
        future = Future()
        self.jobs.append((callback, future))
        return future

    def host(self, callback):
        future = self.loop.submit_host(callback)
        deadline = time.monotonic() + 10
        while not future.done() and time.monotonic() < deadline:
            self.loop.dispatch_ready(timeout=.01)
        return future.result(timeout=0)

    def release(self, index, *, settle=True):
        callback, future = self.jobs[index]
        result = self.host(callback)
        if settle:
            future.set_result(result)
            self.loop.dispatch_ready(timeout=.1)
            error = self.harness.result().completion_error
            if error is not None:
                # Preserve actual product traceback and post-error authority;
                # do not turn a failed terminal projection into acceptance.
                self.snapshot('completion-error')
                raise error
        return result

    def snapshot(self, label):
        # Actual AF_UNIX request/reply, without a transport replacement/thread.
        head = self.owner._core.event_store.max_ordinal()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.path))
            client.sendall(json.dumps(dict(command_id=label, command='snapshot',
                arguments={})).encode() + b'\n')
            client.setblocking(False)
            data = bytearray()
            deadline = time.monotonic() + 10
            while b'\n' not in data and time.monotonic() < deadline:
                self.loop.dispatch_ready(timeout=.01)
                try:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                except BlockingIOError:
                    pass
        response = json.loads(data)
        assert response['status'] == 'OK'
        assert head == self.owner._core.event_store.max_ordinal()
        state = read_state(self.owner)
        state.update(label=label, af_unix_response=response,
                     deterministic_host_body_count=self.body_count)
        self.observations.append(state)
        save(self.name, self.observations)
        return state

    def close(self):
        self.loop.close()
        cleaned = not self.path.exists() and self.loop.listener.fileno() == -1
        save(self.name + '-cleanup', dict(socket_removed=cleaned,
            worker_subprocesses_started=0, body_count=self.body_count))
        assert cleaned

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def assert_lease(owner, initial):
    assert lease_state(current(owner)[2]) == initial


def test_g1_shared_transitions(tmp_path):
    owner = two_readers(tmp_path)
    initial = lease_state(current(owner)[2])
    with Native(owner, 'g1') as n:
        n.snapshot('before')
        assert len(n.harness.schedule_ready()) == 2
        assert n.snapshot('two-live')['active'] == 2
        assert {e.operation.firing.transition_id for e in n.harness._pending.values()} == {'step.left', 'step.right'}
        net, structure, marking = current(owner)
        active = install_active_module_claims(owner._core,
            TeamNetMarking.from_authority(structure, marking), net.net_ref)
        assert set(active[0].claimed_input_refs) & set(active[1].claimed_input_refs) == {initial.token_ref}
        n.release(0)
        assert n.snapshot('one-live')['active'] == 1
        assert_lease(owner, initial)
        n.release(1)
        final = n.snapshot('settled')
        assert final['active'] == 0 and final['settlements'] == 2
        assert final['lease_minted_count'] == 1 and n.body_count == 2
        assert_lease(owner, initial)


def test_g2_same_transition(tmp_path):
    owner = lease_owner(tmp_path / 'r', occurrences=2)
    initial = lease_state(current(owner)[2])
    with Native(owner, 'g2') as n:
        assert len(n.harness.schedule_ready()) == 1
        assert len(n.harness.schedule_ready()) == 1
        assert n.snapshot('two-live')['active'] == 2
        net, structure, marking = current(owner)
        active = install_active_module_claims(owner._core,
            TeamNetMarking.from_authority(structure, marking), net.net_ref)
        assert all(a.transition_id == 'step.run' and initial.token_ref in a.claimed_input_refs for a in active)
        assert len({a.transition_firing_ref for a in active}) == 2
        n.release(0)
        assert n.snapshot('one-live')['active'] == 1
        assert_lease(owner, initial)
        n.release(1)
        final = n.snapshot('settled')
        assert final['active'] == 0 and final['settlements'] == 2
        assert final['lease_minted_count'] == 1 and n.body_count == 2
        assert_lease(owner, initial)


def test_g3_stop_retains_unresolved(tmp_path):
    owner = lease_owner(tmp_path / 'r', occurrences=3)
    initial = lease_state(current(owner)[2])
    with Native(owner, 'g3') as n:
        n.harness.schedule_ready()
        n.harness.schedule_ready()
        assert n.snapshot('two-live')['active'] == 2
        n.harness.request_owner_stop()
        executions = tuple(n.harness._pending.values())
        for (_, future), execution in zip(n.jobs, executions):
            future.set_result(OperationDisposition(execution, 'execution_block'))
        result = n.harness.exact_execute()
        final = n.snapshot('stopped-retained')
        assert result.stop_reason == 'stopped_by_owner'
        assert result.terminal_evidence_ref is None
        assert final['active'] == final['firing_count'] == 2
        assert final['settlements'] == final['completions'] == n.body_count == 0
        assert final['lease_minted_count'] == 1
        assert_lease(owner, initial)


def test_g4_durable_reentry(tmp_path):
    owner = lease_owner(tmp_path / 'r')
    initial = lease_state(current(owner)[2])
    with Native(owner, 'g4-before') as n:
        n.harness.schedule_ready()
        n.release(0, settle=False)
        before = n.snapshot('durable-before-success')
        assert before['active'] == before['completions'] == n.body_count == 1
        assert before['settlements'] == 0
    recovered = resume_run(owner.registration, run_dir=owner._core.run_dir,
                           model_condition='offline-static-lease')
    with Native(recovered, 'g4-after') as n:
        after = n.snapshot('recovered')
        assert after['active'] == 0 and after['settlements'] == 1
        assert_lease(recovered, initial)
        # Resume again according to the existing lifecycle; a closed terminal
        # may reject reentry, but no second body, output token or settlement.
        from cpn.rpnh.registry.errors import ResourceIntegrityFault
        try:
            again = resume_run(owner.registration, run_dir=owner._core.run_dir,
                               model_condition='offline-static-lease')
            repeated = read_state(again)
            mode = 'supported second resume'
        except ResourceIntegrityFault as exc:
            repeated = read_state(recovered)
            mode = 'existing reentry rejection: ' + str(exc)
        save('g4-repeat', dict(mode=mode, state=repeated))
        assert repeated['token_minted_count'] == after['token_minted_count']
        assert repeated['settlements'] == 1 and repeated['completions'] == 1
        assert repeated['leases'] == after['leases']
        assert n.body_count == 0


@pytest.mark.parametrize('mode', ['union', 'produce', 'data-read', 'control-read'])
def test_g5_coexistence(tmp_path, mode):
    owner = (_world(tmp_path / 'r', variable_mode='produce' if mode == 'produce' else 'read')
             if mode in ('union', 'produce') else lease_owner(tmp_path / 'r',
                ordinary_read=True, control_read=mode == 'control-read'))
    before = current(owner)[2]
    leases = _fresh_leases(before)
    with Native(owner, 'g5-' + mode) as n:
        assert len(n.harness.schedule_ready()) == 1
        assert n.snapshot('live')['active'] == 1
        net, structure, marking = current(owner)
        local = TeamNetMarking.from_authority(structure, marking)
        install_active_module_claims(owner._core, local, net.net_ref)
        refs = local.claimed_operation_tokens('step.run', local.epoch)
        assert len({t.token_ref for t in refs}) == len(refs)
        if mode == 'union':
            assert len(refs) == 2  # carrier + deduplicated static/variable lease
        if mode.endswith('read'):
            assert n.harness.schedule_ready() == ()
        n.release(0)
        n.snapshot('settled')
        assert n.body_count == 1
        after = current(owner)[2]
        now = _fresh_leases(after)
        if mode == 'produce':
            assert now['step.static'].token_ref == leases['step.static'].token_ref
            assert now['step.static'].state == leases['step.static'].state
            assert now['step.variable'].token_ref != leases['step.variable'].token_ref
            assert now['step.variable'].state.lease_identity_ref == leases['step.variable'].state.lease_identity_ref
            assert now['step.variable'].state.resource_ref is not None
        else:
            assert {p: (t.token_ref, t.state) for p, t in now.items()} == {
                p: (t.token_ref, t.state) for p, t in leases.items()}
        if mode.endswith('read'):
            old = next(t for t in before.tokens if t.state.place == 'step.request')
            new = [t for t in after.tokens if t.state.place == 'step.request']
            assert len(new) == 1 and new[0].token_ref != old.token_ref
            assert new[0].state.resource_ref == old.state.resource_ref
            assert old.token_ref not in after.token_refs


def test_g6_exact_selection(tmp_path):
    # Direct explicit admission is intentionally labelled: no scheduler search fix.
    from cpn.rpnh.registry.invocations import InvocationLifecycle, InvocationAdmissionError
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.module_gateway import start_module_firing
    from cpn.rpnh.firing_preflight import preflight_module_firing
    owner = _owner_with_dead_then_live_carrier(tmp_path / 'r')
    executable, structure, before = current(owner)
    bad, good = sorted((t for t in before.tokens if t.state.place == 'step.carrier'),
                      key=lambda t: t.state.token_id)
    pool = next(t for t in before.tokens if t.state.place == 'step.pool_a')
    assert bad.state.producer == 'step.bad' and good.state.producer == 'step.good'
    assert owner.admit('step.use', logical_tau=2, command_id='default:admit') is None
    head = owner._core.event_store.max_ordinal()
    invalid = _claim(owner, executable, before, (bad.token_ref, pool.token_ref), (bad.token_ref,))
    with pytest.raises((RegistryConflict, InvocationAdmissionError)):
        InvocationLifecycle(owner._core).admit_firing(invalid, idempotency_key='exact:bad')
    assert owner._core.event_store.max_ordinal() == head
    claim = _claim(owner, executable, before, (good.token_ref, pool.token_ref), (good.token_ref,))
    admitted = InvocationLifecycle(owner._core).admit_firing(claim, idempotency_key='exact:good')
    preflight = preflight_module_firing(structure, before, transition_id='step.use',
                                      claimed_token_refs=claim.claimed_input_refs)
    kernel, repository = owner.operation_repository()
    execution = start_module_firing(owner._core, kernel, repository,
        admitted.context.invocation_ref, preflight=preflight, idempotency_key='exact:start')
    save('g6-direct-admission', dict(classification='direct Registry exact selection',
        default_dead_first_rejected=True, bad_claim_rejected=True, selected=claim.claimed_input_refs))
    with Native(owner, 'g6') as n:
        dispatch = n.prepare(execution=execution)
        n.harness.watch_completion(execution, n.submit(dispatch.invoke))
        n.snapshot('exact-live')
        n.release(0)
        n.snapshot('exact-settled')
        remaining = {t.token_ref: t.state for t in current(owner)[2].tokens}
        assert good.token_ref not in remaining and remaining[bad.token_ref] == bad.state
        for t in before.tokens:
            if t.state.lease_identity_ref is not None:
                assert remaining[t.token_ref] == t.state
        assert sum(t.state.place == 'step.used' for t in current(owner)[2].tokens) == 1


def test_g7_dynamic_drain_adoption(tmp_path):
    from cpn.rpnh.compiler import compile_module
    from cpn.rpnh.petri_contracts import DeclarationError
    from cpn.rpnh.registry.publication import _version_from_payload
    # Independent compiler rejection is direct evidence, not native execution.
    module, registration = _world(tmp_path / 'unused', reset_lease=True)
    with pytest.raises(DeclarationError, match='Reset arc cannot retire'):
        compile_module(module, registration)
    save('g7-direct-reset', dict(classification='direct compiler', status='PASS'))
    # Retirement rejection is independent of the dynamic terminal boundary.
    retirement_owner = lease_owner(tmp_path / 'retire')
    retirement_net, retirement_structure, retirement_marking = current(retirement_owner)
    retirement_lease = lease_state(retirement_marking)
    with Native(retirement_owner, 'g7-retire') as r:
        retirement = prepare_replacement(retirement_owner,
            retirement_structure.compiled.source,
            retire_token_refs=(retirement_lease.token_ref,))
        rejected = r.host(lambda: apply_replacement(retirement_owner,
            retirement, command_id='native:retire-lease'))
        assert rejected['status'] == 'NEEDS_MARKING_DECISION'
        assert 'lease authority cannot retire' in json.dumps(evidence_json(rejected))
        assert current(retirement_owner)[0].net_ref == retirement_net.net_ref
        assert_lease(retirement_owner, retirement_lease)
        r.snapshot('retirement-rejected')
        save('g7-retirement-rejected', rejected)
    owner = _world(tmp_path / 'r', same_pool=False)
    executable, structure, before = current(owner)
    leases = _fresh_leases(before)
    original_lower = owner.registration.resolve('component', 'interactions')

    def without_static(config, context):
        fragment = original_lower(config, context)
        return replace(fragment, arcs=tuple(a for a in fragment.arcs
            if not (a.place == 'static' and a.direction == 'input' and a.mode == 'read')))

    owner.registration.register_component('interactions_without_static', without_static,
        identity={'implementation_id': 'test.static_lease_interactions_without_static', 'revision': 'v1'},
        contracts={'config_schema': CONFIG_SCHEMA_ID})
    document = structure.compiled.source.to_dict()
    next(c for c in document['components'] if c['name'] == 'step')['key'] = 'interactions_without_static'
    candidate = ModuleDeclaration.from_dict(document)
    with Native(owner, 'g7') as n:
        assert len(n.harness.schedule_ready()) == 1
        plan = prepare_replacement(owner, candidate)
        result = n.host(lambda: apply_replacement(owner, plan, command_id='native:replace'))
        assert result['status'] == 'DRAINING'
        assert current(owner)[0].net_ref == executable.net_ref
        assert n.snapshot('draining')['active'] == 1
        n.release(0)
        n.snapshot('adopted')
        adopted = apply_replacement(owner, plan, command_id='native:replace')
        assert adopted['status'] == 'ADOPTED' and adopted['ordinary_retirements'] == []
        after_net, after_structure, after = current(owner)
        assert after_net.net_ref != executable.net_ref and after_structure.lease_reference_arcs == ()
        now = _fresh_leases(after)
        mapping = {_version_from_payload(m['old_token_ref']): _version_from_payload(m['new_token_ref'])
                   for m in adopted['token_mappings']}
        for place, old in leases.items():
            assert mapping[old.token_ref] == now[place].token_ref != old.token_ref
            assert now[place].state.resource_ref == old.state.resource_ref
            assert now[place].state.lease_identity_ref == old.state.lease_identity_ref
        save('g7-mapping', adopted)


# Verbatim real Registry fixture construction from test_static_lease_reads.py.
def two_readers(tmp_path):
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
    return owner
