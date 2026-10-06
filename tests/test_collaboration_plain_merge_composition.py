"""Real author merge -> Branch descriptors -> full Assembly/v2 member proof."""
from copy import deepcopy

import pytest

import test_collaboration_plain_merge as support
from cpn.rpnh.collaboration import (
    AssemblyAuthor, AssemblyAuthorV2, AssemblyCompletion, AssemblyMember, AssemblyMemberV2, PlainModuleMergeAuthor,
    ValidatedClosedRevision, ValidatedPlainMergeRevision, current_branch,
    graph_assembly_schema_data, plain_merge_assembly_schema_data, plain_merge_result_schema_data,
    read_branch_version, validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json

MEMBER = "member:" + "a" * 32


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", plain_merge_assembly_schema_data)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    merger = PlainModuleMergeAuthor(inputs[1], inputs[2], inputs[4].producer)
    assembly = AssemblyAuthorV2(inputs[1], inputs[2], inputs[4].producer)
    return inputs, merger, assembly


def produce_merge(inputs, merger):
    base, ids = inputs[5:]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["name"] = "renamed"
    left_doc["entry"]["second"]["component"] = "renamed"
    left_ids = {p.replace("/components/b", "/components/renamed"): value for p, value in ids.items()}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "explicit incoming fixture edit"}
    left = support.publish(inputs, left_doc, "left", left_ids)
    right = support.publish(inputs, right_doc, "right")
    analysis = inputs[4].analyze(**support.request(inputs, left, right))
    assert analysis.document["conflicts"] == []
    result = merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge-result")
    return left, right, analysis, result


def assembly_request(merged, *, command_id="assembly-merge", parent_ref=None):
    ids = {row["locator"]: row["element_id"] for row in merged.element_map["elements"]}
    return {"name": "MergedAssembly", "members": (AssemblyMemberV2(MEMBER, "Explicit merged member", merged.revision.revision_ref),),
        "connections": (), "completion": AssemblyCompletion(MEMBER, ids["/terminal"]),
        "budget_policy": "shared_exact", "deployment_intent": "same_run_candidate",
        "command_id": command_id, "parent_ref": parent_ref}


def assert_author_only(core):
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1",
        "firing_started/v1", "execution_instance_created/v1")) == ()


def test_real_merge_branch_current_history_and_assembly_v2_full_proof(fixture, monkeypatch):
    inputs, merger, assembly = fixture
    core, gateway = inputs[:2]
    left, right, analysis, merged = produce_merge(inputs, merger)
    assert isinstance(merged, ValidatedPlainMergeRevision)
    assert merged.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    first = gateway.create_author_branch(head_revision_ref=left.revision.revision_ref, command_id="branch-create")
    advanced = gateway.advance_author_branch(expected_branch_version_ref=first.branch_ref,
        expected_head_revision_ref=left.revision.revision_ref, expected_stream_head=first.sequence,
        next_revision_ref=merged.revision.revision_ref, command_id="branch-merge")
    assert current_branch(core, first.branch_ref.ref.entity_id) == advanced
    assert advanced.head_revision_ref == merged.revision.revision_ref
    assert read_branch_version(core, first.branch_ref) == first
    for branch, expected in ((first, left), (advanced, merged)):
        exact = read_branch_version(core, branch.branch_ref)
        full = validate_closed_revision(core, exact.head_revision_ref, support.registration())
        assert full.revision == expected.revision
        assert canonical_json(full.module.to_dict()) == canonical_json(expected.module.to_dict())
    value = assembly.publish(**assembly_request(merged))
    row = value.plan["members"][0]
    assert row["revision_ref"] == merged.revision.revision_ref.to_dict()
    assert row["resolution"] == {"kind": "plain_closed_v1", **{field: getattr(merged.revision, field).to_dict()
        for field in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")}}
    assert type(value.generated) is ValidatedClosedRevision
    assert {origin["element_id"] for origin in value.lowering_map["origins"]} == {
        element["element_id"] for element in merged.element_map["elements"]}
    assert all(origin["revision_ref"] == merged.revision.revision_ref.to_dict()
               and origin["member_id"] == MEMBER for origin in value.lowering_map["origins"])
    assert canonical_json(value.generated.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    connect = reader.event_store.connect
    calls = []
    def counted(*args, **kwargs):
        calls.append("connect")
        return connect(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    before = support.counts(core)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, support.registration())
    assert calls == ["connect"]
    assert checked.revision == value.revision and checked.generated.revision == value.generated.revision
    assert canonical_json(checked.lowering_map) == canonical_json(value.lowering_map)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge-result").revision == merged.revision
    assert current_branch(core, first.branch_ref.ref.entity_id) == advanced
    assert support.counts(core) == before
    assert_author_only(core)


def test_explicit_merge_assembly_catalog_preserves_both_prior_inventories():
    actual, actual_types, actual_paths = plain_merge_assembly_schema_data()
    prior = (plain_merge_result_schema_data(), graph_assembly_schema_data())
    assert set(actual) == set(prior[0][0]) | set(prior[1][0])
    by_type = {item.name: item for item in actual_types}
    assert len(by_type) == len(actual_types)
    for schemas, types, paths in prior:
        assert all(canonical_json(actual[key]) == canonical_json(body) for key, body in schemas.items())
        assert all(by_type[item.name] == item for item in types)
        assert all(actual_paths[key].read_bytes() == path.read_bytes() for key, path in paths.items())


def test_merge_branch_rejects_each_stale_axis_and_preserves_result_after_other_advance(fixture):
    inputs, merger, _ = fixture
    core, gateway = inputs[:2]
    left, right, analysis, merged = produce_merge(inputs, merger)
    root = gateway.create_author_branch(head_revision_ref=inputs[5].revision.revision_ref, command_id="branch-root")
    at_left = gateway.advance_author_branch(expected_branch_version_ref=root.branch_ref,
        expected_head_revision_ref=root.head_revision_ref, expected_stream_head=root.sequence,
        next_revision_ref=left.revision.revision_ref, command_id="branch-left")
    valid = {"expected_branch_version_ref": at_left.branch_ref,
        "expected_head_revision_ref": left.revision.revision_ref, "expected_stream_head": at_left.sequence,
        "next_revision_ref": merged.revision.revision_ref}
    before = support.counts(core)
    for axis, bad in (("version", {"expected_branch_version_ref": root.branch_ref}),
                      ("head", {"expected_head_revision_ref": right.revision.revision_ref}),
                      ("stream", {"expected_stream_head": at_left.sequence + 1})):
        with pytest.raises(RegistryConflict):
            gateway.advance_author_branch(**{**valid, **bad}, command_id="bad-axis-" + axis)
        assert support.counts(core) == before
        assert current_branch(core, root.branch_ref.ref.entity_id) == at_left
    at_merge = gateway.advance_author_branch(**valid, command_id="branch-merge")
    assert at_merge.head_revision_ref == merged.revision.revision_ref

    # This alternative is an ordinary child of L, created before any M edit.
    # It does not enter the separate B3 post-merge authoring stage.
    doc = left.module.to_dict(); doc["name"] = "Alternative"
    ids = {row["locator"]: row["element_id"] for row in left.element_map["elements"]}
    alternative = support.publish(inputs, doc, "alternative-from-left", ids, left.revision.revision_ref)
    other = gateway.create_author_branch(head_revision_ref=left.revision.revision_ref, command_id="other-branch")
    moved = gateway.advance_author_branch(expected_branch_version_ref=other.branch_ref,
        expected_head_revision_ref=other.head_revision_ref, expected_stream_head=other.sequence,
        next_revision_ref=alternative.revision.revision_ref, command_id="other-advance")
    before = support.counts(core)
    with pytest.raises(RegistryConflict):
        gateway.advance_author_branch(expected_branch_version_ref=other.branch_ref,
            expected_head_revision_ref=other.head_revision_ref, expected_stream_head=other.sequence,
            next_revision_ref=merged.revision.revision_ref, command_id="stale-merge-advance")
    assert validate_closed_revision(core, merged.revision.revision_ref, support.registration()).revision == merged.revision
    assert merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge-result").revision == merged.revision
    assert current_branch(core, other.branch_ref.ref.entity_id) == moved
    assert support.counts(core) == before
    assert_author_only(core)


def test_assembly_reader_and_producer_rebuild_the_members_actual_merge_proof(fixture, monkeypatch):
    import json
    inputs, merger, assembly = fixture
    core = inputs[0]
    _, _, analysis, merged = produce_merge(inputs, merger)
    value = assembly.publish(**assembly_request(merged))
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    connect, calls = reader.event_store.connect, []
    def counted(*args, **kwargs):
        calls.append("connect")
        return connect(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    before = support.counts(core)
    for damage, ref in (("command", merged.command_ref), ("resolution", merged.resolution_ref),
                        ("cycle", analysis.analysis_ref)):
        path = core.object_store.path_for_version(ref.ref.resource_version_id)
        raw = path.read_bytes(); document = json.loads(raw)
        if damage == "cycle":
            document["request"]["local_revision_ref"] = merged.revision.revision_ref.to_dict()
            rewritten = canonical_json(document)
        else:
            rewritten = json.dumps(dict(reversed(list(document.items()))), ensure_ascii=True,
                separators=(",", ":")).encode("utf-8")
            assert canonical_json(json.loads(rewritten)) == canonical_json(json.loads(raw))
        assert len(rewritten) == len(raw) and rewritten != raw
        calls.clear()
        try:
            path.write_bytes(rewritten)
            with pytest.raises((RegistryConflict, ObjectIntegrityError), match="cycle" if damage == "cycle" else None):
                validate_assembly_revision(reader, value.revision.revision_ref, support.registration())
            assert calls == ["connect"]
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                assembly.publish(**assembly_request(merged, command_id="bad-member-" + damage))
            assert support.counts(core) == before
        finally:
            path.write_bytes(raw)
        calls.clear()
        assert validate_assembly_revision(reader, value.revision.revision_ref, support.registration()).revision == value.revision
        assert calls == ["connect"]
        assert support.counts(core) == before
    assert_author_only(core)


def test_final_assembly_cut_rechecks_member_proof_and_reuses_the_original_prefix(fixture, monkeypatch):
    inputs, merger, assembly = fixture
    core = inputs[0]
    _, _, _, merged = produce_merge(inputs, merger)
    path = core.object_store.path_for_version(merged.resolution_ref.ref.resource_version_id)
    raw = path.read_bytes()
    original = assembly._publish_document
    def corrupt_after_lowering(key, schema, document, **kwargs):
        ref = original(key, schema, document, **kwargs)
        if key.endswith(":lowering"):
            path.write_bytes(raw + b" ")
        return ref
    monkeypatch.setattr(assembly, "_publish_document", corrupt_after_lowering)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            assembly.publish(**assembly_request(merged))
        assert core.event_store.object_rows_by_type("collaboration_assembly_revision/v2") == ()
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(assembly, "_publish_document", original)
    # The failed attempt already locked its plan and produced its exact prefix.
    # Restoring the immutable member allows the same request to finish once.
    before = support.counts(core)
    value = assembly.publish(**assembly_request(merged))
    assert support.counts(core)[0] == before[0] + 1
    assert validate_assembly_revision(core, value.revision.revision_ref, support.registration()).revision == value.revision
    frozen = support.counts(core)
    assert assembly.publish(**assembly_request(merged)).revision == value.revision
    assert support.counts(core) == frozen
    assert_author_only(core)


def test_legacy_v1_repeated_member_parent_and_replay_keep_original_semantics(fixture, monkeypatch):
    inputs, _, _ = fixture
    core, gateway, selected, _, analyzer, base, ids = inputs
    legacy = AssemblyAuthor(gateway, selected, analyzer.producer)
    other_id = "member:" + "b" * 32
    args = {"name": "LegacyPair", "members": (AssemblyMember(MEMBER, "Same", base.revision.revision_ref),
        AssemblyMember(other_id, "Same", base.revision.revision_ref)), "connections": (),
        "completion": AssemblyCompletion(other_id, ids["/terminal"]), "budget_policy": "shared_exact",
        "deployment_intent": "same_run_candidate", "command_id": "legacy-root"}
    root = legacy.publish(**args)
    child_args = {**args, "command_id": "legacy-child", "parent_ref": root.revision.revision_ref}
    path = core.object_store.path_for_version(root.revision.plan_ref.ref.resource_version_id)
    raw = path.read_bytes()
    false_parent_plan = deepcopy(root.plan)
    false_parent_plan["name"] = "LegacyXair"
    damaged_parent = canonical_json(false_parent_plan)
    assert len(damaged_parent) == len(raw) and damaged_parent != raw
    original = legacy._publish_document
    def corrupt_parent_after_lowering(key, schema, document):
        ref = original(key, schema, document)
        if key.endswith(":lowering"):
            path.write_bytes(damaged_parent)  # Legal same-size JSON, false parent derivation.
        return ref
    monkeypatch.setattr(legacy, "_publish_document", corrupt_parent_after_lowering)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            legacy.publish(**child_args)
        assert len(core.event_store.object_rows_by_type("collaboration_assembly_revision/v1")) == 1
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(legacy, "_publish_document", original)
    # The already committed child resources/generated prefix remain available;
    # restoration permits the same request to add only its final descriptor.
    before_retry = support.counts(core)
    child = legacy.publish(**child_args)
    assert support.counts(core)[0] == before_retry[0] + 1
    assert child.revision.parent_revision_ref == root.revision.revision_ref
    assert child.generated.revision.parent_revision_refs == (root.generated.revision.revision_ref,)
    assert type(child.generated) is ValidatedClosedRevision
    assert len(child.lowering_map["origins"]) == 2 * len(base.element_map["elements"])
    assert {origin["member_id"] for origin in child.lowering_map["origins"]} == {MEMBER, other_id}
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = support.counts(core)
    assert validate_assembly_revision(reader, child.revision.revision_ref, support.registration()).revision == child.revision
    assert legacy.publish(**child_args).revision == child.revision
    assert support.counts(core) == before
    assert_author_only(core)


def test_v2_repeated_merge_siblings_and_final_parent_cut_keep_exact_pair(fixture, monkeypatch):
    inputs, merger, assembly = fixture
    core = inputs[0]
    _, _, _, merged = produce_merge(inputs, merger)
    other_id = "member:" + "b" * 32
    ids = {row["locator"]: row["element_id"] for row in merged.element_map["elements"]}
    args = {**assembly_request(merged), "members": (
        AssemblyMemberV2(MEMBER, "Same", merged.revision.revision_ref),
        AssemblyMemberV2(other_id, "Same", merged.revision.revision_ref)),
        "completion": AssemblyCompletion(other_id, ids["/terminal"])}
    root = assembly.publish(**args)
    assert len(root.lowering_map["origins"]) == 2 * len(merged.element_map["elements"])
    assert {row["member_id"] for row in root.lowering_map["origins"]} == {MEMBER, other_id}
    assert type(root.generated) is ValidatedClosedRevision
    path = core.object_store.path_for_version(root.revision.plan_ref.ref.resource_version_id)
    raw = path.read_bytes()
    false_parent_plan = deepcopy(root.plan)
    false_parent_plan["name"] = "MutateAssembly"
    damaged_parent = canonical_json(false_parent_plan)
    assert len(damaged_parent) == len(raw) and damaged_parent != raw
    original = assembly._publish_document
    def corrupt_parent_after_lowering(key, schema, document, **kwargs):
        ref = original(key, schema, document, **kwargs)
        if key.endswith(":lowering"):
            path.write_bytes(damaged_parent)
        return ref
    child_args = {**args, "parent_ref": root.revision.revision_ref, "command_id": "child-assembly"}
    monkeypatch.setattr(assembly, "_publish_document", corrupt_parent_after_lowering)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            assembly.publish(**child_args)
        assert len(core.event_store.object_rows_by_type("collaboration_assembly_revision/v2")) == 1
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(assembly, "_publish_document", original)
    before = support.counts(core)
    child = assembly.publish(**child_args)
    assert support.counts(core)[0] == before[0] + 1
    assert child.revision.parent_revision_ref == root.revision.revision_ref
    assert child.generated.revision.parent_revision_refs == (root.generated.revision.revision_ref,)
    assert type(child.generated) is ValidatedClosedRevision
    assert len(child.lowering_map["origins"]) == 2 * len(merged.element_map["elements"])
    assert validate_assembly_revision(core, child.revision.revision_ref, support.registration()).revision == child.revision
    frozen = support.counts(core)
    assert assembly.publish(**child_args).revision == child.revision
    assert support.counts(core) == frozen
    assert_author_only(core)
