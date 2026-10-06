"""Real typed business-resource selection for the inert candidate plan."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.components.basic import lower_operation
from cpn.rpnh.petri_contracts import LeaseIdentityDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import content_schema_ref_payload
from cpn.rpnh.collaboration.candidate_plans import read_candidate_plan
from test_native_net_operations import TEXT
from test_candidate_plan_persistence import assert_plan_only, authored, candidate_request
import test_candidate_plan_persistence as persistence


@pytest.fixture
def resource_fixture(tmp_path, monkeypatch, request):
    mode = getattr(request, "param", "basic")
    if mode in {"lease", "pool", "separate_pools"}:
        original = persistence._registration
        def registration_with_lease():
            base, registration = original(), Registration()
            for declaration in base.declarations():
                kind, key = declaration["kind"], declaration["key"]
                if kind == "schema":
                    registration.register_schema(key, declaration["schema"])
                elif kind == "component" and key == "operation":
                    def lower_with_resource(config, context):
                        fragment = lower_operation(config, context)
                        if mode == "lease":
                            return replace(fragment, lease_identities=(LeaseIdentityDeclaration("reference"),))
                        from cpn.rpnh.petri_contracts import PlaceDeclaration, ResourceLeasePoolBinding
                        groups = (("pool", ("a", "b")),) if mode == "pool" else (("pool_a", ("a",)), ("pool_b", ("b",)))
                        return replace(fragment,
                            places=(*fragment.places, *(PlaceDeclaration(name, TEXT, capacity=len(names), reusable=True,
                                token_kind="resource_lease") for name, names in groups)),
                            lease_identities=(LeaseIdentityDeclaration("a"), LeaseIdentityDeclaration("b")),
                            lease_pools=tuple(ResourceLeasePoolBinding(name, name, names) for name, names in groups))
                    registration.register_component(key, lower_with_resource,
                        identity={"implementation_id": "test.plan_resource_operation", "revision": "v1"},
                        contracts=declaration["contracts"])
                else:
                    getattr(registration, "register_" + kind)(key, base.resolve(kind, key),
                        identity=declaration["identity"], contracts=declaration["contracts"])
            return registration
        monkeypatch.setattr(persistence, "_registration", registration_with_lease)
    return persistence.plan_fixture.__wrapped__(tmp_path)


def resource(fixture, text="business-input", schema=TEXT, *, key=None):
    core, identity, gateway = fixture[:3]
    return _publish_private_system(core, identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=canonical_json(text), media_type="application/json",
        content_schema_ref=schema, content_schema_authority_ref=gateway.schema_refs[schema], summary="Offline selected input",
        lifetime_ref=gateway._bootstrap_ref, idempotency_key=key or "fixture:business:" + text))


def selection(axis, ref):
    if axis == "entry":
        return {"entry_inputs": {"request": ref}}
    if axis == "lease":
        return {"owner_resource_inputs": {"step.reference": ref}}
    return {"owner_input_resources": (ref,)}


@pytest.mark.parametrize("resource_fixture,axis", [("basic", "entry"), ("basic", "owner"), ("lease", "lease")], indirect=["resource_fixture"])
def test_real_resource_axis_is_frozen_replayed_and_evidenced(resource_fixture, axis):
    f = resource_fixture
    revision = authored(f)
    ref = resource(f)
    request = candidate_request(f, revision, **selection(axis, ref))
    first = f[-1].publish(**request)
    assert first == f[-1].publish(**request) == read_candidate_plan(f[0], first.plan_ref)
    field = {"entry": "entry_inputs", "owner": "owner_input_resources", "lease": "owner_resource_inputs"}[axis]
    expected = {"entry": {"request": [content_schema_ref_payload(ref)]}, "owner": [content_schema_ref_payload(ref)],
        "lease": {"step.reference": content_schema_ref_payload(ref)}}[axis]
    assert first.plan[field] == expected
    assert any(item["ref"]["version_id"] == str(ref.resource_version_id) for item in first.plan["dependency_evidence"])
    assert_plan_only(f[0])


@pytest.mark.parametrize("resource_fixture,axis", [("basic", "entry"), ("basic", "owner"), ("lease", "lease")], indirect=["resource_fixture"])
def test_each_resource_axis_is_in_the_complete_command_lock(resource_fixture, axis):
    from cpn.rpnh.registry.event_store import RegistryConflict
    f = resource_fixture
    revision = authored(f)
    first, other = resource(f, "first"), resource(f, "other")
    result = f[-1].publish(**candidate_request(f, revision, **selection(axis, first)))
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, revision, **selection(axis, other)))
    assert read_candidate_plan(f[0], result.plan_ref) == result
    assert_plan_only(f[0])


def test_entry_bundle_is_ordered_and_deeply_detached(resource_fixture):
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.event_store import RegistryConflict
    from test_native_net_operations import _simple_module
    f = resource_fixture
    document = _simple_module().to_dict()
    document["components"][0]["ports"][0]["cardinality"] = 2
    revision = authored(f, ModuleDeclaration.from_dict(document))
    a, b = resource(f, "first"), resource(f, "second")
    entries = {"request": [a, b]}
    result = f[-1].publish(**candidate_request(f, revision, entry_inputs=entries))
    entries["request"].reverse()
    assert result.plan["entry_inputs"]["request"] == [content_schema_ref_payload(a), content_schema_ref_payload(b)]
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, revision, entry_inputs=entries))


def test_omitted_entry_and_explicit_zero_bundle_keep_original_distinction(resource_fixture):
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.event_store import RegistryConflict
    from test_native_net_operations import _simple_module
    f = resource_fixture
    document = _simple_module().to_dict()
    document["components"][0]["ports"][0].update(cardinality_minimum=0, cardinality_maximum=1)
    revision = authored(f, ModuleDeclaration.from_dict(document))
    omitted = f[-1].publish(**candidate_request(f, revision))
    explicit = f[-1].publish(**candidate_request(f, revision, entry_inputs={"request": []}, command_id="candidate:explicit-zero"))
    assert omitted.plan["entry_inputs"] == {}
    assert explicit.plan["entry_inputs"] == {"request": []}
    with pytest.raises(RegistryConflict):
        f[-1].publish(**candidate_request(f, revision, entry_inputs={"request": []}))


def mutate_resource_metadata(core, ref, change):
    row = core.event_store.object_row(ref.resource_version_id)
    body = json.loads(row["metadata_json"])
    change(body)
    with core.event_store.connect() as db:
        event = json.loads(db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0])
        event["metadata"] = body
        db.execute("UPDATE objects SET metadata_json=? WHERE version_id=?", (json.dumps(body), str(ref.resource_version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(event), row["published_event_id"]))


@pytest.mark.parametrize("damage", ["unknown_entry", "empty_required", "too_many", "wrong_schema", "wrong_task", "wrong_self", "missing_payload", "invalid_content", "not_private_owner"])
def test_business_role_or_typed_dependency_failure_prevents_plan(resource_fixture, damage):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE
    from jsonschema import ValidationError
    from test_native_net_operations import ALT_TEXT
    f = resource_fixture
    revision = authored(f)
    ref = resource(f, "selected", schema=ALT_TEXT if damage == "wrong_schema" else TEXT)
    request = selection("entry", ref)
    if damage == "unknown_entry":
        request = {"entry_inputs": {"unknown": ref}}
    elif damage == "empty_required":
        request = {"entry_inputs": {"request": []}}
    elif damage == "too_many":
        request = {"entry_inputs": {"request": [ref, ref]}}
    elif damage == "wrong_task":
        mutate_resource_metadata(f[0], ref, lambda body: body["task_ref"].__setitem__("logical_id", str(new_id("task"))))
    elif damage == "wrong_self":
        mutate_resource_metadata(f[0], ref, lambda body: body.__setitem__("resource_id", str(new_id("resource"))))
    elif damage == "missing_payload":
        f[0].object_store.path_for_version(ref.resource_version_id).unlink()
    elif damage == "invalid_content":
        path = f[0].object_store.path_for_version(ref.resource_version_id)
        path.write_bytes(b'1234567890')
    elif damage == "not_private_owner":
        request = selection("owner", ref)
        mutate_resource_metadata(f[0], ref, lambda body: body.__setitem__("origin_kind", "petri_output"))
    with pytest.raises((RegistryConflict, ValidationError)):
        f[-1].publish(**candidate_request(f, revision, **request))
    assert f[0].event_store.object_rows_by_type(PLAN_TYPE) == ()


@pytest.mark.parametrize("resource_fixture,keys", [("lease", {}), ("lease", {"unknown": None})], indirect=["resource_fixture"])
def test_owner_resource_keys_cover_exact_lease_inventory(resource_fixture, keys):
    from cpn.rpnh.registry.event_store import RegistryConflict
    f = resource_fixture
    revision = authored(f)
    ref = resource(f)
    with pytest.raises(RegistryConflict, match="lease symbols"):
        f[-1].publish(**candidate_request(f, revision, owner_resource_inputs={key: ref for key in keys}))


@pytest.mark.parametrize("axis", ["entry", "owner", "lease"])
def test_resource_axes_reject_fake_mutable_ids_without_coercion(resource_fixture, axis):
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.identities import new_id
    f = resource_fixture
    revision = authored(f)
    class Mutable:
        kind = "resource"
        def __str__(self):
            raise AssertionError("fake IDs must not be coerced")
    fake = object.__new__(ResourceVersionRef)
    object.__setattr__(fake, "resource_id", Mutable())
    object.__setattr__(fake, "resource_version_id", new_id("resource_version"))
    with pytest.raises(TypeError, match="TypedId"):
        f[-1].publish(**candidate_request(f, revision, **selection(axis, fake)))


def publish_extra_catalog(fixture):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    core = fixture[0]
    ref = VersionRef("registry_type_catalog/v1", new_id("schema"), new_id("resource_version"))
    body = core.catalog.bundle()
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json",
        schema_ref="registry_v1/registry_type_catalog/v1", idempotency_key="fixture:extra-schema-catalog")
    return ref


def mark_member(core, version_id, state):
    from test_collaboration_authoring import _inject_temporary_member
    from cpn.rpnh.registry.identities import new_id
    root = _inject_temporary_member(core, "object", str(version_id))
    if state == "PUBLISHED":
        tx = core.begin(idempotency_key="fixture:resource-promotion:" + str(version_id))
        terminal = tx.commit()[-1]
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(tx.transaction_id), str(new_id("operation_result_version")), str(new_id("marking_checkpoint_version")), root))
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)", (root, "event", str(terminal.event_id), str(tx.transaction_id)))


@pytest.mark.parametrize("target", ["business_resource", "schema_resource", "extra_catalog"])
@pytest.mark.parametrize("state", ["PUBLISHED", "PROVISIONAL"])
def test_business_and_schema_authorities_keep_canonical_promotion_semantics(resource_fixture, target, state):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from test_native_net_operations import _simple_module
    f = resource_fixture
    schema = "registry_v1/task_round/v1" if target == "extra_catalog" else TEXT
    revision = authored(f, _simple_module(input_schema=schema))
    catalog = publish_extra_catalog(f) if target == "extra_catalog" else None
    if catalog is None:
        ref = resource(f)
    else:
        # The real bootstrap publisher requires catalog authority for a
        # protected mechanical schema, resource authority for application ABI.
        data = dict(f[0].get_version(f[4].version_id).metadata)
        ref = _publish_private_system(f[0], f[1].task_ref, PublishResource(origin=PrivateSystemOrigin(f[2]._bootstrap_ref),
            payload=canonical_json(data), media_type="application/json", content_schema_ref=schema,
            content_schema_authority_ref=catalog, summary="Exact catalog input", lifetime_ref=f[2]._bootstrap_ref,
            idempotency_key="fixture:catalog-backed-business"))
    target_id = ref.resource_version_id if target == "business_resource" else (
        f[2].schema_refs[TEXT].resource_version_id if target == "schema_resource" else catalog.version_id)
    mark_member(f[0], target_id, state)
    request = candidate_request(f, revision, entry_inputs={"request": ref})
    if state == "PROVISIONAL":
        with pytest.raises(RegistryConflict):
            f[-1].publish(**request)
    else:
        result = f[-1].publish(**request)
        assert read_candidate_plan(f[0], result.plan_ref) == result
        assert any(item["ref"]["version_id"] == str(target_id) for item in result.plan["dependency_evidence"])
        assert_plan_only(f[0])


def test_same_schema_bytes_under_another_resource_ref_remain_wrong_authority(resource_fixture):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    from cpn.rpnh.registry.module_nets import _verify_input_schema_authority
    f = resource_fixture
    revision = authored(f)
    schema = _publish_private_system(f[0], f[1].task_ref, PublishResource(
        origin=PrivateSystemOrigin(f[2]._bootstrap_ref), payload=canonical_json(f[6].declaration("schema", TEXT)["schema"]),
        media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
        summary="Different exact schema version, same bytes", lifetime_ref=f[2]._bootstrap_ref,
        idempotency_key="fixture:alias-schema"))
    assert schema != f[2].schema_refs[TEXT]
    ref = _publish_private_system(f[0], f[1].task_ref, PublishResource(origin=PrivateSystemOrigin(f[2]._bootstrap_ref),
        payload=canonical_json("input"), media_type="application/json", content_schema_ref=TEXT,
        content_schema_authority_ref=schema, summary="Different exact schema authority", lifetime_ref=f[2]._bootstrap_ref,
        idempotency_key="fixture:alias-backed-input"))
    with pytest.raises(ContentSchemaAuthorityError, match="exact document"):
        _verify_input_schema_authority(f[0], f[0].get_version(ref.resource_version_id).metadata, f[2].schema_refs[TEXT])
    with pytest.raises(RegistryConflict, match="schema authority"):
        f[-1].publish(**candidate_request(f, revision, entry_inputs={"request": ref}))


@pytest.mark.parametrize("resource_fixture,axis", [("basic", "entry"), ("basic", "owner"), ("lease", "lease")], indirect=["resource_fixture"])
def test_same_size_business_bytes_are_rechecked_on_every_exact_read(resource_fixture, axis):
    from cpn.rpnh.registry.event_store import RegistryConflict
    f = resource_fixture
    revision = authored(f)
    ref = resource(f, "first")
    request = candidate_request(f, revision, **selection(axis, ref))
    first = f[-1].publish(**request)
    path = f[0].object_store.path_for_version(ref.resource_version_id)
    original = path.read_bytes()
    changed = canonical_json("other")
    assert len(original) == len(changed)
    path.write_bytes(changed)
    with pytest.raises(RegistryConflict, match="evidence"):
        read_candidate_plan(f[0], first.plan_ref)
    with pytest.raises(RegistryConflict):
        f[-1].publish(**request)
    assert_plan_only(f[0])


@pytest.mark.parametrize("axis", ["entry", "owner"])
def test_business_inputs_do_not_inherit_author_bootstrap_scope_rules(resource_fixture, axis):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.strict_contracts import ref_payload
    from cpn.rpnh.registry.module_operations import publish_module_operations
    from cpn.rpnh.registry.module_nets import publish_module_net
    f = resource_fixture
    revision = authored(f)
    ref = resource(f)
    # Historical scope specimen: the Module resource role uses exact task and
    # schema, not the candidate's current branch/net/round or author provenance.
    mutate_resource_metadata(f[0], ref, lambda body: body.update(branch_id="historical",
        round_ref=ref_payload(VersionRef("task_round/v1", new_id("task_round"), new_id("task_round_version"))),
        net_ref=ref_payload(VersionRef("net_instance/v1", new_id("net_instance"), new_id("net_instance_version")))))
    first = f[-1].publish(**candidate_request(f, revision, **selection(axis, ref)))
    assert read_candidate_plan(f[0], first.plan_ref) == first
    assert_plan_only(f[0])
    # A real adjacent legacy publication proves these are its existing role
    # conditions. Its explicit graph is a comparison, not a plan side effect.
    schemas = {key: f[2].schema_refs[key] for key in revision.compiled.source.required_schemas}
    operations = publish_module_operations(f[0], revision.compiled, f[6], schemas, idempotency_key="fixture:legacy-operations")
    publication = publish_module_net(f[0], revision.compiled, f[6], identity=f[1], bootstrap_ref=f[2]._bootstrap_ref,
        principal_ref=f[3], task_round_ref=f[4], authority_decision_ref=f[5], schema_refs=schemas,
        operation_refs=operations, entry_inputs={"request": ref} if axis == "entry" else {},
        owner_input_resources=(ref,) if axis == "owner" else (), idempotency_key="fixture:legacy-role-comparison")
    assert publication.net_ref.entity_type == "net_instance/v1"


@pytest.mark.parametrize("damage", ["unknown_entry", "quantity", "owner_origin", "schema_ref", "owner_lease"])
def test_persisted_plan_resource_roles_are_checked_below_producer(resource_fixture, damage):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from test_candidate_plan_persistence import _replace_record
    f = resource_fixture
    revision = authored(f)
    ref = resource(f)
    first = f[-1].publish(**candidate_request(f, revision, entry_inputs={"request": ref}))
    if damage == "unknown_entry":
        change = lambda body: body["entry_inputs"].__setitem__("unknown", body["entry_inputs"].pop("request"))
    elif damage == "quantity":
        change = lambda body: body["entry_inputs"].__setitem__("request", [])
    elif damage == "owner_origin":
        change = lambda body: body["owner_input_resources"].append(content_schema_ref_payload(ref))
        mutate_resource_metadata(f[0], ref, lambda body: body.__setitem__("origin_kind", "petri_output"))
    elif damage == "schema_ref":
        change = lambda body: body["schema_refs"].__setitem__(TEXT, content_schema_ref_payload(ref))
    else:
        change = lambda body: body["owner_resource_inputs"].__setitem__("not_declared", content_schema_ref_payload(ref))
    _replace_record(f[0], first.plan_ref, change)
    with pytest.raises(RegistryConflict):
        read_candidate_plan(f[0], first.plan_ref)


@pytest.mark.parametrize("mode", ["local", "recursive", "nested_id_scope"])
def test_offline_resource_validator_preserves_document_reference_semantics(monkeypatch, mode):
    import socket
    import urllib.request
    from jsonschema import ValidationError
    from cpn.rpnh.registry._candidate_plan_reads import _validate_resource_instance
    from cpn.rpnh.registry.content_schemas import _validate_schema_bytes
    def forbidden(*args, **kwargs):
        raise AssertionError("offline schema validation must not retrieve external data")
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "$id": "application/local_reference_test/v1"}
    if mode == "local":
        schema.update(definitions={"value": {"type": "string"}}, **{"$ref": "#/definitions/value"})
        valid, invalid = "text", 1
    elif mode == "recursive":
        schema.update(definitions={"node": {"type": "object", "required": ["value"],
            "properties": {"value": {"type": "string"}, "next": {"$ref": "#/definitions/node"}}}},
            **{"$ref": "#/definitions/node"})
        valid, invalid = {"value": "a", "next": {"value": "b"}}, {"value": "a", "next": {"value": 1}}
    else:
        # Root and nested scopes deliberately disagree. No rebasing/stripping
        # of legal IDs may make the nested child resolve the root definition.
        schema.update(definitions={"value": {"type": "number"}}, type="object", required=["child"],
            properties={"child": {"$id": "urn:rpnh:local-child", "definitions": {"value": {"type": "string"}},
                "type": "object", "required": ["leaf"], "properties": {"leaf": {"$ref": "#/definitions/value"}}}})
        valid, invalid = {"child": {"leaf": "text"}}, {"child": {"leaf": 1}}
    _, checked = _validate_schema_bytes(canonical_json(schema))
    _validate_resource_instance(checked, valid)
    with pytest.raises(ValidationError):
        _validate_resource_instance(checked, invalid)


def test_resource_validator_cannot_implicitly_retrieve_even_before_local_ref_filter(monkeypatch):
    import socket
    import urllib.request
    from referencing.exceptions import Unresolvable
    from cpn.rpnh.registry._candidate_plan_reads import _validate_resource_instance
    from cpn.rpnh.registry.content_schemas import _validate_schema_bytes, ContentSchemaAuthorityError
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("untrusted reference must not attempt external I/O")
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "$id": "application/untrusted_reference/v1",
        "$ref": "https://invalid.example.invalid/never-retrieve"}
    with pytest.raises(ContentSchemaAuthorityError, match="local JSON pointers"):
        _validate_schema_bytes(canonical_json(schema))
    # Independent defense of the instance-validator boundary, with the schema
    # prefilter intentionally not invoked. An empty registry cannot retrieve.
    with pytest.raises(Unresolvable):
        _validate_resource_instance(schema, {})
    assert attempts == []


@pytest.mark.parametrize("resource_fixture,same", [("pool", True), ("pool", False), ("separate_pools", True)], indirect=["resource_fixture"])
def test_shared_pool_alias_rule_matches_actual_legacy_and_allows_cross_pool_reuse(resource_fixture, same):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.module_operations import publish_module_operations
    from cpn.rpnh.registry.module_nets import publish_module_net
    f = resource_fixture
    revision = authored(f)
    a = resource(f, "a")
    b = a if same else resource(f, "b")
    selections = {"step.a": a, "step.b": b}
    repeated_pool = len(revision.compiled.symbolic.lease_pools) == 1 and same
    if repeated_pool:
        with pytest.raises(RegistryConflict, match="repeated actual lease"):
            f[-1].publish(**candidate_request(f, revision, owner_resource_inputs=selections))
        assert not f[0].event_store.object_rows_by_type("collaboration_candidate_plan/v1")
    else:
        first = f[-1].publish(**candidate_request(f, revision, owner_resource_inputs=selections))
        assert read_candidate_plan(f[0], first.plan_ref) == first
    schemas = {key: f[2].schema_refs[key] for key in revision.compiled.source.required_schemas}
    operations = publish_module_operations(f[0], revision.compiled, f[6], schemas, idempotency_key="fixture:alias-legacy-specs")
    kwargs = dict(identity=f[1], bootstrap_ref=f[2]._bootstrap_ref, principal_ref=f[3], task_round_ref=f[4],
        authority_decision_ref=f[5], schema_refs=schemas, operation_refs=operations, entry_inputs={},
        owner_resource_inputs=selections, idempotency_key="fixture:alias-legacy-graph")
    if repeated_pool:
        with pytest.raises(ValueError, match="repeated actual lease"):
            publish_module_net(f[0], revision.compiled, f[6], **kwargs)
    else:
        assert publish_module_net(f[0], revision.compiled, f[6], **kwargs).net_ref.entity_type == "net_instance/v1"


@pytest.mark.parametrize("resource_fixture", ["pool"], indirect=True)
def test_persisted_alias_plan_is_rejected_without_trusting_producer(resource_fixture):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from test_candidate_plan_persistence import _replace_record
    f = resource_fixture
    revision = authored(f)
    a, b = resource(f, "a"), resource(f, "b")
    first = f[-1].publish(**candidate_request(f, revision, owner_resource_inputs={"step.a": a, "step.b": b}))
    _replace_record(f[0], first.plan_ref, lambda body: body["owner_resource_inputs"].__setitem__("step.b", content_schema_ref_payload(a)))
    with pytest.raises(RegistryConflict, match="repeated actual lease"):
        read_candidate_plan(f[0], first.plan_ref)
