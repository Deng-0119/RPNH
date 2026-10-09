"""Opt-in collaboration contracts; importing this package changes no runtime."""

from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .authoring import NetRevision, AuthorRevisionReadError, read_net_revision
from .schema_catalog import authoring_schema_data, collaboration_schema_data, source_identity_schema_data, branch_schema_data, author_material_schema_data
from .sources import LocalSourceIdentity, get_local_source_identity
from .branches import BranchVersion, GraphBranchVersion, current_branch, read_branch_version
from .materials import ClosedModuleAuthor, ValidatedClosedRevision, validate_closed_revision
from .assemblies import (
    AssemblyMember, AssemblyConnection, AssemblyCompletion, AssemblyRevision,
    AssemblyAuthor, ValidatedAssemblyRevision, read_assembly_revision, validate_assembly_revision,
)
from .schema_catalog import assembly_schema_data, graph_author_schema_data, graph_branch_schema_data
from .graph_authoring import GraphModuleAuthor, GraphNetRevision, ValidatedGraphRevision
from .graph_source import make_graph_source, make_graph_recipe, graph_source_elements, rebuild_graph_module

from .assembly_v2 import AssemblyMemberV2, AssemblyRevisionV2, AssemblyAuthorV2, ValidatedAssemblyRevisionV2
from .schema_catalog import graph_assembly_schema_data

__all__ = (
    "AssemblyAuthorV2",
    "ValidatedAssemblyRevisionV2",
    "AssemblyMemberV2",
    "AssemblyRevisionV2",
    "graph_assembly_schema_data",
    "SourceQualifiedResourceRef",
    "SourceQualifiedVersionRef",
    "collaboration_schema_data",
    "NetRevision",
    "AuthorRevisionReadError",
    "read_net_revision",
    "authoring_schema_data",
    "LocalSourceIdentity",
    "get_local_source_identity",
    "source_identity_schema_data",
    "BranchVersion",
    "GraphBranchVersion",
    "current_branch",
    "read_branch_version",
    "branch_schema_data",
    "author_material_schema_data",
    "ClosedModuleAuthor",
    "ValidatedClosedRevision",
    "validate_closed_revision",
    "AssemblyMember",
    "AssemblyConnection",
    "AssemblyCompletion",
    "AssemblyRevision",
    "AssemblyAuthor",
    "ValidatedAssemblyRevision",
    "read_assembly_revision",
    "validate_assembly_revision",
    "assembly_schema_data",
    "graph_author_schema_data",
    "graph_branch_schema_data",
    "GraphModuleAuthor",
    "GraphNetRevision",
    "ValidatedGraphRevision",
    "make_graph_source",
    "make_graph_recipe",
    "graph_source_elements",
    "rebuild_graph_module",
)

from .plain_merge import PlainModuleMergeAnalyzer, ValidatedPlainMergeAnalysis, read_plain_merge_analysis
from .schema_catalog import plain_merge_schema_data

__all__ += ("PlainModuleMergeAnalyzer", "ValidatedPlainMergeAnalysis", "read_plain_merge_analysis", "plain_merge_schema_data")

from .plain_merge_result import PlainModuleMergeAuthor, ValidatedPlainMergeRevision
from ._plain_merge_resolution import UnresolvedPlainMerge
from .schema_catalog import plain_merge_result_schema_data

__all__ += ("PlainModuleMergeAuthor", "ValidatedPlainMergeRevision", "UnresolvedPlainMerge", "plain_merge_result_schema_data")

from .schema_catalog import plain_merge_assembly_schema_data

__all__ += ("plain_merge_assembly_schema_data",)

from .assembly_v3 import AssemblyMemberV3, AssemblyRevisionV3, AssemblyAuthorV3, ValidatedAssemblyRevisionV3
from .schema_catalog import nested_assembly_schema_data, plain_merge_nested_assembly_schema_data

__all__ += ("AssemblyMemberV3", "AssemblyRevisionV3", "AssemblyAuthorV3", "ValidatedAssemblyRevisionV3",
            "nested_assembly_schema_data", "plain_merge_nested_assembly_schema_data")

from .open_region import OpenRegionAuthor, ValidatedOpenRegion, validate_open_revision
from .schema_catalog import open_region_schema_data

__all__ += ("OpenRegionAuthor", "ValidatedOpenRegion", "validate_open_revision", "open_region_schema_data")

from .open_region_closure import OpenRegionClosureAuthor, ValidatedAdaptedRevision
from .open_region_derived import OpenRegionDerivedAuthor, ValidatedOpenDerivedRevision
from .assembly_v5 import AssemblyMemberV5, AssemblyRevisionV5, AssemblyAuthorV5, ValidatedAssemblyRevisionV5
from .schema_catalog import open_region_derived_schema_data, open_region_derived_assembly_schema_data

__all__ += ("OpenRegionDerivedAuthor", "ValidatedOpenDerivedRevision", "AssemblyMemberV5", "AssemblyRevisionV5",
            "AssemblyAuthorV5", "ValidatedAssemblyRevisionV5", "open_region_derived_schema_data",
            "open_region_derived_assembly_schema_data")
from .schema_catalog import open_region_closure_schema_data

__all__ += ("OpenRegionClosureAuthor", "ValidatedAdaptedRevision", "open_region_closure_schema_data")

from .assembly_v4 import AssemblyMemberV4, AssemblyRevisionV4, AssemblyAuthorV4, ValidatedAssemblyRevisionV4
from .schema_catalog import open_region_assembly_schema_data

__all__ += ("AssemblyMemberV4", "AssemblyRevisionV4", "AssemblyAuthorV4", "ValidatedAssemblyRevisionV4",
            "open_region_assembly_schema_data")

from .source_sets import SourceMember, SourceSetVersion, read_source_set, current_source_set
from .schema_catalog import source_set_schema_data

__all__ += ('SourceMember', 'SourceSetVersion', 'read_source_set', 'current_source_set', 'source_set_schema_data')

from .source_query import SourceSetQuery, RegistrySourceReader, SourceUnavailable, SourceNotEstablished, SourceObservationDraft
from .schema_catalog import source_observation_schema_data

__all__ += ('SourceSetQuery', 'RegistrySourceReader', 'SourceUnavailable', 'SourceNotEstablished', 'SourceObservationDraft',
            'source_observation_schema_data')

from .worksets import (WorksetOwner, WorksetExpectation, ExportResult, AcceptDelivery,
                      Contribute, CompleteWorkset, CompleteWorksetNormalChildren, bind_local_workset_source,
                      prepare_local_delivery, finish_local_delivery, read_record)
from .schema_catalog import workset_schema_data, normal_child_root_schema_data
from .root_terminals import read_root_terminal

__all__ += ("WorksetOwner", "WorksetExpectation", "ExportResult", "AcceptDelivery",
            "Contribute", "CompleteWorkset", "bind_local_workset_source",
            "prepare_local_delivery", "finish_local_delivery", "read_record", "workset_schema_data")
__all__ += ("CompleteWorksetNormalChildren", "normal_child_root_schema_data", "read_root_terminal")


from .plain_transplant import PlainModuleTransplantAnalyzer, ValidatedPlainTransplantAnalysis, read_plain_transplant_analysis
from .plain_transplant_result import PlainModuleTransplantAuthor, ValidatedPlainTransplantRevision
from ._plain_transplant_resolution import UnresolvedPlainTransplant
from .schema_catalog import plain_transplant_schema_data, plain_transplant_assembly_schema_data

__all__ += ("PlainModuleTransplantAnalyzer", "ValidatedPlainTransplantAnalysis", "read_plain_transplant_analysis",
            "PlainModuleTransplantAuthor", "ValidatedPlainTransplantRevision", "UnresolvedPlainTransplant",
            "plain_transplant_schema_data", "plain_transplant_assembly_schema_data")

from .plain_transform import PlainModuleTransformAuthor, ValidatedPlainTransformRevision
from .schema_catalog import plain_transform_schema_data, plain_transform_assembly_schema_data

__all__ += ("PlainModuleTransformAuthor", "ValidatedPlainTransformRevision", "plain_transform_schema_data",
            "plain_transform_assembly_schema_data")

from .plain_transplant_derived import PlainTransplantDerivedAuthor, ValidatedPlainTransplantDerivedRevision
from .assembly_v7 import AssemblyMemberV7, AssemblyRevisionV7, AssemblyAuthorV7, ValidatedAssemblyRevisionV7
from .schema_catalog import plain_transplant_derived_schema_data, plain_transplant_derived_assembly_schema_data

__all__ += ("PlainTransplantDerivedAuthor", "ValidatedPlainTransplantDerivedRevision",
            "AssemblyMemberV7", "AssemblyRevisionV7", "AssemblyAuthorV7", "ValidatedAssemblyRevisionV7",
            "plain_transplant_derived_schema_data", "plain_transplant_derived_assembly_schema_data")

from .assembly_merge import AssemblyMergeAnalyzer, ValidatedAssemblyMergeAnalysis, UnresolvedAssemblyMerge, read_assembly_merge_analysis
from .assembly_v6 import AssemblyRevisionV6, AssemblyAuthorV6, AssemblyMergeAuthor, ValidatedAssemblyRevisionV6
from .schema_catalog import assembly_merge_schema_data
__all__ += ("AssemblyMergeAnalyzer", "ValidatedAssemblyMergeAnalysis", "UnresolvedAssemblyMerge",
            "read_assembly_merge_analysis", "AssemblyRevisionV6", "AssemblyAuthorV6", "AssemblyMergeAuthor",
            "ValidatedAssemblyRevisionV6", "assembly_merge_schema_data")


from .graph_merge import (
    GraphMergeNetRevision, ValidatedGraphMergeRevision, GraphMergeAnalyzer,
    ValidatedGraphMergeAnalysis, read_graph_merge_analysis,
)
from .graph_merge_author import GraphMergeAuthor
from ._graph_merge_model import UnresolvedGraphMerge
from .branches import GraphMergeBranchVersion
from .schema_catalog import graph_merge_schema_data, graph_merge_assembly_schema_data
from .assembly_v8 import AssemblyAuthorV8, AssemblyMemberV8, AssemblyRevisionV8, ValidatedAssemblyRevisionV8

__all__ += ("GraphMergeNetRevision", "ValidatedGraphMergeRevision", "GraphMergeAnalyzer",
    "ValidatedGraphMergeAnalysis", "read_graph_merge_analysis", "GraphMergeAuthor", "UnresolvedGraphMerge",
    "GraphMergeBranchVersion", "graph_merge_schema_data", "graph_merge_assembly_schema_data",
    "AssemblyAuthorV8", "AssemblyMemberV8", "AssemblyRevisionV8", "ValidatedAssemblyRevisionV8")


from .assembly_v9 import (AssemblyMemberV9, AssemblyRevisionV9, AssemblyAuthorV9,
                         ValidatedAssemblyRevisionV9, validate_generated_assembly_v9)
from .schema_catalog import typed_assembly_schema_data

__all__ += ("AssemblyMemberV9", "AssemblyRevisionV9", "AssemblyAuthorV9", "ValidatedAssemblyRevisionV9",
            "validate_generated_assembly_v9", "typed_assembly_schema_data")

from .schema_catalog import candidate_schema_data, candidate_v2_schema_data
__all__ += ("candidate_schema_data", "candidate_v2_schema_data")

# New optional surfaces stay lazy: importing collaboration must not open a
# Registry, probe an environment, discover plugins, or initialize a HOST.
_OPTIONAL_PUBLIC_EXPORTS = {
    **{name: '.environment_contracts' for name in (
        'EnvironmentContractError', 'PackageTarget')},
    **{name: '.environment_requirements' for name in (
        'EnvironmentRequirements', 'ScopedEnvironmentRequirements', 'PackageEnvironment',
        'EnvironmentRequirementsSet', 'read_environment_requirements', 'read_package_environment')},
    **{name: '.environment_local_contracts' for name in (
        'EnvironmentSelection', 'LocalEnvironmentBinding', 'EnvironmentResolutionLock',
        'EnvironmentCheckReport', 'EnvironmentPreparationPlan', 'PreparationReceipt')},
    **{name: '.environment_check' for name in (
        'ProbePolicy', 'check_environment')},
    **{name: '.environment_plan' for name in (
        'ConcreteSelections', 'resolve_local_wheels', 'plan_environment')},
    **{name: '.environment_prepare' for name in (
        'PreparationExecutionContext', 'PreparedEnvironmentResult',
        'LaunchExecutionContext', 'prepare_environment')},
    **{name: '.environment_host' for name in (
        'HostProfile', 'ExistingRunHandle', 'launch_package')},
    'render_environment_setup': '.environment_setup',
    **{name: '.registry_read_contracts' for name in (
        'RegistryReadSessionError', 'ReadLimits', 'SourceSelection', 'ExplicitSources',
        'SelectedSourceSet', 'ReadSessionRequest', 'PublicRegistryHead', 'SourceCut',
        'HistoricalCutRequest', 'TypedPredicate', 'TypedIndexClause', 'IndexQuery',
        'PRODUCT_ORIGIN_PROFILE', 'PRODUCT_ORIGIN_PAGE_SCHEMA', 'PRODUCT_ORIGIN_CONTRACT_REVISION')},
    **{name: '.registry_read_session' for name in (
        'RegistryReadHostBinding', 'RegistryReadSession', 'ExistingReadAuthorityProvider',
        'ResolvedReadSource', 'open_readonly_source', 'open_registry_session',
        'query_index', 'query_product_origin_v1', 'read_exact', 'read_material')},
    **{name: '.registry_typed_readers' for name in (
        'TypedReaderCatalog', 'TypedReadError', 'DEFAULT_TYPED_READER_CATALOG')},
    **{name: '.subnet_exchange' for name in (
        'SubnetExchangeError', 'SubnetExportPlan', 'SubnetExportResult',
        'SubnetImportPlan', 'SubnetImportResult', 'TargetRegistrySelection',
        'plan_subnet_export', 'export_subnet', 'plan_subnet_import', 'import_subnet')},
    **{name: '.read_host_config' for name in (
        'ReadHostConfiguration', 'load_read_host_config', 'open_read_host_session')},
    'registry_read_schema_data': '.schema_catalog',
}
__all__ += tuple(_OPTIONAL_PUBLIC_EXPORTS)


def __getattr__(name):
    module = _OPTIONAL_PUBLIC_EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    from importlib import import_module
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
