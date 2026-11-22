---
name: rpnh-dsh-guide
description: "Use official DSH Web with a bound RPNH main session, or the advanced legacy host."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: dsh_ZH.md
  revision: "2026-11-22.1"
  status: source-reviewed-pre-release
---

[English](dsh.md) | [中文](dsh_ZH.md)

# Official DSH Web and legacy host

Use `rpnh --frontend dsh` to open official DSH Web with the same RPNH main session
as Basic, Codex and OpenCode. First configure a model through the usual RPNH
setup and prepare the pinned DSH checkout described below. Then save your
frontend preference:

```bash
rpnh config frontend dsh --dsh-source /absolute/path/to/deepseek-harness
rpnh config frontend
rpnh --session-dir ./main-session
```

For a single launch, use `rpnh --frontend dsh --session-dir ./main-session`
with a fresh directory. `DSH_SOURCE` overrides the saved checkout path.
The launcher prints a loopback URL and attempts to open it in your browser;
open that URL manually if needed. `Ctrl+C` in the launcher terminal exits the
host and requests a checkpoint stop only for its owned main turn. Independent
child tasks keep their own lifecycles.

## Save a frontend independently of models

`rpnh config frontend [auto|basic|codex|opencode|dsh] [--dsh-source CHECKOUT]`
shows or saves presentation preferences without resolving a model or changing
its selection. Preferences live beside the selected model configuration at
`config_path().with_name(config_path().name + ".frontend.json")`; for example,
`config.json` uses `config.json.frontend.json`. A supplied source path is saved
as an absolute path; this command does not prepare or validate the checkout.

New interactive `rpnh` sessions show a frontend menu; `rpnh --resume ROOT`
reuses that session's last frontend. Use `rpnh frontend setup dsh` to connect
or install the official Web frontend, or `rpnh frontend switch dsh --resume ROOT`
to reopen a session after its previous interface exits. Explicit `--frontend`
wins; explicit `--frontend auto` retains compatible-Codex-or-Basic selection.
One-shot `--prompt` uses Basic; explicit DSH plus `--prompt` is rejected before
model resolution. See [frontend setup](frontends.md).

## Shared session and controls

Create a session with `--session-dir ROOT`; after its frontend exits, reopen
that exact root with `rpnh --frontend dsh --resume ROOT` or another supported
frontend. The shared owner lease permits only one writable owner. Registry
history remains authoritative. The Web session and workspace lists expose only
the launcher-bound MainSession and its display context, not arbitrary DSH
sessions or editable workspaces.

Reopening history does not execute or continue a paused turn. Use the native
Stop control for a checkpoint stop. In the native slash-command menu, select
`/rpnh-resume` to continue or `/rpnh-rollback` to return main conversation to its
previous completed turn. `/rpnh-help` lists the shared RPNH task controls,
including `/rpnh-tasks`, `/rpnh-task`, `/rpnh-net`, `/rpnh-agent` and
`/rpnh-workflow`; use the menu and its argument input rather than submitting
commands as ordinary text. Launching or resuming work can call the selected
model and tools through Python. The native model and reasoning-effort selectors
show the shared RPNH catalog.

## What the Web interface includes

The launcher serves the official DSH `index.html` and client plugins through
DSH's native Web transport. A typed Remote BFF implements the official browser
contracts over Python's shared MainSession; generated binding code opens that
session without editing the official UI. This replaces the component-composed
Web milestone published at `e9fa433`.

Supported input is text, native model/effort selection and native slash-menu
RPNH controls. Only the bound MainSession is available. Native file uploads,
arbitrary session creation, workspace editing and native DSH local tool execution are
unsupported. The presence of an official client plugin does not grant its
backend capability. Stock DSH AgentLoop, LLM, tools and persistence runtimes
are not loaded; execution, Registry history, workspaces and recovery stay with
Python RPNH. The pinned upstream has no bundled terminal TUI.

Supported versions and operational limits are listed in [compatibility](compatibility.md).

## Prepare the pinned source

Use the dedicated writable checkout pinned by `integrations/dsh/UPSTREAM.json`:
default Web version `0.2.2-alpha.2`, upstream
`f3803c96974ad4a94195fb97e5c637e9c2a3afe3`. The opt-in Web candidates are
`0.2.2-rc.1` (`UPSTREAM_CANDIDATE.json`) and `0.2.2-rc.5`
(`UPSTREAM_RC5_CANDIDATE.json`). Each profile requires its exact checkout and
Web assets. Web requires Node `>=24.9.0` and pnpm `11.28.5`. Install the
checkout's dependencies with `pnpm install --frozen-lockfile --ignore-scripts`
before launching.

To prepare and save the rc.5 candidate explicitly:

```bash
rpnh frontend setup dsh --install --dsh-compatibility-profile 0.2.2-rc.5
```

Use `0.2.2-rc.1` in that command for rc.1, or `0.2.2-alpha.2` for the default
profile. Setup saves the selection for subsequent launches; use separate
configuration and installation paths for candidate acceptance. The rc.1 profile
is an existing opt-in.

The following results belong to the earlier frontend acceptance source, not
a rerun or installed RC8 acceptance. On 2026-11-22, rc.5 passed WSL2 frozen installation using Node `24.22.1` and
pnpm `11.28.5`, with all 33 Web packages. The native BFF/projection suite passed
17/17 with zero skips, including three actual Chrome browser cases backed by a
scripted backend. A separate actual Python Owner + Web check passed empty-session
cold reopening at the same root. These checks made no model calls and do not
establish a real business outcome.

In the first authorized live submission, the driver misread `running` plus
`reconciliation_required` as terminal and actively closed the Owner. The result
was `stopped_by_owner`, zero Registry-settled calls, `submission_unknown` and no
registered answer. This first unknown submission remains separate historical
evidence.

After repair, a separately approved independent task with a new input set
passed through the genuine browser on source `b65f6682`, with `gpt-5.6-terra` /
`high`: one input, two Registry-settled calls, observed completion in 81.464
seconds, correct count/total/mean/min/max `3/45/15/10/21`, and semantic verifier
exit 0. Same-root read-only reopening passed in 4.486 seconds with zero native
submissions. The same browser session, Registry references and counters were
retained, and the original answer was readable in the actual DOM. This records
the exact approved live task and reopening; the earlier 17/17 scripted-backend
suite and the first stopped/unknown submission retain their separate scopes.

The first launch downloads 32 official npm Web packages for exact
`0.2.2-alpha.2`, or 33 for either rc candidate, into
`SOURCE/rpnh-frontend/node_modules`. A complete cache is reused without network
access; missing or incomplete packages require a download.
Preparation and asset checks finish before MainSession ownership is acquired.
A download or preflight failure therefore does not acquire the session owner.

Setup generates `rpnh-frontend/official.json`, `tsconfig.json`, typed Remote
descriptor copies and `generated/bound-client`. These connect the official UI
to the bound session; they do not rebuild or modify the official Web UI, patch
the legacy AgentLoop factory, or start a model call. The checkout and its cache
must remain writable and available for subsequent launches. The prepared
checkout and downloaded packages are not bundled in the RPNH wheel.

The following is historical metadata for the separate `0.2.1-rc.2` host
pinned by `integrations/dsh/LEGACY_UPSTREAM.json`, not the current Web default.
That legacy host retains Node `^22.19.0 || >=24.0.0` and pnpm `11.28.5`.
On 2026-10-19, [official npm metadata](https://registry.npmjs.org/@deepseek-ai%2fdsh)
reported `latest=0.2.0-rc.2`, `next=0.2.1-rc.2` and `alpha=0.2.1-alpha.5`.
Default npm install and the newest published prerelease therefore selected
different versions. That legacy integration uses the exact
[source commit](https://github.com/deepseek-ai/deepseek-harness/commit/687f282e8625c6b8d11ff58211a90524be2e4317)
and pinned Web packages, not a default CLI installation. GitHub had no latest
release in that check.

## Legacy headless host preparation

The following optional checks are for `rpnh-dsh`, not official Web:

```bash
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" /absolute/path/to/deepseek-harness
bash integrations/dsh/verify.sh /absolute/path/to/deepseek-harness
```

Legacy preparation checks the pinned revision and applies its narrow AgentLoop
factory seam; a CLI package alone does not provide that patched factory. It
does not copy credentials into the repository. Select an isolated HOME, cache
and temporary directory when running verification in a shared workspace.
`RPNH_DSH_RESULTS` overrides its output directory, which defaults to
`rpnh-results` in the upstream checkout. Verification runs the actual upstream
AgentLoop factory, Session, LlmRuntime, ToolRuntime and headless runner with
deterministic local responses. This is a source-host check, not official Web
or remote-provider acceptance.

## Legacy registered headless host: offline

`rpnh-dsh` remains an advanced registered headless host with its own `--root`
and `--session-id` semantics. It does not open the unified Web frontend or use
the direct MainSession root. The following frame, budget and recovery details
apply to this legacy host.

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json \
  --task "Read the numbers and compute their sum"
```

Offline mode validates host wiring without a provider call.

## Legacy host: configured route

Build a user-owned provider profile with `rpnh config build`, then pass its
absolute execution-selection path:

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --execution /absolute/path/to/selection.json \
  --root "$HOME/.rpnh/dsh" \
  --task "Reply with READY."
```

This command can make a real model call. Record the selected profile,
provider, exact model and call budget before using it. RPNH never silently
switches route or model.

The legacy host applies a 68 MiB limit to the complete framed exchange, including history,
tool declarations, request content and the configured maximum response. A
general profile whose response allowance alone consumes or exceeds that frame
is rejected before model-attempt admission. Create an explicit user-owned DSH
profile that keeps the same provider route, credentials and exact model while
declaring realistic response headroom. This is a host-specific budget, not a
provider or model fallback.

New legacy host sessions use a 48-attempt module budget by default. A positive
`--attempt-budget` selects a different explicit cap; `--attempt-budget
unmetered` removes only that module-attempt cap for a workload whose protocol
requires it. The selected value is persisted with the run. Resume uses that
persisted declaration and therefore does not accept a replacement budget.

Application integrations that override provider-visible tool descriptions or
input schemas pass both an explicit plugin catalog and an absolute
`--managed-bindings` document. The document has schema
`rpnh/dsh_managed_bindings/v1`, a nonempty `tools` mapping whose values contain
exactly `selector`, `description`, and object `input_schema`, plus a nonempty
unique `admitted_effects` list. DSH validates that each selected installed
operation's actual effect is in that list. The legacy selector-only
`--managed-tool NAME=PLUGIN/OPERATION` form remains pure-only and uses the
plugin's own description and schema; it never implicitly grants external
effects.

## Legacy host: recovery boundary

Current sessions use upstream V4 first-class tool messages. The codec retains
read-only decoding of the older V3 and previously inspected V4 revisions.
Execution and resume require the original exact revision; updating the host
does not migrate, relabel or reopen older sessions for execution. Preserve the
original supported host for those sessions. RPNH remains the sole workspace
and checkpoint authority.

The host may reconnect to durable RPNH state, but it cannot infer whether an
unacknowledged physical provider submission completed. Such an attempt remains
`submission_unknown` unless the shared provider adapter can reconcile it.
Managed tool results settle through the same Registry and operation contracts
as other hosts.

If exact registered outputs and their operation completion are durable before a
process loss, shared recovery may finish settlement without replaying the host,
provider or tool. Workspace-bound recovery additionally requires the exact
immutable candidate held by a `map_ready` subordinate execution checkpoint;
declared HOST effects remain excluded. This is Registry evidence recovery, not a
DSH-specific retry path.

## Platform and validation limits

Both launch paths currently support Linux/WSL2 only. Offline tests do not establish live
provider availability, and a prepared upstream checkout is not bundled in the
RPNH wheel. Admission and driver failures are surfaced as explicit non-zero
errors; an empty final response is not successful completion.
