"""Real offline source -> persisted open root, then exact reopened consumption."""
from copy import deepcopy
import json
import uuid

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.collaboration import (ClosedModuleAuthor, OpenRegionAuthor, SourceQualifiedVersionRef,
    OpenRegionClosureAuthor, open_region_assembly_schema_data, validate_closed_revision, validate_open_revision)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json

TEXT = "application/open_region_text/v1"
EXECUTOR = "test/open-region-business/v1"
TERMINAL = "test/open-region-terminal/v1"


def _module():
    components = []
    for name in ("A", "B", "C"):
        components.append({"name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                      {"name": "result", "direction": "output", "schema": TEXT}],
            "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request"], "outputs": ["result"],
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]})
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1", "name": "ABC",
        "components": components, "links": [
            {"source": {"component": a, "port": "result"}, "target": {"component": b, "port": "request"}}
            for a, b in (("A", "B"), ("B", "C"))],
        "entry": {"request": {"component": "A", "port": "request"}},
        "exit": {"result": {"component": "C", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "C", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT], "budgets": {}})


def _registration():
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, {"$id": TEXT, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    registration.register_executor(EXECUTOR, lambda **_kwargs: None,
        identity={"implementation_id": "test.open_region_business", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.open_region_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = open_region_assembly_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(tmp_path / "open-region", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("open-region-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-open", command_id="bind:open")
    ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Offline author"}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="fixture:principal")
    registration = _registration()
    producer = SourceQualifiedVersionRef("source-open", ref)
    closed = ClosedModuleAuthor(gateway, registration, producer)
    author = OpenRegionAuthor(gateway, registration, producer)
    module = _module()
    ids = {path: "element:" + uuid.uuid4().hex for path in _elements(module)}
    source = closed.publish(module=module, element_ids=ids, command_id="source:ABC")
    return core, gateway, closed, author, registration, source, ids


def _counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events())


def _publish(fixture, **changes):
    _, _, _, author, _, source, ids = fixture
    request = {"source_revision_ref": source.revision.revision_ref,
               "component_element_ids": [ids["/components/B"]], "lineage_mode": "new_lineage", "command_id": "open:B"}
    return author.publish(**{**request, **changes})


def test_open_nonterminal_is_persisted_reopened_and_never_a_closed_module(fixture):
    core, gateway, _, _, registration, source, ids = fixture
    opened = _publish(fixture)
    assert opened.revision.parent_revision_refs == opened.revision.selected_change_refs == ()
    assert opened.revision.revision_ref.ref.entity_id != source.revision.revision_ref.ref.entity_id
    assert [row["name"] for row in opened.definition["components"]] == ["B"]
    assert opened.definition["completion"] is None
    assert opened.definition["internal_links"] == []
    assert {row["kind"] for row in opened.element_map["elements"]} == {"component", "operation", "port"}
    assert set(ids.values()).isdisjoint(row["element_id"] for row in opened.element_map["elements"])
    assert len(opened.contract["unresolved_item_ids"]) == 3  # A->B, B->C and declared C completion.
    assert len(opened.boundary_inventory["coverage"]) == 10
    assert all(row["status"] == "complete" and row["source_locations"] for row in opened.boundary_inventory["coverage"])
    assert any(row["material_role"] == "compiled_inventory" for item in opened.boundary_inventory["items"] for row in item["source_locations"])
    branch = gateway.create_author_branch(head_revision_ref=opened.revision.revision_ref, command_id="branch:open")
    assert branch.head_revision_ref == opened.revision.revision_ref
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = _counts(core)
    assert validate_open_revision(reader, opened.revision.revision_ref, registration) == opened
    with pytest.raises(ValueError, match="already closed_module"):
        validate_closed_revision(reader, opened.revision.revision_ref, registration)
    assert _publish(fixture) == opened
    assert _counts(core) == before
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1")) == ()


def test_open_selection_and_complete_command_conflicts_do_not_write(fixture):
    core, _, _, _, _, _, ids = fixture
    opened = _publish(fixture)
    before = _counts(core)
    for changes in ({"component_element_ids": [ids["/terminal"]]}, {"lineage_mode": None},
                    {"component_element_ids": [ids["/components/B"], ids["/components/B"]]},
                    {"component_element_ids": [ids["/components/C"]]}):
        with pytest.raises((ValueError, RegistryConflict)):
            _publish(fixture, **changes)
        assert _counts(core) == before
    assert opened.contract["claim"] == "open_only"


def test_open_interrupted_preparation_resumes_only_the_frozen_request(fixture, monkeypatch):
    from cpn.rpnh.collaboration import open_region
    core, _, _, _, registration, _, ids = fixture
    original = open_region._publish_private_system
    calls = 0
    def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("offline preparation interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(open_region, "_publish_private_system", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        _publish(fixture)
    monkeypatch.setattr(open_region, "_publish_private_system", original)
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="command_conflict"):
        _publish(fixture, component_element_ids=[ids["/components/C"]])
    assert _counts(core) == before
    opened = _publish(fixture)
    assert validate_open_revision(core, opened.revision.revision_ref, registration) == opened


def _closure_request(fixture, opened, *, command_id="close:BC", assembly_command_id="assembly:BC", member_id=None):
    from cpn.rpnh.collaboration._open_region_closure import RESOLVER
    core, gateway, closed, _, registration, source, ids = fixture
    author = OpenRegionClosureAuthor(gateway, registration, closed.producer)
    incoming = next(row for row in opened.boundary_inventory["items"] if any(
        location["material_role"] == "definition" and location["json_pointer"] == "/links/0"
        for location in row["source_locations"]))
    intent = {"schema_version": "rpnh/collaboration/prospective_member_intent/v1", "kind": "prospective",
        "source_id": "source-open", "owner_task_ref": source.revision.owner_task_ref.to_dict(),
        "assembly_protocol": "collaboration_assembly_revision/v4", "resolver_contract": RESOLVER,
        "assembly_command_id": assembly_command_id, "parent_assembly_revision_ref": None, "target_scope": "direct_member",
        "member_id": member_id or "member:" + uuid.uuid4().hex, "completion_expectation": "member_primary_terminal",
        "ingress_expectations": [{"item_id": incoming["item_id"], "kind": "public_entry", "other_member_id": None,
            "other_revision_ref": None, "other_exit_element_id": None}]}
    before = _counts(core)
    request = author.prepare_request(open_revision_ref=opened.revision.revision_ref, provenance_ref=opened.provenance_ref,
        context={"source_revision_ref": source.revision.revision_ref.to_dict(), "element_ids": [ids["/components/C"]]},
        completion={"primary_terminal_element_id": ids["/terminal"], "alternative_terminal_element_ids": []},
        extract_plan={"kind": "components", "components": ["B", "C"],
            "boundary_policy": "preserve_all_dependencies", "output_name": "BC"}, intent=intent, command_id=command_id)
    assert _counts(core) == before
    return author, request


def test_explicit_context_closes_new_root_and_reopened_full_consumer_proves_it(fixture):
    from cpn.rpnh.collaboration import ValidatedAdaptedRevision
    core, gateway, closed, _, registration, source, ids = fixture
    opened = _publish(fixture)
    author, request = _closure_request(fixture, opened)
    result = author.publish(request=request, command_id="close:BC")
    assert type(result) is ValidatedAdaptedRevision
    assert [row.name for row in result.module.components] == ["B", "C"]
    assert result.module.terminal.source.component == "C"
    assert result.revision.parent_revision_refs == result.revision.selected_change_refs == ()
    assert result.revision.open_region_contract_ref is None
    assert result.revision.revision_ref.ref.entity_id not in {
        opened.revision.revision_ref.ref.entity_id, source.revision.revision_ref.ref.entity_id}
    assert result.adaptation_claim == "exact_current_result"
    assert result.target_status == "prospective_unverified"
    assert {row["result_element_id"] for row in result.origin_map["elements"]} == {
        row["element_id"] for row in result.element_map["elements"]}
    open_branch = gateway.create_author_branch(head_revision_ref=opened.revision.revision_ref, command_id="branch:open")
    with pytest.raises((ValueError, RegistryConflict)):
        gateway.advance_author_branch(expected_branch_version_ref=open_branch.branch_ref,
            expected_head_revision_ref=opened.revision.revision_ref, expected_stream_head=1,
            next_revision_ref=result.revision.revision_ref, command_id="branch:invalid-closure")
    closed_branch = gateway.create_author_branch(head_revision_ref=result.revision.revision_ref, command_id="branch:closed")
    assert closed_branch.head_revision_ref == result.revision.revision_ref
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = _counts(core)
    reread = validate_closed_revision(reader, result.revision.revision_ref, registration)
    assert type(reread) is ValidatedAdaptedRevision
    assert reread.adaptation == result.adaptation
    assert author.publish(request=request, command_id="close:BC").revision == result.revision
    with pytest.raises(RegistryConflict, match="legacy author"):
        closed.publish(module=result.module, element_ids={row["locator"]: row["element_id"] for row in result.element_map["elements"]},
            command_id="invalid:ordinary-after-D", parent_ref=result.revision.revision_ref)
    assert _counts(core) == before


def test_closure_request_cannot_invent_completion_drop_dispositions_or_change_intent(fixture):
    core, _, _, _, _, _, ids = fixture
    opened = _publish(fixture)
    author, request = _closure_request(fixture, opened)
    author.publish(request=request, command_id="close:BC")
    before = _counts(core)
    for field, value in (("completion", {"primary_terminal_element_id": ids["/components/B"], "alternative_terminal_element_ids": []}),
                         ("dispositions", []), ("context", {**request["context"], "element_ids": []}),
                         ("intent", {**request["intent"], "assembly_command_id": "assembly:another"})):
        changed = deepcopy(request)
        changed[field] = value
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(request=changed, command_id="close:BC")
        assert _counts(core) == before


def test_actual_v4_consumes_exact_prospective_target_and_independent_generated_pair(fixture):
    from dataclasses import replace
    from cpn.rpnh.collaboration import (AssemblyAuthorV4, AssemblyMemberV4, AssemblyCompletion,
        ValidatedClosedRevision, validate_assembly_revision, read_assembly_revision, AssemblyAuthor, AssemblyMember, AssemblyAuthorV2,
        AssemblyMemberV2, AssemblyAuthorV3, AssemblyMemberV3)
    core, gateway, closed, _, registration, _, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    adapted = closer.publish(request=request, command_id="close:BC")
    author = AssemblyAuthorV4(gateway, registration, closed.producer)
    identity = adapted.intent["member_id"]
    member = AssemblyMemberV4(identity, "Completed B", adapted.revision.revision_ref, "adapted_result_current",
                              adapted.adaptation_ref, adapted.intent_ref)
    primary = next(row["element_id"] for row in adapted.element_map["elements"] if row["locator"] == "/terminal")
    completion = AssemblyCompletion(identity, primary)
    options = dict(name="ComposedBC", members=[member], connections=[], completion=completion,
        budget_policy="shared_exact", deployment_intent="same_run_candidate", command_id="assembly:BC")
    actual = author.publish(**options)
    assert actual.revision.revision_ref.ref.entity_type == "collaboration_assembly_revision/v4"
    assert len(actual.lowering_map["target_matches"]) == 1
    assert actual.lowering_map["target_matches"][0]["status"] == "matched_exact_target"
    assert actual.lowering_map["member_proofs"][0]["adaptation_ref"] == adapted.adaptation_ref.to_dict()
    assert all(row["fields"] for row in actual.lowering_map["fragment_origins"])
    assert type(actual.generated) is ValidatedClosedRevision
    assert not hasattr(actual.generated, "adaptation_claim")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = _counts(core)
    proof = validate_assembly_revision(reader, actual.revision.revision_ref, registration)
    assert read_assembly_revision(reader, actual.revision.revision_ref) == actual.revision
    assert proof.lowering_map == actual.lowering_map
    assert type(validate_closed_revision(reader, actual.revision.generated_revision_ref, registration)) is ValidatedClosedRevision
    assert author.publish(**options).revision == actual.revision
    for changes in ({"command_id": "assembly:another"},
                    {"members": [replace(member, claim="plain_closed_v1", adaptation_ref=None, intent_ref=None)]},
                    {"members": [replace(member, member_id="member:" + uuid.uuid4().hex)]}):
        with pytest.raises((ValueError, RegistryConflict)):
            author.publish(**{**options, **changes})
        assert _counts(core) == before
    # Real legacy producer/resolver entries must not classify D as ordinary.
    for cls, member_cls in ((AssemblyAuthor, AssemblyMember), (AssemblyAuthorV2, AssemblyMemberV2),
                            (AssemblyAuthorV3, AssemblyMemberV3)):
        legacy = cls(gateway, registration, closed.producer)
        counts = _counts(core)
        with pytest.raises(RegistryConflict, match="legacy Assembly"):
            legacy.publish(**{**options, "members": [member_cls(identity, "D", adapted.revision.revision_ref)],
                              "command_id": "legacy:" + cls.__name__})
        assert _counts(core) == counts


def test_closure_interruption_locks_intent_and_complete_output_before_revision(fixture, monkeypatch):
    from cpn.rpnh.collaboration import open_region
    core, _, _, _, registration, _, _ = fixture
    opened = _publish(fixture)
    author, request = _closure_request(fixture, opened)
    original, calls = open_region._publish_private_system, 0
    def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("closure preparation interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(open_region, "_publish_private_system", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        author.publish(request=request, command_id="close:BC")
    monkeypatch.setattr(open_region, "_publish_private_system", original)
    before = _counts(core)
    changed = deepcopy(request)
    changed["intent"]["assembly_command_id"] = "assembly:changed"
    with pytest.raises(RegistryConflict, match="command_conflict"):
        author.publish(request=changed, command_id="close:BC")
    assert _counts(core) == before
    result = author.publish(request=request, command_id="close:BC")
    assert validate_closed_revision(core, result.revision.revision_ref, registration).adaptation == result.adaptation


def test_canonical_legacy_descendant_cannot_erase_adaptation_at_full_reader(fixture):
    from cpn.rpnh.collaboration import NetRevision, SourceQualifiedResourceRef, read_net_revision
    from cpn.rpnh.collaboration.materials import (_command_material, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.schema_catalog import canonical_text
    core, gateway, closed, _, registration, _, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    parent = closer.publish(request=request, command_id="close:BC")
    command_id = "legacy:canonical-E"
    key = "collaboration-author:" + canonical_text({"command_id": command_id})
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": "source-open", "kind": "resource_version"})).hex)
    reference = SourceQualifiedVersionRef("source-open", VersionRef("collaboration_net_revision/v1",
        parent.revision.revision_ref.ref.entity_id, version))
    documents = (parent.module.to_dict(), parent.element_map, parent.boundary_map, parent.host_requirements)
    marker = _command_material(source_id="source-open", owner=parent.revision.owner_task_ref,
        producer=closed.producer, command_id=command_id, parents=(parent.revision.revision_ref,), documents=documents)
    refs = []
    for i, (schema, document) in enumerate(zip((MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA), documents)):
        resource = _publish_private_system(core, gateway._task_ref, PublishResource(
            origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=canonical_json(document), media_type="application/json",
            content_schema_ref=schema, content_schema_authority_ref=closed.schemas[schema], summary=f"Closed author material {i}",
            lifetime_ref=gateway._bootstrap_ref, descriptors={"closed_author_command_v1": marker} if i == 0 else {},
            idempotency_key=f"{key}:material:{i}"))
        refs.append(SourceQualifiedResourceRef("source-open", resource))
    record = NetRevision(reference, parent.revision.owner_task_ref, closed.producer, command_id, "closed_module",
        refs[0], (parent.revision.revision_ref,), (), refs[1], refs[2], refs[3], None)
    core.publish_bytes(object_type="collaboration_net_revision/v1", logical_id=reference.ref.entity_id,
        version_id=reference.ref.version_id, payload=canonical_json(record.to_dict()), metadata=record.to_dict(),
        media_type="application/json", schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key=key)
    before = _counts(core)
    assert read_net_revision(core, reference, local_source_id="source-open") == record
    with pytest.raises(RegistryConflict, match="legacy author"):
        validate_closed_revision(core, reference, registration)
    assert _counts(core) == before


def test_complete_open_and_closure_proofs_reject_real_byte_damage_and_restore(fixture):
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    core, _, _, _, registration, _, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    result = closer.publish(request=request, command_id="close:BC")
    before = _counts(core)
    cases = []
    for owner, read in ((opened, validate_open_revision), (result, validate_closed_revision)):
        command_path = core.object_store.path_for_version(owner.command_ref.ref.resource_version_id)
        command = json.loads(command_path.read_bytes())
        refs = [owner.command_ref, *[SourceQualifiedResourceRef.from_dict(row["resource_ref"], catalog=core.catalog)
                                   for row in command["prepared_materials"]]]
        for ref in refs:
            path = core.object_store.path_for_version(ref.ref.resource_version_id)
            original = path.read_bytes()
            changed = json.dumps(dict(reversed(list(json.loads(original).items()))),
                ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
            assert len(changed) == len(original) and changed != original
            try:
                path.write_bytes(changed)
                with pytest.raises((RegistryConflict, ObjectIntegrityError)):
                    read(core, owner.revision.revision_ref, registration)
                cases.append(ref.to_dict())
            finally:
                path.write_bytes(original)
        assert read(core, owner.revision.revision_ref, registration).revision == owner.revision
    assert len(cases) == 15
    assert _counts(core) == before


def test_actual_control_feedback_and_unknown_lowering_are_never_dropped(fixture, monkeypatch):
    from dataclasses import replace
    from cpn.components.basic import lower_operation
    from cpn.rpnh.petri_contracts import PlaceDeclaration, ArcDeclaration
    core, gateway, closed, author, registration, source, ids = fixture
    # A real control ingress uses typed ports and actual control places.
    control_doc = source.module.to_dict()
    control_doc["components"][0]["ports"][1]["channel"] = "control"
    control_doc["components"][1]["ports"][0]["channel"] = "control"
    control = closed.publish(module=ModuleDeclaration.from_dict(control_doc), element_ids=ids, command_id="source:control")
    control_fixture = (core, gateway, closed, author, registration, control, ids)
    opened = _publish(control_fixture, command_id="open:control")
    assert any(row["kind"] == "control" and row["extraction_disposition"] == "boundary" for row in opened.boundary_inventory["items"])
    closer, request = _closure_request(control_fixture, opened, command_id="close:control")
    adapted = closer.publish(request=request, command_id="close:control")
    assert adapted.module.components[0].ports[0].channel == "control"
    # A supported basic lowerer can still expose a return/read protocol that
    # this closing adapter cannot dispose of. O must retain that obligation.
    feedback_doc = source.module.to_dict()
    feedback_doc["components"][1]["config"] = {"input_modes": {"request": "read"}}
    feedback = closed.publish(module=ModuleDeclaration.from_dict(feedback_doc), element_ids=ids, command_id="source:feedback")
    feedback_fixture = (core, gateway, closed, author, registration, feedback, ids)
    feedback_open = _publish(feedback_fixture, command_id="open:feedback")
    assert any(row["kind"] == "feedback" and row["extraction_disposition"] == "needs_adapter"
               for row in feedback_open.boundary_inventory["items"])
    with pytest.raises(ValueError, match="unresolved_boundary"):
        _closure_request(feedback_fixture, feedback_open, command_id="close:feedback")
    assert validate_open_revision(core, feedback_open.revision.revision_ref, registration).contract["unresolved_item_ids"]
    # Same typed declarations, but actual extra control dependency in lowering:
    # nominal key/metadata cannot prove the supported reconstruction adapter.
    resolve = registration.resolve
    def hidden_dependency(config, context):
        fragment = lower_operation(config, context)
        if context.component == "B":
            return replace(fragment, places=(*fragment.places, PlaceDeclaration("external_gate", TEXT, channel="control")),
                arcs=(*fragment.arcs, ArcDeclaration("external_gate", "run", "input", 1, "read")))
        return fragment
    def selected_resolve(kind, key):
        return hidden_dependency if (kind, key) == ("component", "operation") else resolve(kind, key)
    monkeypatch.setattr(registration, "resolve", selected_resolve)
    unknown = closed.publish(module=source.module, element_ids=ids, command_id="source:unknown")
    unknown_fixture = (core, gateway, closed, author, registration, unknown, ids)
    unknown_open = _publish(unknown_fixture, command_id="open:unknown")
    assert all(row["status"] == "unsupported" for row in unknown_open.boundary_inventory["coverage"])
    assert any("unsupported_actual_fragment" in row["contract"]["unknowns"] for row in unknown_open.boundary_inventory["items"])
    with pytest.raises(ValueError, match="unresolved_boundary"):
        _closure_request(unknown_fixture, unknown_open, command_id="close:unknown")
    assert validate_open_revision(core, unknown_open.revision.revision_ref, registration).boundary_inventory == unknown_open.boundary_inventory


def test_failed_final_cut_reopens_owner_and_resumes_only_original_closure(fixture, monkeypatch):
    from cpn.rpnh.collaboration import open_region
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    core, gateway, closed, _, _, source, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    path = core.object_store.path_for_version(source.revision.definition_ref.ref.resource_version_id)
    raw = path.read_bytes()
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
    assert len(raw) == len(changed) and raw != changed
    publish = open_region._publish_private_system
    def damage_after_last_material(core, owner, resource):
        result = publish(core, owner, resource)
        if resource.idempotency_key.endswith(":host_requirements"):
            path.write_bytes(changed)
        return result
    monkeypatch.setattr(open_region, "_publish_private_system", damage_after_last_material)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            closer.publish(request=request, command_id="close:BC")
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(open_region, "_publish_private_system", publish)
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == 2  # S and O, no D.
    after_cut = _counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = _registration()
    next_author = OpenRegionClosureAuthor(next_gateway, next_registration, closed.producer)
    assert reopened.writer_epoch != core.writer_epoch and _counts(reopened) == after_cut
    changed_request = deepcopy(request)
    changed_request["intent"]["assembly_command_id"] = "assembly:new-target"
    with pytest.raises(RegistryConflict, match="command_conflict"):
        next_author.publish(request=changed_request, command_id="close:BC")
    assert _counts(reopened) == after_cut
    with pytest.raises(StaleWriterError):
        closer.publish(request=request, command_id="close:BC")
    result = next_author.publish(request=request, command_id="close:BC")
    assert _counts(reopened) == (after_cut[0] + 1, after_cut[1] + 2)
    assert validate_closed_revision(reopened, result.revision.revision_ref, next_registration).adaptation == result.adaptation


def test_one_open_region_closes_for_two_exact_connected_targets_in_distinct_assemblies(fixture):
    from cpn.rpnh.collaboration import (AssemblyAuthorV4, AssemblyMemberV4, AssemblyConnection, AssemblyCompletion,
                                       validate_assembly_revision)
    core, gateway, closed, _, registration, source, ids = fixture
    opened = _publish(fixture)
    upstream_id = "member:" + uuid.uuid4().hex
    upstream = AssemblyMemberV4(upstream_id, "Explicit upstream", source.revision.revision_ref, "plain_closed_v1", None, None)
    author = AssemblyAuthorV4(gateway, registration, closed.producer)
    results = []
    for i in (1, 2):
        close_command, assembly_command = f"close:target-{i}", f"assembly:target-{i}"
        closer, public_request = _closure_request(fixture, opened, command_id=close_command, assembly_command_id=assembly_command)
        intent = deepcopy(public_request["intent"])
        expectation = intent["ingress_expectations"][0]
        expectation.update(kind="assembly_connection", other_member_id=upstream_id,
            other_revision_ref=source.revision.revision_ref.to_dict(), other_exit_element_id=ids["/exit/result"])
        request = closer.prepare_request(open_revision_ref=opened.revision.revision_ref, provenance_ref=opened.provenance_ref,
            context=public_request["context"], completion=public_request["completion"], extract_plan=public_request["extract_plan"],
            intent=intent, command_id=close_command)
        adapted = closer.publish(request=request, command_id=close_command)
        identity = intent["member_id"]
        member = AssemblyMemberV4(identity, "Adapted contribution", adapted.revision.revision_ref,
            "adapted_result_current", adapted.adaptation_ref, adapted.intent_ref)
        entries = [row["element_id"] for row in adapted.element_map["elements"] if row["kind"] == "entry"]
        primary = next(row["element_id"] for row in adapted.element_map["elements"] if row["locator"] == "/terminal")
        connection = AssemblyConnection(upstream_id, ids["/exit/result"], identity, entries[0])
        options = dict(name="ConnectedTarget", members=[upstream, member], connections=[connection],
            completion=AssemblyCompletion(identity, primary), budget_policy="shared_exact",
            deployment_intent="same_run_candidate", command_id=assembly_command)
        before = _counts(core)
        with pytest.raises(RegistryConflict, match="target_mismatch"):
            author.publish(**{**options, "connections": []})
        assert _counts(core) == before
        if i == 1:
            competing_id = "member:" + uuid.uuid4().hex
            competing = AssemblyMemberV4(competing_id, "Competing consumer", source.revision.revision_ref,
                                        "plain_closed_v1", None, None)
            reused = AssemblyConnection(upstream_id, ids["/exit/result"], competing_id, ids["/entry/request"])
            with pytest.raises(ValueError, match="multiple consumers"):
                author.publish(**{**options, "members": [upstream, member, competing], "connections": [connection, reused]})
            assert _counts(core) == before
        assembly = author.publish(**options)
        assert assembly.lowering_map["target_matches"][0]["ingress"][0]["connection"] == connection.to_dict()
        assert validate_assembly_revision(core, assembly.revision.revision_ref, registration).lowering_map == assembly.lowering_map
        results.append((adapted, assembly))
    assert results[0][0].module.to_dict() == results[1][0].module.to_dict()
    assert results[0][0].revision.revision_ref.ref.entity_id != results[1][0].revision.revision_ref.ref.entity_id
    assert results[0][0].intent_ref != results[1][0].intent_ref
    assert results[0][1].revision.revision_ref.ref.entity_id != results[1][1].revision.revision_ref.ref.entity_id


def test_canonical_command_self_proof_cycle_rejected_before_recursion(fixture):
    from cpn.rpnh.collaboration import NetRevision
    from cpn.rpnh.collaboration.open_region import _key, _result_ref
    from cpn.rpnh.collaboration.open_region_closure import CLOSURE_MARKER, CLOSURE_COMMAND_SCHEMA
    from cpn.rpnh.collaboration.assembly_v2 import _material_ref
    from cpn.rpnh.collaboration.materials import MODULE_SCHEMA
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.schema_catalog import canonical_text
    core, gateway, _, _, registration, _, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    valid = closer.publish(request=request, command_id="close:BC")
    command = json.loads(core.object_store.path_for_version(valid.command_ref.ref.resource_version_id).read_bytes())
    command_id = "close:cycle"
    reference = _result_ref(core, closer.binding, command_id, closure=True)
    key = _key(command_id, closure=True)
    command_ref = _material_ref(core, closer.binding, key + ":command")
    definition_ref = _material_ref(core, closer.binding, key + ":definition")
    command["command_id"] = command_id
    command["result_revision_ref"] = reference.to_dict()
    command["request"]["open_revision_ref"] = reference.to_dict()
    # Publish real canonical low-level records, not monkeypatched consumers.
    # The active typed proof path must reject D -> itself before trying to
    # reconstruct this unprovable command's later prepared-material claims.
    for role, schema, document, marker, summary in (
            ("command", CLOSURE_COMMAND_SCHEMA, command, CLOSURE_COMMAND_SCHEMA, "Open region complete command"),
            ("definition", MODULE_SCHEMA, valid.module.to_dict(), canonical_text(command_ref.to_dict()), "Open region definition")):
        _publish_private_system(core, gateway._task_ref, PublishResource(origin=PrivateSystemOrigin(gateway._bootstrap_ref),
            payload=canonical_json(document), media_type="application/json", content_schema_ref=schema,
            content_schema_authority_ref=closer.schemas[schema], summary=summary, lifetime_ref=gateway._bootstrap_ref,
            descriptors={CLOSURE_MARKER: marker}, idempotency_key=key + ":" + role))
    record = NetRevision(reference, valid.revision.owner_task_ref, valid.revision.producer_principal_ref, command_id,
        "closed_module", definition_ref, (), (), valid.revision.element_mapping_ref,
        valid.revision.boundary_mapping_ref, valid.revision.host_requirements_ref, None)
    core.publish_bytes(object_type="collaboration_net_revision/v1", logical_id=reference.ref.entity_id,
        version_id=reference.ref.version_id, payload=canonical_json(record.to_dict()), metadata=record.to_dict(),
        media_type="application/json", schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key=key)
    before = _counts(core)
    with pytest.raises(RegistryConflict, match="proof_cycle"):
        validate_closed_revision(core, reference, registration)
    assert _counts(core) == before


def test_v4_final_dependency_cut_reopens_owner_and_keeps_exact_command(fixture, monkeypatch):
    from cpn.rpnh.collaboration import (AssemblyAuthorV4, AssemblyMemberV4, AssemblyCompletion, validate_assembly_revision)
    from cpn.rpnh.collaboration.assembly_v4 import LOWERING_V4_SCHEMA, ASSEMBLY_V4_TYPE
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.event_store import StaleWriterError
    core, gateway, closed, _, registration, _, _ = fixture
    opened = _publish(fixture)
    closer, request = _closure_request(fixture, opened)
    adapted = closer.publish(request=request, command_id="close:BC")
    author = AssemblyAuthorV4(gateway, registration, closed.producer)
    identity = adapted.intent["member_id"]
    member = AssemblyMemberV4(identity, "Adapted", adapted.revision.revision_ref, "adapted_result_current",
                              adapted.adaptation_ref, adapted.intent_ref)
    terminal = next(row["element_id"] for row in adapted.element_map["elements"] if row["locator"] == "/terminal")
    options = dict(name="FinalCut", members=[member], connections=[], completion=AssemblyCompletion(identity, terminal),
        budget_policy="shared_exact", deployment_intent="same_run_candidate", command_id="assembly:BC")
    path = core.object_store.path_for_version(adapted.revision.definition_ref.ref.resource_version_id)
    raw = path.read_bytes()
    changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True, separators=(",", ":")).encode()
    assert len(raw) == len(changed) and raw != changed
    publish = author._publish_document
    def damage_after_lowering(key, schema, document, **kwargs):
        result = publish(key, schema, document, **kwargs)
        if schema == LOWERING_V4_SCHEMA:
            path.write_bytes(changed)
        return result
    monkeypatch.setattr(author, "_publish_document", damage_after_lowering)
    try:
        with pytest.raises((RegistryConflict, ObjectIntegrityError)):
            author.publish(**options)
    finally:
        path.write_bytes(raw)
        monkeypatch.setattr(author, "_publish_document", publish)
    assert core.event_store.object_rows_by_type(ASSEMBLY_V4_TYPE) == ()
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == 4  # S, O, D, generated G.
    after_cut = _counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_registration = _registration()
    next_author = AssemblyAuthorV4(next_gateway, next_registration, closed.producer)
    assert _counts(reopened) == after_cut and reopened.writer_epoch != core.writer_epoch
    with pytest.raises(RegistryConflict, match="conflict"):
        next_author.publish(**{**options, "name": "ChangedCommand"})
    assert _counts(reopened) == after_cut
    with pytest.raises(StaleWriterError):
        author.publish(**options)
    result = next_author.publish(**options)
    assert _counts(reopened) == (after_cut[0] + 1, after_cut[1] + 2)
    assert validate_assembly_revision(reopened, result.revision.revision_ref, next_registration).lowering_map == result.lowering_map
