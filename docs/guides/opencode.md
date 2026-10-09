---
name: rpnh-opencode-frontend
description: "Use the pinned OpenCode TUI as an RPNH presentation frontend."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: opencode_ZH.md
  revision: "2026-10-08.1"
  status: locally-validated-integration-candidate
  upstream-version: "1.18.32"
---

# OpenCode frontend

[中文](opencode_ZH.md)

## Purpose and authority

OpenCode is a terminal presentation client, not an RPNH execution backend. The
path is `rpnh -> OpenCode attach -> loopback HTTP/SSE -> frontend-neutral
application service -> existing MainSession/TaskControl -> Registry/Petri net`.
RPNH continues to own exact model selection, provider calls, registered resources,
workspaces, tools, independent tasks, Designer workflows, checkpoints and terminal
proof. The compatibility layer neither imports upstream runtime code nor starts
an OpenCode server/provider/tool loop.

This remains an integration candidate rather than a published release. Local
acceptance covers the complete harness checkout, real Registry paths, an
out-of-source wheel, the pinned real TUI against a provider-free application
double, and one separately authorized exact-model turn through that TUI. See the
[validation record](examples-validation.md) for the result and its content-level
warning.

## Prerequisites and entry

Use Linux/WSL2, Python 3.11+, a complete harness checkout, a configured RPNH
execution profile, and a separately installed trusted **OpenCode 1.18.32** client.
The compatibility source is `anomalyco/opencode` commit
`545f51d26cc39a907d2867492d498d9607ea5fa4` (tag `v1.18.32`). Check `opencode --version`.
The launcher rejects other version strings; it does not download/install the
client, establish binary provenance, or modify global OpenCode settings.

```bash
python -m pip install .
rpnh --help
rpnh --frontend opencode --session-dir ./my-rpnh-session
# After leaving any RPNH frontend, reopen the same MainSession:
rpnh --frontend opencode --resume ./my-rpnh-session
```

Starting a frontend/catalog does not submit a prompt. Sending a prompt, launching
a task, or explicitly resuming work can invoke the selected RPNH model/tools.
`--prompt` is rejected with this interactive frontend; use `--frontend basic`
for the existing one-shot path. Basic and Codex still delegate to the existing
CLI. No existing runtime, Registry schema, provider implementation or default
frontend is replaced.

The supplied path is the direct canonical MainSession root. It is the same path
accepted by Basic and Codex, so a session created in one frontend can be viewed
and continued by either other frontend after the first exits. OpenCode exposes
one presentation session for that root; it creates no `threads/ses_*` Registry
copy. A shared nonblocking owner lease rejects a second writable frontend.
Protocol `ses_*` IDs and `.frontends/codex.json` are presentation metadata only.

Resume/list/history operations are observational: they do not invoke a model,
settle a terminal main turn or compensate a committed child launch. A terminal
turn inherited from another frontend remains visibly terminal until the user
issues `/rpnh-resume`; that explicit action may commit the main answer and launch
its already-declared child. Historical turns without OpenCode request evidence
remain visible, with their per-turn model evidence marked unavailable instead of
being reconstructed from current defaults or sidecars.

## Conversation and duplicate requests

A request identity is committed by the existing MainThreadRegistry before a
worker launch. HTTP success acknowledges registered input; only a committed
Registry answer creates a successful assistant response. A worker exiting with
code zero is not terminal proof. A registered child launch is not child success.
SSE reconnect replaces stable message/part snapshots and never retries a provider.
Unknown populated attempts are surfaced for reconciliation, not blindly replayed.
While a child is creating its Registry, a present but not yet exactly readable
SQLite authority is projected as `reconciliation_required`; it does not terminate
the frontend owner or authorize another submission. A later tick settles only
from exact Registry evidence.

The pinned stock TUI does not always send `messageID`. Therefore **identical
unkeyed text is the same operation for the lifetime of one session**, even after
completion or interruption. Use `/rpnh-send UNIQUE_ID TEXT` for an intentional
repeat. Retry a lost explicit request with the same ID; choose a fresh ID only
for genuinely new work. HTTP callers can also supply `messageID` or
`Idempotency-Key`; both must agree when supplied together. This conservative
rule is a visible v1 limitation, not transparent exactly-once intent inference.

## Commands

| Command | RPNH operation |
| --- | --- |
| `/rpnh-help` | Discover the supported control surface. |
| `/rpnh-tasks` | List the session's independent tasks/workflows. |
| `/rpnh-task ID status\|result\|stop\|resume` | Address exactly one existing child owner; resume continues its current stopped cut. |
| `/rpnh-task ID checkpoints` | List exact committed checkpoint versions for that child run. |
| `/rpnh-task ID reopen CHECKPOINT [:: REASON]` | Reopen that exact cut as a new generation in the same Registry/run, with optional owner guidance. |
| `/rpnh-task ID message TEXT` | Send a registered control message to one child owner. |
| `/rpnh-task ID net [resource flags]` | Read the selected child's real registered net. |
| `/rpnh-net [--show-resources\|--resources-only]` | Read the latest main attempt's real net using the existing filter. |
| `/rpnh-agent TEXT` | Launch an independent single-agent Registry/task. |
| `/rpnh-workflow TEXT` | Request a Designer-authored workflow through the main session. |
| `/rpnh-resume` | Resume the paused main turn through its exact task owner. |
| `/rpnh-rollback` | Interrupt the paused main conversation while retaining the child Registry. |
| `/rpnh-send UNIQUE_ID TEXT` | Submit/retry with an explicit identity. |

Names are prefixed to avoid collisions with OpenCode-native commands. Stock
OpenCode may still render native commands that it owns; those commands are not
RPNH capabilities and their effect routes fail closed. Command
observations are labelled synthetic, disposable display output, not agent answers
or new terminal proof. They are not restored after process restart. Task IDs,
results and net views come from RPNH and remain available through its authorities.
Net output is a JSON observation, not a native OpenCode Petri-net visualization.

An abort asks the exact main-turn owner to stop. Repeated aborts during one owner
lifetime are coalesced. Acceptance is not a checkpoint: wait for RPNH's
`stopped_by_owner` state. An early abort before the owner channel is ready fails
explicitly instead of sending an unsafe startup interrupt. Leaving the UI asks
every active main turn to reach its normal `stopped_by_owner` checkpoint before
the application owner closes; independently owned child workers keep running.
Resume/rollback never reconstructs a checkpoint from the OpenCode transcript.
Task checkpoint reopen likewise uses only the selected committed Registry cut;
it does not derive state from OpenCode messages or create a replacement task.

## Model, resources and disabled features

All selectable user-configured RPNH profiles appear under one presentation-only
`rpnh` provider. A session can select a profile when created and switch profiles
between main turns; switching during an active turn is rejected. OpenCode-origin
turns retain their request selection evidence; imported Registry history is not
assigned evidence it never recorded. Unknown profiles, unavailable credentials,
non-RPNH agents and variants are rejected. RPNH profile identity is checked before
new execution and before an explicit action that could compensate a launch. The
client receives no provider key, credential environment name,
endpoint, host route or private profile path.

Permission replies and attachments are unavailable in this version: there is no
fake approval UI, default grant, direct file URL intake or attachment-to-text
fallback. OpenCode-native shell, fork, revert, share, summarize, initialization,
provider authentication and configuration writes are unsupported. They do not
fall back to another backend. LSP/MCP/formatter/workspace catalogs are empty
compatibility views, not advertisements of registered RPNH extensions.

Usage, cost and context metrics are **unavailable**. Mandatory numeric OpenCode
DTO fields use zero display sentinels; zero is not a measured/free-use claim.
Use RPNH's registered call counts and underlying execution evidence instead.
The current UI may still render those sentinels as numbers; this is not usage
accounting acceptance.

## Isolation, limits and failures

The launcher resolves the installed binary, probes its version in an isolated
HOME/XDG/config/display directory, then starts an ephemeral authenticated
`127.0.0.1` listener. Its temporary Basic-auth credential is not a provider key.
Provider keys, global OpenCode settings, plugin loaders, proxy variables and
Python/Node injection variables are not inherited by the client. The original
RPNH environment remains available only to its existing execution path. This is
not an OS sandbox against a malicious client binary.

The listener has 32 connection slots, eight SSE slots, a 256 KiB request-body
limit, 128 queued application requests, and a 4 MiB response/snapshot limit.
Slow clients receive coalesced current snapshots, not unbounded delta queues.
Large histories can exceed the v1 display limit and fail explicitly. Diagnostics
retain bounded route shapes/statuses, not prompt bodies, headers or arbitrary
URLs. Unknown outcomes, profile drift, missing owners and unsupported requests
are explicit errors; none asserts successful execution.

## Explicit candidate certification (test-only)

Production remains pinned to **1.18.32**. The manifest additionally records
**1.18.35** (`53d1eabb61e21162157817bf677da0a4ad3332e3`) as
`certification-only`, with production disabled and native G2/G3 `not-run`.
There is no product CLI, environment-variable or config switch for the candidate.
The exact immutable profile is explicitly shared by the test's binary probe and
protocol instance. It changes declared version metadata only; DTOs, execution,
Registry, provider/model ownership and Petri-net projection are unchanged.

Run the candidate's pure G1 checks without a client, socket, PTY or provider:

```bash
python -m pytest -q tests/test_opencode_candidate_profiles.py
```

The packaged schema remains the reviewed source-extracted subset for 1.18.32.
Both profiles share its DTO checks. Its health response has an old-version
constant: candidate tests separately check the exact candidate version and bind
only that version metadata for structural validation. This is not an unchanged
old health-constant pass, official SDK compilation or stock-client acceptance.

Only after separately authorizing native execution, using an already obtained
trusted, unmodified Linux binary and preinstalled test dependencies:

```bash
python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/opencode \
  --opencode-certify-lane=contract
# Run separately for the synthetic committed-Registry read smoke:
python -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 \
  --opencode-certify-binary=/absolute/path/to/opencode \
  --opencode-certify-lane=registry-read
```

These are limited smoke scenarios, not complete G2/G3 certification. Without
explicit options the candidate native cases do not run. Partial options,
unsupported versions/platforms, missing binaries/dependencies, wrong probe
output or a deselected requested lane fail rather than silently passing a skip.
The gates never install, log in, contact a real model or accept a user Registry
path. Record binary provenance separately: a local hash and version output alone
do not prove an official release artifact. For the production control, repeat either explicit smoke command with
`--opencode-certify-version=1.18.32` and the corresponding stock binary. Both
exact targets are test-only selections; this does not alter the legacy pinned
PTY test or the production launcher.

G2 uses the original HTTP/SSE protocol and `ApplicationDouble`. G3 prepares two
committed turns using the existing deterministic fake port, then measures the
original `FrontendGateway`/`RegistryFrontendApplication` read path with zero new
fake-port/model/submit/spawn effects. Preparation calls are reported separately.
Normal gateway ticks and exclusive owner-lease acquire/release remain real.
Read-only net checks use `/rpnh-net` and the existing projection/filter;
`/rpnh-tasks` can reconcile links and is excluded from strict read-only measures.
Picker coverage, forced SSE reconnect and long-history page boundaries remain
uncovered by these smoke gates. Linux and WSL2 are separately measured targets;
no native Windows or macOS support is added. Passing evidence never updates the
manifest's certification state or promotes the default pin.

## Verification and implementation map

```bash
python -m pytest -q tests/test_opencode*.py
# Full existing tests, only from the complete harness checkout:
python -m pytest -q
# Full wheel, installed outside the source tree, using already available dependencies:
WORK=$(mktemp -d)
python tools/check_opencode_wheel.py --work-dir "$WORK/wheel"
```

The optional PTY test runs only with an installed fixed-version client and uses
an in-process fake application, never a real provider. It requires actual
bootstrap routes, a read-only command and one prompt submission, and saves
first-failure terminal/route evidence in the test's temporary directory. Do not
convert its skip into PASS.
The wheel verifier refuses changes-only sources and uses no-index/no-deps.

`cpn/rpnh_cli.py` selects the frontend; `cpn/rpnh/frontend_application.py`
provides neutral application ownership; `cpn/frontend/opencode_protocol.py`
projects pinned DTOs; `opencode_http.py` provides bounded HTTP/SSE;
`opencode_launcher.py` isolates attach; `opencode_compatibility.v1.json` records
the source-extracted SDK subset. No upstream source checkout is bundled.

The fixture is a reviewed subset, not upstream generator output or evidence that
the upstream SDK/TUI was executed. Source references are the pinned generated
SDK types/client, TUI `context/sync.tsx`, `context/project.tsx`, prompt component,
attach command and TUI/core configuration flags. Review them again before changing
the version pin. The non-site `docs/validation/` record contains actual local
results and remaining gates.
