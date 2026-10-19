---
name: rpnh-dsh-unified-validation
description: "Unified DSH frontend selection, shared MainSession and offline validation."
metadata:
  document-kind: validation
  audience: operator-and-developer
  language: en
  counterpart: dsh-unified-20261019_ZH.md
  revision: "2026-10-19.1"
  status: offline-validated
---

[English](dsh-unified-20261019.md) | [中文](dsh-unified-20261019_ZH.md)

# Unified DSH frontend — 2026-10-19

`rpnh --frontend dsh` selects a Web presentation built from native DSH chat,
composer and theme components, with DSH AgentLoop/Session lifecycle objects.
It connects to the same direct RPNH MainSession as Basic, Codex and OpenCode.
It is not the complete upstream Web shell. The pinned upstream does not bundle
a terminal TUI.

The target is DSH source `687f282e8625c6b8d11ff58211a90524be2e4317`,
`0.2.1-rc.2`, on Linux/WSL2 with Node `22.22.1`. The earlier
[frontend compatibility record](frontends-20261019.md) documents the previous
headless DSH host and other clients; this record adds the unified presentation.

## Behavior

- `rpnh config frontend dsh --dsh-source CHECKOUT` saves a frontend preference
  independently of provider/model configuration. Explicit `--frontend` wins;
  `auto` retains its Codex-or-Basic behavior. `DSH_SOURCE` overrides a saved source.
- Startup validates the pinned source and dependencies, compiles the browser
  presentation, and checks assets before acquiring a MainSession owner lease.
- The native host uses two inherited private pipes. Python owns the selected
  provider, Registry writes, tools and recovery. Native DSH provider adapters,
  tools, filesystem plugins and persistent session storage are not mounted.
- History is projected from the bound MainSession. Reopening does not submit a
  prompt or resume a paused turn. `/rpnh-resume` and `/rpnh-rollback` use the
  shared application controls. Closing the host checkpoint-stops only main
  turns launched or explicitly resumed by that frontend owner.
- The existing `rpnh-dsh` registered headless host remains available with its
  own existing session format and recovery contracts.

## Checks and observed fixes

| Window | Result and scope |
|---|---|
| Python selection, CLI, launcher, private RPC and application boundary | 159 passed in 82.53 s; includes real Registry history/owner/reopen checks and local inherited-pipe child processes. |
| Native DSH and actual Chromium | Final 11 passed in 8.56 s, including the earlier eight lifecycle/HTTP/FD/input/history/stop/resume cases and three release regressions. MainSession RPC in this window is scripted. |
| Periodic owner scheduling | Before fix: one regression failed under continuous observation. After fix: three gateway checks passed, including GET observation behavior and owner-thread scheduling. |
| Built wheel | Build, separate target install, packaged frontend modules, installed help and saved frontend configuration passed. This is not a full installed-wheel suite. |
| Production launcher / real Registry / browser | Five phases passed: fresh answer; cold history and startup stop; cold checkpoint explicit resume and commit; checkpoint on exit; cold open remains paused. Two deterministic local provider calls; stop, exit and observation added none. Five explicit terminal SIGINT exits returned 130. |
| Bootstrap stop and close boundary | 27 checks passed (22 application, 5 launcher); final related window: 10 passed. Windows overlap and are not summed. |

Final review also reproduced missing Host/Origin checks and stale reasoning effort after switching from a reasoning model to a model without that setting. Three regressions failed before correction. The server now checks the exact loopback Host before serving the token, checks a supplied API Origin, and initializes UI model/effort once so polling preserves the user selection. All eleven native tests then passed.

Independent review found that a cold explicit resume did not register the newly
launched main task as owned by the frontend. Two regressions failed before the
fix. Ownership is now added only after `RESUME_STARTED`; existing running or
terminal tasks are not newly claimed. Application boundary checks passed.

Production browser testing also confirmed a startup window in which a database exists before its run identity is committed. Stop can now use the one live main-task handle registered by this frontend, then wait for an actual checkpoint. Registry validation is unchanged; foreign tasks cannot be signalled through this path. Original failure and diagnostic evidence are retained. BrokenPipeError observed while closing an exited Node peer is handled by the existing transport-error path.

Production browser testing also exposed owner scheduling that depended on an
idle request queue. Continuous DSH observations could prevent terminal evidence
from being committed. Owner reconciliation now follows a monotonic deadline;
read methods remain observation methods.

Initial failures are retained: a Chromium socket path exceeded its platform
limit; the composer lacked native editor behavior registration; font loading
needed the correct CSP; and the first local provider fixture emitted
noncanonical JSON, which the adapter correctly rejected. The fixture was fixed
without changing protocol validation. The subsequent real Registry run reached
terminal evidence and exposed the scheduling defect above.

The integration build passed and the checked host files had no remaining type
diagnostics. Whole-graph TypeScript checking still fails on upstream vendor
sources; it is not reported as a pass. No remote provider/model was called.
This record does not certify the complete stock Web application, live providers,
all platforms, or later upstream revisions.

See the [DSH guide](../guides/dsh.md) for setup and operation.
