"""Merge-local actual authority closure, preserving the legacy reader boundary."""
import pytest

from test_collaboration_plain_merge import fixture, registration, counts
from cpn.rpnh.collaboration import (
    PlainModuleMergeAnalyzer, SourceQualifiedVersionRef, read_plain_merge_analysis,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json

ERRORS = (RegistryConflict, ObjectIntegrityError, ValueError)


def test_all_four_actual_binding_authorities_reject_before_read_publish_and_setup(fixture):
    core, gateway, _, _, analyzer, base, _ = fixture
    selected = registration()
    request = {"local_ref": base.revision.revision_ref, "incoming_ref": base.revision.revision_ref}
    good = analyzer.analyze(**request, command_id="authority:control")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_plain_merge_analysis(reader, good.analysis_ref, selected).document == good.document
    for role in ("binding_ref", "native_run_ref", "task_ref", "bootstrap_command_ref"):
        path = core.object_store.path_for_version(_version_from_payload(analyzer.binding[role]).version_id)
        original = path.read_bytes()
        before = counts(core)
        try:
            # Still parseable JSON with the same data; exact immutable bytes differ.
            path.write_bytes(original + b" ")
            with pytest.raises(ERRORS):
                read_plain_merge_analysis(reader, good.analysis_ref, selected)
            with pytest.raises(ERRORS):
                analyzer.analyze(**request, command_id="authority:bad:" + role)
            with pytest.raises(ERRORS):
                PlainModuleMergeAnalyzer(gateway, registration(), analyzer.producer)
            assert counts(core) == before
        finally:
            path.write_bytes(original)
        assert read_plain_merge_analysis(reader, good.analysis_ref, selected).document == good.document
    assert analyzer.analyze(**request, command_id="authority:control") == good


def test_distinct_configured_producer_is_exactly_checked_before_analysis_writes(fixture):
    core, gateway, selected, _, _, base, _ = fixture
    ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Distinct merge author"}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="authority:distinct-principal")
    producer = SourceQualifiedVersionRef("plain-source", ref)
    analyzer = PlainModuleMergeAnalyzer(gateway, selected, producer)
    request = {"local_ref": base.revision.revision_ref, "incoming_ref": base.revision.revision_ref}
    good = analyzer.analyze(**request, command_id="authority:distinct-control")
    assert good.document["producer_principal_ref"] == producer.to_dict()
    assert good.document["producer_principal_ref"] != base.revision.producer_principal_ref.to_dict()
    path = core.object_store.path_for_version(ref.version_id)
    original = path.read_bytes()
    before = counts(core)
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ERRORS):
            read_plain_merge_analysis(core, good.analysis_ref, selected)
        with pytest.raises(ERRORS):
            analyzer.analyze(**request, command_id="authority:distinct-bad")
        assert counts(core) == before
    finally:
        path.write_bytes(original)
    assert read_plain_merge_analysis(core, good.analysis_ref, selected).document == good.document
    assert analyzer.analyze(**request, command_id="authority:distinct-control") == good
