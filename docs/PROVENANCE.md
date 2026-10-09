---
name: rpnh-source-provenance
description: "Record the initial source export and current public distribution boundary."
metadata:
  document-kind: provenance
  audience: operator-and-developer
  language: en
  counterpart: PROVENANCE_ZH.md
  revision: "2026-10-09.2"
  status: source-reviewed-v0.1.0rc1
---

# Source provenance

[中文](PROVENANCE_ZH.md)

The initial public prerelease was created as a fresh-root export of reviewed
product sources. That describes the initial export, not the contents of every
later Git commit: internal material has appeared in subsequent public history.
The current public tree distributes reviewed product materials separately from
the private development and evidence repository. Historical commits are not
rewritten by this documentation correction.

The initial unified source was reviewed from these exact predecessor tips;
product development continues on the canonical `main` history:

- core main: `201e2e91709e4e4cceb3930d3b73eda920f73146`;
- Codex/native plugin line: `6f3558390b44d970e54539f6d5ffaa19fbb8cd3c`;
- DSH integration line: `c5fc189efeecb9b6fdda5be3c598c3fd4eaef570`;
- PetriNet viewer line: `16ffa8b7527c29e549db725ee357d1a564d3b367`.

Included: RPNH core and Registry, generic components, provider adapters,
orchestration, Codex and OpenCode presentation compatibility, native plugins,
the optional DSH host, PetriNet projection/viewer, portable profiles and
deterministic tests.

The current distribution boundary excludes internal handoffs and backlogs,
private runtime data, credentials, caches, local Registry databases and raw
live-provider evidence. Reviewed public result summaries and runnable examples
remain product materials. This boundary does not assert that earlier Git history
never contained excluded material. Historical predecessor branches are provenance
only; they are not installation overlays or active development roots.
