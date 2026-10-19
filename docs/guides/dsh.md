---
name: rpnh-dsh-guide
description: "Install and operate the optional DSH host adapter without duplicating RPNH authority."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: dsh_ZH.md
  revision: "2026-10-19.1"
  status: source-reviewed-pre-release
---

[English](dsh.md) | [中文](dsh_ZH.md)

# DSH host adapter

DSH is an optional presentation and tool host. RPNH remains responsible for
the provider route, exact model, Registry records, PetriNet admission, managed
tool grants and final-result accounting. The adapter does not maintain a
second provider catalog or Registry writer.

## Prepare the pinned host

The integration targets the exact upstream revision declared in
`integrations/dsh/UPSTREAM.json`. Preparation requires Node.js and pnpm and
must be run against a separate upstream checkout:

```bash
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" /absolute/path/to/deepseek-harness
bash integrations/dsh/verify.sh /absolute/path/to/deepseek-harness
```

The current host is upstream `687f282e8625c6b8d11ff58211a90524be2e4317`
(2026-10-17), root `@deepseek-ai/dsh-root@0.2.1-rc.2` and CLI
`@deepseek-ai/dsh@0.2.1-rc.2`, using pnpm `11.28.5`. On 2026-10-19,
[official npm metadata](https://registry.npmjs.org/@deepseek-ai%2fdsh)
reported `latest=0.2.0-rc.2`, `next=0.2.1-rc.2` and
`alpha=0.2.1-alpha.5`. Thus a default npm install and the newest published
prerelease select different versions. The [source commit](https://github.com/deepseek-ai/deepseek-harness/commit/687f282e8625c6b8d11ff58211a90524be2e4317)
is the source integration target; a downloaded CLI package alone does not
provide the patched factory. GitHub has no latest release for this check.

Use Node `^22.19.0 || >=24.0.0` (tested here with `22.22.1`). Install the
checkout's dependencies with `pnpm install --frozen-lockfile --ignore-scripts`
before preparation. Select an isolated HOME, cache and temporary directory
when running verification in a shared workspace. `RPNH_DSH_RESULTS` overrides
the verification output directory; its default is `rpnh-results` inside the
upstream checkout. Verification runs the actual upstream AgentLoop factory,
Session, LlmRuntime, ToolRuntime and headless runner with deterministic local
responses. This is a source-host check, not a desktop/browser UI or remote
provider acceptance test.

Preparation checks the pinned revision and applies the narrow factory seam
needed by the RPNH backend. It does not copy credentials into the repository.

## Run offline

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json \
  --task "Read the numbers and compute their sum"
```

Offline mode validates host wiring without a provider call.

## Use a configured route

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

DSH applies a 68 MiB limit to the complete framed exchange, including history,
tool declarations, request content and the configured maximum response. A
general profile whose response allowance alone consumes or exceeds that frame
is rejected before model-attempt admission. Create an explicit user-owned DSH
profile that keeps the same provider route, credentials and exact model while
declaring realistic response headroom. This is a host-specific budget, not a
provider or model fallback.

New sessions use a 48-attempt module budget by default. A positive
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

## Recovery boundary

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

## Limits

The pinned adapter is Linux/WSL2-only. Offline tests do not establish live
provider availability, and a prepared upstream checkout is not bundled in the
RPNH wheel. Admission and driver failures are surfaced as explicit non-zero
errors; an empty final response is not successful completion.
