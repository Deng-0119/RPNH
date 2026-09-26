---
name: rpnh-documentation-index
description: "Navigate the bilingual operator and developer documentation."
metadata:
  document-kind: index
  audience: operator-and-developer
  language: en
  counterpart: index_ZH.md
  revision: "2026-09-26.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](index.md) | [中文](index_ZH.md)

# Documentation

Start with installation and exact model configuration. These are source-reviewed documents for the pre-release code, not proof that the final common candidate or every optional host has passed testing. Each topic has a corresponding Chinese page.

| Need | Read |
|---|---|
| Install core/basic, source or wheel | [Installation](guides/installation.md) |
| Run native, hybrid, task and installed cross-host examples | [Examples](guides/examples.md) |
| Configure exact routes and credentials | [Models](guides/models.md) |
| Operate sessions/tasks and recover | [Usage](guides/usage.md) |
| Diagnose without losing evidence | [Troubleshooting](guides/troubleshooting.md) |
| Add workflows, tools, skill resources or MCP bindings | [Customization](guides/customization.md) |
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
| Review non-blocking follow-up work | [Deferred engineering work](DEFERRED_ENGINEERING_WORK.md) |
| Look up declaration/compile contracts | [Declarations](reference/declarations.md) |
| Understand runtime, owner and atomic records | [Runtime and Registry](reference/runtime-registry.md) |
| Locate session/AgentLoop/model boundaries | [Agents and models](reference/agents.md) |
| Look up plugin, adapter and observer contracts | [Extensions and observation](reference/extensions-observation.md) |
| Test, contribute and prepare a release | [Development and license status](guides/development.md) |
| Review the current offline acceptance boundary | [Release validation](guides/release-validation.md) |
| Review focused example acceptance | [Examples validation](guides/examples-validation.md) |

## Reading the reference
Declaration contracts, advanced trusted-host interfaces and private implementation modules are explicitly separated. Source paths in each page are repository-relative navigation aids, not instructions to read a private repository. Optional APIs are labelled by capability; their availability must be checked in the selected source/artifact. A generated page does not grant SDK stability.

## Validation status

This tree unifies core, native plugins, Codex and OpenCode presentation, DSH,
and the read-only viewer. Offline checks establish deterministic behavior and
package completeness only; they do not prove that a user-owned provider route works.
Raw live API evidence remains outside the public source tree and requires
separate authorization. Sanitized, non-secret acceptance summaries may be
published with the relevant example.
