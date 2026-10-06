"""Original member byte/metadata locks through real v9 producers and consumers."""
from contextlib import contextmanager
import hashlib
import json

import pytest

import test_typed_assembly_v9 as support
from cpn.rpnh.collaboration import (
    AssemblyAuthorV9, validate_assembly_revision, validate_closed_revision,
    validate_generated_assembly_v9,
)
from cpn.rpnh.collaboration import assembly_v9 as assembly
from cpn.rpnh.collaboration.materials import ELEMENT_SCHEMA
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction

ROLES = ("definition", "element_mapping", "boundary_mapping", "host_requirements")


def reordered(raw):
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))),
                         ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    assert changed != raw and len(changed) == len(raw)
    assert canonical_json(json.loads(changed)) == canonical_json(json.loads(raw))
    return changed


@contextmanager
def coherent_metadata(core, reference, changes):
    """Keep publication metadata coherent; isolate the new v9 selection lock."""
    version = str(reference.ref.resource_version_id)
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (version,)).fetchone())
        old_event = db.execute("SELECT payload_json FROM events WHERE event_id=?",
                               (row["published_event_id"],)).fetchone()[0]
    metadata = json.loads(row["metadata_json"])
    for key, value in changes.items():
        metadata[key] = value
        if key in metadata["reference_provenance"]["publication"]:
            metadata["reference_provenance"]["publication"][key] = value
    event = json.loads(old_event)
    event["metadata"] = metadata
    try:
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps(metadata), version))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(event), row["published_event_id"]))
        yield
    finally:
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (row["metadata_json"], version))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (old_event, row["published_event_id"]))


def refuse_pair(core, reg, value):
    with pytest.raises(RegistryConflict, match="member exact proof/material selection differs"):
        validate_assembly_revision(core, value.revision.revision_ref, reg)
    with pytest.raises(RegistryConflict, match="member exact proof/material selection differs"):
        validate_generated_assembly_v9(core, value.revision.revision_ref,
                                       value.revision.generated_revision_ref, reg)


@pytest.mark.parametrize("role", ROLES)
def test_original_member_same_length_json_bytes_are_locked(tmp_path, role):
    core, _, reg, _, author, composer = support.world(tmp_path / role)
    member = support.publish_member(author, reg, "typed:source")
    args = support.request(member)
    value = composer.publish(**args)
    reference = getattr(member[0].revision, role + "_ref")
    path = core.object_store.path_for_version(reference.ref.resource_version_id)
    raw = path.read_bytes()
    pins = value.plan["members"][0]["resolution"]["original_materials"]
    assert [p["role"] for p in pins] == list(ROLES)
    pin = next(p for p in pins if p["role"] == role)
    assert pin["resource_ref"] == reference.to_dict()
    assert pin["bytes"] == len(raw) and pin["sha256"] == hashlib.sha256(raw).hexdigest()
    assert pin["metadata"] == json.loads(core.event_store.object_row(reference.ref.resource_version_id)["metadata_json"])
    before = support.counts(core)
    try:
        path.write_bytes(reordered(raw))
        # Ordinary v1 still proves its documented semantic contract unchanged.
        assert validate_closed_revision(core, member[0].revision.revision_ref, reg).revision == member[0].revision
        refuse_pair(core, reg, value)
        with pytest.raises(RegistryConflict, match="conflict"):
            composer.publish(**args)
        assert support.counts(core) == before
    finally:
        path.write_bytes(raw)
    support.check_pair(core, reg, value)
    assert composer.publish(**args).revision == value.revision
    assert support.counts(core) == before


@pytest.mark.parametrize("change", ["metadata", "schema_authority", "schema_bytes"])
def test_original_member_metadata_and_schema_authority_are_locked(tmp_path, change):
    core, gateway, reg, _, author, composer = support.world(tmp_path / change)
    member = support.publish_member(author, reg, "typed:source")
    selected = author.schemas[ELEMENT_SCHEMA]
    schema_path = core.object_store.path_for_version(selected.resource_version_id)
    raw = schema_path.read_bytes()
    alternative = _publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=raw,
        media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
        summary="Equal schema alternate fixture authority", lifetime_ref=gateway._bootstrap_ref,
        descriptors={"host_registration_kind": "schema", "registered_key": ELEMENT_SCHEMA},
        idempotency_key="fixture:alternate-member-schema"))
    assert alternative != selected
    assert core.object_store.path_for_version(alternative.resource_version_id).read_bytes() == raw
    args = support.request(member)
    value = composer.publish(**args)
    before = support.counts(core)
    if change == "schema_bytes":
        try:
            schema_path.write_bytes(reordered(raw))
            assert validate_closed_revision(core, member[0].revision.revision_ref, reg).revision == member[0].revision
            refuse_pair(core, reg, value)
        finally:
            schema_path.write_bytes(raw)
    else:
        changes = ({"summary": "changed valid summary"} if change == "metadata" else
                   {"content_schema_authority_ref": {"resource_id": str(alternative.resource_id),
                                                     "resource_version_id": str(alternative.resource_version_id)}})
        with coherent_metadata(core, member[0].revision.element_mapping_ref, changes):
            assert validate_closed_revision(core, member[0].revision.revision_ref, reg).revision == member[0].revision
            refuse_pair(core, reg, value)
            with pytest.raises(RegistryConflict, match="conflict"):
                composer.publish(**args)
    assert support.counts(core) == before
    support.check_pair(core, reg, value)


def test_complete_command_locks_original_bytes_before_generated_publication(tmp_path, monkeypatch):
    core, gateway, reg, producer, author, composer = support.world(tmp_path / "recovery")
    member = support.publish_member(author, reg, "typed:source")
    args = support.request(member)
    key = assembly._command(args["command_id"])
    expected_a = assembly._result_ref(core, composer.binding["source_id"], args["command_id"], None)
    _, _, expected_g = assembly._generated_ref(core, composer.binding["source_id"], args["command_id"], None)
    original = RegistryTransaction.commit
    def cut(tx):
        result = original(tx)
        if tx.event_store is core.event_store and tx.idempotency_key == key + ":plan":
            assert tx._closed
            raise support.DurableCut("original inputs locked")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, "commit", cut)
        with pytest.raises(support.DurableCut, match="original inputs locked"):
            composer.publish(**args)
    assert core.event_store.object_row(expected_a.ref.version_id) is None
    assert core.event_store.object_row(expected_g.ref.version_id) is None
    plan_ref = assembly._material_ref(core, composer.binding, key + ":plan")
    plan_raw = core.object_store.path_for_version(plan_ref.ref.resource_version_id).read_bytes()
    assert len(json.loads(plan_raw)["members"][0]["resolution"]["original_materials"]) == 4
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV9(next_gateway, support.registration(), producer)
    before = support.counts(reopened)
    paths = [core.object_store.path_for_version(getattr(member[0].revision, r + "_ref").ref.resource_version_id)
             for r in ROLES]
    paths.append(core.object_store.path_for_version(author.schemas[ELEMENT_SCHEMA].resource_version_id))
    for path in paths:
        raw = path.read_bytes()
        try:
            path.write_bytes(reordered(raw))
            with pytest.raises(RegistryConflict, match="conflict"):
                replay.publish(**args)
            assert support.counts(reopened) == before
            assert core.event_store.object_row(expected_g.ref.version_id) is None
        finally:
            path.write_bytes(raw)
    value = replay.publish(**args)
    assert value.revision.revision_ref == expected_a and value.revision.generated_revision_ref == expected_g
    assert canonical_json(value.plan) == plan_raw
    support.check_pair(reopened, replay.registration, value)
