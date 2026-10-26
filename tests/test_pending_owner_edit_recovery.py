"""Real Registry recovery, including two hard process exits; no transport.

The child deliberately exits at the named owner boundary. The parent uses a
fresh HOST registration and Core. These are component recovery tests, not native
socket or browser acceptance. Damage fixtures are explicitly labelled.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from cpn.rpnh.owner_edit_recovery import PendingEditResumeResult
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryCorruptError, StaleWriterError
from cpn.rpnh.registry.errors import ResourceIntegrityFault, StaleWriterFence
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import resume_pending_edit, resume_run
from test_owner_edit_receipts import arguments
from test_terminal_owner_adoption import make_owner, begin, settle, current, events, MODEL
from test_static_lease_reads import _registration, TEXT


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pending recovery tests may not open sockets")
    monkeypatch.setattr(socket, "socket", forbidden)


def durable_state(owner):
    with owner._core.event_store.connect() as db:
        database = tuple(db.iterdump())
    root = owner._core.object_store.root
    files = tuple((str(p.relative_to(root)), sha256(p.read_bytes()).hexdigest())
                  for p in sorted(root.rglob("*")) if p.is_file())
    return database, files


def selector(owner, receipt, command_id="recover:B"):
    return dict(run_dir=owner._core.run_dir, model_condition=MODEL,
        edit_command_id=command_id, owner_command_ref=receipt["owner_command_ref"],
        candidate_ref=receipt["candidate_ref"])


def pending(owner, monkeypatch, *, command_id="recover:B", edit=None):
    monkeypatch.setattr(owner.control.edits, "advance", lambda: None)
    return owner.command("edit", edit or arguments(owner), command_id=command_id)


def denied(owner, receipt, *, status="BLOCKED", **changes):
    before = durable_state(owner)
    result = resume_pending_edit(_registration(), **{**selector(owner, receipt), **changes})
    assert result.recovery_status == status
    assert result.owner is None
    assert durable_state(owner) == before
    return result


CHILD = r'''
from pathlib import Path
import json, os, socket, sys
from test_terminal_owner_adoption import make_owner
from test_owner_edit_receipts import arguments
from cpn.rpnh.registry.strict_contracts import ref_payload

def forbidden(*a, **k):
    raise AssertionError("crash fixture may not open sockets")
socket.socket = forbidden
root, mode = Path(sys.argv[1]), sys.argv[2]
owner = make_owner(root)
original_advance = owner.control.edits.advance

def exit_at_boundary():
    edit = owner.control.edits.queue[0]
    selected = {"edit_command_id": edit.command_id,
        "owner_command_ref": ref_payload(edit.request.owner_command_ref.as_version_ref()),
        "candidate_ref": ref_payload(edit.publication.declaration_resource_ref.as_version_ref())}
    if mode == "after_adoption":
        selected["receipt"] = original_advance()
    (root / "selection.json").write_text(json.dumps(selected))
    os._exit(23)
owner.control.edits.advance = exit_at_boundary
owner.command("edit", arguments(owner), command_id="recover:B")
raise AssertionError("owner reply must not return")
'''


@pytest.mark.parametrize("window", ["before_adoption", "after_adoption"])
def test_process_exit_at_exact_owner_boundary(tmp_path, monkeypatch, window):
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(source), str(source / "tests"))))
    child = subprocess.run([sys.executable, "-c", CHILD, str(tmp_path), window],
        env=environment, capture_output=True, text=True, timeout=90)
    assert child.returncode == 23, child.stderr
    selected = json.loads((tmp_path / "selection.json").read_text())
    receipt_before = selected.pop("receipt", None)
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    original_epoch, head = core.event_store.writer_epoch, core.event_store.max_ordinal()
    def forbidden(*args, **kwargs):
        raise AssertionError("recovery may not compile, publish a candidate, or execute a HOST tool")
    monkeypatch.setattr("cpn.rpnh.owner_edits.compile_module", forbidden)
    monkeypatch.setattr("cpn.rpnh.owner_edits.publish_module_net", forbidden)
    monkeypatch.setattr("cpn.rpnh.owner_edits.publish_module_operations", forbidden)
    result = resume_pending_edit(_registration(), run_dir=tmp_path / "run",
        model_condition=MODEL, **selected)
    assert isinstance(result, PendingEditResumeResult)
    assert result.recovery_status == "RESUMED" and result.owner is not None
    owner = result.owner
    assert owner._core.writer_epoch == original_epoch + 1
    assert result.receipt["status"] == "ADOPTED"
    assert len(events(owner, "net_adopted/v1")) == 2  # initial + exactly one edit
    assert not events(owner, "firing_admitted/v1")
    assert len(owner.control.records("candidate")) == 1
    assert len(owner._core.event_store.canonical_object_rows(object_type="net_instance/v1")) == 2
    if receipt_before is not None:
        assert result.receipt == receipt_before
        assert owner._core.event_store.max_ordinal() == head
    assert owner.read_edit_receipt("recover:B") == result.receipt
    # Resume itself does not execute; the original owner can now execute once.
    execution = begin(owner)
    settle(owner, execution)
    assert len(events(owner, "transition_firing_settled/v1")) == 1
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    stopped = denied(owner, result.receipt, status="UNSUPPORTED_CONTINUATION")
    assert stopped.receipt == result.receipt


def test_recovered_writer_fences_old_owner_and_keeps_receipt_identity(tmp_path, monkeypatch):
    old = make_owner(tmp_path)
    original = arguments(old)
    retained = pending(old, monkeypatch, edit=original)
    result = resume_pending_edit(_registration(), **selector(old, retained))
    assert result.recovery_status == "RESUMED"
    assert result.owner.command("edit", original, command_id="recover:B") == result.receipt
    for changed in (True, 1.0):
        altered = deepcopy(original)
        altered["candidate"]["components"][0]["config"]["capacities"]["request"] = changed
        with pytest.raises(ValueError, match="different immutable candidate"):
            result.owner.command("edit", altered, command_id="recover:B")
    head = old._core.event_store.max_ordinal()
    with pytest.raises(StaleWriterFence) as rejected:
        old.control.publish("result", "old-writer", None, {"status": "OBSERVED"})
    assert isinstance(rejected.value.__cause__, StaleWriterError)
    assert old._core.event_store.max_ordinal() == head
    assert result.owner.read_edit_receipt("recover:B") == result.receipt


def test_draining_blocks_unknown_firing_but_resumes_after_real_settlement(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    retained = owner.command("edit", arguments(owner), command_id="recover:B")
    assert retained["status"] == "DRAINING"
    assert "provisional" in denied(owner, retained).reason
    monkeypatch.setattr(owner.control.edits, "advance", lambda: None)
    settle(owner, execution)
    result = resume_pending_edit(_registration(), **selector(owner, retained))
    assert result.recovery_status == "RESUMED"
    assert len(events(result.owner, "transition_firing_settled/v1")) == 1


def test_candidate_only_and_multiple_pending_do_not_take_writer(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    args = arguments(owner)
    owner.control.publish("candidate", "partial", None, args,
        lineage=(owner.original_input_ref.as_version_ref(),))
    resource = owner.original_input_ref.as_version_ref()
    from cpn.rpnh.registry.strict_contracts import ref_payload
    dummy = {key: ref_payload(resource) for key in ("owner_command_ref", "candidate_ref")}
    result = denied(owner, dummy, edit_command_id="partial")
    assert result.receipt["status"] == "INCOMPLETE"
    retained = pending(owner, monkeypatch)
    assert "order" in denied(owner, retained).reason
    # A separate clean Registry with two complete pending commands is also ambiguous.
    second = make_owner(tmp_path / "second")
    retained = pending(second, monkeypatch)
    pending(second, monkeypatch, command_id="recover:C", edit=arguments(second, "C"))
    assert "order" in denied(second, retained).reason


@pytest.mark.parametrize("status", ["NEEDS_MARKING_DECISION", "RETRACTED", "CONFLICT"])
def test_recorded_nonrecoverable_state_is_not_reinterpreted(tmp_path, monkeypatch, status):
    owner = make_owner(tmp_path)
    retained = pending(owner, monkeypatch)
    edit = owner.control.edits.queue[0]
    owner.control.edits.result(edit, status)
    result = denied(owner, retained)
    assert result.receipt["status"] == status


def test_changed_selector_model_and_registration_are_read_only_failures(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    retained = pending(owner, monkeypatch)
    assert "selector" in denied(owner, retained, status="CONFLICT",
        candidate_ref=retained["owner_command_ref"]).reason
    before = durable_state(owner)
    with pytest.raises(ResourceIntegrityFault, match="run identity"):
        resume_pending_edit(_registration(), **{**selector(owner, retained), "model_condition": "wrong"})
    assert durable_state(owner) == before
    registration = _registration()
    registration._declarations["executor"]["test/net-operation-business/v1"]["identity"]["revision"] = "changed"
    with pytest.raises(ValueError, match="registration differs"):
        resume_pending_edit(registration, **selector(owner, retained))
    assert durable_state(owner) == before


def test_empty_epoch_gap_allowed_but_later_writer_fact_blocks(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    retained = pending(owner, monkeypatch)
    _RegistryCore(owner._core.run_dir, create=False)  # empty epoch, no facts
    resumed = resume_pending_edit(_registration(), **selector(owner, retained))
    assert resumed.recovery_status == "RESUMED"
    # An unrelated ordinary owner fact after a subsequent writer consumes the cut.
    next_core = _RegistryCore(owner._core.run_dir, create=False)
    resumed.owner._core = next_core
    resumed.owner.control.publish("message", "later-fact", "step.run", {"body": "later"})
    result = denied(resumed.owner, resumed.receipt, status="UNSUPPORTED_CONTINUATION")
    assert "later writer" in result.reason


def test_new_writer_rechecks_cut_before_any_adoption(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    retained = pending(owner, monkeypatch)
    import cpn.rpnh.owner_edit_recovery as recovery
    original = recovery._preflight
    calls = []
    def race(core, registration, **kwargs):
        result = original(core, registration, **kwargs)
        calls.append(core.read_only)
        if core.read_only:
            owner.control.publish("message", "race", "step.run", {"body": "new fact"})
        return result
    monkeypatch.setattr(recovery, "_preflight", race)
    epoch = owner._core.event_store.writer_epoch
    result = resume_pending_edit(_registration(), **selector(owner, retained))
    assert calls == [True, False]
    assert result.recovery_status == "CONFLICT" and result.owner is None
    assert owner._core.event_store.writer_epoch == epoch + 1
    assert len(events(owner, "net_adopted/v1")) == 1


@pytest.mark.parametrize("adopted", [False, True])
def test_allocation_before_invocation_is_not_zero_active_recovery(tmp_path, monkeypatch, adopted):
    owner = make_owner(tmp_path)
    retained = (owner.command("edit", arguments(owner), command_id="recover:B") if adopted
                else pending(owner, monkeypatch))
    def interrupted(**kwargs):
        raise RuntimeError("after durable allocation, before invocation")
    # Use the Registry admission function to exercise its real allocation cut;
    # the live owner correctly pauses new admissions for pending edits.
    from cpn.rpnh.registry.module_execution import admit_module_firing
    with pytest.raises(RuntimeError, match="after durable allocation"):
        admit_module_firing(owner._core, transition_id="step.run", logical_tau=0,
            idempotency_key="allocation-only", prepare_admission=interrupted)
    with owner._core.event_store.connect() as db:
        assert not db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL'").fetchone()
    result = denied(owner, retained, status="UNSUPPORTED_CONTINUATION" if adopted else "BLOCKED")
    assert "allocation" in result.reason


def test_existing_adopted_with_active_firing_or_later_net_is_receipt_only(tmp_path):
    owner = make_owner(tmp_path)
    receipt = owner.command("edit", arguments(owner), command_id="recover:B")
    execution = begin(owner)
    assert "provisional" in denied(owner, receipt, status="UNSUPPORTED_CONTINUATION").reason
    settle(owner, execution)
    owner.command("edit", arguments(owner, "C"), command_id="recover:C")
    result = denied(owner, receipt, status="UNSUPPORTED_CONTINUATION")
    assert result.receipt == receipt


def test_ordinary_resume_still_rejects_plain_running_and_pending(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    pending(owner, monkeypatch)
    before = durable_state(owner)
    with pytest.raises(ResourceIntegrityFault):
        resume_run(_registration(), run_dir=owner._core.run_dir, model_condition=MODEL)
    assert durable_state(owner) == before


def test_registered_owner_inputs_are_reused_without_republication(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    edit["candidate"]["components"][0]["config"]["capacities"]["request"] = 2
    edit["owner_inputs"] = {"step.request": [{"schema_id": TEXT, "value": "another", "summary": "extra"}]}
    retained = pending(owner, monkeypatch, edit=edit)
    rows = owner._core.event_store.canonical_object_rows(object_type="resource_version/v1")
    inputs_before = [r["version_id"] for r in rows if "owner_input_command_id" in json.loads(r["metadata_json"])["descriptors"]]
    result = resume_pending_edit(_registration(), **selector(owner, retained))
    assert result.recovery_status == "RESUMED"
    assert len(result.receipt["owner_input_mappings"]) == 1
    rows = owner._core.event_store.canonical_object_rows(object_type="resource_version/v1")
    assert inputs_before == [r["version_id"] for r in rows if "owner_input_command_id" in json.loads(r["metadata_json"])["descriptors"]]


def test_candidate_budget_mismatch_is_rejected_before_writer(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    edit["candidate"]["budget_buckets"][0]["max_attempts"] += 1
    retained = pending(owner, monkeypatch, edit=edit)
    before = durable_state(owner)
    with pytest.raises(RegistryCorruptError, match="budget inventory"):
        resume_pending_edit(_registration(), **selector(owner, retained))
    assert durable_state(owner) == before


def test_superseded_authority_does_not_resume_original_command(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    retained = pending(owner, monkeypatch)
    from cpn.rpnh.registry.strict_contracts import publish_user_authority_decision
    publish_user_authority_decision(owner._core, authority_kind="scope", canonical_statement="Changed scope",
        user_principal_ref=owner.principal_ref, governed_artifact_refs=(owner.identity.task_ref,),
        selected_choices={"scope": "changed"}, effective_sequence=2,
        supersedes_ref=owner.authority_decision_ref, idempotency_key="changed-authority")
    assert "superseded" in denied(owner, retained, status="CONFLICT").reason


def test_second_same_base_command_cannot_adopt_after_first(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    advance = owner.control.edits.advance
    pending(owner, monkeypatch)
    other = pending(owner, monkeypatch, command_id="recover:C", edit=arguments(owner, "C"))
    assert advance()["status"] == "ADOPTED"
    denied(owner, other, status="CONFLICT", edit_command_id="recover:C")
    assert len(events(owner, "net_adopted/v1")) == 2


@pytest.mark.parametrize("adopted", [False, True])
def test_workspace_genesis_recovery_keeps_original_tar_authority(tmp_path, monkeypatch, adopted):
    # A separate direct-owner boundary: no event loop, mocked transport, HOST
    # completion, or attempt to run the socket-dependent integration test.
    from test_native_net_operations import (
        AgentStage, build_agent_task_module, agent_task_registration, agent_task_catalog,
        LLMInputTarget, make_optional_agent_host_bindings, AGENT_TEXT_SCHEMA,
        OwnerInput, start_run, ModuleBudgetDeclaration,
    )
    module = build_agent_task_module((AgentStage("worker", "Return one result."),),
        max_attempts_per_stage=3)
    target = LLMInputTarget("offline-receipt-workspace", 64, 65536)
    bindings = make_optional_agent_host_bindings(target, provider_backend_config={
        "schema_version": "optional_agent_execution_provenance/v1", "model": target.model_condition,
        "backend": "offline-test", "timeout_seconds": 30, "selection": {},
        "transport_kind": "offline-test", "response_protocol": "llm_response_envelope/v1",
    }, transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
        "response_adapter_ref": "llm_response_envelope/v1"})
    task = OwnerInput(AGENT_TEXT_SCHEMA, canonical_json("task"), "Offline receipt workspace")
    owner = start_run(module, agent_task_registration(), run_dir=tmp_path / "workspace-run",
        task_input=task, entry_inputs={name: task for name in module.entry},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 3, 0, 3, 0), model_condition=target.model_condition,
        owner_statement="Offline receipt workspace", command_id="receipt-workspace:fresh",
        catalog=agent_task_catalog(), host_execution_bindings=bindings)
    checkpoint = owner._core.get_version(current(owner)[2].checkpoint_ref.version_id).metadata
    workspace_refs = checkpoint["workspace_revision_refs"]
    assert len(workspace_refs) == 1
    workspace = owner._core.get_version(_version_from_payload(workspace_refs[0]).version_id)
    assert workspace.media_type == "application/x-tar"
    assert workspace.metadata["disposition"] == "genesis"
    candidate = deepcopy(module.to_dict())
    candidate["name"] = "ReceiptWorkspaceReplacement"
    edit = {"candidate": candidate, "base_net_ref": owner.snapshot()["net_ref"],
            "marking_mapping": {}, "retire_token_refs": [], "owner_inputs": {}}
    if not adopted:
        monkeypatch.setattr(owner.control.edits, "advance", lambda: None)
    receipt = owner.command("edit", edit, command_id="receipt-workspace:edit")
    result = resume_pending_edit(agent_task_registration(), run_dir=owner._core.run_dir,
        model_condition=target.model_condition, edit_command_id="receipt-workspace:edit",
        owner_command_ref=receipt["owner_command_ref"], candidate_ref=receipt["candidate_ref"],
        catalog=agent_task_catalog(), host_execution_bindings=bindings)
    assert result.recovery_status == "RESUMED", result.reason
    successor = owner._core.get_version(_version_from_payload(result.receipt["checkpoint_ref"]).version_id).metadata
    assert successor["workspace_revision_refs"] == workspace_refs
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


def test_post_adoption_entry_does_not_return_a_superseded_writer(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    receipt = owner.command("edit", arguments(owner), command_id="recover:B")
    import cpn.rpnh.registry.run_authority as authority
    original = authority.record_recovered_run_entry
    calls = []
    def superseded(core, *args, **kwargs):
        result = original(core, *args, **kwargs)
        calls.append(core.writer_epoch)
        _RegistryCore(core.run_dir, create=False)
        return result
    monkeypatch.setattr(authority, "record_recovered_run_entry", superseded)
    with pytest.raises(ResourceIntegrityFault, match="Registry advanced"):
        resume_pending_edit(_registration(), **selector(owner, receipt))
    assert len(calls) == 1
    assert len(events(owner, "net_adopted/v1")) == 2
    assert owner.read_edit_receipt("recover:B") == receipt
