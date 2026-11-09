---
name: rpnh-documentation-index
description: "Choose a first-run route, operational guide, API contract or source-code entry point."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: en
  counterpart: index_ZH.md
  revision: "2026-11-09.1"
  status: source-reviewed
---

[English](index.md) | [中文](index_ZH.md)

# Documentation

[Repository home](../README.md) · [Technical report](technical-report.md) ·
[Examples](../examples/README.md) ·
[CLI](reference/cli.md) · [API](reference/api-index.md) · [Source map](guides/repository-layout.md)

RPNH uses Registry and PetriNet to declare, execute, inspect and reuse agent work.
Start with installation and a small example, then choose a guide for your task.
See [compatibility](guides/compatibility.md) for exact client versions and
[releases](guides/releases.md) for the difference between current source and
published packages.

## New to RPNH?

1. [First run](guides/getting-started.md): install one source, run a no-model
   native operation, inspect its actual evidence, and optionally open a model session.
2. [Choose an example](../examples/README.md): find an editable workflow, a
   complete collaboration project or a bounded benchmark by task and prerequisites.
3. [Operate and recover](guides/usage.md): keep session/task identity, inspect
   results, stop safely and continue from an exact checkpoint.

For a conceptual introduction before running anything, read the
[technical report](technical-report.md). For Python integration, start with the
[API map](reference/api-index.md) and [declaration tutorial](reference/declarations.md).

## Install and configure

| Need | Guide |
|---|---|
| Install an approved source or wheel; choose a platform and artifact | [Installation and distribution scope](guides/installation.md) |
| Set a provider, exact model and credentials environment | [Model setup](guides/models.md), [provider catalog fields](PROVIDER_MODEL_CONFIGURATION.md) |
| Understand profile precedence, limits and runtime policy | [Configuration reference](guides/configuration.md) |
| Choose Basic, Codex, OpenCode or official DSH Web; save a frontend preference | [Adapters and coexistence](guides/adapters.md), [OpenCode](guides/opencode.md), [DSH](guides/dsh.md) |
| Prepare a receiver for a dependency-complete package | [Package environments](guides/package-environments.md), [check/plan/prepare flow](environment-preparation.md) |

## Run, inspect and recover

| Need | Guide |
|---|---|
| Start sessions, launch tasks and target workflow messages | [Usage](guides/usage.md), [CLI reference](reference/cli.md) |
| Continue the same task or select an older checkpoint | [Checkpoint recovery](guides/checkpoint-recovery.md) |
| Validate firing products or inspect original owner edit receipts | [Firing content contracts and owner receipts](guides/firing-content-contract.md) |
| Diagnose missing configuration, owner conflicts or unknown effects | [Troubleshooting and safe recovery](guides/troubleshooting.md) |
| Inspect a full PN, agent Overview, firing, resource or checkpoint | [Viewer](guides/viewer.md), [display/evidence semantics](DISPLAY_OBSERVATION.md) |
| Compare checkpoints in one Registry or exact nets across sources | [Checkpoint comparison](guides/checkpoint-comparison.md), [cross-net comparison](guides/comparison-context.md) |
| Inspect actual controlled-user contributions | [Viewer contributions](guides/viewer-user-contributions.md) |
| Check declaration compatibility with an already prepared HOST | [HOST readiness](guides/host-readiness.md) |
| Read local Basic and Viewer help | [Offline frontend help](guides/frontend-help.md) |

## Build, collaborate and reuse

| Need | Guide |
|---|---|
| Edit a shipped input, graph or plugin | [Examples and copy-and-change instructions](guides/examples.md), [customization](guides/customization.md) |
| Copy, co-create, merge, adopt and independently run a complete agent project | [Complete project example](../examples/project_collaboration/README.md), [project operator/API guide](guides/project-entry.md) |
| Coordinate independent roles through selected versioned resources | [Multi-user release handoff](../examples/multiuser_release_handoff/README.md) |
| Understand controlled participants, scoped permissions and protected acceptance | [User collaboration](guides/user-collaboration.md) |
| Extract, compose, instantiate, branch or replace definitions | [Native net operations](guides/net-operations.md) |
| Read public projections and exchange a closed definition | [Typed Registry exchange](guides/typed-registry-exchange.md) |
| Preview/resolve a portable package and run it on a prepared receiver | [Portable packages](guides/portable-packages.md), [native-add package tutorial](guides/package-reuse-example.md) |
| Integrate selected tools, parallel managed calls or isolated programs | [Controlled managed tools](controlled-managed-tools.md), [plugin contracts](reference/extensions-observation.md) |

The complete-project example first demonstrates controlled definition
contributions, explicit conflict resolution, Branch acceptance and owner
adoption. Implementer/reviewer agents then execute the business task, which is
also reused by an independent receiver. Exact results, limits and original-task
verification remain on the example page. Historical
[agent-network co-building](../examples/user_collaboration/agent_cobuild/README.md)
is a separate deterministic protocol example.

## Query and understand the system

| Need | Guide |
|---|---|
| Open an independently authorized typed reader | [Independent Registry reader](guides/independent-registry-reader.md) |
| Query fixed source scopes and read exact references | [SourceSet queries](guides/source-queries.md), [Registry read sessions](reference/registry-read-sessions.md) |
| Trace an exact product to a verified producer | [Bounded product-origin query](reference/registry-read-sessions.md#fixed-product-origin-query) |
| Understand Registry and the two PN layers | [Harness architecture](ARCHITECTURE.md), [closed execution design](architecture/design.md) |
| Locate declarations, runtime, AgentLoop and extension contracts | [API reference map](reference/api-index.md), [source map](guides/repository-layout.md) |
| Analyze a supported finite PN and interpret HOLDS / VIOLATED / UNKNOWN | [Finite PN validation](reference/pn-validation.md) |
| Compare RPNH authority with presentation integrations | [RPNH versus Codex](RPNH_VS_CODEX.md) |

## Maintain, contribute and track versions

- [Development](guides/development.md): deterministic checks, package validation and contributor workflow
- [Changelog](../CHANGELOG.md): changes by version/source; [installation](guides/installation.md) explains source candidates versus older releases
- [Contributing](../CONTRIBUTING.md), [security](../SECURITY.md), [license](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md)
- [Releases](guides/releases.md) and [compatibility](guides/compatibility.md): package choices, supported clients and limitations
- [Benchmark results](results/README.md): published application results with their original conditions

## Reading the reference

Use [CLI reference](reference/cli.md) for operator commands and
[API reference](reference/api-index.md) for exported symbols, explicit module
interfaces, protocol entry points and source definitions. The detailed
[declaration](reference/declarations.md), [runtime/Registry](reference/runtime-registry.md),
[agent/model](reference/agent-runtime.md) and [extension/observation](reference/extensions-observation.md)
pages describe arguments, identity, effects and failure boundaries.

Public facades, trusted HOST interfaces and private implementation modules are
separate. A documented import path or schema does not create execution
permission or promise compatibility with every later release. Use the source
revision matching your installed artifact.

## Integrated explicit contracts

Advanced authoring is grouped in the [API version-operation map](reference/api-index.md#authoring-and-version-contracts):

- [Graph authoring](reference/graph-authoring.md), [graph-source merge](reference/graph-source-merge.md), [identity transforms](reference/author-identity-transform-contract.md)
- [Plain merge analysis](reference/plain-merge-analysis.md), [resolved results](reference/plain-merge-result.md), [composition](reference/plain-merge-composition.md), [nested Assemblies](reference/plain-merge-nested-assembly.md), [full-history Assembly merge](reference/assembly-full-history-merge.md)
- [Open-region authoring](reference/open-region-authoring.md), [open-region descendants](reference/open-region-descendants.md), [selective transplant](reference/plain-transplant.md), [transplant descendants](reference/plain-transplant-descendants.md)
- [Worksets](reference/worksets.md), [normal-child root closure](reference/normal-child-root-contract.md)

## Exact reads and frontend protocol

[Read HOST group configuration](read-host-hierarchy-v2.md)

- [Static resource-lease reads](guides/static-lease-reads.md)
- [Main-thread history at a fixed cut](reference/main-thread-history.md)
- [Codex history projection](reference/codex-history.md)
- [Codex 0.166 candidate](guides/codex-candidate-0166.md)
- [OpenCode 2.2.5 protocol subset](reference/opencode-v2-candidate.md)

## More examples

[Mock refund approval](../examples/approval_workflow/README.md) ·
[External-result quote](../examples/a2a_quote/README.md) ·
[Published benchmark results](results/README.md)

Example results belong to the source versions and conditions stated on their
pages. They do not certify every combination of current source and providers.

### Multi-user release handoff: independent agents and shared resources

[Complete example](../examples/multiuser_release_handoff/README.md) ·
[Role and resource views](../examples/multiuser_release_handoff/figures/README.md) ·
[Run prerequisites](../examples/multiuser_release_handoff/RUNNING.md) ·
[Historical results and limits](../examples/multiuser_release_handoff/RESULTS.md)
