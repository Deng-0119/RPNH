"""Real Registry integration for the narrow direct-product adoption contract.

No constructed proof bags, alternate Registry authority, transport, HOST call,
native worker, or model call is used. Every positive fixture enters through
start_run, ordinary admission/start/products/succeed, and the owner edit gate.
Corruption cases explicitly damage the resulting on-disk fixture; they are not
claims that a legal owner command can produce that damaged state.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from test_static_lease_reads import TEXT, _registration, _simple_module


MODEL = "offline-terminal-owner-adoption"
_OWNERS = []


@pytest.fixture(autouse=True)
def registry_evidence(request):
    """Retain exact fixture addresses in JUnit without replacing test failures.

    Diagnostics read raw on-disk rows, so deliberately corrupted fixtures can
    still identify the damaged authority. These rows are evidence inventory,
    never passed back into the product's proof readers or marked verified.
    """
    _OWNERS.clear()
    yield
    for index, owner in enumerate(_OWNERS):
        try:
            with owner._core.event_store.connect() as db:
                kinds = ("native_run_identity/v1", "run_execution_authority/v1",
                    "net_instance/v1", "marking_checkpoint/v1", "petri_token/v1",
                    "transition_firing/v1", "operation_result/v1", "output_binding/v1",
                    "run_terminal_evidence/v1")
                rows = db.execute("SELECT object_type,logical_id,version_id,transaction_id,metadata_json "
                    "FROM objects WHERE object_type IN (" + ",".join("?" for _ in kinds) + ")", kinds).fetchall()
                objects = []
                fields = ("task_ref", "run_ref", "net_instance_ref", "previous_checkpoint_ref",
                    "latest_checkpoint_ref", "epoch", "execution_generation", "producer_ref",
                    "producer", "resource_ref", "output_resource_refs", "token_refs",
                    "terminal_occurrence_ref", "terminal_result_ref", "final_checkpoint_ref")
                for row in rows:
                    item = dict(row)
                    data = json.loads(item.pop("metadata_json"))
                    item["selected_metadata"] = {key: data[key] for key in fields if key in data}
                    objects.append(item)
                facts = [dict(row) for row in db.execute(
                    "SELECT event_id,event_type,ordinal,transaction_id,writer_fencing_epoch,payload_json "
                    "FROM events WHERE event_type IN ('net_adopted/v1','transition_firing_settled/v1') ORDER BY ordinal")]
                transactions = [dict(row) for row in db.execute(
                    "SELECT transaction_id,status,idempotency_key,writer_epoch FROM transactions ORDER BY created_at")]
                head = db.execute("SELECT MAX(ordinal) FROM events").fetchone()[0]
            request.node.user_properties.append(("registry_trace_" + str(index), json.dumps({
                "diagnostic_raw_inventory": True, "run_dir": str(owner._core.run_dir),
                "physical_head": head, "objects": objects, "facts": facts,
                "transactions": transactions}, sort_keys=True)))
        except Exception as exc:
            # Supplementary observation must never overwrite the primary test
            # result, including an expected integrity failure.
            request.node.user_properties.append(("registry_diagnostic_error_" + str(index), repr(exc)))
    _OWNERS.clear()


def make_owner(tmp_path, module=None, registration=None):
    module = module or _simple_module("A")
    registration = registration or _registration()
    task = OwnerInput(TEXT, canonical_json("task"), "Offline terminal fixture")
    owner = start_run(module, registration, run_dir=tmp_path / "run",
        task_input=task, entry_inputs={name: task for name in module.entry},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            (TEXT,), 8, 0, 8, 0),
        model_condition=MODEL, owner_statement="Bounded offline terminal adoption",
        command_id="terminal-adoption:fresh")
    _OWNERS.append(owner)
    return owner


def current(owner):
    return hydrate_module_runtime(owner._core)


def begin(owner, key="first", transition="step.run"):
    admitted = owner.admit(transition, logical_tau=0, command_id=key + ":admit")
    assert admitted is not None
    return owner.start(admitted, command_id=key + ":start")


def settle(owner, execution, key="first", place="step.result"):
    outputs = owner.products(execution, outcome_id="complete",
        products={place: (canonical_json(key + " result"),)}, command_id=key + ":products")
    result = owner.succeed(outputs, command_id=key + ":succeed")
    return outputs, result


def adopt(owner, name, *, document=None, **mapping):
    if document is None:
        document = current(owner)[1].compiled.source.to_dict()
    document = deepcopy(document)
    document["name"] = name
    return apply_replacement(owner,
        prepare_replacement(owner, ModuleDeclaration.from_dict(document), **mapping),
        command_id="replace:" + name)


def carrier(owner, place="step.result"):
    return next(token for token in current(owner)[2].tokens if token.state.place == place)


def events(owner, event_type):
    return tuple(event for event in owner._core.event_store.list_events()
                 if event.event_type == event_type)


def terminal_count(owner):
    return len(owner._core.event_store.canonical_object_rows(object_type="run_terminal_evidence/v1"))


def assert_terminal(owner, execution, product, *, settlements=1):
    expected_checkpoint = current(owner)[2].checkpoint_ref
    evidence_ref = owner.terminal()
    assert evidence_ref is not None
    evidence = owner._core.get_version(evidence_ref.version_id).metadata
    assert evidence["terminal_occurrence_ref"] == _ref_payload(
        execution.operation.firing.transition_firing_ref)
    assert evidence["terminal_result_ref"] == _ref_payload(product.as_version_ref())
    assert evidence["final_checkpoint_ref"] == _ref_payload(expected_checkpoint)
    assert len(events(owner, "transition_firing_settled/v1")) == settlements
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    assert terminal_count(owner) == 1
    head = owner._core.event_store.max_ordinal()
    assert owner.terminal() == evidence_ref
    assert owner._core.event_store.max_ordinal() == head
    return evidence_ref


def test_direct_no_replacement_control(tmp_path):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    product = carrier(owner).state.resource_ref
    assert_terminal(owner, execution, product)


def test_pending_replacement_settles_advances_once_then_terminal(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    old_net = current(owner)[0].net_ref
    execution = begin(owner)
    assert adopt(owner, "B")["status"] == "DRAINING"
    advance = owner.control.edits.advance
    calls = []
    def counted():
        calls.append(len(events(owner, "transition_firing_settled/v1")))
        return advance()
    monkeypatch.setattr(owner.control.edits, "advance", counted)
    settle(owner, execution)
    assert calls == [1]
    assert current(owner)[0].net_ref != old_net
    assert owner.control.edits.queue == []
    product = carrier(owner).state.resource_ref
    assert_terminal(owner, execution, product)
    assert calls == [1]


def test_sequential_a_b_c_preserves_original_product_and_firing(tmp_path):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    first = carrier(owner)
    nets = [current(owner)[0].net_ref]
    for name in ("B", "C"):
        assert adopt(owner, name)["status"] == "ADOPTED"
        nets.append(current(owner)[0].net_ref)
        assert carrier(owner).state.resource_ref == first.state.resource_ref
    assert len(set(nets)) == 3
    assert carrier(owner).token_ref != first.token_ref
    assert_terminal(owner, execution, first.state.resource_ref)


@pytest.mark.parametrize("suffix", [False, True])
def test_fresh_b_product_uses_only_its_actual_suffix(tmp_path, suffix):
    owner = make_owner(tmp_path)
    assert adopt(owner, "B")["status"] == "ADOPTED"
    producing_net = current(owner)[0].net_ref
    execution = begin(owner)
    settle(owner, execution)
    product = carrier(owner).state.resource_ref
    assert execution.operation.canonical.context.net_instance_ref == producing_net
    if suffix:
        assert adopt(owner, "C")["status"] == "ADOPTED"
    assert_terminal(owner, execution, product)


@pytest.mark.parametrize("change", ["terminal_config", "run_outcome", "operation_config", "capacity"])
def test_legal_contract_change_does_not_inherit_old_product(tmp_path, change):
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    document = current(owner)[1].compiled.source.to_dict()
    if change == "terminal_config":
        document["terminal"]["config"]["meaning"] = "changed"
    elif change == "run_outcome":
        document["terminal"]["config"]["run_outcome"] = "failed"
    elif change == "operation_config":
        document["components"][0]["operations"][0]["config"] = {"input_modes": {}}
    else:
        document["components"][0]["config"] = {"capacities": {"result": 2}}
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


def test_boolean_and_number_terminal_contracts_are_distinct(tmp_path):
    document = _simple_module("A").to_dict()
    document["terminal"]["config"]["typed_option"] = True
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    settle(owner, begin(owner))
    document["terminal"]["config"]["typed_option"] = 1
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


def test_contract_change_then_revert_cannot_skip_intermediate_net(tmp_path):
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    original = current(owner)[1].compiled.source.to_dict()
    changed = deepcopy(original)
    changed["terminal"]["config"]["meaning"] = "changed"
    assert adopt(owner, "B", document=changed)["status"] == "ADOPTED"
    assert adopt(owner, "C", document=original)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


@pytest.mark.parametrize("contract_change", [False, True])
def test_mapping_to_nonterminal_place_is_not_terminal_permission(tmp_path, contract_change):
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    token = carrier(owner)
    document = current(owner)[1].compiled.source.to_dict()
    if contract_change:
        document["terminal"]["config"]["meaning"] = "new contract"
    assert adopt(owner, "B", document=document, marking_mapping={
        str(token.token_ref.version_id): "step.request"})["status"] == "ADOPTED"
    assert owner.terminal() is None
    execution = begin(owner, "fresh")
    settle(owner, execution, "fresh")
    assert_terminal(owner, execution, carrier(owner).state.resource_ref, settlements=2)


def with_aside(document):
    document = deepcopy(document)
    aside = deepcopy(document["components"][0])
    aside["name"] = "aside"
    document["components"].insert(0, aside)
    document["entry"]["aside"] = {"component": "aside", "port": "request"}
    return document


def test_unrelated_settlement_after_adoption_preserves_exact_carrier(tmp_path):
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(with_aside(_simple_module("A").to_dict())))
    execution = begin(owner)
    settle(owner, execution)
    assert adopt(owner, "B")["status"] == "ADOPTED"
    adopted_checkpoint = current(owner)[2].checkpoint_ref
    exact = carrier(owner)
    settle(owner, begin(owner, "aside", "aside.run"), "aside", "aside.result")
    assert carrier(owner).token_ref == exact.token_ref
    assert carrier(owner).state == exact.state
    assert current(owner)[2].checkpoint_ref != adopted_checkpoint
    assert_terminal(owner, execution, exact.state.resource_ref, settlements=2)


def test_unrelated_operation_addition_allows_mechanical_id_renumbering(tmp_path):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    before = current(owner)[1].compiled
    product = carrier(owner).state.resource_ref
    assert adopt(owner, "B", document=with_aside(before.source.to_dict()))["status"] == "ADOPTED"
    after = current(owner)[1].compiled
    old_op = next(item for item in before.operations if item.declaration.name == "step.run")
    new_op = next(item for item in after.operations if item.declaration.name == "step.run")
    assert old_op.operation_id != new_op.operation_id
    assert_terminal(owner, execution, product)


def test_changed_lowerer_can_remove_only_nonproduct_static_read_arc(tmp_path):
    from test_static_lease_reads import lease_owner
    owner = lease_owner(tmp_path / "run")
    _OWNERS.append(owner)
    original_lower = owner.registration.resolve("component", "lease_operation")
    def without_read(config, context):
        fragment = original_lower(config, context)
        return replace(fragment, arcs=tuple(arc for arc in fragment.arcs
            if not (arc.direction == "input" and arc.place == "lease" and arc.mode == "read")))
    owner.registration.register_component("without_static_read", without_read,
        identity={"implementation_id": "tests.without_static_read", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    execution = begin(owner)
    settle(owner, execution)
    product = carrier(owner).state.resource_ref
    document = current(owner)[1].compiled.source.to_dict()
    document["components"][0]["key"] = "without_static_read"
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert any(token.state.place == "step.lease" for token in current(owner)[2].tokens)
    assert_terminal(owner, execution, product)


def test_two_same_base_pending_edits_advance_only_first(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    assert adopt(owner, "B")["status"] == "DRAINING"
    assert adopt(owner, "C")["status"] == "DRAINING"
    assert len(owner.control.edits.queue) == 2
    settle(owner, execution)
    assert current(owner)[1].compiled.source.name == "B"
    assert len(owner.control.edits.queue) == 1
    assert owner.control.edits.queue[0].compiled.source.name == "C"
    result = owner.control.edits.advance()
    assert result["status"] == "CONFLICT"
    assert current(owner)[1].compiled.source.name == "B"
    assert owner.control.edits.queue == []
    assert_terminal(owner, execution, carrier(owner).state.resource_ref)


def test_terminal_retirement_is_rejected_at_original_mapping_gate(tmp_path):
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    old_net = current(owner)[0].net_ref
    result = adopt(owner, "B", retire_token_refs=(carrier(owner).token_ref,))
    assert result["status"] == "NEEDS_MARKING_DECISION"
    assert any("terminal token cannot retire" in item["reason"] for item in result["needs"])
    assert current(owner)[0].net_ref == old_net
    assert terminal_count(owner) == 0


def test_owner_input_cannot_fabricate_terminal_at_original_mapping_gate(tmp_path):
    owner = make_owner(tmp_path)
    old_net = current(owner)[0].net_ref
    result = adopt(owner, "B", owner_inputs={"step.result": ({
        "schema_id": TEXT, "value": "fabricated", "summary": "negative fixture"},)})
    assert result["status"] == "NEEDS_MARKING_DECISION"
    assert any("cannot fabricate terminal" in item["reason"] for item in result["needs"])
    assert current(owner)[0].net_ref == old_net
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


def test_effectful_selected_operation_is_explicitly_unsupported_across_adoption(tmp_path):
    from test_static_lease_reads import TERMINAL
    from cpn.rpnh.registry.errors import TerminalReadUnsupported
    document = _simple_module("A").to_dict()
    # This other outcome is never selected or executed, but is part of the
    # selected operation's whole outcome contract and its effect closure.
    document["components"][0]["operations"][0]["outcomes"].append({
        "name": "unused", "products": [], "effects": [{"key": TERMINAL, "config": {}}]})
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    settle(owner, begin(owner))
    assert adopt(owner, "B")["status"] == "ADOPTED"
    with pytest.raises(TerminalReadUnsupported) as caught:
        owner.terminal()
    assert caught.value.reason
    assert terminal_count(owner) == 0


def test_same_net_reentry_keeps_existing_terminal_path(tmp_path):
    from cpn.rpnh.run import resume_run
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    first = assert_terminal(owner, execution, carrier(owner).state.resource_ref)
    checkpoint = current(owner)[2].checkpoint_ref
    resumed = resume_run(_registration(), run_dir=owner._core.run_dir,
        model_condition=MODEL, checkpoint_version_id=str(checkpoint.version_id),
        reopen_command_id="reenter", reopen_reason="Same-net terminal regression")
    second = resumed.terminal()
    assert second is not None and second != first
    assert resumed.terminal() == second
    assert terminal_count(resumed) == 2
    assert len(events(resumed, "transition_firing_settled/v1")) == 1


def test_reentry_then_adoption_is_unsupported_history(tmp_path):
    from cpn.rpnh.run import resume_run
    from cpn.rpnh.registry.errors import TerminalReadUnsupported
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    checkpoint = current(owner)[2].checkpoint_ref
    owner.record_owner_stop(idempotency_key="stop-for-reentry")
    resumed = resume_run(_registration(), run_dir=owner._core.run_dir,
        model_condition=MODEL, checkpoint_version_id=str(checkpoint.version_id),
        reopen_command_id="reenter", reopen_reason="Mixed history negative control")
    assert adopt(resumed, "B")["status"] == "ADOPTED"
    with pytest.raises(TerminalReadUnsupported) as caught:
        resumed.terminal()
    assert caught.value.reason
    assert terminal_count(resumed) == 0


@pytest.mark.parametrize("terminal_before_crash", [False, True])
def test_new_writer_recloses_committed_adoption_without_cached_queue(tmp_path, terminal_before_crash):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.module_terminal import register_module_terminal
    from cpn.rpnh.registry.errors import TerminalReadStale
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    assert adopt(owner, "B")["status"] == "ADOPTED"
    old_evidence = owner.terminal() if terminal_before_crash else None
    # A fresh real Core is the new writer. No pending-edit queue is recreated,
    # and no old executable or terminal material is supplied to its reader.
    recovered = _RegistryCore(owner._core.run_dir, create=False)
    assert recovered.writer_epoch == owner._core.writer_epoch + 1
    kernel = _ResourceServiceKernel(recovered)
    evidence = register_module_terminal(recovered, kernel)
    assert evidence is not None
    if old_evidence is not None:
        assert evidence == old_evidence
    assert register_module_terminal(recovered, kernel) == evidence
    assert terminal_count(owner) == 1
    with pytest.raises(TerminalReadStale) as caught:
        owner.terminal()
    assert caught.value.reason == "STALE_WRITER"


def test_direct_stale_writer_keeps_original_integrity_error(tmp_path):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.module_terminal import register_module_terminal
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    recovered = _RegistryCore(owner._core.run_dir, create=False)
    with pytest.raises(ResourceIntegrityFault, match="writer.*stale|stale.*writer"):
        owner.terminal()
    assert terminal_count(owner) == 0
    assert register_module_terminal(recovered, _ResourceServiceKernel(recovered)) is not None


def corrupt_object(owner, ref, mutate):
    """Damage a temporary Registry's actual object bytes and matching metadata."""
    document = deepcopy(dict(owner._core.get_version(ref.version_id).metadata))
    mutate(document)
    raw = canonical_json(document)
    owner._core.object_store.path_for_version(ref.version_id).write_bytes(raw)
    with owner._core.event_store.connect() as db:
        db.execute("UPDATE objects SET metadata_json=?, size=? WHERE version_id=?",
                   (raw.decode(), len(raw), str(ref.version_id)))
        db.commit()


def adopted_fixture(tmp_path):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    original = carrier(owner)
    result = adopt(owner, "B")
    assert result["status"] == "ADOPTED"
    event = next(event for event in reversed(events(owner, "net_adopted/v1"))
                 if "owner_command_ref" in event.payload)
    return owner, execution, original, event


@pytest.mark.parametrize("damage", ["result_mapping", "result_retirement", "result_raw_bytes",
    "missing_relation", "weak_relation", "target_producer", "target_epoch", "output_origin",
    "result_bundle", "checkpoint_bytes", "wrong_task"])
def test_corrupt_canonical_adoption_or_producer_cannot_close(tmp_path, damage):
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.identities import new_id
    owner, execution, original, event = adopted_fixture(tmp_path)
    mapped = carrier(owner)
    if damage.startswith("result_") and damage != "result_bundle":
        ref = _version_from_payload(event.payload["owner_command_result_ref"])
        path = owner._core.object_store.path_for_version(ref.version_id)
        document = json.loads(path.read_bytes())
        if damage == "result_mapping":
            document["data"]["token_mappings"] = []
        elif damage == "result_retirement":
            document["data"]["ordinary_retirements"] = [{
                "old_token_ref": _ref_payload(original.token_ref),
                "source_checkpoint_ref": event.payload["owner_predecessor_checkpoint_ref"]}]
        else:
            document["data"]["status"] = "DAMAGED"
        path.write_bytes(canonical_json(document))
    elif damage in ("missing_relation", "weak_relation"):
        with owner._core.event_store.connect() as db:
            statement = ("DELETE FROM relations" if damage == "missing_relation"
                         else "UPDATE relations SET strength='weak'")
            cursor = db.execute(statement + " WHERE relation_type='derived_from' "
                "AND json_extract(source_json,'$.version_id')=? "
                "AND json_extract(target_json,'$.version_id')=?",
                (str(mapped.token_ref.version_id), str(original.token_ref.version_id)))
            assert cursor.rowcount == 1
            db.commit()
    elif damage == "target_producer":
        corrupt_object(owner, mapped.token_ref, lambda doc: doc.update(producer="step.run"))
    elif damage == "target_epoch":
        corrupt_object(owner, mapped.token_ref, lambda doc: doc.update(epoch=doc["epoch"] + 1))
    elif damage == "output_origin":
        product = original.state.resource_ref
        metadata = owner._core.get_version(product.resource_version_id).metadata
        output_ref = _version_from_payload(metadata["origin"]["primary_ref"])
        corrupt_object(owner, output_ref, lambda doc: doc.update(output_port_id="wrong-port"))
    elif damage == "result_bundle":
        record = owner._core.event_store.ordered_firing_record(
            execution.operation.firing.transition_firing_ref.version_id)
        result_ref = _version_from_payload(record["firing_completion"]["operation_result_ref"])
        corrupt_object(owner, result_ref, lambda doc: doc.update(output_resource_refs=[]))
    elif damage == "checkpoint_bytes":
        path = owner._core.object_store.path_for_version(current(owner)[2].checkpoint_ref.version_id)
        path.write_bytes(path.read_bytes().replace(b'"settled":true', b'"settled":null'))
    else:
        corrupt_object(owner, current(owner)[0].team_design_root_ref,
            lambda doc: doc["task_ref"].update(logical_id=str(new_id("task"))))
    with pytest.raises((ResourceIntegrityFault, RegistryCorruptError, ObjectIntegrityError)):
        owner.terminal()
    assert terminal_count(owner) == 0


def test_nonselected_lease_corruption_is_not_ignored(tmp_path):
    from test_static_lease_reads import lease_owner
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    owner = lease_owner(tmp_path / "run")
    _OWNERS.append(owner)
    settle(owner, begin(owner))
    assert adopt(owner, "B")["status"] == "ADOPTED"
    lease = next(token for token in current(owner)[2].tokens if token.state.place == "step.lease")
    corrupt_object(owner, lease.token_ref, lambda doc: doc.update(producer="step.run"))
    with pytest.raises((ResourceIntegrityFault, RegistryCorruptError)):
        owner.terminal()
    assert terminal_count(owner) == 0


def forward_owner(tmp_path, *, defer_router=False):
    """Real same-net consumed-forward fixture, with a separately settled router."""
    from cpn.rpnh.petri_contracts import (
        ArcDeclaration, PlaceDeclaration, PortBinding, PNFragment, TransitionDeclaration)
    registration = _registration()
    def lower(config, context):
        return PNFragment(
            tuple(PlaceDeclaration(name, TEXT) for name in ("request", "result", "final")),
            tuple(TransitionDeclaration(operation.name, operation.name) for operation in context.operations),
            (ArcDeclaration("request", "run", "input"),
             ArcDeclaration("result", "run", "output", mode="produce", outcome="complete"),
             ArcDeclaration("result", "route", "input"),
             ArcDeclaration("final", "route", "output", mode="produce", outcome="complete",
                            emit="forward", forward_source="result")),
            tuple(PortBinding(port.name, "result" if port.name == "route_request" else port.name)
                  for port in context.ports), context.operations)
    registration.register_component("forward_fixture", lower,
        identity={"implementation_id": "tests.terminal_forward", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    document = _simple_module("A").to_dict()
    component = document["components"][0]
    component["key"] = "forward_fixture"
    component["ports"].extend([
        {"name": "route_request", "direction": "input", "schema": TEXT},
        {"name": "final", "direction": "output", "schema": TEXT}])
    router = deepcopy(component["operations"][0])
    router.update(name="route", inputs=["route_request"], outputs=["final"],
                  outcomes=[{"name": "complete", "products": []}])
    component["operations"].append(router)
    document["links"] = [{"source": {"component": "step", "port": "result"},
                          "target": {"component": "step", "port": "route_request"}}]
    document["exit"] = {"result": {"component": "step", "port": "final"}}
    document["terminal"]["source"]["port"] = "final"
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document), registration)
    producer = begin(owner)
    settle(owner, producer)
    product = carrier(owner).state.resource_ref
    if defer_router:
        return owner, producer, product
    route(owner)
    assert carrier(owner, "step.final").state.resource_ref == product
    return owner, producer, product


def route(owner):
    router = begin(owner, "router", "step.route")
    outputs = owner.products(router, outcome_id="complete", products={}, command_id="router:products")
    owner.succeed(outputs, command_id="router:succeed")


def test_same_net_true_forward_keeps_existing_terminal_path(tmp_path):
    owner, producer, product = forward_owner(tmp_path)
    assert_terminal(owner, producer, product, settlements=2)


def test_forward_then_adoption_is_unsupported_history(tmp_path):
    from cpn.rpnh.registry.errors import TerminalReadUnsupported
    owner, _producer, _product = forward_owner(tmp_path)
    assert adopt(owner, "B")["status"] == "ADOPTED"
    with pytest.raises(TerminalReadUnsupported) as caught:
        owner.terminal()
    assert caught.value.reason
    assert terminal_count(owner) == 0


def test_exact_read_budget_boundary_and_same_net_budget_independence(tmp_path, monkeypatch):
    from cpn.rpnh.registry._event_store.adoption_reads import AdoptionPrefixReads
    from cpn.rpnh.registry.errors import TerminalReadIncomplete
    from cpn.rpnh.registry.module_terminal import _terminal_material, register_module_terminal
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    owner, _execution, _original, _event = adopted_fixture(tmp_path)
    kernel = _ResourceServiceKernel(owner._core)
    instances = []
    original_init = AdoptionPrefixReads.__init__
    def capture(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if self.terminal:
            instances.append(self)
    monkeypatch.setattr(AdoptionPrefixReads, "__init__", capture)
    before = owner._core.event_store.max_ordinal()
    assert _terminal_material(owner._core, kernel) is not None
    assert len(instances) == 1
    count = len(instances[0]._terminal_prepared)
    assert count > 0
    assert owner._core.event_store.max_ordinal() == before
    with pytest.raises(TerminalReadIncomplete) as caught:
        register_module_terminal(owner._core, kernel, read_budget=count - 1)
    assert caught.value.reason == "READ_BUDGET_EXHAUSTED"
    assert terminal_count(owner) == 0
    assert owner._core.event_store.max_ordinal() == before
    assert register_module_terminal(owner._core, kernel, read_budget=count) is not None
    direct = make_owner(tmp_path / "direct")
    settle(direct, begin(direct))
    assert register_module_terminal(direct._core, _ResourceServiceKernel(direct._core), read_budget=0) is not None


def test_known_current_match_does_not_hide_unfinished_adopted_alternative(tmp_path, monkeypatch):
    from cpn.rpnh.registry.errors import TerminalReadIncomplete
    from cpn.rpnh.registry.module_terminal import register_module_terminal
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    document = with_aside(_simple_module("A").to_dict())
    old_terminal = deepcopy(document["terminal"])
    document["terminal"]["source"]["component"] = "aside"
    document["terminal_alternatives"] = [old_terminal]
    document["exit"]["aside"] = {"component": "aside", "port": "result"}
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    settle(owner, begin(owner))
    # Deliberately allocate the two new logical token IDs in reverse lexical
    # order. The real writer must canonicalize the checkpoint ref inventory;
    # token IDs, mapping order and all actual publications remain unchanged.
    from cpn.rpnh.registry import owner_mapping
    from cpn.rpnh.registry.identities import TypedId
    allocate_id = owner_mapping.new_id
    descending = iter(("f" * 31 + "2", "f" * 31 + "1"))
    def reversed_token_ids(kind):
        return TypedId("petri_token", next(descending)) if kind == "petri_token" else allocate_id(kind)
    with monkeypatch.context() as mapped:
        mapped.setattr(owner_mapping, "new_id", reversed_token_ids)
        assert adopt(owner, "B")["status"] == "ADOPTED"
    marking = current(owner)[2]
    assert marking.token_refs == tuple(sorted(marking.token_refs,
        key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    settle(owner, begin(owner, "aside", "aside.run"), "aside", "aside.result")
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(TerminalReadIncomplete) as caught:
        register_module_terminal(owner._core, _ResourceServiceKernel(owner._core), read_budget=0)
    assert caught.value.reason == "READ_BUDGET_EXHAUSTED"
    assert terminal_count(owner) == 0
    assert owner._core.event_store.max_ordinal() == before


@pytest.mark.parametrize("race", ["append", "writer"])
def test_explicit_mid_read_change_is_stale_and_never_publishes(tmp_path, monkeypatch, race):
    from cpn.rpnh.registry import _terminal_adoption
    from cpn.rpnh.registry.errors import TerminalReadStale
    from cpn.rpnh.registry._registry import _RegistryCore
    owner, _execution, _original, _event = adopted_fixture(tmp_path)
    original_check = _terminal_adoption.assert_terminal_cut
    calls = []
    def changed(core, cut):
        calls.append(cut.physical_head)
        if len(calls) == 2:
            if race == "append":
                owner.control.publish("result", "race:append", None, {"status": "OBSERVED"})
            else:
                _RegistryCore(owner._core.run_dir, create=False)
        return original_check(core, cut)
    monkeypatch.setattr(_terminal_adoption, "assert_terminal_cut", changed)
    with pytest.raises(TerminalReadStale) as caught:
        owner.terminal()
    assert len(calls) == 2
    assert caught.value.reason == ("STALE_CUT" if race == "append" else "STALE_WRITER")
    assert terminal_count(owner) == 0


def test_append_after_match_is_rejected_by_original_commit_cas(tmp_path, monkeypatch):
    from cpn.rpnh.registry.transaction import RegistryTransaction
    from cpn.rpnh.registry.event_store import RegistryConflict
    owner, _execution, _original, _event = adopted_fixture(tmp_path)
    commit = RegistryTransaction.commit
    injected = []
    def raced(transaction):
        if transaction.idempotency_key.startswith("run-terminal-evidence:") and not injected:
            injected.append(True)
            owner.control.publish("result", "race:precommit", None, {"status": "OBSERVED"})
        return commit(transaction)
    monkeypatch.setattr(RegistryTransaction, "commit", raced)
    with pytest.raises(RegistryConflict, match="ordinal|advanced|head"):
        owner.terminal()
    assert injected == [True]
    assert terminal_count(owner) == 0
    # With the injection removed, a newly read cut may publish once.
    monkeypatch.setattr(RegistryTransaction, "commit", commit)
    assert owner.terminal() is not None
    assert terminal_count(owner) == 1


@pytest.mark.parametrize("errno_name", ["EAGAIN", "EINTR", "ETIMEDOUT", "EIO"])
def test_unfinished_canonical_payload_read_is_incomplete_not_corruption(tmp_path, monkeypatch, errno_name):
    import errno
    from cpn.rpnh.registry.errors import TerminalReadIncomplete
    from cpn.rpnh.registry.object_store import ObjectStore
    owner, _execution, _original, event = adopted_fixture(tmp_path)
    result_ref = _version_from_payload(event.payload["owner_command_result_ref"])
    read = ObjectStore.read_registered
    attempted = []
    def unavailable(store, prepared, *args, **kwargs):
        if prepared.version_id == result_ref.version_id and store.read_only:
            attempted.append(prepared.version_id)
            raise OSError(getattr(errno, errno_name), "injected unavailable read")
        return read(store, prepared, *args, **kwargs)
    monkeypatch.setattr(ObjectStore, "read_registered", unavailable)
    with pytest.raises(TerminalReadIncomplete) as caught:
        owner.terminal()
    assert attempted == [result_ref.version_id]
    assert caught.value.reason == "STORAGE_READ_UNAVAILABLE"
    assert terminal_count(owner) == 0


def test_default_operation_budget_allows_only_mechanical_renumbering(tmp_path):
    from cpn.rpnh.compiler import compile_module
    registration = _registration()
    document = _simple_module("A").to_dict()
    document["components"][0]["operations"][0]["budget_binding"] = None
    candidate = with_aside(document)
    # The owner declares all finite bucket capacities before execution; no
    # replacement expands or silently edits the immutable budget inventory.
    ids = [operation.operation_id for operation in compile_module(
        ModuleDeclaration.from_dict(candidate), registration).operations]
    buckets = [{"bucket_id": name, "budget_scope": name,
                "finalization_scope": None, "max_attempts": 3} for name in ids]
    document["budget_buckets"] = candidate["budget_buckets"] = buckets
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document), registration)
    execution = begin(owner)
    settle(owner, execution)
    product = carrier(owner).state.resource_ref
    old_op = current(owner)[1].compiled.operations[0]
    assert old_op.declaration.budget_binding is None
    assert adopt(owner, "B", document=candidate)["status"] == "ADOPTED"
    new_op = next(operation for operation in current(owner)[1].compiled.operations
                  if operation.declaration.name == "step.run")
    assert new_op.declaration.budget_binding is None
    assert new_op.operation_id != old_op.operation_id
    assert_terminal(owner, execution, product)


def test_terminal_array_order_is_part_of_contract(tmp_path):
    document = _simple_module("A").to_dict()
    document["terminal"]["config"]["ordered"] = [True, 1]
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    settle(owner, begin(owner))
    document["terminal"]["config"]["ordered"] = [1, True]
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


@pytest.mark.parametrize("change", ["request_port", "tools", "input_schema", "input_channel", "input_minimum", "output_maximum"])
def test_other_declared_operation_port_changes_do_not_inherit(tmp_path, change):
    from test_static_lease_reads import ALT_TEXT, TERMINAL
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    document = current(owner)[1].compiled.source.to_dict()
    component = document["components"][0]
    if change == "request_port":
        component["operations"][0]["request_port"] = "request"
    elif change == "tools":
        component["operations"][0]["tools"] = [TERMINAL]
    elif change == "input_schema":
        component["ports"][0]["schema"] = ALT_TEXT
        document["required_schemas"].append(ALT_TEXT)
    elif change == "input_channel":
        component["ports"][0]["channel"] = "control"
    elif change == "input_minimum":
        component["ports"][0]["cardinality_minimum"] = 0
    else:
        component["ports"][1]["cardinality_maximum"] = 2
    if change == "request_port":
        from cpn.rpnh.registry.operations import OperationAuthorityError
        old_net = current(owner)[0].net_ref
        # A deterministic executor cannot legally acquire a model prompt.
        # Record the original gate, rather than fabricating a legal adoption.
        with pytest.raises(OperationAuthorityError, match="deterministic operation has no LLM prompt"):
            adopt(owner, "B", document=document)
        assert current(owner)[0].net_ref == old_net
        assert len(events(owner, "net_adopted/v1")) == 1
        assert terminal_count(owner) == 0
        return
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


def test_explicit_budget_change_is_not_mechanical_renumbering(tmp_path):
    document = _simple_module("A").to_dict()
    other = {"bucket_id": "other", "budget_scope": "other", "finalization_scope": None}
    document["budget_buckets"].append({**other, "max_attempts": 3})
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    settle(owner, begin(owner))
    document["components"][0]["operations"][0]["budget_binding"] = other
    assert adopt(owner, "B", document=document)["status"] == "ADOPTED"
    assert owner.terminal() is None
    assert terminal_count(owner) == 0


@pytest.mark.parametrize("replacement", [False, True])
def test_duplicate_success_keeps_one_original_settlement(tmp_path, replacement):
    from cpn.rpnh.registry.errors import StaleInvocationContext
    owner = make_owner(tmp_path)
    execution = begin(owner)
    if replacement:
        assert adopt(owner, "B")["status"] == "DRAINING"
    outputs, _result = settle(owner, execution)
    before = owner._core.event_store.max_ordinal()
    # This existing public callback rejects a closed/stale invocation; only
    # the completion record and terminal methods promise successful replay.
    with pytest.raises(StaleInvocationContext, match="closed|active net head"):
        owner.succeed(outputs, command_id="first:succeed")
    assert owner._core.event_store.max_ordinal() == before
    assert_terminal(owner, execution, carrier(owner).state.resource_ref)


def test_completed_recovery_advances_once_and_uses_original_product(tmp_path, monkeypatch):
    from cpn.rpnh.run import resume_run
    from cpn.rpnh.owner_edits import OwnerEdits
    from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
    owner = make_owner(tmp_path)
    execution = begin(owner)
    outputs = owner.products(execution, outcome_id="complete",
        products={"step.result": (canonical_json("durable result"),)}, command_id="products")
    kernel, repository = owner.operation_repository()
    first = record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key="completion:first")
    again = record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key="completion:duplicate")
    assert first.event_id == again.event_id
    advance = OwnerEdits.advance
    calls = []
    def counted(edits):
        calls.append(len(events(edits.owner, "transition_firing_settled/v1")))
        return advance(edits)
    monkeypatch.setattr(OwnerEdits, "advance", counted)
    recovered = resume_run(_registration(), run_dir=owner._core.run_dir, model_condition=MODEL)
    assert calls == [1]
    assert len(events(recovered, "registered_operation_completion_recorded/v1")) == 1
    assert len(events(recovered, "transition_firing_settled/v1")) == 1
    assert adopt(recovered, "B")["status"] == "ADOPTED"
    assert_terminal(recovered, execution, carrier(recovered).state.resource_ref)


def test_interrupted_recovery_advances_without_inventing_terminal_product(tmp_path, monkeypatch):
    from cpn.rpnh.run import resume_run
    from cpn.rpnh.owner_edits import OwnerEdits
    document = _simple_module("A").to_dict()
    document["components"][0]["operations"][0]["outcomes"].append({"name": "interrupted", "products": []})
    owner = make_owner(tmp_path, ModuleDeclaration.from_dict(document))
    checkpoint = current(owner)[2].checkpoint_ref
    begin(owner)
    advance = OwnerEdits.advance
    calls = []
    def counted(edits):
        calls.append(len(events(edits.owner, "transition_firing_settled/v1")))
        return advance(edits)
    monkeypatch.setattr(OwnerEdits, "advance", counted)
    recovered = resume_run(_registration(), run_dir=owner._core.run_dir, model_condition=MODEL,
        checkpoint_version_id=str(checkpoint.version_id), reopen_command_id="interrupt-reenter",
        reopen_reason="Offline interrupted recovery control")
    assert calls == [1]
    assert len(events(recovered, "transition_firing_settled/v1")) == 1
    assert adopt(recovered, "B")["status"] == "ADOPTED"
    assert recovered.terminal() is None
    assert terminal_count(recovered) == 0


def test_mapping_prewrite_without_adoption_cannot_select_orphan_carrier(tmp_path, monkeypatch):
    from cpn.rpnh import owner_edits
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_terminal import register_module_terminal
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    owner = make_owner(tmp_path)
    execution = begin(owner)
    settle(owner, execution)
    old_net = current(owner)[0].net_ref
    product = carrier(owner).state.resource_ref
    old_token_count = len(owner._core.event_store.object_rows_by_type("petri_token/v1"))
    def crash_before_adoption(*args, **kwargs):
        raise RuntimeError("injected crash after mapping, before adoption")
    monkeypatch.setattr(owner_edits, "stage_owner_adoption", crash_before_adoption)
    # Exercise the retained standalone allocator explicitly. Normal OwnerEdits
    # now stages mapping in the adoption transaction and leaves no registered
    # orphan tokens on this failure; that path has its own integration check.
    from cpn.rpnh.registry import owner_mapping
    standalone_allocate = owner_mapping.allocate_owner_mapping
    def legacy_mapping(*args, **kwargs):
        kwargs["transaction"] = None
        return standalone_allocate(*args, **kwargs)
    monkeypatch.setattr(owner_mapping, "allocate_owner_mapping", legacy_mapping)
    with pytest.raises(RuntimeError, match="injected crash after mapping"):
        adopt(owner, "B")
    assert len(owner._core.event_store.object_rows_by_type("petri_token/v1")) > old_token_count
    assert current(owner)[0].net_ref == old_net
    assert len(events(owner, "net_adopted/v1")) == 1
    recovered = _RegistryCore(owner._core.run_dir, create=False)
    assert hydrate_module_runtime(recovered)[0].net_ref == old_net
    evidence = register_module_terminal(recovered, _ResourceServiceKernel(recovered))
    assert evidence is not None
    data = recovered.get_version(evidence.version_id).metadata
    assert data["terminal_result_ref"] == _ref_payload(product.as_version_ref())
    assert data["terminal_occurrence_ref"] == _ref_payload(execution.operation.firing.transition_firing_ref)
    assert terminal_count(owner) == 1


def test_adoption_then_forward_is_unsupported_history(tmp_path):
    from cpn.rpnh.registry.errors import TerminalReadUnsupported
    owner, _producer, product = forward_owner(tmp_path, defer_router=True)
    assert adopt(owner, "B")["status"] == "ADOPTED"
    route(owner)
    assert carrier(owner, "step.final").state.resource_ref == product
    with pytest.raises(TerminalReadUnsupported) as caught:
        owner.terminal()
    assert caught.value.reason
    assert terminal_count(owner) == 0
    assert len(events(owner, "transition_firing_settled/v1")) == 2


def test_adoption_then_reentry_is_unsupported_history(tmp_path):
    from cpn.rpnh.run import resume_run
    from cpn.rpnh.registry.errors import TerminalReadUnsupported
    owner = make_owner(tmp_path)
    settle(owner, begin(owner))
    assert adopt(owner, "B")["status"] == "ADOPTED"
    checkpoint = current(owner)[2].checkpoint_ref
    owner.record_owner_stop(idempotency_key="adopted:stop-for-reentry")
    recovered = resume_run(_registration(), run_dir=owner._core.run_dir,
        model_condition=MODEL, checkpoint_version_id=str(checkpoint.version_id),
        reopen_command_id="adopted:reentry", reopen_reason="Post-adoption mixed history control")
    with pytest.raises(TerminalReadUnsupported) as caught:
        recovered.terminal()
    assert caught.value.reason
    assert terminal_count(recovered) == 0
    assert len(events(recovered, "transition_firing_settled/v1")) == 1


def physical_owner_result_fixture(tmp_path, *, fresh_current_product):
    if not fresh_current_product:
        owner, _execution, _original, event = adopted_fixture(tmp_path)
    else:
        owner = make_owner(tmp_path)
        assert adopt(owner, "B")["status"] == "ADOPTED"
        settle(owner, begin(owner))
        event = next(event for event in reversed(events(owner, "net_adopted/v1"))
                     if "owner_command_ref" in event.payload)
    result_ref = _version_from_payload(event.payload["owner_command_result_ref"])
    return owner, owner._core.object_store.path_for_version(result_ref.version_id)


@pytest.mark.parametrize("fresh_current_product", [False, True], ids=["adopted-product", "fresh-current-product"])
@pytest.mark.parametrize("errno_name", ["EAGAIN", "EINTR", "ETIMEDOUT", "EIO"])
def test_physical_owner_result_read_classifies_only_cross_net_unavailability(
        tmp_path, monkeypatch, fresh_current_product, errno_name):
    """Exercise the physical read before the new terminal-mode reader exists.

    Earlier hydration still reads owner witnesses through Path.read_bytes.
    A fresh current-net product after adoption must retain that legacy error,
    whereas an adopted old product requires an explicit incomplete result.
    """
    import errno
    from pathlib import Path
    from cpn.rpnh.registry.errors import TerminalReadIncomplete
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    owner, payload_path = physical_owner_result_fixture(
        tmp_path, fresh_current_product=fresh_current_product)
    read_bytes = Path.read_bytes
    attempted = []
    def unavailable(path):
        if path == payload_path:
            attempted.append(str(path))
            raise OSError(getattr(errno, errno_name), "injected physical owner-result read failure")
        return read_bytes(path)
    before = owner._core.event_store.max_ordinal()
    monkeypatch.setattr(Path, "read_bytes", unavailable)
    expected = RegistryCorruptError if fresh_current_product else TerminalReadIncomplete
    with pytest.raises(expected) as caught:
        owner.terminal()
    if not fresh_current_product:
        assert caught.value.reason == "STORAGE_READ_UNAVAILABLE"
    assert attempted == [str(payload_path)]
    assert terminal_count(owner) == 0
    assert owner._core.event_store.max_ordinal() == before


@pytest.mark.parametrize("fresh_current_product", [False, True], ids=["adopted-product", "fresh-current-product"])
def test_physically_missing_owner_result_is_integrity_not_incomplete(tmp_path, fresh_current_product):
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    owner, payload_path = physical_owner_result_fixture(
        tmp_path, fresh_current_product=fresh_current_product)
    assert payload_path.is_file()
    payload_path.unlink()
    before = owner._core.event_store.max_ordinal()
    with pytest.raises((RegistryCorruptError, ResourceIntegrityFault, ObjectIntegrityError)):
        owner.terminal()
    assert terminal_count(owner) == 0
    assert owner._core.event_store.max_ordinal() == before
