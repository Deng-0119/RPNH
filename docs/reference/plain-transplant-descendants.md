---
name: rpnh-plain-transplant-descendants
description: "Explicit ordinary definitions retaining complete selective transplant history."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-transplant-descendants_ZH.md
  revision: "2026-10-05.1"
  status: bounded-author-contract
---

[English](plain-transplant-descendants.md) | [中文](plain-transplant-descendants_ZH.md)

# Ordinary descendants of a selective transplant

An exactly proved [selective transplant T](plain-transplant.md) can be the
parent or independent-copy source of a new ordinary definition E. Opt in with
`plain_transplant_derived_schema_data()` or
`plain_transplant_derived_assembly_schema_data()`. Configure
`PlainTransplantDerivedAuthor` with the existing owner gateway, trusted
Registration and exact producer ref.

`publish(request=..., command_id=...)` requires every field, without defaults:

- `authority_mode`: exactly `ordinary_new_definition`
- `operation`: `edit` or `copy_root`
- `parent_revision_ref` and `copy_source_ref`: exact source-qualified refs or null
- `module`: the complete new current Module document
- `element_ids`: the total current locator-to-stable-ID map
- `copy_sources`: the explicit result-ID-to-source-ID map, including `{}`

An edit selects exactly one genuine T or E parent and a null copy source. It
keeps that logical lineage. Retained IDs must keep their kind; explicit new
element copies follow the existing parent-element rules. E can be edited to
E2. Each publication actually rebuilds the current Module, elements, boundaries,
exact HOST requirements and closed compile.

A copy root selects a genuine T or E source and a null parent. It has a new
logical lineage, `parents=[]`, entirely fresh element IDs, the exact source
Module, and a total same-locator/same-kind copy map. Edit the copy afterward to
change its definition. Cross-lineage provenance is recorded separately;
the ordinary element map's parent-only `copied_from` fields remain null.

## Historical selection remains fully proved

The full reader returns distinct `ValidatedPlainTransplantDerivedRevision`.
E's current `selected_change_refs` are empty. Its historical summary has
`derivation=historical_only`, `authority_mode=ordinary_new_definition` and
`current_transplant=null`. T's imported changes are not relabeled as new E
imports after E's current content changes.

Origin rows pin exact T/E revisions and commands, T's analysis, resolution and
all selected-change refs, or E's historical-summary ref. Editing adds an
`ancestor` relationship; copying records `copied_from`, including inherited
origins. A separate table records the exact independent element-copy map.
T's B/R remain donor/base provenance, not extra E or copy history parents.

Every full read reconstructs the complete original T proof, all donor/base/local
inputs, exact original selection and dispositions, effective imported changes,
and recursive descendant/copy history in one read transaction. It then rebuilds
the current definition and compile. Actual canonical material bytes, metadata,
schema authorities, source/owner identity and active dependency paths are
checked. Missing history, changed selection, cycle, substituted marker or altered
authority fails closed.

The first durable complete command freezes all request, source proof, prepared
material, identity, schema and metadata choices. An interrupted prefix resumes
only that exact command. The final revision commit publishes after another full
dependency check. Replay is read-only and never moves a Branch. Existing Branch
version/head/stream CAS applies; saved Branch versions select exact historical
heads that callers can explicitly full-read.

## Explicit Assembly/v7 consumption

`AssemblyAuthorV7` uses
`rpnh/collaboration/direct_transplant_derived_member_resolver/v1`.
`AssemblyMemberV7` requires an explicit claim and proof refs. For
`ordinary_new_definition`, the revision must be this exact E/copy family and
`derived_command_ref` and `historical_origins_ref` must be its exact proofs.
For `plain_closed_v1`, both refs must be null and the existing ordinary/merge
family is required. Caller choices still include member IDs, connections,
completion, `shared_exact` and `same_run_candidate`. V7 parents must be V7.

The full Assembly entry reconstructs real composition, final-context resource
carriers, all member/fragment origins, and the exact generated ordinary-v1 G
command/material/compiled pair. G must have exact `ValidatedClosedRevision`
type. Its standalone ordinary definition remains readable; full composition
and historical member proof requires the explicit Assembly entry, without a
mandatory backlink.

Existing ordinary author, merge, P2-derived and earlier Assembly contracts are
unchanged. T's existing direct Assembly/v2 route remains available. This finite
author contract creates no runtime readiness, adoption, execution, resource
lease, external effect or remote permission. An application's required
source-bound contribution or completion remains an explicit caller requirement.
