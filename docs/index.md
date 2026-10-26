---
name: rpnh-documentation-index
description: "Choose a first-run route, operational guide, API contract or source-code entry point."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: en
  counterpart: index_ZH.md
  revision: "2026-10-19.3"
  status: source-reviewed
  basis: "current source; dated validation records retain their own scopes"
---

[English](index.md) | [中文](index_ZH.md)

# Documentation

Frontend selection, automatic connection and resume: [frontend guide](guides/frontends.md).

[Repository home](../README.md) · [Examples](../examples/README.md) · [CLI](reference/cli.md) · [API](reference/api-index.md) · [Source map](guides/repository-layout.md)

RPNH uses Registry and PetriNet together to declare, execute, inspect and reuse
agent work. Start with one small run, then choose the guide for your next task.
These pages describe the selected public source; dated results identify the
older exact revisions they tested.

Optional client versions and earlier validation:
[Codex, DSH and OpenCode — 2026-10-19](validation/frontends-20261019.md).
Use the [official DSH Web guide](guides/dsh.md) for `rpnh --frontend dsh`, package
preparation, saved preferences and native controls for one bound Python
MainSession. The component milestone and official Web E2E results are tracked
in a [separate record](validation/dsh-unified-20261019.md).

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

## Build, collaborate and reuse

| Need | Guide |
|---|---|
| Edit a shipped input, graph or plugin | [Examples and copy-and-change instructions](guides/examples.md), [customization](guides/customization.md) |
| Copy, co-create, merge, adopt and independently run a complete agent project | [Complete project example](../examples/project_collaboration/README.md), [project operator/API guide](guides/project-entry.md) |
| Understand controlled participants, scoped permissions and protected acceptance | [User collaboration](guides/user-collaboration.md) |
| Extract, compose, instantiate, branch or replace definitions | [Native net operations](guides/net-operations.md) |
| Read public projections and exchange a closed definition | [Typed Registry exchange](guides/typed-registry-exchange.md) |
| Preview/resolve a portable package and run it on a prepared receiver | [Portable packages](guides/portable-packages.md), [native-add package tutorial](guides/package-reuse-example.md) |
| Integrate selected tools, parallel managed calls or isolated programs | [Controlled managed tools](controlled-managed-tools.md), [plugin contracts](reference/extensions-observation.md) |

The current complete-project entry follows implementer/reviewer agents through
controlled local collaboration, explicit conflict resolution, acceptance,
owner adoption and independent receiver execution. Its example page retains
exact evidence, limitations and original task verification. Historical
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
| Compare RPNH authority with presentation integrations | [RPNH versus Codex](RPNH_VS_CODEX.md), [source provenance](PROVENANCE.md) |

## Maintain, contribute and track versions

- [Development](guides/development.md): deterministic checks, package validation and contributor workflow
- [Changelog](../CHANGELOG.md): changes by version/source; [installation](guides/installation.md) explains source candidates versus older releases
- [Contributing](../CONTRIBUTING.md), [security](../SECURITY.md), [license](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md)
- [Release validation](guides/release-validation.md), [example validation](guides/examples-validation.md) and [curated results](results/README.md): dated evidence, tested inputs and unrun scope

## Reading the reference

Use [CLI reference](reference/cli.md) for operator commands and
[API reference](reference/api-index.md) for exported symbols, explicit module
interfaces, protocol entry points and source definitions. The detailed
[declaration](reference/declarations.md), [runtime/Registry](reference/runtime-registry.md),
[agent/model](reference/agents.md) and [extension/observation](reference/extensions-observation.md)
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

- [Static resource-lease reads](guides/static-lease-reads.md)
- [Main-thread history at a fixed cut](reference/main-thread-history.md)
- [Codex history projection](reference/codex-history.md)
- [Explicit Codex 0.161 protocol candidate](CODEX_0161_CANDIDATE.md)

## Validation status

A documentation/source check establishes what is declared and linked. Offline
runtime checks, installed-artifact checks, browser checks and live-provider
runs establish different things. The first-run commands are operator
instructions, not a claim that this documentation revision re-ran them.

Read each result's exact commit, inputs, environment, scorer, failures and
unrun scope before reusing its conclusion. A readable graph is not proof of
terminal success, and a historical pass does not certify a later source tree.
Keep raw operational data and credentials private.

## Current finite validation and public results

[Release validation](guides/release-validation.md) · [Example validation](guides/examples-validation.md) · [Result archive](results/README.md) · [AutomationBench first18 + repair4](../examples/automationbench/PUBLIC_RESULTS_20261006.md)

For native-input application examples, see [controlled mock refund](../examples/approval_workflow/README.md)
and [synthetic external-result quote](../examples/a2a_quote/README.md), with their
explicitly bounded evidence and external-protocol status.

## Multi-user release handoff: independent agents and shared resources

The [complete example](../examples/multiuser_release_handoff/README.md) follows four controlled participants with independent Tasks, Registries and private agent PNs, plus a separate fifth Source Registry for resource control. Native checkpoint restoration completed v2, SQL, the QA checklist, incident and final handoffs; the final read-only audit passed 98/98 required checks. Source is not an agent. Original failures and two optional UNKNOWN history-coverage checks remain explicit.

[Actual role PNs and Source details](../examples/multiuser_release_handoff/figures/README.md) · [Original task and run prerequisites](../examples/multiuser_release_handoff/RUNNING.md) · [Exact historical results and limits](../examples/multiuser_release_handoff/RESULTS.md). New model runs require authorization; this is not an official benchmark score, deployment or executed rollback validation.

- [Basic and Viewer offline help](guides/frontend-help.md)
