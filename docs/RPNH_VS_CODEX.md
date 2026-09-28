---
name: rpnh-versus-codex
description: "Explain the boundary between RPNH authority and the Codex frontend."
metadata:
  document-kind: concept
  audience: operator-and-developer
  language: en
  counterpart: RPNH_VS_CODEX_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# RPNH versus Codex

[中文](RPNH_VS_CODEX_ZH.md)

RPNH is not a reskinned Codex client and is not another OpenAI model client.
The current implementation reuses the fixed `codex-cli 0.155.0` terminal UI,
but connects it to an RPNH-owned backend. Codex supplies one frontend; RPNH
supplies the harness, execution model, and evidence authority.

| Dimension | Running `codex` directly | Running `rpnh` |
|---|---|---|
| Terminal UI | Codex TUI | The same pinned TUI, plus `--frontend basic` |
| Backend state | Codex/OpenAI thread and execution state | One RPNH main-session Registry per conversation thread; independent child Registries and typed PetriNet state |
| Provider/model | Codex account and configuration | User-owned RPNH profile with provider, route, and exact model |
| Ordinary input | Codex thread/turn | A supervised single-agent RPNH main turn |
| Independent work | Codex threads, tools, or agent facilities | Process-isolated single-agent or graph-workflow task objects |
| Task switching | Codex frontend semantics | `/switch` changes basic-CLI focus without stopping background tasks |
| Workflow | Codex backend behavior | Designer-authored typed graph lowered to a PetriNet |
| Subagent | Codex implementation-defined | Parent-owned, bounded `delegate_leaf` whose result returns to one action |
| Workspace | Codex workspace/tool semantics | Private firing views and Registry-versioned files published at settlement |
| Scheduling | Codex backend internals | Petri enablement, admission, firing, resources, and settlement |
| Completion | Visible model/session output | Registry terminal evidence plus final-result index |
| Interruption/resume | Codex session behavior | Registry checkpoint, owner stop, exact-lineage resume |
| Inspection | Codex logs/session | Read-only task/net/token/firing/provider/resource projections |

## Four execution identities

RPNH keeps these identities separate because they have different ownership and
recovery behavior:

| Identity | Created by | Ownership | Independently switchable |
|---|---|---|---|
| Main turn | Ordinary main-session input | Main-session thread and turn Registry | Return with `/switch main` |
| Single-agent task | `/agent PROMPT` or a validated main decision | Separate process, Registry, PetriNet, and owner channel | Yes, by task ID |
| Graph workflow | `/workflow PROMPT` or a validated Designer decision | Separate process and graph/PetriNet task authority | Yes, by task ID |
| Delegated leaf | Parent agent's `delegate_leaf` tool | One parent action and its Registry invocation lineage | No; result returns to parent |

A workflow is not a preinstalled project pipeline. The Designer declares a new
graph for the current task; the harness validates and lowers that graph. A
single-agent task is still a real one-transition PetriNet task, not an alias for
the main thread.

The Registry boundary follows execution ownership. One Codex conversation
thread maps to one direct main-session Registry root. After Codex exits, Basic
or OpenCode can reopen that exact root without copying its Registry; a shared
owner lease prevents concurrent writable frontends. Every independently switchable agent
or workflow has its own Registry. The main Registry contains only a relative
link/index, the optional originating turn, and exact task/run references once
the child Registry is readable; it does not absorb the child's event history,
tokens, workspace, or final-result authority.

## User-visible behavior differences

1. `rpnh` does not treat Codex model selection as a second source of truth.
   Codex `/model` lists and updates profiles generated from the RPNH provider
   catalog. The same selection is available through `rpnh config use PROFILE`
   or `rpnh config use PROVIDER MODEL`. A running turn cannot change route.
2. The Codex TUI exposes only protocol methods RPNH implements. Permission and
   sandbox values are compatibility projections over the fixed RPNH execution
   boundary. RPNH-specific `/agent`, `/workflow`, `/switch`, `/task`, and `/net`
   commands belong to `rpnh --frontend basic`; unsupported Codex-only commands
   are not advertised as harness capabilities.
3. The main session is logically one agent. For graph work, that agent acts as
   Designer and declares ingress, egress, typed ports, and arcs. Dependency arcs
   form the initial entry-to-exit DAG. Explicit feedback arcs lower to separate
   rework transitions and consume a shared `max_rework_cycles` budget.
4. The workflow's initial ingress resource token preserves the exact original
   user task, followed by a labeled supplemental Designer brief. The brief
   cannot replace or summarize away the original task; node instructions own
   execution responsibilities. An ordinary untyped cycle is rejected because
   it would mix first-generation and feedback activation without an explicit
   output route or stopping bound.
5. Workspace files become shared state only through successful Registry
   settlement. Later agents see the registered revision and can build on it;
   interrupted, unsettled writes are not promoted. This preserves provenance
   across sequential nodes and safely isolates concurrent firing views.
6. A dead worker process is not success. Task completion requires Registry
   terminal evidence and a final result. The frontend and task-control commands
   report process status and Registry status separately.
7. Main-session history stores only complete Registry-terminal answers. On
   interruption, already settled actions remain durable while the incomplete
   semantic action is invalidated and the turn remains paused at its checkpoint.
   `rpnh --resume SESSION_DIR --frontend basic` reconstructs that paused state
   without executing it. The user then chooses `/resume` for the same child
   Registry lineage or `/rollback` to return the main conversation to its prior
   completed turn. Rollback never deletes or rewinds the child Registry.
8. Independent tasks behave like background agents. Leaving or interrupting the
   main session does not stop them. `/task ID stop` requests a checkpointed owner
   stop; `/task ID resume` continues the same eligible Registry lineage.
9. `rpnh net` and the browser viewer read an existing run without writer
   authority. Ordinary graph nodes are visible by default; actual resource nodes
   appear only through `--show-resources`, `--resources-only`, or the page toggle.
10. Provider credentials are not delegated to the Codex TUI and are never stored
    as values in the RPNH catalog. An external route reads only the environment
    variable named by its user-owned profile.

## Current boundaries

- Runtime support is Linux-only, including WSL2; native Windows and macOS are
  not supported.
- The Codex frontend supports exactly `codex-cli 0.155.0`; compatibility with
  other versions is not implied.
- RPNH persists thread state inside the current RPNH session and supports
  `thread/resume`; it does not import Codex-owned history or resume a paused
  firing automatically. Main-turn `/resume` and `/rollback` are explicit basic
  frontend controls and require a clean Registry checkpoint.
- The Codex TUI primarily provides main-session input/output. Complete task
  controls and the custom slash commands are in `rpnh --frontend basic` and
  `rpnh net`.
- RPNH does not launch a Codex app-server or Codex backend. Model calls use only
  the selected RPNH execution profile.
- RPNH ships an empty provider/model catalog and no project-specific workflow.
  Users own both provider configuration and task-specific graph design.
- This standalone repository is authoritative for the reusable harness; a
  historical experiment workspace is not a runtime dependency.
