"""Native RPNH plugin SDK. Importable without creating a Registry or process."""
from .api import (API_VERSION, API_VERSION_V2, PluginContext, PluginDefinition, PluginError,
                  PluginOperation, PluginResource, ResourceView, plugin_implementation)
from .catalog import BoundPlugin, PluginCatalog, load_catalog
from .managed_tools import (
    ManagedPluginInvocationConflict, ManagedPluginInvocationFailed,
    ManagedPluginInvocationReconciliationRequired,
    ManagedPluginInvocationService, ManagedPluginToolAdapter,
    ManagedPluginToolCatalog, ManagedToolDeclaration, ManagedToolSelector,
    build_managed_plugin_tool_catalog,
)

__all__ = ("API_VERSION", "API_VERSION_V2", "plugin_implementation", "PluginContext", "PluginDefinition", "PluginError",
           "PluginOperation", "PluginResource", "ResourceView", "BoundPlugin",
           "PluginCatalog", "load_catalog", "ManagedPluginToolAdapter",
           "ManagedPluginToolCatalog", "ManagedPluginInvocationService",
           "ManagedPluginInvocationConflict", "ManagedPluginInvocationFailed",
           "ManagedPluginInvocationReconciliationRequired",
           "ManagedToolDeclaration", "ManagedToolSelector",
           "build_managed_plugin_tool_catalog")

