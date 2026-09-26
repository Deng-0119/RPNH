"""Native, application-neutral Petri-net definition and adoption operations.

The package deliberately separates pure declaration transforms from Registry
commands.  Importing it does not register components, start a run, or write to
a Registry.
"""

from .definitions import (
    BranchResult,
    ComposeConnection,
    ComposePlan,
    ExtractPlan,
    ExtractResult,
    branch_module,
    compose_modules,
    extract_module,
    instantiate_module,
)
from .runtime import (
    ReplacementPlan,
    ReentryPlan,
    WorkspaceBindingPlan,
    WorkspaceImportPlan,
    apply_replacement,
    prepare_reentry,
    prepare_replacement,
)


def register_net_components(registration) -> None:
    """Install the optional registered operation library at a HOST root."""
    from cpn.components.net_operations import register_net_components as install
    install(registration)

__all__ = (
    "BranchResult",
    "ComposeConnection",
    "ComposePlan",
    "ExtractPlan",
    "ExtractResult",
    "ReplacementPlan",
    "ReentryPlan",
    "WorkspaceBindingPlan",
    "WorkspaceImportPlan",
    "apply_replacement",
    "branch_module",
    "compose_modules",
    "extract_module",
    "instantiate_module",
    "prepare_reentry",
    "prepare_replacement",
    "register_net_components",
)
