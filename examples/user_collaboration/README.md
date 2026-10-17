# Agent-project collaboration and historical mechanisms

English | [中文](README_ZH.md) | [Run the current example](../project_collaboration/README.md) | [Historical route results and analysis](../../docs/results/user-collaboration-20261012/README.md)

## Current collaboration entry

Start with [copy, co-create and reuse one agent project](../project_collaboration/README.md):
two controlled contributors resolve conflicts into original two-parent M; three
pre-business content adoptions precede exact-M download and independent receiver
execution. Both ends use provider-backed stock implementer/reviewer agents.
The example includes the original task, inputs, environment, verifier, licenses,
actual business and execution PN captures, failures and dated acceptance limits.

### Original current-project captures in the official Viewer

[Source completed Agent Overview: Implementer → Reviewer](../project_collaboration/figures/source-overview.png)

The completed source has two task agents and one structural handoff edge; the [exact observation](../project_collaboration/figures/source-overview.json) retains the original object references.

[![Full business PN of the same run: five nodes and six arcs](../project_collaboration/figures/source-petri.png)](../project_collaboration/figures/source-petri.png)

The same run's full business PN has five nodes and six arcs and retains the original interrupted firing. Both images preserve source-05's original bytes; the [business-PN observation](../project_collaboration/figures/source-petri.json) and [capture provenance](../project_collaboration/figures/provenance.json) record their source and exact positions.

## Historical scripted contract co-building

Start with [agent-network co-building](agent_cobuild/README.md): `curator.run` preserves input lineage and produces the handoff; `auditor.run` reconciles each source and registers the final report. The two native task agents use stock `AgentStage`/default `AgentLoop` in one ordinary Module, connected by `curator.result → auditor.request`.

Deterministic source programs from two controlled original Registry principals contribute contract revisions. The original HOST reviews them, advances a protected Branch sequentially and enables owner adoption. The target’s `local_process` scripted driver computes from actual inputs and tool returns; this validates structure and protocol without live-model calls.

### Historical scripted task network in the official Viewer

[Actual Agent Overview: Curator → Auditor](agent_cobuild/figures/overview-panorama.png)

Read-only captures of the original 2026-10-12 UTC scripted protocol run: the Overview shows two originally registered task agents and one structural handoff relation. See [co-building details and run evidence](agent_cobuild/README.md) for the two controlled source principals' contributions and owner adoption.

[![Actual main-task PetriNet: five nodes and six arcs](agent_cobuild/figures/petri-panorama.png)](agent_cobuild/figures/petri-panorama.png)

The full main-task PN has five nodes, six arcs and two settled firings. Its framework interruption-return paths were not run; file-I/O execution child nets are recorded separately. Both images retain their original bytes from read-only capture-02 of the same target; [provenance](agent_cobuild/figures/provenance.json) records unchanged head/event count 1099 and writer epoch 2 before and after capture.

Open the [contributions and artifacts](agent_cobuild/README.md#what-each-participant-changes), [run with an existing compatible installation](agent_cobuild/README.md#run-with-an-existing-compatible-installation), or review the [implementation and validation scope](agent_cobuild/README.md#implementation-and-validation-scope). Source programs, external control actions and target agents remain separate; sequential single-parent revisions do not imply automatic same-base merging.

### Implementation note

The initial generated `AgentWorkflowGraph` attempt was correctly rejected by the existing plain-proposal `designer_constraints` gate. The example instead assembles two stock single-agent declarations into an ordinary Module. It does not strip graph constraints, change a Registry gate, add an adapter or introduce another scheduler.

## Historical route-planning mechanism evidence

The following preserves the original 74-operation business-tool net, code, versions, results, images and failures. Its 24 ingest → plan → validate batches plus aggregate/publication are business computation; models proposed configuration outside the net. These operations and authors do not count as task agents. [Historical billing](native_billing/README.md) retains ten-operation tool-net contribution/adoption mechanisms. Neither historical record validates current co-building execution.

Three independent controlled simulated Registry actors, A/B/C, contributed configuration changes to the same 24-batch route-planning network. Each had a separate actor and session under one trusted HOST; these were not three human participants or external accounts. Models proposed changes, the owner accepted a revision, and deterministic HOST tools executed the adopted business Petri net.

### Original adopted business net in the official Viewer

[![adopted-M2 complete static PN: 197 nodes / 220 arcs](figures/adopted-M2/petri-full.png)](figures/adopted-M2/petri-full.png)

This is the complete PN of the real [initial_configured declaration](figures/adopted-M2/declaration-frame.json): 74 transitions, 123 places and 220 arcs, retaining all 24 batch branches and final aggregation. It has no historical tokens, firings or completion coloring and does not inherit the 74 settlements described below. [Image/exact-input provenance](figures/adopted-M2/petri-provenance.json) · [Official static Viewer](#static-route-pn-viewer) for zoom, pan and node/arc selection. Click the image for the 9000 × 7000 original.

[Original adopted-M2 runtime Detailed flow](figures/adopted-M2.png)

The linked historical runtime Detailed flow capture (unedited, 4485 × 5000) shows the original **development adopted-M2 runtime**: admitted route inputs, all 24 ingest → plan → validate branches, aggregation, publication and the result. It has 74 settled firings and zero unsettled. Detailed flow folds 97 handoff places, displaying 100 nodes / 123 edges from the full 197-node / 220-arc Petri net. Open the original image to inspect the entire network.

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

[![baseline complete static PN: 197 nodes / 220 arcs](figures/baseline/petri-full.png)](figures/baseline/petri-full.png)

This is the complete PN of the real [initial_configured declaration](figures/baseline/declaration-frame.json): 74 transitions, 123 places and 220 arcs, retaining all 24 batch branches and final aggregation. It has no historical tokens, firings or completion coloring and does not inherit the 74 settlements described below. [Image/exact-input provenance](figures/baseline/petri-provenance.json) · [Official static Viewer](#static-route-pn-viewer) for zoom, pan and node/arc selection. Click the image for the 9000 × 7000 original.

[Original baseline runtime Detailed flow](figures/baseline.png)

The linked historical baseline Detailed flow capture (unedited, 4485 × 5000) is the original **development baseline** with the same topology and `nearest_neighbor` in all 24 planning operations. It also has 74 settled firings and zero unsettled.

Both historical Detailed flow images retain their original technical checkpoint identifiers and observation times. These identify recorded execution state, not a person or account, and are not access tokens. The PNGs contain no textual/EXIF metadata. Neither has been redrawn, relabeled or presented as a new run. Their exact byte identities are in [SOURCES.json](../../docs/results/user-collaboration-20261012/SOURCES.json). Completed execution does not imply all route business constraints passed.

Adopted M2 and baseline were evaluated on fixed development and evaluation sets of 512 routes each. Official sequence scores decrease by about **7.525% / 8.376%**, while mean travel cost increases by about **7.715% / 8.421%**. Time-window failures and unknowns remain. These outcomes do not measure a causal benefit from collaboration; see the [full results and tradeoffs](../../docs/results/user-collaboration-20261012/README.md).

<a id="static-route-pn-viewer"></a>

### Open the static structure in the official Viewer

From a source checkout with its compatible RPNH environment already available:

```bash
python examples/view_declaration.py collaboration-baseline
python examples/view_declaration.py collaboration-adopted
```

Add `--no-open` to print the actual loopback URL instead of opening the browser. In the Viewer choose **PetriNet** for every place/transition/arc or **Detailed flow** for the pure-tool flow. Pan, zoom and node inspection use the existing official UI. No model, dataset, provider profile or retained runtime Registry is needed for these bundled frames.

These commands show **initial_configured static declarations** compiled from the recorded baseline and adopted modules, with payloads and runtime identities excluded. Empty/unmarked state is declaration state, matching the new PN captures rather than the completed execution in the historical Detailed flow PNGs. There is no execution history, checkpoint replay or live run. Both declarations have the same topology; the candidate uses `deadline_first` on batches 0–10 and `zone_nearest` on 11–23, replacing baseline `nearest_neighbor` everywhere. The exact configuration declarations are [baseline](declarations/BASELINE_MODULE.json) and [adopted M2](declarations/ADOPTED_M2_MODULE.json).

### What is reproducible here

You can read the public aggregate results, inspect both retained runtime images and open the two static structures. The entire real-model experiment is not a standalone download-and-run package: it used an existing installation, admitted routing data, user-owned profiles and private Registry state. Dataset terms and scorer/source pins are in the report. Do not interpret the static Viewer as a public replay of that private runtime.

For building a collaboration application, see the [native collaboration guide](../../docs/guides/user-collaboration.md). For current project co-creation and independent reuse, use [project collaboration](../project_collaboration/README.md); [curator → auditor co-building](agent_cobuild/README.md) preserves the historical scripted contract example.
