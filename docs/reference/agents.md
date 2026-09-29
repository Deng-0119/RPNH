---
name: rpnh-agent-model-reference
description: "Explain session control, AgentLoop ports, model configuration and physical-call accounting."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: agents_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](agents.md) | [中文](agents_ZH.md)

# Session control, AgentLoop and model services

## Session and task components
`MainSession` coordinates conversation turns and child links, rather than copying child execution state. The CLI uses `turn`, `launch`, `reconcile_active_turn`, `resume_paused_turn` and `rollback_paused_turn`. `MainSessionPaused` is a checkpoint state requiring explicit continuation; `MainSessionExecutionFailed` is not a successful final answer. User-facing behavior is documented in [usage](../guides/usage.md); these host methods are not a separate unversioned stable SDK.

Task control selects one independent task ID and exposes `list`, `get`, `status`, `result`, `message`, `stop`, `checkpoints`, `resume`, `reopen` and `net` through the existing task/owner path. Reading a status or sending a queued message is not completion. Child launch/resume/reopen can create processes and execute providers/tools; stop is checkpoint-aware. A workflow message requires an explicit target. Lifecycle/identity errors should not be converted into an implicit new task.

## AgentLoop is an optional execution component
The generic harness dispatches an operation; it does not import a specific AgentLoop, provider or workflow implementation. AgentLoop composes turn/action execution, context/compaction, tools, resource waits, workspace, timing and bounded delegation. Its implementation modules now separate those responsibilities while retaining the owner-side Registry gateway.

The `AgentLoopLLMPort` declares `request_agent_turn_v1(execution, loop, catalog, *, idempotency_key)` returning `CompletedAgentLLMInvocation`, and `compact_agent_context_v1(..., trigger_reason, force, idempotency_key)` returning a completed compaction. These are provider-capable boundaries, not pure formatting functions. Compaction may consume model calls and must retain the applicable accounting and evidence.

`AgentLoopRegistryPort` groups prepare/start/record/complete/failure/wait operations. For example, `record_agent_llm_turn_v1(loop, attempt, response_bytes, *, idempotency_key)` returns an updated snapshot and either a normal turn record or a length-interruption record. A truncated response is not automatically a completed turn. Implementations must commit transactionally, enforce optimistic revision/exact-head checks, and return rehydrated committed records.

Workspace execution and task completion are separate facts. A started workspace
action with `status=timed_out` or a nonzero `exit_code` remains an immutable
`ACTION_APPLIED` observation. It is neither erased by a later success nor itself
proof that the assignment failed. The model or declared business operation must
decide whether correction, verification, or an accurate diagnostic result is
appropriate. Registry does not require a retry, a final non-retryable marker, or
a reserved script comment before `complete_interaction`; those are runtime or
business-policy decisions rather than structural integrity. Completion still
requires registered semantic products and preserves every preceding action as
immutable evidence.

Owner interruption is a different boundary. It closes the in-progress action,
persists the clean workspace checkpoint and leaves the run `stopped_by_owner`;
`resume` continues that same Registry from the checkpoint without replaying the
invalidated action. A fully settled terminal run is immutable and is not an
ordinary resume target. `reopen CHECKPOINT [:: REASON]` is the separate
owner-authorized operation for selecting any committed checkpoint in that run;
an optional reason becomes a Registry-backed resumed-agent instruction. It appends a new
execution generation in the same Registry/run, clones that cut's live Petri
tokens, restores its exact workspace revisions and preserves the current
attempt high-water marks. Prior tokens, terminal evidence and files remain
immutable history. Immutable objects/events are not edited; the workspace-head
projection advances by compare-and-swap and the live tree is restored from the
selected revision. Reopening an already terminal cut is a no-model-call closure
of the new generation.

Methods intentionally remain declared directly on the Protocols because gateways enumerate `vars(protocol)`. Replacing them with a visually equivalent inherited method hierarchy can break compatibility. Forward annotation binding is another reason not to import the whole runtime merely to build a documentation index.

## Configuration and transport are separate components

| Interface | Use and return | Limits/effects |
|---|---|---|
| `discover_profiles(directory=None)` | Tuple of `ExecutionProfile` objects from execution files and manifest | Empty absent directory is allowed; mismatched manifest raises `ValueError`; not a live check |
| `load_profile(path)` | Explicit profile without requiring catalog membership | Parses/validates actual selection and adapter data |
| `profile_for_path(selected, directory=None)` | Resolve catalog profile or explicit profile | Does not silently choose a different model |
| `ExecutionProfile.as_public_dict(selected=False, environ=None)` | Display identity, credential variable names, missing variables and `ready` | Never interpret ready as endpoint availability |
| `build_provider_catalog(...)` | Generate profiles from the canonical catalog | File writes in normal mode; `check` verifies generated state; no live provider call |

The exact CLI arguments for catalog generation are in [models](../guides/models.md). Generated execution limits (`timeout_seconds`, `max_output_tokens`, `max_response_bytes`, optional `context_window_tokens`, and optional `context_compaction_retained_tokens`) are not a comprehensive monetary budget. A declared context window enables proactive compaction and is shown as non-secret profile provenance. The provider adapter executes the selected transport; the Registry provider ledger records physical attempts and recovery evidence. Their responsibilities must not be collapsed into an invisible transport retry.

## Failures, testing and stability
Test malformed responses, length interruption, schema mismatch, stale references, cancelled/waiting actions, workspace failure evidence, owner-stop resume and exact provider/model preservation. Context text and tool results do not gain Registry authority merely by appearing in a prompt. The `OptionalAgentLoopRegistryService` owner-side gateway and private commit helpers are implementation surfaces, not plugin-author permission to write Registry internals.

Source map: `cpn/rpnh/main_session.py`, `session_access.py`, `frontend_application.py`, `task_control.py`, `agent_tasks.py`, `agent_workflows.py`; `cpn/components/agent_loop/{ports,service,turn_execution,turn_records,action_execution,action_records,context,compaction,delegation,resource_wait,workspace}.py`; `cpn/rpnh/user_config.py`, `provider_setup.py`, `cpn/llm_adapters`, `cpn/rpnh/registry/_provider_calls`.
