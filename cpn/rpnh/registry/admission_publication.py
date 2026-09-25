"""Generic Registry primitives composed with one Petri firing admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import VersionRef
from .resources import ResourceVersionRef


@dataclass(frozen=True, slots=True)
class PreparedAdmissionObjectPublication:
    """One schema-registered immutable object to publish with admission."""

    ref: VersionRef
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (not isinstance(self.ref, VersionRef)
                or not isinstance(self.metadata, Mapping)):
            raise TypeError(
                "admission object publication requires an exact ref/metadata")


@dataclass(frozen=True, slots=True)
class PreparedAdmissionResourcePublication:
    """One immutable Registry resource plus its direct relation operands."""

    ref: ResourceVersionRef
    payload: bytes
    metadata: Mapping[str, Any]
    media_type: str
    direct_owner_refs: tuple[VersionRef, ...] = ()
    input_resource_refs: tuple[ResourceVersionRef, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.ref, ResourceVersionRef)
                or not isinstance(self.payload, bytes) or not self.payload
                or not isinstance(self.metadata, Mapping)
                or not isinstance(self.media_type, str) or not self.media_type
                or any(not isinstance(ref, VersionRef)
                       for ref in self.direct_owner_refs)
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in self.input_resource_refs)
                or len(set(self.direct_owner_refs))
                != len(self.direct_owner_refs)
                or len(set(self.input_resource_refs))
                != len(self.input_resource_refs)):
            raise TypeError(
                "admission resource publication is not an exact resource bundle")


@dataclass(frozen=True, slots=True)
class PreparedFiringAdmissionPublications:
    """Explicit module composition consumed by the generic admission writer."""

    invocation_ref: VersionRef
    activation_ref: VersionRef
    objects: tuple[PreparedAdmissionObjectPublication, ...]
    resources: tuple[PreparedAdmissionResourcePublication, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.invocation_ref, VersionRef)
                or self.invocation_ref.entity_type != "invocation/v1"
                or not isinstance(self.activation_ref, VersionRef)
                or any(not isinstance(
                    item, PreparedAdmissionObjectPublication)
                       for item in self.objects)
                or any(not isinstance(
                    item, PreparedAdmissionResourcePublication)
                       for item in self.resources)
                or len({item.ref for item in self.objects}) != len(self.objects)
                or len({item.ref for item in self.resources})
                != len(self.resources)
                or self.activation_ref not in {
                    item.ref for item in self.objects}):
            raise TypeError(
                "firing admission publications require exact unique primitives")


__all__ = (
    "PreparedAdmissionObjectPublication",
    "PreparedAdmissionResourcePublication",
    "PreparedFiringAdmissionPublications",
)
