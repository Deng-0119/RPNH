"""Explicit direct ordinary-derived Assembly contracts; proof belongs to v7.

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
from .assembly_v2 import _binding_at, _host_at, _material_ref


ASSEMBLY_V7_TYPE = "collaboration_assembly_revision/v7"
ASSEMBLY_V7_SCHEMA = "registry_v1/collaboration_assembly_revision/v7"
PLAN_V7_SCHEMA = "rpnh/collaboration/assembly_plan/v7"
LOWERING_V7_SCHEMA = "rpnh/collaboration/assembly_lowering_map/v7"
ASSEMBLY_COMMAND = "rpnh/collaboration/assembly_author_command/v7"
RESOLVER_CONTRACT = "rpnh/collaboration/direct_transplant_derived_member_resolver/v1"
CONSTRAINTS_CONTRACT = "rpnh/assembly_member_constraints/v3"
ORIGIN_CONTRACT = "rpnh/collaboration/direct_transplant_derived_member_origins/v1"

_RECIPE = {
    "contract": RESOLVER_CONTRACT,
    "member_contracts": ["plain_closed_v1", "ordinary_new_definition"],
    "history": "same_version_logical_history/v1",
    "containment": "direct_members/v1",
    "instance_identity": "exact_direct_member/v1",
    "component_scoping": "stable_member_uuid_prefix/v1",
    "public_boundary_scoping": "stable_member_uuid_prefix/v1",
    "constraints": CONSTRAINTS_CONTRACT,
    "budget_policy": "shared_exact",
    "completion": "selected_primary_with_alternatives/v1",
    "lowering": "actual_final_context_fragments/v1",
    "carrier_cut": "remove_only_assembly_connections/v1",
    "source_origins": ORIGIN_CONTRACT,
    "material_lock": "canonical_json_sha256_bytes/v1",
    "proof_authority": "explicit_assembly_entry/v1",
}


def resolver_recipe():
    return deepcopy(_RECIPE)


def _command(command_id):
    from .assemblies import _command as validate_command
    validate_command(command_id)
    return "collaboration-assembly-v7:" + canonical_text({"command_id": command_id})


def _assembly_ref(reference):
    return _exact_object_ref(reference, entity_type=ASSEMBLY_V7_TYPE,
                            logical_kind="resource", version_kind="resource_version")


def _member_ref(reference):
    return _revision_ref(reference)


def _result_ref(core, source_id, command_id, parent_ref):
    key = _command(command_id)
    if parent_ref is not None:
        _assembly_ref(parent_ref)
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    return SourceQualifiedVersionRef(source_id, VersionRef(ASSEMBLY_V7_TYPE,
        stable("resource") if parent_ref is None else parent_ref.ref.entity_id, stable("resource_version")))


def material_signature(role, schema, reference, document):
    payload = canonical_json(document)
    return {"role": role, "schema": schema, "resource_ref": reference.to_dict(),
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


@dataclass(frozen=True, slots=True)
class AssemblyMemberV7:
    member_id: str
    display_name: str
    revision_ref: SourceQualifiedVersionRef
    claim: str
    derived_command_ref: SourceQualifiedResourceRef | None
    historical_origins_ref: SourceQualifiedResourceRef | None

    def __post_init__(self):
        _identity(self.member_id, _MEMBER, "member_id")
        if not isinstance(self.display_name, str):
            raise TypeError("Assembly member display_name must be a string")
        _member_ref(self.revision_ref)
        if self.claim not in {"plain_closed_v1", "ordinary_new_definition"}:
            raise ValueError("explicit supported member claim required")
        derived = self.claim == "ordinary_new_definition"
        if any((not isinstance(value, SourceQualifiedResourceRef)) if derived else value is not None
               for value in (self.derived_command_ref, self.historical_origins_ref)):
            raise ValueError("member proof refs must match its explicit claim")

    def to_dict(self):
        return {"member_id": self.member_id, "display_name": self.display_name,
                "revision_ref": self.revision_ref.to_dict(), "claim": self.claim,
                "derived_command_ref": None if self.derived_command_ref is None else self.derived_command_ref.to_dict(),
                "historical_origins_ref": None if self.historical_origins_ref is None else self.historical_origins_ref.to_dict()}


@dataclass(frozen=True, slots=True)
class AssemblyRevisionV7:
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
                raise ValueError("Assembly parent must remain in its v7 logical history")
        if any(value.source_id != self.revision_ref.source_id for value in refs):
            raise ValueError("Assembly supports only one exact local source")

    def to_dict(self):
        return {"schema_version": ASSEMBLY_V7_SCHEMA, "command_id": self.command_id,
                **{field: getattr(self, field).to_dict() for field in (
                    "revision_ref", "owner_task_ref", "producer_principal_ref", "plan_ref",
                    "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")},
                "parent_revision_ref": None if self.parent_revision_ref is None else self.parent_revision_ref.to_dict()}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(ASSEMBLY_V7_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        resource = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]),
            obj(document["producer_principal_ref"]), document["command_id"],
            None if document["parent_revision_ref"] is None else obj(document["parent_revision_ref"]),
            resource(document["plan_ref"]), obj(document["generated_revision_ref"]),
            resource(document["compiled_inventory_ref"]), resource(document["lowering_mapping_ref"]))



@dataclass(frozen=True)
class ValidatedAssemblyRevisionV7:
    """Produced only by the full Assembly v7 consumer, never the v1 reader."""
    revision: AssemblyRevisionV7
    plan: dict
    generated: object
    compiled: object
    lowering_map: dict


def _read_at(db, core, reference, binding):
    from ..registry._event_store.collaboration_descriptors import exact_descriptor
    from ..registry.event_store import RegistryConflict
    _assembly_ref(reference)
    if reference.source_id != binding["source_id"]:
        raise RegistryConflict("Assembly v7 source differs from exact local binding")
    record = AssemblyRevisionV7.from_dict(exact_descriptor(db, core.object_store, core.task_id,
        reference.to_dict()["ref"]), catalog=core.catalog)
    if record.revision_ref != reference or record.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
        raise RegistryConflict("Assembly v7 self-reference or exact owner differs")
    for qualified, kind in ((record.owner_task_ref, "task"), (record.producer_principal_ref, "principal")):
        ref = qualified.to_dict()["ref"]
        body = exact_descriptor(db, core.object_store, core.task_id, ref)
        if body[f"{kind}_id"] != ref["logical_id"] or body[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("Assembly v7 authority self-identity differs")
    return record


def _resolution(member):
    from .plain_transplant_derived import ValidatedPlainTransplantDerivedRevision
    from .plain_merge_result import ValidatedPlainMergeRevision
    derived = type(member) is ValidatedPlainTransplantDerivedRevision
    merged = type(member) is ValidatedPlainMergeRevision
    return {"proof_kind": "ordinary_new_definition" if derived else "plain_merge_v1" if merged else "plain_closed_v1",
        "command_ref": member.command_ref.to_dict() if derived or merged else None,
        **{field: getattr(member.revision, field).to_dict() for field in
            ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")},
        "historical_origin_refs": [member.historical_origins_ref.to_dict()] if derived else []}


def _members_at(db, core, plan, registration, binding, *, locked, visiting=None):
    from .materials import _validate_at as closed_at, _same_json, ValidatedClosedRevision
    from .plain_transplant_derived import ValidatedPlainTransplantDerivedRevision
    from .plain_merge_result import ValidatedPlainMergeRevision
    from ..registry.event_store import RegistryConflict
    identities = [row["member_id"] for row in plan["members"]]
    connections = [canonical_text(row) for row in plan["connections"]]
    if not identities or identities != sorted(set(identities)) or connections != sorted(set(connections)):
        raise RegistryConflict("Assembly v7 members/connections must be unique and canonical")
    if not _same_json(plan["resolver_recipe"], resolver_recipe()):
        raise RegistryConflict("unsupported_contract: exact Assembly v7 resolver recipe required")
    members = {}
    for row in plan["members"]:
        reference = SourceQualifiedVersionRef.from_dict(row["revision_ref"], catalog=core.catalog)
        _member_ref(reference)
        member = closed_at(db, core, reference, registration, binding, set() if visiting is None else visiting)
        if row["claim"] == "ordinary_new_definition":
            if (type(member) is not ValidatedPlainTransplantDerivedRevision
                    or row["derived_command_ref"] != member.command_ref.to_dict()
                    or row["historical_origins_ref"] != member.historical_origins_ref.to_dict()):
                raise RegistryConflict("claim_not_proved: exact ordinary derived command and history refs required")
        elif row["claim"] == "plain_closed_v1":
            if (type(member) not in {ValidatedClosedRevision, ValidatedPlainMergeRevision}
                    or row["derived_command_ref"] is not None or row["historical_origins_ref"] is not None):
                raise RegistryConflict("claim_not_proved: plain claim cannot erase explicit derived authority")
        else:
            raise RegistryConflict("unsupported_contract: unknown ordinary member claim")
        expected = _resolution(member)
        if locked and not _same_json(row["resolution"], expected):
            raise RegistryConflict("Assembly v7 member exact proof/material selection differs")
        row["resolution"] = expected
        members[row["member_id"]] = member
    return members


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
    from ._assembly_v7_lowering import compose_plan_v7, lowering_map_v7
    from .materials import _element_map, _boundaries, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA
    plan = {key: deepcopy(request[key]) for key in _REQUEST_FIELDS}
    parent_ref = None if parent is None else parent.revision.revision_ref
    reference = _result_ref(core, binding["source_id"], plan["command_id"], parent_ref)
    module, compiled = compose_plan_v7(plan, members, registration)
    mapping, ids = lowering_map_v7(plan, members, module, compiled, str(reference.ref.entity_id))
    element_map = _element_map(module, ids, None if parent is None else parent.generated, {})
    boundary_map = _boundaries(module, element_map)
    host = _host_at(db, core, binding, compiled)
    documents = (module.to_dict(), element_map, boundary_map, host, compiled.to_dict(), mapping)
    schemas = (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, "rpnh/executable_net/v1", LOWERING_V7_SCHEMA)
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
    core.catalog.validate_schema_ref(PLAN_V7_SCHEMA, plan)
    return plan, module, compiled, mapping, ids, documents, refs, reference, generated_command, generated_ref


def _envelope_at(db, core, binding, plan, documents, refs, parent, authorities):
    from .materials import MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, _command_material
    from .plain_merge_result import _schema_authority_at, _metadata
    from .open_region import _obj
    from ._open_region_inventory import signature
    from ..registry.event_store import RegistryConflict
    schemas = (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, "rpnh/executable_net/v1", LOWERING_V7_SCHEMA)
    if set(authorities) != set((*schemas, PLAN_V7_SCHEMA, ASSEMBLY_COMMAND)):
        raise RegistryConflict("authority_mismatch: Assembly complete command schema set differs")
    digests = {schema: _schema_authority_at(db, core, binding, schema, authority)
               for schema, authority in authorities.items()}
    generated_command, _, _ = _generated_ref(core, binding["source_id"], plan["command_id"], parent)
    generated_marker = _command_material(source_id=binding["source_id"], owner=_obj(core, plan["owner_task_ref"]),
        producer=_obj(core, plan["producer_principal_ref"]), command_id=generated_command,
        parents=() if parent is None else (parent.revision.generated_revision_ref,), documents=documents[:4])
    specs = []
    roles = ("definition", "element_mapping", "boundary_mapping", "host_requirements", "compiled_inventory", "lowering_mapping")
    for index, (role, schema, reference, document) in enumerate(zip(roles, schemas, refs, documents, strict=True)):
        summary = f"Closed author material {index}" if index < 4 else f"Assembly v7 material {schema}"
        descriptors = {"closed_author_command_v1": generated_marker} if index == 0 else {}
        metadata = _metadata(core, binding, reference, schema, authorities[schema], document, summary, descriptors)
        specs.append({"role": role, "schema": schema, "resource_ref": reference.to_dict(), "document": document,
                      "metadata": metadata, **signature(document)})
    command = {"schema_version": ASSEMBLY_COMMAND, "plan": plan, "prepared_materials": specs,
               "schema_authorities": deepcopy(authorities), "schema_authority_sha256": digests}
    core.catalog.validate_schema_ref(ASSEMBLY_COMMAND, command)
    return command


def _validate_materials_at(db, core, record, registration, binding, parent, *, visiting=None):
    from .materials import _material, _same_json, _validate_at, ValidatedClosedRevision
    from ..registry.event_store import RegistryConflict
    active = (set() if visiting is None else visiting) | {record.revision_ref}
    plan, metadata = _material(db, core, record.plan_ref, binding, PLAN_V7_SCHEMA)
    import json
    from .plain_merge_result import _resource_bytes, _metadata
    try:
        if set(metadata["descriptors"]) != {"assembly_author_command_v7"}:
            raise ValueError("unknown or dual Assembly marker")
        command = json.loads(metadata["descriptors"]["assembly_author_command_v7"])
        core.catalog.validate_schema_ref(ASSEMBLY_COMMAND, command)
    except (ValueError, KeyError, TypeError) as exc:
        raise RegistryConflict("claim_not_proved: exact complete Assembly command required") from exc
    parent_ref = None if parent is None else parent.revision.revision_ref
    expected_header = {"owner_task_ref": record.owner_task_ref.to_dict(),
        "producer_principal_ref": record.producer_principal_ref.to_dict(), "command_id": record.command_id,
        "source_id": binding["source_id"], "parent_revision_ref": None if parent_ref is None else parent_ref.to_dict()}
    if any(not _same_json(plan[key], value) for key, value in expected_header.items()):
        raise RegistryConflict("Assembly v7 descriptor differs from immutable plan")
    members = _members_at(db, core, plan, registration, binding, locked=True, visiting=active)
    expected, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = _prepare_at(
        db, core, plan, members, parent, registration, binding)
    expected_command = _envelope_at(db, core, binding, expected, docs, refs, parent, command["schema_authorities"])
    plan_metadata = _metadata(core, binding, record.plan_ref, PLAN_V7_SCHEMA,
        command["schema_authorities"][PLAN_V7_SCHEMA], expected, f"Assembly v7 material {PLAN_V7_SCHEMA}",
        {"assembly_author_command_v7": canonical_text(expected_command)})
    if (not _same_json(plan, expected) or record.revision_ref != reference
            or record.plan_ref != _material_ref(core, binding, _command(record.command_id) + ":plan")
            or record.generated_revision_ref != generated_ref
            or record.compiled_inventory_ref != refs[4] or record.lowering_mapping_ref != refs[5]
            or not _same_json(command, expected_command) or not _same_json(metadata, plan_metadata)
            or _resource_bytes(db, core, record.plan_ref) != canonical_json(expected)):
        raise RegistryConflict("Assembly v7 complete command, prepared signatures or exact pair differs")
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
        raise RegistryConflict("Assembly v7 generated closed-v1 revision differs from its exact paired derivation")
    inventory, _ = _material(db, core, record.compiled_inventory_ref, binding, "rpnh/executable_net/v1")
    persisted_map, _ = _material(db, core, record.lowering_mapping_ref, binding, LOWERING_V7_SCHEMA)
    if not _same_json((inventory, persisted_map), docs[4:]):
        raise RegistryConflict("Assembly v7 actual compiled inventory or source origin mapping differs")
    for spec, selected in zip(expected_command["prepared_materials"], refs, strict=True):
        document, actual_metadata = _material(db, core, selected, binding, spec["schema"])
        if (_resource_bytes(db, core, selected) != canonical_json(spec["document"])
                or not _same_json(document, spec["document"]) or not _same_json(actual_metadata, spec["metadata"])):
            raise RegistryConflict("material_integrity_mismatch: Assembly exact prepared bytes/role differ")
    return ValidatedAssemblyRevisionV7(record, plan, generated, compiled, mapping)


def _validate_at(db, core, reference, registration, binding, visiting):
    from ..registry.event_store import RegistryConflict
    if reference in visiting:
        raise RegistryConflict("Assembly v7 parent ancestry contains a cycle")
    record = _read_at(db, core, reference, binding)
    active = visiting | {reference}
    parent = None if record.parent_revision_ref is None else _validate_at(db, core,
        record.parent_revision_ref, registration, binding, active)
    return _validate_materials_at(db, core, record, registration, binding, parent, visiting=active)


class AssemblyAuthorV7:
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
            raise TypeError("Assembly v7 requires exact owner gateway and trusted Registration")
        core = gateway._core
        core.catalog.require(ASSEMBLY_V7_TYPE, category="object")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, core)
            if gateway._task_ref != _version_from_payload(binding["task_ref"]) or gateway._bootstrap_ref != _version_from_payload(binding["bootstrap_command_ref"]):
                raise RegistryConflict("Assembly v7 gateway differs from local exact authority")
            _exact_object_ref(producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
            if producer_principal_ref.source_id != binding["source_id"]:
                raise RegistryConflict("Assembly v7 producer must have the same exact source")
            exact_descriptor(db, core.object_store, core.task_id, producer_principal_ref.to_dict()["ref"])
        self.author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = core, gateway, registration
        self.binding, self.producer = binding, producer_principal_ref
        self.schemas = dict(self.author.schemas)
        for schema in (PLAN_V7_SCHEMA, LOWERING_V7_SCHEMA, ASSEMBLY_COMMAND, "rpnh/executable_net/v1"):
            document = json.loads(core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = None if schema in PROTECTED_SCHEMA_REFS else gateway(
                "schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def _publish_document(self, key, schema, document, *, descriptors=None):
        from ..registry.resource_service import _publish_private_system
        from ..registry.resources import PrivateSystemOrigin, PublishResource
        return SourceQualifiedResourceRef(self.binding["source_id"], _publish_private_system(
            self.core, self.gateway._task_ref, PublishResource(origin=PrivateSystemOrigin(self.gateway._bootstrap_ref),
                payload=canonical_json(document), media_type="application/json", content_schema_ref=schema,
                content_schema_authority_ref=self.schemas[schema], summary=f"Assembly v7 material {schema}",
                lifetime_ref=self.gateway._bootstrap_ref, descriptors=descriptors or {}, idempotency_key=key)))

    def publish(self, *, name, members, connections, completion, budget_policy, deployment_intent, command_id, parent_ref=None):
        from .assemblies import AssemblyConnection, AssemblyCompletion, validate_assembly_revision
        from ..registry.event_store import RegistryConflict, StaleWriterError
        from ..registry.errors import ResourceIdempotencyConflict
        from ..registry.object_store import ObjectIntegrityError
        key = _command(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("Assembly v7 publisher uses a stale owner writer")
        if (not isinstance(members, (tuple, list)) or not members or any(not isinstance(m, AssemblyMemberV7) for m in members)
                or not isinstance(connections, (tuple, list)) or any(not isinstance(c, AssemblyConnection) for c in connections)
                or not isinstance(completion, AssemblyCompletion)):
            raise TypeError("Assembly v7 requires explicit typed members, connections and completion")
        if budget_policy != "shared_exact" or deployment_intent != "same_run_candidate":
            raise ValueError("Assembly v7 requires explicit shared_exact and same_run_candidate")
        if parent_ref is not None:
            _assembly_ref(parent_ref)
        owner = SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref)
        request = {"schema_version": PLAN_V7_SCHEMA, "source_id": self.binding["source_id"],
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
                raise RegistryConflict("Assembly v7 configured producer changed or differs from local source")
            producer = exact_descriptor(db, self.core.object_store, self.core.task_id, self.producer.to_dict()["ref"])
            if (producer["principal_id"] != str(self.producer.ref.entity_id)
                    or producer["principal_version_id"] != str(self.producer.ref.version_id)):
                raise RegistryConflict("Assembly v7 producer self-identity differs")
            prospective_ref = _result_ref(self.core, binding["source_id"], command_id, parent_ref)
            active = {prospective_ref}
            parent = None if parent_ref is None else _validate_at(db, self.core, parent_ref, self.registration, binding, active)
            selected = _members_at(db, self.core, request, self.registration, binding, locked=False, visiting=active)
            plan, module, compiled, mapping, ids, docs, refs, reference, generated_command, generated_ref = _prepare_at(
                db, self.core, request, selected, parent, self.registration, binding)
            from .open_region import _authorities_at
            envelope = _envelope_at(db, self.core, binding, plan, docs, refs, parent,
                _authorities_at(db, self.core, self.schemas))
        try:
            plan_ref = self._publish_document(key + ":plan", PLAN_V7_SCHEMA, plan,
                descriptors={"assembly_author_command_v7": canonical_text(envelope)})
            if self.core.event_store.object_row(reference.ref.version_id) is not None:
                return validate_assembly_revision(self.core, reference, self.registration)
            generated = self.author.publish(module=module, element_ids=ids, command_id=generated_command,
                parent_ref=None if parent is None else parent.revision.generated_revision_ref)
            if generated.revision.revision_ref != generated_ref:
                raise RegistryConflict("Assembly v7 generated result identity differs")
            compiled_ref = self._publish_document(key + ":compiled", "rpnh/executable_net/v1", compiled.to_dict())
            mapping_ref = self._publish_document(key + ":lowering", LOWERING_V7_SCHEMA, mapping)
            record = AssemblyRevisionV7(reference, owner, self.producer, command_id, parent_ref, plan_ref,
                generated_ref, compiled_ref, mapping_ref)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                final_binding = _binding_at(db, self.core)
                final_parent = None if parent_ref is None else _validate_at(
                    db, self.core, parent_ref, self.registration, final_binding, {reference})
                _validate_materials_at(db, self.core, record, self.registration, final_binding,
                    final_parent, visiting={reference})
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=ASSEMBLY_V7_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
                payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json", schema_ref=ASSEMBLY_V7_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("Assembly v7 command conflicts with immutable complete prepared material") from exc
        return validate_assembly_revision(self.core, reference, self.registration)
