"""Canonical semantic, byte, schema and historical proof damage reaches strong readers."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_transform import fixture, split_request, fusion_request, assembly_request
from cpn.rpnh.collaboration import SourceQualifiedResourceRef, validate_closed_revision, validate_assembly_revision
from cpn.rpnh.collaboration import plain_transform as implementation
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json


def payload_path(core, ref):
    return core.object_store.path_for_version(ref.ref.resource_version_id)


@contextmanager
def coherent_resource(core, reference, *, document=None, metadata_changes=None):
    """Synthetic coherent resource/publication mutation; successful read still needs the real proof."""
    path = payload_path(core, reference)
    raw = path.read_bytes()
    version = str(reference.ref.resource_version_id)
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (version,)).fetchone())
        old_event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0]
    metadata = json.loads(row["metadata_json"])
    changed = raw if document is None else canonical_json(document)
    metadata["size"] = len(changed)
    metadata["reference_provenance"]["publication"]["size"] = len(changed)
    for key, value in (metadata_changes or {}).items():
        metadata[key] = value
        if key in metadata["reference_provenance"]["publication"]:
            metadata["reference_provenance"]["publication"][key] = value
    event = json.loads(old_event)
    event["metadata"], event["size"] = metadata, len(changed)
    try:
        path.write_bytes(changed)
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (json.dumps(metadata), len(changed), version))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(event), row["published_event_id"]))
        yield
    finally:
        path.write_bytes(raw)
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (row["metadata_json"], row["size"], version))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (old_event, row["published_event_id"]))


def test_canonical_map_command_metadata_and_schema_changes_are_reconstructed(fixture):
    inputs, author, _ = fixture
    core = inputs[0]
    split = author.publish(**split_request(inputs[5]))
    mapping = deepcopy(split.transformation)
    mapping["transform_groups"].pop()
    before = support.counts(core)
    with coherent_resource(core, split.transform_map_ref, document=mapping):
        with pytest.raises(RegistryConflict, match="transform material differs"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    assert validate_closed_revision(core, split.revision.revision_ref, support.registration()).revision == split.revision
    command = json.loads(payload_path(core, split.command_ref).read_bytes())
    changed = deepcopy(command)
    changed["request"]["created_element_ids"] = []
    with coherent_resource(core, split.command_ref, document=changed):
        with pytest.raises(ValueError, match="current identity partition"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    changed = deepcopy(command)
    changed["prepared_materials"][0]["resource_ref"] = changed["prepared_materials"][1]["resource_ref"]
    with coherent_resource(core, split.command_ref, document=changed):
        with pytest.raises(RegistryConflict, match="exact immutable complete command"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    with coherent_resource(core, split.transform_map_ref, metadata_changes={"summary": "plausible changed metadata"}):
        with pytest.raises(RegistryConflict, match="transform material differs"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    with coherent_resource(core, split.transform_map_ref, metadata_changes={"content_schema_ref": implementation.COMMAND_SCHEMA}):
        with pytest.raises(RegistryConflict, match="exact selected content schema authority"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    # The old marker cannot reinterpret this new proof, even with canonical metadata.
    with coherent_resource(core, split.revision.definition_ref,
            metadata_changes={"descriptors": {"closed_author_command_v1": "old contract cannot encode transform"}}):
        with pytest.raises(RegistryConflict, match="immutable complete command"):
            validate_closed_revision(core, split.revision.revision_ref, support.registration())
    assert support.counts(core) == before
    assert validate_closed_revision(core, split.revision.revision_ref, support.registration()).transformation == split.transformation


def test_actual_byte_damage_to_all_new_materials_and_schema_authorities_rejects(fixture):
    inputs, author, _ = fixture
    core = inputs[0]
    args = split_request(inputs[5])
    split = author.publish(**args)
    command = json.loads(payload_path(core, split.command_ref).read_bytes())
    refs = [split.command_ref, *[SourceQualifiedResourceRef.from_dict(row["resource_ref"], catalog=core.catalog)
                                for row in command["prepared_materials"]],
            SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[implementation.MAP_SCHEMA]),
            SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[implementation.COMMAND_SCHEMA])]
    before = support.counts(core)
    observed = []
    for ref in refs:
        path = payload_path(core, ref)
        raw = path.read_bytes()
        changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode()
        assert len(changed) == len(raw) and changed != raw
        assert canonical_json(json.loads(changed)) == canonical_json(json.loads(raw))
        try:
            path.write_bytes(changed)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, split.revision.revision_ref, support.registration())
            observed.append(ref.to_dict())
        finally:
            path.write_bytes(raw)
    assert support.counts(core) == before
    assert validate_closed_revision(core, split.revision.revision_ref, support.registration()).revision == split.revision
    print("TRANSFORM_BYTES_REJECTED=" + json.dumps(observed, sort_keys=True))


def test_ancestor_transform_and_schema_damage_propagates_to_descendant_and_actual_assembly(fixture):
    inputs, author, assembly = fixture
    core, _, _, _, _, base, _ = inputs
    split = author.publish(**split_request(base))
    fused = author.publish(**fusion_request(split))
    value = assembly.publish(**assembly_request(split, fused))
    before = support.counts(core)
    for ref in (base.revision.definition_ref, split.command_ref, split.transform_map_ref,
                SourceQualifiedResourceRef(author.binding["source_id"], author.schemas[implementation.MAP_SCHEMA])):
        path = payload_path(core, ref)
        raw = path.read_bytes()
        # Actual payload damage, preserving the object's metadata and exact reference.
        changed = b"!" + raw[1:]
        try:
            path.write_bytes(changed)
            with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, fused.revision.revision_ref, support.registration())
            with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
                validate_assembly_revision(core, value.revision.revision_ref, support.registration())
        finally:
            path.write_bytes(raw)
    checked = validate_assembly_revision(core, value.revision.revision_ref, support.registration())
    assert checked.revision == value.revision and checked.generated.revision == value.generated.revision
    assert support.counts(core) == before


def test_final_publication_reconstructs_source_after_preparation(fixture, monkeypatch):
    inputs, author, _ = fixture
    core, _, _, _, _, base, _ = inputs
    path = payload_path(core, base.revision.definition_ref)
    raw = path.read_bytes()
    original = implementation._publish_private_system
    def damage_after_outputs(core, owner, resource):
        result = original(core, owner, resource)
        if resource.idempotency_key.endswith(":host_requirements"):
            path.write_bytes(b"!" + raw[1:])
        return result
    args = split_request(base)
    monkeypatch.setattr(implementation, "_publish_private_system", damage_after_outputs)
    try:
        with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
            author.publish(**args)
        assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == 1
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(implementation, "_publish_private_system", original)
    split = author.publish(**args)
    assert validate_closed_revision(core, split.revision.revision_ref, support.registration()).revision == split.revision
