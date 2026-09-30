---
name: rpnh-technical-report
description: "Introduce RPNH's design, execution model, composition boundaries and evaluation path."
metadata:
  document-kind: explanation
  audience: operator-and-developer
  language: en
  counterpart: technical-report_ZH.md
  revision: "2026-09-30.1"
  status: source-reviewed-introduction
  basis: "Deng-0119/RPNH at 40f1be804b00abab587636783a5f0e9d3e1f83a9"
---

English | [中文](technical-report_ZH.md) | [Documentation](index.md)

# RPNH Technical Report
## Registry-governed Agent execution with typed Petri nets

**Document date:** 2026-09-30. **Implementation baseline:**
[`40f1be8`](https://github.com/Deng-0119/RPNH/tree/40f1be804b00abab587636783a5f0e9d3e1f83a9).
This introduction addresses prospective users, integrators and external testers.
It explains the public harness, not private research workflows. It complements
the operating guides and API references rather than replacing them.

## 1. Overview and intended use

RPNH is a provider-neutral execution framework for combining language-model
Agents, native programs and reusable workflows. Its central design is a closed
execution path: a typed Petri net determines when declared work may start;
a persistent Registry supplies the authoritative identities, resource versions
and committed evidence on which that decision depends. Returned products must
be registered and settled before they can authorize downstream work.

The point is not simply to display an Agent conversation as a graph. It is to
make execution dependencies, results and continuation boundaries inspectable.
For example, a report-writing task can require two independently produced
analyses before a synthesis step starts, associate the synthesis with the exact
input versions, and preserve the evidence needed to inspect or continue the run.
Models choose semantic content; the harness manages the execution structure in
which that content is produced and used.

This design is relevant when a task mixes model reasoning and existing code,
requires parallel work and synchronization, produces evolving files, or must be
stopped and continued without treating chat history as the sole execution state.
A small, disposable, single-call task may not benefit enough to justify the
additional declarations and persistent state. These are design tradeoffs, not
measured claims that RPNH outperforms a particular alternative.

The public distribution includes the core runtime, a Basic terminal, optional
Codex/OpenCode presentation and DSH host integration, native-plugin interfaces,
examples and a read-only PetriNet dashboard. The baseline is a prerelease under
the repository's [MIT license](../LICENSE). Supported runtime platforms are
Linux, including WSL2, with Python 3.11 or newer; support declarations are not a
claim that every interpreter/platform combination has been tested. See
[installation](guides/installation.md) and [architecture](architecture/design.md).

## 2. The execution model

### Registry and PetriNet answer different questions

| Element | Question it answers | Meaning in RPNH |
|---|---|---|
| Registry | What is the exact committed fact? | Identities, versions, admitted executions, access, results and their relationships. |
| Place and token | What input or condition is available? | Typed occurrences associated with the declared data, control or resource semantics. |
| Transition and arcs | Which work can proceed, using what? | Input requirements, claims, output production and synchronization. |
| Marking | What is the current execution state? | The current token state and claims, hydrated from registered authority. |
| Owner | Who may advance that state? | The authorized writer path for a run, rather than any worker or viewer. |
| Terminal binding | What establishes this run's final result? | The declared terminal rule and its registered evidence, not process exit or visible text. |

For explanation, a committed run can be written as `S = (G, M, R)`, where `G` is
the adopted graph, `M` its marking and `R` the Registry history. This notation is
not a new public API. Workspace revisions and result identities are recorded in
`R`; a frontend does not maintain a second authoritative copy.

A transition is not eligible merely because its predecessor sent a message.
Its required token occurrences and resource versions, registered bindings,
claims, budgets and applicable declared conditions must satisfy admission.
An injected scheduling policy can select a distinct subset of **already enabled**
transitions; it cannot make an otherwise disabled transition executable.
The current implementation checks this boundary in
[`Harness.schedule_ready`](../cpn/rpnh/harness.py).

Place sharing is also not broadcasting. A workflow that needs one result in two
independent lanes must declare a distributor with appropriate outputs; a join
must declare the inputs it actually needs. Feedback in the Agent-workflow layer
is an explicit, budget-bounded rework route, not an unrestricted dependency
cycle. These distinctions give branching, synchronization and rework executable
meaning rather than relying on their visual appearance. See the
[harness architecture](ARCHITECTURE.md) and
[native net-operation rules](guides/net-operations.md).

### Structural validity and business correctness

RPNH governs structural properties: whether work was admitted through its
registered boundary, which identities and resources it used, and how its outcome
was published. A structurally valid run may still contain a wrong answer or a
business-denied outcome. Access policy, confidentiality rules, retry strategy
and answer acceptance remain application decisions. They can be represented by
registered operations, guards or Inspector conditions at the appropriate
boundary; the generic harness does not decide an organization's policy.

## 3. Architecture and ownership

The execution path separates presentation, session/task control, run ownership,
operation implementation and observation. A model, native tool or adapter is not
permitted to become a competing owner simply because it is easier to integrate
that way.

| Layer | Responsibility | Boundary to preserve |
|---|---|---|
| Presentation | Terminal interaction and protocol translation. | Basic, Codex and OpenCode reuse the same direct-session authority rather than copying its state. |
| Main session and task control | Durable conversation, task links and explicit controls. | Independent children own their own run state; main focus is not child lifetime. |
| RunOwner and owner event loop | Adoption, admission, Start, settlement and controls. | Completion handling returns to the run's owner path. |
| Harness and marking | Enabled-work selection, bounded dispatch and result coordination. | Concurrent operations do not become independent Registry writers. |
| Registered components and plugins | Model calls, native computation and declared application behavior. | Implementation must respect the admitted execution and selected contracts. |
| Registry and workspace services | Versioned evidence and committed publication. | Helpers share the existing transaction/authority boundary. |
| Dashboard and observers | Project current or historical registered state. | Viewing cannot create tokens, settle work or resume a task. |

`Harness` accepts the existing `RunOwner`, its `OwnerEventLoop`, dispatcher and
submission callbacks. It allows bounded in-flight physical operations. When a
future completes, handling re-enters that owner loop, and products are checked
against the exact firing and execution lease before settlement. This is a
single-writer-per-run design, not a claim that all computation is serial. See
[`harness.py`](../cpn/rpnh/harness.py) and the
[runtime reference](reference/runtime-registry.md).

Three different forms of hierarchy should not be conflated. An **independent
child task** has its own Registry, Petri net and owner; the parent records exact
links rather than duplicating its events. A **delegated leaf** is bounded work
owned by an exact parent action. A **subordinate execution net** lives in the
same Registry beneath a business firing and models harness mechanics such as
file materialization or workspace finalization. Its progress cannot itself
advance business tokens: the business settlement must establish the terminal
mapping from that execution evidence to the result and successor checkpoint.
Main-session rollback therefore does not erase an independent child's history,
and a subordinate mechanical checkpoint is not a business-task result.

## 4. From a declaration to a completed result

The lifecycle is best understood through the evidence required at each boundary.

| Stage | What happens | What it does not establish |
|---|---|---|
| Declare and compile | Typed components, ports, operations, outcomes, links, budgets and terminal binding are checked against trusted Registration. | A compiled candidate is not yet a running, adopted net. |
| Adopt and admit | The owner adopts structure and binds an enabled firing to exact inputs, claims and execution context. | Admission alone is not a physical tool/model call. |
| Start and dispatch | Start authority is recorded before the operation callback is submitted. | Submission does not prove remote completion. |
| Register products | Returned data is validated and registered against the exact execution/outcome. | Executor completion is not Petri settlement. |
| Settle | Result, successor marking, relevant workspace publication and subordinate mappings are closed through the owner. | A settled firing need not finish the entire workflow. |
| Establish terminal evidence | The declared terminal rule selects the registered final result. | Text in a terminal window cannot substitute for this evidence. |

The distinction is visible in the code: `OperationProducts` requires
`RegisteredOperationOutputsAuthority`, while a resource wait, execution block or
terminal handoff uses `OperationDisposition`. These states are not silently
converted into Success. `HarnessResult` separately exposes terminal evidence and
a completion error. Even the implementation method named `succeed` refers to
settlement of a declared outcome; it must not be read as a universal guarantee
of business success.

The EventStore commit coordinator validates typed task/transaction identities,
exact references, expected state and publication structure. Its current batch
contract permits at most one firing settlement/publication per transaction;
helper modules do not create separate publication authorities. This keeps a
result's relationship to its successor state explicit. See
[`publish_batch`](../cpn/rpnh/registry/_event_store/commit.py) and the
[runtime/Registry reference](reference/runtime-registry.md).

## 5. Versioned workspaces and recovery

A firing operates on a private view derived from a registered workspace revision.
Publication is not inferred by scanning for a file with a plausible name. The
workspace-finalization path freezes a candidate archive, while ordinary Success
publishes the settled successor and its relationship to the business marking.
Per-path deltas record creates, updates and deletes with exact before/after
resource references. Concurrent publication is handled against the current
workspace head; conflicts are not simply hidden by whichever directory was
written last. The result is versioned execution evidence, not automatic undo
for an external system.

Recovery has several distinct meanings:

| Mechanism | Implemented meaning | Important limit |
|---|---|---|
| Owner stop and resume | Stop at an authorized boundary, preserve a checkpoint and continue the stopped run. | A stop request does not prove that a remote request never took effect. |
| Exact-completion recovery | Under the documented narrow conditions, settle a stale firing from its durable registered completion without invoking it again. | Missing/conflicting proof, excluded HOST effects or unsupported cuts do not authorize replay. |
| Owner-selected checkpoint reopen | In the same run/Registry, select a committed checkpoint and append a new execution generation with fresh occurrences and restored registered workspace state. | Later history remains; old external effects are not undone. |
| Dashboard timeline | Read a saved checkpoint and its projection. | This does not resume, reopen or re-execute anything. |

An unresolved provider submission may be recorded as `submission_unknown`.
Locally sent bytes or a timeout cannot establish the remote outcome. A later,
explicit owner-selected reopen can close an unresolved attempt as interrupted
and continue with a new identity under the supported protocol; that is not an
automatic retry of the old physical attempt. This distinction matters whenever
an operation can charge an account, send a message or change an external system.

Exact protocol conditions, including workspace-bound completion evidence,
parallel-firing drainage and idempotent reopen commands, belong in the
[runtime reference](reference/runtime-registry.md), not in a blanket promise
of crash recovery or exactly-once external execution.

## 6. Workflow composition and the limits of the Git analogy

RPNH exposes application-neutral building blocks for **extracting, composing,
instantiating, branching and replacing net definitions**. They allow an
application to reuse a structured process without embedding its optimization
policy in the core. Definition transformation, execution continuation and
workspace versioning are related, but they are not the same operation.

Extract operates on whole modules or complete components at supported public
port boundaries. Compose joins explicit compatible entry/exit pairs. Instantiate
creates independently named symbols without creating extra budget. Branch
extracts a definition and optionally instantiates it; it does **not** by itself
create another Registry or worker. The deterministic tests include serial and
parallel compilation and rejection of implicit broadcast:
[`test_native_net_operations.py`](../tests/test_native_net_operations.py).

Replacement uses the existing owner-edit path. In the supported
`whole_net_quiescent` mode, new admission pauses, active firings drain, explicit
state mappings or retirements are applied, and the successor is adopted with
the existing budget inventory. Same-owner Agent replacement preserves the
registered workspace lineage and execution environment rather than starting a
new implicit workspace.

This provides meaningful version/composition mechanisms, but not a complete
Git-like distributed execution system. Arbitrary cuts inside monolithic
components, automatic semantic merging of workflow changes, cross-Registry
migration, workspace fork/import and cloning an in-flight model call are not
established by the basic operations. Likewise, supported checkpoint reopen is
not permission for an arbitrary historical-token import. See
[native Petri-net operations](guides/net-operations.md) for the exact API and
unsupported branches.

## 7. Models, tools and frontends

Provider neutrality means that provider/model selection belongs to a shared,
user-owned configuration boundary. The default catalog is empty. A supported
route and exact model must be explicitly configured before model-backed tasks;
a frontend does not silently supply a separate provider authority. It does not
mean that every provider protocol is automatically supported.

Native plugins are explicitly installed and selected. They declare input/output
schemas, operation identity, effects, resources and limits; their computation
then enters the same admission and settlement path. A skill may be a registered
instruction resource. An MCP-backed capability needs an explicit supported
binding with visible operation/resource/effect semantics. Merely adding a
Markdown file or an arbitrary server address does not create a managed skill or
a universally compatible tool.

The public Basic, Codex and OpenCode entries are presentations over the shared
runtime; the supported Codex/OpenCode clients are version-specific. Basic,
Codex and OpenCode can open the same direct-session root sequentially, while
an owner lease prevents concurrent writable presentation. DSH is a registered
host integration with its own session surface. These are not four separate
implementations of provider handling, workspace state or recovery.

Registry/Petri governance is also not an OS sandbox. Trusted host/plugin code
and transitive dependencies still require review. An opaque program's internals
do not become fully governed merely because its outer invocation is registered;
additional internal actions that need governance must be exposed through the
appropriate declared boundaries. See [customization](guides/customization.md)
and [installation surfaces](guides/installation.md).

## 8. A reproducible first test

The included parallel example is a useful entry because it exercises the
structure without requiring API credentials. It has the shape
`prepare -> (facts || risks) -> join`. Its default deterministic local-process
fixture uses canned protocol responses: it tests the execution path, not a
language model's reasoning ability.

In a **fresh checkout** on Linux/WSL2, run:

```bash
git clone https://github.com/Deng-0119/RPNH.git
cd RPNH
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check

DEMO_ROOT="$(mktemp -d /tmp/rpnh-report.XXXXXX)"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

The example reports `status: PASS`, terminal-evidence information and the result
when successful. The final command starts a read-only local viewer and prints
its address. Dependency installation may use the network; the default fixture
does not call a model. These commands target the current checkout, so keep the
printed commit with any feedback. To reproduce this report's implementation
baseline, select the full baseline commit before installing instead of assuming
that a later `main` is identical.

Inspect whether both branches contribute registered results before the join,
whether output references agree with the final evidence, and whether the viewer
is displaying the intended run/checkpoint. A successful screenshot alone is not
the acceptance condition. The [workflow gallery](../examples/workflow_patterns/README.md)
contains real dashboard images from deterministic runs and commands for serial,
document and long-process cases. The report does not label those fixtures as
new live-model experiments.

Model-backed testing is a separate step: configure an authorized route following
the [documentation index](index.md), then use an explicitly selected execution
profile or launch `rpnh --frontend basic`. It may incur the tester's own provider
charges. Start with a small task before testing parallelism or recovery, and do
not use the first recovery experiment to repeat irreversible external actions.

## 9. What the available evidence establishes

This report is a source-grounded introduction, not a new benchmark submission or
an independent full-runtime audit. The baseline includes dated validation
records; their source versions and environmental limitations matter.

| Evidence | Recorded scope | Appropriate interpretation |
|---|---|---|
| Historical complete offline suite | `073a451`, 2026-09-28: 744 passed and one environment-dependent skip. | Deterministic baseline at that revision, not a total for all later code. |
| Release-candidate collection | `1e85b4f`: 790 passed, one OpenCode PTY skip and 23 temporary Unix-socket-path failures; both affected files then passed all 30 tests under a short root, covering 813 unique passes in combination. | Combined candidate evidence, not a claim that the first invocation was a clean full pass. |
| Later focused, packaging and installed checks | Dated, change-specific records in the release-validation page. | Evidence for the tested boundary/artifact; overlapping counts must not be added. |
| Deterministic workflow gallery | Local fixtures with terminal/result checks. | Exercise of protocol, structure and observation, not measured model quality. |
| Real-provider use | Must be tied to its own authorized route, task and dated record. | Does not generalize automatically to another provider, model or workload. |

The [release-validation record](guides/release-validation.md) gives the detailed
provenance and exclusions. It reports no real model/provider calls in the listed
offline validation. Neither those test counts nor the design itself establishes
lower cost, better task accuracy, global liveness or general protection from
malicious models.

For external evaluation, separate functional completion, structural evidence,
recovery behavior and operational cost. A useful report records the exact source
and environment, selected route when applicable, task inputs, expected result,
actual terminal state, reproducible steps and sanitized diagnostics. Comparisons
should control the model, task set, tool permissions and budget, while measuring
model calls, elapsed time and execution/storage overhead separately. There is no
benchmark score claimed in this document. Never publish credentials, confidential
inputs or raw private runs with a public issue.

## 10. Reading and implementation map

| Question | Starting point |
|---|---|
| What does the owner/worker boundary enforce? | [`cpn/rpnh/harness.py`](../cpn/rpnh/harness.py), especially `schedule_ready`, `_complete` and `result`. |
| How is a publication closed? | [`registry/_event_store/commit.py`](../cpn/rpnh/registry/_event_store/commit.py) and the [runtime reference](reference/runtime-registry.md). |
| How do sessions and execution-net layers fit together? | [Harness architecture](ARCHITECTURE.md) and [execution principles](architecture/design.md). |
| Which composition cases are executable? | [Native net operations](guides/net-operations.md) and their [deterministic tests](../tests/test_native_net_operations.py). |
| How should a new capability enter the harness? | [Customization](guides/customization.md). |
| How can a tester inspect a real example? | [Workflow gallery](../examples/workflow_patterns/README.md) and [installation](guides/installation.md). |
| Which observations were actually validated? | [Release-validation record](guides/release-validation.md). |

Source links are navigation aids, not a declaration that private implementation
classes are stable SDKs. This report was prepared from the identified public
source and documentation, including direct inspection of the scheduling,
completion, publication and composition-test boundaries. Preparing it did not
rerun the runtime suite or make model calls.

RPNH's main contribution as an execution design is to keep declared structure,
registered evidence and controlled continuation connected. Its usefulness for a
particular application should be judged by whether those properties solve that
application's coordination and inspection needs, and then verified on its actual
workload.
