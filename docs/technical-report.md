---
name: rpnh-technical-report
description: "A living engineering report on RPNH's executable process assets, Registry/Petri-net runtime, implementation and evaluation."
metadata:
  document-kind: technical-report
  audience: operator-developer-and-researcher
  language: en
  counterpart: technical-report_ZH.md
  revision: "2026-10-08.1"
  status: living-report
  basis: "Deng-0119/RPNH at 74fad32d369876841686d10d33361c016e3d3648"
---

English | [中文](technical-report_ZH.md) | [Documentation](index.md)

# RPNH Technical Report
## Executable, composable and versioned processes for Agent–program systems

**Updated:** 2026-10-08. **Implementation snapshot:**
[`74fad32`](https://github.com/Deng-0119/RPNH/tree/74fad32d369876841686d10d33361c016e3d3648).
Experiment source revisions are recorded separately in [§9](#9-evidence-and-current-results).
This report explains the public implementation, reports retained observations,
and defines how subsequent optimization experiments update the same record.

## Abstract

RPNH is a provider-neutral harness for combining language-model Agents, native
programs and reusable workflows. Its central object is the **process itself**:
a typed declaration that can be constructed, compiled, composed, revised and
associated with exact execution evidence. A persistent Registry records identity,
versions and committed facts; a typed Petri net determines eligibility, claims,
routing and completion. Their integration makes the graph operational rather
than a drawing reconstructed from a conversation.

The implementation connects three useful capabilities: heterogeneous work under
one execution contract; explicit result/workspace lineage and controlled
continuation; and reusable process definitions with author revisions and local
receiver bindings. This gives application builders a basis for treating a
successful method as an asset they can inspect, change and use again. Models can
propose a process or a revision, while trusted host bindings and the existing
owner decide what may execute.

The evidence currently supports specific engineering mechanisms and bounded
example results. It does not yet establish comparative gains in task quality,
cost or speed. The report therefore separates source-backed capability, recorded
execution, business acceptance and comparative evidence. A completed Registry
run and a correct business result are deliberately different measurements.

## 1. Motivation and design objectives

A useful Agent system must do more than produce the next model response. It must
coordinate tools and people, preserve outputs, survive interruptions, and adapt
when the task or environment changes. In a recurring task, the resulting method
can be as valuable as the final answer: which steps depend on one another, where
a program is preferable to a model, what counts as an accepted output, and what
can be reused in the next run.

RPNH makes those relationships explicit. Consider a workflow that gathers two
independent analyses, computes a deterministic summary and synthesizes a report.
The system needs both analysis products before the join; the calculation needs
an exact input; the final result needs a declared completion condition. A later
revision should be able to identify the changed responsibility and its inputs
without treating a similarly named file or node as the same object.

| Design objective | Implemented mechanism | User-relevant question to evaluate |
|---|---|---|
| Reuse the method as well as its output | Data-only `ModuleDeclaration`, composition, author revisions and selected package formats | How much authoring and validation work is saved on the next change or reuse? |
| Coordinate models and existing programs | Registered component/executor contracts, typed ports, Petri-net admission and settlement | Does mixed work reach the correct business result with understandable responsibility boundaries? |
| Preserve precise execution meaning | Exact references, one owner path, registered products and terminal evidence | Can an operator distinguish an attempted action, settled output and completed task? |
| Continue and revise deliberately | Checkpoints, execution generations, workspace revisions and explicit adoption mappings | How much useful work is retained, and which failures still require intervention? |
| Share structure with local bindings | Inert packages, exact local locks, environment plans and separate run approval | Can a receiver reuse the process without copying the sender's machine or private settings? |
| Inspect without taking execution ownership | Read sessions, checkpoint projections and bounded comparisons | Can reviewers find meaningful changes and missing evidence more reliably? |

These are advantages of the **integrated design** and hypotheses about practical
value. Parallel tools, multiple Agents, streaming, progressive disclosure and
provider selection are broadly useful harness facilities, not distinctive
claims by themselves. Small, disposable tasks may not repay the declaration,
state-management and operating overhead. The evaluation plan includes such
controls rather than selecting only tasks that favor the architecture.

## 2. Architecture: four kinds of process asset

A process definition, its executable binding and a record of executing it are
related but different objects. Keeping them separate is central to reuse.

| Asset layer | Concrete objects | What it enables |
|---|---|---|
| Definition and author history | `ModuleDeclaration`, Agent graph source, `NetRevision`, branch heads, assembly recipes and element mappings | Construct, compare, compose and revise a method with explicit provenance. |
| Trusted receiver binding | `Registration`, exact plugin/model selection, package/environment locks and local host bindings | Resolve declarative keys to implementations chosen by the receiving host. |
| Runtime evidence | Adopted net, token occurrences, execution leases, registered resources, workspace revisions, checkpoints and terminal evidence | Execute that bound method and retain the exact facts of the run. |
| Observation | Read sessions, source-qualified references, net/checkpoint projections and comparison results | Explain selected facts without acquiring writer authority. |

`Registration` is the trusted inventory of component lowerers, executors, tools,
analyzers and schemas. A declaration selects registered keys; it cannot invent
an executable Python locator. The compiler preserves the fragments and contracts
actually consumed during lowering. Reading a compiled representation does not
register its callables. See [`module.py`][module-source],
[`registration.py`][registration-source] and [`compiler.py`][compiler-source].

The operating stack then separates presentation, main-session/task control,
`RunOwner` and its event loop, `Harness`, operation implementations, and read-only
observers. Basic, Codex and OpenCode are sequential presentations of one direct
main-session root; the shared lease prevents competing writable presentations.
DSH is a registered host integration with its own session surface. Provider,
Registry, workspace and recovery authority remain in the shared runtime.

Three runtime hierarchies have distinct roles:

- **Independent child tasks** own separate Registries, nets and owners. The
  parent retains exact links; changing main-session focus does not stop a child.
- **Delegated leaves** are bounded work associated with an exact parent action.
- **Subordinate execution nets** use the same Registry to model mechanics such
  as file materialization and workspace finalization beneath a business firing.
  Their evidence must be mapped into business settlement.

Authored assembly membership is a fourth, definition-level relationship. It is
not automatically a distributed runtime task hierarchy. This separation lets
integration code reuse the existing owner instead of growing a second execution
engine. [Architecture](architecture/design.md) and
[runtime reference](reference/runtime-registry.md) give the detailed contracts.

## 3. Registry and Petri net as a joint execution contract

### 3.1 Representation and eligibility

For explanation, write committed state as `S = (G, M, R)`: the adopted graph,
its marking, and Registry history. This is notation, not a new SDK object.
Registry references identify exact versions and context; the marking supplies
token occurrences and claims over those records.

The general net supports typed places, capacity, weighted arcs, consume/read/
borrow/guard/produce/return modes, outcome-dependent products, reusable
resources and explicit resource claims. Composition qualifies symbols and fuses
compatible places. Fusion shares a place; it does not duplicate a product for
broadcast. A fan-out must provide the needed occurrences, and a join must
require its actual inputs. The higher-level Agent graph has a dependency DAG
plus separately declared, bounded feedback.

`Harness.schedule_ready` rehydrates the current net and marking, installs active
claims, finds enabled transitions and respects available in-flight capacity.
An injected scheduler can return only a distinct subset of that enabled set.
For each selection, the owner admits the firing and records Start before the
physical operation is submitted. A scheduling preference cannot make an invalid
firing valid. See [`harness.py`][harness-source] and
[`petri_contracts.py`][petri-source].

### 3.2 The completion path

| Boundary | Evidence created or checked |
|---|---|
| Compile | Typed components, ports, operations, outcomes, links, budgets and terminal declarations match trusted registrations. |
| Adopt and admit | The owner selects an exact graph and binds a firing to current inputs, claims and execution context. |
| Start and dispatch | `OperationDispatch` carries the exact `OperationExecutionAuthority` before external computation starts. |
| Register products | `OperationProducts` carries `RegisteredOperationOutputsAuthority` for that execution. |
| Settle | The owner closes result, successor marking/checkpoint, applicable workspace publication and subordinate execution mappings. |
| Establish terminal | A declared terminal rule selects the registered final result and terminal evidence. |

The Registry is therefore more than an after-the-fact log, and the Petri net is
more than a readiness picture. Registered bindings and versions constrain what
can fire; accepted products and successor state must agree before they can
justify later work. The commit coordinator validates typed identities, exact
references, ordering and publication closure. Its current batch contract permits
at most one firing settlement/publication in a transaction. Helpers participate
in this boundary rather than obtaining independent publication authority.
See [`publish_batch`][commit-source].

A worker future finishing does not itself advance tokens. Completion handling
re-enters the owner event loop and checks firing, Start and lease identity.
Physical operations may run concurrently; authoritative state advances through
one owner path per run. Durable registered products win a racing stop, avoiding
replay of an already completed semantic action. On a completion error, admission
stops while already-started sibling completions drain through the owner.

`OperationDisposition` represents resource wait, execution block or terminal
handoff separately from products. `HarnessResult` exposes terminal evidence and
completion error separately. Even `succeed` means settlement of a declared
outcome, not proof that the application objective was satisfied.

### 3.3 What this contract establishes

The contract makes identities, dependencies, resource use and result publication
checkable. Business correctness still needs domain schemas, checkers, constraints
or human acceptance. The current state/claim checks do not prove global Petri-net
liveness, absence of all deadlocks or truth of model-generated content. This is
why [§9](#9-evidence-and-current-results) reports original task scores separately
from runtime closure.

## 4. Outputs, workspace history and recovery

### 4.1 Outputs become resources, not just transcript text

An output belongs to an admitted operation and a declared outcome. It becomes a
registered resource before a successor relies on it. `TaskControl.result` reads
current terminal authority, the exact `run_terminal_evidence` and its selected
resource, then returns outcome, generation, output and model-call accounting.
A process exit, plausible filename or last assistant message cannot substitute
for this chain. See [`task_control.py`][task-control-source].

The ordinary Agent graph uses symbolic artifact labels over **text-product**
ports. Label agreement provides routing structure; it does not prove that a
text string is a valid purchase plan or numerical model. Authors who need richer
business-data contracts use registered schemas and the general module interface.
Native plugin nodes deserialize JSON text and validate plugin schemas.

### 4.2 Workspace publication is part of settlement

A firing works in a private view derived from a registered workspace revision.
Finalization freezes a candidate archive; ordinary settlement publishes the
successor workspace and links it to the business marking. Per-path create,
update and delete deltas retain exact before/after resource references.
Concurrent revisions are reconciled against the current head with conflicts
preserved, rather than last-writer directory overwrite.

Subordinate execution nets make file-materialization and finalization mechanics
inspectable without adding implementation steps to every Designer-authored
business graph. Their terminal mappings bind mechanical evidence to the business
result and successor checkpoint. Workspace versioning concerns registered local
artifacts; it does not roll back messages, purchases or other external effects.

### 4.3 Continuation preserves the distinction between known and unknown

An owner stop checkpoints unfinished work; `resume` continues the latest
owner-stopped cut. A user-selected `reopen` appends a new execution generation
from a committed checkpoint in the same Registry, preserving later history and
files as evidence. Reopening is deliberate new execution, not deletion of the
intervening past. Main-session rollback does not erase independent children.

Recovery can settle an exact durable completion without repeating its operation
in documented, supported windows. A provider submission whose remote outcome is
unknown is retained as `submission_unknown`; timeout is not evidence of no
remote effect. Explicit owner-selected reopen can close an unresolved attempt
under the supported protocol and continue with new identities. This precision
is important when an operation may incur cost or change an external system.
See [checkpoint recovery](guides/checkpoint-recovery.md).

## 5. Process construction, composition and evolution

### 5.1 Two authoring levels

`AgentWorkflowGraph` is the convenient model/program workflow language:
responsibilities, named input/output artifacts, explicit arcs, execution
selectors and one ingress/egress. The main Designer can propose this structure;
validation and lowering turn it into executable places, transitions, fan-out
occurrences and bounded rework permits. Array order is not dependency authority.
The general `ModuleDeclaration` is the richer interface for typed component
contracts, resource behavior, terminal rules and trusted application extensions.

At the current graph boundary, native-plugin nodes have one input and one
output, and their presence excludes graph feedback. That concrete restriction
matters when choosing a mixed optimize–validate design. It does not describe all
general modules. See [`agent_workflows.py`][graph-source] and
[declarations](reference/declarations.md).

### 5.2 A process can be a process output

The net-definition component accepts and produces `rpnh/module_declaration/v1`
resources. Extract selects supported whole modules/components; Compose connects
explicit compatible public boundaries; Instantiate creates independently named
symbols; Branch extracts and optionally instantiates. These operations can be
registered firings in a larger workflow. This makes design → evaluate → select
→ revise a constructible application pattern rather than a policy hard-coded
into the harness. Producing a definition and adopting it remain distinct steps.
See [net operations](guides/net-operations.md).

### 5.3 Author revisions preserve reusable structure

The opt-in collaboration APIs add immutable author revisions, stable element
identity, explicit boundary mappings and parent/selected-change provenance.
Branch advances use exact expected heads. Plain-module and graph-source merge
analysis is separated from publishing the resolved revision. Selected-change
transplants, explicit split/fusion history, open-region obligations and bounded
assembly protocols represent more detailed reuse and evolution.

Concrete public classes include `ClosedModuleAuthor`, `GraphModuleAuthor`,
`PlainModuleMergeAnalyzer`, `PlainModuleMergeAuthor`, `OpenRegionAuthor` and
`AssemblyAuthorV9`. Their supported contracts differ. Assemblies pin member
revisions and lowering mappings, so a reviewer can ask which exact source
contributed an element instead of relying on matching labels.
See the [public exports][collaboration-source],
[graph authoring](reference/graph-authoring.md),
[identity transforms](reference/author-identity-transform-contract.md) and
[assembly history/merge](reference/assembly-full-history-merge.md).

A coding Agent can use these interfaces as an author just as a person can.
The important research question is whether the next edit, repair or composition
becomes easier and less error-prone. A visual editor is not required for this
value, and the read-only dashboard is not such an editor.

### 5.4 Moving a revision into execution

RPNH has two explicit bridges:

1. **Owner-driven replacement.** Prepare a complete candidate against the exact
   current net; pause new admission; drain active firings; apply explicit
   occurrence mappings/retirements; adopt the successor with existing budgets.
   Same-owner replacement preserves workspace lineage and execution environment.
2. **Declared operation-driven revision.** A registered effect supplies an exact
   Module product and `DeclaredModuleRevision`. The core verifies the bound
   resource, compiles the candidate, derives and checks the structural delta,
   maps surviving occurrences and permits only explicit finite activations.
   Revision witness, successor checkpoint and adoption close with settlement.
   A whole-net switch requires the current firing to be the sole unresolved
   provisional firing.

These mechanisms preserve meaningful work across a controlled change. They do
not clone in-flight model calls or perform unrestricted semantic merges of live
execution. See [`module_revision.py`][revision-source] and the native-operation
[tests][net-tests].

## 6. Reuse across environments and collaboration boundaries

### 6.1 Share the method; bind execution at the receiver

The portable package path separates declarative material from private local
configuration. A package contains supported declarations, schemas, resources,
requirements and provenance. Receiver-local interpreter paths, plugin
configuration, credential references, environment locks, receipts and private
run evidence remain separate.

The installed path is concrete:

1. `rpnh package preview` validates bounded local ZIP material without extraction
   or execution.
2. `package resolve` selects exact locally supplied dependencies and produces a
   lock for a supported closed-module entry.
3. `check-environment`, `resolve-environment` and `plan-environment` inspect the
   selected host and explicit local wheel material, producing a concrete plan.
4. `setup-instructions` renders the same plan for an operator;
   `prepare-environment` executes supported actions after exact-plan approval.
5. `package run` separately approves the run, rechecks binding/receipt/current
   host, and uses normal owner/harness execution. Successful preparation alone
   is not a business result.

The native-plugin receiver uses supplied hashed wheels with isolated,
no-index/no-deps installation. This is a bounded receiver workflow, not a general
package resolver. Package v1/v2 supports one closed-module entry; broader author
and assembly capabilities do not automatically become portable package formats.
Public metadata is not automatic content sanitization. Authors still review
materials before sharing. See [portable packages](guides/portable-packages.md),
[environment preparation](guides/package-environments.md) and the
[runnable package tutorial](guides/package-reuse-example.md).

### 6.2 Sharing definitions, reading evidence and accepting work

Ordinary closed-author subnet import creates local identities with `copied_from`
provenance under target-host checks and expected-head protection. It does not
start execution. Scoped Registry read sessions use selected sources and existing
read authority. Worksets record contribution/delivery/acceptance identity;
`WorksetOwner.accept_delivery` requires actual registered operation outputs on
first acceptance, while an identical repeated delivery can recover its prior
acceptance.

These are useful building blocks for process reuse and exact contribution
accounting. Their current local/explicit binding scope should be retained by an
application rather than advertised as an already complete remote collaboration
service. [Normal-child/Workset contracts](reference/normal-child-root-contract.md)
and [read sessions](reference/registry-read-sessions.md) describe the interfaces.

### 6.3 Observation supports review

`rpnh net --run RUN` and the local dashboard project actual Registry state,
including resources, checkpoints and selected activity. An independent read host
can compare selected sources across definition, configuration, material and
runtime axes. Source-qualified references and explicit element mappings prevent
unrelated objects with equal names from being equated. Missing mapping or
withheld material remains partial/unknown.

The viewer stays read-only, and cross-source comparison is not a globally atomic
snapshot or an automatic performance comparison. Its value is a traceable basis
for human diagnosis and review. See [comparison context](guides/comparison-context.md).

## 7. Embedding, extension and operating interfaces

The supported installed entry is `rpnh`. A user-owned provider/exact-model
catalog starts empty; frontends do not create an alternative provider owner.
Python authoring interfaces expose capabilities beyond the convenience CLI.

| Goal | Current interface | Implementation entry |
|---|---|---|
| Run conversation and independent work | `rpnh --frontend basic`; `/agent`, `/workflow`, `/tasks`, `/task ID result` | `main_session.py`, `task_control.py` |
| Define a typed process | `ModuleDeclaration` + `Registration` + compiler | `module.py`, `registration.py`, `compiler.py` |
| Define an Agent/program graph | `AgentWorkflowGraph`, node execution selectors | `agent_workflows.py`, `agent_tasks.py` |
| Supply existing business code | Explicitly installed `rpnh.plugins` entry point; `PluginDefinition`, `PluginOperation` | `cpn/plugins/api.py`, `catalog.py` |
| Revise or compose author material | Opt-in Python author/branch/merge/assembly APIs | `cpn/rpnh/collaboration/` |
| Export an editable example | `rpnh examples list` / `rpnh examples export` | `cpn/examples/` |
| Bind and run a received package | `rpnh package …` | `collaboration/package_cli.py`, `environment_cli.py` |
| Inspect and compare | `rpnh net`; explicit independent read host | `cpn/frontend/`, Registry read-session APIs |

A plugin declares input/output JSON schemas, operation identity, effects,
resources and limits. A deterministic plugin can run as a formal workflow node
without a model, or be explicitly bound as a managed tool for one Agent.
`PluginContext` supplies execution/invocation identity and cooperative
cancellation without handing the worker a Registry writer.

Bounded parallel calls, exact result paging, optional isolated tool programs,
resource queries and context-management facilities complement this structure.
Their policies remain explicit; adding them must not silently change benchmark
tool access or budgets. Trusted native-plugin processes and the optional Linux
isolated-program substrate have different security contracts. Registry checks
are not a replacement for reviewing trusted host code or establishing the
required operating-system isolation. See
[customization](guides/customization.md) and
[controlled managed tools](controlled-managed-tools.md).

## 8. Reproducing the execution model

The source gallery's deterministic parallel example is a small end-to-end
inspection task. Its topology is `prepare -> (facts || risks) -> join`; preset
local-process responses exercise the real execution path without testing model
reasoning. After selecting the reviewed source commit on Linux/WSL2 with Python
3.11 or newer:

```bash
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check

DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-report.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

Verify the registered results of both branches precede the join, the final
resource is selected by terminal evidence, and the viewer identifies the correct
run/checkpoint. The example reports `status: PASS` on success. The final command
opens a local read-only server. Dependency installation may access the network;
the default fixture makes no call to a real model. These are reproduction instructions,
not a claim that this report revision reran the example.

Next, use the [native plugin](../examples/native_plugin/README.md) to modify a
real program contract, the [hybrid summary](../examples/hybrid_summary/README.md)
to inspect Agent–program–Agent data flow, or the
[package reuse](../examples/package_reuse/README.md) example to bind the same
closed process in a receiver environment. The installed export catalog supports
`adapter_task`, `native_plugin`, `hybrid_summary`, `compose_serial` and
`package_reuse`. `compose_serial` demonstrates declaration composition; matching
trusted registrations are still needed for execution.

Real-provider checks are a separate, explicitly configured step with an exact
model condition and authorized budget. The current source version is an rc2
development candidate; older public rc1 binaries do not contain later source
features. [Installation](guides/installation.md) records that distinction.

## 9. Evidence and current results

### 9.1 Snapshot and evidence levels

The implementation snapshot is `74fad32`; the final ERP smoke/showcase runs
were tested at `6f8ee2e406f3c70edb73206f861e56a0202b9f15`, with clean tracked
source. The later integration commit adds evidence and example integration; it
does not turn those records into tests of a new source revision. Both runs use
`gpt-5.6-terra` through `local_process` and the ERP-Bench task/scorer at
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`. Unrecorded reasoning/profile fields
remain unknown. See [source identity][erp-source] and [model condition][erp-model].

| Record | Retained observation | Interpretation |
|---|---|---|
| ERP `2000_easy_01_buy_only_baseline`, A04 / `s04` | 100/100; business `passed=true`; all 37 applicable checks pass; 9 real provider calls | One successful smoke task in the recorded repaired condition. Public score/lifecycle projections and raw-file hashes are available; the A04 raw grader bytes were not included in this evidence collection. |
| ERP `2299_hard_repair_plan_hard`, A01 / `h01` | 21/100; business `passed=false`; 86/95 applicable checks pass; 13 real provider calls | Native/provider/evaluator paths completed, but the business task failed. Raw reward, rule results and checker log were inspected alongside projections. |
| Historical ERP smoke A03 | 0/100; 11 real calls; source `2ca5fbc` | Earlier input-capability binding failure, retained under its own condition. It is not the current source's rerun result. |
| ERP focused test window | 190 passed, with 35 subtests separately reported | The recorded command covers `examples/erp_bench/tests`, not 225 independent tests or a repository-wide pass. No tests were rerun for this report. |
| SlopCodeBench `code_search` | Adapter, synthetic checks and native/Docker fixture records integrated | No complete benchmark solve or original-evaluator acceptance result yet. Fixture success is not a task score. |
| Historical AutomationBench | Freeze04 first18: 5 PASS / 9 FAIL / 4 BLOCKED; separate repair4: 1 PASS / 3 FAIL | Separate dated conditions. The older 14 scored tasks were not rerun; see the [public record](../examples/automationbench/PUBLIC_RESULTS_20261006.md). |

Sources: [A04 score projection][erp-smoke], [H01 raw reward][erp-reward],
[H01 rule results][erp-rules], [A03 diagnosis][erp-a03],
[focused-test command][erp-test-command] and [log][erp-test-log].
The [release-validation history](guides/release-validation.md) retains other
version-specific offline results; counts from overlapping windows are not added.

The ERP environment is an explicitly adapted runtime: Harbor 0.24.0,
Odoo 19.0.20260926, Python 3.12.3 and PostgreSQL 18.6 are recorded. The solver
uses no external network and reaches only its local Odoo; this is not the
unchanged upstream network condition. The managed action boundary is a complete
Python script, not each individual Odoo transaction. See [world projection][erp-world].
These two single runs are not a task-set estimate or a controlled harness comparison.

### 9.2 What the hard-task failure teaches

H01 is useful because execution completion and business rejection are both
visible. Its score is **not** `86/95` expressed as a percentage. The original
scorer gates the other dimensions when constraints are incomplete: 63/75
constraint points yield `25 × 63/75 = 21`. Hygiene is 15/20 and the reported
optimality subscore is 91.66, but those do not rescue the gated total.
The [original scoring rule][erp-score-rule] remains unchanged.

The nine failed checks comprise four constraints and five component-purchase
provenance checks. They need more specific interpretation:

- **Capacity mismatch is concrete.** The Actor reported 5,130 minutes, matching
  a readback of 114 units at 45 minutes per unit in work-order fields. The task's
  fixed WC02 route rule uses 55 minutes per unit; `114 × 55 = 6,270` exceeds the
  5,555-minute cap. Readback existed, but its measurement did not match the task's
  route semantics. See [actual readback][erp-readback] and [scorer route rule][erp-route].
- **Three failures include checker exceptions.** Supply timing, MO scheduling
  and component-stock capacity log a comparison between `bool` and
  `datetime.date`. Missing/false deadline values interacting with the upstream
  date handling are a supported hypothesis, not an established sole root cause:
  the retained readback does not supply the missing final field values. Preserve
  the original FAILs and 21 score while distinguishing calculation error from a
  successfully calculated constraint violation. See [checker log][erp-checks].
- **Local plan arithmetic is narrower than world validation.** `validate_plan`
  reported no violations and 111,938.90 of supplied-plan spend. The original
  grader reports 181,383.92 for its global spend scope, against 133,394.07
  expected. The helper explicitly validates supplied observations; it does not
  read Odoo or prove full-world correctness. The complete difference is not yet
  attributed. The five purchase-provenance failures also remain unresolved.
  See [validator input/output][erp-plan] and [implementation][erp-planning].

H01 successfully read its registered task input before ERP actions, so the older
A03 missing-reader explanation does not apply. It also contains failed schema
probes for nonexistent workcenter fields before later scripts continue. These
are distinct from a core scheduling failure or a provider outage.

A03's diagnosis identified an example Actor capability-binding omission.
The later repair added the registered input reader and was checked in bounded
input-delivery/lifecycle fixtures; A04 then passed a fresh-world smoke run.
That is actionable repair evidence, but the different revisions and runs do not
establish a general causal improvement in harness performance.
See [input-delivery checks][erp-input-checks].

### 9.3 Evidence coverage is itself part of the result

The ERP [collection manifest][erp-manifest] distinguishes original files,
registered payload bytes, existing projections and serialized metadata. It also
retains 40 historical fresh-reader provenance failures involving missing
`agent_loop_ref`; verifying canonical object bytes does not repair that
provenance. Vendor HTTP wire and full Codex CLI events were not found for the
recorded `s03`/`h01` collection, while `s04` retains public projections and hashes
rather than its raw control transcript.

The [publication review][erp-publication] records digest/size checks across 595
payloads. That is a publication-integrity result, not proof that every execution
or provenance check passed. This report cross-checked selected records rather
than independently rehashing the entire collection. Retaining these distinctions
makes the failures useful for engineering without inventing missing evidence.

## 10. Evaluation and optimization method

### 10.1 Measure the complete user task

A useful evaluation separates at least six axes:

1. **Business result:** original grader, domain constraints, accepted artifacts,
   and independent checks.
2. **Execution integrity:** admitted actions, exact product/terminal lineage,
   stale/duplicate contribution handling, and truthful unknown states.
3. **Evolution and reuse:** changed definition/member identities, preserved
   work, validity of the next revision, and receiver success.
4. **Author/operator effort:** edits, setup burden, repair time, interventions
   and diagnosis accuracy.
5. **Performance and resources:** wall time, model attempts/usage where available,
   native-tool time, owner/Registry time, storage and workspace-copy overhead.
6. **Exposure and portability:** actual disclosure, trusted dependencies,
   platform requirements and explicit unsupported cases.

Use the original benchmark task and scorer for the original-task result. Report
any continuation or modified task separately. Freeze model/exact configuration,
instructions, tool permissions, feedback policy, task set, seed/reset,
checkpoint reveal order, environment and budget. Count all attempts, including
failed and unknown submissions. If usage or cost is not reliably available,
record it as unavailable rather than infer it from call count.

A comparison should use a competent baseline with ordinary tool calling,
parallelism, persistence and recovery where applicable. Proposed ablations
include fixed versus revised process definitions; process reuse versus fresh
authoring; observer-assisted versus ordinary diagnosis; and workspace/checkpoint
retention versus clean restarts. Each variant must preserve relevant privileges
and budgets, and state exactly which mechanism changed. A Registry consistency
check cannot serve as the business-quality metric.

### 10.2 Choose tasks that exercise the proposed value

The first-wave examples provide complementary questions. ERP plan repair tests
real business state, hard constraints and provenance; evolving code-search tasks
test repeated requirements, functional correctness and workspace continuity.
Further mixed simulation/optimization examples could test native-program reuse
and independent numerical validation. Each should include a reuse or change
stage if the claim concerns process assets. Merely executing a supplied graph
does not demonstrate that an Agent authored or improved the graph.

Add a low-structure one-off control task to expose overhead. For interruption or
unknown-effect tests, define the failure window in advance and measure physical
calls and actual external effects as well as Registry records. For authoring,
measure the second successful change and retained component identities, not
only the number of available APIs.

### 10.3 Current improvement priorities

The next work should make existing mechanisms easier to use and diagnose before
adding another scheduler or a broader collaboration service. The following are
**proposals and acceptance criteria**, not implemented feature announcements:

| Priority | Concrete intervention | Acceptance evidence |
|---|---|---|
| Effective task preflight | Expose a read-only per-operation view of input delivery, selected/bound/declared/provider-visible tools, profiles, effects and result readers. Reuse compiler/catalog logic without issuing new authority. | An omitted required reader, wrong selector or missing result reader is distinguished before dispatch; valid alternative input delivery is not rejected. Compare the projection with the actual registered request/catalog. |
| Layered diagnostic export | Link exact Registry, adapter, business-world and grader records at a fixed cut, retaining missing, redacted and provenance-rejected states separately. | A fresh reader identifies what completed, what remains unknown and which layer failed; no missing stage becomes success. Measure diagnosis correctness/time on the same failures. |
| Public exact-Module runner | Factor the existing high-level Agent runner's lifecycle/binding assembly so a validated composed Module can use it directly, with an author-only initialization helper. | Two real closed members compose, reopen and execute with the exact selected compiled closure; profile/tool identity and stop/recovery behavior remain intact. Reduce duplicated private wiring. |
| Usage with coverage | Add a read-only projection separating logical returned calls, physical attempts, provider token usage and versioned price-derived estimates. | Duplicate references do not double-count; partial/unknown usage remains unknown; estimates disclose price source, currency and coverage. Existing call budgets retain their meaning. |
| Typed ERP observations | Version actual readback entities, dates, units, route/workcenter rules and spend scope; compare plan, applied writes and observed state as separate artifacts. | Offline cases catch unit/scope/date mismatches; fixture readback reflects actual state; original grading stays independent and original failures remain unchanged. |

The exact-Module proposal addresses a specific SDK seam: `start_run` already
supports general modules, while `AgentTaskSpec` accepts a stage or graph and the
convenience runner rebuilds a Module while assembling managed services. The
proposal reuses that owner/runtime rather than supplying missing core semantics.
See [`agent_tasks.py`][agent-task-source] and [`start_run`][owner-source].

Similarly, the old ERP input-reader omission is already repaired; the proposed
preflight aims to prevent recurrence across applications. Typed ERP validation
belongs in the domain adapter, using legitimately available task facts, without
exposing hidden grader rules/reference solutions to the solving Agent. A
separate diagnostic fixture can investigate the date exception without modifying
the original result. The current SCB adapter preserves source workspace through
the upstream Session path; it reports `native_workspace_reuse=false`, so no
native RPNH workspace-reuse success is claimed. See the
[SCB example](../examples/slopcodebench/README.md).

Progress should first establish deterministic contracts and supported user-entry
behavior. A subsequent authorized model experiment can then test whether those
changes improve business acceptance or effort. Until measured, these are concrete
engineering questions, not predicted gains.

## 11. Keeping this report current

This is one evolving report, with experiment evidence linked from it. Each
optimization cycle should update the affected mechanism description, append a
condition-specific result row, and state the resulting engineering decision.
A new result supersedes an older claim only for a comparable condition; original
failures remain part of the record.

A compact experiment entry should contain:

| Field | Required content |
|---|---|
| Identity | Stable experiment ID, date, exact code SHA and dirty-tree status; exact upstream/task/scorer revisions. |
| Question | Target mechanism, expected benefit, competing explanation and acceptance criterion. |
| Condition | Model/profile identity without secrets, input hashes, tools, permissions, budgets, environment and reset/reveal policy. |
| Intervention | Exact code/configuration/process revision changed and unchanged controls. |
| Outcomes | Attempt count; runtime terminal; original business score and failed checks; artifact/lineage acceptance; latency and available usage. |
| Evidence | Links and hashes for reviewed public manifests, reports and allowed projections; coverage or missing data. |
| Decision | Accept, reject or inconclusive; tradeoffs, regressions and next experiment. |

This table is a reporting convention, not a newly implemented runtime schema.
Use existing example-specific evidence contracts where present. Publish only
reviewed, appropriately licensed, non-sensitive evidence; raw private runs,
credentials, local profiles and unreviewed transcripts stay outside the report.

### Report evolution

| Report revision | Change |
|---|---|
| 2026-10-01.1 | Source-reviewed introduction at `40f1be8`; runtime, workspace and net-operation explanation. |
| 2026-10-08.1 | Expands the same report around process assets; documents current authoring/receiver/read interfaces; adds first-wave evidence, optimization questions and per-experiment update rules at `74fad32`. |

This documentation revision inspected source and retained records. It did not
launch model experiments, run a runtime suite or change the implementation.

## 12. Related engineering work and source map

The report borrows an engineering presentation style from primary sources:
state the task, follow one execution path, identify ownership, then connect
mechanisms to evidence and remaining costs.

- DeepSeek's [Harness architecture](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/architecture.md)
  is a useful reference for plugin responsibilities and turn lifecycle. Its
  linked [Cordis paper](https://arxiv.org/abs/2608.25512), *A Programming Paradigm
  for Spatiotemporal Composability*, concerns composability and its assumptions;
  it is not a whole-DSH task-performance report.
- OpenAI's [Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
  (2026-01-23) explains a complete request/tool/context path.
- [Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/)
  (2026-02-04) explains shared-core/client protocol boundaries.
- [Harness engineering](https://openai.com/index/harness-engineering/)
  (2026-02-11) connects environment, observability, constraints and feedback.
- [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)
  (2026-08-19) distinguishes application integration from the reusable execution layer.

These sources inform structure and related-work context, not claims about RPNH's
performance. Durable tasks, typed interfaces, event histories, checkpointing and
plugin systems have prior art. Comparative claims require same-condition
implementation review and experiments rather than a feature checklist.

| Implementation question | Pinned source |
|---|---|
| What data describes a process? | [`module.py`][module-source], [`petri_contracts.py`][petri-source] |
| How are trusted implementations selected? | [`registration.py`][registration-source], [`compiler.py`][compiler-source] |
| How does an Agent graph become execution? | [`agent_workflows.py`][graph-source] |
| Who admits and settles operations? | [`harness.py`][harness-source], [`RunOwner`][owner-source] |
| How does publication close? | [`registry/_event_store/commit.py`][commit-source] |
| How can a returned definition change execution? | [`registry/module_revision.py`][revision-source] |
| How are process revisions and assemblies exposed? | [`collaboration/__init__.py`][collaboration-source] |
| How does a received package run? | [`collaboration/environment_host.py`][receiver-source] |
| How is a task's real result read? | [`task_control.py`][task-control-source] |
| Where are focused execution examples checked? | [`test_native_net_operations.py`][net-tests], [release validation](guides/release-validation.md) |

Source links identify the implementation snapshot, not a guarantee that every
private class is a stable SDK. The practical proposition remains testable: a
process with explicit executable meaning can become a reusable, inspectable and
versioned asset. RPNH's next experiments should establish when that structure
helps real work enough to justify its costs.

[module-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/module.py
[petri-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/petri_contracts.py
[registration-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registration.py
[compiler-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/compiler.py
[harness-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/harness.py
[owner-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/run.py
[commit-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registry/_event_store/commit.py
[graph-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/agent_workflows.py
[revision-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/registry/module_revision.py
[collaboration-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/collaboration/__init__.py
[receiver-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/collaboration/environment_host.py
[task-control-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/task_control.py
[net-tests]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/tests/test_native_net_operations.py

[agent-task-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/cpn/rpnh/agent_tasks.py
[erp-source]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/source-identity.json
[erp-model]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/model-configuration.json
[erp-smoke]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/s04/public/original-score.json
[erp-reward]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/reward.json
[erp-rules]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/rule_results.tsv
[erp-a03]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/records/input-delivery-a03-v2/public/diagnosis.json
[erp-test-command]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.json
[erp-test-log]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.log
[erp-world]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/r/h01/public/world-projection.json
[erp-score-rule]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/test.sh#L351-L362
[erp-readback]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/a33b2d441be95df9b29a5ee5336489fc.json
[erp-route]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/checks.py#L305-L313
[erp-checks]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/logs/r/h01/w/harbor/verifier/checks.log
[erp-plan]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/b72a1257ec485ed3a8d0c7900d59af2f.json
[erp-planning]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/examples/erp_bench/src/rpnh_erp_bench/planning.py#L40-L89
[erp-input-checks]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/records/work/final-independent-review/input-delivery-closure.json
[erp-manifest]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/erp/MANIFEST.json
[erp-publication]: https://github.com/Deng-0119/RPNH/blob/74fad32d369876841686d10d33361c016e3d3648/evidence/first_wave/20261008/ERP_PUBLICATION_REVIEW.json
