---
name: rpnh-api-index
description: "Find exported Python facades, explicit module APIs, protocol contracts and source definitions."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: api-index_ZH.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](api-index.md) | [中文](api-index_ZH.md)

# API reference map

[Documentation home](../index.md) · [CLI reference](cli.md) · [Source map](../guides/repository-layout.md)

Start with a task in the table below, then follow its contract and source.
This page inventories the exported Python facades and the documented explicit
module interfaces in public source `b16ecce8`. It is a navigation reference,
not a new SDK-stability promise. Exact schemas, required arguments, errors,
authority and effects live in the linked contracts and implementations.

## Choose an integration surface

| Build or inspect | Interface and contract | Source |
|---|---|---|
| A typed workflow declaration | `ModuleDeclaration`, `Registration`, `lower_module`; [declarations](declarations.md) | [Public facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py), [compiler](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/compiler.py) |
| Definition extraction, composition or replacement | `extract_module`, `compose_modules`, `instantiate_module`, `branch_module`, replacement/reentry plans; [net operations](../guides/net-operations.md) | [Definition transforms](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/definitions.py), [owner operations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/runtime.py) |
| Owner-driven execution and recovery | `start_run`, `RunOwner`, `resume_run`, `OwnerInput`; [runtime and Registry](runtime-registry.md) | [Run owner](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py), [runtime protocols](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_runtime_interfaces.py) |
| Agent sessions and workflow nodes | `MainSession`, `TaskControl`, `AgentStage`, `AgentTaskSpec`, `AgentWorkflowGraph`; [agents](agents.md) | [Sessions](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/main_session.py), [task control](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/task_control.py), [task specs](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_tasks.py), [graphs](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/agent_workflows.py) |
| An installed native tool or instruction resource | `PluginDefinition`, `PluginOperation`, `PluginResource`, `PluginCatalog`; [plugin SDK](extensions-observation.md), [tutorial](../guides/customization.md) | [Plugin facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/__init__.py), [declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py), [catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py) |
| Node-scoped managed tools, batches or programs | Managed plugin catalog and invocation service; [managed tools](../controlled-managed-tools.md) | [Managed tools](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_tools.py), [scheduler](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_scheduler.py), [program execution](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/components/agent_loop/program_execution.py) |
| Authorized independent Registry reads | `ReadSessionRequest`, `RegistryReadHostBinding`, `open_registry_session`, `query_index`, `read_exact`, `read_material`; [read contract](registry-read-sessions.md), [reader guide](../guides/independent-registry-reader.md) | [Request types](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_contracts.py), [session](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py) |
| Exact source queries and producer lineage | `SourceSetQuery`, qualified references, `query_product_origin_v1`; [SourceSets](../guides/source-queries.md), [origin query](registry-read-sessions.md#fixed-product-origin-query) | [Source queries](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_query.py), [typed projections](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/public_projections.py) |
| Author versions, Branches, merges and Assemblies | `cpn.rpnh.collaboration` author families; [version-operation map](#authoring-and-version-contracts) | [Collaboration facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/__init__.py) |
| Controlled participants and complete project reuse | `UserCollaborationHost`, local project requests; [collaboration](../guides/user-collaboration.md), [project operations](../guides/project-entry.md) | [Participant HOST](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/user_collaboration.py), [project endpoint](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py) |
| Definition exchange and package preparation | Subnet plan/export/import; package preview/resolve; environment check/plan/prepare/launch | [Exchange](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py), [package guide](../guides/portable-packages.md), [typed exchange](../guides/typed-registry-exchange.md), [environment guide](../environment-preparation.md) |
| Finite PN analysis and witnesses | `build_analysis_input`, `analyze`, `explore`, report verification; [PN validation](pn-validation.md) | [Analysis facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/__init__.py) |
| Viewer, checkpoints and cross-net comparison | `project_compiled_net`, read-only projection providers; [observation](extensions-observation.md), [comparison](../guides/comparison-context.md) | [Projection](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/inspection.py), [checkpoint projection](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/checkpoint_view.py), [comparison context](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/comparison_context.py) |
| A frontend or LLM host adapter | Shared session/application service and registered ports; [adapters](../guides/adapters.md), [history](main-thread-history.md), [Codex history](codex-history.md) | [Application service](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/frontend_application.py), [AgentLoop ports](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/components/agent_loop/ports.py), [registered LLM contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/llm_contracts.py) |

## Core call boundaries

| Entry | Inputs and result | Effects / follow-up |
|---|---|---|
| `ModuleDeclaration.from_dict(document)` / `.from_json(document)` | Strict declaration data → `ModuleDeclaration` | Parse/validate only; see `DeclarationError` and field contract in [declarations](declarations.md) |
| `lower_module(module, registration)` | Actual declaration + trusted bindings → `SymbolicNet` | Runs trusted registered lowering callbacks; does not publish a run |
| `compile_module(module, registration)` | Explicit import from `cpn.rpnh.compiler` → `CompiledPetriNet` | Checks the lowered candidate; compilation is not owner adoption or execution |
| `start_run(module, registration, **owner_arguments)` | Public facade delegates to the explicit typed [run signature](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/run.py#L510) → `RunOwner` | Creates fresh Registry/owner lineage; requires owner inputs, budgets, identity, run directory and command ID; no default business execution |
| `snapshot(owner_or_client)` | Calls the existing owner/client snapshot | Reads that channel's exact state without creating another writer |
| `apply_replacement(owner, plan, *, command_id)` | Exact prepared replacement → existing owner edit path | May drain/adopt a successor; use [quiescent replacement rules](../guides/net-operations.md) |

Use the complete parse-only example in [declarations](declarations.md#example-validate-a-supplied-declaration-without-starting-a-run)
and the runnable [native-plugin tutorial](../guides/customization.md).
A guessed empty Registration cannot bind an arbitrary workflow. Registry
records and PN structure jointly govern identity, admission and settlement;
frontend display state is not an alternate execution authority.

Business PN models agent work and handoffs. Subordinate execution PNs implement
harness mechanisms under exact business invocations/firings in the same
Registry, with separate marking/checkpoint evidence. See
[runtime](runtime-registry.md) and [architecture](../ARCHITECTURE.md).

## Authoring and version contracts

Choose the contract matching the material you have; author publication,
protected Branch acceptance and runtime adoption are distinct operations.

| Material / action | Contract |
|---|---|
| Source-qualified refs, immutable revisions, closed Modules, Branches, Assembly v1/v2 | [Declarations](declarations.md) |
| Graph source authoring and merge | [Graph authoring](graph-authoring.md), [graph-source merge](graph-source-merge.md) |
| Ordinary Module conflict analysis and resolved result | [Plain merge analysis](plain-merge-analysis.md), [merge result](plain-merge-result.md) |
| Composition and nested Assemblies | [Merge composition](plain-merge-composition.md), [nested merge](plain-merge-nested-assembly.md), [full-history Assembly merge](assembly-full-history-merge.md) |
| Open-region adaptation and ordinary descendants | [Open-region authoring](open-region-authoring.md), [descendants](open-region-descendants.md) |
| Selective transplant and descendants | [Plain transplant](plain-transplant.md), [transplant descendants](plain-transplant-descendants.md) |
| Explicit split/fusion identity transform | [Identity transform](author-identity-transform-contract.md) |
| Finite typed Assembly v9 | [Declarations](declarations.md#finite-typed-members-in-assembly-v9) |
| Workset delivery and normal-child closure | [Worksets](worksets.md), [normal-child root contract](normal-child-root-contract.md) |
| Fixed-cut queries, leases and material reads | [Read sessions](registry-read-sessions.md), [static leases](../guides/static-lease-reads.md), [typed exchange](../guides/typed-registry-exchange.md) |

`restore-content` creates a successor with selected prior content. Task
checkpoint `reopen` creates an execution generation. Package import creates
local identities with provenance. These are different from Git checkout or
rewriting Registry history; their respective guides specify the supported
scope and required authority.

## Frontend and host protocols

- Basic, Codex and OpenCode use the canonical main-session/application path;
  [source ownership](../guides/repository-layout.md#frontend-source-paths) locates each implementation.
- [Codex compatibility](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/codex_compatibility.v1.json) and
  [OpenCode compatibility](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_compatibility.v1.json)
  describe the pinned presentation contracts. [OpenCode HTTP](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_http.py)
  and [protocol](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/frontend/opencode_protocol.py) implement that adapter,
  not a general authenticated public server API.
- DSH uses the [Python backend](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/backend.py) and
  [bundled upper-layer app](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/integrations/dsh/src/app.ts), with explicit
  configured or offline mode. See [DSH contracts](extensions-observation.md).
- The controlled project endpoint has strict bounded JSON request fields,
  trusted local route identities and operation-specific permissions;
  [project-entry](../guides/project-entry.md) is its API contract.
- The [0.161 Codex candidate](../CODEX_0161_CANDIDATE.md) is explicitly separate
  from the default pinned Codex presentation. Presence of an adapter module is
  not evidence of compatibility with every upstream release.

## Exported Python symbols

The following index is grouped by facade and defining module. Names are read
from each facade's explicit `__all__` and lazy-export declarations without
importing runtime code. A module link opens the exact definitions at this
page's source baseline. Use the task/contract tables above for semantics,
required fields and effect boundaries. Symbols intentionally not exported by a
facade may still have a documented explicit import path, as listed above.

### cpn.rpnh

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py) · 49 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.rpnh.registration](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registration.py) | `Registration`, `RegistrationError` |
| [cpn.rpnh.module](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/module.py) | `ModuleDeclaration`, `SymbolicNet` |
| [cpn.rpnh.control_ir](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/control_ir.py) | `ControlIR`, `ControlIRError`, `ControlIRCapabilityError` |
| [cpn.rpnh.control_eval](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/control_eval.py) | `EvalError`, `EvalResult`, `ReadSet`, `evaluate_expression` |
| [cpn.rpnh.petri_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_contracts.py) | `DeclarationError`, `PNFragment` |
| [cpn.rpnh](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/__init__.py) | `lower_module`, `start_run`, `snapshot` |
| [cpn.rpnh.petri_primitives](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/petri_primitives.py) | `PetriStructureDelta`, `PetriStructureDeltaError`, `PetriStructureEdit`, `PetriStructureSnapshot`, `apply_petri_structure_delta`, `derive_petri_structure_delta`, `verify_petri_structure_delta` |
| [cpn.rpnh.marking](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/marking.py) | `PetriFiringResourceAccess`, `PetriMarkingDelta`, `PetriTokenEdit`, `apply_petri_marking_delta`, `derive_petri_marking_delta`, `verify_petri_marking_delta` |
| [cpn.rpnh.firing_resource_access](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/firing_resource_access.py) | `RegisteredPetriFiringResourceAccess`, `derive_registered_firing_resource_access`, `verify_registered_firing_resource_access_conflicts` |
| [cpn.rpnh.net_operations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) | `BranchResult`, `ComposeConnection`, `ComposePlan`, `ExtractPlan`, `ExtractResult`, `ReplacementPlan`, `ReentryPlan`, `WorkspaceBindingPlan`, `WorkspaceImportPlan`, `apply_replacement`, `branch_module`, `compose_modules`, `extract_module`, `instantiate_module`, `prepare_reentry`, `prepare_replacement`, `register_net_components` |

### cpn.rpnh.net_operations

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) · 17 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.rpnh.net_operations.definitions](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/definitions.py) | `BranchResult`, `ComposeConnection`, `ComposePlan`, `ExtractPlan`, `ExtractResult`, `branch_module`, `compose_modules`, `extract_module`, `instantiate_module` |
| [cpn.rpnh.net_operations.runtime](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/runtime.py) | `ReplacementPlan`, `ReentryPlan`, `WorkspaceBindingPlan`, `WorkspaceImportPlan`, `apply_replacement`, `prepare_reentry`, `prepare_replacement` |
| [cpn.rpnh.net_operations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/net_operations/__init__.py) | `register_net_components` |

### cpn.plugins

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/__init__.py) · 19 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.plugins.api](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/api.py) | `API_VERSION`, `PluginContext`, `PluginDefinition`, `PluginError`, `PluginOperation`, `PluginResource`, `ResourceView` |
| [cpn.plugins.catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/catalog.py) | `BoundPlugin`, `PluginCatalog`, `load_catalog` |
| [cpn.plugins.managed_tools](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/managed_tools.py) | `ManagedPluginToolAdapter`, `ManagedPluginToolCatalog`, `ManagedPluginInvocationService`, `ManagedPluginInvocationConflict`, `ManagedPluginInvocationFailed`, `ManagedPluginInvocationReconciliationRequired`, `ManagedToolDeclaration`, `ManagedToolSelector`, `build_managed_plugin_tool_catalog` |

### cpn.rpnh.registry

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/__init__.py) · 7 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.rpnh.registry.identities](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/identities.py) | `TypedId` |
| [cpn.rpnh.registry.models](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/models.py) | `VersionRef` |
| [cpn.rpnh.registry.observer_access](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/registry/observer_access.py) | `ObserverReadScope`, `RegistryReadObserverContext`, `issue_observer_access`, `revoke_observer_access`, `observer_access_schema_data` |

### cpn.rpnh.collaboration

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/__init__.py) · 223 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.rpnh.collaboration.assembly_v2](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v2.py) | `AssemblyAuthorV2`, `ValidatedAssemblyRevisionV2`, `AssemblyMemberV2`, `AssemblyRevisionV2` |
| [cpn.rpnh.collaboration.schema_catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/schema_catalog.py) | `graph_assembly_schema_data`, `collaboration_schema_data`, `authoring_schema_data`, `source_identity_schema_data`, `branch_schema_data`, `author_material_schema_data`, `assembly_schema_data`, `graph_author_schema_data`, `graph_branch_schema_data`, `plain_merge_schema_data`, `plain_merge_result_schema_data`, `plain_merge_assembly_schema_data`, `nested_assembly_schema_data`, `plain_merge_nested_assembly_schema_data`, `open_region_schema_data`, `open_region_derived_schema_data`, `open_region_derived_assembly_schema_data`, `open_region_closure_schema_data`, `open_region_assembly_schema_data`, `source_set_schema_data`, `source_observation_schema_data`, `workset_schema_data`, `normal_child_root_schema_data`, `plain_transplant_schema_data`, `plain_transplant_assembly_schema_data`, `plain_transform_schema_data`, `plain_transform_assembly_schema_data`, `plain_transplant_derived_schema_data`, `plain_transplant_derived_assembly_schema_data`, `assembly_merge_schema_data`, `graph_merge_schema_data`, `graph_merge_assembly_schema_data`, `typed_assembly_schema_data`, `candidate_schema_data`, `candidate_v2_schema_data`, `registry_read_schema_data` |
| [cpn.rpnh.collaboration.references](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/references.py) | `SourceQualifiedResourceRef`, `SourceQualifiedVersionRef` |
| [cpn.rpnh.collaboration.authoring](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/authoring.py) | `NetRevision`, `AuthorRevisionReadError`, `read_net_revision` |
| [cpn.rpnh.collaboration.sources](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/sources.py) | `LocalSourceIdentity`, `get_local_source_identity` |
| [cpn.rpnh.collaboration.branches](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/branches.py) | `BranchVersion`, `GraphBranchVersion`, `current_branch`, `read_branch_version`, `GraphMergeBranchVersion` |
| [cpn.rpnh.collaboration.materials](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/materials.py) | `ClosedModuleAuthor`, `ValidatedClosedRevision`, `validate_closed_revision` |
| [cpn.rpnh.collaboration.assemblies](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assemblies.py) | `AssemblyMember`, `AssemblyConnection`, `AssemblyCompletion`, `AssemblyRevision`, `AssemblyAuthor`, `ValidatedAssemblyRevision`, `read_assembly_revision`, `validate_assembly_revision` |
| [cpn.rpnh.collaboration.graph_authoring](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_authoring.py) | `GraphModuleAuthor`, `GraphNetRevision`, `ValidatedGraphRevision` |
| [cpn.rpnh.collaboration.graph_source](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_source.py) | `make_graph_source`, `make_graph_recipe`, `graph_source_elements`, `rebuild_graph_module` |
| [cpn.rpnh.collaboration.plain_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_merge.py) | `PlainModuleMergeAnalyzer`, `ValidatedPlainMergeAnalysis`, `read_plain_merge_analysis` |
| [cpn.rpnh.collaboration.plain_merge_result](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_merge_result.py) | `PlainModuleMergeAuthor`, `ValidatedPlainMergeRevision` |
| [cpn.rpnh.collaboration._plain_merge_resolution](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_plain_merge_resolution.py) | `UnresolvedPlainMerge` |
| [cpn.rpnh.collaboration.assembly_v3](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v3.py) | `AssemblyMemberV3`, `AssemblyRevisionV3`, `AssemblyAuthorV3`, `ValidatedAssemblyRevisionV3` |
| [cpn.rpnh.collaboration.open_region](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region.py) | `OpenRegionAuthor`, `ValidatedOpenRegion`, `validate_open_revision` |
| [cpn.rpnh.collaboration.open_region_derived](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region_derived.py) | `OpenRegionDerivedAuthor`, `ValidatedOpenDerivedRevision` |
| [cpn.rpnh.collaboration.assembly_v5](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v5.py) | `AssemblyMemberV5`, `AssemblyRevisionV5`, `AssemblyAuthorV5`, `ValidatedAssemblyRevisionV5` |
| [cpn.rpnh.collaboration.open_region_closure](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/open_region_closure.py) | `OpenRegionClosureAuthor`, `ValidatedAdaptedRevision` |
| [cpn.rpnh.collaboration.assembly_v4](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v4.py) | `AssemblyMemberV4`, `AssemblyRevisionV4`, `AssemblyAuthorV4`, `ValidatedAssemblyRevisionV4` |
| [cpn.rpnh.collaboration.source_sets](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_sets.py) | `SourceMember`, `SourceSetVersion`, `read_source_set`, `current_source_set` |
| [cpn.rpnh.collaboration.source_query](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/source_query.py) | `SourceSetQuery`, `RegistrySourceReader`, `SourceUnavailable`, `SourceNotEstablished`, `SourceObservationDraft` |
| [cpn.rpnh.collaboration.worksets](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/worksets.py) | `WorksetOwner`, `WorksetExpectation`, `ExportResult`, `AcceptDelivery`, `Contribute`, `CompleteWorkset`, `bind_local_workset_source`, `prepare_local_delivery`, `finish_local_delivery`, `read_record`, `CompleteWorksetNormalChildren` |
| [cpn.rpnh.collaboration.root_terminals](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/root_terminals.py) | `read_root_terminal` |
| [cpn.rpnh.collaboration.plain_transplant](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant.py) | `PlainModuleTransplantAnalyzer`, `ValidatedPlainTransplantAnalysis`, `read_plain_transplant_analysis` |
| [cpn.rpnh.collaboration.plain_transplant_result](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant_result.py) | `PlainModuleTransplantAuthor`, `ValidatedPlainTransplantRevision` |
| [cpn.rpnh.collaboration._plain_transplant_resolution](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_plain_transplant_resolution.py) | `UnresolvedPlainTransplant` |
| [cpn.rpnh.collaboration.plain_transform](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transform.py) | `PlainModuleTransformAuthor`, `ValidatedPlainTransformRevision` |
| [cpn.rpnh.collaboration.plain_transplant_derived](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/plain_transplant_derived.py) | `PlainTransplantDerivedAuthor`, `ValidatedPlainTransplantDerivedRevision` |
| [cpn.rpnh.collaboration.assembly_v7](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v7.py) | `AssemblyMemberV7`, `AssemblyRevisionV7`, `AssemblyAuthorV7`, `ValidatedAssemblyRevisionV7` |
| [cpn.rpnh.collaboration.assembly_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_merge.py) | `AssemblyMergeAnalyzer`, `ValidatedAssemblyMergeAnalysis`, `UnresolvedAssemblyMerge`, `read_assembly_merge_analysis` |
| [cpn.rpnh.collaboration.assembly_v6](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v6.py) | `AssemblyRevisionV6`, `AssemblyAuthorV6`, `AssemblyMergeAuthor`, `ValidatedAssemblyRevisionV6` |
| [cpn.rpnh.collaboration.graph_merge](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_merge.py) | `GraphMergeNetRevision`, `ValidatedGraphMergeRevision`, `GraphMergeAnalyzer`, `ValidatedGraphMergeAnalysis`, `read_graph_merge_analysis` |
| [cpn.rpnh.collaboration.graph_merge_author](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/graph_merge_author.py) | `GraphMergeAuthor` |
| [cpn.rpnh.collaboration._graph_merge_model](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/_graph_merge_model.py) | `UnresolvedGraphMerge` |
| [cpn.rpnh.collaboration.assembly_v8](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v8.py) | `AssemblyAuthorV8`, `AssemblyMemberV8`, `AssemblyRevisionV8`, `ValidatedAssemblyRevisionV8` |
| [cpn.rpnh.collaboration.assembly_v9](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/assembly_v9.py) | `AssemblyMemberV9`, `AssemblyRevisionV9`, `AssemblyAuthorV9`, `ValidatedAssemblyRevisionV9`, `validate_generated_assembly_v9` |
| [cpn.rpnh.collaboration.environment_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_contracts.py) | `EnvironmentContractError`, `PackageTarget` |
| [cpn.rpnh.collaboration.environment_requirements](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_requirements.py) | `EnvironmentRequirements`, `ScopedEnvironmentRequirements`, `PackageEnvironment`, `EnvironmentRequirementsSet`, `read_environment_requirements`, `read_package_environment` |
| [cpn.rpnh.collaboration.environment_local_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_local_contracts.py) | `EnvironmentSelection`, `LocalEnvironmentBinding`, `EnvironmentResolutionLock`, `EnvironmentCheckReport`, `EnvironmentPreparationPlan`, `PreparationReceipt` |
| [cpn.rpnh.collaboration.environment_check](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_check.py) | `ProbePolicy`, `check_environment` |
| [cpn.rpnh.collaboration.environment_plan](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_plan.py) | `ConcreteSelections`, `resolve_local_wheels`, `plan_environment` |
| [cpn.rpnh.collaboration.environment_prepare](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_prepare.py) | `PreparationExecutionContext`, `PreparedEnvironmentResult`, `LaunchExecutionContext`, `prepare_environment` |
| [cpn.rpnh.collaboration.environment_host](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_host.py) | `HostProfile`, `ExistingRunHandle`, `launch_package` |
| [cpn.rpnh.collaboration.environment_setup](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_setup.py) | `render_environment_setup` |
| [cpn.rpnh.collaboration.registry_read_contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_contracts.py) | `RegistryReadSessionError`, `ReadLimits`, `ReadLimitOverrides`, `SourceSelection`, `ExplicitSources`, `SelectedSourceSet`, `ReadSessionRequest`, `PublicRegistryHead`, `SourceCut`, `HistoricalCutRequest`, `TypedPredicate`, `TypedIndexClause`, `IndexQuery`, `PRODUCT_ORIGIN_PROFILE`, `PRODUCT_ORIGIN_PAGE_SCHEMA`, `PRODUCT_ORIGIN_CONTRACT_REVISION` |
| [cpn.rpnh.collaboration.registry_read_session](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_read_session.py) | `RegistryReadHostBinding`, `RegistryReadSession`, `ExistingReadAuthorityProvider`, `ResolvedReadSource`, `open_readonly_source`, `open_registry_session`, `query_index`, `query_product_origin_v1`, `read_exact`, `read_material` |
| [cpn.rpnh.collaboration.registry_typed_readers](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/registry_typed_readers.py) | `TypedReaderCatalog`, `TypedReadError`, `DEFAULT_TYPED_READER_CATALOG` |
| [cpn.rpnh.collaboration.subnet_exchange](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/subnet_exchange.py) | `SubnetExchangeError`, `SubnetExportPlan`, `SubnetExportResult`, `SubnetImportPlan`, `SubnetImportResult`, `TargetRegistrySelection`, `plan_subnet_export`, `export_subnet`, `plan_subnet_import`, `import_subnet` |
| [cpn.rpnh.collaboration.read_host_config](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/read_host_config.py) | `ReadHostConfiguration`, `load_read_host_config`, `open_read_host_session` |

### cpn.rpnh.pn_validation

[Export declarations](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/__init__.py) · 35 exported names.

| Defining module | Exported symbols |
|---|---|
| [cpn.rpnh.pn_validation.contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/contracts.py) | `ActionBinding`, `ActionEvidence`, `ActiveOccurrence`, `AnalysisInput`, `AnalysisPolicy`, `AnalysisState`, `BindingEnumeration`, `EnvironmentContract`, `OperationCase`, `OperationModel`, `ProducedSpec`, `PropertyResult`, `SchedulerContract`, `SupportResult`, `TerminalContract`, `canonical_data`, `canonical_json`, `state_key` |
| [cpn.rpnh.pn_validation.evidence](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/evidence.py) | `AnalysisReport`, `ReplayResult`, `analyze`, `input_digest`, `replay_witness`, `verify_graph`, `verify_report_binding`, `verify_report` |
| [cpn.rpnh.pn_validation.explorer](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/explorer.py) | `ExplorationGraph`, `Frontier`, `GraphEdge`, `GraphNode`, `explore` |
| [cpn.rpnh.pn_validation.properties](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/properties.py) | `TerminalClassification`, `classify_terminal`, `evaluate_properties` |
| [cpn.rpnh.pn_validation.projection](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/pn_validation/projection.py) | `build_analysis_input` |

## Explicit module APIs and implementation internals

`UserCollaborationHost` is defined in `cpn.rpnh.collaboration.user_collaboration`; the collaboration facade also imports it, but it is not listed in that facade's `__all__`. Complete-project, share-package, HOST-readiness, AgentLoop-port, runtime-owner and Viewer APIs use the explicit module/contract links above. A non-underscore filename alone does not make a stable SDK.

The [LLM adapter facade](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/llm_adapters/__init__.py) is explicitly adapter-private composition. Its execution-selection and transport factories are for trusted adapter integration, not a bypass around registered requests. Private Registry submodules such as `_event_store`, `_invocation` and `_provider_calls` implement validation and transactions; they are not direct plugin write interfaces. See [extension contracts](extensions-observation.md) and the [source map](../guides/repository-layout.md).

Schema versions are data contracts, distinct from the Python package version. Preserve evidence on exact-reference/schema mismatch and use [troubleshooting](../guides/troubleshooting.md); do not change version strings to force compatibility. See the [changelog](../../CHANGELOG.md) and [development guide](../guides/development.md) for changes and contribution checks.
