---
name: rpnh-technical-report
description: "A visual guide to RPNH process definitions, execution authority, observation and historical results."
metadata:
  document-kind: technical-report
  audience: application-developer-and-researcher
  language: en
  counterpart: technical-report_ZH.md
  revision: "2026-10-12.2"
  status: technical-report
  basis: "Deng-0119/RPNH at 6cec63ff5b7a0039611713961d00941767edaaf8"
---

English | [中文](technical-report_ZH.md) | [Documentation](index.md)

# RPNH Technical Report

## Executable processes for Agents and programs

**Updated:** 2026-10-12. **Implementation snapshot:**
[`6cec63ff5b7a0039611713961d00941767edaaf8`][snapshot].
Historical tests retain their own source identities in [§10](#10-historical-results-and-their-limits).
The dated native examples added below retain their own execution windows; the earlier report snapshot and historical model/benchmark results remain separate.

## Abstract

Agent applications need to coordinate work, retain the products of each step and
recover without confusing a plausible answer with a completed process. RPNH
makes those responsibilities explicit: typed Petri nets govern eligibility,
trusted HOST bindings supply execution, and a durable Registry records products,
settlement and checkpoints. This report follows a small runnable example, then
explains the execution and observation contracts it exercises. The evidence
combines scripted demonstrations, deterministic tools and separately identified
historical model-backed evaluations. It supports a concrete execution design;
it does not establish comparative quality, speed or cost gains.

## A worked example before the contracts

Consider a review that needs both a facts check and a risk check before a final
answer. In the checked-in `parallel` scenario, `prepare` registers a shared brief,
`facts` and `risks` consume their declared inputs, and `join` requires both branch
products. The declared maximum is two parallel nodes. These dependencies are
part of the [scenario definition][parallel-scenario], rather than a request for
an Agent to remember the order.

![Recorded parallel workflow in the official RPNH Viewer Overview, showing prepare, facts, risks and join.](../examples/workflow_patterns/assets/parallel-overview.png)

**Example figure A.** The official Viewer's existing Overview of the completed
scripted example. The four readable Agent nodes show the review structure;
Overview omits Petri-net places and arcs. The [paired PetriNet view and reproduction
instructions](../examples/workflow_patterns/README.md#parallel-fan-out-and-join)
show the lower-level representation. This retained image is not a new execution
of the report snapshot, and its topology alone does not prove completion or
simultaneous wall-clock execution.

The [runner][parallel-runner] accepts the example only when the stop reason is
terminal, a terminal-evidence reference exists, and the registered output equals
`Parallel workflow complete.` The fixture supplies prescribed text. It tests the
handoff and settlement protocol, not whether a language model can perform a
useful facts or risk review. The [dated public validation record][parallel-validation]
reports four transitions, nine places, 26 arcs and zero provider calls for this
scenario; it does not pin an exact tested commit, so it is historical example
evidence rather than validation of the current source.

After [installing the selected source](guides/installation.md), follow the
[workflow gallery](../examples/workflow_patterns/README.md) to create a fresh run
and open its read-only Viewer. No live profile is selected by default; supported
local process and IPC permissions are still required. A real model route must
be supplied separately and explicitly.

Two companion examples show why the same structure is useful beyond canned
review text. The [hybrid summary](../examples/hybrid_summary/README.md) places a
native calculation between two Agent nodes. The
[atomic-tool pipeline](../examples/tool_pipeline/README.md) separates usage and
tariff preparation, billing and independent validation into ten tool transitions.
Its [historical native result](results/tool-pipeline-20261008/README.md) preserves
the input, source and scorer for the reported 2.000 kWh / 1.70 CNY result. That
specific fixture establishes a calculation and evidence path, not a general
business-task success rate.

## Research context and reading route

Petri nets provide a long-established language for concurrency, synchronization
and state-based analysis; Murata's review also explains the trade-off between
model expressiveness and analysis complexity [1]. RPNH applies typed net and
Registry contracts around Agent and program execution. The ReAct work studies
interleaving language-model reasoning and actions [2]. Such Agent behavior and
RPNH's execution accounting address different levels of an application; neither
citation is evidence of a head-to-head comparison.

Read [§2](#2-eligibility-comes-from-actual-tokens-and-claims) and
[§3](#3-settlement-connects-computation-to-durable-state) for the worked example's
eligibility and result rules, [§4](#4-revise-definitions-before-explicitly-adopting-them)
for controlled changes, and [§10](#10-historical-results-and-their-limits) for
measured outcomes and failures. The eight numbered figures in the contract
sections are conceptual sketches, not run screenshots or complete schemas.
The linked references define the full contracts. RPNH is useful where persistent
outputs, explicit coordination or controlled process changes matter; it also
adds declaration and state-management work.

## 1. Separate the method from its execution

![Definitions select trusted bindings; RunOwner and Harness execute with Petri-net and Registry state; selected evidence is observed read-only.](assets/technical-report/01-architecture-en.svg)

**Figure 1.** Four related assets: definition, trusted binding, execution evidence
and observation. The arrows show the relationship, not a separate execution engine.

- **Definition:** `ModuleDeclaration`, Agent graph source and immutable author revisions.
- **Binding:** `Registration` resolves declared keys to trusted host implementations.
  A declaration cannot invent executable Python locators; reading compiled data
  does not register callables.
- **Execution:** one `RunOwner`/`Harness` path admits and settles work. `Orchestrator`
  uses the host's existing owner, event loop, workers and dispatch services.
- **Observation:** selected Registry projections and read sessions remain read-only.

Basic, Codex and OpenCode are presentations of the same direct main-session root;
its shared lease prevents competing writable presentations. DSH is a registered
host integration. Independent child tasks own separate Registries, nets and owners;
subordinate execution nets share the business Registry for mechanics such as
workspace finalization. Authored assembly membership is a definition relationship.
The independent child-task entry does not establish a complete native parent-bound
launch-to-parent-completion chain. Its production transport boundary is not wired
at this snapshot. See the [parent–child boundary][parent-child].

Details: [architecture](architecture/design.md), [runtime contracts](reference/runtime-registry.md).
Code: [declarations][module], [registration][registration], [compiler][compiler],
[Orchestrator][orchestrator].

## 2. Eligibility comes from actual tokens and claims

![Two branch transitions produce separate product occurrences; the join needs both. Circles denote places and rectangles denote transitions.](assets/technical-report/02-parallel-join-en.svg)

**Figure 2.** A fragment of `prepare -> (facts || risks) -> join`, after both
branches have settled. Their separate input occurrences have been consumed;
both output occurrences enable the join. The final output place is omitted.

Typed places, weighted arcs, outcomes and resource claims determine which firings
are valid. Consume, read, borrow, guard, produce and return modes have different
contracts. Place fusion shares a place; it does not copy a product for broadcast.

`Harness.schedule_ready` rehydrates the current net and marking, installs active
claims and respects in-flight capacity. An injected selector may choose only a
distinct subset of the enabled transitions. Start is recorded before a worker is
submitted. Scheduling preference cannot make an invalid firing eligible.

`AgentWorkflowGraph` is the convenient dependency-graph interface; general
`ModuleDeclaration` supports richer typed contracts. Agent artifact labels route
text products, without proving business-data validity. At the graph boundary,
native-plugin nodes have one input and one output and exclude graph feedback.

Details: [declarations](reference/declarations.md), [workflow example](../examples/workflow_patterns/README.md).
Code: [Petri contracts][petri], [Harness][harness], [Agent graph][graph].

## 3. Settlement connects computation to durable state

![Admit and Start, dispatch, register products, settle, apply a terminal rule, then read exact terminal evidence.](assets/technical-report/03-settlement-en.svg)

**Figure 3.** The successful result path. Waiting, interruption, blocked execution
and terminal handoff have separate dispositions; not every settled firing ends a run.

A returned future is insufficient. The owner checks the exact firing, Start and
execution lease; registered products then support settlement of the outcome,
successor marking/checkpoint, applicable workspace publication and subordinate
execution mappings. The batch contract permits at most one firing
settlement/publication per transaction. Durable completed products win a racing
stop, avoiding replay of the completed semantic action.

`TaskControl.result` follows the current execution generation, terminal evidence
and selected registered resource. Status and result reads use the same guarded
`RunReadCut` and recheck it before returning. A filename, process exit or last
assistant message cannot select a result.

Workspace changes retain exact before/after resources and revision lineage.
Concurrent changes preserve conflicts. `resume` continues the latest owner-stopped
cut; explicit `reopen` creates a new execution generation while retaining later
history. Workspace recovery does not undo external messages or transactions.
Unknown provider submissions and managed outcomes must not be treated as safe
retries. The ERP adapter's unknown-outcome receipts are script-level replay
controls, not an exactly-once ERP transaction guarantee.

Details: [checkpoint recovery](guides/checkpoint-recovery.md), [ERP lifecycle](../examples/erp_bench/README.md#action-boundary-and-lifecycle).
Code: [owner][owner], [publication gate][commit], [run reader][run-reader], [TaskControl][task-control].

## 4. Revise definitions before explicitly adopting them

![Definition revision B can exist while revision A continues running. Adoption passes through pause, drain and explicit occurrence mapping.](assets/technical-report/04-revision-adoption-en.svg)

**Figure 4.** Definition history and running state advance through separate operations.
The adoption sketch shows owner-driven replacement.

Extract, Compose, Instantiate and Branch can produce registered
`rpnh/module_declaration/v1` resources. Author APIs add stable element identities,
expected-head checks, source mappings, merge analysis and selected-change history.
Assemblies pin exact member revisions and lowering mappings. Equal labels alone
do not establish shared identity.

Runtime change has two explicit paths:

1. **Owner replacement:** prepare against the current net, pause admission, drain
   active firings, check occurrence mappings/retirements and adopt the successor
   with existing budgets and workspace lineage.
2. **Declared operation revision:** a registered Module product and
   `DeclaredModuleRevision` supply a checked structural delta and finite
   activations. Revision witness, successor checkpoint and adoption close with
   settlement; a whole-net switch requires the firing to be the sole unresolved
   provisional firing.

Neither path clones in-flight model calls or performs an unrestricted semantic
merge of live execution. The finite policy in [§8](#8-finite-pn-analysis-has-an-explicit-policy-boundary)
applies to its documented owner-adoption boundary.

Details: [net operations](guides/net-operations.md), [graph authoring](reference/graph-authoring.md),
[identity transforms](reference/author-identity-transform-contract.md), [assemblies](reference/assembly-full-history-merge.md).
Code: [author APIs][authors], [operation revision][revision].

## 5. Compose tools at the declared execution boundary

![An Agent action supplies selected managed calls to a HOST scheduler, which shares capacity across tool A and tool B and preserves exact receipts.](assets/technical-report/05-managed-tools-en.svg)

**Figure 5.** Managed-call concurrency inside Agent execution. These calls are not
automatically separate business-net transitions.

A native plugin can be a formal workflow node or an explicitly bound managed tool
inside an Agent. The [atomic-tool pipeline](../examples/tool_pipeline/README.md)
uses the first kind of boundary: each of its ten transitions binds one trusted
HOST tool to a one-step executor. Its checks, normalization and AND-join are
workflow structure, with separate admitted firings and registered products.

Managed scheduling is opt-in. The pure policy allows bounded pure calls;
conflict-domain policy serializes conflicting reads/writes and allows unrelated
domains to overlap. Missing or unknown conflict declarations form an exclusive
barrier. Capacity is run-shared. Mixed builtin/managed turns remain ordered;
stop prevents new starts while admitted siblings drain. An unknown outcome blocks
new calls to that operation pending authorized reconciliation. Genuine receipts and
whole-turn settlement preserve identities regardless of completion order.

The optional Linux isolated program API composes explicitly selected managed
calls through a HOST broker. Its SDK is synchronous (`tools.call`,
`tools.parallel`, `tools.read_result`, `result`); unsupported isolation has no
unrestricted fallback. It receives no host files, credentials, network or Registry
writer. Native plugin code itself is trusted host code.

Exact output readers page already registered results without re-execution.
Returned output, later submitted-request inclusion, observed business state and
model semantic use remain different claims. Missing policies preserve ordinary
serial/toolkit behavior.

Details: [controlled managed tools](controlled-managed-tools.md), [customization](guides/customization.md).
Code: [scheduler][scheduler], [program broker][broker], [isolated runtime][isolation],
[pipeline declaration][pipeline-module].

## 6. Observe independently at per-source cuts

![A read session queries two sources at different fixed cuts and rechecks current authority before delivering a read-only view.](assets/technical-report/06-source-cuts-en.svg)

**Figure 6.** Per-source consistency does not create a global atomic snapshot.
Unavailable or unauthorized sources retain explicit gaps, not zero counts.

An independent read host opens existing Registries without starting a task,
acquiring a writer fence or recording an Observation. The owner must already have
issued the selected observer profile and grant. Access to a directory, SourceSet
membership or a HOST label does not grant authority. Index, record, material and
export scopes are separate.

Typed queries return source-qualified references and permitted fields. Opaque
session-local cursors bind the full query, source cuts, authority, binding and
reader/schema catalog; ordinary append does not move an existing cut. Delivery
rechecks current access. Revocation, changed binding/catalog or expiry invalidates
affected data and cursors. A comparison clears its required pair if either side
becomes invalid. Material reads are separate bounded operations.

The local viewer projects selected Registry facts and checkpoint history without
becoming a writer. SourceSet's older explicit query/record API can persist selected
observations through its original publication contract; ordinary read-session
pagination writes none, and `capture_observation` is unsupported there. These
local owner-controlled interfaces do not establish remote trust or isolation
between hostile processes of the same OS user.

Details: [independent reader](guides/independent-registry-reader.md), [read-session contract](reference/registry-read-sessions.md),
[SourceSet observations](guides/source-queries.md), [comparison](guides/comparison-context.md).
Code: [read session][read-session], [read-host configuration][read-host].

## 7. Ask a bounded product-origin question

![One exact authorized root at one session cut is checked against its producer closure before returning producer proof and optional Start-input and claim rows.](assets/technical-report/07-product-origin-en.svg)

**Figure 7.** `product_origin_v1` returns finite metadata evidence, not recursive
lineage or evidence that a model read or used content.

At this snapshot, `query_product_origin_v1` is a public Python session method and
convenience function in `cpn.rpnh.collaboration`. This snapshot has no installed
origin-query CLI; the comparison viewer's HTTP endpoints do not expose it either. Supply one exact source-qualified canonical
`petri_output`/`workspace_write` resource produced by an Invocation, or an exact
`operation_result/v1`, plus an unchanged same-source `SourceCut` issued by the
session. Names, paths, `latest` and cross-source search are not accepted roots.

The query always verifies `producer_execution`; optional relations are
`start_inputs` and `claims`. Required record/index fields are preauthorized, and
the producing closure and all requested relation candidates/endpoints are
validated before the first successful page. A later invalid item cannot hide
behind a successful prefix. Current access is checked again after serialization.

- `root_role` separates formal `registered_output` membership from an
  `invocation_produced_resource` and an `operation_result` root. A shared producer
  link alone is insufficient.
- Start rows retain actual input versions, order and repeated resources. Claims
  distinguish consumed from non-consuming claims; their referenced resource may
  differ from a substituted Start input. Bodies are not read.
- `complete` covers only this authorized root, cut and selected relations.
  Recursive ancestry, observed reads, tool-call causality and content influence
  remain outside the profile. Changed-net settlement is unsupported.

Continuation repeats the unchanged full request and uses the returned cursor.
The page default is 20, bounded by session limits; the maximum is 100 or the
session's smaller limit. Validation work, retained state and response bytes are
bounded separately. The profile's content schema is inert validation data.

Details: [origin contract](reference/registry-read-sessions.md#fixed-product-origin-query).
Code: [query/paging][origin-query], [producer proof][origin-core], [relations][origin-includes].

## 8. Finite PN analysis has an explicit policy boundary

![Exact compiled net, marking and finite outcome models enter bounded analysis. Advisory records conclusions; strict requires every mandatory property to be HOLDS.](assets/technical-report/08-finite-pn-policy-en.svg)

**Figure 8.** An opt-in, input-bound analysis. Neither a report nor a policy grants
execution, settlement or terminal authority.

`cpn.rpnh.pn_validation` explores a fixed compiled declaration and exact marking
with finite modeled outcomes, using production reservation/deposit semantics
without executing tools or models. It returns `HOLDS`, `VIOLATED`, `UNKNOWN` or
`NOT_APPLICABLE` for distinct properties. Safety, proper completion, possible
success, allowed completion from every state and inevitable completion without
fairness are not interchangeable. Allowed failure is separate from success.

`start_run(..., pn_validation=...)` registers explicit models, contracts and
policy after real owner inputs exist and before first admission. Advisory retains
the Registry gates while recording conclusions. Strict requires all named
mandatory properties to be `HOLDS`; absent models, unsupported requirements or
cutoffs that leave a required property unresolved block it. The policy persists across reopen and the documented owner
adoption path; adoption checks the exact input, mapping and bounded report within
the commit boundary.

Support is finite: contentless control outputs, declared outcomes and the
supported consume/read/lease/guard forms. Unmodeled data, dynamic nets, external
replies/timeouts, fairness and local Agent progress obligations remain `UNKNOWN`.
HOST fidelity to the model is an assumption. Exact state identities are retained;
exploration cutoffs are not invented cycles. A finite success path or counterexample
can establish its specific conclusion before cutoff; global `HOLDS` needs complete
supported exploration. The scheduler model is
`any-exact-binding`, not certification of arbitrary scheduling callbacks.

Details: [finite PN validation](reference/pn-validation.md).
Code: [analysis contracts][pn-contracts], [owner policy][pn-policy], [adoption gate][pn-adoption].

## 9. Reuse and choose an example

Portable packages carry supported definitions, schemas, resources and provenance;
receiver-local credentials, interpreter paths, bindings and private run evidence
stay separate. Preview/resolve inspect and lock declarative material. Environment
planning and explicitly approved preparation precede a separately approved run.
V1/V2 packages support one closed-module entry; wider author/assembly APIs are
not automatically portable formats. Supplied native wheels are installed through
the bounded no-index/no-deps receiver path. Public metadata does not sanitize
package contents.

Closed-author imports create local identities with `copied_from` provenance and
expected-head protection, without starting execution. Worksets track contribution,
delivery and acceptance; first acceptance requires actual registered operation
outputs. See [Workset contracts](reference/worksets.md).

The installed entry is `rpnh`; the user-owned provider/exact-model catalog starts
empty. Start with [installation](guides/installation.md) and [models](guides/models.md).
Choose an example by the boundary you want to inspect:

- [Workflow patterns](../examples/workflow_patterns/README.md): serial/parallel
  dependencies with scripted local responses by default.
- [Native plugin](../examples/native_plugin/README.md): editable schemas and an
  explicitly installed deterministic program.
- [Hybrid summary](../examples/hybrid_summary/README.md): Agent → program → Agent;
  scripted by default, live execution selected separately.
- [Atomic-tool pipeline](../examples/tool_pipeline/README.md): offline usage/tariff
  branches, amount calculation, independent validation and final publication.
- [SQL query validation](../examples/query_validation/README.md): fixed-candidate,
  read-only SQLite execution, exact ordered comparison and a final SQL resource.
- [Multi-source data merge](../examples/tool_pipeline/multi_source/README.md):
  original JSON/CSV/Parquet inputs, a three-input join and two terminal artifacts.
  Both source examples link actual Viewer captures and navigation instructions;
  their dated native results and adapted-verifier limits are documented separately.
- [Package reuse](../examples/package_reuse/README.md): receiver binding of a
  closed process; preparation and execution remain separate.

At the snapshot, the installed export catalog contains `adapter_task`,
`native_plugin`, `hybrid_summary`, `compose_serial` and `package_reuse`.
`compose_serial` creates a definition only. Exporting never installs or runs it.
Scripted/native examples still require their documented process/IPC environment.
The source is the `0.1.0rc2` development candidate; older `v0.1.0rc1` binaries
have an earlier feature set. See [example catalog](guides/examples.md) and
[package guide](guides/portable-packages.md).

## 10. Historical results and their limits

These are separate historical windows, not validation of `6cec63f` or this rewrite.
The [curated results](results/README.md) preserve complete score/status projections,
inputs, source identities, commands and limitations. Publication commits are not
reruns. Runtime completion does not replace original business acceptance.

### Controlled multi-user collaboration

The [12 October 2026 study](results/user-collaboration-20261012/README.md) completed native proposals, review, explicit peer coordination, protected-branch merge/adoption, business execution and independent evaluation with three controlled simulated Registry identities and two real models. B switched models within its existing identity and cumulative budget. A total of 22 logical calls / provider-attempt specifications included failed attempts; HTTP-attempt counts remain unknown.

On fixed development/eval sets of 512 routes each, sequence score fell by 7.525% / 8.376%, while mean travel increased 7.715% / 8.421%; time-window FAIL and UNKNOWN remained. C2 explicitly assembled the strategy union, then the owner selected incoming C2 through the original merge. This demonstrates a controlled execution chain, not automatic conflict-free fusion, new routing algorithms or causal collaboration gains. [Original runtime panoramas and static declaration Viewer](../examples/user_collaboration/README.md) keep execution evidence separate from offline structure inspection.

### ERP-Bench

A04 and H01 tested clean tracked RPNH `6f8ee2e406f3c70edb73206f861e56a0202b9f15`,
using `gpt-5.6-terra` through `local_process`, with ERP-Bench task/scorer
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`. First publication was `74fad32d…`.
The adapted environment used Harbor 0.24.0, Odoo 19.0.20260926, Python 3.12.3 and
PostgreSQL 18.6; solver access was local Odoo with no external network.

| Individual run | Original business result | Applicable checks | Real calls |
|---|---|---|---|
| A04, `2000_easy_01_buy_only_baseline` | 100/100 PASS | 37/37; 1 NA | 9 |
| H01, `2299_hard_repair_plan_hard` | 21/100 FAIL | 86/95 | 13 |

H01's nine failed checks include four constraints and five purchase-origin checks.
The original scoring gate turns 63/75 constraint points into 21/100 overall;
86/95 is a check count. Checker exceptions prevent a single-cause conclusion.
Earlier A01 was blocked, A02 lacked an evaluable quiescent world, and A03 scored
0/100 with 11 real calls. Unknown counts are not zero. These different tasks are
not a matched comparison or task-set success estimate. The request-dependent
input-reader diagnosis and an offline coverage claim without an exact standalone
source lock remain withdrawn. [Full ERP record](results/erp-first-wave-20261008/README.md).

### SlopCodeBench

The adapted development-prefix `code_search` run tested installed RPNH matching
`74fad32d369876841686d10d33361c016e3d3648`, runner/evaluator
`31ceea3add480edb33431e70475c4c70597e6b31`, problem source
`9cd9ca3a51c3d3e2a99d2488a25baf73a2204451` and `codex/gpt-5.6-terra`.
Publication was `dbad0045…`. Public tasks were inspected; this was not held-out.

| Checkpoint | Original evaluator | Runtime | Real calls |
|---|---|---|---|
| 1 | 13/13; exit 0 | complete | 5 |
| 2 | 25/25; exit 0 | complete | 5 |
| 3 | 40/47; exit 1 | complete | 14 |
| 4 and 5 | Not run | Not run | Not run |

Checkpoint 3 had seven failures, including two Core and five Functionality cases,
with all 25 regression cases passing and `infrastructure_failure=false`.
Totals include regression and cannot be summed as unique tasks. The run used
24 calls, no post-limit excess, and 998.82 seconds; each checkpoint allowed
48 calls and a 7,200-second owner wait. Upstream cost/net-cost/step caps were
disabled. Normalized task tokens and USD cost were unavailable.

The solver had no network and used fresh containers with source snapshots carried
forward by the outer controller (`native_workspace_reuse=false`). Build/evaluation
used host networking and a same-version download adaptation. Original evaluation
ran; official `AgentRunner`, full five-checkpoint execution and quality judging
did not. There was no grader-feedback repair, retry or resume. `any-case` allowed
outer exit 0 despite checkpoint 3 failing. Earlier synthetic failures and blocked
native attempts remain in the [full SCB record](results/scb-prefix3-20261008/README.md).

### Deterministic pipeline and focused runtime windows

- **Atomic-tool pipeline:** `00f2d29c…` plus 16 example files, unchanged core,
  later published at `80a17c3c…`. Real AF_UNIX owner; 32 unique pytest IDs in
  33 executions, including unit checks. The standard fixture produced complete,
  **2.000 kWh / 1.70 CNY**, ten firings, 12 tool products and two source resources;
  the rounding fixture produced **0.02 CNY**. The independent integer-input
  validator is an example scorer, not a benchmark grader. Fresh-process readback
  preserved event ordinal/count 1001 and ten dispatch/Start counts, with zero
  model calls. Earlier AF_UNIX-blocked attempts remain separate.
  [Exact sources, inputs and results](results/tool-pipeline-20261008/README.md).
- **ERP runtime:** `dbad004…` plus the overlay published at `e92b05c…`.
  65 unique offline cases plus four subtests; installed-owner complete/stop/timeout
  paths and six direct-owner receipt scenarios. Nine synthetic backend invocations
  and 18 subsequent unknown-outcome probes retained the intended no-replay boundary.
  No real provider, Odoo world or original grader; reconstruction reused the same
  owner, so this does not certify OS-owner crash recovery.
  [Full window](results/erp-runtime-20261008/README.md).
- **Shared entry and exact reader:** H1 tested `674252f…` plus seven files and
  passed 39 unique native cases. H1+H2a tested `715468d…` plus five reader files,
  later published at `d92ff37…`: 166 unique cases/executions across eight windows,
  including 12 independent package checks. Real AF_UNIX/SIGINT/resume used scripted
  Agents, with no real provider. All 11 stable pipeline/readback fields matched;
  live-only transport/stop fields were absent. Earlier blocked and baseline-failure
  windows remain historical. These are focused checks, not whole-product acceptance.
  [Full source and status record](results/entry-reader-20261009/README.md).

The dated [AutomationBench record](../examples/automationbench/PUBLIC_RESULTS_20261006.md)
retains freeze04 first18 at **5 PASS / 9 FAIL / 4 BLOCKED**, and separate repair4
at **1 PASS / 3 FAIL**; the older 14 scored tasks were not rerun.
The [product-combination record](results/product-validation-20261009/README.md)
retains its separate source identity, split windows and original failures,
including the deferred parity case and incomplete native/stock-client scope.
It is not certification of later PN or origin-query commits. This update reran
none of those windows. [Release-validation history](guides/release-validation.md)
provides the remaining version-specific checks.

## Native input and collaboration

The original input boundary admits an exact input against the selected run, task, net, wait and firing. A trusted local front door binds the submitting principal/session; a browser field does not select that authority. The accepted [mock refund approval](../examples/approval_workflow/README.md) records requester and approver inputs and two settlements; [external-result quote intake](../examples/a2a_quote/README.md) records one controlled verifier input and one settlement. Both use synthetic business inputs. The quote is a deterministic external-result example; its historical A2A module name is retained for CLI/schema compatibility. It does not implement A2A SDK or OID4VP interoperability.

The [native billing collaboration](../examples/user_collaboration/native_billing/README.md) exercises definition ownership separately from input approval. A usage contributor tightens `check_usage_input.run`; the owner reviews and advances the protected Branch. A tariff contributor starts from those exact accepted revision bytes, tightens `check_tariff_input.run`, and receives the second review/advance. The owner then adopts the revision containing both contributions before the ten-step business executes. This is a single-parent ancestor chain. A same-base resolver regression retains the actual `coupled_contract` limitation: selecting one side can discard the other change. It is not proof of automatic two-parent merging. Three controlled local Registry identities are not external SSO or evidence of real-human authorship.

This controlled local example demonstrates traceability of recorded contributions and native execution. Its scope does not include performance comparison, external identity authentication or automatic multi-parent merging; dated source identities and image hashes are recorded in the [capture provenance](../examples/user_collaboration/native_billing/figures/provenance.json).

![Actual native multi-user billing PN panorama, with ten settled business firings.](../examples/user_collaboration/native_billing/figures/panorama.png)

The original capture shows all 28 nodes and 29 arcs of the adopted net. Original Registry readback confirms two source proposals, ten business settlements, twelve resources and one complete terminal, with three `[0,0]` model counters and the exact 2.000 kWh / CNY 1.70 report. [The example](../examples/user_collaboration/native_billing/README.md) includes each contributor and separate owner adoption; [provenance](../examples/user_collaboration/native_billing/figures/provenance.json) keeps execution and capture source windows distinct.

![Actual Viewer focus on the inherited usage contribution and its recorded proposer.](../examples/user_collaboration/native_billing/figures/contributor-2.png)

The Viewer connects the same PN operation node to its changed `value/config/max_integer` field, recorded proposer, and exact proposal/base/candidate revision references. The usage change remains visible as an inherited contribution in the final adopted net; the example links the full reference captures and the separate owner adoption action. This makes the path from a business node through its reviewed revision to adoption inspectable within one execution model.

## References

1. Tadao Murata. “Petri nets: Properties, analysis and applications.”
   *Proceedings of the IEEE* 77(4), 541–580, 1989.
   [DOI](https://doi.org/10.1109/5.24143) ·
   [Paper](https://docenti.ing.unipi.it/~a009435/issw/extra/murata.pdf).
2. Shunyu Yao, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Karthik Narasimhan
   and Yuan Cao. “ReAct: Synergizing Reasoning and Acting in Language Models.”
   ICLR 2023. [Paper](https://arxiv.org/abs/2210.03629v3).

These references supply research context. RPNH implementation and result claims
are supported by the versioned source and dated evidence linked in each section.

## Source map

The implementation links below are pinned to the report snapshot. Historical
result pages identify their different tested and publication revisions.
For API detail, follow the topic links alongside each figure.

[snapshot]: https://github.com/Deng-0119/RPNH/tree/6cec63ff5b7a0039611713961d00941767edaaf8
[module]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/module.py
[registration]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registration.py
[compiler]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/compiler.py
[orchestrator]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/orchestrator/runner.py
[petri]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/petri_contracts.py
[harness]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/harness.py
[graph]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/agent_workflows.py
[owner]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/run.py
[commit]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registry/_event_store/commit.py
[run-reader]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registry/run_authority.py
[task-control]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/task_control.py
[authors]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/__init__.py
[revision]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registry/module_revision.py
[scheduler]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/plugins/managed_scheduler.py
[broker]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/components/agent_loop/program_execution.py
[isolation]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/plugins/controlled_script.py
[pipeline-module]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/examples/tool_pipeline/module.json
[read-session]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/registry_read_session.py
[read-host]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/read_host_config.py
[origin-query]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/_product_origin_query.py
[origin-core]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/_product_origin_core.py
[origin-includes]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/collaboration/_product_origin_includes.py
[pn-contracts]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/pn_validation/contracts.py
[pn-policy]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/pn_validation/runtime_gate.py
[pn-adoption]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registry/pn_validation.py
[parent-child]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/cpn/rpnh/registry/parent_child.py

[parallel-scenario]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/examples/workflow_patterns/scenarios/parallel.json
[parallel-runner]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/examples/workflow_patterns/run.py
[parallel-validation]: https://github.com/Deng-0119/RPNH/blob/6cec63ff5b7a0039611713961d00941767edaaf8/examples/workflow_patterns/validation.json
