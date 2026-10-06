"""Finite JR15--19/N02 with real SQLite and explicit mock product bytes.

Never invoke the registered executor/terminal implementation. Actual Start,
registered products and original Success are deliberately separate API calls.
"""
from dataclasses import replace
import hashlib
import json

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.errors import DeliveryOutcomeUnknown
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
from cpn.rpnh.collaboration.schema_catalog import workset_schema_data
from cpn.rpnh.collaboration.worksets import (
    WorksetOwner, WorksetExpectation, ExportResult, AcceptDelivery, Contribute,
    CompleteWorkset, bind_local_workset_source, read_record, qualified,
    prepare_local_delivery, finish_local_delivery, WORKSET, EXPORT, ACCEPTANCE,
    CONTRIBUTION, ROOT_TERMINAL,
    _stage, record_ref, command_key,
)

TEXT = "application/workset_static_text/v1"
EXECUTOR = "test/workset-never-execute/v1"
TERMINAL = "test/workset-never-call-terminal/v1"
PAYLOAD = canonical_json("explicit finite static result")


def forbidden_executor(**kwargs):
    raise AssertionError("N02 static fixture must never dispatch an executor or terminal callback")


def _module(names):
    bucket = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    def component(name):
        return {"name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                      {"name": "result", "direction": "output", "schema": TEXT}],
            "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request"],
                "outputs": ["result"], "request_port": None, "tools": [], "config": {},
                "budget_binding": bucket,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}], "effects": []}]}]}
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1", "name": "StaticWorkset",
        "components": [component(name) for name in names],
        "links": [{"source": {"component": left, "port": "result"},
                   "target": {"component": right, "port": "request"}} for left, right in zip(names, names[1:])],
        "entry": {"request": {"component": names[0], "port": "request"}},
        "exit": {"result": {"component": names[-1], "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": names[-1], "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT], "budgets": {},
        "budget_buckets": [{**bucket, "max_attempts": 8}]})


def _owner(path, source, names=("accept", "contribute", "root"), *, opt_in=True):
    schemas, types, paths = workset_schema_data() if opt_in else ({}, (), {})
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {"$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    registration.register_executor(EXECUTOR, forbidden_executor,
        identity={"implementation_id": "tests.workset-static", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                   "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, forbidden_executor,
        identity={"implementation_id": "tests.workset-static-terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    module = _module(names)
    value = OwnerInput(TEXT, canonical_json("explicit fixture request"), "Static owner input")
    owner = start_run(module, registration, run_dir=path, task_input=value, entry_inputs={"request": value},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), ("rpnh/module_declaration/v1",), 8, 0, 8, 0),
        model_condition="offline-static-workset-no-model", owner_statement="Explicit finite mock products; no executor",
        command_id="fixture:" + source, catalog=catalog,
        host_execution_bindings=None, configuration_sources=None)
    if opt_in:
        owner.schema_gateway.bind_source_identity(source_id=source, command_id="bind:" + source)
    assert owner.host_execution_bindings is None
    assert owner.control.edits.queue == []
    assert all(not outcome.effects for component in module.components for operation in component.operations for outcome in operation.outcomes)
    assert getattr(owner, "revision_candidate_publisher", None) is None
    return owner


def _refs(owner, source):
    return (SourceQualifiedVersionRef(source, owner.identity.task_ref),
            SourceQualifiedVersionRef(source, owner.original_input_ref.as_version_ref()))


def _qual(core, document):
    return SourceQualifiedVersionRef.from_dict(document["record_ref"], catalog=core.catalog)


def _records(owner, kind):
    return [read_record(owner._core, json.loads(row["metadata_json"])["record_ref"])
            for row in owner._core.event_store.canonical_object_rows(object_type=kind)]


def _current(owner):
    return max(_records(owner, WORKSET), key=lambda value: value["body"]["sequence"])


def _start(owner, name, key):
    assert owner.control.edits.queue == [] and owner.host_execution_bindings is None
    admitted = owner.admit(name + ".run", logical_tau=0, command_id=key + ":admit", prepare_admission=None)
    assert admitted is not None
    binding = owner._core.get_version(admitted.admission.context.operation_binding_ref.version_id)
    assert binding.metadata.get("workspace_binding_ref") is None
    return owner.start(admitted, command_id=key + ":start")


def _products(owner, execution, key, payload=PAYLOAD):
    assert owner.control.edits.queue == [] and getattr(owner, "revision_candidate_publisher", None) is None
    binding = owner._core.get_version(execution.operation.canonical.context.operation_binding_ref.version_id)
    assert binding.metadata.get("workspace_binding_ref") is None
    port = execution.operation.firing.transition_id.rsplit(".", 1)[0] + ".result"
    return owner.products(execution, outcome_id="complete", products={port: (payload,)}, command_id=key + ":products")


def test_workset_growth_seal_compare_version_head_command_replay_reopen(tmp_path):
    owner = _owner(tmp_path / "target", "target")
    service = WorksetOwner(owner)
    requirements, inputs = _refs(owner, "target")
    initial = service.create(requirements_ref=requirements, input_binding_ref=inputs,
        generation=0, expected_slots=("a",), command_id="workset:create")
    assert service.create(requirements_ref=requirements, input_binding_ref=inputs,
        generation=0, expected_slots=("a",), command_id="workset:create") == initial
    old = WorksetExpectation.from_record(owner._core, initial)
    # Both real transactions capture the same initial owner cut before either commits.
    growth_tx = owner._core.begin(idempotency_key=command_key("workset:grow"))
    seal_tx = owner._core.begin(idempotency_key=command_key("workset:stale-seal"))
    growth_body = {**initial["body"], "action": "grow", "expected": old.to_dict(), "sequence": 2,
                   "collection_version": 2, "expected_slots": ["a", "b"]}
    seal_body = {**initial["body"], "action": "seal", "expected": old.to_dict(), "sequence": 2, "state": "sealed"}
    for tx, body, command in ((growth_tx, growth_body, "workset:grow"), (seal_tx, seal_body, "workset:stale-seal")):
        ref = VersionRef(WORKSET, old.record_ref.ref.entity_id, record_ref(WORKSET, owner._core.task_id, "unused", command).version_id)
        _stage(owner._core, tx, WORKSET, ref, body, command_id=command)
    growth_tx.commit()
    with pytest.raises(RegistryConflict, match="stale Workset"):
        seal_tx.commit()
    grown = _current(owner)
    expectation = WorksetExpectation.from_record(owner._core, grown)
    with pytest.raises(RegistryConflict, match="stale Workset"):
        service.change(replace(expectation, command_id="wrong prior command"), action="seal", command_id="workset:bad-command")
    with pytest.raises(RegistryConflict, match="stale Workset"):
        service.change(replace(expectation, stream_head=1), action="seal", command_id="workset:bad-head")
    sealed = service.change(expectation, action="seal", command_id="workset:seal")
    with pytest.raises(RegistryConflict, match="open larger"):
        service.change(WorksetExpectation.from_record(owner._core, sealed), action="grow",
                       expected_slots=("a", "b", "c"), command_id="workset:grow-closed")
    reopened = _RegistryCore(owner._core.run_dir, create=False, read_only=True, catalog=owner._core.catalog)
    assert read_record(reopened, sealed["record_ref"]) == sealed
    assert len(_records(owner, WORKSET)) == 3


def test_n02_two_physical_one_acceptance_real_success_contribution_root_viewer(tmp_path):
    source = _owner(tmp_path / "source", "source", ("produce", "send"))
    target = _owner(tmp_path / "target", "target")
    sources, targets = WorksetOwner(source), WorksetOwner(target)
    source_requirements, source_input = _refs(source, "source")
    request = sources.request(requirements_ref=source_requirements, input_binding_ref=source_input, command_id="source:request")
    produce = _start(source, "produce", "source:produce")
    source_outputs = _products(source, produce, "source:produce")
    product_row = source._core.event_store.object_row(source_outputs.outputs[0].resource_ref.resource_version_id)
    with source._core.event_store.connect() as db:
        original_principal = db.execute("SELECT producer_principal FROM events WHERE event_id=?",
                                        (product_row["published_event_id"],)).fetchone()[0]
        db.execute("UPDATE events SET producer_principal=? WHERE event_id=?",
                   (str(source.principal_ref.entity_id), product_row["published_event_id"]))
    before_bad_publication = source._core.event_store.max_ordinal()
    try:
        with pytest.raises(RegistryConflict, match="ordinary output publication/header/origin"):
            source.succeed(source_outputs, command_id="source:reject-output-actor", workset_action=ExportResult(
                _qual(source._core, request), source_outputs.outputs[0].port_id))
    finally:
        with source._core.event_store.connect() as db:
            db.execute("UPDATE events SET producer_principal=? WHERE event_id=?",
                       (original_principal, product_row["published_event_id"]))
    assert source._core.event_store.max_ordinal() == before_bad_publication
    assert _records(source, EXPORT) == []
    source.succeed(source_outputs, command_id="source:export", workset_action=ExportResult(
        _qual(source._core, request), source_outputs.outputs[0].port_id))
    exported, = _records(source, EXPORT)
    requirements, inputs = _refs(target, "target")
    workset = targets.create(requirements_ref=requirements, input_binding_ref=inputs,
        generation=0, expected_slots=("answer",), command_id="target:workset")
    sealed = targets.change(WorksetExpectation.from_record(target._core, workset), action="seal", command_id="target:seal")
    delivery = sources.logical_delivery(export_ref=_qual(source._core, exported), target_workset=sealed,
                                        slot="answer", command_id="source:logical")
    delivery_ref = _qual(source._core, delivery)
    sending = _start(source, "send", "source:send")
    first = prepare_local_delivery(source, sending, logical_delivery_ref=delivery_ref, command_id="source:P1")
    before_replay = source._core.event_store.max_ordinal()
    assert prepare_local_delivery(source, sending, logical_delivery_ref=delivery_ref, command_id="source:P1") is first
    assert source._core.event_store.max_ordinal() == before_replay
    bind_local_workset_source(target, source, source_id="source")
    accept_execution = _start(target, "accept", "target:accept")
    accept_outputs = _products(target, accept_execution, "target:accept", first.payload)
    assert _records(target, ACCEPTANCE) == []
    expected = WorksetExpectation.from_record(target._core, sealed)
    before_rejection = target._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match="source or exact reference"):
        target.succeed(accept_outputs, command_id="target:reject-source", workset_action=AcceptDelivery(
            expected, delivery_ref, SourceQualifiedVersionRef("imposter", first.physical_delivery_ref.ref),
            accept_outputs.outputs[0].port_id, "answer", "new"))
    assert target._core.event_store.max_ordinal() == before_rejection
    witness_path = source._core.object_store.path_for_version(first.release.witness_ref.version_id)
    original_witness = witness_path.read_bytes()
    corrupted_witness = json.loads(original_witness)
    corrupted_witness["boundary"] = "tool_result"
    try:
        witness_path.write_bytes(canonical_json(corrupted_witness))
        with pytest.raises(RegistryConflict, match="descriptor"):
            target.succeed(accept_outputs, command_id="target:reject-witness", workset_action=AcceptDelivery(
                expected, delivery_ref, first.physical_delivery_ref, accept_outputs.outputs[0].port_id, "answer", "new"))
    finally:
        witness_path.write_bytes(original_witness)
    assert target._core.event_store.max_ordinal() == before_rejection
    assert _records(target, ACCEPTANCE) == []
    acceptance = targets.accept_delivery(first, expected=expected, slot="answer", decision="new",
        command_id="target:accept-success", outputs=accept_outputs, output_port=accept_outputs.outputs[0].port_id)
    assert _records(target, ACCEPTANCE) == [acceptance]
    a_ref = _qual(target._core, acceptance)
    a_body = acceptance["body"]
    a_row = target._core.event_store.object_row(a_ref.ref.version_id)
    o_row = target._core.event_store.object_row(a_body["occurrence_ref"]["version_id"])
    assert a_row["transaction_id"] == o_row["transaction_id"]
    from cpn.frontend.worksets import workset_view
    target_reader = _RegistryCore(target._core.run_dir, create=False, read_only=True, catalog=target._core.catalog)
    accepted_view = workset_view(target_reader)
    assert accepted_view["current"][0]["contribution_count"] is None
    assert accepted_view["current"][0]["contribution_coverage"] == "not_provided"
    assert accepted_view["current"][0]["occurrence_count"] == accepted_view["current"][0]["available_occurrence_count"] == 1
    # Target committed A/O, but the source did not establish that acknowledgement.
    unknown = finish_local_delivery(source, first, outcome="unknown", command_id="source:P1")
    second = prepare_local_delivery(source, sending, logical_delivery_ref=delivery_ref, command_id="source:P2")
    before_retry = target._core.event_store.max_ordinal()
    assert targets.accept_delivery(second, expected=expected, slot="answer", decision="new",
                                   command_id="target:retry") == acceptance
    assert target._core.event_store.max_ordinal() == before_retry
    assert targets.lookup_acceptance(delivery_ref=delivery_ref, target_workset=sealed,
        slot="answer", content_digest=hashlib.sha256(second.payload).hexdigest()) == acceptance
    with pytest.raises(RegistryConflict, match="different content"):
        targets.lookup_acceptance(delivery_ref=delivery_ref, target_workset=sealed,
            slot="answer", content_digest=hashlib.sha256(b"other").hexdigest())
    another_logical = sources.logical_delivery(export_ref=_qual(source._core, exported), target_workset=sealed,
                                              slot="answer", command_id="source:distinct-logical")
    assert targets.lookup_acceptance(delivery_ref=_qual(source._core, another_logical), target_workset=sealed,
        slot="answer", content_digest=hashlib.sha256(second.payload).hexdigest()) is None
    acknowledged = finish_local_delivery(source, second, outcome="acknowledged", command_id="source:P2",
        target_owner=target, acceptance_ref=a_ref)
    assert unknown.outcome == "unknown" and acknowledged.outcome == "acknowledged"
    source.succeed(_products(source, sending, "source:send"), command_id="source:send-success")
    unknown_row = source._core.event_store.object_row(unknown.delivery_ref.version_id)
    assert json.loads(unknown_row["metadata_json"])["state"] == "unknown"
    wrong_physical = next(row for row in source._core.event_store.canonical_object_rows(object_type="resource_delivery/v1")
                          if json.loads(row["metadata_json"]).get("state") == "acknowledged"
                          and json.loads(row["metadata_json"]).get("boundary") == "petri_input")
    from cpn.rpnh.registry.identities import TypedId
    with pytest.raises(RegistryConflict, match="physical delivery closure"):
        targets.reconcile(logical_delivery_ref=delivery_ref,
            physical_delivery_ref=SourceQualifiedVersionRef("source", VersionRef("resource_delivery/v1",
                TypedId.parse(wrong_physical["logical_id"]), TypedId.parse(wrong_physical["version_id"]))),
            acceptance_ref=a_ref, original_outcome="acknowledged", command_id="target:reject-unrelated-physical")
    targets.reconcile(logical_delivery_ref=delivery_ref,
        physical_delivery_ref=SourceQualifiedVersionRef("source", unknown.delivery_ref),
        acceptance_ref=a_ref, original_outcome="unknown", command_id="target:reconcile-P1")
    targets.reconcile(logical_delivery_ref=delivery_ref,
        physical_delivery_ref=SourceQualifiedVersionRef("source", unknown.delivery_ref),
        acceptance_ref=a_ref, original_outcome="unknown", command_id="target:reconcile-P1-again")
    targets.reconcile(logical_delivery_ref=delivery_ref,
        physical_delivery_ref=SourceQualifiedVersionRef("source", acknowledged.delivery_ref),
        acceptance_ref=a_ref, original_outcome="acknowledged", command_id="target:reconcile-P2")
    contribution_execution = _start(target, "contribute", "target:contribute")
    assert workset_view(target_reader)["current"][0]["available_occurrence_count"] is None
    contribution_outputs = _products(target, contribution_execution, "target:contribute")
    target.succeed(contribution_outputs, command_id="target:contribute-success", workset_action=Contribute(
        WorksetExpectation.from_record(target._core, _current(target)), a_ref,
        contribution_outputs.outputs[0].port_id, "answer"))
    root_execution = _start(target, "root", "target:root")
    root_outputs = _products(target, root_execution, "target:root")
    target.succeed(root_outputs, command_id="target:root-success", workset_action=CompleteWorkset(
        WorksetExpectation.from_record(target._core, _current(target)), root_outputs.outputs[0].port_id))
    assert target.terminal() is not None
    assert _current(target)["body"]["state"] == "completed"
    assert len(_records(target, ACCEPTANCE)) == len(_records(target, CONTRIBUTION)) == len(_records(target, ROOT_TERMINAL)) == 1
    assert targets.lookup_acceptance(delivery_ref=delivery_ref, target_workset=sealed,
        slot="answer", content_digest=hashlib.sha256(second.payload).hexdigest()) == acceptance
    with pytest.raises(RegistryConflict, match="cannot be reopened"):
        targets.change(WorksetExpectation.from_record(target._core, _current(target)), action="seal", command_id="target:late")
    from cpn.frontend.worksets import workset_view
    readonly = _RegistryCore(target._core.run_dir, create=False, read_only=True, catalog=target._core.catalog)
    before = target._core.event_store.max_ordinal()
    view = workset_view(readonly)
    assert view["current"][0]["state"] == "completed"
    assert view["current"][0]["acceptance_count"] == view["current"][0]["contribution_count"] == 1
    assert view["current"][0]["root_terminal_evidence_ref"] is not None
    assert len(view["history"]) == 5
    assert view["current"][0]["physical_attempt_count"] is None
    source_reader = _RegistryCore(source._core.run_dir, create=False, read_only=True, catalog=source._core.catalog)
    view = workset_view(readonly, source_readers={"source": source_reader})
    assert view["current"][0]["physical_attempt_count"] == 2
    assert view["current"][0]["occurrence_count"] == 1
    assert view["current"][0]["available_occurrence_count"] == 0
    assert len(view["current"][0]["recorded_physical_attempts"]) == 2
    assert len(next(item for item in view["current"][0]["recorded_physical_attempts"]
                    if item["original_outcome"] == "unknown")["reconciliation_refs"]) == 2
    assert {item["outcome"] for item in view["current"][0]["physical_attempts"]} == {"unknown", "acknowledged"}
    from cpn.frontend.dashboard import RegistryDashboard
    from cpn.frontend.server import handle_request
    provider = RegistryDashboard(target._core.run_dir, catalog=target._core.catalog,
                                 workset_source_readers={"source": source_reader})
    response = handle_request(provider, "GET", "/api/v2/worksets")
    assert response.status == 200 and json.loads(response.body) == view
    assert handle_request(provider, "HEAD", "/api/v2/worksets").body == b""
    assert handle_request(provider, "GET", "/api/v2/worksets?invented=1").status == 400
    assert target._core.event_store.max_ordinal() == before
    (tmp_path / "n02-view.json").write_text(json.dumps(view, indent=2))


def test_physical_command_replay_and_reopen_lost_channel_never_reconsume(tmp_path):
    source = _owner(tmp_path / "source", "source", ("produce", "send"))
    service = WorksetOwner(source)
    requirements, inputs = _refs(source, "source")
    request = service.request(requirements_ref=requirements, input_binding_ref=inputs, command_id="source:request")
    execution = _start(source, "produce", "source:produce")
    outputs = _products(source, execution, "source:produce")
    source.succeed(outputs, command_id="source:export", workset_action=ExportResult(
        _qual(source._core, request), outputs.outputs[0].port_id))
    workset = service.create(requirements_ref=requirements, input_binding_ref=inputs, generation=0,
        expected_slots=("a",), command_id="source:scope")
    export, = _records(source, EXPORT)
    delivery = service.logical_delivery(export_ref=_qual(source._core, export), target_workset=workset,
        slot="a", command_id="source:logical")
    execution = _start(source, "send", "source:send")
    reference = _qual(source._core, delivery)
    attempt = prepare_local_delivery(source, execution, logical_delivery_ref=reference, command_id="source:P1")
    before = source._core.event_store.max_ordinal()
    assert prepare_local_delivery(source, execution, logical_delivery_ref=reference, command_id="source:P1") is attempt
    source._core._workset_delivery_attempts.clear()
    with pytest.raises(DeliveryOutcomeUnknown, match="no live broker channel"):
        prepare_local_delivery(source, execution, logical_delivery_ref=reference, command_id="source:P1")
    assert source._core.event_store.max_ordinal() == before
    reopened = _RegistryCore(source._core.run_dir, create=False, read_only=True, catalog=source._core.catalog)
    rows = reopened.event_store.object_rows_by_logical(attempt.physical_delivery_ref.ref.entity_id,
                                                     object_type="resource_delivery/v1")
    assert {json.loads(row["metadata_json"])["state"] for row in rows} == {"prepared", "release_authorized"}
    assert reopened.event_store.max_ordinal() == before


def test_ordinary_success_without_opt_in_is_unchanged(tmp_path):
    owner = _owner(tmp_path / "ordinary", "legacy", ("ordinary",), opt_in=False)
    execution = _start(owner, "ordinary", "legacy")
    owner.succeed(_products(owner, execution, "legacy"), command_id="legacy:success")
    assert owner.terminal() is not None
    assert owner._core.event_store.canonical_object_rows(object_type=WORKSET) == ()


def test_workset_commit_rejects_forged_plain_pn_colour(tmp_path, monkeypatch):
    owner = _owner(tmp_path / "source", "source", ("produce",))
    service = WorksetOwner(owner)
    requirements, inputs = _refs(owner, "source")
    request = service.request(requirements_ref=requirements, input_binding_ref=inputs, command_id="source:request")
    execution = _start(owner, "produce", "source:produce")
    outputs = _products(owner, execution, "source:produce")
    from cpn.rpnh.registry import success_projection
    original = success_projection.project_module_firing_success
    def forged(*args, **kwargs):
        projected, tokens = original(*args, **kwargs)
        changed = {ref: replace(state, verdict="forged") for ref, state in tokens}
        return replace(projected, tokens=tuple(replace(item, state=changed.get(item.token_ref, item.state))
            for item in projected.tokens)), tuple((ref, changed[ref]) for ref, _state in tokens)
    monkeypatch.setattr(success_projection, "project_module_firing_success", forged)
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict, match="complete local successor"):
        owner.succeed(outputs, command_id="source:forged-success", workset_action=ExportResult(
            _qual(owner._core, request), outputs.outputs[0].port_id))
    assert owner._core.event_store.max_ordinal() == before
    assert _records(owner, EXPORT) == []
