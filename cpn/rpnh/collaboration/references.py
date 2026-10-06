"""Exact source-qualified references for optional collaboration records.

These data contracts neither resolve sources nor grant access. A source ID is
an opaque identity supplied by the caller, not a location or a display alias.
Local Registry refs keep their existing meaning and wire representation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog


SOURCE_VERSION_REF_SCHEMA = "rpnh/collaboration/source_version_ref/v1"
SOURCE_RESOURCE_REF_SCHEMA = "rpnh/collaboration/source_resource_ref/v1"
_ENTITY_TYPE = re.compile(r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*/v[1-9][0-9]*")


def _require_source_id(source_id: object) -> None:
    if (not isinstance(source_id, str) or not source_id
            or source_id != source_id.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in source_id)):
        raise ValueError("source_id must be a nonempty canonical source identity")


@dataclass(frozen=True, slots=True)
class SourceQualifiedVersionRef:
    """One exact object version in one explicitly named source domain.

This validates ref syntax, not existence, object-type/ID-kind authority, or
permission. The target record's producer/reader must check those boundaries.
"""

    source_id: str
    ref: VersionRef

    def __post_init__(self) -> None:
        _require_source_id(self.source_id)
        if (not isinstance(self.ref, VersionRef)
                or not isinstance(self.ref.entity_type, str)
                or _ENTITY_TYPE.fullmatch(self.ref.entity_type) is None
                or not isinstance(self.ref.entity_id, TypedId)
                or not isinstance(self.ref.version_id, TypedId)):
            raise TypeError("ref must be one exact typed VersionRef")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCE_VERSION_REF_SCHEMA,
            "source_id": self.source_id,
            "ref": {
                "entity_type": self.ref.entity_type,
                "logical_id": str(self.ref.entity_id),
                "version_id": str(self.ref.version_id),
            },
        }

    @classmethod
    def from_dict(
        cls, document: Mapping[str, Any], *, catalog: SchemaCatalog,
    ) -> SourceQualifiedVersionRef:
        """Read only the explicitly registered v1 contract; never infer source."""
        catalog.validate_schema_ref(SOURCE_VERSION_REF_SCHEMA, document)
        ref = document["ref"]
        return cls(
            source_id=document["source_id"],
            ref=VersionRef(
                ref["entity_type"], TypedId.parse(ref["logical_id"]),
                TypedId.parse(ref["version_id"]),
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceQualifiedResourceRef:
    """One exact resource version; locations and access paths stay separate."""

    source_id: str
    ref: ResourceVersionRef

    def __post_init__(self) -> None:
        _require_source_id(self.source_id)
        if (not isinstance(self.ref, ResourceVersionRef)
                or not isinstance(self.ref.resource_id, TypedId)
                or not isinstance(self.ref.resource_version_id, TypedId)):
            raise TypeError("ref must be one exact ResourceVersionRef")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SOURCE_RESOURCE_REF_SCHEMA,
            "source_id": self.source_id,
            "ref": {
                "resource_id": str(self.ref.resource_id),
                "resource_version_id": str(self.ref.resource_version_id),
            },
        }

    @classmethod
    def from_dict(
        cls, document: Mapping[str, Any], *, catalog: SchemaCatalog,
    ) -> SourceQualifiedResourceRef:
        catalog.validate_schema_ref(SOURCE_RESOURCE_REF_SCHEMA, document)
        ref = document["ref"]
        return cls(
            source_id=document["source_id"],
            ref=ResourceVersionRef(
                TypedId.parse(ref["resource_id"], expected="resource"),
                TypedId.parse(ref["resource_version_id"], expected="resource_version"),
            ),
        )
