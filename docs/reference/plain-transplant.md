---
name: rpnh-plain-transplant
description: "Explicit selective plain author changes with complete donor proof."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-transplant_ZH.md
  revision: "2026-10-05.1"
  status: bounded-author-contract
---

[English](plain-transplant.md) | [中文](plain-transplant_ZH.md)

# Selective plain author transplant

Explicitly compose `plain_transplant_schema_data()` or
`plain_transplant_assembly_schema_data()` for the existing full Assembly/v2
consumer. These trusted author interfaces do not invoke or adopt a runtime.
Earlier schema inventories and Assembly recipes remain unchanged.

Call `PlainModuleTransplantAnalyzer.analyze` with exact `local_ref`,
`incoming_ref`, a nonempty sorted unique `selected_subjects` list and
`command_id`. Each subject is a changed stable-ID atom from the genuine B→R
diff, using the [plain normalization](plain-merge-analysis.md). There is no
all-changes default. Optional `base_ref` asserts the unique nearest common
ancestor; it cannot replace ancestry. Every participating ancestor must be a
fully proved same-owner, same-source ordinary plain revision, including the
accepted merge-v1 family. Graph, adapted/open, opaque constrained, generated
Assembly and transplant histories are outside this input family.

The saved analysis projects only selected donor states over B. Dependencies
come from the exact B/R atom sources; conservative resource-carrier evidence
comes from those genuinely compiled inputs. The projection is not itself a
compiled author proof. The analysis reports exact structural and selected/
omitted donor coupling gaps as well as B/L/projected-R conflicts. Incomplete
selections never acquire inferred dependency imports.

## Explicit result and provenance

Call `PlainModuleTransplantAuthor.publish` with the saved `analysis_ref`,
explicit `choices`, and a distinct result `command_id`. Use the existing
conflict choice fields `conflict_id`, `choice`, `reason`, and
`delete_element_ids`; every conflict needs one exact caller choice and a
nonempty reason. `choices=[]` is explicit for a conflict-free analysis.
Overlapping choices must agree. Unresolved dependencies and unexpressible
orders remain errors. A donor coupling gap may be declined; importing its
selected facts requires a newly explicit complete selection.

Every unselected atom in T must remain exactly as in L, including absence,
structural order and HOST selection. Broad conflict choices, synthetic delete
cleanup and order pruning cannot silently discard local state. The result is
actually rebuilt and compiled; normalized author atoms, IDs, boundaries and
exact HOST declarations must round-trip. The trusted Registration must
reconstruct every input's existing exact HOST contracts.

T has exactly one parent, L. The donor R and common base B remain provenance,
not extra ancestry. `selected_change_refs` contain only effective imported
facts: the result equals the genuine donor state and differs from L.
Requested donor deletion can be imported. Synthetic caller deletion is a
separate disposition. Already-local, kept-local, selected-base and synthetic
delete outcomes remain recorded with the full original selection in the
command and resolution; they are never falsely labeled imported.

Each imported-change resource pins B/L/R, analysis, result, exact subject,
B/R/result states and the actual resulting stable-ID mapping. The first
durable complete command fixes every future resource, schema authority,
actual authority digest, canonical document, metadata, result ref, parent,
selection and disposition. The final revision commit is publication. An
identical interrupted retry completes the original command; a changed
selection, reason, authority or byte conflicts. Replaying T never moves a
Branch.

## Strong reads, Branch and Assembly

`validate_closed_revision` recognizes only the explicit
`plain_transplant_author_command_v1` marker and returns a distinguishable
`ValidatedPlainTransplantRevision`. In one read transaction it reconstructs
all genuine input proofs, nearest base, exact projection, choices, effective
imports, actual complete compile and every material's canonical bytes and
metadata. A schema-valid claim or stored digest is not independent proof.
Unknown, dual and substituted legacy markers fail closed.

Use the existing Branch gateway with all three expected values: Branch
version, head revision and stream sequence. Saved Branch versions identify
historical heads; explicitly full-read each selected exact head. Result replay
neither advances nor rolls the Branch back.

With the explicit transplant+Assembly catalog, pass T as `AssemblyMemberV2`
and provide the completion, member identity, connections, `shared_exact`
budget policy and `same_run_candidate` intent. The existing `plain_closed_v1`
member resolution pins all four exact materials and follows the full
transplant proof. The actual Assembly reader reconstructs complete member
origins, compiled inventory and the paired generated revision. Generated G
must still have exact ordinary `ValidatedClosedRevision` type and its original
command/material/compiled pair. This opt-in does not loosen those checks.

Legacy merge and ordinary author descendants do not accept T as a new parent
family. This finite contract establishes selective author publication and
composition only; it grants no runtime readiness, deployment, effect or
workflow-requirement authority.

For explicitly rebuilt ordinary successors or independent copies that preserve
the complete T proof, use the separate [transplant descendant contract](plain-transplant-descendants.md).
