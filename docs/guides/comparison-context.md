---
name: rpnh-comparison-context
description: "Compare authorized exact PN definitions and checkpoints without execution."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: comparison-context_ZH.md
  revision: "2026-10-07.1"
  status: implementation-candidate
  basis: "public exact read session; descriptive comparison only"
---

[English](comparison-context.md) | [中文](comparison-context_ZH.md)

# Independent exact-net comparison

The independent viewer reads an existing, HOST-selected Registry read session. It
can compare exact registered net definitions, exact saved checkpoints, and stored
author PN projections. It does not require a current run. It does not compile,
execute, adopt, merge, create a grant, or change a Registry.

Start it with an existing trusted local read-HOST configuration:

```sh
rpnh net --read-host-config /absolute/path/read-host.json --view --no-open
```

This is a same-OS-user, loopback-only viewer. The configuration chooses existing
source bindings and owner-issued observation contexts; HTTP input cannot supply
paths, principal declarations, grants, callbacks, or a compiler. Index, record,
material-body and export permissions remain independent. Opening this viewer
does not grant any of them. See the independent Registry read-session guide for
configuration and issuer details. Existing `rpnh net --run ...` behavior remains.

## Select and navigate

1. Choose a source and an explicit exact object on each side. There is no implicit
   latest checkpoint and neither selector depends on the current dashboard.
2. Choose definition, configuration, materials and/or runtime axes. Unrequested
   axes say `not_requested`.
3. Read the complete pair. Use the independent canvases to pan, zoom and select
   nodes. Both canvases use the existing NetRenderer and ELK layout.
4. Choose a declared module/member scope, or scope to a selected node. Hierarchy
   comes from verified declarations and occurrence paths, never names or layout.
   Parent navigation follows the declared parent. Internal arcs and boundary
   connections are separate; boundary placeholders are not new PN owners.
5. “Full pair” restores both exact nets at the same returned SourceCuts. If the
   full scope exceeds limits or is unavailable, the old scoped pair is cleared.
   Large scopes are never silently truncated and called complete.

When no declared hierarchy is provided, the graph stays flat and says so. A
source-cut handle belongs to its read session; changing its head, borrowing a
handle from another session, or silently selecting a newer cut is rejected.
Ordinary later appends can coexist with a reconstructable fixed cut. Binding,
fence, catalog or authority changes invalidate the observation.

## What each mode means

- `reliable_diff`: verified correspondence closes the selected scope, including
  its internal nodes and arcs. Individual fields can still be unknown
- `partial_mapping`: only the verified portion is compared; remaining subjects
  stay visible without invented add/delete claims
- `full_pair`: independent reading without verified correspondence. Matching
  names or bytes do not establish identity

A retained author identity and a copy are different claims. A retained origin
can also have several distinct copies. Copy direction is explicit after swapping
panes. Split/fusion remains a grouped relation, including its grouped edges;
there is no Cartesian expansion or inferred runtime identity. General many-to-
many mapping is unsupported. Manual visual pairing changes only this displayed
comparison and never increases reliability or writes a Registry fact.

For a direct copy or retained operation using the registered native-plugin v1
component, the reader can also verify its generated capability place and read
arc. It checks the complete stored lowering recipe and exact configuration,
operation, plugin/executor, tool and schema registrations against the authorized
parent projection. This proof uses `rpnh/native_capability_derivation/v1` and
means author correspondence only. It never composes a chain of parents, compiles
a net, or establishes token continuity. Changed configuration, missing parent
material authority, other lowerers and shared capability carriers remain unknown.

## Four independent axes

- Definition compares the explicitly provided public node/arc fields through
  verified subject correspondence
- Configuration compares approved exact declaration/requirements references.
  Different refs do not prove different effective model, tool or workspace
  values. Host-registration requirements and package environment requirements
  retain their distinct types
- Materials uses exact token resource refs and separately authorized headers.
  Body digests are computed only after an authorized material-body read; an
  initially denied body stays unknown. A Boolean arc `resource` flag is never a
  material reference
- Runtime currently covers selected-checkpoint marking and exact token
  occurrence refs only. Firings, activity and completion evidence remain
  explicitly unknown. Definition-only targets have no borrowed current marking

`provided: null`, an unknown field, and proven absence are distinct. Known-same
means one provided field is equal under its comparator, not that two nets are
business-equivalent or safely interchangeable. Counts are field-row counts for
this exact scope. Missing counts are `null`, not zero.

## Publication and lifecycle

The server validates both sides and all evidence, validates the complete DTO,
and rechecks every involved source before publishing anything from the pair.
No half-pair is streamed. Errors are fixed no-store codes without private paths
or exception details. The comparison-only provider has no legacy raw-net route.

Changing a selector, scope, axis or visual pair cancels pending work and clears
both canvases, mapping, counts and details. Late responses cannot repopulate a
new generation. Close, dashboard navigation, Back/Forward, pagehide and bfcache
restoration discard the pair. The previous live-refresh preference is preserved.

The new read-only routes are `GET/HEAD /api/v2/comparison-selection` and
`GET/HEAD /api/v2/comparison-context?request=<JSON>`, using
`rpnh/comparison_request/v1` and `rpnh/comparison_context/v1`. Both request and
response are closed DTOs. The existing `/api/v2/comparison-view` and
`rpnh/checkpoint_comparison/v1` still require the same exact net and preserve
their earlier current-capture semantics.

## Verification boundaries

Deterministic Python tests exercise canonical owner issuers, real stored author
producers, real checkpoint publication, public sessions, permissions and cuts.
Node tests exercise strict DTO validation and cancellation; a controlled-DOM test
runs the actual dual NetRenderer lifecycle with real ELK geometry. Those are not
browser paint, accessibility, mobile behavior, OS isolation, or a malicious-HOST
security audit. Browser/loopback checks blocked by the execution environment must
be completed in a supported local environment without bypassing its restrictions.
