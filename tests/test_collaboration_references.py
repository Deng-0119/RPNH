"""Offline I00 reference contracts, including real socket-free persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from cpn.rpnh.collaboration import (
    SourceQualifiedResourceRef,
    SourceQualifiedVersionRef,
    collaboration_schema_data,
)
from cpn.rpnh.collaboration.references import (
    SOURCE_RESOURCE_REF_SCHEMA,
    SOURCE_VERSION_REF_SCHEMA,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.errors import ResourceSchemaViolation
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resource_service import (
    _ResourceServiceKernel,
    _publish_private_system,
)
from cpn.rpnh.registry.resources import (
    PrivateSystemOrigin,
    PublishResource,
    ResourceVersionRef,
)
from cpn.rpnh.registry.schema_catalog import (
    CURRENT_SCHEMA_REFS,
    SchemaCatalog,
    SchemaGovernanceError,
    canonical_json,
)
from cpn.rpnh.registry.strict_contracts import ref_payload


def _id(kind, digit):
    return TypedId(kind, digit * 32)


@pytest.fixture
def catalog():
    schemas, types, paths = collaboration_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


@pytest.fixture(params=["object", "resource"])
def qualified(request):
    if request.param == "object":
        return SourceQualifiedVersionRef(
            "source-a", VersionRef("net_instance/v1",
                                   _id("net_instance", "1"),
                                   _id("net_instance_version", "2")))
    return SourceQualifiedResourceRef(
        "source-a", ResourceVersionRef(_id("resource", "1"),
                                      _id("resource_version", "2")))


def test_source_qualification_roundtrips_and_distinguishes_domains(catalog, qualified):
    other = type(qualified)("source-b", qualified.ref)
    assert other != qualified
    assert len({qualified, other}) == 2
    assert type(qualified).from_dict(qualified.to_dict(), catalog=catalog) == qualified
    assert type(other).from_dict(other.to_dict(), catalog=catalog) == other
    assert qualified.ref == other.ref


def test_refs_are_immutable_and_documents_are_detached(qualified):
    with pytest.raises(FrozenInstanceError):
        qualified.source_id = "changed"
    document = qualified.to_dict()
    document["source_id"] = "changed"
    document["ref"].clear()
    assert qualified.to_dict()["source_id"] == "source-a"
    assert qualified.to_dict()["ref"]


@pytest.mark.parametrize("source", [None, True, 3, "", " source", "source ", "a\nb", "a\x00b", "a\x7fb"])
def test_source_identity_is_explicit_not_normalized(catalog, qualified, source):
    with pytest.raises(ValueError, match="source_id"):
        type(qualified)(source, qualified.ref)
    document = qualified.to_dict()
    document["source_id"] = source
    with pytest.raises(SchemaGovernanceError):
        type(qualified).from_dict(document, catalog=catalog)


@pytest.mark.parametrize("field", ["schema_version", "source_id", "ref"])
def test_missing_fields_are_not_inferred(catalog, qualified, field):
    document = qualified.to_dict()
    del document[field]
    with pytest.raises(SchemaGovernanceError):
        type(qualified).from_dict(document, catalog=catalog)


@pytest.mark.parametrize("nested", [False, True])
def test_locator_alias_and_unknown_fields_cannot_be_smuggled_in(catalog, qualified, nested):
    document = qualified.to_dict()
    (document["ref"] if nested else document)["locator"] = "file:///not-authority"
    with pytest.raises(SchemaGovernanceError):
        type(qualified).from_dict(document, catalog=catalog)


def test_unknown_version_is_rejected_even_when_separately_registered(catalog, qualified):
    document = qualified.to_dict()
    schema_id = document["schema_version"]
    new_id = schema_id.removesuffix("v1") + "v2"
    schema = deepcopy(catalog.registered_schemas()[schema_id])
    schema["$id"] = new_id
    schema["properties"]["schema_version"]["const"] = new_id
    catalog.register_schema(new_id, schema)
    document["schema_version"] = new_id
    catalog.validate_schema_ref(new_id, document)
    with pytest.raises(SchemaGovernanceError):
        type(qualified).from_dict(document, catalog=catalog)


def test_opt_in_does_not_change_default_catalog_or_local_refs(catalog, qualified):
    assert qualified.to_dict()["schema_version"] not in CURRENT_SCHEMA_REFS
    with pytest.raises(SchemaGovernanceError, match="not in the registered"):
        type(qualified).from_dict(qualified.to_dict(), catalog=SchemaCatalog())
    if isinstance(qualified, SourceQualifiedVersionRef):
        assert ref_payload(qualified.ref) == qualified.to_dict()["ref"]
        assert set(ref_payload(qualified.ref)) == {"entity_type", "logical_id", "version_id"}
    assert catalog.definitions() == SchemaCatalog().definitions()


def test_version_ref_does_not_invent_entity_to_id_kind_rules(catalog):
    # Existing object types may use resource IDs; type-specific authority is not
    # derivable from the entity_type spelling and belongs to the record reader.
    ref = VersionRef("main_thread/v1", _id("resource", "1"),
                     _id("resource_version", "2"))
    qualified = SourceQualifiedVersionRef("source-a", ref)
    assert SourceQualifiedVersionRef.from_dict(qualified.to_dict(), catalog=catalog) == qualified


@pytest.mark.parametrize("ref", [
    None,
    {"entity_type": "net_instance/v1"},
    VersionRef("", _id("net_instance", "1"), _id("net_instance_version", "2")),
    VersionRef("net_instance/v1", "not-a-typed-id", _id("net_instance_version", "2")),
    VersionRef("net_instance/v1", _id("net_instance", "1"), None),
])
def test_object_wrapper_rejects_inexact_python_refs(ref):
    with pytest.raises(TypeError, match="exact typed VersionRef"):
        SourceQualifiedVersionRef("source-a", ref)


def test_resource_wrapper_rejects_generic_object_ref():
    with pytest.raises(TypeError, match="exact ResourceVersionRef"):
        SourceQualifiedResourceRef("source-a", VersionRef(
            "resource_version/v1", _id("resource", "1"), _id("resource_version", "2")))
    with pytest.raises(TypeError, match="exact ResourceVersionRef"):
        SourceQualifiedResourceRef("source-a", ResourceVersionRef(
            SimpleNamespace(kind="resource"), SimpleNamespace(kind="resource_version")))


def test_missing_nested_version_is_rejected(catalog, qualified):
    document = qualified.to_dict()
    field = "version_id" if isinstance(qualified, SourceQualifiedVersionRef) else "resource_version_id"
    del document["ref"][field]
    with pytest.raises(SchemaGovernanceError):
        type(qualified).from_dict(document, catalog=catalog)


def test_typed_reader_rejects_unknown_id_kind_and_wrong_resource_kind(catalog):
    obj = SourceQualifiedVersionRef("source-a", VersionRef(
        "net_instance/v1", _id("net_instance", "1"), _id("net_instance_version", "2")))
    document = obj.to_dict()
    document["ref"]["version_id"] = "unknown_kind:" + "2" * 32
    with pytest.raises(ValueError, match="unknown typed-id kind"):
        SourceQualifiedVersionRef.from_dict(document, catalog=catalog)
    resource = SourceQualifiedResourceRef("source-a", ResourceVersionRef(
        _id("resource", "1"), _id("resource_version", "2"))).to_dict()
    resource["ref"]["resource_version_id"] = str(_id("task_version", "2"))
    with pytest.raises(SchemaGovernanceError):
        SourceQualifiedResourceRef.from_dict(resource, catalog=catalog)


def test_inventory_is_fresh_closed_and_keeps_explicit_provenance(catalog):
    documents, types, paths = collaboration_schema_data()
    assert types == ()
    assert set(documents) == {SOURCE_RESOURCE_REF_SCHEMA, SOURCE_VERSION_REF_SCHEMA}
    for schema_id, document in documents.items():
        assert json.loads(paths[schema_id].read_text()) == document
        catalog.register_schema(schema_id, document, source_path=paths[schema_id])
        bundle = catalog.bundle()["schemas"][schema_id]
        assert json.loads(bundle["source"]) == document
        assert bundle["path"].startswith("rpnh/collaboration/")
        changed = deepcopy(document)
        changed["title"] += " changed"
        with pytest.raises(SchemaGovernanceError, match="immutable"):
            catalog.register_schema(schema_id, changed)
        document["properties"].clear()
    fresh, _, _ = collaboration_schema_data()
    assert all(value["properties"] for value in fresh.values())


def test_host_registration_uses_the_existing_catalog_boundary(qualified):
    registration = Registration()
    catalog = SchemaCatalog()
    registration.bind_schema_catalog(catalog)
    documents, _, _ = collaboration_schema_data()
    for schema_id, document in documents.items():
        registration.register_schema(schema_id, document)
    assert type(qualified).from_dict(qualified.to_dict(), catalog=catalog) == qualified


@pytest.fixture
def publisher(tmp_path, catalog, qualified):
    schema_id = qualified.to_dict()["schema_version"]
    core = _RegistryCore(tmp_path / "run", create=True, catalog=catalog)
    identity = _bootstrap_identity(core, NativeBootstrapManifest((schema_id,)))
    bootstrap_ref = _version_from_payload(json.loads(
        core.event_store.get_meta("bootstrap_command_ref")))
    registration = Registration()
    registration.bind_schema_catalog(catalog)
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap_ref)
    registration.bind_gateway(gateway)
    documents, _, _ = collaboration_schema_data()
    registration.register_schema(schema_id, documents[schema_id])
    def publish(document):
        return _publish_private_system(core, identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(bootstrap_ref),
            payload=canonical_json(document), media_type="application/json",
            content_schema_ref=schema_id, summary="Source-qualified reference fixture",
            content_schema_authority_ref=gateway.schema_refs[schema_id],
            lifetime_ref=bootstrap_ref, idempotency_key="test:source-qualified-reference"))

    return core, publish


def test_reference_content_persists_and_reopens_without_implicit_support(tmp_path, catalog, qualified, publisher):
    core, publish = publisher
    schema_id = qualified.to_dict()["schema_version"]
    resource = publish(qualified.to_dict())
    restored = json.loads(_ResourceServiceKernel(core)._read_registered(resource))
    assert type(qualified).from_dict(restored, catalog=catalog) == qualified
    metadata = core.get_version(resource.resource_version_id).metadata
    assert metadata["content_schema_ref"] == schema_id
    assert metadata["content_schema_authority_ref"] is not None

    # Legacy raw reads remain available; a reader with no opt-in schema must
    # report unsupported semantics instead of decoding the new content as v1.
    old_reader = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    raw = json.loads(_ResourceServiceKernel(old_reader)._read_registered(resource))
    assert raw == qualified.to_dict()
    with pytest.raises(SchemaGovernanceError, match="not in the registered"):
        type(qualified).from_dict(raw, catalog=old_reader.catalog)
    reader = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=catalog)
    assert type(qualified).from_dict(
        json.loads(_ResourceServiceKernel(reader)._read_registered(resource)),
        catalog=reader.catalog) == qualified


def test_trailing_newlines_fail_schema_and_publication(catalog, qualified, publisher):
    core, publish = publisher
    document = qualified.to_dict()
    schema = catalog.registered_schemas()[document["schema_version"]]
    before = len(core.event_store.object_rows())
    paths = [("source_id",)] + [("ref", field) for field in document["ref"]]
    for path in paths:
        malformed = deepcopy(document)
        target = malformed if len(path) == 1 else malformed["ref"]
        target[path[-1]] += "\n"
        with pytest.raises(ValidationError):
            Draft7Validator(schema).validate(malformed)
        with pytest.raises(SchemaGovernanceError):
            catalog.validate_schema_ref(document["schema_version"], malformed)
        with pytest.raises(ResourceSchemaViolation):
            publish(malformed)
        assert len(core.event_store.object_rows()) == before
