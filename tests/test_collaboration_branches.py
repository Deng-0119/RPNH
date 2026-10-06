"""Offline same-owner Branch creation, compare-and-append, and replay tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from threading import Barrier

import pytest

from cpn.rpnh.collaboration import (
    AuthorRevisionReadError, BranchVersion, NetRevision, SourceQualifiedResourceRef, SourceQualifiedVersionRef,
    branch_schema_data, current_branch, read_branch_version,
)
from cpn.rpnh.registry._event_store import branch_publication
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import PendingEvent, VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = branch_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(tmp_path / "branches", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("branch-fixture/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id), "display_name": "Author"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
                       payload=canonical_json(body), metadata=body, media_type="application/json",
                       schema_ref="registry_v1/principal/v1", idempotency_key="fixture:principal")

    def revision(*parents):
        source = "source-a"
        resource = lambda: SourceQualifiedResourceRef(source, ResourceVersionRef(new_id("resource"), new_id("resource_version")))
        ref = SourceQualifiedVersionRef(source, VersionRef("collaboration_net_revision/v1", new_id("resource"), new_id("resource_version")))
        value = NetRevision(ref, SourceQualifiedVersionRef(source, owner.task_ref), SourceQualifiedVersionRef(source, principal),
            f"revision:{ref.ref.version_id}", "closed_module", resource(), tuple(parent.revision_ref for parent in parents), (),
            resource(), resource(), resource(), None)
        core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
                           payload=canonical_json(value.to_dict()), metadata=value.to_dict(), media_type="application/json",
                           schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key=value.command_id)
        return value

    return core, gateway, revision, owner, bootstrap


def _counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events()), len(core.event_store.outbox_rows())


def _advance(gateway, branch, target, command):
    return gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=branch.head_revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=target.revision_ref, command_id=command)


def test_creation_and_direct_advance_keep_exact_provenance_without_adoption(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    assert first.sequence == 1 and first.fork_base_revision_ref == r0.revision_ref
    r1 = revision(r0)
    second = _advance(gateway, first, r1, "branch:advance")
    assert second.sequence == 2 and second.expected_stream_head == 1
    assert second.predecessor_branch_ref == first.branch_ref
    assert second.expected_head_revision_ref == r0.revision_ref
    assert second.head_revision_ref == r1.revision_ref
    assert second.branch_ref.ref.entity_id == first.branch_ref.ref.entity_id
    assert second.branch_ref != first.branch_ref
    assert current_branch(core, first.branch_ref.ref.entity_id) == second
    assert read_branch_version(core, first.branch_ref) == first
    assert core.branch_id == "main"
    assert core.event_store.list_events_by_type(("net_adopted/v1",)) == ()


def test_create_and_advance_replay_after_later_advances_and_reopen(fixture):
    core, gateway, revision, owner, bootstrap = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    second = _advance(gateway, first, r1, "branch:a1")
    r2 = revision(r1)
    third = _advance(gateway, second, r2, "branch:a2")
    before = _counts(core)
    assert gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create") == first
    assert _advance(gateway, first, r1, "branch:a1") == second
    assert _counts(core) == before
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, owner.task_ref, bootstrap)
    assert _advance(next_gateway, first, r1, "branch:a1") == second
    assert current_branch(reopened, first.branch_ref.ref.entity_id) == third
    assert _counts(reopened) == before


@pytest.mark.parametrize("field", ["branch", "head", "stream"])
def test_each_caller_expectation_is_checked_against_the_commit_snapshot(fixture, field):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    second = _advance(gateway, first, r1, "branch:a1")
    r2 = revision(r1)
    args = {"expected_branch_version_ref": second.branch_ref, "expected_head_revision_ref": r1.revision_ref,
            "expected_stream_head": 2, "next_revision_ref": r2.revision_ref, "command_id": "branch:stale"}
    args[{"branch": "expected_branch_version_ref", "head": "expected_head_revision_ref", "stream": "expected_stream_head"}[field]] = {
        "branch": first.branch_ref, "head": r0.revision_ref, "stream": 1}[field]
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="stale"):
        gateway.advance_author_branch(**args)
    assert _counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == second


@pytest.mark.parametrize("same_command", [False, True])
def test_concurrent_advances_commit_once_or_replay_the_same_result(fixture, same_command):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    a, b = revision(r0), revision(r0)
    barrier = Barrier(2)

    def submit(index):
        barrier.wait()
        try:
            return _advance(gateway, first, a if same_command or index == 0 else b,
                            "branch:same" if same_command else f"branch:{index}")
        except RegistryConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(submit, (0, 1)))
    winners = [value for value in values if isinstance(value, BranchVersion)]
    assert len(winners) == (2 if same_command else 1)
    assert all(value == winners[0] for value in winners)
    assert current_branch(core, first.branch_ref.ref.entity_id) == winners[0]
    assert len(core.event_store.object_rows_by_type(branch_publication.BRANCH_TYPE)) == 2


@pytest.mark.parametrize("change", ["head", "stream", "target"])
def test_reusing_a_command_with_changed_material_conflicts(fixture, change):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1, sibling = revision(r0), revision(r0)
    result = _advance(gateway, first, r1, "branch:once")
    args = {"expected_branch_version_ref": first.branch_ref, "expected_head_revision_ref": r0.revision_ref,
            "expected_stream_head": 1, "next_revision_ref": r1.revision_ref, "command_id": "branch:once"}
    args[{"head": "expected_head_revision_ref", "stream": "expected_stream_head", "target": "next_revision_ref"}[change]] = {
        "head": r1.revision_ref, "stream": 2, "target": sibling.revision_ref}[change]
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        gateway.advance_author_branch(**args)
    assert _counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == result


def test_old_version_is_rejected_even_for_a_matching_head_in_an_aba_shaped_snapshot(fixture, monkeypatch):
    # Reset is not enabled. Inject only the observed head shape to exercise the
    # version guard independently of the revision-head comparison.
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    target = revision(r0)
    observed = first.to_dict()
    observed["branch_ref"]["ref"]["version_id"] = str(new_id("resource_version"))
    observed["sequence"] = 3
    monkeypatch.setattr(branch_publication, "current_branch_document", lambda *args: deepcopy(observed))
    with pytest.raises(RegistryConflict, match="stale"):
        gateway.advance_author_branch(expected_branch_version_ref=first.branch_ref,
            expected_head_revision_ref=r0.revision_ref, expected_stream_head=3,
            next_revision_ref=target.revision_ref, command_id="branch:stale-aba")


@pytest.mark.parametrize("target_kind", ["reset", "skip"])
def test_reset_and_skipping_a_direct_parent_are_not_enabled(fixture, target_kind):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    second = _advance(gateway, first, r1, "branch:a1")
    target = r0 if target_kind == "reset" else revision(revision(r1))
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="direct-descendant"):
        _advance(gateway, second, target, "branch:unsupported")
    assert _counts(core) == before


def test_reusing_an_advance_command_as_its_own_predecessor_is_a_conflict(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    second = _advance(gateway, first, r1, "branch:once")
    r2 = revision(r1)
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="predecessor"):
        _advance(gateway, second, r2, "branch:once")
    assert _counts(core) == before


def test_fork_preserves_the_exact_upstream_version_and_base(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    upstream = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:upstream")
    fork = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:fork",
                                       upstream_branch_ref=upstream.branch_ref)
    assert fork.branch_ref.ref.entity_id != upstream.branch_ref.ref.entity_id
    assert fork.upstream_branch_ref == upstream.branch_ref
    assert fork.fork_base_revision_ref == r0.revision_ref


def test_failed_publication_rolls_back_and_identical_retry_recovers(fixture, monkeypatch):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    before = _counts(core)
    original = core.event_store._insert_event

    def fail(db, event):
        original(db, event)
        if event.payload.get("object_type") == branch_publication.BRANCH_TYPE:
            raise RuntimeError("injected branch publication failure")

    monkeypatch.setattr(core.event_store, "_insert_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        _advance(gateway, first, r1, "branch:a1")
    assert _counts(core) == before
    monkeypatch.setattr(core.event_store, "_insert_event", original)
    assert current_branch(core, first.branch_ref.ref.entity_id) == first
    assert _advance(gateway, first, r1, "branch:a1").sequence == 2


def test_unconfigured_reader_rejects_branch_type(fixture):
    core, gateway, revision, _, _ = fixture
    first = gateway.create_author_branch(head_revision_ref=revision().revision_ref, command_id="branch:create")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True)
    with pytest.raises(SchemaGovernanceError):
        read_branch_version(reader, first.branch_ref)


def _successor(first, target, task_id, command):
    return replace(first,
        branch_ref=SourceQualifiedVersionRef("source-a", VersionRef(branch_publication.BRANCH_TYPE,
            first.branch_ref.ref.entity_id, branch_publication.branch_version_id(task_id, command))),
        head_revision_ref=target.revision_ref, predecessor_branch_ref=first.branch_ref,
        expected_head_revision_ref=first.head_revision_ref, expected_stream_head=first.sequence,
        sequence=first.sequence + 1, command_id=command)


def _stage_branch(core, value, *, document=None, key=None):
    body = value.to_dict() if document is None else document
    ref = value.branch_ref.ref
    tx = core.begin(idempotency_key=key or branch_publication.branch_command_key(value.command_id))
    obj = tx.prewrite(object_type=branch_publication.BRANCH_TYPE, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref=branch_publication.BRANCH_SCHEMA)
    return tx, obj


@pytest.mark.parametrize("mutation", ["owner", "source", "stream", "key", "extra_event"])
def test_direct_core_staging_cannot_bypass_owner_cas_or_exact_fact_set(fixture, mutation):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    value = _successor(first, r1, core.task_id, "branch:direct")
    document = value.to_dict()
    if mutation == "owner":
        document["owner_task_ref"]["ref"]["version_id"] = str(new_id("task_version"))
    elif mutation == "source":
        for field, entry in document.items():
            if field.endswith("_ref") and entry is not None:
                entry["source_id"] = "foreign-source"
    elif mutation == "stream":
        document["expected_stream_head"], document["sequence"] = 5, 6
    tx, _ = _stage_branch(core, value, document=document, key="wrong-command-key" if mutation == "key" else None)
    if mutation == "extra_event":
        tx.append(PendingEvent(event_type="transaction_committed/v1", criticality="authoritative",
            stream_id="extra-branch-fact", aggregate_id="extra", aggregate_type="transaction",
            idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key,
            payload={"object_count": 0, "relation_count": 0, "fact_count": 0},
            payload_schema_ref="registry_v1/transaction_committed/v1"))
    before = _counts(core)
    with pytest.raises(RegistryConflict):
        tx.commit()
    assert _counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == first


def test_direct_event_store_batch_requires_the_matching_branch_publication_fact(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    target = revision(r0)
    value = _successor(first, target, core.task_id, "branch:missing-fact")
    tx, obj = _stage_branch(core, value)
    terminal = PendingEvent(event_type="transaction_committed/v1", criticality="authoritative",
        stream_id=f"transaction:{tx.transaction_id}", aggregate_id=str(tx.transaction_id), aggregate_type="transaction",
        idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key,
        payload={"object_count": 1, "relation_count": 0, "fact_count": 0},
        payload_schema_ref="registry_v1/transaction_committed/v1")
    before = _counts(core)
    with pytest.raises(RegistryConflict):
        core.event_store.publish_batch(task_id=core.task_id, branch_id=core.branch_id, task_round_id=None, net_instance_id=None,
            transaction_id=tx.transaction_id, idempotency_key=tx.idempotency_key, writer_epoch=core.writer_epoch,
            objects=(obj,), events=(terminal,), relations=(), expected_heads={terminal.stream_id: 0})
    assert _counts(core) == before


def test_another_object_type_cannot_occupy_an_existing_branch_identity(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    record = replace(r0, revision_ref=SourceQualifiedVersionRef("source-a", VersionRef("collaboration_net_revision/v1",
        first.branch_ref.ref.entity_id, new_id("resource_version"))), command_id="author:collision")
    ref = record.revision_ref.ref
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="Branch publication"):
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
            schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key="author:collision")
    assert _counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == first


def test_preoccupied_creation_identity_is_rejected_without_overwrite(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    command = "branch:occupied"
    logical = branch_publication.branch_id_for_command(core.task_id, "source-a", command)
    collision = replace(r0, revision_ref=SourceQualifiedVersionRef("source-a", VersionRef("collaboration_net_revision/v1",
        logical, new_id("resource_version"))), command_id="author:preoccupy")
    ref = collision.revision_ref.ref
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(collision.to_dict()), metadata=collision.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key="author:preoccupy")
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="incompatible"):
        gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id=command)
    assert _counts(core) == before


@pytest.mark.parametrize("damage", ["publication", "commit", "self"])
def test_reader_rejects_missing_canonical_evidence_or_wrong_self_identity(fixture, damage):
    core, gateway, revision, _, _ = fixture
    first = gateway.create_author_branch(head_revision_ref=revision().revision_ref, command_id="branch:create")
    if damage == "self":
        wrong = SourceQualifiedVersionRef("source-a", replace(first.branch_ref.ref, entity_id=new_id("resource")))
        with pytest.raises(RegistryConflict, match="canonical"):
            read_branch_version(core, wrong)
        return
    with core.event_store.connect() as db:
        obj = db.execute("SELECT published_event_id,transaction_id FROM objects WHERE version_id=?",
                         (str(first.branch_ref.ref.version_id),)).fetchone()
        event = (obj["published_event_id"] if damage == "publication" else db.execute(
            "SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
            (obj["transaction_id"],)).fetchone()[0])
        db.execute("DELETE FROM events WHERE event_id=?", (event,))
    with pytest.raises(RegistryConflict, match="canonical"):
        read_branch_version(core, first.branch_ref)


@pytest.mark.parametrize("field,value", [("expected_stream_head", True), ("sequence", 0),
                                         ("command_id", ""), ("expected_stream_head", 1.0)])
def test_branch_python_contract_rejects_invalid_control_values(fixture, field, value):
    _, gateway, revision, _, _ = fixture
    first = gateway.create_author_branch(head_revision_ref=revision().revision_ref, command_id="branch:create")
    with pytest.raises(ValueError):
        replace(first, **{field: value})


def test_branch_wire_contract_is_closed_and_unknown_versions_fail(fixture):
    core, gateway, revision, _, _ = fixture
    first = gateway.create_author_branch(head_revision_ref=revision().revision_ref, command_id="branch:create")
    assert BranchVersion.from_dict(first.to_dict(), catalog=core.catalog) == first
    for mutation in ("extra", "v2", "newline"):
        document = first.to_dict()
        if mutation == "extra":
            document["adopted"] = True
        elif mutation == "v2":
            document["schema_version"] = "registry_v1/collaboration_branch/v2"
        else:
            document["command_id"] += "\n"
        with pytest.raises(SchemaGovernanceError):
            BranchVersion.from_dict(document, catalog=core.catalog)


def test_branch_validator_runs_inside_the_single_real_commit_snapshot(fixture, monkeypatch):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    r1 = revision(r0)
    original = branch_publication.validate_branch_publication
    seen = []

    def validate(context):
        assert context.db.in_transaction
        head = branch_publication.current_branch_document(context.db, context.event_store.catalog,
            context.task_id, str(first.branch_ref.ref.entity_id), core.object_store)
        assert head["branch_ref"] == first.branch_ref.to_dict()
        seen.append(context.db)
        return original(context)

    monkeypatch.setattr(branch_publication, "validate_branch_publication", validate)
    _advance(gateway, first, r1, "branch:a1")
    assert len(seen) == 1


def _direct_batch(core, tx, obj):
    publication = PendingEvent(event_type="object_version_published/v1", criticality="authoritative",
        stream_id=f"object:{obj.logical_id}", aggregate_id=str(obj.logical_id), aggregate_type=obj.object_type,
        idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key,
        payload={"logical_id": str(obj.logical_id), "version_id": str(obj.version_id), "object_type": obj.object_type,
                 "size": obj.size, "media_type": obj.media_type, "schema_ref": obj.schema_ref,
                 "storage_locator": obj.storage_locator, "metadata": dict(obj.metadata)},
        payload_schema_ref="registry_v1/object_version_published/v1")
    terminal = PendingEvent(event_type="transaction_committed/v1", criticality="authoritative",
        stream_id=f"transaction:{tx.transaction_id}", aggregate_id=str(tx.transaction_id), aggregate_type="transaction",
        idempotency_key=tx.idempotency_key, command_id=tx.idempotency_key,
        payload={"object_count": 1, "relation_count": 0, "fact_count": 1},
        payload_schema_ref="registry_v1/transaction_committed/v1")
    heads = core.event_store.stream_heads()
    return core.event_store.publish_batch(task_id=core.task_id, branch_id=core.branch_id,
        task_round_id=None, net_instance_id=None, transaction_id=tx.transaction_id,
        idempotency_key=tx.idempotency_key, writer_epoch=core.writer_epoch,
        objects=(obj,), events=(publication, terminal), relations=(),
        expected_heads={publication.stream_id: heads.get(publication.stream_id, 0), terminal.stream_id: 0})


@pytest.mark.parametrize("route", ["transaction", "batch"])
@pytest.mark.parametrize("damage", ["media", "payload"])
def test_branch_bytes_and_media_are_enforced_at_both_core_commit_routes(fixture, route, damage):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    target = revision(r0)
    value = _successor(first, target, core.task_id, "branch:bad-descriptor")
    body = value.to_dict()
    payload = deepcopy(body)
    if damage == "payload":
        payload["command_id"] = "different-payload-command"
    ref = value.branch_ref.ref
    tx = core.begin(idempotency_key=branch_publication.branch_command_key(value.command_id))
    obj = tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(payload), metadata=body, schema_ref=branch_publication.BRANCH_SCHEMA,
        media_type="text/plain" if damage == "media" else "application/json")
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="descriptor"):
        tx.commit() if route == "transaction" else _direct_batch(core, tx, obj)
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)
    assert current_branch(core, first.branch_ref.ref.entity_id) == first


@pytest.mark.parametrize("damage", ["locator", "size", "missing"])
def test_direct_batch_cannot_register_an_unreadable_prepared_branch(fixture, damage):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    value = _successor(first, revision(r0), core.task_id, "branch:bad-prepared")
    tx, obj = _stage_branch(core, value)
    if damage == "locator":
        obj = replace(obj, storage_locator="registry-object:" + str(new_id("resource_version")))
    elif damage == "size":
        obj = replace(obj, size=obj.size + 1)
    else:
        core.object_store.path_for_version(obj.version_id).unlink()
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="immutable bytes"):
        _direct_batch(core, tx, obj)
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


def _persist_revision(core, record, *, payload=None, media_type="application/json"):
    ref, body = record.revision_ref.ref, record.to_dict()
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body if payload is None else payload), metadata=body, media_type=media_type,
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key=record.command_id)
    return record


def _fresh_revision(record, *, command):
    return replace(record, revision_ref=SourceQualifiedVersionRef("source-a", VersionRef("collaboration_net_revision/v1",
        new_id("resource"), new_id("resource_version"))), command_id=command)


@pytest.mark.parametrize("target", ["revision", "principal"])
@pytest.mark.parametrize("damage", ["payload", "media"])
def test_direct_branch_commit_requires_readable_target_and_producer(fixture, target, damage):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    record = _fresh_revision(r0, command="author:target")
    record = replace(record, parent_revision_refs=(r0.revision_ref,))
    if target == "principal":
        ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
        metadata = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Metadata"}
        payload = {**metadata, "display_name": "Payload"} if damage == "payload" else metadata
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(payload), metadata=metadata, media_type="text/plain" if damage == "media" else "application/json",
            schema_ref="registry_v1/principal/v1", idempotency_key="author:bad-principal")
        record = replace(record, producer_principal_ref=SourceQualifiedVersionRef("source-a", ref))
        _persist_revision(core, record)
    else:
        payload = {**record.to_dict(), "command_id": "different-bytes"} if damage == "payload" else record.to_dict()
        _persist_revision(core, record, payload=payload, media_type="text/plain" if damage == "media" else "application/json")
    value = _successor(first, record, core.task_id, "branch:bad-target")
    tx, obj = _stage_branch(core, value)
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="descriptor"):
        _direct_batch(core, tx, obj)
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


def _temporary_member(core, member_kind, identity):
    # Inject only the Registry publication projection in a disposable database;
    # these tests do not claim a full firing/OwnerEventLoop acceptance run.
    root = str(new_id("transition_firing_version"))
    with core.event_store.connect() as db:
        tx = db.execute("SELECT transaction_id FROM transactions WHERE status='committed' LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO firing_publications(firing_version_id,firing_logical_id,invocation_version_id,"
            "invocation_logical_id,net_version_id,operation_binding_version_id,admission_checkpoint_version_id,"
            "state,opened_transaction_id) VALUES(?,?,?,?,?,?,?,'PROVISIONAL',?)",
            (root, str(new_id("transition_firing")), str(new_id("invocation_version")), str(new_id("invocation")),
             str(new_id("net_instance_version")), str(new_id("operation_binding_version")),
             str(new_id("marking_checkpoint_version")), tx))
        db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)", (root, member_kind, identity, tx))
    return root


@pytest.mark.parametrize("target", ["revision", "principal"])
@pytest.mark.parametrize("damage", ["missing_commit", "observational_commit", "provisional_commit", "missing_publication", "provisional_object"])
def test_branch_commit_requires_target_and_producer_canonical_closure(fixture, target, damage):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    record = revision(r0)
    ref = record.revision_ref if target == "revision" else record.producer_principal_ref
    value = _successor(first, record, core.task_id, "branch:damaged-authority")
    tx, obj = _stage_branch(core, value)
    with core.event_store.connect() as db:
        row = db.execute("SELECT * FROM objects WHERE version_id=?", (str(ref.ref.version_id),)).fetchone()
        terminal = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
            (row["transaction_id"],)).fetchone()[0]
        if damage in {"missing_commit", "missing_publication"}:
            db.execute("DELETE FROM events WHERE event_id=?", (terminal if damage == "missing_commit" else row["published_event_id"],))
        elif damage == "observational_commit":
            db.execute("UPDATE events SET criticality='observational' WHERE event_id=?", (terminal,))
    if damage == "provisional_commit":
        _temporary_member(core, "event", terminal)
    elif damage == "provisional_object":
        _temporary_member(core, "object", str(ref.ref.version_id))
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="canonical|provisional"):
        _direct_batch(core, tx, obj)
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


@pytest.mark.parametrize("target", ["revision", "principal"])
def test_gateway_rejects_missing_target_or_producer_commit(fixture, target):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    record = revision(r0)
    ref = record.revision_ref if target == "revision" else record.producer_principal_ref
    with core.event_store.connect() as db:
        db.execute("DELETE FROM events WHERE event_type='transaction_committed/v1' AND transaction_id="
            "(SELECT transaction_id FROM objects WHERE version_id=?)", (str(ref.ref.version_id),))
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises((RegistryConflict, AuthorRevisionReadError), match="canonical"):
        _advance(gateway, first, record, "branch:missing-terminal")
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


@pytest.mark.parametrize("promotion", ["valid", "missing_commit", "provisional_commit"])
def test_published_members_remain_usable_only_with_canonical_promotion(fixture, promotion):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    record = revision(r0)
    tx = core.begin(idempotency_key="fixture:promotion")
    terminal = tx.commit()[-1]
    for ref in (record.revision_ref, record.producer_principal_ref):
        root = _temporary_member(core, "object", str(ref.ref.version_id))
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(tx.transaction_id), str(new_id("operation_result_version")),
                 str(new_id("marking_checkpoint_version")), root))
            # Promotion terminal can legitimately be in its published root.
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
                (root, "event", str(terminal.event_id), str(tx.transaction_id)))
    if promotion == "missing_commit":
        with core.event_store.connect() as db:
            db.execute("DELETE FROM events WHERE event_id=?", (str(terminal.event_id),))
    elif promotion == "provisional_commit":
        _temporary_member(core, "event", str(terminal.event_id))
    value = _successor(first, record, core.task_id, "branch:promoted")
    staged, obj = _stage_branch(core, value)
    before, heads = _counts(core), core.event_store.stream_heads()
    if promotion == "valid":
        _direct_batch(core, staged, obj)
        assert current_branch(core, first.branch_ref.ref.entity_id) == value
    else:
        with pytest.raises(RegistryConflict, match="canonical|provisional"):
            _direct_batch(core, staged, obj)
        assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


def test_current_branch_cannot_redirect_to_another_same_sequence_branch(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    a = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:a")
    b = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:b")
    with core.event_store.connect() as db:
        row = db.execute("SELECT * FROM events WHERE stream_id=?", (f"object:{a.branch_ref.ref.entity_id}",)).fetchone()
        payload = json.loads(row["payload_json"])
        payload["metadata"]["branch_ref"] = b.branch_ref.to_dict()
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(payload), row["event_id"]))
    with pytest.raises(RegistryConflict, match="redirect"):
        current_branch(core, a.branch_ref.ref.entity_id)
    assert current_branch(core, b.branch_ref.ref.entity_id) == b


def test_real_cyclic_revision_descriptors_cannot_reset_the_branch(fixture):
    core, gateway, revision, _, _ = fixture
    template = revision()
    a, b = _fresh_revision(template, command="author:a"), _fresh_revision(template, command="author:b")
    a, b = replace(a, parent_revision_refs=(b.revision_ref,)), replace(b, parent_revision_refs=(a.revision_ref,))
    _persist_revision(core, a)
    _persist_revision(core, b)
    first = gateway.create_author_branch(head_revision_ref=a.revision_ref, command_id="branch:create")
    second = _advance(gateway, first, b, "branch:to-b")
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="reset to a prior exact head"):
        _advance(gateway, second, a, "branch:back-to-a")
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)
    assert current_branch(core, first.branch_ref.ref.entity_id) == second


def test_direct_advance_rejects_unreadable_predecessor_bytes(fixture):
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    value = _successor(first, revision(r0), core.task_id, "branch:after-damage")
    tx, obj = _stage_branch(core, value)
    path = core.object_store.path_for_version(first.branch_ref.ref.version_id)
    payload = path.read_bytes()
    path.write_bytes(payload.replace(b'branch:create', b'branch:broken'))
    before, heads = _counts(core), core.event_store.stream_heads()
    with pytest.raises(RegistryConflict, match="bytes differ"):
        _direct_batch(core, tx, obj)
    assert (_counts(core), core.event_store.stream_heads()) == (before, heads)


def test_shared_descriptor_bytes_compare_json_number_representations_exactly(fixture):
    from cpn.rpnh.registry._event_store.collaboration_descriptors import readable_descriptor
    core, gateway, revision, _, _ = fixture
    r0 = revision()
    first = gateway.create_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:create")
    value = _successor(first, revision(r0), core.task_id, "branch:number-types")
    metadata, payload = value.to_dict(), value.to_dict()
    payload["expected_stream_head"] = 1.0  # Python equality would hide this.
    tx = core.begin(idempotency_key="prepared-only:number-types")
    ref = value.branch_ref.ref
    obj = tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(payload), metadata=metadata, media_type="application/json",
        schema_ref=branch_publication.BRANCH_SCHEMA)
    with pytest.raises(RegistryConflict, match="bytes differ"):
        readable_descriptor(core.object_store, obj)
