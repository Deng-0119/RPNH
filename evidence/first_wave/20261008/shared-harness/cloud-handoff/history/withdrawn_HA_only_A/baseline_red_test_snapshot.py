"""Deterministic terminal projection contracts using temporary real Registries.

Fixture owners admit/settle explicit static products. They never dispatch an
executor, start an owner socket, invoke a provider, or run an Office scorer.
Reentry is fixture construction only; every export is guarded read-only.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.run_authority import current_run_execution_authority
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run, resume_run
from rpnh_ha import registry_export as exporter
from rpnh_ha.projection import ObservationCollector

TEXT = "application/ha_terminal_test_text/v1"
EXECUTOR = "test/ha-terminal-never-dispatch/v1"
TERMINAL = "test/ha-terminal/v1"
MODEL = "offline-static-no-provider"
ROLES = {"hub_finalize": "manager"}


def forbidden(*args, **kwargs):
    raise AssertionError("export must not dispatch, open a writer, resume, or use a socket")


def owner_at(path):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {
        "$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    registration.register_executor(EXECUTOR, forbidden,
        identity={"implementation_id": "tests.ha-terminal-static", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, forbidden,
        identity={"implementation_id": "tests.ha-terminal-static", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    bucket = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    module = ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "TerminalIdentityFixture",
        "components": [{"name": "hub_finalize", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                      {"name": "result", "direction": "output", "schema": TEXT}],
            "operations": [{"name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"], "request_port": None,
                "tools": [], "config": {}, "budget_binding": bucket,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}], "effects": []}]}]}],
        "links": [], "entry": {"request": {"component": "hub_finalize", "port": "request"}},
        "exit": {"result": {"component": "hub_finalize", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "hub_finalize", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT], "budgets": {},
        "budget_buckets": [{**bucket, "max_attempts": 3}]})
    value = OwnerInput(TEXT, canonical_json("static request"), "test request")
    return start_run(module, registration, run_dir=path, task_input=value,
        entry_inputs={"request": value},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 3, 0, 3, 0),
        model_condition=MODEL, owner_statement="Static products only; no executor",
        command_id="fixture", catalog=agent_task_catalog(),
        host_execution_bindings=None, configuration_sources=None)


def start(owner):
    admission = owner.admit("hub_finalize.run", logical_tau=0,
                            command_id="admit", prepare_admission=None)
    return owner.start(admission, command_id="start")


def finish(owner, execution=None):
    execution = execution or start(owner)
    outputs = owner.products(execution, outcome_id="complete",
        products={"hub_finalize.result": (canonical_json("canonical final answer"),)},
        command_id="products")
    owner.succeed(outputs, command_id="success")
    return _ref_payload(owner.terminal())


def collector():
    return ObservationCollector(roles={"manager"}, tools=set())


def fingerprint(owner):
    core = owner._core
    return (core.event_store.max_ordinal(), core.event_store.writer_epoch,
            {str(p.relative_to(core.object_store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in core.object_store.root.rglob("*") if p.is_file()})


def export(owner, expected, *, observations=None, provisional=False):
    from unittest.mock import patch
    from cpn.rpnh.registry._registry import _RegistryCore
    import socket
    before = fingerprint(owner)
    observations = observations if observations is not None else collector()
    original_init = _RegistryCore.__init__
    def read_only_init(self, *args, **kwargs):
        assert kwargs.get("read_only") is True
        assert kwargs.get("create") is False
        original_init(self, *args, **kwargs)
    try:
        with patch.object(_RegistryCore, "__init__", read_only_init), \
                patch.object(_RegistryCore, "begin", forbidden), \
                patch("cpn.rpnh.run.resume_run", forbidden), \
                patch.object(socket, "socket", forbidden):
            result = exporter.export_registry(owner._core.run_dir, ROLES, observations,
                terminal_evidence_ref=expected, stop_reason="caller diagnostic",
                include_provisional=provisional)
    finally:
        assert fingerprint(owner) == before
    return result, observations


def test_matching_terminal_uses_exact_registry_identity(tmp_path):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    result, observations = export(owner, deepcopy(ref))
    assert result.terminal_evidence_ref == ref
    assert result.stop_reason == "caller diagnostic"
    assert result.actual_model_calls == 0
    assert result.context_capture_complete is True
    assert len(observations) == 1
    final = observations._observations[0]
    assert final.data["content"] == "canonical final answer"
    assert final.raw_event["handoff_context_raw"]["terminal_evidence_ref"] == ref
    assert result.terminal_evidence_ref is not ref


def test_missing_expected_ref_still_reads_current_terminal(tmp_path):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    result, observations = export(owner, None)
    assert result.terminal_evidence_ref == ref
    assert len(observations) == 1


@pytest.mark.parametrize("field,value", [
    ("entity_type", "final_result_index/v1"),
    ("logical_id", "terminal_evidence:" + "a" * 32),
    ("version_id", "terminal_evidence_version:" + "b" * 32),
    ("unexpected", "extra"),
])
def test_wrong_ref_never_pairs_real_payload_with_caller_identity(tmp_path, field, value):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    wrong = {**ref, field: value}
    observations = collector()
    with pytest.raises(ValueError, match="expected terminal evidence"):
        export(owner, wrong, observations=observations)
    assert len(observations) == 0


def test_cross_run_ref_is_rejected(tmp_path):
    owner = owner_at(tmp_path / "one")
    finish(owner)
    foreign = owner_at(tmp_path / "two")
    foreign_ref = finish(foreign)
    with pytest.raises(ValueError, match="expected terminal evidence"):
        export(owner, foreign_ref)


@pytest.mark.parametrize("state", ["running", "active", "stopped"])
def test_nonterminal_with_no_expected_ref_stays_nonterminal(tmp_path, state):
    owner = owner_at(tmp_path / "run")
    if state == "active":
        start(owner)
    if state == "stopped":
        owner.record_owner_stop(idempotency_key="owner-stop")
    result, observations = export(owner, None, provisional=state == "active")
    assert result.terminal_evidence_ref is None
    assert len(observations) == 0
    wrong = {"entity_type": "run_terminal_evidence/v1",
             "logical_id": str(new_id("terminal_evidence")),
             "version_id": str(new_id("terminal_evidence_version"))}
    with pytest.raises(ValueError, match="expected terminal evidence"):
        export(owner, wrong, provisional=state == "active")


def reenter(owner):
    kernel, _ = owner.operation_repository()
    _, authority = current_run_execution_authority(owner._core, kernel)
    return resume_run(owner.registration, run_dir=owner._core.run_dir,
        model_condition=MODEL, catalog=agent_task_catalog(),
        checkpoint_version_id=authority["latest_checkpoint_ref"]["version_id"],
        reopen_command_id="fixture-new-generation", reopen_reason="Static terminal projection regression")


def test_historical_terminal_does_not_close_reopened_generation(tmp_path):
    owner = owner_at(tmp_path / "run")
    old_ref = finish(owner)
    reopened = reenter(owner)
    result, observations = export(reopened, None)
    assert result.terminal_evidence_ref is None and len(observations) == 0
    with pytest.raises(ValueError, match="expected terminal evidence"):
        export(reopened, old_ref)


def test_multiple_terminal_generations_select_current_only(tmp_path):
    owner = owner_at(tmp_path / "run")
    old_ref = finish(owner)
    reopened = reenter(owner)
    current_ref = _ref_payload(reopened.terminal())
    assert current_ref != old_ref
    assert len(reopened._core.event_store.canonical_object_rows(
        object_type="run_terminal_evidence/v1")) == 2
    result, observations = export(reopened, current_ref)
    assert result.terminal_evidence_ref == current_ref
    assert result.context_capture_complete is True and len(observations) == 1
    assert result.capture_diagnostics["terminal_identity"]["execution_generation"] == 1
    with pytest.raises(ValueError, match="expected terminal evidence"):
        export(reopened, old_ref)


@pytest.mark.parametrize("field", ["head", "epoch"])
def test_moving_snapshot_publishes_no_partial_observations(tmp_path, monkeypatch, field):
    from types import SimpleNamespace
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    original = exporter._model_identity
    def changed_after_reads(snapshot):
        value = original(snapshot)
        # Fault injection at the final read: the storage adapter now reports
        # another committed cut/fence. No writer, thread, or socket is used.
        snapshot.core = SimpleNamespace(event_store=SimpleNamespace(
            max_ordinal=lambda: snapshot.upper + (field == "head"),
            writer_epoch=snapshot.epoch + (field == "epoch")))
        return value
    monkeypatch.setattr(exporter, "_model_identity", changed_after_reads)
    observations = collector()
    with pytest.raises(RuntimeError, match="Registry advanced"):
        export(owner, ref, observations=observations)
    assert len(observations) == 0


def test_wrong_task_result_is_not_exported(tmp_path, monkeypatch):
    from dataclasses import replace
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    kernel, _ = owner.operation_repository()
    terminal = kernel._exact_object(_version_from_payload(ref)).metadata
    result_ref = terminal["terminal_result_ref"]
    original = _ResourceServiceKernel._exact_object_for_view
    def wrong_task(self, view, selected, **kwargs):
        prepared = original(self, view, selected, **kwargs)
        if _ref_payload(selected) == result_ref:
            prepared = replace(prepared, metadata={**prepared.metadata,
                "task_ref": {**prepared.metadata["task_ref"], "logical_id": str(new_id("task"))}})
        return prepared
    monkeypatch.setattr(_ResourceServiceKernel, "_exact_object_for_view", wrong_task)
    observations = collector()
    with pytest.raises(ValueError, match="terminal identity chain"):
        export(owner, ref, observations=observations)
    assert len(observations) == 0


@pytest.mark.parametrize("kind,field", [
    ("run_terminal_evidence/v1", "terminal_evidence_ref"),
    ("run_terminal_evidence/v1", "run_ref"),
    ("run_terminal_evidence/v1", "final_checkpoint_ref"),
    ("final_result_index/v1", "terminal_result_ref"),
    ("final_result_index/v1", "terminal_occurrence_ref"),
])
def test_terminal_chain_identity_faults_fail_closed(tmp_path, monkeypatch, kind, field):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    original = exporter._Snapshot.canonical_document
    def changed(self, value, *, expected):
        row, document = original(self, value, expected=expected)
        if expected == kind:
            document = deepcopy(document)
            document[field]["logical_id"] = str(new_id("terminal_evidence"))
        return row, document
    monkeypatch.setattr(exporter._Snapshot, "canonical_document", changed)
    with pytest.raises((ValueError, RuntimeError)):
        export(owner, ref)


def test_terminal_closure_requires_one_transaction(tmp_path, monkeypatch):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    original = exporter._Snapshot.canonical_document
    def split_transaction(self, value, *, expected):
        row, document = original(self, value, expected=expected)
        if expected == "final_result_index/v1":
            row = {**dict(row), "transaction_id": "different-transaction"}
        return row, document
    monkeypatch.setattr(exporter._Snapshot, "canonical_document", split_transaction)
    with pytest.raises(ValueError, match="not one registered transaction"):
        export(owner, ref)


def test_snapshot_cache_cannot_alias_logical_id_or_type(tmp_path):
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    snapshot = exporter._Snapshot(owner._core.run_dir, include_provisional=False)
    assert snapshot.exact(ref, expected="run_terminal_evidence/v1")["terminal_evidence_ref"] == ref
    for wrong, expected in [
        ({**ref, "logical_id": str(new_id("terminal_evidence"))}, "run_terminal_evidence/v1"),
        ({**ref, "entity_type": "final_result_index/v1"}, "run_terminal_evidence/v1"),
        (ref, "final_result_index/v1"),
    ]:
        with pytest.raises((ValueError, RuntimeError)):
            snapshot.exact(wrong, expected=expected)


def test_terminal_reader_does_not_use_provisional_fallback(tmp_path, monkeypatch):
    from cpn.rpnh.registry.event_store import EventStore
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    original = EventStore.object_row_for_view
    def hidden_at_cut(self, view, version_id):
        if str(version_id) == ref["version_id"]:
            return None
        return original(self, view, version_id)
    monkeypatch.setattr(EventStore, "object_row_for_view", hidden_at_cut)
    with pytest.raises(RuntimeError, match="outside Registry read authority"):
        export(owner, ref, provisional=True)


def test_managed_action_and_terminal_share_staged_collector(tmp_path, monkeypatch):
    """Inject only an action row/node binding; run the real v3 export branch."""
    import json
    owner = owner_at(tmp_path / "run")
    ref = finish(owner)
    terminal_row, = owner._core.event_store.canonical_object_rows(
        object_type="run_terminal_evidence/v1")
    action = {
        "agent_action_ref": {"entity_type": "agent_action/v3",
            "logical_id": str(new_id("agent_action")), "version_id": str(new_id("agent_action_version"))},
        "outcome": "rejected", "tool_name": "synthetic_rejected_tool", "arguments": {},
        "terminal_receipt_ref": None, "non_delivery_reason": "pre-dispatch fixture rejection",
    }
    action_row = {**dict(terminal_row), "object_type": "agent_action/v3",
                  "metadata_json": json.dumps(action)}
    original = exporter._Snapshot.rows
    def rows(self, kind):
        return (action_row,) if kind == "agent_action/v3" else original(self, kind)
    monkeypatch.setattr(exporter._Snapshot, "rows", rows)
    monkeypatch.setattr(exporter, "_node_id", lambda *_: "hub_finalize")
    result, observations = export(owner, ref)
    assert [item.surface for item in observations._observations] == ["tool_call", "communication"]
    assert result.roles_observed == ("manager",)
    assert result.context_capture_complete is True
