# Retained 18-task stratified pilot

[中文](RESULTS_ZH.md) | [Example](README.md) | [Exact condition](EXPERIMENT.md) | [Comparison](COMPARISON.md)

Date: 2026-10-02. Executor label: `deepseek-v4-pro`. The label records the
configured local route and is not a supported-model promise.

## Strict first-attempt outcome

- 18/18 selected tasks were attempted.
- 17/18 reached native `host_terminal`, host/world-owner quiescence, and
  upstream scoring.
- 8/18 had `task_completed_correctly=1.0`.
- The 17 scored tasks averaged 0.8877005348 `partial_credit`.
- The cohort used 437 real model calls and 1,081 successful business-tool
  dispatches.

`8/18 = 44.44%` is the strict cohort outcome when the infrastructure failure
remains in its planned slot. `8/17 = 47.06%` is only a scored-subset diagnostic;
it is not a complete benchmark pass rate.

| # | Task | Domain | Width | Infra | Strict | Partial | Model calls | Tool dispatches |
|---:|---|---|---|---:|---:|---:|---:|---:|
| 1 | `finance-0019` | finance | focused | 1 | 0 | 0.7500 | 29 | 58 |
| 2 | `finance-0015` | finance | standard | 1 | 0 | 0.9091 | 30 | 59 |
| 3 | `finance-0076` | finance | broad | 1 | 1 | 1.0000 | 42 | 113 |
| 4 | `hr-0009` | hr | focused | 1 | 0 | 0.8333 | 19 | 30 |
| 5 | `hr-0035` | hr | standard | 1 | 1 | 1.0000 | 21 | 56 |
| 6 | `hr-0072` | hr | broad | 1 | 1 | 1.0000 | 34 | 86 |
| 7 | `marketing-0083` | marketing | focused | 1 | 0 | 0.6667 | 25 | 106 |
| 8 | `marketing-0043` | marketing | standard | 1 | 1 | 1.0000 | 33 | 101 |
| 9 | `marketing-0086` | marketing | broad | 1 | 1 | 1.0000 | 31 | 67 |
| 10 | `operations-0009` | operations | focused | 0 | — | — | 9 | 21 |
| 11 | `operations-0047` | operations | standard | 1 | 0 | 0.9167 | 18 | 38 |
| 12 | `operations-0028` | operations | broad | 1 | 0 | 0.4000 | 25 | 49 |
| 13 | `sales-0002` | sales | focused | 1 | 1 | 1.0000 | 28 | 76 |
| 14 | `sales-0034` | sales | standard | 1 | 1 | 1.0000 | 28 | 55 |
| 15 | `sales-0065` | sales | broad | 1 | 1 | 1.0000 | 26 | 66 |
| 16 | `support-0058` | support | focused | 1 | 0 | 0.7273 | 13 | 23 |
| 17 | `support-0041` | support | standard | 1 | 0 | 0.9545 | 13 | 42 |
| 18 | `support-0032` | support | broad | 1 | 0 | 0.9333 | 13 | 35 |

The exact unrounded values and task metadata are in the checked-in JSON files
under `results/`.

## Sampling

The source set contained 100 public tasks in each of sales, marketing,
operations, support, finance, and HR. Tasks were grouped by allowed service
count: focused (2–3), standard (4), and broad (5–8). One eligible task was
selected from each domain × width cell by the lexicographically smallest
SHA-256 of `seed:task_id`. The rule and seed were frozen before scores were
read. Fifteen tasks using the ChatGPT business helper and five previously
attempted IDs were excluded.

The hash is only a deterministic sampler; it is not an evidence fingerprint.
One task per cell is too small to estimate domain or width effects.

## Retained infrastructure failure and remediation

The first `operations-0009` attempt passed a JSON scalar body to the pinned
Trello add-label route. That upstream route expanded the parsed string as a
mapping and raised `TypeError`, leaving the native host nonterminal. The failure
was classified as adapter/upstream compatibility, not a Registry or Unix-socket
failure.

After adding an endpoint-specific mapping and regression coverage, a separate
run reached `host_terminal` and strict score 1.0 with 11 model calls and 26 tool
dispatches. That run used already-valid object arguments, so it validates the
repaired end-to-end path but did not itself re-trigger the scalar mapping.

Substituting remediation for the original failed slot gives an engineering
coverage view of 18/18 infrastructure closures and 9/18 strict passes. It is
not a first-attempt rescore and is not used as the main result.

## Scope and provenance

“Real API” means a real external model-provider HTTPS route. Gmail, Salesforce,
Trello, and the other business services were AutomationBench's local simulated
worlds, not production accounts. The original task-level assertions scored the
frozen world; no LLM judge was added.

The first eight attempts recorded RPNH `3492ba2`; the remaining ten and the
remediation recorded `1da3648`. The execution-core `cpn/` tree is identical
between those commits. AutomationBench was fixed at
`4a8e1061254004d9dac807054eed33fad7d1ff14`.

This is not a random representative sample, a complete public-600 run, or an
official AutomationBench score.
