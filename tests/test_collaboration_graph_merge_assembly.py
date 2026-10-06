"""Finite real graph merge/edit Assembly-v8 acceptance; author facts only.

This file is prepared statically. Execute only in the separately frozen guarded
node window; it deliberately never starts an executor, provider or runtime.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import (
    AssemblyCompletion, AssemblyConnection, GraphModuleAuthor,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
    make_graph_source, read_assembly_revision, validate_assembly_revision,
    validate_closed_revision,
)
from cpn.rpnh.collaboration import assembly_v8 as assembly
from cpn.rpnh.collaboration.assembly_v2 import AssemblyMemberV2, AssemblyRevisionV2
from cpn.rpnh.collaboration.assembly_v8 import (
    AssemblyAuthorV8, AssemblyMemberV8, AssemblyRevisionV8, ValidatedAssemblyRevisionV8,
)
from cpn.rpnh.collaboration.graph_merge import GraphMergeAnalyzer, ValidatedGraphMergeRevision
from cpn.rpnh.collaboration.graph_merge_author import GraphMergeAuthor
from cpn.rpnh.collaboration.materials import ValidatedClosedRevision
from cpn.rpnh.collaboration.schema_catalog import graph_merge_assembly_schema_data
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_collaboration_graph_materials import registration, assert_author_only
from test_collaboration_graph_source import graph_wire, recipe, source_ids


A, B, C = ("member:" + character * 32 for character in "abc")
CUTS = ("plan", "definition", "element_mapping", "boundary_mapping", "host_requirements",
        "generated_revision", "compiled_inventory", "lowering_mapping", "assembly_revision")


class DurableCut(RuntimeError):
    pass


def _facts(core):
    with core.event_store.connect() as db:
        committed = db.execute("SELECT COUNT(*) FROM transactions WHERE status='committed'").fetchone()[0]
    return len(core.event_store.object_rows()), len(core.event_store.list_events()), committed


def _version(reference):
    return reference.ref.as_version_ref() if isinstance(reference, SourceQualifiedResourceRef) else reference.ref


def _payload(core, reference):
    return core.object_store.path_for_version(_version(reference).version_id).read_bytes()


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = graph_merge_assembly_schema_data()
    core = _RegistryCore(tmp_path / "graph-merge-assembly-v8", create=True,
                         catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("graph-merge-assembly-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-graph-merge-assembly", command_id="fixture:bind")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id),
            "display_name": "Graph merge Assembly fixture"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
        version_id=principal.version_id, payload=canonical_json(body), metadata=body,
        media_type="application/json", schema_ref="registry_v1/principal/v1", idempotency_key="fixture:principal")
    selected = registration()
    producer = SourceQualifiedVersionRef("source-graph-merge-assembly", principal)
    legacy = GraphModuleAuthor(gateway, selected, producer)
    source = make_graph_source(graph_wire(True))
    identities = source_ids(source)
    base = legacy.publish(source=source, recipe=recipe(), source_ids=identities, command_id="graph:base")
    local = deepcopy(source); local["graph"]["nodes"][0]["instruction"] = "Draft a carefully checked result."
    incoming = deepcopy(source); incoming["graph"]["nodes"][1]["instruction"] = "Review all requirements and deliver."
    left = legacy.publish(source=local, recipe=recipe(), source_ids=identities,
                          command_id="graph:left", parent_ref=base.revision.revision_ref)
    right = legacy.publish(source=incoming, recipe=recipe(), source_ids=identities,
                           command_id="graph:right", parent_ref=base.revision.revision_ref)
    analyzer = GraphMergeAnalyzer(gateway, selected, producer)
    analysis = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
                                command_id="graph:analysis")
    assert analysis.document["status"] == "analyzed"
    decisions = {row["subject"]: row["change"] for row in analysis.document["differences"]}
    assert set(decisions.values()) <= {"local", "incoming"}
    choices = [{"conflict_id": row["conflict_id"],
                "selections": [{"subject": subject, "side": decisions[subject]} for subject in row["subjects"]],
                "reason": "Retain both explicitly selected instruction changes in this fixture."}
               for row in analysis.document["conflicts"]]
    graph_author = GraphMergeAuthor(gateway, selected, producer)
    merged = graph_author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="graph:merged")
    changed = deepcopy(merged.source)
    for node in changed["graph"]["nodes"]:
        if node["node_id"] == "draft": node["node_id"] = "author"
    for arc in changed["graph"]["arcs"]:
        for endpoint in (arc["source"], arc["target"]):
            if endpoint["node_id"] == "draft": endpoint["node_id"] = "author"
    changed["graph"]["ingress"]["node_id"] = "author"
    changed_ids = {row["locator"].replace("/nodes/draft", "/nodes/author"): row["element_id"]
                   for row in merged.source_map["elements"]}
    edited = graph_author.publish_edit(source=changed, recipe=merged.recipe, source_ids=changed_ids,
        command_id="graph:edited", parent_ref=merged.revision.revision_ref)
    author = AssemblyAuthorV8(gateway, selected, producer)
    return core, gateway, author, selected, base, left, right, merged, edited


def request(merged, edited, **changes):
    ids = lambda member: {row["locator"]: row["element_id"] for row in member.element_map["elements"]}
    m, e = ids(merged), ids(edited)
    return {"name": "MergedGraphChain", "members": (
                AssemblyMemberV8(A, "Merged first", merged.revision.revision_ref),
                AssemblyMemberV8(B, "Edited", edited.revision.revision_ref),
                AssemblyMemberV8(C, "Merged again", merged.revision.revision_ref)),
            "connections": (AssemblyConnection(A, m["/exit/result"], B, e["/entry/request"]),
                            AssemblyConnection(B, e["/exit/result"], C, m["/entry/request"])),
            "completion": AssemblyCompletion(C, m["/terminal"]), "budget_policy": "shared_exact",
            "deployment_intent": "same_run_candidate", "command_id": "assembly:merged-chain", **changes}


def _stages(author, command_id):
    core, binding = author.core, author.binding
    key = assembly._command(command_id)
    _, generated_key, generated_ref = assembly._generated_ref(core, binding["source_id"], command_id, None)
    values = [(key + ":plan", assembly._material_ref(core, binding, key + ":plan"))]
    values.extend((generated_key + ":material:" + str(index), assembly._material_ref(
        core, binding, generated_key + ":material:" + str(index))) for index in range(4))
    values.append((generated_key, generated_ref))
    values.extend((key + ":" + role, assembly._material_ref(core, binding, key + ":" + role))
                  for role in ("compiled", "lowering"))
    values.append((key, assembly._result_ref(core, binding["source_id"], command_id, None)))
    return [(name, command, ref) for name, (command, ref) in zip(CUTS, values, strict=True)]


def _check_pair(core, value, selected):
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert read_assembly_revision(reader, value.revision.revision_ref) == value.revision
    checked = validate_assembly_revision(reader, value.revision.revision_ref, selected)
    assert type(checked) is ValidatedAssemblyRevisionV8
    assert checked.plan == value.plan and checked.lowering_map == value.lowering_map
    assert canonical_json(checked.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    independent = validate_closed_revision(reader, value.revision.generated_revision_ref, selected)
    assert type(independent) is ValidatedClosedRevision
    assert canonical_json(independent.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert _facts(core) == before
    assert_author_only(core)
    return checked


def test_real_merge_edit_repeated_instances_reopen_exact_pair_and_graph_roles(fixture, monkeypatch):
    core, _, author, selected, base, left, right, merged, edited = fixture
    before = _facts(core)
    from cpn.rpnh.agent_workflows import lower_agent_workflow_graph
    actual_contexts, original_resolve = [], selected.resolve
    def resolve(kind, key):
        function = original_resolve(kind, key)
        if kind != "component": return function
        assert function is lower_agent_workflow_graph
        def lower(config, context):
            result = function(config, context)
            actual_contexts.append(context.component)
            return result
        return lower
    monkeypatch.setattr(selected, "resolve", resolve)
    value = author.publish(**request(merged, edited))
    assert {"m_" + identity.split(":")[1] + "_team" for identity in (A, B, C)} <= set(actual_contexts)
    assert _facts(core) == tuple(a + b for a, b in zip(before, (9, 25, 9), strict=True))
    assert merged.revision.parent_revision_refs == (left.revision.revision_ref, right.revision.revision_ref)
    assert edited.revision.parent_revision_refs == (merged.revision.revision_ref,)
    assert len(value.generated.module.components) == 3 and len(value.generated.module.links) == 2
    expected = {A: merged, B: edited, C: merged}
    assert {row["member_id"] for row in value.lowering_map["graph_members"]} == set(expected)
    for row in value.plan["members"]:
        member = expected[row["member_id"]]
        assert row["resolution"]["kind"] == "merged_graph_v3"
        assert row["resolution"]["author_command_ref"] == member.command_ref.to_dict()
        assert row["resolution"]["resolution_ref"] == member.resolution_ref.to_dict()
    for row in value.lowering_map["graph_source_origins"]:
        member = expected[row["member_id"]]
        assert row["revision_ref"] == member.revision.revision_ref.to_dict()
        assert row["identity_origin"] == member.identity_origins[row["source_element_id"]]
        assert row["compiled_roles"] and row["declaration_field_targets"]
    assert len(value.lowering_map["graph_source_origins"]) == sum(len(m.source_map["elements"]) for m in expected.values())
    assert {row["member_id"] for row in value.lowering_map["graph_fragment_coverage"]} == set(expected)
    assert any(role["role"] == "permit_consume" for row in value.lowering_map["graph_source_origins"] for role in row["compiled_roles"])
    documents = (value.generated.module.to_dict(), value.generated.element_map, value.generated.boundary_map,
                 value.generated.host_requirements, value.compiled.to_dict(), value.lowering_map)
    for signature, document in zip(value.plan["prepared_materials"], documents, strict=True):
        payload = canonical_json(document)
        assert signature["bytes"] == len(payload) and signature["sha256"] == hashlib.sha256(payload).hexdigest()
    _check_pair(core, value, selected)
    after = _facts(core)
    assert author.publish(**request(merged, edited)).revision == value.revision
    assert _facts(core) == after


def test_every_real_committed_cut_reopens_and_replays_complete_v8_plan(fixture, monkeypatch):
    for cut in CUTS:
        fixture = _recover_real_cut(fixture, monkeypatch, cut)
    _recover_descriptor_rollback(fixture, monkeypatch)


def _recover_real_cut(fixture, monkeypatch, cut):
    core, gateway, author, _, _, _, _, merged, edited = fixture
    args = request(merged, edited, command_id="assembly:cut:" + cut)
    stages = _stages(author, args["command_id"])
    assembly_count = len(core.event_store.object_rows_by_type(assembly.ASSEMBLY_V8_TYPE))
    by_key = {key: (name, ref) for name, key, ref in stages}
    committed, original = {}, RegistryTransaction.commit
    def commit(transaction):
        result = original(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key in by_key:
            name, reference = by_key[transaction.idempotency_key]
            assert transaction._closed
            row = core.event_store.object_row(_version(reference).version_id)
            assert row is not None and row["transaction_id"] == str(transaction.transaction_id)
            committed[name] = _payload(core, reference)
            if name == cut: raise DurableCut(cut)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, "commit", commit)
        with pytest.raises(DurableCut, match=cut): author.publish(**args)
    assert list(committed) == list(CUTS[:CUTS.index(cut) + 1])
    after_cut = _facts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    gateway2 = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV8(gateway2, registration(), author.producer)
    assert reopened.writer_epoch > core.writer_epoch and _facts(reopened) == after_cut
    altered = {**args, "members": (replace(args["members"][0], display_name="Changed label"), *args["members"][1:])}
    with pytest.raises(RegistryConflict, match="conflict"): replay.publish(**altered)
    assert _facts(reopened) == after_cut
    value = replay.publish(**args)
    for name, _, reference in stages:
        if name in committed: assert _payload(reopened, reference) == committed[name]
    assert value.plan == json.loads(committed["plan"])
    if cut == "assembly_revision": assert _facts(reopened) == after_cut
    assert len(reopened.event_store.object_rows_by_type(assembly.ASSEMBLY_V8_TYPE)) == assembly_count + 1
    _check_pair(reopened, value, registration())
    print("GRAPH_MERGE_ASSEMBLY_CUT=" + json.dumps({"case": cut, "durable_stages": list(committed),
        "changed_request_rejected": True, "full_readonly_pair": True}, sort_keys=True))
    return (reopened, gateway2, replay, registration(), *fixture[4:])


def _recover_descriptor_rollback(fixture, monkeypatch):
    core, gateway, author, _, _, _, _, merged, edited = fixture
    args = request(merged, edited, command_id="assembly:descriptor-rollback")
    key = assembly._command(args["command_id"])
    ref = assembly._result_ref(core, author.binding["source_id"], args["command_id"], None)
    original, inserted = core.event_store._insert_event, []
    def insert(db, event):
        result = original(db, event)
        if event.event_type == "object_version_published/v1" and event.payload.get("version_id") == str(ref.ref.version_id):
            assert event.idempotency_key == key and db.in_transaction
            assert db.execute("SELECT event_id FROM events WHERE event_id=?", (str(event.event_id),)).fetchone() is not None
            inserted.append(str(event.event_id))
            raise DurableCut("descriptor SQL rollback")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(core.event_store, "_insert_event", insert)
        with pytest.raises(DurableCut, match="descriptor SQL rollback"):
            author.publish(**args)
    assert len(inserted) == 1 and core.event_store.object_row(ref.ref.version_id) is None
    with core.event_store.connect() as db:
        assert db.execute("SELECT event_id FROM events WHERE event_id=?", (inserted[0],)).fetchone() is None
        assert db.execute("SELECT transaction_id FROM transactions WHERE idempotency_key=?", (key,)).fetchone() is None
    plan_ref = assembly._material_ref(core, author.binding, key + ":plan")
    locked = _payload(core, plan_ref)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    replay = AssemblyAuthorV8(RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref),
                              registration(), author.producer)
    value = replay.publish(**args)
    assert _payload(reopened, plan_ref) == locked and value.revision.revision_ref == ref
    _check_pair(reopened, value, registration())
    print("GRAPH_MERGE_ASSEMBLY_CUT=" + json.dumps({"case": "descriptor_transaction_rollback",
        "real_sql_insert_rolled_back": True, "exact_recovery": True}, sort_keys=True))


def test_final_fresh_cut_rejects_dependency_damage_after_real_plan_and_recovers(fixture, monkeypatch):
    for dependency in ("ancestor", "source", "recipe", "host"):
        _reject_final_damage(fixture, monkeypatch, dependency)
    _reject_frozen_lowering_metadata(fixture, monkeypatch)


def _reject_final_damage(fixture, monkeypatch, dependency):
    core, _, author, _, base, _, _, merged, edited = fixture
    args = request(merged, edited, command_id="assembly:fresh:" + dependency)
    target = {"ancestor": base.revision.graph_source_ref,
              "source": merged.revision.graph_source_ref,
              "recipe": merged.revision.graph_recipe_ref,
              "host": SourceQualifiedResourceRef.from_dict(merged.host_requirements["declaration_refs"][0]["resource_ref"], catalog=core.catalog)}[dependency]
    path = core.object_store.path_for_version(_version(target).version_id)
    original_bytes = path.read_bytes()
    key = assembly._command(args["command_id"])
    plan_ref = assembly._material_ref(core, author.binding, key + ":plan")
    result_ref = assembly._result_ref(core, author.binding["source_id"], args["command_id"], None)
    original, seen = RegistryTransaction.commit, []
    def commit(transaction):
        result = original(transaction)
        # Corrupt only after the last following material has really committed:
        # prepare proof and generated G are complete, final A must reread inputs.
        if transaction.event_store is core.event_store and transaction.idempotency_key == key + ":lowering":
            assert core.event_store.object_row(_version(plan_ref).version_id) is not None
            seen.append(_payload(core, plan_ref))
            path.write_bytes(original_bytes[:-1] + bytes([original_bytes[-1] ^ 1]))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, "commit", commit)
        with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
            author.publish(**args)
    assert seen and core.event_store.object_row(result_ref.ref.version_id) is None
    path.write_bytes(original_bytes)
    recovered = author.publish(**args)
    assert _payload(core, plan_ref) == seen[0] and recovered.plan == json.loads(seen[0])
    _check_pair(core, recovered, registration())
    print("GRAPH_MERGE_ASSEMBLY_FRESH_CUT=" + json.dumps({"case": dependency,
        "plan_was_committed": True, "damaged_commit_rejected": True, "exact_recovery": True}, sort_keys=True))


def test_actual_descriptor_commit_rejects_new_owner_epoch_and_recovers(fixture, monkeypatch):
    core, gateway, author, _, _, _, _, merged, edited = fixture
    args = request(merged, edited, command_id="assembly:epoch")
    key = assembly._command(args["command_id"])
    original, next_cores = RegistryTransaction.commit, []
    def commit(transaction):
        if transaction.event_store is core.event_store and transaction.idempotency_key == key:
            next_cores.append(_RegistryCore(core.run_dir, create=False, catalog=core.catalog))
        return original(transaction)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, "commit", commit)
        with pytest.raises(StaleWriterError): author.publish(**args)
    assert len(next_cores) == 1
    reopened = next_cores[0]
    ref = assembly._result_ref(core, author.binding["source_id"], args["command_id"], None)
    assert reopened.event_store.object_row(ref.ref.version_id) is None
    replay = AssemblyAuthorV8(RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref),
                              registration(), author.producer)
    _check_pair(reopened, replay.publish(**args), registration())


def test_full_v8_reader_rejects_damaged_exact_pair_or_transitive_graph_proof(fixture):
    core, _, author, _, _, _, _, merged, edited = fixture
    value = author.publish(**request(merged, edited, command_id="assembly:tamper"))
    targets = {"merged_source": merged.revision.graph_source_ref,
               "merge_command": merged.command_ref, "merge_resolution": merged.resolution_ref,
               "generated": value.generated.revision.definition_ref,
               "mapping": value.revision.lowering_mapping_ref,
               "host": SourceQualifiedResourceRef.from_dict(value.plan["host_requirements"]["declaration_refs"][0]["resource_ref"], catalog=core.catalog)}
    for damage, target in targets.items():
        path = core.object_store.path_for_version(_version(target).version_id)
        payload = path.read_bytes(); path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        before = _facts(core)
        with pytest.raises((ValueError, RegistryConflict, ObjectIntegrityError)):
            validate_assembly_revision(core, value.revision.revision_ref, registration())
        assert _facts(core) == before
        path.write_bytes(payload)
        _check_pair(core, value, registration())
        print("GRAPH_MERGE_ASSEMBLY_READER_DAMAGE=" + json.dumps({"case": damage,
            "rejected_readonly": True, "exact_restore_passed": True}, sort_keys=True))

    _reject_coherent_metadata_substitutions(core, author, value)

def test_same_v8_parent_and_legacy_decoders_remain_exact(fixture):
    core, gateway, author, selected, base, _, _, merged, edited = fixture
    first = author.publish(**request(merged, edited))
    second = author.publish(**request(merged, edited, command_id="assembly:successor", parent_ref=first.revision.revision_ref))
    assert second.revision.revision_ref.ref.entity_id == first.revision.revision_ref.ref.entity_id
    assert second.generated.revision.parent_revision_refs == (first.generated.revision.revision_ref,)
    with pytest.raises((ValueError, TypeError)):
        AssemblyMemberV2(A, "Merged", merged.revision.revision_ref)
    with pytest.raises((ValueError, TypeError, SchemaGovernanceError)):
        AssemblyRevisionV2.from_dict(first.revision.to_dict(), catalog=core.catalog)
    with pytest.raises((ValueError, TypeError)):
        author.publish(**request(merged, edited, command_id="assembly:bad-parent", parent_ref=merged.revision.revision_ref))
    _check_pair(core, second, registration())
    from test_collaboration_assembly_v2_publication import setup_context_plain
    _, plain = setup_context_plain((core, gateway, author, selected, base))
    ids = lambda value: {row["locator"]: row["element_id"] for row in value.element_map["elements"]}
    b, p, m = ids(base), ids(plain), ids(merged)
    mixed = author.publish(name="MixedVersionChain", members=(
        AssemblyMemberV8(A, "Ordinary graph", base.revision.revision_ref),
        AssemblyMemberV8(B, "Plain", plain.revision.revision_ref),
        AssemblyMemberV8(C, "Merged graph", merged.revision.revision_ref)),
        connections=(AssemblyConnection(A, b["/exit/result"], B, p["/entry/request"]),
                     AssemblyConnection(B, p["/exit/result"], C, m["/entry/request"])),
        completion=AssemblyCompletion(C, m["/terminal"]), budget_policy="shared_exact",
        deployment_intent="same_run_candidate", command_id="assembly:all-member-contracts")
    assert [row["resolution"]["kind"] for row in mixed.plan["members"]] == [
        "ordinary_graph_v2", "plain_closed_v1", "merged_graph_v3"]
    assert "test/final-only/v1" in mixed.plan["host_requirements"]["registrations"]["executor"]
    assert all(row["identity_origin"] is None for row in mixed.lowering_map["graph_source_origins"] if row["member_id"] == A)
    _check_pair(core, mixed, selected)


def test_old_graph_proof_cannot_claim_v3_member_eligibility(fixture):
    from cpn.rpnh.collaboration.graph_authoring import ValidatedGraphRevision
    _, _, _, _, _, _, _, merged, _ = fixture
    assert type(merged) is ValidatedGraphMergeRevision
    stripped = ValidatedGraphRevision(merged.revision, merged.module, merged.compiled,
        merged.element_map, merged.boundary_map, merged.host_requirements,
        merged.source, merged.recipe, merged.source_map)
    with pytest.raises(TypeError, match="exact full consumer"):
        assembly._resolution(stripped)


def test_canonical_same_body_generated_and_map_substitutions_fail_exact_pair(fixture):
    core, _, author, selected, _, _, _, merged, edited = fixture
    seed = author.publish(**request(merged, edited, command_id="assembly:pair-seed"))
    for case in ("generated", "mapping"):
        locked = deepcopy(seed.plan); locked["command_id"] = "assembly:pair-claim:" + case
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = assembly._binding_at(db, core)
            members = assembly._members_at(db, core, locked, selected, binding, locked=True)
            prepared = assembly._prepare_at(db, core, locked, members, None, selected, binding)
        plan, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = prepared
        key = assembly._command(plan["command_id"])
        plan_ref = author._publish_document(key + ":plan", assembly.PLAN_V8_SCHEMA, plan,
            descriptors={"assembly_author_command_v8": assembly._envelope(plan, docs)})
        expected_generated = author.author.publish(module=module, element_ids=ids, command_id=generated_command)
        assert expected_generated.revision.revision_ref == generated_ref
        compiled_ref = author._publish_document(key + ":compiled", "rpnh/executable_net/v1", compiled.to_dict())
        mapping_ref = author._publish_document(key + ":lowering", assembly.LOWERING_V8_SCHEMA, mapping)
        if case == "generated":
            alternate = author.author.publish(module=module, element_ids=ids,
                                               command_id="closed:equivalent-alternate")
            assert canonical_json(alternate.module.to_dict()) == canonical_json(module.to_dict())
            generated_ref = alternate.revision.revision_ref
            assert type(validate_closed_revision(core, generated_ref, registration())) is ValidatedClosedRevision
        else:
            alternate = author._publish_document("assembly:equivalent-map-alternate", assembly.LOWERING_V8_SCHEMA, mapping)
            assert _payload(core, alternate) == _payload(core, mapping_ref)
            mapping_ref = alternate
        record = AssemblyRevisionV8(reference, seed.revision.owner_task_ref, author.producer,
            plan["command_id"], None, plan_ref, generated_ref, compiled_ref, mapping_ref)
        core.publish_bytes(object_type=assembly.ASSEMBLY_V8_TYPE, logical_id=reference.ref.entity_id,
            version_id=reference.ref.version_id, payload=canonical_json(record.to_dict()), metadata=record.to_dict(),
            media_type="application/json", schema_ref=assembly.ASSEMBLY_V8_SCHEMA, idempotency_key="claim:" + case)
        assert read_assembly_revision(core, reference) == record
        before = _facts(core)
        with pytest.raises(RegistryConflict, match="exact pair"):
            validate_assembly_revision(core, reference, registration())
        assert _facts(core) == before
        print("GRAPH_MERGE_ASSEMBLY_PAIR_CLAIM=" + json.dumps({"case": case,
            "canonical_descriptor_read": True, "alternate_body_equal": True, "full_pair_rejected": True}, sort_keys=True))
    assert_author_only(core)


def _replace_metadata(core, reference, metadata):
    """Keep real object/publication facts coherent so the v8 proof is exercised."""
    version = str(_version(reference).version_id)
    with core.event_store.connect() as db:
        row = dict(db.execute("SELECT * FROM objects WHERE version_id=?", (version,)).fetchone())
        event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0]
        publication = json.loads(event)
        publication["metadata"] = metadata
        core.catalog.validate_instance("resource_version/v1", category="object", instance=metadata)
        core.catalog.validate_schema_ref("registry_v1/object_version_published/v1", publication)
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (canonical_json(metadata).decode(), version))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(publication).decode(), row["published_event_id"]))
    return row, event


def _restore_metadata(core, saved):
    row, event = saved
    with core.event_store.connect() as db:
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (row["metadata_json"], row["version_id"]))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (event, row["published_event_id"]))


def _reject_frozen_lowering_metadata(fixture, monkeypatch):
    # The historical independent v8 window was only PRESTART. This is a new
    # rejection/recovery case on the current source, not a relabeled old result.
    core, _, author, _, _, _, _, merged, edited = fixture
    command = "assembly:metadata-lock"
    args = request(merged, edited, command_id=command)
    original = author._publish_document
    seen = []
    def substitute(key, schema, document, *, descriptors=None):
        if schema == assembly.LOWERING_V8_SCHEMA:
            plan_ref = assembly._material_ref(core, author.binding, assembly._command(command) + ":plan")
            assert core.event_store.object_row(_version(plan_ref).version_id) is not None
            seen.append(_payload(core, plan_ref))
            descriptors = {"unknown_author_command_v9": "unfrozen alternate lowering metadata"}
        return original(key, schema, document, descriptors=descriptors)
    with monkeypatch.context() as patch:
        patch.setattr(author, "_publish_document", substitute)
        with pytest.raises(RegistryConflict, match="frozen complete metadata or bytes"):
            author.publish(**args)
    assert len(seen) == 1
    plan = json.loads(seen[0])
    reference = assembly._result_ref(core, author.binding["source_id"], command, None)
    assert core.event_store.object_row(reference.ref.version_id) is None
    # Restore the exact locked metadata in both real persisted fact rows, then
    # prove same-command recovery without changing the original plan bytes.
    lower = SourceQualifiedResourceRef.from_dict(plan["prepared_materials"][-1]["resource_ref"], catalog=core.catalog)
    actual = json.loads(core.event_store.object_row(_version(lower).version_id)["metadata_json"])
    assert actual["descriptors"] == {"unknown_author_command_v9": "unfrozen alternate lowering metadata"}
    restored = deepcopy(actual); restored["descriptors"] = {}
    assert restored == plan["prepared_materials"][-1]["metadata"]
    _replace_metadata(core, lower, restored)
    # Out-of-band fixture restoration does not invalidate the writer's cache of
    # immutable object rows. Reopen through the supported recovery boundary.
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    replay = AssemblyAuthorV8(RegistryRegistrationGateway(
        reopened, author.gateway._task_ref, author.gateway._bootstrap_ref),
        registration(), author.producer)
    value = replay.publish(**args)
    assert _payload(reopened, value.revision.plan_ref) == seen[0]
    _check_pair(reopened, value, registration())
    print("GRAPH_MERGE_V8_METADATA_PRECOMMIT=" + json.dumps({"real_plan_durable": True,
        "unknown_marker_rejected": True, "descriptor_absent_before_restore": True,
        "same_command_exact_recovery": True}, sort_keys=True))


def _reject_coherent_metadata_substitutions(core, author, value):
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    schema = assembly.LOWERING_V8_SCHEMA
    alternate = _publish_private_system(core, author.gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(author.gateway._bootstrap_ref),
        payload=canonical_json(json.loads(core.catalog.schema_path(schema).read_text())),
        media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
        summary="Same-body independent lowering schema", lifetime_ref=author.gateway._bootstrap_ref,
        descriptors={"host_registration_kind": "schema", "registered_key": schema},
        idempotency_key="assembly:alternate-lowering-authority"))
    assert alternate != author.schemas[schema]
    cases = (("plan_summary", value.revision.plan_ref, "summary", "Changed plan summary"),
             ("generated_summary", value.generated.revision.definition_ref, "summary", "Changed generated summary"),
             ("compiled_extensions", value.revision.compiled_inventory_ref, "extensions", {"changed": True}),
             ("lowering_marker", value.revision.lowering_mapping_ref, "descriptors", {"unknown_author_command_v9": "unfrozen"}),
             ("lowering_authority", value.revision.lowering_mapping_ref, "content_schema_authority_ref", {"resource_id": str(alternate.resource_id), "resource_version_id": str(alternate.resource_version_id)}))
    before = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for case, reference, field, changed in cases:
        raw = _payload(core, reference)
        metadata = json.loads(core.event_store.object_row(_version(reference).version_id)["metadata_json"])
        assert metadata[field] != changed
        metadata[field] = changed
        if field == "content_schema_authority_ref":
            # The schema authority also occurs in publication provenance. Keep
            # both copies coherent so this reaches the actual v8 lock.
            metadata["reference_provenance"]["publication"][field] = changed
        saved = _replace_metadata(core, reference, metadata)
        try:
            # This proves rejection at v8's metadata lock, not inconsistent rows
            # or malformed-schema rejection in the generic Registry reader.
            with pytest.raises(RegistryConflict, match="frozen complete metadata or bytes"):
                validate_assembly_revision(reader, value.revision.revision_ref, registration())
            assert _payload(core, reference) == raw and _facts(core) == before
        finally:
            _restore_metadata(core, saved)
        _check_pair(core, value, registration())
        print("GRAPH_MERGE_V8_COHERENT_METADATA_REJECT=" + json.dumps({"case": case,
            "payload_unchanged": True, "readonly_rejection": True, "exact_restore": True}, sort_keys=True))
    # Same length and JSON meaning, with every metadata fact unchanged. This
    # specifically reaches the canonical raw-byte lock rather than a size check.
    reference = value.revision.lowering_mapping_ref
    path = core.object_store.path_for_version(_version(reference).version_id)
    raw = path.read_bytes(); body = json.loads(raw)
    changed = json.dumps(dict(reversed(list(body.items()))), ensure_ascii=False, separators=(",", ":")).encode()
    assert changed != raw and len(changed) == len(raw) and json.loads(changed) == body
    try:
        path.write_bytes(changed)
        with pytest.raises(RegistryConflict, match="frozen complete metadata or bytes"):
            validate_assembly_revision(reader, value.revision.revision_ref, registration())
        assert _facts(core) == before
    finally:
        path.write_bytes(raw)
    _check_pair(core, value, registration())
    print("GRAPH_MERGE_V8_CANONICAL_RAW_REJECT same_length_same_body_exact_restore")
