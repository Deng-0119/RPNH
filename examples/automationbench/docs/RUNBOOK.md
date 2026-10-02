# Local runbook

[中文](RUNBOOK_ZH.md) | [Example](../README.md)

## 1. Inspect the retained result

From the RPNH source root:

```bash
python -I examples/automationbench/example.py results
python -I examples/automationbench/example.py results --json
```

This is read-only and makes no model or business API call.

## 2. Install an isolated pinned upstream

Use a separate AutomationBench checkout at
`4a8e1061254004d9dac807054eed33fad7d1ff14`. Do not copy its dataset into this
repository. Install RPNH and this example in an isolated environment:

```bash
python -m pip install -e .
python -m pip install -e './examples/automationbench[test]'
python -m pip install -e "$AB_UPSTREAM"
```

Keep all generated work outside the repository and use a short path for the
native Unix owner socket:

```bash
AB_WORK="$(mktemp -d)/ab"
```

## 3. Run deterministic checks

```bash
python -m pytest examples/automationbench/tests -q -m 'not integration'
RPNH_AB_UPSTREAM="$AB_UPSTREAM" \
  python -m pytest examples/automationbench/tests/test_offline_runtime.py -q
```

The second command starts a real native worker with a scripted local-process
adapter and the real pinned AutomationBench world. It uses Unix sockets and
Registry evidence but makes no real provider call.
If the system temporary directory is too long for a Unix socket, set
`RPNH_AB_TMPDIR` to a short writable directory outside the repository.

## 4. Select and check a new condition

`PROFILE` must be an existing, separately authorized RPNH execution-selection
JSON. It is not an API-key file and must remain outside the repository.

Use no selection option for all 600 public tasks, `--split simple` for the
separate 200-task simple split, or `--cohort` for the exact ordered task IDs in
a checked-in plan. The retained 18-task plan is a convenient input for a *new*
condition; it does not continue the historical run:

```bash
AB_COHORT="$PWD/examples/automationbench/results/stratified-pilot-plan-20261002.json"

rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT"
rpnh-ab bridge-smoke --upstream "$AB_UPSTREAM" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native
```

`doctor`, `bridge-smoke`, and `prepare` make no model call. `doctor` records the
selected plan digest; `run` rejects a doctor record from another selection.
Inspect `plan.json`, `conditions.json`, and `doctor.json` before continuing.

## 5. Produce installed-host acceptance

The batch gate requires a condition-bound `rpnh-ab/acceptance-manifest/v1`.
Generate it through the installed host, not by copying a unit-test fixture:

```bash
rpnh-ab accept-host --upstream "$AB_UPSTREAM" --work "$AB_WORK" --host native
```

The producer executes only a deterministic synthetic world and local-process
adapter. It records seven cases: installed runtime, managed schema/effects,
real host execution, result boundaries, more than 48 unmetered dispatches,
quiescent stop, and matched upstream world/rubric behavior. It makes zero real
provider and zero production business API calls. The gate reparses the
underlying attempt, lifecycle, tool-event, score and host/Registry evidence;
copied labels or generic proof files are rejected.

Bind that exact manifest into the launch request by repeating the idempotent
preparation with the same arguments:

```bash
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native \
  --acceptance "$AB_WORK/host-acceptance/acceptance.json" \
  --launch-output "$AB_WORK/launch.json"
```

## 6. Live execution and control surfaces

The following command requires explicit authorization and may make paid model
calls:

```bash
rpnh-ab run --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --config "$AB_WORK/launch.json"
```

Shell, Basic, Codex and OpenCode terminals all use this same CLI. They do not
change the benchmark actor. From another terminal:

```bash
rpnh-ab status --work "$AB_WORK"
rpnh-ab stop --work "$AB_WORK"
```

For the optional DSH execution host, use a dedicated pinned checkout and add
`--host dsh --dsh-checkout "$DSH_CHECKOUT"` to both `prepare` calls and to
`accept-host`. Acceptance evidence is host-specific and cannot be reused across
native and DSH conditions. The checkout may be clean or contain only the exact
idempotent factory seam applied by `integrations/dsh/prepare.sh`; both normalize
to the same post-prepare identity, while every other tracked change is rejected.

The live run does not contact production business SaaS accounts unless the
pinned upstream itself is modified, which this adapter rejects.

## 7. Offline evidence maintenance

Use `score`, `reproject`, and `summarize` only on retained frozen evidence.
Scores whose lifecycle, task identity, task contract, scoring input, or final
world no longer matches are ineligible. `export` includes normalization events
and an exact exported-byte inventory while omitting private profiles, raw
Registry databases, and provider transcripts. Never rerun a failed task merely
to make a summary look complete; store remediation under a new condition and
keep the first attempt.

The retained pilot used the historical native condition documented in
[`EXPERIMENT.md`](../EXPERIMENT.md). It is not represented as a newly generated
generic batch manifest. These commands prepare a new condition; they do not
reproduce or overwrite that provider result.
