---
name: rpnh-frontend-help
description: "Use Basic terminal controls and offline contextual help without changing a run."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: frontend-help_ZH.md
  revision: "2026-10-25.1"
  status: source-candidate
---

# Basic terminal and local help

[English](frontend-help.md) | [中文](frontend-help_ZH.md)

Start Basic with `rpnh --frontend basic`. It uses ordinary terminal scrollback,
with the selected model, session and current focus visible. This change preserves
the existing provider setup, task controls and owner lease. The current session frontend selection and `/frontend` switching remain available.

## Find help before configuring a model

```bash
rpnh help
rpnh help viewer
rpnh help recovery --language zh-CN
rpnh help provider --json
```

These commands read the installed bilingual help catalog. They do not discover
profiles, create a session, query a Registry, call a model or start a server.
The JSON form returns static help, not runtime diagnostics. Success returns 0;
an invalid help argument, unknown topic or unavailable catalog returns 2.
With `--json`, these errors are JSON on stdout; text errors use stderr.

The topics are `basic`, `viewer`, `legend`, `live`, `history`, `comparison`,
`connection`, `access`, `registry`, `recovery`, `provider`, `assets` and `layout`.
Each explains the meaning, next checks, component boundary and curated source
and documentation paths. The component identifies where to inspect; it is not
a claim that a root cause has already been proved.

## Basic controls

| Input | Effect |
|---|---|
| `/help` | Concise Basic controls. |
| `/help tasks` | Complete existing task commands. |
| `/help viewer` | Local viewer guidance; other topic IDs work too. |
| `/help viewer --language zh-CN` | Chinese topic text. |
| `/status` | Main-session identity/model/focus/turn state when main is selected; existing task status when a child is selected. |
| `/history` | Last 10 **main-conversation** messages, regardless of selected child. |
| `/frontend [NAME]` | Choose or switch this session’s frontend. |
| `/clear` | Clear an interactive display, retaining all registered history. |
| `/current`, `/switch ID`, `/switch main` | Existing focus controls. |
| `/resume`, `/rollback`, `/task ID ...` | Existing owner/checkpoint controls; see `/help tasks`. |
| `/quit`, `/exit`, EOF | Leave Basic; child tasks are not stopped. |
| `//tmp/file is relevant` | Send the literal message `/tmp/file is relevant`; one leading slash is removed. |

Unknown slash commands are rejected locally instead of accidentally becoming a
model prompt. Use `//` when the message itself must begin with `/`. One-shot
`--prompt` retains its prior output and literal-message behavior.

At the input prompt, **Ctrl+C cancels the current input, not the session**:
it displays exit guidance and accepts another input. During task execution,
the existing interruption/checkpoint behavior applies. Help does not trigger
resume or reinterpret recovery states.

In a terminal, a trailing backslash continues the same message on another line.
Piped input preserves its original one-line protocol, including a trailing
backslash. Where Python readline is available, Tab completes Basic commands and
help topics; Up/Down recalls this process's inputs. The prior readline history
and completer are restored on exit. No second history file is created.

Color and screen controls require suitable terminal capabilities. `NO_COLOR`
and `TERM=dumb` retain plain output. Displayed model/path/history/focus text
escapes terminal control characters; ordinary multiline transcript text remains
readable. This is a presentation boundary, not a claim that every legacy CLI
error or model output path has been sanitized.

## Reading the Viewer and opening help

Choose **Help & diagnostics** in the header, or press `?` outside an input. Search topics in the interface language; closing help returns focus to its trigger. View explanations, selected-object help and error remedies open contextual guidance. The independent helper survives main graph-module failure. If all JavaScript is unavailable, expand the native **Offline help** section at the bottom.

The four modes answer different questions: Overview shows agent responsibility relationships, Detail shows finer structure, PetriNet shows exact places/transitions/tokens/arcs, and Executions lists disclosed firing facts. Structure does not prove every path executed; unavailable is not zero. Expand **Compare & trace** for more controls; current source and warnings stay visible. If a large fitted graph is too small, search by name or ID and locate a readable node. Executions bypasses layout after app initialization; missing main application assets still require offline help or the terminal.

Hidden pages pause periodic reads and replay. Manual refresh pause also changes observation only. History, comparison, filtering and replay never execute, pause or restore a real task.

## Closing the local listener

```bash
rpnh net --run /path/to/existing-run --view --no-open --session-timeout 600
```

The listener binds only to literal loopback and closes **3600 seconds** after startup by default; refreshing cannot renew it. Use `--session-timeout 600` for ten minutes, or explicitly choose `--session-timeout 0` for persistence. Stop your viewer with Ctrl+C in its launching terminal. Closing the browser, hiding a tab or pausing refresh **does not close the server port**. Restart from the terminal after expiry if needed; the project owner’s `viewer-open` reopens an expired cached listener on the next explicit request. Do not broaden binding or disable Host/Origin validation to repair a connection.

Defaults allow 16 processing connections; overload returns 503. The socket/connection deadline is 5 seconds, browser read deadline 15 seconds, and response budget 16 MiB. Layout allows 4 active/queued jobs, up to 20,000 nodes plus edges per job, and 15 seconds of execution. Exceeding a limit reports a problem without silently dropping graph elements. These limits protect transport/presentation; they do not isolate trusted-provider CPU/memory or forcibly cancel a running Python provider. This server is not a public authentication gateway. The existing comparison-context POST remains a bounded read-only selection request, not a write route; other request bodies are rejected.

## Viewer helper and internal problem boundaries

The viewer and CLI use `cpn/frontend/static/help-catalog.json`. The viewer helper
can explain controls and distinguish transport, assets/layout, read access,
Registry, recovery and provider issues. Basic help works even if browser assets
cannot load. See the [viewer guide](viewer.md) for actual browser controls and
the [troubleshooting guide](troubleshooting.md) for owner/runtime boundaries.

Helpers provide checks, not automatic repairs. Never clear an owner lock, edit
historical Registry identities, change the exact saved model or replay an
unknown external effect merely to restore a view. Preserve sensitive evidence
privately and share only reviewed minimal diagnostics. A connection/read
failure remains an unconfirmed cause until the corresponding boundary is checked.

Source: `cpn/rpnh/basic_terminal.py`, `cpn/rpnh/help_catalog.py`,
`cpn/rpnh_cli.py`, `cpn/frontend/static/help-catalog.json`.
