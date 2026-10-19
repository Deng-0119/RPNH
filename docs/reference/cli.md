---
name: rpnh-cli-reference
description: "Locate installed commands, exact option families, execution effects and task-control semantics."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: cli_ZH.md
  revision: "2026-10-19.2"
  status: source-reviewed
  basis: "current source for unified DSH and preferences; older source links retain their revision; not a new runtime validation"
---

[English](cli.md) | [中文](cli_ZH.md)

# Command-line reference

[Documentation home](../index.md) · [First run](../guides/getting-started.md) · [API map](api-index.md)

This map follows the installed parsers in the linked source. Use the matching
artifact's `--help` for full argument help. Shell commands, Basic slash commands,
and the controlled project HOST are separate interfaces. Uppercase arguments
below are placeholders, not literal task IDs or paths.

## Installed entry points

| Entry | Purpose | Source and guide |
|---|---|---|
| `rpnh` | User sessions, configuration, examples, plugins, packages and Viewer | [Parser/dispatch](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py), [usage](../guides/usage.md) |
| `rpnh-configure-providers` | Catalog/profile generation helper | [Entry declaration](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/pyproject.toml), [model setup](../guides/models.md) |
| `rpnh-dsh CHECKOUT ...` | Launch the advanced legacy registered headless host; separate root/session semantics | [Launcher](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/launcher.py), [DSH](../guides/dsh.md) |

`python -m cpn` is a lower-level entry used by existing source examples and
projection tooling; see its [parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/cli.py). Prefer `rpnh` for
ordinary user sessions. Project collaboration uses its own explicit module
entry, described below; there is no `rpnh project` command in this source.

## Sessions

```text
rpnh [--execution PATH] [--save-default]
     [--session-dir NEW_DIR | --resume SESSION_DIR]
     [--prompt TEXT] [--frontend auto|basic|codex|opencode|dsh]
```

| Option | Meaning |
|---|---|
| `--execution PATH` | Explicit execution profile. On resume it must match the persisted exact profile |
| `--save-default` | Save the supplied explicit profile as the user default, then start |
| `--session-dir NEW_DIR` | Absent operational directory for a fresh session |
| `--resume SESSION_DIR` | Reopen the original session root; mutually exclusive with `--session-dir` |
| `--prompt TEXT` | Run one main-session turn and leave the input loop; can execute models/tools |
| `--frontend ...` | Explicit flag overrides saved preference; default `auto` selects compatible Codex in an interactive terminal or Basic otherwise; OpenCode and DSH require an explicit flag or saved preference |

A fresh session without `--execution` uses `RPNH_EXECUTION_CONFIG` or the saved
default. Resume without it uses the session's persisted selection. `--prompt`
uses Basic; explicit OpenCode or DSH plus `--prompt` is rejected before model
resolution. For required client versions and supported platforms, use [installation](../guides/installation.md)
and [adapters](../guides/adapters.md), not the latest upstream client by default.

Opening a session, submitting input, and explicitly continuing a paused turn
are different operations. See [usage](../guides/usage.md) before scripting
session lifecycle or switching writable frontends.

## Setup and configuration

| Command | Effect |
|---|---|
| `rpnh init` / `rpnh config setup` | Interactive model setup/selection; requires a terminal; writes local configuration |
| `rpnh config add` | Interactive addition of a model |
| `rpnh config init [--catalog PATH]` | Create an empty canonical catalog |
| `rpnh config build [--catalog PATH] [--output-root DIR] [--check]` | Generate profiles; `--check` verifies existing generated state without writing it |
| `rpnh config list` / `rpnh config show` | Read generated profiles / current selection |
| `rpnh config use PROFILE_OR_PROVIDER [MODEL] [--effort EFFORT]` | Persist one declared profile or exact provider/model/effort selection |
| `rpnh config frontend [auto\|basic\|codex\|opencode\|dsh] [--dsh-source CHECKOUT]` | Show or save the frontend preference independently of model setup; no provider call or model-selection change |
| `rpnh doctor [--execution PATH] [--json]` | Offline local checks; no provider request |
| `rpnh config doctor [--execution PATH] [--json]` | Same local doctor boundary |

Frontend preferences are stored beside `config_path()` (selected by
`RPNH_CONFIG`, or `~/.config/rpnh/config.json`): the file is named
`config_path().name + ".frontend.json"`, for example `config.json.frontend.json`.
`DSH_SOURCE` overrides a saved `--dsh-source`; saving a source normalizes its
path but does not prepare or check it. No preference means `auto`.

`doctor` returns 0 when locally ready and 1 when not ready; a ready result does
not certify a live endpoint. Setup/parser errors are distinct from readiness.
See [configuration](../guides/configuration.md), [models](../guides/models.md),
and the [implementation](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py).

## Installed examples

| Command | Effect |
|---|---|
| `rpnh examples list` | Read installed export catalog and source-only prerequisites |
| `rpnh examples export --output NEW_DIR [--example NAME]` | Copy files to an absent destination; defaults to `adapter_task` |
| `rpnh examples verify --result FILE [--expected FILE]` | Validate an answer-only batch-summary JSON against stock or explicit expectations |

Export does not install dependencies or run examples. `verify` is the
batch-summary task's verifier, not a universal verifier for every gallery entry.
Sources: [example parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py),
[catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json). Follow the
[example guides](../guides/examples.md) for each task's actual run and verifier.

## Native plugins

```text
rpnh plugins [--config CONFIG] list
rpnh plugins [--config CONFIG] inspect [--diagnose-selection]
rpnh plugins [--config CONFIG] check
rpnh plugins [--config CONFIG] build PLUGIN/OPERATION
rpnh plugins [--config CONFIG] run PLUGIN/OPERATION --input FILE --run-dir NEW_DIR
```

Place `--config` **before** the subcommand; `RPNH_PLUGIN_CONFIG` is the explicit
environment alternative. `inspect` reads inert installed metadata and selection.
`list`, `check` and `build` load selected trusted factories; check/build compile
without invoking operations. `run` invokes the handler and writes run evidence.
A pure no-model operation still executes code. See [customization](../guides/customization.md),
[plugin contracts](extensions-observation.md) and [parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/cli.py).

## Read-only net and result inspection

Choose exactly one source: `--run RUN_DIR` or `--read-host-config FILE`.

| Mode | Options and effect |
|---|---|
| `rpnh net --run RUN_DIR` | Text projection by default; `--format json`, `--show-resources`, `--resources-only`, `--node ID`, `--output FILE` select projection/output |
| `rpnh net --run RUN_DIR --result-evidence` | Read registered request/result evidence and existing stop limits |
| `rpnh net --run RUN_DIR --view` | Start the read-only Viewer; optional `--show-resources`, `--no-open`, `--host`, `--port`, `--max-checkpoints`, `--max-firings` |
| `rpnh net --read-host-config FILE --preflight` | Open/describe/close the selected independent read-HOST without a listener or object query |
| `rpnh net --read-host-config FILE --view` | Open the authorized read-session comparison Viewer |

`--view`, `--preflight` and `--result-evidence` are mutually exclusive.
`--view` rejects `--resources-only`, `--node`, `--output` and a non-default
`--format`. Inspection modes reject resource/viewer projection options;
`--preflight` requires a read-HOST and `--result-evidence` requires a run.

Viewer defaults are `--host 127.0.0.1`, `--port 0`, `--max-checkpoints 2048`
and `--max-firings 2000`. The two limits apply to the run-backed Viewer path.
`--no-open` prevents browser launch, not the listener; `--output` writes a file.
Keep the Viewer loopback-bound. See [Viewer](../guides/viewer.md),
[independent reads](../guides/independent-registry-reader.md), and
[mode validation](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py).

## Portable packages and receiver environments

```text
rpnh package preview ZIP
rpnh package resolve ZIP [--local-package ZIP ...] [--entry ENTRY_ID]
```

Preview and resolution are data-only and do not execute package contents or
install dependencies. Resolution defaults to entry `main` and prints the exact
lock bytes; retain that exact lock. They are different from `examples export`
and from Registry subnet exchange.

| Environment command | Required inputs beyond the common package material |
|---|---|
| `check-environment` | `--selection FILE` or `--binding FILE`; optional `--resolved-selections FILE` |
| `resolve-environment` | Selection/binding plus `--check FILE`; optional repeated `--wheel FILE` and `--allow-existing-changes` |
| `plan-environment` | Selection/binding, `--check FILE`, `--resolved-selections FILE`; optional repeated `--wheel FILE` |
| `prepare-environment` | Selection/binding, `--check FILE`, `--resolved-selections FILE`, `--plan FILE`; optional `--state-dir DIR` |
| `run` | `--binding FILE`, `--resolved-selections FILE`, `--receipt FILE`, `--owner-request FILE`, `--run-dir NEW_DIR` |
| `setup-instructions` | `--plan FILE --output FILE`, optional `--format text|json`; no common package-material arguments |

For the first five rows, common material is `--lock FILE --archive ZIP`, with
optional repeated `--local-package ZIP` and `--entry ENTRY_ID`. They accept
`--output FILE` for private complete documents and refuse overwrites.
`run --include-terminal-result` additionally requires private `--output`.

Check/resolve/plan do not grant preparation or execution permission.
`prepare-environment` asks for the exact plan identity; `run` separately asks
for the exact business-run authorization. These confirmation paths require an
interactive terminal. Environment exit codes: 0 completed for the checked
scope, 2 invalid input/contract, 3 unresolved or unsupported requirements,
4 authorization/preparation failure, 5 stale binding or cancelled preparation.
An environment-check pass is not business completion.

Use the complete [package tutorial](../guides/package-reuse-example.md) and
[environment preparation](../environment-preparation.md) instead of inventing
lock/selection/receipt documents. Sources:
[package parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/package_cli.py),
[environment parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_cli.py).

## Basic terminal controls

Enter these inside `rpnh --frontend basic`, not at a shell or in a stock
Codex/OpenCode slash-command registry:

| Control | Meaning |
|---|---|
| `/help`, `/tasks`, `/current` | Help, linked child task list, selected focus |
| `/agent PROMPT`, `/workflow PROMPT` | Launch an independent agent / request a Designer workflow; may execute models/tools |
| `/switch ID`, `/switch main` | Change focus without stopping children |
| `/task ID status`, `/task ID result`, `/task ID checkpoints` | Inspect task state, registered result or exact committed cuts |
| `/task ID net [view [--show-resources] [--no-open]]` | Summary or read-only Viewer |
| `/task ID message TEXT` | Queue single-agent input |
| `/task ID message TARGET :: TEXT` | Queue input to an explicit workflow target |
| `/task ID stop`, `/task ID resume` | Request checkpoint stop / continue the current owner-stopped cut |
| `/task ID reopen CHECKPOINT [:: REASON]` | Append a generation from an exact selected cut; may execute models/tools |
| `/status`, `/result`, `/net`, `/message`, `/stop`, `/resume` | Act on the selected child; `/resume` has separate main behavior |
| Main `/resume`, main `/rollback` | Settle/continue a paused main turn; or return main conversation to its previous completed turn |
| `/quit` | Exit the frontend without stopping independent tasks |

Plain text goes to main or the selected single-agent; selected workflows need
`TARGET :: TEXT`. There is no short `/checkpoints` or `/reopen` alias in this
parser: use `/task ID ...`. Stop acceptance is not a completed checkpoint.
Resume, checkpoint reopen and rollback have different data/effect semantics;
use the [recovery decision guide](../guides/checkpoint-recovery.md) and
[unknown-effect handling](../guides/troubleshooting.md#stop-resume-and-unknown-effects).

## DSH Web controls

`rpnh --frontend dsh` opens an RPNH Web frontend built with native DSH
chat/composer/theme widgets and AgentLoop/Session. It is not the full stock DSH
Web shell or a TUI; the pinned upstream has no bundled terminal TUI. Use the
printed loopback URL if automatic browser opening fails. `Ctrl+C` exits the
host and checkpoint-stops only its owned main turn. Basic, Codex, OpenCode and
DSH Web share `--session-dir`/`--resume`, Registry history and one writable owner.
Reopening history does not automatically continue a paused turn.

Enter these in the Web composer; `/rpnh-help` gives full arguments:

| Control | Meaning |
|---|---|
| `/rpnh-help`, `/rpnh-tasks` | Shared help and linked task list |
| `/rpnh-task ID ...`, `/rpnh-net` | Task operations and net inspection |
| `/rpnh-agent TEXT`, `/rpnh-workflow TEXT` | Launch independent work; may execute models/tools |
| `/rpnh-resume`, `/rpnh-rollback` | Explicitly continue a paused main turn or roll main conversation back to the previous completed turn |
| `/rpnh-send UNIQUE_ID TEXT` | Submit an explicitly identified main input |

The Stop button requests a checkpoint stop. Model/effort controls use the RPNH
catalog; native DSH providers, tools and workspace filesystem are not loaded.
See [DSH setup and limits](../guides/dsh.md) and the
[unified validation record](../validation/dsh-unified-20261019.md).
`rpnh-dsh` remains a separate advanced legacy headless host; its `--root`,
`--session-id`, frame and attempt-budget options are documented in that guide.

## Controlled project HOST

The documented local project entry is
`python -m cpn.rpnh.collaboration.project_local`, with `init`, `serve` and `call`.
It hosts explicit controlled-principal project operations. JSON request
operations such as propose, review, merge, accept, adopt, read-plan and download
are not general shell subcommands and do not create remote account authentication.
Read the [project operator guide](../guides/project-entry.md) for full fields,
authority, exact references, registration and runtime execution. Sources:
[local HOST](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_local.py),
[request contracts](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py).
