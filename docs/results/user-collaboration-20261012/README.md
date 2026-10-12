---
name: rpnh-user-collaboration-results-20261012
description: "One controlled native collaboration study and its measured tradeoffs."
metadata:
  document-kind: result-report
  audience: application-developer-and-researcher
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-12.1"
  status: recorded-result
---

English | [中文](README_ZH.md) | [Results index](../README.md)

# Controlled multi-user collaboration: 12 October 2026

Three independent controlled **simulated Registry identities/sessions**, two real model conditions, and a same-user model switch completed a native proposal → review → explicit coordination → protected-branch merge → adoption → business execution → independent evaluation chain. The official sequence score improved on both fixed 512-route sets; mean travel time increased. This is one measured end-to-end capability case, not a comparison of models or a causal estimate of collaboration benefit.

[Full workflow panoramas and local structure Viewer](../../../examples/user_collaboration/README.md) · [Exact aggregate values](results.json) · [Source and figure identities](SOURCES.json) · [Collaboration API](../../guides/user-collaboration.md)

## Conditions and actual work

A, B and C were separate controlled Registry identities, not three human participants or external accounts. A and C used `gpt-5.6-terra` (medium); B first used `deepseek-v4-pro` (effort undeclared), then Terra in the **same B session and cumulative budget**. Terra used native Codex app-server 0.162.0; DeepSeek used the existing external-provider input port. These are recorded conditions, not required versions for readers.

The retained runtime was source `c26cf943eb9d32d14646fe411da6332ace9a3c24`, installed wheel SHA-256 `66e55af8ff6e7a8156441e8a6f622628002d62cba7bb28d2259015ab501fbad4`, plus the eight runtime overlays identified in [SOURCES.json](SOURCES.json). It was not an unmodified wheel run. A single trusted HOST coordinator preserved original identity, access and Registry accounting. Authoring had bounded inputs; scorer labels were reserved for the separate evaluators.

There were **22 logical calls and 22 provider-attempt specifications**: A 15, B 4, C 3; no post-limit excess. They include failed authoring attempts. Upstream HTTP-attempt counts are unknown. The configured ceilings were 72 overall, 24 per actor across profiles and 12 per node, with tighter task limits retained.

| Stage | Recorded outcome |
|---|---|
| A1–A4 | Tool-order, history-size or invalid-PN failures retained; A1 made no model call |
| A5 | Valid proposal: batches 0–10 use existing `deadline_first` |
| B1 | Independent proposal: batches 11–17 use existing `zone_nearest` |
| C1 | Independent proposal: batches 12–23 use `zone_nearest` |
| B2 | Same B switched models; withdrawn batch 11 failed review and was not adopted |
| C2 | Explicit peer coordination retained B's batch 11 and C's batches 18–23 |
| M2 | Original two-parent merge of B1/C2; owner selected the already assembled incoming C2; protected branch advanced and result was actually adopted |

B1/C1 overlapped on six batches. The common 74-operation business budget produced a coupled-contract merge conflict. C2 assembled the strategy union before the merge; owner selection resolved it. This was **explicit coordination followed by native merge/adoption**, not automatic conflict-free fusion. Copying B's change into C2 does not create a second independent contribution. A's 11 changes remained. Only existing strategy configurations changed; graph topology and available algorithms did not.

## Measured results

Each of development and eval had a fixed 512-route population, baseline and adopted candidate. Four native business arms completed, each with 74 firings, 98 products and 25 entry resources. Two separate native evaluator runs used the same pinned original scorer, not two independently implemented scoring algorithms. All four arms had 512 finite scores, 512 structurally valid sequences, capacity PASS for 512 routes, and zero recorded scorer errors.

| Metric | Development baseline → candidate | Eval baseline → candidate |
|---|---:|---:|
| Official sequence score, lower is better | 0.109175 → 0.100959 (−7.525%) | 0.103382 → 0.094723 (−8.376%) |
| Mean travel, seconds | 10,998.19 → 11,846.74 (+7.715%) | 10,173.91 → 11,030.64 (+8.421%) |
| Time-window FAIL / UNKNOWN | 182 / 330 → 75 / 437 | 161 / 351 → 79 / 433 |
| Routes with lower / equal / higher score | 324 / 1 / 187 | 320 / 18 / 174 |

`VALID` means permutation/structural validity. Time-window UNKNOWN is not PASS. In the paired records, 126 development and 99 eval routes moved FAIL→UNKNOWN, while 19 and 17 moved UNKNOWN→FAIL. Checked/unknown package counts were unchanged within each pair. Thus fewer observed violations did not establish full constraint satisfaction. Travel increased on 407/512 development and 437/512 eval routes.

The nonrandom `deadline_first` groups worsened sequence score on average (+0.027285 development / +0.030937 eval); `zone_nearest` groups improved it (−0.034330 / −0.032023). Both groups increased mean travel. This descriptive stratification, overlapping edits and different prompts/transports/attempt counts cannot assign independent causal credit to an actor, model or model switch.

## Scope, reuse and limitations

- The fixed evaluation population was retained. Its largest original same-station batch was split deterministically into adjacent halves to use 24 batches without changing route membership, order or the 512 denominator.
- The available evaluation corpus had 3,052 routes, versus 3,072 in the dataset description. The 20-route discrepancy remains unresolved; no missing data were fabricated.
- Model attempts were not all clean successes. B2's conflicting guidance and C2's later coordination are observed conditions, not a controlled prompt or model comparison. No generic superiority, new routing algorithm or arbitrary-scale collaboration claim follows.
- The published artifacts support offline result reading and interactive inspection of **static declaration structure**. The two PNGs are original runtime captures. The full experiment depended on an existing local installation, admitted dataset, private model profiles and retained Registry state. This folder is not a standalone fresh-trial experiment or a public interactive replay of that state.
- No model, business experiment or scoring run was repeated to publish this report. Later product changes do not retroactively change the tested source identities.

## References and data terms

The [Amazon Last Mile Routing Research Challenge dataset record](https://registry.opendata.aws/amazon-last-mile-challenges/) describes the data and its **CC BY-NC 4.0** terms; the RPNH software license does not replace those data terms. Raw routes, package labels, travel matrices, credentials and private Registry/transcript material are not bundled here. Dataset methodology: [Merchán et al., Transportation Science, DOI 10.1287/trsc.2022.1173](https://pubsonline.informs.org/doi/10.1287/trsc.2022.1173).

Scoring used [MIT-CAVE/rc-cli `scoring/score.py` at `fdafa50e8b8a18d4cbff112ec432011952b92c7d`](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/score.py), SHA-256 `4ab51620391b3b5d131720518d2ce4c346f0eedb3d68afcf1ae9174b9e6b1540`. Its [scoring guide](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/README.md) defines the route score and mean; its [MIT license](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/license) is separate from the dataset license.
