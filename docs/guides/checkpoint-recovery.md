---
name: rpnh-checkpoint-recovery
description: "Continue an existing main session or child task without replacing its Registry."
metadata:
  document-kind: how-to
  audience: operator-and-agent
  language: en
  counterpart: checkpoint-recovery_ZH.md
  revision: "2026-09-29.1"
  status: current
  basis: "current MainSession and TaskControl commands"
---

[English](checkpoint-recovery.md) | [中文](checkpoint-recovery_ZH.md)

# Continue an existing task from a checkpoint

This guide is deliberately independent of any example. Give it to an operator
agent when the user says “continue the existing task.” The objective is to
continue the same Registry, not recreate the prompt or launch a replacement.

## Information the agent must identify

- the owning `SESSION_DIR`;
- the child `TASK_ID`, if the work is an independent task/workflow; and
- whether the user wants the latest owner-stopped cut or a specifically named
  older checkpoint.

A task ID is scoped by its owning main session. An unrelated fresh session
cannot safely discover or adopt the child from the ID alone. If the location is
unknown, search only user-authorized roots for the session/task manifest; do
not copy or edit a Registry to make it visible.

## Safe latest-checkpoint continuation

Open the exact owner session from any shell or host context. Use Basic for the
RPNH task-control commands:

```bash
: "${SESSION_DIR:?Set the existing owning session directory}"
rpnh --frontend basic --resume "$SESSION_DIR"
```

At the RPNH prompt:

```text
/tasks
/task TASK_ID status
/task TASK_ID checkpoints
```

Then choose from Registry state, not process appearance:

- `terminal`: use `/task TASK_ID result`; do not resume.
- `RUNNING` or Registry `running`: monitor it; do not start a second writer.
- `stopped_by_owner`: `/task TASK_ID resume` continues the latest committed
  owner-stopped cut.
- `submission_unknown` or another unresolved external effect: do not
  automatically retransmit. Preserve the attempt and follow the provider/
  troubleshooting evidence path.

After `resume`, repeat `status`, then require registered terminal evidence and
`result`. A frontend exit, worker exit or visible answer is not completion.

## User-selected older checkpoint

List exact IDs, stop/reconcile a running owner, and use only an ID returned for
that task:

```text
/task TASK_ID checkpoints
/task TASK_ID reopen CHECKPOINT :: Explain the correction or continuation goal.
```

`reopen` appends a new execution generation in the same Registry. It restores
that cut's Petri marking and settled workspace; it does not delete later
history, copy the Registry, or create a replacement task. External effects
after the selected cut are not automatically undone.

## Interrupted main turn

Resume the owning `SESSION_DIR`. With main selected, `/resume` continues its
paused turn; `/rollback` returns the conversation to the prior completed turn.
Rollback does not delete or rewind child Registries. If the main turn merely
launched a child, inspect that child separately with `/tasks` and `/task`.

See [usage](usage.md) for full command semantics and
[troubleshooting](troubleshooting.md) for unknown outcomes and owner conflicts.
