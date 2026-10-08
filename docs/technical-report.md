---
name: rpnh-technical-report
description: "RPNH architecture, process authoring, runtime, integration and published results."
metadata:
  document-kind: technical-report
  audience: application-developer-and-researcher
  language: en
  counterpart: technical-report_ZH.md
  revision: "2026-10-08.2"
  status: technical-report
  basis: "Deng-0119/RPNH at dbad00458e9b356fcaf0bb97ceb90258ed9b1de0"
---

English | [中文](technical-report_ZH.md) | [Documentation](index.md)

# RPNH Technical Report
## Executable processes for Agent and program systems

**Updated:** 2026-10-08. **Source snapshot:**
[`dbad004`](https://github.com/Deng-0119/RPNH/tree/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0).
Experiment source revisions and publication revisions are identified separately
in [§9](#9-example-results).

## Abstract

RPNH is a provider-neutral harness for combining language-model Agents, native
programs and reusable workflows. It treats a process as a typed, executable
asset: its dependencies, inputs, outputs and completion conditions can be
constructed, composed and revised, while its execution is tied to persistent
records. A Registry stores exact identities, resource versions and committed
facts. A typed Petri net uses those records to determine which work can run and
how its outputs enable subsequent work.

The same runtime supports conversational sessions, independent tasks and
multi-Agent workflows. It connects process definitions to trusted host
implementations, records workspace lineage and checkpoints, and exposes
read-only views of execution. Authoring and package interfaces let developers
reuse a process definition across revisions and receiver environments without
bundling the original machine's private configuration.

This report explains the current architecture, the lifecycle of an operation,
process authoring and reuse, and the supported entry points. It then presents
published ERP-Bench and SlopCodeBench runs, reporting original task acceptance
separately from runtime completion. These are bounded application results;
the available experiments do not establish comparative quality, speed or cost
advantages over other harnesses.

## 1. From an Agent conversation to a reusable process

An Agent application often combines work with different execution needs. A
model interprets a request, a program performs a calculation, several analyses
run independently, and a later step consumes their results. The application
also needs to know which inputs produced an output, what work survived an
interruption and which process version should run next.

RPNH makes these relationships explicit. For a workflow such as
`prepare -> (facts || risks) -> join`, the two branches have separate outputs;
the join becomes eligible when both required products are available. The same
principle applies when a node is an existing business program rather than a
model. The process definition describes the work, trusted registration supplies
its implementations, and the runtime records the exact execution.

| Capability | Current mechanism | Use in an application |
|---|---|---|
| Mixed Agent and program execution | Registered components/executors, typed ports and declared outcomes | Connect reasoning, deterministic computation and validation in one process. |
| Explicit coordination | Petri-net places, arcs, tokens and resource claims | Express dependencies, parallel branches, joins and resource use. |
| Traceable outputs | Exact Registry references, registered products and terminal evidence | Read the selected result together with its execution context. |
| Continuation and controlled change | Checkpoints, execution generations, workspace revisions and adoption mappings | Continue stopped work or adopt a revised process with explicit lineage. |
| Reusable process definitions | `ModuleDeclaration`, composition, author revisions and portable packages | Retain and adapt the method used to produce a result. |
| Separate observation | Read sessions, graph/checkpoint projections and comparisons | Inspect execution and compare selected sources without becoming a writer. |

The architecture is most relevant when an application needs explicit dependencies,
persistent artifacts, repeated process reuse or controlled revision. It also
introduces declaration, validation and state-management work. A short, disposable
interaction may need only the conversational entry point or a simpler tool loop.

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
why [§9](#9-example-results) reports original task scores separately
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

People and coding Agents can both use these Python interfaces to author process
revisions. The stable identities and explicit mappings connect each revision to
its source structure and make selected changes available for later composition.

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

These interfaces provide process reuse and exact contribution accounting through
local, explicitly bound author and read hosts. [Normal-child/Workset contracts](reference/normal-child-root-contract.md)
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
Host policies determine which tools, effects and budgets a task receives.
Native plugins run as trusted host code; the optional Linux isolated-program
substrate provides a separate execution boundary. Applications select the
appropriate trust and isolation policy for their tools. See
[customization](guides/customization.md) and
[controlled managed tools](controlled-managed-tools.md).

## 8. Getting started and example workflows

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
the default fixture makes no call to a real model.

The [hybrid summary](../examples/hybrid_summary/README.md) example makes mixed
execution concrete: `normalize -> demo/summarize -> explain`. Two Agent nodes
surround a native program that performs the summary calculation. In deterministic
mode the Agent responses are scripted; a selected execution profile enables the
live-model path. Each handoff still uses registered inputs and outputs, so the
same graph can be inspected at both the business-node and Petri-net levels.

Next, use the [native plugin](../examples/native_plugin/README.md) to modify a
real program contract, the [hybrid summary](../examples/hybrid_summary/README.md)
to inspect Agent–program–Agent data flow, or the
[package reuse](../examples/package_reuse/README.md) example to bind the same
closed process in a receiver environment. The installed export catalog supports
`adapter_task`, `native_plugin`, `hybrid_summary`, `compose_serial` and
`package_reuse`. `compose_serial` demonstrates declaration composition; matching
trusted registrations are still needed for execution.

For a live model, configure the provider, exact model and execution profile using
the [model guide](guides/models.md), then select that profile in the task or
example. The current source is the `0.1.0rc2` development candidate; the older
public `v0.1.0rc1` binaries contain an earlier feature set. See
[installation](guides/installation.md) for source and release options.

## 9. Example results

The examples connect RPNH to two different kinds of application: ERP operations
on a persistent business system and code changes under progressively revealed
requirements. The tables below keep the original evaluator's result separate
from the native runtime's terminal status.

### 9.1 ERP-Bench

The ERP adapter connects an Agent to a local Odoo world through managed Python
scripts and provides a deterministic plan-validation tool. The Agent can inspect
business entities, prepare a plan, apply changes and read back the resulting
state; the original ERP-Bench grader evaluates that state independently.
A managed operation covers one script, which may contain several Odoo actions.
See the [ERP example](../examples/erp_bench/README.md).

The following runs used clean tracked RPNH source at
`6f8ee2e406f3c70edb73206f861e56a0202b9f15`, model `gpt-5.6-terra` through
`local_process`, and ERP-Bench task/scorer revision
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`. Their evidence was integrated at
`74fad32`; that publication identity is distinct from the tested revision.
The adapted environment used Harbor 0.24.0, Odoo 19.0.20260926, Python 3.12.3
and PostgreSQL 18.6. The solver reached only local Odoo, with no external
network. [Source identity][erp-source] · [Model condition][erp-model] ·
[World configuration][erp-world].

| Task and run | Original business result | Applicable checks | Real model calls |
|---|---|---|---|
| `2000_easy_01_buy_only_baseline`, A04 / `s04` | 100/100, passed | 37/37 | 9 |
| `2299_hard_repair_plan_hard`, A01 / `h01` | 21/100, failed | 86/95 | 13 |

Sources: [A04 score projection][erp-smoke], [H01 original reward][erp-reward]
and [H01 rule results][erp-rules]. A04 retains public score/lifecycle projections
and raw-file hashes; H01 also includes the original reward, rule results and
checker log. The [collection manifest][erp-manifest] identifies the available
materials and historical provenance-validation limits.

The hard task completed its execution path but did not satisfy business
acceptance. Its nine failed checks comprise four constraints and five
component-purchase provenance checks. The original scoring rule gates the other
dimensions when constraints are incomplete, yielding 21/100 from 63/75 constraint
points; 86/95 is a check count, not the score. One concrete mismatch is workcenter
capacity: the Agent's readback used 45 minutes per unit, while the task's route
rule uses 55, producing 6,270 minutes against a 5,555-minute cap. Three constraint
checks also recorded `bool`/`datetime.date` exceptions. These remain part of the
original result. [Scoring rule][erp-score-rule] · [Readback][erp-readback] ·
[Route rule][erp-route] · [Checker log][erp-checks].

This example illustrates the division of responsibility: RPNH records execution,
products and lineage; the application supplies domain validation, and the
independent grader determines business acceptance. A plan validator operating
on supplied observations can pass while the resulting business world fails its
original checks. These two different tasks are individual runs, not a matched
comparison or a task-set success estimate.

### 9.2 SlopCodeBench

The `code_search` example incrementally changes a codebase as new checkpoint
requirements become available. The current integration uses an outer Python
controller to select checkpoint order, apply the continuation policy and hand
off source snapshots through the upstream Session path. Within each checkpoint,
RPNH's native task runtime executes the Agent and its managed commands. Source
continuity across checkpoints belongs to that adapter/controller path; it is
reported as `native_workspace_reuse=false`.
See the [SCB example](../examples/slopcodebench/README.md).

The published run covers **the first three of five checkpoints** in adapted
development-prefix mode. It used runner revision
`31ceea3add480edb33431e70475c4c70597e6b31`, problem revision
`9cd9ca3a51c3d3e2a99d2488a25baf73a2204451`, and model
`codex/gpt-5.6-terra`. The installed RPNH bytes were verified against
`74fad32d369876841686d10d33361c016e3d3648`; the results and execution evidence
were published at `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0`.
[Run conditions][scb-summary] · [Installed source identity][scb-identity].

| Checkpoint | Original evaluator cases passed | Evaluator exit | Runtime outcome | Real model calls |
|---|---|---|---|---|
| 1 | 13/13 | 0 | `complete` | 5 |
| 2 | 25/25 | 0 | `complete` | 5 |
| 3 | 40/47 | 1 | `complete` | 14 |

Original reports: [checkpoint 1][scb-cp1], [checkpoint 2][scb-cp2],
[checkpoint 3][scb-cp3]. The third checkpoint has seven business-test failures
and `infrastructure_failure=false`. Its failures comprise two Core cases and
five Functionality cases; all 25 regression cases pass. Checkpoint totals include
regression cases and therefore cannot be summed as unique benchmark tasks.

Each checkpoint had a 48-model-call limit and a 7,200-second owner wait limit.
The recorded run used 24 real calls with no post-limit excess, and elapsed time
was 998.82 seconds. Upstream cost, net-cost and step caps were disabled (set to zero);
the applied model-call limit is the relevant bounded control for this run.
The task-level normalized token total and USD cost were not provided. Per-call
adapter returns retain token-usage fields, which are separate from a normalized
task-level total. [Run summary][scb-summary] · [Published evidence][scb-collection].

The solver ran without network access in a fresh container for each checkpoint,
with source snapshots carried forward. Image building and evaluation used host
networking; image setup included a same-version download compatibility adaptation.
The original evaluator ran, but the official `AgentRunner` and full five-checkpoint
benchmark did not. Grader feedback was not used to repair the solver, and there
was no automatic retry or resume. The selected `any-case` continuation policy
allowed the outer command to exit successfully despite checkpoint 3's failures;
the table reports the original test outcomes. [Development summary][scb-development]
and [environment adaptation][scb-adaptation] specify these conditions.

The run demonstrates source continuity and native task execution through three
successive requirements, with complete original-case acceptance at the first
two checkpoints and partial acceptance at the third. It does not establish
full-benchmark acceptance or a comparative harness advantage.

### 9.3 Additional retained results

The repository also retains an earlier ERP smoke run, A03, at `2ca5fbc`, with
0/100 and 11 real calls. Its [diagnosis][erp-a03] identifies an input-reader
binding omission; that historical condition is distinct from the later A04 run.
A recorded [ERP offline test window][erp-test-command] reports 190 passed tests
and 35 separately reported subtests for `examples/erp_bench/tests`.

The dated [AutomationBench results](../examples/automationbench/PUBLIC_RESULTS_20261006.md)
record freeze04 first18 as 5 PASS / 9 FAIL / 4 BLOCKED and a separate repair4
condition as 1 PASS / 3 FAIL. The older 14 scored tasks were not rerun.
The [release-validation history](guides/release-validation.md) records other
version-specific checks. These different tasks, revisions and test windows are
kept separate from the ERP and SCB results above.

## 10. Engineering context and source guide

RPNH sits within the broader engineering practice of durable tasks, typed
interfaces, event histories, checkpointing and plugin-based Agent runtimes.
[DeepSeek Harness architecture](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/architecture.md)
describes plugin responsibilities and turn lifecycle. OpenAI's
[Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
and [Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/)
explain request/tool/context processing and the boundary between a shared core
and client surfaces. These provide related architectural context; the published
RPNH runs are not comparisons with those systems.

RPNH organizes its runtime around the connection between a versioned process
definition and exact Registry/Petri-net execution. This connects authoring,
execution, continuation and observation: the method can be retained as a
structured artifact, its implementation can be bound by a host, and its outputs
can be traced to a particular execution and process revision.

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
| How is a task's selected result read? | [`task_control.py`][task-control-source] |

For application integration, start with the [installed entry points](#7-embedding-extension-and-operating-interfaces)
and [examples](#8-getting-started-and-example-workflows), then use the linked
API references for the required authoring or host boundary. The source map
provides implementation detail; public contracts and current platform requirements
are described in the corresponding guides.

[module-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/module.py
[petri-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/petri_contracts.py
[registration-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registration.py
[compiler-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/compiler.py
[harness-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/harness.py
[owner-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/run.py
[commit-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registry/_event_store/commit.py
[graph-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/agent_workflows.py
[revision-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/registry/module_revision.py
[collaboration-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/collaboration/__init__.py
[receiver-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/collaboration/environment_host.py
[task-control-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/cpn/rpnh/task_control.py
[net-tests]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/tests/test_native_net_operations.py
[erp-source]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/source-identity.json
[erp-model]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/model-configuration.json
[erp-smoke]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/s04/public/original-score.json
[erp-reward]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/reward.json
[erp-rules]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/w/harbor/verifier/rule_results.tsv
[erp-a03]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/records/input-delivery-a03-v2/public/diagnosis.json
[erp-test-command]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-local/checks/erp-integrated-tests-01.json
[erp-world]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/r/h01/public/world-projection.json
[erp-score-rule]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/test.sh#L351-L362
[erp-readback]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/records/registry/h01/agent_action_v3/a33b2d441be95df9b29a5ee5336489fc.json
[erp-route]: https://github.com/agentic-labs/erp-bench/blob/ceba3880af555129b5278e056a0c20f2fb5a0ba9/tasks/2299_hard_repair_plan_hard/tests/checks.py#L305-L313
[erp-checks]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/logs/r/h01/w/harbor/verifier/checks.log
[erp-manifest]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/erp/MANIFEST.json
[scb-summary]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/evidence/scb-real-summary.json
[scb-identity]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/evidence/scb-real-install-byte-identity.json
[scb-cp1]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_1/evaluation/report.json
[scb-cp2]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_2/evaluation/report.json
[scb-cp3]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/checkpoint_3/evaluation/report.json
[scb-collection]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/README_ZH.md
[scb-development]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/run/development-summary.json
[scb-adaptation]: https://github.com/Deng-0119/RPNH/blob/dbad00458e9b356fcaf0bb97ceb90258ed9b1de0/evidence/first_wave/20261008/scb-real/preparation/work/scb-real-plan01/adaptation.json
