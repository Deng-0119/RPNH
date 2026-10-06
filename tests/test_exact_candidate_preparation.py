"""Real offline Core candidate contract; rebuilt from reviewed failure cases.

These tests do not substitute a fake OwnerEventLoop, call providers, or reserve
workers. The first recovery checkpoint is deliberately test-first.
"""
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
def candidate_fixture(tmp_path):
    from cpn.rpnh.collaboration import CandidatePublisher, candidate_schema_data
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
    publisher = CandidatePublisher(gateway, registration, author.producer)
    return core, identity, gateway, principal, round_ref, decision, registration, author, publisher


def authored(fixture, module=None, command="author:first"):
    module = _simple_module() if module is None else module
    return fixture[-2].publish(module=module,
        element_ids={key: "element:" + uuid.uuid4().hex for key in _elements(module)}, command_id=command)


def candidate_request(fixture, revision, **changes):
    request = dict(author_ref=revision.revision.revision_ref, identity=fixture[1],
        task_round_ref=fixture[4], authority_decision_ref=fixture[5], command_id="candidate:first")
    return dict(request, **changes)


def test_real_basic_candidate_replays_exactly_without_runtime_effects(candidate_fixture, monkeypatch):
    from cpn.rpnh.collaboration import ExactRuntimeBindingResolver
    f = candidate_fixture
    revision = authored(f)
    def forbidden(*args, **kwargs):
        raise AssertionError("offline candidate cannot open sockets or processes")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = f[-1].publish(**candidate_request(f, revision))
    replay = f[-1].publish(**candidate_request(f, revision))
    assert result.publication == replay.publication
    assert result.manifest_ref == replay.manifest_ref
    resolver = ExactRuntimeBindingResolver(f[0], f[6])
    for transition, executable in result.publication.executable_refs.items():
        assert resolver.resolve(executable_ref=executable) == resolver.resolve(
            net_ref=result.publication.net_ref, transition_id=transition)
    assert result.readiness["execution_permission_checked"] is False
    assert all(check["execution_handle"] == "not_checked"
        and check["runtime_capacity"] == "not_checked"
        and check["reservation_state"] == "not_reserved" for check in result.readiness["checks"])
    assert f[0].event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1")) == ()
    for kind in ("transition_firing/v1", "operation_execution_lease/v1", "llm_invocation_attempt/v1"):
        assert f[0].event_store.object_rows_by_type(kind) == ()


def test_full_command_locks_non_pn_input_and_distinguishes_bool_number(candidate_fixture):
    f = candidate_fixture
    revision = authored(f)
    first = {"nested": {"owner_intent": True}}
    f[-1].publish(**candidate_request(f, revision, command_context=first))
    changed = deepcopy(first)
    changed["nested"]["owner_intent"] = 1
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, revision, command_context=changed))


def graph_author(f, version):
    from cpn.rpnh.module import ModuleDeclaration
    key = f"rpnh/agent-workflow-graph/v{version}"
    f[6].register_component(key, f[6].resolve("component", "operation"),
        identity={"implementation_id": "test.graph-policy", "revision": "v1"},
        contracts=f[6].declaration("component", "operation")["contracts"])
    document = _simple_module().to_dict()
    document["components"][0]["key"] = key
    return authored(f, ModuleDeclaration.from_dict(document))


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_graph_authoritative_preparation_waits_for_single_source_rebuild(candidate_fixture, version):
    f = candidate_fixture
    revision = graph_author(f, version)
    with pytest.raises(ValueError, match="single-source rebuild"):
        f[-1].publish(**candidate_request(f, revision))
    assert f[0].event_store.object_rows_by_type("collaboration_candidate_plan/v1") == ()


@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("boundary", ["core_gate", "strict_reader", "explicit_legacy"])
def test_known_graph_scope_is_closed_below_facade_and_keeps_real_legacy(candidate_fixture, monkeypatch, version, boundary):
    from cpn.rpnh.collaboration import ExactRuntimeBindingResolver, require_explicit_legacy_net
    from cpn.rpnh.collaboration import candidates
    from cpn.rpnh.registry import runtime_bindings
    from cpn.rpnh.registry.module_nets import publish_module_net
    from cpn.rpnh.registry.module_operations import publish_module_operations
    f = candidate_fixture
    revision = graph_author(f, version)
    if boundary == "explicit_legacy":
        schemas = {key: f[2].schema_refs[key] for key in revision.compiled.source.required_schemas}
        operations = publish_module_operations(f[0], revision.compiled, f[6], schemas, idempotency_key="fixture:legacy-operations")
        publication = publish_module_net(f[0], revision.compiled, f[6], identity=f[1], bootstrap_ref=f[2]._bootstrap_ref,
            principal_ref=f[3], task_round_ref=f[4], authority_decision_ref=f[5], entry_inputs={},
            schema_refs=schemas, operation_refs=operations, idempotency_key="fixture:legacy-graph")
        assert require_explicit_legacy_net(f[0], publication.net_ref)["net_instance_ref"] == ref_payload(publication.net_ref)
        assert f[0].event_store.object_rows_by_type("runtime_binding_manifest/v1") == ()
        return
    # An untrusted caller can bypass an API precheck. Core must enforce the
    # same scope using its real transaction cut, independently of this facade.
    monkeypatch.setattr(candidates, "_requires_graph_source_rebuild", lambda module: False)
    if boundary == "core_gate":
        with pytest.raises(RegistryConflict, match="single-source rebuild"):
            f[-1].publish(**candidate_request(f, revision))
        assert f[0].event_store.object_rows_by_type("net_instance/v1") == ()
        assert f[0].event_store.object_rows_by_type("runtime_binding_manifest/v1") == ()
    else:
        # Persist a specimen from a hypothetical older producer. Only that
        # producer gate is bypassed; the real committed reader stays active.
        with monkeypatch.context() as old_producer:
            old_producer.setattr(runtime_bindings, "validate_runtime_manifest_proposal", lambda context: None)
            with pytest.raises(RegistryConflict, match="single-source rebuild"):
                f[-1].publish(**candidate_request(f, revision))
        rows = f[0].event_store.object_rows_by_type("net_instance/v1")
        assert len(rows) == 1
        metadata = json.loads(rows[0]["metadata_json"])
        net_ref = _version_from_payload(metadata["net_instance_ref"])
        with pytest.raises(RegistryConflict, match="single-source rebuild"):
            ExactRuntimeBindingResolver(f[0], f[6]).resolve(net_ref=net_ref,
                transition_id=revision.compiled.symbolic.transitions[0].name)
        assert f[0].event_store.object_rows_by_type("binding_readiness/v1") == ()
