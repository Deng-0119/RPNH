"""Opt-in immutable author revision records, before publication policy.

A record pins authoring material; it is not a compiled Module, a Branch head,
or runtime adoption authority. Referenced maps and requirements keep their own
exact versions and must be checked by their eventual consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, Mapping

from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
from cpn.rpnh.registry._event_store.source_identity import read_source_binding
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef


NET_REVISION_TYPE = "collaboration_net_revision/v1"
NET_REVISION_SCHEMA = "registry_v1/collaboration_net_revision/v1"


class AuthorRevisionReadError(ValueError):
    """An author record cannot be read as the requested exact local authority."""


def _exact_object_ref(
    value: object, *, entity_type: str, logical_kind: str, version_kind: str,
) -> SourceQualifiedVersionRef:
    if (not isinstance(value, SourceQualifiedVersionRef)
            or value.ref.entity_type != entity_type
            or value.ref.entity_id.kind != logical_kind
            or value.ref.version_id.kind != version_kind):
        raise TypeError(f"expected one source-qualified exact {entity_type} ref")
    return value


def _revision_ref(value: object) -> SourceQualifiedVersionRef:
    return _exact_object_ref(value, entity_type=NET_REVISION_TYPE,
                             logical_kind="resource", version_kind="resource_version")


@dataclass(frozen=True, slots=True)
class NetRevision:
    """A typed immutable descriptor; definition and provenance are distinct.

Full parents describe revision ancestry. selected_change_refs record selected
change provenance without turning those sources into full merge parents.
Element/boundary maps and HOST requirements are independently versioned data.
An open region additionally pins its outstanding-boundary/assembly contract.
"""

    revision_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    producer_principal_ref: SourceQualifiedVersionRef
    command_id: str
    definition_kind: Literal["closed_module", "open_region"]
    definition_ref: SourceQualifiedResourceRef
    parent_revision_refs: tuple[SourceQualifiedVersionRef, ...]
    selected_change_refs: tuple[SourceQualifiedResourceRef, ...]
    element_mapping_ref: SourceQualifiedResourceRef
    boundary_mapping_ref: SourceQualifiedResourceRef
    host_requirements_ref: SourceQualifiedResourceRef
    open_region_contract_ref: SourceQualifiedResourceRef | None

    def __post_init__(self) -> None:
        _revision_ref(self.revision_ref)
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1",
                          logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.producer_principal_ref, entity_type="principal/v1",
                          logical_kind="principal", version_kind="principal_version")
        if any(value.source_id != self.revision_ref.source_id for value in (
                self.owner_task_ref, self.producer_principal_ref)):
            raise ValueError("author owner and producer must belong to the publication source")
        if (not isinstance(self.command_id, str) or not self.command_id
                or self.command_id != self.command_id.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.command_id)):
            raise ValueError("command_id must be one nonempty canonical command identity")
        if self.definition_kind not in {"closed_module", "open_region"}:
            raise ValueError("definition_kind must be closed_module or open_region")
        for value in (self.definition_ref, self.element_mapping_ref,
                      self.boundary_mapping_ref, self.host_requirements_ref):
            if not isinstance(value, SourceQualifiedResourceRef):
                raise TypeError("author material must use exact source-qualified resource refs")
        if (not isinstance(self.parent_revision_refs, tuple)
                or not isinstance(self.selected_change_refs, tuple)):
            raise TypeError("author provenance must be immutable tuples")
        for ref in self.parent_revision_refs:
            _revision_ref(ref)
        if (self.revision_ref in self.parent_revision_refs
                or len(set(self.parent_revision_refs)) != len(self.parent_revision_refs)):
            raise ValueError("revision parents must be unique and exclude the revision itself")
        if any(not isinstance(ref, SourceQualifiedResourceRef)
               for ref in self.selected_change_refs):
            raise TypeError("selected changes must use exact source-qualified resource refs")
        if len(set(self.selected_change_refs)) != len(self.selected_change_refs):
            raise ValueError("selected changes must be unique")
        if self.definition_kind == "closed_module":
            if self.open_region_contract_ref is not None:
                raise ValueError("closed_module cannot carry an unresolved open-region contract")
        elif not isinstance(self.open_region_contract_ref, SourceQualifiedResourceRef):
            raise TypeError("open_region requires its exact boundary/assembly contract ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": NET_REVISION_SCHEMA,
            "revision_ref": self.revision_ref.to_dict(),
            "owner_task_ref": self.owner_task_ref.to_dict(),
            "producer_principal_ref": self.producer_principal_ref.to_dict(),
            "command_id": self.command_id,
            "definition_kind": self.definition_kind,
            "definition_ref": self.definition_ref.to_dict(),
            "parent_revision_refs": [ref.to_dict() for ref in self.parent_revision_refs],
            "selected_change_refs": [ref.to_dict() for ref in self.selected_change_refs],
            "element_mapping_ref": self.element_mapping_ref.to_dict(),
            "boundary_mapping_ref": self.boundary_mapping_ref.to_dict(),
            "host_requirements_ref": self.host_requirements_ref.to_dict(),
            "open_region_contract_ref": (
                self.open_region_contract_ref.to_dict()
                if self.open_region_contract_ref is not None else None),
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any], *, catalog: SchemaCatalog) -> NetRevision:
        catalog.validate_instance(NET_REVISION_TYPE, category="object", instance=document)

        def obj(value):
            return SourceQualifiedVersionRef.from_dict(value, catalog=catalog)

        def resource(value):
            return SourceQualifiedResourceRef.from_dict(value, catalog=catalog)

        return cls(
            revision_ref=obj(document["revision_ref"]),
            owner_task_ref=obj(document["owner_task_ref"]),
            producer_principal_ref=obj(document["producer_principal_ref"]),
            command_id=document["command_id"],
            definition_kind=document["definition_kind"],
            definition_ref=resource(document["definition_ref"]),
            parent_revision_refs=tuple(obj(value) for value in document["parent_revision_refs"]),
            selected_change_refs=tuple(resource(value) for value in document["selected_change_refs"]),
            element_mapping_ref=resource(document["element_mapping_ref"]),
            boundary_mapping_ref=resource(document["boundary_mapping_ref"]),
            host_requirements_ref=resource(document["host_requirements_ref"]),
            open_region_contract_ref=(resource(document["open_region_contract_ref"])
                                      if document["open_region_contract_ref"] is not None else None),
        )


def read_net_revision(
    core: Any, reference: SourceQualifiedVersionRef, *, local_source_id: str,
) -> NetRevision:
    """Read a canonical exact record from the caller's selected local source.

The trusted caller supplies the core-to-source association. This is not a
remote resolver or a permission grant. No referenced author material is read,
compiled, or adopted here, and no registration or repair is performed.
"""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _read_net_revision_at(db, core, reference, local_source_id=local_source_id)


def _read_net_revision_at(db, core, reference, *, local_source_id):
    """Same strict descriptor boundary for consumers holding an existing cut."""
    _revision_ref(reference)
    if reference.source_id != local_source_id:
        raise AuthorRevisionReadError("revision source does not match the selected local source")
    # Optional support is checked before touching stored bytes. The metadata,
    # publications, terminal/promotion evidence and source binding share one cut.
    core.catalog.require(NET_REVISION_TYPE, category="object")
    binding = read_source_binding(db, core.catalog, core.task_id)
    if binding is not None and json.loads(binding["binding_metadata_json"])["source_id"] != local_source_id:
        raise AuthorRevisionReadError("selected local source differs from the canonical Registry binding")

    def descriptor(qualified):
        try:
            return exact_descriptor(db, core.object_store, core.task_id, qualified.to_dict()["ref"])
        except RegistryConflict as exc:
            raise AuthorRevisionReadError(str(exc)) from exc

    record = NetRevision.from_dict(descriptor(reference), catalog=core.catalog)
    if record.revision_ref != reference:
        raise AuthorRevisionReadError("record self-reference differs from its exact stored identity")
    if record.owner_task_ref.ref.entity_id != core.task_id:
        raise AuthorRevisionReadError("author record belongs to a different owner task")
    for authority in (record.owner_task_ref, record.producer_principal_ref):
        document = descriptor(authority)
        local = authority.ref
        kind = "task" if local.entity_type == "task/v1" else "principal"
        if document[f"{kind}_id"] != str(local.entity_id) or document[f"{kind}_version_id"] != str(local.version_id):
            raise AuthorRevisionReadError("owner or producer self-identity differs from its exact ref")
    return record
