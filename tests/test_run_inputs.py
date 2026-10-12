"""Native external-input admission, identity, concurrency and recovery contracts.

All business execution is deterministic and produces local Registry resources.
No browser, model, credentials, external side effect or alternate ledger is used.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from cpn.rpnh.collaboration import UserCollaborationHost
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, PNFragment, PlaceDeclaration, PortBinding,
    PortDeclaration, TransitionDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.errors import ResourceIntegrityFault, StaleWriterFence
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.run_inputs import WAIT, RECEIPT
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run, resume_run
from cpn.rpnh.run_inputs import InputWaitSpec, RunInputHost
from test_user_collaboration_authority import personal, catalog


CANDIDATE = "test/input_candidate/v1"
DECISION = "test/input_decision/v1"
RESULT = "test/input_result/v1"
CONFIG = "test/input_config/v1"
COMPONENT = "test/input-component/v1"
EXECUTOR = "test/input-executor/v1"
TERMINAL = "test/input-terminal/v1"
MODEL = "offline-run-inputs"


def _lower(_config, context):
    fragment = PNFragment(
        places=(PlaceDeclaration("candidate", CANDIDATE, capacity=1),
                PlaceDeclaration("decision", DECISION, capacity=1),
                PlaceDeclaration("other_decision", DECISION, capacity=1),
                PlaceDeclaration("result", RESULT, capacity=1)),
        transitions=(TransitionDeclaration("execute", "execute"),
                     TransitionDeclaration("alternate", "alternate")),
        arcs=tuple(arc for name, decision in (("execute", "decision"),
                                              ("alternate", "other_decision"))
                   for arc in (ArcDeclaration("candidate", name, "input"),
                               ArcDeclaration(decision, name, "input"),
                               ArcDeclaration("result", name, "output", mode="produce", outcome="complete"))),
        ports=(PortBinding("candidate", "candidate"), PortBinding("result", "result")),
        operations=context.operations,
        internal_ports=(PortDeclaration("decision", "input", DECISION),
                        PortDeclaration("other_decision", "input", DECISION)),
        internal_bindings=(PortBinding("decision", "decision"),
                           PortBinding("other_decision", "other_decision")),
    )
    if not _config.get("reserve"):
        return fragment
    # A second original anchor shares the decision place, or feeds the same
    # execute transition after the first candidate has been consumed.
    return replace(fragment,
        places=tuple(replace(p, capacity=2) if p.name == "result" else p for p in fragment.places)
            + (PlaceDeclaration("reserve", CANDIDATE, capacity=1),),
        transitions=fragment.transitions + (TransitionDeclaration("other", "other"),
                                             TransitionDeclaration("feed", "feed")),
        arcs=fragment.arcs + (
            ArcDeclaration("reserve", "other", "input"),
            ArcDeclaration("decision", "other", "input"),
            ArcDeclaration("result", "other", "output", mode="produce", outcome="complete"),
            ArcDeclaration("reserve", "feed", "input"),
            ArcDeclaration("candidate", "feed", "output", mode="produce", outcome="complete")),
        ports=fragment.ports + (PortBinding("reserve", "reserve"),),
        internal_ports=fragment.internal_ports + (PortDeclaration("candidate_out", "output", CANDIDATE),),
        internal_bindings=fragment.internal_bindings + (PortBinding("candidate_out", "candidate"),))


def _registration(calls):
    registration = Registration()
    schemas = {
        CANDIDATE: {"type": "object", "properties": {"case": {"type": "string"},
                    "amount_minor": {"type": "integer"}}, "required": ["case", "amount_minor"]},
        DECISION: {"type": "object", "properties": {"decision": {"enum": ["approve", "deny", "expired"]}},
                   "required": ["decision"]},
        RESULT: {"type": "object", "properties": {"case": {"type": "string"},
                 "effect": {"type": "boolean"}}, "required": ["case", "effect"]},
        CONFIG: {"type": "object", "properties": {"reserve": {"type": "boolean"}}},
    }
    for key, schema in schemas.items():
        registration.register_schema(key, dict(schema, additionalProperties=False,
            **{"$id": key, "$schema": "http://json-schema.org/draft-07/schema#"}))
    registration.register_component(COMPONENT, _lower,
        identity={"implementation_id": "input-component", "revision": "v1"},
        contracts={"config_schema": CONFIG})

    def execute(*, execution):
        calls.append(execution.operation.firing.transition_id)
        values = [json.loads(item.artifact.payload) for item in execution.operation.inputs]
        candidate = next(v for v in values if "case" in v)
        if execution.operation.firing.transition_id == "gate.feed":
            return candidate
        decision = next(v for v in values if "decision" in v)
        return {"case": candidate["case"], "effect": decision["decision"] == "approve"}

    registration.register_executor(EXECUTOR, execute,
        identity={"implementation_id": "input-executor", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "input-terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def _module(reserve=False):
    budget = {"bucket_id": "operations", "budget_scope": "module", "finalization_scope": None}
    def terminal(name):
        return {"key": TERMINAL, "source": {"component": "gate", "port": "result"},
                "operation": name, "outcome": "complete", "config": {"run_outcome": "complete"}}
    document = {
        "schema_version": "rpnh/module_declaration/v1", "name": "NativeInputTest",
        "components": [{"name": "gate", "key": COMPONENT, "config_schema": CONFIG,
            "config": {}, "ports": [
                {"name": "candidate", "direction": "input", "schema": CANDIDATE},
                {"name": "result", "direction": "output", "schema": RESULT}],
            "operations": [{"name": name, "executor": EXECUTOR,
                "inputs": ["candidate", port], "outputs": ["result"], "tools": [],
                "config": {}, "request_port": None, "budget_binding": budget,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}
                for name, port in (("execute", "decision"), ("alternate", "other_decision"))]}],
        "links": [], "entry": {"candidate": {"component": "gate", "port": "candidate"}},
        "exit": {"result": {"component": "gate", "port": "result"}},
        "terminal": terminal("execute"), "terminal_alternatives": [terminal("alternate")],
        "required_schemas": list((CANDIDATE, DECISION, RESULT, CONFIG)), "budgets": {},
        "budget_buckets": [dict(budget, max_attempts=3)],
    }
    if reserve:
        component = document["components"][0]
        component["config"] = {"reserve": True}
        component["ports"].append({"name": "reserve", "direction": "input", "schema": CANDIDATE})
        for name, inputs, port in (("other", ["reserve", "decision"], "result"),
                                   ("feed", ["reserve"], "candidate_out")):
            component["operations"].append({"name": name, "executor": EXECUTOR,
                "inputs": inputs, "outputs": [port], "tools": [], "config": {},
                "request_port": None, "budget_binding": budget,
                "outcomes": [{"name": "complete", "products": [{"port": port}]}]})
        document["entry"]["reserve"] = {"component": "gate", "port": "reserve"}
        document["terminal_alternatives"].append(terminal("other"))
    return ModuleDeclaration.from_dict(document)


@pytest.fixture
def world(tmp_path):
    return _world(tmp_path)


def _world(tmp_path, reserve=False):
    calls = []
    module = _module(reserve)
    candidate = OwnerInput(CANDIDATE, canonical_json({"case": "case-001", "amount_minor": 2500}),
                           "Synthetic exact candidate")
    entries = {"candidate": candidate}
    if reserve:
        entries["reserve"] = OwnerInput(CANDIDATE,
            canonical_json({"case": "case-002", "amount_minor": 2600}), "Second original candidate")
    owner = start_run(module, _registration(calls), run_dir=tmp_path / "run",
        task_input=candidate, entry_inputs=entries,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 3, 0, 3, 0),
        model_condition=MODEL, owner_statement="Native deterministic input tests",
        command_id="fresh", catalog=catalog())
    owner.schema_gateway.bind_source_identity(source_id="input-owner", command_id="source")
    collaboration = UserCollaborationHost(owner._core, owner.schema_gateway)
    origins = {"owner": (owner._core, owner.principal_ref)}
    origins.update({role: personal(tmp_path / role, "input-" + role)
                    for role in ("requester", "approver", "third")})
    sessions = {role: collaboration.create_session(origin_core=core, principal_ref=principal,
                command_id=role) for role, (core, principal) in origins.items()}
    assert len({s.actor for s in sessions.values()}) == 4
    anchor, = (t for t in owner.snapshot()["marking"] if t["place"] == "gate.candidate")
    binding = _version_from_payload(anchor["resource_ref"])
    return SimpleNamespace(owner=owner, host=RunInputHost(owner), collaboration=collaboration,
        origins=origins, sessions=sessions, calls=calls, path=tmp_path,
        spec=InputWaitSpec(transition_id="gate.execute", input_port="gate.decision",
            anchor_token_ref=_version_from_payload(anchor["token_ref"]),
            binding_ref=ResourceVersionRef(binding.entity_id, binding.version_id),
            allowed_principal_refs=(collaboration.session_principal(sessions["approver"]),)))


def _checkpoint(w):
    return _version_from_payload(w.owner.snapshot()["checkpoint_ref"])


def _open(w, *, spec=None, command="open"):
    return w.host.open_wait(w.sessions["owner"], spec=spec or w.spec, command_id=command)


def _decision(value="approve"):
    return OwnerInput(DECISION, canonical_json({"decision": value}), "Exact decision")


def _submit(w, wait, *, actor="approver", command="submit", value="approve", **overrides):
    args = dict(wait_ref=wait, expected_checkpoint_ref=_checkpoint(w),
                binding_ref=w.spec.binding_ref, declaration=_decision(value), command_id=command)
    args.update(overrides)
    return w.host.submit(w.sessions[actor], **args)


def _events(w, kind):
    return w.owner._core.event_store.list_events_by_type((kind,))


def _products(w, transition="gate.execute", *, command=None):
    command = command or transition
    admitted = w.owner.admit(transition, logical_tau=0, command_id="admit:" + command)
    assert admitted is not None
    execution = w.owner.start(admitted, command_id="start:" + command)
    result = w.owner.registration.resolve("executor", EXECUTOR)(execution=execution)
    outputs = w.owner.products(execution, outcome_id="complete",
        products={("gate.candidate_out" if transition == "gate.feed" else "gate.result"):
                  (canonical_json(result),)}, command_id="products:" + command)
    return execution, outputs, result


def _resume(w):
    old = w.owner
    w.owner = resume_run(_registration(w.calls), run_dir=w.path / "run", model_condition=MODEL,
                         catalog=catalog())
    w.host = RunInputHost(w.owner)
    w.collaboration = UserCollaborationHost(w.owner._core, w.owner.schema_gateway)
    w.origins["owner"] = (w.owner._core, w.owner.principal_ref)
    w.sessions = {role: w.collaboration.resume_session(session.ref,
        origin_core=w.origins[role][0], principal_ref=w.origins[role][1])
        for role, session in w.sessions.items()}
    return old


@pytest.mark.parametrize("decision,effect", [("approve", True), ("deny", False)])
def test_native_input_enables_same_run_once(world, decision, effect):
    w = world
    before = w.owner.snapshot()
    assert before["enabled_transitions"] == []
    assert w.owner.admit("gate.execute", logical_tau=0, command_id="blocked") is None
    wait = _open(w)
    assert w.host.inspect(wait)["status"] == "waiting"
    assert w.owner.snapshot()["enabled_transitions"] == []
    checkpoint = _checkpoint(w)
    receipt = _submit(w, wait, value=decision, expected_checkpoint_ref=checkpoint)
    after = w.owner.snapshot()
    assert {k: after[k] for k in ("run_ref", "task_ref", "net_ref")} == {
        k: before[k] for k in ("run_ref", "task_ref", "net_ref")}
    assert after["enabled_transitions"] == ["gate.execute"]
    execution, outputs, result = _products(w)
    assert {item.resource_ref for item in execution.operation.inputs} == {
        w.spec.binding_ref, receipt.resource_ref}
    w.owner.succeed(outputs, command_id="settle")
    assert result == {"case": "case-001", "effect": effect}
    assert w.calls == ["gate.execute"]
    assert len(_events(w, "transition_firing_settled/v1")) == 1
    assert w.owner.snapshot()["enabled_transitions"] == []
    assert w.owner._core.event_store.actual_model_call_counts() == (0, 0)
    before_replay = w.owner._core.event_store.max_ordinal()
    replay = _submit(w, wait, value=decision, expected_checkpoint_ref=checkpoint)
    assert replay.replayed and replay.receipt_ref == receipt.receipt_ref
    assert w.owner._core.event_store.max_ordinal() == before_replay
    assert w.calls == ["gate.execute"]


def test_exact_replay_rejects_changed_actor_or_body(world):
    w = world
    wait = _open(w)
    assert _open(w) == wait
    checkpoint = _checkpoint(w)
    first = _submit(w, wait, expected_checkpoint_ref=checkpoint)
    before = w.owner._core.event_store.max_ordinal()
    replay = _submit(w, wait, expected_checkpoint_ref=checkpoint)
    assert replay.replayed and replay.receipt_ref == first.receipt_ref
    for changed in ({"value": "deny"}, {"actor": "requester"}, {"actor": "third"}):
        with pytest.raises(RegistryConflict, match="replay"):
            _submit(w, wait, expected_checkpoint_ref=checkpoint, **changed)
    with pytest.raises(RegistryConflict, match="replay"):
        _open(w, spec=replace(w.spec, input_port="gate.other_decision", transition_id="gate.alternate"))
    assert w.owner._core.event_store.max_ordinal() == before
    assert len(w.owner._core.event_store.object_rows_by_type(RECEIPT)) == 1


@pytest.mark.parametrize("actor", ["requester", "third"])
def test_unlisted_native_actor_cannot_open_or_submit(world, actor):
    w = world
    with pytest.raises(RegistryConflict, match="owner"):
        w.host.open_wait(w.sessions[actor], spec=w.spec, command_id="not-owner")
    wait = _open(w)
    before = w.owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match="actor"):
        _submit(w, wait, actor=actor)
    assert w.owner._core.event_store.max_ordinal() == before
    assert w.host.inspect(wait)["status"] == "waiting"
    assert not w.calls


def test_stale_checkpoint_binding_and_superseded_wait(world):
    w = world
    old_checkpoint = _checkpoint(w)
    wait = _open(w)
    with pytest.raises(RegistryConflict, match="stale"):
        _submit(w, wait, expected_checkpoint_ref=old_checkpoint)
    other = w.owner.original_input_ref
    assert other != w.spec.binding_ref
    with pytest.raises(RegistryConflict, match="binding"):
        _submit(w, wait, binding_ref=other)
    with pytest.raises(RegistryConflict, match="binding"):
        _open(w, spec=replace(w.spec, binding_ref=other), command="wrong-binding")
    successor = _open(w, spec=replace(w.spec, supersedes_wait_ref=wait), command="v2")
    with pytest.raises(RegistryConflict, match="stale"):
        _submit(w, wait, command="old-wait")
    assert w.host.inspect(wait)["status"] == "superseded"
    assert w.host.inspect(successor)["status"] == "waiting"


def test_same_anchor_supersession_includes_changed_transition_and_port(world):
    w = world
    first = _open(w)
    alternative = replace(w.spec, transition_id="gate.alternate", input_port="gate.other_decision")
    with pytest.raises(RegistryConflict, match="supersession"):
        _open(w, spec=alternative, command="parallel-wait")
    second = _open(w, spec=replace(alternative, supersedes_wait_ref=first), command="replacement")
    with pytest.raises(RegistryConflict, match="stale"):
        _submit(w, first, command="late-first")
    _submit(w, second)
    assert w.owner.snapshot()["enabled_transitions"] == ["gate.alternate"]
    assert w.owner.admit("gate.execute", logical_tau=0, command_id="wrong-transition") is None
    _, outputs, _ = _products(w, "gate.alternate")
    w.owner.succeed(outputs, command_id="settle")
    assert w.calls == ["gate.alternate"]


@pytest.mark.parametrize("expiry_input", [True, False])
def test_deadline_requires_explicit_owner_expiry(world, monkeypatch, expiry_input):
    from cpn.rpnh.registry._event_store import run_inputs as guard
    w = world
    clock = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(guard, "now", lambda: clock)
    spec = replace(w.spec, expires_at_utc=(clock + timedelta(seconds=10)).isoformat(),
                   expiry_input=_decision("expired") if expiry_input else None)
    wait = _open(w, spec=spec)
    with pytest.raises(RegistryConflict, match="eligible"):
        w.host.expire(w.sessions["owner"], wait_ref=wait,
            expected_checkpoint_ref=_checkpoint(w), command_id="too-soon")
    clock += timedelta(seconds=10)
    with pytest.raises(RegistryConflict, match="deadline"):
        _submit(w, wait)
    with pytest.raises(RegistryConflict, match="owner"):
        w.host.expire(w.sessions["approver"], wait_ref=wait,
            expected_checkpoint_ref=_checkpoint(w), command_id="not-owner")
    if not expiry_input:
        with pytest.raises(RegistryConflict, match="expiry input"):
            w.host.expire(w.sessions["owner"], wait_ref=wait,
                expected_checkpoint_ref=_checkpoint(w), command_id="expire")
        assert w.owner.snapshot()["enabled_transitions"] == []
        return
    w.host.expire(w.sessions["owner"], wait_ref=wait,
        expected_checkpoint_ref=_checkpoint(w), command_id="expire")
    assert w.host.inspect(wait)["status"] == "expired"
    _, outputs, result = _products(w)
    w.owner.succeed(outputs, command_id="settle")
    assert result["effect"] is False


@pytest.mark.parametrize("competing", [False, True])
def test_two_concurrent_decisions_publish_one_receipt_and_dispatch(world, competing):
    w = world
    wait = _open(w)
    checkpoint = _checkpoint(w)
    start = Barrier(2)
    def submit(index):
        start.wait(timeout=10)
        try:
            return _submit(w, wait, expected_checkpoint_ref=checkpoint,
                command=("decision-" + str(index)) if competing else "same-decision",
                value="deny" if competing and index else "approve")
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    accepted = [r for r in outcomes if not isinstance(r, RegistryConflict)]
    if competing:
        assert len(accepted) == 1
    else:
        assert len(accepted) == 2
        assert accepted[0].receipt_ref == accepted[1].receipt_ref
        assert sorted(r.replayed for r in accepted) == [False, True]
    assert len(w.owner._core.event_store.object_rows_by_type(RECEIPT)) == 1
    winner_resource = w.owner._core.get_version(accepted[0].resource_ref.resource_version_id)
    winner = json.loads(w.owner._core.object_store.read_registered(winner_resource))
    _, outputs, result = _products(w)
    w.owner.succeed(outputs, command_id="settle")
    assert w.calls == ["gate.execute"]
    assert result["effect"] is (winner["decision"] == "approve")


@pytest.mark.parametrize("accepted", [False, True])
def test_resume_waiting_or_accepted_before_admission(world, accepted):
    w = world
    wait = _open(w)
    checkpoint = _checkpoint(w)
    receipt = _submit(w, wait, expected_checkpoint_ref=checkpoint) if accepted else None
    before = w.owner.snapshot()
    old_host, old_sessions = w.host, w.sessions
    _resume(w)
    assert w.owner.snapshot()["net_ref"] == before["net_ref"]
    assert w.owner.snapshot()["checkpoint_ref"] == before["checkpoint_ref"]
    assert w.host.inspect(wait)["status"] == ("accepted" if accepted else "waiting")
    with pytest.raises(StaleWriterError):
        old_host.open_wait(old_sessions["owner"], spec=w.spec, command_id="open")
    if accepted:
        replay = _submit(w, wait, expected_checkpoint_ref=checkpoint)
        assert replay.replayed and replay.receipt_ref == receipt.receipt_ref
    else:
        _submit(w, wait)
    _, outputs, _ = _products(w)
    w.owner.succeed(outputs, command_id="settle")
    assert w.calls == ["gate.execute"]


def test_exact_completion_recovery_never_redispatches_and_fences_old_writer(world):
    w = world
    wait = _open(w)
    _submit(w, wait)
    _, outputs, _ = _products(w)
    kernel, repository = w.owner.operation_repository()
    completion = record_registered_operation_completion(w.owner._core, kernel, repository,
        outputs, idempotency_key="completion-before-response-loss")
    old = _resume(w)
    assert w.owner._core.writer_epoch == completion.writer_fencing_epoch + 1
    assert w.calls == ["gate.execute"]
    assert w.owner.snapshot()["active_firings"] == []
    assert w.owner.snapshot()["enabled_transitions"] == []
    assert len(_events(w, "transition_firing_settled/v1")) == 1
    assert len(_events(w, "registered_operation_completion_recorded/v1")) == 1
    with pytest.raises(StaleWriterFence, match="stale"):
        old.succeed(outputs, command_id="stale-settlement")
    with pytest.raises(ResourceIntegrityFault):
        resume_run(_registration(w.calls), run_dir=w.path / "run", model_condition=MODEL, catalog=catalog())
    assert w.calls == ["gate.execute"]
    assert len(_events(w, "transition_firing_settled/v1")) == 1


def test_missing_completion_stays_unknown_without_writer_bump_or_redispatch(world):
    w = world
    _submit(w, _open(w))
    _products(w)
    before = w.owner._core.event_store.max_ordinal()
    epoch = w.owner._core.event_store.writer_epoch
    with pytest.raises(ResourceIntegrityFault, match="lacks one exact registered-operation completion"):
        _resume(w)
    assert w.owner._core.event_store.writer_epoch == epoch
    assert w.owner._core.event_store.max_ordinal() == before
    assert w.calls == ["gate.execute"]
    assert len(w.owner.snapshot()["active_firings"]) == 1
    assert not _events(w, "transition_firing_settled/v1")


@pytest.mark.parametrize("kind", [WAIT, RECEIPT, "petri_token/v1"])
@pytest.mark.parametrize("route", ["transaction", "direct-batch"])
def test_lowlevel_input_facts_without_native_command_are_rejected(world, monkeypatch, kind, route):
    """Replay real prepared bytes at both publication entry points without HOST authority."""
    from test_collaboration_branches import _direct_batch
    w = world
    wait = None if kind == WAIT else _open(w)
    captured = []
    def capture(**batch):
        captured.extend(o for o in batch["objects"] if o.object_type == kind)
        raise RuntimeError("capture-before-publication")
    before = w.owner._core.event_store.max_ordinal()
    with monkeypatch.context() as patch:
        patch.setattr(w.owner._core.event_store, "publish_batch", capture)
        with pytest.raises(RuntimeError, match="capture-before-publication"):
            _open(w) if kind == WAIT else _submit(w, wait)
    prepared, = captured
    tx = w.owner._core.begin(idempotency_key="forged-input-fact")
    with pytest.raises(RegistryConflict, match="native HOST"):
        if route == "transaction":
            tx._objects.append(prepared)
            tx.commit()
        else:
            _direct_batch(w.owner._core, tx, prepared)
    assert w.owner._core.event_store.max_ordinal() == before
    assert w.owner.snapshot()["enabled_transitions"] == []
    assert not w.calls


def test_lowlevel_acceptance_batch_cannot_strip_native_actor(world, monkeypatch):
    w = world
    wait = _open(w)
    original = w.owner._core.event_store.publish_batch
    intercepted = []
    def remove_command(**batch):
        intercepted.append(True)
        batch["run_input_command"] = None
        return original(**batch)
    before = w.owner._core.event_store.max_ordinal()
    with monkeypatch.context() as patch:
        patch.setattr(w.owner._core.event_store, "publish_batch", remove_command)
        with pytest.raises(RegistryConflict, match="native HOST"):
            _submit(w, wait)
    assert intercepted == [True]
    assert w.owner._core.event_store.max_ordinal() == before
    assert w.host.inspect(wait)["status"] == "waiting"


def test_decision_expires_between_acceptance_and_firing(world, monkeypatch):
    from cpn.rpnh.registry._event_store import run_inputs as guard
    w = world
    clock = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(guard, "now", lambda: clock)
    wait = _open(w, spec=replace(w.spec,
        expires_at_utc=(clock + timedelta(seconds=10)).isoformat(), expiry_input=_decision("expired")))
    accepted_checkpoint = _checkpoint(w)
    accepted = _submit(w, wait, expected_checkpoint_ref=accepted_checkpoint)
    original_receipt = w.owner._core.object_store.read_registered(
        w.owner._core.get_version(accepted.receipt_ref.version_id))
    clock += timedelta(seconds=10)
    before = _checkpoint(w)
    with pytest.raises(RegistryConflict, match="deadline|expired"):
        w.owner.admit("gate.execute", logical_tau=0, command_id="late-admission")
    assert _checkpoint(w) == before
    assert not w.owner._core.event_store.object_rows_by_type("transition_firing/v1")
    assert not w.calls
    expired = w.host.expire(w.sessions["owner"], wait_ref=wait,
        expected_checkpoint_ref=before, command_id="replace-expired-approval")
    assert expired.receipt_ref != accepted.receipt_ref
    old_doc = w.owner._core.get_version(accepted.receipt_ref.version_id).metadata
    new_doc = w.owner._core.get_version(expired.receipt_ref.version_id).metadata
    assert w.owner._core.object_store.read_registered(
        w.owner._core.get_version(accepted.receipt_ref.version_id)) == original_receipt
    assert old_doc["disposition"] == "accepted"
    assert new_doc["disposition"] == "expired"
    assert new_doc["supersedes_receipt_ref"] == ref_payload(accepted.receipt_ref)
    assert len(w.owner._core.event_store.object_rows_by_type(RECEIPT)) == 2
    cp = w.owner._core.get_version(expired.checkpoint_ref.version_id).metadata
    assert ref_payload(accepted.token_ref) not in cp["token_refs"]
    assert ref_payload(expired.token_ref) in cp["token_refs"]
    assert w.host.inspect(wait)["status"] == "expired"
    # A transport replay returns history, without reviving its former token.
    replay = _submit(w, wait, expected_checkpoint_ref=accepted_checkpoint)
    assert replay.replayed and replay.receipt_ref == accepted.receipt_ref
    assert _checkpoint(w) == expired.checkpoint_ref
    _, outputs, result = _products(w)
    w.owner.succeed(outputs, command_id="settle-expiry")
    assert result["effect"] is False
    assert w.calls == ["gate.execute"]


def test_consumed_anchor_cannot_open_a_new_wait(world):
    w = world
    _submit(w, _open(w))
    _, outputs, _ = _products(w)
    w.owner.succeed(outputs, command_id="settle")
    with pytest.raises(RegistryConflict, match="anchor|not available"):
        _open(w, command="consumed-anchor")
    assert len(w.owner._core.event_store.object_rows_by_type(WAIT)) == 1


@pytest.mark.parametrize("mutation", ["omit-decision", "omit-anchor", "cross-transition"])
def test_direct_firing_batch_cannot_bypass_exact_input_consumption(world, monkeypatch, mutation):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from test_collaboration_branches import _direct_batch
    w = world
    receipt = _submit(w, _open(w))
    core = w.owner._core
    original = core.event_store.publish_batch
    captured = []
    def capture(**batch):
        firings = [o for o in batch["objects"] if o.object_type == "transition_firing/v1"]
        if firings:
            captured.extend(firings)
            raise RuntimeError("capture-before-admission")
        return original(**batch)
    with monkeypatch.context() as patch:
        patch.setattr(core.event_store, "publish_batch", capture)
        with pytest.raises(RuntimeError, match="capture-before-admission"):
            w.owner.admit("gate.execute", logical_tau=0, command_id="admit:gate.execute")
    template, = captured
    ref = VersionRef("transition_firing/v1", new_id("transition_firing"), new_id("transition_firing_version"))
    document = dict(template.metadata, transition_firing_ref=ref_payload(ref))
    if mutation == "cross-transition":
        document["transition_id"] = "gate.alternate"
        expected = "another transition|does not belong"
    else:
        omitted = receipt.token_ref if mutation == "omit-decision" else w.spec.anchor_token_ref
        document["claimed_input_refs"] = [r for r in document["claimed_input_refs"] if r != ref_payload(omitted)]
        expected = "exact accepted input and original anchor|does not belong"
    tx = core.begin(idempotency_key="forged-firing:" + mutation)
    prepared = tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(document), metadata=document, media_type="application/json",
        schema_ref="registry_v1/transition_firing/v1", producer_invocation_id=template.producer_invocation_id)
    before = core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match=expected):
        _direct_batch(core, tx, prepared)
    assert core.event_store.max_ordinal() == before
    assert not core.event_store.object_rows_by_type("transition_firing/v1")
    assert not w.calls
    _, outputs, _ = _products(w)
    w.owner.succeed(outputs, command_id="settle")
    assert w.calls == ["gate.execute"]


def test_two_anchors_share_capacity_one_external_input_place(tmp_path):
    w = _world(tmp_path, reserve=True)
    first = _open(w)
    reserve, = (t for t in w.owner.snapshot()["marking"] if t["place"] == "gate.reserve")
    resource = _version_from_payload(reserve["resource_ref"])
    other_spec = replace(w.spec, transition_id="gate.other",
        anchor_token_ref=_version_from_payload(reserve["token_ref"]),
        binding_ref=ResourceVersionRef(resource.entity_id, resource.version_id))
    second = _open(w, spec=other_spec, command="other-anchor")
    _submit(w, first)
    before = _checkpoint(w)
    ordinal = w.owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match="capacity|live token|occupied"):
        _submit(w, second, binding_ref=other_spec.binding_ref, command="second-submit")
    assert _checkpoint(w) == before
    assert w.owner._core.event_store.max_ordinal() == ordinal
    assert w.host.inspect(second)["status"] == "waiting"
    assert len(w.owner._core.event_store.object_rows_by_type(RECEIPT)) == 1
    _, outputs, _ = _products(w)
    w.owner.succeed(outputs, command_id="first-settlement")
    settled_checkpoint = _checkpoint(w)
    _resume(w)
    assert _checkpoint(w) == settled_checkpoint
    assert w.host.inspect(second)["status"] == "waiting"
    _submit(w, second, binding_ref=other_spec.binding_ref, command="second-submit-after-consumption")
    _, outputs, result = _products(w, "gate.other")
    w.owner.succeed(outputs, command_id="second-settlement")
    assert result == {"case": "case-002", "effect": True}
    assert w.calls == ["gate.execute", "gate.other"]


def test_same_transition_can_wait_again_on_new_registered_anchor(tmp_path):
    w = _world(tmp_path, reserve=True)
    first = _open(w)
    _submit(w, first)
    _, outputs, _ = _products(w)
    w.owner.succeed(outputs, command_id="first-settlement")
    _, fed, _ = _products(w, "gate.feed")
    w.owner.succeed(fed, command_id="feed-settlement")
    new_anchor, = (t for t in w.owner.snapshot()["marking"] if t["place"] == "gate.candidate")
    resource = _version_from_payload(new_anchor["resource_ref"])
    new_spec = replace(w.spec, anchor_token_ref=_version_from_payload(new_anchor["token_ref"]),
        binding_ref=ResourceVersionRef(resource.entity_id, resource.version_id))
    assert new_spec.anchor_token_ref != w.spec.anchor_token_ref
    second = _open(w, spec=new_spec, command="next-anchor")
    _submit(w, second, binding_ref=new_spec.binding_ref, command="next-decision")
    _, outputs, result = _products(w, command="execute-second-anchor")
    w.owner.succeed(outputs, command_id="second-settlement")
    assert result == {"case": "case-002", "effect": True}
    assert w.calls == ["gate.execute", "gate.feed", "gate.execute"]
    assert len(_events(w, "transition_firing_settled/v1")) == 3
    assert w.host.inspect(first)["status"] == w.host.inspect(second)["status"] == "accepted"
