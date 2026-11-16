"""Read the selected managed-binding contract before a recovery writer.

The already-validated Registry net supplies the exact readable resource refs.
This reader reuses AgentLoop's parser; it publishes no facts, chooses no new
binding and does not scan unselected historical resources.
"""
from __future__ import annotations

from cpn.components.agent_loop.managed_identity import (
    parse_managed_tool_bindings, validate_tool_catalog_binding,
)
from cpn.components.agent_loop.tool_catalog import parse_agent_tool_catalog

from .registry.errors import ResourceIntegrityFault
from .registry.publication import _version_from_payload
from .registry.strict_contracts import _registered


class ManagedContractAdmissionError(ValueError):
    """A selected exact managed resource has unsupported persisted semantics."""

    def __init__(self, binding_ref, resource_ref, reason: str):
        self.binding_ref = binding_ref
        self.resource_ref = resource_ref
        super().__init__(
            f"registered operation {binding_ref.version_id}: selected managed "
            f"binding resource {resource_ref.version_id} cannot satisfy its "
            f"persisted contract ({reason}); original binding retained")


class ToolCatalogAdmissionError(ValueError):
    """Selected catalog resources disagree with their original declarations."""

    def __init__(self, binding_ref, resource_refs, reason: str):
        self.binding_ref = binding_ref
        self.resource_refs = tuple(resource_refs)
        sources = ", ".join(str(ref.version_id) for ref in self.resource_refs)
        super().__init__(
            f"registered operation {binding_ref.version_id}: selected tool catalog "
            f"resources [{sources}] cannot satisfy their original declarations "
            f"({reason}); original bindings retained")


def require_selected_managed_contracts(core, net, *, compiled, registration) -> None:
    """Reuse selected bodies and the caller's already-loaded exact compilation."""
    if not core.read_only:
        raise TypeError("managed contract admission requires the read-only resume reader")
    managed_cache = {}
    catalog_cache = {}
    resource_roles = {}
    operations = {item.declaration.name: item for item in compiled.operations}
    transitions = {item.name: item.operation for item in compiled.symbolic.transitions}
    for raw in net["operation_binding_refs"]:
        binding_ref = _version_from_payload(raw)
        _, binding = _registered(core, binding_ref, "operation_binding/v1")
        managed_refs, catalog_refs = [], []
        for value in binding["readable_resource_refs"]:
            reference = _version_from_payload(value)
            if reference.entity_type != "resource_version/v1":
                continue
            if reference not in resource_roles:
                _, metadata = _registered(core, reference, "resource_version/v1")
                resource_roles[reference] = metadata.get("descriptors", {}).get("content_role")
            role = resource_roles[reference]
            if role == "optional_agent_tool_catalog":
                catalog_refs.append(reference)
            if role != "optional_agent_managed_tool_bindings":
                continue
            managed_refs.append(reference)
            if reference not in managed_cache:
                try:
                    prepared = core.get_version(reference.version_id)
                    managed_cache[reference] = parse_managed_tool_bindings(
                        core.object_store.read_registered(prepared))
                except ResourceIntegrityFault as error:
                    raise ManagedContractAdmissionError(binding_ref, reference, str(error)) from error
        if not catalog_refs:
            # Custom HOST operations need not use the optional AgentLoop
            # catalog. Do not reinterpret their declarations as built-ins.
            continue
        try:
            if len(catalog_refs) != 1:
                raise ValueError("optional HOST requires one exact optional_agent_tool_catalog")
            reference = catalog_refs[0]
            if reference not in catalog_cache:
                prepared = core.get_version(reference.version_id)
                catalog_cache[reference] = parse_agent_tool_catalog(
                    core.object_store.read_registered(prepared))
            catalog = catalog_cache[reference]
            _, node = _registered(core, _version_from_payload(binding["node_ref"]), "node_declaration/v1")
            operation = operations[transitions[node["transition_id"]]]
            # Match Context._managed_tool_bindings' existing absent/nonunique
            # mapping behavior; the original catalog checker resolves whether
            # the declared tool surface can actually use an empty mapping.
            managed = managed_cache[managed_refs[0]] if len(managed_refs) == 1 else {}
            validate_tool_catalog_binding(
                catalog=catalog, registered_payload=catalog.payload, compiled=compiled,
                operation=operation, registration=registration, managed=managed)
        except (ResourceIntegrityFault, KeyError, TypeError, ValueError) as error:
            raise ToolCatalogAdmissionError(
                binding_ref, (*catalog_refs, *managed_refs), str(error)) from error
