---
name: rpnh-repository-layout
description: "Find user entry points, examples, viewer sources, integrations and validation code."
metadata:
  document-kind: guide
  audience: user-and-developer
  language: en
  counterpart: repository-layout_ZH.md
  revision: "2026-09-26.1"
  status: source-reviewed-pre-release
---

[English](repository-layout.md) | [中文](repository-layout_ZH.md)

# Repository map

The repository keeps execution authority, presentation adapters, examples and
documentation separate. Start from the installed `rpnh` command; source paths
below explain ownership and are not additional runtime entry points.

| Path | Purpose | Start here when |
|---|---|---|
| `cpn/rpnh/` | Registry, PetriNet, owner, tasks, sessions and CLI application services | Changing core execution semantics |
| `cpn/llm_adapters/` | Shared local-process and external-provider input ports | Adding transport behavior without host duplication |
| `cpn/components/` | Reusable AgentLoop and execution-service components | Extending managed Agent behavior |
| `cpn/frontend/static/` | Viewer assets shipped in the Python wheel | Inspecting the runtime dashboard |
| `frontend/net-viewer/` | Lockfile, build script and JavaScript tests for viewer assets | Rebuilding or testing the dashboard |
| `examples/` | Source-checkout examples, inputs and task-pattern gallery | Learning by running a local example |
| `cpn/examples/` | Provider-neutral example bundle exported by installed `rpnh` | Testing Basic, Codex, DSH or OpenCode |
| `integrations/` | Optional host adapters such as DSH | Working on one pinned host boundary |
| `docs/` | Bilingual guides, architecture and references | Looking up supported behavior |
| `scripts/` | Focused build, documentation and validation helpers | Maintaining the repository |
| `tests/` | Deterministic offline acceptance and regressions | Verifying a change |

Generated profiles, provider transcripts, Registry databases, run directories,
virtual environments and viewer dependencies do not belong in the repository.
Examples create their runs under a caller-selected directory outside the source
tree. Sanitized validation summaries may be committed; raw operational evidence
may not.

Use the [example catalog](examples.md) to choose a task and the
[dashboard tutorial](viewer.md) to inspect its PetriNet. Use the
[architecture guide](../architecture/design.md) before changing ownership or
execution boundaries.
