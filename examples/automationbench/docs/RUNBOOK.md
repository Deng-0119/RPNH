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

## 4. Prepare a new condition

`PROFILE` must be an existing, separately authorized RPNH execution-selection
JSON. It is not an API-key file and must remain outside the repository.

```bash
rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" --work "$AB_WORK"
```

`doctor` and `prepare` do not run the model. The default public plan contains
all 600 tasks; inspect `plan.json` and `conditions.json` before continuing.

The batch gate also requires a host-bound `rpnh-ab/acceptance-manifest/v1`
covering the installed runtime, managed schema/effects, real scripted host
execution, output boundaries, an unmetered-over-48-call fixture, quiescent stop,
and matched world/rubric evidence. Do not fabricate this manifest from a unit
test or a startup probe. No authoritative manifest producer is included in this
public example: production of that deployment-bound record belongs to the
deployment owner. The integration test above is core native evidence, but is
not itself a passed manifest. Until a deployment supplies and reviews such a
producer, the packaged batch `run` command must remain blocked.

## 5. Live execution and offline maintenance

After a deployment has implemented its own reviewed manifest producer, explicit
authorization and a matching manifest are still required before creating a
launch request with `prepare --acceptance ... --launch-output ...` and invoking
`rpnh-ab run ... --config ...`. These are interface names, not a turnkey
invocation supplied by this repository. A live run may make paid external model
calls. It does not contact production business SaaS accounts unless the pinned
upstream itself is modified, which this adapter rejects.

Use `score`, `reproject`, and `summarize` only on retained frozen evidence.
`export` omits private profiles, raw Registry databases, and provider
transcripts. Never rerun a failed task merely to make a summary look complete;
store remediation under a new condition and keep the first attempt.

The retained pilot used a task-local native driver whose evidence remains in
the private experiment archive; it is not represented as a passed generic
batch manifest. The checked-in 18-task result is historical evidence. These
commands prepare a new condition; they do not reproduce that exact provider
result automatically.
