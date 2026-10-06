"""Exact plain merge/ordinary successor proofs through two-level Assembly/v3."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_composition import produce_merge, assert_author_only
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyAuthorV3, AssemblyCompletion, AssemblyConnection,
    AssemblyMemberV2, AssemblyMemberV3, PlainModuleMergeAuthor, ValidatedClosedRevision,
    ValidatedPlainMergeRevision, plain_merge_nested_assembly_schema_data,
    read_assembly_revision, validate_assembly_revision,
)
from cpn.rpnh.collaboration import assembly_v2, assembly_v3, materials, schema_catalog
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json

A, B, C, D, E, F = ("member:" + c * 32 for c in "abcdef")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", plain_merge_nested_assembly_schema_data)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    merger = PlainModuleMergeAuthor(inputs[1], inputs[2], inputs[4].producer)
    v2 = AssemblyAuthorV2(inputs[1], inputs[2], inputs[4].producer)
    v3 = AssemblyAuthorV3(inputs[1], inputs[2], inputs[4].producer)
    return inputs, merger, v2, v3


def elements(proof):
    proof = proof.generated if hasattr(proof, "generated") else proof
    return {row["locator"]: row["element_id"] for row in proof.element_map["elements"]}


def request(members, command, *, version=3, connected=False, parent=None):
    Member = AssemblyMemberV3 if version == 3 else AssemblyMemberV2
    first_id, first = members[0]
    last_id, last = members[-1]
    return {"name": "IntegratedAssembly", "members": tuple(
        Member(identity, "Exact selected member", value.revision.revision_ref) for identity, value in members),
        "connections": (AssemblyConnection(first_id, elements(first)["/exit/result"],
            last_id, elements(last)["/entry/request"]),) if connected else (),
        "completion": AssemblyCompletion(last_id, elements(last)["/terminal"]),
        "budget_policy": "shared_exact", "deployment_intent": "same_run_candidate",
        "command_id": command, "parent_ref": parent}


def read_once(core, value, monkeypatch):
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    original, calls = reader.event_store.connect, []
    def connect(*args, **kwargs):
        calls.append("connect")
        return original(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", connect)
    before = support.counts(core)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, support.registration())
    assert calls == ["connect"]
    assert checked.revision == value.revision
    assert type(checked.generated) is ValidatedClosedRevision
    assert canonical_json(checked.lowering_map) == canonical_json(value.lowering_map)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert support.counts(core) == before
    return checked


def test_real_merge_successor_direct_flat_child_and_repeated_v2_paths_are_exact(fixture, monkeypatch):
    inputs, merger, v2, v3 = fixture
    core = inputs[0]
    _, _, analysis, merged = produce_merge(inputs, merger)
    document = merged.module.to_dict()
    document["components"][1]["operations"][0]["config"] = {"value": "explicit successor edit"}
    successor = support.publish(inputs, document, "ordinary-after-merge", elements(merged), merged.revision.revision_ref)
    assert type(merged) is ValidatedPlainMergeRevision
    assert type(successor) is ValidatedClosedRevision
    assert successor.revision.parent_revision_refs == (merged.revision.revision_ref,)

    flat_args = request(((A, merged), (B, successor), (C, merged)), "direct-flat")
    flat = v3.publish(**flat_args)
    child_args = request(((A, merged), (B, successor)), "child-v2", version=2, connected=True)
    child = v2.publish(**child_args)
    root_args = request(((D, child), (E, child), (F, flat)), "nested-root")
    root = v3.publish(**root_args)
    for value in (flat, child, root):
        assert type(value.generated) is ValidatedClosedRevision
        assert canonical_json(value.generated.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
        assert read_assembly_revision(core, value.revision.revision_ref) == value.revision
    direct_paths = {tuple(row["member_path"]) for row in flat.lowering_map["instance_origins"]}
    assert direct_paths == {(A,), (B,), (C,)}
    for identity, proof in ((A, merged), (B, successor), (C, merged)):
        rows = [row for row in flat.lowering_map["instance_origins"] if row["member_path"] == [identity]]
        assert {row["element_id"] for row in rows} == set(elements(proof).values())
        assert all(row["revision_path"] == [proof.revision.revision_ref.to_dict()] for row in rows)
    expected = {(D, A): (child, merged), (D, B): (child, successor),
        (E, A): (child, merged), (E, B): (child, successor),
        (F, A): (flat, merged), (F, B): (flat, successor), (F, C): (flat, merged)}
    nested_rows = [row for row in root.lowering_map["instance_origins"] if len(row["member_path"]) == 2]
    assert {tuple(row["member_path"]) for row in nested_rows} == set(expected)
    for path, (container, leaf) in expected.items():
        rows = [row for row in nested_rows if tuple(row["member_path"]) == path]
        assert len(rows) == len(elements(leaf))
        assert {row["element_id"] for row in rows} == set(elements(leaf).values())
        assert all(row["revision_path"] == [container.revision.revision_ref.to_dict(),
            leaf.revision.revision_ref.to_dict()] for row in rows)
    assert {tuple(row["member_path"]) for row in root.lowering_map["assembly_members"]} == {(D,), (E,), (F,)}
    for identity in (D, E):
        assert any(row["member_path"] == [identity] and row["source_locator"] == "/connections/0"
            for row in root.lowering_map["assembly_introduced_origins"])

    # Observe real validators; every wrapper still calls its original consumer.
    leaves, child_reads = [], []
    original_leaf, original_children = materials._validate_at, assembly_v2._members_at
    def observe_leaf(db, core, reference, registration, binding, visiting):
        leaves.append((db, reference, frozenset(visiting)))
        return original_leaf(db, core, reference, registration, binding, visiting)
    def observe_children(db, core, plan, registration, binding, *, locked, visiting=None):
        child_reads.append((db, frozenset(visiting or ())))
        return original_children(db, core, plan, registration, binding, locked=locked, visiting=visiting)
    monkeypatch.setattr(materials, "_validate_at", observe_leaf)
    monkeypatch.setattr(assembly_v2, "_members_at", observe_children)
    read_once(core, root, monkeypatch)
    root_ref, flat_ref, child_ref = (value.revision.revision_ref for value in (root, flat, child))
    assert leaves and child_reads
    assert len({id(db) for db, *_ in leaves + child_reads}) == 1
    assert all(root_ref in active and child_ref in active for _, active in child_reads)
    for leaf in (merged, successor):
        assert any(reference == leaf.revision.revision_ref and {root_ref, flat_ref} <= active
            for _, reference, active in leaves)
    for value, required in ((root, {root_ref}), (flat, {root_ref, flat_ref}), (child, {root_ref, child_ref})):
        assert any(reference == value.revision.generated_revision_ref and required <= active
            for _, reference, active in leaves)
    # A separate direct-root full read observes its own active frame too.
    leaves.clear(); child_reads.clear()
    read_once(core, flat, monkeypatch)
    assert any(reference == merged.revision.revision_ref and flat_ref in active for _, reference, active in leaves)
    before = support.counts(core)
    assert v3.publish(**root_args).revision == root.revision
    assert v3.publish(**flat_args).revision == flat.revision
    assert v2.publish(**child_args).revision == child.revision
    assert merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge-result").revision == merged.revision
    assert support.publish(inputs, document, "ordinary-after-merge", elements(merged),
        merged.revision.revision_ref).revision == successor.revision
    assert support.counts(core) == before
    assert_author_only(core)


def test_nested_consumer_rejects_merge_proof_damage_and_cycles_in_one_cut(fixture, monkeypatch):
    inputs, merger, v2, v3 = fixture
    core = inputs[0]
    _, _, analysis, merged = produce_merge(inputs, merger)
    document = merged.module.to_dict()
    document["components"][1]["operations"][0]["config"] = {"value": "explicit inherited proof edit"}
    successor = support.publish(inputs, document, "proof-successor", elements(merged), merged.revision.revision_ref)
    assert type(successor) is ValidatedClosedRevision
    assert successor.revision.parent_revision_refs == (merged.revision.revision_ref,)
    child = v2.publish(**request(((A, successor),), "v2-member", version=2))
    args = request(((D, child),), "root-member")
    root = v3.publish(**args)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    connect, calls = reader.event_store.connect, []
    def counted(*args, **kwargs):
        calls.append("connect")
        return connect(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    before = support.counts(core)
    for kind, reference in (("command", merged.command_ref), ("resolution", merged.resolution_ref),
                            ("cycle", analysis.analysis_ref)):
        path = core.object_store.path_for_version(reference.ref.resource_version_id)
        raw = path.read_bytes(); document = json.loads(raw)
        if kind == "cycle":
            document["request"]["local_revision_ref"] = merged.revision.revision_ref.to_dict()
            damage = canonical_json(document)
        else:
            damage = json.dumps(dict(reversed(list(document.items()))), ensure_ascii=True,
                separators=(",", ":")).encode("utf-8")
            assert canonical_json(json.loads(damage)) == canonical_json(document)
        assert damage != raw and len(damage) == len(raw)
        calls.clear()
        try:
            path.write_bytes(damage)
            with pytest.raises((RegistryConflict, ObjectIntegrityError), match="cycle" if kind == "cycle" else None):
                validate_assembly_revision(reader, root.revision.revision_ref, support.registration())
            assert calls == ["connect"]
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                v3.publish(**{**args, "command_id": "bad-" + kind})
            assert support.counts(core) == before
        finally:
            path.write_bytes(raw)
        calls.clear()
        assert validate_assembly_revision(reader, root.revision.revision_ref, support.registration()).revision == root.revision
        assert calls == ["connect"] and support.counts(core) == before
    assert_author_only(core)


def test_nested_final_cut_keeps_parent_member_and_generated_proofs_exact(fixture, monkeypatch):
    inputs, merger, _, v3 = fixture
    core = inputs[0]
    _, _, _, merged = produce_merge(inputs, merger)
    parent = v3.publish(**request(((A, merged),), "parent"))
    args = request(((A, merged),), "child", parent=parent.revision.revision_ref)
    prospective = assembly_v3._result_ref(core, v3.binding["source_id"], args["command_id"], parent.revision.revision_ref)
    parent_path = core.object_store.path_for_version(parent.revision.plan_ref.ref.resource_version_id)
    member_path = core.object_store.path_for_version(merged.resolution_ref.ref.resource_version_id)
    original_publish, original_document, original_validate = v3.author.publish, v3._publish_document, assembly_v3._validate_at
    generated, observations, state = [], [], {"phase": "preflight"}
    def remember_generated(**kwargs):
        value = original_publish(**kwargs); generated.append(value)
        return value
    def observe_parent(db, core, reference, registration, binding, visiting, **kwargs):
        if reference == parent.revision.revision_ref:
            observations.append((state["phase"], db, frozenset(visiting)))
        return original_validate(db, core, reference, registration, binding, visiting, **kwargs)
    monkeypatch.setattr(v3.author, "publish", remember_generated)
    monkeypatch.setattr(assembly_v3, "_validate_at", observe_parent)
    prefix = None
    for kind in ("parent", "member", "generated"):
        state["phase"] = "preflight"
        damaged = []
        def damage_after_lowering(key, schema, document, **kwargs):
            reference = original_document(key, schema, document, **kwargs)
            if key.endswith(":lowering"):
                state["phase"] = "final"
                path = parent_path if kind == "parent" else member_path if kind == "member" else core.object_store.path_for_version(
                    generated[-1].revision.definition_ref.ref.resource_version_id)
                raw = path.read_bytes(); value = json.loads(raw)
                if kind == "parent":
                    value["name"] = "IntegratedAssembla"
                elif kind == "generated":
                    value["name"] = "IntegratedAssembla"
                else:
                    value = dict(reversed(list(value.items())))
                payload = (canonical_json(value) if kind != "member" else json.dumps(value,
                    ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
                assert len(payload) == len(raw) and payload != raw
                damaged.append((path, raw)); path.write_bytes(payload)
            return reference
        monkeypatch.setattr(v3, "_publish_document", damage_after_lowering)
        try:
            with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                v3.publish(**args)
            assert core.event_store.object_row(prospective.ref.version_id) is None
            assert len(core.event_store.object_rows_by_type("collaboration_assembly_revision/v3")) == 1
        finally:
            for path, raw in damaged:
                path.write_bytes(raw)
            monkeypatch.setattr(v3, "_publish_document", original_document)
        assert damaged
        now = support.counts(core)
        if prefix is not None:
            assert now == prefix  # Same immutable command, no duplicate prefix.
        prefix = now
    assert {phase for phase, _, active in observations if prospective in active} == {"preflight", "final"}
    assert all(prospective in active for _, _, active in observations)
    # Each cut has its own connection, while each real parent proof inherits root.
    assert len({id(db) for _, db, _ in observations}) >= 2
    state["phase"] = "restored"
    restored = v3.publish(**args)
    assert support.counts(core)[0] == prefix[0] + 1
    assert restored.revision.parent_revision_ref == parent.revision.revision_ref
    assert type(restored.generated) is ValidatedClosedRevision
    read_once(core, restored, monkeypatch)
    before = support.counts(core)
    assert v3.publish(**args).revision == restored.revision
    assert support.counts(core) == before
    assert_author_only(core)


def test_combined_catalog_preserves_prior_inventories_and_rejects_conflicts(monkeypatch):
    builders = (schema_catalog.plain_merge_result_schema_data, schema_catalog.graph_assembly_schema_data,
        schema_catalog.plain_merge_assembly_schema_data, schema_catalog.nested_assembly_schema_data)
    before = [builder() for builder in builders]
    actual, definitions, paths = plain_merge_nested_assembly_schema_data()
    assert set(actual) == set(before[0][0]) | set(before[-1][0])
    by_name = {value.name: value for value in definitions}
    assert len(by_name) == len(definitions)
    for schemas, types, locations in before:
        assert all(canonical_json(actual[key]) == canonical_json(value) for key, value in schemas.items())
        assert all(by_name[value.name] == value for value in types)
        assert all(paths[key].read_bytes() == path.read_bytes() for key, path in locations.items())
    for builder, (schemas, types, locations) in zip(builders, before):
        after, after_types, after_paths = builder()
        assert canonical_json(after) == canonical_json(schemas) and after_types == types and after_paths == locations
    original = schema_catalog.nested_assembly_schema_data
    shared = sorted(set(before[0][0]) & set(before[-1][0]))[0]
    def conflicting_schema():
        schemas, types, paths = original()
        schemas[shared] = {**schemas[shared], "$comment": "explicit different declaration"}
        return schemas, types, paths
    with monkeypatch.context() as patch:
        patch.setattr(schema_catalog, "nested_assembly_schema_data", conflicting_schema)
        with pytest.raises(ValueError, match="schema conflict"):
            plain_merge_nested_assembly_schema_data()
    shared_type = next(value.name for value in before[0][1] if value.name in {item.name for item in before[-1][1]})
    def conflicting_type():
        schemas, types, paths = original()
        return schemas, tuple(replace(value, recovery_rule=value.recovery_rule + "-different")
            if value.name == shared_type else value for value in types), paths
    with monkeypatch.context() as patch:
        patch.setattr(schema_catalog, "nested_assembly_schema_data", conflicting_type)
        with pytest.raises(ValueError, match="type conflict"):
            plain_merge_nested_assembly_schema_data()
