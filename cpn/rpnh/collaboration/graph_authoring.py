"""Durable ordinary-v3 graph authors and exact version-two revision records.

Publication creates author facts only. Branch v1 and Assembly retain their
existing acceptance contracts; no runtime, executor or plugin is invoked here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

from ..compiler import compile_module
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .authoring import AuthorRevisionReadError, _exact_object_ref
from .graph_source import (
    GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA,
    _validate, graph_element_map, make_graph_source_map, rebuild_graph_module,
)
from .materials import (
    MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, ClosedModuleAuthor,
    ValidatedClosedRevision, _binding, _boundaries, _material, _same_json,
    _validate_materials_at, validate_closed_revision,
)
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef

GRAPH_REVISION_TYPE = "collaboration_net_revision/v2"
GRAPH_REVISION_SCHEMA = "registry_v1/collaboration_net_revision/v2"
GRAPH_COMMAND = "rpnh/collaboration/graph_author_command/v1"
GRAPH_DOCUMENT_SCHEMAS = (GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA,
                          MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)


@dataclass(frozen=True, slots=True)
class GraphNetRevision:
    """Explicit source-authoritative version; never a v1 descriptor downcast."""
    revision_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    producer_principal_ref: SourceQualifiedVersionRef
    command_id: str
    parent_revision_refs: tuple[SourceQualifiedVersionRef, ...]
    graph_source_ref: SourceQualifiedResourceRef
    graph_recipe_ref: SourceQualifiedResourceRef
    graph_source_mapping_ref: SourceQualifiedResourceRef
    definition_ref: SourceQualifiedResourceRef
    element_mapping_ref: SourceQualifiedResourceRef
    boundary_mapping_ref: SourceQualifiedResourceRef
    host_requirements_ref: SourceQualifiedResourceRef

    definition_kind = "closed_module"
    selected_change_refs = ()
    open_region_contract_ref = None

    def __post_init__(self):
        _exact_object_ref(self.revision_ref, entity_type=GRAPH_REVISION_TYPE,
                          logical_kind="resource", version_kind="resource_version")
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1", logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
        if (not isinstance(self.command_id, str) or not self.command_id or self.command_id != self.command_id.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.command_id)):
            raise ValueError("graph author command_id must be canonical")
        if not isinstance(self.parent_revision_refs, tuple) or len(self.parent_revision_refs) > 1:
            raise ValueError("graph author v2 requires root or one exact graph-v2 parent")
        for ref in self.parent_revision_refs:
            _exact_object_ref(ref, entity_type=GRAPH_REVISION_TYPE, logical_kind="resource", version_kind="resource_version")
            if (ref == self.revision_ref or ref.ref.entity_id != self.revision_ref.ref.entity_id):
                raise ValueError("graph parent must precede this revision in its exact lineage")
        materials = tuple(getattr(self, field) for field in GRAPH_REF_FIELDS)
        if any(not isinstance(ref, SourceQualifiedResourceRef) for ref in materials):
            raise TypeError("graph author material requires exact source-qualified resource refs")
        if any(ref.source_id != self.revision_ref.source_id for ref in (
                self.owner_task_ref, self.producer_principal_ref, *self.parent_revision_refs, *materials)):
            raise ValueError("graph author revision and all inputs must share the bound local source")
        if len(set(materials)) != len(materials):
            raise ValueError("graph author material references must be distinct")

    def to_dict(self):
        return {"schema_version": GRAPH_REVISION_SCHEMA, "definition_kind": "closed_module",
            "source_contract": GRAPH_SOURCE_SCHEMA, "revision_ref": self.revision_ref.to_dict(),
            "owner_task_ref": self.owner_task_ref.to_dict(), "producer_principal_ref": self.producer_principal_ref.to_dict(),
            "command_id": self.command_id, "parent_revision_refs": [ref.to_dict() for ref in self.parent_revision_refs],
            **{field: getattr(self, field).to_dict() for field in GRAPH_REF_FIELDS}}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(GRAPH_REVISION_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        resource = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]),
            obj(document["producer_principal_ref"]), document["command_id"],
            tuple(obj(ref) for ref in document["parent_revision_refs"]),
            **{field: resource(document[field]) for field in GRAPH_REF_FIELDS})


GRAPH_REF_FIELDS = ("graph_source_ref", "graph_recipe_ref", "graph_source_mapping_ref",
    "definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")



def _revision_reference(core, source_id, command_id, parent_ref=None):
    key = "collaboration-graph-author:" + canonical_text({"command_id": command_id})
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    return SourceQualifiedVersionRef(source_id, VersionRef(GRAPH_REVISION_TYPE,
        stable("resource") if parent_ref is None else parent_ref.ref.entity_id, stable("resource_version")))


def _ordinary_host_selection(module, registration):
    """Reject plugin declarations and externally resolved schemas before lower.

    Trusted callables remain an explicit HOST boundary. This checks their data
    contracts, without inspecting/importing implementations or resolving tools.
    """
    keys = {("schema", key) for key in module.required_schemas}
    for component in module.components:
        keys.add(("component", component.key))
        for operation in component.operations:
            keys.add(("executor", operation.executor))
            keys.update(("tool", key) for key in operation.tools)
    keys.add(("tool", module.terminal.key))
    def local_refs(value):
        if isinstance(value, dict):
            if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#")):
                raise ValueError("graph author requires self-contained offline selected schemas")
            for child in value.values():
                local_refs(child)
        elif isinstance(value, list):
            for child in value:
                local_refs(child)
    for kind, key in sorted(keys):
        declaration = registration.declaration(kind, key)
        if kind == "schema":
            local_refs(declaration["schema"])
        elif {"native_plugin", "managed_plugin"}.intersection(declaration["contracts"]):
            raise ValueError("graph author v2 excludes native and managed plugin declarations")

def _read_graph_revision_at(db, core, reference, *, local_source_id):
    _exact_object_ref(reference, entity_type=GRAPH_REVISION_TYPE, logical_kind="resource", version_kind="resource_version")
    if reference.source_id != local_source_id:
        raise AuthorRevisionReadError("graph revision source differs from the selected local source")
    core.catalog.require(GRAPH_REVISION_TYPE, category="object")
    record = GraphNetRevision.from_dict(exact_descriptor(db, core.object_store, core.task_id,
        reference.to_dict()["ref"]), catalog=core.catalog)
    if record.revision_ref != reference or record.owner_task_ref.ref.entity_id != core.task_id:
        raise AuthorRevisionReadError("graph revision self-reference or exact owner differs")
    return record


@dataclass(frozen=True)
class ValidatedGraphRevision(ValidatedClosedRevision):
    source: dict
    recipe: dict
    source_map: dict


def _command(*, source_id, owner, producer, command_id, parents, documents):
    return canonical_text({"schema_version": GRAPH_COMMAND, "source_id": source_id,
        "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "parent_revision_refs": [ref.to_dict() for ref in parents],
        "documents": list(documents)})


def _read_graph_proof(db, core, revision, binding, parent):
    expected_ref = _revision_reference(core, binding["source_id"], revision.command_id,
        revision.parent_revision_refs[0] if revision.parent_revision_refs else None)
    if revision.revision_ref != expected_ref:
        raise RegistryConflict("graph revision identity differs from its complete author command")
    if parent is not None and not isinstance(parent, ValidatedGraphRevision):
        raise RegistryConflict("graph source proof requires its complete exact graph-v2 parent")
    source, source_metadata = _material(db, core, revision.graph_source_ref, binding, GRAPH_SOURCE_SCHEMA)
    recipe, _ = _material(db, core, revision.graph_recipe_ref, binding, GRAPH_RECIPE_SCHEMA)
    source_map, _ = _material(db, core, revision.graph_source_mapping_ref, binding, GRAPH_MAP_SCHEMA)
    # Never use the target definition to fill source or recipe gaps.
    module = rebuild_graph_module(source, recipe)
    ids, copies = {}, {}
    for row in source_map["elements"]:
        ids[row["locator"]] = row["element_id"]
        if row["copied_from"] is not None:
            if parent is None or row["copied_from"]["revision_ref"] != parent.revision.revision_ref.to_dict():
                raise RegistryConflict("graph source copy must pin its exact selected parent")
            copies[row["element_id"]] = row["copied_from"]["element_id"]
    expected = make_graph_source_map(source, ids, parent, copies)
    if not _same_json(source_map, expected):
        raise RegistryConflict("graph source mapping differs from the exact author source")
    return source, recipe, source_map, module, source_metadata


def _finish_graph_validation(core, revision, parent, binding, proof, module, compiled, element_map, boundary_map, host):
    source, recipe, source_map, rebuilt, source_metadata = proof
    expected_map = graph_element_map(rebuilt, source, source_map, parent)
    if not _same_json(element_map, expected_map):
        raise RegistryConflict("graph derived element identities differ from source identities and roles")
    if not _same_json(recipe["declaration_refs"], host["declaration_refs"]):
        raise RegistryConflict("graph recipe exact HOST selections differ from compile-consumed requirements")
    documents = (source, recipe, source_map, module.to_dict(), element_map, boundary_map, host)
    command = _command(source_id=binding["source_id"], owner=revision.owner_task_ref,
        producer=revision.producer_principal_ref, command_id=revision.command_id,
        parents=revision.parent_revision_refs, documents=documents)
    if source_metadata["descriptors"].get("graph_author_command_v1") != command:
        raise RegistryConflict("graph source differs from its immutable complete author command")
    return ValidatedGraphRevision(revision, module, compiled, element_map, boundary_map, host, source, recipe, source_map)


class GraphModuleAuthor(ClosedModuleAuthor):
    """Real author-only publication through the existing owner/gateway/transactions."""
    def __init__(self, gateway, registration, producer_principal_ref):
        super().__init__(gateway, registration, producer_principal_ref)
        self.core.catalog.require(GRAPH_REVISION_TYPE, category="object")
        for schema in (GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def publish(self, *, source, recipe, source_ids, command_id, parent_ref=None, copy_sources=None):
        if (not isinstance(command_id, str) or not command_id or command_id != command_id.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in command_id)):
            raise ValueError("graph author command_id must be canonical")
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("graph author publisher uses a stale owner writer")
        if parent_ref is not None and parent_ref.ref.entity_type != GRAPH_REVISION_TYPE:
            raise ValueError("graph author v2 requires a graph-v2 parent; no legacy proof upgrade")
        parent = None if parent_ref is None else validate_closed_revision(self.core, parent_ref, self.registration)
        source = _validate(GRAPH_SOURCE_SCHEMA, source)
        recipe = _validate(GRAPH_RECIPE_SCHEMA, recipe)
        module = rebuild_graph_module(source, recipe)
        source_map = make_graph_source_map(source, dict(source_ids), parent, dict(copy_sources or {}))
        element_map = graph_element_map(module, source, source_map, parent)
        boundary_map = _boundaries(module, element_map)
        _ordinary_host_selection(module, self.registration)
        compiled = compile_module(module, self.registration)
        registrations = compiled.to_dict()["registrations"]
        declaration_refs = []
        for kind, entries in sorted(registrations.items()):
            for key, declaration in sorted(entries.items()):
                ref = self.gateway.declaration_refs.get((kind, key))
                if ref is None:
                    ref = (self.gateway.bind_builtin_schema(key) if kind == "schema" and key in PROTECTED_SCHEMA_REFS
                           else self.gateway(kind, key, declaration))
                declaration_refs.append({"kind": kind, "key": key,
                    "resource_ref": SourceQualifiedResourceRef(self.binding["source_id"], ref).to_dict()})
        if recipe["declaration_refs"] and not _same_json(recipe["declaration_refs"], declaration_refs):
            raise RegistryConflict("requested graph recipe exact HOST selections differ")
        recipe["declaration_refs"] = declaration_refs
        host = {"schema_version": HOST_SCHEMA, "registrations": registrations, "declaration_refs": declaration_refs}
        documents = (source, recipe, source_map, module.to_dict(), element_map, boundary_map, host)
        for schema, document in zip(GRAPH_DOCUMENT_SCHEMAS, documents, strict=True):
            self.core.catalog.validate_schema_ref(schema, document)
        key = "collaboration-graph-author:" + canonical_text({"command_id": command_id})
        reference = _revision_reference(self.core, self.binding["source_id"], command_id, parent_ref)
        existing = self.core.event_store.object_row(reference.ref.version_id)
        if existing is not None:
            if existing["object_type"] != GRAPH_REVISION_TYPE or existing["logical_id"] != str(reference.ref.entity_id):
                raise RegistryConflict("graph command conflicts with original revision identity")
            prior = validate_closed_revision(self.core, reference, self.registration)
            prior_documents = (prior.source, prior.recipe, prior.source_map, prior.module.to_dict(),
                               prior.element_map, prior.boundary_map, prior.host_requirements)
            if (prior.revision.command_id != command_id or prior.revision.producer_principal_ref != self.producer
                    or prior.revision.parent_revision_refs != (() if parent is None else (parent_ref,))
                    or not _same_json(prior_documents, documents)):
                raise RegistryConflict("graph command conflicts with original immutable material")
            return prior
        owner = SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref)
        command = _command(source_id=self.binding["source_id"], owner=owner, producer=self.producer,
            command_id=command_id, parents=() if parent is None else (parent_ref,), documents=documents)
        refs = []
        try:
            for index, (schema, document) in enumerate(zip(GRAPH_DOCUMENT_SCHEMAS, documents, strict=True)):
                ref = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(document),
                    media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=self.schemas[schema],
                    summary=f"Graph author material {index}", lifetime_ref=self.gateway._bootstrap_ref,
                    descriptors={"graph_author_command_v1": command} if index == 0 else {},
                    idempotency_key=f"{key}:material:{index}"))
                refs.append(SourceQualifiedResourceRef(self.binding["source_id"], ref))
            revision = GraphNetRevision(reference, owner, self.producer, command_id,
                () if parent is None else (parent_ref,), *refs)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                _validate_materials_at(db, self.core, revision, self.registration, _binding(db, self.core), parent)
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=GRAPH_REVISION_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
                payload=canonical_json(revision.to_dict()), metadata=revision.to_dict(), media_type="application/json", schema_ref=GRAPH_REVISION_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("graph command conflicts with immutable prepared material") from exc
        return validate_closed_revision(self.core, reference, self.registration)
