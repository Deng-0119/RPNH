"""Opt-in graph-v3 Branch keeps actual Registry CAS and descriptor semantics."""
from dataclasses import replace

import pytest

from cpn.rpnh.collaboration import (
    GraphBranchVersion, GraphMergeBranchVersion, current_branch, read_branch_version, validate_closed_revision,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError, canonical_json
from test_collaboration_graph_materials import registration
from test_collaboration_graph_merge import fixture, merged, counts


def advance(gateway, branch, revision, command_id, **changes):
    return gateway.advance_graph_merge_branch(**{
        "expected_branch_version_ref": branch.branch_ref, "expected_head_revision_ref": branch.head_revision_ref,
        "expected_stream_head": branch.sequence, "next_revision_ref": revision.revision_ref,
        "command_id": command_id, **changes})


def test_real_both_parent_branches_merge_edit_current_history_three_axis_cas(fixture):
    core, gateway, _, _, _, author, _, _ = fixture
    left, right, analysis, result = merged(fixture)
    first = gateway.create_graph_merge_branch(head_revision_ref=left.revision.revision_ref, command_id="branch:left")
    right_branch = gateway.create_graph_merge_branch(head_revision_ref=right.revision.revision_ref, command_id="branch:right")
    second = advance(gateway, first, result.revision, "branch:merge-left")
    right_merged = advance(gateway, right_branch, result.revision, "branch:merge-right")
    assert isinstance(second, GraphMergeBranchVersion) and right_merged.head_revision_ref == second.head_revision_ref
    ids = {row["locator"]:row["element_id"] for row in result.source_map["elements"]}
    edited = author.publish_edit(source=result.source, recipe=result.recipe, source_ids=ids,
        command_id="branch:edited", parent_ref=result.revision.revision_ref)
    before = counts(core)
    wrong = (
        {"expected_branch_version_ref":first.branch_ref},
        {"expected_head_revision_ref":left.revision.revision_ref},
        {"expected_stream_head":first.sequence},
    )
    for i, fields in enumerate(wrong):
        with pytest.raises(RegistryConflict): advance(gateway, second, edited.revision, "branch:bad:" + str(i), **fields)
        assert counts(core) == before
        print("GRAPH_BRANCH_CAS_AXIS", i)
    third = advance(gateway, second, edited.revision, "branch:edit")
    with pytest.raises(RegistryConflict): advance(gateway, second, edited.revision, "branch:competing-stale-caller")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = counts(reader)
    for branch, value in ((first,left),(second,result),(third,edited)):
        observed = read_branch_version(reader, branch.branch_ref)
        assert validate_closed_revision(reader, observed.head_revision_ref, registration()) == value
    assert current_branch(reader, first.branch_ref.ref.entity_id) == third
    assert advance(gateway, first, result.revision, "branch:merge-left") == second
    assert counts(reader) == before


def _persist(core, record):
    ref, body = record.revision_ref.ref, record.to_dict()
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref=body["schema_version"], idempotency_key=record.command_id)


def test_branch_descriptor_authority_does_not_fake_graph_material_proof_and_aba(fixture):
    core, gateway, _, _, _, _, _, _ = fixture
    _, right, _, result = merged(fixture)
    a, b = (replace(result.revision.revision_ref, ref=replace(result.revision.revision_ref.ref,
        version_id=new_id("resource_version"))) for _ in range(2))
    ra = replace(result.revision, revision_ref=a, command_id="descriptor:a", parent_revision_refs=(b,right.revision.revision_ref))
    rb = replace(result.revision, revision_ref=b, command_id="descriptor:b", parent_revision_refs=(a,right.revision.revision_ref))
    _persist(core,ra); _persist(core,rb)
    first = gateway.create_graph_merge_branch(head_revision_ref=a, command_id="descriptor:branch")
    second = advance(gateway, first, rb, "descriptor:next")
    before = counts(core)
    with pytest.raises(RegistryConflict): advance(gateway, second, ra, "descriptor:aba")
    assert counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == second
    with pytest.raises(RegistryConflict): validate_closed_revision(core, first.head_revision_ref, registration())


def test_old_graph_branch_decoder_writer_and_shared_command_domain_remain_strict(fixture):
    core, gateway, _, _, _, _, _, _ = fixture
    left, _, _, result = merged(fixture)
    branch = gateway.create_graph_merge_branch(head_revision_ref=left.revision.revision_ref, command_id="shared:branch")
    before = counts(core)
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        GraphBranchVersion.from_dict(branch.to_dict(), catalog=core.catalog)
    with pytest.raises((ValueError, TypeError, RegistryConflict)):
        gateway.create_graph_author_branch(head_revision_ref=result.revision.revision_ref, command_id="old:graph-reject")
    with pytest.raises(RegistryConflict):
        gateway.create_graph_author_branch(head_revision_ref=left.revision.revision_ref, command_id="shared:branch")
    assert counts(core) == before
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    with pytest.raises(StaleWriterError):
        gateway.create_graph_merge_branch(head_revision_ref=left.revision.revision_ref, command_id="stale:branch")
    assert current_branch(reopened, branch.branch_ref.ref.entity_id) == branch
