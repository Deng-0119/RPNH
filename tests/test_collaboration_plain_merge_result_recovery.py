"""Actual committed prefixes, complete request freeze and reopened owner replay."""
from copy import deepcopy
import hashlib
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_result import fixture
from test_collaboration_plain_merge_resolution import conflicting, choices_for
from cpn.rpnh.collaboration import PlainModuleMergeAuthor, SourceQualifiedResourceRef, validate_closed_revision
from cpn.rpnh.collaboration import plain_merge_result as implementation
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction

CUTS = ("command", *implementation.ROLES, "revision")


class DurableCut(RuntimeError):
    """The original Registry commit completed before its reply was lost."""


def facts(core):
    with core.event_store.connect() as db:
        commits = db.execute("SELECT COUNT(*) FROM transactions WHERE status='committed'").fetchone()[0]
    return {"objects": len(core.event_store.object_rows()), "events": len(core.event_store.list_events()),
            "committed_transactions": commits}


@pytest.mark.parametrize("cut", CUTS)
def test_every_committed_result_cut_reopens_and_preserves_the_first_complete_request(fixture, monkeypatch, cut):
    inputs, author = fixture
    core, gateway = inputs[:2]
    analysis, left, right = conflicting(inputs)
    args = {"analysis_ref": analysis.analysis_ref, "choices": choices_for(analysis, "local"), "command_id": "durable-result"}
    key = implementation._key(args["command_id"])
    by_key = {key + ":" + role: role for role in CUTS[:-1]}
    by_key[key] = "revision"
    before, observed, committed = facts(core), [], {}
    original = RegistryTransaction.commit
    def commit_then_interrupt(transaction):
        value = original(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key in by_key:
            role = by_key[transaction.idempotency_key]
            assert role == CUTS[len(observed)] and transaction._closed
            assert len(transaction._objects) == 1
            prepared = transaction._objects[0]
            row = core.event_store.object_row(prepared.version_id)
            assert row is not None and row["transaction_id"] == str(transaction.transaction_id)
            with core.event_store.connect() as db:
                assert db.execute("SELECT status FROM transactions WHERE transaction_id=?",
                    (str(transaction.transaction_id),)).fetchone()[0] == "committed"
            path = core.object_store.path_for_version(prepared.version_id)
            raw = path.read_bytes()
            committed[role] = (prepared.version_id, raw)
            observed.append({"role": role, "version_id": str(prepared.version_id),
                "transaction_id": str(transaction.transaction_id), "sha256": hashlib.sha256(raw).hexdigest()})
            if role == cut:
                raise DurableCut("committed " + role)
        return value
    monkeypatch.setattr(RegistryTransaction, "commit", commit_then_interrupt)
    with pytest.raises(DurableCut, match="committed " + cut):
        author.publish(**args)
    monkeypatch.setattr(RegistryTransaction, "commit", original)
    prefix = CUTS.index(cut) + 1
    assert [row["role"] for row in observed] == list(CUTS[:prefix])
    after_cut = facts(core)
    assert after_cut["objects"] - before["objects"] == prefix
    assert after_cut["committed_transactions"] - before["committed_transactions"] == prefix
    command = json.loads(committed["command"][1])
    assert command["requested_choices"] == args["choices"]
    assert len(command["prepared_materials"]) == 5
    for spec in command["prepared_materials"]:
        assert spec["sha256"] == hashlib.sha256(canonical_json(spec["document"])).hexdigest()
        assert spec["metadata"]["content_schema_authority_ref"] == command["schema_authorities"][spec["schema"]]

    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    new_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = PlainModuleMergeAuthor(new_gateway, support.registration(), author.producer)
    assert reopened.writer_epoch != core.writer_epoch
    assert facts(reopened) == after_cut
    changed = deepcopy(args["choices"])
    changed[0]["reason"] += "; a changed caller reason"
    with pytest.raises(RegistryConflict, match="conflict"):
        replay.publish(**{**args, "choices": changed})
    assert facts(reopened) == after_cut
    for version, raw in committed.values():
        assert reopened.object_store.path_for_version(version).read_bytes() == raw

    value = replay.publish(**args)
    assert value.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert value.revision.revision_ref.to_dict() == command["result_revision_ref"]
    assert value.module.components[1].operations[0].config == {"value": True}
    for spec in command["prepared_materials"]:
        ref = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=reopened.catalog)
        assert reopened.object_store.path_for_version(ref.ref.resource_version_id).read_bytes() == canonical_json(spec["document"])
    for version, raw in committed.values():
        assert reopened.object_store.path_for_version(version).read_bytes() == raw
    final = facts(reopened)
    assert final["objects"] - before["objects"] == 7
    assert final["committed_transactions"] - before["committed_transactions"] == 7
    if cut == "revision":
        assert final == after_cut
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    assert validate_closed_revision(reader, value.revision.revision_ref, support.registration()).revision == value.revision
    assert replay.publish(**args).revision == value.revision
    assert facts(reopened) == final
    support.assert_inert(reopened, 4)
    print("B1_DURABLE_CUT=" + json.dumps({"cut": cut, "before": before, "after_cut": after_cut,
        "final": final, "committed_prefix": observed, "reopened_epoch": reopened.writer_epoch,
        "old_epoch": core.writer_epoch}, sort_keys=True))
