"""Independent public declaration and trusted HOST registration contracts.

Importing this package does not load optional executors, components or workflows.
Runtime/control integration is tracked separately in the implementation progress
artifact; these declaration exports alone are not execution acceptance evidence.
"""
from .registration import Registration, RegistrationError
from .module import ModuleDeclaration, SymbolicNet
from .petri_contracts import DeclarationError, PNFragment
from .petri_primitives import (
    PetriStructureDelta, PetriStructureDeltaError, PetriStructureEdit,
    PetriStructureSnapshot, apply_petri_structure_delta,
    derive_petri_structure_delta, verify_petri_structure_delta,
)
from .marking import (
    PetriFiringResourceAccess, PetriMarkingDelta, PetriTokenEdit,
    apply_petri_marking_delta, derive_petri_marking_delta,
    verify_petri_marking_delta,
)
from .firing_resource_access import (
    RegisteredPetriFiringResourceAccess,
    derive_registered_firing_resource_access,
    verify_registered_firing_resource_access_conflicts,
)
from .net_operations import (
    BranchResult, ComposeConnection, ComposePlan, ExtractPlan, ExtractResult,
    ReplacementPlan, ReentryPlan, WorkspaceBindingPlan, WorkspaceImportPlan,
    apply_replacement, branch_module, compose_modules, extract_module,
    instantiate_module, prepare_reentry, prepare_replacement,
    register_net_components,
)


def lower_module(module: ModuleDeclaration, registration: Registration) -> SymbolicNet:
    """Lower a human or Designer declaration through the same typed boundary."""
    if not isinstance(module, ModuleDeclaration):
        raise TypeError("lower_module requires the shared ModuleDeclaration")
    return module.lower(registration)


def start_run(module, registration, **owner_arguments):
    """Publish a fresh execution-owning Module lineage; no default execution."""
    from .run import start_run as start
    return start(module, registration, **owner_arguments)


def snapshot(owner_or_client):
    """Read exact owner/channel-client state without opening another writer."""
    return owner_or_client.snapshot()


__all__ = (
    "Registration", "RegistrationError", "ModuleDeclaration", "SymbolicNet",
    "DeclarationError", "PNFragment", "lower_module", "start_run", "snapshot",
    "PetriStructureDelta", "PetriStructureDeltaError", "PetriStructureEdit",
    "PetriStructureSnapshot", "apply_petri_structure_delta",
    "derive_petri_structure_delta", "verify_petri_structure_delta",
    "PetriFiringResourceAccess", "PetriMarkingDelta", "PetriTokenEdit",
    "apply_petri_marking_delta",
    "derive_petri_marking_delta", "verify_petri_marking_delta",
    "RegisteredPetriFiringResourceAccess",
    "derive_registered_firing_resource_access",
    "verify_registered_firing_resource_access_conflicts",
    "BranchResult", "ComposeConnection", "ComposePlan", "ExtractPlan",
    "ExtractResult", "ReplacementPlan", "ReentryPlan",
    "WorkspaceBindingPlan", "WorkspaceImportPlan", "apply_replacement",
    "branch_module", "compose_modules", "extract_module",
    "instantiate_module", "prepare_reentry", "prepare_replacement",
    "register_net_components",
)
