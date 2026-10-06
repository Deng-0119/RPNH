"""Read an exact historical Module resource plan in one Registry cut.

This returns data and a new dependency snapshot. It does not select candidate
slots, compare a persisted plan, prove HOST lowering or grant execution rights.
"""
from __future__ import annotations

from dataclasses import dataclass

from ._candidate_plan_reads import same
from ._event_store.collaboration_descriptors import readable_payload
from ._module_resource_projection import project_module_resource_plan
from ._preserved_basis_reads import _BasisRead, _basis_read_context
from .content_schemas import _validate_schema_bytes
from .event_store import RegistryConflict
from .module_resources import ModuleResourcePlan
from .publication import _resource_from_payload, _version_from_payload
from .runtime_binding_contracts import freeze_candidate_document
from .strict_contracts import ref_payload


@dataclass(frozen=True, slots=True)
class _PreservedResourceRead:
    """Detached resource projection; no live connection or read policy."""
    snapshot: _BasisRead
    resource_plan: ModuleResourcePlan

    @property
    def basis(self):
        return self.snapshot.basis

    @property
    def dependency_evidence(self):
        return self.snapshot.dependency_evidence


_SELF_FIELDS = {
    "net_instance/v1": ("net_instance_ref", None),
    "team_design_root/v1": ("team_design_root_ref", None),
    "node_declaration/v1": ("node_ref", None),
    "operation_binding/v1": ("operation_binding_ref", "operation_binding"),
    "operation_spec/v1": ("operation_spec_ref", "operation_spec"),
    "output_binding/v1": (None, "output_binding"),
    "logical_artifact_slot/v1": ("logical_artifact_slot_ref", None),
    "resource_version/v1": (None, "resource"),
}


def _require_root_member(ref, root, field):
    if not any(same(ref, member) for member in root[field]):
        raise RegistryConflict("resource basis dependency is outside exact root " + field)


def _exact_producer_spec(producer, operation, root):
    spec = operation["operation_spec_ref"]
    if not same(producer["opaque_role_artifact_ref"], spec):
        raise RegistryConflict("resource basis producer role differs from its exact operation spec")
    _require_root_member(spec, root, "artifact_refs")


def _exact_declaration(net, root):
    pair = net["team_net_declaration_resource_ref"]
    declaration = _resource_from_payload(pair)
    if (not same(pair, root["team_net_declaration_resource_ref"])
            or not same(ref_payload(declaration.as_version_ref()), net["llm_macro_net_ref"])
            or not same(net["llm_macro_net_ref"], root["llm_macro_net_ref"])
            or not same(net["task_round_ref"], root["task_round_ref"])):
        raise RegistryConflict("resource basis declaration/root/round differs")
    _require_root_member(ref_payload(declaration.as_version_ref()), root, "resource_refs")
    return declaration


def _resource_plan_at(context):
    reads = context.reads
    metadata, schemas, inventories = {}, {}, {}

    def exact(ref, expected):
        body = reads.metadata(ref, expected)
        self_field, prefix = _SELF_FIELDS[expected]
        if ((self_field is not None and not same(body[self_field], ref_payload(ref)))
                or (prefix is not None and (body[prefix + "_id"] != str(ref.entity_id)
                    or body[prefix + "_version_id"] != str(ref.version_id)))):
            raise RegistryConflict("resource basis dependency self identity differs")
        metadata[ref] = body
        return body

    def compiled_at(ref):
        body = exact(ref, "resource_version/v1")
        value = reads.compiled(ref)
        if body["content_schema_ref"] != value.schema_version:
            raise RegistryConflict("resource basis declaration schema differs")
        return value

    def schema_at(pair, schema_id, compiled, root):
        # This Module role has always required a resource pair. A catalog
        # authority supported by other roles must not widen this contract.
        if type(pair) is not dict or set(pair) != {"resource_id", "resource_version_id"}:
            raise RegistryConflict("resource basis schema requires an exact resource pair")
        ref = _resource_from_payload(pair).as_version_ref()
        _require_root_member(ref_payload(ref), root, "resource_refs")
        exact(ref, "resource_version/v1")
        prepared = reads.prepared(ref, "resource_version/v1")
        actual_id, document = _validate_schema_bytes(readable_payload(
            reads.object_store, prepared, media_type="application/schema+json"))
        registered = compiled.registrations["schema"].get(schema_id)
        if (actual_id != schema_id or registered is None
                or not same(document, registered["schema"])):
            raise RegistryConflict("resource basis schema document differs from compiled registration")
        schemas[ref] = actual_id, document

    net = exact(context.basis.net_ref, "net_instance/v1")
    root = exact(context.root_ref, "team_design_root/v1")
    declaration = _exact_declaration(net, root)
    compiled = compiled_at(declaration.as_version_ref())
    bindings = net["module_resource_bindings"]
    for pair in bindings["owner_resource_inputs"].values():
        body = exact(_resource_from_payload(pair).as_version_ref(), "resource_version/v1")
        schema_at(body["content_schema_authority_ref"], body["content_schema_ref"], compiled, root)
    for value in net["node_refs"]:
        exact(_version_from_payload(value), "node_declaration/v1")
    for value in net["operation_binding_refs"]:
        operation = exact(_version_from_payload(value), "operation_binding/v1")
        exact(_version_from_payload(operation["operation_spec_ref"]), "operation_spec/v1")
        _exact_producer_spec(metadata[_version_from_payload(operation["node_ref"])], operation, root)
    for value in net["output_binding_refs"]:
        output = exact(_version_from_payload(value), "output_binding/v1")
        producer = metadata[_version_from_payload(output["node_ref"])]
        operation = metadata[_version_from_payload(producer["producer_operation_binding_ref"])]
        if not same(output["opaque_action_ref"], operation["operation_spec_ref"]):
            raise RegistryConflict("resource basis output differs from its exact producer spec")
        schema_at(output["content_schema_ref"], output["content_schema_id"], compiled, root)

    for value in bindings["slot_refs"].values():
        slot = exact(_version_from_payload(value), "logical_artifact_slot/v1")
        creation_root = exact(_version_from_payload(slot["team_design_root_ref"]), "team_design_root/v1")
        source_ref = _version_from_payload(slot["authored_index_ref"])
        exact(source_ref, "resource_version/v1")
        producer = exact(_version_from_payload(slot["producer_node_ref"]), "node_declaration/v1")
        output = exact(_version_from_payload(slot["producer_output_binding_ref"]), "output_binding/v1")
        creation_net = exact(_version_from_payload(output["net_ref"]), "net_instance/v1")
        operation = exact(_version_from_payload(producer["producer_operation_binding_ref"]), "operation_binding/v1")
        exact(_version_from_payload(operation["operation_spec_ref"]), "operation_spec/v1")
        _exact_producer_spec(producer, operation, creation_root)
        if (not same(ref_payload(_exact_declaration(creation_net, creation_root).as_version_ref()),
                slot["authored_index_ref"])
                or not same(output["task_round_ref"], creation_net["task_round_ref"])
                or not same(output["opaque_action_ref"], operation["operation_spec_ref"])):
            raise RegistryConflict("resource basis creation declaration/round/spec differs")
        source = compiled
        if not same(slot["team_design_root_ref"], ref_payload(context.root_ref)):
            if source_ref not in inventories:
                inventories[source_ref] = compiled_at(source_ref)
            source = inventories[source_ref]
        schema_at(output["content_schema_ref"], output["content_schema_id"], source, creation_root)

    return project_module_resource_plan(compiled, context.basis.net_ref, net,
        context.root_ref, root, declaration, exact_metadata=metadata,
        owner_schema_documents=schemas, creation_compiled=inventories)


def read_preserved_resource_plan(core, basis, *, _db=None):
    """Read a finite exact basis resource projection, without changing Registry state.

    The optional private DB must already hold this Registry's SQLite read cut.
    Supported JSON/resource roles use fixed canonical readers and offline wire
    validation. Tar/unknown roles are unsupported. No current-head fallback,
    global authority cache, HOST resolution or candidate publication occurs.
    """
    with _basis_read_context(core, basis, _db=_db) as context:
        plan = _resource_plan_at(context)
        snapshot = _BasisRead(context.basis, freeze_candidate_document({
            "dependency_evidence": context.reads.finish_dependencies()}))
        return _PreservedResourceRead(snapshot, plan)
