---
name: rpnh-open-region-authoring
description: "Explicit nonterminal selections, source-component closure and direct Assembly target proofs."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: open-region-authoring_ZH.md
  revision: "2026-10-09.2"
  status: implemented-bounded-author-contract
---

[English](open-region-authoring.md) | [中文](open-region-authoring_ZH.md)

# Open-region authoring

These are opt-in trusted HOST interfaces. Compose `open_region_schema_data()`
for saved open selections, `open_region_closure_schema_data()` for strong
closure, or `open_region_assembly_schema_data()` for direct Assembly/v4
consumption. Earlier catalog functions and versioned recipes remain unchanged.
This page describes the implemented structural interfaces and their limits.
[Ordinary descendants](open-region-descendants.md) covers the separate explicit
edit/copy and Assembly/v5 interfaces. See [finite validation](../guides/release-validation.md)
for historical verification scope; interface availability is not runtime certification.

## Save a nonterminal selection

`OpenRegionAuthor.publish` requires `source_revision_ref`, sorted unique
`component_element_ids`, `lineage_mode="new_lineage"`, and `command_id`.
The first adapter selects whole basic `operation` components from one exact
ordinary closed Module/v1 source. For A→B→C with C declared terminal, selecting
only B saves open O with incoming A→B, outgoing B→C, and missing-completion
obligations. `validate_open_revision` reopens all source/provenance/material
and actual-lowering evidence. It does not compile O as a Module.

The inventory covers typed data/control boundaries, feedback, leases, pools,
slots, resets, external dependencies, effects and completion. Supported absence
has explicit inspection evidence. Unknown actual fragments and unsupported
return/effect/resource protocols remain unresolved in O; compilation success
does not dispose of them. Coverage concerns declarations and actual typed
fragments, not arbitrary executor behavior.

## Close with explicit context and intent

`OpenRegionClosureAuthor.prepare_request` is a read-only preview. Supply O's
exact `open_revision_ref` and `provenance_ref`, the command ID, and all of:

- `context`: the exact source revision and sorted additional component IDs
- `completion`: the source primary terminal ID and all retained alternative
  terminal IDs in source order
- `extract_plan`: `kind="components"`, the sorted exact union of selected and
  context names, `boundary_policy="preserve_all_dependencies"`, and output name
- `intent`: the prospective exact source/owner, Assembly/v4 command, parent
  selector, member ID and explicit public-entry or exact Assembly-connection
  ingress expectations

The preview returns a complete request including each disposition and its
source/result evidence. `publish(request=..., command_id=...)` validates that
whole request and freezes it before preparing any result material. Selecting C
as context, declaring C's existing terminal and choosing an ingress disposition
reconstructs B+C through the existing extractor and compiler. No reachability
rule guesses C, completion, or an ingress policy.

`validate_closed_revision` dispatches D's closure marker to its full proof
reader. The returned `ValidatedAdaptedRevision` exposes
`adaptation_claim="exact_current_result"`, `target_status="prospective_unverified"`,
and exact command/adaptation/intent/origin refs. D has its own closed lineage
and empty history parents. Create a new Branch for D; advancing O's Branch to
D fails the direct-parent rule. An interrupted command resumes only its original
request; changed context, intent, schema authority or result conflicts.

An `assembly_connection` intent freezes its future other-member revision and
exit selectors. Those selectors are conditions on a future Assembly; D does
not consume or prove that other member's material or registered membership.
D fully validates the S/O/context material it actually uses. The actual v4
consumer must fully read the selected other member and verify its exit and
final-context carrier before the connection claim is satisfied.

## Consume the prospective target

An `AssemblyMemberV4` must explicitly choose `plain_closed_v1` with null proof
refs, or `adapted_result_current` with exact adaptation and intent refs.
`AssemblyAuthorV4` and `validate_assembly_revision` derive the target from the
actual Assembly and verify source, owner, command, parent, member, ingress,
root completion, complete final-context lowering/origins and the strict paired
ordinary generated revision. `read_assembly_revision` reads only its descriptor.

An adapted member currently requires its primary terminal to be the Assembly's
root completion. Reusing O for another target requires a new explicit closure
and intent. Two such adaptations can be consumed in two distinct Assemblies;
placing both as root completion in one Assembly fails the actual completion
contract. This is not a global member-count limit.

The generated ordinary revision keeps its independent closed-v1 authority.
Reading it alone proves that ordinary definition; adaptation/composition proof
requires the explicit Assembly/v4 ref. Legacy Assembly/merge consumers and
legacy ordinary descendants of D reject the unsupported claim. Explicit ordinary
edit/copy descendants use the separate
[derived author interfaces](open-region-descendants.md). Merge-v2, graph/node
adapters, nested adapted occurrences and richer boundary/completion protocols
are outside this supported slice.

All results here are structural authoring evidence. They create no run, runtime
adoption, input availability, lease, capacity, permission or provider execution.
