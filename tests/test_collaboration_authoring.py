"""Socket-free tests of the opt-in immutable NetRevision record boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from cpn.rpnh.collaboration import (
    AuthorRevisionReadError,
    NetRevision,
    SourceQualifiedResourceRef,
    SourceQualifiedVersionRef,
    authoring_schema_data,
    collaboration_schema_data,
    read_net_revision,
)
from cpn.rpnh.collaboration.authoring import NET_REVISION_SCHEMA, NET_REVISION_TYPE
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import (
    CURRENT_OBJECT_TYPES,
    SchemaCatalog,
    SchemaGovernanceError,
    canonical_json,
)


def _obj(kind, logical, version, source="source-a"):
    return SourceQualifiedVersionRef(source, VersionRef(kind, new_id(logical), new_id(version)))


def _revision(source="source-a"):
    return _obj(NET_REVISION_TYPE, "resource", "resource_version", source)


def _resource(source="source-a"):
    return SourceQualifiedResourceRef(source, ResourceVersionRef(new_id("resource"), new_id("resource_version")))


@pytest.fixture
def catalog():
    schemas, types, paths = authoring_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


@pytest.fixture
def record():
    return NetRevision(
        revision_ref=_revision(),
        owner_task_ref=_obj("task/v1", "task", "task_version"),
        producer_principal_ref=_obj("principal/v1", "principal", "principal_version"),
        command_id="author:publish:r1",
        definition_kind="closed_module", definition_ref=_resource(),
        parent_revision_refs=(_revision(), _revision("source-b")),
        selected_change_refs=(_resource("source-c"),),
        element_mapping_ref=_resource(), boundary_mapping_ref=_resource(),
        host_requirements_ref=_resource(), open_region_contract_ref=None,
    )


@pytest.mark.parametrize("kind", ["closed_module", "open_region"])
def test_definition_kind_roundtrip_keeps_all_exact_provenance(catalog, record, kind):
    value = replace(record, definition_kind=kind,
                    open_region_contract_ref=_resource() if kind == "open_region" else None)
    assert NetRevision.from_dict(value.to_dict(), catalog=catalog) == value
    assert value.to_dict()["parent_revision_refs"] != value.to_dict()["selected_change_refs"]
    assert value.owner_task_ref == record.owner_task_ref
    assert value.producer_principal_ref == record.producer_principal_ref
    assert value.command_id == record.command_id


def test_root_revision_has_no_implied_parent_or_selected_changes(catalog, record):
    root = replace(record, parent_revision_refs=(), selected_change_refs=())
    assert NetRevision.from_dict(root.to_dict(), catalog=catalog) == root


def test_record_is_deeply_immutable_and_serialization_is_detached(record):
    with pytest.raises(FrozenInstanceError):
        record.command_id = "changed"
    value = record.to_dict()
    value["parent_revision_refs"][0]["ref"].clear()
    value["selected_change_refs"].clear()
    assert len(record.parent_revision_refs) == 2
    assert len(record.selected_change_refs) == 1
    assert record.to_dict()["parent_revision_refs"][0]["ref"]


@pytest.mark.parametrize("changes,error", [
    ({"parent_revision_refs": []}, TypeError),
    ({"selected_change_refs": []}, TypeError),
    ({"command_id": ""}, ValueError),
    ({"command_id": "cmd\n"}, ValueError),
    ({"definition_kind": "compiled_net"}, ValueError),
    ({"definition_kind": "open_region"}, TypeError),
    ({"definition_ref": None}, TypeError),
    ({"element_mapping_ref": None}, TypeError),
    ({"boundary_mapping_ref": None}, TypeError),
    ({"host_requirements_ref": None}, TypeError),
])
def test_python_construction_rejects_incomplete_or_mutable_records(record, changes, error):
    with pytest.raises(error):
        replace(record, **changes)


def test_parents_do_not_merge_sources_or_admit_self_and_duplicates(record):
    parent = record.parent_revision_refs[0]
    other_source = SourceQualifiedVersionRef("source-b", parent.ref)
    assert replace(record, parent_revision_refs=(parent, other_source))
    with pytest.raises(ValueError, match="unique"):
        replace(record, parent_revision_refs=(parent, parent))
    with pytest.raises(ValueError, match="exclude"):
        replace(record, parent_revision_refs=(record.revision_ref,))
    with pytest.raises(ValueError, match="unique"):
        replace(record, selected_change_refs=record.selected_change_refs * 2)


@pytest.mark.parametrize("field", ["owner_task_ref", "producer_principal_ref"])
def test_publication_owner_and_writer_cannot_silently_change_sources(record, field):
    original = getattr(record, field)
    with pytest.raises(ValueError, match="publication source"):
        replace(record, **{field: SourceQualifiedVersionRef("source-other", original.ref)})


@pytest.mark.parametrize("field", ["revision_ref", "owner_task_ref", "producer_principal_ref"])
def test_author_refs_require_the_declared_entity_and_id_kinds(record, field):
    with pytest.raises(TypeError, match="source-qualified exact"):
        replace(record, **{field: _obj("task/v1", "resource", "resource_version")})


def test_closed_definition_cannot_keep_an_unresolved_open_contract(catalog, record):
    document = record.to_dict()
    document["open_region_contract_ref"] = _resource().to_dict()
    with pytest.raises(SchemaGovernanceError):
        NetRevision.from_dict(document, catalog=catalog)
    with pytest.raises(ValueError):
        replace(record, open_region_contract_ref=_resource())


@pytest.mark.parametrize("mutation", ["missing", "extra", "unknown_version", "newline", "wrong_ref_type"])
def test_wire_contract_fails_closed(catalog, record, mutation):
    document = record.to_dict()
    if mutation == "missing":
        del document["host_requirements_ref"]
    elif mutation == "extra":
        document["adopted"] = True
    elif mutation == "unknown_version":
        document["schema_version"] = NET_REVISION_SCHEMA.removesuffix("v1") + "v2"
    elif mutation == "newline":
        document["command_id"] += "\n"
    else:
        document["revision_ref"]["ref"]["entity_type"] = "task/v1"
    with pytest.raises(SchemaGovernanceError):
        NetRevision.from_dict(document, catalog=catalog)


def test_author_inventory_is_optional_and_does_not_expand_reference_only_inventory(record):
    refs, ref_types, _ = collaboration_schema_data()
    docs, types, paths = authoring_schema_data()
    assert len(refs) == 2 and ref_types == ()
    assert set(docs) == {*refs, NET_REVISION_SCHEMA}
    assert NET_REVISION_TYPE not in CURRENT_OBJECT_TYPES
    assert len(types) == 1 and types[0].name == NET_REVISION_TYPE
    catalog = SchemaCatalog(schemas=docs, types=types, schema_paths=paths)
    with pytest.raises(SchemaGovernanceError, match="unregistered"):
        NetRevision.from_dict(record.to_dict(), catalog=SchemaCatalog())
    assert NetRevision.from_dict(record.to_dict(), catalog=catalog) == record
    catalog.register_type(types[0])
    assert json.loads(catalog.bundle()["schemas"][NET_REVISION_SCHEMA]["source"]) == docs[NET_REVISION_SCHEMA]
    docs[NET_REVISION_SCHEMA]["properties"].clear()
    assert authoring_schema_data()[0][NET_REVISION_SCHEMA]["properties"]


@pytest.fixture
def stored(tmp_path, catalog, record):
    core = _RegistryCore(tmp_path / "author", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest((NET_REVISION_SCHEMA,)))
    principal = record.producer_principal_ref.ref
    body = {"principal_id": str(principal.entity_id),
            "principal_version_id": str(principal.version_id), "display_name": "Offline author"}
    core.publish_bytes(object_type="principal/v1", logical_id=principal.entity_id,
                       version_id=principal.version_id, payload=canonical_json(body), metadata=body,
                       media_type="application/json", schema_ref="registry_v1/principal/v1",
                       idempotency_key="fixture:author-principal")
    record = replace(record, owner_task_ref=SourceQualifiedVersionRef("source-a", owner.task_ref))

    def publish(value, *, metadata=None, payload=None, storage_ref=None):
        body = value.to_dict() if metadata is None else metadata
        ref = storage_ref or value.revision_ref.ref
        core.publish_bytes(object_type=NET_REVISION_TYPE, logical_id=ref.entity_id,
                           version_id=ref.version_id,
                           payload=canonical_json(body) if payload is None else payload,
                           metadata=body, media_type="application/json", schema_ref=NET_REVISION_SCHEMA,
                           idempotency_key=f"fixture:author:{ref.version_id}")
        return value

    return core, record, publish


def test_real_optional_object_persistence_and_read_only_reopen(stored):
    core, record, publish = stored
    publish(record)
    before = len(core.event_store.list_events())
    assert read_net_revision(core, record.revision_ref, local_source_id="source-a") == record
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_net_revision(reader, record.revision_ref, local_source_id="source-a") == record
    assert len(core.event_store.list_events()) == before
    assert core.event_store.list_events_by_type(("net_adopted/v1",)) == ()
    assert core.branch_id == "main"
    legacy = _RegistryCore(core.run_dir, create=False, read_only=True)
    with pytest.raises(SchemaGovernanceError, match="unregistered"):
        read_net_revision(legacy, record.revision_ref, local_source_id="source-a")


def test_reader_rejects_foreign_source_and_wrong_logical_identity(stored):
    core, record, publish = stored
    publish(record)
    with pytest.raises(AuthorRevisionReadError, match="source"):
        read_net_revision(core, record.revision_ref, local_source_id="source-b")
    wrong = SourceQualifiedVersionRef("source-a", replace(record.revision_ref.ref, entity_id=new_id("resource")))
    with pytest.raises(AuthorRevisionReadError, match="canonical publication/commit"):
        read_net_revision(core, wrong, local_source_id="source-a")


def test_reader_rejects_payload_metadata_disagreement(stored):
    core, record, publish = stored
    payload = record.to_dict()
    payload["command_id"] = "author:different-command"
    publish(record, payload=canonical_json(payload))
    with pytest.raises(AuthorRevisionReadError, match="bytes differ from registered metadata"):
        read_net_revision(core, record.revision_ref, local_source_id="source-a")


def test_reader_rejects_record_self_reference_mismatch(stored):
    core, record, publish = stored
    stored_ref = _revision()
    publish(record, storage_ref=stored_ref.ref)
    with pytest.raises(AuthorRevisionReadError, match="self-reference"):
        read_net_revision(core, stored_ref, local_source_id="source-a")


def test_reader_rejects_unregistered_or_wrong_owner_producer(stored):
    core, record, publish = stored
    other_task = replace(record, owner_task_ref=_obj("task/v1", "task", "task_version"))
    publish(other_task)
    with pytest.raises(AuthorRevisionReadError, match="different owner task"):
        read_net_revision(core, other_task.revision_ref, local_source_id="source-a")


@pytest.mark.parametrize("field,kind", [("owner_task_ref", "task_version"),
                                        ("producer_principal_ref", "principal_version")])
def test_reader_rejects_unregistered_owner_or_producer_version(stored, field, kind):
    core, record, publish = stored
    current = getattr(record, field)
    missing = SourceQualifiedVersionRef(current.source_id, replace(current.ref, version_id=new_id(kind)))
    record = replace(record, **{field: missing})
    publish(record)
    with pytest.raises(AuthorRevisionReadError, match="canonical publication/commit"):
        read_net_revision(core, record.revision_ref, local_source_id="source-a")


@pytest.mark.parametrize("authority", ["task", "principal"])
@pytest.mark.parametrize("mismatch", ["logical_id", "version_id", "payload"])
def test_reader_rejects_authority_self_identity_and_payload_mismatch(stored, authority, mismatch):
    core, record, publish = stored
    field = "owner_task_ref" if authority == "task" else "producer_principal_ref"
    old_ref = getattr(record, field).ref
    ref = VersionRef(old_ref.entity_type, old_ref.entity_id, new_id(f"{authority}_version"))
    body = {f"{authority}_id": str(ref.entity_id), f"{authority}_version_id": str(ref.version_id)}
    if authority == "principal":
        body["display_name"] = "Offline author"
    payload = dict(body)
    if mismatch == "logical_id":
        body[f"{authority}_id"] = str(new_id(authority))
        payload = body
    elif mismatch == "version_id":
        body[f"{authority}_version_id"] = str(new_id(f"{authority}_version"))
        payload = body
    else:
        payload[f"{authority}_version_id"] = str(new_id(f"{authority}_version"))
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id,
                       version_id=ref.version_id, payload=canonical_json(payload), metadata=body,
                       media_type="application/json", schema_ref=f"registry_v1/{ref.entity_type}",
                       idempotency_key=f"fixture:authority:{ref.version_id}")
    record = replace(record, **{field: SourceQualifiedVersionRef("source-a", ref)})
    publish(record)
    expected = "bytes differ" if mismatch == "payload" else "self-identity"
    with pytest.raises(AuthorRevisionReadError, match=expected):
        read_net_revision(core, record.revision_ref, local_source_id="source-a")


def test_record_schema_cannot_be_used_without_its_type_registration(tmp_path, record):
    documents, _, paths = authoring_schema_data()
    catalog = SchemaCatalog(schemas=documents, schema_paths=paths)
    core = _RegistryCore(tmp_path / "unregistered", create=True, catalog=catalog)
    ref = record.revision_ref.ref
    with pytest.raises(SchemaGovernanceError, match="unregistered"):
        core.publish_bytes(object_type=NET_REVISION_TYPE, logical_id=ref.entity_id,
                           version_id=ref.version_id, payload=canonical_json(record.to_dict()),
                           metadata=record.to_dict(), media_type="application/json",
                           schema_ref=NET_REVISION_SCHEMA, idempotency_key="must-reject")


def _inject_temporary_member(core, kind, identity):
    # Read-side fault/compatibility fixture only, not a complete firing run.
    root = str(new_id("transition_firing_version"))
    with core.event_store.connect() as db:
        tx = db.execute("SELECT transaction_id FROM transactions WHERE status='committed' LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO firing_publications(firing_version_id,firing_logical_id,invocation_version_id,"
            "invocation_logical_id,net_version_id,operation_binding_version_id,admission_checkpoint_version_id,"
            "state,opened_transaction_id) VALUES(?,?,?,?,?,?,?,'PROVISIONAL',?)",
            (root, str(new_id("transition_firing")), str(new_id("invocation_version")), str(new_id("invocation")),
             str(new_id("net_instance_version")), str(new_id("operation_binding_version")),
             str(new_id("marking_checkpoint_version")), tx))
        db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)", (root, kind, identity, tx))
    return root


@pytest.mark.parametrize("field", ["revision_ref", "owner_task_ref", "producer_principal_ref"])
@pytest.mark.parametrize("damage", ["missing_commit", "duplicate_commit", "observational_commit", "provisional_commit"])
def test_public_reader_requires_unique_canonical_commit_closure(stored, field, damage):
    core, record, publish = stored
    publish(record)
    ref = getattr(record, field).ref
    with core.event_store.connect() as db:
        terminal = dict(db.execute("SELECT * FROM events WHERE event_type='transaction_committed/v1' AND transaction_id="
            "(SELECT transaction_id FROM objects WHERE version_id=?)", (str(ref.version_id),)).fetchone())
        if damage == "missing_commit":
            db.execute("DELETE FROM events WHERE event_id=?", (terminal["event_id"],))
        elif damage == "duplicate_commit":
            terminal.pop("ordinal")
            terminal.update(event_id=str(new_id("event")), stream_id="fixture:duplicate-terminal", stream_sequence=1)
            db.execute(f"INSERT INTO events({','.join(terminal)}) VALUES({','.join('?' for _ in terminal)})", tuple(terminal.values()))
        elif damage == "observational_commit":
            db.execute("UPDATE events SET criticality='observational' WHERE event_id=?", (terminal["event_id"],))
    if damage == "provisional_commit":
        _inject_temporary_member(core, "event", terminal["event_id"])
    before = len(core.event_store.list_events())
    with pytest.raises(AuthorRevisionReadError, match="canonical publication/commit"):
        read_net_revision(core, record.revision_ref, local_source_id="source-a")
    assert len(core.event_store.list_events()) == before


@pytest.mark.parametrize("field", ["revision_ref", "producer_principal_ref"])
@pytest.mark.parametrize("state", ["provisional", "published", "missing_promotion_commit"])
def test_public_reader_respects_published_member_promotion(stored, field, state):
    core, record, publish = stored
    publish(record)
    root = _inject_temporary_member(core, "object", str(getattr(record, field).ref.version_id))
    if state != "provisional":
        tx = core.begin(idempotency_key="fixture:reader-promotion")
        terminal = tx.commit()[-1]
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(tx.transaction_id), str(new_id("operation_result_version")),
                 str(new_id("marking_checkpoint_version")), root))
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
                (root, "event", str(terminal.event_id), str(tx.transaction_id)))
            if state == "missing_promotion_commit":
                db.execute("DELETE FROM events WHERE event_id=?", (str(terminal.event_id),))
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = len(core.event_store.list_events())
    if state == "published":
        assert read_net_revision(reader, record.revision_ref, local_source_id="source-a") == record
    else:
        with pytest.raises(AuthorRevisionReadError, match="canonical publication/commit"):
            read_net_revision(reader, record.revision_ref, local_source_id="source-a")
    assert len(core.event_store.list_events()) == before


def test_public_reader_keeps_all_descriptor_checks_in_one_snapshot(stored, monkeypatch):
    from cpn.rpnh.collaboration import authoring
    core, record, publish = stored
    publish(record)
    original = authoring.exact_descriptor
    connections = []

    def check(db, *args):
        assert db.in_transaction
        connections.append(db)
        return original(db, *args)

    monkeypatch.setattr(authoring, "exact_descriptor", check)
    assert read_net_revision(core, record.revision_ref, local_source_id="source-a") == record
    assert len(connections) == 3 and all(db is connections[0] for db in connections)


def test_strict_descriptor_preserves_matching_publication_producer_identity(stored):
    # A projection-level envelope compatibility check, not a real firing run.
    core, record, publish = stored
    publish(record)
    invocation = str(new_id("invocation"))
    with core.event_store.connect() as db:
        version = str(record.producer_principal_ref.ref.version_id)
        db.execute("UPDATE objects SET producer_invocation_id=? WHERE version_id=?", (invocation, version))
        db.execute("UPDATE events SET producer_invocation_id=? WHERE event_id="
            "(SELECT published_event_id FROM objects WHERE version_id=?)", (invocation, version))
    assert read_net_revision(core, record.revision_ref, local_source_id="source-a") == record
