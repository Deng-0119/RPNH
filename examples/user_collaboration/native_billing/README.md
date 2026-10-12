---
name: rpnh-native-billing-collaboration
description: "Historical ten-operation billing tool PN: controlled contribution, original adoption and dated execution evidence."
metadata:
  document-kind: example
  audience: trusted-host-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-12.4"
  status: historical-validated-local-native-and-viewer
  basis: "public synthetic tool pipeline; original Registry and HOST authority"
---

# Historical native billing contribution mechanism

English | [中文](README_ZH.md)

The current agent-contract collaboration entry is [curator → auditor co-building](../agent_cobuild/README.md). This page preserves the historical ten-operation billing tool PN and its original code, versions, images, results and failures. Two deterministic source programs change business-validation limits; neither those authors nor the billing operations are task agents. This record does not validate the current agent example.

In the recorded billing case, a usage contributor proposed the first revision, a tariff contributor extended that accepted revision, and the owner reviewed and adopted the final net. The native run and official Viewer were verified on 2026-10-12. The business result was **2.000 kWh / CNY 1.70**.

![Actual completed native billing PetriNet: all 28 nodes and 29 arcs.](figures/panorama.png)

Open the original image at full resolution. All images below are unedited captures of the same completed run:

- Usage: [contribution](figures/contributor-2.png), [proposal reference](figures/contributor-2-refs.png), [base revision](figures/contributor-2-base_revision_ref.png), [candidate revision](figures/contributor-2-candidate_revision_ref.png)
- Tariff: [contribution](figures/contributor-1.png), [proposal reference](figures/contributor-1-refs.png), [base revision](figures/contributor-1-base_revision_ref.png), [candidate revision](figures/contributor-1-candidate_revision_ref.png)
- Owner: [separate adoption action](figures/owner-adoption.png)

[Capture provenance and exact image hashes](figures/provenance.json) identify the result and its source window.

## The business task and contributions

The example reuses the public [ten-operation electricity pipeline](../../tool_pipeline/README.md): read usage and tariff, validate each input, normalize both, join intervals, compute cost, independently validate with integer arithmetic, and publish the report. Its original public synthetic fixture and expected result are unchanged.

Each contributor copies its exact accepted base through `branch_module(..., ExtractPlan(), output_mode="definition_only")`:

1. The usage contributor starts from the initial base and proposes a maximum of **100,000 Wh per usage interval**. The owner reviews and accepts this revision on the protected shared Branch.
2. The tariff contributor starts from that accepted usage revision and proposes a maximum of **1,000 fen/kWh per tariff interval**, retaining the usage limit. The owner reviews and accepts this successor.
3. The owner explicitly adopts the revision containing both contributions. The original `OwnerEventLoop` and `Orchestrator` then execute the ten-operation business PN.

The original validators use a 1,000,000,000 integer ceiling. The example's illustrative limits tighten business input validation. They are not security permissions, account limits or changes to the billing arithmetic. The thin adapter calls the original validators and retains their complete/rejected product types. A pure boundary test shows that 100,001 Wh is accepted by the base and rejected by the usage contribution. The valid fixture passes both adopted limits and produces the original report.

This is a **single-parent sequence of accepted revisions**. It does not demonstrate an automatic same-base merge: the conservative resolver groups the connected billing edits as one `coupled_contract`, whose choices select a whole side. The example retains both contributions through accepted parent ancestry; a pure regression covers the resolver's existing behavior.

## Original authority and recorded authorship

The owner and two contributors have three distinct original task Registries, principal records and HOST sessions. One trusted local process controls all three. They are simulated local actors, not three human participants, external authenticated accounts or separate OS security domains.

Each deterministic contributor program performs one native source operation, derives a complete revision from its exact accepted base bytes, and publishes stock TEXT through the original gateway. `ingest_completed_text` re-derives the successful source firing, invocation, product and principal. Each proposal has its own delivery witness. Its `synthetic_fixture=false` field identifies the original execution-provenance route; the actors and business inputs remain synthetic.

The target's publication principal does not replace the recorded proposer. The owner approves each exact proposal, performs two protected Branch advances, and separately adopts the final revision through `owner.control.edits.submit`. The original product commit boundaries generate both advance receipts and the adoption receipt. Execution uses the existing scheduler and permission checks; no model, provider, wallet or external business service is called.

## Run and inspect

Use an existing compatible RPNH environment and this source checkout. The example does not install software or select a model/provider. Choose a short writable work path that does not yet exist:

```sh
python -m examples.user_collaboration.native_billing.run --work-dir /path/to/short/fresh-work --serve
```

Every invocation creates fresh canonical identities and a new recorded run. Do not reuse a previous work directory. The runner checks the canonical result and writes derived `evidence.json`, `dashboard.json`, `contributions.json`, and `report.json` into its `export` directory. These are local observations; the original Registries remain authoritative. Runtime exports and session artifacts should remain outside the public repository.

`--serve` keeps the original HOST/session binding alive and prints the actual loopback Viewer URL. The default port is 8774; use `--port` to change it. The owner grants a one-hour read scope, and the example's read session uses that same lifetime, bounded by the original grant expiry. The official `UserContributionView` rechecks read authority on every request.

To reopen the **same result**, keep that `--serve` process alive and revisit its printed URL before the grant expires. This reads the completed runtime without executing the business again. This viewing entry is scoped to the live trusted HOST: a persisted Registry path or exported JSON does not restore the contribution session after the process exits or the grant expires.

In the Viewer:

1. Open **PetriNet** to inspect all 28 nodes and 29 arcs. A pure-tool agent Overview can be empty; **Detailed flow** shows the business-tool structure.
2. Open **User contributions**, select either recorded proposer, and focus `check_usage_input.run` or `check_tariff_input.run`.
3. Inspect `value/config/max_integer` and its exact proposal/base/candidate references.
4. Open **Separate action evidence without node mappings** for the owner's adoption action.

## Historical result and source identity

The recorded native run has two completed source TEXT firings, two approved proposals, two protected Branch advances, one owner adoption, ten settled business firings, twelve business resources and one complete business terminal. The three model counters are `[0, 0]`. The complete report matches the original fixture field for field: **2.000 kWh / CNY 1.70**.

The actual browser capture was checked against the original Registry export, including both proposers' fields and exact references and the separate adoption action. Inspection left head **1318**, event count and writer epoch unchanged. The PNGs preserve original opaque actor/session/checkpoint references and observation times; these are technical labels, not personal identity mappings or credentials. No image pixels were altered.

[SOURCES.json](SOURCES.json) pins the eight reused public billing files and synthetic fixtures. [Provenance](figures/provenance.json) records the actual executed product/example tree fingerprints separately. The billing fixture pin does not identify the executed product tree, and the dated result does not certify a later combined source tree. Seven pure checks passed in the final local validation; they prohibit Registry creation, socket bind/connect/listen and subprocess execution:

```sh
python -m unittest discover -s examples/user_collaboration/native_billing/tests -v
```

This example demonstrates recorded contributions and native execution of one shared PN. It does not establish collaboration quality, remote identity authentication, production billing readiness or automatic crash recovery.

See the [native collaboration guide](../../../docs/guides/user-collaboration.md), [Viewer contribution guide](../../../docs/guides/viewer-user-contributions.md), [retained route-planning study](../README.md) and [MIT license](../../../LICENSE).
