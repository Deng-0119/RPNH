# Agent-network co-building and historical mechanisms

English | [中文](README_ZH.md) | [Detailed results and analysis](../../docs/results/user-collaboration-20261012/README.md)

## Current collaboration entry

The current collaboration entry is [agent-network co-building](agent_cobuild/README.md). Two native task-agent roles, `curator.run` and `auditor.run`, use stock `AgentStage`/default `AgentLoop` in an ordinary Module assembled from two stock single-agent declarations, with `curator.result → auditor.request`. Deterministic source programs from two controlled original principals strengthen the curator input-lineage contract and auditor reconciliation contract. The original HOST reviews exact revisions, advances a protected Branch sequentially and enables owner adoption before target execution.

Source programs, external control actions and target agents remain separate; this single-parent chain does not imply automatic same-base merging. A `local_process` scripted driver computes from actual inputs and tool returns. This demonstrates structure and protocol using registered task agents. It makes no live-model or cognition claim; execution results, artifacts and Viewer evidence are reported on the example page.

The initial generated `AgentWorkflowGraph` attempt was correctly rejected by the existing plain-proposal `designer_constraints` gate. The example instead assembles two stock single-agent declarations into an ordinary Module. It does not strip graph constraints, change a Registry gate, add an adapter or introduce another scheduler.

## Historical route-planning mechanism evidence

The following preserves the original 74-operation business-tool net, code, versions, results, images and failures. Its 24 ingest → plan → validate batches plus aggregate/publication are business computation; models proposed configuration outside the net. These operations and authors do not count as task agents. [Historical billing](native_billing/README.md) retains ten-operation tool-net contribution/adoption mechanisms. Neither historical record validates current co-building execution.

Three independent controlled simulated Registry actors, A/B/C, contributed configuration changes to the same 24-batch route-planning network. Each had a separate actor and session under one trusted HOST; these were not three human participants or external accounts. Models proposed changes, the owner accepted a revision, and deterministic HOST tools executed the adopted business Petri net.

### Original adopted business net in the official Viewer

[![Original adopted-M2 runtime Detailed flow panorama: all 24 batch branches and final aggregation](figures/adopted-M2.png)](figures/adopted-M2.png)

This unedited 4485 × 5000 capture shows the original **development adopted-M2 runtime**: admitted route inputs, all 24 ingest → plan → validate branches, aggregation, publication and the result. It has 74 settled firings and zero unsettled. Detailed flow folds 97 handoff places, displaying 100 nodes / 123 edges from the full 197-node / 220-arc Petri net. Open the original image to inspect the entire network.

The cards are business operations. A/B/C are the configuration authors recorded separately below; they are not three runtime Agent nodes in this tool-only PN. The screenshot shows execution state, without per-author coloring or author/revision badges.

### Which operations each participant changed

The retained configuration declarations identify the planning operations as `batch_0.plan` through `batch_23.plan`. The edits below change each operation's `config.strategy`; places, transitions, arcs and available algorithms keep the same topology.

| Participant and recorded proposal | Planning operations affected | Content retained in adopted M2 |
| --- | --- | --- |
| A, common revision A5 | `batch_0.plan`–`batch_10.plan` | Eleven changes from `nearest_neighbor` to `deadline_first` |
| B, independent proposal B1 from A5 | `batch_11.plan`–`batch_17.plan` | `zone_nearest`; batch 11 is unique to B, while batches 12–17 also appear in C1 |
| C, independent proposal C1 from A5 | `batch_12.plan`–`batch_23.plan` | `zone_nearest`; batches 18–23 are unique to C, while batches 12–17 overlap B1 |
| C, coordinated revision C2 | Adds B's batch-11 change to C1 | The complete `zone_nearest` configuration for batches 11–23, retaining A's 0–10 configuration |
| A, owner decision | Selects incoming C2 in the original B1/C2 merge, advances the protected branch and adopts M2 | The accepted configuration is used in the runtime shown above |

This table is a document-level correspondence between the published module declarations and the [receipt-derived contribution record](visuals/collaboration-evidence.json). It is not an attribution overlay exported by the Viewer. The six overlapping B/C changes count once in the final content; C2's incorporation of B's change is reuse, not a new independent C contribution. B2 was not adopted. The [detailed result record](../../docs/results/user-collaboration-20261012/README.md) retains unsuccessful proposals, the recorded coordination and merge decisions, model usage and their evidence limits.

### Original baseline for comparison

[![Original baseline runtime Detailed flow panorama](figures/baseline.png)](figures/baseline.png)

This second unedited 4485 × 5000 capture is the original **development baseline** with the same topology and `nearest_neighbor` in all 24 planning operations. It also has 74 settled firings and zero unsettled.

Both images retain their original technical checkpoint identifiers and observation times. These identify recorded execution state, not a person or account, and are not access tokens. The PNGs contain no textual/EXIF metadata. Neither has been redrawn, relabeled or presented as a new run. Their exact byte identities are in [SOURCES.json](../../docs/results/user-collaboration-20261012/SOURCES.json). Completed execution does not imply all route business constraints passed.

Adopted M2 and baseline were evaluated on fixed development and evaluation sets of 512 routes each. Official sequence scores decrease by about **7.525% / 8.376%**, while mean travel cost increases by about **7.715% / 8.421%**. Time-window failures and unknowns remain. These outcomes do not measure a causal benefit from collaboration; see the [full results and tradeoffs](../../docs/results/user-collaboration-20261012/README.md).

### Open the static structure in the official Viewer

From a source checkout with its compatible RPNH environment already available:

```bash
python examples/view_declaration.py collaboration-baseline
python examples/view_declaration.py collaboration-adopted
```

Add `--no-open` to print the actual loopback URL instead of opening the browser. In the Viewer choose **PetriNet** for every place/transition/arc or **Detailed flow** for the pure-tool flow. Pan, zoom and node inspection use the existing official UI. No model, dataset, provider profile or retained runtime Registry is needed for these bundled frames.

These commands show **initial_configured static declarations** compiled from the recorded baseline and adopted modules, with payloads and runtime identities excluded. Empty/unmarked state is declaration state, not the completed execution shown in the PNGs. There is no execution history, checkpoint replay or live run. Both declarations have the same topology; the candidate uses `deadline_first` on batches 0–10 and `zone_nearest` on 11–23, replacing baseline `nearest_neighbor` everywhere. The exact configuration declarations are [baseline](declarations/BASELINE_MODULE.json) and [adopted M2](declarations/ADOPTED_M2_MODULE.json).

### What is reproducible here

You can read the public aggregate results, inspect both retained runtime images and open the two static structures. The entire real-model experiment is not a standalone download-and-run package: it used an existing installation, admitted routing data, user-owned profiles and private Registry state. Dataset terms and scorer/source pins are in the report. Do not interpret the static Viewer as a public replay of that private runtime.

For building a collaboration application, see the [native collaboration guide](../../docs/guides/user-collaboration.md). For the current agent-contract example, use [curator → auditor co-building](agent_cobuild/README.md).
