"""Actual new proof bytes, selected authority and legacy dispatch boundaries."""
from dataclasses import replace
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_result import fixture
from cpn.rpnh.collaboration import (
    SourceQualifiedResourceRef, plain_merge_schema_data, plain_merge_result_schema_data,
    validate_closed_revision,
)
from cpn.rpnh.collaboration import plain_merge_result as implementation
from cpn.rpnh.collaboration.materials import MODULE_SCHEMA
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json


def simple_analysis(inputs):
    doc = inputs[5].module.to_dict(); doc["name"] = "ExplicitLeftName"
    left = support.publish(inputs, doc, "left")
    return inputs[4].analyze(**support.request(inputs, left, inputs[5]))


def payload_path(core, reference):
    return core.object_store.path_for_version(reference.ref.resource_version_id)


def test_same_length_key_order_rewrite_of_command_and_all_five_materials_is_rejected(fixture):
    inputs, author = fixture
    core = inputs[0]
    analysis = simple_analysis(inputs)
    args = {"analysis_ref": analysis.analysis_ref, "choices": [], "command_id": "byte-control"}
    result = author.publish(**args)
    command = json.loads(payload_path(core, result.command_ref).read_bytes())
    references = [result.command_ref] + [SourceQualifiedResourceRef.from_dict(row["resource_ref"], catalog=core.catalog)
                                        for row in command["prepared_materials"]]
    before = support.counts(core)
    observed = []
    for reference in references:
        path = payload_path(core, reference)
        raw = path.read_bytes(); doc = json.loads(raw)
        changed = json.dumps(dict(reversed(list(doc.items()))), ensure_ascii=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        assert len(changed) == len(raw) and changed != raw
        assert canonical_json(json.loads(changed)) == canonical_json(doc)
        try:
            path.write_bytes(changed)
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_closed_revision(core, result.revision.revision_ref, support.registration())
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                author.publish(**args)
            assert support.counts(core) == before
            observed.append(reference.to_dict())
        finally:
            path.write_bytes(raw)
    assert len(observed) == 6
    assert validate_closed_revision(core, result.revision.revision_ref, support.registration()).revision == result.revision
    assert author.publish(**args).revision == result.revision
    assert support.counts(core) == before
    print("B1_RAW_BYTE_REJECTION=" + json.dumps(observed, sort_keys=True))


@pytest.mark.parametrize("descriptor", ["benign", "unknown", "dual"])
def test_legacy_scalar_descriptors_remain_compatible_but_command_markers_do_not(fixture, monkeypatch, descriptor):
    from cpn.rpnh.collaboration import materials
    inputs, _ = fixture
    original = materials._publish_private_system
    def decorated(core, owner, resource):
        if resource.content_schema_ref == MODULE_SCHEMA:
            key = {"benign": "display_note", "unknown": "closed_author_command_v999",
                   "dual": implementation.RESULT_MARKER}[descriptor]
            resource = replace(resource, descriptors={**resource.descriptors, key: "fixture metadata"})
        return original(core, owner, resource)
    monkeypatch.setattr(materials, "_publish_private_system", decorated)
    if descriptor == "benign":
        value = support.publish(inputs, inputs[5].module.to_dict(), "legacy-metadata")
        checked = validate_closed_revision(inputs[0], value.revision.revision_ref, support.registration())
        assert checked.module.to_dict() == inputs[5].module.to_dict()
        assert checked.revision.parent_revision_refs == (inputs[5].revision.revision_ref,)
    else:
        with pytest.raises(RegistryConflict, match="unknown or dual"):
            support.publish(inputs, inputs[5].module.to_dict(), "legacy-metadata")


def test_result_opt_in_preserves_prior_schema_bytes_and_types():
    old, old_types, _ = plain_merge_schema_data()
    schemas, types, _ = plain_merge_result_schema_data()
    assert set(schemas) - set(old) == {implementation.RESULT_COMMAND_SCHEMA, implementation.RESOLUTION_SCHEMA}
    assert types == old_types
    assert all(canonical_json(schemas[key]) == canonical_json(value) for key, value in old.items())


def test_first_command_locks_schema_authority_bytes_and_equal_content_exact_ref(fixture, monkeypatch):
    inputs, author = fixture
    core, gateway = inputs[:2]
    analysis = simple_analysis(inputs)
    schema = implementation.RESOLUTION_SCHEMA
    selected = author.schemas[schema]
    path = core.object_store.path_for_version(selected.resource_version_id)
    raw = path.read_bytes()
    alternative = implementation._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=raw, media_type="application/schema+json",
        content_schema_ref="registry_v1/registry_type_catalog/v1", summary="Equal schema alternate fixture authority",
        lifetime_ref=gateway._bootstrap_ref,
        descriptors={"host_registration_kind": "schema", "registered_key": schema},
        idempotency_key="fixture:alternate-resolution-schema"))
    assert alternative != selected
    assert core.object_store.path_for_version(alternative.resource_version_id).read_bytes() == raw
    original = implementation._publish_private_system
    args = {"analysis_ref": analysis.analysis_ref, "choices": [], "command_id": "schema-freeze"}
    def after_complete_command(core, owner, resource):
        value = original(core, owner, resource)
        if resource.content_schema_ref == implementation.RESULT_COMMAND_SCHEMA:
            raise RuntimeError("fixture cut after the complete command")
        return value
    before = support.counts(core)
    monkeypatch.setattr(implementation, "_publish_private_system", after_complete_command)
    with pytest.raises(RuntimeError, match="fixture cut"):
        author.publish(**args)
    monkeypatch.setattr(implementation, "_publish_private_system", original)
    frozen = support.counts(core)
    assert frozen[0] == before[0] + 1
    try:
        author.schemas[schema] = alternative
        with pytest.raises(RegistryConflict, match="conflict"):
            author.publish(**args)
        assert support.counts(core) == frozen
    finally:
        author.schemas[schema] = selected
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert len(changed) == len(raw) and changed != raw
    assert canonical_json(json.loads(changed)) == canonical_json(json.loads(raw))
    try:
        path.write_bytes(changed)
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            author.publish(**args)
        assert support.counts(core) == frozen
    finally:
        path.write_bytes(raw)
    value = author.publish(**args)
    final = support.counts(core)
    try:
        path.write_bytes(changed)
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            validate_closed_revision(core, value.revision.revision_ref, support.registration())
        assert support.counts(core) == final
    finally:
        path.write_bytes(raw)
    assert validate_closed_revision(core, value.revision.revision_ref, support.registration()).revision == value.revision
    assert author.publish(**args).revision == value.revision
    assert support.counts(core) == final
    command = json.loads(payload_path(core, value.command_ref).read_bytes())
    catalog = command["schema_authorities"][MODULE_SCHEMA]
    assert catalog["entity_type"] == "registry_type_catalog/v1"
    catalog_path = core.object_store.path_for_version(TypedId.parse(catalog["version_id"]))
    catalog_raw = catalog_path.read_bytes()
    catalog_changed = json.dumps(dict(reversed(list(json.loads(catalog_raw).items()))), ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert len(catalog_changed) == len(catalog_raw) and catalog_changed != catalog_raw
    assert canonical_json(json.loads(catalog_changed)) == canonical_json(json.loads(catalog_raw))
    try:
        catalog_path.write_bytes(catalog_changed)
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            validate_closed_revision(core, value.revision.revision_ref, support.registration())
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            author.publish(**args)
        assert support.counts(core) == final
    finally:
        catalog_path.write_bytes(catalog_raw)
    assert validate_closed_revision(core, value.revision.revision_ref, support.registration()).revision == value.revision
    assert author.publish(**args).revision == value.revision
    assert support.counts(core) == final


def test_canonical_descriptor_parent_material_and_command_changes_reject_exact_result_proof(fixture):
    from copy import deepcopy
    inputs, author = fixture
    core = inputs[0]
    analysis = simple_analysis(inputs)
    value = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="descriptor-control")
    ref = value.revision.revision_ref
    path = core.object_store.path_for_version(ref.ref.version_id)
    raw = path.read_bytes()
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (str(ref.ref.version_id),)).fetchone())
        original_event = db.execute("SELECT payload_json FROM events WHERE event_id=?",
                                    (row["published_event_id"],)).fetchone()[0]
    before = support.counts(core)
    for damage in ("ordered_parents", "material_refs", "command_id"):
        document = deepcopy(value.revision.to_dict())
        if damage == "ordered_parents":
            document["parent_revision_refs"].reverse()
        elif damage == "material_refs":
            document["element_mapping_ref"], document["boundary_mapping_ref"] = (
                document["boundary_mapping_ref"], document["element_mapping_ref"])
        else:
            document["command_id"] = "different-valid-command"
        changed = canonical_json(document)
        publication = json.loads(original_event)
        publication["metadata"], publication["size"] = document, len(changed)
        try:
            # This synthetic fixture keeps object, authoritative publication and
            # actual descriptor payload coherent, to reach the merge proof gate.
            path.write_bytes(changed)
            with core.event_store.connect() as db:
                db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?",
                    (json.dumps(document), len(changed), row["version_id"]))
                db.execute("UPDATE events SET payload_json=? WHERE event_id=?",
                    (json.dumps(publication), row["published_event_id"]))
            with pytest.raises(RegistryConflict, match="exact immutable complete command"):
                validate_closed_revision(core, ref, support.registration())
            assert support.counts(core) == before
        finally:
            path.write_bytes(raw)
            with core.event_store.connect() as db:
                db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?",
                    (row["metadata_json"], row["size"], row["version_id"]))
                db.execute("UPDATE events SET payload_json=? WHERE event_id=?",
                    (original_event, row["published_event_id"]))
        assert validate_closed_revision(core, ref, support.registration()).revision == value.revision
        assert support.counts(core) == before
