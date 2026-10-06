"""Versioned preserved-basis data only: no adopted authority or runtime claim."""
from copy import deepcopy
import json

import pytest

from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from test_candidate_record_contracts import grammar, record_ref


def test_explicit_v2_catalog_keeps_v1_and_default_entry_unchanged():
    from cpn.rpnh.collaboration import candidate_schema_data, candidate_v2_schema_data
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, MANIFEST_V2_TYPE, READINESS_V2_TYPE
    for data in (candidate_schema_data(),):
        old = SchemaCatalog(schemas=data[0], types=data[1], schema_paths=data[2])
        for kind in (PLAN_V2_TYPE, MANIFEST_V2_TYPE, READINESS_V2_TYPE):
            with pytest.raises(SchemaGovernanceError):
                old.require(kind, category="object")
    data = candidate_v2_schema_data()
    catalog = SchemaCatalog(schemas=data[0], types=data[1], schema_paths=data[2])
    for kind in (PLAN_V2_TYPE, MANIFEST_V2_TYPE, READINESS_V2_TYPE):
        catalog.require(kind, category="object")
        catalog.require(kind[:-1] + "1", category="object")
        with pytest.raises(SchemaGovernanceError):
            SchemaCatalog().require(kind, category="object")


def test_v2_draft_freezes_explicit_event_basis(grammar):
    from cpn.rpnh.registry.preserved_binding_contracts import (
        PLAN_V2_TYPE, MANIFEST_V2_TYPE, PLAN_V2_SCHEMA, PreservedBasis, RuntimeBindingManifestDraftV2,
    )
    _, _, _, source = grammar
    plan, manifest = record_ref(PLAN_V2_TYPE), record_ref(MANIFEST_V2_TYPE)
    basis = PreservedBasis(VersionRef("net_instance/v1", new_id("net_instance"), new_id("net_instance_version")),
        new_id("event"), new_id("transaction"), 1, "a" * 64)
    slot = VersionRef("logical_artifact_slot/v1", new_id("logical_slot"), new_id("logical_slot_version"))
    document = deepcopy(source)
    document.update(schema_version=PLAN_V2_SCHEMA, plan_ref=ref_payload(plan), manifest_ref=ref_payload(manifest),
        preserved_slot_refs={"step.result_slot": ref_payload(slot)}, preserved_basis=basis.to_dict())
    draft = RuntimeBindingManifestDraftV2.from_document(plan, manifest, document)
    document["preserved_basis"]["adoption_event"]["task_control_sequence"] = 2
    assert draft.preserved_basis == basis
    assert draft.plan["preserved_basis"]["adoption_event"]["task_control_sequence"] == 1


@pytest.fixture(scope="module")
def v2_grammar(grammar):
    from cpn.rpnh.collaboration import candidate_v2_schema_data
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, MANIFEST_V2_TYPE, PLAN_V2_SCHEMA, PreservedBasis
    data = candidate_v2_schema_data()
    catalog = SchemaCatalog(schemas=data[0], types=data[1], schema_paths=data[2])
    plan, manifest = record_ref(PLAN_V2_TYPE), record_ref(MANIFEST_V2_TYPE)
    basis = PreservedBasis(VersionRef("net_instance/v1", new_id("net_instance"), new_id("net_instance_version")),
        new_id("event"), new_id("transaction"), 1, "a" * 64)
    slot = VersionRef("logical_artifact_slot/v1", new_id("logical_slot"), new_id("logical_slot_version"))
    document = deepcopy(grammar[3])
    document.update(schema_version=PLAN_V2_SCHEMA, plan_ref=ref_payload(plan), manifest_ref=ref_payload(manifest),
        preserved_slot_refs={"step.slot": ref_payload(slot)}, preserved_basis=basis.to_dict())
    return catalog, plan, manifest, document


@pytest.mark.parametrize("damage", ["missing", "null", "empty", "empty_slots", "event_type", "event_id_kind", "event_ref",
    "transaction_kind", "wrong_net_type", "wrong_slot_type", "basis_extra", "event_extra", "slot_alias_field", "uppercase_digest", "digest_newline"])
def test_v2_closed_grammar_and_typed_draft_reject_bad_preservation(v2_grammar, damage):
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, RuntimeBindingManifestDraftV2
    catalog, plan, manifest, original = v2_grammar
    document = deepcopy(original)
    basis = document["preserved_basis"]
    if damage == "missing":
        document.pop("preserved_basis")
    elif damage == "null":
        document["preserved_basis"] = None
    elif damage == "empty":
        document["preserved_basis"] = {}
    elif damage == "empty_slots":
        document["preserved_slot_refs"] = {}
    elif damage == "event_type":
        basis["adoption_event"]["event_type"] = "object_version_published/v1"
    elif damage == "event_id_kind":
        basis["adoption_event"]["event_id"] = str(new_id("resource_version"))
    elif damage == "event_ref":
        basis["adoption_event"]["event_id"] = basis["net_ref"]
    elif damage == "transaction_kind":
        basis["adoption_event"]["transaction_id"] = str(new_id("event"))
    elif damage == "wrong_net_type":
        basis["net_ref"]["entity_type"] = "plan_version/v1"
    elif damage == "wrong_slot_type":
        next(iter(document["preserved_slot_refs"].values()))["entity_type"] = "resource_version/v1"
    elif damage == "basis_extra":
        basis["execution_permitted"] = True
    elif damage == "event_extra":
        basis["adoption_event"]["current_head"] = True
    elif damage == "slot_alias_field":
        next(iter(document["preserved_slot_refs"].values()))["current"] = True
    elif damage == "uppercase_digest":
        basis["adoption_event_sha256"] = "A" * 64
    else:
        basis["adoption_event_sha256"] += "\n"
    with pytest.raises(SchemaGovernanceError):
        catalog.validate_instance(PLAN_V2_TYPE, category="object", instance=document)
    with pytest.raises((TypeError, ValueError)):
        RuntimeBindingManifestDraftV2.from_document(plan, manifest, document)


@pytest.mark.parametrize("sequence,grammar_accepts", [(True, False), (False, False), (1.0, True), (2.0, True),
    (1.5, False), (0, False), (-1, False), ("1", False), (None, False)])
def test_sequence_distinguishes_draft7_integer_from_typed_builtin_contract(v2_grammar, sequence, grammar_accepts):
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, RuntimeBindingManifestDraftV2
    catalog, plan, manifest, source = v2_grammar
    document = deepcopy(source)
    document["preserved_basis"]["adoption_event"]["task_control_sequence"] = sequence
    if grammar_accepts:
        # JSON Schema defines 1 and 1.0 as the same mathematical integer.
        # The actual v2 typed consumer is a stricter, separate mandatory step.
        catalog.validate_instance(PLAN_V2_TYPE, category="object", instance=document)
    else:
        with pytest.raises(SchemaGovernanceError):
            catalog.validate_instance(PLAN_V2_TYPE, category="object", instance=document)
    with pytest.raises(TypeError, match="builtin integer"):
        RuntimeBindingManifestDraftV2.from_document(plan, manifest, document)


def test_empty_v2_basis_is_explicit_null_not_missing(v2_grammar):
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, RuntimeBindingManifestDraftV2
    catalog, plan, manifest, source = v2_grammar
    document = deepcopy(source)
    document.update(preserved_basis=None, preserved_slot_refs={})
    catalog.validate_instance(PLAN_V2_TYPE, category="object", instance=document)
    draft = RuntimeBindingManifestDraftV2.from_document(plan, manifest, document)
    assert draft.preserved_basis is None
    with pytest.raises((TypeError, ValueError)):
        RuntimeBindingManifestDraftV2.from_document(plan, manifest, {key: value for key, value in document.items() if key != "preserved_basis"})


@pytest.mark.parametrize("field", ["event_id", "transaction_id", "net_id", "net_version", "net_type", "digest", "sequence"])
def test_basis_never_coerces_fake_mutable_identity_data(v2_grammar, field):
    from cpn.rpnh.registry.preserved_binding_contracts import PreservedBasis
    from cpn.rpnh.registry.identities import TypedId
    class Fake:
        kind = "event"
        def __str__(self):
            raise AssertionError("fake identity must not be coerced")
        def __repr__(self):
            raise AssertionError("fake identity must not be coerced")
    source = PreservedBasis.from_document(v2_grammar[3]["preserved_basis"])
    args = [source.net_ref, source.event_id, source.transaction_id, source.task_control_sequence, source.adoption_event_sha256]
    if field in ("net_id", "net_version", "net_type"):
        args[0] = VersionRef(Fake() if field == "net_type" else "net_instance/v1",
            Fake() if field == "net_id" else source.net_ref.entity_id,
            Fake() if field == "net_version" else source.net_ref.version_id)
    else:
        args[{"event_id": 1, "transaction_id": 2, "sequence": 3, "digest": 4}[field]] = Fake()
    with pytest.raises((TypeError, ValueError)):
        PreservedBasis(*args)


def test_basis_copies_standard_ids_and_returns_detached_documents(v2_grammar):
    from cpn.rpnh.registry.preserved_binding_contracts import PreservedBasis
    from dataclasses import FrozenInstanceError
    source = PreservedBasis.from_document(v2_grammar[3]["preserved_basis"])
    target = PreservedBasis(source.net_ref, source.event_id, source.transaction_id, source.task_control_sequence, source.adoption_event_sha256)
    expected = target.to_dict()
    object.__setattr__(source.event_id, "value", "b" * 32)
    object.__setattr__(source.net_ref.entity_id, "value", "c" * 32)
    output = target.to_dict()
    output["adoption_event"]["task_control_sequence"] = 100
    assert target.to_dict() == expected
    with pytest.raises(FrozenInstanceError):
        target.task_control_sequence = 2


@pytest.mark.parametrize("mode", ["v1_plan_v2_manifest", "v2_plan_v1_manifest", "v1_decoder_v2", "v2_decoder_v1", "unknown_v99"])
def test_typed_draft_versions_are_explicit_not_family_wide(v2_grammar, grammar, mode):
    from cpn.rpnh.registry.runtime_binding_contracts import RuntimeBindingManifestDraft, MANIFEST_TYPE
    from cpn.rpnh.registry.preserved_binding_contracts import RuntimeBindingManifestDraftV2
    _, p2, m2, d2 = v2_grammar
    _, p1, m1, d1 = grammar
    if mode == "v1_plan_v2_manifest":
        constructor, args = RuntimeBindingManifestDraft, (p1, m2, d1)
    elif mode == "v2_plan_v1_manifest":
        constructor, args = RuntimeBindingManifestDraftV2, (p2, m1, d2)
    elif mode == "v1_decoder_v2":
        constructor, args = RuntimeBindingManifestDraft, (p2, m2, d2)
    elif mode == "v2_decoder_v1":
        constructor, args = RuntimeBindingManifestDraftV2, (p1, m1, d1)
    else:
        constructor, args = RuntimeBindingManifestDraftV2, (record_ref("collaboration_candidate_plan/v99"), m2, d2)
    with pytest.raises((TypeError, ValueError)):
        constructor.from_document(*args)


def _v2_linked_documents(v2_grammar):
    from cpn.rpnh.registry.preserved_binding_contracts import MANIFEST_V2_SCHEMA, READINESS_V2_SCHEMA, READINESS_V2_TYPE
    _, plan, manifest, source = v2_grammar
    def typed(kind, logical, version):
        return ref_payload(VersionRef(kind, new_id(logical), new_id(version)))
    name = next(iter(source["host_bindings"]))
    executable = typed("executable_transition_binding/v1", "executable_transition_binding", "executable_transition_binding_version")
    manifest_doc = {"schema_version": MANIFEST_V2_SCHEMA, "manifest_ref": ref_payload(manifest), "plan_ref": ref_payload(plan),
        "source_id": source["source_id"], "author_ref": source["author_ref"], "task_ref": source["owner_task_ref"],
        "run_ref": source["run_identity"]["run_ref"], "task_round_ref": source["task_round_ref"],
        "net_ref": source["preserved_basis"]["net_ref"], "root_ref": typed("team_design_root/v1", "team_design_root", "team_design_root_version"),
        "declaration_ref": next(iter(source["schema_refs"].values())), "object_refs": [], "entries": [{"transition_id": name,
            "executable_ref": executable, "operation_binding_ref": typed("operation_binding/v1", "operation_binding", "operation_binding_version"),
            "operation_spec_ref": next(iter(source["operation_refs"].values())), "host_binding": source["host_bindings"][name], "dependencies": {}}]}
    readiness = {"schema_version": READINESS_V2_SCHEMA, "readiness_ref": ref_payload(record_ref(READINESS_V2_TYPE)),
        "plan_ref": ref_payload(plan), "manifest_ref": ref_payload(manifest), "check_kind": "offline_static_preparation",
        "execution_permission_checked": False, "checks": [{"transition_id": name, "executable_ref": executable,
            "registry_resolution": "resolved", "static_scope": "matched", "execution_handle": "not_checked",
            "runtime_capacity": "not_checked", "reservation_state": "not_reserved", "blocking_requirements": []}]}
    return manifest_doc, readiness


@pytest.mark.parametrize("record,field", [("manifest", "plan_ref"), ("manifest", "manifest_ref"),
    ("readiness", "plan_ref"), ("readiness", "manifest_ref"), ("readiness", "readiness_ref")])
def test_v2_linked_record_grammars_reject_mixed_versions(v2_grammar, record, field):
    from cpn.rpnh.registry.preserved_binding_contracts import MANIFEST_V2_TYPE, READINESS_V2_TYPE
    catalog = v2_grammar[0]
    manifest, readiness = _v2_linked_documents(v2_grammar)
    kind, document = (MANIFEST_V2_TYPE, manifest) if record == "manifest" else (READINESS_V2_TYPE, readiness)
    catalog.validate_instance(kind, category="object", instance=document)
    document[field]["entity_type"] = document[field]["entity_type"][:-1] + "1"
    with pytest.raises(SchemaGovernanceError):
        catalog.validate_instance(kind, category="object", instance=document)


def test_v2_schema_is_self_contained_and_preserves_v1_wire_and_host_embeds(v2_grammar, monkeypatch):
    import urllib.request
    from cpn.rpnh.collaboration import candidate_v2_schema_data, candidate_schema_data
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, PLAN_V2_SCHEMA, MANIFEST_V2_TYPE, READINESS_V2_TYPE
    def forbidden(*args, **kwargs):
        raise AssertionError("grammar validation must not retrieve schemas")
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    catalog, _, _, plan = v2_grammar
    catalog.validate_instance(PLAN_V2_TYPE, category="object", instance=plan)
    manifest, readiness = _v2_linked_documents(v2_grammar)
    catalog.validate_instance(MANIFEST_V2_TYPE, category="object", instance=manifest)
    catalog.validate_instance(READINESS_V2_TYPE, category="object", instance=readiness)
    old, _, _ = candidate_schema_data()
    new, _, _ = candidate_v2_schema_data()
    assert all(canonical_json(new[key]) == canonical_json(value) for key, value in old.items())
    for field in ("compiled_wire", "author_host"):
        assert canonical_json(new[PLAN_V2_SCHEMA]["definitions"][field]) == canonical_json(old["registry_v1/collaboration_candidate_plan/v1"]["definitions"][field])


def test_adding_v2_grammar_does_not_enable_runtime_or_split_command_ids(tmp_path, monkeypatch):
    import cpn.rpnh.collaboration as collaboration
    import test_candidate_plan_persistence as persistence
    from cpn.rpnh.collaboration.candidate_plans import _command_key, _record_ref, read_candidate_plan
    from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE, MANIFEST_TYPE, is_runtime_manifest_type
    from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, MANIFEST_V2_TYPE
    monkeypatch.setattr(collaboration, "candidate_schema_data", collaboration.candidate_v2_schema_data)
    f = persistence.plan_fixture.__wrapped__(tmp_path)
    revision = persistence.authored(f)
    request = persistence.candidate_request(f, revision)
    first = f[-1].publish(**request)
    assert first.plan_ref.entity_type == PLAN_TYPE
    assert first == f[-1].publish(**request)
    key = _command_key(f[0].task_id, "source-a", request["command_id"])
    for older, newer, suffix in ((PLAN_TYPE, PLAN_V2_TYPE, ":plan"), (MANIFEST_TYPE, MANIFEST_V2_TYPE, ":manifest")):
        a, b = _record_ref(older, key + suffix), _record_ref(newer, key + suffix)
        assert a.entity_id == b.entity_id and a.version_id == b.version_id
    with pytest.raises(TypeError):
        read_candidate_plan(f[0], _record_ref(PLAN_V2_TYPE, key + ":plan"))
    slot = VersionRef("logical_artifact_slot/v1", new_id("logical_slot"), new_id("logical_slot_version"))
    with pytest.raises(ValueError, match="not supported"):
        f[-1].publish(**dict(request, preserved_slot_refs={"step.slot": slot}))
    assert f[0].event_store.object_rows_by_type(PLAN_V2_TYPE) == ()
    assert is_runtime_manifest_type(MANIFEST_V2_TYPE)
    assert is_runtime_manifest_type("runtime_binding_manifest/v99")
    assert not hasattr(collaboration, "CandidatePublisher")  # Original18 design tests remain genuinely unimplemented.
