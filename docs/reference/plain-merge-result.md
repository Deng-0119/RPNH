---
name: rpnh-plain-merge-result
description: "Explicit caller resolution and immutable closed Module merge proof."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: plain-merge-result_ZH.md
  revision: "2026-10-05.1"
  status: stage-b1-result-candidate
---

[English](plain-merge-result.md) | [中文](plain-merge-result_ZH.md)

# Plain Module merge results (Stage B1)

Compose `plain_merge_result_schema_data()` explicitly. Configure
`PlainModuleMergeAuthor(gateway, registration, producer_principal_ref)` and call
`publish(analysis_ref=..., choices=..., command_id=...)` with the exact saved
[analysis](plain-merge-analysis.md). The candidate produces one closed
`NetRevision/v1` after validating caller decisions and rebuilding the author
Module. It does not advance a Branch or invoke runtime/business operations.
Branch CAS, Assembly consumption and the following ordinary single-parent edit
remain separate B2/B3 acceptance stages.

## Explicit decisions

Every saved conflict ID requires exactly one choice object containing
`conflict_id`, `choice`, `reason` and `delete_element_ids`. `choice` is
`local`, `incoming`, `base` or `delete`; `reason` is the caller's nonempty
explanation. The first three choices require an empty deletion list. A delete
choice requires an explicit nonempty list of stable author element IDs.
An analysis with no conflicts requires `choices=[]`.

The analyzer's complete conflict footprints define which atoms a decision
selects. Overlapping decisions must select type-sensitive identical presence
and values at every common atom. Different labels can be compatible when their
actual selected states match. Contradictory, duplicate, missing, unknown or
stale choices fail before the result command is written. No business decision
is inferred from the explanation, successful compilation or a preference for
one parent. Nonconflicting atoms follow their exact three-way changes.

Delete removes only named complete author elements and structural children
owned by a named component. Moved child ownership requires an explicit child
selection. Deleting the module root or HOST selection is invalid. Removing an
operation does not implicitly remove its component, boundary, consumers, links
or feedback. Surviving business references must remain valid. Deleted IDs can
be removed from their container order; a missing live member or an order that
cannot express both surviving additions yields `UnresolvedPlainMerge`.
There is no automatic sorting, concatenation or implicit deletion to repair it.
Custom order decisions are a later capability.

Stable references are reconstructed using selected parents and names. Operation
port references and terminal source/operation references must stay within the
same selected component. A foreign port with the same name cannot silently
bind to a different local port. The rebuilt normalized atoms must round-trip
exactly, except for explicit deletion bookkeeping and pruning unused selected
HOST declarations. Inherited IDs keep their identities; merge inheritance is
not recorded as an ordinary copy from one parent.

## First command and immutable result

Only an `analyzed` input with one unique common base can produce a result.
Identical exact heads are rejected before result writes. Result parents are
ordered `[L,R]`, with empty `selected_change_refs`; the base is not a parent.
The result uses the shared lineage's logical ID and a deterministic version ID
in the separate `collaboration-plain-merge-result:` command domain.

The first durable resource is `plain_merge_author_command/v1`. It fixes the
source, owner, producer, original choices and reasons, exact analysis and
analysis-command refs, algorithm, result identity and parents. It also fixes
all five future resources: resolution plus definition, element map, boundary
map and HOST requirements. Each has a deterministic ref, schema ID, full
document, full metadata including selected schema authority, and canonical
payload digest. The command additionally records actual schema-authority byte
digests. An equal-byte alternate authority is not the originally selected ref.

Publication then writes `plain_merge_resolution/v1` and the four author
materials, followed by the result revision. A durable partial command is
intentional recovery evidence; it is not a successful result. An identical
retry can finish the command. Changed requests, decisions, selected authority,
metadata or future bytes conflict with the first command. The final revision
commit is the result publication point; replay returns that same proof.

## Strong full reader and compatibility

`validate_closed_revision(core, result_ref, registration)` dispatches using the
definition's explicit `plain_merge_author_command_v1` marker. It recomputes the
analysis, decisions, stable-ID reconstruction and actual author compile within
the same read transaction. The source binding, task, native run, bootstrap and
configured producer have the local exact-authority checks. Nested revision and
proof validation shares cycle tracking; no global mutable proof cache is used.

The reader compares exact command and future-resource refs, original request,
result descriptor, complete metadata, selected schema authority and actual
canonical payload bytes. Parsed JSON equality alone is insufficient: even a
same-length key-order rewrite is not the fixed payload. HOST output is limited
to exact selected declarations actually consumed by the reconstructed compile.
No schema check, descriptor read or successful compile alone proves the result.

The old `closed_author_command/v1` wire and root/single-parent semantics remain
unchanged. Unrelated scalar legacy descriptor metadata remains allowed;
unknown or dual author-command protocol markers fail closed. Parent count
does not select a protocol. Fully validated merge inputs use their distinct
identity contract when a later analysis traverses them. Raw unproved
multi-parent descriptors remain invalid.

The slice requires one trusted Registration capable of reconstructing all
selected inputs. Same-key incompatible historical HOST versions still fail
input validation; persisting and resolving that HOST conflict is a later
capability. Graph-authored, Assembly-generated and opaque constrained inputs
remain outside this plain slice. Finite exact inputs and cycle rejection apply;
no arbitrary product size default or hard memory bound is claimed.
