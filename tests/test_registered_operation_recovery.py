from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.firing_recovery import (
    _reject_conflicting_lifecycle,
    classify_registered_operation_interruption,
    record_registered_operation_completion,
    recover_registered_operation_interruption,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import (
    OwnerInput, _operation_services, resume_run, start_run,
)
from test_structural_evidence import (
    REQUEST, TERMINAL, _module, _owner, _products, _registration, _start,
)


MODEL = "offline-structural-evidence"


def _closed_completion(tmp_path: Path):
    owner = _owner(tmp_path)
    execution = _start(owner, "gate.inspect")
    outputs = _products(owner, execution)
    kernel, repository = owner.operation_repository()
    event = record_registered_operation_completion(
        owner._core, kernel, repository, outputs,
        idempotency_key="test:registered-operation-completion")
    return owner, execution, outputs, event


def test_resume_rejects_active_firing_without_completion_before_writer_bump(
        tmp_path: Path) -> None:
    owner = _owner(tmp_path)
    execution = _start(owner, "gate.inspect")
    _products(owner, execution)
    old_epoch = owner._core.event_store.writer_epoch

    with pytest.raises(
            ResourceIntegrityFault,
            match="lacks one exact registered-operation completion"):
        resume_run(
            _registration(), run_dir=tmp_path / "run",
            model_condition=MODEL)

    assert owner._core.event_store.writer_epoch == old_epoch
    assert not any(
        event.event_type == "transition_firing_settled/v1"
        for event in owner._core.event_store.list_events())


def test_registration_mismatch_fails_before_writer_and_correct_retry_recovers(
        tmp_path: Path) -> None:
    owner, _execution, _outputs, _completion = _closed_completion(tmp_path)
    old_epoch = owner._core.event_store.writer_epoch
    wrong = _registration()
    executor_key = next(iter(wrong._declarations[  # type: ignore[attr-defined]
        "executor"]))
    wrong._declarations["executor"][executor_key][  # type: ignore[attr-defined]
        "identity"]["revision"] = "wrong-revision"

    with pytest.raises(ValueError, match="registration differs"):
        resume_run(
            wrong, run_dir=tmp_path / "run", model_condition=MODEL)

    assert owner._core.event_store.writer_epoch == old_epoch
    resumed = resume_run(
        _registration(), run_dir=tmp_path / "run", model_condition=MODEL)
    assert resumed.snapshot()["active_firings"] == []


def test_empty_writer_epoch_gap_does_not_strand_exact_completion(
        tmp_path: Path) -> None:
    _owner_before, _execution, _outputs, completion = _closed_completion(
        tmp_path)
    empty_writer = _RegistryCore(tmp_path / "run", create=False)
    assert empty_writer.writer_epoch == completion.writer_fencing_epoch + 1
    assert not any(
        event.writer_fencing_epoch == empty_writer.writer_epoch
        for event in empty_writer.event_store.list_events())

    resumed = resume_run(
        _registration(), run_dir=tmp_path / "run", model_condition=MODEL)

    assert resumed._core.writer_epoch == completion.writer_fencing_epoch + 2
    assert resumed.snapshot()["active_firings"] == []


def test_later_writer_fact_invalidates_stale_completion(
        tmp_path: Path) -> None:
    _owner_before, _execution, _outputs, completion = _closed_completion(
        tmp_path)
    later = _RegistryCore(tmp_path / "run", create=False)
    kernel = _ResourceServiceKernel(later)
    run_ref = _version_from_payload(json.loads(
        later.event_store.get_meta("native_run_ref")))
    run = kernel._exact_object(
        run_ref, expected_type="native_run_identity/v1").metadata
    gateway = RegistryRegistrationGateway(
        later,
        _version_from_payload(run["task_ref"]),
        _version_from_payload(json.loads(
            later.event_store.get_meta("bootstrap_command_ref"))),
    )
    gateway("component", "tests/unrelated-registration/v1", {
        "kind": "component",
        "key": "tests/unrelated-registration/v1",
        "identity": {"implementation_id": "tests.unrelated", "revision": "v1"},
        "contracts": {},
    })
    assert any(
        event.writer_fencing_epoch > completion.writer_fencing_epoch
        for event in later.event_store.list_events())
    epoch_after_fact = later.writer_epoch

    with pytest.raises(
            ResourceIntegrityFault,
            match="changed run/net/marking/lease authority"):
        resume_run(
            _registration(), run_dir=tmp_path / "run",
            model_condition=MODEL)

    assert later.event_store.writer_epoch == epoch_after_fact


def test_stale_empty_interruption_requires_one_use_reopen_authorization(
        tmp_path: Path) -> None:
    document = _module().to_dict()
    inspect = next(
        operation
        for operation in document["components"][0]["operations"]
        if operation["name"] == "inspect")
    inspect["outcomes"].append({"name": "interrupted", "products": []})
    module = ModuleDeclaration.from_dict(document)
    request = OwnerInput(
        REQUEST,
        canonical_json({"request_id": "request-A", "allow": True}),
        "Stale empty-settlement authorization boundary",
    )
    owner = start_run(
        module, _registration(), run_dir=tmp_path / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 4, 0, 4, 0),
        model_condition=MODEL,
        owner_statement="Reject generic stale empty settlement",
        command_id="test:stale-empty:fresh",
    )
    _start(owner, "gate.inspect")

    from cpn.rpnh.registry.run_authority import (
        current_run_execution_authority,
    )
    preflight = _RegistryCore(
        tmp_path / "run", create=False, read_only=True)
    preflight_kernel, preflight_repository, historical_input = (
        _operation_services(preflight))
    _authority_ref, authority = current_run_execution_authority(
        preflight, preflight_kernel)
    recovery = classify_registered_operation_interruption(
        preflight, preflight_kernel, preflight_repository,
        current_run=authority, historical_input=historical_input,
        checkpoint_reentry=True)

    replacement = _RegistryCore(tmp_path / "run", create=False)
    kernel, repository, _historical_input = _operation_services(replacement)
    _replacement_ref, replacement_authority = (
        current_run_execution_authority(replacement, kernel))
    without_reopen_proof = replace(recovery, checkpoint_reentry=False)
    with pytest.raises(RegistryConflict, match="stale operation lease"):
        recover_registered_operation_interruption(
            replacement, kernel, repository, without_reopen_proof,
            current_run=replacement_authority,
            idempotency_key="test:stale-empty:without-proof")


def test_nested_host_failure_is_not_a_parent_operation_terminal_conflict(
        tmp_path: Path) -> None:
    _owner_before, _execution, outputs, _completion = _closed_completion(
        tmp_path)
    context = outputs.execution.operation.canonical.context
    nested = SimpleNamespace(
        event_type="provider_attempt_host_closed/v1",
        aggregate_id="provider_attempt:00000000000000000000000000000000",
    )
    core = SimpleNamespace(event_store=SimpleNamespace(
        list_events=lambda: (nested,)))

    _reject_conflicting_lifecycle(
        core,
        invocation_ref=context.invocation_ref,
        firing_ref=outputs.execution.operation.firing.transition_firing_ref,
        lease_ref=outputs.execution.operation_execution_lease_ref,
    )

    parent = SimpleNamespace(
        event_type="operation_terminal_ready/v1",
        aggregate_id=str(context.invocation_ref.entity_id),
    )
    core.event_store.list_events = lambda: (nested, parent)
    with pytest.raises(ResourceIntegrityFault, match="terminal conflict"):
        _reject_conflicting_lifecycle(
            core,
            invocation_ref=context.invocation_ref,
            firing_ref=(
                outputs.execution.operation.firing.transition_firing_ref),
            lease_ref=outputs.execution.operation_execution_lease_ref,
        )


def test_declared_host_effect_completion_fails_closed_before_writer(
        tmp_path: Path) -> None:
    document = _module().to_dict()
    inspect = next(
        operation
        for operation in document["components"][0]["operations"]
        if operation["name"] == "inspect")
    allow = next(
        outcome for outcome in inspect["outcomes"]
        if outcome["name"] == "allow")
    allow["effects"] = [{"key": TERMINAL, "config": {}}]
    module = ModuleDeclaration.from_dict(document)
    request = OwnerInput(
        REQUEST,
        canonical_json({"request_id": "request-A", "allow": True}),
        "Synthetic effect recovery request",
    )
    owner = start_run(
        module, _registration(), run_dir=tmp_path / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 4, 0, 4, 0),
        model_condition=MODEL,
        owner_statement="Deterministic effect recovery boundary",
        command_id="test:effect-recovery:fresh",
    )
    execution = _start(owner, "gate.inspect")
    outputs = _products(owner, execution)
    kernel, repository = owner.operation_repository()
    record_registered_operation_completion(
        owner._core, kernel, repository, outputs,
        idempotency_key="test:effect-recovery:completion")
    old_epoch = owner._core.writer_epoch
    calls = []
    registration = _registration()
    registration._callables["tool"][TERMINAL] = (  # type: ignore[attr-defined]
        lambda **_kwargs: calls.append(True))

    with pytest.raises(
            ResourceIntegrityFault, match="unrecorded declared HOST effects"):
        resume_run(
            registration, run_dir=tmp_path / "run",
            model_condition=MODEL)

    assert calls == []
    assert owner._core.event_store.writer_epoch == old_epoch


def test_exact_completion_recovers_once_without_executor_and_fences_old_writer(
        tmp_path: Path) -> None:
    owner, _execution, outputs, completion = _closed_completion(tmp_path)
    registration = _registration()
    calls = []
    key = outputs.execution.operation.spec.executor_key
    registration._callables["executor"][key] = (  # type: ignore[attr-defined]
        lambda **_kwargs: calls.append("executor")
    )

    resumed = resume_run(
        registration, run_dir=tmp_path / "run", model_condition=MODEL)

    snapshot = resumed.snapshot()
    assert calls == []
    assert snapshot["active_firings"] == []
    assert snapshot["enabled_transitions"] == ["gate.execute"]
    assert sum(
        event.event_type == "registered_operation_completion_recorded/v1"
        for event in resumed._core.event_store.list_events()) == 1
    assert sum(
        event.event_type == "transition_firing_settled/v1"
        for event in resumed._core.event_store.list_events()) == 1
    assert completion.writer_fencing_epoch + 1 == resumed._core.writer_epoch

    with pytest.raises(Exception) as raised:
        owner.succeed(outputs, command_id="test:stale-old-writer")
    assert "stale" in str(raised.value).lower()

    with pytest.raises(ResourceIntegrityFault):
        resume_run(
            _registration(), run_dir=tmp_path / "run",
            model_condition=MODEL)
    assert sum(
        event.event_type == "transition_firing_settled/v1"
        for event in resumed._core.event_store.list_events()) == 1


def test_completion_record_is_idempotent_and_rejects_changed_output_material(
        tmp_path: Path) -> None:
    owner, execution, outputs, first = _closed_completion(tmp_path)
    kernel, repository = owner.operation_repository()

    replay = record_registered_operation_completion(
        owner._core, kernel, repository, outputs,
        idempotency_key="test:replayed-completion-command")
    assert replay.event_id == first.event_id

    request = {"request_id": "request-A", "allow": True}
    crossed = owner.products(
        execution, outcome_id="deny",
        products={"gate.decision_out": (canonical_json(request),)},
        command_id="test:crossed-completion-products")
    with pytest.raises(
            ResourceIntegrityFault, match="conflicts with durable material"):
        record_registered_operation_completion(
            owner._core, kernel, repository, crossed,
            idempotency_key="test:crossed-completion")
