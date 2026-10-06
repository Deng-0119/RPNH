"""Total explicit identity accounting and proof-family exclusion controls."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_transform import fixture, split_request, fusion_request, ids_for, fresh
from cpn.rpnh.collaboration import (
    ValidatedClosedRevision, SourceQualifiedVersionRef, validate_closed_revision,
    plain_transform_schema_data, author_material_schema_data, graph_assembly_schema_data,
    plain_transform_assembly_schema_data,
)
from cpn.rpnh.collaboration import plain_transform as implementation
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json, canonical_text


def test_total_partitions_reject_missing_duplicate_wrong_source_kind_and_identity_reuse(fixture):
    inputs, author, _ = fixture
    core, _, _, _, _, base, old = inputs
    original = split_request(base)
    expected = support.counts(core)
    seen = []
    for damage in ("missing_group", "missing_created", "missing_copy", "duplicate_source", "duplicate_target",
                   "duplicate_group", "wrong_kind", "wrong_source", "wrong_revision", "unknown_source",
                   "unknown_target", "overlap_copy", "overlap_created", "retained_target", "retained_source",
                   "wrong_cardinality", "false_removed", "duplicate_ids", "unknown_locator"):
        args = deepcopy(original)
        args["command_id"] = damage
        groups = args["transform_groups"]
        if damage == "missing_group": groups.pop()
        elif damage == "missing_created": args["created_element_ids"] = []
        elif damage == "missing_copy": args["copy_sources"].pop(next(iter(args["copy_sources"])))
        elif damage == "duplicate_source": groups[0]["source_element_ids"] *= 2
        elif damage == "duplicate_target": groups[0]["target_element_ids"] *= 2
        elif damage == "duplicate_group": groups.append(deepcopy(groups[0]))
        elif damage == "wrong_kind":
            left = next(g for g in groups if g["source_element_ids"] == [old["/components/b"]])
            right = next(g for g in groups if g["source_element_ids"] == [old["/components/b/ports/request"]])
            left["source_element_ids"], right["source_element_ids"] = right["source_element_ids"], left["source_element_ids"]
        elif damage == "wrong_source": groups[0]["source_revision_ref"]["source_id"] = "wrong-source"
        elif damage == "wrong_revision": groups[0]["source_revision_ref"]["ref"]["version_id"] = str(new_id("resource_version"))
        elif damage == "unknown_source": groups[0]["source_element_ids"] = [fresh("unknown", "source")]
        elif damage == "unknown_target": groups[0]["target_element_ids"][0] = fresh("unknown", "target")
        elif damage == "overlap_copy": args["copy_sources"][groups[0]["target_element_ids"][0]] = groups[0]["source_element_ids"][0]
        elif damage == "overlap_created": args["created_element_ids"].append(groups[0]["target_element_ids"][0])
        elif damage == "retained_target": groups[0]["target_element_ids"][0] = old["/components/a"]
        elif damage == "retained_source": groups[0]["source_element_ids"] = [old["/components/a"]]
        elif damage == "wrong_cardinality": groups[0]["kind"] = "fusion"
        elif damage == "false_removed": args["removed_element_ids"] = [old["/components/a"]]
        elif damage == "duplicate_ids": args["element_ids"]["/terminal"] = old["/"]
        else: args["element_ids"]["/invented"] = fresh("unknown", "locator")
        for row in groups:
            row["source_element_ids"].sort(); row["target_element_ids"].sort()
        args["transform_groups"] = sorted(groups, key=canonical_text)
        args["created_element_ids"].sort()
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(**args)
        assert support.counts(core) == expected
        seen.append(damage)
    print("TRANSFORM_TOTAL_MAPPING_NEGATIVES=" + json.dumps(seen))


def test_history_freshness_and_explicit_removed_disposition_are_not_inferred(fixture):
    inputs, author, _ = fixture
    core, _, _, _, _, base, old = inputs
    split = author.publish(**split_request(base))
    args = fusion_request(split)
    before = support.counts(core)
    with pytest.raises(ValueError, match="source identity partition"):
        author.publish(**{**args, "removed_element_ids": []})
    assert support.counts(core) == before
    new_id = args["element_ids"]["/components/joined"]
    args["element_ids"]["/components/joined"] = old["/components/b"]
    for group in args["transform_groups"]:
        if group["target_element_ids"] == [new_id]:
            group["target_element_ids"] = [old["/components/b"]]
    args["transform_groups"].sort(key=canonical_text)
    with pytest.raises(ValueError, match="historical identity"):
        author.publish(**args)
    assert support.counts(core) == before


def test_legacy_author_reader_and_merge_cannot_erase_or_admit_transform_proof(fixture):
    inputs, author, _ = fixture
    core, _, registration, ordinary, analyzer, base, _ = inputs
    split = author.publish(**split_request(base))
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="legacy author cannot erase"):
        ordinary.publish(module=split.module, element_ids=ids_for(split), parent_ref=split.revision.revision_ref, command_id="legacy-child")
    with pytest.raises(RegistryConflict, match="legacy merge cannot consume"):
        analyzer.analyze(local_ref=split.revision.revision_ref, incoming_ref=base.revision.revision_ref,
                         base_ref=base.revision.revision_ref, command_id="legacy-merge")
    assert support.counts(core) == before
    # A canonical low-level descriptor is not authority to discard its actual parent's family.
    ref = SourceQualifiedVersionRef("plain-source", VersionRef("collaboration_net_revision/v1",
        base.revision.revision_ref.ref.entity_id, new_id("resource_version")))
    record = replace(base.revision, revision_ref=ref, parent_revision_refs=(split.revision.revision_ref,), command_id="raw-legacy-child")
    core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key="raw-legacy-child")
    with pytest.raises(RegistryConflict, match="legacy author cannot erase"):
        validate_closed_revision(core, ref, registration)
    with pytest.raises(RegistryConflict, match="legacy author cannot erase"):
        author.publish(**{**fusion_request(split), "parent_ref": ref})


def test_transform_catalog_is_explicit_and_preserves_existing_bytes():
    old, old_types, old_paths = author_material_schema_data()
    schemas, types, paths = plain_transform_schema_data()
    assert set(schemas) - set(old) == {implementation.MAP_SCHEMA, implementation.COMMAND_SCHEMA}
    assert types == old_types
    assert all(canonical_json(schemas[k]) == canonical_json(v) and paths[k].read_bytes() == old_paths[k].read_bytes() for k, v in old.items())
    combined, combined_types, _ = plain_transform_assembly_schema_data()
    assembly, assembly_types, _ = graph_assembly_schema_data()
    assert set(combined) == set(schemas) | set(assembly)
    assert all(canonical_json(combined[k]) == canonical_json(v) for k, v in assembly.items())
    assert set(combined_types) == set(types) | set(assembly_types)


def test_legacy_catalog_cannot_read_transform_materials(fixture):
    from cpn.rpnh.registry._registry import _RegistryCore
    inputs, author, _ = fixture
    split = author.publish(**split_request(inputs[5]))
    schemas, types, paths = author_material_schema_data()
    reader = _RegistryCore(inputs[0].run_dir, create=False, read_only=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    with pytest.raises((RegistryConflict, SchemaGovernanceError, ValueError, KeyError)):
        validate_closed_revision(reader, split.revision.revision_ref, support.registration())


def test_opaque_current_or_source_module_cannot_enter_plain_transform(fixture):
    from cpn.rpnh.module import ModuleDeclaration
    inputs, author, _ = fixture
    core, _, _, ordinary, _, base, ids = inputs
    args = split_request(base)
    doc = args["module"].to_dict(); doc["designer_constraints"] = {"caller_opaque_contract": True}
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="ordinary unconstrained plain"):
        author.publish(**{**args, "module": ModuleDeclaration.from_dict(doc)})
    assert support.counts(core) == before
    doc = base.module.to_dict(); doc["designer_constraints"] = {"caller_opaque_contract": True}
    parent = ordinary.publish(module=ModuleDeclaration.from_dict(doc), element_ids=ids,
        parent_ref=base.revision.revision_ref, command_id="opaque-parent")
    before = support.counts(core)
    with pytest.raises(RegistryConflict, match="ordinary unconstrained plain"):
        author.publish(**{**args, "parent_ref": parent.revision.revision_ref})
    assert support.counts(core) == before


def test_merge_family_hidden_in_ordinary_parent_history_is_rejected(tmp_path, monkeypatch):
    from test_collaboration_plain_transform import catalog_data
    from cpn.rpnh.collaboration import PlainModuleTransformAuthor, PlainModuleMergeAuthor, plain_merge_result_schema_data
    from cpn.rpnh.module import ModuleDeclaration
    def mixed_catalog():
        schemas, types, paths = catalog_data()
        added, more_types, added_paths = plain_merge_result_schema_data()
        for key, value in added.items():
            assert key not in schemas or canonical_json(schemas[key]) == canonical_json(value)
            schemas[key], paths[key] = value, added_paths[key]
        by_name = {t.name: t for t in types}
        for t in more_types:
            assert t.name not in by_name or by_name[t.name] == t
            by_name[t.name] = t
        return schemas, tuple(by_name.values()), paths
    monkeypatch.setattr(support, "plain_merge_schema_data", mixed_catalog)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    core, gateway, registration, ordinary, analyzer, base, ids = inputs
    merge_author = PlainModuleMergeAuthor(gateway, registration, ordinary.producer)
    author = PlainModuleTransformAuthor(gateway, registration, ordinary.producer)
    doc = base.module.to_dict(); doc["name"] = "LeftName"
    left = ordinary.publish(module=ModuleDeclaration.from_dict(doc), element_ids=ids,
        parent_ref=base.revision.revision_ref, command_id="left")
    doc = base.module.to_dict(); doc["components"][1]["operations"][0]["config"] = {"selected": "right"}
    right = ordinary.publish(module=ModuleDeclaration.from_dict(doc), element_ids=ids,
        parent_ref=base.revision.revision_ref, command_id="right")
    analysis = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=base.revision.revision_ref, command_id="analysis")
    merged = merge_author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge")
    child = ordinary.publish(module=merged.module, element_ids=ids_for(merged),
        parent_ref=merged.revision.revision_ref, command_id="ordinary-after-merge")
    before = support.counts(core)
    for parent in (merged, child):
        with pytest.raises(RegistryConflict, match="excludes other author proof families"):
            author.publish(**split_request(parent))
    assert support.counts(core) == before


def test_copy_edges_can_reference_transformed_and_removed_sources_without_hiding_dispositions(fixture):
    from cpn.rpnh.module import ModuleDeclaration
    inputs, author, _ = fixture
    base, original_ids = inputs[5:]
    args = split_request(base)
    for path, identity in args["element_ids"].items():
        if path.startswith("/components/copied"):
            args["copy_sources"][identity] = original_ids[path.replace("/components/copied", "/components/b")]
    split = author.publish(**args)
    transformed_sources = {i for g in split.transformation["transform_groups"] for i in g["source_element_ids"]}
    assert set(split.transformation["copy_sources"].values()) & transformed_sources == transformed_sources
    next_args = fusion_request(split)
    source_ids = ids_for(split)
    document = next_args["module"].to_dict()
    copied = deepcopy(split.module.to_dict()["components"][-1]); copied["name"] = "anothercopy"
    document["components"].append(copied)
    document["entry"]["anothercopy"] = {"component": "anothercopy", "port": "request"}
    next_args["module"] = ModuleDeclaration.from_dict(document)
    for path, source in source_ids.items():
        if path.startswith("/components/copied") or path == "/entry/copy":
            target_path = path.replace("/components/copied", "/components/anothercopy").replace("/entry/copy", "/entry/anothercopy")
            target = fresh("fusion-copy", target_path)
            next_args["element_ids"][target_path] = target
            next_args["copy_sources"][target] = source
    fused = author.publish(**next_args)
    assert set(fused.transformation["copy_sources"].values()) <= set(fused.transformation["removed_element_ids"])
    assert len(fused.transformation["copy_sources"]) == 5
    assert len(fused.transformation["removed_element_ids"]) == 6
