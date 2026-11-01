---
name: rpnh-codex-0166-candidate
description: "Inspect the explicit Codex 0.166 frontend protocol candidate without changing the production pin."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: codex-candidate-0166_ZH.md
  revision: "2026-11-01.1"
  status: source-candidate
  basis: "fixed upstream schema; deterministic offline protocol checks"
---

[English](codex-candidate-0166.md) | [中文](codex-candidate-0166_ZH.md)

# Codex 0.166 frontend candidate

The production frontend remains exactly **0.163.0**. `candidate-0.166.0` is an
explicit trusted-host Python keyword selection on `CodexAppServer`,
`resolve_codex_binary` and `run_codex_frontend`, not an automatic version-range,
environment, CLI preference or client-selected upgrade. Both the binary probe
and the `codex-tui` initialize handshake still require the selected exact version.

The candidate uses the existing RPNH session/Registry history projection. Its
selected request, response and notification contracts were read from official
`rust-v0.166.0`, commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`.
See [fixture provenance](../../tests/fixtures/codex/0.166.0/README.md) for the
38-schema subset and the 0.163 comparison. No required response adaptation was
needed for this tested subset; adding a profile does not claim the full native
client is certified. Unimplemented memory, process, MCP and attachment methods
remain rejected. Empty capability discovery is not installed capability.

Reopening frontend history does not resume a paused business task or resend its
prompt. Existing owner binding, immutable Registry history, safe text-only
projection, exact item cursors and interruption/terminal-race behavior remain.
The `:danger-full-access` wire label does not enlarge Registry grants or isolation.

The separate `codex_subscription_bridge` **exec JSONL model endpoint is unchanged**.
`RPNH_CODEX_BIN` chooses the frontend; it does not replace the configured backend
executable or permit backend tools. The backend still permits only agent-message
and reasoning items and rejects built-in execution items.

The initial offline checks used real temporary Registry history and fake transport/lifecycle
boundaries. The interruption/terminal case supplies deterministic fake session
reconciliation; it is not an actual Registry or native-client race test. That initial window ran no model API, installation, stock 0.166 binary, native UI or full
Unix-socket session. Native qualification and any promotion of the default
require separate evidence and authorization; this candidate is not a release.

## Later bounded native evidence

Official 0.166.0 was subsequently exercised in two real PTY sessions against idle committed Registry history. The launcher now passes `resume` and the exact bound thread ID when reopening; fresh startup is unchanged. Initialization, history and cold reconnect/unsubscribe preserved one thread, two turns and four item IDs, with no new turn or model call. This is not active-task survival, business-checkpoint recovery, default 0.163 native recertification or full client qualification. See the [source validation note](../validation/host-adapter-source-20261101.md) for failures and limits.
