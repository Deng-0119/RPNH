"""Explicit ordinary-graph Assembly contracts; source proof belongs to v2.

A generated NetRevision v1 retains its own closed-declaration contract. These
records never turn that independent v1 consumer into an Assembly proof reader.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import uuid

from ..registry.identities import TypedId, fresh_bootstrap_resource_id
from ..registry.models import VersionRef
from ..registry.publication import _stable_id, _version_from_payload
from ..registry.resources import ResourceVersionRef
from ..registry.schema_catalog import canonical_json, canonical_text
from .assemblies import _identity, _MEMBER
from .authoring import _exact_object_ref, _revision_ref
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .graph_source import GRAPH_BUILDER, GRAPH_SOURCE_SCHEMA

ASSEMBLY_V2_TYPE = "collaboration_assembly_revision/v2"
ASSEMBLY_V2_SCHEMA = "registry_v1/collaboration_assembly_revision/v2"
PLAN_V2_SCHEMA = "rpnh/collaboration/assembly_plan/v2"
LOWERING_V2_SCHEMA = "rpnh/collaboration/assembly_lowering_map/v2"
ASSEMBLY_COMMAND = "rpnh/collaboration/assembly_author_command/v2"
RESOLVER_CONTRACT = "rpnh/collaboration/ordinary_graph_assembly_resolver/v1"
CONSTRAINTS_CONTRACT = "rpnh/assembly_member_constraints/v1"
ORIGIN_CONTRACT = "rpnh/collaboration/ordinary_graph_source_origins/v1"

_RECIPE = {
    "contract": RESOLVER_CONTRACT,
    "member_contracts": ["plain_closed_v1", "ordinary_graph_v2"],
    "graph_builder": GRAPH_BUILDER,
    "graph_source": GRAPH_SOURCE_SCHEMA,
    "component_scoping": "stable_member_uuid_prefix/v1",
    "public_boundary_scoping": "stable_member_uuid_prefix/v1",
    "local_graph_symbols": "preserve/v1",
    "constraints": CONSTRAINTS_CONTRACT,
    "budget_policy": "shared_exact",
    "completion": "selected_primary_with_alternatives/v1",
    "lowering": "actual_final_context_fragments/v1",
    "carrier_cut": "remove_only_assembly_connections/v1",
    "source_origins": ORIGIN_CONTRACT,
    "material_lock": "canonical_json_sha256_bytes/v1",
}


def resolver_recipe():
    return deepcopy(_RECIPE)


def _command(command_id):
    from .assemblies import _command as validate_command
    validate_command(command_id)
    return "collaboration-assembly-v2:" + canonical_text({"command_id": command_id})


def _assembly_ref(reference):
    return _exact_object_ref(reference, entity_type=ASSEMBLY_V2_TYPE,
                            logical_kind="resource", version_kind="resource_version")


def _member_ref(reference):
    if (not isinstance(reference, SourceQualifiedVersionRef)
            or reference.ref.entity_type not in {"collaboration_net_revision/v1", "collaboration_net_revision/v2"}):
        raise ValueError("Assembly v2 member must be an exact closed-v1 or ordinary-graph-v2 revision")
    return _exact_object_ref(reference, entity_type=reference.ref.entity_type,
                            logical_kind="resource", version_kind="resource_version")


def _result_ref(core, source_id, command_id, parent_ref):
    key = _command(command_id)
    if parent_ref is not None:
        _assembly_ref(parent_ref)
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    return SourceQualifiedVersionRef(source_id, VersionRef(ASSEMBLY_V2_TYPE,
        stable("resource") if parent_ref is None else parent_ref.ref.entity_id, stable("resource_version")))


def _material_ref(core, binding, key):
    bootstrap = _version_from_payload(binding["bootstrap_command_ref"])
    return SourceQualifiedResourceRef(binding["source_id"], ResourceVersionRef(
        fresh_bootstrap_resource_id(core.task_id, bootstrap.version_id, key),
        _stable_id("resource_version", core.task_id, key, "fresh_bootstrap_reference",
                   bootstrap.version_id, bootstrap.version_id)))


def material_signature(role, schema, reference, document):
    payload = canonical_json(document)
    return {"role": role, "schema": schema, "resource_ref": reference.to_dict(),
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


@dataclass(frozen=True, slots=True)
class AssemblyMemberV2:
    member_id: str
    display_name: str
    revision_ref: SourceQualifiedVersionRef

    def __post_init__(self):
        _identity(self.member_id, _MEMBER, "member_id")
        if not isinstance(self.display_name, str):
            raise TypeError("Assembly member display_name must be a string")
        _member_ref(self.revision_ref)

    def to_dict(self):
        return {"member_id": self.member_id, "display_name": self.display_name,
                "revision_ref": self.revision_ref.to_dict()}


@dataclass(frozen=True, slots=True)
class AssemblyRevisionV2:
    revision_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    producer_principal_ref: SourceQualifiedVersionRef
    command_id: str
    parent_revision_ref: SourceQualifiedVersionRef | None
    plan_ref: SourceQualifiedResourceRef
    generated_revision_ref: SourceQualifiedVersionRef
    compiled_inventory_ref: SourceQualifiedResourceRef
    lowering_mapping_ref: SourceQualifiedResourceRef

    def __post_init__(self):
        _assembly_ref(self.revision_ref)
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1", logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
        _command(self.command_id)
        if self.parent_revision_ref is not None:
            _assembly_ref(self.parent_revision_ref)
            if self.parent_revision_ref == self.revision_ref:
                raise ValueError("Assembly cannot be its own parent")
        _revision_ref(self.generated_revision_ref)
        for value in (self.plan_ref, self.compiled_inventory_ref, self.lowering_mapping_ref):
            if not isinstance(value, SourceQualifiedResourceRef):
                raise TypeError("Assembly materials require exact resource refs")
        if len({self.plan_ref, self.compiled_inventory_ref, self.lowering_mapping_ref}) != 3:
            raise ValueError("Assembly materials must have distinct exact references")
        refs = (self.owner_task_ref, self.producer_principal_ref, self.plan_ref,
                self.generated_revision_ref, self.compiled_inventory_ref, self.lowering_mapping_ref)
        if self.parent_revision_ref is not None:
            refs += (self.parent_revision_ref,)
            if self.parent_revision_ref.ref.entity_id != self.revision_ref.ref.entity_id:
                raise ValueError("Assembly parent must remain in its v2 logical history")
        if any(value.source_id != self.revision_ref.source_id for value in refs):
            raise ValueError("Assembly supports only one exact local source")

    def to_dict(self):
        return {"schema_version": ASSEMBLY_V2_SCHEMA, "command_id": self.command_id,
                **{field: getattr(self, field).to_dict() for field in (
                    "revision_ref", "owner_task_ref", "producer_principal_ref", "plan_ref",
                    "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")},
                "parent_revision_ref": None if self.parent_revision_ref is None else self.parent_revision_ref.to_dict()}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(ASSEMBLY_V2_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        resource = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]),
            obj(document["producer_principal_ref"]), document["command_id"],
            None if document["parent_revision_ref"] is None else obj(document["parent_revision_ref"]),
            resource(document["plan_ref"]), obj(document["generated_revision_ref"]),
            resource(document["compiled_inventory_ref"]), resource(document["lowering_mapping_ref"]))



@dataclass(frozen=True)
class ValidatedAssemblyRevisionV2:
    """Produced only by the full Assembly v2 consumer, never the v1 reader."""
    revision: AssemblyRevisionV2
    plan: dict
    generated: object
    compiled: object
    lowering_map: dict


# Version-two material authority stays local to this opt-in consumer. In
# particular, these checks do not strengthen the global source/native reader.
def _binding_at(db, core):
    from .materials import _binding, _same_json
    from ..registry._event_store.collaboration_descriptors import exact_descriptor
    from ..registry.event_store import RegistryConflict
    binding = _binding(db, core)  # Preserve original non-provisional/role checks.
    actual = exact_descriptor(db, core.object_store, core.task_id, binding["binding_ref"])
    if not _same_json(actual, binding):
        raise RegistryConflict("Assembly v2 binding bytes differ from selected authority")
    for field in ("task_ref", "native_run_ref", "bootstrap_command_ref"):
        exact_descriptor(db, core.object_store, core.task_id, binding[field])
    return binding


def _read_at(db, core, reference, binding):
    from ..registry._event_store.collaboration_descriptors import exact_descriptor
    from ..registry.event_store import RegistryConflict
    _assembly_ref(reference)
    if reference.source_id != binding["source_id"]:
        raise RegistryConflict("Assembly v2 source differs from exact local binding")
    record = AssemblyRevisionV2.from_dict(exact_descriptor(db, core.object_store, core.task_id,
        reference.to_dict()["ref"]), catalog=core.catalog)
    if record.revision_ref != reference or record.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
        raise RegistryConflict("Assembly v2 self-reference or exact owner differs")
    for qualified, kind in ((record.owner_task_ref, "task"), (record.producer_principal_ref, "principal")):
        ref = qualified.to_dict()["ref"]
        body = exact_descriptor(db, core.object_store, core.task_id, ref)
        if body[f"{kind}_id"] != ref["logical_id"] or body[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("Assembly v2 authority self-identity differs")
    return record


def _resolution(member):
    from .graph_authoring import ValidatedGraphRevision
    fields = ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")
    graph = isinstance(member, ValidatedGraphRevision)
    if graph:
        fields = ("graph_source_ref", "graph_recipe_ref", "graph_source_mapping_ref", *fields)
    return {"kind": "ordinary_graph_v2" if graph else "plain_closed_v1",
            **{field: getattr(member.revision, field).to_dict() for field in fields}}


def _members_at(db, core, plan, registration, binding, *, locked, visiting=None):
    from .assemblies import _members_at as validate_members
    from .materials import _same_json
    from ..registry.event_store import RegistryConflict
    members = validate_members(db, core, plan, registration, binding, visiting=visiting)
    for row in plan["members"]:
        expected = _resolution(members[row["member_id"]])
        if locked and not _same_json(row["resolution"], expected):
            raise RegistryConflict("Assembly v2 member exact material selection differs")
        row["resolution"] = expected
    return members



def _host_metadata_at(db, core, kind, key, metadata):
    """Check only this gateway's HOST-resource schema authority at the same cut."""
    import json
    from ..registry._event_store.collaboration_descriptors import exact_descriptor
    from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS
    from ..registry.event_store import RegistryConflict
    from .materials import _same_json
    descriptors = {"host_registration_kind": kind, "registered_key": key}
    if kind == "schema" and key in PROTECTED_SCHEMA_REFS:
        descriptors["mechanical_schema"] = True
    if not _same_json(metadata["descriptors"], descriptors):
        raise RegistryConflict("Assembly v2 HOST registration role/key metadata differs")
    if kind != "schema":
        if metadata["content_schema_ref"] is not None or metadata["content_schema_authority_ref"] is not None:
            raise RegistryConflict("Assembly v2 untyped HOST declaration has unexpected schema authority")
        return
    schema = "registry_v1/registry_type_catalog/v1"
    if metadata["content_schema_ref"] != schema:
        raise RegistryConflict("Assembly v2 HOST schema has an unexpected content-schema contract")
    catalogs = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
    if len(catalogs) != 1:
        raise RegistryConflict("Assembly v2 HOST schema requires the one exact frozen catalog")
    authority = {"entity_type": "registry_type_catalog/v1", "logical_id": catalogs[0]["logical_id"],
                 "version_id": catalogs[0]["version_id"]}
    if not _same_json(metadata["content_schema_authority_ref"], authority):
        raise RegistryConflict("Assembly v2 HOST schema lacks its exact frozen schema authority")
    bundle = exact_descriptor(db, core.object_store, core.task_id, authority)
    entry = bundle["schemas"].get(schema)
    if not isinstance(entry, dict) or entry.get("schema_id") != schema:
        raise RegistryConflict("Assembly v2 HOST content-schema authority lacks its exact schema entry")
    try:
        actual = json.loads(entry["source"])
        expected = json.loads(core.catalog.schema_path(schema).read_text(encoding="utf-8"))
    except (ValueError, TypeError, KeyError) as exc:
        raise RegistryConflict("Assembly v2 HOST schema authority has invalid exact schema bytes") from exc
    if not _same_json(actual, expected):
        raise RegistryConflict("Assembly v2 HOST schema authority differs from the supported exact contract")


def _host_at(db, core, binding, compiled):
    """Select only already canonical actual-used HOST resources, never publish."""
    from .materials import HOST_SCHEMA, _private_document, _same_json
    from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS
    from ..registry.event_store import RegistryConflict
    registrations = compiled.to_dict()["registrations"]
    refs = []
    for kind, entries in sorted(registrations.items()):
        for key, declaration in sorted(entries.items()):
            command = (f"builtin-schema-source:{key}" if kind == "schema" and key in PROTECTED_SCHEMA_REFS
                       else f"host-registration:{kind}:{key}")
            reference = _material_ref(core, binding, command)
            body, metadata = _private_document(db, core, reference, binding,
                media_type="application/schema+json" if kind == "schema" else "application/json")
            _host_metadata_at(db, core, kind, key, metadata)
            if not _same_json(body, declaration["schema"] if kind == "schema" else declaration):
                raise RegistryConflict("Assembly v2 actual-used exact HOST bytes differ")
            refs.append({"kind": kind, "key": key, "resource_ref": reference.to_dict()})
    return {"schema_version": HOST_SCHEMA, "registrations": registrations, "declaration_refs": refs}


def _generated_ref(core, source_id, command_id, parent):
    command = _command(command_id) + ":generated"
    key = "collaboration-author:" + canonical_text({"command_id": command})
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    reference = SourceQualifiedVersionRef(source_id, VersionRef("collaboration_net_revision/v1",
        stable("resource") if parent is None else parent.revision.generated_revision_ref.ref.entity_id,
        stable("resource_version")))
    return command, key, reference


_REQUEST_FIELDS = ("schema_version", "source_id", "owner_task_ref", "producer_principal_ref", "command_id",
    "parent_revision_ref", "name", "members", "connections", "completion", "budget_policy", "deployment_intent", "resolver_recipe")


def _prepare_at(db, core, request, members, parent, registration, binding):
    from ._assembly_v2_lowering import compose_plan_v2, lowering_map_v2
    from .materials import _element_map, _boundaries, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA
    plan = {key: deepcopy(request[key]) for key in _REQUEST_FIELDS}
    parent_ref = None if parent is None else parent.revision.revision_ref
    reference = _result_ref(core, binding["source_id"], plan["command_id"], parent_ref)
    module, compiled = compose_plan_v2(plan, members, registration)
    mapping, ids = lowering_map_v2(plan, members, module, compiled, str(reference.ref.entity_id))
    element_map = _element_map(module, ids, None if parent is None else parent.generated, {})
    boundary_map = _boundaries(module, element_map)
    host = _host_at(db, core, binding, compiled)
    documents = (module.to_dict(), element_map, boundary_map, host, compiled.to_dict(), mapping)
    schemas = (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, "rpnh/executable_net/v1", LOWERING_V2_SCHEMA)
    roles = ("definition", "element_mapping", "boundary_mapping", "host_requirements", "compiled_inventory", "lowering_mapping")
    generated_command, generated_key, generated_ref = _generated_ref(core, binding["source_id"], plan["command_id"], parent)
    key = _command(plan["command_id"])
    keys = (*[f"{generated_key}:material:{i}" for i in range(4)], key + ":compiled", key + ":lowering")
    refs = tuple(_material_ref(core, binding, key) for key in keys)
    for schema, document in zip(schemas, documents, strict=True):
        core.catalog.validate_schema_ref(schema, document)
    plan.update(host_requirements=host, generated_revision_ref=generated_ref.to_dict(),
        prepared_materials=[material_signature(role, schema, ref, doc)
            for role, schema, ref, doc in zip(roles, schemas, refs, documents, strict=True)])
    core.catalog.validate_schema_ref(PLAN_V2_SCHEMA, plan)
    return plan, module, compiled, mapping, ids, documents, refs, reference, generated_command, generated_ref


def _envelope(plan, documents):
    return canonical_text({"schema_version": ASSEMBLY_COMMAND, "plan": plan, "documents": list(documents)})


def _validate_materials_at(db, core, record, registration, binding, parent, *, visiting=None):
    from .materials import _material, _same_json, _validate_at, ValidatedClosedRevision
    from ..registry.event_store import RegistryConflict
    active = (set() if visiting is None else visiting) | {record.revision_ref}
    plan, metadata = _material(db, core, record.plan_ref, binding, PLAN_V2_SCHEMA)
    parent_ref = None if parent is None else parent.revision.revision_ref
    expected_header = {"owner_task_ref": record.owner_task_ref.to_dict(),
        "producer_principal_ref": record.producer_principal_ref.to_dict(), "command_id": record.command_id,
        "source_id": binding["source_id"], "parent_revision_ref": None if parent_ref is None else parent_ref.to_dict()}
    if any(not _same_json(plan[key], value) for key, value in expected_header.items()):
        raise RegistryConflict("Assembly v2 descriptor differs from immutable plan")
    members = _members_at(db, core, plan, registration, binding, locked=True, visiting=active)
    expected, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = _prepare_at(
        db, core, plan, members, parent, registration, binding)
    if (not _same_json(plan, expected) or record.revision_ref != reference
            or record.plan_ref != _material_ref(core, binding, _command(record.command_id) + ":plan")
            or record.generated_revision_ref != generated_ref
            or record.compiled_inventory_ref != refs[4] or record.lowering_mapping_ref != refs[5]
            or metadata["descriptors"] != {"assembly_author_command_v2": _envelope(expected, docs)}):
        raise RegistryConflict("Assembly v2 complete command, prepared signatures or exact pair differs")
    generated = _validate_at(db, core, generated_ref, registration, binding, active)
    if (type(generated) is not ValidatedClosedRevision
            or generated.revision.command_id != generated_command
            or generated.revision.owner_task_ref != record.owner_task_ref
            or generated.revision.producer_principal_ref != record.producer_principal_ref
            or generated.revision.parent_revision_refs != (() if parent is None else (parent.revision.generated_revision_ref,))
            or tuple(getattr(generated.revision, field) for field in
                ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")) != refs[:4]
            or not _same_json((generated.module.to_dict(), generated.element_map, generated.boundary_map, generated.host_requirements), docs[:4])
            or not _same_json(generated.compiled.to_dict(), compiled.to_dict())):
        raise RegistryConflict("Assembly v2 generated closed-v1 revision differs from its exact paired derivation")
    inventory, _ = _material(db, core, record.compiled_inventory_ref, binding, "rpnh/executable_net/v1")
    persisted_map, _ = _material(db, core, record.lowering_mapping_ref, binding, LOWERING_V2_SCHEMA)
    if not _same_json((inventory, persisted_map), docs[4:]):
        raise RegistryConflict("Assembly v2 actual compiled inventory or source origin mapping differs")
    return ValidatedAssemblyRevisionV2(record, plan, generated, compiled, mapping)


def _validate_at(db, core, reference, registration, binding, visiting):
    from ..registry.event_store import RegistryConflict
    if reference in visiting:
        raise RegistryConflict("Assembly v2 parent ancestry contains a cycle")
    record = _read_at(db, core, reference, binding)
    active = visiting | {reference}
    parent = None if record.parent_revision_ref is None else _validate_at(db, core,
        record.parent_revision_ref, registration, binding, active)
    return _validate_materials_at(db, core, record, registration, binding, parent, visiting=active)


class AssemblyAuthorV2:
    """Explicit owner producer; first plan locks every exact prepared document."""
    def __init__(self, gateway, registration, producer_principal_ref):
        import json
        from ..registration import Registration
        from ..registry.registration_gateway import RegistryRegistrationGateway
        from ..registry._event_store.collaboration_descriptors import exact_descriptor
        from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS
        from ..registry.event_store import RegistryConflict
        from .materials import ClosedModuleAuthor
        if not isinstance(gateway, RegistryRegistrationGateway) or not isinstance(registration, Registration):
            raise TypeError("Assembly v2 requires exact owner gateway and trusted Registration")
        core = gateway._core
        core.catalog.require(ASSEMBLY_V2_TYPE, category="object")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, core)
            if gateway._task_ref != _version_from_payload(binding["task_ref"]) or gateway._bootstrap_ref != _version_from_payload(binding["bootstrap_command_ref"]):
                raise RegistryConflict("Assembly v2 gateway differs from local exact authority")
            _exact_object_ref(producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
            if producer_principal_ref.source_id != binding["source_id"]:
                raise RegistryConflict("Assembly v2 producer must have the same exact source")
            exact_descriptor(db, core.object_store, core.task_id, producer_principal_ref.to_dict()["ref"])
        self.author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = core, gateway, registration
        self.binding, self.producer = binding, producer_principal_ref
        self.schemas = {}
        for schema in (PLAN_V2_SCHEMA, LOWERING_V2_SCHEMA, "rpnh/executable_net/v1"):
            document = json.loads(core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = None if schema in PROTECTED_SCHEMA_REFS else gateway(
                "schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def _publish_document(self, key, schema, document, *, descriptors=None):
        from ..registry.resource_service import _publish_private_system
        from ..registry.resources import PrivateSystemOrigin, PublishResource
        return SourceQualifiedResourceRef(self.binding["source_id"], _publish_private_system(
            self.core, self.gateway._task_ref, PublishResource(origin=PrivateSystemOrigin(self.gateway._bootstrap_ref),
                payload=canonical_json(document), media_type="application/json", content_schema_ref=schema,
                content_schema_authority_ref=self.schemas[schema], summary=f"Assembly v2 material {schema}",
                lifetime_ref=self.gateway._bootstrap_ref, descriptors=descriptors or {}, idempotency_key=key)))

    def publish(self, *, name, members, connections, completion, budget_policy, deployment_intent, command_id, parent_ref=None):
        from .assemblies import AssemblyConnection, AssemblyCompletion, validate_assembly_revision
        from ..registry.event_store import RegistryConflict, StaleWriterError
        from ..registry.errors import ResourceIdempotencyConflict
        from ..registry.object_store import ObjectIntegrityError
        key = _command(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("Assembly v2 publisher uses a stale owner writer")
        if (not isinstance(members, (tuple, list)) or not members or any(not isinstance(m, AssemblyMemberV2) for m in members)
                or not isinstance(connections, (tuple, list)) or any(not isinstance(c, AssemblyConnection) for c in connections)
                or not isinstance(completion, AssemblyCompletion)):
            raise TypeError("Assembly v2 requires explicit typed members, connections and completion")
        if budget_policy != "shared_exact" or deployment_intent != "same_run_candidate":
            raise ValueError("Assembly v2 requires explicit shared_exact and same_run_candidate")
        if parent_ref is not None:
            _assembly_ref(parent_ref)
        owner = SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref)
        request = {"schema_version": PLAN_V2_SCHEMA, "source_id": self.binding["source_id"],
            "owner_task_ref": owner.to_dict(), "producer_principal_ref": self.producer.to_dict(), "command_id": command_id,
            "parent_revision_ref": None if parent_ref is None else parent_ref.to_dict(), "name": name,
            "members": sorted((m.to_dict() for m in members), key=lambda m: m["member_id"]),
            "connections": sorted((c.to_dict() for c in connections), key=canonical_text), "completion": completion.to_dict(),
            "budget_policy": budget_policy, "deployment_intent": deployment_intent, "resolver_recipe": resolver_recipe()}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            from ..registry._event_store.collaboration_descriptors import exact_descriptor
            if self.producer != self.author.producer or self.producer.source_id != binding["source_id"]:
                raise RegistryConflict("Assembly v2 configured producer changed or differs from local source")
            producer = exact_descriptor(db, self.core.object_store, self.core.task_id, self.producer.to_dict()["ref"])
            if (producer["principal_id"] != str(self.producer.ref.entity_id)
                    or producer["principal_version_id"] != str(self.producer.ref.version_id)):
                raise RegistryConflict("Assembly v2 producer self-identity differs")
            prospective_ref = _result_ref(self.core, binding["source_id"], command_id, parent_ref)
            active = {prospective_ref}
            parent = None if parent_ref is None else _validate_at(db, self.core, parent_ref, self.registration, binding, active)
            selected = _members_at(db, self.core, request, self.registration, binding, locked=False, visiting=active)
            plan, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = _prepare_at(
                db, self.core, request, selected, parent, self.registration, binding)
        try:
            plan_ref = self._publish_document(key + ":plan", PLAN_V2_SCHEMA, plan,
                descriptors={"assembly_author_command_v2": _envelope(plan, docs)})
            if self.core.event_store.object_row(reference.ref.version_id) is not None:
                return validate_assembly_revision(self.core, reference, self.registration)
            generated = self.author.publish(module=module, element_ids=ids, command_id=generated_command,
                parent_ref=None if parent is None else parent.revision.generated_revision_ref)
            if generated.revision.revision_ref != generated_ref:
                raise RegistryConflict("Assembly v2 generated result identity differs")
            compiled_ref = self._publish_document(key + ":compiled", "rpnh/executable_net/v1", compiled.to_dict())
            mapping_ref = self._publish_document(key + ":lowering", LOWERING_V2_SCHEMA, mapping)
            record = AssemblyRevisionV2(reference, owner, self.producer, command_id, parent_ref, plan_ref,
                generated_ref, compiled_ref, mapping_ref)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                final_binding = _binding_at(db, self.core)
                final_parent = None if parent_ref is None else _validate_at(
                    db, self.core, parent_ref, self.registration, final_binding, {reference})
                _validate_materials_at(db, self.core, record, self.registration, final_binding,
                    final_parent, visiting={reference})
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=ASSEMBLY_V2_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
                payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json", schema_ref=ASSEMBLY_V2_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("Assembly v2 command conflicts with immutable complete prepared material") from exc
        return validate_assembly_revision(self.core, reference, self.registration)
