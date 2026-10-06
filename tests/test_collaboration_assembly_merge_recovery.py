"""Immutable first-command prefixes, exact authority/metadata and stale owners."""
from copy import deepcopy
from contextlib import contextmanager
import json

import pytest

from cpn.rpnh.collaboration import (AssemblyMergeAuthor, AssemblyMergeAnalyzer, AssemblyAuthorV6,
    validate_assembly_revision, read_assembly_merge_analysis)
from cpn.rpnh.collaboration import assembly_v6, assembly_merge, _assembly_merge_support as support
from cpn.rpnh.collaboration.materials import ELEMENT_SCHEMA
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_merge import fixture, analyze, choices, publish, reopen, request, _registration
from test_collaboration_assembly_v2_publication import counts, assert_no_run


class DurableCut(RuntimeError):
    pass


CUTS = ("plan", "definition", "element_mapping", "boundary_mapping", "host_requirements", "generated", "compiled_inventory", "lowering_mapping", "assembly")


@pytest.mark.parametrize("cut", CUTS)
def test_every_committed_result_prefix_reopens_new_writer_and_recovers(fixture, monkeypatch, cut):
    core, gateway, _, producer, *rest = fixture
    analysis = analyze(fixture)
    author = fixture[11]
    args = {"analysis_ref": analysis.analysis_ref, "choices": choices(analysis), "generated_continuity": "left", "command_id": "durable"}
    observed, payloads = [], {}
    original_material, original_object = author._publish_spec, author._publish_object
    def material(spec):
        result = original_material(spec)
        observed.append(spec["role"])
        path = core.object_store.path_for_version(result.ref.resource_version_id)
        payloads[str(path)] = path.read_bytes()
        if spec["role"] == cut: raise DurableCut(cut)
        return result
    def descriptor(spec, key):
        result = original_object(spec, key)
        observed.append(spec["role"])
        from cpn.rpnh.registry.identities import TypedId
        path = core.object_store.path_for_version(TypedId.parse(spec["revision_ref"]["ref"]["version_id"]))
        payloads[str(path)] = path.read_bytes()
        if spec["role"] == cut: raise DurableCut(cut)
        return result
    monkeypatch.setattr(author, "_publish_spec", material)
    monkeypatch.setattr(author, "_publish_object", descriptor)
    with pytest.raises(DurableCut, match=cut): author.publish(**args)
    assert observed == list(CUTS[:CUTS.index(cut) + 1])
    reference = assembly_v6._result_ref(core, author.binding, "durable", fixture[8].revision.revision_ref)
    assert (core.event_store.object_row(reference.ref.version_id) is not None) == (cut == "assembly")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    if cut != "assembly":
        with pytest.raises((RegistryConflict, ValueError)): validate_assembly_revision(reader, reference, _registration())
    writer = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    new_gateway = RegistryRegistrationGateway(writer, gateway._task_ref, gateway._bootstrap_ref)
    resumed = AssemblyMergeAuthor(new_gateway, _registration(), producer)
    before = counts(writer)
    altered = deepcopy(args)
    altered["choices"][0]["reason"] = "Different caller reason after committed prefix"
    with pytest.raises(RegistryConflict): resumed.publish(**altered)
    assert counts(writer) == before
    value = resumed.publish(**args)
    assert value.revision.revision_ref == reference
    from pathlib import Path
    assert all(Path(path).read_bytes() == raw for path, raw in payloads.items())
    reopen(writer, value)
    frozen = counts(writer)
    assert resumed.publish(**args).revision == value.revision and counts(writer) == frozen
    assert_no_run(writer)
    print("ASSEMBLY_V6_DURABLE_CUT=" + json.dumps({"cut": cut, "actual_committed_stages": observed,
        "original_bytes_preserved": True, "result_ref": reference.to_dict()}, sort_keys=True))


@pytest.mark.parametrize("kind", ["analysis", "merge", "edit"])
def test_first_prefix_pins_schema_authority_bytes_and_all_output_metadata(fixture, monkeypatch, kind):
    core, gateway, registration, producer = fixture[:4]
    if kind == "analysis":
        author = fixture[10]
        args = {"local_ref": fixture[8].revision.revision_ref, "incoming_ref": fixture[9].revision.revision_ref, "command_id": "authority-analysis"}
        run = author.analyze
        schema, first = assembly_merge.ANALYSIS_SCHEMA, "command"
    else:
        analysis = analyze(fixture)
        if kind == "merge":
            author = fixture[11]
            args = {"analysis_ref": analysis.analysis_ref, "choices": choices(analysis), "generated_continuity": "left", "command_id": "authority-merge"}
        else:
            parent = publish(fixture, analysis)
            author = AssemblyAuthorV6(gateway, registration, producer)
            args = request(fixture[5][1], fixture[5][0], command="authority-edit", parent=parent)
        run = author.publish
        schema, first = ELEMENT_SCHEMA, "plan"
    selected = author.schemas[schema]
    path = core.object_store.path_for_version(selected.resource_version_id)
    raw = path.read_bytes()
    alternative = support._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=raw, media_type="application/schema+json",
        content_schema_ref="registry_v1/registry_type_catalog/v1", summary="Equal alternate test schema",
        lifetime_ref=gateway._bootstrap_ref, descriptors={"host_registration_kind": "schema", "registered_key": schema},
        idempotency_key="alternate:" + kind))
    original = author._publish_spec
    def cut(spec):
        result = original(spec)
        if spec["role"] == first: raise DurableCut(first)
        return result
    monkeypatch.setattr(author, "_publish_spec", cut)
    with pytest.raises(DurableCut): run(**args)
    monkeypatch.setattr(author, "_publish_spec", original)
    frozen = counts(core)
    author.schemas[schema] = alternative
    with pytest.raises(RegistryConflict): run(**args)
    assert counts(core) == frozen
    author.schemas[schema] = selected
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    with pytest.raises((RegistryConflict, ObjectIntegrityError)): run(**args)
    assert counts(core) == frozen
    path.write_bytes(raw)
    original_metadata = support._metadata
    def altered_metadata(*values, **kwargs):
        meta = original_metadata(*values, **kwargs)
        if meta["content_schema_ref"] == schema:
            meta["summary"] = "Changed after first prefix"
        return meta
    with monkeypatch.context() as patch:
        patch.setattr(support, "_metadata", altered_metadata)
        with pytest.raises(RegistryConflict): run(**args)
    assert counts(core) == frozen
    value = run(**args)
    if kind == "analysis":
        assert read_assembly_merge_analysis(core, value.analysis_ref, _registration()).document == value.document
        command = json.loads(core.object_store.path_for_version(value.command_ref.ref.resource_version_id).read_bytes())
        assert command["prepared_analysis"]["metadata"]["content_schema_authority_ref"] == command["schema_authorities"][schema]
    else:
        reopen(core, value)
        assert len(value.plan["prepared_objects"]) == 2
        for spec in value.plan["prepared_materials"]:
            assert spec["metadata"]["content_schema_authority_ref"] == value.plan["schema_authorities"][spec["schema"]]
            assert value.plan["schema_authority_pins"][spec["schema"]]["bytes"] > 0


def test_stale_owner_cannot_analyze_or_publish_and_successor_reopens(fixture):
    core, gateway, _, producer = fixture[:4]
    analysis = analyze(fixture)
    writer = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    before = counts(writer)
    with pytest.raises(StaleWriterError): analyze(fixture, command="stale-analysis")
    with pytest.raises(StaleWriterError): publish(fixture, analysis)
    assert counts(writer) == before
    new_gateway = RegistryRegistrationGateway(writer, gateway._task_ref, gateway._bootstrap_ref)
    author = AssemblyMergeAuthor(new_gateway, _registration(), producer)
    value = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis), generated_continuity="left", command_id="M")
    reopen(writer, value)


def test_final_cut_rereads_right_history_and_exact_restoration_recovers(fixture, monkeypatch):
    core = fixture[0]
    analysis = analyze(fixture)
    author = fixture[11]
    path = core.object_store.path_for_version(fixture[9].revision.plan_ref.ref.resource_version_id)
    raw = path.read_bytes()
    original = author._publish_spec
    def damage_after_lowering(spec):
        result = original(spec)
        if spec["role"] == "lowering_mapping":
            path.write_bytes(raw + b" ")
        return result
    monkeypatch.setattr(author, "_publish_spec", damage_after_lowering)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            publish(fixture, analysis, command="right-final-cut")
        reference = assembly_v6._result_ref(core, author.binding, "right-final-cut", fixture[8].revision.revision_ref)
        assert core.event_store.object_row(reference.ref.version_id) is None
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(author, "_publish_spec", original)
    value = publish(fixture, analysis, command="right-final-cut")
    assert value.revision.revision_ref == reference
    reopen(core, value)


@contextmanager
def changed_resource_metadata(core, reference, change):
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (str(reference.ref.resource_version_id),)).fetchone())
        event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0]
    metadata = json.loads(row["metadata_json"])
    change(metadata)
    # Keep the exact bootstrap publication envelope coherent so the strong
    # input-pin comparison, rather than an inconsistent fixture, must reject.
    for field in ("size", "media_type", "content_schema_ref", "content_schema_authority_ref"):
        metadata["reference_provenance"]["publication"][field] = deepcopy(metadata[field])
    publication = json.loads(event)
    publication["metadata"] = metadata
    with core.event_store.connect() as db:
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps(metadata), row["version_id"]))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(publication), row["published_event_id"]))
    try:
        yield
    finally:
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (row["metadata_json"], row["version_id"]))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (event, row["published_event_id"]))


def test_analysis_first_prefix_pins_genuine_input_metadata_and_equal_schema_authority(fixture, monkeypatch):
    core, gateway = fixture[:2]
    analyzer = fixture[10]
    target = fixture[5][2].revision.element_mapping_ref
    selected = fixture[4].schemas[ELEMENT_SCHEMA]
    raw = core.object_store.path_for_version(selected.resource_version_id).read_bytes()
    alternate = support._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=raw, media_type="application/schema+json",
        content_schema_ref="registry_v1/registry_type_catalog/v1", summary="Equal genuine-input schema alternate",
        lifetime_ref=gateway._bootstrap_ref, descriptors={"host_registration_kind": "schema", "registered_key": ELEMENT_SCHEMA},
        idempotency_key="input-alternate"))
    original = analyzer._publish_spec
    def cut(spec):
        value = original(spec)
        if spec["role"] == "command": raise DurableCut("input-command")
        return value
    monkeypatch.setattr(analyzer, "_publish_spec", cut)
    with pytest.raises(DurableCut): analyze(fixture, command="input-pin")
    monkeypatch.setattr(analyzer, "_publish_spec", original)
    frozen = counts(core)
    ref = assembly_merge._material_ref(core, analyzer.binding, assembly_merge._key("input-pin") + ":analysis")
    assert core.event_store.object_row(ref.ref.resource_version_id) is None
    alternate_dict = {"resource_id": str(alternate.resource_id), "resource_version_id": str(alternate.resource_version_id)}
    for change in (lambda metadata: metadata.update(content_schema_authority_ref=alternate_dict),
                   lambda metadata: metadata.update(summary="Changed input metadata after first command")):
        with changed_resource_metadata(core, target, change):
            from cpn.rpnh.collaboration import validate_closed_revision
            assert validate_closed_revision(core, fixture[5][2].revision.revision_ref, _registration()).revision == fixture[5][2].revision
            with pytest.raises(RegistryConflict): analyze(fixture, command="input-pin")
            assert counts(core) == frozen
            assert core.event_store.object_row(ref.ref.resource_version_id) is None
    value = analyze(fixture, command="input-pin")
    pins = value.document["input_proof_pins"]
    pinned = next(row for row in pins["materials"] if row["resource_ref"] == target.to_dict())
    assert pinned["metadata"]["content_schema_authority_ref"] != alternate_dict
    final = counts(core)
    with changed_resource_metadata(core, target, lambda meta: meta.update(content_schema_authority_ref=alternate_dict)):
        with pytest.raises(RegistryConflict): read_assembly_merge_analysis(core, value.analysis_ref, _registration())
        assert counts(core) == final
    assert read_assembly_merge_analysis(core, value.analysis_ref, _registration()).document == value.document


def test_analysis_final_cut_rechecks_input_before_publishing_analysis(fixture, monkeypatch):
    core = fixture[0]
    analyzer = fixture[10]
    target = fixture[5][2].revision.definition_ref
    path = core.object_store.path_for_version(target.ref.resource_version_id)
    raw = path.read_bytes()
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
    original = analyzer._publish_spec
    def cut(spec):
        value = original(spec)
        if spec["role"] == "command": path.write_bytes(changed)
        return value
    monkeypatch.setattr(analyzer, "_publish_spec", cut)
    try:
        with pytest.raises(RegistryConflict, match="canonical actual"):
            analyze(fixture, command="analysis-final-cut")
        ref = assembly_merge._material_ref(core, analyzer.binding, assembly_merge._key("analysis-final-cut") + ":analysis")
        assert core.event_store.object_row(ref.ref.resource_version_id) is None
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(analyzer, "_publish_spec", original)
    value = analyze(fixture, command="analysis-final-cut")
    assert value.analysis_ref == ref
