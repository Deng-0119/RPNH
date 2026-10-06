"""Inert real resolution/result production with exact author proof reconstruction."""
from copy import deepcopy

import pytest

import test_collaboration_plain_merge as support
from cpn.rpnh.collaboration import (
    PlainModuleMergeAuthor, ValidatedPlainMergeRevision, plain_merge_result_schema_data,
    validate_closed_revision,
)
from cpn.rpnh.registry._registry import _RegistryCore


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(support, "plain_merge_schema_data", plain_merge_result_schema_data)
    values = support.fixture.__wrapped__(tmp_path, monkeypatch)
    author = PlainModuleMergeAuthor(values[1], values[2], values[4].producer)
    return values, author


def test_real_result_combines_rename_content_reads_and_replays_without_runtime(fixture):
    inputs, author = fixture
    core, _, selected, _, analyzer, base, ids = inputs
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["name"] = "renamed"
    left_doc["entry"]["second"]["component"] = "renamed"
    left_ids = {p.replace("/components/b", "/components/renamed"): value for p, value in ids.items()}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "explicit incoming fixture edit"}
    left = support.publish(inputs, left_doc, "left", left_ids)
    right = support.publish(inputs, right_doc, "right")
    analysis = analyzer.analyze(**support.request(inputs, left, right))
    assert analysis.document["conflicts"] == []
    before = support.counts(core)
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge:result")
    assert isinstance(result, ValidatedPlainMergeRevision)
    assert result.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert result.revision.selected_change_refs == ()
    assert result.module.components[1].name == "renamed"
    assert result.module.components[1].operations[0].config == right_doc["components"][1]["operations"][0]["config"]
    assert result.module.entry["second"].component == "renamed"
    assert {row["locator"]: row["element_id"] for row in result.element_map["elements"]} == left_ids
    assert all(row["copied_from"] is None for row in result.element_map["elements"])
    assert support.counts(core)[0] == before[0] + 7  # complete command + resolution + four materials + M
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert validate_closed_revision(reader, result.revision.revision_ref, support.registration()).module.to_dict() == result.module.to_dict()
    frozen = support.counts(core)
    assert author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="merge:result").revision == result.revision
    assert support.counts(core) == frozen
    support.assert_inert(core, 4)
