---
name: rpnh-architecture-overview
description: "Explain the public RPNH execution and authority architecture."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: ARCHITECTURE_ZH.md
  revision: "2026-09-28.1"
  status: source-reviewed-pre-release
---

# RPNH Harness Architecture

[中文](ARCHITECTURE_ZH.md)

RPNH is a reusable execution harness. Its public entry point is `rpnh`; the
stock Codex TUI, the basic CLI, and the PetriNet viewer are interfaces over
RPNH-owned state rather than independent execution backends.

```text
stock Codex TUI 0.155.0 ─┐
basic CLI ───────────────┼─> MainSession Registry ─> TaskControl
                         │                            │
provider profile ────────┘                            ├─> independent agent task
                                                      └─> graph workflow task
                                                               │
                                                               v
                                           Harness + typed PetriNet + Registry
                                                               ^
read-only PetriNet CLI/viewer ──────────────────────────────────┘
```

## Ownership layers

The frontend owns terminal interaction only. Each Codex conversation thread
maps one-to-one to one RPNH main-session Registry. `MainSession` owns that
durable thread, turn ordering, committed conversation history, selected
execution profile, and Registry-native links to child tasks. A frontend
container may hold multiple threads, but it does not merge their Registries.

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
retry policy, or create a second accounting path. Codex, managed DSH, and a
future OpenCode integration therefore reuse the same selected profile, input
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

## Repository boundary

This repository contains the reusable harness, schemas, frontend compatibility,
provider configuration tools, and deterministic offline tests. It does not ship
project-specific workflows, experiment data, historical Registries, result
bundles, user profiles, or credentials.

The current runtime supports Linux only, including WSL2. Native Windows and
macOS are unsupported because execution depends on Unix domain sockets, Linux
`/proc`, `fcntl` locking, and POSIX signals.
