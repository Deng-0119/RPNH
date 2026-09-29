---
name: rpnh-design
description: "Explain record authority, Petri admission, settlement and extension boundaries."
metadata:
  document-kind: explanation
  audience: operator-and-developer
  language: en
  counterpart: design_ZH.md
  revision: "2026-09-29.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](design.md) | [中文](design_ZH.md)

# Architecture and execution principles

## Why two authorities cooperate
Registry is authoritative for records, identity, versions and committed evidence. The typed Petri net expresses admission and execution structure: what may start, which exact inputs/resources are required, and how an outcome moves the marking. They are complementary parts of one closed execution design, not interchangeable loggers.

For one run, reason about a committed state as `(G, M, R)`: adopted graph `G`, marking `M`, and Registry history `R`. This is explanatory notation, not a new runtime API. An executor's return is only candidate material. Publication and Success must establish the matching result, resources, marking/workspace successor and evidence in the existing authority before downstream execution may rely on them.

## Intake to Finalization
User input enters the main-session authority; a direct answer path or Designer decision may lead to a run with its own declaration and exact configuration. A declaration lowers through host registration, compiles into a candidate, and is published/adopted by the owner. Admission binds an exact transition, input/resource claims, identities and budget. Start precedes physical dispatch. Returned products are validated and registered, Success settles the firing, and a registered terminal rule establishes terminal evidence/final result.

Intake and Finalization belong to the same complete lifecycle. A formatter, UI or external agent cannot declare completion merely because it has text. Waiting, execution blocking, owner stop and terminal handoff are distinct from Success. A lack of enabled work does not by itself prove a globally live or successfully completed process.

## State ownership and concurrency
A run has one writer/owner path. `RunOwner` supplies the existing authority, `OwnerEventLoop` serializes owner work, and `Harness` can have multiple physical operations in flight without creating multiple Registry writers. Completion callbacks must re-enter the owner path. The EventStore commit boundary controls optimistic heads, writer epoch, idempotency and publication order; domain decomposition does not create independent transactions in each helper.

`TeamNetMarking` remains the state owner for tokens, epoch and active claims even though implementation is split into `_marking`. Similarly `_ResourceServiceKernel` and the EventStore facade retain their state ownership while private domain modules organize implementation. Copying a mutable state object into an adapter to simplify integration breaks this boundary.

## Main, independent children and delegated leaves
The main-session Registry owns the conversation and links to independent children. Each independent child has its own Registry/run, Petri net and owner channel; the main retains exact identity references and relative links, not a second copy of child events. UI focus and UI lifetime do not determine child lifetime. Main rollback therefore cannot delete or rewind a child's execution.

A `delegate_leaf` is different: it is parent-owned, bounded, and returns to an exact parent action. Treating it as an independent task—or treating every independent child as a nested leaf—loses the intended authority boundary.

## Resources, workspace and business security
Resource identity/version and acknowledged access matter, not just file paths or unversioned contents. Firing workspaces expose a private view of the registered revision. Successful settlement publishes the allowed successor; incomplete writes do not become committed shared history. This is not automatic compensation for arbitrary external side effects.

The harness governs **structural safety**: explicit admission, ownership, declared effects and evidence. The business supplies access policy and data-disclosure rules, which can enter through declared guards, Inspector places/tokens or registered operations. The core should not hard-code a particular organization's authorization policy. Conversely, business policy is not an excuse to bypass the structural path. Trusted plugins and host code still require review; neither Registry nor a Petri diagram is an OS sandbox.

## Structural validation and runtime policy
Registry schemas and commit validators enforce facts that must remain true when
history is replayed: typed identity, exact references, ownership, append-only
ordering, Petri token/cardinality rules, transport milestones and matching
terminal evidence. They must not freeze a runtime decision into historical
integrity. Retry eligibility and count, remediation after a failed tool action,
business acceptance and whether an answer is good enough belong to the selected
provider/operation/user policy. A failure record therefore stores what happened
and its transport disposition; the current invocation policy decides whether a
fresh attempt may be admitted. Historical validity does not require a stored
“final non-retryable” assertion. The unchanged v1 schemas accept that field
when reading older append-only events, but current writers and decisions ignore
it.

Point reads must use the Registry's indexed identity, type, aggregate,
transaction or idempotency queries. Materializing the complete event history is
reserved for a deliberately whole-history audit or a recovery rule whose
meaning depends on all later writer facts. This keeps the same evidence boundary
without making long-lived Registries progressively slower merely to resolve one
authority or publication event.

## Observation and adapter boundaries
A net view is a read-only projection of configured or Registry-current structure. It must preserve its source mode, exact IDs and uncertainty; it cannot mint tokens, infer terminal evidence from UI state or hide a real execution step to improve presentation. Translation changes explanatory labels, not protocol IDs, field names, model identities or run data.

Codex reuses presentation. DSH reuses upper-layer host capabilities while routing admitted effects through the same backend. A genuine integration does not run an external agent independently and asynchronously import its logs. Native plugins bind explicit versioned operations/resources and do not create another core.

## Tradeoffs, extension and non-goals
Explicit admission and settlement add work compared with a transcript-only loop, but permit checking exact resource/result lineage and recovery boundaries. Composition is preferred over hidden special cases. Extending schemas, protocols, dispatch or provider behavior requires focused evidence; moving code between modules is not permission to change semantics.

The framework does not guarantee model correctness, global liveness, automatic business-policy correctness, universal transport compatibility or successful recovery from every unknown external effect. These remain distinct claims to test. Sources: `module.py`, `compiler.py`, `run.py`, `harness.py`, `registry/_event_store/commit.py`, `workspace_settlement.py`, `inspection.py`. Continue with [declaration reference](../reference/declarations.md) and [runtime/Registry reference](../reference/runtime-registry.md).
