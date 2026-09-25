---
name: rpnh-deferred-engineering-work
description: "Record confirmed non-blocking engineering follow-ups and reopen conditions."
metadata:
  document-kind: engineering-notes
  audience: developer
  language: en
  counterpart: DEFERRED_ENGINEERING_WORK_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# Deferred Engineering Work

[中文](DEFERRED_ENGINEERING_WORK_ZH.md)

This document records confirmed, non-blocking engineering work that does not
currently justify changing a supported runtime boundary. An entry here is not a
claim that the affected behavior is incorrect. Re-evaluate an entry when its
reopen conditions become observable in normal use.

## DOPT-001: Repeated event materialization in main-thread projection

- **Status:** deferred
- **Category:** performance
- **Priority:** low

### Current behavior

`MainThreadRegistry.project_current_thread()` reconstructs the current thread,
turn lineages, and child-Registry link lineages. Each of those three private
paths calls `EventStore.list_events()` independently to build the same
publication-event ordinal map. A stable Registry therefore materializes and
parses its complete event history three times during one projection.

This does not change Registry authority, ordering, corruption detection, or the
projected result. It adds linear read and allocation overhead as the main
Registry history grows.

### Offline baseline

The following synthetic baseline was measured on 2026-09-24 with committed
main turns and no provider calls. The timing is local diagnostic evidence, not
a portable performance guarantee.

| Committed turns | Registry events | Event envelopes materialized per recovery | Median warm recovery |
| ---: | ---: | ---: | ---: |
| 0 | 4 | 12 | 13 ms |
| 1 | 15 | 45 | 12 ms |
| 8 | 92 | 276 | 37 ms |
| 32 | 356 | 1,068 | 126 ms |
| 128 | 1,412 | 4,236 | 466 ms |

At 128 turns, one complete event scan took a median 27.2 ms and an empty
incremental scan after the current highest ordinal took 0.277 ms. Replacing the
second and third full scans would therefore avoid roughly 54 ms in this sample,
or about 12% of the complete projection time. Object reads and lineage
validation remain the larger part of recovery, so this change would not make
recovery three times faster.

Short sessions do not show a material user impact. No data-loss, resume,
workflow, provider, or Petri-net correctness failure was observed. The focused
main-thread Registry baseline passed all 10 tests.

### Proposed bounded optimization

If this item is reopened:

1. Create a private, request-local publication-event ordinal index inside one
   `project_current_thread()` call.
2. Fully read events for the first subprojection, retain the highest observed
   ordinal, and use `list_events(after_ordinal=...)` for later subprojections.
3. Merge newly visible events before ordering each later object set. Separate
   SQLite reads may observe later commits, so a frozen first-read map is not
   sufficient.
4. Discard the index at the end of the projection. Do not add a process-wide or
   cross-request cache.
5. Keep write-precondition reads independent and preserve the current fail-loud
   behavior for missing publication events and malformed authority.

The expected implementation surface is the private main-thread projection
chain plus focused tests. It should not require EventStore, schema, persistent
format, provider, workflow, or Petri-net changes.

### Acceptance boundary

A future change should prove:

- projection output remains exactly equivalent;
- one stable projection performs one full event read followed by bounded
  incremental reads rather than three full reads;
- a commit that becomes visible between subprojections is incorporated;
- missing publication events and corrupt authority still fail loudly;
- no index survives into a later projection or a write-precondition check; and
- a focused benchmark shows a meaningful improvement on a realistically long
  main-session Registry.

### Reopen conditions

Reconsider this item if normal main-session open, resume, or history projection
develops user-visible latency; if realistic profiling shows repeated event
materialization has become a leading cost; or if another approved main-thread
change already touches the same private projection chain. Until then, the
measured improvement does not justify the concurrency-sensitive code change.
