---
name: rpnh-current-frontend-validation
description: "Dated compatibility boundaries for current Codex, DSH and OpenCode clients."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: frontends-20261019_ZH.md
  revision: "2026-10-19.1"
  status: bounded-offline-validated
---

[English](frontends-20261019.md) | [中文](frontends-20261019_ZH.md)

# Current frontend compatibility — 2026-10-19

This update targets the published stable clients where available. Upstream
development versions are recorded separately. RPNH remains responsible for
model selection, execution, tools, Registry state and checkpoint recovery.

| Frontend | Selected upstream | Boundary |
|---|---|---|
| Codex | `@openai/codex` / `codex-cli 0.163.0` | Exact remote TUI profile; native-generated protocol schemas |
| OpenCode | `opencode-ai 1.19.3` | Stock attach TUI; no OpenCode provider/tool backend |
| DSH | `687f282e8625c6b8d11ff58211a90524be2e4317`, `0.2.1-rc.2` | Prepared source host and explicit current message dialect |

All three selected hosts completed bounded native offline checks. No live
provider experiment is included. Schema checks and native observations are
reported separately; the test windows below overlap and are not additive.

## Implementation and executed checks

Codex now defaults to the exact `pinned-0.163.0` profile. The 0.155 legacy
and 0.161 candidate profiles remain explicit test selections, without automatic
version fallback. The current profile accepts object item cursors and explicit
resume defaults. Native startup required a non-Bedrock advisory response;
cold resume additionally required a null goal read and client unsubscribe.
Unsubscribe suppresses only that client's notifications. It leaves RPNH work
and Registry history intact; presentation resume reattaches notifications.

The real 0.163.0 TUI displayed a deterministic local-process reply, interrupted
a second synthetic turn through the real Registry, and displayed the committed
answer after cold reopening with a fresh client home and server process.
Final startup/cold-resume/exit windows returned zero RPC errors and exit 0.
The final affected Codex/protocol/boundary selection passed **57 tests** in
32.66 s. The broader first window had 254 passes and one invalidated dynamic
source inspection while the handler file was being edited; the affected files
were rerun after the source stopped changing. No interrupted or invalidated
window is presented as a complete passing suite.

OpenCode 1.19.3 requires mixed legacy APIs and eight location-scoped `/api/*`
catalog reads, plus Console discovery. The current protocol/profile selection
passed **236 tests** in 2.06 s. Real stock-client checks passed for fresh
session/help/prompt/SSE rendering and two cold Registry attaches with stable
message identities and PN views. The prompt/SSE contract uses an application
double; the cold-read lane uses a real Registry with deterministic fixture
turns and no measured new model, submit or spawn effects. See the detailed
[OpenCode record](opencode-20261019.md).

DSH uses the current V4 first-class tool-message dialect. The current source
host ran a complete deterministic headless turn: **Sum: 12**, three local model
dispatches, two native tool dispatches, one committed turn and no active turn.
The bounded Python selection passed **49 tests** in 69.89 s, and the TypeScript
codec passed **56 tests**. Selected native checks covered correlated tool DTOs,
pre-admission rejection and malformed/unmatched result rejection. Remaining
native cases were explicitly unselected. An earlier Python window was
interrupted after 20 passes; it is not a full-suite pass. Old revision histories
remain readable, while execution still requires the original exact revision.
The first complete native window had 15 passes and two stale-fixture failures:
the tool round still used V3, and the rejection fixture used the old 3 MiB
budget. Replacing these with the upstream V4 tool-result constructor and the
existing production `LIMIT` made the affected checks pass. The production
admission gate was not weakened; the original errors and JUnit were retained.

Changed bilingual documentation passed metadata/pair, local link/anchor and
code-block syntax checks. CLI help uses the actual compatibility pins.

## Retained failures and limits

The installed local Codex wrapper injected `--add-dir`, which 0.163.0 rejects
with remote mode. The actual stock launcher worked via existing
`RPNH_CODEX_BIN`; no external wrapper/configuration was changed. An initial
synthetic adapter emitted a noncanonical JSON envelope and was corrected
before the accepted deterministic turn. Original failed attempts were retained.
OpenCode's initial exact-pin rejection, missing test dependency, unsupported
bootstrap routes and viewport assertion failure are recorded separately in its
detailed validation page.

This is Linux/WSL2 offline acceptance. It does not certify all native commands,
picker combinations, long-running reconnect/recovery behavior, desktop/browser
frontends, live-provider quality or billing, or every future upstream version.
Existing dated paid-provider results and diagrams retain their original
versions. Updating the presentation host does not migrate old task authority.

## Upstream references

- [Codex 0.163.0](https://github.com/openai/codex/releases/tag/rust-v0.163.0)
  and [app-server protocol](https://learn.chatgpt.com/docs/app-server).
- [OpenCode 1.19.3](https://github.com/anomalyco/opencode/releases/tag/v1.19.3).
  The observed development package `2.0.32` is not the selected stable client.
- [DSH source](https://github.com/deepseek-ai/deepseek-harness/tree/687f282e8625c6b8d11ff58211a90524be2e4317).
  npm `latest` is `0.2.0-rc.2`; `next` is `0.2.1-rc.2`. The integration uses
  the exact source revision, rather than silently tracking either npm tag.

See [installation](../guides/installation.md), [Codex and DSH adapters](../guides/adapters.md),
[OpenCode](../guides/opencode.md) and [DSH](../guides/dsh.md) for launch commands.
