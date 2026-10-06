"""Real bounded selective author publication under the inert legacy fixture."""
from copy import deepcopy
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_composition import assembly_request, assert_author_only
from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, PlainModuleTransplantAnalyzer, PlainModuleTransplantAuthor,
    ValidatedClosedRevision, ValidatedPlainTransplantRevision, current_branch,
    read_branch_version, validate_assembly_revision, validate_closed_revision,
    plain_transplant_assembly_schema_data, SourceQualifiedResourceRef,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", plain_transplant_assembly_schema_data)
    inputs = support.fixture.__wrapped__(tmp_path, monkeypatch)
    analyzer = PlainModuleTransplantAnalyzer(inputs[1], inputs[2], inputs[4].producer)
    author = PlainModuleTransplantAuthor(inputs[1], inputs[2], inputs[4].producer)
    return inputs, analyzer, author


def produce_inputs(inputs):
    base, ids = inputs[5:]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["name"] = "local_name"
    left_doc["entry"]["second"]["component"] = "local_name"
    left_ids = {p.replace("/components/b", "/components/local_name"): value for p, value in ids.items()}
    right_doc["components"][0]["operations"][0]["config"] = {"value": "omitted donor change"}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "selected donor change"}
    left, right = support.publish(inputs, left_doc, "left", left_ids), support.publish(inputs, right_doc, "right")
    subject = ids["/components/b/operations/run"] + "/value"
    return left, right, subject


def request(inputs, left, right, subject, command_id="transplant-analysis"):
    return {**support.request(inputs, left, right, command_id=command_id), "selected_subjects": [subject]}


def command(core, value):
    return json.loads(core.object_store.path_for_version(value.command_ref.ref.resource_version_id).read_bytes())


def test_real_selected_transplant_branch_and_actual_assembly_reopen(fixture, monkeypatch):
    inputs, analyzer, author = fixture
    core, gateway, selected = inputs[:3]
    left, right, subject = produce_inputs(inputs)
    analysis = analyzer.analyze(**request(inputs, left, right, subject))
    assert analysis.document["conflicts"] == analysis.document["selection_gaps"] == []
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="transplant")
    assert type(result) is ValidatedPlainTransplantRevision
    assert result.revision.parent_revision_refs == (left.revision.revision_ref,)
    assert len(result.revision.selected_change_refs) == 1
    assert result.module.components[0].operations[0].config == {}
    assert result.module.components[1].name == "local_name"
    assert result.module.components[1].operations[0].config == {"value": "selected donor change"}
    record = command(core, result)
    assert record["selected_subjects"] == [subject]
    assert record["incoming_revision_ref"] == right.revision.revision_ref.to_dict()
    assert record["base_revision_ref"] == inputs[5].revision.revision_ref.to_dict()
    assert record["dispositions"][0]["disposition"] == "imported"
    proof = json.loads(core.object_store.path_for_version(result.revision.selected_change_refs[0].ref.resource_version_id).read_bytes())
    assert proof["subject"] == subject and proof["incoming"] == proof["result"]
    assert proof["result_element"]["locator"] == "/components/local_name/operations/run"
    first = gateway.create_author_branch(head_revision_ref=left.revision.revision_ref, command_id="branch-create")
    next_branch = gateway.advance_author_branch(expected_branch_version_ref=first.branch_ref,
        expected_head_revision_ref=left.revision.revision_ref, expected_stream_head=first.sequence,
        next_revision_ref=result.revision.revision_ref, command_id="branch-transplant")
    assert current_branch(core, first.branch_ref.ref.entity_id) == next_branch
    assert read_branch_version(core, first.branch_ref) == first
    assembly = AssemblyAuthorV2(gateway, selected, analyzer.producer).publish(**assembly_request(result, command_id="assembly-transplant"))
    assert type(assembly.generated) is ValidatedClosedRevision
    assert {row["element_id"] for row in assembly.lowering_map["origins"]} == {row["element_id"] for row in result.element_map["elements"]}
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    connect, calls = reader.event_store.connect, []
    def counted(*args, **kwargs):
        calls.append("read-cut")
        return connect(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    checked = validate_assembly_revision(reader, assembly.revision.revision_ref, support.registration())
    assert calls == ["read-cut"]
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(assembly.compiled.to_dict())
    assert checked.generated.revision == assembly.generated.revision
    for version in (first, next_branch):
        saved = read_branch_version(reader, version.branch_ref)
        assert validate_closed_revision(reader, saved.head_revision_ref, support.registration()).revision.revision_ref == version.head_revision_ref
    before = support.counts(core)
    assert author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="transplant").revision == result.revision
    assert current_branch(core, first.branch_ref.ref.entity_id) == next_branch
    assert support.counts(core) == before
    assert_author_only(core)
