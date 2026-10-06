"""Exact historical basis reads and actual dependency bytes; no v2 writes."""
from dataclasses import replace
import hashlib

import pytest

from cpn.rpnh.registry.event_store import fact_event_envelope, RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.preserved_binding_contracts import PreservedBasis
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_adoption_prefix import adopted_fixture


def basis_for(event):
    from cpn.rpnh.registry.publication import _version_from_payload
    return PreservedBasis(_version_from_payload(event.payload["net_instance_ref"]), event.event_id,
        event.transaction_id, event.task_control_sequence, hashlib.sha256(canonical_json(fact_event_envelope(event))).hexdigest())


def test_basis_reads_exact_event_and_records_actual_canonical_dependencies(adopted_fixture):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, publication, anchor = adopted_fixture
    before = len(core.event_store.list_events())
    selected = basis_for(anchor)
    result = read_preserved_basis(core, selected)
    assert result.basis == selected
    refs = [row["ref"] for row in result.dependency_evidence]
    assert any(ref["version_id"] == str(publication.net_ref.version_id) for ref in refs)
    assert any(ref["version_id"] == str(publication.declaration_resource_ref.resource_version_id) for ref in refs)
    assert any(ref["entity_type"] == "registry_type_catalog/v1" for ref in refs)
    assert len(core.event_store.list_events()) == before
    evidence = result.dependency_evidence
    evidence[0]["sha256"] = "0" * 64
    assert result.dependency_evidence != evidence


@pytest.mark.parametrize("axis", ["digest", "transaction", "sequence", "net"])
def test_basis_cannot_redirect_the_exact_selected_event(adopted_fixture, axis):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, _, anchor = adopted_fixture
    basis = basis_for(anchor)
    changes = {"digest": {"adoption_event_sha256": "0" * 64},
        "transaction": {"transaction_id": new_id("transaction")},
        "sequence": {"task_control_sequence": basis.task_control_sequence + 1},
        "net": {"net_ref": VersionRef("net_instance/v1", new_id("net_instance"), new_id("net_instance_version"))}}
    with pytest.raises(RegistryConflict):
        read_preserved_basis(core, replace(basis, **changes[axis]))


def test_basis_snapshot_bytes_match_every_controlled_actual_read(adopted_fixture):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, _, anchor = adopted_fixture
    result = read_preserved_basis(core, basis_for(anchor))
    evidence = result.dependency_evidence
    assert evidence == sorted(evidence, key=lambda row: canonical_json(row["ref"]))
    for row in evidence:
        prepared = core.get_version(row["ref"]["version_id"])
        payload = core.object_store.read_registered(prepared)
        assert row["size"] == len(payload)
        assert row["media_type"] == prepared.media_type
        assert row["sha256"] == hashlib.sha256(payload).hexdigest()


def test_basis_supplied_cut_never_opens_another_connection_or_resolves_host(adopted_fixture, monkeypatch):
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, _, anchor = adopted_fixture
    def forbidden(*args, **kwargs):
        raise AssertionError("basis read cannot open another DB cut or resolve HOST")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        monkeypatch.setattr(core.event_store, "connect", forbidden)
        monkeypatch.setattr(Registration, "resolve", forbidden)
        assert read_preserved_basis(core, basis_for(anchor), _db=db).basis == basis_for(anchor)


@pytest.mark.parametrize("axis", ["descriptor_bytes", "schema_bytes", "missing_bytes"])
def test_basis_rechecks_actual_bytes_with_warmed_legacy_caches(adopted_fixture, axis):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.publication import _version_from_payload
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    core, publication, anchor = adopted_fixture
    read_preserved_basis(core, basis_for(anchor))
    if axis == "schema_bytes":
        net = core.get_version(publication.net_ref.version_id).metadata
        output = core.get_version(_version_from_payload(net["output_binding_refs"][0]).version_id).metadata
        prepared = core.get_version(output["content_schema_ref"]["resource_version_id"])
        payload = core.object_store.read_registered(prepared)
        # Same byte count; still schema-shaped JSON, now unsupported primitive.
        altered = payload.replace(b'"string"', b'"strung"')
        assert altered != payload and len(altered) == len(payload)
        core.object_store.path_for_version(prepared.version_id).write_bytes(altered)
    else:
        prepared = core.get_version(publication.net_ref.version_id)
        path = core.object_store.path_for_version(prepared.version_id)
        if axis == "missing_bytes":
            path.unlink()
        else:
            payload = path.read_bytes()
            marker = str(publication.net_ref.entity_id).encode()
            replacement = marker[:-1] + (b"1" if marker[-1:] != b"1" else b"2")
            path.write_bytes(payload.replace(marker, replacement))
    with pytest.raises((RegistryConflict, ContentSchemaAuthorityError)):
        read_preserved_basis(core, basis_for(anchor))


@pytest.mark.parametrize("target", ["net", "declaration"])
@pytest.mark.parametrize("state", ["published", "provisional", "bad_promotion_terminal"])
def test_basis_preserves_canonical_published_dependencies(adopted_fixture, target, state):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    from test_collaboration_authoring import _inject_temporary_member
    core, publication, anchor = adopted_fixture
    ref = publication.net_ref if target == "net" else publication.declaration_resource_ref.as_version_ref()
    root = _inject_temporary_member(core, "object", str(ref.version_id))
    if state != "provisional":
        terminal = core.begin(idempotency_key="fixture:basis:promotion").commit()[-1]
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(terminal.transaction_id), str(new_id("operation_result_version")),
                 str(new_id("marking_checkpoint_version")), root))
            if state == "bad_promotion_terminal":
                db.execute("UPDATE events SET payload_json='{}' WHERE event_id=?", (str(terminal.event_id),))
    if state == "published":
        result = read_preserved_basis(core, basis_for(anchor))
        assert any(row["ref"]["version_id"] == str(ref.version_id) for row in result.dependency_evidence)
    else:
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            read_preserved_basis(core, basis_for(anchor))


def test_basis_rejects_unknown_tar_role_before_treating_it_as_json(adopted_fixture):
    import io
    import tarfile
    from cpn.rpnh.registry._candidate_plan_reads import PlanReadClosure
    from cpn.rpnh.registry._event_store.adoption_reads import AdoptionPrefixReads
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    from cpn.rpnh.registry.strict_contracts import ref_payload
    core, publication, _ = adopted_fixture
    ref = VersionRef("workspace_revision/v1", new_id("workspace_lineage"), new_id("workspace_revision"))
    root = core.get_version(publication.root_ref.version_id).metadata
    metadata = {"workspace_lineage_id": str(ref.entity_id), "workspace_revision_id": str(ref.version_id),
        "workspace_revision_ref": ref_payload(ref), "run_ref": root["run_ref"], "task_ref": root["task_ref"],
        "net_instance_ref": ref_payload(publication.net_ref), "parent_revision_ref": None, "base_revision_ref": None,
        "producer_invocation_ref": None, "transition_firing_ref": None, "firing_workspace_binding_ref": None,
        "disposition": "genesis", "changed_paths": [], "deleted_paths": [], "path_deltas": [], "inventory_paths": [],
        "conflict_paths": [], "semantic_output_refs": [], "trace_summary_refs": [], "payload_kind": "full_workspace_tar", "settled": True}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w"):
        pass
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=buffer.getvalue(), metadata=metadata, media_type="application/x-tar",
        schema_ref="registry_v1/workspace_revision/v1", idempotency_key="fixture:basis:tar")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        closure = PlanReadClosure(db, core)
        reader = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, _plan_reads=closure)
        with pytest.raises(RegistryCorruptError, match="unsupported basis dependency"):
            reader.metadata(ref)
        assert not any(row["ref"]["version_id"] == str(ref.version_id) for row in closure.store.evidence())


@pytest.fixture
def owner_basis_fixture(tmp_path, monkeypatch):
    import socket
    import subprocess
    import urllib.request
    from cpn.rpnh.collaboration import author_material_schema_data
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.run import OwnerInput, start_run
    from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
    from test_native_net_operations import TEXT, _registration, _simple_module
    def forbidden(*args, **kwargs):
        raise AssertionError("owner basis fixture cannot use external effects")
    for target, field in ((socket, "socket"), (subprocess, "Popen"), (urllib.request, "Request"), (urllib.request, "urlopen")):
        monkeypatch.setattr(target, field, forbidden)
    schemas, types, paths = author_material_schema_data()
    module, task = _simple_module(), OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(module, _registration(), run_dir=tmp_path / "basis-owner", task_input=task,
        entry_inputs={"request": task}, budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-basis", owner_statement="Offline basis reader", command_id="fixture:basis:fresh",
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner.schema_gateway.bind_source_identity(source_id="basis-source", command_id="fixture:basis:source")
    first = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[0]
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    initial = read_preserved_basis(owner._core, basis_for(first))
    result = apply_replacement(owner, prepare_replacement(owner, _simple_module("Next")), command_id="fixture:basis:replace")
    assert result["status"] == "ADOPTED"
    last = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    return owner._core, first, last, initial


def test_historical_basis_bytes_remain_exact_after_real_owner_head_advance(owner_basis_fixture):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, first, last, initial = owner_basis_fixture
    assert read_preserved_basis(core, basis_for(first)) == initial
    new = read_preserved_basis(core, basis_for(last))
    assert new.basis.net_ref != initial.basis.net_ref
    refs = [row["ref"] for row in new.dependency_evidence]
    for field in ("owner_command_result_ref", "candidate_ref", "owner_candidate_checkpoint_ref"):
        assert last.payload[field] in refs


def test_basis_records_new_schema_valid_resource_bytes_without_claiming_frozen_replay(owner_basis_fixture):
    import json
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.publication import _resource_from_payload
    from test_candidate_plan_offline import _replace_resource_payload
    core, _, last, _ = owner_basis_fixture
    before = read_preserved_basis(core, basis_for(last))
    pair = last.payload["owner_command_ref"]
    resource = _resource_from_payload({"resource_id": pair["logical_id"], "resource_version_id": pair["version_id"]})
    prepared = core.get_version(resource.resource_version_id)
    value = json.loads(core.object_store.read_registered(prepared))
    # Reorder object keys without changing values or byte count. The basis is
    # unchanged; this reader collects a new snapshot rather than validating a
    # previously frozen plan's evidence.
    payload = core.object_store.read_registered(prepared)
    changed = json.dumps(dict(reversed(tuple(value.items()))), ensure_ascii=False, separators=(",", ":")).encode()
    assert changed != payload and len(changed) == len(payload)
    _replace_resource_payload(core, resource, changed)
    after = read_preserved_basis(core, basis_for(last))
    assert before.basis == after.basis
    assert before.dependency_evidence != after.dependency_evidence


@pytest.mark.parametrize("axis", ["terminal_count", "object_schema", "provisional"])
def test_indirect_schema_catalog_gets_prefix_canonical_checks(adopted_fixture, axis):
    import json
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.event_store import RegistryCorruptError
    from test_collaboration_authoring import _inject_temporary_member
    core, _, anchor = adopted_fixture
    snapshot = read_preserved_basis(core, basis_for(anchor))
    ref = next(row["ref"] for row in snapshot.dependency_evidence if row["ref"]["entity_type"] == "registry_type_catalog/v1")
    with core.event_store.connect() as db:
        obj = db.execute("SELECT * FROM objects WHERE version_id=?", (ref["version_id"],)).fetchone()
        if axis == "terminal_count":
            terminal = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (obj["transaction_id"],)).fetchone()
            payload = json.loads(terminal["payload_json"])
            payload["object_count"] += 1
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(payload).decode(), terminal["event_id"]))
        elif axis == "object_schema":
            publication = db.execute("SELECT * FROM events WHERE event_id=?", (obj["published_event_id"],)).fetchone()
            payload = json.loads(publication["payload_json"])
            payload["schema_ref"] = "registry_v1/principal/v1"
            db.execute("UPDATE objects SET schema_ref=? WHERE version_id=?", (payload["schema_ref"], ref["version_id"]))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(payload).decode(), publication["event_id"]))
    if axis == "provisional":
        _inject_temporary_member(core, "object", ref["version_id"])
    with pytest.raises((RegistryConflict, RegistryCorruptError)):
        read_preserved_basis(core, basis_for(anchor))


def test_basis_rejects_foreign_or_autocommit_cut(adopted_fixture, tmp_path):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, _, anchor = adopted_fixture
    with core.event_store.connect() as db:
        with pytest.raises(TypeError, match="existing SQLite"):
            read_preserved_basis(core, basis_for(anchor), _db=db)
    other = _RegistryCore(tmp_path / "other-basis", create=True, catalog=core.catalog)
    with other.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="another Registry"):
            read_preserved_basis(core, basis_for(anchor), _db=db)


def test_recording_context_cannot_be_an_application_callback(adopted_fixture):
    from cpn.rpnh.registry._event_store.adoption_reads import AdoptionPrefixReads
    core, _, _ = adopted_fixture
    class Application:
        def __getattr__(self, key):
            raise AssertionError("fixed context must reject before invoking application access")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(TypeError, match="fixed same-Registry"):
            AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, _plan_reads=Application())


def test_typed_basis_input_is_not_coerced_from_arbitrary_application_objects(adopted_fixture):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    core, _, _ = adopted_fixture
    class Application:
        def to_dict(self):
            raise AssertionError("reader must not invoke application conversion")
    with pytest.raises(TypeError, match="exact PreservedBasis"):
        read_preserved_basis(core, Application())
