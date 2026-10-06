"""Shared source material closure, in one real Registry cut without HOST lower."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cpn.rpnh.collaboration import materials, assemblies, _assembly_lowering
from cpn.rpnh.collaboration.materials import AuthorMaterialReadContext
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
import test_collaboration_materials as author_cases
import test_collaboration_assemblies as assembly_cases


def read_context(core):
    return AuthorMaterialReadContext(core.object_store, core.catalog, core.task_id, core.branch_id)


def forbid_lower(monkeypatch, registration):
    def forbidden(*args, **kwargs):
        raise AssertionError("static material closure must not execute a HOST lowerer")
    monkeypatch.setattr(materials, "compile_module", forbidden)
    monkeypatch.setattr(_assembly_lowering, "compile_module", forbidden)
    monkeypatch.setattr(registration, "resolve", forbidden)


@pytest.mark.parametrize("has_parent", [False, True])
def test_static_author_closes_complete_materials_and_parent_without_host(tmp_path, monkeypatch, has_parent):
    core, _, author, registration, module, ids = author_cases.fixture.__wrapped__(tmp_path, SimpleNamespace())
    value = author.publish(module=module, element_ids=ids, command_id="author:first")
    if has_parent:
        value = author.publish(module=module, element_ids=ids, command_id="author:second", parent_ref=value.revision.revision_ref)
    context = read_context(core)
    assert not hasattr(context, "event_store") and not hasattr(context, "begin")
    before = len(core.event_store.list_events())
    forbid_lower(monkeypatch, registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        result = materials._validate_source_at(db, context, value.revision.revision_ref, materials._binding(db, context), set())
    assert result.compiled is None
    assert result.module == value.module and result.element_map == value.element_map
    assert result.boundary_map == value.boundary_map and result.host_requirements == value.host_requirements
    assert materials._match_author_compiled(result, value.compiled).compiled == value.compiled
    assert len(core.event_store.list_events()) == before


@pytest.mark.parametrize("axis", ["source", "host"])
def test_pure_author_bridge_rejects_another_real_compiled_inventory(tmp_path, axis):
    core, _, author, registration, module, ids = author_cases.fixture.__wrapped__(tmp_path, SimpleNamespace())
    value = author.publish(module=module, element_ids=ids, command_id="author:first")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        material = materials._validate_source_at(db, read_context(core), value.revision.revision_ref,
            materials._binding(db, core), set())
    if axis == "source":
        document = module.to_dict()
        document["name"] = "AnotherSource"
        other = compile_module(ModuleDeclaration.from_dict(document), registration)
    else:
        other = compile_module(module, author_cases._with_host_revision(registration, "another-host"))
    with pytest.raises(RegistryConflict, match="source/HOST"):
        materials._match_author_compiled(material, other)


@pytest.mark.parametrize("damage", ["element", "boundary", "host_refs", "result_identity"])
def test_static_author_reuses_full_material_and_command_identity_checks(tmp_path, monkeypatch, damage):
    core, _, author, registration, module, ids = author_cases.fixture.__wrapped__(tmp_path, SimpleNamespace())
    value = author.publish(module=module, element_ids=ids, command_id="author:first")
    changes = {}
    if damage != "result_identity":
        field, schema, document = {
            "element": ("element_mapping_ref", materials.ELEMENT_SCHEMA, deepcopy(value.element_map)),
            "boundary": ("boundary_mapping_ref", materials.BOUNDARY_SCHEMA, deepcopy(value.boundary_map)),
            "host_refs": ("host_requirements_ref", materials.HOST_SCHEMA, deepcopy(value.host_requirements)),
        }[damage]
        if damage == "element":
            document["elements"].pop()
        elif damage == "boundary":
            document["entries"] = []
        else:
            document["declaration_refs"].pop()
        changes[field] = author_cases._publish_material(core, author, document, schema)
    record = author_cases._publish_raw_revision(core, value.revision, **changes)
    forbid_lower(monkeypatch, registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
            materials._validate_source_at(db, read_context(core), record.revision_ref, materials._binding(db, core), set())


@pytest.mark.parametrize("mode", ["parallel", "serial", "parent", "final_context"])
def test_static_assembly_rebuilds_complete_final_context_without_host(tmp_path, monkeypatch, mode):
    core, _, author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    module = assembly_cases._simple_module()
    if mode == "final_context":
        from cpn.rpnh.petri_contracts import PlaceDeclaration
        base = registration.resolve("component", "operation")
        def lower(config, context):
            fragment = base(config, context)
            transition = "derived_" + context.component
            return replace(fragment, places=(*fragment.places, PlaceDeclaration(transition, assembly_cases.TEXT)),
                transitions=(*fragment.transitions, replace(fragment.transitions[0], name=transition)),
                arcs=(*fragment.arcs, *(replace(arc, transition=transition) for arc in fragment.arcs)))
        registration.register_component("context-derived", lower,
            identity={"implementation_id": "test.context-derived", "revision": "v1"},
            contracts=registration.declaration("component", "operation")["contracts"])
        document = module.to_dict()
        document["components"][0]["key"] = "context-derived"
        module = ModuleDeclaration.from_dict(document)
    member, ids = assembly_cases._member(author, module)
    if mode == "parent":
        member, ids = assembly_cases._member(author, module, command="member:second",
            parent=member.revision.revision_ref, ids=ids)
    arguments = assembly_cases._request(member, ids,
        connections=assembly_cases._serial(ids) if mode == "serial" else ())
    value = author.publish(**arguments)
    if mode == "parent":
        value = author.publish(**dict(arguments, command_id="assembly:second", parent_ref=value.revision.revision_ref))
    before = len(core.event_store.list_events())
    forbid_lower(monkeypatch, registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        result = assemblies._validate_assembly_at(db, read_context(core), value.revision.revision_ref,
            None, materials._binding(db, core), set(), _static=True)
    assert result.compiled == value.compiled
    assert result.lowering_map == value.lowering_map and result.generated.element_map == value.generated.element_map
    assert len(core.event_store.list_events()) == before


@pytest.mark.parametrize("damage", ["lowering_map", "generated", "compiled"])
def test_static_assembly_retains_full_generated_inventory_and_map_checks(tmp_path, monkeypatch, damage):
    core, _, author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    member, ids = assembly_cases._member(author)
    value = author.publish(**assembly_cases._request(member, ids, connections=assembly_cases._serial(ids)))
    if damage == "generated":
        record = assembly_cases._raw_record(core, value.revision, generated_revision_ref=member.revision.revision_ref)
    else:
        if damage == "lowering_map":
            field, schema, document = "lowering_mapping_ref", assemblies.LOWERING_SCHEMA, deepcopy(value.lowering_map)
            document["origins"].pop()
        else:
            field, schema, document = "compiled_inventory_ref", assemblies.COMPILED_SCHEMA, value.compiled.to_dict()
            document["source"]["name"] = "AnotherSource"
        record = assembly_cases._raw_record(core, value.revision,
            **{field: assembly_cases._resource(author, document, schema)})
    forbid_lower(monkeypatch, registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
            assemblies._validate_assembly_at(db, read_context(core), record.revision_ref,
                None, materials._binding(db, core), set(), _static=True)


@pytest.mark.parametrize("relationship", ["member_final", "member_member"])
@pytest.mark.parametrize("base_revision,alternate_revision", [("v1", "alternate-host-v2"), (1, True)])
def test_static_assembly_rejects_shared_host_identity_drift(tmp_path, monkeypatch, relationship, base_revision, alternate_revision):
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.schema_catalog import canonical_json

    class AlternateGateway(RegistryRegistrationGateway):
        def __call__(self, kind, key, declaration):
            if kind != "executor":
                return super().__call__(kind, key, declaration)
            # Publish a second actual immutable HOST declaration, rather than
            # editing an existing resource or fabricating canonical evidence.
            ref = _publish_private_system(self._core, self._task_ref, PublishResource(
                origin=PrivateSystemOrigin(self._bootstrap_ref), payload=canonical_json(declaration),
                media_type="application/json", content_schema_ref=None, summary="Alternate exact HOST",
                lifetime_ref=self._bootstrap_ref, descriptors={"host_registration_kind": kind, "registered_key": key},
                idempotency_key="fixture:host:" + canonical_json(declaration).decode()))
            self._refs[kind, key] = ref
            return ref

    core, gateway, initial_author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    base_registration = author_cases._with_host_revision(registration, base_revision)
    alternate_registration = author_cases._with_host_revision(registration, alternate_revision)
    base_author = assemblies.AssemblyAuthor(AlternateGateway(core, gateway._task_ref, gateway._bootstrap_ref),
        base_registration, initial_author.producer)
    alternate_author = assemblies.AssemblyAuthor(AlternateGateway(core, gateway._task_ref, gateway._bootstrap_ref),
        alternate_registration, initial_author.producer)
    first, ids = assembly_cases._member(base_author, command="member:base")
    arguments = assembly_cases._request(first, ids)
    final_author, final_registration = alternate_author, alternate_registration
    if relationship == "member_member":
        second, _ = assembly_cases._member(alternate_author, command="member:alternate")
        arguments["members"] = (assemblies.AssemblyMember(assembly_cases.A, "First", first.revision.revision_ref),
            assemblies.AssemblyMember(assembly_cases.B, "Second", second.revision.revision_ref))
        arguments["completion"] = assemblies.AssemblyCompletion(assembly_cases.A, ids["/terminal"])
        final_author, final_registration = base_author, base_registration
    original_members = assemblies._members_at
    def source_only(db, core, plan, registration, binding, **kwargs):
        return original_members(db, core, plan, None, binding, _static=True)
    # A hypothetical old producer creates the malformed but canonical record.
    # Actual resources, command IDs, relations and all other checks stay real.
    with monkeypatch.context() as old_producer:
        old_producer.setattr(assemblies, "_members_at", source_only)
        old_producer.setattr(materials._HostDeclarationClosure, "observe", lambda self, inventory: None)
        value = final_author.publish(**arguments)
    with pytest.raises(RegistryConflict):
        assemblies.validate_assembly_revision(core, value.revision.revision_ref, final_registration)
    forbid_lower(monkeypatch, final_registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="shared HOST"):
            assemblies._validate_assembly_at(db, read_context(core), value.revision.revision_ref,
                None, materials._binding(db, core), set(), _static=True)


def test_static_assembly_allows_different_consumed_dependency_sets(tmp_path, monkeypatch):
    core, _, author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    first, ids = assembly_cases._member(author)
    document = assembly_cases._simple_module(output_schema=assembly_cases.ALT_TEXT).to_dict()
    original_terminal = document["terminal"]["key"]
    alternate_terminal = "test/other-terminal/v1"
    registration.register_tool(alternate_terminal, registration.resolve("tool", original_terminal),
        identity={"implementation_id": "test.other-terminal", "revision": "v1"},
        contracts=registration.declaration("tool", original_terminal)["contracts"])
    document["terminal"]["key"] = alternate_terminal
    second, second_ids = assembly_cases._member(author, ModuleDeclaration.from_dict(document), command="member:other")
    value = author.publish(**assembly_cases._request(first, ids,
        members=(assemblies.AssemblyMember(assembly_cases.A, "First", first.revision.revision_ref),
                 assemblies.AssemblyMember(assembly_cases.B, "Second", second.revision.revision_ref)),
        completion=assemblies.AssemblyCompletion(assembly_cases.B, second_ids["/terminal"])))
    first_keys = {(kind, key) for kind, values in first.host_requirements["registrations"].items() for key in values}
    second_keys = {(kind, key) for kind, values in second.host_requirements["registrations"].items() for key in values}
    final_keys = {(kind, key) for kind, values in value.compiled.to_dict()["registrations"].items() for key in values}
    assert first_keys != second_keys
    assert ("tool", original_terminal) in first_keys and ("tool", original_terminal) not in final_keys
    assert ("tool", alternate_terminal) in final_keys
    forbid_lower(monkeypatch, registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        actual = assemblies._validate_assembly_at(db, read_context(core), value.revision.revision_ref,
            None, materials._binding(db, core), set(), _static=True)
    assert actual.compiled == value.compiled


def variant_author(core, gateway, registration, producer, *, category, selected_key):
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.schema_catalog import canonical_json
    changed = Registration()
    for declaration in registration.declarations():
        kind, key = declaration["kind"], declaration["key"]
        if kind == "schema":
            changed.register_schema(key, declaration["schema"])
        else:
            identity = dict(declaration["identity"])
            if (kind, key) == (category, selected_key):
                identity["revision"] = "historical-drift"
            getattr(changed, "register_" + kind)(key, registration.resolve(kind, key),
                identity=identity, contracts=declaration["contracts"])
    class Gateway(RegistryRegistrationGateway):
        def __call__(self, kind, key, declaration):
            if (kind, key) != (category, selected_key):
                return super().__call__(kind, key, declaration)
            ref = _publish_private_system(self._core, self._task_ref, PublishResource(
                origin=PrivateSystemOrigin(self._bootstrap_ref), payload=canonical_json(declaration),
                media_type="application/json", content_schema_ref=None, summary="Historical alternate HOST",
                lifetime_ref=self._bootstrap_ref, descriptors={"host_registration_kind": kind, "registered_key": key},
                idempotency_key="fixture:historical-host:" + canonical_json(declaration).decode()))
            self._refs[kind, key] = ref
            return ref
    return assemblies.AssemblyAuthor(Gateway(core, gateway._task_ref, gateway._bootstrap_ref), changed, producer), changed


def historical_pair(author, registration):
    first, first_ids = assembly_cases._member(author, command="member:historical-first")
    document = assembly_cases._simple_module().to_dict()
    old_key = document["terminal"]["key"]
    other_key = "test/historical-other-terminal/v1"
    registration.register_tool(other_key, registration.resolve("tool", old_key),
        identity={"implementation_id": "test.historical-other-terminal", "revision": "v1"},
        contracts=registration.declaration("tool", old_key)["contracts"])
    document["terminal"]["key"] = other_key
    second, second_ids = assembly_cases._member(author, ModuleDeclaration.from_dict(document), command="member:historical-second")
    parent = author.publish(**assembly_cases._request(first, first_ids,
        members=(assemblies.AssemblyMember(assembly_cases.A, "First", first.revision.revision_ref),
                 assemblies.AssemblyMember(assembly_cases.B, "Second", second.revision.revision_ref)),
        completion=assemblies.AssemblyCompletion(assembly_cases.B, second_ids["/terminal"])))
    assert old_key not in parent.compiled.to_dict()["registrations"]["tool"]
    return first, second, second_ids, parent, old_key


@pytest.mark.parametrize("ancestry", ["author_parent", "assembly_parent_member_only"])
def test_static_assembly_shares_host_table_through_complete_ancestry(tmp_path, monkeypatch, ancestry):
    core, gateway, author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    if ancestry == "author_parent":
        first, ids = assembly_cases._member(author)
        alternate, alternate_registration = variant_author(core, gateway, registration, author.producer,
            category="executor", selected_key=first.compiled.operations[0].executor_key)
        parent_assembly = None
    else:
        first, _, _, parent_assembly, old_key = historical_pair(author, registration)
        alternate, alternate_registration = variant_author(core, gateway, registration, author.producer,
            category="tool", selected_key=old_key)
    with monkeypatch.context() as old_producer:
        old_producer.setattr(materials._HostDeclarationClosure, "observe", lambda self, inventory: None)
        old_producer.setattr(materials, "_match_author_compiled", lambda material, compiled: replace(material, compiled=compiled))
        if ancestry == "author_parent":
            child, child_ids = assembly_cases._member(alternate, command="member:historical-child",
                parent=first.revision.revision_ref, ids=ids)
        else:
            child, child_ids = assembly_cases._member(alternate, command="member:historical-child")
        value = alternate.publish(**assembly_cases._request(child, child_ids, command_id="assembly:historical-child",
            parent_ref=None if parent_assembly is None else parent_assembly.revision.revision_ref))
    forbid_lower(monkeypatch, alternate_registration)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="shared HOST"):
            assemblies._validate_assembly_at(db, read_context(core), value.revision.revision_ref,
                None, materials._binding(db, core), set(), _static=True)


def test_static_assembly_keeps_historical_unique_host_key_without_requiring_current_use(tmp_path, monkeypatch):
    core, _, author, registration = assembly_cases.fixture.__wrapped__(tmp_path)
    _, second, second_ids, parent, old_key = historical_pair(author, registration)
    value = author.publish(**assembly_cases._request(second, second_ids, command_id="assembly:historical-unique",
        parent_ref=parent.revision.revision_ref))
    assert old_key not in second.host_requirements["registrations"]["tool"]
    assert old_key not in value.compiled.to_dict()["registrations"]["tool"]
    forbid_lower(monkeypatch, registration)
    closure = materials._HostDeclarationClosure()
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        result = assemblies._validate_assembly_at(db, read_context(core), value.revision.revision_ref,
            None, materials._binding(db, core), set(), _static=True, _host_closure=closure)
    assert ("tool", old_key) in closure.declarations
    assert result.compiled == value.compiled
