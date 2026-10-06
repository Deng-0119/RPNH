"""Real merge ancestry and one public full-read snapshot, without runtime entry."""
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_result import fixture
from cpn.rpnh.collaboration import UnresolvedPlainMerge, read_plain_merge_analysis, validate_closed_revision
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json


def test_real_two_parent_proofs_expose_multiple_nearest_bases_without_a_result(fixture):
    inputs, author = fixture
    core, _, _, _, analyzer, base, _ = inputs
    left_doc, right_doc = base.module.to_dict(), base.module.to_dict()
    left_doc["name"] = "Left"
    right_doc["components"][1]["operations"][0]["config"] = {"value": "incoming fixture choice"}
    left = support.publish(inputs, left_doc, "left")
    right = support.publish(inputs, right_doc, "right")
    first = analyzer.analyze(**support.request(inputs, left, right, command_id="left-right"))
    second = analyzer.analyze(**support.request(inputs, right, left, command_id="right-left"))
    assert first.document["conflicts"] == second.document["conflicts"] == []
    m1 = author.publish(analysis_ref=first.analysis_ref, choices=[], command_id="first-merge")
    m2 = author.publish(analysis_ref=second.analysis_ref, choices=[], command_id="second-merge")
    assert m1.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert m2.revision.parent_revision_refs == (right.revision.revision_ref, left.revision.revision_ref)
    unresolved = analyzer.analyze(local_ref=m1.revision.revision_ref, incoming_ref=m2.revision.revision_ref,
                                  command_id="real-multiple-bases")
    assert unresolved.document["status"] == "multiple_bases"
    expected = {left.revision.revision_ref, right.revision.revision_ref}
    actual = {support.SourceQualifiedVersionRef.from_dict(row, catalog=core.catalog)
              for row in unresolved.document["nearest_common_bases"]}
    assert actual == expected and unresolved.document["base_revision_ref"] is None
    assert unresolved.document["normalized"] == {}
    assert unresolved.document["differences"] == unresolved.document["conflicts"] == []
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_plain_merge_analysis(reader, unresolved.analysis_ref, support.registration()).document == unresolved.document
    before = support.counts(core)
    with pytest.raises(UnresolvedPlainMerge, match="unique common base"):
        author.publish(analysis_ref=unresolved.analysis_ref, choices=[], command_id="unresolved-result")
    assert support.counts(core) == before
    support.assert_inert(core, 5)


def test_public_nested_merge_reader_uses_one_snapshot_and_rejects_proof_cycle(fixture, monkeypatch):
    from test_collaboration_plain_merge_result_integrity import simple_analysis
    inputs, author = fixture
    analysis = simple_analysis(inputs)
    first = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="first-cut")
    doc = inputs[5].module.to_dict()
    doc["components"][1]["operations"][0]["config"] = {"value": "third branch"}
    third = support.publish(inputs, doc, "third")
    nested = inputs[4].analyze(local_ref=first.revision.revision_ref,
        incoming_ref=third.revision.revision_ref, command_id="nested-analysis")
    assert nested.document["conflicts"] == []
    value = author.publish(analysis_ref=nested.analysis_ref, choices=[], command_id="nested-result")
    core = inputs[0]
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    original = reader.event_store.connect
    calls = []
    def counted(*args, **kwargs):
        calls.append("connect")
        return original(*args, **kwargs)
    monkeypatch.setattr(reader.event_store, "connect", counted)
    before = support.counts(core)
    result = validate_closed_revision(reader, value.revision.revision_ref, support.registration())
    assert calls == ["connect"]
    assert result.revision == value.revision
    assert support.counts(core) == before
    path = core.object_store.path_for_version(nested.analysis_ref.ref.resource_version_id)
    raw = path.read_bytes()
    cyclic = json.loads(raw)
    cyclic["request"]["local_revision_ref"] = value.revision.revision_ref.to_dict()
    changed = canonical_json(cyclic)
    assert len(changed) == len(raw) and changed != raw
    calls.clear()
    try:
        # An actual schema-valid on-disk request now points back at its result.
        # The active revision context must reject before any recursive re-read.
        path.write_bytes(changed)
        with pytest.raises(RegistryConflict, match="cycle"):
            validate_closed_revision(reader, value.revision.revision_ref, support.registration())
        assert calls == ["connect"]
        assert support.counts(core) == before
    finally:
        path.write_bytes(raw)
    calls.clear()
    assert validate_closed_revision(reader, value.revision.revision_ref, support.registration()).revision == value.revision
    assert calls == ["connect"]
