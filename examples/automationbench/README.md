# AutomationBench retained experiment and host extensions

English | [中文](README_ZH.md)

This example preserves a real, score-blind 18-task AutomationBench pilot and
the native RPNH adapter used to produce it. The historical run covered all six public
business domains and three integration-width strata. It used a real external
model route; AutomationBench's business SaaS systems remained local simulated
worlds.

The strict first-attempt result was 8/18 complete tasks, 17/18 infrastructure
closures, 437 model calls, and 1,081 successful business-tool dispatches. The
17 scored attempts averaged 0.8877005348 partial credit. The one infrastructure
failure was retained and later passed in a separate remediation run; it does
not replace the first attempt.

- [Results and task-level scores](RESULTS.md)
- [Exact historical experiment condition](EXPERIMENT.md)
- [Comparison with published public-600 results](COMPARISON.md)
- [Current native/DSH and Codex/Basic/OpenCode adaptation](EXTENSIONS.md)
- [Design and execution boundary](DESIGN.md)
- [Local runbook](docs/RUNBOOK.md)
- [Protocol](docs/PROTOCOL.md)
- [Evidence rules](docs/EVIDENCE.md)
- [Offline verification](docs/OFFLINE_VERIFICATION.md)

## Inspect the retained result

No package installation, model call, or file write is needed:

```bash
python -I examples/automationbench/example.py results
python -I examples/automationbench/example.py results --json
```

The checked-in machine-readable records are:

- `results/stratified-pilot-plan-20261002.json`
- `results/stratified-pilot-first-attempt-20261002.json`
- `results/operations-0009-remediation-20261002.json`
- `results/conditions-20261002.json`

Raw Registry databases, provider transcripts, execution profiles, credentials,
and local absolute paths are deliberately not included.

## Historical architecture

```text
public task prompt -> native RPNH actor -> managed AutomationBench tools
                                           |
                                           v
                                  serialized local world owner
                                           |
                                           v
                          frozen final world -> upstream strict rubric
```

RPNH owns model execution, managed-tool admission, lifecycle, and Registry
evidence. The independently installed pinned AutomationBench source owns task
loading, simulated business-world semantics, endpoint implementations, and
programmatic end-state scoring. The adapter does not add a second agent loop or
an LLM judge.

Two narrowly scoped compatibility mappings are part of the published adapter:
JSON-string `"null"` values become absent optional arguments, and only the
Trello `POST /1/cards/{id}/idLabels` endpoint wraps a JSON scalar label as the
upstream-required `{"value": ...}` object. Original model arguments remain in
the tool-event evidence.

## Install and test

From the RPNH source root:

```bash
python -m pip install -e .
python -m pip install -e './examples/automationbench[test]'
python -m pytest examples/automationbench/tests -q -m 'not integration'
```

Set `RPNH_AB_UPSTREAM` to an independently installed checkout at
`4a8e1061254004d9dac807054eed33fad7d1ff14` to run the pinned-upstream and
scripted native-worker integration checks. They make no real provider calls.

## Run a new condition with your own profile

The extension contains `doctor`, `bridge-smoke`, `prepare`, the seven-case
`accept-host` producer, guarded `run`, `status`, `stop`, `score`, `reproject`,
`summarize`, and `export`. Native RPNH and the pinned DSH integration are
execution hosts. Basic, Codex and OpenCode are control surfaces that invoke the
same canonical CLI; they are not additional benchmark actors. Keep profiles,
work directories and Registries outside this repository. See the
[extension boundary](EXTENSIONS.md) and [runbook](docs/RUNBOOK.md).

The default preparation path can select all 600 public tasks; `--cohort` can
resolve only the frozen 18 task identities into a new condition. The retained
result itself remains the historical 18-task pilot. It is not an official
AutomationBench leaderboard submission, a representative estimate of the
public-600 score, or a matched comparison with another model or harness.

## Source pins

- AutomationBench: `4a8e1061254004d9dac807054eed33fad7d1ff14`
- RPNH execution core: the first eight attempts recorded `3492ba2`; the
  remaining ten and remediation recorded `1da3648`. There is no `cpn/`
  difference between those commits.

AutomationBench tasks and datasets are not vendored. See
[third-party notices](THIRD_PARTY_NOTICES.md).
