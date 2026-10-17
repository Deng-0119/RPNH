"""Deterministic actual private Registry/PN executions, no model/provider."""
from dataclasses import replace
import json

import pytest

from examples.multiuser_release_handoff.delegation import (
    SourceDelegation, SourceBusy, StaleVersion, DelegationDenied, _inventory,
)
from cpn.rpnh.collaboration import user_collaboration_schema_data
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.run import OwnerInput, start_run
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_static_lease_reads import _registration, _simple_module, TEXT


def private_user(path, name):
    module = _simple_module(name)
    schemas, types, paths = user_collaboration_schema_data()
    task = OwnerInput(TEXT, canonical_json(name), name)
    owner = start_run(module, _registration(), run_dir=path, task_input=task,
        entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="deterministic-native", owner_statement="Private user HOST fixture", owner_name=name,
        command_id="fresh:" + name, catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner.schema_gateway.bind_source_identity(source_id=name, command_id="source:" + name)
    execution = owner.start(owner.admit("step.run", logical_tau=0, command_id="admit:" + name), command_id="start:" + name)
    return owner, execution


@pytest.fixture
def world(tmp_path):
    users = {name: private_user(tmp_path / name, name) for name in ("qa", "support", "editor")}
    source = SourceDelegation.create(run_dir=tmp_path / "source", body="release one",
        requesters={k: v[0] for k, v in users.items()}, editors=(users["editor"][0].principal_ref,))
    def bind(name, **kwargs):
        owner, execution = users[name]
        return source.bind_execution(origin_owner=owner, execution=execution, principal_ref=owner.principal_ref,
                                     call_id="call:" + name, tool_name="release_note", arguments={}, **kwargs)
    return source, users, bind


def test_native_overlapping_readers_edit_and_stable_slot(world):
    source, users, bind = world
    before = source.current()
    qa, support, editor = bind("qa"), bind("support"), bind("editor")
    first = source.begin_read(qa)
    second = source.begin_read(support)
    assert first.result["body"] == second.result["body"] == "release one"
    assert first.result["token_ref"] == second.result["token_ref"] == before["token_ref"]
    assert first.result["requester_principal_ref"] != first.result["source_caller_principal_ref"]
    a, b = first.execution.operation.firing, second.execution.operation.firing
    assert a.transition_firing_ref != b.transition_firing_ref
    assert set(a.claimed_input_refs) & set(b.claimed_input_refs)  # same native lease token
    with pytest.raises(SourceBusy):
        source.edit(editor, expected_version=before["version"], new_body="release two")
    source.finish_read(first)
    with pytest.raises(SourceBusy):
        source.edit(editor, expected_version=before["version"], new_body="release two")
    source.finish_read(second)
    result = source.edit(editor, expected_version=before["version"], new_body="release two")
    assert result["status"] == "published"
    assert result["slot_ref"] == before["slot_ref"]
    assert result["version"] != before["version"]
    assert result["token_ref"] != before["token_ref"]
    assert first.result["version"] == before["version"]
    fresh = source.begin_read(bind("qa"))
    assert fresh.result["body"] == "release two"
    source.finish_read(fresh)
    with pytest.raises(StaleVersion):
        source.edit(editor, expected_version=before["version"], new_body="stale")


def test_wrong_principal_wrong_execution_and_unauthorized_editor(world):
    source, users, bind = world
    owner, execution = users["qa"]
    with pytest.raises(DelegationDenied):
        source.bind_execution(origin_owner=owner, execution=execution, principal_ref=users["editor"][0].principal_ref,
                              call_id="wrong", tool_name="edit", arguments={})
    with pytest.raises(Exception):
        source.bind_execution(origin_owner=owner, execution=users["support"][1], principal_ref=owner.principal_ref,
                              call_id="wrong-execution", tool_name="read", arguments={})
    binding = bind("qa")
    with pytest.raises(DelegationDenied):
        source.edit(binding, expected_version=source.current()["version"], new_body="unauthorized")
    outputs = owner.products(execution, outcome_id="complete", products={"step.result": (canonical_json("done"),)}, command_id="foreign-products")
    owner.succeed(outputs, command_id="foreign-done")
    with pytest.raises(Exception):
        source.begin_read(binding)


def test_failed_edit_retains_native_claim(world, monkeypatch):
    source, _, bind = world
    before = source.current()
    pending_read = bind("qa")
    edit = source.begin_edit(bind("editor"), expected_version=before["version"], new_body="unknown")
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic output failure; outcome unknown")
    monkeypatch.setattr(source.owner, "products", fail)
    with pytest.raises(RuntimeError, match="unknown"):
        source.commit_edit(edit)
    # Native admission, independently of HOST handles, still refuses a reader.
    from cpn.rpnh.registry.publication import _ref_payload
    with _inventory(source.owner):
        assert source.owner.admit("source.write", logical_tau=0, command_id="still-held") is None
        assert _ref_payload(edit.execution.operation.firing.transition_firing_ref) in source.owner.snapshot()["active_firings"]
