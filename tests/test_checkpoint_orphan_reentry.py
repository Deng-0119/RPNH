"""A checkpoint reopen must survive owner loss without a business cancel outcome."""
from __future__ import annotations

import pytest

from copy import deepcopy
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.checkpoint_reentry import committed_checkpoint_refs
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry._event_store import backend
from cpn.rpnh.registry.firing_recovery import record_registered_operation_completion
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.run_authority import current_run_execution_authority
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, resume_run, start_run
from test_structural_evidence import REQUEST, _module, _owner, _registration


def _start(owner, name):
    key = f"test:{owner._core.writer_epoch}:{name}"
    admission = owner.admit(name, logical_tau=0, command_id=key + ":admit")
    assert admission is not None
    return owner.start(admission, command_id=key + ":start")


def _products(owner, execution):
    executor = owner.registration.resolve("executor", execution.operation.spec.executor_key)
    outcome, port, value = executor(execution=execution)
    return owner.products(execution, outcome_id=outcome,
        products={port: (canonical_json(value),)},
        command_id=f"test:products:{execution.operation.firing.transition_firing_ref.version_id}")


def _reopen(tmp_path, selected):
    return resume_run(
        _registration(), run_dir=tmp_path / "run",
        model_condition="offline-structural-evidence",
        checkpoint_version_id=str(selected.version_id),
        reopen_command_id="owner-selected-safe-checkpoint",
        reopen_reason="Continue after the original process exited unexpectedly.")


@pytest.mark.parametrize("boundary", ["admitted", "started", "products", "owner_stopped"])
def test_reopen_complete_only_operation_after_owner_loss(tmp_path, boundary):
    owner = _owner(tmp_path)
    selected = committed_checkpoint_refs(owner._core)[-1]
    original_identity = owner.identity
    old_epoch = owner._core.writer_epoch
    admission = owner.admit("gate.inspect", logical_tau=0, command_id="lost:admit")
    assert admission is not None
    outputs = None
    if boundary != "admitted":
        execution = owner.start(admission, command_id="lost:start")
        if boundary in {"products", "owner_stopped"}:
            outputs = _products(owner, execution)
    if boundary == "owner_stopped":
        owner.record_owner_stop(idempotency_key="lost:owner-stop-before-drain")
    assert len(owner.snapshot()["active_firings"]) == 1

    recovered = _reopen(tmp_path, selected)

    assert recovered.identity == original_identity
    assert recovered._core.writer_epoch > old_epoch
    assert recovered.snapshot()["active_firings"] == []
    _, authority = current_run_execution_authority(
        recovered._core, _ResourceServiceKernel(recovered._core))
    assert authority["execution_generation"] == 1
    assert authority["generation_source_checkpoint_ref"]["version_id"] == str(selected.version_id)
    if outputs is not None:
        for output in outputs.outputs:
            assert recovered._core.event_store.canonical_object_row(
                output.resource_ref.resource_version_id) is None
            assert recovered._core.get_version(
                output.resource_ref.resource_version_id) is not None

    # Reexecute the suffix through ordinary admission/products/Success, with
    # fresh occurrences and the original declaration that has no interrupted.
    fresh = _start(recovered, "gate.inspect")
    assert fresh.operation.firing.attempt_index == 2
    recovered.succeed(_products(recovered, fresh), command_id="fresh:inspect-success")
    terminal = _start(recovered, "gate.execute")
    recovered.succeed(_products(recovered, terminal), command_id="fresh:execute-success")
    assert recovered.terminal() is not None
    _, final = current_run_execution_authority(
        recovered._core, _ResourceServiceKernel(recovered._core))
    assert final["status"] == "terminal"


@pytest.mark.parametrize("returned_completion", [False, True])
@pytest.mark.parametrize("owner_stopped", [False, True])
def test_reopen_supersedes_all_orphans_and_preserves_real_completion(tmp_path, returned_completion, owner_stopped):
    document = _module().to_dict()
    second = deepcopy(document["components"][0])
    second["name"] = "other"
    document["components"].append(second)
    document["entry"]["second"] = {"component": "other", "port": "request"}
    module = ModuleDeclaration.from_dict(document)
    request = OwnerInput(REQUEST, canonical_json({"request_id": "parallel", "allow": True}), "Parallel input")
    owner = start_run(module, _registration(), run_dir=tmp_path / "run",
        task_input=request, entry_inputs={"request": request, "second": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 8, 0, 8, 0),
        model_condition="offline-structural-evidence", owner_statement="Offline parallel owner",
        command_id="parallel:fresh")
    selected = committed_checkpoint_refs(owner._core)[-1]
    first = _start(owner, "gate.inspect")
    second = _start(owner, "other.inspect")
    outputs = _products(owner, first)
    if returned_completion:
        kernel, repository = owner.operation_repository()
        record_registered_operation_completion(owner._core, kernel, repository, outputs,
            idempotency_key="actual-executor-return")
    if owner_stopped:
        owner.record_owner_stop(idempotency_key="parallel:owner-stop")
    recovered = _reopen(tmp_path, selected)
    assert recovered.snapshot()["active_firings"] == []
    first_record = recovered._core.event_store.ordered_firing_record(
        first.operation.firing.transition_firing_ref.version_id)
    second_record = recovered._core.event_store.ordered_firing_record(
        second.operation.firing.transition_firing_ref.version_id)
    assert first_record["state"] == ("PUBLISHED" if returned_completion else "SUPERSEDED")
    assert second_record["state"] == "SUPERSEDED"
    for name in ["gate.inspect", "other.inspect"]:
        assert _start(recovered, name).operation.firing.attempt_index == 2


def test_supersession_and_reentry_roll_back_together_and_retry(tmp_path, monkeypatch):
    owner = _owner(tmp_path)
    selected = committed_checkpoint_refs(owner._core)[-1]
    execution = _start(owner, "gate.inspect")
    firing_id = execution.operation.firing.transition_firing_ref.version_id
    original_hook = EventStore._after_firing_authority_mutation

    def lose_owner(self, db, *, firing_version_id, transaction_id):
        original_hook(self, db, firing_version_id=firing_version_id, transaction_id=transaction_id)
        raise RuntimeError("process lost during reentry transaction")

    monkeypatch.setattr(EventStore, "_after_firing_authority_mutation", lose_owner)
    with pytest.raises(RuntimeError, match="lost during reentry"):
        _reopen(tmp_path, selected)
    reader = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    assert reader.event_store.ordered_firing_record(firing_id)["state"] == "PROVISIONAL"
    assert not reader.event_store.list_events_by_type(("run_reopened/v1",))
    monkeypatch.setattr(EventStore, "_after_firing_authority_mutation", original_hook)
    recovered = _reopen(tmp_path, selected)
    assert recovered.snapshot()["active_firings"] == []
    assert recovered._core.event_store.ordered_firing_record(firing_id)["state"] == "SUPERSEDED"
    assert len(recovered._core.event_store.list_events_by_type(("run_reopened/v1",))) == 1


def test_repeated_command_does_not_abandon_successor_work(tmp_path):
    owner = _owner(tmp_path)
    selected = committed_checkpoint_refs(owner._core)[-1]
    _start(owner, "gate.inspect")
    recovered = _reopen(tmp_path, selected)
    again = _reopen(tmp_path, selected)
    assert len(again._core.event_store.list_events_by_type(("run_reopened/v1",))) == 1
    _start(again, "gate.inspect")
    epoch = again._core.event_store.writer_epoch
    with pytest.raises(ResourceIntegrityFault, match="active successor work"):
        _reopen(tmp_path, selected)
    assert again._core.event_store.writer_epoch == epoch
    assert len(again.snapshot()["active_firings"]) == 1


def test_legacy_registry_migrates_on_writer_open_only(tmp_path, monkeypatch):
    legacy_schema = """CREATE TABLE IF NOT EXISTS firing_publications (
        firing_version_id TEXT PRIMARY KEY, firing_logical_id TEXT NOT NULL UNIQUE,
        invocation_version_id TEXT NOT NULL UNIQUE, invocation_logical_id TEXT NOT NULL UNIQUE,
        net_version_id TEXT NOT NULL, operation_binding_version_id TEXT NOT NULL,
        admission_checkpoint_version_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PROVISIONAL','PUBLISHED')),
        opened_transaction_id TEXT NOT NULL, published_transaction_id TEXT,
        operation_result_version_id TEXT, workspace_lineage_id TEXT,
        workspace_revision_version_id TEXT, marking_checkpoint_version_id TEXT,
        CHECK((state='PROVISIONAL' AND published_transaction_id IS NULL
            AND operation_result_version_id IS NULL AND workspace_lineage_id IS NULL
            AND workspace_revision_version_id IS NULL AND marking_checkpoint_version_id IS NULL)
          OR (state='PUBLISHED' AND published_transaction_id IS NOT NULL
            AND operation_result_version_id IS NOT NULL
            AND ((workspace_lineage_id IS NULL AND workspace_revision_version_id IS NULL)
              OR (workspace_lineage_id IS NOT NULL AND workspace_revision_version_id IS NOT NULL))
            AND marking_checkpoint_version_id IS NOT NULL)),
        FOREIGN KEY(opened_transaction_id) REFERENCES transactions(transaction_id),
        FOREIGN KEY(published_transaction_id) REFERENCES transactions(transaction_id))"""
    with monkeypatch.context() as legacy:
        legacy.setattr(backend, "_FIRING_PUBLICATIONS_SCHEMA", legacy_schema)
        owner = _owner(tmp_path)
        selected = committed_checkpoint_refs(owner._core)[-1]
        execution = _start(owner, "gate.inspect")
    reader = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    with reader.event_store.connect() as db:
        assert "SUPERSEDED" not in db.execute(
            "SELECT sql FROM sqlite_master WHERE name='firing_publications'").fetchone()[0]
        members = db.execute("SELECT COUNT(*) FROM firing_temporary_members").fetchone()[0]
    recovered = _reopen(tmp_path, selected)
    firing_id = execution.operation.firing.transition_firing_ref.version_id
    assert recovered._core.event_store.ordered_firing_record(firing_id)["state"] == "SUPERSEDED"
    with recovered._core.event_store.connect() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM firing_temporary_members").fetchone()[0] == members
        assert "firing_publications" in {
            row[2] for row in db.execute("PRAGMA foreign_key_list(firing_temporary_members)")}


def test_new_reopen_command_can_supersede_stopped_successor(tmp_path):
    owner = _owner(tmp_path)
    selected = committed_checkpoint_refs(owner._core)[-1]
    _start(owner, "gate.inspect")
    recovered = _reopen(tmp_path, selected)
    _start(recovered, "gate.inspect")
    recovered.record_owner_stop(idempotency_key="successor:owner-stopped")
    next_generation = resume_run(_registration(), run_dir=tmp_path / "run",
        model_condition="offline-structural-evidence",
        checkpoint_version_id=str(selected.version_id),
        reopen_command_id="new-owner-reentry-after-successor-loss",
        reopen_reason="Abandon the subsequent stopped generation.")
    assert next_generation.snapshot()["active_firings"] == []
    _, authority = current_run_execution_authority(
        next_generation._core, _ResourceServiceKernel(next_generation._core))
    assert authority["execution_generation"] == 2
    assert _start(next_generation, "gate.inspect").operation.firing.attempt_index == 3

