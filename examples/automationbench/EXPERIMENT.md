# Published experiment condition

[中文](EXPERIMENT_ZH.md) | [Results](RESULTS.md) | [Current extensions](EXTENSIONS.md)

This page describes only the retained 2026-10-02 experiment. Later runtime,
host, frontend, acceptance, cohort, scoring, or export extensions do not change
this condition and must not be read back into the published result.

## What actually ran

| Boundary | Published 18-task pilot |
|---|---|
| Operating environment | WSL Linux |
| Execution engine | Native RPNH worker and Registry |
| Tool transport | Local AF_UNIX socket to one serialized AutomationBench world owner |
| Executor label | `deepseek-v4-pro` |
| Provider label / transport | `volcano` over HTTPS |
| Business services | AutomationBench local simulated worlds, not production SaaS accounts |
| AutomationBench | `1.0.6`, commit `4a8e1061254004d9dac807054eed33fad7d1ff14` |
| RPNH | First eight attempts: `3492ba2`; remaining ten and remediation: `1da3648` |
| Scoring | Pinned upstream `partial_credit` and strict `task_completed_correctly`; no LLM judge |

Codex, OpenCode, Basic, and DSH were not execution evidence for this published
pilot. Their current extension support is documented separately and does not
retroactively turn this into a cross-host experiment.

## Frozen selection and outcome

The score-blind plan selected one eligible task from every public domain ×
integration-width cell, for 18 tasks total. The strict first attempt retained
all planned slots:

- 18 attempted tasks;
- 17 infrastructure closures and scored final worlds;
- 8 strict completions;
- 0.8877005348 mean partial credit over the 17 scored attempts;
- 437 executor model calls;
- 1,081 successful business-tool dispatches.

The failed `operations-0009` first attempt remains in the first-attempt record.
Its later 1.0 remediation is a separate engineering result and does not change
the published 8/18 outcome.

## Immutable public records

| File | SHA-256 |
|---|---|
| `results/conditions-20261002.json` | `cf640896fa90712242831f91129e97771418dce9403e512881ecef3ac9b30bb1` |
| `results/stratified-pilot-plan-20261002.json` | `7f99846bd6247987dd9030342beaa9c0cd70ac675da35dcb30e9abd056f68f11` |
| `results/stratified-pilot-first-attempt-20261002.json` | `31d87b37c5475257a8c7d3d918c11363c180d912d09aaef0b5ddeae11f66767b` |
| `results/operations-0009-remediation-20261002.json` | `89b7023928ffee28c9a63a1aba4acbb138f5f8ae446fa25dfdda17c9131ebfe8` |

Extension tests lock these bytes. New acceptance runs, user cohorts, Registries,
profiles, and exports belong in a caller-owned work directory outside the
repository; they never update `results/`.

## Evidence boundary

The published records support the plan, task-level summary, aggregate counts,
and disclosed execution condition. Raw Registry databases, provider
transcripts, private execution profiles, credentials, and local absolute paths
are intentionally not public. That disclosure limits third-party replay of the
historical provider calls; it is not evidence that the internally consistent
published scores are wrong.

The historical pilot should be rerun only if its task identities, scorer,
arithmetic, frozen-state eligibility, or stated execution condition is shown to
be incorrect. A later improvement to the reusable example is not such a reason.
