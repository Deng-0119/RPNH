"""Real explicit author split/fusion, using only inert author HOST fixtures."""
from copy import deepcopy
import json
import uuid

import pytest

import test_collaboration_plain_merge as support
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyMemberV2, AssemblyCompletion, AssemblyConnection,
    PlainModuleTransformAuthor, ValidatedPlainTransformRevision, ValidatedClosedRevision,
    current_branch, read_branch_version, validate_closed_revision, validate_assembly_revision,
    plain_transform_assembly_schema_data, plain_merge_schema_data,
)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json, canonical_text

A, B = ("member:" + c * 32 for c in "ab")


def catalog_data():
    schemas, types, paths = plain_transform_assembly_schema_data()
    other_schemas, other_types, other_paths = plain_merge_schema_data()
    for key, value in other_schemas.items():
        assert key not in schemas or canonical_json(value) == canonical_json(schemas[key])
        schemas[key], paths[key] = value, other_paths[key]
    actual = {t.name: t for t in types}
    for t in other_types:
        assert t.name not in actual or actual[t.name] == t
        actual[t.name] = t
    return schemas, tuple(actual.values()), paths


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", catalog_data)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    core, gateway, registration, ordinary, analyzer, base, ids = inputs
    author = PlainModuleTransformAuthor(gateway, registration, ordinary.producer)
    assembly = AssemblyAuthorV2(gateway, registration, ordinary.producer)
    return inputs, author, assembly


def ids_for(value):
    return {row["locator"]: row["element_id"] for row in value.element_map["elements"]}


def fresh(phase, path):
    return "element:" + uuid.uuid5(uuid.NAMESPACE_URL, "plain-transform:" + phase + ":" + path).hex


def split_request(base, command_id="split"):
    document = base.module.to_dict()
    old = ids_for(base)
    component = deepcopy(document["components"][1])
    first, second = deepcopy(component), deepcopy(component)
    first["name"], second["name"] = "b1", "b2"
    first["operations"][0]["config"] = {"stage": "first caller step"}
    second["operations"][0]["config"] = {"stage": "second caller step"}
    copied = deepcopy(document["components"][0]); copied["name"] = "copied"
    document["components"] = [document["components"][0], first, second, copied]
    document["entry"]["second"]["component"] = "b1"
    document["entry"]["copy"] = {"component": "copied", "port": "request"}
    document["links"] = [{"source": {"component": "b1", "port": "result"}, "target": {"component": "b2", "port": "request"}}]
    module = ModuleDeclaration.from_dict(document)
    ids = {path: old.get(path, fresh(command_id, path)) for path in _elements(module)}
    groups = []
    for path, identity in old.items():
        if path.startswith("/components/b"):
            groups.append({"kind": "split", "source_revision_ref": base.revision.revision_ref.to_dict(),
                "source_element_ids": [identity], "target_element_ids": sorted([
                    ids[path.replace("/components/b", "/components/b1")], ids[path.replace("/components/b", "/components/b2")]])})
    copies = {identity: old[path.replace("/components/copied", "/components/a")]
              for path, identity in ids.items() if path.startswith("/components/copied")}
    copies[ids["/entry/copy"]] = old["/entry/request"]
    return {"parent_ref": base.revision.revision_ref, "module": module, "element_ids": ids,
        "transform_groups": sorted(groups, key=canonical_text), "copy_sources": copies,
        "created_element_ids": [ids["/links/0"]], "removed_element_ids": [], "command_id": command_id}


def fusion_request(split, command_id="fusion"):
    document = split.module.to_dict()
    old = ids_for(split)
    retained, joined = deepcopy(document["components"][0]), deepcopy(document["components"][1])
    retained["name"] = "renamed"
    retained["operations"][0]["config"] = {"changed": "retained identity is not unchanged content"}
    joined["name"] = "joined"
    joined["operations"][0]["config"] = {"stage": "explicit caller fusion definition"}
    document["components"] = [retained, joined]
    document["links"] = []
    document["entry"].pop("copy")
    document["entry"]["request"]["component"] = "renamed"
    document["entry"]["second"]["component"] = "joined"
    document["exit"]["result"]["component"] = "renamed"
    document["terminal"]["source"]["component"] = "renamed"
    module = ModuleDeclaration.from_dict(document)
    ids = {path: old.get(path.replace("/components/renamed", "/components/a"), fresh(command_id, path)) for path in _elements(module)}
    groups = []
    for path, identity in ids.items():
        if path.startswith("/components/joined"):
            groups.append({"kind": "fusion", "source_revision_ref": split.revision.revision_ref.to_dict(),
                "source_element_ids": sorted([old[path.replace("/components/joined", "/components/b1")],
                                               old[path.replace("/components/joined", "/components/b2")]]),
                "target_element_ids": [identity]})
    consumed = {i for row in groups for i in row["source_element_ids"]}
    return {"parent_ref": split.revision.revision_ref, "module": module, "element_ids": ids,
        "transform_groups": sorted(groups, key=canonical_text), "copy_sources": {}, "created_element_ids": [],
        "removed_element_ids": sorted(set(old.values()) - set(ids.values()) - consumed), "command_id": command_id}


def assembly_request(split, fused, command_id="assembly"):
    left, right = ids_for(split), ids_for(fused)
    return {"name": "ExplicitTransforms", "members": (AssemblyMemberV2(A, "split", split.revision.revision_ref),
            AssemblyMemberV2(B, "fusion", fused.revision.revision_ref)),
        "connections": (AssemblyConnection(A, left["/exit/result"], B, right["/entry/request"]),),
        "completion": AssemblyCompletion(B, right["/terminal"]), "budget_policy": "shared_exact",
        "deployment_intent": "same_run_candidate", "command_id": command_id}


def test_actual_split_fusion_branch_assembly_and_readonly_full_reopen(fixture, monkeypatch):
    inputs, author, assembly = fixture
    core, gateway, registration, _, _, base, original = inputs
    split_args = split_request(base)
    split = author.publish(**split_args)
    assert type(split) is ValidatedPlainTransformRevision
    assert len(split.module.components) == 4 and len(split.module.links) == 1
    assert split.module.to_dict()["components"][0] == base.module.to_dict()["components"][0]
    assert all(ids_for(split)[path] == identity for path, identity in original.items() if not path.startswith("/components/b"))
    assert len(split.transformation["transform_groups"]) == 4
    assert len(split.transformation["copy_sources"]) == 5
    assert len(split.transformation["created_element_ids"]) == 1
    assert split.transformation["removed_element_ids"] == []
    fused = author.publish(**fusion_request(split))
    assert type(fused) is ValidatedPlainTransformRevision
    assert len(fused.module.components) == 2 and not fused.module.links
    assert all(len(row["source_element_ids"]) == 2 and len(row["target_element_ids"]) == 1 for row in fused.transformation["transform_groups"])
    assert len(fused.transformation["removed_element_ids"]) == 6
    assert ids_for(fused)["/components/renamed"] == original["/components/a"]
    assert fused.module.components[0].operations[0].config != base.module.components[0].operations[0].config
    assert fused.revision.parent_revision_refs == (split.revision.revision_ref,)
    assert split.revision.parent_revision_refs == (base.revision.revision_ref,)
    assert fused.revision.revision_ref.ref.entity_id == base.revision.revision_ref.ref.entity_id
    branch = gateway.create_author_branch(head_revision_ref=base.revision.revision_ref, command_id="branch")
    heads = [(branch, base)]
    for value in (split, fused):
        args = {"expected_branch_version_ref": branch.branch_ref, "expected_head_revision_ref": branch.head_revision_ref,
            "expected_stream_head": branch.sequence, "next_revision_ref": value.revision.revision_ref,
            "command_id": "branch:" + value.revision.command_id}
        branch = gateway.advance_author_branch(**args)
        heads.append((branch, value))
    with pytest.raises(RegistryConflict):
        gateway.advance_author_branch(**{**args, "expected_stream_head": 1, "command_id": "stale-branch"})
    result = assembly.publish(**assembly_request(split, fused))
    assert type(result.generated) is ValidatedClosedRevision
    assert canonical_json(result.generated.compiled.to_dict()) == canonical_json(result.compiled.to_dict())
    for member, value in ((A, split), (B, fused)):
        assert {r["element_id"] for r in result.lowering_map["origins"] if r["member_id"] == member} == set(ids_for(value).values())
        row = next(row for row in result.plan["members"] if row["member_id"] == member)
        assert row["resolution"] == {"kind": "plain_closed_v1", **{name: getattr(value.revision, name).to_dict()
            for name in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")}}
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for at, value in heads:
        read = read_branch_version(reader, at.branch_ref)
        verified = validate_closed_revision(reader, read.head_revision_ref, support.registration())
        assert verified.revision == value.revision
        assert canonical_json(verified.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
        if value is not base:
            assert verified.transformation == value.transformation
    connect = reader.event_store.connect
    calls = []
    def counted(*args, **kwargs):
        calls.append("connect")
        return connect(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    reopened = validate_assembly_revision(reader, result.revision.revision_ref, support.registration())
    assert calls == ["connect"]
    assert reopened.revision == result.revision and reopened.generated.revision == result.generated.revision
    assert canonical_json(reopened.lowering_map) == canonical_json(result.lowering_map)
    before = support.counts(core)
    assert author.publish(**split_args).revision == split.revision
    assert current_branch(core, branch.branch_ref.ref.entity_id) == branch
    assert support.counts(core) == before
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1", "firing_started/v1", "execution_instance_created/v1")) == ()
    print("AUTHOR_TRANSFORM_CHAIN=" + json.dumps({"base": base.revision.revision_ref.to_dict(),
        "split": split.revision.revision_ref.to_dict(), "fusion": fused.revision.revision_ref.to_dict(),
        "assembly": result.revision.revision_ref.to_dict(), "generated": result.generated.revision.revision_ref.to_dict(),
        "branch": branch.branch_ref.to_dict()}, sort_keys=True))
