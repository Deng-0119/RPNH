---
name: rpnh-frontends
description: "Choose, configure and switch RPNH frontends with session-specific resume."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: frontends_ZH.md
  revision: "2026-10-19.5"
  status: source-reviewed
  basis: "Frontend selection, setup, Basic terminal and same-session handoff; validation recorded separately"
---

# Choose, configure and switch frontends

[English](frontends.md) | [中文](frontends_ZH.md)

Run `rpnh` in a terminal to choose Codex, OpenCode, official DSH Web or the
built-in Basic terminal for a new conversation. The menu shows which clients
are ready. Selecting an unprepared frontend connects or installs its supported
version before opening a session. Basic needs no separate installation.

On first use, `rpnh init` walks through model setup and frontend selection.
You can also configure a frontend before configuring a model:

```bash
rpnh frontend
rpnh frontend list
rpnh frontend setup codex
rpnh frontend setup opencode
rpnh frontend setup dsh
rpnh frontend setup basic
```

`rpnh frontend` and `setup` configure a choice without opening a conversation
or calling a model. Compatible installed clients are reused. Missing clients
are installed into `frontends/` beside the RPNH preferences, leaving global
client installations unchanged. Codex and OpenCode installation requires Node
and npm. DSH needs Git and Node `^22.19.0 || >=24.0.0`; setup prepares its pinned
source, pnpm dependencies and official Web assets. Missing prerequisites are
reported with instructions; a failed setup does not save an unusable choice.

To connect an existing installation explicitly:

```bash
rpnh frontend setup codex --codex-bin /absolute/path/to/stock/codex
rpnh frontend setup opencode --opencode-bin /absolute/path/to/opencode
rpnh frontend setup dsh --dsh-source /absolute/path/to/deepseek-harness
```

Frontend setup is separate from model and credential setup. Switching from
Codex to DSH changes the interface, while the session retains its exact RPNH
provider, model, history and registered task state.

## Basic terminal

Start with `rpnh --frontend basic`. Basic adds no UI dependencies or separate
client installation. In an interactive terminal, its welcome panel shows the
configured model, current working directory and session. An input separator
and status keep the next prompt distinct from previous output.

Where Python's `readline` is available, edit the current line, use Up/Down to
recall input from this process, and press Tab to complete basic slash commands.
This input history is separate from registered conversation history and is not
saved between processes. End a line with a backslash (`\`) to continue the
same message on another line; submit its final line without the backslash.

`/help` shows only these basic commands:

| Command | Effect |
|---|---|
| `/help` | Show basic help |
| `/clear` | Clear the display without deleting conversation history or resetting the session |
| `/status` | Read main-session identity, model, focus and turn state; when a child is selected, retain its existing task-status behavior |
| `/history` | Show up to the last 10 registered conversation messages |
| `/frontend [NAME]` | Choose or name a frontend for the same session |
| `/resume` | Explicitly resume a paused main turn or settle its terminal result; for a selected child, use its existing resume control |
| `/rollback` | Return a paused main turn to the prior completed turn |
| `/quit` (alias `/exit`) | Leave Basic |

`/status` in the main-session focus is read-only. `/clear` does not delete
Registry records, and `/history` does not replay messages or call a model.
`/help tasks` lists the retained advanced controls, including `/agent`,
`/workflow`, `/tasks`, `/switch` and `/task ID ...`. These task controls remain
usable but do not appear in default help or basic command completion. See
[usage](usage.md) for task examples.

`Ctrl+D` on an empty input signals EOF and exits. `Ctrl+C` at the prompt exits
with status 130. During agent execution, the existing checkpoint-pause behavior and explicit
resume/rollback controls still apply. Changing the frontend does not change the
session's model or recovery semantics.

## Resume and switch

```bash
rpnh --session-dir ./my-session
# After leaving the interface, reopen it using the same frontend:
rpnh --resume ./my-session
# Or choose another frontend for that same session:
rpnh frontend switch opencode --resume ./my-session
```

Resume uses the session's last successfully adopted frontend, independently
of the preferred choice for new sessions. Explicit `--frontend NAME` overrides
that memory. Older sessions without frontend memory show the choice menu in
an interactive terminal. Opening history does not automatically continue a
paused execution.

In Basic, enter `/frontend` to choose another interface, or `/frontend codex`,
`/frontend opencode` or `/frontend dsh` directly. Basic releases its ownership
before the next frontend opens the same session. In other interfaces, leave
the current interface, then use `rpnh frontend switch NAME --resume PATH`.
The one-owner rule still rejects simultaneous writable interfaces.

`rpnh frontend switch codex` without `--resume` configures the preferred choice
for the next new-session menu. It does not take over an already open interface.

## Scripts and explicit choices

`rpnh --frontend NAME` selects an interface without the new-session menu.
In an interactive terminal, a missing selected client enters setup. Scripts
should configure clients with `rpnh frontend setup NAME` first; launch does not
download clients or prompt when stdin is not a terminal.

`--prompt` remains a Basic one-shot run without frontend selection. One-shot
runs, redirected input/output and `TERM=dumb` use plain output without ANSI
prompt effects, the welcome panel or interactive input decoration. A script
with neither an explicit frontend nor a configured preference gets setup
guidance. Explicit `--frontend auto` retains the earlier compatible-Codex-or-Basic
selection; the ordinary interactive `rpnh` command presents the menu.
