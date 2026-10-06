"""Real first-plan publication only; no graph/runtime candidate claim."""
from copy import deepcopy
import json
import socket
import subprocess
import uuid

import pytest

from cpn.rpnh.collaboration import ClosedModuleAuthor, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import bind_operation_registration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import publish_user_authority_decision, ref_payload
from test_native_net_operations import _registration, _simple_module


@pytest.fixture
def plan_fixture(tmp_path):
    from cpn.rpnh.collaboration import candidate_schema_data
    from cpn.rpnh.collaboration.candidate_plans import CandidatePlanPublisher
    schemas, types, paths = candidate_schema_data()
    core = _RegistryCore(tmp_path / "candidate", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    identity = _bootstrap_identity(core, NativeBootstrapManifest(("candidate-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="source:bind")

    def publish(kind, logical, version, body, key):
        ref = VersionRef(kind, new_id(logical), new_id(version))
        document = body(ref)
        core.publish_bytes(object_type=kind, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json",
            schema_ref="registry_v1/" + kind, idempotency_key=key)
        return ref

    principal = publish("principal/v1", "principal", "principal_version", lambda ref: {
        "principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id),
        "display_name": "Offline candidate owner"}, "fixture:principal")
    round_ref = publish("task_round/v1", "task_round", "task_round_version", lambda ref: {
        "task_round_id": str(ref.entity_id), "task_id": str(core.task_id), "round_number": 1,
        "predecessor_task_round_id": None, "task_branch_ref": ref_payload(identity.task_branch_ref)}, "fixture:round")
    decision = publish_user_authority_decision(core, authority_kind="scope",
        canonical_statement="Prepare local static candidates", user_principal_ref=principal,
        governed_artifact_refs=(identity.task_ref,), selected_choices={"candidate": "offline"},
        effective_sequence=1, supersedes_ref=None, idempotency_key="fixture:authority")
    registration = _registration()
    author = ClosedModuleAuthor(gateway, registration, SourceQualifiedVersionRef("source-a", principal))
    bind_operation_registration(registration)
    publisher = CandidatePlanPublisher(gateway, registration, author.producer)
    return core, identity, gateway, principal, round_ref, decision, registration, author, publisher


from test_exact_candidate_preparation import authored, candidate_request
from cpn.rpnh.collaboration.candidate_plans import read_candidate_plan
from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE

def assert_plan_only(core):
    assert len(core.event_store.object_rows_by_type(PLAN_TYPE)) == 1
    for kind in ("operation_spec/v1", "net_instance/v1", "runtime_binding_manifest/v1", "binding_readiness/v1",
                 "transition_firing/v1", "operation_execution_lease/v1", "llm_invocation_attempt/v1"):
        assert core.event_store.object_rows_by_type(kind) == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1")) == ()


def test_real_plan_reopens_replays_exactly_and_has_no_runtime_effects(plan_fixture, monkeypatch):
    f = plan_fixture
    revision = authored(f)
    def forbidden(*args, **kwargs):
        raise AssertionError("offline plan must not create sockets/processes")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    context = {"intent": {"token": True}}
    request = candidate_request(f, revision, command_context=context)
    result = f[-1].publish(**request)
    assert result == f[-1].publish(**request)
    context["intent"]["token"] = "changed after publication"
    assert result.plan["command_context"] == {"intent": {"token": True}}
    core = _RegistryCore(f[0].run_dir, create=False, read_only=True, catalog=f[0].catalog)
    assert read_candidate_plan(core, result.plan_ref) == result
    evidence = result.plan["dependency_evidence"]
    assert evidence and not any(item["ref"] == ref_payload(result.plan_ref) for item in evidence)
    assert any(item["ref"]["entity_type"] == "registry_type_catalog/v1" for item in evidence)
    assert any(item["ref"] == revision.revision.revision_ref.to_dict()["ref"] for item in evidence)
    assert_plan_only(f[0])


@pytest.mark.parametrize("changed", [1, 1.0, False, "true"])
def test_complete_plan_context_conflicts_type_sensitively(plan_fixture, changed):
    f = plan_fixture
    revision = authored(f)
    f[-1].publish(**candidate_request(f, revision, command_context={"input": True}))
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, revision, command_context={"input": changed}))
    assert_plan_only(f[0])


def test_same_command_two_contexts_concurrently_has_one_canonical_winner(plan_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    f = plan_fixture
    revision = authored(f)
    barrier = Barrier(2)
    def publish(value):
        barrier.wait()
        try:
            return f[-1].publish(**candidate_request(f, revision, command_context={"choice": value}))
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, ("a", "b")))
    successes = [item for item in results if not isinstance(item, Exception)]
    assert len(successes) == 1
    assert sum(isinstance(item, RegistryConflict) for item in results) == 1
    assert read_candidate_plan(f[0], successes[0].plan_ref) == successes[0]
    assert_plan_only(f[0])


@pytest.mark.parametrize("option", ["preserved_slot_refs", "host_bindings", "runtime_dependencies", "host_resource_refs", "host_artifact_refs"])
def test_nonempty_future_selections_fail_before_any_plan(plan_fixture, option):
    f = plan_fixture
    revision = authored(f)
    with pytest.raises(ValueError, match="not supported"):
        f[-1].publish(**candidate_request(f, revision, **{option: {"not_supported": "yet"}}))
    assert f[0].event_store.object_rows_by_type(PLAN_TYPE) == ()


@pytest.mark.parametrize("cut", ["after_prewrite", "after_commit"])
def test_real_plan_interruption_reopens_original_command(plan_fixture, monkeypatch, cut):
    from cpn.rpnh.registry.transaction import RegistryTransaction
    from cpn.rpnh.collaboration.candidate_plans import CandidatePlanPublisher
    f = plan_fixture
    revision = authored(f)
    request = candidate_request(f, revision, command_context={"survives": [True, 1]})
    original = RegistryTransaction.commit
    def interrupted(tx, *args, **kwargs):
        if cut == "after_commit":
            original(tx, *args, **kwargs)
        raise RuntimeError("simulated process interruption")
    with monkeypatch.context() as crash:
        crash.setattr(RegistryTransaction, "commit", interrupted)
        with pytest.raises(RuntimeError, match="interruption"):
            f[-1].publish(**request)
    assert len(f[0].event_store.object_rows_by_type(PLAN_TYPE)) == (cut == "after_commit")
    reopened = _RegistryCore(f[0].run_dir, create=False, catalog=f[0].catalog)
    gateway = RegistryRegistrationGateway(reopened, f[1].task_ref, f[2]._bootstrap_ref)
    publisher = CandidatePlanPublisher(gateway, f[6], f[-2].producer)
    result = publisher.publish(**request)
    assert result == read_candidate_plan(reopened, result.plan_ref)
    with pytest.raises(RegistryConflict):
        publisher.publish(**dict(request, command_context={"survives": [1, 1]}))
    assert_plan_only(reopened)


def _replace_record(core, reference, change, *, bytes_only=False):
    """Persisted historical/corruption specimen; never a valid new producer."""
    row = core.event_store.object_row(reference.version_id)
    body = json.loads(row["metadata_json"])
    change(body)
    data = canonical_json(body)
    core.object_store.path_for_version(reference.version_id).write_bytes(data)
    if bytes_only:
        return
    with core.event_store.connect() as db:
        publication = json.loads(db.execute("SELECT payload_json FROM events WHERE event_id=?",
            (row["published_event_id"],)).fetchone()[0])
        publication["metadata"], publication["size"] = body, len(data)
        db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?",
            (json.dumps(body), len(data), str(reference.version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(publication), row["published_event_id"]))


@pytest.mark.parametrize("damage", ["principal_shape", "round_shape", "round_identity", "round_branch", "genesis_identity"])
def test_owner_fixed_domains_reject_canonical_bad_shape_or_self_links(plan_fixture, damage):
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    f = plan_fixture
    revision = authored(f)
    result = f[-1].publish(**candidate_request(f, revision))
    if damage == "principal_shape":
        target, change = f[3], lambda body: body.pop("display_name")
    elif damage == "round_shape":
        target, change = f[4], lambda body: body.__setitem__("round_number", True)
    elif damage == "round_identity":
        target, change = f[4], lambda body: body.__setitem__("task_round_id", str(new_id("task_round")))
    elif damage == "round_branch":
        target, change = f[4], lambda body: body.pop("task_branch_ref")
    else:
        target, change = f[1].genesis_manifest_ref, lambda body: body.__setitem__("genesis_id", str(new_id("native_genesis")))
    _replace_record(f[0], target, change)
    with pytest.raises((RegistryConflict, SchemaGovernanceError)):
        read_candidate_plan(f[0], result.plan_ref)
    with pytest.raises((RegistryConflict, SchemaGovernanceError)):
        f[-1].publish(**candidate_request(f, revision))
    assert_plan_only(f[0])


@pytest.mark.parametrize("damage", ["evidence_missing", "wrong_source", "wrong_schema", "wrong_operation", "extra_input",
    "author_bridge", "command_context_nonfinite", "plan_self", "bytes_metadata", "noncanonical", "missing_payload"])
def test_exact_reader_rejects_low_level_bad_plan_specimens(plan_fixture, damage):
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    from test_native_net_operations import _simple_module
    f = plan_fixture
    revision = authored(f)
    other = authored(f, _simple_module("OtherSource"), command="author:other") if damage == "author_bridge" else None
    result = f[-1].publish(**candidate_request(f, revision))
    changes = {
        "evidence_missing": lambda body: body["dependency_evidence"].pop(),
        "wrong_source": lambda body: body.__setitem__("source_id", "source-b"),
        "wrong_schema": lambda body: body["schema_refs"].clear(),
        "wrong_operation": lambda body: next(iter(body["operation_refs"].values())).__setitem__("version_id", str(new_id("operation_spec_version"))),
        "extra_input": lambda body: body["owner_input_resources"].append(next(iter(body["schema_refs"].values()))),
        "author_bridge": lambda body: body.__setitem__("author_ref", other.revision.revision_ref.to_dict()),
        "command_context_nonfinite": lambda body: body.__setitem__("command_context", {"not_json": float("inf")}),
        "plan_self": lambda body: body["plan_ref"].__setitem__("version_id", str(new_id("resource_version"))),
        "bytes_metadata": lambda body: body.__setitem__("command_context", {"different": True}),
    }
    path = f[0].object_store.path_for_version(result.plan_ref.version_id)
    if damage == "noncanonical":
        path.write_bytes(json.dumps(result.plan, indent=2).encode())
    elif damage == "missing_payload":
        path.unlink()
    else:
        _replace_record(f[0], result.plan_ref, changes[damage], bytes_only=damage == "bytes_metadata")
    with pytest.raises((RegistryConflict, SchemaGovernanceError, ObjectIntegrityError, ValueError)):
        read_candidate_plan(f[0], result.plan_ref)
    assert_plan_only(f[0])


def test_frozen_dependency_evidence_covers_schema_authority_bytes(plan_fixture):
    f = plan_fixture
    revision = authored(f)
    result = f[-1].publish(**candidate_request(f, revision))
    schema_ref = revision.revision.element_mapping_ref.ref
    schema = f[0].get_version(schema_ref.resource_version_id).metadata["content_schema_authority_ref"]
    assert any(item["ref"]["version_id"] == schema["resource_version_id"] for item in result.plan["dependency_evidence"])
    catalog_ref = _version_from_payload(next(item["ref"] for item in result.plan["dependency_evidence"]
        if item["ref"]["entity_type"] == "registry_type_catalog/v1"))
    before = f[0].object_store.path_for_version(catalog_ref.version_id).stat().st_size
    def same_size_title(body):
        key = "registry_v1/task_round/v1"
        source = body["schemas"][key]["source"]
        assert '"title": "task_round/v1"' in source
        body["schemas"][key]["source"] = source.replace('"title": "task_round/v1"', '"title": "task_rounX/v1"')
    _replace_record(f[0], catalog_ref, same_size_title)
    assert f[0].object_store.path_for_version(catalog_ref.version_id).stat().st_size == before
    with pytest.raises(RegistryConflict, match="evidence"):
        read_candidate_plan(f[0], result.plan_ref)


def test_real_alternate_run_is_not_the_bound_owner(plan_fixture):
    f = plan_fixture
    revision = authored(f)
    alternate = _bootstrap_identity(f[0], NativeBootstrapManifest(f[1].protocol_versions))
    # This second bootstrap is individually registered, but the original
    # binding and registry_meta still pin the original native run.
    assert alternate.run_ref != f[1].run_ref
    with pytest.raises(RegistryConflict, match="exact native owner"):
        f[-1].publish(**candidate_request(f, revision, identity=alternate))
    assert f[0].event_store.object_rows_by_type(PLAN_TYPE) == ()


def test_same_compiled_source_different_author_command_conflicts(plan_fixture):
    f = plan_fixture
    first, other = authored(f), authored(f, command="author:other-complete-command")
    assert canonical_json(first.compiled.to_dict()) == canonical_json(other.compiled.to_dict())
    original = f[-1].publish(**candidate_request(f, first))
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, other))
    assert read_candidate_plan(f[0], original.plan_ref) == original
    assert_plan_only(f[0])


def test_exact_plan_reader_calls_no_host_compiler_or_global_resolution(plan_fixture, monkeypatch):
    from cpn.rpnh.collaboration import materials
    from cpn.rpnh import compiler
    from cpn.rpnh.registration import Registration
    f = plan_fixture
    result = f[-1].publish(**candidate_request(f, authored(f)))
    before = len(f[0].event_store.list_events())
    def forbidden(*args, **kwargs):
        raise AssertionError("plan reader is pure data: no HOST lower, effects, or publisher")
    for target, attribute in ((compiler, "compile_module"), (materials, "compile_module"),
            (Registration, "resolve"), (_RegistryCore, "begin"), (socket, "socket"), (subprocess, "Popen")):
        monkeypatch.setattr(target, attribute, forbidden)
    assert read_candidate_plan(f[0], result.plan_ref) == result
    assert len(f[0].event_store.list_events()) == before


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_exact_plan_reader_independently_rejects_unsupported_graph_sources(plan_fixture, monkeypatch, version):
    from cpn.rpnh.collaboration import candidate_plans
    from test_exact_candidate_preparation import graph_author
    f = plan_fixture
    revision = graph_author(f, version)
    with pytest.raises(RegistryConflict, match="single-source rebuild"):
        f[-1].publish(**candidate_request(f, revision))
    assert f[0].event_store.object_rows_by_type(PLAN_TYPE) == ()
    # Only make a hypothetical historical unsupported plan. This is an inert
    # record specimen, not proof of any graph transaction gate or execution.
    with monkeypatch.context() as historical:
        historical.setattr(candidate_plans, "_supported_plan", lambda *args: None)
        result = f[-1].publish(**candidate_request(f, revision))
    with pytest.raises(RegistryConflict, match="single-source rebuild"):
        read_candidate_plan(f[0], result.plan_ref)
    assert_plan_only(f[0])


def test_actual_schema_authority_cycle_fails_closed(plan_fixture):
    f = plan_fixture
    revision = authored(f)
    result = f[-1].publish(**candidate_request(f, revision))
    material = f[0].get_version(revision.revision.element_mapping_ref.ref.resource_version_id)
    authority = material.metadata["content_schema_authority_ref"]
    from cpn.rpnh.registry.identities import TypedId
    ref = VersionRef("resource_version/v1", TypedId.parse(authority["resource_id"]), TypedId.parse(authority["resource_version_id"]))
    # Resource payload is its schema document, not its metadata. Change only
    # persisted metadata + publication to construct a canonical-shaped cycle.
    row = f[0].event_store.object_row(ref.version_id)
    body = json.loads(row["metadata_json"])
    body["content_schema_authority_ref"] = authority
    with f[0].event_store.connect() as db:
        event = json.loads(db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0])
        event["metadata"] = body
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps(body), str(ref.version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(event), row["published_event_id"]))
    with pytest.raises(RegistryConflict, match="provenance"):
        read_candidate_plan(f[0], result.plan_ref)
    # Also exercise the lower typed cycle guard at a real read cut. The full
    # author checker above already rejects this older malformed specimen.
    from cpn.rpnh.registry._candidate_plan_reads import PlanReadClosure
    with f[0].event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="cycle"):
            PlanReadClosure(db, f[0]).resource(ref_payload(ref))


@pytest.mark.parametrize("target", ["plan", "author_resource", "principal"])
@pytest.mark.parametrize("member", ["object", "publication", "terminal"])
def test_exact_plan_never_accepts_provisional_dependency_members(plan_fixture, target, member):
    from test_collaboration_authoring import _inject_temporary_member
    f = plan_fixture
    revision = authored(f)
    result = f[-1].publish(**candidate_request(f, revision))
    version = {"plan": result.plan_ref.version_id, "author_resource": revision.revision.definition_ref.ref.resource_version_id,
        "principal": f[3].version_id}[target]
    row = f[0].event_store.object_row(version)
    if member == "terminal":
        with f[0].event_store.connect() as db:
            identity = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                (row["transaction_id"],)).fetchone()[0]
    else:
        identity = str(version) if member == "object" else row["published_event_id"]
    _inject_temporary_member(f[0], "object" if member == "object" else "event", identity)
    with pytest.raises(RegistryConflict):
        read_candidate_plan(f[0], result.plan_ref)


def test_published_business_resource_keeps_existing_promotion_contract(plan_fixture):
    from test_collaboration_authoring import _inject_temporary_member
    f = plan_fixture
    revision = authored(f)
    result = f[-1].publish(**candidate_request(f, revision))
    root = _inject_temporary_member(f[0], "object", str(revision.revision.definition_ref.ref.resource_version_id))
    tx = f[0].begin(idempotency_key="fixture:plan-promotion")
    terminal = tx.commit()[-1]
    with f[0].event_store.connect() as db:
        db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
            "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
            (str(tx.transaction_id), str(new_id("operation_result_version")),
             str(new_id("marking_checkpoint_version")), root))
        db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
            (root, "event", str(terminal.event_id), str(tx.transaction_id)))
    assert read_candidate_plan(f[0], result.plan_ref) == result
