---
name: rpnh-source-provenance
description: "Record the reviewed source tips and fresh-root exclusion boundary."
metadata:
  document-kind: provenance
  audience: operator-and-developer
  language: en
  counterpart: PROVENANCE_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# Source provenance

[中文](PROVENANCE_ZH.md)

This public candidate is a fresh-root export. It does not carry private
development history, deleted evidence, local Registry data or obsolete remote
branches. Its unified source was reviewed from these exact predecessor tips:

- core main: `201e2e91709e4e4cceb3930d3b73eda920f73146`;
- Codex/native plugin line: `6f3558390b44d970e54539f6d5ffaa19fbb8cd3c`;
- DSH integration line: `c5fc189efeecb9b6fdda5be3c598c3fd4eaef570`;
- PetriNet viewer line: `16ffa8b7527c29e549db725ee357d1a564d3b367`.

Included: RPNH core and Registry, generic components, provider adapters,
orchestration, Codex compatibility, native plugins, the optional DSH host,
PetriNet projection/viewer, portable profiles and deterministic tests.

Excluded: paper/project workflows and results, historical handoffs, branch
transport tooling, private runtime data, credentials, caches, local Registry
databases and live-provider evidence. OpenCode will be included only after its
separate implementation passes the same integration boundary.
