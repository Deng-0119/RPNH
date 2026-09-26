"""Typed plans for existing Registry-owned replacement and historical reentry.

Plans are inert data until an execution owner applies them.  Replacement uses
the existing owner edit queue, which pauses new admissions, drains ordinary
firings, publishes the candidate closure, and adopts it transactionally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..module import ModuleDeclaration
from ..registry.models import VersionRef
from ..registry.resources import ResourceVersionRef
from ..registry.strict_contracts import ref_payload


@dataclass(frozen=True, slots=True)
class WorkspaceBindingPlan:
    mode: str
    revision_ref: VersionRef | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"create_empty", "reuse_existing", "fork_from_revision"}:
            raise ValueError("unknown workspace binding mode")
        if self.mode == "create_empty" and self.revision_ref is not None:
            raise ValueError("create_empty has no source revision")
        if self.mode != "create_empty" and (
                not isinstance(self.revision_ref, VersionRef)
                or self.revision_ref.entity_type != "workspace_revision/v1"):
            raise TypeError("workspace reuse/fork requires an exact workspace revision")


@dataclass(frozen=True, slots=True)
class WorkspaceImportPlan:
    source_revision_ref: VersionRef
    paths: tuple[str, ...]
    conflict_policy: str = "reject"

    def __post_init__(self) -> None:
        if (not isinstance(self.source_revision_ref, VersionRef)
                or self.source_revision_ref.entity_type != "workspace_revision/v1"):
            raise TypeError("workspace import requires an exact source revision")
        if (not self.paths or len(set(self.paths)) != len(self.paths)
                or any(not isinstance(path, str) or not path or path.startswith("/")
                       or ".." in path.split("/") for path in self.paths)):
            raise ValueError("workspace import paths must be unique relative paths")
        if self.conflict_policy != "reject":
            raise ValueError("only explicit conflict rejection is implemented")


@dataclass(frozen=True, slots=True)
class ReplacementPlan:
    candidate: ModuleDeclaration
    base_net_ref: VersionRef
    marking_mapping: Mapping[str, str]
    retire_token_refs: tuple[VersionRef, ...]
    owner_inputs: Mapping[str, tuple[Mapping[str, Any], ...]]
    activation_boundary: str = "whole_net_quiescent"
    state_transfer: str = "explicit_plan"
    workspace_binding: str = "preserve"
    budget_binding: str = "existing"

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ModuleDeclaration):
            raise TypeError("replacement candidate must be a ModuleDeclaration")
        if (not isinstance(self.base_net_ref, VersionRef)
                or self.base_net_ref.entity_type != "net_instance/v1"):
            raise TypeError("replacement requires an exact base net")
        if (self.activation_boundary != "whole_net_quiescent"
                or self.state_transfer != "explicit_plan"
                or self.workspace_binding != "preserve"
                or self.budget_binding != "existing"):
            raise ValueError("replacement requests an unsupported runtime mode")
        if (not isinstance(self.marking_mapping, Mapping)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in self.marking_mapping.items())):
            raise TypeError(
                "marking_mapping must map exact token version IDs to symbolic candidate places")
        if (not isinstance(self.retire_token_refs, tuple)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in self.retire_token_refs)):
            raise TypeError("retire_token_refs require exact Petri token refs")
        if not isinstance(self.owner_inputs, Mapping):
            raise TypeError("owner_inputs must be a symbolic input mapping")

    def owner_command(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "base_net_ref": ref_payload(self.base_net_ref),
            "marking_mapping": dict(self.marking_mapping),
            "retire_token_refs": [ref_payload(ref) for ref in self.retire_token_refs],
            "owner_inputs": {
                key: [dict(value) for value in values]
                for key, values in self.owner_inputs.items()
            },
        }


def prepare_replacement(owner, candidate: ModuleDeclaration, *,
        marking_mapping: Mapping[str, str] | None = None,
        retire_token_refs: tuple[VersionRef, ...] = (),
        owner_inputs: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> ReplacementPlan:
    """Bind a candidate to the owner's exact current net without writing."""
    if not isinstance(candidate, ModuleDeclaration):
        raise TypeError("replacement candidate must be a ModuleDeclaration")
    from ..budgets import validate_budget_binding
    from ..compiler import compile_module
    compiled = compile_module(candidate, owner.registration)
    declared_buckets = [dict(item) for item in owner.budgets.budget_buckets]
    candidate_buckets = candidate.to_dict().get("budget_buckets", [])
    if ({item["bucket_id"]: item for item in candidate_buckets}
            != {item["bucket_id"]: item for item in declared_buckets}):
        raise ValueError(
            "replacement must use the existing exact budget bucket inventory")
    budget_contract = {"budget_buckets": declared_buckets}
    for operation in compiled.operations:
        binding = operation.declaration.budget_binding
        if binding is not None:
            validate_budget_binding(budget_contract, {
                "budget_bucket_id": binding.bucket_id,
                "budget_scope": binding.budget_scope,
                "finalization_scope": binding.finalization_scope,
            })
    snapshot = owner.snapshot()
    net = snapshot.get("net_ref")
    if not isinstance(net, Mapping):
        raise ValueError("owner snapshot lacks an exact active net")
    from ..registry.publication import _version_from_payload
    return ReplacementPlan(
        candidate=candidate,
        base_net_ref=_version_from_payload(net),
        marking_mapping={} if marking_mapping is None else dict(marking_mapping),
        retire_token_refs=retire_token_refs,
        owner_inputs={} if owner_inputs is None else dict(owner_inputs),
    )


def apply_replacement(owner, plan: ReplacementPlan, *, command_id: str):
    """Apply through the sole owner's existing drain/adoption command path."""
    if not isinstance(plan, ReplacementPlan):
        raise TypeError("apply_replacement requires ReplacementPlan")
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("replacement requires a nonempty command_id")
    return owner.command("edit", plan.owner_command(), command_id=command_id)


@dataclass(frozen=True, slots=True)
class ReentryPlan:
    historical_boundary_ref: VersionRef
    reusable_resources: tuple[ResourceVersionRef, ...]
    target: ModuleDeclaration
    generation: int
    workspace: WorkspaceBindingPlan

    def __post_init__(self) -> None:
        if (not isinstance(self.historical_boundary_ref, VersionRef)
                or self.historical_boundary_ref.entity_type != "marking_checkpoint/v1"):
            raise TypeError("reentry requires an exact historical checkpoint")
        if (not isinstance(self.reusable_resources, tuple)
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in self.reusable_resources)):
            raise TypeError("reentry resources require exact immutable resource refs")
        if not isinstance(self.target, ModuleDeclaration):
            raise TypeError("reentry target must be a ModuleDeclaration")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("reentry generation must be positive")
        if not isinstance(self.workspace, WorkspaceBindingPlan):
            raise TypeError("reentry requires a workspace binding plan")


def prepare_reentry(*, historical_boundary_ref: VersionRef,
        reusable_resources: tuple[ResourceVersionRef, ...],
        target: ModuleDeclaration, generation: int,
        workspace: WorkspaceBindingPlan) -> ReentryPlan:
    """Describe a new generation; this never mutates or resumes old tokens."""
    return ReentryPlan(historical_boundary_ref, reusable_resources, target,
                       generation, workspace)
