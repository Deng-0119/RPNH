---
name: rpnh-result-erp-first-wave-20261008
description: "ERP-Bench historical first-wave results"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

English | [中文](README_ZH.md)

# ERP-Bench historical first-wave results

These are individual historical ERP-Bench runs on two different tasks. They are not a matched comparison, full benchmark, or test of the combined V6 product. The [complete score and status projection](results.json) includes all emitted metrics, rule outcomes and historical attempts; it is a rewritten projection, not original verifier bytes.

## Source and inputs

The adapter base was `ae09445fe1d9b973502bc5d2c961976c1d2c0163`. A04 and H01 used clean tracked source `6f8ee2e406f3c70edb73206f861e56a0202b9f15`, with the 35 ERP-owned files identified in `results.json`. Their first publication was `74fad32d369876841686d10d33361c016e3d3648`; publication is not a rerun. The task/scorer source is [ERP-Bench at ceba3880](https://github.com/agentic-labs/erp-bench/tree/ceba3880af555129b5278e056a0c20f2fb5a0ba9). Input task manifests, seeds and file identities are included in the projection.

| Run | Task | Tested commit | Business score | Applicable checks | Real calls |
|---|---|---|---|---|---|
| A01 / s01 | `2000_easy_01_buy_only_baseline` | `8980e1f0552d30f490af08e60d4e8e3724ea638e` | unavailable; blocked before world/owner/model execution | unavailable | unknown |
| A02 / s02 | same smoke task | `5df13012e08b474cc0fe86d9531b4df5d5325c08` | unavailable; no proven quiescent world for evaluation | unavailable | unknown |
| A03 / s03 | same smoke task | `2ca5fbceb058e4ffe3d5b761ca3a6834dac8f621` | **0/100, failed** | 4/32; 6 NA | 11 |
| A04 / s04 | same smoke task | `6f8ee2e406f3c70edb73206f861e56a0202b9f15` | **100/100, passed** | 37/37; 1 NA | 9 |
| A01 / h01 | `2299_hard_repair_plan_hard` | `6f8ee2e406f3c70edb73206f861e56a0202b9f15` | **21/100, failed** | 86/95; 0 NA | 13 |

A03, A04 and H01 completed native/provider/evaluation stages; their separate offline/mock stages were not run within those live conditions. A01 has blocked native/provider/evaluation stages; A02 has failed native/provider stages and blocked evaluation. Unknown accounting is not zero. H01's nine failed checks comprise four constraints and five purchase-origin checks. Constraint points were 63/75, hygiene 15/20 and reported optimality 91.66/100. The original constraint gate yields the overall 21/100; the other metrics must not be substituted for it. All A04/H01 rule rows, including NA, are retained without rule argument text or checker logs. A03's complete aggregate metrics and stage statuses are retained; request-dependent diagnosis is withdrawn.

## Conditions and commands

The recorded model condition was `gpt-5.6-terra` through `local_process`, maximum 12 turns per node, 4 parallel nodes and 900 seconds per request. Official timeouts were actor 3600 seconds, verifier 300 seconds and build 600 seconds. The adapted environment reported Harbor 0.24.0, Python 3.12.3, Odoo 19.0.20260926, PostgreSQL 18.6 and `odoo-client-lib` 2.0.0. Solver access was nonroot/network-none with task-local Odoo loopback. The declared 2048 MB storage is not a verified host writable-layer quota. H01 image/dependency/build identities and full model limits are included in the projection. Dependency installation used the declared uv compatibility adaptation, so these are `grader_compatibility` results.

The historical command kinds were RPNH TaskControl start/status/result/result_evidence/stop, the existing exact-model local-process execution and Harbor `Verifier.verify` with unchanged `tests/test.sh`. This portable CLI recipe requires the historical source, exact upstream, user-owned selection and separately supplied shared validator. It is an instruction, not a command executed by this documentation task:

```sh
rpnh-erp run --upstream "$ERP_SOURCE"   --task 2000_easy_01_buy_only_baseline   --execution-selection "$SELECTION" --run-root "$NEW_RUN"   --condition-id reproduction-smoke --source-root "$SOURCE"   --shared-validator "$VALIDATOR" --authorize-existing-model
```

For H01 select `2299_hard_repair_plan_hard` and a fresh world/condition. Historical build/firewall and Codex endpoint adaptations must be supplied explicitly as described in the [ERP guide](../../../examples/erp_bench/README.md); the recipe alone does not reproduce those prepared assets. No credentials or private selection is distributed. A separately cited integration-only offline window is excluded from these curated results because its command record does not identify exact tested source; it cannot certify an identified product revision.

Some historical resource provenance checks failed; exact-byte recovery does not resolve that provenance. A04 control transcripts and vendor HTTP/full Codex event streams were not collected at this boundary. Scores do not establish a single failure cause, runtime superiority or complete business acceptance.


The [distribution manifest](MANIFEST.json) identifies these rewritten summary/projection bytes, not original materials.
