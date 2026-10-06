"""Socket-free owner/source binding and provisional-visibility regressions.

Provisional membership is injected into disposable fixture databases to test
the exact commit scanner. This is not an execution/OwnerEventLoop acceptance.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from threading import Barrier

import pytest

from cpn.rpnh.collaboration import (
    AuthorRevisionReadError, NetRevision, read_net_revision,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
    get_local_source_identity, source_identity_schema_data,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry._event_store import source_identity
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError, StaleWriterError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import PendingEvent, VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, TypeDefinition, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload


PROBE_TYPE = "source_probe/v1"
PROBE_SCHEMA = "registry_v1/source_probe/v1"


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = source_identity_schema_data()
    schemas[PROBE_SCHEMA] = {
        "$schema": "http://json-schema.org/draft-07/schema#", "$id": PROBE_SCHEMA,
        "type": "object", "additionalProperties": False,
        "properties": {"reference": {}}, "required": ["reference"],
    }
    probe = TypeDefinition(PROBE_TYPE, "object", "test", PROBE_SCHEMA, None,
                           "task-scoped", "run-fact", "test", "test")
    catalog = SchemaCatalog(schemas=schemas, types=(*types, probe), schema_paths=paths)
    core = _RegistryCore(tmp_path / "source", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("source-fixture/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    return core, gateway, owner, bootstrap


def _counts(core):
    return (len(core.event_store.object_rows()), len(core.event_store.list_events()),
            len(core.event_store.outbox_rows()))


def _publish_probe(core, reference):
    ref = VersionRef(PROBE_TYPE, new_id("resource"), new_id("resource_version"))
    body = {"reference": reference}
    core.publish_bytes(object_type=PROBE_TYPE, logical_id=ref.entity_id, version_id=ref.version_id,
                       payload=canonical_json(body), metadata=body, media_type="application/json",
                       schema_ref=PROBE_SCHEMA, idempotency_key=f"probe:{ref.version_id}")
    return ref


def _mark_provisional(core, *, member_kind, identity):
    """Inject an existing member under a foreign open root in this test DB."""
    root = str(new_id("transition_firing_version"))
    with core.event_store.connect() as db:
        tx = db.execute("SELECT transaction_id FROM transactions WHERE status='committed' LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO firing_publications(firing_version_id,firing_logical_id,invocation_version_id,"
                   "invocation_logical_id,net_version_id,operation_binding_version_id,admission_checkpoint_version_id,"
                   "state,opened_transaction_id) VALUES(?,?,?,?,?,?,?,'PROVISIONAL',?)",
                   (root, str(new_id("transition_firing")), str(new_id("invocation_version")),
                    str(new_id("invocation")), str(new_id("net_instance_version")),
                    str(new_id("operation_binding_version")), str(new_id("marking_checkpoint_version")), tx))
        db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)", (root, member_kind, identity, tx))
    return root


def _wrapper(ref, source, kind):
    if kind == "object":
        return SourceQualifiedVersionRef(source, ref).to_dict()
    return SourceQualifiedResourceRef(source, ResourceVersionRef(ref.entity_id, ref.version_id)).to_dict()


def test_binding_is_explicit_immutable_replayable_and_read_only_on_reopen(fixture):
    core, gateway, owner, bootstrap = fixture
    assert get_local_source_identity(core) is None
    result = gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    counts = _counts(core)
    assert gateway.bind_source_identity(source_id="source-a", command_id="bind:first") == result
    assert _counts(core) == counts
    assert result.task_ref == owner.task_ref and result.native_run_ref == owner.run_ref
    assert result.bootstrap_command_ref == bootstrap
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert get_local_source_identity(reader) == result
    assert _counts(core) == counts
    assert core.branch_id == "main"
    assert not core.event_store.list_events_by_type(("net_adopted/v1",))
    with pytest.raises(TypeError, match="owner's exact Registry"):
        RegistryRegistrationGateway(reader, owner.task_ref, bootstrap)


@pytest.mark.parametrize("source,command", [("source-b", "bind:first"),
                                            ("source-b", "bind:second"),
                                            ("source-a", "bind:second")])
def test_binding_cannot_change_source_or_rebind_under_another_command(fixture, source, command):
    core, gateway, _, _ = fixture
    original = gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    before = _counts(core)
    with pytest.raises(RegistryConflict):
        gateway.bind_source_identity(source_id=source, command_id=command)
    assert get_local_source_identity(core) == original
    assert _counts(core) == before


@pytest.mark.parametrize("source", ["", " source-a", "source-a\n", None])
def test_binding_rejects_noncanonical_source_data(fixture, source):
    core, gateway, _, _ = fixture
    before = _counts(core)
    with pytest.raises(SchemaGovernanceError):
        gateway.bind_source_identity(source_id=source, command_id="bind:first")
    assert get_local_source_identity(core) is None
    assert _counts(core) == before


@pytest.mark.parametrize("same_command", [False, True])
def test_concurrent_binding_has_one_commit_and_correct_replay(fixture, same_command):
    core, gateway, _, _ = fixture
    barrier = Barrier(2)

    def submit(index):
        barrier.wait()
        try:
            return gateway.bind_source_identity(source_id="source-a" if same_command else f"source-{index}",
                                                command_id="same" if same_command else f"bind:{index}")
        except RegistryConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, (0, 1)))
    successes = [value for value in outcomes if not isinstance(value, Exception)]
    assert len(successes) == (2 if same_command else 1)
    assert all(value == successes[0] for value in successes)
    rows = [row for row in core.event_store.object_rows()
            if row["object_type"] == source_identity.SOURCE_BINDING_TYPE]
    assert len(rows) == 1
    assert get_local_source_identity(core) == successes[0]


def test_stale_owner_writer_cannot_bind_but_current_gateway_can(fixture):
    core, gateway, owner, bootstrap = fixture
    current = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    with pytest.raises(StaleWriterError):
        gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    active = RegistryRegistrationGateway(current, owner.task_ref, bootstrap)
    assert active.bind_source_identity(source_id="source-a", command_id="bind:first").source_id == "source-a"


@pytest.mark.parametrize("kind", ["object", "resource"])
@pytest.mark.parametrize("bound,source,allowed", [(False, "source-a", False), (False, "source-b", False),
                                                  (True, "source-a", False), (True, "source-b", True)])
def test_source_aware_commit_preserves_local_guard_and_separates_foreign_collision(fixture, kind, bound, source, allowed):
    core, gateway, _, _ = fixture
    if bound:
        gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    ref = _publish_probe(core, None)
    _mark_provisional(core, member_kind="object", identity=str(ref.version_id))
    before = _counts(core)
    document = _wrapper(ref, source, kind)
    if allowed:
        result = _publish_probe(core, document)
        assert core.get_version(result.version_id).metadata["reference"] == document
    else:
        with pytest.raises(RegistryConflict, match="provisional"):
            _publish_probe(core, document)
        assert _counts(core) == before


def test_bare_local_refs_remain_subject_to_visibility_after_binding(fixture):
    core, gateway, _, _ = fixture
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    ref = _publish_probe(core, None)
    _mark_provisional(core, member_kind="object", identity=str(ref.version_id))
    with pytest.raises(RegistryConflict, match="provisional"):
        _publish_probe(core, ref_payload(ref))


@pytest.mark.parametrize("kind", ["object", "resource"])
@pytest.mark.parametrize("mutation", ["extra", "unknown_version", "missing_source", "bad_source", "nested_extra"])
def test_malformed_or_unknown_wrappers_cannot_hide_local_dependencies(fixture, kind, mutation):
    core, gateway, _, _ = fixture
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    ref = _publish_probe(core, None)
    _mark_provisional(core, member_kind="object", identity=str(ref.version_id))
    document = _wrapper(ref, "source-b", kind)
    if mutation == "extra":
        document["extra"] = True
    elif mutation == "unknown_version":
        document["schema_version"] = document["schema_version"].removesuffix("v1") + "v2"
    elif mutation == "missing_source":
        del document["source_id"]
    elif mutation == "bad_source":
        document["source_id"] = "source-b\n"
    else:
        document["ref"]["extra"] = True
    with pytest.raises(RegistryConflict, match="provisional"):
        _publish_probe(core, document)


@pytest.mark.parametrize("member_kind", ["object", "event", "commit"])
def test_noncanonical_binding_is_never_source_authority(fixture, member_kind):
    core, gateway, _, _ = fixture
    binding = gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    identity = (str(binding.event_id) if member_kind == "event" else
                str(source_identity.source_binding_version_id(core.task_id, "bind:first")))
    if member_kind == "commit":
        identity = str(next(event.event_id for event in core.event_store.list_events_by_idempotency_key(
            source_identity.source_command_key("bind:first")) if event.event_type == "transaction_committed/v1"))
        member_kind = "event"
    _mark_provisional(core, member_kind=member_kind, identity=identity)
    with pytest.raises(RegistryCorruptError, match="canonical"):
        get_local_source_identity(core)
    with pytest.raises(RegistryCorruptError, match="canonical"):
        _publish_probe(core, _wrapper(VersionRef(PROBE_TYPE, new_id("resource"), new_id("resource_version")), "source-b", "object"))


def test_missing_binding_fact_with_existing_stream_is_corruption(fixture):
    core, gateway, _, _ = fixture
    result = gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    with core.event_store.connect() as db:
        db.execute("DELETE FROM events WHERE event_id=?", (str(result.event_id),))
    with pytest.raises(RegistryCorruptError, match="no canonical binding"):
        get_local_source_identity(core)


@pytest.mark.parametrize("damage", ["missing", "duplicate"])
def test_binding_requires_one_canonical_committed_terminal(fixture, damage):
    core, gateway, _, _ = fixture
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    terminal = next(event for event in core.event_store.list_events_by_idempotency_key(
        source_identity.source_command_key("bind:first")) if event.event_type == "transaction_committed/v1")
    with core.event_store.connect() as db:
        if damage == "missing":
            db.execute("DELETE FROM events WHERE event_id=?", (str(terminal.event_id),))
        else:
            core.event_store._insert_event(db, replace(terminal, event_id=new_id("event"),
                stream_id="duplicate-source-terminal", stream_sequence=1, aggregate_version=1, ordinal=None))
    with pytest.raises(RegistryCorruptError, match="canonical"):
        get_local_source_identity(core)


def test_binding_failure_rolls_back_registered_state_and_same_command_recovers(fixture, monkeypatch):
    core, gateway, _, _ = fixture
    before = _counts(core)
    insert = core.event_store._insert_event

    def fail_after_insert(db, event):
        insert(db, event)
        if event.payload.get("object_type") == source_identity.SOURCE_BINDING_TYPE:
            raise RuntimeError("injected source publication failure")

    monkeypatch.setattr(core.event_store, "_insert_event", fail_after_insert)
    with pytest.raises(RuntimeError, match="injected"):
        gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    assert _counts(core) == before
    assert get_local_source_identity(core) is None
    monkeypatch.setattr(core.event_store, "_insert_event", insert)
    assert gateway.bind_source_identity(source_id="source-a", command_id="bind:first").source_id == "source-a"


@pytest.mark.parametrize("mutation", ["task", "bootstrap", "self", "command", "extra_object", "extra_event"])
def test_direct_core_publication_cannot_bypass_binding_contract(fixture, mutation):
    core, _, owner, bootstrap = fixture
    command = "direct:bind"
    ref = VersionRef(source_identity.SOURCE_BINDING_TYPE, source_identity.source_binding_id(core.task_id),
                     source_identity.source_binding_version_id(core.task_id, command))
    body = {"schema_version": source_identity.SOURCE_BINDING_SCHEMA, "binding_ref": ref_payload(ref),
            "source_id": "source-a", "task_ref": ref_payload(owner.task_ref),
            "native_run_ref": ref_payload(owner.run_ref), "bootstrap_command_ref": ref_payload(bootstrap),
            "command_id": command}
    if mutation == "task":
        body["task_ref"]["version_id"] = str(new_id("task_version"))
    elif mutation == "bootstrap":
        body["bootstrap_command_ref"]["version_id"] = str(new_id("bootstrap_command_version"))
    elif mutation == "self":
        body["binding_ref"]["version_id"] = str(new_id("resource_version"))
    key = "wrong-key" if mutation == "command" else source_identity.source_command_key(command)
    tx = core.begin(idempotency_key=key)
    tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(body), metadata=body, media_type="application/json",
                schema_ref=source_identity.SOURCE_BINDING_SCHEMA)
    if mutation == "extra_object":
        tx.prewrite(object_type=PROBE_TYPE, logical_id=new_id("resource"), version_id=new_id("resource_version"),
                    payload=canonical_json({"reference": None}), metadata={"reference": None},
                    media_type="application/json", schema_ref=PROBE_SCHEMA)
    elif mutation == "extra_event":
        extra_id, extra_version = new_id("resource"), new_id("resource_version")
        tx.append(PendingEvent(
            event_type="object_version_published/v1", criticality="authoritative",
            stream_id=source_identity.source_stream(core.task_id), aggregate_id=str(extra_id),
            aggregate_type=PROBE_TYPE, idempotency_key=key, command_id=key,
            payload={"logical_id": str(extra_id), "version_id": str(extra_version), "object_type": PROBE_TYPE,
                     "size": 0, "media_type": "application/json", "schema_ref": PROBE_SCHEMA,
                     "storage_locator": f"registry-object:{extra_version}", "metadata": {"reference": None}},
            payload_schema_ref="registry_v1/object_version_published/v1"))
    before = _counts(core)
    with pytest.raises(RegistryConflict):
        tx.commit()
    assert _counts(core) == before
    assert get_local_source_identity(core) is None


@pytest.mark.parametrize("bound", [False, True])
def test_other_types_cannot_claim_reserved_binding_identity_before_or_after_binding(fixture, bound):
    core, gateway, _, _ = fixture
    if bound:
        gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    prior = get_local_source_identity(core)
    before = _counts(core)
    body = {"reference": None}
    with pytest.raises(RegistryConflict, match="source identity"):
        core.publish_bytes(object_type=PROBE_TYPE, logical_id=source_identity.source_binding_id(core.task_id),
                           version_id=new_id("resource_version"), payload=canonical_json(body), metadata=body,
                           media_type="application/json", schema_ref=PROBE_SCHEMA, idempotency_key="reserved:collision")
    assert _counts(core) == before
    assert get_local_source_identity(core) == prior


def _author_record(fixture, source):
    core, _, owner, _ = fixture
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id),
            "display_name": "Offline publisher"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
                       version_id=principal.version_id, payload=canonical_json(body), metadata=body,
                       media_type="application/json", schema_ref="registry_v1/principal/v1",
                       idempotency_key=f"fixture:principal:{principal.version_id}")
    resource = lambda: SourceQualifiedResourceRef(source, ResourceVersionRef(new_id("resource"), new_id("resource_version")))
    return NetRevision(
        revision_ref=SourceQualifiedVersionRef(source, VersionRef(source_identity.AUTHOR_REVISION_TYPE,
                                                                 new_id("resource"), new_id("resource_version"))),
        owner_task_ref=SourceQualifiedVersionRef(source, owner.task_ref),
        producer_principal_ref=SourceQualifiedVersionRef(source, principal), command_id="author:fixture",
        definition_kind="closed_module", definition_ref=resource(), parent_revision_refs=(), selected_change_refs=(),
        element_mapping_ref=resource(), boundary_mapping_ref=resource(), host_requirements_ref=resource(),
        open_region_contract_ref=None,
    )


def _publish_author(core, record):
    ref = record.revision_ref.ref
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
                       payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
                       schema_ref=f"registry_v1/{ref.entity_type}", idempotency_key=f"author:{ref.version_id}")


@pytest.mark.parametrize("source,allowed", [("source-a", True), ("source-b", False)])
def test_binding_does_not_reinterpret_existing_author_identity(fixture, source, allowed):
    core, gateway, _, _ = fixture
    record = _author_record(fixture, "source-a")
    _publish_author(core, record)
    if allowed:
        gateway.bind_source_identity(source_id=source, command_id="bind:first")
        assert read_net_revision(core, record.revision_ref, local_source_id=source) == record
    else:
        before = _counts(core)
        with pytest.raises(RegistryConflict, match="migration"):
            gateway.bind_source_identity(source_id=source, command_id="bind:first")
        assert get_local_source_identity(core) is None
        assert _counts(core) == before


def test_bound_owner_rejects_foreign_author_self_identity_and_reader_alias(fixture):
    core, gateway, _, _ = fixture
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    foreign = _author_record(fixture, "source-b")
    with pytest.raises(RegistryConflict, match="self source"):
        _publish_author(core, foreign)
    local = _author_record(fixture, "source-a")
    _publish_author(core, local)
    with pytest.raises(AuthorRevisionReadError, match="canonical Registry binding"):
        read_net_revision(core, SourceQualifiedVersionRef("source-b", local.revision_ref.ref),
                          local_source_id="source-b")


def test_registered_binding_cannot_be_interpreted_by_an_unconfigured_catalog(fixture):
    core, gateway, _, _ = fixture
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    old_reader = _RegistryCore(core.run_dir, create=False, read_only=True)
    with pytest.raises(SchemaGovernanceError, match="unregistered"):
        get_local_source_identity(old_reader)


def test_binding_validator_uses_the_actual_commit_connection_before_sql_publication(fixture, monkeypatch):
    core, gateway, _, _ = fixture
    original = source_identity.validate_source_binding
    observed = []

    def validate(context):
        assert context.db.in_transaction
        before = context.db.execute("SELECT COUNT(*) FROM objects WHERE object_type=?",
                                    (source_identity.SOURCE_BINDING_TYPE,)).fetchone()[0]
        assert before == 0
        observed.append(context.db)
        return original(context)

    monkeypatch.setattr(source_identity, "validate_source_binding", validate)
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    assert len(observed) == 1


@pytest.mark.parametrize("authority", ["task", "run", "bootstrap"])
@pytest.mark.parametrize("damage", ["publication", "commit", "provisional", "provisional_commit"])
def test_owner_authority_requires_canonical_publication_and_commit_closure(fixture, authority, damage):
    core, gateway, owner, bootstrap = fixture
    ref = {"task": owner.task_ref, "run": owner.run_ref, "bootstrap": bootstrap}[authority]
    with core.event_store.connect() as db:
        row = db.execute("SELECT published_event_id,transaction_id FROM objects WHERE version_id=?",
                         (str(ref.version_id),)).fetchone()
        terminal = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                              (row["transaction_id"],)).fetchone()[0]
        if damage == "publication":
            db.execute("DELETE FROM events WHERE event_id=?", (row["published_event_id"],))
        elif damage == "commit":
            db.execute("DELETE FROM events WHERE event_id=?", (terminal,))
    if damage == "provisional":
        _mark_provisional(core, member_kind="object", identity=str(ref.version_id))
    elif damage == "provisional_commit":
        _mark_provisional(core, member_kind="event", identity=terminal)
    with pytest.raises(RegistryConflict, match="canonical|provisional"):
        gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    assert get_local_source_identity(core) is None


def test_read_snapshot_does_not_mix_first_binding_with_a_new_stream_head(fixture, monkeypatch):
    core, gateway, _, _ = fixture
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    connect = reader.event_store.connect
    injected = False

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            nonlocal injected
            rows = self.cursor.fetchall()
            if not injected:
                injected = True
                gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
            return rows

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class Connection:
        def __init__(self):
            self.db = connect()

        def __enter__(self):
            self.db.__enter__()
            return self

        def __exit__(self, *args):
            return self.db.__exit__(*args)

        def execute(self, sql, *args):
            cursor = self.db.execute(sql, *args)
            if sql.startswith("SELECT e.*, o.logical_id AS binding_logical_id"):
                assert self.db.in_transaction
                return Cursor(cursor)
            return cursor

    monkeypatch.setattr(reader.event_store, "connect", Connection)
    assert get_local_source_identity(reader) is None
    assert injected
    assert get_local_source_identity(reader).source_id == "source-a"
