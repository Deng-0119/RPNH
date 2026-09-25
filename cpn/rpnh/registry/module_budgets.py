"""Explicit Module budget authority through the sole Core manifest producer.

This boundary creates no call baseline, checkpoint, adoption or firing. A typed
publication receipt is an address inventory, not a substitute for Registry facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..budgets import validate_budget_buckets, validate_budget_binding
from ._registry import _RegistryCore
from .event_store import validate_registered_net_closure
from .models import VersionRef
from .module_nets import ModuleNetPublication
from .resources import ResourceVersionRef
from .strict_contracts import _registered, ref_payload


@dataclass(frozen=True, slots=True)
class ModuleBudgetDeclaration:
    """All quantities/scopes are chosen explicitly by the execution owner."""
    budget_buckets: tuple[Mapping[str, Any], ...]
    protocol_versions: tuple[str, ...]
    ordinary_global_cap: int
    terminal_quota: int
    task_total_hard_cap: int
    finalization_budget: int

    def contract(self, inventory_ref: ResourceVersionRef, inventory_schema_id: str) -> dict[str, Any]:
        records = validate_budget_buckets([dict(record) for record in self.budget_buckets])
        if (not self.protocol_versions or any(not isinstance(v, str) or not v for v in self.protocol_versions)
                or len(set(self.protocol_versions)) != len(self.protocol_versions)):
            raise ValueError("Module budgets require exact unique protocol versions")
        for value in (self.ordinary_global_cap, self.task_total_hard_cap):
            if type(value) is not int or value < 1:
                raise ValueError("Module ordinary/task budget quantities must be positive integers")
        for value in (self.terminal_quota, self.finalization_budget):
            if type(value) is not int or value < 0:
                raise ValueError("Module terminal budget quantities must be nonnegative integers")
        return {"budget_buckets": records, "protocol_versions": list(self.protocol_versions),
            "host_resource_inventory_ref": str(inventory_ref.resource_version_id),
            "inventory_schema_id": inventory_schema_id,
            "ordinary_global_cap": self.ordinary_global_cap, "terminal_quota": self.terminal_quota,
            "task_total_hard_cap": self.task_total_hard_cap, "finalization_budget": self.finalization_budget}


def publish_module_budgets(core: _RegistryCore, publication: ModuleNetPublication, *,
                           inventory_ref: ResourceVersionRef, inventory_schema_id: str,
                           declaration: ModuleBudgetDeclaration) -> VersionRef:
    """Verify real bindings against explicit records, then publish Core authority.

    No role interpretation, scope default, cap arithmetic or writer constructor.
    Recovery/startup manifest immutability and call accounting remain Core-owned.
    """
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("Module budgets require the execution owner's Registry")
    if not isinstance(publication, ModuleNetPublication) or not isinstance(declaration, ModuleBudgetDeclaration):
        raise TypeError("Module budgets require typed actual publication and explicit budget declaration")
    if not isinstance(inventory_ref, ResourceVersionRef):
        raise TypeError("Module inventory requires an exact ResourceVersionRef")
    if not isinstance(inventory_schema_id, str) or not inventory_schema_id:
        raise ValueError("Module inventory requires an explicit schema ID")
    _registered(core, inventory_ref.as_version_ref(), "resource_version/v1")
    net = validate_registered_net_closure(core.event_store, core.catalog, publication.net_ref)
    _, root = _registered(core, publication.root_ref, "team_design_root/v1")
    if (net["team_design_root_ref"] != ref_payload(publication.root_ref)
            or root["task_ref"]["logical_id"] != str(core.task_id)
            or len(set(publication.binding_refs.values())) != len(publication.binding_refs)
            or {tuple(sorted(value.items())) for value in net["operation_binding_refs"]}
                != {tuple(sorted(ref_payload(ref).items())) for ref in publication.binding_refs.values()}):
        raise ValueError("Module budget receipt differs from this task's exact binding closure")
    contract = declaration.contract(inventory_ref, inventory_schema_id)
    bindings = []
    for ref in publication.binding_refs.values():
        _, binding = _registered(core, ref, "operation_binding/v1")
        validate_budget_binding(contract, binding)
        bindings.append(binding)
    manifest_ref = core.create_recovery_manifest(contract)
    _, manifest = _registered(core, manifest_ref, "task_recovery_manifest/v1")
    for binding in bindings:
        validate_budget_binding(manifest, binding)
    return manifest_ref


__all__ = ("ModuleBudgetDeclaration", "publish_module_budgets")
