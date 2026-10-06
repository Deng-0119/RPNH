"""Canonical false claims, full-history cycles and closed legacy eligibility."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.rpnh.collaboration import (SourceQualifiedVersionRef, AssemblyMemberV2, AssemblyCompletion,
    assembly_merge_schema_data, graph_assembly_schema_data, nested_assembly_schema_data, open_region_assembly_schema_data,
    validate_assembly_revision, validate_closed_revision, read_assembly_merge_analysis)
from cpn.rpnh.collaboration import assembly_v6, assembly_merge
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_assembly_merge import fixture, analyze, choices, publish, reopen, request, _registration, A, B
from test_collaboration_assembly_v2_publication import counts


def test_explicit_v6_inventory_preserves_every_shared_legacy_contract():
    schemas, types, _ = assembly_merge_schema_data()
    old, old_types, _ = graph_assembly_schema_data()
    assert all(canonical_json(schemas[key]) == canonical_json(value) for key, value in old.items())
    assert {item.name: item for item in types}.keys() >= {item.name: item for item in old_types}.keys()
    assert "collaboration_assembly_revision/v6" not in {item.name for item in old_types}
    assert "collaboration_assembly_revision/v5" not in {item.name for item in types}
    for catalog in (nested_assembly_schema_data, open_region_assembly_schema_data):
        legacy, legacy_types, _ = catalog()
        assert "collaboration_assembly_revision/v6" not in {item.name for item in legacy_types}
        assert all(canonical_json(legacy[key]) == canonical_json(schemas[key]) for key in set(legacy) & set(schemas))


@contextmanager
def canonical_descriptor(core, reference, document):
    path = core.object_store.path_for_version(reference.ref.version_id)
    raw = path.read_bytes()
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (str(reference.ref.version_id),)).fetchone())
        event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0]
    changed = canonical_json(document)
    publication = json.loads(event)
    publication["metadata"], publication["size"] = document, len(changed)
    path.write_bytes(changed)
    with core.event_store.connect() as db:
        db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (json.dumps(document), len(changed), row["version_id"]))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(publication), row["published_event_id"]))
    try:
        yield
    finally:
        path.write_bytes(raw)
        with core.event_store.connect() as db:
            db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (row["metadata_json"], row["size"], row["version_id"]))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (event, row["published_event_id"]))


def test_canonical_descriptor_counterfeits_do_not_discard_right_history(fixture):
    core = fixture[0]
    value = publish(fixture, analyze(fixture))
    before = counts(core)
    for change in ("parents", "command", "material", "producer"):
        doc = deepcopy(value.revision.to_dict())
        if change == "parents": doc["parent_revision_refs"].reverse()
        elif change == "command": doc["command_id"] = "canonical-false-command"
        elif change == "material": doc["compiled_inventory_ref"] = fixture[8].revision.compiled_inventory_ref.to_dict()
        else: doc["producer_principal_ref"]["source_id"] = "foreign"
        with canonical_descriptor(core, value.revision.revision_ref, doc):
            with pytest.raises((RegistryConflict, ValueError)):
                validate_assembly_revision(core, value.revision.revision_ref, _registration())
            assert counts(core) == before
        reopen(core, value)


def test_canonical_cycle_and_counterfeit_source_history_are_rejected(fixture):
    core, _, _, _, _, _, _, base, left, right, analyzer, _ = fixture
    base_ref = base.revision.revision_ref
    refs = tuple(SourceQualifiedVersionRef(base_ref.source_id, VersionRef(base_ref.ref.entity_type,
        base_ref.ref.entity_id, new_id("resource_version"))) for _ in range(2))
    for index, ref in enumerate(refs):
        record = replace(base.revision, revision_ref=ref, parent_revision_ref=refs[1-index], command_id="cycle:" + str(index))
        core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
            payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
            schema_ref="registry_v1/collaboration_assembly_revision/v2", idempotency_key="cycle:" + str(index))
    before = counts(core)
    with pytest.raises(RegistryConflict, match="cycle"):
        analyzer.analyze(local_ref=refs[0], incoming_ref=right.revision.revision_ref, command_id="cycle-analysis")
    assert counts(core) == before
    fake_ref = SourceQualifiedVersionRef(base_ref.source_id, VersionRef(base_ref.ref.entity_type, base_ref.ref.entity_id, new_id("resource_version")))
    fake = replace(left.revision, revision_ref=fake_ref)
    core.publish_bytes(object_type=fake_ref.ref.entity_type, logical_id=fake_ref.ref.entity_id, version_id=fake_ref.ref.version_id,
        payload=canonical_json(fake.to_dict()), metadata=fake.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_assembly_revision/v2", idempotency_key="copied-source")
    before = counts(core)
    with pytest.raises(RegistryConflict):
        analyzer.analyze(local_ref=fake_ref, incoming_ref=right.revision.revision_ref, command_id="counterfeit-analysis")
    assert counts(core) == before


def test_every_saved_command_and_material_requires_canonical_actual_bytes(fixture):
    core = fixture[0]
    analysis = analyze(fixture)
    value = publish(fixture, analysis)
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    references = [analysis.analysis_ref, analysis.command_ref, value.revision.plan_ref] + [
        SourceQualifiedResourceRef.from_dict(row["resource_ref"], catalog=core.catalog) for row in value.plan["prepared_materials"]]
    before = counts(core)
    for reference in references:
        path = core.object_store.path_for_version(reference.ref.resource_version_id)
        raw = path.read_bytes()
        changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
        try:
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                validate_assembly_revision(core, value.revision.revision_ref, _registration())
            assert counts(core) == before
        finally:
            path.write_bytes(raw)
    reopen(core, value)


def test_output_and_genuine_input_descriptors_and_materials_require_canonical_bytes(fixture):
    core = fixture[0]
    value = publish(fixture, analyze(fixture))
    probes = [("merge_descriptor", value.revision.revision_ref.ref.version_id),
        ("generated_descriptor", value.revision.generated_revision_ref.ref.version_id),
        ("right_descriptor", fixture[9].revision.revision_ref.ref.version_id),
        ("right_plan", fixture[9].revision.plan_ref.ref.resource_version_id),
        ("right_leaf_descriptor", fixture[5][2].revision.revision_ref.ref.version_id),
        ("right_leaf_definition", fixture[5][2].revision.definition_ref.ref.resource_version_id)]
    before = counts(core)
    for role, version in probes:
        path = core.object_store.path_for_version(version)
        raw = path.read_bytes()
        altered = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
        assert len(raw) == len(altered) and raw != altered and canonical_json(json.loads(raw)) == canonical_json(json.loads(altered))
        path.write_bytes(altered)
        try:
            with pytest.raises(RegistryConflict, match="canonical actual"):
                validate_assembly_revision(core, value.revision.revision_ref, _registration())
            assert counts(core) == before
            print("ASSEMBLY_V6_CANONICAL_SOURCE_REFUSAL=" + role, flush=True)
        finally:
            path.write_bytes(raw)
    reopen(core, value)


def test_actual_mixed_shared_buckets_cannot_publish_a_resolved_plan(fixture):
    core, _, _, _, leaf_author, leaves, v2, base, _, _, _, _ = fixture
    variants = []
    ids = {row["locator"]: row["element_id"] for row in leaves[0].element_map["elements"]}
    from cpn.rpnh.module import ModuleDeclaration
    for limit in (4, 5):
        doc = leaves[0].module.to_dict()
        doc["budget_buckets"][0]["max_attempts"] = limit
        variants.append(leaf_author.publish(module=ModuleDeclaration.from_dict(doc), element_ids=ids,
            parent_ref=leaves[0].revision.revision_ref, command_id="budget:" + str(limit)))
    left = v2.publish(**request(variants[0], variants[0], command="budget-L", parent=base))
    right = v2.publish(**request(variants[1], variants[1], command="budget-R", parent=base))
    analysis = analyze(fixture, left=left, right=right, command="budget-analysis")
    decisions = choices(analysis)
    next(row for row in decisions if row["subject"] == "member/" + B)["choice"] = "right"
    before = counts(core)
    with pytest.raises(ValueError, match="shared_exact conflicting"):
        publish(fixture, analysis, decisions, "incompatible-budget")
    assert counts(core) == before
    value = publish(fixture, analysis, choices(analysis), "compatible-budget")
    assert value.generated.module.budget_buckets[0].max_attempts == 4
    reopen(core, value)


def test_old_member_resolver_rejects_v6_and_generated_opaque_plain(fixture):
    core = fixture[0]
    value = publish(fixture, analyze(fixture))
    with pytest.raises(ValueError): AssemblyMemberV2(A, "New result", value.revision.revision_ref)
    g = validate_closed_revision(core, value.revision.generated_revision_ref, _registration())
    terminal = next(row["element_id"] for row in g.element_map["elements"] if row["locator"] == "/terminal")
    before = counts(core)
    with pytest.raises(ValueError, match="plain v1"):
        fixture[6].publish(name="Opaque", members=[AssemblyMemberV2(A, "Generated", g.revision.revision_ref)], connections=[],
            completion=AssemblyCompletion(A, terminal), budget_policy="shared_exact", deployment_intent="same_run_candidate", command_id="launder")
    assert counts(core) == before


def test_version_versus_member_plan_conflict_checks_actual_consumers_and_completion(fixture):
    from cpn.rpnh.collaboration import AssemblyConnection
    core, _, _, _, _, leaves, v2, base, _, right, _, _ = fixture
    third = "member:" + "c" * 32
    args = request(leaves[0], leaves[0], command="member-plan-left", parent=base)
    args["members"] += (AssemblyMemberV2(third, "Same", leaves[0].revision.revision_ref),)
    left = v2.publish(**args)
    analysis = analyze(fixture, left=left, right=right, command="version-plan")
    assert next(row for row in analysis.document["atoms"] if row["subject"] == "member/" + A)["conflict"]
    assert next(row for row in analysis.document["atoms"] if row["subject"] == "member/" + third)["conflict"]
    ids = {row["locator"]: row["element_id"] for row in leaves[0].element_map["elements"]}
    decisions = choices(analysis)
    connections = [args["connections"][0].to_dict(), AssemblyConnection(third, ids["/exit/result"], B, ids["/entry/request"]).to_dict()]
    next(row for row in decisions if row["subject"] == "connections").update(choice="exact", value=sorted(connections, key=lambda row: canonical_json(row)))
    before = counts(core)
    with pytest.raises(ValueError, match="multiple producers"):
        publish(fixture, analysis, decisions, "two-consumers")
    assert counts(core) == before
    decisions = choices(analysis)
    next(row for row in decisions if row["subject"] == "completion").update(choice="exact", value=AssemblyCompletion(B, ids["/entry/request"]).to_dict())
    with pytest.raises(ValueError, match="primary terminal"):
        publish(fixture, analysis, decisions, "bad-terminal")
    assert counts(core) == before
    value = publish(fixture, analysis, choices(analysis), "explicit-plan")
    assert set(value.members) == {A, B, third}
    assert len(value.generated.module.links) == 1
    reopen(core, value)


def test_unilateral_members_and_canonical_order_are_not_silently_lost(fixture):
    core, _, _, _, _, leaves, v2, base, _, _, _, _ = fixture
    third = "member:" + "c" * 32
    args = request(leaves[0], leaves[0], command="name-left", parent=base)
    args["name"] = "LocalName"
    left = v2.publish(**args)
    args = request(leaves[0], leaves[0], command="member-right", parent=base)
    args["members"] = (AssemblyMemberV2(third, "Same", leaves[0].revision.revision_ref), *reversed(args["members"]))
    right = v2.publish(**args)
    analysis = analyze(fixture, left=left, right=right, command="unilateral")
    assert not any(row["conflict"] for row in analysis.document["atoms"])
    value = publish(fixture, analysis, [], "unilateral-M")
    assert value.generated.module.name == "LocalName"
    assert list(value.members) == [A, B, third]
    assert value.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    reopen(core, value)


def test_genuine_graph_members_keep_source_rebuild_and_complete_origins(tmp_path, monkeypatch):
    from cpn.rpnh.collaboration import GraphModuleAuthor, AssemblyMergeAnalyzer, AssemblyMergeAuthor
    import test_collaboration_assembly_v2_publication as old
    from test_collaboration_graph_materials import registration
    from test_collaboration_graph_source import recipe
    monkeypatch.setattr(old, "graph_assembly_schema_data", assembly_merge_schema_data)
    core, gateway, v2, selected, first = old.fixture.__wrapped__(tmp_path)
    graph_author = GraphModuleAuthor(gateway, selected, v2.producer)
    ids = {row["locator"]: row["element_id"] for row in first.source_map["elements"]}
    leaves = [first]
    for side in ("left", "right"):
        source = deepcopy(first.source)
        source["graph"]["nodes"][0]["instruction"] = "Explicit " + side + " graph revision"
        leaves.append(graph_author.publish(source=source, recipe=recipe(), source_ids=ids,
            parent_ref=first.revision.revision_ref, command_id="graph:" + side))
    base = v2.publish(**old.request(first, command_id="graph-B"))
    histories = []
    for side, leaf in zip(("left", "right"), leaves[1:]):
        args = old.request(leaf, command_id="graph-" + side, parent_ref=base.revision.revision_ref)
        histories.append(v2.publish(**args))
    analysis = AssemblyMergeAnalyzer(gateway, selected, v2.producer).analyze(local_ref=histories[0].revision.revision_ref,
        incoming_ref=histories[1].revision.revision_ref, command_id="graph-analysis")
    value = AssemblyMergeAuthor(gateway, selected, v2.producer).publish(analysis_ref=analysis.analysis_ref,
        choices=choices(analysis, "right"), generated_continuity="left", command_id="graph-M")
    assert all(member.source == leaves[2].source for member in value.members.values())
    projection = value.lowering_map["projection"]
    assert len(projection["graph_source_origins"]) == 2 * len(leaves[2].source_map["elements"])
    assert len(projection["graph_fragment_coverage"]) == 2
    from cpn.rpnh.registry._registry import _RegistryCore
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert validate_assembly_revision(reader, value.revision.revision_ref, registration()).revision == value.revision
