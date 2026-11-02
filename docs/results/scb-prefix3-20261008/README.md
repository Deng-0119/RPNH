---
name: rpnh-result-scb-prefix3-20261008
description: "SlopCodeBench adapted prefix 3 results"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

English | [中文](README_ZH.md)

# SlopCodeBench adapted prefix 3 results

The historical `code_search` run completed **the first three of five checkpoints** in adapted development-prefix mode. Checkpoints 4 and 5 were **not run**. The [complete case/status projection](results.json) includes every original evaluator case, category, status, source snapshot identity and runtime count, without assertion text, solver source or model/tool transcripts. Public tasks were inspected during development; this was not a held-out evaluation.

## Source and evaluator

Installed RPNH bytes matched `74fad32d369876841686d10d33361c016e3d3648`: 781 core payloads and 11 SCB payloads. Historical adapter base was `ae09445fe1d9b973502bc5d2c961976c1d2c0163`. Publication at `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` was not a rerun. Runner/evaluator: [slop-code-bench 31ceea3](https://github.com/SprocketLab/slop-code-bench/tree/31ceea3add480edb33431e70475c4c70597e6b31). Dataset/task: [scb-problems 9cd9ca3](https://github.com/gabeorlanski/scb-problems/tree/9cd9ca3a51c3d3e2a99d2488a25baf73a2204451). The exact `code_search` configuration blob is `a329353763b33a2d57e9186a957e2a3665333c49`. Input/output snapshot digests preserve continuity from checkpoint 1 through 3 in the projection; upstream owns the solution workspace, and `native_workspace_reuse=false`.

| Checkpoint | Original cases | Core | Functionality | Regression | Error | Evaluator exit | Runtime | Real calls |
|---|---|---|---|---|---|---|---|---|
| 1 | **13/13** | 7/7 | 4/4 | 0/0 | 2/2 | 0 | complete | 5 |
| 2 | **25/25** | 5/5 | 5/5 | 13/13 | 2/2 | 0 | complete | 5 |
| 3 | **40/47** | 6/8 | 7/12 | 25/25 | 2/2 | 1 | complete | 14 |
| 4 | not run | — | — | — | — | — | — | — |
| 5 | not run | — | — | — | — | — | — | — |

Checkpoint 3's seven failed cases are `optional_metavar`, `multiple_metavars`, `multiline_python_if_blocks`, `language_filtering`, `literal_dollar_sign`, `multiple_files_sorted` and `special_chars_in_captures`. `infrastructure_failure=false` for all three. Case totals include regression and must not be summed as unique tasks. Under `any-case`, outer CLI exit 0 does not mean all original cases passed. 15 container commands exited 0; that fact does not override the grader. Two argument-invalid actions were rejected, with no demonstrated single causal relation to the seven failures.

## Conditions and portable recipe

The actual model was `codex/gpt-5.6-terra`. Each checkpoint allowed 48 calls and a 7200-second owner wait. The run used 24 real calls and zero post-limit excess calls, elapsed 998.822568 seconds. Upstream cost/net-cost/step caps were zero (disabled). Normalized task token/USD totals are unavailable, not measured zero; retained per-call usage is a different accounting layer. The original tuple means settled calls/post-limit excess, not real/fake calls.

Solver network was none in a fresh container at each checkpoint; snapshots carried source forward. Build and evaluation used host networking. A same-version download compatibility adaptation used proxy/pipefail/retry; upstream tests and grader were unchanged. The original evaluator ran; official `AgentRunner`, full five-checkpoint execution and quality judging did not. There was no grader feedback to the solver, retry or resume.

With the historical installed source, exact upstream checkouts, existing prepared solver image and authorized private execution selection, the portable command shape is:

```sh
python -m examples.slopcodebench.run   --runner-source "$SCB" --problems-source "$PROBLEMS"   --environment "$SCB/configs/environments/docker-python3.12-uv.yaml"   --template "$SCB/configs/prompts/just-solve.jinja"   --execution "$EXECUTION" --codex-binary "$OFFICIAL_CODEX"   --condition examples/slopcodebench/condition.example.json   --output "$NEW_OUTPUT" --prefix 3 --pass-policy any-case   --acknowledge-development-model-run
```

This is an instruction, not a new run or a byte-complete solver-image reconstruction. The [example guide](../../../examples/slopcodebench/README.md) specifies setup and isolation requirements. Original pytest abbreviated some actual output; omitted text and unretained vendor wire are not reconstructed. Exact-byte recovery does not resolve recorded fresh-reader provenance failures. The result does not establish a causal harness advantage or validate V6.
