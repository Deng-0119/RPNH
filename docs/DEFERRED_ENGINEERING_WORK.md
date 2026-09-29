---
name: rpnh-deferred-engineering-work
description: "Record resolved and deferred engineering observations with reopen conditions."
metadata:
  document-kind: engineering-notes
  audience: developer
  language: en
  counterpart: DEFERRED_ENGINEERING_WORK_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-pre-release
---

# Engineering follow-up record

[中文](DEFERRED_ENGINEERING_WORK_ZH.md)

This document preserves confirmed engineering observations that do not define a
supported runtime feature. Resolved entries remain here so an old baseline is
not mistaken for current behavior. Reopen an entry only when its stated
conditions are observable in normal use.

## DOPT-001: Main-thread projection event materialization

- **Status:** resolved on current `main`
- **Category:** performance
- **Priority:** monitor only
- **Resolution boundary:** `de537681c43a077a999089220558aca266196ead`

### Historical behavior and baseline

The 2026-09-24 implementation rebuilt publication-event ordinal maps by
materializing the complete event history from several main-thread projection
paths. This did not change authority or projected results, but repeated linear
read/allocation work as Registry history grew.

The original provider-free synthetic baseline is retained for provenance:

| Committed turns | Registry events | Event envelopes materialized per recovery | Median warm recovery |
|---:|---:|---:|---:|
| 0 | 4 | 12 | 13 ms |
| 1 | 15 | 45 | 12 ms |
| 8 | 92 | 276 | 37 ms |
| 32 | 356 | 1,068 | 126 ms |
| 128 | 1,412 | 4,236 | 466 ms |

At 128 turns, one complete scan measured 27.2 ms and an empty incremental scan
after the highest ordinal measured 0.277 ms. These local numbers were diagnostic,
not portable performance guarantees.

### Current behavior

Main-thread and Registry authority reads now use indexed object/event query
surfaces such as `canonical_object_rows`, `event_by_id`, filtered event queries
and direct current-authority lookup. They no longer build normal authority by
repeatedly parsing the entire event stream. Exact publication-event presence,
object identity, schema validation and fail-loud corruption behavior remain.

A preserved large historical 3-DOF Registry was used as a realistic read-only
check after the change. The authority inspection benchmark improved from about
1.54 seconds and 491 MiB peak RSS to about 0.57 seconds and 56 MiB; `rpnh net`
also opened the verified event head at ordinal 16185 without writer authority.
These measurements are local evidence, not a release-wide performance promise.

The implementation stayed inside EventStore query/accounting and normal
Registry read paths. It did not add a process-wide cache, persistent index
format, provider behavior, workflow rule or PetriNet semantic change.

### Reopen conditions

Reopen this item only if profiling of a supported, realistic Registry again
shows full event-stream materialization as a leading cost, or if an indexed
query loses exact-head/publication validation. Any new optimization must retain
equivalent projection output, concurrent-read correctness, fail-loud malformed
authority behavior and read-only viewer semantics. Use a focused long-Registry
benchmark; do not infer a need for a cache from a synthetic worst case alone.
