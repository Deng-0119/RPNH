---
name: rpnh-codex-history
description: "Owner-bound Codex 0.155 fixed-cut history projection."
metadata:
  document-kind: reference
  audience: trusted-host-developer
  language: en
  counterpart: codex-history_ZH.md
  revision: "2026-10-08.1"
  status: partial-frontend-compatibility
---

[English](codex-history.md) | [中文](codex-history_ZH.md)

# Codex owner history

The supported client remains **Codex 0.155.0**. `thread/turns/list` and
`thread/items/list` page the same native MainThreadRegistry authority used by
the basic main session. `state.turns` is only a live reconciliation cache and
is not a persisted-history source. Schema fixtures from 0.161.0 are research
evidence, not certification or permission to change the runtime pin.

## Existing owner boundary

The local launcher binds one MainSession behind its private temporary Unix
socket (0600). Initialization checks protocol version, not caller identity.
An existing owner lease and source binding are rechecked before each history
read and after obtaining the response-send lock. Closing the owner, replacing
its root/lock/Registry database/object-store root, rebinding the actual database
path, or changing native source identity rejects the request. Cursors are only
location hints; they never authorize another root, source, caller, or child.

`MainSession.history_registry` is a read-only handle to the **same** physical
Registry, using the existing `_RegistryCore(create=False, read_only=True)`
mode=ro/query_only path. It creates no Registry, writer epoch, grant, owner lease,
or second authority. The live owner retains its normal writer. A history
request never constructs/resumes a MainSession, persists a profile/state, calls
a provider, opens a child Registry, launches work, or reconciles TaskControl.
`thread/resume` remains a composite operation: existing live reconnect/tracking
is preserved, while its initial history cursors are produced separately by the
pure reader. Existing background live tracking may later reconcile a terminal
turn; that is not a pagination effect.

This retains the trusted local-owner/immutable-Registry threat model. Source
pre/post checks are not a guarantee against malicious same-OS-owner ABA races.
Global ObjectStore no-follow/race hardening is outside this change. SQLite may
create its `-shm` and empty `-wal` bookkeeping files on an initial read-only open;
query-only does not mean every directory byte is immutable. Tests establish
their physical-byte observation window after fixture writers finish and the
read handle is prepared, separately checking canonical DB/objects/nonempty WAL,
event head and writer epoch. Volatile `-shm` read marks are not authority.

## Fixed-cut safe rendering

The native cut binds task/branch, boundary event and ordinal, and exact thread
version. Every page revalidates canonical lineage at that cut. Child-link
publication can advance the boundary without changing the thread version, so
the complete cut is retained. Only committed turns have historical items;
accepted/running/interrupted/failed turns do not become conversation body.

Native committed-membership validation and exact-at-cut hydration stay in
`MainThreadRegistry._committed_turn_documents_at`. MainSession alone converts
those trusted internal records to `MainDisplayTurn`: original user text,
decision reply, existing protocol-validity notice, and an immutable at-cut
launch annotation when an exact origin link exists. No raw input/answer JSON,
plugin configuration, profile, task prompt, instruction, workflow graph or
child answer/decision/receipt body crosses this renderer. Current TaskControl
fallback annotations are never injected into an older page. Unknown historical
timestamps are null, not fabricated from request time.

## Protocol details

- Turns default to descending and `itemsView: summary`; items default ascending.
  Both default to 25 entries. Legal uint32 limits clamp to 1..100, including
  zero to one, matching 0.155. Invalid types, negative and out-of-range values
  fail. Items can filter by a committed public turnId.
- `notLoaded` means `items: []`. `summary` and `full` include the two supported
  safe text items for an RPNH turn. Full does not imply access to child/tool
  internals. Items responses contain `{turnId, item}` entries.
- `nextCursor` is exclusive after the last returned entry, only when more
  entries exist. `backwardsCursor` is inclusive at the first entry and bound to
  the opposite direction. Empty pages return both as null.
- Resume captures one cut for both backwards hydration cursors. Initial
  anchors are inclusive; an empty-cut boundary token keeps both initial
  queries empty even if new turns commit after resume. Initial turns tokens
  leave itemsView explicitly unbound because the official resume contract
  does not select one; first-page continuations bind the actual view.
- Cursors are strictly parsed, at most 4096 characters, versioned and bound to
  thread/source/cut/query/order/view/filter/position. No new persistent cursor
  store or signing/authorization service exists. Valid old cuts can be read
  after reopening the same owner source; old cursors cannot select another
  source or survive a revoked live binding. They are not tamper-proof grants.
- `thread/read(includeTurns: true)` uses safe native pages at one cut for its
  explicit full-history request. Ordinary thread documents carry no cached
  `state.turns` body; cold hydration uses the paging methods.

Each registered object size remains its physical read bound. The optional
server-constructor `history_object_max_bytes` and `history_response_max_bytes`
budgets fail an entire request when exceeded. Both default to None; no implicit
4 MiB cutoff or silent body truncation is introduced. Page/refs counts are
bounded, but every native page validates lineage: total scan CPU/memory and
aggregate history are not claimed to be constant or bounded by page count.

## Stable wire IDs and upgrade behavior

The stock Rust client parses threadId as a UUID even though its JSON schema
only says string. Codex now renders the 128 bits of the existing deterministic
`ses_<hex>` presentation identity as a canonical UUID. Generic frontend IDs,
Registry identities, roots and persisted facts are unchanged. The old Codex
sidecar protocol ID was already non-authoritative and is ignored on load;
existing sessions regenerate their display cache without deleting history.
Old `ses_` requests/cursors are rejected, not treated as aliases. Reopen the
same canonical session through the updated frontend to obtain its UUID.

Turn/item IDs use the existing live namespace and real native ordinal,
including failed/interrupted gaps. Terminal notifications and cold items
therefore share IDs rather than reindexing committed turns.

0.161 adds an exclusive object item anchor (`{type: "item", itemId: ...}`),
optional item timestamps and optional resume fields. The 0.155 adapter rejects
that newer input shape. Real stock 0.155 cold-resume testing remains a separate
native gate; pure JSON/Registry tests cannot certify transport or TUI behavior.
