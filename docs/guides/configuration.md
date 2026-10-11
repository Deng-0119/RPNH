---
name: rpnh-configuration-reference
description: "Configure every supported operator policy and distinguish fixed protocol boundaries."
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: en
  counterpart: configuration_ZH.md
  revision: "2026-10-11.1"
  status: source-reviewed-pre-release
---

[English](configuration.md) | [中文](configuration_ZH.md)

# Configuration and limits reference

RPNH has three user-owned configuration surfaces. Edit the provider/model
catalog for model and standard Agent runtime policy, a plugin catalog for
installed native operations, and workflow/module declarations for application
topology and business budgets. Command-line options select a session, run or
view; they do not create a second policy stack. Generated adapter and execution
profiles are derived files and must not be hand-edited.

## Files, selectors and precedence

| Surface | Default | Override or selector |
|---|---|---|
| Provider/model catalog | `~/.config/rpnh/provider_models.json` | `RPNH_PROVIDER_CATALOG`, or `rpnh config build --catalog PATH` |
| Generated profiles | `~/.config/rpnh/profiles/` | build `--output-root`; `RPNH_PROFILE_DIR` points to its `execution` directory |
| Saved active profile | `~/.config/rpnh/config.json` | `RPNH_CONFIG` |
| Current execution selection | saved active profile | `--execution` first, then `RPNH_EXECUTION_CONFIG`, then the saved profile |
| Native plugin catalog | no plugins | `rpnh plugins --config PATH` or `RPNH_PLUGIN_CONFIG` |
| Codex binary lookup | `codex` | `RPNH_CODEX_BIN`; the exact supported version is still enforced |

Use `rpnh config init`, edit only the catalog, then run `rpnh config build`,
`rpnh config build --check`, `rpnh config use ...`, and `rpnh config show`.
`show` includes the selected provider/model, recovery policy, context policy and
runtime policy without credential values. `--save-default` stores an explicit
`--execution` selection. `ready` means only that named credential variables are
present; it is not a network, model, authorization or billing check.
For a model that declares efforts, `rpnh config use ... --effort EFFORT`
selects one of that model's configured values; omission uses its configured
default.

`rpnh doctor` (also `rpnh config doctor`) reports the winning execution
selection source: the explicit `--execution` option, the nonempty
`RPNH_EXECUTION_CONFIG` variable, or the saved profile selection. An empty
variable is absent; an invalid winning selection fails without falling back
to a lower-priority selector. The source check prints only this fixed label,
not the variable value, path, endpoint, adapter arguments or credentials.
It describes the execution selector only, not the origin of every effective
setting. Doctor remains read-only and offline, and does not start interactive
setup. `rpnh config show` continues to describe the saved selection even when
an environment override would select a different profile for execution.
For both external-provider and local-process adapters, doctor validates against
the selected execution profile's exact `reasoning_effort`, including a declared
literal `none`; an absent effort remains `null`. It does not substitute the
catalog default or accept an adapter whose effort differs from the selection.

## Provider and exact-model fields

The catalog root contains the fixed `schema_version` and a `providers` array.
Each provider entry declares `provider` (the stable user-owned provider name),
`display_name` (presentation text), and a nonempty `models` array. Every model
item accepts the following public fields. Fields without a default are required
unless marked optional.

| Field | Meaning and validation |
|---|---|
| `profile` | Unique lowercase file-safe profile name. |
| `model_condition` | Exact outbound model identifier; RPNH does not apply a model allowlist. |
| `reasoning_efforts` | Optional object with a nonempty unique `supported` list and a `default` member of that list. Values are user-owned exact-model metadata; RPNH has no built-in vendor table. |
| `adapter` | Exactly one `external_provider` or `local_process` route described below. |
| `timeout_seconds` | Positive per-formal-request deadline. Interactive setup starts at 900. |
| `max_output_tokens` | Positive output-token request cap. Interactive setup starts at 8192. |
| `max_response_bytes` | Positive returned-envelope byte cap. Interactive setup starts at 16777216 (16 MiB). |
| `context_window_tokens` | Optional exact-model capacity. Declaring it enables proactive pressure compaction. |
| `context_compaction_retained_tokens` | Optional recent complete-message tail; if a window is present this must be smaller than it. |
| `runtime` | Optional complete runtime-policy object. Omission uses the defaults in the next section; generated profiles always contain the resolved object. |

An `external_provider` adapter has these fields:

| Field | Meaning |
|---|---|
| `adapter_kind` | Fixed `external_provider`. |
| `route_id` | File-safe route identity within this generated adapter. |
| `backend` | User-owned backend/provenance label. |
| `protocol` | Fixed `openai_chat_completions/v1`. |
| `endpoint` | Exact chat-completions endpoint; remote routes require HTTPS and plain HTTP is loopback-only. |
| `credential` | `null`, or an object with `environment`, `header`, and `prefix`. This stores the variable name and header recipe, never the secret. |
| `headers` | Static non-secret HTTP header mapping; may be empty. |
| `recovery` | Complete same-route recovery object documented below. |

A `local_process` adapter has these fields:

| Field | Meaning |
|---|---|
| `adapter_kind` | Fixed `local_process`. |
| `argv` | Nonempty command/argument vector used for a formal request. |
| `probe_argv` | Nonempty local readiness command vector. |
| `env` | Static environment additions for the subprocess; may be empty. |
| `inherit_env` | Explicit environment-variable names copied from the launcher; may be empty. |

`{model}` in either local command vector is replaced with the exact selected
model. If `reasoning_efforts` is declared, formal `argv` must contain
`{reasoning_effort}`; it is replaced in each generated immutable variant.
Local processes and probes may still have external effects or cost.

## Standard Agent runtime policy

Put a complete `runtime` object on a model entry to change these values without
editing Python. The generated execution profile records the resolved values,
so a run and its resume retain one exact policy.

| Field | Default | Effect |
|---|---:|---|
| `max_turns_per_node` | 12 | Model/tool-turn budget for each single-agent stage or workflow node. This also bounds the corresponding module bucket. Set it to `null` to record explicit unmetered authority with no artificial node, tool-turn or cumulative task call ceiling. |
| `max_parallel_nodes` | 4 | Maximum concurrently in-flight workflow nodes. A single-agent task remains serial. |
| `main_history_message_limit` | 20 | Number of most recent complete role/body messages included in a new main-Designer prompt. Full committed history remains in Registry. |
| `context_pressure_trigger_ratio` | 0.90 | Fraction of a declared context window that triggers proactive compaction; output-token reservation can trigger earlier. |
| `context_tool_output_byte_limit` | 10000 | Maximum bytes for each model-visible tool-result projection, including the immediately following turn and compaction history; full Registry evidence is unchanged and remains page-readable. Minimum 128. |
| `workspace.timeout_seconds` | 120 | Maximum firing-private workspace command time. A tool request may choose a lower value. |
| `workspace.memory_bytes` | 4294967296 | Address-space bound for the workspace process. |
| `workspace.process_limit` | 64 | Workspace process-count bound. |
| `workspace.source_size_bytes` | 16777216 | Maximum submitted workspace script/source size. |
| `workspace.input_size_bytes` | 16777216 | Maximum workspace file publication/input size. |

Example fragment:

```json
{
  "timeout_seconds": 900,
  "max_output_tokens": 8192,
  "max_response_bytes": 16777216,
  "context_window_tokens": 131072,
  "context_compaction_retained_tokens": 20000,
  "runtime": {
    "max_turns_per_node": 12,
    "max_parallel_nodes": 4,
    "main_history_message_limit": 20,
    "context_pressure_trigger_ratio": 0.9,
    "context_tool_output_byte_limit": 10000,
    "workspace": {
      "timeout_seconds": 120,
      "memory_bytes": 4294967296,
      "process_limit": 64,
      "source_size_bytes": 16777216,
      "input_size_bytes": 16777216
    }
  }
}
```

## Recovery, attempts and idempotency

External-provider transport recovery is user-configurable under
`adapter.recovery`:

| Field | Constraint | Meaning |
|---|---:|---|
| `strategy` | fixed `bounded_same_route_health_probe/v1` | No provider/model/credential-route fallback. |
| `max_probe_attempts` | 1–3 | Maximum probes in one health sequence; the first success stops the sequence. |
| `probe_timeout_budget_seconds` | positive | Shared wall-clock budget for that probe sequence. |
| `max_probe_success_formal_failure_cycles` | 1–3 | Maximum probe-success/formal-failure recovery cycles for one logical call. |

These numbers count provider-private physical recovery calls, not Agent turns.
They do not make an unknown submission safe to replay. Logical call IDs,
physical attempt IDs, Registry idempotency keys, ordinary-turn single-attempt
semantics, the bounded compaction retry, and unknown-submission reconciliation
are protocol invariants. They intentionally have no general “retry N times”
knob: changing them would change evidence and exactly-once boundaries, not tune
performance. The probe prompt (`Reply with READY.`) and its eight-token cap are
also fixed protocol details.

The `provider_attempt_limit` value embedded in compiled Agent operations is a
fixed Registry admission envelope, not the number of automatic retries. It
allows only the attempt shapes defined by the current Agent protocol (one
ordinary call, or the separately bounded compaction path). It is therefore not
copied into the catalog. The only public transport-recovery controls are the
three external-provider recovery fields above.

## Workflow and plugin declarations

The Designer or a module author configures graph policy in the declaration,
not in global source constants. `max_rework_cycles` is a nonnegative workflow
graph budget; zero disables feedback/rework. The selected runtime
`max_turns_per_node` determines each node's attempt bucket, while
`max_parallel_nodes` determines scheduler concurrency. Explicit per-node model
profiles and tool allowlists remain part of the graph. A `null` turn limit does
not disable owner stop, provider retry handling, workspace resource bounds,
context-window handling or Registry/PetriNet admission and settlement.

A native `PluginOperation` declares `timeout_seconds` (1–7200, default 60) and
`max_result_bytes` (1–16777216, SDK default 1048576). DSH-managed tools use the
same 16777216-byte ceiling. Choose each operation's declared bound from its
legal output rather than copying either the ceiling or default.
The plugin catalog root has fixed `schema_version: rpnh/plugins/v1` and a
`plugins` array. Each selected item has exactly `name`, `entry_point`, `version`,
`config`, and `environment`. `config` must satisfy that installed plugin's own
published schema; `environment` contains variable names only. Plugin secrets
remain in the process environment. No catalog means no selected plugins.

Designer workflow graph JSON declares `nodes`, `arcs`, `ingress`, `egress`, and
`max_rework_cycles`. Node configuration includes `node_id`, `instruction`,
input/output ports and `execution`; execution can select a supported `role`, a
sorted tool allowlist, a configured `profile_id`, or one native `plugin`
selector where the schema permits it. Ports use `port_id` and `artifact_id`;
arcs use `arc_id`, source/target endpoints and `kind`. These are graph-specific
declarations, while the profile's runtime object supplies shared turn and
parallelism limits. See [declarations](../reference/declarations.md) for the
generic module surface.

## Sessions, frontends and viewer options

`rpnh` supports `--execution`, `--save-default`, `--session-dir`, `--resume`,
`--prompt`, and `--frontend {auto,codex,basic,opencode}`. Basic task controls,
stop/resume/rollback and workflow commands are listed in the [usage guide](usage.md).
Codex `cwd` is presentation metadata, not a workspace-policy selector.

Terminal net projection supports `--format`, `--show-resources`,
`--resources-only`, `--node` and `--output`. Browser view supports `--no-open`,
literal loopback `--host`, `--port 0..65535`, `--show-resources`,
`--max-checkpoints` (default 2048) and `--max-firings` (default 2000). The last
two bounds can be raised for a known large historical run without modifying
source. The history HTTP endpoint pages 1–100 checkpoints per request.

Host selectors use the same viewer: Codex accepts `--root`, `--thread-id` and
either `--turn` or `--task-id`; DSH accepts `--root`, `--session-id` and either
`--turn` or `--request-id`. Both accept `--describe`, `--json`, `--view`,
`--presentation`, `--no-open`, `--show-resources`, `--port`, and the two
dashboard bounds.

## Fixed compatibility and safety boundaries

The following are deliberately not operator policy: schema/protocol versions,
Registry identity shapes, socket-path and HTTP status validity, credential size
validation, IPC command framing, Codex/OpenCode/DSH version pins, OpenCode's
256 KiB request and 4 MiB response/snapshot limits, DSH's 68 MiB frame and
16 MiB managed-tool ceiling, the workspace command's 1 MiB captured-output
contract, and the current fixed Codex permission projection
(`danger-full-access`, `approvalPolicy=never`, user reviewer). OpenCode
permission/workspace reply APIs remain unavailable. Read-only projection also
uses a small fixed consistency retry when a Registry advances during a read;
this is not an execution retry and never changes Registry. These values define
a tested wire or authority contract; changing source is an implementation
change requiring compatibility tests, not a supported configuration action.

See [models](models.md) for setup examples, [customization](customization.md)
for declarations/plugins, and [viewer](viewer.md) for evidence boundaries.

## Codex local-process bridge: declared context and diagnostics

For an explicitly selected registered profile, align Registry profile metadata
`context_window_tokens` with the bridge argument `--model-context-window`,
which is forwarded as the CLI configuration key `model_context_window`. A
CLI-only declaration does not populate Registry context-pressure policy.
These are locally declared capacities, not officially verified model limits.

Before CLI dispatch the bridge estimates the final rendered request budget as
`ceil((prompt_UTF8_bytes + endpoint_instructions_UTF8_bytes + output_schema_file_bytes) / 4) + configured_output_reserve`.
The prompt includes rendered history and tool schemas.
`configured_output_reserve` comes from the bridge argument
`--model-max-output-tokens`; it is the canonical output reserve, not an enforced
CLI output cap. The historical repair condition used context 272000 and output
reserve 128000; these are condition-specific values, not defaults or fixed
constants in the general budget formula. The byte estimate is
not an exact tokenizer count or a provider guarantee. Oversize estimates are
refused as `context_budget_exceeded`; unknown submission bookkeeping remains
conservative and never grants permission to replay.

Unsupported event/item diagnostics retain only bounded ASCII protocol type
identifiers (at most 64 characters) in private stderr audit metadata. No raw
payload is retained by this diagnostic and no tool allowlist is relaxed.
Registry failure codes do not retain those types; not every failure path is
guaranteed a request-budget summary. A later run without the old unsupported
item does not identify or fix its unknown root cause. Capacity rejection is not
proof of context overflow. The [public result](../../examples/automationbench/PUBLIC_RESULTS_20261006.md)
keeps configuration, canonical usage and provider-unknown fields separate.
