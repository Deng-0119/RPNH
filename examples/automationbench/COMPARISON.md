# Comparison with published AutomationBench results

[中文](COMPARISON_ZH.md) | [Example](README.md) | [Local results](RESULTS.md)

This is a descriptive comparison, not a matched model or harness evaluation.

The [pinned upstream README](https://github.com/zapier/AutomationBench/blob/4a8e1061254004d9dac807054eed33fad7d1ff14/README.md)
reports strict pass rates for complete 600-task public runs, with 100 tasks in
each of six domains:

| Model | Reasoning effort | Published public-600 pass rate |
|---|---|---:|
| Claude Opus 5 | max | 50.30% |
| Kimi K3 | max | 46.67% |
| Claude Fable 5 | max | 46.17% |
| GPT-5.6 Sol | max | 45.83% |
| Gemini 3.6 Flash | high | 45.00% |
| Claude Opus 4.8 | max | 41.00% |
| Gemini 3.5 Flash | high | 38.33% |
| GPT-5.6 Terra | max | 37.17% |
| Claude Sonnet 5 | max | 34.67% |
| GLM 5.2 | max | 26.17% |

The local strict first-attempt cohort is 8/18, or 44.44%. Its numerical
position is near some published aggregate rows, but that does not make the
values directly comparable:

- the local executor was labeled `deepseek-v4-pro`, which has no published row
  in the pinned table;
- the local sample contains 18 deterministic, score-blind strata selections,
  not all 600 public tasks or a random representative sample;
- one planned first attempt was unscored after an infrastructure failure;
- model effort, runtime, prompt mapping, and tool-host conditions are not a
  matched control; and
- the official leaderboard uses a separate, harder held-out private task set.

Mechanically averaging the 17 scored tasks gives 47.06%, but excluding the
failed infrastructure slot biases the denominator. Substituting the remediation
run gives 50.00%, but mixes attempts and is explicitly not the strict result.

## No published per-task ledger

AutomationBench defines two per-task metrics, `partial_credit` and
`task_completed_correctly`, and its runner can export per-task records. However,
the pinned source tree contains no result export for the reported models;
`visualizer/runs/` contains only `.gitkeep`. The public files contain task
rubrics, not achieved model scores. The private leaderboard tasks are not
released.

Consequently there is no published per-task score to join against these same 18
task IDs. This example publishes its own task-level rows without inventing an
original-model baseline.
