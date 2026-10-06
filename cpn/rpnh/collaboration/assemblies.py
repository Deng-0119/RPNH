"""Opt-in same-source closed-member Assembly producer and strict consumer.

The immutable plan is the request lock; the Assembly descriptor is the success
point. Prepared resources and a generated author candidate are not deployment,
Branch publication, adoption, or runtime net instances.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import uuid

from ..registration import Registration
from ..executable_net import load_compiled_net
from .materials import _validate_source_at, _match_author_compiled, _HostDeclarationClosure
from ..executable_net import CompiledPetriNet
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId, fresh_bootstrap_resource_id
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import _stable_id, _version_from_payload
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from ._assembly_lowering import LOWERING_SCHEMA, compose_plan, lowering_map
from .authoring import _exact_object_ref, _revision_ref
from .materials import ClosedModuleAuthor, ValidatedClosedRevision, HOST_SCHEMA, _binding, _material, _same_json, _validate_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef


ASSEMBLY_TYPE = "collaboration_assembly_revision/v1"
ASSEMBLY_SCHEMA = "registry_v1/collaboration_assembly_revision/v1"
PLAN_SCHEMA = "rpnh/collaboration/assembly_plan/v1"
COMPILED_SCHEMA = "rpnh/executable_net/v1"
_MEMBER = re.compile(r"member:[a-f0-9]{32}")
_ELEMENT = re.compile(r"element:[a-f0-9]{32}")


def _identity(value, pattern, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical stable identity")


def _command(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Assembly command_id must be canonical")
    return "collaboration-assembly:" + canonical_text({"command_id": value})


def _assembly_ref(value):
    return _exact_object_ref(value, entity_type=ASSEMBLY_TYPE,
                            logical_kind="resource", version_kind="resource_version")


def _result_ref(core, source_id, command_id, parent_ref):
    key = _command(command_id)
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    return SourceQualifiedVersionRef(source_id, VersionRef(ASSEMBLY_TYPE,
        stable("resource") if parent_ref is None else parent_ref.ref.entity_id, stable("resource_version")))


@dataclass(frozen=True, slots=True)
class AssemblyMember:
    member_id: str
    display_name: str
    revision_ref: SourceQualifiedVersionRef

    def __post_init__(self):
        _identity(self.member_id, _MEMBER, "member_id")
        if not isinstance(self.display_name, str):
            raise TypeError("Assembly member display_name must be a string")
        _revision_ref(self.revision_ref)

    def to_dict(self):
        return {"member_id": self.member_id, "display_name": self.display_name,
                "revision_ref": self.revision_ref.to_dict()}


@dataclass(frozen=True, slots=True)
class AssemblyConnection:
    source_member_id: str
    source_exit_element_id: str
    target_member_id: str
    target_entry_element_id: str

    def __post_init__(self):
        for value in (self.source_member_id, self.target_member_id):
            _identity(value, _MEMBER, "connection member")
        for value in (self.source_exit_element_id, self.target_entry_element_id):
            _identity(value, _ELEMENT, "connection element")
        if self.source_member_id == self.target_member_id:
            raise ValueError("Assembly connections must cross members")

    def to_dict(self):
        return {key: getattr(self, key) for key in (
            "source_member_id", "source_exit_element_id", "target_member_id", "target_entry_element_id")}


@dataclass(frozen=True, slots=True)
class AssemblyCompletion:
    member_id: str
    terminal_element_id: str

    def __post_init__(self):
        _identity(self.member_id, _MEMBER, "completion member")
        _identity(self.terminal_element_id, _ELEMENT, "completion terminal")

    def to_dict(self):
        return {"member_id": self.member_id, "terminal_element_id": self.terminal_element_id}


@dataclass(frozen=True, slots=True)
class AssemblyRevision:
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
        refs = (self.owner_task_ref, self.producer_principal_ref, self.plan_ref,
                self.generated_revision_ref, self.compiled_inventory_ref, self.lowering_mapping_ref)
        if self.parent_revision_ref is not None:
            refs += (self.parent_revision_ref,)
        if any(value.source_id != self.revision_ref.source_id for value in refs):
            raise ValueError("Assembly supports only one exact local source")

    def to_dict(self):
        return {"schema_version": ASSEMBLY_SCHEMA, "command_id": self.command_id,
                **{field: getattr(self, field).to_dict() for field in (
                    "revision_ref", "owner_task_ref", "producer_principal_ref", "plan_ref",
                    "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")},
                "parent_revision_ref": None if self.parent_revision_ref is None else self.parent_revision_ref.to_dict()}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(ASSEMBLY_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        resource = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]),
            obj(document["producer_principal_ref"]), document["command_id"],
            None if document["parent_revision_ref"] is None else obj(document["parent_revision_ref"]),
            resource(document["plan_ref"]), obj(document["generated_revision_ref"]),
            resource(document["compiled_inventory_ref"]), resource(document["lowering_mapping_ref"]))


@dataclass(frozen=True)
class ValidatedAssemblyRevision:
    revision: AssemblyRevision
    plan: dict
    generated: ValidatedClosedRevision
    compiled: CompiledPetriNet
    lowering_map: dict


def _read_at(db, core, reference, binding):
    _assembly_ref(reference)
    core.catalog.require(ASSEMBLY_TYPE, category="object")
    if reference.source_id != binding["source_id"]:
        raise RegistryConflict("Assembly source differs from the bound local source")
    record = AssemblyRevision.from_dict(exact_descriptor(db, core.object_store, core.task_id,
        reference.to_dict()["ref"]), catalog=core.catalog)
    if record.revision_ref != reference or not _same_json(record.owner_task_ref.to_dict()["ref"], binding["task_ref"]):
        raise RegistryConflict("Assembly self-reference or exact bound owner differs")
    for qualified, kind in ((record.owner_task_ref, "task"), (record.producer_principal_ref, "principal")):
        ref = qualified.to_dict()["ref"]
        value = exact_descriptor(db, core.object_store, core.task_id, ref)
        if value[f"{kind}_id"] != ref["logical_id"] or value[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("Assembly owner/producer self-identity differs")
    return record


def read_assembly_revision(core, reference):
    """Canonical descriptor read only; use validate_assembly_revision for materials."""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v9":
            from .assembly_v9 import _read_at as read_v9, _binding_at
            return read_v9(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v8":
            from .assembly_v8 import _read_at as read_v8, _binding_at
            return read_v8(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v6":
            from .assembly_v6 import _read_at as read_v6, _binding_at
            return read_v6(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v7":
            from .assembly_v7 import _read_at as read_v7, _binding_at
            return read_v7(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v5":
            from .assembly_v5 import _read_at as read_v5, _binding_at
            return read_v5(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v4":
            from .assembly_v4 import _read_at as read_v4, _binding_at
            return read_v4(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v3":
            from .assembly_v3 import _read_at as read_v3, _binding_at
            return read_v3(db, core, reference, _binding_at(db, core))
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v2":
            from .assembly_v2 import _read_at as read_v2, _binding_at
            return read_v2(db, core, reference, _binding_at(db, core))
        return _read_at(db, core, reference, _binding(db, core))


def _members_at(db, core, plan, registration, binding, *, visiting=None, _static=False, _host_closure=None):
    # Siblings share the active outer proof path, never a global seen set.
    visiting = set() if visiting is None else visiting
    identities = [item["member_id"] for item in plan["members"]]
    if len(set(identities)) != len(identities) or identities != sorted(identities):
        raise RegistryConflict("Assembly members must be unique and canonically ordered")
    connections = [canonical_text(value) for value in plan["connections"]]
    if len(set(connections)) != len(connections) or connections != sorted(connections):
        raise RegistryConflict("Assembly connections must be unique and canonically ordered")
    def read_member(item):
        reference = SourceQualifiedVersionRef.from_dict(item["revision_ref"], catalog=core.catalog)
        if _static:
            return _validate_source_at(db, core, reference, binding, visiting, _host_closure=_host_closure)
        return _validate_at(db, core, reference, registration, binding, visiting)
    members = {item["member_id"]: read_member(item) for item in plan["members"]}
    if any(getattr(member, "adaptation_claim", None) is not None
           or getattr(member, "historical_origins_ref", None) is not None for member in members.values()):
        raise RegistryConflict("unsupported_contract: legacy Assembly cannot consume adapted member claims")
    return members


def _validate_assembly_at(db, core, reference, registration, binding, visiting, *, _static=False, _host_closure=None):
    if _static and _host_closure is None:
        _host_closure = _HostDeclarationClosure()
    if reference in visiting:
        raise RegistryConflict("Assembly ancestry contains a cycle")
    record = _read_at(db, core, reference, binding)
    active = visiting | {reference}
    parent = None if record.parent_revision_ref is None else _validate_assembly_at(
        db, core, record.parent_revision_ref, registration, binding, active, _static=_static, _host_closure=_host_closure)
    return _validate_materials_at(db, core, record, registration, binding, parent, visiting=active, _static=_static, _host_closure=_host_closure)


def _validate_materials_at(db, core, record, registration, binding, parent, *, visiting=None, _static=False, _host_closure=None):
    active = (set() if visiting is None else visiting) | {record.revision_ref}
    plan, _ = _material(db, core, record.plan_ref, binding, PLAN_SCHEMA)
    bootstrap = _version_from_payload(binding["bootstrap_command_ref"])
    key = _command(record.command_id) + ":plan"
    expected_plan = ResourceVersionRef(
        fresh_bootstrap_resource_id(core.task_id, bootstrap.version_id, key),
        _stable_id("resource_version", core.task_id, key, "fresh_bootstrap_reference",
                   bootstrap.version_id, bootstrap.version_id))
    if record.plan_ref.ref != expected_plan:
        raise RegistryConflict("Assembly plan is not the original immutable command resource")
    for key, expected in (("owner_task_ref", record.owner_task_ref.to_dict()),
            ("producer_principal_ref", record.producer_principal_ref.to_dict()),
            ("command_id", record.command_id), ("source_id", binding["source_id"]),
            ("parent_revision_ref", None if parent is None else parent.revision.revision_ref.to_dict())):
        if not _same_json(plan[key], expected):
            raise RegistryConflict("Assembly descriptor differs from its immutable complete plan")
    if parent is not None and record.revision_ref.ref.entity_id != parent.revision.revision_ref.ref.entity_id:
        raise RegistryConflict("Assembly parent must belong to the same logical history")
    members = _members_at(db, core, plan, registration, binding, visiting=active, _static=_static, _host_closure=_host_closure)
    if _static:
        inventory, _ = _material(db, core, record.compiled_inventory_ref, binding, COMPILED_SCHEMA)
        module, compiled = compose_plan(plan, members, registration, compiled_inventory=load_compiled_net(inventory))
        _validate_shared_host_declarations(members, compiled, _host_closure=_host_closure)
    else:
        module, compiled = compose_plan(plan, members, registration)
    mapping, ids = lowering_map(plan, members, module, compiled, str(record.revision_ref.ref.entity_id))
    if _static:
        generated = _validate_source_at(db, core, record.generated_revision_ref, binding, active, _host_closure=_host_closure)
        generated = _match_author_compiled(generated, compiled)
    else:
        generated = _validate_at(db, core, record.generated_revision_ref, registration, binding, active)
    if (not _same_json(generated.module.to_dict(), module.to_dict())
            or not _same_json(generated.host_requirements, plan["host_requirements"])
            or generated.revision.command_id != _command(record.command_id) + ":generated"
            or generated.revision.owner_task_ref != record.owner_task_ref
            or generated.revision.producer_principal_ref != record.producer_principal_ref
            or generated.revision.parent_revision_refs != (() if parent is None else (parent.revision.generated_revision_ref,))
            or not _same_json({item["locator"]: item["element_id"] for item in generated.element_map["elements"]}, ids)):
        raise RegistryConflict("Assembly generated author material differs from its exact plan")
    inventory, _ = _material(db, core, record.compiled_inventory_ref, binding, COMPILED_SCHEMA)
    persisted_map, _ = _material(db, core, record.lowering_mapping_ref, binding, LOWERING_SCHEMA)
    if (not _same_json(inventory, compiled.to_dict()) or not _same_json(inventory, generated.compiled.to_dict())
            or not _same_json(persisted_map, mapping)):
        raise RegistryConflict("Assembly compiled inventory or complete lowering map differs from actual compile")
    if record.revision_ref != _result_ref(core, binding["source_id"], record.command_id, record.parent_revision_ref):
        raise RegistryConflict("Assembly result identity differs from its one immutable command result")
    return ValidatedAssemblyRevision(record, plan, generated, compiled, persisted_map)


def validate_assembly_revision(core, reference, registration):
    """Read-only strict consumer; reconstruct everything in one Registry snapshot."""
    if not isinstance(registration, Registration):
        raise TypeError("Assembly validation requires an explicit trusted Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v9":
            from .assembly_v9 import _validate_at as validate_v9, _binding_at
            return validate_v9(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v8":
            from .assembly_v8 import _validate_at as validate_v8, _binding_at
            return validate_v8(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v6":
            from .assembly_v6 import _validate_at as validate_v6, _binding_at
            return validate_v6(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v7":
            from .assembly_v7 import _validate_at as validate_v7, _binding_at
            return validate_v7(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v5":
            from .assembly_v5 import _validate_at as validate_v5, _binding_at
            return validate_v5(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v4":
            from .assembly_v4 import _validate_at as validate_v4, _binding_at
            return validate_v4(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v3":
            from .assembly_v3 import _validate_at as validate_v3, _binding_at
            return validate_v3(db, core, reference, registration, _binding_at(db, core), set())
        if isinstance(reference, SourceQualifiedVersionRef) and reference.ref.entity_type == "collaboration_assembly_revision/v2":
            from .assembly_v2 import _validate_at as validate_v2, _binding_at
            return validate_v2(db, core, reference, registration, _binding_at(db, core), set())
        return _validate_assembly_at(db, core, reference, registration, _binding(db, core), set())


class AssemblyAuthor:
    """Trusted owner publisher for single-layer closed-member candidates only."""

    def __init__(self, gateway, registration, producer_principal_ref):
        self.author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.author.core, gateway, registration
        self.binding, self.producer = self.author.binding, self.author.producer
        self.core.catalog.require(ASSEMBLY_TYPE, category="object")
        self.schemas = {}
        for schema in (PLAN_SCHEMA, LOWERING_SCHEMA, COMPILED_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = None if schema in PROTECTED_SCHEMA_REFS else gateway(
                "schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def _publish_document(self, key, schema, document):
        reference = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
            origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(document),
            media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=self.schemas[schema],
            summary=f"Assembly material {schema}", lifetime_ref=self.gateway._bootstrap_ref, idempotency_key=key))
        return SourceQualifiedResourceRef(self.binding["source_id"], reference)

    def _host_requirements(self, compiled):
        registrations = compiled.to_dict()["registrations"]
        refs = []
        for kind, entries in sorted(registrations.items()):
            for key, declaration in sorted(entries.items()):
                ref = self.gateway.declaration_refs.get((kind, key))
                if ref is None:
                    ref = self.gateway.bind_builtin_schema(key) if kind == "schema" and key in PROTECTED_SCHEMA_REFS else self.gateway(kind, key, declaration)
                refs.append({"kind": kind, "key": key,
                    "resource_ref": SourceQualifiedResourceRef(self.binding["source_id"], ref).to_dict()})
        return {"schema_version": HOST_SCHEMA, "registrations": registrations, "declaration_refs": refs}

    def publish(self, *, name, members, connections, completion, budget_policy,
                deployment_intent, command_id, parent_ref=None):
        key = _command(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("Assembly author uses a stale owner writer")
        if (not isinstance(members, (tuple, list)) or not members
                or any(not isinstance(item, AssemblyMember) for item in members)
                or not isinstance(connections, (tuple, list))
                or any(not isinstance(item, AssemblyConnection) for item in connections)
                or not isinstance(completion, AssemblyCompletion)):
            raise TypeError("Assembly requires explicit typed members, connections and root completion")
        if budget_policy != "shared_exact" or deployment_intent != "same_run_candidate":
            raise ValueError("Assembly requires explicit shared_exact budget and same_run_candidate deployment policies")
        owner = SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref)
        plan = {"schema_version": PLAN_SCHEMA, "source_id": self.binding["source_id"],
            "owner_task_ref": owner.to_dict(), "producer_principal_ref": self.producer.to_dict(), "command_id": command_id,
            "parent_revision_ref": None if parent_ref is None else _assembly_ref(parent_ref).to_dict(),
            "name": name, "members": sorted((item.to_dict() for item in members), key=lambda item: item["member_id"]),
            "connections": sorted((item.to_dict() for item in connections), key=canonical_text),
            "completion": completion.to_dict(), "budget_policy": budget_policy, "deployment_intent": deployment_intent}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding(db, self.core)
            reference = _result_ref(self.core, binding["source_id"], command_id, parent_ref)
            active = {reference}
            parent = None if parent_ref is None else _validate_assembly_at(db, self.core, parent_ref, self.registration, binding, active)
            selected = _members_at(db, self.core, plan, self.registration, binding, visiting=active)
        module, compiled = compose_plan(plan, selected, self.registration)
        plan["host_requirements"] = self._host_requirements(compiled)
        self.core.catalog.validate_schema_ref(PLAN_SCHEMA, plan)
        mapping, ids = lowering_map(plan, selected, module, compiled, str(reference.ref.entity_id))
        self.core.catalog.validate_schema_ref(LOWERING_SCHEMA, mapping)
        try:
            # First Assembly-dependent durable write fixes ALL input, including
            # labels/parent/exact members/HOST choices absent from generated PN.
            plan_ref = self._publish_document(key + ":plan", PLAN_SCHEMA, plan)
            existing = self.core.event_store.object_row(reference.ref.version_id)
            if existing is not None:
                prior = validate_assembly_revision(self.core, reference, self.registration)
                if not _same_json(prior.plan, plan) or prior.revision.plan_ref != plan_ref:
                    raise RegistryConflict("Assembly command conflicts with immutable plan")
                return prior
            generated = self.author.publish(module=module, element_ids=ids, command_id=key + ":generated",
                parent_ref=None if parent is None else parent.revision.generated_revision_ref)
            compiled_ref = self._publish_document(key + ":compiled", COMPILED_SCHEMA, compiled.to_dict())
            mapping_ref = self._publish_document(key + ":lowering", LOWERING_SCHEMA, mapping)
            record = AssemblyRevision(reference, owner, self.producer, command_id, parent_ref, plan_ref,
                generated.revision.revision_ref, compiled_ref, mapping_ref)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                final_binding = _binding(db, self.core)
                final_parent = None if parent_ref is None else _validate_assembly_at(
                    db, self.core, parent_ref, self.registration, final_binding, {reference})
                _validate_materials_at(db, self.core, record, self.registration, final_binding,
                    final_parent, visiting={reference})
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=ASSEMBLY_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
                payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json", schema_ref=ASSEMBLY_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("Assembly command conflicts with immutable prepared plan/material") from exc
        return validate_assembly_revision(self.core, reference, self.registration)


def _validate_shared_host_declarations(members, compiled, *, _host_closure=None):
    """Merge actual overlapping keys, retaining ancestor-only declarations."""
    if _host_closure is None:
        _host_closure = _HostDeclarationClosure()
    for value in members.values():
        _host_closure.observe(value.host_requirements["registrations"])
    _host_closure.observe(compiled.to_dict()["registrations"])
