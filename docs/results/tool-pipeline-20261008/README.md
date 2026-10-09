---
name: rpnh-result-tool-pipeline-20261008
description: "Atomic-tool pipeline native validation"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

English | [中文](README_ZH.md)

# Atomic-tool pipeline native validation

The deterministic electricity-bill workflow was tested on `00f2d29c7deffed44e2ec635a24f390c6e0d9ace` plus 16 example files, with unchanged core. The exact source-set digest is `a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`. Publication at `80a17c3ce45ec3c7a1b39c170c36bbb922276de1` retained those bytes; it was not a separate clean-checkout rerun. The [complete input/result/status projection](results.json) includes all 16 file identities, four synthetic input fixtures, all test IDs and readback counts.

| Check | Result |
|---|---|
| Default native suite | 22 passed |
| Supplementary distinct checks | 10 passed |
| Total | 32 unique IDs / 33 executions; one join case repeated |
| Standard CLI | complete; **2.000 kWh / 1.70 CNY**; 10 firings, 12 tool products and 2 source resources |
| Per-interval rounding | **0.02 CNY**; two 5 Wh intervals at 100 fen/kWh |
| Concurrency | Two read workers overlap; single-worker fixture max active 1 |
| Fresh-process readback | Stable fields equal; event ordinal/count 1001 and dispatch/execution starts 10 unchanged; model counts `[0,0]` |

The independent scorer is the example's integer-fen half-up validator, not an external benchmark grader. It recomputes each amount from original integer Wh/fen inputs instead of trusting the calculator. These are real AF_UNIX owner runs with zero model calls; the suite also includes unit checks, so 32 tests are not 32 socket integrations. Earlier cloud native attempts were AF_UNIX-EPERM blocked; pipe semantic checks remain separate historical evidence and cannot certify native transport.

From the historical source with its supplied fixtures and existing dependencies, use new short native paths RUN, EXPORT and READBACK. Execute readback only after the original CLI exits:

```sh
python -B -m pytest -q examples/tool_pipeline/tests
python -B -m examples.tool_pipeline.run --run-dir "$RUN" --output-dir "$EXPORT"
python -B -m examples.tool_pipeline.run --readback --run-dir "$RUN" --output-dir "$READBACK"
python -B -m examples.tool_pipeline.run   --usage examples/tool_pipeline/fixtures/usage-rounding.json   --tariff examples/tool_pipeline/fixtures/tariff-rounding.json   --run-dir "$ROUND_RUN" --output-dir "$ROUND_EXPORT"
```

These are parameterized reproduction instructions; this documentation task ran none of them. Supplementary historical probes are identified by test ID but are not redistributed. See the [example guide](../../../examples/tool_pipeline/README.md). No business-model score, comparative performance claim or combined V6 acceptance follows from this fixture.


The [distribution manifest](MANIFEST.json) identifies these rewritten summary/projection bytes, not original materials.
