"""An ordinary v1 edit/copy after M remains a real Branch/Assembly author input."""
from copy import deepcopy
import json
import uuid

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_composition import fixture, produce_merge, assembly_request, assert_author_only
from cpn.rpnh.collaboration import (
    ValidatedClosedRevision, current_branch, read_branch_version,
    validate_assembly_revision, validate_closed_revision,
)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_json, canonical_text


def test_ordinary_edit_and_explicit_copy_after_merge_close_branch_and_assembly_chain(fixture):
    inputs, merger, assembly = fixture
    core, gateway, _, ordinary = inputs[:4]
    left, _, analysis, merged = produce_merge(inputs, merger)
    at_left = gateway.create_author_branch(head_revision_ref=left.revision.revision_ref, command_id="branch-left")
    at_merge = gateway.advance_author_branch(expected_branch_version_ref=at_left.branch_ref,
        expected_head_revision_ref=at_left.head_revision_ref, expected_stream_head=at_left.sequence,
        next_revision_ref=merged.revision.revision_ref, command_id="branch-merge")

    # This is explicit fixture caller data, using the unchanged ordinary editor.
    original = merged.module.to_dict()
    document = deepcopy(original)
    document["components"][1]["operations"][0]["config"] = {"value": "explicit ordinary edit after M"}
    copied = deepcopy(original["components"][1]); copied["name"] = "copied"
    document["components"].append(copied)
    document["entry"]["copy_request"] = {"component": "copied", "port": "request"}
    module = ModuleDeclaration.from_dict(document)
    inherited = {row["locator"]: row["element_id"] for row in merged.element_map["elements"]}
    ids = {path: inherited.get(path, "element:" + uuid.uuid5(uuid.NAMESPACE_URL,
        "explicit-post-merge-copy:" + path).hex) for path in _elements(module)}
    copies = {}
    for path, identity in ids.items():
        if path.startswith("/components/copied"):
            copies[identity] = inherited[path.replace("/components/copied", "/components/renamed", 1)]
        elif path == "/entry/copy_request":
            copies[identity] = inherited["/entry/second"]
    assert len(copies) == 5
    before = support.counts(core)
    # Retained identity is still not an ordinary copy, even when its parent is M.
    with pytest.raises(ValueError, match="copy needs a new identity"):
        ordinary.publish(module=module, element_ids=ids, parent_ref=merged.revision.revision_ref,
            copy_sources={**copies, inherited["/components/renamed"]: inherited["/components/renamed"]},
            command_id="invalid-retained-copy")
    assert support.counts(core) == before
    args = {"module": module, "element_ids": ids, "parent_ref": merged.revision.revision_ref,
            "copy_sources": copies, "command_id": "ordinary-after-merge"}
    edited = ordinary.publish(**args)
    assert type(edited) is ValidatedClosedRevision
    assert edited.revision.parent_revision_refs == (merged.revision.revision_ref,)
    assert edited.revision.selected_change_refs == ()
    assert edited.revision.revision_ref.ref.entity_id == merged.revision.revision_ref.ref.entity_id
    key = "collaboration-author:" + canonical_text({"command_id": args["command_id"]})
    expected_version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": merged.revision.revision_ref.source_id,
        "kind": "resource_version"})).hex)
    assert edited.revision.revision_ref.ref.version_id == expected_version
    metadata = json.loads(core.event_store.object_row(edited.revision.definition_ref.ref.resource_version_id)["metadata_json"])
    assert set(metadata["descriptors"]) == {"closed_author_command_v1"}
    command = json.loads(metadata["descriptors"]["closed_author_command_v1"])
    assert command["schema_version"] == "rpnh/collaboration/closed_author_command/v1"
    assert command["parent_revision_refs"] == [merged.revision.revision_ref.to_dict()]
    by_id = {row["element_id"]: row for row in edited.element_map["elements"]}
    assert set(inherited.values()) <= set(by_id)
    for path, identity in inherited.items():
        assert by_id[identity]["locator"] == path and by_id[identity]["copied_from"] is None
    for identity, parent_identity in copies.items():
        assert by_id[identity]["copied_from"] == {
            "revision_ref": merged.revision.revision_ref.to_dict(), "element_id": parent_identity}
    assert edited.module.components[1].operations[0].config == {"value": "explicit ordinary edit after M"}
    assert edited.module.components[2].operations[0].config == merged.module.components[1].operations[0].config

    at_edit = gateway.advance_author_branch(expected_branch_version_ref=at_merge.branch_ref,
        expected_head_revision_ref=merged.revision.revision_ref, expected_stream_head=at_merge.sequence,
        next_revision_ref=edited.revision.revision_ref, command_id="branch-edit")
    assert current_branch(core, at_left.branch_ref.ref.entity_id) == at_edit
    assert at_edit.head_revision_ref == edited.revision.revision_ref
    assert read_branch_version(core, at_merge.branch_ref) == at_merge
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for branch, value in ((at_merge, merged), (at_edit, edited)):
        exact = read_branch_version(reader, branch.branch_ref)
        full = validate_closed_revision(reader, exact.head_revision_ref, support.registration())
        assert full.revision == value.revision
        assert canonical_json(full.module.to_dict()) == canonical_json(value.module.to_dict())

    value = assembly.publish(**assembly_request(edited, command_id="assembly-after-edit"))
    assert value.plan["members"][0]["revision_ref"] == edited.revision.revision_ref.to_dict()
    assert value.plan["members"][0]["resolution"] == {"kind": "plain_closed_v1", **{
        field: getattr(edited.revision, field).to_dict() for field in
        ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")}}
    assert type(value.generated) is ValidatedClosedRevision
    assert {row["element_id"] for row in value.lowering_map["origins"]} == set(by_id)
    assert all(row["revision_ref"] == edited.revision.revision_ref.to_dict() for row in value.lowering_map["origins"])
    checked = validate_assembly_revision(reader, value.revision.revision_ref, support.registration())
    assert checked.revision == value.revision and checked.generated.revision == value.generated.revision
    assert canonical_json(checked.lowering_map) == canonical_json(value.lowering_map)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    frozen = support.counts(core)
    assert ordinary.publish(**args).revision == edited.revision
    assert merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge-result").revision == merged.revision
    assert current_branch(core, at_left.branch_ref.ref.entity_id) == at_edit
    assert read_branch_version(core, at_merge.branch_ref).head_revision_ref == merged.revision.revision_ref
    assert support.counts(core) == frozen
    assert_author_only(core)
    print("B3_REAL_CHAIN=" + json.dumps({"merge_ref": merged.revision.revision_ref.to_dict(),
        "ordinary_edit_ref": edited.revision.revision_ref.to_dict(), "copy_count": len(copies),
        "historical_branch_ref": at_merge.branch_ref.to_dict(), "current_branch_ref": at_edit.branch_ref.to_dict(),
        "assembly_ref": value.revision.revision_ref.to_dict(), "generated_ref": value.generated.revision.revision_ref.to_dict(),
        "ordinary_marker": command["schema_version"], "same_command_replay_added_facts": False}, sort_keys=True))
