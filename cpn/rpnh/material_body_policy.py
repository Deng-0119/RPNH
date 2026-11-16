"""Current body policy for exact resources and proven literal-file carriers.

This is a projection of original Registry facts.  It does not introduce an
availability authority, a content index, or a transitive derived-from rule.
Historical completion proofs continue to read their original evidence.
"""
from __future__ import annotations

from .registry.event_store import RegistryConflict, RegistryCorruptError
from .registry.errors import ResourceIntegrityFault
from .registry.object_store import ObjectIntegrityError
from .registry.schema_catalog import SchemaGovernanceError
from .registry.models import VersionRef
from .registry.resources import ResourceVersionRef
from .registry.strict_contracts import ref_payload
from .registry._event_store.collaboration_descriptors import (
    descriptor_store, exact_prepared, exact_descriptor,
)
from .registry._event_store.material_control import reference


_PROOF_ERRORS = (RegistryConflict, RegistryCorruptError, ResourceIntegrityFault,
                 ObjectIntegrityError, SchemaGovernanceError, KeyError, TypeError, ValueError, OSError)


def _ref(value):
    if isinstance(value, ResourceVersionRef):
        value = ref_payload(value.as_version_ref())
    elif isinstance(value, VersionRef):
        value = ref_payload(value)
    return reference(value, "resource_version/v1")


def _literal_inspection(store, db, task_id, prepared):
    """Identify a carrier through its actual adopted producer, then full proof."""
    from .material_carriers import inspect_material_carriers_at
    from .material_execution import MATERIAL_EXECUTOR, EXECUTOR_IDENTITY, EXECUTOR_CONTRACTS
    from .registry._event_store.material_settlement import material_admission

    producer = prepared.metadata.get("producer_ref")
    if producer is None or prepared.producer_invocation_id is None:
        return None
    objects = descriptor_store(store)
    invocation = exact_descriptor(db, objects, task_id, producer)
    if (producer["entity_type"] != "invocation/v1"
            or producer["logical_id"] != str(prepared.producer_invocation_id)):
        raise RegistryConflict("material body carrier producer differs from original invocation")
    binding = exact_descriptor(db, objects, task_id, invocation["operation_binding_ref"])
    spec = exact_descriptor(db, objects, task_id, binding["operation_spec_ref"])
    if spec.get("executor_key") != MATERIAL_EXECUTOR:
        return None
    if (spec.get("implementation_identity") != EXECUTOR_IDENTITY
            or any(spec.get("implementation_contracts", {}).get(k) != v
                   for k, v in EXECUTOR_CONTRACTS.items())):
        raise RegistryConflict("material body carrier has an incomplete literal producer contract")
    found = material_admission(store, db, task_id, invocation["own_transition_firing_ref"])
    if found is None:
        raise RegistryConflict("material body carrier lost its original command admission")
    admission, _command = found
    return inspect_material_carriers_at(store, db, task_id,
        material_command_ref=admission["owner_command_ref"])


def _unknown(resolution, reason):
    return {**resolution, "availability": "unknown", "public_reason": reason,
            "carrier_state": "unknown"}


def resolve_body_availability(store, db, task_id, resource_ref, *, source_cut=None):
    """Resolve at the caller's current cut, including a Registry write lock.

    An exact FILE materialization is restricted by its semantic product only
    when the original literal producer and complete FILE/workspace/Success
    mapping prove that relationship.  Other copies and derived versions are
    not implicitly included.  Original caller visibility is still required.
    """
    from .registry._event_store.material_availability import (
        resolve_availability, source_governs_materials_at, _publication_state,
    )
    exact = _ref(resource_ref)
    try:
        direct = resolve_availability(store, db, task_id, exact)
    except _PROOF_ERRORS:
        upper = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])
        return {"availability": "unknown", "availability_as_of": upper,
                "source_cut": source_cut, "body_resource_ref": exact,
                "public_reason": "CURRENT_MATERIAL_PROOF_UNAVAILABLE",
                "effect_refs": [], "proof_refs": [], "carrier_state": "unknown"}
    result = {**direct, "source_cut": source_cut, "body_resource_ref": exact,
              "restricted_by_resource_refs": [exact]}
    if direct["availability"] != "available":
        return result
    try:
        if not source_governs_materials_at(store, db, task_id):
            return result
        # A new own-firing product is not a canonical old carrier.  The
        # original resource kernel remains responsible for firing visibility.
        pending = db.execute("SELECT p.* FROM firing_temporary_members m "
            "LEFT JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
            "WHERE m.member_kind='object' AND m.member_identity=?",
            (exact["version_id"],)).fetchall()
        if len(pending) > 1 or any(row["firing_version_id"] is None for row in pending):
            raise RegistryConflict("material body has ambiguous original publication responsibility")
        if any(_publication_state(store, db, row) != "PUBLISHED" for row in pending):
            return result
        prepared = exact_prepared(db, descriptor_store(store), task_id, exact)
        if (prepared.metadata.get("origin_kind") != "workspace_write"
                or prepared.metadata.get("descriptors", {}).get("content_role") != "workspace_file"):
            return result
        inspection = _literal_inspection(store, db, task_id, prepared)
        if inspection is None:
            return result
        files = [item for item in inspection["carriers"] if item["role"] == "workspace_file"]
        if len(files) != 1 or files[0]["carrier_ref"] != exact:
            raise RegistryConflict("material body file differs from its exact original FILE evidence")
        semantic_ref = inspection["output_ref"]
        semantic = resolve_availability(store, db, task_id, semantic_ref)
        return {**result, "availability": semantic["availability"],
            "availability_as_of": semantic["availability_as_of"],
            "public_reason": semantic.get("public_reason"),
            "effect_refs": semantic.get("effect_refs", []),
            "proof_refs": semantic.get("proof_refs", []),
            "restricted_by_resource_refs": [exact, semantic_ref],
            "carrier_proof_refs": files[0]["proof_refs"]}
    except _PROOF_ERRORS:
        return _unknown(result, "CARRIER_PROOF_UNAVAILABLE")


def resolve_material_body_availability(core, resource_ref, *, source_cut=None):
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return resolve_body_availability(core.event_store, db, core.task_id,
            resource_ref, source_cut=source_cut)


def require_material_body_available(core, resource_ref, *, purpose):
    if not isinstance(purpose, str) or not purpose:
        raise TypeError("material body use requires an explicit purpose")
    result = resolve_material_body_availability(core, resource_ref)
    if result["availability"] != "available":
        raise RegistryConflict("MATERIAL_" + result["availability"].upper()
            + ": current body use is not available (" + purpose + ")")
    return result


def expanded_material_carrier_refs(store, db, task_id, target_resource_refs):
    """Finite exact release/drain scope, derived at the original lock cut."""
    result = {}
    for value in target_resource_refs:
        target = _ref(value)
        result[target["version_id"]] = target
        prepared = exact_prepared(db, descriptor_store(store), task_id, target)
        inspection = _literal_inspection(store, db, task_id, prepared)
        if inspection is None:
            continue
        if inspection["output_ref"] != target:
            raise RegistryConflict("availability target differs from its original semantic output")
        for entry in inspection["carriers"]:
            if entry["role"] in {"semantic_output", "workspace_file", "dedicated_input"}:
                exact = _ref(entry["carrier_ref"])
                result[exact["version_id"]] = exact
    return tuple(result[key] for key in sorted(result))


def require_workspace_material_available_at(store, db, task_id, revision_ref, *, purpose):
    """Check every exact registered archive member without reading its body."""
    from .material_carriers import _CutObjects
    from .registry._event_store.material_availability import source_governs_materials_at
    if not source_governs_materials_at(store, db, task_id):
        return
    if isinstance(revision_ref, VersionRef):
        revision_ref = ref_payload(revision_ref)
    exact = reference(revision_ref, "workspace_revision/v1")
    cut = _CutObjects(store, db, task_id)
    revision = cut.exact(exact)
    if (revision.metadata.get("settled") is not True
            or revision.metadata.get("payload_kind") != "full_workspace_tar"):
        raise RegistryConflict("MATERIAL_UNKNOWN: workspace is not an exact settled body archive")
    state = cut.state(exact)
    if revision.metadata.get("inventory_paths") != sorted(state):
        raise RegistryConflict("MATERIAL_UNKNOWN: workspace inventory differs from exact path provenance")
    for resource_ref in state.values():
        result = resolve_body_availability(store, db, task_id, resource_ref)
        if result["availability"] != "available":
            raise RegistryConflict("MATERIAL_" + result["availability"].upper()
                + ": workspace carries unavailable material (" + purpose + ")")
    return state


def require_workspace_material_available(core, revision_ref, *, purpose):
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return require_workspace_material_available_at(core.event_store, db, core.task_id,
            revision_ref, purpose=purpose)


__all__ = ("resolve_material_body_availability", "require_material_body_available",
           "require_workspace_material_available")
