"""Genuine B/L/R member competition, full merged history and actual next author."""
from copy import deepcopy
from dataclasses import replace
import json
import uuid

import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyMemberV2, AssemblyConnection, AssemblyCompletion, ClosedModuleAuthor,
    AssemblyMergeAnalyzer, AssemblyMergeAuthor, AssemblyAuthorV6, ValidatedAssemblyRevisionV6,
    ValidatedClosedRevision, UnresolvedAssemblyMerge, SourceQualifiedVersionRef,
    assembly_merge_schema_data, validate_assembly_revision, read_assembly_revision,
    read_assembly_merge_analysis, validate_closed_revision,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from test_collaboration_plain_merge import registration as _registration, module as _plain_module
from test_collaboration_assembly_v2_publication import assert_no_run, counts

A, B = ("member:" + c * 32 for c in "ab")


def _simple_module():
    document = _plain_module().to_dict()
    document["components"] = document["components"][:1]
    document["components"][0]["name"] = "step"
    document["entry"] = {"request": {"component": "step", "port": "request"}}
    document["exit"] = {"result": {"component": "step", "port": "result"}}
    document["terminal"]["source"]["component"] = "step"
    binding = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
    document["components"][0]["operations"][0]["budget_binding"] = binding
    document["budget_buckets"] = [{**binding, "max_attempts": 3}]
    return ModuleDeclaration.from_dict(document)


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = assembly_merge_schema_data()
    core = _RegistryCore(tmp_path / "assembly-merge", create=True, catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("assembly-merge-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-assembly-merge", command_id="bind")
    ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Merge fixture"}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1", idempotency_key="principal")
    producer = SourceQualifiedVersionRef("source-assembly-merge", ref)
    registration = _registration()
    leaf_author = ClosedModuleAuthor(gateway, registration, producer)
    module = _simple_module()
    paths = ("/", "/terminal", "/components/step", "/components/step/ports/request", "/components/step/ports/result",
        "/components/step/operations/run", "/entry/request", "/exit/result")
    ids = {path: "element:" + uuid.uuid5(uuid.NAMESPACE_URL, "assembly-merge:" + path).hex for path in paths}
    r1 = leaf_author.publish(module=module, element_ids=ids, command_id="member:r1")
    print("ASSEMBLY_MERGE_STAGE=member-root", flush=True)
    leaves = [r1]
    for side in ("left", "right"):
        document = module.to_dict()
        document["components"][0]["operations"][0]["config"] = {"internal_revision": side}
        leaves.append(leaf_author.publish(module=ModuleDeclaration.from_dict(document), element_ids=ids,
            parent_ref=r1.revision.revision_ref, command_id="member:" + side))
    v2 = AssemblyAuthorV2(gateway, registration, producer)
    base_args = request(r1, r1, command="B")
    base = v2.publish(**base_args)
    print("ASSEMBLY_MERGE_STAGE=base", flush=True)
    left = v2.publish(**request(leaves[1], r1, command="L", parent=base))
    print("ASSEMBLY_MERGE_STAGE=left", flush=True)
    right = v2.publish(**request(leaves[2], r1, command="R", parent=base))
    print("ASSEMBLY_MERGE_STAGE=right", flush=True)
    analyzer = AssemblyMergeAnalyzer(gateway, registration, producer)
    author = AssemblyMergeAuthor(gateway, registration, producer)
    return core, gateway, registration, producer, leaf_author, leaves, v2, base, left, right, analyzer, author


def request(first, second, *, command, parent=None):
    ids = lambda leaf: {row["locator"]: row["element_id"] for row in leaf.element_map["elements"]}
    result = {"name": "MergedPair", "members": (AssemblyMemberV2(A, "Same", first.revision.revision_ref),
        AssemblyMemberV2(B, "Same", second.revision.revision_ref)), "connections": (
        AssemblyConnection(A, ids(first)["/exit/result"], B, ids(second)["/entry/request"]),),
        "completion": AssemblyCompletion(B, ids(second)["/terminal"]), "budget_policy": "shared_exact",
        "deployment_intent": "same_run_candidate", "command_id": command}
    if parent is not None:
        result["parent_ref"] = parent.revision.revision_ref
    return result


def analyze(fixture, *, left=None, right=None, command="analysis"):
    return fixture[10].analyze(local_ref=(left or fixture[8]).revision.revision_ref,
        incoming_ref=(right or fixture[9]).revision.revision_ref, command_id=command)


def choices(analysis, side="left"):
    return [{"subject": row["subject"], "choice": side, "reason": "Caller retained the " + side + " contract"}
        for row in analysis.document["atoms"] if row["conflict"]]


def publish(fixture, analysis, decisions=None, command="M"):
    return fixture[11].publish(analysis_ref=analysis.analysis_ref, choices=choices(analysis) if decisions is None else decisions,
        generated_continuity="left", command_id=command)


def reopen(core, value):
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    checked = validate_assembly_revision(reader, value.revision.revision_ref, _registration())
    assert read_assembly_revision(reader, value.revision.revision_ref) == value.revision
    assert canonical_json(checked.plan) == canonical_json(value.plan)
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert type(validate_closed_revision(reader, value.revision.generated_revision_ref, _registration())) is ValidatedClosedRevision
    return checked


@pytest.mark.parametrize("side", ["left", "right", "base", "exact"])
def test_real_member_conflict_full_merge_and_actual_next_author(fixture, side):
    core, gateway, registration, producer, _, leaves, _, base, left, right, _, _ = fixture
    analysis = analyze(fixture)
    print("ASSEMBLY_MERGE_STAGE=analysis", flush=True)
    assert analysis.document["base_revision_ref"] == base.revision.revision_ref.to_dict()
    assert len(analysis.document["ancestry"]) == 3
    conflict = next(row for row in analysis.document["atoms"] if row["subject"] == "member/" + A)
    assert conflict["reason"] == "member_version_competition"
    assert [conflict[key]["revision_ref"] for key in ("base", "left", "right")] == [leaf.revision.revision_ref.to_dict() for leaf in leaves]
    decisions = choices(analysis, side if side != "exact" else "left")
    if side == "exact":
        decision = next(row for row in decisions if row["subject"] == "member/" + A)
        decision.update(choice="exact", value=AssemblyMemberV2(A, "Caller selected", leaves[2].revision.revision_ref).to_dict())
    value = publish(fixture, analysis, decisions)
    print("ASSEMBLY_MERGE_STAGE=merge", flush=True)
    assert type(value) is ValidatedAssemblyRevisionV6
    assert value.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert value.revision.lineage_root_ref == base.revision.revision_ref
    chosen = {"left": 1, "right": 2, "base": 0, "exact": 2}[side]
    assert value.members[A].revision.revision_ref == leaves[chosen].revision.revision_ref
    assert value.members[B].revision.revision_ref == leaves[0].revision.revision_ref
    assert value.generated.revision.parent_revision_refs == (left.revision.generated_revision_ref,)
    assert len(value.generated.module.components) == 2 and len(value.generated.module.links) == 1
    assert len(value.lowering_map["member_provenance"]) == 2
    assert value.lowering_map["assembly_parent_revision_refs"] == [left.revision.revision_ref.to_dict(), right.revision.revision_ref.to_dict()]
    reopen(core, value)
    next_author = AssemblyAuthorV6(gateway, registration, producer)
    successor = next_author.publish(**request(leaves[chosen], leaves[0], command="E", parent=value))
    assert successor.revision.parent_revision_refs == (value.revision.revision_ref,)
    assert successor.generated.revision.parent_revision_refs == (value.revision.generated_revision_ref,)
    reopen(core, successor)
    descendant_analysis = analyze(fixture, left=successor, right=right, command="after-E")
    assert {canonical_json(row["revision_ref"]) for row in descendant_analysis.document["ancestry"]} == {
        canonical_json(item.revision.revision_ref.to_dict()) for item in (base, left, right, value, successor)}
    before = counts(core)
    assert publish(fixture, analysis, decisions).revision == value.revision
    assert counts(core) == before
    assert_no_run(core)
    print("ASSEMBLY_V6_REAL_CHAIN=" + json.dumps({"base": base.revision.revision_ref.to_dict(), "left": left.revision.revision_ref.to_dict(),
        "right": right.revision.revision_ref.to_dict(), "merge": value.revision.to_dict(), "edit": successor.revision.to_dict(),
        "selected_side": side, "actual_components": len(value.generated.module.components), "actual_links": len(value.generated.module.links)}, sort_keys=True))


def test_explicit_member_delete_requires_whole_connection_repair(fixture):
    core = fixture[0]
    analysis = analyze(fixture)
    decisions = choices(analysis)
    next(row for row in decisions if row["subject"] == "member/" + A).update(choice="delete")
    with pytest.raises(ValueError, match="connection"):
        publish(fixture, analysis, decisions, "dangling")
    assert not core.event_store.object_rows_by_type("collaboration_assembly_revision/v6")
    next(row for row in decisions if row["subject"] == "connections").update(choice="exact", value=[])
    value = publish(fixture, analysis, decisions)
    assert list(value.members) == [B]
    assert len(value.generated.module.components) == 1 and not value.generated.module.links
    removed = next(row for row in value.lowering_map["member_provenance"] if row["member_id"] == A)
    assert removed["selected_member"] is None and removed["choice"]["choice"] == "delete"
    reopen(core, value)


def test_genuine_crisscross_and_independent_roots_remain_unresolved(fixture):
    core, _, _, _, _, leaves, v2, _, left, right, _, _ = fixture
    first = publish(fixture, analyze(fixture), command="M1")
    second = publish(fixture, analyze(fixture, left=right, right=left, command="reverse"), command="M2")
    ambiguous = analyze(fixture, left=first, right=second, command="ambiguous")
    assert ambiguous.document["status"] == "unresolved_history"
    assert {canonical_json(ref) for ref in ambiguous.document["nearest_common_bases"]} == {
        canonical_json(item.revision.revision_ref.to_dict()) for item in (left, right)}
    before = counts(core)
    with pytest.raises(UnresolvedAssemblyMerge, match="unique nearest"):
        publish(fixture, ambiguous, [], "no-arbitrary-base")
    assert counts(core) == before
    unrelated = v2.publish(**request(leaves[0], leaves[0], command="independent-root"))
    missing = analyze(fixture, right=unrelated, command="no-base")
    assert missing.document["status"] == "unresolved_history" and missing.document["nearest_common_bases"] == []
    with pytest.raises(UnresolvedAssemblyMerge):
        publish(fixture, missing, [], "unrelated")
    assert_no_run(core)


def test_incomplete_or_unknown_caller_decisions_do_not_publish(fixture):
    analysis = analyze(fixture)
    for damage in ("missing", "duplicate", "unknown", "empty_reason", "wrong_member", "extra_field", "implicit_continuity", "null_exact"):
        decisions = choices(analysis)
        if damage == "missing": decisions.pop()
        elif damage == "duplicate": decisions.append(deepcopy(decisions[0]))
        elif damage == "unknown": decisions[0]["subject"] = "member/unknown"
        elif damage == "empty_reason": decisions[0]["reason"] = "  "
        elif damage == "extra_field": decisions[0]["unrequested"] = True
        elif damage == "null_exact":
            next(row for row in decisions if row["subject"] == "member/" + A).update(choice="exact", value=None)
            # Otherwise the old accidental deletion would fail only for a
            # dangling connection, hiding the missing exact-member check.
            next(row for row in decisions if row["subject"] == "connections").update(choice="exact", value=[])
        elif damage == "wrong_member":
            next(row for row in decisions if row["subject"] == "member/" + A).update(choice="exact",
                value=AssemblyMemberV2(B, "Wrong identity", fixture[5][0].revision.revision_ref).to_dict())
        before = counts(fixture[0])
        rejection = (pytest.raises(UnresolvedAssemblyMerge, match="exact member selection requires a complete member row")
            if damage == "null_exact" else pytest.raises((ValueError, TypeError, RegistryConflict)))
        with rejection:
            if damage == "implicit_continuity":
                fixture[11].publish(analysis_ref=analysis.analysis_ref, choices=decisions, generated_continuity=None, command_id="bad:" + damage)
            else:
                publish(fixture, analysis, decisions, "bad:" + damage)
        after = counts(fixture[0])
        assert after == before
        print("ASSEMBLY_V6_DECISION_REFUSAL=" + json.dumps({"case": damage, "before": before, "after": after,
            "writes": 0, "command_id": "bad:" + damage}, sort_keys=True), flush=True)


def test_assembly_damage_rejects_complete_merge_but_g_is_independent(fixture):
    core = fixture[0]
    analysis = analyze(fixture)
    value = publish(fixture, analysis)
    for reference in (fixture[7].revision.plan_ref, fixture[9].revision.plan_ref, analysis.analysis_ref,
                      analysis.command_ref, value.revision.plan_ref, value.revision.lowering_mapping_ref):
        path = core.object_store.path_for_version(reference.ref.resource_version_id)
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        before = counts(core)
        with pytest.raises((RegistryConflict, ObjectIntegrityError, ValueError)):
            validate_assembly_revision(core, value.revision.revision_ref, _registration())
        assert type(validate_closed_revision(core, value.revision.generated_revision_ref, _registration())) is ValidatedClosedRevision
        assert counts(core) == before
        path.write_bytes(original)
        reopen(core, value)


def test_wrong_source_and_changed_commands_fail(fixture):
    core, _, _, _, _, _, _, _, left, right, analyzer, author = fixture
    before = counts(core)
    with pytest.raises(RegistryConflict, match="local"):
        analyzer.analyze(local_ref=SourceQualifiedVersionRef("foreign", left.revision.revision_ref.ref),
            incoming_ref=right.revision.revision_ref, command_id="foreign")
    assert counts(core) == before
    analysis = analyze(fixture)
    value = publish(fixture, analysis)
    for changes in ("reason", "side"):
        decision = choices(analysis)
        decision[0]["reason" if changes == "reason" else "choice"] = "Changed reason" if changes == "reason" else "right"
        before = counts(core)
        with pytest.raises(RegistryConflict): publish(fixture, analysis, decision)
        assert counts(core) == before
    with pytest.raises(RegistryConflict):
        analyze(fixture, left=right, right=left)
    reopen(core, value)
