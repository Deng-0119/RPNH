---
name: rpnh-main-thread-history
description: "Read native main-thread history at a fixed Registry cut."
metadata:
  document-kind: reference
  audience: trusted-host-developer
  language: en
  counterpart: main-thread-history_ZH.md
  revision: "2026-10-08.1"
  status: native-read-primitive
---

[English](main-thread-history.md) | [中文](main-thread-history_ZH.md)

# Fixed-cut main-thread history

## Boundary and authority

`MainThreadRegistry` owns the native main-thread lineage. Its history API is an
advanced **trusted-host, refs-only** read primitive. It is not an authorization
service, public transcript API, Codex DTO, or an execution/recovery entry point.
It works with an existing read-only `_RegistryCore` and does not acquire a writer,
change metadata, reconcile TaskControl, launch children, or invoke a provider.

`capture_read_cut()` returns a frozen `MainThreadReadCut` with the existing task
and branch identities, one `CanonicalView`, the exact boundary event identity,
and the exact thread version. A bare `CanonicalView` carries only an ordinal and
is not accepted as a source-bound cut. Each read rechecks persisted source and
boundary identities, canonical visibility, exact object envelopes and bytes,
and existing thread, turn, and child-link lineage rules. Future ordinals, another
Registry/thread, damaged lineages, and inconsistent references fail closed.
Existing child-link path validation still checks the current filesystem for
unsafe paths/symlinks; it does not open a child Registry or read its bodies.
Changing that filesystem boundary can therefore invalidate an old cut's read.

A cut remains readable after later appends or reopening the same Registry,
provided its immutable objects and identity remain available and valid. A copy
with the same native Registry identity is the same logical source; filesystem
paths do not become cursor authority. Neither a cut nor an anchor is a bearer
grant. Callers must recheck their existing session/source access on every request.
This API does not extend `RegistryReadSession` or `TypedReaderCatalog` permissions.

## Projection and pagination

`project_thread_at(cut)` returns `MainThreadHistoryProjection`. Only committed
turns produce conversational entries, preserving the existing display membership
filter. Accepted, running, interrupted, and failed turns do not acquire history
items. Current thread state and exact active/latest pointers remain separate.

Each turn has its registered ordinal and exact committed turn ref, plus exactly
two field slots in order: `user_input`, then `answer`. A slot's identity is its
exact turn ref and field position, not a reindexed display-list offset. These are
**content references, not public body values**. Raw `user_input` and `answer`
JSON can include plugin configuration, task prompts, and instructions. They are
not returned, and answer/decision/receipt references are not dereferenced into
child resources. The API does not expose a body-read method or accept a path.

`page_turns_at(cut, limit=50, order="asc", anchor=None)` and
`page_items_at(cut, limit=50, order="asc", turn_filter=None, anchor=None)` return
typed pages. Limits must be integers from 1 through 100. Item order is the pair
`(turn ordinal, field position)`; descending order reverses that complete order.
An optional item filter requires the exact committed turn version at the cut.

Native `MainThreadHistoryAnchor` values bind the cut, query kind, order, filter,
exact turn identity, ordinal, and optional item position. Initial anchors can
set `inclusive=True`; returned continuation anchors are exclusive. A next anchor
exists only when another entry remains. Changing query, direction, filter, or
cut rejects an old anchor. There is no separate cursor store or transport codec.
Turns and items must use the same captured cut when a caller needs one snapshot.

## Body bounds and compatibility

There is no implicit 4 MiB history limit. Default reads use each object's
registered size as the physical bound passed to the existing
`ObjectStore.read_registered`. Backing files that exceed that size fail instead
of being read without a bound. Optional `max_object_bytes` on capture, projection,
and page methods is an explicit caller budget; exceeding it fails the read and
does not truncate or omit entries. The caller budget cannot enlarge the physical
bound beyond the registered size. Legal large registered input/answer JSON remains
supported by default, including the existing dictionary projection.

Every page currently reconstructs and validates the cut's lineages. Page response
counts and individual payload reads are bounded; scan work and total history
memory are not claimed to be bounded. Registry row metadata still uses the
existing native query APIs. The small refs-only result does not imply that the
underlying descriptors are small.

`project_current_thread()` and the read alias `recover_thread()` retain their
existing dictionary shape and content, now read through one captured current
canonical view. Their trust/disclosure boundary is unchanged. This does not make
`MainSession` activation or reconciliation methods suitable history readers.

## Annotations and the next adapter boundary

`child_links` contains only immutable main-Registry link facts at the selected
cut, with no child path or child body. Current TaskControl annotations are not
part of this snapshot. Existing `MainSession.display_history` can use a current
TaskControl fallback to render launch annotations; this native projection does
not pretend to reproduce that live text at an old cut. It preserves both history
field slots and exposes immutable link facts separately.

Before a public adapter can consume these values, its existing safe display
filter must be carried through a fixed-cut, authorized body/renderer boundary.
It must decide how to present separately identified live annotations, and must
not expose arbitrary field JSON or infer child access from a ref. No public
reader grant or catalog entry has been invented here. No Codex `thread/turns/list`
or `thread/items/list` RPC, transport cursor, `itemsView` behavior, cold-resume
hydration, or newer client compatibility is implemented or certified by this API.

## Deterministic validation

Focused native Registry tests cover both orders, multiple pages, within-turn
positions, exact filters, initial/continuation boundaries, append stability,
interleaved cuts, reopened readers, invalid identities, empty/partial/stopped/
failed membership, provisional/future filtering, large descriptors, physical
body bounds, corruption, and refs-only disclosure. Read-only tests deny writer
operations and check event/object counts, physical head, writer epoch, Registry
database/object bytes, profile and owner-lock bytes. SQLite's volatile `-shm`
read marks are excluded from byte comparisons. Main-session compatibility tests
remain distinct from stock-frontend or real-provider validation.
