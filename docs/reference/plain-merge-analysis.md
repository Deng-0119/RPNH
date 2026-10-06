---
name: rpnh-plain-merge-analysis
description: "Explicit inert three-way plain Module author analysis."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-merge-analysis_ZH.md
  revision: "2026-10-04.1"
  status: stage-a-analysis-only
---

[English](plain-merge-analysis.md) | [中文](plain-merge-analysis_ZH.md)

# Plain Module merge analysis (Stage A)

Compose `plain_merge_schema_data()` explicitly and configure
`PlainModuleMergeAnalyzer(gateway, registration, producer_principal_ref)`.
`analyze(local_ref=L, incoming_ref=R, command_id=..., base_ref=B)` publishes
an immutable complete command, then its exact analysis resource. `base_ref` is
an optional assertion, never a request to choose an arbitrary ancestor.
`read_plain_merge_analysis(core, analysis_ref, registration)` independently
reconstructs the complete analysis in one Registry read transaction.

The trusted HOST compiles canonical existing author inputs. No operation,
executor, tool, runtime adoption, result revision or Branch mutation is invoked.
`status=analyzed` means the author differences were computed; it does not mean
that a merge succeeded, that conflicts were resolved, or that execution is safe.

## Identity and immutable evidence

Inputs must be exact local `NetRevision/v1` author proofs with the bound owner.
The merge entry locally verifies the actual source-binding payload against its
selected projection and exact task/native-run/bootstrap payloads. Constructor
preflight precedes schema registration; analysis and reading share each check
with their own input/proof read snapshot. The configured producer principal is
also checked before any analysis resource write, even when input authors differ.
Every participating ancestor is fully validated, including definition, stable
mapping, boundaries, actual schema authority bytes, selected HOST declarations
and complete original command. Stage A additionally recomputes every revision's
original v1 command/source/task UUID5 version identity and root or single-parent
logical identity. This is compatibility with the producer identity contract,
not proof of executed code or authorization. A new equivalent copy needs a new
producer command; the inherited general closed reader is unchanged. Graph component keys v1–v4, graph revisions,
generated Assembly constraints and nonempty opaque designer constraints are
outside this slice, including when they occur in an ancestor.

Ancestry determines the unique nearest common revision. An asserted base that
is not that exact revision is rejected. Unrelated histories produce an explicit
`unrelated_histories` analysis with no base, normalized three-way values, diffs
or conflicts. The contract reserves `multiple_bases` with the same unresolved
behavior. Accepted v1 full proofs currently have only zero or one parent, so
this status cannot yet arise from supported canonical inputs; raw unproved
multi-parent descriptors still fail full validation. No ancestor is invented.

The separate analysis command schema freezes the entire request, algorithm,
normalization contract, ancestor/material pins and full expected analysis in its
first durable resource. The second resource must use its exact command marker
and reference. Retry with any changed input conflicts; identical retry can
complete an interrupted publication. Unknown, additional or dual metadata
markers fail closed. This stage does not change `closed_author_command/v1` or
allow multi-parent result material. A later result/resolution command must be a
new immutable command domain referencing this exact analysis.

## Stable-ID comparison and conflicts

Module/component container bodies exclude their child lists. Each stable
identity has separate kind/parent, name and whole-contract value atoms. List
order is recorded separately. Declared endpoint, terminal-operation, operation
input/output/request, outcome-product and effect-port references become stable
IDs; locators remain side-specific evidence. Mechanical effect references,
opaque config, schemas and exact HOST selection stay indivisible values.
No arbitrary recursive JSON field merge or choice is made.

Each changed atom preserves type-sensitive exact base/local/incoming values and
presence. Conflict records have deterministic IDs, reasons, subjects and all
three exact sides. The model reports divergent atoms, concurrent introduction
of one identity, delete/modify, deleted dependencies, same-name different-ID
collisions and coupled contracts. Global boundaries, terminal, schemas/HOST,
budgets and component-local port/config/effect/binding contracts are conservatively
grouped when both sides interact. Existing links join both component contracts;
shared budget buckets and mechanical effect references also join groups. Successful compilation of each input is not
proof of combined author intention. No normalized data is a result Module.

Actual already-validated input fragments also provide an explicit derived
resource-dependency hint (stable component ID and carrier categories). Lease
identity/pool, variable resource arc, logical slot/reset, reusable/resource
places and arc lease claims conservatively couple cross-side edits globally,
even across disconnected author regions. The `lowered_resource_coupling`
conflict preserves the author atoms; it does not diff, merge or adopt PN output.
The strict reader independently rebuilds the hint from the same full inputs.

Inputs are finite and exact; cycles and non-finite JSON numbers fail closed.
No arbitrary size default or hard memory bound is claimed. Coupled conflicts
are emitted once per dependency group, without a Cartesian conflict expansion. Existing optional and default schema catalogs are unchanged.
Result resolution, reference reconstruction, result full-consumer dispatch,
Branch CAS, Assembly consumption, and an ordinary edit after the merge are
subsequent acceptance stages, not claims of this analysis stage.

A single explicit Registration must reconstruct all selected input HOST versions.
If the same HOST key has incompatible pinned revisions, input validation fails
closed before analysis publication; this is not a persisted HOST conflict result.

The opt-in [Stage B1 result candidate](plain-merge-result.md) adds an explicit
merge-command proof branch. Under that extension, analysis can traverse fully
validated merge inputs; the original Stage A-only parent limitation above
describes its accepted baseline. Result validation and B2/B3 integration retain
their separate acceptance boundaries.
