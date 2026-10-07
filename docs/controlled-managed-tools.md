---
name: rpnh-controlled-managed-tools
description: Explicit managed result readers, scheduling and isolated tool programs.
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: controlled-managed-tools.zh.md
  revision: "2026-10-08.1"
---

[English](controlled-managed-tools.md) | [中文](controlled-managed-tools.zh.md)

# Controlled managed tools

These interfaces are explicit selections. The normal toolkit does not add
`read_managed_output`, `run_tool_program` or `read_tool_program_output` by default.
Omitting `AgentTaskSpec.managed_tool_policy` preserves existing serial managed
execution; omitting `tool_program_policy` preserves the ordinary toolkit.
Existing permissions, effect admission, model limits and stop conditions still
apply. Selecting a reader or scheduler grants no additional business authority.

## Exact managed output (P1)

Expose `read_managed_output` in the workflow node's declared tool catalog when
managed structured results must remain reachable. This reader addresses an
earlier, settled, returned `agent_action/v3` in the same loop. The existing
`read_action_output` v2 stdout/stderr reader remains a separate interface.

Copy the exact `agent_action_ref` and `terminal_receipt_ref` from the first result
envelope; pass those fields to `read_managed_output`, optionally with
`offset_chars` (default 0) and `max_bytes` (default 10000, visible maximum 10000).
The action ref has `entity_type`, `logical_id`, `version_id`; the receipt ref has
`resource_id`, `resource_version_id`. Do not substitute a display name or latest
version. The reader validates same-loop history, state and receipt identity.

A `managed_output_page/v1` contains those refs, `reader`, `content`,
`offset_chars`, `next_offset_chars`, `total_chars` and `truncated`. Content is a
fragment of deterministic serialized JSON; join successive fragments before
parsing the whole output. Follow `next_offset_chars` until null. Offsets count
characters; `max_bytes` covers the complete UTF-8 JSON page, including escaped
content and locators. A budget that cannot fit the minimum envelope and progress
fails explicitly. Reading never reruns a handler or changes business state.

The initial bounded result and compaction/replacement history retain a usable
locator when the reader is exposed. Returned, included in a later submitted
request and readable are separate facts. An unavailable reader is not advertised
as callable; an oversized result without that reader fails rather than silently
losing its middle. Failed or unknown managed actions are not relabelled returned.

## Read-only evidence and independent read HOST (P2/P4)

```bash
rpnh net --run RUN_DIR --result-evidence
rpnh net --read-host-config READ_HOST_JSON --preflight
rpnh net --read-host-config READ_HOST_JSON --view --no-open
rpnh plugins --config PLUGIN_JSON inspect
```

`--result-evidence` projects registered action/return references, later request
material and submission evidence, observed follow-up refs, and existing stop
facts. Request projections distinguish `full`, `bounded`, `reference`, `absent`,
`unavailable` and `not_checked`; a registered request alone is not evidence of
submission. No next request and unavailable retained material remain distinct.
`semantic_use` and `decision_influence` remain unknown; business readback and
benchmark score are not inferred. Runtime terminal is separate from business
success. The stop projection retains `llm_turn_cap` versus `task_model_call_cap`,
effective budgets, actual counters, recorded stop stage and exact loop/event refs.
Missing facts stay unavailable or not recorded; inspection does not resume,
retry, rescore or create a second counter.

`--run` and `--read-host-config` are mutually exclusive. Preflight and evidence
modes reject viewer/resource-output options. `--preflight` describes the selected
read session, performs a final access recheck and closes it; it does not query
objects, start a listener, issue grants or check execution readiness.

The owner-controlled JSON uses `schema_version: rpnh/registry_read_host_config/v1`,
`purpose`, `sources`, and optional `limits`/`source_set`. Each source selects
`source_ref`, `access_path`, absolute `registry_root`, `binding_generation` and
an existing `observer_context`. Its task ref and purpose must match the selected
source. This selects existing authority, not principals or grants supplied by a
browser. Use a current-user-owned regular config with mode 0400 or 0600 and
trusted non-symlink absolute paths. The independent read HOST opens canonical
sources read-only and rechecks access; unavailable sources follow the existing
disclosure contract. See [Viewer](guides/viewer.md).

`plugins inspect` reads installed metadata and explicit selection without loading
factories. Installed, selected, declaration loaded, HOST bound, and run-ready are
separate states. Missing inert metadata does not authorize factory execution.
`plugins list`/`check` load trusted selected factories; they have different scope.

## Portable plugin author checks (P5)

Export the native example using `rpnh examples export --example native_plugin
--output ABSENT_DIRECTORY`. Explicitly build/install its wheel in the intended
RPNH environment before using the declaration selfcheck. From the exported root:

```bash
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-a.json
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-b.json --previous declaration-a.json
python -I examples/native_plugin/selfcheck_terminal.py --operation demo/add --run-dir ABSENT_RUN_DIR --result terminal.json
```

Declaration checking executes trusted installed factories, checks descriptors,
schemas, dependencies, resources and actual import paths, and does not bind a
HOST. The separate terminal check executes one explicitly authorized pure
operation using the normal plugin runtime and checks genuine output/terminal
facts and zero model calls. IPC refusal is blocked. Neither check installs a
plugin. For version B update distribution, module, entry point, plugin/version
and selection consistently; review descriptor changes and explicitly rebind.
Old saved bindings do not automatically become B. See
[author versions](../examples/native_plugin/AUTHOR_VERSIONS.md).

## Keep readback conditions distinct

| Example | Explicit new condition | Preserved conditions |
| --- | --- | --- |
| AutomationBench | `api-contract-visibility-readback-v2` | `baseline`, `api-contract-visibility-v1` |
| Office | `office-public-discovery-readback-v2` | no comparison selection, `office-public-discovery-workflow-v1` |

Select via each example's `--configuration-condition`. The new conditions expose
`read_managed_output` through the actual node catalogs and record their own
condition/catalog/implementation identities. Office retains the public discovery
and policy-evidence write gate for its supported public campaign bundles; the
reader does not relax it. Separate registered return, verifiable subsequent
submitted-request inclusion and observed state/readback. Neither an actor claim
nor readback proves semantic use. Preserve historical condition identities and
results; these options make no acceptance-rate or score claim. See
[AutomationBench](../examples/automationbench/README.md) and
[Office](../examples/harnessaudit_office/README.md).

## Stage 1/2: declared managed scheduling

The HOST supplies explicit `managed_bindings` and sets
`AgentTaskSpec.managed_tool_policy` to a complete policy document:

```python
from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy

managed_tool_policy = ManagedSchedulerPolicy(
    policy_id="managed_pure_parallel/v1", max_in_flight=2,
).identity()
```

Stage 1 admits only managed protocol-v2 pure operations. Stage 2 uses
`managed_conflict_domains/v1` and `conflict_domains`, constructed from
`ManagedConflictDomain(registration_key, effect, reads=(), writes=(), unknown=False)`.
Use exact registered keys and trusted HOST domain IDs, not model hints or tool
names. Pure declarations carry no external domains; known external reads declare
reads without writes; known external writes declare writes. Conflicting writes
and reads serialize, unrelated domains can overlap. Missing declarations or
unknown domains form an exclusive barrier. Effects still need normal admission.

`max_in_flight` is backed by one run-shared `ManagedRunCapacity`, including
simultaneous firings. Capacity is reserved before owner admission. Prepare and
finish cross the same owner gateway; workers and waits run outside it. Each
started invocation retains genuine started/terminal receipts. Original model
response ordinal and call identity determine action/result message order;
completion order does not create identities. Every per-item outcome, including
known errors and not-started items, enters one whole-turn settlement and one loop
successor. A known failure is not a batch-wide fail-fast.

Stop/cancellation prevents new starts and drains admitted observations. An
unknown outcome blocks further admission for that operation and retains actual
receipts while already running siblings settle. Do not automatically retry or
reexecute unknown work; use the existing reconciliation authority. Observation
of an existing invocation does not create another execution.

## Stage 3: declared isolated program API

The profile is `linux_isolated_python/v1` in
`cpn.plugins.controlled_script`. The program has a confined Linux process/root,
bounded scratch, no host files, credentials, network or owner/Registry objects.
Business access goes through the registered HOST broker. Unsupported isolation
or setup failure is explicit; there is no unrestricted execution fallback.

The HOST selects the shared scheduler policy and the exact managed names:

```python
import dataclasses
from cpn.plugins.controlled_script import IsolatedProgramBudget

# Supply real node-bound managed names; this is not a default tool list.
def program_policy(selectedmanagednames):
    return {
        "profile_id": "linux_isolated_python/v1",
        "tools": sorted(set(selectedmanagednames)),
        "budget": dataclasses.asdict(IsolatedProgramBudget()),
    }
```

Set `AgentTaskSpec.tool_program_policy` to this document. `tools` must be nonempty,
sorted and unique, and bound on every program node. Nodes explicitly expose both
`run_tool_program` and `read_tool_program_output`. The spec requires
`managed_tool_policy`; program `max_parallel` must not exceed its shared capacity.
The budget is the whole dataclass document, not a partial object. Its fields are
`wall_seconds`, `cpu_seconds`, `memory_bytes`, `process_limit`, `max_output_bytes`,
`max_frame_bytes`, `max_calls`, `max_parallel`, `max_source_bytes`, `scratch_bytes`.
The profile supports one program process; resource limits do not authorize effects.

`run_tool_program` accepts exactly `source` (nonempty Python text) and `arguments`
(a JSON object). The program SDK supplies `arguments`, `tools`, `ToolCallError`
and `result`. Use explicit stable logical keys; child identities are derived from
program identity plus key, not completion order. Only selected managed names are
callable, with schema validation and per-child effect/conflict admission.

SDK entry and calling conventions are described below; source is one program,
not a new host runtime or a script-owned execution queue.

The source-isolated SDK is synchronous. The runtime executes module source;
it does not automatically call `main`, await a coroutine or drive an event loop.
`tools.call(key, name, args)` returns the child value or raises
`ToolCallError` with `.code`. `tools.parallel(calls)` accepts a list of
`{"key": ..., "name": ..., "args": ...}` objects and returns replies in input
order, each with `key`, `ok` and `value` or `error_code`. It overlaps admitted
business executions up to the declared capacity; it does not make the SDK async.
`tools.read_result(locator, offset_chars=0, max_bytes=10000)` synchronously returns
a bounded child page. `result(value)` publishes a JSON-serializable final value.
Call it explicitly; defining a function alone executes no business call.

For a selected pure `demo_add` binding whose input is `{a, b}`, an example source
is below. Replace the illustrative name/input with your exact node-bound name
and schema. Send this source with `arguments={"a": 2, "b": 3}`:

```python
def main(arguments):
    replies = tools.parallel([
        {"key": "left", "name": "demo_add", "args": arguments},
        {"key": "right", "name": "demo_add", "args": arguments},
    ])
    return {"replies": replies}

result(main(arguments))
```

A sequential dependency can use `value = tools.call("first", "demo_add", arguments)`
inside `main` and pass the returned value into a later call after checking its
shape. `result(tools.call("first", "demo_add", arguments))` is also a valid
module-level source. Never use `async main` or `await` for these SDK methods.

A large successful child reply becomes `agent_tool_program_child_output_page/v1`,
with `program_invocation_ref`, `program_call_ref`, `terminal_receipt_ref`, `reader`,
`content`, `offset_chars`, `next_offset_chars`, `total_chars`, `truncated`.
`tools.read_result` accepts either that complete page or its three exact refs.
Follow the continuation to reconstruct serialized business output. It is a
separate read-only broker frame, not an allowlisted business tool, arbitrary
receipt reader or new invocation. Reply-frame overhead counts toward the budget.
If the budget cannot fit a child locator page, the broker returns the known
`program_result_budget_too_small` error. The successful child and its complete
output remain registered and accessible through the closed parent reader.

The parent `tool_program_result/v1` metadata has exactly `kind`,
`program_invocation_ref`, `output_resource_ref`, `status`, `call_count`, `reader`,
`agent_action_ref`. Status preserves `returned`, `failed`, `cancelled` or
`outcome_unknown`; action settlement is an observation, not a success claim.
Typed children and the result resource remain durable even after partial failure
or cancellation. Rejected invalid child arguments count toward the call budget
without worker execution; failed/unknown executed children retain their receipts.

For a closed parent in an earlier turn of the same loop, call
`read_tool_program_output` with exact `agent_action_ref` (`agent_action/v2`),
`output_resource_ref`, optional `offset_chars` (0) and `max_bytes` (10000, visible
maximum 10000). The `tool_program_output_page/v1` preserves those locators,
`reader`, `content`, offsets/continuation/total/truncation and `source_status`.
All four closed statuses are readable after every accepted child is terminal;
started-only or unfinished programs are not. `source_status` preserves the raw
failed/cancelled/unknown status. Read durable successful children instead of
rerunning a failed program to retrieve their IDs. Unknown retains the existing
reconciliation block, refuses new calls and has no transparent retry or resume.

API sources: [task specification](../cpn/rpnh/agent_tasks.py),
[tool catalog](../cpn/components/agent_loop/tool_catalog.py),
[managed scheduler](../cpn/plugins/managed_scheduler.py),
[program owner broker](../cpn/components/agent_loop/program_execution.py),
[isolated runtime](../cpn/plugins/controlled_script.py).
