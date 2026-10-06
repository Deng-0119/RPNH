"""Explicit low-level slot selection; candidate facade scope stays unchanged."""
import hashlib
import json
from dataclasses import replace

import pytest

from cpn.rpnh.compiler import compile_module
from cpn.rpnh.registry._preserved_slot_selection import read_preserved_slot_selection
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
from test_preserved_basis_reads import basis_for
from test_preserved_slot_projection import preserved_slot_fixture, _slot_module, _slot_registration


def _inputs(fixture):
    owner, creation, first, last, arguments = fixture
    with_resource = bool(arguments["net"]["module_resource_bindings"]["owner_resource_inputs"])
    candidate = compile_module(_slot_module("ExplicitCandidate", with_resource), _slot_registration(with_resource))
    output = owner._core.get_version(_version_from_payload(arguments["net"]["output_binding_refs"][0]).version_id).metadata
    slots = dict(creation.resource_plan.slot_refs)
    schemas = {output["content_schema_id"]: _resource_from_payload(output["content_schema_ref"])}
    return candidate, slots, schemas


@pytest.mark.parametrize("anchor", ["creation", "replacement"])
def test_explicit_selection_uses_exact_basis_and_records_all_reads(preserved_slot_fixture, monkeypatch, anchor):
    from cpn.rpnh.registration import Registration
    owner, creation, first, last, _ = preserved_slot_fixture
    core = owner._core
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    basis = basis_for(first if anchor == "creation" else last)
    before = len(core.event_store.list_events()), len(core.event_store.object_rows())
    actual, original = set(), ObjectStore.read_registered
    def record(store, prepared):
        payload = original(store, prepared)
        actual.add((prepared.object_type, str(prepared.logical_id), str(prepared.version_id),
            hashlib.sha256(payload).hexdigest(), len(payload), prepared.media_type))
        return payload
    def forbidden(*args, **kwargs):
        raise AssertionError("slot selection cannot resolve HOST, write or open another cut")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with monkeypatch.context() as patch:
            patch.setattr(ObjectStore, "read_registered", record)
            patch.setattr(core.event_store, "connect", forbidden)
            patch.setattr(core, "get_version", forbidden)
            patch.setattr(core, "begin", forbidden)
            patch.setattr(Registration, "resolve", forbidden)
            result = read_preserved_slot_selection(core, candidate, preserved_basis=basis,
                selected_slot_refs=slots, selected_schema_refs=schemas, _db=db)
    assert result.basis == basis
    assert result.selected_slot_refs == slots and result.selected_schema_refs == schemas
    assert result.document["candidate_document"] == candidate.to_dict()
    assert actual == {(row["ref"]["entity_type"], row["ref"]["logical_id"], row["ref"]["version_id"],
        row["sha256"], row["size"], row["media_type"]) for row in result.dependency_evidence}
    assert (len(core.event_store.list_events()), len(core.event_store.object_rows())) == before
    # Mutable caller data and each returned view are independent.
    slots.clear()
    schemas.clear()
    candidate.registrations.clear()
    changed = result.document
    changed["candidate_document"].clear()
    changed["selected_slot_refs"].clear()
    assert result.document["candidate_document"] and result.selected_slot_refs == creation.resource_plan.slot_refs


def test_empty_selection_needs_no_registry_and_does_not_enable_custom_facade(preserved_slot_fixture, monkeypatch):
    from cpn.rpnh.collaboration.candidate_plans import _supported_plan
    from cpn.rpnh.registry.event_store import RegistryConflict
    candidate, _, _ = _inputs(preserved_slot_fixture)
    result = read_preserved_slot_selection(None, candidate, preserved_basis=None, selected_slot_refs={}, selected_schema_refs={})
    assert result.basis is None and result.selected_slot_refs == {} and result.dependency_evidence == []
    assert candidate.source.components[0].key in {"slot_operation", "slot_resource_operation"}
    plan = {"preserved_slot_refs": {}, "host_bindings": {}, "runtime_dependencies": {},
        "host_resource_refs": [], "host_artifact_refs": []}
    with pytest.raises(RegistryConflict, match="operation"):
        _supported_plan(plan, candidate)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("axis", ["ports", "operations", "operation_handles", "port_handles", "place_aliases",
    "schema_version", "designer_constraints"])
def test_supplied_derived_compiled_fields_cannot_be_silently_regenerated(preserved_slot_fixture, axis):
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    if axis == "ports":
        candidate = replace(candidate, ports=(replace(candidate.ports[0], port_id="port_999"), *candidate.ports[1:]))
    elif axis == "operations":
        altered = dict(candidate.operations[0].executor_declaration, identity={"implementation_id": "other", "revision": "v1"})
        candidate = replace(candidate, operations=(replace(candidate.operations[0], executor_declaration=altered), *candidate.operations[1:]))
    elif axis in {"operation_handles", "port_handles", "place_aliases"}:
        data = dict(getattr(candidate, axis))
        data[next(iter(data))] = "different_handle"
        candidate = replace(candidate, **{axis: data})
    elif axis == "schema_version":
        candidate = replace(candidate, schema_version="rpnh/executable_net/v99")
    else:
        candidate = replace(candidate, source=replace(candidate.source, designer_constraints=False))
    # None cannot supply Registry authority; rejection must happen before IO.
    with pytest.raises(RegistryConflict, match="wire inventory"):
        read_preserved_slot_selection(None, candidate, preserved_basis=basis_for(preserved_slot_fixture[3]),
            selected_slot_refs=slots, selected_schema_refs=schemas)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("axis", ["missing_basis", "extra_empty_basis", "extra_empty_schema", "unknown_symbol",
    "wrong_slot", "missing_schema", "extra_schema", "duplicate_slot"])
def test_selection_has_a_closed_explicit_input_contract(preserved_slot_fixture, axis):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    owner, _, _, last, _ = preserved_slot_fixture
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    basis = basis_for(last)
    error = RegistryConflict
    if axis == "missing_basis":
        basis, error = None, TypeError
    elif axis == "extra_empty_basis":
        slots, schemas, error = {}, {}, ValueError
    elif axis == "extra_empty_schema":
        slots, basis, error = {}, None, ValueError
    elif axis == "unknown_symbol":
        slots = {"other.result_slot": next(iter(slots.values()))}
    elif axis == "wrong_slot":
        slots = {"step.result_slot": VersionRef("logical_artifact_slot/v1", new_id("logical_slot"), new_id("logical_slot_version"))}
    elif axis == "missing_schema":
        schemas = {}
    elif axis == "extra_schema":
        schemas["application/other/v1"] = next(iter(schemas.values()))
    else:
        slots["other.result_slot"] = next(iter(slots.values()))
    before = len(owner._core.event_store.list_events())
    with pytest.raises(error):
        read_preserved_slot_selection(owner._core, candidate, preserved_basis=basis,
            selected_slot_refs=slots, selected_schema_refs=schemas)
    assert len(owner._core.event_store.list_events()) == before


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("axis", ["mapping", "key", "slot_id", "schema_id", "compiled_container"])
def test_application_data_is_rejected_before_any_conversion_hook(preserved_slot_fixture, axis):
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    calls = []
    class ApplicationMeta(type):
        def __eq__(cls, other):
            calls.append("eq")
            return True
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)
    class ApplicationList(list, metaclass=ApplicationMeta):
        pass
    class ApplicationMapping(dict):
        def items(self):
            calls.append("items")
            return super().items()
    class ApplicationString(str):
        def __str__(self):
            calls.append("str")
            return super().__str__()
    class FakeId:
        kind = "logical_slot"
        def __str__(self):
            calls.append("id")
            return "not-an-id"
    if axis == "mapping":
        slots = ApplicationMapping(slots)
    elif axis == "key":
        slots = {ApplicationString("step.result_slot"): next(iter(slots.values()))}
    elif axis == "slot_id":
        ref = next(iter(slots.values()))
        slots = {"step.result_slot": VersionRef(ref.entity_type, FakeId(), ref.version_id)}
    elif axis == "schema_id":
        key, ref = next(iter(schemas.items()))
        fake = FakeId()
        fake.kind = "resource"
        schemas = {key: ResourceVersionRef(fake, ref.resource_version_id)}
    else:
        slot = replace(candidate.symbolic.logical_slots[0], route_transitions=ApplicationList())
        candidate = replace(candidate, symbolic=replace(candidate.symbolic, logical_slots=(slot,)))
    with pytest.raises(TypeError):
        read_preserved_slot_selection(None, candidate, preserved_basis=None, selected_slot_refs=slots, selected_schema_refs=schemas)
    assert calls == []


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("axis", ["artifact", "content", "schema", "remote_schema"])
def test_static_candidate_slot_or_schema_mismatch_rejects(preserved_slot_fixture, axis):
    from cpn.rpnh.executable_net import _load_compiled_net_offline
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    document = candidate.to_dict()
    if axis in {"artifact", "content"}:
        field = "artifact_id" if axis == "artifact" else "content_kind"
        document["fragments"]["step"]["logical_slots"][0][field] = "different"
        document["symbolic"]["logical_slots"][0][field] = "different"
        candidate = _load_compiled_net_offline(document)
    else:
        key = next(iter(schemas))
        changed = dict(candidate.registrations["schema"][key]["schema"])
        changed["title" if axis == "schema" else "$ref"] = "Different" if axis == "schema" else "https://invalid.example.invalid/no-retrieval"
        candidate.registrations["schema"][key]["schema"] = changed
    with pytest.raises(ContentSchemaAuthorityError if axis == "remote_schema" else RegistryConflict):
        read_preserved_slot_selection(preserved_slot_fixture[0]._core, candidate,
            preserved_basis=basis_for(preserved_slot_fixture[3]), selected_slot_refs=slots, selected_schema_refs=schemas)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_real_drop_and_recreate_never_substitute_creation_or_later_head(preserved_slot_fixture):
    from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
    from test_native_net_operations import _simple_module
    owner, _, _, last, _ = preserved_slot_fixture
    core = owner._core
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    old_basis = basis_for(last)
    original = read_preserved_slot_selection(core, candidate, preserved_basis=old_basis,
        selected_slot_refs=slots, selected_schema_refs=schemas)
    assert apply_replacement(owner, prepare_replacement(owner, _simple_module("WithoutSlot")),
        command_id="fixture:selection:drop")["status"] == "ADOPTED"
    dropped = core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    with pytest.raises(RegistryConflict, match="basis symbol"):
        read_preserved_slot_selection(core, candidate, preserved_basis=basis_for(dropped),
            selected_slot_refs=slots, selected_schema_refs=schemas)
    assert apply_replacement(owner, prepare_replacement(owner, _slot_module("Recreated")),
        command_id="fixture:selection:recreate")["status"] == "ADOPTED"
    latest = core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    latest_net = core.get_version(basis_for(latest).net_ref.version_id).metadata
    new_slots = {key: _version_from_payload(value) for key, value in latest_net["module_resource_bindings"]["slot_refs"].items()}
    assert set(new_slots) == set(slots) and new_slots["step.result_slot"] != slots["step.result_slot"]
    assert read_preserved_slot_selection(core, candidate, preserved_basis=old_basis,
        selected_slot_refs=slots, selected_schema_refs=schemas) == original
    for basis, selection in ((old_basis, new_slots), (basis_for(latest), slots)):
        with pytest.raises(RegistryConflict, match="basis symbol"):
            read_preserved_slot_selection(core, candidate, preserved_basis=basis,
                selected_slot_refs=selection, selected_schema_refs=schemas)
    assert read_preserved_slot_selection(core, candidate, preserved_basis=basis_for(latest),
        selected_slot_refs=new_slots, selected_schema_refs=schemas).selected_slot_refs == new_slots


def _alias_schema(owner, schema_ref, task_ref):
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    core = owner._core
    payload = core.object_store.read_registered(core.get_version(schema_ref.resource_version_id))
    bootstrap = owner.schema_gateway._bootstrap_ref
    alias = _publish_private_system(core, task_ref, PublishResource(origin=PrivateSystemOrigin(bootstrap),
        payload=payload, media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
        summary="Selection schema same bytes, different exact ref", lifetime_ref=bootstrap,
        idempotency_key="fixture:selection:schema-alias"))
    assert alias != schema_ref
    assert core.object_store.read_registered(core.get_version(alias.resource_version_id)) == payload
    return alias


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_equal_schema_bytes_under_another_ref_are_not_selected_authority(preserved_slot_fixture):
    owner, _, _, last, arguments = preserved_slot_fixture
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    schema_id, ref = next(iter(schemas.items()))
    schemas[schema_id] = _alias_schema(owner, ref, _version_from_payload(arguments["root"]["task_ref"]))
    with pytest.raises(RegistryConflict, match="exact basis output authority"):
        read_preserved_slot_selection(owner._core, candidate, preserved_basis=basis_for(last),
            selected_slot_refs=slots, selected_schema_refs=schemas)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_candidate_schema_comparison_keeps_boolean_and_number_distinct(preserved_slot_fixture):
    from cpn.rpnh.executable_net import _load_compiled_net_offline
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from test_candidate_plan_offline import _replace_resource_payload
    owner, creation, _, last, arguments = preserved_slot_fixture
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    core = owner._core
    schema_id, ref = next(iter(schemas.items()))
    schema = json.loads(core.object_store.read_registered(core.get_version(ref.resource_version_id)))
    schema["default"] = False
    for declaration in (creation.declaration_resource_ref, arguments["declaration_ref"]):
        wire = json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id)))
        wire["registrations"]["schema"][schema_id]["schema"] = schema
        _replace_resource_payload(core, declaration, canonical_json(wire))
    _replace_resource_payload(core, ref, canonical_json(schema))
    wire = candidate.to_dict()
    wire["registrations"]["schema"][schema_id]["schema"] = schema
    candidate = _load_compiled_net_offline(wire)
    read_preserved_slot_selection(core, candidate, preserved_basis=basis_for(last),
        selected_slot_refs=slots, selected_schema_refs=schemas)
    wire["registrations"]["schema"][schema_id]["schema"] = dict(schema, default=0)
    candidate = _load_compiled_net_offline(wire)
    with pytest.raises(RegistryConflict, match="schema bytes differ"):
        read_preserved_slot_selection(core, candidate, preserved_basis=basis_for(last),
            selected_slot_refs=slots, selected_schema_refs=schemas)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_slot_synopsis_can_change_without_changing_compatibility(preserved_slot_fixture):
    from cpn.rpnh.executable_net import _load_compiled_net_offline
    candidate, slots, schemas = _inputs(preserved_slot_fixture)
    wire = candidate.to_dict()
    for slot in (wire["fragments"]["step"]["logical_slots"][0], wire["symbolic"]["logical_slots"][0]):
        slot["artifact_synopsis"] = "Updated description"
    candidate = _load_compiled_net_offline(wire)
    assert read_preserved_slot_selection(preserved_slot_fixture[0]._core, candidate,
        preserved_basis=basis_for(preserved_slot_fixture[3]), selected_slot_refs=slots,
        selected_schema_refs=schemas).selected_slot_refs == slots


@pytest.fixture
def multiple_slot_fixture(tmp_path, monkeypatch):
    import socket
    import subprocess
    import urllib.request
    from cpn.rpnh.collaboration import author_material_schema_data
    from cpn.rpnh.net_operations import compose_modules, ComposePlan
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
    from cpn.rpnh.run import OwnerInput, start_run
    from test_native_net_operations import TEXT
    def forbidden(*args, **kwargs):
        raise AssertionError("multiple-slot fixture cannot use sockets, processes or network")
    for target, name in ((socket, "socket"), (subprocess, "Popen"), (urllib.request, "Request"), (urllib.request, "urlopen")):
        monkeypatch.setattr(target, name, forbidden)
    # Reuse the same real local slot_operation lower under its existing key.
    module = compose_modules({"left": _slot_module("Left"), "right": _slot_module("Right")},
        ComposePlan("MultipleSlots", "right", mode="parallel"))
    data, types, paths = author_material_schema_data()
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(module, _slot_registration(), run_dir=tmp_path / "multiple-slot-owner", task_input=task,
        entry_inputs={name: task for name in module.entry},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-selection", owner_statement="Offline two-slot selection",
        command_id="fixture:selection:multiple", catalog=SchemaCatalog(schemas=data, types=types, schema_paths=paths))
    owner.schema_gateway.bind_source_identity(source_id="multiple-slot-source", command_id="fixture:selection:source")
    core, publication = owner._core, owner.publication
    event = core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    candidate = compile_module(module, _slot_registration())
    slots = dict(publication.resource_plan.slot_refs)
    assert len(slots) == 2 and {component.key for component in module.components} == {"slot_operation"}
    output = core.get_version(next(iter(publication.output_refs.values())).version_id).metadata
    schemas = {output["content_schema_id"]: _resource_from_payload(output["content_schema_ref"])}
    return owner, publication, basis_for(event), candidate, slots, schemas


def test_explicit_subset_and_shared_schema_ref_are_supported(multiple_slot_fixture):
    owner, _, basis, candidate, slots, schemas = multiple_slot_fixture
    assert read_preserved_slot_selection(owner._core, candidate, preserved_basis=basis,
        selected_slot_refs=slots, selected_schema_refs=schemas).selected_slot_refs == slots
    subset = dict(list(slots.items())[:1])
    assert read_preserved_slot_selection(owner._core, candidate, preserved_basis=basis,
        selected_slot_refs=subset, selected_schema_refs=schemas).selected_slot_refs == subset


def test_shared_schema_id_cannot_silently_choose_between_distinct_exact_refs(multiple_slot_fixture):
    from cpn.rpnh.registry._preserved_resource_reads import read_preserved_resource_plan
    from cpn.rpnh.registry.strict_contracts import ref_payload, content_schema_ref_payload
    from test_candidate_plan_persistence import _replace_record
    owner, publication, basis, candidate, slots, schemas = multiple_slot_fixture
    core = owner._core
    root = core.get_version(publication.root_ref.version_id).metadata
    schema_id, original = next(iter(schemas.items()))
    alias = _alias_schema(owner, original, _version_from_payload(root["task_ref"]))
    right = next(name for name in slots if name.startswith("right_"))
    node = core.get_version(publication.node_refs["right_step.run"].version_id).metadata
    output_ref = _version_from_payload(node["offered_output_binding_refs"][0])
    output = core.get_version(output_ref.version_id).metadata
    spec_ref = _version_from_payload(output["opaque_action_ref"])
    pair = content_schema_ref_payload(alias)
    _replace_record(core, publication.root_ref, lambda body: body["resource_refs"].append(ref_payload(alias.as_version_ref())))
    _replace_record(core, output_ref, lambda body: body.update(content_schema_ref=pair, place_ref=ref_payload(alias.as_version_ref())))
    def alter_spec(body):
        for port in body["output_ports"]:
            if port["port_id"] == output["output_port_id"]:
                port["content_schema_ref"] = pair
    _replace_record(core, spec_ref, alter_spec)
    # A canonical finite historical basis can expose the same schema ID through
    # different exact resources. Each explicit selected set still needs one ref.
    assert read_preserved_resource_plan(core, basis).resource_plan.slot_refs == slots
    for selected_schemas in (schemas, {schema_id: alias}):
        with pytest.raises(RegistryConflict, match="exact basis output authority"):
            read_preserved_slot_selection(core, candidate, preserved_basis=basis,
                selected_slot_refs=slots, selected_schema_refs=selected_schemas)
    assert read_preserved_slot_selection(core, candidate, preserved_basis=basis,
        selected_slot_refs={right: slots[right]}, selected_schema_refs={schema_id: alias}).selected_slot_refs == {right: slots[right]}
