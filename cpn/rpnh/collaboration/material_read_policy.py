"""Current material policy for existing read-only projections.

The original cut still selects identities and pagination.  These helpers apply
current kernel facts only to disclosure and new body use; they grant no read
authority and never create a management firing or a delivery witness.
"""
from __future__ import annotations

from copy import deepcopy

from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.resources import ResourceVersionRef


# A resource summary, descriptor, name or title may contain its entire body.
# Governed zero-write observation therefore exposes only this finite structural
# projection, including when that resource is currently available.  Body reads
# require the separate real delivery protocol, not an index-field permission.
STRUCTURAL_RESOURCE_FIELDS = frozenset({
    "resource_id", "resource_version_id", "media_type", "content_schema_ref",
    "byte_count", "size", "commit_ordinal", "task_ref", "producer_ref",
    "round_ref", "net_ref", "origin_kind", "content_schema_authority_ref",
    "provenance_producer_invocation_ref", "provenance_operation_binding_ref",
})


class MaterialReadPolicyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def local_resource_ref(reference):
    """Normalize already source-checked exact refs; never discover a location."""
    if isinstance(reference, ResourceVersionRef):
        return reference
    if isinstance(reference, VersionRef):
        if reference.entity_type != "resource_version/v1":
            return None
        return ResourceVersionRef(reference.entity_id, reference.version_id)
    if hasattr(reference, "to_dict"):
        reference = reference.to_dict()
    if not isinstance(reference, dict):
        raise TypeError("an exact Registry reference is required")
    value = reference.get("ref", reference)
    if "resource_id" in value:
        return ResourceVersionRef(TypedId.parse(value["resource_id"], expected="resource"),
            TypedId.parse(value["resource_version_id"], expected="resource_version"))
    if value.get("entity_type") != "resource_version/v1":
        return None
    return ResourceVersionRef(TypedId.parse(value["logical_id"], expected="resource"),
        TypedId.parse(value["version_id"], expected="resource_version"))


def project_material_fields(core, reference, fields, *, source_cut=None):
    """Return a fresh field projection plus public current state, or no change.

    Effect/proof references and effect bodies are intentionally not disclosed.
    The resolver still considers them under full kernel authority, independent
    of the read session's public field or relation permissions.
    """
    ref = local_resource_ref(reference)
    if ref is None:
        return deepcopy(fields), None
    from ..material_availability import source_governs_materials
    from ..material_body_policy import resolve_material_body_availability
    current = resolve_material_body_availability(core, ref, source_cut=source_cut)
    governed = source_governs_materials(core)
    availability = current.get("availability")
    if availability not in {"available", "disabled", "unknown"}:
        raise MaterialReadPolicyError("MATERIAL_AVAILABILITY_UNKNOWN")
    if not governed and availability == "available":
        return deepcopy(fields), None
    public = {"availability": availability,
        "availability_as_of": deepcopy(current.get("availability_as_of")),
        "material_access": "explicit_delivery_required" if availability == "available"
            else "unavailable"}
    return {key: deepcopy(value) for key, value in fields.items()
            if key in STRUCTURAL_RESOURCE_FIELDS}, public


def require_untracked_material_read(core, reference, *, purpose):
    """Fail closed for governed body paths that do not own a real delivery.

    This is used before old GET/HEAD/editor/read-session reads and exports.  It
    cannot replace prepare/release or revoke an already authorized witness.
    """
    ref = local_resource_ref(reference)
    if ref is None:
        raise MaterialReadPolicyError("INVALID_REFERENCE")
    from ..material_availability import source_governs_materials
    from ..material_body_policy import require_material_body_available
    try:
        require_material_body_available(core, ref, purpose=purpose)
    except Exception as exc:
        from ..registry.event_store import RegistryConflict
        if not isinstance(exc, RegistryConflict):
            raise
        code = "MATERIAL_DISABLED" if str(exc).startswith("MATERIAL_DISABLED") else "MATERIAL_AVAILABILITY_UNKNOWN"
        raise MaterialReadPolicyError(code) from exc
    if source_governs_materials(core):
        raise MaterialReadPolicyError("GOVERNED_DELIVERY_REQUIRED")


__all__ = ("project_material_fields", "require_untracked_material_read",
    "local_resource_ref", "MaterialReadPolicyError", "STRUCTURAL_RESOURCE_FIELDS")
