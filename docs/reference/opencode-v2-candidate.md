---
name: rpnh-opencode-v2-candidate
description: "Define the explicit OpenCode 2.2.5 protocol candidate and unverified native boundaries."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: opencode-v2-candidate_ZH.md
  revision: "2026-11-01.1"
  status: offline-protocol-candidate
  basis: "fixed upstream source; no native certification"
---

[English](opencode-v2-candidate.md) | [中文](opencode-v2-candidate_ZH.md)

# OpenCode 2.2.5: explicit protocol subset, not a production frontend

The installed frontend remains pinned to **1.19.4**. The separate
`OpenCodeV2Protocol` requires the explicitly resolved 2.2.5 certification profile;
the old protocol rejects that profile rather than silently serving old wire types.
There is no environment selection or default upgrade. This candidate is **not a
complete native boot or live-chat certification** and is not exposed by the
production launcher.

The source target is official `v2.2.5`, commit
`9bbb8ce18c80f099e0e448775f59d965ead40a21`. Checked contracts:

- `packages/cli/src/commands/commands.ts`: positional directory plus `--server`.
- `packages/cli/src/services/server-connection.ts`: `OPENCODE_PASSWORD`, fixed
  Basic username `opencode`, initial `/api/info`; explicit server does not fall
  back to a personal managed daemon.
- `packages/client/src/promise/generated/client.ts` and `types.ts`: REST response
  envelopes, SessionInfo, SessionInboxUser, history messages, interrupt and events.
- `packages/client/src/solid/connection.ts`: first event must be `server.connected`;
  keepalive bytes maintain its watchdog, reconnect does not authorize submission.

## Implemented offline subset

The original `FrontendGateway` remains the only application owner. Candidate
routes are `/api/info`, `/api/session` create/list, `/api/session/active`, session
get, `prompt`, `message` history, `interrupt`, and `/api/event` connection/status.
Text input maps its original `id` to the existing request identity; unchanged
repeats reuse the committed request, conflicting input is rejected by the owner.
Busy sessions remain busy: the wire inbox's `delivery: queue` labels accepted
input only and does **not** add a deferred execution queue. Explicit `delivery`
options, checkpoint resume, files, agents, skills, nonempty metadata, permission
changes, native tools, PTYs, schedules and unsupported catalog routes fail closed.
Select model/effort only through the existing RPNH profiles at session creation.

History reads project safe registered answers/paused observations, not raw provider
content. Required numeric session metrics use explicitly labelled zero display
sentinels, not measured cost or token usage. Cursor pagination is not implemented:
requests needing a partial page fail rather than claiming complete history.

`frontend_launch_spec(..., profile=...)` constructs version-specific argv and
session-local authentication for isolated certification tooling; it does not start
a client. The production launcher still passes only its 1.19.4 default profile.
The candidate HTTP server accepts only its own `opencode` credential, not the
legacy `rpnh` username; loopback Host/Origin and request bounds remain unchanged.

## Explicitly incomplete

Only official **non-durable** `server.connected` and `session.status` events are
projected. Native durable message events need the upstream aggregate event
sequence; a Registry turn ordinal is not that sequence. This candidate does not
invent a second event ledger or fabricate durable metadata. Callers must refresh
REST history. Native UI live message refresh, catalog/bootstrap completeness,
attachment intake, version-pinned native TUI, real owner PTY/IPC execution and live
provider/model behavior remain unverified or unsupported. Local loopback HTTP/SSE
roundtrips are tested only against a scripted application gateway, not a native client. No native frontend,
software, provider or model was installed/called for the offline test suite.

The schema test fixture is a reviewed source-extracted subset, **not upstream
schema-generator output**, and is not evidence of native certification.

### Message identity and history evidence

The upstream runtime schema requires `msg_` message IDs. New explicit IDs must
also satisfy the existing RPNH request-ID grammar; they are echoed unchanged.
Generated user IDs and assistant IDs use `msg_rpnh/{session}/{ordinal}/user`
and `/assistant`. The slash is forbidden in explicit request IDs, so the role
namespace cannot collide with a caller ID, including old `.rpnh-answer` suffixes.
These are reversible display projections of existing references, not new facts,
hashes or stored identity state. Rebuilding the protocol preserves the IDs.
Existing incompatible explicit IDs from other frontends fail history projection
with 409, without rewriting records or pretending they satisfy the v2 schema.
Model evidence/display-basis metadata is retained; legacy current-selection
fallback is not presented as proven historical model selection.

### Legacy 1.19.4 identity guard

The default legacy projection retains all noncolliding IDs byte-for-byte.
For message/prompt_async admission, the independent protocol-instance admission mutex covers
a fresh snapshot, role-ID collision check, and owner submit (not model execution).
A new explicit user/derived-answer ID overlapping another turn is rejected before
submit. Same-key retries still use the original owner dedup/conflict checks.
Already-colliding history, including identities reserved for interrupted turns,
fails explicitly with 409 rather than rewriting IDs or emitting ambiguous events.
This is not a global owner identity mechanism: separate protocol instances or
other command/frontends can still introduce conflicts, which fail closed on read.
