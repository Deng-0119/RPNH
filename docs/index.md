---
name: rpnh-documentation-index
description: "Navigate the bilingual operator and developer documentation."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: en
  counterpart: index_ZH.md
  revision: "2026-09-29.4"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](index.md) | [中文](index_ZH.md)

# Documentation

Start with installation and exact model configuration, then choose the example
closest to the application you want to build. The guides describe current
`main`; dated validation records preserve the exact older revision they tested
and must not be read as automatic certification of later commits. Each topic
has a corresponding Chinese page.

| Need | Read |
|---|---|
| Install core/basic, source or wheel | [Installation](guides/installation.md) |
| Run native, hybrid, task and installed cross-host examples | [Examples](guides/examples.md) |
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
| Understand the closed execution design | [Architecture](architecture/design.md) |
| Read the complete harness architecture overview | [Harness architecture](ARCHITECTURE.md) |
| Compare RPNH authority with Codex presentation | [RPNH versus Codex](RPNH_VS_CODEX.md) |
| Read the detailed provider catalog format | [Provider/model configuration](PROVIDER_MODEL_CONFIGURATION.md) |
| Inspect viewer evidence limits | [Display observation](DISPLAY_OBSERVATION.md) |
| Audit source integration and exclusions | [Source provenance](PROVENANCE.md) |
| Review resolved observations and reopen conditions | [Engineering follow-up record](DEFERRED_ENGINEERING_WORK.md) |
| Look up declaration/compile contracts | [Declarations](reference/declarations.md) |
| Understand runtime, owner and atomic records | [Runtime and Registry](reference/runtime-registry.md) |
| Locate session/AgentLoop/model boundaries | [Agents and models](reference/agents.md) |
| Look up plugin, adapter and observer contracts | [Extensions and observation](reference/extensions-observation.md) |
| Test, contribute and prepare a release | [Development and license status](guides/development.md) |
| Review release changes and project policies | [Changelog](../CHANGELOG.md), [contributing](../CONTRIBUTING.md), [security](../SECURITY.md) |
| Review the historical full offline baseline and current focused delta | [Release validation](guides/release-validation.md) |
| Review dated focused/live example evidence | [Examples validation](guides/examples-validation.md) |

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
