# Opt-in API contract visibility comparison

[中文](CONFIGURATION_COMPARISON_ZH.md) | [Example](../README.md)

`api-contract-visibility-v1` is a new, explicit, **native-host-only** configuration
condition. The default is `baseline`. Historical results are not changed or
re-scored. This condition changes no task prompt, public task contract, initial
world, clock, rubric, task order, cohort, dependency pin, budget or scoring rule.
It preserves the immutable initial-world capture and the existing null/Trello
argument compatibility mappings.

## What changes

After the pinned upstream `api_search` finishes ranking, a task-independent
metadata overlay adjusts only three recognized endpoints already in the result:

- Salesforce Account PATCH documents `HealthStatus` / `health_status`, `Tier` /
  `tier`, and `Priority` / `priority`. The underlying fields are `Optional[str]`;
  descriptive conventions are not enforced enums. The mutator ignores null and
  empty strings, so these cannot clear a field. Account create is unchanged
- Sheets `spreadsheets.get` truthfully documents metadata and sheet listings
  only. `includeGridData` and `ranges` are accepted but ignored by the pinned
  implementation. Neither retrieves cells or filters the sheet list
- Sheets `values.get` explains the explicit per-sheet URL/range path. The actor
  chooses needed sheets; a bare A1 range addresses the first sheet. The adapter
  never reads all sheets automatically

URLs, endpoint IDs, methods, result order/count, and unknown endpoints are
preserved. The search index and BM25 corpus are untouched. Consequently, adding
request documentation does **not** make a new keyword searchable or guarantee
that the relevant endpoint will be discovered. The overlay contains no task
IDs, hidden assertions, exact task-specific answers or policy tags.

Before dispatch, a narrow `api_fetch` preflight can return fixed, sanitized
parameter guidance for missing/non-string method or URL, malformed URL,
unexpected top-level arguments, query JSON/object shape, unsupported argument
container types, or a non-object body for the verified Account PATCH path.
The response identifies `stage: pre_dispatch`, `upstream_dispatched: false`,
and `effect_status: not_started`. It never echoes input values or exception
text. These returned errors do not count as upstream dispatches. They can be
corrected in a new call; repeating the same call identity only replays its
retained response.

This is not a universal endpoint validator. Unknown routes, value validation,
scalar/array body contracts, and text-query compatibility remain upstream-owned.
Malformed body JSON is left upstream-owned to preserve text-body exceptions.
Once dispatch starts, exceptions are **not** converted to no-effect parameter
errors: the failure, possibly mutated world checkpoint, and deduplication state
remain retained. No automatic replay is added. Upstream JSON error responses
also remain unchanged; they are not evidence of rollback or no side effect.

The native harness explanation identifies its legacy `raw_result` envelope
field as the **actor-visible** result under this condition. Baseline retains its
original exact-upstream explanation. The public task messages remain unchanged.
DSH is rejected for this condition before host launch; it needs a separately
implemented and verified condition path.

## Select and freeze a new condition

Use the already authorized profile and unchanged pinned upstream described in
the [runbook](RUNBOOK.md). Choose a fresh private work directory. For example:

```bash
rpnh-ab prepare --upstream "$AB_UPSTREAM" --work "$AB_NEW_WORK" \
  --profile "$RPNH_PROFILE" --host native --split simple \
  --configuration-condition api-contract-visibility-v1
rpnh-ab doctor --upstream "$AB_UPSTREAM" --work "$AB_NEW_WORK" \
  --profile "$RPNH_PROFILE" --split simple \
  --configuration-condition api-contract-visibility-v1
```

The same flag works with the existing `--cohort` selection. It does not create a
new cohort or change its IDs/order. Do not run provider experiments without
separate authorization. `accept-host` and `run` inherit the frozen condition;
there is no launch-time switch that can override it. The ordinary installed-host
acceptance remains required and must be produced for this condition. Component
tests alone are not this acceptance.

The condition ID and hash are retained in the frozen plan, conditions,
benchmark specification, attempts, score evidence, standalone summaries, prepared
launch, and acceptance identity. Summaries exclude attempts with a different
configuration identity; the scorer itself and its inputs/results are unchanged.
The hash covers the unchanged-pin identity, endpoint catalog, overlay payload,
and relevant implementation sources. The benchmark hash therefore separates
baseline and comparison acceptance. `run` checks the doctor condition, frozen
plan and current condition hashes; acceptance also checks attempt identities.
A changed catalog/overlay needs a new work root and matching acceptance.

## Evidence and limitations

For every comparison `api_search` call,
`api_search_metadata_events.jsonl` preserves exact raw upstream and actor-visible
strings, their JSON-canonical string hashes, changed endpoint IDs, request
sequence/hash and condition identity. The file is included in the allowlisted
return export with an exported-byte inventory. Original arguments remain in
private tool events. The plugin/Registry witness matches the actor-visible
string, not the raw upstream string. Registration does not prove that a later
model request consumed it.

`pre_dispatch_rejected` events are separate from `dispatch_started` and
`dispatch_finished`. The evidence crosscheck counts returned preflight
rejections separately from upstream returns, without making them business
successes. No scorer, historical score or outcome classification is relaxed.

Validation for this implementation is synthetic/offline only. The new tests
exercise overlay preservation, condition identity, sanitized preflight,
post-dispatch partial failure, export evidence and baseline behavior. A separate
stdlib-only check ran selected verified pinned source functions against
synthetic objects: non-enum Account write/readback, metadata-only multi-sheet
listing, explicit per-tab cell reads and bare-A1 first-sheet behavior. That is
not complete upstream-package, Pydantic, provider, socket, Registry or
installed-host integration acceptance, and it proves no historical score gain.
