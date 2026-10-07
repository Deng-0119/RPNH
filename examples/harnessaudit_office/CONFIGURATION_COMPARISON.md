# Opt-in public discovery and workflow comparison

[中文](CONFIGURATION_COMPARISON_ZH.md) | [Configuration](CONFIGURATION.md)

`office-public-discovery-workflow-v1` is a separate application condition. It
requires explicit selection; the default graph, catalog, configuration and
published results stay unchanged. This implementation has synthetic offline
coverage only. No executor/judge provider calls, native Registry/AF_UNIX execution,
or installed-entry-point acceptance were performed. No score improvement is claimed.

## Verified public contract and scope

The pinned [Office catalog](https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/tools/office.yaml)
has 15 tools. Its KB search requires an exact stored query key and has no key
discovery. The pinned [backend](https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/banks/office.py)
returns only the first matching article ordered by article_id. Its audience
argument is not an enforced permission check. This condition does not pretend
that baseline audience authorization exists.

v1 supports off-t1, off-t3, off-t4, off-t5 and off-t6 only. Their public goals
request policy review for the requested action bundle. The original public goals
and role text remain unchanged. This condition declares a conservative
bundle-level prerequisite: external writes in those bundles need retrieved
policy evidence. This is not a universal rule for every Office operation.
The legacy off-t2 probe is rejected before configuration-driven run setup;
its public dependency is outside this version's reviewed scope.

## Discovery contract

`discover_knowledge_queries(topic, audience="", offset=0, limit=10)` reads only
KB metadata relations and returns article_id, title, audience and query_key.
All literal topic words must occur in the article title, case-insensitively.
There is no stemming, semantic expansion or guessed fallback. A nonempty topic
is required; page size is 1–20; no match stays no match.

Audience is an optional exact metadata filter, not an ACL. The discovery selects
the same first reachable article as the original search before applying filters,
so it never advertises an unreachable second article under a shared key. It does
not return article bodies, business resource tables, secrets, hidden task fields,
scorer rules or hidden role/argument allowlists. The original search still fetches
policy text with its original semantics. Metadata alone does not satisfy the gate.

## Workflow and missing policy

```text
hub_plan → evidence specialists → hub_coordinate → execution specialists
         → hub_review → verification specialists → hub_finalize
```

Every cross-role edge is hub-mediated, and every hub join needs all preceding
phase reports. Each original public specialist gets evidence, execution and
verification stages. No hidden role/tool allowlist is inferred. Evidence, hub
and verification stages bind read/pure effects only; external writes appear
only at execute_* stages. Each role must still follow its original public
responsibility. Phase/effect filtering does not prove role-specific entitlement.

Comparison-only write declarations add required `policy_evidence`, a string
containing JSON of this form (placeholders are not injected task answers):

```json
{"status":"ready","reason":"Public policy basis for this requested action.","references":[{"query_key":"a-discovered-key","article_id":"a-returned-id"}]}
```

The condition dispatcher requires a nonempty reason and references matching
actual earlier successful KB returns in the same isolated backend run. Missing,
malformed, empty, unavailable or no-match policy evidence returns a typed
blocked_policy result with write_dispatched=false before original business
dispatch. The envelope is stripped once; original arguments and business
semantics stay intact. Underlying dispatch exceptions are not relabelled as
safe pre-dispatch failures. Outcome-unknown writes must not be replayed.

An execution stage may activate with a blocked report to publish an honest
blocked result. A completed report is not a write permit: the dispatcher blocks
empty policy evidence. The graph enforces ordering, not prose truth. The gate
proves retrieval/reference availability and declared readiness only. An
irrelevant retrieved policy could still pass. It does not prove relevance,
truth, completeness, authority or model consumption, and is not a new kernel
authorization mechanism.

## Readback and evidence

Execution reports return through the hub before public read-only state checks
using actual returned identifiers. No suitable public readback means unverified.
Finalization has no write tools. Prompts require a later turn to consume results
before reporting; offline tests do not prove that a real model follows this.

comparison_evidence.json separates tool-return observations, exporter-proved
registered model inputs, verification-phase read candidates and host snapshot
availability. Snapshots are never injected into the model. A phase read is not
automatically a correct field check. The sidecar does not infer business success
or validate final-answer truth. Existing capture diagnostics keep return and
consumption separate. The pure completion_evidence helper classifies explicitly
supplied facts; it is not an independent oracle or replacement scorer.

## Select and freeze a declaration without execution

Add this option to the existing configure command:

```bash
--configuration-condition office-public-discovery-workflow-v1
```

The generated condition_id and configuration_condition must match. Omitting the
option preserves the original default configuration. Unknown versions and
inconsistent identities fail. --authorize still only records route selection;
this option does not make a model call.

After prepare saves the original public task projection:

```bash
python examples/harnessaudit_office/example.py plan-condition \
  --config comparison.json --public-input public_input.json --output plan.json
```

This builds public inputs, graph and bindings without a provider, Registry,
backend or account probe; profile files need not exist. Readiness remains a
profile-declaration check and explicitly marks comparison runtime acceptance
not_performed instead of reusing historical baseline acceptance.

An explicit run propagates the condition through adapter, driver request,
plugin configuration, observations and report identity. New ha_comparison_*
installed entry points select comparison factories; default ha_* definitions
stay unchanged. A future separately authorized native run requires those entry
points to be installed. That startup chain has not been tested here.

## Identity, quotas and scoring

Plans/protocols record discovery/workflow/prompt/write-gate versions; original
public input/catalog hashes; derived public input/catalog, prompt, graph and
binding hashes; and exact implementation-file/bundle SHA-256 values. Launch also
records the resolved native plugin catalog digest. These identify declarations,
not actual provider prompt transport. Reprojection fails if saved labels,
public fields, graph or implementation no longer match; retain matching source.

With S specialists there are 4 + 3S nodes (7, 10 or 13). Null limits remain null.
Finite model calls retain the original per-node floor division. The manifest
shows each allowance, effective total and unallocated remainder: 23 calls/10
nodes gives 2 per node, 20 effective, remainder 3. A cap below node count fails.
This is not a shared budget.

Recovery, reprojection and scoring preserve condition identity. A baseline
scoring configuration cannot silently score the new condition. Pinned scorer
algorithms and task/world fixtures remain unchanged. Discovery calls and extra
phase/role calls are additional observable actions: the collector, crosswalk and
export retain them with original roles. They are not filtered away for scores.
The original evaluator may penalize or not support the new tool/envelope/graph,
even if business state improves. Future results belong to this separate
condition, cannot be pooled with history, and do not by themselves establish a
matched improvement. Published result files remain untouched.

## Offline coverage and unrun work

Tests use synthetic public tasks, a fresh in-memory KB, fake dispatch, typed
graph construction and pure evidence classification. They cover distractors,
no-match/paging, unreachable second articles, missing-policy no-write behavior,
actual return references, post-dispatch exceptions, hub prerequisites, phase
bindings, evidence distinctions, condition identity, CLI, scope and budgets.

```bash
PYTHONPATH=examples/harnessaudit_office/src:. python -m pytest examples/harnessaudit_office/tests -q
```

These tests do not start sockets, Registry processes or real models. Installed
plugin loading, native end-to-end execution, model compliance, field-by-field
readback quality, original judge response and performance improvement remain
unverified. No full repository/release-wide suite is claimed.
