---
name: rpnh-architecture-overview
description: "Explain the public RPNH execution and authority architecture."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: ARCHITECTURE_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-pre-release
---

# RPNH Harness Architecture

[中文](ARCHITECTURE_ZH.md)

RPNH is a reusable execution harness. Its public entry point is `rpnh`; the
stock Codex TUI, the basic CLI, and the PetriNet viewer are interfaces over
RPNH-owned state rather than independent execution backends.

```text
stock Codex TUI 0.155.0 ─┐
OpenCode TUI 1.18.32 ────┼─> one canonical MainSession Registry ─> TaskControl
basic CLI ───────────────┤                                      │
provider profile ────────┘                                      ├─> independent agent task
                                                                └─> graph workflow task
                                                                         │
                                                                         v
                                                     Harness + typed PetriNet + Registry
                                                                         ^
read-only PetriNet CLI/viewer ──────────────────────────────────┘
```

## Ownership layers

The frontend owns terminal interaction only. One direct session root maps
one-to-one to one RPNH main-session Registry. `MainSession` owns that durable
thread, turn ordering, committed conversation history, selected execution
profile, and Registry-native links to child tasks. Basic, Codex and OpenCode
can open that same root sequentially; they do not copy it into frontend-specific
thread containers. Their protocol IDs and optional sidecars are presentation
metadata, never execution authority.

A nonblocking owner lease is held before a fresh canonical root becomes a
valid Registry and prevents two writable frontends from opening it
concurrently. Merely reopening, listing or projecting
the session does not compensate a committed child launch. Compensation and
terminal-turn settlement occur only as part of a subsequent explicit execution
action, including the user's explicit resume command. Independent child owners
remain separate and continue according to their own Registry checkpoints.

Every main turn's model execution runs in its own supervised Registry root. The
main Registry stores the turn lineage and exact terminal receipt needed to
commit the assistant answer. Likewise, every independent `/agent` or workflow
owns a separate Registry root. For these independent children, the main
Registry stores only a parent-Registry-relative path, task kind, optional originating
committed turn, and exact child task/run references once readable. It does not
copy child events, tokens, workspace revisions, or results. TaskControl
manifests remain process-lifecycle projections and are not the relationship
authority.

New Registries interpret each child path relative to the immediate parent
Registry root. A grandchild is reached by following the child link and then the
grandchild link; it is not independently anchored back to the outer session
directory. New Registries record this convention in Registry metadata; absence
of that marker identifies an existing session-root Registry and preserves its
old interpretation. Worker specs and TaskControl manifests likewise persist
parent-relative references.

Each task owner socket remains `owner.sock` inside that task's Registry. On
Linux, RPNH opens the immediate parent directory and uses a short transient
`/proc/.../fd/...` address only for `AF_UNIX` bind/connect. That transport
address is never persisted and does not flatten or replace the Registry tree.

The main agent may reply directly, create one independent single-agent task, or
act as Designer and declare a workflow graph. Every independent child owns a
separate process, Registry root, PetriNet, execution authority, and owner control
channel. Closing or interrupting the main frontend does not imply stopping its
children.

The basic CLI keeps only a process-local task focus. `/switch TASK_ID` changes
where later controls or messages are sent; it does not transfer Registry
ownership and does not pause, restart, or stop a child.

## Workflow graph and PetriNet lowering

A workflow declares nodes, typed input/output ports, one or more ingress and
egress ports, and arcs. The dependency subgraph must be an entry-to-exit DAG and
defines first-generation activation, including fan-out, parallel branches, and
fan-in/join. The user's task is materialized as the ingress task-resource token.

Cycles are not encoded as ordinary dependency edges. A feedback arc is an
explicit alternative rework route with a distinct transition and a positive
`max_rework_cycles` budget. This separates initial dependency inputs from rework
inputs and gives every loop a Registry-enforced stopping bound.

The validated graph is lowered into typed places, transitions, arcs, token
claims, firing admission, execution, and settlement. A process exit or a visible
frontend message cannot replace PetriNet and Registry terminal evidence.
Firing admission requires every predecessor occurrence named by every input arc
weight; successful settlement deposits exactly each output arc's declared
weight into its own successor place. A fused place is a conflict/shared place,
not broadcast. Branching therefore requires a declared transition with distinct
output arcs; native composition rejects one public exit wired directly to
multiple consumers.

Each admitted business firing may also own one or more **execution-net
instances in the same Registry**. These subordinate Petri nets describe
predefined harness mechanics such as file materialization and workspace
finalization; they are not Designer-authored business workflow and do not open
a child Registry. Their tokens, transition firings, checkpoints, and evidence
are separate from the business marking. They use the same weighted input/output
arc rules and may retain multiple independent active firings. Business Success
is allowed only after every subordinate instance is `map_ready`; the Success
transaction compare-and-appends a seal to the firing's execution-child stream,
then records an immutable terminal mapping from each execution checkpoint and
its evidence to the operation result, successor business checkpoint, and
workspace revision. A concurrently attached child makes that transaction stale
and forces re-evaluation. Until Success commits, execution progress cannot
advance or fabricate the business marking.

## Agent execution, workspace, resources, and delegation

An admitted agent firing runs an agent loop with a Registry-defined tool
catalog. Semantic output is written through declared output ports. Tool actions,
provider attempts, response acceptance, and settlement are tied to the exact
firing and invocation identities.

Each firing receives a private workspace view derived from the current
Registry workspace revision. A bounded, network-disabled workspace action may
create or change regular files. Successful settlement registers those files as
versioned resources and advances the workspace lineage, so later firings can
materialize and modify the exact registered state. Files from an interrupted,
unsettled action are not promoted to the shared revision.

Semantic file writes stage bytes below the workspace's reserved internal
directory, validate and publish the exact Registry resource, and only then use
an atomic replace for the user-visible relative path. Validation or publication
failure therefore preserves the prior file. The staged file is synchronized
before Registry publication, and the affected directories are synchronized
after replace before the execution checkpoint can become `map_ready`.
Workspace finalization publishes a full immutable candidate archive and a
`path_deltas` record before operation completion: every create, update, or
delete names its exact before/after resource versions and bounded summaries.
The candidate is evidence for the subordinate execution checkpoint; the live
directory is not completion authority. The final workspace revision and
business marking are still published together by the ordinary Success
transaction.

The workspace shell is a **write-confinement** boundary: it may mutate only its
firing workspace (plus `/dev/null`) and cannot use network sockets. It is not a
host-file read secrecy boundary because the command must still read the Linux
runtime, executables and libraries. Agent semantic reads remain limited by the
Registry `read_file`/resource authorities. Deployments that require filesystem
read secrecy need an outer container or VM boundary; the harness does not claim
that property.

Workspace finalization retries one observed SQLite I/O interruption immediately
with the same per-firing idempotency key. This does not replay the AgentLoop or
provider request. A second failure remains a `framework_repair` block with no
fabricated terminal or final-result evidence; provisional files must not be
reported as completed output.

Registry schema and event validation are structural authorities, not a hidden
business decision engine. They validate exact identities, references, ordering,
transaction closure and evidence consistency. A timed-out or nonzero workspace
action remains immutable evidence, but current writers do not attach a generic
`next_attempt_allowed` decision, require a reserved script comment, or force the
next action to be a retry. The selected runtime and declared business operation
decide whether to correct, verify, diagnose or terminate.

If a process stops after the exact output bundle, immutable workspace candidate,
and registered-operation completion have all been recorded, `resume_run` may
settle that one firing from Registry evidence without rerunning its AgentLoop,
provider, shell, or file action. Recovery validates the candidate archive,
execution checkpoint, parent firing, route identities, and completion record,
then performs the normal Success transaction. A missing or non-`map_ready`
candidate fails closed. This is not generic replay authorization for arbitrary
external effects or for a request whose submission/result remains unknown.

`request_resource` uses Registry resource lifecycle, queue, grant, and lease
authority rather than an informal shared path. A waiting firing continues only
through the exact retained dispatcher after its resource grant becomes current.

`delegate_leaf` creates a bounded child LLM session owned by the parent action.
The leaf may use its permitted atomic tools but cannot recursively delegate. Its
terminal response is registered as a resource and returned to the same parent
action. This is different from `/agent`: a delegated leaf has no independent
process, owner channel, or switchable CLI task identity.

## Interruption, checkpoint, compaction, and resume

Registry checkpoints identify durable execution boundaries. Completed atomic
actions and settled workspace revisions remain visible. Work that was executing
at interruption is recorded as interrupted and is not published as a completed
semantic output.

Stopping a child through its owner channel records `stopped_by_owner`; resuming
uses the same Registry lineage and verifies that every persisted transition
still resolves to the same provider route and exact model. If that child is the
execution object for the current main turn, the main Registry keeps the turn
active but paused. Reopening the main session reconstructs that paused state and
does not dispatch it automatically. `/resume` continues the same child Registry
from its latest checkpoint. User-issued `/rollback` commits only the main turn
as interrupted and returns the conversation to its prior completed turn; the
separate child Registry and every independently indexed task/workflow Registry
remain intact.

Independent child control also exposes an explicit checkpoint-reopen protocol.
`/task ID checkpoints` lists committed cuts; `/task ID reopen CHECKPOINT`
selects one exact cut after the current owner has stopped or terminated. The
Registry atomically appends an owner authorization, fresh occurrences cloned
from that cut's live tokens, workspace revisions based on that cut, a new
checkpoint and a new execution authority generation. Current attempt high-water
marks are retained so physical attempt identities are never reused. Later
history is superseded for current execution but remains immutable and readable;
the task ID, run ID, net and Registry do not change.

If the selected reopen command reaches a recoverable running generation, Core
first enumerates every active firing in deterministic order and closes each
exact firing through its registered completion or owner-interrupted outcome.
Only then may it stage the selected historical cut. Each closure is separately
idempotent, so loss of the replacement owner after one parallel branch has
closed resumes by draining only the remaining branches; it does not replay the
abandoned physical calls or weaken ordinary stale-lease admission.

An owner stop is also propagated into the currently running provider or
workspace boundary. The in-flight process/connection is cancelled, racing
products are rejected, and the current semantic action is not committed. For
an LLM input, Registry closes the provider attempt, logical call, and neutral
invocation attempt atomically as owner-interrupted while retaining the observed
submission state. This creates neither an LLM success nor an LLM failure result;
an explicit resume reserves a new physical attempt identity in the same child
Registry.

When an agent reaches response-length or context-pressure boundaries, context
compaction is a registered execution step. The replacement history preserves a
fact capsule, a model-produced continuation summary of the complete committed
turn prefix, and a token-bounded tail of recent complete messages. Every source
turn remains immutable in Registry. Each compaction records a contiguous
context-session ordinal transition for the same agent and starts a fresh
model-visible session without creating a new firing; replay remains attached to
the interrupted semantic slot instead of becoming a new user turn.

## Provider and frontend authority

A user-generated execution profile fixes adapter kind, provider identity, exact
model, route, limits, and credential environment names. The model identifier
sent to the provider is the configured `model_condition`; a failure never causes
silent provider, route, or model fallback.

Provider execution is shared harness infrastructure, not a frontend feature.
An opted-in host adapter receives a firing-local `registered_llm/v1` capability
and translates its host's messages and results at that boundary. It does not
open provider connections, resolve credentials, choose another model, implement
retry policy, or create a second accounting path. Codex, managed DSH, and the
OpenCode presentation therefore reuse the same selected profile, input
port, physical-attempt records, and settlement rules. Supporting a new host
requires a host adapter/plugin and declared operation bindings, not another
provider implementation.

The default frontend is the exactly pinned stock `codex-cli 0.155.0`. RPNH runs
its own Unix-socket compatibility server and implements only advertised protocol
methods. Codex `/model` projects and updates RPNH profiles; it does not create a
second model authority. RPNH-specific task commands are exposed by
`rpnh --frontend basic`, because the stock Codex client owns its slash-command
registry.

## Read-only inspection

`rpnh net --run RUN_DIR` builds a projection from the Registry without acquiring
writer authority. The terminal viewer and browser view distinguish transitions,
ordinary places, and resource places. Resource nodes and resource-only edges are
hidden by default and appear only when explicitly requested and actually
declared by the net.

### Current execution and terminal reads

Trusted in-process host adapters use `registry.run_authority.read_run_execution`
over an existing Registry core and its resource kernel. `RunReadCut.capture`
binds one canonical view, physical event head, writer epoch, task and native
run pointer to that exact core handle. The reader selects the existing sole
run authority at this cut. Optional expected refs are assertions, never lookup
selectors. An omitted terminal expectation derives the current result; an
active or owner-stopped run has no current terminal result. Older terminal
versions remain history, including across reopened execution generations.

The shared reader checks immutable descriptor bytes, authority/evidence/
checkpoint/result-index identity, atomic terminal publication and the resource
kernel's native direct-provenance contract. It returns exact typed refs and an
in-process `PreparedObject`, not a serialized storage locator or a new access
grant. `read_run_terminal_bytes` reads registered product bytes, with an optional
physical byte bound. These checks retain Registry's exact-version storage
contract; they do not introduce content-addressed storage or a payload hash
that Registry did not record.

Consumers own presentation and body limits. They recheck the same cut after
rendering and before exposing a result. HarnessAudit's exporter and local
environment result delivery share this reader. Fresh environment owners also
assert their startup net ref. Recovered owners without a startup publication
object still assert the run, task and evidence refs; they supply no caller net
expectation. The environment adapter's legacy `None` delivery candidate returns
`status: not_terminal` only to indicate that this owner call supplied no
candidate; it does not assert the Registry's current status. Query current
state with `read_run_execution`, which, like HarnessAudit, derives a current
terminal even when the caller omits an expected ref. No read creates events, opens
an execution owner, resumes a run, issues observer access, or selects a latest
resource as a fallback. This current-cut interface is distinct from a
permission-bearing external read session and from explicit historical views.

## Repository boundary

This repository contains the reusable harness, schemas, frontend compatibility,
provider configuration tools, and deterministic offline tests. It does not ship
project-specific workflows, experiment data, historical Registries, result
bundles, user profiles, or credentials.

The current runtime supports Linux only, including WSL2. Native Windows and
macOS are unsupported because execution depends on Unix domain sockets, Linux
`/proc`, `fcntl` locking, and POSIX signals.
