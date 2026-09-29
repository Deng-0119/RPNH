---
name: rpnh-use-and-resume
description: "Operate main sessions, independent tasks and read-only net projections."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: usage_ZH.md
  revision: "2026-09-28.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](usage.md) | [中文](usage_ZH.md)

# Sessions, tasks, workflows and observation

## Before execution
Install the appropriate surface and select an authorized exact profile. **Conversation, `/agent`, `/workflow`, `/resume` and `--prompt` can execute models/tools.** They are not installation smoke tests. Use a new session directory for a new session, retain returned task IDs, and keep run data private.

Start the explicit basic frontend with `rpnh --frontend basic`. Supply `--execution PATH` to select a file or `--session-dir PATH` for an absent new session directory. `--resume SESSION_DIR` is mutually exclusive with `--session-dir`. `--prompt TEXT` runs a main turn, not a harmless echo.
For a fresh session, omitting `--execution` uses `RPNH_EXECUTION_CONFIG` or the
saved user default. For `--resume`, omitting it instead uses the exact profile
persisted by that session. An explicit `--execution` on resume is an equality
assertion and cannot replace the persisted provider/exact-model identity.

Basic, Codex and OpenCode use the same direct `SESSION_DIR` contract. After the
current frontend exits, reopen that exact root with any of the three frontends;
there is one MainSession Registry, not one copied session per frontend. A shared
owner lease rejects concurrent writable frontends. Reopening, listing and
history projection do not call a model or compensate a child launch.

## Main session and independent task controls
The following are **basic-frontend** commands. They are not additions to stock Codex's slash-command registry.

| Command | Meaning |
|---|---|
| `/tasks`, `/current` | List independent children; inspect focus |
| `/agent PROMPT` | Create an independent single-agent task and select it |
| `/workflow PROMPT` | Ask the main Designer for a graph workflow; invalid design does not count as a launched graph |
| `/switch ID`, `/switch main` | Change focus without stopping or restarting children |
| `/task ID status`, `/task ID result` | Read process/Registry status or registered result |
| `/task ID message TEXT` | Queue input for a single-agent task |
| `/task ID message TARGET :: TEXT` | Explicit workflow recipient |
| `/task ID stop`, `/task ID resume` | Request checkpoint stop; continue the current owner-stopped cut |
| `/task ID checkpoints` | List exact committed checkpoint versions in the same run |
| `/task ID reopen CHECKPOINT [:: REASON]` | Append a new execution generation from the selected checkpoint and make optional owner guidance visible to resumed agents; may execute models/tools |
| `/quit` | Leave the main frontend; independent children may continue |

Short `/status`, `/result`, `/net`, `/message`, `/stop` commands act on the selected child. Plain text goes to main or to the selected single-agent; selected workflows require `TARGET :: TEXT`. A parent-owned `delegate_leaf` is an internal action, not an independently switchable task.

`resume` and `reopen` are intentionally different. `resume` continues the
current `stopped_by_owner` checkpoint. `reopen` accepts an exact ID returned by
`checkpoints`, keeps the same Registry/run, and restores that cut's Petri
marking and workspace as a new append-only execution generation. It does not
delete later history or create a replacement task. Stop a currently running
child before selecting an older cut. An already-terminal selected cut closes
the new generation without another model call. If a worker has exited with one
physical call recorded as `submission_unknown`, `reopen` is also the explicit
owner decision to abandon that unresolved firing and start a fresh attempt from
the selected safe cut. The unknown attempt remains immutable evidence and is
never automatically retransmitted.

When remediation is needed, append ` :: REASON`. The owner reason is stored in
the reopen authorization and projected as a system instruction to agents in
that execution generation. Omitting it retains the neutral default reason.

## Interrupted main turn
Reopen with `rpnh --frontend basic --resume SESSION_DIR`. Without an explicit
`--execution`, this command uses the session's persisted exact profile, not the
current environment/default profile. Opening observes the authoritative state;
it does not settle a terminal child or compensate a committed launch. With main
selected, `/resume` explicitly commits already-terminal evidence or continues a
paused turn; `/rollback` returns the main conversation to its prior completed
turn. Rollback retains the paused child Registry; it neither rewinds independent
children nor compensates external writes. `/rollback` on a selected child is
rejected. An active unreconciled turn must be resolved before accepting new input.

## Inspect existing runs without execution
Use an actual run directory, not a transcript or arbitrary session index:

```bash
: "${RUN_DIR:?Set an existing run directory}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --format json
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

The last command starts a local read-only viewer server, normally on `127.0.0.1` with an allocated port; it is not a model call, but it is a process/network-listener effect. Keep it loopback-bound. `--view` cannot combine with `--resources-only`, `--node`, `--output`, or non-default `--format`. `--output PATH` is a file-writing projection option, not a Registry write.

Resource places are hidden by default and appear only if actually declared. An empty resource-only projection is valid for a run without resource places. `/task ID net view` or selected `/net view` reaches the same observer. A readable graph, `enabled_transitions`, or a displayed answer does not establish global liveness or terminal success.

## Expected evidence and recovery
Success is registered terminal evidence plus the registered result in the child's authority, not “process exited”, a cached UI answer or an empty work queue. Keep main and child roots together when making a controlled consistent backup; relative Registry links are not copied event stores. Do not start a second writer to repair display issues. Use [troubleshooting](troubleshooting.md) for owner conflict, drift or unknown outcomes.

Sources: `cpn/rpnh_cli.py:_task_command`, `_run_task_action`, `_net_command`; `cpn/rpnh/main_session.py`, `task_control.py`, `inspection.py`, `registry/main_thread.py`.
