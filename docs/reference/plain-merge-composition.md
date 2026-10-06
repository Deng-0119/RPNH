---
name: rpnh-plain-merge-composition
description: "Explicit plain merge results in author Branch and Assembly/v2 workflows."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-merge-composition_ZH.md
  revision: "2026-10-05.2"
  status: stage-b3-author-chain-candidate
---

[English](plain-merge-composition.md) | [中文](plain-merge-composition_ZH.md)

# Plain merge, author Branch and Assembly/v2

Use `plain_merge_assembly_schema_data()` to explicitly compose the existing
merge-result and Assembly/v2 catalogs. Branch/v1 is already inherited from the
author-material catalog. Duplicate schemas and object types must agree exactly;
existing `plain_merge_result_schema_data()` and `graph_assembly_schema_data()`
retain their original inventories. This opt-in does not change a default HOST,
schema authority or runtime configuration.

First produce and fully read a [plain merge result](plain-merge-result.md) M
with exact ordered parents `[L,R]` and explicit caller decisions. The gateway's
existing `advance_author_branch` can advance a Branch at L to this direct child
using the exact expected Branch version, head L and stream sequence. A stale
expectation rejects the Branch publication; M remains an immutable author
result. There is no reset or implicit choice of the current head.

`current_branch` and `read_branch_version` return canonical Branch descriptors.
Take the selected descriptor's exact `head_revision_ref` into
`validate_closed_revision` for full merge proof reconstruction. A saved
historical Branch version continues to identify its saved head. These are
separate explicit calls; they do not claim a joint snapshot or that the current
head cannot change between calls. Replaying the original result command returns
M and does not move the Branch.

## Assembly consumes the exact merge proof

Pass M's exact revision ref as an `AssemblyMemberV2` to `AssemblyAuthorV2`.
Choose the completion member and primary terminal element ID explicitly, with
the existing `shared_exact` budget policy and `same_run_candidate` deployment
intent. This is author compilation and publication only; deployment intent is
not runtime adoption.

The existing `plain_closed_v1` member resolution fixes M's definition, element
map, boundary map and HOST requirement refs. The full consumer traverses M's
explicit merge marker, exact analysis, caller decisions, complete command and
actual material bytes. `validate_assembly_revision` then reconstructs the member
Module, generated revision, compiled inventory and lowering origins. A readable
M descriptor alone cannot supply an Assembly member proof.

The Assembly-generated paired revision still uses the original closed-author
protocol and strict `ValidatedClosedRevision` result type. A member merge proof
does not permit replacing that generated revision with a different subclass or
relaxing the existing pair, carrier, constraint or source-origin checks.

## One private validation context per read cut

Assembly/v1 and Assembly/v2 internal parent, member and generated-proof readers
pass the active outer path through the same Registry read transaction. The
current Assembly identity stays active until its entire proof completes.
Sibling members share the outer path without retaining completed siblings as
a global seen set. Reusing one exact member twice therefore remains valid.

Producer preflight and final validation each create their own context in their
own transaction. The final check re-reads its selected parent and member proof;
it does not reuse an earlier transaction's validation as final authority. No
public reader is reopened inside the recursive member path and no global
mutable proof cache is introduced.

## Ordinary single-parent editing after a merge

The existing `ClosedModuleAuthor.publish` accepts the full merge proof M as its
exact `parent_ref`. Supply the new closed Module, complete stable element IDs
and any explicit `copy_sources`. An ordinary edit E keeps the original
`closed_author_command/v1` marker and deterministic identity rule, with exactly
one parent `[M]`. It does not acquire the merge-result marker or two parents.

Retained elements keep their IDs. A copied element needs a new ID and an exact
same-kind element from M; its `copied_from.revision_ref` points to M, not to an
earlier merge input. Marking a retained ID as a copy remains invalid. Reading E
fully validates M through the existing recursive parent path.

The author can advance Branch M→E using the same explicit three expectations.
The saved historical Branch version still selects M; current and historical
heads can each be fully read by their exact refs. Assembly/v2 can consume E as a
`plain_closed_v1` member and rebuild the generated pair, inventory and origins.
Replaying M's original merge command returns M without moving the current
Branch head back or adding new facts.

This B3 candidate tests that continuation using the existing producer and
reader implementations; it introduces no new author protocol or policy.
Assembly/v3 integration and its shared call sites remain separate. This catalog
does not silently add merge support to another opt-in. Existing plain-slice
exclusions, explicit unresolved orders and incompatible historical HOST input
limits continue to apply.
