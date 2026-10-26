"""Real Registry owner receipts; no sockets, HOST execution, or provider calls.

Damage cases alter only offline test fixtures. They do not claim that an owner
command can lawfully produce broken Registry authority.
"""
from copy import deepcopy
from hashlib import sha256
import json
import socket

import pytest

from cpn.rpnh.control_client import ControlClient, ControlProtocolError, serialize_command
from cpn.rpnh.control_server import OwnerEventLoop, parse_command
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.event_store import RegistryCorruptError
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_terminal_owner_adoption import make_owner, begin, settle, current
from test_static_lease_reads import TEXT


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("receipt tests must not create sockets")
    monkeypatch.setattr(socket, "socket", forbidden)


def arguments(owner, name="B"):
    candidate = deepcopy(current(owner)[1].compiled.source.to_dict())
    candidate["name"] = name
    candidate["components"][0]["config"] = {"capacities": {"request": 1}}
    return {"candidate": candidate, "base_net_ref": owner.snapshot()["net_ref"],
            "marking_mapping": {}, "retire_token_refs": [], "owner_inputs": {}}


def state(owner):
    with owner._core.event_store.connect() as db:
        database = tuple(db.iterdump())
    root = owner._core.object_store.root
    files = tuple((str(path.relative_to(root)), sha256(path.read_bytes()).hexdigest())
                  for path in sorted(root.rglob("*")) if path.is_file())
    queue = tuple((edit.command_id, edit.status) for edit in owner.control.edits.queue)
    return database, files, queue, owner.admission_paused


def test_adopted_first_repeat_query_and_later_net_have_one_receipt(tmp_path, monkeypatch):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    first = owner.command("edit", edit, command_id="receipt:B")
    assert first["status"] == "ADOPTED"
    assert first["net_ref"] == first["candidate_net_ref"]
    assert {"checkpoint_ref", "result_ref", "transaction_id", "owner_command_ref"} <= set(first)
    owner.command("edit", arguments(owner, "C"), command_id="receipt:C")
    assert owner.snapshot()["net_ref"] != first["net_ref"]
    before = state(owner)
    def forbidden(*args, **kwargs):
        raise AssertionError("receipt replay must not compile, publish, begin a transaction, or advance")
    monkeypatch.setattr("cpn.rpnh.owner_edits.compile_module", forbidden)
    monkeypatch.setattr(owner.control, "publish", forbidden)
    monkeypatch.setattr(owner._core, "begin", forbidden)
    monkeypatch.setattr(owner.control.edits, "advance", forbidden)
    reordered = dict(reversed(list(edit.items())))
    assert owner.command("edit", reordered, command_id="receipt:B") == first
    assert owner.read_edit_receipt("receipt:B") == first
    assert owner.command("read_edit_receipt", {"edit_command_id": "receipt:B"},
                         command_id="read-only-query") == first
    # Exercise existing dispatch and wire shapes directly, without AF_UNIX.
    loop = object.__new__(OwnerEventLoop)
    loop.owner = owner
    request = parse_command(serialize_command("query:2", "read_edit_receipt", {"edit_command_id": "receipt:B"}))
    assert loop.dispatch_command(request) == {"command_id": "query:2", "status": "ADOPTED", "result": first}
    client = ControlClient("unused-offline-path")
    monkeypatch.setattr(client, "request", lambda name, args, **kwargs: (name, args, kwargs))
    assert client.read_edit_receipt("receipt:B", command_id="query:3") == (
        "read_edit_receipt", {"edit_command_id": "receipt:B"}, {"command_id": "query:3"})
    assert state(owner) == before
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


@pytest.mark.parametrize("changed", [True, 1.0])
def test_same_command_rejects_python_equal_but_canonical_different_payload(tmp_path, changed):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    owner.command("edit", edit, command_id="identity")
    altered = deepcopy(edit)
    altered["candidate"]["components"][0]["config"]["capacities"]["request"] = changed
    assert altered == edit  # The former dict-equality branch accepted this.
    parsed = parse_command(serialize_command("identity", "edit", altered))
    before = state(owner)
    with pytest.raises(ValueError, match="different immutable candidate"):
        owner.command(parsed["command"], parsed["arguments"], command_id=parsed["command_id"])
    assert state(owner) == before


@pytest.mark.parametrize("changed", [float("nan"), object()])
def test_direct_owner_identity_rejects_non_json_before_writes(tmp_path, changed):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    edit["candidate"]["components"][0]["config"]["capacities"]["request"] = changed
    before = state(owner)
    with pytest.raises(ControlProtocolError, match="finite JSON"):
        owner.command("edit", edit, command_id="invalid-json")
    assert state(owner) == before


def test_pending_status_and_conflict_receipts_are_read_only(tmp_path):
    owner = make_owner(tmp_path)
    execution = begin(owner)
    edit = arguments(owner)
    pending = owner.command("edit", edit, command_id="pending")
    assert pending["status"] == "DRAINING"
    before = state(owner)
    assert owner.command("edit", edit, command_id="pending") == pending
    assert owner.read_edit_receipt("pending") == pending
    assert state(owner) == before
    settle(owner, execution)
    assert owner.read_edit_receipt("pending")["status"] == "ADOPTED"
    conflict = owner.command("edit", edit, command_id="stale-base")
    assert conflict["status"] == "CONFLICT"
    assert conflict["reason"] == "base NET changed; explicitly resubmit"
    assert conflict["active_net_ref"] == owner.snapshot()["net_ref"]
    before = state(owner)
    assert owner.command("edit", edit, command_id="stale-base") == conflict
    assert owner.read_edit_receipt("stale-base") == conflict
    assert state(owner) == before


def test_missing_and_candidate_only_are_explicit_without_repair(tmp_path):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    before = state(owner)
    assert owner.read_edit_receipt("absent") == {"status": "NOT_FOUND"}
    assert state(owner) == before
    owner.control.publish("candidate", "partial", None, edit,
        lineage=(owner.original_input_ref.as_version_ref(),))
    before = state(owner)
    incomplete = owner.read_edit_receipt("partial")
    assert incomplete["status"] == "INCOMPLETE"
    assert state(owner) == before
    # Current public semantics permit an explicit identical edit retry to finish
    # candidate-only preparation; the receipt query itself must remain read-only.
    adopted = owner.command("edit", edit, command_id="partial")
    assert adopted["status"] == "ADOPTED"
    assert owner.read_edit_receipt("partial") == adopted
    with pytest.raises(ValueError, match="explicit edit_command_id"):
        owner.command("read_edit_receipt", {"edit_command_id": "partial", "advance": True}, command_id="query")
    # An isolated status word is neither successful adoption nor no evidence.
    owner.control.publish("result", "orphan", None, {"status": "ADOPTED"}, suffix="orphan-fixture")
    before = state(owner)
    assert owner.read_edit_receipt("orphan")["status"] == "INCOMPLETE"
    assert state(owner) == before


def test_owner_input_mapping_retains_same_receipt(tmp_path):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    edit["candidate"]["components"][0]["config"]["capacities"]["request"] = 2
    edit["owner_inputs"] = {"step.request": [
        {"schema_id": TEXT, "value": "extra owner input", "summary": "Offline owner input"}]}
    receipt = owner.command("edit", edit, command_id="with-owner-input")
    assert receipt["status"] == "ADOPTED"
    assert len(receipt["owner_input_mappings"]) == 1
    before = state(owner)
    assert owner.read_edit_receipt("with-owner-input") == receipt
    assert owner.command("edit", edit, command_id="with-owner-input") == receipt
    assert state(owner) == before


def test_workspace_genesis_adoption_receipt_without_host_or_transport(tmp_path):
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
    receipt = owner.command("edit", edit, command_id="receipt-workspace:edit")
    assert receipt["status"] == "ADOPTED"
    successor = owner._core.get_version(_version_from_payload(receipt["checkpoint_ref"]).version_id).metadata
    assert successor["workspace_revision_refs"] == workspace_refs
    before = state(owner)
    assert owner.read_edit_receipt("receipt-workspace:edit") == receipt
    assert owner.command("edit", edit, command_id="receipt-workspace:edit") == receipt
    assert state(owner) == before
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)


@pytest.mark.parametrize("damage", ["missing_payload", "stale_checkpoint", "missing_adoption", "duplicate_result", "result_mapping"])
def test_adopted_receipt_requires_original_complete_closure(tmp_path, damage):
    owner = make_owner(tmp_path)
    edit = arguments(owner)
    first = owner.command("edit", edit, command_id="receipt")
    if damage in {"missing_payload", "result_mapping"}:
        result = _version_from_payload(first["result_ref"])
        path = owner._core.object_store.path_for_version(result.version_id)
        if damage == "missing_payload":
            path.unlink()
        else:
            raw = path.read_bytes()
            document = json.loads(raw)
            mapping = document["data"]["token_mappings"][0]["old_token_ref"]
            original = mapping["version_id"]
            mapping["version_id"] = original[:-1] + ("0" if original[-1] != "0" else "1")
            changed = canonical_json(document)
            assert len(changed) == len(raw)
            path.write_bytes(changed)
    elif damage == "duplicate_result":
        result = next(body for _, body in owner.control.records("result")
                      if body["data"].get("status") == "ADOPTED")
        owner.control.publish("result", "receipt", result["target"], result["data"],
            lineage=tuple(_version_from_payload(ref) for ref in result["lineage"]), suffix="duplicate-fixture")
    else:
        with owner._core.event_store.connect() as db:
            row = db.execute("SELECT * FROM events WHERE event_type='net_adopted/v1' AND command_id='receipt'").fetchone()
            if damage == "missing_adoption":
                db.execute("DELETE FROM events WHERE event_id=?", (row["event_id"],))
            else:
                payload = json.loads(row["payload_json"])
                payload["owner_candidate_checkpoint_ref"] = payload["submission_checkpoint_ref"]
                db.execute("UPDATE events SET payload_json=? WHERE event_id=?",
                    (canonical_json(payload).decode(), row["event_id"]))
    before = state(owner)
    with pytest.raises((RegistryCorruptError, ResourceIntegrityFault, ObjectIntegrityError)):
        owner.read_edit_receipt("receipt")
    assert state(owner) == before
