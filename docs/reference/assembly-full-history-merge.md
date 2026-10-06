---
name: rpnh-assembly-full-history-merge
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: assembly-full-history-merge_ZH.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](assembly-full-history-merge.md) | [中文](assembly-full-history-merge_ZH.md)

# Flat Assembly full-history merge

`assembly_merge_schema_data()` opts in to Assembly v6. Existing v2/v3/v4
catalogs retain their contracts; v5 belongs to a separate resolver. This API
publishes author facts only. It does not start a run or authorize adoption.

`AssemblyMergeAnalyzer(gateway, registration, producer).analyze(...)` takes
exact `local_ref`, `incoming_ref`, and `command_id`. Its inputs are genuine flat
Assembly v2 histories or v6 results/successors in the same local source and
owner. It reconstructs their complete actual parent histories. There is no
caller base override: a unique nearest common ancestor is required. Unrelated
or multiple-base histories produce an immutable `unresolved_history` analysis,
which cannot publish a result.

Stable member IDs identify instances, including repeated display names. The
analysis records every exact base/left/right member revision and whole plan
atom. Competing member upgrades or internal revisions produce
`member_version_competition`. Cross-side structural changes conservatively
couple changed members with connections, completion, budgets and deployment.
The caller must review these conflicts; no recursive member merge is inferred.

Pass the saved `analysis_ref`, explicit `choices`,
`generated_continuity="left"`, and a new `command_id` to
`AssemblyMergeAuthor.publish`. Each conflict requires exactly one choice with
`subject`, `choice`, and a nonempty `reason`. Choices are `left`, `right`,
`base`, `delete` for a member, or `exact` with a complete `value`. An exact
member value has the same `member_id`, a `display_name`, and an exact genuine
leaf `revision_ref`. Deleting or replacing a member never silently repairs
connections or completion: use explicit exact plan-atom choices when needed.
Extra, missing or duplicate choices fail.

Unchanged/unilateral members remain present. Membership and connection arrays
have the existing canonical, inert ordering. Every candidate must still pass
actual whole composition, trusted static lowering, consumer/cardinality and
completion checks, exact shared bucket/binding checks and full member/compiled
origin coverage. `shared_exact` and `same_run_candidate` are explicit required
values. Unsupported or incompatible results fail before final publication.

The merged Assembly M has the actual ordered full parents `[L,R]`, even when
the caller retains local values for every conflict. Its generated ordinary
closed-v1 G separately has `[L.G]`. This explicit ordinary continuity does not
claim that G alone proves the Assembly merge. `validate_assembly_revision`
reconstructs M's complete history, saved analysis, decisions, exact selected
member materials, actual composition and strict ordinary G pair in one read
cut. `read_assembly_revision` reads only the descriptor. The independent
ordinary `validate_closed_revision` remains usable for G without an Assembly
backlink.

`AssemblyAuthorV6.publish` is the next author step. Supply actual v6
`parent_ref=M`, the complete typed plan (`AssemblyMemberV2`,
`AssemblyConnection`, `AssemblyCompletion`), name, budget/deployment values,
and command ID. E has actual Assembly parent `[M]`; E.G has `[M.G]`. Reading E
reconstructs M and both original histories.

Analysis first commands and result/edit first plans lock exact schema
authority refs and canonical authority byte digests, complete expected output
metadata, material bytes, choices/reasons and both output descriptors. This
includes all four G materials and the complete input descriptor/material,
HOST and schema-authority proof closure. Raw descriptor and source-material
bytes must be canonical, even where an independent legacy reader accepts an
equivalent JSON encoding. Analysis rechecks its locked input cut before
publishing the analysis resource. Incomplete publication can resume with the
same command and exact original inputs; changed authority, metadata, reason
or content under that command conflicts. Stale owner writers fail.

The finite leaf domain is accepted ordinary closed-v1, plain merge-v1 and
ordinary graph-v2 proof families, recursively checked through their histories.
Adding catalog schemas does not admit other proof families. Nested, adapted,
open, cross-owner/source, recursive member merging and semantic custom ordering
require separate protocols. Generated opaque Assembly Modules cannot be
laundered into ordinary members.
