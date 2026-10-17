# Multi-user release handoff

English | [中文](README_ZH.md) · [Examples](../README.md) · [Run guide](RUNNING.md)

Four controlled participants coordinate a SQL release through independent agent Tasks, Registries and private business PNs. The original episode completed by restoring native checkpoints in the same business world: revised note v2, original SQL, QA checklist, one support incident and final handoffs are retained. The final read-only audit passed **98/98 required checks** (106 PASS and 2 optional UNKNOWN across 108).

[![Operations: actual private agent PN, complete](figures/operations-petri.png)](figures/operations-petri.png)

[Full-resolution role PN](figures/operations-petri.png) · [Agent step detail](figures/operations-node.png) · [Native execution PN](figures/operations-execution-finalization.png) · [All five PNs and Source details](figures/README.md)

## What the agents actually do

- Operations creates the canonical release note, revises its SQL semantics and rollback guidance, then reconciles version-specific confirmations.
- Developer reads that note and commits the required SQL on the requested branch.
- QA and support read the same canonical note; their native read claims overlap. A competing write returns BUSY with no write effect. They release the initial claims and reread v2 before confirming.
- QA commits the checklist. Support creates the incident with its original fields and separate validation evidence. Operations publishes final bundles to developer, QA and support.

These are controlled local principals under one trusted HOST, not independently authenticated external people. Each role owns its own Task/Registry and AgentLoop workspace. The designer generated two agent stages for operations, QA and support, and one for developer; mechanical lowering preserves those responsibilities. The shared **Source is a separate fifth Registry with a resource-control PN, not a fifth agent**. Local Git/Drive/incident adapters supply the domain environment; that domain does not replace the agent PNs.

## Completion, recovery and limits

The first window created note v1 and SQL, demonstrated overlapping reads and write exclusion, then stopped on stale queued-message read authority. Delivery now renews the exact recipient/message grant at the actual read boundary. Explicit native checkpoint reentry superseded two orphan PROVISIONAL Source roots without inventing completion. Run01 published v2 and completed QA, then a nested-JSON HOST serialization failure stopped support before backend dispatch. That effect was reconciled as absent; the old unknown receipt remains history.

Run02 restored compatible permanent cuts in the original world. Operations/support completed in generation 2; QA retained generation 1 and developer generation 0. V2, SQL and checklist were reused. Operations made no additional `update_note` attempt. Final Source has no active reads/firings/pending requests/edit, and the final world is consistent. Recipient consumption after final publication is not asserted.

[Curated checks and identities](result-summary.json) retain the audit denominator and optional missing developer/QA historical-cut coverage. Those two users were not reopened, so their absent `actual_restored` frames are expected. [Business record](RESULTS.md) explains the exact outputs and accounting. Screenshots are unchanged native Viewer captures, not redrawn workflows.

This closes an adapted release-documentation and coordination task. SQL execution, deployment, executed rollback and downstream consumer acceptance remain unverified. The original judge is **NOT_RUN / NOT_COMPARABLE_ADAPTED**; no official benchmark score or whole-product multi-user acceptance follows.

## Use the example

Follow [Run and inspect](RUNNING.md) for source prerequisites, the original upstream task, a no-provider preflight and a separately authorized fresh model run. This is a source example, not a named wheel export. A new run creates new identities/world; it does not reproduce the retained world's checkpoint recovery. Historical recovery is described above rather than offered as a portable private-state command.

The [release checks](release-validation.json) cover the pinned original-input download, exact guide configuration, the 13-call no-provider native preflight, 80 runtime/example boundary checks, three fresh-driver control tests and an isolated package install. Fresh execution stops waiting peers after a nonterminal return and requires empty native Source/HOST state before reporting completion. These release checks reuse the historical episode rather than starting another paid run.
