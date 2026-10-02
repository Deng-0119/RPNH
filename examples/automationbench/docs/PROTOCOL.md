# Retained pilot protocol

[中文](PROTOCOL_ZH.md) | [Results](../RESULTS.md)

The retained result used the AutomationBench public split at commit
`4a8e1061254004d9dac807054eed33fad7d1ff14`, the API toolset, and one fresh
attempt per selected task. Selection was frozen before scoring.

The six domains were crossed with focused (2–3 services), standard (4), and
broad (5–8) integration width. One eligible task per cell was selected by the
smallest SHA-256 of `seed:task_id`. Tasks using the ChatGPT business helper and
previously attempted IDs were excluded. The checked-in plan records the exact
seed, exclusions, cells, and selected task IDs.

A scored task failure did not stop the pilot. An infrastructure or configuration
failure stopped dispatch until the compatibility issue was diagnosed. Resume
kept the frozen prefix and did not rerun or overwrite prior attempts.

Each score used the upstream `partial_credit` and
`task_completed_correctly` functions on a frozen final world. Missing or errored
scores stayed null. The primary summary is the strict first-attempt cohort;
remediation is separate.

The adapter adds no LLM judge and no cumulative model-call, tool-call, spend, or
task-duration limit. Per-request and managed-operation limits from the selected
RPNH profile still apply and must be recorded for any new run.
