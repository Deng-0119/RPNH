"""Fixed same-cut resource reads; no candidate slot selection or publication."""
import hashlib
import json

import pytest

from cpn.rpnh.registry._preserved_resource_reads import read_preserved_resource_plan
from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from test_preserved_basis_reads import basis_for
from test_preserved_slot_projection import preserved_slot_fixture


@pytest.mark.parametrize("anchor", ["creation", "replacement"])
def test_exact_slot_plan_matches_legacy_and_actual_reads(preserved_slot_fixture, monkeypatch, anchor):
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.module_runtime import hydrate_module_resource_plan
    owner, creation, first, last, arguments = preserved_slot_fixture
    core = owner._core
    basis = basis_for(first if anchor == "creation" else last)
    expected = creation.resource_plan if anchor == "creation" else hydrate_module_resource_plan(core, **arguments)
    before = len(core.event_store.list_events()), len(core.event_store.object_rows())
    actual = set()
    original = ObjectStore.read_registered
    def record(store, prepared):
        payload = original(store, prepared)
        actual.add((prepared.object_type, str(prepared.logical_id), str(prepared.version_id),
            hashlib.sha256(payload).hexdigest(), len(payload), prepared.media_type))
        return payload
    def forbidden(*args, **kwargs):
        raise AssertionError("same-cut resource reader cannot open another DB, write or resolve HOST")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with monkeypatch.context() as patch:
            patch.setattr(ObjectStore, "read_registered", record)
            patch.setattr(core.event_store, "connect", forbidden)
            patch.setattr(core, "get_version", forbidden)
            patch.setattr(core, "begin", forbidden)
            patch.setattr(Registration, "resolve", forbidden)
            result = read_preserved_resource_plan(core, basis, _db=db)
    assert result.basis == basis and result.resource_plan == expected
    assert actual == {(row["ref"]["entity_type"], row["ref"]["logical_id"], row["ref"]["version_id"],
        row["sha256"], row["size"], row["media_type"]) for row in result.dependency_evidence}
    assert (len(core.event_store.list_events()), len(core.event_store.object_rows())) == before
    if anchor == "replacement":
        assert result.basis.net_ref != creation.net_ref
        assert result.resource_plan.slot_refs == creation.resource_plan.slot_refs
        assert result.resource_plan.logical_slots[0].producer_node_ref != creation.resource_plan.logical_slots[0].producer_node_ref
    changed = result.dependency_evidence
    changed[0]["sha256"] = "0" * 64
    assert changed != result.dependency_evidence


def test_resource_plan_reopens_read_only_and_preserves_old_basis(preserved_slot_fixture):
    from cpn.rpnh.registry._registry import _RegistryCore
    owner, _, first, last, _ = preserved_slot_fixture
    core = owner._core
    reopened = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for event in (first, last):
        assert read_preserved_resource_plan(reopened, basis_for(event)) == read_preserved_resource_plan(core, basis_for(event))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_actual_slot_schema_document_is_bound_to_compiled_registration(preserved_slot_fixture):
    from cpn.rpnh.registry.module_runtime import hydrate_module_resource_plan
    from test_candidate_plan_offline import _replace_resource_payload
    owner, _, _, last, arguments = preserved_slot_fixture
    core = owner._core
    expected = hydrate_module_resource_plan(core, **arguments)
    output = core.get_version(_version_from_payload(arguments["net"]["output_binding_refs"][0]).version_id).metadata
    schema = _resource_from_payload(output["content_schema_ref"])
    document = json.loads(core.object_store.read_registered(core.get_version(schema.resource_version_id)))
    document["title"] = "Different exact schema document"
    _replace_resource_payload(core, schema, canonical_json(document))
    # The old hydration checks the exact schema ref but never reads this slot
    # schema document. The fixed consumer must additionally bind those bytes.
    assert hydrate_module_resource_plan(core, **arguments) == expected
    with pytest.raises(RegistryConflict, match="schema document differs"):
        read_preserved_resource_plan(core, basis_for(last))


def _creation_refs(core, creation):
    output_ref = next(iter(creation.output_refs.values()))
    output = core.get_version(output_ref.version_id).metadata
    return {"net": creation.net_ref, "root": creation.root_ref,
        "node": next(iter(creation.node_refs.values())),
        "operation": next(iter(creation.binding_refs.values())),
        "spec": next(iter(creation.operation_refs.values())), "output": output_ref,
        "slot": creation.resource_plan.slot_refs["step.result_slot"],
        "declaration": creation.declaration_resource_ref.as_version_ref(),
        "schema": _resource_from_payload(output["content_schema_ref"]).as_version_ref()}


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("target", ["net", "root", "node", "operation", "spec", "output", "slot", "declaration", "schema"])
def test_missing_exact_creation_bytes_fail_after_successful_read(preserved_slot_fixture, target):
    owner, creation, _, last, _ = preserved_slot_fixture
    core = owner._core
    read_preserved_resource_plan(core, basis_for(last))
    ref = _creation_refs(core, creation)[target]
    core.object_store.path_for_version(ref.version_id).unlink()
    with pytest.raises((RegistryConflict, RegistryCorruptError)):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("target,field", [("net", "net_instance_ref"), ("root", "team_design_root_ref"),
    ("node", "node_ref"), ("operation", "operation_binding_ref"), ("operation", "operation_binding_id"),
    ("spec", "operation_spec_ref"), ("spec", "operation_spec_id"), ("spec", "operation_spec_version_id"),
    ("output", "output_binding_id"), ("output", "output_binding_version_id"), ("slot", "logical_artifact_slot_ref")])
def test_canonical_creation_descriptors_require_exact_self_identity(preserved_slot_fixture, target, field):
    from cpn.rpnh.registry.identities import new_id, TypedId
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, _ = preserved_slot_fixture
    core = owner._core
    read_preserved_resource_plan(core, basis_for(last))
    ref = _creation_refs(core, creation)[target]
    def change(body):
        if field.endswith("_ref"):
            body[field]["logical_id"] = str(new_id(ref.entity_id.kind))
        else:
            body[field] = str(new_id(TypedId.parse(body[field]).kind))
    _replace_record(core, ref, change)
    with pytest.raises((RegistryConflict, RegistryCorruptError)):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("axis", ["task", "run", "current_producer", "declaration", "round"])
def test_creation_scope_and_links_cannot_be_replaced_by_current_values(preserved_slot_fixture, axis):
    from cpn.rpnh.registry.identities import new_id, TypedId
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    refs = _creation_refs(core, creation)
    if axis in {"task", "run"}:
        def change(body):
            field = axis + "_ref"
            body[field]["logical_id"] = str(new_id(TypedId.parse(body[field]["logical_id"]).kind))
        _replace_record(core, creation.root_ref, change)
    elif axis == "current_producer":
        _replace_record(core, refs["slot"], lambda body: body.__setitem__("producer_node_ref", arguments["net"]["node_refs"][0]))
    elif axis == "declaration":
        _replace_record(core, creation.root_ref, lambda body: body.__setitem__("team_net_declaration_resource_ref", arguments["net"]["team_net_declaration_resource_ref"]))
    else:
        def change(body):
            body["task_round_ref"]["logical_id"] = str(new_id("task_round"))
        _replace_record(core, refs["output"], change)
    with pytest.raises((RegistryConflict, RegistryCorruptError)):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture,target", [(False, "slot"), (False, "schema"), (True, "owner")],
    indirect=["preserved_slot_fixture"])
@pytest.mark.parametrize("state", ["published", "provisional", "bad_terminal"])
def test_slot_resource_reader_preserves_canonical_published_dependencies(preserved_slot_fixture, target, state):
    from cpn.rpnh.registry.identities import new_id
    from test_collaboration_authoring import _inject_temporary_member
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    ref = (_resource_from_payload(next(iter(arguments["net"]["module_resource_bindings"]["owner_resource_inputs"].values()))).as_version_ref()
        if target == "owner" else _creation_refs(core, creation)[target])
    root = _inject_temporary_member(core, "object", str(ref.version_id))
    if state != "provisional":
        terminal = core.begin(idempotency_key="fixture:resource-reader:promotion").commit()[-1]
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(terminal.transaction_id), str(new_id("operation_result_version")),
                 str(new_id("marking_checkpoint_version")), root))
            if state == "bad_terminal":
                db.execute("UPDATE events SET payload_json='{}' WHERE event_id=?", (str(terminal.event_id),))
    if state == "published":
        result = read_preserved_resource_plan(core, basis_for(last))
        assert any(row["ref"] == ref_payload(ref) for row in result.dependency_evidence)
    else:
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            read_preserved_resource_plan(core, basis_for(last))


def test_resource_reader_rejects_foreign_and_autocommit_cuts(preserved_slot_fixture, tmp_path):
    from cpn.rpnh.registry._registry import _RegistryCore
    owner, _, _, last, _ = preserved_slot_fixture
    core, basis = owner._core, basis_for(last)
    with core.event_store.connect() as db:
        with pytest.raises(TypeError, match="existing SQLite"):
            read_preserved_resource_plan(core, basis, _db=db)
    other = _RegistryCore(tmp_path / "foreign-resource", create=True, catalog=core.catalog)
    with other.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryConflict, match="another Registry"):
            read_preserved_resource_plan(core, basis, _db=db)


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("target", ["creation", "current"])
def test_compiled_schema_remote_ref_is_rejected_without_retrieval(preserved_slot_fixture, target):
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    from test_candidate_plan_offline import _replace_resource_payload
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    ref = creation.declaration_resource_ref if target == "creation" else arguments["declaration_ref"]
    document = json.loads(core.object_store.read_registered(core.get_version(ref.resource_version_id)))
    schema = next(iter(document["registrations"]["schema"].values()))["schema"]
    schema["$ref"] = "https://invalid.example.invalid/no-retrieval"
    _replace_resource_payload(core, ref, canonical_json(document))
    # The real fixture blocks both urllib entry points and socket creation.
    with pytest.raises(ContentSchemaAuthorityError):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("target", ["creation", "current"])
def test_output_requires_its_exact_producer_spec_not_same_ports(preserved_slot_fixture, target):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.module_runtime import hydrate_module_resource_plan
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    expected = hydrate_module_resource_plan(core, **arguments)
    output_ref = next(iter(creation.output_refs.values())) if target == "creation" else _version_from_payload(arguments["net"]["output_binding_refs"][0])
    output = core.get_version(output_ref.version_id).metadata
    spec = core.get_version(_version_from_payload(output["opaque_action_ref"]).version_id).metadata
    alternate = VersionRef("operation_spec/v1", new_id("operation_spec"), new_id("operation_spec_version"))
    spec.update(operation_spec_id=str(alternate.entity_id), operation_spec_version_id=str(alternate.version_id),
        operation_spec_ref=ref_payload(alternate))
    core.publish_bytes(object_type=alternate.entity_type, logical_id=alternate.entity_id, version_id=alternate.version_id,
        payload=canonical_json(spec), metadata=spec, media_type="application/json", schema_ref="registry_v1/operation_spec/v1",
        idempotency_key="fixture:resource:alternate-spec")
    _replace_record(core, output_ref, lambda body: body.__setitem__("opaque_action_ref", ref_payload(alternate)))
    assert hydrate_module_resource_plan(core, **arguments) == expected
    assert read_preserved_basis(core, basis_for(last)).basis == basis_for(last)
    with pytest.raises(RegistryConflict, match="spec"):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_same_schema_bytes_under_another_ref_do_not_replace_creation_authority(preserved_slot_fixture):
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.strict_contracts import content_schema_ref_payload
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    original = _creation_refs(core, creation)["schema"]
    payload = core.object_store.read_registered(core.get_version(original.version_id))
    bootstrap = owner.schema_gateway._bootstrap_ref
    alias = _publish_private_system(core, _version_from_payload(arguments["root"]["task_ref"]), PublishResource(
        origin=PrivateSystemOrigin(bootstrap), payload=payload, media_type="application/schema+json",
        content_schema_ref="registry_v1/registry_type_catalog/v1", summary="Same schema bytes, another exact ref",
        lifetime_ref=bootstrap, idempotency_key="fixture:resource:schema-alias"))
    assert alias.as_version_ref() != original
    assert core.object_store.read_registered(core.get_version(alias.resource_version_id)) == payload
    output_ref = _version_from_payload(arguments["net"]["output_binding_refs"][0])
    output = core.get_version(output_ref.version_id).metadata
    spec_ref = _version_from_payload(output["opaque_action_ref"])
    assert spec_ref != _creation_refs(core, creation)["spec"]
    pair = content_schema_ref_payload(alias)
    _replace_record(core, output_ref, lambda body: body.__setitem__("content_schema_ref", pair))
    def change_spec(body):
        for port in body["output_ports"]:
            if port["port_id"] == output["output_port_id"]:
                port["content_schema_ref"] = pair
    _replace_record(core, spec_ref, change_spec)
    with pytest.raises(RegistryCorruptError, match="immutable creation"):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_schema_document_comparison_is_json_type_sensitive(preserved_slot_fixture):
    from test_candidate_plan_offline import _replace_resource_payload
    owner, creation, _, last, arguments = preserved_slot_fixture
    core = owner._core
    schema_ref = _creation_refs(core, creation)["schema"]
    schema = json.loads(core.object_store.read_registered(core.get_version(schema_ref.version_id)))
    schema["default"] = False
    schema_id = schema["$id"]
    for declaration in (creation.declaration_resource_ref, arguments["declaration_ref"]):
        compiled = json.loads(core.object_store.read_registered(core.get_version(declaration.resource_version_id)))
        compiled["registrations"]["schema"][schema_id]["schema"] = schema
        _replace_resource_payload(core, declaration, canonical_json(compiled))
    pair = _resource_from_payload({"resource_id": str(schema_ref.entity_id), "resource_version_id": str(schema_ref.version_id)})
    _replace_resource_payload(core, pair, canonical_json(schema))
    read_preserved_resource_plan(core, basis_for(last))
    schema["default"] = 0
    _replace_resource_payload(core, pair, canonical_json(schema))
    with pytest.raises(RegistryConflict, match="schema document differs"):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
def test_descriptor_bytes_cannot_differ_from_canonical_metadata(preserved_slot_fixture):
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, _ = preserved_slot_fixture
    core = owner._core
    read_preserved_resource_plan(core, basis_for(last))
    _replace_record(core, _creation_refs(core, creation)["node"],
        lambda body: body.__setitem__("node_synopsis", "Changed bytes only"), bytes_only=True)
    with pytest.raises((RegistryConflict, RegistryCorruptError)):
        read_preserved_resource_plan(core, basis_for(last))


@pytest.mark.parametrize("preserved_slot_fixture", [False], indirect=True)
@pytest.mark.parametrize("target", ["creation", "current"])
@pytest.mark.parametrize("axis", ["spec_node_role", "spec_root_membership", "spec_role_with_root_member",
    "root_declaration_membership", "root_schema_membership"])
def test_finite_dependencies_require_exact_role_and_root_membership(preserved_slot_fixture, target, axis):
    from cpn.rpnh.registry._preserved_basis_reads import read_preserved_basis
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from test_candidate_plan_persistence import _replace_record
    owner, creation, _, last, arguments = preserved_slot_fixture
    core, basis = owner._core, basis_for(last)
    read_preserved_resource_plan(core, basis)
    refs = _creation_refs(core, creation)
    root_ref = refs["root"] if target == "creation" else arguments["root_ref"]
    output_ref = refs["output"] if target == "creation" else _version_from_payload(arguments["net"]["output_binding_refs"][0])
    output = core.get_version(output_ref.version_id).metadata
    if axis.startswith("spec_"):
        node_ref = _version_from_payload(output["node_ref"])
        node = core.get_version(node_ref.version_id).metadata
        operation_ref = _version_from_payload(node["producer_operation_binding_ref"])
        spec = core.get_version(_version_from_payload(output["opaque_action_ref"]).version_id).metadata
        alias = VersionRef("operation_spec/v1", new_id("operation_spec"), new_id("operation_spec_version"))
        spec.update(operation_spec_id=str(alias.entity_id), operation_spec_version_id=str(alias.version_id),
            operation_spec_ref=ref_payload(alias))
        core.publish_bytes(object_type=alias.entity_type, logical_id=alias.entity_id, version_id=alias.version_id,
            payload=canonical_json(spec), metadata=spec, media_type="application/json", schema_ref="registry_v1/operation_spec/v1",
            idempotency_key="fixture:resource:root-spec-alias")
        _replace_record(core, operation_ref, lambda body: body.__setitem__("operation_spec_ref", ref_payload(alias)))
        _replace_record(core, output_ref, lambda body: body.__setitem__("opaque_action_ref", ref_payload(alias)))
        if axis == "spec_root_membership":
            _replace_record(core, node_ref, lambda body: body.__setitem__("opaque_role_artifact_ref", ref_payload(alias)))
        elif axis == "spec_role_with_root_member":
            _replace_record(core, root_ref, lambda body: body["artifact_refs"].append(ref_payload(alias)))
        message = "artifact_refs" if axis == "spec_root_membership" else "producer role"
    else:
        root = core.get_version(root_ref.version_id).metadata
        missing = (_resource_from_payload(root["team_net_declaration_resource_ref"]).as_version_ref()
            if axis == "root_declaration_membership" else _resource_from_payload(output["content_schema_ref"]).as_version_ref())
        assert ref_payload(missing) in root["resource_refs"]
        _replace_record(core, root_ref, lambda body: body.__setitem__("resource_refs",
            [ref for ref in body["resource_refs"] if ref != ref_payload(missing)]))
        message = "resource_refs"
    # These historical specimens still meet the old prefix's narrower contract.
    assert read_preserved_basis(core, basis).basis == basis
    with pytest.raises(RegistryConflict, match=message):
        read_preserved_resource_plan(core, basis)
