# Current AutomationBench extensions

[中文](EXTENSIONS_ZH.md) | [Historical experiment](EXPERIMENT.md) | [Runbook](docs/RUNBOOK.md)

This page describes reusable code added after the retained 2026-10-02 pilot.
It does not describe how that pilot was executed and does not alter anything in
`results/`.

## Supported boundaries

There are two independent axes:

| Axis | Supported choices | Meaning |
|---|---|---|
| Execution host | native RPNH; pinned DSH integration | Owns the actor loop, managed-tool calls, lifecycle and Registry evidence |
| Control surface | shell, Basic, Codex or OpenCode terminal | Invokes and observes the same `rpnh-ab` CLI and the same files; it is not another benchmark actor |

The CLI is the canonical benchmark authority. Basic, Codex and OpenCode do not
receive a separate task prompt or modify scoring. A user can run the same
commands from any of those terminals. DSH is different: selecting `--host dsh`
changes the execution host and is therefore frozen into the condition and
acceptance identity.

None of these extension paths is evidence for the historical pilot. That run
used the native host described in [EXPERIMENT.md](EXPERIMENT.md).

## What the extension adds

- `--cohort` resolves the checked-in 18-task plan by ordered task ID against the
  pinned upstream and freezes the current task contracts. It never expands to
  the other 582 public tasks.
- `accept-host` runs a deterministic synthetic task through the installed
  native or DSH host. Its seven-case manifest covers the real managed-tool path,
  a result larger than the old 64 KiB DSH boundary, an unmetered sequence above
  48 dispatches, quiescent stop, and matched upstream world/rubric behavior.
  It uses no provider or production business API. The launch gate reparses the
  referenced lifecycle, tool-event, score and host/Registry evidence; labels
  and file hashes alone are not sufficient.
- `status` and `stop` use the caller-owned work directory. A stop request is
  retained; it never causes an automatic replay.
- state-mutating offline maintenance (`score`, `summarize`, `reproject`, and
  `export`) shares the nonblocking batch-owner lock and refuses to run while a
  live owner is publishing evidence. `status` remains read-only and `stop`
  remains available during a run.
- scoring rejects missing lifecycle evidence and stale task-contract,
  final-world, or scoring-input hashes. A score revision must match the inputs
  frozen at attempt creation and the preceding score; `summarize` excludes
  ineligible scores.
- `export` includes normalization events and a hash/size inventory of the exact
  redacted bytes in the return ZIP. Private profiles, raw Registry databases
  and provider transcripts remain local.

`acceptance` remains a compatibility alias for the small bridge smoke test;
new documentation calls it `bridge-smoke` so it cannot be confused with the
seven-case installed-host gate.

## New native condition from the retained cohort

Use an independently installed pinned AutomationBench checkout, an authorized
profile, a new short work path, and an absolute cohort path:

```bash
AB_COHORT="$PWD/examples/automationbench/results/stratified-pilot-plan-20261002.json"

rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT"
rpnh-ab bridge-smoke --upstream "$AB_UPSTREAM" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native
rpnh-ab accept-host --upstream "$AB_UPSTREAM" --work "$AB_WORK" --host native
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native \
  --acceptance "$AB_WORK/host-acceptance/acceptance.json" \
  --launch-output "$AB_WORK/launch.json"
```

Everything above is deterministic or preparatory and makes zero real provider
calls. Inspect the generated plan, conditions, doctor record and acceptance
manifest before authorizing the live command:

```bash
rpnh-ab run --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --config "$AB_WORK/launch.json"
```

That final command may make paid model calls. It creates a new condition; it
does not reproduce, repair or overwrite the retained pilot.

From another terminal or any supported presentation surface:

```bash
rpnh-ab status --work "$AB_WORK"
rpnh-ab stop --work "$AB_WORK"
```

## DSH host variant

Use a dedicated checkout at the revision recorded in
`integrations/dsh/UPSTREAM.json`. Add `--host dsh --dsh-checkout "$DSH_CHECKOUT"`
to both `prepare` calls and to `accept-host`. The adapter uses the supported DSH
console/task/history chain, the same registered provider selection and the same
three managed AutomationBench operations. AutomationBench explicitly requests
an unmetered DSH attempt budget; DSH's normal default remains 48.

The frozen DSH identity treats the clean pinned factory file and its exact,
idempotently prepared RPNH seam as one canonical post-prepare condition. Any
other tracked change is rejected. `accept-host` checks this identity before and
after execution, so the first preparation cannot silently change the frozen
condition.

Run the DSH host acceptance for that exact code/profile/checkout condition
before creating its launch request. Native acceptance is not transferable to a
DSH condition, and neither host's new results are merged with `results/`.
