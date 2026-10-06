---
name: rpnh-open-region-descendants
description: "Explicit ordinary author descendants, independent copies and strong historical consumption."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: open-region-descendants_ZH.md
  revision: "2026-10-05.1"
  status: implemented-bounded-author-contract
---

[English](open-region-descendants.md) | [中文](open-region-descendants_ZH.md)

# Ordinary descendants of an adapted open region

An adapted closed D can be used as the exact parent of a newly established
ordinary definition E. This is an explicit new author command, not an implicit
legacy edit or a refresh of D's prospective target proof. E has complete current
Module, stable-element, boundary and HOST materials and an actual closed compile.
Its derivation is `historical_only`; `current_adaptation` is null.

## Explicit edit and independent copy

Opt in with `open_region_derived_schema_data()` or
`open_region_derived_assembly_schema_data()`. Configure `OpenRegionDerivedAuthor`
with the existing owner gateway, trusted Registration and exact producer ref.
`publish(request=..., command_id=...)` requires every request field:

- `authority_mode`: exactly `ordinary_new_definition`, without an omitted default
- `operation`: `edit` or `copy_root`
- `parent_revision_ref` and `copy_source_ref`: exact source-qualified refs or explicit null
- `module`: the complete current Module document
- `element_ids`: the complete locator-to-stable-ID map
- `copy_sources`: an explicit result-ID-to-source-ID map, including an empty map when appropriate

An edit selects exactly one genuine D or E parent and a null copy source. It
retains the parent's logical lineage. Same-kind retained IDs and explicit new
parent-element copies follow the ordinary element-map rules. E can itself be
edited to E2; every full read reconstructs the entire historical proof.

A copy root selects a genuine D or E source and a null parent. It creates a new
logical lineage with empty history parents and entirely fresh element IDs. Its
initial Module must equal the exact source Module, and its total copy map must
select the same-kind source element at every corresponding locator. Edit the
result later to change content. Cross-lineage copy origins are kept in the new
historical resource; the old element map's parent-only `copied_from` remains null.
A copy creates no common merge ancestor or inherited target eligibility.

## Strong current and historical authority

`validate_closed_revision` returns a distinct `ValidatedOpenDerivedRevision`.
Its exact `command_ref` and `historical_origins_ref` identify the immutable
complete command and reconstructed summary. Origin rows pin actual D/E revision,
command, adaptation or historical-summary refs. History parents use `ancestor`;
copy provenance uses `copied_from`. Copying inherited history converts those
relationships to copy provenance rather than claiming false ancestry.

Publication fixes the complete request, exact source proof and materials, all
current material bytes, schema authorities and historical summary before the
revision is committed. Full consumers reconstruct the same-cut D/O/S and copy
source proofs, current compile, maps, exact owner/source and active dependency
path. Missing history, altered ancestry, wrong material/schema bytes or changed
commands fail. Identical replay is read-only; an interrupted prefix can resume
only the original request. Branch/v1 keeps its exact version/head/stream CAS.

## Explicit ordinary Assembly use

`AssemblyAuthorV5` uses the separately versioned
`rpnh/collaboration/direct_ordinary_derived_member_resolver/v1` contract.
`AssemblyMemberV5` requires a claim plus explicit `derived_command_ref` and
`historical_origins_ref`. For `ordinary_new_definition`, both must name the
selected E/copy's exact proof. For `plain_closed_v1`, both must be null.
The caller still supplies stable member IDs, connections, completion,
`shared_exact` and `same_run_candidate`.

The full Assembly entry reconstructs actual composition, final-context carriers,
complete member/fragment origins and the exact generated ordinary-v1 pair. A
bare generated G proves its independent ordinary definition. Composition and
historical member proof require the explicit Assembly ref; no mandatory G to
Assembly backlink is introduced. V5 history accepts only exact v5 parents.

Legacy ordinary author/reader, merge-v1 and Assembly v1/v2/v3 reject the new
proof family. Frozen adapted Assembly/v4 does not accept a new ordinary claim
or reuse D's intent for E. Its existing schema and resolver recipe are unchanged.

## Boundaries

This finite producer covers D/E descendants and copies. Merge-v2, copies of its
results, graph-open adapters and richer boundary adaptation are later work.
An ordinary author result does not replace an application's required source-bound
contribution or completion contract. Such a requirement remains caller-owned.
No author publication grants adoption, runtime execution, input availability,
resource leases, effects or remote permission.
