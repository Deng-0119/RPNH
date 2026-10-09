---
name: rpnh-documentation-index
description: "Navigate the bilingual operator and developer documentation."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: en
  counterpart: index_ZH.md
  revision: "2026-10-09.1"
  status: source-candidate-not-published
  basis: "core; adapter differences explicitly labelled"
---

[English](index.md) | [中文](index_ZH.md)

# Documentation

For a continuous introduction to the project's purpose, execution model and
evaluation boundaries, read the [technical report](technical-report.md).

Start with installation and exact model configuration, then choose the example
closest to the application you want to build. The guides describe this selected
source snapshot; dated validation records preserve the exact older revision they tested
and must not be read as automatic certification of later commits. Each topic
has a corresponding Chinese page.

| Need | Read |
|---|---|
| Understand RPNH before trying or integrating it | [Technical report](technical-report.md) |
| Install core/basic, source or wheel | [Installation](guides/installation.md) |
| Run native, hybrid, task and installed cross-host examples | [Examples](guides/examples.md) |
| Export and modify a dependency-complete example | [Reusable examples](guides/examples.md#export-an-example-and-make-it-yours) |
| Build a runnable v2 package and prepare its receiver | [Native-add package tutorial](guides/package-reuse-example.md) |
| Understand the source tree and packaged boundaries | [Repository map](guides/repository-layout.md) |
| Configure routes, models and every supported runtime limit | [Configuration reference](guides/configuration.md), [models](guides/models.md) |
| Operate sessions/tasks and recover | [Usage](guides/usage.md) |
| Continue the same task from a checkpoint | [Checkpoint recovery](guides/checkpoint-recovery.md) |
| Diagnose without losing evidence | [Troubleshooting](guides/troubleshooting.md) |
| Add workflows, tools, skill resources or MCP bindings | [Customization](guides/customization.md) |
| Extract, compose, instantiate or replace Petri-net definitions | [Native Petri-net operations](guides/net-operations.md) |
| Use optional Codex/DSH and understand coexistence | [Adapters](guides/adapters.md) |
| Use the pinned OpenCode presentation | [OpenCode frontend](guides/opencode.md) |
| Operate the pinned DSH host | [DSH host adapter](guides/dsh.md) |
| Inspect runs in the PetriNet dashboard | [PetriNet dashboard](guides/viewer.md) |
| Query and inspect fixed source scopes | [SourceSet queries](guides/source-queries.md) |
| Preview declaration packages and lock local dependencies offline | [Portable packages](guides/portable-packages.md) |
| Prepare exact v2 package requirements without changing legacy package behavior | [Package environments](guides/package-environments.md) |
| Open an independently authorized typed reader and cross-net comparison | [Independent Registry reader](guides/independent-registry-reader.md) |
| Understand the fixed-cut typed read contract and authority checks | [Registry read sessions](reference/registry-read-sessions.md) |
| Inspect typed public projections and exchange a complete closed definition | [Typed Registry projections and subnet exchange](guides/typed-registry-exchange.md) |
| Compare exact nets with four independent evidence axes | [Cross-net comparison](guides/comparison-context.md) |
| Diagnose declarations against an already-prepared HOST snapshot | [HOST declaration diagnostics](guides/host-readiness.md) |
| Compare two checkpoints from one Registry without execution | [Checkpoint comparison](guides/checkpoint-comparison.md) |
| Page registered results, schedule managed calls and run isolated programs | [Controlled managed tools](controlled-managed-tools.md) |
| Understand the closed execution design | [Architecture](architecture/design.md) |
| Read the complete harness architecture overview | [Harness architecture](ARCHITECTURE.md) |
| Compare RPNH authority with Codex presentation | [RPNH versus Codex](RPNH_VS_CODEX.md) |
| Read the detailed provider catalog format | [Provider/model configuration](PROVIDER_MODEL_CONFIGURATION.md) |
| Inspect viewer evidence limits | [Display observation](DISPLAY_OBSERVATION.md) |
| Audit source integration and exclusions | [Source provenance](PROVENANCE.md) |
| Author ordinary descendants and independent copies of adapted regions | [Open-region descendants](reference/open-region-descendants.md) |
| Look up declaration/compile contracts | [Declarations](reference/declarations.md) |
| Understand runtime, owner and atomic records | [Runtime and Registry](reference/runtime-registry.md) |
| Locate session/AgentLoop/model boundaries | [Agents and models](reference/agents.md) |
| Look up plugin, adapter and observer contracts | [Extensions and observation](reference/extensions-observation.md) |
| Test, contribute and prepare a release | [Development and license status](guides/development.md) |
| Review release changes and project policies | [Changelog](../CHANGELOG.md), [contributing](../CONTRIBUTING.md), [security](../SECURITY.md) |
| Review the historical full offline baseline and current focused delta | [Release validation](guides/release-validation.md) |
| Review dated focused/live example evidence | [Examples validation](guides/examples-validation.md) |
| Import explicitly selected ordinary author changes | [Selective plain transplant](reference/plain-transplant.md) |
| Author ordinary descendants and independent copies of selective transplants | [Transplant descendants](reference/plain-transplant-descendants.md) |
| Review historical scores, exact sources and unrun scope | [Curated results](results/README.md) |

## Reading the reference
Declaration contracts, advanced trusted-host interfaces and private implementation modules are explicitly separated. Source paths in each page are repository-relative navigation aids, not independent installation instructions. Optional APIs are labelled by capability; their availability must be checked in the selected source/artifact. A generated page does not grant SDK stability.

## Validation status

This tree unifies core, native plugins, Codex and OpenCode presentation, DSH,
and the read-only viewer. Current interface documents cover checkpoint reopen,
subordinate execution nets, versioned workspace deltas and the separation of
Registry structural validation from application retry policy. Offline checks
establish deterministic behavior and package completeness only; they do not
prove that a user-owned provider route works. Raw live API evidence remains
outside the source tree and requires separate authorization. Sanitized,
non-secret acceptance summaries may be published with the relevant example.

- [Local Worksets and acceptance](reference/worksets.md)

## Current finite validation and public results

[Finite integration and browser block](guides/release-validation.md) · [AutomationBench first18 + repair4](../examples/automationbench/PUBLIC_RESULTS_20261006.md)

## Integrated explicit contracts

| Topic | Contract |
|---|---|
| Graph authoring | [graph-authoring](reference/graph-authoring.md) |
| Graph source merge / P6 | [graph-source-merge](reference/graph-source-merge.md) |
| Assembly full-history merge / P4 | [assembly-full-history-merge](reference/assembly-full-history-merge.md) |
| Explicit split/fusion | [author-identity-transform-contract](reference/author-identity-transform-contract.md) |
| Plain merge analysis | [plain-merge-analysis](reference/plain-merge-analysis.md) |
| Plain merge result | [plain-merge-result](reference/plain-merge-result.md) |
| Merge composition | [plain-merge-composition](reference/plain-merge-composition.md) |
| Nested Assembly merge | [plain-merge-nested-assembly](reference/plain-merge-nested-assembly.md) |
| Open-region authoring | [open-region-authoring](reference/open-region-authoring.md) |
| Normal-child root closure | [normal-child-root-contract](reference/normal-child-root-contract.md) |

## Exact reads and frontend protocol

- [Static resource-lease reads](guides/static-lease-reads.md)
- [Main-thread history at a fixed cut](reference/main-thread-history.md)
- [Codex history projection](reference/codex-history.md)
- [Explicit Codex 0.161 protocol candidate](CODEX_0161_CANDIDATE.md)
