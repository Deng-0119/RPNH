---
name: rpnh-codex-0161-candidate
description: "Explicit Codex 0.161 protocol selection and its finite scope."
metadata:
  document-kind: guide
  audience: developer
  language: en
  counterpart: CODEX_0161_CANDIDATE_ZH.md
  revision: "2026-10-09.2"
  status: maintained
---

[English](CODEX_0161_CANDIDATE.md) | [中文](CODEX_0161_CANDIDATE_ZH.md)

# Codex 0.161 protocol candidate

This is a source/schema-qualified candidate, with native certification pending.
The normal RPNH CLI and all existing callers keep the exact Codex 0.155.0 default.
Selecting this profile does not install a client or alter Registry authority.

The existing `cpn/frontend/codex_compatibility.v1.json` is the only capability table.
Its `pinned-0.155.0` default and explicit `candidate-0.161.0` profile are selected
by the optional `compatibility_profile` keyword on `CodexAppServer`,
`resolve_codex_binary`, and `run_codex_frontend`. This is a controlled candidate
gate entry, not a new ordinary CLI flag or an environment-based automatic upgrade.
Unknown selectors, arbitrary version strings and mismatched CLI/initialize versions
are rejected. The launcher verifies only the selected binary; it never installs,
downloads, tries another client, or changes global configuration. A server holds
one immutable profile; a client's self-reported version cannot select capabilities.

The 0.161 profile supports the new `thread/items/list` object anchor
`{"type":"item","itemId":"..."}` only with a bounded, nonempty `turnId`.
The exact committed turn and one of its two existing safe slots must belong to
one newly captured cut. The adapter uses a refs-only lookup and converts the
position to the existing exclusive native anchor. Output continuation cursors
remain the same `rpnh-history-v1` strings. Existing strings preserve their original
cut and remain revalidatable across either exact profile at the same source/root.
An object contains no old cut and therefore cannot inherit a previous resume cut.

Each committed turn has exactly two safe items: original user text and the
safe main-decision reply. After an exclusive object anchor at most one remains;
`nextCursor` is null. A nonempty result has the existing reverse/inclusive string
cursor. There are no new slots, cross-turn anchor searches, Registry stores,
body readers, grants, aliases, or persistent cursor tables. Failed/pending/active,
tool/child items, extra object fields and nonexistent IDs fail closed.

0.161 history entries explicitly return `startedAtMs: null` and
`completedAtMs: null`. Resume returns `collaborationMode: null` and
`disabledPluginIds: []`. These represent absent timing/default-only mode/empty
plugin scope; they are not fabricated historical facts or claims of Plan-mode
or plugin-state restoration. Existing 0.155 wire output remains unchanged.
Required live-notification integer timestamps are unchanged and cannot be omitted.

Use an already installed binary reporting exactly `codex-cli 0.161.0` with
`compatibility_profile="candidate-0.161.0"`; the default profile requires exactly
`codex-cli 0.155.0`. The binary path comes from the explicit argument,
`RPNH_CODEX_BIN`, or `codex` on PATH, in that order. A missing or mismatched
binary raises a compatibility error. Selecting a profile only selects the
adapter protocol; the installed binary must independently match it. General
frontend setup is described in the [adapter guide](guides/adapters.md).

The [2026-10-09 finite validation](results/product-validation-20261009/README.md)
records source/protocol checks, stock clients as NOT_RUN and complete native
fixture binding as BLOCKED. It does not certify an installed 0.161 client.
Pure JSON/Registry tests and source audits establish neither a Rust build,
real-provider validation nor complete API coverage. The deterministic protocol
cases are in `tests/test_codex_0161_candidate.py`; an object-cursor RPC fixture
is not evidence that a stock TUI sent that request.
