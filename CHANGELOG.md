---
name: rpnh-changelog
description: "Record user-visible changes in RPNH releases."
metadata:
  document-kind: project-record
  audience: user-and-developer
  language: en
  counterpart: CHANGELOG_ZH.md
  revision: "2026-10-06.2"
  status: v0.1.0rc1
---

[English](CHANGELOG.md) | [中文](CHANGELOG_ZH.md)

# Changelog

## Unreleased

- Cancelling a previous-net read by opening comparison or leaving the page now
  releases its navigation controls, preserving saved historical-capture restrictions.

- Added an opt-in offline portable-package preview and exact local dependency
  lock through `rpnh package`, inert HOST declaration diagnostics, and read-only
  comparison of two checkpoints from one Registry. These preparation and
  observation interfaces do not install code, grant execution authority, or
  implement candidate-to-runtime or dual-owner execution.

- Portable-package resolution now applies the dependency-depth bound to every
  shared-dependency path and returns a structured error for invalid UTF-8 ZIP
  filenames. Checkpoint comparison resumes the selected live-refresh behavior
  after browser navigation while clearing the previous pair. A cached-page restore
  now retains its renderer and resize observer, resumes only eligible live
  polling, and still cancels stale work; ordinary departure releases the renderer.
  Full-startup synthetic lifecycle tests cover this repair; actual-browser and
  local-socket multi-checkpoint verification remain blocked in the cloud check.

- Documentation builds now include linked result pages and downloads with validation; optional-Node tests preserve HTTP coverage, and wheel audits include the source-observation viewer module.

- Integrated explicit opt-in author/Assembly/graph, typed-v9, P4/P6, bounded I02,
  Workset and normal-child root capabilities with native HTTP/Node read-only
  observation. Acceptance is finite; actual browser remains root/sandbox BLOCKED.
- The bridge checks locally declared context budgets after final rendering and
  retains bounded event/item type diagnostics. Tool allowlists and unknown
  submission semantics remain unchanged; no exact token or CLI output-cap guarantee.
- Added curated bilingual AutomationBench first18 and repair4 public results,
  preserving historical scores and separate conditions. See
  [finite validation](docs/guides/release-validation.md); no full HOST/I00–I10/advanced25 claim.

- Added opt-in source-authoritative Assembly v2 for closed ordinary-v3 graph and
  plain-v1 members, with exact source/fragment provenance, explicit shared budgets,
  final-context carrier checks, complete-request recovery and read-only exact-pair
  validation. Legacy v1 contracts remain intact; open/recursive composition,
  broader graph versions, merge and runtime adoption remain later work.

- Agent runtime profiles may now set `max_turns_per_node` to `null` to omit
  artificial per-node, tool-turn and cumulative task model-call ceilings. The
  Registry records this as explicit unmetered authority while provider retry,
  owner-stop, workspace resource and PetriNet settlement controls remain in
  force. Positive integer profiles retain their existing bounded behavior.
- Agent tasks and workflows can now bind explicitly registered native-plugin
  operations to provider-visible tool names per node or stage. Bindings admit
  exact effects, preserve per-call receipts and structured results, and keep
  legacy tasks and ordinary action records unchanged when the feature is not
  configured.
- Native and managed plugin workers now receive the harness-owned per-call
  `PluginContext.call_id`, so an external effect bridge can distinguish two
  legitimate calls to the same operation from a replay.
- Added optional exact-model reasoning-effort declarations to the user-owned
  provider catalog. Basic, DSH, Codex and OpenCode now consume the same pinned
  model/effort selection, while generated local and external adapters carry the
  selected value to the physical request without a built-in vendor model table.
- Added reproducible JB clinical-packet and 3-DOF powered-descent examples.
  Both use a user-selected exact profile, let the main agent design the graph,
  include independent business verification and show an actual accepted
  PetriNet dashboard without publishing raw Registries or private inputs.
- Added a standalone checkpoint-recovery procedure for continuing the same
  child Registry from its latest owner-stopped cut or a user-selected older
  checkpoint.
- Dashboard capture now validates the terminal/final-result pair referenced by
  current authority, including append-only Registries with prior generations.

## 0.1.0rc1 — 2026-09-29

First public prerelease of the unified RPNH repository.

### Included

- Registry-governed execution with typed Petri-net declarations, markings,
  checkpoints and terminal evidence.
- Conversational main sessions plus independent child task/workflow Registries.
- User-selected checkpoint reopen, owner-stop resume and compacted conversation
  history without rewriting prior Registry evidence.
- Versioned workspace publication, per-path change history and subordinate file
  execution nets.
- User-owned provider/model catalogs, shared external/local provider adapters
  and bounded same-route transport recovery.
- Basic, Codex and OpenCode presentation frontends, plus the optional DSH host.
- Native plugins, serial/parallel/document/long-process examples and a read-only
  PetriNet dashboard with historical checkpoint navigation.

### Release boundary

- Linux and WSL2 are supported; native Windows and macOS are not supported.
- Provider and exact-model routes are not bundled. Live access depends on the
  user's own configuration and authorization.
- Codex, OpenCode and DSH integrations are version-specific; consult their
  guides before installation.
- This is a prerelease. Public declaration contracts are documented, but private
  implementation modules are not a stable SDK.

Validation scope and known limitations are recorded in
[release validation](docs/guides/release-validation.md) and
[examples validation](docs/guides/examples-validation.md).
