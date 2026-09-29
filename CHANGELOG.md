---
name: rpnh-changelog
description: "Record user-visible changes in RPNH releases."
metadata:
  document-kind: project-record
  audience: user-and-developer
  language: en
  counterpart: CHANGELOG_ZH.md
  revision: "2026-09-29.1"
  status: v0.1.0rc1
---

[English](CHANGELOG.md) | [中文](CHANGELOG_ZH.md)

# Changelog

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
