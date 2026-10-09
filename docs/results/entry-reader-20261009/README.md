---
name: rpnh-result-entry-reader-20261009
description: "Historical H1 and H1+H2a native validation"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

English | [中文](README_ZH.md)

# Historical H1 and H1+H2a native validation

These are two distinct historical validations: H1 alone and the later H1+H2a reader overlay. They are not results for the combined V6 product. The [complete source/test/status projection](results.json) keeps both windows, every test ID, corrected paths and native readback counts.

## Source windows

H1 tested `674252feb836f631c162979f177d1fe91f22559f` plus seven files, frozen patch SHA256 `71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c`. Publication was `715468dab0b1bea07d7e94a7aa0606eaf194365c`. H1's local AF_UNIX focused36 and classic3 passed: **39 unique cases / 39 executions**, no failures/errors/skips. Earlier cloud pipe semantics were 39 unique / 50 executions; the cloud native attempt was BLOCKED_ENV. These counts are separate.

H1+H2a tested base `715468dab0b1bea07d7e94a7aa0606eaf194365c` plus five reader files, patch SHA256 `62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24`. Publication was `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`. Its 3493-entry actual worktree manifest SHA256 was `488ce0b6ffac52b468db3d3d5cdc87a263fcb95aadbcc48d4df1f81d26cb5323`, unchanged before/after; this digest is not a Git tree. The projection identifies every changed file. H1 has 39 tests and H2a 130 in this window, with classic3 shared once: **166 unique / 166 executions**, comprising 154 repository and 12 independent package cases, not 169.

| Combined historical window | Passed | JUnit seconds |
|---|---|---|
| h2-focused | 47 | 232.559 |
| h2-compatibility | 44 | 1.393 |
| h2-shared | 15 | 91.731 |
| h2-independent | 12 | 72.818 |
| h2-native-status | 7 | 2.635 |
| h2-native-callbacks | 2 | 0.606 |
| h1-classic | 3 | 162.904 |
| h1-focused | 36 | 745.261 |

The h2-shared path is `examples/harnessaudit_office/tests/test_registry_reader_consumers.py`; historical shortened paths are corrected without changing test names or outcomes. The earlier H2 status attempt remains 6 PASS / 1 AF_UNIX BLOCKED_ENV, and resume was blocked before provider dispatch/assertions. 20 expected old-baseline failures are historical, not new passes. Earlier shared-harness A remains PARTIAL_ENV and was not retested by H1/H2a.

## Native result and reproduction boundary

Python 3.13.12 / pytest 8.4.2 used real AF_UNIX, actual OS SIGINT and explicit resume; classic logical ledgers were `[2,0]`, `[3,0]`, `[3,0]` using scripted implementations, with zero real provider requests. Final OS handler identity was not separately probed. The suite includes unit cases and is not 166 socket integrations or a whole-repository acceptance.

Both native pipeline/readback records produced complete, **2.000 kWh / 1.70 CNY**, ten firings, 12 business products and two source inputs. All 11 stable fields matched in a new readback process; live-only `transport`/`stop_reason` were absent. Event ordinal/count 1001, dispatch/execution starts ten and model counts `[0,0]` remained unchanged. Inputs are the synthetic [pipeline fixtures](../tool-pipeline-20261008/results.json), not a business dataset; acceptance checks Registry state/field equality rather than an external scorer.

With the exact historical overlay and existing dependencies, the H1 focused command and one H2 reader window can be parameterized as follows, with TMP a short native directory:

```sh
python -B -m pytest -q -p no:cacheprovider   tests/test_orchestrator_boundary.py tests/test_harness_quiescence.py   tests/test_harness_resource_continuation.py examples/tool_pipeline/tests   --basetemp="$TMP/h1"
python -m pytest -q -p no:cacheprovider tests/test_task_control_registry_reads.py   --basetemp="$TMP/h2"
```

The projection contains all eight historical command selections with portable placeholders. Independent package tests require separately supplied historical fixtures and are not distributed here. Those recipes are not newly executed commands or a promise that a current checkout recreates the old windows. No real provider, Docker business world or benchmark was run by H1/H2a.


The [distribution manifest](MANIFEST.json) identifies these rewritten summary/projection bytes, not original materials.
