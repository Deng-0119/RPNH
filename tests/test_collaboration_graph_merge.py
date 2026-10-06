"""Real ordinary graph B/L/R analysis, explicit decisions, M and E material proof."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.rpnh.collaboration import (
    GraphModuleAuthor, GraphMergeAuthor, GraphMergeAnalyzer, ValidatedGraphMergeRevision,
    SourceQualifiedVersionRef, graph_merge_assembly_schema_data, make_graph_source,
    read_graph_merge_analysis, validate_closed_revision,
)
from cpn.rpnh.collaboration.graph_source import graph_source_elements, rebuild_graph_module
from cpn.rpnh.collaboration._graph_merge_model import normalize, reconstruct, compare, resolve, same, state, UnresolvedGraphMerge
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from test_collaboration_graph_materials import registration, assert_author_only
from test_collaboration_graph_source import graph_wire, recipe, source_ids


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = graph_merge_assembly_schema_data()
    core = _RegistryCore(tmp_path / "graph-merge", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("graph-merge-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-graph-merge", command_id="fixture:bind")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id), "display_name": "Graph merge fixture"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="fixture:principal")
    selected = registration()
    producer = SourceQualifiedVersionRef("source-graph-merge", principal)
    legacy = GraphModuleAuthor(gateway, selected, producer)
    analyzer, author = GraphMergeAnalyzer(gateway, selected, producer), GraphMergeAuthor(gateway, selected, producer)
    source = make_graph_source(graph_wire(True)); ids = source_ids(source)
    base = legacy.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:base")
    return core, gateway, selected, legacy, analyzer, author, base, ids


def counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events()), len(core.event_store.outbox_rows())


def rename_source(source, ids, old="draft", new="writer"):
    source = deepcopy(source)
    for node in source["graph"]["nodes"]:
        if node["node_id"] == old: node["node_id"] = new
    for arc in source["graph"]["arcs"]:
        for field in ("source", "target"):
            if arc[field]["node_id"] == old: arc[field]["node_id"] = new
    for field in ("ingress", "egress"):
        if source["graph"][field]["node_id"] == old: source["graph"][field]["node_id"] = new
    return source, {key.replace("/nodes/" + old, "/nodes/" + new): value for key, value in ids.items()}


def make_sides(values):
    _, _, _, legacy, _, _, base, ids = values
    local, local_ids = rename_source(base.source, ids)
    incoming = deepcopy(base.source)
    incoming["graph"]["nodes"][0]["instruction"] = "Preserve the explicit incoming instruction."
    left = legacy.publish(source=local, recipe=recipe(), source_ids=local_ids,
        command_id="graph:left", parent_ref=base.revision.revision_ref)
    right = legacy.publish(source=incoming, recipe=recipe(max_attempts_per_node=None), source_ids=ids,
        command_id="graph:right", parent_ref=base.revision.revision_ref)
    return left, right


def choices(analysis, preferred="incoming"):
    models = {key: analysis.document["normalized"][key]["atoms"] for key in ("base", "local", "incoming")}
    def choose(key):
        b, l, r = (state(models[side], key) for side in ("base", "local", "incoming"))
        return "incoming" if same(l, b) else "local" if same(r, b) else preferred
    return [{"conflict_id": row["conflict_id"], "reason": "Caller explicitly combines the selected source and recipe states.",
        "selections": [{"subject": key, "side": choose(key)} for key in row["subjects"]]}
        for row in analysis.document["conflicts"]]


def merged(values, command="graph:merge"):
    left, right = make_sides(values)
    analysis = values[4].analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="graph:analysis")
    result = values[5].publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis), command_id=command)
    return left, right, analysis, result


def test_real_source_recipe_three_way_merge_and_exact_readonly_replay(fixture):
    core, _, _, _, _, author, base, ids = fixture
    left, right, analysis, result = merged(fixture)
    assert isinstance(result, ValidatedGraphMergeRevision)
    assert result.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert analysis.document["base_revision_ref"] == base.revision.revision_ref.to_dict()
    assert len(analysis.document["ancestry"]) == 3
    assert result.source["graph"]["nodes"][0]["node_id"] == "writer"
    assert result.source["graph"]["nodes"][0]["instruction"] == right.source["graph"]["nodes"][0]["instruction"]
    assert result.recipe["max_attempts_per_node"] is None
    assert result.source != left.source and result.source != right.source
    assert result.recipe == right.recipe
    assert result.module.to_dict() == rebuild_graph_module(result.source, result.recipe).to_dict()
    assert set(result.identity_origins) == set(ids.values())
    assert all(origin["revision_ref"] == base.revision.revision_ref.to_dict() for origin in result.identity_origins.values())
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    loaded = validate_closed_revision(reader, result.revision.revision_ref, registration())
    assert loaded == result
    assert read_graph_merge_analysis(reader, analysis.analysis_ref, registration()).document == analysis.document
    assert author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis), command_id="graph:merge").revision == result.revision
    assert counts(core) == before
    assert_author_only(core)


def test_complete_nontrivial_histories_select_true_nearest_base(fixture):
    core, _, _, legacy, analyzer, author, first, ids = fixture
    source = deepcopy(first.source); source["graph"]["nodes"][1]["instruction"] = "New shared base."
    base = legacy.publish(source=source, recipe=recipe(), source_ids=ids, command_id="history:base", parent_ref=first.revision.revision_ref)
    left, right = make_sides((*fixture[:6], base, ids))
    local = deepcopy(left.source); local["graph"]["max_rework_cycles"] = 3
    local_ids = {r["locator"]:r["element_id"] for r in left.source_map["elements"]}
    left2 = legacy.publish(source=local, recipe=recipe(), source_ids=local_ids, command_id="history:left2", parent_ref=left.revision.revision_ref)
    incoming = deepcopy(right.source); incoming["graph"]["nodes"][1]["instruction"] = "Actual right descendant."
    right2 = legacy.publish(source=incoming, recipe=recipe(max_attempts_per_node=None), source_ids=ids, command_id="history:right2", parent_ref=right.revision.revision_ref)
    analysis = analyzer.analyze(local_ref=left2.revision.revision_ref, incoming_ref=right2.revision.revision_ref,
        base_ref=base.revision.revision_ref, command_id="history:analysis")
    assert len(analysis.document["ancestry"]) == 6
    assert analysis.document["base_revision_ref"] == base.revision.revision_ref.to_dict()
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis), command_id="history:merge")
    assert result.source["graph"]["max_rework_cycles"] == 3
    assert result.source["graph"]["nodes"][1]["instruction"] == incoming["graph"]["nodes"][1]["instruction"]
    before = counts(core)
    with pytest.raises(ValueError, match="nearest"):
        analyzer.analyze(local_ref=left2.revision.revision_ref, incoming_ref=right2.revision.revision_ref,
            base_ref=first.revision.revision_ref, command_id="history:wrong-base")
    assert counts(core) == before


def test_explicit_conflict_choices_never_guess_or_accept_stale_decisions(fixture):
    core, _, _, legacy, analyzer, author, base, ids = fixture
    variants = []
    for side, limit in (("local", 2), ("incoming", None)):
        source = deepcopy(base.source); source["graph"]["nodes"][0]["instruction"] = side
        variants.append(legacy.publish(source=source, recipe=recipe(max_attempts_per_node=limit), source_ids=ids,
            parent_ref=base.revision.revision_ref, command_id="conflict:" + side))
    analysis = analyzer.analyze(local_ref=variants[0].revision.revision_ref, incoming_ref=variants[1].revision.revision_ref, command_id="conflict:analysis")
    decisions = choices(analysis)
    assert any(row["reason"] == "divergent_atom" for row in analysis.document["conflicts"])
    bad = [[], decisions + decisions[:1]]
    row = deepcopy(decisions); row[0]["reason"] = "  "; bad.append(row)
    row = deepcopy(decisions); row[0]["conflict_id"] = "conflict:" + "0" * 64; bad.append(row)
    row = deepcopy(decisions); row[0]["selections"] = []; bad.append(row)
    row = deepcopy(decisions); row[0]["selections"][0]["side"] = "guess"; bad.append(row)
    before = counts(core)
    for index, candidate in enumerate(bad):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(analysis_ref=analysis.analysis_ref, choices=candidate, command_id="invalid:" + str(index))
        assert counts(core) == before
        print("GRAPH_DECISION_REJECT", index)
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=decisions, command_id="conflict:merge")
    assert result.source["graph"]["nodes"][0]["instruction"] == "incoming"
    assert result.recipe["max_attempts_per_node"] is None


def test_real_crisscross_nearest_bases_and_unrelated_are_saved_unresolved(fixture):
    core, _, _, legacy, analyzer, author, base, ids = fixture
    left, right = make_sides(fixture)
    first = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="cross:analysis")
    m1 = author.publish(analysis_ref=first.analysis_ref, choices=choices(first, "local"), command_id="cross:m1")
    m2 = author.publish(analysis_ref=first.analysis_ref, choices=choices(first, "incoming"), command_id="cross:m2")
    crossed = analyzer.analyze(local_ref=m1.revision.revision_ref, incoming_ref=m2.revision.revision_ref, command_id="cross:multiple")
    assert crossed.document["status"] == "multiple_bases"
    assert {canonical_json(x) for x in crossed.document["nearest_common_bases"]} == {canonical_json(x.revision.revision_ref.to_dict()) for x in (left, right)}
    assert crossed.document["normalized"] == {}
    other = legacy.publish(source=base.source, recipe=recipe(), source_ids=source_ids(base.source), command_id="cross:unrelated-root")
    unrelated = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=other.revision.revision_ref, command_id="cross:unrelated")
    assert unrelated.document["status"] == "unrelated_histories"
    for case in (crossed, unrelated):
        before = counts(core)
        with pytest.raises(UnresolvedGraphMerge, match="unique"):
            author.publish(analysis_ref=case.analysis_ref, choices=[], command_id="cross:forbidden:" + case.document["status"])
        assert counts(core) == before
    identical = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=left.revision.revision_ref,
        command_id="cross:identical-heads")
    assert identical.document["base_revision_ref"] == left.revision.revision_ref.to_dict()
    before = counts(core)
    with pytest.raises(UnresolvedGraphMerge, match="distinct"):
        author.publish(analysis_ref=identical.analysis_ref, choices=[], command_id="cross:not-two-parents")
    assert counts(core) == before


def test_real_descendant_edit_rename_copy_and_origin_retention(fixture):
    core, _, _, legacy, _, author, _, _ = fixture
    _, _, _, merged_value = merged(fixture)
    ids = {row["locator"]:row["element_id"] for row in merged_value.source_map["elements"]}
    source, renamed = rename_source(merged_value.source, ids, "writer", "writer2")
    edited = author.publish_edit(source=source, recipe=merged_value.recipe, source_ids=renamed,
        parent_ref=merged_value.revision.revision_ref, command_id="edit:rename")
    assert edited.revision.parent_revision_refs == (merged_value.revision.revision_ref,)
    assert edited.identity_origins == merged_value.identity_origins
    fresh = source_ids(source)
    copied = author.publish_edit(source=source, recipe=recipe(), source_ids=fresh,
        copy_sources={fresh[key]:renamed[key] for key in fresh}, parent_ref=edited.revision.revision_ref, command_id="edit:copy")
    assert all(row["copied_from"]["revision_ref"] == edited.revision.revision_ref.to_dict() for row in copied.source_map["elements"])
    assert all(origin["revision_ref"] == copied.revision.revision_ref.to_dict() for origin in copied.identity_origins.values())
    assert validate_closed_revision(core, copied.revision.revision_ref, registration()) == copied
    before = counts(core)
    with pytest.raises(ValueError, match="v2"):
        legacy.publish(source=source, recipe=recipe(), source_ids=renamed, parent_ref=edited.revision.revision_ref, command_id="old:reject")
    assert counts(core) == before


def test_lossless_graph_atoms_cover_array_order_feedback_and_typed_recipe(fixture):
    _, _, _, _, _, _, base, ids = fixture
    origins = {identity:{"revision_ref":base.revision.revision_ref.to_dict(),"element_id":identity,"copied_from":None} for identity in ids.values()}
    normalized = normalize(base, origins)
    source, options, actual, result_origins = reconstruct(normalized["atoms"])
    assert source == base.source and options == base.recipe and actual == ids and result_origins == origins
    alternate = deepcopy(base.source); alternate["graph"]["nodes"].reverse(); alternate["graph"]["arcs"].reverse()
    from types import SimpleNamespace
    next_model = normalize(SimpleNamespace(source=alternate, recipe=base.recipe, source_map=base.source_map), origins)
    assert next_model != normalized
    assert reconstruct(next_model["atoms"])[0] == alternate
    for value in (True, 1.0, 0, -1):
        bad = deepcopy(normalized["atoms"]);bad["recipe/complete"]["max_attempts_per_node"] = value
        with pytest.raises(ValueError): reconstruct(bad)
    for field in ("max_attempts_per_node", "declaration_refs"):
        bad = deepcopy(normalized["atoms"]);del bad["recipe/complete"][field]
        with pytest.raises(ValueError): reconstruct(bad)
    for locator in ("/ingress", "/arcs/draft_to_review"):
        bad = deepcopy(normalized["atoms"]);identity = ids[locator]
        key = "endpoint" if locator == "/ingress" else "target"
        bad[identity + "/value"][key] = ids["/nodes/review/output_ports/result"]
        with pytest.raises(UnresolvedGraphMerge): reconstruct(bad)


def test_exact_source_identity_introduction_collision_and_incomplete_order(fixture):
    _, _, _, _, _, _, base, ids = fixture
    origins = {identity:{"revision_ref":base.revision.revision_ref.to_dict(),"element_id":identity,"copied_from":None} for identity in ids.values()}
    model = normalize(base, origins)
    local, incoming = deepcopy(model), deepcopy(model)
    identity = ids["/nodes/draft"]
    incoming["atoms"][identity + "/origin"]["revision_ref"]["ref"]["version_id"] = str(new_id("resource_version"))
    differences, conflicts = compare(model, local, incoming)
    assert any(row["reason"] == "identity_origin_conflict" for row in conflicts)
    # A selected membership list cannot silently discard live authored elements.
    bad = deepcopy(model["atoms"]); bad[ids["/"] + "/nodes_order"] = [ids["/nodes/draft"]]
    with pytest.raises(UnresolvedGraphMerge, match="membership"): reconstruct(bad)


def test_invalid_nonhead_ancestor_cycle_missing_owner_and_source_fail(fixture):
    core, _, _, legacy, analyzer, _, base, _ = fixture
    left, right = make_sides(fixture)
    payload_path = core.object_store.path_for_version(base.revision.graph_recipe_ref.ref.resource_version_id)
    original = payload_path.read_bytes(); before = counts(core)
    payload_path.write_bytes(b"{}")
    try:
        with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
            analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="bad:ancestor")
        assert counts(core) == before
    finally: payload_path.write_bytes(original)
    missing = SourceQualifiedVersionRef(left.revision.revision_ref.source_id, VersionRef("collaboration_net_revision/v2",
        left.revision.revision_ref.ref.entity_id, new_id("resource_version")))
    for ref in (missing, replace(left.revision.revision_ref, source_id="other-source")):
        with pytest.raises((ValueError, RegistryConflict)):
            analyzer.analyze(local_ref=ref, incoming_ref=right.revision.revision_ref, command_id="bad:missing-or-source")
    refs = [replace(missing, ref=replace(missing.ref, version_id=new_id("resource_version"))) for _ in range(2)]
    for i, reference in enumerate(refs):
        record = replace(base.revision, revision_ref=reference, command_id="bad:cycle:" + str(i), parent_revision_refs=(refs[1-i],))
        document = record.to_dict()
        core.publish_bytes(object_type=reference.ref.entity_type, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json", schema_ref=document["schema_version"], idempotency_key=record.command_id)
    before = counts(core)
    with pytest.raises(RegistryConflict, match="cycle"):
        analyzer.analyze(local_ref=refs[0], incoming_ref=right.revision.revision_ref, command_id="bad:cycle-analysis")
    assert counts(core) == before


def test_real_feedback_delete_then_reintroduction_keeps_distinct_origin(fixture):
    core, _, _, legacy, analyzer, author, base, ids = fixture
    removed = deepcopy(base.source)
    removed["graph"]["arcs"] = [row for row in removed["graph"]["arcs"] if row["kind"] != "feedback"]
    removed["graph"]["nodes"][0]["input_ports"] = [p for p in removed["graph"]["nodes"][0]["input_ports"] if p["port_id"] != "changes"]
    removed["graph"]["nodes"][1]["output_ports"] = [p for p in removed["graph"]["nodes"][1]["output_ports"] if p["port_id"] != "changes"]
    removed["graph"]["max_rework_cycles"] = 0
    retained_ids = {p:ids[p] for p in graph_source_elements(removed)}
    left = legacy.publish(source=removed, recipe=recipe(), source_ids=retained_ids, command_id="struct:delete", parent_ref=base.revision.revision_ref)
    right_source = deepcopy(base.source); right_source["graph"]["nodes"][1]["instruction"] = "Retain the right review edit."
    right = legacy.publish(source=right_source, recipe=recipe(), source_ids=ids, command_id="struct:right", parent_ref=base.revision.revision_ref)
    analysis = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="struct:analysis")
    combined = author.publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis), command_id="struct:merged")
    assert combined.source["graph"]["max_rework_cycles"] == 0
    assert combined.source["graph"]["nodes"][1]["instruction"] == right_source["graph"]["nodes"][1]["instruction"]
    assert not any(operation.name.endswith("__rework") for operation in combined.module.components[0].operations)
    restored = legacy.publish(source=base.source, recipe=recipe(), source_ids=ids,
        command_id="struct:reintroduce", parent_ref=left.revision.revision_ref)
    next_analysis = analyzer.analyze(local_ref=restored.revision.revision_ref, incoming_ref=right.revision.revision_ref, command_id="struct:origin-analysis")
    identity = ids["/arcs/review_to_draft"]
    assert next_analysis.document["normalized"]["local"]["atoms"][identity + "/origin"]["revision_ref"] == restored.revision.revision_ref.to_dict()
    assert next_analysis.document["normalized"]["incoming"]["atoms"][identity + "/origin"]["revision_ref"] == base.revision.revision_ref.to_dict()
    assert any(row["reason"] == "identity_origin_conflict" for row in next_analysis.document["conflicts"])
    explicit = choices(next_analysis)
    # Every reintroduced origin is an explicit caller decision, even where the
    # complete node/port wire happens to match the old source byte for byte.
    for row in explicit:
        for selection in row["selections"]:
            if selection["subject"].endswith("/origin"): selection["side"] = "incoming"
    selected = author.publish(analysis_ref=next_analysis.analysis_ref, choices=explicit, command_id="struct:origin-merge")
    assert selected.identity_origins[identity]["revision_ref"] == base.revision.revision_ref.to_dict()
    assert validate_closed_revision(core, selected.revision.revision_ref, registration()) == selected
    # Both actual parents can independently reintroduce an old spelling. The
    # historical base origin is then absent from both continuous parent chains.
    right_deleted = legacy.publish(source=removed, recipe=recipe(), source_ids=retained_ids,
        command_id="struct:right-delete", parent_ref=right.revision.revision_ref)
    right_restored = legacy.publish(source=right_source, recipe=recipe(), source_ids=ids,
        command_id="struct:right-reintroduce", parent_ref=right_deleted.revision.revision_ref)
    broken_chain = analyzer.analyze(local_ref=restored.revision.revision_ref, incoming_ref=right_restored.revision.revision_ref,
        command_id="struct:both-reintroduced")
    invalid = choices(broken_chain)
    for row in invalid:
        for selection in row["selections"]:
            if selection["subject"] == identity + "/origin": selection["side"] = "base"
    before = counts(core)
    with pytest.raises(UnresolvedGraphMerge, match="origin"):
        author.publish(analysis_ref=broken_chain.analysis_ref, choices=invalid, command_id="struct:forged-continuity")
    assert counts(core) == before


def test_new_analyzer_rejects_dual_or_unknown_legacy_input_markers_without_changing_old_reader(fixture, monkeypatch):
    from cpn.rpnh.collaboration import graph_authoring
    core, _, _, legacy, analyzer, _, base, ids = fixture
    original = graph_authoring._publish_private_system
    for index, marker in enumerate(("graph_merge_author_command_v1", "unknown_author_command_v9")):
        def extra_marker(core, owner, request):
            if request.content_schema_ref == "rpnh/collaboration/graph_author_source/v1":
                request = replace(request, descriptors={**request.descriptors, marker:"unexpected"})
            return original(core,owner,request)
        with monkeypatch.context() as patch:
            patch.setattr(graph_authoring,"_publish_private_system",extra_marker)
            old = legacy.publish(source=base.source,recipe=recipe(),source_ids=ids,
                parent_ref=base.revision.revision_ref,command_id="markers:old:"+str(index))
        assert validate_closed_revision(core,old.revision.revision_ref,registration()).source==base.source
        before=counts(core)
        with pytest.raises(RegistryConflict,match="markers"):
            analyzer.analyze(local_ref=old.revision.revision_ref,incoming_ref=base.revision.revision_ref,command_id="markers:new:"+str(index))
        assert counts(core)==before
        print("GRAPH_INPUT_MARKER_REJECT",marker)
