---
name: rpnh-troubleshoot
description: "Diagnose configuration, ownership, lifecycle and adapter errors without destroying evidence."
metadata:
  document-kind: troubleshooting
  audience: operator-and-developer
  language: en
  counterpart: troubleshooting_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](troubleshooting.md) | [中文](troubleshooting_ZH.md)

# Troubleshooting and safe recovery

## Start with the failing boundary
Record the distribution/source revision, frontend, exact profile identity, command, exit status and the smallest redacted error. Preserve the affected Registry/run and catalog privately. First distinguish installation, configuration, admission, physical execution, settlement and presentation. A failure at one layer is not evidence that another layer succeeded.

| Symptom | First check | Recovery and data impact |
|---|---|---|
| `rpnh` missing or wrong package imported | Active environment, installed entry point, `cpn.__file__` outside checkout | Reinstall the approved artifact in a clean environment; do not delete run data |
| `init`, `doctor`, `plugins`, `examples`, or frontend `auto` unrecognized | Whether `rpnh` and imported `cpn` come from the same current unified installation | Reinstall the reviewed wheel/source; do not mix an older executable with current Python modules |
| Codex version rejection | Exactly `codex-cli 0.155.0`, correct binary | Use the pinned client or explicit basic; this does not change the selected model |
| Codex resume reports `persisted_profile_unavailable` | The exact profile recorded by the omitted thread is still installed and selectable | Restore that exact user-owned profile, then resume again; RPNH does not switch models and does not rewrite the thread Registry |
| No profile / manifest mismatch | Catalog, execution directory, generated manifest, overrides | Back up catalog and regenerate; do not hand-edit execution files independently |
| Missing credential variable / `ready` false | Variable name and current process environment, not secret value | Set the approved variable outside version control, recheck offline; no probe yet |
| `ready` true but call fails | Credential validity, exact route/protocol/model, authorized call evidence | Separate configuration from live reachability; no silent fallback |
| Schema or registered identity mismatch | Input schema version and implementation/profile revision | Use compatible code/config; do not relabel historical records to force acceptance |
| Existing owner / writer fencing conflict | The original owner process and exact run root | Stop/reconcile through the owner; never clear locks or start a competing writer blindly |
| Process gone, result absent | Registry terminal evidence and interrupted/unknown state | Do not report success; explicit resume only after checking the effect boundary |
| Workspace command timed out or returned nonzero, but later work exists | Immutable action record, subsequent tool/model decisions and registered semantic result | Keep the failed action as evidence. Registry does not require a blind retry or reserved “final failure” marker; the declared operation decides whether to correct, diagnose or fail |
| Need to continue from an older task cut | `/task ID checkpoints`, current owner state and external effects after the selected cut | Stop/reconcile the owner, then use `reopen CHECKPOINT [:: REASON]`; do not edit marking, copy a Registry or reuse physical attempt identities |
| Empty resources-only net | Actual resource declarations | Valid when none exist; do not synthesize resources for display |
| DSH rejects provider/model, input or response budget | Offline/configured mode, exact selection and overall 2 MiB frame | Keep the same route and exact model, but build an explicit user-owned DSH profile whose response bound leaves frame headroom; configured tools also require an explicit pure native-plugin allowlist |
| DSH rejects a managed tool catalog | Absolute `--plugin-config`, paired `--managed-tool NAME=PLUGIN/OPERATION`, unique lowercase names, object input schema and pure effect | Correct the selected declaration/config; do not disable guards or mount arbitrary installed tools |
| `managed DSH tool result limit exceeds 64 KiB` | The selected operation's declared `max_result_bytes`, not the size of one observed result | Declare a bound derived from the operation's legal output domain. The current demo operations use 1 KiB after bounding their inputs; do not copy either the DSH ceiling or SDK default without analyzing the operation. |
| Managed tool arguments or next-response budget rejected | Selected input schema; declarations, history, worst-case tool result and next response | Correct the request or approved bounds before execution; a tiny expected result does not override the declared upper bound |
| Correlated `isError: true` tool result | Durable managed-plugin failure receipt | The tool failed; the model may handle that failure. Do not relabel it as a successful value or blindly repeat the tool |
| DSH resume refuses a stale execution | Exact completion event, original registration/profile/catalog, writer history and effect/workspace boundaries | Use the original selection and allowlist; absence of eligible proof is not permission to rerun the provider/tool |
| Empty DSH session returns `failed` instead of `idle` | Whether the latest turn is actually committed | This is a failure, not successful completion; preserve history instead of creating a fake terminal |
| Plugin cannot resolve / changed digest | Installed entry point, declared version, handler bytes, config/resources | Reinstall matching package or create a separately reviewed new run; do not bypass resume identity checks |

Detailed managed-tool and completion arguments and support limits are in
[adapters](adapters.md) and the [DSH guide](dsh.md).

## Stop, resume and unknown effects
A requested stop is not proof that the owner reached a safe checkpoint. Confirm the resulting Registry state. Main `/rollback` only changes main-thread conversation authority and retains child evidence; it does not undo files already settled, external writes or provider billing.

A timeout or dropped transport can leave submission/effect status unknown. Inspect the recorded physical-call and operation state before deciding whether another call is safe. Never infer that “no response” means “no effect”. Do not add a generic retry around the CLI, plugin handler, DSH bridge or owner command to make a test pass.

For configured DSH, a clean stop before model-attempt registration can resume under the original explicit selection. After exact output validation, `registered_operation_completion_recorded/v1` can support settlement of one stale running firing without another HOST/provider/tool invocation. It does not support arbitrary cuts before that event, conflicting/later-writer evidence, or automatic settlement with declared HOST effects. A workspace-bound completion additionally requires its exact immutable candidate and `map_ready` subordinate execution checkpoint; recovery never rescans mutable workspace state. See [runtime recovery](../reference/runtime-registry.md).

A complete raw provider response and an operation-completion event are different milestones. `submission_unknown` or partial bytes are failure diagnostics, not partial success or proof of remote non-execution. Provider-authoritative queries/idempotency are only optional capabilities of a concrete supporting provider contract; do not assume them for a generic compatible endpoint.

For an active main turn, reopen the same session and use its supported reconciliation path. An owner-stopped child resumes its own Registry, not a transcript reconstruction. `reopen` is a separate owner-authorized generation from one exact committed checkpoint; it preserves later history and cannot undo external effects. Keep exact selected provider/model unchanged unless an explicitly reviewed operation authorizes otherwise.

## Logs and issue reports
Report expected/actual behavior, code SHA, platform/Python, exact command with sensitive values replaced, and whether the failure occurred before admission, after physical dispatch or during settlement. Include only a reviewed minimal reproduction. Token text, original input, prompts, provider response bodies, intermediate results, workspace files and even tool-call logs may be sensitive. A filename or hash alone is not a privacy guarantee.

Never post credentials, raw Registry databases, full shell environment, signed download URLs or private endpoints. Preserve original evidence in a controlled private location; make redacted copies rather than rewriting authoritative history. Permission/security failures should use the maintainer's private reporting channel when one is assigned; no public issue address is claimed here.

## Validation and non-goals
Repeat the smallest relevant deterministic test before attempting an authorized live reproduction. Link/build checks do not validate runtime recovery. The framework governs structural admission and records; it is not a universal business authorization policy, an OS sandbox, or an automatic external-effect compensation service.

Source boundaries: `cpn/rpnh/user_config.py`, `control_server.py`, `task_control.py`, `registry/checkpoint_reentry.py`, `harness.py`, `registry/event_store.py`, `registry/firing_recovery.py`; `cpn/plugins/{api,catalog,managed_tools}.py`, `cpn/dsh/backend.py`, `integrations/dsh/src/{agent,app}.ts`. [Architecture](../architecture/design.md) explains why these boundaries are separate.
