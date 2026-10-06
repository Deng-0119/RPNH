"""Lower-level slot read/transport fixture; candidate scope is not expanded."""
from dataclasses import replace
import json
import socket
import subprocess
import urllib.request

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation
from cpn.rpnh.collaboration import author_material_schema_data
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
from cpn.rpnh.petri_contracts import LogicalSlotBinding, LeaseIdentityDeclaration
from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_resource_plan
from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run
from test_native_net_operations import TEXT, ALT_TEXT, _registration, _simple_module
from test_preserved_basis_reads import basis_for


def _slot_module(name, with_resource=False):
    document = _simple_module(name).to_dict()
    document["components"][0]["key"] = "slot_resource_operation" if with_resource else "slot_operation"
    if with_resource:
        document["required_schemas"] = sorted(set(document["required_schemas"]) | {ALT_TEXT})
    return ModuleDeclaration.from_dict(document)


def _slot_registration(with_resource=False):
    registration = _registration()
    def lower(config, context):
        fragment = lower_operation(config, context)
        return replace(fragment, logical_slots=(LogicalSlotBinding(
            "result_slot", "run", "result", "result", "result", TEXT,
            artifact_id="result", artifact_synopsis="Persistent result"),),
            lease_identities=(LeaseIdentityDeclaration("asset", "resource"),) if with_resource else ())
    registration.register_component("slot_resource_operation" if with_resource else "slot_operation", lower,
        identity={"implementation_id": "test.slot_operation", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    return registration


@pytest.fixture(params=[False, True], ids=["slot", "slot_and_resource"])
def preserved_slot_fixture(tmp_path, monkeypatch, request):
    def forbidden(*args, **kwargs):
        raise AssertionError("slot transport fixture cannot use sockets/processes/network")
    for target, field in ((socket, "socket"), (subprocess, "Popen"), (urllib.request, "Request"), (urllib.request, "urlopen")):
        monkeypatch.setattr(target, field, forbidden)
    schemas, types, paths = author_material_schema_data()
    with_resource = request.param
    module, task = _slot_module("Creation", with_resource), OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(module, _slot_registration(with_resource), run_dir=tmp_path / "slot-owner", task_input=task,
        entry_inputs={"request": task}, resource_inputs={"step.asset": task} if with_resource else None,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-slot", owner_statement="Offline slot projection fixture", command_id="fixture:slot:fresh",
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner.schema_gateway.bind_source_identity(source_id="slot-source", command_id="fixture:slot:source")
    creation = owner.publication
    assert set(creation.resource_plan.slot_refs) == {"step.result_slot"}
    first = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[0]
    outcome = apply_replacement(owner, prepare_replacement(owner, _slot_module("Replacement", with_resource)), command_id="fixture:slot:replace")
    assert outcome["status"] == "ADOPTED"
    last = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    core = owner._core
    basis = basis_for(last)
    net = core.get_version(basis.net_ref.version_id).metadata
    root_ref = _version_from_payload(net["team_design_root_ref"])
    root = core.get_version(root_ref.version_id).metadata
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    from cpn.rpnh.executable_net import load_compiled_net
    compiled = load_compiled_net(json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id))))
    arguments = dict(compiled=compiled, net_ref=basis.net_ref, net=net, root_ref=root_ref, root=root, declaration_ref=declaration)
    return owner, creation, first, last, arguments


def test_real_owner_preserves_nonempty_slot_and_creation_is_older_than_basis(preserved_slot_fixture):
    owner, creation, first, last, arguments = preserved_slot_fixture
    core = owner._core
    basis = basis_for(last)
    assert basis.net_ref != creation.net_ref and arguments["root_ref"] != creation.root_ref
    plan = hydrate_module_resource_plan(core, **arguments)
    slot = plan.slot_refs["step.result_slot"]
    assert slot == creation.resource_plan.slot_refs["step.result_slot"]
    assert plan.proposed_slots == ()
    metadata = core.get_version(slot.version_id).metadata
    assert metadata["team_design_root_ref"] == ref_payload(creation.root_ref)
    assert plan.logical_slots[0].producer_node_ref.version_id != str(creation.node_refs["step.run"].version_id)
    snapshot = read_preserved_basis(core, basis)
    refs = [row["ref"] for row in snapshot.dependency_evidence]
    for ref in (slot, creation.root_ref, creation.net_ref, arguments["root_ref"], basis.net_ref):
        assert ref_payload(ref) in refs
    assert read_preserved_basis(core, basis_for(first)).basis.net_ref == creation.net_ref
    assert not core.event_store.object_rows_by_type("transition_firing/v1")
    assert not core.event_store.object_rows_by_type("llm_invocation_attempt/v1")


def test_slot_fixture_component_does_not_expand_candidate_producer_scope(preserved_slot_fixture):
    from cpn.rpnh.collaboration.candidate_plans import _supported_plan
    from cpn.rpnh.registry.event_store import RegistryConflict
    _, _, _, _, arguments = preserved_slot_fixture
    plan = {"preserved_slot_refs": {}, "host_bindings": {}, "runtime_dependencies": {},
        "host_resource_refs": [], "host_artifact_refs": []}
    with pytest.raises(RegistryConflict, match="operation"):
        _supported_plan(plan, arguments["compiled"])


def _projection_data(core, creation):
    from cpn.rpnh.executable_net import load_compiled_net
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.models import VersionRef
    metadata = {VersionRef(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"])):
        json.loads(row["metadata_json"]) for row in core.event_store.object_rows()}
    ref = creation.declaration_resource_ref.as_version_ref()
    compiled = load_compiled_net(json.loads(core.object_store.read_registered(core.get_version(ref.version_id))))
    schemas = {}
    for reference, body in metadata.items():
        authority = body.get("content_schema_authority_ref") if reference.entity_type == "resource_version/v1" else None
        if not isinstance(authority, dict) or set(authority) != {"resource_id", "resource_version_id"}:
            continue
        source = _resource_from_payload(authority)
        verified = core.verify_registered_content_schema_ref(source, schema_document_ref=source.as_version_ref())
        document = json.loads(core.object_store.read_registered(core.get_version(source.resource_version_id)))
        schemas[source.as_version_ref()] = verified.schema_id, document
    return dict(exact_metadata=metadata, owner_schema_documents=schemas, creation_compiled={ref: compiled})


def test_pure_slot_projection_matches_real_hydration_without_io(preserved_slot_fixture, monkeypatch):
    from pathlib import Path
    from cpn.rpnh.registry._module_resource_projection import project_module_resource_plan
    from cpn.rpnh.registration import Registration
    owner, creation, _, _, arguments = preserved_slot_fixture
    core = owner._core
    expected = hydrate_module_resource_plan(core, **arguments)
    data = _projection_data(core, creation)
    def forbidden(*args, **kwargs):
        raise AssertionError("pure resource projection cannot read files, DB or HOST")
    monkeypatch.setattr(core.event_store, "connect", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Registration, "resolve", forbidden)
    assert project_module_resource_plan(**arguments, **data) == expected


@pytest.mark.parametrize("axis", ["mapping", "nested_mapping", "fake_id", "compiled_mapping", "missing_metadata", "missing_creation"])
def test_pure_projection_uses_only_finite_detached_data(preserved_slot_fixture, axis):
    from dataclasses import replace
    from cpn.rpnh.registry._module_resource_projection import project_module_resource_plan
    from cpn.rpnh.registry.models import VersionRef
    owner, creation, _, _, arguments = preserved_slot_fixture
    data = _projection_data(owner._core, creation)
    class ApplicationMapping(dict):
        def items(self):
            raise AssertionError("must reject application mapping before dispatch")
    class FakeId:
        kind = "node"
        def __str__(self):
            raise AssertionError("must reject nonstandard typed ID before stringification")
    if axis == "mapping":
        data["exact_metadata"] = ApplicationMapping(data["exact_metadata"])
    elif axis == "nested_mapping":
        key = next(iter(data["exact_metadata"]))
        data["exact_metadata"][key] = {"nested": ApplicationMapping()}
    elif axis == "fake_id":
        key = next(iter(data["exact_metadata"]))
        data["exact_metadata"] = {VersionRef(key.entity_type, FakeId(), key.version_id): {}}
    elif axis == "compiled_mapping":
        arguments = dict(arguments, compiled=replace(arguments["compiled"], registrations=ApplicationMapping()))
    elif axis == "missing_metadata":
        del data["exact_metadata"][creation.root_ref]
    else:
        data["creation_compiled"] = {}
    with pytest.raises((TypeError, ValueError)):
        project_module_resource_plan(**arguments, **data)


def test_pure_projection_detaches_read_bags(preserved_slot_fixture):
    from cpn.rpnh.registry._module_resource_projection import _DataResourceReads
    owner, creation, _, _, _ = preserved_slot_fixture
    data = _projection_data(owner._core, creation)
    reads = _DataResourceReads(data["exact_metadata"], data["owner_schema_documents"], data["creation_compiled"])
    before = reads.metadata(creation.root_ref)
    old = json.loads(canonical_json(before))
    data["exact_metadata"][creation.root_ref]["resource_refs"].clear()
    data["creation_compiled"][creation.declaration_resource_ref.as_version_ref()].registrations.clear()
    assert reads.metadata(creation.root_ref) == old
    assert reads.creation_compiled(creation.declaration_resource_ref.as_version_ref()).registrations


def test_application_metaclass_cannot_escape_copying_or_return_caller_alias(preserved_slot_fixture):
    from cpn.rpnh.registry._module_resource_projection import project_module_resource_plan
    owner, creation, _, _, arguments = preserved_slot_fixture
    data = _projection_data(owner._core, creation)
    calls = []
    class ApplicationMeta(type):
        def __eq__(cls, other):
            calls.append("eq")
            return other is str
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)
    class ApplicationList(list, metaclass=ApplicationMeta):
        pass
    payload = ApplicationList(["spoofed-route"])
    compiled = arguments["compiled"]
    slot = replace(compiled.symbolic.logical_slots[0], route_transitions=payload)
    candidate = replace(compiled, symbolic=replace(compiled.symbolic, logical_slots=(slot,)))
    with pytest.raises(TypeError, match="standard compiler data"):
        project_module_resource_plan(**dict(arguments, compiled=candidate), **data)
    payload.append("changed-after-rejection")
    assert calls == []


def test_standard_mutable_compiler_sequence_is_detached(preserved_slot_fixture):
    from cpn.rpnh.registry._module_resource_projection import project_module_resource_plan
    owner, creation, _, _, arguments = preserved_slot_fixture
    data = _projection_data(owner._core, creation)
    compiled = arguments["compiled"]
    payload = list(compiled.symbolic.logical_slots[0].route_transitions)
    slot = replace(compiled.symbolic.logical_slots[0], route_transitions=payload)
    candidate = replace(compiled, symbolic=replace(compiled.symbolic, logical_slots=(slot,)))
    result = project_module_resource_plan(**dict(arguments, compiled=candidate), **data)
    assert result.logical_slots[0].route_transition_ids is not payload
    payload.append("changed-after-return")
    assert result.logical_slots[0].route_transition_ids == []


def test_compiler_type_rejection_does_not_invoke_metaclass_hash():
    from cpn.rpnh.registry._module_resource_projection import _copy_compiler_data
    calls = []
    class ApplicationMeta(type):
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)
    class Application(metaclass=ApplicationMeta):
        pass
    with pytest.raises(TypeError, match="standard compiler data"):
        _copy_compiler_data(Application())
    assert calls == []


def test_internal_read_mode_guard_uses_only_type_identity():
    from cpn.rpnh.registry._module_resource_projection import _resource_projection
    calls = []
    class ApplicationMeta(type):
        def __eq__(cls, other):
            calls.append("eq")
            return True
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)
    class Application(metaclass=ApplicationMeta):
        pass
    with pytest.raises(TypeError, match="fixed internal read modes"):
        _resource_projection(Application(), None, None, None, None, None, None)
    assert calls == []
