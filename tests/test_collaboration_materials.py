"""Real offline closed-module author producer and strict material consumer."""

from copy import deepcopy
from dataclasses import replace
import json
import socket
import subprocess
import uuid

import pytest

from cpn.rpnh.collaboration import (
    ClosedModuleAuthor, SourceQualifiedVersionRef, author_material_schema_data,
    current_branch, read_net_revision, validate_closed_revision,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from test_native_net_operations import _registration, _simple_module


@pytest.fixture
def fixture(tmp_path, request):
    schemas, types, paths = author_material_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(tmp_path / "author-material", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("closed-author-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="bind:first")
    ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Test author"}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="fixture:principal")
    registration = _registration()
    if getattr(request, "param", None) == "numeric_host":
        registration = _with_host_revision(registration, 1)
    author = ClosedModuleAuthor(gateway, registration, SourceQualifiedVersionRef("source-a", ref))
    module = _simple_module()
    ids = {path: "element:" + uuid.uuid4().hex for path in (
        "/", "/terminal", "/components/step", "/components/step/ports/request", "/components/step/ports/result",
        "/components/step/operations/run", "/entry/request", "/exit/result")}
    return core, gateway, author, registration, module, ids


def test_real_closed_author_publishes_materials_then_revision_and_feeds_branch(fixture, monkeypatch):
    core, gateway, author, registration, module, ids = fixture
    def forbidden(*args, **kwargs):
        raise AssertionError("offline author publication must not open sockets or spawn processes")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert first.module.to_dict() == module.to_dict()
    assert first.host_requirements["registrations"] == first.compiled.to_dict()["registrations"]
    refs = (first.revision.revision_ref.ref.version_id, *[getattr(first.revision, field).ref.resource_version_id
        for field in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")])
    with core.event_store.connect() as db:
        txs = {db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (str(ref),)).fetchone()[0] for ref in refs}
    assert len(txs) == 5  # Immutable preparation materials precede the revision success point.
    branch = gateway.create_author_branch(head_revision_ref=first.revision.revision_ref, command_id="branch:create")
    document = module.to_dict()
    document["name"] = "Renamed"
    document["components"][0]["name"] = "renamed"
    document["entry"]["request"]["component"] = "renamed"
    document["exit"]["result"]["component"] = "renamed"
    document["terminal"]["source"]["component"] = "renamed"
    renamed = ModuleDeclaration.from_dict(document)
    changed_ids = {path.replace("/components/step", "/components/renamed"): value for path, value in ids.items()}
    second = author.publish(module=renamed, element_ids=changed_ids, command_id="author:r1", parent_ref=first.revision.revision_ref)
    assert {row["element_id"] for row in second.element_map["elements"]} == set(ids.values())
    advanced = gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=first.revision.revision_ref, expected_stream_head=1,
        next_revision_ref=second.revision.revision_ref, command_id="branch:advance")
    assert current_branch(core, branch.branch_ref.ref.entity_id) == advanced
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert validate_closed_revision(reader, first.revision.revision_ref, registration).module.to_dict() == module.to_dict()
    assert validate_closed_revision(reader, second.revision.revision_ref, registration).module.to_dict() == renamed.to_dict()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1")) == ()
    assert core.branch_id == "main"


def _counts(core):
    return (len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")),
            len(core.event_store.object_rows_by_type("collaboration_branch/v1")),
            len(core.event_store.list_events_by_type(("net_adopted/v1",))))


def _publish_raw_revision(core, record, **changes):
    ref = SourceQualifiedVersionRef("source-a", VersionRef("collaboration_net_revision/v1",
        new_id("resource"), new_id("resource_version")))
    value = replace(record, revision_ref=ref, **changes)
    core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(value.to_dict()), metadata=value.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key=f"raw:{ref.ref.version_id}")
    return value


def _publish_material(core, author, document, schema):
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    ref = _publish_private_system(core, author.gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(author.gateway._bootstrap_ref), payload=canonical_json(document),
        media_type="application/json", content_schema_ref=schema,
        content_schema_authority_ref=author.schemas.get(schema), summary="Test mutated author material",
        lifetime_ref=author.gateway._bootstrap_ref, idempotency_key=f"mutation:{uuid.uuid4().hex}"))
    return SourceQualifiedResourceRef("source-a", ref)


def test_replay_after_later_revision_and_reopen_preserves_exact_materials(fixture):
    core, gateway, author, registration, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    changed = module.to_dict()
    changed["name"] = "Second"
    author.publish(module=ModuleDeclaration.from_dict(changed), element_ids=ids, command_id="author:r1", parent_ref=first.revision.revision_ref)
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    assert author.publish(module=module, element_ids=ids, command_id="author:r0").revision == first.revision
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_author = ClosedModuleAuthor(next_gateway, _registration(), author.producer)
    assert next_author.publish(module=module, element_ids=ids, command_id="author:r0").revision == first.revision
    assert (len(reopened.event_store.object_rows()), len(reopened.event_store.list_events())) == before


@pytest.mark.parametrize("change", ["module", "ids", "parent", "other_parent"])
def test_same_author_command_with_different_material_conflicts(fixture, change):
    core, _, author, _, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    next_module, next_ids, parent = module, dict(ids), None
    if change == "module":
        document = module.to_dict()
        document["name"] = "Different"
        next_module = ModuleDeclaration.from_dict(document)
    elif change == "ids":
        next_ids["/"] = "element:" + uuid.uuid4().hex
    elif change == "parent":
        parent = first.revision.revision_ref
    else:
        parent = author.publish(module=module, element_ids=ids, command_id="author:other-parent").revision.revision_ref
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(module=next_module, element_ids=next_ids, command_id="author:r0", parent_ref=parent)
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "bad_id", "bad_copy"])
def test_invalid_element_input_cannot_publish_a_successful_revision(fixture, damage):
    core, _, author, _, module, ids = fixture
    changed, copies = dict(ids), {}
    if damage == "missing":
        changed.pop("/")
    elif damage == "extra":
        changed["/unknown"] = "element:" + uuid.uuid4().hex
    elif damage == "duplicate":
        changed["/"] = changed["/terminal"]
    elif damage == "bad_id":
        changed["/"] += "\n"
    else:
        copies[changed["/"]] = changed["/terminal"]
    before = _counts(core)
    with pytest.raises(ValueError):
        author.publish(module=module, element_ids=changed, copy_sources=copies, command_id="author:bad")
    assert _counts(core) == before


def test_copy_uses_new_stable_ids_and_records_exact_parent_sources(fixture):
    core, _, author, registration, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    fresh = {path: "element:" + uuid.uuid4().hex for path in ids}
    copies = {fresh[path]: ids[path] for path in ids}
    second = author.publish(module=module, element_ids=fresh, command_id="author:copy",
        parent_ref=first.revision.revision_ref, copy_sources=copies)
    assert set(ids.values()).isdisjoint(row["element_id"] for row in second.element_map["elements"])
    for row in second.element_map["elements"]:
        assert row["copied_from"] == {"revision_ref": first.revision.revision_ref.to_dict(), "element_id": ids[row["locator"]]}
    assert validate_closed_revision(core, second.revision.revision_ref, registration).element_map == second.element_map


@pytest.mark.parametrize("damage", ["element_coverage", "element_duplicate", "boundary", "host_contract", "host_refs", "wrong_schema"])
def test_strict_material_consumer_rejects_schema_valid_but_wrong_material(fixture, damage):
    from cpn.rpnh.collaboration.materials import ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA
    core, _, author, registration, module, ids = fixture
    valid = author.publish(module=module, element_ids=ids, command_id="author:r0")
    if damage.startswith("element"):
        field, schema, document = "element_mapping_ref", ELEMENT_SCHEMA, deepcopy(valid.element_map)
        if damage == "element_coverage":
            document["elements"].pop()
        else:
            document["elements"].append(deepcopy(document["elements"][0]))
    elif damage == "boundary":
        field, schema, document = "boundary_mapping_ref", BOUNDARY_SCHEMA, deepcopy(valid.boundary_map)
        document["entries"] = []
    else:
        field, schema, document = "host_requirements_ref", HOST_SCHEMA, deepcopy(valid.host_requirements)
        if damage == "host_contract":
            document["registrations"]["executor"].clear()
        elif damage == "host_refs":
            document["declaration_refs"].pop()
        else:
            schema = None
    resource = _publish_material(core, author, document, schema)
    record = _publish_raw_revision(core, valid.revision, **{field: resource})
    assert read_net_revision(core, record.revision_ref, local_source_id="source-a") == record
    before = _counts(core)
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        validate_closed_revision(core, record.revision_ref, registration)
    assert _counts(core) == before


@pytest.mark.parametrize("damage", ["foreign", "missing", "open", "selected", "multiple_parents"])
def test_strict_consumer_keeps_unsupported_semantics_explicit(fixture, damage):
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    core, _, author, registration, module, ids = fixture
    valid = author.publish(module=module, element_ids=ids, command_id="author:r0")
    changes = {}
    if damage == "foreign":
        changes["definition_ref"] = SourceQualifiedResourceRef("remote", valid.revision.definition_ref.ref)
    elif damage == "missing":
        changes["definition_ref"] = SourceQualifiedResourceRef("source-a", ResourceVersionRef(new_id("resource"), new_id("resource_version")))
    elif damage == "open":
        changes = {"definition_kind": "open_region", "open_region_contract_ref": valid.revision.boundary_mapping_ref}
    elif damage == "selected":
        changes["selected_change_refs"] = (valid.revision.definition_ref,)
    else:
        other = author.publish(module=module, element_ids={path: "element:" + uuid.uuid4().hex for path in ids}, command_id="author:other")
        changes["parent_revision_refs"] = (valid.revision.revision_ref, other.revision.revision_ref)
    record = _publish_raw_revision(core, valid.revision, **changes)
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        validate_closed_revision(core, record.revision_ref, registration)


@pytest.mark.parametrize("damage", ["payload", "missing_commit", "schema_commit", "producer_relation", "observational_relation"])
def test_material_reader_rechecks_exact_bytes_and_canonical_evidence(fixture, damage):
    core, _, author, registration, module, ids = fixture
    valid = author.publish(module=module, element_ids=ids, command_id="author:r0")
    ref = valid.revision.element_mapping_ref.ref
    if damage == "payload":
        path = core.object_store.path_for_version(ref.resource_version_id)
        path.write_bytes(path.read_bytes().replace(b'element:', b'element;'))
    else:
        with core.event_store.connect() as db:
            if damage == "schema_commit":
                target = author.schemas["rpnh/collaboration/author_element_map/v1"].resource_version_id
            else:
                target = ref.resource_version_id
            if damage == "observational_relation":
                db.execute("UPDATE events SET criticality='observational' WHERE event_id IN (SELECT published_event_id FROM relations "
                    "WHERE json_extract(source_json,'$.version_id')=?)", (str(target),))
            elif damage == "producer_relation":
                db.execute("DELETE FROM events WHERE event_id IN (SELECT published_event_id FROM relations "
                    "WHERE json_extract(source_json,'$.version_id')=?)", (str(target),))
            else:
                db.execute("DELETE FROM events WHERE event_type='transaction_committed/v1' AND transaction_id="
                    "(SELECT transaction_id FROM objects WHERE version_id=?)", (str(target),))
    before = _counts(core)
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        validate_closed_revision(core, valid.revision.revision_ref, registration)
    assert _counts(core) == before


def test_revision_commit_failure_preserves_prepared_materials_and_retries(fixture, monkeypatch):
    core, _, author, _, module, ids = fixture
    original = core.event_store._insert_event
    def fail(db, event):
        original(db, event)
        if event.payload.get("object_type") == "collaboration_net_revision/v1":
            raise RuntimeError("injected revision commit failure")
    monkeypatch.setattr(core.event_store, "_insert_event", fail)
    before = _counts(core)
    with pytest.raises(RuntimeError, match="injected"):
        author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert _counts(core) == before
    resources = len(core.event_store.object_rows_by_type("resource_version/v1"))
    monkeypatch.setattr(core.event_store, "_insert_event", original)
    result = author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert result.revision.command_id == "author:r0"
    assert len(core.event_store.object_rows_by_type("resource_version/v1")) == resources
    assert _counts(core) == (1, 0, 0)


def test_actual_compile_failure_precedes_material_or_revision_publication(fixture):
    core, _, author, _, module, ids = fixture
    document = module.to_dict()
    document["components"][0]["operations"][0]["executor"] = "not-registered"
    invalid = ModuleDeclaration.from_dict(document)
    before = (len(core.event_store.object_rows()), _counts(core))
    with pytest.raises(ValueError, match="unregistered"):
        author.publish(module=invalid, element_ids=ids, command_id="author:bad-compile")
    assert (len(core.event_store.object_rows()), _counts(core)) == before


def test_explicit_host_contract_mismatch_is_rejected_without_fallback(fixture):
    core, _, author, _, module, ids = fixture
    valid = author.publish(module=module, element_ids=ids, command_id="author:r0")
    registration = _registration()
    # A distinct HOST configuration with the same lookup key is not the
    # declared version pinned by this author revision. No executor is run.
    key = module.components[0].operations[0].executor
    registration._declarations["executor"][key]["identity"]["revision"] = "different-host"
    with pytest.raises(RegistryConflict, match="HOST requirements"):
        validate_closed_revision(core, valid.revision.revision_ref, registration)


def test_two_uses_of_same_definition_have_distinct_declared_element_ids(fixture):
    from cpn.rpnh.net_operations.definitions import compose_modules, ComposePlan
    from cpn.rpnh.collaboration.materials import _elements
    core, _, author, registration, module, _ = fixture
    composed = compose_modules({"left": module, "right": module}, ComposePlan("Pair", "right", mode="serial"))
    ids = {locator: "element:" + uuid.uuid4().hex for locator in _elements(composed)}
    result = author.publish(module=composed, element_ids=ids, command_id="author:pair")
    components = [row for row in result.element_map["elements"] if row["kind"] == "component"]
    assert len(components) == 2 and len({row["element_id"] for row in components}) == 2
    assert len([row for row in result.element_map["elements"] if row["kind"] == "link"]) == 1
    assert len(result.compiled.fragments) == 2
    assert validate_closed_revision(core, result.revision.revision_ref, registration).boundary_map == result.boundary_map


def test_same_command_concurrency_returns_one_exact_author_revision(fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core, _, author, _, module, ids = fixture
    barrier = Barrier(2)
    def publish(_):
        barrier.wait()
        return author.publish(module=module, element_ids=ids, command_id="author:concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(publish, (0, 1)))
    assert values[0].revision == values[1].revision
    assert _counts(core) == (1, 0, 0)


def test_partial_material_preparation_reuses_prior_resources_on_retry(fixture, monkeypatch):
    from cpn.rpnh.collaboration import materials
    core, _, author, _, module, ids = fixture
    original = materials._publish_private_system
    calls = []
    def interrupt(*args, **kwargs):
        if len(calls) == 2:
            raise RuntimeError("injected preparation interruption")
        ref = original(*args, **kwargs)
        calls.append(ref)
        return ref
    monkeypatch.setattr(materials, "_publish_private_system", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert _counts(core) == (0, 0, 0)
    monkeypatch.setattr(materials, "_publish_private_system", original)
    result = author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert result.revision.definition_ref.ref == calls[0]
    assert result.revision.element_mapping_ref.ref == calls[1]
    assert _counts(core) == (1, 0, 0)


def _with_host_revision(original, value):
    from cpn.rpnh.registration import Registration
    result = Registration()
    for declaration in original.declarations():
        kind, key = declaration["kind"], declaration["key"]
        if kind == "schema":
            result.register_schema(key, declaration["schema"])
        else:
            identity = {**declaration["identity"]}
            if kind == "executor":
                identity["revision"] = value
            getattr(result, f"register_{kind}")(key, original.resolve(kind, key),
                identity=identity, contracts=declaration["contracts"])
    return result


@pytest.mark.parametrize("fixture", ["numeric_host"], indirect=True)
def test_strict_host_comparison_distinguishes_json_boolean_from_number(fixture):
    core, _, author, _, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    alternate = _with_host_revision(_registration(), True)
    with pytest.raises(RegistryConflict, match="HOST requirements"):
        validate_closed_revision(core, first.revision.revision_ref, alternate)


def test_completed_command_replay_distinguishes_json_boolean_from_number(fixture):
    _, _, author, _, module, ids = fixture
    number = module.to_dict()
    number["designer_constraints"] = {"flag": 1}
    boolean = deepcopy(number)
    boolean["designer_constraints"]["flag"] = True
    author.publish(module=ModuleDeclaration.from_dict(number), element_ids=ids, command_id="author:r0")
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(module=ModuleDeclaration.from_dict(boolean), element_ids=ids, command_id="author:r0")


def _interrupt_after_material(monkeypatch, count):
    from cpn.rpnh.collaboration import materials
    original = materials._publish_private_system
    calls = []
    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == count:
            raise RuntimeError("interrupted after committed material")
        return result
    monkeypatch.setattr(materials, "_publish_private_system", interrupt)
    return original, calls


@pytest.mark.parametrize("cut", [1, 2, 3, 4])
def test_complete_command_recovers_after_every_material_cut_and_reopen(fixture, monkeypatch, cut):
    from cpn.rpnh.collaboration import materials
    core, gateway, author, _, module, ids = fixture
    original, calls = _interrupt_after_material(monkeypatch, cut)
    with pytest.raises(RuntimeError, match="interrupted"):
        author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert _counts(core) == (0, 0, 0)
    monkeypatch.setattr(materials, "_publish_private_system", original)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_author = ClosedModuleAuthor(next_gateway, _registration(), author.producer)
    value = next_author.publish(module=module, element_ids=ids, command_id="author:r0")
    refs = [getattr(value.revision, field).ref for field in (
        "definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")]
    assert refs[:cut] == calls
    assert _counts(reopened) == (1, 0, 0)


@pytest.mark.parametrize("cut", [1, 4])
@pytest.mark.parametrize("change", ["ids", "producer", "parent"])
def test_first_prepared_resource_locks_the_entire_command(fixture, monkeypatch, cut, change):
    from cpn.rpnh.collaboration import materials
    core, gateway, author, _, module, ids = fixture
    parent = author.publish(module=module, element_ids=ids, command_id="author:parent") if change == "parent" else None
    original, _ = _interrupt_after_material(monkeypatch, cut)
    with pytest.raises(RuntimeError, match="interrupted"):
        author.publish(module=module, element_ids=ids, command_id="author:r0")
    monkeypatch.setattr(materials, "_publish_private_system", original)
    changed_author, changed_ids, changed_parent = author, dict(ids), None
    if change == "ids":
        changed_ids["/"] = "element:" + uuid.uuid4().hex
    elif change == "parent":
        changed_parent = parent.revision.revision_ref
    else:
        ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
        body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Other author"}
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
            idempotency_key="fixture:other-principal")
        changed_author = ClosedModuleAuthor(gateway, _registration(), SourceQualifiedVersionRef("source-a", ref))
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        changed_author.publish(module=module, element_ids=changed_ids, command_id="author:r0", parent_ref=changed_parent)
    assert _counts(core) == before
    value = author.publish(module=module, element_ids=ids, command_id="author:r0")
    assert value.revision.producer_principal_ref == author.producer and value.revision.parent_revision_refs == ()


@pytest.mark.parametrize("field,value", [("stream_id", "wrong:stream"), ("aggregate_id", "wrong-id"),
    ("aggregate_type", "wrong-type"), ("producer_principal", "not-framework"),
    ("producer_invocation_id", "invocation:" + "a" * 32)])
def test_material_producer_relation_requires_its_complete_envelope(fixture, field, value):
    core, _, author, registration, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    with core.event_store.connect() as db:
        event = db.execute("SELECT published_event_id FROM relations WHERE json_extract(source_json,'$.version_id')=?",
            (str(first.revision.definition_ref.ref.resource_version_id),)).fetchone()[0]
        db.execute(f"UPDATE events SET {field}=? WHERE event_id=?", (value, event))
    with pytest.raises(RegistryConflict, match="producer relation"):
        validate_closed_revision(core, first.revision.revision_ref, registration)


def test_material_publication_producer_must_equal_the_object_envelope(fixture):
    core, _, author, registration, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    with core.event_store.connect() as db:
        db.execute("UPDATE events SET producer_invocation_id=? WHERE event_id="
            "(SELECT published_event_id FROM objects WHERE version_id=?)", (str(new_id("invocation")),
                str(first.revision.definition_ref.ref.resource_version_id)))
    with pytest.raises(RegistryConflict, match="canonical"):
        validate_closed_revision(core, first.revision.revision_ref, registration)


def test_exact_resource_publication_compares_json_types_not_python_equality(fixture):
    core, _, author, registration, module, ids = fixture
    first = author.publish(module=module, element_ids=ids, command_id="author:r0")
    with core.event_store.connect() as db:
        row = db.execute("SELECT * FROM objects WHERE version_id=?", (str(first.revision.definition_ref.ref.resource_version_id),)).fetchone()
        metadata = json.loads(row["metadata_json"])
        metadata["descriptors"]["numeric_flag"] = 1
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps(metadata), row["version_id"]))
        event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
        payload = json.loads(event["payload_json"])
        payload["metadata"] = deepcopy(metadata)
        payload["metadata"]["descriptors"]["numeric_flag"] = True
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(payload), row["published_event_id"]))
    with pytest.raises(RegistryConflict, match="exact publication"):
        validate_closed_revision(core, first.revision.revision_ref, registration)


@pytest.mark.parametrize("change", ["module", "ids", "parent", "producer"])
def test_concurrent_changed_complete_commands_have_one_winner(fixture, change):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core, gateway, author, _, module, ids = fixture
    authors, modules, identities, parents = [author, author], [module, module], [dict(ids), dict(ids)], [None, None]
    if change == "module":
        document = module.to_dict()
        document["name"] = "Alternate"
        modules[1] = ModuleDeclaration.from_dict(document)
    elif change == "ids":
        identities[1]["/"] = "element:" + uuid.uuid4().hex
    elif change == "parent":
        parents[1] = author.publish(module=module, element_ids=ids, command_id="author:parent").revision.revision_ref
    else:
        ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
        body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Concurrent author"}
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
            idempotency_key="fixture:concurrent-principal")
        authors[1] = ClosedModuleAuthor(gateway, _registration(), SourceQualifiedVersionRef("source-a", ref))
    before = _counts(core)
    barrier = Barrier(2)
    def publish(index):
        barrier.wait()
        try:
            return authors[index].publish(module=modules[index], element_ids=identities[index],
                parent_ref=parents[index], command_id="author:race")
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (0, 1)))
    assert sum(isinstance(value, RegistryConflict) for value in results) == 1
    assert _counts(core) == (before[0] + 1, before[1], before[2])
