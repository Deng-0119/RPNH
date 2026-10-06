"""Real Core publication versus its pure complete graph projection."""
import json
import socket
import subprocess
import uuid

import pytest

from cpn.rpnh.collaboration import ClosedModuleAuthor, SourceQualifiedVersionRef, author_material_schema_data
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.net_operations import ComposePlan, compose_modules
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from cpn.rpnh.registry.module_nets import publish_module_net
from cpn.rpnh.registry.module_operations import publish_module_operations
from cpn.rpnh.registry.operations import bind_operation_registration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import publish_user_authority_decision, ref_payload
from test_native_net_operations import TEXT, _registration, _simple_module


def graph_arguments(tmp_path, mode):
    schemas, types, paths = author_material_schema_data()
    core = _RegistryCore(tmp_path / "graph", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    identity = _bootstrap_identity(core, NativeBootstrapManifest(("graph-projection/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="source:bind")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    round_ref = VersionRef("task_round/v1", new_id("task_round"), new_id("task_round_version"))
    for ref, document in ((principal, {"principal_id": str(principal.entity_id),
            "principal_version_id": str(principal.version_id), "display_name": "Projection owner"}),
            (round_ref, {"task_round_id": str(round_ref.entity_id), "task_id": str(core.task_id),
                "round_number": 1, "predecessor_task_round_id": None,
                "task_branch_ref": ref_payload(identity.task_branch_ref)})):
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json",
            schema_ref="registry_v1/" + ref.entity_type, idempotency_key="fixture:" + ref.entity_type)
    decision = publish_user_authority_decision(core, authority_kind="scope", canonical_statement="Offline graph projection",
        user_principal_ref=principal, governed_artifact_refs=(identity.task_ref,), selected_choices={"graph": "offline"},
        effective_sequence=1, supersedes_ref=None, idempotency_key="fixture:authority")
    registration = _registration()
    author = ClosedModuleAuthor(gateway, registration, SourceQualifiedVersionRef("source-a", principal))
    module = _simple_module()
    if mode == "hosts":
        module = compose_modules({"left": module, "right": module}, ComposePlan("Pair", "right", mode="parallel"))
    material = author.publish(module=module, element_ids={key: "element:" + uuid.uuid4().hex for key in _elements(module)},
        command_id="fixture:author")
    compiled = material.compiled
    bind_operation_registration(registration)
    schema_refs = {key: gateway.schema_refs[key] for key in compiled.source.required_schemas}
    operations = publish_module_operations(core, compiled, registration, schema_refs, idempotency_key="fixture:operations")
    def resource(label):
        return _publish_private_system(core, identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(bootstrap), payload=canonical_json(label), media_type="application/json",
            content_schema_ref=TEXT, content_schema_authority_ref=schema_refs[TEXT], summary=label,
            lifetime_ref=bootstrap, idempotency_key="fixture:resource:" + label))
    entries, owner_inputs, hosts = {}, (), {}
    if mode == "inputs":
        entries, owner_inputs = {"request": resource("entry")}, (resource("owner"),)
    if mode == "hosts":
        for transition in compiled.symbolic.transitions:
            agent = VersionRef("agent/v1", new_id("agent"), new_id("agent_version"))
            document = {"agent_id": str(agent.entity_id), "agent_version_id": str(agent.version_id),
                "agent_ref": ref_payload(agent), "team_net_declaration_ref": ref_payload(material.revision.definition_ref.ref.as_version_ref()),
                "declared_agent_id": transition.name, "shadow_transition_id": transition.name}
            core.publish_bytes(object_type=agent.entity_type, logical_id=agent.entity_id, version_id=agent.version_id,
                payload=canonical_json(document), metadata=document, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key="fixture:agent:" + transition.name)
            hosts[transition.name] = HostExecutionBinding(agent_ref=agent, extra_resource_refs=(resource(transition.name),))
    return core, compiled, registration, dict(identity=identity, bootstrap_ref=bootstrap, principal_ref=principal,
        task_round_ref=round_ref, authority_decision_ref=decision, entry_inputs=entries, schema_refs=schema_refs,
        operation_refs=operations, idempotency_key="fixture:graph", owner_resource_inputs={},
        owner_input_resources=owner_inputs, preserved_slot_refs={}, host_execution_bindings=hosts)


@pytest.mark.parametrize("mode", ["basic", "inputs", "hosts"])
def test_pure_projection_matches_all_real_published_graph_objects(tmp_path, monkeypatch, mode):
    from cpn.rpnh.registry._module_graph import allocate_module_graph, materialize_module_graph
    core, compiled, registration, arguments = graph_arguments(tmp_path, mode)
    publication = publish_module_net(core, compiled, registration, **arguments)
    specs = {name: dict(core.get_version(ref.version_id).metadata) for name, ref in arguments["operation_refs"].items()}
    allocation = allocate_module_graph(compiled, identity=arguments["identity"], task_round_ref=arguments["task_round_ref"],
        specs=specs, idempotency_key=arguments["idempotency_key"])
    host_resources, host_artifacts = validate_host_execution_bindings(core, allocation[-1], arguments["host_execution_bindings"])
    rows = [row for row in core.event_store.object_rows() if row["transaction_id"] == str(publication.transaction_id)
        and row["object_type"] != "resource_version/v1"]
    snapshots = {row["version_id"]: (json.loads(row["metadata_json"]),
        core.object_store.read_registered(core.get_version(row["version_id"]))) for row in rows}
    def forbidden(*args, **kwargs):
        raise AssertionError("pure graph projection cannot perform I/O or resolve HOST code")
    monkeypatch.setattr(core, "begin", forbidden)
    monkeypatch.setattr(core.event_store, "connect", forbidden)
    monkeypatch.setattr(registration, "resolve", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    graph = materialize_module_graph(compiled, identity=arguments["identity"], task_round_ref=arguments["task_round_ref"],
        principal_ref=arguments["principal_ref"], authority_decision_ref=arguments["authority_decision_ref"],
        declaration=publication.declaration_resource_ref, schema_refs=arguments["schema_refs"], operation_refs=arguments["operation_refs"],
        specs=specs, entry_inputs=arguments["entry_inputs"], owner_resource_inputs=arguments["owner_resource_inputs"],
        owner_input_resources=arguments["owner_input_resources"], preserved_slot_refs=arguments["preserved_slot_refs"],
        host_bindings=arguments["host_execution_bindings"], host_resources=host_resources, host_artifacts=host_artifacts,
        idempotency_key=arguments["idempotency_key"])
    assert {str(ref.version_id) for ref, _ in graph.objects} == set(snapshots)
    for ref, metadata in graph.objects:
        expected, payload = snapshots[str(ref.version_id)]
        assert canonical_json(metadata) == canonical_json(expected) == payload
    assert graph.resource_plan == publication.resource_plan
